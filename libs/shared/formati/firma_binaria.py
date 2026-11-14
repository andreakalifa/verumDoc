# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/formati/firma_binaria.py — riconoscimento del tipo reale di un file
dalla firma binaria (magic number). Spostato da
``services/gateway/app/services/formato_reale.py`` in R08 (D02): ora è il
RIPIEGO del riconoscimento PRONOM (Siegfried, ``siegfried.py``) e resta il
controllo rapido del gateway sulla TESTA del file durante il caricamento.

Riferimenti: VD-C-017, VD-C-074; REGOLE_SVILUPPO §8.15 (il formato si
identifica dal contenuto, mai dall'estensione o dal MIME dichiarato).

Lavora sui primi ``BYTE_TESTA`` byte: respinge gli eseguibili rinominati e i
file il cui contenuto non corrisponde al formato dichiarato. Non distingue le
versioni (PDF/A, TIFF/EP…): per quelle serve l'identificazione PRONOM
(validazione) e, per i PDF/A, veraPDF (``pdfa.py``).
"""
from __future__ import annotations

from dataclasses import dataclass

#: Byte iniziali necessari al riconoscimento (il campione più lungo usato sotto).
BYTE_TESTA = 4096

# Firme di eseguibili e script: sempre respinti, qualunque sia il nome
_FIRME_ESEGUIBILI: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "eseguibile Windows (PE/DOS)"),
    (b"\x7fELF", "eseguibile ELF"),
    (b"\xfe\xed\xfa\xce", "eseguibile Mach-O"),
    (b"\xfe\xed\xfa\xcf", "eseguibile Mach-O"),
    (b"\xce\xfa\xed\xfe", "eseguibile Mach-O"),
    (b"\xcf\xfa\xed\xfe", "eseguibile Mach-O"),
    (b"\xca\xfe\xba\xbe", "eseguibile Mach-O universale / classe Java"),
    (b"#!", "script con interprete"),
    (b"\x00asm", "modulo WebAssembly"),
    (b"dex\n", "eseguibile Android DEX"),
)

# Formati riconosciuti → MIME canonico
_FIRME_FORMATI: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"-----BEGIN PKCS7-----", "application/pkcs7-mime"),
)

# MIME dichiarati che corrispondono allo stesso formato reale
_EQUIVALENTI: dict[str, str] = {
    "text/xml": "application/xml",
    "application/x-pkcs7-mime": "application/pkcs7-mime",
    "image/jpg": "image/jpeg",
    "image/tif": "image/tiff",
}


@dataclass(frozen=True)
class FormatoRilevato:
    """Esito del riconoscimento: MIME reale (o None) ed eventuale eseguibile."""
    mime: str | None
    eseguibile: str | None = None


def _sembra_der_pkcs7(testa: bytes) -> bool:
    # SEQUENCE DER (0x30) con lunghezza lunga o indefinita seguita dall'OID
    # 1.2.840.113549.1.7.2 (signedData) nei primi byte
    return testa[:1] == b"\x30" and b"\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02" in testa[:64]


def _sembra_xml(testa: bytes) -> bool:
    inizio = testa[:512].lstrip(b"\xef\xbb\xbf").lstrip()
    return inizio.startswith(b"<?xml") or (
        inizio.startswith(b"<") and not inizio[:2].startswith(b"<!") and b">" in inizio
    )


def _sembra_testo(testa: bytes) -> bool:
    campione = testa[:BYTE_TESTA]
    if b"\x00" in campione:
        return False
    try:
        campione.decode("utf-8")
    except UnicodeDecodeError:
        # campione tagliato a metà di un carattere multibyte
        try:
            campione[:-3].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def rileva_formato(testa: bytes) -> FormatoRilevato:
    """Riconosce il formato dai primi ``BYTE_TESTA`` byte del file (il resto è ignorato)."""
    testa = testa[:BYTE_TESTA]
    for firma, descrizione in _FIRME_ESEGUIBILI:
        if testa.startswith(firma):
            return FormatoRilevato(None, descrizione)
    for firma, mime in _FIRME_FORMATI:
        if testa.startswith(firma):
            return FormatoRilevato(mime)
    if _sembra_der_pkcs7(testa):
        return FormatoRilevato("application/pkcs7-mime")
    if _sembra_xml(testa):
        return FormatoRilevato("application/xml")
    if testa and _sembra_testo(testa):
        return FormatoRilevato("text/plain")
    return FormatoRilevato(None)


def normalizza_mime(mime: str | None) -> str | None:
    """MIME dichiarato ricondotto alla forma canonica (minuscolo, senza parametri)."""
    if not mime:
        return None
    base = mime.split(";", 1)[0].strip().lower()
    return _EQUIVALENTI.get(base, base)


def compatibile(dichiarato: str | None, rilevato: str) -> bool:
    """Il MIME dichiarato è coerente con quello rilevato? (XML è anche testo)."""
    d = normalizza_mime(dichiarato)
    if d is None:
        return True
    if d == rilevato:
        return True
    return d == "text/plain" and rilevato == "application/xml"
