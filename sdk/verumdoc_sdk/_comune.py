# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Parti comuni ai client sincrono e asincrono: credenziali, instradamento,
ritentativi, chiavi d'idempotenza, lettura delle risposte, preparazione
dell'indice. Nessun I/O di rete qui.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
import random
import re
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from verumdoc_sdk.errori import (
    ErroreApi,
    RispostaNonValida,
    errore_da_problema,
    problema_da_corpo,
)
from verumdoc_sdk.modelli import DocumentoIndice, ImprontaFile, IndiceUD
from verumdoc_sdk.sorgenti import Sorgente, come_sorgente

VERSIONE = "3.2.0"
USER_AGENT = f"verumdoc-sdk-python/{VERSIONE}"
URL_PREDEFINITO = "https://api.verumdoc.it"

#: Forma della Idempotency-Key accettata dal gateway (VRS-122).
_PATTERN_CHIAVE = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")

M = TypeVar("M", bound=BaseModel)


# ── Credenziali ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChiaveApi:
    """Chiave API del sistema versante (mostrata una volta nel portale).

    ``ChiaveApi("vd_<id>_<segreto>")`` usa l'intestazione ``X-Api-Key``;
    ``ChiaveApi(identificativo="vd_<id>", segreto=...)`` usa ``X-Api-Key-Id`` e
    ``X-Api-Key-Secret``.
    """

    chiave: str | None = field(default=None, repr=False)
    identificativo: str | None = None
    segreto: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.chiave and not self.segreto:
            raise ValueError("ChiaveApi: indicare la chiave completa o identificativo e segreto")
        if self.segreto and not self.chiave and not self.identificativo:
            raise ValueError("ChiaveApi: il segreto richiede l'identificativo")

    def intestazioni(self) -> dict[str, str]:
        if self.chiave:
            return {"X-Api-Key": self.chiave}
        return {"X-Api-Key-Id": str(self.identificativo), "X-Api-Key-Secret": str(self.segreto)}


@dataclass(frozen=True)
class TokenBearer:
    """Token JWT dell'utente (``Authorization: Bearer``). ``token`` può essere una
    funzione senza argomenti che restituisce il token valido (rinnovo a cura del
    chiamante): si chiama a ogni richiesta."""

    token: str | Callable[[], str] = field(repr=False)

    def intestazioni(self) -> dict[str, str]:
        valore = self.token() if callable(self.token) else self.token
        return {"Authorization": f"Bearer {valore}"}


Credenziali = ChiaveApi | TokenBearer


def credenziali_da_ambiente() -> Credenziali | None:
    """``VERUMDOC_API_KEY`` (chiave completa) oppure ``VERUMDOC_TOKEN`` (JWT)."""
    chiave = os.environ.get("VERUMDOC_API_KEY", "").strip()
    if chiave:
        return ChiaveApi(chiave)
    token = os.environ.get("VERUMDOC_TOKEN", "").strip()
    return TokenBearer(token) if token else None


# ── Ritentativi e attese ─────────────────────────────────────────────

@dataclass(frozen=True)
class PoliticaRitentativi:
    """Ritentativi automatici: solo per errori ``ritentabile`` (o di rete) e solo per
    richieste ripetibili senza effetti doppi (lettura, PUT/DELETE, POST con
    Idempotency-Key). Attesa esponenziale con variazione casuale; ``Retry-After``
    del servizio, se più lungo, prevale (fino a ``attesa_massima``)."""

    tentativi: int = 4
    attesa_iniziale: float = 0.5
    fattore: float = 2.0
    attesa_massima: float = 30.0
    variazione: float = 0.2

    def attesa(self, tentativo: int, retry_after: float | None = None,
               casuale: Callable[[], float] = random.random) -> float:
        """Secondi da attendere dopo il tentativo numero ``tentativo`` (da 1)."""
        base = self.attesa_iniziale * (self.fattore ** (tentativo - 1))
        base *= 1 + self.variazione * (2 * casuale() - 1)
        if retry_after is not None:
            base = max(base, retry_after)
        return max(0.0, min(base, self.attesa_massima))


