# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/rate_limiter.py — limite di richieste al minuto per piano.

Riferimenti: piano F1.1.3; R03 b4 (VD-C-099). La logica (finestra scorrevole su
Redis, limiti per piano da impostazioni, QTA-001 con Retry-After) è in
``libs.shared.security.quote``; qui restano i nomi storici usati dal gateway.

``RATE_LIMITS`` sono i valori EFFETTIVI letti dalle impostazioni
(``QUOTE_RICHIESTE_MINUTO``), non costanti. La quota di PdV al giorno si applica
nel caso d'uso unico del versamento (``app.services.quota_versamento``); R04
(VD-C-105) ha tolto ``app.auth.quote`` e ``PDV_LIMITS``/``DEFAULT_RPM``, rimasti
senza chiamanti.
"""
from __future__ import annotations

from typing import Any

from libs.shared.security.quote import (
    ImpostazioniQuote,
    LimitiEnte,
    QuotaSuperata,
    consuma_richiesta,
    limite_richieste,
)

#: Impostazioni delle quote del processo (lette all'avvio).
IMPOSTAZIONI_QUOTE = ImpostazioniQuote()

RATE_LIMITS: dict[str, int] = dict(IMPOSTAZIONI_QUOTE.richieste_minuto)


async def check_rate_limit(
    redis: Any,
    key: str,
    piano_or_limit: str | int | LimitiEnte,
    window: int | None = None,
) -> None:
    """
    Conta la richiesta nella finestra scorrevole ``key``; oltre il limite
    (``LimitiEnte``: quota dell'ente se personalizzata, altrimenti del piano;
    oppure il piano o il limite numerico indicato) solleva ``ErroreApi`` QTA-001
    (429) con ``Retry-After``. ``redis`` None: nessun controllo (test).
    """
    if isinstance(piano_or_limit, LimitiEnte):
        piano = piano_or_limit.piano
        limite = piano_or_limit.richieste_minuto(IMPOSTAZIONI_QUOTE)
    elif isinstance(piano_or_limit, str):
        piano = piano_or_limit
        limite = limite_richieste(piano, IMPOSTAZIONI_QUOTE)
    else:
        piano, limite = "personalizzato", int(piano_or_limit)
    try:
        await consuma_richiesta(
            redis, key, limite=limite, piano=piano,
            finestra=window or IMPOSTAZIONI_QUOTE.finestra_secondi,
        )
    except QuotaSuperata as exc:
        raise exc.errore_api() from None


def get_limit_for_piano(piano: str) -> int:
    return limite_richieste(piano, IMPOSTAZIONI_QUOTE)
