// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Stato vuoto curato (R12c): un elenco senza righe dice che cosa significa e qual è
 * la prossima azione, aperto dallo stesso filetto d'inchiostro dell'intestazione di
 * un tabellone. Non si usa per gli errori di lettura (quelli sono `EsitoCritico`):
 * «vuoto» vuol dire che il servizio ha risposto e non c'è niente, mai «non so».
 */
import type { ReactNode } from "react";

export function Vuoto({ titolo, children, azione }: { titolo: string; children?: ReactNode; azione?: ReactNode }) {
  return (
    <div className="vd-vuoto">
      <p className="vd-vuoto-titolo">{titolo}</p>
      {children && <div className="vd-vuoto-testo">{children}</div>}
      {azione && <div className="vd-comandi">{azione}</div>}
    </div>
  );
}
