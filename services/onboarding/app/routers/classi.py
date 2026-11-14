# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — F3-01: API gestione classi documentali.

VD-C-023 (R03): classi documentali per cliente.
  - Classi di base del servizio (tenant_id NULL): visibili a tutti, le crea e
    modifica SOLO SUPER_ADMIN.
  - Classi del cliente (tenant_id = cliente): le crea e modifica
    l'amministratore di quel cliente (ADMIN_TENANT o RSC_CONSERVATORE del
    cliente) o SUPER_ADMIN; un altro cliente non le vede (404, REGOLE §14.4).
  - Un cliente può creare una sua classe con lo stesso codice di una classe
    di base: per quel cliente la sua prevale (risoluzione per codice).
  - Codice unico fra le classi di base e dentro ogni cliente (vincolo
    uq_classi_tenant_codice, migrazione 20260320_009).
Prima un amministratore di un cliente poteva cambiare, per esempio, gli anni
di conservazione di una classe usata da tutti gli altri clienti.

Le colonne sono garantite dalle migrazioni: tolti i ripieghi «se la colonna
non esiste» che nascondevano gli errori SQL.
"""
from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth.jwt_handler import verify_access_token
from libs.shared.db import get_async_session
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/onboarding/classi-documentali",
                   tags=["Classi Documentali"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])

_RUOLI_AMMINISTRAZIONE = frozenset({"SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE"})
_COLONNE = (
    "id, tenant_id, codice, nome, descrizione, tipologia_xml, "
    "anni_conservazione, conservazione_permanente, "
    "formati_ammessi, metadati_obbligatori, max_dimensione_mb, "
    "attiva, created_at"
)
_MIME = r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
TipoMime = Annotated[str, StringConstraints(pattern=_MIME, max_length=255)]


# ── Modelli ─────────────────────────────────────────────────────────

class ClasseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codice:                  str = Field(..., min_length=2, max_length=50,
                                          pattern=r"^[a-z0-9_]+$")
    nome:                    str = Field(..., min_length=2, max_length=200)
    descrizione:             str | None = Field(None, max_length=5000)
    tipologia_xml:           str = Field(default="DAI",
                                          pattern="^(DI|DAI|AGG)$")
    anni_conservazione:      int = Field(default=10, ge=1, le=100)
    conservazione_permanente: bool = False
    formati_ammessi:         list[TipoMime] = Field(default_factory=list)
    metadati_obbligatori:    list[str] = Field(default_factory=list)
    max_dimensione_mb:       int = Field(default=100, ge=1, le=5120)  # 5 GiB (R03 i1)
    tenant_id:               uuid.UUID | None = Field(
        None,
        description=(
            "Cliente proprietario. Per ADMIN_TENANT/RSC_CONSERVATORE è sempre il "
            "proprio (se indicato deve coincidere). Per SUPER_ADMIN: assente = "
            "classe di base del servizio."
        ),
    )


class ClasseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome:                    str | None = Field(None, min_length=2, max_length=200)
    descrizione:             str | None = Field(None, max_length=5000)
    tipologia_xml:           str | None = Field(default=None,
                                                  pattern="^(DI|DAI|AGG)$")
    anni_conservazione:      int | None = Field(default=None, ge=1, le=100)
    conservazione_permanente: bool | None = None
    formati_ammessi:         list[TipoMime] | None = None
    metadati_obbligatori:    list[str] | None = None
    max_dimensione_mb:       int | None = Field(default=None, ge=1, le=5120)


class ClasseResponse(BaseModel):
    id:                      str
    tenant_id:               str | None
    di_base:                 bool
    codice:                  str
    nome:                    str
    descrizione:             str | None
    tipologia_xml:           str
    anni_conservazione:      int
    conservazione_permanente: bool
    formati_ammessi:         list[str]
    metadati_obbligatori:    list[str]
    max_dimensione_mb:       int
    attiva:                  bool
    created_at:              str | None


# ── Autorizzazione ───────────────────────────────────────────────────

def _check_auth(authorization: str | None) -> dict:
    """Token RS256 obbligatorio (MED-005)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, {"title": "Token mancante"})
    from app.auth.jwt_handler import verify_access_token
    return verify_access_token(authorization.split(" ", 1)[1])


def _check_admin(authorization: str | None) -> dict:
    user = _check_auth(authorization)
    ruolo = user.get("ruolo", "")
    if ruolo not in _RUOLI_AMMINISTRAZIONE:
        raise HTTPException(403, {"title": f"Ruolo {ruolo!r} non autorizzato"})
    return user


def _is_super_admin(user: dict) -> bool:
    return user.get("ruolo") == "SUPER_ADMIN"


def _tenant_utente(user: dict) -> str | None:
    """Cliente del token in forma canonica, None se assente o non valido."""
    try:
        return str(uuid.UUID(str(user.get("tenant_id") or "")))
    except ValueError:
        return None


def _tenant_obbligatorio(user: dict) -> str:
    tid = _tenant_utente(user)
    if tid is None:
        raise HTTPException(403, {"title": "Il token non indica un cliente: accesso negato"})
    return tid


