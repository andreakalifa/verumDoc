# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/routers/pdnd_fruitori.py — fruitori PDND (VD-C-084, R04 servizi).

  GET   /api/dashboard/pdnd/fruitori          elenco a cursore (filtro per ente)
  POST  /api/dashboard/pdnd/fruitori          registra un client PDND per un ente
  PATCH /api/dashboard/pdnd/fruitori/{id}     attiva/disattiva, descrizione

Prima di R04 i fruitori si registravano solo a mano in ``public.pdnd_fruitori``.
Solo SUPER_ADMIN (decisione del piano: la registrazione di un client PDND dà a
un sistema esterno l'accesso ai dati di un ente). Ogni scrittura ha la sua
voce di audit nella stessa transazione (REGOLE §15.1, §15.3). Un fruitore non si
elimina (le voci di audit lo citano): si disattiva.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.errori import errore_db
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi
from libs.shared.security.audit import registra_audit
from libs.shared.security.revoca import dipendenza_non_revocato

router = APIRouter(prefix="/api/dashboard/pdnd/fruitori", tags=["PDND"],
                   responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 Depends(limite_richieste)])


async def amministratore(user: dict = Depends(utente_corrente)) -> dict:
    """Solo SUPER_ADMIN con token utente."""
    if user.get("ruolo") != "SUPER_ADMIN" or user.get("auth_method") == "api_key":
        raise ErroreApi("AUTH-010", "La registrazione dei fruitori PDND è riservata "
                                    "all'amministratore del servizio (SUPER_ADMIN)")
    return user


class Fruitore(BaseModel):
    id: str
    client_id: str = Field(..., description="client_id del voucher PDND")
    tenant_id: str
    ente: str | None = None
    descrizione: str | None = None
    attivo: bool
    creato_il: str


class ElencoFruitori(BaseModel):
    items: list[Fruitore]
    limit: int
    cursore: str | None = None
    altri: bool


class NuovoFruitore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._:\-]+$",
                           description="client_id assegnato da PDND al fruitore")
    tenant_id: uuid.UUID = Field(..., description="Ente per conto del quale opera")
    descrizione: str | None = Field(None, max_length=500)


class ModificaFruitore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attivo: bool | None = None
    descrizione: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _almeno_un_campo(self) -> ModificaFruitore:
        if not self.model_fields_set:
            raise ValueError("indicare almeno un campo")
        return self


_SELECT = ("SELECT f.id::text AS id, f.client_id, f.tenant_id::text AS tenant_id, "
           "t.ragione_sociale AS ente, f.descrizione, f.attivo, f.created_at "
           "FROM public.pdnd_fruitori f JOIN public.tenants t ON t.id = f.tenant_id ")


def _vista(r) -> Fruitore:
    return Fruitore(id=r["id"], client_id=r["client_id"], tenant_id=r["tenant_id"],
                    ente=r["ente"], descrizione=r["descrizione"], attivo=r["attivo"],
                    creato_il=r["created_at"].isoformat())


def _cursore(r) -> str:
    dati = {"t": r["created_at"].isoformat(), "i": r["id"]}
    return base64.urlsafe_b64encode(json.dumps(dati).encode()).decode().rstrip("=")


def _da_cursore(cursore: str) -> tuple[datetime, str]:
    try:
        dati = json.loads(base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)))
        return datetime.fromisoformat(dati["t"]), str(uuid.UUID(dati["i"]))
    except (ValueError, KeyError, TypeError):
        raise ErroreApi("DSH-104") from None


@router.get("", response_model=ElencoFruitori, summary="Fruitori PDND registrati")
async def elenco_fruitori(
    tenant_id: uuid.UUID | None = Query(None),
    cursore: str | None = Query(None, max_length=300),
    limit: int = Query(50, ge=1, le=200),
    _user: dict = Depends(amministratore),
) -> ElencoFruitori:
    cond, par = [], {"n": limit + 1}
    if tenant_id:
        cond.append("f.tenant_id = :tid")
        par["tid"] = tenant_id
    if cursore:
        quando, ultimo = _da_cursore(cursore)
        cond.append("(f.created_at, f.id) < (:dopo_t, CAST(:dopo_i AS uuid))")
        par.update({"dopo_t": quando, "dopo_i": ultimo})
    dove = ("WHERE " + " AND ".join(cond) + " ") if cond else ""
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                _SELECT + dove + "ORDER BY f.created_at DESC, f.id DESC LIMIT :n"), par)
            ).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_db("lettura dei fruitori PDND", exc, codice="GOV-503") from None
    altri = len(righe) > limit
    righe = righe[:limit]
    return ElencoFruitori(items=[_vista(r) for r in righe], limit=limit, altri=altri,
                          cursore=_cursore(righe[-1]) if altri and righe else None)


