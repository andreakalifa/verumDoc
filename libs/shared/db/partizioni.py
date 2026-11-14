# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/db/partizioni.py — partizioni per intervallo di tempo: creazione
anticipata e distacco a fine retention (R04, piano §4.2, VD-C-076, VD-C-103).

Nessuna estensione (pg_partman non è installato nel cluster di sviluppo e su
RDS richiede pg_cron per girare da solo): il lavoro è codice nostro, idempotente,
eseguito dalla manutenzione del database (``libs.shared.db.manutenzione``) in
più repliche senza conflitti: una sola alla volta grazie a un LEASE in tabella
(``manutenzione_lease``), non a un lock consultivo di sessione.

R04 correzioni (rev4 rilievi 5, 6 e 11):
  - compatibile con PgBouncer in modalità transaction: nessuno stato di
    sessione. Il lease è una riga; ``lock_timeout`` è ``SET LOCAL`` dentro la
    transazione esplicita di creazione e aggancio; ``DETACH ... CONCURRENTLY``
    (che non può stare in una transazione) non lo usa: attende senza bloccare le
    scritture (SHARE UPDATE EXCLUSIVE);
  - ruolo dedicato: il ruolo dei servizi NON è proprietario delle tabelle e non
    può creare, agganciare, staccare né eliminare partizioni. La manutenzione
    gira con ``DATABASE_URL_MANUTENZIONE`` (ruolo proprietario, connessione
    diretta: CronJob ``gateway-db-manutenzione`` in esercizio; in sviluppo e nei
    test si ripiega su ``DATABASE_URL``) e lascia traccia di ogni distacco in
    ``audit_global`` (``db.partizione_staccata``, ``db.partizione_riagganciata``);
  - le tabelle PROTETTE (``audit_global``, ``verifiche_registro``) non si staccano
    mai, qualunque sia la politica; in più gli event trigger del database
    (migrazioni 20261023_031 e 033) rifiutano DETACH, DROP e RENAME delle loro
    partizioni. Dalla 033 i registri hanno un proprietario dedicato
    (``verumdoc_audit_proprietario``, NOLOGIN): la manutenzione NON ne è
    proprietaria e ne crea le partizioni solo con ``vd_crea_partizione_registro``;
  - lease perso durante il giro (rinnovo fallito): il giro si ferma con
    ``LeasePerso`` invece di proseguire accanto a un'altra replica;
  - dopo il distacco il controllo si RIPETE sulla partizione staccata: se nel
    frattempo è comparsa una riga attiva (es. una consegna webhook rimessa in
    coda) la partizione si ri-aggancia invece di essere eliminata.

Convenzioni (fissate dalla migrazione 20261023_028):
  - le partizioni stanno nello schema ``partizioni`` (fuori da ``public``: il
    confronto Alembic modelli/schema non le vede, i ruoli applicativi le
    raggiungono solo attraverso la tabella madre);
  - nome ``<tabella>_p<AAAAMMGG>`` (giorno) o ``<tabella>_p<AAAAMM>`` (mese);
  - limiti in UTC: [inizio, fine) del giorno o del mese;
  - nessuna partizione DEFAULT: una riga fuori dalle partizioni esistenti fa
    fallire l'INSERT in modo esplicito. Per questo le partizioni si creano in
    anticipo (``anticipo`` periodi) e la metrica ``verumdoc_partizioni_future``
    avvisa molto prima che si esauriscano.

Creazione senza bloccare le scritture: ``CREATE TABLE ... (LIKE madre)`` e poi
``ATTACH PARTITION`` (lock SHARE UPDATE EXCLUSIVE sulla madre, le INSERT
continuano); la partizione nuova è vuota, il controllo dei limiti è immediato.
Indici, chiavi esterne uscenti e trigger di riga si propagano dalla madre.

