// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Contratti di servizio tra conservatore ed enti — sistema «Partenze» (VD-DR-01).
 * VD-C-014: un errore del servizio non si mostra più come «nessun contratto».
 * g3-frontend: modulo di registrazione per l'amministrazione del servizio
 * (POST /dashboard/contratti, solo SUPER_ADMIN: decisione del coordinatore), con
 * gli stessi controlli del servizio (ContrattoCreate), errori per campo (anche dal
 * 422 del servizio), riepilogo da confermare, 409 per un numero già registrato.
 * Il responsabile del servizio consulta in sola lettura e la pagina lo dice.
 * I profili globali scelgono l'ente (?tenant_id=).
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

const giornoRoma = (d: Date) => d.toLocaleDateString("sv-SE", { timeZone: "Europe/Rome" });
const giorniA = (iso: string) => {
  const g = /^\d{4}-\d{2}-\d{2}$/.test(String(iso)) ? String(iso) : giornoRoma(new Date(iso));
  return Math.round((Date.parse(g + "T00:00:00Z") - Date.parse(giornoRoma(new Date()) + "T00:00:00Z")) / 86_400_000);
};

const STATO: Record<string, { testo: string; classe: string }> = {
  ATTIVO:   { testo: "Attivo", classe: "vd-stato--ok" },
  SCADUTO:  { testo: "Scaduto", classe: "vd-stato--critico" },
  BOZZA:    { testo: "Bozza", classe: "vd-stato--neutro" },
  REVOCATO: { testo: "Revocato", classe: "vd-stato--critico" },
};

// ── Modulo di registrazione ────────────────────────────────────────
interface Bozza { numero_contratto: string; data_stipula: string; data_scadenza: string; piano: string; valore_annuo_eur: string }
const VUOTA: Bozza = { numero_contratto: "", data_stipula: "", data_scadenza: "", piano: "", valore_annuo_eur: "" };
const NUMERO = /^[A-Za-z0-9][A-Za-z0-9 ./_-]*$/;
const DATA = /^\d{4}-\d{2}-\d{2}$/;
const euro = (v: string) => Number(v.replace(",", "."));

/** Stessi controlli di ContrattoCreate (dashboard-api). */
function controlla(b: Bozza): Partial<Record<keyof Bozza, string>> {
  const e: Partial<Record<keyof Bozza, string>> = {};
  const n = b.numero_contratto.trim();
  if (!n) e.numero_contratto = "Indica il numero del contratto.";
  else if (n.length > 100) e.numero_contratto = "Al massimo 100 caratteri.";
  else if (!NUMERO.test(n)) e.numero_contratto = "Lettere, cifre, spazi e . / _ - ; deve iniziare con una lettera o una cifra.";
  if (!DATA.test(b.data_stipula)) e.data_stipula = "Indica la data di stipula.";
  if (b.data_scadenza && !DATA.test(b.data_scadenza)) e.data_scadenza = "Data non valida.";
  else if (b.data_scadenza && DATA.test(b.data_stipula) && b.data_scadenza <= b.data_stipula) e.data_scadenza = "La scadenza deve essere successiva alla stipula.";
  const p = b.piano.trim();
  if (!p) e.piano = "Indica il piano o il tipo di servizio.";
  else if (p.length > 50) e.piano = "Al massimo 50 caratteri.";
  if (b.valore_annuo_eur.trim()) {
    const v = euro(b.valore_annuo_eur.trim());
    if (!Number.isFinite(v) || v < 0) e.valore_annuo_eur = "Un importo in euro, zero o positivo.";
  }
  return e;
}

/** Errori del servizio riportati sui campi (422 per campo, 409 numero, validatore delle date). */
function erroriServizio(err: unknown): { campi: Partial<Record<keyof Bozza, string>>; generale: string | null } {
  const l = leggiErrore(err);
  const campi: Partial<Record<keyof Bozza, string>> = {};
  for (const [k, v] of Object.entries(l.campi ?? {})) if (k in VUOTA) campi[k as keyof Bozza] = v;
  if (l.stato === 409) campi.numero_contratto = l.testo || "Numero di contratto già registrato.";
  if (l.stato === 422 && /data[_ ]scadenza/.test(l.testo) && !campi.data_scadenza) campi.data_scadenza = "La scadenza deve essere successiva alla stipula.";
  const generale = Object.keys(campi).length ? null : motivoDaLetto(l, "operazione");
  return { campi, generale };
}

