// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Attivazione guidata dall'invito (R10, D08; gateway /api/v1/auth/inviti/*). Pagina pubblica.
 * Il collegamento dell'e-mail porta il codice nel frammento (#invito=…): non arriva ai server
 * né ai log, e qui si toglie subito dall'indirizzo. Passi:
 *   1. la persona scrive l'indirizzo e-mail a cui ha ricevuto l'invito (il codice da solo non basta);
 *   2. sceglie come accedere fra i metodi ammessi dall'ente;
 *   3. password e codice a tempo (con i codici di recupero da conservare), oppure passkey,
 *      oppure SPID/CIE (l'account si attiva e si entra dalla pagina d'accesso).
 * Ogni errore dice cosa fare; un passaggio scaduto si ricomincia riaprendo il collegamento.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, sessioneDaRisposta } from "../auth/AuthContext";
import { ErroreRichiesta, NOMI_METODI, chiamaPubblica, creaPasskey } from "../console/accesso";

interface Verifica { passaggio: string; ente: string; nome: string; metodi: string[]; password_lunghezza_minima: number }

function messaggio(e: unknown): string {
  if (!(e instanceof ErroreRichiesta)) return "Qualcosa non ha funzionato: riprova.";
  if (e.codice === "ACC-404-INVITO") return "Il collegamento non è valido, è scaduto o l'indirizzo e-mail non corrisponde. Chiedi un nuovo invito all'amministratore dell'ente.";
  if (e.codice === "ACC-400-PASSAGGIO") return "La procedura è scaduta o è stata aperta in un altro browser: riapri il collegamento dell'e-mail.";
  if (e.codice === "ACC-422-CODICE") return "Il codice a sei cifre non corrisponde: controlla l'ora del telefono e inserisci il codice attuale.";
  if (e.codice === "ACC-429") return "Troppi tentativi: aspetta qualche minuto e riprova.";
  return e.dettaglio || "Operazione non riuscita: riprova.";
}

