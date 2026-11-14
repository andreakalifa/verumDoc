# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-14) — protezione SSRF dei webhook a ELENCO DI AMMESSI: solo unicast
globale (IPv4 1–223.x, IPv6 2000::/3) fuori dai blocchi per uso speciale.
Prima ``fec0::/10`` (site-local deprecato, ancora instradato in alcune reti)
passava perché il controllo era un elenco di divieti (``is_global``).
"""
from __future__ import annotations

import pytest
from app.domain.webhook import UrlNonValido, indirizzo_ammesso, valida_url

RIFIUTATI = [
    "fec0::1", "feff::1",                       # site-local deprecato (REV-14)
    "fe80::1", "fc00::1", "fd12:3456::1",       # link-local, ULA
    "::1", "::", "::ffff:8.8.8.8", "::ffff:127.0.0.1",  # loopback, IPv4 incapsulato
    "64:ff9b::a9fe:a9fe", "64:ff9b:1::1",       # NAT64
    "2002:7f00:1::", "2002:0808:0808::1",       # 6to4 (anche verso indirizzi pubblici)
    "2001:0:4136:e378::1", "2001:db8::1", "2001:10::1", "2001:20::1",  # Teredo, doc, ORCHID
    "3fff::1", "100::1", "ff02::1", "4000::1", "e000::1",  # doc, discard, multicast, fuori 2000::/3
    "0.0.0.0", "10.1.2.3", "100.64.0.1", "127.0.0.1", "169.254.169.254", "172.16.5.4",  # noqa: S104
    "192.0.0.8", "192.0.2.1", "192.88.99.1", "192.168.1.1", "198.18.0.1", "198.51.100.7",
    "203.0.113.9", "224.0.0.1", "240.0.0.1", "255.255.255.255",  # noqa: S104
    "non-un-ip", "",
]
AMMESSI = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2a00:1450:4001:82a::200e",
           "2606:4700:4700::1111", "2001:4860:4860::8888"]


@pytest.mark.parametrize("ip", RIFIUTATI)
def test_indirizzo_ammesso_non_globali_rifiutati(ip: str) -> None:
    assert indirizzo_ammesso(ip) is False


@pytest.mark.parametrize("ip", AMMESSI)
def test_indirizzo_ammesso_unicast_globale(ip: str) -> None:
    assert indirizzo_ammesso(ip) is True


def test_indirizzo_ammesso_privati_solo_con_opzione_di_sviluppo() -> None:
    assert indirizzo_ammesso("127.0.0.1", consenti_privati=True)
    assert indirizzo_ammesso("fec0::1", consenti_privati=True)
    for ip in ("0.0.0.0", "::", "224.0.0.1", "::ffff:127.0.0.1", "2002:7f00:1::"):  # noqa: S104
        assert not indirizzo_ammesso(ip, consenti_privati=True), ip


def test_valida_url_ip_letterale_fec0_wh002() -> None:
    with pytest.raises(UrlNonValido) as exc:
        valida_url("https://[fec0::1]/x", lunghezza_massima=2048)
    assert exc.value.codice == "WH-002"


def test_valida_url_porte_ammesse() -> None:
    """Con ``WEBHOOK_PORTE_AMMESSE`` solo quelle porte (443 se non indicata)."""
    porte = frozenset({443})
    assert valida_url("https://hook.esempio.it/x", lunghezza_massima=2048,
                      porte_ammesse=porte).porta == 443
    with pytest.raises(UrlNonValido) as exc:
        valida_url("https://hook.esempio.it:22/x", lunghezza_massima=2048, porte_ammesse=porte)
    assert exc.value.codice == "WH-002"
    # senza elenco: qualsiasi porta (comportamento precedente)
    assert valida_url("https://hook.esempio.it:8443/x", lunghezza_massima=2048).porta == 8443
