// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
// Prova di carico del versamento — VerumDoc R04 (VD-C-081, piano R03–R05 §2 e §4.2)
//
// Obiettivo di progetto: picco di 50 versamenti al secondo per un'ora, file medio
// 300 KB, presa in carico p95 ≤ 2 s (escluso il trasferimento). Si esegue in CI
// (job manuale «carico-k6», .github/workflows/carico.yml) contro un ambiente REALE
// (staging con PostgreSQL/PgBouncer, S3 con Object Lock, Kafka): mai contro la
// produzione.
//
// Scenari (modello ad arrivi aperti: il ritmo non dipende dalle risposte):
//   versamento  POST /api/v1/versamento/ud multipart (indice + PDF da DIMENSIONE_KB)
//               con Idempotency-Key, poi GET dello stato del PdV
//   sessioni    sessione di caricamento C1: apertura, parti in parallelo,
//               completamento, attesa della verifica, versamento per riferimento
//
// Variabili d'ambiente (k6 run -e NOME=valore):
//   VD_URL         base dell'API (es. https://api.staging.verumdoc.it)    obbligatoria
//   VD_TOKEN       token Bearer di un utente con scope versamento:ud       oppure
//   VD_API_KEY     chiave API (X-Api-Key) dell'ente di prova
//   RITMO          versamenti al secondo (predefinito 50)
//   DURATA         durata dello scenario versamento (predefinito 1h)
//   RAMPA          durata della salita iniziale (predefinito 5m)
//   DIMENSIONE_KB  dimensione del file del versamento (predefinito 300)
//   SESSIONI_RITMO sessioni di caricamento al minuto (predefinito 30; 0 = scenario spento)
//   SESSIONE_MB    dimensione del file delle sessioni (predefinito 12: due parti da 8 MB)
//   CLASSE         classe documentale (predefinito fattura)
//   SOGLIA_P95_MS  p95 massimo della presa in carico (predefinito 2000)
//
// Le soglie (thresholds) sono quelle del piano §2: se non sono rispettate k6
// esce con codice diverso da zero e il job di CI fallisce.

