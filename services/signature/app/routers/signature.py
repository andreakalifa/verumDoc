# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/routers/signature.py
API di firma, marca e verifica — VD-C-010.

Ogni risposta dichiara fornitore e qualificazione: la firma dell'adattatore di
sviluppo porta sempre `qualificata=false` e l'avviso esplicito. Gli errori di
dominio escono come {"codice", "messaggio", "dettagli"} (REGOLE §18.1).
Tutti gli endpoint richiedono un JWT RS256 verificato (REGOLE §19.1).
Revisione di sicurezza M2: /firma e /marca (che usano il certificato del
conservatore) accettano SOLO il token di servizio di hashchain (audience
«verumdoc-signature», scope «firma:servizio»); un token utente riceve 403.

R13a: il servizio di firma nasce dal registro dei fornitori (console) tramite
``GestoreConfigurazione``; livello e algoritmo, se la richiesta non li indica,
vengono dai parametri del motore. ``/rimarca`` (token di servizio) rifà la marca
d'archivio. ``/fornitori`` e ``/fornitori/{codice}/prova`` (stato, scadenze,
firma di prova) sono SOLO per i ruoli fissi del conservatore con il permesso mai
delegabile ``firma_fornitori:gestisci`` (mai un token di servizio né un ruolo
personalizzato); le prove sono limitate con una riserva atomica condivisa fra le
repliche (R16, VD-C-168).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.firma_cms import LIVELLI, ServizioFirma
from app.services.registro import stato_servizio
from app.services.registro_fornitori import GestoreConfigurazione, parametri_firma
from app.services.sviluppo import adesso_utc
from libs.shared.errori import ErroreApi

router = APIRouter(prefix="/api/v1/signature", tags=["Signature"])

_servizio: ServizioFirma | None = None
_gestore: GestoreConfigurazione | None = None


def gestore() -> GestoreConfigurazione:
    global _gestore
    if _gestore is None:
        _gestore = GestoreConfigurazione(settings)
    return _gestore


def imposta_gestore(nuovo: GestoreConfigurazione | None) -> None:
    """Sostituisce il gestore del registro (avvio e test)."""
    global _gestore
    _gestore = nuovo


async def get_servizio() -> ServizioFirma:
    """ServizioFirma dal registro dei fornitori (ricostruito quando cambia) o, nei
    test, quello impostato con ``reimposta_servizio``.

    In produzione con adattatore di sviluppo solleva FornitoreVietatoInProduzione
    (gestito come 409): il servizio rifiuta di firmare.
    """
    if _servizio is not None:
        return _servizio
    return await gestore().servizio()


def reimposta_servizio(servizio: ServizioFirma | None = None) -> None:
    """Sostituisce il servizio (test); None = di nuovo dal registro (gestore nuovo)."""
    global _servizio, _gestore
    _servizio = servizio
    if servizio is None:
        _gestore = None


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, {"codice": "SIG-401", "messaggio": "Token JWT obbligatorio"})
    return authorization.split(" ", 1)[1].strip()


def _sig_auth(authorization: str | None) -> dict:
    """Verifica e stato: token utente RS256 verificato (aud «verumdoc-api») oppure
    token di servizio (aud «verumdoc-signature»)."""
    from app.auth.jwt_handler import verify_access_token, verify_service_token

    token = _bearer(authorization)
    try:
        return verify_access_token(token)
    except HTTPException as errore_utente:
        try:
            return verify_service_token(token)
        except HTTPException:
            raise errore_utente from None


