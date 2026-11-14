# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Riversamento in uscita, restituzione di fine contratto e riversamento in ingresso
(contratto Q2 di R05; REGOLE §9.1, §13.1, §10.3, §19.5).

Uscita/restituzione: PdD MIGRAZIONE chiesti all'API di distribution (finto che
ne rispetta il contratto: ≤ 100 PdA per richiesta, poi PRONTO), manifesto nel
WORM, export dei registri (restituzione), evento ``riversamento.pronto``.
Ingresso: PdD SInCRO di un altro conservatore caricato con una sessione del
gateway (oggetto nello staging + riga ``caricamenti``), copiato nel WORM,
indice letto con defusedxml, impronte ricalcolate, versamento per riferimento
con i metadati che citano l'IdC d'origine; un file alterato → RESPINTA.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from tests.firme_prova import autorizzazione  # R08: firma vera (CA di prova)
from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
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
    outbox,
    s3,
)

pytestmark = [pytest.mark.integration, richiede_pg]


async def _ente_con_pda(db_url, n: int = 3):
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant)
    pda = []
    for i in range(n):
        pdv = await crea_pdv(db_url, tenant, classe, admin)
        pda.append(await crea_pda(db_url, tenant, pdv, scadenza=PASSATO,
                                  stato="CONSERVATO" if i else "IN_CATENA"))
    return tenant, admin, pda


async def test_riversamento_uscita(db_url, s3_ciclo) -> None:
    tenant, admin, pda = await _ente_con_pda(db_url)
    conservatore = await crea_ente(db_url)
    rsc = await crea_utente(db_url, conservatore, "RSC_CONSERVATORE")
    bucket_worm(s3_ciclo)
    chi_admin = intestazioni(tenant, "ADMIN_TENANT", admin)
    chi_rsc = intestazioni(conservatore, "RSC_CONSERVATORE", rsc)
    corpo = {"ente": str(tenant), "criteri": {"classi": ["contratti"]},
             "conservatore_destinatario": "Nuovo conservatore S.p.A.",
             "motivo": "Cambio di conservatore per scadenza del contratto"}
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/riversamento-uscita", headers=chi_admin,
                         json=corpo)
        assert r.status_code == 200, r.text
        piano = r.json()
        assert piano["conteggi"] == {"BLOCCATO": 1, "SELEZIONATO": 2}
        r = await c.post("/api/v1/ciclo-vita/pratiche/riversamento-uscita", headers=chi_admin,
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert r.json()["stato"] == "PROPOSTA"
        # l'ente non può rendere eseguibile: solo il conservatore
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi_admin,
                         json={"stato_atteso": "PROPOSTA", "nuovo_stato": "ESEGUIBILE",
                               "motivo": "Approvazione del riversamento"})
        assert r.status_code == 403
        for da, a, codice in (("PROPOSTA", "ESEGUIBILE", 200), ("ESEGUIBILE", "ESEGUITA", 202)):
            r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi_rsc,
                             json={"stato_atteso": da, "nuovo_stato": a,
                                   "motivo": "Approvazione del riversamento"})
            assert r.status_code == codice, r.text
        dip = dipendenze(db_url)
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=chi_admin)).json()
        oggetti = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}/oggetti?esito=ESPORTATO",
                               headers=chi_admin)).json()
    assert dettaglio["stato"] == "ESEGUITA", dettaglio
    assert dettaglio["conteggi"] == {"BLOCCATO": 1, "ESPORTATO": 2}
    assert len(oggetti["items"]) == 2 and all(o["dettagli"]["zip_sha256"] for o in oggetti["items"])
    assert dip.esportatore.richieste[0]["destinatario"] == "Nuovo conservatore S.p.A."
    assert set(dip.esportatore.richieste[0]["aip_ids"]) == {str(a) for a, _ in pda[1:]}
    manifesto = next(a for a in dettaglio["allegati"] if a["tipo"] == "MANIFESTO")
    riga = (await sql(db_url, "SELECT bucket, chiave FROM public.pratiche_allegati WHERE id = "
                              "$1::uuid", manifesto["id"]))[0]
    corpo_m = json.loads(s3(s3_ciclo).get_object(Bucket=riga["bucket"], Key=riga["chiave"])[
        "Body"].read())
    assert corpo_m["formato"] == "verumdoc-manifesto-riversamento-1"
    assert {p["esito"] for p in corpo_m["pacchetti"]} == {"ESPORTATO", "BLOCCATO"}
    eventi = [e for e in await outbox(db_url, "riversamento.pronto") if e["pratica_id"] == pid]
    assert eventi and eventi[0]["manifesto_sha256"] == manifesto["sha256"]


