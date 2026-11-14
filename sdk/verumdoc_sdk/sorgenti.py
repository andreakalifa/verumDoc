# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Sorgenti dei file da versare: percorso su disco, byte in memoria o flusso.

L'SDK non legge mai un file intero in memoria: i percorsi e i flussi si leggono
a blocchi. Un flusso non riavvolgibile si legge una sola volta, in ordine; per
questo deve dichiarare la propria dimensione (la sessione di caricamento la
chiede all'apertura) e non si può inviare in multipart con ritentativi.

Esempi::

    Sorgente.da_percorso("fattura.pdf")
    Sorgente.da_bytes(b"%PDF-...", nome="fattura.pdf")
    Sorgente.da_flusso(risposta.iter_bytes(), dimensione=123_456_789, nome="video.mp4")
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, cast

#: Blocco di lettura dal disco o dal flusso (non è la dimensione delle parti).
BLOCCO_LETTURA = 1024 * 1024


@dataclass
class Sorgente:
    """Un file da inviare. Usare i costruttori ``da_percorso``, ``da_bytes``, ``da_flusso``."""

    nome: str | None = None
    dimensione: int | None = None
    mime: str | None = None
    _percorso: Path | None = field(default=None, repr=False)
    _dati: bytes | None = field(default=None, repr=False)
    _flusso: Any = field(default=None, repr=False)
    _posizione_iniziale: int | None = field(default=None, repr=False)
    _usato: bool = field(default=False, repr=False)

    # ── Costruttori ───────────────────────────────────────────────────
    @classmethod
    def da_percorso(cls, percorso: str | os.PathLike[str], *, nome: str | None = None,
                    mime: str | None = None) -> Sorgente:
        p = Path(percorso)
        return cls(nome=nome or p.name, dimensione=p.stat().st_size, mime=mime, _percorso=p)

    @classmethod
    def da_bytes(cls, dati: bytes, *, nome: str | None = None,
                 mime: str | None = None) -> Sorgente:
        return cls(nome=nome, dimensione=len(dati), mime=mime, _dati=bytes(dati))

    @classmethod
    def da_flusso(cls, flusso: Any, *, dimensione: int | None = None, nome: str | None = None,
                  mime: str | None = None) -> Sorgente:
        """Oggetto con ``read(n)`` (sincrono o ``async``), iterabile di byte o iterabile
        asincrono di byte. Un file aperto e riavvolgibile si può ritentare e la
        dimensione si ricava da sola; per gli altri flussi ``dimensione`` serve
        alle sessioni di caricamento."""
        posizione: int | None = None
        if _riavvolgibile(flusso):
            posizione = flusso.tell()
            if dimensione is None:
                fine = flusso.seek(0, io.SEEK_END)
                flusso.seek(posizione)
                dimensione = fine - posizione
        return cls(nome=nome, dimensione=dimensione, mime=mime, _flusso=flusso,
                   _posizione_iniziale=posizione)

    # ── Proprietà ─────────────────────────────────────────────────────
    @property
    def ripetibile(self) -> bool:
        """Si può rileggere da capo (per un nuovo tentativo o per l'impronta)."""
        return (self._percorso is not None or self._dati is not None
                or self._posizione_iniziale is not None)

    @property
    def asincrona(self) -> bool:
        """Il flusso si legge solo con ``await`` (client asincrono)."""
        f = self._flusso
        if f is None:
            return False
        if isinstance(f, (AsyncIterable, AsyncIterator)):
            return True
        leggi = getattr(f, "read", None)
        return leggi is not None and asyncio.iscoroutinefunction(leggi)

    # ── Lettura sincrona ──────────────────────────────────────────────
    def apri(self) -> BinaryIO:
        """Oggetto file in lettura dall'inizio della sorgente (per il multipart)."""
        if self._percorso is not None:
            return self._percorso.open("rb")
        if self._dati is not None:
            return io.BytesIO(self._dati)
        if self._posizione_iniziale is not None:
            self._flusso.seek(self._posizione_iniziale)
            return cast(BinaryIO, _NonChiudere(self._flusso))
        self._segna_uso()
        return cast(BinaryIO, _LettoreDaIteratore(_blocchi_sincroni(self._flusso)))

    def blocchi(self) -> Iterator[bytes]:
        """Blocchi della sorgente dall'inizio (lettura in ordine, una volta per i flussi)."""
        f = self.apri()
        try:
            while True:
                b = f.read(BLOCCO_LETTURA)
                if not b:
                    return
                yield b
        finally:
            f.close()

    def sha256(self) -> str:
        """Impronta SHA-256 (esadecimale) calcolata a blocchi; solo sorgenti ripetibili."""
        if not self.ripetibile:
            raise ValueError("Impronta non calcolabile in anticipo: il flusso si legge una "
                             "volta sola (indicare hash nell'indice o usare le sessioni)")
        h = hashlib.sha256()
        for b in self.blocchi():
            h.update(b)
        return h.hexdigest()

    # ── Lettura asincrona ─────────────────────────────────────────────
    async def blocchi_async(self) -> AsyncIterator[bytes]:
        """Blocchi della sorgente; i file su disco si leggono in un thread."""
        f = self._flusso
        if self._percorso is not None or self._dati is not None or not self.asincrona:
            lettore = await asyncio.to_thread(self.apri)
            try:
                while True:
                    b = await asyncio.to_thread(lettore.read, BLOCCO_LETTURA)
                    if not b:
                        return
                    yield b
            finally:
                await asyncio.to_thread(lettore.close)
            return
        self._segna_uso()
        if isinstance(f, (AsyncIterable, AsyncIterator)):
            async for b in f:
                if b:
                    yield bytes(b)
            return
        while True:
            b = await f.read(BLOCCO_LETTURA)
            if not b:
                return
            yield bytes(b)

    def _segna_uso(self) -> None:
        if self._usato:
            raise ValueError("Flusso già letto: non si può rileggere (usare un percorso o "
                             "un file riavvolgibile per ritentare)")
        self._usato = True


def come_sorgente(valore: Any, nome: str | None = None) -> Sorgente:
    """Converte percorso (``str``/``Path``), ``bytes`` o ``Sorgente`` in ``Sorgente``;
    un oggetto file o un iterabile diventano ``Sorgente.da_flusso``."""
    if isinstance(valore, Sorgente):
        if nome and not valore.nome:
            valore.nome = nome
        return valore
    if isinstance(valore, (str, os.PathLike)):
        return Sorgente.da_percorso(valore, nome=nome)
    if isinstance(valore, (bytes, bytearray, memoryview)):
        return Sorgente.da_bytes(bytes(valore), nome=nome)
    return Sorgente.da_flusso(valore, nome=nome)


def _riavvolgibile(f: Any) -> bool:
    try:
        return bool(f.seekable()) and callable(getattr(f, "tell", None))
    except (AttributeError, OSError, ValueError):
        return False


def _blocchi_sincroni(f: Any) -> Iterator[bytes]:
    leggi: Callable[[int], bytes] | None = getattr(f, "read", None)
    if leggi is not None:
        while True:
            b = leggi(BLOCCO_LETTURA)
            if not b:
                return
            yield bytes(b)
    elif isinstance(f, Iterable):
        for b in f:
            if b:
                yield bytes(b)
    else:
        raise TypeError("Sorgente non leggibile: serve read(n) o un iterabile di byte")


class _LettoreDaIteratore(io.RawIOBase):
    """Oggetto file in sola lettura sopra un iteratore di blocchi."""

    def __init__(self, blocchi: Iterator[bytes]) -> None:
        self._blocchi = blocchi
        self._resto = b""

    def readable(self) -> bool:
        return True

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            return self._resto + b"".join(self._blocchi)
        while len(self._resto) < n:
            try:
                self._resto += next(self._blocchi)
            except StopIteration:
                break
        dati, self._resto = self._resto[:n], self._resto[n:]
        return dati


class _NonChiudere(io.RawIOBase):
    """Involucro che non chiude il file del chiamante (resta suo)."""

    def __init__(self, f: Any) -> None:
        self._f = f

    def readable(self) -> bool:
        return True

    def read(self, n: int | None = -1) -> bytes:
        return bytes(self._f.read(n))

    def close(self) -> None:  # il file appartiene al chiamante
        return None


class LettoreParti:
    """Legge una sorgente in ordine e la divide in parti di dimensione data,
    calcolando lo SHA-256 dell'intero file al passaggio (una sola lettura)."""

    def __init__(self, dimensione_parte: int) -> None:
        self.dimensione_parte = dimensione_parte
        self.sha256 = hashlib.sha256()
        self.letti = 0
        self._buffer = bytearray()

    def aggiungi(self, blocco: bytes) -> list[bytes]:
        """Aggiunge un blocco; restituisce le parti complete pronte."""
        self.sha256.update(blocco)
        self.letti += len(blocco)
        self._buffer.extend(blocco)
        pronte: list[bytes] = []
        while len(self._buffer) >= self.dimensione_parte:
            pronte.append(bytes(self._buffer[: self.dimensione_parte]))
            del self._buffer[: self.dimensione_parte]
        return pronte

    def fine(self) -> bytes | None:
        """Ultima parte (il resto), se c'è."""
        if not self._buffer:
            return None
        resto = bytes(self._buffer)
        self._buffer.clear()
        return resto
