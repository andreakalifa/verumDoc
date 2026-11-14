# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/sviluppo.py
Adattatore di SVILUPPO per firma e marca — VD-C-010.

Produce firme CAdES/PAdES e marche RFC 3161 crittograficamente VERE ma NON
QUALIFICATE: chiavi RSA autogenerate, CA di sviluppo, nessun valore legale.
Ogni certificato lo dichiara nel nome; ogni risposta del servizio lo dichiara
nel campo `qualificata=false` e nell'avviso. In ENV=production è rifiutato
(registro.py).

Le chiavi vivono in memoria, oppure in `SVILUPPO_DIR_CHIAVI` (permessi 0600)
se si vuole poter verificare le firme dopo un riavvio.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from asn1crypto import algos, cms, keys, tsp, x509
from cryptography import x509 as cx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore

from app.services.fornitori import FornitoreFirma, FornitoreMarca, MaterialeFirma

logger = structlog.get_logger(__name__)

ORGANIZZAZIONE = "VerumDoc - AMBIENTE DI SVILUPPO - NON QUALIFICATO"
CN_CA = "CA di SVILUPPO VerumDoc - NON QUALIFICATA"
CN_FIRMA = "Sigillo di SVILUPPO VerumDoc - NON QUALIFICATO"
CN_TSA = "TSA di SVILUPPO VerumDoc - NON QUALIFICATA"
VALIDITA = timedelta(days=730)


@dataclass(frozen=True)
class ChiaviSviluppo:
    ca_cert: cx.Certificate
    firma_chiave: rsa.RSAPrivateKey
    firma_cert: cx.Certificate
    tsa_chiave: rsa.RSAPrivateKey
    tsa_cert: cx.Certificate
    #: R13a: chiave della CA di sviluppo, per la CRL (livello B-LT); None per
    #: chiavi salvate prima di R13a (nessuna CRL: B-LT non disponibile)
    ca_chiave: rsa.RSAPrivateKey | None = None

    def crl(self, adesso: datetime) -> bytes | None:
        """CRL vuota (DER) della CA di sviluppo, valida 7 giorni."""
        if self.ca_chiave is None:
            return None
        lista = (cx.CertificateRevocationListBuilder()
                 .issuer_name(self.ca_cert.subject)
                 .last_update(adesso - timedelta(minutes=5))
                 .next_update(adesso + timedelta(days=7))
                 .add_extension(cx.CRLNumber(1), critical=False)
                 .sign(self.ca_chiave, hashes.SHA256()))
        return lista.public_bytes(serialization.Encoding.DER)


def _nome(cn: str) -> cx.Name:
    return cx.Name(
        [
            cx.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
            cx.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZZAZIONE),
            cx.NameAttribute(NameOID.COMMON_NAME, cn),
        ]
    )


def _certificato(
    soggetto: str,
    chiave: rsa.RSAPrivateKey,
    emittente: cx.Name | None,
    chiave_emittente: rsa.RSAPrivateKey,
    adesso: datetime,
    *,
    ca: bool = False,
    tsa: bool = False,
) -> cx.Certificate:
    builder = (
        cx.CertificateBuilder()
        .subject_name(_nome(soggetto))
        .issuer_name(emittente or _nome(soggetto))
        .public_key(chiave.public_key())
        .serial_number(cx.random_serial_number())
        .not_valid_before(adesso - timedelta(minutes=5))
        .not_valid_after(adesso + VALIDITA)
        .add_extension(cx.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            cx.KeyUsage(
                digital_signature=True,
                content_commitment=not ca and not tsa,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(cx.SubjectKeyIdentifier.from_public_key(chiave.public_key()), critical=False)
    )
    if tsa:
        builder = builder.add_extension(
            cx.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]),
            critical=True,
        )
    return builder.sign(chiave_emittente, hashes.SHA256())


def genera_chiavi_sviluppo(adesso: datetime) -> ChiaviSviluppo:
    """Genera CA, chiave di firma e chiave TSA di sviluppo (RSA 3072)."""
    ca_k = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_c = _certificato(CN_CA, ca_k, None, ca_k, adesso, ca=True)
    f_k = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    f_c = _certificato(CN_FIRMA, f_k, ca_c.subject, ca_k, adesso)
    t_k = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    t_c = _certificato(CN_TSA, t_k, ca_c.subject, ca_k, adesso, tsa=True)
    return ChiaviSviluppo(ca_c, f_k, f_c, t_k, t_c, ca_k)


_FILE = ("ca.pem", "firma.key", "firma.pem", "tsa.key", "tsa.pem")


