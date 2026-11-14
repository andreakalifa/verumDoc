# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
API del registro delle impronte a due livelli (contratto K1 di R04).

Servizio: hashchain, prefisso ``/api/v1/hashchain``. Autenticazione come le
altre API della catena (token RS256 verificato, revoca applicata).

- ``GET /checkpoint``: ultimo checkpoint pubblicato (nota K2, text/plain), con
  la controfirma del testimone se presente;
- ``GET /checkpoint/{dimensione}``: il checkpoint emesso a quella dimensione;
- ``GET /prova/inclusione?pda=&documento=[&dimensione=]``: prova documento →
  PdA → registro riferita a un checkpoint. Isolamento per ente: un PdA di un
  altro ente risponde 404 come uno inesistente (REGOLE §14.4); i ruoli del
  conservatore (SUPER_ADMIN, RSC_CONSERVATORE) vedono tutti i PdA;
- ``GET /prova/consistenza?da=&a=``: prova fra due dimensioni (solo hash,
  nessun dato di ente);
- ``GET /registro/foglie?da=&limite=``: hash delle foglie fino all'ultimo
  checkpoint, per ricostruire l'albero. Solo SUPER_ADMIN e RSC_CONSERVATORE o un
  token di SERVIZIO (audience ``CATENA_PUBBLICO_SERVIZIO``, scope
  ``CATENA_SCOPE_LETTURA_FOGLIE``: testimone, verificatori).
