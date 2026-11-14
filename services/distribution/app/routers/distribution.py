# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Router distribution — Sprint 0 FIX-002 + FIX-006; R03 b4 (VD-C-077, VD-C-093, VD-C-099).
FIX-002: autenticazione verificata (nessun fallback anonimo).
FIX-006: log_audit attivato su richieste distribuzione.
R03 b4: ogni rotta accetta Bearer o chiave API (``app.auth.dipendenze``); PdD
con file reali e impronte verificate (LINK) o ZIP preparato fuori dalla
richiesta, stato in ``GET /pdd/{pdd_id}``; ricerca a cursore con stato
attuale dal database.
R04 servizi: ``Idempotency-Key`` su ``POST /richiedi`` (VD-C-106, stessa
semantica del versamento: ripetizione → stesso PdD con 200, corpo diverso →
409 IDEM-409) e ``/storico`` a cursore senza OFFSET né COUNT (VD-C-102).
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_pdd, limite_ricerca, limite_richieste
from app.models.distribution import (
    AIPSearchRequest,
    AIPSearchResult,
    RichiestaDistribuzione,
    RispostaDistribuzione,
)
from app.services.distribution_engine import collegamenti_aggiornati, esegui_distribuzione
from app.services.registro_pdd import registra_pdd
from app.services.search_index import (
    CursoreNonValido,
    RicercaNonDisponibile,
    search_aip,
    stato_attuale_dal_db,
)
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.outbox import pubblica_dopo_commit
from libs.shared.security import idempotenza
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v2/distribution", tags=["Distribution"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 # R03 f2 (REV-03): limite di richieste al minuto del piano
                                 Depends(limite_richieste)])


# ── Sprint 0 FIX-002: autenticazione obbligatoria ──────────────────
# PRIMA: accettava richieste senza token con tenant_id hardcoded
# DOPO:  richiede JWT Bearer valido con firma RS256

def _get_user(authorization: str | None) -> dict:
    """Verifica JWT Bearer RS256. Nessun fallback anonimo."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401,
            {"type": "AUTH-000",
             "title": "Autenticazione obbligatoria",
             "detail": "Header Authorization: Bearer <token> richiesto"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1]
    return verify_access_token(token)


_RUOLI_CONSERVATORE = ("SUPER_ADMIN", "RSC_CONSERVATORE")


def _stesso_tenant(user: dict, tenant_risorsa: str | None) -> bool:
    """True se la risorsa è del tenant del token verificato (fail-closed, §14.3)."""
    tenant_token = str(user.get("tenant_id") or "").strip().lower()
    tenant_ris = str(tenant_risorsa or "").strip().lower()
    return bool(tenant_token) and bool(tenant_ris) and tenant_token == tenant_ris


def _check_tenant_owns_aip(user: dict, aip_tenant_id: str | None) -> None:
    """
    VD-C-005: l'AIP deve appartenere al tenant del token verificato; i ruoli del
    conservatore vedono i metadati di tutti i clienti (§19.9). Un AIP di un altro
    cliente, o senza cliente, risponde 404 come uno inesistente (§14.4).
    """
    if user.get("ruolo", "") in _RUOLI_CONSERVATORE and aip_tenant_id:
        return
    if not _stesso_tenant(user, aip_tenant_id):
        raise HTTPException(404, {"type": "DST-404", "title": "AIP non trovato"})


def _pdd_visibile(user: dict, pdd_tenant_id: str | None) -> bool:
    """Approvazione/rifiuto: ADMIN_TENANT solo sui PdD del proprio cliente."""
    if user.get("ruolo", "") in _RUOLI_CONSERVATORE:
        return True
    return _stesso_tenant(user, pdd_tenant_id)


CANALI_AMMESSI = ("WEB", "API")


def _errore(stato: int, codice: str, titolo: str, exc: BaseException | None) -> ErroreApi:
    """Errore del catalogo (VD-C-100) con l'id della richiesta: il testo
    dell'eccezione resta nel log (§18)."""
    return errore_registrato(codice, exc, titolo=titolo, status=stato,
                             evento="errore_distribuzione")


