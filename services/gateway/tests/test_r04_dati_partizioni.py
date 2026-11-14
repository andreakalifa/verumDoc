# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 dati — partizioni: creazione anticipata, distacco a fine retention,
controlli che bloccano il distacco, audit append-only anche partizionato
(VD-C-076, VD-C-103; piano §4.2). PostgreSQL reale, migrazione 20261023_028.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from libs.shared.db import engine_manutenzione  # R05: ruolo proprietario
from libs.shared.db import metriche as metriche_db
from libs.shared.db.partizioni import (
    POLITICHE,
    RETENZIONE_EVENTI_CLIENTE_GIORNI,
    RETENZIONE_OUTBOX_GIORNI,
    esegui_manutenzione_partizioni,
    inizio_periodo,
    nome_partizione,
    periodo_successivo,
)
from tests.infra_reale import (
    _asyncpg_dsn,
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]

_POL = {p.tabella: p for p in POLITICHE}


async def _partizioni(url: str, tabella: str) -> list[str]:
    righe = await sql(url, "SELECT c.relname FROM pg_inherits i JOIN pg_class c "
                           "ON c.oid = i.inhrelid WHERE i.inhparent = $1::regclass "
                           "ORDER BY 1", f"public.{tabella}")
    return [r["relname"] for r in righe]


async def _imposta(url: str, codice: str, valore: int) -> None:
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, codice, valore, motivo) "
                   "VALUES ('CONSERVATORE', $1, to_jsonb($2::int), 'prova retention')",
              codice, valore)


async def test_creazione_anticipata_idempotente(db_url):
    """Un giro «fra 40 giorni» crea le partizioni mancanti fino all'anticipo
    (7 giorni, 3 mesi); il secondo giro non crea nulla. Le partizioni nuove
    ricevono indici e vincoli della madre."""
    adesso = datetime.now(UTC) + timedelta(days=40)
    esito = await esegui_manutenzione_partizioni(engine_manutenzione(), adesso, POLITICHE)
    assert esito is not None and esito.create["outbox_eventi"] >= 7
    assert esito.future["outbox_eventi"] == 7 and esito.future["eventi_cliente"] == 3
    attese = nome_partizione("outbox_eventi", "giorno",
                             inizio_periodo("giorno", adesso) + timedelta(days=7))
    assert attese in await _partizioni(db_url, "outbox_eventi")
    secondo = await esegui_manutenzione_partizioni(engine_manutenzione(), adesso, POLITICHE)
    assert sum(secondo.create.values()) == 0
    indici = await sql(db_url, "SELECT indexdef FROM pg_indexes WHERE schemaname = 'partizioni' "
                               "AND tablename = $1", attese)
    assert any("(origine, sequenza)" in r["indexdef"] and "pubblicato_il IS NULL" in r["indexdef"]
               for r in indici)
    # una riga del giorno futuro va nella sua partizione
    await sql(db_url, "INSERT INTO public.outbox_eventi (id, origine, gruppo, topic, payload, "
                      "creato_il) VALUES ($1, 'gateway', 0, 't', '{}', $2)",
              uuid.uuid4(), adesso + timedelta(days=7))
    dove = await sql(db_url, "SELECT tableoid::regclass::text AS t FROM public.outbox_eventi")
    assert dove[0]["t"] == f"partizioni.{attese}"


async def test_outbox_partizione_pubblicata_staccata_quella_in_attesa_resta(db_url):
    """Retention dell'outbox (predefinito 3 giorni): la partizione di oggi, fra
    5 giorni, si stacca se tutti i suoi eventi sono pubblicati; con un evento
    in attesa resta e la metrica delle bloccate lo segnala."""
    metriche_db.azzera()
    oggi = nome_partizione("outbox_eventi", "giorno", inizio_periodo("giorno", datetime.now(UTC)))
    await sql(db_url, "INSERT INTO public.outbox_eventi (id, origine, gruppo, topic, payload, "
                      "pubblicato_il) VALUES ($1, 'gateway', 0, 't', '{}', NULL)", uuid.uuid4())
    fra_5 = datetime.now(UTC) + timedelta(days=5)
    esito = await esegui_manutenzione_partizioni(
        engine_manutenzione(), fra_5, [_POL["outbox_eventi"]])
    assert esito.bloccate["outbox_eventi"] == 1
    assert oggi in await _partizioni(db_url, "outbox_eventi")
    assert metriche_db.valore("verumdoc_partizioni_bloccate", tabella="outbox_eventi") == 1
    await sql(db_url, "UPDATE public.outbox_eventi SET pubblicato_il = now()")
    esito = await esegui_manutenzione_partizioni(
        engine_manutenzione(), fra_5, [_POL["outbox_eventi"]])
    assert esito.staccate["outbox_eventi"] == 1 and esito.bloccate["outbox_eventi"] == 0
    assert oggi not in await _partizioni(db_url, "outbox_eventi")
    assert (await sql(db_url, "SELECT to_regclass($1) AS r", f"partizioni.{oggi}"))[0]["r"] is None
    # un giorno di retention in più (impostazione) = la stessa partizione resterebbe
    await _imposta(db_url, RETENZIONE_OUTBOX_GIORNI, 10)
    esito = await esegui_manutenzione_partizioni(
        engine_manutenzione(), fra_5, [_POL["outbox_eventi"]])
    assert esito.staccate["outbox_eventi"] == 0


