// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Testata del servizio — sistema grafico «Partenze» (VD-DR-01), rivista in R12c.
 *
 * Una sola colonna (`--vd-pagina`) per testata, sotto-navigazione e contenuto.
 * - ≥1024 px: marchio, aree come schede, ricerca, «Da fare» e menu della persona su
 *   UNA riga se le aree ci stanno; altrimenti le aree passano su una seconda riga
 *   grafite a tutta colonna. La scelta è misurata (larghezza delle aree contro lo
 *   spazio libero), mai lasciata a una sovrapposizione: le schede non finiscono mai
 *   sotto la ricerca.
 * - <1024 px: le aree escono dalla barra ed entrano nel menu a scomparsa («Menu»),
 *   con la ricerca (sotto 640 px) e le azioni della persona.
 * Le azioni della persona (tema, preferenze, uscita) stanno in un solo menu: niente
 * riquadri sparsi in testata. L'ente su cui si lavora sta nella sotto-navigazione,
 * accanto alle pagine che filtra. Le voci restano filtrate per ruolo e per scope.
 */
import { FormEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Outlet, Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import Campanella from "./Campanella";
import { useAuth, Ruolo, type UserSession } from "../auth/AuthContext";
import { puo, VERSA } from "../auth/permessi";
import { NOME_RUOLO } from "../auth/ruoli";
import { SceltaTema } from "./Tema";
import { SceltaEnteTestata } from "./SceltaEnte";

interface Voce {
  to: string;
  label: string;
  ruoli: Ruolo[] | null; // null = tutti
  scope?: string;
  /** Revisione R17 (R17-01/12): permesso dell'azione (ruolo fisso o personalizzato). */
  permesso?: string;
  /** Revisione R17 (R17-23): quando la voce è attiva, se non basta il prefisso. */
  attivaSu?: (percorso: string) => boolean;
}
interface Area {
  label: string;
  voci: Voce[];
}

const TUTTI: Ruolo[] = ["SUPER_ADMIN", "ADMIN_TENANT", "OPERATORE", "VIEWER", "RSC_CONSERVATORE"];
const OPERATIVI: Ruolo[] = ["SUPER_ADMIN", "ADMIN_TENANT", "OPERATORE", "RSC_CONSERVATORE"];
const GOVERNO: Ruolo[] = ["SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE"];
const RESPONSABILI: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE"];
// R05 (VD-DR-05): console di configurazione, canali e ciclo di vita
const LETTORI_CONFIG: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT", "OPERATORE", "VIEWER"];
const CONFIGURATORI: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"];
const VALIDATORI: Ruolo[] = ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT", "OPERATORE"];

/** R07 (D31): pagine che il ruolo può scegliere come pagina iniziale. */
export function pagineDelRuolo(ruolo: Ruolo | undefined): { to: string; label: string }[] {
  if (!ruolo) return [];
  return AREE.flatMap(a => a.voci.filter(v => !v.ruoli || v.ruoli.includes(ruolo))
    .filter(v => !v.permesso || puo({ ruolo } as UserSession, v.permesso))
    .map(v => ({ to: v.to, label: `${a.label} · ${v.label}` })));
}

const AREE: Area[] = [
  { label: "Servizio", voci: [
    { to: "/overview", label: "Quadro del servizio", ruoli: TUTTI },
    { to: "/notifiche", label: "Notifiche", ruoli: OPERATIVI },
    // revisione R17 (R17-16): «scarto» nella console è lo scarto archivistico
    { to: "/code-scarto", label: "Messaggi non elaborati", ruoli: RESPONSABILI },
  ] },
  { label: "Versamento", voci: [
    { to: "/versamento", label: "Versa un documento", ruoli: null, permesso: VERSA, scope: "versamento:ud" },
    { to: "/versamenti", label: "Pacchetti in arrivo", ruoli: OPERATIVI },
  ] },
  { label: "Archivio", voci: [
    { to: "/aip", label: "Pacchetti conservati", ruoli: TUTTI },
    { to: "/search", label: "Ricerca", ruoli: TUTTI },
    { to: "/fascicoli", label: "Fascicoli", ruoli: OPERATIVI },
  ] },
  { label: "Integrità", voci: [
    { to: "/hashchain", label: "Catena delle impronte", ruoli: GOVERNO },
    { to: "/registro", label: "Registro e verifiche", ruoli: RESPONSABILI },
    { to: "/blockchain", label: "Blocchi", ruoli: TUTTI },
  ] },
  { label: "Esibizioni", voci: [
    { to: "/distribution", label: "Esibizioni e copie", ruoli: TUTTI },
  ] },
  { label: "Conformità", voci: [
    { to: "/manuale", label: "Manuale di conservazione", ruoli: RESPONSABILI },
    { to: "/firme-responsabile", label: "Firme del responsabile", ruoli: RESPONSABILI },
    { to: "/documentazione-servizio", label: "Documentazione del servizio", ruoli: RESPONSABILI },
    { to: "/sla", label: "Livelli di servizio", ruoli: GOVERNO },
    { to: "/audit", label: "Registro delle operazioni", ruoli: GOVERNO },
    { to: "/contratti", label: "Contratti", ruoli: RESPONSABILI },
    { to: "/nomine-rsc", label: "Nomine", ruoli: RESPONSABILI },
  ] },
  { label: "Enti", voci: [
    { to: "/tenants", label: "Enti serviti", ruoli: ["SUPER_ADMIN"] },
    { to: "/utenti", label: "Utenti e chiavi API", ruoli: ["SUPER_ADMIN", "ADMIN_TENANT"] },
    { to: "/utenti/inviti", label: "Inviti e metodi d'accesso", ruoli: ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"] },
    { to: "/utenti/ruoli", label: "Ruoli personalizzati", ruoli: ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"] },
    { to: "/classi-documentali", label: "Classi documentali", ruoli: GOVERNO },
  ] },
  { label: "Configurazione", voci: [
    { to: "/configurazione", label: "Impostazioni e regole", ruoli: LETTORI_CONFIG },
    { to: "/configurazione/profili", label: "Profili di metadati", ruoli: LETTORI_CONFIG },
    { to: "/configurazione/firme", label: "Verifica delle firme", ruoli: LETTORI_CONFIG },
    { to: "/configurazione/firma-qualificata", label: "Fornitori di firma e marca", ruoli: RESPONSABILI },
    { to: "/configurazione/ciclo-vita", label: "Ciclo di vita automatico", ruoli: LETTORI_CONFIG },
    { to: "/configurazione/accesso", label: "Accesso e notifiche", ruoli: CONFIGURATORI },
    { to: "/configurazione/formati", label: "Catalogo dei formati", ruoli: LETTORI_CONFIG },
    { to: "/configurazione/classi-accordi", label: "Classi e accordi", ruoli: CONFIGURATORI },
    { to: "/configurazione/quote", label: "Quote degli enti", ruoli: RESPONSABILI },
    { to: "/configurazione/bozze", label: "Bozze", ruoli: CONFIGURATORI },
    { to: "/configurazione/proposte", label: "Proposte", ruoli: CONFIGURATORI },
    { to: "/configurazione/validatore", label: "Validatore", ruoli: VALIDATORI },
  ] },
  { label: "Canali", voci: [
    { to: "/canali/sftp", label: "Account SFTP", ruoli: CONFIGURATORI },
    { to: "/canali/webhook", label: "Webhook", ruoli: ["ADMIN_TENANT"] },
    { to: "/canali/pec", label: "Canali PEC", ruoli: CONFIGURATORI },
  ] },
  { label: "Pratiche", voci: [
    { to: "/pratiche", label: "Pratiche", ruoli: OPERATIVI,
      attivaSu: p => p === "/pratiche" || (p.startsWith("/pratiche/") && p !== "/pratiche/nuova") },
    { to: "/pratiche/nuova", label: "Nuova pratica", ruoli: OPERATIVI },
  ] },
];

/** Una voce è attiva solo esattamente se un'altra voce dell'area la estende
 *  («/configurazione» e «/configurazione/bozze»), altrimenti anche sulle sue sottopagine. */
const esatta = (voci: Voce[], v: Voce) => voci.some(o => o.to !== v.to && o.to.startsWith(v.to + "/"));

/** Pagine con una fascia a tutta larghezza sotto la testata: gestiscono da sole la colonna. */
const RIDISEGNATE = ["/overview"];


type Oltre = "" | "destra" | "sinistra" | "entrambi";

/**
 * Una riga di schede che può scorrere: dice quale bordo nasconde altre voci
 * (per la sfumatura di `.vd-schede[data-oltre]`) e porta in vista la voce con
 * `aria-current` ogni volta che cambia `chiave`.
 */
function useSchedeScorrevoli(chiave: string): [React.RefObject<HTMLUListElement>, Oltre] {
  const ref = useRef<HTMLUListElement>(null);
  const [oltre, setOltre] = useState<Oltre>("");
  useEffect(() => {
    const ul = ref.current;
    if (!ul) return;
    const misura = () => {
      const sx = ul.scrollLeft > 1;
      const dx = ul.scrollLeft + ul.clientWidth < ul.scrollWidth - 1;
      setOltre(sx && dx ? "entrambi" : dx ? "destra" : sx ? "sinistra" : "");
    };
    misura();
    ul.addEventListener("scroll", misura, { passive: true });
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(misura) : null;
    ro?.observe(ul);
    window.addEventListener("resize", misura);
    return () => { ul.removeEventListener("scroll", misura); window.removeEventListener("resize", misura); ro?.disconnect(); };
  }, [chiave]); // la riga può comparire o cambiare voci a ogni cambio di pagina
  useEffect(() => {
    const ul = ref.current;
    const a = ul?.querySelector<HTMLElement>("[aria-current]");
    if (!ul || !a || ul.scrollWidth <= ul.clientWidth) return;
    // Porta in vista la voce attiva solo dentro la sua riga (block: "nearest" non sposta la pagina);
    // senza animazione se la persona ha chiesto meno movimento.
    const piano = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    a.scrollIntoView({ inline: "center", block: "nearest", behavior: piano ? "auto" : "smooth" });
  }, [chiave]);
  return [ref, oltre];
}

/**
 * Una riga o due: le aree stanno accanto a marchio, ricerca e azioni solo se la loro
 * larghezza naturale entra nello spazio libero. La misura non dipende dalla modalità
 * (la larghezza naturale delle schede e degli altri elementi è la stessa), quindi non
 * oscilla.
 */
function useRigheTestata(testata: React.RefObject<HTMLDivElement>, aree: React.RefObject<HTMLUListElement>, chiave: string): 1 | 2 {
  const [righe, setRighe] = useState<1 | 2>(1);
  const misura = useCallback(() => {
    const t = testata.current, ul = aree.current;
    if (!t || !ul) return;
    const stile = getComputedStyle(t);
    const interno = t.clientWidth - parseFloat(stile.paddingLeft) - parseFloat(stile.paddingRight);
    const gap = parseFloat(stile.columnGap) || 24;
    const altri = [".vd-testata-marchio", ".vd-testata-cerca", ".vd-testata-azioni"]
      .map(sel => (t.querySelector<HTMLElement>(sel)?.offsetWidth ?? 0))
      .reduce((s, w) => s + w, 0);
    const naturali = [...ul.children].reduce((s, li) => s + (li as HTMLElement).offsetWidth, 0)
      + Math.max(0, ul.children.length - 1) * (parseFloat(getComputedStyle(ul).columnGap) || 0);
    setRighe(naturali + altri + gap * 3 + 8 <= interno ? 1 : 2);
  }, [testata, aree]);
  useLayoutEffect(() => {
    misura();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(misura) : null;
    if (testata.current) ro?.observe(testata.current);
    window.addEventListener("resize", misura);
    void document.fonts?.ready.then(misura);
    return () => { ro?.disconnect(); window.removeEventListener("resize", misura); };
  }, [misura, chiave, testata]);
  return righe;
}

/** Disclosure con chiusura su Esc (fuoco al bottone), clic fuori e cambio di pagina. */
function useApribile(pathname: string) {
  const [aperto, setAperto] = useState(false);
  const contenitore = useRef<HTMLDivElement>(null);
  const bottone = useRef<HTMLButtonElement>(null);
  useEffect(() => { setAperto(false); }, [pathname]);
  useEffect(() => {
    if (!aperto) return;
    const tasto = (e: KeyboardEvent) => { if (e.key === "Escape") { setAperto(false); bottone.current?.focus(); } };
    const fuori = (e: MouseEvent) => { if (contenitore.current && !contenitore.current.contains(e.target as Node)) setAperto(false); };
    window.addEventListener("keydown", tasto);
    document.addEventListener("mousedown", fuori);
    return () => { window.removeEventListener("keydown", tasto); document.removeEventListener("mousedown", fuori); };
  }, [aperto]);
  return { aperto, setAperto, contenitore, bottone };
}

function IconaMenu({ aperto }: { aperto: boolean }) {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square">
      {aperto ? <path d="M4 4l10 10M14 4L4 14" /> : <path d="M2 4.5h14M2 9h14M2 13.5h14" />}
    </svg>
  );
}
function Freccia() {
  return (
    <svg aria-hidden="true" className="vd-freccia" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square">
      <path d="M2.5 4.5L6 8l3.5-3.5" />
    </svg>
  );
}

export default function Layout() {
  const { user, logout, hasScope } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [cerca, setCerca] = useState("");
  const ruolo = (user?.ruolo ?? "") as Ruolo;
  const nomeRuolo = NOME_RUOLO[ruolo] ?? ruolo;
  const aree = AREE
    .map(a => ({ ...a, voci: a.voci.filter(v => (!v.ruoli || v.ruoli.includes(ruolo))
                                              && (!v.permesso || puo(user, v.permesso))) }))
    .filter(a => a.voci.length > 0);
  const attiva = aree.find(a => a.voci.some(v => pathname === v.to || pathname.startsWith(v.to + "/")))
    ?? aree.find(a => a.voci.some(v => pathname.startsWith(v.to))) ?? aree[0];

  const testata = useRef<HTMLDivElement>(null);
  const [schede, oltre] = useSchedeScorrevoli(attiva?.label ?? "");
  const [pagine, oltrePagine] = useSchedeScorrevoli(pathname);
  const righe = useRigheTestata(testata, schede, `${ruolo}|${aree.length}`);
  const menu = useApribile(pathname);

  const esci = () => { logout(); navigate("/login"); };
  const invia = (e: FormEvent) => {
    e.preventDefault();
    const q = cerca.trim();
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`);
  };
  const globale = ruolo === "SUPER_ADMIN" || ruolo === "RSC_CONSERVATORE";
  const conSotto = !!attiva && (attiva.voci.length > 1 || globale);

  return (
    <div className="vd-console">
      <a href="#contenuto" className="vd-salta">Vai al contenuto</a>
      <header className="vd-testata-fondo" data-righe={righe}>
        <div ref={testata} className="vd-pagina vd-testata">
          <NavLink to="/overview" className="vd-testata-marchio">VerumDoc</NavLink>
          <nav aria-label="Aree del servizio" className="vd-schede vd-testata-aree" data-oltre={oltre || undefined}>
            <ul ref={schede}>
              {aree.map(a => {
                const corrente = a === attiva;
                const prima = a.voci.find(v => !v.scope || hasScope(v.scope)) ?? a.voci[0];
                return (
                  <li key={a.label}>
                    {/* Link, non NavLink: la scheda è attiva per tutte le pagine dell'area, non solo per la prima */}
                    <Link to={prima.to} className="vd-scheda-area" aria-current={corrente ? (pathname === prima.to ? "page" : "true") : undefined}>
                      {a.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <form role="search" onSubmit={invia} className="vd-testata-cerca">
            <label htmlFor="vd-cerca" className="vd-sr">Cerca nell'archivio: documento, segnatura, protocollo</label>
            <input id="vd-cerca" className="vd-cerca" type="search" value={cerca} onChange={e => setCerca(e.target.value)}
              placeholder="Cerca nell'archivio" />
          </form>
          <div className="vd-testata-azioni">
            <Campanella />
            <div ref={menu.contenitore} className="vd-menu">
              <button ref={menu.bottone} type="button" className="vd-comando-testata vd-menu-bottone"
                      aria-expanded={menu.aperto} aria-controls="vd-menu-pannello" onClick={() => menu.setAperto(!menu.aperto)}>
                <span className="vd-menu-ruolo">{nomeRuolo}</span>
                <span className="vd-menu-parola">Menu</span>
                <span className="vd-sr">, menu della persona e delle aree</span>
                <span className="vd-menu-icona-grande"><IconaMenu aperto={menu.aperto} /></span>
                <span className="vd-menu-icona-piccola"><Freccia /></span>
              </button>
              {menu.aperto && (
                <div id="vd-menu-pannello" className="vd-menu-pannello">
                  <form role="search" onSubmit={invia} className="vd-menu-cerca">
                    <label htmlFor="vd-cerca-menu" className="vd-sr">Cerca nell'archivio</label>
                    <input id="vd-cerca-menu" className="vd-input" type="search" value={cerca} onChange={e => setCerca(e.target.value)}
                      placeholder="Cerca nell'archivio" />
                  </form>
                  <nav aria-label="Menu delle aree" className="vd-menu-aree">
                    {aree.map(a => (
                      <div key={a.label} className="vd-menu-area">
                        <p className="vd-menu-area-nome">{a.label}</p>
                        <ul>
                          {a.voci.map(v => (v.scope && !hasScope(v.scope)) ? (
                            <li key={v.to} className="vd-menu-voce vd-menu-voce--spenta">{v.label} <span>(non abilitato)</span></li>
                          ) : (
                            <li key={v.to}>
                              {v.attivaSu
                                ? <Link to={v.to} className={`vd-menu-voce${v.attivaSu(pathname) ? " active" : ""}`} aria-current={v.attivaSu(pathname) ? "page" : undefined}>{v.label}</Link>
                                : <NavLink to={v.to} end={esatta(a.voci, v)} className="vd-menu-voce">{v.label}</NavLink>}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </nav>
                  <div className="vd-menu-persona">
                    <p className="vd-menu-chi"><strong>{nomeRuolo}</strong><span>{user?.ragione_sociale}</span></p>
                    <SceltaTema />
                    <ul className="vd-menu-comandi">
                      <li><Link to="/preferenze" className="vd-menu-voce">Preferenze</Link></li>
                      <li><Link to="/preferenze/credenziali" className="vd-menu-voce">Le mie credenziali</Link></li>
                      <li><button type="button" onClick={esci} className="vd-menu-voce">Esci</button></li>
                    </ul>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      {conSotto && attiva && (
        <div className="vd-sotto-testata">
          <div className="vd-pagina vd-sotto-testata-corpo">
            {attiva.voci.length > 1 ? (
              <nav aria-label={`Pagine di ${attiva.label}`} className="vd-schede vd-sotto-schede" data-oltre={oltrePagine || undefined}>
                <ul ref={pagine}>
                  {attiva.voci.map(v => {
                    const bloccata = v.scope ? !hasScope(v.scope) : false;
                    if (bloccata) {
                      return <li key={v.to} className="vd-scheda-pagina vd-scheda-pagina--spenta">{v.label} <span>(non abilitato)</span></li>;
                    }
                    return (
                      <li key={v.to}>
                        {v.attivaSu
                          ? <Link to={v.to} className={`vd-scheda-pagina${v.attivaSu(pathname) ? " active" : ""}`} aria-current={v.attivaSu(pathname) ? "page" : undefined}>{v.label}</Link>
                          : <NavLink to={v.to} end={esatta(attiva.voci, v)} className="vd-scheda-pagina">{v.label}</NavLink>}
                      </li>
                    );
                  })}
                </ul>
              </nav>
            ) : <span className="vd-sotto-titolo">{attiva.voci[0].label}</span>}
            <SceltaEnteTestata />
          </div>
        </div>
      )}

      <main id="contenuto" tabIndex={-1} className="vd-principale">
        {RIDISEGNATE.includes(pathname) ? <Outlet /> : (
          <div className="vd-pagina vd-corpo-pagina">
            <Outlet />
          </div>
        )}
      </main>
    </div>
  );
}
