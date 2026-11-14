// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * frontend/src/components/ErrorBoundary.tsx
 * Sprint 3B FIX-023: un errore dell'interfaccia non spegne tutto il servizio.
 * Sistema grafico «Partenze» (VD-DR-01): esito con filetto critico, comandi del sistema.
 * Con le pagine caricate a richiesta (VD-C-027) distingue il pezzo di codice che non
 * arriva (rete o nuova versione pubblicata) da un errore della pagina.
 */
import { Component, ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

const CODICE_NON_ARRIVATO = /dynamically imported module|Importing a module script failed|Loading chunk|error loading dynamically/i;

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    // Registro strutturato nella console; l'invio a un servizio di monitoraggio non esiste ancora (VD-C-033).
    console.error("[ErrorBoundary]", {
      message: error.message,
      stack: error.stack,
      componentStack: info.componentStack || "",
      timestamp: new Date().toISOString(),
    });
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    if (this.props.fallback) return this.props.fallback;

    const messaggio = this.state.error?.message ?? "";
    const nonArrivato = CODICE_NON_ARRIVATO.test(messaggio);

    return (
      <div className="vd-pagina" style={{ paddingBlock: "40px 56px" }}>
        <div role="alert" style={{ maxWidth: 720, borderTop: "4px solid var(--vd-critico)", paddingTop: 14, display: "flex", flexDirection: "column", gap: 12 }}>
          <h1 style={{ fontSize: 28, fontWeight: 800, fontStretch: "85%", lineHeight: 1.2, color: "var(--vd-critico)" }}>
            {nonArrivato ? "Pagina non scaricata" : "Questa pagina si è interrotta"}
          </h1>
          <p style={{ fontSize: 17 }}>
            {nonArrivato
              ? "Il browser non è riuscito a scaricare il codice della pagina: la connessione si è interrotta oppure è stata pubblicata una nuova versione del servizio. Ricarica la pagina."
              : "Un errore dell'interfaccia ha fermato questa pagina. I documenti conservati non sono toccati: l'errore riguarda solo la visualizzazione."}
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 4 }}>
            {nonArrivato ? (
              <button type="button" className="vd-bottone" onClick={this.handleReload}>Ricarica la pagina</button>
            ) : (
              <>
                <button type="button" className="vd-bottone" onClick={this.handleReset}>Riprova</button>
                <button type="button" className="vd-bottone vd-bottone--secondario" onClick={this.handleReload}>Ricarica la pagina</button>
              </>
            )}
            <a href="/overview" className="vd-bottone vd-bottone--secondario" style={{ textDecoration: "none" }}>
              Torna al quadro del servizio
            </a>
          </div>
          {messaggio && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 15, paddingBlock: 11 }}>
                Dettaglio tecnico per l'assistenza
              </summary>
              <p className="vd-mono" style={{ overflowWrap: "anywhere", borderTop: "1px solid var(--vd-filetto)", paddingTop: 8 }}>{messaggio}</p>
            </details>
          )}
        </div>
      </div>
    );
  }
}
