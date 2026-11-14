# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test URN AGID — F2-03 + B-13.
Verifica il generatore URN conforme CNIPA deliberazione 11/2004:
  urn:IT:{CF_CONSERVATORE}:{TipologiaOggetto}:{Anno}:{Progressivo:010d}
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.services.aip_engine import _generate_urn_agid

# Pattern URN AGID completo
URN_PATTERN = re.compile(
    r"^urn:IT:[0-9A-Za-z]+:[A-Z0-9_]+:\d{4}:\d{10}$"
)

NOW = datetime(2026, 3, 19, 10, 0, 0, tzinfo=UTC)
TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _mock_session(progressivo: int = 1):
    """Session che simula nextval della sequence PostgreSQL."""
    session = AsyncMock()
    # Il Result di SQLAlchemy è sincrono: scalar_one() non è una coroutine
    result  = MagicMock()
    result.scalar_one.return_value = progressivo
    session.execute.return_value   = result
    return session


class TestFormatoURN:
    """Verifica che l'URN rispetti il formato AGID."""

    @pytest.mark.asyncio
    async def test_formato_urn_valido(self):
        """URN generato deve rispettare il pattern urn:IT:CF:TIPO:ANNO:PROG."""
        session = _mock_session(1)
        urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        assert URN_PATTERN.match(urn), (
            f"URN non conforme al pattern AGID: {urn!r}"
        )

    @pytest.mark.asyncio
    async def test_prefisso_urn_it(self):
        """L'URN deve iniziare con 'urn:IT:'."""
        session = _mock_session(1)
        urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        assert urn.startswith("urn:IT:"), f"URN deve iniziare con urn:IT: — trovato: {urn!r}"

    @pytest.mark.asyncio
    async def test_anno_corrente_nell_urn(self):
        """L'URN deve contenere l'anno corrente."""
        session = _mock_session(1)
        urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        assert "2026" in urn, f"Anno 2026 non trovato nell'URN: {urn!r}"

    @pytest.mark.asyncio
    async def test_progressivo_10_cifre(self):
        """Il progressivo deve essere zero-padded a 10 cifre."""
        session = _mock_session(42)
        urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        parts   = urn.split(":")
        assert len(parts) == 6
        progressivo_str = parts[5]
        assert len(progressivo_str) == 10, (
            f"Progressivo deve avere 10 cifre, trovato {len(progressivo_str)}: {progressivo_str!r}"
        )
        assert progressivo_str == "0000000042"

    @pytest.mark.asyncio
    async def test_tipologia_uppercase(self):
        """La tipologia documentale deve essere UPPERCASE nell'URN."""
        session = _mock_session(1)
        urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        parts   = urn.split(":")
        tipologia = parts[3]
        assert tipologia == tipologia.upper(), (
            f"Tipologia deve essere UPPERCASE: {tipologia!r}"
        )
        assert tipologia == "FATTURA"

    @pytest.mark.asyncio
    async def test_tipologia_contratto(self):
        """Classe 'contratto' → CONTRATTO nell'URN."""
        session = _mock_session(5)
        urn     = await _generate_urn_agid(session, TENANT_ID, "contratto", NOW)
        assert ":CONTRATTO:" in urn

    @pytest.mark.asyncio
    async def test_tipologia_delibera(self):
        """Classe 'delibera' → DELIBERA nell'URN."""
        session = _mock_session(3)
        urn     = await _generate_urn_agid(session, TENANT_ID, "delibera", NOW)
        assert ":DELIBERA:" in urn

    @pytest.mark.asyncio
    async def test_tipologia_spazi_diventano_underscore(self):
        """Spazi nella classe → underscore nell'URN."""
        session = _mock_session(1)
        urn     = await _generate_urn_agid(session, TENANT_ID, "atto notarile", NOW)
        parts   = urn.split(":")
        assert " " not in parts[3], "Spazi non ammessi nella tipologia URN"
        assert "_" in parts[3] or parts[3] == "ATTO_NOTARILE"

    @pytest.mark.asyncio
    async def test_tipologia_max_20_caratteri(self):
        """La tipologia viene troncata a 20 caratteri."""
        session  = _mock_session(1)
        classe_lunga = "a" * 30  # > 20 caratteri
        urn      = await _generate_urn_agid(session, TENANT_ID, classe_lunga, NOW)
        parts    = urn.split(":")
        tipologia = parts[3]
        assert len(tipologia) <= 20, (
            f"Tipologia deve essere ≤20 caratteri, trovato {len(tipologia)}: {tipologia!r}"
        )


