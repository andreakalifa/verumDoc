# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Ricostruzione del registro delle impronte dal WORM (R05, VD-C-111).

Servizio: hashchain. Comando versionato (REGOLE §16.7):

    python -m app.registro.operatore ricostruisci-da-worm --fino-a <n> --prova
    python -m app.registro.operatore ricostruisci-da-worm --fino-a <n> \\
        --operatore "Mario Rossi" --motivo "ripristino del <data>: …"

Quando serve: il database è tornato a un backup e nessun ripristino point-in-time
copre la soglia ``max(N, X, T)`` (runbook ``ripristino_registro_da_backup.md``,
§4 B.1). Il WORM (Object Lock COMPLIANCE) contiene, per costruzione, tutto ciò
che il registro ha pubblicato: le foglie in segmenti ``registro/foglie/<da>-<a>.jsonl``
e i checkpoint firmati ``registro/checkpoint/<n>.txt`` (con controfirma e sigillo).

Cosa fa, a LOTTI (memoria costante: frontiera O(log n) e un lotto di foglie):
  1. BASE: l'ultimo checkpoint del database non oltre ``D`` la cui nota è
     identica a quella nel WORM, firmata dalla chiave del registro, con l'origine
     attesa e la radice uguale a quella dei nodi del database. Da lì si legge il
     WORM (senza base: da 0). Così un database indietro di poco non rilegge 10
     milioni di foglie.
  2. Legge i segmenti dalla base in ordine, contigui (un buco o una
     sovrapposizione incoerente è un errore), calcola hash di foglia e nodi con
     ``libs.catena.albero`` (gli stessi del sequenziatore);
  3. le foglie già nel database (sotto ``D``) devono avere lo stesso hash; a ``D``
     la radice calcolata deve essere quella dei nodi del database;
  4. ogni checkpoint nel WORM fino a ``--fino-a``: firma del registro (chiave
     attuale o ``--chiave-precedente``), origine, radice = radice ricalcolata;
     controfirma del testimone verificata con ``TESTIMONE_VERIFICATORE``;
     revisione R05 (R5): si ricostruiscono SOLO foglie coperte da un checkpoint
     firmato E controfirmato (``ultimo_checkpoint_controfirmato``). Le foglie
     oltre (un segmento che nessuna firma copre può averlo depositato chiunque
     scriva nel bucket) sono un PROBLEMA: il comando si ferma senza scrivere, o
     con ``scarta_coda_non_coperta`` (``--scarta-coda-non-coperta``) ricostruisce
     fino a quel checkpoint e le scarta SENZA scriverle. Mai una foglia non
     verificata nel registro, quindi mai firmata dal checkpoint successivo;
  5. collegamenti: foglia di un PdA → ``aip`` (URN, ente, radice del PdA o impronta
     del manifest); foglia del ciclo di vita → PdA e richiesta interna (ricreata
     dalla foglia se il database l'ha persa, stesso ``id``); foglia dell'audit →
     richiesta interna ed esportazione dell'audit.
Una qualunque di queste cose che non torna è un PROBLEMA: il comando non scrive
nulla (mai una ricostruzione «quasi giusta»: trasformerebbe un registro fermo in
una biforcazione firmata). Con ``--prova`` si fanno tutti i controlli e non si
scrive nulla, nemmeno il lease.

Scrittura (senza ``--prova`` e senza problemi nella prima lettura): una seconda
lettura identica scrive a lotti, ognuno in UNA transazione con il lease del
registro (il ciclo della hashchain non può scrivere insieme): foglie, nodi,
richieste interne, esportazioni dell'audit, righe dei checkpoint ricostruiti
(sigillo: FIRMATO se la busta è nel WORM, altrimenti DA_FIRMARE; testimone:
CONTROFIRMATO se la controfirma è nel WORM e verificata), stato del registro,
PdA ``IN_CATENA`` e coda. Un'interruzione lascia un prefisso verificato: si
riesegue e riprende da ``D``. Due voci in ``audit_global`` (avvio e fine).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import socket
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.registro import archivio, archivio_worm
from app.registro.interne import chiave_audit, chiave_ciclo_vita
from libs.catena import albero, nota
from libs.catena.foglie import (
    TIPO_AUDIT,
    TIPO_PDA,
    TIPO_RIVERSAMENTO,
    FogliaNonValida,
    FogliaRegistro,
    analizza_foglia_registro,
)
from libs.catena.rfc6962 import hash_foglia
from libs.shared.db import get_async_session
from libs.shared.storage import worm

logger = structlog.get_logger(__name__)

#: Versione del comando (REGOLE §16.7): cambia se cambia il comportamento
VERSIONE = "1"
LOTTO_PREDEFINITO = 10000
LETTURE_PARALLELE = 8
#: Problemi e avvisi conservati per il rapporto (gli altri solo contati)
ELENCO_MASSIMO = 200
LEASE_RICOSTRUZIONE_SECONDI = 600
_SEGMENTO = re.compile(r"registro/foglie/(\d{12})-(\d{12})\.jsonl$")


class RicostruzioneInterrotta(RuntimeError):
    """Qualcosa non torna: nessuna (ulteriore) scrittura."""


@dataclass
class CheckpointWorm:
    dimensione: int
    testo: str
    controfirmato: str | None
    sigillo: bytes | None
    scritto_il: datetime | None


@dataclass
class Rapporto:
    """Esito del comando (stampato dall'operatore)."""

    prova: bool
    fino_a: int
    dimensione_db: int = 0
    base: int = 0
    foglie_lette: int = 0
    foglie_nuove: int = 0
    foglie_scritte: int = 0
    checkpoint_verificati: int = 0
    ultimo_checkpoint: int = 0
    #: R5: ultimo checkpoint firmato E controfirmato (limite della ricostruzione)
    ultimo_checkpoint_controfirmato: int = 0
    #: R5: foglie oltre la copertura scartate senza scriverle (opzione esplicita)
    coda_scartata: int = 0
    checkpoint_ricostruiti: int = 0
    controfirme_verificate: int = 0
    collegamenti: dict[str, int] = field(default_factory=dict)
    interne_ricreate: int = 0
    esportazioni_audit_ricreate: int = 0
    fine_segmenti: int = 0
    problemi: list[str] = field(default_factory=list)
    avvisi: list[str] = field(default_factory=list)
    problemi_totali: int = 0
    avvisi_totali: int = 0

    @property
    def riuscito(self) -> bool:
        return not self.problemi

    def problema(self, testo: str) -> None:
        self.problemi_totali += 1
        if len(self.problemi) < ELENCO_MASSIMO:  # memoria costante anche con 10M foglie
            self.problemi.append(testo)
        if self.problemi_totali == 1:
            logger.error("ricostruzione_problema", problema=testo)

    def avviso(self, testo: str) -> None:
        self.avvisi_totali += 1
        if len(self.avvisi) < ELENCO_MASSIMO:
            self.avvisi.append(testo)

    def righe(self) -> list[str]:
        campi = ("prova", "fino_a", "dimensione_db", "base", "fine_segmenti", "foglie_lette",
                 "foglie_nuove", "foglie_scritte", "checkpoint_verificati", "ultimo_checkpoint",
                 "ultimo_checkpoint_controfirmato", "coda_scartata",
                 "checkpoint_ricostruiti", "controfirme_verificate", "interne_ricreate",
                 "esportazioni_audit_ricreate")
        righe = [f"versione\t{VERSIONE}"] + [f"{c}\t{getattr(self, c)}" for c in campi]
        righe.append(f"collegamenti\t{json.dumps(self.collegamenti, sort_keys=True)}")
        righe += [f"AVVISO\t{a}" for a in self.avvisi]
        if self.avvisi_totali > len(self.avvisi):
            righe.append(f"AVVISO\t… altri {self.avvisi_totali - len(self.avvisi)}")
        righe += [f"PROBLEMA\t{p}" for p in self.problemi]
        if self.problemi_totali > len(self.problemi):
            righe.append(f"PROBLEMA\t… altri {self.problemi_totali - len(self.problemi)}")
        return righe


# ── Lettura del WORM (in thread, a finestra scorrevole) ──────────────────────

def _client_e_bucket() -> tuple[Any, str]:
    from app.services.block_sealer import _client

    return _client(), worm.bucket_conservativo(settings)


def _elenca_segmenti(client: Any, bucket: str, base: int) -> Iterator[tuple[int, int, str]]:
    """Segmenti che iniziano da ``base`` in poi, in ordine (paginati, mai tutti in memoria)."""
    pagine = client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{archivio_worm.PREFISSO}/foglie/",
        StartAfter=f"{archivio_worm.PREFISSO}/foglie/{base:012d}")
    for pagina in pagine:
        for oggetto in pagina.get("Contents", []):
            m = _SEGMENTO.search(oggetto["Key"])
            if m and int(m.group(1)) >= base:
                yield int(m.group(1)), int(m.group(2)), oggetto["Key"]


