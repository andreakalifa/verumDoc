// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Nomine del responsabile del servizio di conservazione — sistema «Partenze» (VD-DR-01).
 * VD-C-014: un errore del servizio non è mai «nessuna nomina». Caricamento,
 * errore e assenza di nomina sono tre stati distinti.
 * g3-frontend: modulo di registrazione per l'amministrazione del servizio
 * (POST /dashboard/nomine-rsc, solo SUPER_ADMIN), con gli stessi controlli del
 * servizio (NominaCreate: nome, codice fiscale di persona fisica, e-mail, PEC,
 * date coerenti), errori per campo, riepilogo da confermare che avvisa della
 * revoca della nomina in carica. Il responsabile del servizio consulta in sola
 * lettura. I profili globali scelgono l'ente (?tenant_id=).
 */
import { giorno } from "../console/comuni";
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { leggiErrore, motivoDaLetto, motivoErrore } from "../api/errori";
import { SceltaEnte, useEnte } from "../components/SceltaEnte";
import { Campo, Riepilogo } from "../components/Modulo";
import { EsitoCritico } from "../components/EsitoCritico";

/** Revisione R17 (R17-20): giorno in ora italiana (Europe/Rome), non il giorno UTC. */
const dataIt = (iso?: string | null) => (iso ? giorno(iso) ?? String(iso) : null);

// ── Modulo di registrazione ────────────────────────────────────────
interface Bozza { nome_rsc: string; codice_fiscale_rsc: string; email_rsc: string; pec_rsc: string; data_nomina: string; data_scadenza: string }
const VUOTA: Bozza = { nome_rsc: "", codice_fiscale_rsc: "", email_rsc: "", pec_rsc: "", data_nomina: "", data_scadenza: "" };
const CF_PERSONA = /^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const DATA = /^\d{4}-\d{2}-\d{2}$/;
const cf = (v: string) => v.replace(/\s+/g, "").toUpperCase();

/** Stessi controlli di NominaCreate (dashboard-api). */
function controlla(b: Bozza): Partial<Record<keyof Bozza, string>> {
  const e: Partial<Record<keyof Bozza, string>> = {};
  const n = b.nome_rsc.trim();
  if (n.length < 2) e.nome_rsc = "Indica nome e cognome.";
  else if (n.length > 300) e.nome_rsc = "Al massimo 300 caratteri.";
  if (!CF_PERSONA.test(cf(b.codice_fiscale_rsc))) e.codice_fiscale_rsc = "Codice fiscale di una persona fisica: 16 caratteri.";
  if (!EMAIL.test(b.email_rsc.trim()) || b.email_rsc.trim().length > 255) e.email_rsc = "Indirizzo di posta non valido.";
  if (b.pec_rsc.trim() && (!EMAIL.test(b.pec_rsc.trim()) || b.pec_rsc.trim().length > 255)) e.pec_rsc = "Indirizzo PEC non valido.";
  if (!DATA.test(b.data_nomina)) e.data_nomina = "Indica la data della nomina.";
  if (b.data_scadenza && !DATA.test(b.data_scadenza)) e.data_scadenza = "Data non valida.";
  else if (b.data_scadenza && DATA.test(b.data_nomina) && b.data_scadenza <= b.data_nomina) e.data_scadenza = "La scadenza deve essere successiva alla nomina.";
  return e;
}

