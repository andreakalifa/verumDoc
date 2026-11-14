# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/api/campanella.py — campanella «da fare per te» e
preferenze delle notifiche (R10, D32).

  GET  /api/v1/notifiche/campanella          da fare (non ancora risolte) e avvisi recenti
  POST /api/v1/notifiche/campanella/letti    segna come letti (alcuni o tutti)
  GET  /api/v1/notifiche/preferenze          cosa ricevere e come, per categoria
  PUT  /api/v1/notifiche/preferenze          salva (con la versione letta)

Riferimenti: decisione D32; topic e tabella degli eventi per i clienti già
esistenti (``eventi_cliente``, contratto C3): la campanella è una LETTURA di
quegli eventi filtrata per permesso e preferenze, più lo stato «letto» per
persona (``notifiche_letture``). Un «da fare» si considera risolto quando la
pratica cambia di nuovo stato (evento ``pratica.cambio_stato`` successivo).
Gli avvisi resi obbligatori dall'ente (``NOTIFICHE_OBBLIGATORIE``, il conservatore
può fissarne per tutti, l'ente solo aggiungerne) non si spengono: PUT che li
spegne → 422 NTF-422-OBBLIGATORIA. Solo persone (token utente), mai chiavi API;
ognuno legge e scrive solo il proprio stato (utente del token verificato).
Finestra della campanella: ``CAMPANELLA_GIORNI`` (30) e al più 200 eventi.

R13b (VD-C-163): ``personali`` sono le voci della SOLA persona del token
(``notifiche_personali``, scritte dal gateway, es. modifica delle proprie
credenziali), separate dagli eventi dell'ente e mai consegnate ai webhook; si
segnano come lette con ``personali`` (o ``tutti``) di ``/campanella/letti``.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.types import BigInteger, String

from app.api.autenticazione import _INTESTAZIONE_401, _verifica_jwt
from app.domain.campanella import (
    NOMI,
    PERMESSI_CATEGORIA,
    TIPI,
    classifica,
    scelte_effettive,
    scelte_spente_obbligatorie,
)
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.parametri import parametri
from libs.shared.security.permessi import ha_permesso
from libs.shared.security.revoca import richiedi_non_revocato
from libs.verumdoc_rules.parametri_servizio import CATEGORIE_NOTIFICHE, NOTIFICHE_OBBLIGATORIE

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/notifiche", tags=["Campanella e preferenze"],
                   responses=RISPOSTE_ERRORE)

CAMPANELLA_GIORNI = 30
CAMPANELLA_MASSIMO = 200
#: R13b (VD-C-163): categoria delle voci personali (non configurabile: sempre in console)
NOME_PERSONALI = "Il tuo account"


