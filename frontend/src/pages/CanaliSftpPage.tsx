// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Account SFTP dell'ente (canale di versamento sftp-ingest; API onboarding
 * /api/v1/onboarding/tenant/{ente}/sftp/account). Non esisteva una pagina: R05 console.
 * Sistema «Partenze»: tabellone degli account con riga di dettaglio (chiavi pubbliche,
 * collegamento), sospendi/riattiva e revoca della chiave come conferme in riga,
 * aggiunta di una chiave e nuovo account come moduli. Mai eliminazioni: il servizio
 * sospende e revoca.
 * L'elenco degli account di un ente è piccolo e il servizio lo dà intero (non a cursore).
 */
import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { motivoErrore } from "../api/errori";
import { aggiungiChiaveSftp, creaAccountSftp, leggiAccountSftp, revocaChiaveSftp, statoAccountSftp, type AccountSftp } from "../console/api";
import { InLettura, quandoBreve, Vuoto } from "../console/comuni";
import { salvaIpAmmessi } from "../console/accesso";

const NOME = /^[a-z0-9][a-z0-9._-]{2,63}$/; // stesso schema del servizio (sftp.py)

/** R10 (D12): indirizzi da cui l'account si collega, entro i limiti del conservatore.
 *  Il server SFTP li confronta con l'indirizzo reale del sistema che si collega. */
