# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Riconoscimento del tipo reale dal contenuto (VD-C-017, REGOLE §8.15).
"""
from __future__ import annotations

import pytest

from libs.shared.formati.firma_binaria import compatibile, normalizza_mime, rileva_formato


@pytest.mark.parametrize("contenuto,mime", [
    (b"%PDF-1.7\n...", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n....", "image/png"),
    (b"\xff\xd8\xff\xe0JFIF", "image/jpeg"),
    (b"II*\x00....", "image/tiff"),
    (b"MM\x00*....", "image/tiff"),
    (b"<?xml version='1.0'?><a/>", "application/xml"),
    (b"\xef\xbb\xbf  <fattura><n>1</n></fattura>", "application/xml"),
    (b"-----BEGIN PKCS7-----\nMII...", "application/pkcs7-mime"),
    (b"\x30\x80\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02\xa0\x80", "application/pkcs7-mime"),
    ("testo semplice àèì".encode(), "text/plain"),
])
def test_rileva_formato_firme_note(contenuto, mime):
    esito = rileva_formato(contenuto)
    assert esito.mime == mime and esito.eseguibile is None


@pytest.mark.parametrize("contenuto", [
    b"MZ\x90\x00\x03\x00\x00\x00",          # PE Windows
    b"\x7fELF\x02\x01\x01",                  # ELF
    b"\xcf\xfa\xed\xfe\x07\x00",             # Mach-O
    b"#!/bin/sh\nrm -rf /\n",                # script
    b"\x00asm\x01\x00\x00\x00",              # WebAssembly
])
def test_rileva_formato_eseguibili_riconosciuti(contenuto):
    esito = rileva_formato(contenuto)
    assert esito.eseguibile is not None and esito.mime is None


def test_rileva_formato_binario_ignoto_nessun_mime():
    assert rileva_formato(b"\x00\x01\x02\x03\xfe\xff").mime is None


def test_rileva_formato_vuoto_nessun_mime():
    assert rileva_formato(b"").mime is None


def test_compatibile_dichiarato_uguale_o_equivalente():
    assert compatibile("application/pdf", "application/pdf")
    assert compatibile("text/xml", "application/xml")
    assert compatibile("APPLICATION/PDF; charset=binary", "application/pdf")
    assert compatibile(None, "image/png")


def test_compatibile_dichiarato_diverso_rifiutato():
    assert not compatibile("application/pdf", "text/plain")
    assert not compatibile("image/png", "application/pdf")


def test_normalizza_mime_alias():
    assert normalizza_mime("image/jpg") == "image/jpeg"
    assert normalizza_mime("") is None


def test_rileva_formato_guarda_solo_la_testa():
    """R03: il riconoscimento usa i primi BYTE_TESTA byte (la testa del caricamento in
    streaming); ciò che segue non cambia l'esito."""
    from libs.shared.formati.firma_binaria import BYTE_TESTA
    from libs.shared.storage.flusso import BYTE_TESTA_PREDEFINITI

    testo = b"a" * BYTE_TESTA + b"\x00\x01binario dopo la testa"
    assert rileva_formato(testo).mime == "text/plain"
    assert rileva_formato(testo[:BYTE_TESTA]).mime == "text/plain"
    assert BYTE_TESTA <= BYTE_TESTA_PREDEFINITI  # lo streaming conserva abbastanza byte
