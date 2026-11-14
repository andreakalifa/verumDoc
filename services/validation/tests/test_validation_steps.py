# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test dei 9 step di validazione AGID.
Riferimento piano: F1.2
"""
from __future__ import annotations

import hashlib

import pytest
from app.services.validation_engine import run_validation
from app.validators.base import ValidationContext
from app.validators.steps import (
    VALIDATION_PIPELINE,
    Step1StrutturaIndice,
    Step2UnicitaDocumenti,
    Step3DocumentoPrincipale,
    Step4FormatoMIME,
    Step5IntegritaHash,
    Step6MetadatiAllegato5,
    Step7ClasseDocumentaleOPA,
    Step8DimensioneFile,
    Step9CoerenzaChiave,
)

from tests.lettore_memoria import lettore_per_nomi


def make_ctx(indice: dict, file_map: dict | None = None,
             pdv_id="test-pdv", tenant_id="test-tenant", regole=None) -> ValidationContext:
    """Contesto con i file come riferimenti + lettore in streaming (contenuti di prova)."""
    riferimenti, lettore = lettore_per_nomi(file_map or {})
    return ValidationContext(pdv_id=pdv_id, tenant_id=tenant_id, indice=indice,
                             riferimenti=riferimenti, lettore=lettore, regole=regole)


class TestStep1StrutturaIndice:
    @pytest.mark.asyncio
    async def test_indice_valido(self, indice_valido):
        ctx = make_ctx(indice_valido)
        r = await Step1StrutturaIndice().validate(ctx)
        assert r.ok

    @pytest.mark.asyncio
    async def test_mancanza_classe_documentale(self):
        ctx = make_ctx({"documenti": [{"id": "x", "tipo": "PRINCIPALE",
                                        "nome_file": "f.pdf",
                                        "hash": {"impronta": "abc", "algoritmo": "SHA-256"}}]})
        r = await Step1StrutturaIndice().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-101"

    @pytest.mark.asyncio
    async def test_documenti_vuoti(self):
        ctx = make_ctx({"classe_documentale": "fattura", "documenti": []})
        r = await Step1StrutturaIndice().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-102"

    @pytest.mark.asyncio
    async def test_campo_obbligatorio_mancante(self):
        ctx = make_ctx({"classe_documentale": "fattura", "documenti": [
            {"tipo": "PRINCIPALE", "nome_file": "f.pdf",
             "hash": {"impronta": "abc", "algoritmo": "SHA-256"}}
        ]})
        r = await Step1StrutturaIndice().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-103"


class TestStep2UnicitaDocumenti:
    @pytest.mark.asyncio
    async def test_nessun_duplicato(self, indice_valido):
        r = await Step2UnicitaDocumenti().validate(make_ctx(indice_valido))
        assert r.ok

    @pytest.mark.asyncio
    async def test_id_duplicato(self):
        doc = {"id": "dup", "tipo": "PRINCIPALE", "nome_file": "f.pdf",
               "hash": {"impronta": "abc", "algoritmo": "SHA-256"}}
        ctx = make_ctx({"classe_documentale": "fattura",
                        "documenti": [doc, {**doc, "nome_file": "g.pdf"}]})
        r = await Step2UnicitaDocumenti().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-201"


class TestStep3DocumentoPrincipale:
    @pytest.mark.asyncio
    async def test_principale_presente(self, indice_valido):
        r = await Step3DocumentoPrincipale().validate(make_ctx(indice_valido))
        assert r.ok

    @pytest.mark.asyncio
    async def test_nessun_principale(self):
        ctx = make_ctx({"classe_documentale": "fattura", "documenti": [
            {"id": "x", "tipo": "ALLEGATO", "nome_file": "f.pdf",
             "hash": {"impronta": "abc", "algoritmo": "SHA-256"}}
        ]})
        r = await Step3DocumentoPrincipale().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-301"

    @pytest.mark.asyncio
    async def test_due_principali(self):
        doc = {"id": "x", "tipo": "PRINCIPALE", "nome_file": "f.pdf",
               "hash": {"impronta": "abc", "algoritmo": "SHA-256"}}
        ctx = make_ctx({"classe_documentale": "fattura",
                        "documenti": [doc, {**doc, "id": "y", "nome_file": "g.pdf"}]})
        r = await Step3DocumentoPrincipale().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-302"


class TestStep4FormatoMIME:
    @pytest.mark.asyncio
    async def test_formato_valido(self, indice_valido):
        r = await Step4FormatoMIME().validate(make_ctx(indice_valido))
        assert r.ok

    @pytest.mark.asyncio
    async def test_formato_non_ammesso(self):
        ctx = make_ctx({"classe_documentale": "f", "documenti": [
            {"id": "x", "tipo": "PRINCIPALE", "nome_file": "f.exe",
             "formato": "application/x-msdownload",
             "hash": {"impronta": "abc", "algoritmo": "SHA-256"}}
        ]})
        r = await Step4FormatoMIME().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-401"


class TestStep5IntegritaHash:
    @pytest.mark.asyncio
    async def test_hash_corretto(self, indice_valido, file_pdf):
        ctx = make_ctx(indice_valido, {"fattura.pdf": file_pdf})
        r = await Step5IntegritaHash().validate(ctx)
        assert r.ok

    @pytest.mark.asyncio
    async def test_hash_errato(self, indice_valido, file_pdf):
        indice = dict(indice_valido)
        indice["documenti"] = [{**indice["documenti"][0],
                                 "hash": {"impronta": "hash_sbagliato", "algoritmo": "SHA-256"}}]
        ctx = make_ctx(indice, {"fattura.pdf": file_pdf})
        r = await Step5IntegritaHash().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-501"

    @pytest.mark.asyncio
    async def test_file_non_trovato(self, indice_valido):
        """VD-C-008: file non letto dallo storage → il passo FALLISCE (fail-closed)."""
        ctx = make_ctx(indice_valido, {})  # file_map vuota
        r = await Step5IntegritaHash().validate(ctx)
        assert not r.ok
        assert r.dettagli["errori"][0]["errore"] == "file_non_disponibile"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("algoritmo,funzione", [
        ("SHA-512", hashlib.sha512), ("SHA3-256", hashlib.sha3_256),
    ])
    async def test_hash_altri_algoritmi_verificati(
        self, indice_valido, file_pdf, algoritmo, funzione):
        """VD-C-017: ogni algoritmo ammesso si verifica davvero (prima solo SHA-256)."""
        doc = indice_valido["documenti"][0]
        giusto = {**indice_valido, "documenti": [{**doc, "hash": {
            "impronta": funzione(file_pdf).hexdigest(), "algoritmo": algoritmo}}]}
        falso = {**indice_valido, "documenti": [{**doc, "hash": {
            "impronta": "ab" * (len(funzione(b"").hexdigest()) // 2), "algoritmo": algoritmo}}]}
        assert (await Step5IntegritaHash().validate(make_ctx(giusto, {"fattura.pdf": file_pdf}))).ok
        r = await Step5IntegritaHash().validate(make_ctx(falso, {"fattura.pdf": file_pdf}))
        assert not r.ok and r.codice_errore == "VLD-501"

    @pytest.mark.asyncio
    async def test_hash_algoritmo_non_ammesso_rifiutato(self, indice_valido, file_pdf):
        doc = indice_valido["documenti"][0]
        indice = {**indice_valido, "documenti": [{**doc, "hash": {
            "impronta": hashlib.md5(file_pdf, usedforsecurity=False).hexdigest(),
            "algoritmo": "MD5"}}]}
        r = await Step5IntegritaHash().validate(make_ctx(indice, {"fattura.pdf": file_pdf}))
        assert not r.ok
        assert "algoritmo_non_ammesso" in r.dettagli["errori"][0]["errore"]


class TestStep8DimensioneFile:
    @pytest.mark.asyncio
    async def test_file_nel_limite(self, file_pdf):
        ctx = make_ctx({"classe_documentale": "f", "documenti": [{"nome_file": "fattura.pdf"}]},
                       {"fattura.pdf": file_pdf})
        r = await Step8DimensioneFile().validate(ctx)
        assert r.ok

    @pytest.mark.asyncio
    async def test_file_troppo_grande_limite_della_classe(self):
        """R03: il limite viene dalla fotografia (qui 1 MB), non da una costante."""
        from app.services.regole_pdv import regole_del_pdv
        regole = regole_del_pdv(
            {"impostazioni": {"CLASSE_DIMENSIONE_MAX_MB": {"valore": 1, "origine": "CLASSE"}}},
            limite_mb_predefinito=5120, formati_predefiniti=[])
        ctx = make_ctx({"classe_documentale": "f", "documenti": [{"nome_file": "big.pdf"}]},
                       {"big.pdf": b"x" * (1024 * 1024 + 1)}, regole=regole)
        r = await Step8DimensioneFile().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-801"
        assert r.dettagli["origine_limite"] == "CLASSE"
        assert ctx.lettore.letture == []  # dimensione dai metadati: file non scaricato


class TestStep9CoerenzaChiave:
    @pytest.mark.asyncio
    async def test_chiave_valida(self, indice_valido):
        r = await Step9CoerenzaChiave().validate(make_ctx(indice_valido))
        assert r.ok

    @pytest.mark.asyncio
    async def test_anno_non_valido(self):
        ctx = make_ctx({"classe_documentale": "f", "documenti": [],
                        "chiave": {"anno": "1800"}})
        r = await Step9CoerenzaChiave().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-901"

    @pytest.mark.asyncio
    async def test_senza_chiave_ok(self):
        ctx = make_ctx({"classe_documentale": "f", "documenti": []})
        r = await Step9CoerenzaChiave().validate(ctx)
        assert r.ok


class TestValidationEngine:
    @pytest.mark.asyncio
    async def test_pipeline_completa_ok(self, indice_valido, file_pdf, pdv_id, tenant_id):
        """Pipeline completa con classe attiva (lettura DB simulata) e metadati Allegato 5."""
        from unittest.mock import AsyncMock, MagicMock, patch
        indice = {**indice_valido, "documenti": [{**indice_valido["documenti"][0], "parametri": {
            "identificativo_documento": "FT-2026-001", "modalita_formazione": "a",
            "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
            "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme S.r.l.",
            "oggetto": "Fattura servizi IT marzo 2026"}}]}
        sessione = AsyncMock()
        risultato = MagicMock()
        risultato.fetchone.return_value = ("aaaaaaaa-0000-0000-0000-000000000001", True)
        sessione.execute.return_value = risultato
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=sessione)
        cm.__aexit__ = AsyncMock(return_value=False)
        riferimenti, lettore = lettore_per_nomi({"fattura.pdf": file_pdf})
        with patch("app.validators.steps.get_async_session", return_value=cm):
            valido, risultati = await run_validation(
                pdv_id=pdv_id,
                tenant_id=tenant_id,
                indice=indice,
                riferimenti=riferimenti, lettore=lettore,
            )
        assert valido is True, [r.to_dict() for r in risultati if not r.ok]
        assert all(r.ok for r in risultati)

    @pytest.mark.asyncio
    async def test_pipeline_fallisce_al_primo_errore(self, pdv_id, tenant_id):
        indice_rotto = {"documenti": []}  # manca classe_documentale → step 1 fallisce
        valido, risultati = await run_validation(
            pdv_id=pdv_id,
            tenant_id=tenant_id,
            indice=indice_rotto,
        )
        assert valido is False
        assert len(risultati) == 1  # si ferma al primo fallimento
        assert risultati[0].step == 1

    @pytest.mark.asyncio
    async def test_pipeline_ha_11_validator(self):
        # R08: formato riconosciuto (10) e firme (11) dopo i 9 passi AgID
        assert len(VALIDATION_PIPELINE) == 11
        steps = [v.step for v in VALIDATION_PIPELINE]
        assert steps == list(range(1, 12))


# ─────────────────────────────────────────────────────────────────────
# B-R02 FIX: TestStep6 e TestStep7 aggiunti alla suite esistente
# per completare la copertura. Test compatti — le suite dedicate
# (test_step6_allegato5.py e test_step7_classe_db.py) coprono i
# casi edge in dettaglio.
# ─────────────────────────────────────────────────────────────────────

class TestStep6MetadatiAllegato5:
    """
    Test integrazione Step6 nella suite principale.
    Verifica il comportamento base con parametri presenti/assenti.
    Test dettagliati → services/validation/tests/test_step6_allegato5.py
    """

    def _doc(self, con_parametri: bool = True, tipo: str = "PRINCIPALE") -> dict:
        doc = {
            "id": "doc-001", "tipo": tipo,
            "nome_file": "test.pdf", "formato": "application/pdf",
            "hash": {"impronta": "a" * 64, "algoritmo": "SHA-256"},
        }
        if con_parametri:
            doc["parametri"] = {
                "identificativo_documento": "TEST-001",
                "modalita_formazione": "a",
                "tipologia_documentale": "fattura",
                "data_documento": "2026-03-19",
                "soggetto_produttore_id": "12345678901",
                "soggetto_produttore_nome": "Test S.r.l.",
                "oggetto": "Documento di test Step6",
            }
        return doc

    def _ctx(self, documenti: list) -> ValidationContext:
        return make_ctx({"classe_documentale": "fattura", "documenti": documenti})

    @pytest.mark.asyncio
    async def test_principale_con_allegato5_completo_ok(self):
        """PRINCIPALE con tutti i 7 campi → step OK."""
        r = await Step6MetadatiAllegato5().validate(self._ctx([self._doc()]))
        assert r.ok, f"Step6 fallito inatteso: {r.messaggio}"

    @pytest.mark.asyncio
    async def test_principale_senza_parametri_fallisce(self):
        """PRINCIPALE senza parametri → VLD-601 (B-04 regression)."""
        r = await Step6MetadatiAllegato5().validate(self._ctx([self._doc(con_parametri=False)]))
        assert not r.ok
        assert r.codice_errore == "VLD-601"

    @pytest.mark.asyncio
    async def test_allegato_senza_parametri_ok(self):
        """ALLEGATO senza parametri → step OK (non obbligatorio)."""
        doc_allegato = self._doc(con_parametri=False, tipo="ALLEGATO")
        r = await Step6MetadatiAllegato5().validate(
            self._ctx([self._doc(), doc_allegato])
        )
        assert r.ok

    @pytest.mark.asyncio
    async def test_campo_mancante_riportato_nell_errore(self):
        """Campo obbligatorio mancante → nome campo nell'errore."""
        doc = self._doc()
        del doc["parametri"]["oggetto"]
        r = await Step6MetadatiAllegato5().validate(self._ctx([doc]))
        assert not r.ok
        errori_str = str(r.dettagli.get("errori", []))
        assert "oggetto" in errori_str


