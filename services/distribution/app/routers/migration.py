# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
F3.4 — Migration API: pacchetto di migrazione per il cambio di conservatore.
Riferimento: art. 44 CAD, LG AgID; R03 b4 (VD-C-077).

POST /api/v2/migration/export/{aip_id}   — Prepara la migrazione di un AIP
POST /api/v2/migration/export-batch      — Prepara la migrazione di più AIP (≤ 100)
GET  /api/v2/migration/status/{job_id}   — Stato e collegamento del pacchetto

Revisione di sicurezza M6 (g5-sicurezza): identificativi non UUID → 422;
errori del database mai come testo al client; /status solo RSC/SUPER_ADMIN e
solo per i job del proprio ente.

R03 b4 (VD-C-077): prima l'esportazione era un dizionario in memoria con
chiavi S3 interne, «conservatore d'origine» scritto nel codice, richiedente
preso dal corpo e nessun audit. Ora ogni esportazione è un PdD MIGRAZIONE
(``pacchetti_distribuzione``, consegna ZIP preparata fuori dalla richiesta
con impronte verificate, ``migrazione.json`` dentro lo ZIP con il
conservatore d'origine da impostazione); richiedente = utente del token;
l'audit ``migrazione.richiesta`` si scrive nella STESSA transazione del
registro (se non si scrive, la richiesta non passa: REGOLE §15.3).

R05 (contratto Q2): ``export-batch`` e ``status`` accettano anche il TOKEN DI
SERVIZIO del ciclo di vita (aud ``MIGRAZIONE_PUBBLICO_SERVIZIO`` =
``verumdoc-distribution``, scope ``MIGRAZIONE_SCOPE_SERVIZIO`` =
``migrazione:servizio``; emesso con ``emetti_token_servizio --servizio
ciclo-vita --pubblico verumdoc-distribution --scope migrazione:servizio``).
Il servizio agisce per conto di un ente: ``ente_richiedente`` (batch) o
``?ente=`` (stato) obbligatori; le transazioni girano nell'ambito RLS di
QUELL'ente e ogni PdA deve esserne (altrimenti finisce negli ``errori``).
L'export singolo resta delle persone RSC/SUPER_ADMIN.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import chiavi_jwt, verify_access_token
from app.auth.limiti import LIMITATORE
from app.config import settings
from app.models.distribution import ConsegnaPdD, RichiestaDistribuzione, TipoPdD
from app.services.distribution_engine import STATI_DISTRIBUIBILI, esegui_distribuzione
from app.services.registro_pdd import registra_pdd
from app.services.s3_client import generate_signed_url
from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.models import Aip
from libs.shared.outbox import pubblica_dopo_commit
from libs.shared.security.ambito_dati import ambito_ente
from libs.shared.security.jwt_rs256 import ErroreTokenNonValido, verifica_token_servizio
from libs.shared.security.revoca import richiedi_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v2/migration", tags=["Migration"])

RUOLI_MIGRAZIONE = ("SUPER_ADMIN", "RSC_CONSERVATORE")
#: Chiave dell'identità che indica un token di servizio (non una persona).
SERVIZIO = "servizio_migrazione"


def _token_di_servizio(token: str) -> dict | None:
    """Payload di un token di servizio di migrazione VALIDO, ``None`` se il token
    non è un token di servizio di questa audience (allora è di una persona).
    Token di servizio senza lo scope → 403 MIG-403."""
    try:
        servizio = verifica_token_servizio(
            token, chiave_pubblica_pem=chiavi_jwt().pubblica_pem,
            emittente=settings.jwt_issuer, pubblico=settings.migrazione_pubblico_servizio)
    except ErroreTokenNonValido:
        return None
    if settings.migrazione_scope_servizio not in (servizio.get("scopes") or []):
        raise HTTPException(403, {"type": "MIG-403", "title": "Token di servizio senza lo "
                                  f"scope {settings.migrazione_scope_servizio}"})
    return {SERVIZIO: True, "servizio": str(servizio["servizio"]),
            "sub": str(servizio.get("sub") or ""), "ruolo": None, "user_id": None}


# BUG-004 FIX: autenticazione obbligatoria su tutti gli endpoint migration.
async def accesso_migrazione(authorization: str | None = Header(default=None)) -> dict:
    """Persona RSC_CONSERVATORE o SUPER_ADMIN (non revocata, limite al minuto del
    piano) oppure token di servizio del ciclo di vita (R05)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            {"type": "MIG-401", "title": "Token JWT obbligatorio per migrazione"},
        )
    token = authorization.split(" ", 1)[1].strip()
    servizio = _token_di_servizio(token)
    if servizio is not None:
        return servizio
    # Revisione B5: token di utenti disattivati o enti sospesi → 401 (prima del ruolo)
    await richiedi_non_revocato(verify_access_token(token))
    user = _require_migration_auth(authorization)
    # R03 f2 (REV-03): limite di richieste al minuto del piano
    await LIMITATORE.consuma(user)
    return user


def _require_migration_auth(authorization: str | None) -> dict:
    """Persona: solo RSC_CONSERVATORE e SUPER_ADMIN possono eseguire migrazioni."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            {"type": "MIG-401", "title": "Token JWT obbligatorio per migrazione"},
        )
    user = verify_access_token(authorization.split(" ", 1)[1])
    if user.get("ruolo") not in RUOLI_MIGRAZIONE:
        raise HTTPException(
            403,
            {"type": "MIG-403", "title": "Solo RSC e SUPER_ADMIN possono migrare AIP"},
        )
    return user


