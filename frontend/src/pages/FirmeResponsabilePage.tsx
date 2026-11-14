// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Firme del responsabile (R16, VD-C-166) — sistema «Partenze» con la revisione R12c: testa
 * di pagina con un solo comando principale per vista, tabelloni con l'ora in prima colonna
 * impilati su telefono, stati vuoti che spiegano, dialogo laterale per la firma.
 *
 * Documenti che il responsabile del servizio firma PERSONALMENTE con l'OTP del prestatore
 * (manuale, piano di cessazione, nomine, altri documenti accodati dai servizi):
 *  1. «Leggi il documento» scarica esattamente i byte che verranno firmati;
 *  2. «Chiedi l'OTP» fa inviare il codice monouso dal prestatore alla persona;
 *  3. «Firma il documento» con OTP (e PIN, se la credenziale lo chiede): la busta CAdES
 *     torna verificata, il servizio la registra e il registro la archivia nel WORM.
 * OTP e PIN restano solo nel modulo del dialogo e si cancellano dopo ogni tentativo.
 * Revisione R17 (R17-04): il manuale e il piano della sicurezza si mettono in firma dalla
 * pagina «Documentazione del servizio» (versione stampabile HTML generata); il dialogo di
 * firma MOSTRA quel documento (cornice isolata, senza script), non un file di dati.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { motivoErrore } from "../api/errori";
import { EsitoCritico } from "../components/EsitoCritico";
import { Vuoto } from "../components/Vuoto";
import { CampoMotivo, DialogoLaterale, InLettura, motivoValido, quando } from "../console/comuni";
import {
  STATO_FIRMA, annullaRichiesta, chiediOtp, firmaConOtp, leggiFirme, leggiTestoDocumento,
  salvaFile, scaricaBusta, scaricaContenuto, type DocumentoDaFirmare,
} from "../console/firme_responsabile";

/** Il documento da firmare, mostrato così com'è (HTML stampabile in una cornice isolata). */
function Anteprima({ d }: { d: DocumentoDaFirmare }) {
  const q = useQuery({ queryKey: ["firma-anteprima", d.id], queryFn: () => leggiTestoDocumento(d.id),
                       enabled: d.nome.endsWith(".html"), staleTime: Infinity });
  if (!d.nome.endsWith(".html")) return null;
  if (q.isLoading) return <InLettura testo="Lettura del documento da firmare…" />;
  if (q.isError || q.data === undefined) return <EsitoCritico titolo="Documento non mostrato" errore={q.error} azione="consultazione" onRiprova={() => q.refetch()} />;
  // sandbox vuoto: niente script, niente moduli, niente navigazione; stessi byte che si firmano
  return <iframe className="vd-anteprima-documento" title={`Documento da firmare: ${d.titolo}`} sandbox="" srcDoc={q.data} />;
}

function OraCella({ iso, etichetta }: { iso: string | null | undefined; etichetta: string }) {
  const q = quando(iso);
  return (
    <td className="vd-ora" data-label={etichetta}>
      {q ? <>{q.ora}<span className="vd-sotto vd-sotto-normale">{q.giorno}</span></> : <span className="vd-ora--attesa">—</span>}
    </td>
  );
}

function Documento({ d }: { d: DocumentoDaFirmare }) {
  return (
    <td data-label="Documento" className="vd-a-capo">
      <span className="vd-forte">{d.titolo}</span>
      <span className="vd-sotto">{d.tipo_descrizione} · <span className="vd-mono">{d.nome}</span> · {(d.dimensione / 1024).toLocaleString("it-IT", { maximumFractionDigits: 1 })} kB</span>
      <span className="vd-sotto vd-mono vd-a-capo" title="Impronta SHA-256 del documento">{d.impronta_sha256}</span>
    </td>
  );
}

