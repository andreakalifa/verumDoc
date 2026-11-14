# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/api/webhook.py
Sottoscrizioni webhook dell'ente e coda di scarto delle consegne — contratto C3.

  POST   /api/v1/webhook                                   crea (segreto mostrato una volta)
  GET    /api/v1/webhook                                   elenco dell'ente
  GET    /api/v1/webhook/tipi                              catalogo dei tipi sottoscrivibili (R05)
  GET    /api/v1/webhook/{id}                              dettaglio
  PATCH  /api/v1/webhook/{id}                              url, tipi, descrizione, attiva
  DELETE /api/v1/webhook/{id}                              elimina (consegne in sospeso annullate)
  GET    /api/v1/webhook/{id}/consegne?stato=FALLITA       consegne (coda di scarto) a cursore
  POST   /api/v1/webhook/{id}/consegne/{cid}/riprova       ripete una consegna FALLITA
  POST   /api/v1/webhook/{id}/segreto/ruota                R13b: nuovo segreto, doppia firma
                                                           nel periodo di transizione
  POST   /api/v1/webhook/{id}/segreto/chiudi-transizione   R13b: ritira subito il precedente

Solo l'amministratore dell'ente (ADMIN_TENANT). Ogni id dell'URL si cerca
dentro l'ente dell'identità: le risorse di altri enti sono 404 (REGOLE §14.4).
Il router traduce soltanto: regole in app/application/webhook_service.py.

