# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-163) — l'avviso delle modifiche alle proprie credenziali arriva anche nel
canale PERSONALE della campanella (``notifiche_personali``), nella stessa transazione
della modifica, anche per una persona senza indirizzo e-mail; mai negli eventi
dell'ente (``eventi_cliente``, consegnati ai webhook).
"""
from __future__ import annotations

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_sessione_cookie import _senza_rate_limit  # noqa: F401
from tests.test_r10_accesso_locale import (  # noqa: F401
    _attiva_password,
    _client,
    _ente,
    _invito,
    accesso_locale,
)
from tests.test_r12b_credenziali_utente import BASE, NUOVA, PASSWORD, _conferma, _csrf

pytestmark = richiede_pg


async def test_voce_personale_anche_senza_email(  # noqa: F811
        db_url, redis_finto, accesso_locale, _senza_rate_limit):  # noqa: F811
    ente = await _ente(db_url, ["PASSWORD_TOTP"])
    uid, token = await _invito(db_url, ente)
    async with _client() as a:
        segreto, _ = await _attiva_password(a, token)
        await sql(db_url, "UPDATE public.users SET email = '' WHERE id = $1", uid)
        assert (await _conferma(a, db_url, uid, segreto)).status_code == 200
        r = await a.put(f"{BASE}/password", headers=_csrf(a), json={
            "password_attuale": PASSWORD, "password_nuova": NUOVA})
        assert r.status_code == 200, r.text
    voci = await sql(db_url, "SELECT user_id, tenant_id, tipo, titolo, collegamento, letto_il "
                             "FROM public.notifiche_personali")
    assert len(voci) == 1
    v = voci[0]
    assert (v["user_id"], v["tipo"], v["collegamento"], v["letto_il"]) == (
        uid, "credenziali.password_cambiata", "/preferenze/credenziali", None)
    assert "cambiata la password" in v["titolo"] and "amministratore" in v["titolo"]
    # nessun evento dell'ente (quindi nessuna consegna ai webhook)
    assert await sql(db_url, "SELECT 1 FROM public.eventi_cliente WHERE tipo LIKE "
                             "'%credenziali%'") == []
