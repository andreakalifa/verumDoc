# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R4: la prova d'inclusione non deve mai
nascondere uno scarto o un annullamento.

Prima della correzione ``foglie_ciclo_vita`` restituiva le PRIME
``CATENA_CICLO_VITA_MAX_FOGLIE`` foglie in ordine di indice, senza dire che
l'elenco era troncato: con 50 rettifiche seguite da uno scarto la prova non
riportava lo scarto e il verificatore concludeva «conservato». Ora scarto e
annullamento ci sono sempre, tutti; le rettifiche al più il limite (le più
recenti) e la risposta dichiara ``ciclo_vita_totale`` e ``ciclo_vita_completo``.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from app.config import settings
from app.registro import interne, prove

from libs.catena import foglie as foglie_lib
from libs.shared.db import get_async_session
from tests.infra_reale import (  # noqa: F401
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)
from tests.supporto_registro import crea_pda, fatto_ciclo_vita
from tests.test_r05_ricostruzione_worm import ORA, _giro, s3, testimone  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def _pda_con_rettifiche(db_url, rettifiche: int, *, scarto: bool):  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    p = await crea_pda(db_url, tenant, classe, n_doc=1)
    await _giro(ORA, None)
    pratiche: list[str] = []
    # Revisione 2 di R05 (S2): ogni foglia ha il suo fatto nel database
    for i in range(rettifiche):
        atto_i = hashlib.sha256(f"atto {i}".encode()).hexdigest()
        async with fatto_ciclo_vita(db_url, p, "rettifica", atto_i) as pratica, \
                get_async_session() as session:
            pratiche.append(pratica)
            await interne.richiedi_foglia_ciclo_vita(
                session, tipo="rettifica", pda=p.urn,
                riferimento={"pratica": pratica, "pdv_rettifica": str(p.pdv_id)},
                impronta=atto_i,
                richiesta_da="prova", adesso=ORA)
    if scarto:
        async with fatto_ciclo_vita(db_url, p, "scarto") as pratica, \
                get_async_session() as session:
            await interne.richiedi_foglia_ciclo_vita(
                session, tipo="scarto", pda=p.urn, riferimento={"pratica": pratica},
                impronta=hashlib.sha256(b"verbale").hexdigest(), richiesta_da="prova",
                adesso=ORA)
    await _giro(ORA + timedelta(minutes=1), None)
    async with get_async_session() as session:
        trovato = await prove.pda_per_urn(session, p.urn)
        return p, pratiche, await prove.prova_inclusione_documento(
            session, trovato, "doc-0", None,
            limite_ciclo_vita=settings.catena_ciclo_vita_max_foglie)


def _stato(urn: str, risposta: dict):
    return foglie_lib.stato_documento(
        urn, [(foglie_lib.analizza_foglia_registro(v["foglia_dati"]),
               v["prova_registro"]["foglia_indice"]) for v in risposta["ciclo_vita"]])


async def test_scarto_dopo_molte_rettifiche_sempre_nella_prova(db_url, s3, testimone,  # noqa: F811
                                                               monkeypatch) -> None:
    """R4: 8 rettifiche (limite 5) e poi uno scarto: lo scarto c'è, le rettifiche
    sono le 5 più recenti, la risposta dichiara 9 foglie e l'elenco incompleto."""
    monkeypatch.setattr(settings, "catena_ciclo_vita_max_foglie", 5)
    p, pratiche_create, risposta = await _pda_con_rettifiche(db_url, 8, scarto=True)
    tipi = [v["tipo"] for v in risposta["ciclo_vita"]]
    assert tipi.count("scarto") == 1 and tipi.count("rettifica") == 5
    assert risposta["ciclo_vita_totale"] == 9
    assert risposta["ciclo_vita_completo"] is False
    indici = [v["prova_registro"]["foglia_indice"] for v in risposta["ciclo_vita"]]
    assert indici == sorted(indici)
    pratiche = {foglie_lib.analizza_foglia_registro(v["foglia_dati"]).dati["riferimento"]
                ["pratica"] for v in risposta["ciclo_vita"] if v["tipo"] == "rettifica"}
    assert pratiche == set(pratiche_create[3:8])  # le più recenti
    assert _stato(p.urn, risposta).invalidante is not None


async def test_cinquanta_rettifiche_e_scarto_scarto_presente(db_url, s3, testimone) -> None:  # noqa: F811
    """Lo scenario della revisione (limite predefinito 50): lo scarto non sparisce."""
    p, _, risposta = await _pda_con_rettifiche(db_url, settings.catena_ciclo_vita_max_foglie,
                                            scarto=True)
    assert "scarto" in [v["tipo"] for v in risposta["ciclo_vita"]]
    assert _stato(p.urn, risposta).invalidante is not None
    assert risposta["ciclo_vita_totale"] == len(risposta["ciclo_vita"])
    assert risposta["ciclo_vita_completo"] is True


async def test_rettifiche_oltre_il_limite_elenco_dichiarato_incompleto(db_url, s3, testimone,  # noqa: F811
                                                                      monkeypatch) -> None:
    """Senza scarto ma con più rettifiche del limite: elenco dichiarato incompleto,
    così il verificatore non conclude «conservato»."""
    monkeypatch.setattr(settings, "catena_ciclo_vita_max_foglie", 2)
    _, _, risposta = await _pda_con_rettifiche(db_url, 3, scarto=False)
    assert len(risposta["ciclo_vita"]) == 2
    assert (risposta["ciclo_vita_totale"], risposta["ciclo_vita_completo"]) == (3, False)
