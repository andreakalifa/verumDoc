# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test F3.4 Migration API."""
import pytest
from pydantic import ValidationError


class TestMigrationModels:
    def test_batch_request_max_100(self):
        from services.distribution.app.routers.migration import BatchMigrationRequest
        req = BatchMigrationRequest(
            aip_ids=["id-1", "id-2"],
            conservatore_destinatario="Nuovo Conservatore S.r.l.",
            motivo="Cambio conservatore per fine contratto",
            richiedente_id="rsc-001",
        )
        assert len(req.aip_ids) == 2

    def test_batch_request_vuota_invalida(self):
        from services.distribution.app.routers.migration import BatchMigrationRequest
        with pytest.raises(ValidationError):
            BatchMigrationRequest(
                aip_ids=[],
                conservatore_destinatario="Test",
                motivo="x" * 10,
                richiedente_id="u",
            )

    def test_migration_request_motivo_corto_invalido(self):
        from services.distribution.app.routers.migration import MigrationRequest
        with pytest.raises(ValidationError):
            MigrationRequest(
                conservatore_destinatario="Test",
                motivo="corto",
                richiedente_id="u",
            )
