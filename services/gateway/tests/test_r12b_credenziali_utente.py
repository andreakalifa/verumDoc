# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12b (VD-C-156) — la persona gestisce le proprie credenziali dalla console, sul
PostgreSQL vero (Redis in memoria con le scadenze, autenticatore passkey di prova).

  - stato senza segreti; con solo SPID/CIE nessuna rotta locale; metodo non ammesso
    dall'ente → 403; chiavi API e token non di sessione → 403;
  - ogni modifica chiede una conferma dell'identità RECENTE e della STESSA sessione
    (finestra ``ACCESSO_RIAUTENTICAZIONE_MINUTI``); scaduta o di un'altra sessione → 403;
  - conferma con password e codice a tempo (o di recupero) o con passkey; tentativi
    sbagliati come riserva atomica per persona: in parallelo non si supera il limite,
    le conferme riuscite non consumano;
  - passkey: aggiunta, nome, revoca; MAI l'ultima utilizzabile, nemmeno con due revoche
    in parallelo;
  - password: verifica della vecchia, regole di robustezza, altre sessioni chiuse
    (VD-P-123), la nuova vale per l'accesso;
  - codice a tempo: nuovo segreto attivo solo dopo un codice valido, il vecchio non vale
    più; codici di recupero: nuovi mostrati una volta, salvati come impronta;
  - ogni modifica: audit nella transazione e avviso per e-mail all'indirizzo della
    persona (outbox ``notification.send``).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import pyotp

from libs.shared.parametri import svuota_cache
from libs.shared.security.revoca import svuota_cache_revoche
from libs.shared.security.sessione_cookie import COOKIE_CSRF
from tests.autenticatore_prova import Autenticatore
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_sessione_cookie import _senza_rate_limit  # noqa: F401
from tests.test_r10_accesso_locale import (  # noqa: F401
    EMAIL,
    ORIGINE,
    PASSWORD,
    _attiva_password,
    _client,
    _ente,
    _invito,
    accesso_locale,
)

pytestmark = richiede_pg

BASE = "/api/v1/auth/credenziali"
NUOVA = "un'altra frase lunga di prova 77"


def _csrf(c) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Origin": ORIGINE, "X-CSRF-Token": c.cookies.get(COOKIE_CSRF) or ""}


async def _codice(url: str, uid: uuid.UUID, segreto: str) -> str:
    """Codice a tempo valido ADESSO: si azzera l'ultimo passo usato (anti-riuso) per poter
    usare più codici nello stesso intervallo di 30 secondi."""
    await sql(url, "UPDATE public.credenziali_totp SET ultimo_passo = 0 WHERE user_id = $1", uid)
    return pyotp.TOTP(segreto).now()


async def _accedi(c, url: str, uid: uuid.UUID, segreto: str,  # type: ignore[no-untyped-def]
                  password: str = PASSWORD) -> int:
    r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL, "password": password})
    if r.status_code != 200:
        return r.status_code
    r = await c.post("/api/v1/auth/locale/secondo-fattore", json={
        "passaggio": r.json()["passaggio"], "codice": await _codice(url, uid, segreto)})
    return r.status_code


async def _conferma(c, url: str, uid: uuid.UUID, segreto: str,  # type: ignore[no-untyped-def]
                    password: str = PASSWORD):
    return await c.post(f"{BASE}/riautenticazione", headers=_csrf(c), json={
        "metodo": "PASSWORD_TOTP", "password": password,
        "codice": await _codice(url, uid, segreto)})


async def _avvisi(url: str) -> list[dict]:
    righe = await sql(url, "SELECT payload FROM public.outbox_eventi WHERE topic = "
                           "'notification.send' AND payload->>'evento' = "
                           "'accesso.credenziali_modificate' ORDER BY sequenza")
    return [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
            for r in righe]


async def _audit(url: str, prefisso: str = "credenziali.") -> list[str]:
    return [r["evento"] for r in await sql(
        url, "SELECT evento FROM public.audit_global WHERE evento LIKE $1 "
             "ORDER BY created_at, evento", prefisso + "%")]


