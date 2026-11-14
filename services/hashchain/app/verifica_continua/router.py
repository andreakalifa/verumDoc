# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
API delle verifiche d'integrità (prefisso ``/api/v1/hashchain``, servizio hashchain).

- ``POST /verifiche``: verifica su richiesta di un elenco di PdA (id o URN) o di
  tutti i PdA di un ente; risponde 202 con lo stato della richiesta (il lavoro lo
  fa il lavoratore ``python -m app.verifica_continua``, con precedenza sulla
  rotazione). 403 INT-403 ruolo non ammesso, 404 INT-404 PdA o ente non trovati
  (anche se di un altro ente), 409 INT-409 verifica dell'ente già attiva,
  422 INT-422 corpo non valido.
- ``GET /verifiche/{richiesta_id}``: stato della richiesta (404 se di altri).
- ``GET /verifiche/registro``: registro delle verifiche dal più recente, a
  cursore, filtrabile per ente, PdA ed esito (``ANOMALIE`` = tutti tranne OK).
- ``GET /verifiche/riepilogo``: copertura del ciclo (PdA verificati negli ultimi
  N giorni sul totale programmato), anomalie aperte, ritardo della rotazione,
  stato del lavoratore e del confronto con le foglie K1. Per il manuale e il
  cruscotto.

Autenticazione: token verificato (``utente_verificato``, §19.1). Perimetro:
``perimetro.py`` (conservatore = tutti gli enti; utenti d'ente = il proprio).
Inclusione in ``app/main.py``: ``app.include_router(verifiche_router)`` (a cura
del coordinatore).
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import utente_verificato
from app.verifica_continua import repository
from app.verifica_continua.config import impostazioni
from app.verifica_continua.perimetro import FuoriPerimetro, Perimetro, perimetro_di
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/hashchain", tags=["Verifiche d'integrità"],
                   responses=RISPOSTE_ERRORE)


# ── modelli ──────────────────────────────────────────────────────────────────

class RichiestaVerifica(BaseModel):
    """Esattamente uno fra ``pda`` ed ``ente``."""

    model_config = ConfigDict(extra="forbid")
    pda: list[str] | None = Field(
        None, min_length=1,
        description="PdA da verificare: id (UUID) o URN; al più VERIFICA_RICHIESTA_MAX_PDA")
    ente: uuid.UUID | None = Field(None, description="Verifica di tutti i PdA dell'ente")


class RichiestaCampione(BaseModel):
    """R09 (D05): verifica a campione dopo un incidente di storage segnalato."""

    model_config = ConfigDict(extra="forbid")
    motivo: str = Field(..., min_length=10, max_length=500,
                        description="Incidente che motiva il campione (resta nel log)")
    pda: int | None = Field(None, ge=1, description=(
        "PdA del campione (predefinito: VERIFICA_CAMPIONE_INCIDENTE_PDA del motore)"))


class StatoRichiesta(BaseModel):
    richiesta_id: str
    ente: str | None = Field(None, description="Ente; null per PdA di più enti")
    ambito: str = Field(..., description="PDA oppure ENTE")
    stato: str = Field(..., description="IN_PREPARAZIONE, IN_CORSO, COMPLETATA")
    pda: list[str] | None = Field(None, description="Id dei PdA richiesti (ambito PDA)")
    oggetti_totali: int = Field(..., description="PdA marcati da questa richiesta")
    oggetti_verificati: int
    gia_in_verifica: int = Field(..., description="PdA già marcati da un'altra richiesta attiva")
    anomalie: int = Field(..., description="PdA verificati con esito diverso da OK")
    preparazione_conclusa: bool
    richiesta_da: str | None
    ruolo: str
    creata_il: str | None
    conclusa_il: str | None


class EsitoFiltro(StrEnum):
    OK = "OK"
    DIVERSO = "DIVERSO"
    MANCANTE = "MANCANTE"
    RETENTION_ASSENTE = "RETENTION_ASSENTE"
    ERRORE_LETTURA = "ERRORE_LETTURA"
    ANOMALIE = "ANOMALIE"


class VoceRegistro(BaseModel):
    verificato_il: str
    ente: str
    pda_id: str
    pda: str | None = Field(None, description="URN del PdA")
    documento_id: str | None
    componente: str = Field(..., description="PDA (manifest), IPDA, FILE, RDV")
    esito: str
    algoritmo: str
    impronta_attesa: str | None
    impronta_trovata: str | None
    dimensione_attesa: int | None
    dimensione_trovata: int | None
    retention_modalita: str | None
    retention_fino_a: str | None
    foglia_attesa: str | None = Field(None, description="Foglia K1 registrata (se disponibile)")
    foglia_trovata: str | None = Field(None, description="Foglia ricalcolata dai byte letti")
    tentativo: int
    motivo: str | None
    richiesta_id: str | None


class PaginaRegistro(BaseModel):
    voci: list[VoceRegistro]
    cursore_successivo: str | None = Field(None, description="null = ultima pagina")


class AnomaliaAperta(BaseModel):
    pda_id: str
    pda: str | None
    ente: str
    esito: str | None
    aperta_dal: str | None


