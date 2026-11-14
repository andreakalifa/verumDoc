# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/parametri.py — parametri del ciclo di vita letti dal motore (R09).

Riferimenti: decisioni D04, D06, D20, D21, D23; REGOLE_SVILUPPO §8.1, §8.2 (una
sola catena di risoluzione: ``libs.shared.parametri``), §9.2.

Prima di R09 ruoli dell'annullamento, tentativi dello scarto e retention degli
allegati erano impostazioni d'ambiente del servizio: ora sono parametri del
motore (conservatore → ente), visibili e modificabili in console. ``rigoroso``
(lavoratore): un errore del database si propaga e il passo si ritenta, invece di
valere come predefinito.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.domain.permessi import doppia_approvazione_richiesta
from libs.verumdoc_rules.parametri_servizio import (
    ANNULLAMENTO_DOPPIA_APPROVAZIONE,
    ANNULLAMENTO_RUOLI_APPROVAZIONE,
    ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE,
    ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE,
    RETENTION_PERMANENTE_ANNI,
)

CODICI_ANNULLAMENTO = (ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE,
                       ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE, ANNULLAMENTO_RUOLI_APPROVAZIONE,
                       ANNULLAMENTO_DOPPIA_APPROVAZIONE)


async def valori(codici: Iterable[str], tenant_id: Any = None, *, session: Any = None,
                 rigoroso: bool = False) -> dict[str, Any]:
    """Valori effettivi (stessa catena della console). ``rigoroso`` (lavoratore):
    lettura sempre dal database (nessuna cache del processo) e un errore si
    propaga invece di valere come predefinito."""
    from libs.shared.parametri import leggi_livelli, parametri, risolvi

    elenco = list(codici)
    if rigoroso:
        if session is None:
            from libs.shared.db import get_async_session

            async with get_async_session() as propria:
                return risolvi(await leggi_livelli(elenco, tenant_id, session=propria), elenco)
        return risolvi(await leggi_livelli(elenco, tenant_id, session=session), elenco)
    return await parametri(elenco, tenant_id, session=session)


@dataclass(frozen=True)
class RegolaAnnullamento:
    """D23: chi annulla prima e dopo la conservazione, chi approva e quando."""

    ruoli_prima: frozenset[str]
    ruoli_dopo: frozenset[str]
    ruoli_approvazione: frozenset[str]
    doppia: str

    def doppia_richiesta(self, *, conservato: bool) -> bool:
        return doppia_approvazione_richiesta(self.doppia, conservato=conservato)

    def come_dict(self) -> dict[str, Any]:
        return {"ruoli_prima": sorted(self.ruoli_prima), "ruoli_dopo": sorted(self.ruoli_dopo),
                "ruoli_approvazione": sorted(self.ruoli_approvazione),
                "doppia_approvazione": self.doppia}


async def regola_annullamento(tenant_id: Any, *, session: Any = None,
                              rigoroso: bool = False) -> RegolaAnnullamento:
    v = await valori(CODICI_ANNULLAMENTO, tenant_id, session=session, rigoroso=rigoroso)
    return RegolaAnnullamento(
        frozenset(v[ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE]),
        frozenset(v[ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE]),
        frozenset(v[ANNULLAMENTO_RUOLI_APPROVAZIONE]),
        str(v[ANNULLAMENTO_DOPPIA_APPROVAZIONE]))


async def anni_retention(tenant_id: Any = None) -> int:
    """R09 (D04): anni di blocco WORM di allegati, verbali, atti e manifesti
    (``RETENTION_PERMANENTE_ANNI``, predefinito 30; prima 100 fissi): il rinnovo
    automatico li estende quando ne mancano meno della soglia."""
    return int((await valori([RETENTION_PERMANENTE_ANNI], tenant_id))[RETENTION_PERMANENTE_ANNI])


__all__ = ["CODICI_ANNULLAMENTO", "RegolaAnnullamento", "anni_retention", "regola_annullamento",
           "valori"]
