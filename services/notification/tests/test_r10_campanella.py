# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R10 (D32) — campanella «da fare per te», preferenze e posta per utente, sul
PostgreSQL vero.

  - la campanella legge gli eventi dell'ente già registrati, filtrati per permesso:
    l'annullamento da approvare lo vede l'amministratore, non l'operatore; un
    ruolo personalizzato non vede le azioni mai delegabili;
  - un «da fare» si risolve quando la pratica cambia stato di nuovo;
  - letti per persona; gli eventi di un altro ente non si segnano;
  - avvisi obbligatori dell'ente non si spengono (422); preferenze con la versione;
  - e-mail alle persone che l'hanno scelta (o per obbligo dell'ente).
"""
from __future__ import annotations

import json
import uuid

from libs.shared.parametri import svuota_cache
from tests.b3_supporto import token_utente
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_b3_api import client, crea_ente  # noqa: F401

pytestmark = richiede_pg


async def _evento(url: str, tenant, tipo: str, dati: dict, posizione: int) -> int:
    riga = await sql(url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, "
                          "risorsa_id, dati, posizione) VALUES ($1, $2, 'avviso', $3, "
                          "CAST($4 AS jsonb), $5) RETURNING id", tenant, tipo,
                     str(uuid.uuid4()), json.dumps(dati), posizione)
    return int(riga[0]["id"])


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def test_campanella_per_permesso_risoluzione_e_letti(client, db_url):  # noqa: F811
    svuota_cache()
    ente = await crea_ente(db_url)
    t = ente["tenant"]
    pratica = str(uuid.uuid4())
    base = 9_000_000 + int(uuid.uuid4().int % 1_000_000) * 10
    da_approvare = await _evento(db_url, t, "ciclo_vita.avviso", {
        "sottotipo": "ANNULLAMENTO_DA_APPROVARE", "pratica_id": pratica}, base + 1)
    await _evento(db_url, t, "quota.soglia_raggiunta", {"soglia": 80}, base + 2)
    await _evento(db_url, t, "versamento.ricevuto", {}, base + 3)  # non va in campanella
    adm = token_utente(t, "ADMIN_TENANT", ente["ADMIN_TENANT"])
    op = token_utente(t, "OPERATORE", ente["OPERATORE"])
    r = await client.get("/api/v1/notifiche/campanella", headers=_h(adm))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert [e["id"] for e in corpo["da_fare"]] == [da_approvare]
    assert [e["categoria"] for e in corpo["avvisi"]] == ["QUOTE"]
    assert corpo["non_letti"] == 2
    # l'operatore non approva annullamenti: lo vede solo l'amministratore
    corpo_op = (await client.get("/api/v1/notifiche/campanella", headers=_h(op))).json()
    assert corpo_op["da_fare"] == [] and [e["categoria"] for e in corpo_op["avvisi"]] == ["QUOTE"]
    # un ruolo personalizzato con «annullamento:approva» nei permessi non lo ottiene
    from app.api.autenticazione import chiavi_jwt
    from app.config import settings

    from libs.shared.security.jwt_rs256 import firma_token
    rp = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.ruoli_personalizzati (id, tenant_id, nome, ruolo_base, "
                      "permessi) VALUES ($1, $2, 'Prova', 'OPERATORE', '{}')", rp, t)
    # utente (e token) con ruolo ADMIN_TENANT e un ruolo personalizzato: non emesso dal
    # gateway (la base è OPERATORE o VIEWER), ma l'azione non delegabile resta negata
    await sql(db_url, "UPDATE public.users SET ruolo_personalizzato_id = $1, ruolo = "
                      "'ADMIN_TENANT' WHERE id = $2", rp, ente["OPERATORE"])
    falso = firma_token({"sub": "x", "tenant_id": str(t), "user_id": str(ente["OPERATORE"]),
                         "ruolo": "ADMIN_TENANT", "rp": str(rp), "pv": 1,
                         "permessi": ["annullamento:approva", "versamento:esegui"]},
                        chiave_privata_pem=chiavi_jwt().privata_pem,
                        emittente=settings.jwt_issuer, pubblico=settings.jwt_audience,
                        durata_secondi=600)
    corpo_rp = (await client.get("/api/v1/notifiche/campanella", headers=_h(falso))).json()
    assert corpo_rp["da_fare"] == []
    # risoluzione: la pratica cambia stato dopo l'avviso
    await _evento(db_url, t, "pratica.cambio_stato", {"pratica_id": pratica, "tipo": "ANNULLAMENTO",
                                                      "a_stato": "APPROVATA"}, base + 4)
    corpo = (await client.get("/api/v1/notifiche/campanella", headers=_h(adm))).json()
    assert corpo["da_fare"] == []
    # letti: solo eventi dell'ente
    altro = await crea_ente(db_url)
    estraneo = await _evento(db_url, altro["tenant"], "quota.soglia_raggiunta", {"soglia": 100},
                             base + 5)
    r = await client.post("/api/v1/notifiche/campanella/letti", headers=_h(adm),
                          json={"ids": [da_approvare, estraneo]})
    assert r.json()["letti"] == 1
    r = await client.post("/api/v1/notifiche/campanella/letti", headers=_h(adm),
                          json={"tutti": True})
    assert (await client.get("/api/v1/notifiche/campanella", headers=_h(adm))).json()[
        "non_letti"] == 0
    vw = token_utente(t, "VIEWER", ente["VIEWER"])
    assert (await client.get("/api/v1/notifiche/campanella", headers=_h(vw))).json()[
        "non_letti"] == 0  # il VIEWER non versa: la quota non lo riguarda


async def test_preferenze_obbligatorie_e_posta(client, db_url):  # noqa: F811
    ente = await crea_ente(db_url)
    t = ente["tenant"]
    # indirizzi veri (example.org è un segnaposto: la posta lo scarta)
    await sql(db_url, "UPDATE public.users SET email = replace(email, 'example.org', "
                      "'comune-prova.it') WHERE tenant_id = $1", t)
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                      "motivo) VALUES ('ENTE', $1, 'NOTIFICHE_OBBLIGATORIE', "
                      "'[\"ALLARMI.EMAIL\"]'::jsonb, 'prova')", t)
    svuota_cache()
    op = token_utente(t, "OPERATORE", ente["OPERATORE"])
    r = await client.get("/api/v1/notifiche/preferenze", headers=_h(op))
    allarmi = next(c for c in r.json()["categorie"] if c["codice"] == "ALLARMI")
    assert allarmi["email"] is True and allarmi["email_obbligatoria"] is True
    r = await client.put("/api/v1/notifiche/preferenze", headers=_h(op), json={
        "versione": 0, "scelte": {"ALLARMI": {"console": True, "email": False}}})
    assert r.status_code == 422 and r.json()["codice"] == "NTF-422-OBBLIGATORIA"
    r = await client.put("/api/v1/notifiche/preferenze", headers=_h(op), json={
        "versione": 0, "scelte": {"QUOTE": {"console": False, "email": True}}})
    assert r.status_code == 200 and r.json()["versione"] == 1
    r = await client.put("/api/v1/notifiche/preferenze", headers=_h(op), json={
        "versione": 0, "scelte": {"QUOTE": {"console": True, "email": True}}})
    assert r.status_code == 409
    # posta: QUOTE all'operatore che l'ha scelta; ALLARMI a tutti (obbligatoria)
    from app.services.destinatari import destinatari_utenti
    email_op = (await sql(db_url, "SELECT email FROM public.users WHERE id = $1",
                          ente["OPERATORE"]))[0][0]
    assert await destinatari_utenti(str(t), "quota.soglia_raggiunta", {"soglia": 80}) == \
        [email_op]
    tutti = await destinatari_utenti(str(t), "integrita.anomalia", {"aip_id": str(uuid.uuid4())})
    assert email_op in tutti and len(tutti) == 3
    # chiavi API: niente campanella
    from libs.shared.errori import ErroreApi  # noqa: F401
    r = await client.get("/api/v1/notifiche/campanella",
                         headers={"X-Api-Key": "vd_inesistente_segreto"})
    assert r.status_code == 401
