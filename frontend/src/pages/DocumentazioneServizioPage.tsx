// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Documentazione del servizio (R15, VD-C-177) — area «Conformità», solo i ruoli fissi del
 * conservatore (permesso mai delegabile «documentazione:genera», ricontrollato dal servizio).
 *  - «Genera la documentazione»: richiesta asincrona; la pagina segue lo stato fino a
 *    COMPLETATA o ERRORE (con il motivo) e dice quali documenti hanno una versione nuova;
 *  - documenti: ultima versione, data, impronta del contenuto, dati da compilare, archivio
 *    nel WORM, scaricamento Markdown e HTML stampabile;
 *  - dati da compilare del documento scelto, sezione per sezione, con chi li fornisce;
 *  - storico delle versioni con impronta e sezioni cambiate;
 *  - revisione R17 (R17-04): «Invia alla firma del responsabile» mette in firma la versione
 *    stampabile (HTML) del manuale e del piano della sicurezza (pagina «Firme del
 *    responsabile», che mostra quel documento); R17-31: il vuoto porta il comando.
 * Sistema R12c (DESIGN.md): testa di pagina chiusa dal binario, colonna unica, tabellone
 * impilato su telefono, stati vuoti con `Vuoto`.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { inviaAllaFirma } from "../console/firme_responsabile";
import { EsitoCritico } from "../components/EsitoCritico";
import { Vuoto } from "../components/Vuoto";
import { InLettura, plurale, quandoBreve } from "../console/comuni";
import {
  NOME_DOCUMENTO, TIPI_DOCUMENTO, leggiGenerazione, leggiQuadro, leggiVersioni, richiediGenerazione,
  scaricaVersione, type Generazione, type TipoDocumento, type Versione,
} from "../console/documentazione";

const breveImpronta = (h: string) => `${h.slice(0, 12)}…`;

function Archivio({ v }: { v: Versione }) {
  return v.archivio.archiviata
    ? <span className="vd-stato vd-stato--ok">nel WORM</span>
    : <span className="vd-stato vd-stato--rischio">in attesa</span>;
}

function Scarica({ v }: { v: Versione }) {
  const [errore, setErrore] = useState<unknown>(null);
  const prendi = (formato: "markdown" | "html") => {
    setErrore(null);
    scaricaVersione(v, formato).catch(setErrore);
  };
  const nome = `${NOME_DOCUMENTO[v.tipo]}, versione ${v.versione}`;
  return (
    <div className="vd-comandi-cella">
      <button type="button" className="vd-link-bottone" onClick={() => prendi("markdown")} aria-label={`Scarica il Markdown: ${nome}`}>Markdown</button>
      <button type="button" className="vd-link-bottone" onClick={() => prendi("html")} aria-label={`Scarica l'HTML stampabile: ${nome}`}>HTML stampabile</button>
      {errore !== null && <EsitoCritico titolo="Scaricamento non riuscito" errore={errore} azione="operazione" />}
    </div>
  );
}

/** Documenti generati che si firmano (stesso elenco del servizio). */
const DA_FIRMARE: TipoDocumento[] = ["MANUALE_CONSERVAZIONE", "PIANO_SICUREZZA"];

function InviaAllaFirma({ v }: { v: Versione }) {
  const qc = useQueryClient();
  const invio = useMutation({ mutationFn: () => inviaAllaFirma(v.id),
                              onSuccess: () => qc.invalidateQueries({ queryKey: ["firme-responsabile"] }) });
  if (!DA_FIRMARE.includes(v.tipo)) return null;
  const nome = `${NOME_DOCUMENTO[v.tipo]}, versione ${v.versione}`;
  return (
    <div className="vd-comandi-cella">
      {invio.isSuccess ? (
        <span role="status" className="vd-stato vd-stato--ok vd-a-capo">
          {invio.data.nuovo ? "In firma" : "Già in firma"}: <Link to="/firme-responsabile" className="vd-riga-link">apri Firme del responsabile</Link>
        </span>
      ) : (
        <button type="button" className="vd-link-bottone" disabled={invio.isPending} onClick={() => invio.mutate()}
                aria-label={`Invia alla firma del responsabile: ${nome}`}>
          {invio.isPending ? "Invio…" : "Invia alla firma del responsabile"}
        </button>
      )}
      {invio.isError && <EsitoCritico titolo="Non inviato alla firma" errore={invio.error} azione="operazione" />}
    </div>
  );
}

