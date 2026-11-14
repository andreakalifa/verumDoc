// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Impostazioni e regole — console di configurazione R05 (contratto Q1), sistema «Partenze».
 *
 * Una sola pagina per conservatore e cliente: il servizio dice per ogni voce il
 * valore effettivo, da quale livello viene («10 anni — ereditato da: conservatore»),
 * se è imposto dalla norma (`locked`, con il riferimento) e se chi guarda lo può
 * cambiare a questo livello (`azioni_consentite`, con il motivo del blocco): la
 * pagina non ricalcola permessi.
 *
 * Una modifica non si applica da qui: si raccoglie nella «bozza in preparazione»
 * (una o più voci, un motivo) e si salva come bozza; anteprima dell'impatto e
 * applicazione stanno nella pagina della bozza (REGOLE §13).
 *
 * Elenchi a cursore dal servizio (`limit`, `cursore`), niente totale.
 * Riusata dalla pagina «Quote degli enti» con `gruppo="quote"`.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import {
  creaBozza, elencoClassi, leggiClassiEnte, leggiImpostazioni, leggiRegole,
  type FiltroContesto, type Livello, type Modifica, type Regola, type Risorsa, type Voce,
} from "../console/api";
import { CampoMotivo, InLettura, motivoValido, origineInParole, valoreInParole, Vuoto } from "../console/comuni";
import { CampoValore, grezzoDa, valoreDa } from "../console/CampoValore";
import { EFFETTO, LIVELLO, nomeLivello, parola } from "../console/parole";
import { leggiErrore } from "../api/errori";

const LIMITE = 100;

interface InPreparazione { chiave: string; voce: string; testo: string; modifica: Modifica }

/** Colonna «Chi decide»: norma, conservatore, oppure chi guarda. */
function ChiDecide({ locked, norm, azioni, ambito }: { locked: boolean; norm: string | null; azioni: { modifica: boolean; motivo_blocco: string | null }; ambito: string[] }) {
  if (locked) {
    // revisione R17 (R17-27): voce imposta dalla norma che solo il conservatore stabilisce
    return (
      <>
        <span className="vd-stato vd-stato--neutro">{azioni.modifica ? "Imposto dalla norma, si stabilisce solo qui" : "Imposto dalla norma"}</span>
        {norm && <span className="vd-sotto vd-a-capo">{norm}</span>}
      </>
    );
  }
  if (!azioni.modifica) {
    return (
      <>
        <span className="vd-stato vd-stato--neutro">Non modificabile qui</span>
        {azioni.motivo_blocco && <span className="vd-sotto vd-a-capo">{azioni.motivo_blocco.replace(/^./, c => c.toUpperCase())}</span>}
      </>
    );
  }
  // Il dettaglio dei livelli serve solo quando la voce non vale a tutti i livelli configurabili
  const tutti = ["CONSERVATORE", "ENTE", "CLASSE", "ACCORDO"].every(l => ambito.includes(l));
  return (
    <>
      <span>Modificabile</span>
      {!tutti && <span className="vd-sotto">solo a livello {ambito.map(l => nomeLivello(l)).join(", ")}</span>}
    </>
  );
}

