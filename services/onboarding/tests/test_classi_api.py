# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
API classi documentali — onboarding su PostgreSQL reale (VD-C-023, R03).

Le classi di base del servizio (tenant_id NULL) le modifica solo
SUPER_ADMIN; l'amministratore di un cliente crea e modifica solo le classi
del proprio cliente e non vede quelle degli altri (404). Prima la lista non
filtrava per cliente e un ADMIN_TENANT poteva cambiare, per esempio, gli
anni di conservazione di una classe usata da tutti.

I vecchi test simulavano il database con righe finte: sostituiti da test che
eseguono le query vere sullo schema delle migrazioni (tests/infra_reale.py).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

BASE = "/api/v1/onboarding/classi-documentali"


def _h(tenant_id: str, ruolo: str) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant_id, user_id=str(uuid.uuid4()), ruolo=ruolo,
    )}


async def _client() -> AsyncClient:
    from app.routers.classi import router

    from libs.shared.errori import installa_gestori_errori

    app = FastAPI()
    installa_gestori_errori(app)  # VD-C-100: errori come in main.py
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _nuova(codice: str = "delibera_giunta", **altro) -> dict:
    return {"codice": codice, "nome": "Delibera di giunta", "anni_conservazione": 20,
            "formati_ammessi": ["application/pdf"], **altro}


async def _anni_base(url: str, codice: str) -> int:
    righe = await sql(url, "SELECT anni_conservazione FROM public.classi_documentali "
                           "WHERE codice = $1 AND tenant_id IS NULL", codice)
    return righe[0]["anni_conservazione"]


