// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Webhook dell'ente (API notification /api/v1/webhook, contratto C3; solo ADMIN_TENANT).
 * Non esisteva una pagina: R05 console. Sistema «Partenze».
 * - Elenco delle sottoscrizioni; attiva/disattiva come conferma in riga.
 * - Nuovo webhook: URL https, tipi di evento, descrizione; il segreto HMAC si mostra
 *   UNA volta (`.vd-chiave`), con `Idempotency-Key` per non crearne due.
 * - Consegne in un dialogo laterale, a cursore (`dopo`), filtro per stato; una consegna
 *   FALLITA si riprova (202, rimessa in coda).
 * - R13b (VD-C-078): «Ruota il segreto» come conferma in riga (periodo di transizione):
 *   il nuovo segreto si mostra una volta; finché la transizione dura ogni consegna porta
 *   due firme (nuovo e precedente) e la riga lo dice, con «Ritira il precedente».
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, useCursori } from "../components/Paginazione";
import { motivoErrore } from "../api/errori";
import { chiudiTransizioneWebhook, creaWebhook, leggiConsegne, leggiTipiEvento, leggiWebhook, modificaWebhook, riprovaConsegna, ruotaSegretoWebhook, type Webhook } from "../console/api";
import { DialogoLaterale, InLettura, quando, quandoBreve, useChiaveIdempotenza, Vuoto } from "../console/comuni";
import { ETICHETTA_EVENTO, parola, STATO_CONSEGNA } from "../console/parole";

const LIMITE = 30;

