# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/routers/dashboard.py
Sprint 0 FIX-001: _require_role ora usa verify_access_token (RS256)
Sprint 0 FIX-006: log_audit attivato su operazioni critiche
Sprint 1 (preparazione): authorization aggiunto a endpoint che ne erano privi
R03 (VD-C-022, VD-C-025): query con CAST(:p AS uuid); contratti e nomine
leggono le colonne reali e hanno la creazione (POST); notifiche filtrate per
tenant_id invece che con LIKE sul destinatario.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_ricerca, limite_richieste
from app.config import settings
from app.errori import (
    ErroreApi,
    errore_da_servizio_a_valle,
    errore_db,
    errore_interno,
    errore_servizio_irraggiungibile,
)
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 # R03 f2 (REV-03): limite di richieste al minuto del piano
                                 Depends(limite_richieste)])
_T = httpx.Timeout(15.0)


# ── Autenticazione — Sprint 0 FIX-001 ──────────────────────────────
# PRIMA: base64 decode senza verifica firma (bypass completo)
# DOPO:  verify_access_token con verifica firma RS256

def _require_role(authorization: str | None, *allowed_roles: str) -> dict:
    """
    Verifica JWT Bearer con firma RS256 e controlla il ruolo.
    Sprint 0 FIX-001: sostituisce la decodifica base64 senza verifica.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            {"type": "AUTH-000", "title": "Token mancante",
             "detail": "Header Authorization: Bearer <token> richiesto"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1]
    payload = verify_access_token(token)

    if allowed_roles and payload.get("ruolo") not in allowed_roles:
        raise HTTPException(
            403,
            {"type": "AUTH-010",
             "title": "Ruolo non autorizzato",
             "detail": f"Richiesto uno tra: {list(allowed_roles)}"},
        )
    return payload


def _require_auth(authorization: str | None) -> dict:
    """Richiede autenticazione senza filtro ruolo."""
    return _require_role(authorization)


def _require_admin(authorization: str | None) -> dict:
    return _require_role(
        authorization, "SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE",
    )


def _require_super_admin(authorization: str | None) -> dict:
    return _require_role(authorization, "SUPER_ADMIN")


def _check_tenant_access(user: dict, requested_tenant_id: str) -> None:
    """
    Verifica che l'utente possa accedere al tenant richiesto.
    SUPER_ADMIN e RSC_CONSERVATORE possono accedere a qualsiasi tenant.
    Gli altri possono accedere solo al proprio.
    """
    user_tenant = user.get("tenant_id", "")
    ruolo = user.get("ruolo", "")
    if ruolo in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
        return
    if str(user_tenant) != str(requested_tenant_id):
        raise HTTPException(
            403,
            {"type": "AUTH-020",
             "title": "Accesso negato a questo tenant",
             "detail": "Puoi accedere solo ai dati del tuo tenant"},
        )


# ── Helpers HTTP ────────────────────────────────────────────────────

# G2 (VD-C-014, §18): l'errore di un servizio a valle conserva il suo codice e
# il suo «detail» JSON; un 5xx o un servizio irraggiungibile diventano 502 con
# id di correlazione, senza testo d'eccezione verso il client.

async def _chiama(
    metodo: str, url: str, *, servizio: str, params: dict | None = None,
    body: dict | None = None, authorization: str | None = None,
    intestazioni: dict | None = None, timeout: httpx.Timeout | float | None = None,
) -> dict:
    headers = {"Authorization": authorization} if authorization else {}
    headers.update(intestazioni or {})
    try:
        async with httpx.AsyncClient(timeout=timeout or _T) as c:
            r = await c.request(metodo, url, params=params or None, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise errore_servizio_irraggiungibile(servizio, exc) from None
    if r.status_code >= 400:
        raise errore_da_servizio_a_valle(servizio, r)
    try:
        return r.json()
    except ValueError as exc:
        raise errore_interno(502, "DSH-502", f"Risposta non leggibile dal servizio {servizio}",
                             exc, servizio=servizio) from None


async def _get(url: str, params: dict | None = None, authorization: str | None = None,
               *, servizio: str = "a valle") -> dict:
    """CONFLICT FIX: accetta authorization opzionale e lo forwarda."""
    return await _chiama("GET", url, servizio=servizio, params=params,
                         authorization=authorization)


async def _post(url: str, body: dict, authorization: str | None = None,
                *, servizio: str = "a valle", intestazioni: dict | None = None,
                timeout: httpx.Timeout | float | None = None) -> dict:
    """CONFLICT FIX: accetta authorization opzionale e lo forwarda."""
    return await _chiama("POST", url, servizio=servizio, body=body,
                         authorization=authorization, intestazioni=intestazioni,
                         timeout=timeout)


# ── Endpoint ────────────────────────────────────────────────────────

@router.get("/overview")
async def overview(
    tenant_id: str = Query(...),
    authorization: str | None = Header(default=None),
) -> dict:
    user = _require_auth(authorization)
    tid = _tenant_lettura(user, tenant_id)

    from sqlalchemy import select

    from libs.shared.db import get_async_session_lettura
    from libs.shared.db.contatori import per_stato, valori
    from libs.shared.models import AipStato, HashchainBlock, Tenant

    # R04 (piano §4.2): PdA per stato dai contatori per ente (trigger su aip,
    # consolidati da libs.shared.db.contatori), mai 8 COUNT(*) su aip (127 ms a
    # 1 M PdV → 0,46 ms misurati da r04-dati). Sessione di sola lettura (replica).
    async with get_async_session_lettura() as session:
        tenant = (await session.execute(
            select(Tenant).where(Tenant.id == tid)
        )).scalar_one_or_none()
        conteggi = per_stato(await valori(session, tenant_id=tid, prefisso="aip.stato."),
                             "aip.stato.")
        aip_counts: dict[str, int] = {s.value: conteggi.get(s.value, 0) for s in AipStato}
        last = (await session.execute(
            select(HashchainBlock)
            .order_by(HashchainBlock.numero_blocco.desc()).limit(1)
        )).scalar_one_or_none()

    quota = float(getattr(tenant, "storage_quota_gb", 100) or 100)
    usato = float(getattr(tenant, "storage_usato_gb", 0) or 0)
    return {
        "tenant": {
            "id": str(tenant.id) if tenant else tenant_id,
            "ragione_sociale": getattr(tenant, "ragione_sociale", "—"),
            "piano": getattr(tenant, "piano", "—"),
            "stato": tenant.stato.value if tenant else "—",
            "storage_quota_gb": quota,
            "storage_usato_gb": round(usato, 2),
            "storage_percentuale": round(usato / quota * 100, 1) if quota else 0.0,
        },
        "aip_stats": {
            "totale": sum(aip_counts.values()),
            "per_stato": aip_counts,
            "conservati": (
                aip_counts.get("CONSERVATO", 0) + aip_counts.get("IN_CATENA", 0)
            ),
        },
        "hashchain": {
            "ultimo_blocco": last.numero_blocco if last else 0,
            "hash_ultimo": (last.hash_blocco[:16] + "...") if last else "—",
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── Ente su cui si legge ────────────────────────────────────────────

def _tenant_lettura(user: dict, tenant_id: str | None) -> uuid.UUID:
    """
    Ente letto da una richiesta di elenco/ricerca. Con ``tenant_id``: UUID
    valido (400) e accessibile al profilo (403, SUPER_ADMIN/RSC qualsiasi ente
    — metadati, VD-C-005). Senza: l'ente del token; un profilo globale senza
    ente deve sceglierne uno (400), gli altri senza ente → 403.
    """
    if tenant_id:
        try:
            tid = uuid.UUID(str(tenant_id))
        except ValueError:
            raise HTTPException(400, {"type": "DSH-400", "title": "tenant_id non valido"}) from None
        _check_tenant_access(user, str(tid))
        return tid
    try:
        return uuid.UUID(str(user.get("tenant_id") or ""))
    except ValueError:
        if user.get("ruolo") in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
            raise HTTPException(400, {"type": "DSH-400-ENTE",
                                      "title": "Scegli un ente: tenant_id obbligatorio"}) from None
        raise HTTPException(403, {"type": "AUTH-020",
                                  "title": "Il token non indica un ente: accesso negato"}) from None


def _enum_o_400(tipo, valore: str | None, nome: str):
    if not valore:
        return None
    try:
        return tipo(valore)
    except ValueError:
        raise HTTPException(422, {"type": "DSH-422", "title": f"{nome} non valido: {valore!r}",
                                  "detail": f"Valori ammessi: {[e.value for e in tipo]}"}) from None


@router.get("/versamenti")
async def versamenti(
    tenant_id: str | None = Query(
        None, description="Ente da leggere; facoltativo: senza, l'ente dell'identità "
                          "(token o chiave API). Solo i profili globali possono indicarne un "
                          "altro"),
    stato: str | None = Query(None, description="Stato del PdV (es. ERRORE)"),
    cursore: str | None = Query(None, max_length=200,
                                description="Cursore restituito dalla pagina precedente"),
    limit: int = Query(20, ge=1, le=100),
    skip: int | None = Query(None, ge=0, deprecated=True,
                             description="Non più supportato (R03): usare `cursore`"),
    user: dict = Depends(utente_corrente),
) -> dict:
    """Versamenti (PdV) dell'ente dal più recente, con paginazione a cursore
    (R03 i1: niente offset né ``COUNT(*)``, regge milioni di righe). Risposta
    ``{items, limit, cursore, altri}``: ``cursore`` (se ``altri``) apre la pagina
    successiva. Per i PdV in ERRORE il motivo del rifiuto letto da
    ``pdv.errori_validazione``. Anche con chiave API (VD-C-099): l'ente viene
    dall'identità. ``skip`` > 0 → 422 DSH-422 (prima: offset)."""
    if skip:
        raise HTTPException(422, {"type": "DSH-422",
                                  "title": "Parametro skip non più supportato: usare cursore",
                                  "detail": "Paginazione a cursore: ripetere la richiesta con il "
                                            "`cursore` della risposta precedente."})
    tid = _tenant_lettura(user, tenant_id)
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.archivio import CursoreNonValido, elenco_versamenti
    from libs.shared.models import PdvStato

    filtro = _enum_o_400(PdvStato, stato, "Stato del versamento")
    try:
        return await elenco_versamenti(tid, stato=filtro, cursore=cursore, limit=limit)
    except CursoreNonValido:
        raise HTTPException(422, {"type": "DSH-104", "title": "Cursore non valido"}) from None
    except SQLAlchemyError as exc:
        raise errore_db("lettura dei versamenti", exc, codice="DSH-503") from None