def _richiedente(user: dict) -> str:
    """Utente del token verificato: è lui il richiedente registrato (G2)."""
    richiedente = str(user.get("user_id") or user.get("sub") or "").strip()
    if not richiedente:
        raise HTTPException(403, {
            "type": "DST-403",
            "title": "Il token non indica l'utente: richiesta non attribuibile",
        })
    return richiedente


def _motivo_esibizione(user: dict) -> str | None:
    """Chi può chiedere (o rigenerare) un PdD: ruolo personalizzato con il permesso,
    ruolo fisso che lo ha nel modello (``libs/verumdoc_rules/permessi.py``: non la
    Consultazione), sistemi versanti con chiave API (canale delle esibizioni dell'SDK)."""
    from libs.shared.security.permessi import (
        ha_permesso,
        motivo_permesso_mancante,
        ruolo_personalizzato,
    )

    motivo = motivo_permesso_mancante(user, "esibizione:richiedi")
    if motivo or ruolo_personalizzato(user) or user.get("ruolo") == "API_SYSTEM":
        return motivo
    if not ha_permesso(user, "esibizione:richiedi"):
        return "Il tuo profilo consulta l'archivio ma non chiede esibizioni"
    return None


@router.post(
    "/richiedi",
    response_model=RispostaDistribuzione,
    status_code=200,
    summary="Richiedi Pacchetto di Distribuzione",
    responses=RISPOSTE_ERRORE,
)
async def richiedi_pdd(
    req: RichiestaDistribuzione,
    response: Response,
    canale: str | None = Header(default=None, alias="X-VerumDoc-Canale"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key",
        description="Facoltativa (R04, VD-C-106): 8-200 caratteri [A-Za-z0-9._:-]. Stessa chiave "
                    "e stessa richiesta entro IDEMPOTENZA_DURATA_ORE → stesso PdD (200, "
                    "intestazione Idempotent-Replayed); corpo diverso → 409 IDEM-409"),
    user: dict = Depends(limite_pdd),
) -> RispostaDistribuzione:
    """
    Genera un PdD (Pacchetto di Distribuzione) per un AIP dell'ente.

    Consegna LINK (200, stato PRONTO): collegamenti prefirmati ai file reali,
    impronte verificate. Consegna ZIP (200, stato IN_PREPARAZIONE): pacchetto
    preparato fuori dalla richiesta; stato e collegamento in ``link_stato``,
    evento ``pdd.pronto``. Il richiedente è l'utente del token o il
    responsabile della chiave API; la richiesta si registra (registro, PdD,
    outbox) in una transazione: se fallisce, 503 e nessun collegamento.

    R04 (VD-C-106): con ``Idempotency-Key`` la stessa richiesta ripetuta (rete
    caduta, ritentativo dell'SDK) restituisce LO STESSO PdD con lo stato attuale
    (come ``GET /pdd/{pdd_id}``, collegamenti rigenerati) invece di crearne uno
    nuovo; la chiave e il PdD si registrano nella stessa transazione.
    """
    # R10 (D30): un ruolo personalizzato chiede PdD solo se ne ha il permesso; revisione
    # R17 (audit R17-02): anche un ruolo fisso senza il permesso (Consultazione) no
    motivo = _motivo_esibizione(user)
    if motivo:
        raise ErroreApi("DST-403", motivo)
    tenant_id = user.get("tenant_id", "")
    richiedente = _richiedente(user)
    canale_reg = (canale or "API").strip().upper()
    if canale_reg not in CANALI_AMMESSI:
        canale_reg = "API"
    req = req.model_copy(update={"richiedente_id": richiedente})

    prenotazione: idempotenza.Prenotazione | None = None
    chiave = idempotenza.valida_chiave(idempotency_key)
    if chiave is not None:
        impronta = idempotenza.impronta_richiesta({
            "richiesta": req.model_dump(mode="json", exclude={"richiedente_id"}),
            "canale": canale_reg, "richiedente": richiedente})
        try:
            prenotazione = await idempotenza.prenota(tenant_id, idempotenza.AMBITO_PDD, chiave,
                                                     impronta, adesso=datetime.now(UTC))
        except SQLAlchemyError as exc:
            raise _errore(503, "DST-503", "Registro delle distribuzioni non disponibile",
                          exc) from None
        if prenotazione.ripetuta and prenotazione.risorsa_id:
            response.headers["Idempotent-Replayed"] = "true"
            logger.info("pdd_richiesta_ripetuta", pdd_id=prenotazione.risorsa_id,
                        tenant_id=tenant_id)
            return await _risposta_pdd(prenotazione.risorsa_id, tenant_id)
    try:
        return await _richiedi_nuovo(req, tenant_id=tenant_id, richiedente=richiedente,
                                     canale_reg=canale_reg, user=user,
                                     prenotazione=prenotazione)
    except BaseException:
        if prenotazione is not None:
            await idempotenza.rilascia(prenotazione)
        raise


