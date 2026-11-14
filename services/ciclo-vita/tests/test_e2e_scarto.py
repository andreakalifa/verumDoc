# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
End-to-end dello SCARTO (REGOLE §23.6, contratto Q2, VD-C-072): proposta
(anteprima senza scritture → applica con l'impronta del piano) → autorizzazione
del produttore (allegato firmato nel WORM) → nulla osta della Soprintendenza
(PA) → ESEGUIBILE → esecuzione fuori richiesta con retention scaduta simulata in
moto → tombstone nel registro (contratto Q3) → verbale nel WORM → ESEGUITA ed
evento ``scarto.eseguito``.

PostgreSQL reale (template ``VD_TEST_DATABASE_URL``), S3 moto con Object Lock
COMPLIANCE (moto rifiuta davvero la cancellazione di una versione sotto
retention), registro delle impronte finto che rispetta il contratto Q3.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from app.infrastructure import storage

from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    FUTURO,
    PASSATO,
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_pda,
    crea_pdv,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
    oggetti_pda,
    outbox,
    versioni,
)

pytestmark = [pytest.mark.integration, richiede_pg]

# R08: firme vere (CA di prova); R12a (VD-C-145): documenti che citano la pratica
from tests.firme_prova import autorizzazione as aut_firmata  # noqa: E402 — dopo pytestmark
from tests.firme_prova import autorizzazione_pdf  # noqa: E402


async def _scenario(db_url, s3_ciclo, *, pa: bool = True):
    tenant = await crea_ente(db_url, pa=pa)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    conservatore_ente = await crea_ente(db_url)
    rsc = await crea_utente(db_url, conservatore_ente, "RSC_CONSERVATORE")
    classe = await crea_classe(db_url, tenant)
    s3 = bucket_worm(s3_ciclo)
    pda = {}
    # scartabile: termine scaduto nel DB e retention WORM scaduta
    for nome in ("scaduto_1", "scaduto_2"):
        pdv = await crea_pdv(db_url, tenant, classe, admin)
        aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
        oggetti_pda(s3, tenant, aip, fino_a=PASSATO)
        pda[nome] = (aip, urn)
    # termine scaduto nel DB ma retention WORM ancora attiva: bloccato all'esecuzione
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    oggetti_pda(s3, tenant, aip, fino_a=FUTURO)
    pda["retention_attiva"] = (aip, urn)
    # legal hold attivo: bloccato all'esecuzione
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    oggetti_pda(s3, tenant, aip, fino_a=PASSATO, legal_hold=True)
    pda["legal_hold"] = (aip, urn)
    # termine non scaduto: bloccato già nell'anteprima
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=datetime.now(UTC) + timedelta(days=90))
    oggetti_pda(s3, tenant, aip, fino_a=FUTURO)
    pda["non_scaduto"] = (aip, urn)
    return {"tenant": tenant, "admin": admin, "rsc": rsc, "rsc_ente": conservatore_ente,
            "s3": s3, "pda": pda}


async def _transizione(c, pratica_id, chi, da, a, motivo="Passaggio di stato della prova",
                       allegati=()):
    return await c.post(f"/api/v1/ciclo-vita/pratiche/{pratica_id}/transizione",
                        headers=chi, json={"stato_atteso": da, "nuovo_stato": a,
                                           "allegati": list(allegati), "motivo": motivo})


