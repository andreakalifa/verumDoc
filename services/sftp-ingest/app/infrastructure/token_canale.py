# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Token di servizio del canale SFTP verso il gateway (contratto C2, R03 ondata B).

Servizio: sftp-ingest. Il canale agisce PER CONTO dell'ente con l'identità
dell'utente responsabile dell'account SFTP. Il token lo firma la funzione
condivisa ``libs.shared.security.token_canale.firma_token_canale`` (la stessa
definizione che il gateway verifica). Claim (contratto C2):
    sub = "servizio:sftp-ingest", servizio = "sftp-ingest", tipo_token = "canale",
    tenant_id, user_id (responsabile), canale = "SFTP", ruolo (dell'utente),
    aud = "verumdoc-canale" (mai il pubblico «verumdoc-api» dei token utente: R03 i1),
più iss/exp/iat/nbf/jti. Gli scope non sono nel token: il gateway li ricava dal
ruolo dell'utente ∩ permessi di un sistema versante.
Il gateway verifica firma, servizio ammesso, utente attivo dello stesso ente
(stessa revoca dei token utente) e legge il piano dal database.

Il token dura poco (``TOKEN_CANALE_DURATA_SECONDI``, predefinito 5 minuti) e si
rinnova in memoria 60 secondi prima della scadenza; non va mai nei log.
La chiave privata (SFTP_TOKEN_CHIAVE_PRIVATA) viene dal secret manager; con
ENV=test e senza chiave si usa una coppia effimera (``chiavi_jwt()`` la espone
ai test, che la passano al gateway finto).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from libs.shared.security.jwt_rs256 import ChiaviJwt, carica_chiavi_jwt
from libs.shared.security.token_canale import PUBBLICO_TOKEN_CANALE, firma_token_canale

NOME_SERVIZIO = "sftp-ingest"
CANALE = "SFTP"
#: Pubblico (aud) dei token di canale: costante del contratto C2, non configurabile.
PUBBLICO = PUBBLICO_TOKEN_CANALE
_MARGINE_RINNOVO_SECONDI = 60


@dataclass(frozen=True)
class _InCache:
    token: str
    scade: float


class EmettitoreToken:
    """Firma e tiene in cache i token per (ente, utente responsabile, ruolo)."""

    def __init__(self, *, chiavi: ChiaviJwt, emittente: str, durata_secondi: int) -> None:
        if not chiavi.privata_pem:
            raise ValueError("Chiave privata del canale SFTP assente")
        self._chiavi = chiavi
        self._emittente = emittente
        self._durata = durata_secondi
        self._cache: dict[tuple[str, str, str], _InCache] = {}

    @property
    def chiave_pubblica_pem(self) -> str:
        return self._chiavi.pubblica_pem

    def token(self, *, tenant_id: uuid.UUID | str, user_id: uuid.UUID | str, ruolo: str,
              adesso: float | None = None) -> str:
        ora = time.time() if adesso is None else adesso
        chiave = (str(tenant_id), str(user_id), ruolo)
        presente = self._cache.get(chiave)
        if presente is not None and presente.scade - _MARGINE_RINNOVO_SECONDI > ora:
            return presente.token
        token = firma_token_canale(
            NOME_SERVIZIO, tenant_id=str(tenant_id), user_id=str(user_id), ruolo=ruolo,
            canale=CANALE, chiave_privata_pem=self._chiavi.privata_pem or "",
            emittente=self._emittente, durata_secondi=self._durata, adesso=int(ora),
        )
        self._cache[chiave] = _InCache(token, ora + self._durata)
        return token


def emettitore_da_impostazioni(*, env: str, chiave_privata: str, emittente: str,
                               durata_secondi: int) -> EmettitoreToken:
    """Carica la chiave (obbligatoria fuori da ENV=test, REGOLE §19.4)."""
    chiavi = carica_chiavi_jwt(env=env, servizio=NOME_SERVIZIO, chiave_pubblica="",
                               chiave_privata=chiave_privata, richiede_privata=True)
    return EmettitoreToken(chiavi=chiavi, emittente=emittente, durata_secondi=durata_secondi)
