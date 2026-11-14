# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/riserva.py — riserva ATOMICA di un uso entro una finestra, condivisa
fra le repliche (Redis).

Riferimenti: REGOLE_SVILUPPO §31 R10 (7) (un limite è una riserva atomica PRIMA
dell'azione, mai «leggi, esegui, poi conta») e R12b revisione (1) (INCR e scadenza in un
passo: una chiave senza scadenza è un blocco permanente). Usato da R16 per il limite delle
prove di connessione dei fornitori di firma (VD-C-168) e dei tentativi di firma personale
con OTP (VD-C-166).

Lo script Lua fa in UN passo (Redis esegue gli script senza interruzioni): INCR; se la
chiave non ha scadenza, EXPIRE; se il valore supera il limite, DECR (restituzione) e
rifiuto. Richieste in parallelo da più repliche non superano mai il limite.
"""
from __future__ import annotations

from typing import Any

#: INCR + scadenza + confronto + restituzione in un passo atomico
SCRIPT_RISERVA = (
    "-- vd:riserva_atomica\n"
    "local n = redis.call('INCR', KEYS[1])\n"
    "if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end\n"
    "if n > tonumber(ARGV[1]) then redis.call('DECR', KEYS[1]) return 0 end\n"
    "return n")


class LimiteNonVerificabile(RuntimeError):
    """Redis non raggiungibile: chi chiama decide (le prove e le firme si rifiutano)."""


async def riserva(redis: Any, chiave: str, *, limite: int, finestra_secondi: int) -> bool:
    """True se l'uso è riservato (entro ``limite`` nella finestra), False se oltre.
    ``LimiteNonVerificabile`` se Redis non risponde."""
    try:
        esito = await redis.eval(SCRIPT_RISERVA, 1, chiave, max(1, int(limite)),
                                 max(1, int(finestra_secondi)))
    except Exception as exc:  # noqa: BLE001 — qualunque errore di Redis
        raise LimiteNonVerificabile(type(exc).__name__) from exc
    return int(esito) > 0


async def restituisci(redis: Any, chiave: str) -> None:
    """Restituisce un uso riservato (esito che non conta). Una chiave scaduta nel
    frattempo, ricreata negativa dal DECR, si toglie."""
    try:
        if int(await redis.decr(chiave)) < 0:
            await redis.delete(chiave)
    except Exception:  # noqa: BLE001 — la restituzione è un'agevolazione, mai un blocco
        return


__all__ = ["SCRIPT_RISERVA", "LimiteNonVerificabile", "restituisci", "riserva"]
