// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Dettaglio di una pratica del ciclo di vita (contratto Q2: GET /pratiche/{id},
 * POST /pratiche/{id}/transizione, POST /pratiche/{id}/allegati, GET /pratiche/{id}/oggetti).
 * Sistema «Partenze».
 *
 * - «Corsa della pratica»: l'iter completo del tipo come un tabellone di tappe, con
 *   l'ora in cui ogni stato è stato raggiunto (dalla cronologia), chi l'ha deciso e il
 *   motivo; la tappa attuale è la riga «in corso» (giallo velato).
 * - Azioni: solo quelle che il servizio restituisce in `azioni_consentite` (con il motivo
 *   di blocco per quelle non consentite); ognuna chiede il motivo e gli allegati
 *   obbligatori (caricati qui, archiviati nel WORM). La transizione manda lo stato letto:
 *   409 LC-409-STATO-CAMBIATO → la pagina dice lo stato attuale e rilegge.
 * - Allegati con impronta e firma RILEVATA (non verificata, VD-C-073).
 * - Oggetti coinvolti a cursore con filtro per esito.
 * - R14 (VD-C-133): pratica SOSPESA dopo i tentativi esauriti: lo dice al posto di «il
 *   servizio riprova da solo»; il conservatore la riprende con un motivo (versione attesa).
 */
