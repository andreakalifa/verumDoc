// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Enti serviti — sistema grafico «Partenze» (VD-DR-01).
 * Tabellone degli enti che conservano presso il servizio, con registrazione di un
 * nuovo ente e attivazione dopo la prova. Ogni riga si apre su utenti e classi.
 * VD-C-014: caricamento, errore e «nessun ente» sono stati distinti.
 * Le chiamate (fetchTenants, createTenant, activateTenant, fetchUtenti,
 * fetchClassiTenant) sono quelle precedenti.
 * R06 (VD-C-138): nel dettaglio la natura dell'ente (PA o PRIVATO, con il motivo),
 * con PUT /api/v1/onboarding/tenant/{id}/natura: decide se lo scarto chiede il
 * nulla osta della Soprintendenza. Solo SUPER_ADMIN (la pagina è già riservata).
 */
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchTenants, createTenant, activateTenant,
  fetchUtenti, fetchClassiTenant, fetchTenant, dichiaraNatura,
} from "../api/client";
import { CampoMotivo, motivoValido, giorno } from "../console/comuni";
import { motivoErrore } from "../api/errori";
import { NOME_RUOLO } from "../auth/ruoli";

/** Motivo leggibile di un errore di chiamata. */

/** Revisione R17 (R17-20): giorno in ora italiana (Europe/Rome), non il giorno UTC. */
const dataIt = (iso?: string | null) => (iso ? giorno(iso) ?? String(iso) : null);

const STATO: Record<string, { testo: string; classe: string }> = {
  ATTIVO:      { testo: "In esercizio", classe: "vd-stato--ok" },
  TRIAL:       { testo: "In prova",     classe: "vd-stato--info" },
  SOSPESO:     { testo: "Sospeso",      classe: "vd-stato--rischio" },
  DISATTIVATO: { testo: "Disattivato",  classe: "vd-stato--neutro" },
};


const PIANI = [
  { value: "starter", label: "Starter" },
  { value: "business", label: "Business" },
  { value: "enterprise", label: "Enterprise" },
];
const nomePiano = (p?: string) => PIANI.find(x => x.value === p)?.label ?? (p || "—");

const numero = (n: number, dec = 1) => n.toLocaleString("it-IT", { maximumFractionDigits: dec });

// ── R06 (VD-C-138): natura dell'ente ────────────────────────────────
const NATURA: Record<string, string> = {
  PA: "Pubblica amministrazione: lo scarto richiede il nulla osta della Soprintendenza",
  PRIVATO: "Soggetto privato: lo scarto non richiede il nulla osta",
};

