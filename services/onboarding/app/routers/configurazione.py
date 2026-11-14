# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/app/routers/configurazione.py — API della configurazione
(contratto R05 Q1, prefisso ``/api/v1/configurazione``).

Router sottile (REGOLE §7.4): parsing, autenticazione, risposta. Regole,
permessi e piano stanno nel motore (``libs/verumdoc_rules``), i casi d'uso in
``app/services/configurazione.py``. Errori dal catalogo (famiglie CFG e RGL,
problem+json).

Chi può fare cosa (contratto Q1; decisione da confermare per le classi di base,
vedi rapporto r05-regole):
  - lettura di impostazioni, regole e profili: conservatore (SUPER_ADMIN,
    RSC_CONSERVATORE) per ogni ente; ADMIN_TENANT, OPERATORE e VIEWER del proprio;
  - bozze, anteprima, applicazione: SUPER_ADMIN e RSC_CONSERVATORE ai livelli
    CONSERVATORE ed ENTE (SUPER_ADMIN anche le classi di base); ADMIN_TENANT ai
    livelli ENTE, CLASSE, ACCORDO del proprio ente, per voci non bloccate;
  - proposte: le vede il conservatore e l'ente; le decide l'ADMIN_TENANT dell'ente;
  - validatore preventivo e profilo esportato: anche con chiave API.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.routers.configurazione_auth import attore_jwt, attore_jwt_o_chiave
from app.services import configurazione as servizio
from libs.shared.errori import RISPOSTE_ERRORE
from libs.verumdoc_rules.configurazione import MOTIVO_MINIMO, Attore, Risorsa

router = APIRouter(prefix="/api/v1/configurazione", tags=["Configurazione"],
                   responses=RISPOSTE_ERRORE)

Utente = Annotated[Attore, Depends(attore_jwt)]
UtenteOChiave = Annotated[Attore, Depends(attore_jwt_o_chiave)]
Limite = Annotated[int, Query(ge=1, le=servizio.LIMITE_MASSIMO,
                              description="Elementi per pagina")]
Cursore = Annotated[str | None, Query(max_length=500,
                                      description="Cursore restituito dalla pagina precedente")]
LivelloQ = Annotated[str | None, Query(
    pattern="^(CONSERVATORE|ENTE|CLASSE|ACCORDO)$",
    description="Livello da cui guardare (predefinito: il più specifico indicato)")]


# ── Modelli ─────────────────────────────────────────────────────────

class AmbitoBozza(BaseModel):
    model_config = ConfigDict(extra="forbid")

    livello: Literal["CONSERVATORE", "ENTE", "CLASSE", "ACCORDO", "PDV"]
    ente: str | None = Field(None, description="Ente (uuid); per ADMIN_TENANT il proprio")
    classe: str | None = Field(None, description="Classe documentale (uuid o codice)")
    accordo: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{1,50}$",
                                description="Codice dell'accordo di versamento")


