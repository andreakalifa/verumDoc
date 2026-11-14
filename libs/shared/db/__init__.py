# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/shared/db/__init__.py
Riferimento piano: F0.4 — Schema Database
Aggiornato: R03 (20/10/2026) — schema solo da Alembic

ARCHITETTURA DB (leggere prima di modificare questo file):
  - Schema unico: public.* (decisione VD-P-00, REGOLE §16.1)
  - Isolamento multi-tenant: colonna tenant_id
  - Nessuno schema per-tenant PostgreSQL — design abbandonato (v4.2)
  - Schema creato SOLO dalle migrazioni Alembic, eseguite dall'entrypoint del
    gateway (`alembic upgrade head`); mai create_all() a runtime (§16.2)
  - ORM SQLAlchemy 2.0 per CRUD; raw SQL solo per aggregazioni e DDL

Funzioni esposte:
  - init_db(url, database_url_lettura=None): inizializza engine e session factory
    (primario e, se configurata, replica di sola lettura)
  - close_db(): chiude i pool di connessioni
  - get_engine(): restituisce l'engine (o None se non inizializzato)
  - get_async_session(): context manager per sessioni async sul primario
  - get_async_session_lettura(): sessione di SOLA LETTURA (R04, piano §4.2 e
    §6.4): sulla replica indicata da ``DATABASE_URL_LETTURA`` se configurata,
    altrimenti sul primario con la transazione in sola lettura. Per cruscotti,
    ricerca, elenchi e statistiche: dati con il ritardo della replica (di norma
    sotto il secondo), MAI per letture che devono vedere una scrittura appena
    fatta (stato del PdV subito dopo il versamento, controlli prima di scrivere).
  - lettura_su_replica(): True se le letture vanno su una replica distinta
  - engine_diretto(): engine su ``DATABASE_URL_DIRETTA`` (R04 correzioni, rev4
    rilievo 6) per il codice che ha bisogno di stato di SESSIONE (lock
    consultivi di sessione tenuti a lungo): mai attraverso PgBouncer in modalità
    transaction. Senza la variabile: l'engine del servizio (che allora DEVE
    essere diretto: è così per la API della hashchain);
  - engine_manutenzione(): engine su ``DATABASE_URL_MANUTENZIONE`` (ruolo
    proprietario delle tabelle, connessione diretta) per la manutenzione delle
    partizioni; senza la variabile: quello del servizio (sviluppo e test, dove
    il ruolo è uno solo). Vedi ``libs/shared/db/partizioni.py``.
  - DBNonInizializzato: eccezione sollevata se init_db() non è stato chiamato

R05 (agente r05-sicurezza):
  - VD-C-080: ogni engine creato qui applica a OGNI transazione l'ambito d'ente
    della Row Level Security (``SET LOCAL app.ente`` / ``app.ambito``) con
    ``libs.shared.db.ambito.installa_ambito``: è l'unico punto che lo scrive.
  - VD-C-113: ``init_db`` fuori da development/test rifiuta di partire con un
    ruolo superutente o proprietario delle tabelle (``libs.shared.db.guardia``);
    ``uso="manutenzione"`` per il processo delle partizioni.
    Revisione R05 (R13): la stessa guardia vale per TUTTE le URL del processo,
    controllate all'avvio da ``init_db``: ``DATABASE_URL_LETTURA`` (replica) e
    ``DATABASE_URL_DIRETTA`` come servizio; ``DATABASE_URL_MANUTENZIONE`` con
    ``uso="manutenzione"`` (l'unica eccezione dichiarata: proprietario delle
    tabelle partizionate operative, mai superutente).
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from libs.shared.db.ambito import installa_ambito
from libs.shared.db.guardia import Uso, verifica_ruolo_database

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory = None
# R04 (piano §4.2): replica di sola lettura facoltativa
_engine_lettura: AsyncEngine | None = None
_session_factory_lettura = None

