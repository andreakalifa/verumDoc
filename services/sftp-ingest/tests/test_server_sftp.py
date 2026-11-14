# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Server SFTP vero (asyncssh) su PostgreSQL reale: autenticazione, chroot,
permessi di scrittura, collegamenti, quota, niente shell né inoltri.
"""
from __future__ import annotations

import asyncssh
import pytest

from tests.conftest import connetti, crea_account, registra_chiave
from tests.infra_reale import richiede_pg, sql

pytestmark = richiede_pg


async def test_accesso_chiave_registrata_vede_le_cartelle_standard(db_url, server_sftp,
                                                                   metriche) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        assert sorted(await sftp.listdir("/")) == [".", "..", "fatti", "in", "lavorazione",
                                                   "out", "scarti"]
        # chroot: risalire non porta fuori dall'area
        assert sorted(await sftp.listdir("/../..")) == sorted(await sftp.listdir("/"))
    assert metriche.accessi.labels("ok")._value.get() == 1


async def test_accesso_chiave_non_registrata_negato(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    estranea = asyncssh.generate_private_key("ssh-ed25519")
    with pytest.raises(asyncssh.PermissionDenied):
        async with connetti(porta, account, chiave=estranea):
            pass


async def test_accesso_utente_inesistente_negato_come_chiave_errata(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    account.nome_utente = "inesistente"
    with pytest.raises(asyncssh.PermissionDenied):
        async with connetti(porta, account):
            pass


async def test_accesso_con_password_non_offerto(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    with pytest.raises(asyncssh.PermissionDenied):
        async with asyncssh.connect("127.0.0.1", porta, username=account.nome_utente,
                                    password="qualsiasi",  # noqa: S106
                                    client_keys=None, known_hosts=None, agent_path=None):
            pass


@pytest.mark.parametrize("variante", ["account_sospeso", "ente_sospeso", "responsabile_inattivo",
                                      "chiave_revocata"])
async def test_accesso_negato_se_non_operativo(db_url, server_sftp, variante) -> None:
    porta, _ = server_sftp
    if variante == "account_sospeso":
        account = await crea_account(db_url, stato="SOSPESO")
    elif variante == "ente_sospeso":
        account = await crea_account(db_url, tenant_stato="SOSPESO")
    else:
        account = await crea_account(db_url)
        if variante == "responsabile_inattivo":
            await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1",
                      account.responsabile_id)
        else:
            await sql(db_url, "UPDATE public.sftp_chiavi SET revocata_il = now() "
                              "WHERE account_id = $1", account.id)
    with pytest.raises(asyncssh.PermissionDenied):
        async with connetti(porta, account):
            pass


async def test_accesso_rsa_corta_negato_anche_se_registrata(db_url, server_sftp) -> None:
    """La politica si ricontrolla all'accesso: una RSA 2048 registrata non entra."""
    porta, _ = server_sftp
    account = await crea_account(db_url, tipo_chiave="ssh-rsa", bit=2048)
    with pytest.raises(asyncssh.PermissionDenied):
        async with connetti(porta, account):
            pass


async def test_accesso_rsa_3072_ammessa(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url, tipo_chiave="ssh-rsa", bit=3072)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        assert "in" in await sftp.listdir("/")


async def test_shell_ed_exec_rifiutati(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn:
        with pytest.raises(asyncssh.ChannelOpenError):
            await conn.run("id", check=False)
        with pytest.raises(asyncssh.ChannelOpenError):
            await conn.create_connection(asyncssh.SSHTCPSession, "127.0.0.1", 5466)


async def test_scrittura_ammessa_solo_in_cartella_di_in(db_url, server_sftp, radice) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/PDV-1.tmp")
        async with sftp.open("/in/PDV-1.tmp/a.pdf", "wb") as f:
            await f.write(b"%PDF-1.7 prova")
        await sftp.rename("/in/PDV-1.tmp", "/in/PDV-1")
        assert (radice / str(account.id) / "in" / "PDV-1" / "a.pdf").read_bytes() == \
            b"%PDF-1.7 prova"
        for percorso in ("/a.pdf", "/in/a.pdf", "/out/x.json", "/lavorazione/x",
                         "/in/PDV-1/sotto/a.pdf"):
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                async with sftp.open(percorso, "wb"):
                    pass
        for cartella in ("/in/PDV-1/sotto", "/out/x", "/in/nome con spazi", "/in/.nascosta"):
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await sftp.mkdir(cartella)
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.rename("/in/PDV-1", "/lavorazione/PDV-1")
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.remove("/lavorazione/x")


async def test_collegamenti_rifiutati(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/P")
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.symlink("/etc/passwd", "/in/P/x")


async def test_rinomina_su_cartella_esistente_rifiutata(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/A.tmp")
        await sftp.mkdir("/in/A")
        with pytest.raises(asyncssh.SFTPError):
            await sftp.rename("/in/A.tmp", "/in/A")
        with pytest.raises(asyncssh.SFTPError):
            await sftp.posix_rename("/in/A.tmp", "/in/A")


async def test_quota_superata_rifiuta_la_scrittura(db_url, server_sftp, metriche) -> None:
    porta, contesto = server_sftp
    contesto.quota.limite_byte = 1000
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/Q")
        async with sftp.open("/in/Q/piccolo.bin", "wb") as f:
            await f.write(b"x" * 600)
        with pytest.raises(asyncssh.SFTPError) as exc:
            async with sftp.open("/in/Q/grande.bin", "wb") as f:
                await f.write(b"y" * 600)
        assert "Quota" in str(exc.value)
        # eliminando si libera spazio
        await sftp.remove("/in/Q/piccolo.bin")
        async with sftp.open("/in/Q/grande.bin", "wb") as f:
            await f.write(b"y" * 600)
    assert metriche.operazioni_negate.labels("quota")._value.get() == 1


async def test_setstat_cambia_solo_gli_orari(db_url, server_sftp, radice) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/T")
        async with sftp.open("/in/T/a.txt", "wb") as f:
            await f.write(b"a")
        await sftp.chmod("/in/T/a.txt", 0o777)
        await sftp.utime("/in/T/a.txt", (1_000_000, 1_000_000))
    st = (radice / str(account.id) / "in" / "T" / "a.txt").stat()
    assert st.st_mode & 0o777 == 0o640
    assert int(st.st_mtime) == 1_000_000


async def test_due_account_aree_separate(db_url, server_sftp, radice) -> None:
    porta, _ = server_sftp
    a = await crea_account(db_url)
    b = await crea_account(db_url)
    async with connetti(porta, a) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/SOLO-A")
    async with connetti(porta, b) as conn, conn.start_sftp_client() as sftp:
        assert await sftp.listdir("/in") in ([".", ".."], ["..", "."])
    assert (radice / str(a.id) / "in" / "SOLO-A").is_dir()


async def test_seconda_chiave_registrata_funziona(db_url, server_sftp) -> None:
    porta, _ = server_sftp
    account = await crea_account(db_url)
    seconda = asyncssh.generate_private_key("ecdsa-sha2-nistp256")
    await registra_chiave(db_url, account.id, account.tenant_id, seconda)
    async with connetti(porta, account, chiave=seconda) as conn, conn.start_sftp_client() as s:
        assert "in" in await s.listdir("/")
