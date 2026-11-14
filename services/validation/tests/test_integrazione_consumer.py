# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Integrazione su PostgreSQL vero (migrato): la verifica SCRIVE lo stato del PdV
(VD-C-012), è fail-closed se lo storage non risponde (VD-C-008) e registra
DLQ + stato ERRORE dopo i ritentativi (VD-C-009).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass

import pytest
from app.services import consumer as modulo
from app.services.consumer import elabora_pdv
from app.validators.base import ErroreVerificaTransitorio

from libs.shared.kafka.consumatore_affidabile import EsitoElaborazione
from tests.infra_reale import (
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)
from tests.lettore_memoria import LettoreMemoria

pytestmark = [pytest.mark.integration, richiede_pg]

PDF = b"%PDF-1.7 documento di prova"


class _Kafka:
    def __init__(self):
        self.eventi: list[tuple[str, str, dict]] = []

    async def __call__(self, topic: str, chiave: str, valore: dict) -> None:
        self.eventi.append((topic, chiave, valore))


async def _pdv(url: str, *, classe_codice: str = "fattura", impronta: str | None = None,
               stato: str = "RICEVUTO") -> dict:
    tenant, classe = await crea_tenant_e_classe(url)
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'prova', $2, $3, $4, $5)", pdv, stato, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
                   "impronta_richiesta) VALUES ($1, $2, $3, $3)", pdv, tenant, "a" * 64)
    return {
        "pdv_id": str(pdv), "tenant_id": str(tenant),
        "indice": {"classe_documentale": classe_codice, "chiave": None, "documenti": [{
            "id": "d1", "tipo": "PRINCIPALE", "nome_file": "doc.pdf", "formato": "application/pdf",
            "hash": {
                "algoritmo": "SHA-512", "impronta": impronta or hashlib.sha512(PDF).hexdigest()},
            "parametri": {
                "identificativo_documento": "FT-1", "modalita_formazione": "a",
                "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
                "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme S.r.l.",
                "oggetto": "Fattura di prova del servizio",
            },
        }]},
        "docs_s3_keys": [{"doc_id": "d1", "nome_file": "doc.pdf", "s3_key": "k", "s3_bucket": "b"}],
        "allegato5": {},
    }


def _lettore_ok() -> LettoreMemoria:
    return LettoreMemoria({("b", "k"): PDF})


async def _stato(url: str, pdv_id: str) -> dict:
    riga = (await sql(url, "SELECT stato::text AS stato, errori_validazione, metadata_json "
                           "FROM public.pdv WHERE id = $1", uuid.UUID(pdv_id)))[0]
    return {"stato": riga["stato"], "errori": json.loads(riga["errori_validazione"]),
            "meta": json.loads(riga["metadata_json"])}


async def test_elabora_pdv_valido_scrive_validato_e_pubblica(db_url):
    evento = await _pdv(db_url)
    kafka = _Kafka()
    await elabora_pdv(evento, lettore=_lettore_ok(), pubblica=kafka)
    stato = await _stato(db_url, evento["pdv_id"])
    assert stato["stato"] == "VALIDATO"
    assert stato["meta"]["verifica"]["esito"] == "VALIDATO"
    assert stato["meta"]["verifica"]["steps_totali"] == 11  # R08: passi 10 e 11
    assert [e[0] for e in kafka.eventi] == ["pdv.validated"]


async def test_elabora_pdv_impronta_falsa_scrive_errore_con_motivo(db_url):
    evento = await _pdv(db_url, impronta="ab" * 64)
    kafka = _Kafka()
    await elabora_pdv(evento, lettore=_lettore_ok(), pubblica=kafka)
    stato = await _stato(db_url, evento["pdv_id"])
    assert stato["stato"] == "ERRORE"
    assert stato["errori"][0]["codice"] == "VLD-501"
    assert kafka.eventi[0][0] == "pdv.invalid"
    risultato = kafka.eventi[0][2]["risultati"][-1]
    assert risultato["valido"] is False and risultato["msg"]  # chiavi lette da notification
    attivo = await sql(db_url, "SELECT attivo FROM public.versamento_impronte WHERE pdv_id = $1",
                       uuid.UUID(evento["pdv_id"]))
    assert attivo[0]["attivo"] is False  # un PdV rifiutato non blocca un nuovo versamento


