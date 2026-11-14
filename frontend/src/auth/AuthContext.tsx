// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Sessione dell'interfaccia e accesso con SPID/CIE (R02, g2-auth).
 *
 * Flusso SPID/CIE (gateway, services/gateway/app/auth/spid.py):
 *   1. `avviaAccessoIdentita("spid" | "cie")` porta il browser su
 *      /api/v1/auth/spid/login, che imposta un cookie HttpOnly di sessione e
 *      rimanda all'aggregatore;
 *   2. al ritorno il gateway rimanda a /login con un CODICE MONOUSO nel frammento
 *      dell'indirizzo (#spid_codice=…, mai il token nell'URL) oppure con
 *      #spid_errore=SPID-0xx; `leggiRitornoIdentita()` lo legge e lo toglie
 *      subito dalla barra degli indirizzi e dalla cronologia;
 *   3. `scambiaCodiceIdentita(codice, enteScelto?)` fa POST /api/v1/auth/spid/scambio
 *      (lo stesso cookie lega il codice a questo browser): il gateway consegna la
 *      sessione in un cookie HttpOnly, oppure l'elenco degli enti quando la persona
 *      è utente di più enti (REGOLE §14.12).
 *
 * R05 (VD-C-079, REGOLE §19.7): la pagina NON vede mai il token. Il gateway lo
 * mette nel cookie `vd_sessione` (HttpOnly, Secure, SameSite=Strict, solo /api),
 * che il browser rimanda da solo alle API della stessa origine; il cookie
 * `vd_csrf` (leggibile) va ripetuto nell'intestazione `X-CSRF-Token` di ogni
 * richiesta che modifica (`intestazioniCsrf`). Niente `localStorage`: al
 * caricamento la pagina chiede chi è connesso a GET /api/v1/auth/sessione;
 * «Esci» chiama DELETE /api/v1/auth/sessione, che cancella i cookie.
 */
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import {
  AVVISO_MS, INDIRIZZO_SCADUTA, limitePredefinito, registraAzione, rimanente, segnaRinnovo,
  serveBattito,
} from "./inattivita";

export type Ruolo =
  | "SUPER_ADMIN"
  | "ADMIN_TENANT"
  | "OPERATORE"
  | "VIEWER"
  | "RSC_CONSERVATORE"
  | "API_SYSTEM";

/** Dati dell'utente connesso per l'interfaccia (il token resta nel cookie HttpOnly). */
export interface UserSession {
  tenant_id: string;
  user_id: string;
  ruolo: Ruolo;
  ragione_sociale: string;
  piano: string;
  scopes: string[];
  /** Revisione R17: permessi effettivi dal gateway (vuoto: modello del ruolo fisso). */
  permessi?: string[];
  /** Solo per gli accessi con identità digitale. */
  nome?: string;
  cognome?: string;
  /** R06 (VD-C-127): secondi d'inattività dopo cui la sessione scade. */
  inattivitaSecondi?: number;
  /** R07 (D14): anticipo dell'avviso «resta collegato», in secondi. */
  avvisoSecondi?: number;
}

interface AuthCtx {
  user: UserSession | null;
  login: (session: UserSession) => void;
  logout: () => void;
  hasScope: (scope: string) => boolean;
  hasRole: (...roles: Ruolo[]) => boolean;
  isAdmin: () => boolean;
  isSuperAdmin: () => boolean;
  isRSC: () => boolean;
}

const Ctx = createContext<AuthCtx>({
  user: null, login: () => {}, logout: () => {},
  hasScope: () => false, hasRole: () => false,
  isAdmin: () => false, isSuperAdmin: () => false, isRSC: () => false,
});

export const useAuth = () => useContext(Ctx);

// ── Cookie della sessione e protezione CSRF ────────────────────────

const URL_SESSIONE = "/api/v1/auth/sessione";
const COOKIE_CSRF = "vd_csrf";
const METODI_SICURI = new Set(["GET", "HEAD", "OPTIONS"]);

/** Valore del cookie anti-CSRF (doppio invio); stringa vuota senza sessione. */
export function tokenCsrf(): string {
  const voce = document.cookie.split(";").map(c => c.trim()).find(c => c.startsWith(`${COOKIE_CSRF}=`));
  return voce ? decodeURIComponent(voce.slice(COOKIE_CSRF.length + 1)) : "";
}

