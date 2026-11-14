# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13a — firma end-to-end con il simulatore CSC v2 + TSA (tools/simulatore_firma),
via trasporto ASGI: registro dei fornitori → ServizioFirma → busta verificata.

Livelli: CAdES B-T, B-LT (certificati e revoche OCSP/CRL inclusi), B-LTA
(archive-time-stamp-v3), firma staccata, PAdES B-LT/B-LTA, rimarcatura.
"""
from __future__ import annotations

import io
from datetime import UTC, datetime

import httpx
import pytest
from app.config import Settings
from app.services.fornitori import FirmaProdottaNonValida
from app.services.registro_fornitori import costruisci_da_registro
from asn1crypto import cms

from libs.shared.firma_qualificata.configurazione import FornitoreRegistrato
from tools.simulatore_firma.app import Simulatore, configurazione_console, crea_app

pytestmark = pytest.mark.asyncio


def _pdf() -> bytes:
    from pyhanko.pdf_utils import generic, writer

    w = writer.PdfFileWriter()
    w.insert_page(writer.PageObject(
        contents=w.add_object(generic.StreamObject(stream_data=b"BT ET")),
        media_box=[0, 0, 595, 842]))
    uscita = io.BytesIO()
    w.write(uscita)
    return uscita.getvalue()


@pytest.fixture(scope="module")
def simulatore() -> Simulatore:
    return Simulatore()


def servizio_da(sim: Simulatore, *, produzione: bool = False, **sovrascritture):
    conf = configurazione_console(sim)
    trasporto = httpx.ASGITransport(app=crea_app(sim))
    fornitori = [
        FornitoreRegistrato("simulatore-csc", "FIRMA", "CSC", "Simulatore", 1,
                            {**conf["firma"]["configurazione"], **sovrascritture}),
        FornitoreRegistrato("simulatore-tsa", "MARCA", "RFC3161", "TSA simulata", 1,
                            conf["marca"]["configurazione"]),
    ]
    import json

    cfg = Settings(env="production" if produzione else "test",
                   firma_segreti_json=json.dumps(conf["segreti"]))
    esito = costruisci_da_registro(
        fornitori, cfg, adesso=datetime.now(UTC),
        trasporti={"simulatore-csc": trasporto, "simulatore-tsa": trasporto},
        trasporto_revoca=trasporto)
    assert esito.problemi == {}, esito.problemi
    return esito.servizio


async def test_cades_b_t_qualificata_e_verificata(simulatore):
    servizio = servizio_da(simulatore)
    esito = await servizio.firma(b"<IPdA/>", formato="cades", livello="B-T",
                                 adesso=datetime.now(UTC))
    assert esito.verificata and esito.qualificata and esito.livello == "B-T"
    assert esito.codice_fornitore == "simulatore-csc"
    assert esito.certificato_scadenza and esito.marca_scadenza
    verifica = await servizio.verifica(esito.busta)
    assert verifica["esito"] == "valida", verifica


@pytest.mark.parametrize("formato", ["cades", "cades_detached"])
async def test_cades_b_lt_include_certificati_e_revoche(simulatore, formato):
    servizio = servizio_da(simulatore)
    contenuto = b"<RdV/>" * 50
    esito = await servizio.firma(contenuto, formato=formato, livello="B-LT",
                                 adesso=datetime.now(UTC))
    sd = cms.ContentInfo.load(esito.busta)["content"]
    assert len(sd["certificates"]) >= 3  # firmatario, CA, TSA
    assert sd["crls"] and len(sd["crls"]) >= 1
    assert esito.livello == "B-LT" and esito.verificata


async def test_cades_b_lta_con_archive_time_stamp_v3(simulatore):
    from app.services import formati_lt

    servizio = servizio_da(simulatore)
    contenuto = b"<IPdA>contenuto</IPdA>"
    esito = await servizio.firma(contenuto, formato="cades", livello="B-LTA",
                                 adesso=datetime.now(UTC))
    ci = cms.ContentInfo.load(esito.busta)
    esiti = formati_lt.verifica_ats_v3(ci, contenuto)
    assert len(esiti) == 1 and esiti[0]["ok"]
    # rimarcatura: una seconda marca d'archivio che copre la prima
    rimarcata = await servizio.rimarca(esito.busta, None, adesso=datetime.now(UTC))
    ci2 = cms.ContentInfo.load(rimarcata.busta)
    esiti2 = formati_lt.verifica_ats_v3(ci2, contenuto)
    assert len(esiti2) == 2 and all(e["ok"] for e in esiti2)
    # manomissione del contenuto: la marca d'archivio non torna
    assert not formati_lt.verifica_ats_v3(ci2, contenuto + b"x")[0]["ok"]


async def test_pades_b_lt_e_b_lta(simulatore):
    servizio = servizio_da(simulatore)
    for livello in ("B-LT", "B-LTA"):
        esito = await servizio.firma(_pdf(), formato="pades", livello=livello,
                                     adesso=datetime.now(UTC))
        assert esito.verificata and esito.formato == "PAdES"
        assert b"/DSS" in esito.busta
        if livello == "B-LTA":
            assert b"/DocTimeStamp" in esito.busta
        verifica = await servizio.verifica(esito.busta)
        assert verifica["esito"] in ("valida", "indeterminata"), (livello, verifica["motivi"])


async def test_sha384(simulatore):
    servizio = servizio_da(simulatore)
    esito = await servizio.firma(b"x", formato="cades", livello="B-T", algoritmo="sha384",
                                 adesso=datetime.now(UTC))
    assert esito.algoritmo == "sha384" and esito.verificata


async def test_radici_sbagliate_in_produzione_firma_rifiutata(simulatore, altre_chiavi):
    """La firma prodotta non si accetta se non risale alle radici del prestatore
    configurate (es. un simulatore scambiato per il prestatore vero)."""
    from cryptography.hazmat.primitives import serialization

    estranea = altre_chiavi.ca_cert.public_bytes(serialization.Encoding.PEM).decode()
    servizio = servizio_da(simulatore, radici_pem=estranea)
    servizio._radici_marca = []
    with pytest.raises(FirmaProdottaNonValida):
        await servizio.firma(b"x", formato="cades", livello="B-B", adesso=datetime.now(UTC))


async def test_b_lt_senza_informazioni_di_revoca_rifiutata():
    """B-LT: se OCSP e CRL non si ottengono la firma non si accetta (anche senza
    radici configurate, fuori produzione)."""
    sim = Simulatore(guasti={"ocsp": [500] * 20, "crl": [500] * 20})
    servizio = servizio_da(sim, radici_pem="")
    with pytest.raises(FirmaProdottaNonValida, match="revoca"):
        await servizio.firma(b"x", formato="cades", livello="B-LT", adesso=datetime.now(UTC))
    # B-T invece non chiede le revoche
    assert (await servizio.firma(b"x", formato="cades", livello="B-T",
                                 adesso=datetime.now(UTC))).verificata


async def test_b_lt_firmatario_revocato_rifiutato():
    sim = Simulatore()
    from cryptography.x509 import load_der_x509_certificate

    sim.pki.revocati.add(load_der_x509_certificate(sim.pki.der(sim.pki.firma)).serial_number)
    with pytest.raises(FirmaProdottaNonValida):
        await servizio_da(sim).firma(b"x", formato="cades", livello="B-LT",
                                     adesso=datetime.now(UTC))
