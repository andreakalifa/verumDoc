# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Migrazione 20261020_013 (deduplica delle notifiche) su PostgreSQL reale.

- con doppioni già presenti la migrazione si FERMA con l'elenco (§16.5) e non
  cancella nulla;
- senza doppioni crea l'indice unico, che rifiuta un secondo inserimento;
- il downgrade toglie l'indice.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]

_TENANT = (
    "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, stato, "
    "email_referente) VALUES ($1, $2, 'Cliente', $3, 'ATTIVO', 'r@cliente.it')"
)
_NOTIFICA = (
    "INSERT INTO public.notifications (tenant_id, tipo, canale, destinatario, payload_json, stato) "
    "VALUES ($1, 'aip.conserved', 'email', 'r@cliente.it', '{\"oggetto_id\": \"aip-1\"}', "
    "'IN_CODA')"
)


def _prepara(url: str) -> uuid.UUID:
    tenant = uuid.uuid4()
    asyncio.run(_esegui(url, _TENANT, tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}"))
    return tenant


def test_doppioni_fermano_la_migrazione_con_elenco(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), "20261020_012")
    tenant = _prepara(db_vuoto)
    asyncio.run(_esegui(db_vuoto, _NOTIFICA, tenant))
    asyncio.run(_esegui(db_vuoto, _NOTIFICA, tenant))
    with pytest.raises(Exception, match="notifiche doppie") as errore:
        command.upgrade(_config(), "20261020_013")
    assert "aip-1" in str(errore.value)
    righe = asyncio.run(_esegui(db_vuoto, "SELECT count(*) AS n FROM public.notifications"))
    assert righe[0]["n"] == 2  # nessuna riga cancellata
    versione = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert versione[0]["version_num"] == "20261020_012"


def test_indice_unico_e_downgrade(db_vuoto: str) -> None:  # noqa: F811
    import asyncpg
    from alembic import command

    command.upgrade(_config(), "20261020_013")
    tenant = _prepara(db_vuoto)
    asyncio.run(_esegui(db_vuoto, _NOTIFICA, tenant))
    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(_esegui(db_vuoto, _NOTIFICA, tenant))
    command.downgrade(_config(), "20261020_012")
    indici = asyncio.run(_esegui(
        db_vuoto, "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_notifications_dedup'"))
    assert indici == []
    asyncio.run(_esegui(db_vuoto, _NOTIFICA, tenant))  # di nuovo ammesso senza indice
