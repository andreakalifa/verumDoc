// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Notifiche — sistema grafico «Partenze» (VD-DR-01).
 * Messaggi del servizio all'ente. Un errore del servizio si mostra come errore
 * (503 con id di correlazione), mai come «nessuna notifica».
 * g3-frontend (g2-dati §6.8, be-conformita VD-C-024): gli stati sono quelli della
 * coda di uscita del servizio notification — IN_CODA, IN_INVIO, INVIATA,
 * NON_PARTITA — con numero di tentativi, errore dell'ultimo tentativo, prossimo
 * tentativo e ora d'invio. Le righe delle esibizioni (PDD_*) sono registrazioni,
 * non messaggi spediti.
 * R04 (VD-C-102): elenco a cursore, senza totale.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchNotifiche } from "../api/client";
import { SceltaEnte, useEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";

const TIPO_LABELS: Record<string, string> = {
  "pdv.invalid":       "Versamento rifiutato",
  "aip.conserved":     "Pacchetto conservato",
  "notification.send": "Messaggio del servizio",
  pdv_invalid:         "Versamento rifiutato",
  aip_conserved:       "Pacchetto conservato",
  PDD_COMPLETO:        "Copia completa richiesta",
  PDD_ESTRATTO:        "Estratto richiesto",
  PDD_ESIBIZIONE:      "Esibizione richiesta",
  PDD_MIGRAZIONE:      "Export per cambio di conservatore",
};

/** Stati della coda di uscita (services/notification/app/services/coda.py). */
const STATO_MAP: Record<string, { testo: string; classe: string; spiegazione: string }> = {
  IN_CODA:     { testo: "In coda", classe: "vd-stato--neutro", spiegazione: "in attesa del prossimo invio" },
  IN_INVIO:    { testo: "In invio", classe: "vd-stato--info", spiegazione: "il servizio la sta spedendo" },
  INVIATA:     { testo: "Inviata", classe: "vd-stato--ok", spiegazione: "accettata dal server di posta" },
  NON_PARTITA: { testo: "Non partita", classe: "vd-stato--critico", spiegazione: "non è stata spedita e non verrà ritentata" },
  // Righe delle esibizioni (registrazioni del servizio di distribuzione)
  INVIATO:     { testo: "Registrata", classe: "vd-stato--neutro", spiegazione: "registrazione di un'esibizione, non un messaggio" },
  PENDING:     { testo: "In attesa di approvazione", classe: "vd-stato--info", spiegazione: "registrazione di un'esibizione" },
  APPROVATA:   { testo: "Approvata", classe: "vd-stato--ok", spiegazione: "registrazione di un'esibizione" },
  RIFIUTATA:   { testo: "Rifiutata", classe: "vd-stato--critico", spiegazione: "registrazione di un'esibizione" },
};

const CANALE_LABELS: Record<string, string> = {
  email:      "Posta elettronica",
  email_smtp: "Posta elettronica",
  pec:        "PEC",
  webhook:    "Webhook",
  WEB:        "Portale (registrazione)",
  API:        "API (registrazione)",
};

const FILTRI: { value: string; label: string; vale: (n: any) => boolean }[] = [
  { value: "", label: "Tutte", vale: () => true },
  { value: "non_partite", label: "Non partite", vale: n => n.stato === "NON_PARTITA" },
  { value: "attesa", label: "In coda o in invio", vale: n => n.stato === "IN_CODA" || n.stato === "IN_INVIO" },
  { value: "rifiuti", label: "Versamenti rifiutati", vale: n => n.tipo === "pdv.invalid" || n.tipo === "pdv_invalid" },
  { value: "conservati", label: "Pacchetti conservati", vale: n => n.tipo === "aip.conserved" || n.tipo === "aip_conserved" },
  { value: "pdd", label: "Esibizioni e copie", vale: n => String(n.tipo ?? "").startsWith("PDD_") },
];

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

/** Oggetto leggibile: campo «oggetto» oppure dal contenuto registrato (motivo, oggetto). */
function oggettoDi(n: any): string | null {
  if (n?.oggetto) return String(n.oggetto);
  let p = n?.payload;
  if (typeof p === "string") { try { p = JSON.parse(p); } catch { return null; } }
  if (p && typeof p === "object") return p.oggetto ?? p.motivo ?? null;
  return null;
}

export default function NotifichePage() {
  const ente = useEnte();
  const tenantId = ente.tenantId;
  const [filtro,  setFiltro]  = useState("");
  const cursori = useCursori();
  const limit = 20;

  const notifiche = useQuery({
    queryKey: ["notifiche", tenantId, cursori.cursore],
    queryFn:  () => fetchNotifiche(tenantId, cursori.cursore, limit),
    enabled:  !!tenantId,
  });

  const items: any[] = notifiche.data?.items ?? [];
  const prossimo = prossimoDi(notifiche.data);

  // Filtro lato client: agisce sulla pagina caricata
  const regola = FILTRI.find(f => f.value === filtro) ?? FILTRI[0];
  const filtered = items.filter(regola.vale);
  const nonPartite = items.filter((n: any) => n.stato === "NON_PARTITA").length;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Notifiche</h1>
        <p>I messaggi che il servizio spedisce all'ente: versamenti rifiutati, pacchetti conservati, esibizioni e copie richieste. Per ognuno: se è partito, quanti tentativi e perché non è partito.</p>
        <div className="vd-testa-comandi">
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => notifiche.refetch()} disabled={!tenantId || notifiche.isFetching}>
            {notifiche.isFetching && !notifiche.isLoading ? "Aggiornamento…" : "Aggiorna"}
          </button>
        </div>
      </div>

      {ente.globale && <div className="vd-filtri"><SceltaEnte id="vd-notifiche-ente" stato={ente} onCambia={cursori.ricomincia} /></div>}

      <section aria-labelledby="vd-elenco-notifiche">
        <div className="vd-sezione-testa">
          <h2 id="vd-elenco-notifiche" className="vd-etichetta">Messaggi</h2>
          {notifiche.isSuccess && items.length > 0 && (
            <span className="vd-nota">
              {items.length.toLocaleString("it-IT")} in questa pagina, dalla più recente
              {nonPartite > 0 && <> · <strong className="vd-stato vd-stato--critico">{nonPartite} non {nonPartite === 1 ? "partita" : "partite"}</strong></>}
            </span>
          )}
        </div>

        {tenantId && notifiche.isSuccess && items.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: "8px 16px", marginBottom: 16 }}>
            <div className="vd-campo" style={{ flex: "0 1 280px" }}>
              <label htmlFor="vd-filtro-notifiche">Mostra</label>
              <select id="vd-filtro-notifiche" className="vd-input" value={filtro} aria-describedby="vd-filtro-aiuto"
                      onChange={e => setFiltro(e.target.value)}>
                {FILTRI.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
              </select>
            </div>
            <span id="vd-filtro-aiuto" className="vd-nota" style={{ paddingBottom: 12 }}>
              {filtro ? `${filtered.length} su ${items.length} in questa pagina` : "Il filtro agisce sulle notifiche di questa pagina."}
            </span>
          </div>
        )}

        {!tenantId ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>
            {ente.globale ? "Scegli un ente per vedere le sue notifiche." : "Il tuo profilo non è legato a un ente: le notifiche si consultano per ente."}
          </p>
        ) : notifiche.isLoading ? (
          <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura delle notifiche…</p>
        ) : notifiche.isError ? (
          <EsitoCritico titolo="Notifiche non disponibili" errore={notifiche.error}
                        spiegazione="Lo stato dei messaggi è sconosciuto, non «nessuna notifica»."
                        onRiprova={() => notifiche.refetch()} inRiprova={notifiche.isFetching} />
        ) : items.length === 0 ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>Nessuna notifica per l'ente finora.</p>
        ) : filtered.length === 0 ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>Nessuna notifica «{regola.label.toLowerCase()}» in questa pagina.</p>
        ) : (
          <div className="vd-scorri">
            <table className="vd-tabellone vd-impila">
              <caption className="vd-sr">Notifiche dell'ente, con stato, tentativi ed errore</caption>
              <thead><tr><th scope="col">Ora</th><th scope="col">Notifica</th><th scope="col">Canale e destinatario</th><th scope="col">Stato</th></tr></thead>
              <tbody>
                {filtered.map((n: any, i: number) => {
                  const q = quando(n.data);
                  const stato = STATO_MAP[n.stato] ?? { testo: n.stato ?? "non disponibile", classe: "vd-stato--neutro", spiegazione: "" };
                  const ogg = oggettoDi(n);
                  const tentativi = typeof n.tentativi === "number" ? n.tentativi : null;
                  const inviata = quandoBreve(n.inviato_at);
                  const prossimo = quandoBreve(n.prossimo_tentativo);
                  return (
                    <tr key={n.id ?? i}>
                      <td className="vd-ora" data-label="Ora">{q ? q.ora : "—"}<span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>{q ? q.giorno : "data non disponibile"}</span></td>
                      <td style={{ minWidth: 0 }} data-label="Notifica">
                        <span style={{ fontWeight: 700 }}>{TIPO_LABELS[n.tipo] ?? n.tipo ?? "Notifica"}</span>
                        {ogg && <span className="vd-sotto vd-a-capo">{ogg}</span>}
                        {n.aip_id && <span className="vd-sotto">pacchetto <span className="vd-mono" title={n.aip_id}>{String(n.aip_id).slice(0, 8)}</span></span>}
                      </td>
                      <td style={{ minWidth: 0 }} data-label="Canale e destinatario">
                        {CANALE_LABELS[n.canale] ?? n.canale ?? "—"}
                        {n.destinatario && <span className="vd-sotto vd-a-capo">{n.destinatario}</span>}
                      </td>
                      <td style={{ whiteSpace: "normal", minWidth: 0 }} data-label="Stato">
                        <span className={`vd-stato ${stato.classe}`}>{stato.testo}</span>
                        {stato.spiegazione && <span className="vd-sotto">{stato.spiegazione}</span>}
                        {n.stato === "INVIATA" && inviata && <span className="vd-sotto">il {inviata}</span>}
                        {tentativi !== null && tentativi > 0 && <span className="vd-sotto">{tentativi === 1 ? "1 tentativo" : `${tentativi} tentativi`}</span>}
                        {n.stato === "IN_CODA" && prossimo && <span className="vd-sotto">prossimo tentativo il {prossimo}</span>}
                        {n.errore && (
                          <span className={`vd-sotto vd-a-capo ${n.stato === "NON_PARTITA" ? "vd-stato--critico" : "vd-stato--rischio"}`} style={{ fontWeight: 600 }}>
                            {n.stato === "NON_PARTITA" ? "Motivo" : "Ultimo errore"}: {n.errore}
                          </span>
                        )}
                        {n.stato === "NON_PARTITA" && !n.errore && <span className="vd-sotto vd-stato--critico">Motivo non registrato dal servizio</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {notifiche.isSuccess && (
          <Paginazione cursori={cursori} prossimo={prossimo} righe={items.length} limite={limit}
                       nome="Notifiche" etichetta="Pagine delle notifiche" />
        )}
      </section>
    </>
  );
}
