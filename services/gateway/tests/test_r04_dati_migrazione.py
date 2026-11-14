# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 dati — migrazione 20261023_028 con dati: upgrade da 024 e downgrade a 024
conservano le righe (outbox in attesa, eventi, consegne, audit) e i contatori
iniziali sono uguali ai COUNT(*). PostgreSQL reale.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.infra_reale import PG_TEMPLATE_URL, richiede_pg

GATEWAY = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.integration, richiede_pg]


def _url(nome: str) -> str:
    p = urlsplit(PG_TEMPLATE_URL)
    return urlunsplit((p.scheme, p.netloc, f"/{nome}", p.query, p.fragment))


async def _sql(url: str, istruzione: str, *argomenti):
    import asyncpg

    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        return await conn.fetch(istruzione, *argomenti)
    finally:
        await conn.close()


def _q(url, istruzione, *argomenti):
    return asyncio.run(_sql(url, istruzione, *argomenti))


def _alembic(url: str, comando: str, revisione: str, monkeypatch) -> None:
    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(GATEWAY / "alembic.ini"))
    cfg.set_main_option("script_location", str(GATEWAY / "alembic"))
    getattr(command, comando)(cfg, revisione)


@pytest.fixture
def db_copia():
    template = urlsplit(PG_TEMPLATE_URL).path.lstrip("/")
    nome = f"vdm_{uuid.uuid4().hex[:12]}"
    _q(_url("postgres"), f'CREATE DATABASE "{nome}" TEMPLATE "{template}"')
    try:
        yield _url(nome)
    finally:
        _q(_url("postgres"), f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')


def test_upgrade_e_downgrade_conservano_i_dati(db_copia, monkeypatch):
    url = db_copia
    _alembic(url, "downgrade", "20261021_024", monkeypatch)
    tenant, classe, pdv, aip, sotto = (uuid.uuid4() for _ in range(5))
    _q(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, stato, "
            "email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', 'a@example.org')",
       tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}")
    _q(url, "INSERT INTO public.classi_documentali (id, codice, nome) VALUES ($1, $2, 'c')",
       classe, f"c{classe.hex[:8]}")
    _q(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
            "VALUES ($1, 'x', 'CONSERVATO', $2, $3, $4)", pdv, classe, uuid.uuid4(), tenant)
    _q(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
            "hash_sha256, data_conservazione) VALUES ($1, $2, $3, 'urn:m', 'CONSERVATO', 'k', "
            "'b', $4, now())", aip, pdv, tenant, "a" * 64)
    _q(url, "INSERT INTO public.documenti (aip_id, nome_file, tipo, formato_mime, "
            "dimensione_bytes, hash_sha256, s3_key) VALUES ($1, 'f', 'P', 'x', 42, 'h', 'k')", aip)
    vecchio = datetime.now(UTC) - timedelta(days=40)
    # outbox: uno in attesa vecchio, uno pubblicato recente, uno pubblicato vecchio
    _q(url, "INSERT INTO public.outbox_eventi (topic, chiave, payload, creato_il, pubblicato_il) "
            "VALUES ('pdv.received', 'p', '{}', $1, NULL), ('pdv.received', 'q', '{}', now(), "
            "now()), ('pdv.received', 'r', '{}', $1, $1)", vecchio)
    righe = _q(url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, risorsa_id, "
                    "creato_il, posizione) VALUES ($1, 'versamento.ricevuto', 'versamento', 'a', "
                    "$2, 1), ($1, 'versamento.ricevuto', 'versamento', 'b', now(), 2) "
                    "RETURNING id", tenant, vecchio)
    _q(url, "INSERT INTO public.webhook_sottoscrizioni (id, tenant_id, url, tipi, segreto_cifrato, "
            "segreto_suffisso) VALUES ($1, $2, 'https://x.example', '[]', 'c', 'abcd')",
       sotto, tenant)
    for r in righe:
        _q(url, "INSERT INTO public.webhook_consegne (tenant_id, sottoscrizione_id, evento_id, "
                "stato) VALUES ($1, $2, $3, 'CONSEGNATA')", tenant, sotto, r["id"])
    _q(url, "INSERT INTO public.audit_global (evento, tenant_id, created_at) VALUES "
            "('vecchio', $1, $2), ('nuovo', $1, now())", tenant, vecchio)

    _alembic(url, "upgrade", "20261023_028", monkeypatch)
    outbox = _q(url, "SELECT chiave, origine, pubblicato_il IS NULL AS attesa FROM "
                     "public.outbox_eventi ORDER BY sequenza")
    assert [(r["chiave"], r["origine"], r["attesa"]) for r in outbox] == [
        ("p", "gateway", True), ("q", "gateway", False)]          # «r» pubblicato da 40 giorni
    eventi = _q(url, "SELECT id, risorsa_id, posizione, tableoid::regclass::text AS t "
                     "FROM public.eventi_cliente ORDER BY id")
    assert [(e["risorsa_id"], e["posizione"]) for e in eventi] == [("a", 1), ("b", 2)]
    assert eventi[0]["t"] == "partizioni.eventi_cliente_p" + vecchio.strftime("%Y%m")
    consegne = _q(url, "SELECT c.evento_id, c.evento_creato_il = e.creato_il AS stessa "
                       "FROM public.webhook_consegne c JOIN public.eventi_cliente e "
                       "ON e.id = c.evento_id ORDER BY c.id")
    assert [c["evento_id"] for c in consegne] == [e["id"] for e in eventi]
    assert all(c["stessa"] for c in consegne)
    assert len(_q(url, "SELECT 1 FROM public.audit_global")) == 2
    # nuovi id dopo quelli copiati (sequenze riallineate)
    nuovo = _q(url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, risorsa_id) "
                    "VALUES ($1, 'x', 'y', 'z') RETURNING id", tenant)
    assert nuovo[0]["id"] > eventi[-1]["id"]
    contatori = {r["metrica"]: r["v"] for r in _q(
        url, "SELECT metrica, sum(valore) AS v FROM public.contatori_ente WHERE tenant_id = $1 "
             "GROUP BY 1", tenant)}
    assert contatori == {"pdv.stato.CONSERVATO": 1, "pdv.ricevuti": 1, "aip.stato.CONSERVATO": 1,
                         "aip.versati": 1, "aip.conservati": 1, "documenti.versati": 1,
                         "documenti.byte": 42}
    _q(url, "DELETE FROM public.eventi_cliente WHERE id = $1", nuovo[0]["id"])

    _alembic(url, "downgrade", "20261021_024", monkeypatch)
    assert [r["chiave"] for r in _q(url, "SELECT chiave FROM public.outbox_eventi "
                                         "ORDER BY sequenza")] == ["p", "q"]
    assert [r["risorsa_id"] for r in _q(url, "SELECT risorsa_id FROM public.eventi_cliente "
                                             "ORDER BY id")] == ["a", "b"]
    assert len(_q(url, "SELECT 1 FROM public.webhook_consegne")) == 2
    assert sorted(r["evento"] for r in _q(url, "SELECT evento FROM public.audit_global")) == [
        "nuovo", "vecchio"]
    assert _q(url, "SELECT to_regclass('public.contatori_ente') AS r")[0]["r"] is None
    # e di nuovo su: la catena è ripetibile
    _alembic(url, "upgrade", "20261023_028", monkeypatch)
    payload = _q(url, "SELECT payload FROM public.outbox_eventi WHERE chiave = 'p'")
    assert json.loads(payload[0]["payload"]) == {}
