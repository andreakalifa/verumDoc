# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/canali.py — identità di canale (contratto C2) nel gateway.

Riferimenti: contratto C2 dell'ondata B R03 (agente b4); piano R03 §3.2;
REGOLE_SVILUPPO §19.1, §19.4, §14.3.

Il gateway accetta, oltre a JWT utente e chiave API, il **token di canale** di
un servizio ammesso (``sftp-ingest``; da R05 ``ciclo-vita``) che agisce per conto di un ente e di
un utente responsabile (``libs.shared.security.token_canale``).

Impostazioni (variabili d'ambiente, prefisso ``CANALE_``):
  - ``CANALE_SERVIZI_AMMESSI`` (JSON servizio → canali), predefinito
    ``{"sftp-ingest": ["SFTP"], "ciclo-vita": ["CICLO_VITA"]}`` (R05);
  - ``CANALE_CHIAVI_PUBBLICHE`` (JSON servizio → chiave pubblica PEM del
    servizio); per sftp-ingest basta ``SFTP_TOKEN_CHIAVE_PUBBLICA`` (la
    pubblica di ``SFTP_TOKEN_CHIAVE_PRIVATA`` del servizio), per ciclo-vita
    ``CICLO_VITA_TOKEN_CHIAVE_PUBBLICA`` (R05). In development e
    test, se manca, si usa la chiave pubblica del
    gateway (il servizio di canale firma allora con la chiave del gateway);
    in ogni altro ambiente un servizio senza chiave dedicata è ESCLUSO: i suoi
    token si rifiutano (avviso nel log all'avvio). Così una chiave privata del
    gateway non deve mai uscire dal gateway in esercizio.

Il payload passato ai casi d'uso per un token di canale ha ``ruolo`` =
``API_SYSTEM`` (sistema che agisce per l'utente: niente accesso alle funzioni
riservate ai ruoli umani), ``ruolo_utente`` = ruolo dell'utente responsabile,
``scopes`` = intersezione fra i permessi del ruolo dell'utente e quelli di un
sistema versante, ``canale`` = canale del token (es. SFTP).
"""
from __future__ import annotations

from typing import Any

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth.jwt_handler import chiavi_jwt
from app.auth.ruoli import scopes_del_ruolo
from app.config import settings
from libs.shared.security.revoca import RUOLO_CHIAVE_API
from libs.shared.security.token_canale import (
    CANALI_CON_TOKEN_DI_SERVIZIO,
    ServizioCanale,
    servizi_da_configurazione,
    verifica_token_canale,
)

logger = structlog.get_logger(__name__)

#: Canale delle richieste con JWT utente o chiave API.
CANALE_PREDEFINITO = "REST"
#: Ambienti in cui un servizio di canale senza chiave dedicata usa quella del gateway.
AMBIENTI_CHIAVE_DI_RIPIEGO = frozenset({"development", "test"})
METODO_TOKEN_CANALE = "token_canale"


class ImpostazioniCanali(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CANALE_", case_sensitive=False)

    servizi_ammessi: dict[str, list[str]] = Field(
        default_factory=lambda: {"sftp-ingest": ["SFTP"], "ciclo-vita": ["CICLO_VITA"]},
        description="Servizi di canale ammessi e canali per cui possono emettere token",
    )
    chiavi_pubbliche: dict[str, str] = Field(
        default_factory=dict, description="Chiave pubblica PEM di ciascun servizio di canale",
    )


class _ChiaveSftp(BaseSettings):
    """Scorciatoia per sftp-ingest (nome concordato con l'agente b2):
    ``SFTP_TOKEN_CHIAVE_PUBBLICA`` = chiave pubblica di ``SFTP_TOKEN_CHIAVE_PRIVATA``."""

    model_config = SettingsConfigDict(env_prefix="SFTP_TOKEN_", case_sensitive=False)

    chiave_pubblica: str = ""


class _ChiaveCicloVita(BaseSettings):
    """R05: ``CICLO_VITA_TOKEN_CHIAVE_PUBBLICA`` = chiave pubblica di
    ``CICLO_VITA_TOKEN_CHIAVE_PRIVATA`` del servizio ciclo-vita (come per SFTP)."""

    model_config = SettingsConfigDict(env_prefix="CICLO_VITA_TOKEN_", case_sensitive=False)

    chiave_pubblica: str = ""


def carica_servizi(imp: ImpostazioniCanali, env: str,
                   chiave_sftp: str | None = None,
                   chiave_ciclo_vita: str | None = None) -> list[ServizioCanale]:
    ripiego = (chiavi_jwt().pubblica_pem
               if (env or "").strip().lower() in AMBIENTI_CHIAVE_DI_RIPIEGO else None)
    chiavi = dict(imp.chiavi_pubbliche)
    if chiave_sftp and "sftp-ingest" not in chiavi:
        chiavi["sftp-ingest"] = chiave_sftp
    if chiave_ciclo_vita is None:
        chiave_ciclo_vita = _ChiaveCicloVita().chiave_pubblica
    if chiave_ciclo_vita and "ciclo-vita" not in chiavi:
        chiavi["ciclo-vita"] = chiave_ciclo_vita
    return servizi_da_configurazione(imp.servizi_ammessi, chiavi, chiave_ripiego_pem=ripiego)


#: Servizi di canale del processo (letti all'avvio).
SERVIZI_CANALE: list[ServizioCanale] = carica_servizi(
    ImpostazioniCanali(), settings.env, _ChiaveSftp().chiave_pubblica)


def payload_da_token_canale(token: str) -> dict[str, Any]:
    """Verifica crittografica del token di canale (``ErroreTokenNonValido`` se no)
    e payload per i casi d'uso. Il controllo sul database (utente attivo,
    dell'ente, stesso ruolo) lo fa ``app.auth.dependencies``."""
    grezzo = verifica_token_canale(token, servizi=SERVIZI_CANALE, emittente=settings.jwt_issuer)
    ruolo_utente = str(grezzo["ruolo"])
    ammessi = set(scopes_del_ruolo(RUOLO_CHIAVE_API))
    scopes = [s for s in scopes_del_ruolo(ruolo_utente) if s in ammessi or s == "*"]
    if "*" in scopes:  # ruoli globali: al più i permessi di un sistema versante
        scopes = sorted(ammessi)
    return {
        "sub": grezzo["sub"],
        "servizio": grezzo["servizio"],
        "tenant_id": str(grezzo["tenant_id"]),
        "user_id": str(grezzo["user_id"]),
        "ruolo": RUOLO_CHIAVE_API,
        "ruolo_utente": ruolo_utente,
        "scopes": scopes,
        "canale": str(grezzo["canale"]),
        "auth_method": METODO_TOKEN_CANALE,
        "jti": grezzo.get("jti"),
    }


def canale_effettivo(user: dict[str, Any], canale_rotta: str) -> str:
    """
    Canale da passare al caso d'uso. Una richiesta con token di canale (es.
    SFTP) resta di quel canale e vale solo sulle rotte REST che quel canale usa
    (sessioni di caricamento, versamento per riferimento, stato): su una rotta
    di un altro canale (PDND, portale) → 403 AUTH-010. Le altre
    credenziali prendono il canale della rotta.
    """
    from libs.shared.errori import ErroreApi

    canale_token = str(user.get("canale") or "")
    if user.get("auth_method") == METODO_TOKEN_CANALE:
        if canale_token not in CANALI_CON_TOKEN_DI_SERVIZIO:
            raise ErroreApi("AUTH-010", "Token di canale senza un canale ammesso.")
        if canale_rotta not in (CANALE_PREDEFINITO, canale_token):
            raise ErroreApi("AUTH-010",
                            f"Il token del canale {canale_token} non vale per il canale "
                            f"{canale_rotta}.")
        return canale_token
    return canale_rotta


__all__ = [
    "CANALE_PREDEFINITO", "METODO_TOKEN_CANALE", "SERVIZI_CANALE", "ImpostazioniCanali",
    "canale_effettivo", "carica_servizi", "payload_da_token_canale",
]
