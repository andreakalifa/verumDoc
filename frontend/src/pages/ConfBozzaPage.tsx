// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Una bozza di configurazione: anteprima dell'impatto e applicazione (contratto Q1,
 * REGOLE §13). Sistema «Partenze».
 *
 * 1. «Calcola l'anteprima» (POST …/anteprima, non scrive): che cosa cambia (prima →
 *    dopo con l'origine), quanti versamenti degli ultimi 30 giorni sarebbero stati
 *    rifiutati o avrebbero avuto avvisi nuovi, con esempi e il campione dichiarato;
 *    elementi bloccati con il motivo; avvisi.
 * 2. «Conferma e applica» con l'impronta del piano. Se nel frattempo il piano è
 *    cambiato il servizio risponde 409 CFG-409 (`dettagli.motivo = PIANO_CAMBIATO`)
 *    con il piano nuovo: la pagina lo mostra al posto del vecchio, dice che nulla è
 *    stato scritto e chiede di confermare di nuovo.
 * Annullare una bozza aperta è una conferma in riga con la versione letta (409 CFG-410).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { EsitoCritico } from "../components/EsitoCritico";
import { leggiErrore } from "../api/errori";
import { annullaBozza, anteprimaBozza, applicaBozza, leggiBozza, type Bozza, type Impatto, type Piano } from "../console/api";
import { breve, InLettura, num, origineInParole, plurale, quandoBreve, valoreInParole } from "../console/comuni";
import { EFFETTO, nomeLivello, parola, RISORSA, STATO_BOZZA } from "../console/parole";

/** «REGOLA.RGL-103.EFFETTO» → «RGL-103 · severità», con i valori come parole di stato. */
const regolaDi = (codice: string) => codice.match(/^REGOLA\.(.+)\.EFFETTO$/)?.[1] ?? null;
function ValorePiano({ codice, v, classe }: { codice: string; v: unknown; classe: string }) {
  if (regolaDi(codice) && typeof v === "string") { const e = parola(EFFETTO, v); return <span className={`vd-stato ${e.classe}`}>{e.testo}</span>; }
  return <span className={classe}>{valoreInParole(v)}</span>;
}

function descriviModifica(m: Record<string, unknown>): [string, string] {
  if ("regola" in m) return [`${String(m.regola)} · severità`, parola(EFFETTO, String(m.effetto ?? "")).testo];
  if ("campo" in m) return [`accordo: ${String(m.campo)}`, valoreInParole(m.valore)];
  if (m.eredita) return [String(m.codice ?? "?"), "torna al valore ereditato"];
  if ("campi" in m) return [String(m.tipologia ?? "profilo"), `${Array.isArray(m.campi) ? m.campi.length : 0} campi`];
  return [String(m.codice ?? "?"), valoreInParole(m.valore)];
}

