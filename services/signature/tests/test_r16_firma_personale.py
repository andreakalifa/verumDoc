# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R16 — firma PERSONALE con OTP del responsabile (VD-C-166) e limite delle prove condiviso fra
le repliche (VD-C-168), provati con il simulatore CSC v2 (credenziale personale con OTP
online monouso) e con Redis vero.

Casi: OTP inviato e firma CAdES B-T verificata con il certificato della PERSONA; OTP errato,
OTP già usato, nessun OTP → rifiuto senza firma; credenziale con un certificato diverso da
quello atteso → nessuna busta; il sigillo del conservatore non vale come firma personale;
fornitore non attivo; ruoli, ruolo personalizzato e chiave API rifiutati; limite per persona;
limite delle prove condiviso fra due «repliche» (due client Redis) e atomico in parallelo.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from app.config import Settings
from app.services.fornitori import (
    ErroreFornitore,
    FirmaProdottaNonValida,
    FornitoreNonRegistrato,
)
from app.services.registro_fornitori import GestoreConfigurazione
from asn1crypto import cms

from libs.shared.firma_qualificata.configurazione import FornitoreRegistrato
from libs.shared.limiti_richieste.redis_di_prova import avvia_redis, redis_disponibile
from tools.simulatore_firma.app import Simulatore, configurazione_console, crea_app

CREDENZIALE = "responsabile-prova-1"


def _gestore(sim: Simulatore, *, produzione: bool = False) -> GestoreConfigurazione:
    conf = configurazione_console(sim)
    trasporto = httpx.ASGITransport(app=crea_app(sim))
    fornitori = [
        FornitoreRegistrato("simulatore-csc", "FIRMA", "CSC", "Simulatore", 1,
                            conf["firma"]["configurazione"]),
        FornitoreRegistrato("simulatore-tsa", "MARCA", "RFC3161", "TSA simulata", 1,
                            conf["marca"]["configurazione"]),
    ]
    cfg = Settings(env="production" if produzione else "test",
                   firma_segreti_json=json.dumps(conf["segreti"]))

    async def lettore() -> list[FornitoreRegistrato]:
        return fornitori

    return GestoreConfigurazione(cfg, trasporti={"simulatore-csc": trasporto,
                                                 "simulatore-tsa": trasporto},
                                 trasporto_revoca=trasporto, lettore=lettore)


@pytest.fixture
def sim() -> Simulatore:
    s = Simulatore()
    s.impronta_personale = s.aggiungi_personale(CREDENZIALE, "Responsabile di prova")  # type: ignore[attr-defined]
    return s


async def _firma(gestore: GestoreConfigurazione, impronta_cert: str, otp: str,
                 contenuto: bytes = b'{"manuale": "di prova"}'):
    servizio, _ = await gestore.personale("simulatore-csc", CREDENZIALE, impronta_cert, otp=otp)
    return await servizio.firma(contenuto, formato="cades", livello="B-T",
                                adesso=datetime.now(UTC))


@pytest.mark.asyncio
async def test_firma_personale_con_otp_verificata_con_il_certificato_della_persona(sim):
    gestore = _gestore(sim)
    _, personale = await gestore.personale("simulatore-csc", CREDENZIALE,
                                           sim.impronta_personale)
    await personale.invia_otp()
    otp = sim.otp_inviati[CREDENZIALE][-1]
    esito = await _firma(gestore, sim.impronta_personale, otp)
    assert esito.verificata and esito.qualificata and esito.livello == "B-T"
    assert esito.certificato_sha256 == sim.impronta_personale
    assert "Responsabile di prova" in esito.firmatario
    # busta CAdES con il contenuto e con il certificato della persona (non del sigillo)
    sd = cms.ContentInfo.load(esito.busta)["content"]
    assert sd["encap_content_info"]["content"].native == b'{"manuale": "di prova"}'
    principale = await gestore.servizio()
    verifica = await principale.verifica(esito.busta)
    assert verifica["esito"] == "valida", verifica


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["errato", "riusato", "senza_invio"])
async def test_otp_errato_riusato_o_mai_inviato_non_firma(sim, caso):
    gestore = _gestore(sim)
    _, personale = await gestore.personale("simulatore-csc", CREDENZIALE,
                                           sim.impronta_personale)
    if caso != "senza_invio":
        await personale.invia_otp()
    otp = sim.otp_inviati.get(CREDENZIALE, ["000000"])[-1]
    if caso == "riusato":
        await _firma(gestore, sim.impronta_personale, otp)  # prima firma: consuma l'OTP
    elif caso == "errato":
        otp = f"{(int(otp) + 1) % 10**6:06d}"
    firme_prima = sim.chiamate.count("signatures/signHash")
    with pytest.raises(ErroreFornitore) as info:
        await _firma(gestore, sim.impronta_personale, otp)
    assert info.value.dettagli.get("errore") == "invalid_otp"
    assert sim.chiamate.count("signatures/signHash") == firme_prima


