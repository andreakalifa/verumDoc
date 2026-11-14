// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Le mie credenziali (R12b, VD-C-156; gateway /api/v1/auth/credenziali).
 * La persona gestisce i propri modi di accedere con i metodi che l'ente ammette:
 *   - passkey: aggiungerne una (per esempio di riserva), cambiarle nome, revocarla — mai l'ultima
 *     con cui si può ancora accedere;
 *   - password: cambiarla indicando quella attuale;
 *   - codice a tempo: sostituirlo con uno nuovo (il segreto si vede una sola volta);
 *   - codici di recupero: generarne di nuovi (si vedono una sola volta, i vecchi non valgono più).
 * Ogni modifica chiede prima di confermare l'identità; la conferma vale per questa sessione per
 * pochi minuti. Dopo il cambio della password, del codice a tempo o la revoca di una passkey le
 * altre sessioni aperte si chiudono; a ogni modifica arriva un'e-mail di avviso.
 * Con solo SPID o CIE la pagina lo dice e non offre comandi.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { leggiErrore } from "../api/errori";
import { EsitoCritico } from "../components/EsitoCritico";
import { creaPasskey, firmaPasskey, NOMI_METODI } from "../console/accesso";
import { InLettura, quandoBreve } from "../console/comuni";
import {
  aggiungiPasskey, cambiaPassword, confermaConPasskey, confermaConPassword, confermaTotp, leggiCredenziali,
  nuoviCodiciRecupero, nuovoTotp, opzioniConfermaPasskey, opzioniNuovaPasskey, revocaPasskey, rinominaPasskey,
  type StatoCredenziali,
} from "../console/credenziali";

const CHIAVE = ["mie-credenziali"];

/** Messaggio in parole per gli errori di questa pagina (gli altri: EsitoCritico). */
function inParole(e: unknown): string {
  if (typeof DOMException !== "undefined" && e instanceof DOMException) {
    return e.name === "NotAllowedError" ? "Operazione annullata sul dispositivo o tempo scaduto: riprova."
      : "Il browser non è riuscito a usare la passkey: riprova o usa un altro dispositivo.";
  }
  const l = leggiErrore(e);
  switch (l.codice) {
    case "ACC-422-RIAUTENTICAZIONE": return l.testo && l.testo !== "Credenziali non corrette" ? l.testo : "Password, codice o passkey non corretti.";
    case "ACC-403-RIAUTENTICAZIONE": return "La conferma dell'identità è scaduta: confermala di nuovo.";
    case "ACC-409-ULTIMA": return "È l'ultima credenziale con cui puoi accedere: aggiungi prima un'altra passkey.";
    case "ACC-422-PASSWORD": return l.testo || "La password non rispetta le regole.";
    case "ACC-422-CODICE": return "Il codice a sei cifre non corrisponde: controlla l'ora del telefono e usa il codice attuale.";
    case "ACC-400-PASSKEY": return "La passkey non è stata accettata: riprova.";
    case "ACC-400-PASSAGGIO": return "La procedura è scaduta: ricomincia.";
    case "ACC-429": return "Troppi tentativi: aspetta qualche minuto e riprova.";
    default: return l.testo || "Operazione non riuscita: riprova.";
  }
}

function Esito({ errore, fatto }: { errore?: unknown; fatto?: string | null }) {
  if (errore) return <div className="vd-esito vd-esito--critico" role="alert"><p className="vd-esito-titolo">Non è andata</p><p>{inParole(errore)}</p></div>;
  if (fatto) return <p className="vd-nota" role="status">{fatto}</p>;
  return null;
}

function altreSessioni(n: number): string {
  return n > 0 ? ` Abbiamo chiuso ${n === 1 ? "l'altra sessione aperta" : `le altre ${n} sessioni aperte`}.` : "";
}

