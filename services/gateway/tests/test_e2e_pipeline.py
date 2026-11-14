# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
End-to-end del flusso normativo versamento → verifica → PdA → catena → RdV
(VD-C-012, REGOLE §11.2 e §23.6): un percorso reale porta il PdV a CONSERVATO
e ``/rdv`` restituisce il rapporto di versamento definitivo.

R04 (VD-C-097, VD-C-096): l'aip-builder non pubblica più alcun evento
«conservato»; il PdA entra nel registro a due livelli tramite la coda, un giro
del ciclo della hashchain lo aggiunge al registro, emette il checkpoint firmato,
registra lo stato del sigillo (NON_FIRMATO: nessun fornitore) e solo allora
porta il PdV a CONSERVATO con il RdV v2 e l'evento ``aip.conserved`` in outbox.

Ogni servizio gira nel proprio processo (stessa base dati PostgreSQL migrata,
stesso S3 moto); gli eventi Kafka si passano a mano fra i passi: il broker non
è parte di questa prova (i cicli Kafka hanno i loro test).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from app.auth.dependencies import get_current_user
from app.config import settings
from app.main import app
from app.models.versamento import ModalitaFormazione
from httpx import ASGITransport, AsyncClient

from tests.identita_prova import dipendenza_utente
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, pytest.mark.e2e, richiede_pg]

RADICE = Path(__file__).resolve().parents[3]
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"

_VALIDATION = """
import asyncio, json, os, sys
from libs.shared.db import init_db
from app.services.consumer import elabora_pdv
async def main():
    await init_db(os.environ["DATABASE_URL"])
    evento = json.load(open(sys.argv[1]))
    uscita = []
    async def pubblica(topic, chiave, valore):
        uscita.append({"topic": topic, "valore": valore})
    await elabora_pdv(evento, pubblica=pubblica)
    json.dump(uscita, open(sys.argv[2], "w"))
asyncio.run(main())
"""

_AIP_BUILDER = """
import asyncio, json, os, sys
from libs.shared.db import init_db
from app.services.aip_engine import costruisci_pda
async def main():
    await init_db(os.environ["DATABASE_URL"])
    evento = json.load(open(sys.argv[1]))
    uscita = await costruisci_pda(evento)
    json.dump(uscita, open(sys.argv[2], "w"))
asyncio.run(main())
"""

# Un giro del ciclo del registro con le dipendenze reali: servizio di firma
# senza credenziali (sigillo «NON_FIRMATO» dichiarato), nessun testimone.
_HASHCHAIN = """
import asyncio, json, os, sys
from libs.shared.db import init_db
from app.registro.ciclo import ciclo_predefinito
async def main():
    await init_db(os.environ["DATABASE_URL"])
    await ciclo_predefinito().giro()
    json.dump({"fatto": True}, open(sys.argv[2], "w"))
asyncio.run(main())
"""


def _esegui(servizio: str, codice: str, ingresso: dict, tmp: Path, ambiente: dict) -> object:
    file_in, file_out = tmp / f"{servizio}-in.json", tmp / f"{servizio}-out.json"
    file_in.write_text(json.dumps(ingresso))
    cartella = RADICE / "services" / servizio
    env = {**os.environ, **ambiente,
           "PYTHONPATH": os.pathsep.join([str(RADICE), str(cartella)])}
    esito = subprocess.run(
        [sys.executable, "-c", codice, str(file_in), str(file_out)],
        cwd=cartella, env=env, capture_output=True, text=True, timeout=120,
    )
    assert esito.returncode == 0, f"{servizio}: {esito.stderr[-3000:]}"
    return json.loads(file_out.read_text())


