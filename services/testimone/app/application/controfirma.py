# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/application/controfirma.py — caso d'uso «controfirma».

Riferimenti: contratto K3 di R04; PIANO_R03_R05 §5.3; REGOLE_SVILUPPO §12.6
(controlla poi crea sotto lock del padre), §12.7 (lock), §15.4 (registro a
prova di manomissione), §18.3 (mai scartare in silenzio).

Concorrenza: ogni richiesta prende il lock della riga ``stato_registro`` della
sua origine (``SELECT … FOR UPDATE``) e rilegge lo stato DOPO il lock. Due
richieste in parallelo sono quindi serializzate: la seconda vede ciò che la
prima ha scritto (stessa dimensione → idempotente o fork; ``old`` superato →
409). I vincoli unici (origine, epoca, dimensione) e (origine,
impronta_precedente) sono l'ultima difesa: anche senza lock due rami non
possono entrare nella stessa epoca.

Esiti con scrittura e risposta d'errore (FORK, PROVA_NON_VALIDA): l'allarme si
salva e si conferma PRIMA di rispondere; il rifiuto viene restituito come
esito, non come eccezione, così la transazione non va in rollback.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import checkpoint as cp_dominio
from app.domain.protocollo import (
    Esito,
    Richiesta,
    UltimoControfirmato,
    decidi,
    nota_controfirmata,
)
from app.infrastructure.modelli import (
    ALLARME_FORK,
    ALLARME_PROVA_NON_VALIDA,
    IMPRONTA_INIZIALE,
    STATO_COMPROMESSO,
    Allarme,
    CheckpointControfirmato,
    StatoRegistro,
)
from app.osservabilita import MetricheTestimone

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Risultato:
    """Esito di una richiesta di controfirma."""

    esito: Esito | None
    origine: str
    dimensione_presentata: int
    # Riga «— verumdoc-testimone <base64>» (CONTROFIRMA, IDEMPOTENTE)
    riga_firma: str | None = None
    # Ultima dimensione controfirmata (OLD_NON_COINCIDE e allarmi)
    dimensione_nota: int | None = None
    # True se il testimone era GIÀ bloccato (nessuna decisione presa)
    bloccato: bool = False


def impronta_catena(precedente: bytes, nota: str) -> bytes:
    """Anello della catena propria del testimone: SHA-256(precedente || nota)."""
    return hashlib.sha256(precedente + nota.encode("utf-8")).digest()


