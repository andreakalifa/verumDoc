# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/services/registro_pdd.py — registro dei PdD richiesti.

Riferimenti: G2 (registro in ``notifications``), g3-backend (outbox, REGOLE
§12.3), R03 b4 (VD-C-077: ``pacchetti_distribuzione``).

Revisione R05 (R10): prima di scrivere il PdD il PdA si blocca ``FOR SHARE`` e
se ne ricontrolla lo stato nella stessa transazione. Lo scarto (ciclo-vita)
blocca lo stesso PdA ``FOR UPDATE`` per il ricontrollo e il passaggio a
ELIMINAZIONE: o il PdD è registrato prima (lo scarto vede l'esibizione in
corso e si ferma), o il PdA è già in ELIMINAZIONE (qui 409 DST-101).

Una sola funzione, chiamata nella transazione della richiesta: riga del
registro delle distribuzioni (``notifications``, tipo PDD_*: storico,
approvazione), riga del PdD (``pacchetti_distribuzione``: file con le chiavi
interne per rigenerare i collegamenti o preparare lo ZIP) ed evento
``aip.distribuito`` nell'outbox. Se una delle tre non si scrive, nessuna resta
e il chiamante risponde 503 senza consegnare collegamenti.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.models.distribution import RichiestaDistribuzione, RispostaDistribuzione
from app.services.distribution_engine import STATI_DISTRIBUIBILI, evento_distribuzione
from libs.shared.errori import ErroreApi
from libs.shared.outbox import accoda_evento

#: Stati in cui un PdD si registra (quelli distribuibili; MAI ELIMINAZIONE)
STATI_REGISTRABILI = frozenset(s.value for s in STATI_DISTRIBUIBILI)


def uuid_o_none(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


async def registra_pdd(
    session: Any,
    *,
    risultato: RispostaDistribuzione,
    req: RichiestaDistribuzione,
    tenant_id: str,
    richiedente: str,
    canale: str,
    opzioni: dict[str, Any] | None = None,
    adesso: datetime | None = None,
) -> str:
    """Scrive registro, PdD e outbox; restituisce l'id (UUID) del PdD registrato."""
    ora = adesso or datetime.now(UTC)
    pdd_uuid = str(uuid_o_none(risultato.pdd_id) or uuid.uuid4())
    aip_uuid = uuid_o_none(req.aip_id)
    await session.execute(
        text(
            "INSERT INTO public.notifications "
            "(id, tenant_id, aip_id, tipo, canale, destinatario, payload_json, stato) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:aip_id AS uuid), :tipo, "
            " :canale, :dest, CAST(:payload AS jsonb), :stato)"
        ),
        {
            "id": pdd_uuid, "tid": tenant_id,
            "aip_id": str(aip_uuid) if aip_uuid else None,
            "tipo": f"PDD_{req.tipo.value}", "canale": canale, "dest": richiedente,
            "payload": json.dumps({
                "motivo": req.motivo, "tipo": req.tipo.value, "pdd_id": pdd_uuid,
                "richiedente_id": richiedente, "hash_pdd": risultato.hash_pdd,
                "url_scadenza": risultato.url_scadenza, "consegna": risultato.consegna,
            }),
            "stato": "PENDING" if risultato.stato == "PENDING" else "INVIATO",
        },
    )
    if aip_uuid is not None:
        stato_pda = (await session.execute(text(
            "SELECT stato::text FROM public.aip WHERE id = :aip FOR SHARE"),
            {"aip": aip_uuid})).scalar_one_or_none()
        if stato_pda is not None and stato_pda not in STATI_REGISTRABILI:
            raise ErroreApi("DST-101", titolo=f"AIP non distribuibile — stato: {stato_pda}")
        durata = settings.pdd_durata_link_secondi
        if risultato.url_scadenza:
            durata = max(60, int((datetime.fromisoformat(risultato.url_scadenza) - ora)
                                 .total_seconds()))
        elif req.url_expiry_seconds:
            durata = min(req.url_expiry_seconds, settings.pdd_durata_link_max_secondi)
        pronto = risultato.stato == "PRONTO"
        await session.execute(text(
            "INSERT INTO public.pacchetti_distribuzione "
            "(id, tenant_id, aip_id, tipo, consegna, stato, richiedente_id, canale, motivo, "
            " file_richiesti, opzioni, documenti, hash_pdd, durata_link_secondi, "
            " pronto_il, scade_il) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :aip, :tipo, :consegna, :stato, "
            " :rich, :canale, :motivo, CAST(:file_richiesti AS jsonb), CAST(:opzioni AS jsonb), "
            " CAST(:documenti AS jsonb), :hash, :durata, :pronto_il, :scade_il)"
        ), {
            "id": pdd_uuid, "tid": tenant_id, "aip": aip_uuid, "tipo": req.tipo.value,
            "consegna": risultato.consegna if risultato.consegna in ("LINK", "ZIP") else "LINK",
            "stato": risultato.stato if risultato.stato in (
                "IN_PREPARAZIONE", "PRONTO", "ERRORE") else "PRONTO",
            "rich": uuid_o_none(richiedente), "canale": canale, "motivo": req.motivo,
            "file_richiesti": json.dumps(req.file_richiesti) if req.file_richiesti else None,
            "opzioni": json.dumps(opzioni or {}),
            "documenti": json.dumps(risultato._file_interni, default=str),
            "hash": risultato.hash_pdd, "durata": durata,
            "pronto_il": ora if pronto else None,
            "scade_il": (ora + timedelta(seconds=durata)) if pronto else None,
        })
    # Evento «aip.distribuito» nella stessa transazione del registro (§12.3)
    await accoda_evento(
        session, topic=settings.kafka_topic_lifecycle, chiave=req.aip_id, tenant_id=tenant_id,
        payload=evento_distribuzione(req.aip_id, tenant_id, req.tipo.value, richiedente,
                                     pdd_uuid, req.motivo),
    )
    return pdd_uuid
