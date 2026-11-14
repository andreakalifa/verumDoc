# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R10 — ruoli personalizzati (D30), inviti (D08), fornitori d'identità (D08) e
indirizzi ammessi SFTP (D12) nella console, sul PostgreSQL vero.

  - D30: nessuna escalation (permesso mai delegabile, non concedibile per l'ente,
    ruolo base ADMIN_TENANT, chi non è amministratore, token con un ruolo
    personalizzato); assegnazione con versione dei permessi e sessioni chiuse;
    modifica con la versione letta; disattivazione solo se non assegnato; audit;
  - D08 inviti: utente non attivo, invito con la sola impronta, e-mail sull'outbox
    (topic notification.send) con il collegamento nel frammento, reinvio che fa
    decadere il precedente, revoca, disattivazione dell'utente che revoca;
  - D08 fornitori: solo il conservatore, nessun segreto, https, audit;
  - D12: elenco degli indirizzi entro i limiti del conservatore, audit.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from libs.shared.parametri import svuota_cache
from libs.shared.security.revoca import svuota_cache_revoche
from libs.shared.security.sigillo import Apritore, contesto_posta
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.posta_prova import POSTA_PRIVATA
from tests.test_g5_gestione_utenti import _chiama, _ente, _tok, _utente

pytestmark = [pytest.mark.integration, richiede_pg]


@pytest.fixture(autouse=True)
def _cache_pulite():
    svuota_cache()
    svuota_cache_revoche()
    yield
    svuota_cache()


async def _eventi(url: str, evento: str) -> list[dict]:
    righe = await sql(url, "SELECT dettagli::text AS d FROM public.audit_global WHERE evento = $1 "
                           "ORDER BY created_at", evento)
    return [json.loads(r["d"]) for r in righe]


def _url(tid: uuid.UUID, coda: str = "") -> str:
    return f"/api/v1/onboarding/tenant/{tid}/ruoli{coda}"


async def test_ruoli_nessuna_escalation(db_url) -> None:  # noqa: F811
    tid = await _ente(db_url)
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    tok = _tok(tid, adm, "ADMIN_TENANT")
    corpo = {"nome": "Operatore con annullamenti", "ruolo_base": "OPERATORE",
             "permessi": ["versamento:esegui", "annullamento:richiedi"]}
    r = await _chiama("POST", _url(tid), tok, corpo)
    assert r.status_code == 201, r.text
    assert r.json()["permessi"] == ["annullamento:richiedi", "archivio:consulta",
                                    "versamento:esegui"]
    for permessi, base in ((["scarto:esegui"], "OPERATORE"),
                           (["annullamento:approva"], "VIEWER"),
                           (["firmatari:gestisci"], "VIEWER"),
                           (["pratica:chiudi_eccezioni"], "OPERATORE"),
                           (["utenti:gestisci"], "OPERATORE"),
                           (["versamento:esegui"], "ADMIN_TENANT"),
                           (["permesso:inventato"], "VIEWER")):
        r = await _chiama("POST", _url(tid), tok, {"nome": f"x {permessi[0]} {base}",
                                                   "ruolo_base": base, "permessi": permessi})
        assert r.status_code == 403 and r.json()["codice"] == "RUO-403", (permessi, r.text)
    # il conservatore non concede le esibizioni a questo ente
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                      "motivo) VALUES ('ENTE', $1, 'RUOLI_PERMESSI_CONCEDIBILI', "
                      "'[\"versamento:esegui\", \"annullamento:richiedi\"]'::jsonb, 'prova')", tid)
    svuota_cache()
    r = await _chiama("POST", _url(tid), tok, {"nome": "Esibizioni", "ruolo_base": "VIEWER",
                                               "permessi": ["esibizione:richiedi"]})
    assert r.json()["codice"] == "RUO-403" and "non consente" in r.json()["detail"]
    # chi non è amministratore, o ha un ruolo personalizzato, non gestisce ruoli
    op = await _utente(db_url, tid, "OPERATORE")
    assert (await _chiama("POST", _url(tid), _tok(tid, op, "OPERATORE"), corpo)).status_code \
        == 403
    # un token con ruolo personalizzato (anche se il ruolo del token fosse ADMIN_TENANT)
    # non gestisce l'ente: la revoca passa (utente coerente), il controllo no
    from app.auth.jwt_handler import create_access_token
    rid = (await sql(db_url, "SELECT id FROM public.ruoli_personalizzati WHERE tenant_id = $1",
                     tid))[0][0]
    finto = await _utente(db_url, tid, "ADMIN_TENANT")
    await sql(db_url, "UPDATE public.users SET ruolo_personalizzato_id = $1 WHERE id = $2", rid,
              finto)
    con_rp = create_access_token(tenant_id=str(tid), user_id=str(finto), ruolo="ADMIN_TENANT",
                                 rp=str(rid), pv=1,
                                 permessi=["ruoli:gestisci", "utenti:gestisci"])
    for metodo, coda, corpo_rp in (("GET", "", None), ("POST", "", corpo)):
        r = await _chiama(metodo, _url(tid, coda), con_rp, corpo_rp)
        assert r.status_code == 403, (metodo, r.text)
    r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/inviti", con_rp,
                      {"email": "c@comune-prova.it", "nome": "Cia", "cognome": "Prova"})
    assert r.status_code == 403
    # un altro ente: 404 (nessun dato rivelato)
    altro = await _ente(db_url)
    assert (await _chiama("GET", _url(altro), tok)).status_code == 404
    assert len(await _eventi(db_url, "ruolo.creato")) == 1