def _checkpoint_worm(client: Any, bucket: str, n: int) -> CheckpointWorm | None:
    """Checkpoint ``n`` nel WORM: UN elenco del prefisso ``<n>.`` trova nota,
    controfirma e busta del sigillo (mai una GET su una chiave assente: 403)."""
    prefisso = f"{archivio_worm.PREFISSO}/checkpoint/{n}."
    oggetti = {o["Key"]: o for o in client.list_objects_v2(
        Bucket=bucket, Prefix=prefisso).get("Contents", [])}
    chiave = archivio_worm.chiave_checkpoint(n)
    if chiave not in oggetti:
        return None

    def leggi(k: str) -> bytes | None:
        return worm.leggi_oggetto(client, bucket, k) if k in oggetti else None

    controfirmato = leggi(archivio_worm.chiave_controfirmato(n))
    return CheckpointWorm(n, worm.leggi_oggetto(client, bucket, chiave).decode("utf-8"),
                          controfirmato.decode("utf-8") if controfirmato else None,
                          leggi(archivio_worm.chiave_sigillo(n)),
                          oggetti[chiave].get("LastModified"))


@dataclass
class SegmentoLetto:
    da: int
    a: int
    chiave: str
    righe: list[str]
    checkpoint: CheckpointWorm | None


def _leggi_segmento(client: Any, bucket: str, da: int, a: int, chiave: str,
                    fino_a: int) -> SegmentoLetto:
    contenuto = worm.leggi_oggetto(client, bucket, chiave).decode("utf-8")
    righe = contenuto.split("\n")
    if righe and righe[-1] == "":
        righe.pop()
    cp = _checkpoint_worm(client, bucket, a) if a <= fino_a else None
    return SegmentoLetto(da, a, chiave, righe, cp)


