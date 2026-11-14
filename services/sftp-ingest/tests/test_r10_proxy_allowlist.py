# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R10 (D12, VD-C-107) — PROXY protocol del bilanciatore e indirizzi ammessi per
account, sul server SFTP vero (asyncssh) e PostgreSQL reale.

  - da una sorgente fidata l'intestazione PROXY (v1 o v2) dà l'IP reale del
    cliente, che decide l'accesso con l'elenco dell'account e finisce in audit;
  - da una sorgente fidata senza intestazione: connessione chiusa;
  - da una sorgente NON fidata l'intestazione non si legge (nessun IP falsificato);
  - elenco obbligatorio per l'ente (SFTP_ALLOWLIST_OBBLIGATORIA): senza elenco, negato.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import struct

import asyncssh
import pytest
from app.infrastructure import repository
from app.infrastructure.server_sftp import ContestoServer, QuotaAccount, avvia_server

from libs.shared.parametri import svuota_cache
from libs.shared.security.proxy_protocol import FIRMA_V2
from tests.conftest import ALGORITMI_FIRMA, TIPI_AMMESSI, crea_account
from tests.infra_reale import richiede_pg, sql

pytestmark = richiede_pg


@pytest.fixture
async def server_proxy(db_url, radice, metriche, request):
    fidate = getattr(request, "param", ["127.0.0.0/8"])
    contesto = ContestoServer(
        radice=str(radice), cerca_account=repository.account_per_accesso,
        tipi_chiave_ammessi=TIPI_AMMESSI, rsa_bit_minimi=3072,
        quota=QuotaAccount(limite_byte=1024 * 1024, ricalcolo_secondi=60),
        metriche=metriche, registra_accesso=repository.registra_accesso)
    ascolto = await avvia_server(contesto, host="127.0.0.1", porta=0,
                                 chiave_host=asyncssh.generate_private_key("ssh-ed25519"),
                                 algoritmi_firma=ALGORITMI_FIRMA, login_timeout=10,
                                 proxy_fidate=fidate, proxy_attesa=1.0)
    svuota_cache()
    try:
        yield ascolto.porta()
    finally:
        ascolto.close()
        await ascolto.wait_closed()


def _v1(ip: str) -> bytes:
    famiglia = "TCP4" if ipaddress.ip_address(ip).version == 4 else "TCP6"
    destinazione = "10.0.0.1" if famiglia == "TCP4" else "::1"
    return f"PROXY {famiglia} {ip} {destinazione} 51000 2222\r\n".encode()


def _v2(ip: str) -> bytes:
    indirizzi = (ipaddress.IPv4Address(ip).packed + ipaddress.IPv4Address("10.0.0.1").packed
                 + struct.pack(">HH", 51000, 2222))
    return FIRMA_V2 + bytes([0x21, 0x11]) + struct.pack(">H", len(indirizzi)) + indirizzi


async def _collega(porta: int, account, intestazione: bytes | None):
    sock = socket.create_connection(("127.0.0.1", porta))
    if intestazione:
        sock.sendall(intestazione)
    return asyncssh.connect(sock=sock, username=account.nome_utente,
                            client_keys=[account.chiave], known_hosts=None, agent_path=None,
                            login_timeout=5)


async def _elenco(url: str, account, voci: list[str]) -> None:
    await sql(url, "UPDATE public.sftp_account SET ip_ammessi = $1 WHERE id = $2", voci,
              account.id)


async def _audit(url: str) -> list[tuple[str, str | None]]:
    righe = await sql(url, "SELECT evento, host(ip_address) AS ip FROM public.audit_global "
                           "WHERE evento LIKE 'sftp.accesso.%' ORDER BY created_at")
    return [(r["evento"], r["ip"]) for r in righe]


async def _attendi_audit(url: str, n: int) -> list[tuple[str, str | None]]:
    import asyncio

    for _ in range(50):
        voci = await _audit(url)
        if len(voci) >= n:
            return voci
        await asyncio.sleep(0.05)
    return await _audit(url)


async def test_proxy_v1_ip_reale_ammesso_e_in_audit(db_url, server_proxy) -> None:
    account = await crea_account(db_url)
    await _elenco(db_url, account, ["203.0.113.0/24"])
    async with await _collega(server_proxy, account, _v1("203.0.113.7")) as conn, \
            conn.start_sftp_client() as sftp:
        assert "in" in await sftp.listdir("/")
    assert await _attendi_audit(db_url, 1) == [("sftp.accesso.riuscito", "203.0.113.7")]


async def test_proxy_v2_ip_non_ammesso_negato(db_url, server_proxy) -> None:
    account = await crea_account(db_url)
    await _elenco(db_url, account, ["203.0.113.0/24"])
    with pytest.raises(asyncssh.PermissionDenied):
        async with await _collega(server_proxy, account, _v2("198.51.100.9")):
            pass
    assert await _attendi_audit(db_url, 1) == [("sftp.accesso.negato", "198.51.100.9")]
    async with await _collega(server_proxy, account, _v2("203.0.113.200")):
        pass


async def test_sorgente_fidata_senza_intestazione_chiusa(db_url, server_proxy) -> None:
    account = await crea_account(db_url)
    with pytest.raises((asyncssh.Error, OSError, TimeoutError)):
        async with await _collega(server_proxy, account, None):
            pass


@pytest.mark.parametrize("server_proxy", [["10.99.0.0/16"]], indirect=True)
async def test_sorgente_non_fidata_non_falsifica_l_ip(db_url, server_proxy) -> None:
    account = await crea_account(db_url)
    await _elenco(db_url, account, ["203.0.113.0/24"])
    # un cliente diretto che scrive una PROXY «ammessa»: per il server sono byte SSH
    with pytest.raises((asyncssh.Error, OSError)):
        async with await _collega(server_proxy, account, _v1("203.0.113.7")):
            pass
    # senza intestazione vale l'IP del socket (127.0.0.1), fuori dall'elenco
    with pytest.raises(asyncssh.PermissionDenied):
        async with await _collega(server_proxy, account, None):
            pass
    await _elenco(db_url, account, ["127.0.0.1/32"])
    async with await _collega(server_proxy, account, None):
        pass


async def test_elenco_obbligatorio_per_l_ente(db_url, server_proxy) -> None:
    account = await crea_account(db_url)
    async with await _collega(server_proxy, account, _v1("192.0.2.1")):
        pass  # elenco vuoto e facoltativo: ammesso (comportamento di prima)
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                      "motivo) VALUES ('ENTE', $1, 'SFTP_ALLOWLIST_OBBLIGATORIA', $2::jsonb, "
                      "'prova R10')", account.tenant_id, json.dumps("OBBLIGATORIA"))
    svuota_cache()
    with pytest.raises(asyncssh.PermissionDenied):
        async with await _collega(server_proxy, account, _v1("192.0.2.1")):
            pass


def test_v4_reti_fidate_troppo_ampie_fermano_l_avvio() -> None:
    from app.config import Settings, problemi_avvio

    assert any("più ampia di /20" in p for p in problemi_avvio(
        Settings(sftp_proxy_sorgenti_fidate=["10.0.0.0/16"])))
    assert not [p for p in problemi_avvio(Settings(
        sftp_proxy_sorgenti_fidate=["10.0.0.0/20", "10.0.16.0/20"])) if "SFTP_PROXY" in p]
