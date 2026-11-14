# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R18.2 — migrazione ``20261114_060_rimozione_adattatori`` (VD-C-187, VD-P-191).

  - tabella vuota: eliminata;
  - tabella con righe: l'upgrade si ferma (nessun dato perso senza conferma) e la
    tabella resta; con ``VD_MIGRAZIONE_060_ELIMINA_RICHIESTE=1`` è eliminata e i PdV
    restano;
  - reversibile (giù alla 059: tabella ricreata vuota con la politica per ente, di nuovo su).
"""
from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.infra_reale import PG_TEMPLATE_URL, richiede_pg
from tests.test_r05_revisione2_migrazioni_riprendibili import _crea_db, _elimina_db
from tests.test_schema_alembic import _config, _esegui, _url_db

pytestmark = [pytest.mark.integration, richiede_pg]

_SPEC = importlib.util.spec_from_file_location(
    "migrazione_060", Path(__file__).resolve().parents[1]
    / "alembic/versions/20261114_060_rimozione_adattatori.py")
_MIGRAZIONE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRAZIONE)  # type: ignore[union-attr]
#: nome della tabella tolta e variabile di conferma: quelli della migrazione
TABELLA: str = _MIGRAZIONE.TABELLA
CONFERMA: str = _MIGRAZIONE.CONFERMA


def _sql(url: str, istruzione: str, *argomenti):  # type: ignore[no-untyped-def]
    return asyncio.run(_esegui(url, istruzione, *argomenti))


def _esiste(url: str) -> bool:
    [riga] = _sql(url, f"SELECT to_regclass('public.{TABELLA}') IS NOT NULL AS c")
    return bool(riga["c"])


def _riga(url: str) -> None:
    ente = uuid.uuid4()
    _sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
              "email_referente) VALUES ($1, $2, 'Ente di prova', $3, 'e@example.org')",
         ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    _sql(url, f"INSERT INTO public.{TABELLA} (tenant_id, chiave, codice_servizio, "  # noqa: S608
              "tipologia_documento, documenti, utente_id) VALUES ($1, 'k', 's', 't', "
              "'[]'::jsonb, $2)", ente, uuid.uuid4())


def test_migrazione_060_elimina_solo_con_conferma_se_ci_sono_righe(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command

    nome = _crea_db(urlsplit(PG_TEMPLATE_URL).path.lstrip("/"))
    url = _url_db(nome)
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.delenv(CONFERMA, raising=False)
        assert not _esiste(url)
        # giù: tabella ricreata vuota con la politica per ente
        command.downgrade(_config(), "20261112_059")
        assert _esiste(url)
        [politica] = _sql(url, "SELECT count(*) AS n FROM pg_policies WHERE tablename = $1 "
                               "AND policyname = 'vd_ente_isolamento'", TABELLA)
        assert politica["n"] == 1
        # vuota: eliminata senza conferma
        command.upgrade(_config(), "head")
        assert not _esiste(url)
        # con righe: si ferma, la tabella resta
        command.downgrade(_config(), "20261112_059")
        _riga(url)
        with pytest.raises(Exception, match="contiene righe"):
            command.upgrade(_config(), "head")
        assert _esiste(url)
        [versione] = _sql(url, "SELECT version_num FROM public.alembic_version")
        assert versione["version_num"] == "20261112_059"
        # con la conferma: eliminata
        monkeypatch.setenv(CONFERMA, "1")
        command.upgrade(_config(), "head")
        assert not _esiste(url)
        [versione] = _sql(url, "SELECT version_num FROM public.alembic_version")
        assert versione["version_num"] == "20261114_060"
    finally:
        _elimina_db(nome)
