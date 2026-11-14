# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 b2 — gestione degli account SFTP (app/routers/sftp.py) su PostgreSQL reale.

Verifica: creazione con responsabile (stesse regole delle chiavi API), nome
utente unico fra enti (409 senza dire quale), chiavi pubbliche validate
(tipo, lunghezza RSA, coerenza tipo/contenuto) con impronta come ssh-keygen,
duplicato attivo 409, revoca senza eliminazione, sospensione e riattivazione
(con responsabile attivo), isolamento fra enti (404), permessi (VIEWER 403,
senza token 401), audit di ogni operazione nella stessa transazione.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from typing import Any

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from app.routers.sftp import analizza_chiave_pubblica
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from httpx import ASGITransport, AsyncClient

from libs.shared.errori import ErroreApi
from libs.shared.security.revoca import svuota_cache_revoche
from tests import infra_reale
from tests.infra_reale import richiede_pg, sql

#: fixture del database (copia del template migrato), registrata in questo modulo
db_url = infra_reale.db_url

TIPI = ["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
        "ssh-rsa"]


def _openssh(privata: Any, commento: str = "") -> str:
    riga = privata.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                             serialization.PublicFormat.OpenSSH).decode()
    return f"{riga} {commento}".strip()


def _ed25519(commento: str = "operatore@protocollo") -> str:
    return _openssh(ed25519.Ed25519PrivateKey.generate(), commento)


# ── Funzione pura di analisi della chiave ────────────────────────────

def test_analizza_chiave_ed25519_impronta_come_ssh_keygen() -> None:
    riga = _ed25519("mario@pc")
    a = analizza_chiave_pubblica(riga, tipi_ammessi=TIPI, rsa_bit_minimi=3072)
    blob = base64.b64decode(riga.split()[1])
    atteso = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    assert (a.tipo, a.bit, a.impronta, a.commento) == ("ssh-ed25519", 256, atteso, "mario@pc")
    assert a.chiave == " ".join(riga.split()[:2])


def test_analizza_chiave_ecdsa_e_rsa_3072_ammesse() -> None:
    a = analizza_chiave_pubblica(_openssh(ec.generate_private_key(ec.SECP384R1())),
                                 tipi_ammessi=TIPI, rsa_bit_minimi=3072)
    assert (a.tipo, a.bit) == ("ecdsa-sha2-nistp384", 384)
    r = analizza_chiave_pubblica(_openssh(rsa.generate_private_key(65537, 3072)),
                                 tipi_ammessi=TIPI, rsa_bit_minimi=3072)
    assert (r.tipo, r.bit, r.commento) == ("ssh-rsa", 3072, None)


@pytest.mark.parametrize("genera,codice", [
    (lambda: _openssh(rsa.generate_private_key(65537, 2048)), "SFTP-002"),
    (lambda: _openssh(dsa.generate_private_key(1024)), "SFTP-002"),  # noqa: S505 — chiave debole voluta
    (lambda: "ssh-ed25519", "SFTP-001"),
    (lambda: "ssh-ed25519 non-base64!!", "SFTP-001"),
    (lambda: _ed25519().replace("ssh-ed25519", "ssh-rsa", 1), "SFTP-001"),
    (lambda: _ed25519() + "\n" + _ed25519(), "SFTP-001"),
    (lambda: "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519xx").decode(),
     "SFTP-001"),
])
def test_analizza_chiave_non_valida_o_non_ammessa(genera, codice) -> None:
    with pytest.raises(ErroreApi) as exc:
        analizza_chiave_pubblica(genera(), tipi_ammessi=TIPI, rsa_bit_minimi=3072)
    assert exc.value.codice == codice


# ── API su PostgreSQL reale ──────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cache_revoche():
    svuota_cache_revoche()
    yield
    svuota_cache_revoche()


async def _ente(url: str) -> uuid.UUID:
    tid = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', 'a@b.it')",
              tid, tid.hex[:11], f"t_{tid.hex[:20]}")
    return tid


async def _utente(url: str, tid: uuid.UUID, ruolo: str, attivo: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, $3, 'Nome', 'Cognome', CAST($4 AS public.user_ruolo), $5)",
              uid, tid, f"{uid.hex[:8]}@x.it", ruolo, attivo)
    return uid


