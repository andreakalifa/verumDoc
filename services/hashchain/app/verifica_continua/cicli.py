# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Frequenza della verifica continua per ente e per classe (R09, decisione D05).

Il ciclo (giorni entro cui ogni PdA si rilegge) è il parametro
``VERIFICA_CICLO_GIORNI`` del motore (livelli CONSERVATORE, ENTE e CLASSE, 7-365,
verso «più sicuro» MINORE): come nel motore (``Risolutore._piu_sicuro``) fra i
valori dell'ente e della classe vince il più breve, mai oltre il TETTO (limite
del conservatore per l'ente, ``LIMITE.VERIFICA_CICLO_GIORNI``, o valore del
conservatore); senza valori del cliente vale il conservatore e, senza righe,
``VERIFICA_CICLO_GIORNI`` dell'ambiente del processo (predefinito 365). Una
query per lotto; un valore non valido nel database vale come assente (log).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text

from libs.verumdoc_rules.impostazioni import Livello, ValoreImpostazioneNonValido, codice_limite
from libs.verumdoc_rules.parametri_servizio import REGISTRO_SERVIZIO, VERIFICA_CICLO_GIORNI

logger = structlog.get_logger(__name__)

SQL_CICLI = text(
    "SELECT livello, tenant_id::text AS tenant_id, classe_id::text AS classe_id, "
    "valore::text AS valore, codice FROM public.impostazioni_valori "
    "WHERE codice = ANY(CAST(:codici AS text[]))")


@dataclass
class Cicli:
    """Giorni del ciclo per (ente, classe) con il valore del processo come base."""

    predefinito: int
    conservatore: int | None = None
    enti: dict[str, int] = field(default_factory=dict)
    classi: dict[str, int] = field(default_factory=dict)
    limiti: dict[str, int] = field(default_factory=dict)

    def giorni(self, tenant_id: Any, classe_id: Any) -> int:
        servizio = self.conservatore if self.conservatore is not None else self.predefinito
        tetto = self.limiti.get(str(tenant_id), servizio)
        cliente = [v for v in (self.classi.get(str(classe_id)) if classe_id is not None else None,
                               self.enti.get(str(tenant_id)) if tenant_id is not None else None)
                   if v is not None]
        if not cliente:
            return min(servizio, tetto)
        return min(min(cliente), tetto)


def da_righe(righe: list[dict[str, Any]], predefinito: int) -> Cicli:
    voce = REGISTRO_SERVIZIO[VERIFICA_CICLO_GIORNI]
    cicli = Cicli(predefinito=min(predefinito, int(voce.massimo or predefinito)))
    for r in righe:
        try:
            livello = Livello(r["livello"])
            valore = voce.converti(json.loads(r["valore"]), livello)
        except (ValueError, ValoreImpostazioneNonValido) as exc:
            logger.error("verifica_ciclo_scartato", livello=r["livello"], errore=str(exc)[:200])
            continue
        if r.get("codice") == codice_limite(VERIFICA_CICLO_GIORNI):
            if livello is Livello.ENTE and r["tenant_id"]:
                cicli.limiti[r["tenant_id"]] = int(valore)
        elif livello is Livello.CLASSE and r["classe_id"]:
            cicli.classi[r["classe_id"]] = int(valore)
        elif livello is Livello.ENTE and r["tenant_id"] and not r["classe_id"]:
            cicli.enti[r["tenant_id"]] = int(valore)
        elif livello is Livello.CONSERVATORE:
            cicli.conservatore = int(valore)
    return cicli


async def carica(predefinito: int) -> Cicli:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        righe = [dict(r) for r in (await session.execute(
            SQL_CICLI, {"codici": [VERIFICA_CICLO_GIORNI,
                                   codice_limite(VERIFICA_CICLO_GIORNI)]})).mappings()]
    return da_righe(righe, predefinito)


__all__ = ["Cicli", "carica", "da_righe"]