function Consegne({ w, onClose }: { w: Webhook; onClose: () => void }) {
  const qc = useQueryClient();
  const [stato, setStato] = useState("FALLITA");
  const cursori = useCursori();
  const dopo = Number(cursori.cursore ?? 0);
  const elenco = useQuery({ queryKey: ["consegne", w.id, stato, dopo], queryFn: () => leggiConsegne(w.id, stato || undefined, dopo, LIMITE) });
  const riprova = useMutation({ mutationFn: (id: number) => riprovaConsegna(w.id, id), onSuccess: () => qc.invalidateQueries({ queryKey: ["consegne", w.id] }) });
  const righe = elenco.data?.consegne ?? [];
  const prossimo = elenco.data?.altri ? String(elenco.data.cursore) : null;
  return (
    <DialogoLaterale id="vd-wh-cons" titolo="Consegne" sottotitolo={w.url} onClose={onClose}>
      <div className="vd-filtri" style={{ marginBottom: 0 }}>
        <div className="vd-campo">
          <label htmlFor="vd-wh-stato">Stato</label>
          <select id="vd-wh-stato" className="vd-input" value={stato} onChange={e => { setStato(e.target.value); cursori.ricomincia(); }}>
            <option value="FALLITA">Fallite (coda di scarto)</option>
            {Object.entries(STATO_CONSEGNA).filter(([k]) => k !== "FALLITA").map(([k, v]) => <option key={k} value={k}>{v.testo}</option>)}
            <option value="">Tutte</option>
          </select>
        </div>
      </div>
      {elenco.isLoading ? <InLettura testo="Lettura delle consegne…" />
        : elenco.isError ? <EsitoCritico titolo="Consegne non disponibili" errore={elenco.error} onRiprova={() => elenco.refetch()} />
        : righe.length === 0 ? <Vuoto titolo={stato === "FALLITA" ? "Nessuna consegna fallita" : "Nessuna consegna con questo stato"} />
        : (
          <section aria-label="Elenco delle consegne">
            <table className="vd-tabellone vd-impila" aria-busy={elenco.isFetching}>
              <caption className="vd-sr">Consegne del webhook, dalla più vecchia</caption>
              <thead><tr><th scope="col">Ora</th><th scope="col">Evento</th><th scope="col">Esito</th></tr></thead>
              <tbody>
                {righe.map(c => {
                  const q = quando(c.ultimo_tentativo_il ?? c.creata_il);
                  const st = parola(STATO_CONSEGNA, c.stato);
                  return (
                    <tr key={c.id}>
                      <td className="vd-ora" data-label="Ora">{q?.ora ?? "—"}<span className="vd-sotto vd-sotto-normale">{q?.giorno}</span></td>
                      <td data-label="Evento"><span className="vd-mono">{c.tipo_evento}</span><span className="vd-sotto">consegna n. {c.id} · evento {c.evento_id}</span></td>
                      <td data-label="Esito" style={{ whiteSpace: "normal" }}>
                        <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
                        <span className="vd-sotto">{c.tentativi} tentativi{c.riprove ? ` · ${c.riprove} riprove chieste` : ""}{c.ultimo_stato_http ? ` · HTTP ${c.ultimo_stato_http}` : ""}</span>
                        {c.ultimo_errore && <span className="vd-sotto vd-a-capo">{c.ultimo_errore}</span>}
                        {c.stato === "FALLITA" && (
                          <button type="button" className="vd-comando-testo" disabled={riprova.isPending} onClick={() => riprova.mutate(c.id)}>Riprova la consegna</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {riprova.isError && <p className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>{motivoErrore(riprova.error, "operazione")}</p>}
            {riprova.isSuccess && <p className="vd-stato vd-stato--ok" role="status">Consegna rimessa in coda.</p>}
            <Paginazione cursori={cursori} prossimo={prossimo} righe={righe.length} limite={LIMITE} nome="Consegne" etichetta="Pagine delle consegne"
                         testoPrecedente="Precedenti" testoSuccessiva="Successive" />
          </section>
        )}
    </DialogoLaterale>
  );
}

export default function CanaliWebhookPage() {
  const qc = useQueryClient();
  const [chiave, nuovaChiave] = useChiaveIdempotenza();
  const [url, setUrl] = useState("https://");
  const [tipi, setTipi] = useState<string[]>([]);
  const [descr, setDescr] = useState("");
  const [tentato, setTentato] = useState(false);
  const [segreto, setSegreto] = useState<{ url: string; segreto: string; ruotato?: boolean; fino?: string | null } | null>(null);
  const [consegne, setConsegne] = useState<Webhook | null>(null);
  const [conferma, setConferma] = useState<string | null>(null);
  const [rotazione, setRotazione] = useState<string | null>(null);
  const [ore, setOre] = useState("24");
  const ruota = useMutation({ mutationFn: (id: string) => ruotaSegretoWebhook(id, Number(ore)),
    onSuccess: w => { setRotazione(null); setSegreto({ url: w.url, segreto: w.segreto, ruotato: true, fino: w.transizione_fino_al ?? null }); qc.invalidateQueries({ queryKey: ["webhook"] }); } });
  const ritira = useMutation({ mutationFn: (id: string) => chiudiTransizioneWebhook(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["webhook"] }) });
  const elenco = useQuery({ queryKey: ["webhook"], queryFn: leggiWebhook });
  // Tipi sottoscrivibili dal servizio (catalogo chiuso, cambia solo con un rilascio)
  const catalogo = useQuery({ queryKey: ["webhook-tipi"], queryFn: leggiTipiEvento, staleTime: Infinity });
  const crea = useMutation({
    mutationFn: () => creaWebhook({ url: url.trim(), tipi, descrizione: descr.trim() || undefined }, chiave),
    onSuccess: w => { setSegreto({ url: w.url, segreto: w.segreto }); setUrl("https://"); setTipi([]); setDescr(""); setTentato(false); nuovaChiave(); qc.invalidateQueries({ queryKey: ["webhook"] }); },
  });
  const attiva = useMutation({ mutationFn: (x: { id: string; attiva: boolean }) => modificaWebhook(x.id, { attiva: x.attiva }),
    onSuccess: () => { setConferma(null); qc.invalidateQueries({ queryKey: ["webhook"] }); } });
  const righe = elenco.data?.sottoscrizioni ?? [];
  const urlOk = /^https:\/\/[^\s/]+\.[^\s]+$/.test(url.trim());

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Webhook</h1>
        <p>Il servizio avvisa i tuoi sistemi a ogni evento (versamento conservato, rifiutato, pacchetto pronto…), con una firma da verificare.</p>
      </div>
      <div className="vd-pila">
        {segreto && (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">{segreto.ruotato ? "Segreto ruotato: copia ora quello nuovo" : "Webhook creato: copia ora il segreto"}</p>
            <p>È l'unico momento in cui il segreto si legge. Serve a verificare la firma HMAC di ogni consegna a <span className="vd-mono vd-a-capo">{segreto.url}</span>.</p>
            {segreto.ruotato && <p>Fino a {quandoBreve(segreto.fino) ?? "fine transizione"} ogni consegna porta due firme v1 (nuovo e precedente): aggiorna il tuo sistema, poi ritira il precedente.</p>}
            <code className="vd-chiave" tabIndex={-1} ref={el => el?.focus()}>{segreto.segreto}</code>
            <div><button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => setSegreto(null)}>L'ho copiato</button></div>
          </div>
        )}
        <section aria-labelledby="vd-wh">
          <div className="vd-sezione-testa"><h2 id="vd-wh" className="vd-etichetta">Sottoscrizioni</h2></div>
          {elenco.isLoading ? <InLettura testo="Lettura dei webhook…" />
            : elenco.isError ? <EsitoCritico titolo="Webhook non disponibili" errore={elenco.error} spiegazione="Non significa che non ce ne siano." onRiprova={() => elenco.refetch()} />
            : righe.length === 0 ? <Vuoto titolo="Nessun webhook"><p>Senza webhook i tuoi sistemi leggono gli eventi con GET /api/v1/eventi.</p></Vuoto>
            : (
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Webhook dell'ente</caption>
                  <thead><tr><th scope="col">Dal</th><th scope="col">Indirizzo</th><th scope="col">Eventi</th><th scope="col">Stato</th><th scope="col">Comandi</th></tr></thead>
                  <tbody>
                    {righe.map(w => {
                      const q = quando(w.creata_il);
                      return (
                        <tr key={w.id}>
                          <td className="vd-ora" data-label="Dal">{q?.giorno ?? "—"}</td>
                          <td data-label="Indirizzo" className="vd-a-capo"><span className="vd-mono">{w.url}</span>{w.descrizione && <span className="vd-sotto">{w.descrizione}</span>}<span className="vd-sotto">segreto …{w.segreto_suffisso}{w.segreto_ruotato_il ? `, ruotato ${quandoBreve(w.segreto_ruotato_il)}` : ""}</span>
                            {w.segreto_precedente_suffisso && w.transizione_fino_al && <span className="vd-sotto vd-stato--rischio">doppia firma: il precedente …{w.segreto_precedente_suffisso} vale fino a {quandoBreve(w.transizione_fino_al)}</span>}</td>
                          <td data-label="Eventi" className="vd-cella-larga">{w.tipi.join(", ")}</td>
                          <td data-label="Stato"><span className={`vd-stato ${w.attiva ? "vd-stato--ok" : "vd-stato--neutro"}`}>{w.attiva ? "Attivo" : "Disattivato"}</span><span className="vd-sotto">aggiornato {quandoBreve(w.aggiornata_il)}</span></td>
                          <td>
                            {rotazione === w.id ? (
                              <span className="vd-conferma-riga">
                                <label htmlFor={`vd-wh-ore-${w.id}`} className="vd-forte">Il precedente vale ancora per</label>
                                <select id={`vd-wh-ore-${w.id}`} className="vd-input" value={ore} onChange={e => setOre(e.target.value)}>
                                  {[1, 6, 24, 72, 168].map(h => <option key={h} value={h}>{h === 1 ? "1 ora" : h < 48 ? `${h} ore` : `${h / 24} giorni`}</option>)}
                                </select>
                                <span className="vd-comandi-cella">
                                  <button type="button" className="vd-bottone vd-bottone--secondario" disabled={ruota.isPending} onClick={() => ruota.mutate(w.id)}>Ruota il segreto</button>
                                  <button type="button" className="vd-comando-testo" onClick={() => setRotazione(null)}>Annulla</button>
                                </span>
                                {ruota.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>{motivoErrore(ruota.error, "operazione")}</span>}
                              </span>
                            ) : conferma === w.id ? (
                              <span className="vd-comandi-cella">
                                <span className="vd-stato vd-stato--critico">{w.attiva ? "Disattivare? Gli eventi non saranno consegnati." : "Riattivare?"}</span>
                                <button type="button" className="vd-bottone vd-bottone--secondario" disabled={attiva.isPending} onClick={() => attiva.mutate({ id: w.id, attiva: !w.attiva })}>{w.attiva ? "Disattiva" : "Riattiva"}</button>
                                <button type="button" className="vd-comando-testo" onClick={() => setConferma(null)}>Annulla</button>
                                {attiva.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>{motivoErrore(attiva.error, "operazione")}</span>}
                              </span>
                            ) : (
                              <span className="vd-comandi-cella">
                                <button type="button" className="vd-comando-testo" aria-haspopup="dialog" onClick={() => setConsegne(w)}>Consegne</button>
                                <button type="button" className="vd-comando-testo" onClick={() => setConferma(w.id)}>{w.attiva ? "Disattiva" : "Riattiva"}</button>
                                <button type="button" className="vd-comando-testo" onClick={() => { setRotazione(w.id); setConferma(null); }}>Ruota il segreto</button>
                                {w.segreto_precedente_suffisso && <button type="button" className="vd-comando-testo" disabled={ritira.isPending} onClick={() => ritira.mutate(w.id)}>Ritira il precedente</button>}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </section>

        <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setTentato(true); if (urlOk && tipi.length) crea.mutate(); }}>
          <h2>Nuovo webhook</h2>
          <div className="vd-griglia">
            <div className="vd-campo">
              <label htmlFor="vd-wh-url">Indirizzo https<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
              <input id="vd-wh-url" className="vd-input vd-mono" type="url" value={url} spellCheck={false}
                     aria-invalid={tentato && !urlOk ? true : undefined} onChange={e => setUrl(e.target.value)} />
              {tentato && !urlOk && <span className="vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Serve un indirizzo https completo.</span>}
            </div>
            <div className="vd-campo">
              <label htmlFor="vd-wh-descr">Descrizione</label>
              <input id="vd-wh-descr" className="vd-input" value={descr} maxLength={200} onChange={e => setDescr(e.target.value)} />
            </div>
          </div>
          <fieldset className="vd-caselle">
            <legend>Eventi da ricevere<span aria-hidden="true"> *</span><span className="vd-sr"> (almeno uno)</span></legend>
            {catalogo.isLoading ? <InLettura testo="Lettura dei tipi di evento…" />
              : catalogo.isError ? <EsitoCritico titolo="Tipi di evento non disponibili" errore={catalogo.error} onRiprova={() => catalogo.refetch()} />
              : (
                <div className="vd-caselle-elenco">
                  {(catalogo.data?.tipi ?? []).map(({ tipo: t, descrizione }) => (
                    <label key={t} className="vd-casella">
                      <input type="checkbox" checked={tipi.includes(t)} onChange={e => setTipi(x => e.target.checked ? [...x, t] : x.filter(y => y !== t))} />
                      <span>{ETICHETTA_EVENTO[t] ?? descrizione}<span className="vd-nota vd-mono" style={{ display: "block" }}>{t}</span></span>
                    </label>
                  ))}
                </div>
              )}
            {tentato && !tipi.length && <span className="vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Scegli almeno un evento.</span>}
          </fieldset>
          {crea.isError && <EsitoCritico titolo="Webhook non creato" errore={crea.error} azione="operazione" />}
          <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={crea.isPending}>{crea.isPending ? "Creazione…" : "Crea il webhook"}</button></div>
        </form>
      </div>
      {consegne && <Consegne w={consegne} onClose={() => setConsegne(null)} />}
    </>
  );
}
