# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Stato onesto e divieti in produzione — VD-C-010, REGOLE §19.4 e §20.2.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from app.config import Settings
from app.main import app
from app.routers.signature import reimposta_servizio
from app.services.firma_cms import ServizioFirma
from app.services.fornitori import FirmaNonConfigurata, FornitoreVietatoInProduzione
from app.services.registro import costruisci_servizio, problemi_configurazione, stato_servizio
from app.services.sviluppo import FornitoreFirmaSviluppo, FornitoreMarcaSviluppo
from httpx import ASGITransport, AsyncClient

from tests.conftest import ADESSO, RSSPSimulato


@pytest.mark.asyncio
async def test_stato_non_configurato_dichiara_firma_qualificata_non_configurata():
    """Senza fornitori: «firma qualificata: non configurata» e production_ready=false."""
    cfg = Settings(env="development")
    servizio = costruisci_servizio(cfg, adesso=ADESSO)
    stato = await stato_servizio(servizio, cfg)
    assert stato["firma_qualificata"] == "non configurata"
    assert stato["firma"]["stato"].startswith("firma qualificata: non configurata")
    assert stato["marca"]["stato"].startswith("marca temporale qualificata: non configurata")
    assert stato["production_ready"] is False
    assert "eIDAS attiva" not in str(stato)


@pytest.mark.asyncio
async def test_firma_non_configurata_rifiutata():
    servizio = costruisci_servizio(Settings(env="development"), adesso=ADESSO)
    with pytest.raises(FirmaNonConfigurata):
        await servizio.firma(b"x", adesso=ADESSO, con_marca=False)


@pytest.mark.asyncio
async def test_stato_sviluppo_non_qualificato(tmp_path):
    cfg = Settings(
        env="development",
        firma_fornitore="sviluppo",
        marca_fornitore="sviluppo",
        sviluppo_dir_chiavi=str(tmp_path / "chiavi"),
    )
    stato = await stato_servizio(costruisci_servizio(cfg, adesso=ADESSO), cfg)
    assert stato["firma"]["qualificata"] is False
    assert "NON qualificata" in stato["firma"]["stato"]
    assert stato["firma_qualificata"].startswith("non configurata")
    assert stato["production_ready"] is False
    # Le chiavi persistite si riusano: stesso certificato al secondo avvio
    s1 = costruisci_servizio(cfg, adesso=ADESSO)
    s2 = costruisci_servizio(cfg, adesso=ADESSO)
    m1 = await s1.fornitore_firma.materiale()
    m2 = await s2.fornitore_firma.materiale()
    assert m1.certificato.dump() == m2.certificato.dump()


def test_costruzione_sviluppo_in_produzione_rifiutata():
    """In produzione il servizio di sviluppo non si costruisce (nessun fallback)."""
    cfg = Settings(env="production", firma_fornitore="sviluppo", marca_fornitore="non_configurato")
    with pytest.raises(FornitoreVietatoInProduzione):
        costruisci_servizio(cfg, adesso=ADESSO)
    assert "FIRMA_FORNITORE=sviluppo vietato in produzione" in problemi_configurazione(cfg)


@pytest.mark.asyncio
async def test_servizio_produzione_rifiuta_firma_di_sviluppo(chiavi):
    """Difesa in profondità: anche un servizio già costruito rifiuta di firmare in produzione."""
    servizio = ServizioFirma(
        FornitoreFirmaSviluppo(chiavi), FornitoreMarcaSviluppo(chiavi), produzione=True
    )
    with pytest.raises(FornitoreVietatoInProduzione):
        await servizio.firma(b"x", adesso=ADESSO)
    with pytest.raises(FornitoreVietatoInProduzione):
        await servizio.marca(b"\x00" * 32)


def test_controlla_avvio_blocca_produzione(monkeypatch):
    """Il servizio non parte in produzione con l'adattatore di sviluppo."""
    import app.main as main

    monkeypatch.setattr(main, "settings", Settings(env="production", firma_fornitore="sviluppo"))
    with pytest.raises(RuntimeError, match="sviluppo"):
        main.controlla_avvio()


def test_problemi_configurazione_csc_incompleta():
    problemi = problemi_configurazione(
        Settings(env="production", firma_fornitore="csc", csc_url_base="http://rssp")
    )
    testo = " ".join(problemi)
    assert "CSC_URL_TOKEN" in testo and "CSC_CREDENTIAL_ID" in testo
    assert "non https" in testo
    assert "FIRMA_RADICI_PEM" in testo


def test_problemi_configurazione_tsa_senza_radici_in_produzione():
    problemi = problemi_configurazione(
        Settings(env="production", marca_fornitore="rfc3161", tsa_url="https://tsa")
    )
    assert any("TSA_RADICI_PEM" in p for p in problemi)


@pytest.mark.asyncio
async def test_stato_csc_qualificato(pki_qualificata, tmp_path):
    """Con CSC configurato e certificato con QcCompliance lo stato lo riporta, senza segreti."""
    from cryptography.hazmat.primitives import serialization

    radici = tmp_path / "radici.pem"
    radici.write_bytes(pki_qualificata["ca"].public_bytes(serialization.Encoding.PEM))
    cfg = Settings(
        env="development",
        firma_fornitore="csc",
        csc_url_base="https://rssp.example.test/csc/v2",
        csc_url_token="https://rssp.example.test/oauth2/token",  # noqa: S106
        csc_client_id="verumdoc",
        csc_client_secret="segreto",  # noqa: S106 — segreto finto del RSSP simulato
        csc_credential_id="cred",
        csc_pin="1234",
        firma_radici_pem=str(radici),
    )
    rssp = RSSPSimulato(pki_qualificata["chiave"], [pki_qualificata["cert"], pki_qualificata["ca"]])
    servizio = costruisci_servizio(cfg, adesso=ADESSO, trasporto_csc=httpx.MockTransport(rssp))
    stato = await stato_servizio(servizio, cfg)
    assert stato["firma"]["qualificata"] is True
    assert stato["firma_qualificata"].startswith("configurata")
    assert stato["production_ready"] is False  # manca la marca
    assert "segreto" not in str(stato) and "1234" not in str(stato)


@pytest.mark.asyncio
async def test_api_produzione_con_sviluppo_risponde_409(chiavi, token_servizio):
    """Via HTTP: in produzione la firma di sviluppo è rifiutata con codice esplicito."""
    reimposta_servizio(
        ServizioFirma(
            FornitoreFirmaSviluppo(chiavi), FornitoreMarcaSviluppo(chiavi), produzione=True
        )
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/signature/firma",
                json={"content_b64": base64.b64encode(b"x").decode()},
                headers={"Authorization": f"Bearer {token_servizio}"},
            )
        assert r.status_code == 409
        assert r.json()["codice"] == "FIRMA-409-SVILUPPO-IN-PRODUZIONE"
    finally:
        reimposta_servizio(None)
