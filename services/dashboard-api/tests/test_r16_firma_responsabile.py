# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R16 — firma PERSONALE con OTP del responsabile (VD-C-166): punto d'aggancio generico,
pagina di firma (API), credenziale personale, audit, protezioni del database.

Il servizio di firma è sostituito da un trasporto httpx che restituisce una busta CAdES
con il contenuto (la firma vera con il simulatore CSC è provata in
``services/signature/tests/test_r16_firma_personale.py``): qui si prova ciò che fa
dashboard-api (inoltro con il token della persona, controlli della busta, stato, audit) e
ciò che il DATABASE impone anche a chi saltasse l'API (trigger e diritti per colonna).
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from asn1crypto import algos, cms, core, x509
from cryptography import x509 as cx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import _asyncpg_dsn, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg
BASE = "/api/dashboard/firme-responsabile"
ENTE = uuid.UUID("00000000-0000-0000-0000-0000000000c0")


def _certificato() -> tuple[bytes, str]:
    chiave = ec.generate_private_key(ec.SECP256R1())
    nome = cx.Name([cx.NameAttribute(cx.oid.NameOID.COMMON_NAME, "Responsabile di prova")])
    adesso = datetime.now(UTC)
    cert = (cx.CertificateBuilder().subject_name(nome).issuer_name(nome)
            .public_key(chiave.public_key()).serial_number(cx.random_serial_number())
            .not_valid_before(adesso - timedelta(days=1))
            .not_valid_after(adesso + timedelta(days=30)).sign(chiave, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    return der, hashlib.sha256(der).hexdigest()


CERT_DER, CERT_SHA = _certificato()


def busta_cades(contenuto: bytes) -> bytes:
    """SignedData con il contenuto incluso e un firmatario (struttura, non crittografia)."""
    cert = x509.Certificate.load(CERT_DER)
    firmatario = cms.SignerInfo({
        "version": "v1",
        "sid": cms.SignerIdentifier({"issuer_and_serial_number": cms.IssuerAndSerialNumber(
            {"issuer": cert.issuer, "serial_number": cert.serial_number})}),
        "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
        "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_ecdsa"}),
        "signature": b"\x00" * 64})
    return cms.ContentInfo({"content_type": "signed_data", "content": cms.SignedData({
        "version": "v1", "digest_algorithms": [{"algorithm": "sha256"}],
        "encap_content_info": {"content_type": "data",
                               "content": core.OctetString(contenuto)},
        "certificates": [cert], "signer_infos": [firmatario]})}).dump()


class Firma:
    """Servizio di firma finto: registra le richieste, risponde come quello vero."""

    def __init__(self) -> None:
        self.richieste: list[tuple[str, str, dict]] = []
        self.credenziale_id = ""
        self.certificato = CERT_SHA
        self.contenuto_busta: bytes | None = None
        self.otp_valido = "246810"

    def __call__(self, richiesta: httpx.Request) -> httpx.Response:
        corpo = json.loads(richiesta.content or b"{}")
        self.richieste.append((richiesta.url.path, richiesta.headers.get("authorization", ""),
                               corpo))
        if richiesta.url.path.endswith("/otp"):
            return httpx.Response(200, json={"inviato": True, "fornitore": "simulatore-csc"})
        if corpo.get("otp") != self.otp_valido:
            return httpx.Response(422, json={"codice": "FIRMA-422-OTP", "detail": "OTP"})
        contenuto = base64.b64decode(corpo["contenuto_b64"])
        busta = busta_cades(self.contenuto_busta if self.contenuto_busta is not None
                            else contenuto)
        return httpx.Response(200, json={
            "busta_b64": base64.b64encode(busta).decode(), "livello": "B-T",
            "qualificata": True, "verificata": True, "firmatario": "Responsabile di prova",
            "certificato_sha256": self.certificato, "credenziale_id": self.credenziale_id,
            "impronta_sha256_contenuto": hashlib.sha256(contenuto).hexdigest(),
            "data_marca": datetime.now(UTC).isoformat(), "avvisi": []})


