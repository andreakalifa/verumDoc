# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R17 (VD-P-164) — ``POST /api/v1/auth/conferme-decisione``: il gateway firma la conferma di una
decisione sensibile solo dopo una conferma dell'identità recente in QUESTA sessione, la
consuma (una conferma dell'identità = una decisione), scrive l'audit prima di restituire la
firma e non firma azioni di un altro ruolo o di un altro ente; senza chiave → 503.
PostgreSQL vero, Redis in memoria con le scadenze.
"""
from __future__ import annotations

import base64
import json
import uuid

import pytest

from libs.shared import conferme_decisione as cd
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_sessione_cookie import _senza_rate_limit  # noqa: F401
from tests.test_r10_accesso_locale import (  # noqa: F401
    _attiva_password,
    _client,
    _ente,
    _invito,
    accesso_locale,
)
from tests.test_r12b_credenziali_utente import _conferma, _csrf

pytestmark = richiede_pg

URL = "/api/v1/auth/conferme-decisione"
SEME = bytes(range(7, 39))
KID = "gw-prova-1"


@pytest.fixture
def chiave_conferme(monkeypatch: pytest.MonkeyPatch):
    from app.auth import conferme_decisione as modulo
    from app.config import settings

    monkeypatch.setattr(settings, "conferme_chiave_privata",
                        type(settings.conferme_chiave_privata)(base64.b64encode(SEME).decode()))
    monkeypatch.setattr(settings, "conferme_kid", KID)
    modulo._chiave.cache_clear()
    yield cd.chiavi_pubbliche(json.dumps({KID: cd.chiave_pubblica_b64(
        cd.chiave_privata(base64.b64encode(SEME).decode()))}))
    modulo._chiave.cache_clear()


def _corpo(ente: uuid.UUID, azione: str = cd.ANNULLAMENTO_RICHIEDI, **altro) -> dict:
    base = {"azione": azione, "pratica": None, "versione": 0, "impronta": "ab" * 32,
            "ente": str(ente)}
    return {**base, **altro}


async def test_firma_solo_dopo_la_conferma_dell_identita_e_una_volta(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, chiave_conferme):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente, ruolo="ADMIN_TENANT")
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        # l'accesso appena fatto non basta quando l'ente ha metodi locali
        r = await c.post(URL, headers=_csrf(c), json=_corpo(ente))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"
        assert (await _conferma(c, db_url, uid, segreto)).status_code == 200
        r = await c.post(URL, headers=_csrf(c), json=_corpo(ente))
        assert r.status_code == 200, r.text
        firmata = r.json()["conferma"]
        crudi, firma, campi = cd.leggi(firmata)
        chiave_conferme[KID].verify(firma, crudi)
        assert (campi["azione"], campi["utente"], campi["ente"], campi["kid"]) == (
            cd.ANNULLAMENTO_RICHIEDI, str(uid), str(ente), KID)
        # una seconda decisione con la stessa conferma dell'identità: rifiutata
        r = await c.post(URL, headers=_csrf(c), json=_corpo(ente, impronta="cd" * 32))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"
        assert "già stata usata" in r.json()["detail"]
        # nuova conferma dell'identità: di nuovo una decisione
        assert (await _conferma(c, db_url, uid, segreto)).status_code == 200
        assert (await c.post(URL, headers=_csrf(c), json=_corpo(ente))).status_code == 200
    voci = await sql(db_url, "SELECT dettagli FROM public.audit_global WHERE evento = "
                             "'decisioni.conferma_firmata'")
    assert len(voci) == 2
    assert json.loads(voci[0]["dettagli"])["nonce"] == campi["nonce"] or \
        json.loads(voci[1]["dettagli"])["nonce"] == campi["nonce"]


async def test_azione_di_un_altro_ruolo_o_di_un_altro_ente(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, chiave_conferme):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente, ruolo="ADMIN_TENANT")
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        assert (await _conferma(c, db_url, uid, segreto)).status_code == 200
        # azione del conservatore chiesta dall'ente
        r = await c.post(URL, headers=_csrf(c), json=_corpo(
            ente, cd.SCARTO_ESEGUI, pratica=str(uuid.uuid4()), versione=3))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-CONFERMA-DECISIONE"
        # azione dell'ente su un altro ente
        r = await c.post(URL, headers=_csrf(c), json=_corpo(uuid.uuid4()))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-CONFERMA-DECISIONE"
        # richieste malformate
        for errata in (_corpo(ente, pratica=str(uuid.uuid4())),
                       _corpo(ente, impronta="x"),
                       _corpo(ente, "AZIONE_IGNOTA")):
            r = await c.post(URL, headers=_csrf(c), json=errata)
            assert r.status_code == 422, r.text
        # nessuna di queste ha consumato la conferma dell'identità
        assert (await c.post(URL, headers=_csrf(c), json=_corpo(ente))).status_code == 200


async def test_conservatore_e_solo_spid_con_accesso_recente(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, chiave_conferme,
        monkeypatch):  # noqa: F811
    """Ruolo fisso del conservatore: firma le sue azioni. Ente senza metodi locali (solo
    SPID/CIE): vale l'accesso fatto da meno della finestra, una sola volta."""
    from app.auth import conferme_decisione as modulo
    from app.auth.jwt_handler import create_access_token

    from libs.shared.security.inattivita import CLAIM_SESSIONE, SESSIONE_PORTALE

    ente = await _ente(db_url, ["SPID_CIE"])
    uid = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, "
                      "attivo) VALUES ($1, $2, 'rsc@example.org', 'R', 'S', "
                      "'RSC_CONSERVATORE', true)", uid, ente)

    async def _metodi(_ente_id: str) -> list[str]:
        return ["SPID_CIE"]

    monkeypatch.setattr(modulo, "metodi_ammessi_ente", _metodi)
    bearer = create_access_token(tenant_id=str(ente), user_id=str(uid),
                                 ruolo="RSC_CONSERVATORE", scopes=["read"],
                                 extra={"auth_method": "spid", CLAIM_SESSIONE: SESSIONE_PORTALE})
    from app.auth.jwt_handler import verify_access_token

    from libs.shared.security.inattivita import registra_sessione

    await registra_sessione(bearer, verify_access_token(bearer), limite_secondi=900)
    intestazioni = {"Authorization": f"Bearer {bearer}"}
    corpo = _corpo(uuid.uuid4(), cd.SCARTO_ESEGUI, pratica=str(uuid.uuid4()), versione=4)
    async with _client() as c:
        r = await c.post(URL, headers=intestazioni, json=corpo)
        assert r.status_code == 200, r.text
        assert cd.leggi(r.json()["conferma"])[2]["utente"] == str(uid)
        r = await c.post(URL, headers=intestazioni, json=corpo)
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"
        # azione dell'ente chiesta dal conservatore
        r = await c.post(URL, headers=intestazioni, json=_corpo(ente))
        assert r.status_code == 403


