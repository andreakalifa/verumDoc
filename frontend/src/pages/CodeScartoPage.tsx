// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Code di scarto — sistema grafico «Partenze» (VD-DR-01), R04 servizi (VD-C-075).
 * I messaggi che un servizio non è riuscito a elaborare dopo i ritentativi (DLQ Kafka
 * e indicizzazione in errore, eventi dell'outbox bloccati), letti dalla tabella `scarti`
 * di dashboard-api. Per un evento dell'outbox bloccato «Rielabora» lo ritenta e
 * «Archivia» lo SCARTA: non sarà mai pubblicato e i successivi della stessa chiave ripartono.
 * «Rielabora» segue anteprima e conferma con l'impronta del piano (REGOLE §13):
 * il dialogo mostra cosa si farà, cosa è bloccato e perché; il motivo è obbligatorio.
 * «Archivia» è una conferma in riga con motivo. Solo SUPER_ADMIN e RSC_CONSERVATORE.
 * Paginazione a cursore (niente totale).
 */
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  anteprimaRielabora, applicaRielabora, archiviaScarto, fetchCodeScarto, fetchScarti, fetchScarto,
  type FiltriScarti, type SelezioneScarti,
} from "../api/client";
import { leggiErrore, motivoErrore } from "../api/errori";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";

const LIMITE = 30;
const MOTIVO_MIN = 10;

/** Nome delle code in parole del mestiere. */
const CODE: Record<string, string> = {
  "pdv.received.dlq": "Verifica dei versamenti",
  "pdv.validated.dlq": "Costruzione dei pacchetti di archiviazione",
  "notification.dlq": "Notifiche e eventi per i clienti",
  indicizzazione_aip: "Indicizzazione per la ricerca",
  outbox_chiavi_ferme: "Eventi in uscita bloccati",
};
const SERVIZI: Record<string, string> = {
  validation: "servizio di verifica",
  "aip-builder": "servizio di conservazione",
  notification: "servizio delle notifiche",
  distribution: "servizio di distribuzione",
  gateway: "servizio di versamento",
  hashchain: "registro delle impronte",
  onboarding: "servizio degli enti",
  "dashboard-api": "console del conservatore",
};
const STATI: Record<string, { testo: string; classe: string }> = {
  DA_GESTIRE: { testo: "Da gestire", classe: "vd-stato--rischio" },
  RIELABORATO: { testo: "Rielaborato", classe: "vd-stato--ok" },
  ARCHIVIATO: { testo: "Archiviato", classe: "vd-stato--neutro" },
};
const FILTRI_STATO: [string, string][] = [
  ["DA_GESTIRE", "Da gestire"], ["RIELABORATO", "Rielaborati"], ["ARCHIVIATO", "Archiviati"], ["", "Tutti"],
];

const nomeCoda = (c?: string | null) => (c && CODE[c]) || c || "coda sconosciuta";
const quando = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return {
    ora: d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }),
    giorno: d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" }),
  };
};
const quandoBreve = (iso?: string | null) => { const q = quando(iso); return q ? `${q.giorno} alle ${q.ora}` : null; };
const breve = (id?: string | null) => (id ? String(id).slice(0, 8) : null);
const plurale = (n: number, uno: string, molti: string) => `${n.toLocaleString("it-IT")} ${n === 1 ? uno : molti}`;

