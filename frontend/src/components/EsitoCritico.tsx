// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Un solo trattamento per gli errori di lettura (VD-C-014, REGOLE_SVILUPPO §18):
 * titolo «X non disponibile», il motivo in parole, che cosa NON significa
 * (un errore non è mai «nessun dato»), codice e riferimento per la segnalazione
 * su una riga a parte, e «Riprova» quando la pagina può rileggere.
 * Sistema «Partenze»: `vd-esito vd-esito--critico`, filetto rosso di 4px.
 */
import { leggiErrore, motivoDaLetto } from "../api/errori";

export interface PropEsitoCritico {
  /** «Contratti non disponibili», «Ricerca non riuscita»… */
  titolo: string;
  /** L'errore della query o della mutation (axios). */
  errore: unknown;
  /** Che cosa l'errore NON significa, o che cosa fare: una frase. */
  spiegazione?: string;
  /** Rilettura (refetch); senza, il comando non compare. */
  onRiprova?: () => void;
  inRiprova?: boolean;
  azione?: "consultazione" | "operazione";
}

export function EsitoCritico({ titolo, errore, spiegazione, onRiprova, inRiprova, azione = "consultazione" }: PropEsitoCritico) {
  const l = leggiErrore(errore);
  const motivo = motivoDaLetto(l, azione, false).replace(/^./, c => c.toUpperCase()).replace(/\.$/, "");
  return (
    <div className="vd-esito vd-esito--critico" role="alert" style={{ marginBlock: 8 }}>
      <p className="vd-esito-titolo">{titolo}</p>
      <p style={{ fontSize: 15, overflowWrap: "anywhere" }}>{motivo}.{spiegazione ? ` ${spiegazione}` : ""}</p>
      {(l.codice || l.riferimento) && (
        <p className="vd-nota" style={{ overflowWrap: "anywhere" }}>
          {l.codice && <>Codice <span className="vd-mono">{l.codice}</span></>}
          {l.codice && l.riferimento && " · "}
          {l.riferimento && <>Riferimento per la segnalazione <span className="vd-mono">{l.riferimento}</span></>}
        </p>
      )}
      {onRiprova && (
        <div>
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={onRiprova} disabled={inRiprova}>
            {inRiprova ? "Nuova lettura in corso…" : "Riprova"}
          </button>
        </div>
      )}
    </div>
  );
}