@dataclass(frozen=True)
class PoliticaAttesa:
    """Interrogazione dello stato con intervalli crescenti fino a ``timeout`` secondi."""

    intervallo_iniziale: float = 1.0
    fattore: float = 1.5
    intervallo_massimo: float = 30.0
    timeout: float = 3600.0

    def intervalli(self) -> Iterator[float]:
        attuale = self.intervallo_iniziale
        while True:
            yield min(attuale, self.intervallo_massimo)
            attuale = min(attuale * self.fattore, self.intervallo_massimo)


# ── Idempotency-Key ──────────────────────────────────────────────────

def nuova_chiave_idempotenza() -> str:
    """Chiave nuova (UUID casuale) per una richiesta che crea qualcosa."""
    return f"vdsdk-{uuid.uuid4().hex}"


def valida_chiave_idempotenza(chiave: str) -> str:
    if not _PATTERN_CHIAVE.match(chiave):
        raise ValueError("Idempotency-Key: 8-200 caratteri fra lettere, cifre e . _ : -")
    return chiave


def chiave_derivata(chiave: str, etichetta: str) -> str:
    """Chiave stabile per una sotto-operazione (es. la sessione di caricamento di un
    file del versamento): stessa chiave del versamento e stesso file → stessa sessione,
    così un versamento interrotto riprende da dove era arrivato."""
    suffisso = hashlib.sha256(etichetta.encode("utf-8")).hexdigest()[:16]
    return valida_chiave_idempotenza(f"{chiave[:180]}.f{suffisso}")


# ── Instradamento ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Instradamento:
    """URL base per percorso. In produzione tutto passa dall'ingress
    (``https://api.verumdoc.it``); in sviluppo ``basi`` indica un URL per prefisso,
    es. ``{"/api/v1/eventi": "http://localhost:8006", "/api/v2": "http://localhost:8008"}``
    (vince il prefisso più lungo)."""

    base_url: str = URL_PREDEFINITO
    basi: Mapping[str, str] = field(default_factory=dict)

    def url(self, percorso: str) -> str:
        migliore, base = -1, self.base_url
        for prefisso, url in self.basi.items():
            if percorso.startswith(prefisso) and len(prefisso) > migliore:
                migliore, base = len(prefisso), url
        return base.rstrip("/") + percorso


# ── Richieste e risposte ─────────────────────────────────────────────

FabbricaCorpo = Callable[[], tuple[dict[str, Any], Callable[[], None]]]


def _nessuna_chiusura() -> None:
    return None


@dataclass
class Richiesta:
    """Descrizione di una chiamata, ricostruibile a ogni tentativo."""

    metodo: str
    percorso: str
    params: Mapping[str, Any] | list[tuple[str, Any]] | None = None
    json: Any = None
    intestazioni: dict[str, str] = field(default_factory=dict)
    corpo: FabbricaCorpo | None = None
    ripetibile: bool = False

    def argomenti(self) -> tuple[dict[str, Any], Callable[[], None]]:
        if self.corpo is not None:
            return self.corpo()
        if self.json is not None:
            return {"json": self.json}, _nessuna_chiusura
        return {}, _nessuna_chiusura


def retry_after(valore: str | None, adesso: datetime | None = None) -> float | None:
    """Secondi di ``Retry-After`` (numero o data HTTP)."""
    if not valore:
        return None
    valore = valore.strip()
    if valore.isdigit():
        return float(valore)
    try:
        quando = email.utils.parsedate_to_datetime(valore)
    except (TypeError, ValueError):
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return max(0.0, (quando - (adesso or datetime.now(UTC))).total_seconds())


def errore_da_risposta(risposta: httpx.Response) -> ErroreApi:
    """Eccezione tipizzata per una risposta 4xx/5xx (corpo già letto)."""
    try:
        corpo = risposta.json()
    except (ValueError, UnicodeDecodeError):
        corpo = None
    problema = problema_da_corpo(risposta.status_code, corpo,
                                 testo=(risposta.text or "")[:200] if corpo is None else "",
                                 istanza=risposta.request.url.path if risposta.request else None)
    return errore_da_problema(
        problema, metodo=risposta.request.method if risposta.request else "",
        url=str(risposta.request.url) if risposta.request else "",
        retry_after=retry_after(risposta.headers.get("Retry-After")))