async def _richiedi_nuovo(req: RichiestaDistribuzione, *, tenant_id: str, richiedente: str,
                          canale_reg: str, user: dict,
                          prenotazione: idempotenza.Prenotazione | None
                          ) -> RispostaDistribuzione:
    """Esecuzione di una richiesta nuova (registro, PdD, outbox e chiave insieme)."""

    logger.info(
        "pdd_requested",
        aip_id=req.aip_id,
        tipo=req.tipo.value,
        richiedente=richiedente,
        tenant_id=tenant_id,
        canale=canale_reg,
        auth_method=user.get("auth_method"),
    )

    result = await esegui_distribuzione(req, tenant_id)

    try:
        async with get_async_session() as session:
            pdd_uuid = await registra_pdd(session, risultato=result, req=req,
                                          tenant_id=tenant_id, richiedente=richiedente,
                                          canale=canale_reg)
            if prenotazione is not None:
                await idempotenza.completa(session, prenotazione, pdd_uuid,
                                           adesso=datetime.now(UTC))
    except SQLAlchemyError as exc:
        raise _errore(503, "DST-503",
                      "Registro delle distribuzioni non disponibile: richiesta non consegnata",
                      exc) from None
    if result.pdd_id != pdd_uuid:
        result = result.model_copy(update={
            "pdd_id": pdd_uuid, "link_stato": f"/api/v2/distribution/pdd/{pdd_uuid}"})
    await pubblica_dopo_commit(evento="aip.distribuito", pdd_id=pdd_uuid)

    # FIX-006: audit trail globale
    from libs.shared.security.audit import log_audit

    await log_audit(
        evento="distribuzione.richiesta",
        tenant_id=tenant_id,
        user_id=richiedente if _uuid_valido(richiedente) else None,
        aip_id=req.aip_id if _uuid_valido(req.aip_id) else None,
        dettagli={
            "tipo": req.tipo.value,
            "motivo": req.motivo,
            "richiedente": richiedente,
            "canale": canale_reg,
            "pdd_id": pdd_uuid,
            "consegna": result.consegna,
            "auth_method": user.get("auth_method"),
        },
    )

    return result


@router.get(
    "/pdd/{pdd_id}",
    response_model=RispostaDistribuzione,
    summary="Stato e collegamenti di un PdD",
    responses=RISPOSTE_ERRORE,
)
async def stato_pdd(pdd_id: str, user: dict = Depends(utente_corrente)) -> RispostaDistribuzione:
    """
    Stato del PdD dell'ente: IN_PREPARAZIONE, PRONTO (collegamenti rigenerati
    fino alla scadenza, o collegamento allo ZIP), ERRORE (problema del
    catalogo in ``errore``). Scaduto: 410 DST-410. Di un altro ente o
    inesistente: 404 DST-404 (VD-C-005: anche per i ruoli del conservatore).
    """
    return await _risposta_pdd(pdd_id, user.get("tenant_id"))


