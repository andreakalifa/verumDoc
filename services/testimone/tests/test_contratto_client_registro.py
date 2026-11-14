# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Contratto K3 provato con il client VERO del registro (services/hashchain,
app/registro/testimone.py) contro questo testimone, via ASGI: stessi corpi,
stessi codici, controfirma verificata dal client con la chiave del testimone."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport

from libs.catena import nota as nota_lib
from tests.conftest import TOKEN_REGISTRO, richiede_pg

pytestmark = [pytest.mark.integration, richiede_pg]

FILE_CLIENT = (Path(__file__).resolve().parents[2] / "hashchain" / "app" / "registro"
               / "testimone.py")


def _modulo_client():  # type: ignore[no-untyped-def]
    if not FILE_CLIENT.is_file():
        pytest.skip("client del testimone della hashchain non presente")
    spec = importlib.util.spec_from_file_location("hashchain_client_testimone", FILE_CLIENT)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo  # le dataclass lo cercano in sys.modules
    spec.loader.exec_module(modulo)
    return modulo


async def test_client_hashchain_controfirma_409_e_rifiuto(client, registro, chiavi) -> None:
    from app.main import app

    m = _modulo_client()
    c = m.ClientTestimone("http://test", TOKEN_REGISTRO,
                          nota_lib.analizza_verificatore(chiavi["testimone"]),
                          transport=ASGITransport(app=app))
    registro.aggiungi(4)
    esito = await c.invia(0, [], registro.checkpoint())
    assert esito.esito == m.ESITO_CONTROFIRMATO, esito
    assert esito.controfirma.startswith("— verumdoc-testimone ")

    registro.aggiungi(3)
    esito = await c.invia(4, registro.prova(4), registro.checkpoint())
    assert esito.esito == m.ESITO_CONTROFIRMATO, esito

    registro.aggiungi(1)
    esito = await c.invia(4, registro.prova(4), registro.checkpoint())
    assert esito.esito == m.ESITO_DIMENSIONE_DIVERSA and esito.dimensione_testimone == 7

    prova = registro.prova(7)
    prova[0] = bytes(32)
    esito = await c.invia(7, prova, registro.checkpoint())
    assert esito.esito == m.ESITO_RIFIUTATO and esito.motivo.startswith("422")