class AnomalieAperte(BaseModel):
    numero: int
    oltre_tetto: bool = Field(..., description="true se il numero reale supera il tetto contato")
    elenco: list[AnomaliaAperta]


class StatoLavoratore(BaseModel):
    ultimo_giro_il: str | None
    detentore: str | None
    confronto_foglia: str = Field(..., description=(
        "Fonte delle foglie K1 usata dal lavoratore; NON_DISPONIBILE = confronto non eseguito"))


class Riepilogo(BaseModel):
    ente: str | None
    ciclo_giorni: int
    finestra_giorni: int
    pda_programmati: int = Field(
        ..., description="PdA nella rotazione (oggetto = PdA con i suoi file)")
    pda_verificati_nella_finestra: int
    pda_mai_verificati: int
    copertura_percentuale: float | None = Field(None, description="null se nessun PdA programmato")
    copertura_calcolata_il: str | None
    anomalie_aperte: AnomalieAperte
    ritardo_rotazione_secondi: float | None = Field(
        None, description="Solo per il conservatore senza filtro d'ente")
    ultima_verifica_il: str | None
    lavoratore: StatoLavoratore


# ── utilità ──────────────────────────────────────────────────────────────────

def _perimetro(utente: dict[str, Any]) -> Perimetro:
    try:
        return perimetro_di(utente)
    except FuoriPerimetro:
        raise ErroreApi("INT-404", "Ente del token non riconosciuto") from None


def _db(exc: SQLAlchemyError) -> ErroreApi:
    return errore_registrato("INT-503", exc, evento="errore_verifiche")


def _ente(perimetro: Perimetro, richiesto: uuid.UUID | None) -> uuid.UUID | None:
    try:
        return perimetro.ente_effettivo(richiesto)
    except FuoriPerimetro:
        raise ErroreApi("INT-404", "Ente non trovato") from None


# ── rotte ────────────────────────────────────────────────────────────────────

@router.post("/verifiche", status_code=202, response_model=StatoRichiesta,
             summary="Chiede la verifica d'integrità di PdA o di un ente")
async def chiedi_verifica(
    corpo: RichiestaVerifica,
    utente: dict[str, Any] = Depends(utente_verificato),
) -> dict[str, Any]:
    perimetro = _perimetro(utente)
    if not perimetro.puo_chiedere:
        raise ErroreApi("INT-403")
    if (corpo.pda is None) == (corpo.ente is None):
        raise ErroreApi("INT-422", "Indicare un elenco di PdA oppure un ente (uno dei due)")
    adesso = datetime.now(UTC)
    try:
        if corpo.pda is not None:
            if len(corpo.pda) > impostazioni.richiesta_max_pda:
                raise ErroreApi(
                    "INT-422", f"Al più {impostazioni.richiesta_max_pda} PdA per richiesta")
            if any(not v.strip() or len(v) > 500 for v in corpo.pda):
                raise ErroreApi("INT-422", "Identificativo di PdA vuoto o troppo lungo")
            try:
                stato = await repository.crea_richiesta_pda(
                    [v.strip() for v in corpo.pda], tenant=perimetro.ente,
                    richiesta_da=perimetro.utente, ruolo=perimetro.ruolo, adesso=adesso)
            except repository.NonTrovati as exc:
                raise ErroreApi("INT-404", f"{len(exc.identificativi)} PdA non trovati",
                                dettagli={"non_trovati": exc.identificativi[:100]}) from None
        else:
            ente = _ente(perimetro, corpo.ente)
            if ente is None or not await repository.ente_esiste(ente):
                raise ErroreApi("INT-404", "Ente non trovato")
            try:
                stato = await repository.crea_richiesta_ente(
                    ente, richiesta_da=perimetro.utente, ruolo=perimetro.ruolo, adesso=adesso)
            except repository.RichiestaEnteAttiva as exc:
                raise ErroreApi(
                    "INT-409", dettagli={"richiesta_id": str(exc.richiesta_id)}) from None
    except SQLAlchemyError as exc:
        raise _db(exc) from None
    logger.info("verifica_richiesta", richiesta_id=stato["richiesta_id"], ambito=stato["ambito"],
                ruolo=perimetro.ruolo)
    return stato


@router.post("/verifiche/campione", status_code=202, response_model=StatoRichiesta,
             summary="Verifica a campione dopo un incidente di storage (conservatore)")