async def test_scarto_end_to_end_pa(db_url, s3_ciclo, monkeypatch) -> None:
    sc = await _scenario(db_url, s3_ciclo, pa=True)
    tenant, s3 = sc["tenant"], sc["s3"]
    admin = intestazioni(tenant, "ADMIN_TENANT", sc["admin"])
    rsc = intestazioni(sc["rsc_ente"], "RSC_CONSERVATORE", sc["rsc"])
    corpo = {"ente": str(tenant), "criteri": {"classi": ["contratti"], "anni": [2015]},
             "motivo": "Scarto dei contratti 2015 secondo il piano di conservazione"}
    cancellate: list[str] = []
    originale = storage.elimina_versione

    def spia(client, bucket, oggetto):
        cancellate.append(oggetto.chiave)
        return originale(client, bucket, oggetto)

    monkeypatch.setattr(storage, "elimina_versione", spia)

    async with client_app() as c:
        # 1. anteprima: nessuna scrittura
        prima = await sql(db_url, "SELECT count(*) AS n FROM public.pratiche")
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=admin, json=corpo)
        assert r.status_code == 200, r.text
        piano = r.json()
        assert piano["totale"] == 5 and piano["selezionabili"] == 4
        assert piano["bloccati"] == {"RETENTION_NON_SCADUTA": 1}
        assert piano["richiede_nulla_osta"] is True
        assert (await sql(db_url, "SELECT count(*) AS n FROM public.pratiche"))[0]["n"] == \
            prima[0]["n"]
        # 2. applica con l'impronta del piano
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=admin,
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
        assert r.status_code == 201, r.text
        pratica = r.json()
        pid = pratica["id"]
        assert pratica["stato"] == "BOZZA" and pratica["richiede_nulla_osta"] is True
        assert pratica["conteggi"] == {"SELEZIONATO": 4, "BLOCCATO": 1}
        # 3. BOZZA → PROPOSTA (chi avvia)
        r = await _transizione(c, pid, admin, "BOZZA", "PROPOSTA")
        assert r.status_code == 200, r.text
        # 4. autorizzazione del produttore: allegato firmato obbligatorio
        r = await _transizione(c, pid, admin, "PROPOSTA", "AUTORIZZATA_PRODUTTORE")
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-ALLEGATI"
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=admin,
                         data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                         files={"file": ("autorizzazione.pdf.p7m", aut_firmata(pid),
                                         "application/pkcs7-mime")})
        assert r.status_code == 201, r.text
        autorizzazione = r.json()
        assert autorizzazione["firma"]["presente"] is True
        # R08: firma VERIFICATA con la CA di prova (non più solo rilevata)
        assert autorizzazione["firma"]["verificata"] is True
        assert autorizzazione["firma"]["in_parole"] == "valida"
        assert autorizzazione["firma"]["firme"][0]["firmatario"] == "Dirigente di prova"
        r = await _transizione(c, pid, admin, "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                               allegati=[autorizzazione["id"]])
        assert r.status_code == 200, r.text
        # 5. PA: nulla osta della Soprintendenza come file
        r = await _transizione(c, pid, rsc, "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-TRANSIZIONE"
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=admin,
                         data={"tipo": "NULLA_OSTA"},
                         files={
                             "file": ("nulla_osta.pdf", b"%PDF-1.7 nulla osta", "application/pdf")})
        assert r.status_code == 201, r.text
        # R08 (D22): nulla osta senza firma accettato con avviso (NULLA_OSTA_FIRMA = AVVISO)
        assert "avviso" in r.json()["firma"]
        nulla_osta = r.json()["id"]
        r = await _transizione(c, pid, admin, "AUTORIZZATA_PRODUTTORE", "NULLA_OSTA",
                               allegati=[nulla_osta])
        assert r.status_code == 200, r.text
        r = await _transizione(c, pid, rsc, "NULLA_OSTA", "ESEGUIBILE")
        assert r.status_code == 200, r.text
        # 6. esecuzione: solo il conservatore, poi il lavoratore
        r = await _transizione(c, pid, admin, "ESEGUIBILE", "ESEGUITA")
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-RUOLO"
        r = await _transizione(c, pid, rsc, "ESEGUIBILE", "ESEGUITA",
                               motivo="Esecuzione dello scarto autorizzato")
        assert r.status_code == 202, r.text
        assert r.json()["stato"] == "ESEGUIBILE" and r.json()["esecuzione_richiesta"] is True

        dip = dipendenze(db_url)
        esiti = await lavora(dip)
        assert esiti and esiti[-1] == "COMPLETATO"

        r = await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=rsc)
        dettaglio = r.json()
    assert dettaglio["stato"] == "ESEGUITA", dettaglio
    assert dettaglio["conteggi"] == {"ELIMINATO": 2, "BLOCCATO": 3}
    assert [p["a_stato"] for p in dettaglio["cronologia"]] == [
        "BOZZA", "PROPOSTA", "AUTORIZZATA_PRODUTTORE", "NULLA_OSTA", "ESEGUIBILE", "ESEGUITA"]
    assert {a["stato"] for a in dettaglio["autorizzazioni"]} == {
        "AUTORIZZATA_PRODUTTORE", "NULLA_OSTA", "ESEGUIBILE"}

    p = sc["pda"]
    # oggetti cancellati SOLO dei PdA con retention scaduta; nessun tentativo sugli altri
    for nome in ("scaduto_1", "scaduto_2"):
        aip, _ = p[nome]
        assert versioni(s3, f"{tenant}/{aip}/") == []
    for nome in ("retention_attiva", "legal_hold", "non_scaduto"):
        aip, _ = p[nome]
        assert len(versioni(s3, f"{tenant}/{aip}/")) == 3
        assert not any(k.startswith(f"{tenant}/{aip}/") for k in cancellate), nome
    stati = {r["id"]: r["stato"] for r in await sql(
        db_url, "SELECT id, stato::text FROM public.aip WHERE tenant_id = $1", tenant)}
    assert stati[p["scaduto_1"][0]] == "ELIMINAZIONE"
    assert stati[p["retention_attiva"][0]] == "CONSERVATO"
    motivi = {r["aip_id"]: r["motivo"] for r in await sql(
        db_url, "SELECT aip_id, motivo FROM public.pratiche_oggetti WHERE pratica_id = $1::uuid",
        pid)}
    assert motivi[p["retention_attiva"][0]] == "RETENTION_NON_SCADUTA"
    assert motivi[p["legal_hold"][0]] == "LEGAL_HOLD"
    assert motivi[p["non_scaduto"][0]] == "RETENTION_NON_SCADUTA"

    # verbale nel WORM con retention e impronta; tombstone con l'impronta del verbale
    verbale = next(a for a in dettaglio["allegati"] if a["tipo"] == "VERBALE_SCARTO")
    riga = (await sql(db_url, "SELECT bucket, chiave, sha256 FROM public.pratiche_allegati "
                              "WHERE id = $1::uuid", verbale["id"]))[0]
    oggetto = s3.head_object(Bucket=riga["bucket"], Key=riga["chiave"], ChecksumMode="ENABLED")
    assert oggetto["ObjectLockMode"] == "COMPLIANCE"
    assert base64.b64decode(oggetto["ChecksumSHA256"]).hex() == riga["sha256"]
    xml = s3.get_object(Bucket=riga["bucket"], Key=riga["chiave"])["Body"].read().decode()
    assert p["scaduto_1"][1] in xml and 'esito="ELIMINATO"' in xml and "LEGAL_HOLD" in xml
    foglie = dip.registro.foglie
    assert {k[1] for k in foglie} == {p["scaduto_1"][1], p["scaduto_2"][1]}
    assert all(k[0] == "scarto" and k[2] == pid for k in foglie)
    assert all(v["impronta"] == riga["sha256"] for v in foglie.values())
    con_foglia = await sql(db_url, "SELECT count(*) AS n FROM public.pratiche_oggetti WHERE "
                                   "pratica_id = $1::uuid AND foglia_id IS NOT NULL", pid)
    assert con_foglia[0]["n"] == 2

    # eventi e audit
    eseguiti = [e for e in await outbox(db_url, "scarto.eseguito") if e["pratica_id"] == pid]
    assert len(eseguiti) == 1 and eseguiti[0]["verbale_sha256"] == riga["sha256"]
    cambi = [e["a_stato"] for e in await outbox(db_url, "pratica.cambio_stato")
             if e["pratica_id"] == pid]
    assert cambi[-1] == "ESEGUITA" and "NULLA_OSTA" in cambi
    voci = {r["evento"] for r in await sql(
        db_url, "SELECT evento FROM public.audit_global WHERE tenant_id = $1", tenant)}
    assert {"ciclo_vita.scarto.proposta", "ciclo_vita.transizione", "ciclo_vita.allegato",
            "ciclo_vita.esecuzione_richiesta", "ciclo_vita.scarto.pda_eliminato",
            "ciclo_vita.scarto.eseguito"} <= voci
    esecuzioni = await sql(db_url, "SELECT esito FROM public.pratiche_esecuzioni "
                                   "WHERE pratica_id = $1::uuid", pid)
    assert [e["esito"] for e in esecuzioni][-1] == "COMPLETATO"


