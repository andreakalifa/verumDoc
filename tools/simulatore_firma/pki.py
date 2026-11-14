# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
PKI del simulatore (SOLO collaudo e test): CA, certificato di sigillo, certificato
della TSA, CRL e risposte OCSP, tutto generato all'avvio e mai salvato.

I certificati dichiarano nel nome «SIMULATORE»; il certificato di sigillo può
portare QcStatements/QcCompliance (``qualificato``) per provare il percorso
«qualificata» dei chiamanti: la qualificazione VERA si decide sulle radici del
prestatore configurate in console, che non includono mai questa CA.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from asn1crypto import algos, core, keys, ocsp, x509
from cryptography import x509 as cx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtendedKeyUsageOID, NameOID

#: QCStatements con QcCompliance (ETSI EN 319 412-5): SEQ{SEQ{OID 0.4.0.1862.1.1}}
QC_STATEMENTS_DER = bytes.fromhex("300a3008060604008e460101")
ORGANIZZAZIONE = "SIMULATORE VerumDoc - NON QUALIFICATO"


def _nome(cn: str) -> cx.Name:
    return cx.Name([cx.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                    cx.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZZAZIONE),
                    cx.NameAttribute(NameOID.COMMON_NAME, cn)])


def _uso(*, ca: bool = False, firma: bool = False) -> cx.KeyUsage:
    return cx.KeyUsage(digital_signature=not ca, content_commitment=firma,
                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                       key_cert_sign=ca, crl_sign=ca, encipher_only=False, decipher_only=False)


@dataclass
class PKISimulatore:
    ca_chiave: rsa.RSAPrivateKey
    ca: cx.Certificate
    firma_chiave: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
    firma: cx.Certificate
    tsa_chiave: rsa.RSAPrivateKey
    tsa: cx.Certificate
    revocati: set[int]

    @property
    def ca_pem(self) -> str:
        return self.ca.public_bytes(serialization.Encoding.PEM).decode()

    def der(self, cert: cx.Certificate) -> bytes:
        return cert.public_bytes(serialization.Encoding.DER)

    def crl(self, adesso: datetime | None = None) -> bytes:
        adesso = adesso or datetime.now(UTC)
        b = (cx.CertificateRevocationListBuilder().issuer_name(self.ca.subject)
             .last_update(adesso - timedelta(minutes=5))
             .next_update(adesso + timedelta(days=1))
             .add_extension(cx.CRLNumber(int(adesso.timestamp())), critical=False))
        for seriale in sorted(self.revocati):
            b = b.add_revoked_certificate(
                cx.RevokedCertificateBuilder().serial_number(seriale)
                .revocation_date(adesso - timedelta(minutes=1)).build())
        return b.sign(self.ca_chiave, hashes.SHA256()).public_bytes(serialization.Encoding.DER)

    def risposta_ocsp(self, richiesta_der: bytes, adesso: datetime | None = None) -> bytes:
        """OCSPResponse firmata direttamente dalla CA (RFC 6960 §4.2.2.2)."""
        adesso = adesso or datetime.now(UTC)
        richiesta = ocsp.OCSPRequest.load(richiesta_der)
        ca_asn1 = x509.Certificate.load(self.der(self.ca))
        risposte = []
        for r in richiesta["tbs_request"]["request_list"]:
            cert_id = r["req_cert"]
            seriale = cert_id["serial_number"].native
            stato = (ocsp.CertStatus(name="revoked", value={
                "revocation_time": adesso - timedelta(minutes=1)})
                if seriale in self.revocati else ocsp.CertStatus(name="good", value=core.Null()))
            risposte.append({"cert_id": cert_id, "cert_status": stato,
                             "this_update": adesso - timedelta(minutes=1),
                             "next_update": adesso + timedelta(hours=12)})
        tbs = ocsp.ResponseData({
            "responder_id": ocsp.ResponderId(
                name="by_key", value=hashlib.sha1(  # noqa: S324 — ResponderID RFC 6960
                    ca_asn1.public_key["public_key"].contents[1:]).digest()),
            "produced_at": adesso, "responses": risposte})
        firma = self.ca_chiave.sign(tbs.dump(), padding.PKCS1v15(), hashes.SHA256())
        basic = ocsp.BasicOCSPResponse({
            "tbs_response_data": tbs,
            "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
            "signature": firma})
        return ocsp.OCSPResponse({"response_status": "successful", "response_bytes": {
            "response_type": "basic_ocsp_response", "response": basic}}).dump()

    def emetti_personale(self, cn: str, *, adesso: datetime | None = None,
                         validita_giorni: int = 365
                         ) -> tuple[rsa.RSAPrivateKey, cx.Certificate]:
        """R16 (VD-C-166): certificato qualificato di firma PERSONALE (nome fittizio)
        emesso dalla CA del simulatore, con le stesse estensioni del sigillo."""
        adesso = adesso or datetime.now(UTC)
        chiave = rsa.generate_private_key(65537, 2048)
        estensioni = [e for e in self.firma.extensions
                      if e.oid.dotted_string not in ("2.5.29.14",)]
        b = (cx.CertificateBuilder().subject_name(_nome(cn)).issuer_name(self.ca.subject)
             .public_key(chiave.public_key()).serial_number(cx.random_serial_number())
             .not_valid_before(adesso - timedelta(days=1))
             .not_valid_after(adesso + timedelta(days=validita_giorni)))
        for e in estensioni:
            b = b.add_extension(e.value, critical=e.critical)
        return chiave, b.sign(self.ca_chiave, hashes.SHA256())

    def tsa_asn1(self) -> tuple[x509.Certificate, keys.PrivateKeyInfo]:
        return (x509.Certificate.load(self.der(self.tsa)),
                keys.PrivateKeyInfo.load(self.tsa_chiave.private_bytes(
                    serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption())))


