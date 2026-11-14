// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Pezzi dei moduli di registrazione (contratti, nomine): campo con etichetta,
 * aiuto ed errore PER CAMPO collegati con aria-describedby, e riepilogo da
 * confermare prima dell'invio. Sistema «Partenze»: solo classi vd-*.
 *
 * Gli errori per campo arrivano da due fonti: i controlli della pagina (gli
 * stessi del servizio: dashboard-api ContrattoCreate / NominaCreate) e il 422
 * del servizio (`leggiErrore(e).campi`, chiave = nome del campo nel corpo).
 */
import { Fragment, ReactNode } from "react";

export interface PropCampo {
  id: string;
  etichetta: string;
  valore: string;
  onCambia: (v: string) => void;
  obbligatorio?: boolean;
  aiuto?: string;
  errore?: string | null;
  tipo?: "text" | "date" | "email" | "number";
  opzioni?: { value: string; label: string }[];
  mono?: boolean;
  extra?: Record<string, unknown>;
}

export function Campo({ id, etichetta, valore, onCambia, obbligatorio, aiuto, errore, tipo = "text", opzioni, mono, extra }: PropCampo) {
  const descritto = [aiuto ? `${id}-aiuto` : "", errore ? `${id}-errore` : ""].filter(Boolean).join(" ") || undefined;
  const comuni = {
    id, className: `vd-input${mono ? " vd-mono" : ""}`, value: valore,
    "aria-invalid": errore ? true : undefined, "aria-describedby": descritto,
    "aria-required": obbligatorio || undefined,
    ...extra,
  };
  return (
    <div className="vd-campo">
      <label htmlFor={id}>
        {etichetta}
        {obbligatorio && <><span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></>}
      </label>
      {opzioni ? (
        <select {...comuni} onChange={e => onCambia(e.target.value)}>
          {opzioni.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      ) : (
        <input {...comuni} type={tipo} onChange={e => onCambia(e.target.value)} autoComplete="off"
               style={mono ? { fontSize: 15 } : undefined} />
      )}
      {aiuto && <span id={`${id}-aiuto`} className="vd-aiuto">{aiuto}</span>}
      {errore && (
        <span id={`${id}-errore`} className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>
          {errore}
        </span>
      )}
    </div>
  );
}

/** Riepilogo di ciò che si sta per registrare, con conferma esplicita. */
export function Riepilogo({ titolo, voci, avviso, inCorso, onConferma, onCorreggi, comando }: {
  titolo: string;
  voci: [string, ReactNode][];
  avviso?: ReactNode;
  inCorso: boolean;
  onConferma: () => void;
  onCorreggi: () => void;
  comando: string;
}) {
  return (
    <div className="vd-esito vd-esito--info" role="group" aria-labelledby="vd-riepilogo-titolo">
      <p id="vd-riepilogo-titolo" className="vd-esito-titolo">{titolo}</p>
      <dl className="vd-dati">
        {voci.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v || <span className="vd-nota">non indicato</span>}</dd></Fragment>)}
      </dl>
      {avviso && <p className="vd-avviso">{avviso}</p>}
      <div className="vd-comandi">
        <button type="button" className="vd-bottone" onClick={onConferma} disabled={inCorso} autoFocus>
          {inCorso ? "Registrazione in corso…" : comando}
        </button>
        <button type="button" className="vd-bottone vd-bottone--secondario" onClick={onCorreggi} disabled={inCorso}>
          Correggi i dati
        </button>
      </div>
    </div>
  );
}
