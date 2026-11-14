# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
CodaUscitaSQL su PostgreSQL vero, con lo schema delle migrazioni — VD-C-024,
REGOLE §23.3.

Si esegue con ``VD_TEST_DATABASE_URL`` (database migrato a head usato come
template, vedi tests/infra_reale.py): ogni test ha una copia nuova.
Copre: cliente obbligatorio (FK e NOT NULL reali), deduplica ATOMICA
sull'indice ``uq_notifications_dedup`` anche con inserimenti concorrenti,
prelievo con due connessioni (SKIP LOCKED), stati, recupero dei bloccati,
risoluzione del cliente dall'oggetto (PdV/PdA) e destinatari dalla tabella clienti.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.services.coda import IN_CODA, INVIATA, NON_PARTITA, CodaUscitaSQL, NuovoMessaggio
from app.services.destinatari import destinatari
from app.services.elaborazione import ScartoEvento, accoda_evento, leggi_tenant_oggetto

from tests.infra_reale import (
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]

ADESSO = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _m(tenant, dest: str = "a@cliente.it", oggetto_id: str = "aip-1",
       tipo: str = "aip.conserved") -> NuovoMessaggio:
    return NuovoMessaggio(
        tenant_id=str(tenant), tipo=tipo, oggetto_id=oggetto_id,
        destinatario=dest, oggetto="Oggetto", testo="Testo",
    )


async def _righe(url):
    return await sql(url, "SELECT stato, tentativi, errore, tenant_id::text AS t, payload_json "
                          "FROM public.notifications ORDER BY created_at, id")


