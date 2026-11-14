# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test state machine tenant.
Riferimento piano: F1.5.2
"""
from __future__ import annotations

import pytest
from app.services.state_machine import get_transizioni_ammesse, validate_transition
from fastapi import HTTPException

from libs.shared.models import TenantStato


class TestValidateTransition:
    def test_trial_to_attivo_ok(self):
        validate_transition(TenantStato.TRIAL, TenantStato.ATTIVO)  # no exception

    def test_trial_to_disattivato_ok(self):
        validate_transition(TenantStato.TRIAL, TenantStato.DISATTIVATO)

    def test_attivo_to_sospeso_ok(self):
        validate_transition(TenantStato.ATTIVO, TenantStato.SOSPESO)

    def test_attivo_to_disattivato_ok(self):
        validate_transition(TenantStato.ATTIVO, TenantStato.DISATTIVATO)

    def test_sospeso_to_attivo_ok(self):
        validate_transition(TenantStato.SOSPESO, TenantStato.ATTIVO)

    def test_sospeso_to_disattivato_ok(self):
        validate_transition(TenantStato.SOSPESO, TenantStato.DISATTIVATO)

    def test_trial_to_sospeso_raises(self):
        with pytest.raises(HTTPException) as exc:
            validate_transition(TenantStato.TRIAL, TenantStato.SOSPESO)
        assert exc.value.status_code == 422
        assert exc.value.detail["type"] == "ONB-201"

    def test_disattivato_to_attivo_raises(self):
        with pytest.raises(HTTPException) as exc:
            validate_transition(TenantStato.DISATTIVATO, TenantStato.ATTIVO)
        assert exc.value.status_code == 422

    def test_disattivato_to_trial_raises(self):
        with pytest.raises(HTTPException):
            validate_transition(TenantStato.DISATTIVATO, TenantStato.TRIAL)

    def test_attivo_to_trial_raises(self):
        with pytest.raises(HTTPException):
            validate_transition(TenantStato.ATTIVO, TenantStato.TRIAL)

    def test_disattivato_ha_zero_transizioni(self):
        t = get_transizioni_ammesse(TenantStato.DISATTIVATO)
        assert t == []

    def test_trial_ha_due_transizioni(self):
        t = get_transizioni_ammesse(TenantStato.TRIAL)
        assert len(t) == 2
        assert "ATTIVO" in t
        assert "DISATTIVATO" in t

    def test_attivo_ha_due_transizioni(self):
        t = get_transizioni_ammesse(TenantStato.ATTIVO)
        assert len(t) == 2
        assert "SOSPESO" in t
        assert "DISATTIVATO" in t

    def test_sospeso_ha_due_transizioni(self):
        t = get_transizioni_ammesse(TenantStato.SOSPESO)
        assert "ATTIVO" in t
        assert "DISATTIVATO" in t
