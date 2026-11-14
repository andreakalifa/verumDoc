# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Budget di lettura (secchio di gettoni) per la verifica continua.

Due budget per replica: letture al secondo e byte al secondo
(``VERIFICA_LETTURE_AL_SECONDO``, ``VERIFICA_BYTE_AL_SECONDO``). Chi consuma
oltre la disponibilità aspetta il tempo necessario: in media il tasso non
supera mai quello impostato; la scorta iniziale (``capacita``, predefinita un
secondo di tasso) ammette solo un breve picco. Le attese sono in fila (lock):
nessun consumatore passa davanti a uno che sta già aspettando.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class Budget:
    """Secchio di gettoni asincrono; ``tasso <= 0`` = nessun limite."""

    def __init__(
        self,
        tasso: float,
        *,
        capacita: float | None = None,
        orologio: Callable[[], float] = time.monotonic,
        dormi: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.tasso = float(tasso)
        self.capacita = float(capacita if capacita is not None else max(self.tasso, 1.0))
        self._orologio = orologio
        self._dormi = dormi
        self._gettoni = self.capacita
        self._istante = orologio()
        self._lock = asyncio.Lock()
        self.atteso_secondi = 0.0  # totale delle attese (diagnosi e test)

    @property
    def illimitato(self) -> bool:
        return self.tasso <= 0

    def _ricarica(self) -> None:
        adesso = self._orologio()
        self._gettoni = min(self.capacita, self._gettoni + (adesso - self._istante) * self.tasso)
        self._istante = adesso

    async def consuma(self, quantita: float) -> None:
        """Preleva ``quantita`` gettoni, aspettando se il budget è esaurito."""
        if self.illimitato or quantita <= 0:
            return
        async with self._lock:
            self._ricarica()
            self._gettoni -= quantita
            if self._gettoni < 0:
                attesa = -self._gettoni / self.tasso
                self.atteso_secondi += attesa
                await self._dormi(attesa)
                self._ricarica()
