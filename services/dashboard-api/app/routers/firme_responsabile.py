# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/routers/firme_responsabile.py — firma PERSONALE con OTP dei
documenti del responsabile della conservazione (R16, VD-C-166, VD-P-140).

  GET  /api/dashboard/firme-responsabile                 documenti e stato della firma
  POST /api/dashboard/firme-responsabile                 accoda un documento (generico)
  POST /api/dashboard/firme-responsabile/documentazione/{versione_id}  (R17: HTML stampabile)
                                                         accoda manuale o piano generati
  GET  /api/dashboard/firme-responsabile/{id}/contenuto  documento da leggere prima di firmare
  POST /api/dashboard/firme-responsabile/{id}/otp        chiede l'OTP al prestatore
  POST /api/dashboard/firme-responsabile/{id}/firma      firma con l'OTP (e il PIN)
  POST /api/dashboard/firme-responsabile/{id}/annulla    annulla un documento da firmare
  GET  /api/dashboard/firme-responsabile/{id}/busta      .p7m firmato

Solo le persone dei ruoli fissi del conservatore con accesso al portale (mai chiave API né
ruolo personalizzato). OTP e firma si inoltrano al servizio di firma con il token della
persona, che lo riverifica e usa la SUA credenziale personale; la busta restituita si
controlla (impronta del contenuto incluso, certificato della credenziale) e si registra
FIRMATO nella stessa transazione della voce d'audit (il trigger della migrazione 057
ricontrolla busta, credenziale e certificato atteso). La hashchain la archivia nel WORM.
Ogni richiesta di OTP e ogni tentativo (anche rifiutato) ha la sua voce d'audit, senza OTP.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import uuid
from typing import Any, Literal

import httpx
from asn1crypto import cms
from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.config import settings
from app.errori import errore_db
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi
from libs.shared.firme_responsabile import (
    TIPI_DEL_RESPONSABILE,
    TIPI_NOTI,
    DocumentoNonValido,
    accoda_documento,
)
from libs.shared.security.audit import registra_audit
from libs.shared.security.revoca import dipendenza_non_revocato
from libs.verumdoc_rules.permessi import RUOLI_CONSERVATORE

router = APIRouter(prefix="/api/dashboard/firme-responsabile",
                   tags=["Firme del responsabile"], responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 Depends(limite_richieste)])

#: trasporto del servizio di firma (test)
TRASPORTO: httpx.AsyncBaseTransport | None = None

_SELECT = ("SELECT id, tipo, titolo, nome, impronta_sha256, stato, richiesto_da, "
           "firmatario_atteso, firmato_da, firmato_il, certificato_soggetto, livello, "
           "busta_chiave IS NOT NULL AS archiviata, motivo, created_at, "
           "octet_length(contenuto) AS dimensione FROM public.firme_responsabile ")


async def responsabile(user: dict = Depends(utente_corrente),
                       authorization: str | None = Header(default=None)) -> dict:
    if user.get("ruolo") not in RUOLI_CONSERVATORE or user.get("auth_method") == "api_key" \
            or user.get("rp") or not authorization:
        raise ErroreApi("AUTH-010", "Le firme del responsabile sono riservate alle persone del "
                                    "conservatore")
    return {**user, "_authorization": authorization}


def _vista(r: Any) -> dict[str, Any]:
    return {"id": str(r["id"]), "tipo": r["tipo"],
            "tipo_descrizione": TIPI_NOTI.get(r["tipo"], r["tipo"]), "titolo": r["titolo"],
            "nome": r["nome"], "impronta_sha256": r["impronta_sha256"], "stato": r["stato"],
            "dimensione": int(r["dimensione"]),
            "firmatario_atteso": str(r["firmatario_atteso"]) if r["firmatario_atteso"] else None,
            "firmato_da": str(r["firmato_da"]) if r["firmato_da"] else None,
            "firmato_il": r["firmato_il"].isoformat() if r["firmato_il"] else None,
            "certificato_soggetto": r["certificato_soggetto"], "livello": r["livello"],
            "archiviata": bool(r["archiviata"]), "motivo": r["motivo"],
            "richiesto_il": r["created_at"].isoformat()}


