# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/services/invio.py
Invio dei messaggi in coda con ritentativi e DLQ — VD-C-024, REGOLE §21.3, §22.

Ciclo: recupera i messaggi rimasti IN_INVIO dopo un crash, preleva i messaggi
IN_CODA scaduti, li invia via SMTP e ne registra l'esito:
  - inviata → INVIATA (con Message-ID);
  - errore temporaneo → IN_CODA con prossimo_tentativo secondo i ritardi;
  - errore permanente o tentativi esauriti → NON_PARTITA + pubblicazione sulla
    DLQ (topic notification.dlq) con il contesto, senza contenuti né segreti;
  - posta non configurata → NON_PARTITA con motivo «posta non configurata».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import structlog

from app.services.coda import CodaUscita, MessaggioUscita
from app.services.smtp import (
    PERMANENTE,
    ConfigSMTP,
    EsitoInvio,
    componi,
    invia_email,
    maschera,
)
from libs.shared.security.sigillo import SEGNAPOSTO, Apritore, ErroreSigillo, contesto_posta

logger = structlog.get_logger(__name__)

InviaFn = Callable[[ConfigSMTP, object], Awaitable[EsitoInvio]]
PubblicaDLQ = Callable[[dict], Awaitable[None]]


async def invia_lotto(
    coda: CodaUscita,
    smtp: ConfigSMTP | None,
    *,
    adesso: datetime,
    ritardi_secondi: list[int],
    lotto: int,
    recupero_dopo_secondi: int,
    pubblica_dlq: PubblicaDLQ,
    invia: InviaFn = invia_email,  # type: ignore[assignment]
    apritore: Apritore | None = None,
) -> dict[str, int]:
    """Un giro di invio. Restituisce il conteggio degli esiti."""
    conteggi = {"inviate": 0, "riprogrammate": 0, "non_partite": 0, "recuperate": 0}
    conteggi["recuperate"] = await coda.recupera_bloccati(
        adesso - timedelta(seconds=recupero_dopo_secondi), adesso
    )
    messaggi = await coda.preleva(adesso, lotto)
    for m in messaggi:
        tentativo = m.tentativi + 1
        testo, html, problema = apri_segreti(m, apritore)
        if smtp is None:
            esito = EsitoInvio(PERMANENTE, "posta non configurata (SMTP_HOST/SMTP_MITTENTE)")
        elif problema:
            esito = EsitoInvio(PERMANENTE, problema)
        else:
            msg = componi(smtp, m.destinatario, str(m.payload.get("oggetto") or ""), testo, html)
            esito = await invia(smtp, msg)
        if esito.esito == "inviata":
            await coda.segna_inviata(m.id, adesso, esito.message_id)
            conteggi["inviate"] += 1
        elif esito.ritentabile and tentativo <= len(ritardi_secondi):
            prossimo = adesso + timedelta(seconds=ritardi_secondi[tentativo - 1])
            await coda.riprogramma(m.id, tentativo, prossimo, esito.messaggio, adesso)
            conteggi["riprogrammate"] += 1
            logger.info(
                "notifica_riprogrammata",
                id=m.id,
                tentativo=tentativo,
                prossimo=prossimo.isoformat(),
                motivo=esito.messaggio,
            )
        else:
            motivo = (
                esito.messaggio
                if not esito.ritentabile
                else (f"tentativi esauriti ({tentativo}): {esito.messaggio}")
            )
            await coda.segna_non_partita(m.id, tentativo, motivo, adesso)
            await pubblica_dlq(_evento_dlq(m, tentativo, motivo, adesso))
            conteggi["non_partite"] += 1
            logger.warning(
                "notifica_non_partita_dlq",
                id=m.id,
                tentativi=tentativo,
                motivo=motivo,
                destinatario=maschera(m.destinatario),
            )
    return conteggi


def apri_segreti(m: MessaggioUscita, apritore: Apritore | None
                 ) -> tuple[str, str | None, str | None]:
    """R12a (VD-C-152): (testo, html, problema). I segreti sigillati si aprono
    SOLO qui, in memoria, al momento dell'invio, con il contesto della riga
    (ente, oggetto, destinatario): una riga con il destinatario cambiato non si
    apre e il messaggio non parte. Senza chiave: nessun invio (mai in chiaro)."""
    testo = str(m.payload.get("testo") or "")
    html = m.payload.get("html")
    segreti = m.payload.get("segreti")
    if not segreti:
        return testo, html, None
    if apritore is None:
        return testo, html, "segreti sigillati ma POSTA_CHIAVE_PRIVATA non configurata"
    if not isinstance(segreti, dict):
        return testo, html, "segreti malformati"
    contesto = contesto_posta(str(m.tenant_id), str(m.payload.get("oggetto_id") or ""),
                              m.destinatario)
    for nome, sigillo in segreti.items():
        try:
            chiaro = apritore.apri(str(sigillo), contesto)
        except ErroreSigillo as exc:
            return testo, html, f"segreto {nome!r} non apribile: {exc}"
        testo = testo.replace(SEGNAPOSTO % nome, chiaro)
        if isinstance(html, str):
            html = html.replace(SEGNAPOSTO % nome, chiaro)
    return testo, html, None


def _evento_dlq(m: MessaggioUscita, tentativi: int, motivo: str, adesso: datetime) -> dict:
    """Evento DLQ: riferimenti e motivo, nessun contenuto del messaggio (REGOLE §19.9)."""
    return {
        "notifica_id": m.id,
        "tenant_id": m.tenant_id,
        "tipo": m.tipo,
        "canale": m.canale,
        "oggetto_id": m.payload.get("oggetto_id"),
        "tentativi": tentativi,
        "errore": motivo,
        "fallita_il": adesso.isoformat(),
    }
