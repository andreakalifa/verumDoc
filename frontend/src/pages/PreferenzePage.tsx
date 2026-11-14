// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Preferenze personali (R07, D31): tema, densità delle tabelle e pagina iniziale,
 * salvati sul server e validi su ogni dispositivo. Le colonne visibili si scelgono
 * sopra ogni tabella; l'ente (ruoli globali) in testata.
 */
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Densita, usePreferenze } from "../auth/Preferenze";
import { pagineDelRuolo } from "../components/Layout";
import { Tema, temaLocale } from "../components/Tema";

const TEMI: [Tema, string][] = [["automatico", "Automatico (come il sistema)"], ["chiaro", "Chiaro"], ["scuro", "Scuro"]];
const DENSITA: [Densita, string][] = [["comoda", "Comoda"], ["compatta", "Compatta"]];

export default function PreferenzePage() {
  const { user } = useAuth();
  const { preferenze, caricate, aggiorna } = usePreferenze();
  const pagine = pagineDelRuolo(user?.ruolo);
  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Preferenze</h1>
        <span className="vd-nota">Valgono per te, su ogni dispositivo. Si salvano appena le cambi.</span>
      </div>
      <nav aria-label="Altre impostazioni personali" className="vd-comandi" style={{ marginBottom: 16 }}>
        <Link to="/preferenze/credenziali" className="vd-comando-testo">Le mie credenziali</Link>
        <Link to="/preferenze/notifiche" className="vd-comando-testo">Cosa ricevere</Link>
      </nav>
      {!caricate && <p className="vd-nota" role="status">Lettura delle preferenze…</p>}
      <div className="vd-filtri" style={{ flexDirection: "column", alignItems: "flex-start", gap: 20 }}>
        <fieldset className="vd-campo" style={{ border: 0, padding: 0 }}>
          <legend style={{ fontWeight: 700 }}>Tema</legend>
          {TEMI.map(([v, t]) => (
            <label key={v} style={{ display: "block" }}>
              <input type="radio" name="vd-pref-tema" value={v}
                     checked={(preferenze.tema ?? temaLocale()) === v}
                     onChange={() => void aggiorna({ tema: v })} />{" "}{t}
            </label>
          ))}
        </fieldset>
        <fieldset className="vd-campo" style={{ border: 0, padding: 0 }}>
          <legend style={{ fontWeight: 700 }}>Densità delle tabelle</legend>
          {DENSITA.map(([v, t]) => (
            <label key={v} style={{ display: "block" }}>
              <input type="radio" name="vd-pref-densita" value={v}
                     checked={(preferenze.densita ?? "comoda") === v}
                     onChange={() => void aggiorna({ densita: v })} />{" "}{t}
            </label>
          ))}
        </fieldset>
        <div className="vd-campo">
          <label htmlFor="vd-pref-iniziale">Pagina iniziale</label>
          <select id="vd-pref-iniziale" className="vd-input" value={preferenze.pagina_iniziale ?? "/overview"}
                  onChange={e => void aggiorna({ pagina_iniziale: e.target.value })}>
            {pagine.map(p => <option key={p.to} value={p.to}>{p.label}</option>)}
          </select>
        </div>
      </div>
    </>
  );
}
