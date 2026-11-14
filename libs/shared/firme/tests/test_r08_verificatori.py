# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R08 (D01) — verificatori di firma intercambiabili: esito normalizzato con una
CA di prova generata a runtime, CRL e OCSP simulati, nessuna rete."""
from __future__ import annotations

import base64
import json

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
    crea_verificatore,
    rileva,
    rileva_formato_firma,
    verifica_al_livello,
)
from libs.shared.firme.dss import VerificatoreDss
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from libs.shared.firme.ripiego import VerificatoreConRipiego
from libs.shared.firme.tests.pki_prova import Pki, pdf_minimo

CRITT = LivelloVerifica.CRITTOGRAFICA
REV = LivelloVerifica.CON_REVOCHE


@pytest.fixture(scope="module")
def pki() -> Pki:
    return Pki.nuova()


@pytest.fixture(scope="module")
def p7m(pki: Pki) -> bytes:
    return pki.cades(b"Autorizzazione allo scarto n. 1")


@pytest.fixture(scope="module")
def pdf(pki: Pki) -> bytes:
    return pki.pades()


def test_rilevazione_formati(p7m: bytes, pdf: bytes) -> None:
    assert rileva_formato_firma(p7m) is FormatoFirma.CADES
    assert rileva_formato_firma(base64.b64encode(p7m), "a.pdf.p7m") is FormatoFirma.CADES
    assert rileva_formato_firma(pdf) is FormatoFirma.PADES
    assert rileva_formato_firma(pdf_minimo()) is FormatoFirma.NESSUNO
    assert rileva_formato_firma(b"<a><ds:Signature xmlns:ds='http://www.w3.org/2000/09/"
                                b"xmldsig#'/></a>") is FormatoFirma.XADES
    # un .p7m scritto a mano non è più «firmato»: busta illeggibile, TOTAL_FAILED
    esito = rileva(b"0\x82\x01\x00 firma finta", "a.p7m")
    assert esito.formato is FormatoFirma.CADES and esito.indicazione is Indicazione.TOTAL_FAILED


def test_rilevata_dice_il_firmatario_ma_non_verifica(p7m: bytes) -> None:
    esito = rileva(p7m)
    assert esito.firme[0].firmatario == "Firmatario di prova"
    assert esito.firme[0].crittografia_valida is None
    assert esito.indicazione is Indicazione.INDETERMINATE


async def test_cades_e_pades_validi_con_trust_anchor_locale(pki: Pki, p7m: bytes,
                                                            pdf: bytes) -> None:
    v = VerificatorePyHanko([pki.ca_asn1])
    for contenuto in (p7m, pdf):
        esito = await v.verifica(contenuto, livello=CRITT)
        firma = esito.firme[0]
        assert esito.indicazione is Indicazione.TOTAL_PASSED, firma.motivi
        assert firma.crittografia_valida and firma.catena_attendibile
        assert firma.firmatario == "Firmatario di prova"
        assert "CA di prova" in (firma.trust_anchor or "")
        assert firma.revoca is StatoRevoca.NON_CONTROLLATO
        assert esito.rapporto_grezzo
        # forma serializzabile e rileggibile (rapporto nel pacchetto, console)
        assert EsitoVerifica.da_dict(json.loads(json.dumps(esito.come_dict()))) \
            .indicazione is Indicazione.TOTAL_PASSED


async def test_catena_non_attendibile_e_contenuto_alterato(pki: Pki, p7m: bytes,
                                                           pdf: bytes) -> None:
    esito = await VerificatorePyHanko([]).verifica(p7m, livello=CRITT)
    assert esito.indicazione is Indicazione.INDETERMINATE
    assert esito.firme[0].sottoindicazione == "NO_CERTIFICATE_CHAIN_FOUND"
    assert "prestatore fidato" in esito.firme[0].motivi[0]
    # contenuto incapsulato alterato: impronta diversa
    i = p7m.index(b"Autorizzazione")
    alterato = p7m[:i] + b"B" + p7m[i + 1:]
    esito = await VerificatorePyHanko([pki.ca_asn1]).verifica(alterato, livello=CRITT)
    assert esito.indicazione is Indicazione.TOTAL_FAILED
    assert esito.firme[0].sottoindicazione == "HASH_FAILURE"
    # PDF modificato nell'intervallo firmato
    j = pdf.index(b"Documento di prova")
    esito = await VerificatorePyHanko([pki.ca_asn1]).verifica(
        pdf[:j] + b"X" + pdf[j + 1:], livello=CRITT)
    assert esito.indicazione is Indicazione.TOTAL_FAILED


async def test_revoche_simulate(pki: Pki, p7m: bytes, pdf: bytes) -> None:
    # senza informazioni di revoca: CRITTOGRAFICA passa, CON_REVOCHE è indeterminata
    v = VerificatorePyHanko([pki.ca_asn1])
    assert (await v.verifica(p7m, livello=CRITT)).indicazione is Indicazione.TOTAL_PASSED
    esito = await v.verifica(p7m, livello=REV)
    assert esito.indicazione is Indicazione.INDETERMINATE
    assert esito.firme[0].revoca is StatoRevoca.SCONOSCIUTO
    assert esito.firme[0].sottoindicazione == "TRY_LATER"
    # CRL e OCSP buoni
    for kw in ({"crl_locali": [pki.crl()]}, {"ocsp_locali": [pki.ocsp()]}):
        esito = await VerificatorePyHanko([pki.ca_asn1], **kw).verifica(pdf, livello=REV)
        assert esito.indicazione is Indicazione.TOTAL_PASSED
        assert esito.firme[0].revoca is StatoRevoca.BUONO
    # revocato (anche al livello solo crittografico, se l'informazione c'è)
    for livello in (CRITT, REV):
        for kw in ({"crl_locali": [pki.crl(revocato=True)]},
                   {"ocsp_locali": [pki.ocsp(revocato=True)]}):
            esito = await VerificatorePyHanko([pki.ca_asn1], **kw).verifica(p7m,
                                                                            livello=livello)
            assert esito.firme[0].revoca is StatoRevoca.REVOCATO, (livello, kw)
            assert esito.indicazione is not Indicazione.TOTAL_PASSED


async def test_firma_staccata_e_formati_non_supportati(pki: Pki) -> None:
    v = VerificatorePyHanko([pki.ca_asn1])
    esito = await v.verifica(b"<a><ds:Signature xmlns:ds='http://www.w3.org/2000/09/xmldsig#'"
                             b"/></a>", livello=CRITT)
    assert esito.formato is FormatoFirma.XADES
    assert esito.indicazione is Indicazione.INDETERMINATE
    assert esito.firme[0].sottoindicazione == "FORMATO_NON_SUPPORTATO"
    assert (await v.verifica(pdf_minimo(), livello=CRITT)).firmato is False


async def test_livelli_nessuna_e_rilevata(pki: Pki, p7m: bytes) -> None:
    v = VerificatorePyHanko([pki.ca_asn1])
    assert await verifica_al_livello(v, p7m, LivelloVerifica.NESSUNA) is None
    rilevata = await verifica_al_livello(v, p7m, LivelloVerifica.RILEVATA)
    assert rilevata is not None and rilevata.verificatore == "rilevazione"


def _dss(gestore) -> VerificatoreDss:  # type: ignore[no-untyped-def]
    return VerificatoreDss("http://verifica-firme:8080", "tok",
                           trasporto=httpx.MockTransport(gestore))


async def test_client_dss_e_ripiego_solo_pdf(pki: Pki, p7m: bytes, pdf: bytes) -> None:
    ricevute: list[dict] = []

    def risponde(req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content)
        ricevute.append({"auth": req.headers["authorization"], **corpo})
        esito = EsitoVerifica(FormatoFirma.CADES, LivelloVerifica(corpo["livello"]),
                              "eu-dss 6.2", firme=(FirmaVerificata(
                                  FormatoFirma.CADES, Indicazione.TOTAL_PASSED,
                                  crittografia_valida=True, catena_attendibile=True,
                                  revoca=StatoRevoca.BUONO),))
        return httpx.Response(200, json=esito.come_dict())

    esito = await _dss(risponde).verifica(p7m, livello=REV, nome_file="a.p7m")
    assert esito.verificatore == "eu-dss 6.2" and esito.livello is REV
    assert ricevute[0]["auth"] == "Bearer tok"
    assert base64.b64decode(ricevute[0]["contenuto"]) == p7m

    def giu(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("rifiutata")

    for gestore in (giu, lambda r: httpx.Response(503), lambda r: httpx.Response(200, text="x")):
        with pytest.raises(ErroreVerificatoreNonDisponibile):
            await _dss(gestore).verifica(p7m, livello=CRITT)
    combinato = VerificatoreConRipiego(_dss(giu), VerificatorePyHanko([pki.ca_asn1]))
    esito = await combinato.verifica(pdf, livello=CRITT)
    assert esito.ripiego and esito.indicazione is Indicazione.TOTAL_PASSED
    assert "non disponibile" in esito.note[-1]
    with pytest.raises(ErroreVerificatoreNonDisponibile):  # CAdES: niente ripiego
        await combinato.verifica(p7m, livello=CRITT)


def test_configurazione_da_ambiente(pki: Pki, tmp_path) -> None:  # type: ignore[no-untyped-def]
    file = tmp_path / "radici.pem"
    file.write_bytes(pki.ca_pem)
    v = crea_verificatore({"FIRME_VERIFICATORE": "pyhanko", "FIRME_RADICI_PEM": str(file)})
    assert isinstance(v, VerificatorePyHanko) and len(v.radici) == 1
    v = crea_verificatore({"FIRME_VERIFICATORE": "dss", "FIRME_DSS_URL": "http://x:8080",
                           "FIRME_DSS_TOKEN": "t", "FIRME_RADICI_PEM": str(file)})
    assert isinstance(v, VerificatoreConRipiego)
    # revisione X7: senza trust anchor pyHanko il ripiego non si attiva
    assert isinstance(crea_verificatore({"FIRME_VERIFICATORE": "dss",
                                         "FIRME_DSS_URL": "http://x:8080"}), VerificatoreDss)
    assert isinstance(crea_verificatore({"FIRME_VERIFICATORE": "dss", "FIRME_DSS_URL": "http://x",
                                         "FIRME_RIPIEGO_PDF": "false"}), VerificatoreDss)
    with pytest.raises(ValueError):
        crea_verificatore({"FIRME_VERIFICATORE": "altro"})
