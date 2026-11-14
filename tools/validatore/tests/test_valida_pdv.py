# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 Q1 — CLI del validatore preventivo (tools/validatore/valida_pdv.py) e
confronto delle regole sui PdV (tools/confronta_regole.py).

Il validatore senza rete usa il profilo esportato dal servizio e lo STESSO motore:
stessi codici del versamento, file letti a blocchi, codici d'uscita 0/1/2.
Il confronto usa lo stesso campione e la stessa funzione dell'anteprima; con
VD_TEST_DATABASE_URL gira anche su PostgreSQL reale (connessione in sola lettura).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI.parent))
sys.path.insert(0, str(QUI.parents[1]))

import confronta_regole as cr  # noqa: E402
import valida_pdv as v  # noqa: E402

from libs.verumdoc_rules import REGISTRO  # noqa: E402
from libs.verumdoc_rules.impostazioni import Livello  # noqa: E402
from libs.verumdoc_rules.lettura import prepara_ambiente  # noqa: E402
from libs.verumdoc_rules.validatore import esporta_profilo  # noqa: E402

ADESSO = datetime(2026, 10, 23, 10, tzinfo=UTC)
PDF = b"%PDF-1.7\n" + b"x" * 3000


def _indice(nome: str = "fattura.pdf", impronta: str | None = None, **extra: Any) -> dict:
    return {"classe_documentale": "fattura", "documenti": [{
        "id": "d1", "tipo": "PRINCIPALE", "nome_file": nome, "formato": "application/pdf",
        "hash": {"impronta": impronta or hashlib.sha256(PDF).hexdigest(), "algoritmo": "SHA-256"},
        "parametri": {"identificativo_documento": "X", "modalita_formazione": "a",
                      "tipologia_documentale": "fattura", "data_documento": "2026-10-01",
                      "soggetto_produttore_id": "12345678901",
                      "soggetto_produttore_nome": "Acme", "oggetto": "Oggetto di prova",
                      **extra}}]}


def _profilo(valori_classe: dict[str, Any]) -> dict[str, Any]:
    ambiente = prepara_ambiente(registro=REGISTRO, livelli={Livello.CLASSE: valori_classe},
                                adesso=ADESSO, tenant_id="t", classe_id="c")
    return esporta_profilo(ambiente, classe="fattura", ente="t", adesso=ADESSO)


@pytest.fixture
def cartella(tmp_path: Path) -> Path:
    (tmp_path / "fattura.pdf").write_bytes(PDF)
    (tmp_path / "profilo.json").write_text(json.dumps(_profilo({
        "FORMATI_AMMESSI": ["application/pdf"], "CLASSE_DIMENSIONE_MAX_MB": 1})))
    return tmp_path


def _esegui(cartella: Path, indice: dict, *altri: str, capsys) -> tuple[int, dict]:
    (cartella / "indice.json").write_text(json.dumps(indice))
    codice = v.main([str(cartella / "indice.json"), "--profilo", str(cartella / "profilo.json"),
                     "--json", *altri])
    return codice, json.loads(capsys.readouterr().out)


def test_conforme_con_file_e_impronta_calcolata(cartella, capsys) -> None:
    codice, esito = _esegui(cartella, _indice(), "--file", str(cartella / "fattura.pdf"),
                            capsys=capsys)
    assert codice == 0 and esito["esito"] == "CONFORME" and esito["rilievi"] == []
    assert "regole_file" in esito["fasi_eseguite"]
    assert esito["regole_applicate"]["impostazioni"]["FORMATI_AMMESSI"]["origine"] == "CLASSE"


def test_non_conforme_stessi_codici_del_versamento(cartella, capsys) -> None:
    codice, esito = _esegui(cartella, _indice(impronta="00" * 32, oggetto=None),
                            "--file", str(cartella / "fattura.pdf"), capsys=capsys)
    assert codice == 1
    assert [r["codice"] for r in esito["rilievi"]] == ["VRS-102", "RGL-101"]
    codice, esito = _esegui(cartella, {**_indice(), "classe_documentale": "altra"},
                            capsys=capsys)
    assert codice == 1 and esito["rilievi"][0]["codice"] == "VRS-105"
    codice, esito = _esegui(cartella, {**_indice(), "override": {
        "motivo": "corto", "valori": {}}}, capsys=capsys)
    assert codice == 1 and esito["rilievi"][0]["codice"] == "RGL-111"


def test_file_grande_letto_a_blocchi_e_limite_della_classe(cartella, capsys, monkeypatch) -> None:
    grande = cartella / "fattura.pdf"
    grande.write_bytes(PDF + b"0" * (2 * 1024 * 1024))
    monkeypatch.setattr(v, "BLOCCO", 64 * 1024)
    codice, esito = _esegui(cartella, _indice(
        impronta=hashlib.sha256(grande.read_bytes()).hexdigest()), "--file", str(grande),
        capsys=capsys)
    assert codice == 1 and [r["codice"] for r in esito["rilievi"]] == ["RGL-104"]


