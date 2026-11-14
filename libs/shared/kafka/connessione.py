# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/kafka/connessione.py — parametri di connessione dei client Kafka (R04).

In esercizio Kafka è Amazon MSK, che accetta solo TLS con autenticazione IAM
(``infra/terraform/modules/kafka``; i ruoli IRSA hanno già i permessi
``kafka-cluster:*``, r04-infra): senza questo modulo nessun servizio si sarebbe
collegato. In locale (docker compose) Kafka resta in chiaro (PLAINTEXT), come oggi.

Impostazioni (variabili d'ambiente, lette da OGNI servizio allo stesso modo):

  KAFKA_AUTENTICAZIONE   ``nessuna`` (predefinito: PLAINTEXT) | ``iam`` (MSK: SASL_SSL
                         + OAUTHBEARER firmato con le credenziali AWS del processo,
                         IRSA in Kubernetes)
  KAFKA_REGIONE          regione AWS del cluster; vuota → ``AWS_REGION`` /
                         ``AWS_DEFAULT_REGION``. Obbligatoria con ``iam``.
  KAFKA_CA_FILE          facoltativo: bundle di CA per TLS (vuoto = CA di sistema,
                         sufficienti per MSK)

Il token è prodotto da ``aws-msk-iam-sasl-signer-python`` (Apache-2.0, AWS),
importato SOLO con ``iam``: in locale la libreria non serve. Il token dura 15
minuti; qui si riusa fino a 60 s prima della scadenza (un token per processo, non
per connessione) e si genera in un thread (firma SigV4 e lettura delle credenziali
sono sincrone).

Uso (stessi parametri per producer, consumer e probe di prontezza):

    AIOKafkaConsumer(*topic, bootstrap_servers=..., **parametri_connessione())
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any, Literal

from aiokafka.abc import AbstractTokenProvider
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Margine prima della scadenza del token oltre cui se ne genera uno nuovo.
MARGINE_RINNOVO_SECONDI = 60.0


class ImpostazioniKafka(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_", case_sensitive=False,
                                      extra="ignore")
    autenticazione: Literal["nessuna", "iam"] = "nessuna"
    regione: str = ""
    ca_file: str = ""


class ConfigurazioneKafkaNonValida(ValueError):
    """Impostazioni di connessione incoerenti: il servizio non deve partire."""


Generatore = Callable[[str], tuple[str, int]]


def _generatore_msk(regione: str) -> tuple[str, int]:
    from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

    token, scadenza_ms = MSKAuthTokenProvider.generate_auth_token(regione)
    return token, int(scadenza_ms)


class TokenMskIam(AbstractTokenProvider):
    """Fornitore del token OAUTHBEARER per MSK IAM (interfaccia di aiokafka).

    ``generatore(regione) -> (token, scadenza in ms epoch)``: predefinito la
    libreria AWS; iniettabile nei test. ``orologio`` in secondi epoch."""

    def __init__(self, regione: str, *, generatore: Generatore | None = None,
                 orologio: Callable[[], float] = time.time) -> None:
        if not regione:
            raise ConfigurazioneKafkaNonValida(
                "KAFKA_AUTENTICAZIONE=iam richiede la regione (KAFKA_REGIONE o AWS_REGION)")
        self.regione = regione
        self._generatore = generatore or _generatore_msk
        self._orologio = orologio
        self._token: str | None = None
        self._scade_il = 0.0
        self._blocco = asyncio.Lock()
        self.generati = 0

    async def token(self) -> str:
        async with self._blocco:
            if self._token is None or self._orologio() >= self._scade_il - MARGINE_RINNOVO_SECONDI:
                token, scadenza_ms = await asyncio.to_thread(self._generatore, self.regione)
                self._token, self._scade_il = token, scadenza_ms / 1000.0
                self.generati += 1
            return self._token

    def extensions(self) -> dict[str, str]:
        return {}


def regione_effettiva(imp: ImpostazioniKafka) -> str:
    return (imp.regione or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            or "").strip()


_fornitori: dict[str, TokenMskIam] = {}


def parametri_connessione(imp: ImpostazioniKafka | None = None, *,
                          generatore: Generatore | None = None) -> dict[str, Any]:
    """Argomenti per ``AIOKafkaProducer``/``AIOKafkaConsumer``/``create_conn``.

    ``nessuna`` → PLAINTEXT. ``iam`` → SASL_SSL + OAUTHBEARER con un fornitore di
    token condiviso nel processo (per regione) e contesto TLS verificato.
    Solleva ``ConfigurazioneKafkaNonValida`` se manca la regione o il file di CA."""
    imp = imp or ImpostazioniKafka()
    if imp.autenticazione == "nessuna":
        return {"security_protocol": "PLAINTEXT"}
    from aiokafka.helpers import create_ssl_context

    regione = regione_effettiva(imp)
    if imp.ca_file and not os.path.isfile(imp.ca_file):
        raise ConfigurazioneKafkaNonValida(f"KAFKA_CA_FILE non trovato: {imp.ca_file}")
    if generatore is not None:
        fornitore = TokenMskIam(regione, generatore=generatore)
    else:
        fornitore = _fornitori.get(regione) or TokenMskIam(regione)
        _fornitori[regione] = fornitore
    return {
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "OAUTHBEARER",
        "sasl_oauth_token_provider": fornitore,
        "ssl_context": create_ssl_context(cafile=imp.ca_file or None),
    }


def descrizione() -> dict[str, str]:
    """Riepilogo NON segreto per i log d'avvio e ``/info``."""
    imp = ImpostazioniKafka()
    return {"autenticazione": imp.autenticazione,
            "protocollo": "SASL_SSL" if imp.autenticazione == "iam" else "PLAINTEXT",
            "regione": regione_effettiva(imp) if imp.autenticazione == "iam" else ""}