function ModuloContratto({ tenantId, nomeEnte, onChiudi }: { tenantId: string; nomeEnte?: string; onChiudi: (registrato?: any) => void }) {
  const qc = useQueryClient();
  const [b, setB] = useState<Bozza>(VUOTA);
  const [tentato, setTentato] = useState(false);
  const [fase, setFase] = useState<"modulo" | "conferma">("modulo");
  const reg = useMutation({
    mutationFn: (corpo: object) => api.post("/dashboard/contratti", corpo).then(r => r.data),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["contratti"] }); onChiudi(r); },
    onError: () => setFase("modulo"),
  });

  const locali = tentato ? controlla(b) : {};
  const server = reg.isError ? erroriServizio(reg.error) : { campi: {}, generale: null };
  const errore = (k: keyof Bozza) => locali[k] ?? (server.campi as Record<string, string>)[k] ?? null;
  const imposta = (k: keyof Bozza) => (v: string) => { setB(x => ({ ...x, [k]: v })); if (reg.isError) reg.reset(); };

  const avanti = (e: FormEvent) => {
    e.preventDefault();
    setTentato(true);
    if (Object.keys(controlla(b)).length === 0) setFase("conferma");
  };
  const registra = () => reg.mutate({
    tenant_id: tenantId,
    numero_contratto: b.numero_contratto.trim(),
    data_stipula: b.data_stipula,
    data_scadenza: b.data_scadenza || null,
    piano: b.piano.trim(),
    valore_annuo_eur: b.valore_annuo_eur.trim() ? euro(b.valore_annuo_eur.trim()) : null,
  });
  const nErrori = Object.keys(locali).length;

  if (fase === "conferma") {
    return (
      <Riepilogo
        titolo="Controlla il contratto prima di registrarlo"
        voci={[
          ["Ente", nomeEnte ?? <span className="vd-mono">{tenantId}</span>],
          ["Numero", <span className="vd-mono">{b.numero_contratto.trim()}</span>],
          ["Stipulato il", dataIt(b.data_stipula)],
          ["Scadenza", b.data_scadenza ? dataIt(b.data_scadenza) : "senza scadenza"],
          ["Piano o servizio", b.piano.trim()],
          ["Valore annuo", b.valore_annuo_eur.trim() ? euro(b.valore_annuo_eur.trim()).toLocaleString("it-IT", { style: "currency", currency: "EUR" }) : null],
        ]}
        avviso="La registrazione resta nel registro delle operazioni e non si cancella: un contratto sbagliato va revocato."
        inCorso={reg.isPending}
        onConferma={registra}
        onCorreggi={() => setFase("modulo")}
        comando="Conferma e registra"
      />
    );
  }

  return (
    <form className="vd-modulo" onSubmit={avanti} noValidate aria-labelledby="vd-nuovo-contratto">
      <h2 id="vd-nuovo-contratto">Registra un contratto{nomeEnte ? ` per ${nomeEnte}` : ""}</h2>
      <div className="vd-griglia">
        <Campo id="vd-c-numero" etichetta="Numero del contratto" obbligatorio mono valore={b.numero_contratto}
               onCambia={imposta("numero_contratto")} errore={errore("numero_contratto")} aiuto="Unico nel servizio, per esempio CTR-2026-014"
               extra={{ maxLength: 100, autoFocus: true }} />
        <Campo id="vd-c-piano" etichetta="Piano o tipo di servizio" obbligatorio valore={b.piano}
               onCambia={imposta("piano")} errore={errore("piano")} aiuto="Per esempio business o enterprise" extra={{ maxLength: 50 }} />
        <Campo id="vd-c-stipula" etichetta="Data di stipula" tipo="date" obbligatorio valore={b.data_stipula}
               onCambia={imposta("data_stipula")} errore={errore("data_stipula")} aiuto="Il contratto decorre da questa data" />
        <Campo id="vd-c-scadenza" etichetta="Data di scadenza" tipo="date" valore={b.data_scadenza}
               onCambia={imposta("data_scadenza")} errore={errore("data_scadenza")} aiuto="Vuota se il contratto non scade" />
        <Campo id="vd-c-valore" etichetta="Valore annuo in euro" valore={b.valore_annuo_eur}
               onCambia={imposta("valore_annuo_eur")} errore={errore("valore_annuo_eur")} aiuto="Facoltativo, per esempio 12000"
               extra={{ inputMode: "decimal" }} />
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
            Contratto non registrato: {server.generale ?? "correggi i campi indicati"}.
          </span>
        )}
      </div>
    </form>
  );
}

