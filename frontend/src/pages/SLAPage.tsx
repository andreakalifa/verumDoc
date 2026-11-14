// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Livelli di servizio — sistema «Partenze» (VD-DR-01).
 * VD-C-029: niente «conforme» senza dati. Prima la pagina mostrava 99,95% e 99,98%
 * di riserva quando il servizio non li forniva, e un segno di spunta fisso.
 * Ora ogni indicatore mostra solo ciò che il servizio misura: se manca la misura
 * l'esito è «Non misurato», e una misura senza fonte dichiarata lo dice nella riga.
 * Rimosso il rimando a Grafana: l'osservabilità non è collegata (VD-C-033).
 * g3-frontend (g2-dati §6.7): ogni indicatore si legge da `misure[chiave]`
 * (valore, obiettivo, raggiunto, fonte, campioni, motivo) o, in mancanza, da
 * `conformita[chiave]` / `conformita.fonti[chiave]` / `conformita.raggiunto[chiave]`.
 * Valore null = «Non misurato» con il motivo del servizio. Oggi l'unica misura
 * reale è il p99 del tempo dal versamento alla conservazione.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { fetchSLA, fetchSLAThroughput, fetchTenantsStats } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { EsitoCritico } from "../components/EsitoCritico";

const num = (n: unknown, cifre = 0) =>
  typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("it-IT", { maximumFractionDigits: cifre }) : null;

/** Indicatori noti: chiave dell'obiettivo (sla_targets) = chiave della misura (conformita). */
const INDICATORI: { chiave: string; nome: string; tipo: "pct" | "ms" }[] = [
  { chiave: "uptime_pct",           nome: "Disponibilità del servizio nel mese", tipo: "pct" },
  { chiave: "disponibilita_pct",    nome: "Disponibilità delle interfacce di versamento e consultazione", tipo: "pct" },
  { chiave: "versamento_p95_ms",    nome: "Tempo di risposta al versamento, 95% delle richieste", tipo: "ms" },
  { chiave: "conservazione_p99_ms", nome: "Tempo dal versamento alla conservazione, 99% dei pacchetti", tipo: "ms" },
];

const formato = (v: number, tipo: "pct" | "ms") =>
  tipo === "pct" ? `${num(v, 2)}%` : v >= 1000 ? `${num(v / 1000, 1)} s` : `${num(v)} ms`;

const STATO_ENTE: Record<string, string> = {
  ATTIVO: "Attivi", TRIAL: "In prova", SOSPESO: "Sospesi", CESSATO: "Cessati", IN_ATTIVAZIONE: "In attivazione",
};

const mesi = Array.from({ length: 6 }, (_, i) => {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - i);
  const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  return { val, nome: d.toLocaleDateString("it-IT", { month: "long", year: "numeric" }) };
});

/** Il servizio restituisce solo i giorni con versamenti: i giorni assenti valgono zero. */
function serieGiorni(perGiorno: { data: string; aip: number }[], giorni: number) {
  const mappa = new Map(perGiorno.map(r => [String(r.data).slice(0, 10), Number(r.aip) || 0]));
  const oggi = new Date();
  return Array.from({ length: giorni }, (_, i) => {
    const d = new Date(Date.UTC(oggi.getUTCFullYear(), oggi.getUTCMonth(), oggi.getUTCDate() - (giorni - 1 - i)));
    const iso = d.toISOString().slice(0, 10);
    return { data: iso, etichetta: `${iso.slice(8, 10)}/${iso.slice(5, 7)}`, aip: mappa.get(iso) ?? 0 };
  });
}

function Suggerimento({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div style={{ background: "var(--vd-carta)", border: "1px solid var(--vd-testo)", padding: "6px 10px", fontSize: 14 }}>
      <div style={{ fontWeight: 700, fontStretch: "75%", fontSize: 16 }}>{p.etichetta}</div>
      <div>{num(p.aip)} pacchetti versati</div>
    </div>
  );
}


/**
 * Da dove viene una misura, in parole del mestiere: la fonte che il servizio
 * dichiara (`misure[k].fonte`) è scritta per i tecnici («database: aip.… − pdv.…»).
 */
function fonteLeggibile(f: string): string {
  const t = f.toLowerCase();
  if (/data_conservazione/.test(t) && /data_ricezione/.test(t))
    return "misurata sull'archivio: tempo dalla ricezione del versamento alla conservazione del pacchetto";
  if (/^(database|db)\b/.test(t)) return "misurata sui registri dell'archivio";
  if (/prometheus|monitor|uptime|probe|sonda/.test(t)) return "misurata dal sistema di monitoraggio";
  if (/log/.test(t)) return "ricavata dai registri del servizio";
  return "fonte dichiarata dal servizio";
}

