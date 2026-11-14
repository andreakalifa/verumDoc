# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Correzioni della revisione indipendente R04 (rev4 rilievo 5): l'audit e gli altri
registri append-only non si cancellano con DETACH + DROP. Provato con ruoli NON
superutente creati nel test (migrazione 20261023_031):

  - ruolo dei servizi (membro di ``verumdoc_applicazione``, non proprietario):
    scrive e legge, ma non cancella, non stacca, non elimina, non disattiva trigger;
    la manutenzione delle partizioni con questo ruolo non può agganciare;
  - ruolo proprietario non superutente (quello della manutenzione): crea e
    aggancia le partizioni; gli event trigger gli rifiutano DETACH (anche
    CONCURRENTLY o nascosto in SQL dinamico), DROP e DISABLE TRIGGER sui registri;
  - la prova del revisore (superutente: DETACH + DROP) ora fallisce.

Secondo giro (migrazione 20261023_033, rilievo 5 contro il proprietario): i
registri hanno un proprietario dedicato ``verumdoc_audit_proprietario``
(NOLOGIN); la manutenzione (membro di ``verumdoc_manutenzione``) ne crea le
partizioni solo con ``vd_crea_partizione_registro`` e non ne è proprietaria.
Anche un login membro del proprietario (configurazione sbagliata simulata nel
test) non aggira le difese: FORCE RLS, event trigger su funzioni, regole,
trigger, politiche, RENAME e stato a fine comando (prova_audit_bypass.sh).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from libs.shared.db.partizioni import (
    POLITICHE,
    esegui_manutenzione_partizioni,
    inizio_periodo,
    nome_partizione,
)
from tests.infra_reale import _asyncpg_dsn, _con_database, db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

_POL = {p.tabella: p for p in POLITICHE}


def _dsn_ruolo(url: str, ruolo: str) -> str:
    """DSN del database di prova con un altro utente (server di prova: trust)."""
    dsn = _asyncpg_dsn(url)
    resto = dsn.split("@", 1)[1]
    return f"postgresql://{ruolo}@{resto}"


async def _partizione_audit_corrente(url: str) -> str:
    return "partizioni." + nome_partizione("audit_global", "mese",
                                           inizio_periodo("mese", datetime.now(UTC)))


@pytest.fixture
async def ruoli(db_url):  # noqa: F811
    """Ruoli NON superutente, eliminati alla fine:
      - ``app``: login dei servizi (membro di ``verumdoc_applicazione``);
      - ``manut``: login della manutenzione (``verumdoc_manutenzione`` e
        ``verumdoc_applicazione``), proprietaria delle sole tabelle operative;
      - ``prop``: login membro del proprietario dei registri (NON previsto in
        esercizio: simula l'attaccante con i diritti del proprietario)."""
    suffisso = uuid.uuid4().hex[:8]
    app, manut, prop = f"vd_app_{suffisso}", f"vd_man_{suffisso}", f"vd_prop_{suffisso}"
    admin = await asyncpg.connect(_asyncpg_dsn(db_url))
    try:
        await admin.execute(f"CREATE ROLE {app} LOGIN IN ROLE verumdoc_applicazione")
        await admin.execute(f"CREATE ROLE {manut} LOGIN IN ROLE verumdoc_manutenzione, "
                            "verumdoc_applicazione")
        await admin.execute(f"CREATE ROLE {prop} LOGIN IN ROLE verumdoc_audit_proprietario, "
                            "verumdoc_applicazione")
        await admin.execute(f"GRANT CREATE ON SCHEMA public TO {prop}")
        yield app, prop, manut
    finally:
        for r in (app, prop, manut):
            await admin.execute(f"REASSIGN OWNED BY {r} TO CURRENT_USER")
            await admin.execute(f"DROP OWNED BY {r}")
        await admin.close()
        # il database si elimina dopo; i ruoli ora non possiedono nulla
        fuori = await asyncpg.connect(_asyncpg_dsn(_con_database(db_url, "postgres")))
        try:
            for r in (app, prop, manut):
                await fuori.execute(f"DROP ROLE IF EXISTS {r}")
        finally:
            await fuori.close()


