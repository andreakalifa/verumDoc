# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza (g5-sicurezza) — dashboard-api, su PostgreSQL e S3 (moto) reali.

B8  ZIP dell'AIP: i nomi dei documenti letti dal DB sono normalizzati (solo il
    nome finale, niente separatori o '..'): nessuna voce dello ZIP esce dalla
    cartella files/ (zip-slip), i doppioni non si sovrascrivono.
B11 /audit: solo i ruoli di governo (SUPER_ADMIN, RSC_CONSERVATORE, ADMIN_TENANT).
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import uuid
import zipfile

import pytest

from tests.dati_prova import BUCKET, PDF, client, crea_aip, token
from tests.infra_reale import (  # noqa: F401
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)

NOMI_OSTILI = ["../../etc/passwd", "..\\..\\windows\\win.ini", "/assoluto/x.pdf",
               "C:\\Users\\a.pdf", "doc.pdf", ".nascosto", "a\x00b\r\n.pdf", ""]


@pytest.fixture
def s3(s3_server, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    cli = client_s3(s3_server)
    try:
        cli.create_bucket(Bucket=BUCKET)
    except cli.exceptions.BucketAlreadyOwnedByYou:
        pass
    return cli


@pytest.mark.parametrize("nome", NOMI_OSTILI)
def test_nome_nel_zip_solo_nome_finale(nome: str) -> None:
    from app.routers.download import nome_nel_zip

    sicuro = nome_nel_zip(nome, set())
    assert sicuro and "/" not in sicuro and "\\" not in sicuro
    assert not sicuro.startswith(".") and ":" not in sicuro
    assert all(ord(ch) >= 32 for ch in sicuro)


def test_nome_nel_zip_doppioni() -> None:
    from app.routers.download import nome_nel_zip

    usati: set[str] = set()
    assert [nome_nel_zip(n, usati) for n in ("doc.pdf", "x/doc.pdf", "DOC.pdf", "doc")] == \
        ["doc.pdf", "doc-2.pdf", "DOC-3.pdf", "doc"]


@richiede_pg
async def test_zip_con_nomi_ostili_resta_nella_cartella(db_url, s3) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await crea_aip(db_url, tenant, classe, s3=s3)
    for i, nome in enumerate(NOMI_OSTILI):
        chiave = f"{tenant}/{aip}/ostile-{i}"
        s3.put_object(Bucket=BUCKET, Key=chiave, Body=PDF)
        await sql(
            db_url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                          "dimensione_bytes, hash_sha256, s3_key, ordine) VALUES ($1, $2, $3, "
                          "'ALLEGATO', 'application/pdf', $4, $5, $6, $7)",
                  uuid.uuid4(), aip, nome.replace("\x00", ""), len(PDF),
                  hashlib.sha256(PDF).hexdigest(), chiave, i + 1)
    async with client() as c:
        z = await c.get(f"/api/dashboard/aip/{aip}/download/completo",
                        headers=token("OPERATORE", tenant))
    assert z.status_code == 200, z.text
    radice = f"AIP_{str(aip)[:8]}/"
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        nomi = zf.namelist()
        documenti = [n for n in nomi if n.startswith(radice + "files/")]
        assert len(documenti) == len(NOMI_OSTILI) + 1          # nessuno sovrascritto
        for voce in nomi:
            assert voce.startswith(radice) and "\\" not in voce
            assert posixpath.normpath(voce) == voce and ".." not in voce.split("/")
        for voce in documenti:
            assert posixpath.dirname(voce) == radice + "files"
            assert zf.read(voce) == PDF


@richiede_pg
@pytest.mark.parametrize("ruolo", ["VIEWER", "OPERATORE", "API_SYSTEM"])
async def test_audit_vietato_ai_profili_operativi(db_url, ruolo: str) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with client() as c:
        r = await c.get("/api/dashboard/audit", headers=token(ruolo, tenant))
    assert r.status_code == 403


@richiede_pg
@pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "SUPER_ADMIN", "RSC_CONSERVATORE"])
async def test_audit_ai_ruoli_di_governo(db_url, ruolo: str) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    async with client() as c:
        r = await c.get("/api/dashboard/audit", headers=token(ruolo, tenant))
    assert r.status_code == 200, r.text


@richiede_pg
async def test_revoca_utente_disattivato_ed_ente_sospeso(db_url, s3) -> None:
    """B5: un token valido non basta se l'utente è disattivato o l'ente sospeso."""
    from libs.shared.security.revoca import svuota_cache_revoche

    svuota_cache_revoche()
    tenant, classe = await crea_tenant_e_classe(db_url)
    aip = await crea_aip(db_url, tenant, classe, s3=s3)
    uid = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, "
                      "attivo) VALUES ($1, $2, 'a@x.it', 'N', 'C', 'ADMIN_TENANT', false)",
              uid, tenant)
    h = token("ADMIN_TENANT", tenant, user_id=str(uid))
    async with client() as c:
        for url in ("/api/dashboard/audit", f"/api/dashboard/aip/{aip}/files"):
            r = await c.get(url, headers=h)
            assert r.status_code == 401 and r.json()["codice"] == "AUTH-003", url
        await sql(db_url, "UPDATE public.users SET attivo = true WHERE id = $1", uid)
        svuota_cache_revoche()
        assert (await c.get("/api/dashboard/audit", headers=h)).status_code == 200
        await sql(db_url, "UPDATE public.tenants SET stato = 'DISATTIVATO' WHERE id = $1", tenant)
        svuota_cache_revoche()
        assert (await c.get(f"/api/dashboard/aip/{aip}/files", headers=h)).status_code == 401
    svuota_cache_revoche()
