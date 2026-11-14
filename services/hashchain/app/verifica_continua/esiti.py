# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Regole pure della verifica continua: esiti, gravità, calendario della rotazione.

Nessun I/O e nessun orologio: il tempo entra come argomento (REGOLE §17.6).
Gli esiti sono quelli del registro delle verifiche (``libs.shared.models.ESITI_VERIFICA``).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from libs.shared.models import ESITI_VERIFICA

OK = "OK"
DIVERSO = "DIVERSO"
MANCANTE = "MANCANTE"
RETENTION_ASSENTE = "RETENTION_ASSENTE"
ERRORE_LETTURA = "ERRORE_LETTURA"

#: Esiti che sono un'anomalia d'integrità (allarme immediato).
ANOMALIE: frozenset[str] = frozenset({DIVERSO, MANCANTE, RETENTION_ASSENTE})

#: Gravità crescente: l'esito del PdA è il più grave fra quelli dei suoi oggetti.
_GRAVITA: dict[str, int] = {
    OK: 0, ERRORE_LETTURA: 1, RETENTION_ASSENTE: 2, DIVERSO: 3, MANCANTE: 4,
}
if set(_GRAVITA) != set(ESITI_VERIFICA):  # un esito nuovo va classificato qui
    raise RuntimeError("esiti della verifica non allineati con libs.shared.models")

#: Topic dell'evento d'allarme (outbox → Kafka).
TOPIC_ANOMALIA = "integrita.anomalia"


def peggiore(esiti: Iterable[str | None]) -> str:
    """Esito più grave (``OK`` se l'elenco è vuoto)."""
    risultato = OK
    for e in esiti:
        if e is not None and _GRAVITA[e] > _GRAVITA[risultato]:
            risultato = e
    return risultato


def prima_verifica(aip_id: uuid.UUID, adesso: datetime, finestra: timedelta) -> datetime:
    """Istante della prima verifica di un PdA nuovo: ``adesso`` più uno scarto
    in [0, finestra) ricavato dall'id (UUID già uniforme): i PdA entrati insieme
    si distribuiscono sulla finestra invece di accumularsi nello stesso giorno."""
    secondi = max(1, int(finestra.total_seconds()))
    return adesso + timedelta(seconds=uuid.UUID(str(aip_id)).int % secondi)


def ritardo_errore(tentativo: int, ritardi: Sequence[int]) -> timedelta:
    """Attesa prima del tentativo successivo al ``tentativo``-esimo ERRORE_LETTURA
    consecutivo (1 = primo): ritardi crescenti, l'ultimo si ripete."""
    indice = min(max(tentativo, 1), len(ritardi)) - 1
    return timedelta(seconds=ritardi[indice])


def retention_valida(
    modalita: str | None,
    fino_a: datetime | None,
    *,
    adesso: datetime,
    scadenza_conservazione: datetime | None,
    tolleranza: timedelta,
) -> str | None:
    """Motivo per cui la retention dell'oggetto NON va bene (None = va bene).

    Richiesti: modalità COMPLIANCE, termine nel futuro e, se il PdA ha una
    scadenza di conservazione, termine non anteriore alla scadenza (meno la
    tolleranza). Per la conservazione permanente la scadenza è assente."""
    if modalita is None or fino_a is None:
        return "retention Object Lock assente"
    if modalita != "COMPLIANCE":
        return f"retention in modalità {modalita}, non COMPLIANCE"
    if fino_a <= adesso:
        return "retention scaduta"
    if scadenza_conservazione is not None and fino_a < scadenza_conservazione - tolleranza:
        return "retention più breve della scadenza di conservazione"
    return None
