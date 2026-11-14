// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Canali PEC (R13b, VD-C-078). Sistema «Partenze» con la revisione R12c: testa di pagina
 * chiusa dal binario (dalla console delle impostazioni), colonna unica, tabelloni con
 * l'ora in prima colonna e impilati su telefono, stati vuoti con `Vuoto`, dettagli in
 * un dialogo laterale.
 *  - impostazioni del gruppo «pec» (PEC_CASI_USO anche per ente, attesa della consegna);
 *  - conservatore (permesso mai delegabile «pec_canali:gestisci»): canali con lo stato
 *    scritto dal lavoratore della posta (segreti presenti sì/no, problemi, ultimo invio
 *    e ultima lettura), registrazione senza segreti, prova d'invio a un indirizzo di
 *    prova, messaggi della casella non associati o non autentici;
 *  - conservatore e amministratore dell'ente (solo il proprio): messaggi PEC con lo
 *    stato fino alla ricevuta di consegna e le ricevute (messaggio grezzo scaricabile).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, useCursori } from "../components/Paginazione";
import { Vuoto } from "../components/Vuoto";
import { motivoErrore } from "../api/errori";
import { DialogoLaterale, InLettura, quando, quandoBreve } from "../console/comuni";
import {
  CAMPI_PEC, CASO_PEC, ESITO_RICEVUTA, STATO_PEC, TIPO_RICEVUTA, leggiCanaliPec, leggiMessaggioPec,
  leggiMessaggiPec, leggiRicevuteNonAssociate, provaCanalePec, salvaCanalePec, scaricaEml,
  type CanalePec, type MessaggioPec, type RegistroPec, type RicevutaPec,
} from "../console/pec";
import { ConsoleImpostazioni } from "./ConfImpostazioniPage";

const LIMITE = 20;
const parola = (mappa: Record<string, { testo: string; classe: string }>, k: string | null) =>
  mappa[k ?? ""] ?? { testo: k ?? "—", classe: "vd-stato--neutro" };

function OraCella({ iso, etichetta }: { iso: string | null | undefined; etichetta: string }) {
  const q = quando(iso);
  return (
    <td className="vd-ora" data-label={etichetta}>
      {q ? <>{q.ora}<span className="vd-sotto vd-sotto-normale">{q.giorno}</span></> : <span className="vd-ora--attesa">—</span>}
    </td>
  );
}

// ── Canali (conservatore) ─────────────────────────────────────────────

function statoCanale(c: CanalePec): { testo: string; classe: string; nota?: string } {
  if (!c.attivo) return { testo: "Disattivato", classe: "vd-stato--neutro" };
  if (!c.stato) return { testo: "Mai visto dal lavoratore", classe: "vd-stato--rischio",
    nota: "Il lavoratore della posta non ha ancora letto il canale: controlla che sia in esecuzione." };
  if (c.stato.problemi.length) return { testo: "Non utilizzabile", classe: "vd-stato--critico" };
  if (c.stato.ultimo_errore) return { testo: "Con errori", classe: "vd-stato--rischio" };
  return { testo: "Pronto", classe: "vd-stato--ok" };
}

function Prova({ c, onChiudi }: { c: CanalePec; onChiudi: () => void }) {
  const [dest, setDest] = useState("");
  const prova = useMutation({ mutationFn: () => provaCanalePec(c.codice, dest.trim()) });
  const id = `vd-pec-prova-${c.codice}`;
  return (
    <form className="vd-conferma-riga" noValidate onSubmit={e => { e.preventDefault(); if (dest.trim()) prova.mutate(); }}>
      <label htmlFor={id} className="vd-forte">Indirizzo PEC di prova</label>
      <input id={id} className="vd-input vd-mono" type="email" value={dest} spellCheck={false}
             placeholder="prova@pec.esempio.it" onChange={e => setDest(e.target.value)} />
      <span className="vd-comandi-cella">
        <button type="submit" className="vd-bottone vd-bottone--secondario" disabled={prova.isPending || !dest.trim()}>
          {prova.isPending ? "Invio…" : "Invia la prova"}</button>
        <button type="button" className="vd-comando-testo" onClick={onChiudi}>Annulla</button>
      </span>
      <span role="status">
        {prova.isSuccess && <span className="vd-stato vd-stato--ok vd-a-capo">Prova accodata: la segui fra i messaggi qui sotto{prova.data.canale_attivo ? "" : " (il canale è disattivato: parte quando lo attivi)"}.</span>}
        {prova.isError && <span className="vd-stato vd-stato--critico vd-a-capo">{motivoErrore(prova.error, "operazione")}</span>}
      </span>
    </form>
  );
}

