# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/services/indicizzazione.py — lavoratore dell'indicizzazione.

Riferimenti: R03 b4 (VD-C-093); piano R03–R05 §4.2; REGOLE_SVILUPPO §12.2
(effetto e conferma insieme), §12.4 (ritentativi con lista costante, poi stato
d'errore consultabile), §19.9 (dati sensibili fuori dall'indice).

La coda è ``public.indicizzazione_aip``: aip-builder vi scrive una riga nella
stessa transazione del PdA (nessun evento perso). Un ciclo:

  1. prende fino a ``INDICIZZAZIONE_LOTTO`` righe IN_CODA scadute con
     ``FOR UPDATE SKIP LOCKED`` (più repliche lavorano su righe diverse);
  2. legge in TRE query i dati dei PdA del lotto (PdA + PdV + classe, nomi dei
     file e metadati Allegato 5, profili per i metadati sensibili) — mai una
     query per PdA;
  3. una chiamata bulk a OpenSearch (id = id del PdA: ripetere è innocuo);
  4. nella stessa transazione del punto 1: INDICIZZATO per le riuscite;
     ``tentativi + 1`` e prossimo tentativo secondo
     ``INDICIZZAZIONE_RITARDI_SECONDI`` per le fallite; esauriti i ritardi la
     riga resta in ERRORE con ``ultimo_errore`` (si rimette in coda con
     ``rimetti_in_coda``).
La transazione resta aperta solo per la durata della bulk (timeout breve).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.services.search_index import RicercaNonDisponibile, documento_ricerca, indicizza_lotto
from libs.shared.db import get_async_session
from libs.shared.db.ambito import con_ambito_conservatore

logger = structlog.get_logger(__name__)


def _json(valore: Any) -> Any:
    if isinstance(valore, str):
        try:
            return json.loads(valore)
        except ValueError:
            return None
    return valore


def nomi_sensibili(campi: Any) -> frozenset[str]:
    """Nomi dei metadati marcati ``sensibile`` nei campi di un profilo."""
    campi = _json(campi) or []
    return frozenset(str(c.get("nome")) for c in campi
                     if isinstance(c, dict) and c.get("sensibile") and c.get("nome"))


def prossimo_tentativo(tentativi: int, adesso: datetime,
                       ritardi: Sequence[int]) -> datetime | None:
    """Istante del prossimo tentativo dopo ``tentativi`` falliti; None = finiti."""
    if tentativi > len(ritardi):
        return None
    return adesso + timedelta(seconds=int(ritardi[tentativi - 1]))


async def _dati_lotto(session: Any, ids: list[uuid.UUID]) -> dict[str, dict]:
    aip = (await session.execute(text(
        # PdA + PdV (chiave e oggetto, profilo applicato) + classe: un join per lotto
        "SELECT a.id, a.pdv_id, a.tenant_id, a.urn, a.stato::text AS stato, a.hash_sha256, "
        "       a.data_versamento, a.data_conservazione, a.scadenza_conservazione, "
        "       a.anni_conservazione, a.dimensione_bytes, p.oggetto, "
        "       p.metadata_json -> 'chiave' AS chiave, "
        "       p.regole_applicate -> 'profilo_metadati' ->> 'id' AS profilo_id, "
        "       c.codice AS classe "
        "FROM public.aip a JOIN public.pdv p ON p.id = a.pdv_id "
        "LEFT JOIN public.classi_documentali c ON c.id = p.classe_doc_id "
        "WHERE a.id = ANY(:ids)"
    ), {"ids": ids})).mappings().all()
    documenti = (await session.execute(text(
        "SELECT d.aip_id, d.nome_file, m.identificativo_documento, m.tipologia_documentale, "
        "       m.oggetto, m.soggetto_produttore_nome, m.numero_registrazione, "
        "       m.classificazione, m.fascicolo_id, m.data_documento, m.metadati_extra "
        "FROM public.documenti d LEFT JOIN public.metadati_allegato5 m ON m.documento_id = d.id "
        "WHERE d.aip_id = ANY(:ids) ORDER BY d.aip_id, d.ordine, d.id"
    ), {"ids": ids})).mappings().all()
    profili_ids = []
    for r in aip:
        try:
            profili_ids.append(uuid.UUID(str(r["profilo_id"])))
        except (TypeError, ValueError):
            continue
    profili: dict[str, frozenset[str]] = {}
    if profili_ids:
        for r in (await session.execute(text(
            "SELECT id, campi FROM public.profili_metadati WHERE id = ANY(:ids)"
        ), {"ids": profili_ids})).mappings().all():
            profili[str(r["id"])] = nomi_sensibili(r["campi"])

    per_aip: dict[str, dict] = {}
    for r in aip:
        per_aip[str(r["id"])] = {
            "aip": {
                "aip_id": str(r["id"]), "pdv_id": str(r["pdv_id"]),
                "tenant_id": str(r["tenant_id"]), "urn": r["urn"], "stato": r["stato"],
                "classe_documentale": r["classe"], "hash_sha256": r["hash_sha256"],
                "data_versamento": r["data_versamento"],
                "data_conservazione": r["data_conservazione"],
                "scadenza_conservazione": r["scadenza_conservazione"],
                "anni_conservazione": r["anni_conservazione"],
                "dimensione_bytes": r["dimensione_bytes"], "oggetto": r["oggetto"],
                "chiave": _json(r["chiave"]) or {}, "nomi_file": [],
            },
            "metadati": [],
            "sensibili": profili.get(str(r["profilo_id"]), frozenset()),
        }
    for d in documenti:
        voce = per_aip.get(str(d["aip_id"]))
        if voce is None:
            continue
        voce["aip"]["nomi_file"].append(d["nome_file"])
        if d["identificativo_documento"] is not None:
            m = dict(d)
            m["metadati_extra"] = _json(m.get("metadati_extra")) or {}
            voce["metadati"].append(m)
    return per_aip


