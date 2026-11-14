# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (integrazione) — canale ``CICLO_VITA`` del gateway (contratto C2, Q2).

ciclo-vita versa i PdV di rettifica e i documenti del riversamento in ingresso
come ogni canale: token di canale firmato con la sua chiave PROPRIA, per conto
dell'utente che ha avviato la pratica. Il gateway lo accetta solo se l'utente
ha una pratica di rettifica o di riversamento in ingresso APERTA nel proprio
ente (vista ``pratiche_canale_versamento``, ``security_invoker``: con la RLS di
``pratiche`` letta nell'ambito dell'ente del token).
"""
from __future__ import annotations

import uuid

import pytest
from app.auth import canali
from app.auth.canali import canale_effettivo
from app.auth.dependencies import get_current_user, get_redis
from app.config import settings
from app.services.caricamenti import canale_da_utente
from app.services.versamento_service import Canale
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from libs.shared.errori import ErroreApi, installa_gestori_errori
from libs.shared.security.quote import svuota_cache_piani
from libs.shared.security.revoca import svuota_cache_revoche
from libs.shared.security.token_canale import (
    CANALI_CON_TOKEN_DI_SERVIZIO,
    TABELLE_ACCOUNT_CANALE,
    ServizioCanale,
    firma_token_canale,
)
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIV_CV = _PRIV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
PUB_CV = _PRIV.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _app() -> FastAPI:
    prova = FastAPI()

    @prova.get("/chi")
    async def chi(user: dict = Depends(get_current_user)) -> dict:  # noqa: B008
        return {k: user.get(k) for k in ("tenant_id", "user_id", "ruolo", "canale",
                                         "auth_method")}

    prova.dependency_overrides[get_redis] = lambda: None
    installa_gestori_errori(prova)
    return prova


async def _chi(token: str):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://gw") as c:
        return await c.get("/chi", headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(autouse=True)
def _servizi(monkeypatch):
    monkeypatch.setattr(canali, "SERVIZI_CANALE", [
        ServizioCanale("ciclo-vita", frozenset({"CICLO_VITA"}), PUB_CV)])
    svuota_cache_piani()
    svuota_cache_revoche()
    yield
    svuota_cache_piani()
    svuota_cache_revoche()


async def _ente_con_utente(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', "
                   "'e@example.org')", tid, tid.hex[:16], f"t_{tid.hex[:20]}")
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, $3, 'Nome', 'Cognome', 'ADMIN_TENANT')",
              uid, tid, f"{uid.hex[:10]}@example.org")
    return tid, uid


async def _pratica(url: str, tid, uid, *, tipo: str = "RETTIFICA",
                   stato: str = "RICHIESTA") -> None:
    await sql(url, "INSERT INTO public.pratiche (tenant_id, tipo, stato, motivo, creata_da) "
                   "VALUES ($1, $2, $3, 'pratica di prova del canale', $4)", tid, tipo, stato, uid)


def _token(tid, uid) -> str:
    return firma_token_canale("ciclo-vita", tenant_id=str(tid), user_id=str(uid),
                              ruolo="ADMIN_TENANT", canale="CICLO_VITA",
                              chiave_privata_pem=PRIV_CV, emittente=settings.jwt_issuer)


def test_canale_ciclo_vita_dichiarato_ovunque():
    assert "CICLO_VITA" in CANALI_CON_TOKEN_DI_SERVIZIO
    assert TABELLE_ACCOUNT_CANALE["CICLO_VITA"] == "public.pratiche_canale_versamento"
    assert Canale("CICLO_VITA") is Canale.CICLO_VITA
    assert canali.ImpostazioniCanali().servizi_ammessi["ciclo-vita"] == ["CICLO_VITA"]
    servizi = canali.carica_servizi(canali.ImpostazioniCanali(chiavi_pubbliche={
        "ciclo-vita": PUB_CV}), "production")
    assert {s.nome: s.canali for s in servizi}["ciclo-vita"] == frozenset({"CICLO_VITA"})


def test_canale_effettivo_ciclo_vita_solo_rest_o_suo_canale():
    utente = {"auth_method": "token_canale", "canale": "CICLO_VITA"}
    assert canale_da_utente(utente) is Canale.CICLO_VITA
    assert canale_effettivo(utente, "REST") == "CICLO_VITA"
    with pytest.raises(ErroreApi):
        canale_effettivo(utente, "PDND")


@richiede_pg
async def test_token_ciclo_vita_con_pratica_aperta_accettato(db_url):  # noqa: F811
    tid, uid = await _ente_con_utente(db_url)
    await _pratica(db_url, tid, uid)
    r = await _chi(_token(tid, uid))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["canale"] == "CICLO_VITA" and corpo["auth_method"] == "token_canale"
    assert corpo["tenant_id"] == str(tid) and corpo["ruolo"] == "API_SYSTEM"


@richiede_pg
@pytest.mark.parametrize("caso", ["senza_pratica", "pratica_chiusa", "altro_tipo",
                                  "pratica_di_altro_ente"])
async def test_token_ciclo_vita_senza_pratica_aperta_rifiutato(db_url, caso):  # noqa: F811
    tid, uid = await _ente_con_utente(db_url)
    if caso == "pratica_chiusa":
        await _pratica(db_url, tid, uid, stato="COMPLETATA")
    elif caso == "altro_tipo":
        await _pratica(db_url, tid, uid, tipo="SCARTO", stato="BOZZA")
    elif caso == "pratica_di_altro_ente":
        # pratica aperta a nome dell'utente ma in un ALTRO ente: non vale
        altro, _ = await _ente_con_utente(db_url)
        await _pratica(db_url, altro, uid)
    r = await _chi(_token(tid, uid))
    assert r.status_code == 401 and r.json()["codice"] == "AUTH-003", r.text


def test_chiave_ciclo_vita_da_variabile_dedicata(monkeypatch):
    """Come per sftp-ingest: la pubblica di CICLO_VITA_TOKEN_CHIAVE_PRIVATA nel
    Secret del gateway come CICLO_VITA_TOKEN_CHIAVE_PUBBLICA (nessun JSON)."""
    monkeypatch.setenv("CICLO_VITA_TOKEN_CHIAVE_PUBBLICA", PUB_CV)
    servizi = canali.carica_servizi(canali.ImpostazioniCanali(), "production")
    assert {s.nome: s.chiave_pubblica_pem for s in servizi} == {"ciclo-vita": PUB_CV.strip()}
