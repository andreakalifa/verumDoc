// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Versa un documento — sistema grafico «Partenze» (VD-DR-01).
 * A sinistra il modulo in tre tappe; a destra il tabellone del versamento:
 * ogni fase compare con l'ora in cui è stata raggiunta.
 * La logica di invio e di interrogazione dello stato è quella precedente.
 * Revisione R17 (R17-05/06): nessuna classe predefinita scritta nel codice (si sceglie fra
 * quelle attive dell'ente: valore = codice, etichetta = nome) e i metadati obbligatori
 * della classe scelta (profilo della classe, `metadati_obbligatori`) compaiono nel modulo.
 */
import { useState, useRef, DragEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { leggiErrore } from "../api/errori";
import { MODALITA_FORMAZIONE } from "../data/modalitaFormazione";
import { nomeMetadato } from "../data/allegato5";

async function sha256File(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, "0")).join("");
}

type Step =
  | { id: "idle" }
  | { id: "hashing"; pct: number }
  | { id: "sending" }
  | { id: "validating"; pdv_id: string }
  | { id: "building"; pdv_id: string }
  | { id: "conserved"; pdv_id: string; aip_id?: string; urn?: string }
  | { id: "error"; msg: string };

type Fase = "hashing" | "sending" | "validating" | "building" | "conserved";
const FASI: { key: Fase; label: string }[] = [
  { key: "hashing",    label: "Calcolo dell'impronta" },
  { key: "sending",    label: "Invio del pacchetto di versamento" },
  { key: "validating", label: "Verifica di metadati e formato" },
  { key: "building",   label: "Costruzione del pacchetto di archiviazione" },
  { key: "conserved",  label: "Iscrizione nella catena delle impronte" },
];


interface ClasseVersamento { codice: string; nome: string; metadati_obbligatori: string[] }
/** Metadati che il modulo chiede sempre (Allegato 5) o fra i facoltativi. */
const CAMPI_DEL_MODULO = new Set([
  "identificativo_documento", "modalita_formazione", "tipologia_documentale", "data_documento",
  "soggetto_produttore_id", "soggetto_produttore_nome", "oggetto", "lingua",
  "numero_registrazione", "classificazione",
]);

const adesso = () => new Date().toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/**
 * Una sottomissione = un versamento: stessa Idempotency-Key (VD-C-083) e stessi
 * identificativi generati sui ritentativi dello stesso modulo, nuova chiave appena
 * cambia un dato o dopo un esito positivo. Così un ritentativo dopo un errore di rete
 * non crea un secondo PdV.
 */
type Sottomissione = { firma: string; chiave: string; docId: string; numero: string };

function Tappa({ n, titolo, children, nota }: { n: number; titolo: string; nota?: string; children: React.ReactNode }) {
  return (
    <fieldset className="vd-tappa">
      <legend>
        <span aria-hidden="true" className="vd-tappa-n">{n}</span>
        <span className="vd-tappa-titolo">{titolo}</span>
        {nota && <span className="vd-nota">{nota}</span>}
      </legend>
      {children}
    </fieldset>
  );
}

function Campo({ id, label, aiuto, obbligatorio, children }: { id: string; label: string; aiuto?: string; obbligatorio?: boolean; children: React.ReactNode }) {
  return (
    <div className="vd-campo">
      <label htmlFor={id}>{label}{obbligatorio && <span aria-hidden="true"> *</span>}{obbligatorio && <span className="vd-sr"> (obbligatorio)</span>}</label>
      {children}
      {aiuto && <span id={`${id}-aiuto`} className="vd-aiuto">{aiuto}</span>}
    </div>
  );
}

const griglia = (min: number): React.CSSProperties => ({
  display: "grid", gridTemplateColumns: `repeat(auto-fill, minmax(min(${min}px, 100%), 1fr))`, gap: "16px 20px",
});

