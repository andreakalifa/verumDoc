// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Fornitori di firma e marca (R13a; VD-C-068, VD-C-124): solo i ruoli fissi del
 * conservatore (permesso mai delegabile «firma_fornitori:gestisci», ricontrollato
 * da onboarding e dal servizio di firma).
 *  - stato: fornitori costruiti, problemi, segreti presenti (mai i valori),
 *    certificato e giorni alla scadenza, «Prova» con firma di prova verificata;
 *  - registro: un fornitore per codice, ruolo FIRMA (CSC, PKCS#11, sviluppo) o
 *    MARCA (TSA RFC 3161, sviluppo), priorità (il successivo è il ripiego);
 *  - parametri del gruppo «firma_qualificata» con la console delle impostazioni.
 * Sistema R12c: testa di pagina della console (chiusa dal binario), colonna unica,
 * tabellone impilato su telefono, stato vuoto con `Vuoto`.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { EsitoCritico } from "../components/EsitoCritico";
import { Vuoto } from "../components/Vuoto";
import {
  CAMPI_FIRMA, leggiRegistro, leggiStatoFirma, provaFornitore, salvaFornitoreFirma,
  type FornitoreFirma, type Registro, type StatoFornitore,
} from "../console/firma";
import { InLettura, quandoBreve } from "../console/comuni";
import { ConsoleImpostazioni } from "./ConfImpostazioniPage";

function Scadenza({ giorni }: { giorni?: number }) {
  if (giorni === undefined) return <span>—</span>;
  const classe = giorni < 30 ? "vd-stato vd-stato--critico" : giorni < 90 ? "vd-stato vd-stato--rischio" : "vd-stato vd-stato--ok";
  return <span className={classe}>{giorni} giorni</span>;
}

function RigaStato({ f }: { f: StatoFornitore }) {
  const prova = useMutation({ mutationFn: () => provaFornitore(f.codice) });
  const mancanti = Object.entries(f.segreti_presenti).filter(([, c]) => !c).map(([n]) => n);
  return (
    <tr>
      <td data-label="Codice" className="vd-mono vd-a-capo">{f.codice}</td>
      <td data-label="Ruolo">{f.ruolo === "FIRMA" ? "Firma" : "Marca"} · {f.tipo}</td>
      <td data-label="Priorità">{f.priorita}</td>
      <td data-label="Stato">
        {f.costruito ? <span className="vd-stato vd-stato--ok">attivo</span> : <span className="vd-stato vd-stato--critico">non utilizzabile</span>}
        {f.problemi.length > 0 && <ul className="vd-nota">{f.problemi.map(p => <li key={p}>{p}</li>)}</ul>}
        {mancanti.length > 0 && <p className="vd-nota">Segreti mancanti: {mancanti.join(", ")}</p>}
      </td>
      <td data-label="Certificato">
        {f.certificato ? (f.certificato.raggiungibile
          ? <><span className="vd-a-capo">{f.certificato.soggetto}</span><br /><Scadenza giorni={f.certificato.giorni_alla_scadenza} />
              {f.certificato.dichiara_qualificato ? " · QcCompliance" : " · non qualificato"}</>
          : <span className="vd-stato vd-stato--critico">{f.certificato.errore}</span>)
          : f.tsa ? <span className="vd-mono vd-a-capo">{f.tsa.host}</span> : "—"}
      </td>
      <td data-label="Prova">
        <button type="button" className="vd-bottone vd-bottone--secondario" disabled={!f.costruito || prova.isPending}
                onClick={() => prova.mutate()} aria-describedby={`vd-prova-${f.codice}`}>
          {prova.isPending ? "Prova in corso…" : "Prova"}
        </button>
        <div id={`vd-prova-${f.codice}`} role="status">
          {prova.isSuccess && <p className="vd-nota">Riuscita in {prova.data.durata_ms} ms
            {prova.data.livello ? `, livello ${prova.data.livello}` : ""}{prova.data.verificata ? ", verificata" : ""}.</p>}
          {prova.isError && <EsitoCritico titolo="Prova non riuscita" errore={prova.error} azione="operazione" />}
        </div>
      </td>
    </tr>
  );
}

function Stato() {
  const q = useQuery({ queryKey: ["firma-stato"], queryFn: leggiStatoFirma });
  if (q.isLoading) return <InLettura testo="Lettura dello stato dei fornitori…" />;
  if (q.isError || !q.data) return <EsitoCritico titolo="Stato dei fornitori non disponibile" errore={q.error} onRiprova={() => q.refetch()} />;
  const s = q.data;
  return (
    <section aria-labelledby="vd-firma-stato">
      <div className="vd-sezione-testa"><h2 id="vd-firma-stato" className="vd-etichetta">Stato</h2></div>
      <p className="vd-nota">
        {s.production_ready ? "Firma e marca qualificate pronte per l'esercizio." : "Firma qualificata non ancora pronta: i documenti si dichiarano non firmati o restano in attesa, secondo FIRMA_SENZA_FORNITORE."}
        {" "}Origine della configurazione: {s.origine === "registro" ? "registro della console" : "variabili d'ambiente"}.
        {" "}Livello {s.parametri.livello}, impronta {s.parametri.algoritmo}.
        {s.errore_lettura && <> Ultima lettura del registro non riuscita ({s.errore_lettura}).</>}
      </p>
      {s.fornitori.length === 0 ? (
        <Vuoto titolo="Nessun fornitore registrato e attivo">
          <p>Registra qui sotto un fornitore di firma e una TSA, metti i segreti nel Secret del servizio di firma (FIRMA_SEGRETI_JSON) e usa «Prova».</p>
        </Vuoto>
      ) : (
        <table className="vd-tabellone vd-impila">
          <caption className="vd-sr">Fornitori di firma e marca attivi</caption>
          <thead><tr><th scope="col">Codice</th><th scope="col">Ruolo</th><th scope="col">Priorità</th>
            <th scope="col">Stato</th><th scope="col">Certificato</th><th scope="col">Prova</th></tr></thead>
          <tbody>{s.fornitori.map(f => <RigaStato key={f.codice} f={f} />)}</tbody>
        </table>
      )}
    </section>
  );
}

function testoCampo(v: unknown, tipo?: string): string {
  if (v === undefined || v === null) return "";
  if (tipo === "lista" && Array.isArray(v)) return v.join("\n");
  return String(v);
}

function valoreCampo(t: string, tipo?: string): unknown {
  if (tipo === "numero") return t.trim() === "" ? undefined : Number(t);
  if (tipo === "lista") return t.split(/[\n,;]+/).map(x => x.trim()).filter(Boolean);
  if (tipo === "si_no") return t === "true";
  return t;
}

function FormFornitore({ f, registro, nuovo }: { f: FornitoreFirma; registro: Registro; nuovo: boolean }) {
  const qc = useQueryClient();
  const [codice, setCodice] = useState(f.codice);
  const [ruolo, setRuolo] = useState(f.ruolo);
  const [tipo, setTipo] = useState(f.tipo);
  const [nome, setNome] = useState(f.nome);
  const [attivo, setAttivo] = useState(f.attivo);
  const [priorita, setPriorita] = useState(String(f.priorita));
  const [conf, setConf] = useState<Record<string, string>>(
    Object.fromEntries((CAMPI_FIRMA[f.tipo] ?? []).map(c => [c.chiave, testoCampo(f.configurazione[c.chiave], c.tipo)])));
  const campi = CAMPI_FIRMA[tipo] ?? [];
  const salva = useMutation({
    mutationFn: () => salvaFornitoreFirma(codice.trim(), {
      ruolo, tipo, nome, attivo, priorita: Number(priorita), versione: f.versione,
      configurazione: Object.fromEntries(campi.map(c => [c.chiave, valoreCampo(conf[c.chiave] ?? "", c.tipo)])
        .filter(([, v]) => v !== undefined && v !== "")),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["firma-registro"] }); qc.invalidateQueries({ queryKey: ["firma-stato"] }); },
  });
  const id = (c: string) => `vd-ff-${f.codice || "nuovo"}-${c}`;
  const segreti = registro.segreti[tipo] ?? [];
  return (
    <form className="vd-modulo" onSubmit={e => { e.preventDefault(); salva.mutate(); }}>
      <h3>{nuovo ? "Nuovo fornitore" : `${f.nome} (${f.codice})`}</h3>
      {!nuovo && <p className="vd-nota">Versione {f.versione}, ultima modifica {quandoBreve(f.aggiornato_il)}.</p>}
      <div className="vd-griglia">
        <div className="vd-campo"><label htmlFor={id("codice")}>Codice</label>
          <input id={id("codice")} className="vd-input vd-mono" value={codice} disabled={!nuovo} spellCheck={false}
                 onChange={e => setCodice(e.target.value)} aria-describedby={id("codice-aiuto")} />
          <span id={id("codice-aiuto")} className="vd-aiuto">Minuscole, cifre e trattini (es. csc-principale).</span></div>
        <div className="vd-campo"><label htmlFor={id("ruolo")}>Ruolo</label>
          <select id={id("ruolo")} className="vd-input" value={ruolo} disabled={!nuovo}
                  onChange={e => { const r = e.target.value as "FIRMA" | "MARCA"; setRuolo(r); setTipo(registro.ruoli[r][0]); }}>
            <option value="FIRMA">Firma o sigillo</option><option value="MARCA">Marca temporale</option></select></div>
        <div className="vd-campo"><label htmlFor={id("tipo")}>Tipo</label>
          <select id={id("tipo")} className="vd-input" value={tipo} disabled={!nuovo} onChange={e => setTipo(e.target.value)}>
            {(registro.ruoli[ruolo] ?? []).filter(t => t !== "SVILUPPO" || registro.sviluppo_ammesso)
              .map(t => <option key={t} value={t}>{t}</option>)}</select></div>
        <div className="vd-campo"><label htmlFor={id("nome")}>Nome</label>
          <input id={id("nome")} className="vd-input" value={nome} onChange={e => setNome(e.target.value)} /></div>
        <div className="vd-campo"><label htmlFor={id("priorita")}>Priorità</label>
          <input id={id("priorita")} className="vd-input" inputMode="numeric" value={priorita}
                 onChange={e => setPriorita(e.target.value)} aria-describedby={id("priorita-aiuto")} />
          <span id={id("priorita-aiuto")} className="vd-aiuto">1 si prova per primo; il successivo è il ripiego.</span></div>
        {campi.map(c => (
          <div className="vd-campo" key={c.chiave}>
            <label htmlFor={id(c.chiave)}>{c.etichetta}</label>
            {c.tipo === "scelta" ? (
              <select id={id(c.chiave)} className="vd-input" value={conf[c.chiave] || (c.scelte?.[0] ?? "")}
                      onChange={e => setConf({ ...conf, [c.chiave]: e.target.value })}>
                {c.scelte?.map(s => <option key={s} value={s}>{s}</option>)}</select>
            ) : c.tipo === "si_no" ? (
              <select id={id(c.chiave)} className="vd-input" value={conf[c.chiave] || "false"}
                      onChange={e => setConf({ ...conf, [c.chiave]: e.target.value })}>
                <option value="false">no</option><option value="true">sì</option></select>
            ) : c.tipo === "pem" || c.tipo === "lista" ? (
              <textarea id={id(c.chiave)} className="vd-input vd-textarea vd-mono" value={conf[c.chiave] ?? ""} spellCheck={false}
                        onChange={e => setConf({ ...conf, [c.chiave]: e.target.value })}
                        aria-describedby={c.aiuto ? id(`${c.chiave}-aiuto`) : undefined} />
            ) : (
              <input id={id(c.chiave)} className="vd-input vd-mono" value={conf[c.chiave] ?? ""} spellCheck={false}
                     inputMode={c.tipo === "numero" ? "numeric" : undefined}
                     onChange={e => setConf({ ...conf, [c.chiave]: e.target.value })}
                     aria-describedby={c.aiuto ? id(`${c.chiave}-aiuto`) : undefined} />
            )}
            {c.aiuto && <span id={id(`${c.chiave}-aiuto`)} className="vd-aiuto">{c.aiuto}</span>}
          </div>
        ))}
      </div>
      <label className="vd-scelta"><input type="checkbox" checked={attivo} onChange={e => setAttivo(e.target.checked)} /><span>Attivo</span></label>
      {segreti.length > 0 && <p className="vd-nota">Segreti da mettere nel Secret del servizio di firma (FIRMA_SEGRETI_JSON, voce «{codice || "codice"}»):{" "}
        {segreti.map(s => `${s.nome}${s.obbligatorio ? "" : " (facoltativo)"}`).join(", ")}.</p>}
      {salva.isError && <EsitoCritico titolo="Fornitore non salvato" errore={salva.error} azione="operazione" />}
      {salva.isSuccess && <p className="vd-nota" role="status">Salvato. Il servizio di firma lo usa entro mezzo minuto.</p>}
      <div className="vd-comandi"><button type="submit" className="vd-bottone vd-bottone--secondario" disabled={salva.isPending}>
        {nuovo ? "Registra il fornitore" : `Salva ${f.codice}`}</button></div>
    </form>
  );
}

