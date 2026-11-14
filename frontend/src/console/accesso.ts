// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * API delle console R10 (accesso, ruoli e notifiche): chiamate e tipi allineati ai servizi.
 *   - fornitori d'identità, metodi dell'ente, inviti, ruoli: services/onboarding/app/routers/
 *     accesso.py, inviti.py, ruoli.py
 *   - campanella e preferenze: services/notification/app/api/campanella.py
 *   - accesso locale e attivazione: services/gateway/app/auth/accesso_locale.py
 *   - indirizzi SFTP: services/onboarding/app/routers/sftp.py (ip-ammessi)
 * Le chiamate pubbliche dell'accesso (prima della sessione) usano fetch con il cookie
 * del browser (passaggi legati al browser, come lo scambio SPID/CIE).
 */
import { api } from "../api/client";

const t = (tid: string) => `/v1/onboarding/tenant/${encodeURIComponent(tid)}`;

// ── Fornitori d'identità (conservatore) ──
export interface Fornitore {
  tipo: "SPID_CIE" | "PASSKEY" | "PASSWORD_TOTP"; nome: string; registrato: boolean; attivo: boolean;
  configurazione: Record<string, unknown>; versione: number; aggiornato_il: string | null;
}
export const leggiFornitori = () => api.get("/v1/onboarding/accesso/fornitori").then(r => r.data as { items: Fornitore[] });
export const salvaFornitore = (tipo: string, corpo: { nome: string; attivo: boolean; configurazione: Record<string, unknown>; versione: number }) =>
  api.put(`/v1/onboarding/accesso/fornitori/${tipo}`, corpo).then(r => r.data as Fornitore);

// ── Metodi d'accesso dell'ente e inviti ──
export interface AccessoEnte {
  metodi_con_fornitore_attivo: string[]; metodi_del_conservatore: string[]; metodi_ammessi: string[];
  invito_validita_ore: number; password_lunghezza_minima: number; nomi: Record<string, string>;
}
export const leggiAccessoEnte = (tid: string) => api.get(`${t(tid)}/accesso`).then(r => r.data as AccessoEnte);
export interface Invito {
  id: string; user_id: string; email: string; nome: string; cognome: string;
  stato: "IN_ATTESA" | "USATO" | "REVOCATO" | "SCADUTO"; scade_il: string; usato_il: string | null;
  metodo_attivato: string | null; creato_il: string;
}
export const leggiInviti = (tid: string) => api.get(`${t(tid)}/inviti`).then(r => r.data as { items: Invito[] });
export const creaInvito = (tid: string, corpo: { email: string; nome: string; cognome: string; ruolo: string; ruolo_personalizzato_id?: string | null; codice_fiscale?: string | null }) =>
  api.post(`${t(tid)}/inviti`, corpo).then(r => r.data);
export const reinviaInvito = (tid: string, id: string) => api.post(`${t(tid)}/inviti/${encodeURIComponent(id)}/reinvia`, {}).then(r => r.data);
export const revocaInvito = (tid: string, id: string) => api.delete(`${t(tid)}/inviti/${encodeURIComponent(id)}`).then(r => r.data);

// ── Ruoli personalizzati ──
export interface Permesso { codice: string; descrizione: string; mai_delegabile: boolean; concedibile: boolean; sempre_incluso: boolean }
export interface RuoloPersonalizzato { id: string; nome: string; descrizione: string; ruolo_base: string; permessi: string[]; attivo: boolean; versione: number; utenti: number; aggiornato_il: string | null }
export interface CatalogoRuoli { permessi: Permesso[]; modelli: Record<string, string[]>; ruoli_base: string[]; concedibili: string[]; puo_gestire: boolean; items: RuoloPersonalizzato[] }
export const leggiRuoli = (tid: string) => api.get(`${t(tid)}/ruoli`).then(r => r.data as CatalogoRuoli);
export const creaRuolo = (tid: string, corpo: { nome: string; descrizione: string; ruolo_base: string; permessi: string[] }) =>
  api.post(`${t(tid)}/ruoli`, corpo).then(r => r.data as RuoloPersonalizzato);
export const modificaRuolo = (tid: string, id: string, corpo: { versione: number; nome?: string; descrizione?: string; permessi?: string[] }) =>
  api.put(`${t(tid)}/ruoli/${encodeURIComponent(id)}`, corpo).then(r => r.data as RuoloPersonalizzato);
export const disattivaRuolo = (tid: string, id: string) => api.post(`${t(tid)}/ruoli/${encodeURIComponent(id)}/disattiva`, {}).then(r => r.data);

