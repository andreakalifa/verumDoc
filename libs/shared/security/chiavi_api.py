# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/chiavi_api.py — chiavi API dei sistemi versanti (REGOLE §19.8).

UNA sola rappresentazione, usata da chi genera (onboarding) e da chi verifica
(gateway), così una chiave generata autentica davvero:

  chiave completa (mostrata UNA volta):   vd_<identificativo>_<segreto>
      identificativo  10 caratteri [a-z2-9] senza caratteri ambigui (~50 bit),
                      pubblico: è salvato in chiaro in api_keys.key_prefix e
                      serve a trovare la riga senza scorrere la tabella;
      segreto         secrets.token_urlsafe(32): 256 bit casuali.
  impronta salvata in api_keys.key_hash:  "hmac-sha256$" + HMAC-SHA256(pepper, chiave completa)

Perché HMAC con «pepper» e non un hash lento (bcrypt/argon2):
  - il segreto ha 256 bit casuali: un hash lento protegge password scelte da
    persone (poca entropia) dai tentativi a dizionario, qui non aggiunge
    sicurezza perché lo spazio non è esplorabile;
  - la chiave si verifica a OGNI chiamata di versamento: bcrypt costa ~100-250 ms
    di CPU per richiesta e renderebbe il gateway attaccabile per esaurimento di
    risorse con chiavi inventate;
  - il pepper (API_KEY_PEPPER, segreto d'ambiente, mai nel database) fa sì che
    chi legge solo il database (backup, replica, dump) non possa nemmeno
    verificare un'ipotesi di chiave;
  - il confronto è a tempo costante (hmac.compare_digest).
L'HMAC copre la chiave completa, identificativo compreso: l'identificativo non
si può spostare su un'altra riga.

Il pepper è unico per ambiente e condiviso fra onboarding e gateway. Cambiarlo
invalida tutte le chiavi esistenti (vanno rigenerate): il prefisso
«hmac-sha256$» lascia spazio a un formato successivo con versione del pepper.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field

PREFISSO_CHIAVE = "vd_"
LUNGHEZZA_IDENTIFICATIVO = 10
ALFABETO_IDENTIFICATIVO = "abcdefghijkmnpqrstuvwxyz23456789"
FORMATO_IMPRONTA = "hmac-sha256$"
#: Lunghezza minima del pepper (caratteri). `openssl rand -hex 32` ne dà 64.
LUNGHEZZA_MINIMA_PEPPER = 32
#: Ambienti in cui il servizio parte anche senza pepper (la funzione chiavi API
#: risponde allora con un errore esplicito di configurazione).
AMBIENTI_SENZA_PEPPER_OBBLIGATORIO = frozenset({"development", "test"})

_FORMA_CHIAVE = re.compile(
    rf"^{PREFISSO_CHIAVE}([{ALFABETO_IDENTIFICATIVO}]{{{LUNGHEZZA_IDENTIFICATIVO}}})_([A-Za-z0-9_-]{{32,128}})$"
)
_FORMA_IDENTIFICATIVO = re.compile(
    rf"^(?:{PREFISSO_CHIAVE})?([{ALFABETO_IDENTIFICATIVO}]{{{LUNGHEZZA_IDENTIFICATIVO}}})$"
)


class ErroreConfigurazioneChiaviApi(RuntimeError):
    """Pepper mancante o troppo corto: fuori da development/test il servizio non parte."""


class ChiaveApiMalformata(ValueError):
    """La stringa ricevuta non ha la forma di una chiave VerumDoc."""


@dataclass(frozen=True)
class ChiaveApiGenerata:
    """Chiave appena generata. `chiave` non compare nel repr (mai nei log)."""

    identificativo: str
    impronta: str
    chiave: str = field(repr=False)


def carica_pepper(*, env: str, valore: str | None, servizio: str) -> bytes | None:
    """
    Valida il pepper letto dall'ambiente (API_KEY_PEPPER).
    - valorizzato: almeno LUNGHEZZA_MINIMA_PEPPER caratteri, altrimenti errore;
    - assente in development/test: None (le chiavi API non sono utilizzabili e
      lo si dice con un errore esplicito a ogni uso);
    - assente altrove: ErroreConfigurazioneChiaviApi (il servizio non parte).
    """
    testo = (valore or "").strip()
    ambiente = (env or "").strip().lower()
    if not testo:
        if ambiente in AMBIENTI_SENZA_PEPPER_OBBLIGATORIO:
            return None
        raise ErroreConfigurazioneChiaviApi(
            f"[{servizio}] API_KEY_PEPPER mancante (ENV={ambiente or '?'}): serve per "
            "generare e verificare le chiavi API. Impostarlo come secret, uguale in "
            "onboarding e gateway (es. openssl rand -hex 32). Il servizio non parte."
        )
    if len(testo) < LUNGHEZZA_MINIMA_PEPPER:
        raise ErroreConfigurazioneChiaviApi(
            f"[{servizio}] API_KEY_PEPPER troppo corto "
            f"(minimo {LUNGHEZZA_MINIMA_PEPPER} caratteri)."
        )
    return testo.encode()


def impronta_chiave(chiave: str, pepper: bytes) -> str:
    """Impronta da salvare in api_keys.key_hash."""
    if not pepper:
        raise ErroreConfigurazioneChiaviApi("API_KEY_PEPPER non configurato")
    digest = hmac.new(pepper, chiave.encode(), hashlib.sha256).hexdigest()
    return f"{FORMATO_IMPRONTA}{digest}"


def genera_chiave_api(pepper: bytes) -> ChiaveApiGenerata:
    """Nuova chiave con identificativo pubblico e impronta da salvare."""
    identificativo = "".join(
        secrets.choice(ALFABETO_IDENTIFICATIVO) for _ in range(LUNGHEZZA_IDENTIFICATIVO)
    )
    chiave = f"{PREFISSO_CHIAVE}{identificativo}_{secrets.token_urlsafe(32)}"
    return ChiaveApiGenerata(
        identificativo=identificativo,
        impronta=impronta_chiave(chiave, pepper),
        chiave=chiave,
    )


def identificativo_da_chiave(chiave: str) -> str:
    """Identificativo pubblico di una chiave completa, o ChiaveApiMalformata."""
    trovata = _FORMA_CHIAVE.match((chiave or "").strip())
    if not trovata:
        raise ChiaveApiMalformata("La chiave API non ha la forma vd_<identificativo>_<segreto>")
    return trovata.group(1)


def normalizza_identificativo(valore: str) -> str:
    """Accetta «vd_abcdefghij» o «abcdefghij»; ChiaveApiMalformata altrimenti."""
    trovato = _FORMA_IDENTIFICATIVO.match((valore or "").strip())
    if not trovato:
        raise ChiaveApiMalformata("Identificativo della chiave API non valido")
    return trovato.group(1)


def verifica_chiave(chiave: str, impronta_salvata: str, pepper: bytes) -> bool:
    """True se la chiave corrisponde all'impronta salvata (confronto a tempo costante)."""
    if not impronta_salvata or not impronta_salvata.startswith(FORMATO_IMPRONTA):
        return False
    return hmac.compare_digest(impronta_chiave(chiave, pepper), impronta_salvata)
