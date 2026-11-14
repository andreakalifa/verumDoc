# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/api/pratiche.py — API delle pratiche del ciclo di vita (contratto Q2).

Prefisso ``/api/v1/ciclo-vita`` (ingress: stesso prefisso, nessuna riscrittura).
Il router traduce richiesta e risposta; regole, permessi e transazioni stanno
in ``app.application`` e ``app.domain`` (REGOLE §7.4). Tutti gli errori sono
problem+json del catalogo (famiglia LC).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from fastapi.responses import JSONResponse

from app.api.limiti import limite_ciclo_vita
from app.api.modelli import (
    AllegatoCaricato,
    DettaglioPratica,
    ElencoOggetti,
    ElencoPratiche,
    PianoAnteprima,
    PraticaRiepilogo,
    RichiestaAnnullamento,
    RichiestaApprovazione,
    RichiestaProposta,
    RichiestaRestituzione,
    RichiestaRettifica,
    RichiestaRipresa,
    RichiestaRiversamentoIngresso,
    RichiestaRiversamentoUscita,
    RichiestaTransizione,
    StatoAutomatico,
    TipoPratica,
)
from app.application import pratiche as pr
from app.application import puntuali, riversamento, scarto
from app.config import settings
from app.domain import stati as st
from app.domain.permessi import Identita
from libs.shared.errori import RISPOSTE_ERRORE

router = APIRouter(prefix="/api/v1/ciclo-vita", tags=["Ciclo di vita"],
                   dependencies=[Depends(limite_ciclo_vita)], responses=RISPOSTE_ERRORE)

Chi = Annotated[Identita, Depends(limite_ciclo_vita)]
ChiaveIdempotenza = Annotated[str | None, Header(
    alias="Idempotency-Key", description="Facoltativa: stessa chiave, stessa pratica")]


def _conferma(conferma: Any) -> dict[str, Any] | None:
    """R17 (VD-P-164): conferma firmata dal gateway, se presente."""
    return None if conferma is None else conferma.model_dump()


def _json(corpo: Any, status: int) -> JSONResponse:
    from fastapi.encoders import jsonable_encoder

    return JSONResponse(jsonable_encoder(corpo), status_code=status)


@router.get("/pratiche", response_model=ElencoPratiche, summary="Elenco delle pratiche")
async def elenco_pratiche(
    identita: Chi,
    tipo: TipoPratica | None = Query(None),
    stato: str | None = Query(None, max_length=40),
    ente: str | None = Query(None, description="Solo per i ruoli del conservatore"),
    in_attesa_di: Literal["me"] | None = Query(
        None, description="R06: «me» = solo le pratiche ferme in uno stato in cui decide il "
                          "ruolo di chi chiede (conservatore: eseguire, rendere eseguibile, "
                          "approvare riversamenti; ADMIN_TENANT: proporre e autorizzare scarti "
                          "e restituzioni); per gli altri ruoli l'elenco è vuoto"),
    cursore: str | None = Query(None, max_length=500),
    limite: int = Query(settings.elenco_limite_predefinito, ge=1,
                        le=settings.elenco_limite_massimo),
) -> dict[str, Any]:
    """Pratiche del perimetro dell'utente, dalla più recente, a cursore."""
    return await pr.elenca(identita, tipo=tipo, stato=stato, ente=ente, cursore=cursore,
                           limite=limite, in_attesa=in_attesa_di == "me")


@router.get("/pratiche/{pratica_id}", response_model=DettaglioPratica,
            summary="Dettaglio di una pratica")
async def dettaglio_pratica(pratica_id: str, identita: Chi) -> dict[str, Any]:
    """Cronologia degli stati, autorizzazioni, allegati (con impronta), conteggi
    degli oggetti coinvolti e azioni consentite con il motivo di blocco."""
    return await pr.leggi(identita, pratica_id)


@router.get("/pratiche/{pratica_id}/oggetti", response_model=ElencoOggetti,
            summary="Oggetti coinvolti in una pratica")
async def oggetti_pratica(
    pratica_id: str, identita: Chi,
    esito: str | None = Query(None, max_length=20),
    cursore: str | None = Query(None, max_length=500),
    limite: int = Query(settings.elenco_limite_predefinito, ge=1,
                        le=settings.elenco_limite_massimo),
) -> dict[str, Any]:
    return await pr.elenca_oggetti(identita, pratica_id, esito=esito, cursore=cursore,
                                   limite=limite)


@router.post("/pratiche/{pratica_id}/transizione", response_model=PraticaRiepilogo,
             summary="Transizione di stato con lo stato atteso",
             responses={202: {"description": "Esecuzione richiesta al lavoratore"}})
