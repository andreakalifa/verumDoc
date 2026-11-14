// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Ruoli personalizzati dell'ente (R10, D30; onboarding /api/v1/onboarding/tenant/{ente}/ruoli).
 * I ruoli fissi sono i modelli; un ruolo personalizzato parte da «Operatore» o «Consultazione»
 * e aggiunge o toglie permessi fra quelli che il conservatore rende concedibili. Alcune azioni
 * non si delegano mai (le mostra la pagina, spente, con il motivo). Il servizio ricontrolla
 * tutto: qui nessuna regola decisiva (§25.4).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { creaRuolo, disattivaRuolo, leggiRuoli, modificaRuolo, type CatalogoRuoli, type RuoloPersonalizzato } from "../console/accesso";
import { InLettura, Vuoto, plurale } from "../console/comuni";
import { nomeRuolo } from "../auth/ruoli";

function Permessi({ cat, scelti, onCambia, id }: { cat: CatalogoRuoli; scelti: string[]; onCambia: (p: string[]) => void; id: string }) {
  return (
    <fieldset className="vd-permessi">
      <legend>Permessi</legend>
      {cat.permessi.map(p => {
        const attivo = p.sempre_incluso || scelti.includes(p.codice);
        const spento = p.sempre_incluso || p.mai_delegabile || !p.concedibile;
        return (
          <label key={p.codice} className="vd-scelta" htmlFor={`${id}-${p.codice}`}>
            <input id={`${id}-${p.codice}`} type="checkbox" checked={attivo && !p.mai_delegabile} disabled={spento}
                   onChange={e => onCambia(e.target.checked ? [...scelti, p.codice] : scelti.filter(x => x !== p.codice))} />
            <span>{p.descrizione}
              {p.mai_delegabile && <span className="vd-sotto">Resta ai ruoli fissi: non si delega.</span>}
              {!p.mai_delegabile && !p.concedibile && !p.sempre_incluso && <span className="vd-sotto">Il conservatore non lo rende concedibile.</span>}
              {p.sempre_incluso && <span className="vd-sotto">Sempre compreso.</span>}
            </span>
          </label>
        );
      })}
    </fieldset>
  );
}

function Modifica({ r, cat, tid, onFatto }: { r: RuoloPersonalizzato; cat: CatalogoRuoli; tid: string; onFatto: () => void }) {
  const [scelti, setScelti] = useState(r.permessi);
  const m = useMutation({ mutationFn: () => modificaRuolo(tid, r.id, { versione: r.versione, permessi: scelti }), onSuccess: onFatto });
  return (
    <div className="vd-pila">
      <Permessi cat={cat} scelti={scelti} onCambia={setScelti} id={`vd-ruolo-${r.id}`} />
      {r.utenti > 0 && <p className="vd-nota">Chi ha questo ruolo ({plurale(r.utenti, "persona", "persone")}) dovrà accedere di nuovo.</p>}
      {m.isError && <EsitoCritico titolo="Ruolo non salvato" errore={m.error} azione="operazione" />}
      <div className="vd-comandi"><button type="button" className="vd-bottone vd-bottone--secondario" disabled={m.isPending} onClick={() => m.mutate()}>Salva i permessi</button></div>
    </div>
  );
}

