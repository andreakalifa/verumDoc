# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-10) — le regole d'autenticazione che notification dichiara per
l'OpenAPI (``__verumdoc_autenticazione__`` delle dipendenze, lette da
``tools/docs/genera_openapi.py``) dicono il vero: per ogni rotta GET senza
parametri di percorso, una chiave API passa se e solo se dichiarata, e un ruolo
fuori da quelli dichiarati riceve 403.
"""
from __future__ import annotations

import base64
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from app.api import autenticazione
from app.application import configurazione
from app.infrastructure.cifratura import Cifratore

from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.revoca import svuota_cache_revoche
from tests import infra_reale
from tests.b3_supporto import token_utente
from tests.infra_reale import richiede_pg, sql

db_url = infra_reale.db_url  # fixture condivisa

pytestmark = [pytest.mark.integration, richiede_pg]

PEPPER = b"r" * 40
RADICE = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _ambiente():
    configurazione.imposta_cifratore(
        Cifratore.da_impostazioni(base64.b64encode(os.urandom(32)).decode(), "k1"))
    autenticazione.imposta_pepper(PEPPER)
    svuota_cache_revoche()
    yield
    configurazione.imposta_cifratore(None)
    autenticazione.imposta_pepper(None)
    svuota_cache_revoche()


def _regole() -> dict[str, dict]:
    sys.path.insert(0, str(RADICE / "tools" / "docs"))
    try:
        import genera_openapi
    finally:
        sys.path.pop(0)
    from app.main import app

    return genera_openapi.autenticazione_rotte(app, "notification")


async def test_regole_dichiarate_uguali_al_comportamento(db_url) -> None:
    regole = {k: v for k, v in _regole().items()
              if k.startswith("get ") and "{" not in k}
    # R05: in più il catalogo dei tipi sottoscrivibili
    # R10 (D32): campanella e preferenze, solo persone (jwt)
    assert set(regole) == {"get /api/v1/eventi", "get /api/v1/webhook",
                           "get /api/v1/webhook/tipi", "get /api/v1/notifiche/campanella",
                           "get /api/v1/notifiche/preferenze",
                           # R13b: console PEC (conservatore con permesso, ADMIN_TENANT per l'ente)
                           "get /api/v1/notifiche/pec/canali", "get /api/v1/notifiche/pec/messaggi",
                           "get /api/v1/notifiche/pec/ricevute"}
    tenant, admin, viewer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                      "schema_name, stato, email_referente) VALUES ($1, $2, 'Ente', $3, "
                      "'ATTIVO', 'a@example.org')", tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}")
    for u, ruolo in ((admin, "ADMIN_TENANT"), (viewer, "VIEWER")):
        await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                          "VALUES ($1, $2, $3, 'N', 'C', CAST($4 AS user_ruolo))",
                  u, tenant, f"{u.hex[:8]}@e.org", ruolo)
    chiave = genera_chiave_api(PEPPER)
    await sql(db_url, "INSERT INTO public.api_keys (tenant_id, user_id, nome, key_hash, "
                      "key_prefix, permessi) VALUES ($1, $2, 'sistema', $3, $4, '[\"read\"]')",
              tenant, admin, chiave.impronta, chiave.identificativo)
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://n") as c:
        for chiave_rotta, regola in regole.items():
            percorso = chiave_rotta.split(" ", 1)[1]
            con_chiave = await c.get(percorso, headers={"X-Api-Key": chiave.chiave})
            if "chiave_api" in regola["metodi"]:
                assert con_chiave.status_code == 200, (percorso, con_chiave.text)
            else:
                assert con_chiave.status_code in (401, 403), (percorso, con_chiave.text)
            visore = await c.get(percorso, headers={
                "Authorization": f"Bearer {token_utente(tenant, 'VIEWER', viewer)}"})
            if regola["ruoli"] and "VIEWER" not in regola["ruoli"]:
                assert visore.status_code == 403, percorso
            else:
                assert visore.status_code == 200, percorso
