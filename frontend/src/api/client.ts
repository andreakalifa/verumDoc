// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
import axios from "axios";
import { intestazioniCsrf } from "../auth/AuthContext";
import { INDIRIZZO_SCADUTA, intestazioniRinnovo } from "../auth/inattivita";

/**
 * Client delle API del portale.
 * R05 (VD-C-079): nessun token nella pagina. Il browser manda da solo il cookie
 * HttpOnly della sessione (stessa origine); le richieste che modificano portano
 * l'intestazione anti-CSRF `X-CSRF-Token` (doppio invio del cookie `vd_csrf`).
 */
export const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  // R06 (VD-C-127): senza azioni recenti dell'utente la richiesta non rinnova la sessione
  const intestazioni = { ...intestazioniCsrf(config.method ?? "get"), ...intestazioniRinnovo() };
  for (const [nome, valore] of Object.entries(intestazioni)) {
    config.headers.set(nome, valore);
  }
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    // Redirige al login SOLO se 401 e non siamo già in /login
    if (err.response?.status === 401 &&
        !window.location.pathname.includes("/login")) {
      // Sessione scaduta o revocata: il cookie non vale più, si torna all'accesso.
      // Accesso revocato (utente disattivato o ente sospeso): la pagina di accesso lo spiega
      // Catalogo errori (VD-C-100): `codice`; forma di R02: `detail.type`
      const tipo = err.response?.data?.codice ?? err.response?.data?.detail?.type;
      window.location.href = tipo === "AUTH-003" ? "/login#spid_errore=AUTH-003"
        : tipo === "AUTH-004" ? INDIRIZZO_SCADUTA : "/login";
    }
    return Promise.reject(err);
  }
);

// ── Overview & KPI ──────────────────────────────────────────────────
export const fetchOverview = (tid: string) =>
  api.get(`/dashboard/overview?tenant_id=${tid}`).then(r => r.data);

// ── Versamenti (PdV pipeline) ───────────────────────────────────────
// R03: elenco a cursore (niente offset né totale): `{items, limit, cursore, altri}`;
// `cursore` della risposta apre la pagina successiva.
export const fetchVersamenti = (tid: string, limit = 20, cursore?: string | null, stato?: string) => {
  const p = new URLSearchParams({ tenant_id: tid, limit: String(limit) });
  if (cursore) p.append("cursore", cursore);
  if (stato) p.append("stato", stato);
  return api.get(`/dashboard/versamenti?${p}`).then(r => r.data);
};

// ── AIP ────────────────────────────────────────────────────────────
// R03 (REV-06): elenco a cursore come i versamenti: `{items, limit, cursore, altri, campi_allegato5}`.
export const fetchAIP = (tid: string, stato?: string, cursore?: string | null, limit = 20) => {
  const p = new URLSearchParams({ tenant_id: tid, limit: String(limit) });
  if (cursore) p.append("cursore", cursore);
  if (stato) p.append("stato", stato);
  return api.get(`/dashboard/aip?${p}`).then(r => r.data);
};

// ── Hashchain ──────────────────────────────────────────────────────
export const fetchHashchain = (limit = 5) =>
  api.get(`/dashboard/hashchain?limit=${limit}`).then(r => r.data);
/**
 * Verifica completa della catena (SUPER_ADMIN, RSC_CONSERVATORE), ASINCRONA (g4-chiusura):
 * l'avvio risponde subito 202 `{verifica_id, stato: "IN_CORSO", avviata_il}` oppure 409
 * HC-409 con il `verifica_id` di quella già in corso; lo stato si interroga a parte.
 */
export const avviaVerificaCompleta = () =>
  api.post("/dashboard/hashchain/verifica-completa", {}).then(r => r.data);
/** Stato di una verifica: IN_CORSO | COMPLETATA (esito) | FALLITA | INTERROTTA, con le date. */
export const fetchStatoVerifica = (verificaId: string) =>
  api.get(`/dashboard/hashchain/verifica-completa/${encodeURIComponent(verificaId)}`).then(r => r.data);

// ── Ricerca ────────────────────────────────────────────────────────
// R03 (REV-06): a cursore, senza totale: `{items, limit, cursore, altri, motore,
// filtri_applicati, campi_testo}`. Con testo o classe cerca sull'indice di ricerca.
export const searchAIP = (payload: object) =>
  api.post("/dashboard/search", payload).then(r => r.data);

