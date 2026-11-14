# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R16 (VD-C-166) — archiviazione nel WORM delle firme PERSONALI del responsabile, su
PostgreSQL vero e S3 (moto con Object Lock).

  - documento FIRMATO coerente → riverifica (contenuto della busta, credenziale, verifica
    del servizio di firma) → busta nel WORM con l'impronta nella chiave, riga ARCHIVIATO;
  - ripresa dopo un crash fra WORM e UPDATE: l'oggetto identico si ritrova (idempotente);
  - credenziale disattivata dopo la firma, busta con un altro contenuto, verifica non
    valida o certificato diverso → RESPINTO, niente nel WORM;
  - servizio di firma non disponibile → nuovo tentativo, nessun rifiuto;
  - fencing: senza il lease del registro nessuna scrittura.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.registro import archivio, firme_responsabile
from app.services import block_sealer
from asn1crypto import algos, cms, core, x509
from cryptography import x509 as cx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from libs.shared.db import close_db, get_async_session, init_db
from libs.shared.firme_responsabile import chiave_worm
from libs.shared.parametri import svuota_cache
from tests.infra_reale import client_s3, db_url, richiede_pg, s3_server, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

ADESSO = datetime.now(UTC).replace(microsecond=0)
IO = "detentore-r16"


def _cert() -> tuple[bytes, str]:
    chiave = ec.generate_private_key(ec.SECP256R1())
    nome = cx.Name([cx.NameAttribute(cx.oid.NameOID.COMMON_NAME, "Responsabile di prova")])
    c = (cx.CertificateBuilder().subject_name(nome).issuer_name(nome)
         .public_key(chiave.public_key()).serial_number(cx.random_serial_number())
         .not_valid_before(ADESSO - timedelta(days=1))
         .not_valid_after(ADESSO + timedelta(days=30)).sign(chiave, hashes.SHA256()))
    der = c.public_bytes(serialization.Encoding.DER)
    return der, hashlib.sha256(der).hexdigest()


CERT_DER, CERT_SHA = _cert()


def _busta(contenuto: bytes) -> bytes:
    cert = x509.Certificate.load(CERT_DER)
    si = cms.SignerInfo({
        "version": "v1",
        "sid": cms.SignerIdentifier({"issuer_and_serial_number": cms.IssuerAndSerialNumber(
            {"issuer": cert.issuer, "serial_number": cert.serial_number})}),
        "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
        "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_ecdsa"}),
        "signature": b"\x01" * 64})
    return cms.ContentInfo({"content_type": "signed_data", "content": cms.SignedData({
        "version": "v1", "digest_algorithms": [{"algorithm": "sha256"}],
        "encap_content_info": {"content_type": "data", "content": core.OctetString(contenuto)},
        "certificates": [cert], "signer_infos": [si]})}).dump()


class VerificaFinta:
    def __init__(self, esito: str = "valida", certificato: str = CERT_SHA,
                 guasto: bool = False) -> None:
        self.esito, self.certificato, self.guasto = esito, certificato, guasto
        self.verificate: list[bytes] = []

    async def verifica(self, busta: bytes, contenuto: bytes | None = None) -> dict:
        self.verificate.append(busta)
        if self.guasto:
            import httpx

            raise httpx.ConnectError("servizio di firma non raggiungibile")
        return {"esito": self.esito, "certificato_sha256": self.certificato, "motivi": []}


@pytest.fixture
def s3(s3_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", 30)
    block_sealer._client.cache_clear()
    client = client_s3(s3_server)
    client.create_bucket(Bucket=settings.s3_bucket_aip, ObjectLockEnabledForBucket=True)
    yield client
    block_sealer._client.cache_clear()


@pytest.fixture
async def db(db_url):  # noqa: F811
    await init_db(db_url)
    svuota_cache()
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, ADESSO, ADESSO + timedelta(hours=1))
    yield db_url
    svuota_cache()
    await close_db()


async def _firmato(url: str, *, contenuto_busta: bytes | None = None) -> tuple[uuid.UUID, bytes]:
    ente = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Conservatore di prova', $3, "
                   "'ATTIVO', 'c@example.invalid')", ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    utente = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) VALUES "
                   "($1, $2, $3, 'Persona', 'Di prova', 'RSC_CONSERVATORE')", utente, ente,
              f"{utente.hex[:8]}@example.invalid")
    cred = (await sql(url, "INSERT INTO public.credenziali_firma_personali (user_id, "
                           "fornitore_codice, credential_id, certificato_sha256) VALUES ($1, "
                           "'simulatore-csc', 'responsabile-prova-1', $2) RETURNING id",
                      utente, CERT_SHA))[0]["id"]
    contenuto = b'{"manuale": "' + uuid.uuid4().hex.encode() + b'"}'
    ident = (await sql(url, "INSERT INTO public.firme_responsabile (tipo, titolo, nome, "
                            "contenuto, impronta_sha256) VALUES ('MANUALE', 'Manuale', "
                            "'manuale_conservazione.json', $1, $2) RETURNING id", contenuto,
                       hashlib.sha256(contenuto).hexdigest()))[0]["id"]
    busta = _busta(contenuto_busta if contenuto_busta is not None else contenuto)
    await sql(url, "UPDATE public.firme_responsabile SET stato = 'FIRMATO', firmato_da = $2, "
                   "firmato_il = now(), credenziale_id = $3, busta = $4, busta_sha256 = $5, "
                   "certificato_sha256 = $6 WHERE id = $1", ident, utente, cred, busta,
              hashlib.sha256(busta).hexdigest(), CERT_SHA)
    return ident, busta


