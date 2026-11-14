# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Download degli AIP — dashboard-api, su PostgreSQL e S3 (moto) reali.

VD-C-022 (R03): le query usavano ``:tid::uuid``, che SQLAlchemy non riconosce
come parametro: tutti i download andavano in errore. I vecchi test simulavano
il database (e non mandavano nemmeno il token) e non potevano accorgersene;
questi eseguono le query vere contro lo schema prodotto dalle migrazioni
(tests/infra_reale.py, VD_TEST_DATABASE_URL).
"""
from __future__ import annotations

import hashlib
import io
import uuid
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import (  # noqa: F401
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)

pytestmark = richiede_pg

BUCKET = "verumdoc-aip-test"
MANIFEST = b'{"manifest": true}'
SINCRO = b"<?xml version='1.0'?><PIndex/>"
PDF = b"%PDF-1.4 prova"


def _token(tenant_id: str | None, ruolo: str = "OPERATORE") -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant_id or "", user_id=str(uuid.uuid4()), ruolo=ruolo,
    )}


@pytest.fixture
def s3(s3_server, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    client = client_s3(s3_server)
    try:
        client.create_bucket(Bucket=BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    return client


async def _aip_con_file(url: str, s3, tenant: uuid.UUID, classe: uuid.UUID,
                        rdv: str | None = "<RdV/>") -> uuid.UUID:
    import json

    pdv, aip, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    meta = json.dumps({"rdv_preliminare": rdv} if rdv else {})
    await sql(url, "INSERT INTO public.pdv (id, oggetto, classe_doc_id, user_id, tenant_id, "
                   "metadata_json) VALUES ($1, 'Prova', $2, $3, $4, CAST($5 AS jsonb))",
              pdv, classe, uuid.uuid4(), tenant, meta)
    chiave = f"{tenant}/{aip}"
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "sincro_xml_key, hash_sha256) VALUES ($1, $2, $3, $4, 'SU_STORAGE', $5, $6, "
                   "$7, $8)",
              aip, pdv, tenant, f"urn:prova:{aip}", f"{chiave}/manifest.json", BUCKET,
              f"{chiave}/IndiceAIP.xml", "a" * 64)
    await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                   "dimensione_bytes, hash_sha256, s3_key) VALUES ($1, $2, 'doc.pdf', "
                   "'PRINCIPALE', "
                   "'application/pdf', $3, $4, $5)",
              doc, aip, len(PDF), hashlib.sha256(PDF).hexdigest(), f"{chiave}/doc.pdf")
    s3.put_object(Bucket=BUCKET, Key=f"{chiave}/manifest.json", Body=MANIFEST)
    s3.put_object(Bucket=BUCKET, Key=f"{chiave}/IndiceAIP.xml", Body=SINCRO)
    s3.put_object(Bucket=BUCKET, Key=f"{chiave}/doc.pdf", Body=PDF)
    return aip


async def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_download_del_proprio_cliente(db_url, s3) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip_con_file(db_url, s3, tenant, classe)
    h = _token(str(tenant))
    async with await _client() as c:
        files = await c.get(f"/api/dashboard/aip/{aip}/files", headers=h)
        assert files.status_code == 200, files.text
        # G2: anche il rapporto di versamento è elencato (metadato)
        assert {f["tipo"] for f in files.json()["files"]} == {"MANIFEST", "SINCRO", "RDV"}
        assert files.json()["contenuto_accessibile"] is True

        m = await c.get(f"/api/dashboard/aip/{aip}/download/manifest", headers=h)
        assert m.status_code == 200 and m.content == MANIFEST
        assert "attachment" in m.headers["content-disposition"]

        x = await c.get(f"/api/dashboard/aip/{aip}/download/sincro", headers=h)
        assert x.status_code == 200 and x.content == SINCRO

        r = await c.get(f"/api/dashboard/aip/{aip}/download/rdv", headers=h)
        assert r.status_code == 200 and r.content == b"<RdV/>"
        assert r.headers["x-rdv-tipo"] == "preliminare"

        z = await c.get(f"/api/dashboard/aip/{aip}/download/completo", headers=h)
    assert z.status_code == 200, z.text
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        nomi = set(zf.namelist())
        breve = str(aip)[:8]
        assert f"AIP_{breve}/manifest.json" in nomi
        assert f"AIP_{breve}/IndiceAIP_SInCRO.xml" in nomi
        assert f"AIP_{breve}/files/doc.pdf" in nomi
        assert f"AIP_{breve}/RdV_preliminare.xml" in nomi
        assert zf.read(f"AIP_{breve}/files/doc.pdf") == PDF


@pytest.mark.parametrize("percorso", [
    "files", "download/manifest", "download/sincro", "download/rdv", "download/completo",
])
async def test_aip_di_altro_cliente_404(db_url, s3, percorso: str) -> None:
    t1, classe = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    aip = await _aip_con_file(db_url, s3, t1, classe)
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/aip/{aip}/{percorso}", headers=_token(str(t2)))
    assert r.status_code == 404


async def test_id_non_valido_404_e_token_senza_cliente_403(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with await _client() as c:
        r = await c.get("/api/dashboard/aip/non-un-uuid/files", headers=_token(str(tenant)))
        assert r.status_code == 404
        r = await c.get(f"/api/dashboard/aip/{uuid.uuid4()}/files", headers=_token("nessuno"))
        assert r.status_code == 403
        r = await c.get(f"/api/dashboard/aip/{uuid.uuid4()}/files")
        assert r.status_code == 401


async def test_rdv_non_ancora_disponibile_404(db_url, s3) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await _aip_con_file(db_url, s3, tenant, classe, rdv=None)
    async with await _client() as c:
        r = await c.get(f"/api/dashboard/aip/{aip}/download/rdv", headers=_token(str(tenant)))
    assert r.status_code == 404
