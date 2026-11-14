# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Migrazione 20261023_039 (foglie interne del registro ed export dell'audit, R05) su
PostgreSQL reale, a partire da un registro già popolato (035):

- le foglie e la coda esistenti restano valide con i vincoli nuovi (convalidati);
- una foglia interna senza PdA e una riga in coda di una foglia interna sono
  ammesse; una foglia PDA senza PdA, una foglia interna con PdA e una riga in coda
  con entrambi o nessuno no;
- ``catena_foglie_interne``: contenuto immutabile, indice scritto una volta, niente
  DELETE; ``audit_esportazioni`` append-only;
- downgrade rifiutato con foglie interne nel registro; senza, possibile e ripetibile.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid

import asyncpg
import pytest

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]

PRIMA = "20261023_035"
QUESTA = "20261023_039"


def _q(url: str, sql: str, *argomenti):
    return asyncio.run(_esegui(url, sql, *argomenti))


def _base(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Un ente, un PdA nel registro (foglia 0) e uno in coda."""
    tenant, classe = uuid.uuid4(), uuid.uuid4()
    _q(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
            "stato, email_referente) VALUES ($1, $2, 'C', $3, 'ATTIVO', 'r@c.it')",
       tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}")
    _q(url, "INSERT INTO public.classi_documentali (id, codice, nome) VALUES ($1, 'c', 'c')",
       classe)
    aip = []
    for _ in range(2):
        pdv, a = uuid.uuid4(), uuid.uuid4()
        _q(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                "VALUES ($1, 'x', 'IN_BUILD', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
        _q(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                "hash_sha256) VALUES ($1, $2, $3, $4, 'SU_STORAGE', 'k', 'b', $5)",
           a, pdv, tenant, f"urn:m:{a}", "ab" * 32)
        aip.append(a)
    _q(url, "INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, dati, hash, "
            "conservato_il) VALUES (0, $1, $2, 'PDA', '{}', $3, now())",
       aip[0], tenant, hashlib.sha256(b"x").digest())
    _q(url, "INSERT INTO public.catena_coda (aip_id) VALUES ($1)", aip[1])
    return tenant, aip[0]


def test_vincoli_nuovi_su_registro_esistente_e_downgrade(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), PRIMA)
    tenant, aip = _base(db_vuoto)
    command.upgrade(_config(), QUESTA)
    # dati esistenti intatti, vincoli convalidati
    assert _q(db_vuoto, "SELECT count(*) AS n FROM public.catena_registro")[0]["n"] == 1
    validi = _q(db_vuoto, "SELECT conname, convalidated FROM pg_constraint WHERE conname IN "
                          "('ck_catena_registro_tipo', 'ck_catena_registro_pda', "
                          "'ck_catena_coda_voce')")
    assert {r["conname"]: r["convalidated"] for r in validi} == {
        "ck_catena_registro_tipo": True, "ck_catena_registro_pda": True,
        "ck_catena_coda_voce": True}

    h = hashlib.sha256(b"y").digest()
    foglia = uuid.uuid4()
    _q(db_vuoto, "INSERT INTO public.catena_foglie_interne (id, tipo, chiave, aip_id, tenant_id, "
                 "pda, dati, hash, richiesta_da) VALUES ($1, 'SCARTO', 'k1', $2, $3, 'urn', "
                 "'{}', $4, 'prova')", foglia, aip, tenant, h)
    _q(db_vuoto, "INSERT INTO public.catena_coda (foglia_interna_id) VALUES ($1)", foglia)
    _q(db_vuoto, "INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, dati, "
                 "hash, conservato_il) VALUES (1, NULL, $1, 'SCARTO', '{}', $2, now())", tenant, h)
    for sql, argomenti in (
        # foglia di un PdA senza PdA; foglia interna con PdA; audit senza ente è ammessa,
        # scarto senza ente no
        ("INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, dati, hash, "
         "conservato_il) VALUES (2, NULL, $1, 'PDA', '{}', $2, now())", (tenant, h)),
        ("INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, dati, hash, "
         "conservato_il) VALUES (2, $1, $2, 'RETTIFICA', '{}', $3, now())", (aip, tenant, h)),
        ("INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, dati, hash, "
         "conservato_il) VALUES (2, NULL, NULL, 'SCARTO', '{}', $1, now())", (h,)),
        ("INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, dati, hash, "
         "conservato_il) VALUES (2, NULL, NULL, 'ALTRO', '{}', $1, now())", (h,)),
        ("INSERT INTO public.catena_coda (aip_id, foglia_interna_id) VALUES (NULL, NULL)", ()),
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            _q(db_vuoto, sql, *argomenti)
    _q(db_vuoto, "INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, dati, "
                 "hash, conservato_il) VALUES (2, NULL, NULL, 'AUDIT', '{}', $1, now())", h)
    # foglia interna: contenuto immutabile, indice una volta, niente DELETE
    with pytest.raises(asyncpg.RaiseError, match="non modificabile"):
        _q(db_vuoto, "UPDATE public.catena_foglie_interne SET pda = 'altro'")
    _q(db_vuoto, "UPDATE public.catena_foglie_interne SET indice = 1, sequenziata_il = now()")
    with pytest.raises(asyncpg.RaiseError, match="già nel registro"):
        _q(db_vuoto, "UPDATE public.catena_foglie_interne SET indice = 5")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        _q(db_vuoto, "DELETE FROM public.catena_foglie_interne")
    # export dell'audit append-only
    _q(db_vuoto, "INSERT INTO public.audit_esportazioni (periodo_da, periodo_a, righe, "
                 "radice_righe, impronta_segmento, worm_bucket, worm_chiave, foglia_id) VALUES "
                 "(now() - interval '2 hours', now() - interval '1 hour', 0, $1, $2, 'b', 'k', $3)",
       "e3" * 32, "ab" * 32, foglia)
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        _q(db_vuoto, "UPDATE public.audit_esportazioni SET righe = 1")

    # Downgrade rifiutato: il registro contiene foglie interne (append-only)
    with pytest.raises(Exception, match="foglie interne"):
        command.downgrade(_config(), PRIMA)
    assert _q(db_vuoto, "SELECT version_num FROM public.alembic_version")[0]["version_num"] \
        == QUESTA


def test_downgrade_e_nuovo_upgrade_senza_foglie_interne(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), PRIMA)
    _base(db_vuoto)
    command.upgrade(_config(), QUESTA)
    command.downgrade(_config(), PRIMA)
    nullabile = _q(db_vuoto, "SELECT is_nullable FROM information_schema.columns WHERE "
                             "table_name = 'catena_registro' AND column_name = 'aip_id'")
    assert nullabile[0]["is_nullable"] == "NO"
    assert _q(db_vuoto, "SELECT to_regclass('public.catena_foglie_interne') AS t")[0]["t"] is None
    command.upgrade(_config(), QUESTA)
    assert _q(db_vuoto, "SELECT count(*) AS n FROM public.catena_coda")[0]["n"] == 1
