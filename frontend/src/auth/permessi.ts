// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Permessi della persona connessa (revisione R17, audit R17-01/02/12/13).
 *
 * Pagine, voci del menu e comandi seguono il PERMESSO dell'azione, non un elenco di ruoli
 * scritto pagina per pagina: la fonte è il gateway (`utente.permessi` della sessione:
 * permessi effettivi del ruolo fisso o personalizzato). Se la sessione non li porta
 * (gateway precedente), valgono i modelli dei ruoli fissi qui sotto, copia di
 * `libs/verumdoc_rules/permessi.py` (MODELLI) verificata da
 * `libs/verumdoc_rules/tests/test_r17_permessi_console.py`.
 * Il servizio ricontrolla sempre (REGOLE §14.1).
 */
import type { UserSession } from "./AuthContext";

export const CONSULTA = "archivio:consulta";
export const VERSA = "versamento:esegui";
export const ESIBIZIONE = "esibizione:richiedi";
export const RETTIFICA = "rettifica:richiedi";
export const ANNULLAMENTO = "annullamento:richiedi";

const TUTTI_I_PERMESSI = [
  "archivio:consulta", "versamento:esegui", "esibizione:richiedi", "rettifica:richiedi",
  "annullamento:richiedi", "scarto:esegui", "scarto:autorizza", "annullamento:approva",
  "firmatari:gestisci", "pratica:chiudi_eccezioni", "utenti:gestisci", "ruoli:gestisci",
  "accesso:configura", "firma_fornitori:gestisci", "pec_canali:gestisci", "pratica:riprendi",
  "documentazione:genera",
];

// MODELLI-INIZIO (copia di libs/verumdoc_rules/permessi.py, controllata dai test)
export const MODELLI: Record<string, string[]> = {
  SUPER_ADMIN: TUTTI_I_PERMESSI,
  RSC_CONSERVATORE: TUTTI_I_PERMESSI,
  ADMIN_TENANT: ["archivio:consulta", "versamento:esegui", "esibizione:richiedi", "rettifica:richiedi",
                 "annullamento:richiedi", "scarto:autorizza", "annullamento:approva",
                 "utenti:gestisci", "ruoli:gestisci", "accesso:configura"],
  OPERATORE: ["archivio:consulta", "versamento:esegui", "esibizione:richiedi", "rettifica:richiedi",
              "annullamento:richiedi"],
  VIEWER: ["archivio:consulta"],
  API_SYSTEM: ["archivio:consulta", "versamento:esegui"],
};
// MODELLI-FINE
export const PERMESSI_NOTI = TUTTI_I_PERMESSI;

/** Permessi effettivi della persona: dalla sessione, altrimenti dal modello del ruolo. */
export function permessiDi(user: UserSession | null | undefined): ReadonlySet<string> {
  if (!user) return new Set();
  if (Array.isArray(user.permessi) && user.permessi.length) return new Set(user.permessi);
  return new Set(MODELLI[user.ruolo] ?? []);
}

/** True se la persona può compiere l'azione `permesso`. */
export const puo = (user: UserSession | null | undefined, permesso: string): boolean =>
  permessiDi(user).has(permesso);