function ModuloNomina({ tenantId, nomeEnte, corrente, onChiudi }: { tenantId: string; nomeEnte?: string; corrente: any | null; onChiudi: (r?: any) => void }) {
  const qc = useQueryClient();
  const [b, setB] = useState<Bozza>(VUOTA);
  const [tentato, setTentato] = useState(false);
  const [fase, setFase] = useState<"modulo" | "conferma">("modulo");
  const reg = useMutation({
    mutationFn: (corpo: object) => api.post("/dashboard/nomine-rsc", corpo).then(r => r.data),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["nomine-rsc"] }); onChiudi({ ...r, nome: b.nome_rsc.trim() }); },
    onError: () => setFase("modulo"),
  });

  const locali = tentato ? controlla(b) : {};
  const l = reg.isError ? leggiErrore(reg.error) : null;
  const campiServer: Record<string, string> = {};
  if (l) {
    for (const [k, v] of Object.entries(l.campi ?? {})) if (k in VUOTA) campiServer[k] = v;
    if (l.stato === 422 && /data[_ ]scadenza/.test(l.testo) && !campiServer.data_scadenza) campiServer.data_scadenza = "La scadenza deve essere successiva alla nomina.";
  }
  const generale = l && Object.keys(campiServer).length === 0 ? motivoDaLetto(l, "operazione") : null;
  const errore = (k: keyof Bozza) => locali[k] ?? campiServer[k] ?? null;
  const imposta = (k: keyof Bozza) => (v: string) => { setB(x => ({ ...x, [k]: v })); if (reg.isError) reg.reset(); };
  const nErrori = Object.keys(locali).length;

  const avanti = (e: FormEvent) => {
    e.preventDefault();
    setTentato(true);
    if (Object.keys(controlla(b)).length === 0) setFase("conferma");
  };
  const registra = () => reg.mutate({
    tenant_id: tenantId,
    nome_rsc: b.nome_rsc.trim(),
    codice_fiscale_rsc: cf(b.codice_fiscale_rsc),
    email_rsc: b.email_rsc.trim(),
    pec_rsc: b.pec_rsc.trim() || null,
    data_nomina: b.data_nomina,
    data_scadenza: b.data_scadenza || null,
  });

  if (fase === "conferma") {
    const nomeCorrente = corrente ? [corrente.nome, corrente.cognome].filter(Boolean).join(" ") : "";
    return (
      <Riepilogo
        titolo="Controlla la nomina prima di registrarla"
        voci={[
          ["Ente", nomeEnte ?? <span className="vd-mono">{tenantId}</span>],
          ["Responsabile", b.nome_rsc.trim()],
          ["Codice fiscale", <span className="vd-mono">{cf(b.codice_fiscale_rsc)}</span>],
          ["Posta elettronica", b.email_rsc.trim()],
          ["PEC", b.pec_rsc.trim() || null],
          ["In carica dal", dataIt(b.data_nomina)],
          ["Scadenza", b.data_scadenza ? dataIt(b.data_scadenza) : "senza scadenza"],
        ]}
        avviso={corrente
          ? <><strong>La nomina in carica sarà revocata.</strong> {nomeCorrente || "Il responsabile attuale"} risulterà revocato dal {dataIt(b.data_nomina)}: per ogni ente c'è una sola nomina in carica, e le nomine non si cancellano.</>
          : "Le nomine non si cancellano: una nomina sbagliata si sostituisce con una nuova."}
        inCorso={reg.isPending}
        onConferma={registra}
        onCorreggi={() => setFase("modulo")}
        comando="Conferma e registra"
      />
    );
  }

  return (
    <form className="vd-modulo" onSubmit={avanti} noValidate aria-labelledby="vd-nuova-nomina">
      <h2 id="vd-nuova-nomina">Registra una nomina{nomeEnte ? ` per ${nomeEnte}` : ""}</h2>
      <div className="vd-griglia">
        <Campo id="vd-n-nome" etichetta="Nome e cognome" obbligatorio valore={b.nome_rsc} onCambia={imposta("nome_rsc")}
               errore={errore("nome_rsc")} extra={{ maxLength: 300, autoFocus: true }} />
        <Campo id="vd-n-cf" etichetta="Codice fiscale" obbligatorio mono valore={b.codice_fiscale_rsc}
               onCambia={v => imposta("codice_fiscale_rsc")(v.toUpperCase())} errore={errore("codice_fiscale_rsc")}
               aiuto="Della persona nominata, 16 caratteri" extra={{ maxLength: 16, spellCheck: false }} />
        <Campo id="vd-n-email" etichetta="Posta elettronica" tipo="email" obbligatorio valore={b.email_rsc}
               onCambia={imposta("email_rsc")} errore={errore("email_rsc")} extra={{ maxLength: 255 }} />
        <Campo id="vd-n-pec" etichetta="PEC" tipo="email" valore={b.pec_rsc} onCambia={imposta("pec_rsc")}
               errore={errore("pec_rsc")} aiuto="Facoltativa" extra={{ maxLength: 255 }} />
        <Campo id="vd-n-data" etichetta="In carica dal" tipo="date" obbligatorio valore={b.data_nomina}
               onCambia={imposta("data_nomina")} errore={errore("data_nomina")} aiuto="Data dell'atto di nomina" />
        <Campo id="vd-n-scadenza" etichetta="Scadenza della nomina" tipo="date" valore={b.data_scadenza}
               onCambia={imposta("data_scadenza")} errore={errore("data_scadenza")} aiuto="Vuota se la nomina non scade" />
      </div>
      <div className="vd-comandi" style={{ borderTop: "2px solid var(--vd-testo)", paddingTop: 18 }}>
        <button type="submit" className="vd-bottone">Controlla e registra</button>
        <button type="button" className="vd-bottone vd-bottone--secondario" onClick={() => onChiudi()}>Annulla</button>
        {nErrori > 0 && (
          <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
            {nErrori === 1 ? "Un campo da correggere." : `${nErrori} campi da correggere.`}
          </span>
        )}
        {reg.isError && (
          <span role="alert" className="vd-stato vd-stato--critico" style={{ whiteSpace: "normal" }}>
            Nomina non registrata: {generale ?? "correggi i campi indicati"}.
          </span>
        )}
      </div>
    </form>
  );
}

