# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — preferenze personali dell'utente salvate sul server (R07).

Riferimenti: decisioni D31 (tema, densità, pagina iniziale, colonne visibili) e
D29 (ente scelto in testata dai ruoli globali, ricordato fra le sessioni);
migrazione 20261030_046 (``preferenze_utente``); REGOLE_SVILUPPO §13.3 (salvataggio
con la versione letta).

  GET /api/v1/onboarding/preferenze   preferenze dell'utente del token (vuote = predefinite)
  PUT /api/v1/onboarding/preferenze   sostituzione completa con la versione letta
                                      (409 ONB-412 se cambiata da un'altra sessione)

Ognuno legge e scrive SOLO le proprie preferenze (utente del token verificato).
L'ente in testata (``ente``: identificativo o «tutti») è ammesso solo per i ruoli
globali del conservatore: gli altri lavorano sempre sull'ente del proprio token.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import utente_da_authorization, verify_access_token
from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/onboarding/preferenze", tags=["Preferenze personali"],
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])

#: Ruoli che scelgono l'ente in testata (D29); gli stessi della console.
RUOLI_GLOBALI = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})
ENTE_TUTTI = "tutti"
_TABELLA = re.compile(r"^[a-z0-9_-]{1,40}$")
_COLONNA = re.compile(r"^[a-z0-9_]{1,40}$")


class Preferenze(BaseModel):
    """Preferenze ammesse (elenco chiuso: un campo sconosciuto è un errore)."""

    model_config = ConfigDict(extra="forbid")

    tema: Literal["automatico", "chiaro", "scuro"] | None = None
    densita: Literal["comoda", "compatta"] | None = None
    # W6: solo percorsi interni: «/» singolo iniziale, nessun «//» (URL relativo al
    # protocollo), nessuno schema, nessun «\»
    pagina_iniziale: str | None = Field(None, max_length=81,
                                        pattern=r"^/(?:[a-z0-9-]+/?)*$")
    colonne: dict[str, list[str]] | None = Field(
        None, description="Colonne visibili per tabella: {tabella: [colonna, …]}")
    ente: str | None = Field(None, description="D29: ente in testata (uuid) o «tutti»")

    @field_validator("colonne")
    @classmethod
    def _colonne(cls, valore: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if valore is None:
            return None
        if len(valore) > 30:
            raise ValueError("al più 30 tabelle")
        for tabella, colonne in valore.items():
            if not _TABELLA.match(tabella):
                raise ValueError(f"nome di tabella non valido: {tabella[:40]!r}")
            if len(colonne) > 40 or not all(_COLONNA.match(c) for c in colonne):
                raise ValueError(f"colonne non valide per {tabella!r}")
        return valore

    @field_validator("ente")
    @classmethod
    def _ente(cls, valore: str | None) -> str | None:
        if valore is None or valore == ENTE_TUTTI:
            return valore
        return str(uuid.UUID(valore))


class SalvaPreferenze(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versione: int = Field(..., ge=0, description="Versione letta (0 = mai salvate)")
    preferenze: Preferenze


class RispostaPreferenze(BaseModel):
    preferenze: dict[str, Any]
    versione: int


def _utente(authorization: str | None) -> dict[str, Any]:
    utente = utente_da_authorization(authorization)
    try:
        uuid.UUID(str(utente.get("user_id")))
    except (TypeError, ValueError):
        raise ErroreApi("ONB-422", "Il token non indica un utente") from None
    return utente


Autorizzazione = Annotated[str | None, Header()]


@router.get("", response_model=RispostaPreferenze, summary="Preferenze personali")
async def leggi(authorization: Autorizzazione = None) -> RispostaPreferenze:
    utente = _utente(authorization)
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT preferenze::text AS p, versione FROM public.preferenze_utente "
                "WHERE user_id = CAST(:u AS uuid)"), {"u": str(utente["user_id"])})).first()
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="preferenze_lettura") from None
    if riga is None:
        return RispostaPreferenze(preferenze={}, versione=0)
    return RispostaPreferenze(preferenze=json.loads(riga[0]), versione=int(riga[1]))


@router.put("", response_model=RispostaPreferenze, summary="Salva le preferenze personali")
async def salva(corpo: SalvaPreferenze, authorization: Autorizzazione = None
                ) -> RispostaPreferenze:
    utente = _utente(authorization)
    preferenze = corpo.preferenze.model_dump(exclude_none=True)
    if "ente" in preferenze and str(utente.get("ruolo") or "") not in RUOLI_GLOBALI:
        raise ErroreApi("ONB-422", "La scelta dell'ente in testata è dei soli ruoli globali")
    uid = str(utente["user_id"])
    dati = json.dumps(preferenze, sort_keys=True)
    try:
        async with get_async_session() as session:
            if corpo.versione == 0:
                nuova = (await session.execute(text(
                    "INSERT INTO public.preferenze_utente (user_id, preferenze) "
                    "SELECT CAST(:u AS uuid), CAST(:p AS jsonb) "
                    "WHERE EXISTS (SELECT 1 FROM public.users WHERE id = CAST(:u AS uuid)) "
                    "ON CONFLICT (user_id) DO NOTHING RETURNING versione"),
                    {"u": uid, "p": dati})).scalar()
            else:
                nuova = (await session.execute(text(
                    "UPDATE public.preferenze_utente SET preferenze = CAST(:p AS jsonb), "
                    "versione = versione + 1, updated_at = now() "
                    "WHERE user_id = CAST(:u AS uuid) AND versione = :v RETURNING versione"),
                    {"u": uid, "p": dati, "v": corpo.versione})).scalar()
            if nuova is None:
                attuale = (await session.execute(text(
                    "SELECT versione FROM public.preferenze_utente "
                    "WHERE user_id = CAST(:u AS uuid)"), {"u": uid})).scalar()
                if attuale is None and corpo.versione == 0:
                    raise ErroreApi("ONB-422", "Utente non registrato")
                raise ErroreApi("ONB-412", "Preferenze modificate da un'altra sessione",
                                dettagli={"versione": int(attuale or 0)})
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="preferenze_salvataggio") from None
    return RispostaPreferenze(preferenze=preferenze, versione=int(nuova))