async def _persona(url: str, ruolo: str = "RSC_CONSERVATORE", *,
                   credenziale: bool = True, cert: str = CERT_SHA) -> tuple[str, str | None]:
    if not await sql(url, "SELECT 1 FROM public.tenants WHERE id = $1", ENTE):
        await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, "
                       "schema_name, stato, email_referente) VALUES ($1, 'cons0000000000c0', "
                       "'Conservatore di prova', 't_cons_c0', 'ATTIVO', 'c@example.invalid')",
                  ENTE)
    utente = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                   "VALUES ($1, $2, $3, 'Persona', 'Di prova', $4)",
              utente, ENTE, f"{utente.hex[:8]}@example.invalid", ruolo)
    cred = None
    if credenziale:
        cred = str((await sql(url, "INSERT INTO public.credenziali_firma_personali (user_id, "
                                   "fornitore_codice, credential_id, certificato_sha256) VALUES "
                                   "($1, 'simulatore-csc', 'responsabile-prova-1', $2) "
                                   "RETURNING id", utente, cert))[0]["id"])
    return str(utente), cred


def _token(utente: str, ruolo: str = "RSC_CONSERVATORE", **extra) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=str(ENTE), user_id=utente, ruolo=ruolo, **extra)}


@pytest.fixture
def firma_finta():
    from app.routers import firme_responsabile as rotte

    finto = Firma()
    rotte.TRASPORTO = httpx.MockTransport(finto)
    yield finto
    rotte.TRASPORTO = None


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _accoda(c: AsyncClient, h: dict, contenuto: bytes = b"<Nomina>di prova</Nomina>",
                  **kw) -> dict:
    r = await c.post(BASE, headers=h, json={
        "tipo": kw.get("tipo", "NOMINA"), "titolo": "Nomina del vicario di prova",
        "nome": "nomina_vicario.xml", "contenuto_b64": base64.b64encode(contenuto).decode(),
        **({"firmatario_atteso": kw["firmatario_atteso"]} if "firmatario_atteso" in kw else {})})
    assert r.status_code == 200, r.text
    return r.json()


async def test_firma_con_otp_end_to_end(db_url, firma_finta) -> None:  # noqa: F811
    utente, cred = await _persona(db_url)
    firma_finta.credenziale_id = cred
    h = _token(utente)
    async with _client() as c:
        doc = await _accoda(c, h)
        assert doc["stato"] == "DA_FIRMARE" and doc["nuovo"]
        assert not (await _accoda(c, h))["nuovo"]  # idempotente per (tipo, impronta)
        letto = await c.get(f"{BASE}/{doc['id']}/contenuto", headers=h)
        assert letto.content == b"<Nomina>di prova</Nomina>"
        r = await c.post(f"{BASE}/{doc['id']}/otp", headers=h)
        assert r.status_code == 200 and r.json()["inviato"]
        r = await c.post(f"{BASE}/{doc['id']}/firma", headers=h, json={"otp": "246810"})
        assert r.status_code == 200, r.text
        assert r.json()["stato"] == "FIRMATO"
        b = await c.get(f"{BASE}/{doc['id']}/busta", headers=h)
        assert b.status_code == 200 and b.headers["content-type"] == "application/pkcs7-mime"
        # seconda firma dello stesso documento: già firmato
        r = await c.post(f"{BASE}/{doc['id']}/firma", headers=h, json={"otp": "246810"})
        assert r.status_code == 409 and r.json()["codice"] == "GOV-409-FIRMA"
    # inoltro con il token DELLA PERSONA (il servizio di firma lo riverifica)
    assert all(a == h["Authorization"] for _, a, _ in firma_finta.richieste)
    assert "otp" not in json.dumps([c for p, _, c in firma_finta.richieste if p.endswith("/otp")])
    riga = (await sql(db_url, "SELECT * FROM public.firme_responsabile WHERE id = $1",
                      uuid.UUID(doc["id"])))[0]
    assert riga["stato"] == "FIRMATO" and str(riga["firmato_da"]) == utente
    assert riga["certificato_sha256"] == CERT_SHA and str(riga["credenziale_id"]) == cred
    assert hashlib.sha256(bytes(riga["busta"])).hexdigest() == riga["busta_sha256"]
    eventi = [r["evento"] for r in await sql(
        db_url, "SELECT evento, dettagli FROM public.audit_global WHERE evento LIKE "
                "'firma_responsabile.%'")]
    assert sorted(eventi) == sorted([
        "firma_responsabile.richiesta", "firma_responsabile.otp_richiesto",
        "firma_responsabile.tentativo", "firma_responsabile.firmato"])
    assert "246810" not in json.dumps([dict(r) for r in await sql(
        db_url, "SELECT dettagli::text FROM public.audit_global")])


