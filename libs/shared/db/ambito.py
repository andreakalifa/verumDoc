# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/shared/db/ambito.py — ambito d'ente delle transazioni (Row Level Security)

Riferimenti: VD-C-080, VD-P-02 (R05, agente r05-sicurezza); REGOLE_SVILUPPO
§14.2 (perimetro vuoto = nessun dato), §14.3 (cliente non determinabile =
negato); documento: docs/architettura/sicurezza.md.

La Row Level Security di PostgreSQL (migrazione 20261023_038) è la SECONDA
difesa dell'isolamento fra enti, dietro i filtri applicativi: un servizio che
dimentica ``WHERE tenant_id = …`` non vede comunque le righe di un altro ente.
Le politiche leggono due impostazioni LOCALI della transazione:

  - ``app.ente``   = id dell'ente (UUID) per cui la transazione opera;
  - ``app.ambito`` = ``conservatore`` per chi opera su tutti gli enti.

UN SOLO PUNTO le scrive: l'evento ``begin`` degli engine creati da
``libs.shared.db`` (``installa_ambito``) esegue, all'inizio di OGNI transazione
(ORM e Core, anche dopo un commit a metà sessione)::

    SELECT set_config('app.ente', :ente, true), set_config('app.ambito', :ambito, true)

``set_config(..., true)`` equivale a ``SET LOCAL``: vale fino alla fine della
transazione, quindi è corretto con PgBouncer in modalità transaction (mai SET
di sessione, che resterebbe sulla connessione e passerebbe alla richiesta di un
altro ente). Senza ambito le due impostazioni sono vuote e le politiche non
restituiscono righe (fail-closed).

Da dove viene l'ambito (variabile di contesto ``contextvars``, quindi per
richiesta HTTP o per task asyncio):
  1. identità VERIFICATA (``imposta_ambito_identita``), chiamata solo dalle
     funzioni di verifica di ``libs/shared/security`` (token JWT dell'utente o
     di canale, chiave API): ruolo del conservatore (``SUPER_ADMIN``,
     ``RSC_CONSERVATORE``) → ambito conservatore; altrimenti l'ente del token;
  2. lavoratori che operano su più enti (consumer, relay dell'outbox,
     sequenziatore, verifica continua, pulizie): ``ambito_conservatore(motivo)``
     esplicito all'avvio del lavoratore, con il motivo nel log;
  3. codice che opera per un ente noto fuori da una richiesta (test, comandi):
     ``ambito_ente(tenant_id)``.
Nessun dato della richiesta (parametri, intestazioni non verificate) può
impostare l'ambito conservatore.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.shared.security.ambito_dati import (
    IMPOSTAZIONE_AMBITO,
    IMPOSTAZIONE_ENTE,
    NESSUN_AMBITO,
    RUOLI_AMBITO_CONSERVATORE,
    VALORE_CONSERVATORE,
    VALORE_ENTE,
    Ambito,
    AmbitoNonValido,
    MiddlewareAmbitoRichiesta,
    ambito_conservatore,
    ambito_corrente,
    ambito_ente,
    come_conservatore,
    con_ambito_conservatore,
    imposta_ambito,
    imposta_ambito_identita,
    ripristina_ambito,
)

_ISTRUZIONE = text(
    "SELECT set_config('app.ente', :ente, true), set_config('app.ambito', :ambito, true)"
)


def fabbrica_sessione_conservatore(motivo: str) -> Callable[[], Any]:
    """Fabbrica di sessioni (come ``get_async_session``) nell'ambito conservatore:
    per i repository usati SOLO da lavoratori multi-ente (code, consegne)."""
    if len((motivo or "").strip()) < 5:
        raise AmbitoNonValido("l'ambito conservatore richiede un motivo esplicito")

    @asynccontextmanager
    async def sessione() -> AsyncIterator[Any]:
        from libs.shared.db import get_async_session

        with ambito_conservatore(motivo, registra=False):
            async with get_async_session() as s:
                yield s

    return sessione


def _applica_ambito(connessione: Any) -> None:
    """Evento ``begin``: ``SET LOCAL`` dell'ambito corrente sulla transazione."""
    connessione.execute(_ISTRUZIONE, ambito_corrente().parametri())


def installa_ambito(engine: AsyncEngine) -> None:
    """Collega l'ambito a ogni transazione dell'engine (idempotente)."""
    sincrono = engine.sync_engine
    if not event.contains(sincrono, "begin", _applica_ambito):
        event.listen(sincrono, "begin", _applica_ambito)


__all__ = [
    "IMPOSTAZIONE_AMBITO", "IMPOSTAZIONE_ENTE", "NESSUN_AMBITO", "RUOLI_AMBITO_CONSERVATORE",
    "VALORE_CONSERVATORE", "VALORE_ENTE", "Ambito", "AmbitoNonValido",
    "MiddlewareAmbitoRichiesta", "ambito_conservatore", "ambito_corrente", "ambito_ente",
    "come_conservatore", "con_ambito_conservatore", "fabbrica_sessione_conservatore",
    "imposta_ambito", "imposta_ambito_identita", "installa_ambito", "ripristina_ambito",
]
