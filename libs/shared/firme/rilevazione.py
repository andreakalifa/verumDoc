# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/rilevazione.py — riconoscimento del formato di firma e
lettura dei firmatari SENZA verifica (livello «RILEVATA», R08 D01).

Usato da tutti i verificatori per decidere il formato (CAdES, PAdES, XAdES,
ASiC) e, da solo, come verificatore del livello RILEVATA: dice chi dichiara di
aver firmato (soggetto del certificato incluso) ma NON che la firma sia valida
(indicazione INDETERMINATE, sotto-indicazione NON_VERIFICATA).

Sostituisce la «firma rilevata» di ciclo-vita (nome .p7m o «/ByteRange» nel
PDF): una busta che si dichiara CAdES ma non è un SignedData leggibile ora è
riconosciuta come tale (TOTAL_FAILED, FORMAT_FAILURE), non «firmata».
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import zipfile
from collections.abc import Iterable

from asn1crypto import cms, x509

import libs.shared.firme.asn1_etsi  # noqa: F401 — R13a: attributi B-LTA leggibili
from libs.shared.firme.esito import (
    SOTTO_NON_VERIFICATA,
    Certificato,
    EsitoVerifica,
    FirmaVerificata,
    FormatoFirma,
    Indicazione,
    LivelloVerifica,
    codice_fiscale,
    esito_illeggibile,
    esito_non_firmato,
    in_parole,
)
from libs.shared.zip_limiti import MembroZipNonAmmesso, leggi_membro

NOME = "rilevazione"
_OID_SIGNED_DATA = b"\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02"
_PEM = re.compile(rb"-----BEGIN (?:PKCS7|CMS)-----(.+?)-----END (?:PKCS7|CMS)-----", re.S)
_XMLDSIG = (b"http://www.w3.org/2000/09/xmldsig#", b"<ds:Signature", b"<Signature ")
_MIME_ASIC = (b"application/vnd.etsi.asic-e+zip", b"application/vnd.etsi.asic-s+zip")


class BustaNonValida(ValueError):
    """Il contenuto si presenta come busta CAdES ma non è decodificabile."""


def _sembra_der_cms(dati: bytes) -> bool:
    return dati[:1] == b"\x30" and _OID_SIGNED_DATA in dati[:64]


def decodifica_cms(contenuto: bytes) -> bytes:
    """DER di una busta CMS scritta in DER, PEM o base64 (forme dei .p7m in uso)."""
    if _sembra_der_cms(contenuto):
        return contenuto
    trovato = _PEM.search(contenuto[:64 * 1024 * 1024])
    testo = trovato.group(1) if trovato else contenuto
    try:
        der = base64.b64decode(b"".join(testo.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BustaNonValida("busta CMS non decodificabile") from exc
    if not _sembra_der_cms(der):
        raise BustaNonValida("il contenuto non è una busta CMS SignedData")
    return der


#: X4: il membro «mimetype» di un ASiC è il solo tipo MIME (ETSI EN 319 162-1)
MIMETYPE_MAX = 256
#: X4: numero massimo di membri esaminati (elenco centrale)
MEMBRI_MAX = 10_000


def _sembra_asic(contenuto: bytes) -> bool:
    """ASiC: ZIP con «mimetype» ASiC o con una firma in META-INF. Nessun membro
    si decomprime oltre ``MIMETYPE_MAX`` byte (revisione X4: zip bomb)."""
    if not contenuto.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(contenuto)) as z:
            elenco = z.infolist()
            if len(elenco) > MEMBRI_MAX:
                return False
            nomi = {i.filename for i in elenco}
            if "mimetype" in nomi:
                try:
                    # Y1: il mimetype ASiC è sempre STORED (ETSI EN 319 162-1)
                    tipo = leggi_membro(z, "mimetype", massimo_byte=MIMETYPE_MAX,
                                        solo_stored=True)
                except MembroZipNonAmmesso:
                    tipo = b""
                if tipo.strip() in _MIME_ASIC:
                    return True
            return any(n.startswith("META-INF/") and "signature" in n.lower() for n in nomi)
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, NotImplementedError,
            EOFError, ValueError):
        return False


