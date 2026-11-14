# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Integrazione su PostgreSQL vero (migrato) e S3 (moto): sigillo, firma,
RdV definitiva, CONSERVATO, verifica e append-only.
VD-C-012, VD-C-018, VD-C-019, VD-C-020.
"""
from __future__ import annotations

import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import asyncpg
import pytest
from app.config import settings
from app.services import block_sealer
from app.services.block_sealer import (
    completa_conservazione,
    firma_blocco,
    sigilla_blocco,
    verify_chain_integrity,
)
from app.services.firma_blocchi import (
    MOTIVO_NON_QUALIFICATO,
    STATO_NON_FIRMATO,
    EsitoFirmaBlocco,
)
from app.services.merkle import verify_merkle_proof

from libs.shared.storage import worm
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]

ORA = datetime(2026, 10, 20, 10, 30, tzinfo=UTC)


class _FirmaNonQualificata:
    def __init__(self):
        self.chiamate: list[bytes] = []

    async def firma(self, contenuto: bytes) -> EsitoFirmaBlocco:
        self.chiamate.append(contenuto)
        return EsitoFirmaBlocco(STATO_NON_FIRMATO, MOTIVO_NON_QUALIFICATO)


@pytest.fixture
def s3(s3_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    block_sealer._client.cache_clear()
    yield client_s3(s3_server)
    block_sealer._client.cache_clear()


async def _pda(url: str, tenant: uuid.UUID, classe: uuid.UUID, *, stato_pdv: str = "IN_BUILD",
               contenuto: bytes = b"%PDF-1.4 x") -> tuple[str, str]:
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'prova', $2, $3, $4, $5)", pdv, stato_pdv, classe, uuid.uuid4(),
                   tenant)
    impronta = hashlib.sha256(contenuto + aip.bytes).hexdigest()
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "hash_sha256) VALUES ($1, $2, $3, $4, 'SU_STORAGE', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:test:{aip}", impronta)
    await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                   "dimensione_bytes, hash_sha256, hash_sha512, s3_key, ordine) "
                   "VALUES ($1, $2, 'doc.pdf', 'PRINCIPALE', 'application/pdf', $3, $4, $5, 'k', "
                   "0)",
              uuid.uuid4(), aip, len(contenuto), hashlib.sha256(contenuto).hexdigest(),
              hashlib.sha512(contenuto).hexdigest())
    return str(pdv), str(aip)


async def test_percorso_completo_fino_a_conservato(db_url, s3):
    """Sigillo → firma (non qualificata, esplicita) → RdV su S3 → AIP e PdV CONSERVATO."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv1, aip1 = await _pda(db_url, tenant, classe)
    pdv2, aip2 = await _pda(db_url, tenant, classe)

    blocco = await sigilla_blocco([aip2, aip1], ora=ORA)
    assert blocco is not None and blocco["numero_blocco"] == 1
    assert blocco["aip_ids"] == sorted([aip1, aip2])  # ordine totale documentato

    servizio = _FirmaNonQualificata()
    assert await firma_blocco(blocco["id"], servizio) == STATO_NON_FIRMATO
    assert servizio.chiamate == [bytes.fromhex(blocco["hash_blocco"])]

    esito = await completa_conservazione(emessa_il=ORA, blocco_id=blocco["id"])
    assert sorted(esito.completati) == sorted([aip1, aip2]) and esito.falliti == {}

    righe = await sql(
        db_url, "SELECT stato::text AS stato, metadata_json FROM public.pdv WHERE id = $1",
                      uuid.UUID(pdv1))
    assert righe[0]["stato"] == "CONSERVATO"
    rif = json.loads(righe[0]["metadata_json"])["rdv_definitiva"]
    assert rif["stato_firma_blocco"] == STATO_NON_FIRMATO
    oggetto = s3.get_object(Bucket=rif["s3_bucket"], Key=rif["s3_key"])["Body"].read()
    assert hashlib.sha256(oggetto).hexdigest() == rif["impronta_sha256"]

    radice = ET.fromstring(oggetto)
    assert radice.find("FirmaBlocco/Motivo").text == MOTIVO_NON_QUALIFICATO
    prova = [(p.text, p.get("lato")) for p in radice.find("ProvaInclusione")]
    impronta_aip = radice.find("HashAIP").text
    assert verify_merkle_proof(impronta_aip, prova, blocco["merkle_root"])

    stati_aip = await sql(db_url, "SELECT stato::text AS s FROM public.aip WHERE pdv_id = $1",
                          uuid.UUID(pdv2))
    assert stati_aip[0]["s"] == "CONSERVATO"
    integra, messaggio = await verify_chain_integrity()
    assert integra, messaggio