function NaturaEnte({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const enteQ = useQuery({ queryKey: ["tenant-natura", tenantId], queryFn: () => fetchTenant(tenantId) });
  const [natura, setNatura] = useState<"" | "PA" | "PRIVATO">("");
  const [motivo, setMotivo] = useState("");
  const [tentato, setTentato] = useState(false);
  const salva = useMutation({
    mutationFn: () => dichiaraNatura(tenantId, natura as "PA" | "PRIVATO", motivo.trim()),
    onSuccess: () => {
      setNatura(""); setMotivo(""); setTentato(false);
      qc.invalidateQueries({ queryKey: ["tenant-natura", tenantId] });
    },
  });
  const attuale = enteQ.data?.natura ?? null;
  // R07 (D25): motivo obbligatorio solo per PRIVATO; per PA si precompila dall'IPA
  const pronto = !!natura && (natura === "PA" || motivoValido(motivo));
  const ipa: string | null = enteQ.data?.codice_ipa ?? null;
  return (
    <section aria-labelledby={`vde-natura-${tenantId}`}>
      <h3 id={`vde-natura-${tenantId}`}>Natura dell'ente</h3>
      {enteQ.isLoading ? <p className="vd-nota" role="status">Lettura della natura…</p>
        : enteQ.isError ? <p className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>Natura non letta: {motivoErrore(enteQ.error)}.</p>
        : <p>{attuale ? NATURA[attuale] ?? attuale : "Non dichiarata: l'ente è trattato come pubblica amministrazione (serve il nulla osta)."}</p>}
      <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setTentato(true); if (pronto) salva.mutate(); }}>
        <fieldset className="vd-campo">
          <legend>Dichiara la natura</legend>
          {(["PA", "PRIVATO"] as const).map(v => (
            <label key={v} style={{ display: "block" }}>
              <input type="radio" name={`vde-natura-scelta-${tenantId}`} value={v} checked={natura === v} onChange={() => setNatura(v)} />{" "}
              {v === "PA" ? "Pubblica amministrazione" : "Soggetto privato"}
            </label>
          ))}
          {tentato && !natura && <span className="vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Scegli la natura.</span>}
        </fieldset>
        <CampoMotivo id={`vde-natura-motivo-${tenantId}`} valore={motivo} onCambia={setMotivo} toccato={tentato} onTocca={() => setTentato(true)}
                     facoltativo={natura !== "PRIVATO"}
                     aiuto={natura === "PRIVATO"
                       ? "Per un soggetto privato indica l'atto o la visura che lo dimostra: senza nulla osta lo scarto parte subito."
                       : ipa
                         ? `Se lo lasci vuoto si registra «Codice IPA dichiarato ${ipa} (non verificato sull'indice IPA)».`
                         : "Facoltativo per la pubblica amministrazione: l'ente non ha un codice IPA registrato."} />
        {salva.isError && <p role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>Natura non salvata: {motivoErrore(salva.error)}.</p>}
        {salva.isSuccess && <p role="status" className="vd-stato vd-stato--ok">Natura dichiarata.</p>}
        <div className="vd-comandi">
          <button type="submit" className="vd-bottone vd-bottone--secondario" disabled={salva.isPending}>
            {salva.isPending ? "Salvataggio…" : "Dichiara la natura"}
          </button>
        </div>
      </form>
    </section>
  );
}

// ── Dettaglio di un ente: utenti e classi abilitate ─────────────────
function DettaglioEnte({ tenantId }: { tenantId: string }) {
  const utentiQ = useQuery({ queryKey: ["tenants-utenti", tenantId], queryFn: () => fetchUtenti(tenantId) });
  const classiQ = useQuery({ queryKey: ["tenants-classi", tenantId], queryFn: () => fetchClassiTenant(tenantId) });

  const utenti: any[] = utentiQ.data?.items ?? [];
  // Il servizio utenti risponde 200 con «nota» quando non riesce a leggere la tabella: è un errore, non «nessun utente».
  const utentiNonLetti = !!utentiQ.data?.nota && utenti.length === 0;
  const classi: any[] = classiQ.data?.classi ?? [];

  return (
    <div className="vd-dettaglio-griglia">
      <section aria-label="Utenti dell'ente">
        <h3>Utenti{utentiQ.isSuccess && !utentiNonLetti ? ` (${utenti.length})` : ""}</h3>
        {utentiQ.isLoading ? (
          <p className="vd-nota" role="status">Lettura degli utenti…</p>
        ) : utentiQ.isError || utentiNonLetti ? (
          <p className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
            Utenti non disponibili: {utentiQ.isError ? motivoErrore(utentiQ.error) : "il servizio non è riuscito a leggerli"}.
          </p>
        ) : utenti.length === 0 ? (
          <p className="vd-nota">Nessun utente registrato per questo ente.</p>
        ) : (
          <ul className="vd-elenco">
            {utenti.slice(0, 6).map((u: any) => (
              <li key={u.id}>
                <span className="vd-a-capo">{u.nome || u.email}</span>
                <span className="vd-nota">
                  {NOME_RUOLO[u.ruolo] ?? u.ruolo}{u.attivo === false ? " · disattivato" : ""}
                </span>
              </li>
            ))}
            {utenti.length > 6 && <li className="vd-nota">e altri {utenti.length - 6}</li>}
          </ul>
        )}
        <p className="vd-nota" style={{ marginTop: 6 }}>
          Gli utenti si gestiscono da <Link to="/utenti" style={{ fontWeight: 700 }}>Utenti e chiavi API</Link> con il profilo dell'ente.
        </p>
      </section>

      <section aria-label="Classi documentali dell'ente">
        <h3>Classi documentali accettate{classiQ.isSuccess ? ` (${classi.length})` : ""}</h3>
        {classiQ.isLoading ? (
          <p className="vd-nota" role="status">Lettura delle classi…</p>
        ) : classiQ.isError ? (
          <p className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
            Classi non disponibili: {motivoErrore(classiQ.error)}.
          </p>
        ) : classi.length === 0 ? (
          <p className="vd-nota">Nessuna classe documentale attiva: l'ente non può versare.</p>
        ) : (
          <ul className="vd-elenco">
            {classi.slice(0, 8).map((c: any) => (
              <li key={c.id}>
                <span className="vd-a-capo">{c.nome || c.codice}</span>
                <span className="vd-mono">{c.codice}</span>
              </li>
            ))}
            {classi.length > 8 && <li className="vd-nota">e altre {classi.length - 8}</li>}
          </ul>
        )}
      </section>
      <NaturaEnte tenantId={tenantId} />
    </div>
  );
}

