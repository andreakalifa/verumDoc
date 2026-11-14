# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 2 di R05 — rilievo S1: le migrazioni 036–041 si RIPRENDONO dopo un
errore (``lock_timeout`` o altro) in qualunque punto.

Prima della correzione Alembic girava in UNA transazione per tutta la corsa e
le revisioni con ``autocommit_block`` (CONCURRENTLY, VALIDATE) confermavano a
metà il lavoro fatto senza registrare la versione: dopo un ``lock_timeout``
nella 041 le tabelle di 036/039 restavano create e ogni nuovo ``upgrade``
falliva con «relation … already exists».

Ora (env.py) una transazione per revisione, con ``alembic_version`` aggiornata
insieme all'ultimo segmento; dentro la revisione i segmenti confermati da un
``autocommit_block`` si saltano (controllo di catalogo) e gli indici
CONCURRENTLY rimasti INVALIDI si ricreano.

Prove su PostgreSQL vero:
  1. scenario del revisore: un lettore tiene ACCESS SHARE su ``tenants``, la
     041 va in ``lock_timeout``; rilasciato il lettore, ``upgrade head``
     arriva alla testa e lo schema è IDENTICO a quello di una corsa pulita;
  2. guasto iniettato all'inizio di OGNI segmento (primo comando dopo ogni
     commit) e su OGNI comando in autocommit della corsa 035 → testa: la
     corsa fallisce, la successiva arriva alla testa con lo schema identico;
  3. indice CONCURRENTLY lasciato INVALIDO: la ripresa lo ricrea valido;
  4. dopo la ripresa: downgrade a 035 e di nuovo upgrade alla testa.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.infra_reale import richiede_pg
from tests.test_schema_alembic import _config, _dsn, _esegui, _url_db

pytestmark = [pytest.mark.integration, richiede_pg]

BASE = "20261023_035"

#: Impronta del catalogo (tutto ciò che le migrazioni creano o cambiano).
_CATALOGO = """
SELECT 'colonna' AS k, c.relname || '.' || a.attname || ' ' ||
       format_type(a.atttypid, a.atttypmod) || ' ' || a.attnotnull::text || ' ' ||
       coalesce(pg_get_expr(d.adbin, d.adrelid), '') AS v
FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
  AND c.relkind IN ('r', 'v', 'p')
UNION ALL
SELECT 'vincolo', c.relname || '.' || k.conname || ' ' || k.convalidated::text || ' ' ||
       pg_get_constraintdef(k.oid)
FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'indice', pg_get_indexdef(i.indexrelid) || ' valido=' || i.indisvalid::text
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'trigger', pg_get_triggerdef(t.oid) FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
UNION ALL
SELECT 'funzione', p.proname || ' ' || md5(pg_get_functiondef(p.oid)) FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'politica', tablename || '.' || policyname || ' ' || cmd || ' ' ||
       coalesce(qual, '') || ' ' || coalesce(with_check, '')
FROM pg_policies WHERE schemaname = 'public'
UNION ALL
SELECT 'rls', c.relname || ' ' || c.relrowsecurity::text FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'
UNION ALL
SELECT 'vista', c.relname || ' ' || md5(pg_get_viewdef(c.oid)) || ' ' ||
       coalesce(array_to_string(c.reloptions, ','), '')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'v'
UNION ALL
SELECT 'versione', version_num FROM public.alembic_version
ORDER BY 1, 2
"""


def _impronta(url: str) -> list[tuple[str, str]]:
    return [(r["k"], r["v"]) for r in asyncio.run(_esegui(url, _CATALOGO))]


def _crea_db(modello: str | None = None) -> str:
    nome = f"vds_{uuid.uuid4().hex[:12]}"
    dal = f' TEMPLATE "{modello}"' if modello else ""
    asyncio.run(_esegui(_url_db("postgres"), f'CREATE DATABASE "{nome}"{dal}'))
    return nome


def _elimina_db(nome: str) -> None:
    asyncio.run(_esegui(_url_db("postgres"), f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))


def _upgrade(url: str, monkeypatch: pytest.MonkeyPatch, verso: str = "head") -> str | None:
    from alembic import command

    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(_config(), verso)
    except Exception as exc:  # noqa: BLE001 — l'esito si confronta nel test
        return f"{type(exc).__name__}: {str(exc)[:300]}"
    return None


