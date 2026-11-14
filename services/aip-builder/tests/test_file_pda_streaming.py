# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 (b5): un file del PdV entra nel PdA senza passare intero dalla memoria del
servizio — impronte calcolate leggendo a blocchi, copia SERVER-SIDE nello
storage WORM (CopyObject o UploadPartCopy) con retention COMPLIANCE, verifica
della copia e ritentativi idempotenti.

S3 simulato con moto (Object Lock reale nel simulatore). Due differenze del
simulatore da AWS esercitano percorsi reali del codice: CopyObject verso un bucket
con Object Lock vuole l'intestazione ``x-amz-sdk-checksum-algorithm`` (il codice
ripete la copia una volta aggiungendola) e il multipart ignora la retention
indicata all'avvio (il codice la verifica e la applica dopo).
"""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import boto3
import pytest
from app.config import settings
from app.services import s3_uploader as up
from app.services.file_pda import includi_file_nel_pda
from botocore.exceptions import ClientError
from moto import mock_aws

from libs.shared.kafka.consumatore_affidabile import ErrorePermanente

REGIONE = "eu-south-1"
STAGING, WORM = "staging-pdv", "aip-worm"
CONTENUTO = b"%PDF-1.7 " + bytes(range(256)) * 400  # ~100 KB


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(settings, "s3_bucket_aip", WORM)
    monkeypatch.setattr(settings, "lettura_blocco_kb", 64)
    with mock_aws():
        up._bucket_verificati.clear()
        client = boto3.client("s3", region_name=REGIONE)
        for nome, lock in ((STAGING, False), (WORM, True)):
            client.create_bucket(Bucket=nome, ObjectLockEnabledForBucket=lock,
                                 CreateBucketConfiguration={"LocationConstraint": REGIONE})
        yield client
    up._bucket_verificati.clear()


def _retention(**kw) -> up.RetentionOggetto:
    return up.calcola_retention(up.PoliticaConservazione(anni_conservazione=10, **kw),
                                datetime.now(UTC))


def _doc(contenuto=CONTENUTO, algoritmo="SHA-256", **altro) -> dict:
    funzione = {"SHA-256": hashlib.sha256, "SHA-512": hashlib.sha512,
                "SHA3-256": hashlib.sha3_256}[algoritmo]
    return {"id": "d1", "tipo": "PRINCIPALE", "nome_file": "atto.pdf", "formato": "application/pdf",
            "hash": {"algoritmo": algoritmo, "impronta": funzione(contenuto).hexdigest()}, **altro}


DIMENSIONE = len(CONTENUTO)


def _info(chiave="caricamenti/t/u1/atto.pdf", dimensione=DIMENSIONE) -> dict:
    return {"doc_id": "d1", "s3_bucket": STAGING, "s3_key": chiave, "size_bytes": dimensione}


async def _includi(s3, doc=None, info=None, **kw):
    return await includi_file_nel_pda(
        aip_id="aip-1", tenant_id="ten-1", ordine=0, doc=doc or _doc(), info=info or _info(),
        suffisso="files/0000-atto.pdf", retention=kw.pop("retention", None) or _retention(),
        client=kw.pop("client", s3), **kw)


def _oggetti(s3, bucket) -> list[str]:
    return [o["Key"] for o in s3.list_objects_v2(Bucket=bucket).get("Contents", [])]


# ── impronte in streaming ─────────────────────────────────────────────

async def test_impronte_oggetto_lette_a_blocchi(s3):
    s3.put_object(Bucket=STAGING, Key="k", Body=CONTENUTO)
    letto = await up.impronte_oggetto(STAGING, "k", ("SHA-256", "SHA-512", "sha3-256"), client=s3)
    assert letto.dimensione == len(CONTENUTO) and letto.etag
    assert letto.impronte == {"SHA-256": hashlib.sha256(CONTENUTO).hexdigest(),
                              "SHA-512": hashlib.sha512(CONTENUTO).hexdigest(),
                              "SHA3-256": hashlib.sha3_256(CONTENUTO).hexdigest()}


def test_impronte_corpo_troncato_errore():
    class Corpo:
        def iter_chunks(self, n):
            yield b"abc"

        def close(self):
            pass

    class Client:
        def get_object(self, **_):
            return {"Body": Corpo(), "ContentLength": 10}

    with pytest.raises(ConnectionError):
        up.impronte_oggetto_sync(Client(), "b", "k", ["SHA-256"])


# ── inclusione nel PdA ────────────────────────────────────────────────

async def test_copia_server_side_con_retention_e_impronte(s3):
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    voce = await _includi(s3, doc=_doc(algoritmo="SHA3-256"))
    assert voce["s3_key"] == "ten-1/aip-1/files/0000-atto.pdf"
    assert voce["hash_sha256"] == hashlib.sha256(CONTENUTO).hexdigest()
    assert voce["hash_sha512"] == hashlib.sha512(CONTENUTO).hexdigest()
    assert voce["dimensione_bytes"] == len(CONTENUTO)
    copia = s3.get_object(Bucket=WORM, Key=voce["s3_key"])
    assert copia["Body"].read() == CONTENUTO
    assert copia["ObjectLockMode"] == "COMPLIANCE" and copia["ContentType"] == "application/pdf"
    # l'origine resta dov'è (la sessione di caricamento ne gestisce il ciclo di vita)
    assert _oggetti(s3, STAGING) == [_info()["s3_key"]]


async def test_copia_multipart_oltre_soglia_retention_e_legal_hold(s3, monkeypatch):
    """File oltre la soglia: UploadPartCopy a parti, completamento senza
    sovrascrittura, retention applicata e copia riletta per verificarla."""
    grande = b"%PDF-1.7 " + b"y" * (11 * 1024 * 1024)
    s3.put_object(Bucket=STAGING, Key="big", Body=grande)
    monkeypatch.setattr(settings, "copia_multipart_soglia_mb", 5)
    monkeypatch.setattr(settings, "copia_parte_mb", 5)
    chiamate = []
    originale = s3.upload_part_copy

    def conta(**kw):
        chiamate.append(kw["CopySourceRange"])
        return originale(**kw)

    monkeypatch.setattr(s3, "upload_part_copy", conta)
    retention = _retention(legal_hold=True)
    voce = await _includi(s3, doc=_doc(grande), info=_info("big", len(grande)), retention=retention)
    assert len(chiamate) == 3  # 5 + 5 + 1 MB circa
    testa = s3.head_object(Bucket=WORM, Key=voce["s3_key"])
    assert testa["ObjectLockMode"] == "COMPLIANCE"
    assert testa["ObjectLockRetainUntilDate"] >= retention.conserva_fino_a
    assert testa["ObjectLockLegalHoldStatus"] == "ON"
    assert hashlib.sha256(s3.get_object(Bucket=WORM, Key=voce["s3_key"])["Body"].read()
                          ).hexdigest() == hashlib.sha256(grande).hexdigest()


async def test_ritentativo_idempotente_oggetto_identico(s3):
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    prima = await _includi(s3)
    dopo = await _includi(s3)
    assert prima == dopo
    assert _oggetti(s3, WORM) == [prima["s3_key"]]


async def test_oggetto_presente_con_contenuto_diverso_errore_permanente(s3):
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    s3.put_object(Bucket=WORM, Key="ten-1/aip-1/files/0000-atto.pdf", Body=b"altro contenuto",
                  ChecksumAlgorithm="SHA256")
    with pytest.raises(ErrorePermanente) as exc:
        await _includi(s3)
    assert exc.value.codice == "AIP-STORAGE-001"


async def test_impronta_dichiarata_errata_nulla_nel_worm(s3):
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    doc = _doc()
    doc["hash"]["impronta"] = "ab" * 32
    with pytest.raises(ErrorePermanente) as exc:
        await _includi(s3, doc=doc)
    assert exc.value.codice == "AIP-INTEGRITA-002"
    assert _oggetti(s3, WORM) == []


async def test_dimensione_versata_diversa(s3):
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    with pytest.raises(ErrorePermanente) as exc:
        await _includi(s3, info=_info(dimensione=len(CONTENUTO) + 1))
    assert exc.value.codice == "AIP-INTEGRITA-003"


async def test_algoritmo_non_ammesso_e_riferimento_mancante(s3):
    doc = _doc()
    doc["hash"]["algoritmo"] = "MD5"
    with pytest.raises(ErrorePermanente) as exc:
        await _includi(s3, doc=doc)
    assert exc.value.codice == "AIP-INTEGRITA-001"
    with pytest.raises(ErrorePermanente) as exc:
        await _includi(s3, info={"s3_bucket": STAGING})
    assert exc.value.codice == "AIP-FILE-001"


async def test_file_mancante_errore_transitorio(s3):
    """File non leggibile: NON è un errore permanente (si ritenta, poi DLQ)."""
    with pytest.raises(ClientError):
        await _includi(s3)


class _ClientAlterato:
    """Client reale con la risposta di CopyObject alterata o con errore di precondizione."""

    def __init__(self, interno, *, checksum: str | None = None, precondizione=False):
        self.interno, self.checksum, self.precondizione = interno, checksum, precondizione

    def __getattr__(self, nome):
        return getattr(self.interno, nome)

    def copy_object(self, **kw):
        if self.precondizione:
            assert kw["CopySourceIfMatch"]  # la copia è vincolata all'ETag letto
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "CopyObject")
        risposta = self.interno.copy_object(**kw)
        risposta["CopyObjectResult"]["ChecksumSHA256"] = self.checksum
        return risposta


async def test_copia_con_checksum_diverso_errore_permanente(s3):
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    sbagliato = base64.b64encode(hashlib.sha256(b"altro").digest()).decode()
    with pytest.raises(ErrorePermanente) as exc:
        await _includi(s3, client=_ClientAlterato(s3, checksum=sbagliato))
    assert exc.value.codice == "AIP-STORAGE-002"


async def test_origine_modificata_errore_transitorio(s3):
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    with pytest.raises(up.OrigineModificata):
        await _includi(s3, client=_ClientAlterato(s3, precondizione=True))
    assert _oggetti(s3, WORM) == []


async def test_copia_senza_checksum_dello_storage_riletta(s3):
    """Storage che non restituisce il checksum della copia: la copia si rilegge a blocchi."""
    s3.put_object(Bucket=STAGING, Key=_info()["s3_key"], Body=CONTENUTO)
    letture = []
    originale = up.impronte_oggetto

    async def conta(bucket, chiave, *a, **kw):
        letture.append(bucket)
        return await originale(bucket, chiave, *a, **kw)

    from unittest.mock import patch

    import app.services.file_pda as modulo
    with patch.object(up, "impronte_oggetto", conta), \
            patch.object(modulo, "impronte_oggetto", conta):
        await _includi(s3, client=_ClientAlterato(s3, checksum=None))
    assert letture == [STAGING, WORM]


def test_intestazione_checksum_solo_sulla_chiamata_ripetuta():
    """La ripetizione di compatibilità aggiunge l'intestazione a quella sola chiamata."""
    viste = []

    class Eventi:
        def __init__(self):
            self.gestori = {}

        def register(self, evento, gestore, unique_id):
            self.gestori[unique_id] = gestore

        def unregister(self, evento, unique_id):
            del self.gestori[unique_id]

    class Client:
        def __init__(self):
            self.meta = type("M", (), {"events": Eventi()})()

        def copy_object(self, **kw):
            richiesta = type("R", (), {"headers": {}})()
            for g in list(self.meta.events.gestori.values()):
                g(request=richiesta)
            viste.append(dict(richiesta.headers))
            return {"VersionId": "v"}

    c = Client()
    assert up._copia_con_intestazione_checksum(c, {}) == {"VersionId": "v"}
    c.copy_object()
    assert viste == [{"x-amz-sdk-checksum-algorithm": "SHA256"}, {}]
