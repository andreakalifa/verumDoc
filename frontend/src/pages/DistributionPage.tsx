// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Esibizioni e copie — sistema grafico «Partenze» (VD-DR-01).
 * Prepara un pacchetto di distribuzione da un pacchetto di archiviazione.
 * VD-C-014: l'esito mostra i link reali restituiti dal servizio o l'errore vero;
 * un'esibizione senza link è un errore, mai un successo.
 * g3-frontend (g2-dati §6.9):
 *  - il richiedente lo ricava il servizio dal token: il client non invia più
 *    `richiedente_id`;
 *  - 503 DST-503 = registro non scrivibile, nessun link consegnato;
 *  - impronta e dimensione dei file possono essere null («non calcolate»);
 *  - storico con canale (portale o API), motivo, motivo del rifiuto,
 *    503 con id di correlazione; i profili globali scelgono l'ente.
 * R04 (VD-C-102): storico a cursore, senza totale.
 * Revisione R17: il modulo solo a chi ha «esibizione:richiedi» (operatore sì,
 * consultazione no: R17-01/02); il pacchetto si cerca e si sceglie, o arriva già scelto
 * dalla scheda del pacchetto (`?aip=`), mai un UUID da incollare (R17-07); l'ente è
 * quello della sotto-navigazione (un solo selettore, R17-08).
 */
import { FormEvent, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { richiediPdD, fetchStoricoDistribuzioni, rigeneraPdD, searchAIP } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ESIBIZIONE, puo } from "../auth/permessi";
import { quando } from "../console/comuni";
import { codaRiferimenti, leggiErrore, motivoDaLetto, motivoErrore } from "../api/errori";
import { useEnte } from "../components/SceltaEnte";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { EsitoCritico } from "../components/EsitoCritico";

const TIPI = [
  { value: "ESIBIZIONE", label: "Esibizione", desc: "Link a scadenza da consegnare a chi deve vedere il documento (ispettore, tribunale, cittadino)." },
  { value: "COMPLETO",   label: "Copia completa", desc: "Tutti i file del pacchetto di archiviazione con il suo indice." },
  { value: "ESTRATTO",   label: "Estratto", desc: "Solo i file che indichi per nome." },
  { value: "MIGRAZIONE", label: "Export per cambio di conservatore", desc: "Pacchetto con indice SInCRO per il passaggio a un altro conservatore." },
];
const NOME_TIPO: Record<string, string> = {
  ESIBIZIONE: "Esibizione", COMPLETO: "Copia completa", ESTRATTO: "Estratto", MIGRAZIONE: "Export per cambio di conservatore",
  PDD_ESIBIZIONE: "Esibizione", PDD_COMPLETO: "Copia completa", PDD_ESTRATTO: "Estratto", PDD_MIGRAZIONE: "Export per cambio di conservatore",
};
const COMANDO: Record<string, string> = {
  ESIBIZIONE: "Prepara l'esibizione", COMPLETO: "Prepara la copia", ESTRATTO: "Prepara l'estratto", MIGRAZIONE: "Prepara l'export",
};