def _sig_auth_servizio(authorization: str | None) -> dict:
    """Firma e marca (revisione di sicurezza M2): SOLO il token di servizio con
    scope «firma:servizio». Un token utente, di qualunque ruolo o ente, riceve
    403: il certificato del conservatore non firma contenuti arbitrari."""
    from app.auth.jwt_handler import verify_access_token, verify_service_token

    token = _bearer(authorization)
    try:
        return verify_service_token(token)   # 403 se manca lo scope
    except HTTPException as errore:
        if errore.status_code != 401:
            raise
        try:
            verify_access_token(token)
        except HTTPException:
            raise errore from None
    # Token utente valido: autenticato ma mai autorizzato a firmare/marcare
    raise HTTPException(403, {"codice": "SIG-403",
                              "messaggio": "Operazione riservata al token di servizio"})


def _b64(valore: str, campo: str) -> bytes:
    try:
        dati = base64.b64decode(valore, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            422, {"codice": "SIG-422-BASE64", "messaggio": f"{campo} non è base64 valido"}
        ) from exc
    if len(dati) > settings.max_dimensione_byte:
        raise HTTPException(
            413,
            {
                "codice": "SIG-413",
                "messaggio": f"{campo} supera {settings.max_dimensione_byte} byte",
            },
        )
    return dati


def _impronta(impronta_hex: str | None, contenuto_b64: str | None) -> bytes:
    if impronta_hex:
        try:
            dati = bytes.fromhex(impronta_hex)
        except ValueError as exc:
            raise HTTPException(
                422,
                {"codice": "SIG-422-IMPRONTA", "messaggio": "impronta_sha256_hex non esadecimale"},
            ) from exc
        if len(dati) != 32:
            raise HTTPException(
                422,
                {"codice": "SIG-422-IMPRONTA", "messaggio": "impronta SHA-256 di 32 byte attesa"},
            )
        return dati
    if contenuto_b64:
        return hashlib.sha256(_b64(contenuto_b64, "contenuto_b64")).digest()
    raise HTTPException(
        422,
        {"codice": "SIG-422-IMPRONTA", "messaggio": "indicare impronta_sha256_hex o contenuto_b64"},
    )


# ── Modelli ───────────────────────────────────────────────────────────


class FirmaRequest(BaseModel):
    content_b64: str = Field(description="Contenuto da firmare, base64")
    formato: Literal["cades", "cades_detached", "pades"] = Field(
        "cades",
        description="cades = .p7m con contenuto (ETSI EN 319 122); "
        "cades_detached = firma separata; pades = PDF firmato (EN 319 142)",
    )
    con_marca: bool = Field(
        True, description="Aggiunge la marca RFC 3161 sulla firma (livello B-T)"
    )
    livello: Literal["B-B", "B-T", "B-LT", "B-LTA"] | None = Field(
        None, description="Livello ETSI; vuoto = parametro FIRMA_LIVELLO_DOCUMENTI "
        "(o B-B se con_marca è falso)")
    algoritmo: Literal["sha256", "sha384", "sha512"] | None = Field(
        None, description="Impronta; vuoto = parametro FIRMA_ALGORITMO_IMPRONTA")


class FirmaResponse(BaseModel):
    busta_b64: str
    formato: str
    contenuto_incluso: bool
    livello: str
    fornitore_firma: str
    fornitore_marca: str | None
    qualificata: bool = Field(
        description="False per l'adattatore di sviluppo e per certificati senza QcCompliance"
    )
    firmatario: str
    data_marca: str | None
    impronta_sha256_contenuto: str
    avvisi: list[str]
    codice_fornitore: str = ""
    algoritmo: str = "sha256"
    certificato_sha256: str = ""
    certificato_scadenza: str | None = None
    marca_scadenza: str | None = None
    verificata: bool = Field(False, description="Firma verificata prima di restituirla")


class RimarcaRequest(BaseModel):
    busta_b64: str = Field(description="Busta CAdES (.p7m) o PDF firmato, base64")
    contenuto_b64: str | None = Field(None, description="Contenuto (firme staccate)")
    algoritmo: Literal["sha256", "sha384", "sha512"] | None = None


class MarcaRequest(BaseModel):
    impronta_sha256_hex: str | None = None
    contenuto_b64: str | None = None


