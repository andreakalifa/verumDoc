// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Blocchi — sistema grafico «Partenze» (VD-DR-01).
 * Ricerca di un pacchetto di archiviazione e ultimi blocchi della catena.
 * VD-C-029: la ricerca legge la scheda del pacchetto e NON dimostra l'iscrizione
 * nella catena; la pagina non scrive mai «verificato» su un semplice lookup.
 * g3-frontend: stato della catena dall'ultima verifica completa e stato di firma
 * di ogni blocco (g2-dati §6.5).
 */
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchHashchain, api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { esitoIntegrita, quandoVerifica, firmaBlocco } from "../data/integrita";
import { EsitoCritico } from "../components/EsitoCritico";

const num = (n: unknown) => (typeof n === "number" ? n.toLocaleString("it-IT") : null);

const quando = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return {
    ora: d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }),
    giorno: d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" }),
  };
};

const STATO_PACCHETTO: Record<string, { testo: string; classe: string }> = {
  IN_BUILD:   { testo: "In costruzione", classe: "vd-stato--info" },
  FIRMATO:    { testo: "Firmato", classe: "vd-stato--info" },
  SU_STORAGE: { testo: "Salvato, non ancora nella catena", classe: "vd-stato--info" },
  IN_CATENA:  { testo: "Iscritto nella catena", classe: "vd-stato--ok" },
  CONSERVATO: { testo: "Conservato", classe: "vd-stato--ok" },
  ERRORE:     { testo: "In errore", classe: "vd-stato--critico" },
};

/** Stato della catena in una parola, dall'ultima verifica completa (come la fascia della home). */
function statoCatena(q: { isLoading: boolean; isError: boolean; data?: any }) {
  switch (esitoIntegrita(q)) {
    case "lettura": return { testo: "lettura in corso", classe: "vd-stato--neutro" };
    case "non_disponibile": return { testo: "non disponibile", classe: "vd-stato--critico" };
    case "integra": return { testo: "integra all'ultima verifica completa", classe: "vd-stato--ok" };
    case "compromessa": return { testo: "compromessa", classe: "vd-stato--critico" };
    case "nessun_blocco": return { testo: "nessun blocco da verificare", classe: "vd-stato--neutro" };
    default: return { testo: "mai verificata", classe: "vd-stato--neutro" };
  }
}

type Esito =
  | { tipo: "trovato"; dati: any }
  | { tipo: "non-trovato"; id: string }
  | { tipo: "altro-ente"; id: string }
  | { tipo: "errore"; id: string; err: unknown };

