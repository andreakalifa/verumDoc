# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica completa ASINCRONA della catena (g4-chiusura, decisione del coordinatore).

Prima la richiesta HTTP restava aperta per tutta la verifica (fino a 300 s),
oltre il timeout dell'ALB (60 s). Ora:

- ``avvia_verifica`` prende un lock consultivo di SESSIONE su una connessione
  dedicata (una sola verifica alla volta, anche fra repliche), registra l'avvio
  in ``audit_global`` (evento ``hashchain.verifica_avviata``, stato IN_CORSO) e
  lancia l'esecuzione in un task di fondo; la risposta (202) parte subito.
- Il task esegue ``esegui_verifica_completa`` con il limite
  HASHCHAIN_VERIFICA_TIMEOUT_SECONDS. Esiti finali (evento
  ``hashchain.verifica_completa``, id riga = verifica_id):
    COMPLETATA  esito INTEGRA / COMPROMESSA / NESSUN_BLOCCO (dalla verifica);
    INTERROTTA  tempo massimo superato o servizio arrestato/riavviato;
    FALLITA     errore durante la verifica (testo dell'eccezione solo nel log).
  Il lock si rilascia solo DOPO la registrazione dell'esito finale.
- Una verifica IN_CORSO senza lock attivo (processo morto, esito non
  scrivibile) è orfana: si chiude come INTERROTTA all'avvio del servizio e alla
  prima lettura del suo stato. La verità non resta appesa.

audit_global è append-only: ogni passaggio è una riga nuova, mai un UPDATE.

R04 correzioni (rev4 rilievo 6): il lock è di SESSIONE e resta tenuto per tutta la
verifica, quindi la connessione dedicata non deve passare da PgBouncer in
modalità transaction: si apre su ``DATABASE_URL_DIRETTA`` (``engine_diretto``),
o sull'URL del servizio, che per la API della hashchain è diretto.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.services.verifica_completa import (
    CONTROLLI,
    EVENTO_VERIFICA,
    VerificaGiaInCorso,
    esegui_verifica_completa,
)
from libs.shared.db import engine_diretto, get_async_session

logger = structlog.get_logger(__name__)

EVENTO_AVVIO = "hashchain.verifica_avviata"
# Lock di SESSIONE tenuto per tutta l'esecuzione (diverso da quello transazionale
# della verifica stessa, che resta per chi la chiama direttamente).
_CHIAVE_ESECUZIONE = "verumdoc.hashchain.verifica_in_esecuzione"

IN_CORSO = "IN_CORSO"
COMPLETATA = "COMPLETATA"
FALLITA = "FALLITA"
INTERROTTA = "INTERROTTA"
STATI = (IN_CORSO, COMPLETATA, FALLITA, INTERROTTA)

_attivita: set[asyncio.Task] = set()


class VerificaInCorso(Exception):  # noqa: N818 — nome di dominio
    """Un'altra verifica è in esecuzione: ``verifica_id`` è quella in corso."""

    def __init__(self, verifica_id: str | None) -> None:
        super().__init__(verifica_id or "")
        self.verifica_id = verifica_id


def _uuid_o_none(valore: object) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


async def _prova_lock(conn) -> bool:
    preso = (await conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                                {"k": _CHIAVE_ESECUZIONE})).scalar_one()
    await conn.commit()
    return bool(preso)


async def _rilascia(conn) -> None:
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"),
                           {"k": _CHIAVE_ESECUZIONE})
        await conn.commit()
    finally:
        await conn.close()


async def _in_corso_senza_esito(session) -> list[dict[str, Any]]:
    """Avvii senza riga finale, dal più recente."""
    righe = (await session.execute(text(
        "SELECT a.dettagli FROM public.audit_global a WHERE a.evento = :avvio "
        "AND NOT EXISTS (SELECT 1 FROM public.audit_global f WHERE f.evento = :fine "
        "AND f.dettagli->>'verifica_id' = a.dettagli->>'verifica_id') "
        "ORDER BY a.created_at DESC"
    ), {"avvio": EVENTO_AVVIO, "fine": EVENTO_VERIFICA})).all()
    return [dict(r[0]) for r in righe if isinstance(r[0], dict)]


