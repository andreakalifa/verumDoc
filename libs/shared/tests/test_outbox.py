# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""libs/shared/outbox.py: la pubblicazione dopo il commit non fa mai fallire il chiamante.

Le prove su PostgreSQL reale (evento nella transazione, Kafka giù, relay) sono
nei servizi: gateway (test_relay_lifespan, test_versamento), onboarding e
distribution (tests/test_g3_outbox.py).
"""
from __future__ import annotations

import pytest

import libs.shared.outbox as outbox


@pytest.mark.parametrize("errore", [RuntimeError("DB non inizializzato"),
                                    outbox.KafkaNonDisponibile("giù"), OSError("rete")])
async def test_pubblica_dopo_commit_non_solleva(monkeypatch, errore) -> None:
    async def rotta(**_):
        raise errore

    monkeypatch.setattr(outbox, "pubblica_in_sospeso", rotta)
    assert await outbox.pubblica_dopo_commit(evento="prova") == 0


async def test_pubblica_dopo_commit_restituisce_i_pubblicati(monkeypatch) -> None:
    chiamate = []

    async def finta(*, limite, riprove=True):
        chiamate.append((limite, riprove))
        return 3

    monkeypatch.setattr(outbox, "pubblica_in_sospeso", finta)
    assert await outbox.pubblica_dopo_commit(limite=7) == 3
    # N2 (secondo giro R04): nella richiesta HTTP niente ritentativi delle chiavi ferme
    assert chiamate == [(7, False)]


def test_gateway_riesporta_lo_stesso_modulo() -> None:
    """Una sola implementazione: il gateway non ha più una copia propria."""
    import importlib.util
    import sys
    from pathlib import Path

    radice = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "outbox_gateway", radice / "services" / "gateway" / "app" / "services" / "outbox.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["outbox_gateway"] = modulo
    spec.loader.exec_module(modulo)
    assert modulo.pubblica_in_sospeso is outbox.pubblica_in_sospeso
    assert modulo.accoda_evento is outbox.accoda_evento
    assert modulo.relay_outbox is outbox.relay_outbox
