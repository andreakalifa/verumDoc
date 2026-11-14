// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Proposte del conservatore agli enti (REGOLE §8.13; contratto Q1 GET /proposte,
 * POST /proposte/{id}/accetta|rifiuta con motivo). Sistema «Partenze».
 * Il conservatore vede lo stato di ogni proposta per ente; l'amministrazione
 * dell'ente decide le sue (il servizio dice se e perché un'azione non è consentita).
 * Accetta e Rifiuta sono conferme in riga con motivo.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { motivoErrore } from "../api/errori";
import { leggiProposte, rispondiProposta, type Proposta } from "../console/api";
import { breve, InLettura, motivoValido, MOTIVO_MIN, quando, quandoBreve, valoreInParole, Vuoto } from "../console/comuni";
import { parola, STATO_PROPOSTA } from "../console/parole";

const LIMITE = 30;

function Risposta({ p, accetta, onFine }: { p: Proposta; accetta: boolean; onFine: () => void }) {
  const qc = useQueryClient();
  const [motivo, setMotivo] = useState("");
  const invia = useMutation({
    mutationFn: () => rispondiProposta(p.proposta_id, accetta, motivo.trim()),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["conf-proposte"] }); onFine(); },
  });
  const id = `vd-prop-${p.proposta_id}`;
  return (
    <div className="vd-conferma-riga" role="group" aria-label={accetta ? "Accetta la proposta" : "Rifiuta la proposta"}>
      <label htmlFor={id} className="vd-nota">
        {accetta ? "Perché la accetti?" : "Perché la rifiuti?"} (almeno {MOTIVO_MIN} caratteri)
      </label>
      <textarea id={id} className="vd-input vd-textarea" value={motivo} maxLength={2000} aria-required="true"
                onChange={e => setMotivo(e.target.value)} autoFocus />
      {accetta && <p className="vd-nota">Il valore vale per l'ente da subito, al livello dell'ente.</p>}
      {invia.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>{motivoErrore(invia.error, "operazione")}</span>}
      <div className="vd-comandi-cella">
        <button type="button" className="vd-bottone vd-bottone--secondario" disabled={!motivoValido(motivo) || invia.isPending}
                onClick={() => invia.mutate()}>
          {invia.isPending ? "Invio…" : accetta ? "Accetta" : "Rifiuta"}
        </button>
        <button type="button" className="vd-comando-testo" onClick={onFine} disabled={invia.isPending}>Annulla</button>
      </div>
    </div>
  );
}

