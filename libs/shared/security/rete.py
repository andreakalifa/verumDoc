# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/rete.py — elenchi di indirizzi IP ammessi (R10, D12).

Una sola implementazione (REGOLE §9.2) per la console (onboarding: l'ente salva
l'elenco di un account SFTP) e per il server SFTP (controllo a ogni accesso).

Regole (parametri del motore, fissati dal conservatore):
  - ogni voce è un indirizzo o una rete in notazione CIDR, IPv4 o IPv6, scritta
    in forma canonica (bit dell'host a zero: ``10.0.0.1/24`` è rifiutata, si
    scrive ``10.0.0.0/24``);
  - nessuna rete più ampia del prefisso minimo (``SFTP_ALLOWLIST_PREFISSO_MINIMO_*``:
    niente ``0.0.0.0/0`` che renderebbe l'elenco inutile);
  - al più ``SFTP_ALLOWLIST_VOCI_MASSIME`` voci, senza ripetizioni.
Un indirizzo IPv4 mappato in IPv6 (``::ffff:a.b.c.d``) si confronta come IPv4.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass

Rete = ipaddress.IPv4Network | ipaddress.IPv6Network
Indirizzo = ipaddress.IPv4Address | ipaddress.IPv6Address


class ElencoNonValido(ValueError):
    """Elenco rifiutato; ``motivi`` in parole semplici, uno per voce."""

    def __init__(self, motivi: list[str]) -> None:
        super().__init__("; ".join(motivi))
        self.motivi = motivi


@dataclass(frozen=True)
class LimitiElenco:
    voci_massime: int = 20
    prefisso_minimo_ipv4: int = 16
    prefisso_minimo_ipv6: int = 48


def normalizza_elenco(voci: Iterable[str], limiti: LimitiElenco) -> list[str]:
    """Elenco canonico (ordine dato, senza ripetizioni) o ``ElencoNonValido``."""
    motivi: list[str] = []
    risultato: list[str] = []
    for grezza in voci:
        testo = str(grezza or "").strip()
        try:
            rete = ipaddress.ip_network(testo, strict=True)
        except ValueError:
            motivi.append(f"«{testo[:60]}» non è un indirizzo o una rete valida "
                          "(es. 203.0.113.10 o 203.0.113.0/24)")
            continue
        minimo = (limiti.prefisso_minimo_ipv4 if rete.version == 4
                  else limiti.prefisso_minimo_ipv6)
        if rete.prefixlen < minimo:
            motivi.append(f"«{testo}» è troppo ampia: al massimo /{minimo}")
            continue
        canonica = str(rete)
        if canonica not in risultato:
            risultato.append(canonica)
    if len(risultato) > limiti.voci_massime:
        motivi.append(f"al massimo {limiti.voci_massime} voci (ne sono state indicate "
                      f"{len(risultato)})")
    if motivi:
        raise ElencoNonValido(motivi)
    return risultato


def indirizzo(testo: str) -> Indirizzo | None:
    try:
        ip = ipaddress.ip_address(str(testo).strip().split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def ammesso(ip: str | None, elenco: Iterable[str]) -> bool:
    """True se ``ip`` sta in una rete dell'elenco. Elenco vuoto o IP illeggibile:
    False (chi chiama decide cosa vale un elenco vuoto)."""
    valore = indirizzo(ip or "")
    if valore is None:
        return False
    for voce in elenco:
        try:
            rete = ipaddress.ip_network(str(voce), strict=False)
        except ValueError:
            continue
        if valore.version == rete.version and valore in rete:
            return True
    return False


__all__ = ["ElencoNonValido", "LimitiElenco", "ammesso", "indirizzo", "normalizza_elenco"]
