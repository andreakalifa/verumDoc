// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Backend finto per le prove end-to-end delle console R05 (route mocking di Playwright).
 *
 * Risponde come i servizi veri, nelle forme lette dal codice (contratti Q1 e Q2 di R05):
 * onboarding (configurazione, classi, SFTP), ciclo-vita (pratiche), hashchain (registro,
 * verifiche), notification (webhook), distribution (fascicoli), dashboard-api (enti).
 * Gli errori sono problem+json del catalogo (`codice`, `dettagli`). Tutti i dati sono
 * dimostrativi; nessuna chiamata esce dal browser.
 */
import type { Page, Route } from "@playwright/test";

export type Ruolo = "SUPER_ADMIN" | "RSC_CONSERVATORE" | "ADMIN_TENANT" | "OPERATORE" | "VIEWER";

export const ENTE = "5f0c1a2e-8b4d-4c7a-9e21-3d6f8a0b1c11";
const ENTE_2 = "7a91d3c4-2e5f-4b80-8c6d-1f2e3a4b5c62";
const ENTE_3 = "0b7e6d5c-4a3b-4291-8f7e-6d5c4b3a2f13";
const CLASSE_FATT = "c1a55e00-0000-4000-8000-00000000f001";
const ORA = "2026-10-23T09:41:00Z";
const minuti = (n: number) => new Date(Date.parse(ORA) - n * 60_000).toISOString();

const problem = (status: number, codice: string, title: string, dettagli: Record<string, unknown> = {}, detail?: string) => ({
  status, body: { type: `https://verumdoc.it/errori/${codice}`, title, status, detail, codice, esito: status >= 500 ? "ERRORE" : "RIFIUTATO", ritentabile: false, dettagli, campi: [], id_richiesta: "4e1c-77b0" },
});

const ruoliVoce = (...l: string[]) => l;

// Revisione R17 (R17-18): scope e permessi dei ruoli fissi come il gateway
// (services/gateway/app/auth/ruoli.py, libs/verumdoc_rules/permessi.py)
const TUTTI_P = ["archivio:consulta", "versamento:esegui", "esibizione:richiedi", "rettifica:richiedi", "annullamento:richiedi",
  "scarto:esegui", "scarto:autorizza", "annullamento:approva", "firmatari:gestisci", "pratica:chiudi_eccezioni", "utenti:gestisci",
  "ruoli:gestisci", "accesso:configura", "firma_fornitori:gestisci", "pec_canali:gestisci", "pratica:riprendi", "documentazione:genera"];
export const PERMESSI_RUOLO: Record<Ruolo, string[]> = {
  SUPER_ADMIN: TUTTI_P, RSC_CONSERVATORE: TUTTI_P,
  ADMIN_TENANT: ["archivio:consulta", "versamento:esegui", "esibizione:richiedi", "rettifica:richiedi", "annullamento:richiedi",
                 "scarto:autorizza", "annullamento:approva", "utenti:gestisci", "ruoli:gestisci", "accesso:configura"],
  OPERATORE: ["archivio:consulta", "versamento:esegui", "esibizione:richiedi", "rettifica:richiedi", "annullamento:richiedi"],
  VIEWER: ["archivio:consulta"],
};
const SCOPE_RUOLO: Record<Ruolo, string[]> = {
  SUPER_ADMIN: ["*"], RSC_CONSERVATORE: ["*", "rsc:approve", "rsc:report"],
  ADMIN_TENANT: ["versamento:ud", "versamento:doc", "read", "distribution", "lifecycle", "admin:tenant"],
  OPERATORE: ["versamento:ud", "versamento:doc", "read", "lifecycle"],
  VIEWER: ["read"],
};
const CLASSI_R17 = [
  { id: CLASSE_FATT, codice: "FATTURA", nome: "Fatture passive", attiva: true, anni_conservazione: 10, conservazione_permanente: false,
    metadati_obbligatori: ["numero_fattura", "partita_iva_cedente"], formati_ammessi: [], tipologia_xml: null },
  { id: "c1a55e00-0000-4000-8000-00000000d001", codice: "DETERMINA", nome: "Determine", attiva: true, anni_conservazione: null,
    conservazione_permanente: true, metadati_obbligatori: [], formati_ammessi: [], tipologia_xml: null },
];
const AIP_R17 = [
  { aip_id: "a1b2c3d4-0000-4000-8000-000000000009", urn: "urn:verumdoc:comune-borgo:2026:DETERMINA:412", oggetto: "Determina n. 412/2026 — affidamento manutenzione",
    stato: "CONSERVATO", classe_documentale: { codice: "DETERMINA", nome: "Determine", conservazione_permanente: true }, conservazione_permanente: true,
    scadenza_conservazione: null, data_versamento: minuti(60 * 24 * 3), data_conservazione: minuti(60 * 24 * 3 - 20), hash_sha256: "f".repeat(64),
    dimensione_bytes: 81_337, metadati_allegato5: [] },
  { aip_id: "a1b2c3d4-0000-4000-8000-000000000010", urn: "urn:verumdoc:comune-borgo:2026:FATTURA:1203", oggetto: "Fattura passiva 1203/2026 — forniture di cancelleria",
    stato: "CONSERVATO", classe_documentale: { codice: "FATTURA", nome: "Fatture passive" }, scadenza_conservazione: "2036-12-31",
    data_versamento: minuti(60 * 24 * 10), data_conservazione: minuti(60 * 24 * 10 - 30), hash_sha256: "9".repeat(64), dimensione_bytes: 41_210, metadati_allegato5: [] },
];

// R13b: messaggi e ricevute PEC dimostrativi (backend finto)
const MESSAGGI_PEC = [
  { id: "m1", ente: ENTE, caso_uso: "SCARTO", destinatario: "protocollo@pec.comune-immaginario.example", oggetto: "[VerumDoc] Proposta di scarto: serve l'autorizzazione dell'ente",
    stato: "CONSEGNATO", canale: "pec-conservatore", message_id: "<pec.a1b2.m1@pec.verumdoc.it>", tentativi: 1, errore: null, creato_il: minuti(95),
    inviato_il: minuti(94), accettato_il: minuti(94), consegnato_il: minuti(93), esito_il: null, avviso: null },
  { id: "m2", ente: ENTE, caso_uso: "RESTITUZIONE", destinatario: "protocollo@pec.comune-immaginario.example", oggetto: "[VerumDoc] Restituzione dei documenti: serve l'autorizzazione dell'ente",
    stato: "ACCETTATO", canale: "pec-conservatore", message_id: "<pec.a1b2.m2@pec.verumdoc.it>", tentativi: 1, errore: null, creato_il: minuti(60 * 30),
    inviato_il: minuti(60 * 30), accettato_il: minuti(60 * 30 - 1), consegnato_il: null, esito_il: null, avviso: "consegna non confermata dopo 24 ore" },
  { id: "m3", ente: ENTE, caso_uso: "SCADENZE", destinatario: "errore.casella@pec.comune-immaginario.example", oggetto: "[VerumDoc] Copie di un versamento rifiutato in scadenza",
    stato: "MANCATA_CONSEGNA", canale: "pec-conservatore", message_id: "<pec.a1b2.m3@pec.verumdoc.it>", tentativi: 1, errore: "no-dest: 5.1.1 - Utente non esistente", creato_il: minuti(60 * 48),
    inviato_il: minuti(60 * 48), accettato_il: minuti(60 * 48), consegnato_il: null, esito_il: minuti(60 * 48 - 2), avviso: null },
];
const RICEVUTE_PEC = [
  { id: 41, canale: "pec-conservatore", esito: "ASSOCIATA", motivo: null, messaggio_id: "m1", tipo: "ACCETTAZIONE", msgid: "<pec.a1b2.m1@pec.verumdoc.it>",
    identificativo: "opec2101.20261023094100@pec.gestore-prova.it", gestore: "Gestore PEC di prova", consegna: null, errore: "nessuno", errore_esteso: null,
    data_ricevuta: minuti(94), certificato_sha256: "a".repeat(64), certificato_soggetto: "CN=posta-certificata@pec.gestore-prova.it", impronta_sha256: "b".repeat(64), dimensione: 4210, ricevuta_il: minuti(94) },
  { id: 42, canale: "pec-conservatore", esito: "ASSOCIATA", motivo: null, messaggio_id: "m1", tipo: "AVVENUTA_CONSEGNA", msgid: "<pec.a1b2.m1@pec.verumdoc.it>",
    identificativo: "opec2101.20261023094200@pec.gestore-ente.it", gestore: "Gestore PEC dell'ente", consegna: "protocollo@pec.comune-immaginario.example", errore: "nessuno", errore_esteso: null,
    data_ricevuta: minuti(93), certificato_sha256: "c".repeat(64), certificato_soggetto: "CN=posta-certificata@pec.gestore-ente.it", impronta_sha256: "d".repeat(64), dimensione: 9120, ricevuta_il: minuti(93) },
];

// R15: versioni dimostrative della documentazione del servizio (backend finto)
const DA_COMPILARE_R15 = [
  { sezione: "man.a", numero: "1", titolo: "Soggetti responsabili del sistema di conservazione", campi: [
    { campo: "conservatore.denominazione", descrizione: "denominazione del conservatore", chi: "il conservatore (legale rappresentante o responsabile del servizio)", dove: "variabile CONSERVATORE_DENOMINAZIONE" },
    { campo: "manuale.storico_responsabili", descrizione: "soggetti che nel tempo hanno avuto la responsabilità del sistema, con date di nomina e cessazione", chi: "il conservatore (legale rappresentante o responsabile del servizio)", dove: "atti di nomina (il sistema non li registra)" },
  ] },
  { sezione: "man.all8", numero: "A.8", titolo: "Allegato 8 — Continuità operativa (RPO e RTO)", campi: [
    { campo: "continuita.rpo", descrizione: "RPO misurato (dati persi al più)", chi: "il responsabile della sicurezza dei sistemi", dove: "esito della prova di ripristino (docs/runbooks/disaster_recovery.md)" },
  ] },
];
const VERSIONI_R15 = ["MANUALE_CONSERVAZIONE", "PIANO_SICUREZZA", "GUIDA_FUNZIONALITA", "CATALOGO_ROUTE"].map((tipo, i) => ({
  id: `ver-r15-${i}`, tipo, versione: 1,
  titolo: ({ MANUALE_CONSERVAZIONE: "Manuale di conservazione", PIANO_SICUREZZA: "Piano della sicurezza", GUIDA_FUNZIONALITA: "Guida alle funzionalità", CATALOGO_ROUTE: "Catalogo delle pagine" } as Record<string, string>)[tipo],
  generata_il: "2026-11-01T09:30:00Z",
  impronta_contenuto: String(i + 1).repeat(64), impronta_sha256: "a".repeat(64), impronta_html: "b".repeat(64),
  differenze: { aggiunte: [], tolte: [], cambiate: [] },
  sezioni_da_compilare: i < 2 ? 2 : 0, campi_da_compilare: i < 2 ? 3 : 0, da_verificare: i === 0 ? 6 : 0,
  archivio: { archiviata: i !== 1, archiviata_il: i !== 1 ? "2026-11-01T09:31:00Z" : null, chiave_markdown: null, chiave_html: null },
  firma: { stato: "non firmato", da_firmare: { formato: "text/html", impronta_sha256: "b".repeat(64) } },
  da_compilare: i < 2 ? DA_COMPILARE_R15 : [], da_verificare_elenco: i === 0 ? [{ requisito: "paragrafo «Processo di conservazione»", motivo: "testo definitivo non riscontrato" }] : [],
}));

function voce(codice: string, descrizione: string, valore: unknown, origine: string, extra: Record<string, unknown> = {}) {
  return {
    codice, descrizione, valore, origine, tipo: "int", locked: false, norm_ref: null,
    ambito_ammesso: ruoliVoce("CONSERVATORE", "ENTE", "CLASSE", "ACCORDO"), riservata_conservatore: false,
    applicata_da: "gateway", gruppo: "versamento", scelte: null, minimo: null, massimo: null, predefinito: valore,
    valore_al_livello: null, ereditato: null, azioni_consentite: { modifica: true, motivo_blocco: null }, ...extra,
  };
}

export function impostazioni(livello: string, ruolo: Ruolo) {
  const cons = ruolo === "SUPER_ADMIN" || ruolo === "RSC_CONSERVATORE";
  const bloccoEnte = (cons ? null : "valore stabilito dal conservatore");
  const voci = [
    voce("CLASSE_DIMENSIONE_MAX_MB", "Dimensione massima di un file della classe, in MB", 100, "PREDEFINITO", { minimo: 1, massimo: 2048 }),
    voce("CONSERVAZIONE_ANNI_PREDEFINITI", "Anni di conservazione predefiniti di una classe nuova", 10, "PREDEFINITO", {
      locked: false, applicata_da: null, gruppo: "normative", norm_ref: "Linee guida AgID (2021), §4.7",
      azioni_consentite: { modifica: false, motivo_blocco: "valore registrato ma non ancora applicato da alcun servizio: non modificabile" },
    }),
    voce("FORMATI_AMMESSI", "Formati (MIME) ammessi; elenco vuoto = ogni formato riconosciuto dal catalogo", ["application/pdf", "application/xml", "application/pkcs7-mime"], livello === "CONSERVATORE" ? "CONSERVATORE" : "CONSERVATORE", {
      tipo: "codes", norm_ref: "Linee guida AgID sul documento informatico (2021), Allegato 2",
      ereditato: livello === "CONSERVATORE" ? { valore: [], origine: "PREDEFINITO" } : { valore: ["application/pdf", "application/xml", "application/pkcs7-mime"], origine: "CONSERVATORE" },
      valore_al_livello: livello === "CONSERVATORE" ? ["application/pdf", "application/xml", "application/pkcs7-mime"] : null,
    }),
    voce("IDEMPOTENZA_OBBLIGATORIA", "Intestazione Idempotency-Key obbligatoria su ogni versamento (VD-P-27)", true, "CONSERVATORE", {
      tipo: "bool", locked: true, ambito_ammesso: ["CONSERVATORE"],
      azioni_consentite: { modifica: cons && livello === "CONSERVATORE", motivo_blocco: cons && livello === "CONSERVATORE" ? null : "valore imposto dalla norma: si stabilisce solo al livello del conservatore" },
    }),
    voce("IDEMPOTENZA_TRANSIZIONE_FINO_AL", "Ultimo giorno (ora italiana) in cui un versamento senza Idempotency-Key è ancora accettato", "2027-03-31", "PREDEFINITO", {
      tipo: "date", ambito_ammesso: ["CONSERVATORE", "ENTE"], azioni_consentite: { modifica: cons, motivo_blocco: bloccoEnte },
    }),
    voce("METADATI_MINIMI_PRINCIPALE", "Metadati minimi del documento principale", ["identificativo_documento", "data_documento", "oggetto", "soggetto_produttore", "riservato", "modalita_formazione", "tipologia_documentale"], "PREDEFINITO", {
      tipo: "codes", locked: true, norm_ref: "Linee guida AgID sul documento informatico (2021), Allegato 5", ambito_ammesso: ["CONSERVATORE"],
      azioni_consentite: { modifica: false, motivo_blocco: "valore imposto dalla norma: si stabilisce solo al livello del conservatore" },
    }),
    voce("METADATI_OBBLIGATORI", "Metadati obbligatori aggiuntivi della classe documentale", ["numero_fattura", "partita_iva_cedente"], livello === "CLASSE" ? "CLASSE" : "PREDEFINITO", { tipo: "codes", valore_al_livello: livello === "CLASSE" ? ["numero_fattura", "partita_iva_cedente"] : null, ereditato: { valore: [], origine: "PREDEFINITO" } }),
    voce("PROFILO_TIPOLOGIA", "Tipologia dell'Allegato 5 il cui profilo di metadati si applica insieme a quello della classe", "", "PREDEFINITO", { tipo: "choice", scelte: ["", "documento_informatico", "documento_amministrativo_informatico", "fattura", "contratto"] }),
  ];
  return voci;
}