def carica_o_genera(cartella: str, adesso: datetime) -> ChiaviSviluppo:
    """Riusa le chiavi in `cartella` se presenti e valide, altrimenti le genera."""
    if not cartella:
        return genera_chiavi_sviluppo(adesso)
    base = Path(cartella)
    if all((base / f).is_file() for f in _FILE):

        def _cert(n: str) -> cx.Certificate:
            return cx.load_pem_x509_certificate((base / n).read_bytes())

        def _key(n: str) -> rsa.RSAPrivateKey:
            k = serialization.load_pem_private_key((base / n).read_bytes(), password=None)
            if not isinstance(k, rsa.RSAPrivateKey):
                raise ValueError(f"chiave di sviluppo {n} non RSA")
            return k

        chiavi = ChiaviSviluppo(
            _cert("ca.pem"),
            _key("firma.key"),
            _cert("firma.pem"),
            _key("tsa.key"),
            _cert("tsa.pem"),
            _key("ca.key") if (base / "ca.key").is_file() else None,
        )
        if chiavi.firma_cert.not_valid_after_utc > adesso:
            return chiavi
        logger.warning("chiavi_sviluppo_scadute_rigenerate", cartella=str(base))
    chiavi = genera_chiavi_sviluppo(adesso)
    base.mkdir(parents=True, exist_ok=True)
    pem = serialization.Encoding.PEM

    def _scrivi(nome: str, dati: bytes) -> None:
        p = base / nome
        p.write_bytes(dati)
        os.chmod(p, 0o600)

    def _k(k: rsa.RSAPrivateKey) -> bytes:
        return k.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    _scrivi("ca.pem", chiavi.ca_cert.public_bytes(pem))
    if chiavi.ca_chiave is not None:
        _scrivi("ca.key", _k(chiavi.ca_chiave))
    _scrivi("firma.key", _k(chiavi.firma_chiave))
    _scrivi("firma.pem", chiavi.firma_cert.public_bytes(pem))
    _scrivi("tsa.key", _k(chiavi.tsa_chiave))
    _scrivi("tsa.pem", chiavi.tsa_cert.public_bytes(pem))
    logger.info("chiavi_sviluppo_generate", cartella=str(base))
    return chiavi


def a_asn1(cert: cx.Certificate) -> x509.Certificate:
    return x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


def chiave_a_asn1(chiave: rsa.RSAPrivateKey) -> keys.PrivateKeyInfo:
    return keys.PrivateKeyInfo.load(
        chiave.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


_HASH = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}


class FornitoreFirmaSviluppo(FornitoreFirma):
    """Firma con chiave locale autogenerata. NON qualificata."""

    nome = "sviluppo"
    di_sviluppo = True

    def __init__(self, chiavi: ChiaviSviluppo, codice: str = "") -> None:
        self._chiavi = chiavi
        self.codice = codice
        self.radici_proprie = (a_asn1(chiavi.ca_cert),)
        self._materiale = MaterialeFirma(
            certificato=a_asn1(chiavi.firma_cert),
            catena=[a_asn1(chiavi.ca_cert)],
            meccanismo=algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
        )

    @property
    def certificato_ca(self) -> x509.Certificate:
        return a_asn1(self._chiavi.ca_cert)

    async def materiale(self) -> MaterialeFirma:
        return self._materiale

    async def firma_impronta(self, impronta: bytes, algoritmo_digest: str) -> bytes:
        alg = _HASH.get(algoritmo_digest)
        if alg is None:
            raise ValueError(f"algoritmo di digest non supportato: {algoritmo_digest}")
        return self._chiavi.firma_chiave.sign(impronta, padding.PKCS1v15(),
                                              utils.Prehashed(alg()))

    async def revoca_locale(self, adesso: datetime) -> tuple[list[bytes], list[bytes]]:
        """CRL della CA di sviluppo (B-LT senza rete)."""
        crl = self._chiavi.crl(adesso)
        return ([crl] if crl else []), []


class TSASviluppo:
    """TSA RFC 3161 locale con chiave di sviluppo: risponde a una TimeStampReq.

    Usata dal fornitore di marca di sviluppo e, nei test, come server TSA
    simulato dietro il client HTTP reale.
    """

    def __init__(self, chiavi: ChiaviSviluppo, adesso: datetime | None = None) -> None:
        self._motore = DummyTimeStamper(
            tsa_cert=a_asn1(chiavi.tsa_cert),
            tsa_key=chiave_a_asn1(chiavi.tsa_chiave),
            certs_to_embed=SimpleCertificateStore.from_certs([a_asn1(chiavi.ca_cert)]),
            fixed_dt=adesso,
        )

    def rispondi(self, richiesta: tsp.TimeStampReq) -> tsp.TimeStampResp:
        return self._motore.request_tsa_response(richiesta)

    async def marca(self, impronta: bytes, algoritmo: str) -> cms.ContentInfo:
        return await self._motore.async_timestamp(impronta, algoritmo)


class FornitoreMarcaSviluppo(FornitoreMarca):
    """Marca RFC 3161 vera, emessa da una TSA locale di sviluppo. NON qualificata."""

    nome = "sviluppo"
    di_sviluppo = True

    def __init__(self, chiavi: ChiaviSviluppo, codice: str = "") -> None:
        self._tsa = TSASviluppo(chiavi)
        self._ca = a_asn1(chiavi.ca_cert)
        self._chiavi = chiavi
        self.codice = codice

    async def marca(self, impronta: bytes, algoritmo: str) -> cms.ContentInfo:
        return await self._tsa.marca(impronta, algoritmo)

    def radici_attendibili(self) -> list[x509.Certificate]:
        return [self._ca]

    async def revoca_locale(self, adesso: datetime) -> tuple[list[bytes], list[bytes]]:
        crl = self._chiavi.crl(adesso)
        return ([crl] if crl else []), []


def adesso_utc() -> datetime:
    return datetime.now(UTC)
