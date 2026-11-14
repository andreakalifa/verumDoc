# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/db/contatori.py — contatori per ente al posto di ``COUNT(*)`` (R04,
piano §4.2: «contatori per ente aggiornati dagli eventi, mai COUNT(*) su tabelle
grandi»; §6.4).

Come funzionano (migrazione 20261023_028):
  - trigger su ``pdv``, ``aip`` e ``documenti`` scrivono, nella STESSA
    transazione della modifica, una variazione in ``contatori_ente_delta``
    (solo INSERT: nessuna riga calda contesa fra versamenti concorrenti);
  - ``consolida`` (manutenzione del database, ogni 30 s) sposta a lotti le
    variazioni in ``contatori_ente`` (una riga per ente, metrica, giorno UTC);
  - le letture sommano ``contatori_ente`` e le variazioni non ancora
    consolidate in UNA istruzione (stessa istantanea): il valore è ESATTO e
    aggiornato al commit, non approssimato.

Metriche (``metrica``; ``giorno`` = giorno UTC):
  pdv.stato.<STATO>     variazione del numero di PdV in quello stato (giorno del
                        cambio): la somma su tutti i giorni = PdV oggi in quello stato
  pdv.ricevuti          PdV ricevuti, per giorno di ``data_ricezione``
  aip.stato.<STATO>     come pdv.stato, per i PdA
  aip.versati           PdA per giorno di ``data_versamento``
  aip.conservati        PdA in CONSERVATO o IN_CATENA, per giorno di ``data_conservazione``
  documenti.versati     documenti per giorno di ``created_at``
  documenti.byte        somma di ``dimensione_bytes`` dei documenti, stesso giorno
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import text

from libs.shared.db import metriche

_UNIONE = (
    "SELECT tenant_id, metrica, giorno, valore FROM public.contatori_ente {filtro} "
    "UNION ALL "
    "SELECT tenant_id, metrica, giorno, delta FROM public.contatori_ente_delta {filtro}"
)


def _filtro(tenant_id: Any, prefisso: str | None, da: date | None, a: date | None,
            ) -> tuple[str, dict[str, Any]]:
    condizioni: list[str] = []
    parametri: dict[str, Any] = {}
    if tenant_id is not None:
        condizioni.append("tenant_id = CAST(:tenant AS uuid)")
        parametri["tenant"] = str(uuid.UUID(str(tenant_id)))
    if prefisso is not None:
        condizioni.append("metrica LIKE :prefisso")
        parametri["prefisso"] = prefisso.replace("%", r"\%").replace("_", r"\_") + "%"
    if da is not None:
        condizioni.append("giorno >= :da")
        parametri["da"] = da
    if a is not None:
        condizioni.append("giorno < :a")
        parametri["a"] = a
    return ("WHERE " + " AND ".join(condizioni)) if condizioni else "", parametri


async def valori(session: Any, *, tenant_id: Any = None, prefisso: str | None = None,
                 da: date | None = None, a: date | None = None) -> dict[str, int]:
    """Somma per metrica (su tutti i giorni o su [da, a)), per un ente o per tutti."""
    filtro, parametri = _filtro(tenant_id, prefisso, da, a)
    righe = (await session.execute(text(
        f"SELECT metrica, sum(valore)::bigint FROM ({_UNIONE.format(filtro=filtro)}) u "  # noqa: S608 — frammenti costanti, valori come parametri
        "GROUP BY metrica"), parametri)).all()
    return {r[0]: int(r[1]) for r in righe}


async def serie_giornaliera(session: Any, metrica: str, *, da: date, a: date,
                            tenant_id: Any = None) -> dict[date, int]:
    """Valori per giorno di una metrica in [da, a) (giorni senza valore assenti)."""
    filtro, parametri = _filtro(tenant_id, None, da, a)
    filtro += " AND metrica = :metrica" if filtro else "WHERE metrica = :metrica"
    parametri["metrica"] = metrica
    righe = (await session.execute(text(
        f"SELECT giorno, sum(valore)::bigint FROM ({_UNIONE.format(filtro=filtro)}) u "  # noqa: S608 — frammenti costanti, valori come parametri
        "GROUP BY giorno ORDER BY giorno"), parametri)).all()
    return {r[0]: int(r[1]) for r in righe if int(r[1]) != 0}


_CONSISTENZA = text(
    "SELECT tenant_id, "
    "  COALESCE(sum(valore) FILTER (WHERE metrica LIKE 'aip.stato.%'), 0)::bigint AS pda, "
    "  COALESCE(sum(valore) FILTER (WHERE metrica = 'documenti.versati'), 0)::bigint AS documenti, "
    "  COALESCE(sum(valore) FILTER (WHERE metrica = 'documenti.byte'), 0)::bigint AS byte "
    "FROM (" + _UNIONE.format(filtro="WHERE metrica LIKE 'aip.stato.%' OR metrica IN "
                                     "('documenti.versati', 'documenti.byte')") + ") u "
    "GROUP BY tenant_id"
)


async def consistenza_per_ente(session: Any) -> dict[str, dict[str, int]]:
    """Per ogni ente: PdA (tutti gli stati), documenti e byte dei documenti."""
    righe = (await session.execute(_CONSISTENZA)).all()
    return {str(r.tenant_id): {"numero_pda": int(r.pda), "numero_documenti": int(r.documenti),
                               "volume_byte": int(r.byte)} for r in righe}


def per_stato(valori_metriche: dict[str, int], prefisso: str) -> dict[str, int]:
    """``{"aip.stato.CONSERVATO": 3, ...}`` → ``{"CONSERVATO": 3, ...}``."""
    return {k[len(prefisso):]: v for k, v in valori_metriche.items() if k.startswith(prefisso)}


_CONSOLIDA = text(
    "WITH presi AS ("
    "  DELETE FROM public.contatori_ente_delta WHERE id IN ("
    "    SELECT id FROM public.contatori_ente_delta ORDER BY id LIMIT :lotto "
    "    FOR UPDATE SKIP LOCKED) "
    "  RETURNING tenant_id, metrica, giorno, delta"
    "), somme AS ("
    "  SELECT tenant_id, metrica, giorno, sum(delta)::bigint AS valore FROM presi "
    "  GROUP BY tenant_id, metrica, giorno"
    "), scritti AS ("
    "  INSERT INTO public.contatori_ente (tenant_id, metrica, giorno, valore) "
    "  SELECT tenant_id, metrica, giorno, valore FROM somme "
    "  ON CONFLICT (tenant_id, metrica, giorno) DO UPDATE "
    "  SET valore = public.contatori_ente.valore + EXCLUDED.valore, aggiornato_il = now() "
    "  RETURNING 1"
    ") SELECT (SELECT count(*) FROM presi), (SELECT count(*) FROM scritti)"
)


async def consolida(session: Any, *, lotto: int = 50_000) -> int:
    """Sposta fino a ``lotto`` variazioni in ``contatori_ente``; restituisce
    quante. Più esecuzioni in parallelo si dividono le righe (SKIP LOCKED)."""
    presi, _ = (await session.execute(_CONSOLIDA, {"lotto": int(lotto)})).one()
    return int(presi)


async def in_attesa(session: Any, *, tetto: int = 1_000_000) -> int:
    """Variazioni non ancora consolidate (con tetto); aggiorna la metrica."""
    n = int((await session.execute(text(
        "SELECT count(*) FROM (SELECT 1 FROM public.contatori_ente_delta LIMIT :t) s"),
        {"t": tetto})).scalar_one())
    metriche.imposta("verumdoc_contatori_delta_in_attesa", n)
    return n
