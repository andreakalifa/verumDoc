# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test SInCRO Builder — tipologie DI, DAI, AGG (F2-01).
Verifica che ogni tipologia produca la struttura XML corretta
conforme a SInCRO 2020 (UNI 11386:2020) e LG AGID 2022.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest
from app.services.sincro_builder import (
    TIPOLOGIE_IPDA,
    MetadatiIPdANonValidi,
    build_sincro_xml,
    tipologia_ipda,
    verifica_metadati_ipda,
)

# ── Costanti di test ──────────────────────────────────────────────────

AIP_ID    = "aaaaaaaa-0000-0000-0000-000000000001"
PDV_ID    = "bbbbbbbb-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-000000000001"
NOW       = datetime(2026, 3, 19, 10, 0, 0, tzinfo=UTC)

ALLEGATO5_COMPLETO = {
    "identificativo_documento": "FT-2026-001",
    "modalita_formazione": "a",
    "tipologia_documentale": "fattura",
    "data_documento": "2026-03-19",
    "soggetto_produttore_id": "12345678901",
    "soggetto_produttore_nome": "Acme S.r.l.",
    "oggetto": "Fattura servizi IT marzo 2026",
    "lingua": "it",
}

DOCUMENTI_BASE = [
    {
        "id": "doc-001",
        "nome_file": "fattura.pdf",
        "tipo": "PRINCIPALE",
        "formato": "application/pdf",
        "hash_sha256": "a" * 64,
        "dimensione_bytes": 12345,
        "s3_key": "tenant/aip/files/fattura.pdf",
    }
]


