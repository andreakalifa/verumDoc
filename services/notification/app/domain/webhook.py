# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/domain/webhook.py
Regole pure dei webhook firmati — contratto C3.

Dominio puro (REGOLE §7.2): niente rete, niente orologio (il tempo entra come
argomento).

- ``valida_url``: solo URL https assoluti, senza credenziali né frammento;
  se l'host è un indirizzo IP letterale, deve essere pubblico.
- ``indirizzo_ammesso``: protezione SSRF a ELENCO DI AMMESSI (R03 f2,
  REV-14): un indirizzo è ammesso solo se sta negli intervalli unicast globali
  (IPv4 1.0.0.0–223.255.255.255, IPv6 2000::/3) e fuori da tutti i blocchi
  per uso speciale del registro IANA che non sono raggiungibili da Internet.
  Tutto il resto è rifiutato, anche ciò che nessun elenco di divieti nomina
  (es. ``fec0::/10``, IPv4 incapsulato, NAT64, 6to4, Teredo, ULA, ORCHID).
- ``firma``: ``VerumDoc-Firma: t=<unix>,v1=<hex HMAC-SHA256(segreto, t + "." + corpo)>``.
  Il segreto si usa come byte UTF-8 della stringa mostrata al cliente.
- ``esito_tentativo``: dopo un tentativo fallito, prossimo tentativo o FALLITA
  secondo la lista costante dei ritardi (REGOLE §12.4).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

PREFISSO_SEGRETO = "whsec_"
INTESTAZIONE_FIRMA = "VerumDoc-Firma"
INTESTAZIONE_EVENTO = "VerumDoc-Evento"
INTESTAZIONE_ID_CONSEGNA = "VerumDoc-Id-Consegna"
INTESTAZIONE_ID_EVENTO = "VerumDoc-Id-Evento"
VERSIONE_FIRMA = "v1"

# Stati delle consegne (stessi valori del vincolo ck_webhook_consegne_stato)
IN_CODA = "IN_CODA"
IN_CONSEGNA = "IN_CONSEGNA"
CONSEGNATA = "CONSEGNATA"
FALLITA = "FALLITA"
ANNULLATA = "ANNULLATA"
STATI_CONSEGNA = (IN_CODA, IN_CONSEGNA, CONSEGNATA, FALLITA, ANNULLATA)


class UrlNonValido(ValueError):
    """URL non utilizzabile come webhook. ``codice`` è WH-001 (forma) o WH-002 (indirizzo)."""

    def __init__(self, codice: str, messaggio: str) -> None:
        super().__init__(messaggio)
        self.codice = codice
        self.messaggio = messaggio


# ── Indirizzi e SSRF ────────────────────────────────────────────────

def _reti(*testi: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(t) for t in testi)


#: Intervalli AMMESSI (unicast globale). Ciò che è fuori non si contatta mai.
AMMESSI_IPV4 = _reti("1.0.0.0/8", "2.0.0.0/7", "4.0.0.0/6", "8.0.0.0/5", "16.0.0.0/4",
                     "32.0.0.0/3", "64.0.0.0/2", "128.0.0.0/2", "192.0.0.0/3")  # 1–223.x
AMMESSI_IPV6 = _reti("2000::/3")
#: Blocchi DENTRO gli ammessi che non sono destinazioni Internet (registri IANA
#: degli indirizzi per uso speciale IPv4 e IPv6, RFC 6890 e successivi).
NON_GLOBALI_IPV4 = _reti(
    "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.31.196.0/24", "192.52.193.0/24", "192.88.99.0/24",
    "192.168.0.0/16", "192.175.48.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24")
NON_GLOBALI_IPV6 = _reti(
    "2001::/23",       # assegnazioni di protocollo IETF (Teredo, ORCHID, benchmark, AMT…)
    "2001:db8::/32",   # documentazione
    "2002::/16",       # 6to4 (IPv4 incapsulato)
    "3fff::/20",       # documentazione (RFC 9637)
)


