# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (integrazione, contratto Q2) — eventi per i clienti del ciclo di vita.

ciclo-vita pubblica (outbox, topic = tipo) ``pratica.cambio_stato``,
``versamento.annullato``, ``versamento.rettificato``, ``scarto.eseguito``,
``riversamento.pronto``. notification li consuma, li proietta con un elenco
chiuso di campi e li rende sottoscrivibili dai webhook. ``GET
/api/v1/webhook/tipi`` espone il catalogo (la console non lo copia più).
"""
from __future__ import annotations

import uuid

import pytest
from app.config import Settings
from app.domain.eventi import TIPI_EVENTO, EventoNonValido, proietta

from tests.infra_reale import db_url, richiede_pg  # noqa: F401
from tests.test_b3_api import _ambiente, client, crea_ente, h  # noqa: F401

EVENTI_Q2 = ("pratica.cambio_stato", "versamento.annullato", "versamento.rettificato",
             "scarto.eseguito", "riversamento.pronto")
T = str(uuid.uuid4())
PRATICA = str(uuid.uuid4())
PDV = str(uuid.uuid4())


def test_eventi_q2_nel_catalogo_e_consumati_dai_loro_topic() -> None:
    assert set(EVENTI_Q2) <= set(TIPI_EVENTO)
    mappa = Settings().eventi_topic
    for tipo in EVENTI_Q2:
        assert mappa[tipo] == tipo


def test_cambio_stato_un_evento_per_ogni_versione() -> None:
    """Ogni cambio di stato è un'occorrenza distinta (la deduplica per risorsa
    scarta solo le riconsegne Kafka, non i passi successivi della pratica)."""
    base = {"tenant_id": T, "pratica_id": PRATICA, "tipo": "SCARTO", "ente": T,
            "il": "2026-10-23T10:00:00+00:00"}
    primo = proietta("pratica.cambio_stato",
                     {**base, "da_stato": None, "a_stato": "BOZZA", "versione": 1})
    secondo = proietta("pratica.cambio_stato",
                       {**base, "da_stato": "BOZZA", "a_stato": "PROPOSTA", "versione": 2})
    assert primo.tenant_id == T and primo.risorsa_tipo == "pratica"
    assert primo.risorsa_id != secondo.risorsa_id
    assert secondo.dati == {"pratica_id": PRATICA, "tipo": "SCARTO", "da_stato": "BOZZA",
                            "a_stato": "PROPOSTA", "versione": 2,
                            "il": "2026-10-23T10:00:00+00:00"}


def test_annullato_e_rettificato_solo_campi_ammessi() -> None:
    annullato = proietta("versamento.annullato", {
        "tenant_id": T, "pdv_id": PDV, "pratica_id": PRATICA, "ente": T,
        "stato_precedente": "CONSERVATO", "urn": "urn:prova:1",
        "annullato_il": "2026-10-23T10:00:00+00:00", "interno": "non va al cliente"})
    assert annullato.risorsa_tipo == "versamento" and annullato.risorsa_id == PDV
    assert "interno" not in annullato.dati and annullato.dati["urn"] == "urn:prova:1"
    nuovo = str(uuid.uuid4())
    rettificato = proietta("versamento.rettificato", {
        "tenant_id": T, "pdv_id": PDV, "pdv_rettifica_id": nuovo, "pratica_id": PRATICA,
        "urn": "urn:prova:1", "urn_rettifica": "urn:prova:2",
        "rettificato_il": "2026-10-23T10:00:00+00:00"})
    assert rettificato.dati["pdv_rettifica_id"] == nuovo
    # una seconda rettifica dello stesso PdV (altra pratica) è un altro evento
    altra = proietta("versamento.rettificato", {
        "tenant_id": T, "pdv_id": PDV, "pdv_rettifica_id": str(uuid.uuid4()),
        "pratica_id": str(uuid.uuid4())})
    assert altra.risorsa_id != rettificato.risorsa_id


def test_scarto_e_riversamento_risorsa_pratica() -> None:
    scarto = proietta("scarto.eseguito", {
        "tenant_id": T, "pratica_id": PRATICA, "conteggi": {"scartati": 3, "bloccati": 1},
        "verbale_sha256": "a" * 64, "eseguito_il": "2026-10-23T10:00:00+00:00"})
    assert (scarto.risorsa_tipo, scarto.risorsa_id) == ("pratica", PRATICA)
    assert scarto.dati["conteggi"] == {"scartati": 3, "bloccati": 1}
    pronto = proietta("riversamento.pronto", {
        "tenant_id": T, "pratica_id": PRATICA, "tipo": "RESTITUZIONE",
        "conteggi": {"pdd": 2}, "manifesto_sha256": "b" * 64,
        "manifesto": f"/api/v1/ciclo-vita/pratiche/{PRATICA}"})
    assert pronto.dati["manifesto"].endswith(PRATICA)


@pytest.mark.parametrize("tipo", EVENTI_Q2)
def test_eventi_q2_senza_ente_rifiutati(tipo) -> None:
    with pytest.raises(EventoNonValido):
        proietta(tipo, {"pratica_id": PRATICA, "pdv_id": PDV, "versione": 1})


@pytest.mark.integration
@richiede_pg
async def test_catalogo_dei_tipi_dall_api(client, db_url):  # noqa: F811
    ente = await crea_ente(db_url)
    r = await client.get("/api/v1/webhook/tipi", headers=h(ente))
    assert r.status_code == 200, r.text
    tipi = {t["tipo"]: t for t in r.json()["tipi"]}
    assert set(tipi) == set(TIPI_EVENTO)
    assert tipi["versamento.annullato"]["risorsa"] == "versamento"
    assert tipi["scarto.eseguito"]["descrizione"]
    r = await client.get("/api/v1/webhook/tipi", headers=h(ente, "VIEWER"))
    assert r.status_code == 403
    # sottoscrivibili davvero
    r = await client.post("/api/v1/webhook", headers=h(ente), json={
        "url": "https://hook.cliente-prova.it/eventi", "tipi": list(EVENTI_Q2)})
    assert r.status_code == 201, r.text
