// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * frontend/src/App.tsx
 * Rotte del servizio. Ogni pagina è caricata a richiesta (React.lazy) per non
 * scaricare tutto il servizio al primo accesso (VD-C-027, REGOLE §25.6).
 */
import { lazy, Suspense } from "react";
import { Routes, Route, Navigate, Outlet } from "react-router-dom";
import { usePreferenze } from "./auth/Preferenze";
import { useAuth, type Ruolo } from "./auth/AuthContext";
import { PermessoGuard, RoleGuard, ScopeGuard } from "./components/RoleGuard";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./components/Layout";

const LoginPage              = lazy(() => import("./pages/LoginPage"));
const OverviewPage           = lazy(() => import("./pages/OverviewPage"));
const AIPPage                = lazy(() => import("./pages/AIPPage"));
const VersamentPage          = lazy(() => import("./pages/VersamentPage"));
const VersamentoLivePage     = lazy(() => import("./pages/VersamentoLivePage"));
const BlockchainExplorerPage = lazy(() => import("./pages/BlockchainExplorerPage"));
const HashchainPage          = lazy(() => import("./pages/HashchainPage"));
const SearchPage             = lazy(() => import("./pages/SearchPage"));
const TenantsPage            = lazy(() => import("./pages/TenantsPage"));
const ClassiDocumentaliPage  = lazy(() => import("./pages/ClassiDocumentaliPage"));
const DistributionPage       = lazy(() => import("./pages/DistributionPage"));
const UtentiPage             = lazy(() => import("./pages/UtentiPage"));
const NotifichePage          = lazy(() => import("./pages/NotifichePage"));
const SLAPage                = lazy(() => import("./pages/SLAPage"));
const ManualePage            = lazy(() => import("./pages/ManualePage"));
const AuditLogPage           = lazy(() => import("./pages/AuditLogPage"));
const FascicoliPage          = lazy(() => import("./pages/FascicoliPage"));
const ContrattiPage          = lazy(() => import("./pages/ContrattiPage"));
const NomineRSCPage          = lazy(() => import("./pages/NomineRSCPage"));
const DaRealizzarePage       = lazy(() => import("./pages/DaRealizzarePage"));
const CodeScartoPage         = lazy(() => import("./pages/CodeScartoPage"));
const PreferenzePage         = lazy(() => import("./pages/PreferenzePage"));
// R05 (VD-DR-05): console di configurazione, canali, registro e ciclo di vita
const ConfImpostazioniPage   = lazy(() => import("./pages/ConfImpostazioniPage"));
const ConfProfiliPage        = lazy(() => import("./pages/ConfProfiliPage"));
const ConfClassiAccordiPage  = lazy(() => import("./pages/ConfClassiAccordiPage"));
const ConfQuotePage          = lazy(() => import("./pages/ConfQuotePage"));
const ConfFirmePage          = lazy(() => import("./pages/ConfFirmePage"));
const ConfFormatiPage        = lazy(() => import("./pages/ConfFormatiPage"));
const ConfCicloVitaPage      = lazy(() => import("./pages/ConfCicloVitaPage"));
const ConfBozzePage          = lazy(() => import("./pages/ConfBozzePage"));
const ConfBozzaPage          = lazy(() => import("./pages/ConfBozzaPage"));
const ConfPropostePage       = lazy(() => import("./pages/ConfPropostePage"));
const ValidatorePage         = lazy(() => import("./pages/ValidatorePage"));
const CanaliSftpPage         = lazy(() => import("./pages/CanaliSftpPage"));
const CanaliWebhookPage      = lazy(() => import("./pages/CanaliWebhookPage"));
const RegistroPage           = lazy(() => import("./pages/RegistroPage"));
const PratichePage           = lazy(() => import("./pages/PratichePage"));
const PraticaPage            = lazy(() => import("./pages/PraticaPage"));
const NuovaPraticaPage       = lazy(() => import("./pages/NuovaPraticaPage"));
// R10: accesso, ruoli e notifiche
const AttivaPage             = lazy(() => import("./pages/AttivaPage"));
const ConfAccessoPage        = lazy(() => import("./pages/ConfAccessoPage"));
const ConfFirmaQualificataPage = lazy(() => import("./pages/ConfFirmaQualificataPage"));  // R13a
const FirmeResponsabilePage = lazy(() => import("./pages/FirmeResponsabilePage"));  // R16 (VD-C-166)
const CanaliPecPage = lazy(() => import("./pages/CanaliPecPage"));  // R13b
const InvitiPage             = lazy(() => import("./pages/InvitiPage"));
const RuoliPage              = lazy(() => import("./pages/RuoliPage"));
const NotifichePreferenzePage = lazy(() => import("./pages/NotifichePreferenzePage"));
const CredenzialiPage = lazy(() => import("./pages/CredenzialiPage"));  // R12b, VD-C-156
const DocumentazioneServizioPage = lazy(() => import("./pages/DocumentazioneServizioPage"));  // R15