function Firma({ d, onFatto }: { d: DocumentoDaFirmare; onFatto: () => void }) {
  const qc = useQueryClient();
  const [otp, setOtp] = useState("");
  const [pin, setPin] = useState("");
  const invio = useMutation({ mutationFn: () => chiediOtp(d.id) });
  const firma = useMutation({
    mutationFn: () => firmaConOtp(d.id, otp.trim(), pin),
    onSettled: () => { setOtp(""); setPin(""); },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["firme-responsabile"] }); },
  });
  const leggi = useMutation({ mutationFn: () => scaricaContenuto(d.id), onSuccess: b => salvaFile(b, d.nome) });
  const otpValido = /^[0-9A-Za-z]{4,16}$/.test(otp.trim());
  if (firma.isSuccess) {
    return (
      <div className="vd-pila">
        <div className="vd-esito vd-esito--ok" role="status">
          <p className="vd-esito-titolo">Documento firmato</p>
          <p>Firma {firma.data.livello ?? ""} verificata{firma.data.firmatario ? ` di ${firma.data.firmatario}` : ""}. Il registro la riverifica e la archivia nel WORM al prossimo giro.</p>
          {firma.data.avvisi.length > 0 && <ul style={{ paddingLeft: 20 }}>{firma.data.avvisi.map(a => <li key={a}>{a}</li>)}</ul>}
        </div>
        <div className="vd-comandi"><button type="button" className="vd-bottone" onClick={onFatto}>Torna all'elenco</button></div>
      </div>
    );
  }
  return (
    <div className="vd-pila">
      <Anteprima d={d} />
      <ol className="vd-elenco" style={{ paddingLeft: 20 }}>
        <li>Leggi il documento: si firmano esattamente questi byte.
          <span className="vd-sotto vd-sotto-normale">Impronta SHA-256: <span className="vd-mono vd-a-capo">{d.impronta_sha256}</span></span>
          <span className="vd-comandi-cella" style={{ marginTop: 4 }}>
            <button type="button" className="vd-bottone vd-bottone--secondario" disabled={leggi.isPending} onClick={() => leggi.mutate()}>
              {leggi.isPending ? "Scaricamento…" : "Leggi il documento"}</button>
          </span>
          {leggi.isError && <span className="vd-stato vd-stato--critico vd-a-capo">{motivoErrore(leggi.error, "consultazione")}</span>}
        </li>
        <li>Chiedi l'OTP: il prestatore lo invia a te, titolare della credenziale personale.
          <span className="vd-comandi-cella" style={{ marginTop: 4 }}>
            <button type="button" className="vd-bottone vd-bottone--secondario" disabled={invio.isPending} onClick={() => invio.mutate()}>
              {invio.isPending ? "Richiesta…" : invio.isSuccess ? "Chiedi un nuovo OTP" : "Chiedi l'OTP"}</button>
          </span>
          <span role="status">
            {invio.isSuccess && <span className="vd-stato vd-stato--ok vd-a-capo">OTP richiesto al prestatore: controlla il dispositivo o il canale indicato dal prestatore.</span>}
            {invio.isError && <span className="vd-stato vd-stato--critico vd-a-capo">{motivoErrore(invio.error, "operazione")}</span>}
          </span>
        </li>
        <li>Inserisci l'OTP e firma.</li>
      </ol>
      <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); if (otpValido) firma.mutate(); }}>
        <div className="vd-griglia">
          <div className="vd-campo">
            <label htmlFor={`vd-otp-${d.id}`}>Codice OTP</label>
            <input id={`vd-otp-${d.id}`} className="vd-input vd-mono" value={otp} inputMode="numeric" autoComplete="one-time-code"
                   spellCheck={false} maxLength={16} onChange={e => setOtp(e.target.value)} aria-describedby={`vd-otp-${d.id}-aiuto`} />
            <span id={`vd-otp-${d.id}-aiuto`} className="vd-aiuto">Vale per una sola firma; non viene conservato.</span>
          </div>
          <div className="vd-campo">
            <label htmlFor={`vd-pin-${d.id}`}>PIN <span className="vd-nota">(se la credenziale lo chiede)</span></label>
            <input id={`vd-pin-${d.id}`} className="vd-input" type="password" value={pin} autoComplete="off"
                   maxLength={64} onChange={e => setPin(e.target.value)} />
          </div>
        </div>
        {firma.isError && <EsitoCritico titolo="Documento non firmato" errore={firma.error} azione="operazione"
                                        spiegazione="Nessuna firma è stata registrata. Con un OTP rifiutato chiedine uno nuovo." />}
        <div className="vd-comandi">
          <button type="submit" className="vd-bottone" disabled={!otpValido || firma.isPending}>
            {firma.isPending ? "Firma in corso…" : "Firma il documento"}</button>
        </div>
      </form>
      <Annulla d={d} onFatto={onFatto} />
    </div>
  );
}

function Annulla({ d, onFatto }: { d: DocumentoDaFirmare; onFatto: () => void }) {
  const qc = useQueryClient();
  const [aperto, setAperto] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [toccato, setToccato] = useState(false);
  const annulla = useMutation({
    mutationFn: () => annullaRichiesta(d.id, motivo.trim()),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["firme-responsabile"] }); onFatto(); },
  });
  if (!aperto) {
    return <p><button type="button" className="vd-comando-testo" onClick={() => setAperto(true)}>Annulla la richiesta di firma</button></p>;
  }
  return (
    <form className="vd-modulo" noValidate onSubmit={e => { e.preventDefault(); setToccato(true); if (motivoValido(motivo)) annulla.mutate(); }}>
      <CampoMotivo id={`vd-annulla-${d.id}`} valore={motivo} onCambia={setMotivo} toccato={toccato} onTocca={() => setToccato(true)}
                   etichetta="Perché la richiesta si annulla" />
      {annulla.isError && <EsitoCritico titolo="Richiesta non annullata" errore={annulla.error} azione="operazione" />}
      <div className="vd-comandi">
        <button type="submit" className="vd-bottone vd-bottone--secondario" disabled={annulla.isPending}>Annulla la richiesta</button>
        <button type="button" className="vd-comando-testo" onClick={() => setAperto(false)}>Lascia da firmare</button>
      </div>
    </form>
  );
}

