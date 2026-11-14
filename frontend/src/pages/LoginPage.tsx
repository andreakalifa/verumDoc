// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Accesso al servizio — sistema grafico «Partenze» (VD-DR-01), pagina intera senza testata.
 *
 * R02 (g2-auth): accesso reale con SPID o CIE tramite il gateway.
 *   «Entra con SPID» / «Entra con CIE» → /api/v1/auth/spid/login → aggregatore →
 *   ritorno qui con un codice monouso nel frammento (#spid_codice) → POST
 *   /api/v1/auth/spid/scambio → sessione in cookie HttpOnly (R05, VD-C-079: la
 *   pagina non vede né salva il token).
 *   Il token non passa mai dall'indirizzo. Gli errori del flusso (#spid_errore o
 *   risposta dello scambio) si mostrano per esteso: cosa è successo e cosa fare.
 *   Una persona utente di più enti sceglie l'ente (REGOLE §14.12, mai «il primo»).
 *
 * VD-C-029: i profili dimostrativi e la chiamata a /auth/dev-token esistono solo nella
 * versione di sviluppo (`import.meta.env.DEV`): nella compilazione di produzione il
 * blocco è eliminato e non entra nel pacchetto (REGOLE §19.3).
 */
import { useEffect, useRef, useState } from "react";
import { ErroreRichiesta, chiamaPubblica, firmaPasskey } from "../console/accesso";
import { useNavigate } from "react-router-dom";
import {
  useAuth, UserSession, Ruolo, EnteSelezionabile, ErroreAccesso, ProviderIdentita,
  avviaAccessoIdentita, leggiRitornoIdentita, pulisciRitornoIdentita, scambiaCodiceIdentita,
  sessioneDaRisposta,
} from "../auth/AuthContext";
import { NOME_RUOLO } from "../auth/ruoli";


