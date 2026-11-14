# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Canale REST del versamento: multipart → caso d'uso unico → risposta (§7.4, §9.1).

Servizio: gateway. Riferimenti: piano R03 §3.1; VD-C-083 (Idempotency-Key),
VD-C-094/095 (R03). Nessuna regola qui: il router traduce il multipart in
``FonteFlusso`` (streaming, nessun file intero in memoria) e chiama
``app.services.versamento_service.versa`` tramite l'adattatore multipart.

Errori: ogni rifiuto è un ``RifiutoVersamento`` (``ErroreApi``) e lo formatta il
gestore comune ``libs.shared.errori`` in application/problem+json.

Risposta 202 (o 200 per la stessa richiesta ripetuta, con ``Idempotent-Replayed``):
id del PdV, stato, avvisi, link allo stato (anche in ``Location``). Senza
``Idempotency-Key`` durante la transizione: intestazioni ``Deprecation``
(RFC 9745) e ``Sunset`` (RFC 8594) e l'avviso VRS-126 nel corpo.

La route GET /versamento/{pdv_id} è servita da status.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from email.utils import format_datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from app.auth.canali import canale_effettivo
from app.auth.dependencies import require_scope
from app.config import settings
from app.models.versamento import VersamentoResponse
from app.services.caricamenti import VersamentoPerRiferimento, versa_per_riferimento
from app.services.corpo import leggi_corpo_limitato, leggi_modulo_limitato, posto_multipart
from app.services.versamento_service import (
    Canale,
    EsitoVersamento,
    costruisci_rdv_preliminare,
    versa_unita_documentaria,
)
from libs.shared.errori import RISPOSTE_ERRORE
from libs.verumdoc_rules.impostazioni import FUSO_NORMATIVO

router = APIRouter(tags=["Versamento"])

# Compatibilità: nome storico della funzione (una sola implementazione nel servizio)
_build_rdv_preliminare = costruisci_rdv_preliminare

_NOTA_RDV = (
    "RdV preliminare nel campo rdv_preliminare di GET /api/v1/versamento/{pdv_id}; "
    "RdV definitivo in GET /api/v1/versamento/{pdv_id}/rdv quando il PdV è CONSERVATO."
)
_versatore = require_scope("versamento:ud")


def _epoch(giorno: datetime) -> int:
    return int(giorno.timestamp())


def intestazioni_versamento(esito: EsitoVersamento) -> dict[str, str]:
    """Intestazioni della risposta di un versamento (usate da ogni canale HTTP)."""
    h = {"Location": esito.link_stato}
    if not esito.nuovo:
        h["Idempotent-Replayed"] = "true"
    if esito.chiave_derivata:
        dal = datetime.combine(settings.idempotenza_deprecata_dal, time(0), tzinfo=UTC)
        h["Deprecation"] = f"@{_epoch(dal)}"
        if esito.transizione_fino_al is not None:
            fine = datetime.combine(
                esito.transizione_fino_al + timedelta(days=1),
                time(0),
                tzinfo=ZoneInfo(FUSO_NORMATIVO),
            )
            h["Sunset"] = format_datetime(fine.astimezone(UTC), usegmt=True)
    return h


def risposta_versamento(esito: EsitoVersamento) -> JSONResponse:
    """202 per un PdV nuovo, 200 per la stessa richiesta ripetuta."""
    corpo = VersamentoResponse(
        pdv_id=esito.pdv_id,
        stato=esito.stato,
        message=(
            "Versamento ricevuto. La validazione è in corso."
            if esito.nuovo
            else "Versamento già ricevuto: stessa richiesta, stesso PdV."
        ),
        avvisi=esito.avvisi,
        link_stato=esito.link_stato,
        nota_rdv=_NOTA_RDV,
    )
    return JSONResponse(
        status_code=202 if esito.nuovo else 200,
        content=corpo.model_dump(mode="json"),
        headers=intestazioni_versamento(esito),
    )


