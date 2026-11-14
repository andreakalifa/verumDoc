// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
// Prova di carico «1 milione di documenti al giorno» — VerumDoc R12 (roadmap R12,
// VD-C-081 parte carico). Preparata in R12a senza ambiente; si esegue il giorno 1 di
// staging (docs/architettura/giorno1_staging.md) contro un ambiente REALE, mai
// contro la produzione.
//
// Profilo: una GIORNATA lavorativa di versamenti, con il volume totale di
// DOCUMENTI_GIORNO (predefinito 1.000.000) distribuito sulle 24 ore secondo una
// curva oraria (picco 9–13 e 14–18, notte bassa, lotti notturni dei gestionali
// alle 2). La giornata si può COMPRIMERE (COMPRESSIONE=4: 24 ore in 6, ritmi ×4)
// per provare il picco senza aspettare un giorno intero: la presa in carico e la
// pipeline vedono lo stesso volume in meno tempo (prova più severa).
//
// Misure (modello ad arrivi aperti: il ritmo non dipende dalle risposte):
//   presa_in_carico      POST /api/v1/versamento/ud (202): p95 ≤ SOGLIA_P95_MS (piano §2)
//   conservazione        per un CAMPIONE dei versamenti (CAMPIONE_PERCENTO), tempo fino
//                        a CONSERVATO (pipeline: verifica, pacchetto, registro con
//                        checkpoint controfirmato): p95 ≤ SOGLIA_CONSERVAZIONE_S
//   documenti_versati    contatore: a fine prova deve valere ≈ DOCUMENTI_GIORNO
//
// Variabili d'ambiente (k6 run -e NOME=valore):
//   VD_URL                     base dell'API (es. https://api.staging.verumdoc.it)  obbligatoria
//   VD_TOKEN | VD_API_KEY      credenziale dell'ente di prova (scope versamento:ud)
//   DOCUMENTI_GIORNO           documenti al giorno (predefinito 1000000)
//   DOCUMENTI_PER_VERSAMENTO   documenti in ogni PdV (predefinito 1)
//   COMPRESSIONE               fattore di compressione della giornata (predefinito 1)
//   ORE                        ore della giornata da eseguire (predefinito 24; es. 2 per una
//                              prova breve: si eseguono le prime ORE ore della curva
//                              a partire da ORA_INIZIO)
//   ORA_INIZIO                 ora della curva da cui partire (predefinito 0)
//   DIMENSIONE_KB              dimensione di ogni documento (predefinito 300)
//   CLASSE                     classe documentale (predefinito fattura)
//   CAMPIONE_PERCENTO          versamenti seguiti fino a CONSERVATO (predefinito 0.5)
//   SOGLIA_P95_MS              (predefinito 2000)
//   SOGLIA_CONSERVAZIONE_S     (predefinito 3600)
//   ATTESA_MAX_S               attesa massima di un campione (predefinito 7200)
//   POLL_S                     intervallo delle letture dello stato di un campione (15)

