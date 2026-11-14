# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Correzioni della revisione indipendente R04 (rev4) sulla manutenzione delle partizioni:

  - rilievo 6 (PgBouncer in modalità transaction): la manutenzione non lascia
    stato di SESSIONE. Tutte le istruzioni eseguite si osservano: nessun lock
    consultivo di sessione, nessun ``SET`` che non sia ``SET LOCAL``; sulla
    connessione restituita al pool ``lock_timeout`` è quello predefinito e nessun
    lock consultivo resta tenuto;
  - rilievo 11: una consegna rimessa in coda fra il controllo e il distacco
    della sua partizione non sparisce: dopo il distacco il controllo si ripete
    e la partizione si ri-aggancia (log, metrica, audit); la «riprova» di una
    consegna oltre la retention è rifiutata (WH-012, provato in notification).
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

from libs.shared.db import engine_manutenzione, partizioni  # R05: ruolo proprietario
from libs.shared.db import metriche as metriche_db
from libs.shared.db.partizioni import (
    POLITICHE,
    RETENZIONE_EVENTI_CLIENTE_GIORNI,
    esegui_manutenzione_partizioni,
    inizio_periodo,
    nome_partizione,
    partizioni_di,
)
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

_POL = {p.tabella: p for p in POLITICHE}
_STATO_DI_SESSIONE = re.compile(
    r"pg_(try_)?advisory_lock\(|^\s*(SET|RESET)\s+(?!LOCAL\b)", re.IGNORECASE)


