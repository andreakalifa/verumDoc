# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Gestore PEC di prova: certificati generati a runtime e ricevute S/MIME firmate
(R13b, VD-C-078). SOLO collaudo e test: mai in produzione (il canale SVILUPPO è
rifiutato dalla console, dal lavoratore e dalla costruzione dell'adattatore).

Le ricevute seguono la struttura delle regole tecniche (DM 2/11/2005): intestazioni
``X-Ricevuta`` e ``X-Riferimento-Message-ID``, ``multipart/signed`` con una parte
``multipart/mixed`` (testo e ``daticert.xml``) firmata in CMS staccato dal
certificato del gestore. ``Pki`` può anche produrre un secondo gestore «estraneo»
(radice diversa) e ricevute non firmate o manomesse, per le prove di autenticità.
"""
from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, make_msgid
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

TIPI_X_RICEVUTA = {
    "ACCETTAZIONE": "accettazione", "NON_ACCETTAZIONE": "non-accettazione",
    "PRESA_IN_CARICO": "presa-in-carico", "AVVENUTA_CONSEGNA": "avvenuta-consegna",
    "ERRORE_CONSEGNA": "errore-consegna",
    "PREAVVISO_ERRORE_CONSEGNA": "preavviso-errore-consegna",
}
OGGETTI = {
    "ACCETTAZIONE": "ACCETTAZIONE", "NON_ACCETTAZIONE": "AVVISO DI NON ACCETTAZIONE",
    "PRESA_IN_CARICO": "PRESA IN CARICO", "AVVENUTA_CONSEGNA": "CONSEGNA",
    "ERRORE_CONSEGNA": "AVVISO DI MANCATA CONSEGNA",
    "PREAVVISO_ERRORE_CONSEGNA": "AVVISO DI MANCATA CONSEGNA PER SUP. TEMPO MASSIMO",
}


def _nome(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Gestore PEC di prova"),
                      x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _certificato(soggetto: x509.Name, chiave_pubblica: object, emittente: x509.Name,
                 chiave_emittente: ec.EllipticCurvePrivateKey, *, ca: bool,
                 inizio: datetime, fine: datetime) -> x509.Certificate:
    b = (x509.CertificateBuilder().subject_name(soggetto).issuer_name(emittente)
         .public_key(chiave_pubblica)  # type: ignore[arg-type]
         .serial_number(x509.random_serial_number())
         .not_valid_before(inizio).not_valid_after(fine)
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(  # type: ignore[arg-type]
             chiave_pubblica),
                        critical=False))
    if not ca:
        b = b.add_extension(x509.KeyUsage(True, True, False, False, False, False, False,
                                          False, False), critical=True)
    return b.sign(chiave_emittente, hashes.SHA256())


@dataclass
class Pki:
    """CA del gestore e certificato di firma delle ricevute (dominio del gestore)."""

    dominio: str = "pec.gestore-prova.test"
    ca_chiave: ec.EllipticCurvePrivateKey = field(init=False)
    ca: x509.Certificate = field(init=False)
    chiave: ec.EllipticCurvePrivateKey = field(init=False)
    certificato: x509.Certificate = field(init=False)
    scaduto: bool = False

    def __post_init__(self) -> None:
        adesso = datetime.now(UTC)
        self.ca_chiave = ec.generate_private_key(ec.SECP256R1())
        nome_ca = _nome(f"CA {self.dominio}")
        self.ca = _certificato(nome_ca, self.ca_chiave.public_key(), nome_ca, self.ca_chiave,
                               ca=True, inizio=adesso - timedelta(days=1),
                               fine=adesso + timedelta(days=365))
        self.chiave = ec.generate_private_key(ec.SECP256R1())
        fine = adesso - timedelta(hours=1) if self.scaduto else adesso + timedelta(days=30)
        self.certificato = _certificato(_nome(f"posta-certificata@{self.dominio}"),
                                        self.chiave.public_key(), nome_ca, self.ca_chiave,
                                        ca=False, inizio=adesso - timedelta(days=1), fine=fine)

    @property
    def radice_pem(self) -> str:
        return self.ca.public_bytes(serialization.Encoding.PEM).decode("ascii")

    def firma_staccata(self, contenuto: bytes) -> bytes:
        return (pkcs7.PKCS7SignatureBuilder().set_data(contenuto)
                .add_signer(self.certificato, self.chiave, hashes.SHA256())
                .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature,
                                                   pkcs7.PKCS7Options.Binary]))


def daticert(tipo: str, *, msgid: str, mittente: str, destinatari: list[str], oggetto: str,
             gestore: str, consegna: str = "", errore: str = "nessuno",
             errore_esteso: str = "", tipo_ricevuta: str = "completa",
             quando: datetime | None = None) -> bytes:
    quando = (quando or datetime.now(UTC)).astimezone()
    zona = quando.strftime("%z") or "+0000"
    righe = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE postacert SYSTEM "Postacert.dtd">',
        f'<postacert tipo="{TIPI_X_RICEVUTA.get(tipo, "posta-certificata")}" '
        f'errore="{escape(errore)}">',
        "<intestazione>",
        f"<mittente>{escape(mittente)}</mittente>",
        *[f'<destinatari tipo="certificato">{escape(d)}</destinatari>' for d in destinatari],
        f"<risposte>{escape(mittente)}</risposte>",
        f"<oggetto>{escape(oggetto)}</oggetto>",
        "</intestazione>",
        "<dati>",
        f"<gestore-emittente>{escape(gestore)}</gestore-emittente>",
        f'<data '
        f'zona="{zona}"><giorno>{quando:%d/%m/%Y}</giorno><ora>{quando:%H:%M:%S}</ora></data>',
        f"<identificativo>opec{secrets.token_hex(6)}@{escape(gestore)}</identificativo>",
        f"<msgid>{escape(msgid)}</msgid>",
    ]
    if tipo == "AVVENUTA_CONSEGNA":
        righe.append(f'<ricevuta tipo="{escape(tipo_ricevuta)}" />')
    if consegna:
        righe.append(f"<consegna>{escape(consegna)}</consegna>")
    if errore_esteso:
        righe.append(f"<errore-esteso>{escape(errore_esteso)}</errore-esteso>")
    righe += ["</dati>", "</postacert>"]
    return "\r\n".join(righe).encode("utf-8")


def _parte_interna(testo: str, xml: bytes) -> bytes:
    confine = "interno-" + secrets.token_hex(8)
    corpo_xml = base64.encodebytes(xml).decode("ascii").replace("\n", "\r\n")
    return ("Content-Type: multipart/mixed; boundary=\"" + confine + "\"\r\n\r\n"
            f"--{confine}\r\nContent-Type: text/plain; charset=UTF-8\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n" + testo + "\r\n"
            f"--{confine}\r\nContent-Type: application/xml; name=\"daticert.xml\"\r\n"
            "Content-Disposition: inline; filename=\"daticert.xml\"\r\n"
            "Content-Transfer-Encoding: base64\r\n\r\n" + corpo_xml +
            f"--{confine}--\r\n").encode("utf-8")


def messaggio_firmato(pki: Pki | None, *, intestazioni: dict[str, str], testo: str,
                      xml: bytes, manomesso: bool = False) -> bytes:
    """Messaggio ``multipart/signed`` (o non firmato se ``pki`` è None)."""
    interna = _parte_interna(testo, xml)
    testa = "".join(f"{k}: {v}\r\n" for k, v in intestazioni.items())
    if pki is None:
        return (testa + "MIME-Version: 1.0\r\n").encode("utf-8") + interna
    firma = base64.encodebytes(pki.firma_staccata(interna)).decode("ascii").replace("\n", "\r\n")
    if manomesso:
        interna = interna.replace(b"Ricevuta di", b"Ricevuta DI", 1)
    confine = "firmato-" + secrets.token_hex(8)
    return ((testa + "MIME-Version: 1.0\r\n"
             f"Content-Type: multipart/signed; protocol=\"application/pkcs7-signature\"; "
             f"micalg=sha-256; boundary=\"{confine}\"\r\n\r\n"
             "Questo e' un messaggio S/MIME firmato.\r\n"
             f"--{confine}\r\n").encode("utf-8") + interna
            + (f"\r\n--{confine}\r\nContent-Type: application/pkcs7-signature; "
               "name=\"smime.p7s\"\r\nContent-Transfer-Encoding: base64\r\n"
               "Content-Disposition: attachment; filename=\"smime.p7s\"\r\n\r\n"
               + firma + f"\r\n--{confine}--\r\n").encode("utf-8"))


def ricevuta(pki: Pki | None, tipo: str, *, msgid: str, mittente: str, destinatari: list[str],
             oggetto: str, consegna: str = "", errore: str = "nessuno",
             errore_esteso: str = "", x_ricevuta: str | None = None,
             x_riferimento: str | None = None, manomessa: bool = False,
             dominio: str | None = None) -> bytes:
    """Ricevuta PEC come la produce un gestore (firmata da ``pki``)."""
    dominio = dominio or (pki.dominio if pki else "pec.gestore-prova.test")
    quando = datetime.now(UTC)
    xml = daticert(tipo, msgid=msgid, mittente=mittente, destinatari=destinatari,
                   oggetto=oggetto, gestore=dominio, consegna=consegna, errore=errore,
                   errore_esteso=errore_esteso, quando=quando)
    intestazioni = {
        "Return-Path": f"<posta-certificata@{dominio}>",
        "From": f"posta-certificata@{dominio}",
        "To": mittente,
        "Subject": f"{OGGETTI.get(tipo, tipo)}: {oggetto}",
        "Date": format_datetime(quando),
        "Message-ID": make_msgid(domain=dominio),
        "X-Ricevuta": x_ricevuta if x_ricevuta is not None else TIPI_X_RICEVUTA.get(tipo, ""),
        "X-Riferimento-Message-ID": x_riferimento if x_riferimento is not None else msgid,
    }
    if tipo == "AVVENUTA_CONSEGNA":
        intestazioni["X-TipoRicevuta"] = "completa"
    testo = (f"Ricevuta di {OGGETTI.get(tipo, tipo).lower()} del messaggio «{oggetto}» "
             f"inviato da {mittente}" + (f" a {consegna}" if consegna else "") + ".")
    return messaggio_firmato(pki, intestazioni=intestazioni, testo=testo, xml=xml,
                             manomesso=manomessa)


__all__ = ["Pki", "daticert", "messaggio_firmato", "ricevuta"]
