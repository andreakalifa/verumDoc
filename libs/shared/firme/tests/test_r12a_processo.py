# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-147) — verifica pyHanko in un processo separato che si può terminare.

  - stesso esito della verifica in thread;
  - oltre ``tempo_max_s`` il processo si TERMINA (non resta a consumare CPU) e
    l'esito è «firme non verificate» entro il tempo previsto (misurato);
  - un processo che supera il limite di memoria dà lo stesso esito;
  - un annullamento del chiamante termina il processo.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from libs.shared.firme import LivelloVerifica
from libs.shared.firme.esito import Indicazione
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from libs.shared.firme.tests.pki_prova import Pki

PKI = Pki.nuova("Firmatario processo", cf="PRCSSO80A01H501Z")
P7M = PKI.cades(b"documento firmato")


class Lento(VerificatorePyHanko):
    """Verifica che non termina da sé (simula un PDF patologico)."""

    async def _verifica(self, *a, **k):  # type: ignore[no-untyped-def]
        while True:
            time.sleep(0.05)


class Avido(VerificatorePyHanko):
    """Verifica che chiede molta memoria."""

    async def _verifica(self, *a, **k):  # type: ignore[no-untyped-def]
        blocchi = [bytearray(256 * 1024 * 1024) for _ in range(64)]
        return blocchi  # pragma: no cover


def _vivo(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_stesso_esito_del_thread() -> None:
    in_processo = await VerificatorePyHanko([PKI.ca_asn1]).verifica(
        P7M, livello=LivelloVerifica.CRITTOGRAFICA, nome_file="a.p7m")
    in_thread = await VerificatorePyHanko([PKI.ca_asn1], processo_separato=False).verifica(
        P7M, livello=LivelloVerifica.CRITTOGRAFICA, nome_file="a.p7m")
    assert in_processo.indicazione is Indicazione.TOTAL_PASSED
    assert in_processo.come_dict() == in_thread.come_dict()


async def test_oltre_il_tempo_il_processo_si_termina() -> None:
    v = Lento([PKI.ca_asn1], tempo_max_s=1.0)
    inizio = time.monotonic()
    esito = await v.verifica(P7M, livello=LivelloVerifica.CRITTOGRAFICA, nome_file="a.p7m")
    durata = time.monotonic() - inizio
    assert esito.indicazione is not Indicazione.TOTAL_PASSED
    assert "tempo massimo" in (esito.motivo or "") and durata < 8
    assert not _vivo(v.ultimo_pid)


async def test_limite_di_memoria() -> None:
    v = Avido([PKI.ca_asn1], tempo_max_s=30, memoria_max_mb=1024)
    esito = await v.verifica(P7M, livello=LivelloVerifica.CRITTOGRAFICA, nome_file="a.p7m")
    assert "memoria" in (esito.motivo or "") or "terminato" in (esito.motivo or "")
    assert not _vivo(v.ultimo_pid)


async def test_annullamento_del_chiamante_termina_il_processo() -> None:
    v = Lento([PKI.ca_asn1], tempo_max_s=60)
    compito = asyncio.create_task(v.verifica(P7M, livello=LivelloVerifica.CRITTOGRAFICA))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if v.ultimo_pid:
            break
    assert _vivo(v.ultimo_pid)
    compito.cancel()
    with pytest.raises(asyncio.CancelledError):
        await compito
    for _ in range(100):
        if not _vivo(v.ultimo_pid):
            break
        await asyncio.sleep(0.05)
    assert not _vivo(v.ultimo_pid)
