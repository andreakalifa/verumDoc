# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R08 — passi 10 e 11: X5 (nessun rifiuto per dimensione a RILEVATA,
rifiuto esplicito da CRITTOGRAFICA solo per i file firmati), X11 (PDF/A
dichiarato non validato = avviso; Siegfried in errore = errore di servizio),
X13 (copia locale confrontata con le impronte del passo 5), X14 (firma staccata
abbinata al documento del PdV).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.config import settings
from app.services.lettura_file import FileLetto
from app.services.regole_pdv import regole_del_pdv
from app.validators.base import ErroreVerificaTransitorio, ValidationContext
from app.validators.contenuto import Step10FormatoRiconosciuto, Step11VerificaFirme

from libs.shared.firme import imposta_verificatore
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from libs.shared.firme.tests.pki_prova import Pki, pdf_minimo
from libs.shared.formati import EsitoPdfA, FormatoIdentificato, imposta_strumenti
from libs.shared.formati.riconoscimento import RiconoscitoreNonDisponibile
from tests.lettore_memoria import lettore_per_nomi

MB = 1024 * 1024
TIFF = b"II*\x00" + b"\x00" * 60


@pytest.fixture(scope="module")
def pki() -> Pki:
    return Pki.nuova()


@pytest.fixture(autouse=True)
def verificatore_di_prova(pki: Pki):
    imposta_verificatore(VerificatorePyHanko([pki.ca_asn1]))
    yield
    imposta_verificatore(None)
    imposta_strumenti(None)


def _foto(**impostazioni) -> dict:
    return {"catalogo_regole": "3", "profilo_metadati": None, "impostazioni": {
        codice: {"valore": valore, "origine": "CLASSE"} for codice, valore in impostazioni.items()}}


def _ctx(cartella: Path, file_map: dict[str, bytes], foto: dict, *,
         impronte: dict[str, bytes] | None = None) -> ValidationContext:
    cartella.mkdir(parents=True, exist_ok=True)
    riferimenti, lettore = lettore_per_nomi(file_map)
    documenti = [{"id": f"d{i}", "tipo": "PRINCIPALE" if i == 0 else "ALLEGATO",
                  "nome_file": nome} for i, nome in enumerate(file_map)]
    ctx = ValidationContext(pdv_id="p1", tenant_id="t1",
                            indice={"classe_documentale": "c", "documenti": documenti},
                            riferimenti=riferimenti, lettore=lettore,
                            regole=regole_del_pdv(foto, limite_mb_predefinito=5120,
                                                  formati_predefiniti=[]),
                            cartella=cartella)
    for nome, c in (impronte or file_map).items():
        ctx.file_letti[nome] = FileLetto(len(c), {"SHA-256": hashlib.sha256(c).hexdigest()})
    return ctx


# ── X5 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("copia_max_mb", [1, 512], ids=["oltre-copia-locale", "oltre-firme"])
async def test_x5_dimensione_rilevata_mai_rifiuto_crittografica_solo_firmati(
        tmp_path: Path, pki: Pki, monkeypatch, copia_max_mb: int) -> None:
    monkeypatch.setattr(settings, "firme_dimensione_max_mb", 1)
    monkeypatch.setattr(settings, "verifica_contenuto_max_mb", copia_max_mb)
    grandi = {"video.tif": TIFF + b"\0" * (2 * MB),
              "grande.pdf.p7m": pki.cades(b"x" * (2 * MB)),
              "grande.pdf": pki.pades("y" * (2 * MB))}
    rilevata = await Step11VerificaFirme().validate(
        _ctx(tmp_path / "r", grandi, _foto(FIRME_LIVELLO_VERIFICA="RILEVATA")))
    assert rilevata.ok and rilevata.dettagli["rilievi"] == []
    esiti = {r["nome_file"]: r["esito"] for r in rilevata.dettagli["rapporto"]["file"]}
    assert esiti["video.tif"]["formato"] == "NESSUNO"
    assert esiti["grande.pdf.p7m"]["limite_superato"] and esiti["grande.pdf"]["limite_superato"]
    critt = await Step11VerificaFirme().validate(
        _ctx(tmp_path / "c", grandi, _foto(FIRME_LIVELLO_VERIFICA="CRITTOGRAFICA")))
    assert not critt.ok and critt.codice_errore == "VLD-1101"
    assert sorted(r["documento"] for r in critt.dettagli["rilievi"]) == ["d1", "d2"]
    assert "firme non verificate" in critt.dettagli["rilievi"][0]["messaggio"]


# ── X13 ─────────────────────────────────────────────────────────────

async def test_x13_staging_cambiato_dopo_il_passo_5(tmp_path: Path, pki: Pki) -> None:
    foto = _foto(FIRME_LIVELLO_VERIFICA="CRITTOGRAFICA")
    # il passo 5 ha hashato un file; nello staging ora c'è una versione firmata valida
    ctx = _ctx(tmp_path, {"a.pdf": pki.pades()}, foto, impronte={"a.pdf": pdf_minimo()})
    with pytest.raises(ErroreVerificaTransitorio, match="cambiato"):
        await Step11VerificaFirme().validate(ctx)
    with pytest.raises(ErroreVerificaTransitorio, match="cambiato"):
        await Step10FormatoRiconosciuto().validate(
            _ctx(tmp_path / "f", {"a.pdf": TIFF}, foto, impronte={"a.pdf": pdf_minimo()}))


# ── X14 ─────────────────────────────────────────────────────────────