@router.post("", response_model=Fruitore, status_code=201, summary="Registra un fruitore PDND")
async def registra_fruitore(corpo: NuovoFruitore, user: dict = Depends(amministratore)
                            ) -> Fruitore:
    """Associa un client_id PDND a un ente: da qui i voucher di quel client operano
    per l'ente (versamento, consultazione). client_id già registrato → 409."""
    nuovo = uuid.uuid4()
    try:
        async with get_async_session() as session:
            esiste = (await session.execute(text(
                "SELECT 1 FROM public.tenants WHERE id = :t"), {"t": corpo.tenant_id})).first()
            if esiste is None:
                raise ErroreApi("GOV-404")
            try:
                async with session.begin_nested():
                    await session.execute(text(
                        "INSERT INTO public.pdnd_fruitori (id, client_id, tenant_id, descrizione, "
                        " attivo) VALUES (:id, :c, :t, :d, true)"),
                        {"id": nuovo, "c": corpo.client_id, "t": corpo.tenant_id,
                         "d": corpo.descrizione})
            except IntegrityError:
                raise ErroreApi("GOV-409-FRUITORE") from None
            await registra_audit(session, evento="pdnd.fruitore_registrato",
                                 tenant_id=str(corpo.tenant_id), user_id=user.get("user_id"),
                                 dettagli={"fruitore_id": str(nuovo),
                                           "client_id": corpo.client_id})
            riga = (await session.execute(text(_SELECT + "WHERE f.id = :id"),
                                          {"id": nuovo})).mappings().one()
    except SQLAlchemyError as exc:
        raise errore_db("registrazione del fruitore PDND", exc, codice="GOV-503") from None
    return _vista(riga)


@router.patch("/{fruitore_id}", response_model=Fruitore, summary="Modifica un fruitore PDND")
async def modifica_fruitore(fruitore_id: str, corpo: ModificaFruitore,
                            user: dict = Depends(amministratore)) -> Fruitore:
    try:
        fid = uuid.UUID(fruitore_id)
    except ValueError:
        raise ErroreApi("GOV-404-FRUITORE") from None
    campi = corpo.model_dump(exclude_unset=True)
    try:
        async with get_async_session() as session:
            prima = (await session.execute(text(_SELECT + "WHERE f.id = :id FOR UPDATE OF f"),
                                           {"id": fid})).mappings().first()
            if prima is None:
                raise ErroreApi("GOV-404-FRUITORE")
            await session.execute(text(
                "UPDATE public.pdnd_fruitori SET attivo = COALESCE(:a, attivo), "
                " descrizione = CASE WHEN :cambia_d THEN :d ELSE descrizione END WHERE id = :id"),
                {"a": campi.get("attivo"), "cambia_d": "descrizione" in campi,
                 "d": campi.get("descrizione"), "id": fid})
            await registra_audit(session, evento="pdnd.fruitore_modificato",
                                 tenant_id=prima["tenant_id"], user_id=user.get("user_id"),
                                 dettagli={"fruitore_id": str(fid), "client_id": prima["client_id"],
                                           "campi": sorted(campi),
                                           "prima": {"attivo": prima["attivo"]},
                                           "dopo": {"attivo": campi.get("attivo",
                                                                        prima["attivo"])}})
            dopo = (await session.execute(text(_SELECT + "WHERE f.id = :id"),
                                          {"id": fid})).mappings().one()
    except SQLAlchemyError as exc:
        raise errore_db("modifica del fruitore PDND", exc, codice="GOV-503") from None
    return _vista(dopo)