export default function ConfPropostePage() {
  const ente = useEnte();
  const [stato, setStato] = useState("PROPOSTA");
  const [perEnte, setPerEnte] = useState(false);
  const [aperta, setAperta] = useState<{ id: string; accetta: boolean } | null>(null);
  const cursori = useCursori();
  const filtri = { stato: stato || undefined, ente: ente.globale && perEnte ? ente.tenantId || undefined : undefined };
  const elenco = useQuery({ queryKey: ["conf-proposte", filtri, cursori.cursore], queryFn: () => leggiProposte(filtri, cursori.cursore, LIMITE) });
  const righe = elenco.data?.items ?? [];

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Proposte</h1>
        <p>{ente.globale
          ? "Valori che il conservatore ha proposto agli enti e le loro risposte."
          : "Valori che il conservatore propone per il tuo ente: accettali o rifiutali con un motivo."}</p>
      </div>
      <section aria-labelledby="vd-prop">
        <div className="vd-sezione-testa"><h2 id="vd-prop" className="vd-etichetta">Proposte</h2><span className="vd-nota">dalla più recente</span></div>
        <div className="vd-filtri">
          <div className="vd-campo">
            <label htmlFor="vd-prop-stato">Stato</label>
            <select id="vd-prop-stato" className="vd-input" value={stato} onChange={e => { setStato(e.target.value); cursori.ricomincia(); }}>
              <option value="PROPOSTA">Da decidere</option><option value="ACCETTATA">Accettate</option>
              <option value="RIFIUTATA">Rifiutate</option><option value="">Tutte</option>
            </select>
          </div>
          {ente.globale && (
            <>
              <label className="vd-casella" style={{ flex: "0 1 auto" }}>
                <input type="checkbox" checked={perEnte} onChange={e => { setPerEnte(e.target.checked); cursori.ricomincia(); }} />
                Solo un ente
              </label>
              {perEnte && <SceltaEnte id="vd-prop-ente" stato={ente} onCambia={cursori.ricomincia} />}
            </>
          )}
        </div>
        {elenco.isLoading ? <InLettura testo="Lettura delle proposte…" />
          : elenco.isError ? (
            <EsitoCritico titolo="Proposte non disponibili" errore={elenco.error}
                          spiegazione="Non significa che non ci siano proposte." onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
          ) : righe.length === 0 ? (
            <Vuoto titolo={stato === "PROPOSTA" ? "Nessuna proposta da decidere" : "Nessuna proposta con questi filtri"} />
          ) : (
            <>
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila" aria-busy={elenco.isFetching}>
                  <caption className="vd-sr">Proposte di configurazione</caption>
                  <thead><tr><th scope="col">Ora</th><th scope="col">Voce e valore proposto</th><th scope="col">Motivo</th><th scope="col">Stato</th><th scope="col">Comandi</th></tr></thead>
                  <tbody>
                    {righe.map(p => {
                      const q = quando(p.created_at);
                      const st = parola(STATO_PROPOSTA, p.stato);
                      return (
                        <tr key={p.proposta_id}>
                          <td className="vd-ora" data-label="Ora">{q?.ora ?? "—"}<span className="vd-sotto vd-sotto-normale">{q?.giorno}</span></td>
                          <td data-label="Voce e valore proposto" className="vd-cella-valore">
                            {p.descrizione ?? p.codice}
                            <span className="vd-sotto"><span className="vd-mono">{p.codice}</span> → <span className="vd-dopo">{valoreInParole(p.valore)}</span></span>
                            {ente.globale && <span className="vd-sotto">ente <span className="vd-mono" title={p.ente}>{breve(p.ente)}</span></span>}
                          </td>
                          <td className="vd-cella-larga vd-a-capo" data-label="Motivo">{p.motivo}</td>
                          <td data-label="Stato" style={{ whiteSpace: "normal" }}>
                            <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
                            {p.risposto_il && <span className="vd-sotto">il {quandoBreve(p.risposto_il)}</span>}
                            {p.motivo_risposta && <span className="vd-sotto vd-a-capo">{p.motivo_risposta}</span>}
                          </td>
                          <td>
                            {aperta?.id === p.proposta_id ? (
                              <Risposta p={p} accetta={aperta.accetta} onFine={() => setAperta(null)} />
                            ) : p.azioni_consentite.accetta || p.azioni_consentite.rifiuta ? (
                              <div className="vd-comandi-cella">
                                {p.azioni_consentite.accetta && <button type="button" className="vd-comando-testo" onClick={() => setAperta({ id: p.proposta_id, accetta: true })}>Accetta</button>}
                                {p.azioni_consentite.rifiuta && <button type="button" className="vd-comando-testo" onClick={() => setAperta({ id: p.proposta_id, accetta: false })}>Rifiuta</button>}
                              </div>
                            ) : p.stato === "PROPOSTA" && p.azioni_consentite.motivo_blocco ? (
                              <span className="vd-nota">{p.azioni_consentite.motivo_blocco.replace(/^./, c => c.toUpperCase())}</span>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Paginazione cursori={cursori} prossimo={prossimoDi(elenco.data)} righe={righe.length} limite={LIMITE}
                           nome="Proposte" etichetta="Pagine delle proposte" />
            </>
          )}
      </section>
    </>
  );
}
