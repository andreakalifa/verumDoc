# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test della firma con adattatore di sviluppo e della verifica crittografica — VD-C-010.

Le firme sono CAdES/PAdES vere (pyHanko) ma NON qualificate; la verifica
ricalcola impronte e firme e rifiuta buste manipolate o scritte a mano.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json

import pytest
from app.services.firma_cms import AVVISO_SVILUPPO, ServizioFirma
from app.services.fornitori import FirmaNonConfigurata, RichiestaNonValida
from app.services.sviluppo import FornitoreFirmaSviluppo, FornitoreMarcaSviluppo, a_asn1
from asn1crypto import cms

from tests.conftest import ADESSO

CONTENUTO = b"<IndicePdA>contenuto di prova VerumDoc</IndicePdA>"


def _servizio(chiavi, *, con_radice: bool = True, marca: bool = True) -> ServizioFirma:
    return ServizioFirma(
        FornitoreFirmaSviluppo(chiavi),
        FornitoreMarcaSviluppo(chiavi) if marca else None,
        produzione=False,
        radici_sviluppo=[a_asn1(chiavi.ca_cert)] if con_radice else [],
    )


def _pdf_minimo() -> bytes:
    from pyhanko.pdf_utils import generic, writer

    w = writer.PdfFileWriter()
    w.insert_page(
        writer.PageObject(
            contents=w.add_object(generic.StreamObject(stream_data=b"BT ET")),
            media_box=[0, 0, 595, 842],
        )
    )
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_firma_cades_sviluppo_dichiara_non_qualificata(chiavi):
    """La firma di sviluppo è B-T, include il contenuto e dichiara qualificata=False con avviso."""
    esito = await _servizio(chiavi).firma(CONTENUTO, adesso=ADESSO)
    assert esito.formato == "CAdES" and esito.livello == "B-T" and esito.contenuto_incluso
    assert esito.qualificata is False
    assert AVVISO_SVILUPPO in esito.avvisi
    assert "NON QUALIFICATO" in esito.firmatario
    assert esito.data_marca is not None
    assert esito.impronta_sha256_contenuto == hashlib.sha256(CONTENUTO).hexdigest()
    ci = cms.ContentInfo.load(esito.busta)
    assert ci["content_type"].native == "signed_data"
    assert ci["content"]["encap_content_info"]["content"].native == CONTENUTO


@pytest.mark.asyncio
async def test_verifica_cades_integra_valida(chiavi):
    """Una busta appena firmata risulta valida, con marca valida e firma di sviluppo segnalata."""
    s = _servizio(chiavi)
    esito = await s.firma(CONTENUTO, adesso=ADESSO)
    v = await s.verifica(esito.busta)
    assert v["esito"] == "valida", v["motivi"]
    assert v["integra"] and v["firma_valida"] and v["catena_attendibile"]
    assert v["marca"]["valida"] and v["marca"]["attendibile"]
    assert v["firma_di_sviluppo"] is True
    assert v["certificato_dichiara_qualificato"] is False


@pytest.mark.asyncio
async def test_verifica_cades_contenuto_manipolato_non_valida(chiavi):
    """VD-C-010: cambiando un byte del contenuto incluso la verifica fallisce."""
    s = _servizio(chiavi)
    busta = (await s.firma(CONTENUTO, adesso=ADESSO)).busta
    manipolata = busta.replace(b"contenuto di prova", b"contenuto di PROVA")
    assert manipolata != busta
    v = await s.verifica(manipolata)
    assert v["esito"] == "non_valida"
    assert v["integra"] is False


@pytest.mark.asyncio
async def test_verifica_cades_valore_firma_manipolato_non_valida(chiavi):
    """Alterare il valore di firma rende la busta non valida."""
    s = _servizio(chiavi)
    busta = (await s.firma(CONTENUTO, adesso=ADESSO, con_marca=False)).busta
    ci = cms.ContentInfo.load(busta)
    firma = bytearray(ci["content"]["signer_infos"][0]["signature"].native)
    pos = busta.find(bytes(firma))
    firma[10] ^= 0xFF
    manipolata = busta[:pos] + bytes(firma) + busta[pos + len(firma) :]
    v = await s.verifica(manipolata)
    assert v["esito"] == "non_valida"
    assert v["firma_valida"] is False


