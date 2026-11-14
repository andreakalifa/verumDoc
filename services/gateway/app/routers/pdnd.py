# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
PDND (Piattaforma Digitale Nazionale Dati, Interoperabilità): tre e-service per le PA.

Servizio: gateway. Riferimenti: piano R03 §3.3; criticità VD-C-021 (R02),
VD-C-092 (R03); REGOLE_SVILUPPO §9.1, §12.1, §14.3, §14.4, §18, §19.1.

Voucher (RFC 9068, invariato da R02):
  - firma RS256 con la chiave PDND configurata (``PDND_PUBLIC_KEY``), claim
    exp/iat/aud/iss obbligatori; con la chiave serve anche audience ed
    emittente reali, altrimenti le sole rotte PDND rispondono 503 PDND-503;
  - ``client_id`` del voucher → cliente tramite ``public.pdnd_fruitori``;
    fruitore non registrato o cliente sospeso: 403 (fail-closed). R04 (VD-C-084):
    i fruitori si registrano da API (``/api/dashboard/pdnd/fruitori``, SUPER_ADMIN);
  - R04 (VD-C-084) ``jti`` monouso: con ``PDND_VOUCHER_MONOUSO`` (predefinito
    attivo) ogni voucher vale per UNA richiesta: ``SET NX`` su Redis della chiave
    ``pdnd:jti:<sha256(iss|jti)>`` con scadenza alla ``exp`` del voucher (più la
    tolleranza). Voucher ripresentato o senza ``jti`` → 401 PDND-401-RIUSATO;
    Redis non raggiungibile → 503 PDND-503-REPLAY (fail-closed: un replay non
    verificato non si accetta). Decisione da confermare col committente: le
    Linee guida PDND ammettono il riuso del voucher fino alla scadenza; un
    fruitore che lo riusa deve chiederne uno per richiesta, oppure il
    conservatore disattiva il controllo (rapporto R04 servizi).

Errori (R03, VD-C-100): ``application/problem+json`` del catalogo (famiglia
PDND per voucher e fruitore, VRS/UPL/RGL per il versamento). È la forma che
le Linee guida AgID sull'interoperabilità tecnica (ModI) raccomandano per le
API REST (RFC 7807, oggi RFC 9457). Il voucher è un token Bearer (RFC 6750):
sulle risposte 401 c'è l'intestazione ``WWW-Authenticate: Bearer
error="invalid_token"`` prevista da RFC 6750 §3; il corpo in stile OAuth
(``{"error": ...}``) è proprio dell'endpoint che EMETTE i token (RFC 6749
§5.2, cioè PDND), non dell'e-service che li riceve: non serve mantenerlo. I
vecchi codici-stringa restano alias nel catalogo.