/** Intestazioni anti-CSRF per una richiesta con `metodo` (vuote per GET/HEAD). */
export function intestazioniCsrf(metodo: string): Record<string, string> {
  const csrf = tokenCsrf();
  return !METODI_SICURI.has(metodo.toUpperCase()) && csrf ? { "X-CSRF-Token": csrf } : {};
}

/** Dati dell'utente dalla risposta del gateway (scambio SPID/CIE o GET /sessione). */
export function sessioneDaRisposta(u: any): UserSession | null {
  if (!u?.tenant_id || !u?.ruolo) return null;
  return {
    tenant_id: String(u.tenant_id),
    user_id: String(u.user_id ?? ""),
    ruolo: u.ruolo as Ruolo,
    ragione_sociale: String(u.ragione_sociale ?? ""),
    piano: String(u.piano ?? ""),
    scopes: Array.isArray(u.scopes) ? u.scopes.map(String) : [],
    permessi: Array.isArray(u.permessi) ? u.permessi.map(String) : undefined,
    nome: u.nome ? String(u.nome) : undefined,
    cognome: u.cognome ? String(u.cognome) : undefined,
  };
}

/** Chi è connesso secondo il gateway (cookie HttpOnly); null senza sessione valida. */
export async function leggiSessione(): Promise<UserSession | null> {
  try {
    const resp = await fetch(URL_SESSIONE, { credentials: "same-origin" });
    if (!resp.ok) return null;
    const corpo: any = await resp.json().catch(() => null);
    const sessione = sessioneDaRisposta(corpo?.utente);
    if (sessione && Number(corpo?.inattivita_secondi) > 0) {
      sessione.inattivitaSecondi = Number(corpo.inattivita_secondi);
    }
    if (sessione && Number(corpo?.avviso_inattivita_secondi) > 0) {
      sessione.avvisoSecondi = Number(corpo.avviso_inattivita_secondi);
    }
    return sessione;
  } catch {
    return null;
  }
}

/** Uscita: il gateway cancella i cookie della sessione. */
export async function chiudiSessione(): Promise<void> {
  try {
    await fetch(URL_SESSIONE, {
      method: "DELETE", credentials: "same-origin", headers: intestazioniCsrf("DELETE"),
    });
  } catch {
    // Rete assente: l'interfaccia esce comunque; il cookie scade da solo.
  }
}

/**
 * R06 (VD-C-127): rinnova la sessione (GET /sessione, a scorrimento). Restituisce
 * false se il gateway la considera scaduta o chiusa.
 */
export async function rinnovaSessione(): Promise<boolean> {
  try {
    const resp = await fetch(URL_SESSIONE, { credentials: "same-origin" });
    if (resp.ok) segnaRinnovo();
    return resp.status !== 401;
  } catch {
    return true; // rete assente: decide il prossimo controllo
  }
}

const EVENTI_AZIONE = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

/**
 * Avviso di scadenza per inattività e ritorno all'accesso (R06, VD-C-127).
 * R07 (D14): due minuti prima del limite (anticipo dal gateway) chiede se restare
 * collegati; al limite chiude.
 */