async def test_assegnazione_modifica_e_disattivazione(db_url) -> None:  # noqa: F811
    tid = await _ente(db_url)
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    tok = _tok(tid, adm, "ADMIN_TENANT")
    r = await _chiama("POST", _url(tid), tok, {"nome": "Lettore che versa", "ruolo_base": "VIEWER",
                                               "permessi": ["versamento:esegui"]})
    ruolo = r.json()
    vw = await _utente(db_url, tid, "OPERATORE")
    await sql(db_url, "INSERT INTO public.sessioni_attive (impronta, user_id, ruolo, "
                      "inattivita_secondi, scade_il) VALUES ($1, $2, 'OPERATORE', 1800, "
                      "now() + interval '1 hour')", hashlib.sha256(vw.bytes).hexdigest(), vw)
    r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{vw}", tok,
                      {"ruolo_personalizzato_id": ruolo["id"]})
    assert r.status_code == 200, r.text
    riga = (await sql(db_url, "SELECT ruolo::text AS ruolo, ruolo_personalizzato_id::text AS rp, "
                              "permessi_versione FROM public.users WHERE id = $1", vw))[0]
    assert (riga["ruolo"], riga["rp"], riga["permessi_versione"]) == ("VIEWER", ruolo["id"], 2)
    assert (await sql(db_url, "SELECT count(*) FROM public.sessioni_attive WHERE user_id = $1",
                      vw))[0][0] == 0
    assert (await _eventi(db_url, "utente.ruolo_assegnato"))[0]["dopo"] == ruolo["id"]
    # modifica con una versione vecchia: 409; con quella letta: versione utenti +1
    r = await _chiama("PUT", _url(tid, f"/{ruolo['id']}"), tok,
                      {"versione": ruolo["versione"] + 1, "permessi": []})
    assert r.status_code == 409
    r = await _chiama("PUT", _url(tid, f"/{ruolo['id']}"), tok,
                      {"versione": ruolo["versione"], "permessi": []})
    assert r.status_code == 200 and r.json()["permessi"] == ["archivio:consulta"]
    assert (await sql(db_url, "SELECT permessi_versione FROM public.users WHERE id = $1",
                      vw))[0][0] == 3
    # assegnato: non si disattiva
    r = await _chiama("POST", _url(tid, f"/{ruolo['id']}/disattiva"), tok)
    assert r.status_code == 409
    # ruolo fisso di nuovo: il personalizzato si toglie
    r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{vw}", tok,
                      {"ruolo": "OPERATORE"})
    assert r.status_code == 200
    assert (await sql(db_url, "SELECT ruolo_personalizzato_id FROM public.users WHERE id = $1",
                      vw))[0][0] is None
    assert (await _chiama("POST", _url(tid, f"/{ruolo['id']}/disattiva"), tok)).status_code == 200
    # un ruolo di un altro ente non si assegna (chiave esterna composta e controllo)
    altro = await _ente(db_url)
    adm2 = await _utente(db_url, altro, "ADMIN_TENANT")
    r2 = await _chiama("POST", _url(altro), _tok(altro, adm2, "ADMIN_TENANT"),
                       {"nome": "Altro ente", "ruolo_base": "VIEWER", "permessi": []})
    r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{vw}", tok,
                      {"ruolo_personalizzato_id": r2.json()["id"]})
    assert r.status_code == 404


