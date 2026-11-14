// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Registro e verifiche — monitor della catena per il conservatore (R04/R05), sistema «Partenze».
 *
 * - Fascia di colonna con lo stato dell'ULTIMO checkpoint: sigillo e controfirma del
 *   testimone (dashboard-api GET /dashboard/hashchain → `registro`). La fascia è gialla
 *   solo con checkpoint sigillato E controfirmato; rifiuto del testimone o stato
 *   illeggibile: critica; in attesa o testimone non configurato: neutra (regola della
 *   verità di stato). L'età dell'attesa si mostra; la soglia d'allarme è del servizio.
 * - R06 (VD-C-129): allarmi del testimone e blocchi del registro dall'API
 *   GET /api/v1/hashchain/registro/allarmi (a cursore), con le condizioni del momento
 *   e la soglia CATENA_CONTROFIRMA_MAX_SECONDI.
 * - Allarmi del testimone e blocchi del registro: NON esposti da un'API (solo metriche,
 *   log e riga di comando): la pagina lo dichiara con «Che cosa manca».
 * - Verifica continua: riepilogo (copertura, anomalie aperte, ritardo, lavoratore) e
 *   registro delle verifiche a cursore, filtrato lato servizio (esito, ente).
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { leggiAllarmiRegistro, leggiRegistroVerifiche, leggiRiepilogoVerifiche, leggiStatoCatena } from "../console/api";
import { breve, InLettura, num, quando, quandoBreve, Vuoto } from "../console/comuni";
import { ESITO_VERIFICA, parola, STATO_SIGILLO, STATO_TESTIMONE } from "../console/parole";

const LIMITE = 30;
const LIMITE_ALLARMI = 20;

const durata = (secondi: number) => {
  if (secondi < 90) return `${Math.round(secondi)} secondi`;
  if (secondi < 5400) return `${Math.round(secondi / 60)} minuti`;
  if (secondi < 172800) return `${Math.round(secondi / 3600)} ore`;
  return `${Math.round(secondi / 86400)} giorni`;
};