@router.post(
    "/pdd/{pdd_id}/rigenera",
    response_model=RispostaDistribuzione,
    summary="Rigenera un PdD scaduto (R07, D18)",
    responses=RISPOSTE_ERRORE,
)
async def rigenera_pdd(pdd_id: str, user: dict = Depends(limite_pdd)) -> RispostaDistribuzione:
    """Rigenerazione con un clic di un PdD SCADUTO dell'ente (anche di restituzione).

    Stesso identificativo (le pratiche che lo citano restano valide): ZIP → torna
    IN_PREPARAZIONE e il lavoratore lo ricostruisce dallo storage conservativo con
    la validità attuale dell'ente (``PDD_VALIDITA_ORE``); LINK → nuova scadenza dei
    collegamenti. Non scaduto: 409 DST-409. Di un altro ente: 404 DST-404.
    Voce d'audit ``distribuzione.rigenerata``."""
    from libs.shared.security.audit import log_audit
    # R10 (revisione V3): rigenerare è richiedere di nuovo il PdD
    motivo = _motivo_esibizione(user)
    if motivo:
        raise ErroreApi("DST-403", motivo)
    pid = _uuid_o_none(pdd_id)
    tid = _uuid_o_none(user.get("tenant_id"))
    if pid is None or tid is None:
        raise ErroreApi("DST-404", titolo="PdD non trovato")
    adesso = datetime.now(UTC)
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT stato, consegna, scade_il, durata_link_secondi "
                "FROM public.pacchetti_distribuzione WHERE id = :id AND tenant_id = :tid "
                "FOR UPDATE"), {"id": pid, "tid": tid})).mappings().first()
            if riga is None:
                raise ErroreApi("DST-404", titolo="PdD non trovato")
            scaduto = riga["stato"] == "SCADUTO" or (
                riga["stato"] == "PRONTO" and riga["scade_il"] is not None
                and riga["scade_il"] <= adesso)
            if not scaduto:
                raise ErroreApi("DST-409", f"Pacchetto in stato {riga['stato']}: non scaduto",
                                dettagli={"stato": riga["stato"]})
            if riga["consegna"] == "ZIP":
                await session.execute(text(
                    "UPDATE public.pacchetti_distribuzione SET stato = 'IN_PREPARAZIONE', "
                    "tentativi = 0, prossimo_tentativo = :ora, lease_fino_al = NULL, "
                    "zip_key = NULL, zip_dimensione = NULL, zip_sha256 = NULL, "
                    "pronto_il = NULL, scade_il = NULL, errore = NULL, ultimo_errore = NULL "
                    "WHERE id = :id"), {"ora": adesso, "id": pid})
            else:
                await session.execute(text(
                    "UPDATE public.pacchetti_distribuzione SET stato = 'PRONTO', "
                    "scade_il = :s WHERE id = :id"),
                    {"s": adesso + timedelta(seconds=int(riga["durata_link_secondi"])),
                     "id": pid})
    except SQLAlchemyError as exc:
        raise _errore(503, "DST-503", "Registro delle distribuzioni non disponibile", exc) from None
    await log_audit(evento="distribuzione.rigenerata", tenant_id=str(tid),
                    user_id=user.get("user_id") if _uuid_valido(user.get("user_id")) else None,
                    dettagli={"pdd_id": str(pid), "consegna": riga["consegna"],
                              "stato_precedente": riga["stato"]})
    logger.info("pdd_rigenerato", pdd_id=str(pid), consegna=riga["consegna"])
    return await _risposta_pdd(str(pid), tid)