R04 servizi (VD-C-106): ``POST`` accetta ``Idempotency-Key`` con la semantica
del versamento (``libs.shared.security.idempotenza``): la stessa richiesta
ripetuta entro la validità della chiave restituisce LA STESSA sottoscrizione
(200, intestazione ``Idempotent-Replayed``) con il suo segreto, decifrato
dalla riga (mai salvato in chiaro altrove); corpo diverso → 409 IDEM-409. La
chiave si completa subito dopo la creazione: un processo che muore fra le due
scritture lascia la chiave IN_CORSO, ripresa dopo
``IDEMPOTENZA_IN_CORSO_MAX_SECONDI`` (in quel caso raro può nascere un secondo
webhook, visibile nell'elenco: limite dichiarato nel rapporto R04).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.autenticazione import amministratore_webhook
from app.api.limiti import limite_webhook
from app.api.modelli import (
    CatalogoTipi,
    Consegna,
    ConsegnaRiaccodata,
    ElencoConsegne,
    ElencoSottoscrizioni,
    ModificaSottoscrizione,
    NuovaSottoscrizione,
    RotazioneSegreto,
    SegretoRuotato,
    Sottoscrizione,
    SottoscrizioneCreata,
    TipoSottoscrivibile,
)
from app.application import webhook_service
from app.application.configurazione import cifratore_obbligatorio, regole_webhook
from app.config import settings
from app.domain.eventi import TIPI_EVENTO
from app.domain.identita import Identita
from app.infrastructure.cifratura import Cifratore, contesto_segreto
from app.infrastructure.repo_webhook import RepositoryWebhook
from app.infrastructure.repo_webhook import Sottoscrizione as RigaSottoscrizione
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.security import idempotenza

router = APIRouter(prefix="/api/v1/webhook", tags=["Webhook"], responses=RISPOSTE_ERRORE,
                   # R03 f2 (REV-03): limite di richieste al minuto del piano
                   dependencies=[Depends(limite_webhook)])

#: Massimo di un bigint PostgreSQL: oltre, l'id non può esistere (mai un errore del DB)
ID_MASSIMO = 2**63 - 1
#: Stessi valori di app.domain.webhook.STATI_CONSEGNA (verificato da un test)
StatoConsegna = Literal["IN_CODA", "IN_CONSEGNA", "CONSEGNATA", "FALLITA", "ANNULLATA"]


def _repo() -> RepositoryWebhook:
    return RepositoryWebhook()


def _guasto(exc: SQLAlchemyError, evento: str, identita: Identita) -> ErroreApi:
    return errore_registrato("WH-503", exc, evento=evento, tenant_id=identita.tenant_id)


def _uuid(valore: str) -> str:
    """Id non UUID = risorsa inesistente (404, mai 422 né 500)."""
    try:
        return str(UUID(valore))
    except ValueError:
        raise ErroreApi("WH-404") from None


def _vista(s: RigaSottoscrizione) -> Sottoscrizione:
    return Sottoscrizione(id=s.id, url=s.url, tipi=s.tipi, descrizione=s.descrizione,
                          attiva=s.attiva, segreto_suffisso=s.segreto_suffisso,
                          creata_il=s.creata_il, aggiornata_il=s.aggiornata_il,
                          segreto_precedente_suffisso=s.segreto_precedente_suffisso,
                          transizione_fino_al=s.transizione_fino_al,
                          segreto_ruotato_il=s.segreto_ruotato_il)


@router.post("", status_code=201, response_model=SottoscrizioneCreata,
             summary="Crea un webhook (il segreto si vede solo ora)",
             responses={200: {"model": SottoscrizioneCreata,
                              "description": "Stessa richiesta ripetuta (stessa Idempotency-Key)"},
                        **RISPOSTE_ERRORE})
async def crea_webhook(
    corpo: NuovaSottoscrizione,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key",
        description="Facoltativa (R04, VD-C-106): 8-200 caratteri [A-Za-z0-9._:-]. Stessa "
                    "chiave e stessa richiesta → stessa sottoscrizione (200); corpo diverso → "
                    "409 IDEM-409"),
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> SottoscrizioneCreata:
    cifratore = cifratore_obbligatorio()
    chiave = idempotenza.valida_chiave(idempotency_key)
    prenotazione: idempotenza.Prenotazione | None = None
    if chiave is not None:
        impronta = idempotenza.impronta_richiesta({"corpo": corpo.model_dump(mode="json"),
                                                   "utente": identita.user_id})
        try:
            prenotazione = await idempotenza.prenota(
                identita.tenant_id, idempotenza.AMBITO_WEBHOOK, chiave, impronta,
                adesso=datetime.now(UTC))
            if prenotazione.ripetuta and prenotazione.risorsa_id:
                ripetuta = await _sottoscrizione_ripetuta(identita, prenotazione.risorsa_id,
                                                          repo=repo, cifratore=cifratore)
                response.status_code = 200
                response.headers["Idempotent-Replayed"] = "true"
                return ripetuta
        except SQLAlchemyError as exc:
            raise _guasto(exc, "webhook_crea_idempotenza", identita) from None
    completa_chiave = None
    if prenotazione is not None:
        chiave_prenotata = prenotazione

        async def completa_chiave(sessione, id_creato: str) -> None:
            # R04 (richiesta di r04-servizi): chiave COMPLETATA nella STESSA
            # transazione del webhook. Prima erano due transazioni: un arresto fra
            # le due lasciava la chiave IN_CORSO e dopo 120 s una ripetizione
            # creava un secondo webhook.
            await idempotenza.completa(sessione, chiave_prenotata, id_creato,
                                       adesso=datetime.now(UTC))
    try:
        creata, segreto = await webhook_service.crea(
            identita, url=corpo.url, tipi=corpo.tipi, descrizione=corpo.descrizione,
            attiva=corpo.attiva, repo=repo, cifratore=cifratore,
            regole=regole_webhook(settings), nella_transazione=completa_chiave)
    except BaseException as exc:
        if prenotazione is not None:
            await idempotenza.rilascia(prenotazione)
        if isinstance(exc, SQLAlchemyError):
            raise _guasto(exc, "webhook_crea", identita) from None
        raise
    return SottoscrizioneCreata(**_vista(creata).model_dump(), segreto=segreto)


async def _sottoscrizione_ripetuta(identita: Identita, id_: str, *, repo: RepositoryWebhook,
                                   cifratore: Cifratore) -> SottoscrizioneCreata:
    """La sottoscrizione creata con la stessa chiave, con il suo segreto (decifrato).
    Eliminata nel frattempo: 404 WH-404 (il segreto non esiste più)."""
    riga = await repo.leggi(identita.tenant_id, id_)
    if riga is None:
        raise ErroreApi("WH-404", "Il webhook creato con questa Idempotency-Key è stato eliminato")
    async with get_async_session() as session:
        cifrato = (await session.execute(text(
            "SELECT segreto_cifrato FROM public.webhook_sottoscrizioni "
            "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid)"),
            {"id": id_, "t": identita.tenant_id})).scalar_one()
    segreto = cifratore.decifra(cifrato, contesto_segreto(identita.tenant_id, id_))
    return SottoscrizioneCreata(**_vista(riga).model_dump(), segreto=segreto)


@router.get("", response_model=ElencoSottoscrizioni, summary="Webhook dell'ente")
async def elenco_webhook(
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> ElencoSottoscrizioni:
    try:
        righe = await repo.elenca(identita.tenant_id)
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_elenco", identita) from None
    return ElencoSottoscrizioni(sottoscrizioni=[_vista(r) for r in righe])


@router.get("/tipi", response_model=CatalogoTipi, summary="Tipi di evento sottoscrivibili")
async def tipi_evento(
    identita: Identita = Depends(amministratore_webhook),  # noqa: ARG001 — solo autorizzazione
) -> CatalogoTipi:
    """Catalogo chiuso (``app.domain.eventi.TIPI_EVENTO``) in ordine stabile: la
    console lo legge da qui invece di copiarlo. Prima di ``/{webhook_id}``."""
    return CatalogoTipi(tipi=[
        TipoSottoscrivibile(tipo=t.tipo, risorsa=t.risorsa_tipo, descrizione=t.descrizione)
        for t in sorted(TIPI_EVENTO.values(), key=lambda t: t.tipo)])


@router.get("/{webhook_id}", response_model=Sottoscrizione, summary="Dettaglio di un webhook")
async def dettaglio_webhook(
    webhook_id: str,
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> Sottoscrizione:
    try:
        riga = await repo.leggi(identita.tenant_id, _uuid(webhook_id))
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_dettaglio", identita) from None
    if riga is None:
        raise ErroreApi("WH-404")
    return _vista(riga)


@router.patch("/{webhook_id}", response_model=Sottoscrizione, summary="Modifica un webhook")
async def modifica_webhook(
    webhook_id: str,
    corpo: ModificaSottoscrizione,
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> Sottoscrizione:
    try:
        dopo = await webhook_service.modifica(
            identita, _uuid(webhook_id), corpo.model_dump(exclude_unset=True), repo=repo,
            regole=regole_webhook(settings))
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_modifica", identita) from None
    return _vista(dopo)


@router.delete("/{webhook_id}", status_code=204, response_class=Response,
               summary="Elimina un webhook")
async def elimina_webhook(
    webhook_id: str,
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> Response:
    try:
        await webhook_service.elimina(identita, _uuid(webhook_id), repo=repo)
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_elimina", identita) from None
    return Response(status_code=204)


@router.get("/{webhook_id}/consegne", response_model=ElencoConsegne,
            summary="Consegne di un webhook (stato=FALLITA: coda di scarto)")
async def consegne_webhook(
    webhook_id: str,
    stato: StatoConsegna | None = Query(None, description="Filtra per stato"),
    dopo: int = Query(0, ge=0, le=ID_MASSIMO,
                      description="Cursore: id dell'ultima consegna già letta"),
    limite: int = Query(settings.eventi_limite_predefinito, ge=1,
                        le=settings.eventi_limite_massimo),
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> ElencoConsegne:
    id_ = _uuid(webhook_id)
    try:
        if await repo.leggi(identita.tenant_id, id_) is None:
            raise ErroreApi("WH-404")
        righe, altri = await repo.elenca_consegne(identita.tenant_id, id_, dopo=dopo,
                                                  limite=limite, stato=stato)
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_consegne", identita) from None
    return ElencoConsegne(consegne=[Consegna(**r) for r in righe],
                          cursore=righe[-1]["id"] if righe else dopo, altri=altri)


@router.post("/{webhook_id}/consegne/{consegna_id}/riprova", status_code=202,
             response_model=ConsegnaRiaccodata, summary="Ripete una consegna FALLITA")
async def riprova_consegna(
    webhook_id: str,
    consegna_id: int,
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> ConsegnaRiaccodata:
    if not 1 <= consegna_id <= ID_MASSIMO:
        raise ErroreApi("WH-010")
    try:
        dati = await webhook_service.riprova(identita, _uuid(webhook_id), consegna_id, repo=repo)
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_riprova", identita) from None
    return ConsegnaRiaccodata(**dati)


@router.post("/{webhook_id}/segreto/ruota", response_model=SegretoRuotato,
             summary="Ruota il segreto HMAC (il nuovo si vede solo ora)")
async def ruota_segreto(
    webhook_id: str,
    corpo: RotazioneSegreto,
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> SegretoRuotato:
    """R13b: il segreto attuale resta valido per ``transizione_ore`` (consegne con due
    firme ``v1``: nuova e precedente), poi vale solo il nuovo."""
    cifratore = cifratore_obbligatorio()
    try:
        ruotata, segreto = await webhook_service.ruota_segreto(
            identita, _uuid(webhook_id), transizione_ore=corpo.transizione_ore, repo=repo,
            cifratore=cifratore)
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_ruota", identita) from None
    return SegretoRuotato(**_vista(ruotata).model_dump(), segreto=segreto)


@router.post("/{webhook_id}/segreto/chiudi-transizione", response_model=Sottoscrizione,
             summary="Ritira subito il segreto precedente")
async def chiudi_transizione(
    webhook_id: str,
    identita: Identita = Depends(amministratore_webhook),
    repo: RepositoryWebhook = Depends(_repo),
) -> Sottoscrizione:
    try:
        s = await webhook_service.chiudi_transizione(identita, _uuid(webhook_id), repo=repo)
    except SQLAlchemyError as exc:
        raise _guasto(exc, "webhook_chiudi_transizione", identita) from None
    return _vista(s)