/** Stati dello storico (g2-dati §6.9: INVIATO, PENDING, APPROVATA, RIFIUTATA). */
const STATO_DIST: Record<string, { classe: string; testo: string }> = {
  INVIATO:    { classe: "vd-stato--ok", testo: "Preparata" },
  PENDING:    { classe: "vd-stato--info", testo: "In attesa di approvazione" },
  APPROVATA:  { classe: "vd-stato--ok", testo: "Approvata" },
  RIFIUTATA:  { classe: "vd-stato--critico", testo: "Rifiutata" },
};
const CANALE: Record<string, string> = {
  WEB: "dal portale", PORTALE: "dal portale", API: "da un sistema, via API", SFTP: "da un sistema, via SFTP",
  PEC: "via PEC", PRATICA: "da una pratica del ciclo di vita",
};
const byte = (n?: number | null) => {
  if (typeof n !== "number") return null;
  if (n < 1024) return `${n.toLocaleString("it-IT")} byte`;
  if (n < 1024 ** 2) return `${(n / 1024).toLocaleString("it-IT", { maximumFractionDigits: 1 })} KB`;
  return `${(n / 1024 ** 2).toLocaleString("it-IT", { maximumFractionDigits: 1 })} MB`;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
interface Scelto { aip_id: string; oggetto?: string | null; urn?: string | null; classe?: string | null }

/**
 * Scelta del pacchetto da esibire: si cerca per oggetto, segnatura o protocollo e si
 * sceglie dai risultati (revisione R17, R17-07). Il pacchetto arrivato dalla scheda
 * (`?aip=`) è già scelto.
 */
function SceltaPacchetto({ tenantId, scelto, onScegli, invalido }: {
  tenantId: string; scelto: Scelto | null; onScegli: (s: Scelto | null) => void; invalido?: "true";
}) {
  const [q, setQ] = useState("");
  const ricerca = useMutation({ mutationFn: (testo: string) => searchAIP({ query: testo, tenant_id: tenantId || undefined, limit: 8 }) });
  const risultati: any[] = Array.isArray(ricerca.data?.items) ? ricerca.data.items : [];
  const cerca = () => { if (q.trim()) ricerca.mutate(q.trim()); };
  if (scelto) {
    return (
      <div className="vd-campo">
        <span style={{ fontSize: 14, fontWeight: 600 }} id="vd-pdd-scelto-et">Pacchetto da esibire</span>
        <div className="vd-scelto" role="group" aria-labelledby="vd-pdd-scelto-et">
          <p style={{ fontWeight: 600, overflowWrap: "anywhere" }}>{scelto.oggetto || "Oggetto non indicato"}</p>
          <p className="vd-sotto vd-mono" style={{ overflowWrap: "anywhere" }}>{scelto.urn || `Pacchetto ${scelto.aip_id.slice(0, 8)}`}{scelto.classe ? ` · ${scelto.classe}` : ""}</p>
          <button type="button" className="vd-comando-testo" onClick={() => onScegli(null)}>Scegli un altro pacchetto</button>
        </div>
      </div>
    );
  }
  return (
    <div className="vd-campo">
      <label htmlFor="vd-pdd-cerca">Pacchetto da esibire<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input id="vd-pdd-cerca" className="vd-input" type="search" style={{ flex: "1 1 220px", width: "auto" }} value={q}
               onChange={e => setQ(e.target.value)} aria-describedby="vd-pdd-cerca-aiuto" aria-invalid={invalido}
               onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); cerca(); } }} />
        <button type="button" className="vd-bottone vd-bottone--secondario" onClick={cerca} disabled={ricerca.isPending}>
          {ricerca.isPending ? "Ricerca…" : "Cerca"}
        </button>
      </div>
      <span id="vd-pdd-cerca-aiuto" className="vd-aiuto">Oggetto, segnatura o numero di protocollo del documento. Puoi anche partire dalla scheda del pacchetto con «Esibisci».</span>
      {ricerca.isError ? (
        <p role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>Ricerca non riuscita: {motivoErrore(ricerca.error)}.</p>
      ) : ricerca.isSuccess && risultati.length === 0 ? (
        <p role="status" className="vd-nota">Nessun pacchetto trovato con «{ricerca.variables}».</p>
      ) : risultati.length > 0 ? (
        <ul className="vd-elenco-scelte" aria-label="Pacchetti trovati">
          {risultati.map(r => (
            <li key={r.aip_id}>
              <span style={{ minWidth: 0 }}>
                <span style={{ fontWeight: 600, overflowWrap: "anywhere", display: "block" }}>{r.oggetto || "Oggetto non indicato"}</span>
                <span className="vd-sotto vd-mono" style={{ overflowWrap: "anywhere" }}>{r.urn || `Pacchetto ${String(r.aip_id).slice(0, 8)}`}</span>
              </span>
              <button type="button" className="vd-bottone vd-bottone--secondario"
                      onClick={() => onScegli({ aip_id: String(r.aip_id), oggetto: r.oggetto, urn: r.urn,
                                                classe: r.classe_documentale?.nome || r.classe_documentale?.codice || null })}>
                Scegli<span className="vd-sr"> {r.oggetto || r.urn || "questo pacchetto"}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}


function CopiaLink({ url }: { url: string }) {
  const [esito, setEsito] = useState<"" | "ok" | "no">("");
  const copia = async () => {
    try { await navigator.clipboard.writeText(url); setEsito("ok"); } catch { setEsito("no"); }
  };
  return (
    <>
      <button type="button" className="vd-comando-testo" onClick={copia}>Copia il link</button>
      <span aria-live="polite" className={`vd-stato ${esito === "no" ? "vd-stato--critico" : "vd-stato--ok"}`}>
        {esito === "ok" ? "Copiato" : esito === "no" ? "Copia non riuscita: seleziona il link a mano" : ""}
      </span>
    </>
  );
}

function Esito({ r, tipo, onNuova }: { r: any; tipo: string; onNuova: () => void }) {
  const documenti: any[] = Array.isArray(r?.documenti) ? r.documenti : [];
  const conLink = documenti.filter(d => d && typeof d.signed_url === "string" && d.signed_url.startsWith("http"));
  const scadenza = quando(r?.url_scadenza);
  const nome = NOME_TIPO[tipo] ?? tipo;
  const inAttesa = r?.stato === "PENDING";

  const riferimenti = (mostraMessaggio = true) => (
    <dl className="vd-dati" style={{ marginTop: 4 }}>
      <dt>Pacchetto di distribuzione</dt><dd><span className="vd-mono">{r?.pdd_id ?? "non disponibile"}</span></dd>
      <dt>Impronta del pacchetto</dt><dd><span className="vd-mono">{r?.hash_pdd ?? "non disponibile"}</span></dd>
      <dt>File inclusi</dt><dd>{documenti.length}</dd>
      {mostraMessaggio && r?.messaggio && <><dt>Messaggio del servizio</dt><dd>{r.messaggio}</dd></>}
    </dl>
  );
  const nuova = <p><button type="button" className="vd-comando-testo" onClick={onNuova}>Nuova richiesta</button></p>;

  if (inAttesa) {
    return (
      <div className="vd-esito vd-esito--info">
        <p className="vd-esito-titolo">{nome}: richiesta in attesa di approvazione</p>
        <p style={{ fontSize: 15 }}>Il link sarà disponibile dopo l'approvazione dell'amministrazione dell'ente.</p>
        {riferimenti()}{nuova}
      </div>
    );
  }
  if (conLink.length > 0) {
    return (
      <div className="vd-esito vd-esito--ok">
        <p className="vd-esito-titolo">{tipo === "ESIBIZIONE" ? "Esibizione pronta" : `${nome} pronta`}</p>
        <p style={{ fontSize: 15 }}>
          {scadenza ? <>I link scadono il <strong>{scadenza.giorno} alle {scadenza.ora}</strong>.</> : <>Il servizio non ha indicato la scadenza dei link.</>}
          {" "}Chi riceve il link può scaricare il file senza accedere al servizio.
        </p>
        <ul style={{ listStyle: "none", borderTop: "2px solid var(--vd-testo)" }}>
          {conLink.map((d, i) => (
            <li key={i} style={{ borderBottom: "1px solid var(--vd-filetto)", paddingBlock: 8, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0 16px" }}>
              <a href={d.signed_url} target="_blank" rel="noopener noreferrer" className="vd-riga-link" style={{ fontWeight: 700, overflowWrap: "anywhere" }}>
                {d.nome_file ?? `File ${i + 1}`}<span className="vd-sr"> (si apre in una nuova finestra)</span>
              </a>
              <CopiaLink url={d.signed_url} />
              <span className="vd-nota" style={{ flexBasis: "100%", fontSize: 13, overflowWrap: "anywhere" }}>
                {[d.formato_mime, byte(d.dimensione_bytes)].filter(Boolean).join(" · ")}
                {d.hash_sha256 ? <> · impronta <span className="vd-mono">{String(d.hash_sha256).slice(0, 16)}…</span></> : " · impronta non calcolata dal servizio"}
              </span>
            </li>
          ))}
        </ul>
        {conLink.length < documenti.length && (
          <p className="vd-stato vd-stato--rischio" style={{ whiteSpace: "normal" }}>
            {documenti.length - conLink.length} file senza link: il servizio non li ha resi scaricabili.
          </p>
        )}
        {riferimenti()}{nuova}
      </div>
    );
  }
  if (tipo === "ESIBIZIONE") {
    return (
      <div role="alert" className="vd-esito vd-esito--critico">
        <p className="vd-esito-titolo">Esibizione senza link</p>
        <p style={{ fontSize: 15 }}>
          Il servizio ha risposto ma non ha restituito alcun link: non c'è nulla da consegnare.
          L'esibizione non è avvenuta. Riprova; se si ripete, segnala il problema indicando il codice qui sotto.
        </p>
        {riferimenti(false)}{nuova}
      </div>
    );
  }
  return (
    <div className="vd-esito vd-esito--rischio">
      <p className="vd-esito-titolo">{nome} registrata, ma non scaricabile dal portale</p>
      <p style={{ fontSize: 15 }}>
        Il servizio ha preparato il pacchetto di distribuzione senza restituire un link per scaricarlo.
        Lo scarico dal portale è una funzione da realizzare.{" "}
        <Link to="/da-realizzare/scarico-pdd" className="vd-riga-link">Che cosa manca</Link>
      </p>
      {riferimenti()}{nuova}
    </div>
  );
}

export default function DistributionPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const ente = useEnte();
  const tenantId = ente.tenantId;

  const puoRichiedere = puo(user, ESIBIZIONE);
  const [params] = useSearchParams();
  const daScheda = params.get("aip") ?? "";
  // dalla scheda del pacchetto arrivano identificativo, oggetto e segnatura (stato del link)
  const statoLink = (useLocation().state ?? {}) as Partial<Scelto>;
  const [scelto, setScelto] = useState<Scelto | null>(UUID.test(daScheda)
    ? { aip_id: daScheda, ...(statoLink.aip_id === daScheda ? statoLink : {}) } : null);
  const aipId = scelto?.aip_id ?? "";
  const sceltoMostrato = scelto;
  const [tipo,          setTipo]          = useState("ESIBIZIONE");
  const [motivo,        setMotivo]        = useState("");
  const [fileRichiesti, setFileRichiesti] = useState("");
  const cursori = useCursori();
  const [tentato,       setTentato]       = useState(false);
  const [tipoInviato,   setTipoInviato]   = useState("");
  const limitStorico = 10;

  const richiesta = useMutation({
    mutationFn: richiediPdD,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storico-dist", tenantId] }),
  });
  // R07 (D18): rigenerazione con un clic di un pacchetto scaduto
  const [rigenerato, setRigenerato] = useState<Record<string, string>>({});
  const rigenera = useMutation({
    mutationFn: (pddId: string) => rigeneraPdD(pddId),
    onSuccess: (dati, pddId) => setRigenerato(r => ({ ...r, [pddId]: dati?.stato === "PRONTO"
      ? "Rigenerato: i collegamenti valgono di nuovo." : "Rigenerazione avviata: sarà pronto a breve." })),
    onError: (e, pddId) => setRigenerato(r => ({ ...r, [pddId]: leggiErrore(e).codice === "DST-409"
      ? "Il pacchetto non è scaduto: si può ancora scaricare." : `Non rigenerato: ${motivoErrore(e)}.` })),
  });

  // Storico richieste
  const storico = useQuery({
    queryKey: ["storico-dist", tenantId, cursori.cursore],
    // tenant_id solo per un profilo globale che ha scelto un altro ente
    queryFn:  () => fetchStoricoDistribuzioni(ente.globale && !ente.proprio ? tenantId : undefined, cursori.cursore, limitStorico),
    enabled:  !!tenantId,
    refetchInterval: 30_000, // aggiorna ogni 30 s per vedere le approvazioni
  });

  const storicoItems: any[] = storico.data?.items ?? [];
  const prossimoStorico = prossimoDi(storico.data);

  const mancanti = [
    !aipId && "pacchetto da esibire",
    tipo === "ESTRATTO" && !fileRichiesti.trim() && "file richiesti",
    motivo.trim().length < 5 && "motivazione (almeno 5 caratteri)",
  ].filter(Boolean) as string[];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTentato(true);
    if (mancanti.length > 0) return;
    // Il richiedente lo ricava il servizio dal token (g2-dati §6.9): niente richiedente_id
    const body: any = { aip_id: aipId, tipo, motivo };
    if (tipo === "ESTRATTO" && fileRichiesti)
      body.file_richiesti = fileRichiesti.split(",").map((f: string) => f.trim()).filter(Boolean);
    setTipoInviato(tipo);
    richiesta.mutate(body);
  };
  const nuova = () => { richiesta.reset(); setTentato(false); };
  const letto = richiesta.isError ? leggiErrore(richiesta.error) : null;
  const err = letto ? { codice: letto.stato, testo: letto.testo, letto } : null;
  const invalido = (vuoto: boolean) => (tentato && vuoto ? "true" : undefined);

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Esibizioni e copie</h1>
        <p>Da un pacchetto di archiviazione si prepara un pacchetto di distribuzione: un'esibizione con link a scadenza, una copia, un estratto o l'export per cambiare conservatore.</p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 40 }}>
        {!puoRichiedere ? (
          <div className="vd-avviso vd-avviso--neutro" role="note">
            <p style={{ fontWeight: 700 }}>Sola lettura</p>
            <p>Il tuo profilo consulta le richieste dell'ente ma non chiede esibizioni né copie. Se ti serve, chiedi il permesso all'amministrazione del tuo ente.</p>
          </div>
        ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 48, alignItems: "flex-start" }}>
          {/* ── Nuova richiesta ─────────────────────────────────── */}
          <section aria-labelledby="vd-nuova" style={{ flex: "1 1 460px", minWidth: 0, maxWidth: 640 }}>
            <div className="vd-sezione-testa">
              <h2 id="vd-nuova" className="vd-etichetta">Nuova richiesta</h2>
            </div>
            <form onSubmit={submit} noValidate style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              <SceltaPacchetto tenantId={tenantId} scelto={sceltoMostrato} onScegli={setScelto} invalido={invalido(!aipId)} />

              <fieldset className="vd-scelte">
                <legend>Che cosa preparare</legend>
                {TIPI.map(t => (
                  <label key={t.value} className="vd-scelta">
                    <input type="radio" name="vd-tipo-pdd" value={t.value} checked={tipo === t.value} onChange={() => setTipo(t.value)} />
                    <strong>{t.label}</strong>
                    <span>{t.desc}</span>
                  </label>
                ))}
              </fieldset>

              {tipo === "ESTRATTO" && (
                <div className="vd-campo">
                  <label htmlFor="vd-pdd-file">File richiesti<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                  <input id="vd-pdd-file" className="vd-input" value={fileRichiesti} onChange={e => setFileRichiesti(e.target.value)}
                         aria-describedby="vd-pdd-file-aiuto" aria-invalid={invalido(!fileRichiesti.trim())} />
                  <span id="vd-pdd-file-aiuto" className="vd-aiuto">Nomi dei file separati da virgola, per esempio fattura.pdf, allegato.xml</span>
                </div>
              )}

              <div className="vd-campo">
                <label htmlFor="vd-pdd-motivo">Motivazione<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <textarea id="vd-pdd-motivo" className="vd-input vd-textarea" rows={3} value={motivo} onChange={e => setMotivo(e.target.value)}
                          aria-describedby="vd-pdd-motivo-aiuto" aria-invalid={invalido(motivo.trim().length < 5)} />
                <span id="vd-pdd-motivo-aiuto" className="vd-aiuto">Per chi e perché, almeno 5 caratteri. Resta nel registro delle operazioni.</span>
              </div>

              <div style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 18, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "10px 20px" }}>
                <button type="submit" className="vd-bottone" disabled={richiesta.isPending}>
                  {richiesta.isPending ? "Preparazione in corso…" : COMANDO[tipo] ?? "Prepara il pacchetto"}
                </button>
                {tentato && mancanti.length > 0 ? (
                  <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>Mancano: {mancanti.join(", ")}.</span>
                ) : !tentato && mancanti.length > 0 ? (
                  <span className="vd-nota">Mancano {mancanti.length} {mancanti.length === 1 ? "dato obbligatorio" : "dati obbligatori"}.</span>
                ) : null}
              </div>
            </form>
          </section>

          {/* ── Esito ───────────────────────────────────────────── */}
          <section aria-labelledby="vd-esito-pdd" aria-live="polite" style={{ flex: "1 1 340px", minWidth: 0 }}>
            <div className="vd-sezione-testa">
              <h2 id="vd-esito-pdd" className="vd-etichetta">Esito</h2>
            </div>
            {richiesta.isPending ? (
              <p className="vd-stato vd-stato--info">Preparazione in corso…</p>
            ) : err ? (
              <div role="alert" className="vd-esito vd-esito--critico">
                <p className="vd-esito-titolo">
                  {err.codice === 503 ? "Richiesta non registrata" : "Richiesta non riuscita"}{err.codice ? ` (errore ${err.codice})` : ""}
                </p>
                <p style={{ fontSize: 15, overflowWrap: "anywhere" }}>
                  {err.codice === 404 ? "Nessun pacchetto di archiviazione con questo identificativo."
                    : err.codice === 403 ? "Il pacchetto appartiene a un altro ente o il tuo profilo non può esibirlo."
                    : err.codice === 503 ? "Il registro delle esibizioni non è scrivibile in questo momento: per non consegnare link non registrati, il servizio non li ha creati. Riprova tra qualche minuto."
                    : err.codice === undefined ? "Il servizio di distribuzione non ha risposto."
                    : motivoDaLetto(err.letto, "operazione").replace(/^./, c => c.toUpperCase()) + "."}
                </p>
                {err.testo && (err.codice === 404 || err.codice === 403 || err.codice === 503) && (
                  <p className="vd-nota" style={{ overflowWrap: "anywhere" }}>Messaggio del servizio: {err.testo}{codaRiferimenti(err.letto)}</p>
                )}
                <p className="vd-nota">Nessun pacchetto di distribuzione è stato preparato e nessun link è stato creato.</p>
              </div>
            ) : richiesta.isSuccess ? (
              <Esito r={richiesta.data} tipo={tipoInviato} onNuova={nuova} />
            ) : (
              <p className="vd-nota">Qui compare l'esito: i link da consegnare, oppure l'errore restituito dal servizio.</p>
            )}
          </section>
        </div>
        )}

        {/* ── Storico ─────────────────────────────────────────────── */}
        <section aria-labelledby="vd-storico">
          <div className="vd-sezione-testa">
            <h2 id="vd-storico" className="vd-etichetta">Richieste dell'ente</h2>
            {storico.isSuccess && (
              <span className="vd-nota">
                Dal portale e via API, le più recenti per prime{ente.nomeEnte ? ` · ${ente.nomeEnte}` : ""}
              </span>
            )}
          </div>
          {!tenantId ? (
            <p className="vd-nota" style={{ paddingBlock: 12 }}>
              {ente.globale ? "Scegli l'ente nella barra sopra («Ente su cui lavori») per vedere le sue richieste." : "Il tuo profilo non è legato a un ente: lo storico delle richieste si consulta per ente."}
            </p>
          ) : storico.isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura delle richieste…</p>
          ) : storico.isError ? (
            <EsitoCritico titolo="Storico delle richieste non disponibile" errore={storico.error}
                          spiegazione="Non significa che non ci siano richieste: quelle fatte non sono perse."
                          onRiprova={() => storico.refetch()} inRiprova={storico.isFetching} />
          ) : storicoItems.length === 0 ? (
            <p className="vd-nota" style={{ paddingBlock: 12 }}>Nessuna esibizione o copia richiesta finora.</p>
          ) : (
            <>
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Richieste di esibizione e copia dell'ente</caption>
                  <thead><tr><th scope="col">Richiesta il</th><th scope="col">Richiesta</th><th scope="col">Pacchetto</th><th scope="col">Stato</th></tr></thead>
                  <tbody>
                    {storicoItems.map((item, i) => {
                      const s = STATO_DIST[item.stato] ?? { classe: "vd-stato--neutro", testo: item.stato ?? "non disponibile" };
                      const q = quando(item.data ?? item.created_at);
                      return (
                        <tr key={item.id ?? i}>
                          <td className="vd-ora" data-label="Richiesta il">{q ? q.ora : "—"}<span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>{q ? q.giorno : "data non disponibile"}</span></td>
                          <td style={{ minWidth: 0 }} data-label="Richiesta">
                            <span style={{ fontWeight: 600 }}>{NOME_TIPO[item.tipo] ?? item.tipo ?? "—"}</span>
                            <span className="vd-sotto">
                              {item.canale ? (CANALE[item.canale] ?? "canale non riconosciuto") : "canale non indicato"}
                              {item.richiedente && user?.user_id && item.richiedente === user.user_id ? " · da te" : ""}
                            </span>
                            {item.motivo && <span className="vd-sotto vd-a-capo">«{item.motivo}»</span>}
                          </td>
                          <td data-label="Pacchetto"><span className="vd-mono" title={item.aip_id}>{item.aip_id ? String(item.aip_id).slice(0, 8) : "—"}</span></td>
                          <td style={{ whiteSpace: "normal", minWidth: 0 }} data-label="Stato">
                            <span className={`vd-stato ${s.classe}`}>{s.testo}</span>
                            {item.stato === "RIFIUTATA" && (
                              <span className="vd-sotto vd-a-capo">{item.motivo_rifiuto ? `Motivo: ${item.motivo_rifiuto}` : "Motivo del rifiuto non registrato"}</span>
                            )}
                            {puoRichiedere && item.pdd_id && item.stato !== "RIFIUTATA" && item.stato !== "PENDING" && (
                              <span className="vd-sotto">
                                {rigenerato[item.pdd_id] ? <span role="status">{rigenerato[item.pdd_id]}</span> : (
                                  <button type="button" className="vd-comando-testo"
                                          disabled={rigenera.isPending && rigenera.variables === item.pdd_id}
                                          onClick={() => rigenera.mutate(item.pdd_id)}>
                                    Rigenera se scaduto
                                  </button>
                                )}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Paginazione cursori={cursori} prossimo={prossimoStorico} righe={storicoItems.length}
                           limite={limitStorico} nome="Richieste" etichetta="Pagine dello storico"
                           testoPrecedente="Precedenti" testoSuccessiva="Successive" />
            </>
          )}
        </section>
      </div>
    </>
  );
}