// ── Tenant (SUPER_ADMIN) ───────────────────────────────────────────
export const fetchTenants = (stato?: string) =>
  api.get(`/dashboard/tenants${stato ? "?stato=" + stato : ""}`).then(r => r.data);
export const createTenant = (body: object) =>
  api.post("/dashboard/tenants", body).then(r => r.data);
export const activateTenant = (id: string) =>
  api.post(`/dashboard/tenants/${id}/attiva`).then(r => r.data);
/** R06 (VD-C-138): natura dell'ente (onboarding, solo SUPER_ADMIN). */
export const fetchTenant = (id: string) =>
  api.get(`/v1/onboarding/tenant/${encodeURIComponent(id)}`).then(r => r.data as { id: string; natura?: string | null; codice_ipa?: string | null });
/** R07 (D25): motivo obbligatorio solo per PRIVATO; per PA, se vuoto, il servizio registra il codice IPA. */
export const dichiaraNatura = (id: string, natura: "PA" | "PRIVATO", motivo: string) =>
  api.put(`/v1/onboarding/tenant/${encodeURIComponent(id)}/natura`,
          motivo ? { natura, motivo } : { natura }).then(r => r.data);

// ── Distribuzione ──────────────────────────────────────────────────
export const richiediPdD = (body: object) =>
  api.post("/dashboard/distribution", body).then(r => r.data);
// HIGH-003 FIX: distribution endpoints use /v2 prefix, not /v1
/** Storico delle esibizioni; `tid` si invia solo per un profilo globale che ha scelto un altro ente.
 * R04 (VD-C-102): a cursore, `{items, limit, cursore, altri}`, senza totale. */
export const fetchStoricoDistribuzioni = (tid: string | undefined, cursore: string | null = null, limit = 20) => {
  const p = new URLSearchParams({ limit: String(limit) });
  if (tid) p.append("tenant_id", tid);
  if (cursore) p.append("cursore", cursore);
  return api.get(`/v2/distribution/storico?${p}`).then(r => r.data);
};
export const approvaPdD = (pddId: string) =>
  api.post(`/v2/distribution/${pddId}/approva`).then(r => r.data);
/** R07 (D18): rigenera con un clic un pacchetto di distribuzione scaduto (stesso id). */
export const rigeneraPdD = (pddId: string) =>
  api.post(`/v2/distribution/pdd/${pddId}/rigenera`).then(r => r.data);
export const rifiutaPdD = (pddId: string, motivo: string) =>
  api.post(`/v2/distribution/${pddId}/rifiuta`, { motivo }).then(r => r.data);

// ── Notifiche ─────────────────────────────────────────────────────
// R04 (VD-C-102): a cursore, `{items, limit, cursore, altri}`, senza totale.
export const fetchNotifiche = (tid: string, cursore: string | null = null, limit = 20) => {
  const p = new URLSearchParams({ tenant_id: tid, limit: String(limit) });
  if (cursore) p.append("cursore", cursore);
  return api.get(`/dashboard/notifiche?${p}`).then(r => r.data);
};

// ── Registro delle operazioni ─────────────────────────────────────
// R04 (VD-C-102): a cursore, `{items, limit, cursore, altri}`, senza totale.
export const fetchAudit = (tid: string | null, evento: string, cursore: string | null, limit: number) => {
  const p = new URLSearchParams({ limit: String(limit) });
  if (tid) p.append("tenant_id", tid);
  if (evento) p.append("evento", evento);
  if (cursore) p.append("cursore", cursore);
  return api.get(`/dashboard/audit?${p}`).then(r => r.data);
};

// ── Code di scarto (R04, VD-C-075) — SUPER_ADMIN e RSC_CONSERVATORE ──
export interface FiltriScarti { stato?: string; coda?: string; servizio?: string; tenant_id?: string; pdv_id?: string }
export type SelezioneScarti = { ids: number[] } | { filtri: Omit<FiltriScarti, "stato"> };

const senzaVuoti = <T extends object>(o: T) =>
  Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "")) as Partial<T>;

