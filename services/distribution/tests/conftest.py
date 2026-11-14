# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fixtures distribution tests."""
import os

# Prima di importare l'app: ambiente di test. Senza JWT_PUBLIC_KEY in ambiente
# il servizio usa una coppia RS256 effimera in memoria (solo con ENV=test, VD-C-004).
os.environ["ENV"] = "test"

import pytest


@pytest.fixture
def aip_id(): return "00000000-0000-0000-0000-000000000011"
@pytest.fixture
def tenant_id(): return "00000000-0000-0000-0000-000000000001"
@pytest.fixture
def richiedente_id(): return "00000000-0000-0000-0000-000000000099"