def _segmenti_letti(base: int, fino_a: int) -> Iterator[SegmentoLetto]:
    """Segmenti dalla base fino a coprire ``fino_a``, letti in parallelo con una
    finestra di ``LETTURE_PARALLELE`` (memoria limitata, ordine preservato)."""
    client, bucket = _client_e_bucket()
    finestra: list[Future[SegmentoLetto]] = []
    with ThreadPoolExecutor(max_workers=LETTURE_PARALLELE) as pool:
        elenco = _elenca_segmenti(client, bucket, base)
        finito = False
        while True:
            while not finito and len(finestra) < LETTURE_PARALLELE:
                prossimo = next(elenco, None)
                if prossimo is None or prossimo[0] >= fino_a:
                    finito = True
                    break
                finestra.append(pool.submit(_leggi_segmento, client, bucket, *prossimo, fino_a))
            if not finestra:
                return
            yield finestra.pop(0).result()


# ── Database ─────────────────────────────────────────────────────────────────

async def _checkpoint_db(session: Any, n: int) -> dict[str, Any] | None:
    return await archivio.ultimo_checkpoint(session, dimensione=n)


def _verifica_nota(testo: str, n: int, radice: bytes, verificatori: list[nota.Verificatore],
                   origine: str) -> str | None:
    """None se la nota del checkpoint ``n`` è firmata dal registro, con l'origine
    attesa e la radice ``radice``; altrimenti il motivo."""
    try:
        cp = nota.analizza(nota.separa(testo)[0])
        firme = nota.verifica(testo, verificatori)
    except nota.ErroreNota as exc:
        return f"checkpoint {n}: nota non valida ({exc})"
    if cp.dimensione != n:
        return f"checkpoint {n}: la nota dichiara la dimensione {cp.dimensione}"
    if cp.origine != origine:
        return f"checkpoint {n}: origine {cp.origine}, attesa {origine}"
    if not any(f.nome == "verumdoc-registro" for f in firme):
        return f"checkpoint {n}: firma del registro non valida"
    if cp.radice != radice:
        return f"checkpoint {n}: radice diversa da quella ricalcolata dalle foglie del WORM"
    return None


@dataclass
class _Contesto:
    """Parametri della ricostruzione."""

    fino_a: int
    prova: bool
    lotto: int
    verificatori: list[nota.Verificatore]
    testimone: nota.Verificatore | None
    origine: str
    detentore: str
    operatore: str
    motivo: str
    #: ultimo checkpoint verificato nella prima lettura (stato del testimone)
    ultimo_checkpoint: int = 0


@dataclass
class _Lotto:
    """Foglie nuove (indice ≥ D) da scrivere in UNA transazione."""

    foglie: list[tuple[int, str, bytes, FogliaRegistro]] = field(default_factory=list)
    nodi: dict[albero.Coordinata, bytes] = field(default_factory=dict)
    checkpoint: list[tuple[CheckpointWorm, bytes, str | None]] = field(default_factory=list)
    fine_segmenti: int = 0


