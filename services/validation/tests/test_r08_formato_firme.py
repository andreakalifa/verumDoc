# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R08 (D01, D02) — passi 10 e 11 della verifica: formato riconosciuto dal contenuto
con il catalogo dei formati fotografato e firme verificate al livello della
classe (CA di prova generata a runtime, nessuna rete). Rapporto di verifica nei
dettagli, avvisi verso il PdV/RdV, verificatore giù = errore transitorio.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from app.services.consumer import avvisi_dei_passi
from app.services.lettura_file import FileLetto
from app.services.regole_pdv import regole_del_pdv
from app.validators.base import ErroreVerificaTransitorio, ValidationContext
from app.validators.contenuto import Step10FormatoRiconosciuto, Step11VerificaFirme

from libs.shared.firme import ErroreVerificatoreNonDisponibile, imposta_verificatore
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from libs.shared.firme.tests.pki_prova import Pki, pdf_minimo
from libs.shared.formati import crea_strumenti, imposta_strumenti
from tests.lettore_memoria import lettore_per_nomi

TIFF = b"II*\x00" + b"\x00" * 60
EXE = b"MZ\x90\x00" + b"\x00" * 60


@pytest.fixture(scope="module")
def pki() -> Pki:
    return Pki.nuova()


@pytest.fixture(autouse=True)
def strumenti_e_verificatore(pki: Pki):
    ambiente = {"FORMATI_RICONOSCITORE": "firma_binaria"}
    if os.environ.get("VD_TEST_SIEGFRIED_BIN"):
        ambiente = {"FORMATI_RICONOSCITORE": "siegfried",
                    "SIEGFRIED_BIN": os.environ["VD_TEST_SIEGFRIED_BIN"],
                    "SIEGFRIED_HOME": os.environ.get("VD_TEST_SIEGFRIED_HOME", "")}
    imposta_strumenti(crea_strumenti(ambiente))
    imposta_verificatore(VerificatorePyHanko([pki.ca_asn1]))
    yield
    imposta_strumenti(None)
    imposta_verificatore(None)


def _foto(**impostazioni) -> dict:
    return {"catalogo_regole": "3", "profilo_metadati": None, "impostazioni": {
        codice: {"valore": valore, "origine": "CLASSE"} for codice, valore in impostazioni.items()}}


def _ctx(tmp_path: Path, file_map: dict[str, bytes], foto: dict | None) -> ValidationContext:
    riferimenti, lettore = lettore_per_nomi(file_map)
    documenti = [{"id": f"d{i}", "tipo": "PRINCIPALE" if i == 0 else "ALLEGATO",
                  "nome_file": nome} for i, nome in enumerate(file_map)]
    regole = regole_del_pdv(foto, limite_mb_predefinito=5120, formati_predefiniti=[])
    ctx = ValidationContext(pdv_id="p1", tenant_id="t1",
                            indice={"classe_documentale": "c", "documenti": documenti},
                            riferimenti=riferimenti, lettore=lettore, regole=regole,
                            cartella=tmp_path)
    # impronte del passo 5 (revisione X13: la copia locale si confronta con queste)
    ctx.file_letti.update({n: FileLetto(len(c), {"SHA-256": hashlib.sha256(c).hexdigest()})
                           for n, c in file_map.items()})
    return ctx


async def test_formato_catalogo_rifiuta_avvisa_e_respinge_eseguibili(tmp_path: Path) -> None:
    foto = _foto(**{"FORMATO.TIFF.ESITO": "RIFIUTATO", "FORMATO.PDF.ESITO": "AVVISO",
                    "FORMATO.PDFA.ESITO": "AVVISO"})
    esito = await Step10FormatoRiconosciuto().validate(
        _ctx(tmp_path, {"a.pdf": pdf_minimo(), "b.tif": TIFF}, foto))
    assert not esito.ok and esito.codice_errore == "VLD-1002"
    regole = {r["documento"]: r["codice"] for r in esito.dettagli["rilievi"]}
    assert regole == {"d0": "RGL-108", "d1": "RGL-107"}
    righe = {r["file"]: r for r in esito.dettagli["formati"]}
    assert righe["b.tif"]["catalogo"]["esito"] == "RIFIUTATO"
    assert righe["a.pdf"]["pdfa"]["livello"] is None  # nessuna dichiarazione PDF/A
    # solo avvisi: il passo è superato e gli avvisi vanno nel PdV (poi nel RdV)
    ok = await Step10FormatoRiconosciuto().validate(
        _ctx(tmp_path / "x", {"a.pdf": pdf_minimo()}, foto) if (tmp_path / "x").mkdir() is None
        else None)
    assert ok.ok and avvisi_dei_passi([ok])[0]["codice"] == "RGL-108"
    (tmp_path / "y").mkdir()
    exe = await Step10FormatoRiconosciuto().validate(_ctx(tmp_path / "y", {"a.pdf": EXE}, foto))
    assert not exe.ok and exe.codice_errore == "VLD-1001"