@router.get("/aip")
async def lista_aip(
    tenant_id: str | None = Query(
        None, description="Ente da leggere; facoltativo: senza, l'ente dell'identità. "
                          "Solo i profili globali possono indicarne un altro"),
    stato: str | None = Query(None),
    cursore: str | None = Query(None, max_length=300,
                                description="Cursore restituito dalla pagina precedente"),
    limit: int = Query(20, ge=1, le=100),
    skip: int | None = Query(None, ge=0, deprecated=True,
                             description="Non più supportato (R03): usare `cursore`"),
    user: dict = Depends(utente_corrente),
) -> dict:
    """Pacchetti di archiviazione dell'ente dal più recente, con classe
    documentale, oggetto e metadati dell'Allegato 5 di ciascun documento.
    Paginazione a cursore (R03 f2, REV-06: niente offset né ``COUNT(*)``):
    risposta ``{items, limit, cursore, altri, campi_allegato5}``. Anche con
    chiave API (VD-C-099). ``skip`` > 0 → 422 DSH-422."""
    _rifiuta_skip(skip)
    tid = _tenant_lettura(user, tenant_id)
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.archivio import CursoreNonValido, elenco_aip
    from libs.shared.models import AipStato

    filtro = _enum_o_400(AipStato, stato, "Stato del pacchetto")
    try:
        return await elenco_aip(tid, stato=filtro, cursore=cursore, limit=limit)
    except CursoreNonValido:
        raise HTTPException(422, {"type": "DSH-104", "title": "Cursore non valido"}) from None
    except SQLAlchemyError as exc:
        raise errore_db("lettura dei pacchetti", exc, codice="DSH-503") from None


