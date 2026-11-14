# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fixtures onboarding tests."""
import os

# Prima di importare l'app: ambiente di test. Senza JWT_PUBLIC_KEY in ambiente
# il servizio usa una coppia RS256 effimera in memoria (solo con ENV=test, VD-C-004).
os.environ["ENV"] = "test"
# R12a (VD-C-152): chiave pubblica della posta (coppia generata a runtime)
from tests.posta_prova import POSTA_PUBBLICA  # noqa: E402

os.environ.setdefault("POSTA_CHIAVE_PUBBLICA", POSTA_PUBBLICA)

import pytest  # noqa: E402


@pytest.fixture
def tenant_payload():
    return {
        "ragione_sociale": "Acme S.r.l.",
        "codice_fiscale": "12345678901",  # partita IVA (11 cifre); prima 17 caratteri
        "email_referente": "admin@acme.it",
        "piano": "starter",
    }
