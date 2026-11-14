// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Pacchetti conservati — sistema grafico «Partenze» (VD-DR-01).
 * Tabellone dei pacchetti di archiviazione dell'ente; il dettaglio si apre in un
 * <dialog> modale vero (focus gestito dal browser, Esc chiude, il focus torna alla riga).
 * g3-frontend (g2-dati §6.3–6.4):
 *  - metadati dell'Allegato 5 reali, per documento (`metadati_allegato5[].metadati`;
 *    `null` = non registrati, mai valori inventati);
 *  - i profili globali scelgono l'ente: elenco e metadati si leggono con
 *    `?tenant_id=<ente scelto>`; il contenuto (pacchetto completo) si consegna solo
 *    per l'ente del proprio profilo (VD-C-005, §19.9) e la pagina lo spiega;
 *  - i file scaricabili sono quelli dichiarati dal servizio (`/files`), con il
 *    tipo di rapporto di versamento (definitivo o preliminare).
 */
import { Fragment, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, fetchAIP } from "../api/client";
import { leggiErroreFetch, motivoDaLetto } from "../api/errori";
import { nomeModalita } from "../data/modalitaFormazione";
import { METADATI_ALLEGATO5, nomeMetadato } from "../data/allegato5";
import { SceltaEnte, useEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { useAuth } from "../auth/AuthContext";
import { ESIBIZIONE, puo } from "../auth/permessi";

const LIMITE = 20;

/** Stati del pacchetto di archiviazione (AipStato) come parole del mestiere. */
const STATO_AIP: Record<string, { testo: string; classe: string }> = {
  IN_BUILD:     { testo: "In costruzione", classe: "vd-stato--info" },
  FIRMATO:      { testo: "Firmato", classe: "vd-stato--info" },
  TIMESTAMPATO: { testo: "Marcato", classe: "vd-stato--info" },
  SU_STORAGE:   { testo: "Archiviato, attende la catena", classe: "vd-stato--info" },
  IN_CATENA:    { testo: "Iscritto nella catena", classe: "vd-stato--ok" },
  CONSERVATO:   { testo: "Conservato", classe: "vd-stato--ok" },
  ESIBIZIONE:   { testo: "In esibizione", classe: "vd-stato--info" },
  ELIMINAZIONE: { testo: "In scarto", classe: "vd-stato--rischio" },
  // Revisione 2 di R05 (S3): cancellazione dello scarto iniziata e non completata
  ELIMINAZIONE_INCOMPLETA: { testo: "Scarto incompleto", classe: "vd-stato--critico" },
};
const FILTRI_STATO = ["", "CONSERVATO", "IN_CATENA", "SU_STORAGE", "IN_BUILD", "FIRMATO", "ELIMINAZIONE"];

const statoDi = (s?: string) =>
  (s ? STATO_AIP[s] : undefined) ?? { testo: s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ") : "Non indicato", classe: "vd-stato--neutro" };

const NELLA_CATENA = ["IN_CATENA", "CONSERVATO"];

const num = (n: number) => n.toLocaleString("it-IT");
const dataIt = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" });
};
const oraIt = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
};
const byte = (n?: number | null) => {
  if (typeof n !== "number") return null;
  if (n < 1024) return `${num(n)} byte`;
  if (n < 1024 ** 2) return `${(n / 1024).toLocaleString("it-IT", { maximumFractionDigits: 1 })} KB`;
  return `${(n / 1024 ** 2).toLocaleString("it-IT", { maximumFractionDigits: 1 })} MB`;
};

const NON_DISPONIBILE = <span className="vd-stato vd-stato--neutro">non disponibile</span>;
/** Revisione R17 (R17-21): conservazione permanente = scadenza nulla per scelta, non dato mancante. */
const permanente = (aip: any) => !aip.scadenza_conservazione
  && (aip.conservazione_permanente === true || aip.classe_documentale?.conservazione_permanente === true);

/** Valore di un metadato dell'Allegato 5 in forma leggibile. */
function testoOggetto(o: Record<string, unknown>): string {
  return Object.entries(o).filter(([, x]) => x !== null && x !== undefined && x !== "")
    .map(([k, x]) => `${nomeMetadato(k)}: ${Array.isArray(x) ? x.join(", ") : typeof x === "boolean" ? (x ? "sì" : "no") : String(x)}`)
    .join(", ");
}