const VUOTO: FornitoreFirma = { codice: "", ruolo: "FIRMA", tipo: "CSC", nome: "", attivo: false, priorita: 10,
  configurazione: {}, versione: 0, aggiornato_il: "" };

function RegistroFornitori() {
  const q = useQuery({ queryKey: ["firma-registro"], queryFn: leggiRegistro });
  if (q.isLoading) return <InLettura testo="Lettura del registro dei fornitori…" />;
  if (q.isError || !q.data) return <EsitoCritico titolo="Registro non disponibile" errore={q.error} onRiprova={() => q.refetch()} />;
  return (
    <section aria-labelledby="vd-firma-registro">
      <div className="vd-sezione-testa"><h2 id="vd-firma-registro" className="vd-etichetta">Registro dei fornitori</h2></div>
      <p className="vd-nota">Ogni modifica è registrata nell'audit. Ruolo e tipo di un fornitore registrato non cambiano: si disattiva e se ne registra uno nuovo.</p>
      {q.data.items.length === 0 && (
        <Vuoto titolo="Nessun fornitore nel registro">
          <p>Il servizio usa la configurazione delle variabili d'ambiente finché non registri il primo fornitore con il modulo qui sotto.</p>
        </Vuoto>
      )}
      <div className="vd-pila">
        {q.data.items.map(f => <FormFornitore key={`${f.codice}-${f.versione}`} f={f} registro={q.data} nuovo={false} />)}
        <FormFornitore key={`nuovo-${q.data.items.length}`} f={VUOTO} registro={q.data} nuovo />
      </div>
    </section>
  );
}

export default function ConfFirmaQualificataPage() {
  const { user } = useAuth();
  const conservatore = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  return (
    <>
      <ConsoleImpostazioni gruppo="firma_qualificata" conRegole={false}
        titolo="Firma e marca qualificate"
        spiegazione="Il sigillo del conservatore su indici dei pacchetti, rapporti di versamento, manuale e piano, la marca dei checkpoint e la rimarcatura prima delle scadenze. Cosa succede senza un fornitore qualificato lo decide FIRMA_SENZA_FORNITORE." />
      {conservatore && <div className="vd-pila"><Stato /><RegistroFornitori /></div>}
    </>
  );
}
