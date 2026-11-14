// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Registro delle operazioni — sistema «Partenze» (VD-DR-01).
 * Tabellone delle operazioni registrate in audit_global, dalla più recente,
 * con filtri per operazione ed ente e paginazione. Aggiornamento ogni 30 secondi.
 * Errore, vuoto e vuoto per filtro sono stati distinti (VD-C-014).
 * g3-frontend (g2-dati §6.9): un errore del servizio è un 503 con codice e id di
 * correlazione (mai più 200 con «error»): la pagina mostra entrambi perché chi
 * segnala il problema possa citarli.
 * R04 (VD-C-102): elenco a cursore, senza totale né «salta N righe».
 */
import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { fetchAudit } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { motivoErrore } from "../api/errori";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";

const PAGE_SIZE = 30;

/** Area del servizio a cui appartiene un'operazione, dal prefisso del codice evento. */
const AREA: [string, string][] = [
  ["pdv.", "Versamento"],
  ["aip.", "Conservazione"],
  ["hashchain.", "Catena delle impronte"],
  ["distribuzione.", "Esibizione"],
  ["tenant.", "Ente"],
  ["lifecycle.", "Ciclo di vita"],
  ["auth.", "Accesso"],
  ["pdnd.", "PDND"],
];
const areaDi = (evento: string) => AREA.find(([p]) => evento.startsWith(p))?.[1] ?? "Altro";

const valore = (v: unknown) => (v !== null && typeof v === "object" ? JSON.stringify(v) : String(v));

export default function AuditLogPage() {
  const { user } = useAuth();
  const isGlobal = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";

  const [filterEvento, setFilterEvento] = useState("");
  const [filterTenant, setFilterTenant] = useState("");
  const cursori = useCursori();

  const tenantParam = isGlobal ? filterTenant.trim() || null : user?.tenant_id || null;

  const { data, isLoading, isError, error, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["audit", tenantParam, filterEvento.trim(), cursori.cursore],
    queryFn: () => fetchAudit(tenantParam, filterEvento.trim(), cursori.cursore, PAGE_SIZE),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });

  const erroreServizio: string | null = isError ? motivoErrore(error) : null;

  const items: any[] = Array.isArray(data?.items) ? data.items : [];
  const prossimo = prossimoDi(data);
  const primo = cursori.pagina * PAGE_SIZE + 1;
  const ultimo = cursori.pagina * PAGE_SIZE + items.length;
  const filtrato = !!filterEvento.trim() || (isGlobal && !!filterTenant.trim());

  const letto = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Registro delle operazioni</h1>
        <span className="vd-nota">
          Ogni operazione registrata dal servizio, dalla più recente: chi l'ha eseguita, quando e da dove.
        </span>
        <div className="vd-testa-comandi">
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? "Lettura in corso…" : "Aggiorna"}
          </button>
        </div>
      </div>

      <section aria-labelledby="vdc-registro">
        <div className="vd-sezione-testa">
          <h2 id="vdc-registro" className="vd-etichetta">Operazioni</h2>
          <span className="vd-nota" role="status">
            {erroreServizio || items.length === 0 ? "" : `Dalla più recente${filtrato ? ", con questi filtri" : ""}`}
            {letto && !erroreServizio ? ` · letto alle ${letto}, si aggiorna ogni 30 secondi` : ""}
          </span>
        </div>

        <div className="vd-filtri">
          <div className="vd-campo">
            <label htmlFor="vdc-evento">Operazione</label>
            <input id="vdc-evento" type="search" className="vd-input" value={filterEvento} aria-describedby="vdc-evento-aiuto"
                   onChange={(e) => { setFilterEvento(e.target.value); cursori.ricomincia(); }} />
            <span id="vdc-evento-aiuto" className="vd-aiuto">Parte del codice, per esempio pdv, aip, auth.login</span>
          </div>
          {isGlobal && (
            <div className="vd-campo">
              <label htmlFor="vdc-ente">Identificativo dell'ente</label>
              <input id="vdc-ente" className="vd-input vd-mono" style={{ fontSize: 15 }} value={filterTenant} aria-describedby="vdc-ente-aiuto"
                     onChange={(e) => { setFilterTenant(e.target.value); cursori.ricomincia(); }} spellCheck={false} />
              <span id="vdc-ente-aiuto" className="vd-aiuto">Vuoto: tutti gli enti</span>
            </div>
          )}
        </div>

        {isLoading ? (
          <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura del registro…</p>
        ) : erroreServizio ? (
          <EsitoCritico titolo="Registro delle operazioni non disponibile" errore={error}
                        spiegazione="Il registro non è vuoto per questo motivo: le operazioni restano registrate."
                        onRiprova={() => refetch()} inRiprova={isFetching} />
        ) : items.length === 0 ? (
          <div className="vd-esito">
            <p className="vd-esito-titolo">
              {filtrato ? "Nessuna operazione con questi filtri" : "Il registro non contiene operazioni"}
            </p>
            {filtrato && (
              <p>
                <button type="button" className="vd-comando-testo" onClick={() => { setFilterEvento(""); setFilterTenant(""); cursori.ricomincia(); }}>
                  Togli i filtri
                </button>
              </p>
            )}
          </div>
        ) : (
          <>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila" aria-busy={isFetching}>
                <caption className="vd-sr">Operazioni registrate, da {primo} a {ultimo}</caption>
                <thead>
                  <tr>
                    <th scope="col">Ora</th><th scope="col">Operazione</th><th scope="col">Dettagli</th>
                    <th scope="col">Eseguita da</th><th scope="col">Indirizzo IP</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item: any, i: number) => {
                    const d = item.data ? new Date(item.data) : null;
                    const valida = d && !Number.isNaN(d.getTime());
                    const dettagli = item.dettagli
                      ? typeof item.dettagli === "string"
                        ? item.dettagli
                        : Object.entries(item.dettagli).map(([k, v]) => `${k}: ${valore(v)}`).join(" · ")
                      : null;
                    return (
                      <tr key={item.id || i}>
                        <td className="vd-ora" data-label="Ora">
                          {valida ? d!.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"}
                          <span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>
                            {valida ? d!.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" }) : "data non registrata"}
                          </span>
                        </td>
                        <td data-label="Operazione">
                          <span style={{ fontWeight: 600 }}>{areaDi(item.evento || "")}</span>
                          <span className="vd-sotto vd-mono vd-a-capo">{item.evento || "codice non registrato"}</span>
                        </td>
                        <td className="vd-a-capo" style={{ fontSize: 14, maxWidth: 520 }} data-label="Dettagli">
                          {dettagli ?? <span className="vd-nota">nessun dettaglio</span>}
                        </td>
                        <td data-label="Eseguita da">
                          {item.user_id
                            ? <span className="vd-mono" title={item.user_id}>{String(item.user_id).slice(0, 8)}</span>
                            : <span className="vd-nota">utente non registrato</span>}
                        </td>
                        <td data-label="Indirizzo IP">{item.ip_address ? <span className="vd-mono">{item.ip_address}</span> : <span className="vd-nota">non registrato</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <Paginazione cursori={cursori} prossimo={prossimo} righe={items.length} limite={PAGE_SIZE}
                         nome="Operazioni" etichetta="Pagine del registro" />
          </>
        )}
      </section>
    </>
  );
}
