// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Catalogo dei formati (R08, decisione D02).
 *
 * In alto la console di «Impostazioni e regole» con il gruppo «formati»: una voce
 * per famiglia dell'Allegato 2 (`FORMATO.<ID>.ESITO`: ammesso, ammesso con
 * avviso, da convertire, rifiutato). Il conservatore stabilisce la base; l'ente
 * può solo restringere, anche per classe documentale, con bozza e anteprima
 * dell'impatto sui versamenti recenti.
 * Sotto, il catalogo base in sola lettura: identificativi PRONOM e MIME con cui
 * il servizio riconosce ogni famiglia dal contenuto dei file.
 */
import { useQuery } from "@tanstack/react-query";
import { leggiCatalogoFormati } from "../console/api";
import { InLettura, giorno } from "../console/comuni";
import { EsitoCritico } from "../components/EsitoCritico";
import { ConsoleImpostazioni } from "./ConfImpostazioniPage";

const ESITO: Record<string, { testo: string; classe: string }> = {
  AMMESSO: { testo: "Ammesso", classe: "vd-stato--ok" },
  AVVISO: { testo: "Ammesso con avviso", classe: "vd-stato--info" },
  DA_CONVERTIRE: { testo: "Da convertire", classe: "vd-stato--rischio" },
  RIFIUTATO: { testo: "Rifiutato", classe: "vd-stato--critico" },
};

export default function ConfFormatiPage() {
  const catalogo = useQuery({ queryKey: ["catalogo-formati"], queryFn: leggiCatalogoFormati });
  return (
    <>
      <ConsoleImpostazioni gruppo="formati" conRegole={false}
        titolo="Catalogo dei formati"
        spiegazione="Per ogni famiglia di formati: ammessa, ammessa con avviso, da convertire o rifiutata. Il formato si riconosce dal contenuto del file, mai dal nome. L'ente può solo restringere il catalogo del conservatore, anche per una classe; il validatore mostra prima del versamento quali file sarebbero rifiutati." />
      <section aria-labelledby="vd-catalogo-base" style={{ marginTop: 24 }}>
        <div className="vd-sezione-testa">
          <h2 id="vd-catalogo-base" className="vd-etichetta">Catalogo base del conservatore</h2>
          {catalogo.data && <span className="vd-nota">versione {catalogo.data.versione}, in vigore dal {giorno(catalogo.data.valido_dal) ?? catalogo.data.valido_dal}</span>}
        </div>
        {catalogo.isLoading ? <InLettura testo="Lettura del catalogo…" />
          : catalogo.isError ? (
            <EsitoCritico titolo="Catalogo non disponibile" errore={catalogo.error}
                          spiegazione="Gli esiti in vigore sono comunque quelli della tabella sopra."
                          onRiprova={() => catalogo.refetch()} inRiprova={catalogo.isFetching} />
          ) : (
            <>
              <p className="vd-nota" style={{ marginBottom: 8 }}>{catalogo.data?.norm_ref}</p>
              <table className="vd-tabellone vd-impila vd-impila--pieno">
                <thead>
                  <tr>
                    <th scope="col">Famiglia</th>
                    <th scope="col">Riconosciuta da</th>
                    <th scope="col">Esito di base</th>
                  </tr>
                </thead>
                <tbody>
                  {(catalogo.data?.formati ?? []).map(f => {
                    const e = ESITO[f.esito_base] ?? { testo: f.esito_base, classe: "vd-stato--neutro" };
                    return (
                      <tr key={f.id}>
                        <td data-label="Famiglia">
                          <span style={{ fontWeight: 600 }}>{f.nome}</span>
                          <span className="vd-sotto vd-mono">{f.codice}</span>
                        </td>
                        <td data-label="Riconosciuta da" className="vd-a-capo">
                          {f.puid.length ? <span className="vd-mono">{f.puid.join(", ")}</span> : <span className="vd-nota">nessun identificativo PRONOM</span>}
                          {f.mime.length > 0 && <span className="vd-sotto">{f.mime.join(", ")}</span>}
                          {f.pdfa && <span className="vd-sotto">conformità con veraPDF se installato; altrimenti «PDF/A dichiarato, non validato»: al più ammesso con avviso</span>}
                        </td>
                        <td data-label="Esito di base"><span className={`vd-stato ${e.classe}`}>{e.testo}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
      </section>
    </>
  );
}
