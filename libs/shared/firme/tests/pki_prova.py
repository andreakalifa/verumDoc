# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
PKI di prova generata A RUNTIME per i test delle firme (R08, D01).

Nessuna chiave sta nel repository: CA, certificati del firmatario, CRL e
risposte OCSP si generano in memoria a ogni sessione di test. Il modulo serve
alle suite di ``libs/shared/firme``, ``services/validation`` e
``services/ciclo-vita`` (stessi documenti firmati, stessa trust anchor locale).

Contenuto:
  - ``Pki.nuova()``: CA radice di prova + firmatario (non ripudio) con punti di
    distribuzione CRL e OCSP fittizi (nessuna rete: le revoche si passano al
    verificatore come «simulate»);
  - ``pki.cades(dati)``: busta CAdES-BES attached (.p7m) con pyHanko;
  - ``pki.pades()``: PDF minimo firmato PAdES;
  - ``pki.crl(revocati)`` e ``pki.ocsp(stato)``: CRL e risposta OCSP firmate
    dalla CA per il certificato del firmatario.
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from asn1crypto import cms as a_cms
from asn1crypto import keys as a_keys
from asn1crypto import x509 as a_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import ocsp
from cryptography.x509.oid import NameOID

URL_CRL = "http://crl.prova.invalid/ca.crl"
URL_OCSP = "http://ocsp.prova.invalid/"


