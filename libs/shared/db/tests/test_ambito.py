# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (VD-C-080) — ambito d'ente delle transazioni, parti senza database:
identità → ambito, ambito conservatore solo esplicito e con motivo, parametri
di ``SET LOCAL``, richiesta HTTP che parte senza ambito e non lo lascia al
chiamante. La prova sul database è services/gateway/tests/test_r05_rls_ente.py.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from libs.shared.db.ambito import (
    NESSUN_AMBITO,
    Ambito,
    AmbitoNonValido,
    MiddlewareAmbitoRichiesta,
    ambito_conservatore,
    ambito_corrente,
    ambito_ente,
    con_ambito_conservatore,
    imposta_ambito,
    imposta_ambito_identita,
    ripristina_ambito,
)

ENTE = uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture(autouse=True)
def _pulito():
    token = imposta_ambito(NESSUN_AMBITO)
    yield
    ripristina_ambito(token)


def test_parametri_di_set_local():
    assert NESSUN_AMBITO.parametri() == {"ente": "", "ambito": ""}
    assert Ambito(ente=ENTE).parametri() == {"ente": str(ENTE), "ambito": "ente"}
    assert Ambito(conservatore=True, motivo="x").parametri() == {
        "ente": "", "ambito": "conservatore"}


@pytest.mark.parametrize(("payload", "atteso"), [
    ({"tenant_id": str(ENTE), "ruolo": "OPERATORE"}, Ambito(ente=ENTE)),
    ({"tenant_id": str(ENTE), "ruolo": "API_SYSTEM"}, Ambito(ente=ENTE)),
    ({"tenant_id": "non-uuid", "ruolo": "OPERATORE"}, NESSUN_AMBITO),
    ({"sub": "servizio:hashchain", "servizio": "hashchain"}, NESSUN_AMBITO),
])
def test_identita_verificata_ente_o_nessuno(payload, atteso):
    assert imposta_ambito_identita(payload) == atteso
    assert ambito_corrente() == atteso


@pytest.mark.parametrize("ruolo", ["SUPER_ADMIN", "RSC_CONSERVATORE"])
def test_ruoli_del_conservatore_tutti_gli_enti(ruolo):
    ambito = imposta_ambito_identita({"tenant_id": str(ENTE), "ruolo": ruolo})
    assert ambito.conservatore and ambito.parametri()["ambito"] == "conservatore"


def test_ambito_conservatore_esplicito_con_motivo_e_ripristinato():
    with pytest.raises(AmbitoNonValido):
        with ambito_conservatore(""):
            pass
    with pytest.raises(AmbitoNonValido):
        con_ambito_conservatore("  ")
    with ambito_ente(ENTE):
        with ambito_conservatore("lavoratore di prova"):
            assert ambito_corrente().conservatore
        assert ambito_corrente() == Ambito(ente=ENTE)
    assert ambito_corrente() == NESSUN_AMBITO
    with pytest.raises(AmbitoNonValido):
        with ambito_ente("non-un-uuid"):
            pass


async def test_decoratore_del_lavoratore():
    @con_ambito_conservatore("lavoratore decorato di prova")
    async def lavoro() -> bool:
        return ambito_corrente().conservatore

    assert await lavoro() is True
    assert ambito_corrente() == NESSUN_AMBITO


async def test_richiesta_parte_senza_ambito_e_non_lo_lascia():
    visto: list[Ambito] = []

    async def app(scope, receive, send):  # noqa: ANN001
        visto.append(ambito_corrente())
        imposta_ambito_identita({"tenant_id": str(ENTE), "ruolo": "OPERATORE"})

    middleware = MiddlewareAmbitoRichiesta(app)
    with ambito_conservatore("chiamante con ambito"):
        await middleware({"type": "http"}, None, None)
        assert ambito_corrente().conservatore  # il chiamante ritrova il suo
    assert visto == [NESSUN_AMBITO]


async def test_task_diversi_ambiti_indipendenti():
    async def in_ente(e: uuid.UUID) -> uuid.UUID | None:
        with ambito_ente(e):
            await asyncio.sleep(0)
            return ambito_corrente().ente

    altro = uuid.uuid4()
    assert await asyncio.gather(in_ente(ENTE), in_ente(altro)) == [ENTE, altro]
