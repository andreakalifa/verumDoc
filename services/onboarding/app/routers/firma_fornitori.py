# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — registro dei fornitori di firma, sigillo e marca temporale (R13a).

Riferimenti: VD-C-068, decisioni VD-P-05/07 (D-preambolo: fornitori
intercambiabili registrati dal conservatore con priorità, ripiego e prova);
migrazione 20261107_054 (``fornitori_firma``); ``libs.shared.firma_qualificata``
(campi e segreti, la stessa validazione del servizio di firma).

  GET  /api/v1/onboarding/firma/fornitori                 registro, tipi e segreti attesi
  PUT  /api/v1/onboarding/firma/fornitori/{codice}        registra o modifica
  GET  /api/v1/onboarding/firma/fornitori/stato           stato e scadenze (servizio di firma)
  POST /api/v1/onboarding/firma/fornitori/{codice}/prova  firma di prova verificata
  GET  /api/v1/onboarding/firma/credenziali-personali     R16: credenziali personali
  PUT  /api/v1/onboarding/firma/credenziali-personali/{user_id}
                                                          R16: registra, cambia, disattiva

R16 (VD-C-166, VD-P-140): la credenziale di firma PERSONALE di una persona del
conservatore (``credentialID`` presso un fornitore CSC del registro e impronta ATTESA del
suo certificato) si registra qui, senza segreti: PIN e OTP li dà la persona a ogni firma.
Una sola attiva per persona; versione attesa; audit con prima e dopo.

Solo i ruoli FISSI del conservatore con il permesso mai delegabile
``firma_fornitori:gestisci`` (mai un ruolo personalizzato, mai una chiave API).
I segreti non passano mai di qui (restano nel Secret del servizio di firma,
``FIRMA_SEGRETI_JSON``); il fornitore di SVILUPPO non si salva in produzione.
Ogni modifica e ogni prova hanno la loro voce d'audit (la modifica nella stessa
transazione, con prima e dopo). Stato e prova si inoltrano al servizio di firma
con il token della persona, che lo riverifica (stesso permesso).
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import utente_da_authorization, verify_access_token
from app.config import settings
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.firma_qualificata.configurazione import (
    CODICE_FORNITORE,
    SEGRETI_PER_TIPO,
    TIPI_PER_RUOLO,
    ConfigurazioneFornitoreNonValida,
    valida_configurazione,
)
from libs.shared.security.audit import registra_audit
from libs.shared.security.permessi import ha_permesso
from libs.shared.security.revoca import dipendenza_non_revocato
from libs.verumdoc_rules.permessi import FIRMA_FORNITORI_GESTISCI, RUOLI_CONSERVATORE

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/onboarding/firma", tags=["Firma: fornitori"],
                   responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])


#: Trasporto HTTP verso il servizio di firma (solo test: None = rete)
TRASPORTO: httpx.AsyncBaseTransport | None = None


def _produzione() -> bool:
    return os.environ.get("ENV", settings.env).strip().lower() == "production"