async def test_elabora_pdv_classe_sconosciuta_rifiutata(db_url):
    evento = await _pdv(db_url, classe_codice="inventata_xyz")
    await elabora_pdv(evento, lettore=_lettore_ok(), pubblica=_Kafka())
    stato = await _stato(db_url, evento["pdv_id"])
    assert stato["stato"] == "ERRORE" and stato["errori"][0]["codice"] == "VLD-703"


async def test_elabora_pdv_storage_giu_nessun_esito(db_url):
    """VD-C-008: S3 non risponde → errore transitorio, il PdV NON diventa VALIDATO."""
    evento = await _pdv(db_url)

    kafka = _Kafka()
    with pytest.raises(ErroreVerificaTransitorio):  # oggetto assente nello storage
        await elabora_pdv(evento, lettore=LettoreMemoria({}), pubblica=kafka)
    assert (await _stato(db_url, evento["pdv_id"]))["stato"] == "IN_VALIDAZIONE"
    assert kafka.eventi == []


async def test_elabora_pdv_riconsegna_ripubblica_esito_salvato(db_url):
    """Crash fra commit ed evento: alla riconsegna si ripubblica l'esito salvato, senza rifare."""
    evento = await _pdv(db_url)

    async def kafka_giu(*_a):
        raise ConnectionError("Kafka giù")

    with pytest.raises(ConnectionError):
        await elabora_pdv(evento, lettore=_lettore_ok(), pubblica=kafka_giu)
    assert (await _stato(db_url, evento["pdv_id"]))["stato"] == "VALIDATO"

    class LettoreVietato:
        async def dimensione(self, rif):
            raise AssertionError("non deve riverificare")

        async def leggi(self, rif, **_):
            raise AssertionError("non deve riverificare")

    kafka = _Kafka()
    await elabora_pdv(evento, lettore=LettoreVietato(), pubblica=kafka)
    assert [e[0] for e in kafka.eventi] == ["pdv.validated"]


@dataclass
class _Msg:
    topic: str
    partition: int
    offset: int
    value: dict


async def test_registra_fallimento_dlq_e_stato_errore(db_url, monkeypatch):
    """Tentativi esauriti: DLQ con motivo strutturato e PdV ERRORE esplicito."""
    evento = await _pdv(db_url)
    kafka = _Kafka()
    monkeypatch.setattr(modulo, "_pubblica", kafka)
    esito = EsitoElaborazione(False, 4, ConnectionError("S3 giù"))
    await modulo.registra_fallimento(_Msg("pdv.received", 0, 5, evento), esito)
    assert kafka.eventi[0][0] == "pdv.received.dlq"
    assert kafka.eventi[0][2]["motivo"]["tipo"] == "tecnico"
    stato = await _stato(db_url, evento["pdv_id"])
    assert stato["stato"] == "ERRORE"
    assert stato["errori"][-1]["fase"] == "verifica" and stato["errori"][-1]["tentativi"] == 4


# ── R03 (b5): regole fotografate nel PdV, lettura in streaming ─────────

async def _con_regole(url: str, evento: dict, regole) -> None:
    await sql(url, "UPDATE public.pdv SET regole_applicate = CAST($2 AS jsonb) WHERE id = $1",
              uuid.UUID(evento["pdv_id"]), json.dumps(regole))


async def test_limite_della_classe_fotografato_file_non_scaricato(db_url):
    """Il limite per file è quello fotografato al versamento (qui 1 MB), non 100 MB fissi;
    il file oltre il limite non si scarica e il PdV è rifiutato con VLD-801."""
    grande = b"%PDF-1.7 " + b"x" * (2 * 1024 * 1024)
    evento = await _pdv(db_url, impronta=hashlib.sha512(grande).hexdigest())
    await _con_regole(db_url, evento, {"catalogo_regole": "1", "impostazioni": {
        "CLASSE_DIMENSIONE_MAX_MB": {"valore": 1, "origine": "CLASSE"},
        "REGOLA.RGL-104.EFFETTO": {"valore": "BLOCCA", "origine": "PREDEFINITO"}}})
    lettore = LettoreMemoria({("b", "k"): grande})
    await elabora_pdv(evento, lettore=lettore, pubblica=_Kafka())
    stato = await _stato(db_url, evento["pdv_id"])
    assert stato["stato"] == "ERRORE" and stato["errori"][0]["codice"] == "VLD-801"
    assert stato["meta"]["verifica"]["regole"]["limite_byte_file"] == 1024 * 1024
    assert stato["meta"]["verifica"]["regole"]["origine_limite"] == "CLASSE"
    assert lettore.letture == []


