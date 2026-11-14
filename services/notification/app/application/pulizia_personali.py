# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/pulizia_personali.py — retention delle notifiche personali (R14, VD-C-172).

Le voci personali della campanella (``notifiche_personali``, R13b: modifiche delle proprie
credenziali) crescevano senza limite: nessun ruolo ha DELETE e il trigger lo vietava. Ora il
lavoratore della posta, una volta ogni ``PULIZIA_PERSONALI_INTERVALLO_SECONDI``, cancella le
voci più vecchie di ``NOTIFICHE_PERSONALI_GIORNI`` (parametro del motore, solo conservatore)
con la funzione del database ``vd_pulisci_notifiche_personali`` (migrazione 056), che non
scende mai sotto 30 giorni e tratta al più 10.000 righe per chiamata.

Lavoro periodico come quelli del ciclo di vita (REGOLE §31, R09 (1), R12a (3)):

  - lease PER LAVORO in tabella (``lavori_periodici``, riga ``notifiche_personali.pulizia``),
    mai un lock di sessione: una sola replica alla volta, e solo quando il lavoro è dovuto;
  - ogni presa alza la ``generazione`` (FENCING TOKEN) e ogni lotto di cancellazioni
    riverifica, nella STESSA transazione, che il lease sia ancora nostro con quella
    generazione (riga bloccata ``FOR UPDATE`` fino al commit): un processo che ha perso il
    lease non cancella nulla;
  - lotti di ``PULIZIA_PERSONALI_LOTTO`` righe, al più ``PULIZIA_PERSONALI_GIRI_MASSIMI``
    lotti per giro; un errore si registra in ``ultimo_esito`` e il lavoro si riprova al
    controllo successivo (``ultimo_giro`` non avanza).

Le voci personali non sono documenti conservati: non stanno nel WORM, non hanno legal hold e
la funzione non tocca altre tabelle.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from libs.shared.db.ambito import ambito_conservatore

logger = structlog.get_logger(__name__)

NOME = "notifiche_personali.pulizia"
MOTIVO_AMBITO = "lavoratore della posta: pulizia delle notifiche personali di tutti gli enti"


class LeasePerso(RuntimeError):
    """Il lease del lavoro non è più nostro (altra replica, scaduto): nessuna scrittura."""


async def giorni_retention() -> int:
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import NOTIFICHE_PERSONALI_GIORNI

    return int((await parametri([NOTIFICHE_PERSONALI_GIORNI], None))[NOTIFICHE_PERSONALI_GIORNI])


async def prendi(detentore: str, adesso: datetime, *, intervallo: timedelta,
                 durata: timedelta) -> int | None:
    """Lease del lavoro se è dovuto e libero; restituisce la NUOVA generazione."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.lavori_periodici (nome) VALUES (:n) ON CONFLICT (nome) DO NOTHING"),
            {"n": NOME})
        preso = (await session.execute(text(
            "UPDATE public.lavori_periodici SET lease_detentore = :d, lease_scade_il = :scade, "
            "generazione = generazione + 1, aggiornato_il = :adesso "
            "WHERE nome = :n AND (lease_scade_il IS NULL OR lease_scade_il < :adesso) "
            "AND (ultimo_giro IS NULL OR ultimo_giro <= :dovuto) RETURNING generazione"),
            {"n": NOME, "d": detentore[:200], "adesso": adesso, "scade": adesso + durata,
             "dovuto": adesso - intervallo})).first()
    return int(preso[0]) if preso is not None else None


async def verifica_fencing(session: Any, detentore: str, generazione: int) -> None:
    """Nella transazione della scrittura: lease nostro, stessa generazione, non scaduto
    (riga bloccata fino al commit). Altrimenti ``LeasePerso``."""
    riga = (await session.execute(text(
        "SELECT 1 FROM public.lavori_periodici WHERE nome = :n AND lease_detentore = :d "
        "AND generazione = :g AND lease_scade_il > now() FOR UPDATE"),
        {"n": NOME, "d": detentore[:200], "g": generazione})).first()
    if riga is None:
        raise LeasePerso(f"lease di {NOME} perso (generazione {generazione})")


async def cancella_lotto(detentore: str, generazione: int, *, giorni: int, lotto: int) -> int:
    """Un lotto di cancellazioni nella transazione della verifica del lease."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await verifica_fencing(session, detentore, generazione)
        return int((await session.execute(text(
            "SELECT public.vd_pulisci_notifiche_personali(:g, :l)"),
            {"g": giorni, "l": lotto})).scalar_one())


async def concludi(detentore: str, generazione: int, adesso: datetime,
                   esito: Mapping[str, Any], *, giro_fatto: bool) -> None:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.lavori_periodici SET lease_detentore = NULL, lease_scade_il = NULL, "
            "aggiornato_il = :adesso, ultimo_esito = CAST(:e AS jsonb), ultimo_giro = CASE "
            "WHEN CAST(:fatto AS boolean) THEN :adesso ELSE ultimo_giro END "
            "WHERE nome = :n AND lease_detentore = :d AND generazione = :g"),
            {"n": NOME, "d": detentore[:200], "g": generazione, "adesso": adesso,
             "fatto": giro_fatto, "e": json.dumps(dict(esito), default=str)})


async def giro(detentore: str, adesso: datetime, *, intervallo: timedelta, durata: timedelta,
               lotto: int, giri_massimi: int) -> dict[str, Any] | None:
    """Un giro della pulizia (``None`` se non dovuto o preso da un'altra replica)."""
    with ambito_conservatore(MOTIVO_AMBITO, registra=False):
        generazione = await prendi(detentore, adesso, intervallo=intervallo, durata=durata)
        if generazione is None:
            return None
        cancellate = 0
        try:
            giorni = await giorni_retention()
            for _ in range(giri_massimi):
                n = await cancella_lotto(detentore, generazione, giorni=giorni, lotto=lotto)
                cancellate += n
                if n < lotto:
                    break
        except LeasePerso:
            logger.warning("pulizia_notifiche_personali_lease_perso", cancellate=cancellate)
            return {"lease_perso": True, "cancellate": cancellate}
        except Exception as exc:  # noqa: BLE001 — registrato, si riprova al prossimo controllo
            logger.error("pulizia_notifiche_personali_errore", errore=type(exc).__name__)
            esito = {"errore": type(exc).__name__, "messaggio": str(exc)[:300],
                     "cancellate": cancellate}
            await concludi(detentore, generazione, adesso, esito, giro_fatto=False)
            return esito
        esito = {"cancellate": cancellate, "giorni": giorni}
        await concludi(detentore, generazione, adesso, esito, giro_fatto=True)
    if cancellate:
        logger.info("notifiche_personali_pulite", cancellate=cancellate, giorni=giorni)
    return esito


__all__ = ["NOME", "LeasePerso", "cancella_lotto", "concludi", "giorni_retention", "giro",
           "prendi", "verifica_fencing"]