def _nome(cn: str, cf: str | None = None) -> x509.Name:
    attributi = [x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                 x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Prova VerumDoc"),
                 x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if cf:  # revisione X3: serialNumber del soggetto come nei certificati qualificati
        attributi.append(x509.NameAttribute(NameOID.SERIAL_NUMBER, f"TINIT-{cf}"))
    return x509.Name(attributi)


def _esegui(coro):  # type: ignore[no-untyped-def]
    """Esegue una coroutine di pyHanko anche dentro un ciclo asyncio già attivo
    (test asincroni): in un thread con il suo ciclo."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as esecutore:
        return esecutore.submit(asyncio.run, coro).result()


def _a(cert: x509.Certificate) -> a_x509.Certificate:
    return a_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


@dataclass
class Pki:
    ca_chiave: ec.EllipticCurvePrivateKey
    ca: x509.Certificate
    chiave: ec.EllipticCurvePrivateKey
    firmatario: x509.Certificate
    adesso: datetime

    @classmethod
    def nuova(cls, cn: str = "Firmatario di prova", *, adesso: datetime | None = None,
              scaduto: bool = False, cf: str | None = None) -> Pki:
        adesso = adesso or datetime.now(UTC)
        ca_chiave = ec.generate_private_key(ec.SECP256R1())
        ca = (x509.CertificateBuilder().subject_name(_nome("CA di prova VerumDoc"))
              .issuer_name(_nome("CA di prova VerumDoc")).public_key(ca_chiave.public_key())
              .serial_number(x509.random_serial_number())
              .not_valid_before(adesso - timedelta(days=30))
              .not_valid_after(adesso + timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                           key_encipherment=False, data_encipherment=False,
                                           key_agreement=False, key_cert_sign=True,
                                           crl_sign=True, encipher_only=False,
                                           decipher_only=False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_chiave.public_key()),
                             critical=False)
              .sign(ca_chiave, hashes.SHA256()))
        chiave = ec.generate_private_key(ec.SECP256R1())
        fine = adesso - timedelta(days=1) if scaduto else adesso + timedelta(days=365)
        firmatario = (x509.CertificateBuilder().subject_name(_nome(cn, cf))
                      .issuer_name(ca.subject).public_key(chiave.public_key())
                      .serial_number(x509.random_serial_number())
                      .not_valid_before(adesso - timedelta(days=10)).not_valid_after(fine)
                      .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                                     critical=True)
                      .add_extension(x509.KeyUsage(digital_signature=True,
                                                   content_commitment=True,
                                                   key_encipherment=False,
                                                   data_encipherment=False,
                                                   key_agreement=False, key_cert_sign=False,
                                                   crl_sign=False, encipher_only=False,
                                                   decipher_only=False), critical=True)
                      .add_extension(x509.CRLDistributionPoints([x509.DistributionPoint(
                          full_name=[x509.UniformResourceIdentifier(URL_CRL)],
                          relative_name=None, reasons=None, crl_issuer=None)]), critical=False)
                      .add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(
                          x509.oid.AuthorityInformationAccessOID.OCSP,
                          x509.UniformResourceIdentifier(URL_OCSP))]), critical=False)
                      .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                          ca_chiave.public_key()), critical=False)
                      .sign(ca_chiave, hashes.SHA256()))
        return cls(ca_chiave, ca, chiave, firmatario, adesso)

    # ── forme utili ai verificatori ──────────────────────────────────

    @property
    def ca_pem(self) -> bytes:
        return self.ca.public_bytes(serialization.Encoding.PEM)

    @property
    def ca_asn1(self) -> a_x509.Certificate:
        return _a(self.ca)

    def _signer(self):  # type: ignore[no-untyped-def]
        from pyhanko.sign import signers
        from pyhanko_certvalidator.registry import SimpleCertificateStore

        chiave = a_keys.PrivateKeyInfo.load(self.chiave.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        return signers.SimpleSigner(signing_cert=_a(self.firmatario), signing_key=chiave,
                                    cert_registry=SimpleCertificateStore.from_certs(
                                        [self.ca_asn1]))

    # ── documenti firmati ────────────────────────────────────────────

    def cades(self, dati: bytes, *, staccata: bool = False) -> bytes:
        """Busta CAdES-BES attached (DER), come un .p7m; ``staccata``: .p7s."""
        firmata = _esegui(self._signer().async_sign_general_data(
            dati, "sha256", detached=staccata))
        assert isinstance(firmata, a_cms.ContentInfo)
        return bytes(firmata.dump())

    def pades(self, testo: str = "Documento di prova") -> bytes:
        """PDF di una pagina firmato PAdES (pyHanko, campo di firma invisibile)."""
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers

        sorgente = io.BytesIO(pdf_minimo(testo))
        uscita = io.BytesIO()
        meta = signers.PdfSignatureMetadata(field_name="Firma1")
        _esegui(signers.PdfSigner(meta, signer=self._signer()).async_sign_pdf(
            IncrementalPdfFileWriter(sorgente), output=uscita))
        return uscita.getvalue()

    # ── revoche simulate ─────────────────────────────────────────────

    def crl(self, *, revocato: bool = False) -> bytes:
        b = (x509.CertificateRevocationListBuilder().issuer_name(self.ca.subject)
             .last_update(self.adesso - timedelta(hours=1))
             .next_update(self.adesso + timedelta(days=7))
             .add_extension(x509.CRLNumber(1), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                 self.ca_chiave.public_key()), critical=False))
        if revocato:
            b = b.add_revoked_certificate(
                x509.RevokedCertificateBuilder().serial_number(self.firmatario.serial_number)
                .revocation_date(self.adesso - timedelta(days=2)).build())
        return b.sign(self.ca_chiave, hashes.SHA256()).public_bytes(serialization.Encoding.DER)

    def ocsp(self, *, revocato: bool = False) -> bytes:
        stato = ocsp.OCSPCertStatus.REVOKED if revocato else ocsp.OCSPCertStatus.GOOD
        b = ocsp.OCSPResponseBuilder().add_response(
            cert=self.firmatario, issuer=self.ca,
            algorithm=hashes.SHA1(),  # noqa: S303 — CertID dell'OCSP (RFC 6960)
            cert_status=stato,
            this_update=self.adesso - timedelta(minutes=5),
            next_update=self.adesso + timedelta(days=1),
            revocation_time=(self.adesso - timedelta(days=2)) if revocato else None,
            revocation_reason=None).responder_id(ocsp.OCSPResponderEncoding.HASH, self.ca)
        risposta = b.sign(self.ca_chiave, hashes.SHA256())
        return risposta.public_bytes(serialization.Encoding.DER)


def pdf_minimo(testo: str = "Documento di prova") -> bytes:
    """PDF 1.7 di una pagina senza firme (nessuna dipendenza)."""
    flusso = f"BT /F1 12 Tf 72 720 Td ({testo}) Tj ET".encode("latin-1")
    oggetti = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(flusso)).encode() + b" >>\nstream\n" + flusso
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    corpo = io.BytesIO()
    corpo.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    posizioni = []
    for i, o in enumerate(oggetti, start=1):
        posizioni.append(corpo.tell())
        corpo.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = corpo.tell()
    corpo.write(f"xref\n0 {len(oggetti) + 1}\n0000000000 65535 f \n".encode())
    for p in posizioni:
        corpo.write(f"{p:010d} 00000 n \n".encode())
    corpo.write(f"trailer\n<< /Size {len(oggetti) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n"
                "%%EOF\n".encode())
    return corpo.getvalue()


__all__ = ["URL_CRL", "URL_OCSP", "Pki", "pdf_minimo"]