async def _risposta_pdd(pdd_id: str, tenant_id: object) -> RispostaDistribuzione:
    """Stato attuale di un PdD dell'ente (usata anche dalla richiesta ripetuta)."""
    from app.services.s3_client import generate_signed_url

    pid = _uuid_o_none(pdd_id)
    tid = _uuid_o_none(tenant_id)
    if pid is None or tid is None:
        raise ErroreApi("DST-404", titolo="PdD non trovato")
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT id::text AS id, aip_id::text AS aip_id, tipo, consegna, stato, "
                "documenti, hash_pdd, zip_bucket, zip_key, zip_dimensione, zip_sha256, "
                "durata_link_secondi, errore, creato_il, scade_il "
                "FROM public.pacchetti_distribuzione WHERE id = :id AND tenant_id = :tid"
            ), {"id": pid, "tid": tid})).mappings().first()
    except SQLAlchemyError as exc:
        raise _errore(503, "DST-503", "Registro delle distribuzioni non disponibile", exc) from None
    if riga is None:
        raise ErroreApi("DST-404", titolo="PdD non trovato")
    adesso = datetime.now(UTC)
    file_interni = riga["documenti"] if isinstance(riga["documenti"], list) else []
    base = {
        "pdd_id": riga["id"], "aip_id": riga["aip_id"], "tipo": riga["tipo"],
        "stato": riga["stato"], "consegna": riga["consegna"], "hash_pdd": riga["hash_pdd"],
        "created_at": riga["creato_il"].isoformat(),
        "sincro_xml_incluso": any(f.get("tipo") == "INDICE" for f in file_interni),
        "link_stato": f"/api/v2/distribution/pdd/{riga['id']}",
    }
    if riga["stato"] == "SCADUTO" or (riga["stato"] == "PRONTO" and riga["scade_il"]
                                      and riga["scade_il"] <= adesso):
        raise ErroreApi("DST-410", f"Pacchetto scaduto il {riga['scade_il'].isoformat()}"
                        if riga["scade_il"] else None)
    documenti = [{"nome_file": f["nome"], "tipo": f["tipo"], "formato_mime": f["formato_mime"],
                  "hash_sha256": f.get("sha256"), "dimensione_bytes": f.get("dimensione")}
                 for f in file_interni]
    if riga["stato"] == "PRONTO" and riga["consegna"] == "LINK":
        documenti, scadenza = await collegamenti_aggiornati(
            file_interni, riga["scade_il"], int(riga["durata_link_secondi"]), adesso)
        return RispostaDistribuzione(**base, documenti=documenti, url_scadenza=scadenza)
    if riga["stato"] == "PRONTO" and riga["zip_key"]:
        rimanente = max(1, min(int(riga["durata_link_secondi"]),
                               int((riga["scade_il"] - adesso).total_seconds())))
        url = await generate_signed_url(riga["zip_bucket"], riga["zip_key"], rimanente,
                                        f"pdd-{riga['id']}.zip")
        return RispostaDistribuzione(
            **base, documenti=documenti, url_scadenza=riga["scade_il"].isoformat(),
            zip={"url": url, "dimensione_bytes": riga["zip_dimensione"],
                 "hash_sha256": riga["zip_sha256"]})
    return RispostaDistribuzione(**base, documenti=documenti,
                                 errore=riga["errore"] if riga["stato"] == "ERRORE" else None)


def _uuid_o_none(valore: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


def _uuid_valido(valore: str | None) -> bool:
    try:
        uuid.UUID(str(valore))
        return True
    except ValueError:
        return False


@router.get("/aip/{aip_id}", summary="Info AIP distribuibile")
async def get_aip_info(
    aip_id: str,
    user: dict = Depends(utente_corrente),
) -> dict:
    """Ritorna le informazioni di un AIP pronto per la distribuzione."""

    async with get_async_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id::text, urn, stato::text, hash_sha256, "
                    "dimensione_bytes, data_versamento, s3_key, s3_bucket, "
                    "tenant_id::text "
                    "FROM public.aip WHERE id = CAST(:id AS uuid)"
                ),
                {"id": aip_id},
            )
        ).fetchone()

    if not row:
        raise HTTPException(404, {"title": "AIP non trovato"})

    # FIX-002: verifica che l'AIP appartenga al tenant dell'utente
    _check_tenant_owns_aip(user, row[8])

    return {
        "aip_id": row[0],
        "urn": row[1],
        "stato": row[2],
        "hash_sha256": row[3],
        "dimensione_bytes": row[4],
        "data_versamento": row[5].isoformat() if row[5] else None,
        "distribuibile": row[2] in ("IN_CATENA", "CONSERVATO", "SU_STORAGE"),
    }


