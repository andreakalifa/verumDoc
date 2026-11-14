// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Canali PEC (R13b, VD-C-078): console di notification (/api/v1/notifiche/pec).
 * Registro, prova e ricevute non associate solo per il conservatore; messaggi e
 * ricevute associate anche per l'amministratore dell'ente (del proprio ente).
 * I segreti non passano mai di qui: restano nel Secret del lavoratore della posta
 * (PEC_SEGRETI_JSON), legati agli host del gestore.
 */
import { api } from "../api/client";

export interface StatoCanale {
  segreti_presenti: Record<string, boolean>; problemi: string[]; ultimo_invio_il: string | null;
  ultima_lettura_il: string | null; ultimo_errore: string | null; aggiornato_il: string | null;
}
export interface CanalePec {
  codice: string; tipo: "SMTP_IMAP" | "SVILUPPO"; nome: string; attivo: boolean; priorita: number;
  configurazione: Record<string, unknown>; versione: number; aggiornato_il: string | null;
  stato: StatoCanale | null;
}
export interface RegistroPec {
  items: CanalePec[]; tipi: string[]; segreti: Record<string, { nome: string; obbligatorio: boolean }[]>;
  variabile_segreti: string; sviluppo_ammesso: boolean; messaggi_30_giorni: Record<string, number>;
}
export interface MessaggioPec {
  id: string; ente: string | null; caso_uso: string; destinatario: string; oggetto: string; stato: string;
  canale: string | null; message_id: string | null; tentativi: number; errore: string | null;
  creato_il: string; inviato_il: string | null; accettato_il: string | null; consegnato_il: string | null;
  esito_il: string | null; avviso: string | null;
}
export interface RicevutaPec {
  id: number; canale: string; esito: string; motivo: string | null; messaggio_id: string | null; tipo: string | null;
  msgid: string | null; identificativo: string | null; gestore: string | null; consegna: string | null;
  errore: string | null; errore_esteso: string | null; data_ricevuta: string | null;
  certificato_sha256: string | null; certificato_soggetto: string | null; impronta_sha256: string | null;
  dimensione: number; ricevuta_il: string;
}
export interface DettaglioMessaggio extends MessaggioPec { testo: string; ricevute: RicevutaPec[] }

const BASE = "/v1/notifiche/pec";
const q = (p: Record<string, string | number | undefined>) => {
  const s = Object.entries(p).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
  return s ? `?${s}` : "";
};
export const leggiCanaliPec = () => api.get(`${BASE}/canali`).then(r => r.data as RegistroPec);
export const salvaCanalePec = (codice: string, corpo: { tipo: string; nome: string; attivo: boolean; priorita: number; configurazione: Record<string, unknown>; versione: number }) =>
  api.put(`${BASE}/canali/${encodeURIComponent(codice)}`, corpo).then(r => r.data as CanalePec);
export const provaCanalePec = (codice: string, destinatario: string) =>
  api.post(`${BASE}/canali/${encodeURIComponent(codice)}/prova`, { destinatario }).then(r => r.data as { messaggio_id: string; stato: string; canale_attivo: boolean; nota: string });
export const leggiMessaggiPec = (filtri: { stato?: string; caso_uso?: string; dopo?: string; limite?: number }) =>
  api.get(`${BASE}/messaggi${q(filtri)}`).then(r => r.data as { items: MessaggioPec[]; cursore: string | null });
export const leggiMessaggioPec = (id: string) => api.get(`${BASE}/messaggi/${encodeURIComponent(id)}`).then(r => r.data as DettaglioMessaggio);
export const leggiRicevuteNonAssociate = (dopo?: number) =>
  api.get(`${BASE}/ricevute${q({ dopo })}`).then(r => r.data as { items: RicevutaPec[]; cursore: number | null });
export const scaricaEml = (id: number) =>
  api.get(`${BASE}/ricevute/${id}/eml`, { responseType: "blob" }).then(r => r.data as Blob);

