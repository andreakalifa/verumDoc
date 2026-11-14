# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — ruoli personalizzati dell'ente (R10, D30).

Riferimenti: decisione D30 (VD-P-38, VD-P-14); catalogo
``libs.verumdoc_rules.permessi``; parametro ``RUOLI_PERMESSI_CONCEDIBILI``;
REGOLE_SVILUPPO §14.1 (un cambio di permessi è una decisione), §14.9, §15.

  GET  /api/v1/onboarding/tenant/{tenant_id}/ruoli                 catalogo, modelli, ruoli
  POST /api/v1/onboarding/tenant/{tenant_id}/ruoli                 crea
  PUT  /api/v1/onboarding/tenant/{tenant_id}/ruoli/{id}            modifica (con la versione letta)
  POST /api/v1/onboarding/tenant/{tenant_id}/ruoli/{id}/disattiva  solo se non assegnato

Chi: un ruolo FISSO che ha ``ruoli:gestisci`` (amministratore dell'ente, o il
conservatore), mai un ruolo personalizzato (permesso non delegabile).
Nessuna escalation: ogni permesso deve essere concedibile per l'ente, non mai
delegabile e già posseduto da chi compone (``motivo_composizione_non_ammessa``);
il ruolo base è OPERATORE o VIEWER (l'amministratore non crea amministratori).
Ogni modifica: voce d'audit nella stessa transazione (esportata nel WORM e nella
catena dell'audit), versione del ruolo e dei permessi degli utenti aumentata,
sessioni degli utenti con quel ruolo chiuse (serve un nuovo accesso).
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.jwt_handler import verify_access_token
from app.routers.utenti import Chiamante, _uuid_o_404
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.parametri import parametri
from libs.shared.security.audit import registra_audit
from libs.shared.security.inattivita import chiudi_sessioni_utente
from libs.shared.security.permessi import ha_permesso_non_delegabile, permessi_identita
from libs.shared.security.revoca import dipendenza_non_revocato
from libs.verumdoc_rules.parametri_servizio import RUOLI_PERMESSI_CONCEDIBILI
from libs.verumdoc_rules.permessi import (
    CONCEDIBILI_POSSIBILI,
    MAI_DELEGABILI,
    MODELLI,
    PERMESSI,
    RUOLI_BASE,
    RUOLI_GESTISCI,
    SEMPRE_INCLUSI,
    motivo_composizione_non_ammessa,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/onboarding/tenant/{tenant_id}/ruoli",
                   tags=["Ruoli personalizzati"], responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])


def richiedi_gestore(caller: dict[str, Any], permesso: str = RUOLI_GESTISCI) -> None:
    """Solo un ruolo fisso con il permesso (mai delegabile)."""
    if not ha_permesso_non_delegabile(caller, permesso):
        raise ErroreApi("AUTH-010", "Solo l'amministratore dell'ente (o il conservatore) può "
                                    "farlo: il permesso non si delega con un ruolo personalizzato")


async def concedibili_ente(tenant_id: Any) -> frozenset[str]:
    valori = await parametri([RUOLI_PERMESSI_CONCEDIBILI], tenant_id)
    return frozenset(valori.get(RUOLI_PERMESSI_CONCEDIBILI) or ())


