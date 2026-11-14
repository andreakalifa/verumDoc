# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Rinnovo del blocco WORM degli oggetti del registro (R12a, VD-C-148).

Servizio: hashchain (l'UNICO con il permesso di retention su ``registro/``).
Riferimenti: decisione D04 di R09 (rinnovo dei PdA permanenti nel lavoratore di
ciclo-vita), revisione R09 Z4/Z5, REGOLE_SVILUPPO §10.2, §12.9.

Gli oggetti ``registro/`` (segmenti delle foglie, checkpoint, controfirme,
sigilli, export dell'audit) si scrivono con l'orizzonte permanente
(``RETENTION_PERMANENTE_ANNI``, legal hold attivo). Il passo li tiene bloccati:

  - ELENCO: ``registro_worm_oggetti`` (chiave, versione, termine del blocco).
    Ogni scrittura nuova vi entra (``archivio_worm.scrivi``); gli oggetti scritti
    prima di R12a (o da un processo con il codice precedente durante il deploy)
    li aggiunge il CENSIMENTO, che scorre ``registro/`` a pagine con un cursore
    persistente e si ripete ogni ``CENSIMENTO_WORM_GIORNI``;
  - RINNOVO: gli oggetti senza termine noto o con il termine prima di adesso +
    ``RETENTION_RINNOVO_SOGLIA_ANNI`` si estendono fino ad adesso +
    ``RETENTION_PERMANENTE_ANNI``. MAI in riduzione, con due guardie
    indipendenti: il filtro della selezione (solo termini prima della soglia, e
    il nuovo termine deve superare la soglia: Z4) e la rilettura nello storage
    (``worm.estendi_retention`` non tocca un termine già pari o successivo);
  - Z4: una configurazione che non permette il rinnovo (anni ≤ soglia) fa
    fallire il passo (non risulta fatto, metrica ``worm_rinnovo_in_errore`` e
    allarme ``RegistroWormRinnovoInErrore``);
  - Z5: un oggetto che non si rinnova non ferma il lotto (``elementi_in_errore``
    con allarme); gli oggetti NULL non affamano quelli in scadenza (due code).

Il passo gira nel ciclo del registro, con il lease del registro (una sola
replica), anche quando il registro è bloccato da un controllo di coerenza (il
rinnovo protegge, non scrive contenuti); non gira con il registro sospeso.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.registro.metriche import metriche
from libs.shared.db import get_async_session
from libs.shared.storage import worm

logger = structlog.get_logger(__name__)

PREFISSO = "registro/"
PAGINA_CENSIMENTO = 1000


class RinnovoNonPossibile(RuntimeError):
    """Z4: la configurazione non permette il rinnovo (anni ≤ soglia)."""


def _client() -> Any:
    from app.services.block_sealer import _client as client

    return client()


# ── Elenco degli oggetti ─────────────────────────────────────────────────

async def registra(chiave: str, version_id: str | None, conserva_fino_a: datetime | None
                   ) -> None:
    """Un oggetto del registro scritto (o ritrovato): entra nell'elenco. Il termine
    registrato non scende mai (GREATEST)."""
    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.registro_worm_oggetti (chiave, version_id, conserva_fino_a) "
            "VALUES (:c, :v, :f) ON CONFLICT (chiave, version_id) DO UPDATE SET "
            "conserva_fino_a = GREATEST(public.registro_worm_oggetti.conserva_fino_a, "
            "EXCLUDED.conserva_fino_a), aggiornato_il = now()"),
            {"c": chiave, "v": version_id or "", "f": conserva_fino_a})


def _pagina(client: Any, bucket: str, dopo: str | None) -> tuple[list[str], bool]:
    argomenti: dict[str, Any] = {"Bucket": bucket, "Prefix": PREFISSO,
                                 "MaxKeys": PAGINA_CENSIMENTO}
    if dopo:
        argomenti["StartAfter"] = dopo
    risposta = client.list_objects_v2(**argomenti)
    chiavi = [str(o["Key"]) for o in risposta.get("Contents") or ()]
    return chiavi, bool(risposta.get("IsTruncated"))


