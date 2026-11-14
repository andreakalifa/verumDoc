// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Lettura degli errori delle chiamate al servizio, in un solo punto.
 *
 * Regole: REGOLE_SVILUPPO §18 (errore del server ≠ assenza di rete; mai testo
 * di eccezioni verso il client; codice + messaggio + id di correlazione) e
 * VD-C-014 (un errore non è mai «nessun dato»).
 *
 * Forme accettate per il corpo d'errore (g3-frontend, allineate a g2-dati §6):
 *   - problem+json del catalogo errori (VD-C-100, forma unica di tutti i servizi da
 *     R03): `{type: "<URI>", title, status, detail?, codice, esito, ritentabile,
 *     dettagli, campi: [{campo, posizione, tipo, messaggio}], id_richiesta}`;
 *     `type` è un URI: il codice stabile è `codice`;
 *   - `{detail: {type, title, detail?, id_correlazione?}}`  ← forma di R02 di
 *     dashboard-api, distribution e hashchain (il proxy ripassa l'oggetto del
 *     servizio a valle per i 4xx; 5xx → 502 con id di correlazione);
 *   - `{detail: {codice, messaggio, riferimento?}}` e `{codice, messaggio}` (§18.1,
 *     onboarding e gateway);
 *   - `{detail: "testo"}` o `{detail: "<JSON del servizio>"}` (proxy vecchi);
 *   - `{detail: [{msg, loc}]}` (validazione FastAPI 422).
 * L'id di correlazione si cerca nel corpo (`id_richiesta`, `id_correlazione`,
 * `request_id`, `riferimento`) e nelle intestazioni `X-Request-ID` / `X-Correlation-ID`.
 */

export interface ErroreLetto {
  /** Codice HTTP; assente quando il servizio non ha risposto (rete, timeout). */
  stato?: number;
  /** Codice d'errore stabile del servizio (§18.2: `type` o `codice`), se presente. */
  codice?: string;
  /** Messaggio del servizio, già ripulito; stringa vuota se non c'è. */
  testo: string;
  /** Identificativo di correlazione da citare nella segnalazione. */
  riferimento?: string;
  /** Errori di validazione per campo (422), chiave = ultimo elemento del percorso del campo. */
  campi?: Record<string, string>;
  /** Classe dell'esito (catalogo errori): correggere, regola, problema del servizio. */
  esito?: "NON_VALIDO" | "RIFIUTATO" | "ERRORE";
  /** Se la stessa richiesta, ripetuta più tardi, può riuscire (catalogo errori). */
  ritentabile?: boolean;
}

type Oggetto = Record<string, unknown>;
const èOggetto = (v: unknown): v is Oggetto => !!v && typeof v === "object" && !Array.isArray(v);

function primoTesto(...valori: unknown[]): string | undefined {
  for (const v of valori) if (typeof v === "string" && v.trim()) return v.trim();
  return undefined;
}

/** Tenta di leggere un testo JSON; se non lo è restituisce il testo com'è. */
function forseJson(v: unknown): unknown {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!t.startsWith("{") && !t.startsWith("[")) return v;
  try { return JSON.parse(t); } catch { return v; }
}

const pulisciPydantic = (s: string) => s.replace(/^Value error, /, "").replace(/^Assertion failed, /, "");

const n = (v: unknown) => (typeof v === "number" ? v.toLocaleString("it-IT") : typeof v === "string" ? v : "");

/**
 * Messaggio in italiano per un errore di validazione FastAPI/pydantic, dal suo
 * `type` (stabile) e dal contesto `ctx`; il testo inglese di `msg` non arriva mai
 * alla persona. `value_error` e `assertion_error` portano il messaggio scritto dal
 * validatore del servizio, che si mostra ripulito del prefisso.
 */