Errori dal catalogo (famiglia CAT): 403, 404, 409, 422, 503.
"""
from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, Path, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import RUOLI_VERIFICA, chiavi_jwt, utente_verificato
from app.config import settings
from app.registro import archivio, prove
from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.security.jwt_rs256 import ErroreTokenNonValido, verifica_token_servizio

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/hashchain", tags=["Registro delle impronte"])

TESTO = "text/plain; charset=utf-8"
LIMITE_FOGLIE_MAX = 10000


class ProvaPda(BaseModel):
    foglia_indice: int = Field(..., description="Posizione della foglia nell'albero del PdA")
    dimensione: int = Field(..., description="Foglie dell'albero del PdA")
    percorso: list[str] = Field(..., description="Percorso di audit RFC 6962 §2.1.1 (hex)")


class ProvaRegistro(BaseModel):
    foglia_indice: int = Field(..., description="Indice della foglia del PdA nel registro")
    dimensione_albero: int = Field(..., description="Dimensione del registro nel checkpoint")
    percorso: list[str] = Field(..., description="Percorso di audit RFC 6962 §2.1.1 (hex)")


class FogliaCicloVita(BaseModel):
    tipo: str = Field(..., description="scarto, annullamento o rettifica")
    foglia_dati: str = Field(..., description="Foglia di formato 2 (JSON canonico)")
    prova_registro: ProvaRegistro


class RispostaInclusione(BaseModel):
    pda: str = Field(..., description="URN del PdA")
    documento: str = Field(..., description="Identificativo del documento nel PdA")
    foglia_dati: str | None = Field(..., description="Foglia del documento (JSON canonico); "
                                    "null per i PdA in riversamento")
    prova_pda: ProvaPda | None
    radice_pda: str | None = Field(..., description="Radice dell'albero del PdA (hex)")
    foglia_registro_dati: str = Field(..., description="Foglia del PdA nel registro")
    prova_registro: ProvaRegistro
    checkpoint: str = Field(..., description="Checkpoint (nota firmata C2SP) di riferimento")
    ciclo_vita: list[FogliaCicloVita] = Field(
        default_factory=list,
        description="R05: foglie del ciclo di vita del PdA (scarto, annullamento, rettifica) "
                    "coperte dallo stesso checkpoint, con la loro prova: uno scarto o un "
                    "annullamento rende il contenuto NON più conservato. Scarto e "
                    "annullamento ci sono SEMPRE tutti; le rettifiche al più "
                    "CATENA_CICLO_VITA_MAX_FOGLIE (le più recenti)")
    ciclo_vita_totale: int = Field(
        0, description="Revisione R05 (R4): numero TOTALE delle foglie del ciclo di vita del "
                       "PdA coperte dal checkpoint")
    ciclo_vita_completo: bool = Field(
        True, description="Revisione R05 (R4): false se «ciclo_vita» non le contiene tutte "
                          "(rettifiche oltre il limite): lo stato del documento non è allora "
                          "determinabile dalla sola prova e il verificatore non conclude "
                          "«conservato»")
    attestazione_ciclo_vita: str | None = Field(
        None, description="Revisione 2 di R05 (S8): nota firmata (C2SP, chiave del registro) "
                          "che impegna, per questo PdA e questo checkpoint, il numero delle "
                          "foglie del ciclo di vita e l'impronta del loro elenco "
                          "(libs/catena/attestazione.py): una foglia di scarto tolta dalla "
                          "prova non torna più. Senza, l'assenza di scarti non è dimostrabile. "
                          "Revisione 3 di R05: v2, rilasciata SOLO per l'ultimo checkpoint, con "
                          "l'istante del rilascio e lo stato del PdA firmati")
    attestazione_assente: str | None = Field(
        None, description="Revisione 3 di R05 (T2): perché l'attestazione manca (checkpoint "
                          "non più l'ultimo, chiave del registro non configurata)")
    ultimo_checkpoint: int | None = Field(
        None, description="Revisione 3 di R05 (T2): dimensione dell'ultimo checkpoint del "
                          "registro al momento della risposta")
    stato_pda: Literal["CONSERVATO", "SCARTO", "SCARTO_PARZIALE"] = Field(
        "CONSERVATO", description="Revisione 3 di R05 (T3): stato del PdA (SCARTO: scarto "
                                  "avviato o eseguito; SCARTO_PARZIALE: cancellazione iniziata "
                                  "e non completata). Firmato nell'attestazione")
    attestazione_sigillo: str | None = Field(
        None, description="R13a (VD-C-124): sigillo qualificato del conservatore sul testo "
                          "dell'attestazione (CAdES staccata con marca, base64), secondo "
                          "ATTESTAZIONE_SIGILLO (SEMPRE, SU_RICHIESTA con sigillo=true, "
                          "DISATTIVO): protegge anche da chi controlla la chiave del registro")
    attestazione_sigillo_stato: Literal["FIRMATO", "NON_FIRMATO", "NON_DISPONIBILE",
                                        "NON_RICHIESTO", "DISATTIVO"] = Field(
        "DISATTIVO", description="R13a: esito del sigillo sull'attestazione")
    attestazione_sigillo_motivo: str | None = None


class RispostaConsistenza(BaseModel):
    da: int
    a: int
    percorso: list[str] = Field(..., description="Prova RFC 6962 §2.1.2 (hex)")


class RispostaFoglie(BaseModel):
    da: int
    foglie: list[str] = Field(..., description="Hash di foglia (hex) da «da» in poi")
    dimensione_checkpoint: int = Field(..., description="Dimensione dell'ultimo checkpoint: "
                                       "le foglie restituite non la superano")


def _errore_db(exc: BaseException) -> ErroreApi:
    return errore_registrato("CAT-503", exc, evento="errore_registro")


def _conservatore(utente: dict[str, Any]) -> bool:
    return utente.get("ruolo") in RUOLI_VERIFICA


async def _nota(dimensione: int | None) -> PlainTextResponse:
    try:
        async with get_async_session() as session:
            cp = await archivio.ultimo_checkpoint(session, dimensione=dimensione)
    except SQLAlchemyError as exc:
        raise _errore_db(exc) from None
    if cp is None:
        raise ErroreApi("CAT-404", "Nessun checkpoint emesso" if dimensione is None
                        else f"Nessun checkpoint alla dimensione {dimensione}")
    return PlainTextResponse(archivio.nota_pubblicata(cp), media_type=TESTO)


@router.get("/checkpoint", response_class=PlainTextResponse,
            summary="Ultimo checkpoint del registro",
            responses={200: {"content": {TESTO: {}}}})
async def ultimo_checkpoint(_utente: dict = Depends(utente_verificato)) -> PlainTextResponse:
    """Ultimo checkpoint (C2SP tlog-checkpoint in nota firmata, K2)."""
    return await _nota(None)


@router.get("/checkpoint/{dimensione}", response_class=PlainTextResponse,
            summary="Checkpoint a una dimensione", responses={200: {"content": {TESTO: {}}}})
async def checkpoint_dimensione(
    dimensione: int = Path(..., ge=1, le=2**62, description="Dimensione del registro"),
    _utente: dict = Depends(utente_verificato),
) -> PlainTextResponse:
    """Il checkpoint emesso a ``dimensione`` (404 CAT-404 se non esiste)."""
    return await _nota(dimensione)


def _firmatario_attestazioni() -> Any:
    """Chiave del registro per l'attestazione del ciclo di vita (S8): la stessa
    dei checkpoint. Non configurata (solo fuori dall'esercizio, dove l'avvio la
    pretende) → nessuna attestazione: il verificatore dirà che l'assenza di
    scarti non è dimostrabile."""
    from app.registro import chiavi

    try:
        return chiavi.firmatario_registro(settings)
    except chiavi.ConfigurazioneCatenaNonValida:
        return None


@router.get("/prova/inclusione", response_model=RispostaInclusione,
            summary="Prova d'inclusione documento → PdA → registro")
async def prova_inclusione(
    pda: str = Query(..., min_length=1, max_length=500, description="URN del PdA"),
    documento: str = Query(..., min_length=1, max_length=500,
                           description="Identificativo del documento (per i PdA in "
                                       "riversamento: nome del file)"),
    dimensione: int | None = Query(None, ge=1, le=2**62,
                                   description="Checkpoint di riferimento (predefinito: ultimo). "
                                               "Revisione 3 di R05: l'attestazione del ciclo di "
                                               "vita c'è solo per l'ultimo checkpoint"),
    sigillo: bool = Query(False, description="R13a (VD-C-124): sigillo qualificato "
                                             "sull'attestazione (con ATTESTAZIONE_SIGILLO = "
                                             "SU_RICHIESTA)"),
    utente: dict = Depends(utente_verificato),
) -> dict[str, Any]:
    """Prova riferita a un checkpoint; 404 per PdA di altri enti o inesistenti, 409 se il
    PdA non è ancora coperto dal checkpoint."""
    try:
        async with get_async_session() as session:
            trovato = await prove.pda_per_urn(session, pda)
            if trovato is None or (not _conservatore(utente)
                                   and str(utente.get("tenant_id")) != trovato.tenant_id):
                raise ErroreApi("CAT-404", "PdA non trovato")
            risposta = await prove.prova_inclusione_documento(
                session, trovato, documento, dimensione,
                limite_ciclo_vita=settings.catena_ciclo_vita_max_foglie,
                firmatario=_firmatario_attestazioni())
    except prove.ProvaNonDisponibile as exc:
        raise ErroreApi(exc.codice, exc.messaggio) from None
    except SQLAlchemyError as exc:
        raise _errore_db(exc) from None
    # R13a: il sigillo si chiede FUORI dalla transazione (servizio esterno)
    from app.registro.firme_documenti import sigillo_attestazione
    from app.services.firma_blocchi import servizio_firma_predefinito

    risposta.update(await sigillo_attestazione(
        risposta.get("attestazione_ciclo_vita"), richiesto=sigillo,
        servizio=servizio_firma_predefinito()))
    return risposta


@router.get("/prova/consistenza", response_model=RispostaConsistenza,
            summary="Prova di consistenza fra due checkpoint")
async def prova_consistenza(
    da: int = Query(..., ge=0, le=2**62, description="Dimensione del checkpoint più vecchio"),
    a: int = Query(..., ge=1, le=2**62, description="Dimensione del checkpoint più recente"),
    _utente: dict = Depends(utente_verificato),
) -> dict[str, Any]:
    """Prova RFC 6962 §2.1.2 da ``da`` ad ``a`` (``a`` non oltre l'ultimo checkpoint)."""
    try:
        async with get_async_session() as session:
            ultimo = await archivio.ultimo_checkpoint(session)
            if da > a or ultimo is None or a > int(ultimo["dimensione"]):
                raise ErroreApi("CAT-422", "Servono 0 ≤ da ≤ a ≤ dimensione dell'ultimo "
                                           "checkpoint", dettagli={
                                               "ultimo_checkpoint": None if ultimo is None
                                               else int(ultimo["dimensione"])})
            return await prove.prova_consistenza(session, da, a)
    except SQLAlchemyError as exc:
        raise _errore_db(exc) from None


async def lettore_foglie(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Token di servizio (audience e scope del registro) oppure utente del conservatore."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            servizio = verifica_token_servizio(
                token, chiave_pubblica_pem=chiavi_jwt().pubblica_pem,
                emittente=settings.jwt_issuer, pubblico=settings.catena_pubblico_servizio)
        except ErroreTokenNonValido:
            servizio = None
        if servizio is not None:
            if settings.catena_scope_lettura_foglie not in (servizio.get("scopes") or []):
                raise ErroreApi("CAT-403", "Token di servizio senza lo scope di lettura "
                                           "del registro")
            return servizio
    utente = await utente_verificato(authorization)
    if not _conservatore(utente):
        raise ErroreApi("CAT-403", "Riservato a SUPER_ADMIN, RSC_CONSERVATORE e al testimone")
    return utente


@router.get("/registro/foglie", response_model=RispostaFoglie,
            summary="Hash delle foglie del registro")
async def foglie_registro(
    da: int = Query(0, ge=0, le=2**62, description="Indice della prima foglia"),
    limite: int = Query(1000, ge=1, le=LIMITE_FOGLIE_MAX, description="Foglie al massimo"),
    _lettore: dict = Depends(lettore_foglie),
) -> dict[str, Any]:
    """Hash di foglia (hex) in ordine d'indice, solo fino all'ultimo checkpoint."""
    try:
        async with get_async_session() as session:
            ultimo = await archivio.ultimo_checkpoint(session)
            n = int(ultimo["dimensione"]) if ultimo else 0
            fine = min(n, da + limite)
            righe = [] if da >= fine else (await session.execute(text(
                "SELECT hash FROM public.catena_registro WHERE indice >= :da AND indice < :a "
                "ORDER BY indice"
            ), {"da": da, "a": fine})).scalars().all()
    except SQLAlchemyError as exc:
        raise _errore_db(exc) from None
    return {"da": da, "foglie": [bytes(h).hex() for h in righe], "dimensione_checkpoint": n}


# ── R06 (VD-C-129): allarmi del registro e del testimone ─────────────────

class Allarme(BaseModel):
    id: str = Field(description="«testimone-<n>» o «blocco-<n>»")
    fonte: Literal["testimone", "registro"]
    causa: str
    aperto_il: str
    ultimo_il: str
    occorrenze: int = Field(description="Rifiuti del testimone o occorrenze del blocco")
    dettagli: dict[str, Any] = Field(default_factory=dict)
    aperto: bool
    gestito_il: str | None = None
    gestito_da: str | None = None
    nota: str | None = None


class Condizione(BaseModel):
    codice: str
    attiva: bool
    descrizione: str
    dettagli: dict[str, Any] = Field(default_factory=dict)


class RispostaAllarmi(BaseModel):
    items: list[Allarme]
    limit: int
    cursore: str | None
    altri: bool
    condizioni: list[Condizione] = Field(
        description="Condizioni del momento (non persistenti), con le soglie della configurazione")
    soglie: dict[str, float]


_ALLARMI = """
    SELECT * FROM (
        SELECT 'testimone' AS fonte, id, causa, aperto_il, ultimo_il, rifiuti AS occorrenze,
               jsonb_build_object('prima_dimensione', prima_dimensione,
                                  'ultima_dimensione', ultima_dimensione,
                                  'ultimo_motivo', ultimo_motivo) AS dettagli,
               gestito_il AS chiuso_il, gestito_da AS chiuso_da, nota
        FROM public.catena_allarmi_testimone
        UNION ALL
        SELECT 'registro' AS fonte, id, causa, aperto_il, ultimo_il, occorrenze, dettagli,
               chiuso_il, chiuso_da, nota
        FROM public.catena_blocchi_registro
    ) a
    WHERE (:solo_aperti = false OR a.chiuso_il IS NULL)
      AND (CAST(:quando AS timestamptz) IS NULL
           OR (a.aperto_il, a.fonte, a.id) < (CAST(:quando AS timestamptz), :fonte, :ultimo))
    ORDER BY a.aperto_il DESC, a.fonte DESC, a.id DESC
    LIMIT :lim"""


def _codifica_cursore_allarmi(quando: Any, fonte: str, ultimo: int) -> str:
    import base64

    grezzo = f"{quando.isoformat()}|{fonte}|{ultimo}".encode()
    return base64.urlsafe_b64encode(grezzo).decode().rstrip("=")


def _decodifica_cursore_allarmi(cursore: str) -> tuple[Any, str, int]:
    import base64
    from datetime import datetime

    try:
        grezzo = base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)).decode()
        istante, fonte, ultimo = grezzo.split("|", 2)
        quando = datetime.fromisoformat(istante)
        if quando.tzinfo is None or fonte not in ("testimone", "registro"):
            raise ValueError("cursore non valido")
        return quando, fonte, int(ultimo)
    except (ValueError, UnicodeDecodeError):
        raise ErroreApi("CAT-400-CURSORE", "Cursore non valido per l'elenco degli allarmi") \
            from None