export function ConsoleImpostazioni({ gruppo, titolo, spiegazione, conRegole = true }: {
  gruppo?: string; titolo: string; spiegazione: string; conRegole?: boolean;
}) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const ente = useEnte(params.get("ente"));
  const conservatore = ente.globale;
  // R07 (D13): le quote si guardano al livello del conservatore (limiti dei piani) o
  // dell'ente (deroga); mai a classe o accordo
  const soloEnte = gruppo === "quote";
  const [livello, setLivello] = useState<Livello>(
    (soloEnte ? "ENTE" : (params.get("livello") as Livello)) || (conservatore ? "CONSERVATORE" : "ENTE"));
  const [classe, setClasse] = useState(params.get("classe") ?? "");
  const [accordo, setAccordo] = useState(params.get("accordo") ?? "");
  const [accordoScritto, setAccordoScritto] = useState(params.get("accordo") ?? "");
  const cursoriI = useCursori();
  const cursoriR = useCursori();

  const [inPrep, setInPrep] = useState<InPreparazione[]>([]);
  const [ambitoPrep, setAmbitoPrep] = useState<FiltroContesto | null>(null);
  const [aperta, setAperta] = useState<string | null>(null);
  const [grezzo, setGrezzo] = useState("");
  const [erroreCampo, setErroreCampo] = useState<string | undefined>();
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const [proporre, setProporre] = useState(false);

  const serveEnte = livello !== "CONSERVATORE";
  const contesto: FiltroContesto = useMemo(() => ({
    livello,
    ente: serveEnte ? ente.tenantId || undefined : undefined,
    classe: (livello === "CLASSE" || livello === "ACCORDO") && classe ? classe : undefined,
    accordo: livello === "ACCORDO" ? accordo || undefined : undefined,
    gruppo,
  }), [livello, serveEnte, ente.tenantId, classe, accordo, gruppo]);
  const pronto = (!serveEnte || !!contesto.ente) && (livello !== "CLASSE" || !!contesto.classe) && (livello !== "ACCORDO" || !!contesto.accordo);
  const bloccaFiltri = inPrep.length > 0;
  const ricomincia = () => { cursoriI.ricomincia(); cursoriR.ricomincia(); setAperta(null); };

  const classi = useQuery({
    queryKey: ["conf-classi", ente.tenantId],
    queryFn: () => leggiClassiEnte(ente.tenantId),
    enabled: !!ente.tenantId && (livello === "CLASSE" || livello === "ACCORDO"),
  });
  const impostazioni = useQuery({
    queryKey: ["conf-impostazioni", contesto, cursoriI.cursore],
    queryFn: () => leggiImpostazioni(contesto, cursoriI.cursore, LIMITE),
    enabled: pronto,
  });
  const regole = useQuery({
    queryKey: ["conf-regole", contesto, cursoriR.cursore],
    queryFn: () => leggiRegole(contesto, cursoriR.cursore, LIMITE),
    enabled: pronto && conRegole,
  });

  const salva = useMutation({
    mutationFn: () => {
      const soloRegole = inPrep.every(m => "regola" in m.modifica);
      const risorsa: Risorsa = gruppo === "quote" ? "quote" : soloRegole ? "regole" : "impostazioni";
      const a = ambitoPrep ?? contesto;
      return creaBozza({
        risorsa,
        ambito: { livello: a.livello as Livello, ente: a.ente ?? null, classe: a.classe ?? null, accordo: a.accordo ?? null },
        modifiche: inPrep.map(m => m.modifica),
        motivo: motivo.trim(),
        ...(proporre && a.livello === "CONSERVATORE" ? { proposta: { enti: null } } : {}),
      });
    },
    onSuccess: (r) => {
      setInPrep([]); setAmbitoPrep(null); setMotivo(""); setToccato(false); setProporre(false);
      navigate(`/configurazione/bozze/${r.bozza_id}`, { state: { creata: true } });
    },
  });

  const apri = (chiave: string, iniziale: string) => { setAperta(chiave); setGrezzo(iniziale); setErroreCampo(undefined); };
  const aggiungi = (p: InPreparazione) => {
    setInPrep(l => [...l.filter(x => x.chiave !== p.chiave), p]);
    if (!ambitoPrep) setAmbitoPrep(contesto);
    setAperta(null);
  };
  const togli = (chiave: string) => setInPrep(l => {
    const resto = l.filter(x => x.chiave !== chiave);
    if (!resto.length) setAmbitoPrep(null);
    return resto;
  });
  const inPrepDi = (chiave: string) => inPrep.find(m => m.chiave === chiave);

  const voci: Voce[] = impostazioni.data?.items ?? [];
  const elencoRegole: Regola[] = regole.data?.items ?? [];
  const elencoClassiEnte = elencoClassi(classi.data);
  const livelli: Livello[] = soloEnte ? (conservatore ? ["ENTE", "CONSERVATORE"] : ["ENTE"])
    : conservatore ? ["CONSERVATORE", "ENTE", "CLASSE", "ACCORDO"] : ["ENTE", "CLASSE", "ACCORDO"];
  const nomeContesto = [
    `livello ${nomeLivello(livello)}`,
    serveEnte && (ente.nomeEnte ?? (ente.proprio ? user?.ragione_sociale : undefined)),
    contesto.classe && `classe ${elencoClassiEnte.find(c => c.id === contesto.classe)?.codice ?? contesto.classe}`,
    contesto.accordo && `accordo ${contesto.accordo}`,
  ].filter(Boolean).join(" · ");
  const salvaPronta = inPrep.length > 0 && motivoValido(motivo);

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>{titolo}</h1>
        <p>{spiegazione}</p>
      </div>

      <div className="vd-pila">
        <section aria-labelledby="vd-conf-dove">
          <h2 id="vd-conf-dove" className="vd-sr">Da dove guardare</h2>
          <div className="vd-filtri" style={{ marginBottom: bloccaFiltri ? 8 : 0 }}>
            {livelli.length > 1 && (
              <div className="vd-campo">
                <label htmlFor="vd-conf-livello">Livello</label>
                <select id="vd-conf-livello" className="vd-input" value={livello} disabled={bloccaFiltri}
                        onChange={e => { setLivello(e.target.value as Livello); ricomincia(); }}>
                  {livelli.map(l => <option key={l} value={l}>{LIVELLO[l].replace(/^./, c => c.toUpperCase())}</option>)}
                </select>
              </div>
            )}
            {serveEnte && <SceltaEnte id="vd-conf-ente" stato={{ ...ente, setEnte: id => { if (!bloccaFiltri) ente.setEnte(id); } }} onCambia={ricomincia} />}
            {(livello === "CLASSE" || livello === "ACCORDO") && (
              <div className="vd-campo">
                <label htmlFor="vd-conf-classe">Classe documentale{livello === "ACCORDO" ? " (facoltativa)" : ""}</label>
                {classi.isError ? (
                  <span className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>Classi non disponibili.</span>
                ) : (
                  <select id="vd-conf-classe" className="vd-input" value={classe} disabled={bloccaFiltri || classi.isLoading || !ente.tenantId}
                          onChange={e => { setClasse(e.target.value); ricomincia(); }}>
                    <option value="">{classi.isLoading ? "Lettura delle classi…" : livello === "ACCORDO" ? "Tutte le classi" : "Scegli una classe"}</option>
                    {elencoClassiEnte.map(c => <option key={c.id} value={c.id}>{c.nome ? `${c.nome} (${c.codice})` : c.codice}</option>)}
                  </select>
                )}
              </div>
            )}
            {livello === "ACCORDO" && (
              <form className="vd-campo" onSubmit={e => { e.preventDefault(); setAccordo(accordoScritto.trim()); ricomincia(); }}>
                <label htmlFor="vd-conf-accordo">Codice dell'accordo di versamento</label>
                <div style={{ display: "flex", gap: 8 }}>
                  <input id="vd-conf-accordo" className="vd-input vd-mono" value={accordoScritto} disabled={bloccaFiltri}
                         spellCheck={false} onChange={e => setAccordoScritto(e.target.value)} />
                  <button type="submit" className="vd-bottone vd-bottone--secondario" disabled={bloccaFiltri}>Apri</button>
                </div>
              </form>
            )}
          </div>
          {bloccaFiltri && (
            <p className="vd-nota">Il livello resta fermo finché la bozza in preparazione non è salvata o svuotata: ogni bozza riguarda un solo ambito.</p>
          )}
        </section>

        {inPrep.length > 0 && (
          <section aria-labelledby="vd-conf-prep" className="vd-modulo">
            <h2 id="vd-conf-prep">Bozza in preparazione</h2>
            <p className="vd-nota" style={{ marginTop: -12 }}>
              Ambito: {nomeContesto}. Nulla cambia finché la bozza non è salvata, controllata nell'anteprima e applicata.
            </p>
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-impila--pieno">
                <caption className="vd-sr">Modifiche della bozza in preparazione</caption>
                <thead><tr><th scope="col">Voce</th><th scope="col">Nuovo valore</th><th scope="col">Comandi</th></tr></thead>
                <tbody>
                  {inPrep.map(m => (
                    <tr key={m.chiave}>
                      <td data-label="Voce">{m.voce}<span className="vd-sotto vd-mono">{m.chiave}</span></td>
                      <td data-label="Nuovo valore" className="vd-cella-valore"><span className="vd-dopo">{m.testo}</span></td>
                      <td><button type="button" className="vd-comando-testo" onClick={() => togli(m.chiave)}>Togli</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {conservatore && (ambitoPrep?.livello ?? livello) === "CONSERVATORE" && !soloEnte && (
              <label className="vd-casella">
                <input type="checkbox" checked={proporre} onChange={e => setProporre(e.target.checked)} />
                Proponi agli enti invece di applicare: ogni ente accetta o rifiuta con un motivo
              </label>
            )}
            <CampoMotivo id="vd-conf-motivo" valore={motivo} onCambia={setMotivo} toccato={toccato} onTocca={() => setToccato(true)}
                         aiuto="Perché si cambia (almeno 10 caratteri): accompagna la bozza, l'anteprima e il registro delle operazioni." />
            {salva.isError && (
              <EsitoCritico titolo="Bozza non salvata" errore={salva.error} azione="operazione"
                            spiegazione="Nessuna modifica è stata registrata." />
            )}
            <div className="vd-comandi">
              <button type="button" className="vd-bottone" disabled={salva.isPending}
                      onClick={() => { setToccato(true); if (salvaPronta) salva.mutate(); }}>
                {salva.isPending ? "Salvataggio…" : `Salva la bozza (${inPrep.length} ${inPrep.length === 1 ? "modifica" : "modifiche"})`}
              </button>
              <button type="button" className="vd-bottone vd-bottone--secondario" disabled={salva.isPending}
                      onClick={() => { setInPrep([]); setAmbitoPrep(null); }}>Svuota</button>
            </div>
          </section>
        )}

        {!pronto ? (
          <Vuoto titolo="Scegli dove guardare">
            <p>{serveEnte && !contesto.ente ? "Scegli un ente." : livello === "CLASSE" ? "Scegli una classe documentale." : "Scrivi il codice dell'accordo e premi «Apri»."}</p>
          </Vuoto>
        ) : (
          <>
            <section aria-labelledby="vd-conf-imp">
              <div className="vd-sezione-testa">
                <h2 id="vd-conf-imp" className="vd-etichetta">{gruppo === "quote" ? "Quote" : "Impostazioni"}</h2>
                <span className="vd-nota">valori effettivi al {nomeContesto}</span>
              </div>
              {impostazioni.isLoading ? <InLettura testo="Lettura delle impostazioni…" />
                : impostazioni.isError ? (
                  <EsitoCritico titolo="Impostazioni non disponibili" errore={impostazioni.error}
                                spiegazione="Non significa che i valori siano quelli predefiniti."
                                onRiprova={() => impostazioni.refetch()} inRiprova={impostazioni.isFetching} />
                ) : voci.length === 0 ? (
                  <Vuoto titolo="Nessuna impostazione in questo gruppo" />
                ) : (
                  <>
                    <div className="vd-scorri">
                      <table className="vd-tabellone vd-impila vd-impila--pieno" aria-busy={impostazioni.isFetching}>
                        <caption className="vd-sr">Impostazioni effettive con l'origine del valore</caption>
                        <thead><tr><th scope="col">Voce</th><th scope="col">Valore</th><th scope="col">Chi decide</th><th scope="col">Comandi</th></tr></thead>
                        <tbody>
                          {voci.map(v => {
                            const prep = inPrepDi(v.codice);
                            const id = `vd-val-${v.codice.replace(/[^A-Za-z0-9]/g, "-")}`;
                            return (
                              <tr key={v.codice}>
                                <td data-label="Voce" className="vd-cella-larga">
                                  {v.descrizione}
                                  <span className="vd-sotto vd-mono">{v.codice}</span>
                                </td>
                                <td data-label="Valore" className="vd-cella-valore">
                                  {/* Gli elenchi di codici sono dati: mono a peso normale; i valori singoli in grassetto */}
                                  <span className={Array.isArray(v.valore) && v.valore.length ? "vd-mono" : "vd-forte"}>{valoreInParole(v.valore, v.tipo)}</span>
                                  <span className="vd-sotto">{origineInParole(v.origine, livello)}</span>
                                  {prep && <span className="vd-sotto">nella bozza: <span className="vd-dopo">{prep.testo}</span></span>}
                                </td>
                                <td data-label="Chi decide" style={{ whiteSpace: "normal" }}>
                                  <ChiDecide locked={v.locked} norm={v.norm_ref} azioni={v.azioni_consentite} ambito={v.ambito_ammesso} />
                                </td>
                                <td>
                                  {aperta === v.codice ? (
                                    <div className="vd-modifica-riga" role="group" aria-label={`Nuovo valore di ${v.descrizione}`}>
                                      <CampoValore id={id} voce={v} valore={grezzo} onCambia={setGrezzo} errore={erroreCampo}
                                                   etichetta={`Valore a livello ${nomeLivello(livello)}`} />
                                      <div className="vd-comandi-cella">
                                        <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => {
                                          const r = valoreDa(grezzo, v);
                                          if ("errore" in r) { setErroreCampo(r.errore); return; }
                                          aggiungi({ chiave: v.codice, voce: v.descrizione, testo: valoreInParole(r.valore, v.tipo), modifica: { codice: v.codice, valore: r.valore } });
                                        }}>Metti nella bozza</button>
                                        {v.valore_al_livello != null && v.ereditato && (
                                          <button type="button" className="vd-comando-testo" onClick={() =>
                                            aggiungi({ chiave: v.codice, voce: v.descrizione, testo: `torna a ${valoreInParole(v.ereditato!.valore, v.tipo)} (${origineInParole(v.ereditato!.origine)})`, modifica: { codice: v.codice, eredita: true } })}>
                                            Torna al valore ereditato
                                          </button>
                                        )}
                                        <button type="button" className="vd-comando-testo" onClick={() => setAperta(null)}>Annulla</button>
                                      </div>
                                    </div>
                                  ) : v.azioni_consentite.modifica ? (
                                    <button type="button" className="vd-comando-testo" aria-label={`Modifica: ${v.descrizione}`}
                                            onClick={() => apri(v.codice, grezzoDa(prep ? (prep.modifica as { valore?: unknown }).valore : v.valore, v.tipo))}>
                                      Modifica
                                    </button>
                                  ) : null}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <Paginazione cursori={cursoriI} prossimo={prossimoDi(impostazioni.data)} righe={voci.length} limite={LIMITE}
                                 nome="Impostazioni" etichetta="Pagine delle impostazioni" testoPrecedente="Precedenti" testoSuccessiva="Successive" />
                  </>
                )}
            </section>

            {conRegole && (
              <section aria-labelledby="vd-conf-regole">
                <div className="vd-sezione-testa">
                  <h2 id="vd-conf-regole" className="vd-etichetta">Severità delle regole</h2>
                  <span className="vd-nota">
                    che cosa succede a un versamento che non le rispetta{regole.data?.catalogo_regole ? ` · catalogo ${regole.data.catalogo_regole}` : ""}
                  </span>
                </div>
                {regole.isLoading ? <InLettura testo="Lettura delle regole…" />
                  : regole.isError ? (
                    <EsitoCritico titolo="Regole non disponibili" errore={regole.error}
                                  onRiprova={() => regole.refetch()} inRiprova={regole.isFetching} />
                  ) : (
                    <>
                      <div className="vd-scorri">
                        <table className="vd-tabellone vd-impila vd-impila--pieno" aria-busy={regole.isFetching}>
                          <caption className="vd-sr">Regole del versamento con l'effetto effettivo</caption>
                          <thead><tr><th scope="col">Regola</th><th scope="col">Effetto</th><th scope="col">Chi decide</th><th scope="col">Comandi</th></tr></thead>
                          <tbody>
                            {elencoRegole.map(r => {
                              const chiave = r.impostazione;
                              const prep = inPrepDi(chiave);
                              const ef = parola(EFFETTO, r.effetto);
                              return (
                                <tr key={r.codice}>
                                  <td data-label="Regola" className="vd-cella-larga">
                                    {r.etichetta}
                                    <span className="vd-sotto"><span className="vd-mono">{r.codice}</span> · fase {r.fase.toLowerCase()}</span>
                                  </td>
                                  <td data-label="Effetto">
                                    <span className={`vd-stato ${ef.classe}`}>{ef.testo}</span>
                                    <span className="vd-sotto">{origineInParole(r.origine, livello)}</span>
                                    {prep && <span className="vd-sotto">nella bozza: <span className="vd-dopo">{prep.testo}</span></span>}
                                  </td>
                                  <td data-label="Chi decide" style={{ whiteSpace: "normal" }}>
                                    <ChiDecide locked={r.locked} norm={r.norm_ref} azioni={r.azioni_consentite} ambito={r.ambito_ammesso} />
                                  </td>
                                  <td>
                                    {aperta === chiave ? (
                                      <div className="vd-modifica-riga" role="group" aria-label={`Nuovo effetto di ${r.codice}`}>
                                        <CampoValore id={`vd-eff-${r.codice}`} voce={{ tipo: "choice", scelte: ["BLOCCA", "AVVISA", "IGNORA"] }}
                                                     valore={grezzo} onCambia={setGrezzo} etichetta="Nuovo effetto" />
                                        <div className="vd-comandi-cella">
                                          <button type="button" className="vd-bottone vd-bottone--secondario"
                                                  onClick={() => aggiungi({ chiave, voce: `${r.codice} — ${r.etichetta}`, testo: parola(EFFETTO, grezzo).testo, modifica: { regola: r.codice, effetto: grezzo } })}>
                                            Metti nella bozza
                                          </button>
                                          <button type="button" className="vd-comando-testo" onClick={() => setAperta(null)}>Annulla</button>
                                        </div>
                                      </div>
                                    ) : r.azioni_consentite.modifica ? (
                                      <button type="button" className="vd-comando-testo" aria-label={`Cambia l'effetto di ${r.codice}`}
                                              onClick={() => apri(chiave, prep ? String((prep.modifica as { effetto?: string }).effetto) : r.effetto)}>
                                        Cambia l'effetto
                                      </button>
                                    ) : null}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      <Paginazione cursori={cursoriR} prossimo={prossimoDi(regole.data)} righe={elencoRegole.length} limite={LIMITE}
                                   nome="Regole" etichetta="Pagine delle regole" testoPrecedente="Precedenti" testoSuccessiva="Successive" />
                    </>
                  )}
              </section>
            )}
          </>
        )}
        <p className="vd-nota">
          L'anteprima dell'impatto e l'applicazione si fanno dalla <Link to="/configurazione/bozze" className="vd-riga-link">pagina delle bozze</Link>.
          {leggiErrore(salva.error).codice === "CFG-403" ? " Il tuo profilo non può salvare bozze a questo livello." : ""}
        </p>
      </div>
    </>
  );
}

export default function ConfImpostazioniPage() {
  return (
    <ConsoleImpostazioni
      titolo="Impostazioni e regole"
      spiegazione="Il valore che vale per i versamenti a ogni livello, da dove viene e chi lo può cambiare." />
  );
}