Distacco: ``DETACH PARTITION ... CONCURRENTLY`` (PostgreSQL ≥ 14, fuori
transazione, nessun lock esclusivo sulla madre) e poi ``DROP TABLE``. Un
distacco interrotto lascia la partizione «in distacco»: il giro successivo lo
completa con ``FINALIZE``. Prima del distacco un controllo per tabella può
bloccare l'operazione (eventi non ancora pubblicati, consegne in corso): la
partizione resta, la metrica ``verumdoc_partizioni_bloccate`` lo segnala; lo
stesso controllo si ripete dopo il distacco (vedi sopra).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import text

from libs.shared.db import metriche
from libs.verumdoc_rules.impostazioni import (
    Impostazione,
    RegistroImpostazioni,
    TipoImpostazione,
)

logger = structlog.get_logger(__name__)

SCHEMA_PARTIZIONI = "partizioni"
Periodo = Literal["giorno", "mese"]

# ── Retention impostabile (registro delle impostazioni, livello CONSERVATORE) ──
RETENZIONE_EVENTI_CLIENTE_GIORNI = "RETENZIONE_EVENTI_CLIENTE_GIORNI"
RETENZIONE_OUTBOX_GIORNI = "RETENZIONE_OUTBOX_GIORNI"

REGISTRO_RETENZIONE = RegistroImpostazioni((
    Impostazione(
        RETENZIONE_EVENTI_CLIENTE_GIORNI, TipoImpostazione.INTERO, predefinito=90,
        descrizione=(
            "Giorni per cui restano consultabili gli eventi per i clienti "
            "(GET /api/v1/eventi) e le consegne webhook; le partizioni mensili "
            "interamente più vecchie si staccano (retention effettiva fra N e N+31 giorni)"),
        minimo=7, massimo=3650),
    Impostazione(
        RETENZIONE_OUTBOX_GIORNI, TipoImpostazione.INTERO, predefinito=3,
        descrizione=(
            "Giorni per cui restano nell'outbox gli eventi già pubblicati "
            "(diagnosi); le partizioni giornaliere più vecchie si staccano se "
            "non contengono eventi in attesa"),
        minimo=1, massimo=90),
))


@dataclass(frozen=True)
class Politica:
    """Come si partiziona e si mantiene una tabella.

    ``controllo`` è una SELECT che restituisce almeno una riga quando la
    partizione NON si può staccare; segnaposti: ``{partizione}`` (nome
    qualificato), ``:inizio`` e ``:fine`` (limiti, parametri).
    ``protezione_audit``: registro append-only (mai staccato); le sue
    partizioni le crea ``vd_crea_partizione_registro`` con RLS e FORCE, le
    politiche e il trigger contro TRUNCATE della tabella madre."""

    tabella: str
    periodo: Periodo
    anticipo: int
    codice_retenzione: str | None = None
    controllo: str | None = None
    protezione_audit: bool = False


#: Ordine significativo: le consegne si staccano prima degli eventi a cui si riferiscono.
POLITICHE: tuple[Politica, ...] = (
    Politica("outbox_eventi", "giorno", anticipo=7, codice_retenzione=RETENZIONE_OUTBOX_GIORNI,
             controllo="SELECT 1 FROM {partizione} WHERE pubblicato_il IS NULL LIMIT 1"),
    Politica("webhook_consegne", "mese", anticipo=3,
             codice_retenzione=RETENZIONE_EVENTI_CLIENTE_GIORNI,
             controllo=("SELECT 1 FROM {partizione} WHERE stato IN ('IN_CODA', 'IN_CONSEGNA') "
                        "LIMIT 1")),
    Politica("eventi_cliente", "mese", anticipo=3,
             codice_retenzione=RETENZIONE_EVENTI_CLIENTE_GIORNI,
             controllo=("SELECT 1 FROM {partizione} WHERE posizione IS NULL "
                        "UNION ALL SELECT 1 FROM public.webhook_consegne "
                        "WHERE evento_creato_il >= :inizio AND evento_creato_il < :fine "
                        "LIMIT 1")),
    Politica("audit_global", "mese", anticipo=3, protezione_audit=True),
    # R04 (verifica continua, migrazione 027): registro delle verifiche, evidenza
    # append-only senza retention (nessun distacco). Il lavoratore della verifica
    # continua crea comunque i mesi futuri con lo stesso lock (anticipo proprio,
    # VERIFICA_PARTIZIONI_MESI_AVANTI); qui le crea anche la manutenzione condivisa.
    Politica("verifiche_registro", "mese", anticipo=3),
)