async def persona(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Dipendenza: token utente verificato e non revocato, con ente e utente."""
    schema, _, token = (authorization or "").partition(" ")
    if schema.lower() != "bearer" or not token.strip():
        raise ErroreApi("AUTH-000", "Serve l'accesso di una persona", headers=_INTESTAZIONE_401)
    payload = _verifica_jwt(token.strip())
    await richiedi_non_revocato(payload)
    try:
        payload["_tenant"] = str(uuid.UUID(str(payload.get("tenant_id"))))
        payload["_utente"] = str(uuid.UUID(str(payload.get("user_id"))))
    except (TypeError, ValueError):
        raise ErroreApi("AUTH-021") from None
    if payload.get("ruolo") == "API_SYSTEM":
        raise ErroreApi("AUTH-010", "La campanella è delle persone, non delle chiavi API")
    return payload


persona.__verumdoc_autenticazione__ = {"metodi": ["jwt"]}  # type: ignore[attr-defined]


async def _obbligatorie(tenant_id: str) -> tuple[str, ...]:
    return tuple((await parametri([NOTIFICHE_OBBLIGATORIE], tenant_id))
                 .get(NOTIFICHE_OBBLIGATORIE) or ())


async def _scelte(session: Any, user_id: str) -> tuple[dict[str, Any], int]:
    riga = (await session.execute(text(
        "SELECT scelte, versione FROM public.notifiche_preferenze "
        "WHERE user_id = CAST(:u AS uuid)"), {"u": user_id})).first()
    return (dict(riga.scelte), int(riga.versione)) if riga else ({}, 0)


def _errore(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("NTF-503", exc, evento=evento)


@router.get("/campanella", summary="Da fare per te e avvisi recenti")
async def campanella(utente: dict[str, Any] = Depends(persona)) -> dict[str, Any]:
    tenant, uid = utente["_tenant"], utente["_utente"]
    obbligatorie = await _obbligatorie(tenant)
    dal = datetime.now(UTC) - timedelta(days=CAMPANELLA_GIORNI)
    try:
        async with get_async_session() as session:
            scelte, _versione = await _scelte(session, uid)
            effettive = scelte_effettive(scelte, obbligatorie)
            righe = (await session.execute(text(
                "SELECT id, tipo, dati, creato_il, posizione FROM public.eventi_cliente "
                "WHERE tenant_id = CAST(:t AS uuid) AND tipo = ANY(:tipi) "
                "AND posizione IS NOT NULL AND creato_il >= :dal "
                "ORDER BY posizione DESC LIMIT :n").bindparams(
                    bindparam("tipi", type_=ARRAY(String))),
                {"t": tenant, "tipi": list(TIPI), "dal": dal, "n": CAMPANELLA_MASSIMO})).all()
            voci = []
            for r in righe:
                voce = classifica(r.tipo, dict(r.dati or {}))
                if voce is None or not effettive[voce.categoria]["CONSOLE"]:
                    continue
                if not ha_permesso(utente, voce.permesso):
                    continue
                voci.append((r, voce))
            pratiche = sorted({v.pratica_id for _r, v in voci if v.da_fare and v.pratica_id})
            ultimi: dict[str, int] = {}
            if pratiche:
                ultimi = {str(p): int(pos) for p, pos in (await session.execute(text(
                    "SELECT dati ->> 'pratica_id', max(posizione) FROM public.eventi_cliente "
                    "WHERE tenant_id = CAST(:t AS uuid) AND tipo = 'pratica.cambio_stato' "
                    "AND posizione IS NOT NULL AND dati ->> 'pratica_id' = ANY(:p) "
                    "GROUP BY 1").bindparams(bindparam("p", type_=ARRAY(String))),
                    {"t": tenant, "p": pratiche})).all()}
            ids = [int(r.id) for r, _v in voci]
            letti = set((await session.execute(text(
                "SELECT evento_id FROM public.notifiche_letture WHERE user_id = CAST(:u AS uuid) "
                "AND evento_id = ANY(:i)").bindparams(bindparam("i", type_=ARRAY(BigInteger))),
                {"u": uid, "i": ids})).scalars().all()) if ids else set()
            # R13b (VD-C-163): voci PERSONALI (proprie credenziali), mai dell'ente né
            # dei webhook: solo quelle della persona del token
            personali = (await session.execute(text(
                "SELECT id, tipo, titolo, collegamento, creato_il, letto_il "
                "FROM public.notifiche_personali WHERE user_id = CAST(:u AS uuid) "
                "AND tenant_id = CAST(:t AS uuid) AND creato_il >= :dal "
                "ORDER BY creato_il DESC, id DESC LIMIT 50"),
                {"u": uid, "t": tenant, "dal": dal})).all()
    except SQLAlchemyError as exc:
        raise _errore("campanella_errore", exc) from None
    voci_personali = [{"id": int(p.id), "categoria": "PERSONALI",
                       "categoria_nome": NOME_PERSONALI, "titolo": p.titolo,
                       "collegamento": p.collegamento, "quando": p.creato_il.isoformat(),
                       "letto": p.letto_il is not None, "da_fare": False}
                      for p in personali]
    da_fare, avvisi = [], []
    for r, voce in voci:
        risolta = bool(voce.da_fare and voce.pratica_id
                       and ultimi.get(voce.pratica_id, 0) > int(r.posizione))
        elemento = {"id": int(r.id), "categoria": voce.categoria,
                    "categoria_nome": NOMI[voce.categoria], "titolo": voce.titolo,
                    "collegamento": voce.collegamento, "quando": r.creato_il.isoformat(),
                    "letto": int(r.id) in letti, "da_fare": voce.da_fare and not risolta}
        (da_fare if elemento["da_fare"] else avvisi).append(elemento)
    return {"da_fare": da_fare, "avvisi": avvisi[:50], "personali": voci_personali,
            "non_letti": sum(1 for e in da_fare + avvisi[:50] + voci_personali
                             if not e["letto"]),
            "intervallo_aggiornamento_secondi": 60}


class Letti(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] = Field(default_factory=list, max_length=500)
    #: R13b (VD-C-163): voci personali (numerazione propria)
    personali: list[int] = Field(default_factory=list, max_length=500)
    tutti: bool = False


@router.post("/campanella/letti", summary="Segna come letti")
async def segna_letti(body: Letti, utente: dict[str, Any] = Depends(persona)) -> dict[str, Any]:
    tenant, uid = utente["_tenant"], utente["_utente"]
    try:
        async with get_async_session() as session:
            if body.tutti:
                ids = list((await session.execute(text(
                    "SELECT id FROM public.eventi_cliente WHERE tenant_id = CAST(:t AS uuid) "
                    "AND tipo = ANY(:tipi) AND posizione IS NOT NULL AND creato_il >= :dal "
                    "ORDER BY posizione DESC LIMIT :n").bindparams(
                        bindparam("tipi", type_=ARRAY(String))),
                    {"t": tenant, "tipi": list(TIPI), "n": CAMPANELLA_MASSIMO,
                     "dal": datetime.now(UTC) - timedelta(days=CAMPANELLA_GIORNI)})
                ).scalars().all())
            else:
                # solo eventi DELL'ENTE (gli id di altri enti non si vedono: RLS e filtro)
                ids = list((await session.execute(text(
                    "SELECT id FROM public.eventi_cliente WHERE tenant_id = CAST(:t AS uuid) "
                    "AND id = ANY(:i)").bindparams(bindparam("i", type_=ARRAY(BigInteger))),
                    {"t": tenant, "i": body.ids})).scalars().all()) if body.ids else []
            for i in ids:
                await session.execute(text(
                    "INSERT INTO public.notifiche_letture (user_id, evento_id, tenant_id) "
                    "VALUES (CAST(:u AS uuid), :e, CAST(:t AS uuid)) ON CONFLICT DO NOTHING"),
                    {"u": uid, "e": int(i), "t": tenant})
            # R13b (VD-C-163): solo le PROPRIE voci personali (utente del token)
            personali = 0
            if body.tutti or body.personali:
                personali = int((await session.execute(text(
                    "UPDATE public.notifiche_personali SET letto_il = now() "
                    "WHERE user_id = CAST(:u AS uuid) AND tenant_id = CAST(:t AS uuid) "
                    "AND letto_il IS NULL AND (:tutti OR id = ANY(:i))").bindparams(
                        bindparam("i", type_=ARRAY(BigInteger))),
                    {"u": uid, "t": tenant, "tutti": body.tutti,
                     "i": [int(x) for x in body.personali]})).rowcount or 0)
    except SQLAlchemyError as exc:
        raise _errore("campanella_letti_errore", exc) from None
    return {"letti": len(ids) + personali}


def _preferenze_json(scelte: dict[str, Any], versione: int, obbligatorie: tuple[str, ...],
                     utente: dict[str, Any]) -> dict[str, Any]:
    effettive = scelte_effettive(scelte, obbligatorie)
    return {"versione": versione, "categorie": [{
        "codice": cat, "nome": NOMI[cat],
        "console": effettive[cat]["CONSOLE"], "email": effettive[cat]["EMAIL"],
        "console_obbligatoria": f"{cat}.CONSOLE" in obbligatorie,
        "email_obbligatoria": f"{cat}.EMAIL" in obbligatorie,
        "pertinente": any(ha_permesso(utente, p) for p in PERMESSI_CATEGORIA[cat]),
    } for cat in CATEGORIE_NOTIFICHE]}


@router.get("/preferenze", summary="Cosa ricevere e come")
async def leggi_preferenze(utente: dict[str, Any] = Depends(persona)) -> dict[str, Any]:
    obbligatorie = await _obbligatorie(utente["_tenant"])
    try:
        async with get_async_session() as session:
            scelte, versione = await _scelte(session, utente["_utente"])
    except SQLAlchemyError as exc:
        raise _errore("preferenze_notifiche_errore", exc) from None
    return _preferenze_json(scelte, versione, obbligatorie, utente)


class Scelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    console: bool = True
    email: bool = False


Categoria = Literal["PRATICHE", "PROPOSTE_SCARTO", "ANNULLAMENTI_RESPINTI", "QUOTE",
                    "SCADENZE_STAGING", "ALLARMI"]


class Preferenze(BaseModel):
    model_config = ConfigDict(extra="forbid")
    versione: int = Field(..., ge=0, description="Versione letta (0 = mai salvate)")
    scelte: dict[Categoria, Scelta]


@router.put("/preferenze", summary="Salva cosa ricevere e come")
async def salva_preferenze(body: Preferenze,
                           utente: dict[str, Any] = Depends(persona)) -> dict[str, Any]:
    scelte = {c: s.model_dump() for c, s in body.scelte.items()}
    obbligatorie = await _obbligatorie(utente["_tenant"])
    spenti = scelte_spente_obbligatorie(scelte, obbligatorie)
    if spenti:
        raise ErroreApi("NTF-422-OBBLIGATORIA", "Avvisi obbligatori per l'ente: "
                        + ", ".join(spenti), dettagli={"obbligatori": spenti})
    try:
        async with get_async_session() as session:
            if body.versione == 0:
                riga = (await session.execute(text(
                    "INSERT INTO public.notifiche_preferenze (user_id, tenant_id, scelte) "
                    "VALUES (CAST(:u AS uuid), CAST(:t AS uuid), CAST(:s AS jsonb)) "
                    "ON CONFLICT (user_id) DO NOTHING RETURNING versione"),
                    {"u": utente["_utente"], "t": utente["_tenant"],
                     "s": json.dumps(scelte)})).first()
            else:
                riga = (await session.execute(text(
                    "UPDATE public.notifiche_preferenze SET scelte = CAST(:s AS jsonb), "
                    " versione = versione + 1, updated_at = now() "
                    "WHERE user_id = CAST(:u AS uuid) AND versione = :v RETURNING versione"),
                    {"u": utente["_utente"], "s": json.dumps(scelte), "v": body.versione})
                ).first()
            if riga is None:
                raise ErroreApi("NTF-409")
    except SQLAlchemyError as exc:
        raise _errore("preferenze_notifiche_errore", exc) from None
    return _preferenze_json(scelte, int(riga[0]), obbligatorie, utente)


__all__ = ["persona", "router"]
