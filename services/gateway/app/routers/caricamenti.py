# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Sessioni di caricamento (contratto C1): API REST per i file grandi.

Servizio: gateway. Riferimenti: piano R03 §3.1; contratti dell'ondata B, C1;
REGOLE_SVILUPPO §7.4 (il router traduce, la logica è in
``app.services.caricamenti``), §14.4 (sessione di un altro ente = 404).

  POST   /api/v1/caricamenti                  apre (stessa Idempotency-Key → stessa sessione)
  PUT    /api/v1/caricamenti/{id}/parti/{n}   parte n (corpo binario; X-Impronta-Parte facoltativa)
  GET    /api/v1/caricamenti/{id}             stato, parti ricevute, impronte, formato, errore
  POST   /api/v1/caricamenti/{id}/completa    202 IN_VERIFICA (200 se già COMPLETO)
  DELETE /api/v1/caricamenti/{id}             204: annulla ed elimina i file

Autenticazione come il versamento (JWT o chiave API, scope ``versamento:ud``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import require_scope
from app.services import caricamenti as servizio
from app.services.versamento_service import IdentitaVersante, identita_da_utente
from libs.shared.errori import RISPOSTE_ERRORE

router = APIRouter(prefix="/caricamenti", tags=["Caricamenti"])
_versatore = require_scope("versamento:ud")


class Collegamenti(BaseModel):
    self: str
    parti: str = Field(..., description="Modello del percorso delle parti ({n} da 1)")
    completa: str


class SessioneCreata(BaseModel):
    caricamento_id: str
    stato: str
    dimensione_parte: int = Field(..., description="Byte di ogni parte (l'ultima: il resto)")
    numero_parti: int
    scade_il: datetime
    link: Collegamenti


class StatoCaricamento(BaseModel):
    caricamento_id: str
    stato: str = Field(..., description="APERTO, IN_VERIFICA, COMPLETO, SCADUTO, ANNULLATO, "
                                        "ERRORE")
    nome_file: str
    dimensione: int
    dimensione_parte: int
    numero_parti: int
    parti_ricevute: list[int]
    scade_il: datetime
    impronte: dict[str, str] | None = Field(
        None, description="Impronte calcolate dal servizio (SHA-256, SHA-512, SHA3-256)")
    formato_rilevato: str | None = Field(None, description="Formato riconosciuto dal contenuto")
    errore: dict[str, Any] | None = Field(None, description="application/problem+json se ERRORE")
    pdv_id: str | None = Field(None, description="PdV che ha usato la sessione")
    link: Collegamenti


class ParteAccettata(BaseModel):
    parte: int
    dimensione: int
    ricevute: list[int]


def _link(caricamento_id: Any) -> Collegamenti:
    base = f"/api/v1/caricamenti/{caricamento_id}"
    return Collegamenti(self=base, parti=f"{base}/parti/{{n}}", completa=f"{base}/completa")


def vista(s: servizio.Sessione, adesso: datetime) -> StatoCaricamento:
    """Stato pubblico della sessione (stessa vista per GET e completa)."""
    return StatoCaricamento(
        caricamento_id=str(s.id), stato=s.stato_effettivo(adesso), nome_file=s.nome_file,
        dimensione=s.dimensione, dimensione_parte=s.dimensione_parte,
        numero_parti=s.numero_parti, parti_ricevute=s.parti_ricevute, scade_il=s.scade_il,
        impronte=s.impronte, formato_rilevato=s.formato_rilevato, errore=s.errore,
        pdv_id=str(s.pdv_id) if s.pdv_id else None, link=_link(s.id),
    )


def _identita(user: dict) -> IdentitaVersante:
    """Ente, utente e canale dall'identità autenticata (contratto C2)."""
    return identita_da_utente(user, servizio.canale_da_utente(user))


@router.post("", status_code=201, response_model=SessioneCreata,
             summary="Apre una sessione di caricamento",
             responses={200: {"model": SessioneCreata,
                              "description": "Stessa Idempotency-Key: la sessione esistente"},
                        **RISPOSTE_ERRORE})
async def apri_caricamento(
    corpo: servizio.AperturaCaricamento,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    user: dict = Depends(_versatore),
) -> JSONResponse:
    """Apre una sessione per un file fino a ``CARICAMENTI_MAX_DIMENSIONE_MB`` (5 GiB):
    la risposta indica dimensione e numero delle parti da inviare."""
    adesso = datetime.now(UTC)
    sessione, nuova = await servizio.apri(_identita(user), corpo, idempotency_key=idempotency_key,
                                          adesso=adesso)
    risposta = SessioneCreata(
        caricamento_id=str(sessione.id), stato=sessione.stato_effettivo(adesso),
        dimensione_parte=sessione.dimensione_parte, numero_parti=sessione.numero_parti,
        scade_il=sessione.scade_il, link=_link(sessione.id))
    intestazioni = {"Location": f"/api/v1/caricamenti/{sessione.id}"}
    if not nuova:
        intestazioni["Idempotent-Replayed"] = "true"
    return JSONResponse(risposta.model_dump(mode="json"), status_code=201 if nuova else 200,
                        headers=intestazioni)


@router.put(
    "/{caricamento_id}/parti/{numero}", response_model=ParteAccettata,
    summary="Invia una parte (corpo binario)",
    responses=RISPOSTE_ERRORE,
    openapi_extra={"requestBody": {"required": True, "content": {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def invia_parte(
    caricamento_id: str,
    numero: int,
    request: Request,
    x_impronta_parte: Annotated[str | None, Header(alias="X-Impronta-Parte")] = None,
    user: dict = Depends(_versatore),
) -> ParteAccettata:
    """Le parti si inviano in parallelo e in qualsiasi ordine; ripetere una parte
    con lo stesso contenuto è idempotente (200), con contenuto diverso è 409 UPL-004."""
    impronta = servizio.impronta_parte_dichiarata(x_impronta_parte)
    lunghezza = request.headers.get("content-length")
    parte = await servizio.ricevi_parte(
        _identita(user).tenant_id, caricamento_id, numero, request.stream(),
        adesso=datetime.now(UTC),
        lunghezza_annunciata=int(lunghezza) if lunghezza and lunghezza.isdigit() else None,
        impronta_dichiarata=impronta)
    return ParteAccettata(parte=parte.numero, dimensione=parte.dimensione, ricevute=parte.ricevute)


@router.get("/{caricamento_id}", response_model=StatoCaricamento,
            summary="Stato della sessione di caricamento", responses=RISPOSTE_ERRORE)
async def stato_caricamento(caricamento_id: str,
                            user: dict = Depends(_versatore)) -> StatoCaricamento:
    sessione = await servizio.leggi(_identita(user).tenant_id, caricamento_id)
    return vista(sessione, datetime.now(UTC))


@router.post("/{caricamento_id}/completa", status_code=202, response_model=StatoCaricamento,
             summary="Chiede la verifica del file (fuori dalla richiesta)",
             responses={200: {"model": StatoCaricamento,
                              "description": "Sessione già verificata (COMPLETO)"},
                        **RISPOSTE_ERRORE})
async def completa_caricamento(caricamento_id: str,
                               user: dict = Depends(_versatore)) -> JSONResponse:
    """Tutte le parti devono essere ricevute (409 UPL-005 con l'elenco delle mancanti).
    Lo stato diventa IN_VERIFICA; la verifica (assemblaggio, impronte, formato) gira
    fuori dalla richiesta: seguire con GET finché lo stato è COMPLETO o ERRORE."""
    adesso = datetime.now(UTC)
    sessione = await servizio.completa(_identita(user).tenant_id, caricamento_id, adesso=adesso)
    corpo = vista(sessione, adesso)
    return JSONResponse(corpo.model_dump(mode="json"),
                        status_code=200 if corpo.stato == servizio.COMPLETO else 202)


@router.delete("/{caricamento_id}", status_code=204, response_class=Response,
               summary="Annulla la sessione ed elimina i file", responses=RISPOSTE_ERRORE)
async def annulla_caricamento(caricamento_id: str,
                              user: dict = Depends(_versatore)) -> Response:
    await servizio.annulla(_identita(user).tenant_id, caricamento_id, adesso=datetime.now(UTC))
    return Response(status_code=204)