export function messaggioValidazione(x: Oggetto): string {
  const tipo = typeof x.type === "string" ? x.type : "";
  const ctx: Oggetto = èOggetto(x.ctx) ? x.ctx : {};
  const msg = typeof x.msg === "string" ? x.msg : "";
  switch (tipo) {
    case "missing": return "Campo obbligatorio.";
    case "string_pattern_mismatch": return "Formato non valido.";
    case "string_too_short": return `Troppo corto: almeno ${n(ctx.min_length) || "il minimo di"} caratteri.`;
    case "string_too_long": return `Troppo lungo: al massimo ${n(ctx.max_length) || "il massimo di"} caratteri.`;
    case "too_short": return `Troppo pochi elementi: almeno ${n(ctx.min_length)}.`;
    case "too_long": return `Troppi elementi: al massimo ${n(ctx.max_length)}.`;
    case "greater_than": return `Deve essere maggiore di ${n(ctx.gt)}.`;
    case "greater_than_equal": return `Deve essere almeno ${n(ctx.ge)}.`;
    case "less_than": return `Deve essere minore di ${n(ctx.lt)}.`;
    case "less_than_equal": return `Deve essere al massimo ${n(ctx.le)}.`;
    case "multiple_of": return `Deve essere un multiplo di ${n(ctx.multiple_of)}.`;
    case "date_parsing": case "date_from_datetime_parsing": case "date_from_datetime_inexact": case "date_type":
      return "Data non valida: usa una data del calendario.";
    case "datetime_parsing": case "datetime_from_date_parsing": case "datetime_type":
      return "Data e ora non valide.";
    case "date_past": return "La data deve essere passata.";
    case "date_future": return "La data deve essere futura.";
    case "int_parsing": case "int_type": case "int_from_float": return "Deve essere un numero intero.";
    case "float_parsing": case "float_type": case "decimal_parsing": case "decimal_type": return "Deve essere un numero.";
    case "decimal_max_places": return `Al massimo ${n(ctx.decimal_places)} cifre decimali.`;
    case "bool_parsing": case "bool_type": return "Deve essere sì o no.";
    case "string_type": return "Deve essere un testo.";
    case "enum": case "literal_error":
      return ctx.expected ? `Valore non ammesso: scegli fra ${String(ctx.expected).replace(/'/g, "")}.` : "Valore non ammesso.";
    case "uuid_parsing": case "uuid_type": return "Identificativo non valido (36 caratteri).";
    case "url_parsing": case "url_type": case "url_scheme": return "Indirizzo web non valido.";
    case "json_invalid": case "model_attributes_type": case "dict_type": case "model_type":
      return "Dati inviati non leggibili dal servizio.";
    case "extra_forbidden": return "Campo non previsto dal servizio.";
    case "value_error": case "assertion_error": {
      const t = pulisciPydantic(msg);
      if (/email/i.test(t) && /not a valid|valid email/i.test(t)) return "Indirizzo e-mail non valido.";
      return t || "Valore non valido.";
    }
    default:
      // Tipo sconosciuto: niente testo inglese verso la persona
      return tipo ? "Valore non accettato dal servizio." : pulisciPydantic(msg) || "Valore non valido.";
  }
}

/** Nome del campo in parole: ultimo elemento di `loc`, senza trattini bassi. */
const nomeCampo = (loc: unknown): string | null => {
  if (!Array.isArray(loc)) return null;
  const ultimo = loc[loc.length - 1];
  return typeof ultimo === "string" && ultimo !== "body" ? ultimo.replace(/_/g, " ") : null;
};

function testoElenco(d: unknown[]): string {
  return d.map(x => {
    if (typeof x === "string") return x;
    if (!èOggetto(x) || !("msg" in x || "type" in x)) return "";
    const m = messaggioValidazione(x).replace(/\.$/, "");
    const c = nomeCampo(x.loc);
    return c ? `${c}: ${m.replace(/^./, ch => ch.toLowerCase())}` : m;
  }).filter(Boolean).join("; ");
}

