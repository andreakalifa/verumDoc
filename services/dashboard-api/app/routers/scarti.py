# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/routers/scarti.py — code di scarto (VD-C-075, R04 servizi).

  GET  /api/dashboard/scarti                       elenco a cursore (filtri)
  GET  /api/dashboard/scarti/code                  code lette, con i messaggi da gestire
  GET  /api/dashboard/scarti/{id}                  dettaglio e valutazione di «Rielabora»
  POST /api/dashboard/scarti/rielabora/anteprima   piano (non scrive)
  POST /api/dashboard/scarti/rielabora/applica     applica il piano confermato (impronta)
  POST /api/dashboard/scarti/{id}/archivia         archivia con motivo

Coda ``outbox_chiavi_ferme`` (R04 correzioni): eventi dell'outbox BLOCCATI dopo
``OUTBOX_MAX_TENTATIVI`` errori dell'evento, di qualunque servizio. «Rielabora»
li RITENTA (la chiave si sblocca); «Archivia» li SCARTA (mai pubblicati) e fa
ripartire gli eventi successivi della stessa chiave (ordine interrotto solo per
quella chiave, registrato nell'audit).

Solo SUPER_ADMIN e RSC_CONSERVATORE (personale del conservatore: vede metadati
tecnici di tutti gli enti, mai i contenuti, REGOLE §19.9). Ogni azione ha un
motivo (≥ 10 caratteri, §15.7) e una voce di audit nella stessa transazione.
Regole in ``app/services/scarti.py``: il router traduce soltanto.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.config import settings
from app.errori import errore_db
from app.services import scarti as servizio
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi
from libs.shared.security.revoca import dipendenza_non_revocato

#: Ruoli del conservatore che gestiscono le code di scarto.
RUOLI_SCARTI = ("SUPER_ADMIN", "RSC_CONSERVATORE")

router = APIRouter(prefix="/api/dashboard/scarti", tags=["Code di scarto"],
                   responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 Depends(limite_richieste)])

StatoScarto = Literal["DA_GESTIRE", "RIELABORATO", "ARCHIVIATO"]


async def conservatore(user: dict = Depends(utente_corrente)) -> dict:
    """Dipendenza: solo il personale del conservatore (token utente, mai chiave API)."""
    if user.get("ruolo") not in RUOLI_SCARTI or user.get("auth_method") == "api_key":
        raise ErroreApi("AUTH-010", "Le code di scarto sono riservate al responsabile del "
                                    "servizio di conservazione e all'amministratore")
    return user


# ── Modelli ──────────────────────────────────────────────────────────

class Scarto(BaseModel):
    id: int
    coda: str = Field(..., description="Coda di scarto (topic DLQ o tabella)")
    servizio: str = Field(..., description="Servizio che ha scartato il messaggio")
    fonte: Literal["KAFKA", "TABELLA"]
    topic_origine: str | None = None
    stato: StatoScarto
    codice: str | None = None
    motivo: str
    tentativi: int
    scartato_il: str | None = None
    registrato_il: str | None = None
    tenant_id: str | None = None
    ente: str | None = Field(None, description="Ragione sociale dell'ente, se esiste")
    pdv_id: str | None = None
    aip_id: str | None = None
    gestito_il: str | None = None
    gestito_da: str | None = None
    motivo_gestione: str | None = None
    rielaborazioni: int
    forma: str = Field(..., description="Forma riconosciuta del messaggio")
    azioni_consentite: list[str] = Field(default_factory=list,
                                         description="Azioni possibili ora (REGOLE §9.6)")


class ElencoScarti(BaseModel):
    items: list[Scarto]
    limit: int
    cursore: str | None = Field(None, description="Cursore della pagina successiva")
    altri: bool


class RiepilogoMessaggio(BaseModel):
    partizione: int | None = None
    posizione: int | None = None
    campi: list[str]
    campi_evento: list[str]
    dimensione_byte: int
    leggibile: bool


class ElementoPiano(BaseModel):
    scarto_id: int
    coda: str
    forma: str
    azione: str | None = None
    rielaborabile: bool
    motivo_blocco: str | None = None
    avvisi: list[str] = Field(default_factory=list)
    pdv_id: str | None = None
    tenant_id: str | None = None


class DettaglioScarto(Scarto):
    messaggio: RiepilogoMessaggio
    valutazione: ElementoPiano = Field(..., description="Esito di «Rielabora» se eseguito ora")


class CodaScarto(BaseModel):
    coda: str
    servizio: str
    da_gestire: int = Field(..., description="Messaggi da gestire (fino a `tetto`)")
    tetto: int


class ElencoCode(BaseModel):
    code: list[CodaScarto]


