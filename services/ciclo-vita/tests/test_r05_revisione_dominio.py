# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — regole pure delle correzioni (R1, R2, R9).
Nessun I/O: le stesse funzioni decidono per il servizio e per l'interfaccia.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domain import scarto as sc
from app.domain import stati as st

ADESSO = datetime(2026, 10, 23, 12, tzinfo=UTC)
SCADUTA = ADESSO - timedelta(days=1)


def _ctx(ruolo: str = st.ADMIN_TENANT, **kw) -> st.ContestoTransizione:
    return st.ContestoTransizione(ruolo=ruolo, stesso_ente=True, richiede_nulla_osta=False,
                                  **kw)


def test_motivo_blocco_transizione_esecuzione_avviata_nessuna_transizione() -> None:
    """R1: con l'esecuzione avviata nemmeno RESPINTA è ammessa (né dall'ente né
    dal conservatore)."""
    for ruolo in (st.ADMIN_TENANT, st.RSC, st.SUPER_ADMIN):
        blocco = st.motivo_blocco_transizione(
            st.SCARTO, st.ESEGUIBILE, st.RESPINTA,
            _ctx(ruolo, lavoro_in_corso=True, esecuzione_avviata=True))
        assert blocco is not None and blocco[0] == "LC-409-IN-ESECUZIONE"


def test_motivo_blocco_transizione_esecuzione_chiesta_si_respinge() -> None:
    """R1: esecuzione chiesta, non ancora avviata: respingere è ammesso, il resto no."""
    ctx = _ctx(lavoro_in_corso=True)
    assert st.motivo_blocco_transizione(st.SCARTO, st.ESEGUIBILE, st.RESPINTA, ctx) is None
    blocco = st.motivo_blocco_transizione(st.SCARTO, st.ESEGUIBILE, st.ESEGUITA,
                                          _ctx(st.RSC, lavoro_in_corso=True))
    assert blocco is not None and blocco[0] == "LC-409-IN-ESECUZIONE"


def test_azioni_consentite_esecuzione_avviata_nessuna_consentita() -> None:
    azioni = st.azioni_consentite(st.SCARTO, st.ESEGUIBILE,
                                  _ctx(lavoro_in_corso=True, esecuzione_avviata=True))
    assert azioni and not any(a["consentita"] for a in azioni)


def test_motivo_blocco_oggetti_nessuna_versione_blocca() -> None:
    """R2: zero versioni nel WORM = anomalia, mai «tutto cancellabile»."""
    assert sc.motivo_blocco_oggetti([], ADESSO) == sc.OGGETTI_ASSENTI


def test_motivo_blocco_oggetti_chiave_attesa_mancante_blocca() -> None:
    oggetto = sc.OggettoWorm("e/a/manifest.json", "v1", SCADUTA, False)
    assert sc.motivo_blocco_oggetti([oggetto], ADESSO) is None
    assert sc.motivo_blocco_oggetti([oggetto], ADESSO,
                                    attese={"e/a/manifest.json"}) is None
    assert sc.motivo_blocco_oggetti([oggetto], ADESSO,
                                    attese={"e/a/manifest.json", "e/a/files/0000-x.pdf"}
                                    ) == sc.OGGETTI_MANCANTI


@pytest.mark.parametrize(("natura", "atteso"), [
    (None, True), ("", True), ("PA", True), ("pa", True), ("SCONOSCIUTA", True),
    ("PRIVATO", False), (" privato ", False),
])
def test_richiede_nulla_osta_dalla_natura(natura, atteso) -> None:
    """R9: solo un ente DICHIARATO privato è esente dal nulla osta."""
    assert sc.richiede_nulla_osta(natura) is atteso
