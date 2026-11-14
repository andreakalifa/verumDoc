# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica delle firme S/MIME delle ricevute PEC (R13b, VD-C-078).

Riferimenti: DM 2/11/2005 (regole tecniche PEC, art. 9 e allegato: ricevute e
avvisi firmati dal gestore con la sua chiave, formato S/MIME); RFC 1847
(multipart/signed), RFC 5751 (S/MIME 3.2), RFC 5652 (CMS SignedData).

Una ricevuta è AUTENTICA solo se:
  1. è ``multipart/signed`` con esattamente due parti (contenuto e firma CMS
     staccata ``application/(x-)pkcs7-signature``);
  2. la firma CMS ha UN firmatario, attributi firmati con ``content-type = data``
     e ``message-digest`` uguale all'impronta dei BYTE della prima parte (così le
     informazioni lette da ``daticert.xml`` sono quelle firmate);
  3. la firma sugli attributi è valida con la chiave pubblica del certificato del
     firmatario (RSA PKCS#1 v1.5, RSA-PSS, ECDSA; impronte SHA-256/384/512);
  4. il certificato del firmatario discende, con firme valide e nel periodo di
     validità all'istante della firma, da una delle RADICI del canale (certificati
     dei gestori PEC, ``radici_pem``) — un certificato fra le radici vale anche da
     solo (impronta fissata).
Le intestazioni esterne del messaggio (``X-Ricevuta``, ``X-Riferimento-Message-ID``,
oggetto) NON sono firmate: ``libs.shared.pec.ricevute`` le confronta con i dati
firmati e decide solo su quelli.