async def test_x14_firma_staccata_abbinata_al_documento(tmp_path: Path, pki: Pki) -> None:
    xml = b"<fattura>1</fattura>"
    p7s = pki.cades(xml, staccata=True)
    foto = _foto(FIRME_LIVELLO_VERIFICA="CRITTOGRAFICA")
    esito = await Step11VerificaFirme().validate(
        _ctx(tmp_path / "a", {"fattura.xml": xml, "fattura.xml.p7s": p7s}, foto))
    assert esito.ok, esito.dettagli["rilievi"]
    righe = {r["nome_file"]: r for r in esito.dettagli["rapporto"]["file"]}
    assert righe["fattura.xml.p7s"]["firma_staccata_di"] == "fattura.xml"
    assert righe["fattura.xml.p7s"]["esito_in_parole"] == "valida"
    # documento firmato diverso: firma non valida
    altro = await Step11VerificaFirme().validate(
        _ctx(tmp_path / "b", {"fattura.xml": b"<fattura>2</fattura>",
                              "fattura.xml.p7s": p7s}, foto))
    assert not altro.ok
    # documento assente: esito esplicito, non «firma non valida» generica
    solo = await Step11VerificaFirme().validate(
        _ctx(tmp_path / "c", {"fattura.xml.p7s": p7s}, foto))
    assert not solo.ok
    assert "firma staccata non verificabile" in solo.dettagli["rilievi"][0]["messaggio"]


# ── X11 ─────────────────────────────────────────────────────────────

class _Riconoscitore:
    nome = "prova"

    def __init__(self, esito: FormatoIdentificato | None) -> None:
        self.esito = esito

    async def identifica(self, percorso: Path) -> FormatoIdentificato:
        if self.esito is None:
            raise RiconoscitoreNonDisponibile("siegfried terminato con 1")
        return self.esito


class _Dichiarazione:
    nome = "dichiarazione XMP (veraPDF non disponibile)"

    async def valida(self, percorso: Path) -> EsitoPdfA:
        return EsitoPdfA(self.nome, "1B", None, motivo="PDF/A dichiarato, non validato")


async def test_x11_pdfa_dichiarato_non_validato_e_siegfried_in_errore(tmp_path: Path) -> None:
    pdfa = FormatoIdentificato("siegfried", puid="fmt/354", mime="application/pdf")
    imposta_strumenti((_Riconoscitore(pdfa), _Dichiarazione()))  # type: ignore[arg-type]
    foto = _foto(**{"FORMATO.PDF.ESITO": "RIFIUTATO"})
    esito = await Step10FormatoRiconosciuto().validate(
        _ctx(tmp_path / "a", {"a.pdf": pdf_minimo()}, foto))
    assert esito.ok
    assert [(r["codice"], r["effetto"]) for r in esito.dettagli["rilievi"]] == [
        ("RGL-108", "AVVISA")]
    assert "non validato" in esito.dettagli["formati"][0]["catalogo"]["motivo"]
    # il conservatore richiede il PDF/A validato: vale come PDF (rifiutato)
    rigido = await Step10FormatoRiconosciuto().validate(_ctx(tmp_path / "b", {
        "a.pdf": pdf_minimo()}, _foto(**{"FORMATO.PDF.ESITO": "RIFIUTATO",
                                         "FORMATO.PDFA_NON_VALIDATO": "COME_PDF"})))
    assert not rigido.ok and rigido.codice_errore == "VLD-1002"
    # Siegfried in errore: errore di servizio esplicito, nessun ripiego silenzioso
    imposta_strumenti((_Riconoscitore(None), _Dichiarazione()))  # type: ignore[arg-type]
    with pytest.raises(ErroreVerificaTransitorio, match="riconoscimento dei formati"):
        await Step10FormatoRiconosciuto().validate(_ctx(tmp_path / "c", {"a.pdf": TIFF}, foto))


# ── Y3 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("copia_max_mb", [1, 512], ids=["oltre-copia-locale", "oltre-firme"])
async def test_y3_pdf_firmato_con_firma_lontana_da_testa_e_coda(
        tmp_path: Path, pki: Pki, monkeypatch, copia_max_mb: int) -> None:
    """Oltre il limite, un PDF firmato a cui sono stati accodati più di 1 MiB di
    aggiornamenti (qui: commenti) dopo la firma resta «firmato, non verificato»."""
    monkeypatch.setattr(settings, "firme_dimensione_max_mb", 1)
    monkeypatch.setattr(settings, "verifica_contenuto_max_mb", copia_max_mb)
    # firma oltre il primo MiB e più di 1 MiB prima della fine
    firmato = pki.pades("z" * (2 * MB)) + b"\n%" + b"x" * (3 * MB) + b"\n%%EOF\n"
    non_firmato = pdf_minimo() + b"\n%" + b"y" * (3 * MB) + b"\n%%EOF\n"
    esito = await Step11VerificaFirme().validate(_ctx(
        tmp_path, {"a.pdf": firmato, "b.pdf": non_firmato},
        _foto(FIRME_LIVELLO_VERIFICA="CON_REVOCHE")))
    assert not esito.ok and esito.codice_errore == "VLD-1101"
    assert [r["documento"] for r in esito.dettagli["rilievi"]] == ["d0"]
    esiti = {r["nome_file"]: r["esito"] for r in esito.dettagli["rapporto"]["file"]}
    assert esiti["a.pdf"]["formato"] == "PAdES" and esiti["a.pdf"]["limite_superato"]
    assert esiti["b.pdf"]["formato"] == "NESSUNO"


def test_y3_ricerca_a_blocchi_a_cavallo() -> None:
    from libs.shared.firme.rilevazione import cerca_in_blocchi

    dati = b"a" * 1000 + b"/ByteRange" + b"b" * 1000
    for n in (1, 3, 7, 1001, 1005, 4096):
        blocchi = [dati[i:i + n] for i in range(0, len(dati), n)]
        assert cerca_in_blocchi(blocchi), n
        assert not cerca_in_blocchi([b.replace(b"R", b"r") for b in blocchi]), n