def _ident(documento_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(documento_id)
    except ValueError:
        raise ErroreApi("GOV-404", "Documento non trovato") from None


async def _riga(session: Any, ident: uuid.UUID, *, blocca: bool = False) -> Any:
    riga = (await session.execute(text(
        "SELECT id, tipo, nome, contenuto, impronta_sha256, stato, firmatario_atteso "
        "FROM public.firme_responsabile WHERE id = :id" + (" FOR UPDATE" if blocca else "")),
        {"id": ident})).mappings().first()
    if riga is None:
        raise ErroreApi("GOV-404", "Documento non trovato")
    return riga


def _firmabile(riga: Any, user: dict) -> None:
    if riga["stato"] != "DA_FIRMARE":
        raise ErroreApi("GOV-409-FIRMA", f"Documento in stato {riga['stato']}")
    if riga["firmatario_atteso"] and str(riga["firmatario_atteso"]) != str(user.get("user_id")):
        raise ErroreApi("GOV-409-FIRMA", "La firma di questo documento spetta a un'altra persona")
    if riga["tipo"] in TIPI_DEL_RESPONSABILE and user.get("ruolo") != "RSC_CONSERVATORE":
        # revisione R17: manuale e piani li firma il responsabile del servizio (ruolo fisso)
        raise ErroreApi("GOV-409-FIRMA", "Manuale e piani li firma il responsabile del "
                                         "servizio di conservazione")


async def _inoltra(percorso: str, authorization: str, corpo: dict | None = None
                   ) -> dict[str, Any]:
    """Richiesta al servizio di firma con il token della persona (che la riverifica)."""
    try:
        async with httpx.AsyncClient(base_url=settings.signature_url.rstrip("/"),
                                     timeout=settings.signature_timeout_seconds,
                                     transport=TRASPORTO) as client:
            risposta = await client.post(percorso, json=corpo or {},
                                         headers={"Authorization": authorization})
    except httpx.HTTPError as exc:
        raise ErroreApi("FIRMA-502-FORNITORE",
                        f"servizio di firma non raggiungibile: {type(exc).__name__}") from None
    try:
        dati = risposta.json()
    except ValueError:
        dati = {}
    if risposta.status_code != 200 or not isinstance(dati, dict):
        codice = str(dati.get("codice") or "") if isinstance(dati, dict) else ""
        raise ErroreApi(codice if codice.startswith("FIRMA-") else "FIRMA-502-FORNITORE",
                        str(dati.get("detail") or "")[:300] or None if isinstance(dati, dict)
                        else None, dettagli={"http": risposta.status_code})
    return dati


async def _audit(evento: str, user: dict, dettagli: dict[str, Any]) -> None:
    try:
        async with get_async_session() as session:
            await registra_audit(session, evento=evento, user_id=user.get("user_id"),
                                 dettagli=dettagli)
    except SQLAlchemyError as exc:
        raise errore_db("audit della firma del responsabile", exc) from None


# ── elenco e accodamento ──────────────────────────────────────────────

@router.get("", summary="Documenti da firmare e firmati dal responsabile")
async def elenco(stato: Literal["DA_FIRMARE", "FIRMATO", "ARCHIVIATO", "RESPINTO", "ANNULLATO",
                                "TUTTI"] = "TUTTI",
                 user: dict = Depends(responsabile)) -> dict[str, Any]:
    filtro = "" if stato == "TUTTI" else "WHERE stato = :s "
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                _SELECT + filtro + "ORDER BY created_at DESC LIMIT 200"),
                {"s": stato})).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_db("elenco delle firme del responsabile", exc) from None
    return {"items": [_vista(r) for r in righe], "tipi": TIPI_NOTI}


