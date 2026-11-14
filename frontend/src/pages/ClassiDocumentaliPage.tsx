// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Classi documentali — sistema grafico «Partenze» (VD-DR-01).
 * Tabellone delle classi che il servizio accetta in conservazione: durata in prima
 * colonna (l'«orario» di una classe è quanto resta conservata), formati, metadati
 * obbligatori, stato. Modulo per creare e modificare con caselle vere.
 * VD-C-014: caricamento, errore e vuoto distinti; gli errori di salvataggio e di
 * attivazione si vedono (prima erano muti).
 * Correzione: l'attivazione chiamava «/api/api/v1/…» (prefisso doppio) e falliva sempre.
 */
import { FormEvent, useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { api } from "../api/client";
import { motivoErrore } from "../api/errori";

/** Motivo leggibile di un errore di chiamata. */

// Elenchi di scelta: andrebbero letti dal servizio delle regole (REGOLE §25.4); per ora sono quelli precedenti.
const FORMATI_AGID: { mime: string; nome: string }[] = [
  { mime: "application/pdf", nome: "PDF" },
  { mime: "application/pkcs7-mime", nome: "Firmato CAdES (P7M)" },
  { mime: "application/xml", nome: "XML" },
  { mime: "text/xml", nome: "XML (text/xml)" },
  { mime: "image/tiff", nome: "TIFF" },
  { mime: "image/jpeg", nome: "JPEG" },
  { mime: "image/png", nome: "PNG" },
  { mime: "text/plain", nome: "Testo semplice" },
  { mime: "application/vnd.oasis.opendocument.text", nome: "Testo OpenDocument (ODT)" },
];
const nomeFormato = (m: string) => FORMATI_AGID.find(f => f.mime === m)?.nome ?? m;

const METADATI_ALLEGATO5: { chiave: string; nome: string }[] = [
  { chiave: "identificativo_documento", nome: "Identificativo del documento" },
  { chiave: "modalita_formazione", nome: "Modalità di formazione" },
  { chiave: "tipologia_documentale", nome: "Tipologia documentale" },
  { chiave: "data_documento", nome: "Data del documento" },
  { chiave: "soggetto_produttore_id", nome: "Codice fiscale del produttore" },
  { chiave: "soggetto_produttore_nome", nome: "Nome del produttore" },
  { chiave: "oggetto", nome: "Oggetto" },
  { chiave: "lingua", nome: "Lingua" },
  { chiave: "classificazione", nome: "Classificazione" },
  { chiave: "fascicolo_id", nome: "Fascicolo" },
  { chiave: "numero_registrazione", nome: "Numero di registrazione" },
  { chiave: "data_registrazione", nome: "Data di registrazione" },
  { chiave: "tipo_firma", nome: "Tipo di firma" },
  { chiave: "soggetto_destinatario", nome: "Destinatario" },
  { chiave: "codice_ipa", nome: "Codice IPA" },
  { chiave: "aoo", nome: "Area organizzativa omogenea" },
];
const nomeMetadato = (k: string) => METADATI_ALLEGATO5.find(m => m.chiave === k)?.nome ?? k.replace(/_/g, " ");

const TIPOLOGIE: Record<string, { nome: string; descrizione: string }> = {
  DI:  { nome: "Documento informatico", descrizione: "struttura SInCRO di base" },
  DAI: { nome: "Documento amministrativo informatico", descrizione: "con metadati dell'Allegato 5 e registro" },
  AGG: { nome: "Aggregazione documentale", descrizione: "fascicolo o serie, con struttura gerarchica" },
};

interface Classe {
  id: string; codice: string; nome: string; descrizione?: string;
  tipologia_xml: string; anni_conservazione: number;
  conservazione_permanente: boolean; formati_ammessi: string[];
  metadati_obbligatori: string[]; max_dimensione_mb: number; attiva: boolean;
}

const defaultForm = {
  codice: "", nome: "", descrizione: "",
  tipologia_xml: "DAI", anni_conservazione: 10,
  conservazione_permanente: false,
  formati_ammessi: ["application/pdf", "application/pkcs7-mime"],
  metadati_obbligatori: ["identificativo_documento", "modalita_formazione",
                         "tipologia_documentale", "data_documento",
                         "soggetto_produttore_id", "soggetto_produttore_nome", "oggetto"],
  max_dimensione_mb: 100,
};

export default function ClassiDocumentaliPage() {
  const { hasRole } = useAuth();
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...defaultForm });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tentato, setTentato] = useState(false);
  const [salvata, setSalvata] = useState<string | null>(null);
  const [daDisattivare, setDaDisattivare] = useState<string | null>(null);
  const [erroreStato, setErroreStato] = useState<{ id: string; msg: string } | null>(null);
  const moduloRef = useRef<HTMLFormElement>(null);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["classi"],
    queryFn: () => api.get("/dashboard/classi-documentali?limit=100").then(r => r.data),
  });

  const chiudiModulo = () => { setShowForm(false); setEditingId(null); setForm({ ...defaultForm }); setTentato(false); };

  const createMut = useMutation({
    mutationFn: (body: any) => api.post("/dashboard/classi-documentali", body).then(r => r.data),
    onSuccess: (_r, body: any) => { qc.invalidateQueries({ queryKey: ["classi"] }); setSalvata(body?.nome ?? ""); chiudiModulo(); },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, body }: any) => api.put(`/dashboard/classi-documentali/${id}`, body).then(r => r.data),
    onSuccess: (_r, v: any) => { qc.invalidateQueries({ queryKey: ["classi"] }); setSalvata(v?.body?.nome ?? ""); chiudiModulo(); },
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, attiva }: { id: string; attiva: boolean }) =>
      api.post(`/v1/onboarding/classi-documentali/${id}/attiva?attiva=${attiva}`).then(r => r.data),
    onMutate: () => setErroreStato(null),
    onSuccess: () => { setDaDisattivare(null); qc.invalidateQueries({ queryKey: ["classi"] }); },
    onError: (e, v) => setErroreStato({ id: v.id, msg: motivoErrore(e, "operazione") }),
  });

  const canEdit = hasRole("SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE");
  const inSalvataggio = createMut.isPending || updateMut.isPending;
  const erroreSalvataggio = createMut.error ?? updateMut.error;

  // Aperto il modulo, il fuoco va al suo titolo: chi usa la tastiera non resta in fondo alla tabella.
  useEffect(() => {
    if (showForm || editingId) moduloRef.current?.querySelector<HTMLElement>("h2")?.focus();
  }, [showForm, editingId]);

  const alterna = (campo: "formati_ammessi" | "metadati_obbligatori", v: string) =>
    setForm(f => ({ ...f, [campo]: f[campo].includes(v) ? f[campo].filter(x => x !== v) : [...f[campo], v] }));

  const mancanti = [
    !form.codice.trim() && "codice",
    !form.nome.trim() && "nome",
    form.formati_ammessi.length === 0 && "almeno un formato ammesso",
    !form.conservazione_permanente && !(form.anni_conservazione > 0) && "anni di conservazione",
    !(form.max_dimensione_mb > 0) && "dimensione massima",
  ].filter(Boolean) as string[];

  const invia = (e: FormEvent) => {
    e.preventDefault();
    setTentato(true);
    if (mancanti.length) return;
    const body = { ...form, codice: form.codice.trim(), nome: form.nome.trim() };
    if (editingId) updateMut.mutate({ id: editingId, body });
    else createMut.mutate(body);
  };

  const modifica = (cl: Classe) => {
    createMut.reset(); updateMut.reset(); setSalvata(null); setTentato(false);
    setEditingId(cl.id);
    setForm({
      codice: cl.codice, nome: cl.nome,
      descrizione: cl.descrizione || "",
      tipologia_xml: cl.tipologia_xml,
      anni_conservazione: cl.anni_conservazione,
      conservazione_permanente: cl.conservazione_permanente,
      formati_ammessi: cl.formati_ammessi ?? [],
      metadati_obbligatori: cl.metadati_obbligatori ?? [],
      max_dimensione_mb: cl.max_dimensione_mb,
    });
    setShowForm(false);
  };

  const classi: Classe[] = data?.items ?? [];
  const inModifica = editingId ? classi.find(c => c.id === editingId) : null;
  const invalido = (v: boolean) => (tentato && v ? "true" : undefined);

  // Formati già salvati su una classe ma fuori dall'elenco proposto: restano visibili e selezionati.
  const formatiScelta = [...FORMATI_AGID.map(f => f.mime), ...form.formati_ammessi.filter(m => !FORMATI_AGID.some(f => f.mime === m))];
  const metadatiScelta = [...METADATI_ALLEGATO5.map(m => m.chiave), ...form.metadati_obbligatori.filter(k => !METADATI_ALLEGATO5.some(m => m.chiave === k))];

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Classi documentali</h1>
        <span className="vd-nota">
          I tipi di documento che il servizio accetta in conservazione: per quanto tempo, in quali formati e con quali metadati obbligatori.
        </span>
        {canEdit && !showForm && !editingId && (
          <div className="vd-testa-comandi">
            <button type="button" className="vd-bottone"
                    onClick={() => { createMut.reset(); setSalvata(null); setForm({ ...defaultForm }); setTentato(false); setShowForm(true); }}>
              Nuova classe
            </button>
          </div>
        )}
      </div>

      <div className="vd-pila">
        {salvata !== null && (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">Classe salvata{salvata ? `: ${salvata}` : ""}</p>
            <p>Vale per i versamenti da adesso in poi; i pacchetti già conservati mantengono le regole con cui sono entrati.</p>
          </div>
        )}

        {canEdit && (showForm || editingId) && (
          <form ref={moduloRef} className="vd-modulo" onSubmit={invia} noValidate aria-labelledby="vde-classe-titolo">
            <h2 id="vde-classe-titolo" tabIndex={-1} style={{ outline: "none" }}>
              {editingId ? `Modifica della classe «${inModifica?.nome ?? form.nome}»` : "Nuova classe documentale"}
            </h2>

            <div className="vd-griglia">
              <div className="vd-campo">
                <label htmlFor="vde-c-codice">Codice<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <input id="vde-c-codice" className="vd-input" disabled={!!editingId} aria-describedby="vde-c-codice-aiuto"
                       aria-invalid={invalido(!form.codice.trim())} value={form.codice}
                       onChange={e => setForm(f => ({ ...f, codice: e.target.value.toLowerCase() }))} />
                <span id="vde-c-codice-aiuto" className="vd-aiuto">
                  {editingId ? "Il codice non si cambia: lo usano i versamenti già fatti." : "Minuscolo, senza spazi, per esempio fattura_pa"}
                </span>
              </div>
              <div className="vd-campo">
                <label htmlFor="vde-c-nome">Nome<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                <input id="vde-c-nome" className="vd-input" aria-invalid={invalido(!form.nome.trim())} value={form.nome}
                       onChange={e => setForm(f => ({ ...f, nome: e.target.value }))} />
              </div>
              <div className="vd-campo">
                <label htmlFor="vde-c-tipo">Tipologia SInCRO</label>
                <select id="vde-c-tipo" className="vd-input" aria-describedby="vde-c-tipo-aiuto" value={form.tipologia_xml}
                        onChange={e => setForm(f => ({ ...f, tipologia_xml: e.target.value }))}>
                  {Object.entries(TIPOLOGIE).map(([v, t]) => <option key={v} value={v}>{t.nome} ({v})</option>)}
                </select>
                <span id="vde-c-tipo-aiuto" className="vd-aiuto">{TIPOLOGIE[form.tipologia_xml]?.descrizione}</span>
              </div>
              <div className="vd-campo">
                <label htmlFor="vde-c-anni">Anni di conservazione</label>
                <input id="vde-c-anni" className="vd-input" type="number" min={1} inputMode="numeric"
                       disabled={form.conservazione_permanente} aria-describedby="vde-c-anni-aiuto"
                       aria-invalid={invalido(!form.conservazione_permanente && !(form.anni_conservazione > 0))}
                       value={form.anni_conservazione}
                       onChange={e => setForm(f => ({ ...f, anni_conservazione: +e.target.value }))} />
                <span id="vde-c-anni-aiuto" className="vd-aiuto">
                  {form.conservazione_permanente ? "Non si applica: conservazione permanente." : "Dal versamento allo scarto"}
                </span>
              </div>
              <div className="vd-campo">
                <label htmlFor="vde-c-mb">Dimensione massima di un file (MB)</label>
                <input id="vde-c-mb" className="vd-input" type="number" min={1} inputMode="numeric"
                       aria-invalid={invalido(!(form.max_dimensione_mb > 0))} value={form.max_dimensione_mb}
                       onChange={e => setForm(f => ({ ...f, max_dimensione_mb: +e.target.value }))} />
              </div>
              <div className="vd-campo" style={{ justifyContent: "flex-end" }}>
                <label className="vd-casella" htmlFor="vde-c-perm" style={{ fontWeight: 600 }}>
                  <input id="vde-c-perm" type="checkbox" checked={form.conservazione_permanente}
                         onChange={e => setForm(f => ({ ...f, conservazione_permanente: e.target.checked }))} />
                  Conservazione permanente
                </label>
              </div>
            </div>

            <div className="vd-campo" style={{ maxWidth: 820 }}>
              <label htmlFor="vde-c-desc">Descrizione</label>
              <textarea id="vde-c-desc" className="vd-input" rows={2} style={{ paddingBlock: 10, resize: "vertical" }}
                        value={form.descrizione} onChange={e => setForm(f => ({ ...f, descrizione: e.target.value }))} />
            </div>

            <fieldset className="vd-caselle" aria-describedby="vde-c-formati-aiuto">
              <legend>Formati ammessi <span aria-hidden="true">*</span><span className="vd-sr">(almeno uno)</span></legend>
              <p id="vde-c-formati-aiuto" className="vd-aiuto vd-nota" style={{ fontSize: 13 }}>Allegato 2 delle Linee guida. Un file in un formato non spuntato viene rifiutato.</p>
              <div className="vd-caselle-elenco">
                {formatiScelta.map(m => (
                  <label key={m} className="vd-casella">
                    <input type="checkbox" checked={form.formati_ammessi.includes(m)} onChange={() => alterna("formati_ammessi", m)} />
                    <span>{nomeFormato(m)} <span className="vd-nota" style={{ fontSize: 13 }}>{m}</span></span>
                  </label>
                ))}
              </div>
              {tentato && form.formati_ammessi.length === 0 && (
                <span className="vd-stato vd-stato--critico">Spunta almeno un formato.</span>
              )}
            </fieldset>

            <fieldset className="vd-caselle">
              <legend>Metadati obbligatori (Allegato 5)</legend>
              <div className="vd-caselle-elenco">
                {metadatiScelta.map(k => (
                  <label key={k} className="vd-casella">
                    <input type="checkbox" checked={form.metadati_obbligatori.includes(k)} onChange={() => alterna("metadati_obbligatori", k)} />
                    <span>{nomeMetadato(k)}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="vd-comandi" style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 18 }}>
              <button type="submit" className="vd-bottone" disabled={inSalvataggio}>
                {inSalvataggio ? "Salvataggio in corso…" : editingId ? "Salva le modifiche" : "Crea la classe"}
              </button>
              <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => { createMut.reset(); updateMut.reset(); chiudiModulo(); }}>
                Annulla
              </button>
              {tentato && mancanti.length > 0 && (
                <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                  Da completare: {mancanti.join(", ")}.
                </span>
              )}
              {erroreSalvataggio && (
                <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
                  Classe non salvata: {motivoErrore(erroreSalvataggio, "operazione")}.
                </span>
              )}
            </div>
          </form>
        )}

        <section aria-labelledby="vde-tab-classi">
          <div className="vd-sezione-testa">
            <h2 id="vde-tab-classi" className="vd-etichetta">Classi</h2>
            {!isLoading && !isError && classi.length > 0 && (
              <span className="vd-nota">
                {classi.filter(c => c.attiva).length} attive su {classi.length}
              </span>
            )}
          </div>

          {isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura delle classi…</p>
          ) : isError ? (
            <div className="vd-esito vd-esito--critico" role="alert">
              <p className="vd-esito-titolo">Classi documentali non disponibili</p>
              <p>Non è possibile dire quali documenti il servizio accetta: {motivoErrore(error)}.</p>
              <p>
                <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => refetch()} disabled={isFetching}>
                  {isFetching ? "Nuova lettura in corso…" : "Riprova"}
                </button>
              </p>
            </div>
          ) : classi.length === 0 ? (
            <div className="vd-esito vd-esito--rischio">
              <p className="vd-esito-titolo">Nessuna classe documentale</p>
              <p>Senza classi il servizio rifiuta ogni versamento.{canEdit ? " Crea la prima con «Nuova classe»." : ""}</p>
            </div>
          ) : (
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Classi documentali: durata di conservazione, classe, tipologia, formati, metadati obbligatori, stato e azioni</caption>
                <thead>
                  <tr>
                    <th scope="col">Durata</th>
                    <th scope="col">Classe</th>
                    <th scope="col">Tipologia SInCRO</th>
                    <th scope="col">Formati</th>
                    <th scope="col">Metadati obbligatori</th>
                    <th scope="col">Stato</th>
                    <th scope="col"><span className="vd-sr">Azioni</span></th>
                  </tr>
                </thead>
                <tbody>
                  {classi.map((cl: Classe) => {
                    const formati = cl.formati_ammessi ?? [];
                    const metadati = cl.metadati_obbligatori ?? [];
                    const tipo = TIPOLOGIE[cl.tipologia_xml];
                    const isOpen = expandedId === cl.id;
                    const conferma = daDisattivare === cl.id;
                    const inCambio = toggleMut.isPending && toggleMut.variables?.id === cl.id;
                    return [
                      <tr key={cl.id}>
                        <td className="vd-ora" data-label="Durata">
                          {cl.conservazione_permanente ? "Permanente" : typeof cl.anni_conservazione === "number" ? `${cl.anni_conservazione} anni` : "—"}
                          {!cl.conservazione_permanente && typeof cl.anni_conservazione !== "number" && <span className="vd-sotto">durata non fornita</span>}
                        </td>
                        {/* g3-frontend: a 1024px i comandi in riga schiacciavano la classe; ora la classe ha una larghezza minima e i comandi vanno a capo */}
                        <td className="vd-a-capo" style={{ minWidth: 180 }} data-label="Classe">
                          <strong>{cl.nome || cl.codice}</strong>
                          <span className="vd-sotto vd-mono">{cl.codice}</span>
                        </td>
                        <td data-label="Tipologia SInCRO">
                          {tipo ? tipo.nome : cl.tipologia_xml || "—"}
                          <span className="vd-sotto">{cl.tipologia_xml}</span>
                        </td>
                        <td className="vd-a-capo" data-label="Formati">
                          {formati.length === 0
                            ? <span className="vd-stato vd-stato--rischio">Nessuno</span>
                            : formati.slice(0, 3).map(nomeFormato).join(", ") + (formati.length === 4 ? " e un altro" : formati.length > 4 ? ` e altri ${formati.length - 3}` : "")}
                        </td>
                        <td style={{ whiteSpace: "nowrap" }} data-label="Metadati obbligatori">{metadati.length} {metadati.length === 1 ? "metadato" : "metadati"}</td>
                        <td className={`vd-stato ${cl.attiva ? "vd-stato--ok" : "vd-stato--neutro"}`} data-label="Stato">{cl.attiva ? "Attiva" : "Non attiva"}</td>
                        <td>
                          <div className="vd-comandi-cella" style={{ columnGap: 14 }}>
                            <button type="button" className="vd-comando-testo" aria-expanded={isOpen} aria-controls={`vde-cl-${cl.id}`}
                                    onClick={() => setExpandedId(isOpen ? null : cl.id)}>
                              {isOpen ? "Chiudi" : "Dettaglio"}<span className="vd-sr"> di {cl.nome}</span>
                            </button>
                            {canEdit && !conferma && (
                              <>
                                <button type="button" className="vd-comando-testo" onClick={() => modifica(cl)}>
                                  Modifica<span className="vd-sr"> {cl.nome}</span>
                                </button>
                                {cl.attiva ? (
                                  <button type="button" className="vd-comando-testo" onClick={() => { setErroreStato(null); setDaDisattivare(cl.id); }}>
                                    Disattiva<span className="vd-sr"> {cl.nome}</span>
                                  </button>
                                ) : (
                                  <button type="button" className="vd-comando-testo" disabled={inCambio}
                                          onClick={() => toggleMut.mutate({ id: cl.id, attiva: true })}>
                                    {inCambio ? "Attivazione…" : "Attiva"}<span className="vd-sr"> {cl.nome}</span>
                                  </button>
                                )}
                              </>
                            )}
                          </div>
                          {canEdit && conferma && (
                            <div className="vd-comandi-cella" role="group" aria-label={`Conferma la disattivazione di ${cl.nome}`}>
                              <span style={{ fontSize: 14 }}>I nuovi versamenti di questa classe verranno rifiutati.</span>
                              <button type="button" className="vd-bottone" style={{ padding: "0 14px", fontSize: 15 }} autoFocus
                                      disabled={inCambio} onClick={() => toggleMut.mutate({ id: cl.id, attiva: false })}>
                                {inCambio ? "Disattivazione…" : "Disattiva"}
                              </button>
                              <button type="button" className="vd-comando-testo" onClick={() => setDaDisattivare(null)}>Annulla</button>
                            </div>
                          )}
                          {erroreStato?.id === cl.id && (
                            <span role="alert" className="vd-stato vd-stato--critico" style={{ display: "block", whiteSpace: "normal", fontSize: 13 }}>
                              Stato non cambiato: {erroreStato.msg}.
                            </span>
                          )}
                        </td>
                      </tr>,
                      isOpen && (
                        <tr key={`${cl.id}-det`} id={`vde-cl-${cl.id}`} className="vd-dettaglio">
                          <td colSpan={7}>
                            <div className="vd-dettaglio-griglia">
                              <section aria-label={`Formati ammessi di ${cl.nome}`}>
                                <h3>Formati ammessi ({formati.length})</h3>
                                {formati.length === 0 ? <p className="vd-nota">Nessun formato: ogni file verrebbe rifiutato.</p> : (
                                  <ul className="vd-elenco">
                                    {formati.map(f => <li key={f}><span>{nomeFormato(f)}</span><span className="vd-mono">{f}</span></li>)}
                                  </ul>
                                )}
                                <p className="vd-nota" style={{ marginTop: 6 }}>
                                  Dimensione massima di un file: {typeof cl.max_dimensione_mb === "number" ? `${cl.max_dimensione_mb} MB` : "non fornita"}
                                </p>
                              </section>
                              <section aria-label={`Metadati obbligatori di ${cl.nome}`}>
                                <h3>Metadati obbligatori ({metadati.length})</h3>
                                {metadati.length === 0 ? <p className="vd-nota">Nessuno.</p> : (
                                  <ul className="vd-elenco">
                                    {metadati.map(m => <li key={m}><span>{nomeMetadato(m)}</span></li>)}
                                  </ul>
                                )}
                                {cl.descrizione && <p style={{ marginTop: 10, fontSize: 15 }}>{cl.descrizione}</p>}
                              </section>
                            </div>
                          </td>
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
