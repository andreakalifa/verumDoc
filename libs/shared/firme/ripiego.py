# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/ripiego.py — verificatore principale con ripiego (R08, D01).

Il principale (EU DSS) verifica tutto. Il ripiego (pyHanko) scatta SOLO se il
principale non è RAGGIUNGIBILE (``ErroreConnessioneVerificatore``: rete,
connessione, tempo scaduto) e il formato del file è fra quelli del ripiego
(predefinito: solo PAdES, come deciso in D01). Revisione R08 (X7):

  - le risposte del principale (401/403/5xx, Trusted List non ancora caricata)
    NON attivano il ripiego: restano ``ErroreVerificatoreNonDisponibile`` e il
    chiamante ritenta;
  - il ripiego non DEGRADA il giudizio: il suo esito vale solo se è netto
    (tutte le firme TOTAL_PASSED, oppure una firma crittograficamente non
    valida, che non dipende dalle trust anchor). Un esito INDETERMINATE o una
    busta che il ripiego non legge possono dipendere dai suoi limiti (trust
    anchor, revoche): si solleva ``ErroreVerificatoreNonDisponibile`` e si
    ritenta con il principale, mai un rifiuto definitivo;
  - ``configurazione.crea_verificatore`` non attiva il ripiego se pyHanko non
    ha trust anchor.
"""
from __future__ import annotations

from dataclasses import replace

import structlog

from libs.shared.firme.esito import EsitoVerifica, FormatoFirma, Indicazione, LivelloVerifica
from libs.shared.firme.interfaccia import (
    ErroreConnessioneVerificatore,
    ErroreVerificatoreNonDisponibile,
    Verificatore,
)
from libs.shared.firme.rilevazione import rileva_formato_firma

logger = structlog.get_logger(__name__)


def esito_netto(esito: EsitoVerifica) -> bool:
    """L'esito del ripiego non dipende dai suoi limiti: non firmato, tutte le
    firme TOTAL_PASSED, o almeno una firma crittograficamente non valida."""
    if not esito.firmato:
        return True
    if not esito.firme or esito.limite_superato:
        return False
    if any(f.indicazione is Indicazione.TOTAL_FAILED and f.crittografia_valida is False
           for f in esito.firme):
        return True
    return all(f.indicazione is Indicazione.TOTAL_PASSED for f in esito.firme)


class VerificatoreConRipiego:
    def __init__(self, principale: Verificatore, ripiego: Verificatore, *,
                 formati_ripiego: frozenset[FormatoFirma] = frozenset({FormatoFirma.PADES})
                 ) -> None:
        self.principale = principale
        self.ripiego = ripiego
        self.formati_ripiego = formati_ripiego & ripiego.formati
        self.nome = f"{principale.nome} (ripiego: {ripiego.nome})"
        self.formati = principale.formati | self.formati_ripiego

    async def verifica(self, contenuto: bytes, *, livello: LivelloVerifica,
                       nome_file: str = "", originale: bytes | None = None,
                       verificato_il: str | None = None) -> EsitoVerifica:
        try:
            return await self.principale.verifica(
                contenuto, livello=livello, nome_file=nome_file, originale=originale,
                verificato_il=verificato_il)
        except ErroreConnessioneVerificatore as exc:
            formato = rileva_formato_firma(contenuto, nome_file)
            if formato not in self.formati_ripiego:
                raise
            logger.warning("verifica_firme_ripiego", principale=self.principale.nome,
                           ripiego=self.ripiego.nome, formato=formato.value, motivo=str(exc))
            esito = await self.ripiego.verifica(contenuto, livello=livello, nome_file=nome_file,
                                                originale=originale,
                                                verificato_il=verificato_il)
            if not esito_netto(esito):
                raise ErroreVerificatoreNonDisponibile(
                    f"verificatore principale non raggiungibile ({exc}) e esito del ripiego "
                    f"{self.ripiego.nome} non netto: si ritenta con il principale") from exc
            return replace(esito, ripiego=True, note=(
                *esito.note, f"verificatore principale non disponibile ({exc}): verificato "
                             f"con {self.ripiego.nome}"))


__all__ = ["VerificatoreConRipiego", "esito_netto"]
