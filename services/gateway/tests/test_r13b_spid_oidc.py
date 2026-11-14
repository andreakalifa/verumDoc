# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-069) — accesso SPID/CIE con il simulatore di OpenID Provider
(``tools.simulatore_oidc``), dall'avvio fino al token VerumDoc, su PostgreSQL vero.

  - PKCE S256 sempre (il simulatore lo esige; code_verifier sbagliato → SPID-004);
  - autenticazione del cliente: ``client_secret_post``, ``client_secret_basic``,
    ``private_key_jwt`` (asserzione firmata, monouso, aud = endpoint del token);
  - request object firmato (JAR) quando la console lo chiede;
  - attributi dallo UserInfo firmato e cifrato (JWE) con lo stesso ``sub``
    dell'id_token: sub diverso o firma estranea → SPID-005;
  - livelli: L3 richiesto dalla console e L2 restituito → SPID-009;
  - segreti legati agli host (``SPID_HOST_AMMESSI``): endpoint del token su un host
    non ammesso → il segreto non parte (SPID-004);
  - federazione OpenID: chiavi dell'OP dalla trust chain verificata con le chiavi
    FISSATE del Trust Anchor; un Trust Anchor che non attesta l'OP → SPID-005.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from app.auth import spid
from app.auth.fornitori import svuota_cache_fornitori
from app.config import settings
from app.main import app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_g2_spid_accesso import CF, _ente, _senza_rate_limit, _utente  # noqa: F401
from tools.simulatore_oidc.app import SimulatoreOidc, crea_app, jwk_pubblica

pytestmark = richiede_pg

SEGRETO = "segreto-del-cliente-di-prova"
HOST = "aggregatore.simulato.test"


class Ambiente:
    def __init__(self, sim: SimulatoreOidc, chiave: rsa.RSAPrivateKey) -> None:
        self.sim = sim
        self.chiave = chiave
        self.app_sim = crea_app(sim)


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch, redis_finto: Any) -> Ambiente:
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    enc = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sim = SimulatoreOidc(client_secret=SEGRETO, client_jwks={"keys": [
        jwk_pubblica(chiave, "cliente-1"), jwk_pubblica(chiave, "cliente-enc", uso="enc")]})
    _ = enc
    amb = Ambiente(sim, chiave)
    monkeypatch.setattr(spid, "_trasporto_http", ASGITransport(app=amb.app_sim))
    monkeypatch.setattr(spid, "_cache_jwks", {})
    monkeypatch.setattr(spid, "_cache_catena", {})
    monkeypatch.setattr(settings, "verumdoc_dev_auth", False)
    monkeypatch.setattr(settings, "spid_aggregator_url", sim.url)
    monkeypatch.setattr(settings, "spid_client_id", sim.client_id)
    monkeypatch.setattr(settings, "spid_issuer", sim.issuer)
    monkeypatch.setattr(settings, "spid_jwks_url", f"{sim.url}/jwks")
    monkeypatch.setattr(settings, "spid_client_secret", type(settings.spid_client_secret)(SEGRETO))
    monkeypatch.setattr(settings, "spid_host_ammessi", [HOST])
    monkeypatch.setattr(settings, "spid_chiave_client_kid", "cliente-1")
    pem = chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
    monkeypatch.setattr(settings, "spid_chiave_privata_client",
                        type(settings.spid_client_secret)(pem))
    monkeypatch.setattr(settings, "base_url", "https://api.verumdoc.it")
    svuota_cache_fornitori()
    yield amb
    svuota_cache_fornitori()


async def _console(url: str, sim: SimulatoreOidc, **extra: Any) -> None:
    conf = {**sim.configurazione_console(), **extra}
    await sql(url, "INSERT INTO public.fornitori_identita (tipo, nome, attivo, configurazione) "
                   "VALUES ('SPID_CIE', 'Aggregatore simulato', true, CAST($1 AS jsonb)) "
                   "ON CONFLICT (tipo) DO UPDATE SET configurazione = EXCLUDED.configurazione",
              json.dumps(conf))
    svuota_cache_fornitori()


async def _accesso(amb: Ambiente, *, verifier_sbagliato: bool = False) -> dict[str, str]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.verumdoc.it",
                           follow_redirects=False) as c, \
            AsyncClient(transport=ASGITransport(app=amb.app_sim), base_url=amb.sim.url,
                        follow_redirects=False) as browser_op:
        r = await c.get("/api/v1/auth/spid/login",
                        params={"redirect_uri": "https://app.verumdoc.it/cb", "provider": "spid"})
        assert r.status_code in (302, 307), r.text
        destinazione = r.headers["location"]
        if not destinazione.startswith(amb.sim.url):
            return {k: v[0] for k, v in parse_qs(urlparse(destinazione).fragment).items()}
        r = await browser_op.get(destinazione)
        assert r.status_code == 302, r.text
        ritorno = urlparse(r.headers["location"])
        if verifier_sbagliato:
            stato = parse_qs(ritorno.query)["state"][0]
            dati = await spid._get_state(stato)
            await spid._set_state(stato, {**dati, "pkce": "x" * 60})
        r = await c.get(f"{ritorno.path}?{ritorno.query}")
        assert r.status_code == 303, r.text
        esito = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).fragment).items()}
        if "spid_codice" in esito:
            s = await c.post("/api/v1/auth/spid/scambio", json={"codice": esito["spid_codice"]})
            esito["scambio"] = str(s.status_code)
        return esito


