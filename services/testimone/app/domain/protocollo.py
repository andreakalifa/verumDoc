# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/domain/protocollo.py — regole pure del testimone (K3).

Riferimenti: contratto K3 di R04 (ispirato a C2SP tlog-witness), K2 (formato
del checkpoint), RFC 6962 §2.1.2 / RFC 9162 §2.1.4.2 (consistenza),
PIANO_R03_R05 §5.3. Nessun I/O, nessun orologio (REGOLE §7.2, §17.6): legge →
converte → decide; le scritture le fa ``app.application.controfirma``.

Corpo della richiesta di controfirma (``text/plain``, UTF-8)::

    old <dimensione precedente>\\n
    <hash della prova di consistenza in base64>\\n     (zero o più righe)
    \\n
    <checkpoint K2: signed note completa>

Decisione (``decidi``), nell'ordine:
  1. il testimone ha già controfirmato, nell'epoca corrente, un checkpoint della
     STESSA dimensione: radice uguale → IDEMPOTENTE (si restituisce la stessa
     controfirma); radice diversa → FORK. Due note firmate dal registro con la
     stessa dimensione e radici diverse sono la prova di un registro biforcato:
     vale qualunque cosa dica ``old``;
  2. ``old`` diverso dall'ultima dimensione controfirmata → OLD_NON_COINCIDE
     (409 con la dimensione nota);
  3. ``old`` maggiore della dimensione del checkpoint → OLD_OLTRE (400);
  4. nessun checkpoint precedente (``old`` 0): si accetta il primo (fiducia al
     primo uso, documentata) purché la prova sia vuota;
  5. altrimenti la prova deve dimostrare che il nuovo albero estende l'ultimo
     controfirmato (``libs.catena.rfc6962.verifica_consistenza``), oppure
     PROVA_NON_VALIDA (422 + allarme).
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from enum import StrEnum

from libs.catena import nota as nota_lib
from libs.catena import rfc6962

_RIGA_OLD = re.compile(r"^old (0|[1-9][0-9]{0,18})$")
_BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


class RichiestaNonValida(ValueError):
    """Corpo della richiesta di controfirma non conforme al protocollo."""


class RichiestaTroppoGrande(ValueError):
    """Corpo oltre i limiti configurati (righe della prova)."""


@dataclass(frozen=True)
class Richiesta:
    """Richiesta di controfirma già analizzata."""

    old: int
    prova: tuple[bytes, ...]
    nota: str

    def prova_testo(self) -> str:
        """Le righe della prova come sono arrivate (base64), per gli allarmi."""
        return "\n".join(base64.b64encode(p).decode("ascii") for p in self.prova)


@dataclass(frozen=True)
class UltimoControfirmato:
    """Ultimo checkpoint controfirmato nell'epoca corrente."""

    dimensione: int
    radice: bytes


class Esito(StrEnum):
    CONTROFIRMA = "CONTROFIRMA"
    IDEMPOTENTE = "IDEMPOTENTE"
    FORK = "FORK"
    OLD_NON_COINCIDE = "OLD_NON_COINCIDE"
    OLD_OLTRE = "OLD_OLTRE"
    PROVA_NON_VALIDA = "PROVA_NON_VALIDA"


def analizza_richiesta(corpo: bytes, *, max_righe_prova: int) -> Richiesta:
    """Separa riga ``old``, prova e nota. Errori espliciti, mai interpretazioni."""
    try:
        testo = corpo.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RichiestaNonValida("Il corpo non è testo UTF-8") from exc
    if "\r" in testo:
        raise RichiestaNonValida("Il protocollo usa solo «\\n» come fine riga")
    testa, separatore, nota = testo.partition("\n\n")
    if not separatore:
        raise RichiestaNonValida("Manca la riga vuota fra la prova e il checkpoint")
    righe = testa.split("\n")
    corrispondenza = _RIGA_OLD.match(righe[0])
    if corrispondenza is None:
        raise RichiestaNonValida("La prima riga deve essere «old <dimensione decimale>»")
    righe_prova = righe[1:]
    if len(righe_prova) > max_righe_prova:
        raise RichiestaTroppoGrande(
            f"Prova di {len(righe_prova)} righe: al massimo {max_righe_prova}"
        )
    prova: list[bytes] = []
    for numero, riga in enumerate(righe_prova, start=1):
        if not _BASE64.match(riga):
            raise RichiestaNonValida(f"Riga {numero} della prova non è base64 standard")
        try:
            valore = base64.b64decode(riga, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RichiestaNonValida(f"Riga {numero} della prova non è base64 valido") from exc
        if len(valore) != rfc6962.LUNGHEZZA_HASH:
            raise RichiestaNonValida(
                f"Riga {numero} della prova: attesi {rfc6962.LUNGHEZZA_HASH} byte, "
                f"ricevuti {len(valore)}"
            )
        prova.append(valore)
    if not nota:
        raise RichiestaNonValida("Manca il checkpoint dopo la riga vuota")
    return Richiesta(old=int(corrispondenza.group(1)), prova=tuple(prova), nota=nota)


def decidi(
    richiesta: Richiesta,
    *,
    dimensione: int,
    radice: bytes,
    testa: UltimoControfirmato | None,
    radice_stessa_dimensione: bytes | None,
) -> Esito:
    """Esito della richiesta per il checkpoint (``dimensione``, ``radice``) la
    cui firma del registro è già stata verificata. ``radice_stessa_dimensione``
    è la radice già controfirmata nell'epoca corrente con la stessa dimensione."""
    if radice_stessa_dimensione is not None:
        return Esito.IDEMPOTENTE if radice_stessa_dimensione == radice else Esito.FORK
    corrente = testa.dimensione if testa is not None else 0
    if richiesta.old != corrente:
        return Esito.OLD_NON_COINCIDE
    if richiesta.old > dimensione:
        return Esito.OLD_OLTRE
    if testa is None:
        return Esito.CONTROFIRMA if not richiesta.prova else Esito.PROVA_NON_VALIDA
    if rfc6962.verifica_consistenza(
        testa.dimensione,
        dimensione,
        testa.radice,
        radice,
        list(richiesta.prova),
    ):
        return Esito.CONTROFIRMA
    return Esito.PROVA_NON_VALIDA


def nota_controfirmata(nota_registro: str, riga_firma_testimone: str, nome_testimone: str) -> str:
    """Nota pubblicata: corpo e firme del registro (senza eventuali vecchie righe
    dello stesso testimone) più la riga di firma del testimone, in fondo."""
    corpo, righe = nota_lib.separa(nota_registro)
    tenute = [r.riga for r in righe if r.nome != nome_testimone]
    return nota_lib.componi(corpo, [*tenute, riga_firma_testimone])


__all__ = [
    "Esito",
    "Richiesta",
    "RichiestaNonValida",
    "RichiestaTroppoGrande",
    "UltimoControfirmato",
    "analizza_richiesta",
    "decidi",
    "nota_controfirmata",
]