E-service 1 — versamento (R03, VD-C-092): niente più chiamata HTTP interna
con un token API_SYSTEM. Il router traduce la richiesta in ``IndiceUD`` +
``FonteFlusso`` e chiama il caso d'uso unico ``versa``:
  - identificativo del documento e chiave UD (registro facoltativo, anno,
    numero) vengono dalla richiesta del fruitore: nulla dipende dall'orario;
  - ``Idempotency-Key`` con la stessa regola del REST (obbligatoria con la
    transizione VD-P-27: fino al 31/03/2027 accettata senza, con avviso
    VRS-126; poi 428 VRS-125): un ritentativo non duplica il PdV;
  - formato rilevato dal contenuto (il MIME dichiarato è facoltativo e solo
    confrontato): p7m e XML non sono più forzati a PDF;
  - il file resta in base64 nel JSON (vincolo dell'e-service): R03 f1 (REV-05)
    il corpo si legge in streaming, il base64 si valida e si decodifica a
    blocchi in un file temporaneo mentre arriva (limite ``PDND_MAX_FILE_MB``,
    PDND-413, ci si ferma subito) e da lì va allo staging: nessuna copia
    dell'intero file in memoria; richieste contemporanee per replica limitate
    (``PDND_RICHIESTE_CONTEMPORANEE_MAX``, 503 PDND-503-OCCUPATO ritentabile);
  - limite di richieste al minuto del piano (QTA-001) comune a tutti i canali
    del gateway (R03 f1, REV-03).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from app.auth.dependencies import get_redis
from app.auth.rate_limiter import IMPOSTAZIONI_QUOTE, check_rate_limit
from app.config import login_sviluppo_consentito, settings
from app.models.versamento import IndiceUD, VersamentoResponse
from app.routers.versamento import risposta_versamento
from app.services.corpo import (
    BUDGET_DISCO_TEMPORANEO,
    LimiteConcorrenza,
    byte_da_prenotare,
    leggi_json_con_file,
)
from app.services.versamento_service import (
    MB,
    Canale,
    FonteFlusso,
    IdentitaVersante,
    RichiestaVersamento,
    versa,
)
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_conservatore, imposta_ambito_identita
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi
from libs.shared.security.quote import chiave_richieste, limiti_ente

logger = structlog.get_logger(__name__)

#: Intestazione delle risposte 401 sul voucher (RFC 6750 §3).
_WWW_AUTHENTICATE = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


async def _pdnd_configurato() -> None:
    # Rinvio a richiedi_pdnd_configurato (definita più sotto, con le impostazioni).
    await richiedi_pdnd_configurato()


#: Tutte le rotte PDND: 503 PDND-503 se la configurazione è incompleta.
router = APIRouter(prefix="/api/pdnd/v1", tags=["PDND"],
                   dependencies=[Depends(_pdnd_configurato)])

#: Stati del cliente in cui il fruitore PDND non può operare.
_STATI_TENANT_BLOCCATI = frozenset({"SOSPESO", "DISATTIVATO"})


class PdndSettings(BaseSettings):
    """Configurazione PDND (variabili d'ambiente con prefisso ``PDND_``).

    public_key: chiave pubblica PEM con cui PDND firma i voucher (RS256).
    audience:   audience attesa nel voucher (quella dell'e-service registrato).
    issuer:     emittente atteso (es. ``interop.pagopa.it``; in collaudo
                ``uat.interop.pagopa.it``).
    Con la chiave configurata audience ed emittente sono OBBLIGATORI (revisione
    di sicurezza M1): senza, il gateway parte ma le rotte PDND rispondono 503
    PDND-503 «PDND non configurato» e un voucher non si accetta mai.
    """
    model_config = SettingsConfigDict(env_prefix="PDND_", case_sensitive=False, extra="ignore")

    public_key: str = ""
    audience: str = ""
    issuer: str = ""
    #: R04 (VD-C-084): ogni voucher (jti) vale per una sola richiesta
    voucher_monouso: bool = True
    #: Tolleranza aggiunta alla scadenza del voucher per la memoria dei jti (secondi)
    tolleranza_secondi: int = 30

    def audience_e_issuer_configurati(self) -> bool:
        return _valore_reale(self.audience) and _valore_reale(self.issuer)


def _valore_reale(valore: str) -> bool:
    """Vuoto o segnaposto dei values («DA_IMPOSTARE:...») non valgono come configurati."""
    testo = (valore or "").strip()
    return bool(testo) and not testo.upper().startswith("DA_IMPOSTARE")


def pdnd_settings() -> PdndSettings:
    """Letta a ogni chiamata: una rotazione della chiave non richiede codice."""
    cfg = PdndSettings()
    if cfg.public_key and "\\n" in cfg.public_key and "\n" not in cfg.public_key:
        cfg = cfg.model_copy(update={"public_key": cfg.public_key.replace("\\n", "\n")})
    return cfg


def pdnd_non_configurato(dettaglio: str | None = None) -> ErroreApi:
    """Errore uniforme delle rotte PDND quando la configurazione è incompleta."""
    return ErroreApi("PDND-503", dettaglio or "Le API PDND non sono disponibili: "
                                              "configurazione incompleta.")


def problema_configurazione_pdnd(cfg: PdndSettings | None = None) -> str | None:
    """Motivo per cui PDND non è utilizzabile, o None se la configurazione è
    completa oppure PDND è spento (nessuna chiave: si gestisce a parte)."""
    cfg = cfg or pdnd_settings()
    if cfg.public_key and not cfg.audience_e_issuer_configurati():
        mancanti = [nome for nome, valore in (("PDND_AUDIENCE", cfg.audience),
                                              ("PDND_ISSUER", cfg.issuer))
                    if not _valore_reale(valore)]
        return (
            f"PDND_PUBLIC_KEY è configurata ma {' e '.join(mancanti)} mancano (o sono "
            "segnaposto): i voucher PDND non sarebbero verificati per intero. Le rotte "
            "/api/pdnd/v1 rispondono 503 PDND-503 finché non si impostano (o si toglie "
            "la chiave); il resto del gateway, versamento compreso, funziona."
        )
    return None


def verifica_configurazione_pdnd(env: str, cfg: PdndSettings | None = None) -> str | None:
    """All'avvio, fuori da ENV=test: con la chiave PDND configurata servono anche
    audience ed emittente reali. Se mancano NON si ferma il gateway (PDND non
    serve al versamento): errore chiaro nel log e le sole rotte PDND rispondono
    503 PDND-503 (fail-closed limitato a PDND). Restituisce il motivo o None."""
    if (env or "").strip().lower() == "test":
        return None
    problema = problema_configurazione_pdnd(cfg)
    if problema:
        logger.error("pdnd_non_configurato", errore=problema)
    return problema


async def richiedi_pdnd_configurato() -> None:
    """Dipendenza del router: configurazione incompleta → 503 PDND-503, prima
    di leggere voucher o corpo. Letta a ogni chiamata (si corregge con un
    riavvio della sola configurazione, senza codice)."""
    if problema_configurazione_pdnd() is not None:
        raise pdnd_non_configurato()


def _non_autorizzato(dettaglio: str = "Voucher PDND non valido") -> ErroreApi:
    """401 uniforme: il motivo tecnico resta nei log (revisione M1/B2)."""
    return ErroreApi("PDND-401", dettaglio, headers=dict(_WWW_AUTHENTICATE))


def _verifica_con_chiave_pdnd(token: str, cfg: PdndSettings) -> dict:
    """Voucher PDND con PyJWT (come il resto del sistema, REGOLE §19.1): firma
    RS256, claim exp/iat/aud/iss obbligatori e verificati."""
    import jwt as pyjwt

    if not cfg.audience_e_issuer_configurati():
        logger.error("pdnd_configurazione_incompleta")
        raise pdnd_non_configurato()
    try:
        return pyjwt.decode(
            token,
            cfg.public_key,
            algorithms=["RS256"],
            audience=cfg.audience,
            issuer=cfg.issuer,
            options={"require": ["exp", "iat", "aud", "iss"], "verify_signature": True},
            leeway=30,
        )
    except pyjwt.ExpiredSignatureError:
        logger.info("pdnd_voucher_rifiutato", motivo="scaduto")
        raise ErroreApi("PDND-401-SCADUTO", "Voucher PDND scaduto",
                        headers=dict(_WWW_AUTHENTICATE)) from None
    except pyjwt.PyJWTError as exc:
        logger.info("pdnd_voucher_rifiutato", motivo=type(exc).__name__)
        raise _non_autorizzato() from None


# Controllo all'avvio (import del router da app.main): fuori dai test, chiave
# senza audience/emittente scrive un errore nel log; il gateway parte e solo
# le rotte PDND rispondono 503 (revisione R3).
verifica_configurazione_pdnd(settings.env)


def _validate_pdnd_voucher(authorization: str | None) -> dict:
    """
    Valida il voucher JWT PDND (RFC 9068), sempre con verifica della firma.

    - Con ``PDND_PUBLIC_KEY`` configurata (qualsiasi ambiente): firma RS256,
      scadenza, audience ed emittente.
    - Senza chiave fuori da development/test: 503 esplicito (non configurato).
    - In development/test senza chiave: solo token firmati dal gateway (prove locali).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise _non_autorizzato("Voucher PDND mancante")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise _non_autorizzato("Voucher PDND mancante")

    cfg = pdnd_settings()
    if cfg.public_key:
        return _verifica_con_chiave_pdnd(token, cfg)
    if not login_sviluppo_consentito(settings.env):
        # Fuori da development/test (staging compreso) senza chiave: mai il
        # ripiego sui token del gateway.
        raise pdnd_non_configurato("Chiave pubblica PDND non configurata (PDND_PUBLIC_KEY)")
    from app.auth.jwt_handler import verify_access_token

    try:
        return verify_access_token(token)
    except ErroreApi:
        raise
    except Exception:  # noqa: BLE001 — qualsiasi rifiuto del token di prova è un 401
        raise _non_autorizzato() from None


@dataclass(frozen=True)
class Fruitore:
    """Fruitore PDND registrato e attivo: id della registrazione, client_id, cliente."""

    id: uuid.UUID
    client_id: str
    tenant_id: uuid.UUID


def _client_id(voucher: dict) -> str:
    client_id = voucher.get("client_id") or voucher.get("sub")
    if not client_id or not isinstance(client_id, str):
        raise _non_autorizzato("Voucher senza client_id")
    return client_id


async def _fruitore(voucher: dict) -> Fruitore:
    """Il fruitore registrato e attivo del voucher (fail-closed, REGOLE §14.3).

    Revisione R05 (R14): ``pdnd_fruitori`` ha la RLS per ente; qui l'ente non è
    ancora noto (lo dà proprio il fruitore), quindi la sola lettura per
    ``client_id`` del voucher GIÀ verificato avviene nell'ambito del
    conservatore, con motivo; il resto della richiesta usa l'ente del fruitore."""
    client_id = _client_id(voucher)
    with ambito_conservatore("autenticazione PDND: fruitore del voucher verificato"):
        return await _leggi_fruitore(client_id)


async def _leggi_fruitore(client_id: str) -> Fruitore:
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "SELECT f.id, f.tenant_id, t.stato::text "
            "FROM public.pdnd_fruitori f "
            "JOIN public.tenants t ON t.id = f.tenant_id "
            "WHERE f.client_id = :client_id AND f.attivo"
        ), {"client_id": client_id})).fetchone()
    if riga is None:
        logger.warning("pdnd_fruitore_non_registrato", client_id=client_id)
        raise ErroreApi("PDND-403", "Il client PDND non è associato a un cliente del servizio")
    if riga[2] in _STATI_TENANT_BLOCCATI:
        raise ErroreApi("PDND-403-CLIENTE", f"Il cliente del fruitore è {riga[2]}")
    return Fruitore(id=riga[0], client_id=client_id, tenant_id=riga[1])


