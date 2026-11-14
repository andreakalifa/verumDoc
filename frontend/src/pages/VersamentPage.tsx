// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Pacchetti in arrivo — sistema grafico «Partenze» (VD-DR-01).
 * Tabellone degli arrivi: ogni pacchetto di versamento ricevuto con l'ora e l'esito,
 * le stesse parole di stato del quadro del servizio. Aggiornato ogni 30 secondi.
 * g3-frontend (g2-dati §6.2): filtro per stato e, per i rifiutati, il motivo del
 * rifiuto (`motivo_rifiuto`, `motivi_rifiuto`). R03: pagine a cursore (il servizio
 * non conta più i versamenti: a milioni di righe il conteggio non regge).
 * R07: «Rielabora» dei propri versamenti rifiutati, direttamente dall'ente.
 */
import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchVersamenti } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { SceltaEnte, useEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Colonna, SceltaColonne, useColonne } from "../components/Colonne";
import { RielaboraVersamento } from "../components/RielaboraVersamento";

// R07 (D31): colonne visibili scelte dalla persona (il pacchetto resta sempre)
const COLONNE: Colonna[] = [
  { id: "ricevuto", etichetta: "Ricevuto" },
  { id: "pacchetto", etichetta: "Pacchetto di versamento", fissa: true },
  { id: "esito", etichetta: "Esito" },
];

const LIMITE = 50;
const FILTRI_STATO: [string, string][] = [
  ["", "Tutti gli stati"], ["ERRORE", "Solo rifiutati"], ["CONSERVATO", "Solo conservati"],
  ["RICEVUTO", "Ricevuti"], ["IN_VALIDAZIONE", "In verifica"], ["VALIDATO", "Verifica superata"],
  ["IN_BUILD", "In conservazione"], ["ANNULLATO", "Annullati"],
];
/** Passi della lavorazione come parole del mestiere (`motivi_rifiuto[].passo`). */
const nomePasso = (p?: string | null) => p ? p.replace(/_/g, " ").replace(/^./, c => c.toUpperCase()) : null;

/** Stati del pacchetto di versamento (PdvStato), come nel quadro del servizio. */
const ESITO: Record<string, { testo: string; classe: string; gruppo: "conservati" | "lavorazione" | "rifiutati" | "altri" }> = {
  RICEVUTO:       { testo: "Ricevuto", classe: "vd-stato--neutro", gruppo: "lavorazione" },
  IN_VALIDAZIONE: { testo: "In verifica", classe: "vd-stato--info", gruppo: "lavorazione" },
  VALIDATO:       { testo: "Verifica superata", classe: "vd-stato--info", gruppo: "lavorazione" },
  IN_BUILD:       { testo: "In conservazione", classe: "vd-stato--info", gruppo: "lavorazione" },
  AIP_IN_BUILD:   { testo: "In conservazione", classe: "vd-stato--info", gruppo: "lavorazione" },
  IN_CATENA:      { testo: "Conservato", classe: "vd-stato--ok", gruppo: "conservati" },
  CONSERVATO:     { testo: "Conservato", classe: "vd-stato--ok", gruppo: "conservati" },
  ERRORE:         { testo: "Rifiutato", classe: "vd-stato--critico", gruppo: "rifiutati" },
  ANNULLATO:      { testo: "Annullato", classe: "vd-stato--neutro", gruppo: "altri" },
};
const esitoDi = (s?: string) =>
  (s ? ESITO[s] : undefined) ?? { testo: s ? `Stato «${s}»` : "Non indicato", classe: "vd-stato--neutro", gruppo: "altri" as const };

const ora = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
};
const giorno = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const oggi = new Date(); oggi.setHours(0, 0, 0, 0);
  const g = new Date(d); g.setHours(0, 0, 0, 0);
  const diff = Math.round((oggi.getTime() - g.getTime()) / 86_400_000);
  if (diff === 0) return "oggi";
  if (diff === 1) return "ieri";
  return d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" });
};

