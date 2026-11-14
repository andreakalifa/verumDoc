# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 integrazione — end-to-end della catena delle impronte con il testimone VERO.

    versamento (gateway, HTTP) → validation → aip-builder (PdA su storage, albero
    K1, coda; nessun «conservato») → PdV NON ancora CONSERVATO → un giro del
    registro (hashchain: sequenziatore, checkpoint firmato, sigillo NON_FIRMATO,
    invio al testimone, completamento) → il testimone (app reale) controfirma →
    PdV CONSERVATO solo ora → RdV definitivo v2 con prove e checkpoint
    controfirmato → verifica indipendente con ``tools/verificatore`` (CLI, come la
    userebbe un ente) → verifica continua: un file del PdA alterato nello storage
    viene trovato (DIVERSO) con l'evento ``integrita.anomalia`` in outbox.

Come girano i pezzi:
  - gateway: app reale in questo processo (ASGI);
  - testimone: app REALE (router, dominio, repository, migrazioni proprie) servita
    da uvicorn come task di QUESTO event loop, così la hashchain la raggiunge in
    HTTP. Due vincoli tecnici: (1) un processo ha un solo motore di
    ``libs.shared.db``, quindi il testimone usa lo schema ``testimone`` dello
    stesso database di prova (ammesso da K3 in sviluppo: «uno schema testimone
    nello stesso cluster»), migrato con le SUE migrazioni; (2) il pacchetto
    ``app`` del testimone si carica con ``sys.modules`` separato e si rimette a
    posto quello del gateway. Il lifespan del testimone non gira (aprirebbe un
    secondo pool sullo stesso database): il contesto si carica con le stesse
    funzioni d'avvio (``carica_contesto``), controlli di configurazione compresi;
  - validation, aip-builder, hashchain e verifica continua: processi separati
    (pacchetti ``app`` distinti), avviati in modo asincrono così il testimone
    continua a rispondere mentre la hashchain lo chiama;
  - Kafka assente: gli eventi si passano fra i passi dall'outbox.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import socket
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from app.auth.dependencies import get_current_user
from app.config import settings
from app.main import app
from app.models.versamento import ModalitaFormazione
from httpx import ASGITransport, AsyncClient

from tests.identita_prova import dipendenza_utente
from tests.infra_reale import (  # noqa: F401 — fixture
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)

pytestmark = [pytest.mark.integration, pytest.mark.e2e, richiede_pg]

RADICE = Path(__file__).resolve().parents[3]
TESTIMONE = RADICE / "services" / "testimone"
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%% verumdoc r04 e2e\n%%EOF\n"
ORIGINE = "verumdoc.it/registro/v1/development"

_VALIDATION = """
import asyncio, json, os, sys
from libs.shared.db import init_db
from app.services.consumer import elabora_pdv
async def main():
    await init_db(os.environ["DATABASE_URL"])
    uscita = []
    async def pubblica(topic, chiave, valore):
        uscita.append({"topic": topic, "valore": valore})
    await elabora_pdv(json.load(open(sys.argv[1])), pubblica=pubblica)
    json.dump(uscita, open(sys.argv[2], "w"))
asyncio.run(main())
"""

_AIP_BUILDER = """
import asyncio, json, os, sys
from libs.shared.db import init_db
from app.services.aip_engine import costruisci_pda
async def main():
    await init_db(os.environ["DATABASE_URL"])
    json.dump(await costruisci_pda(json.load(open(sys.argv[1]))), open(sys.argv[2], "w"))
asyncio.run(main())
"""

# Un giro del registro con il client VERO del testimone (TESTIMONE_URL impostata)
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

# Verifica continua: semina, PdA reso «dovuto» e UN lotto (come --un-giro)
_VERIFICA = """
import asyncio, json, os, sys
from sqlalchemy import text
from libs.shared.db import get_async_session, init_db
from app.verifica_continua.__main__ import client_s3
from app.verifica_continua.config import impostazioni
from app.verifica_continua.foglie import FonteFoglieCatena
from app.verifica_continua.lavoratore import Lavoratore
async def main():
    await init_db(os.environ["DATABASE_URL"])
    lav = Lavoratore(impostazioni, client=client_s3(), fonte_foglie=FonteFoglieCatena(),
                     detentore="e2e")
    await lav.manutenzione(forza=True)
    async with get_async_session() as s:
        await s.execute(text("UPDATE public.verifiche_programmazione "
                             "SET prossima_verifica = now() - interval '1 minute'"))
    presi = await lav.giro()
    json.dump({"presi": presi}, open(sys.argv[2], "w"))
asyncio.run(main())
"""


