# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/infrastructure/consegna_http.py
Invio HTTP di una consegna webhook con protezione SSRF — contratto C3.

Contro SSRF e DNS rebinding il nome si risolve UNA volta, si controllano TUTTI
gli indirizzi restituiti (``indirizzo_ammesso``) e la connessione va al primo
indirizzo controllato, non a una nuova risoluzione: l'URL della richiesta usa
l'IP, l'intestazione ``Host`` e il nome TLS (SNI e verifica del certificato,
estensione ``sni_hostname`` di httpcore) usano il nome. Così il certificato si
verifica sul nome dichiarato dal cliente e la connessione non può finire su
un indirizzo diverso da quello controllato.

Altre scelte:
  - niente reindirizzamenti (un 3xx è un esito non positivo);
  - niente proxy d'ambiente (``trust_env=False``): il proxy annullerebbe il
    fissaggio dell'indirizzo;
  - niente connessioni riusate fra consegne (``max_keepalive_connections=0``):
    il pool è indicizzato per IP e una connessione verificata per un nome non
    deve servire un altro nome sullo stesso IP;
  - il corpo della risposta non si legge (risposte enormi non occupano memoria)
    e non si salva: dell'esito restano stato HTTP e un messaggio nostro.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import structlog

from app.domain.webhook import Destinazione, indirizzo_ammesso, ip_letterale

logger = structlog.get_logger(__name__)

#: (host, porta) → indirizzi IP risolti
Risolutore = Callable[[str, int], Awaitable[list[str]]]


class IndirizzoNonAmmesso(Exception):
    """Il nome risolve (anche) verso un indirizzo non ammesso."""


async def risolvi_dns(host: str, porta: int) -> list[str]:
    """Indirizzi IP del nome (resolver di sistema, senza bloccare il ciclo)."""
    loop = asyncio.get_running_loop()
    risultati = await loop.getaddrinfo(host, porta, type=socket.SOCK_STREAM)
    visti: list[str] = []
    for _famiglia, _tipo, _proto, _nome, indirizzo in risultati:
        ip = str(indirizzo[0])
        if ip not in visti:
            visti.append(ip)
    return visti


@dataclass(frozen=True)
class EsitoInvio:
    consegnata: bool
    codice: str | None = None  # WH-101..WH-104 se non consegnata
    stato_http: int | None = None
    messaggio: str | None = None
    ritentabile: bool = True


def contesto_tls(ca_pem: str = "") -> ssl.SSLContext:
    """Contesto TLS con verifica del certificato e del nome (sempre attiva)."""
    contesto = ssl.create_default_context()
    contesto.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca_pem.strip():
        contesto.load_verify_locations(cadata=ca_pem)
    return contesto


class InviatoreWebhook:
    """Client HTTP unico per le consegne (REGOLE §20.1)."""

    def __init__(self, *, timeout_connessione: float, timeout: float,
                 consenti_privati: bool = False, ca_pem: str = "",
                 risolutore: Risolutore = risolvi_dns) -> None:
        self._consenti_privati = consenti_privati
        self._risolutore = risolutore
        self._client = httpx.AsyncClient(
            verify=contesto_tls(ca_pem),
            timeout=httpx.Timeout(timeout, connect=timeout_connessione),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=200),
        )

    async def chiudi(self) -> None:
        await self._client.aclose()

    async def _indirizzo(self, destinazione: Destinazione) -> str:
        letterale = ip_letterale(destinazione.host)
        indirizzi = [letterale] if letterale else await self._risolutore(
            destinazione.host, destinazione.porta)
        if not indirizzi:
            raise socket.gaierror("nessun indirizzo")
        for ip in indirizzi:
            if not indirizzo_ammesso(ip, consenti_privati=self._consenti_privati):
                raise IndirizzoNonAmmesso(ip)
        return indirizzi[0]

    async def invia(self, destinazione: Destinazione, corpo: bytes,
                    intestazioni: dict[str, str]) -> EsitoInvio:
        try:
            ip = await self._indirizzo(destinazione)
        except IndirizzoNonAmmesso:
            # L'indirizzo non va nel messaggio salvato (lo leggerebbe il cliente)
            logger.warning("webhook_indirizzo_non_ammesso", host=destinazione.host)
            return EsitoInvio(False, "WH-104", None,
                              "Il nome del destinatario risolve verso un indirizzo non ammesso",
                              ritentabile=False)
        except (OSError, UnicodeError):
            return EsitoInvio(False, "WH-102", None, "Nome del destinatario non risolto")

        host_url = f"[{ip}]" if ":" in ip else ip
        url = f"https://{host_url}:{destinazione.porta}{destinazione.percorso}"
        host_intestazione = destinazione.host if ":" not in destinazione.host \
            else f"[{destinazione.host}]"
        if destinazione.porta != 443:
            host_intestazione = f"{host_intestazione}:{destinazione.porta}"
        estensioni: dict[str, object] = {}
        if ip_letterale(destinazione.host) is None:
            estensioni["sni_hostname"] = destinazione.host
        try:
            async with self._client.stream(
                "POST", url, content=corpo,
                headers={**intestazioni, "Host": host_intestazione,
                         "Content-Type": "application/json",
                         "User-Agent": "VerumDoc-Webhook/1"},
                extensions=estensioni,
            ) as risposta:
                stato = risposta.status_code
        except httpx.TimeoutException:
            return EsitoInvio(False, "WH-101", None, "Tempo di risposta superato")
        except httpx.TransportError as exc:
            motivo = "Certificato TLS non valido" if isinstance(
                exc.__context__ or exc.__cause__, ssl.SSLError) or "CERTIFICATE" in str(exc) \
                else "Connessione non riuscita"
            return EsitoInvio(False, "WH-102", None, motivo)
        if 200 <= stato < 300:
            return EsitoInvio(True, None, stato)
        if 300 <= stato < 400:
            return EsitoInvio(False, "WH-103", stato, "Reindirizzamento non seguito")
        return EsitoInvio(False, "WH-103", stato, f"Il destinatario ha risposto {stato}")
