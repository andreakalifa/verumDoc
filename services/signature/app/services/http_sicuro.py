# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/http_sicuro.py
Client HTTP verso i prestatori (CSC, TSA, OCSP/CRL) — R13a, REGOLE §20.1, §20.4.

  - tempo massimo per richiesta (connessione e lettura);
  - ritentativi con attesa crescente: SEMPRE se la connessione non si è aperta
    (la richiesta non è partita), solo per le operazioni IDEMPOTENTI su tempo
    scaduto in lettura, connessione caduta e HTTP 502/503/504 (``Retry-After``
    rispettato fino a 5 s). Un'autorizzazione con OTP non è idempotente (l'OTP si
    consuma) e non si ritenta mai a metà;
  - mTLS facoltativo: certificato e chiave del client dal Secret (PEM), caricati
    da un file temporaneo 0600 subito cancellato;
  - pinning facoltativo: impronte SHA-256 della SubjectPublicKeyInfo del
    certificato del server. Con il pinning attivo ogni risposta si accetta solo
    se la connessione TLS che l'ha portata mostra una chiave fra quelle
    attese (altrimenti errore, la risposta non si usa) e, prima di spedire un
    segreto (token OAuth2), una connessione di prova verifica la chiave. Il
    pinning si somma alla verifica TLS normale, non la sostituisce;
  - host ammessi (revisione R13a (2)): con segreti legati agli host del Secret
    ogni richiesta verso un host diverso si rifiuta PRIMA di partire; con
    ``solo_https`` (produzione e segreti, revisione R13a (3)) anche ogni
    indirizzo non https;
  - una riga di log per chiamata, senza segreti, corpi né impronte complete.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import ssl
import tempfile
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.services.fornitori import ErroreFornitore

logger = structlog.get_logger(__name__)

STATI_RITENTABILI = frozenset({502, 503, 504})
ATTESA_MASSIMA_S = 5.0