import http from 'k6/http';
import crypto from 'k6/crypto';
import { check, fail, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import exec from 'k6/execution';

const URL = (__ENV.VD_URL || '').replace(/\/$/, '');
const DOCUMENTI_GIORNO = Number(__ENV.DOCUMENTI_GIORNO || 1000000);
const PER_VERSAMENTO = Math.max(1, Number(__ENV.DOCUMENTI_PER_VERSAMENTO || 1));
const COMPRESSIONE = Math.max(1, Number(__ENV.COMPRESSIONE) || 1);
const ORE = Math.min(24, Math.max(1, parseInt(__ENV.ORE, 10) || 24));
const ORA_INIZIO = Math.min(23, Math.max(0, parseInt(__ENV.ORA_INIZIO, 10) || 0));
const DIMENSIONE = Number(__ENV.DIMENSIONE_KB || 300) * 1024;
const CLASSE = __ENV.CLASSE || 'fattura';
const CAMPIONE = Number(__ENV.CAMPIONE_PERCENTO || 0.5) / 100;
const SOGLIA_P95 = Number(__ENV.SOGLIA_P95_MS || 2000);
const SOGLIA_CONSERVAZIONE = Number(__ENV.SOGLIA_CONSERVAZIONE_S || 3600) * 1000;
const ATTESA_MAX = Number(__ENV.ATTESA_MAX_S || 7200) * 1000;
const POLL = Number(__ENV.POLL_S || 15);
const ESECUZIONE = `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`;

// Peso di ogni ora della giornata (somma 100): curva tipica di un conservatore
// con enti pubblici e gestionali (picchi in orario d'ufficio, lotto notturno alle 2).
export const CURVA = [1, 1, 4, 2, 1, 1, 1, 2, 4, 8, 9, 9, 8, 4, 7, 8, 8, 6, 4, 3, 3, 2, 2, 2];

export function ritmoOrario(ora) {
  // versamenti al secondo nell'ora «ora» della curva, già compressi
  const documentiOra = DOCUMENTI_GIORNO * CURVA[ora] / 100;
  return (documentiOra / PER_VERSAMENTO / 3600) * COMPRESSIONE;
}

// Una fase per ora: salita di 1/10 dell'ora alla nuova quota, poi costante.
// timeUnit 1m e ritmi interi per minuto (precisione sufficiente anche a ritmi bassi).
const durataOra = 3600 / COMPRESSIONE;
const fasi = [];
for (let i = 0; i < ORE; i++) {
  const ora = (ORA_INIZIO + i) % 24;
  const alMinuto = Math.max(1, Math.round(ritmoOrario(ora) * 60));
  fasi.push({ target: alMinuto, duration: `${Math.round(durataOra / 10)}s` });
  fasi.push({ target: alMinuto, duration: `${Math.round(durataOra * 9 / 10)}s` });
}
const piccoAlSecondo = Math.max(...CURVA.map((_, o) => ritmoOrario(o)));

const presaInCarico = new Trend('presa_in_carico', true);
const conservazione = new Trend('conservazione', true);
const documentiVersati = new Counter('documenti_versati');
const campioniNonConservati = new Rate('campioni_non_conservati');
const rifiutiTemporanei = new Rate('rifiuti_temporanei');

export const options = {
  scenarios: {
    giornata: {
      executor: 'ramping-arrival-rate',
      exec: 'versamento',
      startRate: fasi.length ? fasi[0].target : 1,
      timeUnit: '1m',
      preAllocatedVUs: Math.max(20, Math.ceil(piccoAlSecondo * 3)),
      maxVUs: Math.max(100, Math.ceil(piccoAlSecondo * 12)),
      stages: fasi,
      tags: { scenario: 'giornata' },
    },
  },
  thresholds: {
    presa_in_carico: [`p(95)<${SOGLIA_P95}`],
    'http_req_failed{operazione:versamento}': ['rate<0.01'],
    'http_req_duration{operazione:stato}': ['p(95)<500'],
    conservazione: [`p(95)<${SOGLIA_CONSERVAZIONE}`],
    campioni_non_conservati: ['rate<0.01'],
    rifiuti_temporanei: ['rate<0.01'],
    checks: ['rate>0.99'],
    dropped_iterations: ['count<100'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

function intestazioni(extra) {
  const h = Object.assign({}, extra || {});
  if (__ENV.VD_TOKEN) h.Authorization = `Bearer ${__ENV.VD_TOKEN}`;
  else if (__ENV.VD_API_KEY) h['X-Api-Key'] = __ENV.VD_API_KEY;
  return h;
}

// Documenti sintetici: «%PDF-1.7», corpo casuale (impronte diverse), pochi per VU.
function pdf(dimensione) {
  const testa = '%PDF-1.7\n';
  const coda = '\n%%EOF\n';
  const out = new Uint8Array(dimensione);
  out.set(new Uint8Array(crypto.randomBytes(dimensione - testa.length - coda.length)),
    testa.length);
  for (let i = 0; i < testa.length; i++) out[i] = testa.charCodeAt(i);
  for (let i = 0; i < coda.length; i++) out[dimensione - coda.length + i] = coda.charCodeAt(i);
  return out.buffer;
}

const FILE = [];
for (let i = 0; i < Math.max(4, PER_VERSAMENTO); i++) {
  const dati = pdf(DIMENSIONE);
  FILE.push({ dati, sha256: crypto.sha256(dati, 'hex') });
}

function indice(numero, documenti) {
  return {
    classe_documentale: CLASSE,
    chiave: { numero, anno: '2026', registro: 'CARICO-GIORNATA' },
    documenti: documenti.map((d, i) => ({
      id: `doc-${i + 1}`, tipo: i === 0 ? 'PRINCIPALE' : 'ALLEGATO', nome_file: d.nome,
      formato: 'application/pdf', hash: { impronta: d.sha256, algoritmo: 'SHA-256' },
      parametri: {
        identificativo_documento: `${numero}-${i + 1}`,
        modalita_formazione: 'a',
        tipologia_documentale: CLASSE,
        data_documento: '2026-11-04',
        soggetto_produttore_id: '12345678901',
        soggetto_produttore_nome: 'Ente di prova del carico',
        oggetto: `Documento di prova della giornata ${numero}-${i + 1}`,
      },
    })),
  };
}

export function setup() {
  if (!URL) fail('VD_URL non impostata');
  if (!__ENV.VD_TOKEN && !__ENV.VD_API_KEY) fail('serve VD_TOKEN o VD_API_KEY');
  const r = http.get(`${URL}/api/v1/health/ready`, { tags: { operazione: 'salute' } });
  if (r.status !== 200) fail(`ambiente non pronto: /api/v1/health/ready → ${r.status}`);
  const versamenti = fasi.length ? Math.round(DOCUMENTI_GIORNO * CURVA.slice(ORA_INIZIO)
    .concat(CURVA).slice(0, ORE).reduce((a, b) => a + b, 0) / 100 / PER_VERSAMENTO) : 0;
  console.log(`giornata: ${ORE} ore dalla ${ORA_INIZIO} compresse ×${COMPRESSIONE}, ` +
    `≈${versamenti} versamenti attesi, picco ${piccoAlSecondo.toFixed(1)}/s`);
}

function attendiConservazione(pdvId, inizio) {
  let stato = '';
  while (Date.now() - inizio < ATTESA_MAX) {
    sleep(POLL);
    const r = http.get(`${URL}/api/v1/versamento/${pdvId}`, {
      headers: intestazioni(), tags: { operazione: 'stato' },
    });
    if (r.status !== 200) continue;
    stato = r.json('stato');
    if (stato === 'CONSERVATO' || stato === 'RIFIUTATO' || stato === 'ERRORE') break;
  }
  const conservato = stato === 'CONSERVATO';
  campioniNonConservati.add(!conservato);
  if (conservato) conservazione.add(Date.now() - inizio);
}

// Corpo multipart costruito a mano: più parti con lo STESSO nome «files» (il gateway
// legge getlist("files")); testi solo ASCII (indice compreso).
function ascii(testo) {
  const b = new Uint8Array(testo.length);
  for (let i = 0; i < testo.length; i++) b[i] = testo.charCodeAt(i) & 0x7f;
  return b;
}

export function multipart(indiceJson, files) {
  const confine = `vd-k6-${ESECUZIONE}-${Math.floor(Math.random() * 1e9)}`;
  const pezzi = [ascii(`--${confine}\r\nContent-Disposition: form-data; name="indice"\r\n` +
    `Content-Type: application/json\r\n\r\n${indiceJson}\r\n`)];
  for (const f of files) {
    pezzi.push(ascii(`--${confine}\r\nContent-Disposition: form-data; name="files"; ` +
      `filename="${f.nome}"\r\nContent-Type: application/pdf\r\n\r\n`));
    pezzi.push(new Uint8Array(f.dati));
    pezzi.push(ascii('\r\n'));
  }
  pezzi.push(ascii(`--${confine}--\r\n`));
  const totale = pezzi.reduce((a, p) => a + p.length, 0);
  const corpo = new Uint8Array(totale);
  let pos = 0;
  for (const p of pezzi) { corpo.set(p, pos); pos += p.length; }
  return { corpo: corpo.buffer, tipo: `multipart/form-data; boundary=${confine}` };
}

export function versamento() {
  const n = exec.scenario.iterationInTest;
  const numero = `G1M-${ESECUZIONE}-${n}`;
  const documenti = [];
  for (let i = 0; i < PER_VERSAMENTO; i++) {
    const f = FILE[(n + i) % FILE.length];
    documenti.push({ nome: `documento-${i + 1}.pdf`, sha256: f.sha256, dati: f.dati });
  }
  const { corpo, tipo } = multipart(JSON.stringify(indice(numero, documenti)), documenti);
  const inizio = Date.now();
  const r = http.post(`${URL}/api/v1/versamento/ud`, corpo, {
    headers: intestazioni({ 'Idempotency-Key': `g1m-${ESECUZIONE}-${n}`, 'Content-Type': tipo }),
    tags: { operazione: 'versamento' },
    timeout: '60s',
  });
  rifiutiTemporanei.add(r.status === 429 || r.status === 503);
  const ok = check(r, {
    'versamento 202': (x) => x.status === 202,
    'versamento con pdv_id': (x) => x.status === 202 && !!x.json('pdv_id'),
  });
  if (!ok) return;
  presaInCarico.add(r.timings.duration);
  documentiVersati.add(PER_VERSAMENTO);
  // campione deterministico (stessa frazione in ogni ora della curva)
  if (CAMPIONE > 0 && (n % Math.max(1, Math.round(1 / CAMPIONE))) === 0) {
    attendiConservazione(r.json('pdv_id'), inizio);
  }
}
