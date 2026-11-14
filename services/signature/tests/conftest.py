# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Fixture condivise del servizio di firma (VD-C-010).

Le chiavi di sviluppo si generano una volta per sessione (RSA 3072 è lento).
Il RSSP CSC e la TSA RFC 3161 sono simulati con httpx.MockTransport: i client
reali parlano HTTP con server finti, mai con la rete (REGOLE §20.1).
"""
from __future__ import annotations

import os

# Prima di importare l'app: ambiente di test. Senza JWT_PUBLIC_KEY in ambiente
# il servizio usa una coppia RS256 effimera in memoria (solo con ENV=test, VD-C-004).
os.environ["ENV"] = "test"

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.services.sviluppo import ChiaviSviluppo, TSASviluppo, genera_chiavi_sviluppo
from asn1crypto import tsp
from cryptography import x509 as cx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.x509.oid import NameOID

ADESSO = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def chiavi() -> ChiaviSviluppo:
    return genera_chiavi_sviluppo(datetime.now(UTC))


@pytest.fixture(scope="session")
def altre_chiavi() -> ChiaviSviluppo:
    """Seconda PKI di sviluppo: serve a simulare radici non attendibili."""
    return genera_chiavi_sviluppo(datetime.now(UTC))


@pytest.fixture
def token_jwt() -> str:
    from app.auth.jwt_handler import create_access_token

    return create_access_token(tenant_id="t-001", user_id="u-001", ruolo="SUPER_ADMIN")


@pytest.fixture
def token_servizio() -> str:
    """Token di servizio di hashchain (audience dedicata, scope firma:servizio):
    l'unico ammesso da /firma e /marca (revisione di sicurezza M2)."""
    from app.auth.jwt_handler import create_service_token

    return create_service_token("hashchain")


# ── PKI «qualificata» di test (certificato con QcCompliance) ──────────

QC_STATEMENTS_DER = bytes.fromhex("300a3008060604008e460101")  # SEQ{SEQ{OID 0.4.0.1862.1.1}}


def _nome(cn: str) -> cx.Name:
    return cx.Name(
        [cx.NameAttribute(NameOID.COUNTRY_NAME, "IT"), cx.NameAttribute(NameOID.COMMON_NAME, cn)]
    )


@pytest.fixture(scope="session")
def pki_qualificata() -> dict:
    adesso = datetime.now(UTC)
    ca_k = rsa.generate_private_key(65537, 2048)
    ca = (
        cx.CertificateBuilder()
        .subject_name(_nome("CA di test QTSP"))
        .issuer_name(_nome("CA di test QTSP"))
        .public_key(ca_k.public_key())
        .serial_number(cx.random_serial_number())
        .not_valid_before(adesso - timedelta(days=1))
        .not_valid_after(adesso + timedelta(days=365))
        .add_extension(cx.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            cx.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_k, hashes.SHA256())
    )
    k = rsa.generate_private_key(65537, 2048)
    cert = (
        cx.CertificateBuilder()
        .subject_name(_nome("Sigillo di test del conservatore"))
        .issuer_name(ca.subject)
        .public_key(k.public_key())
        .serial_number(cx.random_serial_number())
        .not_valid_before(adesso - timedelta(days=1))
        .not_valid_after(adesso + timedelta(days=365))
        .add_extension(cx.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            cx.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            cx.UnrecognizedExtension(cx.ObjectIdentifier("1.3.6.1.5.5.7.1.3"), QC_STATEMENTS_DER),
            critical=False,
        )
        .sign(ca_k, hashes.SHA256())
    )
    return {"ca": ca, "chiave": k, "cert": cert}


def _der_b64(cert: cx.Certificate) -> str:
    return base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()


