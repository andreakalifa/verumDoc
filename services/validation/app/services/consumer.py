# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Consumer Kafka della verifica: consuma ``pdv.received``, verifica il PdV,
SCRIVE l'esito sul DB e pubblica ``pdv.validated`` o ``pdv.invalid``.

Servizio: validation. Riferimenti: VD-C-008, VD-C-009, VD-C-012 (R08/R09);
REGOLE_SVILUPPO §11.1 (stati), §11.6 (esito salvato), §12.2, §12.4.

Ciclo di stato del PdV scritto da questo servizio:
  RICEVUTO → IN_VALIDAZIONE → VALIDATO | ERRORE (rifiuto, errori in
  ``errori_validazione``; esito completo in ``metadata_json['verifica']``).
Errori tecnici (storage o DB irraggiungibili) NON sono rifiuti: si ritenta con
attese limitate, poi DLQ ``pdv.received.dlq`` e PdV in ERRORE con motivo
tecnico esplicito. L'offset si conferma solo dopo l'esito persistito.
Riconsegna di un messaggio già verificato: si ripubblica l'esito salvato.

R03 (b5, scalabilità): i file si leggono IN STREAMING dallo storage
(``lettura_file.LettoreS3``, nessun file intero in memoria); i limiti per file e
i formati vengono dalla fotografia delle regole del PdV (``pdv.regole_applicate``,
``regole_pdv``); più PdV si elaborano in parallelo (``CONCORRENZA_PDV``) con
conferma degli offset in ordine (``consumo_parallelo``).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRebalanceListener
from sqlalchemy import text

from app.config import settings
from app.services.consumo_parallelo import ConsumoParallelo
from app.services.lettura_file import LettoreFile, LettoreS3, riferimenti_da_evento
from app.services.metriche import METRICHE
from app.services.regole_pdv import RegoleNonLeggibili, regole_del_pdv
from app.services.validation_engine import run_validation
from app.validators.base import ValidationResult
from app.validators.steps import VALIDATION_PIPELINE
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_ente
from libs.shared.kafka.connessione import parametri_connessione
from libs.shared.kafka.consumatore_affidabile import (
    ErrorePermanente,
    EsitoElaborazione,
)

logger = structlog.get_logger(__name__)

_consumer: AIOKafkaConsumer | None = None
_producer: AIOKafkaProducer | None = None
_ciclo: ConsumoParallelo | None = None

STATI_GIA_DECISI = ("VALIDATO", "ERRORE")
STATI_SUCCESSIVI = ("IN_BUILD", "CONSERVATO", "ANNULLATO")


async def init_consumer() -> None:
    global _consumer, _producer
    _consumer = AIOKafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=_deserializza,
        **parametri_connessione(),
    )
    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        acks="all",
        **parametri_connessione(),
    )
    # Sottoscrizione con ascoltatore dei ribilanciamenti: alla revoca si chiudono
    # i messaggi in corso e si conferma ciò che è chiuso (consumo parallelo).
    _consumer.subscribe([settings.kafka_topic_in], listener=_AscoltatoreRibilanciamento())
    await _consumer.start()
    await _producer.start()
    logger.info("kafka_consumer_started", topic=settings.kafka_topic_in,
                group=settings.kafka_group_id, concorrenza=settings.concorrenza_pdv)


class _AscoltatoreRibilanciamento(ConsumerRebalanceListener):
    async def on_partitions_revoked(self, revoked):  # noqa: D401 — interfaccia aiokafka
        if _ciclo is not None:
            await _ciclo.partizioni_revocate(revoked)

    async def on_partitions_assigned(self, assigned):
        if _ciclo is not None:
            _ciclo.partizioni_assegnate(assigned)


