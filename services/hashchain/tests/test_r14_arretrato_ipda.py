# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R14 (VD-C-165) — risigillo degli IPdA dei PdA già CONSERVATI prima di R13a (senza riga in
``firme_documenti``), su PostgreSQL vero e S3 (moto con Object Lock), con il fornitore finto.

  - senza fornitore qualificato il censimento non parte (si attiva col prestatore);
  - con il fornitore: l'IPdA del PdA storico entra fra i documenti da sigillare con
    l'impronta presa dal SUO manifest (letto alla versione registrata e verificato contro
    ``aip.hash_sha256``) e il giro di sigillo lo firma; busta nel WORM con l'impronta nella
    chiave;
  - limite per giro e cursore: due giri da uno; poi il passaggio è completo e non si rifà
    prima del ripasso;
  - idempotente: rieseguito (anche dopo un crash fra inserimento e cursore) non duplica
    righe né buste;
  - un manifest di impronta diversa NON si accoda (allarme), gli altri sì;
  - fencing: con il lease del registro perso non scrive nulla.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from app.config import settings
from app.registro import archivio, firme_documenti
from app.registro.metriche import metriche
from app.services.firma_blocchi import STATO_NON_FIRMATO

from libs.shared.db import get_async_session
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.supporto_registro import crea_pda
from tests.test_r13a_firme_documenti import ADESSO, IO, FirmaFinta, db, s3  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