def _solo_persone(identita: dict) -> dict:
    if identita.get(SERVIZIO):
        raise HTTPException(403, {"type": "MIG-403",
                                  "title": "Operazione riservata a RSC e SUPER_ADMIN"})
    return identita


# Regole dichiarate per l'OpenAPI e il catalogo degli endpoint (tools/docs)
accesso_migrazione.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt", "token_servizio"]}


def _uuid_o_422(valori: list[str]) -> list[uuid.UUID]:
    """Identificativi AIP: tutti UUID, altrimenti 422 con l'elenco dei non validi."""
    validi: list[uuid.UUID] = []
    non_validi: list[str] = []
    for valore in valori:
        try:
            validi.append(uuid.UUID(str(valore)))
        except ValueError:
            non_validi.append(str(valore)[:64])
    if non_validi:
        raise HTTPException(422, {"type": "MIG-422", "title": "Identificativo AIP non valido",
                                  "non_validi": non_validi[:20]})
    return validi


def _errore_db(evento: str, exc: Exception) -> ErroreApi:
    """Errore del database: dettaglio nel log, al client il codice e l'id della
    richiesta da citare (VD-C-100)."""
    return errore_registrato("MIG-503", exc, evento=evento)


class MigrationRequest(BaseModel):
    conservatore_destinatario: str = Field(..., min_length=3, max_length=500,
                                           description="Conservatore di destinazione")
    motivo: str = Field(..., min_length=10, max_length=500)
    richiedente_id: str | None = Field(None, description="Ignorato: è l'utente del token")
    include_sincro_xml: bool = True
    include_metadati_extra: bool = True


class BatchMigrationRequest(BaseModel):
    aip_ids: list[str] = Field(..., min_length=1, max_length=100)
    conservatore_destinatario: str
    motivo: str
    richiedente_id: str | None = None
    ente_richiedente: str | None = Field(
        None, description="Solo con il token di servizio (obbligatorio): ente per conto del "
                          "quale si migra; ogni PdA deve essere di quell'ente")


async def _leggi_aip(aid: uuid.UUID) -> Aip | None:
    async with get_async_session() as session:
        return (await session.execute(select(Aip).where(Aip.id == aid))).scalar_one_or_none()


