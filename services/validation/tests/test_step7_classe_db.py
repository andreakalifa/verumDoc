# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test Step7ClasseDocumentaleOPA — B-04 (Step7 usa query DB invece di OPA).
VD-C-017: fail-closed in ogni ambiente (classe sconosciuta = rifiuto), ricerca
per cliente; un errore del DB è un errore transitorio, non un esito.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.validators.base import ErroreVerificaTransitorio, ValidationContext
from app.validators.steps import Step7ClasseDocumentaleOPA


def make_ctx(classe: str = "fattura",
             pdv_id: str = "pdv-test",
             tenant_id: str = "00000000-0000-0000-0000-000000000001") -> ValidationContext:
    return ValidationContext(
        pdv_id=pdv_id, tenant_id=tenant_id,
        indice={
            "classe_documentale": classe,
            "documenti": [{"id": "d1", "tipo": "PRINCIPALE",
                           "nome_file": "f.pdf",
                           "hash": {"impronta": "a"*64, "algoritmo": "SHA-256"}}],
        },
    )


def _mock_session_ctx(fetchone_return):
    """Crea un context manager asincrono che ritorna il valore dato su fetchone."""
    session = AsyncMock()
    risultato = MagicMock()  # il Result di SQLAlchemy è sincrono
    risultato.fetchone.return_value = fetchone_return
    session.execute.return_value = risultato
    session.commit = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__  = AsyncMock(return_value=False)
    return cm


class TestStep7ClasseEsistente:
    """Step7 con classe esistente e attiva nel DB."""

    @pytest.mark.asyncio
    async def test_classe_attiva_step_ok(self):
        """Classe esistente e attiva → step OK."""
        classe_id = "aaaaaaaa-0000-0000-0000-000000000001"
        with patch(
            "app.validators.steps.get_async_session",
            return_value=_mock_session_ctx((classe_id, True)),
        ):
            r = await Step7ClasseDocumentaleOPA().validate(make_ctx("fattura"))
        assert r.ok
        assert r.dettagli is not None
        assert "classe_id" in r.dettagli

    @pytest.mark.asyncio
    async def test_classe_inattiva_step_fallisce(self):
        """Classe esistente ma inattiva → VLD-702."""
        classe_id = "aaaaaaaa-0000-0000-0000-000000000001"
        with patch(
            "app.validators.steps.get_async_session",
            return_value=_mock_session_ctx((classe_id, False)),
        ):
            r = await Step7ClasseDocumentaleOPA().validate(make_ctx("fattura"))
        assert not r.ok
        assert r.codice_errore == "VLD-702"
        messaggio = r.messaggio.lower()
        assert "attiva" in messaggio or "inattiva" in messaggio or "non attiva" in messaggio

    @pytest.mark.asyncio
    async def test_classe_inesistente_rifiutata(self):
        """VD-C-017: classe non censita per il cliente → VLD-703 in ogni ambiente."""
        with patch(
            "app.validators.steps.get_async_session",
            return_value=_mock_session_ctx(None),
        ):
            r = await Step7ClasseDocumentaleOPA().validate(make_ctx("classe_nuova"))
        assert not r.ok
        assert r.codice_errore == "VLD-703"

    @pytest.mark.asyncio
    async def test_query_filtra_per_cliente(self):
        """La lettura passa il cliente: classi di altri clienti non sono visibili."""
        cm = _mock_session_ctx(("aaaaaaaa-0000-0000-0000-000000000001", True))
        with patch("app.validators.steps.get_async_session", return_value=cm):
            await Step7ClasseDocumentaleOPA().validate(make_ctx("fattura"))
        sessione = await cm.__aenter__()
        parametri = sessione.execute.call_args.args[1]
        assert str(parametri["t"]) == "00000000-0000-0000-0000-000000000001"

    @pytest.mark.asyncio
    async def test_cliente_non_valido_rifiutato(self):
        r = await Step7ClasseDocumentaleOPA().validate(make_ctx("fattura", tenant_id="non-uuid"))
        assert not r.ok
        assert r.codice_errore == "VLD-705"


class TestStep7ClasseMancante:
    """Step7 senza campo classe_documentale."""

    @pytest.mark.asyncio
    async def test_classe_documentale_vuota_fallisce(self):
        """Classe documentale stringa vuota → VLD-701."""
        ctx = ValidationContext(
            pdv_id="pdv", tenant_id="t",
            indice={"classe_documentale": "", "documenti": []},
        )
        r = await Step7ClasseDocumentaleOPA().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-701"

    @pytest.mark.asyncio
    async def test_classe_documentale_assente_fallisce(self):
        """Indice senza campo classe_documentale → VLD-701."""
        ctx = ValidationContext(
            pdv_id="pdv", tenant_id="t",
            indice={"documenti": []},
        )
        r = await Step7ClasseDocumentaleOPA().validate(ctx)
        assert not r.ok
        assert r.codice_errore == "VLD-701"


class TestStep7ErroreDB:
    """Un errore del DB non è un esito: errore transitorio, il consumer ritenta."""

    @pytest.mark.asyncio
    async def test_errore_db_errore_transitorio(self):
        """Errore DB in Step7 → ErroreVerificaTransitorio (né OK né rifiuto)."""
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        cm.__aexit__  = AsyncMock(return_value=False)

        with patch("app.validators.steps.get_async_session", return_value=cm):
            with pytest.raises(ErroreVerificaTransitorio):
                await Step7ClasseDocumentaleOPA().validate(make_ctx("fattura"))


class TestStep7NomeStep:
    """Verifica metadati dello step."""

    def test_step_numero_7(self):
        assert Step7ClasseDocumentaleOPA.step == 7

    def test_nome_classe_documentale(self):
        assert Step7ClasseDocumentaleOPA.nome == "classe_documentale"

    def test_istanziabile(self):
        s = Step7ClasseDocumentaleOPA()
        assert s is not None
