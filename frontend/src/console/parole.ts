// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Parole del mestiere per le console R05: livelli, origini, stati, tipi di pratica.
 * Una sola tabella per ogni vocabolario (come `auth/ruoli.ts`): le pagine non
 * scrivono a mano i nomi degli stati. Un codice sconosciuto si mostra com'è,
 * in stato neutro: mai indovinato.
 */
export interface Parola { testo: string; classe: string }
const neutro = (codice: string): Parola => ({
  testo: codice ? codice.charAt(0) + codice.slice(1).toLowerCase().replace(/_/g, " ") : "Non indicato",
  classe: "vd-stato--neutro",
});
export const parola = (tabella: Record<string, Parola>, codice: string | null | undefined): Parola =>
  (codice && tabella[codice]) || neutro(codice ?? "");

/** Livelli della catena di risoluzione (REGOLE §8.2), dal più generale. */
export const LIVELLO: Record<string, string> = {
  PREDEFINITO: "predefinito normativo",
  CONSERVATORE: "conservatore",
  ENTE: "ente",
  CLASSE: "classe documentale",
  ACCORDO: "accordo di versamento",
  PDV: "singolo versamento",
};
export const nomeLivello = (l?: string | null) => (l && LIVELLO[l]) || (l ?? "");

export const EFFETTO: Record<string, Parola> = {
  BLOCCA: { testo: "Blocca", classe: "vd-stato--critico" },
  AVVISA: { testo: "Avvisa", classe: "vd-stato--rischio" },
  IGNORA: { testo: "Ignora", classe: "vd-stato--neutro" },
};

export const STATO_BOZZA: Record<string, Parola> = {
  APERTA: { testo: "Aperta", classe: "vd-stato--info" },
  PROGRAMMATA: { testo: "Programmata", classe: "vd-stato--rischio" },
  APPLICATA: { testo: "Applicata", classe: "vd-stato--ok" },
  ANNULLATA: { testo: "Annullata", classe: "vd-stato--neutro" },
};
export const RISORSA: Record<string, string> = {
  impostazioni: "Impostazioni", regole: "Severità delle regole", profilo: "Profilo di metadati",
  classe: "Classe documentale", accordo: "Accordo di versamento", quote: "Quote",
};

export const STATO_PROPOSTA: Record<string, Parola> = {
  PROPOSTA: { testo: "Da decidere", classe: "vd-stato--rischio" },
  ACCETTATA: { testo: "Accettata", classe: "vd-stato--ok" },
  RIFIUTATA: { testo: "Rifiutata", classe: "vd-stato--neutro" },
};

export const TIPO_PRATICA: Record<string, string> = {
  RETTIFICA: "Rettifica dei metadati",
  ANNULLAMENTO: "Annullamento di un versamento",
  SCARTO: "Scarto",
  RIVERSAMENTO_USCITA: "Riversamento in uscita",
  RIVERSAMENTO_INGRESSO: "Riversamento in ingresso",
  RESTITUZIONE: "Restituzione di fine contratto",
};

/** Stati delle pratiche (services/ciclo-vita/app/domain/stati.py). */
export const STATO_PRATICA: Record<string, Parola> = {
  BOZZA: { testo: "Bozza", classe: "vd-stato--neutro" },
  PROPOSTA: { testo: "Proposta", classe: "vd-stato--info" },
  AUTORIZZATA_PRODUTTORE: { testo: "Autorizzata dal produttore", classe: "vd-stato--info" },
  NULLA_OSTA: { testo: "Nulla osta ricevuto", classe: "vd-stato--info" },
  ESEGUIBILE: { testo: "Eseguibile", classe: "vd-stato--rischio" },
  ESEGUITA: { testo: "Eseguita", classe: "vd-stato--ok" },
  // Revisione 2 di R05 (S3): cancellazione di qualche PdA non completata (legal hold, errore)
  ESEGUITA_CON_ECCEZIONI: { testo: "Eseguita con eccezioni", classe: "vd-stato--critico" },
  // R09 (D20): chiusa dal conservatore con motivo; i pacchetti rimasti restano verificati
  CHIUSA_CON_ECCEZIONI: { testo: "Chiusa con eccezioni", classe: "vd-stato--rischio" },
  // R09 (D23): annullamento approvato da una seconda persona
  APPROVATA: { testo: "Approvata", classe: "vd-stato--info" },
  RESPINTA: { testo: "Respinta", classe: "vd-stato--critico" },
  RICHIESTA: { testo: "Richiesta", classe: "vd-stato--info" },
  IN_VERSAMENTO: { testo: "In versamento", classe: "vd-stato--info" },
  COMPLETATA: { testo: "Completata", classe: "vd-stato--ok" },
};

