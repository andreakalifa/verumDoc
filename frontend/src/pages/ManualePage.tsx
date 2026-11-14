// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Manuale di conservazione — sistema «Partenze» (VD-DR-01).
 * VD-C-013: il manuale generato dal servizio descriveva funzioni inesistenti e
 * conteneva dati segnaposto. Questa pagina non dà nulla per buono.
 * g3-frontend: legge i campi DEFINITIVI di manuale.py (g2-dati §6.10, be-conformita):
 *  - firma: `meta.stato_firma` = {firmato, stato, motivo};
 *  - versione `meta.versione`, stato `meta.stato_documento`, data `meta.data_adozione`
 *    (poi `meta.data_generazione`), impronta `meta.impronta_contenuto_sha256`,
 *    riferimenti `meta.riferimenti_normativi`;
 *  - sezioni: `stato_sezione` (generata / parziale / da_realizzare / da_compilare /
 *    non_disponibile), `dati_da_compilare`, `contenuto` (prefisso «[DA …]» tolto
 *    perché lo stato è già mostrato), `funzioni` [{chiave, descrizione, stato, nota,
 *    riferimento}], `dati`;
 *  - anagrafica: `conservatore.<campo>` = {valore, stato: compilato | da compilare |
 *    non valido, motivo?};
 *  - catalogo `funzioni` e `riepilogo_sezioni`. Un errore del DB arriva come 200 con
 *    sezioni «non_disponibile»; un errore HTTP come 4xx/5xx con id di correlazione.
 */
import { giorno } from "../console/comuni";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchManuale } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { motivoErrore } from "../api/errori";
import { EsitoCritico } from "../components/EsitoCritico";

/** Revisione R17 (R17-20): giorno in ora italiana (Europe/Rome), non il giorno UTC. */
const dataIt = (iso?: string | null) => (iso ? giorno(iso) ?? String(iso) : null);

// ── Stato di una sezione (stato_sezione) ───────────────────────────
const STATO_SEZIONE: Record<string, { testo: string; classe: string; spiegazione: string }> = {
  generata:        { testo: "Corrisponde al sistema", classe: "vd-stato--ok", spiegazione: "Generata dai dati e dalle funzioni realizzate del servizio." },
  parziale:        { testo: "In parte da realizzare", classe: "vd-stato--rischio", spiegazione: "Descrive anche funzioni realizzate solo in parte o non ancora realizzate." },
  da_realizzare:   { testo: "Da realizzare", classe: "vd-stato--rischio", spiegazione: "Descrive funzioni che il servizio non ha ancora." },
  da_compilare:    { testo: "Da compilare", classe: "vd-stato--critico", spiegazione: "Mancano dati che il conservatore deve fornire: il testo contiene segnaposto." },
  non_disponibile: { testo: "Non disponibile", classe: "vd-stato--critico", spiegazione: "Il servizio non ha potuto leggere i dati della sezione (archivio dati non raggiungibile)." },
};
const statoSezione = (s?: string) =>
  (s ? STATO_SEZIONE[s] : undefined) ?? { testo: s ? `Stato «${s}»` : "Stato non indicato", classe: "vd-stato--neutro", spiegazione: "Il servizio non indica se la sezione corrisponde al sistema reale." };
const ORDINE_STATI = ["generata", "parziale", "da_realizzare", "da_compilare", "non_disponibile"];

/** Stato di una funzione descritta dal manuale (STATO_FUNZIONI). */
const STATO_FUNZIONE: Record<string, { testo: string; classe: string }> = {
  generata:      { testo: "Realizzata", classe: "vd-stato--ok" },
  parziale:      { testo: "In parte", classe: "vd-stato--rischio" },
  da_realizzare: { testo: "Da realizzare", classe: "vd-stato--critico" },
};
const statoFunzione = (s?: string) =>
  (s ? STATO_FUNZIONE[s] : undefined) ?? { testo: s ? s.replace(/_/g, " ") : "non indicato", classe: "vd-stato--neutro" };

/** Toglie il prefisso «[DA COMPILARE]», «[DA REALIZZARE]»… già reso dallo stato. */
const senzaPrefisso = (t: unknown) => typeof t === "string" ? t.replace(/^\s*\[(DA COMPILARE|DA REALIZZARE|IN PARTE DA REALIZZARE|NON DISPONIBILE)\]\s*/, "") : t;

