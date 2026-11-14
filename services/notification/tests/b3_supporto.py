# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Supporto ai test dei webhook (agente R03 b3): destinatario HTTPS vero in un
thread, certificati autofirmati con nomi a scelta, token JWT e righe di prova.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def certificato(cartella: Path, nomi: list[str]) -> tuple[str, str, str]:
    """(file cert, file chiave, PEM del cert) autofirmato per ``nomi`` (DNS o IP)."""
    chiave = ec.generate_private_key(ec.SECP256R1())
    soggetto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nomi[0])])
    alternativi: list[x509.GeneralName] = []
    for n in nomi:
        try:
            alternativi.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            alternativi.append(x509.DNSName(n))
    adesso = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(soggetto).issuer_name(soggetto)
        .public_key(chiave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(adesso - timedelta(days=1)).not_valid_after(adesso + timedelta(days=5))
        .add_extension(x509.SubjectAlternativeName(alternativi), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(chiave, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    nome = uuid.uuid4().hex[:8]
    f_cert, f_chiave = cartella / f"{nome}.pem", cartella / f"{nome}.key"
    f_cert.write_text(pem)
    f_chiave.write_bytes(chiave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return str(f_cert), str(f_chiave), pem


@dataclass
class Ricevuta:
    percorso: str
    intestazioni: dict[str, str]
    corpo: bytes


@dataclass
class Destinatario:
    """Server HTTPS di prova: registra le richieste; risposta e attesa regolabili."""

    porta: int
    pem: str
    ricevute: list[Ricevuta] = field(default_factory=list)
    stato: int = 200
    attesa: float = 0.0
    intestazioni_risposta: dict[str, str] = field(default_factory=dict)
    _server: ThreadingHTTPServer | None = None

    def ferma(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def avvia_destinatario(cartella: Path, nomi: list[str]) -> Destinatario:
    f_cert, f_chiave, pem = certificato(cartella, nomi)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = int(s.getsockname()[1])
    dest = Destinatario(porta=porta, pem=pem)

    class Gestore(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — nome imposto da http.server
            lunghezza = int(self.headers.get("Content-Length") or 0)
            corpo = self.rfile.read(lunghezza)
            dest.ricevute.append(Ricevuta(self.path, dict(self.headers.items()), corpo))
            if dest.attesa:
                time.sleep(dest.attesa)
            self.send_response(dest.stato)
            for k, v in dest.intestazioni_risposta.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "2")
            self.end_headers()
            try:
                self.wfile.write(b"ok")
            except OSError:
                pass

        def log_message(self, *args) -> None:  # silenzio nei test
            return

    server = ThreadingHTTPServer(("127.0.0.1", porta), Gestore)
    contesto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contesto.load_cert_chain(f_cert, f_chiave)
    server.socket = contesto.wrap_socket(server.socket, server_side=True)
    server.daemon_threads = True
    dest._server = server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return dest


def token_utente(tenant_id, ruolo: str, user_id=None, *, chiave_privata: str | None = None,
                 durata: int = 600) -> str:
    """Token RS256 come quelli del gateway (chiavi effimere di ENV=test)."""
    from app.api.autenticazione import chiavi_jwt
    from app.config import settings

    from libs.shared.security.jwt_rs256 import firma_token

    return firma_token(
        {"sub": str(user_id or uuid.uuid4()), "tenant_id": str(tenant_id),
         "user_id": str(user_id or uuid.uuid4()), "ruolo": ruolo,
         "scopes": ["read"]},
        chiave_privata_pem=chiave_privata or chiavi_jwt().privata_pem,
        emittente=settings.jwt_issuer, pubblico=settings.jwt_audience, durata_secondi=durata)


def evento_kafka(tipo_topic: str, tenant_id, **campi) -> dict:
    base = {"tenant_id": str(tenant_id)}
    base.update({k: str(v) if isinstance(v, uuid.UUID) else v for k, v in campi.items()})
    return json.loads(json.dumps(base))