# Sintassi TypeVar classica: l'SDK supporta Python 3.11
def leggi_modello(modello: type[M], risposta: httpx.Response) -> M:  # noqa: UP047
    """Corpo JSON della risposta come ``modello``; RispostaNonValida se non corrisponde."""
    try:
        return modello.model_validate(risposta.json())
    except (ValueError, ValidationError) as exc:
        raise RispostaNonValida(
            f"Risposta di {risposta.request.method} {risposta.request.url.path} non "
            f"conforme a {modello.__name__}: {str(exc)[:500]}") from None


def intestazioni_base(credenziali: Credenziali | None) -> dict[str, str]:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json, application/problem+json"}
    if credenziali is not None:
        h.update(credenziali.intestazioni())
    return h


# ── Versamento: indice e file ────────────────────────────────────────

def json_indice(indice: IndiceUD) -> str:
    """JSON dell'indice da inviare (i campi non valorizzati non si inviano: il
    servizio applica i propri predefiniti)."""
    return indice.model_dump_json(exclude_none=True)


def come_indice(indice: IndiceUD | Mapping[str, Any]) -> IndiceUD:
    if isinstance(indice, IndiceUD):
        return indice.model_copy(deep=True)
    return IndiceUD.model_validate(indice)


def prepara_sorgenti(indice: IndiceUD, file: Mapping[str, Any]) -> dict[str, Sorgente]:
    """Sorgenti per nome di file, controllando che ogni file sia citato nell'indice e
    ogni documento dell'indice abbia il suo file."""
    nomi_indice = [d.nome_file for d in indice.documenti_tutti()]
    sorgenti = {nome: come_sorgente(v, nome) for nome, v in file.items()}
    estranei = sorted(set(sorgenti) - set(nomi_indice))
    mancanti = sorted(set(nomi_indice) - set(sorgenti))
    if estranei or mancanti:
        raise ValueError(f"File e indice non corrispondono: senza documento {estranei}, "
                         f"senza file {mancanti}")
    return sorgenti


def documenti_senza_impronta(indice: IndiceUD) -> list[DocumentoIndice]:
    return [d for d in indice.documenti_tutti() if d.hash is None]


def imposta_impronta(indice: IndiceUD, nome_file: str, sha256: str) -> None:
    for d in indice.documenti_tutti():
        if d.nome_file == nome_file:
            if d.hash is None:
                d.hash = ImprontaFile(impronta=sha256, algoritmo="SHA-256")
            elif d.hash.algoritmo.upper() == "SHA-256" and d.hash.impronta.lower() != sha256:
                from verumdoc_sdk.errori import ErroreIntegrita

                raise ErroreIntegrita(
                    f"Impronta dichiarata nell'indice per {nome_file!r} diversa da quella "
                    "dei byte letti")


def corpo_multipart(indice: IndiceUD, sorgenti: Mapping[str, Sorgente]) -> FabbricaCorpo:
    """Corpo multipart (indice + file) ricostruito a ogni tentativo: i file si riaprono
    da capo e si inviano a blocchi (nessun file intero in memoria)."""
    testo = json_indice(indice)

    def fabbrica() -> tuple[dict[str, Any], Callable[[], None]]:
        aperti = []
        files = []
        for nome, s in sorgenti.items():
            f = s.apri()
            aperti.append(f)
            files.append(("files", (nome, f, s.mime or "application/octet-stream")))

        def chiudi() -> None:
            for f in aperti:
                f.close()

        return {"data": {"indice": testo}, "files": files}, chiudi

    return fabbrica


def corpo_per_riferimento(indice: IndiceUD, caricamenti: Mapping[str, str]) -> dict[str, Any]:
    return {"indice": json.loads(json_indice(indice)), "file": dict(caricamenti)}


def dimensione_attesa(numero: int, dimensione: int, dimensione_parte: int) -> int:
    """Byte della parte ``numero`` (da 1): l'ultima porta il resto."""
    inizio = (numero - 1) * dimensione_parte
    return max(0, min(dimensione_parte, dimensione - inizio))


def intestazione_parte(dati: bytes) -> str:
    return "sha256=" + hashlib.sha256(dati).hexdigest()