class FiltriPiano(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coda: str | None = Field(None, max_length=100)
    servizio: str | None = Field(None, max_length=50)
    tenant_id: str | None = Field(None, max_length=36)
    pdv_id: str | None = Field(None, max_length=36)


class RichiestaPiano(BaseModel):
    """Selezione: ``ids`` (messaggi indicati) oppure ``filtri`` (messaggi DA_GESTIRE)."""

    model_config = ConfigDict(extra="forbid")

    ids: list[int] | None = Field(None, min_length=1, max_length=10_000)
    filtri: FiltriPiano | None = None

    @model_validator(mode="after")
    def _selezione(self) -> RichiestaPiano:
        if bool(self.ids) == (self.filtri is not None):
            raise ValueError("indicare `ids` oppure `filtri` (non entrambi)")
        return self


class RichiestaApplica(RichiestaPiano):
    impronta_piano: str = Field(..., pattern=r"^[0-9a-f]{64}$",
                                description="Impronta del piano confermato (anteprima)")
    motivo: str = Field(..., min_length=10, max_length=1000,
                        description="Motivo della rielaborazione (REGOLE §15.7)")


class Piano(BaseModel):
    elementi: list[ElementoPiano]
    rielaborabili: int
    bloccati: int
    avvisi: list[str]
    troncato: bool
    chi_sara_avvisato: str
    impronta_piano: str


class EsitoApplica(BaseModel):
    rielaborati: int
    bloccati: int
    impronta_piano: str
    elementi: list[ElementoPiano]


class RichiestaArchivia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(..., min_length=10, max_length=1000,
                        description="Perché il messaggio non si rielabora (REGOLE §15.7)")


# ── Utilità ──────────────────────────────────────────────────────────

def _uuid_o_422(valore: str | None, nome: str) -> str | None:
    if valore is None or valore == "":
        return None
    canonico = servizio.uuid_o_none(valore)
    if canonico is None:
        raise ErroreApi("DSH-422", f"{nome} non è un identificativo valido")
    return canonico


def _filtri(f: FiltriPiano | None) -> servizio.Filtri | None:
    if f is None:
        return None
    return servizio.Filtri(coda=f.coda, servizio=f.servizio,
                           tenant_id=_uuid_o_422(f.tenant_id, "tenant_id"),
                           pdv_id=_uuid_o_422(f.pdv_id, "pdv_id"))


def _controlla_ids(ids: list[int] | None) -> None:
    if ids and (len(ids) > settings.scarti_rielabora_max or any(i < 1 for i in ids)):
        raise ErroreApi("DSH-422-PIANO", f"Da 1 a {settings.scarti_rielabora_max} messaggi "
                                         "con identificativi positivi")


# ── Rotte ────────────────────────────────────────────────────────────

@router.get("", response_model=ElencoScarti, summary="Messaggi delle code di scarto")
async def elenco_scarti(
    stato: StatoScarto | None = Query(None, description="Stato; vuoto: tutti"),
    coda: str | None = Query(None, max_length=100),
    servizio_: str | None = Query(None, alias="servizio", max_length=50),
    tenant_id: str | None = Query(None, max_length=36),
    pdv_id: str | None = Query(None, max_length=36),
    cursore: str | None = Query(None, max_length=200),
    limit: int = Query(30, ge=1, le=500),
    _user: dict = Depends(conservatore),
) -> ElencoScarti:
    """Dal più recente, a cursore (niente OFFSET né COUNT)."""
    filtri = servizio.Filtri(stato=stato, coda=coda, servizio=servizio_,
                             tenant_id=_uuid_o_422(tenant_id, "tenant_id"),
                             pdv_id=_uuid_o_422(pdv_id, "pdv_id"))
    try:
        async with get_async_session() as session:
            pagina = await servizio.elenco(session, filtri, cursore=cursore,
                                           limite=min(limit, settings.scarti_limite_massimo))
    except servizio.CursoreNonValido:
        raise ErroreApi("DSH-104") from None
    except SQLAlchemyError as exc:
        raise errore_db("lettura delle code di scarto", exc, codice="DSH-503") from None
    return ElencoScarti(**pagina)


#: Tetto del conteggio per coda (la tabella degli scarti è piccola, ma il conteggio
#: resta limitato anche se una coda esplodesse).
TETTO_CONTEGGIO = 1000


@router.get("/code", response_model=ElencoCode, summary="Code di scarto lette")
async def code_scarto(_user: dict = Depends(conservatore)) -> ElencoCode:
    code = {**settings.scarti_code_kafka,
            servizio.CODA_INDICIZZAZIONE: servizio.SERVIZIO_INDICIZZAZIONE,
            servizio.CODA_OUTBOX: servizio.SERVIZIO_OUTBOX}
    try:
        async with get_async_session() as session:
            risultato = []
            for nome, serv in code.items():
                n = (await session.execute(text(
                    "SELECT count(*) FROM (SELECT 1 FROM public.scarti WHERE coda = :c "
                    " AND stato = 'DA_GESTIRE' LIMIT :t) AS x"),
                    {"c": nome, "t": TETTO_CONTEGGIO})).scalar_one()
                risultato.append(CodaScarto(coda=nome, servizio=serv, da_gestire=int(n),
                                            tetto=TETTO_CONTEGGIO))
    except SQLAlchemyError as exc:
        raise errore_db("lettura delle code di scarto", exc, codice="DSH-503") from None
    return ElencoCode(code=risultato)


@router.get("/{scarto_id}", response_model=DettaglioScarto, summary="Dettaglio di un messaggio")
async def dettaglio_scarto(scarto_id: int, _user: dict = Depends(conservatore)
                           ) -> DettaglioScarto:
    if not 1 <= scarto_id <= 2**63 - 1:
        raise ErroreApi("DSH-404-SCARTO")
    try:
        async with get_async_session() as session:
            riga = await servizio.leggi(session, scarto_id)
            if riga is None:
                raise ErroreApi("DSH-404-SCARTO")
            piano = await servizio.calcola_piano(session, ids=[scarto_id], filtri=None,
                                                 adesso=datetime.now(UTC), blocca=False)
    except SQLAlchemyError as exc:
        raise errore_db("lettura delle code di scarto", exc, codice="DSH-503") from None
    return DettaglioScarto(**servizio.vista(riga, messaggio=True),
                           valutazione=ElementoPiano(**piano["elementi"][0].pubblico()))


@router.post("/rielabora/anteprima", response_model=Piano,
             summary="Anteprima di «Rielabora» (non scrive)")
async def anteprima_rielabora(corpo: RichiestaPiano, _user: dict = Depends(conservatore)
                              ) -> Piano:
    _controlla_ids(corpo.ids)
    try:
        async with get_async_session() as session:
            piano = await servizio.calcola_piano(session, ids=corpo.ids,
                                                 filtri=_filtri(corpo.filtri),
                                                 adesso=datetime.now(UTC), blocca=False)
            await session.rollback()  # anteprima: nessuna scrittura, nemmeno per errore
    except SQLAlchemyError as exc:
        raise errore_db("calcolo del piano di rielaborazione", exc, codice="DSH-503") from None
    return Piano(**servizio.piano_pubblico(piano))


@router.post("/rielabora/applica", response_model=EsitoApplica,
             summary="Applica «Rielabora» (piano confermato)")
async def applica_rielabora(corpo: RichiestaApplica, user: dict = Depends(conservatore)
                            ) -> EsitoApplica:
    """Ricalcola il piano sotto lock: se l'impronta non coincide 409 DSH-409-PIANO
    con il piano aggiornato in ``dettagli.piano`` e nulla è applicato."""
    _controlla_ids(corpo.ids)
    try:
        async with get_async_session() as session:
            esito = await servizio.applica(session, ids=corpo.ids, filtri=_filtri(corpo.filtri),
                                           impronta=corpo.impronta_piano, motivo=corpo.motivo,
                                           utente=user, adesso=datetime.now(UTC))
    except servizio.PianoCambiato as cambio:
        raise ErroreApi("DSH-409-PIANO", dettagli={
            "piano": _json(servizio.piano_pubblico(cambio.piano))}) from None
    except SQLAlchemyError as exc:
        raise errore_db("rielaborazione dei messaggi scartati", exc, codice="DSH-503") from None
    return EsitoApplica(**esito)


@router.post("/{scarto_id}/archivia", response_model=DettaglioScarto,
             summary="Archivia un messaggio scartato con motivo")
async def archivia_scarto(scarto_id: int, corpo: RichiestaArchivia,
                          user: dict = Depends(conservatore)) -> DettaglioScarto:
    if not 1 <= scarto_id <= 2**63 - 1:
        raise ErroreApi("DSH-404-SCARTO")
    adesso = datetime.now(UTC)
    try:
        async with get_async_session() as session:
            vista = await servizio.archivia(session, scarto_id, motivo=corpo.motivo, utente=user,
                                            adesso=adesso)
            piano = await servizio.calcola_piano(session, ids=[scarto_id], filtri=None,
                                                 adesso=adesso, blocca=False)
    except SQLAlchemyError as exc:
        raise errore_db("archiviazione del messaggio scartato", exc, codice="DSH-503") from None
    return DettaglioScarto(**vista, valutazione=ElementoPiano(**piano["elementi"][0].pubblico()))


def _json(valore: Any) -> Any:
    """Dati serializzabili per ``dettagli`` del problema."""
    import json

    return json.loads(json.dumps(valore, default=str))
