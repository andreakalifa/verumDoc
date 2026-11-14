# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/services/cursori.py — cursori opachi (data, id) per gli
elenchi dal più recente (R04 servizi, VD-C-102).

Un cursore dice «dopo questa riga» nell'ordine ``(data DESC, id DESC)``: niente
OFFSET (che rilegge e scarta le righe precedenti) né ``COUNT(*)`` (che legge
tutto l'elenco). Il cursore porta il nome dell'elenco: quello di un altro
elenco è rifiutato (DSH-104). Non dà accesso a nulla: il perimetro (ente)
si applica sempre nella query.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any


class CursoreNonValido(ValueError):
    """Cursore illeggibile o di un altro elenco (DSH-104)."""


def codifica(elenco: str, quando: datetime, ultimo: Any) -> str:
    dati = {"e": elenco, "t": quando.isoformat(), "i": str(ultimo)}
    return base64.urlsafe_b64encode(json.dumps(dati, separators=(",", ":")).encode()
                                    ).decode().rstrip("=")


def decodifica(elenco: str, cursore: str) -> tuple[datetime, str]:
    """(data, id) dell'ultima riga letta; ``CursoreNonValido`` se non è di ``elenco``."""
    try:
        dati = json.loads(base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)))
        if dati.get("e") != elenco:
            raise CursoreNonValido("cursore di un altro elenco")
        quando = datetime.fromisoformat(dati["t"])
        ultimo = str(uuid.UUID(str(dati["i"])))
    except CursoreNonValido:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CursoreNonValido(str(exc)) from None
    if quando.tzinfo is None:
        raise CursoreNonValido("data senza fuso")
    return quando, ultimo


def pagina(righe: list[Any], limite: int, elenco: str, *, data: str = "created_at",
           ident: str = "id") -> tuple[list[Any], str | None, bool]:
    """Da ``limite + 1`` righe lette: (righe della pagina, cursore successivo, altri)."""
    altri = len(righe) > limite
    righe = righe[:limite]
    cursore = codifica(elenco, righe[-1][data], righe[-1][ident]) if altri and righe else None
    return righe, cursore, altri