async def test_file_oltre_100mb_ammesso_dalla_classe_validato(db_url):
    """Prima: VLD-801 fisso a 100 MB rifiutava in asincrono i file delle sessioni di
    caricamento. Ora vale la classe (qui 500 MB): il PdV è VALIDATO."""
    from app.services.lettura_file import FileLetto, calcolatori

    dimensione, mib = 101 * 1024 * 1024, b"\0" * (1024 * 1024)
    attesa = hashlib.sha512()
    for _ in range(101):
        attesa.update(mib)
    evento = await _pdv(db_url, impronta=attesa.hexdigest())
    await _con_regole(db_url, evento, {"catalogo_regole": "1", "impostazioni": {
        "CLASSE_DIMENSIONE_MAX_MB": {"valore": 500, "origine": "CLASSE"}}})

    class LettoreZeri:
        async def dimensione(self, rif):
            return dimensione

        async def leggi(self, rif, *, algoritmi, limite_byte=None):
            calc = calcolatori(algoritmi)
            for _ in range(101):
                for c in calc.values():
                    c.update(mib)
            return FileLetto(dimensione, {n: c.hexdigest() for n, c in calc.items()})

        async def copia_in(self, rif, destinazione, *, limite_byte):
            # R08: la copia locale per formato e firme rispetta il suo limite
            from app.services.lettura_file import FileOltreLimite
            if dimensione > limite_byte:
                raise FileOltreLimite(limite_byte, dimensione)
            with destinazione.open("wb") as f:
                for _ in range(101):
                    f.write(mib)
            return dimensione

    await elabora_pdv(evento, lettore=LettoreZeri(), pubblica=_Kafka())
    assert (await _stato(db_url, evento["pdv_id"]))["stato"] == "VALIDATO"


async def test_regole_illeggibili_errore_permanente_vld_802(db_url):
    from libs.shared.kafka.consumatore_affidabile import ErrorePermanente

    evento = await _pdv(db_url)
    await _con_regole(db_url, evento, {"impostazioni": {
        "CLASSE_DIMENSIONE_MAX_MB": {"valore": "cento", "origine": "CLASSE"}}})
    with pytest.raises(ErrorePermanente) as exc:
        await elabora_pdv(evento, lettore=_lettore_ok(), pubblica=_Kafka())
    assert exc.value.codice == "VLD-802"
    assert (await _stato(db_url, evento["pdv_id"]))["stato"] == "RICEVUTO"  # nulla scritto


async def test_ciclo_parallelo_su_db_reale_piu_pdv(db_url):
    """Più PdV in parallelo col ciclo del servizio: tutti VALIDATI, offset confermato
    fino all'ultimo messaggio (offset dopo l'esito persistito)."""
    from aiokafka.structs import TopicPartition

    eventi = [await _pdv(db_url) for _ in range(6)]
    commit: list[int] = []

    class Consumer:
        def __init__(self):
            self.lotti = [{TopicPartition("pdv.received", 0): [
                _Msg("pdv.received", 0, i, e) for i, e in enumerate(eventi)]}]

        async def getmany(self, *, timeout_ms, max_records):
            await asyncio.sleep(0.005)
            return self.lotti.pop(0) if self.lotti else {}

        async def commit(self, offsets):
            commit.extend(offsets.values())

    async def gestore(msg):
        await elabora_pdv(msg.value, lettore=_lettore_ok(), pubblica=_Kafka())

    ciclo = modulo.crea_ciclo(Consumer())
    ciclo.gestore = gestore
    fine = asyncio.Event()
    compito = asyncio.create_task(ciclo.esegui(fine))
    async with asyncio.timeout(20):
        while not commit or commit[-1] != 6:
            await asyncio.sleep(0.01)
    fine.set()
    await compito
    for e in eventi:
        assert (await _stato(db_url, e["pdv_id"]))["stato"] == "VALIDATO"
    assert commit == sorted(commit)