async def test_versamento_fino_a_conservato_e_rdv(db_url, s3_server, monkeypatch, tmp_path):
    bucket_pdv, bucket_aip = f"pdv-{uuid.uuid4().hex[:8]}", f"aip-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_pdv", bucket_pdv)
    from libs.catena import nota

    chiave_registro, verificatore_registro = nota.genera_chiave("verumdoc-registro")
    ambiente = {
        "DATABASE_URL": db_url, "ENV": "development", "S3_ENDPOINT_URL": s3_server,
        "S3_ACCESS_KEY": "test", "S3_SECRET_KEY": "test", "S3_REGION": "us-east-1",
        "S3_BUCKET_AIP": bucket_aip, "CATENA_CHIAVE_PRIVATA": chiave_registro,
        "RETENTION_PERMANENTE_ORIZZONTE_ANNI": "30", "SIGNATURE_SERVICE_TOKEN": "",
        "TESTIMONE_URL": "",
    }
    tenant, _ = await crea_tenant_e_classe(db_url)
    app.dependency_overrides[get_current_user] = dipendenza_utente({
        "tenant_id": str(tenant), "user_id": str(uuid.uuid4()), "ruolo": "OPERATORE",
        "scopes": ["versamento:ud", "read"]})
    try:
        import hashlib
        indice = {
            "classe_documentale": "fattura",
            "chiave": {"numero": "FT-E2E-1", "anno": "2026", "registro": "FATTURE"},
            "documenti": [{
                "id": "doc-1", "tipo": "PRINCIPALE", "nome_file": "fattura.pdf",
                "formato": "application/pdf",
                "hash": {"algoritmo": "SHA-512", "impronta": hashlib.sha512(PDF).hexdigest()},
                "parametri": {
                    "identificativo_documento": "FT-E2E-1",
                    "modalita_formazione": ModalitaFormazione.ACQUISIZIONE.value,
                    "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
                    "soggetto_produttore_id": "12345678901",
                    "soggetto_produttore_nome": "Acme S.r.l.",
                    "oggetto": "Fattura di prova end-to-end",
                },
            }],
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/versamento/ud", data={"indice": json.dumps(indice)},
                files=[("files", ("fattura.pdf", PDF, "application/pdf"))],
                headers={"Idempotency-Key": "e2e-000000001"},
            )
            assert resp.status_code == 202, resp.text
            pdv_id = resp.json()["pdv_id"]
            assert (await client.get(f"/api/v1/versamento/{pdv_id}/rdv")).status_code == 409

            # L'evento pdv.received è nell'outbox (Kafka assente in questa prova)
            riga = (await sql(db_url, "SELECT payload FROM public.outbox_eventi "
                                      "WHERE chiave = $1", pdv_id))[0]
            ricevuto = json.loads(riga["payload"])

            verificato = _esegui("validation", _VALIDATION, ricevuto, tmp_path, ambiente)
            assert verificato[0]["topic"] == "pdv.validated", verificato
            costruito = _esegui("aip-builder", _AIP_BUILDER, verificato[0]["valore"], tmp_path,
                                ambiente)
            # VD-C-097: PdA su storage, nessun evento «conservato», PdV non ancora CONSERVATO
            assert costruito["stato"] == "SU_STORAGE" and costruito["radice_pda"]
            assert await sql(db_url, "SELECT 1 FROM public.outbox_eventi "
                                     "WHERE topic = 'aip.conserved'") == []
            assert (await client.get(f"/api/v1/versamento/{pdv_id}")).json()["stato"] != \
                "CONSERVATO"
            assert (await client.get(f"/api/v1/versamento/{pdv_id}/rdv")).status_code == 409

            _esegui("hashchain", _HASHCHAIN, {}, tmp_path, ambiente)

            stato = (await client.get(f"/api/v1/versamento/{pdv_id}")).json()
            assert stato["stato"] == "CONSERVATO"
            rdv = await client.get(f"/api/v1/versamento/{pdv_id}/rdv")
        assert rdv.status_code == 200
        assert rdv.headers["X-Formato-RdV"] == "verumdoc-rdv-definitivo-2"
        assert rdv.headers["X-Checkpoint-Dimensione"] == "1"
        q = "{https://schemi.verumdoc.it/rdv/definitivo/2}"
        radice = ET.fromstring(rdv.content)
        assert radice.get("tipo") == "DEFINITIVO" and radice.get("versione") == "2"
        assert radice.find(f"{q}PdV/{q}Identificativo").text == pdv_id
        documento = radice.find(f"{q}Documenti/{q}Documento")
        assert documento.find(f"{q}Identificativo").text == "doc-1"
        assert documento.find(f"{q}Impronta").text == hashlib.sha256(PDF).hexdigest()
        cp = radice.find(f"{q}Registro/{q}Checkpoint")
        assert cp.find(f"{q}SigilloQualificato").get("stato") == "NON_FIRMATO"
        assert cp.find(f"{q}Testimone").get("stato") == "NON_CONFIGURATO"

        # Verifica indipendente con libs/catena: nota firmata dalla chiave del registro,
        # foglia del PdA inclusa nel registro, documento incluso nel PdA
        from libs.catena.rfc6962 import da_esadecimale, hash_foglia, verifica_inclusione

        testo_nota = cp.find(f"{q}Nota").text
        assert [f.nome for f in nota.verifica(testo_nota, [verificatore_registro])] == \
            ["verumdoc-registro"]
        checkpoint = nota.analizza(testo_nota)
        assert checkpoint.origine == "verumdoc.it/registro/v1/development"
        foglia_reg = radice.find(f"{q}Registro/{q}Foglia").text
        prova_reg = radice.find(f"{q}Registro/{q}ProvaInclusione")
        assert verifica_inclusione(
            hash_foglia(foglia_reg.encode()), int(prova_reg.get("foglia_indice")),
            checkpoint.dimensione, da_esadecimale([h.text for h in prova_reg]), checkpoint.radice)
        radice_pda = radice.find(f"{q}PdA/{q}RadicePdA").text
        assert json.loads(foglia_reg)["radice_pda"] == radice_pda
        prova_doc = documento.find(f"{q}ProvaPdA")
        assert verifica_inclusione(
            hash_foglia(prova_doc.find(f"{q}Foglia").text.encode()),
            int(prova_doc.get("foglia_indice")), int(prova_doc.get("dimensione")),
            da_esadecimale([h.text for h in prova_doc.find(f"{q}Percorso")]),
            bytes.fromhex(radice_pda))

        # Evento «conservato» solo ora, con PdA, foglia e checkpoint (K1)
        eventi = await sql(db_url, "SELECT payload FROM public.outbox_eventi "
                                   "WHERE topic = 'aip.conserved'")
        assert len(eventi) == 1
        evento = json.loads(eventi[0]["payload"])
        assert (evento["fase"], evento["foglia_indice"], evento["checkpoint_dimensione"],
                evento["pdv_id"]) == ("CONSERVATO", 0, 1, pdv_id)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