/** R08 (D01): esito delle firme dal rapporto di verifica, in parole. */
interface FirmaRiassunto { firmatario: string | null; indicazione: string | null; revoca: string | null; motivi: string[] | null }
interface FileFirme { nome_file: string; esito: string | null; motivo: string | null; firme: FirmaRiassunto[] }
const PAROLA_FIRMA: Record<string, string> = { valida: "vd-stato--ok", "non valida": "vd-stato--critico", indeterminata: "vd-stato--rischio" };

function EsitoFirme({ firme }: { firme: { livello: string; file: FileFirme[] } }) {
  const [aperto, setAperto] = useState(false);
  const firmati = firme.file.filter(f => f.esito && f.esito !== "non firmato");
  if (firmati.length === 0) return null;
  const conta = (e: string) => firmati.filter(f => f.esito === e).length;
  const sintesi = [["valida", "valide"], ["non valida", "non valide"], ["indeterminata", "indeterminate"], ["da verificare", "da verificare"]]
    .map(([e, plurale]) => { const n = conta(e); return n ? `${n} ${n === 1 ? e : plurale}` : null; })
    .filter(Boolean).join(", ");
  return (
    <>
      <button type="button" className="vd-comando-testo" aria-expanded={aperto} onClick={() => setAperto(a => !a)}>
        {aperto ? "Nascondi le firme" : `Firme: ${sintesi || `${firmati.length} file firmati`}`}
      </button>
      {aperto && (
        <dl className="vd-dati" style={{ marginTop: 4 }}>
          {firmati.map(f => (
            <Fragment key={f.nome_file}>
              <dt className="vd-a-capo">{f.nome_file}<span className="vd-sotto">verifica «{firme.livello.toLowerCase().replace("_", " ")}»</span></dt>
              <dd className="vd-a-capo">
                <span className={`vd-stato ${PAROLA_FIRMA[f.esito ?? ""] ?? "vd-stato--neutro"}`}>{f.esito}</span>
                {f.firme.map((x, k) => (
                  <span key={k} className="vd-sotto">
                    {x.firmatario || "Firmatario non indicato"}{x.motivi && x.motivi.length ? `: ${x.motivi.join("; ")}` : ""}
                  </span>
                ))}
                {f.firme.length === 0 && f.motivo && <span className="vd-sotto">{f.motivo}</span>}
              </dd>
            </Fragment>
          ))}
        </dl>
      )}
    </>
  );
}