function ScaricaBusta({ d }: { d: DocumentoDaFirmare }) {
  const s = useMutation({ mutationFn: () => scaricaBusta(d.id), onSuccess: b => salvaFile(b, `${d.nome}.p7m`) });
  return (
    <span className="vd-comandi-cella">
      <button type="button" className="vd-comando-testo" disabled={s.isPending} onClick={() => s.mutate()}>Scarica la busta (.p7m)</button>
      {s.isError && <span className="vd-stato vd-stato--critico vd-a-capo">{motivoErrore(s.error, "consultazione")}</span>}
    </span>
  );
}

export default function FirmeResponsabilePage() {
  const q = useQuery({ queryKey: ["firme-responsabile"], queryFn: leggiFirme });
  const [inFirma, setInFirma] = useState<DocumentoDaFirmare | null>(null);
  const items = q.data?.items ?? [];
  const daFirmare = items.filter(d => d.stato === "DA_FIRMARE");
  const altri = items.filter(d => d.stato !== "DA_FIRMARE");
  const comandi = (
    <Link to="/documentazione-servizio" className="vd-bottone vd-bottone--secondario">Metti in firma il manuale o il piano</Link>
  );
  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Firme del responsabile</h1>
        <span className="vd-nota">
          Manuale, piano della sicurezza, nomine e altri documenti che il responsabile del servizio firma con la propria firma qualificata personale e l'OTP del prestatore. La credenziale personale si registra in <Link to="/configurazione/firma-qualificata" className="vd-riga-link">Fornitori di firma e marca</Link>.
        </span>
        <div className="vd-testa-comandi">{comandi}</div>
      </div>
      {q.isLoading ? <InLettura testo="Lettura dei documenti da firmare…" />
        : q.isError || !q.data ? <EsitoCritico titolo="Documenti non disponibili" errore={q.error} spiegazione="Non significa che non ce ne siano." onRiprova={() => q.refetch()} />
        : (
          <div className="vd-pila">
            <section aria-labelledby="vd-fr-da-firmare">
              <div className="vd-sezione-testa"><h2 id="vd-fr-da-firmare" className="vd-etichetta">Da firmare</h2>
                <span className="vd-nota">{daFirmare.length} {daFirmare.length === 1 ? "documento" : "documenti"}</span></div>
              {daFirmare.length === 0 ? (
                <Vuoto titolo="Nessun documento da firmare">
                  <p>Quando un servizio prepara un documento da firmare compare qui, con l'impronta di ciò che firmerai. Il manuale e il piano della sicurezza si mettono in firma dalla versione stampabile generata.</p>
                  <div className="vd-comandi"><Link to="/documentazione-servizio" className="vd-bottone">Vai alla documentazione del servizio</Link></div>
                </Vuoto>
              ) : (
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Documenti da firmare, dal più recente</caption>
                  <thead><tr><th scope="col">Richiesto</th><th scope="col">Documento</th><th scope="col">Comandi</th></tr></thead>
                  <tbody>
                    {daFirmare.map(d => (
                      <tr key={d.id}>
                        <OraCella iso={d.richiesto_il} etichetta="Richiesto" />
                        <Documento d={d} />
                        <td data-label="Comandi">
                          <button type="button" className="vd-bottone vd-bottone--secondario" aria-haspopup="dialog" onClick={() => setInFirma(d)}>Firma</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
            <section aria-labelledby="vd-fr-firmati">
              <div className="vd-sezione-testa"><h2 id="vd-fr-firmati" className="vd-etichetta">Firmati e chiusi</h2></div>
              {altri.length === 0 ? (
                <Vuoto titolo="Nessun documento firmato"><p>Le firme registrate, con lo stato dell'archiviazione nel WORM, compariranno qui.</p></Vuoto>
              ) : (
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Documenti firmati, annullati o respinti, dal più recente</caption>
                  <thead><tr><th scope="col">Firmato</th><th scope="col">Documento</th><th scope="col">Stato</th><th scope="col">Comandi</th></tr></thead>
                  <tbody>
                    {altri.map(d => {
                      const st = STATO_FIRMA[d.stato];
                      return (
                        <tr key={d.id}>
                          <OraCella iso={d.firmato_il} etichetta="Firmato" />
                          <Documento d={d} />
                          <td data-label="Stato" style={{ whiteSpace: "normal" }}>
                            <span className={`vd-stato ${st.classe}`}>{st.testo}</span>
                            <span className="vd-sotto vd-sotto-normale">{st.spiegazione}</span>
                            {d.certificato_soggetto && <span className="vd-sotto vd-sotto-normale vd-a-capo">{d.certificato_soggetto}{d.livello ? ` · ${d.livello}` : ""}</span>}
                            {d.motivo && <span className="vd-sotto vd-sotto-normale vd-a-capo">{d.motivo}</span>}
                          </td>
                          <td data-label="Comandi">{(d.stato === "FIRMATO" || d.stato === "ARCHIVIATO") ? <ScaricaBusta d={d} /> : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </section>
          </div>
        )}
      {inFirma && (
        <DialogoLaterale id="vd-fr-firma" titolo="Firma personale" sottotitolo={inFirma.titolo} onClose={() => setInFirma(null)}>
          <Firma d={inFirma} onFatto={() => setInFirma(null)} />
        </DialogoLaterale>
      )}
    </>
  );
}