async def test_stato_senza_segreti_e_modifiche_solo_dopo_la_conferma(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["SPID_CIE", "PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, vecchi = await _attiva_password(c, token)
        r = await c.get(BASE)
        assert r.status_code == 200, r.text
        stato = r.json()
        assert stato["disponibile"] and stato["metodi_locali"] == ["PASSWORD_TOTP"]
        assert stato["password"]["impostata"] and stato["totp"]["attivo"]
        assert stato["codici_recupero"] == {"rimasti": 10, "totale": 10}
        assert stato["riautenticazione"]["metodi"] == ["PASSWORD_TOTP"]
        assert stato["riautenticazione"]["valida_per_secondi"] is None
        assert stato["riautenticazione"]["finestra_minuti"] == 5
        assert segreto not in r.text and "impronta" not in r.text
        # senza conferma: nessuna modifica
        for metodo, percorso, corpo in (("POST", "/codici-recupero", {}),
                                        ("POST", "/totp", {}),
                                        ("PUT", "/password", {"password_attuale": PASSWORD,
                                                              "password_nuova": NUOVA})):
            r = await c.request(metodo, BASE + percorso, headers=_csrf(c), json=corpo)
            assert r.status_code == 403 and r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"
        # conferma sbagliata, poi giusta
        r = await _conferma(c, db_url, uid, segreto, password="password sbagliata 123")  # noqa: S106
        assert r.status_code == 422 and r.json()["codice"] == "ACC-422-RIAUTENTICAZIONE"
        r = await _conferma(c, db_url, uid, segreto)
        assert r.status_code == 200 and r.json()["finestra_minuti"] == 5
        assert 0 < (await c.get(BASE)).json()["riautenticazione"]["valida_per_secondi"] <= 300
        r = await c.post(f"{BASE}/codici-recupero", headers=_csrf(c))
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        nuovi = r.json()["codici_recupero"]
    assert len(nuovi) == 10 and not set(nuovi) & set(vecchi)
    salvati = {r["impronta"] for r in await sql(
        db_url, "SELECT impronta FROM public.codici_recupero WHERE user_id = $1", uid)}
    assert salvati == {hashlib.sha256(c.replace("-", "").encode()).hexdigest() for c in nuovi}
    assert sorted(await _audit(db_url)) == ["credenziali.codici_recupero_rigenerati",
                                            "credenziali.riautenticazione"]
    avvisi = await _avvisi(db_url)
    assert len(avvisi) == 1 and avvisi[0]["destinatario"] == EMAIL
    assert "nuovi codici di recupero" in avvisi[0]["testo"]
    assert all(c not in avvisi[0]["testo"] for c in nuovi)  # nessun segreto nell'e-mail
    # il vecchio codice di recupero non vale più all'accesso
    async with _client() as c:
        r = await c.post("/api/v1/auth/locale/accesso", json={"email": EMAIL,
                                                               "password": PASSWORD})
        r = await c.post("/api/v1/auth/locale/secondo-fattore", json={
            "passaggio": r.json()["passaggio"], "codice_recupero": vecchi[0]})
        assert r.status_code == 401


async def test_conferma_della_sola_sessione_e_scadenza(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as a, _client() as b:
        segreto, _ = await _attiva_password(a, token)
        assert await _accedi(b, db_url, uid, segreto) == 200
        assert (await _conferma(a, db_url, uid, segreto)).status_code == 200
        # la conferma di A non vale per B (un'altra sessione della stessa persona)
        r = await b.post(f"{BASE}/codici-recupero", headers=_csrf(b))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"
        assert (await a.post(f"{BASE}/codici-recupero", headers=_csrf(a))).status_code == 200
        # la finestra passa: serve una nuova conferma
        for chiave in [k for k in redis_finto.dati if ":riaut:" in k]:
            redis_finto.scadi(chiave)
        r = await a.post(f"{BASE}/codici-recupero", headers=_csrf(a))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"


async def test_conferma_con_finestra_dell_ente(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                      "valore, motivo) VALUES ('ENTE', $1, 'ACCESSO_RIAUTENTICAZIONE_MINUTI', "
                      "'2'::jsonb, 'prova R12b')", ente)
    svuota_cache()
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        r = await _conferma(c, db_url, uid, segreto)
        assert r.json()["valida_per_secondi"] == 120
        chiave = next(k for k in redis_finto.dati if ":riaut:" in k)
        assert 0 < await redis_finto.ttl(chiave) <= 120


async def test_cambio_password_chiude_le_altre_sessioni(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as a, _client() as b:
        segreto, _ = await _attiva_password(a, token)
        assert await _accedi(b, db_url, uid, segreto) == 200
        assert (await b.get("/api/v1/auth/sessione")).status_code == 200
        assert (await _conferma(a, db_url, uid, segreto)).status_code == 200
        r = await a.put(f"{BASE}/password", headers=_csrf(a), json={
            "password_attuale": "non è la mia password", "password_nuova": NUOVA})
        assert r.status_code == 422 and r.json()["codice"] == "ACC-422-RIAUTENTICAZIONE"
        r = await a.put(f"{BASE}/password", headers=_csrf(a), json={
            "password_attuale": PASSWORD, "password_nuova": "corta"})
        assert r.status_code == 422 and r.json()["codice"] == "ACC-422-PASSWORD"
        r = await a.put(f"{BASE}/password", headers=_csrf(a), json={
            "password_attuale": PASSWORD, "password_nuova": NUOVA})
        assert r.status_code == 200 and r.json()["altre_sessioni_chiuse"] == 1, r.text
        assert (await b.get("/api/v1/auth/sessione")).status_code == 401   # chiusa ovunque
        assert (await a.get("/api/v1/auth/sessione")).status_code == 200   # la mia resta
    async with _client() as c:
        assert await _accedi(c, db_url, uid, segreto, password=PASSWORD) == 401
    async with _client() as c:
        assert await _accedi(c, db_url, uid, segreto, password=NUOVA) == 200
    assert "credenziali.password_cambiata" in await _audit(db_url)
    avviso = (await _avvisi(db_url))[-1]
    assert "cambiata la password" in avviso["testo"] and "sessioni" in avviso["testo"]


async def test_nuovo_codice_a_tempo(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        vecchio, _ = await _attiva_password(c, token)
        assert (await _conferma(c, db_url, uid, vecchio)).status_code == 200
        r = await c.post(f"{BASE}/totp", headers=_csrf(c))
        assert r.status_code == 200
        nuovo = r.json()["segreto"]
        assert r.json()["uri"].startswith("otpauth://totp/")
        # finché non si conferma, vale il vecchio
        salvato = (await sql(db_url, "SELECT segreto_cifrato FROM public.credenziali_totp "
                                     "WHERE user_id = $1", uid))[0][0]
        sbagliato = "000000" if pyotp.TOTP(nuovo).now() != "000000" else "111111"
        r = await c.post(f"{BASE}/totp/conferma", headers=_csrf(c), json={"codice": sbagliato})
        assert r.status_code == 422 and r.json()["codice"] == "ACC-422-CODICE"
        r = await c.post(f"{BASE}/totp/conferma", headers=_csrf(c),
                         json={"codice": pyotp.TOTP(nuovo).now()})
        assert r.status_code == 200, r.text
    dopo = (await sql(db_url, "SELECT segreto_cifrato FROM public.credenziali_totp "
                              "WHERE user_id = $1", uid))[0][0]
    assert dopo != salvato and nuovo not in dopo and dopo.startswith("aesgcm$")
    async with _client() as c:
        assert await _accedi(c, db_url, uid, vecchio) == 401
    async with _client() as c:
        assert await _accedi(c, db_url, uid, nuovo) == 200
    assert "credenziali.totp_rigenerato" in await _audit(db_url)


async def test_passkey_aggiunta_nome_revoca_mai_l_ultima(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSKEY"])
    uid, token = await _invito(db_url, ente)
    prima = Autenticatore(rp_id="localhost", origine=ORIGINE)
    seconda = Autenticatore(rp_id="localhost", origine=ORIGINE)
    async with _client() as c:
        r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
        passaggio = r.json()["passaggio"]
        opzioni = (await c.post("/api/v1/auth/inviti/passkey/opzioni",
                                json={"passaggio": passaggio})).json()["opzioni"]
        r = await c.post("/api/v1/auth/inviti/completa", json={
            "passaggio": passaggio, "metodo": "PASSKEY", "credenziale": prima.registra(opzioni)})
        assert r.status_code == 200, r.text
        stato = (await c.get(BASE)).json()
        assert stato["metodi_locali"] == ["PASSKEY"] and len(stato["passkey"]) == 1
        id_prima = stato["passkey"][0]["id"]
        # password e codici: metodo non ammesso dall'ente
        r = await c.post(f"{BASE}/codici-recupero", headers=_csrf(c))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-CREDENZIALI"
        r = await c.post(f"{BASE}/passkey/opzioni", headers=_csrf(c))
        assert r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"
        # conferma con la passkey: firma di un'altra chiave rifiutata, poi la giusta
        o = (await c.post(f"{BASE}/riautenticazione/passkey/opzioni", headers=_csrf(c))).json()
        assert [x["id"] for x in o["opzioni"]["allowCredentials"]]
        r = await c.post(f"{BASE}/riautenticazione", headers=_csrf(c), json={
            "metodo": "PASSKEY", "credenziale": seconda.firma(o["opzioni"])})
        assert r.status_code == 422, r.text
        o = (await c.post(f"{BASE}/riautenticazione/passkey/opzioni", headers=_csrf(c))).json()
        r = await c.post(f"{BASE}/riautenticazione", headers=_csrf(c), json={
            "metodo": "PASSKEY", "credenziale": prima.firma(o["opzioni"])})
        assert r.status_code == 200, r.text
        # l'ultima passkey non si revoca
        r = await c.delete(f"{BASE}/passkey/{id_prima}", headers=_csrf(c))
        assert r.status_code == 409 and r.json()["codice"] == "ACC-409-ULTIMA"
        # seconda passkey (esclusa la prima dalle opzioni), nome, revoca della prima
        o = (await c.post(f"{BASE}/passkey/opzioni", headers=_csrf(c))).json()["opzioni"]
        assert len(o["excludeCredentials"]) == 1
        r = await c.post(f"{BASE}/passkey", headers=_csrf(c), json={
            "credenziale": seconda.registra(o), "nome": "Chiave di riserva"})
        assert r.status_code == 201, r.text
        id_seconda = r.json()["id"]
        r = await c.patch(f"{BASE}/passkey/{id_seconda}", headers=_csrf(c),
                          json={"nome": "Chiave nel cassetto"})
        assert r.status_code == 200
        r = await c.patch(f"{BASE}/passkey/{id_seconda}", headers=_csrf(c),
                          json={"nome": "con\x07controllo"})
        assert r.status_code == 422
        r = await c.delete(f"{BASE}/passkey/{id_prima}", headers=_csrf(c))
        assert r.status_code == 200, r.text
        r = await c.delete(f"{BASE}/passkey/{id_seconda}", headers=_csrf(c))
        assert r.status_code == 409
        nomi = [k["nome"] for k in (await c.get(BASE)).json()["passkey"]]
    assert nomi == ["Chiave nel cassetto"]
    # la passkey revocata non accede più, la rimasta sì
    async with _client() as c:
        for chiave, atteso in ((prima, 400), (seconda, 200)):
            corpo = (await c.post("/api/v1/auth/passkey/opzioni")).json()
            r = await c.post("/api/v1/auth/passkey/accesso", json={
                "passaggio": corpo["passaggio"], "credenziale": chiave.firma(corpo["opzioni"])})
            assert r.status_code == atteso, r.text
    eventi = await _audit(db_url)
    for atteso in ("credenziali.passkey_aggiunta", "credenziali.passkey_rinominata",
                   "credenziali.passkey_revocata"):
        assert atteso in eventi
    assert len(await _avvisi(db_url)) == 3


async def _due_passkey(c, url, token):  # type: ignore[no-untyped-def]
    prima = Autenticatore(rp_id="localhost", origine=ORIGINE)
    seconda = Autenticatore(rp_id="localhost", origine=ORIGINE)
    r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token, "email": EMAIL})
    passaggio = r.json()["passaggio"]
    opzioni = (await c.post("/api/v1/auth/inviti/passkey/opzioni",
                            json={"passaggio": passaggio})).json()["opzioni"]
    await c.post("/api/v1/auth/inviti/completa", json={
        "passaggio": passaggio, "metodo": "PASSKEY", "credenziale": prima.registra(opzioni)})
    o = (await c.post(f"{BASE}/riautenticazione/passkey/opzioni", headers=_csrf(c))).json()
    r = await c.post(f"{BASE}/riautenticazione", headers=_csrf(c), json={
        "metodo": "PASSKEY", "credenziale": prima.firma(o["opzioni"])})
    assert r.status_code == 200, r.text
    o = (await c.post(f"{BASE}/passkey/opzioni", headers=_csrf(c))).json()["opzioni"]
    r = await c.post(f"{BASE}/passkey", headers=_csrf(c),
                     json={"credenziale": seconda.registra(o)})
    assert r.status_code == 201, r.text
    return [k["id"] for k in (await c.get(BASE)).json()["passkey"]]


async def test_due_revoche_in_parallelo_non_tolgono_ogni_accesso(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSKEY"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        ids = await _due_passkey(c, db_url, token)
        assert len(ids) == 2
        esiti = await asyncio.gather(*[c.delete(f"{BASE}/passkey/{i}", headers=_csrf(c))
                                       for i in ids])
    assert sorted(r.status_code for r in esiti) == [200, 409], [r.text for r in esiti]
    rimaste = await sql(db_url, "SELECT count(*) FROM public.credenziali_passkey "
                                "WHERE user_id = $1", uid)
    assert rimaste[0][0] == 1


async def test_spid_con_codice_fiscale_conta_come_credenziale(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, monkeypatch):  # noqa: F811
    """Con SPID/CIE ammesso e il codice fiscale della persona, l'ultima passkey si può
    revocare (resta l'accesso con l'identità digitale)."""
    ente = await _ente(db_url, ["PASSKEY"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        ids = await _due_passkey(c, db_url, token)

        async def _con_spid(_tenant):  # type: ignore[no-untyped-def]
            return ("SPID_CIE", "PASSKEY")  # aggregatore SPID/CIE attivo per l'ente

        monkeypatch.setattr("app.auth.credenziali_utente.metodi_ammessi_ente", _con_spid)
        assert (await c.delete(f"{BASE}/passkey/{ids[0]}", headers=_csrf(c))).status_code == 200
        r = await c.delete(f"{BASE}/passkey/{ids[1]}", headers=_csrf(c))
        assert r.status_code == 409
        await sql(db_url, "UPDATE public.users SET codice_fiscale = 'PRVPRS80A01H501U' "
                          "WHERE id = $1", uid)
        r = await c.delete(f"{BASE}/passkey/{ids[1]}", headers=_csrf(c))
        assert r.status_code == 200, r.text


async def test_conferme_sbagliate_in_parallelo_non_superano_il_limite(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    """R10 (U1) per la persona: la riserva precede la verifica; 20 conferme sbagliate in
    parallelo con limite 5 danno 5 risposte 422 e 15 risposte 429."""
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        # le conferme riuscite restituiscono la riserva: sei di fila non si bloccano
        for _ in range(6):
            assert (await _conferma(c, db_url, uid, segreto)).status_code == 200
        codice = await _codice(db_url, uid, segreto)
        esiti = await asyncio.gather(*[c.post(
            f"{BASE}/riautenticazione", headers=_csrf(c),
            json={"metodo": "PASSWORD_TOTP", "password": f"sbagliata numero {i}",
                  "codice": codice}) for i in range(20)])
    codici = [r.status_code for r in esiti]
    assert codici.count(422) == 5 and codici.count(429) == 15, codici
    contatore = [v for k, v in redis_finto.dati.items() if ":persona:riautenticazione:" in k]
    assert contatore == ["5"]
    # gli errori contano anche per il blocco dell'account, come all'accesso
    bloccato = await sql(db_url, "SELECT bloccato_fino_a FROM public.credenziali_password "
                                 "WHERE user_id = $1", uid)
    assert bloccato[0][0] is not None


async def test_solo_spid_e_token_non_di_sessione(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    from app.auth.fornitori import svuota_cache_fornitori
    from app.auth.jwt_handler import create_access_token

    ente = await _ente(db_url, ["SPID_CIE", "PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        # l'ente passa a solo SPID/CIE (vale per le rotte subito: metodi letti ora)
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                          "valore, motivo) VALUES ('ENTE', $1, 'ACCESSO_METODI_AMMESSI', "
                          "'[\"SPID_CIE\"]'::jsonb, 'prova R12b')", ente)
        svuota_cache()
        svuota_cache_fornitori()
        # R16 (VD-C-153): il cambio dei metodi dell'ente alza la versione dei metodi e la
        # sessione aperta con la password si chiude al primo uso (prima restava aperta e le
        # rotte rispondevano 403): nessuna rotta delle credenziali resta raggiungibile
        r = await c.get(BASE)
        assert r.status_code == 401 and r.json()["codice"] == "AUTH-003", r.text
        r = await _conferma(c, db_url, uid, segreto)
        assert r.status_code == 401, r.text
        r = await c.post(f"{BASE}/passkey/opzioni", headers=_csrf(c))
        assert r.status_code == 401, r.text
    # un token valido ma NON di una sessione del portale (es. integrazione): rifiutato
    svuota_cache_revoche()
    bearer = create_access_token(tenant_id=str(ente), user_id=str(uid), ruolo="OPERATORE",
                                 scopes=["read"], extra={"auth_method": "jwt"})
    async with _client() as c:
        r = await c.get(BASE, headers={"Authorization": f"Bearer {bearer}"})
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-CREDENZIALI"
        r = await c.get(BASE)
        assert r.status_code == 401  # nessuna sessione


async def test_conferma_con_la_passkey_di_un_altra_persona_rifiutata(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    """La firma VALIDA di una passkey registrata da un'altra persona dello stesso ente non
    conferma la mia identità (la passkey si cerca fra le mie)."""
    ente = await _ente(db_url, ["PASSKEY"])
    _uid_a, token_a = await _invito(db_url, ente)
    _uid_b, token_b = await _invito(db_url, ente, email="collega.prova@example.org")
    mia = Autenticatore(rp_id="localhost", origine=ORIGINE)
    altrui = Autenticatore(rp_id="localhost", origine=ORIGINE)
    for token, email, chiave in ((token_a, EMAIL, mia),
                                 (token_b, "collega.prova@example.org", altrui)):
        async with _client() as c:
            r = await c.post("/api/v1/auth/inviti/verifica", json={"token": token,
                                                                   "email": email})
            passaggio = r.json()["passaggio"]
            opzioni = (await c.post("/api/v1/auth/inviti/passkey/opzioni",
                                    json={"passaggio": passaggio})).json()["opzioni"]
            r = await c.post("/api/v1/auth/inviti/completa", json={
                "passaggio": passaggio, "metodo": "PASSKEY",
                "credenziale": chiave.registra(opzioni)})
            assert r.status_code == 200, r.text
    async with _client() as c:
        corpo = (await c.post("/api/v1/auth/passkey/opzioni")).json()
        r = await c.post("/api/v1/auth/passkey/accesso", json={
            "passaggio": corpo["passaggio"], "credenziale": mia.firma(corpo["opzioni"])})
        assert r.status_code == 200
        o = (await c.post(f"{BASE}/riautenticazione/passkey/opzioni", headers=_csrf(c))).json()
        r = await c.post(f"{BASE}/riautenticazione", headers=_csrf(c), json={
            "metodo": "PASSKEY", "credenziale": altrui.firma(o["opzioni"])})
        assert r.status_code == 422 and r.json()["codice"] == "ACC-422-RIAUTENTICAZIONE"
        assert (await c.get(BASE)).json()["riautenticazione"]["valida_per_secondi"] is None


async def test_credenziale_di_un_metodo_non_piu_ammesso_non_conta(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    """La persona ha password, codice a tempo e una passkey; l'ente passa alla sola
    passkey: la password non serve più ad accedere, quindi la passkey è l'ultima
    credenziale utilizzabile e non si revoca."""
    from app.auth.fornitori import svuota_cache_fornitori

    ente = await _ente(db_url, ["PASSWORD_TOTP", "PASSKEY"])
    uid, token = await _invito(db_url, ente)
    chiave = Autenticatore(rp_id="localhost", origine=ORIGINE)
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        assert (await _conferma(c, db_url, uid, segreto)).status_code == 200
        o = (await c.post(f"{BASE}/passkey/opzioni", headers=_csrf(c))).json()["opzioni"]
        r = await c.post(f"{BASE}/passkey", headers=_csrf(c),
                         json={"credenziale": chiave.registra(o)})
        assert r.status_code == 201, r.text
        id_pk = r.json()["id"]
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                          "valore, motivo) VALUES ('ENTE', $1, 'ACCESSO_METODI_AMMESSI', "
                          "'[\"PASSKEY\"]'::jsonb, 'prova R12b')", ente)
        svuota_cache()
        svuota_cache_fornitori()
        r = await c.delete(f"{BASE}/passkey/{id_pk}", headers=_csrf(c))
        assert r.status_code == 409 and r.json()["codice"] == "ACC-409-ULTIMA", r.text


# ── Revisione R12b (rilievi bassi) ──

async def test_revisione_contatori_sempre_con_scadenza(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    """INCR e scadenza in un passo: una chiave rimasta SENZA scadenza (es. errore fra
    INCR ed EXPIRE del codice precedente) la riceve al primo tentativo, sia per la
    persona sia per l'IP: nessun blocco permanente."""
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        persona = f"acc:persona:riautenticazione:{uid}"
        redis_finto.dati[persona] = "2"            # chiave orfana, senza scadenza
        r = await _conferma(c, db_url, uid, segreto, password="sbagliata davvero 1")  # noqa: S106
        assert r.status_code == 422
        assert 0 < await redis_finto.ttl(persona) <= 900
        ip = [k for k in redis_finto.dati if ":ip:fallimenti:" in k]
        assert ip and all(0 < t <= 900 for t in [await redis_finto.ttl(k) for k in ip])
        for k in ip:
            redis_finto.scadenze.pop(k)            # anche per l'IP: orfana → scadenza
        await _conferma(c, db_url, uid, segreto, password="sbagliata davvero 2")  # noqa: S106
        assert all(0 < t <= 900 for t in [await redis_finto.ttl(k) for k in ip])


async def test_revisione_conferma_solo_dopo_l_audit(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit, monkeypatch):  # noqa: F811
    """Se la voce d'audit della conferma non si scrive, la conferma non vale (503 e
    nessuna chiave in Redis)."""
    from sqlalchemy.exc import OperationalError

    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)

        async def _guasto(*_a, **_k):  # type: ignore[no-untyped-def]
            raise OperationalError("INSERT audit", {}, Exception("database giù"))

        monkeypatch.setattr("app.auth.credenziali_utente.registra_audit", _guasto)
        r = await _conferma(c, db_url, uid, segreto)
        assert r.status_code == 503, r.text
        assert not [k for k in redis_finto.dati if ":riaut:" in k]
        r = await c.post(f"{BASE}/codici-recupero", headers=_csrf(c))
        assert r.status_code == 403 and r.json()["codice"] == "ACC-403-RIAUTENTICAZIONE"


async def test_revisione_segreto_del_codice_a_tempo_non_in_cache(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as c:
        segreto, _ = await _attiva_password(c, token)
        assert (await _conferma(c, db_url, uid, segreto)).status_code == 200
        r = await c.post(f"{BASE}/totp", headers=_csrf(c))
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
