# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R14 (3) — migrazione ``20261109_056`` riprendibile anche dopo un
``CREATE UNIQUE INDEX CONCURRENTLY`` interrotto.

Un indice CONCURRENTLY fallito resta nel catalogo INVALIDO: con il solo ``IF NOT EXISTS`` la
ripresa lo terrebbe e il vincolo della cronologia (VD-C-121) non varrebbe. Ora la ripresa lo
elimina e lo ricrea valido; poi la migrazione si può togliere e rimettere.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest

from tests.infra_reale import PG_TEMPLATE_URL, richiede_pg
from tests.test_r05_revisione2_migrazioni_riprendibili import _crea_db, _elimina_db, _upgrade
from tests.test_schema_alembic import _config, _esegui, _url_db

pytestmark = [pytest.mark.integration, richiede_pg]

INDICE = "public.uq_pratiche_stati_precedente"


def _valido(url: str) -> bool | None:
    righe = asyncio.run(_esegui(url, "SELECT i.indisvalid FROM pg_index i "
                                     f"WHERE i.indexrelid = to_regclass('{INDICE}')"))
    return righe[0]["indisvalid"] if righe else None


def test_indice_invalido_della_056_si_ricrea(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command

    nome = _crea_db(urlsplit(PG_TEMPLATE_URL).path.lstrip("/"))
    url = _url_db(nome)
    try:
        assert _valido(url) is True
        # indice lasciato a metà da una corsa interrotta, versione rimasta alla 055
        asyncio.run(_esegui(url, "UPDATE pg_index SET indisvalid = false "
                                 f"WHERE indexrelid = '{INDICE}'::regclass"))
        asyncio.run(_esegui(url, "UPDATE public.alembic_version SET version_num = "
                                 "'20261108_055'"))
        assert _valido(url) is False
        assert _upgrade(url, monkeypatch) is None
        assert _valido(url) is True
        # reversibile: giù alla 055 (indice e colonna tolti) e di nuovo su
        monkeypatch.setenv("DATABASE_URL", url)
        command.downgrade(_config(), "20261108_055")
        assert _valido(url) is None
        assert _upgrade(url, monkeypatch) is None
        assert _valido(url) is True
    finally:
        _elimina_db(nome)
