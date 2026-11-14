# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
F3.2 — Fascicoli Informatici
Un fascicolo (aggregazione documentale) raggruppa più AIP correlati di un
cliente in un'unità logica.

CRIT-003 FIX: auth JWT obbligatoria su tutti gli endpoint.
VD-C-026 (R03): creazione e dettaglio funzionanti; il fascicolo ha tenant_id
NOT NULL e ogni lettura/scrittura è filtrata per il cliente del token. Un
fascicolo o un AIP di un altro cliente risponde 404 (REGOLE §14.4); l'AIP
aggiunto deve appartenere allo stesso cliente del fascicolo (§14.6).
Permessi: lettura per ogni utente autenticato del cliente; creazione e
aggiunta di AIP vietate a VIEWER (G2, decisione del coordinatore §14.1).
Metadati Allegato 5 delle aggregazioni: coperti solo in parte (vedi il
docstring di libs.shared.models.Fascicolo).

R05 (VD-C-087, agente r05-ciclo-vita): ``GET /api/v2/fascicoli`` elenco a cursore
``{items, limit, cursore, altri}`` (filtri stato, anno, tipo) e
``POST /api/v2/fascicoli/{id}/chiudi`` con la data di chiusura (predefinita oggi,
fra apertura e oggi), che il servizio ciclo-vita usa per il termine di scarto.
Regole e query in ``app.services.fascicoli``.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.services import fascicoli as servizio_fascicoli
from libs.shared.db import get_async_session
from libs.shared.errori import errore_registrato
from libs.shared.models import Aip, Fascicolo, FascicoloAip, User
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v2/fascicoli", tags=["Fascicoli"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 # R03 f2 (REV-03): limite di richieste al minuto del piano
                                 Depends(limite_richieste)])

