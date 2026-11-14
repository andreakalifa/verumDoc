# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/idempotenza.py — Idempotency-Key per le richieste che creano
una risorsa fuori dal versamento (R04 servizi, VD-C-106).

Riferimenti: REGOLE_SVILUPPO §12.1 (stessa semantica del versamento: stessa
chiave e stessa richiesta → stessa risposta, stessa chiave con contenuto
diverso → 409), §12.6 («controlla poi crea» con vincolo unico), §18.

Usata da ``POST /api/v2/distribution/richiedi`` (ambito ``distribution.pdd``)
e ``POST /api/v1/webhook`` (ambito ``notification.webhook``). Tabella
``public.richieste_idempotenti`` (migrazione 20261023_029), chiave primaria
(ente, ambito, chiave): due richieste concorrenti con la stessa chiave non
possono creare due risorse.

Ciclo di una richiesta con chiave:

  1. ``prenota`` (transazione propria, confermata subito): riga IN_CORSO con
     l'impronta della richiesta. Se la riga c'è già:
       - scaduta (``scade_il`` passato) → si riusa come nuova;
       - impronta diversa → ``IDEM-409``;
       - COMPLETATA → ``Prenotazione.ripetuta`` con la risorsa: il chiamante
         risponde con la risorsa esistente (200), senza rifare l'operazione;
       - IN_CORSO da meno di ``IDEMPOTENZA_IN_CORSO_MAX_SECONDI`` → 409
         ``IDEM-409-IN-CORSO`` con Retry-After (ritentabile);
       - IN_CORSO da più tempo (processo morto a metà) → la si riprende.
  2. operazione; poi ``completa(session, …)`` NELLA STESSA transazione che
     scrive la risorsa, quando il chiamante può (distribution): risorsa e
     completamento si confermano insieme. Altrimenti ``completa`` apre una
     transazione propria subito dopo (notification: finestra residua dichiarata
     nel rapporto R04).
  3. se l'operazione fallisce: ``rilascia`` elimina la prenotazione IN_CORSO,
     così la stessa chiave si può ritentare.

Le righe scadute si eliminano a piccoli lotti a ogni prenotazione
(``IDEMPOTENZA_PULIZIA_LOTTO``) sull'indice ``idx_richieste_idempotenti_scadenza``.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi

logger = structlog.get_logger(__name__)

#: Ambiti delle chiavi (uno per operazione): la stessa chiave in due ambiti non collide.
AMBITO_PDD = "distribution.pdd"
AMBITO_WEBHOOK = "notification.webhook"

#: Forma della chiave: la stessa del versamento (gateway, VRS-122) e dell'SDK.
PATTERN_CHIAVE = re.compile(r"^[A-Za-z0-9._:\-]{8,200}$")

IN_CORSO = "IN_CORSO"
COMPLETATA = "COMPLETATA"


class ImpostazioniIdempotenza(BaseSettings):
    """Variabili ``IDEMPOTENZA_*`` (predefiniti uguali in ogni servizio che la usa)."""

    model_config = SettingsConfigDict(env_prefix="IDEMPOTENZA_", case_sensitive=False,
                                      extra="ignore")

    #: Validità di una chiave: entro questo tempo la stessa chiave dà la stessa risorsa.
    durata_ore: int = Field(24, ge=1, le=24 * 30)
    #: Dopo questo tempo una prenotazione IN_CORSO si considera abbandonata.
    in_corso_max_secondi: int = Field(120, ge=10, le=3600)
    #: Righe scadute eliminate a ogni prenotazione.
    pulizia_lotto: int = Field(50, ge=0, le=1000)


IMPOSTAZIONI = ImpostazioniIdempotenza()


@dataclass(frozen=True)
class Prenotazione:
    """Esito di ``prenota``: nuova (eseguire l'operazione) o ripetuta (risorsa esistente)."""

    tenant_id: str
    ambito: str
    chiave: str
    impronta: str
    ripetuta: bool
    risorsa_id: str | None = None


def valida_chiave(chiave: str | None) -> str | None:
    """La chiave ben formata, ``None`` se assente; ``IDEM-400`` se malformata."""
    if chiave is None:
        return None
    if not PATTERN_CHIAVE.match(chiave):
        raise ErroreApi("IDEM-400")
    return chiave


def impronta_richiesta(dati: Any) -> str:
    """SHA-256 (hex) del JSON canonico della richiesta (chiavi ordinate, UTF-8)."""
    testo = json.dumps(dati, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       default=str)
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


def _ente(tenant_id: Any) -> uuid.UUID:
    return uuid.UUID(str(tenant_id))