async def _riga(url: str, ident: uuid.UUID):  # type: ignore[no-untyped-def]
    return (await sql(url, "SELECT * FROM public.firme_responsabile WHERE id = $1", ident))[0]


async def test_firma_coerente_archiviata_nel_worm(db, s3) -> None:
    ident, busta = await _firmato(db)
    servizio = VerificaFinta()
    esito = await firme_responsabile.archivia(servizio, detentore=IO, adesso=ADESSO)
    assert esito == {"archiviati": 1, "respinti": 0, "rinviati": 0}
    r = await _riga(db, ident)
    assert r["stato"] == "ARCHIVIATO" and servizio.verificate == [busta]
    assert r["busta_chiave"] == chiave_worm("MANUALE", r["impronta_sha256"], busta)
    assert r["busta_chiave"].startswith("registro/documenti/responsabile/manuale/")
    oggetto = s3.get_object(Bucket=settings.s3_bucket_aip, Key=r["busta_chiave"])
    assert oggetto["Body"].read() == busta and oggetto["ObjectLockMode"] == "COMPLIANCE"


async def test_ripresa_dopo_crash_fra_worm_e_update(db, s3) -> None:
    """La busta è già nel WORM (scritta prima di un crash): la ripresa la ritrova identica."""
    ident, busta = await _firmato(db)
    r = await _riga(db, ident)
    s3.put_object(Bucket=settings.s3_bucket_aip, Key=chiave_worm("MANUALE", r["impronta_sha256"],
                                                                  busta), Body=busta)
    esito = await firme_responsabile.archivia(VerificaFinta(), detentore=IO, adesso=ADESSO)
    assert esito["archiviati"] == 1 and (await _riga(db, ident))["stato"] == "ARCHIVIATO"


@pytest.mark.parametrize("difetto", ["credenziale", "contenuto", "verifica", "certificato"])
async def test_riverifica_che_non_torna_respinge(db, s3, difetto) -> None:
    ident, _ = await _firmato(db, contenuto_busta=b"altro" if difetto == "contenuto" else None)
    servizio = VerificaFinta()
    if difetto == "credenziale":
        await sql(db, "UPDATE public.credenziali_firma_personali SET attiva = false")
    elif difetto == "verifica":
        servizio.esito = "non_valida"
    elif difetto == "certificato":
        servizio.certificato = "e" * 64
    esito = await firme_responsabile.archivia(servizio, detentore=IO, adesso=ADESSO)
    assert esito["respinti"] == 1 and esito["archiviati"] == 0
    r = await _riga(db, ident)
    assert r["stato"] == "RESPINTO" and r["motivo"] and r["busta_chiave"] is None
    assert "Contents" not in s3.list_objects_v2(Bucket=settings.s3_bucket_aip,
                                                Prefix="registro/documenti/responsabile/")
    assert await firme_responsabile.conta_respinte() == 1


async def test_servizio_di_firma_non_disponibile_si_ritenta(db, s3) -> None:
    ident, _ = await _firmato(db)
    esito = await firme_responsabile.archivia(VerificaFinta(guasto=True), detentore=IO,
                                              adesso=ADESSO)
    assert esito["rinviati"] == 1
    r = await _riga(db, ident)
    assert r["stato"] == "FIRMATO" and r["tentativi"] == 1 and r["prossimo_tentativo_il"]
    # prima della scadenza dell'attesa non si riprende
    assert (await firme_responsabile.archivia(VerificaFinta(), detentore=IO, adesso=ADESSO)
            )["archiviati"] == 0
    dopo = ADESSO + timedelta(hours=1)
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, dopo, dopo + timedelta(hours=1))
    assert (await firme_responsabile.archivia(VerificaFinta(), detentore=IO, adesso=dopo)
            )["archiviati"] == 1


async def test_senza_lease_nessuna_scrittura(db, s3) -> None:
    ident, _ = await _firmato(db)
    with pytest.raises(Exception):  # noqa: B017 — lease perso: la transazione si annulla
        await firme_responsabile.archivia(VerificaFinta(), detentore="altro-processo",
                                          adesso=ADESSO)
    assert (await _riga(db, ident))["stato"] == "FIRMATO"