/** Spiegazione per esteso di ogni esito negativo del flusso (codici del gateway). */
const ERRORI: Record<string, { titolo: string; testo: string; cosaFare: string }> = {
  "SPID-001": {
    titolo: "Accesso con SPID e CIE non ancora attivo",
    testo: "Il servizio non è ancora collegato a un aggregatore SPID/CIE, quindi non può riconoscere la tua identità digitale.",
    cosaFare: "Rivolgiti al responsabile del servizio di conservazione: l'accesso sarà disponibile quando il collegamento è configurato.",
  },
  "SPID-002": {
    titolo: "Accesso scaduto o non riconosciuto",
    testo: "Il ritorno dal gestore dell'identità non corrisponde a un accesso valido avviato da questa pagina, oppure è scaduto.",
    cosaFare: "Ricomincia l'accesso da questa pagina e completalo entro cinque minuti.",
  },
  "SPID-003": {
    titolo: "Accesso annullato",
    testo: "L'accesso è stato annullato presso il gestore dell'identità digitale, oppure il gestore non l'ha autorizzato.",
    cosaFare: "Se non l'hai annullato tu, riprova; se il problema continua, verifica la tua identità presso il gestore.",
  },
  "SPID-004": {
    titolo: "Il gestore dell'identità non ha risposto",
    testo: "Lo scambio con l'aggregatore SPID/CIE non è riuscito: nessun dato di accesso è stato ricevuto.",
    cosaFare: "Riprova tra qualche minuto. Se l'errore si ripete, segnalalo al responsabile del servizio.",
  },
  "SPID-005": {
    titolo: "Identità digitale non verificata",
    testo: "La risposta del gestore non ha superato i controlli di sicurezza (firma, scadenza o legame con questa sessione). Per prudenza l'accesso è stato rifiutato.",
    cosaFare: "Ricomincia l'accesso da questa pagina, senza riusare finestre o link di un accesso precedente.",
  },
  "SPID-006": {
    titolo: "Accesso iniziato in un'altra finestra o scaduto",
    testo: "Il ritorno dal gestore non corrisponde a un accesso avviato da questo browser, oppure sono passati più di cinque minuti.",
    cosaFare: "Ricomincia l'accesso da questa pagina, nello stesso browser, e completalo entro cinque minuti.",
  },
  "SPID-007": {
    titolo: "Codice fiscale non ricevuto",
    testo: "L'identità digitale è stata verificata, ma il gestore non ha trasmesso un codice fiscale valido: senza codice fiscale non è possibile riconoscerti.",
    cosaFare: "Riprova autorizzando la trasmissione del codice fiscale; se il problema continua, rivolgiti al tuo gestore dell'identità.",
  },
  "SPID-008": {
    titolo: "Nessun utente associato al tuo codice fiscale",
    testo: "La tua identità è stata verificata, ma nessun ente attivo ha registrato un utente attivo con il tuo codice fiscale.",
    cosaFare: "Chiedi all'amministrazione del tuo ente di registrarti (o riattivarti) nella pagina «Utenti e chiavi API» indicando il tuo codice fiscale.",
  },
  "SPID-008-ENTE": {
    titolo: "Ente non disponibile per il tuo profilo",
    testo: "L'ente scelto non risulta tra quelli in cui sei registrato come utente attivo, oppure nel frattempo è stato sospeso.",
    cosaFare: "Ripeti l'accesso e scegli un altro ente; se l'ente dovrebbe esserci, rivolgiti alla sua amministrazione.",
  },
  "SPID-009": {
    titolo: "Livello di sicurezza dell'accesso insufficiente",
    testo: "Il servizio richiede almeno SPID di livello 2 (password più codice temporaneo o app), oppure CIE.",
    cosaFare: "Ripeti l'accesso scegliendo il livello 2 nella pagina del tuo gestore dell'identità, oppure entra con CIE.",
  },
  "AUTH-003": {
    titolo: "Accesso revocato",
    testo: "Il tuo utente è stato disattivato, oppure il tuo ente è stato sospeso. La sessione è stata chiusa.",
    cosaFare: "Se pensi che sia un errore, rivolgiti all'amministrazione del tuo ente.",
  },
  "AUTH-004": {
    titolo: "Sessione scaduta per inattività",
    testo: "Per sicurezza la sessione si chiude dopo un periodo senza attività: 30 minuti, 15 per i profili del conservatore.",
    cosaFare: "Accedi di nuovo con SPID o CIE.",
  },
  "SPID-010": {
    titolo: "Pagina di accesso non autorizzata dal servizio",
    testo: "Il servizio accetta il ritorno dal gestore dell'identità solo verso indirizzi registrati, e questo non lo è.",
    cosaFare: "Accedi dall'indirizzo ufficiale del servizio; se lo stai già usando, segnala il codice al responsabile del servizio: l'indirizzo va aggiunto all'elenco degli indirizzi di ritorno ammessi.",
  },
  "SPID-011": {
    titolo: "Gestore dell'identità non ammesso",
    testo: "Il servizio accetta solo SPID e CIE.",
    cosaFare: "Usa uno dei due comandi di questa pagina.",
  },
  "SPID-012": {
    titolo: "Passaggio di accesso scaduto o già usato",
    testo: "Il codice che collega il ritorno dal gestore a questa pagina vale una sola volta e per due minuti.",
    cosaFare: "Ripeti l'accesso con SPID o CIE.",
  },
  RETE: {
    titolo: "Il servizio non risponde",
    testo: "Non è stato possibile contattare il gateway del servizio di conservazione.",
    cosaFare: "Controlla la connessione e riprova; se il problema continua, il servizio potrebbe essere in manutenzione.",
  },
};

function spiegazione(codice: string, dettaglio = "") {
  return ERRORI[codice] ?? {
    titolo: "Accesso non riuscito",
    testo: `Il servizio ha rifiutato l'accesso${dettaglio ? `: ${dettaglio}` : "."}`,
    cosaFare: "Ripeti l'accesso; se l'errore si ripete, comunica il codice qui sotto al responsabile del servizio.",
  };
}