def _verifica_modificabile(user: dict, classe: dict[str, Any]) -> None:
    """Le classi di base solo SUPER_ADMIN; le classi di un cliente solo
    l'amministratore di quel cliente o SUPER_ADMIN."""
    if _is_super_admin(user):
        return
    if classe["tenant_id"] is None:
        raise HTTPException(403, {
            "title": "Le classi di base del servizio le modifica solo il SUPER_ADMIN",
        })
    if classe["tenant_id"] != _tenant_utente(user):
        # Non dovrebbe accadere: _carica_classe filtra già per cliente
        raise HTTPException(404, {"title": "Classe non trovata"})


# ── Accesso ai dati ──────────────────────────────────────────────────

def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id":                      str(row[0]),
        "tenant_id":               str(row[1]) if row[1] else None,
        "di_base":                 row[1] is None,
        "codice":                  row[2],
        "nome":                    row[3],
        "descrizione":             row[4],
        "tipologia_xml":           row[5],
        "anni_conservazione":      row[6],
        "conservazione_permanente": row[7],
        "formati_ammessi":         row[8] or [],
        "metadati_obbligatori":    row[9] or [],
        "max_dimensione_mb":       row[10],
        "attiva":                  row[11],
        "created_at":              row[12].isoformat() if row[12] else None,
    }


async def _carica_classe(session: Any, user: dict, classe_id: str) -> dict[str, Any]:
    """Risolve la classe per id o per codice nel perimetro del chiamante.

    Perimetro: classi di base + classi del proprio cliente (SUPER_ADMIN:
    tutte per id; per codice solo le classi di base). Fuori perimetro → 404.
    Per codice, la classe del cliente prevale su quella di base.
    """
    super_admin = _is_super_admin(user)
    tid = _tenant_utente(user)
    try:
        cid = str(uuid.UUID(classe_id))
    except ValueError:
        cid = None

    if cid is not None:
        filtro = "id = CAST(:cid AS uuid)"
        params: dict[str, Any] = {"cid": cid}
        if not super_admin:
            filtro += " AND (tenant_id IS NULL OR tenant_id = CAST(:tid AS uuid))"
            params["tid"] = tid
    else:
        params = {"codice": classe_id}
        if super_admin or tid is None:
            filtro = "codice = :codice AND tenant_id IS NULL"
        else:
            filtro = "codice = :codice AND (tenant_id IS NULL OR tenant_id = CAST(:tid AS uuid))"
            params["tid"] = tid
    row = (await session.execute(text(
        f"SELECT {_COLONNE} FROM public.classi_documentali WHERE {filtro} "  # noqa: S608 — frammenti SQL fissi, valori come parametri
        "ORDER BY tenant_id NULLS LAST LIMIT 1"
    ), params)).fetchone()
    if not row:
        raise HTTPException(404, {"title": f"Classe {classe_id!r} non trovata"})
    return _row_to_dict(row)


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("", response_model=dict, summary="Lista classi documentali")
async def lista_classi(
    attiva: bool | None = Query(default=None),
    tenant_id: uuid.UUID | None = Query(
        default=None, description="Solo SUPER_ADMIN: classi di base + quelle di questo cliente",
    ),
    skip:  int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(default=None),
) -> dict:
    """Classi visibili al chiamante: di base + del proprio cliente.
    SUPER_ADMIN vede tutte le classi (o base + un cliente con ``tenant_id``)."""
    user = _check_auth(authorization)
    condizioni: list[str] = []
    params: dict[str, Any] = {}
    if _is_super_admin(user):
        if tenant_id is not None:
            condizioni.append("(tenant_id IS NULL OR tenant_id = CAST(:tid AS uuid))")
            params["tid"] = str(tenant_id)
    else:
        tid = _tenant_utente(user)
        if tid is None:
            condizioni.append("tenant_id IS NULL")
        else:
            condizioni.append("(tenant_id IS NULL OR tenant_id = CAST(:tid AS uuid))")
            params["tid"] = tid
    if attiva is not None:
        condizioni.append("attiva = :attiva")
        params["attiva"] = attiva
    where = ("WHERE " + " AND ".join(condizioni)) if condizioni else ""

    async with get_async_session() as session:
        total = (await session.execute(text(
            f"SELECT COUNT(*) FROM public.classi_documentali {where}"  # noqa: S608 — frammenti SQL fissi, valori come parametri
        ), params)).scalar_one()
        rows = (await session.execute(text(
            f"SELECT {_COLONNE} FROM public.classi_documentali {where} "  # noqa: S608 — frammenti SQL fissi, valori come parametri
            "ORDER BY codice, tenant_id NULLS FIRST OFFSET :skip LIMIT :limit"
        ), {**params, "skip": skip, "limit": limit})).fetchall()

    return {"total": total, "items": [_row_to_dict(r) for r in rows]}


@router.get("/{classe_id}", response_model=ClasseResponse,
            summary="Dettaglio classe documentale")
async def get_classe(
    classe_id: str,
    authorization: str | None = Header(default=None),
) -> ClasseResponse:
    user = _check_auth(authorization)
    async with get_async_session() as session:
        classe = await _carica_classe(session, user, classe_id)
    return ClasseResponse(**classe)


