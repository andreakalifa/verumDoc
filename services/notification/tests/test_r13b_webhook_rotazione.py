# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-078) — rotazione del segreto HMAC dei webhook con doppia firma.

  - la rotazione mostra il nuovo segreto UNA volta, cifrato a riposo, solo per
    l'amministratore dell'ente proprietario (altri enti 404, altri ruoli 403);
  - nel periodo di transizione ogni consegna porta due firme ``v1`` (nuovo e
    precedente) calcolate sugli stessi byte e lo stesso istante;
  - scaduta la transizione (orologio del lavoratore) il precedente non firma più;
  - «chiudi la transizione» ritira subito il precedente (WH-013 se non c'è);
  - audit della rotazione senza segreti.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.application.configurazione import imposta_cifratore
from app.application.consegne import esegui_giro
from app.domain.webhook import intestazione_firme, verifica_firma
from app.infrastructure.repo_eventi import registra_evento

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_b3_api import client, crea_ente, h  # noqa: F401
from tests.test_b3_integrazione import (
    CIFRATORE,
    NOME,
    Orologio,
    _evento,
    _inviatore,
    _parametri,
    destinatario,  # noqa: F401 — fixture
)


def test_firme_multiple_stesso_istante_e_corpo() -> None:
    corpo = b'{"a":1}'
    intest = intestazione_firme(["whsec_nuovo", "whsec_vecchio", "whsec_nuovo"], 1000, corpo)
    assert intest.count("v1=") == 2 and intest.startswith("t=1000,")
    for segreto in ("whsec_nuovo", "whsec_vecchio"):
        assert verifica_firma(segreto, intest, corpo, adesso=1000)
    assert not verifica_firma("whsec_altro", intest, corpo, adesso=1000)




@pytest.fixture
def cifratore_di_prova():
    imposta_cifratore(CIFRATORE)
    yield CIFRATORE
    imposta_cifratore(None)


@richiede_pg
async def test_rotazione_doppia_firma_poi_solo_il_nuovo(client, db_url, destinatario,  # noqa: F811
                                                        cifratore_di_prova) -> None:
    ente = await crea_ente(db_url)
    r = await client.post("/api/v1/webhook", headers=h(ente), json={
        "url": f"https://{NOME}:{destinatario.porta}/hook", "tipi": ["versamento.ricevuto"]})
    assert r.status_code == 201, r.text
    creato = r.json()
    vecchio, wid = creato["segreto"], creato["id"]
    r = await client.post(f"/api/v1/webhook/{wid}/segreto/ruota", headers=h(ente),
                          json={"transizione_ore": 2})
    assert r.status_code == 200, r.text
    ruotato = r.json()
    nuovo = ruotato["segreto"]
    assert nuovo != vecchio and nuovo.startswith("whsec_")
    assert ruotato["segreto_suffisso"] == nuovo[-4:]
    assert ruotato["segreto_precedente_suffisso"] == vecchio[-4:]
    assert ruotato["transizione_fino_al"]
    # mai in chiaro nel database; il dettaglio non mostra segreti
    righe = await sql(db_url, "SELECT segreto_cifrato, segreto_precedente_cifrato FROM "
                              "public.webhook_sottoscrizioni")
    assert nuovo not in str(righe[0]) and vecchio not in str(righe[0])
    dettaglio = (await client.get(f"/api/v1/webhook/{wid}", headers=h(ente))).json()
    assert "segreto" not in dettaglio and dettaglio["segreto_precedente_suffisso"]

    await registra_evento(_evento(ente["tenant"]), origine="pdv.received")
    orologio = Orologio()
    inviatore = _inviatore(destinatario)
    try:
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE,
                                 parametri=_parametri(), orologio=orologio) == {"CONSEGNATA": 1}
        prima = destinatario.ricevute[-1]
        istante = int(orologio.adesso.timestamp())
        assert prima.intestazioni["VerumDoc-Firma"].count("v1=") == 2
        for segreto in (nuovo, vecchio):
            assert verifica_firma(segreto, prima.intestazioni["VerumDoc-Firma"], prima.corpo,
                                  adesso=istante)
        # dopo la scadenza della transizione: solo il nuovo
        orologio.adesso = datetime.now(UTC) + timedelta(hours=3)
        await registra_evento(_evento(ente["tenant"]), origine="pdv.received")
        await sql(db_url, "UPDATE public.webhook_consegne SET prossimo_tentativo = $1 "
                          "WHERE stato = 'IN_CODA'", orologio.adesso - timedelta(seconds=1))
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE,
                                 parametri=_parametri(), orologio=orologio) == {"CONSEGNATA": 1}
        dopo = destinatario.ricevute[-1]
        istante = int(orologio.adesso.timestamp())
        assert dopo.intestazioni["VerumDoc-Firma"].count("v1=") == 1
        assert verifica_firma(nuovo, dopo.intestazioni["VerumDoc-Firma"], dopo.corpo,
                              adesso=istante)
        assert not verifica_firma(vecchio, dopo.intestazioni["VerumDoc-Firma"], dopo.corpo,
                                  adesso=istante)
    finally:
        await inviatore.chiudi()
    audit = await sql(db_url, "SELECT dettagli FROM public.audit_global WHERE evento = "
                              "'webhook.segreto_ruotato'")
    assert len(audit) == 1
    testo = json.dumps(audit[0]["dettagli"]) if not isinstance(audit[0]["dettagli"], str) \
        else audit[0]["dettagli"]
    assert nuovo not in testo and vecchio not in testo


@richiede_pg
async def test_chiusura_della_transizione_e_permessi(client, db_url,  # noqa: F811
                                                     cifratore_di_prova) -> None:
    ente, altro = await crea_ente(db_url), await crea_ente(db_url)
    wid = (await client.post("/api/v1/webhook", headers=h(ente), json={
        "url": "https://ricevitore.cliente-prova.it/hook",
        "tipi": ["versamento.ricevuto"]})).json()["id"]
    r = await client.post(f"/api/v1/webhook/{wid}/segreto/chiudi-transizione", headers=h(ente))
    assert r.status_code == 409 and r.json()["codice"] == "WH-013"
    # altro ente: non esiste; operatore: non autorizzato
    r = await client.post(f"/api/v1/webhook/{wid}/segreto/ruota", headers=h(altro), json={})
    assert r.status_code == 404
    r = await client.post(f"/api/v1/webhook/{wid}/segreto/ruota",
                          headers=h(ente, "OPERATORE"), json={})
    assert r.status_code == 403
    r = await client.post(f"/api/v1/webhook/{wid}/segreto/ruota", headers=h(ente),
                          json={"transizione_ore": 0})
    assert r.status_code == 422
    assert (await client.post(f"/api/v1/webhook/{wid}/segreto/ruota", headers=h(ente),
                              json={})).status_code == 200
    r = await client.post(f"/api/v1/webhook/{wid}/segreto/chiudi-transizione", headers=h(ente))
    assert r.status_code == 200 and r.json()["segreto_precedente_suffisso"] is None
    righe = await sql(db_url, "SELECT segreto_precedente_cifrato FROM "
                              "public.webhook_sottoscrizioni WHERE id = $1::uuid", wid)
    assert righe[0]["segreto_precedente_cifrato"] is None