def test_errori_d_uso_e_profilo_illeggibile(cartella, capsys) -> None:
    (cartella / "profilo.json").write_text(json.dumps({"formato": "altro"}))
    (cartella / "indice.json").write_text(json.dumps(_indice()))
    assert v.main([str(cartella / "indice.json"), "--profilo",
                   str(cartella / "profilo.json")]) == 2
    with pytest.raises(SystemExit):
        v.main([str(cartella / "indice.json")])  # né --profilo né --url


def test_uscita_testuale_leggibile(cartella, capsys) -> None:
    (cartella / "indice.json").write_text(json.dumps(_indice(nome="x.png")))
    assert v.main([str(cartella / "indice.json"), "--profilo",
                   str(cartella / "profilo.json")]) == 1
    uscita = capsys.readouterr().out
    assert "Esito: NON_CONFORME" in uscita and "SEC-006" in uscita
    assert "Non verificato qui" in uscita


# ── confronta_regole ────────────────────────────────────────────────

def test_variante_da_file_valida_i_valori() -> None:
    variante = cr.variante_da_file({"livello": "ENTE", "valori": {
        "CLASSE_DIMENSIONE_MAX_MB": "5"}, "togli": ["FORMATI_AMMESSI"]}, ente="t", classe=None)
    assert variante.valori == {"CLASSE_DIMENSIONE_MAX_MB": 5} and variante.ente == "t"
    with pytest.raises(ValueError):
        cr.variante_da_file({"valori": {"CLASSE_DIMENSIONE_MAX_MB": "molti"}}, ente=None,
                            classe=None)


PG = os.environ.get("VD_TEST_DATABASE_URL", "")


@pytest.mark.skipif(not PG, reason="VD_TEST_DATABASE_URL non impostata")
def test_confronto_su_postgresql_in_sola_lettura(tmp_path, capsys) -> None:
    import asyncpg

    template = PG.rsplit("/", 1)[1]
    nome = f"vdt_{uuid.uuid4().hex[:12]}"
    base = PG.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0]

    async def prepara() -> None:
        admin = await asyncpg.connect(f"{base}/postgres")
        await admin.execute(f'CREATE DATABASE "{nome}" TEMPLATE "{template}"')
        await admin.close()
        conn = await asyncpg.connect(f"{base}/{nome}")
        tid, cid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await conn.execute("INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                           "schema_name, stato, email_referente) VALUES ($1, $2, 'E', $3, "
                           "'ATTIVO', 'a@b.it')", tid, tid.hex[:11], f"t_{tid.hex[:20]}")
        await conn.execute("INSERT INTO public.classi_documentali (id, tenant_id, codice, nome) "
                           "VALUES ($1, $2, 'fattura', 'fattura')", cid, tid)
        for formato in ("image/png", "application/pdf"):
            meta = {"indice": _indice(), "docs_s3_keys": [{
                "nome_file": "fattura.pdf", "size_bytes": 10, "formato_rilevato": formato}]}
            await conn.execute("INSERT INTO public.pdv (oggetto, classe_doc_id, user_id, "
                               "tenant_id, metadata_json) VALUES ('p', $1, $2, $3, "
                               "CAST($4 AS jsonb))", cid, uid, tid, json.dumps(meta))
        await conn.close()

    async def elimina() -> None:
        admin = await asyncpg.connect(f"{base}/postgres")
        await admin.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
        await admin.close()

    asyncio.run(prepara())
    try:
        modifica = tmp_path / "modifica.json"
        modifica.write_text(json.dumps({"livello": "CONSERVATORE",
                                        "valori": {"FORMATI_AMMESSI": ["application/pdf"]}}))
        url = PG.rsplit("/", 1)[0] + f"/{nome}"
        codice = cr.main(["--database-url", url, "--modifiche", str(modifica)])
        risultato = json.loads(capsys.readouterr().out)
        assert codice == 1  # differenze da approvare
        assert risultato["esito"]["rifiutati_dopo"] == 1 and risultato["differenze"] == 1
        assert risultato["campione"]["letti"] == 2
        assert risultato["campione"]["metodo"] == "ultimi PdV di tutti gli enti"
        nessuna = tmp_path / "nessuna.json"
        nessuna.write_text(json.dumps({"livello": "CONSERVATORE",
                                       "valori": {"CLASSE_DIMENSIONE_MAX_MB": 100}}))
        assert cr.main(["--database-url", url, "--modifiche", str(nessuna)]) == 0
    finally:
        asyncio.run(elimina())