async def test_restituzione_con_export_registri(db_url, s3_ciclo) -> None:
    tenant, admin, pda = await _ente_con_pda(db_url, n=2)
    conservatore = await crea_ente(db_url)
    rsc = await crea_utente(db_url, conservatore, "RSC_CONSERVATORE")
    bucket_worm(s3_ciclo)
    chi_admin = intestazioni(tenant, "ADMIN_TENANT", admin)
    chi_rsc = intestazioni(conservatore, "RSC_CONSERVATORE", rsc)
    corpo = {"ente": str(tenant), "motivo": "Fine del contratto di conservazione"}
    async with client_app() as c:
        piano = (await c.post("/api/v1/ciclo-vita/pratiche/restituzione", headers=chi_rsc,
                              json=corpo)).json()
        r = await c.post("/api/v1/ciclo-vita/pratiche/restituzione", headers=chi_rsc,
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi_rsc, json={
            "stato_atteso": "BOZZA", "nuovo_stato": "PROPOSTA", "motivo": "Proposta all'ente"})
        allegato = (await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=chi_admin,
                                 data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                                 # R12b (VD-C-160): il testo firmato cita la pratica
                                 files={"file": ("richiesta.p7m", autorizzazione(pid),
                                                 "application/pkcs7-mime")}
                                 )).json()
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi_admin, json={
            "stato_atteso": "PROPOSTA", "nuovo_stato": "AUTORIZZATA_PRODUTTORE",
            "allegati": [allegato["id"]], "motivo": "L'ente autorizza la restituzione"})
        assert r.status_code == 200, r.text
        for da, a in (("AUTORIZZATA_PRODUTTORE", "ESEGUIBILE"), ("ESEGUIBILE", "ESEGUITA")):
            r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi_rsc,
                             json={"stato_atteso": da, "nuovo_stato": a,
                                   "motivo": "Esecuzione della restituzione"})
            assert r.status_code in (200, 202), r.text
        dip = dipendenze(db_url)
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=chi_admin)).json()
    assert dettaglio["stato"] == "ESEGUITA", dettaglio
    tipi = {a["tipo"] for a in dettaglio["allegati"]}
    assert {"MANIFESTO", "EXPORT_REGISTRI", "AUTORIZZAZIONE_PRODUTTORE"} <= tipi
    # R12b (VD-C-160): il lavoratore ha riverificato l'autorizzazione dal WORM
    fatti = await sql(db_url, "SELECT tipo FROM public.esiti_esecuzione WHERE pratica_id = "
                              "$1::uuid AND esito = 'AUTORIZZAZIONI_VERIFICATE'", pid)
    assert [f["tipo"] for f in fatti] == ["RESTITUZIONE"]
    export = next(a for a in dettaglio["allegati"] if a["tipo"] == "EXPORT_REGISTRI")
    riga = (await sql(db_url, "SELECT bucket, chiave FROM public.pratiche_allegati WHERE id = "
                              "$1::uuid", export["id"]))[0]
    righe = [json.loads(x) for x in s3(s3_ciclo).get_object(
        Bucket=riga["bucket"], Key=riga["chiave"])["Body"].read().splitlines()]
    sezioni = {r["sezione"] for r in righe}
    assert {"audit", "pratiche"} <= sezioni
    assert any(r["sezione"] == "pratiche" and r["id"] == pid for r in righe)


# ── Ingresso ─────────────────────────────────────────────────────────────

def _zip_pdd(*, altera: bool = False) -> tuple[bytes, dict[str, str]]:
    contenuti = {"documenti/delibera.pdf": b"%PDF-1.7 delibera dell'altro conservatore",
                 "documenti/allegato.txt": b"allegato tecnico"}
    impronte = {n: hashlib.sha256(c).hexdigest() for n, c in contenuti.items()}
    file_xml = "".join(
        f"<File><NomeFile>{n}</NomeFile><TipoFile>{'PRINCIPALE' if 'delibera' in n else 'ALLEGATO'}"
        f"</TipoFile><Hash><Impronta>{h}</Impronta><Algoritmo>SHA-256</Algoritmo></Hash></File>"
        for n, h in impronte.items())
    indice = (f'<?xml version="1.0"?><SInCRO xmlns="urn:uni:uninfo:sincro:2020"><Intestazione>'
              f"<IdentificativoAIP>IDC-ALTRO-77</IdentificativoAIP><ConservatoreAIP>Altro"
              f"</ConservatoreAIP></Intestazione><Descrizione><MetadatiDocumento>"
              f"<IdentificativoDocumento>DEL-12</IdentificativoDocumento>"
              f"<ModalitaFormazione>a</ModalitaFormazione><Oggetto>Delibera</Oggetto>"
              f"</MetadatiDocumento></Descrizione><FileGroup>{file_xml}</FileGroup></SInCRO>")
    flusso = io.BytesIO()
    with zipfile.ZipFile(flusso, "w") as z:
        z.writestr("IndiceAIP_SInCRO.xml", indice)
        for n, c in contenuti.items():
            z.writestr(n, c + (b"!" if altera and "allegato" in n else b""))
        z.writestr("IndiceAIP_SInCRO.xml.p7m", b"firma")
    return flusso.getvalue(), impronte