async def test_sigilla_blocco_riconsegna_idempotente(db_url, s3):
    """Lo stesso AIP riconsegnato non entra in un secondo blocco."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    _, aip = await _pda(db_url, tenant, classe)
    assert await sigilla_blocco([aip], ora=ORA) is not None
    assert await sigilla_blocco([aip], ora=ORA) is None
    blocchi = await sql(db_url, "SELECT count(*) AS n FROM public.hashchain_blocks")
    assert blocchi[0]["n"] == 2  # genesi + 1


async def test_sigilla_blocco_impronta_dal_db_non_dal_messaggio(db_url, s3):
    """La foglia è l'impronta registrata dall'aip-builder, non un valore esterno."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    _, aip = await _pda(db_url, tenant, classe)
    blocco = await sigilla_blocco([aip], ora=ORA)
    riga = await sql(db_url, "SELECT hash_sha256 FROM public.aip WHERE id = $1", uuid.UUID(aip))
    foglia = hashlib.sha256(b"\x00" + bytes.fromhex(riga[0]["hash_sha256"])).hexdigest()
    assert blocco["merkle_root"] == foglia


async def test_completa_conservazione_errore_non_ignorato(db_url, s3):
    """PdV in stato inatteso: l'AIP resta IN_CATENA e l'errore è restituito, non inghiottito."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    _, aip = await _pda(db_url, tenant, classe, stato_pdv="ERRORE")
    blocco = await sigilla_blocco([aip], ora=ORA)
    await firma_blocco(blocco["id"], _FirmaNonQualificata())
    esito = await completa_conservazione(emessa_il=ORA)
    assert aip in esito.falliti and "stato inatteso" in esito.falliti[aip]
    stato = await sql(
        db_url, "SELECT stato::text AS s FROM public.aip WHERE id = $1", uuid.UUID(aip))
    assert stato[0]["s"] == "IN_CATENA"


async def test_completa_conservazione_attende_la_firma(db_url, s3):
    """Blocco ancora DA_FIRMARE (servizio giù): nessun RdV, nessun CONSERVATO."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    _, aip = await _pda(db_url, tenant, classe)
    await sigilla_blocco([aip], ora=ORA)
    esito = await completa_conservazione(emessa_il=ORA)
    assert esito.completati == [] and esito.falliti == {}