function valoreMetadato(chiave: string, v: unknown): React.ReactNode {
  if (v === null || v === undefined || v === "") return null;
  if (chiave === "modalita_formazione") return nomeModalita(v);
  if ((chiave === "data_documento" || chiave === "data_registrazione") && typeof v === "string") return dataIt(v) ?? v;
  if (typeof v === "boolean") return v ? "Sì" : "No";
  if (Array.isArray(v)) {
    // R11: elenchi (parole chiave) e strutture (Soggetti, Tracciature) in chiaro
    if (v.length === 0) return null;
    return <span className="vd-a-capo">{v.map(x => (x !== null && typeof x === "object" ? testoOggetto(x as Record<string, unknown>) : String(x))).join(" · ")}</span>;
  }
  if (typeof v === "object") {
    const voci = Object.entries(v as Record<string, unknown>);
    if (voci.length === 0) return null;
    return <span className="vd-a-capo">{voci.map(([k, x]) => `${k}: ${typeof x === "object" ? JSON.stringify(x) : String(x)}`).join("; ")}</span>;
  }
  return String(v);
}

// ── Scaricamenti ───────────────────────────────────────────────────
const NOME_FILE: Record<string, { etichetta: string; nome: (id: string) => string }> = {
  SINCRO:   { etichetta: "Indice SInCRO (XML)",          nome: id => `IndiceAIP_${id.slice(0, 8)}.xml` },
  MANIFEST: { etichetta: "Elenco dei file (JSON)",       nome: id => `manifest_${id.slice(0, 8)}.json` },
  RDV:      { etichetta: "Rapporto di versamento (XML)", nome: id => `RdV_${id.slice(0, 8)}.xml` },
};

interface FileScaricabile { chiave: string; etichetta: string; url: string; nome: string; nota?: string }

async function scaricaFile(url: string): Promise<{ blob: Blob; rdvTipo: string | null }> {
  // R05 (VD-C-079): la sessione è nel cookie HttpOnly, che il browser manda da solo
  let resp: Response;
  try {
    resp = await fetch(url, { credentials: "same-origin" });
  } catch {
    throw new Error("il servizio non risponde");
  }
  if (!resp.ok) throw new Error(motivoDaLetto(await leggiErroreFetch(resp), "consultazione"));
  return { blob: await resp.blob(), rdvTipo: resp.headers.get("x-rdv-tipo") };
}

