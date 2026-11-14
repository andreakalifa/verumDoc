// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Pezzi comuni delle console R05, tutti nel sistema «Partenze» (classi `vd-*`):
 * date, valori delle impostazioni, campo «motivo», dialogo laterale, stato vuoto
 * spiegato, chiave d'idempotenza di un modulo. Nessun colore, nessuna classe nuova qui.
 */
import { ReactNode, useEffect, useRef, useState } from "react";
import { nomeLivello, SCELTA, Parola } from "./parole";

export const MOTIVO_MIN = 10;

// ── Date in ora italiana ───────────────────────────────────────────
export const quando = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return {
    ora: d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Rome" }),
    giorno: d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Europe/Rome" }),
  };
};
export const quandoBreve = (iso?: string | null) => { const q = quando(iso); return q ? `${q.giorno} alle ${q.ora}` : null; };
export const giorno = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Europe/Rome" });
};
export const breve = (id?: string | null) => (id ? String(id).slice(0, 8) : "");
export const num = (n?: number | null) => (typeof n === "number" ? n.toLocaleString("it-IT") : "—");
export const plurale = (n: number, uno: string, molti: string) => `${n.toLocaleString("it-IT")} ${n === 1 ? uno : molti}`;

/** Valore di un'impostazione in parole: «sì/no», elenchi separati da virgola, date italiane. */
export function valoreInParole(v: unknown, tipo?: string): string {
  if (v === null || v === undefined || v === "") return tipo === "codes" ? "nessuna restrizione" : "non impostato";
  if (typeof v === "boolean") return v ? "sì" : "no";
  if (Array.isArray(v)) return v.length ? v.map(x => (typeof x === "string" && SCELTA[x]) || String(x)).join(", ") : "nessuna restrizione";
  if (typeof v === "number") return v.toLocaleString("it-IT");
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return giorno(v) ?? v;
  if (typeof v === "object") return JSON.stringify(v);
  // revisione R17 (R17-14): un valore di scelta (enum) si legge in parole
  return (typeof v === "string" && SCELTA[v]) || String(v);
}

/** «ereditato da: conservatore» / «stabilito qui» / «predefinito normativo». */
export function origineInParole(origine: string | null | undefined, livelloVista?: string): string {
  if (!origine) return "origine non indicata";
  if (origine === "PREDEFINITO") return "predefinito normativo";
  if (livelloVista && origine === livelloVista) return `stabilito a livello ${nomeLivello(origine)}`;
  return `ereditato da: ${nomeLivello(origine)}`;
}

export function Stato({ p, sotto }: { p: Parola; sotto?: boolean }) {
  return <span className={`vd-stato ${p.classe}${sotto ? " vd-sotto" : ""}`}>{p.testo}</span>;
}

// ── Campo del motivo: obbligatorio, almeno 10 caratteri, resta nel registro ─
export function CampoMotivo({ id, valore, onCambia, etichetta = "Motivo", aiuto, toccato, onTocca, righe, facoltativo = false, minimo = MOTIVO_MIN }: {
  id: string; valore: string; onCambia: (v: string) => void; etichetta?: string; aiuto?: string;
  toccato: boolean; onTocca: () => void; righe?: number;
  /** R07 (D25): motivo facoltativo (nessun minimo di lunghezza). */
  facoltativo?: boolean;
  /** R09 (D20): lunghezza minima diversa (chiusura con eccezioni: 20). */
  minimo?: number;
}) {
  const corto = !facoltativo && valore.trim().length < minimo;
  return (
    <div className="vd-campo">
      <label htmlFor={id}>{etichetta}{facoltativo
        ? <span className="vd-nota"> (facoltativo)</span>
        : <><span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></>}</label>
      <textarea id={id} className="vd-input vd-textarea" value={valore} maxLength={2000} rows={righe}
                aria-required={!facoltativo} aria-invalid={toccato && corto ? true : undefined}
                aria-describedby={`${id}-aiuto${toccato && corto ? ` ${id}-errore` : ""}`}
                onChange={e => onCambia(e.target.value)} onBlur={onTocca} />
      <span id={`${id}-aiuto`} className="vd-aiuto">
        {aiuto ?? `Perché si fa (almeno ${minimo} caratteri): resta nel registro delle operazioni.`}
      </span>
      {toccato && corto && (
        <span id={`${id}-errore`} className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>
          Scrivi un motivo di almeno {minimo} caratteri.
        </span>
      )}
    </div>
  );
}
export const motivoValido = (m: string, minimo = MOTIVO_MIN) => m.trim().length >= minimo;

// ── Dialogo laterale (<dialog> nativo, come Code di scarto) ─────────
export function DialogoLaterale({ id, titolo, sottotitolo, onClose, children }: {
  id: string; titolo: ReactNode; sottotitolo?: ReactNode; onClose: () => void; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const prima = useRef<Element | null>(null);
  useEffect(() => {
    prima.current = document.activeElement;
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => { (prima.current as HTMLElement | null)?.focus?.(); };
  }, []);
  const chiudi = () => ref.current?.close();
  return (
    <dialog ref={ref} className="vd-dialogo" aria-labelledby={`${id}-titolo`} onClose={onClose}
            onClick={e => { if (e.target === ref.current) chiudi(); }}>
      <div className="vd-dialogo-testa">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 id={`${id}-titolo`} className="vd-dialogo-titolo">{titolo}</h2>
          {sottotitolo && <p className="vd-nota" style={{ marginTop: 4 }}>{sottotitolo}</p>}
        </div>
        <button type="button" className="vd-bottone vd-bottone--secondario" onClick={chiudi}>Chiudi</button>
      </div>
      <div className="vd-dialogo-corpo">{children}</div>
    </dialog>
  );
}

// ── Caricamento in corso: una riga di testo, niente animazioni ──────
export function InLettura({ testo }: { testo: string }) {
  return <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>{testo}</p>;
}

/** Stato vuoto spiegato (un vuoto non è un errore, un errore non è un vuoto). */
export function Vuoto({ titolo, children }: { titolo: string; children?: ReactNode }) {
  return (
    <div className="vd-esito">
      <p className="vd-esito-titolo">{titolo}</p>
      {children}
    </div>
  );
}

/** Chiave d'idempotenza stabile finché il modulo non cambia (un nuovo invio dopo un
 *  errore di rete non crea una seconda pratica); nuova dopo un esito positivo. */
export function useChiaveIdempotenza(): [string, () => void] {
  const nuova = () => (typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
  const [chiave, setChiave] = useState(nuova);
  return [chiave, () => setChiave(nuova())];
}
