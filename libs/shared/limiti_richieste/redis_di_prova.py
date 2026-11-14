# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Redis vero per i test (R03 f2): avvia ``redis-server`` su una porta libera,
senza persistenza, e lo ferma alla fine. Usato dai test del limitatore e dei
servizi che lo applicano (distribution, dashboard-api, notification).
Solo per i test: non importato dal codice dei servizi.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager


def redis_disponibile() -> bool:
    return shutil.which("redis-server") is not None


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def avvia_redis() -> Iterator[str]:
    """URL di un Redis appena avviato (``redis://127.0.0.1:<porta>/0``)."""
    eseguibile = shutil.which("redis-server")
    if eseguibile is None:
        raise RuntimeError("redis-server non installato")
    porta = _porta_libera()
    processo = subprocess.Popen(  # noqa: S603 — eseguibile trovato nel PATH, argomenti costanti
        [eseguibile, "--port", str(porta), "--bind", "127.0.0.1", "--save", "",
         "--appendonly", "no"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", porta), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("redis-server non risponde")
        yield f"redis://127.0.0.1:{porta}/0"
    finally:
        processo.terminate()
        processo.wait(timeout=10)


def porta_chiusa() -> str:
    """URL di un Redis che non risponde (porta libera, nessun processo)."""
    return f"redis://127.0.0.1:{_porta_libera()}/0"