async def test_otp_errato_resta_da_firmare_con_audit(db_url, firma_finta) -> None:  # noqa: F811
    utente, cred = await _persona(db_url)
    firma_finta.credenziale_id = cred
    h = _token(utente)
    async with _client() as c:
        doc = await _accoda(c, h)
        r = await c.post(f"{BASE}/{doc['id']}/firma", headers=h, json={"otp": "111111"})
        assert r.status_code == 422 and r.json()["codice"] == "FIRMA-422-OTP"
    assert (await sql(db_url, "SELECT stato FROM public.firme_responsabile WHERE id = $1",
                      uuid.UUID(doc["id"])))[0]["stato"] == "DA_FIRMARE"
    assert await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                             "'firma_responsabile.rifiutata'")


@pytest.mark.parametrize("difetto", ["contenuto", "certificato", "credenziale_altrui"])
async def test_busta_non_coerente_non_si_registra(db_url, firma_finta, difetto) -> None:  # noqa: F811
    """La busta di un altro documento è respinta dall'API; un certificato diverso da quello
    atteso o la credenziale di un'altra persona li respinge il TRIGGER del database."""
    utente, cred = await _persona(db_url)
    altro, cred_altro = await _persona(db_url)
    firma_finta.credenziale_id = cred
    if difetto == "contenuto":
        firma_finta.contenuto_busta = b"<Nomina>diversa</Nomina>"
    elif difetto == "certificato":
        firma_finta.certificato = "f" * 64
    else:
        firma_finta.credenziale_id = cred_altro
    h = _token(utente)
    async with _client() as c:
        doc = await _accoda(c, h)
        r = await c.post(f"{BASE}/{doc['id']}/firma", headers=h, json={"otp": "246810"})
        assert r.status_code in (502, 503), r.text
    assert (await sql(db_url, "SELECT stato FROM public.firme_responsabile WHERE id = $1",
                      uuid.UUID(doc["id"])))[0]["stato"] == "DA_FIRMARE"


async def test_firmatario_atteso_e_annullamento(db_url, firma_finta) -> None:  # noqa: F811
    utente, cred = await _persona(db_url)
    altro, _ = await _persona(db_url)
    firma_finta.credenziale_id = cred
    async with _client() as c:
        doc = await _accoda(c, _token(utente), firmatario_atteso=altro)
        r = await c.post(f"{BASE}/{doc['id']}/firma", headers=_token(utente),
                         json={"otp": "246810"})
        assert r.status_code == 409 and r.json()["codice"] == "GOV-409-FIRMA"
        assert not firma_finta.richieste  # nessun OTP consumato
        r = await c.post(f"{BASE}/{doc['id']}/annulla", headers=_token(altro),
                         json={"motivo": "testo da correggere"})
        assert r.status_code == 200 and r.json()["stato"] == "ANNULLATO"
        # annullato: si può accodare di nuovo lo stesso contenuto (nuova richiesta)
        assert (await _accoda(c, _token(utente)))["nuovo"]