async def _registra_chiusura(avvio: dict[str, Any], stato: str, messaggio: str) -> None:
    """Riga finale FALLITA/INTERROTTA (nessun esito sulla catena)."""
    fine = datetime.now(UTC)
    dettagli = {
        "verifica_id": avvio["verifica_id"], "stato": stato, "esito": None, "integra": None,
        "messaggio": messaggio, "blocchi_verificati": None, "ultimo_blocco_verificato": None,
        "blocchi_senza_firma_qualificata": None, "iniziata_il": avvio.get("avviata_il"),
        "conclusa_il": fine.isoformat(), "durata_ms": None,
        "avviata_da": avvio.get("avviata_da"), "ruolo": avvio.get("ruolo"),
        "controlli": list(CONTROLLI),
    }
    async with get_async_session() as session:
        await session.execute(text(
            # Revisione 2 di R05 (S7): created_at dal database (now()), mai
            # dall'orologio dell'applicazione (conclusa_il resta nei dettagli)
            "INSERT INTO public.audit_global (id, user_id, evento, dettagli, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:utente AS uuid), :evento, "
            "CAST(:dettagli AS jsonb), now())"
        ), {"id": avvio["verifica_id"], "utente": _uuid_o_none(avvio.get("avviata_da")),
            "evento": EVENTO_VERIFICA, "dettagli": json.dumps(dettagli)})
    logger.warning("verifica_completa_chiusa", verifica_id=avvio["verifica_id"], stato=stato)


async def avvia_verifica(*, avviata_da: str | None, ruolo: str | None,
                         timeout_secondi: float) -> dict[str, Any]:
    """Registra l'avvio, lancia il task di fondo e restituisce subito lo stato
    IN_CORSO. VerificaInCorso se un'altra è in esecuzione."""
    conn = await engine_diretto().connect()
    preso = False
    try:
        preso = await _prova_lock(conn)
        if not preso:
            async with get_async_session() as session:
                aperte = await _in_corso_senza_esito(session)
            raise VerificaInCorso(aperte[0]["verifica_id"] if aperte else None)
        avvio = {"verifica_id": str(uuid.uuid4()), "stato": IN_CORSO,
                 "avviata_il": datetime.now(UTC).isoformat(),
                 "avviata_da": avviata_da, "ruolo": ruolo}
        async with get_async_session() as session:
            await session.execute(text(
                "INSERT INTO public.audit_global (id, user_id, evento, dettagli, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:utente AS uuid), :evento, "
                "CAST(:dettagli AS jsonb), now())"
            ), {"id": str(uuid.uuid4()), "utente": _uuid_o_none(avviata_da),
                "evento": EVENTO_AVVIO, "dettagli": json.dumps(avvio)})
    except BaseException:
        if preso:
            await asyncio.shield(_rilascia(conn))
        else:
            await conn.close()
        raise
    task = asyncio.create_task(_esegui_in_fondo(conn, avvio, timeout_secondi))
    _attivita.add(task)
    task.add_done_callback(_attivita.discard)
    logger.info("verifica_completa_avviata", verifica_id=avvio["verifica_id"])
    return {"verifica_id": avvio["verifica_id"], "stato": IN_CORSO,
            "avviata_il": avvio["avviata_il"]}


async def _esegui_in_fondo(conn, avvio: dict[str, Any], timeout_secondi: float) -> None:
    try:
        try:
            await asyncio.wait_for(
                esegui_verifica_completa(
                    avviata_da=avvio["avviata_da"], ruolo=avvio["ruolo"],
                    verifica_id=avvio["verifica_id"],
                    iniziata_il=datetime.fromisoformat(avvio["avviata_il"])),
                timeout=timeout_secondi)
        except TimeoutError:
            await _registra_chiusura(
                avvio, INTERROTTA,
                f"Tempo massimo di {timeout_secondi:g} s superato: verifica interrotta, "
                "nessun esito sulla catena")
        except asyncio.CancelledError:
            await asyncio.shield(_registra_chiusura(
                avvio, INTERROTTA, "Servizio arrestato durante la verifica: nessun esito"))
            raise
        except VerificaGiaInCorso:
            await _registra_chiusura(avvio, FALLITA,
                                     "Un'altra verifica teneva occupata la catena")
        except Exception as exc:  # noqa: BLE001 — esito FALLITA, dettagli solo nel log
            logger.error("verifica_completa_errore", verifica_id=avvio["verifica_id"],
                         tipo=type(exc).__name__, errore=str(exc))
            await _registra_chiusura(
                avvio, FALLITA,
                f"Errore durante la verifica (id {avvio['verifica_id']}): nessun esito")
    except Exception as exc:  # noqa: BLE001 — esito non scrivibile: resterà orfana
        logger.error("verifica_completa_esito_non_registrato",
                     verifica_id=avvio["verifica_id"], tipo=type(exc).__name__, errore=str(exc))
    finally:
        await asyncio.shield(_rilascia(conn))