export default function NomineRSCPage() {
  const { user } = useAuth();
  const ente = useEnte();
  const tenantId = ente.tenantId;
  const puòRegistrare = user?.ruolo === "SUPER_ADMIN";
  const [modulo, setModulo] = useState(false);
  const [registrata, setRegistrata] = useState<any | null>(null);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["nomine-rsc", tenantId],
    queryFn: () => api.get("/dashboard/nomine-rsc", { params: { tenant_id: tenantId } }).then((r) => r.data),
    enabled: !!tenantId,
  });

  const erroreServizio: string | null = isError ? motivoErrore(error) : null;

  const nomina = data?.nomina_corrente ?? null;
  const storico: any[] = Array.isArray(data?.storico) ? data.storico : [];

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Nomine del responsabile del servizio</h1>
        <span className="vd-nota">
          Chi risponde del servizio di conservazione verso AgID e verso gli enti. La nomina è richiesta dal Regolamento AgID sui conservatori.
        </span>
        {puòRegistrare && tenantId && !modulo && !isLoading && !erroreServizio && (
          <div className="vd-testa-comandi">
            <button type="button" className="vd-bottone" onClick={() => { setModulo(true); setRegistrata(null); }}>
              Registra una nomina
            </button>
          </div>
        )}
      </div>

      {ente.globale && (
        <div className="vd-filtri">
          <SceltaEnte id="vd-nomine-ente" stato={ente} onCambia={() => { setModulo(false); setRegistrata(null); }} />
        </div>
      )}

      <div className="vd-pila">
        {!puòRegistrare && (
          <p className="vd-avviso vd-avviso--neutro">
            <strong>Sola lettura.</strong> Le nomine le registra l'amministrazione del servizio; il tuo profilo le consulta.
          </p>
        )}

        {registrata && (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">Nomina registrata: {registrata.nome}</p>
            <p>
              In carica dal {dataIt(registrata.data_nomina)}.
              {Array.isArray(registrata.revocate) && registrata.revocate.length > 0
                ? ` ${registrata.revocate.length === 1 ? "La nomina precedente è stata revocata" : `${registrata.revocate.length} nomine precedenti sono state revocate`} dalla stessa data.`
                : ""}
            </p>
          </div>
        )}

        {modulo && puòRegistrare && tenantId && (
          <ModuloNomina tenantId={tenantId} nomeEnte={ente.nomeEnte} corrente={data?.nomina_corrente ?? null}
                        onChiudi={(r) => { setModulo(false); if (r) setRegistrata(r); }} />
        )}

        <section aria-labelledby="vdc-corrente">
          <div className="vd-sezione-testa">
            <h2 id="vdc-corrente" className="vd-etichetta">Nomina in carica</h2>
          </div>

          {!tenantId ? (
            <p className="vd-nota" style={{ paddingBlock: 12 }}>
              {ente.globale ? "Scegli un ente per vedere le sue nomine." : "Il tuo profilo non è collegato a un ente."}
            </p>
          ) : isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura delle nomine…</p>
          ) : erroreServizio ? (
            <EsitoCritico titolo="Nomine non disponibili" errore={error}
                          spiegazione="Non è possibile dire se un responsabile è nominato: non significa che manchi."
                          onRiprova={() => refetch()} inRiprova={isFetching} />
          ) : !nomina ? (
            <div className="vd-esito vd-esito--rischio">
              <p className="vd-esito-titolo">Nessun responsabile del servizio in carica</p>
              <p>
                Il servizio non ha registrato nessuna nomina attiva. Senza un responsabile nominato il servizio non può essere
                qualificato presso AgID: la nomina va registrata prima della domanda.
              </p>
              <p className="vd-nota">{puòRegistrare ? "Registrala con «Registra una nomina»." : "La registrazione spetta all'amministrazione del servizio."}</p>
            </div>
          ) : (
            <div className="vd-scorri">
              <table className="vd-tabellone vd-impila vd-coppie" style={{ maxWidth: 820 }}>
                <caption className="vd-sr">Nomina in carica del responsabile del servizio</caption>
                <tbody>
                  <tr><th scope="row" className="vd-riga">Responsabile</th>
                      <td style={{ fontSize: 17, fontWeight: 700 }}>{[nomina.nome, nomina.cognome].filter(Boolean).join(" ") || <span className="vd-stato vd-stato--neutro">Nome non disponibile</span>}</td></tr>
                  <tr><th scope="row" className="vd-riga">Codice fiscale</th>
                      <td>{nomina.codice_fiscale ? <span className="vd-mono">{nomina.codice_fiscale}</span> : <span className="vd-stato vd-stato--neutro">Non disponibile</span>}</td></tr>
                  <tr><th scope="row" className="vd-riga">In carica dal</th>
                      <td className="vd-ora">{dataIt(nomina.data_nomina) ?? <span className="vd-stato vd-stato--neutro">Data non disponibile</span>}</td></tr>
                  <tr><th scope="row" className="vd-riga">Stato</th>
                      <td className="vd-stato vd-stato--ok">In carica</td></tr>
                </tbody>
              </table>
            </div>
          )}
        </section>

        {tenantId && !isLoading && !erroreServizio && (
          <section aria-labelledby="vdc-storico">
            <div className="vd-sezione-testa">
              <h2 id="vdc-storico" className="vd-etichetta">Storico delle nomine</h2>
              <span className="vd-nota">Dalla più recente</span>
            </div>
            {storico.length === 0 ? (
              <p className="vd-nota" style={{ paddingBlock: 12 }}>Nessuna nomina precedente registrata.</p>
            ) : (
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Storico delle nomine del responsabile del servizio</caption>
                  <thead>
                    <tr><th scope="col">Dal</th><th scope="col">Responsabile</th><th scope="col">Codice fiscale</th><th scope="col">Fine</th></tr>
                  </thead>
                  <tbody>
                    {storico.map((n: any, i: number) => (
                      <tr key={n.id ?? i}>
                        <td className="vd-ora" data-label="Dal">{dataIt(n.data_nomina) ?? "—"}</td>
                        <td style={{ fontWeight: 600 }} data-label="Responsabile">{[n.nome, n.cognome].filter(Boolean).join(" ") || "—"}</td>
                        <td data-label="Codice fiscale">{n.codice_fiscale ? <span className="vd-mono">{n.codice_fiscale}</span> : <span className="vd-nota">non disponibile</span>}</td>
                        <td data-label="Fine">
                          {n.data_revoca
                            ? <span className="vd-stato vd-stato--neutro">Revocata il {dataIt(n.data_revoca)}</span>
                            : n.attiva === true
                              ? <span className="vd-stato vd-stato--ok">In carica</span>
                              : <span className="vd-stato vd-stato--neutro">Senza data di fine</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
