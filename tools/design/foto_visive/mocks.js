// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
// Mock API realistici per VerumDoc (enti inventati: Comune di Borgo Immaginario e altri)
const crypto = require("crypto");
const h = (s) => crypto.createHash("sha256").update(String(s)).digest("hex");
const uuid = (s) => {
  const x = h(s);
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-4${x.slice(13, 16)}-a${x.slice(17, 20)}-${x.slice(20, 32)}`;
};
const TID = "11111111-1111-1111-1111-111111111111";

const tenants = [
  { id: TID, ragione_sociale: "Comune di Borgo Immaginario", codice_fiscale: "00000000011", email_referente: "protocollo@borgo-immaginario.example", piano: "enterprise", stato: "ATTIVO" },
  { id: uuid("t2"), ragione_sociale: "Azienda sanitaria di Valfittizia", codice_fiscale: "00000000022", email_referente: "conservazione@valfittizia.example", piano: "enterprise", stato: "ATTIVO" },
  { id: uuid("t3"), ragione_sociale: "Unione dei Comuni di Collefinto", codice_fiscale: "00000000033", email_referente: "sistemi.informativi@collefinto.example", piano: "business", stato: "ATTIVO" },
  { id: uuid("t4"), ragione_sociale: "Ferramenta Esempio & Figli S.n.c.", codice_fiscale: "01234567890", email_referente: "amministrazione@example.org", piano: "starter", stato: "TRIAL" },
  { id: uuid("t5"), ragione_sociale: "Ordine professionale di Pianofittizio", codice_fiscale: "00000000055", email_referente: "segreteria@pianofittizio.example", piano: "business", stato: "SOSPESO" },
  { id: uuid("t6"), ragione_sociale: "Consorzio di bonifica di Valle Inventata", codice_fiscale: "00000000066", email_referente: "pec@valle-inventata.example", piano: "business", stato: "ATTIVO" },
];

const TIPI = ["FATTURA", "DELIBERA", "DETERMINAZIONE", "CONTRATTO", "VERBALE", "REGISTRO"];
const STATI_AIP = ["CONSERVATO", "CONSERVATO", "IN_CATENA", "CONSERVATO", "SU_STORAGE", "IN_BUILD", "CONSERVATO", "FIRMATO", "CONSERVATO", "IN_CATENA"];
const oggetti = [
  "Fattura n. 418/PA – manutenzione verde pubblico, settembre 2026",
  "Delibera di Giunta n. 112 – variazione di bilancio 2026-2028",
  "Determinazione n. 734 – affidamento servizio mensa scolastica",
  "Contratto rep. 2291 – lavori di adeguamento sismico scuola primaria di via dei Tigli",
  "Verbale Consiglio Comunale seduta del 29/09/2026",
  "Registro giornaliero di protocollo del 14/10/2026",
];
const aipItems = Array.from({ length: 20 }, (_, i) => {
  const tipo = TIPI[i % TIPI.length];
  const d = new Date(Date.UTC(2026, 9, 20 - Math.floor(i * 0.7), 8 + (i % 9), (i * 17) % 60));
  const scad = new Date(d); scad.setUTCFullYear(d.getUTCFullYear() + (tipo === "DELIBERA" || tipo === "VERBALE" ? 99 : 10));
  const aip_id = uuid("aip" + i);
  return {
    aip_id,
    urn: `urn:IT:c_c287:${tipo}:2026:${String(418 - i * 7).padStart(6, "0")}`,
    stato: STATI_AIP[i % STATI_AIP.length],
    data_versamento: d.toISOString(),
    scadenza_conservazione: scad.toISOString(),
    hash_sha256: h("doc" + i),
    classe_documentale: tipo.toLowerCase(),
    metadata: {
      allegato5: {
        doc1: {
          identificativo_documento: `${tipo.slice(0, 3)}-2026-${String(418 - i * 7).padStart(4, "0")}`,
          tipologia_documentale: tipo.toLowerCase(),
          data_documento: d.toISOString().slice(0, 10),
          soggetto_produttore_nome: "Comune di Borgo Immaginario – Settore Affari Generali",
          soggetto_produttore_id: "00000000011",
          oggetto: oggetti[i % oggetti.length],
          modalita_formazione: "INFORMATICA_AB_ORIGINE",
        },
      },
    },
  };
});

const versamenti = Array.from({ length: 18 }, (_, i) => {
  const st = ["CONSERVATO", "CONSERVATO", "IN_VALIDAZIONE", "CONSERVATO", "ERRORE", "IN_BUILD", "CONSERVATO", "RICEVUTO", "CONSERVATO", "ANNULLATO"][i % 10];
  const d = new Date(Date.UTC(2026, 9, 20, 9 - Math.floor(i / 3), (59 - i * 13 + 600) % 60));
  return { pdv_id: uuid("pdv" + i), stato: st, data_ricezione: d.toISOString() };
});

const blocchi = Array.from({ length: 10 }, (_, i) => ({
  numero: 4817 - i,
  hash: h("block" + (4817 - i)),
  num_aip: [37, 112, 8, 64, 211, 19, 3, 88, 41, 156][i],
  created_at: new Date(Date.UTC(2026, 9, 20, 9 - i, 0, 12 + i)).toISOString(),
  merkle_root: h("merkle" + i),
}));

const classi = [
  ["fattura_pa", "Fattura elettronica PA", "DI", 10, false, 4, 7, true],
  ["delibera_giunta", "Delibera di Giunta", "DAI", 0, true, 2, 9, true],
  ["determinazione", "Determinazione dirigenziale", "DAI", 0, true, 2, 9, true],
  ["contratto_rep", "Contratto in forma pubblica amministrativa", "DAI", 0, true, 3, 11, true],
  ["registro_protocollo", "Registro giornaliero di protocollo", "DAI", 0, true, 2, 8, true],
  ["fascicolo", "Fascicolo di procedimento", "AGG", 40, false, 1, 5, true],
  ["mandato_pagamento", "Mandato di pagamento (OPI)", "DI", 10, false, 1, 7, false],
].map(([codice, nome, t, anni, perm, nf, nm, attiva], i) => ({
  id: uuid("cl" + i), codice, nome, tipologia_xml: t, anni_conservazione: anni || 10,
  conservazione_permanente: perm,
  formati_ammessi: ["application/pdf", "application/pkcs7-mime", "application/xml", "image/tiff"].slice(0, nf),
  metadati_obbligatori: ["identificativo_documento", "modalita_formazione", "tipologia_documentale", "data_documento", "soggetto_produttore_id", "soggetto_produttore_nome", "oggetto", "classificazione", "numero_registrazione", "data_registrazione", "codice_ipa"].slice(0, nm),
  max_dimensione_mb: 100, attiva,
}));

const utenti = [
  ["Anna Esempio", "anna.esempio@example.org", "ADMIN_TENANT", true, "2025-03-11"],
  ["Bruno Fittizio", "bruno.fittizio@example.org", "OPERATORE", true, "2025-03-11"],
  ["Carla Di Prova", "protocollo2@example.org", "OPERATORE", true, "2025-09-02"],
  ["Dora Campione", "dora.campione@example.org", "VIEWER", true, "2026-01-19"],
  ["Ettore Modello", "ettore.modello@example.org", "OPERATORE", false, "2024-11-04"],
].map(([nome, email, ruolo, attivo, c], i) => ({ id: uuid("u" + i), nome, email, ruolo, attivo, created_at: c + "T10:00:00Z" }));

const apiKeys = [
  { id: uuid("k1"), key_prefix: "7f3a91", descrizione: "Protocollo informatico Halley – versamento notturno", attiva: true, created_at: "2025-04-02T08:00:00Z" },
  { id: uuid("k2"), key_prefix: "c02b4e", descrizione: "Contabilità – mandati OPI", attiva: true, created_at: "2025-11-17T08:00:00Z" },
  { id: uuid("k3"), key_prefix: "19de07", descrizione: "Vecchio gestionale (dismesso)", attiva: false, created_at: "2024-06-21T08:00:00Z" },
];

const storico = Array.from({ length: 7 }, (_, i) => ({
  id: uuid("pdd" + i), aip_id: aipItems[i * 2].aip_id,
  tipo: ["PDD_ESIBIZIONE", "PDD_COMPLETO", "PDD_ESIBIZIONE", "PDD_ESTRATTO", "PDD_MIGRAZIONE", "PDD_ESIBIZIONE", "PDD_COMPLETO"][i],
  stato: ["PENDING", "COMPLETATA", "COMPLETATA", "RIFIUTATA", "APPROVATA", "COMPLETATA", "FALLITA"][i],
  canale: i % 3 === 0 ? "API" : "WEB",
  created_at: new Date(Date.UTC(2026, 9, 19 - i * 2, 11, 7 * i)).toISOString(),
}));

const notifiche = Array.from({ length: 14 }, (_, i) => ({
  id: uuid("n" + i),
  tipo: ["aip_conserved", "pdv_invalid", "aip_conserved", "PDD_ESIBIZIONE", "aip_conserved", "email_log", "pdv_invalid"][i % 7],
  oggetto: [
    "AIP conservato – Fattura n. 418/PA",
    "PdV rifiutato: VLD-703 classe 'mandato_pagamento' non abilitata",
    "AIP conservato – Delibera di Giunta n. 112",
    "Esibizione richiesta da Guardia di Finanza – Tenenza di Vignola",
    "AIP conservato – Registro giornaliero di protocollo del 14/10/2026",
    "Riepilogo settimanale versamenti",
    "PdV rifiutato: VLD-412 impronta SHA-256 non corrispondente",
  ][i % 7],
  canale: ["pec", "email_smtp", "pec", "email_smtp", "webhook", "email_log", "pec"][i % 7],
  stato: ["CONSEGNATA", "FALLITA", "CONSEGNATA", "INVIATA", "CONSEGNATA", "IN_ATTESA", "CONSEGNATA"][i % 7],
  destinatario: ["protocollo@pec.example.org", "anna.esempio@example.org"][i % 2],
  data: new Date(Date.UTC(2026, 9, 20 - Math.floor(i / 3), 9, 40 - i * 2)).toISOString(),
}));

const throughput = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(Date.UTC(2026, 8, 21 + i));
  const dow = d.getUTCDay();
  return { data: d.toISOString().slice(0, 10), aip: dow === 0 ? 3 : dow === 6 ? 11 : 140 + ((i * 37) % 90) };
});

const manuale = {
  meta: { versione: "3.2.1", generato_at: "2026-10-20T07:12:00Z" },
  sezioni: [
    ["Presentazione", "Scopo del documento, ambito di applicazione e riferimenti del conservatore."],
    ["Descrizione del sistema", "Architettura logica e fisica del sistema di conservazione, siti primario e secondario."],
    ["Classi documentali", "Elenco delle 7 classi documentali configurate e relativi tempi di conservazione."],
    ["Processi di conservazione", "Acquisizione PdV, verifica, generazione AIP, rapporto di versamento, esibizione."],
    ["Misure di sicurezza", "Controlli ISO/IEC 27001, cifratura a riposo, gestione chiavi HSM."],
    ["Gestione anomalie", "Classificazione delle anomalie, tempi di presa in carico, comunicazione al produttore."],
    ["Piano di disaster recovery", "RPO 1h, RTO 4h, replica sincrona Bologna–Milano."],
    ["Conformità normativa", "LG AgID 2021 (aggiornate 2022), Reg. UE 2014/910, CAD art. 44."],
    ["Responsabilità e SLA", "Ruoli, responsabilità e livelli di servizio contrattuali."],
  ].map(([titolo, sommario], i) => ({ titolo, sommario, contenuto: sommario + " Testo esteso generato dalla configurazione corrente.", riferimenti_normativi: ["LG AgID §4." + (i + 1), "CAD art. 44"], conformita: i !== 6 })),
};

const audit = Array.from({ length: 30 }, (_, i) => ({
  id: uuid("a" + i),
  data: new Date(Date.UTC(2026, 9, 20, 9, 58 - i * 2, i * 7 % 60)).toISOString(),
  evento: ["pdv.ricevuto", "pdv.validato", "aip.generato", "hashchain.blocco", "auth.login", "distribuzione.richiesta", "tenant.utente_creato", "lifecycle.scarto_proposto"][i % 8],
  dettagli: { pdv_id: uuid("pdv" + i).slice(0, 8), classe: ["fattura_pa", "delibera_giunta", "determinazione"][i % 3], esito: i % 5 === 0 ? "KO" : "OK" },
  user_id: i % 4 === 0 ? null : uuid("u" + (i % 5)),
  ip_address: i % 4 === 0 ? "10.20.4.11" : `93.62.${100 + (i % 7)}.${20 + i}`,
}));

const contratti = [
  { ragione_sociale: "Comune di Borgo Immaginario", data_inizio: "2025-03-01", data_fine: "2028-02-29", numero_contratto: "VD-2025-0031", stato: "ATTIVO" },
  { ragione_sociale: "Azienda sanitaria di Valfittizia", data_inizio: "2024-07-01", data_fine: "2027-06-30", numero_contratto: "VD-2024-0012", stato: "ATTIVO" },
  { ragione_sociale: "Ordine professionale di Pianofittizio", data_inizio: "2023-01-01", data_fine: "2025-12-31", numero_contratto: "VD-2023-0004", stato: "SCADUTO" },
  { ragione_sociale: "Ferramenta Bortolotti & Figli S.n.c.", data_inizio: "2026-10-01", data_fine: null, numero_contratto: "VD-2026-0077", stato: "BOZZA" },
];

const nomine = {
  nomina_corrente: { nome: "Responsabile", cognome: "Di Prova", codice_fiscale: "DPRRSP80A01Z000X", data_nomina: "2024-02-01" },
  storico: [
    { nome: "Responsabile", cognome: "Di Prova", codice_fiscale: "DPRRSP80A01Z000X", data_nomina: "2024-02-01", data_revoca: null },
    { nome: "Delegata", cognome: "Fittizia", codice_fiscale: "FTTDLG75B41Z000Y", data_nomina: "2019-06-15", data_revoca: "2024-01-31" },
  ],
};

function normal(url, method) {
  const u = new URL(url);
  const p = u.pathname;
  if (p.startsWith("/api/dashboard/overview")) return {
    tenant: { ragione_sociale: "Comune di Borgo Immaginario", piano: "enterprise", storage_usato_gb: 412.7, storage_quota_gb: 500, storage_percentuale: 82.5 },
    aip_stats: { conservati: 48213, totale: 48390, per_stato: { CONSERVATO: 48213, IN_CATENA: 96, IN_BUILD: 41, SU_STORAGE: 28, ERRORE: 12 } },
    hashchain: { ultimo_blocco: 4817, hash_ultimo: h("block4817").slice(0, 16) },
  };
  if (p.startsWith("/api/dashboard/tenants")) return { items: tenants, total: tenants.length };
  if (p.startsWith("/api/dashboard/versamenti")) return { items: versamenti, total: 1843 };
  if (p.startsWith("/api/dashboard/aip/")) return {};
  if (p.startsWith("/api/dashboard/aip")) return { items: aipItems, total: 48390 };
  if (p.startsWith("/api/dashboard/hashchain")) return { integrita: { integra: true, messaggio: "Verificati 4.817 blocchi, ultima verifica 20/10/2026 09:41" }, totale_blocchi: 4817, blocchi_recenti: blocchi };
  if (p.startsWith("/api/dashboard/search")) return { items: aipItems.slice(0, 8), total: 137 };
  if (p.startsWith("/api/dashboard/classi-documentali")) return { items: classi, total: classi.length };
  if (/\/utenti/.test(p)) return { items: utenti, total: utenti.length };
  if (/\/api-keys/.test(p)) return { items: apiKeys, total: apiKeys.length };
  if (/\/onboarding\/tenant\/.*\/classi/.test(p)) return { classi: classi.slice(0, 5) };
  if (p.startsWith("/api/v2/distribution/storico")) return { items: storico, total: 23 };
  if (p.startsWith("/api/v2/distribution/aip/")) return aipItems[0];
  if (p.startsWith("/api/dashboard/distribution")) return { pdd_id: uuid("pddnew"), tipo: "PDD_ESIBIZIONE", hash_pdd: h("pddnew"), documenti: [1, 2], url_scadenza: "2026-10-27T09:00:00Z", messaggio: "Link di esibizione valido 7 giorni." };
  if (p.startsWith("/api/dashboard/notifiche")) return { items: notifiche, total: 214 };
  if (p.startsWith("/api/dashboard/sla/report")) return { metriche: { aip_conservati: 3912, blocchi_hashchain: 711 }, conformita: { uptime_pct: 99.97, disponibilita_pct: 99.91 }, note: "Manutenzione programmata 12/10 02:00-03:10." };
  if (p.startsWith("/api/dashboard/sla/throughput")) return { per_giorno: throughput, media_giornaliera: 118 };
  if (p.startsWith("/api/dashboard/admin/tenants-stats")) return { per_piano: { starter: 1, business: 3, enterprise: 2 }, per_stato: { ATTIVO: 4, TRIAL: 1, SOSPESO: 1 }, totale_tenant: 6, storage_totale_gb: 1873.4, totale_aip: 211904 };
  if (p.startsWith("/api/dashboard/manuale-conservazione")) return manuale;
  if (p.startsWith("/api/dashboard/audit")) return { items: audit, total: 18342 };
  if (p.startsWith("/api/dashboard/contratti")) return { items: contratti, total: contratti.length };
  if (p.startsWith("/api/dashboard/nomine-rsc")) return nomine;
  if (p.startsWith("/api/v1/versamento/ud")) return { pdv_id: uuid("pdv-live") };
  return { items: [], total: 0 };
}

function empty(url) {
  const p = new URL(url).pathname;
  if (p.startsWith("/api/dashboard/overview")) return { tenant: { ragione_sociale: "Comune di Borgo Immaginario", piano: "enterprise", storage_usato_gb: 0, storage_quota_gb: 500, storage_percentuale: 0 }, aip_stats: { conservati: 0, totale: 0, per_stato: {} }, hashchain: { ultimo_blocco: 0, hash_ultimo: null } };
  if (p.startsWith("/api/dashboard/hashchain")) return { integrita: null, totale_blocchi: 0, blocchi_recenti: [] };
  if (p.startsWith("/api/dashboard/tenants")) return { items: tenants, total: tenants.length };
  return { items: [], total: 0 };
}

// Worst case realistico (break-ui)
function worst(url) {
  const p = new URL(url).pathname;
  const base = normal(url);
  if (p.startsWith("/api/dashboard/overview")) return {
    tenant: { ragione_sociale: "Azienda sanitaria di Valfittizia – Istituto immaginario di ricerca e cura per la prova delle intestazioni molto lunghe", piano: "enterprise", storage_usato_gb: 10483.25, storage_quota_gb: 10240, storage_percentuale: 102.37 },
    aip_stats: { conservati: 12873441, totale: 12904118, per_stato: { CONSERVATO: 12873441, IN_CATENA: 18233, IN_BUILD: 9120, SU_STORAGE: 2911, ERRORE: 413, FIRMATO: 1, ELIMINAZIONE: 0 } },
    hashchain: { ultimo_blocco: 1048576, hash_ultimo: h("x").slice(0, 16) },
  };
  if (p.startsWith("/api/dashboard/aip")) return { items: aipItems.map((a, i) => i === 0 ? { ...a, urn: "urn:IT:ausl_re_irccs:CARTELLA_CLINICA_RICOVERO_ORDINARIO:2026:REP-0004182271-BIS-ALLEGATO-INTEGRATIVO" } : i === 1 ? { ...a, urn: null } : i === 2 ? { ...a, stato: "ERRORE_VALIDAZIONE_FORMATO" } : a), total: 12904118 };
  if (/\/utenti/.test(p)) return { items: [{ ...utenti[0], nome: "Nomefittizio Lunghissimo Diprova-Controllo Impaginazione Esempio", email: "nomefittizio.lunghissimo.diprova.controllo.impaginazione@example.org" }, { ...utenti[1], nome: "Jo", email: "jo@x.it" }, ...utenti.slice(2)], total: 5 };
  return base;
}

// R05 (VD-C-079): la sessione del portale è un cookie HttpOnly e il frontend chiede
// chi è connesso a GET /api/v1/auth/sessione (niente più «vd_session» in
// localStorage). Registrata DOPO il route generico «**/api/**», così vale anche
// nei modi errore/vuoto/caricamento delle foto (Playwright usa l'ultimo route).
function sessione(ruolo = "SUPER_ADMIN") {
  return { utente: { tenant_id: TID, user_id: "u1", ruolo, ragione_sociale: "Comune di Borgo Immaginario",
                     piano: "enterprise", scopes: ["*"], nome: "Utente", cognome: "Di prova" },
           scadenza: "2099-01-01T00:00:00Z", autenticazione: "cookie" };
}

async function instradaSessione(ctx, ruolo = "SUPER_ADMIN") {
  await ctx.route("**/api/v1/auth/sessione", (r) => r.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(sessione(ruolo)) }));
}

module.exports = { normal, empty, worst, sessione, instradaSessione, TID, h, uuid };