@pytest.mark.asyncio
async def test_adattatore_personale_firma_una_volta_sola(sim):
    gestore = _gestore(sim)
    _, personale = await gestore.personale("simulatore-csc", CREDENZIALE,
                                           sim.impronta_personale, otp="123456")
    personale._usato = True  # già usato: un secondo uso non arriva al prestatore
    with pytest.raises(ErroreFornitore):
        await personale.firma_impronta(hashlib.sha256(b"x").digest(), "sha256")
    assert "credentials/authorize" not in sim.chiamate


@pytest.mark.asyncio
async def test_certificato_diverso_da_quello_atteso_nessuna_busta(sim):
    """La credenziale registrata dichiara un certificato; il prestatore ne usa un altro."""
    gestore = _gestore(sim)
    _, personale = await gestore.personale("simulatore-csc", CREDENZIALE, "0" * 64)
    await personale.invia_otp()
    with pytest.raises(FirmaProdottaNonValida):
        await _firma(gestore, "0" * 64, sim.otp_inviati[CREDENZIALE][-1])
    assert "signatures/signHash" not in sim.chiamate


@pytest.mark.asyncio
async def test_il_sigillo_del_conservatore_non_vale_come_firma_personale(sim):
    """Credenziale personale che punta alla credenziale del SIGILLO: l'identità attesa è
    quella della persona, il certificato del sigillo è rifiutato."""
    gestore = _gestore(sim)
    servizio, _ = await gestore.personale("simulatore-csc", sim.credenziale,
                                          sim.impronta_personale, otp=sim.otp)
    with pytest.raises(FirmaProdottaNonValida):
        await servizio.firma(b"x", formato="cades", livello="B-B", adesso=datetime.now(UTC))


@pytest.mark.asyncio
async def test_fornitore_non_csc_o_non_attivo(sim):
    gestore = _gestore(sim)
    for codice in ("simulatore-tsa", "inesistente"):
        with pytest.raises(FornitoreNonRegistrato):
            await gestore.personale(codice, CREDENZIALE, sim.impronta_personale)


# ── API (token, ruoli, limiti) ────────────────────────────────────────

def _token(ruolo: str, user_id: str | None = None, **extra) -> str:
    from app.auth.jwt_handler import create_access_token

    return create_access_token(tenant_id=str(uuid.uuid4()), user_id=user_id or str(uuid.uuid4()),
                               ruolo=ruolo, **extra)


@pytest.fixture
def client(sim, monkeypatch):
    from app.main import app
    from app.routers import personale as rotte_personale
    from app.routers import signature as rotte
    from app.services import limiti
    from app.services.personale import CredenzialePersonale
    from fastapi.testclient import TestClient

    rotte.imposta_gestore(_gestore(sim))
    utente = str(uuid.uuid4())

    async def leggi(user_id: str) -> CredenzialePersonale:
        from app.services.personale import CredenzialeAssente

        if user_id != utente:
            raise CredenzialeAssente(user_id)
        return CredenzialePersonale(str(uuid.uuid4()), utente, "simulatore-csc", CREDENZIALE,
                                    sim.impronta_personale)

    monkeypatch.setattr(rotte_personale, "leggi_credenziale", leggi)
    limiti.azzera_locali()
    c = TestClient(app)
    c.utente = utente  # type: ignore[attr-defined]
    yield c
    rotte.imposta_gestore(None)
    limiti.azzera_locali()


def _corpo(otp: str, contenuto: bytes = b"<Nomina>di prova</Nomina>") -> dict:
    return {"contenuto_b64": base64.b64encode(contenuto).decode(), "otp": otp,
            "impronta_sha256_hex": hashlib.sha256(contenuto).hexdigest(),
            "documento_id": "d1"}