async def test_scarto_piano_cambiato_409_senza_scritture(db_url, s3_ciclo) -> None:
    sc = await _scenario(db_url, s3_ciclo, pa=False)
    admin = intestazioni(sc["tenant"], "ADMIN_TENANT", sc["admin"])
    corpo = {"ente": str(sc["tenant"]), "criteri": {"anni": [2015]},
             "motivo": "Scarto dei documenti del 2015 della prova"}
    async with client_app() as c:
        piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=admin,
                              json=corpo)).json()
        # un PdA in più dopo l'anteprima: il piano cambia
        classe = (await sql(db_url, "SELECT id FROM public.classi_documentali WHERE tenant_id = $1",
                            sc["tenant"]))[0]["id"]
        pdv = await crea_pdv(db_url, sc["tenant"], classe, sc["admin"])
        await crea_pda(db_url, sc["tenant"], pdv, scadenza=PASSATO)
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=admin,
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
    assert r.status_code == 409 and r.json()["codice"] == "LC-409-PIANO-CAMBIATO"
    assert r.json()["dettagli"]["piano"]["totale"] == 6
    assert (await sql(db_url, "SELECT count(*) AS n FROM public.pratiche WHERE tenant_id = $1",
                      sc["tenant"]))[0]["n"] == 0


