# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Revisione R08 — verificatori: X1 (INDETERMINATE e livello CRITTOGRAFICA),
X4 (zip bomb), X5 (rilevazione parziale), X6 (busta non riconosciuta da DSS),
X7 (ripiego solo per errori di connessione e senza degradare il giudizio),
X8 (pyHanko in un thread con tempo massimo), X3 (codice fiscale del firmatario)."""
from __future__ import annotations

import asyncio
import io
import json
import time
import tracemalloc
import zipfile

import httpx
import pytest

from libs.shared.firme import (
    ErroreVerificatoreNonDisponibile,
    EsitoVerifica,
    FirmaVerificata,
    FormatoFirma,
    Indicazione,
    LivelloVerifica,
    StatoRevoca,
    rileva,
    rileva_formato_firma,
)
from libs.shared.firme.dss import VerificatoreDss, adatta_al_livello
from libs.shared.firme.interfaccia import ErroreConnessioneVerificatore
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from libs.shared.firme.rilevazione import rileva_formato_firma_parziale
from libs.shared.firme.ripiego import VerificatoreConRipiego
from libs.shared.firme.tests.pki_prova import Pki, pdf_minimo
from libs.shared.zip_limiti import MembroZipNonAmmesso, leggi_membro
from libs.verumdoc_rules.firme import problemi_firme

CRITT = LivelloVerifica.CRITTOGRAFICA
REV = LivelloVerifica.CON_REVOCHE
CF = "RSSMRA80A01H501U"


@pytest.fixture(scope="module")
def pki() -> Pki:
    return Pki.nuova(cf=CF)


def _dss(gestore, **kw) -> VerificatoreDss:  # type: ignore[no-untyped-def]
    return VerificatoreDss("http://verifica-firme:8080", "tok",
                           trasporto=httpx.MockTransport(gestore), **kw)


def _firma(**kw) -> FirmaVerificata:  # type: ignore[no-untyped-def]
    base = {"crittografia_valida": True, "catena_attendibile": True,
            "revoca": StatoRevoca.NON_CONTROLLATO}
    return FirmaVerificata(FormatoFirma.PADES, **{**base, **kw})


# ── X1 ──────────────────────────────────────────────────────────────

async def test_x1_certificato_scaduto_non_valido_a_crittografica() -> None:
    scaduta = Pki.nuova(scaduto=True)
    esito = await VerificatorePyHanko([scaduta.ca_asn1]).verifica(scaduta.pades(), livello=CRITT)
    assert esito.indicazione is not Indicazione.TOTAL_PASSED
    assert problemi_firme(esito.come_dict(), "CRITTOGRAFICA")


def test_x1_dss_solo_revoca_valida_a_crittografica_non_altrimenti() -> None:
    solo_revoca = EsitoVerifica(FormatoFirma.PADES, CRITT, "eu-dss", firme=(
        _firma(indicazione=Indicazione.INDETERMINATE, sottoindicazione="TRY_LATER"),))
    adattato = adatta_al_livello(solo_revoca)
    assert adattato.indicazione is Indicazione.TOTAL_PASSED
    assert adattato.firme[0].revoca is StatoRevoca.SCONOSCIUTO and adattato.note
    assert problemi_firme(adattato.come_dict(), "CRITTOGRAFICA") == []
    for sotto in ("OUT_OF_BOUNDS_NO_POE", "CRYPTO_CONSTRAINTS_FAILURE_NO_POE",
                  "CHAIN_CONSTRAINTS_FAILURE", None):
        e = EsitoVerifica(FormatoFirma.PADES, CRITT, "eu-dss", firme=(
            _firma(indicazione=Indicazione.INDETERMINATE, sottoindicazione=sotto),))
        assert adatta_al_livello(e).indicazione is Indicazione.INDETERMINATE, sotto
        assert problemi_firme(adatta_al_livello(e).come_dict(), "CRITTOGRAFICA"), sotto
    # a CON_REVOCHE la revoca serve: nessun adattamento
    rev = EsitoVerifica(FormatoFirma.PADES, REV, "eu-dss", firme=solo_revoca.firme)
    assert adatta_al_livello(rev).indicazione is Indicazione.INDETERMINATE
    # revocato o catena non attendibile: mai
    for kw in ({"revoca": StatoRevoca.REVOCATO}, {"catena_attendibile": False}):
        e = EsitoVerifica(FormatoFirma.PADES, CRITT, "eu-dss", firme=(_firma(
            indicazione=Indicazione.INDETERMINATE, sottoindicazione="TRY_LATER", **kw),))
        assert adatta_al_livello(e).indicazione is Indicazione.INDETERMINATE


# ── X3: codice fiscale ──────────────────────────────────────────────

def test_x3_codice_fiscale_dal_certificato(pki: Pki) -> None:
    esito = rileva(pki.cades(b"x"))
    assert esito.firme[0].certificato is not None
    assert esito.firme[0].certificato.codice_fiscale == CF
    assert esito.come_dict()["firme"][0]["certificato"]["codice_fiscale"] == CF


# ── X4: zip bomb ────────────────────────────────────────────────────

def _bomba(nome: str = "mimetype", dimensione: int = 200 * 1024 * 1024) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(nome)
        info.compress_type = zipfile.ZIP_DEFLATED
        with z.open(info, "w", force_zip64=True) as f:
            blocco = b"\0" * (1024 * 1024)
            for _ in range(dimensione // len(blocco)):
                f.write(blocco)
        z.writestr("content.xml", b"<office/>")
    return buf.getvalue()


def test_x4_zip_bomb_nella_rilevazione_asic() -> None:
    bomba = _bomba()
    assert len(bomba) < 1024 * 1024  # ~200 KB compressi, 200 MB dichiarati
    tracemalloc.start()
    inizio = time.monotonic()
    try:
        assert rileva_formato_firma(bomba, "documento.odt") is FormatoFirma.NESSUNO
        assert rileva(bomba, "documento.odt").firmato is False
        _, picco = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert picco < 8 * 1024 * 1024 and time.monotonic() - inizio < 2


def test_x4_limiti_dei_membri() -> None:
    with zipfile.ZipFile(io.BytesIO(_bomba("grande.xml", 8 * 1024 * 1024))) as z:
        with pytest.raises(MembroZipNonAmmesso, match="rapporto"):
            leggi_membro(z, "grande.xml", massimo_byte=16 * 1024 * 1024)
        with pytest.raises(MembroZipNonAmmesso, match="dichiarati"):
            leggi_membro(z, "grande.xml", massimo_byte=256)
        assert leggi_membro(z, "content.xml", massimo_byte=256) == b"<office/>"
    # ASiC vero: mimetype non compresso, riconosciuto
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", b"application/vnd.etsi.asic-e+zip")
        z.writestr("META-INF/signatures001.xml", b"<x/>")
    assert rileva_formato_firma(buf.getvalue()) is FormatoFirma.ASIC
    assert rileva_formato_firma_parziale(buf.getvalue()[:1024], buf.getvalue()[-1024:]) \
        is FormatoFirma.ASIC


def bomba_compressa(metodo: int, mib: int = 64, nome: str = "mimetype",
                    dichiarati: int = 100) -> bytes:
    """Revisione Y1 (prova ``r08/y/bomba_bz2.py``): zip di pochi KB con un membro
    bzip2 o LZMA che si espande a ``mib`` MiB e dichiara ``dichiarati`` byte."""
    import bz2
    import lzma
    import struct

    blocco = b"\0" * (16 * 1024 * 1024)
    if metodo == zipfile.ZIP_BZIP2:
        c = bz2.BZ2Compressor(9)
        comp = b"".join(c.compress(blocco) for _ in range(mib // 16)) + c.flush()
    else:  # ZIP_LZMA: intestazione propria di zip (versione, lunghezza delle proprietà)
        filtri = [{"id": lzma.FILTER_LZMA1}]
        c = lzma.LZMACompressor(lzma.FORMAT_RAW, filters=filtri)
        props = lzma._encode_filter_properties(filtri[0])  # type: ignore[attr-defined]
        comp = struct.pack("<BBH", 9, 4, len(props)) + props + b"".join(
            c.compress(blocco) for _ in range(mib // 16)) + c.flush()
    n = nome.encode()
    lh = struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 63, 0, metodo, 0, 0, 0, len(comp),
                     dichiarati, len(n), 0) + n
    cd = struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 63, 63, 0, metodo, 0, 0, 0,
                     len(comp), dichiarati, len(n), 0, 0, 0, 0, 0, 0) + n
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1, 1, len(cd), len(lh) + len(comp), 0)
    return lh + comp + cd + eocd


@pytest.mark.parametrize("metodo", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"])
def test_y1_bomba_bzip2_lzma_con_dimensione_dichiarata_falsa(metodo: int) -> None:
    bomba = bomba_compressa(metodo)
    assert len(bomba) < 64 * 1024
    tracemalloc.start()
    try:
        assert rileva_formato_firma(bomba, "documento.docx") is FormatoFirma.NESSUNO
        _, picco = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert picco < 8 * 1024 * 1024
    with zipfile.ZipFile(io.BytesIO(bomba)) as z:
        with pytest.raises(MembroZipNonAmmesso, match="metodo di compressione"):
            leggi_membro(z, "mimetype", massimo_byte=256)
    # mimetype ASiC compresso (anche DEFLATE): non è un ASiC (ETSI EN 319 162-1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", b"application/vnd.etsi.asic-e+zip")
    assert rileva_formato_firma(buf.getvalue()) is FormatoFirma.NESSUNO


# ── X5: rilevazione dai soli primi e ultimi byte ─────────────────────

def test_x5_rilevazione_parziale(pki: Pki) -> None:
    pdf = pki.pades()
    assert rileva_formato_firma_parziale(pdf[:64], pdf[-4096:]) is FormatoFirma.PADES
    assert rileva_formato_firma_parziale(pdf_minimo()[:64], pdf_minimo()) is FormatoFirma.NESSUNO
    p7m = pki.cades(b"y")
    assert rileva_formato_firma_parziale(p7m[:128], b"") is FormatoFirma.CADES
    assert rileva_formato_firma_parziale(b"II*\0" + b"\0" * 100, b"\0" * 100) \
        is FormatoFirma.NESSUNO
    assert rileva_formato_firma_parziale(b"x" * 100, b"", "a.xml.p7s") is FormatoFirma.CADES


# ── X6: busta non riconosciuta da DSS ────────────────────────────────

async def test_x6_busta_corrotta_non_diventa_non_firmato(pki: Pki) -> None:
    corrotta = pki.cades(b"z")[:60]

    def nessuno(req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content)
        return httpx.Response(200, json=EsitoVerifica(
            FormatoFirma.NESSUNO, LivelloVerifica(corpo["livello"]), "eu-dss").come_dict())

    esito = await _dss(nessuno).verifica(corrotta, livello=REV, nome_file="a.p7m")
    assert esito.formato is FormatoFirma.CADES
    assert esito.indicazione is Indicazione.TOTAL_FAILED
    assert problemi_firme(esito.come_dict(), "CON_REVOCHE")
    assert problemi_firme(esito.come_dict(), "RILEVATA")  # come la rilevazione locale
    # un file davvero non firmato resta non firmato
    assert (await _dss(nessuno).verifica(pdf_minimo(), livello=REV)).firmato is False


# ── X7: ripiego ─────────────────────────────────────────────────────

async def test_x7_ripiego_solo_per_errori_di_connessione(pki: Pki) -> None:
    pdf = pki.pades()
    pyhanko = VerificatorePyHanko([pki.ca_asn1])
    chiamate: list[str] = []

    class Spia:
        nome, formati = "spia", pyhanko.formati

        async def verifica(self, *a, **kw):  # type: ignore[no-untyped-def]
            chiamate.append("ripiego")
            return await pyhanko.verifica(*a, **kw)

    for stato in (401, 403, 500, 503):
        with pytest.raises(ErroreVerificatoreNonDisponibile) as info:
            await VerificatoreConRipiego(_dss(lambda r, s=stato: httpx.Response(s)),
                                         Spia()).verifica(pdf, livello=CRITT)
        assert not isinstance(info.value, ErroreConnessioneVerificatore)
    assert chiamate == []

    def giu(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("tempo scaduto")

    esito = await VerificatoreConRipiego(_dss(giu), Spia()).verifica(pdf, livello=CRITT)
    assert chiamate == ["ripiego"] and esito.ripiego
    assert esito.indicazione is Indicazione.TOTAL_PASSED


async def test_x7_ripiego_senza_trust_anchor_non_rifiuta(pki: Pki) -> None:
    def giu(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("rifiutata")

    # pyHanko senza radici: catena non attendibile → INDETERMINATE → si ritenta
    with pytest.raises(ErroreVerificatoreNonDisponibile, match="non netto"):
        await VerificatoreConRipiego(_dss(giu), VerificatorePyHanko([])).verifica(
            pki.pades(), livello=CRITT)


async def test_x7_limite_di_dimensione_esplicito(pki: Pki) -> None:
    inviati: list[int] = []

    def troppo(req: httpx.Request) -> httpx.Response:
        inviati.append(len(req.content))
        return httpx.Response(413)

    pdf = pki.pades()
    esito = await _dss(troppo).verifica(pdf, livello=CRITT)
    assert esito.limite_superato and esito.formato is FormatoFirma.PADES
    assert problemi_firme(esito.come_dict(), "CRITTOGRAFICA")
    assert problemi_firme(esito.come_dict(), "RILEVATA") == []
    # oltre il limite configurato il file non si invia nemmeno
    esito = await _dss(troppo, dimensione_max_byte=100).verifica(pdf, livello=CRITT)
    assert esito.limite_superato and len(inviati) == 1
    esito = await _dss(troppo, dimensione_max_byte=100).verifica(pdf_minimo() * 10,
                                                                  livello=CRITT)
    assert esito.firmato is False and esito.limite_superato is None


# ── X8: pyHanko in un thread con tempo massimo ───────────────────────

async def test_x8_pyhanko_in_thread_e_tempo_massimo(pki: Pki, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # modalità thread (FIRME_PROCESSO_SEPARATO=false); il processo: test_r12a_processo.py
    v = VerificatorePyHanko([pki.ca_asn1], tempo_max_s=0.3, processo_separato=False)
    principale = asyncio.get_running_loop()
    loop_usati: list[object] = []
    originale = v._verifica

    async def lenta(*a, **kw):  # type: ignore[no-untyped-def]
        loop_usati.append(asyncio.get_running_loop())
        time.sleep(1.0)  # parsing sincrono patologico
        return await originale(*a, **kw)

    monkeypatch.setattr(v, "_verifica", lenta)
    battiti = 0

    async def battito() -> None:
        nonlocal battiti
        while True:
            await asyncio.sleep(0.05)
            battiti += 1

    compito = asyncio.create_task(battito())
    esito = await v.verifica(pki.pades(), livello=CRITT)
    compito.cancel()
    assert esito.limite_superato and "tempo massimo" in esito.limite_superato
    assert loop_usati and loop_usati[0] is not principale
    assert battiti >= 3  # il loop del consumatore non si è bloccato
