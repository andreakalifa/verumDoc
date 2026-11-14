# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/stato_automatico.py — stato del lavoro automatico del ciclo di
vita e dei blocchi WORM per la console del conservatore (R09).

Riferimenti: decisioni D04 (stato della retention e rinnovi), D06, D20, D21;
REGOLE_SVILUPPO §9.6, §14 (solo i ruoli del conservatore: metadati di tutti gli
enti, nessun contenuto), §25.9 (esito in parole).

Letture costanti: righe di ``ciclo_vita_periodico`` (poche), il PdA permanente
con il blocco più vicino alla scadenza (indice parziale della migrazione 048),
rinnovi degli ultimi 30 giorni (indice degli esiti per PdA non usato: conteggio
con un tetto), soglia e anni dai parametri.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.application import parametri
from app.application import pratiche as pr
from app.application.periodico import SENZA_OGGETTI, STATI_CON_OGGETTI
from app.domain.permessi import Identita
from libs.shared.errori import ErroreApi
from libs.shared.storage.worm import aggiungi_anni
from libs.verumdoc_rules.parametri_servizio import (
    RETENTION_PERMANENTE_ANNI,
    RETENTION_RINNOVO_SOGLIA_ANNI,
)

#: Tetto dei conteggi mostrati (mai un COUNT senza limite su tabelle grandi)
TETTO = 10_000

_DESCRIZIONI = {
    "proposte_scarto": "Proposte di scarto automatiche",
    "riprese_scarto": "Ripresa degli scarti incompleti",
    "minimizzazione": "Riduzione dei dati dei pacchetti scartati",
    "rinnovo_retention": "Rinnovo dei blocchi WORM",
}


def _passo(r: dict[str, Any]) -> dict[str, Any]:
    nome = str(r["nome"])
    base = nome.split(":", 1)[0]
    esito = r["ultimo_esito"]
    if isinstance(esito, str):
        esito = json.loads(esito)
    return {"nome": nome, "descrizione": _DESCRIZIONI.get(base, base),
            "ente": nome.split(":", 1)[1] if ":" in nome else None,
            "ultimo_giro": r["ultimo_giro"], "ultimo_esito": esito or {},
            "in_corso": r["lease_scade_il"] is not None}


async def leggi(identita: Identita) -> dict[str, Any]:
    if not identita.conservatore:
        raise ErroreApi("LC-403-RUOLO", "Lo stato del lavoro automatico è del conservatore")
    from libs.shared.db import get_async_session

    adesso = pr.adesso_utc()
    v = await parametri.valori([RETENTION_PERMANENTE_ANNI, RETENTION_RINNOVO_SOGLIA_ANNI])
    soglia = aggiungi_anni(adesso, int(v[RETENTION_RINNOVO_SOGLIA_ANNI]))
    try:
        async with get_async_session() as session:
            passi = [_passo(dict(r)) for r in (await session.execute(text(
                "SELECT nome, ultimo_giro, ultimo_esito, lease_scade_il "
                "FROM public.ciclo_vita_periodico ORDER BY nome LIMIT 500"))).mappings()]
            prossima = (await session.execute(text(
                "SELECT retention_fino_a FROM public.aip WHERE anni_conservazione = 999 "
                "AND retention_fino_a IS NOT NULL ORDER BY retention_fino_a NULLS FIRST, id "
                "LIMIT 1"))).scalar()
            # revisione R09 (Z5): PdA permanenti senza oggetti nel WORM (sentinella)
            senza_oggetti = int((await session.execute(text(
                "SELECT count(*) FROM (SELECT 1 FROM public.aip WHERE anni_conservazione = 999 "
                "AND retention_fino_a = :sentinella LIMIT :tetto) x"),
                {"sentinella": SENZA_OGGETTI, "tetto": TETTO})).scalar_one())
            da_rinnovare = int((await session.execute(text(
                "SELECT count(*) FROM (SELECT 1 FROM public.aip WHERE anni_conservazione = 999 "
                "AND stato::text = ANY(:stati) "
                "AND (retention_fino_a IS NULL OR retention_fino_a < :soglia) LIMIT :tetto) x"),
                {"soglia": soglia, "tetto": TETTO,
                 "stati": list(STATI_CON_OGGETTI)})).scalar_one())
            rinnovi = int((await session.execute(text(
                "SELECT count(*) FROM (SELECT 1 FROM public.esiti_esecuzione "
                "WHERE tipo = 'RETENTION' AND scritto_il >= :dal LIMIT :tetto) x"),
                {"dal": adesso - timedelta(days=30), "tetto": TETTO})).scalar_one())
    except SQLAlchemyError as exc:
        raise pr.errore_db("stato_automatico", exc) from None
    in_parole = [
        f"I blocchi WORM dei pacchetti permanenti, di allegati, verbali e atti durano "
        f"{v[RETENTION_PERMANENTE_ANNI]} anni e si rinnovano da soli quando ne mancano meno di "
        f"{v[RETENTION_RINNOVO_SOGLIA_ANNI]}.",
        ("Nessun pacchetto permanente in attesa di rinnovo." if not da_rinnovare else
         f"Pacchetti permanenti da rinnovare o da censire: {da_rinnovare}"
         f"{' o più' if da_rinnovare >= TETTO else ''} (il rinnovo giornaliero li tratta)."),
        f"Rinnovi negli ultimi 30 giorni: {rinnovi}{' o più' if rinnovi >= TETTO else ''}.",
    ]
    if prossima == SENZA_OGGETTI:
        prossima = None
    if senza_oggetti:
        in_parole.append(f"Attenzione: {senza_oggetti} pacchetti permanenti non hanno oggetti "
                         "nello storage (allarme): il rinnovo non li può trattare.")
    errori = [p for p in passi if "errore" in p["ultimo_esito"]
              or int(p["ultimo_esito"].get("elementi_in_errore") or 0) > 0]
    if errori:
        in_parole.append(f"Attenzione: {len(errori)} passi automatici hanno dato errore "
                         "all'ultimo giro (vedi dettaglio).")
    return {"passi": passi, "in_parole": in_parole,
            "retention": {"anni": v[RETENTION_PERMANENTE_ANNI],
                          "soglia_anni": v[RETENTION_RINNOVO_SOGLIA_ANNI],
                          "prossima_scadenza": prossima, "da_rinnovare": da_rinnovare,
                          "rinnovi_30_giorni": rinnovi, "senza_oggetti": senza_oggetti,
                          "tetto_conteggi": TETTO}}


__all__ = ["leggi"]
