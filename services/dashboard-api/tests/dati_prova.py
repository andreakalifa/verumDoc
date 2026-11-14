# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Dati di prova comuni ai test di comportamento della dashboard (g3-test, VD-C-030):
token firmati dal servizio, client ASGI sull'app vera, PdV/AIP/documento reali
nel PostgreSQL di test (tests/infra_reale.py) e, se richiesto, i file su S3 (moto).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from tests.infra_reale import sql

BUCKET = "verumdoc-aip-g3"
PDF = b"%PDF-1.4 contenuto di prova"


def token(ruolo: str = "OPERATORE", tenant: uuid.UUID | str | None = None,
          user_id: str | None = None) -> dict[str, str]:
    """Intestazione Authorization con un token RS256 valido (ente sempre presente)."""
    from app.auth.jwt_handler import create_access_token

    tok = create_access_token(tenant_id=str(tenant or uuid.uuid4()),
                              user_id=user_id or str(uuid.uuid4()), ruolo=ruolo)
    return {"Authorization": f"Bearer {tok}"}


def client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def crea_aip(url: str, tenant: uuid.UUID, classe: uuid.UUID, *, s3=None,
                   meta_pdv: str = "{}") -> uuid.UUID:
    """PdV + AIP CONSERVATO + un documento; con ``s3`` carica anche i file."""
    pdv, aip, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    chiave = f"{tenant}/{aip}"
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "metadata_json) VALUES ($1, 'Prova', 'CONSERVATO', $2, $3, $4, "
                   "CAST($5 AS jsonb))", pdv, classe, uuid.uuid4(), tenant, meta_pdv)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
                   "sincro_xml_key, hash_sha256, data_conservazione) VALUES ($1, $2, $3, $4, "
                   "'CONSERVATO', $5, $6, $7, $8, $9)",
              aip, pdv, tenant, f"urn:prova:{aip}", f"{chiave}/manifest.json", BUCKET,
              f"{chiave}/IndiceAIP.xml", "a" * 64, datetime.now(UTC))
    await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                   "dimensione_bytes, hash_sha256, s3_key) VALUES ($1, $2, 'doc.pdf', "
                   "'PRINCIPALE', "
                   "'application/pdf', $3, $4, $5)",
              doc, aip, len(PDF), hashlib.sha256(PDF).hexdigest(), f"{chiave}/doc.pdf")
    if s3 is not None:
        s3.put_object(Bucket=BUCKET, Key=f"{chiave}/manifest.json", Body=b'{"m":1}')
        s3.put_object(Bucket=BUCKET, Key=f"{chiave}/IndiceAIP.xml", Body=b"<PIndex/>")
        s3.put_object(Bucket=BUCKET, Key=f"{chiave}/doc.pdf", Body=PDF)
    return aip
