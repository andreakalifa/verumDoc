# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Correzioni della revisione indipendente R04 (rev4) sulle code di scarto:

  - rilievo 4: due scarti dello stesso PdV (``pdv.received.dlq`` e
    ``pdv.validated.dlq``, o lo stesso messaggio due volte) → UNA sola
    ripartenza: nel piano uno solo è rielaborabile, in applicazione l'evento
    parte solo se il PdV passa davvero da ERRORE, e il vincolo
    ``scarti_ripartenze`` lo garantisce anche se il piano non lo impedisse;
  - rilievo 2: gli eventi BLOCCATI dell'outbox compaiono nelle code di scarto;
    «Rielabora» li ritenta (chiave sbloccata, tentativi azzerati), «Archivia»
    li scarta (mai pubblicati) e fa ripartire la chiave, con audit.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault("ENV", "test")

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401,E402
from tests.test_r04_scarti import (  # noqa: E402
    Messaggio,
    _client,
    _dlq_pipeline,
    _id_scarto,
    _pdv_in_errore,
    _registra,
    _token,
)

pytestmark = richiede_pg


async def _rielabora(ids: list[int]) -> tuple[dict, object]:
    async with _client() as c:
        piano = (await c.post("/api/dashboard/scarti/rielabora/anteprima", json={"ids": ids},
                              headers=_token())).json()
        r = await c.post("/api/dashboard/scarti/rielabora/applica", headers=_token(), json={
            "ids": ids, "impronta_piano": piano["impronta_piano"],
            "motivo": "prova delle correzioni R04"})
    return piano, r


# ── Rilievo 4 ───────────────────────────────────────────────────────

async def test_due_scarti_stesso_pdv_una_sola_ripartenza(db_url):
    """La prova del revisore, rovesciata: prima 2 eventi (received + validated)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv_in_errore(db_url, tenant, classe, giorni=2)
    await _registra([Messaggio("pdv.received.dlq", 0, 1,
                               _dlq_pipeline("pdv.received", pdv, tenant)),
                     Messaggio("pdv.validated.dlq", 0, 1,
                               _dlq_pipeline("pdv.validated", pdv, tenant))])
    ids = [await _id_scarto(db_url, "kafka:pdv.received.dlq:0:1"),
           await _id_scarto(db_url, "kafka:pdv.validated.dlq:0:1")]
    piano, r = await _rielabora(ids)
    assert piano["rielaborabili"] == 1 and piano["bloccati"] == 1
    bloccato = [e for e in piano["elementi"] if not e["rielaborabile"]][0]
    assert bloccato["scarto_id"] == ids[1] and f"scarto {ids[0]}" in bloccato["motivo_blocco"]
    assert r.status_code == 200, r.text
    eventi = await sql(db_url, "SELECT topic, chiave FROM public.outbox_eventi ORDER BY sequenza")
    assert [(e["topic"], e["chiave"]) for e in eventi] == [("pdv.received", str(pdv))]
    stato = (await sql(db_url, "SELECT stato::text AS s FROM public.pdv WHERE id = $1", pdv))[0]
    assert stato["s"] == "RICEVUTO"
    ripartenze = await sql(db_url, "SELECT scarto_id FROM public.scarti_ripartenze "
                                   "WHERE pdv_id = $1", pdv)
    assert [x["scarto_id"] for x in ripartenze] == [ids[0]]


async def test_stesso_messaggio_due_volte_una_ripartenza(db_url):
    """Consegna «almeno una volta»: lo stesso evento finito due volte in DLQ."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv_in_errore(db_url, tenant, classe, giorni=2)
    msg = _dlq_pipeline("pdv.validated", pdv, tenant)
    await _registra([Messaggio("pdv.validated.dlq", 0, 1, msg),
                     Messaggio("pdv.validated.dlq", 0, 2, msg)])
    ids = [await _id_scarto(db_url, f"kafka:pdv.validated.dlq:0:{i}") for i in (1, 2)]
    piano, r = await _rielabora(ids)
    assert piano["rielaborabili"] == 1 and r.status_code == 200, r.text
    eventi = await sql(db_url, "SELECT topic FROM public.outbox_eventi")
    assert [e["topic"] for e in eventi] == ["pdv.validated"]