class RSSPSimulato:
    """Server CSC API v2 minimale: oauth2/token, credentials/info|authorize, signatures/signHash."""

    def __init__(
        self, chiave: rsa.RSAPrivateKey, catena: list[cx.Certificate], pin: str = "1234"
    ) -> None:
        self.chiave = chiave
        self.catena = catena
        self.pin = pin
        self.chiamate: list[str] = []
        self.sad_emessi: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        percorso = request.url.path
        self.chiamate.append(percorso)
        if percorso.endswith("/oauth2/token"):
            corpo = dict(x.split("=") for x in request.content.decode().split("&"))
            if corpo.get("client_secret") != "segreto":
                return httpx.Response(401, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        if request.headers.get("authorization") != "Bearer tok-1":
            return httpx.Response(401, json={"error": "invalid_token"})
        dati = json.loads(request.content)
        if percorso.endswith("/credentials/info"):
            return httpx.Response(
                200,
                json={
                    "key": {"status": "enabled", "algo": ["1.2.840.113549.1.1.1"], "len": 2048},
                    "cert": {"status": "valid", "certificates": [_der_b64(c) for c in self.catena]},
                    "authMode": "explicit",
                    "SCAL": "2",
                },
            )
        if percorso.endswith("/credentials/authorize"):
            if dati.get("PIN") != self.pin:
                return httpx.Response(
                    400, json={"error": "invalid_request", "error_description": "PIN errato"}
                )
            sad = f"SAD-{dati['hashes'][0][:8]}"
            self.sad_emessi.add(sad)
            return httpx.Response(200, json={"SAD": sad, "expiresIn": 300})
        if percorso.endswith("/signatures/signHash"):
            if dati.get("SAD") not in self.sad_emessi:
                return httpx.Response(
                    400, json={"error": "invalid_request", "error_description": "SAD"}
                )
            assert dati["hashAlgorithmOID"] == "2.16.840.1.101.3.4.2.1"
            assert dati["signAlgo"] == "1.2.840.113549.1.1.11"
            firme = [
                base64.b64encode(
                    self.chiave.sign(
                        base64.b64decode(h), padding.PKCS1v15(), utils.Prehashed(hashes.SHA256())
                    )
                ).decode()
                for h in dati["hashes"]
            ]
            return httpx.Response(200, json={"signatures": firme})
        return httpx.Response(404, json={"error": "not_found"})


class TSASimulata:
    """Server HTTP RFC 3161 che risponde con la TSA di sviluppo (token veri)."""

    def __init__(self, tsa: TSASviluppo) -> None:
        self.tsa = tsa
        self.ultima: bytes | None = None
        self.modalita = "normale"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("content-type") != "application/timestamp-query":
            return httpx.Response(415)
        if self.modalita == "errore_http":
            return httpx.Response(500)
        if self.modalita == "tipo_errato":
            return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
        if self.modalita == "rifiuto":
            # TimeStampResp ::= SEQUENCE { status PKIStatusInfo } (token assente)
            stato = tsp.PKIStatusInfo(
                {"status": "rejection", "status_string": ["policy non ammessa"]}
            ).dump()
            der = b"\x30" + bytes([len(stato)]) + stato
            return httpx.Response(
                200, content=der, headers={"content-type": "application/timestamp-reply"}
            )
        if self.modalita == "replay" and self.ultima is not None:
            return httpx.Response(
                200, content=self.ultima, headers={"content-type": "application/timestamp-reply"}
            )
        req = tsp.TimeStampReq.load(request.content)
        if self.modalita == "impronta_altra":
            imprint = req["message_imprint"]
            req = tsp.TimeStampReq(
                {
                    "version": 1,
                    "cert_req": True,
                    "nonce": req["nonce"],
                    "message_imprint": {
                        "hash_algorithm": imprint["hash_algorithm"],
                        "hashed_message": b"\x00" * 32,
                    },
                }
            )
        risposta = self.tsa.rispondi(req).dump()
        self.ultima = risposta
        return httpx.Response(
            200, content=risposta, headers={"content-type": "application/timestamp-reply"}
        )


@pytest.fixture
def tsa_simulata(chiavi: ChiaviSviluppo) -> TSASimulata:
    return TSASimulata(TSASviluppo(chiavi))
