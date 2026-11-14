# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test dell'adattatore CSC API v2 contro un RSSP simulato — VD-C-010.

Il RSSP simulato firma davvero l'impronta con la chiave del certificato «di test
qualificato» (con QcCompliance); la busta CAdES costruita localmente deve
risultare valida e la firma dichiarata qualificata.
"""

from __future__ import annotations

import httpx
import pytest
from app.services.csc_client import ClienteCSC, FornitoreFirmaCSC
from app.services.firma_cms import ServizioFirma
from app.services.fornitori import ErroreFornitore
from app.services.sviluppo import a_asn1
from app.services.tsa_client import ClienteTSA, FornitoreMarcaRFC3161
from cryptography.hazmat.primitives import serialization

from tests.conftest import ADESSO, RSSPSimulato

CONTENUTO = b"<IndicePdA>firma remota</IndicePdA>"


def _fornitore(
    rssp: RSSPSimulato, *, pin: str = "1234", segreto: str = "segreto"
) -> FornitoreFirmaCSC:
    cliente = ClienteCSC(
        "https://rssp.example.test/csc/v2",
        "https://rssp.example.test/oauth2/token",
        "verumdoc",
        segreto,
        trasporto=httpx.MockTransport(rssp),
    )
    return FornitoreFirmaCSC(cliente, "cred-conservatore", pin=pin)


@pytest.mark.asyncio
async def test_csc_firma_cades_qualificata_con_tsa_rfc3161(pki_qualificata, tsa_simulata, chiavi):
    """Flusso completo: token, info, authorize, signHash + marca RFC 3161 → busta valida."""
    rssp = RSSPSimulato(pki_qualificata["chiave"], [pki_qualificata["cert"], pki_qualificata["ca"]])
    radici_tsa = [a_asn1(chiavi.ca_cert)]
    marca = FornitoreMarcaRFC3161(
        ClienteTSA(
            "https://tsa.example.test",
            radici=radici_tsa,
            trasporto=httpx.MockTransport(tsa_simulata),
        ),
        radici_tsa,
    )
    servizio = ServizioFirma(
        _fornitore(rssp),
        marca,
        produzione=True,
        radici_firma=[a_asn1(pki_qualificata["ca"])],
        radici_marca=radici_tsa,
    )
    esito = await servizio.firma(CONTENUTO, adesso=ADESSO)
    assert esito.qualificata is True
    assert esito.avvisi == []
    assert esito.fornitore_firma == "csc" and esito.fornitore_marca == "rfc3161"
    assert rssp.chiamate[0].endswith("/oauth2/token")
    assert any(c.endswith("/signatures/signHash") for c in rssp.chiamate)
    v = await servizio.verifica(esito.busta)
    assert v["esito"] == "valida", v["motivi"]
    assert v["certificato_dichiara_qualificato"] is True
    assert v["firma_di_sviluppo"] is False


@pytest.mark.asyncio
async def test_csc_pin_errato_errore_fornitore(pki_qualificata):
    """PIN errato: il RSSP risponde 400 e l'errore arriva al chiamante con il codice CSC."""
    rssp = RSSPSimulato(pki_qualificata["chiave"], [pki_qualificata["cert"]])
    servizio = ServizioFirma(_fornitore(rssp, pin="0000"), None, produzione=False)
    with pytest.raises(ErroreFornitore) as exc:
        await servizio.firma(CONTENUTO, adesso=ADESSO, con_marca=False)
    assert exc.value.dettagli["errore"] == "invalid_request"
    assert exc.value.dettagli["operazione"] == "credentials/authorize"


@pytest.mark.asyncio
async def test_csc_client_secret_errato(pki_qualificata):
    rssp = RSSPSimulato(pki_qualificata["chiave"], [pki_qualificata["cert"]])
    with pytest.raises(ErroreFornitore) as exc:
        await _fornitore(rssp, segreto="sbagliato").materiale()
    assert exc.value.dettagli["http"] == 401


@pytest.mark.asyncio
async def test_csc_token_riusato_tra_chiamate(pki_qualificata):
    """Il token OAuth2 si chiede una volta e si riusa finché è valido."""
    rssp = RSSPSimulato(pki_qualificata["chiave"], [pki_qualificata["cert"], pki_qualificata["ca"]])
    servizio = ServizioFirma(_fornitore(rssp), None, produzione=False)
    await servizio.firma(CONTENUTO, adesso=ADESSO, con_marca=False)
    await servizio.firma(CONTENUTO, adesso=ADESSO, con_marca=False)
    assert sum(c.endswith("/oauth2/token") for c in rssp.chiamate) == 1


@pytest.mark.asyncio
async def test_csc_certificato_non_qualificato_dichiarato(chiavi):
    """Certificato senza QcCompliance: la firma funziona ma qualificata=False con avviso."""
    rssp = RSSPSimulato(chiavi.firma_chiave, [chiavi.firma_cert, chiavi.ca_cert])
    servizio = ServizioFirma(_fornitore(rssp), None, produzione=False)
    esito = await servizio.firma(CONTENUTO, adesso=ADESSO, con_marca=False)
    assert esito.qualificata is False
    assert any("QcCompliance" in a for a in esito.avvisi)


@pytest.mark.asyncio
async def test_csc_rssp_irraggiungibile():
    def _giu(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    cliente = ClienteCSC(
        "https://rssp.example.test/csc/v2",
        "https://rssp.example.test/oauth2/token",
        "verumdoc",
        "segreto",
        trasporto=httpx.MockTransport(_giu),
    )
    with pytest.raises(ErroreFornitore, match="non raggiungibile"):
        await FornitoreFirmaCSC(cliente, "cred").materiale()


def test_csc_parametri_obbligatori():
    with pytest.raises(ValueError):
        ClienteCSC("", "https://x/token", "id", "s")
    # R13a: credenziale vuota ammessa, si ricava da credentials/list (se una sola)
    cliente = ClienteCSC("https://x/csc/v2", "https://x/token", "id", "s")
    assert FornitoreFirmaCSC(cliente, "") is not None


def test_pem_certificato_qualificato_leggibile(pki_qualificata):
    """Il certificato di test dichiara QcCompliance (prerequisito dei test sopra)."""
    from app.services.fornitori import certificato_dichiara_qualificato

    cert = a_asn1(pki_qualificata["cert"])
    assert certificato_dichiara_qualificato(cert) is True
    assert (
        pki_qualificata["cert"].public_bytes(serialization.Encoding.PEM).startswith(b"-----BEGIN")
    )
