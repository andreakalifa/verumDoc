# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Comandi dell'operatore del testimone (solo dall'account del testimone).

Uso (nel container del testimone, es. ``kubectl exec`` o un Job dedicato):

    python -m app.operatore stato
    python -m app.operatore allarmi [--limite 20]
    python -m app.operatore chiave-pubblica
    python -m app.operatore sblocca --operatore "Mario Rossi" --motivo "…"
    python -m app.operatore nuova-base --nota checkpoint.txt --operatore "…" --motivo "…"

Non esiste un'API remota per sbloccare il testimone: chi controlla il
registro non deve poterlo fare (PIANO §5.3, amministratori diversi). Ogni
intervento resta in ``testimone.interventi_operatore`` (append-only) con chi
l'ha fatto e perché. Codice d'uscita 0 se l'operazione è riuscita, 1 altrimenti.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

from app.application.stato import InterventoNonAmmesso, nuova_base, sblocca, ultimi_allarmi
from app.avvio import ConfigurazioneAvvio, carica_contesto
from app.config import settings
from app.domain.checkpoint import (
    CheckpointNonValido,
    ConfigurazioneNonValida,
    FirmaRegistroNonValida,
)
from app.infrastructure.modelli import StatoRegistro
from app.osservabilita import MetricheTestimone
from libs.shared.db import close_db, get_async_session, init_db


def _scrivi(testo: str) -> None:
    sys.stdout.write(testo + "\n")


async def _esegui(opzioni: argparse.Namespace) -> int:
    contesto = carica_contesto(ConfigurazioneAvvio.da_impostazioni(settings), MetricheTestimone())
    if opzioni.comando == "chiave-pubblica":
        _scrivi(contesto.chiave.verificatore)
        return 0
    origine = opzioni.origine or contesto.origini[0]
    if origine not in contesto.origini:
        _scrivi(f"ERRORE: origine {origine!r} non fra quelle ammesse")
        return 1
    await init_db(settings.database_url)
    try:
        async with get_async_session() as sessione:
            if opzioni.comando == "stato":
                s = await sessione.get(StatoRegistro, origine)
                if s is None:
                    _scrivi(f"{origine}: nessun checkpoint ricevuto")
                else:
                    radice = base64.b64encode(s.radice).decode() if s.radice else "-"
                    _scrivi(
                        f"{origine}: {s.stato} · epoca {s.epoca} · dimensione "
                        f"{s.dimensione} · radice {radice}"
                    )
                    if s.motivo:
                        _scrivi(f"  dal {s.compromesso_dal:%Y-%m-%d %H:%M:%S} UTC: {s.motivo}")
                return 0
            if opzioni.comando == "allarmi":
                for a in await ultimi_allarmi(sessione, origine, opzioni.limite):
                    _scrivi(
                        f"{a.rilevato_il:%Y-%m-%d %H:%M:%S} {a.tipo} blocca={a.blocca} "
                        f"old={a.old} presentata={a.dimensione_presentata} "
                        f"conosciuta={a.dimensione_conosciuta}"
                    )
                return 0
            if opzioni.comando == "sblocca":
                esito = await sblocca(
                    sessione, origine, operatore=opzioni.operatore, motivo=opzioni.motivo
                )
            else:
                testo = Path(opzioni.nota).read_text(encoding="utf-8")
                cp = contesto.verifica_registro(testo)
                if cp.origine != origine:
                    _scrivi(f"ERRORE: la nota è dell'origine {cp.origine!r}")
                    return 1
                esito = await nuova_base(
                    sessione,
                    cp,
                    chiave=contesto.chiave,
                    operatore=opzioni.operatore,
                    motivo=opzioni.motivo,
                )
        _scrivi(
            f"{esito.azione} registrato (intervento {esito.intervento_id}): {esito.origine} "
            f"epoca {esito.epoca}, dimensione {esito.dimensione}"
        )
        return 0
    finally:
        await close_db()


def main(argomenti: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.operatore", description="Comandi dell'operatore del testimone"
    )
    parser.add_argument("--origine", default=None, help="origine (predefinita: la prima ammessa)")
    sotto = parser.add_subparsers(dest="comando", required=True)
    sotto.add_parser("stato", help="stato del testimone")
    p_allarmi = sotto.add_parser("allarmi", help="ultimi allarmi")
    p_allarmi.add_argument("--limite", type=int, default=20)
    sotto.add_parser("chiave-pubblica", help="verifier key del testimone da pubblicare")
    for nome in ("sblocca", "nuova-base"):
        p = sotto.add_parser(nome)
        p.add_argument("--operatore", required=True)
        p.add_argument("--motivo", required=True)
        if nome == "nuova-base":
            p.add_argument(
                "--nota", required=True, help="file con il checkpoint firmato dal registro"
            )
    opzioni = parser.parse_args(argomenti)
    try:
        return asyncio.run(_esegui(opzioni))
    except (
        InterventoNonAmmesso,
        ConfigurazioneNonValida,
        CheckpointNonValido,
        FirmaRegistroNonValida,
        OSError,
    ) as exc:
        _scrivi(f"ERRORE: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