async def censisci(adesso: datetime, *, pagine: int = 20) -> dict[str, Any]:
    """Aggiunge all'elenco gli oggetti ``registro/`` che non ci sono (termine NULL:
    lo legge il rinnovo). Cursore persistente; ripreso dal punto in cui era."""
    client, bucket = _client(), worm.bucket_conservativo(settings)
    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.registro_worm_censimento (id) VALUES (1) "
            "ON CONFLICT (id) DO NOTHING"))
        stato = (await session.execute(text(
            "SELECT ultima_chiave, completato_il FROM public.registro_worm_censimento "
            "WHERE id = 1"))).mappings().one()
    completato = stato["completato_il"]
    if completato is not None and completato > adesso - timedelta(
            days=settings.censimento_worm_giorni):
        return {"censimento": "non dovuto"}
    dopo = None if completato is not None else stato["ultima_chiave"]
    aggiunti = letti = 0
    finito = False
    for _ in range(pagine):
        chiavi, altre = await asyncio.to_thread(_pagina, client, bucket, dopo)
        letti += len(chiavi)
        if chiavi:
            async with get_async_session() as session:
                r = await session.execute(text(
                    "INSERT INTO public.registro_worm_oggetti (chiave, version_id) "
                    "SELECT k, '' FROM unnest(CAST(:c AS text[])) AS k WHERE NOT EXISTS "
                    "(SELECT 1 FROM public.registro_worm_oggetti o WHERE o.chiave = k) "
                    "ON CONFLICT DO NOTHING"),
                    {"c": chiavi})
                aggiunti += int(r.rowcount or 0)
            dopo = chiavi[-1]
        if not altre:
            finito = True
            break
    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.registro_worm_censimento SET ultima_chiave = :u, "
            "completato_il = CASE WHEN CAST(:f AS boolean) THEN CAST(:adesso AS timestamptz) "
            "ELSE NULL END, "
            "aggiornato_il = :adesso WHERE id = 1"),
            {"u": None if finito else dopo, "f": finito, "adesso": adesso})
    return {"censimento": "completato" if finito else "in corso", "letti": letti,
            "aggiunti": aggiunti}


# ── Rinnovo ──────────────────────────────────────────────────────────────

def controlla_configurazione(anni: int, soglia_anni: int) -> None:
    """Z4: il nuovo termine deve superare la soglia (anni > soglia)."""
    if int(anni) <= int(soglia_anni):
        raise RinnovoNonPossibile(
            f"RETENTION_PERMANENTE_ANNI ({anni}) deve superare RETENTION_RINNOVO_SOGLIA_ANNI "
            f"({soglia_anni}): nessun rinnovo possibile per gli oggetti del registro")


def _rinnova_uno(client: Any, bucket: str, o: Mapping[str, Any], nuovo: datetime
                 ) -> datetime | None:
    return worm.estendi_retention(client, bucket, str(o["chiave"]), o["version_id"] or None,
                                  nuovo)