async def _come(url: str, ruolo: str, istruzione: str, *argomenti):
    conn = await asyncpg.connect(_dsn_ruolo(url, ruolo))
    try:
        return await conn.fetch(istruzione, *argomenti)
    finally:
        await conn.close()


async def test_ruolo_dei_servizi_scrive_ma_non_cancella_ne_stacca(db_url, ruoli):
    app, _, _ = ruoli
    partizione = await _partizione_audit_corrente(db_url)
    assert not (await sql(db_url, f"SELECT rolsuper FROM pg_roles WHERE rolname = '{app}'"))[0][  # noqa: S608 — frammenti SQL costanti
        "rolsuper"]
    await _come(db_url, app, "INSERT INTO public.audit_global (evento) VALUES ('prova.ruolo')")
    assert len(await _come(db_url, app, "SELECT 1 FROM public.audit_global "
                                         "WHERE evento = 'prova.ruolo'")) == 1
    # DML ordinario sulle tabelle dei servizi (outbox, chiavi ferme, lease)
    await _come(db_url, app, "INSERT INTO public.outbox_eventi (origine, gruppo, topic, payload) "
                             "VALUES ('prova', 0, 't', '{}'::jsonb)")
    for istruzione in (
        "DELETE FROM public.audit_global",
        "UPDATE public.audit_global SET evento = 'x'",
        "TRUNCATE public.audit_global",
        f"ALTER TABLE public.audit_global DETACH PARTITION {partizione}",
        f"DROP TABLE {partizione}",
        "ALTER TABLE public.audit_global DISABLE TRIGGER trg_audit_global_append_only",
        "DELETE FROM public.catena_registro",
        "DELETE FROM public.verifiche_registro",
        "DROP TABLE public.outbox_eventi",
        "ALTER TABLE public.outbox_eventi DETACH PARTITION "
        + partizione.replace("audit_global", "outbox_eventi"),
    ):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _come(db_url, app, istruzione)
    assert (await sql(db_url, "SELECT count(*) AS n FROM public.audit_global "
                              "WHERE evento = 'prova.ruolo'"))[0]["n"] == 1


async def test_proprietario_non_superutente_fermato_dagli_event_trigger(db_url, ruoli):
    _, prop, _ = ruoli
    partizione = await _partizione_audit_corrente(db_url)
    await sql(db_url, "INSERT INTO public.audit_global (evento) VALUES ('da.nascondere')")
    # nessun nome del registro nel testo inviato: il controllo testuale non scatta,
    # quello sullo stato a fine comando sì
    spezzata = partizione.replace("audit_global", "audit_' || 'global")
    nascosto_detach = (
        "DO $$ BEGIN EXECUTE 'ALTER TABLE public.audit_' || 'global DETACH PARTITION "
        f"{spezzata}'; END $$")
    nascosto_trigger = (
        "DO $$ BEGIN EXECUTE 'ALTER TABLE public.audit_' || 'global DISABLE TRIGGER ' "
        "|| 'trg_audit_' || 'global_append_only'; END $$")
    for istruzione, atteso in (
        (f"ALTER TABLE public.audit_global DETACH PARTITION {partizione}", "DETACH"),
        (f"ALTER TABLE public.audit_global DETACH PARTITION {partizione} CONCURRENTLY",
         "DETACH"),
        (nascosto_detach, "staccata"),                      # controllo a fine comando
        (f"DROP TABLE {partizione}", "eliminazione"),
        ("DROP TABLE public.catena_registro", "eliminazione|must be owner"),
        ("ALTER TABLE public.audit_global DISABLE TRIGGER trg_audit_global_append_only",
         "DISABLE"),
        (nascosto_trigger, "trigger delle difese disattivato"),
        ("ALTER TABLE public.verifiche_registro DISABLE TRIGGER ALL", "DISABLE"),
    ):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError, match=atteso):
            await _come(db_url, prop, istruzione)
    assert (await sql(db_url, "SELECT count(*) AS n FROM public.audit_global "
                              "WHERE evento = 'da.nascondere'"))[0]["n"] == 1


