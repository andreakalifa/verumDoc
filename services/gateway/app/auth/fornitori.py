# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/fornitori.py — fornitori d'identità e metodi d'accesso (R10, D08).

Riferimenti: decisione D08 (VD-C-069); migrazione 20261102_049
(``fornitori_identita``); parametro ``ACCESSO_METODI_AMMESSI`` del motore
(``libs.verumdoc_rules.parametri_servizio``); REGOLE_SVILUPPO §8.14 (default di
una configurazione nuova = comportamento esistente).

  - il CONSERVATORE registra in console un fornitore per tipo (SPID_CIE,
    PASSKEY, PASSWORD_TOTP) e lo attiva;
  - il metodo è abilitato se il fornitore è attivo e il conservatore lo ammette
    in ``ACCESSO_METODI_AMMESSI``; l'ENTE sceglie fra questi (verso «più
    sicuro»: solo meno metodi, mai uno che il conservatore non ha abilitato).

SPID/CIE: con la riga SPID_CIE i parametri dell'aggregatore (URL, client_id,
emittente, JWKS, livello minimo) vengono dalla console; il segreto del cliente
OIDC resta SOLO nella variabile ``SPID_CLIENT_SECRET`` (mai nel database).
Senza riga valgono le variabili d'ambiente ``SPID_*`` di prima (§8.14).
Cache breve (``VD_FORNITORI_CACHE_SECONDI``, 30 s) per non leggere il database
a ogni richiesta; errori del database: nessun metodo (fail-closed).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from libs.verumdoc_rules.parametri_servizio import (
    ACCESSO_METODI_AMMESSI,
    METODI_ACCESSO,
    METODO_PASSKEY,
    METODO_PASSWORD_TOTP,
    METODO_SPID_CIE,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ConfigSpid:
    """Parametri dell'aggregatore OIDC SPID/CIE in uso (mai il segreto)."""

    aggregatore_url: str
    client_id: str
    issuer: str
    jwks_url: str
    livello_minimo: int
    attivo: bool
    da_console: bool
    # R13b (VD-C-069): profilo OIDC dell'aggregatore (predefiniti = comportamento di prima)
    metodo_autenticazione: str = "client_secret_post"
    request_object: bool = False
    attributi_da_userinfo: bool = False
    prompt: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    #: federazione OpenID (facoltativa): {"trust_anchor", "chiavi_trust_anchor", "entita_op"}
    federazione: dict[str, Any] = field(default_factory=dict)

    @property
    def configurato(self) -> bool:
        return bool(self.attivo and self.aggregatore_url and self.client_id and self.issuer
                    and (self.jwks_url or self.federazione))

    @property
    def url_authorize(self) -> str:
        return self.authorize_url or f"{self.aggregatore_url}/authorize"

    @property
    def url_token(self) -> str:
        return self.token_url or f"{self.aggregatore_url}/token"

    @property
    def url_userinfo(self) -> str:
        return self.userinfo_url or f"{self.aggregatore_url}/userinfo"


@dataclass(frozen=True)
class Fornitori:
    """Fotografia delle righe di ``fornitori_identita`` (tipo → (attivo, configurazione))."""

    righe: dict[str, tuple[bool, dict[str, Any]]] = field(default_factory=dict)


_cache: dict[str, tuple[float, Fornitori]] = {}


def durata_cache() -> float:
    try:
        return max(0.0, float(os.environ.get("VD_FORNITORI_CACHE_SECONDI", "30")))
    except ValueError:
        return 30.0


def svuota_cache_fornitori() -> None:
    _cache.clear()


async def leggi_fornitori() -> Fornitori:
    """Righe dei fornitori (cache breve). Database non inizializzato: nessuna riga."""
    from libs.shared.db import DBNonInizializzato, get_async_session

    voce = _cache.get("f")
    adesso = time.monotonic()
    if voce is not None and voce[0] > adesso:
        return voce[1]
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                "SELECT tipo, attivo, configurazione FROM public.fornitori_identita"))).all()
    except DBNonInizializzato:
        return Fornitori()
    fornitori = Fornitori({str(r.tipo): (bool(r.attivo), dict(r.configurazione or {}))
                           for r in righe})
    _cache["f"] = (adesso + durata_cache(), fornitori)
    return fornitori


def _testo(conf: dict[str, Any], chiave: str) -> str:
    valore = conf.get(chiave)
    return str(valore).strip() if isinstance(valore, str) else ""


