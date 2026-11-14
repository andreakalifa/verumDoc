# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test del client RFC 3161 contro una TSA simulata via HTTP — VD-C-010, REGOLE §20.1.

La TSA simulata firma token veri con la chiave TSA di sviluppo; il client
reale li richiede via httpx (MockTransport) e li verifica prima di usarli.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from app.services.fornitori import ErroreFornitore
from app.services.sviluppo import a_asn1
from app.services.tsa_client import ClienteTSA, verifica_token

IMPRONTA = hashlib.sha256(b"radice di Merkle del blocco 42").digest()


def _cliente(tsa_simulata, radici, **kw) -> ClienteTSA:
    return ClienteTSA(
        "https://tsa.example.test/tsa",
        radici=radici,
        trasporto=httpx.MockTransport(tsa_simulata),
        **kw,
    )


@pytest.mark.asyncio
async def test_cliente_tsa_marca_valida_e_verificata(tsa_simulata, chiavi):
    """Caso felice: token con impronta giusta, firma valida e catena attendibile."""
    token = await _cliente(tsa_simulata, [a_asn1(chiavi.ca_cert)]).async_timestamp(
        IMPRONTA, "sha256"
    )
    esito = await verifica_token(token, IMPRONTA, [a_asn1(chiavi.ca_cert)])
    assert esito["valida"] and esito["attendibile"]
    assert esito["algoritmo_impronta"] == "sha256"
    assert esito["data_marca"] is not None


@pytest.mark.asyncio
async def test_cliente_tsa_nonce_ripetuto_rifiutato(tsa_simulata, chiavi):
    """Una risposta riprodotta (nonce di un'altra richiesta) è rifiutata."""
    cliente = _cliente(tsa_simulata, [a_asn1(chiavi.ca_cert)])
    await cliente.async_timestamp(IMPRONTA, "sha256")
    tsa_simulata.modalita = "replay"
    with pytest.raises(ErroreFornitore, match="nonce"):
        await cliente.async_timestamp(IMPRONTA, "sha256")


@pytest.mark.asyncio
async def test_cliente_tsa_impronta_diversa_rifiutata(tsa_simulata, chiavi):
    tsa_simulata.modalita = "impronta_altra"
    with pytest.raises(ErroreFornitore, match="impronta"):
        await _cliente(tsa_simulata, [a_asn1(chiavi.ca_cert)]).async_timestamp(IMPRONTA, "sha256")


@pytest.mark.asyncio
async def test_cliente_tsa_rifiuto_della_tsa(tsa_simulata, chiavi):
    tsa_simulata.modalita = "rifiuto"
    with pytest.raises(ErroreFornitore) as exc:
        await _cliente(tsa_simulata, [a_asn1(chiavi.ca_cert)]).async_timestamp(IMPRONTA, "sha256")
    assert exc.value.dettagli["stato"] == "rejection"


@pytest.mark.asyncio
async def test_cliente_tsa_errore_http(tsa_simulata, chiavi):
    tsa_simulata.modalita = "errore_http"
    with pytest.raises(ErroreFornitore, match="HTTP 500"):
        await _cliente(tsa_simulata, [a_asn1(chiavi.ca_cert)]).async_timestamp(IMPRONTA, "sha256")


@pytest.mark.asyncio
async def test_cliente_tsa_tipo_contenuto_errato(tsa_simulata, chiavi):
    tsa_simulata.modalita = "tipo_errato"
    with pytest.raises(ErroreFornitore, match="tipo di contenuto"):
        await _cliente(tsa_simulata, [a_asn1(chiavi.ca_cert)]).async_timestamp(IMPRONTA, "sha256")


@pytest.mark.asyncio
async def test_cliente_tsa_radice_non_attendibile_rifiutata(tsa_simulata, altre_chiavi):
    """Con radici configurate, un token di una TSA diversa è rifiutato."""
    with pytest.raises(ErroreFornitore, match="radici"):
        await _cliente(tsa_simulata, [a_asn1(altre_chiavi.ca_cert)]).async_timestamp(
            IMPRONTA, "sha256"
        )


@pytest.mark.asyncio
async def test_cliente_tsa_politica_diversa_rifiutata(tsa_simulata, chiavi):
    """La politica configurata deve coincidere con quella del token."""
    with pytest.raises(ErroreFornitore):
        await _cliente(
            tsa_simulata, [a_asn1(chiavi.ca_cert)], politica_oid="1.2.3.4.5"
        ).async_timestamp(IMPRONTA, "sha256")


@pytest.mark.asyncio
async def test_cliente_tsa_irraggiungibile():
    def _giu(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("rifiutata", request=request)

    cliente = ClienteTSA("https://tsa.example.test/tsa", trasporto=httpx.MockTransport(_giu))
    with pytest.raises(ErroreFornitore, match="non raggiungibile"):
        await cliente.async_timestamp(IMPRONTA, "sha256")


@pytest.mark.asyncio
async def test_cliente_tsa_invia_credenziali_basic(tsa_simulata, chiavi):
    """Le credenziali configurate viaggiano come Basic auth (mai nei log)."""
    visti: list[str | None] = []

    def _con_auth(request: httpx.Request) -> httpx.Response:
        visti.append(request.headers.get("authorization"))
        return tsa_simulata(request)

    cliente = ClienteTSA(
        "https://tsa.example.test/tsa",
        utente="u",
        password="p",  # noqa: S106 — credenziale finta della TSA simulata
        trasporto=httpx.MockTransport(_con_auth),
    )
    await cliente.async_timestamp(IMPRONTA, "sha256")
    assert visti[0] == "Basic dTpw"