function Scaricamenti({ aip, ente }: { aip: any; ente: { tenantId: string; globale: boolean; proprio: boolean } }) {
  const [inCorso, setInCorso] = useState<string | null>(null);
  const [esito, setEsito] = useState<{ ok: boolean; testo: string } | null>(null);
  const suffisso = ente.globale && ente.tenantId ? `?tenant_id=${encodeURIComponent(ente.tenantId)}` : "";

  const elenco = useQuery({
    queryKey: ["aip-files", aip.aip_id, suffisso],
    queryFn: () => api.get(`/dashboard/aip/${aip.aip_id}/files${suffisso}`).then(r => r.data),
  });

  const scarica = async (f: FileScaricabile) => {
    setInCorso(f.chiave); setEsito(null);
    try {
      const { blob, rdvTipo } = await scaricaFile(f.url);
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href; a.download = f.nome; a.click();
      URL.revokeObjectURL(href);
      const tipo = rdvTipo === "preliminare" ? " È il rapporto preliminare: quello definitivo non è ancora stato prodotto." : "";
      setEsito({ ok: true, testo: `${f.etichetta}: scaricato come ${f.nome}.${tipo}` });
    } catch (e: any) {
      setEsito({ ok: false, testo: `${f.etichetta}: scaricamento non riuscito: ${e?.message ?? "errore sconosciuto"}.` });
    } finally {
      setInCorso(null);
    }
  };

  if (elenco.isLoading) return <p className="vd-nota" role="status">Lettura dei file scaricabili…</p>;
  if (elenco.isError) {
    return (
      <EsitoCritico titolo="Elenco dei file non disponibile" errore={elenco.error}
                    spiegazione="Non significa che il pacchetto non abbia file da scaricare."
                    onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
    );
  }

  const d = elenco.data ?? {};
  const files: FileScaricabile[] = (Array.isArray(d.files) ? d.files : []).map((x: any) => {
    const n = NOME_FILE[x.tipo] ?? { etichetta: x.nome ?? x.tipo, nome: () => x.nome ?? "file" };
    return {
      chiave: x.tipo ?? x.nome, url: x.download_url, nome: n.nome(aip.aip_id),
      etichetta: x.tipo === "RDV" && x.rdv_tipo === "preliminare" ? "Rapporto di versamento preliminare (XML)" : n.etichetta,
    };
  }).filter((f: FileScaricabile) => typeof f.url === "string" && f.url);
  const completo: FileScaricabile | null = d.contenuto_accessibile && d.download_completo_url
    ? { chiave: "COMPLETO", etichetta: "Pacchetto completo (ZIP)", url: d.download_completo_url, nome: `AIP_${String(aip.aip_id).slice(0, 8)}_completo.zip` }
    : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="vd-nota">Metadati: indice, elenco dei file e rapporto di versamento.</p>
      {files.length === 0 ? (
        <p className="vd-nota">Il servizio non ha file di metadati scaricabili per questo pacchetto.</p>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          {files.map(f => (
            <button key={f.chiave} type="button" className="vd-bottone vd-bottone--secondario"
                    aria-disabled={inCorso !== null || undefined}
                    onClick={() => { if (inCorso === null) scarica(f); }}>
              {inCorso === f.chiave ? "Scaricamento…" : f.etichetta}
            </button>
          ))}
        </div>
      )}
      <p className="vd-nota" style={{ marginTop: 4 }}>Contenuto: i documenti conservati.</p>
      {completo ? (
        <div>
          <button type="button" className="vd-bottone vd-bottone--secondario" aria-disabled={inCorso !== null || undefined}
                  onClick={() => { if (inCorso === null) scarica(completo); }}>
            {inCorso === completo.chiave ? "Scaricamento…" : completo.etichetta}
          </button>
        </div>
      ) : (
        <p className="vd-avviso">
          <strong>Contenuto non consegnato per questo ente.</strong> I documenti di un pacchetto si scaricano solo dal
          profilo dell'ente che li ha versati: chi amministra il servizio o ne è responsabile vede i metadati di tutti
          gli enti, ma non il contenuto dei loro documenti (regola di riservatezza).
        </p>
      )}
      <p aria-live="polite" className={esito ? `vd-stato ${esito.ok ? "vd-stato--ok" : "vd-stato--critico"}` : undefined}
         style={{ whiteSpace: "normal", minHeight: 20 }}>
        {esito?.testo}
      </p>
    </div>
  );
}

