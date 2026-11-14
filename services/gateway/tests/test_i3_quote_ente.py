# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 i3 — quote predefinite per piano e quota propria dell'ente che prevale sul piano.

La quota dell'ente è un'impostazione del registro al livello ENTE
(``libs.verumdoc_rules.quote``: QUOTA_PDV_GIORNO, QUOTA_RICHIESTE_MINUTO) salvata
in ``impostazioni_valori``; la legge ``libs.shared.security.quote.limiti_ente``,
unica fonte per il gateway (richieste al minuto, PdV al giorno) e per il
limitatore degli altri servizi. Su PostgreSQL vero, con le rotte reali.
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import json
import uuid

import pytest
from app.auth import rate_limiter
from app.services.quota_versamento import imposta_fornitore_redis

from libs.shared.security.quote import (
    PDV_GIORNO_PREDEFINITI,
    RICHIESTE_MINUTO_PREDEFINITE,
    ImpostazioniQuote,
    LimitiEnte,
    limiti_ente,
    svuota_cache_piani,
)
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.redis_finto import RedisQuote
from tests.supporto_canali import (  # noqa: F401 — fixture
    client_http,
    indice,
    s3,
    senza_lavoratore,
    utente_di,
)
from tests.test_b4_accesso_canali_quote import (
    _app,
    _chiama,
    _chiave,
    _ente,
    _pulizia,  # noqa: F401 — fixture autouse
)
from tests.test_r03_quote_canali import _quota_429, _versa_multipart


async def _quota_ente(url: str, tenant: uuid.UUID, codice: str, valore: object) -> None:
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ('ENTE', $1, $2, CAST($3 AS jsonb), 'prova R03 i3')",
              tenant, codice, json.dumps(valore))


# ── Unità ───────────────────────────────────────────────────────────

def test_predefiniti_per_piano_r03():
    imp = ImpostazioniQuote()
    assert RICHIESTE_MINUTO_PREDEFINITE == {"starter": 120, "business": 600, "enterprise": 6000}
    assert PDV_GIORNO_PREDEFINITI == {"starter": 1000, "business": 20000, "enterprise": 200000}
    assert LimitiEnte("business").richieste_minuto(imp) == 600
    assert LimitiEnte("enterprise").pdv_giorno(imp) == 200000
    # piano sconosciuto: quello predefinito
    assert LimitiEnte("ignoto").pdv_giorno(imp) == 1000


def test_quota_dell_ente_prevale_sul_piano():
    imp = ImpostazioniQuote()
    limiti = LimitiEnte("starter", richieste_minuto_ente=9000, pdv_giorno_ente=500000)
    assert limiti.richieste_minuto(imp) == 9000
    assert limiti.pdv_giorno(imp) == 500000
    solo_pdv = LimitiEnte("starter", pdv_giorno_ente=3)
    assert solo_pdv.pdv_giorno(imp) == 3 and solo_pdv.richieste_minuto(imp) == 120


# ── Con PostgreSQL ─────────────────────────────────────────────────

@richiede_pg
async def test_limiti_ente_letti_dal_registro(db_url):
    imp = ImpostazioniQuote()
    tid, _ = await _ente(db_url, piano="business")
    altro, _ = await _ente(db_url, piano="business")
    await _quota_ente(db_url, tid, "QUOTA_PDV_GIORNO", 750000)
    await _quota_ente(db_url, tid, "QUOTA_RICHIESTE_MINUTO", 0)   # 0 = vale il piano
    await _quota_ente(db_url, tid, "ALTRA_IMPOSTAZIONE", 1)       # ignorata
    svuota_cache_piani()
    limiti = await limiti_ente(tid, imp)
    assert (limiti.piano, limiti.richieste_minuto_ente, limiti.pdv_giorno_ente) == (
        "business", None, 750000)
    assert limiti.pdv_giorno(imp) == 750000 and limiti.richieste_minuto(imp) == 600
    altri = await limiti_ente(altro, imp)
    assert (altri.piano, altri.richieste_minuto_ente, altri.pdv_giorno_ente) == (
        "business", None, None)


@richiede_pg
async def test_quota_ente_non_valida_vale_il_piano(db_url):
    """Configurazione rotta (tipo errato o negativo): non si indovina, vale il piano."""
    imp = ImpostazioniQuote()
    tid, _ = await _ente(db_url, piano="starter")
    await _quota_ente(db_url, tid, "QUOTA_PDV_GIORNO", "molti")
    svuota_cache_piani()
    limiti = await limiti_ente(tid, imp)
    assert (limiti.piano, limiti.pdv_giorno_ente) == ("starter", None)


@richiede_pg
async def test_richieste_al_minuto_dell_ente_prevalgono(db_url, monkeypatch):
    """Piano business = 4 al minuto (impostazione), ente con 2: la terza è 429;
    un altro ente dello stesso piano ne ha 4."""
    monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "richieste_minuto",
                        {"starter": 1, "business": 4})
    redis = RedisQuote()
    prova = _app(redis)
    tid, uid = await _ente(db_url, piano="business")
    await _quota_ente(db_url, tid, "QUOTA_RICHIESTE_MINUTO", 2)
    chiave = await _chiave(db_url, tid, uid)
    stati = [(await _chiama(prova, "GET", "/chi", {"X-Api-Key": chiave})).status_code
             for _ in range(3)]
    assert stati == [200, 200, 429]
    r = await _chiama(prova, "GET", "/chi", {"X-Api-Key": chiave})
    assert r.json()["codice"] == "QTA-001" and r.json()["dettagli"]["limite"] == 2

    altro, uid2 = await _ente(db_url, piano="business")
    chiave2 = await _chiave(db_url, altro, uid2)
    stati = [(await _chiama(prova, "GET", "/chi", {"X-Api-Key": chiave2})).status_code
             for _ in range(5)]
    assert stati == [200, 200, 200, 200, 429]


@richiede_pg
@pytest.mark.integration
async def test_pdv_al_giorno_dell_ente_prevalgono_nel_caso_d_uso(db_url, s3, utente_di,
                                                                monkeypatch):
    """Piano starter = 1 PdV al giorno, ente con 2: il secondo versamento passa, il
    terzo riceve 429 QTA-002 con il limite dell'ente."""
    redis = RedisQuote()
    precedente = imposta_fornitore_redis(lambda: redis)
    monkeypatch.setattr(rate_limiter.IMPOSTAZIONI_QUOTE, "pdv_giorno", {"starter": 1})
    svuota_cache_piani()
    try:
        tenant, _ = await crea_tenant_e_classe(db_url)
        await _quota_ente(db_url, tenant, "QUOTA_PDV_GIORNO", 2)
        utente_di(tenant, piano="starter")
        async with client_http() as c:
            primo = await _versa_multipart(c, indice(numero="E-1"), "quota-ente-0001")
            secondo = await _versa_multipart(c, indice(numero="E-2"), "quota-ente-0002")
            terzo = await _versa_multipart(c, indice(numero="E-3"), "quota-ente-0003")
        assert primo.status_code == 202, primo.text
        assert secondo.status_code == 202, secondo.text
        _quota_429(terzo)
        assert terzo.json()["dettagli"]["limite"] == 2
    finally:
        imposta_fornitore_redis(precedente)
        svuota_cache_piani()