Ogni difetto è ``RicevutaNonAutentica`` con un motivo in parole semplici (mai il
contenuto del messaggio).
"""
from __future__ import annotations

import base64
import email.parser
import email.policy
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from asn1crypto import cms
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding

_DER = Encoding.DER

#: Algoritmi d'impronta ammessi (SHA-1 rifiutato)
IMPRONTE_AMMESSE: dict[str, type[hashes.HashAlgorithm]] = {
    "sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512,
}
PROTOCOLLI_FIRMA = ("application/pkcs7-signature", "application/x-pkcs7-signature")
PROFONDITA_MASSIMA = 5
FIRMA_MASSIMA_BYTE = 256 * 1024


class RicevutaNonAutentica(ValueError):
    """La ricevuta non è firmata, la firma non è valida o non viene da un gestore
    delle radici configurate."""


@dataclass(frozen=True)
class FirmaVerificata:
    certificato_sha256: str
    soggetto: str
    emittente: str
    istante_firma: datetime | None


@dataclass(frozen=True)
class PartiFirmate:
    """Le due parti di un ``multipart/signed``: i byte firmati (con le
    intestazioni MIME della parte, righe CRLF) e la firma CMS in DER."""

    contenuto: bytes
    firma_der: bytes
    protocollo: str


def _crlf(dati: bytes) -> bytes:
    return dati.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")


def separa_multipart_signed(grezzo: bytes) -> PartiFirmate:
    """Prima parte (byte esatti, forma canonica CRLF) e firma di un messaggio
    ``multipart/signed``. ``RicevutaNonAutentica`` se il messaggio non lo è."""
    testo = _crlf(grezzo)
    fine_testa = testo.find(b"\r\n\r\n")
    if fine_testa < 0:
        raise RicevutaNonAutentica("messaggio senza corpo")
    testa = email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(
        testo[:fine_testa + 4])
    if testa.get_content_type() != "multipart/signed":
        raise RicevutaNonAutentica("ricevuta non firmata (atteso multipart/signed)")
    protocollo = str(testa.get_param("protocol") or "").lower()
    if protocollo not in PROTOCOLLI_FIRMA:
        raise RicevutaNonAutentica("firma non S/MIME (protocollo non ammesso)")
    confine = testa.get_param("boundary")
    if not isinstance(confine, str) or not confine or len(confine) > 200:
        raise RicevutaNonAutentica("multipart/signed senza boundary")
    corpo = b"\r\n" + testo[fine_testa + 4:]
    delimitatore = b"\r\n--" + confine.encode("ascii", "strict")
    pezzi = corpo.split(delimitatore)
    # pezzi[0] = preambolo; poi parte, parte, «--» finale (epilogo)
    parti: list[bytes] = []
    chiuso = False
    for pezzo in pezzi[1:]:
        if pezzo.startswith(b"--"):
            chiuso = True
            break
        # dopo il delimitatore: spazi facoltativi, poi CRLF
        riga, sep, resto = pezzo.partition(b"\r\n")
        if not sep or riga.strip(b" \t"):
            raise RicevutaNonAutentica("multipart/signed malformato")
        parti.append(resto)
    if not chiuso or len(parti) != 2:
        raise RicevutaNonAutentica("multipart/signed con un numero di parti diverso da 2")
    contenuto, firma = parti
    fine_firma = firma.find(b"\r\n\r\n")
    if fine_firma < 0:
        raise RicevutaNonAutentica("parte della firma senza corpo")
    testa_firma = email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(
        firma[:fine_firma + 4])
    if testa_firma.get_content_type() not in PROTOCOLLI_FIRMA:
        raise RicevutaNonAutentica("seconda parte non è una firma S/MIME")
    codifica = str(testa_firma.get("Content-Transfer-Encoding") or "base64").strip().lower()
    dati = firma[fine_firma + 4:]
    if len(dati) > FIRMA_MASSIMA_BYTE * 2:
        raise RicevutaNonAutentica("firma troppo grande")
    try:
        der = base64.b64decode(b"".join(dati.split()), validate=True) \
            if codifica == "base64" else dati
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        raise RicevutaNonAutentica("firma non decodificabile") from None
    return PartiFirmate(contenuto=contenuto, firma_der=der, protocollo=protocollo)


def _nome_impronta(algoritmo: str) -> str:
    nome = algoritmo.lower().replace("-", "")
    if nome not in IMPRONTE_AMMESSE:
        raise RicevutaNonAutentica(f"algoritmo d'impronta non ammesso: {algoritmo}")
    return nome


def _verifica_firma_grezza(cert: x509.Certificate, firma: bytes, dati: bytes,
                           algoritmo_firma: str, impronta: str,
                           parametri_pss: object | None) -> None:
    chiave = cert.public_key()
    h = IMPRONTE_AMMESSE[impronta]()
    try:
        if isinstance(chiave, rsa.RSAPublicKey):
            if algoritmo_firma == "rsassa_pss":
                lunghezza_sale = padding.PSS.DIGEST_LENGTH
                if parametri_pss is not None:
                    lunghezza_sale = int(parametri_pss["salt_length"].native)  # type: ignore[index]
                chiave.verify(firma, dati, padding.PSS(mgf=padding.MGF1(h),
                                                       salt_length=lunghezza_sale), h)
            elif algoritmo_firma in ("rsassa_pkcs1v15", "rsa", "sha256_rsa", "sha384_rsa",
                                     "sha512_rsa"):
                chiave.verify(firma, dati, padding.PKCS1v15(), h)
            else:
                raise RicevutaNonAutentica(f"algoritmo di firma non ammesso: {algoritmo_firma}")
        elif isinstance(chiave, ec.EllipticCurvePublicKey):
            if not algoritmo_firma.startswith(("ecdsa", "sha256_ecdsa", "sha384_ecdsa",
                                               "sha512_ecdsa")):
                raise RicevutaNonAutentica(f"algoritmo di firma non ammesso: {algoritmo_firma}")
            chiave.verify(firma, dati, ec.ECDSA(h))
        else:
            raise RicevutaNonAutentica("tipo di chiave del firmatario non ammesso")
    except InvalidSignature:
        raise RicevutaNonAutentica("firma della ricevuta non valida") from None


def _valido_al(cert: x509.Certificate, istante: datetime) -> bool:
    return cert.not_valid_before_utc <= istante <= cert.not_valid_after_utc


def _catena(firmatario: x509.Certificate, intermedi: list[x509.Certificate],
            radici: list[x509.Certificate], istante: datetime) -> None:
    """Il firmatario discende da una radice (o è una radice) con firme valide e
    certificati validi all'istante della firma."""
    der_radici = {r.public_bytes(_DER) for r in radici}
    attuale = firmatario
    for _ in range(PROFONDITA_MASSIMA):
        if not _valido_al(attuale, istante):
            raise RicevutaNonAutentica("certificato della catena fuori dal periodo di validità")
        if attuale.public_bytes(_DER) in der_radici:
            return
        candidati = [c for c in (*radici, *intermedi)
                     if c.subject == attuale.issuer and c is not attuale]
        emittente = None
        for c in candidati:
            try:
                attuale.verify_directly_issued_by(c)
            except (ValueError, TypeError, InvalidSignature):
                continue
            emittente = c
            break
        if emittente is None:
            raise RicevutaNonAutentica("certificato del firmatario non emesso da un gestore "
                                       "PEC fra le radici del canale")
        try:
            vincoli = emittente.extensions.get_extension_for_class(x509.BasicConstraints).value
            if not vincoli.ca:
                raise RicevutaNonAutentica("emittente del certificato non è una CA")
        except x509.ExtensionNotFound:
            if emittente.public_bytes(_DER) not in der_radici:
                raise RicevutaNonAutentica("intermedio senza BasicConstraints") from None
        if emittente.public_bytes(_DER) in der_radici:
            if not _valido_al(emittente, istante):
                raise RicevutaNonAutentica("radice fuori dal periodo di validità")
            return
        attuale = emittente
    raise RicevutaNonAutentica("catena dei certificati troppo lunga")


