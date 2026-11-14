# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/quote.py — quote personalizzate per ente (R03 i3).

Riferimenti: piano R03–R05 §4.2 («limiti di frequenza e di volume per piano e
per ente»); REGOLE_SVILUPPO §8.1 (limiti in impostazioni, non nel codice),
§8.2 (catena di risoluzione unica); rapporto R03 f2, proposta sulle quote.

Le quote per PIANO sono impostazioni di processo (variabili ``QUOTE_*`` lette da
``libs.shared.security.quote``). Un ente fuori scala (regione, ASL, comune
capoluogo) può avere un valore proprio che PREVALE sul piano: è un'impostazione
del registro al livello ENTE, salvata come le altre in ``impostazioni_valori``
(``livello = 'ENTE'``, ``tenant_id`` dell'ente, ``codice`` qui sotto, ``valore``
intero JSON), con motivo e audit della console di configurazione (R05).

Codici:

  QUOTA_PDV_GIORNO         intero  0 = secondo il piano   PdV al giorno dell'ente
  QUOTA_RICHIESTE_MINUTO   intero  0 = secondo il piano   richieste al minuto dell'ente

Il predefinito 0 vuol dire «nessun valore per l'ente: vale il piano». Questo
registro è separato da ``REGISTRO_V1`` perché le quote non sono regole del
versamento: non entrano nella fotografia del PdV.

Pacchetto puro: nessun database. Chi legge i valori (``libs.shared.security.quote``)
li passa a ``quote_personalizzate``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Impostazione,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    TipoImpostazione,
)

QUOTA_PDV_GIORNO = "QUOTA_PDV_GIORNO"
QUOTA_RICHIESTE_MINUTO = "QUOTA_RICHIESTE_MINUTO"

#: Tetti di plausibilità: un valore oltre è un errore di battitura, non una scelta.
_MASSIMO_PDV_GIORNO = 10_000_000
_MASSIMO_RICHIESTE_MINUTO = 1_000_000

REGISTRO_QUOTE = RegistroImpostazioni(
    (
        Impostazione(
            QUOTA_PDV_GIORNO,
            TipoImpostazione.INTERO,
            predefinito=0,
            descrizione=(
                "Pacchetti di versamento al giorno dell'ente; 0 = limite del piano "
                "(QUOTE_PDV_GIORNO)"
            ),
            minimo=0,
            massimo=_MASSIMO_PDV_GIORNO,
            ambito_ammesso=frozenset({Livello.ENTE}),
            riservata_conservatore=True,
            applicata_da="libs/shared/security/quote (tutti i servizi)",
            gruppo="quote",
        ),
        Impostazione(
            QUOTA_RICHIESTE_MINUTO,
            TipoImpostazione.INTERO,
            predefinito=0,
            descrizione=(
                "Richieste al minuto dell'ente su tutte le API; 0 = limite del piano "
                "(QUOTE_RICHIESTE_MINUTO)"
            ),
            minimo=0,
            massimo=_MASSIMO_RICHIESTE_MINUTO,
            ambito_ammesso=frozenset({Livello.ENTE}),
            riservata_conservatore=True,
            applicata_da="libs/shared/security/quote (tutti i servizi)",
            gruppo="quote",
        ),
    )
)

#: Codici da leggere dal livello ENTE.
CODICI_QUOTE: tuple[str, ...] = REGISTRO_QUOTE.codici()


@dataclass(frozen=True)
class QuotePersonalizzate:
    """Valori propri dell'ente; ``None`` = vale il limite del piano."""

    pdv_giorno: int | None = None
    richieste_minuto: int | None = None


def quote_personalizzate(valori_ente: Mapping[str, Any]) -> QuotePersonalizzate:
    """Risolve le quote dell'ente dai valori del livello ENTE (``{codice: valore}``).

    Usa la catena unica del registro: un valore fuori tipo o fuori dai limiti
    solleva ``ValoreImpostazioneNonValido`` (errore di configurazione: il
    chiamante decide come degradare, non si indovina un valore)."""
    risolutore = Risolutore(
        REGISTRO_QUOTE,
        FornitoreDaDizionari({Livello.ENTE: {k: v for k, v in valori_ente.items()
                                             if k in CODICI_QUOTE}}),
    )
    # Il contesto non dipende dal tempo: le quote non hanno validità datata.
    contesto = ContestoRisoluzione(adesso=datetime(2000, 1, 1, tzinfo=UTC))

    def _valore(codice: str) -> int | None:
        effettivo = risolutore.effettiva(contesto, codice)
        return int(effettivo.valore) if effettivo.valore > 0 else None

    return QuotePersonalizzate(
        pdv_giorno=_valore(QUOTA_PDV_GIORNO),
        richieste_minuto=_valore(QUOTA_RICHIESTE_MINUTO),
    )


__all__ = [
    "CODICI_QUOTE",
    "QUOTA_PDV_GIORNO",
    "QUOTA_RICHIESTE_MINUTO",
    "REGISTRO_QUOTE",
    "QuotePersonalizzate",
    "quote_personalizzate",
]