export default function CredenzialiPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: CHIAVE, queryFn: leggiCredenziali });
  const ricarica = () => qc.invalidateQueries({ queryKey: CHIAVE });
  const s = q.data;
  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Le mie credenziali</h1>
        <p>I modi con cui accedi a VerumDoc. Per ogni modifica ti chiediamo di confermare chi sei; a ogni modifica ricevi un'e-mail di avviso.</p>
      </div>
      {q.isLoading ? <InLettura testo="Lettura delle credenziali…" />
        : q.isError || !s ? <EsitoCritico titolo="Credenziali non disponibili" errore={q.error} onRiprova={() => q.refetch()} />
        : !s.disponibile ? (
          <div className="vd-avviso vd-avviso--neutro" role="note">
            <p>{s.motivo}</p>
            {s.metodi_ente.length > 0 && <p className="vd-nota">Metodi ammessi dall'ente: {s.metodi_ente.map(m => NOMI_METODI[m] ?? m).join(", ")}.</p>}
          </div>
        ) : (
          <div className="vd-pila">
            <Conferma s={s} onFatto={ricarica} />
            {s.metodi_locali.includes("PASSKEY") && <Passkey s={s} onFatto={ricarica} />}
            {s.metodi_locali.includes("PASSWORD_TOTP") && s.password.impostata && <Password s={s} onFatto={ricarica} />}
            {s.metodi_locali.includes("PASSWORD_TOTP") && s.password.impostata && <CodiceATempo s={s} onFatto={ricarica} />}
            {s.metodi_locali.includes("PASSWORD_TOTP") && s.totp.attivo && <CodiciRecupero s={s} onFatto={ricarica} />}
          </div>
        )}
    </>
  );
}

type Prop = { s: StatoCredenziali; onFatto: () => void };
const confermata = (s: StatoCredenziali) => (s.riautenticazione.valida_per_secondi ?? 0) > 0;