// ── Dettaglio del pacchetto: dialog modale ─────────────────────────
function DettaglioPacchetto({ aip, ente, onClose }: { aip: any; ente: { tenantId: string; globale: boolean; proprio: boolean }; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  const documenti: any[] = Array.isArray(aip.metadati_allegato5) ? aip.metadati_allegato5 : [];
  const st = statoDi(aip.stato);
  const nellaCatena = NELLA_CATENA.includes(aip.stato);
  const classe = aip.classe_documentale;
  const { user } = useAuth();
  const puoEsibire = puo(user, ESIBIZIONE);

  const riga = (etichetta: string, valore: React.ReactNode, mono = false) => (
    <>
      <dt>{etichetta}</dt>
      <dd className={mono && valore ? "vd-mono" : undefined}>{valore || NON_DISPONIBILE}</dd>
    </>
  );

  return (
    <dialog ref={ref} className="vd-dialogo" aria-labelledby="vd-dettaglio-titolo" onClose={onClose}
            onClick={e => { if (e.target === ref.current) ref.current?.close(); }}>
      <div className="vd-dialogo-testa">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 id="vd-dettaglio-titolo" style={{ fontSize: 22, fontWeight: 800, fontStretch: "85%", lineHeight: 1.25, overflowWrap: "anywhere" }}>
            {aip.oggetto || "Pacchetto di archiviazione"}
          </h2>
          <p className="vd-mono" style={{ marginTop: 4, overflowWrap: "anywhere" }}>{aip.urn || aip.aip_id}</p>
          <p className={`vd-stato ${st.classe}`} style={{ marginTop: 6 }}>{st.testo}</p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {puoEsibire && (
            // revisione R17 (R17-07): esibizione con il pacchetto già scelto
            <Link className="vd-bottone" to={`/distribution?aip=${encodeURIComponent(aip.aip_id)}`}
                  state={{ aip_id: aip.aip_id, oggetto: aip.oggetto ?? null, urn: aip.urn ?? null,
                           classe: classe ? (classe.nome || classe.codice) : null }}>
              Esibisci
            </Link>
          )}
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => ref.current?.close()}>
            Chiudi
          </button>
        </div>
      </div>

      <div className="vd-dialogo-corpo">
        <section aria-labelledby="vd-dl-id">
          <h3 id="vd-dl-id">Identificazione</h3>
          <dl className="vd-dati">
            {riga("Segnatura", aip.urn, true)}
            {riga("Identificativo del pacchetto (PdA)", aip.aip_id, true)}
            {riga("Classe documentale", classe ? `${classe.nome || classe.codice}${classe.nome && classe.codice ? ` (${classe.codice})` : ""}` : null)}
            {riga("Impronta SHA-256", aip.hash_sha256, true)}
            {riga("Dimensione", byte(aip.dimensione_bytes))}
            {riga("Versato il", dataIt(aip.data_versamento) && `${dataIt(aip.data_versamento)} alle ${oraIt(aip.data_versamento)}`)}
            {riga("Conservato il", dataIt(aip.data_conservazione) && `${dataIt(aip.data_conservazione)} alle ${oraIt(aip.data_conservazione)}`)}
            {riga("Conservare fino al", permanente(aip) ? "Illimitata (conservazione permanente)" : dataIt(aip.scadenza_conservazione) &&
              `${dataIt(aip.scadenza_conservazione)}${typeof aip.anni_conservazione === "number" ? ` (${aip.anni_conservazione} anni)` : ""}`)}
          </dl>
        </section>

        <section aria-labelledby="vd-dl-meta">
          <h3 id="vd-dl-meta">Metadati dei documenti (Allegato 5)</h3>
          {documenti.length === 0 ? (
            <p className="vd-nota" style={{ marginTop: 10 }}>Il servizio non ha restituito documenti per questo pacchetto.</p>
          ) : documenti.map((doc, i) => {
            const m = doc.metadati as Record<string, unknown> | null;
            const presenti = m ? METADATI_ALLEGATO5.filter(c => valoreMetadato(c.chiave, m[c.chiave]) !== null) : [];
            const altri = m ? Object.keys(m).filter(k => !METADATI_ALLEGATO5.some(c => c.chiave === k) && valoreMetadato(k, m[k]) !== null) : [];
            const mancanti = m ? METADATI_ALLEGATO5.length - presenti.length : 0;
            return (
              <div key={doc.documento_id ?? i} style={{ marginTop: 14 }}>
                <p style={{ fontWeight: 700, overflowWrap: "anywhere" }}>
                  {documenti.length > 1 ? `${i + 1}. ` : ""}{doc.nome_file || "Documento senza nome"}
                  <span className="vd-nota" style={{ display: "block", fontWeight: 400, fontSize: 13 }}>
                    {[doc.formato_mime, doc.hash_sha256 ? `impronta ${String(doc.hash_sha256).slice(0, 16)}…` : null].filter(Boolean).join(" · ")}
                  </span>
                </p>
                {!m ? (
                  <p className="vd-stato vd-stato--rischio" style={{ marginTop: 6, whiteSpace: "normal" }}>
                    Metadati non registrati per questo documento: il servizio non ha una riga dell'Allegato 5.
                  </p>
                ) : (
                  <>
                    <dl className="vd-dati" style={{ marginTop: 6 }}>
                      {presenti.map(c => <Fragment key={c.chiave}>{riga(c.nome, valoreMetadato(c.chiave, m[c.chiave]))}</Fragment>)}
                      {altri.map(k => <Fragment key={k}>{riga(nomeMetadato(k), valoreMetadato(k, m[k]))}</Fragment>)}
                    </dl>
                    {mancanti > 0 && (
                      <p className="vd-nota" style={{ marginTop: 6, fontSize: 13 }}>
                        {mancanti === 1 ? "Un altro campo dell'Allegato 5 è vuoto." : `Altri ${mancanti} campi dell'Allegato 5 sono vuoti.`}
                      </p>
                    )}
                  </>
                )}
              </div>
            );
          })}
        </section>

        <section aria-labelledby="vd-dl-catena">
          <h3 id="vd-dl-catena">Catena delle impronte</h3>
          <p style={{ marginTop: 10 }}>
            {nellaCatena ? (
              <><strong className="vd-stato vd-stato--ok">Iscritto</strong> secondo lo stato registrato dal servizio. </>
            ) : (
              <><strong className="vd-stato vd-stato--neutro">Non ancora iscritto</strong>: il pacchetto non ha ancora raggiunto la catena. </>
            )}
            Questa scheda non verifica l'impronta: la prova d'integrità si controlla in <Link to="/blockchain" style={{ fontWeight: 700 }}>Blocchi</Link>.
          </p>
          <p className="vd-avviso" style={{ marginTop: 14 }}>
            <strong className="vd-stato--rischio">Firma e marca non qualificate.</strong> Il servizio non appone ancora firma digitale
            e marca temporale qualificate: «Firmato» e «Marcato» indicano la fase di lavorazione raggiunta, non una garanzia legale.
          </p>
        </section>

        <section aria-labelledby="vd-dl-scarica">
          <h3 id="vd-dl-scarica">Scarica</h3>
          <div style={{ marginTop: 12 }}>
            <Scaricamenti aip={aip} ente={ente} />
          </div>
        </section>
      </div>
    </dialog>
  );
}