// ── Dialogo: piano di rielaborazione (anteprima → conferma) ────────
function DialogoPiano({ selezione, descrizione, onClose }: {
  selezione: SelezioneScarti; descrizione: string; onClose: (fatto: boolean) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const qc = useQueryClient();
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const [pianoAggiornato, setPianoAggiornato] = useState<any | null>(null);
  const [fatto, setFatto] = useState<any | null>(null);

  useEffect(() => { const d = ref.current; if (d && !d.open) d.showModal(); }, []);

  const anteprima = useQuery({
    queryKey: ["scarti-piano", selezione],
    queryFn: () => anteprimaRielabora(selezione),
    staleTime: Infinity, retry: false,
  });
  const piano = pianoAggiornato ?? anteprima.data;

  const applica = useMutation({
    mutationFn: () => applicaRielabora(selezione, piano.impronta_piano, motivo.trim()),
    onSuccess: (dati) => {
      setFatto(dati);
      qc.invalidateQueries({ queryKey: ["scarti"] });
      qc.invalidateQueries({ queryKey: ["scarti-code"] });
    },
    onError: (e: any) => {
      // 409 DSH-409-PIANO: il servizio restituisce il piano ricalcolato da confermare
      const nuovo = e?.response?.data?.dettagli?.piano;
      if (leggiErrore(e).codice === "DSH-409-PIANO" && nuovo) setPianoAggiornato(nuovo);
    },
  });
  const pianoCambiato = leggiErrore(applica.error).codice === "DSH-409-PIANO";
  const motivoCorto = motivo.trim().length < MOTIVO_MIN;
  const chiudi = () => ref.current?.close();
  const elementi: any[] = Array.isArray(piano?.elementi) ? piano.elementi : [];
  const bloccati = elementi.filter(e => !e.rielaborabile);

  return (
    <dialog ref={ref} className="vd-dialogo" aria-labelledby="vd-piano-titolo"
            onClose={() => onClose(!!fatto)}
            onClick={e => { if (e.target === ref.current) chiudi(); }}>
      <div className="vd-dialogo-testa">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 id="vd-piano-titolo" className="vd-dialogo-titolo">Rielabora</h2>
          <p className="vd-nota" style={{ marginTop: 4 }}>{descrizione}</p>
        </div>
        <button type="button" className="vd-bottone vd-bottone--secondario" onClick={chiudi}>Chiudi</button>
      </div>

      <div className="vd-dialogo-corpo">
        {anteprima.isLoading ? (
          <p className="vd-nota" role="status">Calcolo del piano…</p>
        ) : anteprima.isError ? (
          <EsitoCritico titolo="Piano non disponibile" errore={anteprima.error}
                        spiegazione="Nulla è stato rielaborato."
                        onRiprova={() => anteprima.refetch()} inRiprova={anteprima.isFetching} />
        ) : fatto ? (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">{plurale(fatto.rielaborati, "messaggio rielaborato", "messaggi rielaborati")}</p>
            <p>
              I messaggi sono di nuovo in lavorazione presso i servizi che li avevano scartati.
              {fatto.bloccati > 0 && ` ${plurale(fatto.bloccati, "messaggio è rimasto", "messaggi sono rimasti")} da gestire.`}
            </p>
            <div><button type="button" className="vd-bottone vd-bottone--secondario" onClick={chiudi} autoFocus>Chiudi</button></div>
          </div>
        ) : piano ? (
          <>
            {pianoCambiato && (
              <div className="vd-esito vd-esito--rischio" role="alert">
                <p className="vd-esito-titolo">Il piano è cambiato</p>
                <p>Nel frattempo qualcosa è cambiato (nuovi messaggi, o messaggi già gestiti da altri). Nulla è stato rielaborato: controlla il piano aggiornato e conferma di nuovo.</p>
              </div>
            )}
            <section aria-labelledby="vd-piano-sintesi">
              <h3 id="vd-piano-sintesi">Che cosa succederà</h3>
              <dl className="vd-dati">
                <dt>Da rielaborare</dt><dd className="vd-numero">{piano.rielaborabili.toLocaleString("it-IT")}</dd>
                <dt>Bloccati</dt><dd className="vd-numero">{piano.bloccati.toLocaleString("it-IT")}</dd>
                <dt>Chi sarà avvisato</dt><dd>{piano.chi_sara_avvisato}</dd>
              </dl>
              {(piano.avvisi ?? []).map((a: string) => <p key={a} className="vd-avviso" style={{ marginTop: 10 }}>{a}</p>)}
            </section>

            <section aria-labelledby="vd-piano-elementi">
              <h3 id="vd-piano-elementi">Messaggi</h3>
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Esito del piano per ogni messaggio</caption>
                  <thead><tr><th scope="col">Messaggio</th><th scope="col">Esito</th></tr></thead>
                  <tbody>
                    {elementi.map(e => (
                      <tr key={e.scarto_id}>
                        <td data-label="Messaggio">
                          <span className="vd-mono">n. {e.scarto_id}</span>
                          <span className="vd-sotto">{nomeCoda(e.coda)}</span>
                          {e.pdv_id && <span className="vd-sotto">versamento <span className="vd-mono" title={e.pdv_id}>{breve(e.pdv_id)}</span></span>}
                        </td>
                        <td data-label="Esito" style={{ whiteSpace: "normal" }}>
                          {e.rielaborabile
                            ? <span className="vd-stato vd-stato--ok">Si rielabora</span>
                            : <span className="vd-stato vd-stato--critico">Bloccato</span>}
                          <span className="vd-sotto vd-a-capo">{e.rielaborabile ? e.azione : e.motivo_blocco}</span>
                          {(e.avvisi ?? []).map((a: string) => (
                            <span key={a} className="vd-sotto vd-a-capo vd-stato--rischio">{a}</span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {bloccati.length > 0 && piano.rielaborabili > 0 && (
                <p className="vd-nota" style={{ marginTop: 8 }}>I messaggi bloccati restano da gestire: si possono archiviare con un motivo.</p>
              )}
            </section>

            {piano.rielaborabili > 0 ? (
              <section aria-labelledby="vd-piano-conferma" className="vd-modulo">
                <h3 id="vd-piano-conferma">Conferma</h3>
                <div className="vd-campo">
                  <label htmlFor="vd-piano-motivo">
                    Motivo della rielaborazione<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span>
                  </label>
                  <textarea id="vd-piano-motivo" className="vd-input vd-textarea" value={motivo} maxLength={1000}
                            aria-required="true" aria-invalid={toccato && motivoCorto ? true : undefined}
                            aria-describedby="vd-piano-motivo-aiuto"
                            onChange={e => setMotivo(e.target.value)} onBlur={() => setToccato(true)} />
                  <span id="vd-piano-motivo-aiuto" className="vd-aiuto">
                    Che cosa è stato corretto (almeno {MOTIVO_MIN} caratteri): resta nel registro delle operazioni.
                  </span>
                  {toccato && motivoCorto && (
                    <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>
                      Scrivi un motivo di almeno {MOTIVO_MIN} caratteri.
                    </span>
                  )}
                </div>
                {applica.isError && !pianoCambiato && (
                  <EsitoCritico titolo="Rielaborazione non eseguita" errore={applica.error} azione="operazione"
                                spiegazione="Nulla è stato rielaborato." />
                )}
                <div className="vd-comandi">
                  <button type="button" className="vd-bottone" disabled={applica.isPending || motivoCorto}
                          onClick={() => { setToccato(true); if (!motivoCorto) applica.mutate(); }}>
                    {applica.isPending ? "Rielaborazione in corso…"
                      : `Conferma e rielabora ${plurale(piano.rielaborabili, "messaggio", "messaggi")}`}
                  </button>
                  <button type="button" className="vd-bottone vd-bottone--secondario" onClick={chiudi} disabled={applica.isPending}>
                    Annulla
                  </button>
                </div>
              </section>
            ) : (
              <div className="vd-esito vd-esito--rischio">
                <p className="vd-esito-titolo">Nessun messaggio si può rielaborare</p>
                <p>Ogni messaggio selezionato è bloccato per il motivo indicato. Archiviali con un motivo se non serve altro.</p>
              </div>
            )}
          </>
        ) : null}
      </div>
    </dialog>
  );
}

// ── Dialogo: dettaglio di un messaggio ─────────────────────────────
function DialogoDettaglio({ id, onClose, onRielabora }: { id: number; onClose: () => void; onRielabora: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const d = ref.current; if (d && !d.open) d.showModal(); }, []);
  const dettaglio = useQuery({ queryKey: ["scarti", "dettaglio", id], queryFn: () => fetchScarto(id) });
  const s = dettaglio.data;
  const riga = (etichetta: string, valore: React.ReactNode, mono = false) => (
    <><dt>{etichetta}</dt><dd className={mono && valore ? "vd-mono vd-a-capo" : "vd-a-capo"}>{valore || <span className="vd-nota">non indicato</span>}</dd></>
  );
  const stato = s ? STATI[s.stato] ?? { testo: s.stato, classe: "vd-stato--neutro" } : null;
  return (
    <dialog ref={ref} className="vd-dialogo" aria-labelledby="vd-scarto-titolo" onClose={onClose}
            onClick={e => { if (e.target === ref.current) ref.current?.close(); }}>
      <div className="vd-dialogo-testa">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 id="vd-scarto-titolo" className="vd-dialogo-titolo">Messaggio n. {id}</h2>
          {s && <p className="vd-nota" style={{ marginTop: 4 }}>{nomeCoda(s.coda)}</p>}
          {stato && <p className={`vd-stato ${stato.classe}`} style={{ marginTop: 6 }}>{stato.testo}</p>}
        </div>
        <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => ref.current?.close()}>Chiudi</button>
      </div>
      <div className="vd-dialogo-corpo">
        {dettaglio.isLoading ? (
          <p className="vd-nota" role="status">Lettura del messaggio…</p>
        ) : dettaglio.isError ? (
          <EsitoCritico titolo="Messaggio non disponibile" errore={dettaglio.error}
                        onRiprova={() => dettaglio.refetch()} inRiprova={dettaglio.isFetching} />
        ) : s ? (
          <>
            <section aria-labelledby="vd-sc-perche">
              <h3 id="vd-sc-perche">Perché è stato scartato</h3>
              <dl className="vd-dati">
                {riga("Motivo", s.motivo)}
                {riga("Codice", s.codice, true)}
                {riga("Tentativi", typeof s.tentativi === "number" ? s.tentativi.toLocaleString("it-IT") : null)}
                {riga("Scartato il", quandoBreve(s.scartato_il))}
                {riga("Servizio", SERVIZI[s.servizio] ?? s.servizio)}
              </dl>
            </section>
            <section aria-labelledby="vd-sc-rielabora">
              <h3 id="vd-sc-rielabora">Rielaborazione</h3>
              {s.valutazione?.rielaborabile ? (
                <p style={{ marginTop: 10 }}><strong className="vd-stato vd-stato--ok">Si può rielaborare</strong>: {s.valutazione.azione}.</p>
              ) : (
                <p style={{ marginTop: 10 }}><strong className="vd-stato vd-stato--critico">Non si può rielaborare</strong>: {s.valutazione?.motivo_blocco ?? "motivo non indicato"}.</p>
              )}
              {(s.valutazione?.avvisi ?? []).map((a: string) => <p key={a} className="vd-avviso" style={{ marginTop: 10 }}>{a}</p>)}
              {s.azioni_consentite?.includes("rielabora") && (
                <div className="vd-comandi" style={{ marginTop: 14 }}>
                  <button type="button" className="vd-bottone vd-bottone--secondario" onClick={onRielabora}>Rielabora…</button>
                </div>
              )}
            </section>
            <section aria-labelledby="vd-sc-origine">
              <h3 id="vd-sc-origine">Origine</h3>
              <dl className="vd-dati">
                {riga("Coda", s.coda, true)}
                {riga("Topic d'origine", s.topic_origine, true)}
                {riga("Posizione", s.messaggio?.partizione != null ? `partizione ${s.messaggio.partizione}, offset ${s.messaggio.posizione}` : null)}
                {riga("Ente", s.ente ?? s.tenant_id)}
                {riga("Versamento (PdV)", s.pdv_id, true)}
                {riga("Pacchetto (PdA)", s.aip_id, true)}
                {riga("Registrato il", quandoBreve(s.registrato_il))}
              </dl>
            </section>
            <section aria-labelledby="vd-sc-messaggio">
              <h3 id="vd-sc-messaggio">Messaggio</h3>
              <p className="vd-nota" style={{ marginTop: 10 }}>Solo i dati tecnici: il contenuto dei documenti e dei metadati non si mostra.</p>
              <dl className="vd-dati" style={{ marginTop: 6 }}>
                {riga("Leggibile", s.messaggio?.leggibile ? "sì" : "no: formato non riconosciuto")}
                {riga("Dimensione", s.messaggio ? `${s.messaggio.dimensione_byte.toLocaleString("it-IT")} byte` : null)}
                {riga("Campi", (s.messaggio?.campi ?? []).join(", "), true)}
                {riga("Campi dell'evento", (s.messaggio?.campi_evento ?? []).join(", "), true)}
              </dl>
            </section>
            {s.stato !== "DA_GESTIRE" && (
              <section aria-labelledby="vd-sc-gestione">
                <h3 id="vd-sc-gestione">Gestione</h3>
                <dl className="vd-dati">
                  {riga("Gestito il", quandoBreve(s.gestito_il))}
                  {riga("Da", s.gestito_da, true)}
                  {riga("Motivo", s.motivo_gestione)}
                  {riga("Rielaborazioni", s.rielaborazioni?.toLocaleString("it-IT"))}
                </dl>
              </section>
            )}
          </>
        ) : null}
      </div>
    </dialog>
  );
}

// ── Archivia: conferma in riga con motivo ──────────────────────────
function ArchiviaInRiga({ id, coda, onFine }: { id: number; coda?: string | null; onFine: () => void }) {
  const qc = useQueryClient();
  const [motivo, setMotivo] = useState("");
  const archivia = useMutation({
    mutationFn: () => archiviaScarto(id, motivo.trim()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scarti"] });
      qc.invalidateQueries({ queryKey: ["scarti-code"] });
      onFine();
    },
  });
  const corto = motivo.trim().length < MOTIVO_MIN;
  return (
    <div className="vd-conferma-riga" role="group" aria-label={`Archivia il messaggio ${id}`}>
      {coda === "outbox_chiavi_ferme" && (
        <p className="vd-avviso" style={{ margin: 0 }}>
          L'evento sarà scartato e non verrà mai pubblicato; gli eventi successivi della stessa risorsa ripartono.
        </p>
      )}
      <label htmlFor={`vd-archivia-${id}`} className="vd-nota">Perché non si rielabora? (almeno {MOTIVO_MIN} caratteri)</label>
      <textarea id={`vd-archivia-${id}`} className="vd-input vd-textarea" value={motivo} maxLength={1000}
                aria-required="true" onChange={e => setMotivo(e.target.value)} autoFocus />
      {archivia.isError && (
        <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>
          {motivoErrore(archivia.error, "operazione")}
        </span>
      )}
      <div className="vd-comandi-cella">
        <button type="button" className="vd-bottone vd-bottone--secondario" disabled={corto || archivia.isPending}
                onClick={() => archivia.mutate()}>
          {archivia.isPending ? "Archiviazione…" : "Archivia"}
        </button>
        <button type="button" className="vd-comando-testo" onClick={onFine} disabled={archivia.isPending}>Annulla</button>
      </div>
    </div>
  );
}

// ── Pagina ─────────────────────────────────────────────────────────
export default function CodeScartoPage() {
  // R07 (D15): il collegamento «Rielabora» dell'avviso di scadenza porta ?pdv_id=
  const [params] = useSearchParams();
  const [stato, setStato] = useState(params.get("pdv_id") ? "" : "DA_GESTIRE");
  const [coda, setCoda] = useState("");
  const [ente, setEnte] = useState("");
  const [pdv, setPdv] = useState(params.get("pdv_id") ?? "");
  const cursori = useCursori();
  const [scelti, setScelti] = useState<number[]>([]);
  const [piano, setPiano] = useState<{ selezione: SelezioneScarti; descrizione: string } | null>(null);
  const [dettaglio, setDettaglio] = useState<number | null>(null);
  const [archiviando, setArchiviando] = useState<number | null>(null);

  const filtri: FiltriScarti = { stato, coda, tenant_id: ente.trim(), pdv_id: pdv.trim() };
  const cambiaFiltro = (f: () => void) => { f(); cursori.ricomincia(); setScelti([]); };

  const code = useQuery({ queryKey: ["scarti-code"], queryFn: fetchCodeScarto, refetchInterval: 60_000 });
  const elenco = useQuery({
    queryKey: ["scarti", filtri, cursori.cursore],
    queryFn: () => fetchScarti(filtri, cursori.cursore, LIMITE),
    refetchInterval: 60_000,
  });
  const righe: any[] = elenco.data?.items ?? [];
  const prossimo = prossimoDi(elenco.data);
  const gestibili = righe.filter(r => r.azioni_consentite?.includes("rielabora")).map(r => r.id as number);
  const tuttiScelti = gestibili.length > 0 && gestibili.every(id => scelti.includes(id));
  const filtrato = !!(coda || ente.trim() || pdv.trim());
  const letto = elenco.dataUpdatedAt ? new Date(elenco.dataUpdatedAt).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : null;
  const elencoCode: any[] = code.data?.code ?? [];
  const daGestire = elencoCode.reduce((t, c) => t + (c.da_gestire ?? 0), 0);

  const apriPiano = (selezione: SelezioneScarti, descrizione: string) => { setDettaglio(null); setPiano({ selezione, descrizione }); };
  const scegli = (id: number, si: boolean) => setScelti(s => si ? [...new Set([...s, id])] : s.filter(x => x !== id));

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Messaggi non elaborati</h1>
        <p>Messaggi che un servizio non è riuscito a elaborare dopo i ritentativi. Rielaborali dopo aver tolto la causa, oppure archiviali con un motivo.</p>
        <div className="vd-testa-comandi">
          <button type="button" className="vd-bottone vd-bottone--secondario"
                  onClick={() => { code.refetch(); elenco.refetch(); }} disabled={elenco.isFetching}>
            {elenco.isFetching && !elenco.isLoading ? "Aggiornamento…" : "Aggiorna"}
          </button>
        </div>
      </div>

      <div className="vd-pila">
        <section aria-labelledby="vd-code">
          <div className="vd-sezione-testa">
            <h2 id="vd-code" className="vd-etichetta">Code</h2>
            {code.isSuccess && (
              <span className="vd-nota" role="status">
                {daGestire === 0 ? "Nessun messaggio da gestire" : `${plurale(daGestire, "messaggio", "messaggi")} da gestire`}
              </span>
            )}
          </div>
          {code.isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura delle code…</p>
          ) : code.isError ? (
            <EsitoCritico titolo="Messaggi non elaborati non disponibili" errore={code.error}
                          spiegazione="Non significa che le code siano vuote."
                          onRiprova={() => code.refetch()} inRiprova={code.isFetching} />
          ) : (
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-coppie">
                <caption className="vd-sr">Code dei messaggi non elaborati lette dal servizio, con i messaggi da gestire</caption>
                <thead><tr><th scope="col">Coda</th><th scope="col">Chi scarta</th><th scope="col" className="vd-num">Da gestire</th></tr></thead>
                <tbody>
                  {elencoCode.map(c => (
                    <tr key={c.coda}>
                      <th scope="row" className="vd-riga">
                        <button type="button" className="vd-link-bottone" aria-pressed={coda === c.coda}
                                onClick={() => cambiaFiltro(() => { setCoda(coda === c.coda ? "" : c.coda); setStato("DA_GESTIRE"); })}>
                          {nomeCoda(c.coda)}
                        </button>
                        <span className="vd-sotto vd-mono">{c.coda}</span>
                      </th>
                      <td data-label="Chi scarta">{SERVIZI[c.servizio] ?? c.servizio}</td>
                      <td className="vd-num" data-label="Da gestire">
                        <span className={c.da_gestire > 0 ? "vd-stato--rischio" : "vd-stato--ok"}>
                          {c.da_gestire >= c.tetto ? `oltre ${c.tetto.toLocaleString("it-IT")}` : c.da_gestire.toLocaleString("it-IT")}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section aria-labelledby="vd-messaggi">
          <div className="vd-sezione-testa">
            <h2 id="vd-messaggi" className="vd-etichetta">Messaggi</h2>
            {letto && !elenco.isError && <span className="vd-nota">letto alle {letto}, si aggiorna ogni minuto</span>}
          </div>

          <div className="vd-filtri">
            <div className="vd-campo">
              <label htmlFor="vd-sc-stato">Stato</label>
              <select id="vd-sc-stato" className="vd-input" value={stato} onChange={e => cambiaFiltro(() => setStato(e.target.value))}>
                {FILTRI_STATO.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="vd-campo">
              <label htmlFor="vd-sc-coda">Coda</label>
              <select id="vd-sc-coda" className="vd-input" value={coda} onChange={e => cambiaFiltro(() => setCoda(e.target.value))}>
                <option value="">Tutte</option>
                {Object.keys(CODE).map(c => <option key={c} value={c}>{CODE[c]}</option>)}
              </select>
            </div>
            <div className="vd-campo">
              <label htmlFor="vd-sc-ente">Identificativo dell'ente (vuoto: tutti)</label>
              <input id="vd-sc-ente" className="vd-input vd-mono" value={ente} spellCheck={false}
                     onChange={e => cambiaFiltro(() => setEnte(e.target.value))} />
            </div>
            <div className="vd-campo">
              <label htmlFor="vd-sc-pdv">Identificativo del versamento</label>
              <input id="vd-sc-pdv" className="vd-input vd-mono" value={pdv} spellCheck={false}
                     onChange={e => cambiaFiltro(() => setPdv(e.target.value))} />
            </div>
          </div>

          {stato === "DA_GESTIRE" && righe.length > 0 && (
            <div className="vd-comandi vd-comandi-elenco">
              <button type="button" className="vd-bottone vd-bottone--secondario" disabled={scelti.length === 0}
                      onClick={() => apriPiano({ ids: scelti }, `${plurale(scelti.length, "messaggio selezionato", "messaggi selezionati")}`)}>
                Rielabora i selezionati{scelti.length ? ` (${scelti.length})` : ""}
              </button>
              <button type="button" className="vd-comando-testo"
                      onClick={() => apriPiano({ filtri: { coda, tenant_id: ente.trim(), pdv_id: pdv.trim() } },
                                               filtrato ? "Tutti i messaggi da gestire con questi filtri" : "Tutti i messaggi da gestire")}>
                Rielabora tutti i messaggi da gestire{filtrato ? " con questi filtri" : ""}
              </button>
            </div>
          )}

          {elenco.isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura dei messaggi…</p>
          ) : elenco.isError ? (
            <EsitoCritico titolo="Messaggi non disponibili" errore={elenco.error}
                          spiegazione="Non significa che le code siano vuote."
                          onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
          ) : righe.length === 0 ? (
            <div className="vd-esito vd-esito--ok">
              <p className="vd-esito-titolo">
                {filtrato || stato !== "DA_GESTIRE" ? "Nessun messaggio con questi filtri" : "Nessun messaggio da gestire"}
              </p>
              <p>
                {stato === "DA_GESTIRE" && !filtrato
                  ? "Ogni messaggio scartato dai servizi è stato rielaborato o archiviato."
                  : "Cambia o togli i filtri per vedere altri messaggi."}
              </p>
              {(filtrato || stato !== "DA_GESTIRE") && (
                <p><button type="button" className="vd-comando-testo"
                           onClick={() => cambiaFiltro(() => { setStato("DA_GESTIRE"); setCoda(""); setEnte(""); setPdv(""); })}>
                  Togli i filtri
                </button></p>
              )}
            </div>
          ) : (
            <>
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila" aria-busy={elenco.isFetching}>
                  <caption className="vd-sr">Messaggi delle code di scarto, dal più recente</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="vd-cella-scelta">
                        {gestibili.length > 0 && (
                          <input type="checkbox" aria-label="Seleziona tutti i messaggi da gestire di questa pagina"
                                 checked={tuttiScelti}
                                 onChange={e => setScelti(e.target.checked ? gestibili : [])} />
                        )}
                      </th>
                      <th scope="col">Ora</th><th scope="col">Coda</th><th scope="col">Motivo</th>
                      <th scope="col">Versamento ed ente</th><th scope="col">Stato</th><th scope="col">Comandi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {righe.map(r => {
                      const q = quando(r.scartato_il);
                      const st = STATI[r.stato] ?? { testo: r.stato, classe: "vd-stato--neutro" };
                      const gestibile = r.azioni_consentite?.includes("rielabora");
                      return (
                        <tr key={r.id}>
                          <td className="vd-cella-scelta">
                            {gestibile && (
                              <input type="checkbox" aria-label={`Seleziona il messaggio ${r.id}`}
                                     checked={scelti.includes(r.id)} onChange={e => scegli(r.id, e.target.checked)} />
                            )}
                          </td>
                          <td className="vd-ora" data-label="Ora">
                            {q ? q.ora : "—"}
                            <span className="vd-sotto vd-sotto-normale">{q ? q.giorno : "data non registrata"}</span>
                          </td>
                          <td data-label="Coda">
                            <span className="vd-forte">{nomeCoda(r.coda)}</span>
                            <span className="vd-sotto">{SERVIZI[r.servizio] ?? r.servizio}</span>
                          </td>
                          <td className="vd-a-capo vd-cella-larga" data-label="Motivo">
                            {r.motivo}
                            <span className="vd-sotto">
                              {[r.codice && `codice ${r.codice}`, r.tentativi ? plurale(r.tentativi, "tentativo", "tentativi") : null].filter(Boolean).join(" · ")}
                            </span>
                          </td>
                          <td data-label="Versamento ed ente">
                            {r.pdv_id ? <span className="vd-mono" title={r.pdv_id}>{breve(r.pdv_id)}</span>
                              : r.aip_id ? <span>pacchetto <span className="vd-mono" title={r.aip_id}>{breve(r.aip_id)}</span></span>
                              : <span className="vd-nota">nessuno</span>}
                            <span className="vd-sotto vd-a-capo">{r.ente ?? (r.tenant_id ? `ente ${breve(r.tenant_id)}` : "ente non indicato")}</span>
                          </td>
                          <td data-label="Stato" style={{ whiteSpace: "normal" }}>
                            <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
                            {r.gestito_il && <span className="vd-sotto">il {quandoBreve(r.gestito_il)}</span>}
                            {r.motivo_gestione && <span className="vd-sotto vd-a-capo">{r.motivo_gestione}</span>}
                          </td>
                          <td>
                            {archiviando === r.id ? (
                              <ArchiviaInRiga id={r.id} coda={r.coda} onFine={() => setArchiviando(null)} />
                            ) : (
                              <div className="vd-comandi-cella">
                                <button type="button" className="vd-comando-testo" aria-haspopup="dialog"
                                        onClick={() => setDettaglio(r.id)}>Dettaglio</button>
                                {gestibile && (
                                  <>
                                    <button type="button" className="vd-comando-testo" aria-haspopup="dialog"
                                            onClick={() => apriPiano({ ids: [r.id] }, `Messaggio n. ${r.id} — ${nomeCoda(r.coda)}`)}>
                                      Rielabora
                                    </button>
                                    <button type="button" className="vd-comando-testo" onClick={() => setArchiviando(r.id)}>Archivia</button>
                                  </>
                                )}
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Paginazione cursori={cursori} prossimo={prossimo} righe={righe.length} limite={LIMITE}
                           nome="Messaggi" etichetta="Pagine dei messaggi scartati" />
            </>
          )}
        </section>
      </div>

      {dettaglio !== null && (
        <DialogoDettaglio id={dettaglio} onClose={() => setDettaglio(null)}
                          onRielabora={() => apriPiano({ ids: [dettaglio] }, `Messaggio n. ${dettaglio}`)} />
      )}
      {piano && (
        <DialogoPiano selezione={piano.selezione} descrizione={piano.descrizione}
                      onClose={fatto => { setPiano(null); if (fatto) setScelti([]); }} />
      )}
    </>
  );
}