@con_ambito_conservatore("indicizzazione: lotto della coda di tutti gli enti")
async def elabora_lotto(*, limite: int | None = None, adesso: datetime | None = None) -> dict:
    """Un lotto della coda. Restituisce ``{"indicizzati", "falliti", "in_errore"}``."""
    ora = adesso or datetime.now(UTC)
    ritardi = list(settings.indicizzazione_ritardi_secondi)
    esito = {"indicizzati": 0, "falliti": 0, "in_errore": 0}
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT aip_id, tentativi FROM public.indicizzazione_aip "
            "WHERE stato = 'IN_CODA' AND prossimo_tentativo <= :ora "
            "ORDER BY prossimo_tentativo, aip_id LIMIT :limite FOR UPDATE SKIP LOCKED"
        ), {"ora": ora, "limite": limite or settings.indicizzazione_lotto})).all()
        if not righe:
            return esito
        ids = [r[0] for r in righe]
        dati = await _dati_lotto(session, ids)
        documenti = [documento_ricerca(v["aip"], v["metadati"], sensibili=v["sensibili"])
                     for v in dati.values()]
        try:
            errori = await indicizza_lotto(documenti)
        except RicercaNonDisponibile as exc:
            errori = {d["aip_id"]: f"OpenSearch non disponibile: {exc}"[:300] for d in documenti}
        for aip_id in ids:
            if str(aip_id) not in dati:
                errori[str(aip_id)] = "PdA non trovato nel database"
        riusciti = [aip_id for aip_id, _ in righe if str(aip_id) not in errori]
        if riusciti:  # una sola UPDATE per tutte le riuscite del lotto
            await session.execute(text(
                "UPDATE public.indicizzazione_aip SET stato = 'INDICIZZATO', "
                "indicizzato_il = :ora, ultimo_errore = NULL WHERE aip_id = ANY(:ids)"
            ), {"ora": ora, "ids": riusciti})
            esito["indicizzati"] = len(riusciti)
        for aip_id, tentativi in righe:
            errore = errori.get(str(aip_id))
            if errore is None:
                continue
            nuovi = int(tentativi) + 1
            prossimo = prossimo_tentativo(nuovi, ora, ritardi)
            await session.execute(text(
                "UPDATE public.indicizzazione_aip SET tentativi = :t, ultimo_errore = :e, "
                "stato = :stato, prossimo_tentativo = COALESCE(:prossimo, prossimo_tentativo) "
                "WHERE aip_id = :id"
            ), {"t": nuovi, "e": errore, "stato": "IN_CODA" if prossimo else "ERRORE",
                "prossimo": prossimo, "id": aip_id})
            esito["falliti"] += 1
            if prossimo is None:
                esito["in_errore"] += 1
                logger.error("indicizzazione_in_errore", aip_id=str(aip_id), errore=errore)
    if esito["indicizzati"] or esito["falliti"]:
        logger.info("indicizzazione_lotto", **esito)
    return esito


@con_ambito_conservatore("indicizzazione: rimessa in coda (operazione del conservatore)")
async def rimetti_in_coda(aip_ids: Sequence[uuid.UUID] | None = None) -> int:
    """Rimette in coda le righe in ERRORE (tutte o quelle indicate): «Rielabora»."""
    async with get_async_session() as session:
        risultato = await session.execute(text(
            "UPDATE public.indicizzazione_aip SET stato = 'IN_CODA', tentativi = 0, "
            "prossimo_tentativo = now() WHERE stato = 'ERRORE' "
            "AND (CAST(:tutti AS boolean) OR aip_id = ANY(:ids))"
        ), {"tutti": not aip_ids, "ids": list(aip_ids or [])})
    return int(risultato.rowcount or 0)


async def ciclo_indicizzazione(fine: asyncio.Event) -> None:
    """Ciclo continuo (task del processo o processo dedicato)."""
    while not fine.is_set():
        try:
            esito = await elabora_lotto()
            pieno = esito["indicizzati"] + esito["falliti"] >= settings.indicizzazione_lotto
        except Exception as exc:  # noqa: BLE001 — il ciclo non muore: errore nel log e si riprova
            logger.error("indicizzazione_ciclo_errore", errore=type(exc).__name__,
                         messaggio=str(exc)[:300])
            pieno = False
        if not pieno:
            try:
                await asyncio.wait_for(fine.wait(),
                                       timeout=settings.indicizzazione_intervallo_secondi)
            except TimeoutError:
                pass