async def _condizioni(session: Any) -> list[dict[str, Any]]:
    """Condizioni del momento: checkpoint senza controfirma oltre
    CATENA_CONTROFIRMA_MAX_SECONDI, registro sospeso, testimone non configurato."""
    soglia = float(settings.catena_controfirma_max_secondi)
    riga = (await session.execute(text(
        "SELECT min(dimensione) AS dimensione, min(emesso_il) AS emesso_il, "
        "EXTRACT(EPOCH FROM now() - min(emesso_il)) AS eta "
        "FROM public.catena_checkpoint WHERE stato_testimone = 'IN_ATTESA'"
    ))).mappings().first()
    eta = float(riga["eta"]) if riga and riga["eta"] is not None else None
    testimone = bool(settings.testimone_url)
    return [
        {"codice": "CHECKPOINT_SENZA_CONTROFIRMA", "attiva": eta is not None and eta > soglia,
         "descrizione": "Checkpoint in attesa della controfirma del testimone oltre "
                        "CATENA_CONTROFIRMA_MAX_SECONDI",
         "dettagli": {"dimensione": riga["dimensione"] if riga else None,
                      "emesso_il": riga["emesso_il"].isoformat() if eta is not None else None,
                      "attesa_secondi": round(eta, 1) if eta is not None else None,
                      "soglia_secondi": soglia}},
        {"codice": "REGISTRO_SOSPESO", "attiva": bool(settings.catena_registro_sospeso),
         "descrizione": "Registro sospeso (CATENA_REGISTRO_SOSPESO, dopo un ripristino)",
         "dettagli": {}},
        {"codice": "TESTIMONE_NON_CONFIGURATO", "attiva": not testimone,
         "descrizione": "Nessun testimone configurato: i checkpoint non sono controfirmati",
         "dettagli": {}},
    ]


