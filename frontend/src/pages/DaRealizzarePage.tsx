// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Funzioni non ancora realizzate: la pagina dice cosa manca invece di fingere.
 * Collegata dalle righe del tabellone che non hanno ancora una destinazione reale.
 * g3-frontend: tolte «verifica-catena» (la verifica completa si avvia ora dalla
 * pagina della catena, POST /dashboard/hashchain/verifica-completa) e
 * «accesso-spid» (accesso SPID/CIE realizzato da g2-auth; manca solo la
 * configurazione dell'aggregatore, che la pagina d'accesso dichiara con SPID-001).
 * R05 (console): tolte «scarto» (pratiche del ciclo di vita) e «fascicoli» (elenco a
 * cursore, VD-C-087); aggiunte «accordi» e «allarmi-registro».
 * R06: tolte «accordi» (GET /api/v1/configurazione/accordi, VD-C-139) e
 * «allarmi-registro» (GET /api/v1/hashchain/registro/allarmi, VD-C-129).
 */
import { Link, useParams } from "react-router-dom";

const FUNZIONI: Record<string, { titolo: string; cosa: string; manca: string; riferimento: string }> = {
  marche: {
    titolo: "Rinnovo delle marche temporali",
    cosa: "Rinnova le marche dei pacchetti prima della loro scadenza, così che l'integrità resti dimostrabile nel tempo.",
    manca: "Il servizio non appone ancora marche temporali qualificate né ne controlla la scadenza: manca il fornitore qualificato (decisione VD-P-05) e il servizio che gestisce il ciclo di vita dei pacchetti (VD-P-06).",
    riferimento: "Rilasci R13 e R19 del piano di lavoro",
  },
  "firma-manuale": {
    titolo: "Firma del manuale di conservazione",
    cosa: "Il responsabile del servizio firma il manuale con firma qualificata, così che il documento presentato ad AgID e agli enti sia quello approvato e non modificabile.",
    manca: "Dal rilascio R16 il responsabile firma il manuale dalla pagina «Firme del responsabile» con la propria credenziale personale e l'OTP del prestatore (VD-C-166); resta il prestatore qualificato reale (decisione VD-P-05): finché non c'è, la firma si prova solo con il simulatore.",
    riferimento: "Rilascio R16 (firma personale); prestatore: decisione VD-P-05",
  },
  "scarico-pdd": {
    titolo: "Scarico di copie, estratti ed export",
    cosa: "Scarica dal portale i pacchetti di distribuzione diversi dall'esibizione: copia completa, estratto e export per il cambio di conservatore.",
    manca: "Il servizio di distribuzione registra la richiesta e prepara il pacchetto, ma restituisce link a scadenza solo per l'esibizione.",
    riferimento: "Da pianificare (rapporto fe-integrita, richiesta 8)",
  },
};

export default function DaRealizzarePage() {
  const { funzione = "" } = useParams();
  const f = FUNZIONI[funzione];
  return (
    <div style={{ maxWidth: 720, display: "flex", flexDirection: "column", gap: 14 }}>
      <h1 style={{ fontSize: 30, fontWeight: 800, fontStretch: "85%" }}>{f ? f.titolo : "Funzione non disponibile"}</h1>
      <p><strong className="vd-stato vd-stato--neutro" style={{ fontSize: 16 }}>Funzione da realizzare</strong></p>
      {f ? (
        <>
          <p style={{ fontSize: 17 }}>{f.cosa}</p>
          <p style={{ fontSize: 17 }}>{f.manca}</p>
          <p className="vd-nota">{f.riferimento}</p>
        </>
      ) : (
        <p style={{ fontSize: 17 }}>Questa funzione non è ancora disponibile nel servizio.</p>
      )}
      <p><Link to="/overview" style={{ fontWeight: 700 }}>Torna al quadro del servizio</Link></p>
    </div>
  );
}
