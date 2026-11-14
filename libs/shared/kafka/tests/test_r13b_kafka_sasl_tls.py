# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-109) — percorso autenticato verso Kafka come su MSK con IAM: TLS verificato +
SASL OAUTHBEARER con il token del fornitore dei servizi (``TokenMskIam``), provato sul
FILO con un broker di prova che parla il protocollo Kafka (ApiVersions, SaslHandshake
v1, SaslAuthenticate v0) dietro TLS con un certificato di una CA generata a runtime.

Perché un broker di prova: un Kafka vero con SASL non è stato scaricabile da questo
ambiente (archive.apache.org, dlcdn.apache.org, Maven Central e GitHub rifiutati dal
proxy; nessun demone Docker). La prova copre ciò che dipende dal codice di VerumDoc
(parametri di connessione, contesto TLS con ``KAFKA_CA_FILE``, token sul filo nel formato
RFC 7628, rinnovo, rifiuto del broker); restano da provare su MSK reale firma SigV4 del
token, ruoli IRSA e ACL per topic (checklist in ``docs/architettura/giorno1_staging.md``).
"""
from __future__ import annotations

import asyncio
import ipaddress
import ssl
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiokafka.conn import create_conn
from aiokafka.errors import KafkaConnectionError, for_code
from aiokafka.protocol import admin
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from libs.shared.kafka.connessione import ImpostazioniKafka, TokenMskIam, parametri_connessione


def _certificati(cartella: Path) -> tuple[str, str, str]:
    chiave = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "broker-prova")])
    adesso = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
            .public_key(chiave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(adesso - timedelta(days=1))
            .not_valid_after(adesso + timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(chiave, hashes.SHA256()))
    f_cert, f_chiave = cartella / "broker.pem", cartella / "broker.key"
    f_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    f_chiave.write_bytes(chiave.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    return str(f_cert), str(f_chiave), str(f_cert)


class BrokerProva:
    """Broker Kafka minimo: risponde a ApiVersions, SaslHandshake e SaslAuthenticate."""

    def __init__(self, token_atteso: str) -> None:
        self.token_atteso = token_atteso
        self.ricevuti: list[bytes] = []
        self.meccanismi: list[str] = []
        self.porta = 0
        self._server: asyncio.base_events.Server | None = None

    async def _gestisci(self, lettore: asyncio.StreamReader, scrittore: asyncio.StreamWriter
                        ) -> None:
        try:
            while True:
                dimensione = struct.unpack(">i", await lettore.readexactly(4))[0]
                dati = await lettore.readexactly(dimensione)
                api, versione, correlazione = struct.unpack(">hhi", dati[:8])
                lung_client = struct.unpack(">h", dati[8:10])[0]
                corpo = dati[10 + max(lung_client, 0):]
                if api == 18:
                    classe = admin.ApiVersionResponse_v0 if versione == 0 else \
                        admin.ApiVersionResponse_v1
                    voci = [(18, 0, 1), (17, 0, 1), (36, 0, 0)]
                    risposta = classe(0, voci) if versione == 0 else classe(0, voci, 0)
                elif api == 17:
                    lung = struct.unpack(">h", corpo[:2])[0]
                    self.meccanismi.append(corpo[2:2 + lung].decode())
                    risposta = admin.SaslHandShakeResponse_v1(0, ["OAUTHBEARER"])
                elif api == 36:
                    lung = struct.unpack(">i", corpo[:4])[0]
                    gettone = corpo[4:4 + lung]
                    self.ricevuti.append(gettone)
                    atteso = f"n,,\x01auth=Bearer {self.token_atteso}\x01\x01".encode()
                    risposta = admin.SaslAuthenticateResponse_v0(0, None, b"") \
                        if gettone == atteso else \
                        admin.SaslAuthenticateResponse_v0(58, "Authentication failed", b"")
                else:
                    break
                messaggio = struct.pack(">i", correlazione) + risposta.encode()
                scrittore.write(struct.pack(">i", len(messaggio)) + messaggio)
                await scrittore.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            scrittore.close()

    async def avvia(self, contesto: ssl.SSLContext) -> None:
        self._server = await asyncio.start_server(self._gestisci, "127.0.0.1", 0, ssl=contesto)
        self.porta = self._server.sockets[0].getsockname()[1]

    async def ferma(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


@pytest.fixture
async def broker(tmp_path):
    f_cert, f_chiave, ca = _certificati(tmp_path)
    contesto = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    contesto.load_cert_chain(f_cert, f_chiave)
    b = BrokerProva("token-msk-di-prova")
    await b.avvia(contesto)
    b.ca = ca  # type: ignore[attr-defined]
    yield b
    await b.ferma()


def _imp(ca: str) -> ImpostazioniKafka:
    return ImpostazioniKafka(autenticazione="iam", regione="eu-south-1", ca_file=ca)


async def _connetti(broker: BrokerProva, parametri: dict) -> None:
    conn = await create_conn("127.0.0.1", broker.porta, request_timeout_ms=5000, **parametri)
    conn.close()


async def test_tls_verificato_e_oauthbearer_sul_filo(broker) -> None:
    generati: list[str] = []

    def generatore(regione: str) -> tuple[str, int]:
        generati.append(regione)
        return "token-msk-di-prova", int((datetime.now(UTC).timestamp() + 900) * 1000)

    parametri = parametri_connessione(_imp(broker.ca), generatore=generatore)
    assert parametri["security_protocol"] == "SASL_SSL"
    await _connetti(broker, parametri)
    assert broker.meccanismi == ["OAUTHBEARER"]
    assert broker.ricevuti == [b"n,,\x01auth=Bearer token-msk-di-prova\x01\x01"]
    # seconda connessione dello stesso processo: stesso token, nessuna nuova firma
    await _connetti(broker, parametri)
    assert generati == ["eu-south-1"] and len(broker.ricevuti) == 2


async def test_token_rifiutato_dal_broker(broker) -> None:
    parametri = parametri_connessione(
        _imp(broker.ca), generatore=lambda r: ("token-sbagliato", 9_999_999_999_000))
    with pytest.raises(for_code(58)):  # SaslAuthenticationFailed
        await _connetti(broker, parametri)


async def test_senza_la_ca_il_certificato_non_e_attendibile(broker, tmp_path) -> None:
    (tmp_path / "altra").mkdir()
    altra = _certificati(tmp_path / "altra")[2]
    parametri = parametri_connessione(
        _imp(altra), generatore=lambda r: ("token-msk-di-prova", 9_999_999_999_000))
    with pytest.raises((KafkaConnectionError, ssl.SSLError, OSError)):
        await _connetti(broker, parametri)
    assert broker.ricevuti == []   # nessun token prima di un TLS verificato


async def test_token_rinnovato_prima_della_scadenza(broker) -> None:
    adesso = [1_000_000.0]
    scadenze = iter([1_000_000 + 120, 1_000_000 + 3600])
    gettoni = iter(["token-msk-di-prova", "token-msk-di-prova"])

    def generatore(_r: str) -> tuple[str, int]:
        return next(gettoni), int(next(scadenze) * 1000)

    fornitore = TokenMskIam("eu-south-1", generatore=generatore, orologio=lambda: adesso[0])
    await fornitore.token()
    adesso[0] += 61   # entro il margine di 60 s dalla scadenza: nuovo token
    await fornitore.token()
    assert fornitore.generati == 2