@router.get("/registro/allarmi", response_model=RispostaAllarmi,
            summary="Allarmi del registro e del testimone (conservatore)")
async def allarmi_registro(
    aperti: bool = Query(True, description="Solo gli allarmi non ancora gestiti/chiusi"),
    cursore: str | None = Query(None, max_length=500),
    limite: int = Query(50, ge=1, le=200),
    utente: dict = Depends(utente_verificato),
) -> dict[str, Any]:
    """R06 (VD-C-129). Allarmi persistenti, dal più recente, a cursore: rifiuti del
    testimone raggruppati per causa (``catena_allarmi_testimone``) e blocchi del
    registro (``catena_blocchi_registro``); in più le condizioni del momento con
    le soglie (``CATENA_CONTROFIRMA_MAX_SECONDI``). Solo SUPER_ADMIN e
    RSC_CONSERVATORE."""
    if not _conservatore(utente):
        raise ErroreApi("CAT-403", "Allarmi del registro riservati a SUPER_ADMIN e "
                                   "RSC_CONSERVATORE")
    quando, fonte, ultimo = _decodifica_cursore_allarmi(cursore) if cursore else (None, "", 0)
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(_ALLARMI), {
                "solo_aperti": aperti, "quando": quando, "fonte": fonte, "ultimo": ultimo,
                "lim": limite + 1})).mappings().all()
            condizioni = await _condizioni(session)
    except SQLAlchemyError as exc:
        raise _errore_db(exc) from None
    altri = len(righe) > limite
    righe = righe[:limite]
    items = [{
        "id": f"{'testimone' if r['fonte'] == 'testimone' else 'blocco'}-{r['id']}",
        "fonte": r["fonte"], "causa": r["causa"], "aperto_il": r["aperto_il"].isoformat(),
        "ultimo_il": r["ultimo_il"].isoformat(), "occorrenze": int(r["occorrenze"]),
        "dettagli": r["dettagli"] if isinstance(r["dettagli"], dict) else {},
        "aperto": r["chiuso_il"] is None,
        "gestito_il": r["chiuso_il"].isoformat() if r["chiuso_il"] else None,
        "gestito_da": r["chiuso_da"], "nota": r["nota"],
    } for r in righe]
    prossimo = _codifica_cursore_allarmi(righe[-1]["aperto_il"], righe[-1]["fonte"],
                                         int(righe[-1]["id"])) if altri and righe else None
    return {"items": items, "limit": limite, "cursore": prossimo, "altri": altri,
            "condizioni": condizioni,
            "soglie": {"controfirma_max_secondi": float(settings.catena_controfirma_max_secondi),
                       "checkpoint_secondi": float(settings.catena_checkpoint_secondi)}}