async def configurazione_spid() -> ConfigSpid:
    """SPID/CIE in uso: riga della console se esiste, altrimenti le variabili SPID_*."""
    try:
        fornitori = await leggi_fornitori()
    except SQLAlchemyError as exc:
        logger.error("fornitori_non_leggibili", errore=type(exc).__name__)
        return ConfigSpid("", "", "", "", 3, attivo=False, da_console=True)
    riga = fornitori.righe.get(METODO_SPID_CIE)
    if riga is None:
        return ConfigSpid(settings.spid_aggregator_url, settings.spid_client_id,
                          settings.spid_issuer, settings.spid_jwks_url,
                          max(2, int(settings.spid_livello_minimo)), attivo=True,
                          da_console=False)
    attivo, conf = riga
    try:
        livello = int(conf.get("livello_minimo", 2))
    except (TypeError, ValueError):
        livello = 3
    federazione = conf.get("federazione")
    return ConfigSpid(_testo(conf, "aggregatore_url").rstrip("/"), _testo(conf, "client_id"),
                      _testo(conf, "issuer"), _testo(conf, "jwks_url"),
                      min(3, max(2, livello)), attivo=attivo, da_console=True,
                      metodo_autenticazione=_testo(conf, "metodo_autenticazione")
                      or "client_secret_post",
                      request_object=conf.get("request_object") is True,
                      attributi_da_userinfo=conf.get("attributi_da_userinfo") is True,
                      prompt=_testo(conf, "prompt"),
                      authorize_url=_testo(conf, "authorize_url").rstrip("/"),
                      token_url=_testo(conf, "token_url").rstrip("/"),
                      userinfo_url=_testo(conf, "userinfo_url").rstrip("/"),
                      federazione=dict(federazione) if isinstance(federazione, dict) else {})


async def metodi_abilitati_servizio() -> frozenset[str]:
    """Metodi con un fornitore attivo (prima del parametro del conservatore).
    SPID/CIE senza riga: abilitato come prima se l'aggregatore è configurato
    in ambiente o il simulatore di sviluppo è attivo."""
    try:
        fornitori = await leggi_fornitori()
    except SQLAlchemyError as exc:
        logger.error("fornitori_non_leggibili", errore=type(exc).__name__)
        return frozenset()
    attivi = {tipo for tipo, (attivo, _c) in fornitori.righe.items() if attivo}
    if METODO_SPID_CIE not in fornitori.righe:
        cfg = await configurazione_spid()
        if cfg.configurato or (settings.login_sviluppo_abilitato()
                               and bool(settings.spid_mock_enabled)):
            attivi.add(METODO_SPID_CIE)
    return frozenset(m for m in METODI_ACCESSO if m in attivi)


async def metodi_ammessi_ente(tenant_id: Any) -> tuple[str, ...]:
    """Metodi con cui le persone dell'ente accedono: parametro del motore
    (conservatore → ente, verso «più sicuro») ∩ fornitori attivi."""
    from libs.shared.parametri import parametri

    abilitati = await metodi_abilitati_servizio()
    valori = await parametri([ACCESSO_METODI_AMMESSI], tenant_id)
    scelti = valori.get(ACCESSO_METODI_AMMESSI) or ()
    return tuple(m for m in scelti if m in abilitati)


async def metodi_ammessi_servizio() -> tuple[str, ...]:
    """Metodi abilitati dal conservatore per almeno un ente (pagina d'accesso:
    quali pulsanti mostrare; nessun dato di un ente)."""
    from libs.shared.parametri import parametri

    abilitati = await metodi_abilitati_servizio()
    valori = await parametri([ACCESSO_METODI_AMMESSI], None)
    generale = set(valori.get(ACCESSO_METODI_AMMESSI) or ())
    # un LIMITE per ente può ammettere un metodo che il valore generale non ha:
    # la pagina d'accesso mostra ogni metodo con un fornitore attivo e lascia
    # all'accesso il controllo per ente (nessuna enumerazione degli enti)
    return tuple(m for m in METODI_ACCESSO if m in abilitati and (m in generale or m in (
        METODO_PASSKEY, METODO_PASSWORD_TOTP)))


__all__ = ["ConfigSpid", "configurazione_spid", "leggi_fornitori", "metodi_abilitati_servizio",
           "metodi_ammessi_ente", "metodi_ammessi_servizio", "svuota_cache_fornitori"]