function Conferma({ s, onFatto }: Prop) {
  const [password, setPassword] = useState("");
  const [codice, setCodice] = useState("");
  const [recupero, setRecupero] = useState(false);
  const [scade, setScade] = useState<number | null>(null);
  const [adesso, setAdesso] = useState(() => Date.now());
  useEffect(() => {
    setScade(s.riautenticazione.valida_per_secondi ? Date.now() + s.riautenticazione.valida_per_secondi * 1000 : null);
  }, [s.riautenticazione.valida_per_secondi]);
  useEffect(() => {
    if (!scade) return;
    const t = window.setInterval(() => {
      setAdesso(Date.now());
      if (Date.now() >= scade) { window.clearInterval(t); onFatto(); }
    }, 1000);
    return () => window.clearInterval(t);
  }, [scade, onFatto]);
  const conPassword = useMutation({
    mutationFn: () => confermaConPassword(password, recupero ? { codice_recupero: codice } : { codice }),
    onSuccess: () => { setPassword(""); setCodice(""); onFatto(); },
  });
  const conPasskey = useMutation({
    mutationFn: async () => confermaConPasskey(await firmaPasskey((await opzioniConfermaPasskey()).opzioni)),
    onSuccess: onFatto,
  });
  const metodi = s.riautenticazione.metodi;
  const rimasti = scade ? Math.max(0, Math.ceil((scade - adesso) / 1000)) : 0;
  return (
    <section aria-labelledby="vd-cred-conferma">
      <div className="vd-sezione-testa"><h2 id="vd-cred-conferma" className="vd-etichetta">Conferma la tua identità</h2></div>
      {confermata(s) && rimasti > 0 ? (
        <div className="vd-esito vd-esito--ok" role="status">
          <p className="vd-esito-titolo">Identità confermata</p>
          <p>Puoi modificare le credenziali per {Math.floor(rimasti / 60)}:{String(rimasti % 60).padStart(2, "0")} minuti, solo in questa finestra del browser.</p>
        </div>
      ) : metodi.length === 0 ? (
        <p className="vd-nota">Non hai una credenziale con cui confermare l'identità tra i metodi ammessi dall'ente: chiedi all'amministratore un nuovo invito.</p>
      ) : (
        <>
          <p className="vd-nota">La conferma vale {s.riautenticazione.finestra_minuti} minuti, solo per questa sessione.</p>
          {metodi.includes("PASSWORD_TOTP") && (
            <form className="vd-modulo" onSubmit={e => { e.preventDefault(); conPassword.mutate(); }}>
              <div className="vd-campo">
                <label htmlFor="vd-cred-conf-pw">Password attuale</label>
                <input id="vd-cred-conf-pw" type="password" className="vd-input" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} />
              </div>
              <div className="vd-campo">
                <label htmlFor="vd-cred-conf-codice">{recupero ? "Codice di recupero" : "Codice a sei cifre dell'app"}</label>
                <input id="vd-cred-conf-codice" className="vd-input vd-mono" autoComplete="one-time-code" inputMode={recupero ? "text" : "numeric"}
                       maxLength={recupero ? 19 : 6} value={codice}
                       onChange={e => setCodice(recupero ? e.target.value.toUpperCase() : e.target.value.replace(/\D/g, ""))} />
                <button type="button" className="vd-comando-testo" onClick={() => { setRecupero(!recupero); setCodice(""); }}>
                  {recupero ? "Uso il codice a tempo" : "Non ho il telefono: uso un codice di recupero"}
                </button>
              </div>
              <div className="vd-comandi">
                <button type="submit" className="vd-bottone" disabled={conPassword.isPending || !password || (recupero ? codice.length < 16 : codice.length !== 6)}>
                  {conPassword.isPending ? "Verifica…" : "Conferma con password e codice"}
                </button>
              </div>
              <Esito errore={conPassword.error} />
            </form>
          )}
          {metodi.includes("PASSKEY") && (
            <div className="vd-comandi">
              <button type="button" className={metodi.includes("PASSWORD_TOTP") ? "vd-bottone vd-bottone--secondario" : "vd-bottone"}
                      disabled={conPasskey.isPending} onClick={() => conPasskey.mutate()}>
                {conPasskey.isPending ? "In attesa del dispositivo…" : "Conferma con una passkey"}
              </button>
              <Esito errore={conPasskey.error} />
            </div>
          )}
        </>
      )}
    </section>
  );
}

function SenzaConferma() {
  return <p className="vd-nota">Conferma la tua identità qui sopra per modificare.</p>;
}

