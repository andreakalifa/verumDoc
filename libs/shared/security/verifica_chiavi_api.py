# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/verifica_chiavi_api.py — verifica condivisa delle chiavi API.

Riferimenti: VD-C-099 (R03, agente b4); REGOLE_SVILUPPO §19.8 (API
macchina-macchina), §14.3 (cliente non determinabile → negato), §18 (errori dal
catalogo). Prima la chiave valeva solo sul gateway (che la trasformava in un
token interno): distribution, dashboard-api e notification volevano un Bearer e
un sistema versante non poteva cercare, elencare i versamenti o chiedere un PdD.

Una sola verifica per tutti i servizi pubblici:

  - forma della chiave e intestazioni accettate (``chiave_da_intestazioni``):
      ``X-Api-Key: vd_<id>_<segreto>``  oppure
      ``X-Api-Key-Id: vd_<id>`` + ``X-Api-Key-Secret: <chiave completa o solo segreto>``;
  - riga ``api_keys`` cercata per identificativo (indice ``idx_apikeys_prefix``,
    migrazione 20261021_018), impronta HMAC-SHA256 con il pepper confrontata a
    tempo costante (``libs.shared.security.chiavi_api``);
  - stesse regole ovunque: chiave revocata o inesistente → AUTH-001, scaduta →
    AUTH-002, ente SOSPESO/DISATTIVATO → AUTH-001, pepper non configurato →
    AUTH-030, database non raggiungibile → AUTH-503 (fail-closed);
  - ``ultimo_uso`` aggiornato al più una volta ogni ``intervallo_ultimo_uso``
    secondi per chiave (niente scrittura a ogni richiesta).

Il risultato (``IdentitaChiaveApi.payload()``) ha la stessa forma del payload
di un JWT verificato (``tenant_id``, ``user_id``, ``ruolo`` = ``API_SYSTEM``,
``scopes``): i servizi lo trattano come un utente, e la revoca
(``libs.shared.security.revoca``) si applica allo stesso modo (utente
responsabile disattivato o spostato, ente bloccato).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from libs.shared.security.chiavi_api import (
    PREFISSO_CHIAVE,
    ChiaveApiMalformata,
    identificativo_da_chiave,
    normalizza_identificativo,
    verifica_chiave,
)
from libs.shared.security.revoca import RUOLO_CHIAVE_API, STATI_ENTE_BLOCCATI

logger = structlog.get_logger(__name__)

#: Permessi massimi di una chiave API (ruolo API_SYSTEM): versare, leggere.
#: La tabella dei ruoli del gateway (app/auth/ruoli.py) usa questo valore.
SCOPE_CHIAVE_API: tuple[str, ...] = ("versamento:ud", "versamento:doc", "read")

#: Metodo d'autenticazione scritto nel payload (log e audit).
METODO_CHIAVE_API = "api_key"

#: Intervallo minimo fra due aggiornamenti di ``api_keys.ultimo_uso`` (secondi).
INTERVALLO_ULTIMO_USO_PREDEFINITO = 60


class ChiaveApiRifiutata(Exception):
    """Chiave non valida. ``codice``: AUTH-001 (non valida o revocata) o
    AUTH-002 (scaduta). ``motivo`` è solo per i log, mai per il client."""

    def __init__(self, codice: str, motivo: str) -> None:
        super().__init__(f"{codice}: {motivo}")
        self.codice = codice
        self.motivo = motivo


class ChiaviApiNonDisponibili(Exception):
    """Pepper non configurato in questo ambiente (AUTH-030)."""


class VerificaChiaveNonEseguibile(Exception):
    """Il database non ha risposto: la chiave non si accetta (AUTH-503)."""


@dataclass(frozen=True)
class IdentitaChiaveApi:
    """Chi si presenta con la chiave: ente, utente responsabile, permessi."""

    api_key_id: str
    tenant_id: str
    user_id: str
    scopes: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        """Payload nella forma di un JWT verificato (ruolo API_SYSTEM)."""
        return {
            "sub": f"chiave:{self.api_key_id}",
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "ruolo": RUOLO_CHIAVE_API,
            "scopes": list(self.scopes),
            "api_key_id": self.api_key_id,
            "auth_method": METODO_CHIAVE_API,
        }


def chiave_da_intestazioni(
    x_api_key: str | None,
    x_api_key_id: str | None,
    x_api_key_secret: str | None,
) -> str | None:
    """
    Chiave completa dalle intestazioni ricevute, o None se la richiesta non ne
    porta. ``ChiaveApiRifiutata`` (AUTH-001) se le intestazioni ci sono ma non
    formano una chiave (forma errata, identificativo diverso da quello della
    chiave, segreto senza identificativo).
    """
    if x_api_key:
        valore, identificativo = x_api_key, x_api_key_id
    elif x_api_key_secret:
        valore, identificativo = x_api_key_secret, x_api_key_id
    else:
        return None
    valore = valore.strip()
    try:
        if valore.startswith(PREFISSO_CHIAVE):
            chiave = valore
            if identificativo and (
                normalizza_identificativo(identificativo) != identificativo_da_chiave(chiave)
            ):
                raise ChiaveApiRifiutata("AUTH-001",
                                         "identificativo diverso da quello della chiave")
        else:
            if not identificativo:
                raise ChiaveApiRifiutata("AUTH-001", "segreto senza identificativo")
            chiave = f"{PREFISSO_CHIAVE}{normalizza_identificativo(identificativo)}_{valore}"
        identificativo_da_chiave(chiave)  # forma completa
    except ChiaveApiMalformata as exc:
        raise ChiaveApiRifiutata("AUTH-001", "forma non valida") from exc
    return chiave


