# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R10 — PROXY protocol (D12), elenchi di indirizzi (D12) e permessi dell'identità (D30).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct

import pytest

from libs.shared.security.permessi import ha_permesso, ha_permesso_non_delegabile, permessi_identita
from libs.shared.security.proxy_protocol import (
    FIRMA_V2,
    ProxyNonValido,
    analizza,
    leggi_intestazione,
)
from libs.shared.security.rete import ElencoNonValido, LimitiElenco, ammesso, normalizza_elenco


def test_v1_tcp4_tcp6_unknown() -> None:
    i = analizza(b"PROXY TCP4 203.0.113.7 10.0.0.1 51000 2222\r\nSSH-2.0-x")
    assert (i.sorgente, i.porta, i.lunghezza) == ("203.0.113.7", 51000, 44)
    assert analizza(b"PROXY TCP6 2001:db8::1 ::1 1 2\r\n").sorgente == "2001:db8::1"
    assert analizza(b"PROXY UNKNOWN\r\n").sorgente is None
    assert analizza(b"PROXY TCP4 203.0") is None  # servono altri byte


@pytest.mark.parametrize("dati", [
    b"SSH-2.0-OpenSSH_9.6\r\n",
    b"PROXY TCP4 203.0.113.7 10.0.0.1 51000\r\n",
    b"PROXY TCP4 2001:db8::1 10.0.0.1 1 2\r\n",
    b"PROXY TCP4 999.0.0.1 10.0.0.1 1 2\r\n",
    b"PROXY TCP4 1.2.3.4 10.0.0.1 99999 2\r\n",
    b"PROXY " + b"x" * 120,
    FIRMA_V2[:11] + b"X" + b"\x21\x11\x00\x0c",
    FIRMA_V2 + b"\x11\x11\x00\x0c" + bytes(12),   # versione 1 nel campo della v2
    FIRMA_V2 + b"\x21\x11\x10\x00",               # lunghezza oltre il massimo
    FIRMA_V2 + b"\x21\x11\x00\x04" + bytes(4),    # indirizzi troppo corti
])
def test_intestazioni_malformate(dati: bytes) -> None:
    with pytest.raises(ProxyNonValido):
        analizza(dati)


def test_v2_proxy_e_local() -> None:
    corpo = (ipaddress.IPv4Address("198.51.100.9").packed + bytes(4)
             + struct.pack(">HH", 40000, 22))
    i = analizza(FIRMA_V2 + b"\x21\x11" + struct.pack(">H", len(corpo)) + corpo + b"SSH")
    assert (i.sorgente, i.porta, i.lunghezza) == ("198.51.100.9", 40000, 28)
    assert analizza(FIRMA_V2 + b"\x20\x00\x00\x00").sorgente is None
    assert analizza(FIRMA_V2 + b"\x21\x11\x00\x0c" + bytes(5)) is None


async def test_lettura_dal_socket_non_consuma_i_byte_ssh() -> None:
    a, b = socket.socketpair()
    a.setblocking(False)
    b.sendall(b"PROXY TCP4 203.0.113.7 10.0.0.1 51000 2222\r\nSSH-2.0-client\r\n")
    i = await asyncio.wait_for(leggi_intestazione(a), 1)
    assert i.sorgente == "203.0.113.7"
    assert a.recv(100) == b"SSH-2.0-client\r\n"
    a.close()
    b.close()


async def test_lettura_senza_intestazione_scade() -> None:
    a, b = socket.socketpair()
    a.setblocking(False)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(leggi_intestazione(a), 0.1)
    a.close()
    b.close()


def test_elenco_canonico_e_limiti() -> None:
    limiti = LimitiElenco(voci_massime=3, prefisso_minimo_ipv4=16, prefisso_minimo_ipv6=48)
    assert normalizza_elenco(["203.0.113.0/24", "198.51.100.7", "203.0.113.0/24"], limiti) == \
        ["203.0.113.0/24", "198.51.100.7/32"]
    for errato in (["0.0.0.0/0"], ["10.0.0.0/8"], ["10.0.0.5/24"], ["ciao"], ["2001:db8::/32"],
                   ["1.1.1.1", "1.1.1.2", "1.1.1.3", "1.1.1.4"]):
        with pytest.raises(ElencoNonValido):
            normalizza_elenco(errato, limiti)


def test_ammesso() -> None:
    elenco = ["203.0.113.0/24", "2001:db8::/48"]
    assert ammesso("203.0.113.200", elenco)
    assert ammesso("::ffff:203.0.113.5", elenco)       # IPv4 mappato
    assert ammesso("2001:db8::abcd", elenco)
    assert not ammesso("203.0.114.1", elenco)
    assert not ammesso("non-ip", elenco)
    assert not ammesso("203.0.113.1", [])


def test_permessi_identita_e_non_delegabili() -> None:
    fisso = {"ruolo": "ADMIN_TENANT"}
    assert ha_permesso(fisso, "annullamento:approva")
    assert ha_permesso_non_delegabile(fisso, "utenti:gestisci")
    personalizzato = {"ruolo": "OPERATORE", "rp": "x",
                      "permessi": ["versamento:esegui", "annullamento:approva", "scarto:esegui"]}
    # i permessi mai delegabili nel token di un ruolo personalizzato non valgono
    assert permessi_identita(personalizzato) == frozenset({"versamento:esegui"})
    assert not ha_permesso(personalizzato, "annullamento:approva")
    assert not ha_permesso(personalizzato, "scarto:esegui")
    assert not ha_permesso({"ruolo": "OPERATORE", "rp": "x"}, "archivio:consulta")
    assert not ha_permesso({"ruolo": "SCONOSCIUTO"}, "archivio:consulta")
    assert ha_permesso({"ruolo": "VIEWER"}, "archivio:consulta")
    assert not ha_permesso({"ruolo": "VIEWER"}, "versamento:esegui")