async def test_manutenzione_con_ruolo_dedicato_si_con_ruolo_dei_servizi_no(db_url, ruoli):
    from sqlalchemy.ext.asyncio import create_async_engine

    app, _, manut = ruoli
    adesso = datetime.now(UTC) + timedelta(days=200)
    politiche = [_POL["audit_global"], _POL["verifiche_registro"], _POL["outbox_eventi"]]
    url_app = _dsn_ruolo(db_url, app).replace("postgresql://", "postgresql+asyncpg://", 1)
    url_man = _dsn_ruolo(db_url, manut).replace("postgresql://", "postgresql+asyncpg://", 1)
    engine_app = create_async_engine(url_app)
    engine_man = create_async_engine(url_man)
    try:
        with pytest.raises(Exception, match="must be owner|permission denied"):
            await esegui_manutenzione_partizioni(engine_app, adesso, politiche)
        esito = await esegui_manutenzione_partizioni(engine_man, adesso, politiche)
        assert esito is not None and esito.create["audit_global"] >= 1
        assert esito.create["verifiche_registro"] >= 1 and esito.create["outbox_eventi"] >= 1
    finally:
        await engine_app.dispose()
        await engine_man.dispose()
    for tabella in ("audit_global", "verifiche_registro"):
        nome = nome_partizione(tabella, "mese", inizio_periodo("mese", adesso))
        prot = await sql(db_url, "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                                 "c.relispartition, c.relowner::regrole::text AS proprietario, "
                                 "(SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid "
                                 " AND t.tgname LIKE '%no_truncate') AS truncate, "
                                 "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
                                 "AS politiche FROM pg_class c WHERE c.oid = $1::regclass",
                         f"partizioni.{nome}")
        assert prot[0]["relispartition"] and prot[0]["truncate"] == 1
        assert prot[0]["proprietario"] == "verumdoc_audit_proprietario"
        if tabella == "audit_global":
            assert prot[0]["relrowsecurity"] and prot[0]["relforcerowsecurity"]
            assert prot[0]["politiche"] == 2


async def test_manutenzione_non_proprietaria_dei_registri(db_url, ruoli):
    """La manutenzione crea le partizioni dei registri SOLO con la funzione del
    proprietario dedicato: niente DETACH, ALTER o DROP (non è proprietaria) e la
    funzione accetta soltanto un registro e l'inizio di un mese."""
    _, _, manut = ruoli
    partizione = await _partizione_audit_corrente(db_url)
    for istruzione in (
        f"ALTER TABLE public.audit_global DETACH PARTITION {partizione}",
        "ALTER TABLE public.audit_global NO FORCE ROW LEVEL SECURITY",
        "CREATE RULE r AS ON INSERT TO public.audit_global DO INSTEAD NOTHING",
        f"DROP TABLE {partizione}",
        "ALTER TABLE public.verifiche_registro DISABLE TRIGGER ALL",
    ):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError,
                           match="must be owner|registro append-only"):
            await _come(db_url, manut, istruzione)
    for argomenti, atteso in ((("outbox_eventi", "2027-01-01T00:00:00+00:00"),
                               "non è un registro"),
                              (("audit_global", "2027-01-02T00:00:00+00:00"),
                               "non è l'inizio di un mese")):
        with pytest.raises(asyncpg.exceptions.RaiseError, match=atteso):
            await _come(db_url, manut, "SELECT public.vd_crea_partizione_registro($1, $2)",
                        argomenti[0], datetime.fromisoformat(argomenti[1]))
    # la funzione è idempotente: una partizione esistente non si ricrea
    corrente = inizio_periodo("mese", datetime.now(UTC))
    assert (await _come(db_url, manut, "SELECT public.vd_crea_partizione_registro($1, $2) AS f",
                        "audit_global", corrente))[0]["f"] is False


async def _rende_proprietario_della_funzione(url: str, ruolo: str) -> None:
    """Configurazione sbagliata simulata: la funzione del trigger append-only al
    proprietario (l'event trigger impedisce anche questo: lo si sospende qui)."""
    await sql(url, "ALTER EVENT TRIGGER vd_protezione_registri_oggetti DISABLE")
    await sql(url, f"ALTER FUNCTION public.vd_audit_append_only() OWNER TO {ruolo}")
    await sql(url, "ALTER EVENT TRIGGER vd_protezione_registri_oggetti ENABLE ALWAYS")


