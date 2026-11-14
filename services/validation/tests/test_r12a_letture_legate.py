# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-146) — le letture successive al passo 5 (copia locale, testa e coda,
ricerca a blocchi della firma PAdES) sono legate ai byte di cui il passo 5 ha
calcolato le impronte: VersionId dello staging versionato, altrimenti ETag
(``IfMatch``). Un oggetto sostituito fra le letture → errore tecnico (si
ritenta), mai una lettura di byte diversi.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.services.lettura_file import LettoreS3, RiferimentoFile
from app.validators.base import ErroreVerificaTransitorio
from moto import mock_aws

PRIMA = b"%PDF-1.7 versione letta dal passo 5 /ByteRange [0 1 2 3] %%EOF"
DOPO = b"%PDF-1.7 SOSTITUITO dopo il passo 5 senza firma %%EOF"


@pytest.fixture(params=["versionato", "non_versionato"])
def s3(request, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        import boto3
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="pdv")
        if request.param == "versionato":
            client.put_bucket_versioning(Bucket="pdv",
                                         VersioningConfiguration={"Status": "Enabled"})
        client.put_object(Bucket="pdv", Key="k", Body=PRIMA)
        yield client, request.param


async def test_letture_successive_sulla_versione_del_passo_5(s3, tmp_path: Path) -> None:
    client, modo = s3
    lettore = LettoreS3(client)
    rif = RiferimentoFile(nome_file="a.pdf", bucket="pdv", chiave="k")
    letto = await lettore.leggi(rif, algoritmi=["SHA-256"])
    assert letto.impronte["SHA-256"] == hashlib.sha256(PRIMA).hexdigest()
    legato = rif.legato_a(letto)
    assert (legato.version_id is not None) == (modo == "versionato") and legato.etag
    client.put_object(Bucket="pdv", Key="k", Body=DOPO)   # sostituito dopo il passo 5
    if modo == "versionato":
        testa, _ = await lettore.testa_coda(legato, 16)
        assert testa == PRIMA[:16]
        assert await lettore.contiene(legato, b"/ByteRange")
        destinazione = tmp_path / "copia"
        await lettore.copia_in(legato, destinazione, limite_byte=10_000)
        assert destinazione.read_bytes() == PRIMA
    else:
        for lettura in (lambda: lettore.testa_coda(legato, 16),
                        lambda: lettore.contiene(legato, b"/ByteRange"),
                        lambda: lettore.copia_in(legato, tmp_path / "c", limite_byte=10_000)):
            with pytest.raises(ErroreVerificaTransitorio):
                await lettura()
    # senza legame (comportamento di prima) si leggerebbero i byte nuovi
    assert not await lettore.contiene(rif, b"/ByteRange")
