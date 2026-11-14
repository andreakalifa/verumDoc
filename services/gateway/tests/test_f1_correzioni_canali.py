# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f1 — correzioni della revisione indipendente sui canali del gateway.

Le prove del revisore (``r03/rev/``) sono diventate test del repository:
  - REV-01: dopo un rifiuto una UD corretta si riversa (REST, PDND, SFTP con le
    sessioni); la ripetizione di un PdV rifiutato o in errore non è
    mai positiva (409 VRS-127, NEGATIVO);
  - REV-02: la quota di PdV al giorno è atomica (N richieste parallele, Redis vero
    e finto) e si rilascia se il PdV non nasce;
  - REV-03: limite di richieste al minuto anche su PDND, comune agli altri canali;
  - REV-05: PDND in streaming (memoria misurata) e limite di richieste contemporanee;
  - REV-07: una parte la cui scrittura è fallita si reinvia;
  - REV-09: il multipart REST non si legge prima dell'autenticazione;
  - difetto 2 di c2: «allegati» annidati rifiutati (VRS-114);
  - difetto 3 di c2: i metadati minimi non si accettano in ``metadati_extra``.
Infrastruttura vera dove conta (REGOLE §23.3): PostgreSQL migrato, S3 (moto),
Redis reale per la prova dello script Lua (se ``redis-server`` è installato).
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import asyncio
import base64
import json
import time
import tracemalloc
import uuid
from datetime import UTC, datetime

import pytest
from app.auth import rate_limiter
from app.auth.dependencies import get_redis
from app.config import settings
from app.main import app
from app.routers import pdnd as pdnd_router
from app.services import caricamenti as car
from app.services.corpo import leggi_json_con_file
from app.services.quota_versamento import imposta_fornitore_redis
from httpx import ASGITransport, AsyncClient

from libs.shared.limiti_richieste.redis_di_prova import avvia_redis, redis_disponibile
from libs.shared.security.quote import chiave_pdv_giorno, chiave_richieste, svuota_cache_piani
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.redis_finto import RedisQuote
from tests.supporto_canali import (  # noqa: F401 — fixture
    MINIMI,
    PDF,
    client_http,
    indice,
    pdf_di,
    s3,
    senza_lavoratore,
    sha256,
    utente_di,
)
from tests.test_caricamenti import _apri, _carica_completo, _parte
from tests.test_pdnd import _corpo as _corpo_pdnd
from tests.test_pdnd import (
    _registra_fruitore,
    _voucher,
    chiave_pdnd,  # noqa: F401 — fixture
)

TOKEN_SFTP = {"auth_method": "token_canale", "canale": "SFTP"}


async def _rifiuta_come_validation(url: str) -> None:
    """Ciò che fa validation (consumer) per un PdV non valido: ERRORE e chiave liberata."""
    await sql(url, "UPDATE public.pdv SET stato = 'ERRORE'")
    await sql(url, "UPDATE public.versamento_impronte SET attivo = false")


def _multipart(ud: dict, contenuto: bytes) -> tuple[dict, list]:
    return ({"indice": json.dumps(ud)},
            [("files", (ud["documenti"][0]["nome_file"], contenuto, "application/pdf"))])


async def _versa_rest(c, ud: dict, contenuto: bytes = PDF, chiave: str | None = None):
    dati, files = _multipart(ud, contenuto)
    h = {"Idempotency-Key": chiave} if chiave else {}
    return await c.post("/api/v1/versamento/ud", data=dati, files=files, headers=h)


def _app_pdnd(redis=None) -> AsyncClient:
    from fastapi import FastAPI

    from libs.shared.errori import installa_gestori_errori

    prova = FastAPI()
    prova.include_router(pdnd_router.router)
    installa_gestori_errori(prova)
    prova.dependency_overrides[get_redis] = lambda: redis
    return AsyncClient(transport=ASGITransport(app=prova), base_url="http://t")


