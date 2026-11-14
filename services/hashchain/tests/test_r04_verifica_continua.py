# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica continua dell'integrità (R04, r04-verifiche) su PostgreSQL vero
(migrato a 20261023_027) e S3 moto con Object Lock: PdA integro, oggetto alterato,
oggetto mancante, retention tolta o troppo breve, errori di lettura con attesa
crescente, due repliche senza doppie letture, lease scaduto, budget rispettato,
foglie K1, scarto, semina e riconciliazione, partizioni, API con i perimetri.

Moto non implementa ``GetObjectRetention`` (risponde 500): dove una versione
senza retention costringe a chiederla, il client di prova risponde come S3
(``NoSuchObjectLockConfiguration``); la lettura GET, le intestazioni di
retention e i contenuti sono quelli veri di moto.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.verifica_continua import repository
from app.verifica_continua.config import ImpostazioniVerifica
from app.verifica_continua.foglie import FogliePda
from app.verifica_continua.lavoratore import Lavoratore
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI

from libs.catena.foglie import ComponentePda, albero_pda
from libs.shared.errori import installa_gestori_errori
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.token_di_prova import intestazioni

pytestmark = [pytest.mark.integration, richiede_pg]


def _cfg(**altro: Any) -> ImpostazioniVerifica:
    base = {"letture_al_secondo": 0, "byte_al_secondo": 0, "lotto": 50,
            "prima_entro_giorni": 1, "attesa_inattivo_secondi": 0.1, "metriche_porta": 0,
            "soglia_errore_lettura": 3, "ritardi_errore_lettura_secondi": [60, 300, 1500],
            # R09 (D05): il campione dopo un incidente lo provano i test R09
            "incidente_campione": False}
    base.update(altro)
    return ImpostazioniVerifica(**base)


class Orologio:
    """Tempo dei test: parte da adesso (le partizioni del registro sono per mese corrente)."""

    def __init__(self) -> None:
        self.t = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.t

    def avanza(self, **kw: float) -> None:
        self.t += timedelta(**kw)


class ClientS3:
    """Client moto con conteggio delle letture, guasti programmabili e risposta di S3
    a GetObjectRetention per le versioni senza retention (moto risponde 500)."""

    def __init__(self, reale: Any) -> None:
        self.reale = reale
        self.letture: list[str] = []
        self.guasti: dict[str, int] = {}  # chiave → quante letture far fallire

    def get_object(self, **kw: Any) -> Any:
        self.letture.append(kw["Key"])
        if self.guasti.get(kw["Key"], 0) > 0:
            self.guasti[kw["Key"]] -= 1
            raise EndpointConnectionError(endpoint_url="http://s3-giù")
        return self.reale.get_object(**kw)

    def get_object_retention(self, **kw: Any) -> Any:
        raise ClientError({"Error": {"Code": "NoSuchObjectLockConfiguration"}},
                          "GetObjectRetention")


@pytest.fixture
def s3(s3_server):  # noqa: F811
    reale = client_s3(s3_server)
    bucket = f"vc-{uuid.uuid4().hex[:10]}"
    reale.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
    return reale, bucket


def _put(reale, bucket: str, chiave: str, dati: bytes, fino: datetime | None) -> None:
    extra = {"ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": fino} if fino else {}
    reale.put_object(Bucket=bucket, Key=chiave, Body=dati, **extra)


