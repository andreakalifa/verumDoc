// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Firme del responsabile (R16, VD-C-166): documenti da firmare con la firma PERSONALE e
 * l'OTP del prestatore (dashboard-api /api/dashboard/firme-responsabile). OTP e PIN passano
 * solo per la richiesta di firma: il browser non li conserva e il servizio non li registra.
 */
import { api } from "../api/client";

export type StatoFirma = "DA_FIRMARE" | "FIRMATO" | "ARCHIVIATO" | "RESPINTO" | "ANNULLATO";

export interface DocumentoDaFirmare {
  id: string; tipo: string; tipo_descrizione: string; titolo: string; nome: string;
  impronta_sha256: string; stato: StatoFirma; dimensione: number;
  firmatario_atteso: string | null; firmato_da: string | null; firmato_il: string | null;
  certificato_soggetto: string | null; livello: string | null; archiviata: boolean;
  motivo: string | null; richiesto_il: string;
}
export interface ElencoFirme { items: DocumentoDaFirmare[]; tipi: Record<string, string> }
export interface EsitoFirma { id: string; stato: string; livello: string | null; firmatario: string | null; data_marca: string | null; avvisi: string[] }

const BASE = "/dashboard/firme-responsabile";
export const leggiFirme = () => api.get(BASE).then(r => r.data as ElencoFirme);
/** Revisione R17 (R17-04): mette in firma la versione STAMPABILE (HTML) di un documento
 *  generato in «Documentazione del servizio» (manuale, piano della sicurezza). */
export const inviaAllaFirma = (versioneId: string) =>
  api.post(`${BASE}/documentazione/${encodeURIComponent(versioneId)}`).then(r => r.data as DocumentoDaFirmare & { nuovo: boolean });
/** Testo del documento da firmare (HTML stampabile) per mostrarlo nella pagina di firma. */
export const leggiTestoDocumento = (id: string) =>
  api.get(`${BASE}/${id}/contenuto`, { responseType: "text", transformResponse: x => x }).then(r => String(r.data));
export const chiediOtp = (id: string) =>
  api.post(`${BASE}/${id}/otp`).then(r => r.data as { inviato: boolean; fornitore: string | null });
export const firmaConOtp = (id: string, otp: string, pin: string) =>
  api.post(`${BASE}/${id}/firma`, { otp, pin }).then(r => r.data as EsitoFirma);
export const annullaRichiesta = (id: string, motivo: string) =>
  api.post(`${BASE}/${id}/annulla`, { motivo }).then(r => r.data as { id: string; stato: string });
export const scaricaContenuto = (id: string) =>
  api.get(`${BASE}/${id}/contenuto`, { responseType: "blob" }).then(r => r.data as Blob);
export const scaricaBusta = (id: string) =>
  api.get(`${BASE}/${id}/busta`, { responseType: "blob" }).then(r => r.data as Blob);

export const STATO_FIRMA: Record<StatoFirma, { testo: string; classe: string; spiegazione: string }> = {
  DA_FIRMARE: { testo: "Da firmare", classe: "vd-stato--rischio", spiegazione: "In attesa della firma personale." },
  FIRMATO: { testo: "Firmato, da archiviare", classe: "vd-stato--info", spiegazione: "La firma è registrata; il registro la riverifica e la archivia nel WORM." },
  ARCHIVIATO: { testo: "Firmato e archiviato", classe: "vd-stato--ok", spiegazione: "La busta firmata è nel WORM, con l'impronta nella chiave." },
  RESPINTO: { testo: "Respinto dalla verifica", classe: "vd-stato--critico", spiegazione: "La riverifica prima dell'archiviazione non è tornata: il documento va firmato di nuovo." },
  ANNULLATO: { testo: "Annullato", classe: "vd-stato--neutro", spiegazione: "La richiesta è stata annullata prima della firma." },
};

/** Salva un Blob con un nome di file (scaricamento dal browser). */
export function salvaFile(dati: Blob, nome: string) {
  const url = URL.createObjectURL(dati);
  const a = document.createElement("a");
  a.href = url;
  a.download = nome;
  a.click();
  URL.revokeObjectURL(url);
}