def _deserializza(valore: bytes) -> Any:
    try:
        return json.loads(valore.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


async def close_consumer() -> None:
    global _consumer, _producer
    if _consumer:
        await _consumer.stop()
        _consumer = None
    if _producer:
        await _producer.stop()
        _producer = None
    logger.info("kafka_consumer_stopped")


async def _pubblica(topic: str, chiave: str, valore: dict) -> None:
    if _producer is None:
        raise RuntimeError("Producer Kafka non inizializzato")
    from libs.shared.observability.tracciamento import argomenti_kafka

    await _producer.send_and_wait(topic, key=chiave, value=valore, **argomenti_kafka())


# ── Lettura dei file dallo storage del versamento ──────────────────
# In streaming a blocchi: app/services/lettura_file.py (LettoreS3). Il lettore
# è unico per processo (client boto3 condiviso fra i thread).

_lettore_predefinito: LettoreS3 | None = None


def _lettore() -> LettoreFile:
    global _lettore_predefinito
    if _lettore_predefinito is None:
        _lettore_predefinito = LettoreS3()
    return _lettore_predefinito


class _LettoreMisurato:
    """Conta nelle metriche i byte letti dallo storage."""

    def __init__(self, interno: LettoreFile) -> None:
        self.interno = interno

    async def dimensione(self, rif):
        return await self.interno.dimensione(rif)

    async def leggi(self, rif, *, algoritmi, limite_byte=None):
        letto = await self.interno.leggi(rif, algoritmi=algoritmi, limite_byte=limite_byte)
        METRICHE.byte_letti.inc(letto.dimensione)
        return letto

    async def copia_in(self, rif, destinazione, *, limite_byte):
        """R08: copia locale per riconoscimento del formato e firme (passi 10 e 11)."""
        scritti = await self.interno.copia_in(rif, destinazione, limite_byte=limite_byte)
        METRICHE.byte_letti.inc(scritti)
        return scritti

    async def testa_coda(self, rif, n):
        """Revisione X5: testa e coda di un file oltre il limite della copia locale."""
        testa, coda = await self.interno.testa_coda(rif, n)
        METRICHE.byte_letti.inc(len(testa) + len(coda))
        return testa, coda

    async def contiene(self, rif, marcatore):
        """Revisione Y3: ricerca a blocchi della firma PAdES oltre la copia locale."""
        return await self.interno.contiene(rif, marcatore)


# ── Elaborazione di un PdV ─────────────────────────────────────────

def _risultato_evento(r: dict) -> dict:
    """Risultato di un passo per gli eventi: chiavi storiche + quelle lette da notification."""
    return {**r, "valido": r.get("ok"), "msg": r.get("messaggio")}


def avvisi_dei_passi(risultati) -> list[dict]:  # type: ignore[no-untyped-def]
    """R08: rilievi in AVVISO dei passi della verifica (catalogo dei formati,
    firme), nella forma degli avvisi del versamento: si aggiungono a
    ``pdv.avvisi`` e finiscono nel rapporto di versamento."""
    return [r for passo in risultati if passo.ok and passo.dettagli
            for r in passo.dettagli.get("rilievi") or () if r.get("effetto") == "AVVISA"]


def _evento_esito(evento: dict, verifica: dict) -> tuple[str, dict]:
    indice = evento.get("indice") or {}
    topic = settings.kafka_topic_valid if verifica["valido"] else settings.kafka_topic_invalid
    return topic, {
        "pdv_id": evento.get("pdv_id"),
        "tenant_id": evento.get("tenant_id"),
        "valido": verifica["valido"],
        "risultati": [_risultato_evento(r) for r in verifica["passi"]],
        "steps_eseguiti": verifica["steps_eseguiti"],
        "steps_totali": verifica["steps_totali"],
        "indice": indice,
        "classe_documentale": indice.get("classe_documentale", ""),
        "docs_s3_keys": evento.get("docs_s3_keys", []),
        "allegato5": evento.get("allegato5", {}),
    }


def _ids(evento: dict) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        return uuid.UUID(str(evento.get("pdv_id"))), uuid.UUID(str(evento.get("tenant_id")))
    except ValueError as exc:
        raise ErrorePermanente("VLD-EVT-001", "pdv_id o tenant_id non validi nell'evento") from exc


async def elabora_pdv(
    evento: dict,
    *,
    lettore: LettoreFile | None = None,
    pubblica=_pubblica,
    adesso: datetime | None = None,
) -> dict:
    """Verifica un PdV e ne persiste l'esito. Restituisce l'esito salvato.

    ``lettore``: accesso ai file in streaming (predefinito: S3 della configurazione).
    R05 (VD-C-080): le transazioni operano nell'ambito dell'ente dell'evento (Row
    Level Security): un evento con l'ente sbagliato non trova il PdV."""
    _pdv_id, tenant_id = _ids(evento)
    with ambito_ente(tenant_id):
        return await _elabora_pdv(evento, lettore=lettore, pubblica=pubblica, adesso=adesso)


async def _elabora_pdv(
    evento: dict,
    *,
    lettore: LettoreFile | None,
    pubblica,
    adesso: datetime | None,
) -> dict:
    pdv_id, tenant_id = _ids(evento)
    indice = evento.get("indice")
    if not isinstance(indice, dict):
        raise ErrorePermanente("VLD-EVT-002", "Indice mancante nell'evento")

    # 1. Lock del PdV e passaggio a IN_VALIDAZIONE (o ripubblicazione idempotente)
    async with get_async_session() as session:
        riga = (await session.execute(
            text(
                "SELECT stato, metadata_json -> 'verifica' AS verifica, regole_applicate "
                "FROM public.pdv WHERE id = :id AND tenant_id = :tenant FOR UPDATE"
            ),
            {"id": pdv_id, "tenant": tenant_id},
        )).mappings().first()
        if riga is None:
            raise ErrorePermanente("VLD-EVT-003", "PdV inesistente per il cliente indicato")
        stato = str(riga["stato"])
        verifica_salvata = riga["verifica"]
        if isinstance(verifica_salvata, str):
            verifica_salvata = json.loads(verifica_salvata)
        if stato in STATI_GIA_DECISI and verifica_salvata:
            topic, payload = _evento_esito(evento, verifica_salvata)
            logger.info("verifica_gia_registrata_ripubblicazione", pdv_id=str(pdv_id), stato=stato)
            await pubblica(topic, str(pdv_id), payload)
            METRICHE.pdv.labels("RIPUBBLICATO").inc()
            return verifica_salvata
        if stato in STATI_SUCCESSIVI or stato in STATI_GIA_DECISI:
            logger.info("verifica_saltata_stato_successivo", pdv_id=str(pdv_id), stato=stato)
            METRICHE.pdv.labels("SALTATO").inc()
            return {"saltato": True, "stato": stato}
        # Regole fotografate al versamento (REGOLE §8.3): limite per file, formati
        regole_salvate = riga["regole_applicate"]
        if isinstance(regole_salvate, str):
            regole_salvate = json.loads(regole_salvate)
        try:
            regole = regole_del_pdv(
                regole_salvate, limite_mb_predefinito=settings.validazione_max_file_mb,
                formati_predefiniti=settings.formati_ammessi,
            )
        except RegoleNonLeggibili as exc:
            raise ErrorePermanente(
                "VLD-802", f"Regole applicate al PdV non leggibili: {exc}") from exc
        if stato == "RICEVUTO":
            await session.execute(
                text("UPDATE public.pdv SET stato = 'IN_VALIDAZIONE', data_aggiornamento = now() "
                     "WHERE id = :id"),
                {"id": pdv_id},
            )

    # 2-3. Pipeline di verifica: i passi che servono leggono i file in streaming
    # (errore dello storage → ErroreVerificaTransitorio, si propaga e si ritenta)
    valido, risultati = await run_validation(
        pdv_id=str(pdv_id), tenant_id=str(tenant_id), indice=indice,
        riferimenti=riferimenti_da_evento(evento.get("docs_s3_keys") or []),
        lettore=lettore if lettore is not None else _LettoreMisurato(_lettore()),
        regole=regole, canale=evento.get("canale"),
    )
    istante = (adesso or datetime.now(UTC)).isoformat()
    verifica = {
        "valido": valido,
        "esito": "VALIDATO" if valido else "RIFIUTATO",
        "passi": [r.to_dict() for r in risultati],
        "steps_eseguiti": len(risultati),
        "steps_totali": len(VALIDATION_PIPELINE),
        "verificato_il": istante,
        "regole": regole.dettagli(),
    }
    errori = [_errore_da_risultato(r) for r in risultati if not r.ok]
    avvisi = avvisi_dei_passi(risultati)

    # 4. Esito persistito (solo da IN_VALIDAZIONE)
    async with get_async_session() as session:
        aggiornato = await session.execute(
            text(
                "UPDATE public.pdv SET stato = :stato, data_aggiornamento = now(), "
                "errori_validazione = CAST(:errori AS jsonb), "
                # R08: avvisi dei passi del contenuto (RGL-108/109 in avviso) → RdV
                "avvisi = COALESCE(avvisi, '[]'::jsonb) || CAST(:avvisi AS jsonb), "
                "metadata_json = COALESCE(metadata_json, '{}'::jsonb) "
                "  || jsonb_build_object('verifica', CAST(:verifica AS jsonb)) "
                "WHERE id = :id AND tenant_id = :tenant AND stato = 'IN_VALIDAZIONE'"
            ),
            {"stato": "VALIDATO" if valido else "ERRORE",
             "errori": json.dumps(errori, default=str),
             "avvisi": json.dumps(avvisi, default=str),
             "verifica": json.dumps(verifica, default=str),
             "id": pdv_id, "tenant": tenant_id},
        )
        if aggiornato.rowcount != 1:
            raise RuntimeError("PdV non più IN_VALIDAZIONE: esito non registrato")
        if not valido:
            await _libera_impronta_versamento(session, pdv_id)

    logger.info("pdv_verifica_registrata", pdv_id=str(pdv_id), valido=valido,
                passi=len(risultati))
    METRICHE.pdv.labels("VALIDATO" if valido else "RIFIUTATO").inc()
    # 5. Evento (dopo il commit; se fallisce si ritenta e si ripubblica l'esito salvato)
    topic, payload = _evento_esito(evento, verifica)
    await pubblica(topic, str(pdv_id), payload)
    return verifica


def _errore_da_risultato(r: ValidationResult) -> dict:
    return {"step": r.step, "nome": r.nome, "codice": r.codice_errore,
            "messaggio": r.messaggio, "dettagli": r.dettagli, "tipo": "verifica"}


async def _libera_impronta_versamento(session, pdv_id: uuid.UUID) -> None:
    """
    Un PdV rifiutato non blocca più un nuovo versamento con lo stesso indice
    (VD-C-016: il controllo dei duplicati vale fra i PdV non rifiutati).
    """
    await session.execute(
        text("UPDATE public.versamento_impronte SET attivo = false WHERE pdv_id = :id"),
        {"id": pdv_id},
    )


# ── Integrazione con il ciclo affidabile ───────────────────────────

async def gestisci_messaggio(msg: Any) -> None:
    evento = msg.value
    if not isinstance(evento, dict):
        raise ErrorePermanente("VLD-EVT-000", "Messaggio non JSON o non oggetto")
    await elabora_pdv(evento)


async def segna_errore_tecnico(evento: dict, motivo: dict) -> bool:
    """PdV RICEVUTO/IN_VALIDAZIONE → ERRORE con motivo esplicito."""
    try:
        pdv_id, tenant_id = _ids(evento)
    except ErrorePermanente:
        return False
    with ambito_ente(tenant_id):  # R05, VD-C-080: ente dell'evento
        return await _segna_errore_tecnico(pdv_id, tenant_id, motivo)


async def _segna_errore_tecnico(pdv_id: uuid.UUID, tenant_id: uuid.UUID, motivo: dict) -> bool:
    async with get_async_session() as session:
        risultato = await session.execute(
            text(
                "UPDATE public.pdv SET stato = 'ERRORE', data_aggiornamento = now(), "
                "errori_validazione = COALESCE(errori_validazione, '[]'::jsonb) "
                "  || jsonb_build_array(CAST(:motivo AS jsonb)) "
                "WHERE id = :id AND tenant_id = :tenant "
                "AND stato IN ('RICEVUTO', 'IN_VALIDAZIONE')"
            ),
            {"motivo": json.dumps(motivo, default=str), "id": pdv_id, "tenant": tenant_id},
        )
        if risultato.rowcount == 1:
            await _libera_impronta_versamento(session, pdv_id)
    return risultato.rowcount == 1


async def registra_fallimento(msg: Any, esito: EsitoElaborazione) -> None:
    """DLQ con motivo strutturato + stato ERRORE esplicito. Solleva se non riesce."""
    evento = msg.value if isinstance(msg.value, dict) else {}
    motivo = {"fase": "verifica", **esito.motivo()}
    await _pubblica(f"{settings.kafka_topic_in}{settings.suffisso_dlq}",
                    str(evento.get("pdv_id") or "senza-chiave"), {
        "topic_origine": msg.topic, "partizione": msg.partition, "offset": msg.offset,
        "evento": evento, "motivo": motivo,
        "registrato_il": datetime.now(UTC).isoformat(),
    })
    await segna_errore_tecnico(evento, motivo)


def _chiave_messaggio(msg: Any) -> str | None:
    """Chiave di serializzazione: il PdV (mai due elaborazioni dello stesso PdV insieme)."""
    valore = msg.value if isinstance(msg.value, dict) else {}
    pdv = valore.get("pdv_id")
    if pdv:
        return str(pdv)
    chiave = getattr(msg, "key", None)
    return chiave.decode("utf-8", "replace") if isinstance(chiave, bytes) else chiave


def crea_ciclo(consumer: Any) -> ConsumoParallelo:
    """Ciclo di consumo parallelo con la configurazione del servizio."""
    return ConsumoParallelo(
        consumer,
        gestore=gestisci_messaggio,
        su_fallimento=registra_fallimento,
        ritardi=list(settings.ritardi_ritentativi_secondi),
        concorrenza=settings.concorrenza_pdv,
        max_in_sospeso=settings.max_messaggi_in_sospeso,
        chiave_di=_chiave_messaggio,
        attesa_dopo_errore_dlq=settings.attesa_dopo_errore_dlq_secondi,
        metriche=METRICHE,
    )


async def consume_loop() -> None:
    """Loop principale: fino a CONCORRENZA_PDV PdV in parallelo, offset in ordine
    dopo l'esito (vedi consumo_parallelo)."""
    global _ciclo
    if _consumer is None or _producer is None:
        raise RuntimeError("Consumer non inizializzato")
    _ciclo = crea_ciclo(_consumer)
    try:
        await _ciclo.esegui()
    finally:
        _ciclo = None