def spki_sha256(certificato_der: bytes) -> str:
    """Impronta SHA-256 (esadecimale) della SubjectPublicKeyInfo di un certificato."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_der_x509_certificate(certificato_der)
    spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).hexdigest()


def contesto_tls(mtls_certificato_pem: str = "", mtls_chiave_pem: str = "") -> ssl.SSLContext:
    """Contesto TLS con verifica del server (radici del sistema/proxy) e, se dati,
    certificato e chiave del client (mTLS)."""
    cafile = os.environ.get("SSL_CERT_FILE") or None
    contesto = ssl.create_default_context(cafile=cafile)
    contesto.minimum_version = ssl.TLSVersion.TLSv1_2
    if mtls_certificato_pem or mtls_chiave_pem:
        if not (mtls_certificato_pem and mtls_chiave_pem):
            raise ErroreFornitore("mTLS: servono sia il certificato sia la chiave del client",
                                  {"servizio": "mtls"})
        descrittore, percorso = tempfile.mkstemp(prefix="vd-mtls-", suffix=".pem")
        try:
            os.chmod(percorso, 0o600)
            with os.fdopen(descrittore, "w", encoding="ascii") as f:
                f.write(mtls_certificato_pem.strip() + "\n" + mtls_chiave_pem.strip() + "\n")
            contesto.load_cert_chain(percorso)
        except (ssl.SSLError, ValueError) as exc:
            raise ErroreFornitore("mTLS: certificato o chiave del client non validi",
                                  {"servizio": "mtls"}) from exc
        finally:
            try:
                os.unlink(percorso)
            except OSError:
                pass
    return contesto


def _certificato_della_risposta(risposta: httpx.Response) -> bytes | None:
    flusso = risposta.extensions.get("network_stream")
    if flusso is None:
        return None
    oggetto = flusso.get_extra_info("ssl_object")
    if oggetto is None:
        return None
    der = oggetto.getpeercert(binary_form=True)
    return bytes(der) if der else None


def controlla_pin(certificato_der: bytes | None, pin: Iterable[str], servizio: str) -> None:
    """Pinning: la chiave del server deve essere fra quelle attese (fail-closed)."""
    attesi = {p.lower() for p in pin}
    if not attesi:
        return
    if certificato_der is None:
        raise ErroreFornitore("pinning attivo ma connessione senza TLS verificabile",
                              {"servizio": servizio, "motivo": "pin_non_verificabile"})
    if spki_sha256(certificato_der) not in attesi:
        logger.error("pinning_chiave_diversa", servizio=servizio)
        raise ErroreFornitore("chiave del server diversa da quelle attese (pinning)",
                              {"servizio": servizio, "motivo": "pin_diverso"})


class ClienteHttpSicuro:
    """Client HTTP di un fornitore (un'istanza per fornitore)."""

    def __init__(self, servizio: str, *, timeout: float = 30.0, ritentativi: int = 2,
                 trasporto: httpx.AsyncBaseTransport | None = None,
                 mtls_certificato_pem: str = "", mtls_chiave_pem: str = "",
                 pin_spki_sha256: Iterable[str] = (), attesa_base: float = 0.2,
                 host_ammessi: Iterable[str] = (), solo_https: bool = False) -> None:
        self.servizio = servizio
        self.host_ammessi = frozenset(h.lower() for h in host_ammessi)
        self.solo_https = solo_https
        self.timeout = timeout
        self.ritentativi = max(0, int(ritentativi))
        self.pin = tuple(p.lower() for p in pin_spki_sha256)
        self._trasporto = trasporto
        self._attesa_base = attesa_base
        self._tls: ssl.SSLContext | bool = True
        if mtls_certificato_pem or mtls_chiave_pem or self.pin:
            self._tls = contesto_tls(mtls_certificato_pem, mtls_chiave_pem)
        self._pin_verificati: dict[str, float] = {}

    def controlla_destinazione(self, url: str) -> None:
        """Host fra quelli legati ai segreti e https quando richiesto (fail-closed)."""
        parti = urlparse(url)
        if self.solo_https and parti.scheme != "https":
            raise ErroreFornitore(f"{self.servizio}: indirizzo non https con credenziali",
                                  {"servizio": self.servizio, "motivo": "non_https"})
        if self.host_ammessi and (parti.hostname or "").lower() not in self.host_ammessi:
            logger.error("destinazione_non_ammessa", servizio=self.servizio)
            raise ErroreFornitore(f"{self.servizio}: host non legato ai segreti del fornitore",
                                  {"servizio": self.servizio, "motivo": "host_non_ammesso"})

    async def verifica_pin_preliminare(self, url: str) -> None:
        """Connessione TLS di prova prima di spedire un segreto (solo con pinning
        attivo e senza trasporto iniettato). Verifica ripetuta al più ogni 5 minuti."""
        self.controlla_destinazione(url)
        if not self.pin or self._trasporto is not None:
            return
        parti = urlparse(url)
        if parti.scheme != "https" or not parti.hostname:
            raise ErroreFornitore("pinning attivo con un indirizzo non https",
                                  {"servizio": self.servizio, "motivo": "pin_non_verificabile"})
        chiave = f"{parti.hostname}:{parti.port or 443}"
        if self._pin_verificati.get(chiave, 0.0) > time.monotonic():
            return
        contesto = self._tls if isinstance(self._tls, ssl.SSLContext) else contesto_tls()
        try:
            _, scrittore = await asyncio.wait_for(asyncio.open_connection(
                parti.hostname, parti.port or 443, ssl=contesto,
                server_hostname=parti.hostname), timeout=self.timeout)
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            raise ErroreFornitore(f"{self.servizio}: connessione di prova non riuscita: "
                                  f"{type(exc).__name__}", {"servizio": self.servizio}) from exc
        try:
            oggetto = scrittore.get_extra_info("ssl_object")
            der = oggetto.getpeercert(binary_form=True) if oggetto else None
            controlla_pin(der, self.pin, self.servizio)
        finally:
            scrittore.close()
        self._pin_verificati[chiave] = time.monotonic() + 300

    async def richiesta(self, metodo: str, url: str, *, operazione: str, idempotente: bool,
                        **argomenti: Any) -> httpx.Response:
        """Una richiesta con i ritentativi descritti nel modulo. Errori di rete →
        ``ErroreFornitore`` (transitorio per il chiamante)."""
        self.controlla_destinazione(url)
        tentativo = 0
        while True:
            inizio = time.monotonic()
            esito = "non_raggiungibile"
            try:
                async with httpx.AsyncClient(transport=self._trasporto, timeout=self.timeout,
                                             verify=self._tls, follow_redirects=False) as c:
                    risposta = await c.request(metodo, url, **argomenti)
                    # pinning: la risposta vale solo dalla connessione con la chiave attesa
                    controlla_pin(_certificato_della_risposta(risposta), self.pin,
                                  self.servizio)
                esito = f"http_{risposta.status_code}"
                if (risposta.status_code in STATI_RITENTABILI and idempotente
                        and tentativo < self.ritentativi):
                    attesa = self._attesa(tentativo, risposta.headers.get("retry-after"))
                    tentativo += 1
                    await asyncio.sleep(attesa)
                    continue
                return risposta
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                errore: Exception = exc
                ritentabile = True  # la richiesta non è partita
            except (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError,
                    httpx.WriteError, httpx.PoolTimeout) as exc:
                errore, ritentabile = exc, idempotente
            except httpx.HTTPError as exc:
                errore, ritentabile = exc, False
            finally:
                logger.info("chiamata_integrazione", servizio=self.servizio,
                            operazione=operazione, esito=esito, tentativo=tentativo,
                            durata_ms=int((time.monotonic() - inizio) * 1000))
            if ritentabile and tentativo < self.ritentativi:
                await asyncio.sleep(self._attesa(tentativo, None))
                tentativo += 1
                continue
            raise ErroreFornitore(f"{self.servizio} non raggiungibile: {type(errore).__name__}",
                                  {"servizio": self.servizio, "operazione": operazione}
                                  ) from errore

    def _attesa(self, tentativo: int, retry_after: str | None) -> float:
        if retry_after and retry_after.strip().isdigit():
            return min(float(retry_after.strip()), ATTESA_MASSIMA_S)
        return min(self._attesa_base * (2 ** tentativo), ATTESA_MASSIMA_S)


__all__ = ["ClienteHttpSicuro", "contesto_tls", "controlla_pin", "spki_sha256"]
