#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/confronta_regole.py — prova di una modifica delle regole sui PdV reali, in
SOLA LETTURA (REGOLE_SVILUPPO §8.11; contratto R05 Q1).

Riapplica il motore delle regole ai PdV recenti con la configurazione di oggi e
con la configurazione modificata, e riporta le differenze («12 PdV sarebbero ora
rifiutati per formato non ammesso»). Usa LO STESSO codice dell'anteprima delle
bozze di onboarding (``libs.verumdoc_rules.campione`` e ``confronta``): stesso
campione (ultimi PdV per data di ricezione, limite e tempo massimo dichiarati),
stesse query in blocco.

Uso::

    python tools/confronta_regole.py --database-url postgresql+asyncpg://… \\
        --modifiche modifica.json [--ente UUID] [--classe UUID] [--giorni 30] \\
        [--limite 2000] [--tempo-max 60]

``modifica.json``::

    {"livello": "ENTE", "valori": {"FORMATI_AMMESSI": ["application/pdf"]},
     "togli": ["CLASSE_DIMENSIONE_MAX_MB"]}

oppure una nuova versione di un profilo di tipologia::

    {"profilo_tipologia": {…forma di ProfiloMetadati.come_dict…}}

La connessione è in sola lettura (``default_transaction_read_only``) e ogni query
ha ``statement_timeout``. Codice d'uscita: 0 nessuna differenza, 1 differenze
(da approvare prima del rilascio), 2 errore.
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

_RADICE = Path(__file__).resolve().parents[1]
if str(_RADICE) not in sys.path:
    sys.path.insert(0, str(_RADICE))

from libs.verumdoc_rules import CATALOGO, REGISTRO  # noqa: E402
from libs.verumdoc_rules.campione import Esecutore, Variante, carica_campione  # noqa: E402
from libs.verumdoc_rules.confronta import confronta  # noqa: E402
from libs.verumdoc_rules.impostazioni import Livello  # noqa: E402
from libs.verumdoc_rules.profili import ProfiloMetadati  # noqa: E402
from libs.verumdoc_rules.validatore import CAMPI_METADATI_INDICE  # noqa: E402


def variante_da_file(dati: Mapping[str, Any], *, ente: str | None,
                     classe: str | None) -> Variante:
    """Variante dalla descrizione JSON della modifica (valori validati sul registro)."""
    if "profilo_tipologia" in dati:
        return Variante(profilo_tipologia=ProfiloMetadati.da_dict(dati["profilo_tipologia"]))
    livello = Livello(str(dati.get("livello") or "CONSERVATORE"))
    valori = {}
    for codice, valore in (dati.get("valori") or {}).items():
        valori[codice] = REGISTRO[codice].converti(valore, livello)
    return Variante(livello=livello, ente=ente, classe=classe, valori=valori,
                    togli=frozenset(dati.get("togli") or ()))


async def esegui_confronto(esegui: Esecutore, variante: Variante, *, ente: str | None,
                           classe: str | None, giorni: int, limite: int, tempo_max: float,
                           adesso: datetime) -> dict[str, Any]:
    """Confronto sul campione: stessa funzione dell'anteprima delle bozze."""
    campione = await carica_campione(esegui, registro=REGISTRO, adesso=adesso, giorni=giorni,
                                     limite=limite, ente=ente, classe=classe,
                                     campi_noti=CAMPI_METADATI_INDICE)
    scadenza = time.monotonic() + tempo_max
    esito = confronta(campione.pdv, catalogo=CATALOGO,
                      ambiente_prima=lambda p: campione.contesti.ambiente(p),  # type: ignore[arg-type]
                      ambiente_dopo=lambda p: campione.contesti.ambiente(p, variante),  # type: ignore[arg-type]
                      tempo_scaduto=lambda: time.monotonic() > scadenza)
    return {"campione": {**campione.dichiarazione(), "tempo_max_s": tempo_max},
            "esito": esito.come_dict(),
            "differenze": esito.con_differenze + esito.non_valutabili}


async def _principale(a: argparse.Namespace) -> dict[str, Any]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    variante = variante_da_file(json.loads(a.modifiche.read_text(encoding="utf-8")),
                                ente=a.ente, classe=a.classe)
    motore = create_async_engine(a.database_url, connect_args={"server_settings": {
        "default_transaction_read_only": "on", "statement_timeout": str(a.timeout_query_ms)}})
    try:
        async with motore.connect() as conn:
            async def esegui(sql: str, parametri: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
                return (await conn.execute(text(sql), dict(parametri))).mappings().all()

            return await esegui_confronto(esegui, variante, ente=a.ente, classe=a.classe,
                                          giorni=a.giorni, limite=a.limite,
                                          tempo_max=a.tempo_max, adesso=datetime.now(UTC))
    finally:
        await motore.dispose()


def main(argomenti: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confronto delle regole sui PdV (sola lettura)")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--modifiche", type=Path, required=True)
    parser.add_argument("--ente")
    parser.add_argument("--classe")
    parser.add_argument("--giorni", type=int, default=30)
    parser.add_argument("--limite", type=int, default=2000)
    parser.add_argument("--tempo-max", type=float, default=60.0)
    parser.add_argument("--timeout-query-ms", type=int, default=30_000)
    a = parser.parse_args(argomenti)
    try:
        risultato = asyncio.run(_principale(a))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(risultato, ensure_ascii=False, indent=2, default=str))
    return 0 if risultato["differenze"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
