# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/application/stato.py — stato del testimone e interventi
dell'operatore.

Riferimenti: contratto K3 (allarme di fork: stato persistente «compromesso»,
nessuna controfirma finché un operatore non interviene), REGOLE §15.7 (il
motivo è un dato), §11.5 (ogni stato ha un'uscita).

Due uscite dallo stato COMPROMESSO, entrambe manuali, registrate in
``interventi_operatore`` con operatore e motivo, eseguibili SOLO dall'account
del testimone (``python -m app.operatore``; nessuna API remota):

  - SBLOCCO: torna ATTIVO tenendo l'ultimo checkpoint controfirmato come
    riferimento; il registro deve continuare da lì (con prova di consistenza).
    Si usa quando l'indagine conclude che il ramo controfirmato è quello buono.
  - NUOVA_BASE: apre un'epoca nuova a partire da un checkpoint firmato dal
    registro indicato dall'operatore (per esempio il registro ricostruito dal
    WORM), che il testimone controfirma come base. Le controfirme delle epoche
    precedenti restano, immutate, come prova.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.controfirma import blocca_stato, registra_controfirma
from app.domain.checkpoint import CheckpointRegistro, ChiaveTestimone
from app.infrastructure.modelli import (
    AZIONE_NUOVA_BASE,
    AZIONE_SBLOCCO,
    STATO_ATTIVO,
    STATO_COMPROMESSO,
    Allarme,
    CheckpointControfirmato,
    InterventoOperatore,
    StatoRegistro,
)
from app.osservabilita import MetricheTestimone
from libs.shared.db import get_async_session


class InterventoNonAmmesso(ValueError):
    """L'intervento richiesto non è coerente con lo stato attuale."""


@dataclass(frozen=True)
class EsitoIntervento:
    azione: str
    origine: str
    epoca: int
    dimensione: int | None
    intervento_id: int


async def aggiorna_metriche_stato(metriche: MetricheTestimone, origini: list[str]) -> None:
    """Indicatori Prometheus dallo stato salvato (vale per tutte le repliche)."""
    async with get_async_session() as sessione:
        righe = (
            (
                await sessione.execute(
                    select(StatoRegistro).where(StatoRegistro.origine.in_(origini))
                )
            )
            .scalars()
            .all()
        )
        ultime = dict(
            (
                await sessione.execute(
                    select(
                        CheckpointControfirmato.origine,
                        func.max(CheckpointControfirmato.controfirmato_il),
                    )
                    .where(CheckpointControfirmato.origine.in_(origini))
                    .group_by(CheckpointControfirmato.origine)
                )
            ).all()
        )
    trovate = {r.origine: r for r in righe}
    for origine in origini:
        r = trovate.get(origine)
        ultima = ultime.get(origine)
        metriche.aggiorna_stato(
            origine,
            compromesso=r is not None and r.stato == STATO_COMPROMESSO,
            dimensione=r.dimensione if r is not None else None,
            ultima_controfirma=ultima.timestamp() if ultima is not None else None,
        )


def _controlla_testo(operatore: str, motivo: str) -> None:
    if not operatore.strip():
        raise InterventoNonAmmesso("Indicare l'operatore")
    if len(motivo.strip()) < 10:
        raise InterventoNonAmmesso("Il motivo deve spiegare l'intervento (almeno 10 caratteri)")


async def sblocca(
    sessione: AsyncSession, origine: str, *, operatore: str, motivo: str
) -> EsitoIntervento:
    """COMPROMESSO → ATTIVO mantenendo la testa controfirmata."""
    _controlla_testo(operatore, motivo)
    stato = await blocca_stato(sessione, origine)
    if stato.stato != STATO_COMPROMESSO:
        raise InterventoNonAmmesso(f"Il testimone per {origine} non è bloccato")
    intervento = InterventoOperatore(
        origine=origine,
        azione=AZIONE_SBLOCCO,
        operatore=operatore.strip(),
        motivo=motivo.strip(),
        epoca_precedente=stato.epoca,
        epoca_nuova=stato.epoca,
        stato_precedente=stato.stato,
    )
    sessione.add(intervento)
    stato.stato = STATO_ATTIVO
    stato.compromesso_dal = None
    stato.motivo = None
    stato.aggiornato_il = datetime.now(UTC)
    await sessione.flush()
    return EsitoIntervento(AZIONE_SBLOCCO, origine, stato.epoca, stato.dimensione, intervento.id)


async def nuova_base(
    sessione: AsyncSession,
    cp: CheckpointRegistro,
    *,
    chiave: ChiaveTestimone,
    operatore: str,
    motivo: str,
) -> EsitoIntervento:
    """Epoca nuova a partire da ``cp`` (firma del registro già verificata),
    controfirmato come base; lo stato torna ATTIVO."""
    _controlla_testo(operatore, motivo)
    stato = await blocca_stato(sessione, cp.origine)
    epoca_precedente, stato_precedente = stato.epoca, stato.stato
    stato.epoca = epoca_precedente + 1
    riga = await registra_controfirma(sessione, stato, cp, chiave=chiave)
    intervento = InterventoOperatore(
        origine=cp.origine,
        azione=AZIONE_NUOVA_BASE,
        operatore=operatore.strip(),
        motivo=motivo.strip(),
        epoca_precedente=epoca_precedente,
        epoca_nuova=stato.epoca,
        stato_precedente=stato_precedente,
        checkpoint_id=riga.id,
    )
    sessione.add(intervento)
    stato.stato = STATO_ATTIVO
    stato.compromesso_dal = None
    stato.motivo = None
    await sessione.flush()
    return EsitoIntervento(AZIONE_NUOVA_BASE, cp.origine, stato.epoca, cp.dimensione, intervento.id)


async def ultimi_allarmi(sessione: AsyncSession, origine: str, limite: int = 20) -> list[Allarme]:
    return list(
        (
            await sessione.execute(
                select(Allarme)
                .where(Allarme.origine == origine)
                .order_by(Allarme.id.desc())
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "EsitoIntervento",
    "InterventoNonAmmesso",
    "aggiorna_metriche_stato",
    "nuova_base",
    "sblocca",
    "ultimi_allarmi",
]