# ── Calcoli puri ─────────────────────────────────────────────────────────────

def inizio_periodo(periodo: Periodo, istante: datetime) -> datetime:
    """Inizio (UTC) del giorno o del mese che contiene ``istante``."""
    if istante.tzinfo is None:
        raise ValueError("istante senza fuso orario")
    u = istante.astimezone(UTC)
    if periodo == "giorno":
        return datetime(u.year, u.month, u.day, tzinfo=UTC)
    return datetime(u.year, u.month, 1, tzinfo=UTC)


def periodo_successivo(periodo: Periodo, inizio: datetime) -> datetime:
    if periodo == "giorno":
        return inizio + timedelta(days=1)
    return (inizio.replace(year=inizio.year + 1, month=1) if inizio.month == 12
            else inizio.replace(month=inizio.month + 1))


def nome_partizione(tabella: str, periodo: Periodo, inizio: datetime) -> str:
    formato = "%Y%m%d" if periodo == "giorno" else "%Y%m"
    return f"{tabella}_p{inizio.strftime(formato)}"


_NOME = re.compile(r"^(?P<tabella>[a-z_0-9]+)_p(?P<data>\d{6}|\d{8})$")


def inizio_da_nome(nome: str) -> datetime | None:
    """Inizio del periodo dal nome della partizione (None se il nome non è nostro)."""
    m = _NOME.match(nome)
    if not m:
        return None
    d = m.group("data")
    if len(d) == 8:
        return datetime(int(d[:4]), int(d[4:6]), int(d[6:]), tzinfo=UTC)
    return datetime(int(d[:4]), int(d[4:6]), 1, tzinfo=UTC)


def periodi_da_creare(periodo: Periodo, adesso: datetime, anticipo: int) -> list[datetime]:
    """Inizi dei periodi che devono esistere: quello corrente e ``anticipo`` successivi."""
    inizio = inizio_periodo(periodo, adesso)
    risultato = [inizio]
    for _ in range(anticipo):
        inizio = periodo_successivo(periodo, inizio)
        risultato.append(inizio)
    return risultato


def scadute(periodo: Periodo, inizi: Sequence[datetime], adesso: datetime,
            retenzione_giorni: int) -> list[datetime]:
    """Partizioni il cui periodo è finito da più di ``retenzione_giorni``:
    ogni riga contenuta è più vecchia della retention."""
    soglia = adesso - timedelta(days=retenzione_giorni)
    return [i for i in sorted(inizi) if periodo_successivo(periodo, i) <= soglia]


# ── Accesso al database ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Partizione:
    nome: str
    inizio: datetime
    in_distacco: bool


async def partizioni_di(conn: Any, tabella: str) -> list[Partizione]:
    """Partizioni nostre (schema ``partizioni``, nome convenzionale) della tabella madre."""
    righe = (await conn.execute(text(
        "SELECT c.relname, i.inhdetachpending FROM pg_inherits i "
        "JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE i.inhparent = CAST(:madre AS regclass) AND n.nspname = :schema"),
        {"madre": f"public.{tabella}", "schema": SCHEMA_PARTIZIONI})).all()
    risultato = []
    for nome, pendente in righe:
        inizio = inizio_da_nome(nome)
        if inizio is not None and nome.startswith(f"{tabella}_p"):
            risultato.append(Partizione(nome, inizio, bool(pendente)))
    return sorted(risultato, key=lambda p: p.inizio)


def _letterale(istante: datetime) -> str:
    return "'" + istante.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00") + "'"


#: Tabelle che non si staccano mai (registri append-only senza retention): la
#: manutenzione rifiuta il distacco anche se una politica lo chiedesse.
TABELLE_PROTETTE = frozenset({"audit_global", "verifiche_registro"})


