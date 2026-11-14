# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Impostazioni (avvio bloccato in produzione), token di canale C2, client del
gateway (ritentativi solo sugli errori ritentabili), chiave host."""
from __future__ import annotations

import json
import os
from pathlib import Path

import asyncssh
import httpx
import pytest
from app.config import Settings, problemi_avvio
from app.infrastructure.gateway import ClientGateway, ErroreGateway, impronta_parte
from app.infrastructure.server_sftp import QuotaAccount, carica_chiave_host
from app.infrastructure.token_canale import emettitore_da_impostazioni
from pydantic import SecretStr

from libs.shared.security.jwt_rs256 import ErroreChiaviJwt, ErroreTokenNonValido, verifica_token


def test_problemi_avvio_sviluppo_senza_segreti_nessuno() -> None:
    assert problemi_avvio(Settings(env="development")) == []


def test_problemi_avvio_produzione_senza_segreti_bloccato() -> None:
    problemi = problemi_avvio(Settings(env="production"))
    assert any("SFTP_CHIAVE_HOST" in p for p in problemi)
    assert any("SFTP_TOKEN_CHIAVE_PRIVATA" in p for p in problemi)


def test_problemi_avvio_produzione_con_segreti_e_solo_lavoratore() -> None:
    s = Settings(env="production", sftp_componenti="lavoratore",
                 sftp_token_chiave_privata=SecretStr("x"))
    assert s.sftp_componenti == ["lavoratore"]
    assert problemi_avvio(s) == []


def test_r06_server_in_produzione_con_la_chiave_dei_token_non_parte() -> None:
    """R06 (VD-C-126): il server esposto non deve avere la chiave dei token di canale."""
    s = Settings(env="production", sftp_componenti="server",
                 sftp_chiave_host=SecretStr("h"), sftp_token_chiave_privata=SecretStr("x"))
    assert any("nel processo del solo server" in p for p in problemi_avvio(s))
    s = Settings(env="production", sftp_componenti="server", sftp_chiave_host=SecretStr("h"))
    assert problemi_avvio(s) == []
    # server e lavoratore insieme (sviluppo / singolo processo): la chiave serve
    s = Settings(env="production", sftp_componenti="server,lavoratore",
                 sftp_chiave_host=SecretStr("h"), sftp_token_chiave_privata=SecretStr("x"))
    assert problemi_avvio(s) == []


@pytest.mark.parametrize("variazione,frammento", [
    ({"sftp_componenti": "server,altro"}, "non ammessi"),
    ({"sftp_rsa_bit_minimi": 1024}, "2048"),
    ({"sftp_algoritmi_firma": ["ssh-rsa"]}, "SHA-1"),
    ({"lavoratore_lease_secondi": 5}, "LEASE"),
    ({"lavoratore_ritardi_secondi": []}, "RITARDI"),
])
def test_problemi_avvio_impostazioni_pericolose(variazione, frammento) -> None:
    assert any(frammento in p for p in problemi_avvio(Settings(env="test", **variazione)))


def test_componenti_da_json_e_da_virgole(monkeypatch) -> None:
    monkeypatch.setenv("SFTP_COMPONENTI", '["server"]')
    assert Settings().sftp_componenti == ["server"]
    monkeypatch.setenv("SFTP_COMPONENTI", "server, lavoratore")
    assert Settings().sftp_componenti == ["server", "lavoratore"]


T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"
U1 = "33333333-3333-3333-3333-333333333333"


def test_token_canale_claim_del_contratto_c2(emettitore) -> None:
    """R03 i1: aud «verumdoc-canale» (non il pubblico dei token utente) e tipo_token."""
    token = emettitore.token(tenant_id=T1, user_id=U1, ruolo="OPERATORE", adesso=None)
    claims = verifica_token(token, chiave_pubblica_pem=emettitore.chiave_pubblica_pem,
                            emittente="verumdoc.it", pubblico="verumdoc-canale")
    assert claims["sub"] == "servizio:sftp-ingest" and claims["canale"] == "SFTP"
    assert claims["aud"] == "verumdoc-canale" and claims["tipo_token"] == "canale"
    assert (claims["tenant_id"], claims["user_id"], claims["ruolo"]) == (T1, U1, "OPERATORE")
    assert claims["exp"] - claims["iat"] == 300
    with pytest.raises(ErroreTokenNonValido):  # non vale come token utente
        verifica_token(token, chiave_pubblica_pem=emettitore.chiave_pubblica_pem,
                       emittente="verumdoc.it", pubblico="verumdoc-api")


def test_token_canale_verificato_dalla_funzione_del_gateway(emettitore) -> None:
    """Emissione e verifica usano la stessa definizione (libs.shared.security.token_canale)."""
    from libs.shared.security.token_canale import ServizioCanale, verifica_token_canale

    token = emettitore.token(tenant_id=T1, user_id=U1, ruolo="OPERATORE")
    payload = verifica_token_canale(token, servizi=[ServizioCanale(
        "sftp-ingest", frozenset({"SFTP"}), emettitore.chiave_pubblica_pem)],
        emittente="verumdoc.it")
    assert payload["servizio"] == "sftp-ingest" and payload["tenant_id"] == T1


def test_token_canale_in_cache_e_rinnovato_prima_della_scadenza(emettitore) -> None:
    t1 = emettitore.token(tenant_id=T1, user_id=U1, ruolo="R", adesso=1_000_000)
    assert emettitore.token(tenant_id=T1, user_id=U1, ruolo="R", adesso=1_000_100) == t1
    assert emettitore.token(tenant_id=T1, user_id=U1, ruolo="R", adesso=1_000_250) != t1
    assert emettitore.token(tenant_id=T2, user_id=U1, ruolo="R", adesso=1_000_000) != t1


def test_token_canale_senza_chiave_fuori_test_non_parte() -> None:
    with pytest.raises(ErroreChiaviJwt):
        emettitore_da_impostazioni(env="development", chiave_privata="", emittente="x",
                                   durata_secondi=60)


def test_durata_del_token_fuori_contratto_impedisce_l_avvio() -> None:
    assert any("TOKEN_CANALE_DURATA" in p for p in problemi_avvio(
        Settings(env="test", token_canale_durata_secondi=7200)))
    assert not any("TOKEN_CANALE_DURATA" in p for p in problemi_avvio(Settings(env="test")))


def _client(gestore, attese: list[float]) -> ClientGateway:
    async def attendi(s: float) -> None:
        attese.append(s)
    return ClientGateway("http://gw", timeout_secondi=5, ritentativi_secondi=[1, 2],
                         transport=httpx.MockTransport(gestore), attendi=attendi)


async def test_client_errore_di_rete_ritentato_poi_sftp_201() -> None:
    chiamate = []

    def gestore(req: httpx.Request) -> httpx.Response:
        chiamate.append(req)
        raise httpx.ConnectError("rifiutata", request=req)

    attese: list[float] = []
    client = _client(gestore, attese)
    with pytest.raises(ErroreGateway) as exc:
        await client.completa("tok", "id")
    await client.chiudi()
    assert len(chiamate) == 3 and attese == [1, 2]
    assert exc.value.problema["codice"] == "SFTP-201" and exc.value.ritentabile


async def test_client_errore_non_ritentabile_subito() -> None:
    chiamate = []

    def gestore(req: httpx.Request) -> httpx.Response:
        chiamate.append(req)
        return httpx.Response(422, json={"codice": "VRS-130", "ritentabile": False,
                                         "title": "Metadati"})

    client = _client(gestore, [])
    with pytest.raises(ErroreGateway) as exc:
        await client.versa("tok", indice={}, file={}, chiave="k" * 10)
    await client.chiudi()
    assert len(chiamate) == 1 and exc.value.problema["codice"] == "VRS-130"
    assert chiamate[0].headers["Idempotency-Key"] == "k" * 10
    assert json.loads(chiamate[0].content) == {"indice": {}, "file": {}}


async def test_client_502_senza_corpo_ritentato_e_poi_riuscito() -> None:
    risposte = [httpx.Response(502, text="<html>bad gateway</html>"),
                httpx.Response(200, json={"stato": "COMPLETO"})]

    def gestore(req: httpx.Request) -> httpx.Response:
        return risposte.pop(0)

    attese: list[float] = []
    client = _client(gestore, attese)
    assert await client.completa("tok", "id") == {"stato": "COMPLETO"}
    await client.chiudi()
    assert attese == [1]


async def test_client_429_rispetta_retry_after() -> None:
    risposte = [httpx.Response(429, headers={"Retry-After": "7"},
                               json={"codice": "RATE-001", "ritentabile": True}),
                httpx.Response(200, json={"stato": "COMPLETO"})]
    attese: list[float] = []
    client = _client(lambda req: risposte.pop(0), attese)
    await client.completa("tok", "id")
    await client.chiudi()
    assert attese == [7]


async def test_client_risposta_non_json_errore_sftp_201() -> None:
    client = _client(lambda req: httpx.Response(200, text="ok"), [])
    with pytest.raises(ErroreGateway) as exc:
        await client.completa("tok", "id")
    await client.chiudi()
    assert exc.value.problema["codice"] == "SFTP-201"


async def test_client_parte_in_streaming_con_impronta(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(bytes(range(256)) * 20)
    ricevuto = {}

    async def gestore_async(req: httpx.Request) -> httpx.Response:
        corpo = b"".join([c async for c in req.stream])
        ricevuto.update(corpo=corpo, impronta=req.headers["X-Impronta-Parte"],
                        lunghezza=req.headers["Content-Length"])
        return httpx.Response(200, json={"parte": 2})

    client = ClientGateway("http://gw", timeout_secondi=5, ritentativi_secondi=[],
                           transport=httpx.MockTransport(gestore_async))
    await client.carica_parte("tok", "id", 2, percorso=f, inizio=1000, lunghezza=1500)
    await client.chiudi()
    atteso = f.read_bytes()[1000:2500]
    assert ricevuto["corpo"] == atteso and ricevuto["lunghezza"] == "1500"
    assert ricevuto["impronta"] == f"sha256={impronta_parte(f, 1000, 1500)}"


def test_impronta_parte_file_corto_errore(tmp_path: Path) -> None:
    f = tmp_path / "corto"
    f.write_bytes(b"abc")
    with pytest.raises(OSError):
        impronta_parte(f, 0, 10)


def test_chiave_host_dal_segreto_sviluppo_e_produzione(tmp_path: Path) -> None:
    chiave = asyncssh.generate_private_key("ssh-ed25519")
    pem = chiave.export_private_key().decode()
    assert carica_chiave_host(valore=pem, ambiente="production", file_sviluppo="x") \
        .get_fingerprint() == chiave.get_fingerprint()
    file_dev = tmp_path / ".chiave_host_sviluppo"
    prima = carica_chiave_host(valore="", ambiente="development", file_sviluppo=str(file_dev))
    assert file_dev.exists() and (os.stat(file_dev).st_mode & 0o777) == 0o600
    dopo = carica_chiave_host(valore="", ambiente="development", file_sviluppo=str(file_dev))
    assert prima.get_fingerprint() == dopo.get_fingerprint()
    with pytest.raises(RuntimeError, match="SFTP_CHIAVE_HOST"):
        carica_chiave_host(valore="", ambiente="production", file_sviluppo=str(file_dev))


async def test_quota_ricalcolo_e_riserva(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x" * 300)
    q = QuotaAccount(limite_byte=1000, ricalcolo_secondi=60)
    assert await q.aggiorna("acc", tmp_path, adesso=0) == 300
    assert q.riserva("acc", 700) and not q.riserva("acc", 1)
    assert q.riserva("acc", -500) and q.riserva("acc", 400)
    (tmp_path / "b").write_bytes(b"y" * 10)
    assert await q.aggiorna("acc", tmp_path, adesso=30) == 900   # non ancora ricalcolato
    assert await q.aggiorna("acc", tmp_path, adesso=61) == 310   # ricalcolato dal disco


@pytest.mark.parametrize("valore", ["ssh-ed25519, ecdsa-sha2-nistp256",
                                    '["ssh-ed25519", "ecdsa-sha2-nistp256"]'])
def test_tipi_chiave_ammessi_virgole_come_onboarding_e_json(monkeypatch, valore) -> None:
    """R03 i1: SFTP_TIPI_CHIAVE_AMMESSI ha lo stesso formato in onboarding e
    sftp-ingest (virgole); il JSON resta accettato."""
    monkeypatch.setenv("SFTP_TIPI_CHIAVE_AMMESSI", valore)
    monkeypatch.setenv("SFTP_ALGORITMI_FIRMA", "ssh-ed25519,rsa-sha2-512")
    s = Settings()
    assert s.sftp_tipi_chiave_ammessi == ["ssh-ed25519", "ecdsa-sha2-nistp256"]
    assert s.sftp_algoritmi_firma == ["ssh-ed25519", "rsa-sha2-512"]
