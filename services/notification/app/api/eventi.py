# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/api/eventi.py
GET /api/v1/eventi — elenco degli eventi dell'ente con cursore (contratto C3).

Alternativa ai webhook per chi non può ricevere chiamate: il client chiede
gli eventi dopo l'ultimo cursore visto. Solo gli eventi del proprio ente
(``tenant_id`` dell'identità verificata, mai da parametro). Il cursore è la
POSIZIONE di pubblicazione (R03 f2, REV-13): un evento compare solo dopo che
il passo di pubblicazione seriale gliel'ha assegnata, quindi l'elenco non
salta mai un evento.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import SQLAlchemyError

from app.api.autenticazione import lettore_eventi
from app.api.limiti import limite_eventi
from app.api.modelli import ElencoEventi
from app.config import settings
from app.domain.eventi import tipi_ammessi, tipi_sconosciuti
from app.domain.identita import Identita
from app.infrastructure.repo_eventi import elenca_eventi, rappresenta_evento
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato

router = APIRouter(prefix="/api/v1", tags=["Eventi"],
                   # R03 f2 (REV-03): limite di richieste al minuto del piano
                   dependencies=[Depends(limite_eventi)])


@router.get("/eventi", response_model=ElencoEventi, responses=RISPOSTE_ERRORE,
            summary="Eventi dell'ente dopo un cursore")
async def elenco_eventi(
    dopo: int = Query(0, ge=0, le=2**63 - 1,
                      description="Cursore: `cursore` della risposta precedente (posizione "
                                  "dell'ultimo evento letto); 0 dall'inizio"),
    limite: int = Query(settings.eventi_limite_predefinito, ge=1,
                        le=settings.eventi_limite_massimo, description="Eventi per pagina"),
    tipo: list[str] | None = Query(None, description="Filtra per tipo (ripetibile)"),
    identita: Identita = Depends(lettore_eventi),
) -> ElencoEventi:
    tipi = list(dict.fromkeys(tipo or []))
    sconosciuti = tipi_sconosciuti(tipi)
    if sconosciuti:
        raise ErroreApi("EVT-001", f"Tipi sconosciuti: {', '.join(sconosciuti)}",
                        dettagli={"sconosciuti": sconosciuti, "ammessi": tipi_ammessi()})
    try:
        righe, altri = await elenca_eventi(identita.tenant_id, dopo=dopo, limite=limite,
                                           tipi=tipi)
    except SQLAlchemyError as exc:
        raise errore_registrato("EVT-503", exc, evento="elenco_eventi",
                                tenant_id=identita.tenant_id) from None
    return ElencoEventi.model_validate({
        "eventi": [rappresenta_evento(r) for r in righe],
        "cursore": righe[-1].posizione if righe else dopo,
        "altri": altri,
    })