import http from 'k6/http';
import crypto from 'k6/crypto';
import { check, fail, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import exec from 'k6/execution';

const URL = (__ENV.VD_URL || '').replace(/\/$/, '');
const RITMO = Number(__ENV.RITMO || 50);
const DURATA = __ENV.DURATA || '1h';
const RAMPA = __ENV.RAMPA || '5m';
const DIMENSIONE = Number(__ENV.DIMENSIONE_KB || 300) * 1024;
const SESSIONI_RITMO = Number(__ENV.SESSIONI_RITMO || 30);
const SESSIONE = Number(__ENV.SESSIONE_MB || 12) * 1024 * 1024;
const CLASSE = __ENV.CLASSE || 'fattura';
const SOGLIA_P95 = Number(__ENV.SOGLIA_P95_MS || 2000);
const ESECUZIONE = `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`;

const presaInCarico = new Trend('presa_in_carico', true);
const versamentiAccettati = new Counter('versamenti_accettati');
const rifiutiTemporanei = new Rate('rifiuti_temporanei'); // 429/503 ritentabili
const verificaSessione = new Trend('verifica_sessione', true);

const scenari = {
  versamento: {
    executor: 'ramping-arrival-rate',
    exec: 'versamento',
    startRate: Math.max(1, Math.floor(RITMO / 10)),
    timeUnit: '1s',
    preAllocatedVUs: Math.max(20, RITMO * 2),
    maxVUs: Math.max(100, RITMO * 8),
    stages: [
      { target: RITMO, duration: RAMPA },
      { target: RITMO, duration: DURATA },
    ],
    tags: { scenario: 'versamento' },
  },
};
if (SESSIONI_RITMO > 0) {
  scenari.sessioni = {
    executor: 'constant-arrival-rate',
    exec: 'sessione',
    rate: SESSIONI_RITMO,
    timeUnit: '1m',
    duration: DURATA,
    startTime: RAMPA,
    preAllocatedVUs: 5,
    maxVUs: 40,
    tags: { scenario: 'sessioni' },
  };
}

export const options = {
  scenarios: scenari,
  discardResponseBodies: false,
  thresholds: {
    // Presa in carico (piano §2): p95 ≤ 2 s
    presa_in_carico: [`p(95)<${SOGLIA_P95}`],
    'http_req_duration{operazione:versamento}': [`p(95)<${SOGLIA_P95}`],
    'http_req_failed{operazione:versamento}': ['rate<0.01'],
    // Stato del PdV: risposta del portale/API ≤ 500 ms (piano §2)
    'http_req_duration{operazione:stato}': ['p(95)<500'],
    rifiuti_temporanei: ['rate<0.01'],
    checks: ['rate>0.99'],
    // Il generatore deve riuscire a mantenere il ritmo (altrimenti la prova non vale)
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

// PDF sintetici: testa «%PDF-1.7», corpo casuale (impronte tutte diverse). Pochi
// per VU (memoria): la chiave UD e la Idempotency-Key cambiano a ogni richiesta.
function pdf(dimensione) {
  const testa = '%PDF-1.7\n';
  const coda = '\n%%EOF\n';
  const corpo = new Uint8Array(crypto.randomBytes(dimensione - testa.length - coda.length));
  const out = new Uint8Array(dimensione);
  for (let i = 0; i < testa.length; i++) out[i] = testa.charCodeAt(i);
  out.set(corpo, testa.length);
  for (let i = 0; i < coda.length; i++) out[dimensione - coda.length + i] = coda.charCodeAt(i);
  return out.buffer;
}

const FILE = [];
for (let i = 0; i < 4; i++) {
  const dati = pdf(DIMENSIONE);
  FILE.push({ dati, sha256: crypto.sha256(dati, 'hex') });
}

function indice(numero, file, nome) {
  return {
    classe_documentale: CLASSE,
    chiave: { numero, anno: '2026', registro: 'CARICO-K6' },
    documenti: [{
      id: 'doc-1', tipo: 'PRINCIPALE', nome_file: nome, formato: 'application/pdf',
      hash: { impronta: file.sha256, algoritmo: 'SHA-256' },
      parametri: {
        identificativo_documento: numero,
        modalita_formazione: 'a',
        tipologia_documentale: CLASSE,
        data_documento: '2026-10-23',
        soggetto_produttore_id: '12345678901',
        soggetto_produttore_nome: 'Ente di prova del carico',
        oggetto: `Documento di prova di carico ${numero}`,
      },
    }],
  };
}

export function setup() {
  if (!URL) fail('VD_URL non impostata');
  if (!__ENV.VD_TOKEN && !__ENV.VD_API_KEY) fail('serve VD_TOKEN o VD_API_KEY');
  const r = http.get(`${URL}/api/v1/health/ready`, { tags: { operazione: 'salute' } });
  if (r.status !== 200) fail(`ambiente non pronto: /api/v1/health/ready → ${r.status}`);
}

export function versamento() {
  const n = exec.scenario.iterationInTest;
  const file = FILE[n % FILE.length];
  const numero = `K6-${ESECUZIONE}-${n}`;
  const nome = `documento-${n % FILE.length}.pdf`;
  const corpo = {
    indice: JSON.stringify(indice(numero, file, nome)),
    files: http.file(file.dati, nome, 'application/pdf'),
  };
  const r = http.post(`${URL}/api/v1/versamento/ud`, corpo, {
    headers: intestazioni({ 'Idempotency-Key': `k6-${ESECUZIONE}-${n}` }),
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
  versamentiAccettati.add(1);
  const stato = http.get(`${URL}/api/v1/versamento/${r.json('pdv_id')}`, {
    headers: intestazioni(), tags: { operazione: 'stato' },
  });
  check(stato, { 'stato 200': (x) => x.status === 200 });
}

export function sessione() {
  const n = exec.scenario.iterationInTest;
  const dati = pdf(SESSIONE);
  const sha = crypto.sha256(dati, 'hex');
  const nome = `grande-${n}.pdf`;
  const apertura = http.post(`${URL}/api/v1/caricamenti`, JSON.stringify({
    nome_file: nome, dimensione: SESSIONE, mime_dichiarato: 'application/pdf',
    impronta: { algoritmo: 'SHA-256', valore: sha },
  }), {
    headers: intestazioni({ 'Content-Type': 'application/json', 'Idempotency-Key': `k6-s-${ESECUZIONE}-${n}` }),
    tags: { operazione: 'caricamento_apertura' },
  });
  if (!check(apertura, { 'sessione aperta 201': (x) => x.status === 201 })) return;
  const id = apertura.json('caricamento_id');
  const parte = apertura.json('dimensione_parte');
  const numeroParti = apertura.json('numero_parti');
  const richieste = [];
  for (let p = 1; p <= numeroParti; p++) {
    const pezzo = dati.slice((p - 1) * parte, Math.min(p * parte, SESSIONE));
    richieste.push(['PUT', `${URL}/api/v1/caricamenti/${id}/parti/${p}`, pezzo, {
      headers: intestazioni({
        'Content-Type': 'application/octet-stream',
        'X-Impronta-Parte': `sha256=${crypto.sha256(pezzo, 'hex')}`,
      }),
      tags: { operazione: 'caricamento_parte' },
      timeout: '120s',
    }]);
  }
  const parti = http.batch(richieste);
  if (!check(parti, { 'parti accettate': (xs) => xs.every((x) => x.status === 200) })) return;
  const completa = http.post(`${URL}/api/v1/caricamenti/${id}/completa`, null, {
    headers: intestazioni(), tags: { operazione: 'caricamento_completa' },
  });
  if (!check(completa, { 'completamento 202/200': (x) => x.status === 202 || x.status === 200 })) return;
  const inizio = Date.now();
  let stato = completa.json('stato');
  while (stato !== 'COMPLETO' && stato !== 'ERRORE' && Date.now() - inizio < 120000) {
    sleep(2);
    stato = http.get(`${URL}/api/v1/caricamenti/${id}`, {
      headers: intestazioni(), tags: { operazione: 'caricamento_stato' },
    }).json('stato');
  }
  verificaSessione.add(Date.now() - inizio);
  if (!check(stato, { 'sessione COMPLETO': (s) => s === 'COMPLETO' })) return;
  const numero = `K6S-${ESECUZIONE}-${n}`;
  const r = http.post(`${URL}/api/v1/versamento/ud`, JSON.stringify({
    indice: indice(numero, { sha256: sha }, nome), file: { [nome]: id },
  }), {
    headers: intestazioni({ 'Content-Type': 'application/json', 'Idempotency-Key': `k6-sv-${ESECUZIONE}-${n}` }),
    tags: { operazione: 'versamento_riferimento' },
  });
  check(r, { 'versamento per riferimento 202': (x) => x.status === 202 });
}