export default function VersamentPage() {
  const { hasScope } = useAuth();
  const ente = useEnte();
  const colonne = useColonne("versamenti", COLONNE);
  const tenantId = ente.tenantId;
  // R07: il collegamento «Rielabora» dell'avviso apre i rifiutati (?stato=ERRORE)
  const [parametri] = useSearchParams();
  const [stato, setStato] = useState(() => FILTRI_STATO.some(([v]) => v === parametri.get("stato"))
    ? parametri.get("stato") ?? "" : "");
  const [pagina, setPagina] = useState(0);
  // cursori[n] apre la pagina n (la prima senza cursore); si allunga andando avanti
  const [cursori, setCursori] = useState<(string | null)[]>([null]);
  const ricomincia = () => { setPagina(0); setCursori([null]); };
  const [aperti, setAperti] = useState<Record<string, boolean>>({});

  const arrivi = useQuery({
    queryKey: ["versamenti", tenantId, stato, pagina, cursori[pagina]],
    queryFn: () => fetchVersamenti(tenantId, LIMITE, cursori[pagina], stato || undefined),
    enabled: !!tenantId,
    refetchInterval: 30_000,
  });

  // Il servizio ordina per data di ricezione decrescente (archivio.elenco_versamenti:
  // ORDER BY data_ricezione DESC, id); la pagina riordina comunque la sua pagina, così
  // un servizio più vecchio o un proxy non mostrano mai gli arrivi fuori ordine.
  const ms = (iso?: string | null) => { const t = iso ? new Date(iso).getTime() : NaN; return Number.isNaN(t) ? -Infinity : t; };
  const righe: any[] = [...(arrivi.data?.items ?? [])].sort((a, b) => ms(b.data_ricezione) - ms(a.data_ricezione));
  const primo = pagina * LIMITE + 1;
  const ultimo = pagina * LIMITE + righe.length;
  const prossimo: string | null = arrivi.data?.altri && arrivi.data?.cursore ? arrivi.data.cursore : null;
  const piuPagine = pagina > 0 || prossimo !== null;
  const avanti = () => {
    if (!prossimo) return;
    setCursori(c => [...c.slice(0, pagina + 1), prossimo]);
    setPagina(p => p + 1);
  };
  const conta = { conservati: 0, lavorazione: 0, rifiutati: 0, altri: 0 };
  righe.forEach(r => { conta[esitoDi(r.stato).gruppo] += 1; });
  const aggiornato = arrivi.dataUpdatedAt ? new Date(arrivi.dataUpdatedAt).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : null;
  const nomeFiltro = FILTRI_STATO.find(([v]) => v === stato)?.[1].toLowerCase();

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Pacchetti in arrivo</h1>
        <span className="vd-nota">I pacchetti di versamento ricevuti, dal più recente, con l'esito della lavorazione e il motivo di ogni rifiuto.</span>
        {hasScope("versamento:ud") && (
          <div className="vd-testa-comandi">
            <Link to="/versamento" className="vd-comando-testo">Versa un documento</Link>
          </div>
        )}
      </div>

      <div className="vd-filtri">
        <SceltaEnte id="vd-arrivi-ente" stato={ente} onCambia={ricomincia} />
        <div className="vd-campo">
          <label htmlFor="vd-arrivi-stato">Esito</label>
          <select id="vd-arrivi-stato" className="vd-input" value={stato} onChange={e => { setStato(e.target.value); ricomincia(); }}>
            {FILTRI_STATO.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </div>
      </div>

      <section aria-labelledby="vd-arrivi-tab">
        <div className="vd-sezione-testa">
          <h2 id="vd-arrivi-tab" className="vd-etichetta">Arrivi</h2>
          {arrivi.isSuccess && (
            <span className="vd-nota" aria-live="polite">
              {`${piuPagine ? `Pagina ${pagina + 1}` : `${righe.length} ${righe.length === 1 ? "versamento" : "versamenti"}`}${stato ? ` (${nomeFiltro})` : ""}`}
              {aggiornato ? `, aggiornato alle ${aggiornato}` : ""}
            </span>
          )}
        </div>

        {!tenantId ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>
            {ente.globale ? "Scegli un ente per vedere i suoi versamenti." : "Il tuo profilo non è collegato a un ente: chiedi all'amministrazione del servizio."}
          </p>
        ) : arrivi.isLoading ? (
          <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura dei versamenti…</p>
        ) : arrivi.isError && righe.length === 0 ? (
          <EsitoCritico titolo="Versamenti non disponibili" errore={arrivi.error}
                        spiegazione="Non significa che non ne siano arrivati."
                        onRiprova={() => arrivi.refetch()} inRiprova={arrivi.isFetching} />
        ) : righe.length === 0 ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>
            {stato ? `Nessun versamento con esito «${nomeFiltro}» per questo ente.` : "Nessun versamento ricevuto per questo ente."}
          </p>
        ) : (
          <>
            {arrivi.isError && (
              <EsitoCritico titolo="Aggiornamento non disponibile" errore={arrivi.error}
                            spiegazione={`Le righe qui sotto sono quelle dell'ultima lettura${aggiornato ? `, delle ${aggiornato}` : ""}.`}
                            onRiprova={() => arrivi.refetch()} inRiprova={arrivi.isFetching} />
            )}
            {!stato && (
              <p style={{ fontSize: 15, marginBottom: 12 }}>
                {piuPagine ? "In questa pagina: " : "Fra questi: "}<strong className="vd-stato vd-stato--ok">{conta.conservati} conservati</strong>
                {" · "}<strong className="vd-stato vd-stato--info">{conta.lavorazione} in lavorazione</strong>
                {" · "}<strong className={`vd-stato ${conta.rifiutati ? "vd-stato--critico" : "vd-stato--neutro"}`}>{conta.rifiutati} rifiutati</strong>
                {conta.altri > 0 && <>{" · "}<strong className="vd-stato vd-stato--neutro">{conta.altri} {conta.altri === 1 ? "altro" : "altri"}</strong></>}
              </p>
            )}
            <SceltaColonne tabella="versamenti" colonne={COLONNE} stato={colonne} />
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Pacchetti di versamento ricevuti, dal più recente, da {primo} a {ultimo}</caption>
                <thead>
                  <tr>
                    {colonne.visibile("ricevuto") && <th scope="col">Ricevuto</th>}
                    <th scope="col">Pacchetto di versamento</th>
                    {colonne.visibile("esito") && <th scope="col">Esito</th>}
                  </tr>
                </thead>
                <tbody>
                  {righe.map(v => {
                    const e = esitoDi(v.stato);
                    const motivi: any[] = Array.isArray(v.motivi_rifiuto) ? v.motivi_rifiuto : [];
                    const rifiutato = v.stato === "ERRORE";
                    const aperto = !!aperti[v.pdv_id];
                    return (
                      <tr key={v.pdv_id}>
                        {colonne.visibile("ricevuto") && (
                        <td className="vd-ora" data-label="Ricevuto">
                          {ora(v.data_ricezione) ?? "—"}
                          <span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>{giorno(v.data_ricezione) ?? "ora non indicata"}</span>
                        </td>
                        )}
                        <td style={{ minWidth: 0 }} data-label="Pacchetto di versamento">
                          <span style={{ fontWeight: 600, overflowWrap: "anywhere" }}>{v.oggetto || <span className="vd-nota">Oggetto non indicato</span>}</span>
                          <span className="vd-sotto">
                            {[v.classe_documentale ? (v.classe_documentale.nome || v.classe_documentale.codice) : null,
                              v.numero_protocollo ? `protocollo ${v.numero_protocollo}` : null].filter(Boolean).join(" · ") || "Classe e protocollo non indicati"}
                          </span>
                          <span className="vd-sotto vd-mono" style={{ overflowWrap: "anywhere" }}>{v.pdv_id}</span>
                        </td>
                        {colonne.visibile("esito") && (
                        <td style={{ whiteSpace: "normal", minWidth: 0 }} data-label="Esito">
                          <span className={`vd-stato ${e.classe}`} style={{ display: "block" }}>{e.testo}</span>
                          {rifiutato && (
                            <span className="vd-sotto" style={{ color: "var(--vd-testo)", overflowWrap: "anywhere" }}>
                              {v.motivo_rifiuto || "Motivo non registrato dal servizio"}
                            </span>
                          )}
                          {rifiutato && <RielaboraVersamento pdvId={v.pdv_id} />}
                          {v.firme && <EsitoFirme firme={v.firme} />}
                          {rifiutato && motivi.length > 0 && (
                            <>
                              <button type="button" className="vd-comando-testo" aria-expanded={aperto}
                                      onClick={() => setAperti(a => ({ ...a, [v.pdv_id]: !a[v.pdv_id] }))}>
                                {aperto ? "Nascondi i controlli" : `${motivi.length === 1 ? "Il controllo non superato" : `I ${motivi.length} controlli non superati`}`}
                              </button>
                              {aperto && (
                                <dl className="vd-dati" style={{ marginTop: 4 }}>
                                  {motivi.map((m, k) => (
                                    <Fragment key={k}>
                                      <dt>{nomePasso(m.passo) ?? "Controllo"}{m.codice ? <span className="vd-sotto vd-mono">{m.codice}</span> : null}</dt>
                                      <dd className="vd-a-capo">{m.messaggio || "messaggio non registrato"}{m.tipo === "tecnico" ? <span className="vd-sotto">Errore del servizio, non dei dati versati.</span> : null}</dd>
                                    </Fragment>
                                  ))}
                                </dl>
                              )}
                            </>
                          )}
                        </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {piuPagine && (
              <nav aria-label="Pagine degli arrivi" className="vd-paginazione">
                <button type="button" className="vd-bottone vd-bottone--secondario" disabled={pagina === 0}
                        onClick={() => setPagina(p => Math.max(0, p - 1))}>Precedente</button>
                <span className="vd-nota">Versamenti {primo.toLocaleString("it-IT")}–{ultimo.toLocaleString("it-IT")}</span>
                <button type="button" className="vd-bottone vd-bottone--secondario" disabled={prossimo === null}
                        onClick={avanti}>Successiva</button>
              </nav>
            )}
          </>
        )}
      </section>
    </>
  );
}
