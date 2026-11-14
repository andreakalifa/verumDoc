// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Utenti e accessi — sistema grafico «Partenze» (VD-DR-01).
 * Due tabelloni: le persone dell'ente e le chiavi API dei sistemi versanti.
 * Una chiave generata si mostra una sola volta, con avviso e comando di copia.
 * VD-C-014: caricamento, errore e vuoto sono stati distinti (il servizio risponde
 * 503 con un riferimento quando non riesce a leggere: mai 200 con un campo d'errore).
 * R02 (g2-auth): nome, cognome e codice fiscale delle persone (accesso SPID/CIE:
 * il servizio riconosce la persona dal codice fiscale); codice fiscale indicabile
 * anche sugli utenti esistenti; email o codice fiscale già presenti → 409 mostrato.
 * Chiavi API: tolto l'avviso «Da sapere», gateway e onboarding usano ora la stessa
 * rappresentazione (libs/shared/security/chiavi_api.py).
 * Le chiamate (fetchUtenti, createUtente, disattivaUtente, fetchApiKeys,
 * generateApiKey, revokaApiKey) sono quelle precedenti.
 */
import { giorno } from "../console/comuni";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchUtenti, createUtente, updateUtente, disattivaUtente,
  fetchApiKeys, generateApiKey, revokaApiKey,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { NOME_RUOLO } from "../auth/ruoli";
import { motivoErrore } from "../api/errori";

/** Revisione R17 (R17-20): giorno in ora italiana (Europe/Rome), non il giorno UTC. */
const dataIt = (iso?: string | null) => (iso ? giorno(iso) ?? String(iso) : null);


/** Ruoli che l'amministrazione dell'ente può assegnare da qui (come prima: operatore e consultazione). */
const RUOLI_ASSEGNABILI = [
  { value: "OPERATORE", label: "Operatore", aiuto: "versa e consulta i documenti" },
  { value: "VIEWER", label: "Consultazione", aiuto: "solo lettura" },
];

function Esito({ tono, titolo, children, ruolo }: { tono?: "critico" | "rischio" | "ok"; titolo: string; children?: React.ReactNode; ruolo?: "alert" | "status" }) {
  return (
    <div className={`vd-esito${tono ? ` vd-esito--${tono}` : ""}`} role={ruolo}>
      <p className="vd-esito-titolo">{titolo}</p>
      {children}
    </div>
  );
}

// ── Persone ─────────────────────────────────────────────────────────
/** Forma del codice fiscale di una persona (16 caratteri). Il controllo completo,
 * con il carattere di controllo, lo fa il servizio: qui solo un aiuto immediato. */
const formaCf = (v: string) => /^[A-Z0-9]{16}$/.test(v.replace(/\s+/g, "").toUpperCase().replace(/^TINIT-/, ""));
const normalizzaCf = (v: string) => v.replace(/\s+/g, "").toUpperCase().replace(/^TINIT-/, "");

const MODULO_VUOTO = { email: "", nome: "", cognome: "", codice_fiscale: "", ruolo: "OPERATORE" };

/** Codice fiscale di un utente esistente: aggiunta o correzione in linea. */
function CellaCodiceFiscale({ tenantId, utente }: { tenantId: string; utente: any }) {
  const qc = useQueryClient();
  const [aperto, setAperto] = useState(false);
  const [valore, setValore] = useState("");
  const mut = useMutation({
    mutationFn: () => updateUtente(tenantId, utente.id, { codice_fiscale: normalizzaCf(valore) }),
    onSuccess: () => { setAperto(false); qc.invalidateQueries({ queryKey: ["utenti", tenantId] }); },
  });
  const chi = [utente.nome, utente.cognome].filter(Boolean).join(" ") || utente.email;
  if (!aperto) {
    return (
      <>
        {utente.codice_fiscale
          ? <span className="vd-mono">{utente.codice_fiscale}</span>
          : <span className="vd-stato vd-stato--rischio" style={{ whiteSpace: "normal" }}>Non indicato: non può entrare con SPID o CIE</span>}
        <span className="vd-sotto">
          <button type="button" className="vd-comando-testo" style={{ minHeight: 0, padding: "4px 0", fontSize: 14 }}
                  onClick={() => { mut.reset(); setValore(utente.codice_fiscale ?? ""); setAperto(true); }}>
            {utente.codice_fiscale ? "Correggi" : "Indica il codice fiscale"}<span className="vd-sr"> di {chi}</span>
          </button>
        </span>
      </>
    );
  }
  const id = `vd-cf-${utente.id}`;
  return (
    <form className="vd-comandi-cella" onSubmit={e => { e.preventDefault(); if (formaCf(valore)) mut.mutate(); }}
          aria-label={`Codice fiscale di ${chi}`}>
      <label htmlFor={id} className="vd-sr">Codice fiscale di {chi}</label>
      <input id={id} className="vd-input vd-mono" style={{ maxWidth: 220 }} autoFocus autoComplete="off" spellCheck={false}
             maxLength={22} value={valore} aria-invalid={valore && !formaCf(valore) ? "true" : undefined}
             onChange={e => setValore(e.target.value)} />
      <button type="submit" className="vd-bottone" style={{ padding: "0 14px", fontSize: 15 }}
              disabled={mut.isPending || !formaCf(valore)}>
        {mut.isPending ? "Salvataggio…" : "Salva"}
      </button>
      <button type="button" className="vd-comando-testo" onClick={() => setAperto(false)}>Annulla</button>
      {valore && !formaCf(valore) && (
        <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>16 caratteri, lettere e cifre.</span>
      )}
      {mut.isError && (
        <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>
          Non salvato: {motivoErrore(mut.error, "operazione")}.
        </span>
      )}
    </form>
  );
}

function SezioneUtenti({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(MODULO_VUOTO);
  const [tentato, setTentato] = useState(false);
  const [creato, setCreato] = useState<string | null>(null);
  const [daDisattivare, setDaDisattivare] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["utenti", tenantId],
    queryFn: () => fetchUtenti(tenantId),
    enabled: !!tenantId,
  });

  const createMut = useMutation({
    mutationFn: () => createUtente(tenantId, {
      email: form.email.trim(), nome: form.nome.trim(), cognome: form.cognome.trim(),
      codice_fiscale: normalizzaCf(form.codice_fiscale), ruolo: form.ruolo,
    }),
    onSuccess: (u: any) => {
      qc.invalidateQueries({ queryKey: ["utenti", tenantId] });
      setCreato([u?.nome, u?.cognome].filter(Boolean).join(" ") || form.email.trim());
      setForm(MODULO_VUOTO);
      setShowForm(false);
      setTentato(false);
    },
  });

  const disattivaMut = useMutation({
    mutationFn: (uid: string) => disattivaUtente(tenantId, uid),
    onSuccess: () => { setDaDisattivare(null); qc.invalidateQueries({ queryKey: ["utenti", tenantId] }); },
  });

  const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim());
  const nomeOk = form.nome.trim().length >= 2;
  const cognomeOk = form.cognome.trim().length >= 1;
  const cfOk = formaCf(form.codice_fiscale);
  const mancanti = [
    !nomeOk && "nome (almeno 2 caratteri)", !cognomeOk && "cognome",
    !cfOk && "codice fiscale di 16 caratteri", !emailOk && "email valida",
  ].filter(Boolean) as string[];
  const invalido = (v: boolean) => (tentato && v ? "true" : undefined);

  const invia = (e: FormEvent) => {
    e.preventDefault();
    setTentato(true);
    if (mancanti.length) return;
    createMut.mutate();
  };

  const utenti: any[] = q.data?.items ?? [];

  return (
    <section aria-labelledby="vde-persone" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="vd-sezione-testa" style={{ marginBottom: 0 }}>
        <h2 id="vde-persone" className="vd-etichetta">Persone</h2>
        {!showForm && (
          <button type="button" className="vd-bottone vd-bottone--secondario"
                  onClick={() => { setShowForm(true); setCreato(null); createMut.reset(); }}>
            Aggiungi un utente
          </button>
        )}
      </div>
      <p className="vd-nota" style={{ maxWidth: "72ch", fontSize: 15 }}>
        Le persone entrano con SPID o CIE: il servizio le riconosce dal codice fiscale registrato qui.
        Chi non ha il codice fiscale indicato non può entrare.
      </p>

      {creato && (
        <Esito tono="ok" titolo="Utente aggiunto" ruolo="status">
          <p className="vd-a-capo">{creato} può entrare con SPID o CIE usando l'identità digitale del codice fiscale indicato.</p>
        </Esito>
      )}

      {showForm && (
        <form className="vd-modulo" onSubmit={invia} noValidate aria-labelledby="vde-nuovo-utente">
          <h3 id="vde-nuovo-utente" style={{ fontSize: 22, fontWeight: 800, fontStretch: "85%" }}>Aggiungi un utente</h3>
          <div className="vd-griglia">
            <div className="vd-campo">
              <label htmlFor="vde-u-nome">Nome<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
              <input id="vde-u-nome" className="vd-input" autoFocus autoComplete="off" aria-invalid={invalido(!nomeOk)} value={form.nome}
                     onChange={e => setForm(f => ({ ...f, nome: e.target.value }))} />
            </div>
            <div className="vd-campo">
              <label htmlFor="vde-u-cognome">Cognome<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
              <input id="vde-u-cognome" className="vd-input" autoComplete="off" aria-invalid={invalido(!cognomeOk)} value={form.cognome}
                     onChange={e => setForm(f => ({ ...f, cognome: e.target.value }))} />
            </div>
            <div className="vd-campo">
              <label htmlFor="vde-u-cf">Codice fiscale<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
              <input id="vde-u-cf" className="vd-input vd-mono" autoComplete="off" spellCheck={false} maxLength={22}
                     aria-describedby="vde-u-cf-aiuto" aria-invalid={invalido(!cfOk)} value={form.codice_fiscale}
                     onChange={e => setForm(f => ({ ...f, codice_fiscale: e.target.value }))} />
              <span id="vde-u-cf-aiuto" className="vd-aiuto">Quello della persona: serve per entrare con SPID o CIE. Il servizio ne verifica il carattere di controllo.</span>
            </div>
            <div className="vd-campo">
              <label htmlFor="vde-u-email">Email<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
              <input id="vde-u-email" type="email" className="vd-input" autoComplete="off"
                     aria-invalid={invalido(!emailOk)} value={form.email}
                     onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
            </div>
            <div className="vd-campo">
              <label htmlFor="vde-u-ruolo">Ruolo</label>
              <select id="vde-u-ruolo" className="vd-input" aria-describedby="vde-u-ruolo-aiuto" value={form.ruolo}
                      onChange={e => setForm(f => ({ ...f, ruolo: e.target.value }))}>
                {RUOLI_ASSEGNABILI.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
              <span id="vde-u-ruolo-aiuto" className="vd-aiuto">
                {RUOLI_ASSEGNABILI.find(r => r.value === form.ruolo)?.aiuto}
              </span>
            </div>
          </div>
          <div className="vd-comandi" style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 18 }}>
            <button type="submit" className="vd-bottone" disabled={createMut.isPending}>
              {createMut.isPending ? "Creazione in corso…" : "Aggiungi l'utente"}
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
                Utente non aggiunto: {motivoErrore(createMut.error, "operazione")}.
              </span>
            )}
          </div>
        </form>
      )}

      {q.isLoading ? (
        <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura degli utenti…</p>
      ) : q.isError ? (
        <Esito tono="critico" titolo="Elenco degli utenti non disponibile" ruolo="alert">
          <p>Non è possibile dire chi accede al servizio: {motivoErrore(q.error)}.</p>
          <p>
            <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => q.refetch()} disabled={q.isFetching}>
              {q.isFetching ? "Nuova lettura in corso…" : "Riprova"}
            </button>
          </p>
        </Esito>
      ) : utenti.length === 0 ? (
        <Esito titolo="Nessun utente">
          <p>L'ente non ha ancora utenti. Aggiungi il primo con «Aggiungi un utente».</p>
        </Esito>
      ) : (
        <div className="vd-scorri">
          <table className="vd-tabellone vd-impila">
            <caption className="vd-sr">Utenti dell'ente: data di creazione, persona, codice fiscale, ruolo, stato e azioni</caption>
            <thead>
              <tr>
                <th scope="col">Dal</th>
                <th scope="col">Persona</th>
                <th scope="col">Codice fiscale</th>
                <th scope="col">Ruolo</th>
                <th scope="col">Stato</th>
                <th scope="col"><span className="vd-sr">Azioni</span></th>
              </tr>
            </thead>
            <tbody>
              {utenti.map((u: any) => {
                const dal = dataIt(u.created_at);
                const conferma = daDisattivare === u.id;
                const chi = [u.nome, u.cognome].filter(Boolean).join(" ");
                return (
                  <tr key={u.id}>
                    <td className="vd-ora" style={dal ? undefined : { color: "var(--vd-testo-2)" }} data-label="Dal">
                      {dal ?? "—"}{!dal && <span className="vd-sotto">data non fornita</span>}
                    </td>
                    <td className="vd-a-capo" data-label="Persona">
                      <strong>{chi || "Senza nome"}</strong>
                      {u.email && <span className="vd-sotto">{u.email}</span>}
                    </td>
                    <td className="vd-a-capo" data-label="Codice fiscale"><CellaCodiceFiscale tenantId={tenantId} utente={u} /></td>
                    <td data-label="Ruolo">{NOME_RUOLO[u.ruolo] ?? u.ruolo ?? "—"}</td>
                    <td className={`vd-stato ${u.attivo ? "vd-stato--ok" : "vd-stato--neutro"}`} data-label="Stato">{u.attivo ? "Attivo" : "Disattivato"}</td>
                    <td>
                      {u.attivo && !conferma && (
                        <button type="button" className="vd-comando-testo" onClick={() => { disattivaMut.reset(); setDaDisattivare(u.id); }}>
                          Disattiva<span className="vd-sr"> {chi || u.email}</span>
                        </button>
                      )}
                      {u.attivo && conferma && (
                        <div className="vd-comandi-cella" role="group" aria-label={`Conferma la disattivazione di ${chi || u.email}`}>
                          <span style={{ fontSize: 14 }}>Non potrà più accedere.</span>
                          <button type="button" className="vd-bottone" style={{ padding: "0 14px", fontSize: 15 }}
                                  onClick={() => disattivaMut.mutate(u.id)} disabled={disattivaMut.isPending} autoFocus>
                            {disattivaMut.isPending ? "Disattivazione…" : "Conferma"}
                          </button>
                          <button type="button" className="vd-comando-testo" onClick={() => setDaDisattivare(null)}>Annulla</button>
                          {disattivaMut.isError && (
                            <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>
                              Non disattivato: {motivoErrore(disattivaMut.error, "operazione")}.
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ── Chiavi API ──────────────────────────────────────────────────────
function SezioneChiavi({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [descrizione, setDescrizione] = useState("");
  const [nuova, setNuova] = useState<{ chiave: string; identificativo: string; descrizione: string } | null>(null);
  const [copia, setCopia] = useState<"" | "fatta" | "fallita">("");
  const [daRevocare, setDaRevocare] = useState<string | null>(null);
  const avvisoRef = useRef<HTMLDivElement>(null);

  const q = useQuery({
    queryKey: ["api-keys", tenantId],
    queryFn: () => fetchApiKeys(tenantId),
    enabled: !!tenantId,
  });

  const generateMut = useMutation({
    mutationFn: () => generateApiKey(tenantId, { descrizione: descrizione.trim() }),
    onSuccess: (result: any) => {
      qc.invalidateQueries({ queryKey: ["api-keys", tenantId] });
      setNuova(result?.api_key
        ? { chiave: result.api_key, identificativo: result.api_key_id ?? `vd_${result.key_prefix ?? ""}`, descrizione: result.descrizione ?? descrizione.trim() }
        : null);
      setCopia("");
      setDescrizione("");
      setShowForm(false);
    },
  });

  const revocaMut = useMutation({
    mutationFn: (kid: string) => revokaApiKey(tenantId, kid),
    onSuccess: () => { setDaRevocare(null); qc.invalidateQueries({ queryKey: ["api-keys", tenantId] }); },
  });

  // La chiave appena generata riceve il fuoco: chi usa un lettore di schermo la trova subito.
  useEffect(() => { if (nuova) avvisoRef.current?.focus(); }, [nuova]);

  const copiaChiave = async () => {
    if (!nuova) return;
    try {
      await navigator.clipboard.writeText(nuova.chiave);
      setCopia("fatta");
    } catch {
      setCopia("fallita");
    }
  };

  const chiavi: any[] = q.data?.items ?? [];

  return (
    <section aria-labelledby="vde-chiavi" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="vd-sezione-testa" style={{ marginBottom: 0 }}>
        <h2 id="vde-chiavi" className="vd-etichetta">Chiavi API dei sistemi versanti</h2>
        {!showForm && !nuova && (
          <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => { setShowForm(true); generateMut.reset(); }}>
            Genera una chiave
          </button>
        )}
      </div>
      <p className="vd-nota" style={{ maxWidth: "72ch", fontSize: 15 }}>
        Il protocollo informatico o la contabilità dell'ente usano una chiave per versare senza una persona collegata.
        Ogni sistema ha la sua chiave: se un sistema viene dismesso, si revoca solo quella.
      </p>

      {nuova && (
        <div ref={avvisoRef} tabIndex={-1} className="vd-esito vd-esito--rischio" role="region" aria-labelledby="vde-nuova-titolo" style={{ maxWidth: 820, outline: "none" }}>
          <p id="vde-nuova-titolo" className="vd-esito-titolo">Copia la chiave adesso: non sarà più mostrata</p>
          <p>
            Questa è l'unica volta in cui la chiave si legge per intero{nuova.descrizione ? <> («{nuova.descrizione}»)</> : null}.
            Il servizio ne conserva solo l'impronta: se la perdi, va revocata e generata di nuovo.
          </p>
          <p>
            Il sistema versante la invia in ogni chiamata nell'intestazione <span className="vd-mono">X-Api-Key</span>
            {" "}(con l'SDK: <span className="vd-mono">X-Api-Key-Id</span> = <span className="vd-mono">{nuova.identificativo}</span> e
            {" "}<span className="vd-mono">X-Api-Key-Secret</span> = la chiave).
          </p>
          <code className="vd-chiave" aria-label="Chiave API">{nuova.chiave}</code>
          <div className="vd-comandi">
            <button type="button" className="vd-bottone" onClick={copiaChiave}>Copia la chiave</button>
            <button type="button" className="vd-bottone vd-bottone--secondario"
                    onClick={() => { setNuova(null); setCopia(""); }}>
              Ho conservato la chiave
            </button>
            <span aria-live="polite" className={`vd-stato ${copia === "fallita" ? "vd-stato--critico" : "vd-stato--ok"}`} style={{ whiteSpace: "normal" }}>
              {copia === "fatta" ? "Copiata negli appunti." : copia === "fallita" ? "Copia non riuscita: seleziona la chiave e copiala a mano." : ""}
            </span>
          </div>
        </div>
      )}

      {showForm && (
        <form className="vd-modulo" aria-labelledby="vde-nuova-chiave"
              onSubmit={e => { e.preventDefault(); generateMut.mutate(); }}>
          <h3 id="vde-nuova-chiave" style={{ fontSize: 22, fontWeight: 800, fontStretch: "85%" }}>Genera una chiave</h3>
          <div className="vd-campo" style={{ maxWidth: 560 }}>
            <label htmlFor="vde-k-desc">Sistema che la userà</label>
            <input id="vde-k-desc" className="vd-input" autoFocus aria-describedby="vde-k-desc-aiuto" value={descrizione}
                   onChange={e => setDescrizione(e.target.value)} />
            <span id="vde-k-desc-aiuto" className="vd-aiuto">Facoltativo. Per riconoscerla, per esempio «Protocollo informatico, versamento notturno».</span>
          </div>
          <div className="vd-comandi" style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 18 }}>
            <button type="submit" className="vd-bottone" disabled={generateMut.isPending}>
              {generateMut.isPending ? "Generazione in corso…" : "Genera la chiave"}
            </button>
            <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => { setShowForm(false); generateMut.reset(); }}>
              Annulla
            </button>
            {generateMut.isError && (
              <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                Chiave non generata: {motivoErrore(generateMut.error, "operazione")}.
              </span>
            )}
            {generateMut.isSuccess && !nuova && (
              <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                Il servizio non ha restituito la chiave: revoca quella appena creata e riprova.
              </span>
            )}
          </div>
        </form>
      )}

      {q.isLoading ? (
        <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura delle chiavi…</p>
      ) : q.isError ? (
        <Esito tono="critico" titolo="Elenco delle chiavi non disponibile" ruolo="alert">
          <p>Non è possibile dire quali sistemi possono versare: {motivoErrore(q.error)}.</p>
          <p>
            <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => q.refetch()} disabled={q.isFetching}>
              {q.isFetching ? "Nuova lettura in corso…" : "Riprova"}
            </button>
          </p>
        </Esito>
      ) : chiavi.length === 0 ? (
        <Esito titolo="Nessuna chiave">
          <p>Nessun sistema dell'ente versa con una chiave API.</p>
        </Esito>
      ) : (
        <div className="vd-scorri">
          <table className="vd-tabellone vd-impila">
            <caption className="vd-sr">Chiavi API: data di creazione, chiave e sistema, ultimo uso, stato e azioni</caption>
            <thead>
              <tr>
                <th scope="col">Creata</th>
                <th scope="col">Chiave e sistema</th>
                <th scope="col">Ultimo uso</th>
                <th scope="col">Stato</th>
                <th scope="col"><span className="vd-sr">Azioni</span></th>
              </tr>
            </thead>
            <tbody>
              {chiavi.map((k: any) => {
                const creata = dataIt(k.created_at);
                const conferma = daRevocare === k.id;
                return (
                  <tr key={k.id}>
                    <td className="vd-ora" style={creata ? undefined : { color: "var(--vd-testo-2)" }} data-label="Creata">
                      {creata ?? "—"}{!creata && <span className="vd-sotto">data non fornita</span>}
                    </td>
                    <td className="vd-a-capo" data-label="Chiave e sistema">
                      <span className="vd-mono" title="Si mostra solo l'inizio della chiave">vd_{k.key_prefix}…</span>
                      <span className="vd-sotto">{k.descrizione || "Sistema non indicato"}</span>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }} data-label="Ultimo uso">
                      {k.last_used_at ? dataIt(k.last_used_at) : <span className="vd-stato vd-stato--neutro">Mai usata</span>}
                    </td>
                    <td className={`vd-stato ${k.attiva ? "vd-stato--ok" : "vd-stato--neutro"}`} data-label="Stato">{k.attiva ? "Attiva" : "Revocata"}</td>
                    <td>
                      {k.attiva && !conferma && (
                        <button type="button" className="vd-comando-testo" onClick={() => { revocaMut.reset(); setDaRevocare(k.id); }}>
                          Revoca<span className="vd-sr"> la chiave vd_{k.key_prefix}</span>
                        </button>
                      )}
                      {k.attiva && conferma && (
                        <div className="vd-comandi-cella" role="group" aria-label={`Conferma la revoca della chiave vd_${k.key_prefix}`}>
                          <span style={{ fontSize: 14 }}>La revoca è definitiva: il sistema smetterà di versare.</span>
                          <button type="button" className="vd-bottone" style={{ padding: "0 14px", fontSize: 15 }}
                                  onClick={() => revocaMut.mutate(k.id)} disabled={revocaMut.isPending} autoFocus>
                            {revocaMut.isPending ? "Revoca…" : "Revoca"}
                          </button>
                          <button type="button" className="vd-comando-testo" onClick={() => setDaRevocare(null)}>Annulla</button>
                          {revocaMut.isError && (
                            <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal", fontSize: 13 }}>
                              Non revocata: {motivoErrore(revocaMut.error, "operazione")}.
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ── Pagina ──────────────────────────────────────────────────────────
export default function UtentiPage() {
  const { user } = useAuth();
  const tenantId = user?.tenant_id ?? "";

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Utenti e chiavi API</h1>
        <span className="vd-nota">
          Chi accede al servizio per conto {user?.ragione_sociale ? <>di <strong>{user.ragione_sociale}</strong></> : "dell'ente"} e
          con quali chiavi versano i suoi sistemi.
        </span>
      </div>
      {!tenantId ? (
        <Esito tono="critico" titolo="Ente non riconosciuto" ruolo="alert">
          <p>La sessione non indica a quale ente appartieni: esci e accedi di nuovo.</p>
        </Esito>
      ) : (
        <div className="vd-pila">
          <SezioneUtenti tenantId={tenantId} />
          <SezioneChiavi tenantId={tenantId} />
        </div>
      )}
    </>
  );
}