function IndirizziAmmessi({ a, tid }: { a: AccountSftp; tid: string }) {
  const qc = useQueryClient();
  const [testo, setTesto] = useState((a.ip_ammessi ?? []).join("\n"));
  const salva = useMutation({ mutationFn: () => salvaIpAmmessi(tid, a.id, testo.split(/[\s,]+/).map(x => x.trim()).filter(Boolean)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sftp", tid] }) });
  return (
    <div>
      <h3>Indirizzi ammessi</h3>
      <p className="vd-nota">{(a.ip_ammessi ?? []).length ? "L'account si collega solo da questi indirizzi." : "Nessun elenco: l'account si collega da qualsiasi indirizzo (se l'ente non lo rende obbligatorio)."}</p>
      <div className="vd-campo">
        <label htmlFor={`vd-sftp-ip-${a.id}`}>Un indirizzo o una rete per riga</label>
        <textarea id={`vd-sftp-ip-${a.id}`} className="vd-input vd-textarea vd-mono" value={testo} spellCheck={false}
                  placeholder={"203.0.113.10\n203.0.113.0/24"} onChange={e => setTesto(e.target.value)} />
        <span className="vd-aiuto">Per esempio 203.0.113.10 o 203.0.113.0/24. Le reti troppo ampie non sono ammesse.</span>
      </div>
      {salva.isError && <EsitoCritico titolo="Elenco non salvato" errore={salva.error} azione="operazione" />}
      {salva.isSuccess && <p className="vd-nota" role="status">Elenco salvato.</p>}
      <div className="vd-comandi"><button type="button" className="vd-bottone vd-bottone--secondario" disabled={salva.isPending} onClick={() => salva.mutate()}>Salva gli indirizzi</button></div>
    </div>
  );
}

function Dettaglio({ a, tid }: { a: AccountSftp; tid: string }) {
  const qc = useQueryClient();
  const [chiave, setChiave] = useState("");
  const [revoca, setRevoca] = useState<string | null>(null);
  const agg = useMutation({ mutationFn: () => aggiungiChiaveSftp(tid, a.id, chiave.trim()), onSuccess: () => { setChiave(""); qc.invalidateQueries({ queryKey: ["sftp", tid] }); } });
  const rev = useMutation({ mutationFn: (k: string) => revocaChiaveSftp(tid, a.id, k), onSuccess: () => { setRevoca(null); qc.invalidateQueries({ queryKey: ["sftp", tid] }); } });
  const c = a.connessione;
  return (
    <div className="vd-dettaglio-griglia">
      <div>
        <h3>Collegamento</h3>
        <ul className="vd-elenco">
          <li><span>Utente</span><span className="vd-mono">{c.utente}</span></li>
          <li><span>Indirizzo</span><span className="vd-mono">{c.host ? `${c.host}:${c.porta}` : "non configurato nel servizio"}</span></li>
          <li><span>Responsabile</span><span>{a.responsabile ? `${a.responsabile.nome ?? ""} ${a.responsabile.cognome ?? ""}`.trim() || a.responsabile.email : "—"}</span></li>
          <li><span>Creato</span><span>{quandoBreve(a.created_at) ?? "—"}</span></li>
        </ul>
      </div>
      <IndirizziAmmessi a={a} tid={tid} />
      <div>
        <h3>Chiavi pubbliche</h3>
        {a.chiavi.length === 0 ? <p className="vd-nota">Nessuna chiave: senza chiave l'account non può collegarsi.</p> : (
          <ul className="vd-elenco">
            {a.chiavi.map(k => (
              <li key={k.id} style={{ flexDirection: "column", alignItems: "stretch" }}>
                <span><span className="vd-mono vd-a-capo">{k.impronta}</span></span>
                <span className="vd-nota">{k.algoritmo}{k.bit ? ` ${k.bit} bit` : ""}{k.commento ? ` · ${k.commento}` : ""} · {k.attiva ? `aggiunta ${quandoBreve(k.created_at)}` : `revocata ${quandoBreve(k.revocata_il)}`}</span>
                {k.attiva && (revoca === k.id ? (
                  <span className="vd-comandi-cella">
                    <span className="vd-stato vd-stato--critico">Revocare la chiave?</span>
                    <button type="button" className="vd-bottone vd-bottone--secondario" disabled={rev.isPending} onClick={() => rev.mutate(k.id)}>Revoca</button>
                    <button type="button" className="vd-comando-testo" onClick={() => setRevoca(null)}>Annulla</button>
                  </span>
                ) : <span><button type="button" className="vd-comando-testo" onClick={() => setRevoca(k.id)}>Revoca</button></span>)}
              </li>
            ))}
          </ul>
        )}
        {rev.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>{motivoErrore(rev.error, "operazione")}</span>}
        <div className="vd-campo" style={{ marginTop: 12 }}>
          <label htmlFor={`vd-sftp-k-${a.id}`}>Aggiungi una chiave pubblica SSH</label>
          <textarea id={`vd-sftp-k-${a.id}`} className="vd-input vd-textarea vd-mono" value={chiave} spellCheck={false} placeholder="ssh-ed25519 AAAA… commento"
                    onChange={e => setChiave(e.target.value)} />
          {agg.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>{motivoErrore(agg.error, "operazione")}</span>}
        </div>
        <div className="vd-comandi" style={{ marginTop: 8 }}>
          <button type="button" className="vd-bottone vd-bottone--secondario" disabled={chiave.trim().length < 20 || agg.isPending} onClick={() => agg.mutate()}>
            {agg.isPending ? "Aggiunta…" : "Aggiungi la chiave"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function CanaliSftpPage() {
  const qc = useQueryClient();
  const ente = useEnte();
  const tid = ente.tenantId;
  const [aperto, setAperto] = useState<string | null>(null);
  const [conferma, setConferma] = useState<string | null>(null);
  const [nome, setNome] = useState("");
  const [descr, setDescr] = useState("");
  const [tentato, setTentato] = useState(false);
  const elenco = useQuery({ queryKey: ["sftp", tid], queryFn: () => leggiAccountSftp(tid), enabled: !!tid });
  const crea = useMutation({ mutationFn: () => creaAccountSftp(tid, { nome_utente: nome.trim(), descrizione: descr.trim() }),
    onSuccess: a => { setNome(""); setDescr(""); setTentato(false); setAperto(a.id); qc.invalidateQueries({ queryKey: ["sftp", tid] }); } });
  const stato = useMutation({ mutationFn: (x: { id: string; azione: "sospendi" | "riattiva" }) => statoAccountSftp(tid, x.id, x.azione),
    onSuccess: () => { setConferma(null); qc.invalidateQueries({ queryKey: ["sftp", tid] }); } });
  const righe = elenco.data?.items ?? [];
  const nomeOk = NOME.test(nome.trim());

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Account SFTP</h1>
        <p>Per i sistemi che versano depositando file in una cartella. Le chiavi API si gestiscono in <Link to="/utenti" className="vd-riga-link">Utenti e chiavi API</Link>.</p>
      </div>
      {ente.globale && <div className="vd-filtri"><SceltaEnte id="vd-sftp-ente" stato={ente} /></div>}
      <div className="vd-pila">
        <section aria-labelledby="vd-sftp-acc">
          <div className="vd-sezione-testa"><h2 id="vd-sftp-acc" className="vd-etichetta">Account</h2></div>
          {!tid ? <Vuoto titolo="Scegli un ente" />
            : elenco.isLoading ? <InLettura testo="Lettura degli account…" />
            : elenco.isError ? <EsitoCritico titolo="Account SFTP non disponibili" errore={elenco.error} spiegazione="Non significa che l'ente non ne abbia." onRiprova={() => elenco.refetch()} />
            : righe.length === 0 ? <Vuoto titolo="Nessun account SFTP"><p>Crealo qui sotto, poi aggiungi la chiave pubblica del sistema che versa.</p></Vuoto>
            : (
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Account SFTP dell'ente</caption>
                  <thead><tr><th scope="col">Utente</th><th scope="col">Descrizione</th><th scope="col">Chiavi</th><th scope="col">Stato</th><th scope="col">Comandi</th></tr></thead>
                  <tbody>
                    {righe.map(a => {
                      const attive = a.chiavi.filter(k => k.attiva).length;
                      const azione = a.azioni_consentite.sospendi ? "sospendi" : a.azioni_consentite.riattiva ? "riattiva" : null;
                      return (
                        <Fragment key={a.id}>
                          <tr>
                            <td className="vd-ora" data-label="Utente"><span className="vd-a-capo">{a.nome_utente}</span></td>
                            <td className="vd-cella-larga vd-a-capo" data-label="Descrizione">{a.descrizione || <span className="vd-nota">—</span>}</td>
                            <td data-label="Chiavi">{attive ? `${attive} ${attive === 1 ? "attiva" : "attive"}` : <span className="vd-stato vd-stato--rischio">Nessuna chiave</span>}</td>
                            <td data-label="Stato" style={{ whiteSpace: "normal" }}>
                              <span className={`vd-stato ${a.stato === "ATTIVO" ? "vd-stato--ok" : "vd-stato--neutro"}`}>{a.stato === "ATTIVO" ? "Attivo" : "Sospeso"}</span>
                              {a.sospeso_il && <span className="vd-sotto">dal {quandoBreve(a.sospeso_il)}</span>}
                            </td>
                            <td>
                              {conferma === a.id && azione ? (
                                <span className="vd-comandi-cella">
                                  <span className="vd-stato vd-stato--critico">{azione === "sospendi" ? "Sospendere l'accesso e la lavorazione?" : "Riattivare l'account?"}</span>
                                  <button type="button" className="vd-bottone vd-bottone--secondario" disabled={stato.isPending} onClick={() => stato.mutate({ id: a.id, azione })}>
                                    {azione === "sospendi" ? "Sospendi" : "Riattiva"}
                                  </button>
                                  <button type="button" className="vd-comando-testo" onClick={() => setConferma(null)}>Annulla</button>
                                  {stato.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>{motivoErrore(stato.error, "operazione")}</span>}
                                </span>
                              ) : (
                                <span className="vd-comandi-cella">
                                  <button type="button" className="vd-comando-testo" aria-expanded={aperto === a.id} onClick={() => setAperto(aperto === a.id ? null : a.id)}>
                                    {aperto === a.id ? "Chiudi" : "Chiavi e collegamento"}
                                  </button>
                                  {azione && <button type="button" className="vd-comando-testo" onClick={() => setConferma(a.id)}>{azione === "sospendi" ? "Sospendi" : "Riattiva"}</button>}
                                </span>
                              )}
                            </td>
                          </tr>
                          {aperto === a.id && <tr className="vd-dettaglio"><td colSpan={5}><Dettaglio a={a} tid={tid} /></td></tr>}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </section>

        {tid && (
          <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setTentato(true); if (nomeOk) crea.mutate(); }}>
            <h2>Nuovo account</h2>
            <div className="vd-griglia">
              <div className="vd-campo">
                <label htmlFor="vd-sftp-nome">Nome utente<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <input id="vd-sftp-nome" className="vd-input vd-mono" value={nome} spellCheck={false} autoComplete="off"
                       aria-invalid={tentato && !nomeOk ? true : undefined} aria-describedby="vd-sftp-nome-aiuto" onChange={e => setNome(e.target.value)} />
                <span id="vd-sftp-nome-aiuto" className="vd-aiuto">3–64 caratteri: minuscole, cifre, punto, trattino; unico fra tutti gli enti.</span>
                {tentato && !nomeOk && <span className="vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Nome utente non valido.</span>}
              </div>
              <div className="vd-campo">
                <label htmlFor="vd-sftp-descr">Descrizione</label>
                <input id="vd-sftp-descr" className="vd-input" value={descr} maxLength={200} onChange={e => setDescr(e.target.value)} />
              </div>
            </div>
            {crea.isError && <EsitoCritico titolo="Account non creato" errore={crea.error} azione="operazione" />}
            <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={crea.isPending}>{crea.isPending ? "Creazione…" : "Crea l'account"}</button></div>
          </form>
        )}
      </div>
    </>
  );
}