async def _prepara(aip: Aip, user: dict, destinatario: str, motivo: str,
                   ente_richiedente: str | None = None) -> dict:
    """PdD MIGRAZIONE (ZIP) + registro + audit nella stessa transazione."""
    richiedente = str(user.get("user_id") or user.get("sub") or "")
    req = RichiestaDistribuzione(aip_id=str(aip.id), tipo=TipoPdD.MIGRAZIONE,
                                 motivo=motivo[:500], consegna=ConsegnaPdD.ZIP)
    risultato = await esegui_distribuzione(req, str(aip.tenant_id))
    ente_richiedente = ente_richiedente or str(user.get("tenant_id") or "")
    dettagli_servizio = {"servizio": user["servizio"]} if user.get(SERVIZIO) else {}
    async with get_async_session() as session:
        pdd_id = await registra_pdd(
            session, risultato=risultato, req=req, tenant_id=str(aip.tenant_id),
            richiedente=richiedente, canale="API",
            opzioni={"conservatore_destinatario": destinatario,
                     "ente_richiedente": ente_richiedente},
        )
        await session.execute(text(
            "INSERT INTO public.audit_global (id, tenant_id, user_id, aip_id, evento, dettagli) "
            "VALUES (:id, :t, :u, :a, 'migrazione.richiesta', CAST(:d AS jsonb))"
        ), {"id": uuid.uuid4(), "t": aip.tenant_id,
            "u": _uuid_o_none(richiedente), "a": aip.id,
            "d": json.dumps({"pdd_id": pdd_id, "conservatore_destinatario": destinatario,
                             "motivo": motivo[:500], "ruolo": user.get("ruolo"),
                             "ente_richiedente": ente_richiedente, **dettagli_servizio})})
    await pubblica_dopo_commit(evento="aip.distribuito", pdd_id=pdd_id)
    logger.info("migration_export_created", export_id=pdd_id, aip_id=str(aip.id),
                destinatario=destinatario)
    return {"export_id": pdd_id, "pdd_id": pdd_id, "aip_id": str(aip.id), "urn": aip.urn,
            "stato": risultato.stato, "link_stato": f"/api/v2/migration/status/{pdd_id}"}


