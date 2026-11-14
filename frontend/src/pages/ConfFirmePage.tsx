// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Firme (R08, decisioni D01 e D22): stessa console di «Impostazioni e regole» con
 * il gruppo «firme».
 *  - FIRME_LIVELLO_VERIFICA: livello di verifica delle firme dei documenti versati
 *    (nessuna, rilevata, crittografica, con revoche); il conservatore fissa il
 *    minimo, l'ente lo può solo alzare, anche per classe;
 *  - NULLA_OSTA_FIRMA, CICLO_VITA_FIRME_LIVELLO, ANNULLAMENTO_RICHIESTA_FIRMATA:
 *    firme del ciclo di vita (scarto, nulla osta, annullamento).
 * L'effetto di un esito insufficiente (blocca, avvisa, ignora) è la regola RGL-109
 * nella tabella delle regole.
 */
import { ConsoleImpostazioni } from "./ConfImpostazioniPage";

export default function ConfFirmePage() {
  return (
    <ConsoleImpostazioni gruppo="firme"
      titolo="Verifica delle firme"
      spiegazione="Quanto a fondo si verificano le firme dei documenti versati e dei documenti di scarto e annullamento. Il conservatore fissa il livello minimo; l'ente può solo renderlo più severo. Cosa succede quando una firma non basta lo decide la regola RGL-109." />
  );
}