@router.post("", response_model=ClasseResponse, status_code=201,
             summary="Crea classe documentale")
async def crea_classe(
    body: ClasseCreate,
    authorization: str | None = Header(default=None),
) -> ClasseResponse:
    user = _check_admin(authorization)
    if _is_super_admin(user):
        proprietario = str(body.tenant_id) if body.tenant_id else None
    else:
        proprietario = _tenant_obbligatorio(user)
        if body.tenant_id is not None and str(body.tenant_id) != proprietario:
            raise HTTPException(403, {
                "title": "Si possono creare classi solo per il proprio cliente",
            })

    new_id = str(uuid.uuid4())
    try:
        async with get_async_session() as session:
            await session.execute(text("""
                INSERT INTO public.classi_documentali
                    (id, tenant_id, codice, nome, descrizione, tipologia_xml,
                     anni_conservazione, conservazione_permanente,
                     formati_ammessi, metadati_obbligatori, max_dimensione_mb, attiva)
                VALUES
                    (CAST(:id AS uuid), CAST(:tid AS uuid), :codice, :nome, :desc, :tipo,
                     :anni, :perm, CAST(:formati AS jsonb), CAST(:meta AS jsonb),
                     :max_dim, true)
            """), {
                "id":      new_id,
                "tid":     proprietario,
                "codice":  body.codice,
                "nome":    body.nome,
                "desc":    body.descrizione,
                "tipo":    body.tipologia_xml,
                "anni":    body.anni_conservazione,
                "perm":    body.conservazione_permanente,
                "formati": json.dumps(body.formati_ammessi),
                "meta":    json.dumps(body.metadati_obbligatori),
                "max_dim": body.max_dimensione_mb,
            })
            await session.commit()
    except IntegrityError:
        raise HTTPException(409, {
            "title": f"Classe con codice {body.codice!r} già esistente per questo cliente "
                     "(o cliente inesistente)",
        }) from None

    logger.info("classe_creata", codice=body.codice, id=new_id, tenant_id=proprietario,
                utente=user.get("user_id"))
    async with get_async_session() as session:
        return ClasseResponse(**await _carica_classe(session, user, new_id))


@router.put("/{classe_id}", response_model=ClasseResponse,
            summary="Aggiorna classe documentale")
async def aggiorna_classe(
    classe_id: str,
    body: ClasseUpdate,
    authorization: str | None = Header(default=None),
) -> ClasseResponse:
    user = _check_admin(authorization)

    campi = body.model_dump(exclude_none=True)
    if not campi:
        raise HTTPException(400, {"title": "Nessun campo da aggiornare"})
    colonne = {
        "nome": ("nome = :nome", None),
        "descrizione": ("descrizione = :descrizione", None),
        "tipologia_xml": ("tipologia_xml = :tipologia_xml", None),
        "anni_conservazione": ("anni_conservazione = :anni_conservazione", None),
        "conservazione_permanente": ("conservazione_permanente = :conservazione_permanente", None),
        "formati_ammessi": ("formati_ammessi = CAST(:formati_ammessi AS jsonb)", json.dumps),
        "metadati_obbligatori": (
            "metadati_obbligatori = CAST(:metadati_obbligatori AS jsonb)", json.dumps,
        ),
        "max_dimensione_mb": ("max_dimensione_mb = :max_dimensione_mb", None),
    }
    updates: list[str] = []
    params: dict[str, Any] = {}
    for nome, valore in campi.items():
        sql, conv = colonne[nome]
        updates.append(sql)
        params[nome] = conv(valore) if conv else valore

    async with get_async_session() as session:
        classe = await _carica_classe(session, user, classe_id)
        _verifica_modificabile(user, classe)
        params["id"] = classe["id"]
        await session.execute(text(
            f"UPDATE public.classi_documentali SET {', '.join(updates)}, "  # noqa: S608 — frammenti SQL fissi, valori come parametri
            "updated_at = now() WHERE id = CAST(:id AS uuid)"
        ), params)
        await session.commit()
        logger.info("classe_aggiornata", id=classe["id"], campi=sorted(campi),
                    tenant_id=classe["tenant_id"], utente=user.get("user_id"))
        return ClasseResponse(**await _carica_classe(session, user, classe["id"]))


@router.post("/{classe_id}/attiva", summary="Attiva/disattiva classe")
async def toggle_attiva(
    classe_id: str,
    attiva: bool = Query(...),
    authorization: str | None = Header(default=None),
) -> dict:
    user = _check_admin(authorization)
    async with get_async_session() as session:
        classe = await _carica_classe(session, user, classe_id)
        _verifica_modificabile(user, classe)
        await session.execute(text(
            "UPDATE public.classi_documentali SET attiva = :attiva, updated_at = now() "
            "WHERE id = CAST(:id AS uuid)"
        ), {"attiva": attiva, "id": classe["id"]})
        await session.commit()
    logger.info("classe_attivazione", id=classe["id"], attiva=attiva,
                tenant_id=classe["tenant_id"], utente=user.get("user_id"))
    return {"id": classe["id"], "attiva": attiva}