class NuovoDocumento(BaseModel):
    tipo: str = Field(min_length=2, max_length=30, description="Codice in maiuscolo (NOMINA…)")
    titolo: str = Field(min_length=1, max_length=300)
    nome: str = Field(min_length=1, max_length=200, description="Nome del file firmato")
    contenuto_b64: str = Field(description="Documento, base64 (al più 5 MB)")
    firmatario_atteso: str | None = Field(None, description="Persona che deve firmare")


async def _accoda(user: dict, *, tipo: str, titolo: str, nome: str, contenuto: bytes,
                  firmatario_atteso: str | None = None) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            ident, nuovo = await accoda_documento(
                session, tipo=tipo, titolo=titolo, nome=nome, contenuto=contenuto,
                richiesto_da=user.get("user_id"), firmatario_atteso=firmatario_atteso)
            if nuovo:
                await registra_audit(session, evento="firma_responsabile.richiesta",
                                     user_id=user.get("user_id"),
                                     dettagli={"id": ident, "tipo": tipo,
                                               "impronta_sha256":
                                                   hashlib.sha256(contenuto).hexdigest()})
            riga = (await session.execute(text(_SELECT + "WHERE id = :id"),
                                          {"id": uuid.UUID(ident)})).mappings().one()
    except DocumentoNonValido as exc:
        raise ErroreApi("GOV-422-FIRMA", str(exc)) from None
    except SQLAlchemyError as exc:
        raise errore_db("accodamento del documento da firmare", exc) from None
    return {**_vista(riga), "nuovo": nuovo}