async def test_hashchain_blocks_append_only(db_url, s3):
    """Il trigger impedisce di riscrivere un blocco o la sua firma registrata."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    _, aip = await _pda(db_url, tenant, classe)
    blocco = await sigilla_blocco([aip], ora=ORA)
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await sql(db_url, "UPDATE public.hashchain_blocks SET merkle_root = 'x' WHERE id = $1",
                  uuid.UUID(blocco["id"]))
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await sql(
            db_url, "DELETE FROM public.hashchain_blocks WHERE id = $1", uuid.UUID(blocco["id"]))
    await firma_blocco(blocco["id"], _FirmaNonQualificata())
    with pytest.raises(asyncpg.RaiseError, match="già registrata"):
        await sql(
            db_url, "UPDATE public.hashchain_blocks SET stato_firma = 'FIRMATO' WHERE id = $1",
                  uuid.UUID(blocco["id"]))


async def test_verify_chain_integrity_rileva_impronta_aip_alterata(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    _, aip = await _pda(db_url, tenant, classe)
    await sigilla_blocco([aip], ora=ORA)
    assert (await verify_chain_integrity())[0]
    await sql(
        db_url, "UPDATE public.aip SET hash_sha256 = $2 WHERE id = $1", uuid.UUID(aip), "0" * 64)
    integra, messaggio = await verify_chain_integrity()
    assert not integra and "Merkle" in messaggio


async def test_rdv_scritto_in_worm_con_retention_del_pda(db_url, s3):
    """VD-C-011/§10.2: RdV con Object Lock COMPLIANCE e retention = classe + data del PdA;
    nessuna sovrascrittura possibile, nessuna cancellazione della versione."""
    from botocore.exceptions import ClientError

    tenant, classe = await crea_tenant_e_classe(db_url)
    await sql(db_url, "UPDATE public.classi_documentali SET anni_conservazione = 7, "
                      "conservazione_permanente = false WHERE id = $1", classe)
    pdv, aip = await _pda(db_url, tenant, classe)
    riferimento = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    await sql(db_url, "UPDATE public.aip SET data_versamento = $2 WHERE id = $1",
              uuid.UUID(aip), riferimento)
    blocco = await sigilla_blocco([aip], ora=ORA)
    await firma_blocco(blocco["id"], _FirmaNonQualificata())
    esito = await completa_conservazione(emessa_il=ORA)
    assert esito.completati == [aip], esito.falliti

    meta = await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1", uuid.UUID(pdv))
    rif = json.loads(meta[0]["metadata_json"])["rdv_definitiva"]
    testa = s3.head_object(Bucket=rif["s3_bucket"], Key=rif["s3_key"])
    assert testa["ObjectLockMode"] == "COMPLIANCE"
    assert testa["ObjectLockRetainUntilDate"] == datetime(2033, 3, 1, 9, 0, tzinfo=UTC)
    # Revisione 2 di R05 (S4): senza legal hold l'intestazione non si invia (S3
    # la omette nella lettura); con il legal hold la si invia ON (test seguente)
    assert testa.get("ObjectLockLegalHoldStatus", "OFF") == "OFF"
    assert rif["s3_version_id"] == testa["VersionId"]
    lock = s3.get_object_lock_configuration(Bucket=rif["s3_bucket"])
    assert lock["ObjectLockConfiguration"]["ObjectLockEnabled"] == "Enabled"
    with pytest.raises(ClientError):
        s3.delete_object(Bucket=rif["s3_bucket"], Key=rif["s3_key"], VersionId=testa["VersionId"])


async def test_rdv_classe_permanente_orizzonte_dal_parametro(db_url, s3, monkeypatch):
    """R09 (D04, VD-C-070): classe permanente senza orizzonte d'ambiente: vale il
    parametro RETENTION_PERMANENTE_ANNI (predefinito 30), legal hold attivo."""
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", None)
    tenant, classe = await crea_tenant_e_classe(db_url)
    await sql(db_url, "UPDATE public.classi_documentali SET conservazione_permanente = true "
                      "WHERE id = $1", classe)
    _, aip = await _pda(db_url, tenant, classe)
    blocco = await sigilla_blocco([aip], ora=ORA)
    await firma_blocco(blocco["id"], _FirmaNonQualificata())
    esito = await completa_conservazione(emessa_il=ORA)
    assert esito.completati == [aip], esito.falliti
    chiave = f"{tenant}/{aip}/{block_sealer.NOME_RDV}"
    testa = s3.head_object(Bucket=settings.s3_bucket_aip, Key=chiave)
    assert testa["ObjectLockLegalHoldStatus"] == "ON"
    assert testa["ObjectLockRetainUntilDate"].year - 30 in range(2000, 2100)


async def test_rdv_ripresa_dopo_scrittura_riusa_quello_conservato(db_url, s3, monkeypatch):
    """Crash dopo la scrittura WORM e prima del commit: alla ripresa il RdV già
    conservato si riusa (niente sovrascrittura, impronta coerente con l'oggetto)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv, aip = await _pda(db_url, tenant, classe)
    blocco = await sigilla_blocco([aip], ora=ORA)
    await firma_blocco(blocco["id"], _FirmaNonQualificata())

    originale = block_sealer.get_async_session
    chiamate = {"n": 0}

    def sessione_che_cade():
        chiamate["n"] += 1
        if chiamate["n"] == 3:  # 1: candidati, 2: documenti, 3: transazione finale
            raise ConnectionError("database giù dopo la scrittura del RdV")
        return originale()

    monkeypatch.setattr(block_sealer, "get_async_session", sessione_che_cade)
    esito = await completa_conservazione(emessa_il=ORA)
    assert aip in esito.falliti
    monkeypatch.setattr(block_sealer, "get_async_session", originale)

    chiave = f"{tenant}/{aip}/{block_sealer.NOME_RDV}"
    primo = s3.get_object(Bucket=settings.s3_bucket_aip, Key=chiave)["Body"].read()
    esito = await completa_conservazione(emessa_il=datetime(2026, 10, 21, tzinfo=UTC))
    assert esito.completati == [aip], esito.falliti
    versioni = s3.list_object_versions(Bucket=settings.s3_bucket_aip, Prefix=chiave)["Versions"]
    assert len(versioni) == 1
    meta = await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1", uuid.UUID(pdv))
    rif = json.loads(meta[0]["metadata_json"])["rdv_definitiva"]
    assert rif["impronta_sha256"] == hashlib.sha256(primo).hexdigest()


def test_bucket_rdv_produzione_deve_essere_quello_terraform(monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "s3_bucket_aip", "verumdoc-aip")
    monkeypatch.setattr(settings, "s3_endpoint_url", "")
    with pytest.raises(worm.ConfigurazioneStorageNonValida):
        worm.bucket_conservativo(settings)