def _parse_xml(xml_bytes: bytes) -> ET.Element:
    """
    Parsa il SInCRO XML e ritorna il root element.
    Il builder dichiara il namespace di default SInCRO (FIX-012): i tag si
    riportano al nome locale così i test verificano la struttura.
    """
    root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{"):
            el.tag = el.tag.split("}", 1)[1]
    return root


# ══════════════════════════════════════════════════════════════════════
# Test comuni a tutte le tipologie
# ══════════════════════════════════════════════════════════════════════

class TestSInCROBaseComune:
    """Test applicabili a tutte le tipologie DI/DAI/AGG."""

    @pytest.mark.parametrize("valore", ["DI", "DAI", "AGG", "dai", " AGG "])
    def test_tipologia_ipda_dalla_classe_valida(self, valore: str):
        """R03: la tipologia è il valore di classi_documentali.tipologia_xml."""
        assert tipologia_ipda(valore) == valore.strip().upper()
        assert tipologia_ipda(valore) in TIPOLOGIE_IPDA

    @pytest.mark.parametrize("valore", [None, "", "FATTURA", "XYZ"])
    def test_tipologia_ipda_assente_o_ignota_errore(self, valore):
        """Nessuna deduzione dal nome della classe: senza tipologia → errore."""
        with pytest.raises(MetadatiIPdANonValidi, match="Tipologia"):
            tipologia_ipda(valore)

    def test_tipologia_ipda_accetta_enum(self):
        from libs.shared.models import TipoSInCROXML

        assert tipologia_ipda(TipoSInCROXML.AGG) == "AGG"

    def test_build_senza_tipologia_errore(self):
        """Classe «fattura» senza tipologia: prima diventava DAI dal nome, ora errore."""
        with pytest.raises(MetadatiIPdANonValidi):
            build_sincro_xml(
                aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
                classe_documentale="fattura", documenti=DOCUMENTI_BASE,
                data_versamento=NOW, allegato5=ALLEGATO5_COMPLETO,
            )
        with pytest.raises(MetadatiIPdANonValidi):
            verifica_metadati_ipda("fattura", ALLEGATO5_COMPLETO, None)

    def test_nome_classe_non_decide_la_tipologia(self):
        """Una classe chiamata «fattura» configurata DI produce un IPdA DI."""
        assert verifica_metadati_ipda("fattura", None, "DI") == "DI"
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fascicolo", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI", allegato5=ALLEGATO5_COMPLETO,
        )
        assert _parse_xml(result).get("tipo") == "DAI"

    def test_build_sincro_ritorna_bytes(self):
        """build_sincro_xml deve ritornare bytes."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI", allegato5=ALLEGATO5_COMPLETO,
        )
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_xml_valido_parsabile(self):
        """Il SInCRO XML prodotto deve essere XML valido."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI", allegato5=ALLEGATO5_COMPLETO,
        )
        root = _parse_xml(result)
        assert root is not None

    def test_intestazione_presente(self):
        """Tutte le tipologie devono avere la sezione Intestazione."""
        for tipo in ["DI", "DAI", "AGG"]:
            result = build_sincro_xml(
                aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
                classe_documentale="fattura", documenti=DOCUMENTI_BASE,
                data_versamento=NOW, tipo_xml=tipo, allegato5=ALLEGATO5_COMPLETO,
            )
            root = _parse_xml(result)
            intestazione = root.find("Intestazione")
            assert intestazione is not None, f"Intestazione mancante in tipo {tipo}"

    def test_intestazione_contiene_aip_id(self):
        """L'Intestazione deve contenere l'IdentificativoAIP."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI", allegato5=ALLEGATO5_COMPLETO,
        )
        root   = _parse_xml(result)
        id_el  = root.find(".//IdentificativoAIP")
        assert id_el is not None and id_el.text == AIP_ID

    def test_intestazione_contiene_pdv_id(self):
        """L'Intestazione deve contenere l'IdentificativoPdV."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI", allegato5=ALLEGATO5_COMPLETO,
        )
        root  = _parse_xml(result)
        pdv_el = root.find(".//IdentificativoPdV")
        assert pdv_el is not None and pdv_el.text == PDV_ID

    def test_file_group_presente(self):
        """FileGroup con i file fisici deve essere presente in tutte le tipologie."""
        for tipo in ["DI", "DAI"]:
            result = build_sincro_xml(
                aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
                classe_documentale="fattura", documenti=DOCUMENTI_BASE,
                data_versamento=NOW, tipo_xml=tipo, allegato5=ALLEGATO5_COMPLETO,
            )
            root = _parse_xml(result)
            fg   = root.find(".//FileGroup")
            assert fg is not None, f"FileGroup mancante in tipo {tipo}"
            files = fg.findall("File")
            assert len(files) == len(DOCUMENTI_BASE)

    def test_file_group_contiene_hash_sha256(self):
        """Ogni File nel FileGroup deve avere l'Impronta SHA-256."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI", allegato5=ALLEGATO5_COMPLETO,
        )
        root   = _parse_xml(result)
        hashes = root.findall(".//Hash/Impronta")
        assert any(h.text == "a" * 64 for h in hashes)

    def test_file_group_senza_riferimento_storage_interno(self):
        """FIX-012: la chiave di storage interna NON compare nell'IPdA distribuibile."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI", allegato5=ALLEGATO5_COMPLETO,
        )
        root = _parse_xml(result)
        assert root.findall(".//RiferimentoStorage") == []
        assert DOCUMENTI_BASE[0]["s3_key"].encode() not in result

    def test_tipo_forzato_sovrascrive_auto_detect(self):
        """tipo_xml esplicito sovrascrive l'auto-detect."""
        # Classe "fattura" → DAI di default
        # Ma se forziamo DI, deve produrre DI
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DI",
        )
        root = _parse_xml(result)
        assert root.get("tipo") == "DI"


# ══════════════════════════════════════════════════════════════════════
# Test DI — Documento Informatico
# ══════════════════════════════════════════════════════════════════════

class TestSInCROTipoDI:
    """Test struttura DI (Documento Informatico)."""

    def _build_di(self, **kwargs) -> ET.Element:
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="altro", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DI", **kwargs,
        )
        return _parse_xml(result)

    def test_attributo_tipo_di(self):
        """Root element deve avere tipo='DI'."""
        root = self._build_di()
        assert root.get("tipo") == "DI"

    def test_ha_sezione_descrizione(self):
        """DI deve avere sezione Descrizione."""
        root = self._build_di()
        assert root.find("Descrizione") is not None

    def test_non_ha_sezione_registro(self):
        """DI NON deve avere sezione Registro (quella è solo per DAI)."""
        root = self._build_di()
        assert root.find("Registro") is None, (
            "DI non deve avere sezione Registro — è riservata a DAI"
        )

    def test_con_allegato5_ha_metadati_documento(self):
        """DI con Allegato 5 → MetadatiDocumento nella Descrizione."""
        root = self._build_di(allegato5=ALLEGATO5_COMPLETO)
        meta = root.find(".//MetadatiDocumento")
        assert meta is not None