// Chi vede che cosa nelle console R05 (il servizio ricontrolla sempre, REGOLE §14.1)
const LETTORI_CONFIG: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT", "OPERATORE", "VIEWER"];
const CONFIGURATORI: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"];
const VALIDATORI: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT", "OPERATORE"];
const RESPONSABILI: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE"];
const OPERATIVI: Ruolo[] = ["SUPER_ADMIN", "ADMIN_TENANT", "OPERATORE", "RSC_CONSERVATORE"];
const conGuardia = (ruoli: Ruolo[], pagina: JSX.Element) => <RoleGuard roles={ruoli}>{pagina}</RoleGuard>;

/** Mentre arriva il codice della pagina: una riga di testo, niente animazioni. */
function Caricamento() {
  return (
    <p role="status" className="vd-nota" style={{ fontSize: 16, paddingBlock: 28 }}>
      Caricamento…
    </p>
  );
}

/** Dentro la testata: solo il contenuto aspetta, testata e navigazione restano. */
function PaginaInArrivo() {
  return (
    <Suspense fallback={<div className="vd-pagina"><Caricamento /></div>}>
      <Outlet />
    </Suspense>
  );
}

function Protected({ children }: { children: JSX.Element }) {
  const { user } = useAuth();
  return user ? children : <Navigate to="/login" replace />;
}

/** R07 (W6): solo percorsi interni («/» singolo, nessun «//», schema o barra rovescia). */
function percorsoInterno(percorso: string | undefined): boolean {
  return !!percorso && percorso.length <= 81 && /^\/(?:[a-z0-9-]+\/?)*$/.test(percorso);
}

/** R07 (D31): dopo l'accesso si va alla pagina iniziale delle preferenze (o alla panoramica). */
function PaginaIniziale() {
  const { preferenze, caricate } = usePreferenze();
  if (!caricate) return <Caricamento />;
  const destinazione = percorsoInterno(preferenze.pagina_iniziale) && preferenze.pagina_iniziale !== "/"
    ? preferenze.pagina_iniziale! : "/overview";
  return <Navigate to={destinazione} replace />;
}