const quote = (cons: boolean) => [
  voce("QUOTA_PDV_GIORNO", "Pacchetti di versamento al giorno dell'ente; 0 = limite del piano (QUOTE_PDV_GIORNO)", 25000, "ENTE", {
    gruppo: "quote", ambito_ammesso: ["ENTE"], riservata_conservatore: true, minimo: 0, massimo: 5000000, valore_al_livello: 25000,
    ereditato: { valore: 0, origine: "PREDEFINITO" }, azioni_consentite: { modifica: cons, motivo_blocco: cons ? null : "valore stabilito dal conservatore" },
  }),
  voce("QUOTA_RICHIESTE_MINUTO", "Richieste al minuto dell'ente su tutte le API; 0 = limite del piano (QUOTE_RICHIESTE_MINUTO)", 0, "PREDEFINITO", {
    gruppo: "quote", ambito_ammesso: ["ENTE"], riservata_conservatore: true, minimo: 0, massimo: 600000,
    azioni_consentite: { modifica: cons, motivo_blocco: cons ? null : "valore stabilito dal conservatore" },
  }),
];

// R08 (D01, D02): voci dei gruppi «firme» e «formati»
const SCELTE_FORMATO = ["RIFIUTATO", "DA_CONVERTIRE", "AVVISO", "AMMESSO"];
const firme = (cons: boolean) => [
  voce("FIRME_LIVELLO_VERIFICA", "Livello di verifica delle firme dei documenti versati: NESSUNA, RILEVATA, CRITTOGRAFICA, CON_REVOCHE", "CRITTOGRAFICA", "CONSERVATORE", {
    tipo: "choice", gruppo: "firme", scelte: ["CON_REVOCHE", "CRITTOGRAFICA", "RILEVATA", "NESSUNA"], ambito_ammesso: ["CONSERVATORE", "ENTE", "CLASSE"],
    applicata_da: "validation (verifica delle firme)", predefinito: "RILEVATA" }),
  voce("NULLA_OSTA_FIRMA", "Nulla osta allo scarto senza firma: AVVISO o BLOCCANTE", "AVVISO", "PREDEFINITO", {
    tipo: "choice", gruppo: "firme", scelte: ["BLOCCANTE", "AVVISO"], ambito_ammesso: ["CONSERVATORE", "ENTE"],
    azioni_consentite: { modifica: true, motivo_blocco: cons ? null : null } }),
];
const formati = () => [
  voce("FORMATO.PDF.ESITO", "Esito del formato «PDF (ISO 32000)» nel versamento e nella verifica", "AMMESSO", "PREDEFINITO", {
    tipo: "choice", gruppo: "formati", scelte: SCELTE_FORMATO, ambito_ammesso: ["CONSERVATORE", "ENTE", "CLASSE"] }),
  voce("FORMATO.OFFICE_BINARIO.ESITO", "Esito del formato «Formati binari Microsoft Office 97-2003»", "DA_CONVERTIRE", "PREDEFINITO", {
    tipo: "choice", gruppo: "formati", scelte: SCELTE_FORMATO, ambito_ammesso: ["CONSERVATORE", "ENTE", "CLASSE"] }),
];
// R09: voci dei gruppi del ciclo di vita automatico
const cicloVita = (g: string) => ({
  scarto: [
    voce("SCARTO_PROPOSTA_AUTOMATICA", "Proposta di scarto automatica in bozza con anteprima e avviso all'ente", "DISATTIVA", "PREDEFINITO", {
      tipo: "choice", gruppo: "scarto", scelte: ["ATTIVA", "DISATTIVA"], ambito_ammesso: ["CONSERVATORE", "ENTE"] }),
    voce("SCARTO_PROPOSTA_FREQUENZA_GIORNI", "Ogni quanti giorni si prepara la proposta di scarto automatica", 30, "PREDEFINITO", {
      gruppo: "scarto", minimo: 7, massimo: 365, ambito_ammesso: ["CONSERVATORE", "ENTE"] }),
    voce("SCARTO_MINIMIZZAZIONE_GIORNI", "Giorni dopo lo scarto oltre i quali restano solo i dati dell'elenco di scarto", 30, "PREDEFINITO", {
      gruppo: "scarto", minimo: 1, massimo: 365, ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
  ],
  annullamento: [
    voce("ANNULLAMENTO_DOPPIA_APPROVAZIONE", "Doppia approvazione dell'annullamento (quattro occhi)", "DOPO_CONSERVAZIONE", "PREDEFINITO", {
      tipo: "choice", gruppo: "annullamento", scelte: ["SEMPRE", "DOPO_CONSERVAZIONE", "MAI"], ambito_ammesso: ["CONSERVATORE", "ENTE"] }),
  ],
  retention: [
    voce("RETENTION_PERMANENTE_ANNI", "Anni di blocco WORM delle classi permanenti, di allegati, verbali e atti", 30, "PREDEFINITO", {
      gruppo: "retention", minimo: 5, massimo: 100, ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
    voce("RETENTION_RINNOVO_SOGLIA_ANNI", "Rinnovo quando mancano meno di questi anni (mai ridotto)", 5, "PREDEFINITO", {
      gruppo: "retention", minimo: 1, massimo: 30, ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
  ],
  verifiche: [
    voce("VERIFICA_CICLO_GIORNI", "Ogni pacchetto si rilegge almeno una volta in questi giorni", 365, "PREDEFINITO", {
      gruppo: "verifiche", minimo: 1, massimo: 365, ambito_ammesso: ["CONSERVATORE", "ENTE", "CLASSE"], riservata_conservatore: true }),
  ],
  audit: [
    voce("AUDIT_VERIFICA_COMPLETA_GIORNO", "Giorno del mese della verifica completa dell'audit", 1, "PREDEFINITO", {
      gruppo: "audit", minimo: 1, massimo: 28, ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
  ],
} as Record<string, ReturnType<typeof voce>[]>)[g] ?? null;
// R10: voci dei gruppi di accesso, ruoli, notifiche e SFTP
const accessoR10 = (g: string) => ({
  accesso: [
    voce("ACCESSO_METODI_AMMESSI", "Metodi con cui le persone accedono: SPID_CIE, PASSKEY, PASSWORD_TOTP", ["SPID_CIE", "PASSWORD_TOTP"], "CONSERVATORE", {
      tipo: "codes", gruppo: "accesso", ambito_ammesso: ["CONSERVATORE", "ENTE"], verso_sicuro: "sottoinsieme" }),
    voce("INVITO_VALIDITA_ORE", "Ore di validità del collegamento d'invito inviato per e-mail", 72, "PREDEFINITO", {
      gruppo: "accesso", minimo: 1, massimo: 336, ambito_ammesso: ["CONSERVATORE", "ENTE"] }),
  ],
  ruoli: [
    voce("RUOLI_PERMESSI_CONCEDIBILI", "Permessi che l'ente può mettere nei ruoli personalizzati", ["archivio:consulta", "versamento:esegui", "annullamento:richiedi"], "PREDEFINITO", {
      tipo: "codes", gruppo: "ruoli", ambito_ammesso: ["CONSERVATORE", "ENTE"], riservata_conservatore: true }),
  ],
  notifiche: [
    voce("NOTIFICHE_OBBLIGATORIE", "Avvisi che le persone non possono spegnere (CATEGORIA.CANALE)", ["ALLARMI.CONSOLE"], "ENTE", {
      tipo: "codes", gruppo: "notifiche", ambito_ammesso: ["CONSERVATORE", "ENTE"], verso_sicuro: "sovrainsieme" }),
  ],
  // R13a: firma e marca qualificate
  firma_qualificata: [
    voce("FIRMA_SENZA_FORNITORE", "Senza un fornitore qualificato di firma: BLOCCA o NON_FIRMATO", "NON_FIRMATO", "PREDEFINITO", {
      tipo: "choice", gruppo: "firma_qualificata", scelte: ["BLOCCA", "NON_FIRMATO"], ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
    voce("FIRMA_LIVELLO_DOCUMENTI", "Livello ETSI delle firme del conservatore", "B-LT", "PREDEFINITO", {
      tipo: "choice", gruppo: "firma_qualificata", scelte: ["B-LTA", "B-LT", "B-T"], ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
  ],
  // R13b: canale PEC
  pec: [
    voce("PEC_CASI_USO", "Comunicazioni formali inviate anche per PEC: SCARTO, RESTITUZIONE, RIVERSAMENTO, SCADENZE", ["SCARTO", "RESTITUZIONE"], "CONSERVATORE", {
      tipo: "codes", gruppo: "pec", ambito_ammesso: ["CONSERVATORE", "ENTE"], verso_sicuro: "sovrainsieme", norm_ref: "DPR 68/2005; CAD art. 48" }),
    voce("PEC_CONSEGNA_ATTESA_ORE", "Ore dopo l'accettazione oltre le quali la consegna è «non confermata»", 24, "PREDEFINITO", {
      gruppo: "pec", minimo: 1, massimo: 72, ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
  ],
  sftp: [
    voce("SFTP_ALLOWLIST_VOCI_MASSIME", "Indirizzi o reti al massimo nell'elenco di un account SFTP", 20, "PREDEFINITO", {
      gruppo: "sftp", minimo: 1, massimo: 500, ambito_ammesso: ["CONSERVATORE"], riservata_conservatore: true }),
  ],
} as Record<string, ReturnType<typeof voce>[]>)[g] ?? null;
const CATALOGO_RUOLI = {
  permessi: [
    { codice: "archivio:consulta", descrizione: "Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento", mai_delegabile: false, concedibile: true, sempre_incluso: true },
    { codice: "versamento:esegui", descrizione: "Versare pacchetti (API, SFTP, portale)", mai_delegabile: false, concedibile: true, sempre_incluso: false },
    { codice: "esibizione:richiedi", descrizione: "Chiedere pacchetti di distribuzione ed esibizioni", mai_delegabile: false, concedibile: false, sempre_incluso: false },
    { codice: "annullamento:richiedi", descrizione: "Chiedere l'annullamento di un versamento", mai_delegabile: false, concedibile: true, sempre_incluso: false },
    { codice: "scarto:esegui", descrizione: "Eseguire lo scarto (solo il conservatore)", mai_delegabile: true, concedibile: false, sempre_incluso: false },
    { codice: "annullamento:approva", descrizione: "Approvare un annullamento come seconda persona (solo l'amministratore dell'ente)", mai_delegabile: true, concedibile: false, sempre_incluso: false },
  ],
  modelli: { ADMIN_TENANT: ["archivio:consulta"], OPERATORE: ["archivio:consulta", "versamento:esegui"], VIEWER: ["archivio:consulta"] },
  ruoli_base: ["OPERATORE", "VIEWER"], concedibili: ["versamento:esegui", "annullamento:richiedi"], puo_gestire: true,
  items: [{ id: "r0000000-0000-4000-8000-000000000001", nome: "Lettore che versa", descrizione: "", ruolo_base: "VIEWER", permessi: ["archivio:consulta", "versamento:esegui"], attivo: true, versione: 1, utenti: 2, aggiornato_il: ORA }],
};
const STATO_AUTOMATICO = {
  passi: [
    { nome: "proposte_scarto", descrizione: "Proposte di scarto automatiche", ente: null, ultimo_giro: minuti(30), ultimo_esito: { enti: {} }, in_corso: false },
    { nome: "rinnovo_retention", descrizione: "Rinnovo dei blocchi WORM", ente: null, ultimo_giro: minuti(60 * 3), ultimo_esito: { pda_controllati: 12, pda_rinnovati: 12, allegati_rinnovati: 0 }, in_corso: false },
  ],
  in_parole: [
    "I blocchi WORM dei pacchetti permanenti, di allegati, verbali e atti durano 30 anni e si rinnovano da soli quando ne mancano meno di 5.",
    "Nessun pacchetto permanente in attesa di rinnovo.",
    "Rinnovi negli ultimi 30 giorni: 12.",
  ],
  retention: { anni: 30, soglia_anni: 5, prossima_scadenza: "2056-10-30T00:00:00Z", da_rinnovare: 0, rinnovi_30_giorni: 12, tetto_conteggi: 10000 },
};
const R09_ECCEZIONI = "51a7e0aa-1111-4aaa-8aaa-0000000000c1";
/** R14 (VD-C-133): scarto con l'esecuzione avviata e SOSPESA dopo i tentativi esauriti. */
export const R14_SOSPESA = "51a7e0aa-1111-4aaa-8aaa-0000000000d1";
const R09_ANNULLAMENTO = "51a7e0aa-1111-4aaa-8aaa-0000000000c2";
function dettaglioR09(id: string, ruolo: Ruolo) {
  const cons = ruolo === "SUPER_ADMIN" || ruolo === "RSC_CONSERVATORE";
  if (id === R09_ECCEZIONI) {
    return {
      ...pratica(id, "SCARTO", "ESEGUITA_CON_ECCEZIONI", { conteggi: { ELIMINATO: 41, NON_COMPLETATO: 2 } }),
      dati: { riprese_automatiche: 0 }, cronologia: [], autorizzazioni: [], allegati: [],
      oggetti: { conteggi: { ELIMINATO: 41, NON_COMPLETATO: 2 }, elenco: "" },
      in_parole: "Scarto concluso con eccezioni: 2 pacchetti non cancellati del tutto (di solito un blocco legale). Ripresa automatica quando il blocco sarà tolto (riprese automatiche già fatte: 0); il conservatore può anche riprendere ora o chiudere con eccezioni.",
      azioni_consentite: [
        { nuovo_stato: "ESEGUITA", allegati_richiesti: [], esecuzione: true, consentita: cons, motivo_blocco: cons ? null : "Esegue il conservatore", motivo_minimo: null },
        { nuovo_stato: "CHIUSA_CON_ECCEZIONI", allegati_richiesti: [], esecuzione: true, consentita: cons, motivo_blocco: cons ? null : "Chiude il conservatore", motivo_minimo: 20 },
      ],
    };
  }
  return {
    ...pratica(id, "ANNULLAMENTO", "RICHIESTA", { pdv_id: "1c2b3a49-5867-4d1e-9f0a-b1c2d3e4f5a7", motivo: "Versamento doppio del protocollo 2026/15001", creata_ruolo: "ADMIN_TENANT" }),
    dati: { stato_precedente: "CONSERVATO" }, cronologia: [], autorizzazioni: [], allegati: [],
    oggetti: { conteggi: {}, elenco: "" }, azioni_consentite: [],
    in_parole: "Annullamento in attesa: deve approvarlo un'altra persona dell'ente. Il versamento resta valido fino all'approvazione.",
    approvazione: { in_attesa: true, ruoli_ammessi: ["ADMIN_TENANT"], consentita: ruolo === "ADMIN_TENANT", motivo_blocco: ruolo === "ADMIN_TENANT" ? null : "Possono approvare un annullamento: ADMIN_TENANT" },
  };
}

const CATALOGO_FORMATI = { versione: 1, valido_dal: "2026-10-31", norm_ref: "Linee guida AgID sul documento informatico (2021), Allegato 2", esiti: SCELTE_FORMATO, formati: [
  { id: "PDFA", nome: "PDF/A (ISO 19005)", famiglia: "documenti impaginati", puid: ["fmt/95", "fmt/477"], mime: ["application/pdf"], esito_base: "AMMESSO", pdfa: true, codice: "FORMATO.PDFA.ESITO" },
  { id: "OFFICE_BINARIO", nome: "Formati binari Microsoft Office 97-2003 (DOC, XLS, PPT)", famiglia: "documenti modificabili", puid: ["fmt/40"], mime: ["application/msword"], esito_base: "DA_CONVERTIRE", pdfa: false, codice: "FORMATO.OFFICE_BINARIO.ESITO" },
  { id: "NON_IDENTIFICATO", nome: "File non identificato dal contenuto", famiglia: "—", puid: [], mime: [], esito_base: "RIFIUTATO", pdfa: false, codice: "FORMATO.NON_IDENTIFICATO.ESITO" },
] };
const FIRME_PDV = { livello: "CRITTOGRAFICA", file: [
  { nome_file: "determina.pdf.p7m", esito: "valida", motivo: null, verificatore: "pyhanko 0.38.0", firme: [{ firmatario: "Dirigente del settore", indicazione: "TOTAL_PASSED", revoca: "NON_CONTROLLATO", motivi: [] }] },
  { nome_file: "allegato.pdf", esito: "indeterminata", motivo: null, verificatore: "pyhanko 0.38.0", firme: [{ firmatario: "Funzionario istruttore", indicazione: "INDETERMINATE", revoca: "NON_CONTROLLATO", motivi: ["il certificato non risale a un prestatore fidato (Trusted List)"] }] },
  { nome_file: "nota.xml", esito: "non firmato", motivo: "nessuna firma nel file", verificatore: "pyhanko 0.38.0", firme: [] },
] };

function regole(ruolo: Ruolo) {
  const cons = ruolo === "SUPER_ADMIN" || ruolo === "RSC_CONSERVATORE";
  const r = (codice: string, etichetta: string, effetto: string, origine: string, locked = false, norm: string | null = null) => ({
    codice, etichetta, fase: "METADATI", effetto, origine, effetto_predefinito: effetto, locked, norm_ref: norm,
    impostazione: `REGOLA.${codice}.EFFETTO`, ambito_ammesso: locked ? ["CONSERVATORE"] : ["CONSERVATORE", "ENTE", "CLASSE", "ACCORDO"],
    azioni_consentite: locked ? { modifica: false, motivo_blocco: "valore imposto dalla norma: si stabilisce solo al livello del conservatore" } : { modifica: true, motivo_blocco: null },
  });
  void cons;
  return [
    r("RGL-101", "Metadati minimi dell'Allegato 5 sul documento principale", "BLOCCA", "PREDEFINITO", true, "Linee guida AgID sul documento informatico (2021), Allegato 5"),
    r("RGL-102", "Metadati obbligatori della classe documentale", "BLOCCA", "PREDEFINITO"),
    r("RGL-103", "Metadati non previsti (conservati come aggiuntivi)", "AVVISA", "ENTE"),
    r("RGL-104", "Dimensione massima per file della classe", "BLOCCA", "PREDEFINITO"),
    r("RGL-105", "Formati ammessi dalla classe documentale", "BLOCCA", "PREDEFINITO", false, "Linee guida AgID sul documento informatico (2021), Allegato 2"),
    r("RGL-106", "Tipi dei metadati del profilo della classe", "BLOCCA", "PREDEFINITO"),
  ].map(x => ({ ...x, fase: x.codice === "RGL-104" || x.codice === "RGL-105" ? "FILE" : "METADATI" }));
}

const PIANO_1 = {
  impronta_piano: "a3f1c09b7e2d4f6a8b0c1d2e3f405162738495a6b7c8d9e0f1a2b3c4d5e6f708",
  modifiche: [{ codice: "REGOLA.RGL-103.EFFETTO", prima: "AVVISA", dopo: "BLOCCA", origine_prima: "ENTE", origine_dopo: "ENTE" }],
  bloccati: [], avvisi: [], dettagli: {},
};
const PIANO_2 = {
  ...PIANO_1,
  impronta_piano: "b7e2d4f6a8b0c1d2e3f405162738495a6b7c8d9e0f1a2b3c4d5e6f708a3f1c09",
  modifiche: [
    ...PIANO_1.modifiche,
    { codice: "FORMATI_AMMESSI", prima: ["application/pdf", "application/xml"], dopo: ["application/pdf", "application/xml"], origine_prima: "CONSERVATORE", origine_dopo: "CONSERVATORE" },
  ],
  avvisi: ["Nel frattempo il conservatore ha cambiato i formati ammessi: il valore ereditato è diverso da quello dell'anteprima."],
};
const IMPATTO = {
  pdv_ultimi_30_giorni_che_sarebbero_rifiutati: 12,
  pdv_ultimi_30_giorni_con_nuovi_avvisi: 0,
  rifiutati_con_la_configurazione_attuale: 3,
  non_valutabili: 1,
  esempi: [
    { pdv_id: "9d3e2f1a-0b4c-4d5e-8f60-718293a4b5c6", ricevuto_il: minuti(60 * 26), esito: "RIFIUTATO", rilievi: [{ regola: "RGL-103", effetto: "BLOCCA", campo: "metadati.codice_cig", documento: null, messaggio: "Metadato non previsto dalla classe FATTURA: codice_cig" }], avvisi: [] },
    { pdv_id: "1c2b3a49-5867-4d1e-9f0a-b1c2d3e4f5a6", ricevuto_il: minuti(60 * 75), esito: "RIFIUTATO", rilievi: [{ regola: "RGL-103", effetto: "BLOCCA", campo: "metadati.riferimento_ordine", documento: null, messaggio: "Metadato non previsto dalla classe FATTURA: riferimento_ordine" }], avvisi: [] },
  ],
  campione: { metodo: "ultimi versamenti dell'ambito", finestra_giorni: 30, limite: 2000, letti: 2000, raggiunto_limite: true, analizzati: 2000, tempo_max_s: 5, troncato_per_tempo: false, nota: null },
};

const BOZZA = (stato = "APERTA") => ({
  bozza_id: "b0220000-1111-4222-8333-444455556666", risorsa: "regole",
  ambito: { livello: "ENTE", ente: ENTE, classe: null, accordo: null },
  modifiche: [{ regola: "RGL-103", effetto: "BLOCCA" }], proposta: null,
  motivo: "Le fatture con metadati non concordati vanno respinte, non accolte con avviso.",
  versione: stato === "APERTA" ? 1 : 2, stato, creato_da: "u1", aggiornato_da: "u1",
  created_at: minuti(40), updated_at: minuti(stato === "APERTA" ? 40 : 0),
  applicata_il: stato === "APPLICATA" ? ORA : null, applicata_da: stato === "APPLICATA" ? "u1" : null,
  impronta_piano: stato === "APPLICATA" ? PIANO_2.impronta_piano : null, esito: null,
  valido_dal: stato === "PROGRAMMATA" ? "2026-12-01" : null, anteprima_programmata: null,
});

const pratica = (id: string, tipo: string, stato: string, extra: Record<string, unknown> = {}) => ({
  id, tipo, stato, versione: 3, ente: ENTE, motivo: "Fatture passive 2014 a fine termine decennale", pdv_id: null, pdv_risultato_id: null,
  richiede_nulla_osta: true, impronta_piano: null, creata_da: "u2", creata_ruolo: "RSC_CONSERVATORE",
  creata_il: minuti(60 * 24 * 9), aggiornata_il: minuti(60 * 5), conclusa_il: null, conteggi: {},
  esecuzione: { lavoro: null, tentativi: null, prossimo_tentativo: null, ultimo_errore: null, in_corso_fino_al: null }, ...extra,
});
const PRATICHE = [
  pratica("51a7e0aa-1111-4aaa-8aaa-000000000001", "SCARTO", "PROPOSTA", { conteggi: { SELEZIONATO: 1204, BLOCCATO: 37 } }),
  pratica("51a7e0aa-1111-4aaa-8aaa-000000000002", "SCARTO", "ESEGUIBILE", { conteggi: { SELEZIONATO: 418 }, motivo: "Delibere 2013 scartabili dopo il nulla osta n. 112/2026", aggiornata_il: minuti(60 * 30) }),
  pratica("51a7e0aa-1111-4aaa-8aaa-000000000003", "RETTIFICA", "COMPLETATA", { pdv_id: "9d3e2f1a-0b4c-4d5e-8f60-718293a4b5c6", motivo: "Data del documento errata nel versamento originale", richiede_nulla_osta: false, aggiornata_il: minuti(60 * 50) }),
  pratica("51a7e0aa-1111-4aaa-8aaa-000000000004", "ANNULLAMENTO", "ESEGUITA", { pdv_id: "1c2b3a49-5867-4d1e-9f0a-b1c2d3e4f5a6", motivo: "Versamento doppio dello stesso protocollo 2026/14872", richiede_nulla_osta: false, aggiornata_il: minuti(60 * 70) }),
  pratica("51a7e0aa-1111-4aaa-8aaa-000000000005", "RIVERSAMENTO_USCITA", "PROPOSTA", { ente: ENTE_2, conteggi: { SELEZIONATO: 88231 }, motivo: "Passaggio dell'archivio storico al polo regionale", richiede_nulla_osta: false, aggiornata_il: minuti(60 * 3) }),
  pratica("51a7e0aa-1111-4aaa-8aaa-000000000006", "RESTITUZIONE", "RESPINTA", { ente: ENTE_3, motivo: "Fine contratto richiesta per errore dall'ufficio", aggiornata_il: minuti(60 * 99) }),
];

function dettaglio(p: any, ruolo: Ruolo, stato?: string) {
  const s = stato ?? p.stato;
  const passi: [string | null, string, string, string, number][] = [
    [null, "BOZZA", "RSC_CONSERVATORE", "Fatture passive 2014 a fine termine decennale", 60 * 24 * 9],
  ];
  if (s !== "BOZZA") passi.push(["BOZZA", "PROPOSTA", "RSC_CONSERVATORE", "Proposta inviata al Comune per l'autorizzazione", 60 * 24 * 8]);
  if (s === "RESPINTA") passi.push(["PROPOSTA", "RESPINTA", "ADMIN_TENANT", "Il Comune ha ritirato l'elenco", 60]);
  if (["AUTORIZZATA_PRODUTTORE", "NULLA_OSTA", "ESEGUIBILE"].includes(s)) passi.push(["PROPOSTA", "AUTORIZZATA_PRODUTTORE", "ADMIN_TENANT", "Autorizzazione firmata dal dirigente dei servizi finanziari", 60 * 24 * 4]);
  const cons = ruolo === "SUPER_ADMIN" || ruolo === "RSC_CONSERVATORE";
  const azioni = s === "BOZZA" ? [
    { nuovo_stato: "PROPOSTA", allegati_richiesti: [], esecuzione: false, consentita: true, motivo_blocco: null },
    { nuovo_stato: "RESPINTA", allegati_richiesti: [], esecuzione: false, consentita: true, motivo_blocco: null },
  ] : s === "PROPOSTA" ? [
    { nuovo_stato: "AUTORIZZATA_PRODUTTORE", allegati_richiesti: ["AUTORIZZAZIONE_PRODUTTORE"], esecuzione: false, consentita: ruolo === "ADMIN_TENANT", motivo_blocco: ruolo === "ADMIN_TENANT" ? null : "Può autorizzare solo l'amministrazione dell'ente produttore" },
    { nuovo_stato: "RESPINTA", allegati_richiesti: [], esecuzione: false, consentita: true, motivo_blocco: null },
  ] : s === "AUTORIZZATA_PRODUTTORE" ? [
    { nuovo_stato: "NULLA_OSTA", allegati_richiesti: ["NULLA_OSTA"], esecuzione: false, consentita: true, motivo_blocco: null },
    { nuovo_stato: "RESPINTA", allegati_richiesti: [], esecuzione: false, consentita: true, motivo_blocco: null },
  ] : s === "ESEGUIBILE" ? [
    { nuovo_stato: "ESEGUITA", allegati_richiesti: [], esecuzione: true, consentita: cons, motivo_blocco: cons ? null : "Esegue il conservatore" },
  ] : [];
  return {
    ...p, stato: s, dati: { criteri: { classi: ["FATTURA"], anni: [2014], scadenza_entro: null } },
    cronologia: passi.map(([da, a, ruoloP, motivo, m]) => ({ da_stato: da, a_stato: a, utente_id: "u", ruolo: ruoloP, motivo, allegati: a === "AUTORIZZATA_PRODUTTORE" ? ["al1"] : [], il: minuti(m) })),
    autorizzazioni: [],
    allegati: s === "PROPOSTA" ? [] : [{ id: "al1", tipo: "AUTORIZZAZIONE_PRODUTTORE", nome_file: "autorizzazione_scarto_2014.pdf.p7m", content_type: "application/pkcs7-mime", dimensione: 184_233, sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", conserva_fino_a: "2036-10-23T00:00:00Z", firma: { presente: true, tipo: "CAdES", verificata: false }, caricato_da: "u3", caricato_il: minuti(60 * 24 * 4) }],
    oggetti: { conteggi: { SELEZIONATO: 1204, BLOCCATO: 37 }, elenco: `/api/v1/ciclo-vita/pratiche/${p.id}/oggetti` },
    azioni_consentite: azioni,
  };
}

const PIANO_SCARTO = (impronta = "5ca7a0e1d2c3b4a5968778695a4b3c2d1e0f1a2b3c4d5e6f708192a3b4c5d6e7") => ({
  impronta_piano: impronta, totale: 1241, selezionabili: 1204, bloccati: { RETENTION_NON_SCADUTA: 21, FASCICOLO_APERTO: 12, LEGAL_HOLD: 4 },
  esempi: {
    selezionabili: [{ aip_id: "a1", urn: "urn:IT:c_c107:FATTURA:2014:000118", scadenza: "2024-12-31T00:00:00Z" }, { aip_id: "a2", urn: "urn:IT:c_c107:FATTURA:2014:000119", scadenza: "2024-12-31T00:00:00Z" }],
    bloccati: [{ aip_id: "a3", urn: "urn:IT:c_c107:FATTURA:2014:000944", motivo: "LEGAL_HOLD", descrizione: "Blocco legale (legal hold) attivo su un oggetto del PdA nello storage" }],
    limite_esempi: 10,
  },
  ente: ENTE, criteri: { classi: ["FATTURA"], anni: [], scadenza_entro: "2025-12-31" }, motivo: "", richiede_nulla_osta: true, calcolata_il: ORA,
});

export interface Stato {
  /** Revisione R17: permessi effettivi forzati (ruolo personalizzato); se assenti quelli del ruolo. */
  permessi?: string[];
  inviatiAllaFirma?: string[];
  esibizioniRichieste?: any[];
  versamenti?: string[];
  ruolo: Ruolo;
  /** R07: elenco dei versamenti con un rifiutato rielaborabile. */
  versamentoRifiutato?: boolean;
  bozza: string;
  applicaChiamate: number;
  transizioni: { stato_atteso: string; nuovo_stato: string; motivo: string }[];
  approvazioni: { esito: string; motivo: string }[];
  /** R14: riprese chieste (POST /pratiche/{id}/riprendi). */
  riprese?: { versione: number; motivo: string }[];
  statoPratica: string;
  scartoChiamate: number;
  creata: boolean;
  /** Forza un 409 alla prossima transizione (stato cambiato da altri). */
  transizioneInConflitto: boolean;
  richieste: { metodo: string; percorso: string; corpo: unknown }[];
  tipoContenutoAllegato?: string;
  /** R06 (VD-C-127): limite d'inattività comunicato dal gateway (secondi). */
  inattivitaSecondi?: number;
  /** R06: la sessione è scaduta per inattività (401 AUTH-004 a ogni richiesta). */
  sessioneScaduta?: boolean;
  /** R06 (VD-C-138): natura dichiarata dell'ente. */
  natura?: string;
  /** R07 (D31, D29): preferenze personali salvate sul server e loro versione. */
  preferenze: Record<string, unknown>;
  versionePreferenze: number;
  /** R10: campanella, avvisi segnati come letti e metodi d'accesso della pagina pubblica. */
  campanella?: unknown;
  /** R13b: canali PEC registrati e fine della transizione del segreto del webhook. */
  canaliPec?: unknown[];
  webhookRuotato?: string;
  letti?: number;
  metodiAccesso?: string[];
  /** R12b (VD-C-156): le mie credenziali (gateway /api/v1/auth/credenziali). */
  credenziali?: CredenzialiFinte;
  /** R16 (VD-C-166): documenti da firmare dal responsabile e OTP chiesti. */
  firme?: FirmaFinta[];
  otpChiesti?: number;
  /** R15: documentazione del servizio (versioni generate, richieste, letture dello stato). */
  documentazione?: { versioni: Record<string, unknown>[]; generazioni: Record<string, unknown>[]; letture: number };
  /** R17 (VD-P-164): conferma firmata delle decisioni (attiva?), richieste al gateway,
   *  decisioni puntuali inviate a ciclo-vita (annullamento, rettifica). */
  conferme?: { attiva: boolean; richieste: Record<string, unknown>[]; decisioni: Record<string, unknown>[] };
}

export interface FirmaFinta {
  id: string; tipo: string; tipo_descrizione: string; titolo: string; nome: string; impronta_sha256: string;
  stato: string; dimensione: number; firmatario_atteso: string | null; firmato_da: string | null; firmato_il: string | null;
  certificato_soggetto: string | null; livello: string | null; archiviata: boolean; motivo: string | null; richiesto_il: string;
}

/** R16: documenti dimostrativi della pagina «Firme del responsabile» (nessun dato reale). */
export const FIRME_INIZIALI = (): FirmaFinta[] => [
  { id: "fr-nomina", tipo: "NOMINA", tipo_descrizione: "Nomina o delega", titolo: "Nomina del vicario del responsabile del servizio di conservazione",
    nome: "nomina_vicario_2026.pdf", impronta_sha256: "3f6c0e1a9b2d4c5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6", stato: "DA_FIRMARE", dimensione: 48_213,
    firmatario_atteso: null, firmato_da: null, firmato_il: null, certificato_soggetto: null, livello: null, archiviata: false, motivo: null, richiesto_il: minuti(35) },
  { id: "fr-piano-1", tipo: "PIANO", tipo_descrizione: "Piano di cessazione", titolo: "Piano di cessazione", nome: "piano_cessazione.json",
    impronta_sha256: "9a8b7c6d5e4f30211203f4e5d6c7b8a99a8b7c6d5e4f30211203f4e5d6c7b8a9", stato: "ARCHIVIATO", dimensione: 12_904,
    firmatario_atteso: null, firmato_da: "u1", firmato_il: minuti(60 * 26), certificato_soggetto: "Responsabile di prova (simulatore)", livello: "B-T",
    archiviata: true, motivo: null, richiesto_il: minuti(60 * 27) },
];

export interface CredenzialiFinte {
  metodi: string[];
  confermata: boolean;
  password: boolean;
  passkey: { id: string; nome: string; creata_il: string; ultimo_uso: string | null }[];
  codiciRimasti: number;
}

export async function installaFinto(page: Page, ruolo: Ruolo): Promise<Stato> {
  const stato: Stato = {
    ruolo, bozza: "APERTA", applicaChiamate: 0, transizioni: [], approvazioni: [], statoPratica: "BOZZA",
    scartoChiamate: 0, creata: false, transizioneInConflitto: false, richieste: [],
    preferenze: {}, versionePreferenze: 0,
  };
  const tenantUtente = ENTE;
  // Solo le chiamate al servizio: i moduli di Vite (/src/api/…) passano
  await page.route(url => url.pathname.startsWith("/api/"), async (route: Route) => {
    const req = route.request();
    const u = new URL(req.url());
    const p = u.pathname.replace(/^\/api/, "");
    const m = req.method();
    let corpo: any = null;
    try { corpo = req.postDataJSON(); } catch { corpo = null; }
    stato.richieste.push({ metodo: m, percorso: p + u.search, corpo });
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const errore = (e: { status: number; body: unknown }) => route.fulfill({ status: e.status, contentType: "application/problem+json", body: JSON.stringify(e.body) });
    const cons = stato.ruolo === "SUPER_ADMIN" || stato.ruolo === "RSC_CONSERVATORE";

    // R06 (VD-C-127): sessione scaduta per inattività
    if (stato.sessioneScaduta && !(p === "/v1/auth/sessione" && m === "DELETE")) {
      return errore({ status: 401, body: { codice: "AUTH-004", title: "Sessione scaduta per inattività", status: 401 } });
    }
    // Sessione (cookie HttpOnly: il browser non vede il token)
    if (p === "/v1/auth/sessione" && m === "DELETE") return route.fulfill({ status: 204 });
    if (p === "/v1/auth/sessione") {
      // revisione R17 (R17-18): scope e permessi del ruolo come il gateway vero
      return json({ utente: { tenant_id: tenantUtente, user_id: "u1", ruolo: stato.ruolo, ragione_sociale: cons ? "VerumDoc — conservatore" : "Comune di Borgo Immaginario", piano: "PA", scopes: SCOPE_RUOLO[stato.ruolo], permessi: stato.permessi ?? PERMESSI_RUOLO[stato.ruolo] }, inattivita_secondi: stato.inattivitaSecondi ?? 1800 });
    }
    // R12c: situazione dell'archivio dell'ente (quadro del servizio), dati dimostrativi
    if (p === "/dashboard/overview") {
      const tid = u.searchParams.get("tenant_id");
      const nome = tid === ENTE_2 ? "Azienda sanitaria di Valfittizia" : tid === ENTE_3 ? "Unione dei Comuni di Collefinto" : "Comune di Borgo Immaginario";
      return json({
        tenant: { id: tid, ragione_sociale: nome, storage_usato_gb: 41.3, storage_quota_gb: 100, storage_percentuale: 41.3 },
        aip_stats: { totale: 1284, conservati: 1271, per_stato: { CONSERVATO: 1271, IN_VALIDAZIONE: 6, IN_BUILD: 4, ERRORE: 3 } },
      });
    }
    if (p === "/dashboard/tenants") {
      return json({ items: [
        { id: ENTE, ragione_sociale: "Comune di Borgo Immaginario" },
        { id: ENTE_2, ragione_sociale: "Azienda sanitaria di Valfittizia" },
        { id: ENTE_3, ragione_sociale: "Unione dei Comuni di Collefinto" },
      ] });
    }
    // R07 (D31, D29): preferenze personali (onboarding), salvataggio con la versione letta
    if (p === "/v1/onboarding/preferenze" && m === "GET") {
      return json({ preferenze: stato.preferenze, versione: stato.versionePreferenze });
    }
    if (p === "/v1/onboarding/preferenze" && m === "PUT") {
      if (corpo?.versione !== stato.versionePreferenze) {
        return errore(problem(409, "ONB-412", "Preferenze modificate da un'altra sessione", { versione: stato.versionePreferenze }));
      }
      stato.preferenze = corpo.preferenze; stato.versionePreferenze += 1;
      return json({ preferenze: stato.preferenze, versione: stato.versionePreferenze });
    }
    // R06 (VD-C-138): natura dell'ente (onboarding)
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/natura$/.test(p) && m === "PUT") {
      stato.natura = corpo?.natura;
      return json({ id: p.split("/")[4], natura: corpo?.natura });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+$/.test(p) && m === "GET") {
      return json({ id: p.split("/")[4], natura: stato.natura ?? null, codice_ipa: "c_b999" });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/utenti$/.test(p)) {
      return json({ items: [] });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/classi$/.test(p)) {
      return json({ tenant_id: ENTE, total: 4, classi: [
        { id: CLASSE_FATT, codice: "FATTURA", nome: "Fatture passive", anni_conservazione: 10, attiva: true },
        { id: "c1a55e00-0000-4000-8000-00000000d002", codice: "DELIBERA", nome: "Delibere di giunta", anni_conservazione: 999, attiva: true },
        { id: "c1a55e00-0000-4000-8000-00000000c003", codice: "CONTRATTO", nome: "Contratti", anni_conservazione: 10, attiva: true },
        { id: "c1a55e00-0000-4000-8000-00000000e004", codice: "PROTOCOLLO", nome: "Registro di protocollo", anni_conservazione: 999, attiva: true },
      ] });
    }

    // ── Configurazione (Q1) ──
    if (p === "/v1/configurazione/impostazioni") {
      const livello = u.searchParams.get("livello") ?? "ENTE";
      const g = u.searchParams.get("gruppo");
      const items = g === "quote" ? quote(cons) : g === "firme" ? firme(cons) : g === "formati" ? formati()
        : (g && (cicloVita(g) ?? accessoR10(g))) || impostazioni(livello, stato.ruolo);
      // revisione R17 (R17-18): operatore e consultazione leggono soltanto (come il servizio)
      const lettore = stato.ruolo === "OPERATORE" || stato.ruolo === "VIEWER";
      const viste = lettore ? (items as any[]).map(v => ({ ...v, azioni_consentite: { modifica: false, motivo_blocco: "il tuo profilo consulta le impostazioni" } })) : items;
      return json({ items: viste, limit: 100, cursore: null, altri: false, contesto: { livello, ente: u.searchParams.get("ente"), classe: u.searchParams.get("classe"), accordo: null } });
    }
    if (p === "/v1/configurazione/formati") return json(CATALOGO_FORMATI);
    if (p === "/v1/configurazione/regole") {
      const livello = u.searchParams.get("livello") ?? "ENTE";
      return json({ items: regole(stato.ruolo), limit: 100, cursore: null, altri: false, contesto: { livello }, catalogo_regole: "2" });
    }
    if (p === "/v1/configurazione/bozze" && m === "POST") {
      return json({ bozza_id: BOZZA().bozza_id, versione: 1, stato: "APERTA" }, 201);
    }
    if (p === "/v1/configurazione/bozze") {
      return json({ items: [BOZZA(stato.bozza), {
        ...BOZZA(), bozza_id: "b0220000-1111-4222-8333-444455557777", risorsa: "quote", ambito: { livello: "ENTE", ente: ENTE_2, classe: null, accordo: null },
        modifiche: [{ codice: "QUOTA_PDV_GIORNO", valore: 60000 }], motivo: "Campagna di versamento straordinaria dell'archivio sanitario", updated_at: minuti(300),
      }], limit: 30, cursore: null, altri: false });
    }
    if (/^\/v1\/configurazione\/bozze\/[^/]+$/.test(p) && m === "GET") return json(BOZZA(stato.bozza));
    if (/^\/v1\/configurazione\/bozze\/[^/]+\/anteprima$/.test(p)) {
      // L'anteprima dà sempre il piano 1; applicarlo risponde 409 col piano 2, che si applica
      return json({ ...PIANO_1, bozza_id: BOZZA().bozza_id, versione: 1, impatto: IMPATTO });
    }
    if (/^\/v1\/configurazione\/bozze\/[^/]+\/applica$/.test(p)) {
      stato.applicaChiamate += 1;
      if (corpo?.impronta_piano === PIANO_1.impronta_piano) {
        return errore(problem(409, "CFG-409", "Il piano è cambiato dall'anteprima: nulla è stato scritto", { motivo: "PIANO_CAMBIATO", piano: PIANO_2 }));
      }
      if (corpo?.valido_dal) {   // R07 (D28): decorrenza futura = modifica programmata
        stato.bozza = "PROGRAMMATA";
        return json({ bozza_id: BOZZA().bozza_id, stato: "PROGRAMMATA", impronta_piano: corpo.impronta_piano, valido_dal: corpo.valido_dal, esito: null });
      }
      stato.bozza = "APPLICATA";
      return json({ bozza_id: BOZZA().bozza_id, stato: "APPLICATA", impronta_piano: corpo?.impronta_piano, esito: { valori_scritti: 2 } });
    }
    // R06 (VD-C-139): elenco degli accordi di versamento
    if (p === "/v1/configurazione/accordi") {
      return json({ items: [
        { codice: "PROTOCOLLO-WS", versione: 3, ente: ENTE, classe: "c1a55e00-0000-4000-8000-00000000e004", descrizione: "Versamento giornaliero del registro di protocollo", valido_dal: minuti(60 * 24 * 20), cessato: false },
        { codice: "FATTURE-SDI", versione: 1, ente: ENTE, classe: CLASSE_FATT, descrizione: null, valido_dal: minuti(60 * 24 * 90), cessato: false },
      ], limit: 30, cursore: null, altri: false });
    }
    if (p === "/v1/configurazione/profili") {
      const breve = (v: number, c: number, o: number, r: number, dal = "2026-01-01") => ({ versione: v, valido_dal: dal, valido_al: null, campi: c, obbligatori: o, da_riscontrare: r });
      return json({ items: [
        { id: "classe:" + CLASSE_FATT, tipo: "classe", codice: "FATTURA", nome: "Fatture passive", ente: ENTE, versioni: 3, in_vigore: breve(3, 14, 9, 0, "2026-07-01") },
        { id: "tipologia:documento_amministrativo_informatico", tipo: "tipologia", codice: "documento_amministrativo_informatico", nome: "Documento amministrativo informatico", estende: "documento_informatico", nota: null, versioni: 1, in_vigore: breve(1, 22, 13, 4) },
        { id: "tipologia:documento_informatico", tipo: "tipologia", codice: "documento_informatico", nome: "Documento informatico", estende: null, nota: null, versioni: 1, in_vigore: breve(1, 17, 11, 3) },
        { id: "tipologia:fattura", tipo: "tipologia", codice: "fattura", nome: "Fattura elettronica", estende: "documento_informatico", nota: "Derivato dal DM MEF 17/06/2014 e dalle specifiche FatturaPA", versioni: 2, in_vigore: breve(2, 21, 15, 2, "2026-10-01") },
      ], limit: 100, cursore: null, altri: false });
    }
    if (/^\/v1\/configurazione\/profili\/.+\/versioni$/.test(p)) {
      return json({ id: "tipologia:fattura", items: [
        { versione: 1, valido_dal: "2021-01-01", valido_al: "2026-09-30", origine: "predefinito", campi: [] },
        { versione: 2, valido_dal: "2026-10-01", valido_al: null, origine: "conservatore", norm_ref: "DM MEF 17/06/2014, art. 3", campi: [
          { nome: "identificativo_documento", obbligatorio: true, allegato5: "IdDoc", calcolato: false, da_riscontrare: false, descrizione: null },
          { nome: "impronta", obbligatorio: true, allegato5: "Impronta", calcolato: true, da_riscontrare: false, descrizione: "Calcolata dal servizio sul file versato" },
          { nome: "numero_fattura", obbligatorio: true, allegato5: null, calcolato: false, da_riscontrare: false, descrizione: null },
          { nome: "riservato", obbligatorio: true, allegato5: "Riservato", calcolato: false, da_riscontrare: true, descrizione: "Obbligatorietà da riscontrare sul testo ufficiale" },
        ] },
      ] });
    }
    if (p === "/v1/configurazione/proposte" && m === "GET") {
      const az = stato.ruolo === "ADMIN_TENANT" ? { accetta: true, rifiuta: true, motivo_blocco: null } : { accetta: false, rifiuta: false, motivo_blocco: "decide l'amministratore dell'ente" };
      return json({ items: [
        { proposta_id: "p1", bozza_id: "b9", ente: ENTE, codice: "REGOLA.RGL-103.EFFETTO", valore: "BLOCCA", motivo: "Allineamento ai nuovi accordi di versamento delle fatture", descrizione: "Effetto della regola RGL-103 — Metadati non previsti", stato: "PROPOSTA", motivo_risposta: null, risposto_da: null, risposto_il: null, created_at: minuti(60 * 20), azioni_consentite: az },
        { proposta_id: "p2", bozza_id: "b8", ente: ENTE, codice: "CLASSE_DIMENSIONE_MAX_MB", valore: 250, motivo: "Le planimetrie superano i 100 MB", descrizione: "Dimensione massima di un file della classe, in MB", stato: "ACCETTATA", motivo_risposta: "Serve all'ufficio tecnico per i progetti", risposto_da: "u3", risposto_il: minuti(60 * 48), created_at: minuti(60 * 72), azioni_consentite: { accetta: false, rifiuta: false, motivo_blocco: "proposta già decisa" } },
      ], limit: 30, cursore: null, altri: false });
    }
    if (p === "/v1/configurazione/valida") {
      const ind = corpo?.indice ?? {};
      const metadati = ind?.metadati ?? {};
      const rilievi = [];
      if (!metadati.oggetto) rilievi.push({ codice: "RGL-101", effetto: "BLOCCA", campo: "metadati.oggetto", documento: null, messaggio: "Metadato minimo dell'Allegato 5 assente: oggetto" });
      if (!metadati.numero_fattura) rilievi.push({ codice: "RGL-102", effetto: "BLOCCA", campo: "metadati.numero_fattura", documento: null, messaggio: "Metadato obbligatorio della classe FATTURA assente: numero_fattura" });
      return json({
        esito: rilievi.length ? "NON_CONFORME" : "CONFORME", rilievi,
        avvisi: [{ codice: "RGL-103", effetto: "AVVISA", campo: "metadati.codice_cig", documento: null, messaggio: "Metadato non previsto dalla classe: sarà conservato come aggiuntivo" }],
        regole_applicate: { catalogo_regole: "2" }, fasi_eseguite: ["forma", "metadati", "file"],
        controlli_non_eseguiti: ["formato riconosciuto dal contenuto del file (si usa il formato indicato)", "eseguibili e antivirus", "duplicati, chiave dell'unità documentaria e Idempotency-Key", "quote dell'ente"],
      });
    }

    // ── Ciclo di vita (Q2) ──
    if (p === "/v1/ciclo-vita/pratiche" && m === "GET") {
      const t = u.searchParams.get("tipo"), s = u.searchParams.get("stato");
      // R06 (VD-C-128): «in attesa di me» come il servizio (domain/stati.py, in_attesa_di)
      const attese: [string | null, string][] = u.searchParams.get("in_attesa_di") !== "me" ? []
        : cons ? [[null, "ESEGUIBILE"], [null, "AUTORIZZATA_PRODUTTORE"], [null, "NULLA_OSTA"], ["RIVERSAMENTO_USCITA", "PROPOSTA"], ["RIVERSAMENTO_INGRESSO", "PROPOSTA"]]
        : stato.ruolo === "ADMIN_TENANT" ? [["SCARTO", "BOZZA"], ["SCARTO", "PROPOSTA"], ["RESTITUZIONE", "PROPOSTA"]] : [];
      if (u.searchParams.get("in_attesa_di") === "me" && attese.length === 0) return json({ items: [], limit: 30, cursore: null, altri: false });
      const items = PRATICHE.filter(x => (!t || x.tipo === t) && (!s || x.stato === s)
        && (attese.length === 0 || attese.some(([tipoA, statoA]) => x.stato === statoA && (!tipoA || x.tipo === tipoA))));
      return json({ items, limit: Number(u.searchParams.get("limite") ?? 30), cursore: null, altri: false });
    }
    if (p === "/v1/ciclo-vita/pratiche/scarto/proposta") {
      stato.scartoChiamate += 1;
      if (!corpo?.impronta_piano) return json({ ...PIANO_SCARTO(), motivo: corpo?.motivo ?? "" });
      if (corpo.impronta_piano === PIANO_SCARTO().impronta_piano && !stato.creata && stato.scartoChiamate === 2) {
        // prima conferma: il piano è cambiato (un pacchetto è entrato in un fascicolo aperto)
        const nuovo = { ...PIANO_SCARTO("6db8b1f2e3d4c5b6a7988796a5b4c3d2e1f0a1b2c3d4e5f60718293a4b5c6d7e8"), selezionabili: 1203, bloccati: { RETENTION_NON_SCADUTA: 21, FASCICOLO_APERTO: 13, LEGAL_HOLD: 4 } };
        return errore(problem(409, "LC-409-PIANO-CAMBIATO", "Il piano è cambiato dall'anteprima: rivedere e confermare", { errore: "PIANO_CAMBIATO", piano: nuovo }));
      }
      stato.creata = true;
      return json(pratica("51a7e0aa-1111-4aaa-8aaa-0000000000aa", "SCARTO", "BOZZA", { conteggi: { SELEZIONATO: 1203, BLOCCATO: 38 }, motivo: corpo.motivo }), 201);
    }
    if (p === "/v1/ciclo-vita/stato-automatico") {
      return cons ? json(STATO_AUTOMATICO) : errore(problem(403, "LC-403-RUOLO", "Lo stato del lavoro automatico è del conservatore"));
    }
    // ── R17 (VD-P-164): conferma firmata delle decisioni sensibili ──
    if (p === "/v1/ciclo-vita/conferme" && m === "GET") {
      return json({ attiva: !!stato.conferme?.attiva, azioni_creazione: ["ANNULLAMENTO_RICHIEDI", "RETTIFICA_RICHIEDI"],
        azioni: ["SCARTO_APPROVA", "SCARTO_ESEGUI", "RIVERSAMENTO_USCITA_ESEGUI", "ANNULLAMENTO_RICHIEDI", "ANNULLAMENTO_APPROVA", "RETTIFICA_RICHIEDI", "PRATICA_RIPRENDI"] });
    }
    if ((p === "/v1/ciclo-vita/pratiche/annullamento" || p === "/v1/ciclo-vita/pratiche/rettifica") && m === "POST") {
      (stato.conferme ??= { attiva: false, richieste: [], decisioni: [] }).decisioni.push(corpo);
      if (stato.conferme.attiva && !corpo?.conferma) return errore(problem(403, "LC-403-CONFERMA", "Conferma firmata della decisione richiesta"));
      const tipo = p.endsWith("annullamento") ? "ANNULLAMENTO" : "RETTIFICA";
      return json(pratica("51a7e0aa-1111-4aaa-8aaa-0000000000d1", tipo, "RICHIESTA", { pdv_id: corpo?.pdv_id ?? null, motivo: corpo?.motivo, richiede_nulla_osta: false }), tipo === "ANNULLAMENTO" ? 201 : 202);
    }
    const mPr = p.match(/^\/v1\/ciclo-vita\/pratiche\/([^/]+)(\/[a-z]+)?$/);
    if (mPr && mPr[1] === R14_SOSPESA) {
      const cons = stato.ruolo === "SUPER_ADMIN" || stato.ruolo === "RSC_CONSERVATORE";
      const ripresa = (stato.riprese ?? []).length > 0;
      const base = dettaglio(pratica(R14_SOSPESA, "SCARTO", "ESEGUIBILE", { conteggi: { ELIMINATO: 418 }, versione: ripresa ? 6 : 5,
        motivo: "Delibere 2013 scartabili dopo il nulla osta n. 112/2026" }), stato.ruolo, "ESEGUIBILE");
      const esecuzione = ripresa
        ? { lavoro: "ESEGUI", tentativi: 0, prossimo_tentativo: minuti(0), ultimo_errore: null, in_corso_fino_al: null, lavoro_sospeso: null, ripresa_consentita: false }
        : { lavoro: null, tentativi: 30, prossimo_tentativo: null, in_corso_fino_al: null, lavoro_sospeso: "ESEGUI", ripresa_consentita: cons,
            ultimo_errore: { codice: "LC-409-TENTATIVI-ESAURITI", lavoro_sospeso: "ESEGUI", tentativi: 30,
              messaggio: "Lavoro ESEGUI sospeso dopo 30 tentativi falliti; ultimo errore: registro delle impronte non raggiungibile",
              ultimo: { codice: "LC-503-REGISTRO" } } };
      if (!mPr[2] && m === "GET") {
        return json({ ...base, dati: { esecuzione: { avviata_il: minuti(60 * 60) } }, esecuzione,
          azioni_consentite: [{ nuovo_stato: "ESEGUITA", allegati_richiesti: [], esecuzione: true, consentita: false,
            motivo_blocco: "L'esecuzione della pratica è iniziata: il servizio la porta a termine" }] });
      }
      if (mPr[2] === "/riprendi" && m === "POST") {
        (stato.riprese ??= []).push(corpo);
        return json({ ...pratica(R14_SOSPESA, "SCARTO", "ESEGUIBILE"), versione: 6 });
      }
    }
    if (mPr && (mPr[1] === R09_ECCEZIONI || mPr[1] === R09_ANNULLAMENTO)) {
      const [, id, coda] = mPr;
      if (!coda && m === "GET") return json(dettaglioR09(id, stato.ruolo));
      if (coda === "/approvazione") { stato.approvazioni.push(corpo); return json({ ...pratica(id, "ANNULLAMENTO", corpo.esito === "APPROVA" ? "ESEGUITA" : "RESPINTA") }); }
      if (coda === "/transizione") { stato.transizioni.push(corpo); return json({ ...pratica(id, "SCARTO", "ESEGUITA_CON_ECCEZIONI"), esecuzione_richiesta: true }, 202); }
    }
    if (mPr) {
      const [, id, coda] = mPr;
      const base = PRATICHE.find(x => x.id === id) ?? pratica(id, "SCARTO", stato.statoPratica, { conteggi: { SELEZIONATO: 1203, BLOCCATO: 38 } });
      const nuova = id.endsWith("aa");
      if (!coda && m === "GET") return json(dettaglio(base, stato.ruolo, nuova ? stato.statoPratica : undefined));
      if (coda === "/transizione") {
        stato.transizioni.push(corpo);
        if (stato.transizioneInConflitto) {
          stato.transizioneInConflitto = false;
          stato.statoPratica = "RESPINTA";
          return errore(problem(409, "LC-409-STATO-CAMBIATO", `Lo stato della pratica è RESPINTA, non ${corpo.stato_atteso}`, { errore: "STATO_CAMBIATO", stato_attuale: "RESPINTA", versione: 5 }));
        }
        if (nuova) stato.statoPratica = corpo.nuovo_stato;
        return json({ ...base, stato: corpo.nuovo_stato });
      }
      if (coda === "/allegati" && m === "POST") {
        stato.tipoContenutoAllegato = req.headers()["content-type"] ?? "";
        return json({ id: "al9", tipo: "AUTORIZZAZIONE_PRODUTTORE", sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", dimensione: 2048, nome_file: "autorizzazione.pdf.p7m", firma: { presente: true, tipo: "CAdES", verificata: false } }, 201);
      }
      if (coda === "/oggetti") {
        return json({ items: [
          { aip_id: "a1", pdv_id: null, urn: "urn:IT:c_c107:FATTURA:2014:000118", esito: "SELEZIONATO", motivo: null },
          { aip_id: "a3", pdv_id: null, urn: "urn:IT:c_c107:FATTURA:2014:000944", esito: "BLOCCATO", motivo: "LEGAL_HOLD" },
        ], limit: 30, cursore: null, altri: false });
      }
    }

    // ── Registro e verifiche (hashchain) ──
    if (p === "/dashboard/hashchain") {
      return json({ registro: { dimensione: 4_812_377, testimone_dimensione: 4_812_120, ultimo_checkpoint: {
        dimensione: 4_812_120, radice: "7f3a9c2e5b8d1f4a6c0e9b2d5f8a1c4e7b0d3f6a9c2e5b8d1f4a6c0e9b2d5f8a", emesso_il: minuti(1),
        stato_sigillo: "FIRMATO", motivo_sigillo: null, stato_testimone: "CONTROFIRMATO", controfirmato_il: minuti(0.5), esito_testimone: null,
      } } });
    }
    // R06 (VD-C-129): allarmi del registro e del testimone
    if (p === "/v1/hashchain/registro/allarmi") {
      const aperti = u.searchParams.get("aperti") !== "false";
      const voci = [
        { id: "testimone-3", fonte: "testimone", causa: "FIRMA_NON_VALIDA", aperto_il: minuti(40), ultimo_il: minuti(5), occorrenze: 7,
          dettagli: { prima_dimensione: 4_812_000, ultima_dimensione: 4_812_120, ultimo_motivo: "firma del checkpoint non verificabile" }, aperto: true, gestito_il: null, gestito_da: null, nota: null },
        { id: "blocco-1", fonte: "registro", causa: "COERENZA_NON_VERIFICABILE", aperto_il: minuti(60 * 30), ultimo_il: minuti(60 * 29), occorrenze: 2,
          dettagli: {}, aperto: false, gestito_il: minuti(60 * 28), gestito_da: "operatore-turno", nota: "ripristinato" },
      ].filter(a => !aperti || a.aperto);
      return json({ items: voci, limit: 20, cursore: null, altri: false, soglie: { controfirma_max_secondi: 600, checkpoint_secondi: 60 },
        condizioni: [{ codice: "CHECKPOINT_SENZA_CONTROFIRMA", attiva: false, descrizione: "Checkpoint in attesa della controfirma del testimone oltre CATENA_CONTROFIRMA_MAX_SECONDI", dettagli: {} }] });
    }
    if (p === "/v1/hashchain/verifiche/riepilogo") {
      return json({ ente: null, ciclo_giorni: 365, finestra_giorni: 365, pda_programmati: 4_812_377, pda_verificati_nella_finestra: 4_101_904, pda_mai_verificati: 210_455,
        copertura_percentuale: 85.2, copertura_calcolata_il: minuti(12), anomalie_aperte: { numero: 2, oltre_tetto: false, elenco: [] }, ritardo_rotazione_secondi: 3600 * 7,
        ultima_verifica_il: minuti(0.2), lavoratore: { ultimo_giro_il: minuti(0.2), detentore: "verifica-0", confronto_foglia: "REGISTRO" } });
    }
    if (p === "/v1/hashchain/verifiche/registro") {
      return json({ voci: [
        { verificato_il: minuti(80), ente: ENTE, pda_id: "a9", pda: "urn:IT:c_c107:DELIBERA:2019:000442", documento_id: "doc-1", componente: "FILE", esito: "DIVERSO", motivo: "Impronta letta diversa da quella registrata", tentativo: 1, impronta_attesa: "aa", impronta_trovata: "bb" },
        { verificato_il: minuti(300), ente: ENTE_2, pda_id: "a8", pda: "urn:IT:asl_rm6:REFERTO:2021:118204", documento_id: null, componente: "PDA", esito: "ERRORE_LETTURA", motivo: "Storage non raggiungibile (timeout)", tentativo: 3, impronta_attesa: null, impronta_trovata: null },
      ], cursore_successivo: null });
    }

    // ── Canali ──
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/sftp\/account$/.test(p) && m === "GET") {
      return json({ total: 2, items: [
        { id: "s1", nome_utente: "borgo-protocollo", descrizione: "Protocollo informatico (invio notturno)", stato: "ATTIVO", sospeso_il: null,
          responsabile: { id: "u3", email: "ced@example.org", nome: "Referente", cognome: "Di Prova", ruolo: "ADMIN_TENANT", attivo: true },
          chiavi: [{ id: "k1", algoritmo: "ssh-ed25519", bit: 256, impronta: "SHA256:9xQm1lV8kP3t0YzW2r5cN7dFh4jL6sA8eB0uI1oK2gE", commento: "server-protocollo", attiva: true, created_at: minuti(60 * 24 * 40), revocata_il: null }],
          connessione: { protocollo: "sftp", utente: "borgo-protocollo", host: "sftp.verumdoc.it", porta: 22 }, created_at: minuti(60 * 24 * 40),
          azioni_consentite: { sospendi: true, riattiva: false, aggiungi_chiave: true, modifica: true } },
        { id: "s2", nome_utente: "borgo-tributi", descrizione: "", stato: "SOSPESO", sospeso_il: minuti(60 * 24 * 2), responsabile: null, chiavi: [],
          connessione: { protocollo: "sftp", utente: "borgo-tributi", host: "sftp.verumdoc.it", porta: 22 }, created_at: minuti(60 * 24 * 90),
          azioni_consentite: { sospendi: false, riattiva: true, aggiungi_chiave: true, modifica: true } },
      ] });
    }
    // Catalogo dei tipi sottoscrivibili (notification, R05): la console non lo copia più
    if (p === "/v1/webhook/tipi" && m === "GET") {
      return json({ tipi: [
        { tipo: "catena.checkpoint", risorsa: "checkpoint", descrizione: "Nuovo checkpoint firmato del registro delle impronte." },
        { tipo: "pratica.cambio_stato", risorsa: "pratica", descrizione: "Una pratica del ciclo di vita è passata a un nuovo stato." },
        { tipo: "scarto.eseguito", risorsa: "pratica", descrizione: "Lo scarto autorizzato è stato eseguito." },
        { tipo: "versamento.annullato", risorsa: "versamento", descrizione: "Il PdV è stato annullato." },
        { tipo: "versamento.conservato", risorsa: "versamento", descrizione: "Il PdA è nel registro ed è coperto da un checkpoint." },
        { tipo: "versamento.rifiutato", risorsa: "versamento", descrizione: "La verifica del PdV ha trovato errori bloccanti." },
      ] });
    }
    if (p === "/v1/webhook" && m === "GET") {
      const ruotato = stato.webhookRuotato;
      return json({ sottoscrizioni: [
        { id: "w1", url: "https://protocollo.comune-immaginario.example/verumdoc/eventi", tipi: ["versamento.conservato", "versamento.rifiutato"], descrizione: "Gestionale del protocollo", attiva: true,
          segreto_suffisso: ruotato ? "N3wZ" : "x9Qa", creata_il: minuti(60 * 24 * 30), aggiornata_il: minuti(60 * 24 * 2),
          segreto_precedente_suffisso: ruotato ? "x9Qa" : null, transizione_fino_al: ruotato ?? null, segreto_ruotato_il: ruotato ? ORA : null },
      ] });
    }
    // R13b: rotazione del segreto del webhook (doppia firma nel periodo di transizione)
    if (/^\/v1\/webhook\/[^/]+\/segreto\/ruota$/.test(p) && m === "POST") {
      const fino = new Date(Date.parse(ORA) + (corpo?.transizione_ore ?? 24) * 3_600_000).toISOString();
      stato.webhookRuotato = fino;
      return json({ id: "w1", url: "https://protocollo.comune-immaginario.example/verumdoc/eventi", tipi: ["versamento.conservato"], descrizione: "Gestionale del protocollo", attiva: true,
        segreto_suffisso: "N3wZ", segreto_precedente_suffisso: "x9Qa", transizione_fino_al: fino, segreto_ruotato_il: ORA, creata_il: minuti(60 * 24 * 30), aggiornata_il: ORA,
        segreto: "whsec_nuovo-segreto-di-prova-0123456789-N3wZ" });
    }
    if (/^\/v1\/webhook\/[^/]+\/segreto\/chiudi-transizione$/.test(p) && m === "POST") {
      stato.webhookRuotato = undefined;
      return json({ id: "w1", url: "https://protocollo.comune-immaginario.example/verumdoc/eventi", tipi: ["versamento.conservato"], descrizione: "Gestionale del protocollo", attiva: true,
        segreto_suffisso: "N3wZ", segreto_precedente_suffisso: null, transizione_fino_al: null, segreto_ruotato_il: ORA, creata_il: minuti(60 * 24 * 30), aggiornata_il: ORA });
    }
    // R13b: canali PEC (notification)
    if (p === "/v1/notifiche/pec/canali" && m === "GET") {
      return json({ items: stato.canaliPec ?? [], tipi: ["SMTP_IMAP", "SVILUPPO"],
        segreti: { SMTP_IMAP: [{ nome: "password", obbligatorio: true }, { nome: "utente", obbligatorio: false }], SVILUPPO: [] },
        variabile_segreti: "PEC_SEGRETI_JSON", sviluppo_ammesso: false, messaggi_30_giorni: { CONSEGNATO: 3, ACCETTATO: 1 } });
    }
    if (/^\/v1\/notifiche\/pec\/canali\/[a-z0-9-]+$/.test(p) && m === "PUT") {
      const conf = corpo?.configurazione ?? {};
      if (Object.keys(conf).some(k => /secret|pin|password|otp/.test(k))) return errore(problem(422, "NTF-422-PEC", "Configurazione del canale PEC non valida"));
      const nuovo = { codice: p.split("/").pop(), ...corpo, versione: (corpo?.versione ?? 0) + 1, aggiornato_il: ORA, stato: null };
      stato.canaliPec = [...(stato.canaliPec ?? []).filter((c: { codice: string }) => c.codice !== nuovo.codice), nuovo];
      return json(nuovo);
    }
    if (/^\/v1\/notifiche\/pec\/canali\/[a-z0-9-]+\/prova$/.test(p) && m === "POST") {
      if (!/@pec\./.test(String(corpo?.destinatario ?? ""))) return errore(problem(422, "NTF-422-PEC", "Configurazione del canale PEC non valida", {}, "Indirizzo di prova non valido o segnaposto"));
      return json({ messaggio_id: "m-prova", stato: "IN_CODA", canale: p.split("/")[5], canale_attivo: true, nota: "" }, 202);
    }
    if (p === "/v1/notifiche/pec/messaggi" && m === "GET") return json({ items: MESSAGGI_PEC, cursore: null });
    if (/^\/v1\/notifiche\/pec\/messaggi\/[^/]+$/.test(p) && m === "GET") {
      return json({ ...MESSAGGI_PEC[0], testo: "Il conservatore ha registrato una proposta di scarto…", ricevute: RICEVUTE_PEC });
    }
    if (p === "/v1/notifiche/pec/ricevute" && m === "GET") {
      return json({ items: [{ ...RICEVUTE_PEC[0], id: 77, esito: "NON_AUTENTICA", messaggio_id: null, tipo: null, gestore: null, consegna: null, certificato_soggetto: null,
        motivo: "certificato del firmatario non emesso da un gestore PEC fra le radici del canale", data_ricevuta: null, ricevuta_il: minuti(40) }], cursore: null });
    }
    if (/^\/v1\/webhook\/[^/]+\/consegne$/.test(p)) {
      return json({ consegne: [
        { id: 88123, evento_id: 4401, tipo_evento: "versamento.rifiutato", stato: "FALLITA", tentativi: 8, riprove: 0, prossimo_tentativo: null, ultimo_stato_http: 503, ultimo_codice: "WH-103", ultimo_errore: "Il sistema ha risposto 503 per 8 tentativi", ultimo_tentativo_il: minuti(200), consegnata_il: null, creata_il: minuti(900) },
      ], cursore: 88123, altri: false });
    }

    // R07 (D31): arrivi per la scelta delle colonne visibili
    // R07: «Rielabora» di un proprio versamento rifiutato
    const PIANO_PDV = { elementi: [{ scarto_id: 7, coda: "pdv.received.dlq", forma: "pipeline", azione: "PdV da ERRORE a RICEVUTO e ripubblica su pdv.received", rielaborabile: true, motivo_blocco: null, avvisi: [], pdv_id: "pdv00000-0000-4000-8000-000000000002", tenant_id: ENTE }], rielaborabili: 1, bloccati: 0, avvisi: [], troncato: false, chi_sara_avvisato: "", impronta_piano: "a".repeat(64) };
    if (/^\/dashboard\/versamenti\/[^/]+\/rielabora\/anteprima$/.test(p) && m === "POST") return json(PIANO_PDV);
    if (/^\/dashboard\/versamenti\/[^/]+\/rielabora\/applica$/.test(p) && m === "POST") {
      return json({ rielaborati: 1, bloccati: 0, impronta_piano: PIANO_PDV.impronta_piano, elementi: PIANO_PDV.elementi });
    }
    if (p === "/dashboard/versamenti" && stato.versamentoRifiutato) {
      return json({ items: [{ pdv_id: "pdv00000-0000-4000-8000-000000000002", oggetto: "Determina n. 413/2026", stato: "ERRORE", data_ricezione: minuti(20), classe_documentale: { codice: "DETERMINA", nome: "Determine" }, motivo_rifiuto: "Errore del servizio durante la verifica", motivi_rifiuto: [] }], limit: 50, cursore: null, altri: false });
    }
    if (p === "/dashboard/versamenti") {
      return json({ items: [{ pdv_id: "pdv00000-0000-4000-8000-000000000001", oggetto: "Determina n. 412/2026", stato: "CONSERVATO", data_ricezione: minuti(30), classe_documentale: { codice: "DETERMINA", nome: "Determine" }, motivi_rifiuto: [], firme: FIRME_PDV }], limit: 20, cursore: null, altri: false });
    }
    // R07 (D18): rigenerazione di un PdD scaduto
    if (/^\/v2\/distribution\/pdd\/[^/]+\/rigenera$/.test(p) && m === "POST") {
      return json({ pdd_id: p.split("/")[4], stato: "IN_PREPARAZIONE", consegna: "ZIP", documenti: [] });
    }
    if (p === "/v2/distribution/storico") {
      return json({ items: [{ id: "n1", pdd_id: "pdd00000-0000-4000-8000-000000000001", tipo: "COMPLETO", canale: "PORTALE", stato: "INVIATO", aip_id: "a1b2c3d4-0000-4000-8000-000000000009", motivo: "Restituzione all'ente", data: minuti(60 * 24 * 5) }], limit: 10, cursore: null, altri: false });
    }

    // ── R10: campanella, preferenze delle notifiche, ruoli, inviti, fornitori, metodi ──
    if (p === "/v1/notifiche/campanella" && m === "GET") return json(stato.campanella ?? { da_fare: [], avvisi: [], non_letti: 0, intervallo_aggiornamento_secondi: 60 });
    if (p === "/v1/notifiche/campanella/letti" && m === "POST") { stato.letti = (stato.letti ?? 0) + 1; return json({ letti: 1 }); }
    if (p === "/v1/notifiche/preferenze") {
      if (m === "PUT" && corpo?.scelte?.ALLARMI?.console === false) return errore(problem(422, "NTF-422-OBBLIGATORIA", "Avviso obbligatorio", {}, "Avvisi obbligatori per l'ente: ALLARMI.CONSOLE"));
      const categorie = [["PRATICHE", "Pratiche da approvare"], ["QUOTE", "Quote vicine al limite"], ["ALLARMI", "Allarmi d'integrità"]].map(([codice, nome]) => ({
        codice, nome, console: true, email: m === "PUT" ? !!corpo?.scelte?.[codice]?.email : false,
        console_obbligatoria: codice === "ALLARMI", email_obbligatoria: false, pertinente: true }));
      return json({ versione: m === "PUT" ? 1 : 0, categorie });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/ruoli$/.test(p) && m === "GET") return json(CATALOGO_RUOLI);
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/ruoli$/.test(p) && m === "POST") {
      if ((corpo?.permessi ?? []).includes("scarto:esegui")) return errore(problem(403, "RUO-403", "Permessi non concedibili", {}, "Questi permessi restano solo ai ruoli fissi: scarto:esegui"));
      return json({ ...CATALOGO_RUOLI.items[0], id: "r0000000-0000-4000-8000-000000000002", nome: corpo?.nome, ruolo_base: corpo?.ruolo_base, permessi: corpo?.permessi, utenti: 0 }, 201);
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/inviti$/.test(p) && m === "GET") {
      return json({ items: [{ id: "i1", user_id: "u9", email: "nuova.persona@comune-immaginario.it", nome: "Nuova", cognome: "Persona", stato: "IN_ATTESA", scade_il: ORA, usato_il: null, metodo_attivato: null, creato_il: minuti(60) }] });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/inviti$/.test(p) && m === "POST") return json({ id: "i2", user_id: "u10", email: corpo?.email, stato: "IN_ATTESA", scade_il: ORA }, 201);
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/inviti\/[^/]+\/reinvia$/.test(p)) return json({ id: "i3", stato: "IN_ATTESA", scade_il: ORA });
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/accesso$/.test(p)) {
      return json({ metodi_con_fornitore_attivo: ["SPID_CIE", "PASSWORD_TOTP"], metodi_del_conservatore: ["SPID_CIE", "PASSWORD_TOTP"], metodi_ammessi: ["SPID_CIE"], invito_validita_ore: 72, password_lunghezza_minima: 12, nomi: {} });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/sftp\/account\/[^/]+\/ip-ammessi$/.test(p) && m === "PUT") {
      if ((corpo?.ip_ammessi ?? []).includes("0.0.0.0/0")) return errore(problem(422, "SFTP-422-IP", "Elenco di indirizzi non valido", {}, "«0.0.0.0/0» è troppo ampia: al massimo /16"));
      return json({ ip_ammessi: corpo?.ip_ammessi });
    }
    // R13a: fornitori di firma e marca
    if (p === "/v1/onboarding/firma/fornitori" && m === "GET") {
      return json({ items: [{ codice: "csc-principale", ruolo: "FIRMA", tipo: "CSC", nome: "Firma remota", attivo: true, priorita: 1,
        configurazione: { url_base: "https://rssp.prova.example/csc/v2", url_token: "https://rssp.prova.example/oauth2/token", client_id: "vd" }, versione: 1, aggiornato_il: ORA }],
        ruoli: { FIRMA: ["CSC", "PKCS11", "SVILUPPO"], MARCA: ["RFC3161", "SVILUPPO"] },
        segreti: { CSC: [{ nome: "client_secret", obbligatorio: true }, { nome: "pin", obbligatorio: false }], PKCS11: [{ nome: "pin", obbligatorio: true }], RFC3161: [{ nome: "password", obbligatorio: false }], SVILUPPO: [] },
        sviluppo_ammesso: false });
    }
    if (p === "/v1/onboarding/firma/fornitori/stato" && m === "GET") {
      return json({ origine: "registro", ultima_lettura: ORA, errore_lettura: null, production_ready: false,
        parametri: { livello: "B-LT", algoritmo: "sha256", algoritmi_ammessi: ["sha256", "sha384", "sha512"] },
        fornitori: [{ codice: "csc-principale", ruolo: "FIRMA", tipo: "CSC", nome: "Firma remota", priorita: 1, costruito: true, problemi: [],
          segreti_presenti: { client_secret: true, pin: true }, vietato_in_produzione: false,
          certificato: { raggiungibile: true, soggetto: "Sigillo di prova del conservatore", valido_al: ORA, giorni_alla_scadenza: 45, dichiara_qualificato: true } }] });
    }
    if (/^\/v1\/onboarding\/firma\/fornitori\/[a-z0-9-]+\/prova$/.test(p) && m === "POST") {
      return json({ codice: p.split("/")[5], esito: "OK", durata_ms: 812, ruolo: "FIRMA", verificata: true, qualificata: true, livello: "B-T" });
    }
    if (/^\/v1\/onboarding\/firma\/fornitori\/[a-z0-9-]+$/.test(p) && m === "PUT") {
      const conf = corpo?.configurazione ?? {};
      if (Object.keys(conf).some(k => /secret|pin|password|otp/.test(k))) return errore(problem(422, "FIRMA-422-CONFIGURAZIONE", "Configurazione del fornitore non valida"));
      return json({ codice: p.split("/").pop(), ...corpo, versione: (corpo?.versione ?? 0) + 1, aggiornato_il: ORA });
    }
    if (p === "/v1/onboarding/accesso/fornitori" && m === "GET") {
      return json({ items: ["SPID_CIE", "PASSKEY", "PASSWORD_TOTP"].map(tipo => ({ tipo, nome: tipo === "SPID_CIE" ? "SPID e CIE (aggregatore)" : tipo === "PASSKEY" ? "Passkey" : "Password e codice a tempo", registrato: false, attivo: false, configurazione: {}, versione: 0, aggiornato_il: null })) });
    }
    if (/^\/v1\/onboarding\/accesso\/fornitori\/[A-Z_]+$/.test(p) && m === "PUT") {
      if (corpo?.configurazione?.client_secret) return errore(problem(422, "ACC-422-FORNITORE", "Configurazione del fornitore non valida"));
      return json({ tipo: p.split("/").pop(), nome: corpo?.nome, registrato: true, attivo: corpo?.attivo, configurazione: corpo?.configurazione, versione: 1, aggiornato_il: ORA });
    }
    if (p === "/v1/auth/metodi") return json({ metodi: stato.metodiAccesso ?? [] });
    if (p === "/v1/auth/locale/accesso" && m === "POST") {
      if (corpo?.password !== "una frase lunga di prova") return errore(problem(401, "ACC-401", "Accesso non riuscito"));
      return json({ esito: "secondo_fattore", passaggio: "passaggio-di-prova-0123456789abcdef", scadenza_secondi: 300 });
    }
    if (p === "/v1/auth/locale/secondo-fattore" && m === "POST") {
      if (corpo?.codice !== "123456") return errore(problem(401, "ACC-401", "Accesso non riuscito"));
      return json({ esito: "accesso", utente: { tenant_id: tenantUtente, user_id: "u1", ruolo: stato.ruolo, scopes: ["read"], ragione_sociale: "Studio privato di prova", piano: "business" } });
    }
    if (p === "/v1/auth/inviti/verifica" && m === "POST") {
      if (corpo?.email !== "nuova.persona@comune-immaginario.it") return errore(problem(404, "ACC-404-INVITO", "Invito non valido"));
      return json({ passaggio: "attivazione-di-prova-0123456789abcdef", ente: "Comune di Borgo Immaginario", nome: "Nuova", metodi: ["PASSWORD_TOTP"], password_lunghezza_minima: 12 });
    }
    if (p === "/v1/auth/inviti/totp" && m === "POST") return json({ segreto: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", uri: "otpauth://totp/VerumDoc" });
    if (p === "/v1/auth/inviti/completa" && m === "POST") {
      if (corpo?.codice !== "123456") return errore(problem(422, "ACC-422-CODICE", "Codice a tempo non valido"));
      return json({ esito: "attivato_e_accesso", metodo: "PASSWORD_TOTP", codici_recupero: ["ABCD-EFGH-JKLM-NPQR", "STUV-WXYZ-2345-6789"],
        utente: { tenant_id: tenantUtente, user_id: "u9", ruolo: "OPERATORE", scopes: ["read"], ragione_sociale: "Comune di Borgo Immaginario", piano: "PA" } });
    }

    // ── R17 (VD-P-164): il gateway firma la conferma dopo una conferma dell'identità
    //    recente, una volta sola (poi serve una nuova conferma dell'identità) ──
    if (p === "/v1/auth/conferme-decisione" && m === "POST") {
      const c = stato.credenziali ??= { metodi: ["SPID_CIE", "PASSWORD_TOTP"], confermata: false, password: true, passkey: [], codiciRimasti: 7 };
      (stato.conferme ??= { attiva: true, richieste: [], decisioni: [] }).richieste.push(corpo);
      if (!c.confermata) return errore(problem(403, "ACC-403-RIAUTENTICAZIONE", "Conferma dell'identità richiesta"));
      c.confermata = false;
      return json({ conferma: { payload: "eyJmaW50byI6dHJ1ZX0", firma: "ZmlybWEtZGktcHJvdmEtZGVsLWdhdGV3YXk" }, kid: "gw-1", scadenza_secondi: 300 });
    }
    // ── R12b (VD-C-156): le mie credenziali ──
    if (p.startsWith("/v1/auth/credenziali")) {
      const c = stato.credenziali ??= { metodi: ["SPID_CIE", "PASSWORD_TOTP"], confermata: false, password: true, passkey: [], codiciRimasti: 7 };
      const locali = c.metodi.filter(x => x === "PASSKEY" || x === "PASSWORD_TOTP");
      const ACC403 = problem(403, "ACC-403-RIAUTENTICAZIONE", "Conferma dell'identità richiesta");
      const sotto = p.slice("/v1/auth/credenziali".length);
      if (sotto === "" && m === "GET") {
        return json({
          disponibile: locali.length > 0, motivo: locali.length ? null : "L'ente ammette solo SPID o CIE: le credenziali si gestiscono presso il gestore dell'identità digitale.",
          metodi_ente: c.metodi, metodi_locali: locali, passkey: c.metodi.includes("PASSKEY") ? c.passkey : [],
          password: { impostata: c.password, impostata_il: c.password ? minuti(60 * 24 * 40) : null },
          totp: { attivo: c.password, confermato_il: c.password ? minuti(60 * 24 * 40) : null },
          codici_recupero: { rimasti: c.codiciRimasti, totale: 10 }, spid_cie: false,
          riautenticazione: { metodi: [...(c.password && c.metodi.includes("PASSWORD_TOTP") ? ["PASSWORD_TOTP"] : []), ...(c.passkey.length && c.metodi.includes("PASSKEY") ? ["PASSKEY"] : [])],
            valida_per_secondi: c.confermata ? 300 : null, finestra_minuti: 5 },
          password_lunghezza_minima: 12,
        });
      }
      if (sotto === "/riautenticazione/passkey/opzioni") return json({ opzioni: { challenge: "c2ZpZGEtZGktcHJvdmE", rpId: "localhost", allowCredentials: c.passkey.map(() => ({ type: "public-key", id: "Y3JlZGVuemlhbGUwMQ" })), userVerification: "required" } });
      if (sotto === "/riautenticazione" && m === "POST") {
        const ok = corpo?.metodo === "PASSKEY" ? !!corpo?.credenziale : corpo?.password === "una frase lunga di prova" && (corpo?.codice === "123456" || !!corpo?.codice_recupero);
        if (!ok) return errore(problem(422, "ACC-422-RIAUTENTICAZIONE", "Credenziali non corrette"));
        c.confermata = true;
        return json({ esito: "confermata", valida_per_secondi: 300, finestra_minuti: 5 });
      }
      if (!c.confermata) return errore(ACC403);
      if (sotto === "/passkey/opzioni") {
        return json({ opzioni: { challenge: "c2ZpZGEtZGktcHJvdmE", rp: { id: "localhost", name: "VerumDoc" }, user: { id: "dXRlbnRl", name: "persona@comune-immaginario.it", displayName: "persona@comune-immaginario.it" },
          pubKeyCredParams: [{ type: "public-key", alg: -7 }], excludeCredentials: [] } });
      }
      if (sotto === "/passkey" && m === "POST") {
        const nuova = { id: `pk000000-0000-4000-8000-0000000000${String(c.passkey.length + 1).padStart(2, "0")}`, nome: corpo?.nome ?? "Passkey", creata_il: ORA, ultimo_uso: null };
        c.passkey.push(nuova);
        return json(nuova, 201);
      }
      const pk = /^\/passkey\/([^/]+)$/.exec(sotto);
      if (pk && m === "PATCH") {
        const k = c.passkey.find(x => x.id === pk[1]);
        if (k) k.nome = corpo?.nome;
        return json({ id: pk[1], nome: corpo?.nome });
      }
      if (pk && m === "DELETE") {
        const altre = c.passkey.length - 1 + (c.password && c.metodi.includes("PASSWORD_TOTP") ? 1 : 0);
        if (altre < 1) return errore(problem(409, "ACC-409-ULTIMA", "Ultima credenziale utilizzabile"));
        c.passkey = c.passkey.filter(x => x.id !== pk[1]);
        return json({ esito: "revocata", id: pk[1], altre_sessioni_chiuse: 1 });
      }
      if (sotto === "/password" && m === "PUT") {
        if (corpo?.password_attuale !== "una frase lunga di prova") return errore(problem(422, "ACC-422-RIAUTENTICAZIONE", "Credenziali non corrette", {}, "La password attuale non è corretta."));
        return json({ esito: "cambiata", altre_sessioni_chiuse: 1 });
      }
      if (sotto === "/totp" && m === "POST") return json({ segreto: "KRSXG5CTMVRXEZLUKN2XAZLSKNSWG4TF", uri: "otpauth://totp/VerumDoc" });
      if (sotto === "/totp/conferma" && m === "POST") {
        if (corpo?.codice !== "123456") return errore(problem(422, "ACC-422-CODICE", "Codice a tempo non valido"));
        return json({ esito: "attivato", altre_sessioni_chiuse: 0 });
      }
      if (sotto === "/codici-recupero" && m === "POST") {
        c.codiciRimasti = 10;
        return json({ esito: "rigenerati", codici_recupero: ["QWER-TYUP-ASDF-GHJK", "ZXCV-BNMQ-WERT-YUPA"] });
      }
    }

    // ── R15: documentazione del servizio (dashboard-api) ──
    if (p.startsWith("/dashboard/documentazione-servizio")) {
      if (!cons) return errore(problem(403, "AUTH-010", "Permesso mancante", {}, "La documentazione del servizio la genera il responsabile del servizio di conservazione"));
      const d = (stato.documentazione ??= { versioni: [], generazioni: [], letture: 0 });
      const sotto = p.slice("/dashboard/documentazione-servizio".length);
      if (sotto === "/generazioni" && m === "POST") {
        const g = { id: "gen-r15-1", stato: "IN_CORSO", richiesta_il: minuti(0), scade_il: minuti(-10), conclusa_il: null, esito: {}, errore: null };
        d.generazioni = [g];
        return json({ ...g, nuova: true }, 202);
      }
      const mg = /^\/generazioni\/([^/]+)$/.exec(sotto);
      if (mg) {
        const g = d.generazioni.find(x => x.id === mg[1]);
        if (!g) return errore(problem(404, "GOV-404", "Non trovato"));
        d.letture += 1;
        if (g.stato === "IN_CORSO" && d.letture >= 2) {
          Object.assign(g, { stato: "COMPLETATA", conclusa_il: minuti(0), esito: { nuove: VERSIONI_R15.map(v => ({ tipo: v.tipo, versione: 1 })), invariate: [] } });
          d.versioni = VERSIONI_R15;
        }
        return json(g);
      }
      if (sotto === "" && m === "GET") {
        return json({ documenti: ["MANUALE_CONSERVAZIONE", "PIANO_SICUREZZA", "GUIDA_FUNZIONALITA", "CATALOGO_ROUTE"].map(t => ({ tipo: t, ultima: d.versioni.find(v => v.tipo === t) ?? null })), generazioni: d.generazioni });
      }
      if (sotto === "/versioni" && m === "GET") {
        const t = u.searchParams.get("tipo");
        return json({ tipo: t, items: d.versioni.filter(v => v.tipo === t) });
      }
      const ms = /^\/versioni\/([^/]+)\/(markdown|html)$/.exec(sotto);
      if (ms) {
        return route.fulfill({ status: 200, contentType: ms[2] === "html" ? "text/html; charset=utf-8" : "text/markdown; charset=utf-8",
          headers: { "content-disposition": `attachment; filename="manuale_conservazione_v1.${ms[2] === "html" ? "html" : "md"}"`, "x-impronta-sha256": "e".repeat(64) },
          body: "# Manuale di conservazione\n" });
      }
    }

    // ── Fascicoli ──
    if (p === "/v2/fascicoli" && m === "GET") {
      return json({ items: [
        { id: "f1", codice: "FASC-2026-0412", titolo: "Lavori di adeguamento sismico della scuola primaria «Marconi»", descrizione: null, tipo: "PROCEDIMENTO", anno: 2026, stato: "APERTO", data_apertura: "2026-02-11", data_chiusura: null, num_aip: 37, created_at: minuti(60 * 24 * 250) },
        { id: "f2", codice: "FASC-2025-0098", titolo: "Contratto di servizio per la raccolta dei rifiuti 2025–2029", descrizione: null, tipo: "CONTRATTO", anno: 2025, stato: "CHIUSO", data_apertura: "2025-01-20", data_chiusura: "2025-12-31", num_aip: 12, created_at: minuti(60 * 24 * 600) },
      ], limit: 30, cursore: null, altri: false });
    }

    // ── R16 (VD-C-166): firme del responsabile (dashboard-api) ──
    if (p.startsWith("/dashboard/firme-responsabile")) {
      if (!cons) return errore(problem(403, "AUTH-010", "Operazione non consentita"));
      const firme = (stato.firme ??= FIRME_INIZIALI());
      if (p === "/dashboard/firme-responsabile" && m === "GET") {
        return json({ items: firme, tipi: { MANUALE: "Manuale di conservazione", PIANO: "Piano di cessazione", PIANO_SICUREZZA: "Piano della sicurezza", NOMINA: "Nomina o delega" } });
      }
      // revisione R17 (R17-04): in firma la versione STAMPABILE (HTML) della documentazione
      const daDoc = /^\/dashboard\/firme-responsabile\/documentazione\/([^/]+)$/.exec(p);
      if (daDoc && m === "POST") {
        const v = (stato.documentazione?.versioni ?? VERSIONI_R15).find(x => x.id === daDoc[1]);
        if (!v) return errore(problem(404, "GOV-404", "Versione non trovata"));
        if (v.tipo !== "MANUALE_CONSERVAZIONE" && v.tipo !== "PIANO_SICUREZZA") return errore(problem(422, "GOV-422-FIRMA", "Si firmano il manuale e il piano della sicurezza"));
        const manuale = v.tipo === "MANUALE_CONSERVAZIONE";
        const nuovo: FirmaFinta = { id: `fr-doc-${v.id}`, tipo: manuale ? "MANUALE" : "PIANO_SICUREZZA",
          tipo_descrizione: manuale ? "Manuale di conservazione" : "Piano della sicurezza", titolo: `${v.titolo} — versione ${v.versione}`,
          nome: `${manuale ? "manuale_conservazione" : "piano_sicurezza"}_v${v.versione}.html`, impronta_sha256: v.impronta_html, stato: "DA_FIRMARE",
          dimensione: 81_337, firmatario_atteso: null, firmato_da: null, firmato_il: null, certificato_soggetto: null, livello: null, archiviata: false,
          motivo: null, richiesto_il: ORA };
        stato.inviatiAllaFirma = [...(stato.inviatiAllaFirma ?? []), v.id];
        const gia = firme.find(f => f.id === nuovo.id);
        if (!gia) firme.unshift(nuovo);
        return json({ ...(gia ?? nuovo), nuovo: !gia });
      }
      const azione = /^\/dashboard\/firme-responsabile\/([^/]+)\/(otp|firma|annulla|contenuto|busta)$/.exec(p);
      const doc = azione ? firme.find(f => f.id === azione[1]) : undefined;
      if (azione && !doc) return errore(problem(404, "GOV-404", "Documento non trovato"));
      if (azione && doc) {
        if (azione[2] === "contenuto") {
          return doc.nome.endsWith(".html")
            ? route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>${doc.titolo}</title></head><body><h1>${doc.titolo}</h1><p>Versione stampabile generata dalla configurazione del servizio.</p></body></html>` })
            : route.fulfill({ status: 200, contentType: "application/pdf", body: "%PDF-1.7 documento di prova" });
        }
        if (azione[2] === "busta") return route.fulfill({ status: 200, contentType: "application/pkcs7-mime", body: "busta di prova" });
        if (doc.stato !== "DA_FIRMARE") return errore(problem(409, "GOV-409-FIRMA", "Documento non firmabile"));
        if (azione[2] === "otp") { stato.otpChiesti = (stato.otpChiesti ?? 0) + 1; return json({ inviato: true, fornitore: "simulatore-csc" }); }
        if (azione[2] === "annulla") { Object.assign(doc, { stato: "ANNULLATO", motivo: corpo?.motivo ?? null }); return json({ id: doc.id, stato: "ANNULLATO" }); }
        if (corpo?.otp !== "246810") return errore(problem(422, "FIRMA-422-OTP", "OTP o PIN non accettato", {}, "Il prestatore ha rifiutato il codice monouso."));
        Object.assign(doc, { stato: "FIRMATO", firmato_da: "u1", firmato_il: ORA, certificato_soggetto: "Responsabile di prova (simulatore)", livello: "B-T" });
        return json({ id: doc.id, stato: "FIRMATO", livello: "B-T", firmatario: "Responsabile di prova (simulatore)", data_marca: ORA, avvisi: [] });
      }
    }

    // ── Revisione R17 (R17-18): dati delle pagine centrali, così che siano provate ──
    if (p === "/dashboard/aip" && m === "GET") {
      if (stato.ruolo !== "SUPER_ADMIN" && stato.ruolo !== "RSC_CONSERVATORE" && u.searchParams.get("tenant_id") !== ENTE) return errore(problem(403, "AUTH-010", "Ente non consentito"));
      return json({ items: AIP_R17, limit: 20, cursore: null, altri: false, campi_allegato5: [] });
    }
    if (p === "/dashboard/search" && m === "POST") {
      const q = String(corpo?.query ?? "").toLowerCase();
      const items = AIP_R17.filter(a => !q || `${a.oggetto} ${a.urn}`.toLowerCase().includes(q));
      return json({ items, limit: corpo?.limit ?? 20, cursore: null, altri: false, motore: "indice", filtri_applicati: {}, campi_testo: ["oggetto"] });
    }
    if (p === "/dashboard/classi-documentali" && m === "GET") {
      return json({ items: CLASSI_R17, total: CLASSI_R17.length });
    }
    if (p === "/dashboard/notifiche" && m === "GET") {
      return json({ items: [
        { id: "nt1", tipo: "aip.conserved", stato: "INVIATA", canale: "EMAIL", destinatario: "protocollo@comune-immaginario.example", aip_id: AIP_R17[0].aip_id, data: minuti(90), inviato_at: minuti(89), tentativi: 1, oggetto: "Pacchetto conservato: Determina n. 412/2026" },
        { id: "nt2", tipo: "pdv.invalid", stato: "NON_PARTITA", canale: "WEBHOOK", destinatario: "https://gestionale.comune-immaginario.example/esiti", data: minuti(300), tentativi: 8, errore: "Il sistema ha risposto 503 per 8 tentativi", oggetto: "Versamento rifiutato" },
      ], limit: 20, cursore: null, altri: false });
    }
    if (p === "/dashboard/audit" && m === "GET") {
      return json({ items: [
        { id: "au1", data: minuti(15), evento: "distribuzione.richiesta", user_id: "u1", ip_address: "10.0.0.12", dettagli: { pdd_id: "pdd00000-0000-4000-8000-000000000001", tipo: "ESIBIZIONE" } },
        { id: "au2", data: minuti(240), evento: "configurazione.applicata", user_id: "u3", ip_address: "10.0.0.7", dettagli: { bozza_id: "b9" } },
      ], limit: 50, cursore: null, altri: false });
    }
    if (p === "/dashboard/scarti/code" && m === "GET") {
      return json({ code: [{ coda: "pdv.received.dlq", totale: 3, da_gestire: 1, ultimo: minuti(45) }, { coda: "notification.dlq", totale: 0, da_gestire: 0, ultimo: null }] });
    }
    if (p === "/dashboard/scarti" && m === "GET") {
      return json({ items: [{ id: 7, coda: "pdv.received.dlq", stato: "DA_GESTIRE", motivo: "Errore del servizio durante la verifica", pdv_id: "pdv00000-0000-4000-8000-000000000002", tenant_id: ENTE, tentativi: 3, ricevuto_il: minuti(45), forma: "pipeline" }], limit: 20, cursore: null, altri: false });
    }
    if (p === "/dashboard/sla/report" && m === "GET") {
      return json({ mese: "2026-10", sla_targets: { uptime_pct: 99.5, disponibilita_pct: 99.5, versamento_p95_ms: 2000, conservazione_p99_ms: 86_400_000 },
        misure: { uptime_pct: { valore: 99.93, obiettivo: 99.5, raggiunto: true, fonte: "sonde del servizio", campioni: 8928 },
                  versamento_p95_ms: { valore: 840, obiettivo: 2000, raggiunto: true, fonte: "metriche del gateway", campioni: 15_204 } },
        conformita: {}, metriche: {}, totale_aip: 1284, totale_tenant: 3, storage_totale_gb: 124.6, per_stato: { ATTIVO: 3 }, per_piano: { PA: 3 }, note: [] });
    }
    if (p === "/dashboard/sla/throughput" && m === "GET") {
      return json({ per_giorno: [{ data: ORA.slice(0, 10), aip: 42 }] });
    }
    if (p === "/dashboard/manuale-conservazione" && m === "GET") {
      return json({ meta: { versione: "2026.10", stato_firma: "non firmato", generato_il: ORA }, conservatore: { denominazione: "Conservatore di prova" },
        sezioni: [
          { id: "s1", numero: "1", titolo: "Scopo e ambito del documento", stato_sezione: "generata", contenuto: "Il manuale descrive il servizio di conservazione." },
          { id: "s2", numero: "4", titolo: "Soggetti e ruoli", stato_sezione: "da_compilare", contenuto: "Responsabile del servizio: DA COMPILARE." },
        ], funzioni: [], riepilogo_sezioni: { generata: 1, da_compilare: 1 } });
    }
    if (p === "/dashboard/contratti" && m === "GET") {
      return json({ items: [
        { id: "ct1", tenant_id: ENTE, ragione_sociale: "Comune di Borgo Immaginario", numero_contratto: "CONS-2024-017", tipo_servizio: "Conservazione a norma", data_inizio: "2024-01-01", data_fine: "2026-12-31", stato: "ATTIVO" },
      ], total: 1 });
    }
    if (p === "/dashboard/nomine-rsc" && m === "GET") {
      return json({ nomina_corrente: { id: "nm1", nome: "Responsabile", cognome: "Di Prova", codice_fiscale: "RSPDPR80A01H501X", data_nomina: "2025-03-01", data_revoca: null, attiva: true }, storico: [] });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/utenti$/.test(p) && m === "GET") {
      return json({ items: [{ id: "u3", email: "ced@example.org", nome: "Referente", cognome: "Di Prova", ruolo: "ADMIN_TENANT", attivo: true, ultimo_accesso: minuti(60) }], total: 1 });
    }
    if (/^\/v1\/onboarding\/tenant\/[^/]+\/api-keys$/.test(p) && m === "GET") {
      return json({ items: [{ id: "k1", nome: "Gestionale del protocollo", prefisso: "vd_live_ab12", scopes: ["versamento:ud", "read"], attiva: true, creata_il: minuti(60 * 24 * 90), ultimo_uso: minuti(30) }], total: 1 });
    }
    if (p === "/dashboard/distribution" && m === "POST") {
      if (!PERMESSI_RUOLO[stato.ruolo].includes("esibizione:richiedi")) return errore(problem(403, "DST-403", "Il tuo profilo consulta l'archivio ma non chiede esibizioni"));
      stato.esibizioniRichieste = [...(stato.esibizioniRichieste ?? []), corpo];
      return json({ pdd_id: "pdd00000-0000-4000-8000-000000000002", stato: "PRONTO", consegna: "LINK", tipo: corpo?.tipo, hash_pdd: "d".repeat(64),
        documenti: [{ nome_file: "determina_412.pdf", signed_url: "https://storage.example/pdd/determina_412.pdf?firma=x", hash_sha256: "e".repeat(64), dimensione_bytes: 81_337 }],
        url_scadenza: minuti(-60 * 24) });
    }
    if (p === "/v1/versamento/ud" && m === "POST") {
      stato.versamenti = [...(stato.versamenti ?? []), req.postData() ?? ""];
      return json({ pdv_id: "pdv00000-0000-4000-8000-000000000077", stato: "RICEVUTO", avvisi: [] }, 202);
    }

    return errore(problem(404, "TEST-404", `Nessuna risposta finta per ${m} ${p}`));
  });
  return stato;
}
