# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/formati/riconoscimento.py — riconoscimento dei formati dietro
un'interfaccia (R08, decisione D02; VD-C-074).

Realizzazioni:
  - ``RiconoscitoreSiegfried`` (``siegfried.py``): identificazione PRONOM
    (PUID, versione, MIME) con Siegfried e il file di firme DROID/PRONOM;
  - ``RiconoscitoreFirmaBinaria``: il ripiego, firma binaria sui primi KB
    (``firma_binaria.py``), solo MIME, nessun PUID;
  - ``RiconoscitoreConRipiego``: Siegfried, con la firma binaria usata SOLO per
    riconoscere comunque gli eseguibili (VD-C-017). Revisione R08 (X11): niente
    ripiego silenzioso. Se Siegfried non è eseguibile, termina con errore, va
    oltre il tempo massimo o dà un'uscita non interpretabile, l'errore
    (``RiconoscitoreNonDisponibile``) arriva al chiamante: la verifica lo tratta
    come errore di servizio esplicito (si ritenta, poi DLQ), mai come
    riconoscimento per firma binaria (che riconoscerebbe HTML e SVG come XML
    AMMESSO). Gli errori di Siegfried SUL CONTENUTO del file (campo ``errors``,
    es. file vuoto) danno un formato non identificato, con l'avviso.
    La firma binaria resta disponibile solo se scelta esplicitamente
    (``FORMATI_RICONOSCITORE=firma_binaria``, sviluppo) e lo dichiara l'esito.

Per i PDF che si dichiarano PDF/A il controllo di conformità è un'altra
interfaccia (``pdfa.py``: veraPDF, ripiego «dichiarazione» XMP).

Il riconoscimento lavora su un FILE (percorso locale): la validazione copia il
file dallo staging in una cartella temporanea limitata (nessun file intero in
memoria).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

import structlog

from libs.shared.formati.firma_binaria import BYTE_TESTA, rileva_formato

logger = structlog.get_logger(__name__)


class RiconoscitoreNonDisponibile(Exception):
    """Strumento non installato o non eseguibile: errore tecnico, non esito."""


@dataclass(frozen=True)
class FormatoIdentificato:
    """Esito del riconoscimento di un file."""

    riconoscitore: str
    puid: str | None = None
    mime: str | None = None
    nome: str | None = None
    versione: str | None = None
    #: su cosa si basa (es. «byte match at 0, 8»)
    base: str | None = None
    avviso: str | None = None
    eseguibile: str | None = None
    note: tuple[str, ...] = field(default_factory=tuple)

    @property
    def identificato(self) -> bool:
        return bool(self.puid or self.mime)

    def come_dict(self) -> dict[str, object]:
        return {"riconoscitore": self.riconoscitore, "puid": self.puid, "mime": self.mime,
                "nome": self.nome, "versione": self.versione, "base": self.base,
                "avviso": self.avviso, "eseguibile": self.eseguibile, "note": list(self.note)}


class Riconoscitore(Protocol):
    nome: str

    async def identifica(self, percorso: Path) -> FormatoIdentificato: ...


class RiconoscitoreFirmaBinaria:
    """Ripiego: firma binaria sulla testa del file (MIME, nessun PUID)."""

    nome = "firma_binaria"

    async def identifica(self, percorso: Path) -> FormatoIdentificato:
        testa = await asyncio.to_thread(_leggi_testa, percorso)
        rilevato = rileva_formato(testa)
        return FormatoIdentificato(self.nome, mime=rilevato.mime,
                                   eseguibile=rilevato.eseguibile,
                                   base="firma binaria sui primi byte")


def _leggi_testa(percorso: Path) -> bytes:
    with percorso.open("rb") as f:
        return f.read(BYTE_TESTA)


class RiconoscitoreConRipiego:
    def __init__(self, principale: Riconoscitore, ripiego: Riconoscitore) -> None:
        self.principale = principale
        self.ripiego = ripiego
        self.nome = f"{principale.nome} (ripiego: {ripiego.nome})"

    async def identifica(self, percorso: Path) -> FormatoIdentificato:
        try:
            esito = await self.principale.identifica(percorso)
        except RiconoscitoreNonDisponibile as exc:
            # X11: nessun ripiego silenzioso: errore di servizio per il chiamante
            logger.warning("riconoscimento_formati_non_disponibile", motivo=str(exc))
            raise
        if esito.eseguibile is None:
            # gli eseguibili si respingono comunque per firma binaria (VD-C-017)
            binaria = await self.ripiego.identifica(percorso)
            if binaria.eseguibile:
                esito = replace(esito, eseguibile=binaria.eseguibile)
        return esito


__all__ = ["FormatoIdentificato", "Riconoscitore", "RiconoscitoreConRipiego",
           "RiconoscitoreFirmaBinaria", "RiconoscitoreNonDisponibile"]
