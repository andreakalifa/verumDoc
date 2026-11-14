# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Token firmati con la coppia effimera di ENV=test (stessa verifica dell'esercizio)."""
from __future__ import annotations

import uuid
from typing import Any

from app.auth.jwt_handler import chiavi_jwt
from app.config import settings

from libs.shared.security.jwt_rs256 import firma_token

TENANT_CONSERVATORE = "11111111-1111-1111-1111-111111111111"


def token(ruolo: str, *, user_id: str | None = None, durata: int = 600,
          chiave_privata: str | None = None, **altri: Any) -> str:
    return firma_token(
        {"tenant_id": TENANT_CONSERVATORE, "user_id": user_id or str(uuid.uuid4()),
         "ruolo": ruolo, **altri},
        chiave_privata_pem=chiave_privata or chiavi_jwt().privata_pem,
        emittente=settings.jwt_issuer,
        pubblico=settings.jwt_audience,
        durata_secondi=durata,
    )


def intestazioni(ruolo: str, **kw: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(ruolo, **kw)}"}
