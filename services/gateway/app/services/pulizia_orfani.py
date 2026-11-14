# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Pulizia dei file orfani nello storage dei versamenti.

Servizio: gateway. Riferimenti: VD-C-015 (R08); REGOLE_SVILUPPO §22.3 (rete di sicurezza).

Il versamento carica i file (in streaming) PRIMA della transazione del PdV e,
se la transazione fallisce, li elimina subito (compensazione). Resta un caso:
il processo muore fra il caricamento e il commit. Questo job, sul bucket di
staging dei versamenti (MAI sul bucket conservativo dei PdA):

  - elimina gli oggetti senza PdV nel database e più vecchi di ``eta_minima``
    (così non tocca un versamento in corso), con le chiavi
      ``versamenti/{tenant}/{pdv_id}/...`` (staging R03, caricamento in streaming)
      ``{tenant}/{pdv_id}/...``             (formato precedente a R03);
  - annulla i caricamenti multipart rimasti aperti sotto ``versamenti/`` da più
    di ``eta_minima`` (processo morto durante il trasferimento: le parti
    caricate occupano spazio finché il multipart non si annulla);
  - NON tocca ``caricamenti/...``: gli oggetti delle sessioni di caricamento
    hanno il loro ciclo di vita (scadenza della sessione).

Uso: ``python -m app.services.pulizia_orfani [--prova]`` (``--prova`` elenca
senza eliminare).
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from libs.shared.db import get_async_session
from libs.shared.db.ambito import con_ambito_conservatore
from libs.shared.storage.flusso import annulla_multipart_incompleti

logger = structlog.get_logger(__name__)

ETA_MINIMA_PREDEFINITA = timedelta(hours=1)


#: Prefisso dello staging dei versamenti (stesso di ``versamento_service``).
PREFISSO_VERSAMENTI = "versamenti"


def _uuid(testo: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(testo)
    except ValueError:
        return None


def _pdv_da_chiave(chiave: str) -> uuid.UUID | None:
    """PdV a cui appartiene un oggetto di staging; None se la chiave non è del versamento."""
    parti = chiave.split("/")
    if parti[0] == PREFISSO_VERSAMENTI:
        if len(parti) < 4 or _uuid(parti[1]) is None:
            return None
        return _uuid(parti[2])
    # Formato precedente a R03: {tenant}/{pdv}/{file}. Ogni altro prefisso (es.
    # «caricamenti/») non è un UUID e si ignora.
    if len(parti) < 3 or _uuid(parti[0]) is None:
        return None
    return _uuid(parti[1])


def _elenca_oggetti(s3: Any, bucket: str) -> list[dict]:
    oggetti: list[dict] = []
    for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        oggetti.extend(pagina.get("Contents", []))
    return oggetti


@con_ambito_conservatore("pulizia dei file orfani dello staging di tutti gli enti")
async def pulisci_orfani_pdv(
    *,
    s3: Any,
    bucket: str,
    adesso: datetime,
    eta_minima: timedelta = ETA_MINIMA_PREDEFINITA,
    prova: bool = False,
) -> list[str]:
    """Elimina (o, in prova, elenca) gli oggetti orfani; restituisce le chiavi.

    Annulla anche (in prova: elenca nel log) i multipart aperti sotto ``versamenti/``."""
    aperti = await annulla_multipart_incompleti(
        s3, bucket, prefisso=f"{PREFISSO_VERSAMENTI}/", iniziati_prima_di=adesso - eta_minima,
        prova=prova,
    )
    if aperti:
        logger.info("pulizia_multipart_incompleti", bucket=bucket, num=len(aperti), prova=prova)
    oggetti = await asyncio.to_thread(_elenca_oggetti, s3, bucket)
    candidati: dict[uuid.UUID, list[str]] = defaultdict(list)
    for o in oggetti:
        pdv_id = _pdv_da_chiave(o["Key"])
        if pdv_id is None:
            continue
        if adesso - o["LastModified"] < eta_minima:
            continue
        candidati[pdv_id].append(o["Key"])
    if not candidati:
        return []

    async with get_async_session() as session:
        esistenti = {
            r[0] for r in (await session.execute(
                text("SELECT id FROM public.pdv WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": list(candidati)},
            )).fetchall()
        }
    orfani = sorted(k for pdv, chiavi in candidati.items() if pdv not in esistenti for k in chiavi)
    if orfani and not prova:
        for i in range(0, len(orfani), 1000):
            await asyncio.to_thread(
                s3.delete_objects, Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in orfani[i:i + 1000]], "Quiet": True},
            )
    logger.info("pulizia_orfani_pdv", bucket=bucket, orfani=len(orfani), prova=prova)
    return orfani


async def _main(prova: bool) -> None:  # pragma: no cover — comando operativo
    from app.config import settings
    from app.services.versamento_service import _bucket_pdv, _s3_client
    from libs.shared.db import init_db

    await init_db(settings.database_url)
    chiavi = await pulisci_orfani_pdv(
        s3=_s3_client(), bucket=_bucket_pdv(), adesso=datetime.now(UTC), prova=prova,
    )
    import sys

    sys.stdout.write("".join(f"{k}\n" for k in chiavi))


if __name__ == "__main__":  # pragma: no cover
    import sys

    asyncio.run(_main("--prova" in sys.argv))
