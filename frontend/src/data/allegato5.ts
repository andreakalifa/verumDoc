// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Metadati del documento informatico (Allegato 5 alle Linee guida AgID) come li
 * restituisce il servizio (`metadati_allegato5[].metadati`, g2-dati §6.3), con il
 * nome in italiano chiaro. L'ordine è quello in cui si mostrano.
 * Un campo assente in questa tabella si mostra con la sua chiave.
 * R11 (D07): anche i campi dei profili v2 riscontrati sul testo ufficiale
 * (registrazione, Soggetti, Riservato, Verifica, Tracciature…); i sottocampi delle
 * strutture hanno il nome in `SOTTOCAMPI_ALLEGATO5`.
 */
export const METADATI_ALLEGATO5: { chiave: string; nome: string }[] = [
  { chiave: "identificativo_documento", nome: "Identificativo del documento" },
  { chiave: "oggetto", nome: "Oggetto" },
  { chiave: "tipologia_documentale", nome: "Tipologia documentale" },
  { chiave: "data_documento", nome: "Data del documento" },
  { chiave: "modalita_formazione", nome: "Modalità di formazione" },
  { chiave: "soggetto_produttore_nome", nome: "Nome del produttore" },
  { chiave: "soggetto_produttore_id", nome: "Codice fiscale del produttore" },
  { chiave: "soggetto_destinatario", nome: "Destinatario" },
  { chiave: "numero_registrazione", nome: "Numero di registrazione" },
  { chiave: "data_registrazione", nome: "Data di registrazione" },
  { chiave: "numero_progressivo", nome: "Numero progressivo" },
  { chiave: "classificazione", nome: "Classificazione" },
  { chiave: "fascicolo_id", nome: "Fascicolo" },
  { chiave: "codice_ipa", nome: "Codice IPA" },
  { chiave: "aoo", nome: "Area organizzativa omogenea" },
  { chiave: "codice_ruo", nome: "Unità organizzativa responsabile" },
  { chiave: "formato", nome: "Formato" },
  { chiave: "versione_documento", nome: "Versione del documento" },
  { chiave: "numero_allegati", nome: "Numero di allegati" },
  { chiave: "tipo_firma", nome: "Tipo di firma" },
  { chiave: "tipologia_flusso", nome: "Flusso (entrata, uscita, interno)" },
  { chiave: "tipo_registro", nome: "Tipo di registro" },
  { chiave: "codice_registro", nome: "Codice del registro" },
  { chiave: "segnatura", nome: "Segnatura di protocollo" },
  { chiave: "soggetti", nome: "Soggetti" },
  { chiave: "riservato", nome: "Riservato" },
  { chiave: "firmato_digitalmente", nome: "Firmato digitalmente" },
  { chiave: "sigillato_elettronicamente", nome: "Sigillato elettronicamente" },
  { chiave: "marcatura_temporale", nome: "Marca temporale" },
  { chiave: "conformita_copie_immagine", nome: "Copia per immagine conforme" },
  { chiave: "nome_documento", nome: "Nome del documento" },
  { chiave: "tracciature_modifiche", nome: "Modifiche del documento" },
  { chiave: "parole_chiave", nome: "Parole chiave" },
  { chiave: "tempo_conservazione", nome: "Tempo di conservazione (anni)" },
  { chiave: "lingua", nome: "Lingua" },
  { chiave: "stato_conservazione", nome: "Stato di conservazione" },
  { chiave: "metadati_extra", nome: "Altri metadati" },
];

/** Sottocampi delle strutture (Soggetti, Tracciature, Assegnazione): solo i nomi. */
export const SOTTOCAMPI_ALLEGATO5: Record<string, string> = {
  ruolo: "Ruolo", tipo_soggetto: "Tipo di soggetto", cognome: "Cognome", nome: "Nome",
  denominazione: "Denominazione", codice_fiscale: "Codice fiscale", codice_ipa: "Codice IPA",
  uor: "Unità organizzativa", denominazione_ufficio: "Ufficio",
  indirizzi_digitali: "Indirizzi digitali", denominazione_sistema: "Sistema",
  tipo_modifica: "Modifica", autore_modifica: "Autore della modifica",
  data_ora_modifica: "Data e ora della modifica", id_doc_versione_precedente: "Versione precedente",
  tipo_assegnazione: "Assegnazione", soggetto_assegnatario: "Assegnatario",
  data_inizio: "Dal", data_fine: "Al",
};

export const nomeMetadato = (k: string): string =>
  METADATI_ALLEGATO5.find(m => m.chiave === k)?.nome ?? SOTTOCAMPI_ALLEGATO5[k] ?? k.replace(/_/g, " ");