async def test_prova_del_revisore_proprietario_non_aggira_le_difese(db_url, ruoli):
    """sh prova_audit_bypass.sh: prima il proprietario sostituiva la funzione del
    trigger append-only («voci rimaste: 0») e inghiottiva le voci nuove con una
    RULE («voci inghiottite presenti: 0»). Ora ogni via è rifiutata."""
    _, prop, _ = ruoli
    await _rende_proprietario_della_funzione(db_url, prop)
    partizione = await _partizione_audit_corrente(db_url)
    nome_partizione_corrente = partizione.split(".")[1]
    spezzata = partizione.replace("audit_global", "audit_' || 'global")
    await sql(db_url, "INSERT INTO public.audit_global (evento) VALUES ('voce.da.nascondere')")
    await _come(db_url, prop, "CREATE FUNCTION public.prova_inghiotti() RETURNS trigger "
                              "LANGUAGE plpgsql AS $f$BEGIN RETURN NULL; END$f$")
    for istruzione, atteso in (
        # a) funzione del trigger sostituita o modificata
        ("CREATE OR REPLACE FUNCTION public.vd_audit_append_only() RETURNS trigger "
         "LANGUAGE plpgsql AS $f$BEGIN IF TG_OP='DELETE' THEN RETURN OLD; END IF; "
         "RETURN NEW; END$f$", "funzione di protezione"),
        ("ALTER FUNCTION public.vd_audit_append_only() SECURITY DEFINER",
         "funzione di protezione"),
        ("DROP FUNCTION public.vd_audit_append_only() CASCADE", "eliminazione"),
        # b) regola o trigger che inghiottono le voci nuove
        ("CREATE RULE nascondi AS ON INSERT TO public.audit_global DO INSTEAD NOTHING",
         "regole vietate"),
        (f"CREATE RULE nascondi AS ON INSERT TO {partizione} DO INSTEAD NOTHING",
         "regole vietate"),
        ("CREATE TRIGGER inghiotti BEFORE INSERT ON public.audit_global FOR EACH ROW "
         "EXECUTE FUNCTION public.prova_inghiotti()", "trigger inghiotti non ammesso"),
        ("ALTER TRIGGER trg_audit_global_append_only ON public.audit_global RENAME TO x",
         "non ammesso"),
        # RLS: niente DISABLE né NO FORCE, nemmeno nascosti in SQL dinamico
        ("ALTER TABLE public.audit_global NO FORCE ROW LEVEL SECURITY", "NO FORCE"),
        ("ALTER TABLE public.audit_global DISABLE ROW LEVEL SECURITY", "DISABLE"),
        ("DO $$ BEGIN EXECUTE 'ALTER TABLE public.audit_' || 'global N' || 'O FORCE ROW "
         "LEVEL SECURITY'; END $$", "RLS e FORCE"),
        (f"DO $$ BEGIN EXECUTE 'ALTER TABLE {spezzata} DIS' || 'ABLE ROW LEVEL SECURITY'; "
         "END $$", "RLS e FORCE"),
        # trigger delle difese attivi solo in replica: non scattano più
        ("ALTER TABLE public.audit_global ENABLE REPLICA TRIGGER trg_audit_global_append_only",
         "trigger delle difese disattivato"),
        # politiche: né eliminate, né cambiate, né aggiunte
        ("DROP POLICY audit_global_select_all ON public.audit_global", "eliminazione"),
        ("ALTER POLICY audit_global_select_all ON public.audit_global USING (false)",
         "non ammessa"),
        ("CREATE POLICY cancella ON public.audit_global FOR DELETE USING (true)", "non ammessa"),
        # partizione rinominata e poi staccata in SQL dinamico
        (f"ALTER TABLE {partizione} RENAME TO nascosta", "rinominata"),
        ("ALTER TABLE public.audit_global RENAME TO audit_vecchio", "non si rinomina"),
    ):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError, match=atteso):
            await _come(db_url, prop, istruzione)
    # DELETE e TRUNCATE del proprietario: FORCE RLS (nessuna politica di DELETE) e trigger
    assert await _come(db_url, prop, "DELETE FROM public.audit_global "
                                     "WHERE evento = 'voce.da.nascondere' RETURNING 1") == []
    for istruzione in ("TRUNCATE public.audit_global", f"TRUNCATE {partizione}"):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _come(db_url, prop, istruzione)
    await sql(db_url, "INSERT INTO public.audit_global (evento) VALUES ('voce.nuova')")
    conta = await sql(db_url, "SELECT evento, count(*) AS n FROM public.audit_global "
                              "WHERE evento IN ('voce.da.nascondere', 'voce.nuova') "
                              "GROUP BY evento ORDER BY evento")
    assert [(r["evento"], r["n"]) for r in conta] == [("voce.da.nascondere", 1),
                                                     ("voce.nuova", 1)]
    stato = await sql(db_url, "SELECT relrowsecurity AND relforcerowsecurity AS rls, relname "
                              "FROM pg_class WHERE oid = $1::regclass", partizione)
    assert stato[0]["rls"] and stato[0]["relname"] == nome_partizione_corrente