class PropostaBozza(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enti: list[str] | None = Field(
        None, description="Enti destinatari (uuid); assente = tutti gli enti attivi")


class BozzaCrea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risorsa: Risorsa
    ambito: AmbitoBozza
    modifiche: list[dict[str, Any]] = Field(
        ..., min_length=1, max_length=200,
        description=("impostazioni/quote/classe/accordo: {codice, valore} o {codice, "
                     "eredita: true}; regole: {regola, effetto}; accordo anche {campo: "
                     "descrizione|cessato, valore}; profilo: [{tipologia?, campi, "
                     "descrizione?}]"))
    motivo: str = Field(..., min_length=MOTIVO_MINIMO, max_length=2000)
    proposta: PropostaBozza | None = Field(
        None, description="Solo livello CONSERVATORE: proporre agli enti invece di applicare "
                          "(REGOLE §8.13)")


class BozzaAggiorna(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versione: int = Field(..., ge=1, description="Versione letta (409 CFG-410 se cambiata)")
    modifiche: list[dict[str, Any]] | None = Field(None, min_length=1, max_length=200)
    motivo: str | None = Field(None, min_length=MOTIVO_MINIMO, max_length=2000)
    proposta: PropostaBozza | None = None


class Applica(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impronta_piano: str = Field(..., pattern="^[0-9a-f]{64}$",
                                description="Impronta del piano restituita dall'anteprima")
    valido_dal: date | None = Field(
        None, description="Decorrenza (giorno italiano) di accordi e profili; oggi se assente")


class Risposta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(..., min_length=MOTIVO_MINIMO, max_length=2000)


class ImprontaFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impronta: str | None = Field(None, max_length=128)
    algoritmo: str = Field("SHA-256", max_length=20)
    dimensione: int | None = Field(None, ge=0, description="Byte")
    formato: str | None = Field(None, max_length=255,
                                description="MIME (il servizio lo riconoscerà dal contenuto)")


class Valida(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indice: Any = Field(..., description="Indice del versamento (stesso JSON di /versamento/ud)")
    file_impronte: dict[str, ImprontaFile] | None = Field(
        None, description="Per nome file: impronta, dimensione, formato")
    ente: str | None = Field(None, description="Solo per il conservatore: ente da simulare")


# ── Lettura ─────────────────────────────────────────────────────────

@router.get("/impostazioni", summary="Impostazioni effettive con origine")
async def impostazioni(attore: Utente, livello: LivelloQ = None, ente: str | None = None,
                       classe: str | None = None, accordo: str | None = None,
                       gruppo: str | None = Query(None, max_length=50),
                       limit: Limite = 100, cursore: Cursore = None) -> dict[str, Any]:
    """Elenco a cursore: ``{codice, valore, origine, tipo, locked, norm_ref,
    descrizione, ambito_ammesso, …, azioni_consentite}`` nel contesto indicato."""
    return await servizio.elenco_impostazioni(
        attore, livello=livello, ente=ente, classe=classe, accordo=accordo, gruppo=gruppo,
        limit=limit, cursore=cursore, adesso=servizio.adesso_utc())


@router.get("/regole", summary="Catalogo delle regole con l'effetto effettivo")
async def regole(attore: Utente, livello: LivelloQ = None, ente: str | None = None,
                 classe: str | None = None, accordo: str | None = None,
                 limit: Limite = 100, cursore: Cursore = None) -> dict[str, Any]:
    return await servizio.elenco_regole(
        attore, livello=livello, ente=ente, classe=classe, accordo=accordo, limit=limit,
        cursore=cursore, adesso=servizio.adesso_utc())


@router.get("/formati", summary="Catalogo base dei formati (PUID, MIME, esito predefinito)")
async def formati(attore: Utente) -> dict[str, Any]:
    """R08 (D02): famiglie dell'Allegato 2 con identificativi PRONOM e MIME;
    esiti per livello in ``/impostazioni?gruppo=formati``."""
    return servizio.catalogo_formati(attore)


@router.get("/profili", summary="Profili di metadati per tipologia e per classe")
async def profili(attore: Utente, ente: str | None = None, limit: Limite = 100,
                  cursore: Cursore = None) -> dict[str, Any]:
    return await servizio.elenco_profili(attore, ente=ente, limit=limit, cursore=cursore,
                                         adesso=servizio.adesso_utc())


@router.get("/profili/{profilo_id}/versioni", summary="Versioni di un profilo")
async def versioni_profilo(profilo_id: str, attore: Utente) -> dict[str, Any]:
    """``profilo_id``: ``tipologia:<codice>`` o ``classe:<uuid>``; versioni con
    ``valido_dal``/``valido_al`` e campi (``da_riscontrare`` dove il testo
    ufficiale va verificato)."""
    return await servizio.versioni_profilo(attore, profilo_id)


# ── Bozze ───────────────────────────────────────────────────────────

@router.post("/bozze", status_code=201, summary="Crea una bozza di modifica")
async def crea_bozza(corpo: BozzaCrea, attore: Utente) -> dict[str, Any]:
    return await servizio.crea_bozza(
        attore, risorsa=corpo.risorsa, ambito=corpo.ambito.model_dump(),
        modifiche=corpo.modifiche, motivo=corpo.motivo,
        proposta=corpo.proposta.model_dump() if corpo.proposta else None)


@router.get("/bozze", summary="Bozze (a cursore)")
async def bozze(attore: Utente, stato: str | None = Query(None, max_length=20),
                ente: str | None = None, limit: Limite = 50,
                cursore: Cursore = None) -> dict[str, Any]:
    return await servizio.elenco_bozze(attore, stato=stato, ente=ente, limit=limit,
                                       cursore=cursore)


@router.get("/bozze/{bozza_id}", summary="Dettaglio di una bozza")
async def bozza(bozza_id: str, attore: Utente) -> dict[str, Any]:
    return await servizio.leggi_bozza(attore, bozza_id)


@router.patch("/bozze/{bozza_id}", summary="Aggiorna una bozza (con la versione letta)")
async def aggiorna_bozza(bozza_id: str, corpo: BozzaAggiorna, attore: Utente) -> dict[str, Any]:
    return await servizio.aggiorna_bozza(
        attore, bozza_id, versione=corpo.versione, modifiche=corpo.modifiche,
        motivo=corpo.motivo, proposta=corpo.proposta.model_dump() if corpo.proposta else None)


@router.delete("/bozze/{bozza_id}", status_code=204, summary="Annulla una bozza")
async def annulla_bozza(bozza_id: str, attore: Utente,
                        versione: int | None = Query(None, ge=1)) -> Response:
    await servizio.annulla_bozza(attore, bozza_id, versione=versione)
    return Response(status_code=204)


@router.post("/bozze/{bozza_id}/anteprima", summary="Anteprima: piano e impatto (non scrive)")
async def anteprima(bozza_id: str, attore: Utente) -> dict[str, Any]:
    """``{impronta_piano, modifiche[{codice, prima, dopo, origine_prima, origine_dopo}],
    impatto{…, campione}, bloccati[{codice, motivo}], avvisi}``. Nulla si scrive."""
    return await servizio.anteprima(attore, bozza_id, servizio.adesso_utc())


@router.post("/bozze/{bozza_id}/applica", summary="Applica la bozza (impronta del piano)")
async def applica(bozza_id: str, corpo: Applica, attore: Utente) -> dict[str, Any]:
    """Atomica; 409 CFG-409 (PIANO_CAMBIATO) con il piano nuovo se l'impronta è
    cambiata; 422 CFG-422 se il piano ha elementi bloccati."""
    return await servizio.applica_bozza(attore, bozza_id, impronta_piano=corpo.impronta_piano,
                                        valido_dal=corpo.valido_dal,
                                        adesso=servizio.adesso_utc())


# ── Accordi di versamento (R05: elenco per la console) ──────────────

@router.get("/accordi", summary="Accordi di versamento (ultima versione, a cursore)")
async def accordi(attore: Utente, ente: str | None = None, classe: str | None = None,
                  stato: Literal["ATTIVO", "CESSATO"] | None = None, limit: Limite = 50,
                  cursore: Cursore = None) -> dict[str, Any]:
    return await servizio.elenco_accordi(attore, ente=ente, classe=classe, stato=stato,
                                         limit=limit, cursore=cursore)


# ── Proposte ────────────────────────────────────────────────────────

@router.get("/proposte", summary="Proposte del conservatore agli enti")
async def proposte(attore: Utente, stato: str | None = Query(None, max_length=20),
                   ente: str | None = None, limit: Limite = 50,
                   cursore: Cursore = None) -> dict[str, Any]:
    return await servizio.elenco_proposte(attore, stato=stato, ente=ente, limit=limit,
                                          cursore=cursore)


@router.post("/proposte/{proposta_id}/accetta", summary="Accetta una proposta")
async def accetta(proposta_id: str, corpo: Risposta, attore: Utente) -> dict[str, Any]:
    return await servizio.rispondi_proposta(attore, proposta_id, accetta=True,
                                            motivo=corpo.motivo)


@router.post("/proposte/{proposta_id}/rifiuta", summary="Rifiuta una proposta")
async def rifiuta(proposta_id: str, corpo: Risposta, attore: Utente) -> dict[str, Any]:
    return await servizio.rispondi_proposta(attore, proposta_id, accetta=False,
                                            motivo=corpo.motivo)


# ── Validatore preventivo ───────────────────────────────────────────

@router.post("/valida", summary="Validatore preventivo (stessi rilievi del versamento)")
async def valida(corpo: Valida, attore: UtenteOChiave) -> dict[str, Any]:
    """Nulla si scrive. ``esito`` CONFORME o NON_CONFORME con ``rilievi`` e
    ``avvisi`` nella forma del versamento; ``controlli_non_eseguiti`` dice cosa
    solo il versamento verifica."""
    file_impronte = ({k: v.model_dump() for k, v in corpo.file_impronte.items()}
                     if corpo.file_impronte is not None else None)
    return await servizio.valida(attore, indice=corpo.indice, file_impronte=file_impronte,
                                 ente=corpo.ente, adesso=servizio.adesso_utc())


@router.get("/profilo-validatore", summary="Profilo esportato per il validatore senza rete")
async def profilo_validatore(attore: UtenteOChiave,
                             classe: str = Query(..., min_length=1, max_length=50),
                             accordo: str | None = Query(None, max_length=50),
                             ente: str | None = None) -> dict[str, Any]:
    """Configurazione effettiva di (ente, classe, accordo) da usare con
    ``tools/validatore/valida_pdv.py --profilo``."""
    return await servizio.profilo_esportato(attore, ente=ente, classe=classe, accordo=accordo,
                                            adesso=servizio.adesso_utc())
