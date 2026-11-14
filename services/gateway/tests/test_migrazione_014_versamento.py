# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Migrazione 20261021_014 (versamento unico, regole, chiave UD) su PostgreSQL reale.

- le righe d'impronta esistenti ricevono la chiave UD normalizzata dal PdV;
- con chiavi UD doppie fra PdV attivi la migrazione si FERMA con l'elenco
  (§16.5) e non cancella nulla; i PdV rifiutati (non attivi) non contano;
- il downgrade toglie tabelle, indice e colonne nuove.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _esegui, db_vuoto  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]

_TENANT = (
    "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, stato, "
    "email_referente) VALUES ($1, $2, 'Cliente', $3, 'ATTIVO', 'r@cliente.it')"
)
_CLASSE = (
    "INSERT INTO public.classi_documentali (id, codice, nome) VALUES ($1, $2, $2) RETURNING id"
)
_PDV = (
    "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
    "metadata_json) VALUES ($1, 'x', 'RICEVUTO', $2, $3, $4, CAST($5 AS jsonb))"
)
_IMPRONTA = (
    "INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
    "impronta_richiesta, attivo) VALUES ($1, $2, $3, $3, $4)"
)


def _prepara(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant, classe = uuid.uuid4(), uuid.uuid4()
    asyncio.run(_esegui(url, _TENANT, tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}"))
    asyncio.run(_esegui(url, _CLASSE, classe, f"c{classe.hex[:8]}"))
    return tenant, classe


def _pdv(
    url: str, tenant: uuid.UUID, classe: uuid.UUID, chiave: dict | None, attivo: bool = True
) -> uuid.UUID:
    pdv = uuid.uuid4()
    asyncio.run(
        _esegui(url, _PDV, pdv, classe, uuid.uuid4(), tenant, json.dumps({"chiave": chiave}))
    )
    asyncio.run(_esegui(url, _IMPRONTA, pdv, tenant, pdv.hex * 2, attivo))
    return pdv


def test_riempimento_chiave_ud_normalizzata(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), "20261020_013")
    tenant, classe = _prepara(db_vuoto)
    con = _pdv(db_vuoto, tenant, classe, {"numero": " 7 ", "anno": "2026", "registro": " fatt "})
    senza = _pdv(db_vuoto, tenant, classe, {"numero": "8"})
    command.upgrade(_config(), "20261021_014")
    righe = {
        r["pdv_id"]: r
        for r in asyncio.run(
            _esegui(
                db_vuoto,
                "SELECT pdv_id, chiave_ud_registro, chiave_ud_anno, chiave_ud_numero "
                "FROM public.versamento_impronte",
            )
        )
    }
    assert (
        righe[con]["chiave_ud_registro"],
        righe[con]["chiave_ud_anno"],
        righe[con]["chiave_ud_numero"],
    ) == ("FATT", "2026", "7")
    assert righe[senza]["chiave_ud_numero"] is None
    avvisi = asyncio.run(_esegui(db_vuoto, "SELECT avvisi::text AS a FROM public.pdv LIMIT 1"))
    assert avvisi[0]["a"] == "[]"


def test_chiavi_ud_doppie_fermano_la_migrazione(db_vuoto: str) -> None:  # noqa: F811
    from alembic import command

    command.upgrade(_config(), "20261020_013")
    tenant, classe = _prepara(db_vuoto)
    chiave = {"numero": "7", "anno": "2026", "registro": "FATT"}
    _pdv(db_vuoto, tenant, classe, chiave)
    _pdv(db_vuoto, tenant, classe, {**chiave, "registro": "fatt"})
    _pdv(db_vuoto, tenant, classe, chiave, attivo=False)  # rifiutato: non conta
    with pytest.raises(Exception, match="chiavi UD doppie") as errore:
        command.upgrade(_config(), "20261021_014")
    assert "numero=7" in str(errore.value)
    righe = asyncio.run(_esegui(db_vuoto, "SELECT count(*) AS n FROM public.versamento_impronte"))
    assert righe[0]["n"] == 3
    versione = asyncio.run(_esegui(db_vuoto, "SELECT version_num FROM public.alembic_version"))
    assert versione[0]["version_num"] == "20261020_013"


def test_indice_unico_solo_fra_attivi_e_downgrade(db_vuoto: str) -> None:  # noqa: F811
    import asyncpg
    from alembic import command

    command.upgrade(_config(), "20261021_014")
    tenant, classe = _prepara(db_vuoto)
    _pdv(db_vuoto, tenant, classe, {"numero": "7", "anno": "2026", "registro": "FATT"})
    asyncio.run(
        _esegui(
            db_vuoto,
            "UPDATE public.versamento_impronte SET chiave_ud_registro = "
            "'FATT', chiave_ud_anno = '2026', chiave_ud_numero = '7'",
        )
    )
    stessa_chiave = (
        "INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
        "impronta_richiesta, attivo, chiave_ud_registro, chiave_ud_anno, "
        "chiave_ud_numero) VALUES ($1, $2, 'x', 'x', $3, 'FATT', '2026', '7')"
    )
    rifiutato = _pdv(db_vuoto, tenant, classe, None, attivo=False)
    asyncio.run(
        _esegui(db_vuoto, "DELETE FROM public.versamento_impronte WHERE pdv_id = $1", rifiutato)
    )
    asyncio.run(_esegui(db_vuoto, stessa_chiave, rifiutato, tenant, False))  # non attivo: ammesso
    attivo = _pdv(db_vuoto, tenant, classe, None)
    asyncio.run(
        _esegui(db_vuoto, "DELETE FROM public.versamento_impronte WHERE pdv_id = $1", attivo)
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(_esegui(db_vuoto, stessa_chiave, attivo, tenant, True))
    command.downgrade(_config(), "20261020_013")
    tabelle = asyncio.run(
        _esegui(
            db_vuoto,
            "SELECT 1 FROM pg_tables WHERE tablename IN "
            "('impostazioni_valori', 'profili_metadati')",
        )
    )
    colonne = asyncio.run(
        _esegui(
            db_vuoto,
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'pdv' "
            "AND column_name IN ('regole_applicate', 'avvisi')",
        )
    )
    assert tabelle == [] and colonne == []