async def configurazione() -> tuple[int, int]:
    """(anni, soglia in anni) dal motore; ``RinnovoNonPossibile`` se anni ≤ soglia (Z4)."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import (
        RETENTION_PERMANENTE_ANNI,
        RETENTION_RINNOVO_SOGLIA_ANNI,
    )

    v = await parametri([RETENTION_PERMANENTE_ANNI, RETENTION_RINNOVO_SOGLIA_ANNI])
    anni, soglia_anni = int(v[RETENTION_PERMANENTE_ANNI]), int(v[RETENTION_RINNOVO_SOGLIA_ANNI])
    controlla_configurazione(anni, soglia_anni)
    return anni, soglia_anni


async def rinnova(adesso: datetime) -> dict[str, Any]:
    """Un passo di rinnovo (vedi modulo). Solleva ``RinnovoNonPossibile`` (Z4)."""
    anni, soglia_anni = await configurazione()
    soglia = worm.aggiungi_anni(adesso, soglia_anni)
    nuovo = worm.aggiungi_anni(adesso, anni)
    lotto = settings.rinnovo_worm_lotto
    async with get_async_session() as session:
        in_scadenza = (await session.execute(text(
            "SELECT chiave, version_id, conserva_fino_a FROM public.registro_worm_oggetti "
            "WHERE conserva_fino_a < :soglia ORDER BY conserva_fino_a, chiave LIMIT :lim"),
            {"soglia": soglia, "lim": lotto})).mappings().all()
        da_leggere = (await session.execute(text(
            "SELECT chiave, version_id, conserva_fino_a FROM public.registro_worm_oggetti "
            "WHERE conserva_fino_a IS NULL ORDER BY chiave LIMIT :lim"),
            {"lim": lotto})).mappings().all()
    client, bucket = _client(), worm.bucket_conservativo(settings)
    rinnovati, errori = 0, []
    for o in [*in_scadenza, *da_leggere]:
        # prima guardia: solo termini ignoti o prima della soglia
        if o["conserva_fino_a"] is not None and o["conserva_fino_a"] >= soglia:
            continue
        try:
            dopo = await asyncio.to_thread(_rinnova_uno, client, bucket, o, nuovo)
            if dopo is None:
                raise worm.ErroreStorageConservativo(
                    "oggetto del registro senza retention dopo il rinnovo",
                    bucket=bucket, chiave=o["chiave"])
        except Exception as exc:  # noqa: BLE001 — Z5: un oggetto non ferma il lotto
            logger.error("registro_worm_rinnovo_fallito", chiave=o["chiave"],
                         errore=type(exc).__name__, dettaglio=str(exc)[:300])
            errori.append(f"{o['chiave']}: {type(exc).__name__}")
            continue
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.registro_worm_oggetti SET conserva_fino_a = GREATEST("
                "conserva_fino_a, :f), rinnovato_il = :adesso, aggiornato_il = :adesso "
                "WHERE chiave = :c AND version_id = :v"),
                {"f": dopo, "adesso": adesso, "c": o["chiave"], "v": o["version_id"]})
        if o["conserva_fino_a"] is None or (dopo and o["conserva_fino_a"] < dopo):
            rinnovati += 1
    async with get_async_session() as session:
        restanti = int((await session.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM public.registro_worm_oggetti "
            "WHERE conserva_fino_a IS NULL OR conserva_fino_a < :soglia LIMIT 100000) x"),
            {"soglia": soglia})).scalar_one())
    m = metriche()
    m.worm_rinnovo_elementi_in_errore.set(len(errori))
    m.worm_in_scadenza.set(restanti)
    if errori:
        logger.error("registro_worm_rinnovo_elementi_in_errore", elementi=len(errori),
                     primi=errori[:5])
    return {"controllati": len(in_scadenza) + len(da_leggere), "rinnovati": rinnovati,
            "elementi_in_errore": len(errori), "errori": errori[:20],
            "in_scadenza_dopo": restanti, "fino_a": nuovo.isoformat()}


async def passo(adesso: datetime | None = None) -> dict[str, Any]:
    """Censimento (se dovuto) e rinnovo; la metrica ``worm_rinnovo_in_errore`` dice
    se il passo è riuscito (Z4: una configurazione impossibile NON risulta fatta)."""
    adesso = adesso or datetime.now(UTC)
    try:
        await configurazione()  # Z4 prima di ogni accesso allo storage
        esito = {"censimento": await censisci(adesso), **await rinnova(adesso)}
    except Exception:
        metriche().worm_rinnovo_in_errore.set(1)
        raise
    metriche().worm_rinnovo_in_errore.set(0)
    logger.info("registro_worm_rinnovo", **{k: str(v)[:200] for k, v in esito.items()})
    return esito


__all__ = ["RinnovoNonPossibile", "censisci", "configurazione", "controlla_configurazione",
           "passo", "registra",
           "rinnova"]