async def _caricamento(db_url, endpoint, tenant, utente, contenuto: bytes) -> uuid.UUID:
    client = s3(endpoint)
    try:
        client.create_bucket(Bucket="verumdoc-pdv")
    except Exception as exc:  # noqa: BLE001 — già creato nella sessione
        _ = exc  # già creato nella sessione: si prosegue
    cid = uuid.uuid4()
    chiave = f"caricamenti/{tenant}/{cid}/pdd.zip"
    client.put_object(Bucket="verumdoc-pdv", Key=chiave, Body=contenuto)
    await sql(db_url,
              "INSERT INTO public.caricamenti (id, tenant_id, utente_id, canale, "
              "impronta_richiesta, "
              "nome_file, dimensione, dimensione_parte, numero_parti, stato, bucket, "
              "chiave_oggetto, "
              "impronte, scade_il, completato_il) VALUES ($1, $2, $3, 'REST', $4, 'pdd.zip', $5, "
              "$5, "
              "1, 'COMPLETO', 'verumdoc-pdv', $6, CAST($7 AS jsonb), $8, now())",
              cid, tenant, utente, "aa" * 32, len(contenuto), chiave,
              json.dumps({"SHA-256": hashlib.sha256(contenuto).hexdigest()}),
              datetime.now(UTC) + timedelta(days=1))
    return cid


async def _ingresso(db_url, s3_ciclo, *, altera: bool):
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    await crea_classe(db_url, tenant, "delibere")
    conservatore = await crea_ente(db_url)
    rsc = await crea_utente(db_url, conservatore, "RSC_CONSERVATORE")
    bucket_worm(s3_ciclo)
    contenuto, impronte = _zip_pdd(altera=altera)
    cid = await _caricamento(db_url, s3_ciclo, tenant, admin, contenuto)
    chi_admin = intestazioni(tenant, "ADMIN_TENANT", admin)
    chi_rsc = intestazioni(conservatore, "RSC_CONSERVATORE", rsc)
    dip = dipendenze(db_url)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/riversamento-ingresso", headers=chi_admin,
                         json={"ente": str(tenant), "caricamento_id": str(cid),
                               "classe_documentale": "delibere",
                               "conservatore_origine": "Altro conservatore",
                               "metadati": {"tipologia_documentale": "delibera"},
                               "motivo": "Presa in carico dal precedente conservatore"})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        await lavora(dip)  # acquisizione e verifica
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=chi_admin)).json()
        if dettaglio["stato"] == "PROPOSTA":
            for da, a in (("PROPOSTA", "ESEGUIBILE"), ("ESEGUIBILE", "ESEGUITA")):
                r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione",
                                 headers=chi_rsc, json={"stato_atteso": da, "nuovo_stato": a,
                                                        "motivo": "Accettazione del riversamento"})
                assert r.status_code in (200, 202), r.text
            await lavora(dip)
            dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}",
                                     headers=chi_admin)).json()
    return dettaglio, dip, impronte, contenuto


async def test_riversamento_ingresso(db_url, s3_ciclo) -> None:
    dettaglio, dip, impronte, contenuto = await _ingresso(db_url, s3_ciclo, altera=False)
    assert dettaglio["stato"] == "ESEGUITA", dettaglio
    verifica = dettaglio["dati"]["verifica"]
    assert verifica["schema_validato"] is False
    assert verifica["indice"]["identificativo"] == "IDC-ALTRO-77"
    assert verifica["firma"]["presente"] is True and verifica["firma"]["verificata"] is False
    assert {f["esito"] for f in verifica["file"]} == {"OK"}
    ricevuto = next(a for a in dettaglio["allegati"] if a["tipo"] == "PDD_RICEVUTO")
    assert ricevuto["sha256"] == hashlib.sha256(contenuto).hexdigest()
    (versamento,) = dip.versatore.versamenti.values()
    assert dettaglio["pdv_risultato_id"] == versamento["pdv_id"]
    documenti = {d["nome_file"]: d for d in versamento["indice"]["documenti"]}
    assert documenti["delibera.pdf"]["tipo"] == "PRINCIPALE"
    assert documenti["delibera.pdf"]["hash"]["impronta"] == impronte["documenti/delibera.pdf"]
    parametri = documenti["delibera.pdf"]["parametri"]
    assert parametri["riversamento_idc_origine"] == "IDC-ALTRO-77"
    assert parametri["riversamento_impronta_idc"] == verifica["indice"]["sha256"]
    assert parametri["riversamento_conservatore_origine"] == "Altro conservatore"
    assert parametri["identificativo_documento"] == "DEL-12"
    assert parametri["tipologia_documentale"] == "delibera"
    assert dettaglio["conteggi"] == {"VERSATO": 2}


async def test_riversamento_ingresso_impronta_alterata_respinto(db_url, s3_ciclo) -> None:
    dettaglio, dip, _, _ = await _ingresso(db_url, s3_ciclo, altera=True)
    assert dettaglio["stato"] == "RESPINTA"
    esiti = {f["nome"]: f["esito"] for f in dettaglio["dati"]["verifica"]["file"]}
    assert esiti["documenti/allegato.txt"] == "IMPRONTA_DIVERSA"
    assert dip.versatore.versamenti == {}