class _Ricostruzione:
    def __init__(self, ctx: _Contesto, rapporto: Rapporto) -> None:
        self.ctx = ctx
        self.r = rapporto
        self.frontiera: dict[albero.Coordinata, bytes] = {}
        self.posizione = 0
        self.ultimo_segmento: SegmentoLetto | None = None

    # -- base e confronto con il database --

    async def base(self, d: int) -> int:
        """Ultimo checkpoint del database ≤ D identico al WORM e verificato."""
        if d == 0:
            return 0
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT dimensione, testo FROM public.catena_checkpoint WHERE dimensione <= :d "
                "ORDER BY dimensione DESC LIMIT 1"), {"d": d})).mappings().first()
            if riga is None:
                return 0
            n = int(riga["dimensione"])
            nodi = await archivio.leggi_nodi(session, albero.nodi_per_radice(n))
        client, bucket = _client_e_bucket()
        cp = await asyncio.to_thread(_checkpoint_worm, client, bucket, n)
        try:
            radice = albero.radice_da_nodi(n, nodi)
        except albero.NodoMancante:
            self.r.avviso(f"base: nodi del checkpoint {n} incompleti nel database, "
                                 "lettura del WORM da 0")
            return 0
        motivo = None if cp is not None else f"checkpoint {n} del database assente nel WORM"
        if cp is not None and cp.testo != str(riga["testo"]):
            motivo = f"checkpoint {n}: nota del database diversa da quella nel WORM"
        motivo = motivo or _verifica_nota(cp.testo, n, radice, self.ctx.verificatori,
                                          self.ctx.origine)
        if motivo is not None:
            self.r.avviso(f"base: {motivo}; lettura del WORM da 0")
            return 0
        self.frontiera = {c: nodi[c] for c in albero.coordinate_frontiera(n)}
        self.posizione = n
        return n

    async def _hash_db(self, da: int, a: int) -> list[bytes]:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                "SELECT hash FROM public.catena_registro WHERE indice >= :da AND indice < :a "
                "ORDER BY indice"), {"da": da, "a": a})).scalars().all()
        return [bytes(h) for h in righe]

    async def _radice_db(self, n: int) -> bytes | None:
        async with get_async_session() as session:
            nodi = await archivio.leggi_nodi(session, albero.nodi_per_radice(n))
        try:
            return albero.radice_da_nodi(n, nodi)
        except albero.NodoMancante:
            return None

    # -- collegamenti --

    async def _collega(self, foglie: list[tuple[int, str, bytes, FogliaRegistro]]
                       ) -> dict[str, dict[str, Any]]:
        """Righe ``aip`` per URN (con radice del PdA e foglia già nel registro) e
        richieste interne per id, per le foglie nuove; registra i problemi."""
        urn = sorted({f.pda for _, _, _, f in foglie if f.pda})
        ids = sorted({str(f.dati["id"]) for _, _, _, f in foglie if f.versione == 2})
        async with get_async_session() as session:
            aip = {r["urn"]: dict(r) for r in (await session.execute(text(
                # join aip → albero del PdA e foglia già nel registro (doppioni)
                "SELECT a.id, a.tenant_id, a.urn, a.hash_sha256, c.radice, "
                "r.indice AS indice_registro FROM public.aip a "
                "LEFT JOIN public.catena_pda c ON c.aip_id = a.id "
                "LEFT JOIN public.catena_registro r ON r.aip_id = a.id "
                "WHERE a.urn = ANY(CAST(:urn AS varchar[]))"), {"urn": urn})).mappings().all()}
            interne = {str(r["id"]): dict(r) for r in (await session.execute(text(
                "SELECT id, dati, indice FROM public.catena_foglie_interne "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [uuid.UUID(i) for i in ids]})).mappings().all()}
        for indice, testo, _, f in foglie:
            self.r.collegamenti[f.tipo] = self.r.collegamenti.get(f.tipo, 0) + 1
            if f.pda is not None:
                riga = aip.get(f.pda)
                if riga is None:
                    self.r.problema(f"foglia {indice} ({f.tipo}): PdA {f.pda} assente dal "
                                    "database")
                    continue
                if str(f.dati.get("ente")) != str(riga["tenant_id"]):
                    self.r.problema(f"foglia {indice}: ente della foglia diverso da quello "
                                    f"del PdA {f.pda}")
                if f.tipo in (TIPO_PDA, TIPO_RIVERSAMENTO):
                    if riga["indice_registro"] is not None and \
                            int(riga["indice_registro"]) != indice:
                        self.r.problema(f"foglia {indice}: il PdA {f.pda} è già nel registro "
                                        f"alla foglia {riga['indice_registro']}")
                    if f.tipo == TIPO_PDA and riga["radice"] is None:
                        self.r.avviso(f"foglia {indice}: albero del PdA {f.pda} assente "
                                             "dal database (catena_pda)")
                    elif f.tipo == TIPO_PDA and riga["radice"] != f.dati["radice_pda"]:
                        self.r.problema(f"foglia {indice}: radice del PdA {f.pda} diversa da "
                                        "quella del database")
                    if f.tipo == TIPO_RIVERSAMENTO and \
                            riga["hash_sha256"] != f.dati["impronta_pda"]:
                        self.r.problema(f"foglia {indice}: impronta del PdA {f.pda} diversa "
                                        "da quella del database")
            if f.versione == 2:
                esistente = interne.get(str(f.dati["id"]))
                if esistente is None:
                    continue  # si ricrea dalla foglia
                if esistente["dati"] != testo:
                    self.r.problema(f"foglia {indice}: richiesta interna {f.dati['id']} con "
                                    "dati diversi")
                elif esistente["indice"] is not None and int(esistente["indice"]) != indice:
                    self.r.problema(f"foglia {indice}: richiesta interna {f.dati['id']} già "
                                    f"collegata alla foglia {esistente['indice']}")
        return {"aip": aip, "interne": interne}

    # -- scorrimento --

    async def scorri(self, d: int, scrivi: bool) -> None:
        """Legge i segmenti dalla base fino a ``fino_a`` (vedi modulo). Con
        ``scrivi`` interrompe al primo problema; senza, li raccoglie tutti."""
        segmenti = _segmenti_letti(self.posizione, self.ctx.fino_a)
        try:
            await self._scorri(d, scrivi, segmenti)
        finally:
            await asyncio.to_thread(segmenti.close)

    async def _scorri(self, d: int, scrivi: bool, segmenti: Iterator[SegmentoLetto]) -> None:
        fino_a = self.ctx.fino_a
        lotto = _Lotto()
        while self.posizione < fino_a:
            seg = await asyncio.to_thread(next, segmenti, None)
            if seg is None:
                break
            righe = self._righe_nuove(seg)
            if righe is None:
                break
            inizio = self.posizione
            righe = righe[: fino_a - inizio]
            hash_ = [hash_foglia(x.encode("utf-8")) for x in righe]
            interpretate: list[FogliaRegistro | None] = []
            for i, testo in enumerate(righe):
                try:
                    interpretate.append(analizza_foglia_registro(testo))
                except FogliaNonValida as exc:
                    self.r.problema(f"foglia {inizio + i}: {exc}")
                    interpretate.append(None)
            # foglie già nel database: stesso hash
            fine_db = min(d, inizio + len(righe))
            if inizio < fine_db and \
                    await self._hash_db(inizio, fine_db) != hash_[:fine_db - inizio]:
                self.r.problema(f"foglie {inizio}-{fine_db}: diverse da quelle del database")
            frontiera_inizio = self.frontiera
            nodi = albero.aggiungi_foglie(inizio, frontiera_inizio, hash_)
            disponibili = {**frontiera_inizio, **nodi}
            self.posizione = inizio + len(righe)
            self.frontiera = {c: disponibili[c]
                              for c in albero.coordinate_frontiera(self.posizione)}
            self.r.foglie_lette += len(righe)
            self.r.fine_segmenti = max(self.r.fine_segmenti, seg.a)
            if 0 < d and inizio < d <= self.posizione:
                # la radice dei nodi del database a D deve essere quella delle foglie del WORM
                if await self._radice_db(d) != albero.radice_da_nodi(d, disponibili):
                    self.r.problema(f"radice del database a {d} diversa da quella delle foglie "
                                    "del WORM (nodi del database incompleti o storia diversa)")
            nuove = [(inizio + i, righe[i], hash_[i], f) for i, f in enumerate(interpretate)
                     if inizio + i >= d and f is not None]
            lotto.foglie.extend(nuove)
            # nodi nuovi = quelli che finiscono oltre D (gli altri sono già nel database)
            lotto.nodi.update({c: h for c, h in nodi.items() if (c[1] + 1) << c[0] > d})
            self.r.foglie_nuove += len(nuove)
            if seg.a == self.posizione:
                lotto.fine_segmenti = seg.a
                if seg.checkpoint is not None:
                    await self._checkpoint(seg.checkpoint, lotto)
            if scrivi and not self.r.riuscito:
                raise RicostruzioneInterrotta(self.r.problemi[0])
            if len(lotto.foglie) >= self.ctx.lotto:
                await self._chiudi_lotto(lotto, scrivi)
                lotto = _Lotto()
        if self.posizione < fino_a:
            self.r.problema(f"il WORM contiene foglie contigue solo fino a {self.posizione}: "
                            f"--fino-a {fino_a} non è ricostruibile")
        # R5: le foglie oltre l'ultimo checkpoint firmato e controfirmato non si
        # scrivono mai (controllo in ``ricostruisci`` dopo la prima lettura; qui
        # la seconda lettura lo ripete: nessuna scrittura oltre la copertura)
        if scrivi and self.posizione > max(d, self.r.ultimo_checkpoint_controfirmato):
            self.r.problema(f"foglie {max(d, self.r.ultimo_checkpoint_controfirmato)}-"
                            f"{self.posizione} non coperte da un checkpoint firmato e "
                            "controfirmato: non si scrivono")
        if scrivi and not self.r.riuscito:
            raise RicostruzioneInterrotta(self.r.problemi[0])
        await self._chiudi_lotto(lotto, scrivi)

    def _righe_nuove(self, seg: SegmentoLetto) -> list[str] | None:
        """Righe del segmento oltre la posizione corrente; controlla il numero di
        righe, la contiguità e le sovrapposizioni (un segmento riscritto dopo un
        errore di aggiornamento del database ricomincia dallo stesso punto: le
        righe in comune devono essere identiche a quelle del segmento precedente)."""
        if len(seg.righe) != seg.a - seg.da:
            self.r.problema(f"segmento {seg.chiave}: {len(seg.righe)} righe, attese "
                            f"{seg.a - seg.da}")
            return None
        if seg.da > self.posizione:
            self.r.problema(f"buco nel WORM: dopo la foglia {self.posizione} il segmento "
                            f"successivo inizia da {seg.da}")
            return None
        if seg.da < self.posizione:
            prec = self.ultimo_segmento
            comuni = seg.righe[: self.posizione - seg.da]
            if prec is None or seg.da < prec.da or \
                    prec.righe[seg.da - prec.da: self.posizione - prec.da] != comuni:
                self.r.problema(f"segmento {seg.chiave}: sovrapposto a foglie già lette con "
                                "contenuto diverso o non verificabile")
                return None
        self.ultimo_segmento = seg
        return seg.righe[self.posizione - seg.da:]

    async def _checkpoint(self, cp: CheckpointWorm, lotto: _Lotto) -> None:
        """Firma, origine, radice e controfirma di un checkpoint del WORM; se il
        database non l'ha, si ricostruisce con il lotto."""
        n = cp.dimensione
        radice = albero.radice_da_nodi(n, self.frontiera)
        motivo = _verifica_nota(cp.testo, n, radice, self.ctx.verificatori, self.ctx.origine)
        if motivo is not None:
            self.r.problema(motivo)
            return
        self.r.checkpoint_verificati += 1
        self.r.ultimo_checkpoint = max(self.r.ultimo_checkpoint, n)
        riga_controfirma = None
        if cp.controfirmato is not None:
            if self.ctx.testimone is None:
                self.r.problema(f"checkpoint {n}: controfirma nel WORM ma TESTIMONE_VERIFICATORE "
                                "non configurato: non verificabile")
                return
            try:
                corpo, righe = nota.separa(cp.controfirmato)
                valida = corpo == nota.separa(cp.testo)[0] and bool(nota.verifica(
                    cp.controfirmato, [self.ctx.testimone]))
            except nota.ErroreNota:
                valida, righe = False, []
            if not valida:
                self.r.problema(f"checkpoint {n}: controfirma del testimone non valida")
                return
            riga_controfirma = next(r.riga for r in righe if r.nome == self.ctx.testimone.nome
                                    and r.key_hash == self.ctx.testimone.key_hash)
            self.r.controfirme_verificate += 1
            self.r.ultimo_checkpoint_controfirmato = max(self.r.ultimo_checkpoint_controfirmato,
                                                         n)
        async with get_async_session() as session:
            nel_db = await _checkpoint_db(session, n)
        if nel_db is not None:
            if str(nel_db["testo"]) != cp.testo:
                self.r.problema(f"checkpoint {n}: nota del database diversa da quella del WORM")
            return
        lotto.checkpoint.append((cp, radice, riga_controfirma))

    # -- scrittura --

    async def _chiudi_lotto(self, lotto: _Lotto, scrivi: bool) -> None:
        if not lotto.foglie and not lotto.checkpoint:
            return
        doppi = _pda_doppi(lotto.foglie)
        for urn in doppi:
            self.r.problema(f"il PdA {urn} compare in più foglie di conservazione")
        collegamenti = await self._collega(lotto.foglie)
        if not scrivi:
            return
        if not self.r.riuscito:
            raise RicostruzioneInterrotta(self.r.problemi[0])
        await _scrivi_lotto(self.ctx, self.r, lotto, collegamenti)


