# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/routers/personale.py — firma PERSONALE con OTP (R16, VD-C-166).

  POST /api/v1/signature/personale/otp     invio dell'OTP al titolare (credentials/sendOTP)
  POST /api/v1/signature/personale/firma   firma CAdES (.p7m con il contenuto) con l'OTP

Solo il token UTENTE di una persona di un ruolo fisso del conservatore, con accesso al
portale (mai chiave API, token di servizio o ruolo personalizzato) e con una credenziale
personale attiva; la credenziale si legge dal database per il ``user_id`` del token, mai
dalla richiesta. Ogni operazione riserva un tentativo per persona (Redis, atomico) PRIMA di
contattare il prestatore; una firma riuscita restituisce il tentativo. La busta è
verificata (R08) prima di restituirla e porta il certificato ATTESO dalla credenziale
(controllato due volte: identità dell'adattatore e confronto finale). OTP e PIN non vanno
mai nei log. Lo chiama dashboard-api inoltrando il token della persona.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.config import settings
from app.routers.signature import _bearer, gestore
from app.services.fornitori import ErroreFornitore, FornitoreNonRegistrato
from app.services.limiti import restituisci_personale, riserva_personale
from app.services.personale import (
    CredenzialeAssente,
    CredenzialePersonale,
    errore_di_autorizzazione,
    leggi_credenziale,
)
from app.services.registro_fornitori import parametri_firma
from app.services.sviluppo import adesso_utc
from libs.shared.errori import ErroreApi

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/signature/personale", tags=["Firma personale"])


def responsabile(authorization: str | None) -> dict[str, Any]:
    """Persona di un ruolo FISSO del conservatore con accesso al portale."""
    from app.auth.jwt_handler import verify_access_token
    from libs.verumdoc_rules.permessi import RUOLI_CONSERVATORE

    payload = verify_access_token(_bearer(authorization))
    try:
        uuid.UUID(str(payload.get("user_id")))
    except ValueError:
        raise ErroreApi("FIRMA-403-PERSONALE") from None
    if payload.get("ruolo") not in RUOLI_CONSERVATORE or payload.get("rp") \
            or payload.get("auth_method") == "api_key":
        raise ErroreApi("FIRMA-403-PERSONALE")
    return payload


async def _credenziale(user_id: str) -> CredenzialePersonale:
    try:
        return await leggi_credenziale(user_id)
    except CredenzialeAssente:
        raise ErroreApi("FIRMA-404-CREDENZIALE") from None


class FirmaPersonaleRequest(BaseModel):
    contenuto_b64: str = Field(description="Documento da firmare, base64 (CAdES con contenuto)")
    impronta_sha256_hex: str = Field(min_length=64, max_length=64,
                                     description="Impronta attesa del documento")
    otp: str = Field(min_length=4, max_length=16, description="Codice monouso del prestatore")
    pin: str = Field("", max_length=64, description="PIN, se la credenziale lo chiede")
    documento_id: str = Field("", max_length=64, description="Riferimento per il log")


def _contenuto(req: FirmaPersonaleRequest) -> bytes:
    try:
        dati = base64.b64decode(req.contenuto_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ErroreApi("FIRMA-422-RICHIESTA", "contenuto_b64 non è base64 valido") from None
    if not dati or len(dati) > settings.max_dimensione_byte:
        raise ErroreApi("FIRMA-422-RICHIESTA", "contenuto vuoto o troppo grande")
    if hashlib.sha256(dati).hexdigest() != req.impronta_sha256_hex.lower():
        raise ErroreApi("FIRMA-422-RICHIESTA", "impronta diversa dal contenuto")
    return dati


@router.post("/otp", summary="Invio dell'OTP al titolare della credenziale personale")
async def invia_otp(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    payload = responsabile(authorization)
    user_id = str(payload["user_id"])
    credenziale = await _credenziale(user_id)
    await riserva_personale(user_id, "otp")
    try:
        _, personale = await gestore().personale(credenziale.fornitore_codice,
                                                 credenziale.credential_id,
                                                 credenziale.certificato_sha256)
        await personale.invia_otp()
    except FornitoreNonRegistrato:
        raise ErroreApi("FIRMA-404-CREDENZIALE",
                        "Il fornitore della credenziale personale non è attivo") from None
    logger.info("firma_personale_otp_inviato", utente=user_id[:8],
                fornitore=credenziale.fornitore_codice)
    return {"inviato": True, "fornitore": credenziale.fornitore_codice}


@router.post("/firma", summary="Firma personale CAdES con OTP, verificata")
async def firma_personale(req: FirmaPersonaleRequest,
                          authorization: str | None = Header(default=None)) -> dict[str, Any]:
    payload = responsabile(authorization)
    user_id = str(payload["user_id"])
    contenuto = _contenuto(req)
    credenziale = await _credenziale(user_id)
    await riserva_personale(user_id, "firma")
    valori = await parametri_firma()
    try:
        servizio, _ = await gestore().personale(
            credenziale.fornitore_codice, credenziale.credential_id,
            credenziale.certificato_sha256, otp=req.otp, pin=req.pin)
        livello = valori["livello"] if servizio.fornitore_marca else "B-B"
        esito = await servizio.firma(contenuto, formato="cades", livello=livello,
                                     algoritmo=valori["algoritmo"], adesso=adesso_utc())
    except FornitoreNonRegistrato:
        raise ErroreApi("FIRMA-404-CREDENZIALE",
                        "Il fornitore della credenziale personale non è attivo") from None
    except ErroreFornitore as exc:
        if errore_di_autorizzazione(exc):
            logger.warning("firma_personale_otp_rifiutato", utente=user_id[:8])
            raise ErroreApi("FIRMA-422-OTP") from None
        raise
    # secondo controllo, indipendente dall'identità dell'adattatore: il certificato della
    # busta è quello della credenziale della persona (mai il sigillo del conservatore)
    if esito.certificato_sha256 != credenziale.certificato_sha256 or not esito.verificata:
        logger.error("firma_personale_certificato_inatteso", utente=user_id[:8])
        raise ErroreApi("FIRMA-502-FIRMA-NON-VALIDA",
                        "La firma restituita non porta il certificato della credenziale")
    await restituisci_personale(user_id, "firma")
    logger.info("firma_personale_apposta", utente=user_id[:8],
                documento=req.documento_id[:40], livello=esito.livello,
                impronta=esito.impronta_sha256_contenuto[:16])
    return {"busta_b64": base64.b64encode(esito.busta).decode("ascii"),
            "livello": esito.livello, "qualificata": esito.qualificata,
            "verificata": esito.verificata, "firmatario": esito.firmatario,
            "certificato_sha256": esito.certificato_sha256,
            "certificato_scadenza": esito.certificato_scadenza,
            "data_marca": esito.data_marca, "credenziale_id": credenziale.id,
            "impronta_sha256_contenuto": esito.impronta_sha256_contenuto,
            "avvisi": esito.avvisi}


__all__ = ["responsabile", "router"]