function campiElenco(d: unknown[]): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  for (const x of d) {
    if (!èOggetto(x)) continue;
    // Catalogo errori: {campo: "a.b", tipo, messaggio} con messaggio già in italiano
    if (typeof x.campo === "string" && x.campo) {
      const ultimo = x.campo.split(".").pop() ?? x.campo;
      const testo = primoTesto(x.messaggio)
        ?? messaggioValidazione({ type: x.tipo, ctx: x.ctx, msg: "" });
      if (ultimo !== "body") out[ultimo] = testo;
      continue;
    }
    if (!Array.isArray(x.loc) || !("msg" in x || "type" in x)) continue;
    const ultimo = x.loc[x.loc.length - 1];
    if (typeof ultimo === "string" && ultimo !== "body") out[ultimo] = messaggioValidazione(x);
  }
  return Object.keys(out).length ? out : undefined;
}

/**
 * Scende dentro `detail` finché trova l'oggetto d'errore «vero»: il proxy può
 * annidare il corpo del servizio (`{detail: "{\"detail\": {...}}"}`), ma un
 * oggetto che ha già `title`/`type`/`codice`/`messaggio` è quello buono, anche se
 * contiene un campo `detail` testuale (la spiegazione).
 */
function scendi(v: unknown, profondità = 0): unknown {
  const x = forseJson(v);
  if (profondità > 4 || !èOggetto(x)) return x;
  const haForma = ["title", "type", "codice", "messaggio", "message"].some(k => typeof x[k] === "string");
  if (!haForma && "detail" in x) return scendi(x.detail, profondità + 1);
  return x;
}

function leggiCorpo(stato: number, corpo: unknown, intestazioni?: (nome: string) => unknown): ErroreLetto {
  const radice = forseJson(corpo);
  const c: Oggetto = èOggetto(radice) ? radice : {};
  // Il problem+json ha già la forma al primo livello (`title`, `codice`): il suo
  // `detail` è la spiegazione, non un corpo annidato. `scendi` lo riconosce.
  const d = scendi(radice);

  let testo = "";
  let codice: string | undefined;
  let riferimento: string | undefined;
  let campi: Record<string, string> | undefined;
  let esito: ErroreLetto["esito"];
  let ritentabile: boolean | undefined;

  if (typeof d === "string") testo = d;
  else if (Array.isArray(d)) { testo = testoElenco(d); campi = campiElenco(d); }
  else if (èOggetto(d)) {
    const titolo = primoTesto(d.title, d.messaggio, d.message);
    const spiegazione = primoTesto(typeof d.detail === "string" ? d.detail : undefined);
    testo = titolo && spiegazione && spiegazione !== titolo ? `${titolo}. ${spiegazione}` : (titolo ?? spiegazione ?? "");
    if (!testo && Array.isArray(d.detail)) testo = testoElenco(d.detail);
    // `codice` prima di `type`: nel problem+json `type` è un URI, non il codice
    codice = primoTesto(d.codice, d.type, d.code);
    riferimento = primoTesto(d.id_richiesta, d.id_correlazione, d.request_id, d.riferimento);
    if (Array.isArray(d.campi) && d.campi.length) campi = campiElenco(d.campi);
    else if (Array.isArray(d.errori)) campi = campiElenco(d.errori);
    if (d.esito === "NON_VALIDO" || d.esito === "RIFIUTATO" || d.esito === "ERRORE") esito = d.esito;
    if (typeof d.ritentabile === "boolean") ritentabile = d.ritentabile;
  }
  codice ??= primoTesto(c.codice, c.type);
  riferimento ??= primoTesto(c.id_richiesta, c.id_correlazione, c.request_id, c.riferimento,
    intestazioni?.("x-request-id"), intestazioni?.("x-correlation-id"));
  if (!testo) testo = primoTesto(c.messaggio, c.title, c.message) ?? "";

  // Una pagina HTML (proxy, nginx) non è un messaggio da mostrare.
  if (/<\s*(!doctype|html|body)/i.test(testo)) testo = "";
  // Un `type` URI del catalogo porta il codice in fondo; un `type` di sola forma
  // (es. "about:blank") non è un codice utile.
  const daUri = codice?.match(/\/errori\/([A-Z][A-Z0-9-]*)$/);
  if (daUri) codice = daUri[1];
  if (codice && !/^[A-Z][A-Z0-9_-]*$/i.test(codice)) codice = undefined;
  return { stato, codice, testo: testo.slice(0, 400), riferimento, campi, esito, ritentabile };
}