def rileva_formato_firma(contenuto: bytes, nome_file: str = "") -> FormatoFirma:
    """Formato della firma dal CONTENUTO (il nome conta solo per il .p7m/.p7s
    in base64, che altrimenti non si distingue da un testo)."""
    testa = contenuto[:8]
    if testa.startswith(b"%PDF-"):
        return FormatoFirma.PADES if b"/ByteRange" in contenuto else FormatoFirma.NESSUNO
    if _sembra_der_cms(contenuto) or _PEM.search(contenuto[:4096]):
        return FormatoFirma.CADES
    if _sembra_asic(contenuto):
        return FormatoFirma.ASIC
    inizio = contenuto[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
    if inizio.startswith(b"<") and any(s in contenuto for s in _XMLDSIG):
        return FormatoFirma.XADES
    if nome_file.lower().endswith((".p7m", ".p7s")):
        return FormatoFirma.CADES  # base64: si prova a decodificare (o è illeggibile)
    return FormatoFirma.NESSUNO


#: Byte letti all'inizio e alla fine di un file oltre i limiti della verifica.
PARZIALE_BYTE = 1024 * 1024


def rileva_formato_firma_parziale(testa: bytes, coda: bytes, nome_file: str = "") -> FormatoFirma:
    """Formato della firma dai SOLI primi e ultimi byte (revisione X5: file oltre
    il limite della verifica). Limite dichiarato: un PDF firmato a cui dopo la
    firma sono stati aggiunti più di ``PARZIALE_BYTE`` byte in aggiornamenti
    incrementali risulta non firmato."""
    if testa.startswith(b"%PDF-"):
        return (FormatoFirma.PADES if b"/ByteRange" in testa or b"/ByteRange" in coda
                else FormatoFirma.NESSUNO)
    if _sembra_der_cms(testa) or _PEM.search(testa[:4096]):
        return FormatoFirma.CADES
    if testa.startswith(b"PK\x03\x04"):
        # ASiC: primo membro «mimetype» non compresso, oppure firma in META-INF
        # nell'elenco centrale (in coda)
        if testa[30:38] == b"mimetype" and any(m in testa[38:38 + 64] for m in _MIME_ASIC):
            return FormatoFirma.ASIC
        if b"META-INF/" in coda and b"signature" in coda.lower():
            return FormatoFirma.ASIC
        return FormatoFirma.NESSUNO
    inizio = testa[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
    if inizio.startswith(b"<") and any(s in testa or s in coda for s in _XMLDSIG):
        return FormatoFirma.XADES
    if nome_file.lower().endswith((".p7m", ".p7s")):
        return FormatoFirma.CADES
    return FormatoFirma.NESSUNO


MARCATORE_PADES = b"/ByteRange"


def cerca_in_blocchi(blocchi: Iterable[bytes], marcatore: bytes = MARCATORE_PADES) -> bool:
    """Revisione Y3: il marcatore compare nel flusso (blocchi consecutivi, con
    sovrapposizione: un marcatore a cavallo di due blocchi si trova). Memoria
    costante, costo lineare."""
    coda = b""
    for blocco in blocchi:
        if marcatore in coda + blocco[:len(marcatore)] or marcatore in blocco:
            return True
        coda = blocco[-(len(marcatore) - 1):] if len(blocco) >= len(marcatore) - 1 \
            else (coda + blocco)[-(len(marcatore) - 1):]
    return False


def certificato(cert: x509.Certificate) -> Certificato:
    """Vista normalizzata di un certificato asn1crypto."""
    validita = cert["tbs_certificate"]["validity"]
    return Certificato(
        soggetto=cert.subject.human_friendly, emittente=cert.issuer.human_friendly,
        seriale=format(cert.serial_number, "x"),
        valido_dal=validita["not_before"].native.isoformat(),
        valido_al=validita["not_after"].native.isoformat(),
        impronta_sha256=hashlib.sha256(cert.dump()).hexdigest(),
        codice_fiscale=codice_fiscale(cert.subject.native.get("serial_number")))


def nome_firmatario(cert: x509.Certificate) -> str:
    nativo = cert.subject.native
    return str(nativo.get("common_name") or cert.subject.human_friendly)


def _firmatari_cms(sd: cms.SignedData) -> Iterable[tuple[x509.Certificate | None, str | None]]:
    certificati = [c.chosen for c in sd["certificates"] or () if c.name == "certificate"]
    for si in sd["signer_infos"]:
        sid = si["sid"]
        trovato = None
        for c in certificati:
            if sid.name == "issuer_and_serial_number":
                if (c.issuer == sid.chosen["issuer"]
                        and c.serial_number == sid.chosen["serial_number"].native):
                    trovato = c
            elif c.key_identifier == sid.chosen.native:
                trovato = c
        data = None
        for attr in si["signed_attrs"] or ():
            if attr["type"].native == "signing_time":
                data = attr["values"][0].native.isoformat()
        yield trovato, data


def rileva(contenuto: bytes, nome_file: str = "", *,
           verificato_il: str | None = None) -> EsitoVerifica:
    """Esito del livello RILEVATA: formato e firmatari dichiarati, nessuna verifica."""
    formato = rileva_formato_firma(contenuto, nome_file)
    livello = LivelloVerifica.RILEVATA
    if formato is FormatoFirma.NESSUNO:
        return esito_non_firmato(livello, NOME, verificato_il)
    firme: list[FirmaVerificata] = []
    if formato is FormatoFirma.CADES:
        try:
            ci = cms.ContentInfo.load(decodifica_cms(contenuto))
            if ci["content_type"].native != "signed_data":
                raise BustaNonValida("non è un SignedData")
            sd = ci["content"]
            for cert, data in _firmatari_cms(sd):
                firme.append(_firma_rilevata(formato, cert, data))
        except Exception as exc:  # noqa: BLE001 — busta illeggibile = TOTAL_FAILED
            return esito_illeggibile(formato, livello, NOME, type(exc).__name__, verificato_il)
    elif formato is FormatoFirma.PADES:
        try:
            from pyhanko.pdf_utils.reader import PdfFileReader

            lettore = PdfFileReader(io.BytesIO(contenuto), strict=False)
            for emb in lettore.embedded_signatures:
                data = emb.self_reported_timestamp
                firme.append(_firma_rilevata(formato, emb.signer_cert,
                                             data.isoformat() if data else None))
        except Exception as exc:  # noqa: BLE001
            return esito_illeggibile(formato, livello, NOME, type(exc).__name__, verificato_il)
    else:
        firme.append(FirmaVerificata(formato, Indicazione.INDETERMINATE,
                                     sottoindicazione=SOTTO_NON_VERIFICATA,
                                     motivi=(in_parole(SOTTO_NON_VERIFICATA) or "",)))
    if not firme:
        return esito_illeggibile(formato, livello, NOME, "nessuna firma leggibile",
                                 verificato_il)
    return EsitoVerifica(formato, livello, NOME, tuple(firme), verificato_il=verificato_il)


def _firma_rilevata(formato: FormatoFirma, cert: x509.Certificate | None,
                    data: str | None) -> FirmaVerificata:
    return FirmaVerificata(
        formato, Indicazione.INDETERMINATE,
        firmatario=nome_firmatario(cert) if cert is not None else None,
        certificato=certificato(cert) if cert is not None else None,
        data_firma=data, sottoindicazione=SOTTO_NON_VERIFICATA,
        motivi=(in_parole(SOTTO_NON_VERIFICATA) or "",))


class RilevatoreFirme:
    """Verificatore del solo livello RILEVATA (nessuna crittografia)."""

    nome = NOME
    formati = frozenset({FormatoFirma.CADES, FormatoFirma.PADES, FormatoFirma.XADES,
                         FormatoFirma.ASIC})

    async def verifica(self, contenuto: bytes, *, livello: LivelloVerifica,
                       nome_file: str = "", originale: bytes | None = None,
                       verificato_il: str | None = None) -> EsitoVerifica:
        return rileva(contenuto, nome_file, verificato_il=verificato_il)


__all__ = ["PARZIALE_BYTE", "BustaNonValida", "RilevatoreFirme", "certificato", "decodifica_cms",
           "MARCATORE_PADES", "cerca_in_blocchi", "nome_firmatario", "rileva",
           "rileva_formato_firma", "rileva_formato_firma_parziale"]