#: Prefisso delle chiavi Redis dei jti già usati.
PREFISSO_JTI = "pdnd:jti:"


async def consuma_jti(voucher: dict, redis: Any, *, adesso: float | None = None,
                      cfg: PdndSettings | None = None) -> None:
    """Il voucher non è mai stato presentato: ne registra il ``jti`` fino alla scadenza.

    ``SET NX EX`` atomico: due richieste contemporanee con lo stesso voucher, una
    sola passa. ``redis`` None: nessun controllo (solo test, come il limite di
    richieste); errore di Redis: 503 PDND-503-REPLAY (fail-closed)."""
    import hashlib
    import time

    cfg = cfg or pdnd_settings()
    if not cfg.voucher_monouso or redis is None:
        return
    jti = voucher.get("jti")
    if not isinstance(jti, str) or not jti.strip():
        logger.info("pdnd_voucher_rifiutato", motivo="senza_jti")
        raise ErroreApi("PDND-401-RIUSATO", "Voucher PDND senza jti",
                        headers=dict(_WWW_AUTHENTICATE))
    ora = time.time() if adesso is None else adesso
    try:
        scadenza = int(voucher.get("exp") or 0)
    except (TypeError, ValueError):
        scadenza = 0
    durata = max(1, int(scadenza - ora) + cfg.tolleranza_secondi)
    impronta = hashlib.sha256(f"{voucher.get('iss', '')}|{jti}".encode()).hexdigest()
    try:
        nuovo = await redis.set(PREFISSO_JTI + impronta, "1", nx=True, ex=durata)
    except Exception as exc:
        from libs.shared.security.quote import errore_redis

        if not errore_redis(exc):
            raise
        logger.error("pdnd_jti_non_verificabile", errore=type(exc).__name__)
        raise ErroreApi("PDND-503-REPLAY") from None
    if not nuovo:
        logger.warning("pdnd_voucher_riusato", client_id=voucher.get("client_id"))
        raise ErroreApi("PDND-401-RIUSATO", headers=dict(_WWW_AUTHENTICATE))