export default function ContrattiPage() {
  const { user } = useAuth();
  const ente = useEnte();
  const tenantId = ente.tenantId;
  const puòRegistrare = user?.ruolo === "SUPER_ADMIN";
  const [q, setQ] = useState("");
  const [modulo, setModulo] = useState(false);
  const [registrato, setRegistrato] = useState<any | null>(null);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["contratti", tenantId],
    queryFn: () =>
      api.get("/dashboard/contratti", { params: { tenant_id: tenantId } }).then((r) => r.data),
    enabled: !!tenantId,
  });

  const erroreServizio: string | null = isError ? motivoErrore(error) : null;

  const tutti: any[] = Array.isArray(data?.items) ? data.items : [];
  const items = tutti.filter((c: any) =>
    !q || [c.ragione_sociale, c.tenant_nome, c.numero_contratto, c.tipo_servizio]
      .some(v => String(v ?? "").toLowerCase().includes(q.trim().toLowerCase())),
  );

  return (
    <>
      <div className="vd-testa-pagina">
        <h1>Contratti</h1>
        <span className="vd-nota">
          I contratti di servizio con gli enti: decorrenza, scadenza e stato di ciascuno.
        </span>
        {puòRegistrare && tenantId && !modulo && (
          <div className="vd-testa-comandi">
            <button type="button" className="vd-bottone" onClick={() => { setModulo(true); setRegistrato(null); }}>
              Registra un contratto
            </button>
          </div>
        )}
      </div>

      {ente.globale && (
        <div className="vd-filtri">
          <SceltaEnte id="vd-contratti-ente" stato={ente} onCambia={() => { setModulo(false); setRegistrato(null); setQ(""); }} />
        </div>
      )}

      <div className="vd-pila">
        {!puòRegistrare && (
          <p className="vd-avviso vd-avviso--neutro">
            <strong>Sola lettura.</strong> I contratti li registra l'amministrazione del servizio; il tuo profilo li consulta.
          </p>
        )}

        {registrato && (
          <div className="vd-esito vd-esito--ok" role="status">
            <p className="vd-esito-titolo">Contratto registrato: <span className="vd-mono">{registrato.numero_contratto}</span></p>
            <p>
              {registrato.data_inizio ? `Decorre dal ${dataIt(registrato.data_inizio)}` : "Registrato"}
              {registrato.data_fine ? `, scade il ${dataIt(registrato.data_fine)}` : ", senza scadenza"}.
              {" "}Stato: {(STATO[registrato.stato]?.testo ?? registrato.stato ?? "non indicato").toLowerCase()}.
            </p>
          </div>
        )}

        {modulo && puòRegistrare && tenantId && (
          <ModuloContratto tenantId={tenantId} nomeEnte={ente.nomeEnte}
                           onChiudi={(r) => { setModulo(false); if (r) setRegistrato(r); }} />
        )}

        <section aria-labelledby="vdc-contratti">
          <div className="vd-sezione-testa">
            <h2 id="vdc-contratti" className="vd-etichetta">Contratti dell'ente</h2>
            {data && !erroreServizio && <span className="vd-nota">{tutti.length} {tutti.length === 1 ? "contratto" : "contratti"}{ente.nomeEnte ? ` · ${ente.nomeEnte}` : ""}</span>}
          </div>

          {!tenantId ? (
            <p className="vd-nota" style={{ paddingBlock: 12 }}>
              {ente.globale ? "Scegli un ente per vedere i suoi contratti." : "Il tuo profilo non è collegato a un ente: non c'è un elenco di contratti da mostrare."}
            </p>
          ) : isLoading ? (
            <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Lettura dei contratti…</p>
          ) : erroreServizio ? (
            <EsitoCritico titolo="Contratti non disponibili" errore={error}
                          spiegazione="Non è possibile dire quali contratti sono in vigore: non significa che non ce ne siano."
                          onRiprova={() => refetch()} inRiprova={isFetching} />
          ) : tutti.length === 0 ? (
            <div className="vd-esito">
              <p className="vd-esito-titolo">Nessun contratto registrato per questo ente</p>
              <p className="vd-nota">
                {puòRegistrare ? "Registralo con «Registra un contratto»." : "La registrazione spetta all'amministrazione del servizio."}
              </p>
            </div>
          ) : (
            <>
              <div className="vd-filtri" style={{ maxWidth: 520 }}>
                <div className="vd-campo">
                  <label htmlFor="vdc-cerca-contratti">Cerca nei contratti</label>
                  <input id="vdc-cerca-contratti" type="search" className="vd-input" value={q}
                         onChange={(e) => setQ(e.target.value)} aria-describedby="vdc-cerca-aiuto" />
                  <span id="vdc-cerca-aiuto" className="vd-aiuto">Ente, numero del contratto o tipo di servizio</span>
                </div>
              </div>
            {items.length === 0 ? (
              <p className="vd-nota" role="status" style={{ paddingBlock: 12 }}>Nessun contratto corrisponde a «{q}».</p>
            ) : (
              <div className="vd-scorri">
                <table className="vd-tabellone vd-impila">
                  <caption className="vd-sr">Contratti dell'ente, con scadenza e stato</caption>
                  <thead>
                    <tr><th scope="col">Scadenza</th><th scope="col">Ente e piano</th><th scope="col">Numero</th><th scope="col">Decorrenza</th><th scope="col">Stato</th></tr>
                  </thead>
                  <tbody>
                    {items.map((c: any, i: number) => {
                      const s = STATO[c.stato] ?? { testo: c.stato ? String(c.stato) : "Non indicato", classe: "vd-stato--neutro" };
                      const g = c.data_fine ? giorniA(c.data_fine) : null;
                      const inScadenza = c.stato === "ATTIVO" && g !== null && g >= 0 && g <= 90;
                      const oltre = c.stato === "ATTIVO" && g !== null && g < 0;
                      return (
                        <tr key={c.id ?? i}>
                          <td className="vd-ora" data-label="Scadenza">
                            {dataIt(c.data_fine) ?? "Nessuna"}
                            <span className="vd-sotto" style={{ fontStretch: "100%", fontWeight: 500 }}>
                              {g === null ? "senza scadenza" : g === 0 ? "oggi" : g > 0 ? `tra ${g.toLocaleString("it-IT")} gg` : `scaduta da ${(-g).toLocaleString("it-IT")} gg`}
                            </span>
                          </td>
                          <td className="vd-a-capo" style={{ fontWeight: 600 }} data-label="Ente e piano">
                            {c.ragione_sociale || c.tenant_nome || "Ente non indicato"}
                            {c.tipo_servizio && <span className="vd-sotto" style={{ fontWeight: 400 }}>{c.tipo_servizio}</span>}
                          </td>
                          <td data-label="Numero">{c.numero_contratto ? <span className="vd-mono">{c.numero_contratto}</span> : <span className="vd-nota">non indicato</span>}</td>
                          <td data-label="Decorrenza">{dataIt(c.data_inizio) ?? <span className="vd-nota">non indicata</span>}</td>
                          <td data-label="Stato">
                            <span className={`vd-stato ${s.classe}`}>{s.testo}</span>
                            {inScadenza && <span className="vd-sotto vd-stato vd-stato--rischio" style={{ fontSize: 13 }}>Scade entro 90 giorni</span>}
                            {oltre && <span className="vd-sotto vd-stato vd-stato--critico" style={{ fontSize: 13 }}>Data di fine superata</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
