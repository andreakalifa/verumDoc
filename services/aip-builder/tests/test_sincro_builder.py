# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test SInCRO XML builder. Riferimento piano: F1.3"""
from app.services.sincro_builder import build_sincro_xml, compute_sincro_hash

# IPdA DAI (tipologia_xml della classe): Allegato 5 obbligatorio (VD-C-037)
A5 = {
    "identificativo_documento": "FT-1", "modalita_formazione": "a",
    "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
    "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme S.r.l.",
    "oggetto": "Fattura di prova",
}


class TestSinCROBuilder:
    def test_produce_xml_bytes(self, aip_id, pdv_id, tenant_id, documenti_list):
        xml = build_sincro_xml(aip_id, pdv_id, tenant_id,
                               "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        assert isinstance(xml, bytes)
        assert len(xml) > 100

    def test_contiene_aip_id(self, aip_id, pdv_id, tenant_id, documenti_list):
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        assert aip_id.encode() in xml

    def test_contiene_pdv_id(self, aip_id, pdv_id, tenant_id, documenti_list):
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        assert pdv_id.encode() in xml

    def test_contiene_classe_documentale(self, aip_id, pdv_id, tenant_id, documenti_list):
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        assert b"fattura" in xml

    def test_contiene_nome_file(self, aip_id, pdv_id, tenant_id, documenti_list):
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        assert b"doc.pdf" in xml

    def test_contiene_hash_documento(self, aip_id, pdv_id, tenant_id, documenti_list):
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        assert documenti_list[0]["hash_sha256"].encode() in xml

    def test_con_chiave_documentale(self, aip_id, pdv_id, tenant_id, documenti_list):
        chiave = {"anno": "2026", "numero": "001", "registro": "FATTURE"}
        xml = build_sincro_xml(aip_id, pdv_id, tenant_id, "fattura",
                               documenti_list, chiave=chiave, tipo_xml="DAI", allegato5=A5)
        assert b"2026" in xml
        assert b"FATTURE" in xml

    def test_sincro_hash_e_sha256(self, aip_id, pdv_id, tenant_id, documenti_list):
        import hashlib
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        h = compute_sincro_hash(xml)
        assert h == hashlib.sha256(xml).hexdigest()
        assert len(h) == 64

    def test_xml_valido(self, aip_id, pdv_id, tenant_id, documenti_list):
        import xml.etree.ElementTree as ET
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "fattura", documenti_list, tipo_xml="DAI", allegato5=A5)
        root = ET.fromstring(xml)
        assert root.tag.endswith("SInCRO")

    def test_piu_documenti(self, aip_id, pdv_id, tenant_id):
        import hashlib
        docs = [
            {"nome_file": f"doc{i}.pdf", "tipo": "PRINCIPALE" if i == 0 else "ALLEGATO",
             "formato": "application/pdf", "dimensione_bytes": 100,
             "hash_sha256": hashlib.sha256(f"content{i}".encode()).hexdigest()}
            for i in range(3)
        ]
        xml = build_sincro_xml(
            aip_id, pdv_id, tenant_id, "contratto", docs, tipo_xml="DAI", allegato5=A5)
        assert b"doc0.pdf" in xml
        assert b"doc2.pdf" in xml


class TestS3Hash:
    def test_compute_aip_hash(self, file_bytes):
        from app.services.s3_uploader import compute_aip_hash
        sha256, sha512 = compute_aip_hash(file_bytes)
        assert len(sha256) == 64
        assert len(sha512) == 128

    def test_hash_deterministico(self, file_bytes):
        from app.services.s3_uploader import compute_aip_hash
        h1 = compute_aip_hash(file_bytes)
        h2 = compute_aip_hash(file_bytes)
        assert h1 == h2

    def test_hash_diversi_per_contenuti_diversi(self):
        from app.services.s3_uploader import compute_aip_hash
        h1, _ = compute_aip_hash(b"content_a")
        h2, _ = compute_aip_hash(b"content_b")
        assert h1 != h2


class TestMetadatiAggiuntivi:
    """R03, VD-C-094: metadati del profilo, chiavi non previste e ``metadati_extra``
    entrano nell'IPdA (prima si perdevano); il resto del formato non cambia."""

    def _metadati(self, xml: bytes) -> dict[tuple[str, str], str]:
        import xml.etree.ElementTree as ET

        ns = {"s": "https://schemi.verumdoc.it/ipda/provvisorio/1"}
        radice = ET.fromstring(xml)
        return {(m.get("origine"), m.get("nome")): m.text for m in radice.findall(
            "s:Descrizione/s:MetadatiDocumento/s:MetadatiAggiuntivi/s:Metadato", ns)}

    def test_extra_e_non_previsti_emessi_con_origine(self, aip_id, pdv_id, tenant_id,
                                                     documenti_list):
        meta = {**A5, "importo": "1250.00", "pagata": True, "righe": [1, 2],
                "metadati_extra": {"numero_fattura": "12", "nota": None}}
        xml = build_sincro_xml(aip_id, pdv_id, tenant_id, "fattura", documenti_list,
                               tipo_xml="DAI", allegato5=meta)
        assert self._metadati(xml) == {
            ("parametri", "importo"): "1250.00", ("parametri", "pagata"): "true",
            ("parametri", "righe"): "[1,2]", ("metadati_extra", "numero_fattura"): "12",
        }

    def test_senza_metadati_aggiuntivi_nessun_elemento(self, aip_id, pdv_id, tenant_id,
                                                       documenti_list):
        xml = build_sincro_xml(aip_id, pdv_id, tenant_id, "fattura", documenti_list,
                               tipo_xml="DAI", allegato5={
                                   **A5, "lingua": "it", "metadati_extra": {}})
        assert b"MetadatiAggiuntivi" not in xml

    def test_ordine_stabile_a_parita_di_metadati(self, aip_id, pdv_id, tenant_id,
                                                 documenti_list):
        from datetime import UTC, datetime

        ora = datetime(2026, 10, 21, tzinfo=UTC)
        a = build_sincro_xml(aip_id, pdv_id, tenant_id, "fattura", documenti_list,
                             data_versamento=ora, tipo_xml="DAI", allegato5={
                                 **A5, "b": "2", "a": "1"})
        b = build_sincro_xml(aip_id, pdv_id, tenant_id, "fattura", documenti_list,
                             data_versamento=ora, tipo_xml="DAI", allegato5={
                                 **A5, "a": "1", "b": "2"})
        assert a == b

    def test_versione_documento_non_va_persa(self, aip_id, pdv_id, tenant_id, documenti_list):
        xml = build_sincro_xml(aip_id, pdv_id, tenant_id, "fattura", documenti_list,
                               tipo_xml="DAI", allegato5={**A5, "versione_documento": "2.0"})
        # R11 (D07): metadato dell'Allegato 5, emesso con il suo nome (VersioneDelDocumento)
        import xml.etree.ElementTree as ET

        ns = {"s": "https://schemi.verumdoc.it/ipda/provvisorio/1"}
        assert ET.fromstring(xml).findtext(
            "s:Descrizione/s:MetadatiDocumento/s:MetadatiAllegato5/s:VersioneDelDocumento",
            namespaces=ns) == "2.0"
        assert ("parametri", "versione_documento") not in self._metadati(xml)
