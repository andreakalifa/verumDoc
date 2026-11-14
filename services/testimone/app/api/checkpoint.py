# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/api/checkpoint.py — API del testimone (contratto K3).

Rotte:
  POST /api/v1/testimone/checkpoint            controfirma (token di servizio del registro)
  GET  /api/v1/testimone/checkpoint            ultimo checkpoint controfirmato (pubblico)
  GET  /api/v1/testimone/checkpoint/{dimensione}  checkpoint controfirmato a quella dimensione
  GET  /api/v1/testimone/stato                 stato del testimone e sua chiave pubblica

Le GET sono PUBBLICHE per scelta (elenco esplicito delle rotte pubbliche,
REGOLE §19.1): contengono solo origine, dimensione, radice e firme, cioè
quello che il cliente riceve comunque nelle ricevute; servono a clienti e
ispettori per confrontare i checkpoint senza passare dal conservatore.

Risposte del POST (K3): 200 ``text/plain`` con la sola riga di firma del
testimone; 409 ``text/plain`` con la dimensione nota (formato tlog-witness);
gli altri rifiuti in ``application/problem+json`` con codici TST.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Annotated

import structlog
from fastapi import APIRouter, Header, Path, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.application.controfirma import controfirma
from app.domain import checkpoint as cp_dominio
from app.domain.protocollo import (
    Esito,
    RichiestaNonValida,
    RichiestaTroppoGrande,
    analizza_richiesta,
)
from app.infrastructure.modelli import CheckpointControfirmato, StatoRegistro
from libs.shared.db import DBNonInizializzato, get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/testimone", tags=["testimone"], responses=RISPOSTE_ERRORE)

TESTO = "text/plain; charset=utf-8"


def _contesto(request: Request) -> cp_dominio.ContestoTestimone:
    contesto = getattr(request.app.state, "testimone", None)
    if contesto is None:
        raise ErroreApi("TST-503", "Il testimone non è configurato")
    return contesto  # type: ignore[no-any-return]


