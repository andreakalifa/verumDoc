// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R17 (VD-P-164): conferma firmata dal gateway delle decisioni sensibili del ciclo di vita
 * (approvazione ed esecuzione di uno scarto, esecuzione di un riversamento in uscita,
 * richiesta e approvazione di un annullamento, rettifica, ripresa di una pratica sospesa).
 *
 * La console calcola l'impronta del contenuto che la persona sta decidendo (stessa forma
 * canonica di libs/shared/conferme_decisione.py: JSON con chiavi ordinate, nessuno spazio,
 * SHA-256), la fa firmare al gateway (POST /api/v1/auth/conferme-decisione, dopo una
 * conferma dell'identità recente in questa sessione) e la manda con la decisione a
 * ciclo-vita, che la registra; il lavoratore la riverifica prima di eseguire.
 */
import { api } from "../api/client";

export type AzioneConferma =
  | "SCARTO_APPROVA" | "SCARTO_ESEGUI" | "RIVERSAMENTO_USCITA_ESEGUI"
  | "ANNULLAMENTO_RICHIEDI" | "ANNULLAMENTO_APPROVA" | "RETTIFICA_RICHIEDI" | "PRATICA_RIPRENDI";

export interface ConfermaFirmata { payload: string; firma: string }
export interface StatoConferme { attiva: boolean; azioni: string[]; azioni_creazione: string[] }
/** Ciò che la persona conferma: pratica e versione attesa (nulle/0 se la decisione crea la
 *  pratica), ente della pratica, campi del contenuto da cui si calcola l'impronta. */
export interface RichiestaConferma {
  azione: AzioneConferma;
  pratica: string | null;
  versione: number;
  ente: string;
  campi: Record<string, unknown>;
}

export const statoConferme = (): Promise<StatoConferme> =>
  api.get("/v1/ciclo-vita/conferme").then(r => r.data as StatoConferme);

/** JSON canonico: chiavi ordinate a ogni livello, nessuno spazio (come json_canonico). */
export function jsonCanonico(valore: unknown): string {
  if (valore === null || typeof valore !== "object") return JSON.stringify(valore);
  if (Array.isArray(valore)) return `[${valore.map(jsonCanonico).join(",")}]`;
  const o = valore as Record<string, unknown>;
  return `{${Object.keys(o).sort().map(k => `${JSON.stringify(k)}:${jsonCanonico(o[k])}`).join(",")}}`;
}

/** SHA-256 esadecimale di {"azione": …, ...campi} in forma canonica. */
export async function improntaContenuto(azione: AzioneConferma, campi: Record<string, unknown>): Promise<string> {
  const dati = new TextEncoder().encode(jsonCanonico({ azione, ...campi }));
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", dati));
  return Array.from(h, b => b.toString(16).padStart(2, "0")).join("");
}

export const chiediConferma = (r: RichiestaConferma, impronta: string): Promise<ConfermaFirmata> =>
  api.post("/v1/auth/conferme-decisione", {
    azione: r.azione, pratica: r.pratica, versione: r.versione, impronta, ente: r.ente,
  }).then(res => (res.data as { conferma: ConfermaFirmata }).conferma);

/** Azione sensibile di una transizione (null = non chiede conferma). */
export function azioneTransizione(tipo: string, nuovoStato: string): AzioneConferma | null {
  if (tipo === "SCARTO" && nuovoStato === "ESEGUIBILE") return "SCARTO_APPROVA";
  if (tipo === "SCARTO" && nuovoStato === "ESEGUITA") return "SCARTO_ESEGUI";
  if (tipo === "RIVERSAMENTO_USCITA" && nuovoStato === "ESEGUITA") return "RIVERSAMENTO_USCITA_ESEGUI";
  return null;
}

/** La persona ha chiuso il dialogo senza confermare: nessun errore da mostrare. */
export class ConfermaAnnullata extends Error {
  constructor() { super("Decisione non confermata"); this.name = "ConfermaAnnullata"; }
}
