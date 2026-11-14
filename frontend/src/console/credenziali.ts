// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Le mie credenziali (R12b, VD-C-156): chiamate al gateway, services/gateway/app/auth/
 * credenziali_utente.py. Sessione del portale (cookie HttpOnly e intestazione anti-CSRF del
 * client `api`). Ogni modifica chiede prima la conferma dell'identità, valida per la sola
 * sessione per pochi minuti (ACCESSO_RIAUTENTICAZIONE_MINUTI).
 */
import { api } from "../api/client";

const B = "/v1/auth/credenziali";

export interface PasskeyMia { id: string; nome: string; creata_il: string | null; ultimo_uso: string | null }
export interface StatoCredenziali {
  disponibile: boolean;
  motivo: string | null;
  metodi_ente: string[];
  metodi_locali: string[];
  passkey: PasskeyMia[];
  password: { impostata: boolean; impostata_il: string | null };
  totp: { attivo: boolean; confermato_il: string | null };
  codici_recupero: { rimasti: number; totale: number };
  spid_cie: boolean;
  riautenticazione: { metodi: string[]; valida_per_secondi: number | null; finestra_minuti: number };
  password_lunghezza_minima: number;
}

export const leggiCredenziali = () => api.get(B).then(r => r.data as StatoCredenziali);

export const confermaConPassword = (password: string, codice: { codice?: string; codice_recupero?: string }) =>
  api.post(`${B}/riautenticazione`, { metodo: "PASSWORD_TOTP", password, ...codice })
    .then(r => r.data as { valida_per_secondi: number; finestra_minuti: number });
export const opzioniConfermaPasskey = () =>
  api.post(`${B}/riautenticazione/passkey/opzioni`, {}).then(r => r.data as { opzioni: unknown });
export const confermaConPasskey = (credenziale: unknown) =>
  api.post(`${B}/riautenticazione`, { metodo: "PASSKEY", credenziale })
    .then(r => r.data as { valida_per_secondi: number; finestra_minuti: number });

export const opzioniNuovaPasskey = () => api.post(`${B}/passkey/opzioni`, {}).then(r => r.data as { opzioni: unknown });
export const aggiungiPasskey = (credenziale: unknown, nome: string) =>
  api.post(`${B}/passkey`, { credenziale, nome }).then(r => r.data as PasskeyMia);
export const rinominaPasskey = (id: string, nome: string) =>
  api.patch(`${B}/passkey/${encodeURIComponent(id)}`, { nome }).then(r => r.data);
export const revocaPasskey = (id: string) =>
  api.delete(`${B}/passkey/${encodeURIComponent(id)}`).then(r => r.data as { altre_sessioni_chiuse: number });

export const cambiaPassword = (password_attuale: string, password_nuova: string) =>
  api.put(`${B}/password`, { password_attuale, password_nuova }).then(r => r.data as { altre_sessioni_chiuse: number });

export const nuovoTotp = () => api.post(`${B}/totp`, {}).then(r => r.data as { segreto: string; uri: string });
export const confermaTotp = (codice: string) =>
  api.post(`${B}/totp/conferma`, { codice }).then(r => r.data as { altre_sessioni_chiuse: number });

export const nuoviCodiciRecupero = () =>
  api.post(`${B}/codici-recupero`, {}).then(r => r.data as { codici_recupero: string[] });