def verifica_firma(contenuto: bytes, firma_der: bytes, radici: list[x509.Certificate], *,
                   adesso: datetime | None = None) -> FirmaVerificata:
    """Verifica la firma CMS staccata su ``contenuto`` (byte esatti della prima
    parte). Solleva ``RicevutaNonAutentica``."""
    if not radici:
        raise RicevutaNonAutentica("nessuna radice dei gestori PEC configurata nel canale")
    if len(firma_der) > FIRMA_MASSIMA_BYTE:
        raise RicevutaNonAutentica("firma troppo grande")
    try:
        info = cms.ContentInfo.load(firma_der, strict=True)
        if info["content_type"].native != "signed_data":
            raise RicevutaNonAutentica("la firma non è un CMS SignedData")
        sd = info["content"]
        if sd["encap_content_info"]["content"].native is not None:
            raise RicevutaNonAutentica("firma non staccata (contenuto incapsulato)")
        firmatari = list(sd["signer_infos"])
        certificati = [x509.load_der_x509_certificate(c.chosen.dump())
                       for c in (sd["certificates"] or []) if c.name == "certificate"]
    except RicevutaNonAutentica:
        raise
    except Exception:  # noqa: BLE001 — struttura ASN.1 non valida
        raise RicevutaNonAutentica("firma CMS non leggibile") from None
    if len(firmatari) != 1:
        raise RicevutaNonAutentica("la ricevuta deve avere un solo firmatario")
    si = firmatari[0]
    sid = si["sid"]
    firmatario: x509.Certificate | None = None
    for c in certificati:
        if sid.name == "issuer_and_serial_number":
            if c.serial_number == sid.chosen["serial_number"].native and \
                    c.issuer.public_bytes() == sid.chosen["issuer"].dump():
                firmatario = c
                break
        else:
            try:
                ski = c.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
            except x509.ExtensionNotFound:
                continue
            if ski == sid.chosen.native:
                firmatario = c
                break
    if firmatario is None:
        raise RicevutaNonAutentica("certificato del firmatario assente dalla firma")
    impronta = _nome_impronta(si["digest_algorithm"]["algorithm"].native)
    attributi = si["signed_attrs"]
    if not attributi or attributi.native is None:
        raise RicevutaNonAutentica("firma senza attributi firmati")
    valori: dict[str, list[object]] = {}
    for a in attributi:
        valori.setdefault(a["type"].native, []).extend(a["values"].native)
    if valori.get("content_type") != ["data"]:
        raise RicevutaNonAutentica("attributo content-type assente o diverso da data")
    digest = valori.get("message_digest") or []
    atteso = hashlib.new(impronta, contenuto).digest()
    if len(digest) != 1 or digest[0] != atteso:
        raise RicevutaNonAutentica("impronta del contenuto diversa da quella firmata")
    istante: datetime | None = None
    tempi = valori.get("signing_time") or []
    if len(tempi) == 1 and isinstance(tempi[0], datetime):
        istante = tempi[0].astimezone(UTC)
    algoritmo_firma = si["signature_algorithm"]["algorithm"].native
    parametri = si["signature_algorithm"]["parameters"] \
        if algoritmo_firma == "rsassa_pss" else None
    dati_firmati = b"\x31" + attributi.dump()[1:]
    _verifica_firma_grezza(firmatario, si["signature"].native, dati_firmati,
                           str(algoritmo_firma), impronta, parametri)
    riferimento = istante or adesso or datetime.now(UTC)
    _catena(firmatario, certificati, radici, riferimento)
    return FirmaVerificata(
        certificato_sha256=hashlib.sha256(firmatario.public_bytes(_DER)).hexdigest(),
        soggetto=firmatario.subject.rfc4514_string()[:500],
        emittente=firmatario.issuer.rfc4514_string()[:500],
        istante_firma=istante)


def radici_da_pem(testo: str) -> list[x509.Certificate]:
    from libs.shared.firma_qualificata.configurazione import certificati_pem

    return [x509.load_der_x509_certificate(d) for d in certificati_pem(testo or "")]


__all__ = ["FirmaVerificata", "PartiFirmate", "RicevutaNonAutentica", "radici_da_pem",
           "separa_multipart_signed", "verifica_firma"]