export default function RuoliPage() {
  const qc = useQueryClient();
  const ente = useEnte();
  const tid = ente.tenantId;
  const q = useQuery({ queryKey: ["ruoli", tid], queryFn: () => leggiRuoli(tid), enabled: !!tid });
  const [aperto, setAperto] = useState<string | null>(null);
  const [nome, setNome] = useState("");
  const [base, setBase] = useState("OPERATORE");
  const [scelti, setScelti] = useState<string[]>([]);
  const ricarica = () => qc.invalidateQueries({ queryKey: ["ruoli", tid] });
  const crea = useMutation({ mutationFn: () => creaRuolo(tid, { nome: nome.trim(), descrizione: "", ruolo_base: base, permessi: scelti }),
    onSuccess: () => { setNome(""); setScelti([]); ricarica(); } });
  const disattiva = useMutation({ mutationFn: (id: string) => disattivaRuolo(tid, id), onSuccess: ricarica });
  const cat = q.data;
  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Ruoli personalizzati</h1>
        <p>Parti da un ruolo fisso e scegli i permessi. Esecuzione e autorizzazione dello scarto, approvazione degli annullamenti, firmatari e gestione degli utenti restano sempre ai ruoli fissi.</p>
      </div>
      {ente.globale && <div className="vd-filtri"><SceltaEnte id="vd-ruoli-ente" stato={ente} /></div>}
      {!tid ? <Vuoto titolo="Scegli un ente" />
        : q.isLoading ? <InLettura testo="Lettura dei ruoli…" />
        : q.isError || !cat ? <EsitoCritico titolo="Ruoli non disponibili" errore={q.error} onRiprova={() => q.refetch()} />
        : (
          <div className="vd-pila">
            <section aria-labelledby="vd-ruoli-elenco">
              <div className="vd-sezione-testa"><h2 id="vd-ruoli-elenco" className="vd-etichetta">Ruoli dell'ente</h2></div>
              {cat.items.length === 0 ? <Vuoto titolo="Nessun ruolo personalizzato"><p>Crealo qui sotto, poi assegnalo in «Utenti e chiavi API».</p></Vuoto> : (
                <ul className="vd-elenco">
                  {cat.items.map(r => (
                    <li key={r.id} className="vd-campanella-voce">
                      <span><strong>{r.nome}</strong> · parte da {nomeRuolo(r.ruolo_base)} · {r.attivo ? plurale(r.utenti, "persona", "persone") : "disattivato"}</span>
                      <span className="vd-sotto">{r.permessi.map(p => cat.permessi.find(x => x.codice === p)?.descrizione ?? p).join(" · ")}</span>
                      {cat.puo_gestire && r.attivo && (
                        <span className="vd-comandi-cella">
                          <button type="button" className="vd-comando-testo" aria-expanded={aperto === r.id} onClick={() => setAperto(aperto === r.id ? null : r.id)}>
                            {aperto === r.id ? "Chiudi" : "Cambia i permessi"}</button>
                          {r.utenti === 0 && <button type="button" className="vd-comando-testo" onClick={() => disattiva.mutate(r.id)}>Disattiva</button>}
                        </span>
                      )}
                      {aperto === r.id && <Modifica r={r} cat={cat} tid={tid} onFatto={() => { setAperto(null); ricarica(); }} />}
                    </li>
                  ))}
                </ul>
              )}
              {disattiva.isError && <EsitoCritico titolo="Ruolo non disattivato" errore={disattiva.error} azione="operazione" />}
            </section>
            {cat.puo_gestire && (
              <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); if (nome.trim().length >= 3) crea.mutate(); }}>
                <h2>Nuovo ruolo</h2>
                <div className="vd-griglia">
                  <div className="vd-campo">
                    <label htmlFor="vd-ruolo-nome">Nome del ruolo</label>
                    <input id="vd-ruolo-nome" className="vd-input" value={nome} maxLength={100} onChange={e => setNome(e.target.value)} />
                    <span className="vd-aiuto">Per esempio «Operatore con annullamenti».</span>
                  </div>
                  <div className="vd-campo">
                    <label htmlFor="vd-ruolo-base">Parte da</label>
                    <select id="vd-ruolo-base" className="vd-input" value={base} onChange={e => setBase(e.target.value)}>
                      {cat.ruoli_base.map(b => <option key={b} value={b}>{nomeRuolo(b)}</option>)}
                    </select>
                  </div>
                </div>
                <Permessi cat={cat} scelti={scelti} onCambia={setScelti} id="vd-ruolo-nuovo" />
                {crea.isError && <EsitoCritico titolo="Ruolo non creato" errore={crea.error} azione="operazione" />}
                <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={crea.isPending || nome.trim().length < 3}>{crea.isPending ? "Creazione…" : "Crea il ruolo"}</button></div>
              </form>
            )}
          </div>
        )}
    </>
  );
}