/** Il verbo dell'azione che porta a uno stato (bottone della transizione). */
export const AZIONE_VERSO: Record<string, string> = {
  PROPOSTA: "Proponi",
  AUTORIZZATA_PRODUTTORE: "Autorizza come produttore",
  NULLA_OSTA: "Registra il nulla osta",
  ESEGUIBILE: "Rendi eseguibile",
  ESEGUITA: "Esegui",
  RESPINTA: "Respingi",
  // R09 (D20)
  CHIUSA_CON_ECCEZIONI: "Chiudi con eccezioni",
};

/** Iter completo per tipo (corsa della pratica), senza lo stato di rifiuto. */
export const ITER: Record<string, string[]> = {
  SCARTO: ["BOZZA", "PROPOSTA", "AUTORIZZATA_PRODUTTORE", "NULLA_OSTA", "ESEGUIBILE", "ESEGUITA"],
  RESTITUZIONE: ["BOZZA", "PROPOSTA", "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE", "ESEGUITA"],
  RIVERSAMENTO_USCITA: ["PROPOSTA", "ESEGUIBILE", "ESEGUITA"],
  RIVERSAMENTO_INGRESSO: ["PROPOSTA", "ESEGUIBILE", "ESEGUITA"],
  RETTIFICA: ["RICHIESTA", "IN_VERSAMENTO", "COMPLETATA"],
  ANNULLAMENTO: ["RICHIESTA", "APPROVATA", "ESEGUITA"],
};

export const ALLEGATO: Record<string, string> = {
  AUTORIZZAZIONE_PRODUTTORE: "Autorizzazione del produttore (firmata)",
  NULLA_OSTA: "Nulla osta della Soprintendenza",
  VERBALE_SCARTO: "Verbale di scarto",
  ATTO: "Atto della pratica",
  MANIFESTO: "Manifesto del riversamento",
  PDD_RICEVUTO: "Pacchetto ricevuto",
  EXPORT_REGISTRI: "Esportazione dei registri",
  ALTRO: "Altro documento",
};

/** Motivi di blocco dello scarto (services/ciclo-vita/app/domain/scarto.py). */
export const MOTIVO_BLOCCO: Record<string, string> = {
  STATO_NON_CONSERVATO: "Il pacchetto non è nello stato conservato",
  CONSERVAZIONE_PERMANENTE: "La classe prevede la conservazione permanente",
  RETENTION_NON_SCADUTA: "Il termine di conservazione non è ancora scaduto",
  FASCICOLO_APERTO: "Il pacchetto appartiene a un fascicolo ancora aperto",
  TERMINE_FASCICOLO_NON_SCADUTO: "Il termine calcolato dalla chiusura del fascicolo non è scaduto",
  IN_ALTRA_PRATICA: "Il pacchetto è già in un'altra pratica di scarto",
  ESIBIZIONE_IN_CORSO: "Un'esibizione del pacchetto è in preparazione",
  LEGAL_HOLD: "Blocco legale attivo nello storage",
  RETENTION_ASSENTE: "Retention assente nello storage: anomalia da verificare",
  OGGETTI_ASSENTI: "Nessun oggetto del pacchetto nello storage: anomalia da verificare",
  OGGETTI_MANCANTI: "Mancano oggetti del pacchetto nello storage: anomalia da verificare",
  ERRORE_PERSISTENTE: "Lo storage ha rifiutato più volte la cancellazione: da riprendere",
  VERSIONI_RIMASTE: "Restano versioni del pacchetto nello storage: da verificare e riprendere",
  FUORI_PIANO: "Il pacchetto non era selezionato nel piano approvato: mai cancellato, anomalia da verificare",
};

export const ESITO_OGGETTO: Record<string, Parola> = {
  SELEZIONATO: { testo: "Selezionato", classe: "vd-stato--info" },
  BLOCCATO: { testo: "Bloccato", classe: "vd-stato--critico" },
  ESEGUITO: { testo: "Eseguito", classe: "vd-stato--ok" },
  SCARTATO: { testo: "Scartato", classe: "vd-stato--ok" },
  IN_ATTESA: { testo: "In attesa", classe: "vd-stato--info" },
  ERRORE: { testo: "Errore", classe: "vd-stato--critico" },
  IN_CORSO: { testo: "In cancellazione", classe: "vd-stato--info" },
  ELIMINATO: { testo: "Eliminato", classe: "vd-stato--ok" },
  // Revisione 2 di R05 (S3): cancellazione iniziata e non completata
  NON_COMPLETATO: { testo: "Cancellazione incompleta", classe: "vd-stato--critico" },
};