async def test_senza_chiave_nessuna_conferma(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, monkeypatch):  # noqa: F811
    from app.auth import conferme_decisione as modulo
    from app.config import settings

    monkeypatch.setattr(settings, "conferme_kid", "")
    modulo._chiave.cache_clear()
    assert modulo.problema_avvio() == "CONFERME_KID assente"
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente, ruolo="ADMIN_TENANT")
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        assert (await _conferma(c, db_url, uid, segreto)).status_code == 200
        r = await c.post(URL, headers=_csrf(c), json=_corpo(ente))
        assert r.status_code == 503 and r.json()["codice"] == "ACC-503-CONFERMA-DECISIONE"
    monkeypatch.setattr(settings, "conferme_kid", KID)
    monkeypatch.setattr(settings, "conferme_chiave_privata",
                        type(settings.conferme_chiave_privata)("non una chiave"))
    modulo._chiave.cache_clear()
    assert (modulo.problema_avvio() or "").startswith("CONFERME_CHIAVE_PRIVATA")


def test_ruolo_personalizzato_non_firma_azioni_del_conservatore() -> None:
    """Guardia del gateway, oltre alla revoca (un token con un ruolo personalizzato che non
    corrisponde al database si chiude prima): anche con ``ruolo`` RSC nel token, un ruolo
    personalizzato (claim ``rp``) non ottiene la firma di un'azione del conservatore."""
    from app.auth import conferme_decisione as modulo
    from app.auth.credenziali_utente import Persona

    chi = Persona(str(uuid.uuid4()), str(uuid.uuid4()), "0" * 64)
    corpo = modulo.RichiestaConferma(azione=cd.SCARTO_ESEGUI, pratica=str(uuid.uuid4()),
                                     versione=2, impronta="ab" * 32, ente=str(uuid.uuid4()))
    assert modulo._autorizzata(corpo, chi, {"ruolo": "RSC_CONSERVATORE"}) is None
    assert modulo._autorizzata(corpo, chi, {"ruolo": "RSC_CONSERVATORE", "rp": "r1"}) \
        == "azione riservata ai ruoli fissi del conservatore"
    assert modulo._autorizzata(corpo, chi, {"ruolo": "ADMIN_TENANT"}) is not None
