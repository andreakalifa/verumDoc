# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Test modelli Pydantic onboarding."""
import pytest
from app.models.tenant import TenantCreate
from pydantic import ValidationError


class TestTenantCreate:
    def test_payload_valido(self, tenant_payload):
        t = TenantCreate(**tenant_payload)
        assert t.ragione_sociale == "Acme S.r.l."
        assert t.piano == "starter"

    def test_piano_non_valido(self, tenant_payload):
        with pytest.raises(ValidationError):
            TenantCreate(**{**tenant_payload, "piano": "premium"})

    def test_cf_troppo_corto(self, tenant_payload):
        with pytest.raises(ValidationError):
            TenantCreate(**{**tenant_payload, "codice_fiscale": "ABC"})

    def test_ragione_sociale_vuota(self, tenant_payload):
        with pytest.raises(ValidationError):
            TenantCreate(**{**tenant_payload, "ragione_sociale": "X"})

    def test_piano_default_starter(self):
        t = TenantCreate(
            ragione_sociale="Test S.r.l.",
            codice_fiscale="TSTSRL80A01H501Z",  # 16 caratteri (prima 17)
            email_referente="test@test.it",
        )
        assert t.piano == "starter"

    def test_piani_validi(self, tenant_payload):
        for piano in ("starter", "business", "enterprise"):
            t = TenantCreate(**{**tenant_payload, "piano": piano})
            assert t.piano == piano