async def crea_partizione(conn: Any, politica: Politica, inizio: datetime) -> bool:
    """Crea e aggancia la partizione del periodo che inizia a ``inizio``.
    Idempotente: False se esisteva già. ``conn`` dentro una transazione
    esplicita (``engine.begin()``): creazione, aggancio e difese dell'audit
    sono atomici e ``lock_timeout`` vale solo qui (``SET LOCAL``, compatibile
    con PgBouncer in modalità transaction)."""
    nome = nome_partizione(politica.tabella, politica.periodo, inizio)
    if politica.tabella in TABELLE_PROTETTE:
        # Registri: la manutenzione NON ne è proprietaria; crea con la funzione
        # del proprietario dedicato (migrazione 20261023_033), che decide nome,
        # limiti e difese (RLS con FORCE, politiche, trigger) prima dell'aggancio.
        creata = bool((await conn.execute(text(
            "SELECT public.vd_crea_partizione_registro(:tabella, :inizio)"),
            {"tabella": politica.tabella, "inizio": inizio})).scalar_one())
        if creata:
            logger.info("partizione_creata", tabella=politica.tabella, partizione=nome)
        return creata
    qualificato = f"{SCHEMA_PARTIZIONI}.{nome}"
    fine = periodo_successivo(politica.periodo, inizio)
    agganciata = (await conn.execute(text(
        "SELECT 1 FROM pg_inherits WHERE inhrelid = to_regclass(:p) "
        "AND inhparent = CAST(:madre AS regclass)"),
        {"p": qualificato, "madre": f"public.{politica.tabella}"})).first()
    if agganciata is not None:
        return False
    await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    await conn.execute(text(
        f"CREATE TABLE IF NOT EXISTS {qualificato} "
        f"(LIKE public.{politica.tabella} INCLUDING DEFAULTS INCLUDING CONSTRAINTS "
        "INCLUDING STORAGE INCLUDING COMMENTS)"))
    await conn.execute(text(
        f"ALTER TABLE public.{politica.tabella} ATTACH PARTITION {qualificato} "
        f"FOR VALUES FROM ({_letterale(inizio)}) TO ({_letterale(fine)})"))
    logger.info("partizione_creata", tabella=politica.tabella, partizione=nome)
    return True


async def _traccia(engine: Any, evento: str, dettagli: dict[str, Any]) -> None:
    """Voce di audit di un'operazione della manutenzione (chi: il ruolo del database)."""
    import json

    async with engine.begin() as tx:
        await tx.execute(text(
            "INSERT INTO public.audit_global (evento, dettagli) VALUES "
            "(:evento, CAST(:dettagli AS jsonb) || jsonb_build_object('ruolo_db', current_user))"),
            {"evento": evento, "dettagli": json.dumps(dettagli, default=str)})