class VerificaRequest(BaseModel):
    busta_b64: str = Field(description="Busta CAdES (.p7m DER/PEM/base64) o PDF firmato, base64")
    contenuto_b64: str | None = Field(
        None, description="Contenuto originale (obbligatorio per firme detached)"
    )


class VerificaMarcaRequest(BaseModel):
    token_b64: str
    impronta_sha256_hex: str | None = None
    contenuto_b64: str | None = None


# ── Endpoint ──────────────────────────────────────────────────────────


@router.post("/firma", summary="Firma CAdES/PAdES con marca RFC 3161", response_model=FirmaResponse)
async def firma(
    req: FirmaRequest,
    authorization: str | None = Header(default=None),
    servizio: ServizioFirma = Depends(get_servizio),
) -> FirmaResponse:
    _sig_auth_servizio(authorization)
    contenuto = _b64(req.content_b64, "content_b64")
    livello, algoritmo = req.livello, req.algoritmo
    if livello is None or algoritmo is None:
        valori = await parametri_firma()
        livello = livello or (valori["livello"] if req.con_marca else "B-B")
        algoritmo = algoritmo or valori["algoritmo"]
    esito = await servizio.firma(
        contenuto, formato=req.formato, con_marca=req.con_marca, adesso=adesso_utc(),
        livello=livello, algoritmo=algoritmo,
    )
    return _risposta(esito)


def _risposta(esito: Any) -> FirmaResponse:
    return FirmaResponse(
        busta_b64=base64.b64encode(esito.busta).decode("ascii"),
        formato=esito.formato,
        contenuto_incluso=esito.contenuto_incluso,
        livello=esito.livello,
        fornitore_firma=esito.fornitore_firma,
        fornitore_marca=esito.fornitore_marca,
        qualificata=esito.qualificata,
        firmatario=esito.firmatario,
        data_marca=esito.data_marca,
        impronta_sha256_contenuto=esito.impronta_sha256_contenuto,
        avvisi=esito.avvisi,
        codice_fornitore=esito.codice_fornitore,
        algoritmo=esito.algoritmo,
        certificato_sha256=esito.certificato_sha256,
        certificato_scadenza=esito.certificato_scadenza,
        marca_scadenza=esito.marca_scadenza,
        verificata=esito.verificata,
    )


@router.post("/rimarca", summary="Nuova marca d'archivio su una busta firmata (B-LTA)",
             response_model=FirmaResponse)
async def rimarca(
    req: RimarcaRequest,
    authorization: str | None = Header(default=None),
    servizio: ServizioFirma = Depends(get_servizio),
) -> FirmaResponse:
    _sig_auth_servizio(authorization)
    busta = _b64(req.busta_b64, "busta_b64")
    contenuto = _b64(req.contenuto_b64, "contenuto_b64") if req.contenuto_b64 else None
    algoritmo = req.algoritmo or (await parametri_firma())["algoritmo"]
    return _risposta(await servizio.rimarca(busta, contenuto, adesso=adesso_utc(),
                                            algoritmo=algoritmo))


@router.post("/marca", summary="Marca temporale RFC 3161 su un'impronta SHA-256")
async def marca(
    req: MarcaRequest,
    authorization: str | None = Header(default=None),
    servizio: ServizioFirma = Depends(get_servizio),
) -> dict:
    _sig_auth_servizio(authorization)
    impronta = _impronta(req.impronta_sha256_hex, req.contenuto_b64)
    token, esito = await servizio.marca(impronta)
    return {
        "token_b64": base64.b64encode(token.dump()).decode("ascii"),
        "fornitore_marca": servizio.fornitore_marca.nome if servizio.fornitore_marca else None,
        "verifica": esito,
        "avviso": (
            "MARCA DI SVILUPPO NON QUALIFICATA: nessun valore legale"
            if esito.get("di_sviluppo")
            else None
        ),
    }


