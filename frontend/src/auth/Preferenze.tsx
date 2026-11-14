// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Preferenze personali salvate sul server (R07, decisioni D31 e D29).
 *
 * `GET/PUT /api/v1/onboarding/preferenze`: tema, densità delle tabelle, pagina
 * iniziale, colonne visibili e, per i ruoli globali, l'ente scelto in testata
 * («tutti» o un ente), validi su ogni dispositivo. Il salvataggio porta la
 * versione letta: se un'altra sessione ha salvato nel frattempo (409 ONB-412) si
 * rilegge e si riapplica la sola modifica. Se il servizio non risponde la console
 * funziona lo stesso con i valori predefiniti (e il tema ricordato nel browser).
 */
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { leggiErrore } from "../api/errori";
import { useAuth } from "./AuthContext";
import { applicaTema, Tema, temaLocale } from "../components/Tema";

export type Densita = "comoda" | "compatta";
export const ENTE_TUTTI = "tutti";

export interface Preferenze {
  tema?: Tema;
  densita?: Densita;
  pagina_iniziale?: string;
  colonne?: Record<string, string[]>;
  /** D29: ente scelto in testata (solo ruoli globali): identificativo o «tutti». */
  ente?: string;
}

interface CtxPreferenze {
  preferenze: Preferenze;
  caricate: boolean;
  aggiorna: (modifica: Partial<Preferenze>) => Promise<void>;
}

const URL = "/v1/onboarding/preferenze";
const Ctx = createContext<CtxPreferenze>({ preferenze: {}, caricate: false, aggiorna: async () => {} });

function senzaVuoti(p: Preferenze): Preferenze {
  return Object.fromEntries(Object.entries(p).filter(([, v]) => v !== undefined && v !== null)) as Preferenze;
}

/** Tema e densità sul documento (prima del primo disegno il tema arriva dal browser). */
export function applicaPreferenze(p: Preferenze) {
  applicaTema(p.tema ?? temaLocale());
  const html = document.documentElement;
  if (p.densita === "compatta") html.setAttribute("data-densita", "compatta");
  else html.removeAttribute("data-densita");
}

export function PreferenzeProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const utente = user?.user_id ?? null;
  const [preferenze, setPreferenze] = useState<Preferenze>({});
  const [caricate, setCaricate] = useState(false);
  const versione = useRef(0);

  useEffect(() => {
    let annullato = false;
    setCaricate(false);
    if (!utente) { setPreferenze({}); versione.current = 0; return; }
    api.get(URL).then(r => {
      if (annullato) return;
      versione.current = Number(r.data?.versione ?? 0);
      const lette = senzaVuoti(r.data?.preferenze ?? {});
      setPreferenze(lette);
      applicaPreferenze(lette);
    }).catch(() => { /* servizio non raggiungibile: valori predefiniti */ })
      .finally(() => { if (!annullato) setCaricate(true); });
    return () => { annullato = true; };
  }, [utente]);

  const aggiorna = useCallback(async (modifica: Partial<Preferenze>) => {
    const salva = async (base: Preferenze) => {
      const nuove = senzaVuoti({ ...base, ...modifica });
      setPreferenze(nuove);
      applicaPreferenze(nuove);
      const r = await api.put(URL, { versione: versione.current, preferenze: nuove });
      versione.current = Number(r.data?.versione ?? versione.current + 1);
    };
    try {
      await salva(preferenze);
    } catch (e) {
      if (leggiErrore(e).codice !== "ONB-412") return; // non salvate: valgono per questa visita
      try {
        const r = await api.get(URL);
        versione.current = Number(r.data?.versione ?? 0);
        await salva(senzaVuoti(r.data?.preferenze ?? {}));
      } catch { /* resta la modifica locale */ }
    }
  }, [preferenze]);

  return <Ctx.Provider value={{ preferenze, caricate, aggiorna }}>{children}</Ctx.Provider>;
}

export function usePreferenze(): CtxPreferenze {
  return useContext(Ctx);
}
