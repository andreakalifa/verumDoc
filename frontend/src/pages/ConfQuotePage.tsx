// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Quote (conservatore): R07 (D13) al livello del conservatore i limiti dei piani
 * (starter, business, enterprise: valori R03 come modello iniziale), al livello
 * dell'ente la deroga per il singolo ente (voci `riservata_conservatore`). Stessa
 * console di «Impostazioni e regole» con il gruppo «quote»: ogni modifica passa da
 * una bozza (risorsa «quote») con anteprima, applicazione o decorrenza futura.
 * La scelta «rallenta invece di rifiutare» è dell'ente (gruppo «consumi»).
 */
import { ConsoleImpostazioni } from "./ConfImpostazioniPage";

export default function ConfQuotePage() {
  return (
    <ConsoleImpostazioni gruppo="quote" conRegole={false}
      titolo="Quote degli enti"
      spiegazione="Al livello del conservatore: i limiti di ogni piano. Al livello dell'ente: la deroga per quell'ente (zero vuol dire il limite del piano). All'80% e al 100% l'ente riceve un avviso automatico." />
  );
}