/** Testimone del registro (docs/architettura/catena.md, K3). */
export const STATO_TESTIMONE: Record<string, Parola> = {
  CONTROFIRMATO: { testo: "Controfirmato", classe: "vd-stato--ok" },
  IN_ATTESA: { testo: "In attesa di controfirma", classe: "vd-stato--info" },
  RIFIUTATO: { testo: "Rifiutato dal testimone", classe: "vd-stato--critico" },
  SUPERATO: { testo: "Superato da un checkpoint successivo", classe: "vd-stato--neutro" },
  NON_CONFIGURATO: { testo: "Testimone non configurato", classe: "vd-stato--rischio" },
};
export const STATO_SIGILLO: Record<string, Parola> = {
  FIRMATO: { testo: "Sigillato", classe: "vd-stato--ok" },
  NON_FIRMATO: { testo: "Senza sigillo qualificato", classe: "vd-stato--rischio" },
  DA_FIRMARE: { testo: "Sigillo in corso", classe: "vd-stato--info" },
  IN_ATTESA: { testo: "Sigillo in corso", classe: "vd-stato--info" },
};
export const ESITO_VERIFICA: Record<string, Parola> = {
  OK: { testo: "Integro", classe: "vd-stato--ok" },
  DIVERSO: { testo: "Impronta diversa", classe: "vd-stato--critico" },
  MANCANTE: { testo: "Oggetto mancante", classe: "vd-stato--critico" },
  RETENTION_ASSENTE: { testo: "Retention assente", classe: "vd-stato--critico" },
  ERRORE_LETTURA: { testo: "Errore di lettura", classe: "vd-stato--rischio" },
};

export const STATO_CONSEGNA: Record<string, Parola> = {
  IN_CODA: { testo: "In coda", classe: "vd-stato--info" },
  IN_CONSEGNA: { testo: "In consegna", classe: "vd-stato--info" },
  CONSEGNATA: { testo: "Consegnata", classe: "vd-stato--ok" },
  FALLITA: { testo: "Fallita", classe: "vd-stato--critico" },
  ANNULLATA: { testo: "Annullata", classe: "vd-stato--neutro" },
};

/** Etichette brevi dei tipi di evento. L'ELENCO dei tipi viene dal servizio
 *  (`GET /api/v1/webhook/tipi`, notification): un tipo senza etichetta qui si mostra
 *  con la descrizione del servizio. */
export const ETICHETTA_EVENTO: Record<string, string> = {
  "versamento.ricevuto": "Versamento preso in carico",
  "versamento.validato": "Versamento verificato senza errori bloccanti",
  "versamento.rifiutato": "Versamento rifiutato",
  "versamento.conservato": "Pacchetto conservato e coperto da un checkpoint",
  "versamento.staging_scaduto": "Copie di transito di un versamento rifiutato in scadenza",
  "caricamento.completato": "Caricamento verificato",
  "caricamento.errore": "Caricamento non riuscito",
  "pdd.pronto": "Pacchetto di distribuzione pronto",
  "catena.checkpoint": "Nuovo checkpoint del registro delle impronte",
  "integrita.anomalia": "Anomalia trovata dalla verifica d'integrità",
  "pratica.cambio_stato": "Una pratica ha cambiato stato",
  "versamento.annullato": "Versamento annullato",
  "versamento.rettificato": "Versamento rettificato",
  "scarto.eseguito": "Scarto eseguito",
  "riversamento.pronto": "Riversamento o restituzione pronti",
};

/**
 * Revisione R17 (R17-14): valori delle scelte (enum) in parole. Il codice resta solo come
 * dettaglio (mono) dove DESIGN.md lo prevede; un codice che manca qui si mostra «a frase»
 * («Con revoche»), mai in maiuscolo con i trattini bassi.
 */
export const SCELTA: Record<string, string> = {
  AMMESSO: "Ammesso", AVVISO: "Ammesso con avviso", DA_CONVERTIRE: "Da convertire", RIFIUTATO: "Rifiutato",
  CON_REVOCHE: "Con controllo delle revoche", SENZA_REVOCHE: "Senza controllo delle revoche",
  NON_FIRMATO: "Non firmato", FIRMA_NON_VALIDA: "Firma non valida", FIRMA_VALIDA: "Firma valida",
  SPID_CIE: "SPID o CIE", SPID: "SPID", CIE: "CIE", PASSWORD_TOTP: "Password e codice monouso (TOTP)",
  CREDENZIALI_LOCALI: "Credenziali locali",
  PORTALE: "Portale", WEB: "Portale", API: "API", SFTP: "SFTP", PEC: "PEC",
  RICHIESTA: "Richiesta", NON_RICHIESTA: "Non richiesta",
  ATTIVO: "Attivo", ATTIVA: "Attiva", DISATTIVO: "Disattivo", DISATTIVA: "Disattivata", SPENTO: "Spento",
  BLOCCA: "Blocca", AVVISA: "Avvisa", IGNORA: "Ignora",
  PUBBLICO: "Ente pubblico", PRIVATO: "Soggetto privato",
};
const MAIUSCOLO = /^[A-Z][A-Z0-9_]*$/;
/** Valore di una scelta in parole: dal vocabolario, altrimenti «a frase». */
export const nomeScelta = (v: string | null | undefined): string => {
  if (v === null || v === undefined || v === "") return "nessuna";
  if (SCELTA[v]) return SCELTA[v];
  return MAIUSCOLO.test(v) ? v.charAt(0) + v.slice(1).toLowerCase().replace(/_/g, " ") : v;
};