def _dentro(indirizzo: ipaddress.IPv4Address | ipaddress.IPv6Address,
            reti: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    return any(indirizzo in rete for rete in reti)


def indirizzo_globale_ammesso(indirizzo: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True solo per un unicast globale fuori dai blocchi per uso speciale
    (doppio controllo con ``is_global`` della libreria standard)."""
    if isinstance(indirizzo, ipaddress.IPv4Address):
        ammesso = (_dentro(indirizzo, AMMESSI_IPV4)
                   and not _dentro(indirizzo, NON_GLOBALI_IPV4))
    else:
        ammesso = (_dentro(indirizzo, AMMESSI_IPV6)
                   and not _dentro(indirizzo, NON_GLOBALI_IPV6))
    return ammesso and indirizzo.is_global


def indirizzo_ammesso(ip: str, *, consenti_privati: bool = False) -> bool:
    """True se l'indirizzo IP è una destinazione ammessa per un webhook.

    Con ``consenti_privati`` (solo sviluppo/test) si ammettono anche privati e
    loopback, mai però non specificati (0.0.0.0, ::), multicast né indirizzi
    incapsulati (IPv4 in IPv6, 6to4, Teredo)."""
    try:
        indirizzo = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if consenti_privati:
        if isinstance(indirizzo, ipaddress.IPv6Address) and (
                indirizzo.ipv4_mapped or indirizzo.sixtofour or indirizzo.teredo):
            return False
        return not (indirizzo.is_unspecified or indirizzo.is_multicast)
    return indirizzo_globale_ammesso(indirizzo)


def porta_ammessa(porta: int, ammesse: frozenset[int] | None) -> bool:
    """Porta di destinazione ammessa: tutte se ``ammesse`` è vuoto/None."""
    return not ammesse or porta in ammesse


def ip_letterale(host: str) -> str | None:
    """L'host come indirizzo IP normalizzato, o None se è un nome."""
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None


@dataclass(frozen=True)
class Destinazione:
    """URL validato, scomposto per la connessione con indirizzo fissato."""

    url: str
    host: str
    porta: int
    percorso: str  # percorso + query, mai vuoto


def valida_url(url: str, *, lunghezza_massima: int, consenti_privati: bool = False,
               porte_ammesse: frozenset[int] | None = None) -> Destinazione:
    """Destinazione dall'URL del webhook, o ``UrlNonValido``. ``porte_ammesse``
    (non vuoto) limita la porta di destinazione (WH-002 per le altre)."""
    testo = (url or "").strip()
    if not testo or len(testo) > lunghezza_massima:
        raise UrlNonValido("WH-001", f"URL vuoto o più lungo di {lunghezza_massima} caratteri")
    if any(ord(c) < 33 or ord(c) == 127 for c in testo):
        raise UrlNonValido("WH-001", "L'URL contiene spazi o caratteri di controllo")
    try:
        parti = urlsplit(testo)
        porta = parti.port
    except ValueError:
        raise UrlNonValido("WH-001", "URL non interpretabile") from None
    if parti.scheme.lower() != "https":
        raise UrlNonValido("WH-001", "Il webhook deve usare https")
    if parti.username is not None or parti.password is not None:
        raise UrlNonValido("WH-001", "L'URL non deve contenere credenziali: la consegna è firmata")
    if parti.fragment or testo.endswith("#"):
        raise UrlNonValido("WH-001", "L'URL non deve contenere un frammento (#)")
    host = (parti.hostname or "").rstrip(".").lower()
    if not host:
        raise UrlNonValido("WH-001", "L'URL non indica l'host")
    letterale = ip_letterale(host)
    if letterale is not None:
        if not indirizzo_ammesso(letterale, consenti_privati=consenti_privati):
            raise UrlNonValido("WH-002", "L'indirizzo indicato non è raggiungibile da Internet")
    elif "." not in host and not consenti_privati:
        # «localhost», nomi brevi della rete interna
        raise UrlNonValido("WH-002", "Il nome dell'host non è un nome Internet completo")
    if not porta_ammessa(porta or 443, porte_ammesse):
        raise UrlNonValido("WH-002", "Porta non ammessa per i webhook: "
                                     f"{', '.join(str(p) for p in sorted(porte_ammesse or ()))}")
    percorso = parti.path or "/"
    if parti.query:
        percorso = f"{percorso}?{parti.query}"
    return Destinazione(url=testo, host=letterale or host, porta=porta or 443, percorso=percorso)


# ── Segreto e firma ─────────────────────────────────────────────────

def genera_segreto() -> str:
    """Segreto HMAC nuovo (256 bit casuali), mostrato al cliente una sola volta."""
    return PREFISSO_SEGRETO + secrets.token_urlsafe(32)


def suffisso_segreto(segreto: str) -> str:
    """Ultimi 4 caratteri, per riconoscere il segreto senza mostrarlo."""
    return segreto[-4:]


def corpo_consegna(evento: dict[str, Any]) -> bytes:
    """Corpo JSON della consegna: compatto, chiavi ordinate, UTF-8. La firma
    copre esattamente questi byte."""
    return json.dumps(evento, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def calcola_firma(segreto: str, istante: int, corpo: bytes) -> str:
    """hex HMAC-SHA256(segreto, "<istante>." + corpo)."""
    messaggio = str(int(istante)).encode("ascii") + b"." + corpo
    return hmac.new(segreto.encode("utf-8"), messaggio, hashlib.sha256).hexdigest()


def intestazione_firma(segreto: str, istante: int, corpo: bytes) -> str:
    return f"t={int(istante)},{VERSIONE_FIRMA}={calcola_firma(segreto, istante, corpo)}"


def intestazione_firme(segreti: list[str], istante: int, corpo: bytes) -> str:
    """R13b: una firma ``v1`` per ogni segreto valido (rotazione: nuovo e precedente
    nel periodo di transizione), nello stesso istante. Il destinatario accetta se
    UNA corrisponde al segreto che conosce (``verifica_firma``)."""
    firme = ",".join(f"{VERSIONE_FIRMA}={calcola_firma(s, istante, corpo)}"
                     for s in dict.fromkeys(segreti))
    return f"t={int(istante)},{firme}"


def verifica_firma(segreto: str, intestazione: str, corpo: bytes, *, adesso: int,
                   tolleranza_secondi: int = 300) -> bool:
    """Verifica lato destinatario (usata nei test e come riferimento nella
    documentazione): firma corretta e istante entro la tolleranza (anti-replay)."""
    campi: dict[str, list[str]] = {}
    for parte in (intestazione or "").split(","):
        chiave, _, valore = parte.strip().partition("=")
        campi.setdefault(chiave, []).append(valore)
    try:
        istante = int(campi["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs(adesso - istante) > tolleranza_secondi:
        return False
    attesa = calcola_firma(segreto, istante, corpo)
    return any(hmac.compare_digest(attesa, f) for f in campi.get(VERSIONE_FIRMA, []))


# ── Ritentativi ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class EsitoTentativo:
    stato: str  # IN_CODA (si riprova) | FALLITA
    tentativi: int
    prossimo_tentativo: datetime | None


def esito_tentativo(tentativi_precedenti: int, *, ritentabile: bool, adesso: datetime,
                    ritardi_secondi: list[int]) -> EsitoTentativo:
    """Stato dopo un tentativo FALLITO. Tentativi massimi = len(ritardi) + 1."""
    fatti = tentativi_precedenti + 1
    if not ritentabile or fatti > len(ritardi_secondi):
        return EsitoTentativo(FALLITA, fatti, None)
    return EsitoTentativo(IN_CODA, fatti, adesso + timedelta(seconds=ritardi_secondi[fatti - 1]))