// ── Pagina ──────────────────────────────────────────────────────────
const VUOTO = { ragione_sociale: "", codice_fiscale: "", email_referente: "", piano: "starter" };

export default function TenantsPage() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [aperto, setAperto] = useState<string | null>(null);
  const [form, setForm] = useState({ ...VUOTO });
  const [tentato, setTentato] = useState(false);
  const [registrato, setRegistrato] = useState<string | null>(null);
  const [attivazione, setAttivazione] = useState<{ id: string; errore?: string } | null>(null);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["tenants"],
    queryFn: () => fetchTenants(),
  });

  const createMut = useMutation({
    mutationFn: createTenant,
    onSuccess: (_r, vars: any) => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      setRegistrato(vars?.ragione_sociale ?? "");
      setShowForm(false);
      setTentato(false);
      setForm({ ...VUOTO });
    },
  });

  const activateMut = useMutation({
    mutationFn: activateTenant,
    onMutate: (id: string) => setAttivazione({ id }),
    onSuccess: () => { setAttivazione(null); qc.invalidateQueries({ queryKey: ["tenants"] }); },
    onError: (e, id) => setAttivazione({ id, errore: motivoErrore(e, "operazione") }),
  });

  const cf = form.codice_fiscale.trim();
  const mancanti = [
    form.ragione_sociale.trim().length < 2 && "ragione sociale",
    (cf.length < 11 || cf.length > 16) && "codice fiscale o partita IVA (da 11 a 16 caratteri)",
    !form.email_referente.includes("@") && "email del referente",
  ].filter(Boolean) as string[];
  const invalido = (v: boolean) => (tentato && v ? "true" : undefined);

  const invia = (e: FormEvent) => {
    e.preventDefault();
    setTentato(true);
    if (mancanti.length) return;
    createMut.mutate({ ...form, ragione_sociale: form.ragione_sociale.trim(), codice_fiscale: cf, email_referente: form.email_referente.trim() });
  };

  const enti: any[] = data?.items ?? [];
  const totale: number = typeof data?.total === "number" ? data.total : enti.length;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Enti serviti</h1>
        <span className="vd-nota">Gli enti e le aziende che conservano i loro documenti presso il servizio, dalla prova all'esercizio.</span>
        <div className="vd-testa-comandi">
          {!showForm && (
            <button type="button" className="vd-bottone" onClick={() => { setShowForm(true); setRegistrato(null); createMut.reset(); }}>
              Registra un ente
            </button>
          )}
        </div>
      </div>

      <div className="vd-pila">
        {registrato !== null && (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">Ente registrato{registrato ? `: ${registrato}` : ""}</p>
            <p>L'ente parte in prova. Attivalo dal tabellone quando l'accordo di versamento è firmato.</p>
          </div>
        )}

        {showForm && (
          <form className="vd-modulo" onSubmit={invia} noValidate aria-labelledby="vde-nuovo-ente">
            <h2 id="vde-nuovo-ente">Registra un ente</h2>
            <div className="vd-griglia">
              <div className="vd-campo">
                <label htmlFor="vde-ragione">Ragione sociale<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <input id="vde-ragione" className="vd-input" autoFocus aria-invalid={invalido(form.ragione_sociale.trim().length < 2)}
                       value={form.ragione_sociale} onChange={e => setForm({ ...form, ragione_sociale: e.target.value })} />
              </div>
              <div className="vd-campo">
                <label htmlFor="vde-cf">Codice fiscale o partita IVA<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <input id="vde-cf" className="vd-input" autoComplete="off" aria-describedby="vde-cf-aiuto"
                       aria-invalid={invalido(cf.length < 11 || cf.length > 16)}
                       value={form.codice_fiscale} onChange={e => setForm({ ...form, codice_fiscale: e.target.value.toUpperCase() })} />
                <span id="vde-cf-aiuto" className="vd-aiuto">Da 11 a 16 caratteri</span>
              </div>
              <div className="vd-campo">
                <label htmlFor="vde-email">Email del referente<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <input id="vde-email" type="email" className="vd-input" autoComplete="off" aria-invalid={invalido(!form.email_referente.includes("@"))}
                       value={form.email_referente} onChange={e => setForm({ ...form, email_referente: e.target.value })} />
              </div>
              <div className="vd-campo">
                <label htmlFor="vde-piano">Piano</label>
                <select id="vde-piano" className="vd-input" value={form.piano} onChange={e => setForm({ ...form, piano: e.target.value })}>
                  {PIANI.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </div>
            </div>
            <div className="vd-comandi" style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 18 }}>
              <button type="submit" className="vd-bottone" disabled={createMut.isPending}>
                {createMut.isPending ? "Registrazione in corso…" : "Registra l'ente"}
              </button>
              <button type="button" className="vd-bottone vd-bottone--secondario"
                      onClick={() => { setShowForm(false); setTentato(false); createMut.reset(); }}>
                Annulla
              </button>
              {tentato && mancanti.length > 0 && (
                <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                  Da completare: {mancanti.join(", ")}.
                </span>
              )}
              {createMut.isError && (
                <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                  Ente non registrato: {motivoErrore(createMut.error, "operazione")}.
                </span>
              )}
            </div>
          </form>
        )}

        <section aria-labelledby="vde-tab-enti">
          <div className="vd-sezione-testa">
            <h2 id="vde-tab-enti" className="vd-etichetta">Enti</h2>
            {!isLoading && !isError && enti.length > 0 && (
              <span className="vd-nota">
                {totale > enti.length ? `Mostrati ${enti.length} di ${numero(totale, 0)} enti` : `${enti.length} ${enti.length === 1 ? "ente" : "enti"}`}
              </span>
            )}
          </div>

          {isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura degli enti…</p>
          ) : isError ? (
            <div className="vd-esito vd-esito--critico" role="alert">
              <p className="vd-esito-titolo">Elenco degli enti non disponibile</p>
              <p>Non è possibile dire quali enti sono serviti: {motivoErrore(error)}.</p>
              <p>
                <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => refetch()} disabled={isFetching}>
                  {isFetching ? "Nuova lettura in corso…" : "Riprova"}
                </button>
              </p>
            </div>
          ) : enti.length === 0 ? (
            <div className="vd-esito">
              <p className="vd-esito-titolo">Nessun ente registrato</p>
              <p>Il servizio non ha ancora enti. Registra il primo con «Registra un ente».</p>
            </div>
          ) : (
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Enti serviti: data, ente, codice fiscale, piano, spazio occupato, stato e azioni</caption>
                <thead>
                  <tr>
                    <th scope="col">Dal</th>
                    <th scope="col">Ente</th>
                    <th scope="col">Codice fiscale</th>
                    <th scope="col">Piano</th>
                    <th scope="col">Spazio occupato</th>
                    <th scope="col">Stato</th>
                    <th scope="col"><span className="vd-sr">Azioni</span></th>
                  </tr>
                </thead>
                <tbody>
                  {enti.map((t: any) => {
                    const st = STATO[t.stato] ?? { testo: t.stato || "Stato sconosciuto", classe: "vd-stato--neutro" };
                    const dal = dataIt(t.data_attivazione) ?? dataIt(t.created_at);
                    const usato = typeof t.storage_usato_gb === "number" ? t.storage_usato_gb : null;
                    const quota = typeof t.storage_quota_gb === "number" && t.storage_quota_gb > 0 ? t.storage_quota_gb : null;
                    const pct = usato !== null && quota !== null ? (usato / quota) * 100 : null;
                    const isOpen = aperto === t.id;
                    const att = attivazione;
                    const inAttivazione = !!att && att.id === t.id && !att.errore;
                    return [
                      <tr key={t.id}>
                        <td className="vd-ora" style={dal ? undefined : { color: "var(--vd-testo-2)" }} data-label="Dal">
                          {dal ?? "—"}
                          <span className="vd-sotto">{t.data_attivazione ? "in esercizio" : dal ? "registrato" : "data non fornita"}</span>
                        </td>
                        <td className="vd-a-capo" data-label="Ente">
                          <strong>{t.ragione_sociale || "Ente senza nome"}</strong>
                          {t.email_referente && <span className="vd-sotto">{t.email_referente}</span>}
                        </td>
                        <td data-label="Codice fiscale"><span className="vd-mono">{t.codice_fiscale || "—"}</span></td>
                        <td data-label="Piano">{nomePiano(t.piano)}</td>
                        <td style={{ whiteSpace: "nowrap" }} data-label="Spazio occupato">
                          {usato === null ? (
                            <span className="vd-stato vd-stato--neutro">Spazio non disponibile</span>
                          ) : (
                            <>
                              {numero(usato)} GB{quota !== null ? ` di ${numero(quota, 0)}` : ""}
                              {pct !== null && (
                                <span className={`vd-sotto${pct >= 80 ? " vd-stato vd-stato--rischio" : ""}`}>
                                  {numero(pct, 0)}% della quota{pct >= 100 ? ", superata" : pct >= 80 ? ", oltre l'80%" : ""}
                                </span>
                              )}
                            </>
                          )}
                        </td>
                        <td className={`vd-stato ${st.classe}`} data-label="Stato">{st.testo}</td>
                        <td>
                          <div className="vd-comandi-cella">
                            {t.stato === "TRIAL" && (
                              <button type="button" className="vd-bottone vd-bottone--secondario" style={{ padding: "0 14px", fontSize: 15 }}
                                      onClick={() => activateMut.mutate(t.id)} disabled={activateMut.isPending}
                                      aria-label={`Metti in esercizio ${t.ragione_sociale}`}>
                                {inAttivazione ? "Attivazione…" : "Metti in esercizio"}
                              </button>
                            )}
                            <button type="button" className="vd-comando-testo" aria-expanded={isOpen} aria-controls={`vde-det-${t.id}`}
                                    onClick={() => setAperto(isOpen ? null : t.id)}>
                              {isOpen ? "Chiudi" : "Utenti e classi"}<span className="vd-sr"> di {t.ragione_sociale}</span>
                            </button>
                          </div>
                          {att && att.id === t.id && att.errore && (
                            <span role="alert" className="vd-stato vd-stato--critico" style={{ display: "block", whiteSpace: "normal", fontSize: 13 }}>
                              Non attivato: {att.errore}.
                            </span>
                          )}
                        </td>
                      </tr>,
                      isOpen && (
                        <tr key={`${t.id}-det`} id={`vde-det-${t.id}`} className="vd-dettaglio">
                          <td colSpan={7}><DettaglioEnte tenantId={t.id} /></td>
                        </tr>
                      ),
                    ];
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
