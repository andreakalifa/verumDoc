# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/formati/siegfried.py — identificazione PRONOM con Siegfried (R08, D02).

Siegfried (Apache-2.0) identifica il formato con le firme DROID/PRONOM dei
National Archives britannici (file ``default.sig``). Si esegue come processo:

    sf -json -home <SIEGFRIED_HOME> -sig <SIEGFRIED_FIRME> <file>

Il binario e il file di firme li installa l'immagine della validazione (release
GitHub con SHA-256 verificato, ``services/validation/Dockerfile``). Binario o
firme mancanti, uscita non interpretabile, tempo scaduto (il processo si
termina): ``RiconoscitoreNonDisponibile``, errore di servizio (revisione X11:
nessun ripiego per firma binaria). Gli errori sul contenuto riportati da
Siegfried nel campo ``errors`` danno un formato NON identificato con l'avviso.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from libs.shared.formati.riconoscimento import FormatoIdentificato, RiconoscitoreNonDisponibile


class RiconoscitoreSiegfried:
    def __init__(self, binario: str = "sf", *, home: str | None = None,
                 firme: str = "default.sig", timeout_s: float = 120.0) -> None:
        self.binario = binario
        self.home = home
        self.firme = firme
        self.timeout_s = timeout_s
        self.nome = "siegfried"

    def _comando(self, percorso: Path) -> list[str]:
        eseguibile = shutil.which(self.binario) or self.binario
        comando = [eseguibile, "-json", "-sig", self.firme]
        if self.home:
            comando[1:1] = ["-home", self.home]
        return [*comando, str(percorso)]

    async def identifica(self, percorso: Path) -> FormatoIdentificato:
        try:
            processo = await asyncio.create_subprocess_exec(
                *self._comando(percorso), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise RiconoscitoreNonDisponibile(f"siegfried non eseguibile: {exc}") from exc
        try:
            uscita, errori = await asyncio.wait_for(processo.communicate(), self.timeout_s)
        except TimeoutError as exc:
            processo.kill()
            await processo.wait()
            raise RiconoscitoreNonDisponibile("siegfried: tempo scaduto") from exc
        if processo.returncode != 0:
            raise RiconoscitoreNonDisponibile(
                f"siegfried terminato con {processo.returncode}: {errori[:200]!r}")
        return interpreta(uscita)


def interpreta(uscita: bytes) -> FormatoIdentificato:
    """Esito dall'uscita JSON di ``sf -json`` (una riga per file)."""
    try:
        dati: dict[str, Any] = json.loads(uscita)
        file = dati["files"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RiconoscitoreNonDisponibile("uscita di siegfried non interpretabile") from exc
    dettagli = ", ".join(str(i.get("details") or "") for i in dati.get("identifiers") or ())
    nome = f"siegfried {dati.get('siegfried', '?')} ({dettagli})" if dettagli else "siegfried"
    if file.get("errors"):
        # X11: errore sul contenuto (file vuoto, illeggibile): non identificato
        return FormatoIdentificato(nome, avviso=f"siegfried: {str(file['errors'])[:200]}")
    pronom = [m for m in file.get("matches") or () if m.get("ns") == "pronom"]
    if not pronom or pronom[0].get("id") in (None, "", "UNKNOWN"):
        avviso = pronom[0].get("warning") if pronom else None
        return FormatoIdentificato(nome, avviso=avviso or "formato non identificato")
    m = pronom[0]
    base = str(m.get("basis") or "")
    if base and all(p.strip().startswith("extension match") for p in base.split(";")):
        # REGOLE §8.15: il formato si riconosce dal contenuto, mai dall'estensione
        return FormatoIdentificato(nome, nome=m.get("format") or None, base=base,
                                   avviso="riconosciuto solo dall'estensione")
    return FormatoIdentificato(nome, puid=str(m["id"]), mime=m.get("mime") or None,
                               nome=m.get("format") or None, versione=m.get("version") or None,
                               base=m.get("basis") or None, avviso=m.get("warning") or None)


__all__ = ["RiconoscitoreSiegfried", "interpreta"]