function testoConf(v: unknown): string {
  return v === undefined || v === null ? "" : String(v);
}

function FormCanale({ c, registro, onFatto }: { c: CanalePec | null; registro: RegistroPec; onFatto: () => void }) {
  const qc = useQueryClient();
  const nuovo = c === null;
  const [codice, setCodice] = useState(c?.codice ?? "");
  const [tipo, setTipo] = useState<string>(c?.tipo ?? "SMTP_IMAP");
  const [nome, setNome] = useState(c?.nome ?? "");
  const [attivo, setAttivo] = useState(c?.attivo ?? false);
  const [priorita, setPriorita] = useState(String(c?.priorita ?? 10));
  const [conf, setConf] = useState<Record<string, string>>(
    Object.fromEntries(CAMPI_PEC.map(k => [k.chiave, testoConf(c?.configurazione[k.chiave])])));
  const salva = useMutation({
    mutationFn: () => salvaCanalePec(codice.trim(), {
      tipo, nome: nome.trim(), attivo, priorita: Number(priorita), versione: c?.versione ?? 0,
      configurazione: Object.fromEntries(CAMPI_PEC.map(k => [k.chiave, k.tipo === "numero"
        ? (conf[k.chiave].trim() ? Number(conf[k.chiave]) : undefined) : conf[k.chiave].trim()])
        .filter(([, v]) => v !== undefined && v !== "")),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pec-canali"] }); onFatto(); },
  });
  const id = (k: string) => `vd-pec-${c?.codice ?? "nuovo"}-${k}`;
  const segreti = registro.segreti[tipo] ?? [];
  return (
    <form className="vd-modulo" onSubmit={e => { e.preventDefault(); salva.mutate(); }}>
      <div className="vd-griglia">
        <div className="vd-campo"><label htmlFor={id("codice")}>Codice</label>
          <input id={id("codice")} className="vd-input vd-mono" value={codice} disabled={!nuovo} spellCheck={false}
                 onChange={e => setCodice(e.target.value)} aria-describedby={id("codice-aiuto")} />
          <span id={id("codice-aiuto")} className="vd-aiuto">Minuscole, cifre e trattini (es. pec-principale).</span></div>
        <div className="vd-campo"><label htmlFor={id("tipo")}>Tipo</label>
          <select id={id("tipo")} className="vd-input" value={tipo} disabled={!nuovo} onChange={e => setTipo(e.target.value)}>
            {registro.tipi.filter(t => t !== "SVILUPPO" || registro.sviluppo_ammesso).map(t =>
              <option key={t} value={t}>{t === "SMTP_IMAP" ? "Gestore PEC (SMTP e IMAP)" : "Simulatore di sviluppo"}</option>)}</select></div>
        <div className="vd-campo"><label htmlFor={id("nome")}>Nome</label>
          <input id={id("nome")} className="vd-input" value={nome} onChange={e => setNome(e.target.value)} /></div>
        <div className="vd-campo"><label htmlFor={id("priorita")}>Priorità</label>
          <input id={id("priorita")} className="vd-input" inputMode="numeric" value={priorita}
                 onChange={e => setPriorita(e.target.value)} aria-describedby={id("priorita-aiuto")} />
          <span id={id("priorita-aiuto")} className="vd-aiuto">Il primo canale attivo e utilizzabile invia.</span></div>
        {CAMPI_PEC.map(k => (
          <div className="vd-campo" key={k.chiave}>
            <label htmlFor={id(k.chiave)}>{k.etichetta}</label>
            {k.tipo === "scelta" ? (
              <select id={id(k.chiave)} className="vd-input" value={conf[k.chiave] || (k.scelte?.[0] ?? "")}
                      onChange={e => setConf({ ...conf, [k.chiave]: e.target.value })}>
                {k.scelte?.map(s => <option key={s} value={s}>{s}</option>)}</select>
            ) : k.tipo === "pem" ? (
              <textarea id={id(k.chiave)} className="vd-input vd-textarea vd-mono" value={conf[k.chiave]} spellCheck={false}
                        onChange={e => setConf({ ...conf, [k.chiave]: e.target.value })}
                        aria-describedby={k.aiuto ? id(`${k.chiave}-aiuto`) : undefined} />
            ) : (
              <input id={id(k.chiave)} className="vd-input vd-mono" value={conf[k.chiave]} spellCheck={false}
                     inputMode={k.tipo === "numero" ? "numeric" : undefined}
                     onChange={e => setConf({ ...conf, [k.chiave]: e.target.value })}
                     aria-describedby={k.aiuto ? id(`${k.chiave}-aiuto`) : undefined} />
            )}
            {k.aiuto && <span id={id(`${k.chiave}-aiuto`)} className="vd-aiuto">{k.aiuto}</span>}
          </div>
        ))}
      </div>
      <label className="vd-scelta"><input type="checkbox" checked={attivo} onChange={e => setAttivo(e.target.checked)} /><span>Attivo</span></label>
      <p className="vd-nota">La password non si scrive qui: va nel Secret del lavoratore della posta ({registro.variabile_segreti}, voce
        «{codice || "codice"}», con gli host del gestore a cui legarla){segreti.length ? `: ${segreti.map(s => `${s.nome}${s.obbligatorio ? "" : " (facoltativo)"}`).join(", ")}` : ""}.</p>
      {salva.isError && <EsitoCritico titolo="Canale non salvato" errore={salva.error} azione="operazione" />}
      <div className="vd-comandi"><button type="submit" className="vd-bottone" disabled={salva.isPending}>
        {nuovo ? "Registra il canale" : `Salva ${c?.codice}`}</button></div>
    </form>
  );
}

function Canali() {
  const q = useQuery({ queryKey: ["pec-canali"], queryFn: leggiCanaliPec });
  const [modifica, setModifica] = useState<CanalePec | "nuovo" | null>(null);
  const [prova, setProva] = useState<string | null>(null);
  const registra = <button type="button" className="vd-bottone vd-bottone--secondario" aria-haspopup="dialog" onClick={() => setModifica("nuovo")}>Registra un canale</button>;
  return (
    <section aria-labelledby="vd-pec-canali">
      <div className="vd-sezione-testa"><h2 id="vd-pec-canali" className="vd-etichetta">Canali</h2>{q.data && q.data.items.length > 0 && registra}</div>
      {q.isLoading ? <InLettura testo="Lettura dei canali PEC…" />
        : q.isError || !q.data ? <EsitoCritico titolo="Canali non disponibili" errore={q.error} spiegazione="Non significa che non ce ne siano." onRiprova={() => q.refetch()} />
        : q.data.items.length === 0 ? (
          <Vuoto titolo="Nessun canale PEC registrato" azione={registra}>
            <p>Finché non registri la casella PEC del conservatore, nessuna comunicazione parte per PEC: i messaggi restano in coda e poi «non partiti» con il motivo.</p>
          </Vuoto>
        ) : (
          <table className="vd-tabellone vd-impila">
            <caption className="vd-sr">Canali PEC, in ordine di priorità</caption>
            <thead><tr><th scope="col">Ultimo invio</th><th scope="col">Canale</th><th scope="col">Stato</th><th scope="col">Comandi</th></tr></thead>
            <tbody>
              {q.data.items.map(c => {
                const st = statoCanale(c);
                const mancanti = Object.entries(c.stato?.segreti_presenti ?? {}).filter(([, v]) => !v).map(([n]) => n);
                return (
                  <tr key={c.codice}>
                    <OraCella iso={c.stato?.ultimo_invio_il} etichetta="Ultimo invio" />
                    <td data-label="Canale" className="vd-a-capo">
                      <span className="vd-forte">{c.nome}</span>
                      <span className="vd-sotto"><span className="vd-mono">{c.codice}</span> · priorità {c.priorita} · {c.tipo === "SVILUPPO" ? "simulatore" : "gestore PEC"}</span>
                      <span className="vd-sotto vd-mono">{testoConf(c.configurazione.indirizzo)}</span>
                    </td>
                    <td data-label="Stato" style={{ whiteSpace: "normal" }}>
                      <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
                      {st.nota && <span className="vd-sotto vd-sotto-normale">{st.nota}</span>}
                      {c.stato?.problemi.map(p => <span key={p} className="vd-sotto vd-sotto-normale vd-a-capo">{p}</span>)}
                      {mancanti.length > 0 && <span className="vd-sotto vd-sotto-normale">Segreti mancanti: {mancanti.join(", ")}</span>}
                      {c.stato?.ultimo_errore && <span className="vd-sotto vd-sotto-normale vd-a-capo">{c.stato.ultimo_errore}</span>}
                      {c.stato?.ultima_lettura_il && <span className="vd-sotto vd-sotto-normale">ricevute lette {quandoBreve(c.stato.ultima_lettura_il)}</span>}
                    </td>
                    <td data-label="Comandi">
                      {prova === c.codice ? <Prova c={c} onChiudi={() => setProva(null)} /> : (
                        <span className="vd-comandi-cella">
                          <button type="button" className="vd-comando-testo" onClick={() => setProva(c.codice)}>Prova d'invio</button>
                          <button type="button" className="vd-comando-testo" aria-haspopup="dialog" onClick={() => setModifica(c)}>Modifica</button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      {modifica && q.data && (
        <DialogoLaterale id="vd-pec-modifica" titolo={modifica === "nuovo" ? "Nuovo canale PEC" : `Canale ${modifica.codice}`}
                         sottotitolo="Ogni modifica è nell'audit; il lavoratore della posta la usa al giro successivo."
                         onClose={() => setModifica(null)}>
          <FormCanale c={modifica === "nuovo" ? null : modifica} registro={q.data} onFatto={() => setModifica(null)} />
        </DialogoLaterale>
      )}
    </section>
  );
}

// ── Messaggi (conservatore ed ente) ───────────────────────────────────

function RigaRicevuta({ r }: { r: RicevutaPec }) {
  const scarica = useMutation({
    mutationFn: () => scaricaEml(r.id),
    onSuccess: b => { const u = URL.createObjectURL(b); const a = document.createElement("a"); a.href = u; a.download = `ricevuta-${r.id}.eml`; a.click(); URL.revokeObjectURL(u); },
  });
  const es = parola(ESITO_RICEVUTA, r.esito);
  return (
    <tr>
      <OraCella iso={r.data_ricevuta ?? r.ricevuta_il} etichetta="Ora" />
      <td data-label="Ricevuta" className="vd-a-capo">
        <span className="vd-forte">{TIPO_RICEVUTA[r.tipo ?? ""] ?? r.tipo ?? "Messaggio non riconosciuto"}</span>
        {r.gestore && <span className="vd-sotto">gestore {r.gestore}</span>}
        {r.consegna && <span className="vd-sotto vd-mono">{r.consegna}</span>}
        {r.errore && r.errore !== "nessuno" && <span className="vd-sotto vd-stato--critico">{r.errore}{r.errore_esteso ? `: ${r.errore_esteso}` : ""}</span>}
        {r.certificato_soggetto && <span className="vd-sotto vd-sotto-normale">firmata da {r.certificato_soggetto}</span>}
        {r.motivo && <span className="vd-sotto vd-sotto-normale">{r.motivo}</span>}
      </td>
      <td data-label="Esito" style={{ whiteSpace: "normal" }}>
        <span className={`vd-stato ${es.classe}`}>{es.testo}</span>
        {r.dimensione > 0 && <span className="vd-sotto"><button type="button" className="vd-comando-testo" disabled={scarica.isPending} onClick={() => scarica.mutate()}>Scarica il messaggio</button></span>}
        {scarica.isError && <span className="vd-sotto vd-stato--critico">{motivoErrore(scarica.error, "operazione")}</span>}
      </td>
    </tr>
  );
}

function Dettaglio({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["pec-messaggio", id], queryFn: () => leggiMessaggioPec(id) });
  const m = q.data;
  return (
    <DialogoLaterale id="vd-pec-dettaglio" titolo="Messaggio PEC" sottotitolo={m?.oggetto} onClose={onClose}>
      {q.isLoading ? <InLettura testo="Lettura del messaggio…" />
        : q.isError || !m ? <EsitoCritico titolo="Messaggio non disponibile" errore={q.error} onRiprova={() => q.refetch()} />
        : (
          <div className="vd-pila">
            <dl className="vd-dati">
              <dt>Stato</dt><dd><span className={`vd-stato ${parola(STATO_PEC, m.stato).classe}`}>{parola(STATO_PEC, m.stato).testo}</span>{m.avviso && <span className="vd-sotto vd-stato--rischio">{m.avviso}</span>}</dd>
              <dt>Caso</dt><dd>{CASO_PEC[m.caso_uso] ?? m.caso_uso}</dd>
              <dt>Destinatario</dt><dd className="vd-mono vd-a-capo">{m.destinatario}</dd>
              <dt>Canale</dt><dd className="vd-mono">{m.canale ?? "—"}</dd>
              <dt>Message-ID</dt><dd className="vd-mono vd-a-capo">{m.message_id ?? "assegnato all'invio"}</dd>
              <dt>Tentativi</dt><dd>{m.tentativi}{m.errore ? ` · ${m.errore}` : ""}</dd>
            </dl>
            <section aria-labelledby="vd-pec-ricevute">
              <div className="vd-sezione-testa"><h3 id="vd-pec-ricevute" className="vd-etichetta">Ricevute</h3></div>
              {m.ricevute.length === 0 ? <Vuoto titolo="Nessuna ricevuta ancora"><p>Le ricevute di accettazione e di consegna arrivano nella casella del conservatore e compaiono qui al giro successivo del lavoratore.</p></Vuoto> : (
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Ricevute del messaggio, dalla prima</caption>
                  <thead><tr><th scope="col">Ora</th><th scope="col">Ricevuta</th><th scope="col">Esito</th></tr></thead>
                  <tbody>{m.ricevute.map(r => <RigaRicevuta key={r.id} r={r} />)}</tbody>
                </table>
              )}
            </section>
          </div>
        )}
    </DialogoLaterale>
  );
}

function Messaggi({ conservatore }: { conservatore: boolean }) {
  const [stato, setStato] = useState("");
  const [caso, setCaso] = useState("");
  const [aperto, setAperto] = useState<string | null>(null);
  const cursori = useCursori();
  const q = useQuery({
    queryKey: ["pec-messaggi", stato, caso, cursori.cursore],
    queryFn: () => leggiMessaggiPec({ stato, caso_uso: caso, dopo: cursori.cursore ?? undefined, limite: LIMITE }),
  });
  const righe: MessaggioPec[] = q.data?.items ?? [];
  return (
    <section aria-labelledby="vd-pec-messaggi">
      <div className="vd-sezione-testa"><h2 id="vd-pec-messaggi" className="vd-etichetta">Messaggi</h2></div>
      <div className="vd-filtri">
        <div className="vd-campo"><label htmlFor="vd-pec-stato">Stato</label>
          <select id="vd-pec-stato" className="vd-input" value={stato} onChange={e => { setStato(e.target.value); cursori.ricomincia(); }}>
            <option value="">Tutti</option>{Object.entries(STATO_PEC).map(([k, v]) => <option key={k} value={k}>{v.testo}</option>)}</select></div>
        <div className="vd-campo"><label htmlFor="vd-pec-caso">Caso</label>
          <select id="vd-pec-caso" className="vd-input" value={caso} onChange={e => { setCaso(e.target.value); cursori.ricomincia(); }}>
            <option value="">Tutti</option>{Object.entries(CASO_PEC).filter(([k]) => conservatore || k !== "PROVA").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
      </div>
      {q.isLoading ? <InLettura testo="Lettura dei messaggi PEC…" />
        : q.isError ? <EsitoCritico titolo="Messaggi non disponibili" errore={q.error} spiegazione="Non significa che non ce ne siano." onRiprova={() => q.refetch()} />
        : righe.length === 0 ? (
          <Vuoto titolo={stato || caso ? "Nessun messaggio con questi filtri" : "Nessun messaggio PEC"}>
            <p>{conservatore ? "I messaggi nascono dalle pratiche e dalle scadenze degli enti che li hanno scelti (PEC_CASI_USO) e dalle prove d'invio." : "Per ricevere per PEC le comunicazioni formali, scegli i casi d'uso nelle impostazioni qui sopra e controlla l'indirizzo PEC del referente."}</p>
          </Vuoto>
        ) : (
          <>
            <table className="vd-tabellone vd-impila" aria-busy={q.isFetching}>
              <caption className="vd-sr">Messaggi PEC, dal più recente</caption>
              <thead><tr><th scope="col">Ora</th><th scope="col">Comunicazione</th><th scope="col">Stato</th><th scope="col">Comandi</th></tr></thead>
              <tbody>
                {righe.map(m => {
                  const st = parola(STATO_PEC, m.stato);
                  return (
                    <tr key={m.id}>
                      <OraCella iso={m.creato_il} etichetta="Ora" />
                      <td data-label="Comunicazione" className="vd-a-capo">
                        <span className="vd-forte">{m.oggetto.replace(/^\[VerumDoc\] /, "")}</span>
                        <span className="vd-sotto">{CASO_PEC[m.caso_uso] ?? m.caso_uso} · <span className="vd-mono">{m.destinatario}</span></span>
                      </td>
                      <td data-label="Stato" style={{ whiteSpace: "normal" }}>
                        <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
                        {m.avviso && <span className="vd-sotto vd-stato--rischio">{m.avviso}</span>}
                        {m.consegnato_il && <span className="vd-sotto vd-sotto-normale">consegnato {quandoBreve(m.consegnato_il)}</span>}
                        {!m.consegnato_il && m.accettato_il && <span className="vd-sotto vd-sotto-normale">accettato {quandoBreve(m.accettato_il)}</span>}
                        {m.errore && ["NON_PARTITO", "MANCATA_CONSEGNA", "NON_ACCETTATO", "IN_CODA"].includes(m.stato) && <span className="vd-sotto vd-sotto-normale vd-a-capo">{m.errore}</span>}
                      </td>
                      <td data-label="Comandi"><button type="button" className="vd-comando-testo" aria-haspopup="dialog" onClick={() => setAperto(m.id)}>Ricevute</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <Paginazione cursori={cursori} prossimo={q.data?.cursore ?? null} righe={righe.length} limite={LIMITE} nome="Messaggi" etichetta="Pagine dei messaggi PEC" />
          </>
        )}
      {aperto && <Dettaglio id={aperto} onClose={() => setAperto(null)} />}
    </section>
  );
}

// ── Messaggi della casella non associati (conservatore) ───────────────

function NonAssociate() {
  const q = useQuery({ queryKey: ["pec-non-associate"], queryFn: () => leggiRicevuteNonAssociate() });
  return (
    <section aria-labelledby="vd-pec-estranee">
      <div className="vd-sezione-testa"><h2 id="vd-pec-estranee" className="vd-etichetta">Ricevute non associate</h2></div>
      <p className="vd-nota">Messaggi della casella che non hanno cambiato lo stato di nessun messaggio: firma non valida o di un gestore non fra le radici, ricevute di altri messaggi, PEC in arrivo. Restano come evidenza.</p>
      {q.isLoading ? <InLettura testo="Lettura…" />
        : q.isError || !q.data ? <EsitoCritico titolo="Elenco non disponibile" errore={q.error} onRiprova={() => q.refetch()} />
        : q.data.items.length === 0 ? <Vuoto titolo="Nessun messaggio da controllare"><p>Ogni messaggio letto dalla casella è una ricevuta autentica di un messaggio inviato.</p></Vuoto>
        : (
          <table className="vd-tabellone vd-impila">
            <caption className="vd-sr">Messaggi della casella non associati, dal più recente</caption>
            <thead><tr><th scope="col">Ora</th><th scope="col">Ricevuta</th><th scope="col">Esito</th></tr></thead>
            <tbody>{q.data.items.map(r => <RigaRicevuta key={r.id} r={r} />)}</tbody>
          </table>
        )}
    </section>
  );
}

export default function CanaliPecPage() {
  const { user } = useAuth();
  const conservatore = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  return (
    <>
      <ConsoleImpostazioni gruppo="pec" conRegole={false} titolo="Canali PEC"
        spiegazione="Le comunicazioni formali all'ente (scarto, restituzione, riversamento, scadenze) partono anche per PEC, con le ricevute del gestore. Quali casi usano la PEC lo sceglie PEC_CASI_USO, anche per ente." />
      {/* stessa distanza fra sezioni maggiori della pagina (--vd-sezione) */}
      <div className="vd-pila vd-pila--dopo">
        {conservatore && <Canali />}
        <Messaggi conservatore={conservatore} />
        {conservatore && <NonAssociate />}
      </div>
    </>
  );
}