def _uuid_o_none(valore: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


@router.post("/export/{aip_id}", summary="Prepara il pacchetto di migrazione di un AIP")
async def export_aip(
    aip_id: str,
    req: MigrationRequest,
    identita: dict = Depends(accesso_migrazione),  # noqa: B008
) -> dict:
    """
    Prepara il pacchetto di migrazione (ZIP con documenti, IPdA, manifesto e
    ``migrazione.json``) fuori dalla richiesta: stato e collegamento in
    ``link_stato``. Solo RSC_CONSERVATORE o SUPER_ADMIN.
    """
    user = _solo_persone(identita)
    [aid] = _uuid_o_422([aip_id])
    try:
        aip = await _leggi_aip(aid)
    except SQLAlchemyError as exc:
        raise _errore_db("migration_export_errore_db", exc) from exc
    if not aip:
        raise HTTPException(404, {"type": "MIG-404", "title": "AIP non trovato"})
    if aip.stato not in STATI_DISTRIBUIBILI:
        raise HTTPException(422, {"type": "MIG-101",
                                  "title": f"AIP non distribuibile — stato: {aip.stato.value}"})
    try:
        esito = await _prepara(aip, user, req.conservatore_destinatario, req.motivo)
    except SQLAlchemyError as exc:
        raise _errore_db("migration_export_errore_db", exc) from exc
    return {**esito, "conservatore_destinatario": req.conservatore_destinatario,
            "istruzioni": ("Seguire link_stato fino a PRONTO, scaricare lo ZIP, verificarne "
                           "l'impronta e quelle elencate in contenuto_pdd.json, caricarlo nel "
                           "sistema del conservatore destinatario.")}


@router.post("/export-batch", summary="Prepara la migrazione di più AIP")
async def export_batch(
    req: BatchMigrationRequest,
    user: dict = Depends(accesso_migrazione),  # noqa: B008
) -> dict:
    """Un pacchetto di migrazione per ogni AIP (al più 100). BUG-004 FIX: richiede auth.
    Con il token di servizio (R05) ``ente_richiedente`` è obbligatorio."""
    identificativi = _uuid_o_422(req.aip_ids)
    ente = None
    if user.get(SERVIZIO):
        ente = _uuid_o_none(req.ente_richiedente)
        if ente is None:
            raise HTTPException(422, {"type": "MIG-422",
                                      "title": "ente_richiedente obbligatorio con il token "
                                               "di servizio"})
    risultati, errori = [], []
    try:
        # Token di servizio: ambito RLS dell'ente richiedente (i PdA degli altri
        # enti non si vedono); persone: l'ambito della loro identità.
        with ambito_ente(ente) if ente is not None else contextlib.nullcontext():
            for aip_id, aid in zip(req.aip_ids, identificativi, strict=True):
                aip = await _leggi_aip(aid)
                if aip is None or aip.stato not in STATI_DISTRIBUIBILI or (
                        ente is not None and aip.tenant_id != ente):
                    errori.append({"aip_id": aip_id,
                                   "errore": "non trovato o non distribuibile"})
                    continue
                risultati.append(await _prepara(
                    aip, user, req.conservatore_destinatario,
                    req.motivo or "migrazione massiva",
                    ente_richiedente=str(ente) if ente is not None else None))
    except SQLAlchemyError as exc:
        raise _errore_db("migration_batch_errore_db", exc) from exc
    logger.info("migration_batch_created", totale=len(req.aip_ids), ok=len(risultati),
                errori=len(errori))
    return {
        "totale": len(req.aip_ids), "esportati": len(risultati), "errori_count": len(errori),
        "conservatore_destinatario": req.conservatore_destinatario,
        "results": risultati, "errori": errori, "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/status/{job_id}", summary="Stato del pacchetto di migrazione")
async def stato_job(job_id: str, ente: str | None = None,
                    user: dict = Depends(accesso_migrazione)) -> dict:  # noqa: B008
    """Solo RSC/SUPER_ADMIN e solo per le migrazioni chieste dal proprio ente;
    con il token di servizio (R05) per l'ente indicato in ``?ente=`` (obbligatorio)."""
    ente_richiedente = str(user.get("tenant_id") or "")
    ambito = contextlib.nullcontext()
    if user.get(SERVIZIO):
        ente_uuid = _uuid_o_none(ente)
        if ente_uuid is None:
            raise HTTPException(422, {"type": "MIG-422",
                                      "title": "ente obbligatorio con il token di servizio"})
        ente_richiedente = str(ente_uuid)
        ambito = ambito_ente(ente_uuid)
    jid = _uuid_o_none(job_id)
    non_trovato = HTTPException(404, {"type": "MIG-404", "title": "Job non trovato"})
    if jid is None:
        raise non_trovato
    try:
        with ambito:
            riga = await _riga_stato(jid, ente_richiedente)
    except SQLAlchemyError as exc:
        raise _errore_db("migration_status_errore_db", exc) from exc
    if riga is None:
        raise non_trovato
    return await _esito_stato(riga)


async def _riga_stato(jid: uuid.UUID, ente_richiedente: str) -> Mapping[str, Any] | None:
    async with get_async_session() as session:
        return (await session.execute(text(
            "SELECT id::text AS id, aip_id::text AS aip_id, stato, zip_bucket, zip_key, "
            "zip_dimensione, zip_sha256, hash_pdd, scade_il, errore, durata_link_secondi, "
            "opzioni FROM public.pacchetti_distribuzione "
            "WHERE id = :id AND tipo = 'MIGRAZIONE' "
            "AND opzioni ->> 'ente_richiedente' = :ente"
        ), {"id": jid, "ente": ente_richiedente})).mappings().first()


async def _esito_stato(riga: Mapping[str, Any]) -> dict:
    esito = {"export_id": riga["id"], "aip_id": riga["aip_id"], "stato": riga["stato"],
             "conservatore_destinatario": (riga["opzioni"] or {}).get("conservatore_destinatario"),
             "hash_pdd": riga["hash_pdd"], "errore": riga["errore"]}
    adesso = datetime.now(UTC)
    if riga["stato"] == "PRONTO" and riga["zip_key"] and riga["scade_il"] > adesso:
        secondi = max(1, min(int(riga["durata_link_secondi"]),
                             int((riga["scade_il"] - adesso).total_seconds())))
        esito["zip"] = {
            "url": await generate_signed_url(riga["zip_bucket"], riga["zip_key"], secondi,
                                             f"migrazione-{riga['id']}.zip"),
            "dimensione_bytes": riga["zip_dimensione"], "hash_sha256": riga["zip_sha256"],
            "scade_il": riga["scade_il"].isoformat(),
        }
    return esito