async def _esegui(servizio: str, codice: str, ingresso: dict, tmp: Path, ambiente: dict,
                  *, cartella: Path | None = None) -> object:
    """Processo del servizio avviato in modo ASINCRONO: l'event loop (e il
    testimone servito da uvicorn) resta libero mentre il processo lavora."""
    file_in, file_out = tmp / f"{servizio}-in.json", tmp / f"{servizio}-out.json"
    file_in.write_text(json.dumps(ingresso))
    cartella = cartella or RADICE / "services" / servizio
    env = {**os.environ, **ambiente,
           "PYTHONPATH": os.pathsep.join([str(RADICE), str(cartella)])}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", codice, str(file_in), str(file_out), cwd=cartella, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    assert proc.returncode == 0, f"{servizio}: {err.decode()[-3000:]}"
    return json.loads(file_out.read_text())


async def _migra_testimone(url: str) -> None:
    """Migrazioni PROPRIE del testimone (schema ``testimone``) sul database di prova."""
    env = {**os.environ, "DATABASE_URL": url, "ENV": "test",
           "PYTHONPATH": os.pathsep.join([str(RADICE), str(TESTIMONE)])}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "upgrade", "head", cwd=TESTIMONE, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    assert proc.returncode == 0, err.decode()[-3000:]
    # R05: in questa prova il testimone usa lo stesso engine dei servizi (login NON
    # superutente, tests/infra_reale.py); in esercizio ha database e login propri
    await sql(url, "GRANT USAGE ON SCHEMA testimone TO verumdoc_applicazione")
    await sql(url, "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA testimone "
                   "TO verumdoc_applicazione")
    await sql(url, "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA testimone "
                   "TO verumdoc_applicazione")