# ══════════════════════════════════════════════════════════════════════
# Test DAI — Documento Amministrativo Informatico
# ══════════════════════════════════════════════════════════════════════

class TestSInCROTipoDAI:
    """Test struttura DAI (Documento Amministrativo Informatico)."""

    def _build_dai(self, allegato5=None, **kwargs) -> ET.Element:
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI",
            allegato5=allegato5 or ALLEGATO5_COMPLETO, **kwargs,
        )
        return _parse_xml(result)

    def test_attributo_tipo_dai(self):
        """Root element deve avere tipo='DAI'."""
        assert self._build_dai().get("tipo") == "DAI"

    def test_ha_sezione_registro(self):
        """DAI deve avere sezione Registro con dati protocollo."""
        root = self._build_dai()
        reg  = root.find("Registro")
        assert reg is not None, "DAI deve avere sezione Registro"

    def test_ha_sezione_firma(self):
        """DAI deve avere sezione Firma (anche stub)."""
        root = self._build_dai()
        firma = root.find("Firma")
        assert firma is not None, "DAI deve avere sezione Firma"

    def test_allegato5_in_descrizione(self):
        """DAI con Allegato 5 → MetadatiDocumento nella Descrizione."""
        root = self._build_dai()
        meta = root.find(".//MetadatiDocumento")
        assert meta is not None

    def test_allegato5_contiene_identificativo(self):
        """MetadatiDocumento deve contenere IdentificativoDocumento."""
        root = self._build_dai()
        id_doc = root.find(".//IdentificativoDocumento")
        assert id_doc is not None
        assert id_doc.text == ALLEGATO5_COMPLETO["identificativo_documento"]

    def test_allegato5_contiene_soggetto_produttore(self):
        """MetadatiDocumento deve contenere SoggettoProduttore."""
        root = self._build_dai()
        sp   = root.find(".//SoggettoProduttore")
        assert sp is not None
        denominazione = sp.find("Denominazione")
        assert denominazione is not None
        assert denominazione.text == ALLEGATO5_COMPLETO["soggetto_produttore_nome"]

    def test_dai_senza_allegato5_errore_nessun_segnaposto(self):
        """VD-C-037: DAI senza Allegato 5 → errore, nessuna modalità inventata."""
        with pytest.raises(MetadatiIPdANonValidi):
            build_sincro_xml(
                aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
                classe_documentale="fattura", documenti=DOCUMENTI_BASE,
                data_versamento=NOW, tipo_xml="DAI", allegato5=None,
            )
        with pytest.raises(MetadatiIPdANonValidi):
            verifica_metadati_ipda("fattura", None, "DAI")

    @pytest.mark.parametrize("lettera", ["a", "b", "c", "d"])
    def test_modalita_dal_metadato(self, lettera):
        root = self._build_dai(allegato5={**ALLEGATO5_COMPLETO, "modalita_formazione": lettera})
        assert root.find(".//ModalitaFormazione").text == lettera

    @pytest.mark.parametrize("valore", [None, "", "INFORMATICA_AB_ORIGINE", "SCANSIONE", "e"])
    def test_modalita_mancante_o_vecchia_errore(self, valore):
        meta = {**ALLEGATO5_COMPLETO, "modalita_formazione": valore}
        if valore is None:
            del meta["modalita_formazione"]
        with pytest.raises(MetadatiIPdANonValidi, match="modalita_formazione"):
            build_sincro_xml(
                aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
                classe_documentale="fattura", documenti=DOCUMENTI_BASE,
                data_versamento=NOW, tipo_xml="DAI", allegato5=meta,
            )
        with pytest.raises(MetadatiIPdANonValidi):
            verifica_metadati_ipda("fattura", meta, "DAI")

    def test_chiave_documentale_in_registro(self):
        """La ChiaveDocumentale deve essere nel DAI con i dati dell'IndiceUD."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fattura", documenti=DOCUMENTI_BASE,
            data_versamento=NOW, tipo_xml="DAI",
            allegato5=ALLEGATO5_COMPLETO,
            chiave={"numero": "FT-001", "anno": "2026", "registro": "FATTURE"},
        )
        root = _parse_xml(result)
        chiave = root.find(".//ChiaveDocumentale")
        assert chiave is not None
        assert chiave.find("Anno").text == "2026"
        assert chiave.find("Numero").text == "FT-001"


# ══════════════════════════════════════════════════════════════════════
# Test AGG — Aggregazione Documentale
# ══════════════════════════════════════════════════════════════════════

class TestSInCROTipoAGG:
    """Test struttura AGG (Aggregazione Documentale / Fascicolo)."""

    DOCUMENTI_MULTIPLI = [
        {
            "id": "doc-001", "nome_file": "contratto.pdf", "tipo": "PRINCIPALE",
            "formato": "application/pdf", "hash_sha256": "a" * 64,
            "dimensione_bytes": 10000, "s3_key": "t/a/contratto.pdf",
        },
        {
            "id": "doc-002", "nome_file": "allegato1.pdf", "tipo": "ALLEGATO",
            "formato": "application/pdf", "hash_sha256": "b" * 64,
            "dimensione_bytes": 5000, "s3_key": "t/a/allegato1.pdf",
        },
    ]

    def _build_agg(self, **kwargs) -> ET.Element:
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fascicolo",
            documenti=self.DOCUMENTI_MULTIPLI,
            data_versamento=NOW, tipo_xml="AGG", **kwargs,
        )
        return _parse_xml(result)

    def test_attributo_tipo_agg(self):
        """Root element deve avere tipo='AGG'."""
        assert self._build_agg().get("tipo") == "AGG"

    def test_ha_sezione_fascicolo(self):
        """AGG deve avere sezione Fascicolo (il contenitore gerarchico)."""
        root = self._build_agg()
        fasc = root.find("Fascicolo")
        assert fasc is not None, "AGG deve avere sezione Fascicolo"

    def test_fascicolo_ha_identificativo(self):
        """Fascicolo deve avere IdentificativoFascicolo."""
        root = self._build_agg()
        id_f = root.find(".//IdentificativoFascicolo")
        assert id_f is not None
        assert id_f.text  # non vuoto

    def test_fascicolo_ha_titolo(self):
        """Fascicolo deve avere TitoloFascicolo."""
        root = self._build_agg()
        tit  = root.find(".//TitoloFascicolo")
        assert tit is not None

    def test_ha_documenti_figlio(self):
        """AGG deve avere sezione DocumentiFiglio con i documenti."""
        root   = self._build_agg()
        figli  = root.find("DocumentiFiglio")
        assert figli is not None, "AGG deve avere sezione DocumentiFiglio"
        docs   = figli.findall("Documento")
        assert len(docs) == len(self.DOCUMENTI_MULTIPLI)

    def test_documenti_figlio_contengono_hash(self):
        """Ogni documento figlio deve avere l'Hash."""
        root  = self._build_agg()
        figli = root.findall(".//DocumentiFiglio/Documento")
        for figlio in figli:
            h = figlio.find("Hash")
            assert h is not None, "Documento figlio senza Hash"
            imp = h.find("Impronta")
            assert imp is not None and imp.text

    def test_fascicolo_id_personalizzabile(self):
        """Il fascicolo_id custom deve essere usato come IdentificativoFascicolo."""
        root = self._build_agg(fascicolo_id="FASC-2026-001")
        id_f = root.find(".//IdentificativoFascicolo")
        assert id_f.text == "FASC-2026-001"

    def test_titolo_fascicolo_personalizzabile(self):
        """Il titolo_fascicolo custom deve essere usato come TitoloFascicolo."""
        root = self._build_agg(titolo_fascicolo="Pratica contrattuale 2026")
        tit  = root.find(".//TitoloFascicolo")
        assert tit.text == "Pratica contrattuale 2026"

    def test_fascicolo_tipologia_agg_dalla_classe(self):
        """Classe con tipologia_xml AGG → IPdA AGG."""
        result = build_sincro_xml(
            aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
            classe_documentale="fascicolo",
            documenti=self.DOCUMENTI_MULTIPLI,
            data_versamento=NOW, tipo_xml="AGG",
        )
        root = _parse_xml(result)
        assert root.get("tipo") == "AGG"

    def test_pratica_senza_tipologia_non_diventa_agg(self):
        """Classe 'pratica' senza tipologia_xml: errore (prima AGG dal nome)."""
        with pytest.raises(MetadatiIPdANonValidi):
            build_sincro_xml(
                aip_id=AIP_ID, pdv_id=PDV_ID, tenant_id=TENANT_ID,
                classe_documentale="pratica",
                documenti=self.DOCUMENTI_MULTIPLI,
                data_versamento=NOW,
            )