export default function VersamentoLivePage() {
  const { user, hasScope } = useAuth();

  const { data: classiData } = useQuery({
    queryKey: ["classi-versamento", user?.tenant_id],
    queryFn: () => api.get(`/dashboard/classi-documentali?attiva=true`).then((r) => r.data),
    enabled: !!user?.tenant_id,
    staleTime: 5 * 60 * 1000,
  });
  const classiDocumentali: ClasseVersamento[] = (Array.isArray(classiData?.items) ? classiData.items : [])
    .filter((c: any) => c?.codice)
    .map((c: any) => ({ codice: String(c.codice), nome: String(c.nome || c.codice),
                        metadati_obbligatori: Array.isArray(c.metadati_obbligatori) ? c.metadati_obbligatori.map(String) : [] }));
  const senzaClassi = !!classiData && classiDocumentali.length === 0;

  const [file, setFile]     = useState<File | null>(null);
  const [trascina, setTrascina] = useState(false);
  const [classe, setClasse] = useState("");
  const [anno, setAnno]     = useState(new Date().getFullYear().toString());
  const [numero, setNumero] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // Metadati Allegato 5 — 7 campi obbligatori
  const [metaId, setMetaId]             = useState("");
  const [metaModalita, setMetaModalita] = useState("a");
  const [metaTipo, setMetaTipo]         = useState("");
  const [metaData, setMetaData]         = useState("");
  const [metaProdId, setMetaProdId]     = useState("");
  const [metaProdNome, setMetaProdNome] = useState("");
  const [metaOggetto, setMetaOggetto]   = useState("");
  const [showAvanzati, setShowAvanzati] = useState(false);
  const [metaProtocollo, setMetaProtocollo] = useState("");
  const [metaClassifica, setMetaClassifica] = useState("");
  const [metaLingua, setMetaLingua]     = useState("it");
  // metadati obbligatori della classe che il modulo non chiede già
  const [extra, setExtra] = useState<Record<string, string>>({});
  const classeScelta = classiDocumentali.find(c => c.codice === classe);
  const obbligatoriClasse = classeScelta?.metadati_obbligatori ?? [];
  const extraClasse = obbligatoriClasse.filter(k => !CAMPI_DEL_MODULO.has(k));
  const protocolloObbligatorio = obbligatoriClasse.includes("numero_registrazione");
  const classificaObbligatoria = obbligatoriClasse.includes("classificazione");

  const sottomissione = useRef<Sottomissione | null>(null);
  const [step, setStep]     = useState<Step>({ id: "idle" });
  const [orari, setOrari]   = useState<Partial<Record<Fase, string>>>({});
  const [result, setResult] = useState<any>(null);
  const [log, setLog]       = useState<string[]>([]);
  const [tentato, setTentato] = useState(false);

  const addLog = (msg: string) => setLog(l => [...l, `${adesso()}  ${msg}`]);
  const vaiA = (s: Step) => {
    setStep(s);
    if (s.id !== "idle" && s.id !== "error") {
      const k = s.id as Fase;
      setOrari(o => (o[k] ? o : { ...o, [k]: adesso() }));
    }
  };

  const mancanti = [
    !file && "documento",
    !classeScelta && "classe documentale",
    protocolloObbligatorio && !metaProtocollo.trim() && "numero di protocollo",
    classificaObbligatoria && !metaClassifica.trim() && "classificazione",
    ...extraClasse.filter(k => !(extra[k] ?? "").trim()).map(k => nomeMetadato(k).toLowerCase()),
    !metaId && "identificativo",
    !metaTipo && "tipologia",
    !metaData && "data del documento",
    !metaProdId && "codice fiscale o partita IVA del produttore",
    !metaProdNome && "nome del produttore",
    metaOggetto.length < 5 && "oggetto (almeno 5 caratteri)",
  ].filter(Boolean) as string[];
  const metadatiObbligatoriCompilati = () => mancanti.filter(m => m !== "documento").length === 0;
  const inCorso = !["idle", "error", "conserved"].includes(step.id);

  const startVersamento = async () => {
    setTentato(true);
    if (!file || !metadatiObbligatoriCompilati()) return;

    setLog([]);
    setResult(null);
    setOrari({});

    try {
      vaiA({ id: "hashing", pct: 0 });
      addLog(`Calcolo SHA-256 di ${file.name} (${(file.size / 1024).toFixed(1)} KB)`);
      const hash = await sha256File(file);
      addLog(`Impronta SHA-256: ${hash}`);

      vaiA({ id: "sending" });
      const firma = JSON.stringify([
        file.name, file.size, file.lastModified, hash, classe, anno, numero, metaId, metaModalita,
        metaTipo, metaData, metaProdId, metaProdNome, metaOggetto, metaProtocollo, metaClassifica, metaLingua,
        extraClasse.map(k => [k, extra[k] ?? ""]),
      ]);
      if (sottomissione.current?.firma !== firma) {
        const istante = Date.now();
        sottomissione.current = { firma, chiave: crypto.randomUUID(), docId: `doc-${istante}`, numero: `DOC-${istante}` };
      }
      const invio = sottomissione.current;
      const parametri: any = {
        identificativo_documento: metaId,
        modalita_formazione: metaModalita,
        tipologia_documentale: metaTipo || classe,
        data_documento: metaData,
        soggetto_produttore_id: metaProdId,
        soggetto_produttore_nome: metaProdNome,
        oggetto: metaOggetto,
        lingua: metaLingua,
      };
      if (metaProtocollo) parametri.numero_registrazione = metaProtocollo;
      if (metaClassifica) parametri.classificazione = metaClassifica;
      for (const k of extraClasse) parametri[k] = (extra[k] ?? "").trim();

      const indice = JSON.stringify({
        classe_documentale: classe,
        chiave: { anno, numero: numero || invio.numero, registro: "PRINCIPALE" },
        documenti: [{
          id: invio.docId,
          tipo: "PRINCIPALE",
          nome_file: file.name,
          formato: file.type || "application/pdf",
          hash: { impronta: hash, algoritmo: "SHA-256" },
          parametri,
        }],
      });

      addLog("Invio del pacchetto di versamento al gateway");
      const form = new FormData();
      form.append("indice", indice);
      form.append("files", file, file.name);

      const resp = await api.post("/v1/versamento/ud", form, {
        headers: { "Content-Type": "multipart/form-data", "Idempotency-Key": invio.chiave },
      });
      sottomissione.current = null; // esito ricevuto: il prossimo invio è un versamento nuovo
      const { pdv_id } = resp.data;
      addLog(resp.status === 200
        ? `Pacchetto già ricevuto con questo invio, identificativo ${pdv_id}`
        : `Pacchetto accettato, identificativo ${pdv_id}`);
      for (const avviso of (resp.data.avvisi ?? []) as { codice: string; messaggio: string }[]) {
        addLog(`Avviso ${avviso.codice}: ${avviso.messaggio}`);
      }

      vaiA({ id: "validating", pdv_id });

      let conserved = false;
      for (let i = 0; i < 40 && !conserved; i++) {
        await new Promise(r => setTimeout(r, 2000));
        try {
          const stato = await api.get(`/v1/versamento/${pdv_id}`);
          const s = stato.data.stato;
          addLog(`Stato: ${s}`);
          if (s === "IN_BUILD" || s === "AIP_IN_BUILD") {
            vaiA({ id: "building", pdv_id });
          } else if (s === "IN_CATENA" || s === "CONSERVATO") {
            vaiA({ id: "conserved", pdv_id });
            setResult(stato.data);
            addLog(`Conservato${stato.data.urn ? `, segnatura ${stato.data.urn}` : ""}`);
            conserved = true;
          } else if (s === "ERRORE") {
            throw Object.assign(new Error(stato.data.errori?.[0]?.msg || "Errore nella lavorazione del pacchetto"), { rifiuto: true });
          }
        } catch (e: any) {
          if (e?.rifiuto) throw e; // errori di rete: si riprova al giro successivo
        }
      }
      if (!conserved) addLog("Nessun esito definitivo dopo 80 secondi: controlla il pacchetto in «Pacchetti in arrivo»");
    } catch (e: any) {
      const msg = leggiErrore(e).testo || e.message || "Errore sconosciuto";
      setStep({ id: "error", msg });
      addLog(`Rifiutato: ${msg}`);
    }
  };

  const scegliFile = (f: File | null) => { setFile(f); };
  const onDrop = (e: DragEvent) => {
    e.preventDefault(); setTrascina(false);
    const f = e.dataTransfer.files?.[0]; if (f) scegliFile(f);
  };

  if (!hasScope("versamento:ud")) {
    return (
      <>
        <div className="vd-testa-pagina"><h1>Versamento non abilitato</h1></div>
        <div className="vd-vuoto">
          <p className="vd-vuoto-titolo">Il tuo profilo non può versare documenti</p>
          <p>Serve l'abilitazione «versamento», che concede l'amministrazione dell'ente.</p>
          <div className="vd-comandi"><Link to="/versamenti" className="vd-comando-testo">Vedi i pacchetti in arrivo</Link></div>
        </div>
      </>
    );
  }

  const invalido = (vuoto: boolean) => (tentato && vuoto ? "true" : undefined);
  const faseIdx = FASI.findIndex(f => f.key === step.id);

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Versa un documento</h1>
        <p>Un documento con i suoi metadati diventa un pacchetto di versamento.</p>
      </div>

      <div className="vd-affianca">
        {/* ── Modulo ───────────────────────────────────────────────── */}
        <form onSubmit={e => { e.preventDefault(); startVersamento(); }} noValidate
              style={{ flex: "999 1 560px", minWidth: 0, display: "flex", flexDirection: "column", gap: 36 }}>
          <Tappa n={1} titolo="Documento">
            <input ref={fileRef} id="vd-file" type="file" className="vd-sr"
                   onChange={e => scegliFile(e.target.files?.[0] || null)} />
            <div onDragOver={e => { e.preventDefault(); setTrascina(true); }} onDragLeave={() => setTrascina(false)} onDrop={onDrop}
                 style={{
                   border: `2px ${file ? "solid" : "dashed"} ${trascina || file ? "var(--vd-testo)" : "var(--vd-bordo-campo)"}`,
                   background: trascina ? "var(--vd-giallo-velato)" : "var(--vd-carta)",
                   padding: "18px 20px", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "12px 20px",
                 }}>
              {file ? (
                <div style={{ flex: "1 1 260px", minWidth: 0 }}>
                  <div style={{ fontSize: 17, fontWeight: 700, overflowWrap: "anywhere" }}>{file.name}</div>
                  <div className="vd-nota">{(file.size / 1024).toLocaleString("it-IT", { maximumFractionDigits: 1 })} KB · {file.type || "tipo non riconosciuto"}</div>
                </div>
              ) : (
                <div style={{ flex: "1 1 260px", minWidth: 0 }}>
                  <div style={{ fontSize: 17, fontWeight: 600 }}>Trascina qui il file oppure sceglilo dal computer</div>
                  <div className="vd-nota">PDF, P7M, XML, TIFF fino a 100 MB</div>
                </div>
              )}
              <label htmlFor="vd-file" className="vd-bottone vd-bottone--secondario" style={{ cursor: "pointer" }}
                     tabIndex={0} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileRef.current?.click(); } }}>
                {file ? "Cambia file" : "Scegli il file"}
              </label>
            </div>
            {tentato && !file && <span className="vd-stato vd-stato--critico">Scegli il documento da versare.</span>}
          </Tappa>

          <Tappa n={2} titolo="Dati archivistici">
            {senzaClassi && (
              <p role="alert" style={{ fontSize: 15, borderTop: "2px solid var(--vd-rischio)", paddingTop: 8 }}>
                <strong className="vd-stato--rischio">Nessuna classe documentale attiva.</strong> L'ente non ha classi attive:
                il versamento non è possibile finché l'amministrazione dell'ente non ne attiva una.
              </p>
            )}
            <div style={griglia(200)}>
              <Campo id="vd-classe" label="Classe documentale" obbligatorio
                     aiuto={classeScelta && obbligatoriClasse.length ? `Metadati obbligatori della classe: ${obbligatoriClasse.map(nomeMetadato).join(", ")}` : undefined}>
                <select id="vd-classe" className="vd-input" value={classe} onChange={e => setClasse(e.target.value)}
                        aria-invalid={invalido(!classeScelta)} aria-describedby={classeScelta && obbligatoriClasse.length ? "vd-classe-aiuto" : undefined}>
                  <option value="">Scegli la classe</option>
                  {classiDocumentali.map(c => (
                    <option key={c.codice} value={c.codice}>{c.nome}</option>
                  ))}
                </select>
              </Campo>
              <Campo id="vd-anno" label="Anno" obbligatorio>
                <input id="vd-anno" className="vd-input" inputMode="numeric" value={anno} onChange={e => setAnno(e.target.value)} />
              </Campo>
              <Campo id="vd-numero" label="Numero" aiuto="Se vuoto viene generato">
                <input id="vd-numero" className="vd-input" aria-describedby="vd-numero-aiuto" value={numero} onChange={e => setNumero(e.target.value)} />
              </Campo>
            </div>
          </Tappa>

          <Tappa n={3} titolo="Metadati del documento"
                 nota={extraClasse.length || protocolloObbligatorio || classificaObbligatoria
                   ? "Allegato 5 delle Linee guida e metadati richiesti dalla classe"
                   : "Allegato 5 delle Linee guida, 7 obbligatori"}>
            <div style={griglia(260)}>
              <Campo id="vd-id" label="Identificativo del documento" obbligatorio aiuto="Come lo identifica il sistema che versa, per esempio FT-2026-001">
                <input id="vd-id" className="vd-input" aria-describedby="vd-id-aiuto" aria-invalid={invalido(!metaId)} value={metaId} onChange={e => setMetaId(e.target.value)} />
              </Campo>
              <Campo id="vd-modalita" label="Modalità di formazione" obbligatorio aiuto="Come è nato il documento (Linee guida AgID §2.1.1). Una scansione o un file ricevuto è «b»">
                <select id="vd-modalita" className="vd-input" aria-describedby="vd-modalita-aiuto" value={metaModalita} onChange={e => setMetaModalita(e.target.value)}>
                  {MODALITA_FORMAZIONE.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </Campo>
              <Campo id="vd-tipo" label="Tipologia documentale" obbligatorio aiuto="Per esempio fattura elettronica">
                <input id="vd-tipo" className="vd-input" aria-describedby="vd-tipo-aiuto" aria-invalid={invalido(!metaTipo)} value={metaTipo} onChange={e => setMetaTipo(e.target.value)} />
              </Campo>
              <Campo id="vd-data" label="Data del documento" obbligatorio>
                <input id="vd-data" type="date" className="vd-input" aria-invalid={invalido(!metaData)} value={metaData} onChange={e => setMetaData(e.target.value)} />
              </Campo>
              <Campo id="vd-prod-id" label="Codice fiscale o partita IVA del produttore" obbligatorio>
                <input id="vd-prod-id" className="vd-input" inputMode="numeric" aria-invalid={invalido(!metaProdId)} value={metaProdId} onChange={e => setMetaProdId(e.target.value)} />
              </Campo>
              <Campo id="vd-prod-nome" label="Nome del produttore" obbligatorio>
                <input id="vd-prod-nome" className="vd-input" aria-invalid={invalido(!metaProdNome)} value={metaProdNome} onChange={e => setMetaProdNome(e.target.value)} />
              </Campo>
            </div>
            <Campo id="vd-oggetto" label="Oggetto" obbligatorio aiuto="Descrizione breve del contenuto, almeno 5 caratteri">
              <input id="vd-oggetto" className="vd-input" aria-describedby="vd-oggetto-aiuto" aria-invalid={invalido(metaOggetto.length < 5)} value={metaOggetto} onChange={e => setMetaOggetto(e.target.value)} />
            </Campo>
            {(extraClasse.length > 0 || protocolloObbligatorio || classificaObbligatoria) && (
              <div style={griglia(260)} aria-label={`Metadati richiesti dalla classe ${classeScelta?.nome ?? ""}`} role="group">
                {protocolloObbligatorio && (
                  <Campo id="vd-protocollo-cl" label="Numero di protocollo" obbligatorio aiuto="Richiesto dalla classe">
                    <input id="vd-protocollo-cl" className="vd-input" aria-describedby="vd-protocollo-cl-aiuto" aria-invalid={invalido(!metaProtocollo.trim())} value={metaProtocollo} onChange={e => setMetaProtocollo(e.target.value)} />
                  </Campo>
                )}
                {classificaObbligatoria && (
                  <Campo id="vd-classifica-cl" label="Classificazione" obbligatorio aiuto="Richiesta dalla classe, per esempio VII/3">
                    <input id="vd-classifica-cl" className="vd-input" aria-describedby="vd-classifica-cl-aiuto" aria-invalid={invalido(!metaClassifica.trim())} value={metaClassifica} onChange={e => setMetaClassifica(e.target.value)} />
                  </Campo>
                )}
                {extraClasse.map(k => (
                  <Campo key={k} id={`vd-extra-${k}`} label={nomeMetadato(k).replace(/^./, c => c.toUpperCase())} obbligatorio aiuto="Richiesto dalla classe">
                    <input id={`vd-extra-${k}`} className="vd-input" aria-describedby={`vd-extra-${k}-aiuto`} aria-invalid={invalido(!(extra[k] ?? "").trim())}
                           value={extra[k] ?? ""} onChange={e => setExtra(x => ({ ...x, [k]: e.target.value }))} />
                  </Campo>
                ))}
              </div>
            )}

            <button type="button" aria-expanded={showAvanzati} aria-controls="vd-avanzati" onClick={() => setShowAvanzati(v => !v)}
                    style={{ alignSelf: "flex-start", background: "none", border: 0, padding: "8px 0", font: "inherit", fontSize: 15, fontWeight: 700, textDecoration: "underline", cursor: "pointer", color: "var(--vd-testo)" }}>
              {showAvanzati ? "Nascondi i metadati facoltativi" : "Aggiungi metadati facoltativi"}
            </button>
            {showAvanzati && (
              <div id="vd-avanzati" style={griglia(200)}>
                {!protocolloObbligatorio && (
                  <Campo id="vd-protocollo" label="Numero di protocollo">
                    <input id="vd-protocollo" className="vd-input" value={metaProtocollo} onChange={e => setMetaProtocollo(e.target.value)} />
                  </Campo>
                )}
                {!classificaObbligatoria && (
                  <Campo id="vd-classifica" label="Classificazione" aiuto="Per esempio VII/3">
                    <input id="vd-classifica" className="vd-input" aria-describedby="vd-classifica-aiuto" value={metaClassifica} onChange={e => setMetaClassifica(e.target.value)} />
                  </Campo>
                )}
                <Campo id="vd-lingua" label="Lingua">
                  <select id="vd-lingua" className="vd-input" value={metaLingua} onChange={e => setMetaLingua(e.target.value)}>
                    <option value="it">Italiano</option>
                    <option value="en">Inglese</option>
                    <option value="fr">Francese</option>
                    <option value="de">Tedesco</option>
                  </select>
                </Campo>
              </div>
            )}
          </Tappa>

          <div style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 18, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "10px 20px" }}>
            <button type="submit" className="vd-bottone" disabled={inCorso || senzaClassi} style={{ minWidth: 220 }}>
              {inCorso ? "Versamento in corso…" : "Versa in conservazione"}
            </button>
            {tentato && mancanti.length > 0 ? (
              <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                Mancano: {mancanti.join(", ")}.
              </span>
            ) : !tentato && mancanti.length > 0 ? (
              <span className="vd-nota">Mancano {mancanti.length} dati obbligatori.</span>
            ) : null}
          </div>
        </form>

        {/* ── Tabellone del versamento ─────────────────────────────── */}
        <aside aria-labelledby="vd-tabellone-v" style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 24 }}>
          <section>
            <div className="vd-sezione-testa">
              <h2 id="vd-tabellone-v" className="vd-etichetta">Corsa del pacchetto</h2>
            </div>
            {/* R12c: la corsa come una linea ferroviaria verticale; ogni fermata ha un pallino
                sul binario (pieno = raggiunta, giallo = in corso, vuoto = in attesa) e la parola
                di stato accanto, perché lo stato non sia affidato al solo segno. */}
            <ol className="vd-linea" aria-live="polite" aria-label="Fermate del pacchetto">
              {FASI.map((f, i) => {
                const fallitaQui = step.id === "error" && !!orari[f.key] && !FASI.slice(i + 1).some(x => orari[x.key]);
                const fatta = !!orari[f.key] && !fallitaQui && (i < faseIdx || step.id === "conserved" || step.id === "error");
                const attuale = step.id === f.key && step.id !== "conserved";
                const fallita = step.id === "error" && orari[f.key] && !FASI.slice(i + 1).some(x => orari[x.key]);
                // fase superata tra due interrogazioni dello stato: non la diamo per fatta all'ora sbagliata
                const nonOsservata = !orari[f.key] && FASI.slice(i + 1).some(x => orari[x.key]);
                const tipo = fallita ? "fallita" : fatta || nonOsservata ? "fatta" : attuale ? "attuale" : "attesa";
                return (
                  <li key={f.key} className={`vd-fermata vd-fermata--${tipo}`} aria-current={attuale ? "step" : undefined}>
                    <span className="vd-fermata-ora">{orari[f.key] ?? ""}</span>
                    <span className="vd-fermata-binario" aria-hidden="true"><span className="vd-fermata-punto" /></span>
                    <span className="vd-fermata-nome">
                      {f.label}
                      <span className={`vd-sotto vd-stato ${fallita ? "vd-stato--critico" : fatta || nonOsservata ? "vd-stato--ok" : attuale ? "vd-stato--info" : "vd-stato--neutro"}`}>
                        {fallita ? "Interrotta" : fatta ? "Fatto" : attuale ? "In corso" : nonOsservata ? "Superata, ora non rilevata" : "In attesa"}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ol>
            {step.id === "conserved" && (
              <div style={{ marginTop: 16, borderTop: "4px solid var(--vd-ok)", paddingTop: 10 }}>
                <p style={{ fontSize: 17, fontWeight: 700, color: "var(--vd-ok)" }}>Conservato</p>
                <p className="vd-mono" style={{ overflowWrap: "anywhere", marginTop: 4 }}>{result?.urn || result?.pdv_id || step.pdv_id}</p>
                <p style={{ marginTop: 8 }}><Link to="/aip" style={{ fontWeight: 700 }}>Vai ai pacchetti conservati</Link></p>
              </div>
            )}
            {step.id === "error" && (
              <div role="alert" style={{ marginTop: 16, borderTop: "4px solid var(--vd-critico)", paddingTop: 10 }}>
                <p style={{ fontSize: 17, fontWeight: 700, color: "var(--vd-critico)" }}>Versamento rifiutato</p>
                <p style={{ marginTop: 4, overflowWrap: "anywhere" }}>{step.msg}</p>
                <p className="vd-nota" style={{ marginTop: 6 }}>Correggi il dato indicato e versa di nuovo: il pacchetto rifiutato non è stato conservato.</p>
              </div>
            )}
            {step.id === "idle" && (
              <p className="vd-nota" style={{ marginTop: 10 }}>Ogni fermata si accende con l'ora in cui il pacchetto la raggiunge.</p>
            )}
          </section>

          {log.length > 0 && (
            <section aria-labelledby="vd-registro">
              <h2 id="vd-registro" style={{ fontSize: 17, fontWeight: 800, fontStretch: "85%", borderBottom: "2px solid var(--vd-testo)", paddingBottom: 6 }}>Registro tecnico</h2>
              <ol style={{ listStyle: "none", maxHeight: 240, overflowY: "auto", paddingBlock: 6 }} className="vd-mono">
                {log.map((l, i) => (
                  <li key={i} style={{ paddingBlock: 3, borderBottom: "1px solid var(--vd-filetto)", overflowWrap: "anywhere",
                                       color: l.includes("Rifiutato") ? "var(--vd-critico)" : "var(--vd-testo)" }}>{l}</li>
                ))}
              </ol>
            </section>
          )}
        </aside>
      </div>
    </>
  );
}