function Cambiate({ v }: { v: Versione }) {
  const d = v.differenze;
  if (!d) return <span>—</span>;
  const voci = [...d.cambiate, ...d.aggiunte.map(s => ({ ...s, titolo: `${s.titolo} (nuova)` })),
    ...d.tolte.map(s => ({ ...s, titolo: `${s.titolo} (tolta)` }))];
  if (voci.length === 0) return <span>{v.versione === 1 ? "prima versione" : "nessuna"}</span>;
  return <ul className="vd-elenco-sezioni">{voci.map(s => <li key={s.id}>{s.numero} {s.titolo}</li>)}</ul>;
}

/** Stato dell'ultima richiesta, seguito finché è in corso. */
function StatoGenerazione({ id, onConclusa }: { id: string; onConclusa: () => void }) {
  const q = useQuery({
    queryKey: ["documentazione-generazione", id], queryFn: () => leggiGenerazione(id),
    refetchInterval: d => (d.state.data?.stato === "IN_CORSO" ? 1500 : false),
  });
  const stato = q.data?.stato;
  useEffect(() => { if (stato && stato !== "IN_CORSO") onConclusa(); }, [stato, onConclusa]);
  if (q.isError) return <EsitoCritico titolo="Stato della generazione non disponibile" errore={q.error} onRiprova={() => q.refetch()} />;
  return <div role="status" aria-live="polite"><EsitoGenerazione g={q.data} /></div>;
}

function EsitoGenerazione({ g }: { g?: Generazione }) {
  if (!g || g.stato === "IN_CORSO") return <p className="vd-nota">Generazione in corso: il servizio legge la configurazione in sola lettura e compone i documenti…</p>;
  if (g.stato === "ERRORE") {
    return (
      <div className="vd-esito vd-esito--critico">
        <p className="vd-esito-titolo">Generazione non riuscita</p>
        <p>{g.esito?.interrotta ? "La richiesta si è interrotta senza esito." : `Motivo: ${g.errore ?? "non indicato"}.`} Nessuna versione è stata registrata: puoi generare di nuovo.</p>
      </div>
    );
  }
  const nuove = g.esito?.nuove ?? [];
  return (
    <div className="vd-esito vd-esito--ok">
      <p className="vd-esito-titolo">Generazione completata {quandoBreve(g.conclusa_il) ?? ""}</p>
      <p>{nuove.length === 0
        ? "La configurazione non è cambiata: nessuna versione nuova."
        : `Versioni nuove: ${nuove.map(n => `${NOME_DOCUMENTO[n.tipo]} ${n.versione}`).join(", ")}.`}</p>
    </div>
  );
}