async def test_sql_accoda_deduplica_e_non_partita(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    coda = CodaUscitaSQL()
    assert await coda.accoda(_m(tenant), ADESSO) is True
    assert await coda.accoda(_m(tenant), ADESSO) is False
    assert await coda.registra_non_partita(_m(tenant, "", "pdv-1"), "destinatario non configurato",
                                           ADESSO)
    assert await coda.registra_non_partita(_m(tenant, "", "pdv-1"), "ripetuto", ADESSO) is False
    righe = {r["stato"]: r for r in await _righe(db_url)}
    assert set(righe) == {IN_CODA, NON_PARTITA}
    assert righe[IN_CODA]["t"] == str(tenant)
    assert righe[NON_PARTITA]["errore"] == "destinatario non configurato"


async def test_sql_deduplica_atomica_con_inserimenti_concorrenti(db_url):
    """Repliche che ricevono lo stesso evento insieme: una sola riga (indice unico)."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    coda = CodaUscitaSQL()
    esiti = await asyncio.gather(*(coda.accoda(_m(tenant), ADESSO) for _ in range(8)))
    assert sorted(esiti) == [False] * 7 + [True]
    assert len(await _righe(db_url)) == 1


async def test_sql_chiave_di_deduplica(db_url):
    """Stesso oggetto ma tipo, destinatario o cliente diversi: righe distinte."""
    t1, _ = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    coda = CodaUscitaSQL()
    assert await coda.accoda(_m(t1), ADESSO)
    assert await coda.accoda(_m(t1, tipo="pdv.invalid"), ADESSO)
    assert await coda.accoda(_m(t1, dest="b@cliente.it"), ADESSO)
    assert await coda.accoda(_m(t2), ADESSO)
    assert len(await _righe(db_url)) == 4
    indice = await sql(db_url, "SELECT indexdef FROM pg_indexes "
                               "WHERE indexname = 'uq_notifications_dedup'")
    assert "UNIQUE" in indice[0]["indexdef"]


async def test_sql_cliente_inesistente_rifiutato_dal_db(db_url):
    """La FK impedisce righe di clienti inesistenti: il servizio deve scartare prima."""
    coda = CodaUscitaSQL()
    with pytest.raises(Exception, match="fk_notifications_tenant|foreign key"):
        await coda.accoda(_m(uuid.uuid4()), ADESSO)
    assert await _righe(db_url) == []


async def test_sql_prelievo_concorrente_senza_doppioni(db_url):
    """Due prelievi concorrenti (due connessioni) non prendono lo stesso messaggio."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    coda = CodaUscitaSQL()
    for i in range(10):
        await coda.accoda(_m(tenant, f"u{i}@cliente.it", f"aip-{i}"), ADESSO)
    a, b = await asyncio.gather(coda.preleva(ADESSO, 6), coda.preleva(ADESSO, 6))
    ids = [m.id for m in a] + [m.id for m in b]
    assert len(ids) == len(set(ids)) == 10
    assert {m.tenant_id for m in a + b} == {str(tenant)}


async def test_sql_ciclo_stati_e_recupero(db_url):
    # Orologio reale: il trigger updated_at della migrazione 010 scrive now()
    # (il recupero dei bloccati si basa su updated_at).
    ADESSO = datetime.now(UTC)  # noqa: N806
    tenant, _ = await crea_tenant_e_classe(db_url)
    coda = CodaUscitaSQL()
    await coda.accoda(_m(tenant, "a@cliente.it", "1"), ADESSO)
    await coda.accoda(_m(tenant, "b@cliente.it", "2"), ADESSO)
    await coda.accoda(_m(tenant, "c@cliente.it", "3"), ADESSO)
    presi = {m.payload["oggetto_id"]: m for m in await coda.preleva(ADESSO, 10)}
    m1, m2, m3 = presi["1"], presi["2"], presi["3"]
    await coda.segna_inviata(m1.id, ADESSO, "<id@x>")
    await coda.riprogramma(m2.id, 1, ADESSO + timedelta(minutes=5), "451", ADESSO)
    await coda.segna_non_partita(m3.id, 1, "550", ADESSO)
    assert await coda.preleva(ADESSO, 10) == []  # m2 non ancora scaduto
    ripreso = await coda.preleva(ADESSO + timedelta(minutes=6), 10)
    assert [m.id for m in ripreso] == [m2.id] and ripreso[0].tentativi == 1
    # ripreso resta IN_INVIO: dopo la soglia torna in coda
    assert await coda.recupera_bloccati(ADESSO + timedelta(minutes=30),
                                        ADESSO + timedelta(minutes=30)) == 1
    import json

    righe = {}
    for r in await _righe(db_url):
        payload = json.loads(r["payload_json"])
        righe[payload["oggetto_id"]] = (r, payload)
    assert righe["1"][0]["stato"] == INVIATA and righe["1"][1]["message_id"] == "<id@x>"
    assert righe["2"][0]["stato"] == IN_CODA
    assert righe["3"][0]["stato"] == NON_PARTITA and righe["3"][0]["errore"] == "550"


async def test_sql_cliente_risolto_dal_pdv_e_dal_pda(db_url):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(
        db_url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                      "VALUES ($1, 'x', 'ERRORE', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    await sql(
        db_url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                      "hash_sha256) VALUES ($1, $2, $3, $4, 'CONSERVATO', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:t:{aip}", "0" * 64)
    assert await leggi_tenant_oggetto(str(pdv), "") == str(tenant)
    assert await leggi_tenant_oggetto("", str(aip)) == str(tenant)
    assert await leggi_tenant_oggetto(str(uuid.uuid4()), "non-uuid") is None

    coda = CodaUscitaSQL()
    # Evento senza tenant_id: cliente dal PdA; il referente di prova è un segnaposto
    # (example.org) → riga NON_PARTITA del cliente giusto, con motivo
    await accoda_evento("aip.conserved", {"aip_id": str(aip), "urn": "u"}, coda, adesso=ADESSO)
    righe = await _righe(db_url)
    assert [(r["stato"], r["t"]) for r in righe] == [(NON_PARTITA, str(tenant))]

    with pytest.raises(ScartoEvento):
        await accoda_evento("aip.conserved", {"aip_id": str(uuid.uuid4())}, coda, adesso=ADESSO)
    assert len(await _righe(db_url)) == 1


async def test_sql_destinatari_da_tabella_clienti(db_url):
    """destinatari() con la query reale su public.tenants (email_referente, stato)."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "UPDATE public.tenants SET email_referente = 'referente@cliente.it' "
                      "WHERE id = $1", tenant)
    segnaposto, _ = await crea_tenant_e_classe(db_url)
    await sql(db_url, "UPDATE public.tenants SET email_referente = $2 WHERE id = $1",
              segnaposto, f"tenant-{segnaposto}@verumdoc.local")
    assert (await destinatari(str(tenant), "aip.conserved")).indirizzi == ["referente@cliente.it"]
    d = await destinatari(str(segnaposto), "aip.conserved")
    assert d.indirizzi == [] and "segnaposto" in d.motivo
    assert (await destinatari(str(uuid.uuid4()), "aip.conserved")).motivo == "cliente inesistente"
