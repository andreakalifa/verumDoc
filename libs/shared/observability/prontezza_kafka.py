# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Controllo di prontezza di Kafka: connessione a un broker di bootstrap e richiesta
Metadata (protocollo Kafka, come fa aiokafka all'avvio). Basta un broker che
risponde con almeno un broker nei metadati.

Una connessione nuova per ogni probe (ogni 10 s): costo trascurabile e nessuna
dipendenza dallo stato interno dei consumer/producer del servizio.
"""
from __future__ import annotations

from aiokafka.conn import create_conn
from aiokafka.protocol.metadata import MetadataRequest

from libs.shared.kafka.connessione import parametri_connessione
from libs.shared.observability.prontezza import NonPronto

PORTA_PREDEFINITA = 9092


def indirizzi(bootstrap: str) -> list[tuple[str, int]]:
    """«host:porta,host2:porta» → [(host, porta), ...] (porta predefinita 9092)."""
    risultato: list[tuple[str, int]] = []
    for voce in (bootstrap or "").split(","):
        voce = voce.strip()
        if not voce:
            continue
        host, sep, porta = voce.rpartition(":")
        if not sep:
            host, porta = voce, str(PORTA_PREDEFINITA)
        risultato.append((host.strip("[]"), int(porta)))
    return risultato


async def controlla_kafka(bootstrap: str, *, timeout_ms: int = 1500) -> None:
    """Termina se almeno un broker risponde ai metadati; altrimenti solleva."""
    elenco = indirizzi(bootstrap)
    if not elenco:
        raise NonPronto("non_configurato")
    ultimo: Exception | None = None
    for host, porta in elenco:
        conn = None
        try:
            conn = await create_conn(host, porta, client_id="verumdoc-readiness",
                                     request_timeout_ms=timeout_ms,
                                     **parametri_connessione())
            metadati = await conn.send(MetadataRequest([]))
        except Exception as exc:  # noqa: BLE001 — si prova il broker successivo
            ultimo = exc
            continue
        finally:
            if conn is not None:
                conn.close()
        if metadati.brokers:
            return
        ultimo = RuntimeError(f"{host}:{porta} senza broker nei metadati")
    raise ultimo or RuntimeError("nessun broker raggiungibile")
