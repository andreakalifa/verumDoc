# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test del limitatore di richieste dei servizi pubblici (R03 f2, REV-03) su Redis
VERO (``redis-server`` avviato su una porta libera; test saltati se assente).
"""
from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from libs.shared.errori import ErroreApi, installa_gestori_errori
from libs.shared.limiti_richieste import (
    ImpostazioniLimiti,
    LimitatoreRichieste,
    controlla_configurazione,
    soggetto,
)
from libs.shared.limiti_richieste.redis_di_prova import avvia_redis, porta_chiusa, redis_disponibile
from libs.shared.security.quote import ImpostazioniQuote

pytestmark = pytest.mark.skipif(not redis_disponibile(),
                                reason="redis-server non installato")


@pytest.fixture(scope="module")
def url_redis() -> Iterator[str]:
    with avvia_redis() as url:
        yield url


def _quote(**limiti: int) -> ImpostazioniQuote:
    return ImpostazioniQuote(richieste_minuto=limiti or {"starter": 3, "business": 5},
                             pdv_giorno={"starter": 1}, piano_predefinito="starter")


def _piano_fisso(piano: str):
    async def piano_di(_ente: object, _imp: ImpostazioniQuote) -> str:
        return piano
    return piano_di


@pytest.fixture
async def limitatore(url_redis: str) -> AsyncIterator[LimitatoreRichieste]:
    lim = LimitatoreRichieste(f"prova-{uuid.uuid4().hex[:6]}", quote=_quote(),
                              impostazioni=ImpostazioniLimiti(),
                              piano_di=_piano_fisso("starter"), redis_url=url_redis, env="test")
    try:
        yield lim
    finally:
        await lim.chiudi()


async def test_consuma_oltre_il_limite_del_piano_qta001_con_retry_after(limitatore) -> None:
    """Piano starter da 3 richieste al minuto: la quarta riceve QTA-001 (429) con
    Retry-After fra 1 e 60 secondi e il piano nei dettagli."""
    identita = {"tenant_id": str(uuid.uuid4()), "ruolo": "API_SYSTEM"}
    for _ in range(3):
        await limitatore.consuma(identita)
    with pytest.raises(ErroreApi) as exc:
        await limitatore.consuma(identita)
    errore = exc.value
    assert errore.codice == "QTA-001" and errore.status == 429
    assert 1 <= int(errore.headers["Retry-After"]) <= 60
    assert errore.dettagli == {"limite": 3, "piano": "starter"}


async def test_consuma_enti_e_servizi_hanno_contatori_separati(limitatore, url_redis) -> None:
    """Un ente che esaurisce il limite non tocca un altro ente né lo stesso ente su
    un altro servizio (contatore per servizio)."""
    a, b = {"tenant_id": str(uuid.uuid4())}, {"tenant_id": str(uuid.uuid4())}
    for _ in range(3):
        await limitatore.consuma(a)
    with pytest.raises(ErroreApi):
        await limitatore.consuma(a)
    await limitatore.consuma(b)
    altro = LimitatoreRichieste("altro-servizio", quote=_quote(),
                                piano_di=_piano_fisso("starter"), redis_url=url_redis, env="test")
    try:
        await altro.consuma(a)
    finally:
        await altro.chiudi()


async def test_consuma_ambito_ente_usa_il_contatore_del_gateway(url_redis) -> None:
    """Con ambito «ente» la chiave è quella del gateway (``rl:rpm:<ente>``)."""
    lim = LimitatoreRichieste("x", quote=_quote(), piano_di=_piano_fisso("starter"),
                              impostazioni=ImpostazioniLimiti(ambito="ente"),
                              redis_url=url_redis, env="test")
    ente = str(uuid.uuid4())
    try:
        await lim.consuma({"tenant_id": ente})
        assert lim.chiave(ente) == f"rl:rpm:{ente}"
        client = lim.client_redis()
        assert await client.zcard(f"rl:rpm:{ente}") == 1
    finally:
        await lim.chiudi()


async def test_consuma_piano_dell_ente_cambia_il_limite(url_redis) -> None:
    """Il limite viene dal piano dell'ente (qui business = 5), non dal token."""
    lim = LimitatoreRichieste("y", quote=_quote(), piano_di=_piano_fisso("business"),
                              redis_url=url_redis, env="test")
    identita = {"tenant_id": str(uuid.uuid4()), "piano": "starter"}
    try:
        for _ in range(5):
            await lim.consuma(identita)
        with pytest.raises(ErroreApi) as exc:
            await lim.consuma(identita)
        assert exc.value.dettagli["piano"] == "business"
    finally:
        await lim.chiudi()