/** Campi della configurazione per tipo (mai segreti). */
export interface CampoPec { chiave: string; etichetta: string; tipo?: "numero" | "scelta" | "pem"; scelte?: string[]; aiuto?: string }
export const CAMPI_PEC: CampoPec[] = [
  { chiave: "indirizzo", etichetta: "Casella PEC del conservatore", aiuto: "Mittente di ogni messaggio e casella in cui arrivano le ricevute." },
  { chiave: "smtp_host", etichetta: "Server di invio (SMTP)", aiuto: "Solo il nome dell'host, es. smtps.pec.gestore.it." },
  { chiave: "smtp_porta", etichetta: "Porta SMTP", tipo: "numero" },
  { chiave: "smtp_sicurezza", etichetta: "Cifratura SMTP", tipo: "scelta", scelte: ["tls", "starttls", "nessuna"] },
  { chiave: "imap_host", etichetta: "Server delle ricevute (IMAP)" },
  { chiave: "imap_porta", etichetta: "Porta IMAP", tipo: "numero" },
  { chiave: "imap_sicurezza", etichetta: "Cifratura IMAP", tipo: "scelta", scelte: ["tls", "nessuna"] },
  { chiave: "cartella", etichetta: "Cartella IMAP" },
  { chiave: "tipo_ricevuta", etichetta: "Ricevuta di consegna", tipo: "scelta", scelte: ["completa", "breve", "sintetica"] },
  { chiave: "radici_pem", etichetta: "Certificati dei gestori PEC (PEM)", tipo: "pem", aiuto: "Dall'indice pubblico dei gestori: solo le ricevute firmate da questi certificati cambiano lo stato dei messaggi." },
];

/** Parole di stato dei messaggi (sistema «Partenze»: parola, non icona). */
export const STATO_PEC: Record<string, { testo: string; classe: string }> = {
  IN_CODA: { testo: "In coda", classe: "vd-stato--neutro" },
  IN_INVIO: { testo: "In invio", classe: "vd-stato--info" },
  INVIATO: { testo: "Inviato al gestore", classe: "vd-stato--info" },
  ACCETTATO: { testo: "Accettato", classe: "vd-stato--info" },
  CONSEGNATO: { testo: "Consegnato", classe: "vd-stato--ok" },
  NON_ACCETTATO: { testo: "Non accettato", classe: "vd-stato--critico" },
  MANCATA_CONSEGNA: { testo: "Mancata consegna", classe: "vd-stato--critico" },
  NON_PARTITO: { testo: "Non partito", classe: "vd-stato--critico" },
};
export const CASO_PEC: Record<string, string> = {
  SCARTO: "Scarto", RESTITUZIONE: "Restituzione", RIVERSAMENTO: "Riversamento", SCADENZE: "Scadenze", PROVA: "Prova",
};
export const TIPO_RICEVUTA: Record<string, string> = {
  ACCETTAZIONE: "Accettazione", NON_ACCETTAZIONE: "Non accettazione", PRESA_IN_CARICO: "Presa in carico",
  AVVENUTA_CONSEGNA: "Avvenuta consegna", ERRORE_CONSEGNA: "Mancata consegna",
  PREAVVISO_ERRORE_CONSEGNA: "Preavviso di mancata consegna", RILEVAZIONE_VIRUS: "Rilevazione di virus",
  POSTA_CERTIFICATA: "Messaggio PEC in arrivo",
};
export const ESITO_RICEVUTA: Record<string, { testo: string; classe: string }> = {
  ASSOCIATA: { testo: "Associata", classe: "vd-stato--ok" },
  NON_AUTENTICA: { testo: "Non autentica", classe: "vd-stato--critico" },
  NON_ASSOCIATA: { testo: "Non associata", classe: "vd-stato--rischio" },
  OLTRE_LIMITE: { testo: "Oltre il limite", classe: "vd-stato--rischio" },
};
