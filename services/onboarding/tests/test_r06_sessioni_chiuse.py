# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R06 — VD-C-127: disattivazione o cambio di ruolo di un utente chiudono SUBITO
le sue sessioni del portale (``sessioni_attive``), in ogni servizio.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_g5_gestione_utenti import _chiama, _ente, _tok, _utente

pytestmark = [pytest.mark.integration, richiede_pg]


async def _sessione(url: str, uid: uuid.UUID) -> None:
    await sql(url, "INSERT INTO public.sessioni_attive (impronta, user_id, ruolo, "
                   "inattivita_secondi, scade_il) VALUES ($1, $2, 'VIEWER', 1800, "
                   "now() + interval '1 hour')", hashlib.sha256(uid.bytes).hexdigest(), uid)


async def _sessioni(url: str, uid: uuid.UUID) -> int:
    return len(await sql(url, "SELECT 1 FROM public.sessioni_attive WHERE user_id = $1", uid))


async def test_disattivazione_e_cambio_di_ruolo_chiudono_le_sessioni(db_url) -> None:  # noqa: F811
    tid = await _ente(db_url)
    adm = await _utente(db_url, tid, "ADMIN_TENANT")
    tok = _tok(tid, adm, "ADMIN_TENANT")
    vw, op = await _utente(db_url, tid, "VIEWER"), await _utente(db_url, tid, "OPERATORE")
    for u in (vw, op):
        await _sessione(db_url, u)
    # modifica del solo nome: la sessione resta
    r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{op}", tok, {"nome": "Nuovo"})
    assert r.status_code == 200, r.text
    assert await _sessioni(db_url, op) == 1
    # cambio di ruolo: chiusa
    r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{op}", tok,
                      {"ruolo": "VIEWER"})
    assert r.status_code == 200, r.text
    assert await _sessioni(db_url, op) == 0
    # disattivazione: chiusa
    r = await _chiama("DELETE", f"/api/v1/onboarding/tenant/{tid}/utenti/{vw}", tok)
    assert r.status_code == 204
    assert await _sessioni(db_url, vw) == 0