async def test_vincolo_una_ripartenza_anche_senza_il_piano(db_url, ambito_del_conservatore):
    """Difesa in profondità: due esecuzioni dello stesso elemento (come due
    richieste che non si vedessero) → la seconda non accoda nulla."""
    from app.services import scarti

    from libs.shared.db import get_async_session

    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv_in_errore(db_url, tenant, classe, giorni=2)
    await _registra([Messaggio("pdv.received.dlq", 0, 1,
                               _dlq_pipeline("pdv.received", pdv, tenant))])
    sid = await _id_scarto(db_url, "kafka:pdv.received.dlq:0:1")
    async with get_async_session() as session:
        piano = await scarti.calcola_piano(session, ids=[sid], filtri=None,
                                           adesso=datetime.now(UTC), blocca=False)
    elemento = piano["elementi"][0]
    assert elemento.rielaborabile
    async with get_async_session() as session:
        assert await scarti._esegui(session, elemento, adesso=datetime.now(UTC)) is True
    # Il PdV torna in ERRORE con lo STESSO istante d'errore (episodio identico)
    await sql(db_url, "UPDATE public.pdv SET stato = 'ERRORE', data_aggiornamento = $2 "
                      "WHERE id = $1", pdv, elemento.interno["errore_il"])
    async with get_async_session() as session:
        assert await scarti._esegui(session, elemento, adesso=datetime.now(UTC)) is False
    assert len(await sql(db_url, "SELECT 1 FROM public.outbox_eventi")) == 1


# ── Rilievo 2: eventi bloccati dell'outbox nella console ────────────

async def _evento_bloccato(url, tenant, *, chiave="pdv-x", topic="topic-sbagliato",
                           successivi: int = 1) -> tuple[uuid.UUID, datetime]:
    """Un evento bloccato (come lo lascia il relay) e ``successivi`` eventi della
    stessa chiave in attesa dietro di lui."""
    evento, creato = uuid.uuid4(), datetime.now(UTC) - timedelta(minutes=30)
    await sql(url, "INSERT INTO public.outbox_eventi (id, origine, gruppo, tenant_id, topic, "
                   "chiave, payload, creato_il, tentativi, ultimo_errore) VALUES ($1, 'gateway', "
                   "(hashtext($2) & 63)::smallint, $3, $4, $2, '{}'::jsonb, $5, 10, "
                   "'UnknownTopicOrPartitionError')", evento, chiave, tenant, topic, creato)
    for i in range(successivi):
        await sql(url, "INSERT INTO public.outbox_eventi (origine, gruppo, tenant_id, topic, "
                       "chiave, payload) VALUES ('gateway', (hashtext($1) & 63)::smallint, $2, "
                       "'t', $1, CAST($3 AS jsonb))", chiave, tenant, json.dumps({"i": i}))
    await sql(url, "INSERT INTO public.outbox_chiavi_ferme (origine, chiave, gruppo, evento_id, "
                   "evento_creato_il, sequenza, topic, tentativi, ultimo_errore, "
                   "prossimo_tentativo_il, bloccato_il) SELECT 'gateway', $1, gruppo, id, "
                   "creato_il, sequenza, topic, 10, 'UnknownTopicOrPartitionError', now(), now() "
                   "FROM public.outbox_eventi WHERE id = $2", chiave, evento)
    return evento, creato


async def _rileva() -> int:
    from app.services.consumer_scarti import rileva_tabelle

    return await rileva_tabelle()