async def chiudi_verifiche_orfane() -> int:
    """Chiude come INTERROTTA le verifiche IN_CORSO che nessun processo esegue
    (lock libero). Restituisce quante ne ha chiuse."""
    conn = await engine_diretto().connect()
    if not await _prova_lock(conn):
        await conn.close()
        return 0  # una verifica è davvero in esecuzione
    try:
        async with get_async_session() as session:
            aperte = await _in_corso_senza_esito(session)
        for avvio in aperte:
            await _registra_chiusura(
                avvio, INTERROTTA,
                "Verifica interrotta: il servizio è stato riavviato o arrestato "
                "durante l'esecuzione; nessun esito")
        return len(aperte)
    finally:
        await _rilascia(conn)


def _risposta(avvio: dict[str, Any] | None, fine: dict[str, Any] | None) -> dict[str, Any]:
    base = fine or {}
    origine = avvio or {}
    return {
        "verifica_id": (fine or avvio or {}).get("verifica_id"),
        "stato": base.get("stato", COMPLETATA) if fine else IN_CORSO,
        "avviata_il": origine.get("avviata_il") or base.get("iniziata_il"),
        "conclusa_il": base.get("conclusa_il"),
        "avviata_da": origine.get("avviata_da", base.get("avviata_da")),
        "ruolo": origine.get("ruolo", base.get("ruolo")),
        "esito": base.get("esito"),
        "integra": base.get("integra"),
        "messaggio": base.get("messaggio"),
        "blocchi_verificati": base.get("blocchi_verificati"),
        "ultimo_blocco_verificato": base.get("ultimo_blocco_verificato"),
        "blocchi_senza_firma_qualificata": base.get("blocchi_senza_firma_qualificata"),
        "durata_ms": base.get("durata_ms"),
    }


async def _leggi(verifica_id: str) -> tuple[dict | None, dict | None]:
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT evento, dettagli FROM public.audit_global "
            "WHERE evento IN (:avvio, :fine) AND dettagli->>'verifica_id' = :id"
        ), {"avvio": EVENTO_AVVIO, "fine": EVENTO_VERIFICA, "id": verifica_id})).all()
    avvio = next((dict(d) for e, d in righe if e == EVENTO_AVVIO), None)
    fine = next((dict(d) for e, d in righe if e == EVENTO_VERIFICA), None)
    return avvio, fine


async def stato_verifica(verifica_id: str) -> dict[str, Any] | None:
    """Stato di una verifica (None se sconosciuta). Una IN_CORSO orfana si
    chiude qui come INTERROTTA prima di rispondere."""
    avvio, fine = await _leggi(verifica_id)
    if avvio is None and fine is None:
        return None
    if fine is None and await chiudi_verifiche_orfane():
        avvio, fine = await _leggi(verifica_id)
    return _risposta(avvio, fine)


async def arresta_verifiche() -> None:
    """Allo spegnimento: annulla i task (ognuno registra INTERROTTA)."""
    for task in list(_attivita):
        task.cancel()
    for task in list(_attivita):
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.error("verifica_completa_arresto", tipo=type(exc).__name__)


async def attendi_verifiche() -> None:
    """Solo per i test: attende la fine dei task in corso."""
    while _attivita:
        await asyncio.gather(*list(_attivita), return_exceptions=True)