class FirmaQualificata(FirmaFinta):
    def __init__(self, qualificato: bool = True, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self._q = qualificato

    async def qualificato(self) -> bool:
        return self._q


async def _pda_storico(url: str, s3, *, manifest_alterato: bool = False):  # type: ignore[no-untyped-def]
    """PdA CONSERVATO senza riga IPDA, con IPdA e manifest veri nello storage."""
    tenant, classe = await crea_tenant_e_classe(url)
    pda = await crea_pda(url, tenant, classe, stato="CONSERVATO", in_coda=False)
    bucket = settings.s3_bucket_aip
    ipda = f"<SInCRO>{pda.aip_id}</SInCRO>".encode()
    chiave_ipda = f"{tenant}/{pda.aip_id}/IndiceAIP_SInCRO.xml"
    s3.put_object(Bucket=bucket, Key=chiave_ipda, Body=ipda)
    manifest = json.dumps({"aip_id": str(pda.aip_id), "sincro_xml_key": chiave_ipda,
                           "sincro_hash_sha256": hashlib.sha256(ipda).hexdigest()}).encode()
    chiave_m = f"{tenant}/{pda.aip_id}/manifest.json"
    vid = s3.put_object(Bucket=bucket, Key=chiave_m, Body=manifest).get("VersionId")
    registrata = hashlib.sha256(manifest + (b"x" if manifest_alterato else b"")).hexdigest()
    await sql(url, "UPDATE public.aip SET s3_bucket = $2, s3_key = $3, s3_version_id = $4, "
                   "hash_sha256 = $5 WHERE id = $1", pda.aip_id, bucket, chiave_m, vid,
              registrata)
    return pda, ipda


async def _righe(url: str) -> list:
    return await sql(url, "SELECT aip_id, stato, impronta_sha256, busta_chiave FROM "
                          "public.firme_documenti WHERE tipo = 'IPDA' ORDER BY aip_id")


async def test_senza_fornitore_qualificato_non_parte(db, s3) -> None:  # noqa: F811
    await _pda_storico(db, s3)
    esito = await firme_documenti.censisci_arretrato_ipda(
        FirmaQualificata(False, stato=STATO_NON_FIRMATO), detentore=IO, adesso=ADESSO)
    assert esito == {"in_attesa": "nessun fornitore qualificato attivo"}
    assert await _righe(db) == []


async def test_arretrato_censito_a_lotti_sigillato_e_idempotente(db, s3) -> None:  # noqa: F811
    (p1, ipda1), (p2, ipda2) = [await _pda_storico(db, s3) for _ in range(2)]
    servizio = FirmaQualificata()
    e1 = await firme_documenti.censisci_arretrato_ipda(servizio, detentore=IO, adesso=ADESSO,
                                                        limite=1)
    assert e1 == {"inseriti": 1, "scartati": 0, "completato": False}
    e2 = await firme_documenti.censisci_arretrato_ipda(servizio, detentore=IO, adesso=ADESSO,
                                                        limite=1)
    assert e2["inseriti"] == 1
    e3 = await firme_documenti.censisci_arretrato_ipda(servizio, detentore=IO, adesso=ADESSO,
                                                        limite=1)
    assert e3 == {"inseriti": 0, "scartati": 0, "completato": True}
    assert metriche().firme_arretrato_completato._value.get() == 1
    # passaggio completo: non si rifà prima del ripasso
    assert await firme_documenti.censisci_arretrato_ipda(
        servizio, detentore=IO, adesso=ADESSO + timedelta(hours=1)) == {"completato": True}
    righe = await _righe(db)
    impronte = {r["aip_id"]: r["impronta_sha256"] for r in righe}
    assert impronte == {p1.aip_id: hashlib.sha256(ipda1).hexdigest(),
                        p2.aip_id: hashlib.sha256(ipda2).hexdigest()}
    # il giro di sigillo li firma
    esito = await firme_documenti.sigilla(servizio, detentore=IO, adesso=ADESSO)
    assert esito["firmati"] == 2 and sorted(servizio.firmati) == sorted([ipda1, ipda2])
    righe = await _righe(db)
    assert {r["stato"] for r in righe} == {"FIRMATO"}
    for r in righe:
        sha = hashlib.sha256(b"P7M" + (ipda1 if r["aip_id"] == p1.aip_id else ipda2)).hexdigest()
        assert r["busta_chiave"].endswith(f".{sha[:24]}.p7m")
    # ripasso dopo il termine (crash simulato: cursore azzerato): nessuna riga né busta nuova
    await sql(db, "UPDATE public.firme_arretrato_ipda SET completato_il = NULL, "
                  "ultimo_aip = NULL")
    assert (await firme_documenti.censisci_arretrato_ipda(
        servizio, detentore=IO, adesso=ADESSO))["inseriti"] == 0
    assert len(await _righe(db)) == 2
    assert (await firme_documenti.sigilla(servizio, detentore=IO, adesso=ADESSO))["firmati"] == 0


async def test_manifest_alterato_non_si_accoda(db, s3) -> None:  # noqa: F811
    buono, _ = await _pda_storico(db, s3)
    await _pda_storico(db, s3, manifest_alterato=True)
    prima = metriche().firme_arretrato_scartati._value.get()
    esito = await firme_documenti.censisci_arretrato_ipda(FirmaQualificata(), detentore=IO,
                                                           adesso=ADESSO)
    assert esito["inseriti"] == 1 and esito["scartati"] == 1
    assert metriche().firme_arretrato_scartati._value.get() == prima + 1
    assert [r["aip_id"] for r in await _righe(db)] == [buono.aip_id]


async def test_senza_lease_del_registro_non_scrive(db, s3) -> None:  # noqa: F811
    await _pda_storico(db, s3)
    async with get_async_session() as session:
        await archivio.rilascia_lease(session, IO)
        assert await archivio.acquisisci_lease(session, "altro-" + uuid.uuid4().hex[:6], ADESSO,
                                               ADESSO + timedelta(hours=1))
    with pytest.raises(archivio.LeasePerso):
        await firme_documenti.censisci_arretrato_ipda(FirmaQualificata(), detentore=IO,
                                                       adesso=ADESSO)
    assert await _righe(db) == []
    assert await sql(db, "SELECT 1 FROM public.firme_arretrato_ipda") == []


async def test_client_qualificato_solo_con_stato_dichiarato() -> None:
    import httpx
    from app.services.firma_blocchi import ClientServizioSignature

    def risposta(corpo, codice=200):  # type: ignore[no-untyped-def]
        return httpx.MockTransport(lambda r: httpx.Response(codice, json=corpo))

    def client(t):  # type: ignore[no-untyped-def]
        return ClientServizioSignature("http://firma.invalid", "token", transport=t)

    assert await client(risposta({"firma": {"qualificata": True}})).qualificato()
    assert not await client(risposta({"firma": {"qualificata": False}})).qualificato()
    assert not await client(risposta({"firma": {"qualificata": True}}, 503)).qualificato()
    assert not await ClientServizioSignature("", "token").qualificato()

    def guasto(r):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("giù")

    assert not await client(httpx.MockTransport(guasto)).qualificato()


async def test_versione_dell_ipda_con_l_impronta_del_manifest(db, s3) -> None:  # noqa: F811
    """Revisione R14 (6): se dopo la conservazione l'oggetto dell'IPdA ha una versione
    nuova (altri byte), la riga registra la versione con l'impronta del manifest, non la
    corrente; nessuna versione con l'impronta = PdA non accodato."""
    pda, ipda = await _pda_storico(db, s3)
    chiave = f"{pda.tenant}/{pda.aip_id}/IndiceAIP_SInCRO.xml"
    giusta = [v["VersionId"] for v in s3.list_object_versions(
        Bucket=settings.s3_bucket_aip, Prefix=chiave)["Versions"]][0]
    s3.put_object(Bucket=settings.s3_bucket_aip, Key=chiave, Body=b"<SInCRO>altro</SInCRO>")
    esito = await firme_documenti.censisci_arretrato_ipda(FirmaQualificata(), detentore=IO,
                                                           adesso=ADESSO)
    assert esito["inseriti"] == 1
    riga = (await sql(db, "SELECT origine_version_id FROM public.firme_documenti "
                          "WHERE aip_id = $1", pda.aip_id))[0]
    assert riga["origine_version_id"] == giusta
    assert (await firme_documenti.sigilla(FirmaQualificata(), detentore=IO,
                                          adesso=ADESSO))["firmati"] == 1


async def test_nessuna_versione_con_l_impronta_non_si_accoda(db, s3) -> None:  # noqa: F811
    pda, _ = await _pda_storico(db, s3)
    chiave = f"{pda.tenant}/{pda.aip_id}/IndiceAIP_SInCRO.xml"
    for v in s3.list_object_versions(Bucket=settings.s3_bucket_aip,
                                     Prefix=chiave)["Versions"]:
        s3.delete_object(Bucket=settings.s3_bucket_aip, Key=chiave, VersionId=v["VersionId"])
    s3.put_object(Bucket=settings.s3_bucket_aip, Key=chiave, Body=b"<SInCRO>sostituito</SInCRO>")
    esito = await firme_documenti.censisci_arretrato_ipda(FirmaQualificata(), detentore=IO,
                                                           adesso=ADESSO)
    assert esito == {"inseriti": 0, "scartati": 1, "completato": True}