async def fruitore_autorizzato(authorization: str | None, redis: Any) -> Fruitore:
    """Voucher valido, fruitore registrato e attivo, voucher mai usato prima (R04),
    limite di richieste al minuto.

    R03 f1 (REV-03, difetto 5 di c2): il limite QTA-001 del piano dell'ente vale
    anche su PDND ed è COMUNE a tutti i canali del gateway (stessa finestra Redis
    per ente di ``get_current_user``: REST, sessioni, SFTP)."""
    voucher = _validate_pdnd_voucher(authorization)
    fruitore = await _fruitore(voucher)
    await consuma_jti(voucher, redis)
    limiti = await limiti_ente(fruitore.tenant_id, IMPOSTAZIONI_QUOTE)
    await check_rate_limit(redis, chiave_richieste(str(fruitore.tenant_id)), limiti)
    # R05 (VD-C-080): il voucher PDND verificato vale come identità dell'ente del
    # fruitore: le transazioni successive della richiesta vedono solo quell'ente
    imposta_ambito_identita({"tenant_id": str(fruitore.tenant_id), "ruolo": "FRUITORE_PDND"})
    return fruitore


async def _tenant_del_fruitore(authorization: str | None, redis: Any) -> tuple[str, str]:
    """(client_id, tenant_id) del fruitore registrato e attivo (con limite al minuto)."""
    fruitore = await fruitore_autorizzato(authorization, redis)
    return fruitore.client_id, str(fruitore.tenant_id)