def genera(url_pubblico: str, *, qualificato: bool = True, chiave: str = "rsa",
           validita_giorni: int = 365, adesso: datetime | None = None) -> PKISimulatore:
    """PKI nuova; ``url_pubblico`` serve per CRL (``/crl/ca.crl``) e OCSP (``/ocsp``)."""
    adesso = adesso or datetime.now(UTC)
    base = url_pubblico.rstrip("/")
    ca_k = rsa.generate_private_key(65537, 2048)
    ca = (cx.CertificateBuilder().subject_name(_nome("CA del SIMULATORE"))
          .issuer_name(_nome("CA del SIMULATORE")).public_key(ca_k.public_key())
          .serial_number(cx.random_serial_number())
          .not_valid_before(adesso - timedelta(days=1))
          .not_valid_after(adesso + timedelta(days=validita_giorni + 30))
          .add_extension(cx.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(_uso(ca=True), critical=True)
          .add_extension(cx.SubjectKeyIdentifier.from_public_key(ca_k.public_key()),
                         critical=False)
          .sign(ca_k, hashes.SHA256()))
    cdp = cx.CRLDistributionPoints([cx.DistributionPoint(
        full_name=[cx.UniformResourceIdentifier(f"{base}/crl/ca.crl")], relative_name=None,
        reasons=None, crl_issuer=None)])
    aia = cx.AuthorityInformationAccess([cx.AccessDescription(
        AuthorityInformationAccessOID.OCSP, cx.UniformResourceIdentifier(f"{base}/ocsp"))])
    firma_k: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey = (
        ec.generate_private_key(ec.SECP256R1()) if chiave == "ec"
        else rsa.generate_private_key(65537, 2048))
    b = (cx.CertificateBuilder().subject_name(_nome("Sigillo di prova del conservatore"))
         .issuer_name(ca.subject).public_key(firma_k.public_key())
         .serial_number(cx.random_serial_number())
         .not_valid_before(adesso - timedelta(days=1))
         .not_valid_after(adesso + timedelta(days=validita_giorni))
         .add_extension(cx.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(_uso(firma=True), critical=True)
         .add_extension(cdp, critical=False).add_extension(aia, critical=False)
         .add_extension(cx.AuthorityKeyIdentifier.from_issuer_public_key(ca_k.public_key()),
                        critical=False))
    if qualificato:
        b = b.add_extension(cx.UnrecognizedExtension(
            cx.ObjectIdentifier("1.3.6.1.5.5.7.1.3"), QC_STATEMENTS_DER), critical=False)
    firma = b.sign(ca_k, hashes.SHA256())
    tsa_k = rsa.generate_private_key(65537, 2048)
    tsa = (cx.CertificateBuilder().subject_name(_nome("TSA del SIMULATORE"))
           .issuer_name(ca.subject).public_key(tsa_k.public_key())
           .serial_number(cx.random_serial_number())
           .not_valid_before(adesso - timedelta(days=1))
           .not_valid_after(adesso + timedelta(days=validita_giorni))
           .add_extension(cx.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(_uso(), critical=True)
           .add_extension(cx.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]),
                          critical=True)
           .add_extension(cdp, critical=False)
           .add_extension(cx.AuthorityKeyIdentifier.from_issuer_public_key(ca_k.public_key()),
                          critical=False)
           .sign(ca_k, hashes.SHA256()))
    return PKISimulatore(ca_k, ca, firma_k, firma, tsa_k, tsa, set())


__all__ = ["PKISimulatore", "genera"]