async def test_in_firma_la_versione_stampabile_della_documentazione(db_url) -> None:  # noqa: F811
    """Revisione R17 (audit R17-04): va in firma l'HTML stampabile generato da R15, con le
    stesse impronte della versione immutabile; solo manuale e piano della sicurezza."""
    utente, _ = await _persona(db_url, "SUPER_ADMIN")
    h = _token(utente, "SUPER_ADMIN")
    doc_base = "/api/dashboard/documentazione-servizio"
    async with _client() as c:
        r = await c.post(f"{doc_base}/generazioni", headers=h)
        assert r.status_code == 202, r.text
        quadro = (await c.get(doc_base, headers=h)).json()
        ultime = {d["tipo"]: d["ultima"] for d in quadro["documenti"]}
        manuale = ultime["MANUALE_CONSERVAZIONE"]
        r = await c.post(f"{BASE}/documentazione/{manuale['id']}", headers=h)
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["tipo"] == "MANUALE" and doc["nome"].endswith(".html")
        assert doc["impronta_sha256"] == manuale["impronta_html"]
        assert f"versione {manuale['versione']}" in doc["titolo"]
        letto = await c.get(f"{BASE}/{doc['id']}/contenuto", headers=h)
        assert letto.headers["content-type"].startswith("text/html")
        assert "default-src 'none'" in letto.headers["content-security-policy"]
        html = await c.get(f"{doc_base}/versioni/{manuale['id']}/html", headers=h)
        assert letto.content == html.content
        r = await c.post(f"{BASE}/documentazione/{ultime['PIANO_SICUREZZA']['id']}", headers=h)
        assert r.status_code == 200 and r.json()["tipo"] == "PIANO_SICUREZZA"
        r = await c.post(f"{BASE}/documentazione/{ultime['GUIDA_FUNZIONALITA']['id']}",
                         headers=h)
        assert r.status_code == 422
        r = await c.post(f"{BASE}/documentazione/{uuid.uuid4()}", headers=h)
        assert r.status_code == 404


async def test_manuale_e_piani_li_firma_solo_il_responsabile(db_url, firma_finta) -> None:  # noqa: F811
    """Revisione R17 (sicurezza A4): un SUPER_ADMIN può accodare il manuale ma non firmarlo,
    neppure con una credenziale personale: serve il ruolo RSC_CONSERVATORE."""
    admin, cred = await _persona(db_url, "SUPER_ADMIN")
    firma_finta.credenziale_id = cred
    async with _client() as c:
        doc = await _accoda(c, _token(admin, "SUPER_ADMIN"), tipo="MANUALE")
        for azione, corpo in (("otp", None), ("firma", {"otp": "246810"})):
            r = await c.post(f"{BASE}/{doc['id']}/{azione}", headers=_token(admin, "SUPER_ADMIN"),
                             json=corpo)
            assert r.status_code == 409 and r.json()["codice"] == "GOV-409-FIRMA", r.text
        assert not firma_finta.richieste


@pytest.mark.parametrize("ruolo,extra", [("ADMIN_TENANT", {}), ("OPERATORE", {}),
                                          ("RSC_CONSERVATORE", {"rp": str(uuid.uuid4())})])
async def test_solo_persone_del_conservatore(db_url, ruolo, extra) -> None:  # noqa: F811
    utente, _ = await _persona(db_url, ruolo, credenziale=False)
    async with _client() as c:
        r = await c.get(BASE, headers=_token(utente, ruolo, **extra))
        assert r.status_code in (401, 403)


async def test_tipo_non_valido(db_url) -> None:  # noqa: F811
    utente, _ = await _persona(db_url)
    async with _client() as c:
        r = await c.post(BASE, headers=_token(utente), json={
            "tipo": "nomina minuscola", "titolo": "x", "nome": "x.xml",
            "contenuto_b64": base64.b64encode(b"x").decode()})
        assert r.status_code == 422


# ── protezioni del database (anche per chi salta l'API) ─────────────────