async def test_admin_cliente_non_modifica_classi_di_base(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    prima = await _anni_base(db_url, "fattura")
    admin = _h(str(tenant), "ADMIN_TENANT")
    async with await _client() as c:
        r = await c.put(f"{BASE}/fattura", json={"anni_conservazione": 1}, headers=admin)
        assert r.status_code == 403, r.text
        r = await c.post(f"{BASE}/fattura/attiva", params={"attiva": "false"}, headers=admin)
        assert r.status_code == 403
    assert await _anni_base(db_url, "fattura") == prima


async def test_super_admin_modifica_classi_di_base(db_url) -> None:
    async with await _client() as c:
        r = await c.put(f"{BASE}/fattura", json={"anni_conservazione": 12},
                        headers=_h(str(uuid.uuid4()), "SUPER_ADMIN"))
    assert r.status_code == 200, r.text
    assert r.json()["di_base"] is True and r.json()["anni_conservazione"] == 12
    assert await _anni_base(db_url, "fattura") == 12


async def test_classi_del_cliente_isolate(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        r = await c.post(BASE, json=_nuova(), headers=_h(str(t1), "ADMIN_TENANT"))
        assert r.status_code == 201, r.text
        classe = r.json()
        assert classe["tenant_id"] == str(t1) and classe["di_base"] is False

        # Il cliente t2 non la vede né la modifica (404, non 403)
        assert (await c.get(f"{BASE}/{classe['id']}",
                            headers=_h(str(t2), "ADMIN_TENANT"))).status_code == 404
        assert (await c.put(f"{BASE}/{classe['id']}", json={"anni_conservazione": 1},
                            headers=_h(str(t2), "ADMIN_TENANT"))).status_code == 404
        lista_t2 = (await c.get(BASE, params={"limit": 200},
                                headers=_h(str(t2), "VIEWER"))).json()
        assert all(v["tenant_id"] in (None, str(t2)) for v in lista_t2["items"])
        assert "delibera_giunta" not in {v["codice"] for v in lista_t2["items"]}

        # t1 la vede insieme alle classi di base e la modifica
        lista_t1 = (await c.get(BASE, params={"limit": 200},
                                headers=_h(str(t1), "VIEWER"))).json()
        assert "delibera_giunta" in {v["codice"] for v in lista_t1["items"]}
        assert any(v["di_base"] for v in lista_t1["items"])
        r = await c.put(f"{BASE}/delibera_giunta", json={"anni_conservazione": 25},
                        headers=_h(str(t1), "ADMIN_TENANT"))
        assert r.status_code == 200, r.text
        assert r.json()["anni_conservazione"] == 25


async def test_classe_del_cliente_con_codice_di_base_prevale(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    admin = _h(str(tenant), "ADMIN_TENANT")
    async with await _client() as c:
        r = await c.post(BASE, json=_nuova("fattura", nome="Fattura del comune"), headers=admin)
        assert r.status_code == 201, r.text
        # Seconda classe con lo stesso codice nello stesso cliente: 409
        assert (await c.post(BASE, json=_nuova("fattura"), headers=admin)).status_code == 409
        d = await c.get(f"{BASE}/fattura", headers=admin)
        assert d.json()["tenant_id"] == str(tenant)
        assert d.json()["nome"] == "Fattura del comune"
        # Modificare «fattura» per questo cliente tocca la sua, non quella di base
        prima = await _anni_base(db_url, "fattura")
        r = await c.put(f"{BASE}/fattura", json={"anni_conservazione": 3}, headers=admin)
        assert r.status_code == 200 and r.json()["tenant_id"] == str(tenant)
    assert await _anni_base(db_url, "fattura") == prima


async def test_creazione_permessi_e_proprietario(db_url) -> None:
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        # Un amministratore non crea classi per un altro cliente
        r = await c.post(BASE, json=_nuova(tenant_id=str(t2)), headers=_h(str(t1), "ADMIN_TENANT"))
        assert r.status_code == 403
        # VIEWER/OPERATORE non creano (permessi invariati)
        for ruolo in ("VIEWER", "OPERATORE"):
            r = await c.post(BASE, json=_nuova(), headers=_h(str(t1), ruolo))
            assert r.status_code == 403, ruolo
        # SUPER_ADMIN: senza tenant_id crea una classe di base, con tenant_id per quel cliente
        sa = _h(str(uuid.uuid4()), "SUPER_ADMIN")
        r = await c.post(BASE, json=_nuova("registro_base"), headers=sa)
        assert r.status_code == 201 and r.json()["di_base"] is True
        r = await c.post(BASE, json=_nuova("registro_t2", tenant_id=str(t2)), headers=sa)
        assert r.status_code == 201 and r.json()["tenant_id"] == str(t2)
        # Classe di base con codice già usato: 409
        assert (await c.post(BASE, json=_nuova("registro_base"), headers=sa)).status_code == 409


@pytest.mark.parametrize("corpo", [
    _nuova("Maiuscole"),
    _nuova(formati_ammessi=["non un mime"]),
    _nuova(anni_conservazione=0),
    _nuova(campo_ignoto=True),
])
async def test_validazione(db_url, corpo) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        r = await c.post(BASE, json=corpo, headers=_h(str(tenant), "ADMIN_TENANT"))
    assert r.status_code == 422, r.text


async def test_senza_token_401_e_classe_inesistente_404(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        assert (await c.get(BASE)).status_code == 401
        assert (await c.get(f"{BASE}/{uuid.uuid4()}",
                            headers=_h(str(tenant), "VIEWER"))).status_code == 404
        assert (await c.get(f"{BASE}/inesistente",
                            headers=_h(str(tenant), "VIEWER"))).status_code == 404


async def test_dimensione_massima_della_classe_fino_a_5_gib(db_url) -> None:
    """R03 i1: 5120 MB ammessi (come le sessioni di caricamento), 5121 no."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    admin = _h(str(tenant), "ADMIN_TENANT")
    async with await _client() as c:
        r = await c.post(BASE, json=_nuova("classe_grande", max_dimensione_mb=5120), headers=admin)
        assert r.status_code == 201, r.text
        assert r.json()["max_dimensione_mb"] == 5120
        oltre = await c.post(BASE, json=_nuova("classe_oltre", max_dimensione_mb=5121),
                             headers=admin)
        assert oltre.status_code == 422
        modifica = await c.put(f"{BASE}/classe_grande", json={"max_dimensione_mb": 5121},
                               headers=admin)
        assert modifica.status_code == 422
