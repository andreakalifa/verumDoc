# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13a, rilievi della revisione sul servizio di firma.

  (2) segreti legati agli host del Secret: chi cambia in console ``url_token`` o
      l'indirizzo della TSA verso un host esterno non porta fuori client_secret o
      password (fornitore non costruito; il client HTTP ricontrolla a ogni
      richiesta, prima che parta); pinning del Secret che prevale sulla console;
  (3) in produzione https obbligatorio per chi ha credenziali, TSA comprese;
  (4) identità attesa del certificato (impronta o organizationIdentifier),
      obbligatoria in produzione, controllata sul materiale e sulla firma
      prodotta; ``credential_id`` obbligatorio in produzione;
  (6) token della TSA malformato → errore del fornitore, ripiego sulla TSA successiva.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from app.config import Settings
from app.services.csc_client import ClienteCSC
from app.services.fornitori import ErroreFornitore, FirmaProdottaNonValida, IdentitaAttesa
from app.services.registro_fornitori import Contesto, costruisci_da_registro, radici_da_pem
from app.services.tsa_client import ClienteTSA, FornitoreMarcaRFC3161, MarcaConRipiego
from asn1crypto import cms, tsp

from libs.shared.firma_qualificata.configurazione import (
    ConfigurazioneFornitoreNonValida,
    FornitoreRegistrato,
    controlla_deploy,
    leggi_segreti,
    valida_configurazione,
    vincoli_deploy,
)
from tools.simulatore_firma.app import Simulatore, configurazione_console, crea_app

from .test_r13a_simulatore_e2e import servizio_da


class Registratore(httpx.AsyncBaseTransport):
    """Trasporto che registra le richieste (nessuna deve partire)."""

    def __init__(self) -> None:
        self.richieste: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.richieste.append(request)
        return httpx.Response(200, json={"access_token": "t", "expires_in": 300})


def _costruisci(sim: Simulatore, *, produzione: bool = False, segreti: dict | None = None,
                firma: dict | None = None, marca: dict | None = None):
    conf = configurazione_console(sim)
    trasporto = httpx.ASGITransport(app=crea_app(sim))
    fornitori = [
        FornitoreRegistrato("simulatore-csc", "FIRMA", "CSC", "Simulatore", 1,
                            {**conf["firma"]["configurazione"], **(firma or {})}),
        FornitoreRegistrato("simulatore-tsa", "MARCA", "RFC3161", "TSA", 1,
                            {**conf["marca"]["configurazione"], **(marca or {})}),
    ]
    cfg = Settings(env="production" if produzione else "test",
                   firma_segreti_json=json.dumps(segreti if segreti is not None
                                                 else conf["segreti"]))
    return costruisci_da_registro(fornitori, cfg, adesso=datetime.now(UTC),
                                  trasporti={"simulatore-csc": trasporto,
                                             "simulatore-tsa": trasporto},
                                  trasporto_revoca=trasporto)


# ── (2) segreti legati agli host ─────────────────────────────────────

async def test_url_token_verso_host_esterno_fornitore_non_costruito():
    sim = Simulatore()
    esito = _costruisci(sim, firma={"url_token": "http://esterno.example/token"})
    assert "simulatore-csc" in esito.problemi
    assert any("host non fra quelli legati" in m for m in esito.problemi["simulatore-csc"])
    assert esito.servizio.fornitori_firma == []


async def test_segreti_senza_host_nel_secret_fornitore_non_costruito():
    sim = Simulatore()
    segreti = {"simulatore-csc": {"client_secret": sim.client_secret, "pin": sim.pin}}
    esito = _costruisci(sim, segreti=segreti)
    assert any("«host»" in m for m in esito.problemi["simulatore-csc"])


async def test_tsa_con_password_verso_host_esterno_non_costruita():
    sim = Simulatore()
    conf = configurazione_console(sim)
    segreti = {**conf["segreti"], "simulatore-tsa": {"host": ["simulatore.locale"],
                                                     "utente": "u", "password": "p"}}
    esito = _costruisci(sim, segreti=segreti, marca={"url": "http://esterno.example/tsa"})
    assert any("host non fra quelli legati" in m for m in esito.problemi["simulatore-tsa"])


async def test_cliente_csc_non_spedisce_il_segreto_a_un_host_non_ammesso():
    registratore = Registratore()
    cliente = ClienteCSC("https://csc.prestatore.example/csc/v2",
                         "https://esterno.example/token", "id", "segreto-da-non-spedire",
                         trasporto=registratore, host_ammessi=("csc.prestatore.example",))
    with pytest.raises(ErroreFornitore, match="host non legato"):
        await cliente.chiama("credentials/list", {})
    assert registratore.richieste == []


async def test_cliente_tsa_non_spedisce_la_password_a_un_host_non_ammesso():
    registratore = Registratore()
    cliente = ClienteTSA("https://esterno.example/tsa", utente="u", password="p",  # noqa: S106
                         trasporto=registratore, host_ammessi=("tsa.prestatore.example",))
    with pytest.raises(ErroreFornitore, match="host non legato"):
        await cliente.async_timestamp(hashlib.sha256(b"x").digest(), "sha256")
    assert registratore.richieste == []


def test_pinning_e_host_dal_secret_prevalgono_sulla_console():
    pin_secret, pin_console = "aa" * 32, "bb" * 32
    testo = json.dumps({"c": {"host": ["csc.example"], "pin_spki_sha256": [pin_secret],
                              "client_secret": "s"}})
    assert leggi_segreti(testo, "c") == {"client_secret": "s"}  # host e pin non sono segreti
    c = Contesto(Settings(env="test"), datetime.now(UTC), testo)
    rete = c.rete(FornitoreRegistrato("c", "FIRMA", "CSC", "c", 1,
                                      {"pin_spki_sha256": [pin_console]}))
    assert rete["host_ammessi"] == ("csc.example",)
    assert rete["pin_spki_sha256"] == (pin_secret,)
    with pytest.raises(ValueError, match="host"):
        vincoli_deploy(json.dumps({"c": {"host": ["https://csc.example:443/x"]}}), "c")


