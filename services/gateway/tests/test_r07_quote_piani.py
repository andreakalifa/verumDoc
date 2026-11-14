# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07 (D13) — piani modificabili in console, rallentamento invece del rifiuto,
avvisi automatici all'80% e al 100% della quota giornaliera.

I limiti dei piani sono impostazioni del conservatore (``QUOTA_PIANO_*`` in
``impostazioni_valori``, livello CONSERVATORE) che prevalgono sulle variabili
``QUOTE_*``; l'ente sceglie RIFIUTA o RALLENTA (``QUOTA_SUPERAMENTO``). Su
PostgreSQL vero con le rotte reali del versamento.
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import json
import uuid

import pytest
from app.services.quota_versamento import (
    imposta_fornitore_redis,
    piano_suggerito,
    soglie_raggiunte,
)

from libs.shared.security.quote import ImpostazioniQuote, limiti_ente, svuota_cache_piani
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
    _ente,
    _pulizia,  # noqa: F401 — fixture autouse
)
from tests.test_r03_quote_canali import _quota_429, _versa_multipart


async def _valore(url: str, livello: str, tenant: uuid.UUID | None, codice: str,
                  valore: object) -> None:
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ($1, $2, $3, CAST($4 AS jsonb), 'prova R07 D13')",
              livello, tenant, codice, json.dumps(valore))


def test_soglie_e_piano_suggerito():
    assert soglie_raggiunte(799, 1000) == []
    assert soglie_raggiunte(800, 1000) == [80]
    assert soglie_raggiunte(1000, 1000) == [80, 100]
    assert soglie_raggiunte(2, 2) == [80, 100]   # 80% di 2 arrotondato per eccesso = 2
    piani = {"starter": 1000, "business": 20000, "enterprise": 200000}
    assert piano_suggerito("starter", 1000, piani) == "business"
    assert piano_suggerito("business", 50000, piani) == "enterprise"
    assert piano_suggerito("enterprise", 900000, piani) is None


@richiede_pg
async def test_limite_del_piano_dalla_console_prevale_su_ambiente(db_url):
    imp = ImpostazioniQuote()
    tid, _ = await _ente(db_url, piano="business")
    await _valore(db_url, "CONSERVATORE", None, "QUOTA_PIANO_BUSINESS_PDV_GIORNO", 7)
    await _valore(db_url, "CONSERVATORE", None, "QUOTA_PIANO_BUSINESS_RICHIESTE_MINUTO", 9)
    svuota_cache_piani()
    limiti = await limiti_ente(tid, imp)
    assert limiti.pdv_giorno(imp) == 7 and limiti.richieste_minuto(imp) == 9
    assert limiti.rallenta_secondi is None          # predefinito: RIFIUTA
    # la quota propria dell'ente prevale ancora sul piano
    await _valore(db_url, "ENTE", tid, "QUOTA_PDV_GIORNO", 11)
    await _valore(db_url, "ENTE", tid, "QUOTA_SUPERAMENTO", "RALLENTA")
    svuota_cache_piani()
    limiti = await limiti_ente(tid, imp)
    # revisione W1: RALLENTA scelto dall'ente vale solo se il conservatore lo consente
    assert limiti.pdv_giorno(imp) == 11 and limiti.rallenta_secondi is None
    await _valore(db_url, "ENTE", tid, "LIMITE.QUOTA_SUPERAMENTO", "RALLENTA")
    svuota_cache_piani()
    limiti = await limiti_ente(tid, imp)
    assert limiti.pdv_giorno(imp) == 11 and limiti.rallenta_secondi == 30


@richiede_pg
@pytest.mark.integration
async def test_rallenta_invece_di_rifiutare_e_avvisi_80_100(db_url, s3, utente_di):
    """Piano starter = 2 PdV al giorno dalla console, ente con RALLENTA ogni 60 s:
    il secondo versamento raggiunge 80% e 100% (due avvisi, una volta sola), il
    terzo passa rallentato, il quarto riceve 429 con l'attesa del rallentamento."""
    redis = RedisQuote()
    precedente = imposta_fornitore_redis(lambda: redis)
    svuota_cache_piani()
    try:
        tenant, _ = await crea_tenant_e_classe(db_url)
        await _valore(db_url, "CONSERVATORE", None, "QUOTA_PIANO_STARTER_PDV_GIORNO", 2)
        await _valore(db_url, "CONSERVATORE", None, "QUOTA_RALLENTAMENTO_SECONDI", 60)
        # W1: RALLENTA concesso dal conservatore per l'ente
        await _valore(db_url, "ENTE", tenant, "LIMITE.QUOTA_SUPERAMENTO", "RALLENTA")
        await _valore(db_url, "ENTE", tenant, "QUOTA_SUPERAMENTO", "RALLENTA")
        utente_di(tenant, piano="starter")
        async with client_http() as c:
            esiti = [await _versa_multipart(c, indice(numero=f"Q7-{i}"), f"quota-r07-{i:04d}")
                     for i in range(4)]
        assert [r.status_code for r in esiti[:3]] == [202, 202, 202], esiti[2].text
        _quota_429(esiti[3])
        assert 1 <= int(esiti[3].headers["Retry-After"]) <= 60
        avvisi = await sql(db_url, "SELECT chiave, dati::text AS dati FROM "
                                   "public.avvisi_automatici WHERE tenant_id = $1 "
                                   "ORDER BY chiave", tenant)
        assert [a["chiave"].split(":")[-1] for a in avvisi] == ["100", "80"]
        dati = json.loads(avvisi[0]["dati"])
        assert dati["limite"] == 2 and dati["effetto"] == "rallentamento"
        assert dati["piano_suggerito"] == "business"
        eventi = await sql(db_url, "SELECT count(*) AS n FROM public.outbox_eventi "
                                   "WHERE topic = 'quota.soglia_raggiunta' AND tenant_id = $1",
                                   tenant)
        assert eventi[0]["n"] == 2
    finally:
        imposta_fornitore_redis(precedente)
        svuota_cache_piani()