async def _connessione(url: str, gruppi: tuple[str, ...], nome: str) -> asyncpg.Connection:
    from libs.shared.db.ruolo_prova import assicura_ruolo_servizio, url_con_utente

    await assicura_ruolo_servizio(url, nome, gruppi, solo_gruppi=True)
    return await asyncpg.connect(_asyncpg_dsn(url_con_utente(url, nome)))


async def _documento(url: str) -> uuid.UUID:
    contenuto = uuid.uuid4().bytes
    return (await sql(url, "INSERT INTO public.firme_responsabile (tipo, titolo, nome, "
                           "contenuto, impronta_sha256) VALUES ('NOMINA', 't', 'n.xml', $1, $2) "
                           "RETURNING id", contenuto, hashlib.sha256(contenuto).hexdigest())
            )[0]["id"]


async def _firma_db(url: str, ident: uuid.UUID, utente: str, cred: str) -> bytes:
    busta = b"busta-" + ident.bytes
    await sql(url, "UPDATE public.firme_responsabile SET stato = 'FIRMATO', firmato_da = $2, "
                   "firmato_il = now(), credenziale_id = $3, busta = $4, busta_sha256 = $5, "
                   "certificato_sha256 = $6 WHERE id = $1", ident, uuid.UUID(utente),
              uuid.UUID(cred), busta, hashlib.sha256(busta).hexdigest(), CERT_SHA)
    return busta


async def test_trigger_passaggi_e_immutabilita(db_url) -> None:  # noqa: F811
    from libs.shared.firme_responsabile import chiave_worm

    utente, cred = await _persona(db_url)
    ident = await _documento(db_url)
    # una riga nuova non entra già firmata (§31 R13a (3))
    with pytest.raises(asyncpg.RaiseError, match="DA_FIRMARE"):
        await sql(db_url, "INSERT INTO public.firme_responsabile (tipo, titolo, nome, contenuto, "
                          "impronta_sha256, stato) VALUES ('NOMINA', 't', 'n', 'x', "
                          "encode(sha256('x'), 'hex'), 'FIRMATO')")
    with pytest.raises(asyncpg.RaiseError, match="impronta"):
        await sql(db_url, "INSERT INTO public.firme_responsabile (tipo, titolo, nome, contenuto, "
                          "impronta_sha256) VALUES ('NOMINA', 't', 'n', 'x', $1)", "0" * 64)
    # busta con impronta falsa
    with pytest.raises(asyncpg.RaiseError, match="busta"):
        await sql(db_url, "UPDATE public.firme_responsabile SET stato = 'FIRMATO', firmato_da = "
                          "$2, firmato_il = now(), credenziale_id = $3, busta = 'b', "
                          "busta_sha256 = $4, certificato_sha256 = $5 WHERE id = $1", ident,
                  uuid.UUID(utente), uuid.UUID(cred), "0" * 64, CERT_SHA)
    busta = await _firma_db(db_url, ident, utente, cred)
    for istruzione in ("UPDATE public.firme_responsabile SET contenuto = 'y' WHERE id = $1",
                       "UPDATE public.firme_responsabile SET busta = 'z' WHERE id = $1",
                       "UPDATE public.firme_responsabile SET stato = 'DA_FIRMARE' WHERE id = $1",
                       "DELETE FROM public.firme_responsabile WHERE id = $1"):
        with pytest.raises(asyncpg.RaiseError):
            await sql(db_url, istruzione, ident)
    with pytest.raises(asyncpg.RaiseError):
        await sql(db_url, "TRUNCATE public.firme_responsabile")
    # archiviazione: la chiave deve contenere le impronte del documento e della busta
    with pytest.raises(asyncpg.RaiseError, match="chiave WORM"):
        await sql(db_url, "UPDATE public.firme_responsabile SET stato = 'ARCHIVIATO', "
                          "busta_chiave = 'registro/altro.p7m', archiviato_il = now() "
                          "WHERE id = $1", ident)
    impronta = (await sql(db_url, "SELECT impronta_sha256 FROM public.firme_responsabile "
                                  "WHERE id = $1", ident))[0]["impronta_sha256"]
    await sql(db_url, "UPDATE public.firme_responsabile SET stato = 'ARCHIVIATO', "
                      "busta_chiave = $2, archiviato_il = now() WHERE id = $1", ident,
              chiave_worm("NOMINA", impronta, busta))
    with pytest.raises(asyncpg.RaiseError, match="finale"):
        await sql(db_url, "UPDATE public.firme_responsabile SET busta_chiave = 'x' WHERE id = $1",
                  ident)