async def stacca_partizione(conn: Any, politica: Politica, partizione: Partizione, *,
                            engine: Any = None) -> bool:
    """Stacca ed elimina la partizione se il controllo lo consente, prima E dopo
    il distacco. True se eliminata, False se bloccata o ri-agganciata (resta,
    metrica e log). ``conn`` in AUTOCOMMIT; ``engine`` per le transazioni brevi
    (ri-aggancio, audit): predefinito quello di ``conn``."""
    if politica.tabella in TABELLE_PROTETTE or politica.protezione_audit:
        logger.error("partizione_protetta_non_staccabile", tabella=politica.tabella,
                     partizione=partizione.nome)
        return False
    engine = engine or conn.engine
    qualificato = f"{SCHEMA_PARTIZIONI}.{partizione.nome}"
    fine = periodo_successivo(politica.periodo, partizione.inizio)
    parametri = {"inizio": partizione.inizio, "fine": fine}
    if politica.controllo and not partizione.in_distacco:
        bloccata = (await conn.execute(
            text(politica.controllo.format(partizione=qualificato)), parametri)).first()
        if bloccata is not None:
            logger.warning("partizione_scaduta_non_staccabile", tabella=politica.tabella,
                           partizione=partizione.nome)
            return False
    if partizione.in_distacco:
        await conn.execute(text(
            f"ALTER TABLE public.{politica.tabella} DETACH PARTITION {qualificato} FINALIZE"))
    else:
        await conn.execute(text(
            f"ALTER TABLE public.{politica.tabella} DETACH PARTITION {qualificato} "
            "CONCURRENTLY"))
    # Corsa fra il controllo e il distacco (rev4 rilievo 11): si ricontrolla la
    # partizione ora staccata; una riga attiva comparsa nel frattempo la fa
    # ri-agganciare invece di eliminarla.
    if politica.controllo and (await conn.execute(
            text(politica.controllo.format(partizione=qualificato)), parametri)).first():
        async with engine.begin() as tx:
            await tx.execute(text("SET LOCAL lock_timeout = '5s'"))
            await tx.execute(text(
                f"ALTER TABLE public.{politica.tabella} ATTACH PARTITION {qualificato} "
                f"FOR VALUES FROM ({_letterale(partizione.inizio)}) TO ({_letterale(fine)})"))
        metriche.incrementa("verumdoc_partizioni_riagganciate", tabella=politica.tabella)
        logger.error("partizione_riagganciata", tabella=politica.tabella,
                     partizione=partizione.nome,
                     motivo="righe attive comparse durante il distacco")
        await _traccia(engine, "db.partizione_riagganciata", {
            "tabella": politica.tabella, "partizione": partizione.nome})
        return False
    await conn.execute(text(f"DROP TABLE IF EXISTS {qualificato}"))
    metriche.incrementa("verumdoc_partizioni_staccate", tabella=politica.tabella)
    logger.info("partizione_staccata", tabella=politica.tabella, partizione=partizione.nome)
    await _traccia(engine, "db.partizione_staccata", {
        "tabella": politica.tabella, "partizione": partizione.nome,
        "dal": partizione.inizio.isoformat(), "al": fine.isoformat()})
    return True


async def retenzione_giorni(conn: Any, codice: str) -> int:
    """Valore effettivo dell'impostazione di retention: livello CONSERVATORE del
    registro (``impostazioni_valori``) se presente e valido, altrimenti il
    predefinito. Un valore non valido NON si indovina: si usa il predefinito
    e si registra l'errore (la retention più lunga è la scelta prudente solo
    se il predefinito è il più lungo: per questo il minimo è alto)."""
    voce = REGISTRO_RETENZIONE[codice]
    riga = (await conn.execute(text(
        "SELECT valore FROM public.impostazioni_valori "
        "WHERE livello = 'CONSERVATORE' AND codice = :c"), {"c": codice})).first()
    if riga is None:
        return int(voce.predefinito)
    try:
        return int(voce.converti(riga[0]))
    except ValueError as exc:
        logger.error("retenzione_non_valida", codice=codice, errore=str(exc))
        return int(voce.predefinito)


@dataclass
class EsitoManutenzione:
    create: dict[str, int]
    staccate: dict[str, int]
    bloccate: dict[str, int]
    future: dict[str, int]


async def mantieni(engine: Any, conn: Any, adesso: datetime,
                   politiche: Sequence[Politica] = POLITICHE,
                   rinnova: Any = None) -> EsitoManutenzione:
    """Un giro di manutenzione (con il lease già preso): crea le partizioni
    mancanti fino all'anticipo (una transazione per partizione su ``engine``) e
    stacca quelle scadute (``conn`` in AUTOCOMMIT). ``rinnova`` (facoltativo) si
    chiama fra una tabella e l'altra per rinnovare il lease."""
    esito = EsitoManutenzione({}, {}, {}, {})
    for p in politiche:
        if rinnova is not None:
            await rinnova()
        create = 0
        for inizio in periodi_da_creare(p.periodo, adesso, p.anticipo):
            async with engine.begin() as tx:
                if await crea_partizione(tx, p, inizio):
                    create += 1
        esistenti = await partizioni_di(conn, p.tabella)
        corrente = inizio_periodo(p.periodo, adesso)
        future = sum(1 for x in esistenti if x.inizio > corrente and not x.in_distacco)
        staccate = bloccate = 0
        if p.codice_retenzione and p.tabella not in TABELLE_PROTETTE:
            giorni = await retenzione_giorni(conn, p.codice_retenzione)
            da_staccare = set(scadute(p.periodo, [x.inizio for x in esistenti], adesso, giorni))
            for x in esistenti:
                if x.inizio in da_staccare:
                    if rinnova is not None:
                        await rinnova()
                    if await stacca_partizione(conn, p, x, engine=engine):
                        staccate += 1
                    else:
                        bloccate += 1
        esito.create[p.tabella] = create
        esito.staccate[p.tabella] = staccate
        esito.bloccate[p.tabella] = bloccate
        esito.future[p.tabella] = future
        metriche.imposta("verumdoc_partizioni_future", future, tabella=p.tabella)
        metriche.imposta("verumdoc_partizioni_bloccate", bloccate, tabella=p.tabella)
    return esito