@router.post("", summary="Accoda un documento da firmare dal responsabile")
async def accoda(body: NuovoDocumento, user: dict = Depends(responsabile)) -> dict[str, Any]:
    try:
        contenuto = base64.b64decode(body.contenuto_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ErroreApi("GOV-422-FIRMA", "contenuto_b64 non è base64 valido") from None
    return await _accoda(user, tipo=body.tipo, titolo=body.titolo, nome=body.nome,
                         contenuto=contenuto, firmatario_atteso=body.firmatario_atteso)


#: revisione R17 (audit R17-04): documenti della documentazione generata (R15) che si
#: firmano, con il tipo del documento da firmare
DOCUMENTAZIONE_DA_FIRMARE = {"MANUALE_CONSERVAZIONE": "MANUALE",
                             "PIANO_SICUREZZA": "PIANO_SICUREZZA"}


@router.post("/documentazione/{versione_id}",
             summary="Mette in firma la versione stampabile (HTML) di un documento generato")
async def accoda_documentazione(versione_id: str,
                                user: dict = Depends(responsabile)) -> dict[str, Any]:
    """La firma del responsabile si appone sulla versione STAMPABILE generata da R15 (HTML,
    byte e impronta della versione immutabile), non sui dati del manuale."""
    ident = _ident(versione_id)
    try:
        async with get_async_session() as session:
            v = (await session.execute(text(
                "SELECT tipo, versione, titolo, nome_file, html FROM "
                "public.documentazione_versioni WHERE id = :id"), {"id": ident})
                 ).mappings().first()
    except SQLAlchemyError as exc:
        raise errore_db("versione della documentazione da firmare", exc) from None
    if v is None:
        raise ErroreApi("GOV-404", "Versione non trovata")
    tipo = DOCUMENTAZIONE_DA_FIRMARE.get(v["tipo"])
    if tipo is None:
        raise ErroreApi("GOV-422-FIRMA", "Si firmano il manuale e il piano della sicurezza")
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", f"{v['nome_file']}_v{v['versione']}")[:190] + ".html"
    return await _accoda(user, tipo=tipo, titolo=f"{v['titolo']} — versione {v['versione']}",
                         nome=nome, contenuto=bytes(v["html"]))


@router.get("/{documento_id}/contenuto", summary="Documento da leggere prima di firmarlo")
async def contenuto(documento_id: str, user: dict = Depends(responsabile)) -> Response:
    ident = _ident(documento_id)
    try:
        async with get_async_session() as session:
            riga = await _riga(session, ident)
    except SQLAlchemyError as exc:
        raise errore_db("contenuto del documento da firmare", exc) from None
    tipo_media = "application/json" if riga["nome"].endswith(".json") else \
        "text/html; charset=utf-8" if riga["nome"].endswith(".html") else \
        "application/octet-stream"
    return Response(bytes(riga["contenuto"]), media_type=tipo_media,
                    headers={"Content-Disposition": f'attachment; filename="{riga["nome"]}"',
                             "Cache-Control": "no-store",
                             # l'HTML da firmare non esegue nulla e non carica risorse
                             "Content-Security-Policy": "default-src 'none'; "
                                                        "style-src 'unsafe-inline'"})


# ── firma con OTP ─────────────────────────────────────────────────────

@router.post("/{documento_id}/otp", summary="Chiede l'invio dell'OTP al prestatore")
async def richiedi_otp(documento_id: str, user: dict = Depends(responsabile)) -> dict[str, Any]:
    ident = _ident(documento_id)
    try:
        async with get_async_session() as session:
            _firmabile(await _riga(session, ident), user)
    except SQLAlchemyError as exc:
        raise errore_db("documento da firmare", exc) from None
    # audit PRIMA dell'effetto esterno (§31 R12b revisione (2))
    await _audit("firma_responsabile.otp_richiesto", user, {"id": str(ident)})
    esito = await _inoltra("/api/v1/signature/personale/otp", user["_authorization"])
    return {"inviato": bool(esito.get("inviato")), "fornitore": esito.get("fornitore")}


class Firma(BaseModel):
    otp: str = Field(min_length=4, max_length=16, pattern=r"^[0-9A-Za-z]+$")
    pin: str = Field("", max_length=64)


def _controlla_busta(busta: bytes, contenuto: bytes) -> None:
    """La busta contiene ESATTAMENTE il documento (CAdES con contenuto, un firmatario)."""
    try:
        ci = cms.ContentInfo.load(busta)
        sd = ci["content"]
        incluso = sd["encap_content_info"]["content"].native
        firmatari = len(sd["signer_infos"])
    except Exception:  # noqa: BLE001 — busta illeggibile = non coerente
        raise ErroreApi("GOV-502-FIRMA", "Busta non leggibile") from None
    if ci["content_type"].native != "signed_data" or incluso != contenuto or firmatari != 1:
        raise ErroreApi("GOV-502-FIRMA", "La busta non contiene il documento da firmare")


@router.post("/{documento_id}/firma", summary="Firma personale con l'OTP")
async def firma(documento_id: str, body: Firma,
                user: dict = Depends(responsabile)) -> dict[str, Any]:
    ident = _ident(documento_id)
    try:
        async with get_async_session() as session:
            riga = await _riga(session, ident)
            _firmabile(riga, user)
    except SQLAlchemyError as exc:
        raise errore_db("documento da firmare", exc) from None
    dati = bytes(riga["contenuto"])
    await _audit("firma_responsabile.tentativo", user, {"id": str(ident)})
    try:
        esito = await _inoltra("/api/v1/signature/personale/firma", user["_authorization"], {
            "contenuto_b64": base64.b64encode(dati).decode(), "otp": body.otp, "pin": body.pin,
            "impronta_sha256_hex": riga["impronta_sha256"], "documento_id": str(ident)})
    except ErroreApi as exc:
        await _audit("firma_responsabile.rifiutata", user, {"id": str(ident),
                                                             "codice": exc.codice})
        raise
    try:
        busta = base64.b64decode(str(esito.get("busta_b64") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise ErroreApi("GOV-502-FIRMA", "Busta non decodificabile") from None
    _controlla_busta(busta, dati)
    if esito.get("verificata") is not True or \
            esito.get("impronta_sha256_contenuto") != riga["impronta_sha256"]:
        raise ErroreApi("GOV-502-FIRMA", "Firma non verificata o di un altro documento")
    try:
        async with get_async_session() as session:
            aggiornate = (await session.execute(text(
                "UPDATE public.firme_responsabile SET stato = 'FIRMATO', firmato_da = "
                " CAST(:u AS uuid), firmato_il = now(), credenziale_id = CAST(:c AS uuid), "
                " busta = :b, busta_sha256 = :bs, certificato_sha256 = :cs, "
                " certificato_soggetto = :sog, livello = :liv, updated_at = now() "
                "WHERE id = :id AND stato = 'DA_FIRMARE'"),
                {"u": str(user.get("user_id")), "c": str(esito.get("credenziale_id") or ""),
                 "b": busta, "bs": hashlib.sha256(busta).hexdigest(),
                 "cs": str(esito.get("certificato_sha256") or ""),
                 "sog": str(esito.get("firmatario") or "")[:500] or None,
                 "liv": str(esito.get("livello") or "")[:10] or None, "id": ident})).rowcount
            if not aggiornate:
                raise ErroreApi("GOV-409-FIRMA", "Il documento è stato firmato o annullato "
                                                 "nel frattempo")
            await registra_audit(session, evento="firma_responsabile.firmato",
                                 user_id=user.get("user_id"),
                                 dettagli={"id": str(ident), "tipo": riga["tipo"],
                                           "impronta_sha256": riga["impronta_sha256"],
                                           "busta_sha256": hashlib.sha256(busta).hexdigest(),
                                           "certificato_sha256": esito.get("certificato_sha256"),
                                           "livello": esito.get("livello")})
    except SQLAlchemyError as exc:
        # il trigger rifiuta credenziale o certificato non coerenti: nessuna firma registrata
        raise errore_db("registrazione della firma del responsabile", exc,
                        codice="GOV-503") from None
    return {"id": str(ident), "stato": "FIRMATO", "livello": esito.get("livello"),
            "firmatario": esito.get("firmatario"), "data_marca": esito.get("data_marca"),
            "avvisi": esito.get("avvisi") or []}


class Annulla(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)


@router.post("/{documento_id}/annulla", summary="Annulla un documento ancora da firmare")
async def annulla(documento_id: str, body: Annulla,
                  user: dict = Depends(responsabile)) -> dict[str, Any]:
    ident = _ident(documento_id)
    try:
        async with get_async_session() as session:
            riga = await _riga(session, ident, blocca=True)
            if riga["stato"] != "DA_FIRMARE":
                raise ErroreApi("GOV-409-FIRMA", f"Documento in stato {riga['stato']}")
            await session.execute(text(
                "UPDATE public.firme_responsabile SET stato = 'ANNULLATO', motivo = :m, "
                " annullato_da = CAST(:u AS uuid), updated_at = now() WHERE id = :id"),
                {"m": body.motivo.strip(), "u": str(user.get("user_id")), "id": ident})
            await registra_audit(session, evento="firma_responsabile.annullato",
                                 user_id=user.get("user_id"),
                                 dettagli={"id": str(ident), "motivo": body.motivo[:200]})
    except SQLAlchemyError as exc:
        raise errore_db("annullamento del documento da firmare", exc) from None
    return {"id": str(ident), "stato": "ANNULLATO"}


@router.get("/{documento_id}/busta", summary="Busta CAdES (.p7m) firmata dal responsabile")
async def busta(documento_id: str, user: dict = Depends(responsabile)) -> Response:
    ident = _ident(documento_id)
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT nome, busta, stato FROM public.firme_responsabile WHERE id = :id"),
                {"id": ident})).mappings().first()
    except SQLAlchemyError as exc:
        raise errore_db("busta del documento firmato", exc) from None
    if riga is None or riga["stato"] not in ("FIRMATO", "ARCHIVIATO") or riga["busta"] is None:
        raise ErroreApi("GOV-404", "Documento firmato non trovato")
    return Response(bytes(riga["busta"]), media_type="application/pkcs7-mime",
                    headers={"Content-Disposition": f'attachment; filename="{riga["nome"]}.p7m"',
                             "Cache-Control": "no-store"})


__all__ = ["router"]
