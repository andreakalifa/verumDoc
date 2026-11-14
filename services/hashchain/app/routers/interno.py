# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
API INTERNA del registro delle impronte per il ciclo di vita (R05, contratto Q3).

Servizio: hashchain, prefisso ``/interno/v1/registro``
(revisione 2 di R05, S2:
FUORI da ``/api/``, quindi da nessuna rotta dell'ingress; ``verifica_manifest``
lo impone e le richieste arrivate dall'ALB, con ``X-Forwarded-For``, si
rifiutano con 404; la NetworkPolicy della hashchain ammette il solo lavoratore
di ciclo-vita fra i servizi chiamanti; non pubblicata nell'OpenAPI). Solo token di
SERVIZIO (audience ``CATENA_PUBBLICO_SERVIZIO`` = ``verumdoc-registro``) con lo
scope ``CATENA_SCOPE_AGGIUNTA_FOGLIE`` = ``registro:aggiungi`` (emesso con
``python -m libs.shared.security.emetti_token_servizio --servizio ciclo-vita
--pubblico verumdoc-registro --scope registro:aggiungi``). Nessun token utente,
nessun ruolo: è il servizio ``ciclo-vita`` a decidere chi può avviare ed
eseguire le pratiche (contratto Q2); qui si registra il fatto nella catena.

- ``POST /foglie`` ``{tipo: scarto|annullamento|rettifica, pda: urn,
  riferimento: {pratica, ...}, impronta_documento_pratica: hex}`` → 202
  ``{foglia_id}``. Solo se il fatto RISULTA dal database (revisione 2 di R05,
  S2, ``interne.riscontro_ciclo_vita``), altrimenti 409 ``CAT-409-RISCONTRO``.
  Idempotente per ``(tipo, pda, riferimento.pratica)``: la
  stessa richiesta restituisce la stessa foglia; la stessa chiave con dati
  diversi → 409 ``IDEM-409``. PdA inesistente → 404 ``CAT-404``.
- ``GET /foglie/{id}`` → stato (IN_CODA, NEL_REGISTRO, IN_CHECKPOINT,
  CONTROFIRMATA) con indice e checkpoint quando ci sono; 404 ``CAT-404``.
Errori: 401 senza token valido, 403 ``CAT-403`` senza lo scope, 422 ``VAL-422``,
503 ``CAT-503``.

Ambito del database (R05, VD-C-080): un token di servizio non ha ente; il
servizio chiamante opera su PdA di tutti gli enti, quindi le transazioni di
questa API usano ``ambito_conservatore`` (solo DOPO la verifica dello scope).
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import chiavi_jwt
from app.config import settings
from app.registro import interne
from libs.catena.foglie import FogliaNonValida, controlla_riferimento
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_conservatore
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.security.jwt_rs256 import ErroreTokenNonValido, verifica_token_servizio

logger = structlog.get_logger(__name__)
PREFISSO = "/interno/v1/registro"


async def solo_traffico_interno(request: Request) -> None:
    """Difesa in profondità (S2): l'ALB aggiunge sempre ``X-Forwarded-For``;
    una richiesta che lo porta è arrivata dall'esterno → 404 (l'indirizzo non
    dice che l'API esiste)."""
    if request.headers.get("x-forwarded-for") is not None:
        raise ErroreApi("CAT-404", "Risorsa non trovata")


router = APIRouter(prefix=PREFISSO, tags=["Registro: API interna"], include_in_schema=False,
                   dependencies=[Depends(solo_traffico_interno)])

_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
MOTIVO_AMBITO = "API interna del registro: foglie del ciclo di vita (servizio ciclo-vita)"


async def servizio_aggiunta(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Solo token di servizio del registro con lo scope di aggiunta delle foglie."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ErroreApi("AUTH-001", "Token di servizio richiesto (Authorization: Bearer)",
                        headers={"WWW-Authenticate": "Bearer"})
    try:
        servizio = verifica_token_servizio(
            authorization.split(" ", 1)[1].strip(), chiave_pubblica_pem=chiavi_jwt().pubblica_pem,
            emittente=settings.jwt_issuer, pubblico=settings.catena_pubblico_servizio)
    except ErroreTokenNonValido as exc:
        raise ErroreApi(exc.codice, exc.dettaglio or None,
                        headers={"WWW-Authenticate": "Bearer"}) from None
    if settings.catena_scope_aggiunta_foglie not in (servizio.get("scopes") or []):
        raise ErroreApi("CAT-403", "Token di servizio senza lo scope "
                                   f"{settings.catena_scope_aggiunta_foglie}",
                        titolo="Aggiunta di foglie al registro non autorizzata")
    return servizio


class RichiestaFoglia(BaseModel):
    """Corpo di ``POST /foglie`` (contratto Q3)."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["scarto", "annullamento", "rettifica", "chiusura_eccezioni", "minimizzazione",
                  "rinnovo_retention"]
    pda: str = Field(..., min_length=1, max_length=500, description="URN del PdA")
    riferimento: dict[str, Any] = Field(
        ..., description="Riferimenti della pratica: «pratica» obbligatoria (chiave "
                         "d'idempotenza con tipo e PdA); altre chiavi piatte (testo, interi, "
                         "booleani, null), al più 16")
    impronta_documento_pratica: str = Field(
        ..., description="SHA-256 (hex) del documento della pratica (verbale, atto)")

    @field_validator("riferimento")
    @classmethod
    def _riferimento(cls, valore: dict[str, Any]) -> dict[str, Any]:
        try:
            return controlla_riferimento(valore)
        except FogliaNonValida as exc:
            raise ValueError(str(exc)) from None

    @field_validator("impronta_documento_pratica")
    @classmethod
    def _impronta(cls, valore: str) -> str:
        if not _HEX64.fullmatch(valore):
            raise ValueError("atteso SHA-256 esadecimale (64 cifre)")
        return valore.lower()


class RispostaFoglia(BaseModel):
    foglia_id: str


class CheckpointFoglia(BaseModel):
    dimensione: int
    emesso_il: str
    stato_sigillo: str
    stato_testimone: str


class StatoFoglia(BaseModel):
    foglia_id: str
    tipo: str
    pda: str | None
    riferimento: dict[str, Any]
    impronta_documento_pratica: str | None
    foglia_dati: str = Field(..., description="Foglia di formato 2 (JSON canonico)")
    richiesta_il: str
    stato: Literal["IN_CODA", "NEL_REGISTRO", "IN_CHECKPOINT", "CONTROFIRMATA"]
    indice: int | None
    sequenziata_il: str | None
    checkpoint: CheckpointFoglia | None = Field(
        None, description="Primo checkpoint emesso che copre la foglia")
    checkpoint_controfirmato: CheckpointFoglia | None = Field(
        None, description="Primo checkpoint controfirmato dal testimone che copre la foglia")


@router.post("/foglie", status_code=202, response_model=RispostaFoglia,
             summary="Aggiunge una foglia del ciclo di vita al registro (idempotente)")
async def aggiungi_foglia(
    richiesta: RichiestaFoglia, servizio: dict = Depends(servizio_aggiunta),
) -> JSONResponse:
    """La foglia entra in coda al sequenziatore con lo stesso trattamento delle
    foglie dei PdA (indice, checkpoint, sigillo, controfirma)."""
    chiamante = str(servizio.get("servizio") or servizio.get("sub") or "servizio")
    try:
        with ambito_conservatore(MOTIVO_AMBITO, registra=False):
            async with get_async_session() as session:
                esito = await interne.richiedi_foglia_ciclo_vita(
                    session, tipo=richiesta.tipo, pda=richiesta.pda,
                    riferimento=richiesta.riferimento,
                    impronta=richiesta.impronta_documento_pratica,
                    richiesta_da=chiamante, adesso=datetime.now(UTC))
    except interne.PdaNonTrovato:
        raise ErroreApi("CAT-404", "PdA non trovato") from None
    except interne.RiscontroMancante as exc:
        logger.warning("foglia_interna_senza_riscontro", tipo=richiesta.tipo, pda=richiesta.pda,
                       servizio=chiamante, motivo=exc.motivo)
        raise ErroreApi("CAT-409-RISCONTRO", exc.motivo,
                        dettagli={"tipo": richiesta.tipo}) from None
    except interne.ConflittoFoglia as exc:
        raise ErroreApi("IDEM-409", "Foglia già richiesta per lo stesso tipo, PdA e pratica "
                                    "con dati diversi", dettagli={"foglia_id": exc.foglia_id},
                        titolo="Foglia già richiesta con dati diversi") from None
    except SQLAlchemyError as exc:
        raise errore_registrato("CAT-503", exc, evento="errore_foglia_interna") from None
    logger.info("foglia_interna_richiesta", foglia_id=esito.foglia_id, tipo=richiesta.tipo,
                nuova=esito.nuova, servizio=chiamante)
    return JSONResponse(status_code=202, content={"foglia_id": esito.foglia_id})


@router.get("/foglie/{foglia_id}", response_model=StatoFoglia,
            summary="Stato di una foglia interna (indice e checkpoint)")
async def leggi_foglia(
    foglia_id: uuid.UUID = Path(..., description="Id restituito dall'aggiunta"),
    _servizio: dict = Depends(servizio_aggiunta),
) -> dict[str, Any]:
    try:
        with ambito_conservatore(MOTIVO_AMBITO, registra=False):
            async with get_async_session() as session:
                stato = await interne.stato_foglia(session, foglia_id)
    except SQLAlchemyError as exc:
        raise errore_registrato("CAT-503", exc, evento="errore_foglia_interna") from None
    if stato is None:
        raise ErroreApi("CAT-404", "Foglia interna non trovata")
    return stato
