# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/catena/attestazione.py — attestazione firmata del ciclo di vita di un PdA.

Revisione 2 di R05 (rilievo S8). Il registro delle impronte non dà prove di NON
appartenenza: chi presenta a un terzo una prova d'inclusione potrebbe togliere
la foglia di scarto (o di annullamento) e i campi del conteggio, e il
verificatore direbbe «conservato». Per impedirlo la prova porta
un'ATTESTAZIONE: una nota firmata (C2SP signed note, Ed25519) con la STESSA
chiave del registro che firma i checkpoint, che impegna, per quel PdA e quel
checkpoint, il NUMERO delle foglie del ciclo di vita e l'IMPRONTA del loro
elenco (indice e hash di foglia RFC 6962 di ciascuna). Il verificatore:

  - controlla la firma con la chiave del registro e che origine e dimensione
    siano quelle del checkpoint della prova e il PdA quello della prova;
  - ricalcola l'impronta dalle foglie presentate (se l'elenco è completo) e il
    loro numero: una foglia tolta, aggiunta o cambiata non torna;
  - senza attestazione l'assenza di uno scarto NON è dimostrabile: lo stato del
    documento non è determinabile (mai «conservato»).

Revisione 3 di R05 (T2, T3): l'attestazione v1 si otteneva per QUALUNQUE
checkpoint, anche vecchio, e non diceva di quando fosse lo stato: dopo uno
scarto, una prova chiesta a un checkpoint precedente (o scaricata prima e
conservata) risultava «conservato» senza alcuna avvertenza. La v2:

  - si rilascia SOLO per l'ultimo checkpoint del registro (la hashchain la
    omette per un checkpoint più vecchio);
  - impegna l'ISTANTE del rilascio (``rilasciata``, orologio del database): il
    verificatore scrive sempre «stato al checkpoint N del <data>» e avverte se
    gli si fornisce un checkpoint più recente;
  - impegna lo STATO del PdA in quell'istante (``stato``): ``CONSERVATO``,
    ``SCARTO`` (scarto avviato o eseguito: contenuto non più garantito) o
    ``SCARTO_PARZIALE`` (cancellazione iniziata e non completata,
    ``ELIMINAZIONE_INCOMPLETA``): mai «conservato» per un PdA mutilato, anche
    senza foglia di scarto.

Corpo della nota (righe; nessuna può essere letta come un checkpoint, la
seconda non è un numero)::

    verumdoc.it/attestazione-ciclo-vita/v2
    origine <origine del checkpoint>
    checkpoint <dimensione>
    rilasciata <AAAA-MM-GGThh:mm:ssZ>
    pda <URN del PdA>
    stato <CONSERVATO | SCARTO | SCARTO_PARZIALE>
    foglie <numero>
    elenco <SHA-256 esadecimale delle righe «<indice> <hash di foglia hex>\\n»>

Solo libreria standard + ``cryptography`` (tramite ``nota``): la usa anche il
verificatore indipendente.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from libs.catena import nota