function ImpattoVista({ i }: { i: Impatto }) {
  const rif = i.pdv_ultimi_30_giorni_che_sarebbero_rifiutati;
  const avv = i.pdv_ultimi_30_giorni_con_nuovi_avvisi;
  const c = i.campione;
  if (!c) {
    return <p className="vd-avviso vd-avviso--neutro">Nessun impatto sui versamenti: {i.nota ?? "la modifica non riguarda le regole del versamento"}.</p>;
  }
  return (
    <>
      <p style={{ fontSize: 17, maxWidth: "72ch" }}>
        {rif > 0
          ? <>Con questa modifica <strong className="vd-stato--critico">{plurale(rif, "versamento", "versamenti")}</strong> degli ultimi {c.finestra_giorni ?? 30} giorni {rif === 1 ? "sarebbe stato rifiutato" : "sarebbero stati rifiutati"}.</>
          : <>Nessun versamento degli ultimi {c.finestra_giorni ?? 30} giorni sarebbe stato rifiutato in più.</>}
        {avv > 0 && <> {plurale(avv, "versamento avrebbe", "versamenti avrebbero")} avuto avvisi nuovi.</>}
      </p>
      <dl className="vd-dati" style={{ marginTop: 12 }}>
        <dt>Sarebbero rifiutati</dt><dd><span className={`vd-numero${rif > 0 ? " vd-stato--critico" : ""}`}>{num(rif)}</span></dd>
        <dt>Con avvisi nuovi</dt><dd><span className={`vd-numero${avv > 0 ? " vd-stato--rischio" : ""}`}>{num(avv)}</span></dd>
        <dt>Rifiutati comunque</dt><dd><span className="vd-numero">{num(i.rifiutati_con_la_configurazione_attuale)}</span> <span className="vd-nota">anche con la configurazione attuale</span></dd>
        <dt>Non valutabili</dt><dd><span className="vd-numero">{num(i.non_valutabili)}</span></dd>
        <dt>Campione</dt>
        <dd>
          {num(c.analizzati)} versamenti analizzati su {num(c.letti)} letti, al massimo {num(c.limite)} negli ultimi {c.finestra_giorni ?? 30} giorni
          {c.metodo ? ` (${c.metodo})` : ""}.
          {c.raggiunto_limite && <span className="vd-stato vd-stato--rischio" style={{ display: "block", whiteSpace: "normal" }}>Limite del campione raggiunto: i numeri valgono per il campione, non per tutti i versamenti.</span>}
          {c.troncato_per_tempo && <span className="vd-stato vd-stato--rischio" style={{ display: "block", whiteSpace: "normal" }}>Calcolo fermato dopo {c.tempo_max_s} secondi: analisi parziale.</span>}
          {c.nota && <span className="vd-nota" style={{ display: "block" }}>Stima {c.nota}.</span>}
        </dd>
      </dl>
      {i.esempi.length > 0 && (
        <div className="vd-scorri" style={{ marginTop: 20 }}>
          <table className="vd-tabellone vd-impila">
            <caption style={{ textAlign: "left", fontWeight: 700, paddingBottom: 6 }}>Esempi dal campione</caption>
            <thead><tr><th scope="col">Ora</th><th scope="col">Versamento</th><th scope="col">Esito con la modifica</th></tr></thead>
            <tbody>
              {i.esempi.map(e => {
                const q = e.ricevuto_il ? new Date(e.ricevuto_il) : null;
                const rilievi = [...(e.rilievi ?? []), ...(e.avvisi ?? [])];
                return (
                  <tr key={e.pdv_id}>
                    <td className="vd-ora" data-label="Ora">
                      {q ? q.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "—"}
                      <span className="vd-sotto vd-sotto-normale">{q ? q.toLocaleDateString("it-IT") : ""}</span>
                    </td>
                    <td data-label="Versamento"><span className="vd-mono" title={e.pdv_id}>{breve(e.pdv_id)}</span></td>
                    <td data-label="Esito con la modifica" style={{ whiteSpace: "normal" }}>
                      <span className={`vd-stato ${e.esito === "RIFIUTATO" ? "vd-stato--critico" : e.esito === "AVVISI" ? "vd-stato--rischio" : "vd-stato--neutro"}`}>
                        {e.esito === "RIFIUTATO" ? "Sarebbe rifiutato" : e.esito === "AVVISI" ? "Avvisi nuovi" : "Non valutabile"}
                      </span>
                      {e.motivo && <span className="vd-sotto vd-a-capo">{e.motivo}</span>}
                      {rilievi.map((r, k) => (
                        <span key={k} className="vd-sotto vd-a-capo"><span className="vd-mono">{r.regola ?? r.codice}</span> {r.messaggio}</span>
                      ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function PianoVista({ piano, livello }: { piano: Piano; livello: string }) {
  return (
    <>
      <section aria-labelledby="vd-piano-cambia">
        <h3 id="vd-piano-cambia" className="vd-tappa-titolo" style={{ marginBottom: 10 }}>Che cosa cambia</h3>
        {piano.modifiche.length === 0 ? <p className="vd-nota">Nessun valore cambia.</p> : (
          <div className="vd-scorri">
            <table className="vd-tabellone vd-impila vd-impila--pieno">
              <caption className="vd-sr">Valori prima e dopo l'applicazione</caption>
              <thead><tr><th scope="col">Voce</th><th scope="col">Prima</th><th scope="col">Dopo</th></tr></thead>
              <tbody>
                {piano.modifiche.map(m => (
                  <tr key={m.codice}>
                    <td data-label="Voce">{regolaDi(m.codice) ? <><span className="vd-mono">{regolaDi(m.codice)}</span> · severità</> : <span className="vd-mono vd-a-capo">{m.codice}</span>}</td>
                    <td data-label="Prima" className="vd-cella-valore">
                      <ValorePiano codice={m.codice} v={m.prima} classe="vd-prima" />
                      <span className="vd-sotto">{origineInParole(m.origine_prima, livello)}</span>
                    </td>
                    <td data-label="Dopo" className="vd-cella-valore">
                      <ValorePiano codice={m.codice} v={m.dopo} classe="vd-dopo" />
                      <span className="vd-sotto">{origineInParole(m.origine_dopo, livello)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {piano.bloccati.length > 0 && (
        <div className="vd-esito vd-esito--critico" role="alert">
          <p className="vd-esito-titolo">{plurale(piano.bloccati.length, "elemento bloccato", "elementi bloccati")}: la bozza non si può applicare così</p>
          <ul className="vd-elenco">
            {piano.bloccati.map((b, k) => <li key={k}><span className="vd-mono">{b.codice}</span><span>{b.motivo}</span></li>)}
          </ul>
          <p className="vd-nota">Togli o correggi questi elementi in una bozza nuova.</p>
        </div>
      )}
      {piano.avvisi.map(a => <p key={a} className="vd-avviso">{a}</p>)}
    </>
  );
}

export default function ConfBozzaPage() {
  const { id = "" } = useParams();
  const location = useLocation();
  const qc = useQueryClient();
  const [piano, setPiano] = useState<Piano | null>(null);
  const [impattoVecchio, setImpattoVecchio] = useState(false);
  const [validoDal, setValidoDal] = useState("");
  const [annullando, setAnnullando] = useState(false);

  const bozza = useQuery({ queryKey: ["conf-bozza", id], queryFn: () => leggiBozza(id) });
  const b: Bozza | undefined = bozza.data;

  const anteprima = useMutation({
    mutationFn: () => anteprimaBozza(id),
    onSuccess: p => { setPiano(p); setImpattoVecchio(false); },
  });
  const applica = useMutation({
    mutationFn: () => applicaBozza(id, piano!.impronta_piano, validoDal || undefined),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["conf-bozza", id] }); qc.invalidateQueries({ queryKey: ["conf-bozze"] }); },
    onError: (e: unknown) => {
      const l = leggiErrore(e);
      const dettagli = (e as { response?: { data?: { dettagli?: { piano?: Piano } } } })?.response?.data?.dettagli;
      if (l.codice === "CFG-409" && dettagli?.piano) {
        // Il piano nuovo non porta l'impatto: si tiene quello calcolato prima, dichiarato come superato
        setPiano(p => ({ ...dettagli.piano!, impatto: p?.impatto }));
        setImpattoVecchio(true);
      }
    },
  });
  const annulla = useMutation({
    mutationFn: () => annullaBozza(id, b!.versione),
    onSuccess: () => { setAnnullando(false); qc.invalidateQueries({ queryKey: ["conf-bozza", id] }); qc.invalidateQueries({ queryKey: ["conf-bozze"] }); },
  });
  const pianoCambiato = leggiErrore(applica.error).codice === "CFG-409";
  // R07 (D28): ogni impostazione si può programmare da una data futura (non le proposte)
  const conDecorrenza = !b?.proposta;
  const versionata = b?.risorsa === "accordo" || b?.risorsa === "profilo" || b?.ambito.livello === "ACCORDO";

  if (bozza.isLoading) return <InLettura testo="Lettura della bozza…" />;
  if (bozza.isError || !b) {
    return <EsitoCritico titolo="Bozza non disponibile" errore={bozza.error} onRiprova={() => bozza.refetch()} inRiprova={bozza.isFetching} />;
  }
  const st = parola(STATO_BOZZA, b.stato);
  const modifiche = b.modifiche ?? [];
  const aperta = b.stato === "APERTA";
  const programmata = b.stato === "PROGRAMMATA";
  const fatta = applica.isSuccess;
  const esitoProgrammato = applica.data?.stato === "PROGRAMMATA";
  const anteprimaProg = b.anteprima_programmata;
  const bloccoProgrammazione = (b.esito as { programmazione?: { bloccati?: { codice: string; motivo: string }[]; valido_dal?: string } } | null)?.programmazione;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Bozza: {(RISORSA[b.risorsa] ?? b.risorsa).toLowerCase()}</h1>
        <p>
          <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
          {" · "}livello {nomeLivello(b.ambito.livello)}{b.proposta ? " · proposta agli enti" : ""}
        </p>
        <div className="vd-testa-comandi">
          <Link to="/configurazione/bozze" className="vd-comando-testo">Tutte le bozze</Link>
        </div>
      </div>

      <div className="vd-pila">
        {(location.state as { creata?: boolean } | null)?.creata && aperta && !piano && (
          <div className="vd-esito vd-esito--info" role="status">
            <p className="vd-esito-titolo">Bozza salvata</p>
            <p>Nulla è ancora cambiato. Calcola l'anteprima per vedere l'effetto sui versamenti, poi applica.</p>
          </div>
        )}

        <section aria-labelledby="vd-bozza-richiesta">
          <div className="vd-sezione-testa"><h2 id="vd-bozza-richiesta" className="vd-etichetta">Richiesta</h2></div>
          <dl className="vd-dati">
            <dt>Ambito</dt>
            <dd>
              {nomeLivello(b.ambito.livello).replace(/^./, c => c.toUpperCase())}
              {b.ambito.ente && <> · ente <span className="vd-mono" title={b.ambito.ente}>{breve(b.ambito.ente)}</span></>}
              {b.ambito.classe && <> · classe <span className="vd-mono" title={b.ambito.classe}>{breve(b.ambito.classe)}</span></>}
              {b.ambito.accordo && <> · accordo <span className="vd-mono">{b.ambito.accordo}</span></>}
            </dd>
            <dt>Motivo</dt><dd>{b.motivo}</dd>
            <dt>Aggiornata</dt><dd>{quandoBreve(b.updated_at)} · versione {b.versione}</dd>
            {b.valido_dal && <><dt>{programmata ? "Si applicherà il" : "Decorrenza"}</dt><dd>{b.valido_dal.split("-").reverse().join("/")}</dd></>}
            {b.applicata_il && <><dt>Applicata</dt><dd>{quandoBreve(b.applicata_il)}</dd></>}
            {b.impronta_piano && <><dt>Impronta del piano</dt><dd className="vd-mono">{b.impronta_piano}</dd></>}
          </dl>
          <div className="vd-scorri" style={{ marginTop: 20 }}>
            <table className="vd-tabellone vd-impila vd-impila--pieno">
              <caption style={{ textAlign: "left", fontWeight: 700, paddingBottom: 6 }}>Modifiche richieste</caption>
              <thead><tr><th scope="col">Voce</th><th scope="col">Richiesta</th></tr></thead>
              <tbody>
                {modifiche.map((m, k) => {
                  const [voce, testo] = descriviModifica(m);
                  return <tr key={k}><td data-label="Voce" className="vd-a-capo">{voce}</td><td data-label="Richiesta" className="vd-cella-valore">{testo}</td></tr>;
                })}
              </tbody>
            </table>
          </div>
          {aperta && !fatta && (
            <div className="vd-comandi" style={{ marginTop: 16 }}>
              {annullando ? (
                <div className="vd-comandi-cella" role="group" aria-label="Annulla la bozza">
                  <span className="vd-stato vd-stato--critico">Annullare la bozza? Resta nell'elenco come annullata.</span>
                  <button type="button" className="vd-bottone vd-bottone--secondario" disabled={annulla.isPending} onClick={() => annulla.mutate()}>
                    {annulla.isPending ? "Annullamento…" : "Annulla la bozza"}
                  </button>
                  <button type="button" className="vd-comando-testo" onClick={() => setAnnullando(false)}>Tieni la bozza</button>
                </div>
              ) : (
                <button type="button" className="vd-comando-testo" onClick={() => setAnnullando(true)}>Annulla la bozza</button>
              )}
              {annulla.isError && (
                <EsitoCritico titolo="Bozza non annullata" errore={annulla.error} azione="operazione"
                              spiegazione={leggiErrore(annulla.error).codice === "CFG-410" ? "Qualcuno l'ha modificata nel frattempo: rileggi la pagina." : undefined}
                              onRiprova={() => bozza.refetch()} />
              )}
            </div>
          )}
        </section>

        {aperta && !fatta && (
          <section aria-labelledby="vd-bozza-anteprima">
            <div className="vd-sezione-testa">
              <h2 id="vd-bozza-anteprima" className="vd-etichetta">Anteprima</h2>
              <span className="vd-nota">non scrive nulla</span>
            </div>
            {!piano ? (
              <div className="vd-comandi">
                <button type="button" className="vd-bottone" disabled={anteprima.isPending} onClick={() => anteprima.mutate()}>
                  {anteprima.isPending ? "Calcolo dell'anteprima…" : "Calcola l'anteprima"}
                </button>
                <span className="vd-nota">Riapplica le regole ai versamenti recenti: può chiedere qualche secondo.</span>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
                {pianoCambiato && (
                  <div className="vd-esito vd-esito--rischio" role="alert">
                    <p className="vd-esito-titolo">Il piano è cambiato</p>
                    <p>Dopo l'anteprima qualcosa è cambiato (un'altra modifica applicata allo stesso ambito). Nulla è stato scritto: qui sotto c'è il piano aggiornato. Controllalo e conferma di nuovo.</p>
                  </div>
                )}
                <PianoVista piano={piano} livello={b.ambito.livello} />
                {piano.impatto && (
                  <section aria-labelledby="vd-bozza-impatto">
                    <h3 id="vd-bozza-impatto" className="vd-tappa-titolo" style={{ marginBottom: 10 }}>Impatto sui versamenti</h3>
                    {impattoVecchio && (
                      <p className="vd-avviso" style={{ marginBottom: 12 }}>
                        Impatto calcolato sul piano precedente.{" "}
                        <button type="button" className="vd-comando-testo" style={{ minHeight: 0, padding: 0 }} onClick={() => anteprima.mutate()}>Ricalcola l'impatto</button>
                      </p>
                    )}
                    <ImpattoVista i={piano.impatto} />
                  </section>
                )}

                <section aria-labelledby="vd-bozza-applica" className="vd-modulo">
                  <h2 id="vd-bozza-applica">Applica</h2>
                  {piano.bloccati.length > 0 ? (
                    <p>La bozza ha elementi bloccati: non si applica.</p>
                  ) : (
                    <>
                      {conDecorrenza && (
                        <div className="vd-campo" style={{ maxWidth: 280 }}>
                          <label htmlFor="vd-bozza-dal">Valida dal (facoltativo)</label>
                          <input id="vd-bozza-dal" type="date" className="vd-input" value={validoDal} onChange={e => setValidoDal(e.target.value)} aria-describedby="vd-bozza-dal-aiuto" />
                          <span id="vd-bozza-dal-aiuto" className="vd-aiuto">
                            {versionata
                              ? "Vuoto: da oggi. I versamenti già fatti restano con la versione di allora."
                              : "Vuoto: da subito. Con una data futura la modifica resta programmata: il giorno prima l'anteprima si ricalcola e ricevi un avviso; fino ad allora si può annullare."}
                          </span>
                        </div>
                      )}
                      <p className="vd-nota">Impronta del piano: <span className="vd-mono vd-a-capo">{piano.impronta_piano}</span></p>
                      {applica.isError && !pianoCambiato && (
                        <EsitoCritico titolo="Modifica non applicata" errore={applica.error} azione="operazione" spiegazione="Nulla è stato scritto." />
                      )}
                      <div className="vd-comandi">
                        <button type="button" className="vd-bottone" disabled={applica.isPending} onClick={() => applica.mutate()}>
                          {applica.isPending ? "Applicazione…" : `${validoDal && !versionata ? "Conferma e programma" : "Conferma e applica"} ${plurale(piano.modifiche.length, "modifica", "modifiche")}`}
                        </button>
                        <button type="button" className="vd-bottone vd-bottone--secondario" disabled={anteprima.isPending || applica.isPending} onClick={() => anteprima.mutate()}>
                          {anteprima.isPending ? "Calcolo…" : "Ricalcola l'anteprima"}
                        </button>
                      </div>
                    </>
                  )}
                </section>
              </div>
            )}
            {anteprima.isError && (
              <EsitoCritico titolo="Anteprima non disponibile" errore={anteprima.error} spiegazione="Nulla è stato applicato."
                            onRiprova={() => anteprima.mutate()} inRiprova={anteprima.isPending} />
            )}
          </section>
        )}

        {programmata && (
          <section aria-labelledby="vd-bozza-programmata" className="vd-esito vd-esito--info">
            <p id="vd-bozza-programmata" className="vd-esito-titolo">
              Modifica programmata per il {b.valido_dal?.split("-").reverse().join("/")}
            </p>
            {anteprimaProg ? (
              <>
                <p>Anteprima ricalcolata il {quandoBreve(anteprimaProg.calcolata_il ?? b.anteprima_il ?? undefined)}
                  {b.avviso_autore_il ? ", con avviso a chi l'ha programmata" : ""}:</p>
                <ul>
                  {anteprimaProg.modifiche.map(m => (
                    <li key={m.codice}><span className="vd-mono">{m.codice}</span>: <span className="vd-prima">{JSON.stringify(m.prima)}</span> → <span className="vd-dopo">{JSON.stringify(m.dopo)}</span></li>
                  ))}
                  {anteprimaProg.bloccati.map(x => <li key={x.codice} className="vd-stato vd-stato--critico">{x.codice}: {x.motivo}</li>)}
                </ul>
              </>
            ) : <p>Il giorno prima l'anteprima si ricalcola sui dati di quel momento e chi l'ha programmata riceve un avviso.</p>}
            <div className="vd-comandi">
              <button type="button" className="vd-bottone vd-bottone--secondario" disabled={annulla.isPending}
                      onClick={() => annulla.mutate()}>
                {annulla.isPending ? "Annullamento…" : "Annulla la modifica programmata"}
              </button>
            </div>
            {annulla.isError && <EsitoCritico titolo="Non annullata" errore={annulla.error} azione="operazione" />}
          </section>
        )}
        {aperta && bloccoProgrammazione && (
          <div className="vd-esito vd-esito--rischio" role="status">
            <p className="vd-esito-titolo">La modifica programmata non è stata applicata</p>
            <ul>{(bloccoProgrammazione.bloccati ?? []).map(x => <li key={x.codice}>{x.codice}: {x.motivo}</li>)}</ul>
            <p>La bozza è tornata aperta: correggila o annullala.</p>
          </div>
        )}
        {fatta && esitoProgrammato && (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">Modifica programmata</p>
            <p>Si applicherà il {String(applica.data?.valido_dal ?? "").split("-").reverse().join("/")}. Fino ad allora nulla cambia e la puoi annullare.</p>
          </div>
        )}
        {fatta && !esitoProgrammato && (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">{b.proposta ? "Proposta inviata agli enti" : "Modifica applicata"}</p>
            <p>
              {b.proposta
                ? "Il valore del conservatore non cambia: ogni ente decide e la sua risposta compare tra le proposte."
                : "Vale per i versamenti da adesso; quelli già fatti conservano la configurazione con cui sono stati verificati."}
            </p>
            <p><Link to="/configurazione" className="vd-riga-link">Torna alle impostazioni</Link></p>
          </div>
        )}
      </div>
    </>
  );
}
