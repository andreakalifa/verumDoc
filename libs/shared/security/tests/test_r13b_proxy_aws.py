# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R13b (VD-C-154) — intestazione PROXY v2 come la manda un NLB AWS con TLV (tipo 0xEA,
sottotipo 0x01 = identificativo dell'endpoint VPC di PrivateLink, e 0x03 CRC32C): il
parser consuma ESATTAMENTE l'intestazione con i TLV e lascia intatti i byte del
protocollo che seguono (banner SSH). Riferimento pubblico: documentazione AWS «Network
Load Balancers — Proxy protocol» e specifica HAProxy PROXY v2 (§2.2, TLV). La prova con
l'NLB reale resta in giorno1_staging.md."""
from __future__ import annotations

import ipaddress
import struct

from libs.shared.security.proxy_protocol import FIRMA_V2, analizza


def _tlv(tipo: int, valore: bytes) -> bytes:
    return bytes([tipo]) + struct.pack(">H", len(valore)) + valore


def _aws(sorgente: str, porta: int) -> bytes:
    indirizzi = (ipaddress.IPv4Address(sorgente).packed + ipaddress.IPv4Address("10.0.1.20").packed
                 + struct.pack(">HH", porta, 22))
    tlv = _tlv(0xEA, b"\x01vpce-0123456789abcdef0") + _tlv(0x04, b"")  # PP2_TYPE_NOOP
    corpo = indirizzi + tlv
    return FIRMA_V2 + bytes([0x21, 0x11]) + struct.pack(">H", len(corpo)) + corpo


def test_tlv_aws_consumati_e_sorgente_reale() -> None:
    testa = _aws("203.0.113.7", 50123)
    dati = testa + b"SSH-2.0-OpenSSH_9.6\r\n"
    esito = analizza(dati)
    assert esito is not None and esito.versione == 2
    assert (esito.sorgente, esito.porta) == ("203.0.113.7", 50123)
    assert esito.lunghezza == len(testa)
    assert dati[esito.lunghezza:].startswith(b"SSH-2.0-")


def test_tlv_aws_incompleti_chiedono_altri_byte() -> None:
    testa = _aws("198.51.100.9", 40000)
    assert analizza(testa[:-3]) is None
