// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Nomi dei ruoli in italiano chiaro, gli stessi in testata, guardie e pagine.
 * Unica tabella: se cambia un nome, cambia ovunque.
 */
export const NOME_RUOLO: Record<string, string> = {
  SUPER_ADMIN: "Amministrazione del servizio",
  RSC_CONSERVATORE: "Responsabile del servizio",
  ADMIN_TENANT: "Amministrazione dell'ente",
  OPERATORE: "Operatore",
  VIEWER: "Consultazione",
  API_SYSTEM: "Sistema versante",
};

/** Nome del ruolo, o il codice stesso se il ruolo non è in tabella. */
export const nomeRuolo = (ruolo: string | null | undefined): string =>
  ruolo ? NOME_RUOLO[ruolo] ?? ruolo : "";
