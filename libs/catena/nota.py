# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/catena/nota.py — checkpoint del registro (C2SP tlog-checkpoint) dentro una
nota firmata (C2SP signed-note), con firme Ed25519.

Riferimenti: contratto K2 di R04; specifiche C2SP «signed-note» e
«tlog-checkpoint» (stesso formato di ``golang.org/x/mod/sumdb/note`` e dei
registri di trasparenza: le note prodotte qui si verificano con quel pacchetto
e viceversa, vedi ``tests/test_oracolo_go.py``).

Formato del corpo del checkpoint (ogni riga termina con a capo)::

    <origine>                      es. verumdoc.it/registro/v1
    <dimensione decimale>
    <radice in base64 standard>
    [<riga di estensione>...]      facoltative, non usate dal registro

Nota firmata = corpo + riga vuota + una riga per firma::

    — <nome chiave> <base64(key hash 4 byte || firma 64 byte)>

  - key hash (Ed25519) = primi 4 byte di SHA-256(nome || "\\n" || 0x01 || chiave pubblica);
  - la firma Ed25519 copre i byte UTF-8 del corpo, a capo finale compreso;
  - chiave pubblica distribuita come «verifier key»:
    ``<nome>+<key hash esadecimale 8 cifre>+<base64(0x01 || chiave pubblica)>``;
  - chiave privata (formato di ``note.NewSigner``):
    ``PRIVATE+KEY+<nome>+<key hash>+<base64(0x01 || seme di 32 byte)>``.

Solo libreria standard + ``cryptography`` (Ed25519).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ALGORITMO_ED25519 = 0x01
TRATTINO = "—"  # «—» (em dash) che apre ogni riga di firma
PREFISSO_PRIVATA = "PRIVATE+KEY+"
MASSIMO_FIRME = 100
_DIMENSIONE = re.compile(r"(0|[1-9][0-9]{0,19})")


class ErroreNota(ValueError):
    """Nota, checkpoint o chiave malformati."""


# ── Checkpoint ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Checkpoint:
    """Corpo di un checkpoint: origine del registro, dimensione e radice."""

    origine: str
    dimensione: int
    radice: bytes
    estensioni: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.origine or "\n" in self.origine or self.origine != self.origine.strip():
            raise ErroreNota("Origine del checkpoint vuota o con a capo/spazi ai bordi")
        if self.dimensione < 0:
            raise ErroreNota("Dimensione del checkpoint negativa")
        if len(self.radice) != 32:
            raise ErroreNota("Radice del checkpoint: attesi 32 byte (SHA-256)")
        for riga in self.estensioni:
            if not riga or "\n" in riga:
                raise ErroreNota("Riga di estensione vuota o con a capo")


def formatta(cp: Checkpoint) -> str:
    """Testo del corpo del checkpoint (termina con a capo)."""
    righe = [cp.origine, str(cp.dimensione), base64.b64encode(cp.radice).decode("ascii"),
             *cp.estensioni]
    return "\n".join(righe) + "\n"