class TestStep7ClasseDocumentale:
    """
    Test integrazione Step7 nella suite principale.
    Test dettagliati → services/validation/tests/test_step7_classe_db.py
    """

    @pytest.mark.asyncio
    async def test_classe_vuota_fallisce(self):
        """Classe documentale stringa vuota → VLD-701."""
        ctx = make_ctx({"classe_documentale": "", "documenti": []})
        r   = await Step7ClasseDocumentaleOPA().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-701"

    @pytest.mark.asyncio
    async def test_classe_assente_fallisce(self):
        """Indice senza campo classe_documentale → VLD-701."""
        ctx = make_ctx({"documenti": []})
        r   = await Step7ClasseDocumentaleOPA().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-701"

    @pytest.mark.asyncio
    async def test_errore_db_errore_transitorio_non_esito(self):
        """
        VD-C-017/VD-C-009: un errore del DB in Step7 non è un esito (né OK né
        rifiuto): solleva ErroreVerificaTransitorio e il consumer ritenta.
        """
        from unittest.mock import AsyncMock, patch

        from app.validators.base import ErroreVerificaTransitorio
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(side_effect=Exception("DB connection refused"))
        cm.__aexit__  = AsyncMock(return_value=False)
        with patch("app.validators.steps.get_async_session", return_value=cm):
            ctx = make_ctx({"classe_documentale": "fattura", "documenti": []},
                           tenant_id="00000000-0000-0000-0000-000000000001")
            with pytest.raises(ErroreVerificaTransitorio):
                await Step7ClasseDocumentaleOPA().validate(ctx)