async def test_trigger_credenziale_disattivata_o_certificato_diverso(db_url) -> None:  # noqa: F811
    utente, cred = await _persona(db_url)
    ident = await _documento(db_url)
    await sql(db_url, "UPDATE public.credenziali_firma_personali SET attiva = false "
                      "WHERE id = $1", uuid.UUID(cred))
    with pytest.raises(asyncpg.RaiseError, match="credenziale"):
        await _firma_db(db_url, ident, utente, cred)


async def test_diritti_per_colonna(db_url) -> None:  # noqa: F811
    from libs.shared.db.ruolo_prova import (
        GRUPPO_APPLICAZIONE,
        GRUPPO_FIRME_DOCUMENTI_SIGILLO,
        GRUPPO_FIRME_RESPONSABILE,
    )

    utente, cred = await _persona(db_url)
    ident = await _documento(db_url)
    api = await _connessione(db_url, (GRUPPO_APPLICAZIONE, GRUPPO_FIRME_RESPONSABILE),
                             "vd_prova_r16_dashboard")
    catena = await _connessione(db_url, (GRUPPO_APPLICAZIONE, GRUPPO_FIRME_DOCUMENTI_SIGILLO),
                                "vd_prova_r16_hashchain")
    servizi = await _connessione(db_url, (GRUPPO_APPLICAZIONE,), "vd_prova_r16_servizi")
    try:
        # chi non è nel gruppo non legge né scrive
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await servizi.fetch("SELECT 1 FROM public.firme_responsabile")
        # dashboard-api: niente colonne dell'archiviazione
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("UPDATE public.firme_responsabile SET busta_chiave = 'x' "
                              "WHERE id = $1", ident)
        # dashboard-api: non archivia né respinge (solo la hashchain)
        await _firma_db(db_url, ident, utente, cred)
        with pytest.raises(asyncpg.RaiseError, match="hashchain"):
            await api.execute("UPDATE public.firme_responsabile SET stato = 'RESPINTO' "
                              "WHERE id = $1", ident)
        # hashchain: niente colonne della firma
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await catena.execute("UPDATE public.firme_responsabile SET busta = 'x' "
                                 "WHERE id = $1", ident)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await catena.execute("INSERT INTO public.firme_responsabile (tipo, titolo, nome, "
                                 "contenuto, impronta_sha256) VALUES ('NOMINA', 't', 'n', 'x', "
                                 "encode(sha256('x'), 'hex'))")
        # nessuno cancella, nemmeno il gruppo della firma
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("DELETE FROM public.firme_responsabile WHERE id = $1", ident)
        # credenziali personali: dashboard-api legge, non scrive
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("UPDATE public.credenziali_firma_personali SET attiva = false")
    finally:
        for c in (api, catena, servizi):
            await c.close()


def test_passaggi_del_dominio_uguali_a_quelli_della_migrazione() -> None:
    import importlib.util
    from pathlib import Path

    from libs.shared.firme_responsabile import PASSAGGI, STATI

    percorso = (Path(__file__).resolve().parents[2] / "gateway" / "alembic" / "versions"
                / "20261110_057_residui.py")
    spec = importlib.util.spec_from_file_location("m057", percorso)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert set(modulo.PASSAGGI) == set(PASSAGGI) and tuple(modulo.STATI) == STATI