async def test_inviti_creazione_reinvio_revoca(db_url, monkeypatch) -> None:  # noqa: F811
    from app.config import settings
    monkeypatch.setattr(settings, "base_url_console", "https://console.prova.example")
    tid = await _ente(db_url)
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    tok = _tok(tid, adm, "ADMIN_TENANT")
    url = f"/api/v1/onboarding/tenant/{tid}/inviti"
    r = await _chiama("POST", url, tok, {"email": "Nuova.Persona@Example.org", "nome": "Nuova",
                                         "cognome": "Persona", "ruolo": "VIEWER"})
    assert r.status_code == 201, r.text
    invito = r.json()
    assert "token" not in json.dumps(invito) and "#invito=" not in json.dumps(invito)
    utente = (await sql(db_url, "SELECT attivo, email FROM public.users WHERE id = $1::uuid",
                        invito["user_id"]))[0]
    assert (utente["attivo"], utente["email"]) == (False, "nuova.persona@example.org")
    posta = await sql(db_url, "SELECT topic, payload::text AS p FROM public.outbox_eventi "
                              "WHERE topic = 'notification.send'")
    assert len(posta) == 1
    corpo = json.loads(posta[0]["p"])
    assert corpo["destinatario"] == "nuova.persona@example.org"
    # R12a (VD-C-152): nell'outbox il collegamento è SIGILLATO (nessun segreto in chiaro)
    assert "#invito=" not in posta[0]["p"] and tid.hex + "." not in posta[0]["p"]
    assert "{{segreto:collegamento}}" in corpo["testo"]
    aperto = Apritore.da_impostazioni(POSTA_PRIVATA).apri(
        corpo["segreti"]["collegamento"],
        contesto_posta(str(tid), corpo["oggetto_id"], corpo["destinatario"]))
    collegamento = aperto.split("https://console.prova.example/attiva#invito=")[1]
    assert collegamento.startswith(tid.hex + ".")
    impronta = (await sql(db_url, "SELECT impronta FROM public.inviti"))[0][0]
    assert impronta == hashlib.sha256(collegamento.encode()).hexdigest()
    # stessa e-mail nell'ente: 409
    r = await _chiama("POST", url, tok, {"email": "nuova.persona@example.org", "nome": "Altra",
                                         "cognome": "Persona"})
    assert r.status_code == 409 and r.json()["codice"] == "ACC-409-INVITO"
    # l'amministratore non invita amministratori; l'operatore non invita
    r = await _chiama("POST", url, tok, {"email": "a@example.org", "nome": "Ammi",
                                         "cognome": "Nistratore", "ruolo": "ADMIN_TENANT"})
    assert r.status_code == 403
    op = await _utente(db_url, tid, "OPERATORE")
    r = await _chiama("POST", url, _tok(tid, op, "OPERATORE"),
                      {"email": "b@example.org", "nome": "Bea", "cognome": "Prova"})
    assert r.status_code == 403
    # reinvio: il vecchio decade, uno solo in attesa
    r = await _chiama("POST", f"{url}/{invito['id']}/reinvia", tok)
    assert r.status_code == 200, r.text
    stati = sorted(x["stato"] for x in await sql(db_url, "SELECT stato FROM public.inviti"))
    assert stati == ["IN_ATTESA", "REVOCATO"]
    elenco = (await _chiama("GET", url, tok)).json()["items"]
    assert [i["stato"] for i in elenco] == ["IN_ATTESA", "REVOCATO"]
    # disattivare l'utente revoca l'invito in attesa
    r = await _chiama("DELETE", f"/api/v1/onboarding/tenant/{tid}/utenti/{invito['user_id']}",
                      tok)
    assert r.status_code == 204
    assert {x["stato"] for x in await sql(db_url, "SELECT stato FROM public.inviti")} == \
        {"REVOCATO"}
    assert len(await _eventi(db_url, "invito.creato")) == 1
    assert len(await _eventi(db_url, "invito.reinviato")) == 1


