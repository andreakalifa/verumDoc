# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/api/pec.py — console dei canali PEC (R13b, VD-C-078).

  GET  /api/v1/notifiche/pec/canali                     registro, stato dal lavoratore, tipi
  PUT  /api/v1/notifiche/pec/canali/{codice}            registra o modifica (versione letta)
  POST /api/v1/notifiche/pec/canali/{codice}/prova      prova d'invio a un indirizzo di prova
  GET  /api/v1/notifiche/pec/messaggi                   messaggi a cursore (filtri stato, caso)
  GET  /api/v1/notifiche/pec/messaggi/{id}              messaggio con le sue ricevute
  GET  /api/v1/notifiche/pec/ricevute                   ricevute non associate o non autentiche
  GET  /api/v1/notifiche/pec/ricevute/{id}/eml          messaggio grezzo (evidenza)

Registro, prova e ricevute non associate: solo i ruoli FISSI del conservatore con il
permesso mai delegabile ``pec_canali:gestisci`` (mai un ruolo personalizzato, mai una
chiave API). Messaggi e ricevute ASSOCIATE: anche l'amministratore dell'ente, per il
proprio ente (filtro e Row Level Security). I segreti non passano mai di qui (Secret
del lavoratore della posta); lo stato dei segreti lo scrive il lavoratore
(``pec_canali_stato``: solo presenti sì/no). La prova accoda un messaggio PROVA sul
canale scelto: lo invia il lavoratore, la console ne segue lo stato e le ricevute.
Ogni modifica e ogni prova hanno la loro voce d'audit (nella stessa transazione).
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.autenticazione import _INTESTAZIONE_401, _verifica_jwt
from app.config import settings
from app.domain import pec as dom
from app.services.smtp import indirizzo_plausibile
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.firma_qualificata.configurazione import ConfigurazioneFornitoreNonValida
from libs.shared.pec.configurazione import (
    CODICE_CANALE,
    SEGRETI_PER_TIPO,
    TIPI,
    VARIABILE_SEGRETI,
    valida_configurazione,
)
from libs.shared.security.audit import registra_audit
from libs.shared.security.permessi import ha_permesso
from libs.shared.security.revoca import richiedi_non_revocato
from libs.verumdoc_rules.permessi import PEC_CANALI_GESTISCI, RUOLI_CONSERVATORE

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/notifiche/pec", tags=["PEC"], responses=RISPOSTE_ERRORE)

LIMITE_PREDEFINITO = 30
LIMITE_MASSIMO = 100


def _produzione() -> bool:
    return os.environ.get("ENV", settings.env).strip().lower() == "production"


async def _payload(authorization: str | None) -> dict[str, Any]:
    schema, _, token = (authorization or "").partition(" ")
    if schema.lower() != "bearer" or not token.strip():
        raise ErroreApi("AUTH-000", "Serve l'accesso di una persona", headers=_INTESTAZIONE_401)
    payload = _verifica_jwt(token.strip())
    await richiedi_non_revocato(payload)
    return payload


