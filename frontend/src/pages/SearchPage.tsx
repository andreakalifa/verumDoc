// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Ricerca — sistema grafico «Partenze» (VD-DR-01).
 * Cerca tra i pacchetti conservati. Accetta ?q= dalla casella della testata.
 * g3-frontend: il servizio (POST /dashboard/search) applica tutti i filtri: testo,
 * classe documentale, anno di versamento e stato. La pagina descrive i risultati con
 * i filtri che il servizio dichiara applicati (`filtri_applicati`), non con quelli in
 * modifica. R03 (REV-06): testo e classe si cercano sull'indice di ricerca; pagine a
 * cursore senza totale (l'archivio può avere milioni di pacchetti).
 */
import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, searchAIP } from "../api/client";
import { motivoErrore } from "../api/errori";
import { SceltaEnte, useEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";

const LIMITE = 20;

const STATO_AIP: Record<string, { testo: string; classe: string }> = {
  IN_BUILD:     { testo: "In costruzione", classe: "vd-stato--info" },
  FIRMATO:      { testo: "Firmato", classe: "vd-stato--info" },
  TIMESTAMPATO: { testo: "Marcato", classe: "vd-stato--info" },
  SU_STORAGE:   { testo: "Archiviato, attende la catena", classe: "vd-stato--info" },
  IN_CATENA:    { testo: "Iscritto nella catena", classe: "vd-stato--ok" },
  CONSERVATO:   { testo: "Conservato", classe: "vd-stato--ok" },
  ESIBIZIONE:   { testo: "In esibizione", classe: "vd-stato--info" },
  ELIMINAZIONE: { testo: "In scarto", classe: "vd-stato--rischio" },
  // Revisione 2 di R05 (S3): cancellazione dello scarto iniziata e non completata
  ELIMINAZIONE_INCOMPLETA: { testo: "Scarto incompleto", classe: "vd-stato--critico" },
};
const statoDi = (s?: string) =>
  (s ? STATO_AIP[s] : undefined) ?? { testo: s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ") : "Non indicato", classe: "vd-stato--neutro" };

/** Etichette dei campi dell'indice in cui il servizio cerca il testo (`campi_testo`). */
const CAMPO_TESTO: Record<string, string> = {
  testo_completo: "metadati del documento",
  oggetto: "oggetto",
  soggetti_produttori: "produttore",
  classe_documentale: "classe documentale",
  numero: "numero dell'unità documentaria",
  registro: "registro",
  urn: "segnatura",
  identificativi_documento: "identificativo del documento",
  numeri_registrazione: "numero di registrazione",
};

const num = (n: number) => n.toLocaleString("it-IT");
const dataIt = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" });
};

interface Filtri { q: string; classe: string; anno: string; stato: string }
const VUOTI: Filtri = { q: "", classe: "", anno: "", stato: "" };

export default function SearchPage() {
  const ente = useEnte();
  const [params] = useSearchParams();
  const qIniziale = params.get("q") ?? "";
  const [f, setF] = useState<Filtri>({ ...VUOTI, q: qIniziale });
  // cursori[n] apre la pagina n (la prima senza cursore)
  const [pagina, setPagina] = useState(0);
  const [cursori, setCursori] = useState<(string | null)[]>([null]);
  // Ciò che è stato davvero inviato: i risultati si descrivono con questo, non con i campi in modifica
  const [inviata, setInviata] = useState<Filtri | null>(null);

  const ricerca = useMutation({ mutationFn: searchAIP });
  const classi = useQuery({
    queryKey: ["classi-ricerca"],
    queryFn: () => api.get("/dashboard/classi-documentali?limit=100").then(r => r.data),
  });
  const elencoClassi: any[] = Array.isArray(classi.data?.items) ? classi.data.items : [];

  const cerca = (nuovaPagina = 0, filtri: Filtri = f, pila: (string | null)[] = [null]) => {
    const pulito = { q: filtri.q.trim(), classe: filtri.classe.trim(), anno: filtri.anno.trim(), stato: filtri.stato };
    setPagina(nuovaPagina);
    setCursori(pila);
    setInviata(pulito);
    ricerca.mutate({
      query: pulito.q || undefined,
      classe_documentale: pulito.classe || undefined,
      anno: pulito.anno || undefined,
      stato: pulito.stato || undefined,
      tenant_id: ente.tenantId || undefined,
      cursore: pila[nuovaPagina] || undefined,
      limit: LIMITE,
    });
  };

  // Ricerca avviata dalla casella nella testata (?q=...)
  useEffect(() => {
    if (qIniziale) { const nuovi = { ...VUOTI, q: qIniziale }; setF(nuovi); cerca(0, nuovi); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qIniziale]);

  const invia = (e: FormEvent) => { e.preventDefault(); cerca(0); };
  const azzera = () => { ricerca.reset(); setF(VUOTI); setPagina(0); setCursori([null]); setInviata(null); };
  const cambia = (k: keyof Filtri) => (e: { target: { value: string } }) => setF(v => ({ ...v, [k]: e.target.value }));

  const annoNonValido = f.anno.trim() !== "" && !/^\d{4}$/.test(f.anno.trim());
  const dati = ricerca.data;
  const righe: any[] = dati?.items ?? [];
  const applicati = dati?.filtri_applicati ?? {};
  // Campi in cui il servizio cerca: quelli dichiarati, altrimenti quelli noti (mai un elenco vuoto)
  const campiTesto: string[] = Array.isArray(dati?.campi_testo) && dati.campi_testo.length > 0 ? dati.campi_testo : Object.keys(CAMPO_TESTO);

  // Descrizione dei filtri applicati, dalle parole del servizio
  const descrizione: string[] = [];
  if (applicati.testo) descrizione.push(`con «${applicati.testo}»`);
  if (applicati.classe_documentale) {
    const c = elencoClassi.find(x => x.codice?.toLowerCase() === String(applicati.classe_documentale).toLowerCase() || x.id === applicati.classe_documentale);
    descrizione.push(`della classe ${c?.nome ? `«${c.nome}»` : applicati.classe_documentale}`);
  }
  if (applicati.anno_versamento) descrizione.push(`versati nel ${applicati.anno_versamento}`);
  if (applicati.stato) descrizione.push(`nello stato «${statoDi(applicati.stato).testo.toLowerCase()}»`);
  // Filtri della query davvero applicata: quelli dichiarati dal servizio, oppure quelli inviati
  const qualcheFiltro = descrizione.length > 0 || !!(inviata && (inviata.q || inviata.classe || inviata.anno || inviata.stato));
  const primo = pagina * LIMITE + 1;
  const ultimo = pagina * LIMITE + righe.length;
  const prossimo: string | null = dati?.altri && dati?.cursore ? dati.cursore : null;
  const piuPagine = pagina > 0 || prossimo !== null;
  const avanti = () => {
    if (!prossimo || !inviata) return;
    cerca(pagina + 1, inviata, [...cursori.slice(0, pagina + 1), prossimo]);
  };
  const indietro = () => { if (inviata && pagina > 0) cerca(pagina - 1, inviata, cursori); };

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Ricerca</h1>
        <span className="vd-nota">Cerca tra i pacchetti conservati per testo, classe documentale, anno di versamento e stato.</span>
      </div>

      <form role="search" aria-label="Ricerca nei pacchetti conservati" onSubmit={invia} noValidate className="vd-filtri" style={{ marginBottom: 32 }}>
        <SceltaEnte id="vd-cerca-ente" stato={ente} onCambia={() => { ricerca.reset(); setInviata(null); }} />
        <div className="vd-campo" style={{ flex: "3 1 320px" }}>
          <label htmlFor="vd-cerca-testo">Testo da cercare</label>
          <input id="vd-cerca-testo" type="search" className="vd-input" value={f.q} onChange={cambia("q")}
                 aria-describedby="vd-cerca-testo-aiuto" autoComplete="off" maxLength={200} />
        </div>
        <div className="vd-campo" style={{ flex: "2 1 220px" }}>
          <label htmlFor="vd-cerca-classe">Classe documentale</label>
          {classi.isError || (classi.isSuccess && elencoClassi.length === 0) ? (
            <input id="vd-cerca-classe" className="vd-input" value={f.classe} onChange={cambia("classe")} maxLength={100}
                   aria-describedby="vd-cerca-classe-aiuto" autoComplete="off" />
          ) : (
            <select id="vd-cerca-classe" className="vd-input" value={f.classe} onChange={cambia("classe")} disabled={classi.isLoading}>
              <option value="">{classi.isLoading ? "Lettura delle classi…" : "Tutte le classi"}</option>
              {elencoClassi.map(c => <option key={c.id ?? c.codice} value={c.codice}>{c.nome || c.codice} ({c.codice})</option>)}
            </select>
          )}
        </div>
        <div className="vd-campo" style={{ flex: "0 1 140px" }}>
          <label htmlFor="vd-cerca-anno">Anno di versamento</label>
          <input id="vd-cerca-anno" className="vd-input" inputMode="numeric" maxLength={4} value={f.anno}
                 onChange={cambia("anno")} aria-invalid={annoNonValido || undefined}
                 aria-describedby="vd-cerca-anno-aiuto" />
        </div>
        <div className="vd-campo" style={{ flex: "1 1 200px" }}>
          <label htmlFor="vd-cerca-stato">Stato</label>
          <select id="vd-cerca-stato" className="vd-input" value={f.stato} onChange={cambia("stato")}>
            <option value="">Tutti gli stati</option>
            {Object.keys(STATO_AIP).map(k => <option key={k} value={k}>{STATO_AIP[k].testo}</option>)}
          </select>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          <button type="submit" className="vd-bottone" disabled={ricerca.isPending || annoNonValido || !ente.tenantId}>
            {ricerca.isPending ? "Ricerca in corso…" : "Cerca"}
          </button>
          {(inviata || f.q || f.classe || f.anno || f.stato) && (
            <button type="button" className="vd-bottone vd-bottone--secondario" onClick={azzera}>Azzera</button>
          )}
        </div>
        <div style={{ flexBasis: "100%", display: "flex", flexDirection: "column", gap: 4 }}>
          <span id="vd-cerca-testo-aiuto" className="vd-nota" style={{ fontSize: 13 }}>
            Ogni parola (fino a 8) deve comparire in almeno uno di questi campi: {campiTesto.map(c => CAMPO_TESTO[c] ?? c).join(", ")}.
            Si cercano parole intere, non parti di parola; i metadati marcati come riservati non sono cercabili.
          </span>
          {classi.isError && (
            <span id="vd-cerca-classe-aiuto" className="vd-stato vd-stato--rischio" style={{ fontSize: 13, whiteSpace: "normal" }}>
              Elenco delle classi non disponibile ({motivoErrore(classi.error)}): scrivi il codice della classe.
            </span>
          )}
          <span id="vd-cerca-anno-aiuto" className={annoNonValido ? "vd-stato vd-stato--critico" : "vd-nota"}
                style={{ fontSize: 13, whiteSpace: "normal" }}>
            {annoNonValido ? "L'anno va scritto con quattro cifre, per esempio 2026." : "Anno in cui il pacchetto è stato versato, quattro cifre."}
          </span>
        </div>
      </form>

      <section aria-labelledby="vd-cerca-risultati" aria-busy={ricerca.isPending}>
        <div className="vd-sezione-testa">
          <h2 id="vd-cerca-risultati" className="vd-etichetta">Risultati</h2>
          {ricerca.isSuccess && inviata && (
            <span className="vd-nota" aria-live="polite">
              {righe.length > 0 ? `Pacchetti ${num(primo)}–${num(ultimo)}` : "Nessun pacchetto"}{descrizione.length ? ` ${descrizione.join(", ")}` : qualcheFiltro ? " con i filtri scelti" : " dell'ente, nessun filtro"}{prossimo ? ": ce ne sono altri" : ""}
              {ente.nomeEnte ? ` · ${ente.nomeEnte}` : ""}
            </span>
          )}
        </div>

        {!ente.tenantId ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>
            {ente.globale ? "Scegli un ente: la ricerca riguarda un ente alla volta." : "Il tuo profilo non è collegato a un ente: chiedi all'amministrazione del servizio."}
          </p>
        ) : !inviata ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>Scrivi un testo o scegli un filtro e premi «Cerca». Senza filtri si elencano tutti i pacchetti, dal più recente.</p>
        ) : ricerca.isPending ? (
          <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Ricerca in corso…</p>
        ) : ricerca.isError ? (
          <EsitoCritico titolo="Ricerca non disponibile" errore={ricerca.error}
                        spiegazione="Il risultato non è «nessun documento»: la ricerca non è stata eseguita."
                        onRiprova={() => inviata && cerca(pagina, inviata, cursori)} inRiprova={ricerca.isPending} />
        ) : righe.length === 0 && !piuPagine ? (
          <p className="vd-nota" style={{ paddingBlock: 12 }}>
            {qualcheFiltro
              ? `Nessun pacchetto ${descrizione.length ? descrizione.join(", ") : "con i filtri scelti"}. Prova a togliere o cambiare un filtro: l'archivio dell'ente non è per questo vuoto.`
              : "Nessun pacchetto conservato per questo ente."}
          </p>
        ) : (
          <>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila">
                <caption className="vd-sr">Risultati della ricerca, da {num(primo)} a {num(ultimo)}</caption>
                <thead>
                  <tr><th scope="col">Versato</th><th scope="col">Pacchetto</th><th scope="col">Classe</th><th scope="col">Stato</th></tr>
                </thead>
                <tbody>
                  {righe.map((r, i) => {
                    const st = statoDi(r.stato);
                    return (
                      <tr key={r.aip_id ?? i}>
                        <td className="vd-ora" data-label="Versato">
                          {dataIt(r.data_versamento)?.slice(0, 5) ?? "—"}
                          <span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>
                            {dataIt(r.data_versamento)?.slice(6) ?? "data non fornita"}
                          </span>
                        </td>
                        <td data-label="Pacchetto">
                          <span style={{ fontWeight: 600, overflowWrap: "anywhere" }}>{r.oggetto || <span className="vd-nota">Oggetto non indicato</span>}</span>
                          <span className="vd-sotto vd-mono" style={{ overflowWrap: "anywhere" }}>{r.urn || `Senza segnatura · ${String(r.aip_id ?? "").slice(0, 8)}`}</span>
                        </td>
                        <td className="vd-a-capo" data-label="Classe">
                          {r.classe_documentale ? (
                            <>{r.classe_documentale.nome || r.classe_documentale.codice}<span className="vd-sotto vd-mono">{r.classe_documentale.codice}</span></>
                          ) : <span className="vd-nota">non indicata</span>}
                        </td>
                        <td className={`vd-stato ${st.classe}`} style={{ whiteSpace: "normal" }} data-label="Stato">{st.testo}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {righe.length === 0 && (
              <p className="vd-nota" style={{ paddingBlock: 12 }}>
                Nessun pacchetto in questa pagina con lo stato scelto: lo stato si controlla sul dato attuale, passa alla pagina successiva.
              </p>
            )}
            {piuPagine && (
              <nav aria-label="Pagine dei risultati" className="vd-paginazione">
                <button type="button" className="vd-bottone vd-bottone--secondario" disabled={pagina === 0}
                        onClick={indietro}>Precedente</button>
                <span className="vd-nota">{righe.length > 0 ? `Pacchetti ${num(primo)}–${num(ultimo)}` : `Pagina ${num(pagina + 1)}`}</span>
                <button type="button" className="vd-bottone vd-bottone--secondario" disabled={prossimo === null}
                        onClick={avanti}>Successiva</button>
              </nav>
            )}
          </>
        )}
      </section>
    </>
  );
}
