# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Fixture del servizio notifiche (VD-C-024).

- server SMTP simulato con aiosmtpd: in chiaro, con STARTTLS obbligatorio e con
  TLS implicito, certificato autofirmato generato al volo;
- coda di uscita in memoria con la stessa semantica di CodaUscitaSQL
  (deduplica, prelievo, stati), per i test del ciclo di invio.
"""

from __future__ import annotations

import os

# Ambiente esplicito: il default delle Settings è «production» (revisione M5).
os.environ["ENV"] = "test"

import ipaddress
import socket
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from aiosmtpd.controller import Controller
from app.services.coda import (
    IN_CODA,
    IN_INVIO,
    INVIATA,
    NON_PARTITA,
    MessaggioUscita,
    NuovoMessaggio,
)
from app.services.smtp import ConfigSMTP
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def certificato_tls(tmp_path_factory) -> dict:
    chiave = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    adesso = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chiave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(adesso - timedelta(days=1))
        .not_valid_after(adesso + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(chiave, hashes.SHA256())
    )
    cartella = tmp_path_factory.mktemp("tls")
    cert_pem = cartella / "smtp.pem"
    key_pem = cartella / "smtp.key"
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(
        chiave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return {"cert": str(cert_pem), "key": str(key_pem)}


class GestoreSMTP:
    """Riceve i messaggi; rifiuta in modo permanente «rifiuta@…», temporaneo «occupato@…»."""

    def __init__(self) -> None:
        self.ricevuti: list = []
        self.tls_usato: list[bool] = []

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: ANN001
        if address.startswith("rifiuta@"):
            return "550 5.1.1 casella inesistente"
        if address.startswith("occupato@"):
            return "451 4.3.0 riprovare piu tardi"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001
        self.ricevuti.append(envelope)
        self.tls_usato.append(session.ssl is not None)
        return "250 Message accepted"


@dataclass
class ServerSMTP:
    porta: int
    gestore: GestoreSMTP
    controller: Controller


def _avvia(gestore: GestoreSMTP, **kw) -> ServerSMTP:
    porta = _porta_libera()
    controller = Controller(gestore, hostname="127.0.0.1", port=porta, **kw)
    controller.start()
    return ServerSMTP(porta, gestore, controller)


@pytest.fixture
def smtp_starttls(certificato_tls):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certificato_tls["cert"], certificato_tls["key"])
    s = _avvia(GestoreSMTP(), tls_context=ctx, require_starttls=True)
    yield s
    s.controller.stop()


@pytest.fixture
def smtp_tls_implicito(certificato_tls):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certificato_tls["cert"], certificato_tls["key"])
    s = _avvia(GestoreSMTP(), ssl_context=ctx)
    yield s
    s.controller.stop()


@pytest.fixture
def smtp_in_chiaro():
    s = _avvia(GestoreSMTP())
    yield s
    s.controller.stop()


def config_smtp(porta: int, sicurezza: str, ca: str = "") -> ConfigSMTP:
    return ConfigSMTP(
        host="127.0.0.1",
        porta=porta,
        sicurezza=sicurezza,
        mittente="conservazione@conservatore-prova.it",
        nome_mittente="Conservatore di prova",
        timeout=5,
        ca_pem=ca,
    )


@dataclass
class _Riga:
    id: str
    tenant_id: str | None
    tipo: str
    canale: str
    destinatario: str
    payload: dict
    stato: str
    tentativi: int = 0
    prossimo_tentativo: datetime | None = None
    errore: str | None = None
    aggiornata: datetime | None = None
    creata: datetime | None = None


@dataclass
class CodaMemoria:
    """Doppio di test di CodaUscitaSQL con la stessa semantica."""

    righe: list[_Riga] = field(default_factory=list)

    def _dup(self, m: NuovoMessaggio) -> bool:
        # stessa chiave dell'indice uq_notifications_dedup (migrazione 20261020_013)
        return any(
            r.tenant_id == m.tenant_id
            and r.tipo == m.tipo
            and r.destinatario == m.destinatario
            and r.payload.get("oggetto_id") == m.oggetto_id
            for r in self.righe
        )

    async def _inserisci(
        self, m: NuovoMessaggio, stato: str, errore: str | None, adesso: datetime
    ) -> bool:
        if self._dup(m):
            return False
        self.righe.append(
            _Riga(
                str(uuid.uuid4()),
                m.tenant_id or None,
                m.tipo,
                m.canale,
                m.destinatario,
                {
                    "oggetto_id": m.oggetto_id,
                    "oggetto": m.oggetto,
                    "testo": m.testo,
                    "html": m.html,
                    **({"segreti": dict(m.segreti)} if m.segreti else {}),
                },
                stato,
                errore=errore,
                aggiornata=adesso,
                creata=adesso,
            )
        )
        return True

    async def accoda(self, m, adesso):
        return await self._inserisci(m, IN_CODA, None, adesso)

    async def registra_non_partita(self, m, motivo, adesso):
        return await self._inserisci(m, NON_PARTITA, motivo, adesso)

    async def preleva(self, adesso, limite):
        presi = []
        for r in self.righe:
            if (
                r.stato == IN_CODA
                and (r.prossimo_tentativo is None or r.prossimo_tentativo <= adesso)
                and len(presi) < limite
            ):
                r.stato, r.aggiornata = IN_INVIO, adesso
                presi.append(
                    MessaggioUscita(
                        r.id,
                        r.tenant_id,
                        r.tipo,
                        r.canale,
                        r.destinatario,
                        r.tentativi,
                        dict(r.payload),
                    )
                )
        return presi

    def _r(self, id_):
        return next(r for r in self.righe if r.id == id_)

    async def segna_inviata(self, id_, adesso, message_id):
        r = self._r(id_)
        r.stato, r.tentativi, r.errore, r.aggiornata = INVIATA, r.tentativi + 1, None, adesso
        r.payload.pop("segreti", None)  # R12a (VD-C-152): come MINIMIZZA_DOPO_INVIO
        r.payload["message_id"] = message_id

    async def riprogramma(self, id_, tentativi, prossimo, errore, adesso):
        r = self._r(id_)
        r.stato, r.tentativi, r.prossimo_tentativo, r.errore = IN_CODA, tentativi, prossimo, errore

    async def segna_non_partita(self, id_, tentativi, errore, adesso):
        r = self._r(id_)
        r.stato, r.tentativi, r.errore, r.prossimo_tentativo = NON_PARTITA, tentativi, errore, None

    async def recupera_bloccati(self, soglia, adesso):
        n = 0
        for r in self.righe:
            if r.stato == IN_INVIO and r.aggiornata and r.aggiornata < soglia:
                r.stato, n = IN_CODA, n + 1
        return n


@pytest.fixture
def coda() -> CodaMemoria:
    return CodaMemoria()


@pytest.fixture(autouse=True)
def ambito_conservatore_per_i_repository():
    """R05 (VD-C-080): molti test chiamano DIRETTAMENTE i repository delle API
    (sottoscrizioni, elenco eventi) che in esercizio girano con l'ambito d'ente
    impostato dal token verificato, e quelli dei lavoratori (già nell'ambito
    conservatore per decorazione). Qui i test girano nell'ambito conservatore:
    l'isolamento provato resta quello del filtro applicativo; le richieste HTTP
    dei test sostituiscono l'ambito con quello del token. La RLS per ente è
    provata in services/gateway/tests/test_r05_rls_ente.py."""
    from libs.shared.db.ambito import ambito_conservatore

    with ambito_conservatore("test dei repository di notification"):
        yield
