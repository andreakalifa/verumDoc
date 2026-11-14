# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — fornitori d'identità (conservatore) e metodi d'accesso dell'ente (R10, D08).

Riferimenti: decisione D08 (VD-C-069); migrazione 20261102_049
(``fornitori_identita``); gateway ``app/auth/fornitori.py`` (chi li usa).

  GET /api/v1/onboarding/accesso/fornitori            i tre fornitori (registrati o no)
  PUT /api/v1/onboarding/accesso/fornitori/{tipo}     registra o modifica (solo conservatore)
  GET /api/v1/onboarding/tenant/{tenant_id}/accesso   metodi abilitati dal conservatore e
                                                      ammessi dall'ente, validità degli inviti

Il fornitore SPID/CIE porta i parametri dell'aggregatore OIDC (indirizzo,
client_id, emittente, JWKS, livello minimo 2 o 3); il segreto del cliente OIDC
resta SOLO nella variabile d'ambiente del gateway (mai nel database né qui).
PASSKEY: identificativo della parte (dominio della console) e origini ammesse.
PASSWORD_TOTP: nessun parametro. Fuori da development/test gli indirizzi sono
https. Ogni modifica: salvataggio con la versione letta e voce d'audit con
prima/dopo nella stessa transazione. La scelta dei metodi dell'ente si fa con
la console delle impostazioni (``ACCESSO_METODI_AMMESSI``, verso «più sicuro»),
che ha già bozza, anteprima, applicazione e audit.
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import utente_da_authorization, verify_access_token
from app.routers.utenti import Chiamante, _uuid_o_404
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.parametri import parametri
from libs.shared.security.audit import registra_audit
from libs.shared.security.revoca import dipendenza_non_revocato
from libs.verumdoc_rules.parametri_servizio import (
    ACCESSO_METODI_AMMESSI,
    INVITO_VALIDITA_ORE,
    METODI_ACCESSO,
    PASSWORD_LUNGHEZZA_MINIMA,
)
from libs.verumdoc_rules.permessi import RUOLI_CONSERVATORE

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Accesso: fornitori e metodi"], responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token))])

NOMI = {"SPID_CIE": "SPID e CIE (aggregatore)", "PASSKEY": "Passkey",
        "PASSWORD_TOTP": "Password e codice a tempo"}
_DOMINIO = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9-]{1,63})*$")


def _https_obbligatorio() -> bool:
    return os.environ.get("ENV", "production").strip().lower() not in ("development", "test")


