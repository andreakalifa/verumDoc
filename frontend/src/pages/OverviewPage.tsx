// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Quadro del servizio — home del responsabile del servizio.
 * Sistema grafico «Partenze» (VD-DR-01): fascia di stato, tabellone delle
 * scadenze (Partenze) e dei versamenti (Arrivi), situazione dell'archivio.
 * Principio: uno stato sconosciuto si mostra come sconosciuto, mai come «tutto bene».
 * g3-frontend: la fascia legge l'ultima verifica COMPLETA della catena
 * (`integrita.esito`, `verificata_il`, `blocchi_aggiunti_dopo`; regola unica in
 * src/data/integrita.ts, condivisa con la pagina della catena).
 * R12c: le Partenze sono calcolate dai dati reali del servizio (verifica della catena,
 * pratiche che aspettano chi guarda, blocchi WORM); le righe dimostrative compaiono
 * solo in sviluppo con `?dimostrativi=1`, dichiarate in una fascia e in ogni riga.
 * L'ente è quello scelto in testata (`useEnte`), non una seconda scelta locale.
 */
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchOverview, fetchHashchain, fetchVersamenti } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { SCADENZE_DIMOSTRATIVE, ETICHETTA_SCADENZA } from "../data/scadenzeDimostrative";
import { fasciaIntegrita, esitoIntegrita, Fascia } from "../data/integrita";
import { EsitoCritico } from "../components/EsitoCritico";
import { Vuoto } from "../components/Vuoto";
import { SceltaEnte, useEnte } from "../components/SceltaEnte";
import { leggiPratiche, leggiStatoAutomatico, type Pratica } from "../console/api";
import { TIPO_PRATICA, STATO_PRATICA, parola } from "../console/parole";

const num = (n: number | undefined | null) =>
  typeof n === "number" ? n.toLocaleString("it-IT") : "—";

const ora = (iso?: string) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
};

const giorno = (iso: string) => {
  const [a, m, d] = iso.split("-");
  return Number(a) === new Date().getFullYear() ? `${d}/${m}` : `${d}/${m}/${a}`;
};
const traQuanto = (iso: string) => {
  const oggi = new Date(); oggi.setHours(0, 0, 0, 0);
  const g = Math.round((new Date(iso + "T00:00:00").getTime() - oggi.getTime()) / 86_400_000);
  if (g > 730) return `tra ${Math.floor(g / 365)} anni`;
  if (g > 90) return `tra ${Math.round(g / 30)} mesi`;
  return g === 0 ? "oggi" : g === 1 ? "domani" : g > 1 ? `tra ${g} gg` : `scaduta da ${-g} gg`;
};

const ESITO: Record<string, { testo: string; classe: string }> = {
  RICEVUTO:       { testo: "Ricevuto", classe: "vd-stato--neutro" },
  IN_VALIDAZIONE: { testo: "In verifica", classe: "vd-stato--info" },
  IN_BUILD:       { testo: "In conservazione", classe: "vd-stato--info" },
  AIP_IN_BUILD:   { testo: "In conservazione", classe: "vd-stato--info" },
  IN_CATENA:      { testo: "Conservato", classe: "vd-stato--ok" },
  CONSERVATO:     { testo: "Conservato", classe: "vd-stato--ok" },
  ERRORE:         { testo: "Rifiutato", classe: "vd-stato--critico" },
  ANNULLATO:      { testo: "Annullato", classe: "vd-stato--neutro" },
};


/** Una riga del tabellone delle Partenze, calcolata dai dati del servizio. */
interface Partenza {
  chiave: string;
  data: string | null;        // AAAA-MM-GG, null = «adesso»
  sotto: string;
  attivita: string;
  destinazione: string;
  dettaglio: string;
  ente: string;
  stato: { testo: string; classe: string };
  dimostrativa?: boolean;
  /** Testo della colonna Data quando non c'è una data («90 gg»); senza, «Ora». */
  etichetta?: string;
  /** In fondo al tabellone (nessuna scadenza vicina). */
  ultima?: boolean;
}

const isoGiorno = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const giorniDa = (iso: string) => Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 86_400_000));
const dataLunga = (iso: string) => new Date(iso).toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" });