async def _chiama(metodo: str, percorso: str, *, tid: uuid.UUID, uid: uuid.UUID,
                  ruolo: str = "ADMIN_TENANT", corpo: Any = None,
                  token: str | None = None) -> tuple[int, Any]:
    tok = token if token is not None else create_access_token(
        tenant_id=str(tid), user_id=str(uid), ruolo=ruolo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.request(metodo, percorso, json=corpo,
                            headers={"Authorization": f"Bearer {tok}"} if tok else {})
    return r.status_code, (r.json() if r.content else None)


def _base(tid: uuid.UUID) -> str:
    return f"/api/v1/onboarding/tenant/{tid}/sftp/account"


async def _audit(url: str, tid: uuid.UUID) -> list[str]:
    righe = await sql(url, "SELECT evento FROM public.audit_global WHERE tenant_id = $1 "
                           "AND evento LIKE 'sftp.%' ORDER BY created_at", tid)
    return [r["evento"] for r in righe]


@richiede_pg
async def test_ciclo_completo_account_e_chiavi(db_url) -> None:
    tid = await _ente(db_url)
    admin = await _utente(db_url, tid, "ADMIN_TENANT")
    operatore = await _utente(db_url, tid, "OPERATORE")
    stato, acc = await _chiama("POST", _base(tid), tid=tid, uid=admin, corpo={
        "nome_utente": "comune.prova", "descrizione": "Protocollo", "user_id": str(operatore)})
    assert stato == 201, acc
    assert acc["stato"] == "ATTIVO" and acc["responsabile"]["id"] == str(operatore)
    assert acc["chiavi"] == [] and acc["azioni_consentite"]["sospendi"] is True
    aid = acc["id"]

    riga = _ed25519()
    stato, chiave = await _chiama("POST", f"{_base(tid)}/{aid}/chiavi", tid=tid, uid=admin,
                                  corpo={"chiave_pubblica": riga})
    assert stato == 201, chiave
    assert chiave["algoritmo"] == "ssh-ed25519" and chiave["impronta"].startswith("SHA256:")
    stato, dup = await _chiama("POST", f"{_base(tid)}/{aid}/chiavi", tid=tid, uid=admin,
                               corpo={"chiave_pubblica": riga})
    assert stato == 409 and dup["codice"] == "SFTP-003"

    stato, _ = await _chiama("DELETE", f"{_base(tid)}/{aid}/chiavi/{chiave['id']}", tid=tid,
                             uid=admin)
    assert stato == 204
    stato, _ = await _chiama("DELETE", f"{_base(tid)}/{aid}/chiavi/{chiave['id']}", tid=tid,
                             uid=admin)
    assert stato == 404
    # revocata resta registrata; la stessa chiave si può registrare di nuovo
    stato, _ = await _chiama("POST", f"{_base(tid)}/{aid}/chiavi", tid=tid, uid=admin,
                             corpo={"chiave_pubblica": riga})
    assert stato == 201
    stato, det = await _chiama("GET", f"{_base(tid)}/{aid}", tid=tid, uid=admin)
    assert [k["attiva"] for k in det["chiavi"]] == [False, True]

    stato, sosp = await _chiama("POST", f"{_base(tid)}/{aid}/sospendi", tid=tid, uid=admin)
    assert stato == 200 and sosp["stato"] == "SOSPESO" and sosp["sospeso_il"]
    stato, _ = await _chiama("POST", f"{_base(tid)}/{aid}/sospendi", tid=tid, uid=admin)
    assert stato == 200   # idempotente
    stato, riatt = await _chiama("POST", f"{_base(tid)}/{aid}/riattiva", tid=tid, uid=admin)
    assert stato == 200 and riatt["stato"] == "ATTIVO" and riatt["sospeso_il"] is None

    stato, mod = await _chiama("PUT", f"{_base(tid)}/{aid}", tid=tid, uid=admin,
                               corpo={"descrizione": "Nuovo sistema"})
    assert stato == 200 and mod["descrizione"] == "Nuovo sistema"
    stato, elenco = await _chiama("GET", _base(tid), tid=tid, uid=admin)
    assert stato == 200 and elenco["total"] == 1
    assert await _audit(db_url, tid) == [
        "sftp.account.creato", "sftp.chiave.aggiunta", "sftp.chiave.revocata",
        "sftp.chiave.aggiunta", "sftp.account.sospeso", "sftp.account.riattivato",
        "sftp.account.modificato"]


@richiede_pg
async def test_nome_utente_unico_fra_enti_409(db_url) -> None:
    t1, t2 = await _ente(db_url), await _ente(db_url)
    a1, a2 = await _utente(db_url, t1, "ADMIN_TENANT"), await _utente(db_url, t2, "ADMIN_TENANT")
    stato, _ = await _chiama("POST", _base(t1), tid=t1, uid=a1, corpo={"nome_utente": "condiviso"})
    assert stato == 201
    stato, corpo = await _chiama("POST", _base(t2), tid=t2, uid=a2,
                                 corpo={"nome_utente": "condiviso"})
    assert stato == 409 and corpo["codice"] == "SFTP-004"
    assert str(t1) not in str(corpo)


@richiede_pg
@pytest.mark.parametrize("nome", ["AB", "Maiuscole", "con spazio", "-inizio", "x" * 65])
async def test_nome_utente_non_valido_422(db_url, nome) -> None:
    tid = await _ente(db_url)
    admin = await _utente(db_url, tid, "ADMIN_TENANT")
    stato, corpo = await _chiama("POST", _base(tid), tid=tid, uid=admin,
                                 corpo={"nome_utente": nome})
    assert stato == 422 and corpo["codice"] == "VAL-422"


@richiede_pg
async def test_responsabile_stesse_regole_delle_chiavi_api(db_url) -> None:
    tid = await _ente(db_url)
    admin = await _utente(db_url, tid, "ADMIN_TENANT")
    super_admin = await _utente(db_url, tid, "SUPER_ADMIN")
    inattivo = await _utente(db_url, tid, "OPERATORE", attivo=False)
    altro_ente = await _utente(db_url, await _ente(db_url), "OPERATORE")
    for responsabile, atteso, codice in ((super_admin, 403, "AUTH-010"),
                                         (inattivo, 403, "AUTH-010"),
                                         (altro_ente, 404, "ONB-404")):
        stato, corpo = await _chiama("POST", _base(tid), tid=tid, uid=admin, corpo={
            "nome_utente": f"u{uuid.uuid4().hex[:8]}", "user_id": str(responsabile)})
        assert (stato, corpo["codice"]) == (atteso, codice)
    # senza user_id risponde chi crea (utente attivo dell'ente)
    stato, corpo = await _chiama("POST", _base(tid), tid=tid, uid=admin,
                                 corpo={"nome_utente": "senza.responsabile"})
    assert stato == 201 and corpo["responsabile"]["id"] == str(admin)
    assert len(await sql(db_url, "SELECT 1 FROM public.sftp_account WHERE tenant_id = $1",
                         tid)) == 1


@richiede_pg
async def test_riattiva_con_responsabile_inattivo_409(db_url) -> None:
    tid = await _ente(db_url)
    admin = await _utente(db_url, tid, "ADMIN_TENANT")
    op = await _utente(db_url, tid, "OPERATORE")
    _, acc = await _chiama("POST", _base(tid), tid=tid, uid=admin,
                           corpo={"nome_utente": "da.riattivare", "user_id": str(op)})
    await _chiama("POST", f"{_base(tid)}/{acc['id']}/sospendi", tid=tid, uid=admin)
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", op)
    stato, corpo = await _chiama("POST", f"{_base(tid)}/{acc['id']}/riattiva", tid=tid, uid=admin)
    assert stato == 409 and corpo["codice"] == "SFTP-409"
    # indicato un nuovo responsabile, la riattivazione riesce
    stato, _ = await _chiama("PUT", f"{_base(tid)}/{acc['id']}", tid=tid, uid=admin,
                             corpo={"user_id": str(admin)})
    assert stato == 200
    stato, corpo = await _chiama("POST", f"{_base(tid)}/{acc['id']}/riattiva", tid=tid, uid=admin)
    assert stato == 200 and corpo["stato"] == "ATTIVO"


@richiede_pg
async def test_isolamento_fra_enti_404_e_permessi(db_url) -> None:
    t1, t2 = await _ente(db_url), await _ente(db_url)
    a1, a2 = await _utente(db_url, t1, "ADMIN_TENANT"), await _utente(db_url, t2, "ADMIN_TENANT")
    _, acc = await _chiama("POST", _base(t1), tid=t1, uid=a1, corpo={"nome_utente": "ente.uno"})
    # l'amministratore di t2 non vede né tocca l'account di t1, nemmeno via il proprio ente
    for metodo, percorso in (("GET", f"{_base(t2)}/{acc['id']}"),
                             ("POST", f"{_base(t2)}/{acc['id']}/sospendi")):
        stato, corpo = await _chiama(metodo, percorso, tid=t2, uid=a2)
        assert stato == 404 and corpo["codice"] == "SFTP-404"
    stato, corpo = await _chiama("GET", f"{_base(t1)}/{acc['id']}", tid=t2, uid=a2)
    assert stato == 404 and corpo["codice"] == "ONB-404"
    stato, corpo = await _chiama("POST", f"{_base(t2)}/{acc['id']}/chiavi", tid=t2, uid=a2,
                                 corpo={"chiave_pubblica": _ed25519()})
    assert stato == 404
    viewer = await _utente(db_url, t1, "VIEWER")
    stato, corpo = await _chiama("GET", _base(t1), tid=t1, uid=viewer, ruolo="VIEWER")
    assert stato == 403 and corpo["codice"] == "AUTH-010"
    stato, _ = await _chiama("GET", _base(t1), tid=t1, uid=a1, token="")
    assert stato == 401
    stato, corpo = await _chiama("GET", f"{_base(t1)}/non-un-uuid", tid=t1, uid=a1)
    assert stato == 404 and corpo["codice"] == "SFTP-404"


@richiede_pg
async def test_chiave_non_ammessa_422_nessuna_riga(db_url) -> None:
    tid = await _ente(db_url)
    admin = await _utente(db_url, tid, "ADMIN_TENANT")
    _, acc = await _chiama("POST", _base(tid), tid=tid, uid=admin,
                           corpo={"nome_utente": "rsa.corta"})
    stato, corpo = await _chiama(
        "POST", f"{_base(tid)}/{acc['id']}/chiavi", tid=tid, uid=admin,
        corpo={"chiave_pubblica": _openssh(rsa.generate_private_key(65537, 2048))})
    assert stato == 422 and corpo["codice"] == "SFTP-002"
    assert await sql(db_url, "SELECT 1 FROM public.sftp_chiavi WHERE tenant_id = $1", tid) == []
    assert await _audit(db_url, tid) == ["sftp.account.creato"]


@richiede_pg
async def test_super_admin_gestisce_qualsiasi_ente(db_url) -> None:
    tid = await _ente(db_url)
    responsabile = await _utente(db_url, tid, "OPERATORE")
    stato, corpo = await _chiama("POST", _base(tid), tid=await _ente(db_url), uid=uuid.uuid4(),
                                 ruolo="SUPER_ADMIN",
                                 corpo={"nome_utente": "da.super", "user_id": str(responsabile)})
    assert stato == 201, corpo


@pytest.mark.parametrize("valore", ["ssh-ed25519, ecdsa-sha2-nistp256",
                                    '["ssh-ed25519", "ecdsa-sha2-nistp256"]'])
def test_tipi_chiave_ammessi_virgole_come_sftp_ingest_e_json(monkeypatch, valore) -> None:
    """R03 i1: stesso formato di sftp-ingest (virgole); il JSON resta accettato."""
    from app.routers.sftp import ImpostazioniSftp

    monkeypatch.setenv("SFTP_TIPI_CHIAVE_AMMESSI", valore)
    assert ImpostazioniSftp().sftp_tipi_chiave_ammessi == ["ssh-ed25519", "ecdsa-sha2-nistp256"]