async def test_manutenzione_senza_stato_di_sessione(db_url):
    """Pool di DUE connessioni server (la manutenzione ne usa una in AUTOCOMMIT e
    una per le transazioni): nessuna istruzione con stato di sessione e, dopo,
    nulla di residuo su nessuna delle due (come per il client di PgBouncer che
    le riceve dopo)."""
    engine = create_async_engine(db_url, pool_size=2, max_overflow=0)
    eseguite: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _spia(conn, cursor, istruzione, parametri, contesto, molti):  # noqa: ANN001
        eseguite.append(istruzione)

    try:
        adesso = datetime.now(UTC) + timedelta(days=45)
        esito = await esegui_manutenzione_partizioni(engine, adesso, POLITICHE)
        assert esito is not None and sum(esito.create.values()) > 0
        assert any("SET LOCAL lock_timeout" in s for s in eseguite)
        sessione = [s for s in eseguite if _STATO_DI_SESSIONE.search(s)]
        assert sessione == [], sessione
        from sqlalchemy import text

        async with engine.connect() as uno, engine.connect() as due:
            for conn in (uno, due):
                assert (await conn.execute(text("SHOW lock_timeout"))).scalar_one() == "0"
                assert (await conn.execute(text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = pg_backend_pid()"))).scalar_one() == 0
    finally:
        await engine.dispose()


async def test_consegna_riaccodata_durante_il_distacco_partizione_riagganciata(db_url,
                                                                               monkeypatch):
    """La corsa del rilievo 11: controllo OK (consegna FALLITA), poi la «riprova»
    la rimette IN_CODA prima del DETACH. Prima: partizione eliminata con la
    consegna. Ora: ricontrollo dopo il distacco → ri-aggancio, consegna salva."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    sotto = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.webhook_sottoscrizioni (id, tenant_id, url, tipi, "
                      "segreto_cifrato, segreto_suffisso) VALUES ($1, $2, 'https://x.example', "
                      "'[\"versamento.ricevuto\"]', 'c', 'abcd')", sotto, tenant)
    mese = inizio_periodo("mese", datetime.now(UTC))
    riga = await sql(db_url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, "
                             "risorsa_id, creato_il, posizione) VALUES ($1, "
                             "'versamento.ricevuto', 'versamento', 'r1', $2, 1) "
                             "RETURNING id, creato_il", tenant, mese + timedelta(hours=1))
    consegna = await sql(db_url, "INSERT INTO public.webhook_consegne (tenant_id, "
                                 "sottoscrizione_id, evento_id, evento_creato_il, stato) VALUES "
                                 "($1, $2, $3, $4, 'FALLITA') RETURNING id", tenant, sotto,
                         riga[0]["id"], riga[0]["creato_il"])
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, codice, valore, motivo) "
                      "VALUES ('CONSERVATORE', $1, to_jsonb(30), 'prova')",
              RETENZIONE_EVENTI_CLIENTE_GIORNI)

    originale = partizioni.stacca_partizione
    nome = nome_partizione("webhook_consegne", "mese", mese)

    class ConnSpia:
        """Proxy della connessione: al DETACH, prima esegue la «riprova» concorrente."""

        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, nome):
            return getattr(self._conn, nome)

        async def execute(self, istruzione, *argomenti, **opzioni):
            # R17: solo al distacco della partizione della prova (non di un'altra scaduta)
            if "DETACH PARTITION" in str(istruzione) and nome in str(istruzione):
                await sql(db_url, "UPDATE public.webhook_consegne SET stato = 'IN_CODA' "
                                  "WHERE id = $1", consegna[0]["id"])
            return await self._conn.execute(istruzione, *argomenti, **opzioni)

    async def stacca_con_corsa(conn, politica, partizione, *, engine=None):
        return await originale(ConnSpia(conn), politica, partizione, engine=engine or conn.engine)

    monkeypatch.setattr(partizioni, "stacca_partizione", stacca_con_corsa)
    metriche_db.azzera()
    oltre = mese + timedelta(days=70)
    esito = await esegui_manutenzione_partizioni(engine_manutenzione(), oltre,
                                                 [_POL["webhook_consegne"]])
    # R17: si guarda SOLO la partizione della prova. Il template può avere altre
    # partizioni scadute (vuote: create da una migrazione fatta in un mese precedente,
    # o dalla preparazione della corsa) che la manutenzione stacca legittimamente:
    # prima il test contava tutte le staccate della tabella e falliva dal mese dopo
    # quello in cui il template era stato migrato
    assert esito.bloccate["webhook_consegne"] == 1
    staccate = {r["p"] for r in await sql(
        db_url, "SELECT dettagli ->> 'partizione' AS p FROM public.audit_global "
                "WHERE evento = 'db.partizione_staccata'")}
    assert nome not in staccate
    async with engine_manutenzione().connect() as conn:
        assert nome in [p.nome for p in await partizioni_di(conn, "webhook_consegne")]
    rimasta = await sql(db_url, "SELECT stato FROM public.webhook_consegne WHERE id = $1",
                        consegna[0]["id"])
    assert rimasta and rimasta[0]["stato"] == "IN_CODA"
    assert metriche_db.valore("verumdoc_partizioni_riagganciate",
                              tabella="webhook_consegne") == 1
    traccia = await sql(db_url, "SELECT dettagli ->> 'partizione' AS p FROM public.audit_global "
                                "WHERE evento = 'db.partizione_riagganciata'")
    assert [t["p"] for t in traccia] == [nome]


async def test_distacco_lascia_traccia_nell_audit(db_url):
    """Ogni distacco con eliminazione lascia una voce di audit con il ruolo del database."""
    import json

    oggi = nome_partizione("outbox_eventi", "giorno", inizio_periodo("giorno", datetime.now(UTC)))
    esito = await esegui_manutenzione_partizioni(engine_manutenzione(), datetime.now(UTC)
                                                 + timedelta(days=5), [_POL["outbox_eventi"]])
    assert esito.staccate["outbox_eventi"] >= 1
    tracce = [json.loads(r["dettagli"]) for r in await sql(
        db_url, "SELECT dettagli FROM public.audit_global WHERE evento = 'db.partizione_staccata'")]
    assert any(t["partizione"] == oggi and t["ruolo_db"] for t in tracce)


async def test_tabelle_protette_mai_staccate_anche_con_una_politica_sbagliata(db_url):
    """Una politica che chiedesse la retention dell'audit non stacca nulla."""
    sbagliata = partizioni.Politica("audit_global", "mese", anticipo=1,
                                    codice_retenzione=partizioni.RETENZIONE_OUTBOX_GIORNI,
                                    protezione_audit=True)
    esito = await esegui_manutenzione_partizioni(engine_manutenzione(), datetime.now(UTC)
                                                 + timedelta(days=800), [sbagliata])
    assert esito.staccate["audit_global"] == 0


async def test_lease_perso_durante_il_giro_ferma_la_manutenzione(db_url, monkeypatch):
    """Nota del revisore sul lease (secondo giro): se il lease scade durante il
    giro (es. un DETACH CONCURRENTLY in attesa oltre la durata) e un'altra
    replica lo prende, il rinnovo lo rileva e il giro si FERMA con
    ``LeasePerso`` invece di proseguire accanto all'altra replica."""
    originale = partizioni.mantieni
    eseguiti: list[str] = []

    async def mantieni_con_furto(engine, conn, adesso, politiche, rinnova):
        await sql(db_url, "UPDATE public.manutenzione_lease SET detentore = 'altra-replica', "
                          "scade_il = now() + interval '1 hour' WHERE nome = 'partizioni'")
        eseguiti.append("prima")
        return await originale(engine, conn, adesso, politiche, rinnova)

    monkeypatch.setattr(partizioni, "mantieni", mantieni_con_furto)
    with pytest.raises(partizioni.LeasePerso):
        await esegui_manutenzione_partizioni(engine_manutenzione(), datetime.now(UTC),
                                             [_POL["outbox_eventi"]], detentore="questa")
    assert eseguiti == ["prima"]
    # il lease resta all'altra replica (il rilascio tocca solo il proprio)
    riga = (await sql(db_url, "SELECT detentore FROM public.manutenzione_lease "
                              "WHERE nome = 'partizioni'"))[0]
    assert riga["detentore"] == "altra-replica"