def _errore(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("ONB-503", exc, evento=evento)


class RuoloCrea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: str = Field(..., min_length=3, max_length=100)
    descrizione: str = Field("", max_length=500)
    ruolo_base: str = Field(..., description="OPERATORE o VIEWER (perimetro del ruolo)")
    permessi: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("nome", "descrizione")
    @classmethod
    def _testo(cls, v: str) -> str:
        return v.strip()


class RuoloModifica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    versione: int = Field(..., ge=1, description="Versione letta (salvataggio con la versione)")
    nome: str | None = Field(None, min_length=3, max_length=100)
    descrizione: str | None = Field(None, max_length=500)
    permessi: list[str] | None = Field(None, max_length=50)


def _json(riga: Any) -> dict[str, Any]:
    return {"id": str(riga["id"]), "nome": riga["nome"], "descrizione": riga["descrizione"],
            "ruolo_base": riga["ruolo_base"], "permessi": sorted(riga["permessi"] or []),
            "attivo": riga["attivo"], "versione": riga["versione"],
            "utenti": int(riga.get("utenti") or 0),
            "aggiornato_il": riga["updated_at"].isoformat() if riga.get("updated_at") else None}


def _permessi_puliti(permessi: list[str]) -> list[str]:
    return sorted({p.strip() for p in permessi} | SEMPRE_INCLUSI)


def controlla_composizione(caller: dict[str, Any], ruolo_base: str, permessi: list[str],
                           concedibili: frozenset[str]) -> None:
    motivo = motivo_composizione_non_ammessa(
        ruolo_chiamante=str(caller.get("ruolo") or ""),
        permessi_chiamante=permessi_identita(caller), ruolo_base=ruolo_base,
        richiesti=permessi, concedibili=concedibili)
    if motivo:
        logger.warning("ruolo_composizione_rifiutata", motivo=motivo,
                       ruolo_chiamante=caller.get("ruolo"))
        raise ErroreApi("RUO-403", motivo)


_SQL_RUOLI = ("SELECT r.*, (SELECT count(*) FROM public.users u WHERE "
              "u.ruolo_personalizzato_id = r.id) AS utenti FROM public.ruoli_personalizzati r "
              "WHERE r.tenant_id = :t")


@router.get("", summary="Permessi, modelli e ruoli personalizzati dell'ente")
async def elenco(tenant_id: str, caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    concedibili = await concedibili_ente(tid)
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(_SQL_RUOLI + " ORDER BY r.nome"),
                                           {"t": tid})).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore("ruoli_elenco_errore", exc) from None
    return {
        "permessi": [{"codice": c, "descrizione": d, "mai_delegabile": c in MAI_DELEGABILI,
                      "concedibile": c in concedibili and c not in MAI_DELEGABILI,
                      "sempre_incluso": c in SEMPRE_INCLUSI} for c, d in PERMESSI.items()],
        "modelli": {r: sorted(MODELLI[r]) for r in ("ADMIN_TENANT", "OPERATORE", "VIEWER")},
        "ruoli_base": list(RUOLI_BASE),
        "concedibili": [c for c in CONCEDIBILI_POSSIBILI if c in concedibili],
        "puo_gestire": ha_permesso_non_delegabile(caller, RUOLI_GESTISCI),
        "items": [_json(r) for r in righe],
    }


@router.post("", status_code=201, summary="Crea un ruolo personalizzato")
async def crea(tenant_id: str, body: RuoloCrea, caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    richiedi_gestore(caller)
    permessi = _permessi_puliti(body.permessi)
    controlla_composizione(caller, body.ruolo_base, permessi, await concedibili_ente(tid))
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "INSERT INTO public.ruoli_personalizzati (tenant_id, nome, descrizione, "
                " ruolo_base, permessi, creato_da) VALUES (:t, :n, :d, :b, :p, :c) RETURNING *"),
                {"t": tid, "n": body.nome, "d": body.descrizione, "b": body.ruolo_base,
                 "p": permessi, "c": _uuid(caller.get("user_id"))})).mappings().one()
            await registra_audit(session, evento="ruolo.creato", tenant_id=str(tid),
                                 user_id=_testo_uuid(caller.get("user_id")),
                                 dettagli={"ruolo_id": str(riga["id"]), "nome": body.nome,
                                           "ruolo_base": body.ruolo_base,
                                           "permessi": permessi})
    except IntegrityError as exc:
        if "uq_ruoli_personalizzati_nome" in str(exc.orig):
            raise ErroreApi("RUO-409", "Esiste già un ruolo con questo nome") from None
        raise _errore("ruolo_crea_errore", exc) from None
    except SQLAlchemyError as exc:
        raise _errore("ruolo_crea_errore", exc) from None
    return _json(riga)


async def _invalida_utenti(session: Any, ruolo_id: uuid.UUID) -> int:
    """Versione dei permessi aumentata e sessioni chiuse per chi ha il ruolo."""
    utenti = (await session.execute(text(
        "UPDATE public.users SET permessi_versione = permessi_versione + 1 "
        "WHERE ruolo_personalizzato_id = :r RETURNING id::text"), {"r": ruolo_id})).scalars().all()
    for uid in utenti:
        await chiudi_sessioni_utente(session, uid)
    return len(utenti)


