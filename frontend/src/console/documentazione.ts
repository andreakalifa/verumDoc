// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Documentazione del servizio (R15, VD-C-177): manuale di conservazione, piano della
 * sicurezza, guida alle funzionalità e catalogo delle pagine generati dalla configurazione
 * (dashboard-api, services/dashboard-api/app/routers/documentazione_servizio.py).
 * Solo i ruoli fissi del conservatore (permesso mai delegabile «documentazione:genera»).
 */
import { api } from "../api/client";

export type TipoDocumento = "MANUALE_CONSERVAZIONE" | "PIANO_SICUREZZA" | "GUIDA_FUNZIONALITA" | "CATALOGO_ROUTE";

export const NOME_DOCUMENTO: Record<TipoDocumento, string> = {
  MANUALE_CONSERVAZIONE: "Manuale di conservazione",
  PIANO_SICUREZZA: "Piano della sicurezza",
  GUIDA_FUNZIONALITA: "Guida alle funzionalità",
  CATALOGO_ROUTE: "Catalogo delle pagine",
};
export const TIPI_DOCUMENTO = Object.keys(NOME_DOCUMENTO) as TipoDocumento[];

export interface SezioneRef { id: string; numero: string; titolo: string }
export interface Differenze { aggiunte: SezioneRef[]; tolte: SezioneRef[]; cambiate: SezioneRef[] }
export interface CampoDaCompilare { campo: string; descrizione: string; chi: string; dove: string }
export interface SezioneDaCompilare { sezione: string; numero: string; titolo: string; campi: CampoDaCompilare[] }

export interface Versione {
  id: string; tipo: TipoDocumento; versione: number; titolo: string; generata_il: string;
  impronta_contenuto: string; impronta_sha256: string; impronta_html: string;
  differenze: Differenze | null; sezioni_da_compilare: number; campi_da_compilare: number;
  da_verificare: number;
  archivio: { archiviata: boolean; archiviata_il: string | null; chiave_markdown: string | null; chiave_html: string | null };
  firma: { stato: string; da_firmare: { formato: string; impronta_sha256: string } };
  da_compilare?: SezioneDaCompilare[];
  da_verificare_elenco?: { requisito: string; motivo: string }[];
}

export interface Generazione {
  id: string; stato: "IN_CORSO" | "COMPLETATA" | "ERRORE"; richiesta_il: string; scade_il: string;
  conclusa_il: string | null; errore: string | null;
  esito: { nuove?: { tipo: TipoDocumento; versione: number }[]; invariate?: TipoDocumento[]; interrotta?: boolean };
  nuova?: boolean;
}

export interface Quadro {
  documenti: { tipo: TipoDocumento; ultima: Versione | null }[];
  generazioni: Generazione[];
}

const BASE = "/dashboard/documentazione-servizio";
export const leggiQuadro = () => api.get(BASE).then(r => r.data as Quadro);
export const leggiVersioni = (tipo: TipoDocumento) =>
  api.get(`${BASE}/versioni`, { params: { tipo } }).then(r => r.data as { tipo: TipoDocumento; items: Versione[] });
export const richiediGenerazione = () => api.post(`${BASE}/generazioni`).then(r => r.data as Generazione);
export const leggiGenerazione = (id: string) => api.get(`${BASE}/generazioni/${id}`).then(r => r.data as Generazione);

/** Scarica il Markdown o l'HTML stampabile di una versione con il nome del servizio. */
export async function scaricaVersione(v: Versione, formato: "markdown" | "html") {
  const r = await api.get(`${BASE}/versioni/${v.id}/${formato}`, { responseType: "blob" });
  const nome = /filename="([^"]+)"/.exec(String(r.headers["content-disposition"] ?? ""))?.[1]
    ?? `${v.tipo.toLowerCase()}_v${v.versione}.${formato === "markdown" ? "md" : "html"}`;
  const url = URL.createObjectURL(r.data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nome;
  a.click();
  URL.revokeObjectURL(url);
}