INTESTAZIONE = "verumdoc.it/attestazione-ciclo-vita/v2"
_DECIMALE = re.compile(r"0|[1-9][0-9]{0,19}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ISTANTE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

#: Stati del PdA attestati (revisione 3 di R05, T3)
STATO_CONSERVATO = "CONSERVATO"
STATO_SCARTO = "SCARTO"
STATO_SCARTO_PARZIALE = "SCARTO_PARZIALE"
STATI = frozenset({STATO_CONSERVATO, STATO_SCARTO, STATO_SCARTO_PARZIALE})
_CAMPI = ("origine", "checkpoint", "rilasciata", "pda", "stato", "foglie", "elenco")


def stato_da_aip(stato_aip: str | None) -> str:
    """Stato attestato dallo stato del PdA nel database: ``ELIMINAZIONE`` (scarto
    avviato o eseguito) → SCARTO; ``ELIMINAZIONE_INCOMPLETA`` → SCARTO_PARZIALE;
    altrimenti (il PdA è nel registro, quindi i suoi oggetti sono nel WORM)
    CONSERVATO."""
    if stato_aip == "ELIMINAZIONE":
        return STATO_SCARTO
    if stato_aip == "ELIMINAZIONE_INCOMPLETA":
        return STATO_SCARTO_PARZIALE
    return STATO_CONSERVATO


def istante(valore: datetime) -> str:
    """Istante canonico (UTC, secondi) della riga ``rilasciata``."""
    if valore.tzinfo is None:
        raise AttestazioneNonValida("istante senza fuso orario")
    return valore.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AttestazioneNonValida(ValueError):
    """Attestazione malformata."""


@dataclass(frozen=True)
class Attestazione:
    origine: str
    dimensione: int
    pda: str
    foglie: int
    elenco: str
    #: T2: istante del rilascio (``AAAA-MM-GGThh:mm:ssZ``)
    rilasciata: str
    #: T3: stato del PdA in quell'istante (``STATI``)
    stato: str

    def __post_init__(self) -> None:
        for nome, valore in (("origine", self.origine), ("pda", self.pda)):
            if not valore or valore != valore.strip() or any(c in valore for c in "\n\r"):
                raise AttestazioneNonValida(f"{nome} vuota o con spazi/a capo ai bordi")
        if self.dimensione < 0 or self.foglie < 0:
            raise AttestazioneNonValida("dimensione e numero di foglie non negativi")
        if not _HEX64.fullmatch(self.elenco):
            raise AttestazioneNonValida("elenco: atteso SHA-256 esadecimale minuscolo")
        if not _ISTANTE.fullmatch(self.rilasciata):
            raise AttestazioneNonValida("rilasciata: atteso AAAA-MM-GGThh:mm:ssZ")
        if self.stato not in STATI:
            raise AttestazioneNonValida(f"stato: atteso uno fra {', '.join(sorted(STATI))}")


def impronta_elenco(voci: Iterable[tuple[int, bytes]]) -> str:
    """SHA-256 dell'elenco ordinato per indice: una riga «<indice> <hash hex>»."""
    righe = sorted((int(i), bytes(h)) for i, h in voci)
    return hashlib.sha256("".join(f"{i} {h.hex()}\n" for i, h in righe).encode()).hexdigest()


def corpo(att: Attestazione) -> str:
    return (f"{INTESTAZIONE}\norigine {att.origine}\ncheckpoint {att.dimensione}\n"
            f"rilasciata {att.rilasciata}\npda {att.pda}\nstato {att.stato}\n"
            f"foglie {att.foglie}\nelenco {att.elenco}\n")


def firma(att: Attestazione, firmatario: nota.Firmatario) -> str:
    """Nota firmata con la chiave del registro."""
    testo = corpo(att)
    return nota.componi(testo, [firmatario.firma(testo)])


def analizza(testo: str) -> Attestazione:
    """Attestazione dal testo di una nota firmata (le firme NON si verificano qui)."""
    try:
        testo_corpo, _ = nota.separa(testo)
    except nota.ErroreNota as exc:
        raise AttestazioneNonValida(str(exc)) from exc
    righe = testo_corpo[:-1].split("\n")
    if len(righe) != len(_CAMPI) + 1 or righe[0] != INTESTAZIONE:
        raise AttestazioneNonValida("non è un'attestazione del ciclo di vita (v2)")
    valori: dict[str, str] = {}
    for riga, chiave in zip(righe[1:], _CAMPI, strict=True):
        if not riga.startswith(chiave + " "):
            raise AttestazioneNonValida(f"riga «{chiave}» mancante o fuori posto")
        valori[chiave] = riga[len(chiave) + 1:]
    for chiave in ("checkpoint", "foglie"):
        if not _DECIMALE.fullmatch(valori[chiave]):
            raise AttestazioneNonValida(f"{chiave}: decimale canonico atteso")
    return Attestazione(origine=valori["origine"], dimensione=int(valori["checkpoint"]),
                        pda=valori["pda"], foglie=int(valori["foglie"]),
                        elenco=valori["elenco"], rilasciata=valori["rilasciata"],
                        stato=valori["stato"])


def firmata_da(testo: str, verificatore: nota.Verificatore | str) -> bool:
    """True se la nota ha una firma valida della chiave indicata (il registro)."""
    try:
        return bool(nota.verifica(testo, [verificatore]))
    except nota.ErroreNota:
        return False


__all__ = ["INTESTAZIONE", "STATI", "STATO_CONSERVATO", "STATO_SCARTO", "STATO_SCARTO_PARZIALE",
           "Attestazione", "AttestazioneNonValida", "analizza", "corpo", "firma", "firmata_da",
           "impronta_elenco", "istante", "stato_da_aip"]
