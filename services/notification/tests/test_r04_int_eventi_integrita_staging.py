# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — eventi per i clienti ``integrita.anomalia`` (verifica continua,
hashchain) e ``versamento.staging_scaduto`` (VD-C-104, gateway).

Decisione (rapporto int): ``integrita.anomalia`` diventa un EVENTO PER L'ENTE
del PdA (elenco eventi + webhook a chi lo sottoscrive); l'allarme al
conservatore resta Alertmanager sulle metriche ``verumdoc_verifica_anomalie_*``.
"""
from __future__ import annotations

import uuid

import pytest
from app.application.eventi_consumer import elabora_evento_cliente
from app.config import Settings, settings
from app.domain.eventi import (
    OGGETTI_ANOMALIA_MASSIMI,
    TIPI_EVENTO,
    EventoNonValido,
    proietta,
)
from app.infrastructure.repo_eventi import elenca_eventi, pubblica_eventi

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401 — fixture
from tests.test_b3_integrazione import crea_sottoscrizione, crea_tenant

T = str(uuid.uuid4())
AIP = str(uuid.uuid4())
DOC = str(uuid.uuid4())
PDV = str(uuid.uuid4())


def _anomalia(**altro) -> dict:
    return {
        "tipo": "integrita.anomalia", "versione": 1, "aip_id": AIP, "pda": "urn:pda:1",
        "tenant_id": T, "verificato_il": "2026-10-23T10:00:00+00:00", "esito": "DIVERSO",
        "tentativo": 1, "errore_lettura_ripetuto": False,
        "oggetti": [{"componente": "FILE", "documento_id": DOC, "esito": "DIVERSO",
                     "impronta_attesa": "a" * 64, "impronta_trovata": "b" * 64,
                     "motivo": "impronta diversa", "chiave": "ente/pda/Mario Rossi.pdf"}],
        **altro,
    }


def _staging(**altro) -> dict:
    return {"tipo": "versamento.staging_scaduto", "pdv_id": PDV, "tenant_id": T,
            "stato": "ERRORE", "scade_il": "2026-11-01T00:00:00+00:00",
            "giorni_conservazione": 90, "preavviso_giorni": 7,
            "avvisato_il": "2026-10-25T00:00:00+00:00", "docs_s3_keys": ["staging/x"], **altro}


def test_topic_mappati_e_nel_catalogo() -> None:
    mappa = Settings().eventi_topic
    assert mappa["integrita.anomalia"] == "integrita.anomalia"
    assert mappa["versamento.staging_scaduto"] == "versamento.staging_scaduto"
    assert TIPI_EVENTO["integrita.anomalia"].risorsa_tipo == "verifica_integrita"
    assert TIPI_EVENTO["versamento.staging_scaduto"].risorsa_tipo == "versamento"


def test_proiezione_anomalia_solo_campi_ammessi_e_id_per_occorrenza() -> None:
    ev = proietta("integrita.anomalia", _anomalia())
    assert ev.tenant_id == T and not ev.tutti_gli_enti
    assert ev.risorsa_id == f"{AIP}@2026-10-23T10:00:00+00:00"
    assert len(ev.risorsa_id) <= 100  # colonna varchar(100)
    assert ev.dati["esito"] == "DIVERSO" and ev.dati["pda"] == "urn:pda:1"
    oggetto = ev.dati["oggetti"][0]
    # niente motivo testuale né chiavi di storage o nomi di file (§19.9)
    assert set(oggetto) == {"componente", "documento_id", "esito", "impronta_attesa",
                            "impronta_trovata"}
    successiva = proietta("integrita.anomalia",
                          _anomalia(verificato_il="2027-10-23T10:00:00+00:00"))
    assert successiva.risorsa_id != ev.risorsa_id  # due anomalie = due eventi
    molti = proietta("integrita.anomalia", _anomalia(oggetti=[
        {"componente": "FILE", "esito": "MANCANTE"}] * (OGGETTI_ANOMALIA_MASSIMI + 5)))
    assert len(molti.dati["oggetti"]) == OGGETTI_ANOMALIA_MASSIMI
    with pytest.raises(EventoNonValido):
        proietta("integrita.anomalia", _anomalia(verificato_il=None))
    with pytest.raises(EventoNonValido):
        proietta("integrita.anomalia", _anomalia(aip_id="non-uuid"))


def test_proiezione_staging_scaduto() -> None:
    ev = proietta("versamento.staging_scaduto", _staging())
    assert (ev.tenant_id, ev.risorsa_tipo, ev.risorsa_id) == (T, "versamento", PDV)
    assert ev.dati == {"pdv_id": PDV, "stato": "ERRORE", "scade_il": "2026-11-01T00:00:00+00:00",
                       "giorni_conservazione": 90, "preavviso_giorni": 7,
                       "avvisato_il": "2026-10-25T00:00:00+00:00"}
    with pytest.raises(EventoNonValido):
        proietta("versamento.staging_scaduto", _staging(pdv_id=None))


@pytest.mark.integration
@richiede_pg
async def test_anomalia_e_staging_diventano_eventi_dell_ente_e_consegne(db_url):
    ente = await crea_tenant(db_url)
    altro = await crea_tenant(db_url)
    await crea_sottoscrizione(db_url, ente, ["integrita.anomalia", "versamento.staging_scaduto"])
    anomalia = _anomalia(tenant_id=ente)
    assert await elabora_evento_cliente("integrita.anomalia", anomalia,
                                        mappa_topic=settings.eventi_topic) == 1
    # riconsegna Kafka: deduplicata
    assert await elabora_evento_cliente("integrita.anomalia", anomalia,
                                        mappa_topic=settings.eventi_topic) == 0
    # anomalia successiva dello stesso PdA: evento nuovo
    assert await elabora_evento_cliente(
        "integrita.anomalia", {**anomalia, "verificato_il": "2027-01-01T00:00:00+00:00"},
        mappa_topic=settings.eventi_topic) == 1
    assert await elabora_evento_cliente("versamento.staging_scaduto",
                                        _staging(tenant_id=ente),
                                        mappa_topic=settings.eventi_topic) == 1
    await pubblica_eventi()
    eventi, _ = await elenca_eventi(str(ente), dopo=0, limite=10, tipi=None)
    assert sorted(e.tipo for e in eventi) == [
        "integrita.anomalia", "integrita.anomalia", "versamento.staging_scaduto"]
    estranei, _ = await elenca_eventi(str(altro), dopo=0, limite=10, tipi=None)
    assert estranei == []
    righe = await sql(db_url, "SELECT count(*) AS n FROM public.webhook_consegne")
    assert righe[0]["n"] == 3
