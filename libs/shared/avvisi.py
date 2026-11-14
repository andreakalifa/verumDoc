# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/avvisi.py — avvisi automatici agli enti (R07).

Riferimenti: decisioni D13 (quota all'80% e al 100%), D17 (versamenti senza
Idempotency-Key); REGOLE_SVILUPPO §12.3 (outbox transazionale), §12.1.

Un avviso automatico è UN evento per il cliente (outbox, topic = tipo
dell'evento: elenco degli eventi, webhook) che notification trasforma anche in
e-mail al referente dell'ente. Si registra una sola volta per (tipo, ente,
chiave) in ``avvisi_automatici`` NELLA STESSA transazione dell'evento: un
secondo tentativo (altra replica, ritentativo) non duplica nulla.

Tipi (catalogo degli eventi di notification):
  quota.soglia_raggiunta        chiave = giorno + quota + soglia (es. 2026-10-30:PDV:80)
  idempotenza.chiave_omessa     chiave = settimana ISO (es. 2026-W44)
  ciclo_vita.avviso             R09: ``sottotipo`` nel payload (PROPOSTA_SCARTO,
                                RIPRESA_SCARTO, ANNULLAMENTO_DA_APPROVARE,
                                ANNULLAMENTO_RESPINTO, PRATICA_RIPRESA);
                                chiave = sottotipo + pratica (+ giro)
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)

TIPO_QUOTA_SOGLIA = "quota.soglia_raggiunta"
TIPO_IDEMPOTENZA_MANCANTE = "idempotenza.chiave_omessa"
#: R09 (D20, D21, D23): avvisi del ciclo di vita (un topic, sottotipo nel payload)
TIPO_CICLO_VITA = "ciclo_vita.avviso"
TIPI_AVVISO: tuple[str, ...] = (TIPO_QUOTA_SOGLIA, TIPO_IDEMPOTENZA_MANCANTE, TIPO_CICLO_VITA)


async def registra_avviso(session: Any, *, tipo: str, tenant_id: str | uuid.UUID, chiave: str,
                          dati: Mapping[str, Any]) -> bool:
    """Registra l'avviso e accoda l'evento nella transazione di ``session``.

    Restituisce False se lo stesso avviso c'era già (nessun evento)."""
    from libs.shared.outbox import accoda_evento

    if tipo not in TIPI_AVVISO:
        raise ValueError(f"tipo di avviso sconosciuto: {tipo}")
    tid = str(uuid.UUID(str(tenant_id)))
    nuovo = (await session.execute(text(
        "INSERT INTO public.avvisi_automatici (tipo, tenant_id, chiave, dati) "
        "VALUES (:tipo, CAST(:t AS uuid), :k, CAST(:d AS jsonb)) "
        "ON CONFLICT ON CONSTRAINT uq_avvisi_automatici DO NOTHING RETURNING id::text"),
        {"tipo": tipo, "t": tid, "k": chiave[:100], "d": json.dumps(dict(dati), default=str)},
    )).scalar()
    if nuovo is None:
        return False
    await accoda_evento(session, topic=tipo, chiave=f"{tid}:{chiave}", tenant_id=tid,
                        payload={"tipo": tipo, "avviso_id": nuovo, "tenant_id": tid,
                                 "chiave": chiave, **dict(dati)})
    logger.info("avviso_automatico", tipo=tipo, tenant_id=tid, chiave=chiave)
    return True


__all__ = ["TIPI_AVVISO", "TIPO_CICLO_VITA", "TIPO_IDEMPOTENZA_MANCANTE", "TIPO_QUOTA_SOGLIA",
           "registra_avviso"]