async def test_fornitori_solo_conservatore_senza_segreti(db_url) -> None:  # noqa: F811
    tid = await _ente(db_url)
    rsc = await _utente(db_url, tid, "RSC_CONSERVATORE")
    tok = _tok(tid, rsc, "RSC_CONSERVATORE")
    url = "/api/v1/onboarding/accesso/fornitori"
    assert [f["registrato"] for f in (await _chiama("GET", url, tok)).json()["items"]] == \
        [False, False, False]
    spid = {"nome": "Aggregatore di prova", "attivo": True, "versione": 0, "configurazione": {
        "aggregatore_url": "https://aggregatore.prova.example", "client_id": "verumdoc",
        "issuer": "https://aggregatore.prova.example", "livello_minimo": 2,
        "jwks_url": "https://aggregatore.prova.example/jwks"}}
    con_segreto = json.loads(json.dumps(spid))
    con_segreto["configurazione"]["client_secret"] = "non-si-salva"
    r = await _chiama("PUT", f"{url}/SPID_CIE", tok, con_segreto)
    assert r.status_code == 422 and r.json()["codice"] == "ACC-422-FORNITORE"
    r = await _chiama("PUT", f"{url}/SPID_CIE", tok, spid)
    assert r.status_code == 200, r.text
    assert r.json()["versione"] == 1
    r = await _chiama("PUT", f"{url}/SPID_CIE", tok, spid)  # versione vecchia
    assert r.status_code == 409
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    assert (await _chiama("GET", url, _tok(tid, adm, "ADMIN_TENANT"))).status_code == 403
    assert len(await _eventi(db_url, "accesso.fornitore_modificato")) == 1
    # vista dell'ente: SPID/CIE (fornitore attivo e predefinito del conservatore)
    r = await _chiama("GET", f"/api/v1/onboarding/tenant/{tid}/accesso",
                      _tok(tid, adm, "ADMIN_TENANT"))
    assert r.json()["metodi_ammessi"] == ["SPID_CIE"]


async def test_sftp_indirizzi_ammessi(db_url) -> None:  # noqa: F811
    tid = await _ente(db_url)
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    tok = _tok(tid, adm, "ADMIN_TENANT")
    aid = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.sftp_account (id, tenant_id, nome_utente, "
                      "responsabile_id) VALUES ($1, $2, $3, $4)", aid, tid, f"ente{aid.hex[:6]}",
              adm)
    url = f"/api/v1/onboarding/tenant/{tid}/sftp/account/{aid}/ip-ammessi"
    r = await _chiama("PUT", url, tok, {"ip_ammessi": ["203.0.113.0/24", " 198.51.100.7 ",
                                                       "203.0.113.0/24", "2001:db8::/48"]})
    assert r.status_code == 200, r.text
    assert r.json()["ip_ammessi"] == ["203.0.113.0/24", "198.51.100.7/32", "2001:db8::/48"]
    for errato in (["0.0.0.0/0"], ["10.0.0.0/8"], ["10.0.0.1/24"], ["non-un-ip"],
                   ["2001:db8::/32"]):
        r = await _chiama("PUT", url, tok, {"ip_ammessi": errato})
        assert r.status_code == 422 and r.json()["codice"] == "SFTP-422-IP", errato
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                      "motivo) VALUES ('CONSERVATORE', NULL, 'SFTP_ALLOWLIST_VOCI_MASSIME', "
                      "'2'::jsonb, 'prova') ON CONFLICT ON CONSTRAINT "
                      "uq_impostazioni_valori_livello DO UPDATE SET valore = EXCLUDED.valore")
    svuota_cache()
    r = await _chiama("PUT", url, tok, {"ip_ammessi": ["192.0.2.1", "192.0.2.2", "192.0.2.3"]})
    assert r.status_code == 422
    eventi = await _eventi(db_url, "sftp.account.ip_ammessi")
    assert eventi[0]["nuovo"] == ["203.0.113.0/24", "198.51.100.7/32", "2001:db8::/48"]


