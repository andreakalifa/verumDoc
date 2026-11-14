# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 i1 — quota di PdV al giorno e canale effettivo per OGNI canale che crea un PdV.

La quota (VD-C-099) si applica nel caso d'uso unico ``versa`` (vedi
``app/services/quota_versamento.py``): qui si prova, con le rotte reali del
gateway su PostgreSQL e S3 (moto), che vale per REST multipart, REST per
riferimento (sessione di caricamento), PDND e per il token del canale SFTP; che
una richiesta ripetuta con la stessa Idempotency-Key riceve lo stesso PdV anche a
quota esaurita; che ogni rotta registra il canale effettivo (contratto C2) e che
un token di canale non vale sulle rotte di un altro canale (403 AUTH-010).
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from app.auth import rate_limiter
from app.services.quota_versamento import imposta_fornitore_redis, registra_pdv_versamento
from app.services.versamento_service import Canale, IdentitaVersante

from libs.shared.security.quote import svuota_cache_piani
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.redis_finto import RedisQuote
from tests.supporto_canali import (  # noqa: F401 — fixture
    PDF,
    chiavi_staging,
    client_http,
    indice,
    s3,
    senza_lavoratore,
    sha256,
    utente_di,
)
from tests.test_caricamenti import _carica_completo
from tests.test_pdnd import _client as _client_pdnd
from tests.test_pdnd import _corpo as _corpo_pdnd
from tests.test_pdnd import (
    _registra_fruitore,
    _versa_pdnd,
    chiave_pdnd,  # noqa: F401 — fixture usata come parametro
)

TOKEN_SFTP = {"auth_method": "token_canale", "canale": "SFTP"}


@pytest.fixture
def redis_quote(monkeypatch):
    """Redis in memoria per le quote del caso d'uso, con limite di 1 PdV al giorno."""
    redis = RedisQuote()
    precedente = imposta_fornitore_redis(lambda: redis)
    monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "pdv_giorno", {"starter": 1})
    svuota_cache_piani()
    yield redis
    imposta_fornitore_redis(precedente)
    svuota_cache_piani()


def _multipart(ud: dict, contenuto: bytes = PDF) -> tuple[dict, list]:
    return ({"indice": json.dumps(ud)},
            [("files", (ud["documenti"][0]["nome_file"], contenuto, "application/pdf"))])


async def _versa_multipart(c, ud: dict, chiave: str, contenuto: bytes = PDF):
    dati, files = _multipart(ud, contenuto)
    return await c.post("/api/v1/versamento/ud", data=dati, files=files,
                        headers={"Idempotency-Key": chiave})


def _quota_429(r) -> None:
    assert r.status_code == 429, r.text
    assert r.json()["codice"] == "QTA-002"
    assert 1 <= int(r.headers["Retry-After"]) <= 86400 + 3600


async def _canali_pdv(url: str) -> list[str]:
    righe = await sql(url, "SELECT metadata_json FROM public.pdv ORDER BY created_at")
    return [json.loads(r["metadata_json"])["canale"] for r in righe]


# ── Unità ───────────────────────────────────────────────────────────

async def test_registra_conta_per_ente_e_giorno():
    redis = RedisQuote()
    precedente = imposta_fornitore_redis(lambda: redis)
    try:
        import uuid

        identita = IdentitaVersante(uuid.uuid4(), uuid.uuid4(), Canale.REST, "starter")
        adesso = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)  # 00:30 del 22 in Italia
        assert await registra_pdv_versamento(identita, adesso) == 1
        assert await registra_pdv_versamento(identita, adesso) == 2
        [chiave] = list(redis.valori)
        assert chiave.endswith(":20261022")
    finally:
        imposta_fornitore_redis(precedente)


def test_identita_da_utente_applica_il_canale_effettivo():
    from app.services.versamento_service import identita_da_utente

    from libs.shared.errori import ErroreApi

    base = {"tenant_id": "00000000-0000-0000-0000-000000000001",
            "user_id": "00000000-0000-0000-0000-000000000002", "piano": "business"}
    assert identita_da_utente(base, Canale.REST).canale is Canale.REST
    assert identita_da_utente(base, Canale.REST).piano == "business"
    assert identita_da_utente({**base, **TOKEN_SFTP}, Canale.REST).canale is Canale.SFTP
    # un claim «canale» in un JWT utente non cambia il canale
    assert identita_da_utente({**base, "canale": "SFTP"}, Canale.REST).canale is Canale.REST
    for rotta in (Canale.PDND, Canale.PORTALE):
        with pytest.raises(ErroreApi) as errore:
            identita_da_utente({**base, **TOKEN_SFTP}, rotta)
        assert errore.value.codice == "AUTH-010"


# ── Con PostgreSQL e S3 ─────────────────────────────────────────────