async def test_fotografia_precedente_registra_solo_l_identificazione(tmp_path: Path) -> None:
    foto = {**_foto(), "catalogo_regole": "2"}
    esito = await Step10FormatoRiconosciuto().validate(_ctx(tmp_path, {"b.tif": TIFF}, foto))
    assert esito.ok and esito.dettagli["catalogo"] == "non in vigore al versamento"
    firme = await Step11VerificaFirme().validate(_ctx(tmp_path, {"b.tif": TIFF}, foto))
    assert firme.ok and firme.dettagli["livello"] == "NESSUNA"


async def test_firme_crittografiche_blocca_e_rapporto(tmp_path: Path, pki: Pki) -> None:
    valido = pki.cades(b"documento firmato")
    i = valido.index(b"documento")
    alterato = valido[:i] + b"D" + valido[i + 1:]
    foto = _foto(FIRME_LIVELLO_VERIFICA="CRITTOGRAFICA")
    esito = await Step11VerificaFirme().validate(_ctx(
        tmp_path, {"a.pdf.p7m": valido, "b.pdf.p7m": alterato, "c.pdf": pki.pades(),
                   "d.pdf": pdf_minimo()}, foto))
    assert not esito.ok and esito.codice_errore == "VLD-1101"
    assert [r["documento"] for r in esito.dettagli["rilievi"]] == ["d1"]
    assert "modificato dopo la firma" in esito.dettagli["rilievi"][0]["messaggio"]
    rapporto = esito.dettagli["rapporto"]
    parole = {r["nome_file"]: r["esito_in_parole"] for r in rapporto["file"]}
    assert parole == {"a.pdf.p7m": "valida", "b.pdf.p7m": "non valida", "c.pdf": "valida",
                      "d.pdf": "non firmato"}
    assert rapporto["livello"] == "CRITTOGRAFICA" and rapporto["verificatore"].startswith(
        "pyhanko")
    assert rapporto["file"][0]["esito"]["firme"][0]["firmatario"] == "Firmatario di prova"


async def test_firme_effetto_avvisa_e_livello_rilevata(tmp_path: Path, pki: Pki) -> None:
    alterato = pki.cades(b"documento firmato").replace(b"documento", b"Documento", 1)
    foto = _foto(FIRME_LIVELLO_VERIFICA="CRITTOGRAFICA", **{"REGOLA.RGL-109.EFFETTO": "AVVISA"})
    esito = await Step11VerificaFirme().validate(_ctx(tmp_path, {"a.p7m": alterato}, foto))
    assert esito.ok and avvisi_dei_passi([esito])[0]["codice"] == "RGL-109"
    # livello RILEVATA: la firma alterata non si verifica (nessun rilievo), il firmatario sì
    rilevata = await Step11VerificaFirme().validate(_ctx(
        tmp_path, {"a.p7m": alterato}, _foto(FIRME_LIVELLO_VERIFICA="RILEVATA")))
    assert rilevata.ok and rilevata.dettagli["rilievi"] == []
    assert rilevata.dettagli["rapporto"]["file"][0]["esito"]["verificatore"] == "rilevazione"


async def test_verificatore_non_disponibile_e_errore_transitorio(tmp_path: Path,
                                                                 pki: Pki) -> None:
    class Giu:
        nome = "giu"
        formati = frozenset()

        async def verifica(self, *a, **k):
            raise ErroreVerificatoreNonDisponibile("verifica-firme non raggiungibile")

    imposta_verificatore(Giu())  # type: ignore[arg-type]
    with pytest.raises(ErroreVerificaTransitorio):
        await Step11VerificaFirme().validate(_ctx(
            tmp_path, {"a.p7m": pki.cades(b"x")}, _foto(FIRME_LIVELLO_VERIFICA="CON_REVOCHE")))