def _conservatore(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    caller = utente_da_authorization(authorization)
    if caller.get("ruolo") not in RUOLI_CONSERVATORE or caller.get("rp"):
        raise ErroreApi("AUTH-010", "I fornitori d'identità li registra il conservatore")
    return caller


def _indirizzo(valore: Any, campo: str, motivi: list[str]) -> str:
    testo = str(valore or "").strip().rstrip("/")
    ammessi = ("https://",) if _https_obbligatorio() else ("https://", "http://")
    if not testo.startswith(ammessi) or len(testo) > 500 or any(c.isspace() for c in testo):
        schema = "https" if _https_obbligatorio() else "http(s)"
        motivi.append(f"{campo}: serve un indirizzo {schema}")
    return testo


#: R13b (VD-C-069): profilo OIDC dell'aggregatore (docs/architettura/accesso_spid_cie.md)
METODI_CLIENTE = ("client_secret_post", "client_secret_basic", "private_key_jwt")
PROMPT_AMMESSI = ("", "login", "consent", "consent login")


def _profilo_oidc(conf: dict[str, Any], motivi: list[str], *, jwks: str) -> dict[str, Any]:
    """Campi facoltativi del profilo OIDC; predefiniti = comportamento di prima."""
    metodo = str(conf.get("metodo_autenticazione") or "client_secret_post")
    if metodo not in METODI_CLIENTE:
        motivi.append("Autenticazione del cliente: " + ", ".join(METODI_CLIENTE))
    prompt = str(conf.get("prompt") or "")
    if prompt not in PROMPT_AMMESSI:
        motivi.append("prompt: vuoto, login, consent o «consent login»")
    profilo: dict[str, Any] = {
        "metodo_autenticazione": metodo, "prompt": prompt,
        "request_object": conf.get("request_object") is True,
        "attributi_da_userinfo": conf.get("attributi_da_userinfo") is True}
    for campo, nome in (("authorize_url", "Endpoint di autorizzazione"),
                        ("token_url", "Endpoint del token"), ("userinfo_url", "Endpoint UserInfo")):
        if conf.get(campo):
            profilo[campo] = _indirizzo(conf.get(campo), nome, motivi)
    federazione = conf.get("federazione")
    if federazione:
        if not isinstance(federazione, dict):
            motivi.append("federazione: oggetto con trust_anchor, chiavi_trust_anchor, entita_op")
        else:
            chiavi = federazione.get("chiavi_trust_anchor")
            if not isinstance(chiavi, dict) or not isinstance(chiavi.get("keys"), list) or \
                    not all(isinstance(k, dict) and k.get("kid") for k in chiavi["keys"]):
                motivi.append("federazione: chiavi del Trust Anchor in formato JWKS (con kid)")
                chiavi = {}
            # R16 (VD-C-173): trust mark richiesti (tipo → emittenti ammessi) e verifica
            # dello stato (revoca) all'endpoint dell'emittente
            richiesti = federazione.get("trust_marks_richiesti") or {}
            if not isinstance(richiesti, dict) or not all(
                    isinstance(t, str) and t.startswith("https://") and isinstance(e, list)
                    and e and all(isinstance(x, str) and x.startswith("https://") for x in e)
                    for t, e in richiesti.items()):
                motivi.append("federazione: trust_marks_richiesti come {tipo https: "
                              "[emittenti https]}")
                richiesti = {}
            profilo["federazione"] = {
                "trust_anchor": _indirizzo(federazione.get("trust_anchor"), "Trust Anchor",
                                           motivi),
                "entita_op": _indirizzo(federazione.get("entita_op"), "Entità dell'OP", motivi),
                "chiavi_trust_anchor": chiavi,
                "trust_marks_richiesti": richiesti,
                "verifica_stato_trust_marks":
                    federazione.get("verifica_stato_trust_marks") is True}
    elif not jwks:
        motivi.append("Chiavi pubbliche (JWKS) obbligatorie senza federazione")
    return profilo


def valida_configurazione(tipo: str, conf: dict[str, Any]) -> dict[str, Any]:
    """Configurazione normalizzata o ACC-422-FORNITORE con i motivi. Mai segreti."""
    motivi: list[str] = []
    vietate = [k for k in conf if "secret" in k.lower() or "segreto" in k.lower()
               or "password" in k.lower()]
    if vietate:
        motivi.append("i segreti non si salvano nella console: restano nelle variabili "
                      "d'ambiente del gateway")
    if tipo == "SPID_CIE":
        jwks = conf.get("jwks_url")
        pulita = {"aggregatore_url": _indirizzo(conf.get("aggregatore_url"),
                                                "Indirizzo dell'aggregatore", motivi),
                  "issuer": _indirizzo(conf.get("issuer"), "Emittente", motivi),
                  # R13b: con la federazione le chiavi vengono dalla trust chain
                  "jwks_url": _indirizzo(jwks, "Chiavi pubbliche (JWKS)", motivi)
                  if jwks or not conf.get("federazione") else "",
                  "client_id": str(conf.get("client_id") or "").strip()}
        if not pulita["client_id"] or len(pulita["client_id"]) > 200:
            motivi.append("Identificativo del cliente (client_id) obbligatorio")
        livello = conf.get("livello_minimo", 2)
        if livello not in (2, 3):
            motivi.append("Livello minimo: 2 o 3")
        pulita["livello_minimo"] = livello
        pulita.update(_profilo_oidc(conf, motivi, jwks=pulita["jwks_url"]))
    elif tipo == "PASSKEY":
        rp = str(conf.get("rp_id") or "").strip().lower()
        if rp and not _DOMINIO.match(rp):
            motivi.append("Dominio della passkey non valido (es. app.esempio.it)")
        origini = conf.get("origini") or []
        if not isinstance(origini, list) or len(origini) > 10:
            motivi.append("Origini: un elenco di al più 10 indirizzi")
            origini = []
        pulita = {"rp_id": rp,
                  "origini": [_indirizzo(o, "Origine", motivi) for o in origini]}
    else:
        pulita = {}
    if motivi:
        raise ErroreApi("ACC-422-FORNITORE", "; ".join(motivi), dettagli={"motivi": motivi})
    return pulita


def _json(tipo: str, riga: Any | None) -> dict[str, Any]:
    if riga is None:
        return {"tipo": tipo, "nome": NOMI[tipo], "registrato": False, "attivo": False,
                "configurazione": {}, "versione": 0, "aggiornato_il": None}
    return {"tipo": tipo, "nome": riga["nome"], "registrato": True, "attivo": riga["attivo"],
            "configurazione": dict(riga["configurazione"] or {}), "versione": riga["versione"],
            "aggiornato_il": riga["updated_at"].isoformat()}


@router.get("/api/v1/onboarding/accesso/fornitori", summary="Fornitori d'identità")
async def elenco_fornitori(caller: dict[str, Any] = Depends(_conservatore)) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            righe = {r["tipo"]: r for r in (await session.execute(text(
                "SELECT * FROM public.fornitori_identita"))).mappings().all()}
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="fornitori_elenco_errore") from None
    return {"items": [_json(t, righe.get(t)) for t in METODI_ACCESSO]}


