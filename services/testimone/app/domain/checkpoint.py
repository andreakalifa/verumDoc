# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/domain/checkpoint.py — checkpoint del registro e chiave
del testimone, sopra ``libs.catena.nota`` (contratti K2 e K4).

Nessuna copia dell'algoritmo: formato della nota, key hash, firme Ed25519 e
verifier key vengono da ``libs.catena``. Qui stanno solo le regole del
testimone: quale firma conta (quella del registro, con le chiavi configurate),
che cosa si conserva (la nota così come è arrivata) e come si firma (il corpo
del checkpoint, a capo finale compreso, con il nome ``verumdoc-testimone``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from libs.catena import nota as nota_lib


class CheckpointNonValido(ValueError):
    """La nota non è un checkpoint K2 ben formato."""


class FirmaRegistroNonValida(ValueError):
    """Nessuna firma valida del registro sulla nota."""


class ConfigurazioneNonValida(ValueError):
    """Chiavi del testimone o del registro non utilizzabili."""


@dataclass(frozen=True)
class CheckpointRegistro:
    """Checkpoint con firma del registro verificata."""

    origine: str
    dimensione: int
    radice: bytes
    corpo: str  # testo firmato (origine, dimensione, radice, a capo finale compreso)
    nota: str  # nota completa come ricevuta (corpo + righe di firma)


@dataclass(frozen=True)
class ChiaveTestimone:
    """Chiave Ed25519 del testimone (``libs.catena.nota.Firmatario``)."""

    firmatario: nota_lib.Firmatario = field(repr=False)

    @property
    def nome(self) -> str:
        return self.firmatario.nome

    @property
    def verificatore(self) -> str:
        """Verifier key pubblica («nome+keyhash+base64»), da distribuire ai clienti."""
        return self.firmatario.verificatore.testo

    def firma(self, corpo: str) -> str:
        """Riga «— nome base64(keyhash || firma)» sul corpo del checkpoint."""
        return self.firmatario.firma(corpo)


def carica_chiave_testimone(testo: str, nome: str) -> ChiaveTestimone:
    """Chiave privata nel formato ``PRIVATE+KEY+nome+keyhash+base64`` (lo stesso
    del registro e di Go ``note.NewSigner``), con il nome atteso."""
    if not testo.strip():
        raise ConfigurazioneNonValida("TESTIMONE_CHIAVE_PRIVATA non impostata")
    try:
        return ChiaveTestimone(nota_lib.analizza_firmatario(testo, nome_atteso=nome))
    except ValueError as exc:
        # Il messaggio di libs.catena non contiene mai la chiave
        raise ConfigurazioneNonValida(f"TESTIMONE_CHIAVE_PRIVATA non valida: {exc}") from exc


def controlla_verificatori(verificatori: list[str]) -> None:
    """Le verifier key del registro devono essere leggibili da libs.catena."""
    if not verificatori:
        raise ConfigurazioneNonValida("TESTIMONE_CHIAVI_REGISTRO non impostata")
    for v in verificatori:
        try:
            nota_lib.analizza_verificatore(v)
        except ValueError as exc:
            raise ConfigurazioneNonValida(f"Chiave del registro non valida: {v[:40]}…") from exc


def verifica_checkpoint_registro(testo: str, verificatori: list[str]) -> CheckpointRegistro:
    """Nota → checkpoint, solo se porta una firma valida di una chiave del registro."""
    try:
        valide = nota_lib.verifica(testo, verificatori)
    except ValueError as exc:
        raise CheckpointNonValido(f"Nota non valida: {exc}") from exc
    if not valide:
        raise FirmaRegistroNonValida(
            "La nota non porta una firma valida della chiave del registro configurata"
        )
    try:
        cp = nota_lib.analizza(testo)
    except ValueError as exc:
        raise CheckpointNonValido(f"Checkpoint non valido: {exc}") from exc
    corpo = nota_lib.formatta(cp)
    if not testo.startswith(corpo + "\n"):
        raise CheckpointNonValido("Il corpo firmato non è nel formato canonico del checkpoint")
    return CheckpointRegistro(
        origine=cp.origine,
        dimensione=cp.dimensione,
        radice=bytes(cp.radice),
        corpo=corpo,
        nota=testo,
    )


@dataclass
class ContestoTestimone:
    """Configurazione caricata all'avvio e condivisa dalle richieste."""

    chiave: ChiaveTestimone
    verificatori_registro: list[str]
    origini: list[str]
    impronte_token: list[str]
    max_corpo_byte: int
    max_righe_prova: int
    metriche: Any

    def verifica_registro(self, testo: str) -> CheckpointRegistro:
        return verifica_checkpoint_registro(testo, self.verificatori_registro)


__all__ = [
    "CheckpointNonValido",
    "CheckpointRegistro",
    "ChiaveTestimone",
    "ConfigurazioneNonValida",
    "ContestoTestimone",
    "FirmaRegistroNonValida",
    "carica_chiave_testimone",
    "controlla_verificatori",
    "verifica_checkpoint_registro",
]