def _rifiuta_skip(skip: int | None) -> None:
    if skip:
        raise HTTPException(422, {"type": "DSH-422",
                                  "title": "Parametro skip non più supportato: usare cursore",
                                  "detail": "Paginazione a cursore: ripetere la richiesta con il "
                                            "`cursore` della risposta precedente."})


# ── Catena delle impronte ───────────────────────────────────────────

@router.get("/hashchain")
async def hashchain_status(
    limit: int = Query(5, ge=1, le=100, description="Blocchi recenti da restituire"),
    authorization: str | None = Header(default=None),
) -> dict:
    """Stato della catena (servizio hashchain): blocchi recenti con stato di
    firma, conteggi per stato di firma, esito dell'ultima verifica completa."""
    _require_auth(authorization)
    # g4: il token dell'utente si inoltra: hashchain verifica di nuovo firma e
    # ruolo (g3-backend, §19.1) e senza token risponderebbe 401.
    return await _get(f"{settings.hashchain_url}/api/v1/hashchain/status",
                      {"limit": limit}, authorization=authorization, servizio="hashchain")


@router.post("/hashchain/verifica-completa", status_code=202)
async def hashchain_verifica_completa(
    authorization: str | None = Header(default=None),
) -> dict:
    """Avvia la verifica completa della catena (ricalcolo di impronte,
    collegamenti e radici di Merkle di tutti i blocchi). g4-chiusura: la
    verifica è ASINCRONA nel servizio hashchain; la risposta arriva subito:
    202 ``{"verifica_id", "stato": "IN_CORSO", "avviata_il"}``, oppure 409
    HC-409 con ``verifica_id`` di quella già in corso. Lo stato si legge con
    GET /hashchain/verifica-completa/{verifica_id}. Solo SUPER_ADMIN e
    RSC_CONSERVATORE; il token si inoltra (hashchain verifica di nuovo ruolo
    ed esecutore)."""
    user = _require_role(authorization, "SUPER_ADMIN", "RSC_CONSERVATORE")
    return await _post(
        f"{settings.hashchain_url}/api/v1/hashchain/verifica-completa",
        {"avviata_da": user.get("user_id") or user.get("sub"), "ruolo": user.get("ruolo")},
        authorization=authorization,
        servizio="hashchain",
    )


@router.get("/hashchain/verifica-completa/{verifica_id}")
async def hashchain_stato_verifica(
    verifica_id: uuid.UUID,
    authorization: str | None = Header(default=None),
) -> dict:
    """Stato di una verifica completa: IN_CORSO / COMPLETATA / FALLITA /
    INTERROTTA, esito e date (vedi il servizio hashchain). Solo SUPER_ADMIN e
    RSC_CONSERVATORE."""
    _require_role(authorization, "SUPER_ADMIN", "RSC_CONSERVATORE")
    return await _get(
        f"{settings.hashchain_url}/api/v1/hashchain/verifica-completa/{verifica_id}",
        authorization=authorization, servizio="hashchain",
    )


# ── Ricerca ─────────────────────────────────────────────────────────

class SearchReq(BaseModel):
    """Ricerca dei pacchetti di un ente. Tutti i filtri indicati sono applicati."""
    query: str | None = Field(None, max_length=200,
                              description="Testo libero (indice di ricerca)")
    classe_documentale: str | None = Field(None, max_length=100,
                                           description="Codice o id della classe documentale")
    anno: str | None = Field(None, pattern=r"^\d{4}$",
                             description="Anno di versamento (fuso Europe/Rome)")
    stato: str | None = Field(None, description="Stato del pacchetto")
    tenant_id: str | None = None
    cursore: str | None = Field(None, max_length=1000,
                                description="Cursore restituito dalla pagina precedente")
    skip: int | None = Field(None, ge=0, json_schema_extra={"deprecated": True},
                             description="Non più supportato (R03): usare `cursore`")
    limit: int = Field(20, ge=1, le=100)


#: Intestazioni d'autenticazione inoltrate a distribution (la ricerca sull'indice
#: si fa con l'identità del chiamante: stessi permessi e stesse quote).
_INOLTRATE = ("authorization", "x-api-key", "x-api-key-id", "x-api-key-secret", "x-request-id")
#: Trasporto HTTP verso distribution (sostituibile nei test, REGOLE §20.1).
TRASPORTO_DISTRIBUTION: httpx.AsyncBaseTransport | None = None