export default function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<div className="vd-pagina"><Caricamento /></div>}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/attiva" element={<AttivaPage />} />

        <Route path="/" element={<Protected><Layout /></Protected>}>
          <Route element={<PaginaInArrivo />}>
          {/* R07 (D31): pagina iniziale scelta nelle preferenze personali */}
          <Route index element={<PaginaIniziale />} />
          <Route path="preferenze" element={<PreferenzePage />} />
          <Route path="preferenze/notifiche" element={<NotifichePreferenzePage />} />
          <Route path="preferenze/credenziali" element={<CredenzialiPage />} />

          {/* Tutti i ruoli autenticati */}
          <Route path="overview" element={<OverviewPage />} />

          {/* Versamento — chi ha il permesso versamento:esegui e lo scope versamento:ud
              (revisione R17, R17-12) */}
          <Route path="versamento"
            element={
              <PermessoGuard permesso="versamento:esegui">
                <ScopeGuard scope="versamento:ud">
                  <VersamentoLivePage />
                </ScopeGuard>
              </PermessoGuard>
            } />

          {/* Pipeline PdV — operatori e admin */}
          <Route path="versamenti"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT","OPERATORE","RSC_CONSERVATORE"]}>
                <VersamentPage />
              </RoleGuard>
            } />

          {/* AIP — tutti tranne API_SYSTEM */}
          <Route path="aip" element={<AIPPage />} />

          {/* Blockchain — tutti */}
          <Route path="blockchain" element={<BlockchainExplorerPage />} />

          {/* Hashchain integrity — solo admin e RSC */}
          <Route path="hashchain"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT","RSC_CONSERVATORE"]}>
                <HashchainPage />
              </RoleGuard>
            } />

          {/* Ricerca — tutti */}
          <Route path="search" element={<SearchPage />} />

          {/* Esibizioni: tutti i ruoli vedono lo storico; il modulo solo con
              esibizione:richiedi (revisione R17, R17-01/02: anche l'operatore) */}
          <Route path="distribution"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT","OPERATORE","VIEWER","RSC_CONSERVATORE"]}>
                <DistributionPage />
              </RoleGuard>
            } />

          {/* Classi documentali */}
          <Route path="classi-documentali"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT","RSC_CONSERVATORE"]}>
                <ClassiDocumentaliPage />
              </RoleGuard>
            } />

          {/* Tenant — solo SUPER_ADMIN */}
          <Route path="tenants"
            element={
              <RoleGuard roles={["SUPER_ADMIN"]}>
                <TenantsPage />
              </RoleGuard>
            } />

          {/* Utenti */}
          <Route path="utenti"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT"]}>
                <UtentiPage />
              </RoleGuard>
            } />

          {/* Notifiche */}
          <Route path="notifiche"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT","OPERATORE","RSC_CONSERVATORE"]}>
                <NotifichePage />
              </RoleGuard>
            } />

          {/* SLA & Report */}
          <Route path="sla"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT","RSC_CONSERVATORE"]}>
                <SLAPage />
              </RoleGuard>
            } />

          {/* Manuale della Conservazione */}
          <Route path="manuale"
            element={
              <RoleGuard roles={["SUPER_ADMIN","RSC_CONSERVATORE"]}>
                <ManualePage />
              </RoleGuard>
            } />

          {/* R15 (VD-C-177): documentazione di conformità generata — solo il conservatore */}
          <Route path="documentazione-servizio" element={conGuardia(RESPONSABILI, <DocumentazioneServizioPage />)} />

          {/* Sprint 3B FIX-021: Audit Log — RSC e SUPER_ADMIN */}
          <Route path="audit"
            element={
              <RoleGuard roles={["SUPER_ADMIN","RSC_CONSERVATORE","ADMIN_TENANT"]}>
                <AuditLogPage />
              </RoleGuard>
            } />

          {/* Sprint 4B FIX-033: Fascicoli, Contratti, Nomine RSC */}
          <Route path="fascicoli"
            element={
              <RoleGuard roles={["SUPER_ADMIN","ADMIN_TENANT","OPERATORE","RSC_CONSERVATORE"]}>
                <FascicoliPage />
              </RoleGuard>
            } />

          <Route path="contratti"
            element={
              <RoleGuard roles={["SUPER_ADMIN","RSC_CONSERVATORE"]}>
                <ContrattiPage />
              </RoleGuard>
            } />

          <Route path="nomine-rsc"
            element={
              <RoleGuard roles={["SUPER_ADMIN","RSC_CONSERVATORE"]}>
                <NomineRSCPage />
              </RoleGuard>
            } />

          {/* R04 (VD-C-075): code di scarto — responsabile del servizio e amministratore */}
          <Route path="code-scarto"
            element={
              <RoleGuard roles={["SUPER_ADMIN","RSC_CONSERVATORE"]}>
                <CodeScartoPage />
              </RoleGuard>
            } />

          {/* R05: console di configurazione (contratto Q1) */}
          <Route path="configurazione" element={conGuardia(LETTORI_CONFIG, <ConfImpostazioniPage />)} />
          <Route path="configurazione/profili" element={conGuardia(LETTORI_CONFIG, <ConfProfiliPage />)} />
          <Route path="configurazione/classi-accordi" element={conGuardia(CONFIGURATORI, <ConfClassiAccordiPage />)} />
          <Route path="configurazione/quote" element={conGuardia(RESPONSABILI, <ConfQuotePage />)} />
          <Route path="configurazione/firme" element={conGuardia(LETTORI_CONFIG, <ConfFirmePage />)} />
          <Route path="configurazione/formati" element={conGuardia(LETTORI_CONFIG, <ConfFormatiPage />)} />
          <Route path="configurazione/ciclo-vita" element={conGuardia(LETTORI_CONFIG, <ConfCicloVitaPage />)} />
          <Route path="configurazione/accesso" element={conGuardia(CONFIGURATORI, <ConfAccessoPage />)} />
          <Route path="configurazione/firma-qualificata" element={conGuardia(RESPONSABILI, <ConfFirmaQualificataPage />)} />
          <Route path="firme-responsabile" element={conGuardia(RESPONSABILI, <FirmeResponsabilePage />)} />
          <Route path="utenti/inviti" element={conGuardia(["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"], <InvitiPage />)} />
          <Route path="utenti/ruoli" element={conGuardia(["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"], <RuoliPage />)} />
          <Route path="configurazione/bozze" element={conGuardia(CONFIGURATORI, <ConfBozzePage />)} />
          <Route path="configurazione/bozze/:id" element={conGuardia(CONFIGURATORI, <ConfBozzaPage />)} />
          <Route path="configurazione/proposte" element={conGuardia(CONFIGURATORI, <ConfPropostePage />)} />
          <Route path="configurazione/validatore" element={conGuardia(VALIDATORI, <ValidatorePage />)} />
          {/* R05: canali (SFTP, webhook) e registro delle impronte */}
          <Route path="canali/sftp" element={conGuardia(CONFIGURATORI, <CanaliSftpPage />)} />
          <Route path="canali/webhook" element={conGuardia(["ADMIN_TENANT"], <CanaliWebhookPage />)} />
          {/* R13b (VD-C-078): canali PEC (conservatore) e messaggi PEC dell'ente */}
          <Route path="canali/pec" element={conGuardia(["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"], <CanaliPecPage />)} />
          <Route path="registro" element={conGuardia(RESPONSABILI, <RegistroPage />)} />
          {/* R05: ciclo di vita (contratto Q2) */}
          <Route path="pratiche" element={conGuardia(OPERATIVI, <PratichePage />)} />
          <Route path="pratiche/nuova" element={conGuardia(OPERATIVI, <NuovaPraticaPage />)} />
          <Route path="pratiche/:id" element={conGuardia(OPERATIVI, <PraticaPage />)} />

          {/* Funzioni non ancora realizzate: pagina che lo dichiara */}
          <Route path="da-realizzare/:funzione" element={<DaRealizzarePage />} />

          {/* 404 interno */}
          <Route path="*" element={<Navigate to="/overview" replace />} />
          </Route>
        </Route>
      </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
