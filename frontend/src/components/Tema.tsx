// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Tema chiaro e scuro (VD-DR-05).
 * Predefinito: segue il sistema operativo (`prefers-color-scheme`). La scelta
 * della persona («Chiaro» / «Scuro») si mette su <html data-tema>.
 * R07 (D31): la scelta si salva fra le preferenze personali sul server (vale su
 * ogni dispositivo); il browser la ricorda solo per il primo disegno della pagina,
 * prima che le preferenze arrivino, e come riserva se il servizio non risponde.
 */
import { usePreferenze } from "../auth/Preferenze";

export type Tema = "automatico" | "chiaro" | "scuro";
const CHIAVE = "vd-tema";
const ORDINE: Tema[] = ["automatico", "chiaro", "scuro"];
const NOME: Record<Tema, string> = { automatico: "automatico", chiaro: "chiaro", scuro: "scuro" };

/** Tema ricordato in questo browser (riserva e primo disegno). */
export function temaLocale(): Tema {
  try {
    const v = window.localStorage.getItem(CHIAVE);
    return v === "chiaro" || v === "scuro" ? v : "automatico";
  } catch {
    return "automatico";
  }
}

export function applicaTema(t: Tema) {
  const html = document.documentElement;
  if (t === "automatico") html.removeAttribute("data-tema");
  else html.setAttribute("data-tema", t);
  try {
    if (t === "automatico") window.localStorage.removeItem(CHIAVE);
    else window.localStorage.setItem(CHIAVE, t);
  } catch { /* memoria del browser non disponibile: vale per questa visita */ }
}

/** Da chiamare prima del primo disegno (main.tsx): niente lampo di tema sbagliato. */
export function applicaTemaSalvato() {
  applicaTema(temaLocale());
}

/** Scelta del tema nel menu della persona: tre bottoni con aria-pressed (salvata sul server). */
export function SceltaTema() {
  const { preferenze, aggiorna } = usePreferenze();
  const tema: Tema = preferenze.tema ?? temaLocale();
  return (
    <div role="group" aria-label="Tema" className="vd-scelta-tema">
      <span aria-hidden="true" className="vd-scelta-tema-nome">Tema</span>
      {ORDINE.map(t => (
        <button key={t} type="button" aria-pressed={t === tema} onClick={() => void aggiorna({ tema: t })}>
          {NOME[t].charAt(0).toUpperCase() + NOME[t].slice(1)}
        </button>
      ))}
    </div>
  );
}
