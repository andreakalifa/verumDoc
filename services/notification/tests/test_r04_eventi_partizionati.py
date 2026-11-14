# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 dati — eventi per i clienti su tabelle partizionate per mese (VD-C-103) e
evento ``catena.checkpoint`` per tutti gli enti (contratto K1), su PostgreSQL
reale (migrazione 20261023_028).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.application.eventi_consumer import elabora_evento_cliente
from app.config import settings
from app.domain.eventi import EventoCliente, EventoNonValido, proietta
from app.infrastructure.repo_eventi import elenca_eventi, pubblica_eventi, registra_evento
from app.infrastructure.repo_webhook import preleva_consegne

from libs.shared.db import engine_manutenzione
from libs.shared.db.partizioni import POLITICHE, esegui_manutenzione_partizioni
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401 — fixture
from tests.test_b3_integrazione import crea_sottoscrizione, crea_tenant

pytestmark = [pytest.mark.integration, richiede_pg]


def _evento(tenant, risorsa: str) -> EventoCliente:
    return EventoCliente(str(tenant), "versamento.ricevuto", "versamento", risorsa,
                         {"pdv_id": risorsa})


async def test_riconsegna_deduplicata_anche_con_l_originale_in_un_mese_precedente(db_url):
    """Il vincolo unico globale non esiste più (partizioni): la deduplica la fa
    il consumer su TUTTE le partizioni presenti, anche a cavallo di mese."""
    tenant = await crea_tenant(db_url)
    risorsa = str(uuid.uuid4())
    mese_prima = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0,
                                           microsecond=0) - timedelta(minutes=1)
    # partizione del mese scorso (la manutenzione «come se fosse» il mese scorso)
    await esegui_manutenzione_partizioni(
        engine_manutenzione(), mese_prima, [p for p in POLITICHE if p.tabella == "eventi_cliente"])
    await sql(db_url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, "
                      "risorsa_id, creato_il) VALUES ($1, 'versamento.ricevuto', 'versamento', "
                      "$2, $3)", tenant, risorsa, mese_prima)
    esito = await registra_evento(_evento(tenant, risorsa), origine="pdv.received")
    assert esito.evento_id is None
    righe = await sql(db_url, "SELECT count(*) AS n FROM public.eventi_cliente")
    assert righe[0]["n"] == 1


async def test_registrazioni_concorrenti_della_stessa_chiave_una_sola_riga(db_url):
    """Due consumer (repliche, riconsegna Kafka) registrano lo stesso evento
    nello stesso istante: il lock consultivo sulla chiave ne lascia passare uno."""
    tenant = await crea_tenant(db_url)
    sid = await crea_sottoscrizione(db_url, tenant, ["versamento.ricevuto"])
    risorsa = str(uuid.uuid4())
    esiti = await asyncio.gather(*(registra_evento(_evento(tenant, risorsa), origine="x")
                                   for _ in range(6)))
    assert sum(1 for e in esiti if e.evento_id is not None) == 1
    assert sum(e.consegne for e in esiti) == 1
    consegne = await sql(db_url, "SELECT c.sottoscrizione_id::text AS sid, "
                                 "c.evento_creato_il = e.creato_il AS stessa_partizione "
                                 "FROM public.webhook_consegne c JOIN public.eventi_cliente e "
                                 "ON e.id = c.evento_id")
    assert [(c["sid"], c["stessa_partizione"]) for c in consegne] == [(sid, True)]


async def test_consegna_prelevata_con_l_evento_della_sua_partizione(db_url):
    tenant = await crea_tenant(db_url)
    await crea_sottoscrizione(db_url, tenant, ["versamento.ricevuto"])
    risorsa = str(uuid.uuid4())
    await registra_evento(_evento(tenant, risorsa), origine="x")
    prese = await preleva_consegne(adesso=datetime.now(UTC), lotto=10, lease_secondi=60)
    assert len(prese) == 1 and prese[0].evento["risorsa"]["id"] == risorsa