@router.post(
    "/search",
    response_model=AIPSearchResult,
    summary="Ricerca AIP per distribuzione",
    responses=RISPOSTE_ERRORE,
)
async def search(
    req: AIPSearchRequest,
    user: dict = Depends(limite_ricerca),
) -> AIPSearchResult:
    """Ricerca nei soli AIP dell'ente del token o della chiave API; i profili
    del conservatore (SUPER_ADMIN, RSC_CONSERVATORE) possono indicare
    `tenant_id` (stesso perimetro di `/storico` e della ricerca del portale),
    per gli altri `tenant_id` nel corpo è ignorato. Pagina a cursore: passare
    `cursore` della risposta per la pagina successiva (`altri`); `skip` > 0 →
    422 DST-422 (R03 f2: niente paginazione per posizione). `total` è contato
    al massimo fino a RICERCA_CONTEGGIO_MASSIMO (`total_esatto`). Lo `stato`
    di ogni risultato è quello attuale nel database; un risultato che nel
    database non è dell'ente non si mostra (fail-closed)."""
    if req.skip:
        raise ErroreApi("DST-422", "Parametro skip non più supportato: usare cursore",
                        dettagli={"campo": "skip"})
    ente = str(user.get("tenant_id") or "")
    if req.tenant_id and user.get("ruolo", "") in _RUOLI_CONSERVATORE:
        ente = str(req.tenant_id)
        if not _uuid_valido(ente):
            raise ErroreApi("DST-422", "tenant_id non valido", dettagli={"campo": "tenant_id"})
    if not _uuid_valido(ente):
        raise HTTPException(403, {"type": "DST-ENTE",
                                  "title": "Il token non indica un ente valido"})
    try:
        esito = await search_aip(req.query, ente, req.classe_documentale, req.anno,
                                 req.skip, req.limit, data_da=req.data_da, data_a=req.data_a,
                                 dopo=req.cursore)
    except CursoreNonValido:
        raise ErroreApi("DST-104") from None
    except RicercaNonDisponibile as exc:
        raise _errore(503, "DST-503", "Ricerca temporaneamente non disponibile", exc) from None
    try:
        esito["items"] = await stato_attuale_dal_db(esito["items"], ente)
    except SQLAlchemyError as exc:
        raise _errore(503, "DST-503", "Ricerca temporaneamente non disponibile", exc) from None
    return AIPSearchResult(**esito)


#: Nome dell'elenco nel cursore dello storico (un cursore di altri elenchi è rifiutato)
_ELENCO_STORICO = "storico"


def _cursore_storico(quando: datetime, ultimo: str) -> str:
    dati = {"e": _ELENCO_STORICO, "t": quando.isoformat(), "i": ultimo}
    return base64.urlsafe_b64encode(json.dumps(dati, separators=(",", ":")).encode()
                                    ).decode().rstrip("=")


def _leggi_cursore_storico(cursore: str) -> tuple[datetime, str]:
    try:
        dati = json.loads(base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)))
        if dati.get("e") != _ELENCO_STORICO:
            raise ValueError("cursore di un altro elenco")
        quando = datetime.fromisoformat(dati["t"])
        if quando.tzinfo is None:
            raise ValueError("data senza fuso")
        return quando, str(uuid.UUID(str(dati["i"])))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ErroreApi("DST-422", titolo="Cursore non valido",
                        dettagli={"parametro": "cursore"}) from None


