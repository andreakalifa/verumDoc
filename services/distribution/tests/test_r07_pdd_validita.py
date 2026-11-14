# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07 (D18) — validità dei PdD ZIP (anche di restituzione) come impostazione della
console e rigenerazione con un clic.

``PDD_VALIDITA_ORE`` (72 h, fino a 720) per ente, mai oltre il tetto tecnico del
bucket (``PDD_ZIP_VALIDITA_ORE``). ``POST /pdd/{id}/rigenera``: un PdD scaduto
torna IN_PREPARAZIONE con lo stesso identificativo e il lavoratore lo ricostruisce.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from libs.shared.parametri import svuota_cache
from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.pda_reale import crea_pda, s3  # noqa: F401
from tests.test_b4_pdd import URL, _client, _h

pytestmark = richiede_pg


async def _validita(url: str, tenant, ore: object) -> None:
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ('ENTE', $1, 'PDD_VALIDITA_ORE', CAST($2 AS jsonb), "
                   "'restituzione con tempi lunghi')", tenant, json.dumps(ore))


async def _zip_pronto(url: str, c, pda) -> str:
    from app.services.pdd_zip import elabora_un_pdd

    r = await c.post(URL, json={"aip_id": str(pda["aip"]), "tipo": "COMPLETO",
                                "motivo": "copia completa", "consegna": "ZIP"},
                     headers=_h(pda["tenant"]))
    assert r.status_code == 200, r.text
    assert await elabora_un_pdd() == "PRONTO"
    return r.json()["pdd_id"]


async def _ore_di_validita(url: str, pdd: str) -> float:
    [riga] = await sql(url, "SELECT scade_il - pronto_il AS durata FROM "
                            "public.pacchetti_distribuzione WHERE id = $1", uuid.UUID(pdd))
    return riga["durata"].total_seconds() / 3600


async def test_validita_dell_ente_con_tetto(db_url, s3, monkeypatch) -> None:  # noqa: F811
    from app.config import settings

    pda = await crea_pda(db_url, s3)
    await _validita(db_url, pda["tenant"], 240)
    svuota_cache()
    async with await _client() as c:
        # revisione W1: senza il consenso del conservatore l'ente non supera i suoi 72 h
        assert round(await _ore_di_validita(db_url, await _zip_pronto(db_url, c, pda))) == 72
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                          "valore, motivo) VALUES ('ENTE', $1, 'LIMITE.PDD_VALIDITA_ORE', "
                          "'240', 'limite concesso dal conservatore')", pda["tenant"])
        svuota_cache()
        assert round(await _ore_di_validita(db_url, await _zip_pronto(db_url, c, pda))) == 240
        monkeypatch.setattr(settings, "pdd_zip_validita_ore", 100)   # tetto del bucket
        assert round(await _ore_di_validita(db_url, await _zip_pronto(db_url, c, pda))) == 100
        monkeypatch.setattr(settings, "pdd_zip_validita_ore", 720)
        await sql(db_url, "UPDATE public.impostazioni_valori SET valore = '9999' "
                          "WHERE codice = 'PDD_VALIDITA_ORE'")   # fuori intervallo: ignorato
        svuota_cache()
        assert round(await _ore_di_validita(db_url, await _zip_pronto(db_url, c, pda))) == 72


async def test_rigenera_pdd_scaduto(db_url, s3) -> None:  # noqa: F811
    from app.config import settings
    from app.services.pdd_zip import elabora_un_pdd, pulisci_scaduti

    pda = await crea_pda(db_url, s3)
    async with await _client() as c:
        pdd = await _zip_pronto(db_url, c, pda)
        percorso = f"/api/v2/distribution/pdd/{pdd}/rigenera"
        r = await c.post(percorso, headers=_h(pda["tenant"]))
        assert r.status_code == 409 and r.json()["codice"] == "DST-409", r.text
        assert await pulisci_scaduti(adesso=datetime.now(UTC) + timedelta(days=4)) == 1
        assert s3.list_objects_v2(Bucket=settings.s3_bucket_pdd)["KeyCount"] == 0
        # un altro ente non lo vede
        assert (await c.post(percorso, headers=_h(uuid.uuid4()))).status_code == 404
        r = await c.post(percorso, headers=_h(pda["tenant"]))
        assert r.status_code == 200 and r.json()["stato"] == "IN_PREPARAZIONE", r.text
        assert await elabora_un_pdd() == "PRONTO"
        pronto = await c.get(f"/api/v2/distribution/pdd/{pdd}", headers=_h(pda["tenant"]))
    assert pronto.json()["stato"] == "PRONTO" and pronto.json()["zip"]["url"]
    assert s3.list_objects_v2(Bucket=settings.s3_bucket_pdd)["KeyCount"] == 1
    audit = await sql(db_url, "SELECT count(*) AS n FROM public.audit_global "
                              "WHERE evento = 'distribuzione.rigenerata'")
    assert audit[0]["n"] == 1