def _ricerca_su_distribution(request: Request):
    """Funzione che passa una ricerca all'indice di distribution (OpenSearch)."""
    intestazioni = {k: v for k, v in request.headers.items() if k.lower() in _INOLTRATE}

    async def cerca(corpo: dict) -> dict:
        from app.services.archivio import CursoreNonValido

        try:
            async with httpx.AsyncClient(timeout=_T, transport=TRASPORTO_DISTRIBUTION) as c:
                r = await c.post(f"{settings.distribution_url}/api/v2/distribution/search",
                                 json=corpo, headers=intestazioni)
        except httpx.HTTPError as exc:
            raise errore_servizio_irraggiungibile("distribution", exc) from None
        if r.status_code >= 400:
            try:
                codice = r.json().get("codice")
            except (ValueError, AttributeError):
                codice = None
            if codice == "DST-104":
                raise CursoreNonValido("cursore dell'indice rifiutato")
            if r.status_code == 429:
                attesa = r.headers.get("Retry-After", "60")
                raise ErroreApi("QTA-001", "Limite di ricerche al minuto del piano",
                                headers={"Retry-After": attesa},
                                retry_after=int(attesa) if attesa.isdigit() else None)
            raise errore_da_servizio_a_valle("distribution", r)
        try:
            dati = r.json()
        except ValueError as exc:
            raise errore_interno(502, "DSH-502", "Risposta non leggibile dal servizio "
                                 "distribution", exc, servizio="distribution") from None
        if not isinstance(dati, dict):
            raise errore_interno(502, "DSH-502", "Risposta non leggibile dal servizio "
                                 "distribution", None, servizio="distribution")
        return dati

    return cerca


@router.post("/search")
async def search(
    req: SearchReq,
    request: Request,
    user: dict = Depends(limite_ricerca),
) -> dict:
    """Ricerca dei PdA dell'ente, a cursore (R03 f2, REV-06): risposta
    ``{items, limit, cursore, altri, motore, filtri_applicati, campi_testo}``,
    senza totale. Con testo o classe documentale la ricerca va sull'indice
    OpenSearch (distribution, con le credenziali del chiamante: ``motore`` =
    «indice»); i risultati si rileggono dal database (stato attuale, solo
    dell'ente). Con i soli stato e anno va sul database (``motore`` =
    «database»). Anche con chiave API. ``skip`` > 0 → 422 DSH-422; cursore di
    un'altra ricerca → 422 DSH-104."""
    _rifiuta_skip(req.skip)
    tid = _tenant_lettura(user, req.tenant_id)
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.archivio import CursoreNonValido, cerca_aip
    from libs.shared.models import AipStato

    stato = _enum_o_400(AipStato, req.stato, "Stato del pacchetto")
    try:
        return await cerca_aip(
            tid, testo=req.query, classe=req.classe_documentale,
            anno=int(req.anno) if req.anno else None, stato=stato,
            cursore=req.cursore, limit=req.limit,
            ricerca_indice=_ricerca_su_distribution(request),
        )
    except CursoreNonValido:
        raise HTTPException(422, {"type": "DSH-104", "title": "Cursore non valido"}) from None
    except SQLAlchemyError as exc:
        raise errore_db("ricerca dei pacchetti", exc, codice="DSH-503") from None