def conservatore_firma(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Ruolo fisso del conservatore con il permesso mai delegabile (§31 R10 (2))."""
    caller = utente_da_authorization(authorization)
    if caller.get("ruolo") not in RUOLI_CONSERVATORE or caller.get("auth_method") == "api_key" \
            or not ha_permesso(caller, FIRMA_FORNITORI_GESTISCI):
        raise ErroreApi("FIRMA-403-FORNITORI",
                        "I fornitori di firma e marca li gestisce il conservatore")
    caller["_authorization"] = authorization
    return caller


def _json(riga: Any) -> dict[str, Any]:
    return {"codice": riga["codice"], "ruolo": riga["ruolo"], "tipo": riga["tipo"],
            "nome": riga["nome"], "attivo": riga["attivo"], "priorita": riga["priorita"],
            "configurazione": dict(riga["configurazione"] or {}), "versione": riga["versione"],
            "aggiornato_il": riga["updated_at"].isoformat()}


def _tipi() -> dict[str, Any]:
    return {"ruoli": TIPI_PER_RUOLO,
            "segreti": {t: [{"nome": n, "obbligatorio": o} for n, o in s]
                        for t, s in SEGRETI_PER_TIPO.items()},
            "sviluppo_ammesso": not _produzione()}


@router.get("/fornitori", summary="Registro dei fornitori di firma e marca")
async def elenco(caller: dict[str, Any] = Depends(conservatore_firma)) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                "SELECT * FROM public.fornitori_firma ORDER BY ruolo, priorita, codice"
            ))).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="firma_fornitori_elenco_errore") from None
    return {"items": [_json(r) for r in righe], **_tipi()}


class FornitoreModifica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruolo: str = Field(..., pattern="^(FIRMA|MARCA)$")
    tipo: str = Field(..., pattern="^(CSC|PKCS11|SVILUPPO|RFC3161)$")
    nome: str = Field(..., min_length=3, max_length=200)
    attivo: bool
    priorita: int = Field(10, ge=1, le=100,
                          description="Ordine di prova (1 = primo); il successivo è il ripiego")
    configurazione: dict[str, Any] = Field(default_factory=dict)
    versione: int = Field(0, ge=0, description="Versione letta (0 = nuovo)")


@router.put("/fornitori/{codice}", summary="Registra o modifica un fornitore di firma o marca")
async def modifica(codice: str, body: FornitoreModifica,
                   caller: dict[str, Any] = Depends(conservatore_firma)) -> dict[str, Any]:
    if not CODICE_FORNITORE.match(codice):
        raise ErroreApi("FIRMA-422-CONFIGURAZIONE",
                        "Codice: minuscole, cifre e trattini (es. csc-principale)")
    try:
        conf = valida_configurazione(body.ruolo, body.tipo, body.configurazione,
                                     produzione=_produzione())
    except ConfigurazioneFornitoreNonValida as exc:
        raise ErroreApi("FIRMA-422-CONFIGURAZIONE", "; ".join(exc.motivi),
                        dettagli={"motivi": exc.motivi}) from None
    autore = _uuid(caller.get("user_id"))
    try:
        async with get_async_session() as session:
            prima = (await session.execute(text(
                "SELECT * FROM public.fornitori_firma WHERE codice = :c FOR UPDATE"),
                {"c": codice})).mappings().one_or_none()
            if (prima["versione"] if prima else 0) != body.versione:
                raise ErroreApi("CFG-409", "Il fornitore è stato modificato da un'altra persona")
            if prima is not None and (prima["ruolo"], prima["tipo"]) != (body.ruolo, body.tipo):
                raise ErroreApi("FIRMA-422-CONFIGURAZIONE",
                                "Ruolo e tipo di un fornitore registrato non cambiano: "
                                "disattivarlo e registrarne uno nuovo")
            valori = {"c": codice, "r": body.ruolo, "t": body.tipo, "n": body.nome.strip(),
                      "a": body.attivo, "p": body.priorita, "conf": json.dumps(conf),
                      "u": autore}
            if prima is None:
                dopo = (await session.execute(text(
                    "INSERT INTO public.fornitori_firma (codice, ruolo, tipo, nome, attivo, "
                    " priorita, configurazione, aggiornato_da) VALUES (:c, :r, :t, :n, :a, :p, "
                    " CAST(:conf AS jsonb), :u) RETURNING *"), valori)).mappings().one()
            else:
                dopo = (await session.execute(text(
                    "UPDATE public.fornitori_firma SET nome = :n, attivo = :a, priorita = :p, "
                    " configurazione = CAST(:conf AS jsonb), versione = versione + 1, "
                    " aggiornato_da = :u, updated_at = now() WHERE codice = :c RETURNING *"),
                    valori)).mappings().one()
            await registra_audit(session, evento="firma.fornitore_modificato",
                                 user_id=str(autore) if autore else None,
                                 dettagli={"codice": codice,
                                           "prima": _json(prima) if prima else None,
                                           "dopo": _json(dopo)})
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="firma_fornitore_modifica_errore") from None
    logger.info("fornitore_firma_modificato", codice=codice, attivo=body.attivo,
                tipo=body.tipo)
    return _json(dopo)


async def _inoltra(metodo: str, percorso: str, authorization: str) -> dict[str, Any]:
    """Richiesta al servizio di firma con il token della persona (che la riverifica)."""
    try:
        async with httpx.AsyncClient(base_url=settings.signature_url.rstrip("/"),
                                     timeout=settings.signature_timeout_seconds,
                                     transport=TRASPORTO) as client:
            risposta = await client.request(metodo, percorso,
                                            headers={"Authorization": authorization})
    except httpx.HTTPError as exc:
        raise ErroreApi("FIRMA-502-FORNITORE",
                        f"servizio di firma non raggiungibile: {type(exc).__name__}") from None
    try:
        corpo = risposta.json()
    except ValueError:
        corpo = {}
    if risposta.status_code != 200:
        codice = str(corpo.get("codice") or "FIRMA-502-FORNITORE") if isinstance(corpo, dict) \
            else "FIRMA-502-FORNITORE"
        messaggio = str(corpo.get("detail") or corpo.get("messaggio") or "errore del servizio "
                        "di firma") if isinstance(corpo, dict) else "errore del servizio di firma"
        raise ErroreApi(codice if codice.startswith("FIRMA-") else "FIRMA-502-FORNITORE",
                        messaggio[:500], dettagli={"http": risposta.status_code})
    return corpo if isinstance(corpo, dict) else {}


@router.get("/fornitori/stato", summary="Stato, certificati e scadenze dei fornitori")
async def stato(caller: dict[str, Any] = Depends(conservatore_firma)) -> dict[str, Any]:
    return await _inoltra("GET", "/api/v1/signature/fornitori", caller["_authorization"])


@router.post("/fornitori/{codice}/prova",
             summary="Prova di connessione con firma (o marca) di prova verificata")
async def prova(codice: str, caller: dict[str, Any] = Depends(conservatore_firma)
                ) -> dict[str, Any]:
    if not CODICE_FORNITORE.match(codice):
        raise ErroreApi("FIRMA-404-FORNITORE", "Fornitore non registrato")
    autore = _uuid(caller.get("user_id"))
    esito: dict[str, Any] | None = None
    errore: ErroreApi | None = None
    try:
        esito = await _inoltra("POST", f"/api/v1/signature/fornitori/{codice}/prova",
                               caller["_authorization"])
    except ErroreApi as exc:
        errore = exc
    try:
        async with get_async_session() as session:
            await registra_audit(session, evento="firma.fornitore_provato",
                                 user_id=str(autore) if autore else None,
                                 dettagli={"codice": codice,
                                           "esito": "OK" if esito else "ERRORE",
                                           "errore": errore.codice if errore else None})
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="firma_prova_audit_errore") from None
    if errore is not None:
        raise errore
    assert esito is not None
    return esito


# ── R16 (VD-C-166): credenziali di firma personali ────────────────────────

def _credenziale(r: Any) -> dict[str, Any]:
    return {"id": str(r["id"]), "user_id": str(r["user_id"]),
            "fornitore_codice": r["fornitore_codice"], "credential_id": r["credential_id"],
            "certificato_sha256": r["certificato_sha256"], "descrizione": r["descrizione"],
            "attiva": r["attiva"], "versione": r["versione"],
            "aggiornato_il": r["updated_at"].isoformat()}


@router.get("/credenziali-personali", summary="Credenziali di firma personali (R16)")
async def credenziali(caller: dict[str, Any] = Depends(conservatore_firma)) -> dict[str, Any]:
    from libs.shared.security.ambito_dati import ambito_conservatore

    try:
        with ambito_conservatore("credenziali di firma personali", registra=False):
            async with get_async_session() as session:
                righe = (await session.execute(text(
                    "SELECT c.*, u.nome, u.cognome, u.email, u.ruolo::text AS ruolo "
                    "FROM public.credenziali_firma_personali c "
                    "JOIN public.users u ON u.id = c.user_id ORDER BY c.attiva DESC, "
                    "u.cognome, u.nome, c.updated_at DESC LIMIT 500"))).mappings().all()
                persone = (await session.execute(text(
                    "SELECT id, nome, cognome, email, ruolo::text AS ruolo FROM public.users "
                    "WHERE ruolo::text = ANY(CAST(:r AS text[])) AND attivo "
                    "ORDER BY cognome, nome LIMIT 200"),
                    {"r": sorted(RUOLI_CONSERVATORE)})).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="credenziali_personali_errore") from None
    return {"items": [{**_credenziale(r), "persona": f"{r['nome']} {r['cognome']}",
                       "email": r["email"], "ruolo": r["ruolo"]} for r in righe],
            "persone": [{"user_id": str(p["id"]), "persona": f"{p['nome']} {p['cognome']}",
                         "email": p["email"], "ruolo": p["ruolo"]} for p in persone]}


class CredenzialeModifica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fornitore_codice: str = Field(..., min_length=2, max_length=40)
    credential_id: str = Field(..., min_length=1, max_length=200)
    certificato_sha256: str = Field(..., pattern="^[0-9a-fA-F]{64}$",
                                    description="SHA-256 del certificato DER della persona")
    descrizione: str | None = Field(None, max_length=200)
    attiva: bool = True
    versione: int = Field(0, ge=0, description="Versione letta della credenziale attiva "
                                               "(0 = nessuna)")


@router.put("/credenziali-personali/{user_id}",
            summary="Registra, cambia o disattiva la credenziale personale di una persona")
async def modifica_credenziale(user_id: str, body: CredenzialeModifica,
                               caller: dict[str, Any] = Depends(conservatore_firma)
                               ) -> dict[str, Any]:
    from libs.shared.security.ambito_dati import ambito_conservatore

    persona = _uuid(user_id)
    if persona is None:
        raise ErroreApi("ONB-404", "Persona non trovata")
    autore = _uuid(caller.get("user_id"))
    try:
        with ambito_conservatore("credenziale di firma personale", registra=False):
            async with get_async_session() as session:
                utente = (await session.execute(text(
                    "SELECT ruolo::text AS ruolo, attivo FROM public.users WHERE id = :u"),
                    {"u": persona})).mappings().one_or_none()
                if utente is None or utente["ruolo"] not in RUOLI_CONSERVATORE:
                    raise ErroreApi("FIRMA-422-CONFIGURAZIONE", "La firma personale è delle "
                                    "persone del conservatore (ruoli fissi)")
                fornitore = (await session.execute(text(
                    "SELECT ruolo, tipo FROM public.fornitori_firma WHERE codice = :c"),
                    {"c": body.fornitore_codice})).mappings().one_or_none()
                if fornitore is None or (fornitore["ruolo"], fornitore["tipo"]) != ("FIRMA",
                                                                                   "CSC"):
                    raise ErroreApi("FIRMA-422-CONFIGURAZIONE", "Il fornitore deve essere un "
                                    "fornitore di firma CSC del registro")
                prima = (await session.execute(text(
                    "SELECT * FROM public.credenziali_firma_personali WHERE user_id = :u "
                    "AND attiva FOR UPDATE"), {"u": persona})).mappings().one_or_none()
                if (prima["versione"] if prima else 0) != body.versione:
                    raise ErroreApi("CFG-409", "La credenziale è stata modificata da un'altra "
                                               "persona")
                valori = {"u": persona, "f": body.fornitore_codice,
                          "c": body.credential_id.strip(),
                          "h": body.certificato_sha256.lower(), "d": body.descrizione,
                          "a": autore}
                if prima is not None:
                    # mai riscrivere una credenziale usata: si disattiva e se ne crea una nuova
                    await session.execute(text(
                        "UPDATE public.credenziali_firma_personali SET attiva = false, "
                        " aggiornato_da = :a, updated_at = now() WHERE id = :id"),
                        {"a": autore, "id": prima["id"]})
                dopo = None
                if body.attiva:
                    dopo = (await session.execute(text(
                        "INSERT INTO public.credenziali_firma_personali (user_id, "
                        " fornitore_codice, credential_id, certificato_sha256, descrizione, "
                        " versione, aggiornato_da) VALUES (:u, :f, :c, :h, :d, :v, :a) "
                        "RETURNING *"), {**valori, "v": (prima["versione"] + 1) if prima else 1}
                    )).mappings().one()
                await registra_audit(session, evento="firma.credenziale_personale_modificata",
                                     user_id=str(autore) if autore else None,
                                     dettagli={"persona": str(persona),
                                               "prima": _credenziale(prima) if prima else None,
                                               "dopo": _credenziale(dopo) if dopo else None})
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="credenziale_personale_errore") from None
    logger.info("credenziale_personale_modificata", persona=str(persona)[:8],
                attiva=body.attiva)
    return _credenziale(dopo) if dopo else {"user_id": str(persona), "attiva": False}


def _uuid(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


__all__ = ["conservatore_firma", "router"]
