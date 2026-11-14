// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Scadenza della sessione del portale per inattività (R06, VD-C-127).
 *
 * Il gateway fissa all'accesso un limite d'inattività per ruolo (R07, D14:
 * impostazione della console; predefiniti 15 minuti per SUPER_ADMIN,
 * RSC_CONSERVATORE e ADMIN_TENANT, 30 per gli altri; l'ente può solo ridurlo)
 * e ogni servizio rifiuta con 401 `AUTH-004`
 * una sessione rimasta senza richieste oltre il limite. Ogni richiesta rinnova
 * la sessione (a scorrimento), tranne quelle con `X-Sessione-Rinnovo: no`.
 *
 * Qui la pagina:
 *  - registra le azioni dell'utente (clic, tasti, scorrimento), condivise fra le
 *    schede con il `localStorage` (solo un istante, nessun dato personale);
 *  - marca `X-Sessione-Rinnovo: no` le richieste partite senza azioni recenti
 *    (aggiornamenti automatici delle pagine): da sole non tengono viva la sessione;
 *  - quando l'utente ha agito dopo l'ultimo rinnovo e non ci sono richieste da
 *    un minuto, rinnova la sessione con un GET di /api/v1/auth/sessione (battito);
 *  - conta il limite dall'ultimo rinnovo (lo stesso riferimento del server, in
 *    qualunque scheda): due minuti prima avvisa con «Resta collegato» (R07,
 *    D14: anticipo comunicato dal gateway), al limite torna all'accesso.
 */

const CHIAVE = "vd_attivita_utente";
const CHIAVE_RINNOVO = "vd_rinnovo_sessione";
/** Azioni dell'utente più vecchie di così: la richiesta non rinnova la sessione. */
export const FINESTRA_AZIONE_MS = 60_000;
/** Azione dopo l'ultimo rinnovo e nessuna richiesta da così: battito. */
export const BATTITO_MS = 60_000;
/** Anticipo dell'avviso sulla scadenza (R07, D14: 2 minuti; il gateway lo comunica). */
export const AVVISO_MS = 120_000;
/** Ruoli con il limite predefinito più breve (R07, D14). */
export const RUOLI_PRIVILEGIATI = ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"];
export const INTESTAZIONE_RINNOVO = "X-Sessione-Rinnovo";

let ultimaAzione = Date.now();
let ultimoRinnovo = Date.now();

function leggi(chiave: string): number {
  try {
    return Number(window.localStorage.getItem(chiave) ?? 0) || 0;
  } catch {
    return 0;
  }
}

function scrivi(chiave: string, valore: number): void {
  try {
    window.localStorage.setItem(chiave, String(valore));
  } catch {
    // archiviazione non disponibile: vale la sola scheda corrente
  }
}

/** Limite predefinito del ruolo (secondi) finché il gateway non lo comunica. */
export function limitePredefinito(ruolo: string): number {
  return (RUOLI_PRIVILEGIATI.includes(ruolo) ? 15 : 30) * 60;
}

export function registraAzione(adesso = Date.now()): void {
  ultimaAzione = adesso;
  scrivi(CHIAVE, adesso);
}

/** Ultima azione dell'utente in QUALUNQUE scheda del portale. */
export function ultimaAzioneUtente(): number {
  return Math.max(ultimaAzione, leggi(CHIAVE));
}

/** Intestazione per una richiesta in partenza: rinnova solo dopo un'azione recente. */
export function intestazioniRinnovo(adesso = Date.now()): Record<string, string> {
  if (adesso - ultimaAzioneUtente() > FINESTRA_AZIONE_MS) return { [INTESTAZIONE_RINNOVO]: "no" };
  segnaRinnovo(adesso);
  return {};
}

/** Ultima richiesta che ha rinnovato la sessione, in QUALUNQUE scheda. */
export function ultimoRinnovoSessione(): number {
  return Math.max(ultimoRinnovo, leggi(CHIAVE_RINNOVO));
}

export function segnaRinnovo(adesso = Date.now()): void {
  ultimoRinnovo = adesso;
  scrivi(CHIAVE_RINNOVO, adesso);
}

/** Serve un battito: l'utente ha agito dopo l'ultimo rinnovo, fermo da un minuto. */
export function serveBattito(adesso = Date.now()): boolean {
  const rinnovo = ultimoRinnovoSessione();
  return ultimaAzioneUtente() > rinnovo && adesso - rinnovo >= BATTITO_MS;
}

/** Millisecondi alla scadenza per inattività (negativo: già scaduta). */
export function rimanente(limiteSecondi: number, adesso = Date.now()): number {
  return ultimoRinnovoSessione() + limiteSecondi * 1000 - adesso;
}

export const INDIRIZZO_SCADUTA = "/login#spid_errore=AUTH-004";
