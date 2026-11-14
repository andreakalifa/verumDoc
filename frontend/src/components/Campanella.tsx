// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Campanella «da fare per te» in testata (R10, D32; notification GET /api/v1/notifiche/campanella).
 * Da fare (pratiche da approvare, proposte di scarto) e avvisi recenti (quote, versamenti
 * rifiutati in scadenza, allarmi, annullamenti non applicati) filtrati dal servizio per
 * permesso e preferenze; R13b (VD-C-163): «Il tuo account», le voci personali (modifiche delle
 * proprie credenziali), mai degli altri. Aggiornamento ogni N secondi deciso dal servizio, sospeso a pagina
 * nascosta (§25.11) e senza rinnovare la sessione (X-Sessione-Rinnovo: no).
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { leggiCampanella, segnaLetti, type VoceCampanella } from "../console/accesso";
import { quandoBreve } from "../console/comuni";

function Voce({ v, onApri }: { v: VoceCampanella; onApri: () => void }) {
  return (
    <li className="vd-campanella-voce">
      <Link to={v.collegamento} onClick={onApri} className="vd-riga-link">
        {!v.letto && <span className="vd-sr">Non letto: </span>}
        <span className={v.letto ? undefined : "vd-campanella-nuovo"}>{v.titolo}</span>
      </Link>
      <span className="vd-sotto">{v.categoria_nome} · {quandoBreve(v.quando)}</span>
    </li>
  );
}

export default function Campanella() {
  const qc = useQueryClient();
  const [aperta, setAperta] = useState(false);
  const pannello = useRef<HTMLDivElement>(null);
  const q = useQuery({
    queryKey: ["campanella"], queryFn: leggiCampanella, retry: false,
    refetchInterval: d => (d.state.data?.intervallo_aggiornamento_secondi ?? 60) * 1000,
    refetchIntervalInBackground: false,
  });
  const letti = useMutation({ mutationFn: segnaLetti, onSuccess: () => qc.invalidateQueries({ queryKey: ["campanella"] }) });
  useEffect(() => {
    if (!aperta) return;
    const chiudi = (e: KeyboardEvent) => { if (e.key === "Escape") setAperta(false); };
    window.addEventListener("keydown", chiudi);
    pannello.current?.focus();
    return () => window.removeEventListener("keydown", chiudi);
  }, [aperta]);
  const n = q.data?.non_letti ?? 0;
  const apri = (v: VoceCampanella) => {
    setAperta(false);
    if (!v.letto) letti.mutate(v.categoria === "PERSONALI" ? { personali: [v.id] } : { ids: [v.id] });
  };
  const personali = q.data?.personali ?? [];
  return (
    <div className="vd-campanella">
      <button type="button" className="vd-comando-testata" aria-expanded={aperta} aria-controls="vd-campanella-pannello"
              onClick={() => setAperta(!aperta)}>
        Da fare{n > 0 && <span className="vd-conta">{n}</span>}<span className="vd-sr">{n > 0 ? `: ${n} da leggere` : ": niente di nuovo"}</span>
      </button>
      {aperta && (
        <div id="vd-campanella-pannello" ref={pannello} tabIndex={-1} role="region" aria-label="Da fare per te" className="vd-campanella-pannello">
          {q.isError ? <p className="vd-nota">Avvisi non disponibili in questo momento: riprova tra poco.</p>
            : !q.data ? <p className="vd-nota">Lettura…</p>
            : (
              <>
                <h2 className="vd-etichetta">Da fare per te</h2>
                {q.data.da_fare.length === 0 ? <p className="vd-nota">Niente da fare adesso.</p>
                  : <ul className="vd-elenco">{q.data.da_fare.map(v => <Voce key={v.id} v={v} onApri={() => apri(v)} />)}</ul>}
                {personali.length > 0 && (
                  <>
                    {/* R13b (VD-C-163): il proprio account, separato dagli avvisi dell'ente */}
                    <h2 className="vd-etichetta">Il tuo account</h2>
                    <ul className="vd-elenco">{personali.map(v => <Voce key={`p${v.id}`} v={v} onApri={() => apri(v)} />)}</ul>
                  </>
                )}
                <h2 className="vd-etichetta">Avvisi</h2>
                {q.data.avvisi.length === 0 ? <p className="vd-nota">Nessun avviso negli ultimi 30 giorni.</p>
                  : <ul className="vd-elenco">{q.data.avvisi.map(v => <Voce key={v.id} v={v} onApri={() => apri(v)} />)}</ul>}
                <div className="vd-comandi">
                  {n > 0 && <button type="button" className="vd-comando-testo" onClick={() => letti.mutate({ tutti: true })}>Segna tutto come letto</button>}
                  <Link to="/preferenze/notifiche" className="vd-comando-testo" onClick={() => setAperta(false)}>Scegli cosa ricevere</Link>
                </div>
              </>
            )}
        </div>
      )}
    </div>
  );
}