function Passkey({ s, onFatto }: Prop) {
  const [nome, setNome] = useState("");
  const [inModifica, setInModifica] = useState<string | null>(null);
  const [nuovoNome, setNuovoNome] = useState("");
  const [daRevocare, setDaRevocare] = useState<string | null>(null);
  const [fatto, setFatto] = useState<string | null>(null);
  const aggiungi = useMutation({
    mutationFn: async () => aggiungiPasskey(await creaPasskey((await opzioniNuovaPasskey()).opzioni), nome.trim() || "Passkey"),
    onSuccess: p => { setNome(""); setFatto(`Passkey «${p.nome}» aggiunta.`); onFatto(); },
  });
  const rinomina = useMutation({
    mutationFn: (id: string) => rinominaPasskey(id, nuovoNome.trim()),
    onSuccess: () => { setInModifica(null); setFatto("Nome della passkey cambiato."); onFatto(); },
  });
  const revoca = useMutation({
    mutationFn: (id: string) => revocaPasskey(id),
    onSuccess: r => { setDaRevocare(null); setFatto(`Passkey revocata.${altreSessioni(r.altre_sessioni_chiuse)}`); onFatto(); },
  });
  const attiva = confermata(s);
  const unica = s.passkey.length <= 1 && !s.spid_cie && !(s.metodi_locali.includes("PASSWORD_TOTP") && s.password.impostata && s.totp.attivo);
  return (
    <section aria-labelledby="vd-cred-passkey">
      <div className="vd-sezione-testa"><h2 id="vd-cred-passkey" className="vd-etichetta">Passkey</h2></div>
      {s.passkey.length === 0 ? <p className="vd-nota">Non hai passkey registrate.</p> : (
        <div className="vd-scorri">
          <table className="vd-tabellone vd-impila">
            <caption className="vd-sr">Le tue passkey</caption>
            <thead><tr><th scope="col">Nome</th><th scope="col">Aggiunta</th><th scope="col">Ultimo uso</th><th scope="col"><span className="vd-sr">Comandi</span></th></tr></thead>
            <tbody>
              {s.passkey.map(k => (
                <tr key={k.id}>
                  <td data-label="Nome">
                    {inModifica === k.id ? (
                      <form onSubmit={e => { e.preventDefault(); rinomina.mutate(k.id); }} className="vd-campo">
                        <label htmlFor={`vd-pk-nome-${k.id}`} className="vd-sr">Nuovo nome della passkey</label>
                        <input id={`vd-pk-nome-${k.id}`} className="vd-input" maxLength={100} value={nuovoNome} onChange={e => setNuovoNome(e.target.value)} />
                        <span className="vd-comandi">
                          <button type="submit" className="vd-bottone" disabled={!nuovoNome.trim() || rinomina.isPending}>Salva il nome</button>
                          <button type="button" className="vd-comando-testo" onClick={() => setInModifica(null)}>Annulla</button>
                        </span>
                      </form>
                    ) : k.nome}
                  </td>
                  <td data-label="Aggiunta">{quandoBreve(k.creata_il) ?? "—"}</td>
                  <td data-label="Ultimo uso">{quandoBreve(k.ultimo_uso) ?? "Mai usata"}</td>
                  <td data-label="Comandi" className="vd-comandi-cella">
                    {daRevocare === k.id ? (
                      <span className="vd-comandi">
                        <button type="button" className="vd-bottone" disabled={revoca.isPending} onClick={() => revoca.mutate(k.id)}>Sì, revoca «{k.nome}»</button>
                        <button type="button" className="vd-comando-testo" onClick={() => setDaRevocare(null)}>Annulla</button>
                      </span>
                    ) : (
                      <span className="vd-comandi">
                        <button type="button" className="vd-comando-testo" disabled={!attiva} onClick={() => { setInModifica(k.id); setNuovoNome(k.nome); }}>Rinomina</button>
                        <button type="button" className="vd-comando-testo" disabled={!attiva || unica}
                                title={unica ? "È l'ultima credenziale con cui puoi accedere" : undefined}
                                onClick={() => setDaRevocare(k.id)}>Revoca</button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {unica && s.passkey.length === 1 && <p className="vd-nota">È l'unica credenziale con cui accedi: aggiungi una seconda passkey (per esempio su un altro dispositivo) prima di poterla revocare.</p>}
      {!attiva ? <SenzaConferma /> : (
        <form className="vd-modulo" onSubmit={e => { e.preventDefault(); aggiungi.mutate(); }}>
          <div className="vd-campo">
            <label htmlFor="vd-cred-pk-nome">Nome della nuova passkey</label>
            <input id="vd-cred-pk-nome" className="vd-input" maxLength={100} placeholder="Per esempio: telefono personale" value={nome} onChange={e => setNome(e.target.value)} />
            <span className="vd-aiuto">Il dispositivo ti chiederà l'impronta, il volto o il PIN.</span>
          </div>
          <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={aggiungi.isPending}>{aggiungi.isPending ? "In attesa del dispositivo…" : "Aggiungi una passkey"}</button></div>
        </form>
      )}
      <Esito errore={aggiungi.error || rinomina.error || revoca.error} fatto={fatto} />
    </section>
  );
}

function Password({ s, onFatto }: Prop) {
  const [attuale, setAttuale] = useState("");
  const [nuova, setNuova] = useState("");
  const [ripeti, setRipeti] = useState("");
  const [fatto, setFatto] = useState<string | null>(null);
  const cambia = useMutation({
    mutationFn: () => cambiaPassword(attuale, nuova),
    onSuccess: r => { setAttuale(""); setNuova(""); setRipeti(""); setFatto(`Password cambiata.${altreSessioni(r.altre_sessioni_chiuse)}`); onFatto(); },
  });
  const minimo = s.password_lunghezza_minima;
  const diverse = ripeti.length > 0 && ripeti !== nuova;
  return (
    <section aria-labelledby="vd-cred-password">
      <div className="vd-sezione-testa">
        <h2 id="vd-cred-password" className="vd-etichetta">Password</h2>
        {s.password.impostata_il && <span className="vd-nota">Impostata il {quandoBreve(s.password.impostata_il)}</span>}
      </div>
      {!confermata(s) ? <SenzaConferma /> : (
        <form className="vd-modulo" onSubmit={e => { e.preventDefault(); cambia.mutate(); }}>
          <div className="vd-campo">
            <label htmlFor="vd-cred-pw-attuale">Password attuale</label>
            <input id="vd-cred-pw-attuale" type="password" className="vd-input" autoComplete="current-password" value={attuale} onChange={e => setAttuale(e.target.value)} />
          </div>
          <div className="vd-campo">
            <label htmlFor="vd-cred-pw-nuova">Nuova password</label>
            <input id="vd-cred-pw-nuova" type="password" className="vd-input" autoComplete="new-password" aria-describedby="vd-cred-pw-aiuto" value={nuova} onChange={e => setNuova(e.target.value)} />
            <span id="vd-cred-pw-aiuto" className="vd-aiuto">Almeno {minimo} caratteri: una frase lunga è più sicura di una parola complicata.</span>
          </div>
          <div className="vd-campo">
            <label htmlFor="vd-cred-pw-ripeti">Ripeti la nuova password</label>
            <input id="vd-cred-pw-ripeti" type="password" className="vd-input" autoComplete="new-password" aria-invalid={diverse || undefined}
                   aria-describedby={diverse ? "vd-cred-pw-diverse" : undefined} value={ripeti} onChange={e => setRipeti(e.target.value)} />
            {diverse && <span id="vd-cred-pw-diverse" className="vd-aiuto">Le due password non coincidono.</span>}
          </div>
          <p className="vd-nota">Le altre sessioni aperte con il tuo account si chiuderanno.</p>
          <div className="vd-comandi">
            <button type="submit" className="vd-bottone" disabled={cambia.isPending || !attuale || nuova.length < minimo || nuova !== ripeti}>
              {cambia.isPending ? "Salvataggio…" : "Cambia la password"}
            </button>
          </div>
        </form>
      )}
      <Esito errore={cambia.error} fatto={fatto} />
    </section>
  );
}

function CodiceATempo({ s, onFatto }: Prop) {
  const [segreto, setSegreto] = useState<{ segreto: string; uri: string } | null>(null);
  const [codice, setCodice] = useState("");
  const [fatto, setFatto] = useState<string | null>(null);
  const genera = useMutation({ mutationFn: nuovoTotp, onSuccess: r => { setSegreto(r); setFatto(null); } });
  const attiva = useMutation({
    mutationFn: () => confermaTotp(codice),
    onSuccess: r => { setSegreto(null); setCodice(""); setFatto(`Nuovo codice a tempo attivo: il precedente non vale più.${altreSessioni(r.altre_sessioni_chiuse)}`); onFatto(); },
  });
  return (
    <section aria-labelledby="vd-cred-totp">
      <div className="vd-sezione-testa">
        <h2 id="vd-cred-totp" className="vd-etichetta">Codice a tempo</h2>
        {s.totp.confermato_il && <span className="vd-nota">Attivo dal {quandoBreve(s.totp.confermato_il)}</span>}
      </div>
      {!confermata(s) ? <SenzaConferma /> : !segreto ? (
        <>
          <p>Se hai cambiato telefono o pensi che qualcuno abbia la chiave, sostituisci il codice a tempo. Quello attuale resta valido finché non attivi il nuovo.</p>
          <div className="vd-comandi"><button type="button" className="vd-bottone vd-bottone--secondario" disabled={genera.isPending} onClick={() => genera.mutate()}>Prepara un nuovo codice a tempo</button></div>
        </>
      ) : (
        <form className="vd-modulo" onSubmit={e => { e.preventDefault(); attiva.mutate(); }}>
          <p>Aggiungi VerumDoc all'app di autenticazione con questa chiave. Non la mostreremo più.</p>
          <p className="vd-mono vd-a-capo" aria-label="Nuova chiave del codice a tempo">{segreto.segreto}</p>
          <div className="vd-campo">
            <label htmlFor="vd-cred-totp-codice">Codice a sei cifre mostrato dall'app per la nuova chiave</label>
            <input id="vd-cred-totp-codice" inputMode="numeric" autoComplete="one-time-code" className="vd-input vd-mono" maxLength={6} value={codice} onChange={e => setCodice(e.target.value.replace(/\D/g, ""))} />
          </div>
          <div className="vd-comandi">
            <button type="submit" className="vd-bottone" disabled={attiva.isPending || codice.length !== 6}>Attiva il nuovo codice a tempo</button>
            <button type="button" className="vd-comando-testo" onClick={() => { setSegreto(null); setCodice(""); }}>Annulla</button>
          </div>
        </form>
      )}
      <Esito errore={genera.error || attiva.error} fatto={fatto} />
    </section>
  );
}

function CodiciRecupero({ s, onFatto }: Prop) {
  const [codici, setCodici] = useState<string[] | null>(null);
  const [conferma, setConferma] = useState(false);
  const genera = useMutation({
    mutationFn: nuoviCodiciRecupero,
    onSuccess: r => { setCodici(r.codici_recupero); setConferma(false); onFatto(); },
  });
  const { rimasti, totale } = s.codici_recupero;
  return (
    <section aria-labelledby="vd-cred-codici">
      <div className="vd-sezione-testa">
        <h2 id="vd-cred-codici" className="vd-etichetta">Codici di recupero</h2>
        <span className="vd-nota">{rimasti} di {totale} ancora da usare</span>
      </div>
      {codici ? (
        <div className="vd-pila">
          <div className="vd-esito vd-esito--info" role="status">
            <p className="vd-esito-titolo">Conserva i nuovi codici di recupero</p>
            <p>Ognuno vale una volta, al posto del codice a tempo. Non li mostreremo più; i codici precedenti non valgono più.</p>
          </div>
          <ul className="vd-elenco vd-mono" aria-label="Nuovi codici di recupero">{codici.map(c => <li key={c}><span>{c}</span></li>)}</ul>
          <div className="vd-comandi"><button type="button" className="vd-bottone" onClick={() => setCodici(null)}>Li ho conservati</button></div>
        </div>
      ) : !confermata(s) ? <SenzaConferma /> : conferma ? (
        <div className="vd-comandi">
          <button type="button" className="vd-bottone" disabled={genera.isPending} onClick={() => genera.mutate()}>Sì, genera nuovi codici</button>
          <button type="button" className="vd-comando-testo" onClick={() => setConferma(false)}>Annulla</button>
        </div>
      ) : (
        <>
          <p>{rimasti <= 3 ? "Ti restano pochi codici: generane di nuovi." : "Se hai perso i codici, generane di nuovi: quelli precedenti smetteranno di valere."}</p>
          <div className="vd-comandi"><button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => setConferma(true)}>Genera nuovi codici di recupero</button></div>
        </>
      )}
      <Esito errore={genera.error} />
    </section>
  );
}