async def _versa_pdnd(c, client_id: str, corpo: dict, chiave: str | None):
    h = {"Authorization": f"Bearer {_voucher(client_id)}"}
    if chiave:
        h["Idempotency-Key"] = chiave
    return await c.post("/api/pdnd/v1/versamento", json=corpo, headers=h)


# ── REV-01: riversamento dopo un rifiuto, mai POSITIVO su un PdV rifiutato ──


@richiede_pg
@pytest.mark.integration
class TestRiversamentoDopoRifiuto:


    async def test_rest_con_chiave_ripetizione_409_vrs127_nuova_chiave_corretta_202(
            self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            r1 = await _versa_rest(c, indice(), chiave="rest-rifiuto-0001")
            assert r1.status_code == 202, r1.text
            await _rifiuta_come_validation(db_url)
            ripetuto = await _versa_rest(c, indice(), chiave="rest-rifiuto-0001")
            corretto = PDF + b"%corretto"
            r2 = await _versa_rest(c, indice(contenuto=corretto), corretto,
                                   chiave="rest-rifiuto-0002")
        assert ripetuto.status_code == 409, ripetuto.text
        corpo = ripetuto.json()
        assert corpo["codice"] == "VRS-127" and corpo["esito"] == "RIFIUTATO"
        assert corpo["dettagli"]["pdv_id_esistente"] == r1.json()["pdv_id"]
        assert corpo["dettagli"]["stato_esistente"] == "ERRORE"
        assert r2.status_code == 202, r2.text

    async def test_rest_senza_chiave_in_transizione_riversa_dopo_il_rifiuto(
            self, db_url, s3, utente_di, monkeypatch):
        """Transizione VD-P-27: la chiave derivata NON è nel vincolo unico; dopo un rifiuto
        la stessa UD (corretta o identica, per la rielaborazione) crea un PdV nuovo."""
        monkeypatch.setattr(settings, "idempotenza_transizione_fino_al",
                            datetime(2999, 1, 1).date())
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            r1 = await _versa_rest(c, indice())
            assert r1.status_code == 202, r1.text
            await _rifiuta_come_validation(db_url)
            corretto = PDF + b"%corretto"
            r2 = await _versa_rest(c, indice(contenuto=corretto), corretto)
            ripetuto = await _versa_rest(c, indice(contenuto=corretto), corretto)
        assert r2.status_code == 202, r2.text
        assert r2.json()["pdv_id"] != r1.json()["pdv_id"]
        # stesso contenuto con il PdV attivo: stessa risposta (chiave derivata)
        assert ripetuto.status_code == 200 and ripetuto.json()["pdv_id"] == r2.json()["pdv_id"]

    async def test_pdnd_ripetizione_di_pdv_rifiutato_409(self, db_url, s3, chiave_pdnd):
        tenant, _ = await crea_tenant_e_classe(db_url)
        await _registra_fruitore(db_url, "client-rifiuto", tenant)
        async with _app_pdnd() as c:
            r1 = await _versa_pdnd(c, "client-rifiuto", _corpo_pdnd(), "pdnd-rifiuto-0001")
            assert r1.status_code == 202, r1.text
            await _rifiuta_come_validation(db_url)
            ripetuto = await _versa_pdnd(c, "client-rifiuto", _corpo_pdnd(), "pdnd-rifiuto-0001")
            corretto = PDF + b"%corretto"
            r2 = await _versa_pdnd(c, "client-rifiuto", _corpo_pdnd(corretto),
                                   "pdnd-rifiuto-0002")
        assert ripetuto.status_code == 409 and ripetuto.json()["codice"] == "VRS-127"
        assert r2.status_code == 202, r2.text

    async def test_sftp_con_sessioni_ripetizione_409_mai_versata(self, db_url, s3, utente_di):
        """Canale SFTP (token di canale + sessioni + versamento per riferimento): la stessa
        chiave della cartella dopo un rifiuto riceve 409 VRS-127, non 200 «VERSATA»."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, **TOKEN_SFTP)
        async with client_http() as c:
            cid = await _carica_completo(c, PDF, chiave="sftp-sessione-0001")
            corpo = {"indice": indice("fattura.pdf", PDF), "file": {"fattura.pdf": cid}}
            r1 = await c.post("/api/v1/versamento/ud", json=corpo,
                              headers={"Idempotency-Key": "sftp-cartella-0001"})
            assert r1.status_code == 202, r1.text
            await _rifiuta_come_validation(db_url)
            ripetuto = await c.post("/api/v1/versamento/ud", json=corpo,
                                    headers={"Idempotency-Key": "sftp-cartella-0001"})
            corretto = PDF + b"%corretto"
            cid2 = await _carica_completo(c, corretto, chiave="sftp-sessione-0002")
            r2 = await c.post("/api/v1/versamento/ud", json={
                "indice": indice("fattura.pdf", corretto), "file": {"fattura.pdf": cid2}},
                headers={"Idempotency-Key": "sftp-cartella-0002"})
        assert ripetuto.status_code == 409 and ripetuto.json()["codice"] == "VRS-127"
        assert r2.status_code == 202, r2.text
        canali = [json.loads(r["metadata_json"])["canale"]
                  for r in await sql(db_url, "SELECT metadata_json FROM public.pdv")]
        assert canali == ["SFTP", "SFTP"]


# ── REV-02: quota di PdV al giorno atomica ───────────────────────────

@pytest.fixture
def redis_quote(monkeypatch):
    """Redis in memoria (script emulati atomicamente), limite di 1 PdV al giorno."""
    redis = RedisQuote()
    precedente = imposta_fornitore_redis(lambda: redis)
    monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "pdv_giorno", {"starter": 1})
    svuota_cache_piani()
    yield redis
    imposta_fornitore_redis(precedente)
    svuota_cache_piani()


@pytest.fixture(scope="module")
def redis_vero():
    """Redis reale effimero (``redis-server``), per provare gli script Lua veri."""
    if not redis_disponibile():
        pytest.skip("redis-server non installato: prova dello script Lua su Redis reale saltata")
    with avvia_redis() as url:
        yield url


@richiede_pg
@pytest.mark.integration
class TestQuotaAtomica:
    async def test_richieste_parallele_non_superano_la_quota_redis_finto(
            self, db_url, s3, utente_di, redis_quote):
        """Prova del revisore (``test_rev_quota_corsa``): prima 6 PdV su 1 ammesso."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, piano="starter")
        async with client_http() as c:
            risposte = await asyncio.gather(*(
                _versa_rest(c, indice(numero=f"PAR-{i}", contenuto=PDF + str(i).encode()),
                            PDF + str(i).encode(), chiave=f"quota-corsa-{i:04d}")
                for i in range(6)))
        codici = sorted(r.status_code for r in risposte)
        assert codici == [202, 429, 429, 429, 429, 429], codici
        assert len(await sql(db_url, "SELECT 1 FROM public.pdv")) == 1
        # R07 (D13): oltre al contatore ci sono i filtri degli avvisi (qta:avviso:…)
        assert [v for k, v in redis_quote.valori.items() if k.startswith("qta:pdv:")] == ["1"]

    async def test_richieste_parallele_su_redis_vero_script_lua(self, db_url, s3, utente_di,
                                                                 redis_vero, monkeypatch):
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_vero, decode_responses=True)
        precedente = imposta_fornitore_redis(lambda: client)
        monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "pdv_giorno", {"starter": 3})
        # R03 f3 (REV-N2): 10 multipart insieme superano il tetto predefinito (8) per
        # replica; qui si prova la quota, non il tetto di concorrenza
        monkeypatch.setattr(settings, "versamento_multipart_richieste_contemporanee_max", 100)
        svuota_cache_piani()
        try:
            tenant, _ = await crea_tenant_e_classe(db_url)
            utente_di(tenant, piano="starter")
            n = 10
            async with client_http() as c:
                risposte = await asyncio.gather(*(
                    _versa_rest(c, indice(numero=f"LUA-{i}", contenuto=PDF + str(i).encode()),
                                PDF + str(i).encode(), chiave=f"quota-lua-{i:04d}")
                    for i in range(n)))
            codici = [r.status_code for r in risposte]
            assert codici.count(202) == 3 and codici.count(429) == n - 3, codici
            assert len(await sql(db_url, "SELECT 1 FROM public.pdv")) == 3
            chiave = chiave_pdv_giorno(str(tenant), datetime.now(UTC), "Europe/Rome")
            assert await client.get(chiave) == "3"
            assert 0 < await client.ttl(chiave) <= 2 * 86400
        finally:
            imposta_fornitore_redis(precedente)
            svuota_cache_piani()
            await client.aclose()

    async def test_prenotazione_rilasciata_se_il_pdv_non_nasce(self, db_url, s3, utente_di,
                                                               redis_quote):
        """Rifiuto dopo la prenotazione (impronta errata, VRS-102) e ripetizione: la quota
        resta libera per il versamento valido successivo."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, piano="starter")
        sbagliato = indice()
        sbagliato["documenti"][0]["hash"]["impronta"] = "0" * 64
        async with client_http() as c:
            rifiutato = await _versa_rest(c, sbagliato, chiave="quota-rilascio-0001")
            buono = await _versa_rest(c, indice(), chiave="quota-rilascio-0002")
            ripetuto = await _versa_rest(c, indice(), chiave="quota-rilascio-0002")
            oltre = await _versa_rest(c, indice(numero="FT-9", contenuto=PDF + b"9"), PDF + b"9",
                                      chiave="quota-rilascio-0003")
        assert rifiutato.status_code == 400 and rifiutato.json()["codice"] == "VRS-102"
        assert buono.status_code == 202, buono.text
        assert ripetuto.status_code == 200
        assert oltre.status_code == 429 and oltre.json()["codice"] == "QTA-002"
        # R07 (D13): oltre al contatore ci sono i filtri degli avvisi (qta:avviso:…)
        assert [v for k, v in redis_quote.valori.items() if k.startswith("qta:pdv:")] == ["1"]

    async def test_redis_non_raggiungibile_fail_open(self, db_url, s3, utente_di, monkeypatch):
        """Decisione (coerente con b4): Redis giù → versamento ammesso con avviso nel log."""
        guasto = RedisQuote(guasto=True)
        precedente = imposta_fornitore_redis(lambda: guasto)
        try:
            tenant, _ = await crea_tenant_e_classe(db_url)
            utente_di(tenant, piano="starter")
            async with client_http() as c:
                r = await _versa_rest(c, indice(), chiave="quota-guasto-0001")
            assert r.status_code == 202, r.text
        finally:
            imposta_fornitore_redis(precedente)


# ── REV-03: limite al minuto su PDND, comune a tutti i canali ────────

@richiede_pg
@pytest.mark.integration
async def test_pdnd_limite_al_minuto_comune_con_gli_altri_canali(db_url, s3, chiave_pdnd,
                                                                monkeypatch):
    monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "richieste_minuto", {"starter": 2})
    svuota_cache_piani()
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-rpm", tenant)
    redis = RedisQuote()
    # Una richiesta dello stesso ente da un altro canale (REST, sessioni, adattatori, SFTP:
    # tutti passano da get_current_user, che usa la stessa finestra per ente)
    redis.zset[chiave_richieste(str(tenant))] = {"rest-1": time.time()}
    async with _app_pdnd(redis) as c:
        prima = await _versa_pdnd(c, "client-rpm", _corpo_pdnd(), "pdnd-rpm-0001")
        seconda = await _versa_pdnd(c, "client-rpm", _corpo_pdnd(numero="2",
                                                                id_documento="d2"),
                                    "pdnd-rpm-0002")
        consultazione = await c.get(f"/api/pdnd/v1/aip/{uuid.uuid4()}",
                                    headers={"Authorization": f"Bearer {_voucher('client-rpm')}"})
    assert prima.status_code == 202, prima.text
    assert seconda.status_code == 429 and seconda.json()["codice"] == "QTA-001"
    assert int(seconda.headers["Retry-After"]) >= 1
    assert consultazione.status_code == 429  # anche gli e-service di consultazione
    # stessa finestra per ente: la richiesta dell'altro canale più le tre PDND (ogni
    # richiesta entra nella finestra, anche se rifiutata: semantica di b4)
    assert len(redis.zset[chiave_richieste(str(tenant))]) == 4
    svuota_cache_piani()


# ── REV-05: PDND in streaming, memoria e richieste contemporanee ─────

class _RichiestaFinta:
    def __init__(self, corpo: bytes, passo: int = 64 * 1024) -> None:
        self._corpo, self._passo = corpo, passo
        self.headers = {"content-length": str(len(corpo))}

    async def stream(self):
        for i in range(0, len(self._corpo), self._passo):
            yield self._corpo[i:i + self._passo]


async def test_pdnd_streaming_memoria_limitata_e_contenuto_identico():
    """Stessa misura della prova del revisore (``prova_memoria_pdnd.py``): prima il picco
    era ~2,8 volte il file (140 MB per 50 MB); ora resta sotto pochi MB."""
    import os

    mb = 20
    dati = os.urandom(mb * 1024 * 1024)
    corpo = json.dumps({**_corpo_pdnd(), "file_b64": base64.b64encode(dati).decode()}).encode()
    richiesta = _RichiestaFinta(corpo)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        letto = await leggi_json_con_file(
            richiesta, campo="file_b64", limite_corpo=len(corpo) + 1, limite_altri=4 << 20,
            limite_file=(mb + 1) << 20, codice_corpo="PDND-413", codice_file="PDND-413",
            codice_base64="PDND-422")
        _, picco = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert picco < 8 * 1024 * 1024, f"picco {picco / 2**20:.1f} MB"
        assert letto.file is not None and letto.file.dimensione == len(dati)
        ricostruito = b"".join([b async for b in letto.file.blocchi()])
        assert ricostruito == dati
        assert json.loads(letto.json)["file_b64"] == ""
    finally:
        letto.chiudi()


async def test_pdnd_streaming_errori_base64_e_limite():
    def corpo(valore: str) -> bytes:
        return json.dumps({"x": 1, "file_b64": valore}).encode()

    async def leggi(dati: bytes, limite_file: int = 1 << 20):
        return await leggi_json_con_file(
            _RichiestaFinta(dati, 3), campo="file_b64", limite_corpo=1 << 20,
            limite_altri=1 << 20, limite_file=limite_file, codice_corpo="PDND-413",
            codice_file="PDND-413", codice_base64="PDND-422")

    from libs.shared.errori import ErroreApi

    for valore in ("QQ=", "QQ=A", "Q Q==", "QUJD\n"):
        with pytest.raises(ErroreApi) as e:
            await leggi(corpo(valore))
        assert e.value.codice == "PDND-422", valore
    with pytest.raises(ErroreApi) as e:
        await leggi(corpo(base64.b64encode(b"x" * 5000).decode()), limite_file=4000)
    assert e.value.codice == "PDND-413"
    # «\/» è un modo valido di scrivere «/» in JSON
    ok = await leggi(json.dumps({"file_b64": base64.b64encode(b"\xff\xff\xff").decode()})
                     .replace("/", "\\/").encode())
    assert b"".join([b async for b in ok.file.blocchi()]) == b"\xff\xff\xff"
    ok.chiudi()


@richiede_pg
@pytest.mark.integration
async def test_pdnd_oltre_le_richieste_contemporanee_503_ritentabile(db_url, s3, chiave_pdnd,
                                                                    monkeypatch):
    monkeypatch.setattr(settings, "pdnd_richieste_contemporanee_max", 1)
    tenant, _ = await crea_tenant_e_classe(db_url)
    await _registra_fruitore(db_url, "client-occupato", tenant)
    async with _app_pdnd() as c:
        with pdnd_router.LIMITE_VERSAMENTI_PDND.posto():   # un versamento già in corso
            occupato = await _versa_pdnd(c, "client-occupato", _corpo_pdnd(), "pdnd-occ-0001")
        libero = await _versa_pdnd(c, "client-occupato", _corpo_pdnd(), "pdnd-occ-0001")
    assert occupato.status_code == 503, occupato.text
    assert occupato.json()["codice"] == "PDND-503-OCCUPATO"
    assert occupato.json()["ritentabile"] is True and int(occupato.headers["Retry-After"]) >= 1
    assert libero.status_code == 202, libero.text
    assert pdnd_router.LIMITE_VERSAMENTI_PDND.in_corso == 0


# ── REV-07: parte con scrittura fallita reinviabile ──────────────────

class _StorageGiu:
    def __init__(self, vero):
        self._vero = vero

    def __getattr__(self, nome):
        if nome == "upload_part":
            def _rotto(**kw):
                raise ConnectionError("storage giù")
            return _rotto
        return getattr(self._vero, nome)


@richiede_pg
@pytest.mark.integration
class TestParteReinviabile:
    async def test_parte_fallita_su_storage_si_reinvia(self, db_url, s3, utente_di,
                                                       senza_lavoratore, monkeypatch):
        """Prova del revisore (``test_rev_parte_bloccata``): prima 409 UPL-004 per sempre."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        contenuto = pdf_di(1024)
        originale = car._s3_client
        async with client_http() as c:
            aperta = await _apri(c, contenuto)
            assert aperta.status_code == 201, aperta.text
            cid = aperta.json()["caricamento_id"]
            monkeypatch.setattr(car, "_s3_client", lambda: _StorageGiu(originale()))
            primo = await _parte(c, cid, 1, b"X" * len(contenuto))
            monkeypatch.setattr(car, "_s3_client", originale)
            secondo = await _parte(c, cid, 1, contenuto)
            completa = await c.post(f"/api/v1/caricamenti/{cid}/completa")
        assert primo.status_code == 503 and primo.json()["codice"] == "UPL-503"
        assert secondo.status_code == 200, secondo.text
        assert secondo.json()["ricevute"] == [1]
        assert completa.status_code == 202
        await car.verifica_in_sospeso()
        [riga] = await sql(db_url, "SELECT stato FROM public.caricamenti")
        assert riga["stato"] == "COMPLETO"

    async def test_prenotazione_abbandonata_si_sostituisce_recente_503(
            self, db_url, s3, utente_di, senza_lavoratore, monkeypatch):
        """Processo morto fra prenotazione e scrittura: la riga senza ETag resta. Recente
        con byte diversi → 503 ritentabile; oltre il limite → sostituita."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        contenuto = pdf_di(1024)
        async with client_http() as c:
            cid = (await _apri(c, contenuto)).json()["caricamento_id"]
            await sql(db_url, "INSERT INTO public.caricamenti_parti (caricamento_id, numero, "
                              "tenant_id, dimensione, sha256, ricevuta_il) VALUES ($1, 1, $2, "
                              "$3, $4, now())", uuid.UUID(cid), tenant, len(contenuto),
                      "f" * 64)
            recente = await _parte(c, cid, 1, contenuto)
            await sql(db_url, "UPDATE public.caricamenti_parti SET ricevuta_il = now() - "
                              "interval '1 hour'")
            dopo = await _parte(c, cid, 1, contenuto)
            diversa = await _parte(c, cid, 1, b"Y" * len(contenuto))
        assert recente.status_code == 503 and recente.json()["ritentabile"] is True
        assert dopo.status_code == 200, dopo.text
        # parte ricevuta (ETag registrato) con byte diversi: resta il 409 del contratto C1
        assert diversa.status_code == 409 and diversa.json()["codice"] == "UPL-004"


# ── REV-09: multipart REST non letto prima dell'autenticazione ───────

async def test_multipart_non_autenticato_401_senza_leggere_il_corpo():
    letti: list[int] = []

    async def corpo():
        for _ in range(50):
            letti.append(1)
            yield b"x" * 65536

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/versamento/ud", content=corpo(),
                         headers={"Content-Type": "multipart/form-data; boundary=zzz"})
    assert r.status_code == 401, r.text
    assert letti == [], f"letti {len(letti)} blocchi del corpo prima della 401"


@richiede_pg
@pytest.mark.integration
async def test_multipart_chunked_oltre_il_limite_413(db_url, s3, utente_di, monkeypatch):
    monkeypatch.setattr(settings, "versamento_rest_max_corpo_mb", 1)
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    confine = "zzzconfine"
    testa = (f"--{confine}\r\nContent-Disposition: form-data; name=\"indice\"\r\n\r\n"
             f"{json.dumps(indice())}\r\n--{confine}\r\nContent-Disposition: form-data; "
             f"name=\"files\"; filename=\"fattura.pdf\"\r\nContent-Type: application/pdf"
             f"\r\n\r\n").encode()
    letti: list[int] = []

    async def corpo():  # senza Content-Length: chunked
        yield testa
        for _ in range(40):
            letti.append(1)
            yield b"x" * 65536

    async with client_http() as c:
        r = await c.post("/api/v1/versamento/ud", content=corpo(),
                         headers={"Content-Type": f"multipart/form-data; boundary={confine}",
                                  "Idempotency-Key": "chunked-oltre-0001"})
    assert r.status_code == 413 and r.json()["codice"] == "UPL-413", r.text
    assert len(letti) < 40
    assert await sql(db_url, "SELECT 1 FROM public.pdv") == []


# ── Difetti 2 e 3 del rapporto c2 ────────────────────────────────────

@richiede_pg
@pytest.mark.integration
class TestIndiceDifettiC2:
    async def test_allegati_annidati_rifiutati_vrs114(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        ud = indice()
        ud["documenti"][0]["allegati"] = [{
            "id": "a1", "tipo": "ALLEGATO", "nome_file": "allegato.pdf",
            "hash": {"impronta": sha256(PDF), "algoritmo": "SHA-256"}}]
        vuoto = indice(numero="FT-2")
        vuoto["documenti"][0]["allegati"] = []
        async with client_http() as c:
            r = await _versa_rest(c, ud, chiave="annidati-0001")
            ok = await _versa_rest(c, vuoto, chiave="annidati-0002")
        assert r.status_code == 400 and r.json()["codice"] == "VRS-114", r.text
        assert r.json()["dettagli"]["documenti"] == ["doc-001"]
        assert ok.status_code == 202, ok.text   # elenco vuoto: nessun documento perso

    async def test_minimi_in_metadati_extra_non_sostituiscono_rgl101(self, db_url, s3,
                                                                    utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        ud = indice()
        parametri = dict(MINIMI)
        oggetto = parametri.pop("oggetto")
        parametri["metadati_extra"] = {"oggetto": oggetto}
        ud["documenti"][0]["parametri"] = parametri
        async with client_http() as c:
            r = await _versa_rest(c, ud, chiave="minimi-extra-0001")
        assert r.status_code == 422 and r.json()["codice"] == "VRS-130", r.text
        [rilievo] = r.json()["dettagli"]["rilievi"]
        assert rilievo["codice"] == "RGL-101" and rilievo["campo"] == "oggetto"
