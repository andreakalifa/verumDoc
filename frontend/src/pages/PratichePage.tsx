// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Pratiche del ciclo di vita (contratto Q2: GET /api/v1/ciclo-vita/pratiche a cursore).
 * Sistema «Partenze».
 * - «In attesa di te»: le pratiche ferme in uno stato in cui decide il ruolo di chi
 *   guarda (conservatore: rendere eseguibile ed eseguire; produttore: autorizzare).
 *   R06 (VD-C-128): UNA sola richiesta (`in_attesa_di=me`): il servizio sceglie gli
 *   stati in base al ruolo; la pagina mostra le prime e lo dichiara.
 * - Elenco completo con filtri lato servizio (tipo, stato, ente), dal più recente.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { leggiPratiche, type Pratica } from "../console/api";
import { breve, InLettura, quando, Vuoto } from "../console/comuni";
import { parola, STATO_PRATICA, TIPO_PRATICA } from "../console/parole";

const LIMITE = 30;
const PRIME = 30;

/** Cosa aspetta la pratica, per (tipo, stato): stessi stati del servizio
 * (services/ciclo-vita/app/domain/stati.py, `in_attesa_di`); qui solo le parole. */
const IN_ATTESA: Record<string, { tipo?: string; stato: string; cosa: string }[]> = {
  CONSERVATORE: [
    { stato: "ESEGUIBILE", cosa: "da eseguire" },
    { stato: "AUTORIZZATA_PRODUTTORE", cosa: "autorizzata: da rendere eseguibile o in attesa del nulla osta" },
    { stato: "NULLA_OSTA", cosa: "nulla osta ricevuto: da rendere eseguibile" },
    { tipo: "RIVERSAMENTO_USCITA", stato: "PROPOSTA", cosa: "riversamento da approvare" },
    { tipo: "RIVERSAMENTO_INGRESSO", stato: "PROPOSTA", cosa: "riversamento da approvare" },
  ],
  PRODUTTORE: [
    { tipo: "SCARTO", stato: "BOZZA", cosa: "bozza di scarto da proporre" },
    { tipo: "SCARTO", stato: "PROPOSTA", cosa: "scarto da autorizzare (autorizzazione firmata)" },
    { tipo: "RESTITUZIONE", stato: "PROPOSTA", cosa: "restituzione da autorizzare" },
  ],
};

function RigaPratica({ p, cosa, mostraEnte }: { p: Pratica; cosa?: string; mostraEnte: boolean }) {
  const q = quando(p.aggiornata_il);
  const st = parola(STATO_PRATICA, p.stato);
  const sel = p.conteggi?.SELEZIONATO, bl = p.conteggi?.BLOCCATO;
  return (
    <tr className={p.esecuzione?.lavoro ? "vd-riga-in-corso" : undefined}>
      <td className="vd-ora" data-label="Ora">{q?.ora ?? "—"}<span className="vd-sotto vd-sotto-normale">{q?.giorno}</span></td>
      <td data-label="Pratica">
        <Link className="vd-riga-link" to={`/pratiche/${p.id}`}>{TIPO_PRATICA[p.tipo] ?? p.tipo}</Link>
        <span className="vd-sotto"><span className="vd-mono" title={p.id}>{breve(p.id)}</span>{p.richiede_nulla_osta ? " · ente pubblico, serve il nulla osta" : ""}</span>
        {cosa && <span className="vd-sotto">{cosa}</span>}
      </td>
      <td data-label="Oggetti" style={{ whiteSpace: "normal" }}>
        {sel != null || bl != null
          ? <>{(sel ?? 0).toLocaleString("it-IT")} pacchetti{bl ? <span className="vd-sotto">{bl.toLocaleString("it-IT")} bloccati</span> : null}</>
          : p.pdv_id ? <>versamento <span className="vd-mono" title={p.pdv_id}>{breve(p.pdv_id)}</span></> : <span className="vd-nota">—</span>}
        {mostraEnte && <span className="vd-sotto">ente <span className="vd-mono" title={p.ente}>{breve(p.ente)}</span></span>}
      </td>
      <td className="vd-cella-larga vd-a-capo" data-label="Motivo">{p.motivo}</td>
      <td data-label="Stato" style={{ whiteSpace: "normal" }}>
        <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
        {p.esecuzione?.lavoro && <span className="vd-sotto vd-stato--info">esecuzione in corso</span>}
        {p.esecuzione?.ultimo_errore && <span className="vd-sotto vd-stato--critico">ultimo tentativo non riuscito</span>}
      </td>
    </tr>
  );
}

function Tabella({ righe, didascalia, mostraEnte, cosa, occupato }: { righe: Pratica[]; didascalia: string; mostraEnte: boolean; cosa?: Map<string, string>; occupato?: boolean }) {
  return (
    <div className="vd-scorri">
      <table className="vd-tabellone vd-impila" aria-busy={occupato}>
        <caption className="vd-sr">{didascalia}</caption>
        <thead><tr><th scope="col">Ora</th><th scope="col">Pratica</th><th scope="col">Oggetti</th><th scope="col">Motivo</th><th scope="col">Stato</th></tr></thead>
        <tbody>{righe.map(p => <RigaPratica key={p.id} p={p} cosa={cosa?.get(p.id)} mostraEnte={mostraEnte} />)}</tbody>
      </table>
    </div>
  );
}