async def blocca_stato(sessione: AsyncSession, origine: str) -> StatoRegistro:
    """Riga di stato dell'origine sotto lock (creata alla prima richiesta)."""
    await sessione.execute(
        insert(StatoRegistro)
        .values(origine=origine, impronta_ultima=IMPRONTA_INIZIALE)
        .on_conflict_do_nothing(index_elements=["origine"])
    )
    stato = (
        await sessione.execute(
            select(StatoRegistro)
            .where(StatoRegistro.origine == origine)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return stato


async def checkpoint_a_dimensione(
    sessione: AsyncSession,
    origine: str,
    epoca: int,
    dimensione: int,
) -> CheckpointControfirmato | None:
    return (
        await sessione.execute(
            select(CheckpointControfirmato).where(
                CheckpointControfirmato.origine == origine,
                CheckpointControfirmato.epoca == epoca,
                CheckpointControfirmato.dimensione == dimensione,
            )
        )
    ).scalar_one_or_none()


async def registra_controfirma(
    sessione: AsyncSession,
    stato: StatoRegistro,
    cp: cp_dominio.CheckpointRegistro,
    *,
    chiave: cp_dominio.ChiaveTestimone,
) -> CheckpointControfirmato:
    """Firma il corpo del checkpoint, salva la riga e sposta la testa (sotto lock)."""
    riga = chiave.firma(cp.corpo)
    nota = nota_controfirmata(cp.nota, riga, chiave.nome)
    impronta = impronta_catena(stato.impronta_ultima, nota)
    riga_db = CheckpointControfirmato(
        origine=stato.origine,
        epoca=stato.epoca,
        dimensione=cp.dimensione,
        radice=cp.radice,
        nota_registro=cp.nota,
        firma_testimone=riga,
        nota_controfirmata=nota,
        impronta=impronta,
        impronta_precedente=stato.impronta_ultima,
    )
    sessione.add(riga_db)
    stato.dimensione = cp.dimensione
    stato.radice = cp.radice
    stato.impronta_ultima = impronta
    stato.aggiornato_il = datetime.now(UTC)
    await sessione.flush()
    return riga_db


async def controfirma(
    sessione: AsyncSession,
    richiesta: Richiesta,
    cp: cp_dominio.CheckpointRegistro,
    *,
    chiave: cp_dominio.ChiaveTestimone,
    metriche: MetricheTestimone,
) -> Risultato:
    """Decide e registra la richiesta (firma del registro e origine già
    verificate). La sessione va confermata dal chiamante anche negli esiti
    di rifiuto: gli allarmi sono scritture."""
    stato = await blocca_stato(sessione, cp.origine)
    if stato.stato == STATO_COMPROMESSO:
        metriche.rifiuti.labels("compromesso").inc()
        logger.error(
            "testimone_compromesso_richiesta_rifiutata",
            origine=cp.origine,
            dimensione=cp.dimensione,
            compromesso_dal=str(stato.compromesso_dal),
        )
        return Risultato(
            None, cp.origine, cp.dimensione, bloccato=True, dimensione_nota=stato.dimensione
        )

    stessa = await checkpoint_a_dimensione(sessione, cp.origine, stato.epoca, cp.dimensione)
    testa = (
        UltimoControfirmato(stato.dimensione, stato.radice)
        if stato.dimensione is not None and stato.radice is not None
        else None
    )
    esito = decidi(
        richiesta,
        dimensione=cp.dimensione,
        radice=cp.radice,
        testa=testa,
        radice_stessa_dimensione=stessa.radice if stessa is not None else None,
    )

    if esito is Esito.CONTROFIRMA:
        riga = await registra_controfirma(sessione, stato, cp, chiave=chiave)
        metriche.controfirme.labels(cp.origine).inc()
        metriche.aggiorna_stato(
            cp.origine, compromesso=False, dimensione=cp.dimensione, ultima_controfirma=time.time()
        )
        logger.info(
            "checkpoint_controfirmato",
            origine=cp.origine,
            dimensione=cp.dimensione,
            old=richiesta.old,
            radice=cp.radice.hex()[:16],
        )
        return Risultato(
            esito,
            cp.origine,
            cp.dimensione,
            riga_firma=riga.firma_testimone,
            dimensione_nota=cp.dimensione,
        )

    if esito is Esito.IDEMPOTENTE:
        assert stessa is not None  # noqa: S101 — garantito da decidi
        logger.info("checkpoint_gia_controfirmato", origine=cp.origine, dimensione=cp.dimensione)
        return Risultato(
            esito,
            cp.origine,
            cp.dimensione,
            riga_firma=stessa.firma_testimone,
            dimensione_nota=stato.dimensione,
        )

    if esito is Esito.FORK:
        assert stessa is not None  # noqa: S101 — garantito da decidi
        adesso = datetime.now(UTC)
        sessione.add(
            Allarme(
                origine=cp.origine,
                epoca=stato.epoca,
                tipo=ALLARME_FORK,
                blocca=True,
                dimensione_presentata=cp.dimensione,
                radice_presentata=cp.radice,
                dimensione_conosciuta=stessa.dimensione,
                radice_conosciuta=stessa.radice,
                old=richiesta.old,
                nota_presentata=cp.nota,
                prova=richiesta.prova_testo(),
            )
        )
        stato.stato = STATO_COMPROMESSO
        stato.compromesso_dal = adesso
        stato.motivo = (
            f"Fork: due checkpoint firmati dal registro con dimensione "
            f"{cp.dimensione} e radici diverse"
        )
        stato.aggiornato_il = adesso
        await sessione.flush()
        metriche.allarmi.labels(cp.origine, ALLARME_FORK).inc()
        metriche.rifiuti.labels("fork").inc()
        metriche.aggiorna_stato(
            cp.origine, compromesso=True, dimensione=stato.dimensione, ultima_controfirma=None
        )
        logger.critical(
            "registro_biforcato",
            origine=cp.origine,
            dimensione=cp.dimensione,
            radice_presentata=cp.radice.hex(),
            radice_controfirmata=stessa.radice.hex(),
            azione="testimone bloccato: serve l'intervento di un operatore",
        )
        return Risultato(esito, cp.origine, cp.dimensione, dimensione_nota=stato.dimensione)

    if esito is Esito.PROVA_NON_VALIDA:
        sessione.add(
            Allarme(
                origine=cp.origine,
                epoca=stato.epoca,
                tipo=ALLARME_PROVA_NON_VALIDA,
                blocca=False,
                dimensione_presentata=cp.dimensione,
                radice_presentata=cp.radice,
                dimensione_conosciuta=stato.dimensione,
                radice_conosciuta=stato.radice,
                old=richiesta.old,
                nota_presentata=cp.nota,
                prova=richiesta.prova_testo(),
            )
        )
        await sessione.flush()
        metriche.allarmi.labels(cp.origine, ALLARME_PROVA_NON_VALIDA).inc()
        metriche.rifiuti.labels("prova_non_valida").inc()
        logger.error(
            "prova_consistenza_non_valida",
            origine=cp.origine,
            old=richiesta.old,
            dimensione=cp.dimensione,
            dimensione_controfirmata=stato.dimensione,
        )
        return Risultato(esito, cp.origine, cp.dimensione, dimensione_nota=stato.dimensione)

    # OLD_NON_COINCIDE, OLD_OLTRE: nessuna scrittura
    metriche.rifiuti.labels(esito.value.lower()).inc()
    logger.warning(
        "richiesta_controfirma_rifiutata",
        esito=esito.value,
        origine=cp.origine,
        old=richiesta.old,
        dimensione=cp.dimensione,
        dimensione_controfirmata=stato.dimensione,
    )
    return Risultato(
        esito,
        cp.origine,
        cp.dimensione,
        dimensione_nota=stato.dimensione if stato.dimensione is not None else 0,
    )


__all__ = [
    "Risultato",
    "blocca_stato",
    "checkpoint_a_dimensione",
    "controfirma",
    "impronta_catena",
    "registra_controfirma",
]