def _pda_doppi(foglie: list[tuple[int, str, bytes, FogliaRegistro]]) -> list[str]:
    visti: set[str] = set()
    doppi: list[str] = []
    for _, _, _, f in foglie:
        if f.prova_conservazione and f.pda is not None:
            if f.pda in visti:
                doppi.append(f.pda)
            visti.add(f.pda)
    return doppi


async def _scrivi_lotto(ctx: _Contesto, r: Rapporto, lotto: _Lotto,
                        collegamenti: dict[str, dict[str, Any]]) -> None:
    """UNA transazione: lease, foglie, nodi, richieste interne, export dell'audit,
    checkpoint, stato, PdA e coda."""
    adesso = datetime.now(UTC)
    aip = collegamenti["aip"]
    interne_esistenti = collegamenti["interne"]
    bucket = worm.bucket_conservativo(settings)
    async with get_async_session() as session:
        stato = await archivio.stato_con_lease(session, ctx.detentore, adesso)
        if lotto.foglie and stato.dimensione != lotto.foglie[0][0]:
            raise RicostruzioneInterrotta(f"il registro è cambiato durante la ricostruzione "
                                          f"(dimensione {stato.dimensione}, attesa "
                                          f"{lotto.foglie[0][0]})")
        await session.execute(text(
            "UPDATE public.catena_stato SET lease_scade_il = :scade WHERE id = 1"),
            {"scade": adesso + timedelta(seconds=LEASE_RICOSTRUZIONE_SECONDI)})
        righe_registro: list[dict[str, Any]] = []
        interne_nuove: list[dict[str, Any]] = []
        collega: list[tuple[Any, int]] = []
        esportazioni: list[dict[str, Any]] = []
        for indice, testo, h, f in lotto.foglie:
            riga_aip = aip.get(f.pda) if f.pda else None
            if f.versione == 1:
                righe_registro.append({
                    "indice": indice, "aip_id": riga_aip["id"], "tenant_id": riga_aip["tenant_id"],
                    "tipo": f.tipo.upper(), "dati": testo, "hash": h,
                    "conservato_il": _istante(f.dati.get("conservato_il"), adesso)})
                continue
            fid = uuid.UUID(str(f.dati["id"]))
            ente = riga_aip["tenant_id"] if riga_aip else None
            righe_registro.append({
                "indice": indice, "aip_id": None, "tenant_id": ente, "tipo": f.tipo.upper(),
                "dati": testo, "hash": h,
                "conservato_il": _istante(f.dati.get("registrato_il"), adesso)})
            if str(fid) in interne_esistenti:
                if interne_esistenti[str(fid)]["indice"] is None:
                    collega.append((fid, indice))
            else:
                if f.tipo == TIPO_AUDIT:
                    chiave, pratica, rif, impronta = chiave_audit(str(f.dati["segmento"])), \
                        None, {}, None
                else:
                    rif = dict(f.dati["riferimento"])
                    pratica = str(rif["pratica"])
                    chiave = chiave_ciclo_vita(f.tipo, str(f.pda), pratica)
                    impronta = str(f.dati["impronta_documento_pratica"])
                interne_nuove.append({
                    "id": fid, "tipo": f.tipo.upper(), "chiave": chiave,
                    "aip": riga_aip["id"] if riga_aip else None, "ente": ente, "pda": f.pda,
                    "pratica": pratica, "riferimento": json.dumps(rif), "impronta": impronta,
                    "dati": testo, "hash": h, "creata_il": _istante(f.dati["registrato_il"],
                                                                   adesso),
                    "indice": indice})
            if f.tipo == TIPO_AUDIT:
                esportazioni.append({
                    "da": _istante(f.dati["periodo_da"], adesso),
                    "a": _istante(f.dati["periodo_a"], adesso), "righe": int(f.dati["righe"]),
                    "radice": f.dati["radice_righe"], "impronta": f.dati["impronta_segmento"],
                    "precedente": f.dati["impronta_precedente"], "bucket": bucket,
                    "chiave": f.dati["segmento"], "foglia": fid,
                    "esportato_il": _istante(f.dati["registrato_il"], adesso)})
        await archivio.scrivi_nodi(session, lotto.nodi)
        await archivio.scrivi_foglie(session, righe_registro)
        for riga in interne_nuove:
            await session.execute(text(
                "INSERT INTO public.catena_foglie_interne (id, tipo, chiave, aip_id, tenant_id, "
                "pda, pratica, riferimento, impronta, dati, hash, richiesta_da, creata_il, indice, "
                "sequenziata_il) VALUES (:id, :tipo, :chiave, :aip, :ente, :pda, :pratica, "
                "CAST(:riferimento AS jsonb), :impronta, :dati, :hash, 'ricostruzione-worm', "
                ":creata_il, :indice, :adesso)"), {**riga, "adesso": adesso})
        await archivio.collega_foglie_interne(session, collega, adesso)
        for e in esportazioni:
            inserita = (await session.execute(text(
                "INSERT INTO public.audit_esportazioni (periodo_da, periodo_a, righe, "
                "radice_righe, impronta_segmento, impronta_precedente, worm_bucket, worm_chiave, "
                "foglia_id, esportato_il) VALUES (:da, :a, :righe, :radice, :impronta, "
                ":precedente, :bucket, :chiave, :foglia, :esportato_il) "
                "ON CONFLICT (periodo_da) DO NOTHING RETURNING periodo_da"), e)).first()
            r.esportazioni_audit_ricreate += 1 if inserita else 0
        r.interne_ricreate += len(interne_nuove)
        fine = lotto.foglie[-1][0] + 1 if lotto.foglie else stato.dimensione
        controfirmata = stato.testimone_dimensione
        testimone_configurato = bool(settings.testimone_url)
        for cp, radice, controfirma in lotto.checkpoint:
            n = cp.dimensione
            # Senza controfirma nel WORM: l'ultimo checkpoint ricostruito torna IN_ATTESA
            # (il ciclo lo invia al testimone), i precedenti SUPERATO
            if controfirma:
                stato_testimone = "CONTROFIRMATO"
                controfirmata = max(controfirmata or 0, n)
            elif not testimone_configurato:
                stato_testimone = "NON_CONFIGURATO"
            else:
                stato_testimone = "IN_ATTESA" if n == ctx.ultimo_checkpoint else "SUPERATO"
            sigillo = base64.b64encode(cp.sigillo).decode() if cp.sigillo else None
            await session.execute(text(
                "INSERT INTO public.catena_checkpoint (dimensione, radice, origine, testo, "
                "chiave_verificatore, emesso_il, worm_bucket, worm_chiave, stato_sigillo, "
                "motivo_sigillo, sigillo_cades, sigillo_worm_chiave, stato_testimone, "
                "controfirma, controfirmato_il, esito_testimone) VALUES (:n, :radice, :origine, "
                ":testo, :chiave, :emesso, :bucket, :worm_chiave, :sigillo_stato, :motivo, "
                ":sigillo, :sigillo_chiave, :testimone, :controfirma, :controfirmato_il, :esito)"
            ), {"n": n, "radice": radice.hex(), "origine": ctx.origine, "testo": cp.testo,
                "chiave": ctx.verificatori[0].testo, "emesso": cp.scritto_il or adesso,
                "bucket": bucket, "worm_chiave": archivio_worm.chiave_checkpoint(n),
                "sigillo_stato": "FIRMATO" if sigillo else "DA_FIRMARE",
                "motivo": "ricostruito dal WORM (data della marca nella busta)" if sigillo
                else None, "sigillo": sigillo,
                "sigillo_chiave": archivio_worm.chiave_sigillo(n) if sigillo else None,
                "testimone": stato_testimone, "controfirma": controfirma,
                "controfirmato_il": (cp.scritto_il or adesso) if controfirma else None,
                "esito": "ricostruito dal WORM"})
            r.checkpoint_ricostruiti += 1
        esportate = max(stato.worm_foglie_esportate, min(lotto.fine_segmenti, fine))
        await session.execute(text(
            "UPDATE public.catena_stato SET dimensione = :n, worm_foglie_esportate = :e, "
            "testimone_dimensione = :t, aggiornato_il = :adesso WHERE id = 1"
        ), {"n": fine, "e": esportate, "t": controfirmata, "adesso": adesso})
        aip_ids = [riga["aip_id"] for riga in righe_registro if riga["aip_id"] is not None]
        await session.execute(text(
            "UPDATE public.aip SET stato = 'IN_CATENA', updated_at = now() "
            "WHERE id = ANY(CAST(:ids AS uuid[])) AND stato = 'SU_STORAGE' "
            "AND hashchain_block_id IS NULL"), {"ids": aip_ids})
        await session.execute(text(
            "DELETE FROM public.catena_coda WHERE aip_id = ANY(CAST(:ids AS uuid[])) "
            "OR foglia_interna_id = ANY(CAST(:interne AS uuid[]))"),
            {"ids": aip_ids, "interne": [x["id"] for x in interne_nuove] + [c[0] for c in collega]})
    r.foglie_scritte += len(lotto.foglie)
    logger.info("ricostruzione_lotto_scritto", fino_a=fine, foglie=len(lotto.foglie),
                checkpoint=len(lotto.checkpoint))


