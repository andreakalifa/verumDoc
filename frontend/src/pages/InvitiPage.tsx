// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Metodi d'accesso e inviti dell'ente (R10, D08; onboarding /accesso e /inviti).
 * In alto i metodi con cui le persone dell'ente accedono (scelti fra quelli abilitati dal
 * conservatore: si cambiano in «Accesso e notifiche»), poi gli inviti: una persona riceve
 * un'e-mail con un collegamento personale e scade dopo il tempo indicato; l'attivazione la
 * guida passo passo. Il collegamento non si vede qui: arriva solo alla persona.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { NOMI_METODI, creaInvito, leggiAccessoEnte, leggiInviti, leggiRuoli, reinviaInvito, revocaInvito, type Invito } from "../console/accesso";
import { InLettura, Vuoto, quandoBreve } from "../console/comuni";

const STATO: Record<Invito["stato"], { testo: string; classe: string }> = {
  IN_ATTESA: { testo: "In attesa", classe: "vd-stato--info" },
  USATO: { testo: "Attivato", classe: "vd-stato--ok" },
  REVOCATO: { testo: "Revocato", classe: "vd-stato--neutro" },
  SCADUTO: { testo: "Scaduto", classe: "vd-stato--rischio" },
};
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export default function InvitiPage() {
  const qc = useQueryClient();
  const ente = useEnte();
  const tid = ente.tenantId;
  const accesso = useQuery({ queryKey: ["accesso-ente", tid], queryFn: () => leggiAccessoEnte(tid), enabled: !!tid });
  const inviti = useQuery({ queryKey: ["inviti", tid], queryFn: () => leggiInviti(tid), enabled: !!tid });
  const ruoli = useQuery({ queryKey: ["ruoli", tid], queryFn: () => leggiRuoli(tid), enabled: !!tid });
  const [f, setF] = useState({ email: "", nome: "", cognome: "", ruolo: "OPERATORE", codice_fiscale: "" });
  const [tentato, setTentato] = useState(false);
  const ricarica = () => qc.invalidateQueries({ queryKey: ["inviti", tid] });
  const crea = useMutation({
    mutationFn: () => {
      const personalizzato = f.ruolo.startsWith("rp:") ? f.ruolo.slice(3) : null;
      const base = personalizzato ? ruoli.data?.items.find(r => r.id === personalizzato)?.ruolo_base ?? "VIEWER" : f.ruolo;
      return creaInvito(tid, { email: f.email.trim(), nome: f.nome.trim(), cognome: f.cognome.trim(), ruolo: base,
        ruolo_personalizzato_id: personalizzato, codice_fiscale: f.codice_fiscale.trim() || null });
    },
    onSuccess: () => { setF({ email: "", nome: "", cognome: "", ruolo: "OPERATORE", codice_fiscale: "" }); setTentato(false); ricarica(); },
  });
  const azione = useMutation({ mutationFn: (x: { id: string; tipo: "reinvia" | "revoca" }) => x.tipo === "reinvia" ? reinviaInvito(tid, x.id) : revocaInvito(tid, x.id), onSuccess: ricarica });
  const valido = EMAIL.test(f.email.trim()) && f.nome.trim().length >= 2 && f.cognome.trim().length >= 1;
  const a = accesso.data;
  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Inviti e metodi d'accesso</h1>
        <p>Invita le persone dell'ente: riceveranno un'e-mail per attivare l'accesso con uno dei metodi ammessi.</p>
      </div>
      {ente.globale && <div className="vd-filtri"><SceltaEnte id="vd-inviti-ente" stato={ente} /></div>}
      {!tid ? <Vuoto titolo="Scegli un ente" /> : (
        <div className="vd-pila">
          <section aria-labelledby="vd-metodi">
            <div className="vd-sezione-testa"><h2 id="vd-metodi" className="vd-etichetta">Metodi d'accesso</h2></div>
            {accesso.isLoading ? <InLettura testo="Lettura dei metodi…" />
              : accesso.isError || !a ? <EsitoCritico titolo="Metodi d'accesso non disponibili" errore={accesso.error} onRiprova={() => accesso.refetch()} />
              : (
                <ul className="vd-elenco">
                  <li><span>Ammessi per l'ente</span><span>{a.metodi_ammessi.map(m => NOMI_METODI[m] ?? m).join(", ") || "nessuno"}</span></li>
                  <li><span>Abilitati dal conservatore</span><span>{a.metodi_del_conservatore.map(m => NOMI_METODI[m] ?? m).join(", ") || "nessuno"}</span></li>
                  <li><span>Validità di un invito</span><span>{a.invito_validita_ore} ore</span></li>
                  <li><span>Cambiare i metodi</span><span><Link to="/configurazione/accesso" className="vd-riga-link">Accesso e notifiche</Link> (si possono solo togliere metodi)</span></li>
                </ul>
              )}
          </section>
          <section aria-labelledby="vd-inviti">
            <div className="vd-sezione-testa"><h2 id="vd-inviti" className="vd-etichetta">Inviti</h2></div>
            {inviti.isLoading ? <InLettura testo="Lettura degli inviti…" />
              : inviti.isError ? <EsitoCritico titolo="Inviti non disponibili" errore={inviti.error} onRiprova={() => inviti.refetch()} />
              : (inviti.data?.items.length ?? 0) === 0 ? <Vuoto titolo="Nessun invito"><p>Invita la prima persona qui sotto.</p></Vuoto>
              : (
                <div className="vd-scorri">
                  <table className="vd-tabellone vd-impila">
                    <caption className="vd-sr">Inviti dell'ente</caption>
                    <thead><tr><th scope="col">Persona</th><th scope="col">Stato</th><th scope="col">Scadenza</th><th scope="col">Comandi</th></tr></thead>
                    <tbody>
                      {inviti.data!.items.map(i => (
                        <tr key={i.id}>
                          <td data-label="Persona">{i.nome} {i.cognome}<span className="vd-sotto vd-a-capo">{i.email}</span></td>
                          <td data-label="Stato"><span className={`vd-stato ${STATO[i.stato].classe}`}>{STATO[i.stato].testo}</span>
                            {i.metodo_attivato && <span className="vd-sotto">con {NOMI_METODI[i.metodo_attivato] ?? i.metodo_attivato}</span>}</td>
                          <td data-label="Scadenza">{i.stato === "USATO" ? `attivato ${quandoBreve(i.usato_il)}` : quandoBreve(i.scade_il)}</td>
                          <td>
                            <span className="vd-comandi-cella">
                              {(i.stato === "IN_ATTESA" || i.stato === "SCADUTO") && <button type="button" className="vd-comando-testo" disabled={azione.isPending} onClick={() => azione.mutate({ id: i.id, tipo: "reinvia" })}>Invia di nuovo</button>}
                              {i.stato === "IN_ATTESA" && <button type="button" className="vd-comando-testo" disabled={azione.isPending} onClick={() => azione.mutate({ id: i.id, tipo: "revoca" })}>Revoca</button>}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            {azione.isError && <EsitoCritico titolo="Operazione non riuscita" errore={azione.error} azione="operazione" />}
          </section>
          <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setTentato(true); if (valido) crea.mutate(); }}>
            <h2>Invita una persona</h2>
            <div className="vd-griglia">
              {([["email", "Indirizzo e-mail"], ["nome", "Nome"], ["cognome", "Cognome"]] as const).map(([k, etichetta]) => (
                <div className="vd-campo" key={k}>
                  <label htmlFor={`vd-invito-${k}`}>{etichetta}<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                  <input id={`vd-invito-${k}`} className="vd-input" value={f[k]} type={k === "email" ? "email" : "text"} autoComplete="off"
                         aria-invalid={tentato && !valido ? true : undefined} onChange={e => setF({ ...f, [k]: e.target.value })} />
                </div>
              ))}
              <div className="vd-campo">
                <label htmlFor="vd-invito-ruolo">Ruolo</label>
                <select id="vd-invito-ruolo" className="vd-input" value={f.ruolo} onChange={e => setF({ ...f, ruolo: e.target.value })}>
                  <option value="OPERATORE">Operatore</option>
                  <option value="VIEWER">Consultazione</option>
                  {(ruoli.data?.items ?? []).filter(r => r.attivo).map(r => <option key={r.id} value={`rp:${r.id}`}>{r.nome}</option>)}
                </select>
              </div>
              <div className="vd-campo">
                <label htmlFor="vd-invito-cf">Codice fiscale</label>
                <input id="vd-invito-cf" className="vd-input vd-mono" value={f.codice_fiscale} maxLength={16} onChange={e => setF({ ...f, codice_fiscale: e.target.value.toUpperCase() })} />
                <span className="vd-aiuto">Serve solo per accedere con SPID o CIE.</span>
              </div>
            </div>
            {tentato && !valido && <p className="vd-stato vd-stato--critico">Indica e-mail, nome e cognome.</p>}
            {crea.isError && <EsitoCritico titolo="Invito non inviato" errore={crea.error} azione="operazione" />}
            {crea.isSuccess && <p className="vd-nota" role="status">Invito inviato: la persona riceverà un'e-mail.</p>}
            <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={crea.isPending}>{crea.isPending ? "Invio…" : "Invia l'invito"}</button></div>
          </form>
        </div>
      )}
    </>
  );
}