async def crea_pda(url: str, reale, bucket: str, tenant: uuid.UUID, classe: uuid.UUID, *,
                   adesso: datetime, n_file: int = 2, dimensione: int = 1000,
                   stato: str = "CONSERVATO", data_versamento: datetime | None = None,
                   ) -> dict[str, Any]:
    """PdA completo: file, IPdA, manifest e RdV in WORM (COMPLIANCE fino alla scadenza),
    righe pdv/aip/documenti come le scrivono aip-builder e hashchain."""
    pdv_id, aip_id = uuid.uuid4(), uuid.uuid4()
    scadenza = adesso + timedelta(days=3650)
    base = f"{tenant}/{aip_id}"
    documenti = []
    for i in range(n_file):
        dati = (f"file {i} del PdA {aip_id} ".encode() * (dimensione // 30 + 1))[:dimensione]
        chiave = f"{base}/files/{i:04d}-doc{i}.pdf"
        _put(reale, bucket, chiave, dati, scadenza)
        documenti.append({"id": uuid.uuid4(), "nome_file": f"doc{i}.pdf", "s3_key": chiave,
                          "hash_sha256": hashlib.sha256(dati).hexdigest(),
                          "dimensione": len(dati), "ordine": i, "dati": dati})
    ipda = f"<IndiceAIP id='{aip_id}'/>".encode()
    chiave_ipda = f"{base}/IndiceAIP_SInCRO.xml"
    _put(reale, bucket, chiave_ipda, ipda, scadenza)
    manifest = json.dumps({
        "aip_id": str(aip_id), "sincro_xml_key": chiave_ipda,
        "sincro_hash_sha256": hashlib.sha256(ipda).hexdigest(),
        "documenti": [{"id": f"d{d['ordine']}", "s3_key": d["s3_key"],
                       "hash_sha256": d["hash_sha256"]} for d in documenti],
    }, indent=2).encode()
    chiave_manifest = f"{base}/manifest.json"
    _put(reale, bucket, chiave_manifest, manifest, scadenza)
    rdv = f"<RicevutaVersamento>{aip_id}</RicevutaVersamento>".encode()
    chiave_rdv = f"{base}/RdV_Definitiva.xml"
    _put(reale, bucket, chiave_rdv, rdv, scadenza)
    rif = {"aip_id": str(aip_id), "s3_bucket": bucket, "s3_key": chiave_rdv,
           "impronta_sha256": hashlib.sha256(rdv).hexdigest()}
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "metadata_json) VALUES ($1, 'prova', 'CONSERVATO', $2, $3, $4, $5::jsonb)",
              pdv_id, classe, uuid.uuid4(), tenant, json.dumps({"rdv_definitiva": rif}))
    urn = f"urn:prova:{aip_id}"
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256, dimensione_bytes, sincro_xml_key, scadenza_conservazione, "
                   "data_versamento) VALUES ($1, $2, $3, $4, $5::public.aip_stato, $6, $7, $8, $9, "
                   "$10, $11, $12)",
              aip_id, pdv_id, tenant, urn, stato, chiave_manifest, bucket,
              hashlib.sha256(manifest).hexdigest(), len(manifest), chiave_ipda, scadenza,
              data_versamento or adesso)
    for d in documenti:
        await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                       "dimensione_bytes, hash_sha256, s3_key, ordine) VALUES ($1, $2, $3, "
                       "'PRINCIPALE', 'application/pdf', $4, $5, $6, $7)",
                  d["id"], aip_id, d["nome_file"], d["dimensione"], d["hash_sha256"],
                  d["s3_key"], d["ordine"])
    return {"aip_id": aip_id, "urn": urn, "tenant": tenant, "documenti": documenti,
            "ipda": ipda, "chiave_ipda": chiave_ipda, "chiave_manifest": chiave_manifest,
            "chiave_rdv": chiave_rdv, "scadenza": scadenza}


async def _registro(url: str, aip_id: uuid.UUID) -> list[dict[str, Any]]:
    righe = await sql(url, "SELECT * FROM public.verifiche_registro WHERE aip_id = $1 "
                           "ORDER BY id", aip_id)
    return [dict(r) for r in righe]


async def _programmazione(url: str, aip_id: uuid.UUID) -> dict[str, Any]:
    riga = dict((await sql(url, "SELECT * FROM public.verifiche_programmazione "
                               "WHERE aip_id = $1", aip_id))[0])
    if isinstance(riga["da_ritentare"], str):
        riga["da_ritentare"] = json.loads(riga["da_ritentare"])
    return riga


async def _eventi(url: str) -> list[dict[str, Any]]:
    righe = await sql(url, "SELECT payload FROM public.outbox_eventi "
                           "WHERE topic = 'integrita.anomalia' ORDER BY sequenza")
    return [json.loads(r["payload"]) for r in righe]


async def _prepara(url: str, s3, orologio: Orologio, n: int = 1, **kw: Any):
    reale, bucket = s3
    tenant, classe = await crea_tenant_e_classe(url)
    pda = [await crea_pda(url, reale, bucket, tenant, classe, adesso=orologio(), **kw)
           for _ in range(n)]
    return tenant, classe, pda


async def _tutto_in_scadenza(lav: Lavoratore, orologio: Orologio) -> None:
    """Semina e porta il tempo oltre la prima verifica (finestra di 1 giorno)."""
    await repository.semina(lav.cfg, orologio())
    orologio.avanza(days=1, seconds=1)


# ── esiti ────────────────────────────────────────────────────────────────────

