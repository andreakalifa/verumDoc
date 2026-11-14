# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test OpenSearch mapping e search index."""
from app.services.search_index import INDEX_MAPPING


class TestIndexMapping:
    def test_mapping_ha_campi_obbligatori(self):
        props = INDEX_MAPPING["mappings"]["properties"]
        obbligatori = ["aip_id", "tenant_id", "classe_documentale",
                       "stato", "hash_sha256", "data_versamento"]
        for campo in obbligatori:
            assert campo in props, f"Campo {campo!r} mancante nel mapping"

    def test_aip_id_e_keyword(self):
        props = INDEX_MAPPING["mappings"]["properties"]
        assert props["aip_id"]["type"] == "keyword"

    def test_testo_completo_ha_analyzer_italian(self):
        props = INDEX_MAPPING["mappings"]["properties"]
        assert props["testo_completo"]["type"] == "text"
        assert props["testo_completo"]["analyzer"] == "italian"

    def test_date_fields_sono_date(self):
        props = INDEX_MAPPING["mappings"]["properties"]
        for campo in ["data_versamento", "data_conservazione", "scadenza_conservazione"]:
            assert props[campo]["type"] == "date", f"{campo} deve essere date"

    def test_settings_shards_e_replicas(self):
        s = INDEX_MAPPING["settings"]
        assert s["number_of_shards"] >= 1
        assert s["number_of_replicas"] >= 0
