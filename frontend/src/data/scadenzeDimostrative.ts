// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Scadenze del servizio — DATI DIMOSTRATIVI.
 * Il backend non fornisce ancora il calendario delle scadenze
 * (verifiche periodiche, rinnovo marche, firma del manuale, scarti):
 * la home le mostra etichettate come dimostrative finché il servizio
 * `lifecycle` non esiste (decisione in attesa VD-P-06).
 */
export type StatoScadenza = "in_orario" | "rischio" | "ritardo" | "sospesa";

export interface Scadenza {
  data: string;       // AAAA-MM-GG
  destinazione: string; // pagina dove si agisce, oppure /da-realizzare/<funzione>
  attivita: string;
  dettaglio: string;
  ente: string;
  stato: StatoScadenza;
}

export const SCADENZE_DIMOSTRATIVE: Scadenza[] = [
  { data: "2026-10-22", destinazione: "/hashchain", attivita: "Verifica d'integrità completa", dettaglio: "Programmata alle 06:00", ente: "Tutti gli enti", stato: "in_orario" },
  { data: "2026-11-12", destinazione: "/manuale", attivita: "Firma del manuale di conservazione v3.2", dettaglio: "In attesa della tua firma", ente: "Servizio", stato: "in_orario" },
  { data: "2026-11-30", destinazione: "/hashchain", attivita: "Verifica quinquennale di integrità e leggibilità", dettaglio: "62% completata", ente: "Azienda sanitaria di Valfittizia", stato: "in_orario" },
  { data: "2026-11-30", destinazione: "/da-realizzare/marche", attivita: "Rinnovo marche temporali, 214.803 pacchetti", dettaglio: "Non avviato", ente: "11 enti", stato: "rischio" },
  { data: "2026-12-31", destinazione: "/pratiche?tipo=SCARTO", attivita: "Scarto di 1.204 fatture del 2015", dettaglio: "Attesa nulla osta della Soprintendenza", ente: "Unione dei Comuni di Collefinto", stato: "sospesa" },
];

export const ETICHETTA_SCADENZA: Record<StatoScadenza, { testo: string; classe: string }> = {
  in_orario: { testo: "In orario", classe: "vd-stato--ok" },
  rischio:   { testo: "Rischio ritardo", classe: "vd-stato--rischio" },
  ritardo:   { testo: "In ritardo", classe: "vd-stato--critico" },
  sospesa:   { testo: "Sospeso", classe: "vd-stato--neutro" },
};
