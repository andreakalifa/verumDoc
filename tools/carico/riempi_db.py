# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Prova di volume del database: dati sintetici realistici e query calde (R04 dati).

Obiettivo del piano (§4.2): PostgreSQL deve reggere 1 milione di documenti al
giorno (~365 milioni l'anno) più audit, outbox ed eventi. Questo strumento:

  1. ``riempi``: genera con COPY (asyncpg ``copy_records_to_table``) enti,
     PdV, PdA, documenti, impronte di versamento, eventi per i clienti con
     consegne webhook, outbox e audit, distribuiti su ``--giorni`` giorni e su
     ``--enti`` enti con una distribuzione sbilanciata (il primo ente ha circa
     il 40% dei versamenti, come una regione o un'ASL). Funziona sullo schema
     R03 (migrazione 20261021_024) e su quello R04 (20261023_028: crea prima
     le partizioni dei periodi passati con ``libs.shared.db.partizioni``);
  2. ``misura``: esegue le query calde con ``EXPLAIN (ANALYZE, BUFFERS)`` (tre
     volte, si tiene la mediana del tempo d'esecuzione) e scrive un JSON e una
     tabella Markdown: inserimento del versamento, stato del PdV, elenchi a
     cursore, eventi a cursore, relay dell'outbox, cruscotto (conteggi per
     stato, throughput, totale PdA, elenchi di consistenza, percentile SLA).
     Le scritture misurate (INSERT, FOR UPDATE) sono in transazioni annullate.

Uso (dalla radice del repository, database DEDICATO, mai uno condiviso):

    createdb -h 127.0.0.1 -p 5466 -U postgres r04_dati_volume
    export DSN=postgresql://postgres@127.0.0.1:5466/r04_dati_volume
    (cd services/gateway && DATABASE_URL=${DSN/postgresql/postgresql+asyncpg} \\
        python -m alembic upgrade 20261021_024)
    python tools/carico/riempi_db.py riempi --dsn $DSN --pdv 1000000 --documenti-per-pdv 10
    python tools/carico/riempi_db.py misura --dsn $DSN --etichetta prima --uscita /tmp/volume
    (cd services/gateway && ... python -m alembic upgrade 20261023_028)
    python tools/carico/riempi_db.py misura --dsn $DSN --etichetta dopo --uscita /tmp/volume

Nessun dato personale reale: nomi e codici sono sintetici e riconoscibili
(«Ente sintetico N», codici fiscali che iniziano per «SINT»).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

RADICE = Path(__file__).resolve().parents[2]
if str(RADICE) not in sys.path:
    sys.path.insert(0, str(RADICE))

STATI_PDV = [("CONSERVATO", 0.95), ("ERRORE", 0.02), ("VALIDATO", 0.01), ("IN_BUILD", 0.01),
             ("RICEVUTO", 0.01)]


def _log(*parti: Any) -> None:
    print(time.strftime("%H:%M:%S"), *parti, flush=True)  # noqa: T201 — strumento da riga di comando


# ── Schema ──────────────────────────────────────────────────────────────────

async def schema_r04(conn: asyncpg.Connection) -> bool:
    """True se l'outbox ha le colonne di R04 (migrazione 20261023_028)."""
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = 'outbox_eventi' AND column_name = 'origine')"))


async def prepara_partizioni(dsn: str, dal: datetime) -> None:
    """Schema R04: crea le partizioni dei periodi da ``dal`` a oggi (idempotente)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from libs.shared.db.partizioni import (
        POLITICHE,
        crea_partizione,
        inizio_periodo,
        periodo_successivo,
    )

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        for p in POLITICHE:
            inizio = inizio_periodo(p.periodo, dal)
            fine = datetime.now(UTC) + timedelta(days=1)
            while inizio <= fine:
                async with engine.begin() as tx:  # una transazione per partizione
                    await crea_partizione(tx, p, inizio)
                inizio = periodo_successivo(p.periodo, inizio)
    finally:
        await engine.dispose()


# ── Generazione ─────────────────────────────────────────────────────────────

@dataclass
class Parametri:
    enti: int
    pdv: int
    documenti_per_pdv: int
    eventi_per_pdv: int
    audit: int
    outbox: int
    giorni: int
    lotto: int
    seme: int


def _pesi_enti(n: int) -> list[float]:
    """Primo ente ~40%, gli altri a coda lunga (Zipf)."""
    coda = [1 / (i + 1) for i in range(n - 1)]
    totale = sum(coda) or 1
    return [0.4] + [0.6 * c / totale for c in coda]


def _stato(rnd: random.Random) -> str:
    x = rnd.random()
    for stato, p in STATI_PDV:
        if x < p:
            return stato
        x -= p
    return STATI_PDV[0][0]


async def _copia(conn: asyncpg.Connection, tabella: str, colonne: list[str],
                 righe: Iterator[tuple], lotto: int) -> int:
    n = 0
    blocco: list[tuple] = []
    for r in righe:
        blocco.append(r)
        if len(blocco) >= lotto:
            await conn.copy_records_to_table(tabella, records=blocco, columns=colonne,
                                             schema_name="public")
            n += len(blocco)
            blocco = []
    if blocco:
        await conn.copy_records_to_table(tabella, records=blocco, columns=colonne,
                                         schema_name="public")
        n += len(blocco)
    return n


async def riempi(dsn: str, par: Parametri) -> dict[str, Any]:
    rnd = random.Random(par.seme)  # noqa: S311 — dati sintetici riproducibili, non crittografia
    conn = await asyncpg.connect(dsn)
    tempi: dict[str, float] = {}
    try:
        r04 = await schema_r04(conn)
        adesso = datetime.now(UTC)
        inizio = adesso - timedelta(days=par.giorni)
        if r04:
            await prepara_partizioni(dsn, inizio - timedelta(days=1))
        # Enti e classe
        t0 = time.monotonic()
        enti = [uuid.uuid4() for _ in range(par.enti)]
        await _copia(conn, "tenants", ["id", "codice_fiscale", "ragione_sociale", "schema_name",
                                       "stato", "email_referente", "piano"],
                     ((t, f"SINT{i:012d}", f"Ente sintetico {i}", f"sint_{t.hex[:20]}", "ATTIVO",
                       f"ente{i}@example.org", "enterprise" if i == 0 else "starter")
                      for i, t in enumerate(enti)), par.lotto)
        classe = await conn.fetchval("SELECT id FROM public.classi_documentali "
                                     "WHERE tenant_id IS NULL ORDER BY codice LIMIT 1")
        if classe is None:
            classe = uuid.uuid4()
            await conn.execute("INSERT INTO public.classi_documentali (id, codice, nome) "
                               "VALUES ($1, 'fattura', 'Fattura')", classe)
        pesi = _pesi_enti(par.enti)
        # Piano dei PdV (in memoria solo gli identificativi: 1M × ~100 byte)
        piano: list[tuple[uuid.UUID, uuid.UUID, datetime, str]] = []
        passo = par.giorni * 86400 / max(par.pdv, 1)
        for i in range(par.pdv):
            ricevuto = inizio + timedelta(seconds=i * passo + rnd.random() * passo)
            piano.append((uuid.uuid4(), rnd.choices(enti, pesi)[0], ricevuto, _stato(rnd)))
        _log(f"piano di {len(piano)} PdV pronto")

        utente = uuid.uuid4()
        meta = json.dumps({"canale": "API", "num_documenti": par.documenti_per_pdv,
                           "indice": {"chiave": {"registro": "FATTURE", "anno": "2026"}}})

        def righe_pdv():
            for pid, ente, ricevuto, stato in piano:
                errori = '[{"codice": "VRS-108", "msg": "formato"}]' if stato == "ERRORE" else "[]"
                yield (pid, f"Fattura sintetica {pid.hex[:8]}", stato, classe, utente, ente,
                       meta, errori, f"staging/versamenti/{ente}/{pid}", ricevuto,
                       ricevuto + timedelta(minutes=2), ricevuto)

        n = await _copia(conn, "pdv", ["id", "oggetto", "stato", "classe_doc_id", "user_id",
                                       "tenant_id", "metadata_json", "errori_validazione",
                                       "s3_prefix", "data_ricezione", "data_aggiornamento",
                                       "created_at"], righe_pdv(), par.lotto)
        tempi["pdv"] = time.monotonic() - t0
        _log(f"pdv: {n}")

        t0 = time.monotonic()

        def righe_impronte():
            for i, (pid, ente, ricevuto, _s) in enumerate(piano):
                yield (pid, ente, f"{rnd.getrandbits(256):064x}", f"{rnd.getrandbits(256):064x}",
                       f"idem-{pid.hex}", "FATTURE", "2026", f"N{i}", ricevuto)

        n = await _copia(conn, "versamento_impronte",
                         ["pdv_id", "tenant_id", "impronta_indice", "impronta_richiesta",
                          "chiave_idempotenza", "chiave_ud_registro", "chiave_ud_anno",
                          "chiave_ud_numero", "created_at"], righe_impronte(), par.lotto)
        tempi["versamento_impronte"] = time.monotonic() - t0
        _log(f"versamento_impronte: {n}")

        # PdA: uno per PdV conservato o in costruzione
        t0 = time.monotonic()
        aip_di: list[tuple[uuid.UUID, uuid.UUID, datetime]] = []

        def righe_aip():
            for pid, ente, ricevuto, stato in piano:
                if stato not in ("CONSERVATO", "IN_BUILD"):
                    continue
                aid = uuid.uuid4()
                versato = ricevuto + timedelta(seconds=30)
                conservato = ricevuto + timedelta(seconds=40 + rnd.random() * 60) \
                    if stato == "CONSERVATO" else None
                aip_di.append((aid, ente, versato))
                yield (aid, pid, ente, f"urn:verumdoc:sint:{aid}",
                       "CONSERVATO" if stato == "CONSERVATO" else "IN_BUILD",
                       f"{ente}/{aid}/manifest.json", "verumdoc-aip",
                       f"{rnd.getrandbits(256):064x}",
                       rnd.randrange(50_000, 5_000_000), versato, conservato,
                       versato, versato)

        n = await _copia(conn, "aip", ["id", "pdv_id", "tenant_id", "urn", "stato", "s3_key",
                                       "s3_bucket", "hash_sha256", "dimensione_bytes",
                                       "data_versamento", "data_conservazione", "created_at",
                                       "updated_at"], righe_aip(), par.lotto)
        tempi["aip"] = time.monotonic() - t0
        _log(f"aip: {n}")

        t0 = time.monotonic()

        def righe_documenti():
            for aid, ente, versato in aip_di:
                for k in range(par.documenti_per_pdv):
                    yield (uuid.uuid4(), aid, f"documento_{k}.pdf",
                           "PRINCIPALE" if k == 0 else "ALLEGATO", "application/pdf",
                           rnd.randrange(20_000, 600_000), f"{rnd.getrandbits(256):064x}",
                           f"{ente}/{aid}/documento_{k}.pdf", k, versato)

        n = await _copia(conn, "documenti", ["id", "aip_id", "nome_file", "tipo", "formato_mime",
                                             "dimensione_bytes", "hash_sha256", "s3_key",
                                             "ordine", "created_at"],
                         righe_documenti(), par.lotto)
        tempi["documenti"] = time.monotonic() - t0
        _log(f"documenti: {n}")

        # Eventi per i clienti (posizione = ordine di creazione) e consegne (10%)
        t0 = time.monotonic()
        sotto = {}
        for ente in enti[: max(1, par.enti // 10)]:
            sid = uuid.uuid4()
            sotto[ente] = sid
            await conn.execute(
                "INSERT INTO public.webhook_sottoscrizioni (id, tenant_id, url, tipi, "
                "segreto_cifrato, segreto_suffisso) VALUES ($1, $2, 'https://ricevitore.example', "
                "'[\"versamento.conservato\"]', 'sintetico', 'sint')", sid, ente)
        tipi = ["versamento.ricevuto", "versamento.conservato", "versamento.validato",
                "pdd.pronto"][: par.eventi_per_pdv]
        base_id = int(await conn.fetchval(
            "SELECT COALESCE(max(id), 0) FROM public.eventi_cliente")) + 1
        consegne: list[tuple] = []

        def righe_eventi():
            eid = base_id
            for pid, ente, ricevuto, _s in piano:
                for k, tipo in enumerate(tipi):
                    quando = ricevuto + timedelta(seconds=k * 60)
                    yield (eid, ente, tipo, "versamento", str(pid),
                           json.dumps({"pdv_id": str(pid)}), "sintetico", quando, eid)
                    if ente in sotto and tipo == "versamento.conservato":
                        consegne.append((ente, sotto[ente], eid, quando))
                    eid += 1

        colonne_ev = ["id", "tenant_id", "tipo", "risorsa_tipo", "risorsa_id", "dati", "origine",
                      "creato_il", "posizione"]
        if not r04:
            # Schema R03: id IDENTITY ALWAYS, COPY non può scriverlo: si lascia al DB
            # e la posizione si riassegna dopo con lo stesso ordine.
            def senza_id():
                for r in righe_eventi():
                    yield r[1:8]
            n = await _copia(conn, "eventi_cliente", colonne_ev[1:8], senza_id(), par.lotto)
            await conn.execute("UPDATE public.eventi_cliente SET posizione = id "
                               "WHERE posizione IS NULL")
        else:
            n = await _copia(conn, "eventi_cliente", colonne_ev, righe_eventi(), par.lotto)
            await conn.execute("SELECT setval('public.eventi_cliente_seq', $1)",
                               base_id + n)
        await conn.execute(
            "INSERT INTO public.eventi_cliente_pubblicazione (id, ultima_posizione) VALUES (1, "
            "(SELECT COALESCE(max(posizione), 0) FROM public.eventi_cliente)) "
            "ON CONFLICT (id) DO UPDATE SET ultima_posizione = EXCLUDED.ultima_posizione")
        tempi["eventi_cliente"] = time.monotonic() - t0
        _log(f"eventi_cliente: {n}")
        t0 = time.monotonic()
        if r04:
            n = await _copia(conn, "webhook_consegne",
                             ["tenant_id", "sottoscrizione_id", "evento_id", "evento_creato_il",
                              "stato", "tentativi", "consegnata_il", "creata_il"],
                             ((t, s, e, q, "CONSEGNATA", 1, q, q) for t, s, e, q in consegne),
                             par.lotto)
        else:
            n = await conn.fetchval(
                "WITH c AS (INSERT INTO public.webhook_consegne (tenant_id, sottoscrizione_id, "
                "evento_id, stato, tentativi, consegnata_il, creata_il) "
                "SELECT e.tenant_id, s.id, e.id, 'CONSEGNATA', 1, e.creato_il, e.creato_il "
                "FROM public.eventi_cliente e JOIN public.webhook_sottoscrizioni s "
                "ON s.tenant_id = e.tenant_id WHERE e.tipo = 'versamento.conservato' "
                "RETURNING 1) SELECT count(*) FROM c")
        tempi["webhook_consegne"] = time.monotonic() - t0
        _log(f"webhook_consegne: {n}")

        # Outbox: ultime 48 ore, 2% in attesa (arretrato di un'interruzione di Kafka)
        t0 = time.monotonic()

        def righe_outbox():
            for i in range(par.outbox):
                quando = adesso - timedelta(seconds=(par.outbox - i) * 172_800 / par.outbox)
                pid, ente, _r, _s = piano[rnd.randrange(len(piano))]
                pubblicato = None if i >= par.outbox * 0.98 else quando + timedelta(seconds=1)
                payload = json.dumps({"pdv_id": str(pid), "tenant_id": str(ente)})
                base = (uuid.uuid4(), ente, "pdv.received", str(pid), payload, quando,
                        pubblicato, 1 if pubblicato else 0)
                if r04:
                    yield base + ("gateway", rnd.randrange(64))
                else:
                    yield base

        colonne_ob = ["id", "tenant_id", "topic", "chiave", "payload", "creato_il",
                      "pubblicato_il", "tentativi"] + (["origine", "gruppo"] if r04 else [])
        n = await _copia(conn, "outbox_eventi", colonne_ob, righe_outbox(), par.lotto)
        tempi["outbox_eventi"] = time.monotonic() - t0
        _log(f"outbox_eventi: {n}")

        t0 = time.monotonic()

        def righe_audit():
            for i in range(par.audit):
                pid, ente, ricevuto, _s = piano[(i * 7919) % len(piano)]
                yield (uuid.uuid4(), ente, utente, "versamento.ricevuto",
                       json.dumps({"pdv_id": str(pid)}), ricevuto)

        n = await _copia(conn, "audit_global", ["id", "tenant_id", "user_id", "evento",
                                                "dettagli", "created_at"], righe_audit(),
                         par.lotto)
        tempi["audit_global"] = time.monotonic() - t0
        _log(f"audit_global: {n}")
        t0 = time.monotonic()
        if r04:
            # i trigger dei contatori hanno già scritto le variazioni: si consolidano
            while True:
                presi = await conn.fetchval(
                    "WITH presi AS (DELETE FROM public.contatori_ente_delta WHERE id IN ("
                    "SELECT id FROM public.contatori_ente_delta ORDER BY id LIMIT 200000) "
                    "RETURNING tenant_id, metrica, giorno, delta), s AS (INSERT INTO "
                    "public.contatori_ente (tenant_id, metrica, giorno, valore) SELECT tenant_id, "
                    "metrica, giorno, sum(delta) FROM presi GROUP BY 1, 2, 3 ON CONFLICT "
                    "(tenant_id, metrica, giorno) DO UPDATE SET valore = public.contatori_ente."
                    "valore + EXCLUDED.valore RETURNING 1) SELECT count(*) FROM presi")
                if not presi:
                    break
        await conn.execute("VACUUM (ANALYZE)")
        tempi["vacuum_analyze"] = time.monotonic() - t0
        dimensioni = {r["t"]: r["b"] for r in await conn.fetch(
            "SELECT c.relname AS t, pg_total_relation_size(c.oid) AS b FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
            "AND c.relkind IN ('r', 'p') ORDER BY 2 DESC LIMIT 12")}
        return {"schema_r04": r04, "tempi_secondi": tempi, "dimensioni_byte": dimensioni}
    finally:
        await conn.close()


# ── Misure ──────────────────────────────────────────────────────────────────

@dataclass
class Query:
    nome: str
    gruppo: str
    sql: list[tuple[str, list[Any]]]   # più istruzioni = tempi sommati (es. 6 conteggi)
    scrive: bool = False


def _piano_tempo(piano: Any) -> tuple[float, dict[str, int]]:
    radice = piano[0]
    p = radice["Plan"]
    buf = {k: int(p.get(k, 0)) for k in ("Shared Hit Blocks", "Shared Read Blocks")}
    # «Execution Time» comprende già il tempo dei trigger (contatori)
    return float(radice["Execution Time"]), buf


async def _esegui(conn: asyncpg.Connection, q: Query,
                 ) -> tuple[float, dict[str, int], str, list[float]]:
    totale = 0.0
    dettaglio: list[float] = []
    buffer = {"Shared Hit Blocks": 0, "Shared Read Blocks": 0}
    testo_piano = ""
    tr = conn.transaction()
    await tr.start()
    try:
        for istruzione, argomenti in q.sql:
            if istruzione.lstrip().upper().startswith(("SELECT PG_ADVISORY", "SET ")):
                await conn.execute(istruzione, *argomenti)
                continue
            righe = await conn.fetchval(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + istruzione, *argomenti)
            piano = json.loads(righe) if isinstance(righe, str) else righe
            t, b = _piano_tempo(piano)
            totale += t
            dettaglio.append(round(t, 3))
            for k in buffer:
                buffer[k] += b[k]
            nodo = piano[0]["Plan"]
            testo_piano += f"{nodo['Node Type']}" + (f" ({nodo['Relation Name']})"
                                                     if "Relation Name" in nodo else "") + "; "
    finally:
        await tr.rollback()
    return totale, buffer, testo_piano, dettaglio


async def _campioni(conn: asyncpg.Connection) -> dict[str, Any]:
    grande = await conn.fetchval("SELECT tenant_id FROM public.pdv GROUP BY tenant_id "
                                 "ORDER BY count(*) DESC LIMIT 1")
    piccolo = await conn.fetchval("SELECT id FROM public.tenants WHERE id <> $1 AND "
                                  "codice_fiscale LIKE 'SINT%' ORDER BY codice_fiscale DESC "
                                  "LIMIT 1", grande)
    pdv = await conn.fetchrow("SELECT id, tenant_id FROM public.pdv WHERE tenant_id = $1 "
                              "ORDER BY data_ricezione DESC OFFSET 1000 LIMIT 1", grande)
    cursore = await conn.fetchrow("SELECT data_ricezione, id FROM public.pdv WHERE tenant_id = $1 "
                                  "ORDER BY data_ricezione DESC, id DESC OFFSET 5000 LIMIT 1",
                                  grande)
    cursore_aip = await conn.fetchrow("SELECT data_versamento, id FROM public.aip "
                                      "WHERE tenant_id = $1 ORDER BY data_versamento DESC, "
                                      "id DESC OFFSET 5000 LIMIT 1", grande)
    pos = await conn.fetchval("SELECT posizione FROM public.eventi_cliente WHERE tenant_id = $1 "
                              "AND posizione IS NOT NULL ORDER BY posizione DESC OFFSET 20000 "
                              "LIMIT 1", grande)
    classe = await conn.fetchval("SELECT id FROM public.classi_documentali "
                                 "WHERE tenant_id IS NULL LIMIT 1")
    return {"grande": grande, "piccolo": piccolo, "pdv": pdv["id"], "cursore": cursore,
            "cursore_aip": cursore_aip, "posizione": pos or 0, "classe": classe}


def _query(r04: bool, c: dict[str, Any]) -> list[Query]:
    nuovo_pdv = uuid.uuid4()
    adesso = datetime.now(UTC)
    mese = adesso.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    q: list[Query] = []
    outbox_insert = (
        ("INSERT INTO public.outbox_eventi (id, origine, gruppo, tenant_id, topic, chiave, "
         "payload) VALUES ($1, 'gateway', (hashtext($2) & 63)::smallint, $3, 'pdv.received', "
         "$2, '{}'::jsonb)", [uuid.uuid4(), str(nuovo_pdv), c["grande"]])
        if r04 else
        ("INSERT INTO public.outbox_eventi (id, tenant_id, topic, chiave, payload) "
         "VALUES ($1, $3, 'pdv.received', $2, '{}'::jsonb)",
         [uuid.uuid4(), str(nuovo_pdv), c["grande"]]))
    q.append(Query("inserimento del versamento (PdV + impronte + outbox)", "versamento", [
        ("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", [f"v:{nuovo_pdv}"]),
        ("SELECT pdv_id FROM public.versamento_impronte WHERE tenant_id = $1 AND "
         "(chiave_idempotenza = $2 OR (impronta_indice = $3 AND attivo))",
         [c["grande"], f"k-{nuovo_pdv}", "f" * 64]),
        ("INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, tipo_flusso, user_id, "
         "tenant_id, metadata_json, errori_validazione, s3_prefix, regole_applicate, avvisi) "
         "VALUES ($1, 'x', 'RICEVUTO', $2, 'ATTIVO', $3, $4, '{}'::jsonb, '[]'::jsonb, 'p', "
         "'{}'::jsonb, '[]'::jsonb)", [nuovo_pdv, c["classe"], uuid.uuid4(), c["grande"]]),
        ("INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
         "impronta_richiesta, chiave_idempotenza) VALUES ($1, $2, $3, $3, $4)",
         [nuovo_pdv, c["grande"], "e" * 64, f"k-{nuovo_pdv}"]),
        outbox_insert,
    ], scrive=True))
    q.append(Query("stato del PdV (GET /versamento/{id})", "stato", [(
        "SELECT p.id, p.stato::text AS stato, p.data_ricezione, p.data_aggiornamento, "
        "p.errori_validazione, p.metadata_json, c.codice AS classe, a.id AS aip_id, a.urn "
        "FROM public.pdv p LEFT JOIN public.classi_documentali c ON c.id = p.classe_doc_id "
        "LEFT JOIN public.aip a ON a.pdv_id = p.id WHERE p.id = $1 AND p.tenant_id = $2 "
        "ORDER BY a.created_at DESC NULLS LAST LIMIT 1", [c["pdv"], c["grande"]])]))
    q.append(Query("elenco versamenti a cursore (ente grande, pagina dopo 5.000)", "elenchi", [(
        "SELECT p.id, p.stato, p.data_ricezione, p.oggetto, (SELECT a.id FROM public.aip a "
        "WHERE a.pdv_id = p.id ORDER BY a.created_at DESC LIMIT 1) AS aip_id FROM public.pdv p "
        "WHERE p.tenant_id = $1 AND (p.data_ricezione, p.id) < ($2, $3) "
        "ORDER BY p.data_ricezione DESC, p.id DESC LIMIT 51",
        [c["grande"], c["cursore"]["data_ricezione"], c["cursore"]["id"]])]))
    q.append(Query("elenco PdA a cursore (ente grande)", "elenchi", [(
        "SELECT a.id, a.urn, a.stato, a.data_versamento FROM public.aip a WHERE a.tenant_id = $1 "
        "AND (a.data_versamento, a.id) < ($2, $3) ORDER BY a.data_versamento DESC, a.id DESC "
        "LIMIT 51", [c["grande"], c["cursore_aip"]["data_versamento"], c["cursore_aip"]["id"]])]))
    q.append(Query("eventi a cursore (GET /eventi, ente grande)", "eventi", [(
        "SELECT id, tipo, risorsa_tipo, risorsa_id, creato_il, dati, posizione "
        "FROM public.eventi_cliente WHERE tenant_id = $1 AND posizione IS NOT NULL "
        "AND posizione > $2 ORDER BY posizione LIMIT 101", [c["grande"], c["posizione"]])]))
    q.append(Query("eventi a cursore (ente piccolo, dall'inizio)", "eventi", [(
        "SELECT id, tipo, posizione FROM public.eventi_cliente WHERE tenant_id = $1 "
        "AND posizione IS NOT NULL AND posizione > 0 ORDER BY posizione LIMIT 101",
        [c["piccolo"]])]))
    if r04:
        from libs.shared.outbox import _chiave_lock

        q.append(Query("relay outbox: lotto di 500 (gruppi + FOR UPDATE SKIP LOCKED)", "relay", [
            ("WITH candidati AS MATERIALIZED (SELECT DISTINCT p.gruppo AS g FROM ("
             "SELECT gruppo FROM public.outbox_eventi WHERE origine = 'gateway' "
             "AND pubblicato_il IS NULL ORDER BY sequenza LIMIT 2000) p), mescolati AS "
             "MATERIALIZED (SELECT g FROM candidati ORDER BY random()) SELECT g FROM mescolati "
             "WHERE pg_try_advisory_xact_lock($1, g) LIMIT 16", [_chiave_lock("gateway")]),
            ("SELECT id, creato_il, sequenza, gruppo, topic, chiave, payload "
             "FROM public.outbox_eventi WHERE origine = 'gateway' AND pubblicato_il IS NULL "
             "AND gruppo = ANY($1::smallint[]) ORDER BY sequenza LIMIT 500 "
             "FOR UPDATE SKIP LOCKED", [list(range(16))]),
        ], scrive=True))
    else:
        q.append(Query("relay outbox: lotto di 500 (FOR UPDATE SKIP LOCKED)", "relay", [(
            "SELECT id, topic, chiave, payload FROM public.outbox_eventi "
            "WHERE pubblicato_il IS NULL ORDER BY sequenza LIMIT 500 FOR UPDATE SKIP LOCKED",
            [])], scrive=True))
    if r04:
        unione = ("SELECT tenant_id, metrica, giorno, valore FROM public.contatori_ente {f} "
                  "UNION ALL SELECT tenant_id, metrica, giorno, delta FROM "
                  "public.contatori_ente_delta {f}")
        q.append(Query("cruscotto: PdA per stato dell'ente (contatori)", "cruscotto", [(
            "SELECT metrica, sum(valore) FROM (" + unione.format(  # noqa: S608 — frammenti costanti
                f="WHERE tenant_id = $1 AND metrica LIKE 'aip.stato.%'") + ") u GROUP BY metrica",
            [c["grande"]])]))
        q.append(Query("cruscotto: throughput 90 giorni (contatori)", "cruscotto", [(
            "SELECT giorno, sum(valore) FROM (" + unione.format(  # noqa: S608 — frammenti costanti
                f="WHERE metrica = 'aip.versati' AND giorno >= $1") + ") u GROUP BY giorno "
            "ORDER BY giorno", [(adesso - timedelta(days=90)).date()])]))
        q.append(Query("cruscotto: totale PdA di tutti gli enti (contatori)", "cruscotto", [(
            "SELECT sum(valore) FROM (" + unione.format(f="WHERE metrica LIKE 'aip.stato.%'")  # noqa: S608 — frammenti costanti
            + ") u", [])]))
        q.append(Query("cruscotto: elenchi di consistenza per ente (contatori)", "cruscotto", [(
            "SELECT tenant_id, sum(valore) FILTER (WHERE metrica LIKE 'aip.stato.%'), "  # noqa: S608 — frammenti costanti
            "sum(valore) FILTER (WHERE metrica = 'documenti.versati'), sum(valore) FILTER "
            "(WHERE metrica = 'documenti.byte') FROM (" + unione.format(
                f="WHERE metrica LIKE 'aip.stato.%' OR metrica IN ('documenti.versati', "
                  "'documenti.byte')") + ") u GROUP BY tenant_id", [])]))
    else:
        # come GET /api/dashboard/overview: un COUNT per ogni stato di AipStato
        q.append(Query("cruscotto: PdA per stato dell'ente (8 COUNT)", "cruscotto", [
            ("SELECT count(id) FROM public.aip WHERE stato = $1 AND tenant_id = $2",
             [s, c["grande"]]) for s in ("IN_BUILD", "FIRMATO", "TIMESTAMPATO", "SU_STORAGE",
                                         "IN_CATENA", "CONSERVATO", "ESIBIZIONE",
                                         "ELIMINAZIONE")]))
        q.append(Query("cruscotto: throughput 90 giorni (GROUP BY su aip)", "cruscotto", [(
            "SELECT date_trunc('day', data_versamento) AS g, count(id) FROM public.aip "
            "WHERE data_versamento >= $1 GROUP BY 1 ORDER BY 1", [adesso - timedelta(days=90)])]))
        q.append(Query("cruscotto: totale PdA di tutti gli enti (COUNT)", "cruscotto", [(
            "SELECT count(id) FROM public.aip", [])]))
        q.append(Query("cruscotto: elenchi di consistenza per ente (COUNT/SUM)", "cruscotto", [(
            "SELECT t.ragione_sociale, (SELECT COUNT(*) FROM public.aip a WHERE a.tenant_id = "
            "t.id), (SELECT COUNT(*) FROM public.documenti d JOIN public.aip a ON a.id = d.aip_id "
            "WHERE a.tenant_id = t.id), (SELECT COALESCE(SUM(d.dimensione_bytes), 0) FROM "
            "public.documenti d JOIN public.aip a ON a.id = d.aip_id WHERE a.tenant_id = t.id) "
            "FROM public.tenants t ORDER BY t.ragione_sociale", [])]))
    q.append(Query("SLA: percentile 99 della conservazione nel mese", "cruscotto", [(
        "SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM "
        "(a.data_conservazione - p.data_ricezione)) * 1000), count(a.id) FROM public.aip a "
        "JOIN public.pdv p ON p.id = a.pdv_id WHERE a.data_conservazione >= $1 AND "
        "a.data_conservazione < $2 AND a.stato = 'CONSERVATO'",
        [mese, (mese + timedelta(days=32)).replace(day=1)])]))
    return q


async def misura(dsn: str, etichetta: str, uscita: Path, ripetizioni: int) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        r04 = await schema_r04(conn)
        await conn.execute("SET jit = off")
        campioni = await _campioni(conn)
        risultati = []
        for q in _query(r04, campioni):
            tempi = []
            buffer: dict[str, int] = {}
            descrizione = ""
            dettaglio: list[float] = []
            for _ in range(ripetizioni):
                t, buffer, descrizione, dettaglio = await _esegui(conn, q)
                tempi.append(t)
            risultati.append({"etichetta": etichetta, "schema_r04": r04, "gruppo": q.gruppo,
                              "query": q.nome, "ms_mediana": round(statistics.median(tempi), 3),
                              "ms_tutte": [round(t, 3) for t in tempi],
                              "ms_per_istruzione_ultima": dettaglio, **buffer,
                              "nodo": descrizione})
            _log(f"{q.nome}: {statistics.median(tempi):.3f} ms")
        conteggi = {t: await conn.fetchval(f"SELECT count(*) FROM public.{t}")  # noqa: S608 — nomi costanti
                    for t in ("pdv", "aip", "documenti", "eventi_cliente", "webhook_consegne",
                              "outbox_eventi", "audit_global", "versamento_impronte")}
    finally:
        await conn.close()
    uscita.mkdir(parents=True, exist_ok=True)
    (uscita / f"misure_{etichetta}.json").write_text(
        json.dumps({"righe": conteggi, "misure": risultati}, indent=2, default=str))
    righe_md = [f"| {r['query']} | {r['ms_mediana']} | {r['Shared Hit Blocks']} | "
                f"{r['Shared Read Blocks']} |" for r in risultati]
    (uscita / f"misure_{etichetta}.md").write_text(
        f"Righe: {conteggi}\n\n| Query | ms (mediana di {ripetizioni}) | buffer hit | "
        "buffer letti |\n|---|---|---|---|\n" + "\n".join(righe_md) + "\n")
    return risultati


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sotto = ap.add_subparsers(dest="comando", required=True)
    r = sotto.add_parser("riempi", help="genera i dati sintetici con COPY")
    r.add_argument("--dsn", required=True)
    r.add_argument("--enti", type=int, default=200)
    r.add_argument("--pdv", type=int, default=1_000_000)
    r.add_argument("--documenti-per-pdv", type=int, default=10)
    r.add_argument("--eventi-per-pdv", type=int, default=2)
    r.add_argument("--audit", type=int, default=1_000_000)
    r.add_argument("--outbox", type=int, default=300_000)
    r.add_argument("--giorni", type=int, default=90)
    r.add_argument("--lotto", type=int, default=20_000)
    r.add_argument("--seme", type=int, default=42)
    r.add_argument("--uscita", type=Path, default=None)
    m = sotto.add_parser("misura", help="EXPLAIN (ANALYZE, BUFFERS) delle query calde")
    m.add_argument("--dsn", required=True)
    m.add_argument("--etichetta", required=True)
    m.add_argument("--uscita", type=Path, required=True)
    m.add_argument("--ripetizioni", type=int, default=3)
    a = ap.parse_args(argv)
    if a.comando == "riempi":
        inizio = time.monotonic()
        esito = asyncio.run(riempi(a.dsn, Parametri(
            a.enti, a.pdv, a.documenti_per_pdv, a.eventi_per_pdv, a.audit, a.outbox, a.giorni,
            a.lotto, a.seme)))
        esito["totale_secondi"] = round(time.monotonic() - inizio, 1)
        _log(json.dumps(esito, default=str))
        if a.uscita:
            a.uscita.mkdir(parents=True, exist_ok=True)
            (a.uscita / "riempimento.json").write_text(json.dumps(esito, indent=2, default=str))
        return 0
    asyncio.run(misura(a.dsn, a.etichetta, a.uscita, a.ripetizioni))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