def analizza(testo: str) -> Checkpoint:
    """Checkpoint dal testo del corpo o da una nota firmata completa (le firme
    NON vengono verificate qui: usare ``verifica``)."""
    corpo, _ = separa(testo) if "\n\n" in testo else (testo, [])
    if not corpo.endswith("\n"):
        raise ErroreNota("Il corpo del checkpoint deve terminare con a capo")
    righe = corpo[:-1].split("\n")
    if len(righe) < 3:
        raise ErroreNota("Checkpoint incompleto: servono origine, dimensione e radice")
    origine, dimensione, radice_b64, *estensioni = righe
    if not _DIMENSIONE.fullmatch(dimensione):
        raise ErroreNota(f"Dimensione non decimale canonica: {dimensione!r}")
    try:
        radice = base64.b64decode(radice_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ErroreNota("Radice non in base64 standard") from exc
    if base64.b64encode(radice).decode("ascii") != radice_b64:
        raise ErroreNota("Radice in base64 non canonico")
    return Checkpoint(origine, int(dimensione), radice, tuple(estensioni))


# ── Chiavi ────────────────────────────────────────────────────────────

def _controlla_nome(nome: str) -> None:
    if not nome or "+" in nome or any(c.isspace() for c in nome) or any(
        unicodedata.category(c).startswith("C") for c in nome
    ):
        raise ErroreNota(f"Nome di chiave non valido: {nome!r} (vuoto, con spazi o '+')")


def _bytes_pubblica(chiave: Ed25519PublicKey | bytes) -> bytes:
    if isinstance(chiave, Ed25519PublicKey):
        return chiave.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    dati = bytes(chiave)
    if len(dati) != 32:
        raise ErroreNota("Chiave pubblica Ed25519: attesi 32 byte")
    return dati


def _chiave_privata(chiave: Ed25519PrivateKey | bytes) -> Ed25519PrivateKey:
    if isinstance(chiave, Ed25519PrivateKey):
        return chiave
    dati = bytes(chiave)
    if len(dati) != 32:
        raise ErroreNota("Chiave privata Ed25519: atteso il seme di 32 byte")
    return Ed25519PrivateKey.from_private_bytes(dati)


def key_hash(nome: str, chiave_pubblica: Ed25519PublicKey | bytes) -> int:
    """Key hash della signed note (uint32 big endian)."""
    _controlla_nome(nome)
    materiale = nome.encode("utf-8") + b"\n" + bytes([ALGORITMO_ED25519]) \
        + _bytes_pubblica(chiave_pubblica)
    return int.from_bytes(hashlib.sha256(materiale).digest()[:4], "big")


@dataclass(frozen=True)
class Verificatore:
    """Chiave pubblica di verifica con il suo nome."""

    nome: str
    key_hash: int
    chiave_pubblica: bytes

    def verifica(self, testo: bytes, firma: bytes) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(self.chiave_pubblica).verify(firma, testo)
        except (InvalidSignature, ValueError):
            return False
        return True

    @property
    def testo(self) -> str:
        return chiave_verificatore(self.nome, self.chiave_pubblica)


@dataclass(frozen=True)
class Firmatario:
    """Chiave privata di firma con il suo nome (mai stampata: ``repr`` la nasconde)."""

    nome: str
    key_hash: int
    chiave_privata: Ed25519PrivateKey = field(repr=False)

    @property
    def verificatore(self) -> Verificatore:
        pubblica = _bytes_pubblica(self.chiave_privata.public_key())
        return Verificatore(self.nome, self.key_hash, pubblica)

    def firma(self, testo: str) -> str:
        return firma(testo, self.nome, self.chiave_privata)


def chiave_verificatore(nome: str, chiave_pubblica: Ed25519PublicKey | bytes) -> str:
    """Verifier key ``nome+keyhash+base64(0x01 || chiave pubblica)``."""
    pubblica = _bytes_pubblica(chiave_pubblica)
    codifica = base64.b64encode(bytes([ALGORITMO_ED25519]) + pubblica).decode("ascii")
    return f"{nome}+{key_hash(nome, pubblica):08x}+{codifica}"


def analizza_verificatore(testo: str) -> Verificatore:
    """Verificatore da una verifier key; ErroreNota se malformata o incoerente."""
    parti = testo.strip().split("+", 2)  # il base64 può contenere «+»
    if len(parti) != 3:
        raise ErroreNota("Verifier key: atteso nome+keyhash+chiave")
    nome, hash_esadecimale, codifica = parti
    _controlla_nome(nome)
    try:
        materiale = base64.b64decode(codifica, validate=True)
        dichiarato = int(hash_esadecimale, 16)
    except (binascii.Error, ValueError) as exc:
        raise ErroreNota("Verifier key con codifica non valida") from exc
    if len(hash_esadecimale) != 8 or len(materiale) != 33 or materiale[0] != ALGORITMO_ED25519:
        raise ErroreNota("Verifier key: algoritmo diverso da Ed25519 o lunghezza errata")
    pubblica = materiale[1:]
    if key_hash(nome, pubblica) != dichiarato:
        raise ErroreNota("Verifier key: key hash non corrispondente alla chiave")
    return Verificatore(nome, dichiarato, pubblica)


def chiave_firmatario(nome: str, chiave_privata: Ed25519PrivateKey | bytes) -> str:
    """Chiave privata nel formato ``PRIVATE+KEY+nome+keyhash+base64(0x01 || seme)``."""
    privata = _chiave_privata(chiave_privata)
    seme = privata.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
    codifica = base64.b64encode(bytes([ALGORITMO_ED25519]) + seme).decode("ascii")
    return f"{PREFISSO_PRIVATA}{nome}+{key_hash(nome, privata.public_key()):08x}+{codifica}"


def analizza_firmatario(testo: str, nome_atteso: str | None = None) -> Firmatario:
    """Firmatario da ``PRIVATE+KEY+...``; ErroreNota se malformata, incoerente o
    con un nome diverso da ``nome_atteso``. Il messaggio non contiene mai la chiave."""
    valore = testo.strip()
    if not valore.startswith(PREFISSO_PRIVATA):
        raise ErroreNota("Chiave privata: atteso il formato PRIVATE+KEY+nome+keyhash+chiave")
    parti = valore[len(PREFISSO_PRIVATA):].split("+", 2)
    if len(parti) != 3:
        raise ErroreNota("Chiave privata: atteso PRIVATE+KEY+nome+keyhash+chiave")
    nome, hash_esadecimale, codifica = parti
    _controlla_nome(nome)
    if nome_atteso is not None and nome != nome_atteso:
        raise ErroreNota(f"Chiave privata con nome {nome!r}, atteso {nome_atteso!r}")
    try:
        materiale = base64.b64decode(codifica, validate=True)
        dichiarato = int(hash_esadecimale, 16)
    except (binascii.Error, ValueError) as exc:
        raise ErroreNota("Chiave privata con codifica non valida") from exc
    if len(materiale) != 33 or materiale[0] != ALGORITMO_ED25519:
        raise ErroreNota("Chiave privata: algoritmo diverso da Ed25519 o lunghezza errata")
    privata = Ed25519PrivateKey.from_private_bytes(materiale[1:])
    if key_hash(nome, privata.public_key()) != dichiarato:
        raise ErroreNota("Chiave privata: key hash non corrispondente")
    return Firmatario(nome, dichiarato, privata)


def genera_chiave(nome: str) -> tuple[str, str]:
    """Nuova coppia: (chiave privata ``PRIVATE+KEY+…``, verifier key)."""
    privata = Ed25519PrivateKey.generate()
    return chiave_firmatario(nome, privata), chiave_verificatore(nome, privata.public_key())


# ── Note firmate ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class RigaFirma:
    """Riga di firma analizzata (non ancora verificata)."""

    nome: str
    key_hash: int
    firma: bytes
    riga: str


@dataclass(frozen=True)
class FirmaValida:
    """Firma verificata con un verificatore noto."""

    nome: str
    key_hash: int


def _controlla_testo(testo: str) -> None:
    if not testo or not testo.endswith("\n"):
        raise ErroreNota("Il testo della nota deve essere non vuoto e terminare con a capo")
    if any((ord(c) < 0x20 and c != "\n") or ord(c) == 0x7F for c in testo):
        raise ErroreNota("Il testo della nota contiene caratteri di controllo")


def firma(testo: str, nome: str, chiave_privata: Ed25519PrivateKey | bytes) -> str:
    """Riga di firma (senza a capo finale) del corpo ``testo``."""
    _controlla_testo(testo)
    privata = _chiave_privata(chiave_privata)
    valore = privata.sign(testo.encode("utf-8"))
    hash_chiave = key_hash(nome, privata.public_key())
    codifica = base64.b64encode(struct.pack(">I", hash_chiave) + valore).decode("ascii")
    return f"{TRATTINO} {nome} {codifica}"


def analizza_riga_firma(riga: str) -> RigaFirma:
    """Riga ``— nome base64`` (con o senza a capo finale)."""
    riga = riga.rstrip("\n")
    if not riga.startswith(TRATTINO + " "):
        raise ErroreNota("Riga di firma: deve iniziare con «— »")
    parti = riga[2:].split(" ")
    if len(parti) != 2:
        raise ErroreNota("Riga di firma: atteso «— nome firma»")
    nome, codifica = parti
    _controlla_nome(nome)
    try:
        dati = base64.b64decode(codifica, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ErroreNota("Riga di firma: base64 non valido") from exc
    if len(dati) < 5:
        raise ErroreNota("Riga di firma: firma troppo corta")
    return RigaFirma(nome, struct.unpack(">I", dati[:4])[0], dati[4:], riga)


def separa(nota: str) -> tuple[str, list[RigaFirma]]:
    """(corpo, righe di firma) di una nota firmata."""
    divisione = nota.rfind("\n\n")
    if divisione < 0:
        raise ErroreNota("Nota senza firme (manca la riga vuota prima delle firme)")
    corpo, firme = nota[: divisione + 1], nota[divisione + 2:]
    _controlla_testo(corpo)
    if not firme.endswith("\n"):
        raise ErroreNota("Le righe di firma devono terminare con a capo")
    righe = [analizza_riga_firma(r) for r in firme[:-1].split("\n")]
    if not righe or len(righe) > MASSIMO_FIRME:
        raise ErroreNota(f"Una nota ha da 1 a {MASSIMO_FIRME} firme")
    return corpo, righe


def componi(corpo: str, righe: Iterable[str]) -> str:
    """Nota firmata dal corpo e dalle righe di firma (duplicati tolti, ordine mantenuto)."""
    _controlla_testo(corpo)
    viste: list[str] = []
    for riga in righe:
        r = analizza_riga_firma(riga).riga
        if r not in viste:
            viste.append(r)
    if not viste:
        raise ErroreNota("Una nota firmata ha almeno una firma")
    return corpo + "\n" + "".join(r + "\n" for r in viste)


def aggiungi_firme(nota: str, righe: Iterable[str]) -> str:
    """Aggiunge righe di firma (es. la controfirma del testimone) a una nota."""
    corpo, esistenti = separa(nota)
    return componi(corpo, [*(r.riga for r in esistenti), *righe])


def verifica(
    nota: str, verificatori: Iterable[Verificatore | str],
) -> list[FirmaValida]:
    """
    Firme VALIDE della nota fra quelle dei verificatori indicati (oggetti o
    verifier key). Le firme di chiavi sconosciute si ignorano; una firma di una
    chiave nota che non torna NON compare nell'elenco. ErroreNota se la nota è
    malformata.
    """
    corpo, righe = separa(nota)
    noti: dict[tuple[str, int], Verificatore] = {}
    for v in verificatori:
        oggetto = analizza_verificatore(v) if isinstance(v, str) else v
        noti[(oggetto.nome, oggetto.key_hash)] = oggetto
    testo = corpo.encode("utf-8")
    valide: list[FirmaValida] = []
    for riga in righe:
        verificatore = noti.get((riga.nome, riga.key_hash))
        if verificatore is None or len(riga.firma) != 64:
            continue
        valida = FirmaValida(riga.nome, riga.key_hash)
        if verificatore.verifica(testo, riga.firma) and valida not in valide:
            valide.append(valida)
    return valide


__all__ = [
    "ALGORITMO_ED25519", "Checkpoint", "ErroreNota", "FirmaValida", "Firmatario",
    "RigaFirma", "TRATTINO", "Verificatore", "aggiungi_firme", "analizza",
    "analizza_firmatario", "analizza_riga_firma", "analizza_verificatore",
    "chiave_firmatario", "chiave_verificatore", "componi", "firma", "formatta",
    "genera_chiave", "key_hash", "separa", "verifica",
]