@router.get("/storico", summary="Storico richieste distribuzione del tenant")
async def storico_distribuzioni(
    tenant_id: str | None = Query(None, description="Ente scelto (solo SUPER_ADMIN/RSC)"),
    cursore: str | None = Query(None, max_length=300,
                                description="Cursore restituito dalla pagina precedente"),
    limit: int = Query(20, ge=1, le=100),
    skip: int | None = Query(None, ge=0, deprecated=True,
                             description="Non più supportato (R04): usare `cursore`"),
    user: dict = Depends(utente_corrente),
) -> dict:
    """
    Richieste di distribuzione dell'ente (dal portale e via API), dalla più
    recente. G2: errore del database → 503 con id di correlazione (prima: elenco
    vuoto); un profilo globale può indicare ``tenant_id`` (metadati, VD-C-005).
    R04 (VD-C-102): a cursore ``(created_at, id)`` sull'indice parziale
    ``idx_notifications_pdd_cursore``, niente OFFSET né ``COUNT(*)``; risposta
    ``{items, limit, cursore, altri}``. ``skip`` > 0 → 422 DST-422.
    """
    if skip:
        raise ErroreApi("DST-422", titolo="Parametro skip non più supportato: usare cursore",
                        dettagli={"parametro": "skip"})
    if tenant_id and user.get("ruolo", "") in _RUOLI_CONSERVATORE:
        ente = tenant_id
    elif tenant_id and not _stesso_tenant(user, tenant_id):
        raise HTTPException(403, {"type": "AUTH-020", "title": "Accesso negato a questo ente"})
    else:
        ente = user.get("tenant_id", "")
    if not _uuid_valido(ente):
        raise HTTPException(400 if tenant_id else 403, {
            "type": "DST-ENTE",
            "title": "tenant_id non valido" if tenant_id
            else "Il token non indica un ente: indicare tenant_id",
        })

    parametri: dict = {"tid": ente, "n": limit + 1}
    dopo = ""
    if cursore:
        parametri["dopo_t"], parametri["dopo_i"] = _leggi_cursore_storico(cursore)
        dopo = "AND (created_at, id) < (:dopo_t, CAST(:dopo_i AS uuid)) "
    try:
        async with get_async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, aip_id::text AS aip_id, tipo, destinatario, "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
                        "stato, created_at, canale, payload_json "
                        "FROM public.notifications "
                        "WHERE tipo LIKE 'PDD\\_%' AND tenant_id = CAST(:tid AS uuid) "
                        f"{dopo}ORDER BY created_at DESC, id DESC LIMIT :n"
                    ),
                    parametri,
                )
            ).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore(503, "DST-503", "Storico delle distribuzioni non disponibile",
                      exc) from None

    altri = len(rows) > limit
    rows = rows[:limit]
    items = []
    for r in rows:
        payload = r["payload_json"] if isinstance(r["payload_json"], dict) else {}
        items.append({
            "id": str(r["id"]),
            "pdd_id": payload.get("pdd_id") or str(r["id"]),
            "aip_id": r["aip_id"],
            "tipo": r["tipo"],
            "richiedente": r["destinatario"],
            "stato": r["stato"],
            "data": r["created_at"].isoformat() if r["created_at"] else None,
            "canale": r["canale"],
            "motivo": payload.get("motivo"),
            "motivo_rifiuto": payload.get("motivo_rifiuto"),
        })
    prossimo = (_cursore_storico(rows[-1]["created_at"], str(rows[-1]["id"]))
                if altri and rows else None)
    return {"items": items, "limit": limit, "cursore": prossimo, "altri": altri}


# ── Approvazione / Rifiuto PdD ──────────────────────────────────────
# Revisione di sicurezza B10: identificativo non UUID → 422 (prima 500); si
# approvano/rifiutano SOLO richieste di distribuzione (tipo PDD_*): ogni altra
# notifica risponde 404; `motivo` deve essere testo (altrimenti 422).


def _pdd_id_o_422(valore: str) -> str:
    try:
        return str(uuid.UUID(str(valore)))
    except ValueError:
        raise HTTPException(422, {"type": "DST-422",
                                  "title": "Identificativo della richiesta non valido"}) from None



