// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Classi documentali e accordi di versamento dell'ente (contratto Q1, livelli CLASSE e ACCORDO).
 * - Classi: elenco dell'ente (API esistente) con il collegamento alle impostazioni della
 *   classe nella console (valori ereditati, metadati, formati, severità).
 * - Accordi: un accordo nuovo nasce da una bozza (risorsa «accordo», livello ACCORDO) con
 *   anteprima e applicazione. R06 (VD-C-139): gli accordi esistenti si elencano con
 *   GET /api/v1/configurazione/accordi (ultima versione, a cursore); resta l'apertura
 *   per codice.
 * Solo l'amministrazione dell'ente crea accordi (permessi Q1); il conservatore consulta.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useEnte, SceltaEnte } from "../components/SceltaEnte";
import { EsitoCritico } from "../components/EsitoCritico";
import { Paginazione, prossimoDi, useCursori } from "../components/Paginazione";
import { creaBozza, elencoClassi, leggiAccordi, leggiClassiEnte } from "../console/api";
import { CampoMotivo, InLettura, motivoValido, quandoBreve, Vuoto } from "../console/comuni";

const LIMITE_ACCORDI = 30;

const CODICE = /^[A-Za-z0-9._-]{1,50}$/;

export default function ConfClassiAccordiPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const ente = useEnte();
  const puoCreare = user?.ruolo === "ADMIN_TENANT";
  const classi = useQuery({ queryKey: ["conf-classi", ente.tenantId], queryFn: () => leggiClassiEnte(ente.tenantId), enabled: !!ente.tenantId });
  const elenco = elencoClassi(classi.data);
  const cursori = useCursori();
  const accordi = useQuery({
    queryKey: ["conf-accordi", ente.tenantId, cursori.cursore],
    queryFn: () => leggiAccordi({ ente: ente.tenantId || undefined }, cursori.cursore, LIMITE_ACCORDI),
    enabled: !!ente.tenantId,
  });
  const nomeClasse = (id: string | null) => {
    const c = id ? elenco.find(x => x.id === id) : undefined;
    return !id ? "tutte le classi" : c ? (c.nome ?? c.codice) : id;
  };
  const [apri, setApri] = useState("");
  const [codice, setCodice] = useState("");
  const [classe, setClasse] = useState("");
  const [descrizione, setDescrizione] = useState("");
  const [motivo, setMotivo] = useState("");
  const [tentato, setTentato] = useState(false);
  const codiceOk = CODICE.test(codice.trim());

  const crea = useMutation({
    mutationFn: () => creaBozza({
      risorsa: "accordo",
      ambito: { livello: "ACCORDO", ente: ente.tenantId, classe: classe || null, accordo: codice.trim() },
      modifiche: [{ campo: "descrizione", valore: descrizione.trim() }],
      motivo: motivo.trim(),
    }),
    onSuccess: r => navigate(`/configurazione/bozze/${r.bozza_id}`, { state: { creata: true } }),
  });
  const conf = (q: string) => `/configurazione?${q}${ente.globale && ente.tenantId ? `&ente=${encodeURIComponent(ente.tenantId)}` : ""}`;

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Classi e accordi</h1>
        <p>Le classi dicono che cosa si versa; un accordo di versamento fissa per un canale o un sistema versante le regole concordate con il conservatore.</p>
      </div>
      {ente.globale && <div className="vd-filtri"><SceltaEnte id="vd-ca-ente" stato={ente} /></div>}
      <div className="vd-pila">
        <section aria-labelledby="vd-ca-classi">
          <div className="vd-sezione-testa">
            <h2 id="vd-ca-classi" className="vd-etichetta">Classi documentali</h2>
            <span className="vd-nota">abilitate per l'ente</span>
          </div>
          {!ente.tenantId ? <Vuoto titolo="Scegli un ente" />
            : classi.isLoading ? <InLettura testo="Lettura delle classi…" />
            : classi.isError ? <EsitoCritico titolo="Classi non disponibili" errore={classi.error} onRiprova={() => classi.refetch()} inRiprova={classi.isFetching} />
            : elenco.length === 0 ? <Vuoto titolo="Nessuna classe abilitata"><p>Le classi si abilitano in <Link className="vd-riga-link" to="/classi-documentali">Classi documentali</Link>.</p></Vuoto>
            : (
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Classi documentali dell'ente</caption>
                  <thead><tr><th scope="col">Codice</th><th scope="col">Classe</th><th scope="col" className="vd-num">Conservazione</th><th scope="col">Comandi</th></tr></thead>
                  <tbody>
                    {elenco.map(c => (
                      <tr key={c.id}>
                        <td className="vd-ora" data-label="Codice">{c.codice}</td>
                        <td data-label="Classe">{c.nome ?? c.codice}{c.attiva === false && <span className="vd-sotto vd-stato--neutro">non attiva</span>}</td>
                        <td className="vd-num" data-label="Conservazione">
                          {c.anni_conservazione != null ? `${c.anni_conservazione} anni` : "—"}
                        </td>
                        <td>
                          <div className="vd-comandi-cella">
                            <Link className="vd-comando-testo" to={conf(`livello=CLASSE&classe=${encodeURIComponent(c.id)}`)}>Impostazioni della classe</Link>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </section>

        <section aria-labelledby="vd-ca-accordi">
          <div className="vd-sezione-testa">
            <h2 id="vd-ca-accordi" className="vd-etichetta">Accordi di versamento</h2>
            <span className="vd-nota">versionati: un versamento porta la versione con cui è stato verificato</span>
          </div>
          {!ente.tenantId ? <Vuoto titolo="Scegli un ente" />
            : accordi.isLoading ? <InLettura testo="Lettura degli accordi…" />
            : accordi.isError ? <EsitoCritico titolo="Accordi non disponibili" errore={accordi.error} onRiprova={() => accordi.refetch()} inRiprova={accordi.isFetching} />
            : (accordi.data?.items ?? []).length === 0 ? <Vuoto titolo="Nessun accordo di versamento"><p>Un accordo nasce da una bozza con anteprima.</p></Vuoto>
            : (
              <div className="vd-scorri" style={{ marginBottom: 20 }}>
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Accordi di versamento dell'ente, ultima versione</caption>
                  <thead><tr><th scope="col">Codice</th><th scope="col">Classe</th><th scope="col" className="vd-num">Versione</th><th scope="col">In vigore dal</th><th scope="col">Comandi</th></tr></thead>
                  <tbody>
                    {(accordi.data?.items ?? []).map(a => (
                      <tr key={`${a.ente}-${a.codice}`}>
                        <td className="vd-ora vd-mono" data-label="Codice">{a.codice}
                          {a.descrizione && <span className="vd-sotto vd-sotto-normale">{a.descrizione}</span>}
                        </td>
                        <td data-label="Classe">{nomeClasse(a.classe)}</td>
                        <td className="vd-num" data-label="Versione">{a.versione}</td>
                        <td data-label="In vigore dal">{quandoBreve(a.valido_dal) ?? a.valido_dal}
                          {a.cessato && <span className="vd-sotto vd-stato--neutro">cessato</span>}
                        </td>
                        <td>
                          <div className="vd-comandi-cella">
                            <Link className="vd-comando-testo" to={conf(`livello=ACCORDO&accordo=${encodeURIComponent(a.codice)}`)}>Regole dell'accordo</Link>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Paginazione cursori={cursori} prossimo={prossimoDi(accordi.data)} righe={(accordi.data?.items ?? []).length}
                             limite={LIMITE_ACCORDI} nome="Accordi" etichetta="Pagine degli accordi" />
              </div>
            )}
          <form className="vd-filtri" onSubmit={e => { e.preventDefault(); if (CODICE.test(apri.trim())) navigate(conf(`livello=ACCORDO&accordo=${encodeURIComponent(apri.trim())}`)); }}>
            <div className="vd-campo">
              <label htmlFor="vd-ca-apri">Codice dell'accordo</label>
              <input id="vd-ca-apri" className="vd-input vd-mono" value={apri} spellCheck={false} onChange={e => setApri(e.target.value)} />
            </div>
            <button type="submit" className="vd-bottone vd-bottone--secondario" disabled={!CODICE.test(apri.trim())}>Apri l'accordo</button>
          </form>

          {puoCreare && (
            <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setTentato(true); if (codiceOk && motivoValido(motivo)) crea.mutate(); }}>
              <h2>Nuovo accordo di versamento</h2>
              <div className="vd-griglia">
                <div className="vd-campo">
                  <label htmlFor="vd-ca-codice">Codice<span aria-hidden="true"> *</span><span className="vd-sr"> (obbligatorio)</span></label>
                  <input id="vd-ca-codice" className="vd-input vd-mono" value={codice} spellCheck={false} maxLength={50}
                         aria-invalid={tentato && !codiceOk ? true : undefined} aria-describedby="vd-ca-codice-aiuto"
                         onChange={e => setCodice(e.target.value)} />
                  <span id="vd-ca-codice-aiuto" className="vd-aiuto">Lettere, cifre, punto, trattino; lo usa il sistema versante nel campo «accordo» dell'indice.</span>
                  {tentato && !codiceOk && <span className="vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Codice non valido.</span>}
                </div>
                <div className="vd-campo">
                  <label htmlFor="vd-ca-classe">Classe documentale</label>
                  <select id="vd-ca-classe" className="vd-input" value={classe} onChange={e => setClasse(e.target.value)} aria-describedby="vd-ca-classe-aiuto">
                    <option value="">Tutte le classi</option>
                    {elenco.map(c => <option key={c.id} value={c.id}>{c.nome ? `${c.nome} (${c.codice})` : c.codice}</option>)}
                  </select>
                  <span id="vd-ca-classe-aiuto" className="vd-aiuto">La classe di un accordo non cambia nelle versioni successive.</span>
                </div>
              </div>
              <div className="vd-campo">
                <label htmlFor="vd-ca-descr">Descrizione</label>
                <textarea id="vd-ca-descr" className="vd-input vd-textarea" value={descrizione} maxLength={2000} onChange={e => setDescrizione(e.target.value)} />
              </div>
              <CampoMotivo id="vd-ca-motivo" valore={motivo} onCambia={setMotivo} toccato={tentato} onTocca={() => setTentato(true)} />
              <p className="vd-nota">Le regole dell'accordo (metadati, formati, severità) si impostano poi dalla console al livello «accordo».</p>
              {crea.isError && <EsitoCritico titolo="Bozza dell'accordo non salvata" errore={crea.error} azione="operazione" />}
              <div className="vd-comandi">
                <button type="submit" className="vd-bottone" disabled={crea.isPending}>{crea.isPending ? "Salvataggio…" : "Salva la bozza dell'accordo"}</button>
              </div>
            </form>
          )}
        </section>
      </div>
    </>
  );
}