@richiede_pg
@pytest.mark.integration
class TestQuotaPerOgniCanale:
    async def test_rest_multipart_quota_e_ripetizione(self, db_url, s3, utente_di, redis_quote):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, piano="starter")
        async with client_http() as c:
            primo = await _versa_multipart(c, indice(numero="Q-1"), "quota-rest-0001")
            secondo = await _versa_multipart(c, indice(numero="Q-2"), "quota-rest-0002")
            ripetuto = await _versa_multipart(c, indice(numero="Q-1"), "quota-rest-0001")
        assert primo.status_code == 202, primo.text
        _quota_429(secondo)
        # la stessa richiesta ripetuta non riceve 429: stesso PdV
        assert ripetuto.status_code == 200 and ripetuto.json()["pdv_id"] == primo.json()["pdv_id"]
        assert await _canali_pdv(db_url) == ["REST"]
        # il secondo è fermato prima del trasferimento: nello staging solo il primo
        assert all(f"/{primo.json()['pdv_id']}/" in k for k in chiavi_staging(s3))

    async def test_quota_per_ente_altro_ente_non_toccato(self, db_url, s3, utente_di,
                                                         redis_quote):
        t1, _ = await crea_tenant_e_classe(db_url)
        t2, _ = await crea_tenant_e_classe(db_url)
        async with client_http() as c:
            utente_di(t1)
            assert (await _versa_multipart(c, indice(numero="E-1"), "quota-ente-0001")
                    ).status_code == 202
            _quota_429(await _versa_multipart(c, indice(numero="E-2"), "quota-ente-0002"))
            utente_di(t2)
            assert (await _versa_multipart(c, indice(numero="E-1"), "quota-ente-0003")
                    ).status_code == 202

    async def test_piano_letto_dal_database_se_il_canale_non_lo_porta(self, db_url, s3,
                                                                       utente_di, redis_quote,
                                                                       monkeypatch):
        monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "pdv_giorno",
                            {"starter": 1, "business": 2})
        tenant, _ = await crea_tenant_e_classe(db_url)
        await sql(db_url, "UPDATE public.tenants SET piano = 'business' WHERE id = $1", tenant)
        utente_di(tenant)  # nessun «piano» nell'identità
        async with client_http() as c:
            stati = [(await _versa_multipart(c, indice(numero=f"P-{i}"), f"quota-piano-000{i}")
                      ).status_code for i in range(3)]
        assert stati == [202, 202, 429]

    async def test_per_riferimento_quota_e_sessione_non_consumata(self, db_url, s3, utente_di,
                                                                   redis_quote):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            cid1 = await _carica_completo(c, PDF)
            cid2 = await _carica_completo(c, PDF + b"%2", nome="altro.pdf")
            r1 = await c.post("/api/v1/versamento/ud", json={
                "indice": indice("fattura.pdf", PDF, numero="R-1"),
                "file": {"fattura.pdf": cid1}}, headers={"Idempotency-Key": "quota-rif-0001"})
            r2 = await c.post("/api/v1/versamento/ud", json={
                "indice": indice("altro.pdf", PDF + b"%2", numero="R-2"),
                "file": {"altro.pdf": cid2}}, headers={"Idempotency-Key": "quota-rif-0002"})
            stato2 = (await c.get(f"/api/v1/caricamenti/{cid2}")).json()
        assert r1.status_code == 202, r1.text
        _quota_429(r2)
        assert stato2["stato"] == "COMPLETO" and stato2["pdv_id"] is None

    async def test_token_canale_sftp_quota_e_canale(self, db_url, s3, utente_di, redis_quote):
        """Il canale SFTP passa dal REST (sessioni + per riferimento) con il suo token."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, **TOKEN_SFTP)
        async with client_http() as c:
            cid = await _carica_completo(c, PDF)
            r = await c.post("/api/v1/versamento/ud", json={
                "indice": indice("fattura.pdf", PDF, numero="S-1"),
                "file": {"fattura.pdf": cid}}, headers={"Idempotency-Key": "quota-sftp-0001"})
            multipart = await _versa_multipart(c, indice(numero="S-2"), "quota-sftp-0002")
        assert r.status_code == 202, r.text
        _quota_429(multipart)
        assert await _canali_pdv(db_url) == ["SFTP"]

    async def test_pdnd_quota(self, db_url, s3, chiave_pdnd, redis_quote):
        tenant, _ = await crea_tenant_e_classe(db_url)
        await _registra_fruitore(db_url, "client-quota", tenant)
        async with await _client_pdnd() as c:
            r1 = await _versa_pdnd(c, "client-quota", _corpo_pdnd(numero="1"),
                                   chiave="pdnd-quota-0001")
            r2 = await _versa_pdnd(c, "client-quota", _corpo_pdnd(numero="2",
                                                                    id_documento="doc-2"),
                                   chiave="pdnd-quota-0002")
            ripetuto = await _versa_pdnd(c, "client-quota", _corpo_pdnd(numero="1"),
                                         chiave="pdnd-quota-0001")
        assert r1.status_code == 202, r1.text
        _quota_429(r2)
        assert ripetuto.status_code == 200
        assert await _canali_pdv(db_url) == ["PDND"]


@richiede_pg
@pytest.mark.integration
class TestCanaleEffettivoSulleRotte:

    async def test_multipart_con_token_sftp_registra_sftp(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, **TOKEN_SFTP)
        async with client_http() as c:
            r = await _versa_multipart(c, indice(numero="C-1"), "canale-mp-0001")
        assert r.status_code == 202, r.text
        assert await _canali_pdv(db_url) == ["SFTP"]
        [evento] = await sql(db_url, "SELECT payload FROM public.outbox_eventi "
                                     "WHERE topic = 'pdv.received'")
        assert json.loads(evento["payload"])["canale"] == "SFTP"

    async def test_jwt_con_claim_canale_resta_rest(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, canale="SFTP", auth_method="jwt")
        async with client_http() as c:
            r = await _versa_multipart(c, indice(numero="C-2"), "canale-mp-0002")
        assert r.status_code == 202, r.text
        assert await _canali_pdv(db_url) == ["REST"]