// ── Pagina ─────────────────────────────────────────────────────────
export default function AIPPage() {
  const ente = useEnte();
  const tenantId = ente.tenantId;

  const [stato, setStato] = useState("");
  const [pagina, setPagina] = useState(0);
  // R03 (REV-06): cursori[n] apre la pagina n (la prima senza cursore); niente totale
  const [cursori, setCursori] = useState<(string | null)[]>([null]);
  const ricomincia = () => { setPagina(0); setCursori([null]); };
  const [aperto, setAperto] = useState<any | null>(null);
  const origine = useRef<HTMLButtonElement | null>(null);

  const elenco = useQuery({
    queryKey: ["aip", tenantId, stato, pagina, cursori[pagina]],
    queryFn: () => fetchAIP(tenantId, stato || undefined, cursori[pagina], LIMITE),
    enabled: !!tenantId,
  });

  const righe: any[] = elenco.data?.items ?? [];
  const primo = pagina * LIMITE + 1;
  const ultimo = pagina * LIMITE + righe.length;
  const prossimo: string | null = elenco.data?.altri && elenco.data?.cursore ? elenco.data.cursore : null;
  const piuPagine = pagina > 0 || prossimo !== null;
  const avanti = () => {
    if (!prossimo) return;
    setCursori(c => [...c.slice(0, pagina + 1), prossimo]);
    setPagina(p => p + 1);
  };

  const apri = (aip: any, bottone: HTMLButtonElement) => { origine.current = bottone; setAperto(aip); };
  const chiudi = () => { setAperto(null); requestAnimationFrame(() => origine.current?.focus()); };

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Pacchetti conservati</h1>
        <span className="vd-nota">
          I pacchetti di archiviazione (AIP) costruiti secondo UNI 11386:2020 SInCRO, dal più recente, con i metadati di ogni documento.
        </span>
      </div>

      <div className="vd-filtri">
        <SceltaEnte id="vd-aip-ente" stato={ente} onCambia={ricomincia} />
        <div className="vd-campo">
          <label htmlFor="vd-aip-stato">Stato</label>
          <select id="vd-aip-stato" className="vd-input" value={stato} onChange={e => { setStato(e.target.value); ricomincia(); }}>
            {FILTRI_STATO.map(s => <option key={s} value={s}>{s ? statoDi(s).testo : "Tutti gli stati"}</option>)}
          </select>
        </div>
      </div>

      {ente.globale && tenantId && !ente.proprio && (
        <p className="vd-avviso" style={{ marginBottom: 24 }}>
          <strong>Stai consultando un altro ente.</strong> Vedi i metadati dei suoi pacchetti e puoi scaricarne indice,
          elenco dei file e rapporto di versamento; il contenuto dei documenti si consegna solo al profilo dell'ente
          che li ha versati.
        </p>
      )}

      <section aria-labelledby="vd-aip-tabellone">
        <div className="vd-sezione-testa">
          <h2 id="vd-aip-tabellone" className="vd-etichetta">Archivio</h2>
          {elenco.isSuccess && (
            <span className="vd-nota">
              {righe.length > 0 ? `Pacchetti ${num(primo)}–${num(ultimo)}` : "Nessun pacchetto"}{stato ? ` nello stato «${statoDi(stato).testo.toLowerCase()}»` : ""}{ente.nomeEnte ? `, ${ente.nomeEnte}` : ""}{prossimo ? ", dal più recente: ce ne sono altri" : ""}
            </span>
          )}
        </div>

        {!tenantId ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>
            {ente.globale ? "Scegli un ente per vedere i suoi pacchetti." : "Il tuo profilo non è collegato a un ente: chiedi all'amministrazione del servizio."}
          </p>
        ) : elenco.isLoading ? (
          <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura dei pacchetti…</p>
        ) : elenco.isError ? (
          <EsitoCritico titolo="Pacchetti conservati non disponibili" errore={elenco.error}
                        spiegazione="Non significa che l'archivio sia vuoto."
                        onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
        ) : righe.length === 0 ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>
            {stato ? `Nessun pacchetto nello stato «${statoDi(stato).testo.toLowerCase()}».` : "Nessun pacchetto conservato per questo ente."}
          </p>
        ) : (
          <>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Pacchetti di archiviazione, da {num(primo)} a {num(ultimo)}</caption>
                <thead>
                  <tr><th scope="col">Versato</th><th scope="col">Pacchetto</th><th scope="col">Conservare fino al</th><th scope="col">Stato</th></tr>
                </thead>
                <tbody>
                  {righe.map(aip => {
                    const st = statoDi(aip.stato);
                    const docs: any[] = Array.isArray(aip.metadati_allegato5) ? aip.metadati_allegato5 : [];
                    const senzaMeta = docs.filter(d => !d.metadati).length;
                    const classe = aip.classe_documentale;
                    return (
                      <tr key={aip.aip_id}>
                        <td className="vd-ora" data-label="Versato">
                          {dataIt(aip.data_versamento)?.slice(0, 5) ?? "—"}
                          <span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>
                            {dataIt(aip.data_versamento) ? `${dataIt(aip.data_versamento)!.slice(6)}, ${oraIt(aip.data_versamento)}` : "data non indicata"}
                          </span>
                        </td>
                        <td style={{ minWidth: 0 }} data-label="Pacchetto">
                          <button type="button" className="vd-link-bottone" style={{ fontSize: 16, overflowWrap: "anywhere", textAlign: "left" }}
                                  aria-haspopup="dialog" onClick={e => apri(aip, e.currentTarget)}>
                            {aip.oggetto || aip.urn || `Senza segnatura · ${String(aip.aip_id).slice(0, 8)}`}
                          </button>
                          <span className="vd-sotto">
                            {classe ? (classe.nome || classe.codice) : "Classe non indicata"}
                            {" · "}{docs.length === 1 ? "1 documento" : `${docs.length} documenti`}
                            {senzaMeta > 0 && <span className="vd-stato vd-stato--rischio">{` · ${senzaMeta} senza metadati`}</span>}
                          </span>
                          {aip.oggetto && aip.urn && <span className="vd-sotto vd-mono" style={{ overflowWrap: "anywhere" }}>{aip.urn}</span>}
                        </td>
                        <td data-label="Conservare fino al">{permanente(aip) ? "Illimitata" : dataIt(aip.scadenza_conservazione) ?? <span className="vd-nota">non indicata</span>}</td>
                        <td className={`vd-stato ${st.classe}`} style={{ whiteSpace: "normal" }} data-label="Stato">{st.testo}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {piuPagine && (
              <nav aria-label="Pagine dell'archivio" className="vd-paginazione">
                <button type="button" className="vd-bottone vd-bottone--secondario" disabled={pagina === 0}
                        onClick={() => setPagina(p => Math.max(0, p - 1))}>Precedente</button>
                <span className="vd-nota">Pacchetti {num(primo)}–{num(ultimo)}</span>
                <button type="button" className="vd-bottone vd-bottone--secondario" disabled={prossimo === null}
                        onClick={avanti}>Successiva</button>
              </nav>
            )}
          </>
        )}
      </section>

      {aperto && <DettaglioPacchetto aip={aperto} ente={ente} onClose={chiudi} />}
    </>
  );
}