#: Variabile d'ambiente con l'URL della replica di sola lettura (facoltativa).
VARIABILE_LETTURA = "DATABASE_URL_LETTURA"
#: R04 correzioni: connessione diretta (senza PgBouncer) e ruolo della manutenzione.
VARIABILE_DIRETTA = "DATABASE_URL_DIRETTA"
VARIABILE_MANUTENZIONE = "DATABASE_URL_MANUTENZIONE"
_engine_aggiuntivi: dict[str, AsyncEngine] = {}
#: R13: URL aggiuntive e uso della guardia del ruolo (la manutenzione è l'unica
#: che può essere proprietaria delle tabelle partizionate operative)
_USI_AGGIUNTIVI: tuple[tuple[str, Uso], ...] = (
    (VARIABILE_DIRETTA, "servizio"),
    (VARIABILE_MANUTENZIONE, "manutenzione"),
)


class DBNonInizializzato(RuntimeError):
    """init_db() non è stato chiamato in questo processo.

    Eccezione dedicata: chi deve distinguere «nessun database configurato»
    da un errore del database (es. la revoca dei token) intercetta questa
    classe, mai il testo del messaggio. Resta una RuntimeError per
    compatibilità con chi già intercettava RuntimeError.
    """


def _url_lettura(esplicito: str | None) -> str | None:
    """URL della replica: argomento esplicito, altrimenti ``DATABASE_URL_LETTURA``;
    vuoto = nessuna replica (letture sul primario)."""
    valore = esplicito if esplicito is not None else os.environ.get(VARIABILE_LETTURA, "")
    valore = (valore or "").strip()
    return valore or None


async def init_db(database_url: str, *, database_url_lettura: str | None = None,
                  uso: Uso = "servizio") -> None:
    """
    Inizializza il pool di connessioni PostgreSQL async.

    Chiamato nel lifespan di ogni microservizio:
        async with lifespan(app):
            await init_db(settings.database_url)

    Il database_url deve essere nel formato:
        postgresql+asyncpg://user:pass@host:port/dbname

    ``database_url_lettura`` (o la variabile ``DATABASE_URL_LETTURA``) indica
    una replica di sola lettura: le sue connessioni aprono ogni transazione in
    sola lettura (``default_transaction_read_only``), quindi un errore di
    programmazione che scrivesse lì fallisce invece di divergere dal primario.

    VD-C-113: fuori da development/test il ruolo connesso non può essere
    superutente né proprietario delle tabelle (``RuoloDatabaseNonAmmesso``).
    """
    global _engine, _session_factory, _engine_lettura, _session_factory_lettura
    engine = create_async_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False,
    )
    try:
        await verifica_ruolo_database(engine, uso=uso)
    except BaseException:
        await engine.dispose()
        raise
    installa_ambito(engine)
    _engine = engine
    _session_factory = sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )
    url_lettura = _url_lettura(database_url_lettura)
    try:
        if url_lettura:
            argomenti: dict = {}
            if "+asyncpg" in url_lettura:
                argomenti = {"server_settings": {"default_transaction_read_only": "on"}}
            lettura = create_async_engine(
                url_lettura,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=300,
                echo=False,
                connect_args=argomenti,
            )
            try:
                # R13: la replica è letta con la RLS come il primario
                await verifica_ruolo_database(lettura, uso="servizio")
            except BaseException:
                await lettura.dispose()
                raise
            installa_ambito(lettura)
            _engine_lettura = lettura
            _session_factory_lettura = sessionmaker(
                _engine_lettura, class_=AsyncSession, expire_on_commit=False
            )
        # R13: engine aggiuntivi dichiarati nell'ambiente, controllati all'avvio
        for variabile, uso_aggiuntivo in _USI_AGGIUNTIVI:
            if (os.environ.get(variabile) or "").strip():
                await verifica_ruolo_database(_engine_da_variabile(variabile),
                                              uso=uso_aggiuntivo)
    except BaseException:
        await close_db()
        raise
    logger.info("db_initialized", replica_lettura=url_lettura is not None)


async def close_db() -> None:
    """Chiude i pool di connessioni. Chiamato nello shutdown del lifespan."""
    global _engine, _session_factory, _engine_lettura, _session_factory_lettura
    for aggiuntivo in list(_engine_aggiuntivi.values()):
        await aggiuntivo.dispose()
    _engine_aggiuntivi.clear()
    if _engine_lettura is not None:
        await _engine_lettura.dispose()
        _engine_lettura = None
        _session_factory_lettura = None
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("db_closed")


