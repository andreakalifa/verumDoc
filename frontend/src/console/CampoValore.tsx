// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Campo per il nuovo valore di un'impostazione, secondo il suo tipo
 * (libs/verumdoc_rules/impostazioni.py, TipoImpostazione): intero, sì/no,
 * scelta, elenco di codici, durata in secondi, data. I limiti (minimo, massimo,
 * scelte) arrivano dal servizio: la pagina non ne conosce altri.
 */
import { nomeScelta } from "./parole";
import type { Voce } from "./api";

export type Grezzo = string;

/** Testo iniziale del campo dal valore attuale. */
export function grezzoDa(v: unknown, tipo: string): Grezzo {
  if (v === null || v === undefined) return "";
  if (tipo === "codes" && Array.isArray(v)) return v.join("\n");
  if (tipo === "bool") return v ? "true" : "false";
  return String(v);
}

/** Valore da inviare, oppure il motivo per cui il testo non va bene. */
export function valoreDa(g: Grezzo, voce: Pick<Voce, "tipo" | "minimo" | "massimo" | "scelte">): { valore: unknown } | { errore: string } {
  const t = g.trim();
  switch (voce.tipo) {
    case "int": case "duration": {
      if (!/^-?\d+$/.test(t)) return { errore: voce.tipo === "duration" ? "Scrivi una durata in secondi (numero intero)." : "Scrivi un numero intero." };
      const n = Number(t);
      if (voce.minimo != null && n < voce.minimo) return { errore: `Almeno ${voce.minimo.toLocaleString("it-IT")}.` };
      if (voce.massimo != null && n > voce.massimo) return { errore: `Al massimo ${voce.massimo.toLocaleString("it-IT")}.` };
      return { valore: n };
    }
    case "bool": return t === "true" || t === "false" ? { valore: t === "true" } : { errore: "Scegli sì o no." };
    case "codes": return { valore: t.split(/[\n,;]+/).map(x => x.trim()).filter(Boolean) };
    case "date": return /^\d{4}-\d{2}-\d{2}$/.test(t) ? { valore: t } : { errore: "Scegli una data." };
    case "choice":
      if (voce.scelte?.length && !voce.scelte.includes(t)) return { errore: "Scegli un valore dell'elenco." };
      return { valore: t };
    default: return { valore: t };
  }
}

export function CampoValore({ id, voce, valore, onCambia, errore, etichetta }: {
  id: string; voce: Pick<Voce, "tipo" | "scelte" | "minimo" | "massimo">; valore: Grezzo;
  onCambia: (g: Grezzo) => void; errore?: string; etichetta: string;
}) {
  const aiuto = voce.tipo === "codes" ? "Uno per riga (o separati da virgola); vuoto = nessuna restrizione."
    : voce.tipo === "duration" ? "In secondi."
    : (voce.minimo != null || voce.massimo != null) ? `Fra ${voce.minimo?.toLocaleString("it-IT") ?? "—"} e ${voce.massimo?.toLocaleString("it-IT") ?? "—"}.`
    : undefined;
  const desc = [aiuto ? `${id}-aiuto` : "", errore ? `${id}-errore` : ""].filter(Boolean).join(" ") || undefined;
  const comuni = { id, "aria-invalid": errore ? true : undefined, "aria-describedby": desc };
  return (
    <div className="vd-campo">
      <label htmlFor={id}>{etichetta}</label>
      {voce.tipo === "bool" ? (
        <select {...comuni} className="vd-input" value={valore} onChange={e => onCambia(e.target.value)}>
          <option value="">Scegli…</option><option value="true">sì</option><option value="false">no</option>
        </select>
      ) : voce.tipo === "choice" && voce.scelte?.length ? (
        <select {...comuni} className="vd-input" value={valore} onChange={e => onCambia(e.target.value)}>
          {voce.scelte.map(s => <option key={s} value={s}>{nomeScelta(s)}</option>)}
        </select>
      ) : voce.tipo === "codes" ? (
        <textarea {...comuni} className="vd-input vd-textarea vd-mono" value={valore} spellCheck={false}
                  onChange={e => onCambia(e.target.value)} />
      ) : (
        <input {...comuni} className="vd-input" value={valore}
               type={voce.tipo === "date" ? "date" : "text"}
               inputMode={voce.tipo === "int" || voce.tipo === "duration" ? "numeric" : undefined}
               onChange={e => onCambia(e.target.value)} />
      )}
      {aiuto && <span id={`${id}-aiuto`} className="vd-aiuto">{aiuto}</span>}
      {errore && <span id={`${id}-errore`} className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>{errore}</span>}
    </div>
  );
}
