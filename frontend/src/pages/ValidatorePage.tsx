// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Validatore preventivo dal browser (contratto Q1: POST /api/v1/configurazione/valida).
 * Si carica l'indice JSON di un versamento (lo stesso di /versamento/ud) e il servizio
 * risponde con gli stessi rilievi del versamento, senza scrivere nulla. Il file resta
 * nel browser: si invia solo l'indice letto. Dichiara i controlli che solo il
 * versamento vero esegue (`controlli_non_eseguiti`). Sistema «Partenze».
 */
import { DragEvent, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { validaIndice, type Rilievo } from "../console/api";
import { plurale } from "../console/comuni";
import { EFFETTO, parola } from "../console/parole";

const MAX_BYTE = 2 * 1024 * 1024;

function TabellaRilievi({ righe, titolo }: { righe: Rilievo[]; titolo: string }) {
  return (
    <div className="vd-scorri">
      <table className="vd-tabellone vd-impila">
        <caption style={{ textAlign: "left", fontWeight: 700, paddingBottom: 6 }}>{titolo}</caption>
        <thead><tr><th scope="col">Regola</th><th scope="col">Dove</th><th scope="col">Che cosa</th><th scope="col">Effetto</th></tr></thead>
        <tbody>
          {righe.map((r, k) => {
            const ef = parola(EFFETTO, r.effetto);
            return (
              <tr key={k}>
                <td className="vd-ora" data-label="Regola"><span className="vd-mono" style={{ fontSize: 14 }}>{r.codice ?? r.regola}</span></td>
                <td data-label="Dove" className="vd-a-capo">
                  {r.campo ? <span className="vd-mono">{r.campo}</span> : <span className="vd-nota">indice</span>}
                  {r.documento && <span className="vd-sotto">documento {r.documento}</span>}
                </td>
                <td className="vd-cella-larga vd-a-capo" data-label="Che cosa">{r.messaggio}</td>
                <td data-label="Effetto"><span className={`vd-stato ${ef.classe}`}>{ef.testo}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ValidatorePage() {
  const ente = useEnte();
  const fileRef = useRef<HTMLInputElement>(null);
  const [nome, setNome] = useState<string | null>(null);
  const [indice, setIndice] = useState<unknown>(undefined);
  const [errFile, setErrFile] = useState<string | null>(null);
  const [sopra, setSopra] = useState(false);

  const valida = useMutation({ mutationFn: () => validaIndice(indice, ente.globale ? ente.tenantId : undefined) });

  const leggi = (f: File | null | undefined) => {
    valida.reset();
    setIndice(undefined); setErrFile(null); setNome(f?.name ?? null);
    if (!f) return;
    if (f.size > MAX_BYTE) { setErrFile("Il file supera 2 MB: un indice è molto più piccolo. Hai scelto il documento invece dell'indice?"); return; }
    f.text().then(t => {
      try { setIndice(JSON.parse(t)); }
      catch { setErrFile("Il file non è un JSON leggibile: controlla virgole e parentesi."); }
    }, () => setErrFile("Il file non si legge."));
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setSopra(false); leggi(e.dataTransfer.files?.[0]); };
  const r = valida.data;
  const classeArea = `vd-area-file${nome ? " vd-area-file--scelto" : ""}${sopra ? " vd-area-file--sopra" : ""}`;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Validatore preventivo</h1>
        <p>Controlla un indice di versamento con le regole in vigore per l'ente, prima di versare. Nulla viene versato né registrato.</p>
      </div>
      <div className="vd-pila">
        <fieldset className="vd-tappa">
          <legend><span className="vd-tappa-n" aria-hidden="true">1</span><span className="vd-tappa-titolo">Indice del versamento</span><span className="vd-nota">file JSON, lo stesso inviato a /versamento/ud</span></legend>
          {ente.globale && <div className="vd-filtri" style={{ marginBottom: 0 }}><SceltaEnte id="vd-val-ente" stato={ente} onCambia={() => valida.reset()} /></div>}
          <input ref={fileRef} id="vd-val-file" type="file" accept="application/json,.json" className="vd-sr"
                 onChange={e => leggi(e.target.files?.[0])} />
          <div className={classeArea} onDragOver={e => { e.preventDefault(); setSopra(true); }} onDragLeave={() => setSopra(false)} onDrop={onDrop}>
            <div style={{ flex: "1 1 260px", minWidth: 0 }}>
              {nome ? <div className="vd-area-file-nome">{nome}</div> : <div style={{ fontSize: 17, fontWeight: 600 }}>Trascina qui l'indice oppure sceglilo dal computer</div>}
              <div className="vd-nota">{nome ? (indice !== undefined ? "Letto: pronto da controllare" : errFile ? "Non leggibile" : "Lettura…") : "Il file non lascia il browser: si invia solo il suo contenuto"}</div>
            </div>
            <label htmlFor="vd-val-file" className="vd-bottone vd-bottone--secondario" style={{ cursor: "pointer" }} tabIndex={0}
                   onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileRef.current?.click(); } }}>
              {nome ? "Cambia file" : "Scegli il file"}
            </label>
          </div>
          {errFile && <span className="vd-stato vd-stato--critico" role="alert" style={{ whiteSpace: "normal" }}>{errFile}</span>}
          <div className="vd-comandi" style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 16 }}>
            <button type="button" className="vd-bottone" disabled={indice === undefined || valida.isPending || (ente.globale && !ente.tenantId)}
                    onClick={() => valida.mutate()}>
              {valida.isPending ? "Controllo in corso…" : "Controlla l'indice"}
            </button>
          </div>
        </fieldset>

        {valida.isError && (
          <EsitoCritico titolo="Controllo non eseguito" errore={valida.error} azione="operazione"
                        spiegazione="Non significa che l'indice sia sbagliato: il controllo non è avvenuto." onRiprova={() => valida.mutate()} />
        )}

        {r && (
          <section aria-labelledby="vd-val-esito" aria-live="polite" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            <div className={`vd-esito ${r.esito === "CONFORME" ? "vd-esito--ok" : "vd-esito--critico"}`}>
              <h2 id="vd-val-esito" className="vd-esito-titolo">
                {r.esito === "CONFORME" ? "Indice conforme: il versamento sarebbe accettato" : "Indice non conforme: il versamento sarebbe rifiutato"}
              </h2>
              <p>
                {plurale(r.rilievi.length, "rilievo bloccante", "rilievi bloccanti")} · {plurale(r.avvisi.length, "avviso", "avvisi")}
                {r.fasi_eseguite.length > 0 && <> · fasi controllate: {r.fasi_eseguite.join(", ")}</>}
              </p>
            </div>
            {r.rilievi.length > 0 && <TabellaRilievi righe={r.rilievi} titolo="Da correggere" />}
            {r.avvisi.length > 0 && <TabellaRilievi righe={r.avvisi} titolo="Avvisi (il versamento passa)" />}
            {/* R08 (D02): file per file, con l'esito del catalogo dei formati */}
            {!!r.file?.length && (
              <section aria-labelledby="vd-val-file">
                <h2 id="vd-val-file" className="vd-etichetta">File</h2>
                {!!r.file_rifiutati?.length && (
                  <p style={{ marginBlock: 8 }}><strong className="vd-stato vd-stato--critico">
                    {plurale(r.file_rifiutati.length, "file verrebbe rifiutato", "file verrebbero rifiutati")}</strong>: {r.file_rifiutati.join(", ")}</p>
                )}
                <table className="vd-tabellone vd-impila">
                  <thead><tr><th scope="col">File</th><th scope="col">Catalogo dei formati</th><th scope="col">Esito</th></tr></thead>
                  <tbody>
                    {r.file.map(f => (
                      <tr key={f.documento}>
                        <td data-label="File" className="vd-a-capo">{f.nome_file}{f.formato && <span className="vd-sotto vd-mono">{f.formato}</span>}</td>
                        <td data-label="Catalogo dei formati" className="vd-a-capo">{f.catalogo ? f.catalogo.motivo : <span className="vd-nota">formato non indicato</span>}</td>
                        <td data-label="Esito">
                          <span className={`vd-stato ${f.rifiutato ? "vd-stato--critico" : f.avvisi.length ? "vd-stato--info" : "vd-stato--ok"}`}>
                            {f.rifiutato ? "Rifiutato" : f.avvisi.length ? "Con avviso" : "Ammesso"}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
            {r.firme && (
              <p className="vd-nota">Firme: dopo il versamento si verificano al livello «{r.firme.descrizione}».</p>
            )}
            {!!r.controlli_non_eseguiti?.length && (
              <div className="vd-avviso vd-avviso--neutro">
                <p><strong>Che cosa controlla solo il versamento vero</strong></p>
                <ul style={{ paddingLeft: 20, marginTop: 4 }}>{r.controlli_non_eseguiti.map(c => <li key={c}>{c}</li>)}</ul>
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
