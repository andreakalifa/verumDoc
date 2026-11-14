// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Guardie di ruolo e di abilitazione per le rotte.
 * La logica è quella precedente; i messaggi di accesso negato sono nel sistema
 * «Partenze» (VD-DR-01) e nominano i ruoli con le parole della testata.
 * Il controllo vero resta nel servizio: questa guardia evita solo di mostrare
 * pagine che il servizio rifiuterebbe.
 */
import { Navigate, Link } from "react-router-dom";
import { useAuth, Ruolo } from "../auth/AuthContext";
import { NOME_RUOLO } from "../auth/ruoli";
import { puo } from "../auth/permessi";

interface RoleGuardProps {
  roles: Ruolo[];
  children: JSX.Element;
}


const NOME_ABILITAZIONE: Record<string, string> = {
  "versamento:ud": "versamento dei documenti",
  "versamento:doc": "versamento dei documenti",
  read: "consultazione",
  distribution: "esibizione",
  lifecycle: "ciclo di vita dei pacchetti",
  "admin:tenant": "amministrazione dell'ente",
  "rsc:approve": "approvazioni del responsabile",
  "rsc:report": "rapporti del responsabile",
};

function AccessoNegato({ titolo, children, pagina }: { titolo: string; children: React.ReactNode; pagina?: boolean }) {
  const corpo = (
    <div style={{ maxWidth: 720, display: "flex", flexDirection: "column", gap: 10 }}>
      <h1 style={{ fontSize: 28, fontWeight: 800, fontStretch: "85%", lineHeight: 1.2 }}>{titolo}</h1>
      {children}
      <p style={{ marginTop: 6 }}>
        <Link to="/overview" style={{ fontWeight: 700 }}>Torna al quadro del servizio</Link>
      </p>
    </div>
  );
  // Le pagine già ridisegnate non ricevono il margine dalla testata: in quel caso lo aggiunge la guardia.
  return pagina ? <div className="vd-pagina" style={{ paddingBlock: "28px 48px" }}>{corpo}</div> : corpo;
}

/**
 * Protegge una route o un blocco UI in base al ruolo e spiega perché la pagina non è
 * accessibile (revisione R17, R17-11: mai un rinvio muto al quadro).
 */
export function RoleGuard({ roles, children }: RoleGuardProps) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!roles.includes(user.ruolo as Ruolo)) {
    return (
      <AccessoNegato titolo="Pagina non accessibile con il tuo profilo">
        <p style={{ fontSize: 17 }}>
          È riservata a: <strong>{roles.map(r => NOME_RUOLO[r] ?? r).join(", ")}</strong>.
        </p>
        <p className="vd-nota">
          Il tuo profilo: {NOME_RUOLO[user.ruolo] ?? user.ruolo}. Se ti serve, chiedi l'abilitazione all'amministrazione del tuo ente.
        </p>
      </AccessoNegato>
    );
  }
  return children;
}

const NOME_PERMESSO: Record<string, string> = {
  "versamento:esegui": "versare documenti",
  "esibizione:richiedi": "chiedere esibizioni e copie",
};

/** Revisione R17 (R17-12): guardia sul PERMESSO dell'azione (ruolo fisso o personalizzato). */
export function PermessoGuard({ permesso, children }: { permesso: string; children: JSX.Element }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!puo(user, permesso)) {
    return (
      <AccessoNegato titolo="Funzione non compresa nel tuo profilo" pagina>
        <p style={{ fontSize: 17 }}>
          Il tuo profilo ({NOME_RUOLO[user.ruolo] ?? user.ruolo}) non comprende il permesso di {NOME_PERMESSO[permesso] ?? permesso}.
        </p>
        <p className="vd-nota">Se ti serve, chiedilo all'amministrazione del tuo ente.</p>
      </AccessoNegato>
    );
  }
  return children;
}

/** Guardia basata sull'abilitazione (scope), non sul ruolo. */
export function ScopeGuard({ scope, children }: { scope: string; children: JSX.Element }) {
  const { hasScope, user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!hasScope(scope)) {
    return (
      <AccessoNegato titolo="Funzione non abilitata per il tuo profilo" pagina>
        <p style={{ fontSize: 17 }}>
          Serve l'abilitazione «{NOME_ABILITAZIONE[scope] ?? scope}», che concede l'amministrazione dell'ente.
        </p>
        <p className="vd-nota">Codice dell'abilitazione: <span className="vd-mono">{scope}</span></p>
      </AccessoNegato>
    );
  }
  return children;
}