@router.post("/distribution")
async def distribution(
    body: dict,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    # HIGH-002 FIX: proxy verso il servizio distribution con il token dell'utente.
    # G2: il canale «WEB» distingue nello storico le richieste fatte dal portale;
    # il richiedente lo ricava distribution dal token (il valore del client è ignorato).
    return await _post(
        f"{settings.distribution_url}/api/v2/distribution/richiedi", body,
        authorization=authorization, servizio="distribution",
        intestazioni={"X-VerumDoc-Canale": "WEB"},
    )


@router.get("/tenants")
async def lista_tenants(
    stato: str | None = Query(None),
    skip: int = Query(0),
    limit: int = Query(20),
    authorization: str | None = Header(default=None),
) -> dict:
    _require_super_admin(authorization)
    params: dict = {"skip": skip, "limit": limit}
    if stato:
        params["stato"] = stato
    return await _get(
        f"{settings.onboarding_url}/api/v1/onboarding/tenant", params,
        authorization=authorization,
    )


@router.post("/tenants", status_code=201)
async def crea_tenant(
    body: dict,
    authorization: str | None = Header(default=None),
) -> dict:
    user = _require_super_admin(authorization)
    result = await _post(
        f"{settings.onboarding_url}/api/v1/onboarding/tenant", body,
        authorization=authorization,
    )
    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit
        await log_audit(
            evento="tenant.creato_via_dashboard",
            tenant_id=result.get("id"),
            user_id=user.get("user_id"),
            dettagli={"ragione_sociale": body.get("ragione_sociale", "")},
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)
    return result


@router.post("/tenants/{tenant_id}/attiva")
async def attiva_tenant(
    tenant_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    user = _require_super_admin(authorization)
    result = await _chiama(
        "POST", f"{settings.onboarding_url}/api/v1/onboarding/tenant/{tenant_id}/attiva",
        servizio="onboarding", authorization=authorization,
    )
    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit
        await log_audit(
            evento="tenant.attivato_via_dashboard",
            tenant_id=tenant_id,
            user_id=user.get("user_id"),
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)
    return result


@router.get("/classi-documentali", summary="Lista classi documentali")
async def lista_classi_documentali(
    attiva: bool | None = Query(default=None),
    skip: int = Query(default=0),
    limit: int = Query(default=50),
    authorization: str | None = Header(default=None),
) -> dict:
    """CONFLICT FIX: auth + forward header a onboarding."""
    _require_auth(authorization)
    params: dict = {"skip": skip, "limit": limit}
    if attiva is not None:
        params["attiva"] = str(attiva).lower()
    return await _get(
        f"{settings.onboarding_url}/api/v1/onboarding/classi-documentali",
        params,
        authorization=authorization,
    )


@router.post(
    "/classi-documentali",
    status_code=201,
    summary="Crea classe documentale",
)
async def crea_classe_documentale(
    body: dict,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    return await _post(
        f"{settings.onboarding_url}/api/v1/onboarding/classi-documentali", body,
        authorization=authorization, servizio="onboarding",
    )


@router.put(
    "/classi-documentali/{classe_id}",
    summary="Aggiorna classe documentale",
)
async def aggiorna_classe_documentale(
    classe_id: str,
    body: dict,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    return await _chiama(
        "PUT", f"{settings.onboarding_url}/api/v1/onboarding/classi-documentali/{classe_id}",
        servizio="onboarding", body=body, authorization=authorization,
    )


@router.get("/notifiche", summary="Storico notifiche del tenant")
async def lista_notifiche(
    tenant_id: str = Query(...),
    cursore: str | None = Query(None, max_length=300,
                                description="Cursore restituito dalla pagina precedente"),
    limit: int = Query(20, ge=1, le=100),
    skip: int | None = Query(None, ge=0, deprecated=True,
                             description="Non più supportato (R04): usare `cursore`"),
    authorization: str | None = Header(default=None),
) -> dict:
    """Notifiche (e registrazioni di distribuzione) dell'ente dalla più recente,
    con stato, numero di tentativi ed errore dell'ultimo tentativo.
    R04 (VD-C-102): a cursore ``(created_at, id)`` sull'indice
    ``idx_notifications_tenant_cursore``, niente OFFSET né ``COUNT(*)``; risposta
    ``{items, limit, cursore, altri}``. ``skip`` > 0 → 422 DSH-422. Errore del
    database → 503 con id di correlazione (mai 200 con «error», mai elenco vuoto)."""
    _rifiuta_skip(skip)
    user = _require_auth(authorization)
    tid = str(_tenant_lettura(user, tenant_id))

    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.services import cursori
    from libs.shared.db import get_async_session_lettura

    params: dict = {"tid": tid, "n": limit + 1}
    dopo = ""
    if cursore:
        try:
            params["dopo_t"], params["dopo_i"] = cursori.decodifica("notifiche", cursore)
        except cursori.CursoreNonValido:
            raise HTTPException(422, {"type": "DSH-104", "title": "Cursore non valido"}) from None
        dopo = "AND (created_at, id) < (:dopo_t, CAST(:dopo_i AS uuid)) "
    try:
        async with get_async_session_lettura() as session:
            rows = (await session.execute(
                text(
                    "SELECT id, tipo, canale, destinatario, stato, created_at, "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
                    "payload_json, tentativi, errore, inviato_at, prossimo_tentativo, "
                    "aip_id::text AS aip_id, updated_at "
                    "FROM public.notifications "
                    f"WHERE tenant_id = CAST(:tid AS uuid) {dopo}"
                    "ORDER BY created_at DESC, id DESC LIMIT :n"
                ),
                params,
            )).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_db("lettura notifiche", exc, codice="DSH-503") from None

    righe, prossimo, altri = cursori.pagina(list(rows), limit, "notifiche")

    def _data(v):
        return v.isoformat() if v else None

    items = []
    for r in righe:
        payload = r["payload_json"] if isinstance(r["payload_json"], dict) else {}
        items.append({
            "id": str(r["id"]), "tipo": r["tipo"], "canale": r["canale"],
            "destinatario": r["destinatario"], "stato": r["stato"],
            "data": _data(r["created_at"]),
            "payload": r["payload_json"],
            "oggetto": payload.get("oggetto") or payload.get("motivo"),
            "tentativi": r["tentativi"],
            "errore": r["errore"],
            "inviato_at": _data(r["inviato_at"]),
            "prossimo_tentativo": _data(r["prossimo_tentativo"]),
            "aip_id": r["aip_id"],
            "aggiornato_at": _data(r["updated_at"]),
        })
    return {"items": items, "limit": limit, "cursore": prossimo, "altri": altri}


@router.get(
    "/manuale-conservazione",
    summary="Genera il Manuale di conservazione dai dati reali (bozza non firmata)",
)
async def genera_manuale_conservazione(
    authorization: str | None = Header(default=None),
) -> dict:
    """
    VD-C-013: Manuale di conservazione generato da configurazione (CONSERVATORE_*)
    e dal DB (classi documentali). Ogni sezione ha `stato_sezione`
    (generata / da_compilare / da_realizzare / parziale / non_disponibile);
    `meta` riporta versione, stato del documento e stato di firma.
    Accessibile solo a RSC_CONSERVATORE e SUPER_ADMIN.
    """
    _require_role(authorization, "SUPER_ADMIN", "RSC_CONSERVATORE")
    from app.services.manuale import DatiConservatore, genera_manuale, leggi_classi_attive
    classi, errore = await leggi_classi_attive()
    return genera_manuale(DatiConservatore(), adesso=datetime.now(UTC),
                          classi=classi, errore_classi=errore)


# ── Sprint 3A FIX-021: Audit Log endpoint ───────────────────────────

@router.get("/audit", summary="Storico audit trail — SUPER_ADMIN, RSC, ADMIN_TENANT")
async def lista_audit(
    tenant_id: str | None = Query(None),
    evento: str | None = Query(None, max_length=100),
    cursore: str | None = Query(None, max_length=300,
                                description="Cursore restituito dalla pagina precedente"),
    limit: int = Query(50, ge=1, le=200),
    skip: int | None = Query(None, ge=0, deprecated=True,
                             description="Non più supportato (R04): usare `cursore`"),
    authorization: str | None = Header(default=None),
) -> dict:
    """
    Sprint 3A FIX-021: registro delle operazioni (audit_global, append-only).
    SUPER_ADMIN e RSC_CONSERVATORE vedono tutto (o l'ente indicato);
    ADMIN_TENANT solo il proprio ente. Revisione di sicurezza B11: solo i ruoli
    di governo (OPERATORE, VIEWER, API_SYSTEM → 403). G2: parametri con CAST esplicito (prima il
    confronto uuid = testo falliva sempre), ``evento`` cercato come testo
    letterale; errore del database → 503 con id di correlazione, mai 200 con
    il testo dell'eccezione SQL.
    R04 (VD-C-102): dalla più recente a cursore ``(created_at, id)`` sugli
    indici ``idx_audit_global_cursore`` e ``idx_audit_global_tenant_cursore``,
    niente OFFSET né ``COUNT(*)``: risposta ``{items, limit, cursore, altri}``;
    ``skip`` > 0 → 422 DSH-422.
    """
    _rifiuta_skip(skip)
    user = _require_admin(authorization)   # SUPER_ADMIN, ADMIN_TENANT, RSC_CONSERVATORE
    ruolo = user.get("ruolo", "")

    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.services import cursori
    from libs.shared.db import get_async_session_lettura

    conditions = []
    params: dict = {"n": limit + 1}
    if ruolo not in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
        # Gli altri profili vedono solo il proprio ente (tenant_id della query ignorato)
        params["tid"] = _uuid_o_errore(
            user.get("tenant_id"), 403, "Il token non indica un cliente: accesso negato",
        )
        conditions.append("tenant_id = CAST(:tid AS uuid)")
    elif tenant_id:
        params["tid"] = _uuid_o_errore(tenant_id, 400, "tenant_id non valido")
        conditions.append("tenant_id = CAST(:tid AS uuid)")
    if evento:
        sicuro = evento.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["evento_pattern"] = f"%{sicuro}%"
        conditions.append("evento LIKE :evento_pattern ESCAPE '\\'")
    if cursore:
        try:
            params["dopo_t"], params["dopo_i"] = cursori.decodifica("audit", cursore)
        except cursori.CursoreNonValido:
            raise HTTPException(422, {"type": "DSH-104", "title": "Cursore non valido"}) from None
        conditions.append("(created_at, id) < (:dopo_t, CAST(:dopo_i AS uuid))")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    try:
        async with get_async_session_lettura() as session:
            rows = (await session.execute(
                text(
                    "SELECT id, tenant_id::text AS tenant_id, user_id::text AS user_id, "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
                    "aip_id::text AS aip_id, evento, dettagli, ip_address, created_at "
                    f"FROM public.audit_global {where} "
                    "ORDER BY created_at DESC, id DESC LIMIT :n"
                ),
                params,
            )).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_db("lettura del registro delle operazioni", exc, codice="DSH-503") from None

    righe, prossimo, altri = cursori.pagina(list(rows), limit, "audit")
    return {
        "items": [
            {
                "id": str(r["id"]),
                "tenant_id": r["tenant_id"],
                "user_id": r["user_id"],
                "aip_id": r["aip_id"],
                "evento": r["evento"],
                "dettagli": r["dettagli"],
                "ip_address": str(r["ip_address"]) if r["ip_address"] else None,
                "data": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in righe
        ],
        "limit": limit,
        "cursore": prossimo,
        "altri": altri,
    }


# ── Sprint 4A FIX-031: Piano di Cessazione ───────────────────────────

@router.get(
    "/piano-cessazione",
    summary="Piano di Cessazione — Allegato B Det. 455/2021",
)
async def piano_cessazione(
    authorization: str | None = Header(default=None),
) -> dict:
    """
    VD-C-013: Piano di cessazione (Allegato B) generato da configurazione
    (CONSERVATORE_*) e dagli elenchi di consistenza per produttore letti dal DB.
    Sezioni con `stato_sezione`; `meta` con versione, stato e stato di firma.
    Accessibile solo a RSC_CONSERVATORE e SUPER_ADMIN.
    """
    _require_role(authorization, "SUPER_ADMIN", "RSC_CONSERVATORE")
    from app.services.manuale import DatiConservatore
    from app.services.piano_cessazione import genera_piano_cessazione, leggi_consistenza
    consistenza, errore = await leggi_consistenza()
    return genera_piano_cessazione(DatiConservatore(), adesso=datetime.now(UTC),
                                   consistenza=consistenza, errore_consistenza=errore)


# ── Login con API key: rimosso (g3-backend) ─────────────────────────
# Le chiavi API le genera onboarding e le verifica SOLO il gateway
# (libs/shared/security/chiavi_api.py, HMAC con API_KEY_PEPPER): il vecchio
# POST /api/dashboard/auth/api-key verificava bcrypt su un formato che nessuna
# chiave ha più e firmava token propri. Nessun uso nel frontend.


# ── Contratti e nomine del responsabile della conservazione ─────────
# VD-C-025 (R03): le letture usano le colonne reali di public.contracts e
# public.rsc_nominations (prima: colonne inesistenti, errore nascosto e lista
# «vuota»); un errore del database è un 503 esplicito, mai un elenco vuoto.
# Creazione: POST con validazione Pydantic, solo SUPER_ADMIN (nessun
# allargamento dei permessi esistenti, REGOLE §14.1), registro di audit.

_CF_PERSONA_FISICA = (r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z]"
                      r"[0-9LMNPQRSTUV]{3}[A-Z]$")
_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _uuid_o_errore(valore: str | None, stato: int, titolo: str) -> str:
    try:
        return str(uuid.UUID(str(valore or "")))
    except ValueError:
        raise HTTPException(stato, {"type": "GOV-UUID", "title": titolo}) from None


def _tenant_richiesto(user: dict, tenant_id: str | None) -> str:
    """Cliente su cui opera la richiesta: SUPER_ADMIN e RSC_CONSERVATORE
    possono indicarne uno qualsiasi, gli altri solo il proprio."""
    ruolo = user.get("ruolo", "")
    if tenant_id and ruolo in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
        return _uuid_o_errore(tenant_id, 400, "tenant_id non valido")
    return _uuid_o_errore(
        user.get("tenant_id"), 403, "Il token non indica un cliente: accesso negato",
    )


def _errore_db(operazione: str, exc: Exception) -> HTTPException:
    """503 con id di correlazione (app.errori): mai lista vuota, mai testo SQL."""
    return errore_db(operazione, exc, codice="GOV-503")


def _oggi_roma() -> date:
    return datetime.now(ZoneInfo("Europe/Rome")).date()


def _stato_contratto(attivo: bool, data_scadenza: date | None, oggi: date) -> str:
    if not attivo:
        return "REVOCATO"
    if data_scadenza is not None and data_scadenza < oggi:
        return "SCADUTO"
    return "ATTIVO"


class ContrattoCreate(BaseModel):
    """Contratto di servizio fra il conservatore e un cliente."""
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID = Field(..., description="Cliente (ente) titolare del contratto")
    numero_contratto: str = Field(
        ..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ./_-]*$",
        description="Numero del contratto, unico nel sistema",
    )
    data_stipula: date = Field(..., description="Data di stipula (decorrenza)")
    data_scadenza: date | None = Field(
        None, description="Data di scadenza, successiva alla stipula")
    piano: str = Field(..., min_length=1, max_length=50, description="Piano o tipo di servizio")
    valore_annuo_eur: float | None = Field(None, ge=0, description="Valore annuo in euro")

    @model_validator(mode="after")
    def _date_coerenti(self) -> ContrattoCreate:
        if self.data_scadenza is not None and self.data_scadenza <= self.data_stipula:
            raise ValueError("data_scadenza deve essere successiva a data_stipula")
        return self


class NominaCreate(BaseModel):
    """Nomina del responsabile della conservazione di un cliente (LG AgID, ruoli)."""
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID = Field(..., description="Cliente che nomina il responsabile")
    nome_rsc: str = Field(..., min_length=2, max_length=300, description="Nome e cognome")
    codice_fiscale_rsc: str = Field(
        ..., pattern=_CF_PERSONA_FISICA,
        description="Codice fiscale della persona fisica (16 caratteri, maiuscole)",
    )
    email_rsc: str = Field(..., max_length=255, pattern=_EMAIL)
    pec_rsc: str | None = Field(None, max_length=255, pattern=_EMAIL)
    data_nomina: date
    data_scadenza: date | None = None
    documento_s3_key: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _date_coerenti(self) -> NominaCreate:
        if self.data_scadenza is not None and self.data_scadenza <= self.data_nomina:
            raise ValueError("data_scadenza deve essere successiva a data_nomina")
        return self


async def _verifica_tenant_esiste(session, tenant_id: str) -> None:
    from sqlalchemy import text
    esiste = (await session.execute(
        text("SELECT 1 FROM public.tenants WHERE id = CAST(:tid AS uuid)"),
        {"tid": tenant_id},
    )).first()
    if not esiste:
        raise HTTPException(404, {"type": "GOV-404", "title": "Cliente non trovato"})


@router.get("/contratti", summary="Lista contratti di servizio del cliente")
async def lista_contratti(
    tenant_id: str | None = Query(None),
    authorization: str | None = Header(default=None),
) -> dict:
    """Contratti del cliente indicato (o del cliente del token)."""
    user = _require_auth(authorization)
    tid = _tenant_richiesto(user, tenant_id)

    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import get_async_session_lettura

    try:
        async with get_async_session_lettura() as session:
            # Join a 2 tabelle per la ragione sociale del cliente
            rows = (await session.execute(
                text(
                    "SELECT c.id::text, c.tenant_id::text, t.ragione_sociale, "
                    "c.numero_contratto, c.data_stipula, c.data_scadenza, "
                    "c.attivo, c.piano, c.valore_annuo_eur, c.created_at "
                    "FROM public.contracts c "
                    "JOIN public.tenants t ON t.id = c.tenant_id "
                    "WHERE c.tenant_id = CAST(:tid AS uuid) "
                    "ORDER BY c.data_stipula DESC, c.created_at DESC"
                ),
                {"tid": tid},
            )).fetchall()
    except SQLAlchemyError as exc:
        raise _errore_db("lettura contratti", exc) from exc

    oggi = _oggi_roma()
    return {
        "total": len(rows),
        "items": [
            {
                "id": r[0], "tenant_id": r[1],
                "ragione_sociale": r[2],
                "numero_contratto": r[3],
                "data_inizio": r[4].isoformat() if r[4] else None,
                "data_fine": r[5].isoformat() if r[5] else None,
                "stato": _stato_contratto(bool(r[6]), r[5], oggi),
                "tipo_servizio": r[7],
                "valore_annuo_eur": r[8],
                "created_at": r[9].isoformat() if r[9] else None,
            }
            for r in rows
        ],
    }


@router.post("/contratti", status_code=201, summary="Registra un contratto di servizio")
async def crea_contratto(
    body: ContrattoCreate,
    authorization: str | None = Header(default=None),
) -> dict:
    user = _require_super_admin(authorization)

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError

    from libs.shared.db import get_async_session
    from libs.shared.security.audit import log_audit

    tid = str(body.tenant_id)
    nuovo_id = str(uuid.uuid4())
    try:
        async with get_async_session() as session:
            await _verifica_tenant_esiste(session, tid)
            await session.execute(
                text(
                    "INSERT INTO public.contracts "
                    "(id, tenant_id, numero_contratto, data_stipula, data_scadenza, "
                    " piano, valore_annuo_eur, attivo) "
                    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :numero, :stipula, "
                    " :scadenza, :piano, :valore, true)"
                ),
                {
                    "id": nuovo_id, "tid": tid, "numero": body.numero_contratto,
                    "stipula": body.data_stipula, "scadenza": body.data_scadenza,
                    "piano": body.piano, "valore": body.valore_annuo_eur,
                },
            )
            await session.commit()
    except IntegrityError:
        raise HTTPException(
            409, {"type": "GOV-409", "title": "Numero di contratto già registrato"},
        ) from None
    except SQLAlchemyError as exc:
        raise _errore_db("registrazione contratto", exc) from exc

    await log_audit(
        evento="contratto.registrato", tenant_id=tid, user_id=user.get("user_id"),
        dettagli={"contratto_id": nuovo_id, "numero_contratto": body.numero_contratto},
    )
    return {
        "id": nuovo_id, "tenant_id": tid, "numero_contratto": body.numero_contratto,
        "data_inizio": body.data_stipula.isoformat(),
        "data_fine": body.data_scadenza.isoformat() if body.data_scadenza else None,
        "stato": _stato_contratto(True, body.data_scadenza, _oggi_roma()),
        "tipo_servizio": body.piano, "valore_annuo_eur": body.valore_annuo_eur,
    }


def _nomina_da_riga(r) -> dict:
    return {
        "id": r[0], "tenant_id": r[1],
        # Il nome è memorizzato per intero in nome_rsc: «cognome» resta vuoto
        "nome": r[2], "cognome": None,
        "codice_fiscale": r[3], "email": r[4], "pec": r[5],
        "data_nomina": r[6].isoformat() if r[6] else None,
        "data_scadenza": r[7].isoformat() if r[7] else None,
        "data_revoca": r[8].isoformat() if r[8] else None,
        "attiva": bool(r[9]),
        "documento_disponibile": bool(r[10]),
    }


@router.get("/nomine-rsc", summary="Nomine del responsabile della conservazione")
async def nomine_rsc(
    tenant_id: str | None = Query(None),
    authorization: str | None = Header(default=None),
) -> dict:
    """Nomina in vigore e storico delle nomine del cliente indicato
    (o del cliente del token)."""
    user = _require_role(authorization, "SUPER_ADMIN", "RSC_CONSERVATORE")
    tid = _tenant_richiesto(user, tenant_id)

    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import get_async_session_lettura

    try:
        async with get_async_session_lettura() as session:
            rows = (await session.execute(
                text(
                    "SELECT id::text, tenant_id::text, nome_rsc, codice_fiscale_rsc, "
                    "email_rsc, pec_rsc, data_nomina, data_scadenza, data_revoca, "
                    "attiva, documento_s3_key "
                    "FROM public.rsc_nominations "
                    "WHERE tenant_id = CAST(:tid AS uuid) "
                    "ORDER BY attiva DESC, data_nomina DESC, created_at DESC"
                ),
                {"tid": tid},
            )).fetchall()
    except SQLAlchemyError as exc:
        raise _errore_db("lettura nomine", exc) from exc

    voci = [_nomina_da_riga(r) for r in rows]
    corrente = next((v for v in voci if v["attiva"]), None)
    return {
        "tenant_id": tid,
        "nomina_corrente": corrente,
        "storico": [v for v in voci if v is not corrente],
    }


@router.post("/nomine-rsc", status_code=201,
             summary="Registra la nomina del responsabile della conservazione")
async def crea_nomina_rsc(
    body: NominaCreate,
    authorization: str | None = Header(default=None),
) -> dict:
    """Registra una nuova nomina. La nomina attiva precedente dello stesso
    cliente viene revocata con data_revoca = data_nomina della nuova
    (una sola nomina attiva per cliente; nessuna cancellazione, REGOLE §10.5)."""
    user = _require_super_admin(authorization)

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError

    from libs.shared.db import get_async_session
    from libs.shared.security.audit import log_audit

    tid = str(body.tenant_id)
    nuovo_id = str(uuid.uuid4())
    try:
        async with get_async_session() as session:
            await _verifica_tenant_esiste(session, tid)
            revocate = (await session.execute(
                text(
                    "UPDATE public.rsc_nominations "
                    "SET attiva = false, data_revoca = :data_revoca "
                    "WHERE tenant_id = CAST(:tid AS uuid) AND attiva "
                    "RETURNING id::text"
                ),
                {"tid": tid, "data_revoca": body.data_nomina},
            )).fetchall()
            await session.execute(
                text(
                    "INSERT INTO public.rsc_nominations "
                    "(id, tenant_id, nome_rsc, codice_fiscale_rsc, email_rsc, pec_rsc, "
                    " data_nomina, data_scadenza, documento_s3_key, attiva) "
                    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :nome, :cf, :email, "
                    " :pec, :data_nomina, :data_scadenza, :doc, true)"
                ),
                {
                    "id": nuovo_id, "tid": tid, "nome": body.nome_rsc,
                    "cf": body.codice_fiscale_rsc, "email": body.email_rsc,
                    "pec": body.pec_rsc, "data_nomina": body.data_nomina,
                    "data_scadenza": body.data_scadenza, "doc": body.documento_s3_key,
                },
            )
            await session.commit()
    except IntegrityError:
        # Due registrazioni concorrenti per lo stesso cliente: vince la prima
        raise HTTPException(
            409, {"type": "GOV-409",
                  "title": "Nomina registrata nel frattempo da un'altra richiesta: ricaricare"},
        ) from None
    except SQLAlchemyError as exc:
        raise _errore_db("registrazione nomina", exc) from exc

    await log_audit(
        evento="nomina_rsc.registrata", tenant_id=tid, user_id=user.get("user_id"),
        dettagli={"nomina_id": nuovo_id, "revocate": [r[0] for r in revocate]},
    )
    return {
        "id": nuovo_id, "tenant_id": tid, "attiva": True,
        "data_nomina": body.data_nomina.isoformat(),
        "revocate": [r[0] for r in revocate],
    }