import { Fragment, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { useConfermaDecisione } from "../components/ConfermaDecisione";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { leggiErrore } from "../api/errori";
import { NOME_RUOLO } from "../auth/ruoli";
import { useAuth } from "../auth/AuthContext";
import { api, fetchTenants } from "../api/client";
import {
  caricaAllegato, decidiAnnullamento, leggiOggettiPratica, leggiPratica, riprendiPratica, transizionePratica,
  type Allegato, type AzioneConsentita, type DettaglioPratica,
} from "../console/api";
import { ConfermaAnnullata, azioneTransizione } from "../console/conferme";
import { breve, CampoMotivo, InLettura, motivoValido, num, quando, quandoBreve, valoreInParole, Vuoto } from "../console/comuni";
import { ALLEGATO, AZIONE_VERSO, ESITO_OGGETTO, ITER, MOTIVO_BLOCCO, parola, STATO_PRATICA, TIPO_PRATICA } from "../console/parole";

const LIMITE = 30;
/** Criteri di selezione in parole: «classi FATTURA · anni 2014 · termine scaduto entro 31/12/2025». */
function criteriInParole(c: unknown, nomeClasse: (codice: string) => string = x => x): string {
  const x = (c ?? {}) as { classi?: string[]; anni?: number[]; scadenza_entro?: string | null };
  const parti = [
    x.classi?.length ? `classi ${x.classi.map(nomeClasse).join(", ")}` : "tutte le classi",
    x.anni?.length ? `anni ${x.anni.join(", ")}` : null,
    x.scadenza_entro ? `termine scaduto entro ${new Date(x.scadenza_entro).toLocaleDateString("it-IT")}` : null,
  ];
  return parti.filter(Boolean).join(" · ");
}
const CONTEGGIO: Record<string, string> = { SELEZIONATO: "Inclusi", BLOCCATO: "Bloccati" };
/** R17: la persona ha chiuso la conferma dell'identità: nessun errore da mostrare. */
const annullata = (e: unknown) => e instanceof ConfermaAnnullata;
const nomeRuolo = (r?: string | null) => (r ? NOME_RUOLO[r] ?? (r === "SISTEMA" ? "servizio" : r) : "");

function Corsa({ p }: { p: DettaglioPratica }) {
  const iter = (ITER[p.tipo] ?? []).filter(s => s !== "NULLA_OSTA" || p.richiede_nulla_osta);
  const raggiunto = new Map(p.cronologia.map(c => [c.a_stato, c]));
  const indice = iter.indexOf(p.stato);
  const respinta = p.stato === "RESPINTA" ? raggiunto.get("RESPINTA") : undefined;
  const righe = respinta ? [...iter.filter(s => raggiunto.has(s)), "RESPINTA"] : iter;
  return (
    <table className="vd-tabellone vd-impila" aria-live="polite">
      <caption className="vd-sr">Iter della pratica con l'ora di ogni tappa</caption>
      <thead><tr><th scope="col">Ora</th><th scope="col">Tappa</th><th scope="col">Chi e perché</th></tr></thead>
      <tbody>
        {righe.map((s, i) => {
          const c = raggiunto.get(s);
          const q = quando(c?.il);
          const attuale = s === p.stato && s !== "RESPINTA" && !(ITER[p.tipo]?.slice(-1)[0] === s);
          const fatta = !!c && !attuale;
          const st = s === "RESPINTA" ? parola(STATO_PRATICA, "RESPINTA") : fatta ? { testo: "Fatto", classe: "vd-stato--ok" }
            : attuale ? { testo: "Qui adesso", classe: "vd-stato--info" }
            : indice >= 0 && i < indice ? { testo: "Superata", classe: "vd-stato--neutro" } : { testo: "In attesa", classe: "vd-stato--neutro" };
          return (
            <tr key={s} className={attuale ? "vd-riga-in-corso" : undefined}>
              <td className={`vd-ora${q ? "" : " vd-ora--attesa"}`} data-label="Ora">{q?.ora ?? "--:--"}<span className="vd-sotto vd-sotto-normale">{q?.giorno ?? ""}</span></td>
              <td data-label="Tappa">
                {parola(STATO_PRATICA, s).testo}
                <span className={`vd-sotto vd-stato ${st.classe}`}>{st.testo}</span>
              </td>
              <td data-label="Chi e perché" className="vd-cella-larga vd-a-capo">
                {c ? <>{nomeRuolo(c.ruolo)}{c.motivo ? <span className="vd-sotto">{c.motivo}</span> : null}{c.allegati?.length ? <span className="vd-sotto">{c.allegati.length} allegati citati</span> : null}</> : <span className="vd-nota">—</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Azione({ p, a, onFine, onFatto }: { p: DettaglioPratica; a: AzioneConsentita; onFine: () => void; onFatto: (e: { stato: string; esecuzione: boolean }) => void }) {
  const qc = useQueryClient();
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const [scelti, setScelti] = useState<Record<string, string>>({});
  const [carico, setCarico] = useState<string | null>(null);
  const [erroreCarico, setErroreCarico] = useState<unknown>(null);
  const fileRef = useRef<Record<string, HTMLInputElement | null>>({});
  const conferma = useConfermaDecisione();

  const invia = useMutation({
    mutationFn: async () => {
      // R17 (VD-P-164): approvazione ed esecuzione di uno scarto, esecuzione di un
      // riversamento in uscita: conferma firmata dal gateway del piano deciso
      const azione = azioneTransizione(p.tipo, a.nuovo_stato);
      const firmata = azione ? await conferma.ottieni({
        azione, pratica: p.id, versione: p.versione, ente: p.ente,
        campi: { impronta_piano: p.impronta_piano ?? null },
      }) : undefined;
      return transizionePratica(p.id, {
        stato_atteso: p.stato, nuovo_stato: a.nuovo_stato, motivo: motivo.trim(),
        allegati: a.allegati_richiesti.map(t => scelti[t]).filter(Boolean),
        ...(firmata ? { conferma: firmata } : {}),
      });
    },
    onSuccess: r => { onFatto({ stato: a.nuovo_stato, esecuzione: !!r.esecuzione_richiesta }); qc.invalidateQueries({ queryKey: ["pratica", p.id] }); qc.invalidateQueries({ queryKey: ["pratiche"] }); },
  });
  const carica = async (tipo: string, f: File | undefined) => {
    if (!f) return;
    setCarico(tipo); setErroreCarico(null);
    try {
      const r = await caricaAllegato(p.id, tipo, f);
      setScelti(s => ({ ...s, [tipo]: r.id }));
      qc.invalidateQueries({ queryKey: ["pratica", p.id] });
    } catch (e) { setErroreCarico(e); } finally { setCarico(null); }
  };
  const mancano = a.allegati_richiesti.filter(t => !scelti[t]);
  const cambiato = leggiErrore(invia.error).codice === "LC-409-STATO-CAMBIATO";
  const statoAttuale = (invia.error as { response?: { data?: { dettagli?: { stato_attuale?: string } } } } | null)?.response?.data?.dettagli?.stato_attuale;
  const verbo = AZIONE_VERSO[a.nuovo_stato] ?? `Porta a ${parola(STATO_PRATICA, a.nuovo_stato).testo.toLowerCase()}`;
  const minimo = a.motivo_minimo ?? undefined;

  return (
    <>
    <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setToccato(true); if (motivoValido(motivo, minimo) && !mancano.length) invia.mutate(); }}>
      <h2>{verbo}</h2>
      {a.nuovo_stato === "CHIUSA_CON_ECCEZIONI" && <p className="vd-avviso" style={{ marginTop: -8 }}>I pacchetti non cancellati del tutto restano conservati in parte e continuano a essere verificati; la pratica non si riprende più. Il motivo resta nel registro delle impronte.</p>}
      {a.esecuzione && <p className="vd-avviso" style={{ marginTop: -8 }}>L'esecuzione non si annulla: per lo scarto, ogni pacchetto si cancella dallo storage solo a retention scaduta e resta una traccia nel registro delle impronte.</p>}
      {a.allegati_richiesti.map(t => {
        const esistenti = p.allegati.filter(x => x.tipo === t);
        return (
          <fieldset key={t} className="vd-scelte">
            <legend>{ALLEGATO[t] ?? t}<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></legend>
            {esistenti.map(x => (
              <label key={x.id} className="vd-scelta">
                <input type="radio" name={`vd-all-${t}`} checked={scelti[t] === x.id} onChange={() => setScelti(s => ({ ...s, [t]: x.id }))} />
                <strong>{x.nome_file}</strong>
                <span>caricato il {quandoBreve(x.caricato_il)} · {x.firma?.presente ? `firma ${String(x.firma.tipo)} rilevata, non verificata` : "senza firma"}</span>
              </label>
            ))}
            <div className="vd-comandi" style={{ marginTop: 8 }}>
              <input ref={el => { fileRef.current[t] = el; }} id={`vd-file-${t}`} type="file" className="vd-sr" onChange={e => carica(t, e.target.files?.[0])} />
              <label htmlFor={`vd-file-${t}`} className="vd-bottone vd-bottone--secondario" style={{ cursor: "pointer" }} tabIndex={0}
                     onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileRef.current[t]?.click(); } }}>
                {carico === t ? "Caricamento…" : "Carica un file"}
              </label>
              {/* R12a (VD-C-145) scarto, R12b (VD-C-160) restituzione: documento legato alla pratica */}
              {t === "AUTORIZZAZIONE_PRODUTTORE" && !LEGATE.includes(p.tipo) && <span className="vd-nota">Documento firmato: CAdES (.p7m) o PDF con firma PAdES.</span>}
              {(t === "AUTORIZZAZIONE_PRODUTTORE" || t === "NULLA_OSTA") && LEGATE.includes(p.tipo) && (
                <span className="vd-nota">Documento firmato (CAdES .p7m o PDF con firma PAdES) che riporta nel testo firmato l'identificativo di questa pratica: <code>{p.id}</code>.</span>
              )}
            </div>
            {toccato && !scelti[t] && <span className="vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Scegli o carica il documento.</span>}
          </fieldset>
        );
      })}
      {erroreCarico != null && <EsitoCritico titolo="Allegato non caricato" errore={erroreCarico} azione="operazione" />}
      <CampoMotivo id={`vd-trans-${a.nuovo_stato}`} valore={motivo} onCambia={setMotivo} toccato={toccato} onTocca={() => setToccato(true)} minimo={minimo} />
      {cambiato && (
        <div className="vd-esito vd-esito--rischio" role="alert">
          <p className="vd-esito-titolo">Lo stato della pratica è cambiato</p>
          <p>
            {statoAttuale ? <>Adesso è «{parola(STATO_PRATICA, statoAttuale).testo}». </> : null}
            Qualcun altro ha agito nel frattempo: nulla è stato registrato. Rileggi la pratica e decidi di nuovo.
          </p>
          <div><button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => { qc.invalidateQueries({ queryKey: ["pratica", p.id] }); onFine(); }}>Rileggi la pratica</button></div>
        </div>
      )}
      {invia.isError && !cambiato && !annullata(invia.error) && <EsitoCritico titolo="Transizione non registrata" errore={invia.error} azione="operazione" spiegazione="Lo stato della pratica non è cambiato." />}
      <div className="vd-comandi">
        <button type="submit" className={a.nuovo_stato === "RESPINTA" ? "vd-bottone vd-bottone--secondario" : "vd-bottone"} disabled={invia.isPending || !!carico}>
          {invia.isPending ? "Registrazione…" : `Conferma: ${verbo.toLowerCase()}`}
        </button>
        <button type="button" className="vd-comando-testo" onClick={onFine} disabled={invia.isPending}>Annulla</button>
      </div>
    </form>
    {conferma.dialogo}
    </>
  );
}

function Allegati({ elenco }: { elenco: Allegato[] }) {
  if (!elenco.length) return <p className="vd-nota">Nessun allegato.</p>;
  return (
    <div className="vd-scorri">
      <table className="vd-tabellone vd-impila">
        <caption className="vd-sr">Allegati della pratica</caption>
        <thead><tr><th scope="col">Ora</th><th scope="col">Documento</th><th scope="col">Impronta</th><th scope="col">Firma</th></tr></thead>
        <tbody>
          {elenco.map(x => {
            const q = quando(x.caricato_il);
            return (
              <tr key={x.id}>
                <td className="vd-ora" data-label="Ora">{q?.ora ?? "—"}<span className="vd-sotto vd-sotto-normale">{q?.giorno}</span></td>
                <td data-label="Documento">{ALLEGATO[x.tipo] ?? x.tipo}<span className="vd-sotto vd-a-capo">{x.nome_file} · {num(Math.ceil(x.dimensione / 1024))} KB</span></td>
                <td data-label="Impronta"><span className="vd-mono" title={x.sha256}>{x.sha256.slice(0, 16)}…</span><span className="vd-sotto">SHA-256{x.conserva_fino_a ? ` · conservato fino al ${new Date(x.conserva_fino_a).toLocaleDateString("it-IT")}` : ""}</span></td>
                <td data-label="Firma" style={{ whiteSpace: "normal" }}>
                  {x.firma?.presente
                    ? <><span className="vd-stato vd-stato--rischio">Rilevata, non verificata</span><span className="vd-sotto">{String(x.firma.tipo ?? "")}</span></>
                    : <span className="vd-stato vd-stato--neutro">Nessuna</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Oggetti({ id, conteggi }: { id: string; conteggi: Record<string, number> }) {
  const [esito, setEsito] = useState("");
  const cursori = useCursori();
  const elenco = useQuery({ queryKey: ["pratica-oggetti", id, esito, cursori.cursore], queryFn: () => leggiOggettiPratica(id, esito || undefined, cursori.cursore, LIMITE) });
  const righe = elenco.data?.items ?? [];
  const chiavi = Object.keys(conteggi);
  return (
    <>
      {chiavi.length > 0 && (
        <dl className="vd-dati" style={{ marginBottom: 20 }}>
          {chiavi.map(k => <Fragment key={k}><dt>{CONTEGGIO[k] ?? parola(ESITO_OGGETTO, k).testo}</dt><dd><span className="vd-numero">{num(conteggi[k])}</span></dd></Fragment>)}
        </dl>
      )}
      <div className="vd-filtri">
        <div className="vd-campo">
          <label htmlFor="vd-ogg-esito">Esito</label>
          <select id="vd-ogg-esito" className="vd-input" value={esito} onChange={e => { setEsito(e.target.value); cursori.ricomincia(); }}>
            <option value="">Tutti</option>
            {chiavi.map(k => <option key={k} value={k}>{parola(ESITO_OGGETTO, k).testo}</option>)}
          </select>
        </div>
      </div>
      {elenco.isLoading ? <InLettura testo="Lettura degli oggetti…" />
        : elenco.isError ? <EsitoCritico titolo="Oggetti non disponibili" errore={elenco.error} onRiprova={() => elenco.refetch()} />
        : righe.length === 0 ? <Vuoto titolo="Nessun oggetto con questo esito" />
        : (
          <>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-impila--pieno" aria-busy={elenco.isFetching}>
                <caption className="vd-sr">Oggetti coinvolti nella pratica</caption>
                <thead><tr><th scope="col">Pacchetto</th><th scope="col">Esito</th></tr></thead>
                <tbody>
                  {righe.map((o, k) => {
                    const es = parola(ESITO_OGGETTO, o.esito);
                    return (
                      <tr key={`${o.aip_id ?? o.pdv_id ?? o.urn}-${k}`}>
                        <td data-label="Pacchetto" className="vd-a-capo"><span className="vd-mono">{o.urn ?? o.aip_id ?? o.pdv_id}</span></td>
                        <td data-label="Esito" style={{ whiteSpace: "normal" }}>
                          <span className={`vd-stato ${es.classe}`}>{es.testo}</span>
                          {o.motivo && <span className="vd-sotto vd-a-capo">{MOTIVO_BLOCCO[o.motivo] ?? o.motivo}</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Paginazione cursori={cursori} prossimo={prossimoDi(elenco.data)} righe={righe.length} limite={LIMITE}
                         nome="Oggetti" etichetta="Pagine degli oggetti" testoPrecedente="Precedenti" testoSuccessiva="Successivi" />
          </>
        )}
    </>
  );
}

/** R09 (D23): la seconda persona dell'ente approva o respinge l'annullamento. */
function Approvazione({ p }: { p: DettaglioPratica }) {
  const qc = useQueryClient();
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const conferma = useConfermaDecisione();
  const decidi = useMutation({
    mutationFn: async (esito: "APPROVA" | "RESPINGI") => {
      // R17 (VD-P-164): approvare è una decisione sensibile; respingere no
      const firmata = esito === "APPROVA" ? await conferma.ottieni({
        azione: "ANNULLAMENTO_APPROVA", pratica: p.id, versione: p.versione, ente: p.ente,
        campi: { pdv_id: p.pdv_id ?? null, motivo: p.motivo },
      }) : undefined;
      return decidiAnnullamento(p.id, { esito, motivo: motivo.trim(), ...(firmata ? { conferma: firmata } : {}) });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pratica", p.id] }); qc.invalidateQueries({ queryKey: ["pratiche"] }); },
  });
  const a = p.approvazione;
  if (!a?.in_attesa) return null;
  const invia = (esito: "APPROVA" | "RESPINGI") => { setToccato(true); if (motivoValido(motivo)) decidi.mutate(esito); };
  return (
    <section aria-labelledby="vd-pr-approvazione">
      <div className="vd-sezione-testa"><h2 id="vd-pr-approvazione" className="vd-etichetta">{a.solo_respingere ? "Annullamento fermo" : "Seconda approvazione"}</h2></div>
      {a.consentita && a.solo_respingere ? (
        <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); invia("RESPINGI"); }}>
          <p>L'annullamento è fermo: il ricontrollo del sistema non è riuscito (vedi «In breve»). Il versamento è intatto. Respingilo; poi, se serve, chiedi di nuovo l'annullamento.</p>
          <CampoMotivo id="vd-approvazione" valore={motivo} onCambia={setMotivo} toccato={toccato} onTocca={() => setToccato(true)} />
          {decidi.isError && !annullata(decidi.error) && <EsitoCritico titolo="Decisione non registrata" errore={decidi.error} azione="operazione" />}
          <div className="vd-comandi">
            <button type="submit" className="vd-bottone vd-bottone--secondario" disabled={decidi.isPending}>Respingi</button>
          </div>
        </form>
      ) : a.consentita ? (
        <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); invia("APPROVA"); }}>
          <p>L'annullamento è stato chiesto da un'altra persona: decidi tu se approvarlo. Approvato, il sistema lo ricontrolla e annulla il versamento entro pochi secondi (nessun file cancellato).</p>
          <CampoMotivo id="vd-approvazione" valore={motivo} onCambia={setMotivo} toccato={toccato} onTocca={() => setToccato(true)} />
          {decidi.isError && !annullata(decidi.error) && <EsitoCritico titolo="Decisione non registrata" errore={decidi.error} azione="operazione" />}
          <div className="vd-comandi">
            <button type="submit" className="vd-bottone" disabled={decidi.isPending}>Approva l'annullamento</button>
            <button type="button" className="vd-bottone vd-bottone--secondario" disabled={decidi.isPending} onClick={() => invia("RESPINGI")}>Respingi</button>
          </div>
        </form>
      ) : (
        <p className="vd-nota">{a.motivo_blocco ?? "Non puoi approvare questo annullamento."} Possono approvare: {a.ruoli_ammessi.map(nomeRuolo).join(", ") || "nessun ruolo"}.</p>
      )}
      {conferma.dialogo}
    </section>
  );
}

/** R14 (VD-C-133): pratica sospesa dopo i tentativi esauriti; ripresa del conservatore. */
function Sospesa({ p }: { p: DettaglioPratica }) {
  const qc = useQueryClient();
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const conferma = useConfermaDecisione();
  const riprendi = useMutation({
    mutationFn: async () => {
      // R17 (VD-P-164): la ripresa rimette in esecuzione un lavoro distruttivo
      const firmata = await conferma.ottieni({
        azione: "PRATICA_RIPRENDI", pratica: p.id, versione: p.versione, ente: p.ente,
        campi: { lavoro: p.esecuzione?.lavoro_sospeso ?? null },
      });
      return riprendiPratica(p.id, { versione: p.versione, motivo: motivo.trim(), ...(firmata ? { conferma: firmata } : {}) });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pratica", p.id] }); qc.invalidateQueries({ queryKey: ["pratiche"] }); },
    onError: e => { if (leggiErrore(e).codice === "LC-409-STATO-CAMBIATO") qc.invalidateQueries({ queryKey: ["pratica", p.id] }); },
  });
  if (!p.esecuzione?.lavoro_sospeso) return null;
  const tentativi = (p.esecuzione.ultimo_errore as { tentativi?: number } | null)?.tentativi;
  const cambiato = leggiErrore(riprendi.error).codice === "LC-409-STATO-CAMBIATO";
  return (
    <section aria-labelledby="vd-pr-sospesa">
      <div className="vd-sezione-testa"><h2 id="vd-pr-sospesa" className="vd-etichetta">Pratica ferma</h2></div>
      <p>Il servizio ha provato {tentativi ? `${num(tentativi)} volte` : "più volte"} senza riuscirci e si è fermato: non riprova più da solo. Ciò che era già stato fatto resta registrato; la ripresa continua da lì.</p>
      {p.esecuzione.ripresa_consentita ? (
        <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setToccato(true); if (motivoValido(motivo, 20)) riprendi.mutate(); }}>
          <CampoMotivo id="vd-ripresa" etichetta="Causa corretta e motivo della ripresa" valore={motivo} onCambia={setMotivo} toccato={toccato} onTocca={() => setToccato(true)} minimo={20}
                       aiuto="Resta nel registro delle attività. Prima di eseguire, il servizio ricontrolla da sé le autorizzazioni." />
          {riprendi.isError && (cambiato
            ? <p className="vd-avviso" role="alert">La pratica è cambiata nel frattempo: la pagina è stata aggiornata, controlla e riprova.</p>
            : annullata(riprendi.error) ? null : <EsitoCritico titolo="Ripresa non registrata" errore={riprendi.error} azione="operazione" />)}
          <div className="vd-comandi">
            <button type="submit" className="vd-bottone" disabled={riprendi.isPending}>{riprendi.isPending ? "Ripresa in corso…" : "Riprendi il lavoro"}</button>
          </div>
        </form>
      ) : (
        <p className="vd-nota">La pratica la riprende il conservatore, corretta la causa.</p>
      )}
      {conferma.dialogo}
    </section>
  );
}

/** Pratiche i cui documenti autorizzativi devono citarne l'identificativo nel testo firmato. */
const LEGATE = ["SCARTO", "RESTITUZIONE"];

export default function PraticaPage() {
  // revisione R17 (R17-15): ente con la ragione sociale e classi con il nome, non codici
  const { user } = useAuth();
  const globale = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  const enti = useQuery({ queryKey: ["tenants-scelta"], queryFn: () => fetchTenants(), enabled: globale });
  const classiNomi = useQuery({ queryKey: ["classi-ricerca"],
                                queryFn: () => api.get("/dashboard/classi-documentali?limit=100").then(r => r.data) });
  const nomeEnte = (id: string | null | undefined): string | null => {
    if (!id) return null;
    if (id === user?.tenant_id && user?.ragione_sociale) return user.ragione_sociale;
    const e = ((enti.data?.items ?? []) as { id: string; ragione_sociale?: string }[]).find(x => x.id === id);
    return e?.ragione_sociale ?? null;
  };
  const nomeClasse = (codice: string): string => {
    const c = ((classiNomi.data?.items ?? []) as { codice: string; nome?: string }[]).find(x => x.codice === codice);
    return c?.nome || codice;
  };
  const { id = "" } = useParams();
  const [aperta, setAperta] = useState<string | null>(null);
  const [fatto, setFatto] = useState<{ stato: string; esecuzione: boolean } | null>(null);
  const pratica = useQuery({
    queryKey: ["pratica", id], queryFn: () => leggiPratica(id),
    // Durante l'esecuzione la corsa si aggiorna da sola (solo con la scheda in primo piano)
    refetchInterval: q => (q.state.data?.esecuzione?.lavoro ? 5_000 : false),
  });
  if (pratica.isLoading) return <InLettura testo="Lettura della pratica…" />;
  if (pratica.isError || !pratica.data) {
    return <EsitoCritico titolo="Pratica non disponibile" errore={pratica.error} onRiprova={() => pratica.refetch()} inRiprova={pratica.isFetching} />;
  }
  const p = pratica.data;
  const st = parola(STATO_PRATICA, p.stato);
  const consentite = p.azioni_consentite.filter(a => a.consentita);
  const bloccate = p.azioni_consentite.filter(a => !a.consentita);
  const azione = consentite.find(a => a.nuovo_stato === aperta);
  const dati = Object.entries(p.dati ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== "");

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>{TIPO_PRATICA[p.tipo] ?? p.tipo}</h1>
        <p><span className={`vd-stato ${st.classe}`}>{st.testo}</span> · pratica <span className="vd-mono" title={p.id}>{breve(p.id)}</span> · aperta il {quandoBreve(p.creata_il)}</p>
        <div className="vd-testa-comandi">
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => pratica.refetch()} disabled={pratica.isFetching}>
            {pratica.isFetching ? "Aggiornamento…" : "Aggiorna"}
          </button>
          <Link to="/pratiche" className="vd-comando-testo">Tutte le pratiche</Link>
        </div>
      </div>

      <div className="vd-pila">
        {p.in_parole && (
          <div className="vd-esito vd-esito--info" role="status">
            <p className="vd-esito-titolo">In breve</p>
            <p>{p.in_parole}</p>
          </div>
        )}
        <Approvazione p={p} />
        {p.esecuzione?.lavoro && (
          <div className="vd-esito vd-esito--info" role="status">
            <p className="vd-esito-titolo">Esecuzione in corso</p>
            <p>Tentativo {num(p.esecuzione.tentativi)}{p.esecuzione.prossimo_tentativo ? ` · prossimo tentativo ${quandoBreve(p.esecuzione.prossimo_tentativo)}` : ""}. La pagina si aggiorna ogni 5 secondi.</p>
          </div>
        )}
        {p.esecuzione?.ultimo_errore && (
          <div className="vd-esito vd-esito--critico" role="alert">
            <p className="vd-esito-titolo">L'ultimo tentativo di esecuzione non è riuscito</p>
            <p className="vd-a-capo">{String((p.esecuzione.ultimo_errore as { messaggio?: string }).messaggio ?? JSON.stringify(p.esecuzione.ultimo_errore))}</p>
            {!p.esecuzione.lavoro_sospeso && <p className="vd-nota">Il servizio riprova da solo: nessun pacchetto è stato cancellato due volte.</p>}
          </div>
        )}
        <Sospesa p={p} />

        <section aria-labelledby="vd-pr-corsa">
          <div className="vd-sezione-testa">
            <h2 id="vd-pr-corsa" className="vd-etichetta">Corsa della pratica</h2>
            {p.richiede_nulla_osta && <span className="vd-nota">ente pubblico: serve il nulla osta della Soprintendenza</span>}
          </div>
          <div className="vd-scorri"><Corsa p={p} /></div>
        </section>

        {p.azioni_consentite.length > 0 && (
          <section aria-labelledby="vd-pr-azioni">
            <div className="vd-sezione-testa"><h2 id="vd-pr-azioni" className="vd-etichetta">Prossimo passo</h2></div>
            {fatto && !azione && (
              <div className="vd-esito vd-esito--ok" role="status" style={{ marginBottom: 20 }}>
                <p className="vd-esito-titolo">{fatto.esecuzione ? "Esecuzione richiesta" : `Fatto: ${parola(STATO_PRATICA, fatto.stato).testo.toLowerCase()}`}</p>
                <p>{fatto.esecuzione
                  ? "Il servizio esegue la pratica fuori da questa pagina: lo stato diventa «Eseguita» quando ogni pacchetto è stato trattato. La corsa si aggiorna da sola."
                  : "La tappa è registrata nella corsa con il tuo motivo."}</p>
              </div>
            )}
            {azione ? <Azione p={p} a={azione} onFine={() => setAperta(null)} onFatto={e => { setFatto(e); setAperta(null); }} /> : (
              <>
                {consentite.length > 0 ? (
                  <div className="vd-comandi">
                    {consentite.map(a => (
                      <button key={a.nuovo_stato} type="button"
                              className={a.nuovo_stato === "RESPINTA" ? "vd-bottone vd-bottone--secondario" : "vd-bottone"}
                              onClick={() => { setFatto(null); setAperta(a.nuovo_stato); }}>
                        {AZIONE_VERSO[a.nuovo_stato] ?? a.nuovo_stato}…
                      </button>
                    ))}
                  </div>
                ) : <p>Nessun passo spetta al tuo profilo in questo stato.</p>}
                {bloccate.length > 0 && (
                  <ul className="vd-elenco" style={{ marginTop: 16, maxWidth: 820 }}>
                    {bloccate.map(a => (
                      <li key={a.nuovo_stato}>
                        <span>{AZIONE_VERSO[a.nuovo_stato] ?? a.nuovo_stato}</span>
                        <span className="vd-nota">{a.motivo_blocco ?? "non consentito"}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>
        )}

        <section aria-labelledby="vd-pr-dati">
          <div className="vd-sezione-testa"><h2 id="vd-pr-dati" className="vd-etichetta">Dati della pratica</h2></div>
          <dl className="vd-dati">
            <dt>Motivo</dt><dd>{p.motivo}</dd>
            <dt>Ente</dt><dd>{nomeEnte(p.ente) ?? <span className="vd-mono">{p.ente}</span>}</dd>
            <dt>Aperta da</dt><dd>{nomeRuolo(p.creata_ruolo) || "—"}</dd>
            {p.pdv_id && <><dt>Versamento</dt><dd className="vd-mono">{p.pdv_id}</dd></>}
            {p.pdv_risultato_id && <><dt>Versamento prodotto</dt><dd className="vd-mono">{p.pdv_risultato_id}</dd></>}
            {p.impronta_piano && <><dt>Impronta del piano</dt><dd className="vd-mono">{p.impronta_piano}</dd></>}
            {dati.map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ").replace(/^./, c => c.toUpperCase())}</dt><dd>{k === "criteri" ? criteriInParole(v, nomeClasse) : valoreInParole(v)}</dd></Fragment>)}
            {p.conclusa_il && <><dt>Conclusa</dt><dd>{quandoBreve(p.conclusa_il)}</dd></>}
          </dl>
        </section>

        <section aria-labelledby="vd-pr-allegati">
          <div className="vd-sezione-testa"><h2 id="vd-pr-allegati" className="vd-etichetta">Allegati</h2><span className="vd-nota">archiviati nello storage conservativo</span></div>
          <Allegati elenco={p.allegati} />
        </section>

        {Object.keys(p.oggetti?.conteggi ?? {}).length > 0 && (
          <section aria-labelledby="vd-pr-oggetti">
            <div className="vd-sezione-testa"><h2 id="vd-pr-oggetti" className="vd-etichetta">Pacchetti coinvolti</h2></div>
            <Oggetti id={p.id} conteggi={p.oggetti.conteggi} />
          </section>
        )}
      </div>
    </>
  );
}
