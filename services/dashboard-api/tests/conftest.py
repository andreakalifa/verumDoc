# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Fixtures dashboard-api tests."""
import os

# Prima di importare l'app: ambiente di test. Senza JWT_PUBLIC_KEY in ambiente
# il servizio usa una coppia RS256 effimera in memoria (solo con ENV=test, VD-C-004).
os.environ["ENV"] = "test"

import pytest


@pytest.fixture
def tenant_id():
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def ambito_del_conservatore():
    """R05 (VD-C-080): per i test che chiamano DIRETTAMENTE funzioni dei servizi
    usate in esercizio solo da richieste di SUPER_ADMIN/RSC_CONSERVATORE (ambito
    conservatore impostato dal token verificato)."""
    from libs.shared.db.ambito import ambito_conservatore

    with ambito_conservatore("test di una funzione riservata al conservatore"):
        yield