def autentica_registro(authorization: str | None, impronte: list[str]) -> None:
    """Token opaco del registro confrontato per impronta SHA-256, a tempo costante."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ErroreApi(
            "TST-401",
            "Manca il token di servizio del registro",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip().encode("utf-8")
    impronta = hashlib.sha256(token).hexdigest()
    valido = False
    for attesa in impronte:
        valido |= hmac.compare_digest(impronta, attesa)
    if not valido:
        raise ErroreApi(
            "TST-401",
            "Token di servizio del registro non accettato",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _leggi_corpo(request: Request, massimo: int) -> bytes:
    """Corpo letto a pezzi con limite: mai più di ``massimo`` byte in memoria."""
    dichiarata = request.headers.get("content-length")
    if dichiarata is not None and dichiarata.isdigit() and int(dichiarata) > massimo:
        raise ErroreApi("TST-413", f"Corpo di {dichiarata} byte: al massimo {massimo}")
    parti: list[bytes] = []
    letti = 0
    async for pezzo in request.stream():
        letti += len(pezzo)
        if letti > massimo:
            raise ErroreApi("TST-413", f"Corpo oltre {massimo} byte")
        parti.append(pezzo)
    return b"".join(parti)


@router.post(
    "/checkpoint",
    summary="Controfirma un checkpoint del registro (K3)",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {"text/plain": {}},
            "description": "Riga di firma «— verumdoc-testimone <base64>»",
        },
        409: {
            "content": {"text/plain": {}},
            "description": "Dimensione dell'ultimo checkpoint controfirmato",
        },
    },
)
async def post_checkpoint(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> PlainTextResponse:
    ctx = _contesto(request)
    autentica_registro(authorization, ctx.impronte_token)
    corpo = await _leggi_corpo(request, ctx.max_corpo_byte)
    try:
        richiesta = analizza_richiesta(corpo, max_righe_prova=ctx.max_righe_prova)
    except RichiestaTroppoGrande as exc:
        ctx.metriche.rifiuti.labels("troppo_grande").inc()
        raise ErroreApi("TST-413", str(exc)) from None
    except RichiestaNonValida as exc:
        ctx.metriche.rifiuti.labels("corpo_non_valido").inc()
        raise ErroreApi("TST-400", str(exc)) from None
    try:
        cp = ctx.verifica_registro(richiesta.nota)
    except cp_dominio.CheckpointNonValido as exc:
        ctx.metriche.rifiuti.labels("checkpoint_non_valido").inc()
        raise ErroreApi("TST-400", str(exc)) from None
    except cp_dominio.FirmaRegistroNonValida as exc:
        ctx.metriche.rifiuti.labels("firma_registro").inc()
        logger.warning("firma_registro_non_valida", motivo=str(exc))
        raise ErroreApi("TST-403", str(exc)) from None
    if cp.origine not in ctx.origini:
        ctx.metriche.rifiuti.labels("origine").inc()
        raise ErroreApi(
            "TST-405",
            "Origine non osservata da questo testimone",
            dettagli={"origini_ammesse": list(ctx.origini)},
        )

    try:
        async with get_async_session() as sessione:
            risultato = await controfirma(
                sessione, richiesta, cp, chiave=ctx.chiave, metriche=ctx.metriche
            )
    except (SQLAlchemyError, DBNonInizializzato, OSError) as exc:
        raise errore_registrato(
            "TST-503", exc, evento="controfirma_db_non_disponibile", origine=cp.origine
        ) from None

    if risultato.bloccato or risultato.esito is Esito.FORK:
        raise ErroreApi(
            "TST-423",
            "Testimone bloccato: il registro ha presentato due checkpoint firmati con la "
            "stessa dimensione e radici diverse. Nessuna controfirma finché un operatore "
            "del testimone non interviene.",
            dettagli={
                "stato": "COMPROMESSO",
                "origine": cp.origine,
                "dimensione_presentata": cp.dimensione,
            },
        )
    if risultato.esito in (Esito.CONTROFIRMA, Esito.IDEMPOTENTE):
        return PlainTextResponse(risultato.riga_firma or "", media_type=TESTO)
    if risultato.esito is Esito.OLD_NON_COINCIDE:
        return PlainTextResponse(
            f"{risultato.dimensione_nota or 0}\n", status_code=409, media_type=TESTO
        )
    if risultato.esito is Esito.OLD_OLTRE:
        raise ErroreApi(
            "TST-400", f"«old» {richiesta.old} supera la dimensione del checkpoint {cp.dimensione}"
        )
    raise ErroreApi(
        "TST-422",
        f"La prova non dimostra che il checkpoint {cp.dimensione} estende il "
        f"{richiesta.old} già controfirmato",
        dettagli={"old": richiesta.old, "dimensione": cp.dimensione},
    )


# ── Letture pubbliche ──────────────────────────────────────────────────


def _origine(ctx: cp_dominio.ContestoTestimone, origine: str | None) -> str:
    scelta = origine or ctx.origini[0]
    if scelta not in ctx.origini:
        raise ErroreApi(
            "TST-405",
            "Origine non osservata da questo testimone",
            dettagli={"origini_ammesse": list(ctx.origini)},
        )
    return scelta


async def _leggi_checkpoint(origine: str, dimensione: int | None) -> CheckpointControfirmato:
    try:
        async with get_async_session() as sessione:
            stato = (
                await sessione.execute(
                    select(StatoRegistro).where(StatoRegistro.origine == origine)
                )
            ).scalar_one_or_none()
            if stato is None or stato.dimensione is None:
                raise ErroreApi("TST-404", "Il testimone non ha ancora controfirmato checkpoint")
            filtro = [
                CheckpointControfirmato.origine == origine,
                CheckpointControfirmato.epoca == stato.epoca,
            ]
            if dimensione is None:
                filtro.append(CheckpointControfirmato.dimensione == stato.dimensione)
            else:
                filtro.append(CheckpointControfirmato.dimensione == dimensione)
            riga = (
                await sessione.execute(select(CheckpointControfirmato).where(*filtro))
            ).scalar_one_or_none()
    except (SQLAlchemyError, DBNonInizializzato, OSError) as exc:
        raise errore_registrato("TST-503", exc, evento="lettura_checkpoint") from None
    if riga is None:
        raise ErroreApi("TST-404", f"Nessun checkpoint controfirmato con dimensione {dimensione}")
    return riga


@router.get(
    "/checkpoint",
    summary="Ultimo checkpoint controfirmato (nota con le firme di registro e testimone)",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_checkpoint(
    request: Request,
    origine: Annotated[str | None, Query(max_length=255)] = None,
) -> PlainTextResponse:
    ctx = _contesto(request)
    riga = await _leggi_checkpoint(_origine(ctx, origine), None)
    return PlainTextResponse(riga.nota_controfirmata, media_type=TESTO)


@router.get(
    "/checkpoint/{dimensione}",
    summary="Checkpoint controfirmato a una dimensione",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_checkpoint_dimensione(
    request: Request,
    dimensione: Annotated[int, Path(ge=0, le=2**63 - 1)],
    origine: Annotated[str | None, Query(max_length=255)] = None,
) -> PlainTextResponse:
    ctx = _contesto(request)
    riga = await _leggi_checkpoint(_origine(ctx, origine), dimensione)
    return PlainTextResponse(riga.nota_controfirmata, media_type=TESTO)


class StatoTestimone(BaseModel):
    origine: str = Field(description="Origine del registro osservato (riga 1 del checkpoint)")
    stato: str = Field(description="ATTIVO oppure COMPROMESSO (fork rilevato: nessuna controfirma)")
    epoca: int = Field(description="Epoca corrente: cresce a ogni «nuova base» dell'operatore")
    dimensione: int | None = Field(description="Ultima dimensione controfirmata")
    radice: str | None = Field(description="Radice dell'ultimo checkpoint controfirmato (base64)")
    compromesso_dal: str | None = Field(description="Istante (UTC) del blocco, se compromesso")
    motivo: str | None = Field(description="Motivo del blocco")
    chiave_testimone: str = Field(description="Verifier key signed note del testimone")


@router.get(
    "/stato", summary="Stato del testimone e sua chiave pubblica", response_model=StatoTestimone
)
async def get_stato(
    request: Request,
    origine: Annotated[str | None, Query(max_length=255)] = None,
) -> StatoTestimone:
    ctx = _contesto(request)
    scelta = _origine(ctx, origine)
    try:
        async with get_async_session() as sessione:
            stato = (
                await sessione.execute(select(StatoRegistro).where(StatoRegistro.origine == scelta))
            ).scalar_one_or_none()
    except (SQLAlchemyError, DBNonInizializzato, OSError) as exc:
        raise errore_registrato("TST-503", exc, evento="lettura_stato") from None
    if stato is None:
        return StatoTestimone(
            origine=scelta,
            stato="ATTIVO",
            epoca=1,
            dimensione=None,
            radice=None,
            compromesso_dal=None,
            motivo=None,
            chiave_testimone=ctx.chiave.verificatore,
        )
    return StatoTestimone(
        origine=scelta,
        stato=stato.stato,
        epoca=stato.epoca,
        dimensione=stato.dimensione,
        radice=base64.b64encode(stato.radice).decode() if stato.radice else None,
        compromesso_dal=stato.compromesso_dal.isoformat() if stato.compromesso_dal else None,
        motivo=stato.motivo,
        chiave_testimone=ctx.chiave.verificatore,
    )


__all__ = ["autentica_registro", "router"]