def _istante(valore: Any, ripiego: datetime) -> datetime:
    if not valore:
        return ripiego
    return datetime.fromisoformat(str(valore).replace("Z", "+00:00"))


# ── Comando ──────────────────────────────────────────────────────────────────

async def _audit(evento: str, dettagli: dict[str, Any]) -> None:
    from libs.shared.security.audit import registra_audit

    async with get_async_session() as session:
        await registra_audit(session, evento=evento, dettagli=dettagli)


async def ricostruisci(*, fino_a: int, prova: bool, operatore: str = "", motivo: str = "",
                       lotto: int = LOTTO_PREDEFINITO,
                       chiavi_precedenti: list[str] | None = None,
                       scarta_coda_non_coperta: bool = False) -> Rapporto:
    """Ricostruzione dal WORM (vedi modulo). Restituisce sempre il rapporto: con
    problemi, in prova o no, NON scrive nulla. Revisione R05 (R5): solo foglie
    coperte da un checkpoint firmato e controfirmato; oltre, problema (oppure,
    con ``scarta_coda_non_coperta``, ricostruzione fino a quel checkpoint e coda
    scartata senza scriverla)."""
    from app.registro.chiavi import firmatario_registro, origine_registro, verificatore_testimone

    r = Rapporto(prova=prova, fino_a=fino_a)
    if fino_a < 1 or lotto < 1:
        r.problema("--fino-a e --lotto devono essere positivi")
        return r
    verificatori = [firmatario_registro(settings).verificatore]
    for chiave in chiavi_precedenti or []:
        try:
            verificatori.append(nota.analizza_verificatore(chiave))
        except nota.ErroreNota as exc:
            r.problema(f"--chiave-precedente non valida: {exc}")
            return r
    ctx = _Contesto(fino_a, prova, lotto, verificatori, verificatore_testimone(settings),
                    origine_registro(settings),
                    f"ricostruzione:{socket.gethostname()}:{os.getpid()}"[:200],
                    operatore, motivo)
    async with get_async_session() as session:
        stato = await archivio.leggi_stato(session)
    d = r.dimensione_db = stato.dimensione
    if fino_a <= d:
        r.avviso(f"il database ha già {d} foglie: si verificano soltanto")

    # Prima lettura: tutti i controlli, nessuna scrittura
    prima = _Ricostruzione(ctx, r)
    r.base = await prima.base(d)
    await prima.scorri(d, scrivi=False)
    logger.info("ricostruzione_verificata", **{k: v for k, v in vars(r).items()
                                                if not isinstance(v, list | dict)},
                problemi=len(r.problemi))
    copertura = r.ultimo_checkpoint_controfirmato
    if r.riuscito and fino_a > max(d, copertura):
        coda = (f"foglie {max(d, copertura)}-{fino_a} del WORM non coperte da un checkpoint "
                "firmato dal registro e controfirmato dal testimone"
                + ("" if ctx.testimone is not None else
                   " (TESTIMONE_VERIFICATORE non configurato: nessuna controfirma verificabile)"))
        if not scarta_coda_non_coperta:
            r.problema(f"{coda}: non si ricostruiscono (mai una foglia non verificata nel "
                       f"registro). Usare --fino-a {copertura} se basta, oppure "
                       "--scarta-coda-non-coperta")
            return r
        if copertura <= d:
            r.avviso(f"{coda}: scartate senza scriverle; nulla da ricostruire oltre le {d} "
                     "foglie del database")
            r.coda_scartata = fino_a - max(d, copertura)
            return r
        ridotto = await ricostruisci(fino_a=copertura, prova=prova, operatore=operatore,
                                     motivo=motivo, lotto=lotto,
                                     chiavi_precedenti=chiavi_precedenti)
        ridotto.avviso(f"{coda}: scartate senza scriverle (--scarta-coda-non-coperta); "
                       f"ricostruzione fino al checkpoint {copertura}")
        ridotto.coda_scartata = fino_a - copertura
        return ridotto
    if prova or not r.riuscito or fino_a <= d:
        return r

    # Seconda lettura: scrittura a lotti con il lease
    adesso = datetime.now(UTC)
    async with get_async_session() as session:
        if not await archivio.acquisisci_lease(
                session, ctx.detentore, adesso,
                adesso + timedelta(seconds=LEASE_RICOSTRUZIONE_SECONDI)):
            r.problema("lease del registro detenuto dal ciclo della hashchain: sospendere il "
                       "registro (CATENA_REGISTRO_SOSPESO) e riprovare")
            return r
    await _audit("catena.ricostruzione_da_worm_avviata", {
        "versione": VERSIONE, "fino_a": fino_a, "dimensione_db": d, "base": r.base,
        "operatore": operatore[:200], "motivo": motivo[:2000]})
    verificati, nuove = r.checkpoint_verificati, r.foglie_nuove
    ctx.ultimo_checkpoint = r.ultimo_checkpoint
    r.checkpoint_verificati = r.foglie_nuove = r.foglie_lette = 0
    r.collegamenti = {}
    seconda = _Ricostruzione(ctx, r)
    try:
        await seconda.base(d)
        await seconda.scorri(d, scrivi=True)
    except RicostruzioneInterrotta as exc:
        if not r.problemi:
            r.problema(str(exc))
    except Exception as exc:  # noqa: BLE001 — es. vincolo violato: prefisso già scritto valido
        r.problema(f"scrittura interrotta: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        async with get_async_session() as session:
            await archivio.rilascia_lease(session, ctx.detentore)
    if r.riuscito and (r.checkpoint_verificati, r.foglie_nuove) != (verificati, nuove):
        r.problema("la seconda lettura del WORM differisce dalla prima")
    await _audit("catena.ricostruzione_da_worm_conclusa", {
        "versione": VERSIONE, "fino_a": fino_a, "riuscita": r.riuscito,
        "foglie_scritte": r.foglie_scritte, "checkpoint_ricostruiti": r.checkpoint_ricostruiti,
        "problemi": r.problemi[:20], "operatore": operatore[:200], "motivo": motivo[:2000]})
    return r
