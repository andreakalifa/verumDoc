# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R08 (D02) — riconoscimento dei formati: Siegfried (PRONOM) se installato
(VD_TEST_SIEGFRIED_BIN / VD_TEST_SIEGFRIED_HOME), ripiego per firma binaria,
veraPDF (rapporto MRR da un processo di prova) e dichiarazione XMP."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from libs.shared.firme.tests.pki_prova import pdf_minimo
from libs.shared.formati import crea_strumenti
from libs.shared.formati.pdfa import (
    DichiarazionePdfA,
    ValidatorePdfAConRipiego,
    ValidatorePdfANonDisponibile,
    ValidatoreVeraPdf,
    interpreta_mrr,
)
from libs.shared.formati.riconoscimento import (
    RiconoscitoreConRipiego,
    RiconoscitoreFirmaBinaria,
    RiconoscitoreNonDisponibile,
)
from libs.shared.formati.siegfried import RiconoscitoreSiegfried, interpreta

SF_BIN = os.environ.get("VD_TEST_SIEGFRIED_BIN")
SF_HOME = os.environ.get("VD_TEST_SIEGFRIED_HOME")


def _png() -> bytes:
    import struct
    import zlib

    def blocco(tipo: bytes, dati: bytes) -> bytes:
        return (struct.pack(">I", len(dati)) + tipo + dati
                + struct.pack(">I", zlib.crc32(tipo + dati) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + blocco(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(b"\x00\x00")) + blocco(b"IEND", b""))


PNG = _png()
XMP_PDFA = (b"<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF><rdf:Description "
            b"xmlns:pdfaid='http://www.aiim.org/pdfa/ns/id/' pdfaid:part='2' "
            b"pdfaid:conformance='B'/></rdf:RDF></x:xmpmeta>")


def _scrivi(tmp_path: Path, nome: str, contenuto: bytes) -> Path:
    p = tmp_path / nome
    p.write_bytes(contenuto)
    return p


def _pdfa() -> bytes:
    base = pdf_minimo()
    return base.replace(b"%\xe2\xe3\xcf\xd3\n", b"%\xe2\xe3\xcf\xd3\n% " + XMP_PDFA + b"\n", 1)


async def test_firma_binaria_e_siegfried_mancante_errore_esplicito(tmp_path: Path) -> None:
    pdf = _scrivi(tmp_path, "a.pdf", pdf_minimo())
    esito = await RiconoscitoreFirmaBinaria().identifica(pdf)
    assert esito.mime == "application/pdf" and esito.puid is None
    assente = RiconoscitoreSiegfried(str(tmp_path / "sf-che-non-esiste"))
    combinato = RiconoscitoreConRipiego(assente, RiconoscitoreFirmaBinaria())
    # revisione X11: nessun ripiego silenzioso sulla firma binaria
    with pytest.raises(RiconoscitoreNonDisponibile):
        await combinato.identifica(pdf)
    # siegfried che termina con errore o va oltre il tempo: stesso esito di servizio
    for script in (b"#!/bin/sh\nexit 3\n", b"#!/bin/sh\nexec sleep 5\n"):
        sf = _scrivi(tmp_path, "sf-prova", script)
        sf.chmod(sf.stat().st_mode | stat.S_IEXEC)
        with pytest.raises(RiconoscitoreNonDisponibile):
            await RiconoscitoreConRipiego(RiconoscitoreSiegfried(str(sf), timeout_s=0.5),
                                          RiconoscitoreFirmaBinaria()).identifica(pdf)
    # un eseguibile si riconosce comunque per firma binaria se Siegfried non lo segnala
    vero = _scrivi(tmp_path, "sf-vero", b"#!/bin/sh\necho '{\"files\":[{\"errors\":\"\","
                   b"\"matches\":[{\"ns\":\"pronom\",\"id\":\"UNKNOWN\"}]}]}'\n")
    vero.chmod(vero.stat().st_mode | stat.S_IEXEC)
    exe = _scrivi(tmp_path, "documento.pdf", b"MZ\x90\x00" + b"\x00" * 60)
    assert (await RiconoscitoreConRipiego(RiconoscitoreSiegfried(str(vero)),
                                          RiconoscitoreFirmaBinaria()).identifica(exe)).eseguibile


def test_x11_errore_sul_contenuto_non_identificato() -> None:
    esito = interpreta(b'{"files":[{"errors":"empty source","matches":[]}]}')
    assert not esito.identificato and "empty source" in (esito.avviso or "")


async def test_x11_verapdf_terminato_al_tempo_massimo(tmp_path: Path) -> None:
    segno = tmp_path / "vivo"
    lento = _scrivi(tmp_path, "verapdf-lento",
                    f"#!/bin/sh\nsleep 3\ntouch {segno}\n".encode())
    lento.chmod(lento.stat().st_mode | stat.S_IEXEC)
    pdf = _scrivi(tmp_path, "a.pdf", pdf_minimo())
    with pytest.raises(ValidatorePdfANonDisponibile, match="tempo scaduto"):
        await ValidatoreVeraPdf(str(lento), timeout_s=0.3).valida(pdf)
    import asyncio
    await asyncio.sleep(3.5)
    assert not segno.exists()  # il processo è stato terminato


def test_interpretazione_uscita_siegfried() -> None:
    uscita = (b'{"siegfried":"1.11.1","identifiers":[{"name":"pronom","details":'
              b'"DROID_SignatureFile_V118.xml"}],"files":[{"filename":"x","errors":"",'
              b'"matches":[{"ns":"pronom","id":"fmt/479","format":"PDF/A-2b","version":"2b",'
              b'"mime":"application/pdf","basis":"byte match","warning":""}]}]}')
    esito = interpreta(uscita)
    assert esito.puid == "fmt/479" and esito.mime == "application/pdf"
    assert "V118" in esito.riconoscitore
    ignoto = interpreta(b'{"files":[{"errors":"","matches":[{"ns":"pronom","id":"UNKNOWN",'
                        b'"warning":"no match"}]}]}')
    assert ignoto.puid is None and not ignoto.identificato and ignoto.avviso == "no match"


@pytest.mark.skipif(not (SF_BIN and SF_HOME),
                    reason="Siegfried non installato (VD_TEST_SIEGFRIED_*)")
async def test_siegfried_reale_pronom(tmp_path: Path) -> None:
    sf = RiconoscitoreSiegfried(str(SF_BIN), home=SF_HOME)
    pdf = await sf.identifica(_scrivi(tmp_path, "a.pdf", pdf_minimo()))
    assert pdf.puid and pdf.puid.startswith("fmt/") and pdf.mime == "application/pdf"
    png = await sf.identifica(_scrivi(tmp_path, "b.bin", PNG))
    assert png.mime == "image/png"
    pdfa = await sf.identifica(_scrivi(tmp_path, "c.pdf", _pdfa()))
    assert pdfa.puid == "fmt/477", pdfa  # PDF/A-2b (PRONOM)
    ignoto = await sf.identifica(_scrivi(tmp_path, "d.xyz", os.urandom(64)))
    assert ignoto.puid is None
    solo_estensione = await sf.identifica(_scrivi(tmp_path, "e.pdf", b"testo qualsiasi"))
    assert solo_estensione.puid is None and solo_estensione.avviso


async def test_pdfa_verapdf_e_dichiarazione(tmp_path: Path) -> None:
    mrr = (b"<report><jobs><job><validationReport profileName='PDF/A-2B validation profile' "
           b"isCompliant='false'><details passedRules='120' failedRules='3'/>"
           b"</validationReport></job></jobs></report>")
    esito = interpreta_mrr(mrr)
    assert esito.livello == "2B" and esito.conforme is False and esito.regole_violate == 3
    falso = _scrivi(tmp_path, "verapdf", b"#!/bin/sh\ncat <<'X'\n" + mrr.replace(
        b"'false'", b"'true'") + b"\nX\n")
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    pdf = _scrivi(tmp_path, "a.pdf", _pdfa())
    vera = await ValidatoreVeraPdf(str(falso)).valida(pdf)
    assert vera.conforme is True and vera.livello == "2B"
    combinato = ValidatorePdfAConRipiego(ValidatoreVeraPdf(str(tmp_path / "manca")),
                                         DichiarazionePdfA())
    esito = await combinato.valida(pdf)
    assert esito.livello == "2B" and esito.conforme is None and "non validata" in esito.motivo
    nessuna = await combinato.valida(_scrivi(tmp_path, "b.pdf", pdf_minimo()))
    assert nessuna.livello is None


def test_configurazione() -> None:
    r, p = crea_strumenti({"FORMATI_RICONOSCITORE": "firma_binaria"})
    assert isinstance(r, RiconoscitoreFirmaBinaria)
    r, _ = crea_strumenti({})
    assert isinstance(r, RiconoscitoreConRipiego)
    with pytest.raises(ValueError):
        crea_strumenti({"FORMATI_RICONOSCITORE": "droid"})
