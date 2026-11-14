# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Avvio del lavoratore di verifica continua come processo separato::

    python -m app.verifica_continua            # ciclo continuo (Deployment)
    python -m app.verifica_continua --un-giro  # manutenzione + un lotto, poi esce (prova)

Legge ``DATABASE_URL`` e ``S3_*`` dalle impostazioni del servizio hashchain
(``app.config``) e ``VERIFICA_*`` da ``app.verifica_continua.config``.
Metriche Prometheus su ``VERIFICA_METRICHE_PORTA`` (0 = spente). SIGTERM e
SIGINT fermano il ciclo alla fine del lotto in corso.
"""
from __future__ import annotations

import asyncio
import signal
import sys

import boto3
import botocore.config
import structlog

from app.config import settings
from app.verifica_continua.config import impostazioni
from app.verifica_continua.foglie import FonteFoglieCatena
from app.verifica_continua.lavoratore import Lavoratore
from libs.shared.db import close_db, init_db
from libs.shared.db.ambito import ambito_conservatore

logger = structlog.get_logger(__name__)


def client_s3() -> object:
    """Client S3 dello storage conservativo (in sola lettura per questo processo)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        region_name=settings.s3_region,
        config=botocore.config.Config(
            signature_version="s3v4", retries={"max_attempts": 3},
            max_pool_connections=max(10, impostazioni.concorrenza * 2)),
    )


async def principale(argomenti: list[str]) -> None:  # pragma: no cover — avvio del processo
    from prometheus_client import start_http_server

    await init_db(settings.database_url)
    lavoratore = Lavoratore(impostazioni, client=client_s3(), fonte_foglie=FonteFoglieCatena())
    if impostazioni.metriche_porta:
        start_http_server(impostazioni.metriche_porta, registry=lavoratore.metriche.registro)
    fine = asyncio.Event()
    ciclo = asyncio.get_running_loop()
    for segnale in (signal.SIGTERM, signal.SIGINT):
        try:
            ciclo.add_signal_handler(segnale, fine.set)
        except NotImplementedError:  # Windows: solo Ctrl+C con KeyboardInterrupt
            pass
    try:
        # R05 (VD-C-080): la verifica continua legge PdA e documenti di tutti gli enti
        with ambito_conservatore("verifica continua dell'integrità (tutti gli enti)"):
            if "--un-giro" in argomenti:
                await lavoratore.manutenzione(forza=True)
                await lavoratore.giro()
            else:
                await lavoratore.esegui(fine)
    finally:
        await close_db()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(principale(sys.argv[1:]))
