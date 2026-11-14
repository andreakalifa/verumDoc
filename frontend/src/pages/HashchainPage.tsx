// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Catena delle impronte — sistema grafico «Partenze» (VD-DR-01).
 * La fascia dice l'esito dell'ultima verifica COMPLETA con le stesse cinque
 * verità della home (src/data/integrita.ts): in lettura, non disponibile, mai
 * verificata, integra, compromessa.
 * g4-frontend: la verifica completa è ASINCRONA (g4-chiusura): POST → 202 con
 * `verifica_id` (o 409 con l'id di quella già in corso, che la pagina aggancia),
 * poi GET …/verifica-completa/{id} ogni 5 s (sospeso a scheda nascosta) fino a
 * COMPLETATA / FALLITA / INTERROTTA; alla chiusura si rilegge lo stato della catena.
 * g3-frontend (g2-dati §6.5–6.6):
 *  - «Avvia verifica completa» (solo amministrazione e responsabile del servizio)
 *    chiama POST /dashboard/hashchain/verifica-completa e mostra l'esito; anche
 *    «compromessa» è un esito, non un errore. 409 = un'altra verifica in corso;
 *    502/503 = verifica non eseguita, nessun esito registrato.
 *  - stato di firma per blocco (con il motivo) e conteggi per stato di firma;
 *  - la voce «verifica-catena» di DaRealizzarePage non serve più ed è stata tolta.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { avviaVerificaCompleta, fetchHashchain, fetchStatoVerifica } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { leggiErrore } from "../api/errori";
import { EsitoCritico } from "../components/EsitoCritico";
import { fasciaIntegrita, esitoIntegrita, quandoVerifica, rigaCopertura, firmaBlocco, qualitaFirma, QualitaFirma } from "../data/integrita";

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

const durata = (ms: unknown) => {
  if (typeof ms !== "number") return null;
  if (ms < 1000) return `${ms.toLocaleString("it-IT")} ms`;
  const s = ms / 1000;
  return s < 120 ? `${s.toLocaleString("it-IT", { maximumFractionDigits: 1 })} secondi` : `${Math.round(s / 60)} minuti`;
};

/** Ora «HH:MM» di un istante, o null. */
const oraDi = (iso?: string | null) => quando(iso)?.ora ?? null;

/**
 * Verifica avviata da questa pagina (o agganciata con il 409): avvio, stato
 * interrogato ogni 5 s finché è IN_CORSO, poi l'esito. INTERROTTA e FALLITA sono
 * stati veri: nessun esito sulla catena, l'ultimo esito mostrato resta valido.
 */
function EsitoVerifica({ avvio, stato, agganciata, conteggi }: {
  avvio: ReturnType<typeof useMutation<any, unknown, void>>;
  stato: ReturnType<typeof useQuery<any>> | null;
  agganciata: boolean;
  conteggi?: Record<string, number>;
}) {
  if (avvio.isPending) {
    return (
      <div className="vd-esito vd-esito--info" role="status">
        <p className="vd-esito-titolo">Avvio della verifica completa…</p>
      </div>
    );
  }
  if (avvio.isError && !agganciata) {
    const l = leggiErrore(avvio.error);
    return (
      <EsitoCritico titolo="Verifica non avviata" errore={avvio.error} azione="operazione"
                    spiegazione={l.stato === 409
                      ? "Il servizio dice che un'altra verifica è in corso ma non ne indica l'identificativo: rileggi lo stato tra qualche minuto."
                      : "Nessuna verifica è partita e nessun esito è stato registrato: l'ultimo esito mostrato sopra resta valido."}
                    onRiprova={() => avvio.mutate()} />
    );
  }
  if (!stato) return null;
  const d = stato.data ?? {};
  const avviataIl = d.avviata_il ?? avvio.data?.avviata_il ?? null;
  const inCorso = !stato.data || d.stato === "IN_CORSO";
  if (stato.isError && inCorso) {
    return (
      <EsitoCritico titolo="Stato della verifica non disponibile" errore={stato.error}
                    spiegazione="La verifica può essere ancora in corso nel servizio: non significa che sia fallita."
                    onRiprova={() => stato.refetch()} inRiprova={stato.isFetching} />
    );
  }
  if (inCorso) {
    return (
      <div className="vd-esito vd-esito--info" role="status">
        <p className="vd-esito-titolo">Verifica in corso{oraDi(avviataIl) ? `, avviata alle ${oraDi(avviataIl)}` : ""}</p>
        {agganciata && <p>Era già in corso una verifica completa: la pagina segue quella invece di avviarne un'altra.</p>}
        <p>
          Il servizio ricalcola impronte, collegamenti e radici di Merkle di tutti i blocchi: può durare alcuni minuti.
          Lo stato si rilegge ogni 5 secondi mentre questa scheda è aperta in primo piano.
        </p>
        {d.verifica_id && <p className="vd-nota">Verifica <span className="vd-mono">{String(d.verifica_id).slice(0, 8)}</span></p>}
      </div>
    );
  }
  if (d.stato === "INTERROTTA" || d.stato === "FALLITA") {
    const interrotta = d.stato === "INTERROTTA";
    return (
      <div className={`vd-esito ${interrotta ? "vd-esito--rischio" : "vd-esito--critico"}`} role="alert">
        <p className="vd-esito-titolo">{interrotta ? "Verifica interrotta" : "Verifica fallita"}</p>
        <p>
          {interrotta
            ? "La verifica si è fermata prima della fine (tempo massimo superato o servizio riavviato)."
            : "Durante la verifica il servizio ha avuto un errore."}
          {" "}Nessun esito è stato registrato sulla catena: l'ultimo esito mostrato sopra resta valido. Puoi avviarne un'altra.
        </p>
        {d.messaggio && <p className="vd-nota">Messaggio del servizio: {d.messaggio}</p>}
        <p className="vd-nota">
          {oraDi(avviataIl) ? `Avviata alle ${oraDi(avviataIl)}` : "Ora di avvio non indicata"}
          {quandoVerifica(d.conclusa_il) ? `, chiusa il ${quandoVerifica(d.conclusa_il)}` : ""}
          {d.verifica_id ? ` · verifica ${String(d.verifica_id).slice(0, 8)}` : ""}
        </p>
      </div>
    );
  }
  const tono = d.esito === "INTEGRA" ? "ok" : d.esito === "COMPROMESSA" ? "critico" : "info";
  const titolo = d.esito === "INTEGRA" ? "Verifica completata: catena integra"
    : d.esito === "COMPROMESSA" ? "Verifica completata: integrità compromessa"
    : d.esito === "NESSUN_BLOCCO" ? "Verifica completata: nessun blocco da verificare"
    : `Verifica completata: esito «${d.esito ?? "non indicato"}»`;
  const controlli: string[] = Array.isArray(d.controlli) ? d.controlli : [];
  return (
    <div className={`vd-esito vd-esito--${tono}`} role="status">
      <p className="vd-esito-titolo">{titolo}</p>
      {d.messaggio && <p>{d.messaggio}</p>}
      <dl className="vd-dati">
        <dt>Blocchi verificati</dt><dd>{num(d.blocchi_verificati) ?? "non indicato"}{typeof d.ultimo_blocco_verificato === "number" ? `, fino al n. ${num(d.ultimo_blocco_verificato)}` : ""}</dd>
        <dt>Avviata e conclusa</dt><dd>{oraDi(avviataIl) ? `avviata alle ${oraDi(avviataIl)}, ` : ""}conclusa il {quandoVerifica(d.conclusa_il) ?? "(data non indicata)"}{durata(d.durata_ms) ? `, in ${durata(d.durata_ms)}` : ""}</dd>
        {typeof d.blocchi_senza_firma_qualificata === "number" && (
          <><dt>Blocchi senza firma qualificata</dt><dd>{num(d.blocchi_senza_firma_qualificata)}{dettaglioQualita(qualitaFirma(conteggi, d.blocchi_senza_firma_qualificata))}</dd></>
        )}
        {controlli.length > 0 && (
          <><dt>Controlli eseguiti</dt><dd><ul style={{ paddingLeft: 18 }}>{controlli.map(c => <li key={c}>{c}</li>)}</ul></dd></>
        )}
      </dl>
      <p className="vd-nota">L'esito è registrato nel registro delle operazioni{d.verifica_id ? ` (verifica ${String(d.verifica_id).slice(0, 8)})` : ""}.</p>
    </div>
  );
}

/** «: 4.790 firmati e marcati con firma non qualificata e 27 non ancora firmati» */
function dettaglioQualita(qf: QualitaFirma): string {
  if (qf.firmatiNonQualificati === null) return "";
  const parti = [
    qf.firmatiNonQualificati > 0 ? `${num(qf.firmatiNonQualificati)} firmati e marcati con firma non qualificata` : "",
    qf.daFirmare + qf.nonFirmati > 0 ? `${num(qf.daFirmare + qf.nonFirmati)} non ancora firmati` : "",
  ].filter(Boolean);
  return parti.length ? `: ${parti.join(" e ")}` : "";
}

export default function HashchainPage() {
  const { user } = useAuth();
  const puòVerificare = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  const qc = useQueryClient();
  const catena = useQuery({ queryKey: ["hashchain", 10], queryFn: () => fetchHashchain(10), staleTime: 60_000 });
  // Verifica seguita da questa pagina: avviata qui (202) o agganciata (409 con l'id di quella in corso)
  const [verificaId, setVerificaId] = useState<string | null>(null);
  const [agganciata, setAgganciata] = useState(false);
  const avvio = useMutation<any, unknown, void>({
    mutationFn: () => avviaVerificaCompleta(),
    onMutate: () => { setVerificaId(null); setAgganciata(false); },
    onSuccess: (d: any) => { if (d?.verifica_id) setVerificaId(String(d.verifica_id)); },
    onError: (e: any) => {
      const dati = e?.response?.data;
      // problem+json (VD-C-100): l'id è in `dettagli`; forma di R02: in `detail`
      const id = e?.response?.status === 409
        ? (dati?.dettagli?.verifica_id ?? dati?.detail?.verifica_id ?? dati?.verifica_id) : null;
      if (id) { setVerificaId(String(id)); setAgganciata(true); }
    },
  });
  const statoVerifica = useQuery({
    queryKey: ["verifica-completa", verificaId],
    queryFn: () => fetchStatoVerifica(verificaId as string),
    enabled: !!verificaId,
    // ogni 5 s finché è in corso; react-query sospende l'intervallo con la scheda nascosta
    refetchInterval: q => (!q.state.data || q.state.data?.stato === "IN_CORSO" ? 5_000 : false),
    refetchIntervalInBackground: false,
    retry: 1,
  });
  const statoFinale = statoVerifica.data?.stato && statoVerifica.data.stato !== "IN_CORSO" ? statoVerifica.data.stato : null;

  // Alla chiusura della verifica la fascia, la home e i blocchi rileggono lo stato della catena;
  // con un esito (COMPLETATA) la fascia lo segue subito, prima ancora della rilettura.
  const chiusa = useRef<string | null>(null);
  useEffect(() => {
    const d = statoVerifica.data;
    if (!verificaId || !statoFinale || chiusa.current === verificaId) return;
    chiusa.current = verificaId;
    if (statoFinale === "COMPLETATA" && d?.esito && d?.conclusa_il) {
      const integrita = {
        integra: d.integra ?? (d.esito === "INTEGRA" ? true : d.esito === "COMPROMESSA" ? false : null),
        esito: d.esito, messaggio: d.messaggio ?? null, verificata_il: d.conclusa_il, verifica_id: d.verifica_id ?? verificaId,
        blocchi_verificati: d.blocchi_verificati ?? null, ultimo_blocco_verificato: d.ultimo_blocco_verificato ?? null,
        blocchi_aggiunti_dopo: 0, blocchi_senza_firma_qualificata: d.blocchi_senza_firma_qualificata ?? null, avviata_da: d.avviata_da ?? null,
      };
      const aggiorna = (vecchi: any) => (vecchi ? { ...vecchi, integrita } : vecchi);
      qc.setQueriesData({ queryKey: ["hashchain"] }, aggiorna);
      qc.setQueriesData({ queryKey: ["hashchain-stato"] }, aggiorna);
    }
    void qc.invalidateQueries({ queryKey: ["hashchain"] });
    void qc.invalidateQueries({ queryKey: ["hashchain-stato"] });
  }, [verificaId, statoFinale, statoVerifica.data, qc]);
  const verificaInCorso = avvio.isPending || (!!verificaId && !statoFinale);

  const fascia = fasciaIntegrita(catena);
  const esito = esitoIntegrita(catena);
  const i = catena.data?.integrita ?? {};
  const firma = catena.data?.firma ?? {};
  const conteggi: Record<string, number> = firma.conteggi ?? {};
  const blocchi: any[] = catena.data?.blocchi_recenti ?? [];
  const totale = num(catena.data?.totale_blocchi);
  const nonFirmati = typeof firma.blocchi_da_firmare_o_non_firmati === "number" ? firma.blocchi_da_firmare_o_non_firmati : null;
  const senzaQualificata = typeof i.blocchi_senza_firma_qualificata === "number" ? i.blocchi_senza_firma_qualificata : null;
  const provaDimostrata = nonFirmati === 0 && (firma.blocchi_firmati ?? 0) > 0 && senzaQualificata === 0;
  const qf = qualitaFirma(conteggi, senzaQualificata);
  // Con tutti i firmati a firma non qualificata, lo si dice anche blocco per blocco
  const tuttiNonQualificati = qf.firmati > 0 && qf.firmatiNonQualificati === qf.firmati;
  const soloIniziale = typeof catena.data?.totale_blocchi === "number" && catena.data.totale_blocchi <= 1;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Catena delle impronte</h1>
        <p>Ogni blocco sigilla le impronte di un gruppo di pacchetti di archiviazione e quella del blocco precedente: se un pacchetto cambia, la catena non torna più.</p>
      </div>

      <section aria-labelledby="vd-esito-catena" aria-live="polite" className={`vd-fascia vd-fascia--${fascia.tono}${fascia.tono === "giallo" ? " vd-su-giallo" : ""}`}>
        <h2 id="vd-esito-catena">{fascia.titolo}</h2>
        <span className="vd-fascia-riga">{fascia.riga}</span>
        <div className="vd-fascia-comandi">
          {puòVerificare && (
            <button type="button" className="vd-bottone" onClick={() => avvio.mutate()} disabled={verificaInCorso}>
              {verificaInCorso ? "Verifica in corso…" : "Avvia verifica completa"}
            </button>
          )}
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => catena.refetch()} disabled={catena.isFetching}>
            {catena.isFetching ? "Lettura in corso…" : "Rileggi lo stato"}
          </button>
        </div>
      </section>

      <div style={{ display: "flex", flexDirection: "column", gap: 40 }}>
        {(avvio.isPending || avvio.isError || !!verificaId) && (
          <section aria-label="Verifica completa avviata" aria-live="polite">
            <EsitoVerifica avvio={avvio} stato={verificaId ? statoVerifica : null} agganciata={agganciata} conteggi={conteggi} />
          </section>
        )}

        <section aria-labelledby="vd-catena-dati">
          <h2 id="vd-catena-dati" className="vd-sr">Dati della catena</h2>
          <dl className="vd-dati">
            <dt>Blocchi nella catena</dt>
            <dd>
              {catena.isLoading ? <span className="vd-nota">Lettura…</span>
                : catena.isError || totale === null ? <span className="vd-stato vd-stato--neutro">Non disponibile</span>
                : soloIniziale ? <span className="vd-stato vd-stato--neutro">Solo il blocco iniziale</span>
                : <><span className="vd-numero">{totale}</span>{typeof catena.data?.ultimo_blocco === "number" && <span className="vd-nota"> · ultimo n. {num(catena.data.ultimo_blocco)}</span>}</>}
            </dd>
            <dt>Ultima verifica completa</dt>
            <dd>
              {catena.isLoading ? <span className="vd-nota">Lettura…</span>
                : catena.isError ? <span className="vd-stato vd-stato--neutro">Non disponibile</span>
                : esito === "mai_verificata" ? (
                  <><span className="vd-stato vd-stato--neutro">Mai verificata</span>
                    <span className="vd-nota" style={{ display: "block" }}>
                      {puòVerificare ? "Avvia la prima verifica completa con il comando qui sopra." : "La verifica completa la avvia l'amministrazione o il responsabile del servizio."}
                    </span></>
                ) : (
                  <><span className={`vd-stato ${esito === "integra" ? "vd-stato--ok" : esito === "compromessa" ? "vd-stato--critico" : "vd-stato--neutro"}`}>
                      {esito === "integra" ? "Integra" : esito === "compromessa" ? "Compromessa" : "Nessun blocco"}
                    </span>
                    <span className="vd-nota" style={{ display: "block" }}>{rigaCopertura(i) || quandoVerifica(i.verificata_il)}</span></>
                )}
            </dd>
            <dt>Firma dei blocchi</dt>
            <dd>
              {catena.isLoading ? <span className="vd-nota">Lettura…</span>
                : catena.isError || !firma.conteggi ? <span className="vd-stato vd-stato--neutro">Non disponibile</span>
                : (
                  <>
                    <span className={`vd-stato ${tuttiNonQualificati ? "vd-stato--rischio" : "vd-stato--ok"}`}>
                      {num(conteggi.FIRMATO ?? 0)} firmati e marcati{qf.etichetta ? ` · ${qf.etichetta}` : ""}
                    </span>{" · "}
                    <span className="vd-stato vd-stato--info">{num(conteggi.DA_FIRMARE ?? 0)} in attesa</span>{" · "}
                    <span className={`vd-stato ${(conteggi.NON_FIRMATO ?? 0) > 0 ? "vd-stato--rischio" : "vd-stato--neutro"}`}>{num(conteggi.NON_FIRMATO ?? 0)} non firmati</span>
                    <span className="vd-nota" style={{ display: "block" }}>
                      Il blocco iniziale non si conta.
                      {qf.firmatiNonQualificati === null && qf.firmati > 0 ? " La qualità della firma (qualificata o no) la dice la verifica completa, non ancora eseguita." : ""}
                    </span>
                  </>
                )}
            </dd>
            <dt>Valore di prova verso terzi</dt>
            <dd>{provaDimostrata ? (
              <><span className="vd-stato vd-stato--ok">Tutti i blocchi firmati e marcati</span>
                <span className="vd-nota" style={{ display: "block" }}>La firma si controlla blocco per blocco.</span></>
            ) : (
              <><span className="vd-stato vd-stato--rischio">Non dimostrato</span>
                <span className="vd-nota" style={{ display: "block" }}>
                  {(() => {
                    const parti = [
                      nonFirmati !== null && nonFirmati > 0 ? `${num(nonFirmati)} blocchi non hanno ancora firma e marca` : "",
                      qf.firmatiNonQualificati ? `${num(qf.firmatiNonQualificati)} sono firmati e marcati con una firma non qualificata` : "",
                    ].filter(Boolean);
                    return parti.length
                      ? `${parti.join(" e ")}: la catena dimostra la coerenza interna dell'archivio, non è una prova opponibile.`
                      : "Il servizio non riporta firma qualificata e marca temporale dei blocchi: la catena dimostra la coerenza interna dell'archivio, non è ancora una prova opponibile.";
                  })()}
                </span></>
            )}</dd>
          </dl>
        </section>

        <section aria-labelledby="vd-ultimi-blocchi">
          <div className="vd-sezione-testa">
            <h2 id="vd-ultimi-blocchi" className="vd-etichetta">Ultimi blocchi</h2>
            {blocchi.length > 0 && <span className="vd-nota">I più recenti per primi</span>}
          </div>
          {catena.isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura dei blocchi…</p>
          ) : catena.isError ? (
            <EsitoCritico titolo="Ultimi blocchi non disponibili" errore={catena.error}
                          spiegazione="Non significa che la catena sia vuota."
                          onRiprova={() => catena.refetch()} inRiprova={catena.isFetching} />
          ) : blocchi.length === 0 ? (
            <p className="vd-nota" style={{ paddingBlock: 12 }}>
              {soloIniziale ? "Nella catena c'è solo il blocco iniziale" : "Nessun blocco da mostrare"}: i blocchi successivi si formano quando i pacchetti di archiviazione vengono sigillati dopo il versamento.
            </p>
          ) : (
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Ultimi blocchi della catena delle impronte, con lo stato di firma</caption>
                <thead><tr><th scope="col">Sigillato</th><th scope="col">Blocco</th><th scope="col">Impronta del blocco</th><th scope="col">Pacchetti sigillati</th><th scope="col">Firma</th></tr></thead>
                <tbody>
                  {blocchi.map((b, k) => {
                    const q = quando(b.created_at);
                    const f = firmaBlocco(b.stato_firma);
                    const marca = quando(b.data_marca);
                    return (
                      <tr key={b.numero ?? k}>
                        <td className="vd-ora" data-label="Sigillato">{q ? q.ora : "—"}<span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>{q ? q.giorno : "data non disponibile"}</span></td>
                        <td style={{ fontWeight: 700 }} data-label="Blocco">n. {num(b.numero) ?? "—"}{b.numero === 0 && <span className="vd-sotto" style={{ fontWeight: 400 }}>blocco iniziale</span>}</td>
                        <td data-label="Impronta del blocco"><span className="vd-mono vd-a-capo" title={b.hash_blocco ?? b.hash}>{b.hash ?? "—"}</span></td>
                        <td data-label="Pacchetti sigillati">{typeof b.num_aip === "number" ? `${num(b.num_aip)} ${b.num_aip === 1 ? "pacchetto" : "pacchetti"}` : "—"}</td>
                        <td style={{ whiteSpace: "normal", minWidth: 0 }} data-label="Firma">
                          <span className={`vd-stato ${b.stato_firma === "FIRMATO" && tuttiNonQualificati ? "vd-stato--rischio" : f.classe}`}>{f.testo}</span>
                          {b.stato_firma === "FIRMATO" && tuttiNonQualificati && <span className="vd-sotto">firma non qualificata</span>}
                          {marca && <span className="vd-sotto">marca del {marca.giorno}</span>}
                          {b.motivo_firma && <span className="vd-sotto vd-a-capo">{b.motivo_firma}</span>}
                        </td>
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