export default function SLAPage() {
  const { user } = useAuth();
  const isGlobal = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  const [giorni, setGiorni] = useState(30);
  const [mese, setMese] = useState("");

  const sla = useQuery({ queryKey: ["sla", mese], queryFn: () => fetchSLA(mese || undefined) });
  const tp = useQuery({ queryKey: ["sla-throughput", giorni], queryFn: () => fetchSLAThroughput(giorni) });
  const enti = useQuery({ queryKey: ["admin-stats"], queryFn: fetchTenantsStats, enabled: isGlobal });

  const obiettivi: Record<string, unknown> = sla.data?.sla_targets ?? {};
  const conformita: Record<string, any> = sla.data?.conformita ?? {};
  const misureDettaglio: Record<string, any> = sla.data?.misure ?? {};
  const metriche: Record<string, unknown> = sla.data?.metriche ?? {};
  /** Misura di un indicatore nella forma unica { valore, obiettivo, raggiunto, fonte, campioni, motivo }. */
  const misura = (k: string) => {
    const m = misureDettaglio[k] ?? {};
    const valore = typeof m.valore === "number" ? m.valore : typeof conformita[k] === "number" ? conformita[k] : null;
    const obiettivo = typeof m.obiettivo === "number" ? m.obiettivo : typeof obiettivi[k] === "number" ? obiettivi[k] as number : null;
    const fonte = typeof m.fonte === "string" ? m.fonte : typeof conformita.fonti?.[k] === "string" ? conformita.fonti[k] : null;
    const r = typeof m.raggiunto === "boolean" ? m.raggiunto : typeof conformita.raggiunto?.[k] === "boolean" ? conformita.raggiunto[k] : null;
    return { valore, obiettivo, fonte, raggiunto: r as boolean | null, campioni: typeof m.campioni === "number" ? m.campioni : null, motivo: typeof m.motivo === "string" ? m.motivo : null };
  };
  const righe = INDICATORI.filter(ind => misura(ind.chiave).obiettivo !== null);
  const misureSenzaFonte = righe.some(r => { const m = misura(r.chiave); return m.valore !== null && !m.fonte; });
  const nomeMese = mese ? mesi.find(m => m.val === mese)?.nome : "mese in corso";

  const serie = tp.data ? serieGiorni(Array.isArray(tp.data.per_giorno) ? tp.data.per_giorno : [], giorni) : [];
  const totale = serie.reduce((s, r) => s + r.aip, 0);

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Livelli di servizio</h1>
        <span className="vd-nota">Gli obiettivi promessi agli enti e quanto il servizio ha davvero misurato.</span>
        <div className="vd-testa-comandi">
          <div className="vd-campo" style={{ width: 220 }}>
            <label htmlFor="vdc-mese">Mese</label>
            <select id="vdc-mese" className="vd-input" value={mese} onChange={e => setMese(e.target.value)}>
              <option value="">Mese in corso</option>
              {mesi.map(m => <option key={m.val} value={m.val}>{m.nome}</option>)}
            </select>
          </div>
        </div>
      </div>

      <div className="vd-pila">
        {/* ── Obiettivi ───────────────────────────────────────────── */}
        <section aria-labelledby="vdc-obiettivi">
          <div className="vd-sezione-testa">
            <h2 id="vdc-obiettivi" className="vd-etichetta">Obiettivi del mese</h2>
            <span className="vd-nota">{nomeMese}</span>
          </div>
          {sla.isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura dei livelli di servizio…</p>
          ) : sla.isError ? (
            <EsitoCritico titolo="Livelli di servizio non disponibili" errore={sla.error} spiegazione="Gli indicatori sono sconosciuti, non raggiunti né mancati." onRiprova={() => sla.refetch()} inRiprova={sla.isFetching} />
          ) : righe.length === 0 ? (
            <div className="vd-esito">
              <p className="vd-esito-titolo">Obiettivi non indicati</p>
              <p>Il servizio non ha restituito gli obiettivi di servizio: non c'è nulla da confrontare.</p>
            </div>
          ) : (
            <>
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila vd-coppie">
                  <caption className="vd-sr">Obiettivi di servizio, misura del mese ed esito</caption>
                  <thead>
                    <tr><th scope="col">Indicatore</th><th scope="col">Obiettivo</th><th scope="col">Misurato</th><th scope="col">Esito</th></tr>
                  </thead>
                  <tbody>
                    {righe.map(ind => {
                      const m = misura(ind.chiave);
                      const ob = m.obiettivo as number;
                      const misurato = m.valore !== null && Number.isFinite(m.valore);
                      // L'esito lo dà il servizio; se manca lo si ricava solo da un valore misurato
                      const raggiunto = !misurato ? null
                        : m.raggiunto ?? (ind.tipo === "pct" ? (m.valore as number) >= ob : (m.valore as number) <= ob);
                      return (
                        <tr key={ind.chiave}>
                          <th scope="row" className="vd-riga" style={{ fontWeight: 600 }}>{ind.nome}</th>
                          <td style={{ whiteSpace: "nowrap" }} data-label="Obiettivo">{ind.tipo === "pct" ? "almeno" : "entro"} {formato(ob, ind.tipo)}</td>
                          <td style={{ minWidth: 0 }} data-label="Misurato">
                            {misurato
                              ? <span style={{ fontStretch: "75%", fontWeight: 700, fontSize: 20 }}>{formato(m.valore as number, ind.tipo)}</span>
                              : <span className="vd-stato vd-stato--neutro">Non misurato</span>}
                            {misurato && (
                              <span className="vd-sotto vd-a-capo">
                                {m.fonte ? fonteLeggibile(m.fonte) : "fonte della misura non indicata"}
                                {m.campioni !== null ? ` · ${num(m.campioni)} ${m.campioni === 1 ? "pacchetto misurato" : "pacchetti misurati"}` : ""}
                              </span>
                            )}
                            {!misurato && <span className="vd-sotto vd-a-capo">{m.motivo ?? "il servizio non fornisce questa misura"}</span>}
                          </td>
                          <td data-label="Esito">
                            {raggiunto === null
                              ? <span className="vd-stato vd-stato--neutro">Non misurato</span>
                              : raggiunto
                                ? <span className="vd-stato vd-stato--ok">Raggiunto</span>
                                : <span className="vd-stato vd-stato--critico">Mancato</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="vd-nota" style={{ marginTop: 10, maxWidth: 820 }}>
                Un indicatore senza misura resta «Non misurato»: non viene dato per raggiunto.
                {misureSenzaFonte &&
                  " Le misure mostrate non dichiarano da quale sistema di monitoraggio provengono: non valgono come prova verso un ente."}
              </p>
              {typeof sla.data?.note === "string" && sla.data.note && (
                <p className="vd-nota" style={{ marginTop: 6, maxWidth: 820 }}>Nota del servizio: {sla.data.note}</p>
              )}
            </>
          )}
        </section>

        {/* ── Attività del mese ───────────────────────────────────── */}
        {!sla.isLoading && !sla.isError && (
          <section aria-labelledby="vdc-attivita">
            <div className="vd-sezione-testa">
              <h2 id="vdc-attivita" className="vd-etichetta">Attività del mese</h2>
              <span className="vd-nota">Conteggi dall'archivio, {nomeMese}</span>
            </div>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-coppie" style={{ maxWidth: 820 }}>
                <caption className="vd-sr">Attività del servizio nel mese</caption>
                <tbody>
                  {([
                    ["Pacchetti di archiviazione conservati", metriche.aip_conservati],
                    ["Blocchi della catena delle impronte sigillati", metriche.blocchi_hashchain],
                    ["Enti attivi", metriche.tenant_attivi],
                  ] as [string, unknown][]).map(([nome, v]) => (
                    <tr key={nome}>
                      <th scope="row" className="vd-riga">{nome}</th>
                      <td className="vd-num">{num(v) ?? <span className="vd-stato vd-stato--neutro" style={{ fontStretch: "100%" }}>Non disponibile</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* ── Versamenti per giorno ───────────────────────────────── */}
        <section aria-labelledby="vdc-giorni">
          <div className="vd-sezione-testa">
            <h2 id="vdc-giorni" className="vd-etichetta">Versamenti per giorno</h2>
            <div className="vd-selettore" role="group" aria-label="Periodo">
              {[7, 30, 90].map(g => (
                <button key={g} type="button" aria-pressed={giorni === g}
                        className={giorni === g ? "vd-bottone" : "vd-bottone vd-bottone--secondario"}
                        onClick={() => setGiorni(g)}>
                  {g} giorni
                </button>
              ))}
            </div>
          </div>
          {tp.isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura dei versamenti…</p>
          ) : tp.isError ? (
            <EsitoCritico titolo="Versamenti per giorno non disponibili" errore={tp.error} spiegazione="Non significa che non ci siano stati versamenti." onRiprova={() => tp.refetch()} inRiprova={tp.isFetching} />
          ) : totale === 0 ? (
            <div className="vd-esito">
              <p className="vd-esito-titolo">Nessun pacchetto versato negli ultimi {giorni} giorni</p>
            </div>
          ) : (
            <>
              <p style={{ fontSize: 16, marginBottom: 12 }}>
                <strong style={{ fontStretch: "75%", fontSize: 20 }}>{num(totale)}</strong> pacchetti versati negli ultimi {giorni} giorni,
                in media <strong>{num(totale / giorni, 1)}</strong> al giorno.
              </p>
              <figure style={{ maxWidth: 1040 }}>
                <div style={{ height: 220 }} aria-hidden="true">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={serie} margin={{ top: 4, right: 0, left: 0, bottom: 0 }} barCategoryGap={giorni > 30 ? 1 : 2}>
                      <CartesianGrid vertical={false} stroke="var(--vd-filetto)" />
                      <XAxis dataKey="etichetta" tick={{ fontSize: 13, fill: "var(--vd-testo-2)" }} tickLine={false}
                             axisLine={{ stroke: "var(--vd-testo)", strokeWidth: 2 }} interval="preserveStartEnd" minTickGap={24} />
                      <YAxis tick={{ fontSize: 13, fill: "var(--vd-testo-2)" }} axisLine={false} tickLine={false} width={44} allowDecimals={false} />
                      <Tooltip content={<Suggerimento />} cursor={{ fill: "var(--vd-binario)" }} isAnimationActive={false} />
                      <Bar dataKey="aip" fill="var(--vd-testo)" isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <figcaption className="vd-nota" style={{ marginTop: 6 }}>
                  Pacchetti per data di versamento (UTC). I giorni senza versamenti valgono zero.
                </figcaption>
              </figure>
              <details style={{ marginTop: 8 }}>
                <summary className="vd-comando-testo" style={{ display: "inline-flex", alignItems: "center" }}>Mostra i valori giorno per giorno</summary>
                <div className="vd-scorri" style={{ maxHeight: 360, overflowY: "auto", maxWidth: 420, marginTop: 8 }}>
                  <table className="vd-tabellone">
                    <caption className="vd-sr">Pacchetti versati per giorno</caption>
                    <thead><tr><th scope="col">Giorno</th><th scope="col" style={{ textAlign: "right" }}>Pacchetti</th></tr></thead>
                    <tbody>
                      {[...serie].reverse().map(r => (
                        <tr key={r.data}>
                          <td className="vd-ora">{r.etichetta}</td>
                          <td style={{ textAlign: "right" }}>{num(r.aip)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          )}
        </section>

        {/* ── Enti serviti (solo amministrazione e responsabile) ──── */}
        {isGlobal && (
          <section aria-labelledby="vdc-enti">
            <div className="vd-sezione-testa">
              <h2 id="vdc-enti" className="vd-etichetta">Enti serviti</h2>
              {enti.data && (
                <span className="vd-nota">
                  {num(enti.data.totale_tenant) ?? "—"} enti · {num(enti.data.totale_aip) ?? "—"} pacchetti · {num(enti.data.storage_totale_gb, 1) ?? "—"} GB occupati
                </span>
              )}
            </div>
            {enti.isLoading ? (
              <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura degli enti…</p>
            ) : enti.isError ? (
              <EsitoCritico titolo="Riepilogo degli enti non disponibile" errore={enti.error} onRiprova={() => enti.refetch()} inRiprova={enti.isFetching} />
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 36, alignItems: "flex-start" }}>
                {([
                  ["Per piano", enti.data?.per_piano, (k: string) => k.charAt(0).toUpperCase() + k.slice(1)],
                  ["Per stato", enti.data?.per_stato, (k: string) => STATO_ENTE[k] ?? k],
                ] as [string, Record<string, number> | undefined, (k: string) => string][]).map(([titolo, valori, nome]) => (
                  <div key={titolo} style={{ flex: "1 1 300px", minWidth: 0, maxWidth: 480 }}>
                    <table className="vd-tabellone">
                      <caption style={{ textAlign: "left", fontSize: 17, fontWeight: 800, fontStretch: "85%", paddingBottom: 6 }}>{titolo}</caption>
                      <thead><tr><th scope="col">{titolo === "Per piano" ? "Piano" : "Stato"}</th><th scope="col" style={{ textAlign: "right" }}>Enti</th></tr></thead>
                      <tbody>
                        {Object.entries(valori ?? {}).length === 0 ? (
                          <tr><td colSpan={2} className="vd-nota">Nessun ente</td></tr>
                        ) : Object.entries(valori ?? {}).map(([k, v]) => (
                          <tr key={k}><td>{nome(k)}</td><td className="vd-num">{num(v) ?? "—"}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