function DaCompilare({ v }: { v: Versione }) {
  const sezioni = v.da_compilare ?? [];
  return (
    <section aria-labelledby="vd-doc-compilare">
      <div className="vd-sezione-testa"><h2 id="vd-doc-compilare" className="vd-etichetta">Dati da compilare — {NOME_DOCUMENTO[v.tipo]}</h2></div>
      {sezioni.length === 0 ? (
        <Vuoto titolo="Nessun dato da compilare"><p>Ogni sezione di questa versione è generata da dati del servizio.</p></Vuoto>
      ) : (
        <>
          <p className="vd-nota">{plurale(v.campi_da_compilare, "dato manca", "dati mancano")} in {plurale(sezioni.length, "sezione", "sezioni")}: il documento li segnala come «DA COMPILARE» con chi deve fornirli. Dopo averli inseriti, genera di nuovo.</p>
          <div className="vd-da-compilare">
            {sezioni.map(s => (
              <div key={s.sezione} className="vd-da-compilare-sezione">
                <h3 id={`vd-dc-${s.sezione}`}>{s.numero} {s.titolo}</h3>
                <ul aria-labelledby={`vd-dc-${s.sezione}`}>
                  {s.campi.map(c => (
                    <li key={c.campo}>
                      <strong>{c.descrizione}</strong>
                      <span className="vd-sotto">Fornisce: {c.chi}{c.dove ? ` · dove: ${c.dove}` : ""}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </>
      )}
      {(v.da_verificare_elenco ?? []).length > 0 && (
        <p className="vd-nota">{plurale(v.da_verificare, "requisito è segnalato", "requisiti sono segnalati")} «da verificare sul testo ufficiale»: il testo normativo non è stato riscontrato e il documento non lo inventa.</p>
      )}
    </section>
  );
}

function Storico({ tipo, onTipo }: { tipo: TipoDocumento; onTipo: (t: TipoDocumento) => void }) {
  const q = useQuery({ queryKey: ["documentazione-versioni", tipo], queryFn: () => leggiVersioni(tipo) });
  return (
    <section aria-labelledby="vd-doc-storico">
      <div className="vd-sezione-testa"><h2 id="vd-doc-storico" className="vd-etichetta">Storico delle versioni</h2></div>
      <div className="vd-filtri">
        <div className="vd-campo">
          <label htmlFor="vd-doc-tipo">Documento</label>
          <select id="vd-doc-tipo" className="vd-input" value={tipo} onChange={e => onTipo(e.target.value as TipoDocumento)}>
            {TIPI_DOCUMENTO.map(t => <option key={t} value={t}>{NOME_DOCUMENTO[t]}</option>)}
          </select>
        </div>
      </div>
      {q.isLoading ? <InLettura testo="Lettura dello storico…" />
        : q.isError || !q.data ? <EsitoCritico titolo="Storico non disponibile" errore={q.error} onRiprova={() => q.refetch()} />
        : q.data.items.length === 0 ? (
          <Vuoto titolo="Nessuna versione di questo documento"><p>La prima versione nasce con «Genera la documentazione».</p></Vuoto>
        ) : (
          <table className="vd-tabellone vd-impila">
            <caption className="vd-sr">Versioni di {NOME_DOCUMENTO[tipo]}</caption>
            <thead><tr><th scope="col">Versione</th><th scope="col">Generata</th><th scope="col">Impronta del contenuto</th>
              <th scope="col">Sezioni cambiate</th><th scope="col">Archivio</th><th scope="col">Scarica</th></tr></thead>
            <tbody>{q.data.items.map(v => (
              <tr key={v.id}>
                <td data-label="Versione" className="vd-ora">{v.versione}</td>
                <td data-label="Generata">{quandoBreve(v.generata_il)}</td>
                <td data-label="Impronta del contenuto" className="vd-mono vd-a-capo" title={v.impronta_contenuto}>{breveImpronta(v.impronta_contenuto)}</td>
                <td data-label="Sezioni cambiate"><Cambiate v={v} /></td>
                <td data-label="Archivio"><Archivio v={v} /></td>
                <td data-label="Scarica"><Scarica v={v} /></td>
              </tr>
            ))}</tbody>
          </table>
        )}
    </section>
  );
}

export default function DocumentazioneServizioPage() {
  const qc = useQueryClient();
  const quadro = useQuery({ queryKey: ["documentazione-quadro"], queryFn: leggiQuadro });
  const [tipo, setTipo] = useState<TipoDocumento>("MANUALE_CONSERVAZIONE");
  const [richiesta, setRichiesta] = useState<string | null>(null);
  const genera = useMutation({ mutationFn: richiediGenerazione, onSuccess: g => setRichiesta(g.id) });
  const ultimaRichiesta = richiesta ?? quadro.data?.generazioni.find(g => g.stato === "IN_CORSO")?.id ?? null;
  const conclusa = () => {
    qc.invalidateQueries({ queryKey: ["documentazione-quadro"] });
    qc.invalidateQueries({ queryKey: ["documentazione-versioni"] });
  };
  const documenti = quadro.data?.documenti ?? [];
  const conVersione = documenti.filter(d => d.ultima);
  const scelto = documenti.find(d => d.tipo === tipo)?.ultima ?? null;
  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Documentazione del servizio</h1>
        <p>Manuale di conservazione, piano della sicurezza, guida e catalogo delle pagine generati dalla configurazione reale: ciò che il servizio non sa resta «da compilare».</p>
        <div className="vd-testa-comandi">
          <button type="button" className="vd-bottone" onClick={() => genera.mutate()} disabled={genera.isPending}>
            {genera.isPending ? "Richiesta in corso…" : "Genera la documentazione"}
          </button>
        </div>
      </div>
      <div className="vd-pila">
        {genera.isError && <EsitoCritico titolo="Generazione non avviata" errore={genera.error} azione="operazione" />}
        {ultimaRichiesta && <StatoGenerazione id={ultimaRichiesta} onConclusa={conclusa} />}
        {quadro.isLoading ? <InLettura testo="Lettura della documentazione…" />
          : quadro.isError || !quadro.data ? <EsitoCritico titolo="Documentazione non disponibile" errore={quadro.error} onRiprova={() => quadro.refetch()} />
          : (
            <>
              <section aria-labelledby="vd-doc-documenti">
                <div className="vd-sezione-testa"><h2 id="vd-doc-documenti" className="vd-etichetta">Documenti</h2></div>
                {conVersione.length === 0 ? (
                  <Vuoto titolo="Nessuna versione generata">
                    <p>Il servizio legge la configurazione in sola lettura e crea la prima versione di ogni documento, con impronta e archivio nel WORM.</p>
                    <div className="vd-comandi">
                      <button type="button" className="vd-bottone" onClick={() => genera.mutate()} disabled={genera.isPending}>Genera la documentazione</button>
                    </div>
                  </Vuoto>
                ) : (
                  <table className="vd-tabellone vd-impila">
                    <caption className="vd-sr">Ultima versione di ogni documento</caption>
                    <thead><tr><th scope="col">Documento</th><th scope="col">Versione</th><th scope="col">Generata</th>
                      <th scope="col">Da compilare</th><th scope="col">Archivio</th><th scope="col">Comandi</th></tr></thead>
                    <tbody>{documenti.map(({ tipo: t, ultima: v }) => (
                      <tr key={t}>
                        <th scope="row" className="vd-riga" data-label="Documento">
                          <button type="button" className="vd-link-bottone" aria-pressed={t === tipo} onClick={() => setTipo(t)}>{NOME_DOCUMENTO[t]}</button>
                        </th>
                        <td data-label="Versione">{v ? v.versione : "—"}</td>
                        <td data-label="Generata">{v ? quandoBreve(v.generata_il) : "mai"}</td>
                        <td data-label="Da compilare">{!v ? "—" : v.campi_da_compilare === 0
                          ? <span className="vd-stato vd-stato--ok">nessuno</span>
                          : <span className="vd-stato vd-stato--critico">{plurale(v.campi_da_compilare, "dato", "dati")}</span>}</td>
                        <td data-label="Archivio">{v ? <Archivio v={v} /> : "—"}</td>
                        <td data-label="Comandi">{v ? <><Scarica v={v} /><InviaAllaFirma v={v} /></> : "—"}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
                <p className="vd-nota">Ogni versione è immutabile e ha un'impronta SHA-256; la stessa configurazione non crea una versione nuova. La firma del responsabile si appone sulla versione stampabile: «Invia alla firma del responsabile» la mette in <Link to="/firme-responsabile" className="vd-riga-link">Firme del responsabile</Link>.</p>
              </section>
              {scelto && <DaCompilare v={scelto} />}
              <Storico tipo={tipo} onTipo={setTipo} />
            </>
          )}
      </div>
    </>
  );
}
