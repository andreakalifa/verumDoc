# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/storage/client.py — client S3 riusato per processo (R04 integrazione).

La prova di carico di R04 (``tools/carico/prova_locale.py``) ha misurato che
creare un client boto3 a ogni versamento costa ~5,6 ms di CPU e una connessione
nuova (in esercizio un handshake TLS): 18,8 % dei campioni del gateway. I client
boto3 sono utilizzabili da più thread: qui se ne tiene UNO per combinazione di
parametri (endpoint, credenziali, regione, opzioni), creato alla prima richiesta.

Credenziali vuote → ``None`` (richiesta di r04-infra, VD-C-101): con IRSA le
chiavi statiche non ci sono e boto3 deve usare la catena di credenziali
(ruolo del ServiceAccount); con la stringa vuota firmerebbe con credenziali
vuote e ogni richiesta fallirebbe.

La chiave della cache comprende i parametri, non solo il servizio: un cambio di
configurazione (nei test: un server S3 per sessione) produce un client nuovo.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

#: Combinazioni di parametri tenute in memoria (un servizio ne usa una o due).
MASSIMO_CLIENT = 8


def _o_none(valore: Any) -> Any:
    """Stringa vuota o di soli spazi → None (lascia decidere a boto3)."""
    if valore is None:
        return None
    testo = str(valore)
    if hasattr(valore, "get_secret_value"):  # pydantic SecretStr
        testo = valore.get_secret_value()
    return testo if testo.strip() else None


@lru_cache(maxsize=MASSIMO_CLIENT)
def _client_in_cache(endpoint_url: str | None, access_key: str | None, secret_key: str | None,
                     region: str | None, opzioni: tuple[tuple[str, Any], ...]) -> Any:
    import boto3
    import botocore.config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        # le opzioni annidate (es. retries) arrivano come tuple di coppie (hashable)
        config=botocore.config.Config(**{k: dict(v) if isinstance(v, tuple) else v
                                         for k, v in opzioni}),
    )


def client_s3(*, endpoint_url: Any = None, access_key: Any = None, secret_key: Any = None,
              region: Any = None, **opzioni: Any) -> Any:
    """Client S3 condiviso per i parametri indicati.

    ``opzioni`` va a ``botocore.config.Config`` (predefinita ``signature_version``
    = ``s3v4``); valori annidati come ``retries`` si passano come dizionario."""
    opzioni.setdefault("signature_version", "s3v4")
    chiave = tuple(sorted(
        (k, tuple(sorted(v.items())) if isinstance(v, dict) else v) for k, v in opzioni.items()))
    return _client_in_cache(_o_none(endpoint_url), _o_none(access_key), _o_none(secret_key),
                            _o_none(region), chiave)


def svuota_cache() -> None:
    """Dimentica i client creati (test, rotazione delle credenziali statiche)."""
    _client_in_cache.cache_clear()


def client_creati() -> int:
    """Quanti client diversi sono stati creati (per test e diagnosi)."""
    return _client_in_cache.cache_info().currsize
