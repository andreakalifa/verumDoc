# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/shared/security/ambito_dati.py — ambito d'ente della richiesta o del lavoro

Riferimenti: VD-C-080, VD-P-02 (R05, agente r05-sicurezza); docs/architettura/sicurezza.md.

Parte SENZA dipendenze dal database dell'ambito della Row Level Security:
variabile di contesto, identità verificata → ambito, ambiti espliciti dei
lavoratori, azzeramento a ogni richiesta HTTP. La usano le funzioni di verifica
di ``libs/shared/security`` anche nei servizi senza database (signature).
L'unico punto che scrive l'ambito sulla transazione (``SET LOCAL app.ente`` /
``app.ambito``) è ``libs.shared.db.ambito.installa_ambito``, che legge
``ambito_corrente()``: vedi il suo modulo per il disegno completo.
"""
from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

#: Impostazioni lette dalle politiche RLS (migrazione 20261023_038).
IMPOSTAZIONE_ENTE = "app.ente"
IMPOSTAZIONE_AMBITO = "app.ambito"
#: Valore di ``app.ambito`` che apre tutti gli enti.
VALORE_CONSERVATORE = "conservatore"
#: Valore di ``app.ambito`` di una transazione legata a un ente.
VALORE_ENTE = "ente"

#: Ruoli del personale del conservatore che vedono tutti gli enti (gli stessi
#: dei controlli applicativi: dashboard, distribution, hashchain).
RUOLI_AMBITO_CONSERVATORE: frozenset[str] = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})


@dataclass(frozen=True)
class Ambito:
    """Ambito di una transazione: un ente, tutti gli enti (conservatore) o nessuno."""

    ente: UUID | None = None
    conservatore: bool = False
    motivo: str = ""

    def parametri(self) -> dict[str, str]:
        """Valori di ``app.ente`` e ``app.ambito`` (stringhe vuote = nessun ambito)."""
        if self.conservatore:
            return {"ente": "", "ambito": VALORE_CONSERVATORE}
        if self.ente is not None:
            return {"ente": str(self.ente), "ambito": VALORE_ENTE}
        return {"ente": "", "ambito": ""}


NESSUN_AMBITO = Ambito()
_ambito: ContextVar[Ambito] = ContextVar("vd_ambito_db", default=NESSUN_AMBITO)


class AmbitoNonValido(ValueError):
    """Ente non esprimibile come UUID, o ambito conservatore senza motivo."""


def ambito_corrente() -> Ambito:
    """Ambito che verrà applicato alla prossima transazione di questo contesto."""
    return _ambito.get()


def _uuid(ente: UUID | str) -> UUID:
    if isinstance(ente, UUID):
        return ente
    try:
        return UUID(str(ente))
    except (TypeError, ValueError) as exc:
        raise AmbitoNonValido(f"ente non valido per l'ambito della transazione: {ente!r}") from exc


def imposta_ambito(ambito: Ambito) -> Token[Ambito]:
    """Imposta l'ambito del contesto corrente; restituisce il token per ``ripristina_ambito``."""
    return _ambito.set(ambito)


def ripristina_ambito(token: Token[Ambito]) -> None:
    """Torna all'ambito precedente a ``imposta_ambito``."""
    _ambito.reset(token)


def imposta_ambito_identita(payload: Mapping[str, Any]) -> Ambito:
    """Ambito dall'identità VERIFICATA (token o chiave API): solo per
    ``libs/shared/security``. Ruolo del conservatore → tutti gli enti; altrimenti
    l'ente del token; senza ente valido → nessun ambito (nessuna riga)."""
    ruolo = str(payload.get("ruolo") or "")
    if ruolo in RUOLI_AMBITO_CONSERVATORE:
        ambito = Ambito(conservatore=True, motivo=f"ruolo {ruolo}")
    else:
        try:
            ambito = Ambito(ente=_uuid(payload.get("tenant_id") or ""))
        except AmbitoNonValido:
            ambito = NESSUN_AMBITO
    _ambito.set(ambito)
    return ambito


@contextmanager
def ambito_ente(ente: UUID | str) -> Iterator[Ambito]:
    """Blocco in cui le transazioni operano per un solo ente."""
    ambito = Ambito(ente=_uuid(ente))
    token = _ambito.set(ambito)
    try:
        yield ambito
    finally:
        _ambito.reset(token)


def _ambito_conservatore(motivo: str) -> Ambito:
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise AmbitoNonValido("l'ambito conservatore richiede un motivo esplicito")
    return Ambito(conservatore=True, motivo=motivo)


@contextmanager
def ambito_conservatore(motivo: str, *, registra: bool = True) -> Iterator[Ambito]:
    """Blocco in cui le transazioni vedono TUTTI gli enti (lavoratori multi-ente).

    Il motivo è obbligatorio e va nel log (``registra=False`` per i blocchi
    brevi e frequenti di un modulo multi-ente, che lo dichiara nel codice):
    l'elenco dei chiamanti è in docs/architettura/sicurezza.md."""
    ambito = _ambito_conservatore(motivo)
    token = _ambito.set(ambito)
    if registra:
        logger.info("ambito_conservatore_attivo", motivo=ambito.motivo)
    try:
        yield ambito
    finally:
        _ambito.reset(token)


P = ParamSpec("P")
R = TypeVar("R")


def con_ambito_conservatore(motivo: str) -> Callable[[Callable[P, Awaitable[R]]],
                                                      Callable[P, Awaitable[R]]]:
    """Decoratore per l'ingresso di un lavoratore asincrono multi-ente."""
    _ambito_conservatore(motivo)  # motivo controllato alla decorazione

    def decora(funzione: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(funzione)
        async def avvolta(*args: P.args, **kwargs: P.kwargs) -> R:
            with ambito_conservatore(motivo):
                return await funzione(*args, **kwargs)
        return avvolta
    return decora


async def come_conservatore[T](motivo: str, lavoro: Awaitable[T]) -> T:
    """Esegue ``lavoro`` (una coroutine) nell'ambito conservatore: per i task dei
    lavoratori multi-ente, ``asyncio.create_task(come_conservatore("…", ciclo()))``."""
    with ambito_conservatore(motivo):
        return await lavoro


class MiddlewareAmbitoRichiesta:
    """Ogni richiesta HTTP parte SENZA ambito e, finita, non lascia il suo ambito
    al chiamante (ASGI in-process, test con ``ASGITransport``): l'ambito lo
    imposta solo la verifica dell'identità dentro la richiesta. ASGI puro."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        token = _ambito.set(NESSUN_AMBITO)
        try:
            await self.app(scope, receive, send)
        finally:
            _ambito.reset(token)


__all__ = [
    "IMPOSTAZIONE_AMBITO", "IMPOSTAZIONE_ENTE", "NESSUN_AMBITO", "RUOLI_AMBITO_CONSERVATORE",
    "VALORE_CONSERVATORE", "VALORE_ENTE", "Ambito", "AmbitoNonValido",
    "MiddlewareAmbitoRichiesta", "ambito_conservatore", "ambito_corrente", "ambito_ente",
    "come_conservatore", "con_ambito_conservatore", "imposta_ambito", "imposta_ambito_identita",
    "ripristina_ambito",
]