async def test_prova_del_revisore_superutente_detach_drop_rifiutati(db_url):
    """sh prova_audit_detach.sh: prima «righe dopo DETACH+DROP: 0»."""
    partizione = await _partizione_audit_corrente(db_url)
    await sql(db_url, "INSERT INTO public.audit_global (evento) "
                      "VALUES ('operazione.da.nascondere')")
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await sql(db_url, f"ALTER TABLE public.audit_global DETACH PARTITION {partizione}")
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await sql(db_url, f"DROP TABLE {partizione}")
    assert (await sql(db_url, "SELECT count(*) AS n FROM public.audit_global "
                              "WHERE evento = 'operazione.da.nascondere'"))[0]["n"] == 1


async def test_migrazioni_con_il_ruolo_amministratore(db_url):
    """Ruoli separati in sviluppo (compose): il gateway entra come ruolo dei
    servizi (DATABASE_URL) e migra con DATABASE_URL_MIGRAZIONI. Alembic deve
    usare quest'ultima: con un DATABASE_URL irraggiungibile risponde lo stesso."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    gateway = Path(__file__).resolve().parents[1]
    ambiente = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://nessuno@127.0.0.1:1/nulla",
                "DATABASE_URL_MIGRAZIONI": db_url}
    esito = subprocess.run([sys.executable, "-m", "alembic", "current"], cwd=gateway,
                           env=ambiente, capture_output=True, text=True, timeout=120)
    assert esito.returncode == 0, esito.stderr[-500:]
    assert "20261114_060 (head)" in esito.stdout


# ── Terzo giro (migrazione 20261023_034): T1, T2 e migrazioni future ────────

async def test_t1_t2_proprietario_non_cambia_struttura_ne_crea_partizioni(db_url, ruoli):
    """prova_audit_bypass_v3.sh (T1) e prova_audit_partizione_futura.sh (T2): prima
    il proprietario aggiungeva un CHECK che faceva fallire voci d'audit scelte e
    creava una partizione futura senza difese (che poi bloccava manutenzione e
    migrazioni). Ora entrambi rifiutati; la manutenzione continua a creare."""
    _, prop, manut = ruoli
    partizione = await _partizione_audit_corrente(db_url)
    for istruzione, atteso in (
        ("ALTER TABLE public.audit_global ADD CONSTRAINT nessuna "
         "CHECK (evento <> 'voce.nuova') NOT VALID", "struttura di un registro"),
        ("ALTER TABLE public.verifiche_registro ALTER COLUMN esito DROP NOT NULL",
         "struttura di un registro"),
        ("CREATE UNIQUE INDEX solo_uno ON public.audit_global (evento, created_at)",
         "struttura di un registro"),
        (f"ALTER TABLE {partizione} ADD CONSTRAINT nessuna CHECK (evento <> 'voce.nuova')",
         "proprio di una partizione"),
        (f"ALTER TABLE {partizione} ALTER COLUMN ip_address SET NOT NULL",
         "proprio di una partizione|struttura"),
        ("CREATE TABLE partizioni.audit_global_p209901 PARTITION OF public.audit_global "
         "FOR VALUES FROM ('2099-01-01') TO ('2099-02-01')", "solo con vd_crea_partizione"),
    ):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError, match=atteso):
            await _come(db_url, prop, istruzione)
    await sql(db_url, "INSERT INTO public.audit_global (evento) VALUES ('voce.nuova')")
    assert (await _come(db_url, manut, "SELECT public.vd_crea_partizione_registro($1, $2) AS f",
                        "audit_global", datetime(2031, 1, 1, tzinfo=UTC)))[0]["f"] is True
    # Una migrazione futura che cambia un registro SENZA registri_modificabili è fermata
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError, match="struttura"):
        await sql(db_url, "ALTER TABLE public.audit_global ADD COLUMN y int")


async def test_event_trigger_dei_registri_enable_always_alla_testa(db_url):
    righe = await sql(db_url, "SELECT evtname, evtenabled::text AS evtenabled "
                              "FROM pg_event_trigger "
                              "WHERE evtname LIKE 'vd_protezione_registri_%' ORDER BY 1")
    assert [(r["evtname"], r["evtenabled"]) for r in righe] == [
        ("vd_protezione_registri_alter", "A"), ("vd_protezione_registri_drop", "A"),
        ("vd_protezione_registri_oggetti", "A"), ("vd_protezione_registri_stato", "A")]
    impronte = await sql(db_url, "SELECT tabella FROM protezione.impronte_registri ORDER BY 1")
    assert [r["tabella"] for r in impronte] == ["audit_global", "verifiche_registro"]


async def _con_operations(url: str, funzione, *, autocommit: bool):
    """Esegue ``funzione(op)`` con un ``alembic.operations.Operations`` reale."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            if autocommit:
                conn = await conn.execution_options(isolation_level="AUTOCOMMIT")

            def _sync(sincrona):
                return funzione(Operations(MigrationContext.configure(sincrona)))

            try:
                return await conn.run_sync(_sync)
            finally:
                if not autocommit:
                    await conn.rollback()
    finally:
        await engine.dispose()