const FORMATO_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function BlockchainExplorerPage() {
  const { user } = useAuth();
  const vedeVerifica = ["SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE"].includes(user?.ruolo ?? "");
  const catena = useQuery({ queryKey: ["hashchain", 10], queryFn: () => fetchHashchain(10) });
  const [searchAip, setSearchAip] = useState("");
  const [esito, setEsito] = useState<Esito | null>(null);
  const [searching, setSearching] = useState(false);
  const [formatoErrato, setFormatoErrato] = useState(false);

  const cercaPacchetto = (e: FormEvent) => {
    e.preventDefault();
    const id = searchAip.trim();
    if (!id) return;
    if (!FORMATO_UUID.test(id)) { setFormatoErrato(true); setEsito(null); return; }
    setFormatoErrato(false);
    void esegui(id);
  };
  const esegui = async (id: string) => {
    setSearching(true);
    setEsito(null);
    try {
      const resp = await api.get(`/v2/distribution/aip/${id}`);
      setEsito({ tipo: "trovato", dati: resp.data });
    } catch (err: any) {
      const codice: number | undefined = err?.response?.status;
      setEsito(codice === 404 ? { tipo: "non-trovato", id }
        : codice === 403 ? { tipo: "altro-ente", id }
        : { tipo: "errore", id, err });
    } finally {
      setSearching(false);
    }
  };

  const blocchi: any[] = catena.data?.blocchi_recenti ?? [];
  const sc = statoCatena(catena);
  const ultimo = blocchi[0]?.numero;
  const soloIniziale = typeof catena.data?.totale_blocchi === "number" && catena.data.totale_blocchi <= 1;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Blocchi</h1>
        <p>Cerca un pacchetto di archiviazione e consulta gli ultimi blocchi della catena delle impronte.</p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 40 }}>
        <section aria-labelledby="vd-catena-sintesi">
          <h2 id="vd-catena-sintesi" className="vd-sr">Sintesi della catena</h2>
          <dl className="vd-dati">
            <dt>Stato della catena</dt>
            <dd>
              <span className={`vd-stato ${sc.classe}`}>{sc.testo.charAt(0).toUpperCase() + sc.testo.slice(1)}</span>
              {quandoVerifica(catena.data?.integrita?.verificata_il) && <span className="vd-nota"> · {quandoVerifica(catena.data.integrita.verificata_il)}</span>}
              {vedeVerifica && <> · <Link to="/hashchain" style={{ fontWeight: 700 }}>Dettaglio della verifica</Link></>}
            </dd>
            <dt>Blocchi nella catena</dt>
            <dd>{catena.isLoading ? <span className="vd-nota">Lettura…</span>
              : soloIniziale && !catena.isError ? <span className="vd-stato vd-stato--neutro">Solo il blocco iniziale</span>
              : num(catena.data?.totale_blocchi) !== null && !catena.isError ? <span className="vd-numero">{num(catena.data.totale_blocchi)}</span>
              : <span className="vd-stato vd-stato--neutro">Non disponibile</span>}</dd>
            <dt>Ultimo blocco</dt>
            <dd>{catena.isLoading ? <span className="vd-nota">Lettura…</span>
              : typeof ultimo === "number" ? <span className="vd-numero">n. {num(ultimo)}</span>
              : <span className="vd-stato vd-stato--neutro">{catena.isError ? "Non disponibile" : soloIniziale ? "Il blocco iniziale (n. 0)" : "Non indicato dal servizio"}</span>}</dd>
          </dl>
        </section>

        {/* ── Ricerca di un pacchetto ─────────────────────────────── */}
        <section aria-labelledby="vd-cerca-pacchetto" style={{ maxWidth: 820 }}>
          <div className="vd-sezione-testa">
            <h2 id="vd-cerca-pacchetto" className="vd-etichetta">Cerca un pacchetto</h2>
          </div>
          <form onSubmit={cercaPacchetto} noValidate style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: "12px 16px" }}>
            <div className="vd-campo" style={{ flex: "1 1 320px" }}>
              <label htmlFor="vd-aip-id">Identificativo del pacchetto di archiviazione</label>
              <input id="vd-aip-id" className="vd-input vd-mono" style={{ fontSize: 15 }} value={searchAip}
                     onChange={e => { setSearchAip(e.target.value); setFormatoErrato(false); }}
                     aria-describedby="vd-aip-id-aiuto" aria-invalid={formatoErrato ? "true" : undefined}
                     autoComplete="off" spellCheck={false} />
            </div>
            <button type="submit" className="vd-bottone" disabled={searching || !searchAip.trim()}>
              {searching ? "Ricerca in corso…" : "Cerca"}
            </button>
            <span id="vd-aip-id-aiuto" className="vd-aiuto vd-nota" style={{ flexBasis: "100%", fontSize: 13 }}>
              Il codice di 36 caratteri del pacchetto (UUID), per esempio dalla scheda in «Pacchetti conservati».
            </span>
          </form>

          <div aria-live="polite" style={{ marginTop: 16 }}>
            {formatoErrato && (
              <p className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                L'identificativo non ha il formato di un UUID (8-4-4-4-12 caratteri esadecimali).
              </p>
            )}
            {esito?.tipo === "trovato" && (() => {
              const d = esito.dati;
              const st = STATO_PACCHETTO[d.stato] ?? { testo: d.stato ?? "non disponibile", classe: "vd-stato--neutro" };
              const v = quando(d.data_versamento);
              return (
                <div className="vd-esito vd-esito--info">
                  <p className="vd-esito-titolo">Pacchetto trovato</p>
                  <p style={{ fontSize: 15, maxWidth: "72ch" }}>
                    Questa è la scheda del pacchetto. L'iscrizione nella catena <strong>non è stata verificata</strong>:
                    la ricerca non ricalcola la prova di inclusione nel blocco.
                  </p>
                  <dl className="vd-dati" style={{ marginTop: 4 }}>
                    <dt>Segnatura</dt><dd><span className="vd-mono">{d.urn ?? "non disponibile"}</span></dd>
                    <dt>Stato del pacchetto</dt><dd><span className={`vd-stato ${st.classe}`}>{st.testo}</span></dd>
                    <dt>Impronta SHA-256</dt><dd><span className="vd-mono">{d.hash_sha256 ?? "non disponibile"}</span></dd>
                    <dt>Versato il</dt><dd>{v ? `${v.giorno} alle ${v.ora}` : "non disponibile"}</dd>
                    <dt>Esibibile</dt><dd>{d.distribuibile === true ? "Sì" : d.distribuibile === false ? "No, il pacchetto non è ancora conservato" : "non disponibile"}</dd>
                  </dl>
                  <p className="vd-nota">Verifica dell'inclusione nella catena: funzione da realizzare.</p>
                </div>
              );
            })()}
            {esito?.tipo === "non-trovato" && (
              <div className="vd-esito vd-esito--critico">
                <p className="vd-esito-titolo">Nessun pacchetto con questo identificativo</p>
                <p className="vd-mono vd-a-capo">{esito.id}</p>
                <p className="vd-nota">Controlla l'identificativo nella scheda del pacchetto e riprova.</p>
              </div>
            )}
            {esito?.tipo === "altro-ente" && (
              <div className="vd-esito vd-esito--critico">
                <p className="vd-esito-titolo">Pacchetto di un altro ente</p>
                <p style={{ fontSize: 15 }}>Il tuo profilo non può consultare questo pacchetto.</p>
              </div>
            )}
            {esito?.tipo === "errore" && (
              <EsitoCritico titolo="Ricerca del pacchetto non disponibile" errore={esito.err}
                            spiegazione="Non sappiamo se il pacchetto esiste: la ricerca non è stata eseguita."
                            onRiprova={() => void esegui(esito.id)} inRiprova={searching} />
            )}
          </div>
        </section>

        {/* ── Ultimi blocchi ──────────────────────────────────────── */}
        <section aria-labelledby="vd-blocchi">
          <div className="vd-sezione-testa">
            <h2 id="vd-blocchi" className="vd-etichetta">Ultimi blocchi</h2>
            {blocchi.length > 0 && <span className="vd-nota">I più recenti per primi</span>}
          </div>
          {catena.isLoading ? (
            <p className="vd-nota" style={{ paddingBlock: 12 }}>Lettura dei blocchi…</p>
          ) : catena.isError ? (
            <EsitoCritico titolo="Ultimi blocchi non disponibili" errore={catena.error}
                          spiegazione="Non significa che la catena sia vuota."
                          onRiprova={() => catena.refetch()} inRiprova={catena.isFetching} />
          ) : blocchi.length === 0 ? (
            <p className="vd-nota" style={{ paddingBlock: 12 }}>
              {soloIniziale ? "Nella catena c'è solo il blocco iniziale" : "Nessun blocco da mostrare"}: i blocchi successivi si formano quando i pacchetti versati vengono sigillati.
            </p>
          ) : (
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Ultimi blocchi della catena delle impronte</caption>
                <thead><tr><th scope="col">Sigillato</th><th scope="col">Blocco</th><th scope="col">Impronta del blocco</th><th scope="col">Radice di Merkle</th><th scope="col">Pacchetti</th><th scope="col">Firma</th></tr></thead>
                <tbody>
                  {blocchi.map((b, i) => {
                    const q = quando(b.created_at);
                    return (
                      <tr key={b.numero ?? i}>
                        <td className="vd-ora" data-label="Sigillato">{q ? q.ora : "—"}<span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>{q ? q.giorno : "data non disponibile"}</span></td>
                        <td style={{ fontWeight: 700 }} data-label="Blocco">n. {num(b.numero) ?? "—"}</td>
                        <td data-label="Impronta del blocco"><span className="vd-mono vd-a-capo" title={b.hash}>{b.hash ?? "—"}</span></td>
                        <td data-label="Radice di Merkle"><span className="vd-mono" title={b.merkle_root ?? undefined}>{b.merkle_root ? `${String(b.merkle_root).slice(0, 16)}…` : "—"}</span></td>
                        <td data-label="Pacchetti">{typeof b.num_aip === "number" ? num(b.num_aip) : "—"}</td>
                        <td className={`vd-stato ${firmaBlocco(b.stato_firma).classe}`} style={{ whiteSpace: "normal" }} data-label="Firma">{firmaBlocco(b.stato_firma).testo}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