async def transizione_pratica(pratica_id: str, corpo: RichiestaTransizione,
                              identita: Chi) -> JSONResponse:
    """409 LC-409-STATO-CAMBIATO se lo stato non è quello atteso. Le transizioni
    verso ESEGUITA chiedono l'esecuzione al lavoratore (202)."""
    esito = await pr.transizione(identita, pratica_id, stato_atteso=corpo.stato_atteso,
                                 nuovo_stato=corpo.nuovo_stato, allegati=corpo.allegati,
                                 motivo=corpo.motivo, conferma=_conferma(corpo.conferma))
    return _json(esito, 202 if esito.get("esecuzione_richiesta") else 200)


@router.post("/pratiche/{pratica_id}/allegati", response_model=AllegatoCaricato,
             status_code=201, summary="Carica un allegato della pratica (archiviato nel WORM)")
async def carica_allegato(
    pratica_id: str, identita: Chi,
    file: UploadFile = File(..., description="Documento (autorizzazione firmata, nulla osta…)"),
    tipo: str = Form(..., description="AUTORIZZAZIONE_PRODUTTORE, NULLA_OSTA o ALTRO"),
) -> dict[str, Any]:
    return await pr.carica_allegato(
        identita, pratica_id, tipo=tipo, nome_file=file.filename or "allegato",
        content_type=file.content_type or "application/octet-stream", lettore=file)


@router.post("/pratiche/rettifica", response_model=PraticaRiepilogo, status_code=202,
             summary="Rettifica dei metadati (nuovo PdV collegato all'originale)")
async def rettifica(corpo: RichiestaRettifica, identita: Chi,
                    idempotency_key: ChiaveIdempotenza = None) -> JSONResponse:
    pratica, nuova = await puntuali.rettifica(
        identita, aip_id=corpo.aip_id, pdv_id=corpo.pdv_id, metadati_nuovi=corpo.metadati_nuovi,
        motivo=corpo.motivo, idempotency_key=idempotency_key,
        conferma=_conferma(corpo.conferma))
    return _json(pratica, 202 if nuova else 200)


@router.post("/pratiche/annullamento", response_model=PraticaRiepilogo, status_code=201,
             summary="Annullamento logico di un versamento")
async def annullamento(corpo: RichiestaAnnullamento, identita: Chi,
                       idempotency_key: ChiaveIdempotenza = None) -> JSONResponse:
    pratica, nuova = await puntuali.annulla(
        identita, pdv_id=corpo.pdv_id, motivo=corpo.motivo, idempotency_key=idempotency_key,
        documento_firmato=(corpo.documento_firmato.model_dump()
                           if corpo.documento_firmato else None),
        conferma=_conferma(corpo.conferma))
    return _json(pratica, 201 if nuova else 200)


@router.post("/pratiche/{pratica_id}/approvazione", response_model=PraticaRiepilogo,
             summary="Seconda approvazione di un annullamento (quattro occhi)")
async def approvazione(pratica_id: str, corpo: RichiestaApprovazione,
                       identita: Chi) -> dict[str, Any]:
    """R09 (D23): una persona dell'ente diversa dal richiedente, con un ruolo
    ammesso dall'ente, approva (annullamento eseguito) o respinge (si può
    chiedere di nuovo). 403 LC-403-QUATTRO-OCCHI, 409 LC-409-APPROVAZIONE."""
    return await puntuali.decidi_approvazione(identita, pratica_id,
                                              approva=corpo.esito == "APPROVA",
                                              motivo=corpo.motivo,
                                              conferma=_conferma(corpo.conferma))


@router.post("/pratiche/{pratica_id}/riprendi", response_model=PraticaRiepilogo,
             summary="Riprende una pratica sospesa dopo i tentativi esauriti")
async def riprendi(pratica_id: str, corpo: RichiestaRipresa, identita: Chi) -> dict[str, Any]:
    """Revisione R14 (4, VD-C-133): solo il conservatore (ruolo fisso, token utente,
    permesso mai delegabile ``pratica:riprendi``) rimette il lavoro sospeso di una pratica
    ``LC-409-TENTATIVI-ESAURITI`` con i tentativi azzerati; pratica bloccata, versione
    attesa, audit e avviso all'ente. 403 LC-403-RUOLO, 409 LC-409-NON-SOSPESA o
    LC-409-STATO-CAMBIATO."""
    from app.application import ripresa

    return await ripresa.riprendi(identita, pratica_id, versione=corpo.versione,
                                  motivo=corpo.motivo, conferma=_conferma(corpo.conferma))