async def prenota(tenant_id: Any, ambito: str, chiave: str, impronta: str, *,
                  adesso: datetime, imp: ImpostazioniIdempotenza | None = None) -> Prenotazione:
    """Prenota la chiave o restituisce la risorsa già creata (vedi docstring del modulo)."""
    imp = imp or IMPOSTAZIONI
    ente = _ente(tenant_id)
    scade = adesso + timedelta(hours=imp.durata_ore)
    base = {"t": ente, "a": ambito, "k": chiave}
    async with get_async_session() as session:
        if imp.pulizia_lotto:
            await session.execute(text(
                "DELETE FROM public.richieste_idempotenti WHERE ctid IN ("
                " SELECT ctid FROM public.richieste_idempotenti WHERE scade_il < :ora "
                " ORDER BY scade_il LIMIT :n FOR UPDATE SKIP LOCKED)"
            ), {"ora": adesso, "n": imp.pulizia_lotto})
        nuova = (await session.execute(text(
            "INSERT INTO public.richieste_idempotenti (tenant_id, ambito, chiave, impronta, "
            " stato, creata_il, aggiornata_il, scade_il) "
            "VALUES (:t, :a, :k, :i, 'IN_CORSO', :ora, :ora, :scade) "
            "ON CONFLICT (tenant_id, ambito, chiave) DO NOTHING RETURNING chiave"
        ), {**base, "i": impronta, "ora": adesso, "scade": scade})).first()
        if nuova is not None:
            return Prenotazione(str(ente), ambito, chiave, impronta, ripetuta=False)
        riga = (await session.execute(text(
            "SELECT impronta, stato, risorsa_id, aggiornata_il, scade_il "
            "FROM public.richieste_idempotenti "
            "WHERE tenant_id = :t AND ambito = :a AND chiave = :k FOR UPDATE"
        ), base)).mappings().one()
        if riga["scade_il"] <= adesso:
            await session.execute(text(
                "UPDATE public.richieste_idempotenti SET impronta = :i, stato = 'IN_CORSO', "
                " risorsa_id = NULL, creata_il = :ora, aggiornata_il = :ora, scade_il = :scade "
                "WHERE tenant_id = :t AND ambito = :a AND chiave = :k"
            ), {**base, "i": impronta, "ora": adesso, "scade": scade})
            return Prenotazione(str(ente), ambito, chiave, impronta, ripetuta=False)
        if riga["impronta"] != impronta:
            raise ErroreApi("IDEM-409", dettagli={"ambito": ambito})
        if riga["stato"] == COMPLETATA:
            return Prenotazione(str(ente), ambito, chiave, impronta, ripetuta=True,
                                risorsa_id=riga["risorsa_id"])
        if (adesso - riga["aggiornata_il"]).total_seconds() < imp.in_corso_max_secondi:
            raise ErroreApi("IDEM-409-IN-CORSO")
        # Prenotazione abbandonata (processo terminato a metà): si riprende
        await session.execute(text(
            "UPDATE public.richieste_idempotenti SET aggiornata_il = :ora "
            "WHERE tenant_id = :t AND ambito = :a AND chiave = :k"
        ), {**base, "ora": adesso})
        logger.warning("idempotenza_prenotazione_ripresa", ambito=ambito, tenant_id=str(ente))
        return Prenotazione(str(ente), ambito, chiave, impronta, ripetuta=False,
                            risorsa_id=riga["risorsa_id"])


async def completa(session: Any | None, prenotazione: Prenotazione, risorsa_id: str, *,
                   adesso: datetime) -> None:
    """Segna la chiave COMPLETATA con la risorsa creata. Con ``session`` nella
    transazione del chiamante (risorsa e chiave insieme), altrimenti in una propria."""
    parametri = {"t": _ente(prenotazione.tenant_id), "a": prenotazione.ambito,
                 "k": prenotazione.chiave, "r": str(risorsa_id), "ora": adesso}
    istruzione = text(
        "UPDATE public.richieste_idempotenti SET stato = 'COMPLETATA', risorsa_id = :r, "
        " aggiornata_il = :ora WHERE tenant_id = :t AND ambito = :a AND chiave = :k"
    )
    if session is not None:
        await session.execute(istruzione, parametri)
        return
    async with get_async_session() as propria:
        await propria.execute(istruzione, parametri)


async def rilascia(prenotazione: Prenotazione) -> None:
    """Elimina una prenotazione IN_CORSO dopo un'operazione fallita (chiave ritentabile).
    Non solleva: un errore resta nel log e la prenotazione scade da sola."""
    try:
        async with get_async_session() as session:
            await session.execute(text(
                "DELETE FROM public.richieste_idempotenti "
                "WHERE tenant_id = :t AND ambito = :a AND chiave = :k AND stato = 'IN_CORSO'"
            ), {"t": _ente(prenotazione.tenant_id), "a": prenotazione.ambito,
                "k": prenotazione.chiave})
    except Exception as exc:  # noqa: BLE001 — la prenotazione scade da sola (in_corso_max)
        logger.warning("idempotenza_rilascio_non_riuscito", ambito=prenotazione.ambito,
                       errore=type(exc).__name__)
