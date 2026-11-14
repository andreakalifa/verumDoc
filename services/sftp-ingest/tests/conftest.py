# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Fixture del servizio sftp-ingest.

- ``ENV=test`` prima di importare l'app (chiavi effimere, nessun segreto).
- Database: copia del template migrato (``tests/infra_reale.py``).
- Gateway finto (``tests/gateway_finto.py``) servito con ``httpx.ASGITransport``.
- Server SFTP vero (asyncssh) sulla porta libera, nel loop del test.
"""
from __future__ import annotations

import os

os.environ["ENV"] = "test"

import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import asyncssh  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from app.application.lavoratore import ConfigLavoratore, Lavoratore  # noqa: E402
from app.infrastructure import repository  # noqa: E402
from app.infrastructure.gateway import ClientGateway  # noqa: E402
from app.infrastructure.server_sftp import (  # noqa: E402
    ContestoServer,
    QuotaAccount,
    avvia_server,
)
from app.infrastructure.token_canale import emettitore_da_impostazioni  # noqa: E402
from app.metriche import MetricheSftp  # noqa: E402

from tests import infra_reale  # noqa: E402
from tests.gateway_finto import StatoGateway, crea_gateway  # noqa: E402
from tests.infra_reale import _porta_libera, sql  # noqa: E402

#: fixture del database (copia del template migrato) per tutti i test del servizio
db_url = infra_reale.db_url

ALGORITMI_FIRMA = ["ssh-ed25519", "ecdsa-sha2-nistp256", "rsa-sha2-512", "rsa-sha2-256"]
TIPI_AMMESSI = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
                "ecdsa-sha2-nistp521", "ssh-rsa")


@dataclass
class Account:
    id: uuid.UUID
    tenant_id: uuid.UUID
    responsabile_id: uuid.UUID
    nome_utente: str
    chiave: asyncssh.SSHKey


async def crea_ente(url: str, stato: str = "ATTIVO") -> uuid.UUID:
    tid = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, "
                   "CAST($4 AS public.tenant_stato), 'a@b.it')",
              tid, tid.hex[:11], f"t_{tid.hex[:20]}", stato)
    return tid


async def crea_utente(url: str, tid: uuid.UUID, ruolo: str = "OPERATORE",
                      attivo: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, $3, 'Nome', 'Cognome', CAST($4 AS public.user_ruolo), $5)",
              uid, tid, f"{uid.hex[:8]}@x.it", ruolo, attivo)
    return uid


async def registra_chiave(url: str, account: uuid.UUID, tid: uuid.UUID,
                          chiave: asyncssh.SSHKey) -> None:
    pub = chiave.convert_to_public()
    testo = pub.export_public_key("openssh").decode().split()
    await sql(url, "INSERT INTO public.sftp_chiavi (account_id, tenant_id, algoritmo, "
                   "chiave_pubblica, impronta) VALUES ($1, $2, $3, $4, $5)",
              account, tid, pub.get_algorithm(), " ".join(testo[:2]),
              pub.get_fingerprint("sha256"))


async def crea_account(url: str, *, stato: str = "ATTIVO", tenant_stato: str = "ATTIVO",
                       tipo_chiave: str = "ssh-ed25519", bit: int | None = None) -> Account:
    tid = await crea_ente(url, tenant_stato)
    uid = await crea_utente(url, tid)
    aid = uuid.uuid4()
    nome = f"ente{aid.hex[:8]}"
    await sql(url, "INSERT INTO public.sftp_account (id, tenant_id, nome_utente, responsabile_id, "
                   "stato) VALUES ($1, $2, $3, $4, $5)", aid, tid, nome, uid, stato)
    chiave = (asyncssh.generate_private_key(tipo_chiave, key_size=bit) if bit
              else asyncssh.generate_private_key(tipo_chiave))
    await registra_chiave(url, aid, tid, chiave)
    return Account(aid, tid, uid, nome, chiave)


@pytest.fixture
def radice(tmp_path: Path) -> Path:
    r = tmp_path / "sftp"
    r.mkdir()
    return r


@pytest.fixture
def metriche() -> MetricheSftp:
    return MetricheSftp()


@pytest.fixture
async def server_sftp(db_url, radice, metriche) -> AsyncIterator[tuple[int, ContestoServer]]:
    """Server SFTP vero su una porta libera; restituisce (porta, contesto)."""
    contesto = ContestoServer(
        radice=str(radice), cerca_account=repository.account_per_accesso,
        tipi_chiave_ammessi=TIPI_AMMESSI, rsa_bit_minimi=3072,
        quota=QuotaAccount(limite_byte=10 * 1024 * 1024, ricalcolo_secondi=60),
        metriche=metriche)
    porta = _porta_libera()
    acceptor = await avvia_server(contesto, host="127.0.0.1", porta=porta,
                                  chiave_host=asyncssh.generate_private_key("ssh-ed25519"),
                                  algoritmi_firma=ALGORITMI_FIRMA, login_timeout=10)
    try:
        yield porta, contesto
    finally:
        acceptor.close()
        await acceptor.wait_closed()


def connetti(porta: int, account: Account, chiave: asyncssh.SSHKey | None = None, **opzioni):
    """Connessione client asyncssh (host key non verificata: server di test)."""
    return asyncssh.connect("127.0.0.1", porta, username=account.nome_utente,
                            client_keys=[chiave or account.chiave], known_hosts=None,
                            agent_path=None, **opzioni)


@pytest.fixture
def emettitore():
    return emettitore_da_impostazioni(env="test", chiave_privata="", emittente="verumdoc.it",
                                      durata_secondi=300)


@pytest.fixture
def gateway_finto(emettitore) -> StatoGateway:
    return StatoGateway(chiave_pubblica=emettitore.chiave_pubblica_pem)


@pytest.fixture
async def client_gateway(gateway_finto) -> AsyncIterator[ClientGateway]:
    attese: list[float] = []

    async def attendi(secondi: float) -> None:
        attese.append(secondi)

    client = ClientGateway("http://gateway", timeout_secondi=10, ritentativi_secondi=[1, 2],
                           transport=httpx.ASGITransport(app=crea_gateway(gateway_finto)),
                           attendi=attendi)
    client.attese = attese  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        await client.chiudi()


def config_lavoratore(radice: Path, **variazioni) -> ConfigLavoratore:
    base: dict[str, Any] = {
        "radice": str(radice), "intervallo_secondi": 0.1, "parallelismo": 4,
        "parti_parallele": 3, "lease_secondi": 60, "stabilita_secondi": 0,
        "abbandono_secondi": 3600, "ritardi_secondi": (0, 0), "attesa_verifica_secondi": 5,
        "indice_max_byte": 64 * 1024, "cartelle_per_giro": 50, "conserva_fatti_giorni": 7,
        "conserva_scarti_giorni": 30, "conserva_esiti_giorni": 30,
        "url_pubblico_api": "https://api.verumdoc.test"}
    base.update(variazioni)
    return ConfigLavoratore(**base)


@pytest.fixture
def lavoratore(radice, client_gateway, emettitore, metriche) -> Lavoratore:
    return Lavoratore(config_lavoratore(radice), gateway=client_gateway, emettitore=emettitore,
                      metriche=metriche, detentore="replica-a")