@pytest.fixture(scope="module")
def modelli() -> Iterator[dict[str, Any]]:
    """Database modello alla 035 e impronta del catalogo di una corsa PULITA
    035 → testa (riferimento)."""
    from alembic import command

    mp = pytest.MonkeyPatch()
    base = _crea_db()
    riferimento = None
    try:
        mp.setenv("DATABASE_URL", _url_db(base))
        command.upgrade(_config(), BASE)
        riferimento = _crea_db(base)
        mp.setenv("DATABASE_URL", _url_db(riferimento))
        command.upgrade(_config(), "head")
        yield {"base": base, "impronta": _impronta(_url_db(riferimento))}
    finally:
        mp.undo()
        if riferimento:
            _elimina_db(riferimento)
        _elimina_db(base)


@pytest.fixture
def db_035(modelli: dict[str, Any]) -> Iterator[str]:
    nome = _crea_db(modelli["base"])
    try:
        yield _url_db(nome)
    finally:
        _elimina_db(nome)


def _differenze(attese: list[tuple[str, str]], trovate: list[tuple[str, str]]) -> list[str]:
    a, t = set(attese), set(trovate)
    return [f"manca {x}" for x in sorted(a - t)] + [f"in più {x}" for x in sorted(t - a)]


def test_upgrade_ripreso_dopo_lock_timeout_su_tenants(
        db_035: str, modelli: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario del revisore rovesciato: lock_timeout nella 041, poi ripresa."""
    import asyncpg

    monkeypatch.setenv("VD_MIGRAZIONE_LOCK_TIMEOUT", "300ms")

    async def tieni_e_migra() -> str | None:
        lettore = await asyncpg.connect(_dsn(db_035))
        await lettore.execute("BEGIN")
        await lettore.execute("LOCK TABLE public.tenants IN ACCESS SHARE MODE")
        try:
            return await asyncio.to_thread(_upgrade, db_035, monkeypatch)
        finally:
            await lettore.execute("ROLLBACK")
            await lettore.close()

    primo = asyncio.run(tieni_e_migra())
    assert primo is not None and "lock timeout" in primo, primo
    versioni = asyncio.run(_esegui(db_035, "SELECT version_num FROM public.alembic_version"))
    # le revisioni completate restano REGISTRATE (una transazione per revisione)
    assert [r["version_num"] for r in versioni] == ["20261023_040"]

    secondo = _upgrade(db_035, monkeypatch)
    assert secondo is None, secondo
    assert _differenze(modelli["impronta"], _impronta(db_035)) == []


def _registra_corsa(url: str, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Corsa 035 → testa che registra i comandi: ("COMMIT", False) per ogni
    commit, (comando, in_autocommit) per ogni istruzione."""
    eventi: list[tuple[str, bool]] = []

    def prima(conn: Any, _c: Any, istruzione: str, *_a: Any) -> None:
        auto = conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
        eventi.append((istruzione, auto))

    def commit(_conn: Any) -> None:
        eventi.append(("COMMIT", False))

    event.listen(Engine, "before_cursor_execute", prima)
    event.listen(Engine, "commit", commit)
    try:
        assert _upgrade(url, monkeypatch) is None
    finally:
        event.remove(Engine, "before_cursor_execute", prima)
        event.remove(Engine, "commit", commit)
    return eventi


def _punti_di_guasto(eventi: list[tuple[str, bool]]) -> list[tuple[int, str]]:
    """Indici (fra le sole istruzioni) dei punti in cui iniettare l'errore:
    primo comando di ogni segmento transazionale (dopo un commit o dopo
    l'autocommit) e ogni comando in autocommit."""
    punti: list[tuple[int, str]] = []
    n = -1
    dopo_confine = True
    precedente_auto = False
    for istruzione, auto in eventi:
        if istruzione == "COMMIT":
            dopo_confine = True
            continue
        n += 1
        if auto or dopo_confine or precedente_auto:
            punti.append((n, " ".join(istruzione.split())[:90]))
        dopo_confine = False
        precedente_auto = auto
    return punti


class _Guasto(RuntimeError):
    pass


def test_guasto_in_ogni_segmento_e_ripresa_fino_alla_testa(
        modelli: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Per OGNI confine di segmento e OGNI comando in autocommit della corsa
    035 → testa: errore iniettato in quel punto → corsa fallita → nuova corsa
    senza errori fino alla testa, con il catalogo identico al riferimento."""
    nome = _crea_db(modelli["base"])
    try:
        eventi = _registra_corsa(_url_db(nome), monkeypatch)
    finally:
        _elimina_db(nome)
    punti = _punti_di_guasto(eventi)
    # la corsa ha davvero segmenti in autocommit (036, 037, 038, 039, 041)
    assert sum(1 for i, a in eventi if a) >= 20
    assert len(punti) >= 30
    falliti: list[str] = []
    for indice, descrizione in punti:
        contatore = {"n": -1}

        def guasto(_conn: Any, _c: Any, _istruzione: str, *_a: Any,
                   _bersaglio: int = indice, _conta: dict[str, int] = contatore) -> None:
            _conta["n"] += 1
            if _conta["n"] == _bersaglio:
                raise _Guasto(f"guasto simulato al comando {_bersaglio}")

        nome = _crea_db(modelli["base"])
        url = _url_db(nome)
        try:
            event.listen(Engine, "before_cursor_execute", guasto)
            try:
                primo = _upgrade(url, monkeypatch)
            finally:
                event.remove(Engine, "before_cursor_execute", guasto)
            if primo is None or "guasto simulato" not in primo:
                falliti.append(f"[{indice}] {descrizione}: guasto non avvenuto ({primo})")
                continue
            secondo = _upgrade(url, monkeypatch)
            if secondo is not None:
                falliti.append(f"[{indice}] {descrizione}: ripresa fallita: {secondo}")
                continue
            diff = _differenze(modelli["impronta"], _impronta(url))
            if diff:
                falliti.append(f"[{indice}] {descrizione}: schema diverso: {diff[:5]}")
        finally:
            _elimina_db(nome)
    assert falliti == [], "\n".join(falliti)


def test_indice_concurrently_invalido_si_ricrea(
        db_035: str, modelli: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Un CREATE INDEX CONCURRENTLY interrotto lascia l'indice INVALIDO: la
    ripresa lo elimina e lo ricrea (IF NOT EXISTS da solo lo terrebbe)."""
    assert _upgrade(db_035, monkeypatch, "20261023_036") is None
    # simula l'indice lasciato a metà: stesso nome, marcato non valido
    asyncio.run(_esegui(db_035, """
        UPDATE pg_index SET indisvalid = false
        WHERE indexrelid = 'public.idx_pdv_classe_ricezione'::regclass"""))
    asyncio.run(_esegui(db_035, "DELETE FROM public.alembic_version"))
    asyncio.run(_esegui(db_035, "INSERT INTO public.alembic_version VALUES ($1)", BASE))
    assert _upgrade(db_035, monkeypatch) is None
    valido = asyncio.run(_esegui(db_035, """
        SELECT indisvalid FROM pg_index
        WHERE indexrelid = 'public.idx_pdv_classe_ricezione'::regclass"""))
    assert valido[0]["indisvalid"] is True
    assert _differenze(modelli["impronta"], _impronta(db_035)) == []


def test_ripresa_poi_downgrade_035_e_upgrade(
        db_035: str, modelli: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Dopo un'interruzione e la ripresa la catena resta reversibile:
    downgrade a 035 e di nuovo upgrade alla testa, schema identico."""
    from alembic import command

    contatore = {"n": 0}

    def guasto(_conn: Any, _c: Any, istruzione: str, *_a: Any) -> None:
        if "VALIDATE CONSTRAINT ck_catena_registro_pda" in istruzione:
            contatore["n"] += 1
            raise _Guasto("guasto simulato nella convalida della 039")

    event.listen(Engine, "before_cursor_execute", guasto)
    try:
        assert "guasto simulato" in (_upgrade(db_035, monkeypatch) or "")
    finally:
        event.remove(Engine, "before_cursor_execute", guasto)
    assert contatore["n"] == 1
    assert _upgrade(db_035, monkeypatch) is None
    command.downgrade(_config(), BASE)
    versioni = asyncio.run(_esegui(db_035, "SELECT version_num FROM public.alembic_version"))
    assert [r["version_num"] for r in versioni] == [BASE]
    assert _upgrade(db_035, monkeypatch) is None
    assert _differenze(modelli["impronta"], _impronta(db_035)) == []
