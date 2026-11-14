// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Fascicoli — sistema grafico «Partenze» (VD-DR-01), R05 (VD-C-087).
 * Aggregazioni documentali dell'ente del profilo (distribution, /api/v2/fascicoli):
 * elenco a cursore con filtri lato servizio (stato, anno, tipo), apertura di un
 * fascicolo nuovo e chiusura con la data di chiusura, da cui decorre il termine di
 * scarto dei pacchetti (servizio ciclo-vita). La chiusura è una conferma in riga con
 * motivo e lo stato letto (409 se un altro l'ha già chiusa). Un fascicolo chiuso non
 * accetta altri pacchetti. Il servizio mostra solo i fascicoli dell'ente del profilo.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { motivoErrore } from "../api/errori";
import { chiudiFascicolo, creaFascicolo, leggiFascicoli, type Fascicolo } from "../console/api";
import { giorno, InLettura, motivoValido, MOTIVO_MIN, num, Vuoto } from "../console/comuni";

const LIMITE = 30;
const TIPI: Record<string, string> = {
  GENERICO: "Generico", PROCEDIMENTO: "Procedimento amministrativo", CONTRATTO: "Contratto", PROGETTO: "Progetto", PERSONA: "Persona",
};
const oggi = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Rome" });

function Chiudi({ f, onFine }: { f: Fascicolo; onFine: () => void }) {
  const qc = useQueryClient();
  const [data, setData] = useState(oggi());
  const [motivo, setMotivo] = useState("");
  const chiudi = useMutation({
    mutationFn: () => chiudiFascicolo(f.id, { data_chiusura: data || undefined, motivo: motivo.trim(), stato_atteso: f.stato }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fascicoli"] }); onFine(); },
  });
  return (
    <div className="vd-conferma-riga" role="group" aria-label={`Chiudi il fascicolo ${f.codice}`}>
      <p className="vd-avviso" style={{ margin: 0 }}>Dopo la chiusura il fascicolo non accetta altri pacchetti; dalla data di chiusura decorre il termine di scarto.</p>
      <label htmlFor={`vd-fc-data-${f.id}`} className="vd-nota">Data di chiusura</label>
      <input id={`vd-fc-data-${f.id}`} type="date" className="vd-input" value={data} min={f.data_apertura} max={oggi()} onChange={e => setData(e.target.value)} />
      <label htmlFor={`vd-fc-mot-${f.id}`} className="vd-nota">Motivo (almeno {MOTIVO_MIN} caratteri)</label>
      <textarea id={`vd-fc-mot-${f.id}`} className="vd-input vd-textarea" value={motivo} maxLength={1000} aria-required="true" onChange={e => setMotivo(e.target.value)} />
      {chiudi.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>{motivoErrore(chiudi.error, "operazione")}</span>}
      <div className="vd-comandi-cella">
        <button type="button" className="vd-bottone vd-bottone--secondario" disabled={!motivoValido(motivo) || chiudi.isPending} onClick={() => chiudi.mutate()}>
          {chiudi.isPending ? "Chiusura…" : "Chiudi il fascicolo"}
        </button>
        <button type="button" className="vd-comando-testo" onClick={onFine} disabled={chiudi.isPending}>Annulla</button>
      </div>
    </div>
  );
}

export default function FascicoliPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const puoScrivere = user?.ruolo !== "VIEWER";
  const [stato, setStato] = useState("");
  const [anno, setAnno] = useState("");
  const [tipo, setTipo] = useState("");
  const [chiudendo, setChiudendo] = useState<string | null>(null);
  const cursori = useCursori();
  const annoOk = !anno || /^\d{4}$/.test(anno);
  const filtri = { stato: stato || undefined, anno: annoOk ? anno || undefined : undefined, tipo: tipo || undefined };
  const elenco = useQuery({ queryKey: ["fascicoli", filtri, cursori.cursore], queryFn: () => leggiFascicoli(filtri, cursori.cursore, LIMITE) });
  const righe = elenco.data?.items ?? [];
  const cambia = (f: () => void) => { f(); cursori.ricomincia(); };

  const [titolo, setTitolo] = useState("");
  const [nuovoTipo, setNuovoTipo] = useState("PROCEDIMENTO");
  const [nuovoAnno, setNuovoAnno] = useState(String(new Date().getFullYear()));
  const [apertura, setApertura] = useState(oggi());
  const [descr, setDescr] = useState("");
  const [tentato, setTentato] = useState(false);
  const crea = useMutation({
    mutationFn: () => creaFascicolo({ titolo: titolo.trim(), tipo: nuovoTipo, anno: Number(nuovoAnno) || undefined, data_apertura: apertura || undefined, descrizione: descr.trim() || undefined }),
    onSuccess: () => { setTitolo(""); setDescr(""); setTentato(false); qc.invalidateQueries({ queryKey: ["fascicoli"] }); },
  });
  const titoloOk = titolo.trim().length >= 2;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Fascicoli</h1>
        <p>Le aggregazioni documentali dell'ente: un procedimento, un contratto, una persona. La chiusura fa partire il termine di conservazione.</p>
      </div>
      <div className="vd-pila">
        <section aria-labelledby="vd-fasc">
          <div className="vd-sezione-testa"><h2 id="vd-fasc" className="vd-etichetta">Fascicoli</h2><span className="vd-nota">dell'ente del tuo profilo, dal più recente</span></div>
          <div className="vd-filtri">
            <div className="vd-campo">
              <label htmlFor="vd-fasc-stato">Stato</label>
              <select id="vd-fasc-stato" className="vd-input" value={stato} onChange={e => cambia(() => setStato(e.target.value))}>
                <option value="">Tutti</option><option value="APERTO">Aperti</option><option value="CHIUSO">Chiusi</option>
              </select>
            </div>
            <div className="vd-campo">
              <label htmlFor="vd-fasc-anno">Anno</label>
              <input id="vd-fasc-anno" className="vd-input" inputMode="numeric" maxLength={4} value={anno} aria-invalid={!annoOk ? true : undefined}
                     onChange={e => cambia(() => setAnno(e.target.value.trim()))} />
            </div>
            <div className="vd-campo">
              <label htmlFor="vd-fasc-tipo">Tipo</label>
              <select id="vd-fasc-tipo" className="vd-input" value={tipo} onChange={e => cambia(() => setTipo(e.target.value))}>
                <option value="">Tutti</option>{Object.entries(TIPI).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
          </div>
          {elenco.isLoading ? <InLettura testo="Lettura dei fascicoli…" />
            : elenco.isError ? <EsitoCritico titolo="Fascicoli non disponibili" errore={elenco.error} spiegazione="Non significa che l'ente non abbia fascicoli." onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
            : righe.length === 0 ? <Vuoto titolo={stato || anno || tipo ? "Nessun fascicolo con questi filtri" : "Nessun fascicolo"} />
            : (
              <>
                <div className="vd-scorri">
                  <table className="vd-tabellone vd-impila" aria-busy={elenco.isFetching}>
                    <caption className="vd-sr">Fascicoli dell'ente</caption>
                    <thead><tr><th scope="col">Aperto il</th><th scope="col">Fascicolo</th><th scope="col" className="vd-num">Pacchetti</th><th scope="col">Stato</th><th scope="col">Comandi</th></tr></thead>
                    <tbody>
                      {righe.map(f => (
                        <tr key={f.id}>
                          <td className="vd-ora" data-label="Aperto il">{giorno(f.data_apertura) ?? "—"}<span className="vd-sotto vd-sotto-normale">{f.anno ?? ""}</span></td>
                          <td data-label="Fascicolo" className="vd-cella-larga">
                            <span className="vd-forte">{f.titolo}</span>
                            <span className="vd-sotto"><span className="vd-mono">{f.codice}</span> · {TIPI[f.tipo] ?? f.tipo}</span>
                          </td>
                          <td className="vd-num" data-label="Pacchetti">{num(f.num_aip)}</td>
                          <td data-label="Stato" style={{ whiteSpace: "normal" }}>
                            <span className={`vd-stato ${f.stato === "APERTO" ? "vd-stato--info" : "vd-stato--neutro"}`}>{f.stato === "APERTO" ? "Aperto" : "Chiuso"}</span>
                            {f.data_chiusura && <span className="vd-sotto">il {giorno(f.data_chiusura)}</span>}
                          </td>
                          <td>
                            {chiudendo === f.id ? <Chiudi f={f} onFine={() => setChiudendo(null)} />
                              : f.stato === "APERTO" && puoScrivere ? <button type="button" className="vd-comando-testo" onClick={() => setChiudendo(f.id)}>Chiudi</button> : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Paginazione cursori={cursori} prossimo={prossimoDi(elenco.data)} righe={righe.length} limite={LIMITE} nome="Fascicoli" etichetta="Pagine dei fascicoli" />
              </>
            )}
        </section>

        {puoScrivere && (
          <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setTentato(true); if (titoloOk) crea.mutate(); }}>
            <h2>Apri un fascicolo</h2>
            <div className="vd-griglia">
              <div className="vd-campo" style={{ gridColumn: "1 / -1" }}>
                <label htmlFor="vd-fn-tit">Oggetto<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <input id="vd-fn-tit" className="vd-input" value={titolo} maxLength={500} aria-invalid={tentato && !titoloOk ? true : undefined} onChange={e => setTitolo(e.target.value)} />
                {tentato && !titoloOk && <span className="vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Scrivi l'oggetto del fascicolo.</span>}
              </div>
              <div className="vd-campo">
                <label htmlFor="vd-fn-tipo">Tipo</label>
                <select id="vd-fn-tipo" className="vd-input" value={nuovoTipo} onChange={e => setNuovoTipo(e.target.value)}>
                  {Object.entries(TIPI).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <div className="vd-campo"><label htmlFor="vd-fn-anno">Anno</label><input id="vd-fn-anno" className="vd-input" inputMode="numeric" maxLength={4} value={nuovoAnno} onChange={e => setNuovoAnno(e.target.value)} /></div>
              <div className="vd-campo"><label htmlFor="vd-fn-ap">Data di apertura</label><input id="vd-fn-ap" type="date" className="vd-input" value={apertura} max={oggi()} onChange={e => setApertura(e.target.value)} /></div>
            </div>
            <div className="vd-campo"><label htmlFor="vd-fn-descr">Descrizione</label><textarea id="vd-fn-descr" className="vd-input vd-textarea" value={descr} maxLength={5000} onChange={e => setDescr(e.target.value)} /></div>
            {crea.isError && <EsitoCritico titolo="Fascicolo non aperto" errore={crea.error} azione="operazione" />}
            {crea.isSuccess && <p className="vd-stato vd-stato--ok" role="status">Fascicolo aperto: è in cima all'elenco.</p>}
            <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={crea.isPending}>{crea.isPending ? "Apertura…" : "Apri il fascicolo"}</button></div>
          </form>
        )}
      </div>
    </>
  );
}
