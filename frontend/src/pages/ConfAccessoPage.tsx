// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Accesso e notifiche (R10: D08, D12, D30, D32): stessa console di «Impostazioni e regole»,
 * un gruppo per scheda (bozza, anteprima, applicazione, verso «più sicuro»), più i fornitori
 * d'identità per il conservatore (onboarding /api/v1/onboarding/accesso/fornitori).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { EsitoCritico } from "../components/EsitoCritico";
import { leggiFornitori, salvaFornitore, type Fornitore } from "../console/accesso";
import { InLettura, quandoBreve } from "../console/comuni";
import { ConsoleImpostazioni } from "./ConfImpostazioniPage";

const GRUPPI = [
  { gruppo: "accesso", nome: "Metodi d'accesso",
    spiegazione: "Con quali metodi le persone accedono, quanto vale un invito, i tentativi prima del blocco e la lunghezza della password. Il conservatore abilita i metodi; l'ente può solo toglierne." },
  { gruppo: "ruoli", nome: "Ruoli personalizzati",
    spiegazione: "Quali permessi l'ente può mettere nei propri ruoli. Le azioni più delicate (scarto, approvazione degli annullamenti, firmatari, gestione degli utenti) non si delegano mai." },
  { gruppo: "notifiche", nome: "Avvisi obbligatori",
    spiegazione: "Avvisi che le persone non possono spegnere, per categoria e canale (console o e-mail). L'ente può solo aggiungerne." },
  { gruppo: "sftp", nome: "Indirizzi SFTP",
    spiegazione: "Limiti dell'elenco di indirizzi da cui un account SFTP si collega, e se l'elenco è obbligatorio." },
];

const CAMPI: Record<string, { chiave: string; etichetta: string; aiuto?: string }[]> = {
  SPID_CIE: [
    { chiave: "aggregatore_url", etichetta: "Indirizzo dell'aggregatore" },
    { chiave: "client_id", etichetta: "Identificativo del cliente (client_id)", aiuto: "Il segreto del cliente non si scrive qui: resta nelle variabili del servizio." },
    { chiave: "issuer", etichetta: "Emittente dei token" },
    { chiave: "jwks_url", etichetta: "Indirizzo delle chiavi pubbliche (JWKS)" },
  ],
  PASSKEY: [{ chiave: "rp_id", etichetta: "Dominio della console", aiuto: "Vuoto: quello della console. Le passkey valgono solo su questo dominio." }],
  PASSWORD_TOTP: [],
};

function FormFornitore({ f }: { f: Fornitore }) {
  const qc = useQueryClient();
  const [attivo, setAttivo] = useState(f.attivo);
  const [conf, setConf] = useState<Record<string, unknown>>({ livello_minimo: 2, ...f.configurazione });
  const salva = useMutation({ mutationFn: () => salvaFornitore(f.tipo, { nome: f.nome, attivo, configurazione: f.tipo === "PASSWORD_TOTP" ? {} : conf, versione: f.versione }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["fornitori"] }) });
  return (
    <form className="vd-modulo" onSubmit={e => { e.preventDefault(); salva.mutate(); }}>
      <h3>{f.nome}</h3>
      <p className="vd-nota">{f.registrato ? `Ultima modifica ${quandoBreve(f.aggiornato_il)}.` : "Non ancora registrato."}</p>
      <label className="vd-scelta"><input type="checkbox" checked={attivo} onChange={e => setAttivo(e.target.checked)} /><span>Attivo</span></label>
      <div className="vd-griglia">
        {CAMPI[f.tipo].map(c => (
          <div className="vd-campo" key={c.chiave}>
            <label htmlFor={`vd-f-${f.tipo}-${c.chiave}`}>{c.etichetta}</label>
            <input id={`vd-f-${f.tipo}-${c.chiave}`} className="vd-input vd-mono" value={String(conf[c.chiave] ?? "")} spellCheck={false}
                   onChange={e => setConf({ ...conf, [c.chiave]: e.target.value })} />
            {c.aiuto && <span className="vd-aiuto">{c.aiuto}</span>}
          </div>
        ))}
        {f.tipo === "SPID_CIE" && (
          <div className="vd-campo">
            <label htmlFor="vd-f-livello">Livello minimo</label>
            <select id="vd-f-livello" className="vd-input" value={String(conf.livello_minimo ?? 2)} onChange={e => setConf({ ...conf, livello_minimo: Number(e.target.value) })}>
              <option value="2">Livello 2</option><option value="3">Livello 3</option>
            </select>
          </div>
        )}
      </div>
      {salva.isError && <EsitoCritico titolo="Fornitore non salvato" errore={salva.error} azione="operazione" />}
      {salva.isSuccess && <p className="vd-nota" role="status">Salvato.</p>}
      <div className="vd-comandi"><button type="submit" className="vd-bottone vd-bottone--secondario" disabled={salva.isPending}>Salva {f.nome}</button></div>
    </form>
  );
}

function Fornitori() {
  const q = useQuery({ queryKey: ["fornitori"], queryFn: leggiFornitori });
  return (
    <section aria-labelledby="vd-fornitori">
      <div className="vd-sezione-testa"><h2 id="vd-fornitori" className="vd-etichetta">Fornitori d'identità</h2></div>
      <p className="vd-nota">Un metodo d'accesso funziona solo se il suo fornitore è attivo e il metodo è ammesso nelle impostazioni.</p>
      {q.isLoading ? <InLettura testo="Lettura dei fornitori…" />
        : q.isError || !q.data ? <EsitoCritico titolo="Fornitori non disponibili" errore={q.error} onRiprova={() => q.refetch()} />
        : <div className="vd-pila">{q.data.items.map(f => <FormFornitore key={`${f.tipo}-${f.versione}`} f={f} />)}</div>}
    </section>
  );
}

export default function ConfAccessoPage() {
  const { user } = useAuth();
  const conservatore = user?.ruolo === "SUPER_ADMIN" || user?.ruolo === "RSC_CONSERVATORE";
  const [scelto, setScelto] = useState(GRUPPI[0].gruppo);
  const g = GRUPPI.find(x => x.gruppo === scelto) ?? GRUPPI[0];
  return (
    <>
      <div className="vd-comandi" role="tablist" aria-label="Gruppi di accesso e notifiche">
        {GRUPPI.map(x => (
          <button key={x.gruppo} type="button" role="tab" aria-selected={x.gruppo === scelto}
                  className={x.gruppo === scelto ? "vd-bottone" : "vd-bottone vd-bottone--secondario"}
                  onClick={() => setScelto(x.gruppo)}>{x.nome}</button>
        ))}
      </div>
      <ConsoleImpostazioni key={g.gruppo} gruppo={g.gruppo} conRegole={false}
        titolo={`Accesso e notifiche: ${g.nome.toLowerCase()}`} spiegazione={g.spiegazione} />
      {conservatore && <div className="vd-pila"><Fornitori /></div>}
    </>
  );
}