function FasciaStato({ fascia, children }: { fascia: Fascia; children?: React.ReactNode }) {
  return (
    <section aria-labelledby="vd-stato" aria-live="polite" className={`vd-banda vd-banda--${fascia.tono}${fascia.tono === "giallo" ? " vd-su-giallo" : ""}`}>
      <div className="vd-pagina vd-banda-corpo">
        <div className="vd-banda-testo">
          <h1 id="vd-stato">{fascia.titolo}</h1>
          <p>{fascia.riga}</p>
        </div>
        <div className="vd-banda-comandi">{children}</div>
      </div>
    </section>
  );
}

export default function OverviewPage() {
  const { user, hasScope } = useAuth();
  const [params] = useSearchParams();
  const ente = useEnte();
  const { globale, tenantId } = ente;
  const conservatore = globale;
  const ruoloAttesa = globale ? "CONSERVATORE" : user?.ruolo === "ADMIN_TENANT" ? "PRODUTTORE" : null;
  // Righe dimostrative: solo nella versione di sviluppo e solo se chieste esplicitamente
  const dimostrative = import.meta.env.DEV && params.get("dimostrativi") === "1";

  const quadro = useQuery({ queryKey: ["overview", tenantId], queryFn: () => fetchOverview(tenantId), enabled: !!tenantId });
  const catena = useQuery({ queryKey: ["hashchain-stato"], queryFn: () => fetchHashchain(), refetchInterval: 60_000 });
  const arrivi = useQuery({
    queryKey: ["versamenti-arrivi", tenantId],
    queryFn: () => fetchVersamenti(tenantId, 6),
    enabled: !!tenantId,
    refetchInterval: 30_000,
  });
  const attesa = useQuery({
    queryKey: ["pratiche-attesa", ruoloAttesa],
    queryFn: () => leggiPratiche({ in_attesa_di: "me" }, null, 5),
    enabled: !!ruoloAttesa, retry: false,
  });
  const automatico = useQuery({ queryKey: ["stato-automatico"], queryFn: leggiStatoAutomatico, enabled: conservatore, retry: false });

  // ── Fascia: la verità sullo stato dell'archivio, dall'ultima verifica COMPLETA (g3-frontend) ──
  const fascia: Fascia = fasciaIntegrita(catena);

  const t = quadro.data?.tenant ?? {};
  const a = quadro.data?.aip_stats ?? {};
  const perStato: Record<string, number> = a.per_stato ?? {};
  const inLavorazione = Object.entries(perStato)
    .filter(([k]) => !["CONSERVATO", "ERRORE"].includes(k))
    .reduce((s, [, v]) => s + (v as number), 0);
  const spazioPct = typeof t.storage_percentuale === "number" ? t.storage_percentuale : null;

  const elencoEnti: any[] = ente.enti.data?.items ?? [];
  const nomeEnte = (id: string) => elencoEnti.find(e => e.id === id)?.ragione_sociale
    ?? (id === user?.tenant_id ? (user?.ragione_sociale ?? "Il tuo ente") : `Ente ${String(id).slice(0, 8)}`);
  const righeArrivi: any[] = [...(arrivi.data?.items ?? [])]
    .sort((x, y) => String(y.data_ricezione ?? "").localeCompare(String(x.data_ricezione ?? "")))
    .slice(0, 6);

  // ── Partenze calcolate dai dati del servizio ──
  const partenze: Partenza[] = [];
  const esito = esitoIntegrita(catena);
  const integ = catena.data?.integrita;
  if (esito === "integra" && integ?.verificata_il) {
    const prossima = new Date(integ.verificata_il); prossima.setFullYear(prossima.getFullYear() + 5);
    partenze.push({ chiave: "catena", data: isoGiorno(prossima), sotto: traQuanto(isoGiorno(prossima)),
      attivita: "Verifica periodica della catena delle impronte", destinazione: "/hashchain",
      dettaglio: `Almeno ogni cinque anni · ultima completa il ${dataLunga(integ.verificata_il)}`, ente: "Tutti gli enti",
      stato: { testo: "In orario", classe: "vd-stato--ok" } });
  } else if (esito === "mai_verificata" || esito === "nessun_blocco") {
    partenze.push({ chiave: "catena", data: null, sotto: esito === "nessun_blocco" ? "nessun blocco" : "mai eseguita",
      attivita: "Prima verifica completa della catena delle impronte", destinazione: "/hashchain",
      dettaglio: "Finché non è eseguita l'integrità non è dimostrata", ente: "Tutti gli enti",
      stato: { testo: "Da eseguire", classe: "vd-stato--rischio" } });
  } else if (esito === "compromessa") {
    partenze.push({ chiave: "catena", data: null, sotto: "subito",
      attivita: "Indagine sull'integrità della catena", destinazione: "/hashchain",
      dettaglio: "L'ultima verifica completa ha trovato impronte divergenti", ente: "Tutti gli enti",
      stato: { testo: "Compromessa", classe: "vd-stato--critico" } });
  }
  (attesa.data?.items ?? []).forEach((p: Pratica) => {
    const st = parola(STATO_PRATICA, p.stato);
    partenze.push({ chiave: `pratica-${p.id}`, data: null, sotto: `ferma da ${giorniDa(p.aggiornata_il)} gg`,
      attivita: TIPO_PRATICA[p.tipo] ?? p.tipo, destinazione: `/pratiche/${p.id}`,
      dettaglio: `${st.testo} · aperta il ${dataLunga(p.creata_il)}`, ente: nomeEnte(p.ente),
      stato: { testo: "Tocca a te", classe: "vd-stato--rischio" } });
  });
  // Blocchi WORM: la prossima scadenza utile (entro 90 giorni) o la dichiarazione che non ne servono
  const ret = automatico.data?.retention;
  if (ret) {
    // revisione R17 (R17-20): giorno in ora italiana
    const g = ret.prossima_scadenza
      ? new Date(ret.prossima_scadenza).toLocaleDateString("sv-SE", { timeZone: "Europe/Rome" }) : null;
    const entro90 = !!g && (new Date(g + "T00:00:00").getTime() - Date.now()) <= 90 * 86_400_000;
    if (ret.da_rinnovare > 0 || entro90) {
      partenze.push({ chiave: "worm", data: g, sotto: g ? traQuanto(g) : "adesso",
        attivita: "Rinnovo dei blocchi WORM", destinazione: "/configurazione/ciclo-vita",
        dettaglio: ret.da_rinnovare > 0 ? `${num(ret.da_rinnovare)} pacchetti da rinnovare` : "Rinnovo automatico in programma",
        ente: "Servizio",
        stato: ret.da_rinnovare > 0 ? { testo: "Da rinnovare", classe: "vd-stato--rischio" } : { testo: "In orario", classe: "vd-stato--ok" } });
    } else {
      partenze.push({ chiave: "worm", data: null, ultima: true, etichetta: "90 gg", sotto: "nessuna scadenza",
        attivita: "Rinnovo dei blocchi WORM", destinazione: "/configurazione/ciclo-vita",
        dettaglio: `Nessun rinnovo necessario entro 90 giorni · ${num(ret.rinnovi_30_giorni)} rinnovati negli ultimi 30`,
        ente: "Servizio", stato: { testo: "In orario", classe: "vd-stato--ok" } });
    }
  }
  const ordine = (x: Partenza) => (x.ultima ? "9" : x.data ?? "");
  partenze.sort((x, y) => ordine(x).localeCompare(ordine(y)));
  if (dimostrative) {
    SCADENZE_DIMOSTRATIVE.forEach((s, i) => partenze.push({
      chiave: `demo-${i}`, data: s.data, sotto: traQuanto(s.data), attivita: s.attivita, destinazione: s.destinazione,
      dettaglio: s.dettaglio, ente: s.ente, dimostrativa: true,
      stato: { testo: `Esempio: ${ETICHETTA_SCADENZA[s.stato].testo.toLowerCase()}`, classe: "vd-stato--neutro" },
    }));
  }
  const partenzeInLettura = catena.isLoading || (!!ruoloAttesa && attesa.isLoading);

  return (
    <>
      <FasciaStato fascia={fascia}>
        <Link to="/hashchain" className="vd-comando-testo">Dettaglio della verifica</Link>
      </FasciaStato>

      {dimostrative && (
        <p className="vd-pagina vd-avviso-sviluppo" role="note">
          <strong>Dati dimostrativi (solo sviluppo).</strong> Le righe «Esempio» del tabellone Partenze sono inventate; togli <span className="vd-mono">?dimostrativi=1</span> dall'indirizzo per vedere solo i dati del servizio.
        </p>
      )}

      <div className="vd-pagina vd-corpo-pagina vd-pila">
        <div className="vd-affianca vd-affianca--home">
          {/* ── Partenze ─────────────────────────────────────────── */}
          <section aria-labelledby="vd-partenze" className="vd-affianca-principale">
            <div className="vd-sezione-testa">
              <h2 id="vd-partenze" className="vd-etichetta">Partenze</h2>
              <span className="vd-nota">Scadenze e attese del servizio</span>
            </div>
            {partenzeInLettura && partenze.length === 0 ? (
              <p className="vd-nota vd-in-lettura">Lettura delle scadenze…</p>
            ) : partenze.length === 0 ? (
              <Vuoto titolo="Nessuna scadenza calcolabile adesso"
                     azione={<Link to="/pratiche" className="vd-comando-testo">Vai alle pratiche</Link>}>
                Il servizio non ha restituito scadenze: nessuna pratica aspetta te e lo stato della catena non indica verifiche da eseguire.
              </Vuoto>
            ) : (
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Scadenze del servizio{dimostrative ? " (con righe dimostrative)" : ""}</caption>
                  <thead><tr><th scope="col">Data</th><th scope="col">Attività</th><th scope="col">Ente</th><th scope="col">Stato</th></tr></thead>
                  <tbody>
                    {partenze.map(s => (
                      <tr key={s.chiave}>
                        <td className="vd-ora" data-label="Data">{s.data ? giorno(s.data) : s.etichetta ?? "Ora"}<span className="vd-sotto vd-sotto-normale">{s.sotto}</span></td>
                        <td data-label="Attività">
                          <Link to={s.destinazione} className="vd-riga-link" style={{ fontWeight: s.stato.classe === "vd-stato--rischio" || s.stato.classe === "vd-stato--critico" ? 700 : 500 }}>{s.attivita}</Link>
                          <span className="vd-sotto">{s.dettaglio}{s.destinazione.startsWith("/da-realizzare") ? " · funzione da realizzare" : ""}</span>
                        </td>
                        <td data-label="Ente">{s.ente}</td>
                        <td className={`vd-stato ${s.stato.classe}`} data-label="Stato">{s.stato.testo}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="vd-nota vd-dopo-tabellone">
              Calcolate dai dati del servizio{attesa.isError || automatico.isError ? " (alcune fonti non hanno risposto)" : ""}. Il calendario completo, con firma del manuale e rinnovo delle marche, non è ancora fornito: <Link to="/da-realizzare/marche">che cosa manca</Link>.
            </p>
          </section>

          {/* ── Arrivi ───────────────────────────────────────────── */}
          <section aria-labelledby="vd-arrivi" className="vd-affianca-laterale">
            <div className="vd-sezione-testa">
              <h2 id="vd-arrivi" className="vd-etichetta">Arrivi</h2>
              <span className="vd-nota">Ultimi versamenti{t.ragione_sociale ? `, ${t.ragione_sociale}` : ""}</span>
            </div>
            {!tenantId && globale ? (
              <Vuoto titolo="Scegli un ente">
                <span>Gli arrivi e la situazione dell'archivio sono di un ente alla volta.</span>
                <SceltaEnte id="vd-quadro-ente" stato={ente} />
              </Vuoto>
            ) : arrivi.isLoading ? (
              <p className="vd-nota vd-in-lettura">Lettura dei versamenti…</p>
            ) : arrivi.isError ? (
              <EsitoCritico titolo="Arrivi non disponibili" errore={arrivi.error}
                            spiegazione="Non significa che non siano arrivati versamenti."
                            onRiprova={() => arrivi.refetch()} inRiprova={arrivi.isFetching} />
            ) : righeArrivi.length === 0 ? (
              <Vuoto titolo="Nessun versamento ricevuto"
                     azione={hasScope("versamento:ud") ? <Link to="/versamento" className="vd-bottone">Versa un documento</Link> : undefined}>
                I pacchetti compaiono qui appena arrivano, con l'esito della verifica. L'elenco si aggiorna ogni 30 secondi.
              </Vuoto>
            ) : (
              <div className="vd-scorri">
                <table className="vd-tabellone">
                  <caption className="vd-sr">Ultimi versamenti ricevuti</caption>
                  <thead><tr><th scope="col">Ora</th><th scope="col">Pacchetto</th><th scope="col">Esito</th></tr></thead>
                  <tbody>
                    {righeArrivi.map(v => {
                      const e = ESITO[v.stato] ?? { testo: v.stato ?? "Sconosciuto", classe: "vd-stato--neutro" };
                      return (
                        <tr key={v.pdv_id}>
                          <td className="vd-ora">{ora(v.data_ricezione)}</td>
                          {/* revisione R17 (R17-29): l'oggetto in prima riga, l'identificativo sotto */}
                          <td className="vd-a-capo">
                            {v.oggetto ? <span className="vd-forte">{v.oggetto}</span> : <span className="vd-nota">Oggetto non indicato</span>}
                            <span className="vd-sotto vd-mono" title={v.pdv_id}>{String(v.pdv_id ?? "").slice(0, 8)}</span>
                          </td>
                          <td className={`vd-stato ${e.classe}`}>{e.testo}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {tenantId && (
              <p className="vd-dopo-tabellone">
                <Link to="/versamenti" className="vd-comando-testo">Tutti i versamenti</Link>
              </p>
            )}
          </section>
        </div>

        {tenantId && <hr className="vd-binario" aria-hidden="true" />}

        {/* ── Situazione dell'archivio dell'ente ─────────────────── */}
        {tenantId && (
        <section aria-labelledby="vd-situazione">
          <div className="vd-sezione-testa">
            <h2 id="vd-situazione" className="vd-titolo-sezione">
              Situazione dell'archivio{t.ragione_sociale ? `: ${t.ragione_sociale}` : ""}
            </h2>
          </div>
          {quadro.isLoading ? (
            <p className="vd-nota vd-in-lettura">Lettura in corso…</p>
          ) : quadro.isError ? (
            <EsitoCritico titolo="Situazione dell'archivio non disponibile" errore={quadro.error}
                          spiegazione="I conteggi sono sconosciuti, non zero."
                          onRiprova={() => quadro.refetch()} inRiprova={quadro.isFetching} />
          ) : (
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-coppie" style={{ maxWidth: 820 }}>
                <caption className="vd-sr">Situazione dell'archivio dell'ente</caption>
                <tbody>
                  <tr><th scope="row" className="vd-riga">Pacchetti conservati</th>
                      <td className="vd-num">{num(a.conservati)}</td><td>su {num(a.totale)} ricevuti</td></tr>
                  <tr><th scope="row" className="vd-riga">In lavorazione</th>
                      <td className="vd-num">{num(inLavorazione)}</td><td className="vd-nota">verifica, costruzione o iscrizione nella catena</td></tr>
                  <tr><th scope="row" className="vd-riga">In errore</th>
                      <td className="vd-num" style={{ color: perStato.ERRORE ? "var(--vd-critico)" : undefined }}>{num(perStato.ERRORE ?? 0)}</td>
                      <td>{perStato.ERRORE ? <Link to="/versamenti" style={{ fontWeight: 700 }}>Vedi i pacchetti rifiutati</Link> : <span className="vd-nota">nessuno</span>}</td></tr>
                  <tr><th scope="row" className="vd-riga">Spazio occupato</th>
                      <td className="vd-num" style={{ color: spazioPct !== null && spazioPct >= 80 ? "var(--vd-rischio)" : undefined }}>
                        {spazioPct === null ? "—" : `${spazioPct.toLocaleString("it-IT", { maximumFractionDigits: 1 })}%`}
                      </td>
                      <td>{num(t.storage_usato_gb)} GB di {num(t.storage_quota_gb)} GB
                        {spazioPct !== null && spazioPct >= 100 && <strong className="vd-stato vd-stato--critico"> · quota superata</strong>}
                        {spazioPct !== null && spazioPct >= 80 && spazioPct < 100 && <strong className="vd-stato vd-stato--rischio"> · oltre l'80%</strong>}
                      </td></tr>
                </tbody>
              </table>
            </div>
          )}
        </section>
        )}
      </div>
    </>
  );
}
