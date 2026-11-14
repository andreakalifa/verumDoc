// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Modalità di formazione del documento informatico (metadato ModalitaDiFormazione,
 * Allegato 5 alle Linee guida AgID; §2.1.1, lettere a–d). Il valore trasmesso e
 * conservato è la lettera: stessa enumerazione di libs/shared/models
 * (ModalitaFormazione), unica nel sistema. Qui solo le etichette in italiano chiaro.
 */
export const MODALITA_FORMAZIONE = [
  { value: "a", label: "a) Creato con un programma", descrizione: "creato con strumenti software nei formati dell'Allegato 2" },
  { value: "b", label: "b) Acquisito, o copia di un cartaceo", descrizione: "acquisito per via telematica o su supporto, o copia per immagine o informatica di un documento analogico" },
  { value: "c", label: "c) Da transazioni o moduli online", descrizione: "memorizzazione di transazioni, processi informatici o moduli compilati online" },
  { value: "d", label: "d) Generato da banche dati", descrizione: "generato o raggruppato da banche dati secondo una struttura predeterminata" },
] as const;

/** Etichetta per un valore conservato; i valori non riconosciuti si mostrano come arrivano. */
export function nomeModalita(v: unknown): string | null {
  if (typeof v !== "string" || !v) return null;
  const m = MODALITA_FORMAZIONE.find(x => x.value === v.toLowerCase());
  return m ? m.label : v.toLowerCase().replace(/_/g, " ");
}
