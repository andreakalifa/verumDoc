# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test F1-02 + B-04 + B-05: MetadatiDocumento Allegato 5 AGID.
Verifica validazione Pydantic e comportamento endpoint.
"""
from __future__ import annotations

import pytest
from app.models.versamento import (
    DocumentoIndice,
    HashInfo,
    IndiceUD,
    MetadatiDocumento,
    ModalitaFormazione,
)
from pydantic import ValidationError


def _rilievi_minimi(parametri: dict | None, tipo: str = "PRINCIPALE") -> dict[str, str]:
    """R03 (VD-C-094): la presenza dei 7 metadati minimi la controlla la regola RGL-101
    del motore delle regole (locked, blocca), non più lo schema Pydantic: così il
    rifiuto elenca TUTTI i mancanti. Restituisce {campo: effetto} dei rilievi RGL-101."""
    from datetime import UTC, datetime

    from app.services.versamento_service import dati_per_regole

    from libs.verumdoc_rules import (
        CATALOGO_V1,
        REGISTRO_V1,
        AmbienteRegole,
        ContestoRisoluzione,
        Fase,
        FornitoreDaDizionari,
        Risolutore,
    )

    indice = IndiceUD(classe_documentale="fattura", documenti=[DocumentoIndice(
        id="d1", tipo=tipo, nome_file="a.pdf", hash=HashInfo(impronta="a" * 64),
        parametri=parametri)])
    ambiente = AmbienteRegole(Risolutore(REGISTRO_V1, FornitoreDaDizionari()),
                              ContestoRisoluzione(adesso=datetime.now(UTC)))
    esito = CATALOGO_V1.valuta(dati_per_regole(indice), ambiente, Fase.INDICE)
    return {r.campo: r.effetto.value for r in esito.rilievi if r.regola == "RGL-101"}


class TestMetadatiDocumento:
    """Test validazione Pydantic MetadatiDocumento (7 campi obbligatori Allegato 5)."""

    def _base_meta(self, **overrides) -> dict:
        base = {
            "identificativo_documento": "FT-2026-001",
            "modalita_formazione": "a",
            "tipologia_documentale": "fattura",
            "data_documento": "2026-03-19",
            "soggetto_produttore_id": "12345678901",
            "soggetto_produttore_nome": "Acme S.r.l.",
            "oggetto": "Fattura servizi IT marzo 2026",
        }
        base.update(overrides)
        return base

    @pytest.mark.unit
    def test_campi_obbligatori_completi(self):
        """Tutti i 7 campi obbligatori → validazione OK."""
        meta = MetadatiDocumento(**self._base_meta())
        assert meta.identificativo_documento == "FT-2026-001"
        assert meta.modalita_formazione == ModalitaFormazione.CREAZIONE_SOFTWARE
        assert meta.tipologia_documentale == "fattura"
        assert meta.lingua == "it"  # default

    @pytest.mark.unit
    def test_identificativo_documento_obbligatorio(self):
        """Metadato minimo mancante sul principale → rilievo bloccante RGL-101."""
        d = self._base_meta()
        del d["identificativo_documento"]
        assert _rilievi_minimi(d) == {"identificativo_documento": "BLOCCA"}

    @pytest.mark.unit
    def test_modalita_formazione_obbligatoria(self):
        """Metadato minimo mancante sul principale → rilievo bloccante RGL-101."""
        d = self._base_meta()
        del d["modalita_formazione"]
        assert _rilievi_minimi(d) == {"modalita_formazione": "BLOCCA"}

    @pytest.mark.unit
    def test_modalita_formazione_valore_non_valido(self):
        with pytest.raises(ValidationError):
            MetadatiDocumento(**self._base_meta(modalita_formazione="ALTRO_VALORE"))

    @pytest.mark.unit
    def test_tutti_i_valori_modalita_validi(self):
        # LG AgID §2.1.1: lettere a)–d), stessa definizione di libs.shared.models
        valori_agid = ["a", "b", "c", "d"]
        for val in valori_agid:
            meta = MetadatiDocumento(**self._base_meta(modalita_formazione=val))
            assert meta.modalita_formazione.value == val

    @pytest.mark.unit
    @pytest.mark.parametrize("vecchio", ["INFORMATICA_AB_ORIGINE", "SCANSIONE",
                                         "COPIA_IMMAGINE", "TRASCRIZIONE", "A", "e"])
    def test_vecchi_valori_rifiutati(self, vecchio):
        """VD-C-037: i nomi pre-2026 non sono più accettati (cambio incompatibile)."""
        with pytest.raises(ValidationError):
            MetadatiDocumento(**self._base_meta(modalita_formazione=vecchio))

    @pytest.mark.unit
    def test_enum_e_quello_condiviso(self):
        from libs.shared.models import ModalitaFormazione as Condivisa
        assert ModalitaFormazione is Condivisa

    @pytest.mark.unit
    def test_data_documento_obbligatoria(self):
        """Metadato minimo mancante sul principale → rilievo bloccante RGL-101."""
        d = self._base_meta()
        del d["data_documento"]
        assert _rilievi_minimi(d) == {"data_documento": "BLOCCA"}

    @pytest.mark.unit
    def test_soggetto_produttore_id_obbligatorio(self):
        """Metadato minimo mancante sul principale → rilievo bloccante RGL-101."""
        d = self._base_meta()
        del d["soggetto_produttore_id"]
        assert _rilievi_minimi(d) == {"soggetto_produttore_id": "BLOCCA"}

    @pytest.mark.unit
    def test_soggetto_produttore_nome_obbligatorio(self):
        """Metadato minimo mancante sul principale → rilievo bloccante RGL-101."""
        d = self._base_meta()
        del d["soggetto_produttore_nome"]
        assert _rilievi_minimi(d) == {"soggetto_produttore_nome": "BLOCCA"}

    @pytest.mark.unit
    def test_oggetto_obbligatorio(self):
        """Metadato minimo mancante sul principale → rilievo bloccante RGL-101."""
        d = self._base_meta()
        del d["oggetto"]
        assert _rilievi_minimi(d) == {"oggetto": "BLOCCA"}

    @pytest.mark.unit
    def test_oggetto_troppo_corto(self):
        """oggetto deve avere almeno 5 caratteri."""
        with pytest.raises(ValidationError) as exc:
            MetadatiDocumento(**self._base_meta(oggetto="AB"))
        assert "oggetto" in str(exc.value)

    @pytest.mark.unit
    def test_oggetto_minimo_5_caratteri(self):
        """oggetto con esattamente 5 caratteri → OK."""
        meta = MetadatiDocumento(**self._base_meta(oggetto="12345"))
        assert meta.oggetto == "12345"

    @pytest.mark.unit
    def test_campi_opzionali_default(self):
        """I campi opzionali devono avere i default corretti."""
        meta = MetadatiDocumento(**self._base_meta())
        assert meta.lingua == "it"
        assert meta.classificazione is None
        assert meta.fascicolo_id is None
        assert meta.numero_registrazione is None
        assert meta.tipo_firma is None
        assert meta.soggetto_destinatario == []
        assert meta.codice_ipa is None
        assert meta.aoo is None
        assert meta.versione_documento == "1.0"
        assert meta.metadati_extra == {}

    @pytest.mark.unit
    def test_campi_opzionali_valorizzabili(self):
        """Tutti i campi opzionali devono essere accettati."""
        meta = MetadatiDocumento(**self._base_meta(
            lingua="en",
            classificazione="VII/3",
            fascicolo_id="FASC-2026-001",
            numero_registrazione="PROT-001",
            tipo_firma="CAdES-T",
            soggetto_destinatario=["destinatario1@mail.it"],
            codice_ipa="c_h501",
            aoo="AOO_CENTRALE",
            versione_documento="2.0",
            metadati_extra={"chiave": "valore"},
        ))
        assert meta.lingua == "en"
        assert meta.classificazione == "VII/3"
        assert meta.fascicolo_id == "FASC-2026-001"
        assert meta.numero_registrazione == "PROT-001"
        assert meta.tipo_firma == "CAdES-T"
        assert meta.codice_ipa == "c_h501"

    @pytest.mark.unit
    def test_serializzazione_model_dump(self):
        """model_dump() deve produrre un dict serializzabile in JSON."""
        import json
        meta = MetadatiDocumento(**self._base_meta())
        d = meta.model_dump()
        assert isinstance(d, dict)
        # Deve essere serializzabile in JSON (date come stringhe)
        json_str = json.dumps(d, default=str)
        assert "identificativo_documento" in json_str


class TestDocumentoIndiceConParametri:
    """Test DocumentoIndice con il campo parametri MetadatiDocumento."""

    @pytest.mark.unit
    def test_documento_senza_parametri_valido(self):
        """parametri è opzionale — DocumentoIndice senza parametri è valido."""
        doc = DocumentoIndice(
            id="doc-001",
            tipo="PRINCIPALE",
            nome_file="test.pdf",
            hash=HashInfo(impronta="a" * 64),
        )
        assert doc.parametri is None

    @pytest.mark.unit
    def test_documento_con_parametri_valido(self):
        """DocumentoIndice con parametri MetadatiDocumento completi."""
        doc = DocumentoIndice(
            id="doc-001",
            tipo="PRINCIPALE",
            nome_file="test.pdf",
            hash=HashInfo(impronta="a" * 64),
            parametri={
                "identificativo_documento": "FT-001",
                "modalita_formazione": "a",
                "tipologia_documentale": "fattura",
                "data_documento": "2026-03-19",
                "soggetto_produttore_id": "12345678901",
                "soggetto_produttore_nome": "Acme S.r.l.",
                "oggetto": "Fattura test",
            },
        )
        assert doc.parametri is not None
        assert isinstance(doc.parametri, MetadatiDocumento)
        assert doc.parametri.identificativo_documento == "FT-001"

    @pytest.mark.unit
    def test_documento_con_parametri_incompleti_tutti_i_mancanti_rilevati(self):
        """Parametri incompleti: lo schema li accetta, RGL-101 elenca i 6 mancanti insieme."""
        rilievi = _rilievi_minimi({"identificativo_documento": "FT-001"})
        assert set(rilievi) == {"modalita_formazione", "tipologia_documentale", "data_documento",
                                "soggetto_produttore_id", "soggetto_produttore_nome", "oggetto"}
        assert set(rilievi.values()) == {"BLOCCA"}

    @pytest.mark.unit
    def test_allegato_con_parametri_incompleti_nessun_rilievo(self):
        """I minimi si chiedono al documento principale (come la verifica asincrona VLD-601)."""
        assert _rilievi_minimi({"identificativo_documento": "FT-001"}, tipo="ALLEGATO") == {}

    @pytest.mark.unit
    def test_allegato_senza_parametri_valido(self):
        """Documento tipo ALLEGATO non richiede parametri."""
        doc = DocumentoIndice(
            id="doc-002",
            tipo="ALLEGATO",
            nome_file="allegato.pdf",
            hash=HashInfo(impronta="b" * 64),
        )
        assert doc.parametri is None
        assert doc.tipo == "ALLEGATO"


class TestIndiceUDConAllegato5:
    """Test IndiceUD completo con Allegato 5."""

    @pytest.mark.unit
    def test_indice_valido_con_allegato5(self):
        """IndiceUD con documento principale e Allegato 5 completo."""
        indice = IndiceUD(
            classe_documentale="fattura",
            chiave={"numero": "FT-001", "anno": "2026", "registro": "FATTURE"},
            documenti=[
                DocumentoIndice(
                    id="doc-001",
                    tipo="PRINCIPALE",
                    nome_file="fattura.pdf",
                    formato="application/pdf",
                    hash=HashInfo(impronta="a" * 64),
                    parametri={
                        "identificativo_documento": "FT-2026-001",
                        "modalita_formazione": "a",
                        "tipologia_documentale": "fattura",
                        "data_documento": "2026-03-19",
                        "soggetto_produttore_id": "12345678901",
                        "soggetto_produttore_nome": "Acme S.r.l.",
                        "oggetto": "Fattura servizi IT",
                    },
                )
            ],
        )
        assert len(indice.documenti) == 1
        assert indice.documenti[0].parametri is not None

    @pytest.mark.unit
    def test_indice_multi_documento_allegato_senza_parametri(self):
        """IndiceUD con PRINCIPALE (Allegato5) + ALLEGATO (senza parametri)."""
        indice = IndiceUD(
            classe_documentale="contratto",
            documenti=[
                DocumentoIndice(
                    id="doc-001", tipo="PRINCIPALE",
                    nome_file="contratto.pdf",
                    hash=HashInfo(impronta="a" * 64),
                    parametri={
                        "identificativo_documento": "CT-001",
                        "modalita_formazione": "a",
                        "tipologia_documentale": "contratto",
                        "data_documento": "2026-01-01",
                        "soggetto_produttore_id": "12345678901",
                        "soggetto_produttore_nome": "Acme S.r.l.",
                        "oggetto": "Contratto di servizio",
                    },
                ),
                DocumentoIndice(
                    id="doc-002", tipo="ALLEGATO",
                    nome_file="appendice.pdf",
                    hash=HashInfo(impronta="b" * 64),
                    # nessun parametri — valido per ALLEGATO
                ),
            ],
        )
        assert len(indice.documenti) == 2
        assert indice.documenti[0].parametri is not None
        assert indice.documenti[1].parametri is None


def test_r09_atto_di_rettifica_ammesso_solo_dal_canale_ciclo_vita() -> None:
    """R09 (D24): estensione e formato dell'atto XML di rettifica non dipendono dai
    formati della classe, ma solo con il canale CICLO_VITA."""
    from datetime import UTC, datetime

    from app.services.versamento_service import (
        RifiutoVersamento,
        controlla_estensioni,
        dati_per_regole,
    )

    from libs.verumdoc_rules import (
        REGISTRO_V1,
        AmbienteRegole,
        ContestoRisoluzione,
        FornitoreDaDizionari,
        Livello,
        Risolutore,
    )

    parametri = {"identificativo_documento": "rettifica-p1", "rettifica_di": "urn:x",
                 "pdv_rettificato": "p0", "pratica_rettifica": "p1",
                 "modalita_formazione": "a", "tipologia_documentale": "contratto",
                 "data_documento": "2015-03-01", "soggetto_produttore_id": "c",
                 "soggetto_produttore_nome": "Ente", "oggetto": "Rettifica"}
    indice = IndiceUD(classe_documentale="fattura", documenti=[DocumentoIndice(
        id="d1", tipo="PRINCIPALE", nome_file="atto_rettifica_p1.xml", formato="application/xml",
        hash=HashInfo(impronta="a" * 64), parametri=parametri)])
    ambiente = AmbienteRegole(
        Risolutore(REGISTRO_V1, FornitoreDaDizionari(
            {Livello.CONSERVATORE: {"FORMATI_AMMESSI": ["application/pdf"]}})),
        ContestoRisoluzione(adesso=datetime.now(UTC)))
    controlla_estensioni(indice, ambiente, "CICLO_VITA")
    with pytest.raises(RifiutoVersamento):
        controlla_estensioni(indice, ambiente, "REST")
    assert dati_per_regole(indice, canale="CICLO_VITA").documenti[0].atto_di_sistema
    assert not dati_per_regole(indice, canale="REST").documenti[0].atto_di_sistema
