# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test dello storage conservativo con Object Lock — VD-C-011.

S3 simulato con moto (Object Lock, If-None-Match e retention reali nel
simulatore). Da affiancare in CI con MinIO con Object Lock (REGOLE §23.3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import boto3
import pytest
from app.services import s3_uploader as up
from moto import mock_aws

ADESSO = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
REGIONE = "eu-south-1"


@pytest.fixture
def s3():
    with mock_aws():
        up._bucket_verificati.clear()
        yield boto3.client("s3", region_name=REGIONE)
    up._bucket_verificati.clear()


def _bucket(s3, nome: str, *, lock: bool = True, retention_predefinita: bool = False) -> str:
    kwargs = {"Bucket": nome, "CreateBucketConfiguration": {"LocationConstraint": REGIONE}}
    if lock:
        kwargs["ObjectLockEnabledForBucket"] = True
    s3.create_bucket(**kwargs)
    if retention_predefinita:
        s3.put_object_lock_configuration(
            Bucket=nome,
            ObjectLockConfiguration={
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Years": 1}},
            },
        )
    return nome


def _retention(anni: int = 10, **kw) -> up.RetentionOggetto:
    return up.calcola_retention(up.PoliticaConservazione(anni_conservazione=anni, **kw), ADESSO)


# ── calcolo della retention ───────────────────────────────────────────


def test_calcola_retention_anni_classe():
    """La retention termina dopo gli anni della classe documentale, in modalità COMPLIANCE."""
    r = _retention(10)
    assert r.conserva_fino_a == datetime(2036, 10, 20, 12, 0, tzinfo=UTC)
    assert r.modalita == "COMPLIANCE" and r.legal_hold is False


def test_calcola_retention_29_febbraio_non_accorcia():
    r = up.calcola_retention(
        up.PoliticaConservazione(anni_conservazione=1), datetime(2028, 2, 29, tzinfo=UTC)
    )
    assert r.conserva_fino_a == datetime(2029, 3, 1, tzinfo=UTC)


def test_calcola_retention_permanente_legal_hold_e_orizzonte_obbligatorio():
    pol = up.PoliticaConservazione(anni_conservazione=None, permanente=True)
    with pytest.raises(up.RetentionNonDeterminabile):
        up.calcola_retention(pol, ADESSO)
    r = up.calcola_retention(pol, ADESSO, orizzonte_permanente_anni=50)
    assert r.legal_hold is True
    assert r.conserva_fino_a.year == 2076


def test_calcola_retention_anni_mancanti_errore():
    """Nessun default silenzioso (era 10 anni): senza anni validi si rifiuta."""
    for anni in (None, 0, -3):
        with pytest.raises(up.RetentionNonDeterminabile):
            up.calcola_retention(up.PoliticaConservazione(anni_conservazione=anni), ADESSO)


def test_calcola_retention_data_naive_rifiutata():
    with pytest.raises(up.RetentionNonDeterminabile):
        up.calcola_retention(up.PoliticaConservazione(anni_conservazione=5), datetime(2026, 1, 1))


# ── configurazione del bucket ─────────────────────────────────────────


def _cfg(env: str, bucket: str, endpoint: str = "") -> SimpleNamespace:
    return SimpleNamespace(env=env, s3_bucket_aip=bucket, s3_endpoint_url=endpoint)


def test_bucket_produzione_deve_coincidere_con_terraform():
    assert up.nome_bucket_terraform("production") == "verumdoc-prod-aip-prod"
    assert up.nome_bucket_terraform("staging") == "verumdoc-staging-aip-prod"
    assert (
        up.bucket_conservativo(_cfg("production", "verumdoc-prod-aip-prod"))
        == "verumdoc-prod-aip-prod"
    )
    with pytest.raises(up.ConfigurazioneStorageNonValida, match="Terraform"):
        up.bucket_conservativo(_cfg("production", "verumdoc-aip"))


def test_bucket_produzione_endpoint_locale_rifiutato():
    with pytest.raises(up.ConfigurazioneStorageNonValida, match="locale"):
        up.bucket_conservativo(_cfg("production", "verumdoc-prod-aip-prod", "http://minio:9000"))


def test_bucket_sviluppo_libero_ma_non_vuoto():
    assert up.bucket_conservativo(_cfg("development", "verumdoc-aip")) == "verumdoc-aip"
    with pytest.raises(up.ConfigurazioneStorageNonValida):
        up.bucket_conservativo(_cfg("development", ""))


# ── scrittura WORM ────────────────────────────────────────────────────


def test_scrittura_applica_object_lock_compliance(s3):
    """Oggetto scritto in COMPLIANCE, retention dalla classe, legal hold come richiesto."""
    b = _bucket(s3, "aip-test")
    r = _retention(10, legal_hold=True)
    resp = up.scrivi_oggetto_conservato(
        s3, b, "t1/a1/IndiceAIP.xml", b"<x/>", "application/xml", r, adesso=ADESSO, produzione=False
    )
    assert resp.get("VersionId")
    h = s3.head_object(Bucket=b, Key="t1/a1/IndiceAIP.xml")
    assert h["ObjectLockMode"] == "COMPLIANCE"
    assert h["ObjectLockRetainUntilDate"] == r.conserva_fino_a
    assert h["ObjectLockLegalHoldStatus"] == "ON"


def test_scrittura_oggetto_non_cancellabile(s3):
    """Con COMPLIANCE la versione scritta non si cancella prima della scadenza."""
    from botocore.exceptions import ClientError

    b = _bucket(s3, "aip-test")
    up.scrivi_oggetto_conservato(
        s3,
        b,
        "t1/a1/doc.pdf",
        b"%PDF",
        "application/pdf",
        _retention(),
        adesso=ADESSO,
        produzione=False,
    )
    versione = s3.list_object_versions(Bucket=b)["Versions"][0]["VersionId"]
    with pytest.raises(ClientError):
        s3.delete_object(Bucket=b, Key="t1/a1/doc.pdf", VersionId=versione)


def test_scrittura_nessuna_sovrascrittura(s3):
    """Seconda scrittura sulla stessa chiave: OggettoGiaPresente, contenuto originale intatto."""
    b = _bucket(s3, "aip-test")
    up.scrivi_oggetto_conservato(
        s3,
        b,
        "t1/a1/doc",
        b"originale",
        "text/plain",
        _retention(),
        adesso=ADESSO,
        produzione=False,
    )
    with pytest.raises(up.OggettoGiaPresente):
        up.scrivi_oggetto_conservato(
            s3,
            b,
            "t1/a1/doc",
            b"altro",
            "text/plain",
            _retention(),
            adesso=ADESSO,
            produzione=False,
        )
    assert s3.get_object(Bucket=b, Key="t1/a1/doc")["Body"].read() == b"originale"


def test_scrittura_concorrente_if_none_match(s3, monkeypatch):
    """Se l'oggetto compare tra controllo e scrittura, If-None-Match fa fallire la put (412)."""
    b = _bucket(s3, "aip-test")
    s3.put_object(
        Bucket=b,
        Key="t1/a1/doc",
        Body=b"arrivato prima",
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=_retention().conserva_fino_a,
    )
    originale = s3.head_object

    def _head_finto(**kw):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    monkeypatch.setattr(s3, "head_object", _head_finto)
    with pytest.raises(up.OggettoGiaPresente, match="concorrenza"):
        up.scrivi_oggetto_conservato(
            s3,
            b,
            "t1/a1/doc",
            b"secondo",
            "text/plain",
            _retention(),
            adesso=ADESSO,
            produzione=False,
        )
    monkeypatch.setattr(s3, "head_object", originale)


def test_bucket_senza_object_lock_errore_esplicito(s3):
    b = _bucket(s3, "aip-senza-lock", lock=False)
    with pytest.raises(up.BucketSenzaObjectLock):
        up.scrivi_oggetto_conservato(
            s3, b, "t1/a1/doc", b"x", "text/plain", _retention(), adesso=ADESSO, produzione=False
        )
    assert "Contents" not in s3.list_objects_v2(Bucket=b)


def test_bucket_produzione_senza_retention_predefinita_rifiutato(s3):
    """In produzione serve anche la retention predefinita COMPLIANCE (come in Terraform)."""
    b = _bucket(s3, "aip-lock-senza-default")
    with pytest.raises(up.BucketSenzaObjectLock, match="predefinita"):
        up.scrivi_oggetto_conservato(
            s3, b, "t1/a1/doc", b"x", "text/plain", _retention(), adesso=ADESSO, produzione=True
        )
    b2 = _bucket(s3, "aip-lock-con-default", retention_predefinita=True)
    up.scrivi_oggetto_conservato(
        s3, b2, "t1/a1/doc", b"x", "text/plain", _retention(), adesso=ADESSO, produzione=True
    )


def test_bucket_mancante_creato_con_lock_solo_in_sviluppo(s3):
    up.scrivi_oggetto_conservato(
        s3,
        "aip-nuovo",
        "t1/a1/doc",
        b"x",
        "text/plain",
        _retention(),
        adesso=ADESSO,
        produzione=False,
    )
    assert (
        s3.get_object_lock_configuration(Bucket="aip-nuovo")["ObjectLockConfiguration"][
            "ObjectLockEnabled"
        ]
        == "Enabled"
    )
    with pytest.raises(up.ErroreStorageConservativo):
        up.scrivi_oggetto_conservato(
            s3,
            "aip-assente-prod",
            "t1/a1/doc",
            b"x",
            "text/plain",
            _retention(),
            adesso=ADESSO,
            produzione=True,
        )


@pytest.mark.parametrize("chiave", ["../x", "t1//doc", "/assoluta", "t1/a1/\x00", "t1/./doc"])
def test_chiave_pericolosa_rifiutata(s3, chiave):
    b = _bucket(s3, "aip-test")
    with pytest.raises(up.ErroreStorageConservativo, match="chiave"):
        up.scrivi_oggetto_conservato(
            s3, b, chiave, b"x", "text/plain", _retention(), adesso=ADESSO, produzione=False
        )


def test_retention_scaduta_rifiutata(s3):
    b = _bucket(s3, "aip-test")
    passata = up.RetentionOggetto(
        conserva_fino_a=datetime(2020, 1, 1, tzinfo=UTC), legal_hold=False
    )
    with pytest.raises(up.RetentionNonDeterminabile):
        up.scrivi_oggetto_conservato(
            s3, b, "t1/a1/doc", b"x", "text/plain", passata, adesso=ADESSO, produzione=False
        )


@pytest.mark.asyncio
async def test_upload_aip_to_s3_usa_bucket_configurato(s3, monkeypatch):
    """L'API asincrona usa il bucket unico S3_BUCKET_AIP e restituisce chiave, bucket e versione."""
    _bucket(s3, "verumdoc-aip")
    monkeypatch.setattr(up, "_client", lambda: s3)
    monkeypatch.setattr(up.settings, "s3_bucket_aip", "verumdoc-aip")
    monkeypatch.setattr(up.settings, "env", "development")
    chiave, bucket, versione = await up.upload_aip_to_s3(
        "a1",
        "t1",
        b"<x/>",
        "IndiceAIP_SInCRO.xml",
        "application/xml",
        retention=up.calcola_retention(
            up.PoliticaConservazione(anni_conservazione=10), datetime.now(UTC)
        ),
    )
    assert (chiave, bucket) == ("t1/a1/IndiceAIP_SInCRO.xml", "verumdoc-aip")
    assert versione
    assert s3.head_object(Bucket=bucket, Key=chiave)["ObjectLockMode"] == "COMPLIANCE"


@pytest.mark.asyncio
async def test_upload_aip_to_s3_senza_retention_non_ammesso():
    """REGOLE §10.2: la retention è un argomento obbligatorio."""
    with pytest.raises(TypeError):
        await up.upload_aip_to_s3("a1", "t1", b"x", "doc")  # type: ignore[call-arg]