interface ProfiloSviluppo {
  tenant_id: string;
  user_id: string;
  ruolo: Ruolo;
  ragione_sociale: string;
  piano: string;
  scopes: string[];
  desc: string;
}

/**
 * Accesso rapido con profili dimostrativi: SOLO sviluppo.
 * Il componente vale `null` fuori da `vite dev`, e con lui spariscono profili e chiamata.
 */
const AccessoSviluppo: null | ((p: { onEntrato: () => void }) => JSX.Element) = import.meta.env.DEV
  ? function AccessoSviluppo({ onEntrato }) {
      const { login } = useAuth();
      const [inCorso, setInCorso] = useState<string | null>(null);
      const [errore, setErrore] = useState("");

      // F1-05: 5 profili con gli scope di ogni ruolo (dati dimostrativi)
      const PROFILI: ProfiloSviluppo[] = [
        { tenant_id: "00000000-0000-0000-0000-000000000001", user_id: "00000000-0000-0000-0000-000000000010",
          ruolo: "SUPER_ADMIN", ragione_sociale: "VerumDoc S.r.l. (amministrazione)", piano: "enterprise",
          scopes: ["*"], desc: "Tutti gli enti, tutte le funzioni" },
        { tenant_id: "00000000-0000-0000-0000-000000000001", user_id: "00000000-0000-0000-0000-000000000014",
          ruolo: "RSC_CONSERVATORE", ragione_sociale: "VerumDoc S.r.l. (responsabile)", piano: "enterprise",
          scopes: ["*", "rsc:approve", "rsc:report"], desc: "Responsabile del servizio di conservazione" },
        { tenant_id: "00000000-0000-0000-0000-000000000001", user_id: "00000000-0000-0000-0000-000000000011",
          ruolo: "ADMIN_TENANT", ragione_sociale: "Acme S.r.l.", piano: "business",
          scopes: ["versamento:ud", "versamento:doc", "read", "distribution", "lifecycle", "admin:tenant"], desc: "Amministrazione dell'ente" },
        { tenant_id: "00000000-0000-0000-0000-000000000001", user_id: "00000000-0000-0000-0000-000000000012",
          ruolo: "OPERATORE", ragione_sociale: "Acme S.r.l.", piano: "business",
          scopes: ["versamento:ud", "versamento:doc", "read", "lifecycle"], desc: "Versa e consulta" },
        { tenant_id: "00000000-0000-0000-0000-000000000001", user_id: "00000000-0000-0000-0000-000000000013",
          ruolo: "VIEWER", ragione_sociale: "Acme S.r.l.", piano: "business",
          scopes: ["read"], desc: "Sola consultazione" },
      ];

      const entra = async (p: ProfiloSviluppo) => {
        setErrore("");
        setInCorso(p.ruolo);
        try {
          const resp = await fetch("/api/v1/auth/dev-token", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tenant_id: p.tenant_id, user_id: p.user_id, ruolo: p.ruolo, scopes: p.scopes }),
          });
          if (!resp.ok) {
            const body = await resp.json().catch(() => ({}));
            throw new Error(resp.status === 404
              ? "il gateway non offre il token di sviluppo (non avviato, oppure non è in ambiente di sviluppo)"
              : `il gateway ha risposto con un errore (${resp.status})${body?.title ? `: ${body.title}` : body?.detail ? `: ${typeof body.detail === "string" ? body.detail : body.detail.title ?? ""}` : ""}`);
          }
          // R05 (VD-C-079): la sessione arriva nel cookie HttpOnly impostato dal
          // gateway; la pagina conserva solo i dati del profilo, mai il token
          const { desc: _desc, ...sessione } = p;
          login(sessione as UserSession);
          onEntrato();
        } catch (e: any) {
          setErrore(e?.message?.startsWith("il ") ? e.message : "il gateway non risponde: avvia i servizi con docker compose");
        } finally {
          setInCorso(null);
        }
      };

      return (
        <section aria-labelledby="vd-sviluppo" className="vd-accesso-sviluppo">
          <div className="vd-sezione-testa" style={{ marginBottom: 0 }}>
            <h2 id="vd-sviluppo" className="vd-etichetta vd-etichetta--bordata">Profili di sviluppo</h2>
            <span className="vd-nota">Solo in sviluppo: non esiste nella versione di produzione.</span>
          </div>
          {errore && (
            <div className="vd-esito vd-esito--critico" role="alert">
              <p className="vd-esito-titolo">Accesso non riuscito</p>
              <p>{errore.charAt(0).toUpperCase() + errore.slice(1)}.</p>
            </div>
          )}
          <ul className="vd-profili" aria-label="Profili dimostrativi per lo sviluppo">
            {PROFILI.map(p => (
              <li key={p.ruolo}>
                <span>
                  <strong>{NOME_RUOLO[p.ruolo] ?? p.ruolo}</strong>
                  <span className="vd-sotto">{p.desc} · {p.ragione_sociale} (ente dimostrativo)</span>
                </span>
                <button type="button" className="vd-bottone vd-bottone--secondario"
                        disabled={inCorso !== null} onClick={() => entra(p)}>
                  {inCorso === p.ruolo ? "Accesso…" : "Entra"}<span className="vd-sr"> come {NOME_RUOLO[p.ruolo] ?? p.ruolo}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="vd-nota">
            Serve il gateway attivo su localhost:8000 con ENV di sviluppo. In sviluppo «Entra con SPID/CIE» usa il gestore
            simulato del gateway, con il codice fiscale FTZPRV00A01Z999V: entra solo se un utente attivo ha quel codice fiscale.
          </p>
        </section>
      );
    }
  : null;

type Fase =
  | { tipo: "pronto" }
  | { tipo: "verifica" }
  | { tipo: "scelta"; codice: string; enti: EnteSelezionabile[]; scadenzaSecondi: number }
  | { tipo: "errore"; codice: string; dettaglio: string };

/** Ritorno dal gestore, letto prima del primo disegno (lettura pura: il frammento
 * si toglie nell'effetto, una volta sola). */
function faseIniziale(): { fase: Fase; codice: string | null } {
  const ritorno = leggiRitornoIdentita();
  if (ritorno?.tipo === "codice") return { fase: { tipo: "verifica" }, codice: ritorno.codice };
  if (ritorno?.tipo === "errore") return { fase: { tipo: "errore", codice: ritorno.codice, dettaglio: "" }, codice: null };
  return { fase: { tipo: "pronto" }, codice: null };
}

/**
 * R10 (D08): accesso senza SPID per i clienti privati, se il conservatore lo abilita
 * (GET /api/v1/auth/metodi): password e codice a tempo (o codice di recupero) e passkey.
 * Gli errori sono gli stessi per e-mail sconosciuta e password errata (nessuna enumerazione).
 */
function AccessoLocale({ onEntrato }: { onEntrato: () => void }) {
  const { login } = useAuth();
  const [metodi, setMetodi] = useState<string[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passaggio, setPassaggio] = useState<{ passaggio: string; enti?: { tenant_id: string; ragione_sociale: string }[] } | null>(null);
  const [ente, setEnte] = useState("");
  const [codice, setCodice] = useState("");
  const [recupero, setRecupero] = useState(false);
  const [errore, setErrore] = useState("");
  const [occupato, setOccupato] = useState(false);
  useEffect(() => {
    chiamaPubblica<{ metodi: string[] }>("/v1/auth/metodi", undefined, "GET").then(r => setMetodi(r.metodi ?? [])).catch(() => setMetodi([]));
  }, []);
  const esegui = async (f: () => Promise<void>) => {
    setErrore(""); setOccupato(true);
    try { await f(); } catch (e) {
      const err = e instanceof ErroreRichiesta ? e : null;
      setErrore(err?.codice === "ACC-401" ? "Indirizzo e-mail, password o codice non corretti, oppure accesso bloccato per qualche minuto dopo troppi tentativi."
        : err?.codice === "ACC-403-METODO" ? "Il tuo ente non ammette questo metodo d'accesso: usa SPID o CIE, o chiedi all'amministratore dell'ente."
        : err?.codice === "ACC-400-PASSAGGIO" ? "L'accesso è scaduto: ricomincia."
        : err?.codice === "ACC-429" ? "Troppi tentativi: aspetta qualche minuto."
        : err?.dettaglio || "Accesso non riuscito: riprova.");
      if (err?.codice === "ACC-400-PASSAGGIO") setPassaggio(null);
    } finally { setOccupato(false); }
  };
  const entra = (r: any) => { const s = sessioneDaRisposta(r?.utente); if (s) { login(s); onEntrato(); } };
  const primoPasso = () => esegui(async () => {
    setPassaggio(await chiamaPubblica("/v1/auth/locale/accesso", { email: email.trim(), password }));
    setPassword("");
  });
  const secondoPasso = () => esegui(async () => {
    entra(await chiamaPubblica("/v1/auth/locale/secondo-fattore", {
      passaggio: passaggio!.passaggio, ...(recupero ? { codice_recupero: codice } : { codice }),
      ...(ente ? { tenant_id: ente } : {}) }));
  });
  const passkey = () => esegui(async () => {
    const o = await chiamaPubblica<{ passaggio: string; opzioni: unknown }>("/v1/auth/passkey/opzioni");
    entra(await chiamaPubblica("/v1/auth/passkey/accesso", { passaggio: o.passaggio, credenziale: await firmaPasskey(o.opzioni) }));
  });
  const conPassword = metodi.includes("PASSWORD_TOTP");
  const conPasskey = metodi.includes("PASSKEY");
  if (!conPassword && !conPasskey) return null;
  return (
    <section aria-labelledby="vd-locale" className="vd-pila">
      <div className="vd-sezione-testa"><h2 id="vd-locale" className="vd-etichetta">Accesso senza SPID</h2></div>
      {errore && <div className="vd-esito vd-esito--critico" role="alert"><p className="vd-esito-titolo">Accesso non riuscito</p><p>{errore}</p></div>}
      {conPasskey && <div className="vd-comandi"><button type="button" className="vd-bottone vd-bottone--secondario" disabled={occupato} onClick={() => void passkey()}>Entra con una passkey</button></div>}
      {conPassword && (!passaggio ? (
        <form className="vd-modulo" onSubmit={e => { e.preventDefault(); void primoPasso(); }}>
          <div className="vd-campo">
            <label htmlFor="vd-loc-email">Indirizzo e-mail</label>
            <input id="vd-loc-email" type="email" autoComplete="username" className="vd-input" value={email} onChange={e => setEmail(e.target.value)} />
          </div>
          <div className="vd-campo">
            <label htmlFor="vd-loc-pw">Password</label>
            <input id="vd-loc-pw" type="password" autoComplete="current-password" className="vd-input" value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={occupato || !email || !password}>Continua</button></div>
        </form>
      ) : (
        <form className="vd-modulo" onSubmit={e => { e.preventDefault(); void secondoPasso(); }}>
          {passaggio.enti && (
            <div className="vd-campo">
              <label htmlFor="vd-loc-ente">Ente</label>
              <select id="vd-loc-ente" className="vd-input" value={ente} onChange={e => setEnte(e.target.value)}>
                <option value="">Scegli l'ente</option>
                {passaggio.enti.map(x => <option key={x.tenant_id} value={x.tenant_id}>{x.ragione_sociale}</option>)}
              </select>
            </div>
          )}
          <div className="vd-campo">
            <label htmlFor="vd-loc-codice">{recupero ? "Codice di recupero" : "Codice a sei cifre dell'app di autenticazione"}</label>
            <input id="vd-loc-codice" className="vd-input vd-mono" autoComplete="one-time-code" inputMode={recupero ? "text" : "numeric"} value={codice} onChange={e => setCodice(e.target.value)} />
          </div>
          <div className="vd-comandi">
            <button type="submit" className="vd-bottone" disabled={occupato || !codice || (!!passaggio.enti && !ente)}>Entra</button>
            <button type="button" className="vd-comando-testo" onClick={() => { setRecupero(!recupero); setCodice(""); }}>{recupero ? "Usa il codice dell'app" : "Ho perso il telefono: usa un codice di recupero"}</button>
          </div>
        </form>
      ))}
    </section>
  );
}

export default function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [iniziale] = useState(faseIniziale);
  const [fase, setFase] = useState<Fase>(iniziale.fase);
  const [enteScelto, setEnteScelto] = useState("");
  const [partenza, setPartenza] = useState<ProviderIdentita | null>(null);
  const esitoRef = useRef<HTMLDivElement>(null);
  const avviato = useRef(false);

  const scambia = async (codice: string, tenantId?: string) => {
    setFase({ tipo: "verifica" });
    try {
      const esito = await scambiaCodiceIdentita(codice, tenantId);
      if (esito.esito === "accesso") {
        login(esito.sessione);
        navigate("/", { replace: true });   // R07 (D31): pagina iniziale delle preferenze
        return;
      }
      setEnteScelto("");
      setFase({ tipo: "scelta", codice: esito.codice, enti: esito.enti, scadenzaSecondi: esito.scadenzaSecondi });
    } catch (e) {
      const err = e instanceof ErroreAccesso ? e : new ErroreAccesso("RETE");
      // Dopo la scelta dell'ente, SPID-008 vuol dire «ente non tuo», non «nessun utente»
      const codice = tenantId && err.codice === "SPID-008" ? "SPID-008-ENTE" : err.codice;
      setFase({ tipo: "errore", codice, dettaglio: err.dettaglio });
    }
  };

  // Ritorno dal gestore: frammento tolto subito, codice scambiato una volta sola
  // (il ref evita il doppio scambio dello StrictMode di sviluppo).
  useEffect(() => {
    if (avviato.current) return;
    avviato.current = true;
    pulisciRitornoIdentita();
    if (iniziale.codice) void scambia(iniziale.codice);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Un esito o una scelta ricevono il fuoco: chi usa un lettore di schermo li trova subito.
  useEffect(() => {
    if (fase.tipo === "errore" || fase.tipo === "scelta") esitoRef.current?.focus();
  }, [fase.tipo]);

  const parti = (provider: ProviderIdentita) => {
    setPartenza(provider);
    avviaAccessoIdentita(provider);
  };

  const occupato = fase.tipo === "verifica" || partenza !== null;
  const errore = fase.tipo === "errore" ? spiegazione(fase.codice, fase.dettaglio) : null;

  return (
    <div className="vd-accesso-pagina">
      <aside className="vd-accesso-identita" aria-label="VerumDoc">
        <p className="vd-marchio">VerumDoc</p>
        <span className="vd-accesso-fascia">Servizio di conservazione digitale</span>
        <p className="vd-accesso-motto">L'archivio dice sempre la verità sul proprio stato.</p>
        <ul className="vd-accesso-orario">
          <li><strong>Versare</strong><span>Ogni pacchetto viene controllato e ha un esito, con il motivo di ogni rifiuto.</span></li>
          <li><strong>Verificare</strong><span>La catena delle impronte mostra se l'archivio è integro, o perché non lo è.</span></li>
          <li><strong>Consultare</strong><span>I pacchetti conservati si cercano per testo, classe, anno e stato.</span></li>
        </ul>
        <p className="vd-accesso-piede">Accesso riservato alle persone registrate dagli enti e dal conservatore.</p>
      </aside>
      <main className="vd-accesso-corpo">
        <div className="vd-accesso">
          <div className="vd-accesso-testa">
            <h1>Accedi al servizio di conservazione</h1>
            <p className="vd-nota" style={{ fontSize: 15 }}>
              Per il responsabile del servizio, per gli enti che conservano i loro documenti e per chi versa.
            </p>
          </div>

          <section aria-labelledby="vd-spid" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="vd-sezione-testa" style={{ marginBottom: 0 }}>
              <h2 id="vd-spid" className="vd-etichetta">Accesso con identità digitale</h2>
            </div>

            {fase.tipo === "verifica" ? (
              <p className="vd-nota" role="status" style={{ fontSize: 16 }}>Verifica dell'accesso in corso…</p>
            ) : fase.tipo === "scelta" ? (
              <div ref={esitoRef} tabIndex={-1} className="vd-esito vd-esito--info" role="region"
                   aria-labelledby="vd-scelta-titolo" style={{ outline: "none" }}>
                <p id="vd-scelta-titolo" className="vd-esito-titolo">Per quale ente vuoi entrare?</p>
                <p>Sei registrato in più enti. Scegli quello per cui operi adesso; per cambiarlo dovrai uscire e accedere di nuovo.
                  La scelta va fatta entro {Math.round(fase.scadenzaSecondi / 60) || 2} minuti.</p>
                <form onSubmit={e => { e.preventDefault(); if (enteScelto) void scambia(fase.codice, enteScelto); }}
                      style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                  <fieldset className="vd-scelte">
                    <legend>Ente</legend>
                    {fase.enti.map(ente => (
                      <label key={ente.tenant_id} className="vd-scelta">
                        <input type="radio" name="vd-ente" value={ente.tenant_id}
                               checked={enteScelto === ente.tenant_id}
                               onChange={() => setEnteScelto(ente.tenant_id)} />
                        <strong>{ente.ragione_sociale}</strong>
                        <span>{NOME_RUOLO[ente.ruolo] ?? ente.ruolo}</span>
                      </label>
                    ))}
                  </fieldset>
                  <div className="vd-comandi">
                    <button type="submit" className="vd-bottone" disabled={!enteScelto}>Entra per l'ente scelto</button>
                    <button type="button" className="vd-comando-testo" onClick={() => setFase({ tipo: "pronto" })}>Annulla</button>
                  </div>
                </form>
              </div>
            ) : (
              <>
                {errore && fase.tipo === "errore" && (
                  <div ref={esitoRef} tabIndex={-1} className="vd-esito vd-esito--critico" role="alert" style={{ outline: "none" }}>
                    <p className="vd-esito-titolo">{errore.titolo}</p>
                    <p>{errore.testo}</p>
                    <p><strong>Cosa fare.</strong> {errore.cosaFare}</p>
                    <p className="vd-nota">
                      Codice dell'errore: <span className="vd-mono">{fase.codice.replace(/-ENTE$/, "")}</span>
                      {/riferimento per la segnalazione/.test(fase.dettaglio) && ERRORI[fase.codice]
                        ? <> · {fase.dettaglio.slice(fase.dettaglio.indexOf("riferimento")).replace(/\)$/, "")}</> : null}
                    </p>
                  </div>
                )}
                <p style={{ fontSize: 17, maxWidth: "62ch" }}>
                  Entri con la tua identità digitale personale. L'amministrazione del tuo ente deve averti registrato con il tuo codice fiscale.
                </p>
                <div className="vd-comandi">
                  <button type="button" className="vd-bottone" disabled={occupato} onClick={() => parti("spid")}>
                    {partenza === "spid" ? "Collegamento a SPID…" : "Entra con SPID"}
                  </button>
                  <button type="button" className="vd-bottone vd-bottone--secondario" disabled={occupato} onClick={() => parti("cie")}>
                    {partenza === "cie" ? "Collegamento a CIE…" : "Entra con CIE"}
                  </button>
                </div>
              </>
            )}
            <p className="vd-nota" style={{ maxWidth: "62ch" }}>
              I sistemi versanti (protocollo, contabilità) non passano da questa pagina: versano con la propria chiave API.
            </p>
          </section>

          <AccessoLocale onEntrato={() => navigate("/", { replace: true })} />

          {AccessoSviluppo && <AccessoSviluppo onEntrato={() => navigate("/")} />}
        </div>
      </main>
    </div>
  );
}
