# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/revoca.py
Informazioni di revoca per il livello B-LT (R13a; ETSI EN 319 122-1 §5.5, EN 319 142-1 §5.4).

Per ogni certificato della catena (firmatario, intermedi, TSA; non le radici):
  1. OCSP dall'indirizzo AIA del certificato (RFC 6960, richiesta POST, nonce
     non richiesto: molti responder qualificati usano risposte precalcolate);
     la risposta si accetta solo se ``successful``;
  2. altrimenti la CRL dal punto di distribuzione (CDP).
Le informazioni che il fornitore dà senza rete (``revoca_locale``: CRL della
CA di sviluppo o del simulatore) si usano per prime. Il trasporto HTTP è
iniettabile (test con il simulatore). Lo stato «non revocato» lo stabilisce poi
la validazione (pyhanko-certvalidator, revoca obbligatoria): qui si raccoglie.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

import httpx
import structlog
from asn1crypto import algos, core, crl, ocsp, x509

from app.services.fornitori import ErroreFornitore
from app.services.http_sicuro import ClienteHttpSicuro

logger = structlog.get_logger(__name__)

DIMENSIONE_MASSIMA = 10 * 1024 * 1024


def _emittente(cert: x509.Certificate, candidati: Iterable[x509.Certificate]
               ) -> x509.Certificate | None:
    for c in candidati:
        if c.subject == cert.issuer and c is not cert:
            return c
    return None


def richiesta_ocsp(cert: x509.Certificate, emittente: x509.Certificate) -> bytes:
    """OCSPRequest DER (SHA-1 come CertID, come richiede RFC 6960 per l'interoperabilità)."""
    cert_id = ocsp.CertId({
        "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha1"}),
        "issuer_name_hash": hashlib.sha1(emittente.subject.dump()).digest(),  # noqa: S324
        "issuer_key_hash": hashlib.sha1(  # noqa: S324 — CertID RFC 6960
            emittente.public_key["public_key"].contents[1:]).digest(),
        "serial_number": cert.serial_number,
    })
    return ocsp.OCSPRequest({"tbs_request": {"request_list": [
        {"req_cert": cert_id}]}}).dump()


def _cdp(cert: x509.Certificate) -> list[str]:
    """Indirizzi http(s) dei punti di distribuzione della CRL."""
    urls: list[str] = []
    try:
        punti = cert.crl_distribution_points_value
        for punto in punti or []:
            dp = punto["distribution_point"]
            if dp.name != "full_name":
                continue
            for nome in dp.chosen:
                if nome.name == "uniform_resource_identifier":
                    urls.append(str(nome.native))
    except (KeyError, ValueError, AttributeError):
        return []
    return [u for u in urls if u.startswith(("http://", "https://"))]


def _ocsp_url(cert: x509.Certificate) -> list[str]:
    try:
        return [u for u in cert.ocsp_urls if u.startswith(("http://", "https://"))]
    except (KeyError, ValueError, AttributeError):
        return []


async def recupera(certificati: list[x509.Certificate], *,
                   gia_disponibili: tuple[list[bytes], list[bytes]] = ([], []),
                   trasporto: httpx.AsyncBaseTransport | None = None,
                   timeout: float = 20.0) -> tuple[list[bytes], list[bytes]]:
    """(CRL DER, risposte OCSP DER) per i certificati non radice di ``certificati``."""
    crl_der = list(gia_disponibili[0])
    ocsp_der = list(gia_disponibili[1])
    coperti: set[bytes] = set()
    for d in crl_der:
        try:
            coperti.add(crl.CertificateList.load(d)["tbs_cert_list"]["issuer"].dump())
        except ValueError:
            continue
    http = ClienteHttpSicuro("revoca", timeout=timeout, ritentativi=1, trasporto=trasporto)
    for cert in certificati:
        if cert.self_signed in ("maybe", "yes"):
            continue
        if cert.issuer.dump() in coperti:
            continue
        emittente = _emittente(cert, certificati)
        ottenuta = False
        if emittente is not None:
            for url in _ocsp_url(cert):
                try:
                    r = await http.richiesta(
                        "POST", url, operazione="ocsp", idempotente=True,
                        content=richiesta_ocsp(cert, emittente),
                        headers={"Content-Type": "application/ocsp-request"})
                except ErroreFornitore:
                    continue
                if r.status_code == 200 and len(r.content) <= DIMENSIONE_MASSIMA:
                    try:
                        risposta = ocsp.OCSPResponse.load(r.content)
                        if risposta["response_status"].native == "successful":
                            ocsp_der.append(r.content)
                            ottenuta = True
                            break
                    except ValueError:
                        logger.warning("ocsp_illeggibile", url_host=httpx.URL(url).host)
        if ottenuta:
            continue
        for url in _cdp(cert):
            try:
                r = await http.richiesta("GET", url, operazione="crl", idempotente=True)
            except ErroreFornitore:
                continue
            if r.status_code == 200 and len(r.content) <= DIMENSIONE_MASSIMA:
                try:
                    lista = crl.CertificateList.load(r.content)
                    _ = lista["tbs_cert_list"]["issuer"].native
                except ValueError:
                    continue
                crl_der.append(r.content)
                coperti.add(lista["tbs_cert_list"]["issuer"].dump())
                break
    return crl_der, ocsp_der


def scelta_ocsp(der: bytes) -> core.Asn1Value:
    """RevocationInfoChoice «other» con una risposta OCSP (RFC 5940)."""
    from asn1crypto import cms

    return cms.RevocationInfoChoice(
        name="other", value=cms.OtherRevocationInfoFormat({
            "other_rev_info_format": "ocsp_response",
            "other_rev_info": ocsp.OCSPResponse.load(der)}))


__all__ = ["recupera", "richiesta_ocsp", "scelta_ocsp"]
