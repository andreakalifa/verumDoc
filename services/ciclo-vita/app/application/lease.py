# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/lease.py — lease del lavoratore, rinnovato anche DENTRO un passo.

Riferimenti: REGOLE_SVILUPPO §12.9 (operazioni lunghe uniche: lease con
scadenza); revisione R05, rilievo R11.

Un passo dello scarto tratta fino a ``LAVORATORE_LOTTO`` PdA, ognuno con un
elenco delle versioni nel WORM, una HEAD per versione e le cancellazioni: con
S3 lento supera la durata del lease (``LAVORATORE_LEASE_SECONDI``). Se il lease
si rinnovasse solo fra un passo e l'altro, un'altra replica prenderebbe la
pratica scaduta e le due eseguirebbero lo stesso lotto insieme. Qui il passo
rinnova il lease prima di OGNI elemento (``rinnova_nel_passo``) e si ferma con
``LeasePerso`` se un'altra replica l'ha preso: nessuna cancellazione dopo la
perdita del lease.

Il lease corrente viaggia in una ``ContextVar`` impostata da ``lease_attivo``
nel lavoratore: i passi non cambiano firma e, chiamati fuori dal lavoratore
(test di unità), non rinnovano nulla.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

from app.config import settings


class LeasePerso(RuntimeError):
    """Il lease della pratica è passato a un altro detentore: il passo si ferma."""


@dataclass(frozen=True)
class _Lease:
    pratica_id: uuid.UUID
    detentore: str
    orologio: Callable[[], datetime]


_corrente: ContextVar[_Lease | None] = ContextVar("ciclo_vita_lease", default=None)


async def rinnova(pratica_id: uuid.UUID, detentore: str, adesso: datetime) -> bool:
    """Proroga il lease di ``LAVORATORE_LEASE_SECONDI`` se è ancora di ``detentore``."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        r = await session.execute(text(
            "UPDATE public.pratiche SET lease_scade_il = :scade WHERE id = :id "
            "AND lease_detentore = :d"),
            {"id": pratica_id, "d": detentore,
             "scade": adesso + timedelta(seconds=settings.lavoratore_lease_secondi)})
    return bool(r.rowcount)


@contextmanager
def lease_attivo(pratica_id: uuid.UUID, detentore: str,
                 orologio: Callable[[], datetime]) -> Iterator[None]:
    """Rende il lease della pratica disponibile ai passi (``rinnova_nel_passo``)."""
    token = _corrente.set(_Lease(pratica_id, detentore, orologio))
    try:
        yield
    finally:
        _corrente.reset(token)


async def rinnova_nel_passo() -> None:
    """Rinnova il lease corrente; ``LeasePerso`` se non è più nostro. Fuori dal
    lavoratore (nessun lease attivo) non fa nulla."""
    lease = _corrente.get()
    if lease is None:
        return
    if not await rinnova(lease.pratica_id, lease.detentore, lease.orologio()):
        raise LeasePerso(f"lease della pratica {lease.pratica_id} perso durante il passo")


__all__ = ["LeasePerso", "lease_attivo", "rinnova", "rinnova_nel_passo"]