# ── (3) https in produzione con credenziali ──────────────────────────

def test_produzione_https_obbligatorio_con_credenziali_tsa_comprese():
    vincoli = {"host": ("tsa.example",), "pin_spki_sha256": ()}
    motivi = controlla_deploy("RFC3161", {"url": "http://tsa.example/tsa"},
                              {"password": "p"}, vincoli, produzione=True)
    assert any("solo https" in m for m in motivi)
    # senza credenziali una TSA http resta ammessa (RFC 3161 su http è comune)
    assert controlla_deploy("RFC3161", {"url": "http://tsa.example/tsa"}, {}, vincoli,
                            produzione=True) == []
    assert controlla_deploy("RFC3161", {"url": "https://tsa.example/tsa"}, {"password": "p"},
                            vincoli, produzione=True) == []


async def test_produzione_cliente_con_credenziali_rifiuta_http():
    registratore = Registratore()
    cliente = ClienteTSA("http://tsa.example/tsa", utente="u", password="p",  # noqa: S106
                         trasporto=registratore, host_ammessi=("tsa.example",),
                         solo_https=True)
    with pytest.raises(ErroreFornitore, match="non https"):
        await cliente.async_timestamp(hashlib.sha256(b"x").digest(), "sha256")
    assert registratore.richieste == []


# ── (4) identità attesa del certificato ──────────────────────────────

async def test_certificato_diverso_da_quello_atteso_firma_rifiutata():
    servizio = servizio_da(Simulatore(), certificati_attesi_sha256=["00" * 32])
    with pytest.raises((FirmaProdottaNonValida, ErroreFornitore)):
        await servizio.firma(b"<IPdA/>", formato="cades", livello="B-T",
                             adesso=datetime.now(UTC))


async def test_organization_identifier_diverso_firma_rifiutata():
    servizio = servizio_da(Simulatore(), certificati_attesi_sha256=[],
                           organization_identifier="VATIT-00000000000")
    with pytest.raises((FirmaProdottaNonValida, ErroreFornitore)):
        await servizio.firma(b"<IPdA/>", formato="cades", livello="B-T",
                             adesso=datetime.now(UTC))


async def test_identita_attesa_controllata_sul_materiale():
    sim = Simulatore()
    servizio = servizio_da(sim)
    fornitore = servizio.fornitori_firma[0]
    mat = await fornitore.materiale()
    impronta = hashlib.sha256(mat.certificato.dump()).hexdigest()
    IdentitaAttesa(frozenset({impronta})).controlla(mat.certificato)
    with pytest.raises(FirmaProdottaNonValida):
        IdentitaAttesa(frozenset({"00" * 32})).controlla(mat.certificato)
    with pytest.raises(FirmaProdottaNonValida):
        IdentitaAttesa(organization_identifier="VATIT-1").controlla(mat.certificato)


def test_produzione_identita_e_credential_id_obbligatori():
    sim = Simulatore()
    base = {**configurazione_console(sim)["firma"]["configurazione"],
            "url_base": "https://csc.example/v2", "url_token": "https://csc.example/token"}
    valida_configurazione("FIRMA", "CSC", base, produzione=True)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="identità attesa"):
        valida_configurazione("FIRMA", "CSC", {**base, "certificati_attesi_sha256": []},
                              produzione=True)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="credential_id"):
        valida_configurazione("FIRMA", "CSC", {**base, "credential_id": ""}, produzione=True)
    # fuori produzione l'identità è facoltativa
    valida_configurazione("FIRMA", "CSC", {**base, "certificati_attesi_sha256": []},
                          produzione=False)


# ── (6) token della TSA malformato ───────────────────────────────────

class TsaMalformata(httpx.AsyncBaseTransport):
    """Risposta «granted» con un token che non è un SignedData."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        risposta = tsp.TimeStampResp({
            "status": {"status": "granted"},
            "time_stamp_token": cms.ContentInfo({"content_type": "data",
                                                 "content": b"non un token"})})
        return httpx.Response(200, content=risposta.dump(),
                              headers={"content-type": "application/timestamp-reply"})


async def test_token_malformato_errore_del_fornitore_e_ripiego():
    cliente = ClienteTSA("http://tsa.rotta/tsa", trasporto=TsaMalformata())
    with pytest.raises(ErroreFornitore, match="non decodificabile"):
        await cliente.async_timestamp(hashlib.sha256(b"x").digest(), "sha256")
    buona = Simulatore()
    radici = list(radici_da_pem(buona.pki.ca_pem))
    marca = MarcaConRipiego([
        FornitoreMarcaRFC3161(cliente, [], codice="rotta"),
        FornitoreMarcaRFC3161(ClienteTSA("http://simulatore.locale/tsa", radici=radici,
                                         trasporto=httpx.ASGITransport(app=crea_app(buona))),
                              radici, codice="buona")])
    assert await marca.marca(hashlib.sha256(b"x").digest(), "sha256")
    assert marca.ultimo.codice == "buona"


async def test_materiale_csc_con_certificato_non_atteso_rifiutato():
    """Il controllo vale già alla lettura della credenziale (credentials/info)."""
    sim = Simulatore()
    servizio = servizio_da(sim, certificati_attesi_sha256=["00" * 32])
    with pytest.raises(FirmaProdottaNonValida, match="non è fra quelli attesi"):
        await servizio.fornitori_firma[0].materiale()
