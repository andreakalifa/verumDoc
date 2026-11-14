# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Segnalazioni degli account con attività in ``in/`` (R03 f2, REV-15).

Servizio: sftp-ingest. Il server SFTP chiama ``segnala`` (sincrono, nel ciclo
degli eventi di asyncssh) quando un cliente crea, scrive, rinomina o elimina
qualcosa in ``in/``; le segnalazioni si accumulano in memoria e si scrivono sul
database a lotti ogni ``intervallo_secondi`` (una sola INSERT … ON CONFLICT per
lotto), così una scrittura a blocchi di un file grande non diventa una
scrittura sul database per blocco.

Con le segnalazioni il lavoratore guarda solo gli account con attività
(``repository.prendi_segnalazioni``), non tutte le aree a ogni giro. Se il
processo muore prima di scrivere un lotto, il controllo completo periodico del
lavoratore (``LAVORATORE_CONTROLLO_COMPLETO_SECONDI``) ritrova le cartelle.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping

import structlog

from app.infrastructure import repository

logger = structlog.get_logger(__name__)

Scrivi = Callable[..., Awaitable[None]]


class Segnalatore:
    """Accumula le segnalazioni e le scrive a lotti."""

    def __init__(self, *, stabilita_secondi: float, intervallo_secondi: float = 0.5,
                 scrivi: Scrivi | None = None) -> None:
        self._stabilita = stabilita_secondi
        self._intervallo = intervallo_secondi
        self._scrivi = scrivi or repository.segnala_attivita
        self._in_attesa: dict[uuid.UUID, tuple[uuid.UUID, bool]] = {}

    def segnala(self, account_id: uuid.UUID, tenant_id: uuid.UUID, *, pronta: bool) -> None:
        """Annota l'attività di un account («pronta»: una cartella appena
        rinominata con un nome definitivo, da guardare subito)."""
        precedente = self._in_attesa.get(account_id)
        self._in_attesa[account_id] = (tenant_id, pronta or bool(precedente and precedente[1]))

    @property
    def in_attesa(self) -> Mapping[uuid.UUID, tuple[uuid.UUID, bool]]:
        return dict(self._in_attesa)

    async def svuota(self) -> int:
        """Scrive le segnalazioni accumulate; in errore le rimette in attesa."""
        lotto, self._in_attesa = self._in_attesa, {}
        if not lotto:
            return 0
        try:
            await self._scrivi(lotto, stabilita_secondi=self._stabilita)
        except Exception as exc:  # noqa: BLE001 — si riprova al giro dopo, errore nel log
            for account, (ente, pronta) in lotto.items():
                self.segnala(account, ente, pronta=pronta)
            logger.warning("sftp_segnalazioni_non_scritte", n=len(lotto),
                           errore=type(exc).__name__)
            return 0
        return len(lotto)

    async def esegui(self) -> None:
        """Ciclo del processo del server (lifespan)."""
        try:
            while True:
                await asyncio.sleep(self._intervallo)
                await self.svuota()
        finally:
            await self.svuota()