function ControlloInattivita({ limiteSecondi, avvisoMs = AVVISO_MS, onScaduta }: {
  limiteSecondi: number; avvisoMs?: number; onScaduta: () => void;
}) {
  const [avviso, setAvviso] = useState<number | null>(null);

  useEffect(() => {
    let ultima = 0;
    const azione = () => {
      const adesso = Date.now();
      if (adesso - ultima > 1000) { ultima = adesso; registraAzione(adesso); }
    };
    EVENTI_AZIONE.forEach(e => window.addEventListener(e, azione, { passive: true }));
    const controllo = window.setInterval(() => {
      const resta = rimanente(limiteSecondi);
      if (resta <= 0) { onScaduta(); return; }
      if (resta <= avvisoMs) { setAvviso(Math.ceil(resta / 1000)); return; }
      setAvviso(null);
      if (serveBattito()) {
        void rinnovaSessione().then(valida => { if (!valida) onScaduta(); });
      }
    }, 1000);
    return () => {
      EVENTI_AZIONE.forEach(e => window.removeEventListener(e, azione));
      window.clearInterval(controllo);
    };
  }, [limiteSecondi, avvisoMs, onScaduta]);

  if (avviso === null) return null;
  const resta = async () => {
    registraAzione();
    if (await rinnovaSessione()) setAvviso(null); else onScaduta();
  };
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="vd-inattivita-titolo"
         aria-describedby="vd-inattivita-testo" className="vd-avviso-inattivita"
         style={{ position: "fixed", inset: 0, display: "grid", placeItems: "center",
                  background: "rgba(0,0,0,.45)", zIndex: 1000 }}>
      <div style={{ background: "var(--vd-sfondo, #fff)", color: "inherit", padding: 24,
                    borderRadius: 8, maxWidth: 420 }}>
        <h2 id="vd-inattivita-titolo" style={{ marginTop: 0 }}>La sessione sta per scadere</h2>
        <p id="vd-inattivita-testo">
          Per sicurezza la sessione si chiude dopo un periodo senza attività.
          Mancano {avviso} secondi.
        </p>
        <button type="button" className="vd-btn" autoFocus onClick={() => void resta()}>
          Resta collegato
        </button>
      </div>
    </div>
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserSession | null>(null);
  // Finché il gateway non ha detto se c'è una sessione, nessuna pagina decide
  // (altrimenti un ricaricamento porterebbe sempre alla pagina di accesso).
  const [verificata, setVerificata] = useState(false);

  useEffect(() => {
    let attivo = true;
    leggiSessione().then(sessione => {
      if (!attivo) return;
      if (sessione) segnaRinnovo();
      setUser(prima => prima ?? sessione);
      setVerificata(true);
    });
    return () => { attivo = false; };
  }, []);

  const login = (session: UserSession) => {
    registraAzione();
    segnaRinnovo();
    setUser(session);
    setVerificata(true);
    // il limite esatto lo comunica il gateway (configurabile)
    void leggiSessione().then(s => {
      if (s?.inattivitaSecondi) {
        setUser(prima => prima && prima.user_id === s.user_id
          ? { ...prima, inattivitaSecondi: s.inattivitaSecondi, avvisoSecondi: s.avvisoSecondi }
          : prima);
      }
    });
  };

  const scaduta = () => {
    setUser(null);
    void chiudiSessione();
    window.location.href = INDIRIZZO_SCADUTA;
  };

  const logout = () => {
    setUser(null);
    void chiudiSessione();
  };

  const hasScope = (scope: string) => {
    if (!user) return false;
    if (user.scopes.includes("*")) return true;
    if (["SUPER_ADMIN", "RSC_CONSERVATORE"].includes(user.ruolo)) return true;
    return user.scopes.includes(scope);
  };

  const hasRole = (...roles: Ruolo[]) =>
    !!user && roles.includes(user.ruolo);

  const isAdmin = () =>
    hasRole("SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE");

  const isSuperAdmin = () => hasRole("SUPER_ADMIN");

  const isRSC = () => hasRole("RSC_CONSERVATORE");

  return (
    <Ctx.Provider value={{ user, login, logout, hasScope, hasRole, isAdmin, isSuperAdmin, isRSC }}>
      {verificata && user
        ? <ControlloInattivita limiteSecondi={user.inattivitaSecondi ?? limitePredefinito(user.ruolo)}
                               avvisoMs={user.avvisoSecondi ? user.avvisoSecondi * 1000 : AVVISO_MS}
                               onScaduta={scaduta} />
        : null}
      {verificata
        ? children
        : <p role="status" className="vd-nota" style={{ fontSize: 16, padding: 28 }}>Verifica della sessione…</p>}
    </Ctx.Provider>
  );
}

// ── Accesso con identità digitale (SPID / CIE) ──────────────────────

export type ProviderIdentita = "spid" | "cie";

/** Esito del ritorno dal gestore, letto dal frammento dell'indirizzo. */
export type RitornoIdentita =
  | { tipo: "codice"; codice: string }
  | { tipo: "errore"; codice: string }
  | null;

