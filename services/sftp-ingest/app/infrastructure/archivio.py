# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Area su disco degli account SFTP (volume condiviso fra server e lavoratori).

Servizio: sftp-ingest. Funzioni sincrone e brevi (stat, rename, scrittura di
un piccolo JSON): il lavoratore le chiama con ``asyncio.to_thread``. Nessun
file viene letto per intero in memoria qui; i file dei pacchetti si leggono a
blocchi nel client del gateway.

Atomicità: lo spostamento di una cartella è un ``os.rename`` sullo stesso
filesystem (atomico); ``esito.json`` si scrive in un file temporaneo e si
sostituisce con ``os.replace``.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from app.domain.protocollo import (
    NOME_ESITO,
    SOTTOCARTELLE,
    TipoVoce,
    VoceCartella,
)

logger = structlog.get_logger(__name__)


def area_account(radice: str | Path, account_id: str) -> Path:
    """Cartella dell'account sul volume (nome = id dell'account, mai il nome utente)."""
    return Path(radice) / str(account_id)


def assicura_area(area: Path) -> None:
    """Crea l'area e le sottocartelle standard se mancano (idempotente)."""
    area.mkdir(mode=0o750, parents=True, exist_ok=True)
    for nome in SOTTOCARTELLE:
        (area / nome).mkdir(mode=0o750, exist_ok=True)


def _tipo(modo: int) -> TipoVoce:
    if stat.S_ISREG(modo):
        return TipoVoce.FILE
    if stat.S_ISDIR(modo):
        return TipoVoce.CARTELLA
    return TipoVoce.ALTRO


def voci(cartella: Path) -> list[VoceCartella]:
    """Elementi di una cartella con lstat (i collegamenti non si seguono)."""
    risultato: list[VoceCartella] = []
    with os.scandir(cartella) as it:
        for e in it:
            st = e.stat(follow_symlinks=False)
            risultato.append(VoceCartella(e.name, _tipo(st.st_mode), st.st_size, st.st_mtime))
    return risultato


def sottocartelle(cartella: Path, limite: int) -> list[tuple[str, float]]:
    """Fino a ``limite`` sottocartelle (nome, mtime), le meno recenti prima."""
    if not cartella.is_dir():
        return []
    elenco: list[tuple[str, float]] = []
    with os.scandir(cartella) as it:
        for e in it:
            if e.is_dir(follow_symlinks=False):
                elenco.append((e.name, e.stat(follow_symlinks=False).st_mtime))
    elenco.sort(key=lambda x: (x[1], x[0]))
    return elenco[:limite]


def mtime(percorso: Path) -> float:
    return percorso.lstat().st_mtime


def sposta(sorgente: Path, destinazione: Path) -> None:
    """Rinomina atomica; mai sopra una destinazione esistente (FileExistsError)."""
    if destinazione.exists() or destinazione.is_symlink():
        raise FileExistsError(str(destinazione))
    os.rename(sorgente, destinazione)


def destinazione_libera(base: Path, nome: str, adesso: datetime) -> Path:
    """``base/nome``, o ``base/nome__<istante>`` se già occupata (reinvii)."""
    candidata = base / nome
    if not candidata.exists():
        return candidata
    marca = adesso.strftime("%Y%m%dT%H%M%S%fZ")
    return base / f"{nome}__{marca}"


def scrivi_esito(area: Path, cartella: str, contenuto: dict[str, Any]) -> Path:
    """Scrive ``out/<cartella>/esito.json`` in modo atomico."""
    destinazione = area / "out" / cartella
    destinazione.mkdir(mode=0o750, exist_ok=True)
    finale = destinazione / NOME_ESITO
    temporaneo = destinazione / f".tmp-{NOME_ESITO}"
    dati = json.dumps(contenuto, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")
    with open(temporaneo, "wb") as f:
        f.write(dati)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporaneo, finale)
    return finale


def leggi_limitato(percorso: Path, massimo: int) -> bytes | None:
    """Contenuto di un file piccolo (indice), ``None`` se supera ``massimo`` byte."""
    with open(percorso, "rb") as f:
        dati = f.read(massimo + 1)
    return None if len(dati) > massimo else dati


def uso_disco(area: Path) -> int:
    """Byte occupati dai file dell'area (lstat, collegamenti non seguiti)."""
    totale = 0
    pila = [area]
    while pila:
        corrente = pila.pop()
        try:
            with os.scandir(corrente) as it:
                for e in it:
                    st = e.stat(follow_symlinks=False)
                    if stat.S_ISDIR(st.st_mode):
                        pila.append(Path(e.path))
                    else:
                        totale += st.st_size
        except FileNotFoundError:
            continue
    return totale


def _vecchie(cartella: Path, limite_epoch: float) -> Iterator[Path]:
    if not cartella.is_dir():
        return
    with os.scandir(cartella) as it:
        for e in it:
            if e.stat(follow_symlinks=False).st_mtime < limite_epoch:
                yield Path(e.path)


def pulisci(cartella: Path, limite_epoch: float) -> int:
    """Elimina gli elementi di ``cartella`` non modificati da prima di ``limite_epoch``.

    Usata solo su fatti/, scarti/ e out/: copie di transito, il pacchetto versato
    è nello storage del conservatore."""
    eliminati = 0
    for percorso in list(_vecchie(cartella, limite_epoch)):
        try:
            if percorso.is_dir() and not percorso.is_symlink():
                shutil.rmtree(percorso)
            else:
                percorso.unlink()
            eliminati += 1
        except OSError as exc:
            logger.warning("sftp_pulizia_non_riuscita", percorso=str(percorso),
                           errore=type(exc).__name__)
    return eliminati