async def conservatore_pec(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Ruolo fisso del conservatore con il permesso mai delegabile (§31 R10 (2))."""
    payload = await _payload(authorization)
    if payload.get("ruolo") not in RUOLI_CONSERVATORE or payload.get("auth_method") == "api_key" \
            or not ha_permesso(payload, PEC_CANALI_GESTISCI):
        raise ErroreApi("NTF-403-PEC")
    return payload


async def lettore_pec(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Conservatore (con il permesso) o amministratore del proprio ente."""
    payload = await _payload(authorization)
    ruolo = payload.get("ruolo")
    if ruolo in RUOLI_CONSERVATORE and ha_permesso(payload, PEC_CANALI_GESTISCI):
        payload["_ente"] = None
        return payload
    if ruolo == "ADMIN_TENANT" and payload.get("auth_method") != "api_key":
        try:
            payload["_ente"] = str(uuid.UUID(str(payload.get("tenant_id"))))
        except (TypeError, ValueError):
            raise ErroreApi("AUTH-021") from None
        return payload
    raise ErroreApi("NTF-403-PEC", "I messaggi PEC li vedono il conservatore e "
                                   "l'amministratore dell'ente")


conservatore_pec.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt"], "ruoli": sorted(RUOLI_CONSERVATORE)}
lettore_pec.__verumdoc_autenticazione__ = {  # type: ignore[attr-defined]
    "metodi": ["jwt"], "ruoli": sorted({*RUOLI_CONSERVATORE, "ADMIN_TENANT"})}


def _errore(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("NTF-503", exc, evento=evento)


def _uuid(valore: Any) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


def _iso(valore: Any) -> str | None:
    return valore.isoformat() if isinstance(valore, datetime) else None


def _canale(r: Any, stato: Any | None) -> dict[str, Any]:
    return {"codice": r["codice"], "tipo": r["tipo"], "nome": r["nome"], "attivo": r["attivo"],
            "priorita": r["priorita"], "configurazione": dict(r["configurazione"] or {}),
            "versione": r["versione"], "aggiornato_il": _iso(r["updated_at"]),
            "stato": None if stato is None else {
                "segreti_presenti": dict(stato["segreti_presenti"] or {}),
                "problemi": list(stato["problemi"] or []),
                "ultimo_invio_il": _iso(stato["ultimo_invio_il"]),
                "ultima_lettura_il": _iso(stato["ultima_lettura_il"]),
                "ultimo_errore": stato["ultimo_errore"],
                "aggiornato_il": _iso(stato["aggiornato_il"])}}


@router.get("/canali", summary="Canali PEC: registro, stato e tipi")
async def elenco_canali(_chi: dict[str, Any] = Depends(conservatore_pec)) -> dict[str, Any]:
    try:
        async with get_async_session() as s:
            righe = (await s.execute(text(
                "SELECT * FROM public.canali_pec ORDER BY priorita, codice"))).mappings().all()
            stati = {r["canale_codice"]: r for r in (await s.execute(text(
                "SELECT * FROM public.pec_canali_stato"))).mappings().all()}
            conteggi = {r[0]: int(r[1]) for r in (await s.execute(text(
                "SELECT stato, count(*) FROM (SELECT stato FROM public.pec_messaggi "
                "WHERE created_at >= now() - interval '30 days' LIMIT 20000) m "
                "GROUP BY stato"))).all()}
    except SQLAlchemyError as exc:
        raise _errore("pec_canali_elenco_errore", exc) from None
    return {"items": [_canale(r, stati.get(r["codice"])) for r in righe],
            "tipi": list(TIPI),
            "segreti": {t: [{"nome": n, "obbligatorio": o} for n, o in v]
                        for t, v in SEGRETI_PER_TIPO.items()},
            "variabile_segreti": VARIABILE_SEGRETI,
            "sviluppo_ammesso": not _produzione(),
            "messaggi_30_giorni": conteggi}


class CanaleModifica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["SMTP_IMAP", "SVILUPPO"]
    nome: str = Field(..., min_length=3, max_length=200)
    attivo: bool
    priorita: int = Field(10, ge=1, le=100,
                          description="Ordine (1 = primo): il primo attivo e utilizzabile invia")
    configurazione: dict[str, Any] = Field(default_factory=dict)
    versione: int = Field(0, ge=0, description="Versione letta (0 = nuovo)")


@router.put("/canali/{codice}", summary="Registra o modifica un canale PEC")
async def modifica_canale(codice: str, body: CanaleModifica,
                          chi: dict[str, Any] = Depends(conservatore_pec)) -> dict[str, Any]:
    if not CODICE_CANALE.match(codice):
        raise ErroreApi("NTF-422-PEC", "Codice: minuscole, cifre e trattini (es. pec-principale)")
    try:
        conf = valida_configurazione(body.tipo, body.configurazione, produzione=_produzione())
    except ConfigurazioneFornitoreNonValida as exc:
        raise ErroreApi("NTF-422-PEC", "; ".join(exc.motivi),
                        dettagli={"motivi": exc.motivi}) from None
    autore = _uuid(chi.get("user_id"))
    try:
        async with get_async_session() as s:
            prima = (await s.execute(text(
                "SELECT * FROM public.canali_pec WHERE codice = :c FOR UPDATE"),
                {"c": codice})).mappings().one_or_none()
            if (prima["versione"] if prima else 0) != body.versione:
                raise ErroreApi("NTF-409-PEC")
            if prima is not None and prima["tipo"] != body.tipo:
                raise ErroreApi("NTF-422-PEC", "Il tipo di un canale registrato non cambia: "
                                               "disattivarlo e registrarne uno nuovo")
            valori = {"c": codice, "t": body.tipo, "n": body.nome.strip(), "a": body.attivo,
                      "p": body.priorita, "conf": json.dumps(conf), "u": autore}
            if prima is None:
                dopo = (await s.execute(text(
                    "INSERT INTO public.canali_pec (codice, tipo, nome, attivo, priorita, "
                    " configurazione, aggiornato_da) VALUES (:c, :t, :n, :a, :p, "
                    " CAST(:conf AS jsonb), CAST(:u AS uuid)) RETURNING *"), valori)
                ).mappings().one()
            else:
                dopo = (await s.execute(text(
                    "UPDATE public.canali_pec SET nome = :n, attivo = :a, priorita = :p, "
                    " configurazione = CAST(:conf AS jsonb), versione = versione + 1, "
                    " aggiornato_da = CAST(:u AS uuid), updated_at = now() WHERE codice = :c "
                    "RETURNING *"), valori)).mappings().one()
            await registra_audit(s, evento="pec.canale_modificato", user_id=autore,
                                 dettagli={"codice": codice,
                                           "prima": _canale(prima, None) if prima else None,
                                           "dopo": _canale(dopo, None)})
    except SQLAlchemyError as exc:
        raise _errore("pec_canale_modifica_errore", exc) from None
    logger.info("pec_canale_modificato", codice=codice, attivo=body.attivo, tipo=body.tipo)
    return _canale(dopo, None)


class RichiestaProva(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destinatario: str = Field(..., min_length=6, max_length=320,
                              description="Indirizzo PEC di prova (riceve un messaggio reale)")


@router.post("/canali/{codice}/prova", status_code=202,
             summary="Prova d'invio: accoda un messaggio di prova sul canale")
async def prova_canale(codice: str, body: RichiestaProva,
                       chi: dict[str, Any] = Depends(conservatore_pec)) -> dict[str, Any]:
    destinatario = body.destinatario.strip().lower()
    if not CODICE_CANALE.match(codice):
        raise ErroreApi("NTF-404-PEC")
    if not indirizzo_plausibile(destinatario):
        raise ErroreApi("NTF-422-PEC", "Indirizzo di prova non valido o segnaposto",
                        dettagli={"motivi": ["destinatario non valido"]})
    autore = _uuid(chi.get("user_id"))
    c = dom.comunicazione_prova(destinatario)
    try:
        async with get_async_session() as s:
            canale = (await s.execute(text(
                "SELECT codice, attivo FROM public.canali_pec WHERE codice = :c"),
                {"c": codice})).first()
            if canale is None:
                raise ErroreApi("NTF-404-PEC")
            # riserva atomica (§31 R10 (7)): le prove dell'ultima ora contate sotto lock
            await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('pec:prova', 0))"))
            fatte = int((await s.execute(text(
                "SELECT count(*) FROM (SELECT 1 FROM public.pec_messaggi WHERE caso_uso = "
                "'PROVA' AND created_at >= :dal LIMIT :n) p"),
                {"dal": datetime.now(UTC) - timedelta(hours=1),
                 "n": settings.pec_prove_per_ora})).scalar_one())
            if fatte >= settings.pec_prove_per_ora:
                raise ErroreApi("NTF-429-PEC")
            id_ = (await s.execute(text(
                "INSERT INTO public.pec_messaggi (tenant_id, caso_uso, oggetto_id, destinatario, "
                " oggetto, testo, stato, creato_da, canale_codice) VALUES (NULL, 'PROVA', :oid, "
                " :d, :o, :t, 'IN_CODA', CAST(:u AS uuid), :c) RETURNING id::text"),
                {"oid": f"prova:{codice}:{uuid.uuid4()}", "d": destinatario, "o": c.oggetto,
                 "t": c.testo, "u": autore, "c": codice})).scalar_one()
            await registra_audit(s, evento="pec.canale_provato", user_id=autore,
                                 dettagli={"codice": codice, "messaggio_id": id_,
                                           "canale_attivo": bool(canale.attivo)})
    except SQLAlchemyError as exc:
        raise _errore("pec_prova_errore", exc) from None
    return {"messaggio_id": id_, "stato": dom.IN_CODA, "canale": codice,
            "canale_attivo": bool(canale.attivo),
            "nota": "Il lavoratore della posta invia la prova da questo canale appena è "
                    "attivo e utilizzabile; ricevute e stato compaiono nel messaggio."}


def _cursore(valore: str | None) -> tuple[datetime, str] | None:
    if not valore:
        return None
    try:
        quando, _, id_ = base64.urlsafe_b64decode(valore.encode() + b"==").decode().partition("|")
        return datetime.fromisoformat(quando), str(uuid.UUID(id_))
    except (ValueError, UnicodeDecodeError):
        raise ErroreApi("NTF-404-PEC", "Cursore non valido") from None


def _nuovo_cursore(quando: datetime, id_: str) -> str:
    return base64.urlsafe_b64encode(f"{quando.isoformat()}|{id_}".encode()).decode().rstrip("=")


async def _attesa_ore(ente: str | None) -> int:
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import PEC_CONSEGNA_ATTESA_ORE

    return int((await parametri([PEC_CONSEGNA_ATTESA_ORE], ente))[PEC_CONSEGNA_ATTESA_ORE])


def _messaggio(r: Any, adesso: datetime, attesa_ore: int) -> dict[str, Any]:
    avviso = None
    if r["stato"] == dom.ACCETTATO and r["accettato_il"] and \
            r["accettato_il"] < adesso - timedelta(hours=attesa_ore):
        avviso = f"consegna non confermata dopo {attesa_ore} ore"
    elif r["stato"] == dom.INVIATO and r["inviato_il"] and \
            r["inviato_il"] < adesso - timedelta(hours=1):
        avviso = "accettazione del gestore non ricevuta dopo un'ora"
    return {"id": r["id"], "ente": r["tenant_id"], "caso_uso": r["caso_uso"],
            "destinatario": r["destinatario"], "oggetto": r["oggetto"], "stato": r["stato"],
            "canale": r["canale_codice"], "message_id": r["message_id"],
            "tentativi": r["tentativi"], "errore": r["errore"],
            "creato_il": _iso(r["created_at"]), "inviato_il": _iso(r["inviato_il"]),
            "accettato_il": _iso(r["accettato_il"]), "consegnato_il": _iso(r["consegnato_il"]),
            "esito_il": _iso(r["esito_il"]), "avviso": avviso}


_COLONNE_M = ("id::text AS id, tenant_id::text AS tenant_id, caso_uso, destinatario, oggetto, "
              "stato, canale_codice, message_id, tentativi, errore, created_at, inviato_il, "
              "accettato_il, consegnato_il, esito_il")


@router.get("/messaggi", summary="Messaggi PEC, dal più recente, a cursore")
async def elenco_messaggi(
    stato: Literal["IN_CODA", "IN_INVIO", "INVIATO", "ACCETTATO", "CONSEGNATO", "NON_ACCETTATO",
                   "MANCATA_CONSEGNA", "NON_PARTITO"] | None = Query(None),
    caso_uso: Literal["SCARTO", "RESTITUZIONE", "RIVERSAMENTO", "SCADENZE",
                      "PROVA"] | None = Query(None),
    dopo: str | None = Query(None, max_length=200, description="Cursore della pagina precedente"),
    limite: int = Query(LIMITE_PREDEFINITO, ge=1, le=LIMITE_MASSIMO),
    chi: dict[str, Any] = Depends(lettore_pec),
) -> dict[str, Any]:
    filtri, valori = [], {"n": limite + 1}
    if chi["_ente"]:
        filtri.append("tenant_id = CAST(:ente AS uuid)")
        valori["ente"] = chi["_ente"]
    if stato:
        filtri.append("stato = :stato")
        valori["stato"] = stato
    if caso_uso:
        filtri.append("caso_uso = :caso")
        valori["caso"] = caso_uso
    cursore = _cursore(dopo)
    if cursore:
        filtri.append("(created_at, id) < (:qc, CAST(:qi AS uuid))")
        valori["qc"], valori["qi"] = cursore
    dove = (" WHERE " + " AND ".join(filtri)) if filtri else ""
    try:
        async with get_async_session() as s:
            righe = (await s.execute(text(
                f"SELECT {_COLONNE_M} FROM public.pec_messaggi{dove} "  # noqa: S608 — filtri chiusi
                "ORDER BY created_at DESC, id DESC LIMIT :n"), valori)).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore("pec_messaggi_errore", exc) from None
    adesso = datetime.now(UTC)
    attesa = await _attesa_ore(chi["_ente"])
    pagina = righe[:limite]
    return {"items": [_messaggio(r, adesso, attesa) for r in pagina],
            "cursore": _nuovo_cursore(pagina[-1]["created_at"], pagina[-1]["id"])
            if len(righe) > limite else None}


def _ricevuta(r: Any) -> dict[str, Any]:
    return {"id": int(r["id"]), "canale": r["canale_codice"], "esito": r["esito"],
            "motivo": r["motivo"], "messaggio_id": r["messaggio_id"], "tipo": r["tipo"],
            "msgid": r["msgid"], "identificativo": r["identificativo"], "gestore": r["gestore"],
            "consegna": r["consegna"], "errore": r["errore"], "errore_esteso": r["errore_esteso"],
            "data_ricevuta": _iso(r["data_ricevuta"]),
            "certificato_sha256": r["certificato_sha256"],
            "certificato_soggetto": r["certificato_soggetto"],
            "impronta_sha256": r["impronta_sha256"], "dimensione": r["dimensione"],
            "ricevuta_il": _iso(r["ricevuta_il"])}


_COLONNE_R = ("id, canale_codice, esito, motivo, messaggio_id::text AS messaggio_id, tipo, msgid, "
              "identificativo, gestore, consegna, errore, errore_esteso, data_ricevuta, "
              "certificato_sha256, certificato_soggetto, impronta_sha256, dimensione, ricevuta_il")


@router.get("/messaggi/{messaggio_id}", summary="Messaggio PEC con le sue ricevute")
async def dettaglio_messaggio(messaggio_id: str,
                              chi: dict[str, Any] = Depends(lettore_pec)) -> dict[str, Any]:
    id_ = _uuid(messaggio_id)
    if id_ is None:
        raise ErroreApi("NTF-404-PEC")
    filtro = " AND tenant_id = CAST(:ente AS uuid)" if chi["_ente"] else ""
    try:
        async with get_async_session() as s:
            riga = (await s.execute(text(
                f"SELECT {_COLONNE_M}, testo FROM public.pec_messaggi "  # noqa: S608 — costanti
                f"WHERE id = CAST(:id AS uuid){filtro}"),
                {"id": id_, "ente": chi["_ente"]})).mappings().first()
            if riga is None:
                raise ErroreApi("NTF-404-PEC")
            ricevute = (await s.execute(text(
                f"SELECT {_COLONNE_R} FROM public.pec_ricevute "  # noqa: S608 — costanti
                "WHERE messaggio_id = CAST(:id AS uuid) ORDER BY id"), {"id": id_})
            ).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore("pec_messaggio_errore", exc) from None
    return {**_messaggio(riga, datetime.now(UTC), await _attesa_ore(chi["_ente"])),
            "testo": riga["testo"], "ricevute": [_ricevuta(r) for r in ricevute]}


@router.get("/ricevute", summary="Messaggi della casella non associati o non autentici")
async def ricevute_non_associate(
    dopo: int | None = Query(None, ge=1, le=2**63 - 1),
    limite: int = Query(LIMITE_PREDEFINITO, ge=1, le=LIMITE_MASSIMO),
    _chi: dict[str, Any] = Depends(conservatore_pec),
) -> dict[str, Any]:
    try:
        async with get_async_session() as s:
            righe = (await s.execute(text(
                f"SELECT {_COLONNE_R} FROM public.pec_ricevute "  # noqa: S608 — costanti
                "WHERE esito <> 'ASSOCIATA' AND (CAST(:dopo AS bigint) IS NULL OR id < :dopo) "
                "ORDER BY id DESC LIMIT :n"), {"dopo": dopo, "n": limite + 1})).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore("pec_ricevute_errore", exc) from None
    pagina = righe[:limite]
    return {"items": [_ricevuta(r) for r in pagina],
            "cursore": int(pagina[-1]["id"]) if len(righe) > limite else None}


@router.get("/ricevute/{ricevuta_id}/eml", summary="Messaggio grezzo di una ricevuta (evidenza)",
            response_class=Response)
async def eml_ricevuta(ricevuta_id: int, chi: dict[str, Any] = Depends(lettore_pec)) -> Response:
    if not 1 <= ricevuta_id <= 2**63 - 1:
        raise ErroreApi("NTF-404-PEC")
    filtro = " AND tenant_id = CAST(:ente AS uuid) AND esito = 'ASSOCIATA'" if chi["_ente"] else ""
    try:
        async with get_async_session() as s:
            riga = (await s.execute(text(
                "SELECT eml FROM public.pec_ricevute WHERE id = :id"  # noqa: S608 — costanti
                f"{filtro}"), {"id": ricevuta_id, "ente": chi["_ente"]})).first()
    except SQLAlchemyError as exc:
        raise _errore("pec_eml_errore", exc) from None
    if riga is None or riga[0] is None:
        raise ErroreApi("NTF-404-PEC")
    return Response(content=bytes(riga[0]), media_type="message/rfc822",
                    headers={"Content-Disposition":
                             f'attachment; filename="ricevuta-{ricevuta_id}.eml"',
                             "X-Content-Type-Options": "nosniff"})


__all__ = ["conservatore_pec", "lettore_pec", "router"]