// ── Campanella e preferenze ──
export interface VoceCampanella { id: number; categoria: string; categoria_nome: string; titolo: string; collegamento: string; quando: string; letto: boolean; da_fare: boolean }
export interface Campanella { da_fare: VoceCampanella[]; avvisi: VoceCampanella[]; non_letti: number; intervallo_aggiornamento_secondi: number;
  /** R13b (VD-C-163): voci del proprio account, separate da quelle dell'ente */
  personali?: VoceCampanella[] }
export const leggiCampanella = () => api.get("/v1/notifiche/campanella", { headers: { "X-Sessione-Rinnovo": "no" } }).then(r => r.data as Campanella);
export const segnaLetti = (corpo: { ids?: number[]; personali?: number[]; tutti?: boolean }) => api.post("/v1/notifiche/campanella/letti", corpo).then(r => r.data);
export interface CategoriaPreferenza { codice: string; nome: string; console: boolean; email: boolean; console_obbligatoria: boolean; email_obbligatoria: boolean; pertinente: boolean }
export const leggiPreferenzeNotifiche = () => api.get("/v1/notifiche/preferenze").then(r => r.data as { versione: number; categorie: CategoriaPreferenza[] });
export const salvaPreferenzeNotifiche = (versione: number, scelte: Record<string, { console: boolean; email: boolean }>) =>
  api.put("/v1/notifiche/preferenze", { versione, scelte }).then(r => r.data as { versione: number; categorie: CategoriaPreferenza[] });

// ── Indirizzi SFTP ──
export const salvaIpAmmessi = (tid: string, id: string, ip_ammessi: string[]) =>
  api.put(`${t(tid)}/sftp/account/${encodeURIComponent(id)}/ip-ammessi`, { ip_ammessi }).then(r => r.data);

// ── Accesso pubblico (prima della sessione) ──
export class ErroreRichiesta extends Error {
  constructor(public codice: string, public dettaglio: string, public stato: number) { super(codice); }
}
export async function chiamaPubblica<T>(percorso: string, corpo: unknown = {}, metodo = "POST"): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`/api${percorso}`, {
      method: metodo, credentials: "same-origin",
      headers: metodo === "GET" ? {} : { "Content-Type": "application/json" },
      body: metodo === "GET" ? undefined : JSON.stringify(corpo),
    });
  } catch {
    throw new ErroreRichiesta("RETE", "Il servizio non risponde: controlla la connessione e riprova.", 0);
  }
  const dati: any = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new ErroreRichiesta(String(dati?.codice ?? `HTTP-${resp.status}`), String(dati?.detail ?? dati?.title ?? ""), resp.status);
  }
  return dati as T;
}

export const NOMI_METODI: Record<string, string> = {
  SPID_CIE: "SPID o CIE", PASSKEY: "Passkey", PASSWORD_TOTP: "Password e codice a tempo",
};

/** Conversioni WebAuthn: le opzioni arrivano in JSON (base64url), il browser vuole ArrayBuffer. */
const daB64 = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), c => c.charCodeAt(0));
const aB64 = (b: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export async function creaPasskey(opzioni: any): Promise<unknown> {
  const pk = { ...opzioni, challenge: daB64(opzioni.challenge), user: { ...opzioni.user, id: daB64(opzioni.user.id) },
    excludeCredentials: (opzioni.excludeCredentials ?? []).map((c: any) => ({ ...c, id: daB64(c.id) })) };
  const cred = await navigator.credentials.create({ publicKey: pk }) as PublicKeyCredential;
  const r = cred.response as AuthenticatorAttestationResponse;
  return { id: cred.id, rawId: aB64(cred.rawId), type: cred.type,
    response: { clientDataJSON: aB64(r.clientDataJSON), attestationObject: aB64(r.attestationObject) } };
}

export async function firmaPasskey(opzioni: any): Promise<unknown> {
  const pk = { ...opzioni, challenge: daB64(opzioni.challenge),
    allowCredentials: (opzioni.allowCredentials ?? []).map((c: any) => ({ ...c, id: daB64(c.id) })) };
  const cred = await navigator.credentials.get({ publicKey: pk }) as PublicKeyCredential;
  const r = cred.response as AuthenticatorAssertionResponse;
  return { id: cred.id, rawId: aB64(cred.rawId), type: cred.type,
    response: { clientDataJSON: aB64(r.clientDataJSON), authenticatorData: aB64(r.authenticatorData),
      signature: aB64(r.signature), userHandle: r.userHandle ? aB64(r.userHandle) : null } };
}
