# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/conferme_decisione.py — conferma FIRMATA delle decisioni sensibili
del ciclo di vita (R17, VD-P-164; VD-C-120, VD-C-141).

Rotta (sessione personale della console: cookie del portale o lo stesso token Bearer; mai
chiavi API né token di canale → 403 ACC-403-CREDENZIALI):
  POST /api/v1/auth/conferme-decisione    conferma {azione, pratica, versione, impronta, ente}

Regole:
  - azione del proprio ruolo: le azioni del conservatore (approvazione ed esecuzione dello
    scarto, esecuzione del riversamento in uscita, ripresa) solo ai ruoli FISSI SUPER_ADMIN
    e RSC_CONSERVATORE (mai un ruolo personalizzato); quelle dell'ente (annullamento,
    rettifica) solo a una persona di QUELL'ente (``ente`` = ente del token);
  - conferma dell'identità RECENTE in QUESTA sessione: la riautenticazione di R12b (chiave in
    Redis legata all'impronta del token di sessione), oppure, se l'ente non ammette alcun
    metodo locale (solo SPID/CIE, dove la riautenticazione locale non esiste), l'accesso
    stesso fatto da meno di ``ACCESSO_RIAUTENTICAZIONE_MINUTI``;
  - ogni conferma dell'identità vale per UNA sola decisione: il gateway la CONSUMA con un
    ``SET NX`` atomico in Redis (chiave = sessione + istante della conferma); Redis non
    raggiungibile → 503, mai una conferma senza (fail-closed);
  - audit ``decisioni.conferma_firmata`` scritto e confermato PRIMA di restituire la firma;
  - firma Ed25519 con la chiave SOLO del gateway (``CONFERME_CHIAVE_PRIVATA``, ``kid`` per la
    rotazione; l'API di ciclo-vita e il lavoratore hanno solo le chiavi pubbliche); senza
    chiave → 503 ACC-503-CONFERMA-DECISIONE.

Il gateway non conosce le pratiche: firma ciò che la persona conferma; la corrispondenza con
la pratica (versione, contenuto) la controllano l'API di ciclo-vita e, prima di eseguire, il
lavoratore (``libs.shared.conferme_decisione.difetti``).
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth import credenziali_utente as cu
from app.auth.dependencies import get_current_user
from app.auth.fornitori import metodi_ammessi_ente
from app.config import settings
from libs.shared import conferme_decisione as cd
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_ente
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.security.audit import registra_audit
from libs.verumdoc_rules.parametri_servizio import (
    ACCESSO_RIAUTENTICAZIONE_MINUTI,
    METODO_PASSKEY,
    METODO_PASSWORD_TOTP,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth/conferme-decisione",
                   tags=["Conferme delle decisioni"])

RUOLI_CONSERVATORE = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})
CLAIM_RUOLO_PERSONALIZZATO = "rp"


class RichiestaConferma(BaseModel):
    model_config = ConfigDict(extra="forbid")
    azione: str = Field(..., max_length=40, description="Azione sensibile (catalogo VD-P-164)")
    pratica: str | None = Field(None, max_length=36,
                                description="Pratica; nulla per le richieste che la creano")
    versione: int = Field(..., ge=0, le=1_000_000, description="Versione attesa della pratica")
    impronta: str = Field(..., max_length=64,
                          description="SHA-256 del contenuto deciso (forma canonica)")
    ente: str = Field(..., max_length=36, description="Ente della pratica")


@lru_cache(maxsize=4)
def _chiave(testo: str) -> Any:
    return cd.chiave_privata(testo)


def chiave_configurata() -> tuple[str, Any]:
    """``(kid, chiave privata)``; senza chiave valida → 503 (nessuna conferma)."""
    kid = settings.conferme_kid.strip()
    try:
        if not kid:
            raise ValueError("kid assente")
        return kid, _chiave(settings.conferme_chiave_privata.get_secret_value())
    except ValueError as exc:
        logger.error("conferme_chiave_non_configurata", motivo=str(exc))
        raise ErroreApi("ACC-503-CONFERMA-DECISIONE") from None


def problema_avvio() -> str | None:
    """Motivo per cui le conferme non si possono firmare (None = configurate)."""
    try:
        if not settings.conferme_kid.strip():
            return "CONFERME_KID assente"
        _chiave(settings.conferme_chiave_privata.get_secret_value())
    except ValueError as exc:
        return f"CONFERME_CHIAVE_PRIVATA: {exc}"
    return None


def _uuid(valore: Any) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


def _autorizzata(body: RichiestaConferma, p: cu.Persona, user: dict[str, Any]) -> str | None:
    """None se l'azione è del ruolo e dell'ente di chi chiede, altrimenti il motivo."""
    ruolo = str(user.get("ruolo") or "")
    personalizzato = bool(user.get(CLAIM_RUOLO_PERSONALIZZATO))
    if body.azione in cd.AZIONI_CONSERVATORE:
        if ruolo not in RUOLI_CONSERVATORE or personalizzato:
            return "azione riservata ai ruoli fissi del conservatore"
        return None
    if ruolo in RUOLI_CONSERVATORE:
        return "azione dell'ente produttore"
    if body.ente != p.tenant_id:
        return "azione su un altro ente"
    return None