export default function RegistroPage() {
  const ente = useEnte();
  const [perEnte, setPerEnte] = useState(false);
  const [esito, setEsito] = useState("ANOMALIE");
  const cursori = useCursori();
  const filtroEnte = perEnte ? ente.tenantId || undefined : undefined;

  const catena = useQuery({ queryKey: ["registro-catena"], queryFn: leggiStatoCatena, refetchInterval: 30_000 });
  const riepilogo = useQuery({ queryKey: ["verifiche-riepilogo", filtroEnte], queryFn: () => leggiRiepilogoVerifiche(filtroEnte), refetchInterval: 60_000 });
  const registro = useQuery({
    queryKey: ["verifiche-registro", filtroEnte, esito, cursori.cursore],
    queryFn: () => leggiRegistroVerifiche({ ente: filtroEnte, esito: esito || undefined }, cursori.cursore, LIMITE),
  });

  const [soloAperti, setSoloAperti] = useState(true);
  const cursoriAllarmi = useCursori();
  const allarmi = useQuery({
    queryKey: ["registro-allarmi", soloAperti, cursoriAllarmi.cursore],
    queryFn: () => leggiAllarmiRegistro(soloAperti, cursoriAllarmi.cursore, LIMITE_ALLARMI),
    refetchInterval: 60_000,
  });
  const condizioniAttive = (allarmi.data?.condizioni ?? []).filter(c => c.attiva);
  const sogliaControfirma = allarmi.data?.soglie?.controfirma_max_secondi;

  const reg = catena.data?.registro;
  const cp = reg?.ultimo_checkpoint ?? null;
  const testimone = parola(STATO_TESTIMONE, cp?.stato_testimone);
  const sigillo = parola(STATO_SIGILLO, cp?.stato_sigillo);
  const attesa = cp?.emesso_il && cp.stato_testimone === "IN_ATTESA" ? (Date.now() - new Date(cp.emesso_il).getTime()) / 1000 : null;
  const tono = catena.isError || (catena.isSuccess && !reg) ? "critico"
    : !cp ? "neutro"
    : cp.stato_testimone === "RIFIUTATO" ? "critico"
    : cp.stato_testimone === "CONTROFIRMATO" && cp.stato_sigillo === "FIRMATO" ? "giallo" : "neutro";
  const titolo = catena.isLoading ? "Lettura del registro…"
    : catena.isError || !reg ? "Stato del registro non disponibile"
    : !cp ? "Nessun checkpoint emesso"
    : cp.stato_testimone === "RIFIUTATO" ? "Checkpoint rifiutato dal testimone"
    : cp.stato_testimone === "CONTROFIRMATO" ? (cp.stato_sigillo === "FIRMATO" ? "Registro controfirmato" : "Controfirmato, senza sigillo qualificato")
    : cp.stato_testimone === "IN_ATTESA" ? "In attesa della controfirma"
    : testimone.testo;
  const riga = catena.isError ? "Non significa che il registro sia integro: lo stato non si è potuto leggere."
    : cp ? `Checkpoint a ${num(cp.dimensione)} foglie, emesso ${quandoBreve(cp.emesso_il) ?? "in data non indicata"}${attesa != null ? `; in attesa da ${durata(attesa)} (allarme oltre ${sogliaControfirma != null ? durata(sogliaControfirma) : "il limite configurato"})` : ""}.`
    : reg ? `Il registro ha ${num(reg.dimensione)} foglie e nessun checkpoint.` : "";

  const r = riepilogo.data;
  const voci = registro.data?.voci ?? [];
  const prossimo = registro.data?.cursore_successivo ?? null;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Registro e verifiche</h1>
        <p>Il registro delle impronte, la controfirma del testimone e la rilettura continua dei pacchetti dallo storage.</p>
        <div className="vd-testa-comandi">
          <Link to="/hashchain" className="vd-comando-testo">Catena delle impronte</Link>
        </div>
      </div>

      <section className={`vd-fascia vd-fascia--${tono}`} aria-labelledby="vd-reg-stato" aria-live="polite">
        <h2 id="vd-reg-stato">{titolo}</h2>
        <span className="vd-fascia-riga">{riga}</span>
        <div className="vd-fascia-comandi">
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => { catena.refetch(); riepilogo.refetch(); registro.refetch(); }}>
            Rileggi lo stato
          </button>
        </div>
      </section>

      <div className="vd-pila">
        {catena.isError && <EsitoCritico titolo="Registro non disponibile" errore={catena.error} onRiprova={() => catena.refetch()} inRiprova={catena.isFetching} />}
        {reg && (
          <section aria-labelledby="vd-reg-cp">
            <div className="vd-sezione-testa"><h2 id="vd-reg-cp" className="vd-etichetta">Ultimo checkpoint</h2></div>
            {cp ? (
              <dl className="vd-dati">
                <dt>Foglie nel registro</dt><dd><span className="vd-numero">{num(reg.dimensione)}</span></dd>
                <dt>Coperte dal checkpoint</dt><dd><span className="vd-numero">{num(cp.dimensione)}</span>{reg.dimensione > cp.dimensione && <span className="vd-nota"> · {num(reg.dimensione - cp.dimensione)} in attesa del prossimo</span>}</dd>
                <dt>Radice</dt><dd className="vd-mono">{cp.radice}</dd>
                <dt>Sigillo</dt><dd><span className={`vd-stato ${sigillo.classe}`}>{sigillo.testo}</span>{cp.motivo_sigillo && <span className="vd-nota"> · {cp.motivo_sigillo}</span>}</dd>
                <dt>Testimone</dt>
                <dd>
                  <span className={`vd-stato ${testimone.classe}`}>{testimone.testo}</span>
                  {cp.controfirmato_il && <span className="vd-nota"> · {quandoBreve(cp.controfirmato_il)}</span>}
                  {cp.esito_testimone && <span className="vd-nota" style={{ display: "block" }}>{cp.esito_testimone}</span>}
                </dd>
                <dt>Ultima dimensione controfirmata</dt><dd><span className="vd-numero">{num(reg.testimone_dimensione)}</span></dd>
              </dl>
            ) : <Vuoto titolo="Nessun checkpoint emesso"><p>Il primo checkpoint si emette dopo il primo pacchetto entrato nel registro.</p></Vuoto>}
          </section>
        )}

        <section aria-labelledby="vd-reg-allarmi">
          <div className="vd-sezione-testa">
            <h2 id="vd-reg-allarmi" className="vd-etichetta">Allarmi del registro e del testimone</h2>
            <label className="vd-nota">
              <input type="checkbox" checked={!soloAperti} onChange={e => { setSoloAperti(!e.target.checked); cursoriAllarmi.ricomincia(); }} />{" "}
              anche quelli già gestiti
            </label>
          </div>
          {allarmi.isLoading ? <InLettura testo="Lettura degli allarmi…" />
            : allarmi.isError ? <EsitoCritico titolo="Allarmi non disponibili" errore={allarmi.error}
                                              spiegazione="Non significa che non ci siano allarmi." onRiprova={() => allarmi.refetch()} />
            : (
              <>
                {condizioniAttive.length > 0 && (
                  <ul className="vd-elenco" aria-label="Condizioni d'allarme in corso">
                    {condizioniAttive.map(c => (
                      <li key={c.codice}><strong className="vd-stato--critico">{c.descrizione}</strong>
                        {typeof c.dettagli?.attesa_secondi === "number" && <span className="vd-nota"> · in attesa da {durata(c.dettagli.attesa_secondi as number)}</span>}
                      </li>
                    ))}
                  </ul>
                )}
                {(allarmi.data?.items ?? []).length === 0 ? (
                  condizioniAttive.length === 0 && <div className="vd-esito vd-esito--ok"><p className="vd-esito-titolo">{soloAperti ? "Nessun allarme aperto" : "Nessun allarme registrato"}</p></div>
                ) : (
                  <div className="vd-scorri">
                    <table className="vd-tabellone vd-impila">
                      <caption className="vd-sr">Allarmi del registro e del testimone, dal più recente</caption>
                      <thead><tr><th scope="col">Aperto</th><th scope="col">Origine</th><th scope="col">Causa</th><th scope="col">Occorrenze</th><th scope="col">Stato</th></tr></thead>
                      <tbody>
                        {(allarmi.data?.items ?? []).map(a => (
                          <tr key={a.id}>
                            <td data-label="Aperto">{quandoBreve(a.aperto_il) ?? a.aperto_il}</td>
                            <td data-label="Origine">{a.fonte === "testimone" ? "testimone" : "registro"}</td>
                            <td data-label="Causa"><span className="vd-mono">{a.causa}</span>
                              {typeof a.dettagli?.ultimo_motivo === "string" && <span className="vd-sotto">{a.dettagli.ultimo_motivo as string}</span>}
                            </td>
                            <td data-label="Occorrenze"><span className="vd-numero">{num(a.occorrenze)}</span></td>
                            <td data-label="Stato">
                              {a.aperto ? <span className="vd-stato vd-stato--critico">aperto</span>
                                : <span className="vd-stato">gestito{a.gestito_da ? ` da ${a.gestito_da}` : ""}</span>}
                              {a.gestito_il && <span className="vd-sotto">{quandoBreve(a.gestito_il)}</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <Paginazione cursori={cursoriAllarmi} prossimo={prossimoDi(allarmi.data)} righe={(allarmi.data?.items ?? []).length}
                             limite={LIMITE_ALLARMI} nome="allarmi" etichetta="Pagine degli allarmi" />
                <p className="vd-nota" style={{ marginTop: 8 }}>Gli allarmi si gestiscono da riga di comando (python -m app.registro.operatore).</p>
              </>
            )}
        </section>

        <section aria-labelledby="vd-reg-cont">
          <div className="vd-sezione-testa">
            <h2 id="vd-reg-cont" className="vd-etichetta">Verifica continua</h2>
            <span className="vd-nota">ogni pacchetto riletto dallo storage e confrontato con la sua foglia</span>
          </div>
          {ente.globale && (
            <div className="vd-filtri">
              <label className="vd-casella" style={{ flex: "0 1 auto" }}>
                <input type="checkbox" checked={perEnte} onChange={e => { setPerEnte(e.target.checked); cursori.ricomincia(); }} />
                Solo un ente
              </label>
              {perEnte && <SceltaEnte id="vd-reg-ente" stato={ente} onCambia={cursori.ricomincia} />}
            </div>
          )}
          {riepilogo.isLoading ? <InLettura testo="Lettura del riepilogo…" />
            : riepilogo.isError ? <EsitoCritico titolo="Riepilogo delle verifiche non disponibile" errore={riepilogo.error}
                                                spiegazione="Non significa che non ci siano anomalie." onRiprova={() => riepilogo.refetch()} />
            : r ? (
              <dl className="vd-dati">
                <dt>Copertura del ciclo</dt>
                <dd>
                  <span className="vd-numero">{r.copertura_percentuale == null ? "—" : `${r.copertura_percentuale.toLocaleString("it-IT", { maximumFractionDigits: 1 })}%`}</span>
                  <span className="vd-nota"> · {num(r.pda_verificati_nella_finestra)} pacchetti riletti negli ultimi {r.finestra_giorni} giorni su {num(r.pda_programmati)} (ciclo di {r.ciclo_giorni} giorni)</span>
                </dd>
                <dt>Mai riletti</dt><dd><span className="vd-numero">{num(r.pda_mai_verificati)}</span></dd>
                <dt>Anomalie aperte</dt>
                <dd>
                  <span className={`vd-numero${r.anomalie_aperte.numero ? " vd-stato--critico" : " vd-stato--ok"}`}>{r.anomalie_aperte.oltre_tetto ? `oltre ${num(r.anomalie_aperte.numero)}` : num(r.anomalie_aperte.numero)}</span>
                </dd>
                {r.ritardo_rotazione_secondi != null && <><dt>Ritardo della rotazione</dt><dd>{durata(r.ritardo_rotazione_secondi)}</dd></>}
                <dt>Ultima rilettura</dt><dd>{quandoBreve(r.ultima_verifica_il) ?? "mai"}</dd>
                <dt>Lavoratore</dt>
                <dd>
                  ultimo giro {quandoBreve(r.lavoratore.ultimo_giro_il) ?? "mai"}
                  {r.lavoratore.confronto_foglia === "NON_DISPONIBILE"
                    ? <span className="vd-stato vd-stato--rischio" style={{ display: "block", whiteSpace: "normal" }}>Confronto con le foglie del registro non eseguito: si controllano solo le impronte.</span>
                    : <span className="vd-nota" style={{ display: "block" }}>confronto con le foglie: {r.lavoratore.confronto_foglia.toLowerCase()}</span>}
                </dd>
                {r.copertura_calcolata_il && <><dt>Calcolato</dt><dd>{quandoBreve(r.copertura_calcolata_il)}</dd></>}
              </dl>
            ) : null}
        </section>

        <section aria-labelledby="vd-reg-reg">
          <div className="vd-sezione-testa">
            <h2 id="vd-reg-reg" className="vd-etichetta">Registro delle verifiche</h2>
            <span className="vd-nota">dalla più recente</span>
          </div>
          <div className="vd-filtri">
            <div className="vd-campo">
              <label htmlFor="vd-reg-esito">Esito</label>
              <select id="vd-reg-esito" className="vd-input" value={esito} onChange={e => { setEsito(e.target.value); cursori.ricomincia(); }}>
                <option value="ANOMALIE">Solo anomalie</option>
                {Object.entries(ESITO_VERIFICA).map(([k, v]) => <option key={k} value={k}>{v.testo}</option>)}
                <option value="">Tutti</option>
              </select>
            </div>
          </div>
          {registro.isLoading ? <InLettura testo="Lettura del registro delle verifiche…" />
            : registro.isError ? <EsitoCritico titolo="Registro delle verifiche non disponibile" errore={registro.error}
                                               spiegazione="Non significa che non ci siano anomalie." onRiprova={() => registro.refetch()} />
            : voci.length === 0 ? (
              <div className={`vd-esito${esito === "ANOMALIE" ? " vd-esito--ok" : ""}`}>
                <p className="vd-esito-titolo">{esito === "ANOMALIE" ? "Nessuna anomalia registrata" : "Nessuna verifica con questo esito"}</p>
              </div>
            ) : (
              <>
                <div className="vd-scorri">
                  <table className="vd-tabellone vd-impila" aria-busy={registro.isFetching}>
                    <caption className="vd-sr">Verifiche d'integrità, dalla più recente</caption>
                    <thead><tr><th scope="col">Ora</th><th scope="col">Pacchetto</th><th scope="col">Componente</th><th scope="col">Esito</th></tr></thead>
                    <tbody>
                      {voci.map((v, k) => {
                        const q = quando(v.verificato_il);
                        const es = parola(ESITO_VERIFICA, v.esito);
                        return (
                          <tr key={`${v.pda_id}-${v.verificato_il}-${k}`}>
                            <td className="vd-ora" data-label="Ora">{q?.ora ?? "—"}<span className="vd-sotto vd-sotto-normale">{q?.giorno}</span></td>
                            <td data-label="Pacchetto" className="vd-a-capo">
                              <span className="vd-mono">{v.pda ?? v.pda_id}</span>
                              {ente.globale && <span className="vd-sotto">ente <span className="vd-mono" title={v.ente}>{breve(v.ente)}</span></span>}
                            </td>
                            <td data-label="Componente">{v.componente}{v.documento_id && <span className="vd-sotto vd-mono">{v.documento_id}</span>}</td>
                            <td data-label="Esito" style={{ whiteSpace: "normal" }}>
                              <span className={`vd-stato ${es.classe}`}>{es.testo}</span>
                              {v.motivo && <span className="vd-sotto vd-a-capo">{v.motivo}</span>}
                              {v.tentativo > 1 && <span className="vd-sotto">tentativo {v.tentativo}</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <Paginazione cursori={cursori} prossimo={prossimo} righe={voci.length} limite={LIMITE}
                             nome="Verifiche" etichetta="Pagine del registro delle verifiche" />
              </>
            )}
        </section>
      </div>
    </>
  );
}