def _carica_testimone(ambiente: dict[str, str]):
    """Importa ``app.main`` del TESTIMONE con un ``sys.modules`` separato per il
    pacchetto ``app`` e rimette quello del gateway. Restituisce il modulo."""
    vecchi_env = {k: os.environ.get(k) for k in ambiente}
    os.environ.update(ambiente)
    gateway = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for n in gateway:
        del sys.modules[n]
    sys.path.insert(0, str(TESTIMONE))
    try:
        modulo = importlib.import_module("app.main")
    finally:
        for n in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            del sys.modules[n]
        sys.modules.update(gateway)
        sys.path.remove(str(TESTIMONE))
        for k, v in vecchi_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return modulo


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_versamento_checkpoint_testimone_verificatore_e_verifica_continua(
        db_url, s3_server, monkeypatch, tmp_path):
    import uvicorn

    from libs.catena import nota

    bucket_pdv, bucket_aip = f"pdv-{uuid.uuid4().hex[:8]}", f"aip-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_pdv", bucket_pdv)

    # ── chiavi e testimone reale ─────────────────────────────────────────
    chiave_registro, vk_registro = nota.genera_chiave("verumdoc-registro")
    chiave_testimone, vk_testimone = nota.genera_chiave("verumdoc-testimone")
    token_registro = "token-registro-e2e-" + uuid.uuid4().hex
    await _migra_testimone(db_url)
    testimone = _carica_testimone({
        "ENV": "test", "DATABASE_URL": db_url,
        "TESTIMONE_CHIAVE_PRIVATA": chiave_testimone,
        "TESTIMONE_CHIAVI_REGISTRO": vk_registro,
        "TESTIMONE_ORIGINI_AMMESSE": ORIGINE,
        "TESTIMONE_IMPRONTE_TOKEN_REGISTRO": hashlib.sha256(token_registro.encode()).hexdigest(),
    })
    app_t = testimone.app
    app_t.state.testimone = testimone.carica_contesto(
        testimone.ConfigurazioneAvvio.da_impostazioni(testimone.settings),
        app_t.state.metriche_testimone)
    porta = _porta_libera()
    server = uvicorn.Server(uvicorn.Config(app_t, host="127.0.0.1", port=porta,
                                           lifespan="off", log_level="warning"))
    servizio_testimone = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "il testimone non è partito"
    url_testimone = f"http://127.0.0.1:{porta}"

    ambiente = {
        "DATABASE_URL": db_url, "ENV": "development", "S3_ENDPOINT_URL": s3_server,
        "S3_ACCESS_KEY": "test", "S3_SECRET_KEY": "test", "S3_REGION": "us-east-1",
        "S3_BUCKET_AIP": bucket_aip, "CATENA_CHIAVE_PRIVATA": chiave_registro,
        "RETENTION_PERMANENTE_ORIZZONTE_ANNI": "30", "SIGNATURE_SERVICE_TOKEN": "",
        "TESTIMONE_URL": url_testimone, "TESTIMONE_VERIFICATORE": vk_testimone,
        "TESTIMONE_SERVICE_TOKEN": token_registro, "VERIFICA_METRICHE_PORTA": "0",
    }
    tenant, _ = await crea_tenant_e_classe(db_url)
    app.dependency_overrides[get_current_user] = dipendenza_utente({
        "tenant_id": str(tenant), "user_id": str(uuid.uuid4()), "ruolo": "OPERATORE",
        "scopes": ["versamento:ud", "read"]})
    try:
        indice = {
            "classe_documentale": "fattura",
            "chiave": {"numero": "FT-R04-1", "anno": "2026", "registro": "FATTURE"},
            "documenti": [{
                "id": "doc-1", "tipo": "PRINCIPALE", "nome_file": "fattura.pdf",
                "formato": "application/pdf",
                "hash": {"algoritmo": "SHA-512", "impronta": hashlib.sha512(PDF).hexdigest()},
                "parametri": {
                    "identificativo_documento": "FT-R04-1",
                    "modalita_formazione": ModalitaFormazione.ACQUISIZIONE.value,
                    "tipologia_documentale": "fattura", "data_documento": "2026-10-23",
                    "soggetto_produttore_id": "12345678901",
                    "soggetto_produttore_nome": "Acme S.r.l.",
                    "oggetto": "Fattura di prova della catena R04",
                },
            }],
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # ── 1. versamento ────────────────────────────────────────────
            resp = await client.post(
                "/api/v1/versamento/ud", data={"indice": json.dumps(indice)},
                files=[("files", ("fattura.pdf", PDF, "application/pdf"))],
                headers={"Idempotency-Key": "e2e-r04-catena-0001"})
            assert resp.status_code == 202, resp.text
            pdv_id = resp.json()["pdv_id"]
            ricevuto = json.loads((await sql(
                db_url, "SELECT payload FROM public.outbox_eventi WHERE chiave = $1 "
                        "AND topic = 'pdv.received'", pdv_id))[0]["payload"])

            # ── 2. validation e aip-builder: PdA su storage, NON conservato ──
            verificato = await _esegui("validation", _VALIDATION, ricevuto, tmp_path, ambiente)
            assert verificato[0]["topic"] == "pdv.validated", verificato
            costruito = await _esegui("aip-builder", _AIP_BUILDER, verificato[0]["valore"],
                                      tmp_path, ambiente)
            assert costruito["stato"] == "SU_STORAGE" and costruito["radice_pda"]
            assert (await client.get(f"/api/v1/versamento/{pdv_id}")).json()["stato"] != \
                "CONSERVATO"
            assert (await client.get(f"/api/v1/versamento/{pdv_id}/rdv")).status_code == 409
            assert await sql(db_url, "SELECT 1 FROM public.catena_checkpoint") == []

            # ── 3. un giro del registro: checkpoint, testimone, completamento ─
            await _esegui("hashchain", _HASHCHAIN, {}, tmp_path, ambiente)
            cp_db = await sql(db_url, "SELECT dimensione, stato_testimone, stato_sigillo "
                                      "FROM public.catena_checkpoint")
            assert [(c["dimensione"], c["stato_testimone"], c["stato_sigillo"]) for c in cp_db] \
                == [(1, "CONTROFIRMATO", "NON_FIRMATO")]
            stato = (await client.get(f"/api/v1/versamento/{pdv_id}")).json()
            assert stato["stato"] == "CONSERVATO"  # solo DOPO il checkpoint controfirmato
            rdv = await client.get(f"/api/v1/versamento/{pdv_id}/rdv")
        assert rdv.status_code == 200
        assert rdv.headers["X-Formato-RdV"] == "verumdoc-rdv-definitivo-2"
        assert rdv.headers["X-Checkpoint-Dimensione"] == "1"
        q = "{https://schemi.verumdoc.it/rdv/definitivo/2}"
        radice = ET.fromstring(rdv.content)
        assert radice.get("versione") == "2"
        cp = radice.find(f"{q}Registro/{q}Checkpoint")
        assert cp.find(f"{q}Testimone").get("stato") == "CONTROFIRMATO"
        assert cp.find(f"{q}SigilloQualificato").get("stato") == "NON_FIRMATO"
        firme = {f.nome for f in nota.verifica(cp.find(f"{q}Nota").text,
                                               [vk_registro, vk_testimone])}
        assert firme == {"verumdoc-registro", "verumdoc-testimone"}

        # ── 4. il testimone conserva e pubblica il checkpoint controfirmato ──
        async with httpx.AsyncClient(base_url=url_testimone) as ht:
            pubblicato = await ht.get("/api/v1/testimone/checkpoint",
                                      params={"origine": ORIGINE})
            stato_t = (await ht.get("/api/v1/testimone/stato",
                                    params={"origine": ORIGINE})).json()
        assert pubblicato.status_code == 200, pubblicato.text
        assert {f.nome for f in nota.verifica(pubblicato.text, [vk_registro, vk_testimone])} \
            == {"verumdoc-registro", "verumdoc-testimone"}
        assert (stato_t["stato"], stato_t["dimensione"]) == ("ATTIVO", 1)

        # ── 5. verifica indipendente con il verificatore pubblico (CLI) ──────
        (tmp_path / "rdv.xml").write_bytes(rdv.content)
        (tmp_path / "fattura.pdf").write_bytes(PDF)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(RADICE / "tools" / "verificatore" / "verifica_ricevuta.py"),
            "--rdv", str(tmp_path / "rdv.xml"), "--documento", str(tmp_path / "fattura.pdf"),
            "--chiave-registro", vk_registro, "--chiave-testimone", vk_testimone,
            "--origine", ORIGINE,  # R04 correzioni: origine attesa (qui di sviluppo)
            cwd=tmp_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        uscita, errore = await asyncio.wait_for(proc.communicate(), timeout=60)
        testo = uscita.decode()
        assert proc.returncode == 0, testo + errore.decode()
        assert "FALLITO" not in testo and "NON ESEGUITO" not in testo
        # un documento diverso NON passa
        (tmp_path / "falso.pdf").write_bytes(PDF + b"x")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(RADICE / "tools" / "verificatore" / "verifica_ricevuta.py"),
            "--rdv", str(tmp_path / "rdv.xml"), "--documento", str(tmp_path / "falso.pdf"),
            "--chiave-registro", vk_registro, "--chiave-testimone", vk_testimone,
            "--origine", ORIGINE,  # R04 correzioni: origine attesa (qui di sviluppo)
            cwd=tmp_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=60)
        assert proc.returncode == 1

        # ── 6. verifica continua: file alterato nello storage → DIVERSO ──────
        doc = (await sql(db_url, "SELECT d.id, d.s3_key FROM public.documenti d "
                                 "JOIN public.aip a ON a.id = d.aip_id "
                                 "WHERE a.pdv_id = $1", uuid.UUID(pdv_id)))[0]
        s3 = client_s3(s3_server)
        s3.put_object(Bucket=bucket_aip, Key=doc["s3_key"], Body=PDF.replace(b"r04", b"R04"),
                      ObjectLockMode="COMPLIANCE",
                      ObjectLockRetainUntilDate=datetime.now(UTC) + timedelta(days=3650 * 3))
        esito = await _esegui("verifica_continua", _VERIFICA, {}, tmp_path, ambiente,
                              cartella=RADICE / "services" / "hashchain")
        assert esito["presi"] == 1
        righe = await sql(db_url, "SELECT componente, documento_id, esito "
                                  "FROM public.verifiche_registro ORDER BY componente")
        per_componente = {(r["componente"], r["documento_id"]): r["esito"] for r in righe}
        assert per_componente[("FILE", doc["id"])] == "DIVERSO"
        anomalie = await sql(db_url, "SELECT payload FROM public.outbox_eventi "
                                     "WHERE topic = 'integrita.anomalia'")
        assert len(anomalie) == 1
        evento = json.loads(anomalie[0]["payload"])
        assert evento["esito"] == "DIVERSO" and evento["tenant_id"] == str(tenant)
        assert any(o["componente"] == "FILE" and o["esito"] == "DIVERSO"
                   for o in evento["oggetti"])
        assert "fattura.pdf" not in json.dumps(evento)  # nessun nome di file (§19.9)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        server.should_exit = True
        await asyncio.wait_for(servizio_testimone, timeout=10)