async def test_scarto_non_pa_salta_nulla_osta_e_seconda_proposta_bloccata(db_url, s3_ciclo
                                                                          ) -> None:
    sc = await _scenario(db_url, s3_ciclo, pa=False)
    admin = intestazioni(sc["tenant"], "ADMIN_TENANT", sc["admin"])
    rsc = intestazioni(sc["rsc_ente"], "RSC_CONSERVATORE", sc["rsc"])
    corpo = {"ente": str(sc["tenant"]), "criteri": {"anni": [2015]},
             "motivo": "Scarto dei documenti del 2015 della prova"}
    async with client_app() as c:
        piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=rsc,
                              json=corpo)).json()
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=rsc,
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
        assert r.status_code == 201, r.text
        pratica = r.json()
        assert pratica["richiede_nulla_osta"] is False
        # una seconda proposta vede gli stessi PdA bloccati (già in un'altra pratica)
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=admin, json=corpo)
        assert r.json()["bloccati"].get("IN_ALTRA_PRATICA") == 4
        # stato atteso diverso → 409 STATO_CAMBIATO
        r = await _transizione(c, pratica["id"], admin, "PROPOSTA", "AUTORIZZATA_PRODUTTORE")
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-STATO-CAMBIATO"
        assert r.json()["dettagli"]["stato_attuale"] == "BOZZA"
        # l'autorizzazione non firmata è rifiutata
        await _transizione(c, pratica["id"], admin, "BOZZA", "PROPOSTA")
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pratica['id']}/allegati", headers=admin,
                         data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                         files={"file": ("autorizzazione.pdf", b"%PDF-1.7 senza firma",
                                         "application/pdf")})
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-FIRMA"
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pratica['id']}/allegati", headers=admin,
                         data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                         files={"file": ("autorizzazione.pdf",
                                         b"%PDF-1.7 /Type /Sig /ByteRange [0 1 2 3] firmato",
                                         "application/pdf")})
        # R08: una firma PAdES finta ora si verifica e si rifiuta
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-FIRMA"
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pratica['id']}/allegati", headers=admin,
                         data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                         files={"file": ("autorizzazione.pdf", autorizzazione_pdf(pratica["id"]),
                                         "application/pdf")})
        assert r.status_code == 201 and r.json()["firma"]["formato"] == "PAdES"
        assert r.json()["firma"]["in_parole"] == "valida"
        allegato = r.json()["id"]
        r = await _transizione(c, pratica["id"], admin, "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                               allegati=[allegato])
        assert r.status_code == 200
        # l'allegato già usato non vale per un'altra transizione; non PA: diretta a ESEGUIBILE
        r = await _transizione(c, pratica["id"], rsc, "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 200, r.text
        r = await _transizione(c, pratica["id"], rsc, "ESEGUIBILE", "ESEGUITA")
        assert r.status_code == 202
        r = await _transizione(c, pratica["id"], rsc, "ESEGUIBILE", "ESEGUITA")
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-IN-ESECUZIONE"