def _uuid_o_404(valore: str) -> str:
    try:
        return str(uuid.UUID(valore))
    except ValueError:
        raise ErroreApi("PDND-404") from None


# ── E-service 1: Versamento PA ───────────────────────────────────────

class VersamentoPdndRequest(BaseModel):
    """Versamento di un documento (principale) tramite PDND.

    R03 (VD-C-092): ``id_documento`` e ``numero`` sono OBBLIGATORI e vengono dal
    fruitore (prima erano generati dall'orario); ``formato`` è facoltativo (il
    formato si rileva dal contenuto)."""

    classe_documentale: str = Field(..., min_length=1, max_length=50)
    registro: str | None = Field(None, max_length=100,
                                 description="Registro della chiave UD (es. PROTOCOLLO)")
    anno: str = Field(..., pattern=r"^\d{4}$")
    numero: str = Field(..., min_length=1, max_length=100,
                        description="Numero della chiave UD: con registro e anno identifica "
                                    "l'unità documentaria (VD-C-095)")
    id_documento: str = Field(..., min_length=1, max_length=100,
                              description="Identificativo del documento nel sistema del fruitore")
    nome_file: str = Field(..., min_length=1, max_length=255)
    formato: str | None = Field(None, max_length=255,
                                description="MIME dichiarato (facoltativo; si confronta con il "
                                            "formato rilevato dal contenuto)")
    hash_sha256: str = Field(..., pattern=r"^[0-9a-fA-F]{64}$")
    file_b64: str = Field(..., description="File in base64 standard (limite PDND_MAX_FILE_MB)")
    metadati: dict[str, Any] = Field(default_factory=dict,
                                     description="Metadati del documento (Allegato 5 e della "
                                                 "classe), come «parametri» del REST")


def _limite_corpo() -> int:
    """Byte del corpo JSON: file in base64 (4/3) più i campi diversi dal file."""
    return settings.pdnd_max_file_mb * MB * 4 // 3 + settings.pdnd_max_metadati_mb * MB


#: Versamenti PDND contemporanei in questa replica (REV-05): oltre, 503 ritentabile.
LIMITE_VERSAMENTI_PDND = LimiteConcorrenza(
    lambda: settings.pdnd_richieste_contemporanee_max, "PDND-503-OCCUPATO")


def indice_pdnd(richiesta: VersamentoPdndRequest) -> IndiceUD:
    """Traduzione pura della richiesta PDND nell'indice del caso d'uso (VRS-001 se non valido)."""
    try:
        return IndiceUD.model_validate({
            "classe_documentale": richiesta.classe_documentale,
            "chiave": {"registro": richiesta.registro, "anno": richiesta.anno,
                       "numero": richiesta.numero},
            "documenti": [{
                "id": richiesta.id_documento, "tipo": "PRINCIPALE",
                "nome_file": richiesta.nome_file, "formato": richiesta.formato,
                "hash": {"impronta": richiesta.hash_sha256.lower(), "algoritmo": "SHA-256"},
                "parametri": richiesta.metadati,
            }],
        })
    except ValidationError as exc:
        raise ErroreApi("VRS-001", str(exc)[:2000]) from None


