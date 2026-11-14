# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/routers/rielabora_ente.py — «Rielabora» dei propri
versamenti rifiutati, eseguita direttamente dall'ente (R07, richiesta del committente).

  POST /api/dashboard/versamenti/{pdv_id}/rielabora/anteprima   piano (non scrive)
  POST /api/dashboard/versamenti/{pdv_id}/rielabora/applica     applica il piano confermato

Stesse regole della coda di scarto del conservatore (``app/services/scarti.py``:
piano con impronta, lock, una ripartenza per PdV, copie di transito non scadute
secondo ``STAGING_SCARTATI_GIORNI``, nessun doppione, audit nella stessa
transazione con il motivo), limitate a:

  - i messaggi scartati della verifica e della costruzione del PdA (forma
    «pipeline») ancora da gestire, del PdV indicato;
  - i versamenti del PROPRIO ente: ADMIN_TENANT e OPERATORE dell'ente del token
    (filtro applicativo sull'ente e Row Level Security dell'ambito d'ente: un PdV
    di un altro ente dà un piano vuoto, senza dire se esiste). Il conservatore
    (SUPER_ADMIN, RSC_CONSERVATORE) la può usare per qualunque ente, come la sua
    coda di scarto. Mai con chiave API.

Un PdV rifiutato per i dati versati (controlli non superati) non ha messaggi
scartati: il piano è vuoto e lo dice; si corregge e si versa di nuovo.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.errori import errore_db
from app.routers.scarti import EsitoApplica, Piano, _json
from app.services import scarti as servizio
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi
from libs.shared.security.revoca import dipendenza_non_revocato

#: Ruoli dell'ente che rielaborano i propri versamenti rifiutati.
RUOLI_ENTE = ("ADMIN_TENANT", "OPERATORE")
RUOLI_CONSERVATORE = ("SUPER_ADMIN", "RSC_CONSERVATORE")

NESSUN_MESSAGGIO = ("Nessun messaggio scartato da rielaborare per questo versamento: se è stato "
                    "rifiutato per i dati versati, correggerli e versare di nuovo.")

router = APIRouter(prefix="/api/dashboard/versamenti", tags=["Versamenti rifiutati"],
                   responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 Depends(limite_richieste)])


async def chi_rielabora(user: dict = Depends(utente_corrente)) -> dict:
    """ADMIN_TENANT e OPERATORE del proprio ente, o il conservatore; mai chiave API."""
    if user.get("auth_method") == "api_key" or \
            user.get("ruolo") not in (*RUOLI_ENTE, *RUOLI_CONSERVATORE):
        raise ErroreApi("AUTH-010", "La rielaborazione dei versamenti rifiutati è riservata "
                                    "all'amministratore e agli operatori dell'ente")
    from libs.shared.security.permessi import motivo_permesso_mancante

    # R10 (revisione V3): rielaborare fa ripartire un versamento (permesso di versare)
    motivo = motivo_permesso_mancante(user, "versamento:esegui")
    if motivo:
        raise ErroreApi("AUTH-010", motivo)
    return user


def _filtri(user: dict, pdv_id: str) -> tuple[servizio.Filtri, str | None]:
    pdv = servizio.uuid_o_none(pdv_id)
    if pdv is None:
        raise ErroreApi("DSH-422", "pdv_id non è un identificativo valido")
    if user.get("ruolo") in RUOLI_CONSERVATORE:
        return servizio.Filtri(pdv_id=pdv), None
    ente = servizio.uuid_o_none(user.get("tenant_id"))
    if ente is None:
        raise ErroreApi("AUTH-010", "Il token non indica un ente")
    return servizio.Filtri(tenant_id=ente, pdv_id=pdv), ente


class RichiestaRielabora(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impronta_piano: str = Field(..., pattern=r"^[0-9a-f]{64}$",
                                description="Impronta del piano confermato (anteprima)")
    motivo: str = Field(..., min_length=10, max_length=1000,
                        description="Motivo della rielaborazione (REGOLE §15.7)")


def _pubblico(piano: dict) -> Piano:
    dati = servizio.piano_pubblico(piano)
    if not dati["elementi"]:
        dati["avvisi"] = [*dati["avvisi"], NESSUN_MESSAGGIO]
    return Piano(**dati)


@router.post("/{pdv_id}/rielabora/anteprima", response_model=Piano,
             summary="Anteprima di «Rielabora» di un proprio versamento rifiutato")
async def anteprima(pdv_id: str, user: dict = Depends(chi_rielabora)) -> Piano:
    filtri, _ente = _filtri(user, pdv_id)
    try:
        async with get_async_session() as session:
            piano = await servizio.calcola_piano(session, ids=None, filtri=filtri,
                                                 adesso=datetime.now(UTC), blocca=False,
                                                 solo_pipeline=True)
            await session.rollback()  # anteprima: nessuna scrittura
    except SQLAlchemyError as exc:
        raise errore_db("calcolo del piano di rielaborazione", exc, codice="DSH-503") from None
    return _pubblico(piano)


@router.post("/{pdv_id}/rielabora/applica", response_model=EsitoApplica,
             summary="Applica «Rielabora» di un proprio versamento rifiutato")
async def applica(pdv_id: str, corpo: RichiestaRielabora,
                  user: dict = Depends(chi_rielabora)) -> EsitoApplica:
    """Ricalcola il piano sotto lock: impronta diversa → 409 DSH-409-PIANO con il
    piano aggiornato; nessun messaggio rielaborabile → 422 DSH-422-PIANO."""
    filtri, ente = _filtri(user, pdv_id)
    try:
        async with get_async_session() as session:
            esito = await servizio.applica(session, ids=None, filtri=filtri,
                                           impronta=corpo.impronta_piano, motivo=corpo.motivo,
                                           utente=user, adesso=datetime.now(UTC),
                                           solo_pipeline=True, tenant_id=ente)
    except servizio.PianoCambiato as cambio:
        raise ErroreApi("DSH-409-PIANO", dettagli={
            "piano": _json(servizio.piano_pubblico(cambio.piano))}) from None
    except SQLAlchemyError as exc:
        raise errore_db("rielaborazione del versamento rifiutato", exc,
                        codice="DSH-503") from None
    return EsitoApplica(**esito)
