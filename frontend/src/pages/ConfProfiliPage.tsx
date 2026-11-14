// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Profili di metadati e loro versioni (contratto Q1: GET /profili, GET /profili/{id}/versioni).
 * Profili per tipologia dell'Allegato 5 (dati versionati con validità) e profili delle classi.
 * Un profilo si apre in un dialogo laterale con le versioni (valido dal / al) e i campi
 * della versione scelta; i campi «da riscontrare» sul testo ufficiale lo dichiarano.
 * R11 (D07): adozione della versione (automatica o scelta dalla classe con
 * PROFILO_TIPOLOGIA_VERSIONE), profili propri del conservatore, riferimento al testo
 * dell'Allegato 5, obblighi a condizione e sottocampi delle strutture.
 * Sistema «Partenze».
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { leggiProfili, leggiVersioniProfilo, type CampoProfilo, type CondizioneCampo, type Profilo } from "../console/api";
import { DialogoLaterale, giorno, InLettura, num, Vuoto } from "../console/comuni";

const LIMITE = 100;

const condizione = (c: CondizioneCampo) =>
  c.in ? `${c.campo} è ${c.in.join(" o ")}` : `${c.campo} non è ${(c.non_in ?? []).join(" né ")}`;

/** Obbligo del campo in parole: calcolato, obbligatorio, obbligatorio a condizione, facoltativo. */
function obbligo(c: CampoProfilo): string {
  if (c.calcolato) return "Calcolato dal servizio";
  if (c.obbligatorio) return "Obbligatorio";
  if (c.obbligatorio_se?.length) return `Obbligatorio se ${c.obbligatorio_se.map(condizione).join(" oppure ")}`;
  return "Facoltativo";
}

function Sottocampi({ campi }: { campi: CampoProfilo[] }) {
  return (
    <ul className="vd-sotto vd-a-capo">
      {campi.map(s => <li key={s.nome}><span className="vd-mono">{s.nome}</span> — {obbligo(s).toLowerCase()}{s.valori_ammessi?.length ? ` (${s.valori_ammessi.join(", ")})` : ""}</li>)}
    </ul>
  );
}

