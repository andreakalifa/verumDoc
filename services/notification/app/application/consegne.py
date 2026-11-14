# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/application/consegne.py
Lavoratore delle consegne webhook — contratto C3, REGOLE §12.4, §12.11.

Un giro: preleva un lotto di consegne scadute (SKIP LOCKED, lease), le invia
in parallelo con un limite di concorrenza e timeout brevi, registra l'esito di
ciascuna. Più repliche del servizio possono girare insieme. Il consumer Kafka
non aspetta mai questo lavoratore: legge e scrive solo righe nel database.

Esiti:
  - 2xx → CONSEGNATA;
  - timeout, connessione, risposta non 2xx → IN_CODA con il prossimo ritardo
    della lista costante; dopo l'ultimo → FALLITA (coda di scarto, ripetibile
    con ``POST /api/v1/webhook/{id}/consegne/{id}/riprova``);
  - indirizzo risolto non ammesso (SSRF), URL non più valido, segreto non
    decifrabile → FALLITA subito (ripetere non cambierebbe l'esito).
Consegna «almeno una volta»: se il lavoratore cade dopo l'invio e prima di
registrare l'esito, il lease scade e la consegna riparte; il destinatario
deduplica con ``VerumDoc-Id-Consegna`` / ``VerumDoc-Id-Evento``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from app.domain.webhook import (
    CONSEGNATA,
    FALLITA,
    INTESTAZIONE_EVENTO,
    INTESTAZIONE_FIRMA,
    INTESTAZIONE_ID_CONSEGNA,
    INTESTAZIONE_ID_EVENTO,
    UrlNonValido,
    corpo_consegna,
    esito_tentativo,
    intestazione_firme,
    valida_url,
)
from app.infrastructure.cifratura import Cifratore, ErroreCifratura, contesto_segreto
from app.infrastructure.consegna_http import EsitoInvio, InviatoreWebhook
from app.infrastructure.repo_webhook import ConsegnaDaFare, chiudi_tentativo, preleva_consegne

logger = structlog.get_logger(__name__)

Orologio = Callable[[], datetime]


def adesso_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ParametriConsegna:
    ritardi_secondi: list[int]
    lotto: int
    concorrenza: int
    lease_secondi: int
    lunghezza_massima_url: int
    consenti_privati: bool
    porte_ammesse: frozenset[int] = frozenset()


async def _consegna(c: ConsegnaDaFare, *, inviatore: InviatoreWebhook, cifratore: Cifratore,
                    parametri: ParametriConsegna, orologio: Orologio,
                    sessione_factory: Any) -> str:
    """Un tentativo per una consegna; restituisce lo stato registrato."""
    esito: EsitoInvio
    try:
        from app.application.webhook_service import porte_ente

        # R07 (D16): porte dell'ente (443 + globali + impostazione della console)
        porte = await porte_ente(c.tenant_id, parametri.porte_ammesse)
        destinazione = valida_url(c.url, lunghezza_massima=parametri.lunghezza_massima_url,
                                  consenti_privati=parametri.consenti_privati,
                                  porte_ammesse=porte)
        segreto = cifratore.decifra(c.segreto_cifrato,
                                    contesto_segreto(c.tenant_id, c.sottoscrizione_id))
        segreti = [segreto, *_precedente(c, cifratore, orologio())]
    except UrlNonValido as exc:
        esito = EsitoInvio(False, "WH-104", None, exc.messaggio, ritentabile=False)
    except ErroreCifratura:
        logger.error("webhook_segreto_non_decifrabile", consegna_id=c.id,
                     sottoscrizione_id=c.sottoscrizione_id)
        esito = EsitoInvio(False, "WH-106", None, "Segreto del webhook non leggibile",
                           ritentabile=False)
    else:
        corpo = corpo_consegna(c.evento)
        istante = orologio()
        intestazioni = {
            # R13b: nel periodo di transizione due firme (nuovo e precedente)
            INTESTAZIONE_FIRMA: intestazione_firme(segreti, int(istante.timestamp()), corpo),
            INTESTAZIONE_EVENTO: str(c.evento["tipo"]),
            INTESTAZIONE_ID_CONSEGNA: str(c.id),
            INTESTAZIONE_ID_EVENTO: str(c.evento["id"]),
        }
        esito = await inviatore.invia(destinazione, corpo, intestazioni)

    adesso = orologio()
    if esito.consegnata:
        stato, tentativi, prossimo = CONSEGNATA, c.tentativi + 1, None
    else:
        decisione = esito_tentativo(c.tentativi, ritentabile=esito.ritentabile, adesso=adesso,
                                    ritardi_secondi=parametri.ritardi_secondi)
        stato, tentativi = decisione.stato, decisione.tentativi
        prossimo = decisione.prossimo_tentativo
    registrato = await chiudi_tentativo(
        c, stato=stato, tentativi=tentativi, prossimo_tentativo=prossimo,
        stato_http=esito.stato_http, codice=esito.codice, errore=esito.messaggio,
        adesso=adesso, sessione_factory=sessione_factory)
    logger.info("webhook_tentativo", consegna_id=c.id, tenant_id=c.tenant_id,
                sottoscrizione_id=c.sottoscrizione_id, stato=stato, tentativi=tentativi,
                codice=esito.codice, stato_http=esito.stato_http, registrato=registrato)
    if stato == FALLITA:
        logger.warning("webhook_consegna_fallita", consegna_id=c.id, tenant_id=c.tenant_id,
                       codice=esito.codice)
    return stato if registrato else "LEASE_PERSO"


def _precedente(c: ConsegnaDaFare, cifratore: Cifratore, adesso: datetime) -> list[str]:
    """R13b: il segreto precedente firma SOLO finché la transizione non è scaduta
    (confronto con l'orologio del lavoratore a ogni consegna). Illeggibile: si
    consegna con il solo segreto attuale (e si registra)."""
    if not c.segreto_precedente_cifrato or c.segreto_precedente_scade_il is None \
            or c.segreto_precedente_scade_il <= adesso:
        return []
    try:
        return [cifratore.decifra(c.segreto_precedente_cifrato,
                                  contesto_segreto(c.tenant_id, c.sottoscrizione_id))]
    except ErroreCifratura:
        logger.error("webhook_segreto_precedente_non_decifrabile", consegna_id=c.id,
                     sottoscrizione_id=c.sottoscrizione_id)
        return []


async def esegui_giro(*, inviatore: InviatoreWebhook, cifratore: Cifratore,
                      parametri: ParametriConsegna, orologio: Orologio = adesso_utc,
                      sessione_factory: Any = None) -> dict[str, int]:
    """Un giro del lavoratore; conteggio degli stati registrati."""
    consegne = await preleva_consegne(adesso=orologio(), lotto=parametri.lotto,
                                      lease_secondi=parametri.lease_secondi,
                                      sessione_factory=sessione_factory)
    limite = asyncio.Semaphore(max(1, parametri.concorrenza))

    async def _una(c: ConsegnaDaFare) -> str:
        async with limite:
            return await _consegna(c, inviatore=inviatore, cifratore=cifratore,
                                   parametri=parametri, orologio=orologio,
                                   sessione_factory=sessione_factory)

    conteggi: dict[str, int] = {}
    for stato in await asyncio.gather(*(_una(c) for c in consegne)):
        conteggi[stato] = conteggi.get(stato, 0) + 1
    return conteggi


async def ciclo_consegne(*, inviatore: InviatoreWebhook, cifratore: Cifratore,
                         parametri: ParametriConsegna, intervallo_secondi: float,
                         attendi: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Ciclo continuo: un giro dopo l'altro; pausa solo se il giro era vuoto."""
    while True:
        try:
            conteggi = await esegui_giro(inviatore=inviatore, cifratore=cifratore,
                                         parametri=parametri)
            if conteggi:
                logger.info("webhook_giro", **conteggi)
                if sum(conteggi.values()) >= parametri.lotto:
                    continue  # lotto pieno: c'è altro da fare subito
        except Exception as exc:  # noqa: BLE001 — il ciclo continua, l'errore è registrato
            logger.error("webhook_giro_errore", errore=type(exc).__name__)
        await attendi(intervallo_secondi)