async def test_pda_integro_tutti_ok_copertura_e_prossima_al_ciclo(db_url, s3):
    """Manifest, IPdA, due file e RdV riletti: OK con impronte e retention COMPLIANCE;
    prossima verifica dopo il ciclo; copertura spostata da «mai» a oggi."""
    orologio = Orologio()
    tenant, _, (pda,) = await _prepara(db_url, s3, orologio)
    lav = Lavoratore(_cfg(), client=ClientS3(s3[0]), detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    assert await lav.giro() == 1
    righe = await _registro(db_url, pda["aip_id"])
    assert sorted(r["componente"] for r in righe) == ["FILE", "FILE", "IPDA", "PDA", "RDV"]
    assert {r["esito"] for r in righe} == {"OK"}
    assert all(r["impronta_attesa"] == r["impronta_trovata"] for r in righe)
    assert all(r["retention_modalita"] == "COMPLIANCE" for r in righe)
    prog = await _programmazione(db_url, pda["aip_id"])
    assert prog["ultimo_esito"] == "OK" and prog["anomalia_dal"] is None
    assert prog["prossima_verifica"] == orologio() + timedelta(days=365)
    assert prog["lease_id"] is None and prog["verifiche_eseguite"] == 1
    cop = {r["giorno"]: r["oggetti"] for r in await sql(
        db_url, "SELECT giorno, oggetti FROM public.verifiche_copertura WHERE tenant_id = $1",
        tenant)}
    assert cop[repository.GIORNO_MAI] == 0 and cop[orologio().date()] == 1
    assert await _eventi(db_url) == []
    assert await lav.giro() == 0  # nulla in scadenza


async def test_oggetto_alterato_diverso_allarme_in_outbox(db_url, s3):
    """Una nuova versione con contenuto diverso sulla chiave di un file: DIVERSO,
    anomalia aperta, evento integrita.anomalia senza nomi di file."""
    orologio = Orologio()
    reale, bucket = s3
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    doc = pda["documenti"][1]
    _put(reale, bucket, doc["s3_key"], b"contenuto manomesso", pda["scadenza"])
    lav = Lavoratore(_cfg(), client=ClientS3(reale), detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    await lav.giro()
    righe = {(r["componente"], r["documento_id"]): r
             for r in await _registro(db_url, pda["aip_id"])}
    alterato = righe[("FILE", doc["id"])]
    assert alterato["esito"] == "DIVERSO"
    assert alterato["impronta_trovata"] == hashlib.sha256(b"contenuto manomesso").hexdigest()
    assert alterato["dimensione_trovata"] == len(b"contenuto manomesso")
    assert righe[("FILE", pda["documenti"][0]["id"])]["esito"] == "OK"
    prog = await _programmazione(db_url, pda["aip_id"])
    assert prog["ultimo_esito"] == "DIVERSO" and prog["anomalia_dal"] == orologio()
    (evento,) = await _eventi(db_url)
    assert evento["aip_id"] == str(pda["aip_id"]) and evento["esito"] == "DIVERSO"
    assert [o["documento_id"] for o in evento["oggetti"]] == [str(doc["id"])]
    assert "doc1.pdf" not in json.dumps(evento)


async def test_oggetto_mancante(db_url, s3):
    """Marcatore di cancellazione sulla chiave dell'IPdA (S3 lo consente anche sotto
    Object Lock): la GET non trova l'oggetto → MANCANTE."""
    orologio = Orologio()
    reale, bucket = s3
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    reale.delete_object(Bucket=bucket, Key=pda["chiave_ipda"])
    lav = Lavoratore(_cfg(), client=ClientS3(reale), detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    await lav.giro()
    ipda = [r for r in await _registro(db_url, pda["aip_id"]) if r["componente"] == "IPDA"][0]
    assert ipda["esito"] == "MANCANTE" and ipda["impronta_trovata"] is None
    assert (await _programmazione(db_url, pda["aip_id"]))["ultimo_esito"] == "MANCANTE"
    assert (await _eventi(db_url))[0]["esito"] == "MANCANTE"


async def test_retention_tolta_e_retention_troppo_breve(db_url, s3):
    """Stesso contenuto riscritto senza retention (versione nuova) → RETENTION_ASSENTE;
    retention COMPLIANCE di 2 giorni su un PdA da 10 anni → RETENTION_ASSENTE."""
    orologio = Orologio()
    reale, bucket = s3
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    d0, d1 = pda["documenti"]
    _put(reale, bucket, d0["s3_key"], d0["dati"], None)
    _put(reale, bucket, d1["s3_key"], d1["dati"], orologio() + timedelta(days=2))
    lav = Lavoratore(_cfg(), client=ClientS3(reale), detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    await lav.giro()
    righe = {r["documento_id"]: r for r in await _registro(db_url, pda["aip_id"])
             if r["componente"] == "FILE"}
    assert righe[d0["id"]]["esito"] == "RETENTION_ASSENTE"
    assert righe[d0["id"]]["retention_modalita"] is None
    assert righe[d0["id"]]["impronta_trovata"] == d0["hash_sha256"]  # contenuto integro
    assert righe[d1["id"]]["esito"] == "RETENTION_ASSENTE"
    assert "più breve" in righe[d1["id"]]["motivo"]
    assert (await _programmazione(db_url, pda["aip_id"]))["ultimo_esito"] == "RETENTION_ASSENTE"


async def test_errore_lettura_ritentato_con_attesa_crescente_poi_allarme(db_url, s3):
    """Storage giù per un file: ERRORE_LETTURA, si rilegge SOLO quel file a +60 s,
    +300 s; al terzo errore consecutivo allarme (errore ripetuto); quando la lettura
    riesce la verifica si conclude OK e l'anomalia si chiude."""
    orologio = Orologio()
    reale, _ = s3
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    client = ClientS3(reale)
    chiave = pda["documenti"][0]["s3_key"]
    client.guasti[chiave] = 3
    lav = Lavoratore(_cfg(), client=client, detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    await lav.giro()
    prog = await _programmazione(db_url, pda["aip_id"])
    assert prog["errori_lettura_consecutivi"] == 1
    assert prog["prossima_verifica"] == orologio() + timedelta(seconds=60)
    assert prog["da_ritentare"]["componenti"] == [f"FILE:{pda['documenti'][0]['id']}"]
    assert prog["ultima_verifica"] is None and await _eventi(db_url) == []

    orologio.avanza(seconds=59)
    assert await lav.giro() == 0  # non prima dell'attesa
    orologio.avanza(seconds=2)
    client.letture.clear()
    await lav.giro()
    assert client.letture == [chiave]  # solo il file non letto
    prog = await _programmazione(db_url, pda["aip_id"])
    assert prog["errori_lettura_consecutivi"] == 2
    assert prog["prossima_verifica"] == orologio() + timedelta(seconds=300)

    orologio.avanza(seconds=301)
    await lav.giro()
    prog = await _programmazione(db_url, pda["aip_id"])
    assert prog["errori_lettura_consecutivi"] == 3 and prog["anomalia_dal"] is not None
    (evento,) = await _eventi(db_url)
    assert evento["errore_lettura_ripetuto"] is True and evento["esito"] == "ERRORE_LETTURA"

    orologio.avanza(seconds=1501)
    await lav.giro()
    prog = await _programmazione(db_url, pda["aip_id"])
    assert prog["ultimo_esito"] == "OK" and prog["anomalia_dal"] is None
    assert prog["errori_lettura_consecutivi"] == 0 and prog["da_ritentare"] is None
    tentativi = [r["tentativo"] for r in await _registro(db_url, pda["aip_id"])
                 if r["documento_id"] == pda["documenti"][0]["id"]]
    assert tentativi == [1, 2, 3, 4]


# ── repliche, lease, budget ──────────────────────────────────────────────────

async def test_due_repliche_senza_doppie_letture(db_url, s3):
    """Due lavoratori in parallelo su 30 PdA: ogni oggetto letto una sola volta in
    tutto, ogni PdA registrato una volta, entrambe le repliche lavorano."""
    orologio = Orologio()
    _, _, pda = await _prepara(db_url, s3, orologio, n=30, n_file=1)
    c1, c2 = ClientS3(s3[0]), ClientS3(s3[0])
    cfg = _cfg(lotto=4, concorrenza=2)
    r1 = Lavoratore(cfg, client=c1, detentore="replica-1", orologio=orologio)
    r2 = Lavoratore(cfg, client=c2, detentore="replica-2", orologio=orologio)
    await _tutto_in_scadenza(r1, orologio)

    async def finché_c_è(lav: Lavoratore) -> None:
        while await lav.giro():
            await asyncio.sleep(0)

    await asyncio.gather(finché_c_è(r1), finché_c_è(r2))
    letture = c1.letture + c2.letture
    assert len(letture) == len(set(letture)) == 30 * 4  # manifest, IPdA, file, RdV
    assert c1.letture and c2.letture
    righe = await sql(db_url, "SELECT aip_id, count(*) AS n, count(DISTINCT esecutore) AS e "
                              "FROM public.verifiche_registro GROUP BY aip_id")
    assert len(righe) == 30 and {(r["n"], r["e"]) for r in righe} == {(4, 1)}


async def test_lease_scaduto_la_replica_lenta_non_registra(db_url, s3):
    """La replica A prende il lotto e si ferma; scaduto il lease la B lo riprende e
    registra; l'esito tardivo di A viene scartato (recinto sul lease_id)."""
    from app.verifica_continua.verificatore import verifica_pda

    orologio = Orologio()
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    cfg = _cfg(lease_secondi=60)
    a = Lavoratore(cfg, client=ClientS3(s3[0]), detentore="A", orologio=orologio)
    b = Lavoratore(cfg, client=ClientS3(s3[0]), detentore="B", orologio=orologio)
    await _tutto_in_scadenza(a, orologio)
    (presa_a,) = await repository.prendi_lotto(cfg, orologio(), "A")
    assert await b.giro() == 0  # in lease
    orologio.avanza(seconds=61)
    assert await b.giro() == 1
    esito_a = await verifica_pda(presa_a, client=a.client, budget_letture=a.budget_letture,
                                 budget_byte=a.budget_byte, cfg=cfg, adesso=orologio())
    conclusione = await repository.registra_esito(esito_a, orologio(), cfg, "A")
    assert conclusione.registrato is False
    assert {r["esecutore"] for r in await _registro(db_url, pda["aip_id"])} == {"B"}


async def test_budget_di_byte_e_letture_rispettati(db_url, s3):
    """3 PdA da 2 file di 100 KB: con 200 KB/s il giro dura almeno (byte - scorta)/tasso;
    con 4 letture/s le 12 letture durano almeno (12 - 4)/4 s."""
    orologio = Orologio()
    await _prepara(db_url, s3, orologio, n=3, dimensione=100_000)
    cfg = _cfg(byte_al_secondo=200_000)
    lav = Lavoratore(cfg, client=ClientS3(s3[0]), detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    inizio = time.monotonic()
    assert await lav.giro() == 3
    durata = time.monotonic() - inizio
    letti = int((await sql(db_url, "SELECT sum(dimensione_trovata) AS s "
                                   "FROM public.verifiche_registro"))[0]["s"])
    assert letti > 600_000
    assert durata >= (letti - 200_000) / 200_000 * 0.95
    assert lav.budget_byte.atteso_secondi > 1.5

    orologio2 = Orologio()
    orologio2.t = orologio()
    await _prepara(db_url, s3, orologio2, n=3, dimensione=100)
    cfg2 = _cfg(letture_al_secondo=4)
    lav2 = Lavoratore(cfg2, client=ClientS3(s3[0]), detentore="r2", orologio=orologio2)
    await _tutto_in_scadenza(lav2, orologio2)
    inizio = time.monotonic()
    assert await lav2.giro() == 3
    assert time.monotonic() - inizio >= (12 - 4) / 4 * 0.95


# ── foglie K1 ────────────────────────────────────────────────────────────────

class FonteDiProva:
    nome = "PROVA"

    def __init__(self, foglie: dict[uuid.UUID, FogliePda]) -> None:
        self._foglie = foglie

    async def foglie(self, aip_ids):
        return {a: self._foglie[a] for a in aip_ids if a in self._foglie}


def _foglie_k1(pda: dict[str, Any]) -> FogliePda:
    albero = albero_pda(pda["urn"], [
        ComponentePda(doc=f"d{d['ordine']}", file=d["nome_file"], sha256=d["hash_sha256"],
                      metadati={}, ordine=d["ordine"]) for d in pda["documenti"]],
        hashlib.sha256(pda["ipda"]).hexdigest())
    return FogliePda(albero.foglie, albero.radice_hex)


async def test_foglie_k1_confrontate_e_manomissione_coerente_scoperta(db_url, s3):
    """Con le foglie: foglia attesa = ricalcolata per ogni file e per l'IPdA. Un file
    sostituito INSIEME alla sua impronta nel database (DB e storage coerenti) passa il
    confronto con il database ma non quello con la foglia → DIVERSO."""
    orologio = Orologio()
    reale, bucket = s3
    _, _, (buono, manomesso) = await _prepara(db_url, s3, orologio, n=2)
    fonte = FonteDiProva({p["aip_id"]: _foglie_k1(p) for p in (buono, manomesso)})
    nuovo = b"documento sostituito"
    doc = manomesso["documenti"][0]
    _put(reale, bucket, doc["s3_key"], nuovo, manomesso["scadenza"])
    await sql(db_url, "UPDATE public.documenti SET hash_sha256 = $1, dimensione_bytes = $2 "
                      "WHERE id = $3", hashlib.sha256(nuovo).hexdigest(), len(nuovo), doc["id"])
    lav = Lavoratore(_cfg(), client=ClientS3(reale), detentore="r1", orologio=orologio,
                     fonte_foglie=fonte)
    await _tutto_in_scadenza(lav, orologio)
    await lav.giro()
    righe = await _registro(db_url, buono["aip_id"])
    confrontate = [r for r in righe if r["componente"] in ("FILE", "IPDA")]
    assert len(confrontate) == 3 and all(
        r["esito"] == "OK" and r["foglia_attesa"] == r["foglia_trovata"] for r in confrontate)
    alterato = [r for r in await _registro(db_url, manomesso["aip_id"])
                if r["documento_id"] == doc["id"]][0]
    assert alterato["impronta_attesa"] == alterato["impronta_trovata"]  # DB riscritto
    assert alterato["esito"] == "DIVERSO"
    assert alterato["foglia_attesa"] != alterato["foglia_trovata"]
    # Il manifest integro dice un'altra impronta: segnalato anche questo
    assert "manifest" in alterato["motivo"]


# ── semina, riconciliazione, scarto, partizioni ─────────────────────────────

async def test_semina_idempotente_e_riconciliazione_dei_ritardatari(db_url, s3):
    """La semina porta i PdA una volta sola; un PdA scritto dopo con una data di
    versamento molto più vecchia (oltre la sovrapposizione) lo trova la riconciliazione."""
    orologio = Orologio()
    reale, bucket = s3
    tenant, classe, pda = await _prepara(db_url, s3, orologio, n=3)
    cfg = _cfg()
    assert await repository.semina(cfg, orologio()) == 3
    assert await repository.semina(cfg, orologio()) == 0
    tardivo = await crea_pda(db_url, reale, bucket, tenant, classe, adesso=orologio(),
                             data_versamento=orologio() - timedelta(days=3))
    assert await repository.semina(cfg, orologio()) == 0
    assert await repository.riconcilia(cfg, orologio()) == 1
    assert (await _programmazione(db_url, tardivo["aip_id"]))["tenant_id"] == tenant
    assert await repository.riconcilia(cfg, orologio()) == 0  # passaggio concluso, attesa
    cop = await sql(db_url, "SELECT oggetti FROM public.verifiche_copertura WHERE tenant_id = $1 "
                            "AND giorno = $2", tenant, repository.GIORNO_MAI)
    assert cop[0]["oggetti"] == 4


async def test_pda_in_scarto_esce_dalla_rotazione(db_url, s3):
    orologio = Orologio()
    tenant, _, (pda,) = await _prepara(db_url, s3, orologio, stato="ELIMINAZIONE")
    lav = Lavoratore(_cfg(), client=ClientS3(s3[0]), detentore="r1", orologio=orologio)
    await repository.riconcilia(lav.cfg, orologio())  # la semina esclude ELIMINAZIONE
    await sql(db_url, "INSERT INTO public.verifiche_programmazione (aip_id, tenant_id, "
                      "prossima_verifica) VALUES ($1, $2, now() - interval '1 day')",
              pda["aip_id"], tenant)
    assert await lav.giro() == 1
    assert await sql(db_url, "SELECT 1 FROM public.verifiche_programmazione WHERE aip_id = $1",
                     pda["aip_id"]) == []
    assert await _registro(db_url, pda["aip_id"]) == []


async def test_registro_append_only_e_partizioni_future(db_url, s3):
    """UPDATE/DELETE sul registro vietati; la manutenzione crea i mesi futuri."""
    import asyncpg

    orologio = Orologio()
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    lav = Lavoratore(_cfg(partizioni_mesi_avanti=6), client=ClientS3(s3[0]),
                     detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    await lav.giro()
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await sql(db_url, "UPDATE public.verifiche_registro SET esito = 'OK'")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await sql(db_url, "DELETE FROM public.verifiche_registro")
    await lav.manutenzione(forza=True)
    partizioni = await sql(db_url, "SELECT c.relname FROM pg_inherits i "
                                   "JOIN pg_class c ON c.oid = i.inhrelid WHERE i.inhparent = "
                                   "'public.verifiche_registro'::regclass")
    assert len(partizioni) >= 7
    stato = await repository.stato_lavoratore()
    assert stato["fonte_foglie"] == "NON_DISPONIBILE" and stato["detentore"] == "r1"


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def api():
    from app.verifica_continua.router import router

    app = FastAPI()
    app.include_router(router)
    installa_gestori_errori(app)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_api_senza_token_401(db_url, api):
    async with api as c:
        for metodo, percorso in (("POST", "/api/v1/hashchain/verifiche"),
                                 ("GET", "/api/v1/hashchain/verifiche/registro"),
                                 ("GET", "/api/v1/hashchain/verifiche/riepilogo"),
                                 ("GET", f"/api/v1/hashchain/verifiche/{uuid.uuid4()}")):
            r = await c.request(metodo, percorso, json={})
            assert r.status_code == 401, percorso


async def test_api_richiesta_pda_perimetri_e_completamento(db_url, s3, api):
    """ADMIN_TENANT chiede la verifica di un proprio PdA (per URN): 202; un PdA di un
    altro ente → 404; VIEWER → 403; il lavoratore la serve prima della rotazione e la
    richiesta diventa COMPLETATA; il registro dell'ente non mostra gli altri enti."""
    orologio = Orologio()
    tenant, _, (mio,) = await _prepara(db_url, s3, orologio)
    altro_ente, _, (altrui,) = await _prepara(db_url, s3, orologio)
    admin = intestazioni("ADMIN_TENANT", tenant_id=str(tenant))
    async with api as c:
        r = await c.post("/api/v1/hashchain/verifiche", json={"pda": [mio["urn"]]}, headers=admin)
        assert r.status_code == 202, r.text
        stato = r.json()
        assert stato["stato"] == "IN_CORSO" and stato["oggetti_totali"] == 1
        r = await c.post("/api/v1/hashchain/verifiche", json={"pda": [str(altrui["aip_id"])]},
                         headers=admin)
        assert r.status_code == 404 and r.json()["codice"] == "INT-404"
        r = await c.post("/api/v1/hashchain/verifiche", json={"pda": [mio["urn"]]},
                         headers=intestazioni("VIEWER", tenant_id=str(tenant)))
        assert r.status_code == 403 and r.json()["codice"] == "INT-403"
        r = await c.post("/api/v1/hashchain/verifiche", json={"pda": [mio["urn"]],
                                                              "ente": str(tenant)}, headers=admin)
        assert r.status_code == 422 and r.json()["codice"] == "INT-422"

        # Il PdA non era ancora seminato: la richiesta lo ha messo in programmazione, adesso
        lav = Lavoratore(_cfg(), client=ClientS3(s3[0]), detentore="r1")
        assert await lav.giro() == 1
        r = await c.get(f"/api/v1/hashchain/verifiche/{stato['richiesta_id']}", headers=admin)
        assert r.json()["stato"] == "COMPLETATA" and r.json()["oggetti_verificati"] == 1
        r = await c.get(f"/api/v1/hashchain/verifiche/{stato['richiesta_id']}",
                        headers=intestazioni("ADMIN_TENANT", tenant_id=str(altro_ente)))
        assert r.status_code == 404

        r = await c.get("/api/v1/hashchain/verifiche/registro", headers=admin)
        voci = r.json()["voci"]
        assert len(voci) == 5 and {v["ente"] for v in voci} == {str(tenant)}
        assert {v["pda"] for v in voci} == {mio["urn"]}
        r = await c.get("/api/v1/hashchain/verifiche/registro",
                        params={"ente": str(altro_ente)}, headers=admin)
        assert r.status_code == 404
        r = await c.get("/api/v1/hashchain/verifiche/registro",
                        params={"pda": str(altrui["aip_id"])}, headers=admin)
        assert r.status_code == 404


async def test_api_verifica_ente_preparazione_409_e_riepilogo(db_url, s3, api):
    """RSC chiede la verifica di tutto un ente: la preparazione marca i PdA a lotti,
    una seconda richiesta → 409; a fine lavoro COMPLETATA con le anomalie contate.
    Riepilogo: copertura dell'ente e anomalie aperte; registro a cursore."""
    orologio = Orologio()
    reale, bucket = s3
    tenant, _, pda = await _prepara(db_url, s3, orologio, n=5, n_file=1)
    _put(reale, bucket, pda[0]["documenti"][0]["s3_key"], b"alterato", pda[0]["scadenza"])
    cfg = _cfg(preparazione_lotto=2)
    await repository.semina(cfg, orologio())  # prime verifiche nel futuro (entro 1 giorno)
    rsc = intestazioni("RSC_CONSERVATORE")
    async with api as c:
        r = await c.post("/api/v1/hashchain/verifiche", json={"ente": str(tenant)}, headers=rsc)
        assert r.status_code == 202 and r.json()["stato"] == "IN_PREPARAZIONE"
        rid = r.json()["richiesta_id"]
        r2 = await c.post("/api/v1/hashchain/verifiche", json={"ente": str(tenant)}, headers=rsc)
        assert r2.status_code == 409 and r2.json()["dettagli"]["richiesta_id"] == rid
        r = await c.post("/api/v1/hashchain/verifiche", json={"ente": str(uuid.uuid4())},
                         headers=rsc)
        assert r.status_code == 404

        assert await repository.prepara_richieste(cfg, orologio()) == 5
        lav = Lavoratore(cfg, client=ClientS3(reale), detentore="r1")
        while await lav.giro():
            pass
        stato = (await c.get(f"/api/v1/hashchain/verifiche/{rid}", headers=rsc)).json()
        assert (stato["stato"], stato["oggetti_totali"], stato["oggetti_verificati"],
                stato["anomalie"]) == ("COMPLETATA", 5, 5, 1)

        admin = intestazioni("ADMIN_TENANT", tenant_id=str(tenant))
        rie = (await c.get("/api/v1/hashchain/verifiche/riepilogo", headers=admin)).json()
        assert rie["pda_programmati"] == 5 and rie["pda_verificati_nella_finestra"] == 5
        assert rie["copertura_percentuale"] == 100.0 and rie["pda_mai_verificati"] == 0
        assert rie["anomalie_aperte"]["numero"] == 1
        assert rie["anomalie_aperte"]["elenco"][0]["pda"] == pda[0]["urn"]
        assert rie["ritardo_rotazione_secondi"] is None  # solo per il conservatore

        await lav.manutenzione(forza=True)  # riepilogo globale
        glob = (await c.get("/api/v1/hashchain/verifiche/riepilogo", headers=rsc)).json()
        assert glob["pda_programmati"] == 5 and glob["copertura_calcolata_il"] is not None
        assert glob["ritardo_rotazione_secondi"] is not None
        assert glob["lavoratore"]["confronto_foglia"] == "NON_DISPONIBILE"

        pagine, cursore = [], None
        while True:
            params = {"limite": 4, "esito": "OK"} | ({"cursore": cursore} if cursore else {})
            r = (await c.get("/api/v1/hashchain/verifiche/registro", params=params,
                             headers=admin)).json()
            pagine.append(r["voci"])
            cursore = r["cursore_successivo"]
            if not cursore:
                break
        tutte = [v for p in pagine for v in p]
        assert len(tutte) == 5 * 4 - 1 and all(v["esito"] == "OK" for v in tutte)
        anomalie = (await c.get("/api/v1/hashchain/verifiche/registro",
                                params={"esito": "ANOMALIE"}, headers=rsc)).json()["voci"]
        assert [v["esito"] for v in anomalie] == ["DIVERSO"]
        r = await c.get("/api/v1/hashchain/verifiche/registro", params={"cursore": "@@"},
                        headers=admin)
        assert r.status_code == 400 and r.json()["codice"] == "INT-400"


async def test_fonte_foglie_catena_legge_catena_pda_e_radice_del_registro(db_url, s3):
    """FonteFoglieCatena (migrazione 025): foglie da catena_pda, radice ancorata dalla
    foglia del registro globale. Foglie del PdA che non ricompongono la radice del
    registro → DIVERSO sul manifest; senza riga in catena_pda nessun confronto."""
    from app.verifica_continua.foglie import FonteFoglieCatena

    from libs.catena.foglie import dati_foglia_registro
    from libs.catena.rfc6962 import hash_foglia

    orologio = Orologio()
    tenant, _, (buono, sospetto, senza) = await _prepara(db_url, s3, orologio, n=3)
    for indice, pda in enumerate((buono, sospetto)):
        foglie = _foglie_k1(pda)
        await sql(db_url, "INSERT INTO public.catena_pda (aip_id, tenant_id, urn, radice, "
                          "num_foglie, foglie) VALUES ($1, $2, $3, $4, $5, CAST($6 AS jsonb))",
                  pda["aip_id"], tenant, pda["urn"], foglie.radice_pda, len(foglie.foglie),
                  json.dumps(list(foglie.foglie)))
        radice_registro = foglie.radice_pda if pda is buono else "ab" * 32
        dati = dati_foglia_registro(pda["urn"], radice_registro, str(tenant), orologio())
        await sql(db_url, "INSERT INTO public.catena_registro (indice, aip_id, tenant_id, tipo, "
                          "dati, hash, conservato_il) VALUES ($1, $2, $3, 'PDA', $4, $5, $6)",
                  indice, pda["aip_id"], tenant, dati.decode(), hash_foglia(dati), orologio())
    fonte = FonteFoglieCatena()
    lette = await fonte.foglie([buono["aip_id"], sospetto["aip_id"], senza["aip_id"]])
    assert set(lette) == {buono["aip_id"], sospetto["aip_id"]}
    lav = Lavoratore(_cfg(), client=ClientS3(s3[0]), detentore="r1", orologio=orologio,
                     fonte_foglie=fonte)
    await _tutto_in_scadenza(lav, orologio)
    assert await lav.giro() == 3
    per_pda = {p["aip_id"]: {r["componente"]: r for r in await _registro(db_url, p["aip_id"])}
               for p in (buono, sospetto, senza)}
    assert {r["esito"] for r in per_pda[buono["aip_id"]].values()} == {"OK"}
    assert per_pda[buono["aip_id"]]["IPDA"]["foglia_attesa"] is not None
    assert per_pda[sospetto["aip_id"]]["PDA"]["esito"] == "DIVERSO"
    assert "radice" in per_pda[sospetto["aip_id"]]["PDA"]["motivo"]
    assert per_pda[senza["aip_id"]]["IPDA"]["foglia_attesa"] is None
    assert {r["esito"] for r in per_pda[senza["aip_id"]].values()} == {"OK"}
