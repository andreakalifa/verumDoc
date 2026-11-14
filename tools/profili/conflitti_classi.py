#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/profili/conflitti_classi.py — rapporto PRE-DEPLOY delle classi i cui profili
cambiano comportamento con la composizione di R11 (revisione T2, VD-P-113).

Prima di R11 un campo del profilo di classe con lo stesso nome di un campo della
tipologia lo SOSTITUIVA; da R11 lo restringe soltanto (``restringi_campo``). Per
ogni classe con un profilo proprio e una tipologia il rapporto elenca:

  - i campi omonimi con i motivi del cambio (``conflitti_composizione``: obbligo
    della tipologia che resta, tipo incompatibile che cede al tipo della
    tipologia, valori tolti, …);
  - l'anteprima sui PdV recenti della classe: quanti sarebbero rifiutati o
    avrebbero avvisi nuovi con la composizione di R11 rispetto a quella di R05
    (stesso motore dell'anteprima delle bozze, ``confronta``).

Sola lettura (transazione READ ONLY, ``statement_timeout``)::

    python tools/profili/conflitti_classi.py --database-url postgresql+asyncpg://…

Uscita: 0 nessuna classe cambia esito; 1 almeno una (da risolvere o accettare
prima del deploy, README_RILASCIO); 2 errore.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_RADICE = Path(__file__).resolve().parents[2]
if str(_RADICE) not in sys.path:
    sys.path.insert(0, str(_RADICE))

from libs.verumdoc_rules import CATALOGO, REGISTRO  # noqa: E402
from libs.verumdoc_rules.campione import Esecutore, carica_campione  # noqa: E402
from libs.verumdoc_rules.confronta import confronta  # noqa: E402
from libs.verumdoc_rules.impostazioni import giorno_locale  # noqa: E402
from libs.verumdoc_rules.lettura import (  # noqa: E402
    SQL_PROFILI_CLASSE,
    SQL_PROFILI_TIPOLOGIA,
    SQL_VALORI_LIVELLI,
    livelli_da_righe,
    prepara_ambiente,
    profili_classe_da_righe,
    profili_tipologia_da_righe,
)
from libs.verumdoc_rules.profili import (  # noqa: E402
    ProfiloNonValido,
    componi_profili_r05,
    conflitti_composizione,
    seleziona_profilo,
)
from libs.verumdoc_rules.regole import AmbienteRegole  # noqa: E402
from libs.verumdoc_rules.validatore import CAMPI_METADATI_INDICE  # noqa: E402

SQL_CLASSI_CON_PROFILO = (
    "SELECT DISTINCT c.id::text AS id, c.codice, c.tenant_id::text AS tenant_id "
    "FROM public.profili_metadati p JOIN public.classi_documentali c ON c.id = p.classe_id "
    "ORDER BY 1")


async def rapporto(esegui: Esecutore, *, adesso: datetime, giorni: int = 30,
                   limite: int = 500, tempo_max: float = 60.0) -> dict[str, Any]:
    giorno = giorno_locale(adesso)
    db = profili_tipologia_da_righe(await esegui(SQL_PROFILI_TIPOLOGIA, {}))
    classi: list[dict[str, Any]] = []
    for r in await esegui(SQL_CLASSI_CON_PROFILO, {}):
        voce: dict[str, Any] = {"classe": r["codice"], "classe_id": r["id"],
                                "ente": r["tenant_id"]}
        livelli = livelli_da_righe(await esegui(SQL_VALORI_LIVELLI,
                                                {"t": r["tenant_id"], "c": r["id"]}))
        profili = profili_classe_da_righe(await esegui(SQL_PROFILI_CLASSE,
                                                       {"c": r["id"], "g": giorno}), r["codice"])
        try:
            tipologia = prepara_ambiente(registro=REGISTRO, livelli=livelli, adesso=adesso,
                                         tenant_id=r["tenant_id"], classe_id=r["id"],
                                         profili_tipologia_db=db).profilo
        except (ProfiloNonValido, ValueError) as exc:
            classi.append({**voce, "errore": str(exc), "cambia_esito": True})
            continue
        profilo_classe = seleziona_profilo(profili, giorno)
        if tipologia is None or profilo_classe is None:
            continue
        conflitti = [c for c in conflitti_composizione(tipologia, profilo_classe)
                     if c["cambia_esito"]]
        if not conflitti:
            continue
        voce.update({"tipologia": tipologia.codice, "versione_tipologia": tipologia.versione,
                     "conflitti": conflitti, "cambia_esito": True})
        r05 = componi_profili_r05(tipologia, profilo_classe)
        campione = await carica_campione(esegui, registro=REGISTRO, adesso=adesso, giorni=giorni,
                                         limite=limite, ente=r["tenant_id"], classe=r["id"],
                                         campi_noti=CAMPI_METADATI_INDICE)

        def prima(p: Any, contesti: Any = campione.contesti, r05: Any = r05) -> AmbienteRegole:
            a = contesti.ambiente(p)
            return AmbienteRegole(a.risolutore, a.contesto, r05, accordo=a.accordo,
                                  override=a.override)

        scadenza = time.monotonic() + tempo_max
        esito = confronta(campione.pdv, catalogo=CATALOGO, ambiente_prima=prima,
                          ambiente_dopo=lambda p, c=campione.contesti: c.ambiente(p),
                          tempo_scaduto=lambda s=scadenza: time.monotonic() > s)
        voce["anteprima"] = {
            "pdv_analizzati": esito.analizzati,
            "rifiutati_con_r05": esito.rifiutati_prima,
            "rifiutati_con_r11": esito.rifiutati_dopo,
            "con_nuovi_avvisi": esito.con_nuovi_avvisi,
            "esempi": esito.esempi}
        classi.append(voce)
    return {"generato_il": adesso.isoformat(), "classi_che_cambiano": len(classi),
            "classi": classi}


async def _principale(a: argparse.Namespace) -> dict[str, Any]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    motore = create_async_engine(a.database_url, connect_args={"server_settings": {
        "default_transaction_read_only": "on", "statement_timeout": str(a.timeout_query_ms)}})
    try:
        async with motore.connect() as conn:
            async def esegui(sql: str, parametri: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
                return (await conn.execute(text(sql), dict(parametri))).mappings().all()

            return await rapporto(esegui, adesso=datetime.now(UTC), giorni=a.giorni,
                                  limite=a.limite, tempo_max=a.tempo_max)
    finally:
        await motore.dispose()


def main(argomenti: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classi con profili in conflitto (R11)")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--giorni", type=int, default=30)
    parser.add_argument("--limite", type=int, default=500)
    parser.add_argument("--tempo-max", type=float, default=60.0)
    parser.add_argument("--timeout-query-ms", type=int, default=30_000)
    a = parser.parse_args(argomenti)
    try:
        risultato = asyncio.run(_principale(a))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(risultato, ensure_ascii=False, indent=2, default=str))
    return 0 if risultato["classi_che_cambiano"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
