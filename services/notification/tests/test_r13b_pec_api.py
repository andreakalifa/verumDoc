# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-078) — console dei canali PEC (notification /api/v1/notifiche/pec).

  - registro, prova e ricevute non associate: solo ruoli fissi del conservatore con il
    permesso mai delegabile ``pec_canali:gestisci``; un ruolo personalizzato o un ente
    non entrano (403), nemmeno con il permesso nel token;
  - configurazione validata (segreti rifiutati, versione letta, tipo immutabile) e audit;
  - prova accodata sul canale scelto, con limite orario atomico;
  - messaggi: il conservatore vede tutto, l'amministratore solo il proprio ente
    (anche il messaggio grezzo delle ricevute).
"""
from __future__ import annotations

import uuid

import pytest

from tests.b3_supporto import token_utente
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_b3_api import client, crea_ente  # noqa: F401
from tools.simulatore_pec.gestore import Pki

pytestmark = richiede_pg

CONF = {"indirizzo": "conservazione@pec.conservatore-prova.it",
        "smtp_host": "smtps.pec.gestore.it", "imap_host": "imaps.pec.gestore.it"}


async def _conservatore(url: str, ruolo: str = "RSC_CONSERVATORE") -> dict:
    t = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Conservatore', $3, 'ATTIVO', "
                   "'c@example.org')",
              t, t.hex[:16], f"t_{t.hex[:20]}")
    u = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, $3, 'R', 'S', CAST($4 AS user_ruolo))",
              u, t, f"{u.hex[:8]}@example.org", ruolo)
    return {"Authorization": f"Bearer {token_utente(t, ruolo, u)}"}


def _corpo(versione: int = 0, **kw) -> dict:
    return {"tipo": "SMTP_IMAP", "nome": "Gestore PEC", "attivo": True, "priorita": 1,
            "configurazione": dict(CONF), "versione": versione} | kw


async def test_solo_il_conservatore_con_il_permesso(client, db_url) -> None:  # noqa: F811
    ente = await crea_ente(db_url)
    token = token_utente(ente['tenant'], 'ADMIN_TENANT', ente['ADMIN_TENANT'])
    adm = {"Authorization": f"Bearer {token}"}
    for metodo, percorso, corpo in (("GET", "/api/v1/notifiche/pec/canali", None),
                                    ("PUT", "/api/v1/notifiche/pec/canali/pec-1", _corpo()),
                                    ("POST", "/api/v1/notifiche/pec/canali/pec-1/prova",
                                     {"destinatario": "prova@pec.ente-prova.it"}),
                                    ("GET", "/api/v1/notifiche/pec/ricevute", None)):
        r = await client.request(metodo, percorso, json=corpo, headers=adm)
        assert r.status_code == 403 and r.json()["codice"] == "NTF-403-PEC", (percorso, r.text)
    # un OPERATORE con il permesso «concesso» nel token: mai (mai delegabile)
    falso = {"Authorization": "Bearer " + token_utente(ente["tenant"], "OPERATORE",
                                                       ente["OPERATORE"])}
    r = await client.get("/api/v1/notifiche/pec/messaggi", headers=falso)
    assert r.status_code == 403
    r = await client.get("/api/v1/notifiche/pec/canali")
    assert r.status_code == 401


async def test_registro_validato_versionato_e_in_audit(client, db_url) -> None:  # noqa: F811
    h = await _conservatore(db_url)
    r = await client.put("/api/v1/notifiche/pec/canali/pec-1",
                         json=_corpo(configurazione=CONF | {"password": "x"}), headers=h)
    assert r.status_code == 422 and r.json()["codice"] == "NTF-422-PEC"
    assert any("segreti" in m for m in r.json()["dettagli"]["motivi"])
    r = await client.put("/api/v1/notifiche/pec/canali/pec-1", json=_corpo(), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["versione"] == 1 and r.json()["configurazione"]["smtp_porta"] == 465
    r = await client.put("/api/v1/notifiche/pec/canali/pec-1", json=_corpo(versione=0), headers=h)
    assert r.status_code == 409
    r = await client.put("/api/v1/notifiche/pec/canali/pec-1",
                         json=_corpo(versione=1, tipo="SVILUPPO"), headers=h)
    assert r.status_code == 422 and "tipo" in r.json()["detail"]
    r = await client.put("/api/v1/notifiche/pec/canali/pec-1",
                         json=_corpo(versione=1, attivo=False), headers=h)
    assert r.status_code == 200 and r.json()["versione"] == 2
    elenco = (await client.get("/api/v1/notifiche/pec/canali", headers=h)).json()
    assert [c["codice"] for c in elenco["items"]] == ["pec-1"]
    assert elenco["items"][0]["stato"] is None   # il lavoratore non l'ha ancora visto
    assert elenco["variabile_segreti"] == "PEC_SEGRETI_JSON" and elenco["sviluppo_ammesso"]
    audit = await sql(db_url, "SELECT evento FROM public.audit_global WHERE evento = "
                              "'pec.canale_modificato'")
    assert len(audit) == 2


async def test_prova_accodata_con_limite_orario(client, db_url, monkeypatch) -> None:  # noqa: F811
    from app.config import settings

    h = await _conservatore(db_url)
    await client.put("/api/v1/notifiche/pec/canali/pec-1", json=_corpo(), headers=h)
    monkeypatch.setattr(settings, "pec_prove_per_ora", 2)
    r = await client.post("/api/v1/notifiche/pec/canali/pec-1/prova",
                          json={"destinatario": "prova@example.org"}, headers=h)
    assert r.status_code == 422   # indirizzo segnaposto
    for _ in range(2):
        r = await client.post("/api/v1/notifiche/pec/canali/pec-1/prova",
                              json={"destinatario": "prova@pec.ente-prova.it"}, headers=h)
        assert r.status_code == 202, r.text
    r = await client.post("/api/v1/notifiche/pec/canali/pec-1/prova",
                          json={"destinatario": "prova@pec.ente-prova.it"}, headers=h)
    assert r.status_code == 429 and r.json()["codice"] == "NTF-429-PEC"
    righe = await sql(db_url, "SELECT caso_uso, canale_codice, tenant_id, stato FROM "
                              "public.pec_messaggi")
    assert {(r["caso_uso"], r["canale_codice"], r["tenant_id"], r["stato"]) for r in righe} == {
        ("PROVA", "pec-1", None, "IN_CODA")}
    r = await client.post("/api/v1/notifiche/pec/canali/sconosciuto/prova",
                          json={"destinatario": "prova@pec.ente-prova.it"}, headers=h)
    assert r.status_code in (404, 429)


async def test_messaggi_per_ente_e_ricevute(client, db_url) -> None:  # noqa: F811
    h = await _conservatore(db_url)
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    for ente in (a, b):
        await sql(db_url, "INSERT INTO public.pec_messaggi (tenant_id, caso_uso, oggetto_id, "
                          "destinatario, oggetto, testo, stato, message_id, canale_codice, "
                          "inviato_il) VALUES ($1, 'SCARTO', $2, 'p@pec.e.it', 'Oggetto', 'Testo', "
                          "'INVIATO', $3, 'pec-1', now() - interval '2 hours')",
                  ente["tenant"], str(uuid.uuid4()), f"<{uuid.uuid4()}@pec.c.it>")
    mid_a = (await sql(db_url, "SELECT id FROM public.pec_messaggi WHERE tenant_id = $1",
                       a["tenant"]))[0]["id"]
    rid = (await sql(db_url, "INSERT INTO public.pec_ricevute (canale_codice, imap_uidvalidity, "
                             "imap_uid, esito, messaggio_id, tenant_id, tipo, eml) VALUES "
                             "('pec-1', 1, 1, 'ASSOCIATA', $1, $2, 'ACCETTAZIONE', 'eml-a') "
                             "RETURNING id", mid_a, a["tenant"]))[0]["id"]
    estranea = (await sql(db_url, "INSERT INTO public.pec_ricevute (canale_codice, "
                                  "imap_uidvalidity, imap_uid, esito, motivo, eml) VALUES "
                                  "('pec-1', 1, 2, 'NON_AUTENTICA', 'firma non valida', 'x') "
                                  "RETURNING id"))[0]["id"]
    tutti = (await client.get("/api/v1/notifiche/pec/messaggi", headers=h)).json()
    assert len(tutti["items"]) == 2 and tutti["cursore"] is None
    assert all("accettazione" in (m["avviso"] or "") for m in tutti["items"])
    pagina = (await client.get("/api/v1/notifiche/pec/messaggi?limite=1", headers=h)).json()
    seconda = (await client.get(f"/api/v1/notifiche/pec/messaggi?limite=1&dopo={pagina['cursore']}",
                                headers=h)).json()
    assert {pagina["items"][0]["id"], seconda["items"][0]["id"]} == {
        m["id"] for m in tutti["items"]}
    adm_a = {"Authorization": "Bearer " + token_utente(a["tenant"], "ADMIN_TENANT",
                                                       a["ADMIN_TENANT"])}
    propri = (await client.get("/api/v1/notifiche/pec/messaggi", headers=adm_a)).json()
    assert [m["id"] for m in propri["items"]] == [str(mid_a)]
    dettaglio = (await client.get(f"/api/v1/notifiche/pec/messaggi/{mid_a}", headers=adm_a)).json()
    assert [x["tipo"] for x in dettaglio["ricevute"]] == ["ACCETTAZIONE"]
    altro = (await sql(db_url, "SELECT id FROM public.pec_messaggi WHERE tenant_id = $1",
                       b["tenant"]))[0]["id"]
    assert (await client.get(f"/api/v1/notifiche/pec/messaggi/{altro}",
                             headers=adm_a)).status_code == 404
    eml = await client.get(f"/api/v1/notifiche/pec/ricevute/{rid}/eml", headers=adm_a)
    assert eml.status_code == 200 and eml.content == b"eml-a"
    assert eml.headers["content-type"].startswith("message/rfc822")
    # l'ente non scarica i messaggi non associati (possono essere di chiunque)
    assert (await client.get(f"/api/v1/notifiche/pec/ricevute/{estranea}/eml",
                             headers=adm_a)).status_code == 404
    non_associate = (await client.get("/api/v1/notifiche/pec/ricevute", headers=h)).json()
    assert [x["esito"] for x in non_associate["items"]] == ["NON_AUTENTICA"]


@pytest.mark.parametrize("ruolo", ["SUPER_ADMIN"])
async def test_super_admin_vede_il_registro(client, db_url, ruolo) -> None:  # noqa: F811
    h = await _conservatore(db_url, ruolo)
    r = await client.get("/api/v1/notifiche/pec/canali", headers=h)
    assert r.status_code == 200
    assert Pki  # il gestore di prova è importabile dai test del servizio