async def _persona(url: str) -> uuid.UUID:
    ente = await _ente(url, "Comune di Prova")
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ('ENTE', $1, 'ACCESSO_METODI_AMMESSI', '[\"SPID_CIE\"]', "
                   "'prova')", ente)
    return await _utente(url, ente, ruolo="ADMIN_TENANT")


async def test_accesso_completo_pkce_e_segreto_post(db_url, ambiente) -> None:  # noqa: F811
    await _persona(db_url)
    esito = await _accesso(ambiente)
    assert esito.get("scambio") == "200", esito
    autorizza = ambiente.sim.richieste[0]["authorize"]
    assert autorizza["code_challenge_method"] == "S256" and len(autorizza["code_challenge"]) == 43
    assert ambiente.sim.richieste[1]["token"]["code_verifier"]


async def test_code_verifier_sbagliato_rifiutato(db_url, ambiente) -> None:  # noqa: F811
    await _persona(db_url)
    assert await _accesso(ambiente, verifier_sbagliato=True) == {"spid_errore": "SPID-004"}


@pytest.mark.parametrize("metodo", ["client_secret_basic", "private_key_jwt"])
async def test_metodi_di_autenticazione_del_cliente(db_url, ambiente, metodo) -> None:  # noqa: F811
    await _persona(db_url)
    await _console(db_url, ambiente.sim, metodo_autenticazione=metodo)
    if metodo == "private_key_jwt":
        ambiente.sim.client_secret = ""   # il segreto non basta: serve l'asserzione
    esito = await _accesso(ambiente)
    assert esito.get("scambio") == "200", esito
    token = ambiente.sim.richieste[1]["token"]
    assert "client_secret" not in token
    if metodo == "private_key_jwt":
        assert token["client_assertion_type"].endswith("jwt-bearer")
        assert len(ambiente.sim.jti_visti) == 1


async def test_request_object_userinfo_cifrato_e_sub(db_url, ambiente) -> None:  # noqa: F811
    await _persona(db_url)
    ambiente.sim.request_object = True
    ambiente.sim.cifra_userinfo = True
    ambiente.sim.cf_nell_id_token = False
    await _console(db_url, ambiente.sim, request_object=True, attributi_da_userinfo=True,
                   metodo_autenticazione="private_key_jwt", prompt="consent login")
    esito = await _accesso(ambiente)
    assert esito.get("scambio") == "200", esito
    autorizza = ambiente.sim.richieste[0]["authorize"]
    assert "request" in autorizza and "nonce" not in autorizza   # tutto nel request object
    # UserInfo di un'altra persona (sub diverso): rifiutato
    ambiente.sim.sub_userinfo = "altra-persona"
    assert await _accesso(ambiente) == {"spid_errore": "SPID-005"}


async def test_userinfo_firmato_da_chiave_estranea(db_url, ambiente) -> None:  # noqa: F811
    await _persona(db_url)
    ambiente.sim.cf_nell_id_token = False
    await _console(db_url, ambiente.sim, attributi_da_userinfo=True)
    ambiente.sim.chiave_firma_falsa = rsa.generate_private_key(public_exponent=65537,
                                                               key_size=2048)
    assert await _accesso(ambiente) == {"spid_errore": "SPID-005"}


async def test_livello_l3_richiesto_l2_restituito(db_url, ambiente) -> None:  # noqa: F811
    await _persona(db_url)
    await _console(db_url, ambiente.sim, livello_minimo=3)
    assert await _accesso(ambiente) == {"spid_errore": "SPID-009"}
    ambiente.sim.livello = 3
    assert (await _accesso(ambiente)).get("scambio") == "200"


async def test_segreto_solo_agli_host_ammessi(db_url, ambiente) -> None:  # noqa: F811
    await _persona(db_url)
    await _console(db_url, ambiente.sim, token_url="https://raccoglitore.attaccante.test/token")
    assert await _accesso(ambiente) == {"spid_errore": "SPID-004"}
    assert not any("token" in r for r in ambiente.sim.richieste)


async def test_federazione_trust_chain(db_url, ambiente) -> None:  # noqa: F811
    await _persona(db_url)
    federazione = {"trust_anchor": ambiente.sim.trust_anchor,
                   "chiavi_trust_anchor": ambiente.sim.jwks_ta,
                   "entita_op": ambiente.sim.issuer}
    await _console(db_url, ambiente.sim, jwks_url="", federazione=federazione)
    assert (await _accesso(ambiente)).get("scambio") == "200"
    # Trust Anchor che non attesta la chiave dell'OP
    spid._cache_catena.clear()
    ambiente.sim.ta_attesta_op = False
    assert await _accesso(ambiente) == {"spid_errore": "SPID-005"}
    # configurazione del Trust Anchor autofirmata con una chiave non fissata
    spid._cache_catena.clear()
    ambiente.sim.ta_attesta_op = True
    ambiente.sim.chiave_ec_ta_falsa = rsa.generate_private_key(65537, 2048)
    assert await _accesso(ambiente) == {"spid_errore": "SPID-005"}
    ambiente.sim.chiave_ec_ta_falsa = None
    # chiavi del Trust Anchor fissate diverse da quelle vere
    spid._cache_catena.clear()
    ambiente.sim.ta_attesta_op = True
    falso = {"keys": [jwk_pubblica(rsa.generate_private_key(65537, 2048), "ta-1")]}
    await _console(db_url, ambiente.sim, jwks_url="",
                   federazione={**federazione, "chiavi_trust_anchor": falso})
    assert await _accesso(ambiente) == {"spid_errore": "SPID-005"}
