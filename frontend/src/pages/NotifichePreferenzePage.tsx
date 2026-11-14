// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Cosa ricevere e come (R10, D32; notification /api/v1/notifiche/preferenze).
 * Per categoria: nella campanella della console e per e-mail. Gli avvisi resi obbligatori
 * dall'ente (o dal conservatore) sono spuntati e non si possono togliere; le categorie che
 * non riguardano il tuo ruolo restano visibili ma con la nota.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EsitoCritico } from "../components/EsitoCritico";
import { leggiPreferenzeNotifiche, salvaPreferenzeNotifiche, type CategoriaPreferenza } from "../console/accesso";
import { InLettura } from "../console/comuni";

type Scelte = Record<string, { console: boolean; email: boolean }>;

export default function NotifichePreferenzePage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["preferenze-notifiche"], queryFn: leggiPreferenzeNotifiche });
  const [scelte, setScelte] = useState<Scelte>({});
  useEffect(() => {
    if (q.data) setScelte(Object.fromEntries(q.data.categorie.map(c => [c.codice, { console: c.console, email: c.email }])));
  }, [q.data]);
  const salva = useMutation({
    mutationFn: () => salvaPreferenzeNotifiche(q.data?.versione ?? 0, scelte),
    onSuccess: d => qc.setQueryData(["preferenze-notifiche"], d),
  });
  const cambia = (c: CategoriaPreferenza, canale: "console" | "email", v: boolean) =>
    setScelte(s => ({ ...s, [c.codice]: { ...s[c.codice], [canale]: v } }));
  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Cosa ricevere</h1>
        <p>Scegli quali avvisi vedere nella campanella «Da fare» e quali ricevere anche per e-mail. Gli avvisi che l'ente ha reso obbligatori restano attivi.</p>
      </div>
      {q.isLoading ? <InLettura testo="Lettura delle preferenze…" />
        : q.isError || !q.data ? <EsitoCritico titolo="Preferenze non disponibili" errore={q.error} onRiprova={() => q.refetch()} />
        : (
          <form className="vd-modulo" onSubmit={e => { e.preventDefault(); salva.mutate(); }}>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Avvisi per categoria e canale</caption>
                <thead><tr><th scope="col">Avviso</th><th scope="col">Nella console</th><th scope="col">Per e-mail</th></tr></thead>
                <tbody>
                  {q.data.categorie.map(c => (
                    <tr key={c.codice}>
                      <td data-label="Avviso">{c.nome}{!c.pertinente && <span className="vd-sotto">Non riguarda il tuo ruolo: non ne riceverai.</span>}</td>
                      {(["console", "email"] as const).map(canale => {
                        const obbligatoria = canale === "console" ? c.console_obbligatoria : c.email_obbligatoria;
                        return (
                          <td key={canale} data-label={canale === "console" ? "Nella console" : "Per e-mail"}>
                            <label className="vd-scelta">
                              <input type="checkbox" checked={obbligatoria || !!scelte[c.codice]?.[canale]} disabled={obbligatoria}
                                     onChange={e => cambia(c, canale, e.target.checked)} />
                              <span>{obbligatoria ? "Obbligatorio per l'ente" : "Sì"}</span>
                            </label>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {salva.isError && <EsitoCritico titolo="Preferenze non salvate" errore={salva.error} azione="operazione" />}
            {salva.isSuccess && <p className="vd-nota" role="status">Preferenze salvate.</p>}
            <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={salva.isPending}>{salva.isPending ? "Salvataggio…" : "Salva le preferenze"}</button></div>
          </form>
        )}
    </>
  );
}