export default function PratichePage() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const ente = useEnte();
  const [tipo, setTipo] = useState(params.get("tipo") ?? "");
  const [stato, setStato] = useState(params.get("stato") ?? "");
  const [perEnte, setPerEnte] = useState(false);
  const cursori = useCursori();
  const ruolo = ente.globale ? "CONSERVATORE" : user?.ruolo === "ADMIN_TENANT" ? "PRODUTTORE" : null;
  const attese = ruolo ? IN_ATTESA[ruolo] : [];

  const attesa = useQuery({
    queryKey: ["pratiche-attesa", ruolo],
    queryFn: () => leggiPratiche({ in_attesa_di: "me" }, null, PRIME),
    enabled: !!ruolo,
    refetchInterval: 60_000,
  });
  const cosa = new Map<string, string>();
  const inAttesa: Pratica[] = [...(attesa.data?.items ?? [])];
  inAttesa.forEach(p => {
    const voce = attese.find(a => a.stato === p.stato && (!a.tipo || a.tipo === p.tipo));
    if (voce) cosa.set(p.id, voce.cosa);
  });
  inAttesa.sort((a, b) => a.aggiornata_il.localeCompare(b.aggiornata_il)); // la più vecchia prima: aspetta da più tempo
  const erroreAttesa = attesa.isError ? attesa : undefined;
  const troncate = !!attesa.data?.altri;

  const filtri = { tipo: tipo || undefined, stato: stato || undefined, ente: ente.globale && perEnte ? ente.tenantId || undefined : undefined };
  const elenco = useQuery({ queryKey: ["pratiche", filtri, cursori.cursore], queryFn: () => leggiPratiche(filtri, cursori.cursore, LIMITE) });
  const righe = elenco.data?.items ?? [];
  const cambia = (f: () => void) => { f(); cursori.ricomincia(); };

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Pratiche</h1>
        <p>Rettifiche, annullamenti, scarti, riversamenti e restituzioni: ogni procedimento con il suo iter, i suoi allegati e chi l'ha autorizzato.</p>
        <div className="vd-testa-comandi">
          <Link to="/pratiche/nuova" className="vd-bottone" style={{ textDecoration: "none" }}>Nuova pratica</Link>
        </div>
      </div>
      <div className="vd-pila">
        {ruolo && (
          <section aria-labelledby="vd-prat-attesa">
            <div className="vd-sezione-testa">
              <h2 id="vd-prat-attesa" className="vd-etichetta">In attesa di te</h2>
              <span className="vd-nota">{ruolo === "CONSERVATORE" ? "decide il conservatore" : "decide il produttore"} · dalla più vecchia</span>
            </div>
            {attesa.isLoading ? <InLettura testo="Lettura delle pratiche in attesa…" />
              : erroreAttesa ? (
                <EsitoCritico titolo="Pratiche in attesa non disponibili" errore={erroreAttesa.error}
                              spiegazione="Non significa che non ci sia nulla da fare." onRiprova={() => void attesa.refetch()} />
              ) : inAttesa.length === 0 ? (
                <div className="vd-esito vd-esito--ok"><p className="vd-esito-titolo">Nessuna pratica aspetta una tua decisione</p></div>
              ) : (
                <>
                  <Tabella righe={inAttesa} didascalia="Pratiche in attesa di una tua decisione" mostraEnte={ente.globale} cosa={cosa} />
                  {troncate && <p className="vd-nota" style={{ marginTop: 8 }}>Sono mostrate le {PRIME} più recenti: le altre si trovano con i filtri qui sotto.</p>}
                </>
              )}
          </section>
        )}

        <section aria-labelledby="vd-prat-tutte">
          <div className="vd-sezione-testa">
            <h2 id="vd-prat-tutte" className="vd-etichetta">Tutte le pratiche</h2>
            <span className="vd-nota">dalla più recente</span>
          </div>
          <div className="vd-filtri">
            <div className="vd-campo">
              <label htmlFor="vd-prat-tipo">Tipo</label>
              <select id="vd-prat-tipo" className="vd-input" value={tipo} onChange={e => cambia(() => setTipo(e.target.value))}>
                <option value="">Tutti</option>
                {Object.entries(TIPO_PRATICA).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="vd-campo">
              <label htmlFor="vd-prat-stato">Stato</label>
              <select id="vd-prat-stato" className="vd-input" value={stato} onChange={e => cambia(() => setStato(e.target.value))}>
                <option value="">Tutti</option>
                {Object.entries(STATO_PRATICA).map(([k, v]) => <option key={k} value={k}>{v.testo}</option>)}
              </select>
            </div>
            {ente.globale && (
              <>
                <label className="vd-casella" style={{ flex: "0 1 auto" }}>
                  <input type="checkbox" checked={perEnte} onChange={e => cambia(() => setPerEnte(e.target.checked))} />
                  Solo un ente
                </label>
                {perEnte && <SceltaEnte id="vd-prat-ente" stato={ente} onCambia={cursori.ricomincia} />}
              </>
            )}
          </div>
          {elenco.isLoading ? <InLettura testo="Lettura delle pratiche…" />
            : elenco.isError ? (
              <EsitoCritico titolo="Pratiche non disponibili" errore={elenco.error}
                            spiegazione="Non significa che non ci siano pratiche." onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
            ) : righe.length === 0 ? (
              <Vuoto titolo={tipo || stato || perEnte ? "Nessuna pratica con questi filtri" : "Nessuna pratica"}>
                <p>Una pratica nasce da <Link to="/pratiche/nuova" className="vd-riga-link">Nuova pratica</Link>.</p>
              </Vuoto>
            ) : (
              <>
                <Tabella righe={righe} didascalia="Pratiche del ciclo di vita, dalla più recente" mostraEnte={ente.globale} occupato={elenco.isFetching} />
                <Paginazione cursori={cursori} prossimo={prossimoDi(elenco.data)} righe={righe.length} limite={LIMITE}
                             nome="Pratiche" etichetta="Pagine delle pratiche" />
              </>
            )}
        </section>
      </div>
    </>
  );
}