def _fascicoli_auth(authorization: str | None) -> dict:
    """CRIT-003 FIX: auth obbligatoria su tutti gli endpoint fascicoli."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, {"type": "FSC-401", "title": "Token JWT obbligatorio"})
    return verify_access_token(authorization.split(" ", 1)[1])


# G2 (decisione del coordinatore, §14.1): VIEWER legge ma non crea né modifica
# fascicoli. Elenco chiuso dei ruoli che scrivono: un ruolo sconosciuto → 403.
RUOLI_SCRITTURA = ("SUPER_ADMIN", "ADMIN_TENANT", "OPERATORE", "RSC_CONSERVATORE", "API_SYSTEM")


def _richiede_scrittura(user: dict) -> None:
    from libs.shared.security.permessi import motivo_permesso_mancante

    # R10 (revisione V3): con un ruolo personalizzato (base OPERATORE) serve anche il
    # permesso di versare: creare, chiudere e riempire fascicoli è lavoro di versamento
    if user.get("ruolo") not in RUOLI_SCRITTURA \
            or motivo_permesso_mancante(user, "versamento:esegui"):
        raise HTTPException(403, {
            "type": "FSC-403-RUOLO",
            "title": "Il tuo profilo può consultare i fascicoli ma non crearli o modificarli",
        })


def _tenant_del_token(user: dict) -> uuid.UUID:
    """Cliente del token; senza cliente non si vede né si crea nulla (§14.3)."""
    try:
        return uuid.UUID(str(user.get("tenant_id") or ""))
    except ValueError:
        raise HTTPException(
            403, {"type": "FSC-403", "title": "Il token non indica un cliente: accesso negato"},
        ) from None


def _uuid_o_404(valore: str, titolo: str) -> uuid.UUID:
    try:
        return uuid.UUID(valore)
    except ValueError:
        raise HTTPException(404, {"type": "FSC-404", "title": titolo}) from None


class FascicoloCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titolo: str = Field(..., min_length=2, max_length=500,
                        description="Oggetto del fascicolo (Allegato 5: ChiaveDescrittiva)")
    descrizione: str | None = Field(None, max_length=5000)
    codice: str | None = Field(
        None, min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ./_-]*$",
        description="Identificativo dell'aggregazione nel cliente; se assente viene generato",
    )
    tipo: str = Field(default="GENERICO",
                      pattern="^(GENERICO|PROCEDIMENTO|CONTRATTO|PROGETTO|PERSONA)$")
    anno: int | None = Field(None, ge=1900, le=2100)
    responsabile_id: uuid.UUID | None = None
    data_apertura: date | None = Field(
        None, description="Data di apertura (Allegato 5: DataApertura); default oggi"
    )


class FascicoloResponse(BaseModel):
    id: str
    codice: str
    titolo: str
    descrizione: str | None
    tipo: str
    anno: int | None
    stato: str
    data_apertura: str
    num_aip: int
    created_at: str
    data_chiusura: str | None = None


class FascicoloElenco(BaseModel):
    id: str
    codice: str
    titolo: str
    descrizione: str | None
    tipo: str
    anno: int | None
    stato: str
    data_apertura: date
    data_chiusura: date | None
    num_aip: int
    created_at: datetime


class ElencoFascicoli(BaseModel):
    items: list[FascicoloElenco]
    limit: int
    cursore: str | None = Field(None, description="Da passare come `cursore` per la pagina dopo")
    altri: bool


class ChiusuraFascicolo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_chiusura: date | None = Field(
        None, description="Allegato 5: DataChiusura (predefinita oggi, ora italiana); fra la "
                          "data di apertura e oggi. Decorre da qui il termine di scarto dei PdA")
    motivo: str = Field(..., min_length=10, max_length=1000)
    stato_atteso: str | None = Field(None, description="Stato letto dal client (409 se cambiato)")


class FascicoloChiuso(BaseModel):
    id: str
    stato: str
    data_chiusura: str


class AggiungiAIPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aip_id: str
    ruolo: str = Field(default="DOCUMENTO", min_length=1, max_length=50,
                       description="Ruolo dell'AIP nel fascicolo")
    note: str | None = Field(None, max_length=2000)


def _risposta(f: Fascicolo, num_aip: int) -> FascicoloResponse:
    return FascicoloResponse(
        id=str(f.id),
        codice=f.codice,
        titolo=f.titolo,
        descrizione=f.descrizione,
        tipo=f.tipo,
        anno=f.anno,
        stato=f.stato,
        data_apertura=f.data_apertura.isoformat(),
        num_aip=num_aip,
        created_at=f.created_at.isoformat(),
        data_chiusura=f.data_chiusura.isoformat() if f.data_chiusura else None,
    )


async def _fascicolo_del_cliente(session, fascicolo_id: str, tenant_id: uuid.UUID) -> Fascicolo:
    fid = _uuid_o_404(fascicolo_id, "Fascicolo non trovato")
    f = (await session.execute(
        select(Fascicolo).where(Fascicolo.id == fid, Fascicolo.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, {"type": "FSC-404", "title": "Fascicolo non trovato"})
    return f


@router.get("", response_model=ElencoFascicoli, summary="Elenco dei fascicoli dell'ente")
async def elenco_fascicoli(
    authorization: str | None = Header(default=None),
    stato: str | None = Query(None, pattern="^(APERTO|CHIUSO)$"),
    anno: int | None = Query(None, ge=1900, le=2100),
    tipo: str | None = Query(None, pattern="^(GENERICO|PROCEDIMENTO|CONTRATTO|PROGETTO|PERSONA)$"),
    cursore: str | None = Query(None, max_length=500),
    limite: int = Query(50, ge=1, le=200),
) -> dict:
    """VD-C-087: fascicoli del cliente del token dal più recente, a cursore."""
    user = _fascicoli_auth(authorization)
    tenant_id = _tenant_del_token(user)
    try:
        async with get_async_session() as session:
            return await servizio_fascicoli.elenca(session, tenant_id, stato=stato, anno=anno,
                                                   tipo=tipo, cursore=cursore, limite=limite)
    except SQLAlchemyError as exc:
        raise errore_registrato("FSC-503", exc, evento="elenco_fascicoli") from None


@router.post("/{fascicolo_id}/chiudi", response_model=FascicoloChiuso,
             summary="Chiudi il fascicolo (data di chiusura per il termine di scarto)")
async def chiudi_fascicolo(
    fascicolo_id: str,
    body: ChiusuraFascicolo,
    authorization: str | None = Header(default=None),
) -> dict:
    """Solo un fascicolo APERTO; data di chiusura fra apertura e oggi; dopo la
    chiusura il fascicolo non accetta altri PdA. Audit ``fascicolo.chiuso``."""
    user = _fascicoli_auth(authorization)
    _richiede_scrittura(user)
    tenant_id = _tenant_del_token(user)
    fid = _uuid_o_404(fascicolo_id, "Fascicolo non trovato")
    try:
        utente = str(uuid.UUID(str(user.get("user_id"))))
    except ValueError:
        utente = None
    try:
        async with get_async_session() as session:
            esito = await servizio_fascicoli.chiudi(
                session, tenant_id=tenant_id, fascicolo_id=fid, data_chiusura=body.data_chiusura,
                motivo=body.motivo, utente_id=utente, stato_atteso=body.stato_atteso)
    except SQLAlchemyError as exc:
        raise errore_registrato("FSC-503", exc, evento="chiusura_fascicolo") from None
    logger.info("fascicolo_chiuso", fascicolo_id=str(fid), tenant_id=str(tenant_id))
    return esito


@router.post("", response_model=FascicoloResponse, status_code=201,
             summary="Crea fascicolo informatico")
async def crea_fascicolo(
    body: FascicoloCreate,
    authorization: str | None = Header(default=None),
) -> FascicoloResponse:
    """Crea un nuovo fascicolo informatico del cliente del token."""
    user = _fascicoli_auth(authorization)
    _richiede_scrittura(user)
    tenant_id = _tenant_del_token(user)
    oggi = datetime.now(ZoneInfo("Europe/Rome")).date()
    codice = body.codice or f"FASC-{body.anno or oggi.year}-{secrets.token_hex(4).upper()}"

    try:
        async with get_async_session() as session:
            if body.responsabile_id is not None:
                # Revisione B7: il responsabile è un utente ATTIVO dello stesso
                # ente; altrimenti 422, uguale per «inesistente» e «di un altro
                # ente» (nessun oracolo sugli identificativi di altri clienti).
                responsabile = (await session.execute(
                    select(User.id).where(User.id == body.responsabile_id,
                                          User.tenant_id == tenant_id, User.attivo.is_(True))
                )).scalar_one_or_none()
                if responsabile is None:
                    raise HTTPException(422, {
                        "type": "FSC-422-RESPONSABILE",
                        "title": "Il responsabile deve essere un utente attivo di questo ente",
                    })
            fascicolo = Fascicolo(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                codice=codice,
                titolo=body.titolo,
                descrizione=body.descrizione,
                tipo=body.tipo,
                anno=body.anno,
                responsabile_id=body.responsabile_id,
                data_apertura=body.data_apertura or oggi,
            )
            session.add(fascicolo)
            await session.commit()
            await session.refresh(fascicolo)
    except IntegrityError:
        raise HTTPException(
            409, {"type": "FSC-409", "title": "Codice di fascicolo già usato"},
        ) from None

    logger.info("fascicolo_creato", fascicolo_id=str(fascicolo.id),
                tenant_id=str(tenant_id))
    return _risposta(fascicolo, 0)


@router.get("/{fascicolo_id}", response_model=FascicoloResponse,
            summary="Dettaglio fascicolo")
async def get_fascicolo(
    fascicolo_id: str,
    authorization: str | None = Header(default=None),
) -> FascicoloResponse:
    user = _fascicoli_auth(authorization)
    tenant_id = _tenant_del_token(user)
    async with get_async_session() as session:
        f = await _fascicolo_del_cliente(session, fascicolo_id, tenant_id)
        num_aip = (await session.execute(
            select(func.count()).select_from(FascicoloAip)
            .where(FascicoloAip.fascicolo_id == f.id)
        )).scalar_one()
    return _risposta(f, int(num_aip))


@router.post("/{fascicolo_id}/aip", status_code=200, summary="Aggiungi AIP al fascicolo")
async def aggiungi_aip(
    fascicolo_id: str,
    body: AggiungiAIPRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    """Associa un AIP del cliente al fascicolo dello stesso cliente."""
    user = _fascicoli_auth(authorization)
    _richiede_scrittura(user)
    tenant_id = _tenant_del_token(user)
    aip_id = _uuid_o_404(body.aip_id, "AIP non trovato")
    try:
        async with get_async_session() as session:
            f = await _fascicolo_del_cliente(session, fascicolo_id, tenant_id)
            if f.stato != "APERTO":
                raise HTTPException(
                    409, {"type": "FSC-409", "title": "Il fascicolo non è aperto"},
                )
            aip = (await session.execute(
                select(Aip.id).where(Aip.id == aip_id, Aip.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if aip is None:
                raise HTTPException(404, {"type": "FSC-405", "title": "AIP non trovato"})
            session.add(FascicoloAip(
                fascicolo_id=f.id, aip_id=aip_id, ruolo=body.ruolo, note=body.note,
            ))
            await session.commit()
    except IntegrityError:
        raise HTTPException(
            409, {"type": "FSC-409", "title": "AIP già presente nel fascicolo"},
        ) from None

    logger.info("aip_aggiunto_al_fascicolo",
                fascicolo_id=fascicolo_id, aip_id=str(aip_id), tenant_id=str(tenant_id))
    return {"ok": True, "fascicolo_id": str(f.id), "aip_id": str(aip_id)}


@router.get("/{fascicolo_id}/aip", summary="Lista AIP nel fascicolo")
async def lista_aip_fascicolo(
    fascicolo_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    user = _fascicoli_auth(authorization)
    tenant_id = _tenant_del_token(user)
    async with get_async_session() as session:
        f = await _fascicolo_del_cliente(session, fascicolo_id, tenant_id)
        righe = (await session.execute(
            select(Aip, FascicoloAip.ruolo)
            .join(FascicoloAip, FascicoloAip.aip_id == Aip.id)
            .where(FascicoloAip.fascicolo_id == f.id, Aip.tenant_id == tenant_id)
            .order_by(FascicoloAip.data_inserimento)
        )).all()
    return {
        "fascicolo_id": str(f.id),
        "total": len(righe),
        "items": [
            {"aip_id": str(a.id), "urn": a.urn, "stato": a.stato.value, "ruolo": ruolo}
            for a, ruolo in righe
        ],
    }
