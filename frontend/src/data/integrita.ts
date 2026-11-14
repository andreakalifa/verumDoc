// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Stato d'integrità della catena delle impronte, letto dall'ultima verifica
 * COMPLETA registrata dal servizio (g2-dati §6.5: `integrita.esito`,
 * `verificata_il`, `blocchi_aggiunti_dopo`). Unica regola per la home, la
 * pagina della catena e la pagina dei blocchi: uno stato sconosciuto si mostra
 * come sconosciuto, mai come «integro».
 *
 * Le cinque verità della fascia: in lettura, non disponibile, mai verificata,
 * integra, compromessa. «Nessun blocco» (catena vuota al momento della verifica)
 * è una variante neutra di «mai verificata»: non c'era niente da verificare.
 */

export type TonoFascia = "giallo" | "critico" | "neutro";
export interface Fascia { tono: TonoFascia; titolo: string; riga: string }

const num = (n: unknown) => (typeof n === "number" ? n.toLocaleString("it-IT") : null);

/** «20/10/2026 alle 14:05», oppure null. */
export function quandoVerifica(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" })} alle ${d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}`;
}

export type EsitoIntegrita = "lettura" | "non_disponibile" | "mai_verificata" | "nessun_blocco" | "integra" | "compromessa";

/** Esito in una parola, dalla risposta di GET /dashboard/hashchain. */
export function esitoIntegrita(q: { isLoading: boolean; isError: boolean; data?: any }): EsitoIntegrita {
  if (q.isLoading) return "lettura";
  if (q.isError || !q.data) return "non_disponibile";
  const i = q.data.integrita;
  if (!i || !i.verificata_il || i.esito === "MAI_VERIFICATA") return "mai_verificata";
  if (i.esito === "NESSUN_BLOCCO") return "nessun_blocco";
  if (i.esito === "COMPROMESSA" || i.integra === false) return "compromessa";
  if (i.esito === "INTEGRA" && i.integra === true) return "integra";
  // Combinazione inattesa: non si deduce un esito che il servizio non ha dato
  return "mai_verificata";
}

/** Riga di copertura: data, blocchi verificati e blocchi sigillati dopo la verifica. */
export function rigaCopertura(i: any): string {
  const parti: string[] = [];
  const quando = quandoVerifica(i?.verificata_il);
  if (quando) parti.push(`Ultima verifica completa il ${quando}`);
  if (num(i?.blocchi_verificati)) parti.push(`${num(i.blocchi_verificati)} blocchi verificati`);
  let riga = parti.join(": ");
  const dopo = typeof i?.blocchi_aggiunti_dopo === "number" ? i.blocchi_aggiunti_dopo : 0;
  if (dopo > 0) riga += `. ${dopo === 1 ? "Un blocco sigillato dopo non è coperto" : `${num(dopo)} blocchi sigillati dopo non sono coperti`} da questo esito`;
  return riga ? `${riga}.` : "";
}

export function fasciaIntegrita(q: { isLoading: boolean; isError: boolean; data?: any }): Fascia {
  const e = esitoIntegrita(q);
  const i = q.data?.integrita;
  switch (e) {
    case "lettura":
      return { tono: "neutro", titolo: "Lettura dello stato in corso", riga: "Sto leggendo l'esito dell'ultima verifica completa della catena delle impronte." };
    case "non_disponibile":
      return { tono: "critico", titolo: "Stato dell'archivio non disponibile", riga: "Il servizio che verifica l'integrità non risponde. Lo stato è sconosciuto, non integro per difetto." };
    case "mai_verificata":
      return { tono: "neutro", titolo: "Integrità mai verificata", riga: "Nessuna verifica completa della catena delle impronte è stata ancora eseguita: l'integrità non è dimostrata." };
    case "nessun_blocco":
      return { tono: "neutro", titolo: "Nessun blocco da verificare", riga: `${quandoVerifica(i?.verificata_il) ? `Verifica completa del ${quandoVerifica(i.verificata_il)}: ` : ""}la catena non conteneva ancora blocchi.` };
    case "integra":
      return { tono: "giallo", titolo: "Archivio integro", riga: rigaCopertura(i) || (i?.messaggio ?? "") };
    case "compromessa":
      return { tono: "critico", titolo: "Integrità compromessa", riga: [i?.messaggio || "La verifica ha trovato impronte o collegamenti divergenti.", rigaCopertura(i)].filter(Boolean).join(" ") };
  }
}

/** Stato di firma di un blocco (colonne di be-pipeline, VD-C-019). */
export const FIRMA_BLOCCO: Record<string, { testo: string; classe: string }> = {
  FIRMATO:     { testo: "Firmato e marcato", classe: "vd-stato--ok" },
  NON_FIRMATO: { testo: "Non firmato", classe: "vd-stato--rischio" },
  DA_FIRMARE:  { testo: "In attesa di firma", classe: "vd-stato--info" },
};
export const firmaBlocco = (s?: string | null) =>
  (s ? FIRMA_BLOCCO[s] : undefined) ?? { testo: s ? s.replace(/_/g, " ").toLowerCase() : "non indicato", classe: "vd-stato--neutro" };

/**
 * Qualità della firma dei blocchi: i conteggi per stato (FIRMATO, DA_FIRMARE,
 * NON_FIRMATO) dicono se un blocco è firmato e marcato; `blocchi_senza_firma_qualificata`
 * (dall'ultima verifica completa) dice quanti NON hanno una firma qualificata,
 * compresi quelli non ancora firmati. Le due cifre non si contraddicono: un blocco
 * può essere «firmato e marcato» con una firma non qualificata.
 */
export interface QualitaFirma {
  firmati: number;
  daFirmare: number;
  nonFirmati: number;
  /** Firmati e marcati con firma NON qualificata; null se il servizio non lo dice. */
  firmatiNonQualificati: number | null;
  /** Firmati e marcati con firma qualificata; null se il servizio non lo dice. */
  firmatiQualificati: number | null;
  /** Etichetta della qualità per i firmati: «firma non qualificata», «firma qualificata», … */
  etichetta: string | null;
}

export function qualitaFirma(conteggi: Record<string, number> | undefined, senzaQualificata: number | null): QualitaFirma {
  const firmati = conteggi?.FIRMATO ?? 0, daFirmare = conteggi?.DA_FIRMARE ?? 0, nonFirmati = conteggi?.NON_FIRMATO ?? 0;
  if (senzaQualificata === null) return { firmati, daFirmare, nonFirmati, firmatiNonQualificati: null, firmatiQualificati: null, etichetta: null };
  const nq = Math.min(firmati, Math.max(0, senzaQualificata - daFirmare - nonFirmati));
  const q = firmati - nq;
  const etichetta = firmati === 0 ? null
    : nq === firmati ? "firma non qualificata"
    : nq === 0 ? "firma qualificata"
    : `${num(nq)} con firma non qualificata`;
  return { firmati, daFirmare, nonFirmati, firmatiNonQualificati: nq, firmatiQualificati: q, etichetta };
}