@router.post(
    "/versamento/ud",
    response_model=VersamentoResponse,
    status_code=202,
    summary="Versamento Unità Documentaria — LG AGID 2022",
    responses={
        200: {
            "model": VersamentoResponse,
            "description": "Stessa richiesta ripetuta (stessa Idempotency-Key): stesso PdV",
        },
        **RISPOSTE_ERRORE,
    },
    # R03 b1 (contratto C1): stesso percorso anche in JSON, con i file citati per
    # riferimento a sessioni di caricamento COMPLETE. R03 f1 (REV-09): il multipart
    # è descritto qui e letto nella funzione, DOPO l'autenticazione.
    openapi_extra={"requestBody": {"required": True, "content": {
        "multipart/form-data": {"schema": {
            "type": "object", "required": ["indice"],
            "properties": {
                "indice": {"type": "string", "description": "IndiceUD in JSON"},
                "files": {"type": "array", "items": {"type": "string", "format": "binary"},
                          "description": "File dichiarati nell'indice (nome = nome_file)"},
            }}},
        "application/json": {"schema": VersamentoPerRiferimento.model_json_schema()}}}},
)
async def versa_ud(
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    user: dict = Depends(_versatore),
) -> JSONResponse:
    """
    Versa un'Unità Documentaria (indice JSON + file). Intestazione
    ``Idempotency-Key`` obbligatoria (VD-P-27; in transizione: accettata senza,
    con avviso e intestazione Deprecation): la stessa chiave con lo stesso indice
    restituisce lo stesso PdV (200); con indice diverso 409 VRS-121. Lo stesso
    indice di un PdV attivo con un'altra chiave: 409 VRS-120; stessa chiave UD
    (registro, anno, numero) con contenuto diverso: 409 VRS-124. Metadati non
    conformi alle regole della classe: 422 VRS-130 con tutti i rilievi.

    In ``application/json`` (versamento per riferimento, contratto C1):
    ``{"indice": {...}, "file": {"<nome_file>": "<caricamento_id>"}}`` con
    sessioni di caricamento COMPLETE; stesse risposte e stessi errori, più
    UPL-404/409/410/008/009. Una sessione si usa in un solo versamento.

    R03 f1 (REV-09): nessun byte del corpo si legge prima dell'autenticazione
    (dipendenza ``_versatore``): il multipart si legge qui, con un limite
    sull'intero corpo (``VERSAMENTO_REST_MAX_CORPO_MB``, UPL-413) che vale anche
    in chunked e al più ``VERSAMENTO_MAX_FILE_PER_PDV`` file. R03 f3 (REV-N2):
    al più ``VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX`` multipart per
    replica e un budget di byte su disco temporaneo (``DISCO_TEMPORANEO_BUDGET_MB``,
    condiviso con PDND): oltre, 503 UPL-503-OCCUPATO ritentabile, senza leggere il corpo.
    """
    tipo = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if tipo == "application/json":
        corpo = await leggi_corpo_limitato(
            request, settings.versamento_json_max_mb * 1024 * 1024, codice="UPL-413")
        esito = await versa_per_riferimento(corpo, user=user, idempotency_key=idempotency_key)
        return risposta_versamento(esito)
    limite = settings.versamento_rest_max_corpo_mb * 1024 * 1024
    # R03 f3 (REV-N2): tetto di multipart contemporanei e budget di /tmp della
    # replica, prima di leggere il corpo (503 UPL-503-OCCUPATO ritentabile);
    # rilasciati dopo la chiusura del modulo (file temporanei eliminati)
    with posto_multipart(request, limite=limite):
        return await _versamento_multipart(request, user, idempotency_key, limite)


async def _versamento_multipart(request: Request, user: dict, idempotency_key: str | None,
                                limite: int) -> JSONResponse:
    modulo = await leggi_modulo_limitato(
        request, limite=limite, codice="UPL-413",
        max_file=settings.versamento_max_file_per_pdv + 1)
    try:
        indice = modulo.get("indice")
        if not isinstance(indice, str):
            # Stessa risposta di prima della lettura manuale: campo del modulo mancante
            raise RequestValidationError([{"type": "missing", "loc": ("body", "indice"),
                                           "msg": "Field required", "input": None}])
        files = [v for v in modulo.getlist("files") if isinstance(v, UploadFile)]
        esito = await versa_unita_documentaria(
            indice_json=indice,
            files=files,
            user=user,
            idempotency_key=idempotency_key,
            # Canale effettivo (contratto C2): un token di canale resta del suo canale
            canale=Canale(canale_effettivo(user, Canale.REST.value)),
        )
    finally:
        await modulo.close()
    return risposta_versamento(esito)