def lettura_su_replica() -> bool:
    """True se ``get_async_session_lettura`` usa una replica distinta dal primario."""
    return _session_factory_lettura is not None


def get_engine() -> AsyncEngine:
    """
    Restituisce l'engine SQLAlchemy.

    Raises:
        DBNonInizializzato (RuntimeError): se init_db() non è stato ancora chiamato.
    """
    if _engine is None:
        raise DBNonInizializzato(
            "DB non inizializzato — chiama init_db() nel lifespan del servizio"
        )
    return _engine


def _engine_da_variabile(variabile: str) -> AsyncEngine:
    """Engine piccolo (1-2 connessioni) su ``variabile`` se impostata, altrimenti
    l'engine del servizio."""
    url = (os.environ.get(variabile) or "").strip()
    if not url:
        return get_engine()
    engine = _engine_aggiuntivi.get(variabile)
    if engine is None:
        engine = _engine_aggiuntivi[variabile] = create_async_engine(
            url, pool_size=1, max_overflow=1, pool_pre_ping=True, pool_recycle=300)
        installa_ambito(engine)
    return engine


def engine_diretto() -> AsyncEngine:
    """Engine per lo stato di sessione (vedi docstring del modulo)."""
    return _engine_da_variabile(VARIABILE_DIRETTA)


def engine_manutenzione() -> AsyncEngine:
    """Engine della manutenzione delle partizioni (vedi docstring del modulo)."""
    return _engine_da_variabile(VARIABILE_MANUTENZIONE)


def manutenzione_configurata() -> bool:
    """True se ``DATABASE_URL_MANUTENZIONE`` è impostata."""
    return bool((os.environ.get(VARIABILE_MANUTENZIONE) or "").strip())


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager per sessioni async SQLAlchemy.

    Uso corretto:
        async with get_async_session() as session:
            result = await session.execute(select(Aip).where(...))

    - Esegue commit automatico all'uscita del blocco
    - Esegue rollback automatico in caso di eccezione
    - Non usare mai session.commit() dentro il blocco (doppio commit)

    Raises:
        DBNonInizializzato (RuntimeError): se init_db() non è stato ancora chiamato.
    """
    if _session_factory is None:
        raise DBNonInizializzato(
            "DB non inizializzato — chiama init_db() nel lifespan del servizio"
        )
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_async_session_lettura() -> AsyncGenerator[AsyncSession, None]:
    """
    Sessione di SOLA LETTURA per cruscotti, ricerca, elenchi e statistiche.

    - Con ``DATABASE_URL_LETTURA``: connessione alla replica (ogni transazione
      è in sola lettura per impostazione della connessione).
    - Senza: ripiego sul primario, con la transazione aperta in sola lettura
      (``postgresql_readonly``): il comportamento è lo stesso nei due casi.

    All'uscita la sessione si chiude senza COMMIT (non c'è nulla da
    confermare; la transazione si annulla al rilascio della connessione) e gli
    oggetti letti restano leggibili. Una scrittura dentro il blocco fallisce con l'errore di
    PostgreSQL «cannot execute ... in a read-only transaction».

    Raises:
        DBNonInizializzato (RuntimeError): se init_db() non è stato ancora chiamato.
    """
    fabbrica = _session_factory_lettura or _session_factory
    if fabbrica is None:
        raise DBNonInizializzato(
            "DB non inizializzato — chiama init_db() nel lifespan del servizio"
        )
    async with fabbrica() as session:
        try:
            if _session_factory_lettura is None:
                # Transazione aperta in sola lettura dal driver (asyncpg
                # ``readonly=True``): nessuna istruzione in più per richiesta;
                # la caratteristica torna normale quando la connessione rientra
                # nel pool.
                await session.connection(execution_options={"postgresql_readonly": True})
            yield session
        finally:
            # close(), non rollback(): la transazione (di sola lettura) si annulla
            # comunque al rilascio della connessione, ma gli oggetti ORM letti
            # restano utilizzabili dopo il blocco. Con rollback() venivano
            # «scaduti» e il primo accesso fuori sessione dava DetachedInstanceError
            # (elenco dei PdA della dashboard, trovato in R04 integrazione).
            await session.close()
