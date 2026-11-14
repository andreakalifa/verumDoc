# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R08 (D01) — il rapporto di verifica delle firme salvato dalla verifica sul PdV
è conservato nel PdA (WORM con retention), la sua impronta entra nel manifest e
nella riga del PdA (citata dal RdV). PostgreSQL vero e S3 con Object Lock.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from app.config import settings
from app.services.aip_engine import NOME_RAPPORTO_FIRME, costruisci_pda, rapporto_verifica_firme

from libs.catena.canonico import json_canonico
from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.test_integrazione_pda import ADESSO, _pdv_validato, s3  # noqa: F401 — fixture

RAPPORTO = {"versione": 1, "livello": "CRITTOGRAFICA", "verificatore": "pyhanko 0.38.0",
            "file": [{"documento": "d1", "nome_file": "doc.pdf", "esito_in_parole": "valida",
                      "esito": {"formato": "PAdES", "indicazione": "TOTAL_PASSED", "firme": []}}]}


def test_lettura_del_rapporto_dalla_verifica() -> None:
    verifica = {"passi": [{"nome": "verifica_firme", "dettagli": {"rapporto": RAPPORTO}}]}
    assert rapporto_verifica_firme(verifica) == RAPPORTO
    assert rapporto_verifica_firme(json.dumps(verifica)) == RAPPORTO
    assert rapporto_verifica_firme({"passi": [{"nome": "verifica_firme",
                                               "dettagli": {"livello": "NESSUNA"}}]}) is None
    assert rapporto_verifica_firme(None) is None


@pytest.mark.integration
@richiede_pg
async def test_rapporto_conservato_nel_pda_e_nel_manifest(db_url, s3):  # noqa: F811
    evento = await _pdv_validato(db_url, s3)
    await sql(db_url, "UPDATE public.pdv SET metadata_json = jsonb_build_object('verifica', "
                      "CAST($2 AS jsonb)) WHERE id = $1", uuid.UUID(evento["pdv_id"]),
              json.dumps({"passi": [{"nome": "verifica_firme", "dettagli": {
                  "rapporto": RAPPORTO}}]}))
    uscita = await costruisci_pda(evento, adesso=ADESSO)
    riga = (await sql(db_url, "SELECT rapporto_firme_key, rapporto_firme_sha256, s3_key "
                              "FROM public.aip WHERE id = $1", uuid.UUID(uscita["aip_id"])))[0]
    atteso = hashlib.sha256(json_canonico(RAPPORTO)).hexdigest()
    assert riga["rapporto_firme_sha256"] == atteso
    assert riga["rapporto_firme_key"].endswith(NOME_RAPPORTO_FIRME)
    oggetto = s3.get_object(Bucket=settings.s3_bucket_aip, Key=riga["rapporto_firme_key"])
    assert oggetto["ObjectLockMode"] == "COMPLIANCE"
    assert hashlib.sha256(oggetto["Body"].read()).hexdigest() == atteso
    manifest = json.loads(s3.get_object(Bucket=settings.s3_bucket_aip,
                                        Key=riga["s3_key"])["Body"].read())
    assert manifest["rapporto_verifica_firme"]["sha256"] == atteso
    assert manifest["rapporto_verifica_firme"]["livello"] == "CRITTOGRAFICA"


@pytest.mark.integration
@richiede_pg
async def test_senza_rapporto_nessuna_colonna(db_url, s3):  # noqa: F811
    evento = await _pdv_validato(db_url, s3)
    uscita = await costruisci_pda(evento, adesso=ADESSO)
    riga = (await sql(db_url, "SELECT rapporto_firme_key, rapporto_firme_sha256 FROM "
                              "public.aip WHERE id = $1", uuid.UUID(uscita["aip_id"])))[0]
    assert riga["rapporto_firme_key"] is None and riga["rapporto_firme_sha256"] is None