export default function AttivaPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("invito") ?? "");
  const [email, setEmail] = useState("");
  const [v, setV] = useState<Verifica | null>(null);
  const [metodo, setMetodo] = useState("");
  const [totp, setTotp] = useState<{ segreto: string; uri: string } | null>(null);
  const [password, setPassword] = useState("");
  const [codice, setCodice] = useState("");
  const [codici, setCodici] = useState<string[] | null>(null);
  const [errore, setErrore] = useState("");
  const [fatto, setFatto] = useState("");
  const [occupato, setOccupato] = useState(false);

  useEffect(() => { if (window.location.hash) window.history.replaceState(null, "", window.location.pathname); }, []);

  const esegui = async (f: () => Promise<void>) => {
    setErrore(""); setOccupato(true);
    try { await f(); } catch (e) { setErrore(messaggio(e)); } finally { setOccupato(false); }
  };
  const verifica = () => esegui(async () => {
    const r = await chiamaPubblica<Verifica>("/v1/auth/inviti/verifica", { token, email: email.trim().toLowerCase() });
    setV(r); setMetodo(r.metodi[0] ?? "");
  });
  const preparaTotp = () => esegui(async () => {
    setTotp(await chiamaPubblica<{ segreto: string; uri: string }>("/v1/auth/inviti/totp", { passaggio: v!.passaggio }));
  });
  const entra = (r: any) => {
    const s = sessioneDaRisposta(r?.utente);
    if (s) login(s);
  };
  const completa = () => esegui(async () => {
    let corpo: Record<string, unknown> = { passaggio: v!.passaggio, metodo };
    if (metodo === "PASSWORD_TOTP") corpo = { ...corpo, password, codice };
    if (metodo === "PASSKEY") {
      const o = await chiamaPubblica<{ opzioni: unknown }>("/v1/auth/inviti/passkey/opzioni", { passaggio: v!.passaggio });
      corpo = { ...corpo, credenziale: await creaPasskey(o.opzioni) };
    }
    const r: any = await chiamaPubblica("/v1/auth/inviti/completa", corpo);
    if (r.codici_recupero?.length) { setCodici(r.codici_recupero); entra(r); return; }
    if (r.esito === "attivato_e_accesso") { entra(r); navigate("/", { replace: true }); return; }
    setFatto(r.messaggio ?? "Account attivato.");
  });

  return (
    <div className="vd-pagina-accesso">
      <main className="vd-pagina">
        <div className="vd-accesso">
          <p className="vd-marchio">VerumDoc</p>
          <h1>Attiva il tuo accesso</h1>
          {errore && <div className="vd-esito vd-esito--critico" role="alert"><p className="vd-esito-titolo">Non è andata</p><p>{errore}</p></div>}
          {!token ? <p className="vd-nota">Apri il collegamento che hai ricevuto per e-mail: questa pagina funziona solo da lì.</p>
            : codici ? (
              <section aria-labelledby="vd-codici" className="vd-pila">
                <h2 id="vd-codici" className="vd-etichetta">Conserva i codici di recupero</h2>
                <p>Ognuno vale una volta, al posto del codice a tempo, se perdi il telefono. Non li mostreremo più.</p>
                <ul className="vd-elenco vd-mono">{codici.map(c => <li key={c}><span>{c}</span></li>)}</ul>
                <div className="vd-comandi"><button type="button" className="vd-bottone" onClick={() => navigate("/", { replace: true })}>Li ho conservati: entra</button></div>
              </section>
            ) : fatto ? <div className="vd-esito vd-esito--info" role="status"><p className="vd-esito-titolo">{fatto}</p></div>
            : !v ? (
              <form className="vd-modulo" onSubmit={e => { e.preventDefault(); void verifica(); }}>
                <div className="vd-campo">
                  <label htmlFor="vd-att-email">Indirizzo e-mail a cui hai ricevuto l'invito</label>
                  <input id="vd-att-email" type="email" className="vd-input" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} />
                </div>
                <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={occupato || !email.includes("@")}>Continua</button></div>
              </form>
            ) : (
              <form className="vd-modulo" onSubmit={e => { e.preventDefault(); void completa(); }}>
                <p>{v.nome ? `Ciao ${v.nome}: ` : ""}{v.ente} ti ha invitato. Scegli come accedere.</p>
                {v.metodi.length === 0 ? <p className="vd-nota">L'ente non ha ancora un metodo d'accesso disponibile: avvisa l'amministratore.</p> : (
                  <fieldset className="vd-scelte">
                    <legend>Metodo d'accesso</legend>
                    {v.metodi.map(m => (
                      <label key={m} className="vd-scelta"><input type="radio" name="metodo" value={m} checked={metodo === m} onChange={() => setMetodo(m)} /><span>{NOMI_METODI[m] ?? m}</span></label>
                    ))}
                  </fieldset>
                )}
                {metodo === "PASSWORD_TOTP" && (
                  <>
                    <div className="vd-campo">
                      <label htmlFor="vd-att-pw">Scegli una password</label>
                      <input id="vd-att-pw" type="password" className="vd-input" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} />
                      <span className="vd-aiuto">Almeno {v.password_lunghezza_minima} caratteri: una frase lunga è più sicura di una parola complicata.</span>
                    </div>
                    {!totp ? <div className="vd-comandi"><button type="button" className="vd-bottone vd-bottone--secondario" disabled={occupato} onClick={() => void preparaTotp()}>Prepara il codice a tempo</button></div> : (
                      <>
                        <p>Aggiungi VerumDoc all'app di autenticazione del telefono con questa chiave:</p>
                        <p className="vd-mono vd-a-capo" aria-label="Chiave del codice a tempo">{totp.segreto}</p>
                        <div className="vd-campo">
                          <label htmlFor="vd-att-codice">Codice a sei cifre mostrato dall'app</label>
                          <input id="vd-att-codice" inputMode="numeric" autoComplete="one-time-code" className="vd-input vd-mono" maxLength={6} value={codice} onChange={e => setCodice(e.target.value.replace(/\D/g, ""))} />
                        </div>
                      </>
                    )}
                  </>
                )}
                {metodo === "PASSKEY" && <p className="vd-nota">Il dispositivo ti chiederà di creare una passkey (impronta, volto o PIN).</p>}
                {metodo === "SPID_CIE" && <p className="vd-nota">L'account si attiva subito; poi entra con SPID o CIE dalla pagina d'accesso.</p>}
                <div className="vd-comandi">
                  <button type="submit" className="vd-bottone" disabled={occupato || !metodo || (metodo === "PASSWORD_TOTP" && (!totp || codice.length !== 6 || password.length < v.password_lunghezza_minima))}>
                    {occupato ? "Attivazione…" : "Attiva l'accesso"}
                  </button>
                </div>
              </form>
            )}
        </div>
      </main>
    </div>
  );
}
