// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Bozze di configurazione — elenco a cursore (contratto Q1, GET /configurazione/bozze).
 * Ogni bozza si apre nella sua pagina: anteprima dell'impatto e applicazione.
 * Filtri lato servizio: stato ed ente (solo per il conservatore).
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { leggiBozze } from "../console/api";
import { InLettura, quando, Vuoto } from "../console/comuni";
import { nomeLivello, parola, RISORSA, STATO_BOZZA } from "../console/parole";

const LIMITE = 30;

export default function ConfBozzePage() {
  const ente = useEnte();
  const [stato, setStato] = useState("APERTA");
  const [tuttiEnti, setTuttiEnti] = useState(true);
  const cursori = useCursori();
  const filtri = { stato: stato || undefined, ente: ente.globale && !tuttiEnti ? ente.tenantId || undefined : undefined };
  const elenco = useQuery({ queryKey: ["conf-bozze", filtri, cursori.cursore], queryFn: () => leggiBozze(filtri, cursori.cursore, LIMITE) });
  const righe = elenco.data?.items ?? [];

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Bozze</h1>
        <p>Ogni modifica della configurazione passa da qui: si controlla l'impatto, poi si applica. Nulla cambia prima.</p>
        <div className="vd-testa-comandi">
          <Link to="/configurazione" className="vd-bottone vd-bottone--secondario" style={{ textDecoration: "none" }}>Nuova modifica</Link>
        </div>
      </div>
      <section aria-labelledby="vd-bozze">
        <div className="vd-sezione-testa">
          <h2 id="vd-bozze" className="vd-etichetta">Bozze</h2>
          <span className="vd-nota">dalla più recente</span>
        </div>
        <div className="vd-filtri">
          <div className="vd-campo">
            <label htmlFor="vd-bozze-stato">Stato</label>
            <select id="vd-bozze-stato" className="vd-input" value={stato} onChange={e => { setStato(e.target.value); cursori.ricomincia(); }}>
              <option value="APERTA">Aperte</option><option value="PROGRAMMATA">Programmate</option><option value="APPLICATA">Applicate</option>
              <option value="ANNULLATA">Annullate</option><option value="">Tutte</option>
            </select>
          </div>
          {ente.globale && (
            <>
              <label className="vd-casella" style={{ flex: "0 1 auto" }}>
                <input type="checkbox" checked={tuttiEnti} onChange={e => { setTuttiEnti(e.target.checked); cursori.ricomincia(); }} />
                Tutti gli enti e il livello del conservatore
              </label>
              {!tuttiEnti && <SceltaEnte id="vd-bozze-ente" stato={ente} onCambia={cursori.ricomincia} />}
            </>
          )}
        </div>
        {elenco.isLoading ? <InLettura testo="Lettura delle bozze…" />
          : elenco.isError ? (
            <EsitoCritico titolo="Bozze non disponibili" errore={elenco.error}
                          spiegazione="Non significa che non ci siano bozze." onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
          ) : righe.length === 0 ? (
            <Vuoto titolo={stato === "APERTA" ? "Nessuna bozza aperta" : "Nessuna bozza con questi filtri"}>
              <p>Una bozza nasce dalla pagina <Link to="/configurazione" className="vd-riga-link">Impostazioni e regole</Link> con «Modifica».</p>
            </Vuoto>
          ) : (
            <>
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila" aria-busy={elenco.isFetching}>
                  <caption className="vd-sr">Bozze di configurazione</caption>
                  <thead><tr><th scope="col">Ora</th><th scope="col">Bozza</th><th scope="col">Ambito</th><th scope="col">Motivo</th><th scope="col">Stato</th></tr></thead>
                  <tbody>
                    {righe.map(b => {
                      const q = quando(b.updated_at);
                      const st = parola(STATO_BOZZA, b.stato);
                      const n = b.modifiche?.length ?? 0;
                      return (
                        <tr key={b.bozza_id}>
                          <td className="vd-ora" data-label="Ora">{q?.ora ?? "—"}<span className="vd-sotto vd-sotto-normale">{q?.giorno}</span></td>
                          <td data-label="Bozza">
                            <Link className="vd-riga-link" to={`/configurazione/bozze/${b.bozza_id}`}>{RISORSA[b.risorsa] ?? b.risorsa}</Link>
                            <span className="vd-sotto">{n} {n === 1 ? "modifica" : "modifiche"}{b.proposta ? " · proposta agli enti" : ""}</span>
                          </td>
                          <td data-label="Ambito">
                            {nomeLivello(b.ambito.livello).replace(/^./, c => c.toUpperCase())}
                            <span className="vd-sotto vd-a-capo">{[b.ambito.ente && `ente ${b.ambito.ente.slice(0, 8)}`, b.ambito.classe && `classe ${b.ambito.classe.slice(0, 8)}`, b.ambito.accordo && `accordo ${b.ambito.accordo}`].filter(Boolean).join(" · ")}</span>
                          </td>
                          <td className="vd-cella-larga vd-a-capo" data-label="Motivo">{b.motivo}</td>
                          <td data-label="Stato"><span className={`vd-stato ${st.classe}`}>{st.testo}</span><span className="vd-sotto">versione {b.versione}</span></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Paginazione cursori={cursori} prossimo={prossimoDi(elenco.data)} righe={righe.length} limite={LIMITE}
                           nome="Bozze" etichetta="Pagine delle bozze" />
            </>
          )}
      </section>
    </>
  );
}
