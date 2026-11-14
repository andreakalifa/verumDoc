# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R11 (revisione T2) — rapporto pre-deploy delle classi con profili in conflitto con la
composizione di R11, su PostgreSQL vero (``VD_TEST_DATABASE_URL``, testa migrata).

Una classe con tipologia «documento_informatico» (v1) e un profilo proprio che:
  - restringe ``tipologia_documentale`` (testo libero) a un elenco chiuso:
    restrizione mantenuta, nessun cambio;
  - rende facoltativo ``riservato`` (obbligatorio per la tipologia): cambio segnalato;
  - ridefinisce ``data_documento`` come testo (più largo della data): vale la data.
Un PdV recente senza ``riservato``: accettato con R05, rifiutato con R11.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI.parent))
sys.path.insert(0, str(QUI.parents[2]))

import conflitti_classi as cc  # noqa: E402

URL = os.environ.get("VD_TEST_DATABASE_URL")
richiede_pg = pytest.mark.skipif(not URL, reason="VD_TEST_DATABASE_URL non impostata")


def _base() -> str:
    assert URL
    return URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0]


async def _copia(nome: str, crea: bool) -> None:
    """Database proprio del test (copia del template), per non lasciare righe in quello
    condiviso dalle altre suite."""
    import asyncpg

    assert URL
    admin = await asyncpg.connect(f"{_base()}/postgres")
    try:
        if crea:
            await admin.execute(f'CREATE DATABASE "{nome}" TEMPLATE "{URL.rsplit("/", 1)[1]}"')
        else:
            await admin.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
    finally:
        await admin.close()


async def _prepara(nome: str) -> tuple[str, str]:
    import asyncpg

    conn = await asyncpg.connect(f"{_base()}/{nome}")
    try:
        tid, cid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await conn.execute("INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                           "schema_name, stato, email_referente) VALUES ($1, $2, 'Ente', $3, "
                           "'ATTIVO', 'a@b.it')", tid, tid.hex[:11], f"t_{tid.hex[:20]}")
        await conn.execute("INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, "
                           "attivo) VALUES ($1, $2, $3, 'N', 'C', 'ADMIN_TENANT', true)",
                           uid, tid, f"{uid.hex[:8]}@x.it")
        codice = f"rel_{cid.hex[:6]}"
        await conn.execute("INSERT INTO public.classi_documentali (id, tenant_id, codice, nome, "
                           "formati_ammessi, max_dimensione_mb, attiva) VALUES ($1, $2, $3, $3, "
                           "'[]'::jsonb, 100, true)", cid, tid, codice)
        await conn.execute("INSERT INTO public.impostazioni_valori (livello, tenant_id, classe_id, "
                           "codice, valore) VALUES ('CLASSE', $1, $2, 'PROFILO_TIPOLOGIA', "
                           "'\"documento_informatico\"'::jsonb)", tid, cid)
        await conn.execute("INSERT INTO public.profili_metadati (id, classe_id, versione, "
                           "valido_dal, campi) VALUES ($1, $2, 1, DATE '2026-01-01', "
                           "CAST($3 AS jsonb))", uuid.uuid4(), cid, json.dumps([
                               {"nome": "tipologia_documentale", "tipo": "enum",
                                "valori_ammessi": ["Relazione"], "obbligatorio": True},
                               {"nome": "riservato", "tipo": "booleano", "obbligatorio": False},
                               {"nome": "data_documento", "tipo": "stringa"}]))
        parametri = {"identificativo_documento": "X1", "modalita_formazione": "c",
                     "tipologia_documentale": "Relazione", "data_documento": "2026-10-01",
                     "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "A",
                     "oggetto": "Relazione di prova", "firmato_digitalmente": False,
                     "sigillato_elettronicamente": False, "marcatura_temporale": False}
        meta = {"indice": {"classe_documentale": codice, "documenti": [{
            "id": "d1", "tipo": "PRINCIPALE", "nome_file": "a.pdf",
            "hash": {"impronta": "ab" * 32, "algoritmo": "SHA-256"}, "parametri": parametri}]},
            "docs_s3_keys": [{"nome_file": "a.pdf", "size_bytes": 10,
                              "formato_rilevato": "application/pdf"}]}
        await conn.execute("INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, "
                           "tenant_id, metadata_json, data_ricezione) VALUES ($1, 'PdV', "
                           "'RICEVUTO', $2, $3, $4, CAST($5 AS jsonb), $6)", uuid.uuid4(), cid,
                           uid, tid, json.dumps(meta), datetime.now(UTC) - timedelta(days=1))
        return str(cid), codice
    finally:
        await conn.close()


@richiede_pg
def test_rapporto_delle_classi_in_conflitto(capsys: pytest.CaptureFixture[str]) -> None:
    nome = f"vdt_{uuid.uuid4().hex[:12]}"
    asyncio.run(_copia(nome, crea=True))
    try:
        cid, _ = asyncio.run(_prepara(nome))
        assert cc.main(["--database-url", str(URL).rsplit("/", 1)[0] + f"/{nome}"]) == 1
    finally:
        asyncio.run(_copia(nome, crea=False))
    rapporto = json.loads(capsys.readouterr().out)
    voce = next(c for c in rapporto["classi"] if c["classe_id"] == cid)
    campi = {c["campo"]: c["motivi"] for c in voce["conflitti"]}
    assert "tipologia_documentale" not in campi              # restrizione mantenuta
    assert "resta obbligatorio" in campi["riservato"][0]
    assert any("incompatibile" in m for m in campi["data_documento"])
    anteprima = voce["anteprima"]
    assert anteprima["pdv_analizzati"] == 1
    assert (anteprima["rifiutati_con_r05"], anteprima["rifiutati_con_r11"]) == (0, 1)


def test_errore_di_connessione_uscita_2() -> None:
    assert cc.main(["--database-url", "postgresql+asyncpg://nessuno@127.0.0.1:1/x"]) == 2
