# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: routers/status
Servizio: gateway
Riferimento piano: F1.1.4 — Endpoint API v1; VD-C-012, VD-C-020 (R09/R10)

GET /api/v1/versamento/{pdv_id}      — Stato del PdV.
GET /api/v1/versamento/{pdv_id}/rdv  — Download del RdV definitivo.

Il RdV definitivo lo emette la hashchain quando il PdV diventa CONSERVATO e
ne scrive il riferimento in ``pdv.metadata_json['rdv_definitiva']``
(bucket, chiave, impronta SHA-256, formato). ``/rdv`` restituisce il documento
quando il riferimento esiste (impronta verificata alla lettura) e 409 solo
quando davvero non è ancora stato emesso.

R04 (VD-C-096, contratto K5): da R04 il RdV è nel formato 2
(``verumdoc-rdv-definitivo-2``, namespace ``https://schemi.verumdoc.it/rdv/definitivo/2``)
con le prove d'inclusione documento → PdA → registro e il checkpoint firmato;
quelli già emessi nel formato 1 restano come sono e si scaricano uguali.
L'intestazione ``X-Formato-RdV`` dice quale dei due è; per il formato 2
``X-Checkpoint-Dimensione`` indica il checkpoint a cui si riferiscono le prove.

R05 (contratto Q2, ciclo di vita): lo stato riporta anche ``annullamento``
(pratica, istante e stato precedente quando il PdV è ANNULLATO) e
``rettifica`` (catena diretta: ``rettifica_di`` per un PdV di rettifica,
``rettificato_da`` per l'originale), letti dalle pratiche del servizio
ciclo-vita (``public.pratiche``, indici ``idx_pratiche_pdv`` e
``idx_pratiche_pdv_risultato``). L'originale non cambia mai.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_serializer
from sqlalchemy import text

from app.auth.dependencies import get_current_user
from app.models.responses import ProblemDetail, StatoPdvResponse
from libs.shared.errori import ErroreApi

router = APIRouter(prefix="/versamento", tags=["Versamento"])
logger = structlog.get_logger(__name__)


def _problema(status: int, codice: str, titolo: str, dettaglio: str | None = None,
              **extra: Any) -> ErroreApi:
    """Errore del catalogo (VD-C-100): sollevato, il gestore risponde in problem+json."""
    return ErroreApi(codice, dettaglio, titolo=titolo, status=status, dettagli=extra)


def _tenant(user: dict) -> UUID | None:
    try:
        return UUID(str(user.get("tenant_id")))
    except ValueError:
        return None


async def _leggi_pdv(pdv_id: UUID, tenant: UUID) -> dict | None:
    """PdV del cliente con classe e AIP (una sessione, una query)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        riga = (await session.execute(
            text(
                # join pdv → classe (codice) e → aip (id, urn): tutto in una lettura
                "SELECT p.id, p.stato::text AS stato, p.data_ricezione, p.data_aggiornamento, "
                "p.errori_validazione, p.metadata_json, c.codice AS classe, "
                "a.id AS aip_id, a.urn "
                "FROM public.pdv p "
                "LEFT JOIN public.classi_documentali c ON c.id = p.classe_doc_id "
                "LEFT JOIN public.aip a ON a.pdv_id = p.id "
                "WHERE p.id = :id AND p.tenant_id = :tenant "
                "ORDER BY a.created_at DESC NULLS LAST LIMIT 1"
            ),
            {"id": pdv_id, "tenant": tenant},
        )).mappings().first()
    return dict(riga) if riga else None


class AnnullamentoPdv(BaseModel):
    """Annullamento logico del PdV (pratica ANNULLAMENTO del ciclo di vita)."""
    pratica_id: str
    annullato_il: str | None = None
    stato_precedente: str | None = None


class RettificaPdv(BaseModel):
    """Catena di rettifica diretta (contratto Q2): l'originale non cambia; ogni
    rettifica è un PdV nuovo che segue la pipeline normale."""
    rettifica_di: dict[str, Any] | None = Field(
        None, description="Per un PdV di rettifica: PdV originale e pratica")
    rettificato_da: list[dict[str, Any]] = Field(
        default_factory=list, description="Per l'originale: rettifiche chieste, dalla più recente")


class StatoPdvCicloVita(StatoPdvResponse):
    """Stato del PdV con annullamento e catena di rettifica (R05)."""
    annullamento: AnnullamentoPdv | None = None
    rettifica: RettificaPdv | None = None

    @model_serializer(mode="wrap")
    def _senza_ciclo_vita_vuoto(self, serializza: Any):  # noqa: ANN202 — senza tipo lo schema resta quello del modello
        """I due campi compaiono solo quando c'è qualcosa da dire: la risposta
        di un PdV senza annullamento né rettifiche resta identica a prima
        (client e SDK esistenti non vedono campi nuovi)."""
        dati = serializza(self)
        for campo in ("annullamento", "rettifica"):
            if dati.get(campo) is None:
                dati.pop(campo, None)
        return dati


async def _ciclo_vita(pdv_id: UUID, tenant: UUID, metadata: dict) -> dict[str, Any]:
    """Annullamento (dai metadati del PdV) e rettifiche collegate (pratiche)."""
    from libs.shared.db import get_async_session

    risultato: dict[str, Any] = {}
    annullamento = metadata.get("annullamento")
    if isinstance(annullamento, dict) and annullamento.get("pratica_id"):
        risultato["annullamento"] = AnnullamentoPdv(
            pratica_id=str(annullamento["pratica_id"]), annullato_il=annullamento.get("il"),
            stato_precedente=annullamento.get("stato_precedente"))
    async with get_async_session() as session:
        righe = (await session.execute(
            text(
                # due indici parziali: pratiche sull'originale e sul PdV di rettifica
                "SELECT id::text AS pratica_id, stato, pdv_id::text AS pdv_id, "
                "pdv_risultato_id::text AS pdv_rettifica_id, created_at, conclusa_il "
                "FROM public.pratiche WHERE tipo = 'RETTIFICA' AND tenant_id = :tenant "
                "AND (pdv_id = :id OR pdv_risultato_id = :id) "
                "ORDER BY created_at DESC, id DESC LIMIT 50"
            ),
            {"id": pdv_id, "tenant": tenant},
        )).mappings().all()
    di = next((r for r in righe if r["pdv_rettifica_id"] == str(pdv_id)), None)
    da = [r for r in righe if r["pdv_id"] == str(pdv_id)]
    if di is not None or da:
        risultato["rettifica"] = RettificaPdv(
            rettifica_di=None if di is None else {
                "pdv_id": di["pdv_id"], "pratica_id": di["pratica_id"], "stato": di["stato"]},
            rettificato_da=[{"pratica_id": r["pratica_id"], "stato": r["stato"],
                             "pdv_rettifica_id": r["pdv_rettifica_id"],
                             "richiesta_il": r["created_at"], "conclusa_il": r["conclusa_il"]}
                            for r in da])
    return risultato


def _non_trovato(pdv_id: UUID) -> ErroreApi:
    return _problema(404, "VRS-404", "PdV non trovato",
                     f"PdV {pdv_id} non trovato per questo cliente")


@router.get(
    "/{pdv_id}",
    response_model=StatoPdvCicloVita,
    summary="Stato PdV",
    responses={404: {"model": ProblemDetail}, 401: {"model": ProblemDetail}},
)
async def stato_pdv(
    pdv_id: UUID,
    user: dict = Depends(get_current_user),
):
    """
    Stato attuale di un Pacchetto di Versamento: stato, errori di verifica,
    RdV preliminare, riferimento al RdV definitivo (se emesso), AIP.
    """
    tenant = _tenant(user)
    if tenant is None:
        raise _non_trovato(pdv_id)
    pdv = await _leggi_pdv(pdv_id, tenant)
    if pdv is None:
        raise _non_trovato(pdv_id)
    metadata = pdv["metadata_json"] or {}
    return StatoPdvCicloVita(
        pdv_id=pdv["id"],
        stato=pdv["stato"],
        classe_documentale=pdv["classe"],
        data_ricezione=pdv["data_ricezione"],
        data_aggiornamento=pdv["data_aggiornamento"],
        errori=pdv["errori_validazione"] or [],
        rdv_preliminare=metadata.get("rdv_preliminare"),
        rdv_definitivo=metadata.get("rdv_definitiva"),
        aip_id=pdv["aip_id"],
        urn=pdv["urn"],
        **await _ciclo_vita(pdv_id, tenant, metadata),
    )


def _scarica_rdv_sync(bucket: str, chiave: str) -> bytes:
    from app.services.versamento_service import _s3_client

    risposta = _s3_client().get_object(Bucket=bucket, Key=chiave)
    return risposta["Body"].read()


@router.get(
    "/{pdv_id}/rdv",
    summary="Download RdV definitivo",
    responses={
        200: {"content": {"application/xml": {}}, "description": "RdV definitivo (XML)"},
        404: {"model": ProblemDetail},
        409: {"model": ProblemDetail, "description": "RdV non ancora emesso"},
        502: {"model": ProblemDetail, "description": "Impronta del RdV non corrispondente"},
        503: {"model": ProblemDetail, "description": "Storage non disponibile"},
    },
)
async def download_rdv(
    pdv_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Restituisce il RdV definitivo (XML) del PdV conservato."""
    tenant = _tenant(user)
    if tenant is None:
        raise _non_trovato(pdv_id)
    pdv = await _leggi_pdv(pdv_id, tenant)
    if pdv is None:
        raise _non_trovato(pdv_id)

    riferimento = (pdv["metadata_json"] or {}).get("rdv_definitiva")
    if not isinstance(riferimento, dict) or not riferimento.get("s3_key"):
        raise _problema(
            409, "VRS-409", "RdV non ancora disponibile",
            f"Stato attuale: {pdv['stato']}. Il RdV definitivo si emette quando il PdV "
            "è CONSERVATO: il pacchetto è nel registro delle impronte ed è coperto da un "
            "checkpoint firmato.",
            stato=pdv["stato"],
        )
    try:
        contenuto = await asyncio.to_thread(
            _scarica_rdv_sync, riferimento["s3_bucket"], riferimento["s3_key"],
        )
    except Exception as exc:  # noqa: BLE001 — storage irraggiungibile: 503 esplicito
        logger.error("rdv_download_errore", pdv_id=str(pdv_id), errore=str(exc)[:200])
        raise _problema(503, "VRS-503", "Storage temporaneamente non disponibile",
                        "Riprovare tra qualche minuto.") from None
    impronta = hashlib.sha256(contenuto).hexdigest()
    if riferimento.get("impronta_sha256") and impronta != riferimento["impronta_sha256"]:
        logger.error("rdv_impronta_non_corrispondente", pdv_id=str(pdv_id))
        raise _problema(502, "VRS-502", "Il RdV letto non corrisponde all'impronta registrata")
    intestazioni = {
        "Content-Disposition": f'attachment; filename="RdV_{pdv_id}.xml"',
        "X-Impronta-SHA256": impronta,
        # R04: formato del RdV (1 = emesso prima del registro a due livelli)
        "X-Formato-RdV": str(riferimento.get("formato") or "verumdoc-rdv-1"),
    }
    if riferimento.get("checkpoint_dimensione") is not None:
        intestazioni["X-Checkpoint-Dimensione"] = str(int(riferimento["checkpoint_dimensione"]))
    return Response(content=contenuto, media_type="application/xml", headers=intestazioni)