/** Estrae stato, codice, messaggio e riferimento da un errore di axios (o qualsiasi). */
export function leggiErrore(e: unknown): ErroreLetto {
  const r = (e as { response?: { status?: number; data?: unknown; headers?: unknown } } | null)?.response;
  if (!r || typeof r.status !== "number") return { testo: "" };
  const h = r.headers;
  const intestazione = (nome: string): unknown => {
    if (!h) return undefined;
    const get = (h as { get?: (n: string) => unknown }).get;
    if (typeof get === "function") { try { const v = get.call(h, nome); if (v) return v; } catch { /* oggetto semplice */ } }
    return (h as Oggetto)[nome] ?? (h as Oggetto)[nome.toUpperCase()];
  };
  return leggiCorpo(r.status, r.data, intestazione);
}

/** Come `leggiErrore`, per una risposta di `fetch` (scaricamenti, accesso SPID). */
export async function leggiErroreFetch(resp: Response): Promise<ErroreLetto> {
  let corpo: unknown = null;
  try { corpo = await resp.text(); } catch { /* corpo illeggibile */ }
  return leggiCorpo(resp.status, corpo, n => resp.headers.get(n));
}

/** Coda «(codice X; riferimento per la segnalazione Y)» se ci sono. */
export function codaRiferimenti(l: Pick<ErroreLetto, "codice" | "riferimento">): string {
  const parti = [l.codice ? `codice ${l.codice}` : "", l.riferimento ? `riferimento per la segnalazione ${l.riferimento}` : ""].filter(Boolean);
  return parti.length ? ` (${parti.join("; ")})` : "";
}

/** Motivo leggibile da un errore già letto (vedi `motivoErrore`). */
export function motivoDaLetto(l: ErroreLetto, azione: "consultazione" | "operazione" = "consultazione", conCoda = true): string {
  const { stato, testo } = l;
  if (stato === undefined) return "il servizio non risponde";
  if (stato === 403) {
    // Un 403 con un codice proprio (es. DL-403-CONTENUTO) spiega già il perché.
    return l.codice && testo
      ? `${testo.replace(/^./, c => c.toLowerCase())}${conCoda ? codaRiferimenti(l) : ""}`
      : `il tuo profilo non è abilitato a questa ${azione}`;
  }
  const generico = stato === 503 ? "il servizio non è al momento disponibile"
    : stato === 502 ? "un servizio interno non ha risposto correttamente"
    : `il servizio ha risposto con un errore (${stato})`;
  // 502/503 con un messaggio del servizio: basta il messaggio (niente doppioni «non disponibile: non disponibile»)
  const corpo = (stato === 502 || stato === 503) && testo
    ? testo.replace(/^./, c => c.toLowerCase()).replace(/\.$/, "")
    : `${generico}${testo ? `: ${testo.replace(/\.$/, "")}` : ""}`;
  return `${corpo}${conCoda ? codaRiferimenti(l) : ""}`;
}

/**
 * Motivo leggibile, da inserire dopo i due punti di una frase della pagina
 * («Utenti non disponibili: <motivo>.»). Inizia in minuscolo.
 * `azione` sceglie la frase del 403: lettura («consultazione») o scrittura («operazione»).
 */
export function motivoErrore(e: unknown, azione: "consultazione" | "operazione" = "consultazione"): string {
  return motivoDaLetto(leggiErrore(e), azione);
}
