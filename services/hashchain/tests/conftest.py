# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fixtures test F1.4 Hashchain."""
import os

# Prima di importare l'app: con ENV=test la verifica dei JWT usa una coppia di
# chiavi effimera in memoria (libs/shared/security/jwt_rs256.py); in ogni altro
# ambiente senza JWT_PUBLIC_KEY il servizio non parte.
os.environ["ENV"] = "test"

import hashlib

import pytest  # noqa: E402


@pytest.fixture
def sample_hashes():
    return [hashlib.sha256(f"aip-{i}".encode()).hexdigest() for i in range(4)]


@pytest.fixture(autouse=True)
def ambito_conservatore_dei_lavoratori():
    """R05 (VD-C-080): i test chiamano direttamente le funzioni dei lavoratori del
    registro e della verifica continua, che in esercizio girano nell'ambito
    conservatore impostato all'avvio (app/main.py, verifica_continua/__main__.py,
    registro/operatore.py). Le richieste HTTP dei test sostituiscono l'ambito con
    quello del token verificato (libs/shared/security/jwt_rs256.py)."""
    from libs.shared.db.ambito import ambito_conservatore

    with ambito_conservatore("test dei lavoratori della hashchain"):
        yield


#: R17: primo giorno dei dati di prova datati che scrivono in ``audit_global`` (segmenti
#: dell'audit del 20/10/2026, giri incrementali di novembre 2026)
PRIMO_GIORNO_DATI_PROVA = (2026, 10, 1)


@pytest.fixture(scope="session", autouse=True)
def partizioni_audit_per_i_dati_di_prova():
    """R17: le partizioni di ``audit_global`` per i mesi dei dati di prova.

    La migrazione crea le partizioni del registro dal mese CORRENTE: dal mese dopo
    quello del 20/10/2026 (cioè dal 1° novembre) i dati di prova datati 20/10 non
    avevano più una partizione e 10 test dell'audit nel WORM fallivano per la sola
    data della corsa. Qui, una volta per sessione, nel database migrato che fa da
    template (prima che i test ne facciano copie), si creano i mesi dal primo giorno dei
    dati di prova fino a due mesi dopo oggi, con la stessa funzione e lo stesso ruolo
    della manutenzione in esercizio (idempotente). Il comportamento di produzione
    non cambia; senza ``VD_TEST_DATABASE_URL`` non fa nulla."""
    url = os.environ.get("VD_TEST_DATABASE_URL", "")
    if not url:
        yield
        return
    import asyncio
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import create_async_engine

    from libs.shared.db.partizioni import (
        POLITICHE,
        crea_partizione,
        inizio_periodo,
        periodo_successivo,
    )

    politica = next(p for p in POLITICHE if p.tabella == "audit_global")

    async def _crea() -> None:
        engine = create_async_engine(url)
        try:
            inizio = inizio_periodo(politica.periodo, datetime(*PRIMO_GIORNO_DATI_PROVA,
                                                                tzinfo=UTC))
            fine = datetime.now(UTC) + timedelta(days=62)
            while inizio <= fine:
                async with engine.begin() as tx:
                    await tx.exec_driver_sql("SET LOCAL ROLE verumdoc_manutenzione")
                    await crea_partizione(tx, politica, inizio)
                inizio = periodo_successivo(politica.periodo, inizio)
        finally:
            await engine.dispose()

    asyncio.run(_crea())
    yield