class TestURNUnicita:
    """Verifica che progressivi diversi producano URN diversi."""

    @pytest.mark.asyncio
    async def test_progressivi_diversi_producono_urn_diversi(self):
        """Due progressivi diversi → due URN diversi."""
        urn1 = await _generate_urn_agid(_mock_session(1),   TENANT_ID, "fattura", NOW)
        urn2 = await _generate_urn_agid(_mock_session(2),   TENANT_ID, "fattura", NOW)
        urn3 = await _generate_urn_agid(_mock_session(100), TENANT_ID, "fattura", NOW)
        assert urn1 != urn2 != urn3

    @pytest.mark.asyncio
    async def test_classi_diverse_stessa_sequence_urn_diversi(self):
        """Stessa sequence ma classe diversa → URN diversi."""
        urn_fattura  = await _generate_urn_agid(_mock_session(1), TENANT_ID, "fattura", NOW)
        urn_contratto = await _generate_urn_agid(_mock_session(1), TENANT_ID, "contratto", NOW)
        assert urn_fattura != urn_contratto

    @pytest.mark.asyncio
    async def test_anni_diversi_stessa_sequence_urn_diversi(self):
        """Stesso progressivo ma anno diverso → URN diversi."""
        now_2025 = datetime(2025, 3, 19, tzinfo=UTC)
        now_2026 = datetime(2026, 3, 19, tzinfo=UTC)
        urn_2025 = await _generate_urn_agid(_mock_session(1), TENANT_ID, "fattura", now_2025)
        urn_2026 = await _generate_urn_agid(_mock_session(1), TENANT_ID, "fattura", now_2026)
        assert "2025" in urn_2025
        assert "2026" in urn_2026
        assert urn_2025 != urn_2026


class TestURNConservatoreCF:
    """Verifica il codice fiscale del conservatore nell'URN."""

    @pytest.mark.asyncio
    async def test_cf_conservatore_presente(self):
        """Il CF conservatore deve essere nella posizione corretta (indice 2)."""
        session = _mock_session(1)
        urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        parts   = urn.split(":")
        # urn:IT:{CF}:{TIPO}:{ANNO}:{PROG}
        assert len(parts) == 6
        cf = parts[2]
        assert len(cf) > 0, "CF conservatore non può essere vuoto"
        # In dev usa placeholder 00000000000
        assert cf.isalnum(), f"CF deve essere alfanumerico: {cf!r}"

    @pytest.mark.asyncio
    async def test_cf_da_settings(self):
        """Il CF deve venire da settings.conservatore_cf."""
        with patch("app.services.aip_engine.settings") as mock_settings:
            mock_settings.conservatore_cf = "TESTCF12345"
            session = _mock_session(1)
            urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        parts = urn.split(":")
        assert parts[2] == "TESTCF12345", (
            f"CF conservatore deve venire da settings: trovato {parts[2]!r}"
        )


class TestURNSequence:
    """Verifica l'uso della sequence PostgreSQL."""

    @pytest.mark.asyncio
    async def test_usa_nextval_sequence(self):
        """_generate_urn_agid deve chiamare nextval sulla sequence."""
        session = _mock_session(999)
        _ = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        # Verifica che execute sia stato chiamato con nextval
        call_args = session.execute.call_args
        sql_str   = str(call_args[0][0])
        assert "nextval" in sql_str, (
            "La sequence PostgreSQL deve essere usata per garantire l'unicità del progressivo"
        )
        assert "urn_progressivo" in sql_str

    @pytest.mark.asyncio
    async def test_progressivo_1000000_zero_padded(self):
        """Progressivo grande deve essere still zero-padded a 10 cifre."""
        session = _mock_session(1_000_000)
        urn     = await _generate_urn_agid(session, TENANT_ID, "fattura", NOW)
        parts   = urn.split(":")
        assert parts[5] == "0001000000"
