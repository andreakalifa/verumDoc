# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Broker Kafka minimo per i test di prontezza (g4-chiusura).

Parla il protocollo Kafka vero sul socket (frame con lunghezza, intestazione con
api_key/api_version/correlation_id), quanto basta per ApiVersions v0 e
Metadata v0: le richieste che aiokafka invia per connettersi e leggere i
metadati. Le risposte sono codificate con le strutture di aiokafka stesse.
Non esiste un Kafka nell'ambiente di test (nessun demone Docker).

Uso:
    async with broker_kafka_finto() as bootstrap:   # "127.0.0.1:<porta>"
        ...
    async with broker_kafka_finto(senza_broker=True) as bootstrap:
        ...   # risponde ai metadati con un elenco di broker vuoto
"""
from __future__ import annotations

import asyncio
import struct
from contextlib import asynccontextmanager

from aiokafka.protocol.admin import ApiVersionResponse_v0
from aiokafka.protocol.metadata import MetadataResponse_v0

API_METADATA = 3
API_VERSIONS = 18


@asynccontextmanager
async def broker_kafka_finto(*, senza_broker: bool = False):
    richieste: list[int] = []

    async def gestisci(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        porta = writer.get_extra_info("sockname")[1]
        try:
            while True:
                (lunghezza,) = struct.unpack(">i", await reader.readexactly(4))
                dati = await reader.readexactly(lunghezza)
                api_key, _versione, correlazione = struct.unpack(">hhi", dati[:8])
                richieste.append(api_key)
                if api_key == API_VERSIONS:
                    corpo = ApiVersionResponse_v0(
                        error_code=0,
                        api_versions=[(API_METADATA, 0, 0), (API_VERSIONS, 0, 0)],
                    ).encode()
                elif api_key == API_METADATA:
                    brokers = [] if senza_broker else [(1, "127.0.0.1", porta)]
                    corpo = MetadataResponse_v0(brokers=brokers, topics=[]).encode()
                else:
                    break
                risposta = struct.pack(">i", correlazione) + corpo
                writer.write(struct.pack(">i", len(risposta)) + risposta)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(gestisci, "127.0.0.1", 0)
    porta = server.sockets[0].getsockname()[1]
    try:
        yield f"127.0.0.1:{porta}"
    finally:
        server.close()
        await server.wait_closed()


def porta_chiusa() -> str:
    """Bootstrap verso una porta locale su cui nessuno ascolta."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    return f"127.0.0.1:{porta}"