async def test_consuma_categoria_limite_separato(url_redis) -> None:
    """La categoria ha un limite proprio e un contatore proprio; il dettaglio dice
    quale categoria è esaurita."""
    lim = LimitatoreRichieste(
        "z", quote=_quote(starter=100), piano_di=_piano_fisso("starter"),
        impostazioni=ImpostazioniLimiti(categorie={"ricerca": {"starter": 2}}),
        redis_url=url_redis, env="test")
    identita = {"tenant_id": str(uuid.uuid4())}
    try:
        await lim.consuma(identita, categoria="ricerca")
        await lim.consuma(identita, categoria="ricerca")
        with pytest.raises(ErroreApi) as exc:
            await lim.consuma(identita, categoria="ricerca")
        assert exc.value.codice == "QTA-001"
        assert exc.value.dettagli["categoria"] == "ricerca"
        await lim.consuma(identita)                        # il limite generale è intatto
        await lim.consuma(identita, categoria="sconosciuta")  # senza limite: passa
    finally:
        await lim.chiudi()


async def test_consuma_finestra_scorrevole_libera_dopo_un_minuto(limitatore) -> None:
    """Le richieste uscite dalla finestra di 60 s non contano più."""
    identita = {"tenant_id": str(uuid.uuid4())}
    inizio = time.time()
    for _ in range(3):
        await limitatore.consuma(identita, adesso=inizio)
    with pytest.raises(ErroreApi):
        await limitatore.consuma(identita, adesso=inizio + 1)
    await limitatore.consuma(identita, adesso=inizio + 62)


async def test_consuma_redis_non_raggiungibile_la_richiesta_passa() -> None:
    """Redis giù: nessun 500 e nessun blocco (avviso nel log, come nel gateway)."""
    lim = LimitatoreRichieste("guasto", quote=_quote(), piano_di=_piano_fisso("starter"),
                              redis_url=porta_chiusa(), env="test")
    try:
        for _ in range(5):
            await lim.consuma({"tenant_id": str(uuid.uuid4())})
    finally:
        await lim.chiudi()


def test_controlla_configurazione_produzione_senza_redis_blocca() -> None:
    assert controlla_configurazione(env="production", redis_url="") != []
    assert controlla_configurazione(env="production", redis_url="redis://r:6379/0",
                                    attivi=False) != []
    assert controlla_configurazione(env="production", redis_url="redis://r:6379/0") == []
    assert controlla_configurazione(env="development", redis_url="") == []
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        LimitatoreRichieste("p", redis_url="", env="production")


def test_soggetto_ente_o_utente_globale() -> None:
    ente = str(uuid.uuid4())
    utente = str(uuid.uuid4())
    assert soggetto({"tenant_id": ente, "user_id": utente}) == ente
    assert soggetto({"tenant_id": None, "user_id": utente}) == f"utente:{utente}"

    class Identita:
        tenant_id = ente
    assert soggetto(Identita()) == ente


def test_dipendenza_fastapi_429_problem_json(url_redis) -> None:
    """Dipendenza a livello di router: l'autenticazione del servizio gira una volta
    sola per richiesta (anche se la rotta la richiede di nuovo) e la quarta
    richiesta riceve 429 QTA-001 in problem+json con Retry-After."""
    ente = str(uuid.uuid4())
    chiamate = {"n": 0}

    async def autentica() -> dict:
        chiamate["n"] += 1
        return {"tenant_id": ente}

    lim = LimitatoreRichieste("dip", quote=_quote(), piano_di=_piano_fisso("starter"),
                              redis_url=url_redis, env="test")
    app = FastAPI(dependencies=[Depends(lim.dipendenza(autentica))])
    installa_gestori_errori(app)

    @app.get("/x")
    async def x(identita: dict = Depends(autentica)) -> dict:
        return identita

    with TestClient(app) as client:
        risposte = [client.get("/x") for _ in range(4)]
    assert [r.status_code for r in risposte] == [200, 200, 200, 429]
    assert chiamate["n"] == 4
    ultima = risposte[-1]
    assert ultima.headers["content-type"].startswith("application/problem+json")
    assert ultima.json()["codice"] == "QTA-001" and int(ultima.headers["Retry-After"]) >= 1


async def test_quota_dell_ente_prevale_sul_piano(url_redis) -> None:
    """R03 i3: stessa fonte del gateway (``limiti_ente``): la quota propria dell'ente
    (qui 2) prevale sul piano (business = 5); le categorie restano per piano."""
    from libs.shared.security.quote import LimitiEnte

    async def limiti_di(_ente: object, _imp: ImpostazioniQuote) -> LimitiEnte:
        return LimitiEnte("business", richieste_minuto_ente=2)

    lim = LimitatoreRichieste("z-ente", quote=_quote(), limiti_di=limiti_di,
                              redis_url=url_redis, env="test")
    identita = {"tenant_id": str(uuid.uuid4())}
    try:
        await lim.consuma(identita)
        await lim.consuma(identita)
        with pytest.raises(ErroreApi) as exc:
            await lim.consuma(identita)
        assert exc.value.dettagli == {"limite": 2, "piano": "business"}
    finally:
        await lim.chiudi()


def test_fonte_predefinita_e_quella_del_gateway() -> None:
    """Senza ``limiti_di`` né ``piano_di`` il limitatore usa ``limiti_ente`` di
    ``libs.shared.security.quote``, la stessa funzione del gateway."""
    from libs.shared.security.quote import limiti_ente

    lim = LimitatoreRichieste("fonte", quote=_quote(), redis_url="", env="test")
    assert lim._limiti_di is limiti_ente
