# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/aip-builder/tests/test_sprint2a_sincro_scadenza.py
Sprint 2A FIX-012: SInCRO XML namespace XSD conforme UNI 11386:2020.
Sprint 2A FIX-013: scadenza conservazione dalla classe documentale.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest
from app.services.sincro_builder import (
    NAMESPACE_STORICI,
    SINCRO_NS,
    build_sincro_xml,
)

from tests.infra_reale import db_url, richiede_pg, s3_server  # noqa: F401


class TestSInCRONamespaceXSD:
    """FIX-012 rivisto in R14 (VD-C-096): namespace PROPRIO del conservatore, dichiarato
    provvisorio; nessun URI inventato a nome di UNI o AgID, nessun schemaLocation verso un
    XSD che non esiste."""

    def _build_sample(self, tipo: str = "DI") -> bytes:
        return build_sincro_xml(
            aip_id="aip-test-012",
            pdv_id="pdv-test-012",
            tenant_id="t-012",
            classe_documentale="fattura" if tipo == "DAI" else "immagine",
            documenti=[{
                "id": "doc-001",
                "nome_file": "test.pdf",
                "tipo": "PRINCIPALE",
                "formato": "application/pdf",
                "hash_sha256": "a" * 64,
                "dimensione_bytes": 1024,
            }],
            tipo_xml=tipo,
            data_versamento=datetime(2026, 3, 20, tzinfo=UTC),
            allegato5={
                "identificativo_documento": "DOC-001",
                "modalita_formazione": "a",
                "tipologia_documentale": "fattura",
                "data_documento": "2026-03-20",
                "soggetto_produttore_id": "12345678901",
                "soggetto_produttore_nome": "Acme S.r.l.",
                "oggetto": "Test Sprint 2A",
            } if tipo == "DAI" else None,
        )

    def test_namespace_del_conservatore(self):
        """R14: namespace del conservatore, mai a nome di UNI o AgID."""
        assert SINCRO_NS == "https://schemi.verumdoc.it/ipda/provvisorio/1"
        assert "uni" not in SINCRO_NS.split("//")[1].split("/")[0]
        assert "agid" not in SINCRO_NS and SINCRO_NS not in NAMESPACE_STORICI

    def test_di_has_xmlns(self):
        """DI XML deve avere xmlns nel root element."""
        xml_bytes = self._build_sample("DI")
        xml_str = xml_bytes.decode("utf-8")
        assert f'xmlns="{SINCRO_NS}"' in xml_str
        assert "uni:uninfo" not in xml_str and "urn:agid" not in xml_str

    def test_dichiarato_provvisorio_senza_schema_location(self):
        """R14: nessun schemaLocation verso uno schema inesistente; un commento lo dichiara
        provvisorio e non conforme."""
        xml_str = self._build_sample("DI").decode("utf-8")
        assert "schemaLocation" not in xml_str
        assert "formato PROVVISORIO di VerumDoc" in xml_str
        assert "NON è lo schema UNI 11386:2020" in xml_str
        radice = ET.fromstring(xml_str.encode())
        assert radice.tag == f"{{{SINCRO_NS}}}SInCRO"
        assert radice.findtext(f"{{{SINCRO_NS}}}Intestazione/{{{SINCRO_NS}}}VersatoreSchema") \
            == f"{SINCRO_NS}#id-ente"

    def test_dai_has_xmlns(self):
        """DAI XML deve avere xmlns conforme."""
        xml_bytes = self._build_sample("DAI")
        xml_str = xml_bytes.decode("utf-8")
        assert SINCRO_NS in xml_str and "uni:uninfo" not in xml_str

    def test_agg_has_xmlns(self):
        """AGG XML deve avere xmlns conforme."""
        xml_bytes = build_sincro_xml(
            aip_id="aip-agg", pdv_id="pdv-agg", tenant_id="t-agg",
            classe_documentale="fascicolo",
            documenti=[{
                "id": "doc-agg", "nome_file": "f.pdf", "tipo": "PRINCIPALE",
                "formato": "application/pdf", "hash_sha256": "b" * 64,
                "dimensione_bytes": 512,
            }],
            tipo_xml="AGG",
            data_versamento=datetime(2026, 3, 20, tzinfo=UTC),
        )
        assert SINCRO_NS.encode() in xml_bytes and b"uni:uninfo" not in xml_bytes

    def test_no_s3_key_in_xml(self):
        """SInCRO XML NON deve contenere riferimenti S3 interni."""
        xml_bytes = self._build_sample("DI")
        xml_str = xml_bytes.decode("utf-8")
        assert "RiferimentoStorage" not in xml_str
        assert "s3_key" not in xml_str
        assert "verumdoc-aip" not in xml_str

    def test_xml_is_valid(self):
        """SInCRO XML deve essere parsabile."""
        for tipo in ("DI", "DAI", "AGG"):
            xml_bytes = self._build_sample(tipo) if tipo != "AGG" else build_sincro_xml(
                aip_id="a", pdv_id="p", tenant_id="t",
                classe_documentale="fascicolo",
                documenti=[{"id": "d", "nome_file": "f.pdf", "tipo": "PRINCIPALE",
                            "formato": "application/pdf", "hash_sha256": "c" * 64,
                            "dimensione_bytes": 100}],
                tipo_xml="AGG",
                data_versamento=datetime(2026, 1, 1, tzinfo=UTC),
            )
            # Deve parsare senza errori
            ET.fromstring(xml_bytes)

    def test_sincro_has_versione(self):
        """Root element deve avere attributo versione=2.0."""
        xml_bytes = self._build_sample("DI")
        root = ET.fromstring(xml_bytes)
        assert root.get("versione") == "2.0"


