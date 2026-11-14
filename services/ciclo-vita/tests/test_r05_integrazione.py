# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Integrazione R05 di ciclo-vita con il lavoro di r05-sicurezza (VD-C-079, VD-C-080).

- Il portale chiama ``/api/v1/ciclo-vita`` con la sessione in cookie HttpOnly:
  il cookie vale come Bearer, le modifiche chiedono ``X-CSRF-Token`` e la
  stessa origine (403 AUTH-040 altrimenti).
- Ogni richiesta parte senza ambito RLS e non lo lascia al chiamante.
- Con la Row Level Security di ``pratiche`` (migrazione 20261023_040) e il
  login dei servizi, l'ente vede le sue pratiche e non quelle degli altri;
  il conservatore le vede tutte.
"""
from __future__ import annotations

import uuid

import pytest

from libs.shared.db.ambito import NESSUN_AMBITO, ambito_corrente, imposta_ambito, ripristina_ambito
from libs.shared.security.sessione_cookie import COOKIE_CSRF, COOKIE_SESSIONE
from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, crea_ente, crea_utente, intestazioni, token

pytestmark = [pytest.mark.integration]


async def _pratica(url: str, tenant: uuid.UUID, utente: uuid.UUID) -> uuid.UUID:
    pid = uuid.uuid4()
    await sql(url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, creata_da) "
                   "VALUES ($1, $2, 'SCARTO', 'BOZZA', 'scarto di prova RLS', $3)",
              pid, tenant, utente)
    return pid


@richiede_pg
async def test_sessione_in_cookie_del_portale(db_url) -> None:
    ente = await crea_ente(db_url)
    utente = await crea_utente(db_url, ente, "ADMIN_TENANT")
    sessione = token(ente, "ADMIN_TENANT", utente)
    async with client_app() as c:
        c.cookies.set(COOKIE_SESSIONE, sessione)
        c.cookies.set(COOKIE_CSRF, "csrf-di-prova")
        r = await c.get("/api/v1/ciclo-vita/pratiche")
        assert r.status_code == 200, r.text
        # modifica con il solo cookie, senza token anti-CSRF: rifiutata
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         json={"pdv_id": str(uuid.uuid4()), "motivo": "annullamento di prova"})
        assert r.status_code == 403
        assert r.json()["codice"] == "AUTH-040"
        # con il token anti-CSRF e la stessa origine la richiesta arriva al servizio
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         json={"pdv_id": str(uuid.uuid4()), "motivo": "annullamento di prova"},
                         headers={"X-CSRF-Token": "csrf-di-prova", "Origin": "http://cv"})
        assert r.status_code == 404, r.text  # PdV inesistente: autenticato e autorizzato


@richiede_pg
async def test_ambito_della_richiesta_non_esce(db_url) -> None:
    ente = await crea_ente(db_url)
    precedente = imposta_ambito(NESSUN_AMBITO)
    try:
        async with client_app() as c:
            r = await c.get("/api/v1/ciclo-vita/pratiche",
                            headers=intestazioni(ente, "ADMIN_TENANT"))
        assert r.status_code == 200
        assert ambito_corrente() == NESSUN_AMBITO
    finally:
        ripristina_ambito(precedente)


@richiede_pg
async def test_pratiche_con_rls_per_ente(db_url) -> None:
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    ua = await crea_utente(db_url, a, "ADMIN_TENANT")
    ub = await crea_utente(db_url, b, "ADMIN_TENANT")
    pa, pb = await _pratica(db_url, a, ua), await _pratica(db_url, b, ub)
    async with client_app() as c:
        r = await c.get(f"/api/v1/ciclo-vita/pratiche/{pa}",
                        headers=intestazioni(a, "ADMIN_TENANT", ua))
        assert r.status_code == 200, r.text
        r = await c.get(f"/api/v1/ciclo-vita/pratiche/{pb}",
                        headers=intestazioni(a, "ADMIN_TENANT", ua))
        assert r.status_code == 404
        r = await c.get("/api/v1/ciclo-vita/pratiche",
                        headers=intestazioni(a, "RSC_CONSERVATORE"))
        assert {p["id"] for p in r.json()["items"]} >= {str(pa), str(pb)}


class _SessioneFinta:
    def __init__(self) -> None:
        self.parametri: list[dict] = []

    async def execute(self, _istruzione, parametri):
        self.parametri.append(parametri)


@pytest.mark.parametrize("topic", ["pratica.cambio_stato", "versamento.annullato",
                                   "versamento.rettificato", "scarto.eseguito",
                                   "riversamento.pronto"])
async def test_eventi_cliente_con_tenant_id_nel_payload(topic) -> None:
    """notification ricava l'ente SOLO dal ``tenant_id`` del payload (come per
    tutti gli altri produttori): senza, l'evento non arriva a nessun cliente."""
    import json

    from app.application import pratiche as pr

    ente = uuid.uuid4()
    sessione = _SessioneFinta()
    await pr.evento(sessione, topic, "chiave", {"pratica_id": "p", "ente": str(ente)}, ente)
    payload = json.loads(sessione.parametri[0]["payload"])
    assert payload["tenant_id"] == str(ente) and payload["ente"] == str(ente)