def test_api_otp_e_firma(client, sim):
    h = {"Authorization": f"Bearer {_token('RSC_CONSERVATORE', client.utente)}"}
    r = client.post("/api/v1/signature/personale/otp", headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/signature/personale/firma", headers=h,
                    json=_corpo(sim.otp_inviati[CREDENZIALE][-1]))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["certificato_sha256"] == sim.impronta_personale and corpo["verificata"]
    assert base64.b64decode(corpo["busta_b64"])
    # OTP già usato: rifiutato con il codice della persona (non un errore del servizio)
    r = client.post("/api/v1/signature/personale/firma", headers=h,
                    json=_corpo(sim.otp_inviati[CREDENZIALE][-1]))
    assert r.status_code == 422 and r.json()["codice"] == "FIRMA-422-OTP"


def test_api_impronta_diversa_dal_contenuto(client, sim):
    h = {"Authorization": f"Bearer {_token('RSC_CONSERVATORE', client.utente)}"}
    corpo = _corpo("123456")
    corpo["impronta_sha256_hex"] = "a" * 64
    r = client.post("/api/v1/signature/personale/firma", headers=h, json=corpo)
    assert r.status_code == 422 and r.json()["codice"] == "FIRMA-422-RICHIESTA"
    assert "credentials/authorize" not in sim.chiamate


@pytest.mark.parametrize("ruolo,extra", [("ADMIN_TENANT", {}), ("OPERATORE", {}),
                                          ("RSC_CONSERVATORE", {"rp": str(uuid.uuid4())}),
                                          ("SUPER_ADMIN", {"auth_method": "api_key"})])
def test_api_solo_persone_del_conservatore(client, ruolo, extra):
    h = {"Authorization": f"Bearer {_token(ruolo, client.utente, **extra)}"}
    r = client.post("/api/v1/signature/personale/otp", headers=h)
    assert r.status_code == 403 and r.json()["codice"] == "FIRMA-403-PERSONALE"


def test_api_token_di_servizio_e_senza_credenziale(client, token_servizio):
    r = client.post("/api/v1/signature/personale/otp",
                    headers={"Authorization": f"Bearer {token_servizio}"})
    assert r.status_code in (401, 403)
    altro = {"Authorization": f"Bearer {_token('SUPER_ADMIN')}"}
    r = client.post("/api/v1/signature/personale/otp", headers=altro)
    assert r.status_code == 404 and r.json()["codice"] == "FIRMA-404-CREDENZIALE"


def test_api_limite_per_persona(client, sim, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "firma_personale_tentativi", 2)
    h = {"Authorization": f"Bearer {_token('RSC_CONSERVATORE', client.utente)}"}
    for _ in range(2):
        r = client.post("/api/v1/signature/personale/firma", headers=h, json=_corpo("999999"))
        assert r.status_code == 422
    r = client.post("/api/v1/signature/personale/firma", headers=h, json=_corpo("999999"))
    assert r.status_code == 429 and r.json()["codice"] == "FIRMA-429-PERSONALE"
    assert sim.chiamate.count("credentials/authorize") == 2  # il terzo non parte


# ── VD-C-168: limite delle prove condiviso fra le repliche (Redis vero) ────────

@pytest.mark.asyncio
@pytest.mark.skipif(not redis_disponibile(), reason="redis-server non installato")
async def test_limite_delle_prove_condiviso_e_atomico(monkeypatch):
    import redis.asyncio as aioredis
    from app.config import settings
    from app.services import limiti

    from libs.shared.errori import ErroreApi

    monkeypatch.setattr(settings, "firma_prove_per_ora", 5)
    with avvia_redis() as url:
        repliche = [aioredis.from_url(url), aioredis.from_url(url)]
        esiti: list[bool] = []

        async def prova(i: int) -> None:
            limiti.imposta_redis(repliche[i % 2])
            try:
                await limiti.riserva_prova()
                esiti.append(True)
            except ErroreApi as exc:
                assert exc.codice == "FIRMA-429-PROVA"
                esiti.append(False)

        await asyncio.gather(*(prova(i) for i in range(20)))
        assert esiti.count(True) == 5  # due «repliche», venti richieste in parallelo
        chiave = limiti.PREFISSO + "prove"
        assert 0 < await repliche[0].ttl(chiave) <= limiti.FINESTRA_PROVE
        assert int(await repliche[0].get(chiave)) == 5
        for r in repliche:
            await r.aclose()
    limiti.imposta_redis(None)