async def test_eventi_e_consegne_staccati_insieme_cursore_senza_salti(db_url):
    """VD-C-103: retention impostabile (qui 30 giorni). Il mese degli eventi
    vecchi si stacca con le sue consegne; un client che riparte dal cursore
    riceve gli eventi rimasti in ordine e senza buchi fra quelli presenti; una
    consegna ancora IN_CODA blocca il distacco di consegne ed eventi."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    sotto = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.webhook_sottoscrizioni (id, tenant_id, url, tipi, "
                      "segreto_cifrato, segreto_suffisso) VALUES ($1, $2, 'https://x.example', "
                      "'[\"versamento.ricevuto\"]', 'c', 'abcd')", sotto, tenant)
    adesso = datetime.now(UTC)
    mese = inizio_periodo("mese", adesso)
    successivo = periodo_successivo("mese", mese)
    # 3 eventi in questo mese (posizioni 1..3), 2 nel prossimo (4..5)
    for pos, quando in [(1, mese), (2, mese), (3, mese), (4, successivo), (5, successivo)]:
        riga = await sql(db_url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, "
                                 "risorsa_tipo, risorsa_id, creato_il, posizione) VALUES ($1, "
                                 "'versamento.ricevuto', 'versamento', $2, $3, $4) "
                                 "RETURNING id, creato_il", tenant, f"r{pos}",
                         quando + timedelta(hours=1), pos)
        await sql(db_url, "INSERT INTO public.webhook_consegne (tenant_id, sottoscrizione_id, "
                          "evento_id, evento_creato_il, stato) VALUES ($1, $2, $3, $4, $5)",
                  tenant, sotto, riga[0]["id"], riga[0]["creato_il"],
                  "IN_CODA" if pos == 1 else "CONSEGNATA")
    await _imposta(db_url, RETENZIONE_EVENTI_CLIENTE_GIORNI, 30)
    giorno_dopo_retention = successivo + timedelta(days=31)
    politiche = [_POL["webhook_consegne"], _POL["eventi_cliente"]]
    esito = await esegui_manutenzione_partizioni(
        engine_manutenzione(), giorno_dopo_retention, politiche)
    assert esito.bloccate == {"webhook_consegne": 1, "eventi_cliente": 1}
    await sql(db_url, "UPDATE public.webhook_consegne SET stato = 'FALLITA'")
    esito = await esegui_manutenzione_partizioni(
        engine_manutenzione(), giorno_dopo_retention, politiche)
    assert esito.staccate == {"webhook_consegne": 1, "eventi_cliente": 1}
    nome = nome_partizione("eventi_cliente", "mese", mese)
    assert nome not in await _partizioni(db_url, "eventi_cliente")

    pagina = await sql(db_url, "SELECT posizione FROM public.eventi_cliente "
                               "WHERE tenant_id = $1 AND posizione > 0 ORDER BY posizione",
                       tenant)
    assert [r["posizione"] for r in pagina] == [4, 5]
    consegne = await sql(db_url, "SELECT count(*) AS n FROM public.webhook_consegne")
    assert consegne[0]["n"] == 2


async def test_retention_non_valida_usa_il_predefinito(db_url):
    """Un valore fuori dai limiti nel registro non si indovina: vale il predefinito."""
    from libs.shared.db.partizioni import retenzione_giorni

    await _imposta(db_url, RETENZIONE_EVENTI_CLIENTE_GIORNI, 1)  # minimo 7
    async with engine_manutenzione().connect() as conn:
        assert await retenzione_giorni(conn, RETENZIONE_EVENTI_CLIENTE_GIORNI) == 90
        assert await retenzione_giorni(conn, RETENZIONE_OUTBOX_GIORNI) == 3


async def test_manutenzione_una_replica_alla_volta(db_url):
    """Con il lease tenuto da un'altra replica il giro non fa nulla (R04 correzioni:
    lease in tabella invece del lock consultivo di sessione, rev4 rilievo 6)."""
    from libs.shared.db.partizioni import LEASE

    await sql(db_url, "INSERT INTO public.manutenzione_lease (nome, detentore, scade_il) "
                      "VALUES ($1, 'altra-replica', now() + interval '10 minutes')", LEASE)
    assert await esegui_manutenzione_partizioni(engine_manutenzione()) is None
    # lease scaduto (replica morta): si subentra; alla fine il lease è libero
    await sql(db_url, "UPDATE public.manutenzione_lease SET scade_il = now() - interval '1 s'")
    assert await esegui_manutenzione_partizioni(engine_manutenzione()) is not None
    [riga] = await sql(db_url, "SELECT detentore, scade_il <= now() AS libero "
                               "FROM public.manutenzione_lease")
    assert riga["detentore"] != "altra-replica" and riga["libero"] is True


# ── Audit append-only partizionato ─────────────────────────────────────────

async def _audit(url: str, quando: datetime | None = None) -> None:
    await sql(url, "INSERT INTO public.audit_global (evento, created_at) VALUES ('prova', $1)",
              quando or datetime.now(UTC))


@pytest.mark.parametrize("istruzione", [
    "UPDATE {t} SET evento = 'manomesso'",
    "DELETE FROM {t}",
    "TRUNCATE {t}",
])
async def test_audit_vietato_modificare_madre_e_partizione(db_url, istruzione):
    """REGOLE §15.4: né dalla madre né raggiungendo direttamente la partizione
    (anche il proprietario: trigger, non solo RLS)."""
    await _audit(db_url)
    partizione = "partizioni." + nome_partizione("audit_global", "mese",
                                                 inizio_periodo("mese", datetime.now(UTC)))
    for tabella in ("public.audit_global", partizione):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError,
                           match="append-only"):
            await sql(db_url, istruzione.format(t=tabella))
    assert (await sql(db_url, "SELECT count(*) AS n FROM public.audit_global "
                              "WHERE evento = 'prova'"))[0]["n"] == 1


async def test_audit_partizione_nuova_protetta_come_le_altre(db_url):
    """La manutenzione crea le partizioni future dell'audit con RLS, politiche e
    trigger: le difese non dipendono da chi ha creato la partizione."""
    adesso = datetime.now(UTC) + timedelta(days=200)
    await esegui_manutenzione_partizioni(engine_manutenzione(), adesso, [_POL["audit_global"]])
    nome = nome_partizione("audit_global", "mese", inizio_periodo("mese", adesso))
    prot = await sql(db_url, "SELECT c.relrowsecurity, "
                             "(SELECT array_agg(polname ORDER BY polname) FROM pg_policy "
                             " WHERE polrelid = c.oid) AS politiche, "
                             "(SELECT array_agg(tgname ORDER BY tgname) FROM pg_trigger "
                             " WHERE tgrelid = c.oid) AS trigger "
                             "FROM pg_class c WHERE c.oid = $1::regclass", f"partizioni.{nome}")
    assert prot[0]["relrowsecurity"] is True
    assert prot[0]["politiche"] == ["audit_global_insert_only", "audit_global_select_all"]
    assert set(prot[0]["trigger"]) == {"trg_audit_global_append_only",
                                       "trg_audit_global_no_truncate"}
    await _audit(db_url, adesso)
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await sql(db_url, f"DELETE FROM partizioni.{nome}")


async def test_audit_ruolo_applicativo_rls_solo_insert_e_select(db_url):
    """Un ruolo senza privilegi di proprietario, anche con GRANT UPDATE/DELETE:
    INSERT e SELECT sì; UPDATE e DELETE non toccano nulla (RLS: nessuna
    politica le ammette, il trigger fa da seconda difesa per il proprietario);
    TRUNCATE rifiutato dal trigger."""
    ruolo = f"vd_app_{uuid.uuid4().hex[:8]}"
    await sql(db_url, f"CREATE ROLE {ruolo} LOGIN")
    try:
        await sql(db_url, f"GRANT USAGE ON SCHEMA public TO {ruolo}")
        await sql(db_url, f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON public.audit_global "
                          f"TO {ruolo}")
        conn = await asyncpg.connect(_asyncpg_dsn(db_url), user=ruolo)
        try:
            await conn.execute("INSERT INTO public.audit_global (evento) VALUES ('app')")
            assert await conn.fetchval("SELECT count(*) FROM public.audit_global") == 1
            assert await conn.execute("UPDATE public.audit_global SET evento = 'x'") == "UPDATE 0"
            assert await conn.execute("DELETE FROM public.audit_global") == "DELETE 0"
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("TRUNCATE public.audit_global")
            assert await conn.fetchval("SELECT evento FROM public.audit_global") == "app"
            # la partizione non è raggiungibile direttamente (nessun permesso sullo schema)
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("SELECT 1 FROM partizioni." + nome_partizione(
                    "audit_global", "mese", inizio_periodo("mese", datetime.now(UTC))))
        finally:
            await conn.close()
    finally:
        await sql(db_url, f"DROP OWNED BY {ruolo}")
        await sql(db_url, f"DROP ROLE {ruolo}")


async def test_log_audit_orm_scrive_nella_partizione(db_url):
    """Lo scrittore esistente (ORM, PK composta) funziona sulla tabella partizionata."""
    from libs.shared.security.audit import log_audit

    await log_audit("prova.orm", tenant_id=str(uuid.uuid4()), dettagli={"a": 1})
    righe = await sql(db_url, "SELECT tableoid::regclass::text AS t FROM public.audit_global "
                              "WHERE evento = 'prova.orm'")
    assert len(righe) == 1 and righe[0]["t"].startswith("partizioni.audit_global_p")