async def _finestra_minuti(tenant_id: str) -> int:
    from libs.shared.parametri import parametri

    return int((await parametri([ACCESSO_RIAUTENTICAZIONE_MINUTI], tenant_id))
               [ACCESSO_RIAUTENTICAZIONE_MINUTI])


async def _evento_identita(p: cu.Persona, user: dict[str, Any], adesso: datetime) -> str:
    """Identificativo della conferma dell'identità recente di questa sessione: la
    riautenticazione di R12b o, se l'ente non ha metodi locali, l'accesso recente."""
    async def _leggi(r: Any) -> str | None:
        grezzo = await r.get(cu._chiave("riaut", p))
        if not grezzo:
            return None
        dati = json.loads(grezzo)
        if dati.get("user_id") != p.user_id or not dati.get("il"):
            return None
        return f"riaut:{dati['il']}"

    evento = await cu._con_redis(_leggi)
    if evento is not None:
        return evento
    metodi = set(await metodi_ammessi_ente(p.tenant_id))
    if not metodi & {METODO_PASSKEY, METODO_PASSWORD_TOTP}:
        iat = user.get("iat")
        if isinstance(iat, int | float):
            eta = adesso.timestamp() - float(iat)
            if 0 <= eta <= await _finestra_minuti(p.tenant_id) * 60:
                return f"accesso:{int(iat)}"
    raise ErroreApi("ACC-403-RIAUTENTICAZIONE",
                    "Per confermare una decisione sensibile serve una conferma dell'identità "
                    "recente in questa sessione.")


async def _consuma(p: cu.Persona, evento: str, minuti: int) -> None:
    """Una conferma dell'identità vale per una sola decisione (SET NX atomico)."""
    async def _nx(r: Any) -> bool:
        nome = f"{cu._chiave('conferma_usata', p)}:{evento}"
        return bool(await r.set(nome, "1", nx=True, ex=minuti * 60 + 120))

    if not await cu._con_redis(_nx):
        raise ErroreApi("ACC-403-RIAUTENTICAZIONE",
                        "La conferma dell'identità è già stata usata per un'altra decisione: "
                        "confermare di nuovo l'identità.")


@router.post("", summary="Conferma firmata di una decisione sensibile del ciclo di vita")
async def conferma(
    body: RichiestaConferma,
    chi: cu.Chiamante,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    pratica = None if body.pratica is None else _uuid(body.pratica)
    ente = _uuid(body.ente)
    if (body.pratica is not None and pratica is None) or ente is None:
        raise ErroreApi("ACC-422-CONFERMA-DECISIONE", "Pratica o ente non validi.")
    richiesta = cd.Richiesta(azione=body.azione, pratica=pratica, versione=body.versione,
                             impronta=body.impronta, ente=ente)
    motivo = cd.controlla_richiesta(richiesta)
    if motivo is not None:
        raise ErroreApi("ACC-422-CONFERMA-DECISIONE", motivo[:1].upper() + motivo[1:] + ".")
    corpo = body.model_copy(update={"ente": ente})
    blocco = _autorizzata(corpo, chi, user)
    if blocco is not None:
        raise ErroreApi("ACC-403-CONFERMA-DECISIONE", blocco[:1].upper() + blocco[1:] + ".")
    kid, privata = chiave_configurata()
    adesso = datetime.now(UTC)
    evento = await _evento_identita(chi, user, adesso)
    minuti = await _finestra_minuti(chi.tenant_id)
    await _consuma(chi, evento, minuti)
    nonce = secrets.token_hex(16)
    firmata = cd.firma(richiesta, utente=chi.user_id, kid=kid, privata=privata, adesso=adesso,
                       durata_secondi=settings.conferme_durata_secondi, nonce=nonce)
    # audit PRIMA di restituire la firma: senza la sua voce nessuna conferma esce
    try:
        with ambito_ente(chi.tenant_id):
            async with get_async_session() as session:
                await registra_audit(session, evento="decisioni.conferma_firmata",
                                     tenant_id=chi.tenant_id, user_id=chi.user_id,
                                     dettagli={"azione": richiesta.azione,
                                               "pratica": richiesta.pratica,
                                               "ente": richiesta.ente,
                                               "versione": richiesta.versione,
                                               "impronta": richiesta.impronta,
                                               "nonce": nonce, "kid": kid,
                                               "identita": evento.split(":", 1)[0]})
    except SQLAlchemyError as exc:
        raise errore_registrato("ACC-503", exc, evento="conferme_audit_errore") from None
    logger.info("conferma_decisione_firmata", azione=richiesta.azione,
                pratica=richiesta.pratica, user_id=chi.user_id, kid=kid)
    return {"conferma": firmata, "kid": kid,
            "scadenza_secondi": settings.conferme_durata_secondi}


__all__ = ["chiave_configurata", "conferma", "problema_avvio", "router"]