class FornitoreModifica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: str = Field(..., min_length=3, max_length=200)
    attivo: bool
    configurazione: dict[str, Any] = Field(default_factory=dict)
    versione: int = Field(0, ge=0, description="Versione letta (0 = nuovo)")


@router.put("/api/v1/onboarding/accesso/fornitori/{tipo}", summary="Registra o modifica")
async def modifica_fornitore(tipo: Literal["SPID_CIE", "PASSKEY", "PASSWORD_TOTP"],
                             body: FornitoreModifica,
                             caller: dict[str, Any] = Depends(_conservatore)) -> dict[str, Any]:
    conf = valida_configurazione(tipo, body.configurazione)
    autore = _uuid(caller.get("user_id"))
    try:
        async with get_async_session() as session:
            prima = (await session.execute(text(
                "SELECT * FROM public.fornitori_identita WHERE tipo = :t FOR UPDATE"),
                {"t": tipo})).mappings().one_or_none()
            if (prima["versione"] if prima else 0) != body.versione:
                raise ErroreApi("CFG-409", "Il fornitore è stato modificato da un'altra persona")
            import json

            if prima is None:
                dopo = (await session.execute(text(
                    "INSERT INTO public.fornitori_identita (tipo, nome, attivo, configurazione, "
                    " aggiornato_da) VALUES (:t, :n, :a, CAST(:c AS jsonb), :u) RETURNING *"),
                    {"t": tipo, "n": body.nome.strip(), "a": body.attivo,
                     "c": json.dumps(conf), "u": autore})).mappings().one()
            else:
                dopo = (await session.execute(text(
                    "UPDATE public.fornitori_identita SET nome = :n, attivo = :a, "
                    " configurazione = CAST(:c AS jsonb), versione = versione + 1, "
                    " aggiornato_da = :u, updated_at = now() WHERE tipo = :t RETURNING *"),
                    {"t": tipo, "n": body.nome.strip(), "a": body.attivo,
                     "c": json.dumps(conf), "u": autore})).mappings().one()
            await registra_audit(session, evento="accesso.fornitore_modificato",
                                 user_id=str(autore) if autore else None,
                                 dettagli={"tipo": tipo,
                                           "prima": _json(tipo, prima) if prima else None,
                                           "dopo": _json(tipo, dopo)})
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="fornitore_modifica_errore") from None
    logger.info("fornitore_identita_modificato", tipo=tipo, attivo=body.attivo)
    return _json(tipo, dopo)


@router.get("/api/v1/onboarding/tenant/{tenant_id}/accesso",
            summary="Metodi d'accesso abilitati e ammessi per l'ente")
async def accesso_ente(tenant_id: str, caller: Chiamante) -> dict[str, Any]:
    tid = _uuid_o_404(tenant_id, "Tenant non trovato")
    try:
        async with get_async_session() as session:
            attivi = set((await session.execute(text(
                "SELECT tipo FROM public.fornitori_identita WHERE attivo"))).scalars().all())
            registrati = set((await session.execute(text(
                "SELECT tipo FROM public.fornitori_identita"))).scalars().all())
    except SQLAlchemyError as exc:
        raise errore_registrato("ONB-503", exc, evento="accesso_ente_errore") from None
    if "SPID_CIE" not in registrati:
        attivi.add("SPID_CIE")  # come prima (§8.14): l'aggregatore dalle variabili del gateway
    valori = await parametri([ACCESSO_METODI_AMMESSI, INVITO_VALIDITA_ORE,
                              PASSWORD_LUNGHEZZA_MINIMA], tid)
    generale = await parametri([ACCESSO_METODI_AMMESSI], None)
    ammessi = [m for m in valori[ACCESSO_METODI_AMMESSI] if m in attivi]
    return {
        "metodi_con_fornitore_attivo": [m for m in METODI_ACCESSO if m in attivi],
        "metodi_del_conservatore": [m for m in generale[ACCESSO_METODI_AMMESSI] if m in attivi],
        "metodi_ammessi": ammessi,
        "invito_validita_ore": int(valori[INVITO_VALIDITA_ORE]),
        "password_lunghezza_minima": int(valori[PASSWORD_LUNGHEZZA_MINIMA]),
        "nomi": NOMI,
    }


def _uuid(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


__all__ = ["router", "valida_configurazione"]