async def test_migrazione_futura_con_registri_modificabili(db_url):
    """Punto 4: la funzione di supporto sospende gli event trigger per il blocco e
    li riattiva SEMPRE (finally), aggiornando le impronte della struttura."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from libs.shared.db.migrazioni_registri import registri_modificabili

    def migrazione(op):
        with registri_modificabili(op, motivo="prova: colonna e politica nuove"):
            op.execute("ALTER TABLE public.audit_global ADD COLUMN prova_colonna int")
            op.execute("CREATE POLICY prova_lettura ON public.audit_global FOR SELECT "
                       "USING (true)")

    # Transazione confermata: modifiche applicate, trigger attivi, impronte aggiornate
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            from alembic.migration import MigrationContext
            from alembic.operations import Operations

            await conn.run_sync(lambda s: migrazione(Operations(MigrationContext.configure(s))))
    finally:
        await engine.dispose()
    stato = await sql(db_url, "SELECT count(*) FILTER (WHERE evtenabled <> 'A') AS spenti "
                              "FROM pg_event_trigger")
    assert stato[0]["spenti"] == 0
    # dopo: un ALTER TABLE qualsiasi passa (impronta aggiornata), le difese tornano attive
    await sql(db_url, "ALTER TABLE public.outbox_eventi ALTER COLUMN topic SET STATISTICS 100")
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError, match="non ammessa"):
        await sql(db_url, "CREATE POLICY altra ON public.audit_global FOR SELECT USING (true)")

    # Errore nel blocco, AUTOCOMMIT: il finally riattiva; l'errore originale resta
    def fallisce(op):
        with registri_modificabili(op, motivo="prova d'errore"):
            raise ValueError("errore della migrazione")

    with pytest.raises(ValueError, match="errore della migrazione"):
        await _con_operations(db_url, fallisce, autocommit=True)
    # Errore in transazione: ROLLBACK (la sospensione è annullata), errore non coperto
    def fallisce_sql(op):
        with registri_modificabili(op, motivo="prova d'errore in transazione"):
            op.execute("SELECT 1/0")

    with pytest.raises(Exception, match="division by zero"):
        await _con_operations(db_url, fallisce_sql, autocommit=False)
    righe = await sql(db_url, "SELECT evtname FROM pg_event_trigger WHERE evtenabled <> 'A'")
    assert righe == []
    with pytest.raises(ValueError, match="motivo"):
        with registri_modificabili(object(), motivo=" "):
            pass
