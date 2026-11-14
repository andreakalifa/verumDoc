# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/proxy_protocol.py — PROXY protocol v1 e v2 (R10, D12).

Riferimenti: decisione D12 (VD-C-107); specifica HAProxy «The PROXY protocol»
(versioni 1 e 2). Il bilanciatore TCP davanti al server SFTP antepone alla
connessione un'intestazione con l'indirizzo del cliente; senza, il server vede
solo l'indirizzo del bilanciatore.

Regola di sicurezza: l'intestazione si legge SOLO se la connessione arriva da
una sorgente fidata configurata (``SFTP_PROXY_SORGENTI_FIDATE``, reti dei
bilanciatori): un cliente che si collega direttamente e scrive un'intestazione
PROXY falsa non cambia il proprio indirizzo (per lui i byte sono SSH e la
connessione fallisce). Da una sorgente fidata l'intestazione è OBBLIGATORIA:
senza, o malformata, la connessione si chiude.

Lettura senza consumare i byte SSH: si legge in anteprima (``MSG_PEEK``) fino a
riconoscere l'intestazione intera, poi si consumano ESATTAMENTE i suoi byte.
Limiti: v1 al più 107 byte, v2 al più 16 + ``LUNGHEZZA_V2_MASSIMA`` byte; tempo
di attesa dell'intestazione limitato (chi chiama usa ``asyncio.wait_for``).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
from dataclasses import dataclass

FIRMA_V2 = b"\r\n\r\n\x00\r\nQUIT\n"
LUNGHEZZA_V1_MASSIMA = 107
LUNGHEZZA_V2_MASSIMA = 512


class ProxyNonValido(ValueError):
    """Intestazione PROXY assente o malformata."""


@dataclass(frozen=True)
class Intestazione:
    """Intestazione riconosciuta. ``sorgente`` None = connessione del bilanciatore
    stesso (v2 LOCAL, v1 UNKNOWN: controlli di salute), non di un cliente."""

    lunghezza: int
    sorgente: str | None
    porta: int | None
    versione: int


def analizza(dati: bytes) -> Intestazione | None:
    """Intestazione all'inizio di ``dati``; None se servono altri byte;
    ``ProxyNonValido`` se i byte non sono un'intestazione PROXY."""
    if not dati:
        return None
    if dati[:1] == b"\r":
        return _v2(dati)
    if dati[:1] == b"P":
        return _v1(dati)
    raise ProxyNonValido("intestazione PROXY assente")


def _v1(dati: bytes) -> Intestazione | None:
    prefisso = b"PROXY "
    if not prefisso.startswith(dati[:6]) and not dati.startswith(prefisso):
        raise ProxyNonValido("intestazione PROXY v1 malformata")
    fine = dati.find(b"\r\n", 0, LUNGHEZZA_V1_MASSIMA)
    if fine < 0:
        if len(dati) >= LUNGHEZZA_V1_MASSIMA:
            raise ProxyNonValido("intestazione PROXY v1 troppo lunga")
        return None
    try:
        parti = dati[:fine].decode("ascii").split(" ")
    except UnicodeDecodeError:
        raise ProxyNonValido("intestazione PROXY v1 non ASCII") from None
    if parti[0] != "PROXY" or len(parti) < 2:
        raise ProxyNonValido("intestazione PROXY v1 malformata")
    if parti[1] == "UNKNOWN":
        return Intestazione(fine + 2, None, None, 1)
    if parti[1] not in ("TCP4", "TCP6") or len(parti) != 6:
        raise ProxyNonValido("intestazione PROXY v1 malformata")
    try:
        sorgente = ipaddress.ip_address(parti[2])
        ipaddress.ip_address(parti[3])
        porta = int(parti[4])
        int(parti[5])
    except ValueError:
        raise ProxyNonValido("indirizzi della PROXY v1 non validi") from None
    if (sorgente.version == 4) != (parti[1] == "TCP4") or not 0 <= porta <= 65535:
        raise ProxyNonValido("famiglia o porta della PROXY v1 non coerenti")
    return Intestazione(fine + 2, str(sorgente), porta, 1)


def _v2(dati: bytes) -> Intestazione | None:
    if len(dati) < 16:
        if not FIRMA_V2.startswith(dati[:12]):
            raise ProxyNonValido("intestazione PROXY v2 malformata")
        return None
    if dati[:12] != FIRMA_V2:
        raise ProxyNonValido("firma PROXY v2 errata")
    versione_comando, famiglia = dati[12], dati[13]
    (lunghezza,) = struct.unpack(">H", dati[14:16])
    if versione_comando >> 4 != 2:
        raise ProxyNonValido("versione PROXY v2 non supportata")
    if lunghezza > LUNGHEZZA_V2_MASSIMA:
        raise ProxyNonValido("intestazione PROXY v2 troppo lunga")
    totale = 16 + lunghezza
    if len(dati) < totale:
        return None
    comando = versione_comando & 0x0F
    if comando == 0x0:  # LOCAL: il bilanciatore stesso
        return Intestazione(totale, None, None, 2)
    if comando != 0x1:
        raise ProxyNonValido("comando PROXY v2 sconosciuto")
    indirizzi = dati[16:totale]
    if famiglia == 0x11 and lunghezza >= 12:      # TCP su IPv4
        sorgente = str(ipaddress.IPv4Address(indirizzi[0:4]))
        (porta,) = struct.unpack(">H", indirizzi[8:10])
    elif famiglia == 0x21 and lunghezza >= 36:    # TCP su IPv6
        sorgente = str(ipaddress.IPv6Address(indirizzi[0:16]))
        (porta,) = struct.unpack(">H", indirizzi[32:34])
    elif famiglia == 0x00:                        # UNSPEC: indirizzo non noto
        return Intestazione(totale, None, None, 2)
    else:
        raise ProxyNonValido("famiglia PROXY v2 non supportata o indirizzi troppo corti")
    return Intestazione(totale, sorgente, porta, 2)


async def leggi_intestazione(conn: socket.socket, *, pausa: float = 0.01) -> Intestazione:
    """Legge l'intestazione dalla connessione (non bloccante) senza consumare i
    byte che seguono. Chi chiama limita il tempo con ``asyncio.wait_for``."""
    massimo = 16 + LUNGHEZZA_V2_MASSIMA
    while True:
        try:
            anteprima = conn.recv(massimo, socket.MSG_PEEK)
        except BlockingIOError:
            await asyncio.sleep(pausa)
            continue
        if not anteprima:
            raise ProxyNonValido("connessione chiusa prima dell'intestazione PROXY")
        intestazione = analizza(anteprima)
        if intestazione is not None:
            letti = 0
            while letti < intestazione.lunghezza:
                try:
                    blocco = conn.recv(intestazione.lunghezza - letti)
                except BlockingIOError:
                    await asyncio.sleep(pausa)
                    continue
                if not blocco:
                    raise ProxyNonValido("connessione chiusa durante l'intestazione PROXY")
                letti += len(blocco)
            return intestazione
        await asyncio.sleep(pausa)


__all__ = ["FIRMA_V2", "Intestazione", "ProxyNonValido", "analizza", "leggi_intestazione"]