function DialogoProfilo({ p, onClose }: { p: Profilo; onClose: () => void }) {
  const versioni = useQuery({ queryKey: ["conf-profilo", p.id], queryFn: () => leggiVersioniProfilo(p.id) });
  const elenco = versioni.data?.items ?? [];
  const [scelta, setScelta] = useState<number | null>(null);
  const v = elenco.find(x => x.versione === scelta) ?? elenco[elenco.length - 1];
  return (
    <DialogoLaterale id="vd-profilo" titolo={p.nome} sottotitolo={`${p.tipo === "tipologia" ? "Tipologia" : "Classe"} ${p.codice}${p.estende ? ` · estende ${p.estende}` : ""}`} onClose={onClose}>
      {versioni.isLoading ? <InLettura testo="Lettura delle versioni…" />
        : versioni.isError ? <EsitoCritico titolo="Versioni non disponibili" errore={versioni.error} onRiprova={() => versioni.refetch()} />
        : (
          <>
            <section aria-labelledby="vd-prof-versioni">
              <h3 id="vd-prof-versioni">Versioni</h3>
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Versioni del profilo con la validità</caption>
                <thead><tr><th scope="col">Versione</th><th scope="col">Validità</th><th scope="col">Origine</th><th scope="col">Adozione</th></tr></thead>
                <tbody>
                  {elenco.map(x => (
                    <tr key={x.versione} className={x === v ? "vd-riga-in-corso" : undefined}>
                      <td className="vd-ora" data-label="Versione">
                        <button type="button" className="vd-link-bottone" aria-pressed={x === v} onClick={() => setScelta(x.versione)}>{x.versione}</button>
                      </td>
                      <td data-label="Validità">dal {giorno(x.valido_dal)}{x.valido_al ? ` al ${giorno(x.valido_al)}` : " · in vigore"}</td>
                      <td data-label="Origine">{x.origine}</td>
                      <td data-label="Adozione">{x.adozione === "esplicita" ? "Scelta dalla classe" : "Automatica"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            {v && (
              <section aria-labelledby="vd-prof-campi">
                <h3 id="vd-prof-campi">Campi della versione {v.versione}</h3>
                {v.norm_ref && <p className="vd-nota" style={{ marginTop: 8 }}>{String(v.norm_ref)}</p>}
                {v.adozione === "esplicita" && <p className="vd-nota">Questa versione si applica solo alle classi che la scelgono (impostazione «PROFILO_TIPOLOGIA_VERSIONE», con anteprima); i versamenti già fatti restano con la loro versione.</p>}
                <table className="vd-tabellone vd-impila vd-impila--pieno" style={{ marginTop: 8 }}>
                  <caption className="vd-sr">Campi del profilo</caption>
                  <thead><tr><th scope="col">Campo</th><th scope="col">Obbligo</th></tr></thead>
                  <tbody>
                    {(v.campi ?? []).map(c => (
                      <tr key={c.nome}>
                        <td data-label="Campo">
                          <span className="vd-mono">{c.nome}</span>
                          {c.allegato5 && <span className="vd-sotto">Allegato 5: {c.allegato5}{c.rif ? ` (${c.rif})` : ""}</span>}
                          {c.origine === "conservatore" && <span className="vd-sotto">Campo proprio del conservatore (non dell'Allegato 5)</span>}
                          {c.origine === "servizio" && <span className="vd-sotto">Metadato del servizio</span>}
                          {c.descrizione && <span className="vd-sotto vd-a-capo">{c.descrizione}</span>}
                          {!!c.sottocampi?.length && <Sottocampi campi={c.sottocampi} />}
                        </td>
                        <td data-label="Obbligo" style={{ whiteSpace: "normal" }}>
                          <span className={`vd-stato ${c.obbligatorio || c.obbligatorio_se?.length ? "vd-stato--info" : "vd-stato--neutro"}`}>
                            {obbligo(c)}
                          </span>
                          {c.da_riscontrare && <span className="vd-sotto vd-stato--rischio">da riscontrare sul testo ufficiale</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </>
        )}
    </DialogoLaterale>
  );
}

export default function ConfProfiliPage() {
  const ente = useEnte();
  const cursori = useCursori();
  const [aperto, setAperto] = useState<Profilo | null>(null);
  const filtroEnte = ente.globale ? ente.tenantId || undefined : undefined;
  const elenco = useQuery({ queryKey: ["conf-profili", filtroEnte, cursori.cursore], queryFn: () => leggiProfili(filtroEnte, cursori.cursore, LIMITE) });
  const righe = elenco.data?.items ?? [];
  const tipologie = righe.filter(p => p.tipo === "tipologia");
  const classi = righe.filter(p => p.tipo === "classe");

  const tabella = (voci: Profilo[], didascalia: string) => (
    <div className="vd-scorri">
      <table className="vd-tabellone vd-impila" aria-busy={elenco.isFetching}>
        <caption className="vd-sr">{didascalia}</caption>
        <thead><tr><th scope="col">In vigore dal</th><th scope="col">Profilo</th><th scope="col" className="vd-num">Campi</th><th scope="col">Versioni</th></tr></thead>
        <tbody>
          {voci.map(p => (
            <tr key={p.id}>
              <td className="vd-ora" data-label="In vigore dal">
                {p.in_vigore ? giorno(p.in_vigore.valido_dal) : "—"}
                <span className="vd-sotto vd-sotto-normale">{p.in_vigore ? `versione ${p.in_vigore.versione}` : "nessuna versione in vigore"}</span>
              </td>
              <td data-label="Profilo">
                <button type="button" className="vd-link-bottone" aria-haspopup="dialog" onClick={() => setAperto(p)}>{p.nome}</button>
                <span className="vd-sotto"><span className="vd-mono">{p.codice}</span>{p.estende ? ` · estende ${p.estende}` : ""}</span>
                {p.origine === "conservatore" && <span className="vd-sotto">Profilo proprio del conservatore</span>}
                {p.nota && <span className="vd-sotto vd-a-capo">{p.nota}</span>}
              </td>
              <td className="vd-num" data-label="Campi">
                {num(p.in_vigore?.campi)}
                <span className="vd-sotto vd-sotto-normale">{num(p.in_vigore?.obbligatori)} obbligatori</span>
              </td>
              <td data-label="Versioni" style={{ whiteSpace: "normal" }}>
                {p.versioni}
                {!!p.in_vigore?.da_riscontrare && <span className="vd-sotto vd-stato--rischio">{p.in_vigore.da_riscontrare} campi da riscontrare</span>}
                {p.ultima && <span className="vd-sotto vd-a-capo">versione {p.ultima.versione} disponibile, scelta per classe{p.ultima.da_riscontrare ? ` (${p.ultima.da_riscontrare} campi da riscontrare)` : ""}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Profili di metadati</h1>
        <p>Quali metadati chiede ogni tipologia di documento e ogni classe, versione per versione. Un versamento porta la versione con cui è stato verificato.</p>
      </div>
      {ente.globale && <div className="vd-filtri"><SceltaEnte id="vd-prof-ente" stato={ente} onCambia={cursori.ricomincia} /></div>}
      {elenco.isLoading ? <InLettura testo="Lettura dei profili…" />
        : elenco.isError ? <EsitoCritico titolo="Profili non disponibili" errore={elenco.error} onRiprova={() => elenco.refetch()} inRiprova={elenco.isFetching} />
        : righe.length === 0 ? <Vuoto titolo="Nessun profilo" />
        : (
          <div className="vd-pila">
            <section aria-labelledby="vd-prof-tip">
              <div className="vd-sezione-testa"><h2 id="vd-prof-tip" className="vd-etichetta">Tipologie</h2><span className="vd-nota">Allegato 5 delle Linee guida</span></div>
              {tipologie.length ? tabella(tipologie, "Profili per tipologia") : <p className="vd-nota">Nessuna tipologia in questa pagina.</p>}
            </section>
            <section aria-labelledby="vd-prof-cl">
              <div className="vd-sezione-testa"><h2 id="vd-prof-cl" className="vd-etichetta">Classi documentali</h2></div>
              {classi.length ? tabella(classi, "Profili delle classi") : <p className="vd-nota">Nessuna classe con un profilo proprio.</p>}
            </section>
            <Paginazione cursori={cursori} prossimo={prossimoDi(elenco.data)} righe={righe.length} limite={LIMITE}
                         nome="Profili" etichetta="Pagine dei profili" testoPrecedente="Precedenti" testoSuccessiva="Successivi" />
          </div>
        )}
      {aperto && <DialogoProfilo p={aperto} onClose={() => setAperto(null)} />}
    </>
  );
}