@pytest.mark.asyncio
async def test_verifica_busta_json_scritta_a_mano_non_valida(chiavi):
    """La vecchia busta finta (JSON base64 con l'hash) non è più accettata."""
    finta = base64.b64encode(
        json.dumps(
            {"tipo": "CAdES-T", "hash_documento": hashlib.sha256(CONTENUTO).hexdigest()}
        ).encode()
    )
    v = await _servizio(chiavi).verifica(finta, CONTENUTO)
    assert v["esito"] == "non_valida"
    assert v["firma_valida"] is False


@pytest.mark.asyncio
async def test_verifica_cades_detached_contenuto_diverso_non_valida(chiavi):
    """Firma detached: valida col contenuto giusto, non valida con un altro o senza contenuto."""
    s = _servizio(chiavi)
    esito = await s.firma(CONTENUTO, formato="cades_detached", adesso=ADESSO)
    assert esito.contenuto_incluso is False
    assert (await s.verifica(esito.busta, CONTENUTO))["esito"] == "valida"
    altro = await s.verifica(esito.busta, CONTENUTO + b" ")
    assert altro["esito"] == "non_valida" and altro["integra"] is False
    senza = await s.verifica(esito.busta)
    assert senza["esito"] == "non_valida"
    assert "detached" in senza["motivi"][0]


@pytest.mark.asyncio
async def test_verifica_cades_radice_sconosciuta_indeterminata(chiavi, altre_chiavi):
    """Firma crittograficamente valida ma emessa da una CA non attendibile: indeterminata."""
    busta = (await _servizio(chiavi).firma(CONTENUTO, adesso=ADESSO, con_marca=False)).busta
    verificatore = _servizio(altre_chiavi)
    v = await verificatore.verifica(busta)
    assert v["integra"] and v["firma_valida"]
    assert v["catena_attendibile"] is False
    assert v["esito"] == "indeterminata"


@pytest.mark.asyncio
async def test_verifica_contenuto_fornito_diverso_da_incluso_non_valida(chiavi):
    """Busta con contenuto incluso + contenuto fornito diverso: non valida."""
    s = _servizio(chiavi)
    busta = (await s.firma(CONTENUTO, adesso=ADESSO, con_marca=False)).busta
    v = await s.verifica(busta, b"altro")
    assert v["esito"] == "non_valida"


@pytest.mark.asyncio
async def test_firma_pades_e_verifica_manipolazione(chiavi):
    """PAdES B-T su un PDF: valido; cambiando un byte firmato diventa non valido."""
    s = _servizio(chiavi)
    esito = await s.firma(_pdf_minimo(), formato="pades", adesso=ADESSO)
    assert esito.formato == "PAdES" and esito.livello == "B-T"
    v = await s.verifica(esito.busta)
    assert v["esito"] == "valida", v["motivi"]
    pos = esito.busta.find(b"/MediaBox")
    manipolato = esito.busta[:pos] + b"/MediaBoX" + esito.busta[pos + 9 :]
    v2 = await s.verifica(manipolato)
    assert v2["esito"] == "non_valida"


@pytest.mark.asyncio
async def test_firma_pades_su_non_pdf_rifiutata(chiavi):
    with pytest.raises(RichiestaNonValida):
        await _servizio(chiavi).firma(CONTENUTO, formato="pades", adesso=ADESSO)


@pytest.mark.asyncio
async def test_firma_senza_marca_configurata_rifiutata_se_richiesta(chiavi):
    """Marca richiesta ma nessuna TSA configurata: firma rifiutata (nessuna marca finta)."""
    s = _servizio(chiavi, marca=False)
    with pytest.raises(FirmaNonConfigurata) as exc:
        await s.firma(CONTENUTO, adesso=ADESSO)
    assert "marca temporale qualificata: non configurata" in exc.value.messaggio
    b_b = await s.firma(CONTENUTO, adesso=ADESSO, con_marca=False)
    assert b_b.livello == "B-B"


@pytest.mark.asyncio
async def test_marca_e_verifica_token(chiavi):
    """La marca di sviluppo è un token RFC 3161 vero, verificabile e legato all'impronta."""
    s = _servizio(chiavi)
    impronta = hashlib.sha256(CONTENUTO).digest()
    token, esito = await s.marca(impronta)
    assert esito["valida"] and esito["attendibile"] and esito["di_sviluppo"]
    assert (await s.verifica_marca(token.dump(), impronta))["valida"] is True
    altra = hashlib.sha256(b"altro").digest()
    assert (await s.verifica_marca(token.dump(), altra))["valida"] is False
    assert (await s.verifica_marca(b"spazzatura", impronta))["valida"] is False