// ── Anagrafica del conservatore (conservatore.*) ───────────────────
const ANAGRAFICA: { chiave: string; nome: string; mono?: boolean }[] = [
  { chiave: "denominazione", nome: "Conservatore" },
  { chiave: "codice_fiscale", nome: "Codice fiscale", mono: true },
  { chiave: "partita_iva", nome: "Partita IVA", mono: true },
  { chiave: "sede_legale", nome: "Sede legale" },
  { chiave: "pec", nome: "PEC" },
  { chiave: "email_contatto", nome: "Posta elettronica di contatto" },
  { chiave: "responsabile_servizio_nome", nome: "Responsabile del servizio" },
  { chiave: "responsabile_servizio_cf", nome: "Codice fiscale del responsabile", mono: true },
  { chiave: "responsabile_funzione_archivistica_nome", nome: "Responsabile della funzione archivistica" },
  { chiave: "responsabile_sicurezza_nome", nome: "Responsabile della sicurezza" },
  { chiave: "responsabile_protezione_dati_nome", nome: "Responsabile della protezione dei dati" },
  { chiave: "ubicazione_data_center", nome: "Ubicazione del centro dati" },
];

// ── Contenuto libero (dati di una sezione): testo, elenco o struttura ─
const chiaveLeggibile = (k: string) => (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ");

function Contenuto({ v, livello = 0 }: { v: unknown; livello?: number }) {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v !== "object") return <p style={{ whiteSpace: "pre-line" }}>{String(v)}</p>;
  if (livello > 3) return <pre className="vd-mono vd-a-capo" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(v, null, 2)}</pre>;
  if (Array.isArray(v)) {
    return (
      <ul>
        {v.map((x, i) => (
          <li key={i}>{typeof x === "object" && x !== null ? <Contenuto v={x} livello={livello + 1} /> : String(x)}</li>
        ))}
      </ul>
    );
  }
  return (
    <dl>
      {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{chiaveLeggibile(k)}</dt>
          <dd>{typeof x === "object" && x !== null ? <Contenuto v={x} livello={livello + 1} /> : x === "" || x === null ? <span className="vd-nota">vuoto</span> : String(x)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Funzioni({ voci }: { voci: any[] }) {
  if (voci.length === 0) return null;
  return (
    <ul style={{ listStyle: "none", borderTop: "1px solid var(--vd-filetto)" }}>
      {voci.map((f, i) => {
        const st = statoFunzione(f.stato);
        return (
          <li key={f.chiave ?? i} style={{ paddingBlock: 8, borderBottom: "1px solid var(--vd-filetto)" }}>
            <span className={`vd-stato ${st.classe}`}>{st.testo}:</span>{" "}
            <strong>{f.descrizione ?? f.chiave}</strong>
            {(f.nota || f.riferimento) && (
              <span className="vd-nota" style={{ display: "block" }}>{[f.nota, f.riferimento].filter(Boolean).join(" · ")}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default function ManualePage() {
  const { user } = useAuth();
  const tenantId = user?.tenant_id ?? "";

  // Il manuale è del conservatore, non dell'ente: la lettura non dipende dall'ente del profilo.
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["manuale", tenantId],
    queryFn: () => fetchManuale(tenantId),
  });

  const scarica = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `manuale-conservazione-dati-${String(data?.meta?.versione ?? "senza-versione").replace(/\s+/g, "-")}-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const erroreServizio: string | null = isError ? motivoErrore(error) : null;

  const meta = data?.meta ?? {};
  const sezioni: any[] = Array.isArray(data?.sezioni) ? data.sezioni : [];
  const catalogo: any[] = Array.isArray(data?.funzioni) ? data.funzioni : [];
  const conservatore: Record<string, any> = data?.conservatore ?? {};
  const firma = meta.stato_firma && typeof meta.stato_firma === "object" ? meta.stato_firma : null;
  const firmato: boolean | null = typeof firma?.firmato === "boolean" ? firma.firmato : null;
  const riferimenti: string[] = Array.isArray(meta.riferimenti_normativi) ? meta.riferimenti_normativi : [];

  // Riepilogo: dal servizio, altrimenti contato sulle sezioni
  const riepilogo: Record<string, number> = data?.riepilogo_sezioni && typeof data.riepilogo_sezioni === "object"
    ? data.riepilogo_sezioni
    : sezioni.reduce((acc: Record<string, number>, s: any) => { const k = s.stato_sezione ?? "ignoto"; acc[k] = (acc[k] ?? 0) + 1; return acc; }, {});
  const conta = (k: string) => riepilogo[k] ?? 0;
  const statiPresenti = [...ORDINE_STATI, ...Object.keys(riepilogo).filter(k => !ORDINE_STATI.includes(k))].filter(k => conta(k) > 0);

  const anagrafica = ANAGRAFICA.map(r => ({ ...r, c: conservatore[r.chiave] as { valore?: string | null; stato?: string; motivo?: string } | undefined }));
  const anagraficaIncompleta = anagrafica.filter(r => r.c?.stato !== "compilato");

  const motivi: string[] = [];
  if (firmato !== true) motivi.push(firma?.motivo ? `Il manuale non è firmato: ${firma.motivo}.` : "Il servizio non indica che il manuale sia firmato dal responsabile del servizio.");
  if (meta.stato_documento && meta.stato_documento !== "completo") motivi.push(`Il documento è nello stato «${meta.stato_documento}».`);
  if (!meta.data_adozione) motivi.push("La data di adozione non è stata indicata.");
  if (conta("da_compilare")) motivi.push(`${conta("da_compilare")} ${conta("da_compilare") === 1 ? "sezione ha" : "sezioni hanno"} dati da compilare.`);
  if (conta("da_realizzare") + conta("parziale")) motivi.push(`${conta("da_realizzare") + conta("parziale")} ${conta("da_realizzare") + conta("parziale") === 1 ? "sezione descrive" : "sezioni descrivono"} funzioni non ancora realizzate o realizzate in parte.`);
  if (conta("non_disponibile")) motivi.push(`${conta("non_disponibile")} ${conta("non_disponibile") === 1 ? "sezione non è stata generata" : "sezioni non sono state generate"}: il servizio non ha potuto leggere i dati.`);
  if (anagraficaIncompleta.length) motivi.push(`Anagrafica del conservatore incompleta: ${anagraficaIncompleta.length} ${anagraficaIncompleta.length === 1 ? "dato" : "dati"} da compilare o non validi.`);
  if (sezioni.length === 0) motivi.push("Il manuale non contiene sezioni.");

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Manuale di conservazione</h1>
        <span className="vd-nota">
          Il documento che descrive come il servizio conserva. Vale solo se corrisponde al sistema reale ed è firmato dal responsabile del servizio.
        </span>
        <div className="vd-testa-comandi">
          {/* revisione R17 (R17-17): la versione leggibile e firmabile è quella stampabile generata */}
          <Link to="/documentazione-servizio" className="vd-bottone">Versione stampabile da firmare</Link>
          {data && !erroreServizio && (
            <button type="button" className="vd-bottone vd-bottone--secondario" onClick={scarica}>
              Scarica i dati del manuale (JSON)
            </button>
          )}
        </div>
      </div>

      {isLoading ? (
        <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Generazione del manuale in corso…</p>
      ) : erroreServizio ? (
        <EsitoCritico titolo="Manuale non disponibile" errore={error}
                      spiegazione="Il manuale non è stato generato: non significa che manchi."
                      onRiprova={() => refetch()} inRiprova={isFetching} />
      ) : (
        <div className="vd-pila">
          {/* ── Esito complessivo ─────────────────────────────────── */}
          {motivi.length > 0 ? (
            <div className="vd-esito vd-esito--rischio" role="status">
              <p className="vd-esito-titolo">Manuale non presentabile così com'è</p>
              <ul style={{ paddingLeft: 20, marginTop: 6 }}>
                {motivi.map(m => <li key={m} style={{ marginTop: 2 }}>{m}</li>)}
              </ul>
            </div>
          ) : (
            <div className="vd-esito vd-esito--ok" role="status">
              <p className="vd-esito-titolo">Manuale firmato, completo, tutte le sezioni corrispondono al sistema</p>
              <p className="vd-nota">Secondo quanto indicato dal servizio.</p>
            </div>
          )}

          {/* ── Stato del documento ───────────────────────────────── */}
          <section aria-labelledby="vdc-documento">
            <div className="vd-sezione-testa">
              <h2 id="vdc-documento" className="vd-etichetta">Stato del documento</h2>
            </div>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-coppie" style={{ maxWidth: 820 }}>
                <caption className="vd-sr">Versione, stato, data, firma e sezioni del manuale</caption>
                <tbody>
                  <tr><th scope="row" className="vd-riga">Versione</th>
                      <td>{meta.versione ? <span style={{ fontStretch: "75%", fontWeight: 700, fontSize: 20 }}>{String(meta.versione)}</span> : <span className="vd-stato vd-stato--neutro">Non indicata</span>}
                        {meta.stato_documento && (
                          <span className={`vd-sotto vd-stato ${meta.stato_documento === "completo" ? "vd-stato--ok" : "vd-stato--rischio"}`}>
                            {meta.stato_documento === "completo" ? "Documento completo" : `Documento in ${String(meta.stato_documento)}`}
                          </span>
                        )}</td></tr>
                  <tr><th scope="row" className="vd-riga">Adottato il</th>
                      <td>{dataIt(meta.data_adozione) ?? <span className="vd-stato vd-stato--rischio">Data di adozione non indicata</span>}
                        {meta.data_generazione && <span className="vd-sotto">testo generato il {dataIt(meta.data_generazione)}</span>}</td></tr>
                  <tr><th scope="row" className="vd-riga">Firma del responsabile</th>
                      <td>
                        {firmato === true ? <span className="vd-stato vd-stato--ok">Firmato</span>
                          : firmato === false ? <span className="vd-stato vd-stato--rischio">{firma?.stato ? firma.stato.charAt(0).toUpperCase() + firma.stato.slice(1) : "Non firmato"}</span>
                          : <span className="vd-stato vd-stato--neutro">Stato della firma non indicato dal servizio</span>}
                        {firma?.motivo && <span className="vd-sotto vd-a-capo">{firma.motivo}</span>}
                        {firmato !== true && (
                          <span className="vd-sotto">
                            Firma personale con OTP:{" "}
                            <Link to="/firme-responsabile" className="vd-riga-link">Firme del responsabile</Link>
                          </span>
                        )}
                      </td></tr>
                  <tr><th scope="row" className="vd-riga">Sezioni</th>
                      <td>
                        {sezioni.length.toLocaleString("it-IT")} in tutto
                        <span className="vd-sotto">
                          {statiPresenti.map(k => `${conta(k)} ${statoSezione(k).testo.toLowerCase()}`).join(" · ") || "nessuna"}
                        </span>
                      </td></tr>
                  {meta.impronta_contenuto_sha256 && (
                    <tr><th scope="row" className="vd-riga">Impronta del contenuto</th>
                        <td><span className="vd-mono vd-a-capo">{String(meta.impronta_contenuto_sha256)}</span>
                          <span className="vd-sotto">SHA-256 del testo generato: cambia se cambia una qualsiasi sezione.</span></td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {/* ── Anagrafica del conservatore ──────────────────────── */}
          <section aria-labelledby="vdc-anagrafica">
            <div className="vd-sezione-testa">
              <h2 id="vdc-anagrafica" className="vd-etichetta">Anagrafica del conservatore</h2>
              <span className="vd-nota">Dalla configurazione del servizio; un dato di comodo resta «da compilare»</span>
            </div>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-coppie" style={{ maxWidth: 820 }}>
                <caption className="vd-sr">Dati del conservatore riportati nel manuale</caption>
                <tbody>
                  {anagrafica.map(r => (
                    <tr key={r.chiave}><th scope="row" className="vd-riga">{r.nome}</th>
                      <td className="vd-a-capo">
                        {r.c?.stato === "compilato" && r.c.valore
                          ? <span className={r.mono ? "vd-mono" : undefined}>{String(r.c.valore)}</span>
                          : r.c?.stato === "non valido"
                            ? <span className="vd-stato vd-stato--critico">Non valido</span>
                            : r.c?.stato === "da compilare"
                              ? <span className="vd-stato vd-stato--critico">Da compilare</span>
                              : <span className="vd-stato vd-stato--neutro">Non indicato dal servizio</span>}
                        {r.c?.motivo && <span className="vd-sotto">{r.c.motivo}</span>}
                      </td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* ── Sezioni ───────────────────────────────────────────── */}
          <section aria-labelledby="vdc-sezioni">
            <div className="vd-sezione-testa">
              <h2 id="vdc-sezioni" className="vd-etichetta">Sezioni del manuale</h2>
              <span className="vd-nota">Apri una sezione per leggerne il testo, le funzioni descritte e i dati mancanti</span>
            </div>
            {sezioni.length === 0 ? (
              <p className="vd-nota" style={{ paddingBlock: 12 }}>Il servizio non ha restituito sezioni.</p>
            ) : (
              <div className="vd-voci" style={{ maxWidth: 1040 }}>
                {sezioni.map((sez: any, idx: number) => {
                  const st = statoSezione(sez.stato_sezione);
                  const daCompilare: string[] = Array.isArray(sez.dati_da_compilare) ? sez.dati_da_compilare : [];
                  const funzioni: any[] = Array.isArray(sez.funzioni) ? sez.funzioni : [];
                  const funzioniAperte = funzioni.filter(f => f.stato !== "generata");
                  // I dati del conservatore sono già nell'anagrafica qui sopra
                  const dati = sez.dati && typeof sez.dati === "object" && !("conservatore" in sez.dati) ? sez.dati : null;
                  return (
                    <details key={idx} className="vd-voce">
                      <summary>
                        <span className="vd-voce-num">{sez.numero ?? idx + 1}</span>
                        <span className="vd-voce-titolo">{sez.titolo || "Sezione senza titolo"}</span>
                        <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
                      </summary>
                      <div className="vd-voce-corpo">
                        <p className={`vd-stato ${st.classe}`} style={{ whiteSpace: "normal", fontSize: 15 }}>{st.spiegazione}</p>
                        {daCompilare.length > 0 && (
                          <p style={{ fontSize: 15 }}><strong>Dati da compilare:</strong> {daCompilare.join(", ")}.</p>
                        )}
                        <Contenuto v={senzaPrefisso(sez.contenuto)} />
                        {funzioniAperte.length > 0 && (
                          <div>
                            <p style={{ fontWeight: 700 }}>Funzioni descritte non ancora realizzate del tutto</p>
                            <Funzioni voci={funzioniAperte} />
                          </div>
                        )}
                        {dati && <Contenuto v={dati} />}
                      </div>
                    </details>
                  );
                })}
              </div>
            )}
          </section>

          {/* ── Catalogo delle funzioni ──────────────────────────── */}
          {catalogo.length > 0 && (
            <section aria-labelledby="vdc-funzioni">
              <div className="vd-sezione-testa">
                <h2 id="vdc-funzioni" className="vd-etichetta">Funzioni del servizio</h2>
                <span className="vd-nota">
                  {catalogo.filter(f => f.stato === "generata").length} realizzate · {catalogo.filter(f => f.stato === "parziale").length} in parte · {catalogo.filter(f => f.stato === "da_realizzare").length} da realizzare
                </span>
              </div>
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Stato reale delle funzioni descritte dal manuale</caption>
                  <thead><tr><th scope="col">Funzione</th><th scope="col">Stato</th><th scope="col">Nota e riferimento</th></tr></thead>
                  <tbody>
                    {catalogo.map((f, i) => {
                      const st = statoFunzione(f.stato);
                      return (
                        <tr key={f.chiave ?? i}>
                          <td style={{ fontWeight: 600, minWidth: 0 }} className="vd-a-capo" data-label="Funzione">{f.descrizione ?? f.chiave}</td>
                          <td className={`vd-stato ${st.classe}`} data-label="Stato">{st.testo}</td>
                          <td className="vd-a-capo" style={{ minWidth: 0 }} data-label="Nota e riferimento">{f.nota ?? "—"}{f.riferimento && <span className="vd-sotto">{f.riferimento}</span>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {riferimenti.length > 0 && (
            <section aria-labelledby="vdc-riferimenti">
              <div className="vd-sezione-testa">
                <h2 id="vdc-riferimenti" className="vd-etichetta">Riferimenti normativi</h2>
              </div>
              <ul className="vd-elenco" style={{ maxWidth: 820 }}>{riferimenti.map(r => <li key={r}>{r}</li>)}</ul>
            </section>
          )}
        </div>
      )}
    </>
  );
}