@pytest.mark.asyncio
async def test_redis_irraggiungibile_rifiuta(monkeypatch):
    import redis.asyncio as aioredis
    from app.services import limiti

    from libs.shared.errori import ErroreApi
    from libs.shared.limiti_richieste.redis_di_prova import porta_chiusa

    limiti.imposta_redis(aioredis.from_url(porta_chiusa(), socket_connect_timeout=0.5))
    try:
        with pytest.raises(ErroreApi) as info:
            await limiti.riserva_prova()
        assert info.value.codice == "FIRMA-503-LIMITE"
    finally:
        limiti.imposta_redis(None)


def test_produzione_senza_redis_non_parte():
    from app.services.registro_fornitori import problemi_avvio

    assert any("REDIS_URL" in p for p in problemi_avvio(
        Settings(env="production", database_url="postgresql://x"), []))
    assert not any("REDIS_URL" in p for p in problemi_avvio(
        Settings(env="production", database_url="postgresql://x",
                 redis_url="redis://r.invalid:6379/0"), []))


def test_api_secondo_controllo_del_certificato(client, sim, monkeypatch):
    """Guardia indipendente dall'identità dell'adattatore: anche se il controllo
    dell'identità mancasse, l'API non restituisce una busta con un certificato diverso da
    quello della credenziale della persona."""
    from app.routers import personale as rotte_personale
    from app.services.fornitori import IdentitaAttesa
    from app.services.personale import CredenzialePersonale

    monkeypatch.setattr(IdentitaAttesa, "controlla", lambda self, cert: None)

    async def leggi(user_id: str) -> CredenzialePersonale:
        return CredenzialePersonale(str(uuid.uuid4()), user_id, "simulatore-csc", CREDENZIALE,
                                    "e" * 64)

    monkeypatch.setattr(rotte_personale, "leggi_credenziale", leggi)
    h = {"Authorization": f"Bearer {_token('RSC_CONSERVATORE', client.utente)}"}
    assert client.post("/api/v1/signature/personale/otp", headers=h).status_code == 200
    r = client.post("/api/v1/signature/personale/firma", headers=h,
                    json=_corpo(sim.otp_inviati[CREDENZIALE][-1]))
    assert r.status_code == 502 and r.json()["codice"] == "FIRMA-502-FIRMA-NON-VALIDA"
    assert "busta_b64" not in r.text


async def test_simulatore_da_riga_di_comando_con_credenziale_personale(capsys):
    """Collaudo a mano (README del pacchetto R15–R17): ``--personale ID:PIN`` crea la
    credenziale personale, ne stampa credentialID e impronta del certificato (i campi
    dell'API di onboarding) e a ogni sendOTP stampa l'OTP monouso nel terminale."""
    from tools.simulatore_firma.__main__ import prepara

    tutte = "0.0.0.0"  # noqa: S104 - solo l'argomento della riga di comando, nessun server
    sim, conf, args = prepara(["--host", tutte, "--url-pubblico",
                               "http://host.docker.internal:8099", "--personale", "resp-1:4321"])
    assert args.host == tutte
    assert conf["firma"]["configurazione"]["url_base"] == "http://host.docker.internal:8099/csc/v2"
    voce = conf["credenziali_personali"][0]
    assert voce["credential_id"] == "resp-1" and voce["pin"] is True
    assert len(voce["certificato_sha256"]) == 64 and sim.personali["resp-1"].pin == "4321"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=crea_app(sim)),
                                 base_url="http://sim") as c:
        token = (await c.post("/oauth2/token", data={
            "grant_type": "client_credentials", "client_id": sim.client_id,
            "client_secret": sim.client_secret})).json()["access_token"]
        r = await c.post("/csc/v2/credentials/sendOTP", json={"credentialID": "resp-1"},
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert f"OTP per resp-1: {sim.otp_inviati['resp-1'][-1]}" in capsys.readouterr().out
    # senza --personale nessuna stampa dell'OTP (comportamento dei test invariato)
    sim2, conf2, _ = prepara([])
    assert sim2.stampa_otp is False and "credenziali_personali" not in conf2
