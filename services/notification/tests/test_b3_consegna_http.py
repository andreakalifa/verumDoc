# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Invio HTTP delle consegne con un destinatario HTTPS vero — contratto C3 (agente R03 b3).

Copre: firma verificabile dal destinatario con le intestazioni del contratto,
indirizzo fissato dopo la risoluzione con certificato verificato sul NOME,
SSRF alla risoluzione (anche con un solo indirizzo privato fra molti),
risposte non 2xx, reindirizzamenti non seguiti, timeout, certificato errato.
"""

from __future__ import annotations

import json
import time

import pytest
from app.domain.webhook import valida_url, verifica_firma
from app.infrastructure.consegna_http import InviatoreWebhook

from tests.b3_supporto import avvia_destinatario

NOME = "ricevitore.cliente-prova.it"


@pytest.fixture
def destinatario(tmp_path):
    d = avvia_destinatario(tmp_path, [NOME, "localhost", "127.0.0.1"])
    yield d
    d.ferma()


def _risolutore(mappa: dict[str, list[str]]):
    async def risolvi(host: str, porta: int) -> list[str]:
        return mappa[host]
    return risolvi


def _inviatore(dest, *, privati=True, mappa=None, timeout=3.0) -> InviatoreWebhook:
    return InviatoreWebhook(timeout_connessione=2.0, timeout=timeout, consenti_privati=privati,
                            ca_pem=dest.pem, risolutore=_risolutore(mappa or {NOME: ["127.0.0.1"]}))


async def test_invia_con_nome_fissato_su_ip_e_firma_verificabile(destinatario) -> None:
    """Connessione all'IP risolto, Host e certificato verificati sul nome dichiarato."""
    from app.domain.webhook import corpo_consegna, intestazione_firma

    inviatore = _inviatore(destinatario)
    try:
        dest = valida_url(f"https://{NOME}:{destinatario.porta}/eventi?x=1",
                          lunghezza_massima=2048, consenti_privati=True)
        corpo = corpo_consegna({"id": 7, "tipo": "pdd.pronto"})
        adesso = int(time.time())
        esito = await inviatore.invia(dest, corpo, {
            "VerumDoc-Firma": intestazione_firma("whsec_prova", adesso, corpo),
            "VerumDoc-Evento": "pdd.pronto", "VerumDoc-Id-Consegna": "11",
            "VerumDoc-Id-Evento": "7"})
    finally:
        await inviatore.chiudi()
    assert esito.consegnata and esito.stato_http == 200
    r = destinatario.ricevute[0]
    assert r.percorso == "/eventi?x=1"
    assert r.intestazioni["Host"] == f"{NOME}:{destinatario.porta}"
    assert r.intestazioni["VerumDoc-Evento"] == "pdd.pronto"
    assert r.intestazioni["VerumDoc-Id-Consegna"] == "11"
    assert r.intestazioni["Content-Type"] == "application/json"
    assert json.loads(r.corpo) == {"id": 7, "tipo": "pdd.pronto"}
    assert verifica_firma("whsec_prova", r.intestazioni["VerumDoc-Firma"], r.corpo,
                          adesso=int(time.time()))


async def test_invia_risoluzione_verso_indirizzo_privato_bloccata(destinatario) -> None:
    """SSRF: basta un indirizzo non ammesso fra quelli risolti; nessuna richiesta parte."""
    inviatore = _inviatore(destinatario, privati=False,
                           mappa={NOME: ["93.184.216.34", "127.0.0.1"]})
    try:
        dest = valida_url(f"https://{NOME}:{destinatario.porta}/x", lunghezza_massima=2048)
        esito = await inviatore.invia(dest, b"{}", {})
    finally:
        await inviatore.chiudi()
    assert (esito.consegnata, esito.codice, esito.ritentabile) == (False, "WH-104", False)
    assert "127.0.0.1" not in (esito.messaggio or "")
    assert destinatario.ricevute == []


@pytest.mark.parametrize(("stato", "codice"), [(500, "WH-103"), (404, "WH-103"),
                                               (302, "WH-103")])
async def test_invia_risposta_non_2xx(destinatario, stato, codice) -> None:
    destinatario.stato = stato
    if stato == 302:
        destinatario.intestazioni_risposta = {"Location": "https://169.254.169.254/"}
    inviatore = _inviatore(destinatario)
    try:
        dest = valida_url(f"https://{NOME}:{destinatario.porta}/x", lunghezza_massima=2048,
                          consenti_privati=True)
        esito = await inviatore.invia(dest, b"{}", {})
    finally:
        await inviatore.chiudi()
    assert (esito.consegnata, esito.codice, esito.stato_http, esito.ritentabile) == (
        False, codice, stato, True)
    assert len(destinatario.ricevute) == 1  # il reindirizzamento NON è stato seguito


async def test_invia_timeout(destinatario) -> None:
    destinatario.attesa = 1.5
    inviatore = _inviatore(destinatario, timeout=0.5)
    try:
        dest = valida_url(f"https://{NOME}:{destinatario.porta}/x", lunghezza_massima=2048,
                          consenti_privati=True)
        esito = await inviatore.invia(dest, b"{}", {})
    finally:
        await inviatore.chiudi()
    assert (esito.consegnata, esito.codice) == (False, "WH-101")


async def test_invia_certificato_per_altro_nome_rifiutato(destinatario) -> None:
    """Il nome fissato su IP non disattiva la verifica TLS: nome diverso dal
    certificato → WH-102."""
    inviatore = _inviatore(destinatario, mappa={"altro.cliente-prova.it": ["127.0.0.1"]})
    try:
        dest = valida_url(f"https://altro.cliente-prova.it:{destinatario.porta}/x",
                          lunghezza_massima=2048, consenti_privati=True)
        esito = await inviatore.invia(dest, b"{}", {})
    finally:
        await inviatore.chiudi()
    assert (esito.consegnata, esito.codice) == (False, "WH-102")
    assert destinatario.ricevute == []


async def test_invia_nome_non_risolto() -> None:
    async def non_risolve(host: str, porta: int) -> list[str]:
        raise OSError("NXDOMAIN")

    inviatore = InviatoreWebhook(timeout_connessione=1, timeout=1, risolutore=non_risolve)
    try:
        esito = await inviatore.invia(
            valida_url("https://non-esiste.cliente-prova.it/x", lunghezza_massima=2048), b"{}", {})
    finally:
        await inviatore.chiudi()
    assert (esito.consegnata, esito.codice, esito.ritentabile) == (False, "WH-102", True)
