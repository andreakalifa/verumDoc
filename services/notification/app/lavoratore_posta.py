# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/lavoratore_posta.py — invio della posta in un processo dedicato
(R12b, VD-C-161).

Perché: dal R12a (VD-C-152) il collegamento d'invito attraversa outbox, topic e coda
SIGILLATO per la posta e si apre solo all'invio con ``POSTA_CHIAVE_PRIVATA``. Finché
l'invio girava nel Deployment ``notification`` (le API eventi, webhook e campanella,
esposte all'ingress), una compromissione del processo esposto dava la chiave per
aprire i collegamenti in coda. Ora:

R14 (VD-C-172): anche la pulizia periodica delle notifiche personali oltre
``NOTIFICHE_PERSONALI_GIORNI`` (``ciclo_pulizia_personali``, lease e fencing in
``lavori_periodici``); le sue metriche e quelle della posta e della PEC (VD-C-170) le
calcolano dal database le repliche API (``app.application.metriche_posta``): questo
processo resta senza HTTP.

R13b (VD-C-078): lo stesso processo gira anche il canale PEC (``ciclo_pec``: invio
SMTP al gestore, lettura delle ricevute via IMAP, ``PEC_SEGRETI_JSON`` legati agli
host), con ``PEC_ATTIVO=true``.

  - ``python -m app.lavoratore_posta`` (Deployment ``notification-posta``, nessun
    HTTP, nessuna rotta dell'ingress, nessun chiamante) è l'UNICO processo con
    ``POSTA_INVIO_ATTIVO=true`` e l'unico a ricevere ``POSTA_CHIAVE_PRIVATA``,
    ``POSTA_CHIAVI_PRIVATE_PRECEDENTI`` e le credenziali SMTP (Secret proprio
    ``notification-posta-secrets``); uscita su Internet solo verso le porte SMTP;
  - le repliche API (``POSTA_INVIO_ATTIVO=false``) continuano ad ACCODARE i messaggi
    (consumer di ``notification.send`` e degli eventi da avvisare: basta controllare
    che un segreto sia un sigillo, non aprirlo) e in produzione NON partono se
    ricevono uno di quei segreti (``configurazione.problemi_posta``).

Ciclo: lo stesso di prima (``invio.invia_lotto``: recupero dei messaggi rimasti
IN_INVIO, prelievo con SKIP LOCKED, invio, ritentativi, DLQ), quindi più repliche
del lavoratore lavorano insieme senza doppioni. In produzione il lavoratore non parte
senza chiave privata valida o con SMTP in chiaro. Fuori produzione (sviluppo, un
solo processo) il ciclo può girare anche nel servizio con POSTA_INVIO_ATTIVO=true.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


async def ciclo_invio(coda: Any) -> None:
    """Giri di invio continui (prima in ``app.main``): un errore di un giro non
    ferma il ciclo."""
    from app.application.configurazione import apritore_posta
    from app.services.consumer import adesso_utc, pubblica_dlq
    from app.services.invio import invia_lotto
    from app.services.smtp import config_da_settings

    smtp = config_da_settings(settings)
    try:
        apritore = apritore_posta(settings)  # R12a (VD-C-152)
    except Exception as exc:  # noqa: BLE001 — in produzione l'avvio è già fermato
        logger.error("posta_chiave_privata_non_valida", errore=type(exc).__name__)
        apritore = None
    while True:
        try:
            esiti = await invia_lotto(
                coda,
                smtp,
                adesso=adesso_utc(),
                ritardi_secondi=settings.invio_ritardi_secondi,
                lotto=settings.invio_lotto,
                recupero_dopo_secondi=settings.invio_recupero_dopo_secondi,
                pubblica_dlq=pubblica_dlq,
                apritore=apritore,
            )
            if any(esiti.values()):
                logger.info("notifiche_giro_invio", **esiti)
        except Exception as exc:  # noqa: BLE001 — il ciclo continua, l'errore è registrato
            logger.error("notifiche_giro_invio_errore", errore=type(exc).__name__)
        await asyncio.sleep(settings.invio_intervallo_secondi)


async def ciclo_pec() -> None:
    """R13b (VD-C-078): giri del canale PEC (stato dei canali, invio, lettura delle
    ricevute); un errore di un giro non ferma il ciclo."""
    from app.application.pec import ImpostazioniPec, giro_pec
    from app.services.consumer import adesso_utc

    imp = ImpostazioniPec(
        segreti_json=settings.pec_segreti_json.get_secret_value(),
        produzione=settings.in_produzione, ritardi_secondi=list(settings.pec_ritardi_secondi),
        lotto=settings.pec_lotto, recupero_dopo_secondi=settings.pec_recupero_dopo_secondi,
        lettura_massimo=settings.pec_lettura_massimo)
    while True:
        try:
            conteggi = await giro_pec(imp, adesso=adesso_utc())
            if any(conteggi.values()):
                logger.info("pec_giro", **conteggi)
        except Exception as exc:  # noqa: BLE001 — il ciclo continua, l'errore è registrato
            logger.error("pec_giro_errore", errore=type(exc).__name__)
        await asyncio.sleep(settings.pec_intervallo_secondi)


async def ciclo_pulizia_personali(detentore: str | None = None) -> None:
    """R14 (VD-C-172): pulizia periodica delle notifiche personali (lease e fencing in
    ``lavori_periodici``); un errore di un giro non ferma il ciclo."""
    import os
    import socket
    from datetime import timedelta

    from app.application.pulizia_personali import giro
    from app.services.consumer import adesso_utc

    detentore = detentore or f"{socket.gethostname()}:{os.getpid()}"
    intervallo = timedelta(seconds=settings.pulizia_personali_intervallo_secondi)
    durata = timedelta(seconds=settings.pulizia_personali_lease_secondi)
    while True:
        try:
            await giro(detentore, adesso_utc(), intervallo=intervallo, durata=durata,
                       lotto=settings.pulizia_personali_lotto,
                       giri_massimi=settings.pulizia_personali_giri_massimi)
        except Exception as exc:  # noqa: BLE001 — il ciclo continua, l'errore è registrato
            logger.error("pulizia_notifiche_personali_giro_errore", errore=type(exc).__name__)
        # controllo frequente (il lease in tabella decide se il lavoro è dovuto)
        await asyncio.sleep(min(300.0, settings.pulizia_personali_intervallo_secondi))


def problemi_lavoratore(s: Any) -> list[str]:
    """Controlli puri dell'avvio del lavoratore della posta: in produzione ognuno lo
    ferma (REGOLE §19.4)."""
    from app.application.configurazione import problemi_posta
    from app.services.smtp import sicurezza_effettiva

    problemi: list[str] = []
    if not s.posta_invio_attivo:
        problemi.append("POSTA_INVIO_ATTIVO=false nel lavoratore della posta: nessun invio")
    problemi += problemi_posta(s)
    if s.in_produzione and s.smtp_host and \
            sicurezza_effettiva(s.smtp_port, s.smtp_sicurezza) == "nessuna":
        problemi.append("SMTP senza cifratura vietato in produzione")
    return problemi


async def esegui(ferma: asyncio.Event | None = None) -> None:
    """Avvio, ciclo di invio fino al segnale di arresto, chiusura ordinata."""
    from app.services.coda import CodaUscitaSQL
    from app.services.consumer import close_notification_consumer, init_producer_dlq
    from libs.shared.db import close_db, init_db

    problemi = problemi_lavoratore(settings)
    if problemi:
        if settings.in_produzione:
            raise SystemExit("lavoratore della posta non avviato: " + "; ".join(problemi))
        logger.warning("lavoratore_posta_configurazione_incompleta", problemi=problemi)
    ferma = ferma or asyncio.Event()
    await init_db(settings.database_url)
    await init_producer_dlq()
    logger.info("lavoratore_posta_avviato", env=settings.env)
    compiti = [asyncio.create_task(ciclo_invio(CodaUscitaSQL()))]
    if settings.pec_attivo:
        compiti.append(asyncio.create_task(ciclo_pec()))
    if settings.pulizia_personali_attiva:
        compiti.append(asyncio.create_task(ciclo_pulizia_personali()))
    try:
        await ferma.wait()
    finally:
        for compito in compiti:
            compito.cancel()
        for compito in compiti:
            with contextlib.suppress(asyncio.CancelledError):
                await compito
        await close_notification_consumer()
        await close_db()
        logger.info("lavoratore_posta_fermato")


def main() -> None:
    async def _principale() -> None:
        ferma = asyncio.Event()
        ciclo = asyncio.get_running_loop()
        for segnale in (signal.SIGTERM, signal.SIGINT):
            ciclo.add_signal_handler(segnale, ferma.set)
        await esegui(ferma)

    asyncio.run(_principale())


if __name__ == "__main__":
    main()


__all__ = ["ciclo_invio", "ciclo_pec", "ciclo_pulizia_personali", "esegui", "main",
           "problemi_lavoratore"]
