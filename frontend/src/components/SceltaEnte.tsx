// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Scelta dell'ente per i profili globali (amministrazione del servizio e
 * responsabile del servizio). Gli altri profili lavorano sempre sull'ente del
 * proprio token e non vedono il campo.
 *
 * Un solo componente per tutte le pagine che leggono dati per ente (pacchetti,
 * arrivi, ricerca, notifiche, esibizioni, contratti, nomine): stesse parole,
 * stesso stato d'errore se l'elenco degli enti non si legge (VD-C-014).
 *
 * R07 (D29): la scelta è UNA per tutta la console, in testata (`SceltaEnteTestata`),
 * con l'opzione «Tutti gli enti», e si ricorda fra una sessione e l'altra nelle
 * preferenze personali. Le pagine partono dall'ente scelto; con «tutti» le pagine
 * che lavorano su un solo ente chiedono di sceglierlo.
 *
 * Revisione R17 (R17-08): nelle pagine NON c'è più un secondo selettore (scriveva la
 * stessa preferenza con opzioni diverse): `SceltaEnte` dice su quale ente lavora la pagina
 * e dove si cambia, e avvisa la pagina (`onCambia`) quando l'ente cambia in testata.
 */
import { useEffect, useRef, useState } from "react";
import { ENTE_TUTTI, usePreferenze } from "../auth/Preferenze";
import { useQuery, UseQueryResult } from "@tanstack/react-query";
import { fetchTenants } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { motivoErrore } from "../api/errori";

export interface StatoEnte {
  /** Il profilo può scegliere un ente diverso dal proprio. */
  globale: boolean;
  /** Ente su cui lavora la pagina ("" se non c'è). */
  tenantId: string;
  /** L'ente scelto è quello del token (il contenuto dei pacchetti si consegna solo per questo). */
  proprio: boolean;
  /** Ragione sociale dell'ente scelto, se nota. */
  nomeEnte?: string;
  setEnte: (id: string) => void;
  enti: UseQueryResult<any>;
}

/** `iniziale`: ente da cui partire (es. dall'indirizzo della pagina); vale solo per i profili globali. */
export function useEnte(iniziale?: string | null): StatoEnte {
  const { user } = useAuth();
  const { preferenze, aggiorna } = usePreferenze();
  const globale = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  // D29: l'indirizzo della pagina prevale; poi l'ente scelto in testata
  const [locale, setLocale] = useState<string | null>(iniziale || null);
  const testata = preferenze.ente === ENTE_TUTTI ? "" : (preferenze.ente ?? user?.tenant_id ?? "");
  const scelto = locale ?? testata;
  // revisione R17: una scelta fatta in testata dopo l'apertura prevale sull'ente dell'indirizzo
  const testataIniziale = useRef(testata);
  useEffect(() => {
    if (testata !== testataIniziale.current) { testataIniziale.current = testata; setLocale(null); }
  }, [testata]);
  const setEnte = (id: string) => {
    setLocale(null);
    void aggiorna({ ente: id || ENTE_TUTTI });
  };
  const tenantId = globale ? scelto : (user?.tenant_id ?? "");
  const enti = useQuery({ queryKey: ["tenants-scelta"], queryFn: () => fetchTenants(), enabled: globale });
  const elenco: any[] = enti.data?.items ?? [];
  return {
    globale, tenantId, setEnte, enti,
    proprio: !!tenantId && tenantId === user?.tenant_id,
    nomeEnte: elenco.find(e => e.id === tenantId)?.ragione_sociale,
  };
}

export function SceltaEnte({ id, stato, onCambia }: { id: string; stato: StatoEnte; onCambia?: () => void }) {
  const { user } = useAuth();
  const precedente = useRef(stato.tenantId);
  const avvisa = useRef(onCambia);
  avvisa.current = onCambia;
  useEffect(() => {
    if (precedente.current !== stato.tenantId) { precedente.current = stato.tenantId; avvisa.current?.(); }
  }, [stato.tenantId]);
  if (!stato.globale) return null;
  const { enti, tenantId } = stato;
  const nome = !tenantId ? null
    : stato.nomeEnte ?? (tenantId === user?.tenant_id ? "Ente del tuo profilo" : enti.isLoading ? "Lettura degli enti…" : "Ente scelto");
  return (
    <div className="vd-campo" id={id} role="group" aria-labelledby={`${id}-et`}>
      <span id={`${id}-et`} className="vd-campo-etichetta">Ente</span>
      <span className={nome ? "vd-forte" : "vd-nota"}>{nome ?? "Tutti gli enti: per questa vista scegline uno"}</span>
      <span className="vd-aiuto">Si cambia in «Ente su cui lavori», nella barra sopra.</span>
      {enti.isError && <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>Elenco degli enti non disponibile: {motivoErrore(enti.error)}.</span>}
    </div>
  );
}

/** R07 (D29): scelta dell'ente in testata per i ruoli globali, con «Tutti gli enti». */
export function SceltaEnteTestata() {
  const { user } = useAuth();
  const { preferenze, aggiorna } = usePreferenze();
  const globale = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  const enti = useQuery({ queryKey: ["tenants-scelta"], queryFn: () => fetchTenants(), enabled: globale });
  if (!globale) return null;
  const elenco: any[] = enti.data?.items ?? [];
  const valore = preferenze.ente ?? user?.tenant_id ?? ENTE_TUTTI;
  return (
    <span className="vd-testata-ente">
      <label htmlFor="vd-testata-ente">Ente su cui lavori</label>
      <select id="vd-testata-ente" className="vd-input" value={valore}
              disabled={enti.isLoading} title={enti.isError ? "Elenco degli enti non disponibile" : "Ente su cui lavori"}
              onChange={e => void aggiorna({ ente: e.target.value })}>
        <option value={ENTE_TUTTI}>Tutti gli enti</option>
        {valore !== ENTE_TUTTI && !elenco.some(e => e.id === valore) && (
          <option value={valore}>{valore === user?.tenant_id ? "Ente del tuo profilo" : "Ente scelto"}</option>
        )}
        {elenco.map(e => <option key={e.id} value={e.id}>{e.ragione_sociale}</option>)}
      </select>
    </span>
  );
}