export interface EnteSelezionabile {
  tenant_id: string;
  ragione_sociale: string;
  ruolo: Ruolo;
}

export type EsitoScambio =
  | { esito: "accesso"; sessione: UserSession }
  | { esito: "scelta_ente"; codice: string; enti: EnteSelezionabile[]; scadenzaSecondi: number };

/** Errore del flusso con il codice del gateway (SPID-0xx) o RETE/HTTP-nnn. */
export class ErroreAccesso extends Error {
  constructor(public codice: string, public dettaglio: string = "") {
    super(codice);
  }
}

const URL_LOGIN = "/api/v1/auth/spid/login";
const URL_SCAMBIO = "/api/v1/auth/spid/scambio";

/** Indirizzo di ritorno: la pagina di accesso di questa stessa origine (whitelist del gateway). */
export function indirizzoDiRitorno(): string {
  return `${window.location.origin}/login`;
}

/** Porta il browser al gateway, che avvia l'accesso presso l'aggregatore. */
export function avviaAccessoIdentita(provider: ProviderIdentita): void {
  const q = new URLSearchParams({ provider, redirect_uri: indirizzoDiRitorno(), livello: "2" });
  window.location.assign(`${URL_LOGIN}?${q.toString()}`);
}

/** Legge #spid_codice / #spid_errore senza modificare nulla (funzione pura). */
export function leggiRitornoIdentita(): RitornoIdentita {
  const frammento = window.location.hash.replace(/^#/, "");
  if (!frammento) return null;
  const p = new URLSearchParams(frammento);
  const codice = p.get("spid_codice");
  const errore = p.get("spid_errore");
  if (codice) return { tipo: "codice", codice };
  if (errore) return { tipo: "errore", codice: errore };
  return null;
}

/** Toglie il frammento da indirizzo e cronologia: il codice monouso non resta
 * visibile e non si ripresenta con «Indietro». */
export function pulisciRitornoIdentita(): void {
  if (window.location.hash) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
}

/** Scambia il codice monouso con la sessione in cookie (o con l'elenco degli enti tra cui scegliere). */
export async function scambiaCodiceIdentita(codice: string, tenantId?: string): Promise<EsitoScambio> {
  let resp: Response;
  try {
    resp = await fetch(URL_SCAMBIO, {
      method: "POST",
      credentials: "same-origin", // il cookie HttpOnly lega il codice a questo browser
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(tenantId ? { codice, tenant_id: tenantId } : { codice }),
    });
  } catch {
    throw new ErroreAccesso("RETE");
  }
  const corpo: any = await resp.json().catch(() => null);
  if (!resp.ok) {
    // Forma d'errore: problem+json del catalogo {codice, title, detail?, id_richiesta}
    // (VD-C-100); compatibile con quella di R02 {detail: {type, title, ...}} (§18)
    const d = corpo?.codice ? corpo : corpo?.detail;
    const tipo = d && typeof d === "object" && (d.codice || d.type) ? String(d.codice ?? d.type) : `HTTP-${resp.status}`;
    const testo = typeof d === "string" ? d : d && typeof d === "object" ? String(d.title ?? d.detail ?? "") : "";
    const rif = (d && typeof d === "object" && (d.id_richiesta ?? d.id_correlazione)) || resp.headers.get("x-request-id") || resp.headers.get("x-correlation-id");
    throw new ErroreAccesso(tipo, `${testo}${rif ? ` (riferimento per la segnalazione ${rif})` : ""}`);
  }
  if (corpo?.esito === "scelta_ente" && Array.isArray(corpo.enti) && corpo.codice) {
    return { esito: "scelta_ente", codice: String(corpo.codice), enti: corpo.enti,
             scadenzaSecondi: Number(corpo.scadenza_secondi) || 120 };
  }
  // R05 (VD-C-079): il token è nel cookie HttpOnly appena impostato, mai nel corpo
  const sessione = corpo?.esito === "accesso" ? sessioneDaRisposta(corpo?.utente) : null;
  if (!sessione) {
    throw new ErroreAccesso("RISPOSTA", "risposta del gateway incompleta");
  }
  return { esito: "accesso", sessione };
}