@router.post("/verifica", summary="Verifica crittografica di una firma CAdES/PAdES")
async def verifica(
    req: VerificaRequest,
    authorization: str | None = Header(default=None),
    servizio: ServizioFirma = Depends(get_servizio),
) -> dict:
    _sig_auth(authorization)
    busta = _b64(req.busta_b64, "busta_b64")
    contenuto = _b64(req.contenuto_b64, "contenuto_b64") if req.contenuto_b64 else None
    return await servizio.verifica(busta, contenuto)


@router.post("/marca/verifica", summary="Verifica di un token di marca RFC 3161")
async def verifica_marca(
    req: VerificaMarcaRequest,
    authorization: str | None = Header(default=None),
    servizio: ServizioFirma = Depends(get_servizio),
) -> dict:
    _sig_auth(authorization)
    impronta = _impronta(req.impronta_sha256_hex, req.contenuto_b64)
    return await servizio.verifica_marca(_b64(req.token_b64, "token_b64"), impronta)


@router.get("/status", summary="Stato onesto di firma e marca")
async def status(
    authorization: str | None = Header(default=None),
    servizio: ServizioFirma = Depends(get_servizio),
) -> dict:
    _sig_auth(authorization)
    return await stato_servizio(servizio, settings)


# ── R13a: fornitori (console del conservatore) ─────────────────────────

def _conservatore_fornitori(authorization: str | None) -> dict:
    """Solo token UTENTE di un ruolo fisso del conservatore con il permesso mai
    delegabile ``firma_fornitori:gestisci`` (mai un token di servizio)."""
    from app.auth.jwt_handler import verify_access_token
    from libs.shared.security.permessi import ha_permesso
    from libs.verumdoc_rules.permessi import FIRMA_FORNITORI_GESTISCI, RUOLI_CONSERVATORE

    payload = verify_access_token(_bearer(authorization))
    if payload.get("ruolo") not in RUOLI_CONSERVATORE or not ha_permesso(
            payload, FIRMA_FORNITORI_GESTISCI):
        raise ErroreApi("FIRMA-403-FORNITORI",
                        "I fornitori di firma e marca li gestisce il conservatore")
    return payload


async def _certificato(fornitore: Any) -> dict:
    try:
        mat = await asyncio.wait_for(fornitore.materiale(), timeout=15)
    except Exception as exc:  # noqa: BLE001 — stato del fornitore, non errore dell'API
        return {"raggiungibile": False,
                "errore": str(getattr(exc, "messaggio", type(exc).__name__))[:300]}
    from datetime import UTC, datetime

    cert = mat.certificato
    scade = cert["tbs_certificate"]["validity"]["not_after"].native
    return {
        "raggiungibile": True,
        "soggetto": cert.subject.human_friendly,
        "emittente": cert.issuer.human_friendly,
        "numero_serie": format(cert.serial_number, "x"),
        "valido_dal": cert["tbs_certificate"]["validity"]["not_before"].native.isoformat(),
        "valido_al": scade.isoformat(),
        "giorni_alla_scadenza": (scade - datetime.now(UTC)).days,
        "dichiara_qualificato": mat.qualificato,
        "impronta_sha256": hashlib.sha256(cert.dump()).hexdigest(),
        "dettagli": dict(getattr(fornitore, "info", {}) or {}),
    }


