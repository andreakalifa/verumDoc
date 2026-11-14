// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Nuova pratica del ciclo di vita (contratto Q2). Sistema «Partenze», tappe numerate
 * come il versamento.
 *
 * - Scarto, riversamento in uscita, restituzione: pratiche MASSIVE con anteprima e
 *   applica (REGOLE §13). La prima richiesta senza impronta è un'anteprima (nessuna
 *   scrittura): pacchetti selezionabili, bloccati per motivo (retention non scaduta,
 *   legal hold, fascicolo aperto…), esempi. La conferma ripete la richiesta con
 *   `impronta_piano`; 409 LC-409-PIANO-CAMBIATO porta il piano nuovo da confermare.
 * - Rettifica e annullamento: puntuali, con riepilogo da confermare e
 *   `Idempotency-Key` (un nuovo invio dopo un errore di rete non crea una seconda pratica).
 * - Riversamento in ingresso: dal PdD già caricato con una sessione di caricamento.
 * I tipi proposti dipendono dal ruolo (services/ciclo-vita/app/domain/permessi.py);
 * il servizio ricontrolla comunque.
 */
import { Fragment, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { useConfermaDecisione } from "../components/ConfermaDecisione";
import { ConfermaAnnullata } from "../console/conferme";
import { leggiErrore } from "../api/errori";
import {
  annullamento, èPiano, elencoClassi, leggiClassiEnte, propostaScarto, restituzione, rettifica,
  riversamentoIngresso, riversamentoUscita, type Criteri, type Pratica,
} from "../console/api";
import { CampoMotivo, giorno, motivoValido, num, plurale, useChiaveIdempotenza } from "../console/comuni";
import { MOTIVO_BLOCCO, TIPO_PRATICA } from "../console/parole";

type Tipo = "SCARTO" | "RIVERSAMENTO_USCITA" | "RESTITUZIONE" | "RETTIFICA" | "ANNULLAMENTO" | "RIVERSAMENTO_INGRESSO";
const SPIEGA: Record<Tipo, string> = {
  SCARTO: "Elimina i pacchetti a fine conservazione, con autorizzazione del produttore e, per gli enti pubblici, il nulla osta della Soprintendenza.",
  RIVERSAMENTO_USCITA: "Prepara i pacchetti di distribuzione completi per passarli a un altro conservatore.",
  RESTITUZIONE: "A fine contratto: tutti i pacchetti dell'ente più registri, prove e audit.",
  RETTIFICA: "Corregge i metadati con un nuovo versamento collegato all'originale, che resta immutato.",
  ANNULLAMENTO: "Annulla logicamente un versamento: nulla si cancella prima dello scarto.",
  RIVERSAMENTO_INGRESSO: "Acquisisce il pacchetto di distribuzione di un altro conservatore, già caricato.",
};
const AVVIO_MASSIVO = ["RSC_CONSERVATORE", "ADMIN_TENANT"];
const PUNTUALI = ["ADMIN_TENANT", "OPERATORE"];
// Il riversamento in ingresso versa i documenti a nome di chi lo avvia: solo
// l'amministratore dell'ente (stessa regola del servizio, app/domain/permessi.py)
const AVVIO_INGRESSO = ["ADMIN_TENANT"];

function Tappa({ n, titolo, nota, children }: { n: number; titolo: string; nota?: string; children: React.ReactNode }) {
  return (
    <fieldset className="vd-tappa">
      <legend><span className="vd-tappa-n" aria-hidden="true">{n}</span><span className="vd-tappa-titolo">{titolo}</span>{nota && <span className="vd-nota">{nota}</span>}</legend>
      {children}
    </fieldset>
  );
}

type PianoQualsiasi = Record<string, any>;

/** Anteprima di una pratica massiva (forme dello scarto e del riversamento). */
function VistaPiano({ piano, tipo }: { piano: PianoQualsiasi; tipo: Tipo }) {
  const scarto = tipo === "SCARTO";
  const selezionabili: number = scarto ? piano.selezionabili : piano.conteggi?.SELEZIONATO ?? 0;
  const perMotivo: [string, number][] = scarto ? Object.entries(piano.bloccati ?? {}) : [];
  const bloccati: number = scarto ? perMotivo.reduce((t, [, n]) => t + n, 0) : piano.conteggi?.BLOCCATO ?? 0;
  const esempi: any[] = scarto ? [...(piano.esempi?.selezionabili ?? []).map((e: any) => ({ ...e, esito: "SELEZIONATO" })), ...(piano.esempi?.bloccati ?? []).map((e: any) => ({ ...e, esito: "BLOCCATO" }))] : piano.esempi ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <p style={{ fontSize: 17, maxWidth: "72ch" }}>
        {selezionabili > 0
          ? <>La pratica comprenderebbe <strong>{plurale(selezionabili, "pacchetto", "pacchetti")}</strong>{bloccati > 0 ? <>; {plurale(bloccati, "resta fuori", "restano fuori")} per i motivi qui sotto</> : null}.</>
          : <>Nessun pacchetto si può includere con questi criteri{bloccati > 0 ? ": sono tutti bloccati" : ""}.</>}
      </p>
      <dl className="vd-dati">
        <dt>Pacchetti esaminati</dt><dd><span className="vd-numero">{num(piano.totale)}</span></dd>
        <dt>Inclusi</dt><dd><span className="vd-numero vd-stato--ok">{num(selezionabili)}</span></dd>
        <dt>Bloccati</dt><dd><span className={`vd-numero${bloccati ? " vd-stato--critico" : ""}`}>{num(bloccati)}</span></dd>
        {piano.richiede_nulla_osta != null && <><dt>Nulla osta</dt><dd>{piano.richiede_nulla_osta ? "Serve il nulla osta della Soprintendenza (ente pubblico)" : "Non serve (ente privato)"}</dd></>}
      </dl>
      {perMotivo.length > 0 && (
        <div className="vd-scorri">
          <table className="vd-tabellone vd-impila vd-coppie">
            <caption style={{ textAlign: "left", fontWeight: 700, paddingBottom: 6 }}>Perché alcuni pacchetti restano fuori</caption>
            <thead><tr><th scope="col">Motivo</th><th scope="col" className="vd-num">Pacchetti</th></tr></thead>
            <tbody>
              {perMotivo.map(([m, n]) => (
                <tr key={m}><th scope="row" className="vd-riga">{MOTIVO_BLOCCO[m] ?? m}<span className="vd-sotto vd-mono">{m}</span></th><td className="vd-num" data-label="Pacchetti">{num(n)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {esempi.length > 0 && (
        <div className="vd-scorri">
          <table className="vd-tabellone vd-impila vd-impila--pieno">
            <caption style={{ textAlign: "left", fontWeight: 700, paddingBottom: 6 }}>Esempi (al più {piano.esempi?.limite_esempi ?? piano.limite_esempi ?? esempi.length} per gruppo)</caption>
            <thead><tr><th scope="col">Pacchetto</th><th scope="col">Esito</th></tr></thead>
            <tbody>
              {esempi.map((e, k) => (
                <tr key={`${e.aip_id}-${k}`}>
                  <td data-label="Pacchetto" className="vd-a-capo"><span className="vd-mono">{e.urn ?? e.aip_id}</span>{e.scadenza && <span className="vd-sotto">termine scaduto il {giorno(e.scadenza)}</span>}</td>
                  <td data-label="Esito" style={{ whiteSpace: "normal" }}>
                    {e.esito === "BLOCCATO"
                      ? <><span className="vd-stato vd-stato--critico">Bloccato</span><span className="vd-sotto vd-a-capo">{e.descrizione ?? MOTIVO_BLOCCO[e.motivo] ?? e.motivo}</span></>
                      : <span className="vd-stato vd-stato--ok">Incluso</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Massiva({ tipo }: { tipo: "SCARTO" | "RIVERSAMENTO_USCITA" | "RESTITUZIONE" }) {
  const navigate = useNavigate();
  const ente = useEnte();
  const [classi, setClassi] = useState<string[]>([]);
  const [criterio, setCriterio] = useState<"anni" | "scadenza">("scadenza");
  const [anni, setAnni] = useState("");
  const [scadenza, setScadenza] = useState("");
  const [destinatario, setDestinatario] = useState("");
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const [piano, setPiano] = useState<PianoQualsiasi | null>(null);
  const [chiave, nuovaChiave] = useChiaveIdempotenza();
  const elencoCl = useQuery({ queryKey: ["conf-classi", ente.tenantId], queryFn: () => leggiClassiEnte(ente.tenantId), enabled: !!ente.tenantId && tipo !== "RESTITUZIONE" });

  const anniNum = anni.split(/[\s,;]+/).filter(Boolean).map(Number);
  const anniOk = anniNum.every(n => Number.isInteger(n) && n >= 1900 && n <= 2100);
  const criteri: Criteri = {
    classi, anni: criterio === "anni" ? anniNum : [],
    scadenza_entro: criterio === "scadenza" && scadenza ? scadenza : null,
  };
  const criteriOk = tipo !== "SCARTO" || (criterio === "anni" ? anniNum.length > 0 && anniOk : !!scadenza);
  const destOk = tipo !== "RIVERSAMENTO_USCITA" || destinatario.trim().length >= 3;
  const pronto = !!ente.tenantId && criteriOk && destOk && motivoValido(motivo);

  const chiama = (impronta?: string) => {
    const base = { ente: ente.tenantId, motivo: motivo.trim(), ...(impronta ? { impronta_piano: impronta } : {}) };
    // La chiave d'idempotenza serve solo alla conferma (l'anteprima non scrive)
    const k = impronta ? chiave : undefined;
    if (tipo === "SCARTO") return propostaScarto({ ...base, criteri }, k);
    if (tipo === "RIVERSAMENTO_USCITA") return riversamentoUscita({ ...base, criteri, conservatore_destinatario: destinatario.trim() }, k);
    return restituzione({ ...base, conservatore_destinatario: destinatario.trim() || null }, k);
  };
  const anteprima = useMutation({ mutationFn: () => chiama(), onSuccess: r => setPiano(r as PianoQualsiasi) });
  const conferma = useMutation({
    mutationFn: () => chiama(piano!.impronta_piano),
    onSuccess: r => { if (!èPiano(r)) { nuovaChiave(); navigate(`/pratiche/${(r as Pratica).id}`); } },
    onError: (e: unknown) => {
      const d = (e as { response?: { data?: { dettagli?: { piano?: PianoQualsiasi } } } })?.response?.data?.dettagli;
      // Piano nuovo = richiesta nuova: anche la chiave d'idempotenza è nuova
      if (leggiErrore(e).codice === "LC-409-PIANO-CAMBIATO" && d?.piano) { setPiano(p => ({ ...p, ...d.piano })); nuovaChiave(); }
    },
  });
  const cambiato = leggiErrore(conferma.error).codice === "LC-409-PIANO-CAMBIATO";
  const modifica = (f: () => void) => { f(); setPiano(null); anteprima.reset(); conferma.reset(); };
  const selezionabili = piano ? (tipo === "SCARTO" ? piano.selezionabili : piano.conteggi?.SELEZIONATO ?? 0) : 0;

  return (
    <>
      <Tappa n={2} titolo="Che cosa" nota={tipo === "RESTITUZIONE" ? "tutti i pacchetti dell'ente" : "criteri sui pacchetti conservati"}>
        {ente.globale && <div className="vd-filtri" style={{ marginBottom: 0 }}><SceltaEnte id="vd-np-ente" stato={{ ...ente, setEnte: id => modifica(() => ente.setEnte(id)) }} /></div>}
        {tipo !== "RESTITUZIONE" && (
          <fieldset className="vd-caselle">
            <legend>Classi documentali (nessuna scelta: tutte)</legend>
            {elencoCl.isError ? <span className="vd-stato vd-stato--critico">Classi non disponibili.</span> : (
              <div className="vd-caselle-elenco">
                {elencoClassi(elencoCl.data).map(c => (
                  <label key={c.id} className="vd-casella">
                    <input type="checkbox" checked={classi.includes(c.codice)}
                           onChange={e => modifica(() => setClassi(l => e.target.checked ? [...l, c.codice] : l.filter(x => x !== c.codice)))} />
                    {c.nome ?? c.codice} <span className="vd-nota vd-mono">{c.codice}</span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>
        )}
        {tipo === "SCARTO" && (
          <fieldset className="vd-scelte">
            <legend>Quali pacchetti</legend>
            <label className="vd-scelta"><input type="radio" name="vd-np-crit" checked={criterio === "scadenza"} onChange={() => modifica(() => setCriterio("scadenza"))} />
              <strong>Termine di conservazione scaduto entro una data</strong><span>Il criterio più comune: il servizio esclude da solo quelli ancora da conservare.</span></label>
            <label className="vd-scelta"><input type="radio" name="vd-np-crit" checked={criterio === "anni"} onChange={() => modifica(() => setCriterio("anni"))} />
              <strong>Anni di versamento</strong><span>Per esempio le fatture del 2015.</span></label>
          </fieldset>
        )}
        {tipo === "SCARTO" && criterio === "scadenza" && (
          <div className="vd-campo" style={{ maxWidth: 280 }}>
            <label htmlFor="vd-np-scad">Termine scaduto entro il</label>
            <input id="vd-np-scad" type="date" className="vd-input" value={scadenza} aria-invalid={toccato && !scadenza ? true : undefined} onChange={e => modifica(() => setScadenza(e.target.value))} />
          </div>
        )}
        {(tipo === "RIVERSAMENTO_USCITA" || (tipo === "SCARTO" && criterio === "anni")) && (
          <div className="vd-campo" style={{ maxWidth: 360 }}>
            <label htmlFor="vd-np-anni">Anni di versamento{tipo === "RIVERSAMENTO_USCITA" ? " (vuoto: tutti)" : ""}</label>
            <input id="vd-np-anni" className="vd-input" value={anni} inputMode="numeric" aria-describedby="vd-np-anni-aiuto"
                   aria-invalid={toccato && !anniOk ? true : undefined} onChange={e => modifica(() => setAnni(e.target.value))} />
            <span id="vd-np-anni-aiuto" className="vd-aiuto">Separati da virgola, per esempio 2014, 2015.</span>
          </div>
        )}
        {tipo !== "SCARTO" && (
          <div className="vd-campo" style={{ maxWidth: 520 }}>
            <label htmlFor="vd-np-dest">Conservatore destinatario{tipo === "RESTITUZIONE" ? " (vuoto: restituzione all'ente)" : ""}</label>
            <input id="vd-np-dest" className="vd-input" value={destinatario} maxLength={500} aria-invalid={toccato && !destOk ? true : undefined}
                   onChange={e => modifica(() => setDestinatario(e.target.value))} />
          </div>
        )}
        <CampoMotivo id="vd-np-motivo" valore={motivo} onCambia={v => modifica(() => setMotivo(v))} toccato={toccato} onTocca={() => setToccato(true)} />
      </Tappa>

      <Tappa n={3} titolo="Anteprima" nota="non scrive nulla">
        {!piano ? (
          <div className="vd-comandi">
            <button type="button" className="vd-bottone" disabled={anteprima.isPending}
                    onClick={() => { setToccato(true); if (pronto) anteprima.mutate(); }}>
              {anteprima.isPending ? "Calcolo dell'anteprima…" : "Calcola l'anteprima"}
            </button>
          </div>
        ) : <VistaPiano piano={piano} tipo={tipo} />}
        {anteprima.isError && <EsitoCritico titolo="Anteprima non disponibile" errore={anteprima.error} spiegazione="Nessuna pratica è stata creata." />}
      </Tappa>

      {piano && (
        <div className="vd-modulo">
          <h2>Conferma</h2>
          {cambiato && (
            <div className="vd-esito vd-esito--rischio" role="alert">
              <p className="vd-esito-titolo">Il piano è cambiato</p>
              <p>Fra l'anteprima e la conferma qualcosa è cambiato nei pacchetti (nuovi versamenti, scadenze, altre pratiche). Nessuna pratica è stata creata: il piano sopra è quello aggiornato. Controllalo e conferma di nuovo.</p>
            </div>
          )}
          {conferma.isError && !cambiato && <EsitoCritico titolo="Pratica non creata" errore={conferma.error} azione="operazione" />}
          {selezionabili > 0 ? (
            <div className="vd-comandi">
              <button type="button" className="vd-bottone" disabled={conferma.isPending} onClick={() => conferma.mutate()}>
                {conferma.isPending ? "Creazione…" : `Crea la pratica: ${plurale(selezionabili, "pacchetto", "pacchetti")}`}
              </button>
              <span className="vd-nota">{tipo === "SCARTO" ? "Nasce come bozza: nulla si cancella finché produttore e conservatore non autorizzano." : "Nulla si consegna finché il conservatore non la rende eseguibile."}</span>
            </div>
          ) : <p>Non c'è nulla da includere: cambia i criteri.</p>}
        </div>
      )}
    </>
  );
}

function Puntuale({ tipo }: { tipo: "RETTIFICA" | "ANNULLAMENTO" | "RIVERSAMENTO_INGRESSO" }) {
  const navigate = useNavigate();
  const ente = useEnte();
  const [chiave, nuovaChiave] = useChiaveIdempotenza();
  const [rif, setRif] = useState("");
  const [suPdv, setSuPdv] = useState(true);
  const [coppie, setCoppie] = useState<{ nome: string; valore: string }[]>([{ nome: "", valore: "" }]);
  const [classe, setClasse] = useState("");
  const [origine, setOrigine] = useState("");
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const [controllo, setControllo] = useState(false);
  const metadati = Object.fromEntries(coppie.filter(c => c.nome.trim()).map(c => [c.nome.trim(), c.valore]));
  const ok = rif.trim().length > 0 && motivoValido(motivo)
    && (tipo !== "RETTIFICA" || Object.keys(metadati).length > 0)
    && (tipo !== "RIVERSAMENTO_INGRESSO" || (classe.trim() && origine.trim().length >= 2 && !!ente.tenantId));
  const conferma = useConfermaDecisione();
  const invia = useMutation({
    mutationFn: async () => {
      if (tipo === "RIVERSAMENTO_INGRESSO")
        return riversamentoIngresso({ ente: ente.tenantId, caricamento_id: rif.trim(), classe_documentale: classe.trim(), conservatore_origine: origine.trim(), motivo: motivo.trim() }, chiave);
      // R17 (VD-P-164): richiesta di annullamento e rettifica, conferma firmata dal gateway
      const id = rif.trim().toLowerCase();
      if (tipo === "ANNULLAMENTO") {
        const firmata = await conferma.ottieni({ azione: "ANNULLAMENTO_RICHIEDI", pratica: null, versione: 0, ente: ente.tenantId,
                                                 campi: { pdv_id: id, motivo: motivo.trim() } });
        return annullamento({ pdv_id: id, motivo: motivo.trim(), ...(firmata ? { conferma: firmata } : {}) }, chiave);
      }
      const firmata = await conferma.ottieni({ azione: "RETTIFICA_RICHIEDI", pratica: null, versione: 0, ente: ente.tenantId,
                                               campi: { riferimento: `${suPdv ? "pdv" : "aip"}:${id}`, metadati_nuovi: metadati, motivo: motivo.trim() } });
      return rettifica({ ...(suPdv ? { pdv_id: id } : { aip_id: id }), metadati_nuovi: metadati, motivo: motivo.trim(), ...(firmata ? { conferma: firmata } : {}) }, chiave);
    },
    onSuccess: r => { nuovaChiave(); navigate(`/pratiche/${r.id}`); },
  });
  const cambia = <T,>(f: (v: T) => void) => (v: T) => { f(v); setControllo(false); invia.reset(); };
  const etichettaRif = tipo === "RIVERSAMENTO_INGRESSO" ? "Sessione di caricamento del pacchetto" : suPdv ? "Identificativo del versamento (PdV)" : "Identificativo del pacchetto conservato (PdA)";

  return (
    <>
      <Tappa n={2} titolo="Su che cosa">
        {tipo === "RIVERSAMENTO_INGRESSO" && ente.globale && <div className="vd-filtri" style={{ marginBottom: 0 }}><SceltaEnte id="vd-np-ente2" stato={ente} /></div>}
        {tipo === "RETTIFICA" && (
          <fieldset className="vd-scelte">
            <legend>Che cosa rettificare</legend>
            <label className="vd-scelta"><input type="radio" name="vd-np-rif" checked={suPdv} onChange={() => cambia(setSuPdv)(true)} /><strong>Un versamento</strong><span>Con il suo identificativo di versamento.</span></label>
            <label className="vd-scelta"><input type="radio" name="vd-np-rif" checked={!suPdv} onChange={() => cambia(setSuPdv)(false)} /><strong>Un pacchetto conservato</strong><span>Con l'identificativo del pacchetto di archiviazione.</span></label>
          </fieldset>
        )}
        <div className="vd-campo" style={{ maxWidth: 520 }}>
          <label htmlFor="vd-np-rifid">{etichettaRif}<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
          <input id="vd-np-rifid" className="vd-input vd-mono" value={rif} spellCheck={false} aria-invalid={toccato && !rif.trim() ? true : undefined}
                 onChange={e => cambia(setRif)(e.target.value)} aria-describedby={tipo === "RIVERSAMENTO_INGRESSO" ? "vd-np-rifid-aiuto" : undefined} />
          {tipo === "RIVERSAMENTO_INGRESSO" && <span id="vd-np-rifid-aiuto" className="vd-aiuto">Il pacchetto (ZIP SInCRO) si carica prima con una sessione di caricamento dal sistema versante; qui va l'identificativo della sessione completata.</span>}
        </div>
        {tipo === "RIVERSAMENTO_INGRESSO" && (
          <div className="vd-griglia">
            <div className="vd-campo"><label htmlFor="vd-np-cl">Classe documentale (codice)</label><input id="vd-np-cl" className="vd-input vd-mono" value={classe} onChange={e => cambia(setClasse)(e.target.value)} /></div>
            <div className="vd-campo"><label htmlFor="vd-np-or">Conservatore di provenienza</label><input id="vd-np-or" className="vd-input" value={origine} onChange={e => cambia(setOrigine)(e.target.value)} /></div>
          </div>
        )}
        {tipo === "RETTIFICA" && (
          <fieldset className="vd-caselle">
            <legend>Metadati corretti del documento principale</legend>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {coppie.map((c, i) => (
                <div key={i} className="vd-griglia">
                  <div className="vd-campo"><label htmlFor={`vd-np-mn-${i}`}>Nome del metadato</label>
                    <input id={`vd-np-mn-${i}`} className="vd-input vd-mono" value={c.nome} onChange={e => cambia(setCoppie)(coppie.map((x, k) => k === i ? { ...x, nome: e.target.value } : x))} /></div>
                  <div className="vd-campo"><label htmlFor={`vd-np-mv-${i}`}>Valore corretto</label>
                    <input id={`vd-np-mv-${i}`} className="vd-input" value={c.valore} onChange={e => cambia(setCoppie)(coppie.map((x, k) => k === i ? { ...x, valore: e.target.value } : x))} /></div>
                </div>
              ))}
              <div><button type="button" className="vd-comando-testo" onClick={() => setCoppie(l => [...l, { nome: "", valore: "" }])}>Aggiungi un metadato</button></div>
            </div>
          </fieldset>
        )}
        <CampoMotivo id="vd-np-motivo2" valore={motivo} onCambia={cambia(setMotivo)} toccato={toccato} onTocca={() => setToccato(true)} />
      </Tappa>

      <div className="vd-modulo">
        {!controllo ? (
          <div className="vd-comandi">
            <button type="button" className="vd-bottone" onClick={() => { setToccato(true); if (ok) setControllo(true); }}>Controlla e conferma</button>
          </div>
        ) : (
          <div className="vd-esito vd-esito--info">
            <p className="vd-esito-titolo">{TIPO_PRATICA[tipo]}</p>
            <dl className="vd-dati">
              <dt>{etichettaRif}</dt><dd className="vd-mono">{rif}</dd>
              {tipo === "RETTIFICA" && Object.entries(metadati).map(([k, v]) => <Fragment key={k}><dt className="vd-mono">{k}</dt><dd>{v}</dd></Fragment>)}
              <dt>Motivo</dt><dd>{motivo}</dd>
            </dl>
            <p className="vd-avviso">
              {tipo === "ANNULLAMENTO" ? "Il versamento diventa «annullato» e l'ente riceve l'evento versamento.annullato. Non si torna indietro."
                : tipo === "RETTIFICA" ? "Nasce un versamento di rettifica che segue la verifica normale; l'originale resta com'è e la rettifica lo cita."
                : "Il pacchetto viene verificato (indice, impronte, firma e marca se presenti) prima di versare i documenti."}
            </p>
            {invia.isError && !(invia.error instanceof ConfermaAnnullata) && <EsitoCritico titolo="Pratica non creata" errore={invia.error} azione="operazione" />}
            <div className="vd-comandi">
              <button type="button" className="vd-bottone" autoFocus disabled={invia.isPending} onClick={() => invia.mutate()}>
                {invia.isPending ? "Invio…" : "Conferma e crea la pratica"}
              </button>
              <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => setControllo(false)}>Correggi i dati</button>
            </div>
          </div>
        )}
      </div>
      {conferma.dialogo}
    </>
  );
}

export default function NuovaPraticaPage() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const ruolo = user?.ruolo ?? "";
  const tipi = (Object.keys(SPIEGA) as Tipo[]).filter(t =>
    ["RETTIFICA", "ANNULLAMENTO"].includes(t) ? PUNTUALI.includes(ruolo)
      : t === "RIVERSAMENTO_INGRESSO" ? AVVIO_INGRESSO.includes(ruolo) : AVVIO_MASSIVO.includes(ruolo));
  const iniziale = (params.get("tipo") ?? "").toUpperCase() as Tipo;
  const [tipo, setTipo] = useState<Tipo | "">(tipi.includes(iniziale) ? iniziale : "");

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Nuova pratica</h1>
        <p>Ogni pratica ha un motivo, un iter e chi la autorizza. Quelle che toccano molti pacchetti mostrano prima che cosa faranno.</p>
        <div className="vd-testa-comandi"><Link to="/pratiche" className="vd-comando-testo">Tutte le pratiche</Link></div>
      </div>
      {tipi.length === 0 ? (
        <div className="vd-esito"><p className="vd-esito-titolo">Il tuo profilo non avvia pratiche</p>
          <p>Le pratiche le avviano l'amministrazione dell'ente, gli operatori (rettifiche e annullamenti) e il responsabile del servizio.</p></div>
      ) : (
        <form className="vd-pila" style={{ gap: 36 }} noValidate onSubmit={e => e.preventDefault()}>
          <Tappa n={1} titolo="Tipo di pratica">
            <fieldset className="vd-scelte">
              <legend className="vd-sr">Tipo di pratica</legend>
              {tipi.map(t => (
                <label key={t} className="vd-scelta">
                  <input type="radio" name="vd-np-tipo" checked={tipo === t} onChange={() => setTipo(t)} />
                  <strong>{TIPO_PRATICA[t]}</strong><span>{SPIEGA[t]}</span>
                </label>
              ))}
            </fieldset>
          </Tappa>
          {tipo === "SCARTO" || tipo === "RIVERSAMENTO_USCITA" || tipo === "RESTITUZIONE" ? <Massiva key={tipo} tipo={tipo} />
            : tipo ? <Puntuale key={tipo} tipo={tipo} /> : null}
        </form>
      )}
    </>
  );
}