@pytest.fixture
def s3(s3_server, monkeypatch):
    """S3 (moto) con Object Lock per il PdA, bucket di transito per i file versati."""
    import uuid

    from app.config import settings
    from app.services import s3_uploader

    from tests.infra_reale import client_s3

    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    s3_uploader._client.cache_clear()
    client = client_s3(s3_server)
    client.create_bucket(Bucket="pdv-transito")
    yield client
    s3_uploader._client.cache_clear()


async def _costruisci_con_classe(url: str, s3, *, anni: int, permanente: bool):
    """Costruisce davvero un PdA (costruisci_pda) per un PdV della classe «fattura»
    configurata con ``anni``/``permanente``; restituisce (riga AIP, oggetto S3)."""
    import hashlib
    import uuid

    from app.config import settings
    from app.services.aip_engine import costruisci_pda

    from tests.infra_reale import crea_tenant_e_classe, sql

    tenant, classe = await crea_tenant_e_classe(url)
    await sql(url, "UPDATE public.classi_documentali SET anni_conservazione = $2, "
                   "conservazione_permanente = $3 WHERE id = $1", classe, anni, permanente)
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'prova', 'VALIDATO', $2, $3, $4)", pdv, classe, uuid.uuid4(),
                   tenant)
    pdf = b"%PDF-1.7\n%%EOF\n"
    chiave = f"{tenant}/{pdv}/0000-doc.pdf"
    s3.put_object(Bucket="pdv-transito", Key=chiave, Body=pdf)
    evento = {
        "pdv_id": str(pdv), "tenant_id": str(tenant),
        "indice": {"classe_documentale": "fattura", "chiave": None, "documenti": [{
            "id": "d1", "tipo": "PRINCIPALE", "nome_file": "doc.pdf", "formato": "application/pdf",
            "hash": {"algoritmo": "SHA-256", "impronta": hashlib.sha256(pdf).hexdigest()}}]},
        "docs_s3_keys": [{"doc_id": "d1", "nome_file": "doc.pdf", "s3_key": chiave,
                          "s3_bucket": "pdv-transito", "size_bytes": len(pdf)}],
        "allegato5": {"d1": {
            "identificativo_documento": "FT-1", "modalita_formazione": "b",
            "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
            "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme S.r.l.",
            "oggetto": "Fattura di prova"}},
    }

    uscita = await costruisci_pda(evento, adesso=ADESSO)
    riga = (await sql(url, "SELECT anni_conservazione, scadenza_conservazione, retention_fino_a "
                           "FROM public.aip "
                           "WHERE id = $1", uuid.UUID(uscita["aip_id"])))[0]
    doc = (await sql(url, "SELECT s3_key FROM public.documenti WHERE aip_id = $1",
                     uuid.UUID(uscita["aip_id"])))[0]
    testa = s3.head_object(Bucket=settings.s3_bucket_aip, Key=doc["s3_key"])
    return riga, testa


