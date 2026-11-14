# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Migrazione 20261023_025 (registro delle impronte a due livelli) su PostgreSQL reale.

- i PdA della vecchia catena (blocchi formato 1/2) entrano in coda nell'ordine
  (blocco, posizione nel blocco), poi i PdA su storage non ancora in catena per
  data di versamento; i blocchi e i PdA non si toccano (REGOLE §10.6);
- tabelle append-only per trigger; una riga di stato con dimensione 0;
- il downgrade toglie tabelle, trigger e funzioni e si può rifare l'upgrade.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]

PRIMA = "20261021_024"
QUESTA = "20261023_025"


def _pda(url: str, tenant: uuid.UUID, classe: uuid.UUID, stato: str,
         versamento: datetime) -> uuid.UUID:
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    asyncio.run(_esegui(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, "
                             "user_id, tenant_id) VALUES ($1, 'x', 'IN_BUILD', $2, $3, $4)",
                        pdv, classe, uuid.uuid4(), tenant))
    asyncio.run(_esegui(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, "
                             "s3_bucket, hash_sha256, data_versamento) VALUES ($1, $2, $3, $4, "
                             "CAST($5 AS public.aip_stato), 'k', 'b', $6, $7)",
                        aip, pdv, tenant, f"urn:m:{aip}", stato, "ab" * 32, versamento))
    return aip


def _blocco(url: str, numero: int, aip_ids: list[uuid.UUID]) -> None:
    blocco = uuid.uuid4()
    asyncio.run(_esegui(url, "INSERT INTO public.hashchain_blocks (id, numero_blocco, hash_blocco, "
                             "hash_blocco_precedente, merkle_root, aip_ids, num_aip, "
                             "versione_formato, stato_firma) VALUES ($1, $2, $3, 'p', 'm', "
                             "CAST($4 AS jsonb), $5, 2, 'NON_FIRMATO')",
                        blocco, numero, f"h{numero}", json.dumps([str(a) for a in aip_ids]),
                        len(aip_ids)))
    for aip in aip_ids:
        asyncio.run(_esegui(url, "UPDATE public.aip SET hashchain_block_id = $1, "
                                 "stato = 'IN_CATENA' WHERE id = $2", blocco, aip))


def test_coda_iniziale_ordinata_e_dati_conservati_intatti(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), PRIMA)
    tenant, classe = uuid.uuid4(), uuid.uuid4()
    asyncio.run(_esegui(db_vuoto, "INSERT INTO public.tenants (id, codice_fiscale, "
                                  "ragione_sociale, schema_name, stato, email_referente) VALUES "
                                  "($1, $2, 'C', $3, 'ATTIVO', 'r@c.it')",
                        tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}"))
    asyncio.run(_esegui(db_vuoto, "INSERT INTO public.classi_documentali (id, codice, nome) "
                                  "VALUES ($1, 'c', 'c')", classe))
    t = datetime(2026, 10, 1, tzinfo=UTC)
    b1 = [_pda(db_vuoto, tenant, classe, "SU_STORAGE", t) for _ in range(3)]
    b2 = [_pda(db_vuoto, tenant, classe, "SU_STORAGE", t) for _ in range(2)]
    _blocco(db_vuoto, 1, [b1[2], b1[0], b1[1]])       # ordine del blocco, non dell'id
    _blocco(db_vuoto, 2, b2)
    su_storage = [_pda(db_vuoto, tenant, classe, "SU_STORAGE", t + timedelta(hours=h))
                  for h in (2, 1)]
    _pda(db_vuoto, tenant, classe, "IN_BUILD", t)    # non costruito: non entra
    prima = asyncio.run(_esegui(db_vuoto, "SELECT id, stato, hashchain_block_id FROM public.aip "
                                          "ORDER BY id"))

    command.upgrade(_config(), QUESTA)
    coda = [r["aip_id"] for r in asyncio.run(_esegui(
        db_vuoto, "SELECT aip_id FROM public.catena_coda ORDER BY sequenza"))]
    assert coda == [b1[2], b1[0], b1[1], *b2, su_storage[1], su_storage[0]]
    assert asyncio.run(_esegui(db_vuoto, "SELECT id, stato, hashchain_block_id FROM public.aip "
                                         "ORDER BY id")) == prima
    stato = asyncio.run(_esegui(db_vuoto, "SELECT * FROM public.catena_stato"))[0]
    assert (stato["id"], stato["dimensione"], stato["detentore"]) == (1, 0, None)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        asyncio.run(_esegui(db_vuoto, "INSERT INTO public.catena_stato (id) VALUES (2)"))
    asyncio.run(_esegui(db_vuoto, "INSERT INTO public.catena_nodi (livello, indice, hash) "
                                  "VALUES (0, 0, $1)", b"\x00" * 32))
    with pytest.raises(asyncpg.exceptions.RaiseError):
        asyncio.run(_esegui(db_vuoto, "UPDATE public.catena_nodi SET hash = $1", b"\x01" * 32))

    command.downgrade(_config(), PRIMA)
    tabelle = {r["tablename"] for r in asyncio.run(_esegui(
        db_vuoto, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                  "AND tablename LIKE 'catena_%'"))}
    assert tabelle == set()
    funzioni = asyncio.run(_esegui(db_vuoto, "SELECT proname FROM pg_proc WHERE proname "
                                             "LIKE 'catena_%'"))
    assert funzioni == []
    command.upgrade(_config(), QUESTA)
    assert len(asyncio.run(_esegui(db_vuoto, "SELECT 1 FROM public.catena_coda"))) == 7