@router.post(
    "/{pdd_id}/approva",
    summary="Approva richiesta PdD in attesa",
)
async def approva_pdd(
    pdd_id: str,
    user: dict = Depends(utente_corrente),
) -> dict:
    """
    Approva una richiesta PdD in stato PENDING.
    Solo ADMIN_TENANT e SUPER_ADMIN/RSC_CONSERVATORE.
    """
    ruolo = user.get("ruolo", "")

    if ruolo not in ("SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE"):
        raise HTTPException(
            403, {"title": "Solo ADMIN_TENANT e SUPER_ADMIN possono approvare"},
        )
    pdd_id = _pdd_id_o_422(pdd_id)

    async with get_async_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, stato, tipo, canale, CAST(tenant_id AS text) "
                    "FROM public.notifications WHERE id = CAST(:id AS uuid) "
                    "AND tipo LIKE 'PDD\\_%'"
                ),
                {"id": pdd_id},
            )
        ).fetchone()

        # VD-C-005: un PdD di un altro cliente risponde 404 come uno inesistente
        if not row or not _pdd_visibile(user, row[4]):
            raise HTTPException(404, {"title": f"PdD {pdd_id!r} non trovato"})

        if row[1] not in ("PENDING", "IN_ATTESA"):
            raise HTTPException(
                409,
                {
                    "title": "PdD non in attesa",
                    "detail": f"Stato corrente: {row[1]}",
                },
            )

        await session.execute(
            text("UPDATE public.notifications SET stato = :stato WHERE id = CAST(:id AS uuid)"),
            {"stato": "APPROVATA", "id": pdd_id},
        )
        await session.commit()

    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit

        await log_audit(
            evento="distribuzione.approvata",
            tenant_id=user.get("tenant_id"),
            user_id=user.get("user_id"),
            dettagli={"pdd_id": pdd_id},
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)

    logger.info("pdd_approvato", pdd_id=pdd_id, approvato_da=user.get("tenant_id"))
    return {"pdd_id": pdd_id, "stato": "APPROVATA", "approvato": True}


@router.post(
    "/{pdd_id}/rifiuta",
    summary="Rifiuta richiesta PdD in attesa",
)
async def rifiuta_pdd(
    pdd_id: str,
    body: dict | None = None,
    user: dict = Depends(utente_corrente),
) -> dict:
    """Rifiuta una richiesta PdD. Body: { "motivo": "..." } (testo; altro tipo → 422)."""
    ruolo = user.get("ruolo", "")

    if ruolo not in ("SUPER_ADMIN", "ADMIN_TENANT", "RSC_CONSERVATORE"):
        raise HTTPException(
            403, {"title": "Solo ADMIN_TENANT può rifiutare richieste"},
        )
    pdd_id = _pdd_id_o_422(pdd_id)
    motivo_grezzo = (body or {}).get("motivo") if isinstance(body, dict) else None
    if motivo_grezzo is not None and (not isinstance(motivo_grezzo, str)
                                      or len(motivo_grezzo) > 1000):
        raise HTTPException(422, {"type": "DST-422",
                                  "title": "motivo deve essere un testo di al più 1000 caratteri"})
    motivo = (motivo_grezzo or "").strip() or "Rifiuto senza motivazione"

    async with get_async_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, stato, CAST(tenant_id AS text) "
                    "FROM public.notifications WHERE id = CAST(:id AS uuid) "
                    "AND tipo LIKE 'PDD\\_%'"
                ),
                {"id": pdd_id},
            )
        ).fetchone()

        # VD-C-005: un PdD di un altro cliente risponde 404 come uno inesistente
        if not row or not _pdd_visibile(user, row[2]):
            raise HTTPException(404, {"title": f"PdD {pdd_id!r} non trovato"})

        if row[1] not in ("PENDING", "IN_ATTESA"):
            raise HTTPException(
                409,
                {
                    "title": "PdD non in attesa",
                    "detail": f"Stato corrente: {row[1]}",
                },
            )

        import json

        await session.execute(
            text(
                "UPDATE public.notifications "
                "SET stato = :stato, "
                "payload_json = COALESCE(payload_json, '{}'::jsonb) || CAST(:meta AS jsonb) "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {
                "stato": "RIFIUTATA",
                "meta": json.dumps({"motivo_rifiuto": motivo}),
                "id": pdd_id,
            },
        )
        await session.commit()

    # FIX-006: audit trail
    try:
        from libs.shared.security.audit import log_audit

        await log_audit(
            evento="distribuzione.rifiutata",
            tenant_id=user.get("tenant_id"),
            user_id=user.get("user_id"),
            dettagli={"pdd_id": pdd_id, "motivo": motivo[:200]},
        )
    except Exception as exc:  # noqa: BLE001 — non blocca l'operazione, resta nel log
        logger.warning("audit_non_registrato", errore=type(exc).__name__)

    logger.info("pdd_rifiutato", pdd_id=pdd_id, motivo=motivo[:80])
    return {"pdd_id": pdd_id, "stato": "RIFIUTATA", "motivo": motivo}