async def test_v2_reinvio_e_revoca_con_le_regole_della_gestione_utenti(db_url) -> None:  # noqa: F811
    """Prova della revisione (test_rev_reinvia.py) come regressione: l'ADMIN dell'ente
    non riattiva né revoca l'invito di un altro amministratore (404 come /utenti)."""
    tid = await _ente(db_url)
    a = await _utente(db_url, tid, "ADMIN_TENANT")
    b = await _utente(db_url, tid, "ADMIN_TENANT", attivo=False)
    usato, attesa = uuid.uuid4(), uuid.uuid4()
    for iid, stato, imp in ((usato, "USATO", b"x"), (attesa, "IN_ATTESA", b"y")):
        await sql(db_url, "INSERT INTO public.inviti (id, tenant_id, user_id, email, impronta, "
                          "stato, scade_il, created_at) VALUES ($1, $2, $3, 'b@x.it', $4, $5, "
                          "now() + interval '1 day', now() - interval '1 hour')",
                  iid, tid, b, hashlib.sha256(imp).hexdigest(), stato)
    tok = _tok(tid, a, "ADMIN_TENANT")
    base = f"/api/v1/onboarding/tenant/{tid}/inviti"
    assert (await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{b}", tok,
                          {"attivo": True})).status_code == 404
    assert (await _chiama("POST", f"{base}/{usato}/reinvia", tok)).status_code == 404
    assert (await _chiama("DELETE", f"{base}/{attesa}", tok)).status_code == 404
    stati = sorted(r["stato"] for r in await sql(db_url, "SELECT stato FROM public.inviti "
                                                         "WHERE user_id = $1", b))
    assert stati == ["IN_ATTESA", "USATO"]
    # il conservatore sì
    sa = await _utente(db_url, tid, "SUPER_ADMIN")
    assert (await _chiama("DELETE", f"{base}/{attesa}", _tok(tid, sa, "SUPER_ADMIN"))
            ).status_code == 204
    # un OPERATORE disattivato l'ADMIN lo reinvita
    op = await _utente(db_url, tid, "OPERATORE", attivo=False)
    iop = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.inviti (id, tenant_id, user_id, email, impronta, stato, "
                      "scade_il) VALUES ($1, $2, $3, 'o@x.it', $4, 'USATO', now())", iop, tid, op,
              hashlib.sha256(b"z").hexdigest())
    assert (await _chiama("POST", f"{base}/{iop}/reinvia", tok)).status_code == 200


async def test_v2_reinvio_ruolo_personalizzato_non_piu_assegnabile(db_url) -> None:  # noqa: F811
    """Reinviare l'invito di chi ha un ruolo personalizzato è come assegnarglielo di
    nuovo: se il ruolo comprende permessi che l'ente oggi non concede, no."""
    tid = await _ente(db_url)
    a = await _utente(db_url, tid, "ADMIN_TENANT")
    tok = _tok(tid, a, "ADMIN_TENANT")
    r = await _chiama("POST", _url(tid), tok, {"nome": "Esibisce", "ruolo_base": "VIEWER",
                                               "permessi": ["esibizione:richiedi"]})
    assert r.status_code == 201, r.text
    rid = uuid.UUID(r.json()["id"])
    v = await _utente(db_url, tid, "VIEWER", attivo=False)
    await sql(db_url, "UPDATE public.users SET ruolo_personalizzato_id = $1, permessi_versione = 1 "
                      "WHERE id = $2", rid, v)
    iid = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.inviti (id, tenant_id, user_id, email, impronta, stato, "
                      "scade_il) VALUES ($1, $2, $3, 'v@x.it', $4, 'USATO', now())", iid, tid, v,
              hashlib.sha256(b"v").hexdigest())
    base = f"/api/v1/onboarding/tenant/{tid}/inviti"
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                      "motivo) VALUES ('ENTE', $1, 'RUOLI_PERMESSI_CONCEDIBILI', "
                      "'[\"versamento:esegui\"]'::jsonb, 'prova')", tid)
    svuota_cache()
    r = await _chiama("POST", f"{base}/{iid}/reinvia", tok)
    assert r.status_code == 403 and r.json()["codice"] == "RUO-403", r.text
    await sql(db_url, "DELETE FROM public.impostazioni_valori WHERE tenant_id = $1", tid)
    svuota_cache()
    assert (await _chiama("POST", f"{base}/{iid}/reinvia", tok)).status_code == 200