export const fetchScarti = (filtri: FiltriScarti, cursore: string | null, limit: number) => {
  const p = new URLSearchParams({ limit: String(limit), ...senzaVuoti(filtri) as Record<string, string> });
  if (cursore) p.append("cursore", cursore);
  return api.get(`/dashboard/scarti?${p}`).then(r => r.data);
};
export const fetchCodeScarto = () => api.get("/dashboard/scarti/code").then(r => r.data);
export const fetchScarto = (id: number) => api.get(`/dashboard/scarti/${id}`).then(r => r.data);
/** Anteprima di «Rielabora» (non scrive): piano con impronta (REGOLE §13). */
export const anteprimaRielabora = (sel: SelezioneScarti) => {
  const corpo = "filtri" in sel ? { filtri: senzaVuoti(sel.filtri) } : sel;
  return api.post("/dashboard/scarti/rielabora/anteprima", corpo).then(r => r.data);
};
/** Applica il piano confermato; 409 DSH-409-PIANO con il piano aggiornato in `dettagli.piano`. */
export const applicaRielabora = (sel: SelezioneScarti, impronta_piano: string, motivo: string) => {
  const corpo = "filtri" in sel ? { filtri: senzaVuoti(sel.filtri) } : sel;
  return api.post("/dashboard/scarti/rielabora/applica", { ...corpo, impronta_piano, motivo }).then(r => r.data);
};
/** R07: «Rielabora» di un proprio versamento rifiutato (ADMIN_TENANT e OPERATORE dell'ente). */
export const anteprimaRielaboraVersamento = (pdvId: string) =>
  api.post(`/dashboard/versamenti/${encodeURIComponent(pdvId)}/rielabora/anteprima`).then(r => r.data);
export const applicaRielaboraVersamento = (pdvId: string, impronta_piano: string, motivo: string) =>
  api.post(`/dashboard/versamenti/${encodeURIComponent(pdvId)}/rielabora/applica`,
           { impronta_piano, motivo }).then(r => r.data);
export const archiviaScarto = (id: number, motivo: string) =>
  api.post(`/dashboard/scarti/${id}/archivia`, { motivo }).then(r => r.data);

// ── Utenti tenant ─────────────────────────────────────────────────
export const fetchUtenti = (tid: string) =>
  api.get(`/v1/onboarding/tenant/${tid}/utenti`).then(r => r.data);
export const createUtente = (tid: string, body: object) =>
  api.post(`/v1/onboarding/tenant/${tid}/utenti`, body).then(r => r.data);
export const updateUtente = (tid: string, uid: string, body: object) =>
  api.put(`/v1/onboarding/tenant/${tid}/utenti/${uid}`, body).then(r => r.data);
export const disattivaUtente = (tid: string, uid: string) =>
  api.delete(`/v1/onboarding/tenant/${tid}/utenti/${uid}`).then(r => r.data);

// ── API Keys ──────────────────────────────────────────────────────
export const fetchApiKeys = (tid: string) =>
  api.get(`/v1/onboarding/tenant/${tid}/api-keys`).then(r => r.data);
export const generateApiKey = (tid: string, body: object) =>
  api.post(`/v1/onboarding/tenant/${tid}/api-keys`, body).then(r => r.data);
export const revokaApiKey = (tid: string, kid: string) =>
  api.delete(`/v1/onboarding/tenant/${tid}/api-keys/${kid}`).then(r => r.data);

// ── Classi documentali per tenant ────────────────────────────────
export const fetchClassiTenant = (tid: string) =>
  api.get(`/v1/onboarding/tenant/${tid}/classi`).then(r => r.data);
export const updateClassiTenant = (tid: string, body: object) =>
  api.put(`/v1/onboarding/tenant/${tid}/classi`, body).then(r => r.data);

// ── SLA & Report ─────────────────────────────────────────────────
export const fetchSLA = (mese?: string) =>
  api.get(`/dashboard/sla/report${mese ? "?mese=" + mese : ""}`).then(r => r.data);
export const fetchSLAThroughput = (giorni = 7) =>
  api.get(`/dashboard/sla/throughput?giorni=${giorni}`).then(r => r.data);
export const fetchTenantsStats = () =>
  api.get("/dashboard/admin/tenants-stats").then(r => r.data);

// ── Manuale della Conservazione (RSC) ────────────────────────────
export const fetchManuale = (tid: string) =>
  api.get(`/dashboard/manuale-conservazione?tenant_id=${tid}`).then(r => r.data);