async def chiedi_campione(
    corpo: RichiestaCampione,
    utente: dict[str, Any] = Depends(utente_verificato),
) -> dict[str, Any]:
    """R09 (D05): il conservatore (o l'allarme che lo chiama) avvia la rilettura
    immediata di PdA presi a caso fra tutti gli enti; la stessa verifica parte da
    sola quando la verifica continua trova un'anomalia."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import VERIFICA_CAMPIONE_INCIDENTE_PDA

    perimetro = _perimetro(utente)
    if not (perimetro.puo_chiedere and perimetro.conservatore):
        raise ErroreApi("INT-403")
    n = corpo.pda or int((await parametri([VERIFICA_CAMPIONE_INCIDENTE_PDA]))[
        VERIFICA_CAMPIONE_INCIDENTE_PDA])
    try:
        stato = await repository.campione(
            min(n, impostazioni.richiesta_max_pda), richiesta_da=perimetro.utente,
            ruolo=perimetro.ruolo, adesso=datetime.now(UTC))
    except SQLAlchemyError as exc:
        raise _db(exc) from None
    if stato is None:
        raise ErroreApi("INT-404", "Nessun PdA in verifica")
    logger.warning("verifica_campione_richiesto", motivo=corpo.motivo[:300],
                   richiesta_id=stato["richiesta_id"], ruolo=perimetro.ruolo)
    return stato


@router.get("/verifiche/registro", response_model=PaginaRegistro,
            summary="Registro delle verifiche d'integrità (a cursore)")
async def registro_verifiche(
    ente: uuid.UUID | None = Query(None, description="Filtro per ente (solo conservatore)"),
    pda: str | None = Query(None, max_length=500, description="Id o URN del PdA"),
    esito: EsitoFiltro | None = Query(None, description="Esito; ANOMALIE = tutti tranne OK"),
    cursore: str | None = Query(None, max_length=200),
    limite: int = Query(100, ge=1, le=500),
    utente: dict[str, Any] = Depends(utente_verificato),
) -> dict[str, Any]:
    perimetro = _perimetro(utente)
    tenant = _ente(perimetro, ente)
    try:
        aip_id = None
        if pda is not None:
            aip_id = await repository.pda_di_ente(pda.strip(), tenant)
            if aip_id is None:
                raise ErroreApi("INT-404", "PdA non trovato")
        return await repository.elenco_registro(
            tenant=tenant, aip_id=aip_id, esito=esito.value if esito else None,
            cursore=cursore, limite=limite)
    except repository.CursoreNonValido:
        raise ErroreApi("INT-400") from None
    except SQLAlchemyError as exc:
        raise _db(exc) from None


@router.get("/verifiche/riepilogo", response_model=Riepilogo,
            summary="Copertura del ciclo e anomalie aperte (manuale e cruscotto)")
async def riepilogo_verifiche(
    ente: uuid.UUID | None = Query(None, description="Filtro per ente (solo conservatore)"),
    giorni: int | None = Query(None, ge=1, le=3650, description=(
        "Finestra della copertura in giorni (predefinita: VERIFICA_CICLO_GIORNI)")),
    utente: dict[str, Any] = Depends(utente_verificato),
) -> dict[str, Any]:
    perimetro = _perimetro(utente)
    tenant = _ente(perimetro, ente)
    finestra = giorni or impostazioni.ciclo_giorni
    adesso = datetime.now(UTC)
    try:
        per_giorno, calcolata_il = await repository.copertura_per_giorno(tenant, adesso)
        anomalie = await repository.anomalie_aperte(tenant)
        ultima = await repository.ultima_verifica_registrata(tenant)
        ritardo = (await repository.ritardo_rotazione(adesso)
                   if perimetro.conservatore and tenant is None else None)
        lavoratore = await repository.stato_lavoratore()
    except SQLAlchemyError as exc:
        raise _db(exc) from None
    soglia: date = (adesso - timedelta(days=finestra)).date()
    totale = sum(per_giorno.values())
    mai = per_giorno.get(repository.GIORNO_MAI, 0)
    nella_finestra = sum(n for g, n in per_giorno.items()
                         if g > repository.GIORNO_VECCHI and g >= soglia)
    return {
        "ente": str(tenant) if tenant else None,
        "ciclo_giorni": impostazioni.ciclo_giorni, "finestra_giorni": finestra,
        "pda_programmati": totale, "pda_verificati_nella_finestra": nella_finestra,
        "pda_mai_verificati": mai,
        "copertura_percentuale": round(100.0 * nella_finestra / totale, 2) if totale else None,
        "copertura_calcolata_il": calcolata_il,
        "anomalie_aperte": anomalie, "ritardo_rotazione_secondi": ritardo,
        "ultima_verifica_il": ultima.isoformat() if ultima else None,
        "lavoratore": {
            "ultimo_giro_il": lavoratore.get("giro_il"),
            "detentore": lavoratore.get("detentore") if perimetro.conservatore else None,
            "confronto_foglia": lavoratore.get("fonte_foglie") or "NON_DISPONIBILE",
        },
    }


@router.get("/verifiche/{richiesta_id}", response_model=StatoRichiesta,
            summary="Stato di una verifica su richiesta")
async def stato_richiesta(
    richiesta_id: uuid.UUID = Path(..., description="Id restituito da POST /verifiche"),
    utente: dict[str, Any] = Depends(utente_verificato),
) -> dict[str, Any]:
    perimetro = _perimetro(utente)
    try:
        stato = await repository.leggi_richiesta(richiesta_id)
    except SQLAlchemyError as exc:
        raise _db(exc) from None
    if stato is None or not perimetro.vede_richiesta(stato["ente"]):
        raise ErroreApi("INT-404", "Richiesta di verifica non trovata")
    return stato