async def test_evento_bloccato_compare_e_si_ritenta(db_url):
    tenant, _ = await crea_tenant_e_classe(db_url)
    evento, _ = await _evento_bloccato(db_url, tenant)
    assert await _rileva() == 1
    assert await _rileva() == 0                                  # idempotente
    [riga] = await sql(db_url, "SELECT id, coda, servizio, codice, topic_origine, tenant_id "
                               "FROM public.scarti")
    assert (riga["coda"], riga["servizio"], riga["codice"], riga["topic_origine"],
            riga["tenant_id"]) == ("outbox_chiavi_ferme", "gateway", "OUTBOX_BLOCCATO",
                                   "topic-sbagliato", tenant)
    async with _client() as c:
        code = (await c.get("/api/dashboard/scarti/code", headers=_token())).json()["code"]
        dettaglio = (await c.get(f"/api/dashboard/scarti/{riga['id']}", headers=_token())).json()
    assert {"coda": "outbox_chiavi_ferme", "da_gestire": 1}.items() <= \
        [x for x in code if x["coda"] == "outbox_chiavi_ferme"][0].items()
    assert dettaglio["forma"] == "outbox_bloccato"
    assert dettaglio["valutazione"]["rielaborabile"] is True
    piano, r = await _rielabora([int(riga["id"])])
    assert r.status_code == 200, r.text
    assert await sql(db_url, "SELECT 1 FROM public.outbox_chiavi_ferme") == []
    ev = (await sql(db_url, "SELECT tentativi, ultimo_errore, pubblicato_il FROM "
                            "public.outbox_eventi WHERE id = $1", evento))[0]
    assert (ev["tentativi"], ev["ultimo_errore"], ev["pubblicato_il"]) == (0, None, None)
    audit = await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                              "'scarti.rielaborati'")
    assert len(audit) == 1
    # Una seconda applicazione dello stesso scarto: nulla da rielaborare
    piano, r = await _rielabora([int(riga["id"])])
    assert piano["rielaborabili"] == 0


async def test_evento_bloccato_archiviato_e_scartato_la_chiave_riparte(db_url):
    import libs.shared.outbox as outbox

    tenant, _ = await crea_tenant_e_classe(db_url)
    evento, _ = await _evento_bloccato(db_url, tenant, successivi=2)
    await _rileva()
    [riga] = await sql(db_url, "SELECT id FROM public.scarti")
    async with _client() as c:
        r = await c.post(f"/api/dashboard/scarti/{riga['id']}/archivia", headers=_token(),
                         json={"motivo": "topic dismesso: evento non più necessario"})
    assert r.status_code == 200, r.text
    ev = (await sql(db_url, "SELECT pubblicato_il, scartato_il, ultimo_errore FROM "
                            "public.outbox_eventi WHERE id = $1", evento))[0]
    assert ev["pubblicato_il"] is not None and ev["scartato_il"] == ev["pubblicato_il"]
    assert ev["ultimo_errore"].startswith("scartato dalle code di scarto")
    assert await sql(db_url, "SELECT 1 FROM public.outbox_chiavi_ferme") == []
    [audit] = await sql(db_url, "SELECT dettagli FROM public.audit_global WHERE evento = "
                                "'scarti.archiviato'")
    dettagli = json.loads(audit["dettagli"])
    assert dettagli["evento_scartato"] is True and dettagli["ordine_interrotto_per_la_chiave"] \
        == "pdv-x"
    # I successivi della stessa chiave ora escono, in ordine
    inviati = []

    async def invia(topic, chiave, payload):
        inviati.append(payload["i"])

    await outbox.pubblica_in_sospeso(invia, origine="gateway")
    assert inviati == [0, 1]


# ── Rilievo 7: la ripubblicazione resta nei permessi Kafka del servizio ──

def test_topic_ripubblicabili_coerenti_con_i_produttori_di_topics_yaml():
    """Ogni topic su cui «Rielabora» ripubblica ha dashboard-api fra i producers
    (da cui il ruolo IAM con WriteData solo su quei topic); i topic che annunciano
    conservazioni o checkpoint non sono ripubblicabili da qui."""
    from pathlib import Path

    import yaml
    from app.config import Settings

    radice = Path(__file__).resolve().parents[3]
    topics = {t["name"]: t for t in yaml.safe_load(
        (radice / "infra" / "kafka" / "topics.yaml").read_text(encoding="utf-8"))["topics"]}
    ripubblicabili = Settings().scarti_topic_ripubblicabili
    assert ripubblicabili
    for nome in ripubblicabili:
        assert "dashboard-api" in (topics[nome].get("producers") or []), nome
    assert not {"aip.conserved", "catena.checkpoint"} & set(ripubblicabili)