class LeasePerso(RuntimeError):
    """Il lease della manutenzione è scaduto ed è passato a un'altra replica."""


#: Nome del lease della manutenzione delle partizioni (``manutenzione_lease``).
LEASE = "partizioni"
#: Durata del lease (rinnovato fra una tabella e l'altra): oltre, un'altra replica
#: può subentrare a una manutenzione morta.
DURATA_LEASE_SECONDI = 900


async def prendi_lease(conn: Any, detentore: str,
                       durata: float = DURATA_LEASE_SECONDI) -> bool:
    """Prende (o rinnova) il lease se libero, scaduto o già di ``detentore``.
    Tempo del database (il lease riguarda il tempo reale, non l'``adesso`` del giro)."""
    riga = (await conn.execute(text(
        "INSERT INTO public.manutenzione_lease (nome, detentore, scade_il, aggiornato_il) "
        "VALUES (:nome, :detentore, now() + make_interval(secs => :durata), now()) "
        "ON CONFLICT (nome) DO UPDATE SET detentore = EXCLUDED.detentore, "
        "  scade_il = EXCLUDED.scade_il, aggiornato_il = EXCLUDED.aggiornato_il "
        "WHERE manutenzione_lease.scade_il <= now() "
        "   OR manutenzione_lease.detentore = EXCLUDED.detentore "
        "RETURNING detentore"),
        {"nome": LEASE, "detentore": detentore, "durata": float(durata)})).first()
    return riga is not None


async def rilascia_lease(conn: Any, detentore: str) -> None:
    await conn.execute(text(
        "UPDATE public.manutenzione_lease SET scade_il = '-infinity' "
        "WHERE nome = :nome AND detentore = :detentore"), {"nome": LEASE, "detentore": detentore})


def detentore_predefinito() -> str:
    import os
    import socket
    import uuid

    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:200]


async def esegui_manutenzione_partizioni(engine: Any = None, adesso: datetime | None = None,
                                         politiche: Sequence[Politica] = POLITICHE,
                                         detentore: str | None = None,
                                         ) -> EsitoManutenzione | None:
    """Giro completo con il lease (una replica alla volta). None se un'altra
    replica lo detiene. ``engine`` predefinito: quello della manutenzione
    (``DATABASE_URL_MANUTENZIONE``, altrimenti quello del servizio)."""
    from libs.shared.db import engine_manutenzione

    engine = engine or engine_manutenzione()
    adesso = adesso or datetime.now(UTC)
    detentore = detentore or detentore_predefinito()
    async with engine.connect() as grezza:
        conn = await grezza.execution_options(isolation_level="AUTOCOMMIT")
        if not await prendi_lease(conn, detentore):
            return None

        async def rinnova() -> None:
            # Lease perso (es. un DETACH CONCURRENTLY che ha atteso oltre la
            # durata): il giro si FERMA, un'altra replica può averlo preso.
            if not await prendi_lease(conn, detentore):
                logger.error("manutenzione_lease_perso", lease=LEASE, detentore=detentore)
                raise LeasePerso(f"lease «{LEASE}» perso da {detentore}")

        try:
            return await mantieni(engine, conn, adesso, politiche, rinnova)
        finally:
            await rilascia_lease(conn, detentore)


def giorno_utc(istante: datetime) -> date:
    return istante.astimezone(UTC).date()