def _testo(valore: Any) -> str:
    return str(getattr(valore, "value", valore))


async def verifica_chiave_api(
    chiave: str,
    pepper: bytes | None,
    *,
    adesso: datetime | None = None,
    scope_ammessi: Iterable[str] = SCOPE_CHIAVE_API,
    intervallo_ultimo_uso: int = INTERVALLO_ULTIMO_USO_PREDEFINITO,
) -> IdentitaChiaveApi:
    """
    Verifica la chiave sul database e restituisce l'identità.

    Solleva ``ChiaviApiNonDisponibili`` senza pepper, ``ChiaveApiRifiutata``
    (AUTH-001/AUTH-002) per una chiave non accettabile,
    ``VerificaChiaveNonEseguibile`` se il database non risponde.
    I permessi restituiti sono quelli salvati con la chiave, mai oltre
    ``scope_ammessi``.
    """
    from sqlalchemy import or_, select, update
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import get_async_session
    from libs.shared.models import ApiKey, Tenant

    if pepper is None:
        raise ChiaviApiNonDisponibili("API_KEY_PEPPER non configurato")
    try:
        identificativo = identificativo_da_chiave(chiave)
    except ChiaveApiMalformata as exc:
        raise ChiaveApiRifiutata("AUTH-001", "forma non valida") from exc
    ora = adesso or datetime.now(UTC)
    ammessi = set(scope_ammessi)

    try:
        async with get_async_session() as session:
            righe = (await session.execute(
                select(ApiKey.id, ApiKey.tenant_id, ApiKey.user_id, ApiKey.key_hash,
                       ApiKey.permessi, ApiKey.scadenza, Tenant.stato)
                .join(Tenant, Tenant.id == ApiKey.tenant_id)
                .where(ApiKey.key_prefix == identificativo, ApiKey.attiva.is_(True))
            )).all()
            trovata = next((r for r in righe if verifica_chiave(chiave, r.key_hash, pepper)), None)
            if trovata is None:
                raise ChiaveApiRifiutata("AUTH-001", "nessuna chiave attiva corrisponde")
            if trovata.scadenza is not None and trovata.scadenza < ora:
                raise ChiaveApiRifiutata("AUTH-002", f"chiave {trovata.id} scaduta")
            stato = _testo(trovata.stato)
            if stato in STATI_ENTE_BLOCCATI:
                raise ChiaveApiRifiutata("AUTH-001", f"ente in stato {stato}")
            # ultimo_uso: al più una scrittura per intervallo (non a ogni richiesta)
            await session.execute(
                update(ApiKey)
                .where(ApiKey.id == trovata.id,
                       or_(ApiKey.ultimo_uso.is_(None),
                           ApiKey.ultimo_uso < ora - timedelta(seconds=intervallo_ultimo_uso)))
                .values(ultimo_uso=ora)
            )
    except (ChiaveApiRifiutata, ChiaviApiNonDisponibili):
        raise
    except SQLAlchemyError as exc:
        logger.error("chiave_api_non_verificabile", errore=type(exc).__name__)
        raise VerificaChiaveNonEseguibile("verifica della chiave non riuscita") from exc

    scopes = tuple(s for s in (trovata.permessi or []) if s in ammessi)
    identita = IdentitaChiaveApi(
        api_key_id=str(trovata.id), tenant_id=str(trovata.tenant_id),
        user_id=str(trovata.user_id), scopes=scopes,
    )
    # VD-C-080: le transazioni successive della richiesta vedono solo l'ente della chiave
    from libs.shared.security.ambito_dati import imposta_ambito_identita

    imposta_ambito_identita(identita.payload())
    return identita


def errore_api_chiave(exc: Exception) -> Exception:
    """Errore del catalogo (problem+json) per un'eccezione di questo modulo.

    Il motivo tecnico va nel log, al client solo il codice (non si dice quale
    parte della chiave è sbagliata)."""
    from libs.shared.errori import ErroreApi

    www = {"WWW-Authenticate": "Bearer"}
    if isinstance(exc, ChiaveApiRifiutata):
        logger.warning("chiave_api_rifiutata", codice=exc.codice, motivo=exc.motivo)
        if exc.codice == "AUTH-002":
            return ErroreApi("AUTH-002", "Chiave API scaduta: generarne una nuova.", headers=www)
        return ErroreApi("AUTH-001", "Chiave API non valida o revocata.",
                         titolo="Chiave API non valida o revocata", headers=www)
    if isinstance(exc, ChiaviApiNonDisponibili):
        return ErroreApi("AUTH-030", "API_KEY_PEPPER non configurato in questo ambiente: "
                                     "le chiavi API non possono essere verificate.")
    if isinstance(exc, VerificaChiaveNonEseguibile):
        return ErroreApi("AUTH-503", "Riprovare tra poco.")
    return exc


__all__ = [
    "INTERVALLO_ULTIMO_USO_PREDEFINITO", "METODO_CHIAVE_API", "SCOPE_CHIAVE_API",
    "ChiaveApiRifiutata", "ChiaviApiNonDisponibili", "IdentitaChiaveApi",
    "VerificaChiaveNonEseguibile", "chiave_da_intestazioni", "errore_api_chiave",
    "verifica_chiave_api",
]
