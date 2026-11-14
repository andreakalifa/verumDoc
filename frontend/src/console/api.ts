// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * API delle console R05 (configurazione, ciclo di vita, registro, canali, fascicoli).
 *
 * Usa il client comune (`api/client.ts`, sessione in cookie HttpOnly e intestazione
 * anti-CSRF) senza modificarlo: qui ci sono solo le chiamate e i tipi delle risposte,
 * allineati al codice dei servizi (contratti Q1 e Q2 di R05):
 *   - configurazione: services/onboarding/app/routers/configurazione.py
 *   - ciclo di vita:  services/ciclo-vita/app/api/pratiche.py, modelli.py
 *   - registro:       services/hashchain (via dashboard-api /dashboard/hashchain) e
 *                     services/hashchain/app/verifica_continua/router.py
 *   - canali:         services/onboarding/app/routers/sftp.py,
 *                     services/notification/app/api/webhook.py
 *   - fascicoli:      services/distribution/app/routers/fascicoli.py
 * Ogni elenco del servizio è a cursore: `{items, limit, cursore, altri}` (niente totale).
 */
import { api } from "../api/client";
import type { ConfermaFirmata } from "./conferme";

export interface Pagina<T> { items: T[]; limit: number; cursore: string | null; altri: boolean }

const query = (o: Record<string, string | number | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") p.append(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

// ── Configurazione (Q1, onboarding) ────────────────────────────────

export type Livello = "CONSERVATORE" | "ENTE" | "CLASSE" | "ACCORDO" | "PDV";
export type Origine = Livello | "PREDEFINITO";
export type Risorsa = "impostazioni" | "regole" | "profilo" | "classe" | "accordo" | "quote";

export interface Azioni { modifica: boolean; motivo_blocco: string | null }

export interface Voce {
  codice: string;
  valore: unknown;
  origine: Origine;
  tipo: "int" | "bool" | "choice" | "codes" | "date" | "str" | string;
  locked: boolean;
  norm_ref: string | null;
  descrizione: string;
  ambito_ammesso: Livello[];
  riservata_conservatore?: boolean;
  /** R07 (W1): verso più sicuro (l'ente può andare solo in questo verso rispetto al tetto). */
  verso_sicuro?: "minore" | "maggiore" | "ordine_scelte" | "sottoinsieme" | null;
  /** R07 (W1): per una voce `LIMITE.<codice>`, il codice limitato dal conservatore per l'ente. */
  limite_di?: string | null;
  applicata_da?: string | null;
  gruppo?: string | null;
  scelte?: string[] | null;
  minimo?: number | null;
  massimo?: number | null;
  predefinito?: unknown;
  valore_al_livello?: unknown;
  ereditato?: { valore: unknown; origine: Origine } | null;
  azioni_consentite: Azioni;
}

export interface Contesto { livello: Livello; ente: string | null; classe: string | null; accordo: string | null; accordo_versione?: number | null }
export interface ElencoVoci extends Pagina<Voce> { contesto: Contesto }

export interface Regola {
  codice: string;
  etichetta: string;
  fase: string;
  effetto: "BLOCCA" | "AVVISA" | "IGNORA" | string;
  origine: Origine;
  effetto_predefinito: string;
  locked: boolean;
  norm_ref: string | null;
  impostazione: string;
  ambito_ammesso: Livello[];
  azioni_consentite: Azioni;
}
export interface ElencoRegole extends Pagina<Regola> { contesto: Contesto; catalogo_regole: string }

export interface FiltroContesto { livello?: string; ente?: string; classe?: string; accordo?: string; gruppo?: string }

export const leggiImpostazioni = (f: FiltroContesto, cursore: string | null, limit = 100): Promise<ElencoVoci> =>
  api.get(`/v1/configurazione/impostazioni${query({ ...f, cursore, limit })}`).then(r => r.data);
export const leggiRegole = (f: FiltroContesto, cursore: string | null, limit = 100): Promise<ElencoRegole> =>
  api.get(`/v1/configurazione/regole${query({ livello: f.livello, ente: f.ente, classe: f.classe, accordo: f.accordo, cursore, limit })}`).then(r => r.data);

export interface ProfiloBreve { versione: number; valido_dal: string; valido_al: string | null; campi: number; obbligatori: number; da_riscontrare: number; adozione?: "automatica" | "esplicita" }
export interface Profilo {
  id: string; tipo: "tipologia" | "classe"; codice: string; nome: string;
  estende?: string | null; nota?: string | null; norm_ref?: string | null; ente?: string | null;
  /** R11: «allegato5» o «conservatore» (profilo proprio, non dell'Allegato 5) */
  origine?: string | null;
  versioni: number; in_vigore: ProfiloBreve | null;
  /** R11: versione più alta valida se diversa da quella in vigore (adozione esplicita) */
  ultima?: ProfiloBreve | null;
}
export interface CondizioneCampo { campo: string; in?: string[]; non_in?: string[] }
export interface CampoProfilo {
  nome: string; tipo?: string; obbligatorio?: boolean; descrizione?: string | null;
  allegato5?: string | null; calcolato?: boolean; da_riscontrare?: boolean; valori_ammessi?: string[] | null;
  /** R11 */
  obbligatorio_se?: CondizioneCampo[]; sottocampi?: CampoProfilo[]; origine?: string | null; rif?: string | null;
  [k: string]: unknown;
}
export interface VersioneProfilo {
  versione: number; valido_dal: string; valido_al: string | null; origine: string;
  /** R11: assente = automatica */
  adozione?: "automatica" | "esplicita";
  campi: CampoProfilo[]; norm_ref?: string | null; tipologia?: string | null; [k: string]: unknown;
}
export const leggiProfili = (ente: string | undefined, cursore: string | null, limit = 100): Promise<Pagina<Profilo>> =>
  api.get(`/v1/configurazione/profili${query({ ente, cursore, limit })}`).then(r => r.data);
export const leggiVersioniProfilo = (id: string): Promise<{ id: string; items: VersioneProfilo[] }> =>
  api.get(`/v1/configurazione/profili/${encodeURIComponent(id)}/versioni`).then(r => r.data);

export interface Ambito { livello: Livello; ente?: string | null; classe?: string | null; accordo?: string | null }
export type Modifica = Record<string, unknown>;
export interface Bozza {
  bozza_id: string; risorsa: Risorsa; ambito: Ambito; modifiche: Modifica[] | null;
  proposta: { enti: string[] | null } | null; motivo: string; versione: number;
  stato: "APERTA" | "APPLICATA" | "ANNULLATA" | string;
  creato_da: string | null; aggiornato_da: string | null; created_at: string; updated_at: string;
  applicata_il: string | null; applicata_da: string | null; impronta_piano: string | null;
  esito: Record<string, unknown> | null;
  /** R07 (D28): modifica programmata con decorrenza futura */
  valido_dal?: string | null; programmata_il?: string | null; programmata_da?: string | null;
  anteprima_programmata?: (Piano & { impatto?: Impatto | null; calcolata_il?: string }) | null;
  anteprima_il?: string | null; avviso_autore_il?: string | null;
}
export interface RigaPiano { codice: string; prima: unknown; dopo: unknown; origine_prima: string; origine_dopo: string }
export interface Esempio { pdv_id: string; ricevuto_il?: string; esito: string; motivo?: string; rilievi?: Rilievo[]; avvisi?: Rilievo[] }
export interface Campione { metodo?: string; finestra_giorni?: number; limite?: number; letti?: number; raggiunto_limite?: boolean; analizzati?: number; tempo_max_s?: number; troncato_per_tempo?: boolean; nota?: string | null }
export interface Impatto {
  pdv_ultimi_30_giorni_che_sarebbero_rifiutati: number;
  pdv_ultimi_30_giorni_con_nuovi_avvisi: number;
  esempi: Esempio[];
  rifiutati_con_la_configurazione_attuale?: number;
  non_valutabili?: number;
  campione: Campione | null;
  nota?: string;
}
export interface Piano {
  impronta_piano: string;
  modifiche: RigaPiano[];
  bloccati: { codice: string; motivo: string }[];
  avvisi: string[];
  dettagli?: Record<string, unknown>;
  impatto?: Impatto;
  bozza_id?: string;
  versione?: number;
}

export const creaBozza = (corpo: { risorsa: Risorsa; ambito: Ambito; modifiche: Modifica[]; motivo: string; proposta?: { enti: string[] | null } }):
  Promise<{ bozza_id: string; versione: number; stato: string }> =>
  api.post("/v1/configurazione/bozze", corpo).then(r => r.data);
export const leggiBozze = (f: { stato?: string; ente?: string }, cursore: string | null, limit = 30): Promise<Pagina<Bozza>> =>
  api.get(`/v1/configurazione/bozze${query({ ...f, cursore, limit })}`).then(r => r.data);
export const leggiBozza = (id: string): Promise<Bozza> =>
  api.get(`/v1/configurazione/bozze/${encodeURIComponent(id)}`).then(r => r.data);
export const aggiornaBozza = (id: string, corpo: { versione: number; modifiche?: Modifica[]; motivo?: string }) =>
  api.patch(`/v1/configurazione/bozze/${encodeURIComponent(id)}`, corpo).then(r => r.data);
export const annullaBozza = (id: string, versione: number) =>
  api.delete(`/v1/configurazione/bozze/${encodeURIComponent(id)}${query({ versione })}`).then(r => r.data);
export const anteprimaBozza = (id: string): Promise<Piano> =>
  api.post(`/v1/configurazione/bozze/${encodeURIComponent(id)}/anteprima`, {}).then(r => r.data);
export const applicaBozza = (id: string, impronta_piano: string, valido_dal?: string) =>
  api.post(`/v1/configurazione/bozze/${encodeURIComponent(id)}/applica`, valido_dal ? { impronta_piano, valido_dal } : { impronta_piano })
    .then(r => r.data as { bozza_id: string; stato: string; impronta_piano: string; esito: Record<string, unknown> | null; valido_dal?: string });

export interface Proposta {
  proposta_id: string; bozza_id: string; ente: string; codice: string; valore: unknown; motivo: string;
  descrizione: string | null; stato: "PROPOSTA" | "ACCETTATA" | "RIFIUTATA" | string;
  motivo_risposta: string | null; risposto_da: string | null; risposto_il: string | null; created_at: string;
  azioni_consentite: { accetta: boolean; rifiuta: boolean; motivo_blocco: string | null };
}
export const leggiProposte = (f: { stato?: string; ente?: string }, cursore: string | null, limit = 30): Promise<Pagina<Proposta>> =>
  api.get(`/v1/configurazione/proposte${query({ ...f, cursore, limit })}`).then(r => r.data);
export const rispondiProposta = (id: string, accetta: boolean, motivo: string) =>
  api.post(`/v1/configurazione/proposte/${encodeURIComponent(id)}/${accetta ? "accetta" : "rifiuta"}`, { motivo }).then(r => r.data);

export interface Rilievo { codice?: string; regola?: string; effetto: string; campo: string | null; documento: string | null; messaggio: string }
export interface EsitoValidatore {
  esito: "CONFORME" | "NON_CONFORME";
  rilievi: Rilievo[]; avvisi: Rilievo[];
  regole_applicate: Record<string, unknown> | null;
  fasi_eseguite: string[];
  controlli_non_eseguiti?: string[];
  // R08 (D02, D01): esito per file e livello delle firme
  file?: { documento: string; nome_file: string; formato: string | null; rifiutato: boolean; motivi: string[]; avvisi: string[];
           catalogo: { formato: string; nome: string; esito: string; motivo: string } | null }[];
  file_rifiutati?: string[];
  firme?: { livello: string; descrizione: string; origine: string } | null;
}
export const validaIndice = (indice: unknown, ente?: string): Promise<EsitoValidatore> =>
  api.post("/v1/configurazione/valida", ente ? { indice, ente } : { indice }).then(r => r.data);

// ── Catalogo dei formati (R08, D02) ─────────────────────────────────

export interface FormatoCatalogo {
  id: string; nome: string; famiglia: string; puid: string[]; mime: string[];
  esito_base: string; pdfa: boolean; codice: string;
}
export interface CatalogoFormati { versione: number; valido_dal: string; norm_ref: string; esiti: string[]; formati: FormatoCatalogo[] }
export const leggiCatalogoFormati = (): Promise<CatalogoFormati> =>
  api.get("/v1/configurazione/formati").then(r => r.data);

// ── Ciclo di vita (Q2, ciclo-vita) ─────────────────────────────────

export type TipoPratica = "RETTIFICA" | "ANNULLAMENTO" | "SCARTO" | "RIVERSAMENTO_USCITA" | "RIVERSAMENTO_INGRESSO" | "RESTITUZIONE";
export interface Pratica {
  id: string; tipo: TipoPratica; stato: string; versione: number; ente: string; motivo: string;
  pdv_id: string | null; pdv_risultato_id: string | null; richiede_nulla_osta: boolean;
  impronta_piano: string | null; creata_da: string | null; creata_ruolo: string | null;
  creata_il: string; aggiornata_il: string; conclusa_il: string | null;
  conteggi: Record<string, number>;
  esecuzione: {
    lavoro: string | null; tentativi: number | null; prossimo_tentativo: string | null; ultimo_errore: Record<string, unknown> | null; in_corso_fino_al: string | null;
    /** R14 (VD-C-133): lavoro sospeso dopo i tentativi esauriti; ripresa consentita a chi legge (conservatore). */
    lavoro_sospeso?: string | null; ripresa_consentita?: boolean;
  };
}
export interface PassoCronologia { da_stato: string | null; a_stato: string; utente_id: string | null; ruolo: string | null; motivo: string | null; allegati: string[] | null; il: string }
export interface Allegato { id: string; tipo: string; nome_file: string; content_type: string; dimensione: number; sha256: string; conserva_fino_a: string | null; firma: Record<string, unknown>; caricato_da: string | null; caricato_il: string }
export interface AzioneConsentita { nuovo_stato: string; allegati_richiesti: string[]; esecuzione: boolean; consentita: boolean; motivo_blocco: string | null; /** R09 (D20): motivo minimo (chiusura con eccezioni) */ motivo_minimo?: number | null }
/** R09 (D23): annullamento in attesa della seconda persona. */
export interface Approvazione { in_attesa: boolean; ruoli_ammessi: string[]; consentita: boolean; motivo_blocco: string | null; /** Revisione R09 (W1): annullamento fermo dopo il ricontrollo del sistema, si può solo respingere */ solo_respingere?: boolean }
export interface DettaglioPratica extends Pratica {
  dati: Record<string, unknown>;
  cronologia: PassoCronologia[];
  autorizzazioni: { stato: string; utente_id: string | null; ruolo: string | null; allegati: string[] | null; il: string }[];
  allegati: Allegato[];
  oggetti: { conteggi: Record<string, number>; elenco: string };
  azioni_consentite: AzioneConsentita[];
  /** R09: esito o prossimo passo in parole semplici */
  in_parole?: string | null;
  approvazione?: Approvazione | null;
}
export interface OggettoPratica { aip_id: string | null; pdv_id: string | null; urn: string | null; esito: string; motivo: string | null; dettagli?: unknown; foglia_id?: string | null; tentativi?: number; aggiornato_il?: string }

/** R06 (VD-C-128): `in_attesa_di: "me"` = solo le pratiche ferme in uno stato in cui decide chi chiede. */
export const leggiPratiche = (f: { tipo?: string; stato?: string; ente?: string; in_attesa_di?: "me" }, cursore: string | null, limite = 30): Promise<Pagina<Pratica>> =>
  api.get(`/v1/ciclo-vita/pratiche${query({ ...f, cursore, limite })}`).then(r => r.data);
export const leggiPratica = (id: string): Promise<DettaglioPratica> =>
  api.get(`/v1/ciclo-vita/pratiche/${encodeURIComponent(id)}`).then(r => r.data);
export const leggiOggettiPratica = (id: string, esito: string | undefined, cursore: string | null, limite = 30): Promise<Pagina<OggettoPratica>> =>
  api.get(`/v1/ciclo-vita/pratiche/${encodeURIComponent(id)}/oggetti${query({ esito, cursore, limite })}`).then(r => r.data);
export const transizionePratica = (id: string, corpo: { stato_atteso: string; nuovo_stato: string; allegati: string[]; motivo: string; conferma?: ConfermaFirmata }) =>
  api.post(`/v1/ciclo-vita/pratiche/${encodeURIComponent(id)}/transizione`, corpo).then(r => r.data as Pratica & { esecuzione_richiesta?: boolean });
/** R09 (D23): la seconda persona approva o respinge un annullamento in attesa. */
export const decidiAnnullamento = (id: string, corpo: { esito: "APPROVA" | "RESPINGI"; motivo: string; conferma?: ConfermaFirmata }) =>
  api.post(`/v1/ciclo-vita/pratiche/${encodeURIComponent(id)}/approvazione`, corpo).then(r => r.data as Pratica);
/** Revisione R14 (4): ripresa di una pratica sospesa (solo il conservatore, versione attesa). */
export const riprendiPratica = (id: string, corpo: { versione: number; motivo: string; conferma?: ConfermaFirmata }) =>
  api.post(`/v1/ciclo-vita/pratiche/${encodeURIComponent(id)}/riprendi`, corpo).then(r => r.data as Pratica);
/** R09 (D04, D06, D20, D21): lavoro automatico del ciclo di vita e blocchi WORM (conservatore). */
export interface PassoAutomatico { nome: string; descrizione: string; ente: string | null; ultimo_giro: string | null; ultimo_esito: Record<string, unknown>; in_corso: boolean }
export interface StatoAutomatico {
  passi: PassoAutomatico[]; in_parole: string[];
  retention: { anni: number; soglia_anni: number; prossima_scadenza: string | null; da_rinnovare: number; rinnovi_30_giorni: number; tetto_conteggi: number };
}
export const leggiStatoAutomatico = (): Promise<StatoAutomatico> =>
  api.get("/v1/ciclo-vita/stato-automatico").then(r => r.data);
export const caricaAllegato = (id: string, tipo: string, file: File) => {
  const dati = new FormData();
  dati.append("file", file);
  dati.append("tipo", tipo);
  // Content-Type lasciato al browser (confine multipart)
  return api.post(`/v1/ciclo-vita/pratiche/${encodeURIComponent(id)}/allegati`, dati, { headers: { "Content-Type": undefined } })
    .then(r => r.data as { id: string; tipo: string; sha256: string; dimensione: number; nome_file: string; firma: Record<string, unknown> });
};

export interface Criteri { classi: string[]; anni: number[]; scadenza_entro: string | null }
export interface PianoScarto {
  impronta_piano: string; totale: number; selezionabili: number; bloccati: Record<string, number>;
  esempi: { selezionabili: { aip_id: string; urn: string; scadenza: string | null }[]; bloccati: { aip_id: string; urn: string; motivo: string; descrizione: string }[]; limite_esempi: number };
  ente: string; criteri: Criteri; motivo: string; richiede_nulla_osta: boolean; calcolata_il: string;
}
/** Anteprima (senza impronta) o applica (con l'impronta del piano): stessa richiesta. */
export const propostaScarto = (corpo: { ente: string; criteri: Criteri; motivo: string; impronta_piano?: string }, chiave?: string) =>
  api.post(`/v1/ciclo-vita/pratiche/scarto/proposta`, corpo, chiave ? { headers: { "Idempotency-Key": chiave } } : undefined).then(r => r.data as PianoScarto | Pratica);
export const riversamentoUscita = (corpo: { ente: string; criteri: Criteri; conservatore_destinatario: string; motivo: string; impronta_piano?: string }, chiave?: string) =>
  api.post(`/v1/ciclo-vita/pratiche/riversamento-uscita`, corpo, chiave ? { headers: { "Idempotency-Key": chiave } } : undefined).then(r => r.data as (PianoScarto & Record<string, unknown>) | Pratica);
export const restituzione = (corpo: { ente: string; conservatore_destinatario?: string | null; motivo: string; impronta_piano?: string }, chiave?: string) =>
  api.post(`/v1/ciclo-vita/pratiche/restituzione`, corpo, chiave ? { headers: { "Idempotency-Key": chiave } } : undefined).then(r => r.data as (PianoScarto & Record<string, unknown>) | Pratica);
export const rettifica = (corpo: { aip_id?: string; pdv_id?: string; metadati_nuovi: Record<string, unknown>; motivo: string; conferma?: ConfermaFirmata }, chiave: string) =>
  api.post(`/v1/ciclo-vita/pratiche/rettifica`, corpo, { headers: { "Idempotency-Key": chiave } }).then(r => r.data as Pratica);
export const annullamento = (corpo: { pdv_id: string; motivo: string; conferma?: ConfermaFirmata }, chiave: string) =>
  api.post(`/v1/ciclo-vita/pratiche/annullamento`, corpo, { headers: { "Idempotency-Key": chiave } }).then(r => r.data as Pratica);
export const riversamentoIngresso = (corpo: { ente: string; caricamento_id: string; classe_documentale: string; conservatore_origine: string; motivo: string }, chiave: string) =>
  api.post(`/v1/ciclo-vita/pratiche/riversamento-ingresso`, corpo, { headers: { "Idempotency-Key": chiave } }).then(r => r.data as Pratica);

/** È un piano (anteprima) e non una pratica creata? */
export const èPiano = (r: unknown): r is PianoScarto => !!r && typeof r === "object" && "impronta_piano" in r && !("tipo" in r && "versione" in r);

// ── Registro delle impronte e verifiche continue (hashchain) ──────

export interface StatoRegistro {
  dimensione: number;
  ultimo_checkpoint: null | {
    dimensione: number; radice: string; emesso_il: string | null; stato_sigillo: string | null; motivo_sigillo: string | null;
    stato_testimone: string | null; controfirmato_il: string | null; esito_testimone: string | null;
  };
  testimone_dimensione: number | null;
}
export const leggiStatoCatena = () =>
  api.get(`/dashboard/hashchain?limit=1`).then(r => r.data as { registro?: StatoRegistro; integrita?: Record<string, unknown> });

export interface RiepilogoVerifiche {
  ente: string | null; ciclo_giorni: number; finestra_giorni: number; pda_programmati: number;
  pda_verificati_nella_finestra: number; pda_mai_verificati: number; copertura_percentuale: number | null;
  copertura_calcolata_il: string | null;
  anomalie_aperte: { numero: number; oltre_tetto: boolean; elenco: { pda_id: string; pda: string | null; ente: string; esito: string | null; aperta_dal: string | null }[] };
  ritardo_rotazione_secondi: number | null; ultima_verifica_il: string | null;
  lavoratore: { ultimo_giro_il: string | null; detentore: string | null; confronto_foglia: string };
}
export const leggiRiepilogoVerifiche = (ente?: string) =>
  api.get(`/v1/hashchain/verifiche/riepilogo${query({ ente })}`).then(r => r.data as RiepilogoVerifiche);
export interface VoceVerifica {
  verificato_il: string; ente: string; pda_id: string; pda: string | null; documento_id: string | null; componente: string;
  esito: string; motivo: string | null; tentativo: number; impronta_attesa: string | null; impronta_trovata: string | null;
}
export const leggiRegistroVerifiche = (f: { ente?: string; esito?: string }, cursore: string | null, limite = 30) =>
  api.get(`/v1/hashchain/verifiche/registro${query({ ...f, cursore, limite })}`)
    .then(r => r.data as { voci: VoceVerifica[]; cursore_successivo: string | null });

/** R06 (VD-C-139): accordi di versamento dell'ente (ultima versione), a cursore. */
export interface AccordoVersamento { codice: string; versione: number; ente: string; classe: string | null; descrizione: string | null; valido_dal: string; cessato: boolean }
export const leggiAccordi = (f: { ente?: string; stato?: "ATTIVO" | "CESSATO" }, cursore: string | null, limit = 30): Promise<Pagina<AccordoVersamento>> =>
  api.get(`/v1/configurazione/accordi${query({ ...f, cursore, limit })}`).then(r => r.data);

/** R06 (VD-C-129): allarmi del registro e del testimone (solo conservatore), a cursore. */
export interface AllarmeRegistro {
  id: string; fonte: "testimone" | "registro"; causa: string; aperto_il: string; ultimo_il: string;
  occorrenze: number; dettagli: Record<string, unknown>; aperto: boolean;
  gestito_il: string | null; gestito_da: string | null; nota: string | null;
}
export interface CondizioneRegistro { codice: string; attiva: boolean; descrizione: string; dettagli: Record<string, unknown> }
export const leggiAllarmiRegistro = (aperti: boolean, cursore: string | null, limite = 20) =>
  api.get(`/v1/hashchain/registro/allarmi${query({ aperti: String(aperti), cursore, limite })}`)
    .then(r => r.data as Pagina<AllarmeRegistro> & { condizioni: CondizioneRegistro[]; soglie: Record<string, number> });

// ── Canali: account SFTP (onboarding) e webhook (notification) ────

export interface ChiaveSftp { id: string; algoritmo: string; bit: number | null; impronta: string; commento: string | null; attiva: boolean; created_at: string | null; revocata_il: string | null }
export interface AccountSftp {
  id: string; nome_utente: string; descrizione: string; stato: "ATTIVO" | "SOSPESO" | string; sospeso_il: string | null;
  responsabile: null | { id: string; email: string; nome: string | null; cognome: string | null; ruolo: string; attivo: boolean };
  chiavi: ChiaveSftp[]; connessione: { protocollo: string; utente: string; host: string | null; porta: number };
  created_at: string | null;
  azioni_consentite: { sospendi: boolean; riattiva: boolean; aggiungi_chiave: boolean; modifica: boolean; ip_ammessi?: boolean };
  /** R10 (D12): indirizzi o reti da cui l'account si collega (vuoto = ogni indirizzo) */
  ip_ammessi?: string[];
}
const sftp = (tid: string) => `/v1/onboarding/tenant/${encodeURIComponent(tid)}/sftp/account`;
export const leggiAccountSftp = (tid: string) => api.get(sftp(tid)).then(r => r.data as { items: AccountSftp[] });
export const creaAccountSftp = (tid: string, corpo: { nome_utente: string; descrizione: string }) =>
  api.post(sftp(tid), corpo).then(r => r.data as AccountSftp);
export const statoAccountSftp = (tid: string, id: string, azione: "sospendi" | "riattiva") =>
  api.post(`${sftp(tid)}/${encodeURIComponent(id)}/${azione}`, {}).then(r => r.data as AccountSftp);
export const aggiungiChiaveSftp = (tid: string, id: string, chiave_pubblica: string) =>
  api.post(`${sftp(tid)}/${encodeURIComponent(id)}/chiavi`, { chiave_pubblica }).then(r => r.data);
export const revocaChiaveSftp = (tid: string, id: string, chiave: string) =>
  api.delete(`${sftp(tid)}/${encodeURIComponent(id)}/chiavi/${encodeURIComponent(chiave)}`).then(r => r.data);

export interface Webhook { id: string; url: string; tipi: string[]; descrizione: string | null; attiva: boolean; segreto_suffisso: string; creata_il: string; aggiornata_il: string;
  /** R13b: rotazione del segreto (precedente ancora valido fino a transizione_fino_al) */
  segreto_precedente_suffisso?: string | null; transizione_fino_al?: string | null; segreto_ruotato_il?: string | null }
export interface Consegna {
  id: number; evento_id: number; tipo_evento: string; stato: string; tentativi: number; riprove: number;
  prossimo_tentativo: string | null; ultimo_stato_http: number | null; ultimo_codice: string | null; ultimo_errore: string | null;
  ultimo_tentativo_il: string | null; consegnata_il: string | null; creata_il: string | null;
}
export const leggiWebhook = () => api.get(`/v1/webhook`).then(r => r.data as { sottoscrizioni: Webhook[] });
/** Catalogo dei tipi di evento sottoscrivibili (notification `GET /api/v1/webhook/tipi`, R05). */
export interface TipoEvento { tipo: string; risorsa: string; descrizione: string }
export const leggiTipiEvento = () => api.get(`/v1/webhook/tipi`).then(r => r.data as { tipi: TipoEvento[] });
export const creaWebhook = (corpo: { url: string; tipi: string[]; descrizione?: string }, chiave: string) =>
  api.post(`/v1/webhook`, corpo, { headers: { "Idempotency-Key": chiave } }).then(r => r.data as Webhook & { segreto: string });
/** R13b: nuovo segreto (mostrato una volta), il precedente vale ancora per `ore`. */
export const ruotaSegretoWebhook = (id: string, ore: number) =>
  api.post(`/v1/webhook/${encodeURIComponent(id)}/segreto/ruota`, { transizione_ore: ore }).then(r => r.data as Webhook & { segreto: string });
export const chiudiTransizioneWebhook = (id: string) =>
  api.post(`/v1/webhook/${encodeURIComponent(id)}/segreto/chiudi-transizione`, {}).then(r => r.data as Webhook);
export const modificaWebhook = (id: string, corpo: Partial<{ url: string; tipi: string[]; descrizione: string; attiva: boolean }>) =>
  api.patch(`/v1/webhook/${encodeURIComponent(id)}`, corpo).then(r => r.data as Webhook);
export const leggiConsegne = (id: string, stato: string | undefined, dopo: number, limite = 30) =>
  api.get(`/v1/webhook/${encodeURIComponent(id)}/consegne${query({ stato, dopo: dopo || undefined, limite })}`)
    .then(r => r.data as { consegne: Consegna[]; cursore: number; altri: boolean });
export const riprovaConsegna = (id: string, consegna: number) =>
  api.post(`/v1/webhook/${encodeURIComponent(id)}/consegne/${consegna}/riprova`, {}).then(r => r.data);

// ── Fascicoli (distribution, VD-C-087) ────────────────────────────

export interface Fascicolo {
  id: string; codice: string; titolo: string; descrizione: string | null; tipo: string; anno: number | null;
  stato: "APERTO" | "CHIUSO" | string; data_apertura: string; data_chiusura: string | null; num_aip: number; created_at: string;
}
export const leggiFascicoli = (f: { stato?: string; anno?: string; tipo?: string }, cursore: string | null, limite = 30): Promise<Pagina<Fascicolo>> =>
  api.get(`/v2/fascicoli${query({ ...f, cursore, limite })}`).then(r => r.data);
export const creaFascicolo = (corpo: { titolo: string; descrizione?: string; tipo: string; anno?: number; data_apertura?: string }) =>
  api.post(`/v2/fascicoli`, corpo).then(r => r.data as Fascicolo);
export const chiudiFascicolo = (id: string, corpo: { data_chiusura?: string; motivo: string; stato_atteso: string }) =>
  api.post(`/v2/fascicoli/${encodeURIComponent(id)}/chiudi`, corpo).then(r => r.data as { id: string; stato: string; data_chiusura: string });

// ── Classi dell'ente (onboarding, già esistente) ───────────────────
export interface Classe { id: string; codice: string; nome?: string; descrizione?: string; tenant_id?: string | null; attiva?: boolean; anni_conservazione?: number }
export const leggiClassiEnte = (tid: string) =>
  api.get(`/v1/onboarding/tenant/${encodeURIComponent(tid)}/classi`).then(r => r.data as { items?: Classe[]; classi?: Classe[] } | Classe[]);
export const elencoClassi = (d: unknown): Classe[] => {
  if (Array.isArray(d)) return d as Classe[];
  const o = (d ?? {}) as { items?: Classe[]; classi?: Classe[] };
  return o.items ?? o.classi ?? [];
};