@router.get("/conferme", summary="Conferma firmata delle decisioni sensibili: stato")
async def stato_conferme(identita: Chi) -> dict[str, Any]:
    """R17 (VD-P-164): se la conferma firmata dal gateway è attiva (sempre in produzione) e
    quali azioni la richiedono; la console la chiede al gateway prima di decidere."""
    from app.application import conferme

    return {"attiva": conferme.attiva(), "azioni": list(conferme.cd.AZIONI),
            "azioni_creazione": sorted(conferme.cd.AZIONI_CREAZIONE)}


@router.get("/stato-automatico", response_model=StatoAutomatico,
            summary="Lavoro automatico del ciclo di vita e blocchi WORM")
async def stato_automatico(identita: Chi) -> dict[str, Any]:
    """R09 (D04, D06, D20, D21): ultimo giro e esito dei passi periodici,
    pacchetti permanenti e blocchi in scadenza, rinnovi recenti. Solo i ruoli
    del conservatore (dati di tutti gli enti, nessun contenuto)."""
    from app.application import stato_automatico as sa

    return await sa.leggi(identita)


@router.post("/pratiche/scarto/proposta", response_model=PianoAnteprima | PraticaRiepilogo,
             summary="Proposta di scarto: anteprima, poi applica con l'impronta del piano",
             responses={201: {"description": "Pratica di scarto creata (BOZZA)"}})
async def proposta_scarto(corpo: RichiestaProposta, identita: Chi,
                          idempotency_key: ChiaveIdempotenza = None) -> JSONResponse:
    """Senza ``impronta_piano``: anteprima dei PdA selezionabili e dei bloccati
    con il motivo (nessuna scrittura). Con ``impronta_piano``: crea la pratica
    se il piano ricalcolato coincide (altrimenti 409 LC-409-PIANO-CAMBIATO)."""
    criteri = corpo.criteri.model_dump(mode="json")
    if corpo.impronta_piano is None:
        return _json(await scarto.anteprima(identita, ente=corpo.ente, criteri=criteri,
                                            motivo=corpo.motivo), 200)
    pratica, nuova = await scarto.applica(identita, ente=corpo.ente, criteri=criteri,
                                          motivo=corpo.motivo,
                                          impronta_piano=corpo.impronta_piano,
                                          idempotency_key=idempotency_key)
    return _json(pratica, 201 if nuova else 200)


@router.post("/pratiche/riversamento-uscita", response_model=PianoAnteprima | PraticaRiepilogo,
             summary="Riversamento in uscita: PdD MIGRAZIONE con manifesto",
             responses={201: {"description": "Pratica creata"}})
async def riversamento_uscita(corpo: RichiestaRiversamentoUscita, identita: Chi,
                              idempotency_key: ChiaveIdempotenza = None) -> JSONResponse:
    esito, status = await riversamento.uscita(
        identita, tipo=st.RIVERSAMENTO_USCITA, ente=corpo.ente,
        criteri=corpo.criteri.model_dump(mode="json"),
        destinatario=corpo.conservatore_destinatario, motivo=corpo.motivo,
        impronta_piano=corpo.impronta_piano, idempotency_key=idempotency_key)
    return _json(esito, status)


@router.post("/pratiche/restituzione", response_model=PianoAnteprima | PraticaRiepilogo,
             summary="Restituzione di fine contratto (export completo dell'ente)",
             responses={201: {"description": "Pratica creata (BOZZA)"}})
async def restituzione(corpo: RichiestaRestituzione, identita: Chi,
                       idempotency_key: ChiaveIdempotenza = None) -> JSONResponse:
    esito, status = await riversamento.uscita(
        identita, tipo=st.RESTITUZIONE, ente=corpo.ente, criteri=None,
        destinatario=corpo.conservatore_destinatario, motivo=corpo.motivo,
        impronta_piano=corpo.impronta_piano, idempotency_key=idempotency_key)
    return _json(esito, status)


@router.post("/pratiche/riversamento-ingresso", response_model=PraticaRiepilogo,
             status_code=201, summary="Riversamento in ingresso da un altro conservatore")
async def riversamento_ingresso(corpo: RichiestaRiversamentoIngresso, identita: Chi,
                                idempotency_key: ChiaveIdempotenza = None) -> JSONResponse:
    pratica, nuova = await riversamento.ingresso(
        identita, ente=corpo.ente, caricamento_id=corpo.caricamento_id,
        classe=corpo.classe_documentale, conservatore_origine=corpo.conservatore_origine,
        metadati=corpo.metadati, motivo=corpo.motivo, idempotency_key=idempotency_key)
    return _json(pratica, 201 if nuova else 200)