def _campi(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"campo": ".".join(str(p) for p in e["loc"]), "posizione": "body", "tipo": e["type"],
             "messaggio": e["msg"]} for e in exc.errors()[:20]]


@router.post(
    "/versamento", status_code=202, response_model=VersamentoResponse,
    summary="E-service 1 PDND — Versamento PA",
    responses={200: {"model": VersamentoResponse,
                     "description": "Stessa richiesta ripetuta (stessa Idempotency-Key)"},
               **RISPOSTE_ERRORE},
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
        "schema": VersamentoPdndRequest.model_json_schema()}}}},
)
async def versamento_pdnd(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    redis: Any = Depends(get_redis),
) -> JSONResponse:
    """Versamento per conto del cliente del fruitore PDND: stesso caso d'uso, stesse
    regole, stesse risposte del REST (202 nuovo, 200 ripetuto, errori del catalogo).

    R03 f1 (REV-05): il corpo si legge IN STREAMING (``leggi_json_con_file``): il
    base64 si valida e si decodifica a blocchi in un file temporaneo mentre arriva,
    senza copie dell'intero file in memoria; al più
    ``PDND_RICHIESTE_CONTEMPORANEE_MAX`` versamenti per replica (oltre: 503
    PDND-503-OCCUPATO, ritentabile, prima di leggere il corpo). R03 f3 (REV-N2):
    il file decodificato prenota il suo massimo nel budget unico del disco
    temporaneo (``DISCO_TEMPORANEO_BUDGET_MB``); budget esaurito: stesso 503."""
    fruitore = await fruitore_autorizzato(authorization, redis)
    limite = settings.pdnd_max_file_mb * MB
    # R03 f3 (REV-N2): il file decodificato va su /tmp: prenotazione nel budget
    # unico del disco temporaneo (condiviso con il multipart) prima di leggere
    with LIMITE_VERSAMENTI_PDND.posto(), BUDGET_DISCO_TEMPORANEO.prenota(
            min(byte_da_prenotare(request, _limite_corpo()) * 3 // 4, limite),
            LIMITE_VERSAMENTI_PDND.codice):
        corpo = await leggi_json_con_file(
            request, campo="file_b64", limite_corpo=_limite_corpo(),
            limite_altri=settings.pdnd_max_metadati_mb * MB, limite_file=limite,
            codice_corpo="PDND-413", codice_file="PDND-413", codice_base64="PDND-422")
        try:
            try:
                richiesta = VersamentoPdndRequest.model_validate_json(corpo.json)
            except ValidationError as exc:
                raise ErroreApi("VAL-422", "Richiesta di versamento PDND non valida",
                                campi=_campi(exc)) from None
            if corpo.file is None:  # difesa: il modello richiede già il campo
                raise ErroreApi("VAL-422", "Campo file_b64 mancante")
            indice = indice_pdnd(richiesta)
            fonte = FonteFlusso(nome_file=richiesta.nome_file, flusso=corpo.file.blocchi(),
                                content_type=richiesta.formato,
                                dimensione_annunciata=corpo.file.dimensione, limite_byte=limite)
            logger.info("pdnd_versamento", client_id=fruitore.client_id,
                        tenant_id=str(fruitore.tenant_id), classe=richiesta.classe_documentale)
            esito = await versa(RichiestaVersamento(
                indice=indice, fonti=[fonte],
                # L'utente del PdV è la registrazione del fruitore (sistema, non persona)
                identita=IdentitaVersante(fruitore.tenant_id, fruitore.id, Canale.PDND),
                idempotency_key=idempotency_key, adesso=datetime.now(UTC),
            ))
        finally:
            corpo.chiudi()
    if esito.nuovo:
        from libs.shared.security.audit import log_audit

        await log_audit(evento="pdnd.versamento", tenant_id=str(fruitore.tenant_id),
                        user_id=str(fruitore.id),
                        dettagli={"pdv_id": esito.pdv_id, "classe": richiesta.classe_documentale,
                                  "client_id": fruitore.client_id})
    return risposta_versamento(esito)


# ── E-service 2: Consultazione AIP ──────────────────────────────────

@router.get("/aip/{aip_id}", summary="E-service 2 PDND — Consultazione AIP",
            responses=RISPOSTE_ERRORE)
async def consulta_aip_pdnd(
    aip_id: str,
    authorization: str | None = Header(default=None),
    redis: Any = Depends(get_redis),
) -> dict:
    """Accesso all'AIP del cliente del fruitore PDND."""
    ente_id, tenant_id = await _tenant_del_fruitore(authorization, redis)
    aid = _uuid_o_404(aip_id)

    async with get_async_session() as session:
        row = (await session.execute(text(
            "SELECT id::text, urn, stato::text, hash_sha256, "
            "dimensione_bytes, data_versamento, anni_conservazione "
            "FROM public.aip "
            "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"
        ), {"id": aid, "tid": tenant_id})).fetchone()

    if not row:
        raise ErroreApi("PDND-404")

    logger.info("pdnd_aip_consultato", aip_id=aid, ente_id=ente_id, tenant_id=tenant_id)

    return {
        "aip_id":           row[0],
        "urn":              row[1],
        "stato":            row[2],
        "hash_sha256":      row[3],
        "dimensione_bytes": row[4],
        "data_versamento":  row[5].isoformat() if row[5] else None,
        "anni_conservazione": row[6],
        "ente_richiedente": ente_id,
    }


# ── E-service 3: Verifica integrità hashchain ────────────────────────

#: Cosa la risposta dell'e-service 3 verifica davvero oggi (e cosa no): R04 aggiunge
#: prove d'inclusione Merkle, checkpoint firmati e verifica continua (piano §5).
VERIFICHE_ESEGUITE = ("registrazione_nella_catena",)
VERIFICHE_NON_ESEGUITE = (
    "ricalcolo_delle_impronte_sui_byte_conservati",
    "prova_di_inclusione_merkle",
    "verifica_della_firma_e_della_marca_del_blocco",
)


@router.get(
    "/hashchain/verify/{aip_id}",
    summary="E-service 3 PDND — Registrazione dell'AIP nella catena delle impronte",
    responses=RISPOSTE_ERRORE,
)
async def verifica_integrita_pdnd(
    aip_id: str,
    authorization: str | None = Header(default=None),
    redis: Any = Depends(get_redis),
) -> dict:
    """Dice se un AIP del cliente del fruitore è registrato in un blocco della catena.

    Dichiara cosa verifica: SOLO la registrazione (stato dell'AIP e blocco
    collegato). NON ricalcola le impronte sui byte conservati, non produce una
    prova d'inclusione e non verifica firma e marca del blocco (VD-C-008, R04).
    """
    _, tenant_id = await _tenant_del_fruitore(authorization, redis)
    aid = _uuid_o_404(aip_id)

    async with get_async_session() as session:
        # Join a 2 tabelle: AIP e blocco della catena che lo contiene
        row = (await session.execute(text(
            "SELECT a.id::text, a.urn, a.stato::text, a.hash_sha256, "
            "a.hashchain_block_id::text, "
            "h.numero_blocco, h.hash_blocco, h.merkle_root "
            "FROM public.aip a "
            "LEFT JOIN public.hashchain_blocks h ON h.id = a.hashchain_block_id "
            "WHERE a.id = CAST(:id AS uuid) AND a.tenant_id = CAST(:tid AS uuid)"
        ), {"id": aid, "tid": tenant_id})).fetchone()

    if not row:
        raise ErroreApi("PDND-404")

    in_catena = row[2] in ("IN_CATENA", "CONSERVATO") and row[4] is not None

    return {
        "aip_id":           row[0],
        "urn":              row[1],
        "in_catena":        in_catena,
        "numero_blocco":    row[5],
        "hash_blocco":      row[6],
        "merkle_root":      row[7],
        "hash_documento":   row[3],
        "verifiche_eseguite": list(VERIFICHE_ESEGUITE),
        "verifiche_non_eseguite": list(VERIFICHE_NON_ESEGUITE),
        "nota": ("Esito della sola registrazione nella catena delle impronte di VerumDoc: "
                 "le impronte non sono ricalcolate sui byte conservati e la risposta non "
                 "contiene una prova d'inclusione verificabile."),
        "verifica_at":      datetime.now(UTC).isoformat(),
    }
