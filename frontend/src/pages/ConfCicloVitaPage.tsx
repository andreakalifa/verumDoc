// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Ciclo di vita automatico (R09: decisioni D04, D05, D06, D20, D21, D23, D27): stessa
 * console di «Impostazioni e regole», un gruppo per scheda, più lo stato del lavoro
 * automatico per il conservatore.
 *  - Scarto: proposta automatica in bozza (attiva, frequenza, classi escluse),
 *    tentativi e riprese automatiche, minimizzazione dopo lo scarto;
 *  - Annullamento: ruoli prima e dopo la conservazione, doppia approvazione;
 *  - Blocchi WORM: anni della conservazione permanente e soglia del rinnovo;
 *  - Verifiche: frequenza (anche per ente e classe), campione dopo un incidente;
 *  - Audit: finestra della verifica giornaliera, giorno della verifica completa.
 * Lo stato del lavoro automatico (ultimo giro di ogni passo, blocchi in scadenza,
 * rinnovi) viene da GET /api/v1/ciclo-vita/stato-automatico, già in parole.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { EsitoCritico } from "../components/EsitoCritico";
import { leggiStatoAutomatico } from "../console/api";
import { InLettura, num, quandoBreve } from "../console/comuni";
import { ConsoleImpostazioni } from "./ConfImpostazioniPage";

const GRUPPI: { gruppo: string; nome: string; spiegazione: string }[] = [
  { gruppo: "scarto", nome: "Scarto",
    spiegazione: "La proposta automatica prepara una pratica in bozza con i pacchetti scaduti e avvisa l'ente: nulla si cancella senza autorizzazione firmata, nulla osta ed esecuzione del conservatore. Uno scarto fermato da un blocco legale riprende da solo quando il blocco è tolto. Dopo lo scarto, trascorso il periodo, dei pacchetti restano solo i dati dell'elenco di scarto." },
  { gruppo: "annullamento", nome: "Annullamento",
    spiegazione: "Chi può annullare un versamento prima e dopo la conservazione e quando serve una seconda persona che approva (mai chi l'ha chiesto). L'ente può solo rendere la regola più severa." },
  { gruppo: "retention", nome: "Blocchi WORM",
    spiegazione: "Durata del blocco WORM dei pacchetti a conservazione permanente e di allegati, verbali e atti; il blocco si rinnova da solo, mai in riduzione, e ogni rinnovo resta nel registro delle impronte." },
  { gruppo: "verifiche", nome: "Verifiche",
    spiegazione: "Ogni pacchetto si rilegge e si verifica almeno una volta nel periodo indicato; per enti o classi più critici il periodo può essere più breve. Dopo un incidente dello storage parte una verifica a campione." },
  { gruppo: "audit", nome: "Audit",
    spiegazione: "Il registro delle operazioni si confronta ogni giorno con le copie nello storage WORM (ultimi giorni) e per intero una volta al mese, con un accesso in sola lettura." },
];

function esitoInParole(e: Record<string, unknown>): string {
  if (typeof e.esito === "string") return e.esito.replace(/_/g, " ").toLowerCase();
  const parti = Object.entries(e).map(([k, v]) => {
    const valore = typeof v === "number" ? num(v) : Array.isArray(v) ? num(v.length)
      : v && typeof v === "object" ? num(Object.keys(v).length) : String(v);
    return `${k.replace(/_/g, " ")}: ${valore}`;
  });
  return parti.join(" · ") || "—";
}

function StatoLavoro() {
  const q = useQuery({ queryKey: ["stato-automatico"], queryFn: leggiStatoAutomatico });
  if (q.isLoading) return <InLettura testo="Lettura del lavoro automatico…" />;
  if (q.isError || !q.data) return <EsitoCritico titolo="Stato del lavoro automatico non disponibile" errore={q.error} onRiprova={() => q.refetch()} inRiprova={q.isFetching} />;
  const s = q.data;
  return (
    <section aria-labelledby="vd-cv-lavoro">
      <div className="vd-sezione-testa"><h2 id="vd-cv-lavoro" className="vd-etichetta">Lavoro automatico</h2></div>
      <ul className="vd-elenco" style={{ maxWidth: 820 }}>
        {s.in_parole.map(t => <li key={t}><span>{t}</span></li>)}
      </ul>
      <div className="vd-scorri">
        <table className="vd-tabella">
          <caption className="vd-sr">Ultimo giro dei passi automatici</caption>
          <thead><tr><th scope="col">Passo</th><th scope="col">Ente</th><th scope="col">Ultimo giro</th><th scope="col">Esito</th></tr></thead>
          <tbody>
            {s.passi.map(p => (
              <tr key={p.nome}>
                <td>{p.descrizione}{p.in_corso && <span className="vd-sotto">in corso</span>}</td>
                <td className="vd-mono">{p.ente ?? "tutti"}</td>
                <td>{p.ultimo_giro ? quandoBreve(p.ultimo_giro) : "mai"}</td>
                <td>{"errore" in p.ultimo_esito
                  ? <span className="vd-stato vd-stato--critico">errore: {String(p.ultimo_esito.errore)}</span>
                  : <span className="vd-nota">{esitoInParole(p.ultimo_esito)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="vd-nota">Prossima scadenza di un blocco permanente: {s.retention.prossima_scadenza ? quandoBreve(s.retention.prossima_scadenza) : "nessuna registrata"}.</p>
    </section>
  );
}

export default function ConfCicloVitaPage() {
  const { user } = useAuth();
  const conservatore = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  const [scelto, setScelto] = useState(GRUPPI[0].gruppo);
  const g = GRUPPI.find(x => x.gruppo === scelto) ?? GRUPPI[0];
  return (
    <>
      <div className="vd-comandi" role="tablist" aria-label="Gruppi del ciclo di vita automatico" style={{ marginBottom: 16 }}>
        {GRUPPI.map(x => (
          <button key={x.gruppo} type="button" role="tab" aria-selected={x.gruppo === scelto}
                  className={x.gruppo === scelto ? "vd-bottone" : "vd-bottone vd-bottone--secondario"}
                  onClick={() => setScelto(x.gruppo)}>{x.nome}</button>
        ))}
      </div>
      <ConsoleImpostazioni key={g.gruppo} gruppo={g.gruppo} conRegole={false}
        titolo={`Ciclo di vita automatico: ${g.nome.toLowerCase()}`} spiegazione={g.spiegazione} />
      {conservatore && <div style={{ marginTop: 32 }}><StatoLavoro /></div>}
    </>
  );
}
