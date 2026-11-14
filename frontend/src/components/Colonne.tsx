// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Colonne visibili di una tabella (R07, D31): scelta personale salvata sul server
 * con le altre preferenze (`colonne[tabella]`). Le colonne `fisse` restano sempre.
 */
import { usePreferenze } from "../auth/Preferenze";

export interface Colonna { id: string; etichetta: string; fissa?: boolean }

export function useColonne(tabella: string, colonne: Colonna[]) {
  const { preferenze, aggiorna } = usePreferenze();
  const salvate = preferenze.colonne?.[tabella];
  const visibili = new Set(salvate ?? colonne.map(c => c.id));
  colonne.filter(c => c.fissa).forEach(c => visibili.add(c.id));
  const cambia = (id: string, mostra: boolean) => {
    const nuove = colonne.filter(c => c.fissa || (c.id === id ? mostra : visibili.has(c.id))).map(c => c.id);
    void aggiorna({ colonne: { ...(preferenze.colonne ?? {}), [tabella]: nuove } });
  };
  return { visibile: (id: string) => visibili.has(id), cambia };
}

export function SceltaColonne({ tabella, colonne, stato }: {
  tabella: string; colonne: Colonna[]; stato: ReturnType<typeof useColonne>;
}) {
  return (
    <fieldset className="vd-colonne">
      <legend>Colonne visibili</legend>
      {colonne.filter(c => !c.fissa).map(c => (
        <label key={c.id} style={{ fontSize: 14 }}>
          <input type="checkbox" id={`vd-col-${tabella}-${c.id}`} checked={stato.visibile(c.id)}
                 onChange={e => stato.cambia(c.id, e.target.checked)} />{" "}{c.etichetta}
        </label>
      ))}
    </fieldset>
  );
}