async def test_vdc152_senza_chiave_della_posta_nessun_invito(db_url, monkeypatch) -> None:  # noqa: F811
    """R12a (VD-C-152): senza chiave pubblica della posta (o con una non valida) l'invito
    non si crea: 503 PRIMA di ogni scrittura (nessun utente, invito o evento)."""
    from app.config import settings
    tid = await _ente(db_url)
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    tok = _tok(tid, adm, "ADMIN_TENANT")
    for valore in ("", "non-base64!", "QUJD"):
        monkeypatch.setattr(settings, "posta_chiave_pubblica", valore)
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/inviti", tok,
                          {"email": "x@example.org", "nome": "Ics", "cognome": "Prova"})
        assert r.status_code == 503, r.text
    assert await sql(db_url, "SELECT 1 FROM public.users WHERE email = 'x@example.org'") == []
    assert await sql(db_url, "SELECT 1 FROM public.inviti") == []
    assert await sql(db_url, "SELECT 1 FROM public.outbox_eventi "
                             "WHERE topic = 'notification.send'") == []


async def test_vdc152_sigillo_legato_al_destinatario(db_url, monkeypatch) -> None:  # noqa: F811
    """Il sigillo si apre solo con il contesto (ente, invito, destinatario) dell'evento:
    cambiare il destinatario nella coda non fa partire il collegamento verso un altro."""
    from libs.shared.security.sigillo import ErroreSigillo
    tid = await _ente(db_url)
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/inviti",
                      _tok(tid, adm, "ADMIN_TENANT"),
                      {"email": "giusta@example.org", "nome": "Giu", "cognome": "Sta"})
    assert r.status_code == 201, r.text
    corpo = json.loads((await sql(db_url, "SELECT payload::text AS p FROM public.outbox_eventi "
                                          "WHERE topic = 'notification.send'"))[0]["p"])
    apritore = Apritore.da_impostazioni(POSTA_PRIVATA)
    for contesto in (contesto_posta(str(tid), corpo["oggetto_id"], "altro@example.org"),
                     contesto_posta(str(uuid.uuid4()), corpo["oggetto_id"], "giusta@example.org"),
                     contesto_posta(str(tid), "invito:" + str(uuid.uuid4()), "giusta@example.org")):
        with pytest.raises(ErroreSigillo):
            apritore.apri(corpo["segreti"]["collegamento"], contesto)


async def test_vdc152_collegamenti_in_chiaro_rimasti_dopo_il_deploy(db_url) -> None:  # noqa: F811
    """Revisione R12a: eventi d'invito in chiaro scritti da pod con il codice precedente,
    una volta pubblicati, e notifiche già inviate perdono il collegamento al giro del
    lavoratore; un evento non ancora pubblicato resta (parte come prima)."""
    from app.services.programmate import minimizza_inviti_in_chiaro
    tid = await _ente(db_url)
    testo = "Apri https://console.prova.example/attiva#invito=abc.SEGRETO123 entro domani"
    for pubblicato in (True, False):
        await sql(db_url, "INSERT INTO public.outbox_eventi (origine, gruppo, tenant_id, topic, "
                          "chiave, payload, pubblicato_il) VALUES ('onboarding', 0, $1, "
                          "'notification.send', 'k', CAST($2 AS jsonb), "
                          "CASE WHEN $3 THEN now() ELSE NULL END)",
                  tid, json.dumps({"evento": "accesso.invito", "testo": testo}), pubblicato)
    await sql(
        db_url, "INSERT INTO public.notifications (id, tenant_id, tipo, canale, destinatario, "
                      "payload_json, stato, tentativi, created_at, updated_at) VALUES "
                      "(gen_random_uuid(), $1, 'accesso.invito', 'email', 'a@cliente-prova.it', "
                      "CAST($2 AS jsonb), 'INVIATA', 1, now(), now())",
              tid, json.dumps({"oggetto_id": "invito:x", "testo": testo}))
    assert await minimizza_inviti_in_chiaro() == 2
    righe = await sql(db_url, "SELECT pubblicato_il IS NOT NULL AS pubblicato, payload->>'testo' "
                              "AS t FROM public.outbox_eventi WHERE topic = 'notification.send'")
    assert {r["pubblicato"]: "SEGRETO123" in r["t"] for r in righe} == {True: False, False: True}
    notifica = await sql(db_url, "SELECT payload_json->>'testo' AS t FROM public.notifications")
    assert "SEGRETO123" not in notifica[0]["t"] and "rimosso" in notifica[0]["t"]
    assert await minimizza_inviti_in_chiaro() == 0   # idempotente