async def test_catena_checkpoint_un_solo_evento_globale_letto_da_tutti(db_url):
    """K1 + R04 correzioni (rev4 rilievo 10): il checkpoint arriva una volta da
    Kafka e diventa UN evento globale (``tenant_id`` NULL), non una copia per
    ente; ogni ente lo legge da GET /eventi nell'ordine delle posizioni, senza
    salti; consegna webhook solo a chi l'ha sottoscritto (e solo enti ATTIVI);
    la riconsegna non duplica nulla."""
    a = await crea_tenant(db_url)
    b = await crea_tenant(db_url)
    sospeso = await crea_tenant(db_url, stato="SOSPESO")
    await crea_sottoscrizione(db_url, a, ["catena.checkpoint"])
    await crea_sottoscrizione(db_url, b, ["versamento.ricevuto"])
    await crea_sottoscrizione(db_url, sospeso, ["catena.checkpoint"])
    prima = str(uuid.uuid4())
    await registra_evento(_evento(b, prima), origine="x")
    messaggio = {"dimensione": 42, "radice": "q83vEjRWeJA=", "emesso_il": "2026-10-23T10:00:00Z",
                 "testo": "verumdoc.it/registro/v1\n42\nq83vEjRWeJA=\n\n— verumdoc-registro AAAA\n"}
    consegne = await elabora_evento_cliente("catena.checkpoint", messaggio,
                                            mappa_topic=settings.eventi_topic)
    assert consegne == 1
    assert await elabora_evento_cliente("catena.checkpoint", messaggio,
                                        mappa_topic=settings.eventi_topic) == 0
    dopo = str(uuid.uuid4())
    await registra_evento(_evento(b, dopo), origine="x")
    righe = await sql(db_url, "SELECT tenant_id, risorsa_tipo, risorsa_id, dati->>'radice' AS r "
                              "FROM public.eventi_cliente WHERE tipo = 'catena.checkpoint'")
    assert [(r["tenant_id"], r["risorsa_tipo"], r["risorsa_id"], r["r"]) for r in righe] == [
        (None, "checkpoint", "42", "q83vEjRWeJA=")]
    destinatari = await sql(db_url, "SELECT c.tenant_id FROM public.webhook_consegne c JOIN "
                                    "public.eventi_cliente e ON e.id = c.evento_id "
                                    "WHERE e.tipo = 'catena.checkpoint'")
    assert [r["tenant_id"] for r in destinatari] == [a]
    await pubblica_eventi()
    eventi, _ = await elenca_eventi(str(b), dopo=0, limite=10, tipi=["catena.checkpoint"])
    assert [e.dati["dimensione"] for e in eventi] == [42]
    for ente in (a, b, sospeso):   # visibile a tutti (dati pubblici del registro)
        eventi, _ = await elenca_eventi(str(ente), dopo=0, limite=10, tipi=[])
        assert "catena.checkpoint" in [e.tipo for e in eventi]
    # ordine e cursore senza salti per b: suo, globale, suo
    tutti, altri = await elenca_eventi(str(b), dopo=0, limite=10, tipi=[])
    assert [e.tipo for e in tutti] == ["versamento.ricevuto", "catena.checkpoint",
                                       "versamento.ricevuto"] and not altri
    assert [e.posizione for e in tutti] == sorted(e.posizione for e in tutti)
    primo, altri = await elenca_eventi(str(b), dopo=0, limite=1, tipi=[])
    assert altri and [e.risorsa_id for e in primo] == [prima]
    resto, _ = await elenca_eventi(str(b), dopo=primo[0].posizione, limite=10, tipi=[])
    assert [e.risorsa_id for e in resto] == ["42", dopo]
    # gli eventi di b non si vedono da a
    eventi_a, _ = await elenca_eventi(str(a), dopo=0, limite=10, tipi=[])
    assert [e.tipo for e in eventi_a] == ["catena.checkpoint"]


def test_proiezione_checkpoint_e_conservato_con_catena() -> None:
    ev = proietta("catena.checkpoint", {"dimensione": "7", "radice": "x", "testo": "t",
                                        "tenant_id": "ignorato", "segreto": "mai"})
    assert ev.tutti_gli_enti and ev.risorsa_id == "7" and "segreto" not in ev.dati
    with pytest.raises(EventoNonValido):
        proietta("catena.checkpoint", {"radice": "x"})
    pdv, aip = str(uuid.uuid4()), str(uuid.uuid4())
    base = {"tenant_id": str(uuid.uuid4()), "pdv_id": pdv, "aip_id": aip}
    vecchio = proietta("versamento.conservato", base)
    assert vecchio.dati["fase"] == "PDA_SU_STORAGE"
    nuovo = proietta("versamento.conservato", {**base, "pda": "urn:pda:1", "foglia_indice": 9,
                                               "checkpoint_dimensione": 10})
    assert nuovo.dati["fase"] == "CONSERVATO"
    assert (nuovo.dati["pda"], nuovo.dati["foglia_indice"],
            nuovo.dati["checkpoint_dimensione"]) == ("urn:pda:1", 9, 10)


async def test_riprova_consegna_oltre_la_retention_rifiutata_wh_012(db_url):
    """R04 correzioni (rev4 rilievo 11): una consegna FALLITA il cui evento è più
    vecchio della retention non torna in coda (la sua partizione può essere in
    distacco); dentro la retention sì."""
    from app.infrastructure.repo_webhook import RepositoryWebhook

    tenant = await crea_tenant(db_url)
    sid = await crea_sottoscrizione(db_url, tenant, ["versamento.ricevuto"])
    await registra_evento(_evento(tenant, str(uuid.uuid4())), origine="x")
    vecchio = datetime.now(UTC) - timedelta(days=95)            # retention predefinita: 90
    await esegui_manutenzione_partizioni(
        engine_manutenzione(), vecchio, [p for p in POLITICHE if p.tabella in ("eventi_cliente",
                                                                       "webhook_consegne")])
    ev = await sql(db_url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, "
                           "risorsa_id, creato_il) VALUES ($1, 'versamento.ricevuto', "
                           "'versamento', 'vecchia', $2) RETURNING id, creato_il", tenant, vecchio)
    vecchia = await sql(db_url, "INSERT INTO public.webhook_consegne (tenant_id, "
                                "sottoscrizione_id, evento_id, evento_creato_il, stato) VALUES "
                                "($1, $2, $3, $4, 'FALLITA') RETURNING id", tenant, uuid.UUID(sid),
                        ev[0]["id"], ev[0]["creato_il"])
    await sql(db_url, "UPDATE public.webhook_consegne SET stato = 'FALLITA'")
    recente = (await sql(db_url, "SELECT id FROM public.webhook_consegne WHERE id <> $1",
                         vecchia[0]["id"]))[0]["id"]
    repo = RepositoryWebhook()
    esito, dati = await repo.riprova(str(tenant), sid, vecchia[0]["id"])
    assert esito == "oltre_retention" and dati["evento_creato_il"]
    assert (await repo.riprova(str(tenant), sid, recente))[0] == "ok"
    stati = {r["id"]: r["stato"] for r in await sql(db_url, "SELECT id, stato FROM "
                                                          "public.webhook_consegne")}
    assert stati == {vecchia[0]["id"]: "FALLITA", recente: "IN_CODA"}
