# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test Step6MetadatiAllegato5 — B-04 FIX regression.
Verifica che il validator controlli doc.parametri (MetadatiDocumento JSON)
invece del vecchio metadati_file XML.
"""
from __future__ import annotations

import pytest
from app.validators.base import ValidationContext
from app.validators.steps import Step6MetadatiAllegato5


def make_ctx(documenti: list, pdv_id: str = "pdv-test",
             tenant_id: str = "tenant-test") -> ValidationContext:
    return ValidationContext(
        pdv_id=pdv_id,
        tenant_id=tenant_id,
        indice={"classe_documentale": "fattura", "documenti": documenti},
    )


_COMPLETI = object()  # valore predefinito: metadati Allegato 5 completi


def _doc_principale(parametri: dict | None | object = _COMPLETI, **overrides) -> dict:
    """Documento PRINCIPALE con metadati Allegato 5 completi (default).

    ``parametri=None`` produce un documento SENZA il campo ``parametri``
    (prima ``None`` ricadeva sui metadati completi, così i test «senza
    parametri» provavano in realtà un documento completo e fallivano).
    """
    base: dict = {
        "id": "doc-001",
        "tipo": "PRINCIPALE",
        "nome_file": "fattura.pdf",
        "formato": "application/pdf",
        "hash": {"impronta": "a" * 64, "algoritmo": "SHA-256"},
    }
    base.update(overrides)
    if parametri is _COMPLETI:
        base["parametri"] = _parametri_completi()
    elif parametri is not None:
        base["parametri"] = parametri
    return base


def _parametri_completi(**overrides) -> dict:
    """Metadati Allegato 5 completi — tutti i 7 campi obbligatori."""
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


class TestStep6ConParametriCompleti:
    """Step6 deve passare con metadati Allegato 5 completi."""

    @pytest.mark.asyncio
    async def test_documento_principale_con_allegato5_completo(self):
        """Tutti i 7 campi obbligatori presenti → step OK."""
        ctx = make_ctx([_doc_principale()])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert r.ok, f"Step6 fallito con Allegato 5 completo: {r.messaggio}"
        assert r.dettagli.get("allegato5_validato") is True

    @pytest.mark.asyncio
    async def test_piu_documenti_tutti_con_allegato5(self):
        """Più documenti PRINCIPALE con Allegato 5 completo → step OK."""
        ctx = make_ctx([
            _doc_principale(id="doc-001", parametri=_parametri_completi(
                identificativo_documento="FT-001"
            )),
            _doc_principale(id="doc-002", tipo="ALLEGATO", parametri=None),
        ])
        r = await Step6MetadatiAllegato5().validate(ctx)
        assert r.ok


class TestStep6SenzaParametri:
    """Step6 deve fallire se doc PRINCIPALE manca di parametri."""

    @pytest.mark.asyncio
    async def test_principale_senza_parametri_fallisce(self):
        """B-04 regression: PRINCIPALE senza parametri → VLD-601."""
        ctx = make_ctx([_doc_principale(parametri=None)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-601"
        assert r.dettagli is not None
        assert len(r.dettagli["errori"]) > 0
        # Deve citare il campo 'parametri' nell'errore
        errore_msg = str(r.dettagli["errori"][0])
        assert "parametri" in errore_msg or "Allegato 5" in errore_msg

    @pytest.mark.asyncio
    async def test_allegato_senza_parametri_ok(self):
        """ALLEGATO senza parametri → step OK (non obbligatorio)."""
        doc_allegato = {
            "id": "doc-002",
            "tipo": "ALLEGATO",
            "nome_file": "allegato.pdf",
            "formato": "application/pdf",
            "hash": {"impronta": "b" * 64, "algoritmo": "SHA-256"},
        }
        ctx = make_ctx([_doc_principale(), doc_allegato])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert r.ok

    @pytest.mark.asyncio
    async def test_documenti_vuoti_non_genera_errori(self):
        """Indice senza documenti → step OK (nessun documento da validare)."""
        ctx = ValidationContext(
            pdv_id="pdv", tenant_id="t",
            indice={"classe_documentale": "fattura", "documenti": []},
        )
        r = await Step6MetadatiAllegato5().validate(ctx)
        assert r.ok


class TestStep6CampiObbligatori:
    """Ogni campo obbligatorio mancante deve generare un errore."""

    @pytest.mark.asyncio
    async def test_identificativo_documento_mancante(self):
        p = _parametri_completi()
        del p["identificativo_documento"]
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        errori = r.dettagli["errori"]
        assert any("identificativo_documento" in str(e) for e in errori)

    @pytest.mark.asyncio
    async def test_modalita_formazione_mancante(self):
        p = _parametri_completi()
        del p["modalita_formazione"]
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        assert any("modalita_formazione" in str(e) for e in r.dettagli["errori"])

    @pytest.mark.asyncio
    async def test_tipologia_documentale_mancante(self):
        p = _parametri_completi()
        del p["tipologia_documentale"]
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok

    @pytest.mark.asyncio
    async def test_data_documento_mancante(self):
        p = _parametri_completi()
        del p["data_documento"]
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        assert any("data_documento" in str(e) for e in r.dettagli["errori"])

    @pytest.mark.asyncio
    async def test_soggetto_produttore_id_mancante(self):
        p = _parametri_completi()
        del p["soggetto_produttore_id"]
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok

    @pytest.mark.asyncio
    async def test_soggetto_produttore_nome_mancante(self):
        p = _parametri_completi()
        del p["soggetto_produttore_nome"]
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok

    @pytest.mark.asyncio
    async def test_oggetto_mancante(self):
        p = _parametri_completi()
        del p["oggetto"]
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok

    @pytest.mark.asyncio
    async def test_campo_vuoto_equivale_a_mancante(self):
        """Un campo con stringa vuota è considerato mancante."""
        p = _parametri_completi(identificativo_documento="")
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok

    @pytest.mark.asyncio
    async def test_campo_solo_spazi_equivale_a_mancante(self):
        """Un campo con solo spazi è considerato vuoto."""
        p = _parametri_completi(soggetto_produttore_nome="   ")
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok

    @pytest.mark.asyncio
    async def test_oggetto_troppo_corto(self):
        """oggetto con meno di 5 caratteri → errore."""
        p = _parametri_completi(oggetto="AB")
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        assert any("oggetto" in str(e) for e in r.dettagli["errori"])


class TestStep6ModalitaFormazione:
    """Valori enum per modalita_formazione."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("modalita", ["a", "b", "c", "d"])
    async def test_tutte_le_modalita_valide(self, modalita: str):
        p = _parametri_completi(modalita_formazione=modalita)
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert r.ok, f"Modalità {modalita!r} dovrebbe essere valida"

    @pytest.mark.asyncio
    async def test_modalita_non_valida(self):
        p = _parametri_completi(modalita_formazione="VALORE_INESISTENTE")
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        assert any("modalita_formazione" in str(e) for e in r.dettagli["errori"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("modalita", ["A", "INFORMATICA_AB_ORIGINE", "SCANSIONE", "e"])
    async def test_valori_non_allegato5_rifiutati(self, modalita: str):
        """VD-C-037: solo le lettere a–d dell'Allegato 5 (LG §2.1.1); i vecchi
        valori inventati e le maiuscole sono rifiutati."""
        p = _parametri_completi(modalita_formazione=modalita)
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok


class TestStep6ErroriMultipli:
    """Più errori simultanei devono essere tutti riportati."""

    @pytest.mark.asyncio
    async def test_piu_campi_mancanti_tutti_segnalati(self):
        """Se mancano 3 campi, l'errore deve riportarli tutti."""
        p = {
            "identificativo_documento": "FT-001",
            # mancano: modalita_formazione, data_documento, soggetto_produttore_id,
            # soggetto_produttore_nome, oggetto (5 campi)
            "tipologia_documentale": "fattura",
        }
        ctx = make_ctx([_doc_principale(parametri=p)])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        assert len(r.dettagli["errori"]) >= 4

    @pytest.mark.asyncio
    async def test_due_documenti_principali_errori_separati(self):
        """Con due PRINCIPALE, gli errori devono essere per ciascuno."""
        doc1 = _doc_principale(id="doc-001", parametri=None)
        doc2 = {
            "id": "doc-002",
            "tipo": "PRINCIPALE",
            "nome_file": "altro.pdf",
            "formato": "application/pdf",
            "hash": {"impronta": "c" * 64, "algoritmo": "SHA-256"},
        }  # nessun parametri
        ctx = make_ctx([doc1, doc2])
        r   = await Step6MetadatiAllegato5().validate(ctx)
        assert not r.ok
        assert len(r.dettagli["errori"]) >= 2
        # un errore per ciascun documento, con il nome del file
        assert {e["doc"] for e in r.dettagli["errori"]} == {"fattura.pdf", "altro.pdf"}