ADESSO = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@richiede_pg
class TestScadenzaDaClasse:
    """FIX-013: scadenza conservazione letta dalla classe documentale.

    g3-test (VD-C-030): prima i tre test cercavano nel sorgente di aip_engine
    le parole «classi_documentali», «999», «permanente» e l'assenza di
    «timedelta(days=365 * 10)». Ora costruiscono davvero il PdA su PostgreSQL
    reale e S3 (moto, Object Lock) e misurano scadenza e retention prodotte.
    """

    async def test_aip_engine_uses_classi_documentali(self, db_url, s3):
        """Classe a 7 anni → anni 7, scadenza = versamento + 7 anni, retention WORM uguale."""
        riga, testa = await _costruisci_con_classe(db_url, s3, anni=7, permanente=False)
        attesa = ADESSO.replace(year=ADESSO.year + 7)
        assert riga["anni_conservazione"] == 7
        assert riga["scadenza_conservazione"] == attesa
        assert testa["ObjectLockMode"] == "COMPLIANCE"
        assert testa["ObjectLockRetainUntilDate"] == attesa
        assert testa["ObjectLockLegalHoldStatus"] == "OFF"

    async def test_aip_engine_no_hardcoded_10(self, db_url, s3):
        """Classi diverse danno scadenze diverse: nessun valore fisso a 10 anni."""
        riga3, _ = await _costruisci_con_classe(db_url, s3, anni=3, permanente=False)
        assert riga3["anni_conservazione"] == 3
        assert riga3["scadenza_conservazione"] == ADESSO.replace(year=ADESSO.year + 3)

    async def test_aip_engine_handles_permanent(self, db_url, s3, monkeypatch):
        """Classe permanente: anni 999, nessuna scadenza, legal hold ON e retention fino
        all'orizzonte configurato; senza orizzonte nessun PdA (nessun valore inventato)."""
        from app.config import settings
        from app.services.aip_engine import costruisci_pda  # noqa: F401

        from libs.shared.storage.worm import RetentionNonDeterminabile

        monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", 50)
        riga, testa = await _costruisci_con_classe(db_url, s3, anni=10, permanente=True)
        assert riga["anni_conservazione"] == 999
        assert riga["scadenza_conservazione"] is None
        assert testa["ObjectLockLegalHoldStatus"] == "ON"
        assert testa["ObjectLockRetainUntilDate"] == ADESSO.replace(year=ADESSO.year + 50)
        # revisione R09 (Z5): il termine del blocco WORM è nella riga dal primo giorno
        assert riga["retention_fino_a"] == testa["ObjectLockRetainUntilDate"]

        # R09 (D04, VD-C-070): senza valore d'ambiente vale il parametro
        # RETENTION_PERMANENTE_ANNI del motore (predefinito 30, rinnovo automatico)
        from libs.shared.parametri import svuota_cache

        svuota_cache()
        monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", None)
        _, testa = await _costruisci_con_classe(db_url, s3, anni=10, permanente=True)
        assert testa["ObjectLockRetainUntilDate"] == ADESSO.replace(year=ADESSO.year + 30)
        assert RetentionNonDeterminabile  # resta per le classi senza anni
