// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Paginazione a cursore (R04, VD-C-102): gli elenchi del servizio non danno più
 * il totale né accettano «salta N righe»: ogni pagina restituisce `cursore` e
 * `altri`. La pagina tiene la pila dei cursori già visti per tornare indietro.
 * Sistema «Partenze»: `.vd-paginazione`, bottoni secondari ai lati, posizione al centro.
 */
import { useState } from "react";

export interface Cursori {
  /** Indice della pagina mostrata (0 = la più recente). */
  pagina: number;
  /** Cursore da inviare per la pagina mostrata (null per la prima). */
  cursore: string | null;
  /** Torna alla prima pagina (filtri cambiati). */
  ricomincia: () => void;
  /** Va alla pagina successiva con il cursore restituito dal servizio. */
  avanti: (prossimo: string | null) => void;
  indietro: () => void;
}

export function useCursori(): Cursori {
  const [pagina, setPagina] = useState(0);
  // cursori[n] apre la pagina n (la prima senza cursore); si allunga andando avanti
  const [cursori, setCursori] = useState<(string | null)[]>([null]);
  return {
    pagina,
    cursore: cursori[pagina] ?? null,
    ricomincia: () => { setPagina(0); setCursori([null]); },
    avanti: (prossimo) => {
      if (!prossimo) return;
      setCursori(c => [...c.slice(0, pagina + 1), prossimo]);
      setPagina(p => p + 1);
    },
    indietro: () => setPagina(p => Math.max(0, p - 1)),
  };
}

/** Cursore della pagina successiva dalla risposta `{cursore, altri}`, o null. */
export function prossimoDi(dati: { cursore?: string | null; altri?: boolean } | undefined): string | null {
  return dati?.altri && dati?.cursore ? dati.cursore : null;
}

interface PropPaginazione {
  cursori: Cursori;
  prossimo: string | null;
  /** Righe della pagina mostrata. */
  righe: number;
  limite: number;
  /** «Notifiche», «Operazioni»… */
  nome: string;
  /** Etichetta accessibile della navigazione. */
  etichetta: string;
  testoPrecedente?: string;
  testoSuccessiva?: string;
}

/** Navigazione fra le pagine; non compare se c'è una sola pagina. */
export function Paginazione({ cursori, prossimo, righe, limite, nome, etichetta,
                              testoPrecedente = "Più recenti", testoSuccessiva = "Meno recenti" }: PropPaginazione) {
  if (cursori.pagina === 0 && prossimo === null) return null;
  const primo = cursori.pagina * limite + 1;
  const ultimo = cursori.pagina * limite + righe;
  return (
    <nav aria-label={etichetta} className="vd-paginazione">
      <button type="button" className="vd-bottone vd-bottone--secondario" disabled={cursori.pagina === 0}
              onClick={cursori.indietro}>{testoPrecedente}</button>
      <span>{nome} {primo.toLocaleString("it-IT")}–{ultimo.toLocaleString("it-IT")}</span>
      <button type="button" className="vd-bottone vd-bottone--secondario" disabled={prossimo === null}
              onClick={() => cursori.avanti(prossimo)}>{testoSuccessiva}</button>
    </nav>
  );
}