@router.put("/{ruolo_id}", summary="Modifica un ruolo personalizzato")
async def modifica(tenant_id: str, ruolo_id: str, body: RuoloModifica,
                   caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    rid = _uuid_o_404(ruolo_id, "Ruolo non trovato")
    richiedi_gestore(caller)
    concedibili = await concedibili_ente(tid)
    try:
        async with get_async_session() as session:
            prima = (await session.execute(text(
                "SELECT * FROM public.ruoli_personalizzati WHERE id = :r AND tenant_id = :t "
                "FOR UPDATE"), {"r": rid, "t": tid})).mappings().one_or_none()
            if prima is None:
                raise ErroreApi("RUO-404")
            if prima["versione"] != body.versione:
                raise ErroreApi("RUO-409", "Il ruolo è stato modificato da un'altra persona")
            permessi = (_permessi_puliti(body.permessi) if body.permessi is not None
                        else sorted(prima["permessi"]))
            # anche togliere permessi è una modifica: chi la fa deve poter
            # comporre il ruolo risultante (nessun ruolo «ereditato» più ampio)
            controlla_composizione(caller, prima["ruolo_base"], permessi, concedibili)
            dopo = (await session.execute(text(
                "UPDATE public.ruoli_personalizzati SET nome = :n, descrizione = :d, "
                " permessi = :p, versione = versione + 1, updated_at = now() "
                "WHERE id = :r RETURNING *"),
                {"n": body.nome or prima["nome"],
                 "d": prima["descrizione"] if body.descrizione is None else body.descrizione,
                 "p": permessi, "r": rid})).mappings().one()
            toccati = await _invalida_utenti(session, rid)
            await registra_audit(session, evento="ruolo.modificato", tenant_id=str(tid),
                                 user_id=_testo_uuid(caller.get("user_id")),
                                 dettagli={"ruolo_id": str(rid), "utenti": toccati,
                                           "prima": {"nome": prima["nome"],
                                                     "permessi": sorted(prima["permessi"])},
                                           "dopo": {"nome": dopo["nome"],
                                                    "permessi": permessi}})
    except IntegrityError as exc:
        if "uq_ruoli_personalizzati_nome" in str(exc.orig):
            raise ErroreApi("RUO-409", "Esiste già un ruolo con questo nome") from None
        raise _errore("ruolo_modifica_errore", exc) from None
    except SQLAlchemyError as exc:
        raise _errore("ruolo_modifica_errore", exc) from None
    return _json(dict(dopo, utenti=toccati))


@router.post("/{ruolo_id}/disattiva", summary="Disattiva un ruolo non assegnato")
async def disattiva(tenant_id: str, ruolo_id: str, caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    rid = _uuid_o_404(ruolo_id, "Ruolo non trovato")
    richiedi_gestore(caller)
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                _SQL_RUOLI + " AND r.id = :r FOR UPDATE OF r"), {"t": tid, "r": rid})
            ).mappings().one_or_none()
            if riga is None:
                raise ErroreApi("RUO-404")
            if int(riga["utenti"]) > 0:
                raise ErroreApi("RUO-409", "Il ruolo è assegnato a qualcuno: prima cambia il "
                                           "ruolo di quelle persone")
            await session.execute(text(
                "UPDATE public.ruoli_personalizzati SET attivo = false, versione = versione + 1, "
                "updated_at = now() WHERE id = :r"), {"r": rid})
            await registra_audit(session, evento="ruolo.disattivato", tenant_id=str(tid),
                                 user_id=_testo_uuid(caller.get("user_id")),
                                 dettagli={"ruolo_id": str(rid), "nome": riga["nome"]})
    except SQLAlchemyError as exc:
        raise _errore("ruolo_disattiva_errore", exc) from None
    return {"id": str(rid), "attivo": False}


async def ruolo_assegnabile(session: Any, caller: dict[str, Any], tid: uuid.UUID,
                            ruolo_id: uuid.UUID) -> str:
    """Ruolo base del ruolo personalizzato se chi chiama può assegnarlo, altrimenti
    errore. Assegnare è come comporre: nessun permesso che chi assegna non ha."""
    richiedi_gestore(caller)
    riga = (await session.execute(text(
        "SELECT ruolo_base, permessi, attivo FROM public.ruoli_personalizzati "
        "WHERE id = :r AND tenant_id = :t"), {"r": ruolo_id, "t": tid})).mappings().one_or_none()
    if riga is None or not riga["attivo"]:
        raise ErroreApi("RUO-404")
    controlla_composizione(caller, riga["ruolo_base"], list(riga["permessi"] or []),
                           await concedibili_ente(tid))
    return str(riga["ruolo_base"])


def _uuid(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


def _testo_uuid(valore: Any) -> str | None:
    u = _uuid(valore)
    return str(u) if u else None


__all__ = ["concedibili_ente", "controlla_composizione", "richiedi_gestore",
           "ruolo_assegnabile", "router"]
