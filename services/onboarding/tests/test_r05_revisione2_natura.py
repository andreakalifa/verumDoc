# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 2 di R05 — rilievo S5: la natura PRIVATO (esenzione dello scarto dal
nulla osta della Soprintendenza) dichiarata ALLA CREAZIONE dell'ente vuole un
motivo e lascia la voce d'audit ``tenant.natura_dichiarata`` nella stessa
transazione, come ``PUT /tenant/{id}/natura`` (prima: accettata senza motivo né
traccia).
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401


def _cf() -> str:
    return str(uuid.uuid4().int)[:11]


def test_modello_privato_senza_motivo_rifiutato() -> None:
    from app.models.tenant import TenantCreate

    base = {"ragione_sociale": "Ditta Di Prova", "codice_fiscale": _cf(),
            "email_referente": "ref@example.org"}
    with pytest.raises(ValidationError, match="motivo_natura"):
        TenantCreate(**base, natura="PRIVATO")
    with pytest.raises(ValidationError):
        TenantCreate(**base, natura="PRIVATO", motivo_natura="corto")
    assert TenantCreate(**base, natura="PRIVATO",
                        motivo_natura="Società privata: visura camerale allegata").natura == \
        "PRIVATO"
    assert TenantCreate(**base).natura is None   # non dichiarata = PA, nessun motivo


@richiede_pg
async def test_creazione_privato_con_motivo_e_audit_nella_transazione(db_url):  # noqa: F811
    from app.services.onboarding_service import crea_tenant

    from libs.shared.db.ambito import ambito_conservatore

    with pytest.raises(HTTPException) as errore:
        await crea_tenant("Ditta Senza Motivo", _cf(), "ref@example.org", natura="PRIVATO")
    assert errore.value.status_code == 422
    assert await sql(db_url, "SELECT 1 FROM public.tenants "
                             "WHERE ragione_sociale = 'Ditta Senza Motivo'") == []

    chi = str(uuid.uuid4())
    with ambito_conservatore("test della natura alla creazione", registra=False):
        tenant = await crea_tenant("Ditta Di Prova", _cf(), "ref@example.org", natura="PRIVATO",
                                   dichiarata_da=chi,
                                   motivo_natura="Società privata: visura camerale verificata")
    righe = await sql(db_url, "SELECT user_id, dettagli FROM public.audit_global "
                              "WHERE evento = 'tenant.natura_dichiarata' AND tenant_id = $1",
                      tenant.id)
    assert len(righe) == 1 and str(righe[0]["user_id"]) == chi
    dettagli = json.loads(righe[0]["dettagli"])
    assert dettagli["dopo"] == "PRIVATO" and dettagli["alla_creazione"] is True
    assert dettagli["motivo"].startswith("Società privata")