@router.get("/fornitori", summary="Stato dei fornitori di firma e marca registrati")
async def fornitori(authorization: str | None = Header(default=None)) -> dict:
    _conservatore_fornitori(authorization)
    from libs.shared.firma_qualificata.configurazione import leggi_segreti, segreti_presenti

    esito = await gestore().esito()
    servizio = esito.servizio
    adattatori: dict[str, Any] = {f.codice: f for f in servizio.fornitori_firma if f.codice}
    marca = servizio.fornitore_marca
    for m in (getattr(marca, "fornitori", None) or []):
        if m.codice:
            adattatori[m.codice] = m
    voci = []
    for r in esito.registrati:
        try:
            presenti = segreti_presenti(r.tipo, leggi_segreti(
                settings.firma_segreti_json.get_secret_value(), r.codice))
        except ValueError:
            presenti = {}
        voce: dict[str, Any] = {
            "codice": r.codice, "ruolo": r.ruolo, "tipo": r.tipo, "nome": r.nome,
            "priorita": r.priorita, "costruito": r.codice in adattatori,
            "problemi": esito.problemi.get(r.codice, []), "segreti_presenti": presenti,
            "vietato_in_produzione": r.tipo == "SVILUPPO" and settings.in_produzione,
        }
        a = adattatori.get(r.codice)
        if a is not None and r.ruolo == "FIRMA":
            voce["certificato"] = await _certificato(a)
        elif a is not None:
            voce["tsa"] = {"host": getattr(getattr(a, "cliente", None), "host", None)}
        voci.append(voce)
    stato = await stato_servizio(servizio, settings)
    return {"origine": servizio.configurazione.get("origine", "ambiente"),
            "ultima_lettura": gestore().ultima_lettura.isoformat()
            if gestore().ultima_lettura else None,
            "errore_lettura": gestore().ultimo_errore,
            "fornitori": voci, "production_ready": stato["production_ready"],
            "parametri": await parametri_firma()}


@router.post("/fornitori/{codice}/prova",
             summary="Prova di connessione con firma (o marca) di prova verificata")
async def prova(codice: str, authorization: str | None = Header(default=None)) -> dict:
    payload = _conservatore_fornitori(authorization)
    # R16 (VD-C-168): riserva atomica condivisa fra le repliche (Redis), PRIMA della prova
    from app.services.limiti import riserva_prova

    await riserva_prova()
    servizio = await get_servizio()
    contenuto = (f"Prova di connessione VerumDoc {adesso_utc().isoformat()} "
                 f"{os.urandom(8).hex()}").encode()
    inizio = time.monotonic()
    firma_ok = servizio.fornitore(codice)
    if firma_ok is not None:
        esito = await servizio.firma(contenuto, formato="cades_detached",
                                     livello="B-T" if servizio.fornitore_marca else "B-B",
                                     adesso=adesso_utc(), solo_fornitore=codice)
        risultato = {"ruolo": "FIRMA", "firmatario": esito.firmatario,
                     "qualificata": esito.qualificata, "livello": esito.livello,
                     "verificata": esito.verificata, "data_marca": esito.data_marca,
                     "certificato_scadenza": esito.certificato_scadenza,
                     "avvisi": esito.avvisi}
    else:
        marche = [m for m in (getattr(servizio.fornitore_marca, "fornitori", None) or [])
                  if m.codice == codice]
        if not marche:
            from app.services.fornitori import FornitoreNonRegistrato

            raise FornitoreNonRegistrato(f"fornitore {codice} non attivo o non costruito")
        from app.services.tsa_client import verifica_token

        impronta = hashlib.sha256(contenuto).digest()
        token = await marche[0].marca(impronta, "sha256")
        verifica = await verifica_token(token, impronta, servizio._radici_token())
        risultato = {"ruolo": "MARCA", "verificata": bool(verifica.get("valida")),
                     "data_marca": verifica.get("data_marca"),
                     "dichiarata_qualificata": verifica.get("dichiarata_qualificata"),
                     "certificato_tsa_scadenza": verifica.get("certificato_tsa_scadenza")}
    import structlog

    structlog.get_logger(__name__).info("fornitore_firma_provato", codice=codice,
                                        utente=str(payload.get("user_id") or "")[:40])
    return {"codice": codice, "esito": "OK", "durata_ms": int((time.monotonic() - inizio) * 1000),
            **risultato}


__all__ = ["LIVELLI", "gestore", "get_servizio", "imposta_gestore", "reimposta_servizio",
           "router"]
