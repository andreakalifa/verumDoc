# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07 (D14) — inattività della sessione per ruolo come impostazione della console.

``SESSIONE_INATTIVITA_MINUTI_<RUOLO>``: il conservatore fissa il massimo, l'ente
può solo abbassarlo (``solo_riduzione``). Il limite si fissa all'accesso nella
riga di ``sessioni_attive``; un valore dell'ente più alto del massimo (scritto
fuori dalla console, o rimasto da prima che il conservatore abbassasse il
massimo) non vale: vince il minimo. La console riceve l'anticipo dell'avviso
(2 minuti) per «resta collegato».
"""
from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from libs.shared.parametri import svuota_cache
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_r05_sessione_cookie import _senza_rate_limit  # noqa: F401
from tests.test_r06_sessione_inattivita import ORIGINE, _accedi, _invecchia, _scaduta

pytestmark = richiede_pg


async def _valore(url: str, livello: str, tenant: object, codice: str, valore: int) -> None:
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ($1, $2, $3, CAST($4 AS jsonb), 'prova R07 D14') "
                   "ON CONFLICT ON CONSTRAINT uq_impostazioni_valori_livello DO UPDATE "
                   "SET valore = EXCLUDED.valore", livello, tenant, codice, json.dumps(valore))


async def _limite(url: str) -> list[int]:
    righe = await sql(url, "SELECT inattivita_secondi FROM public.sessioni_attive "
                           "ORDER BY creata_il")
    return [r["inattivita_secondi"] for r in righe]


async def test_massimo_del_conservatore_e_riduzione_dell_ente(  # noqa: F811
        db_url, redis_finto, login_sviluppo, _senza_rate_limit):
    from app.main import app

    codice = "SESSIONE_INATTIVITA_MINUTI_OPERATORE"
    await _valore(db_url, "CONSERVATORE", None, codice, 20)
    svuota_cache()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGINE) as c:
        uid = await _accedi(c, db_url, "OPERATORE")
        assert await _limite(db_url) == [20 * 60]
        r = await c.get("/api/v1/auth/sessione")
        assert r.json()["inattivita_secondi"] == 1200
        assert r.json()["avviso_inattivita_secondi"] == 120
        ente = (await sql(db_url, "SELECT tenant_id FROM public.users WHERE id = $1::uuid",
                          uid))[0]["tenant_id"]

        # l'ente riduce: vale al prossimo accesso
        await _valore(db_url, "ENTE", ente, codice, 10)
        svuota_cache()
        await sql(db_url, "DELETE FROM public.sessioni_attive")
        c.cookies.clear()
        await _accedi_di_nuovo(c, db_url)
        assert await _limite(db_url) == [10 * 60]
        await _invecchia(db_url, 11)
        _scaduta(await c.get("/api/v1/auth/sessione"))

        # un valore dell'ente più alto del massimo non vale: vince il conservatore
        await _valore(db_url, "ENTE", ente, codice, 90)
        svuota_cache()
        await sql(db_url, "DELETE FROM public.sessioni_attive")
        c.cookies.clear()
        await _accedi_di_nuovo(c, db_url)
        assert await _limite(db_url) == [20 * 60]


async def _accedi_di_nuovo(c: AsyncClient, db_url: str) -> None:  # noqa: F811
    """Nuovo accesso SPID simulato dello stesso utente (CF di prova)."""
    from urllib.parse import parse_qs, urlparse

    from tests.test_r05_sessione_cookie import CF

    r = await c.get("/api/v1/auth/spid/login",
                    params={"provider": "spid", "redirect_uri": f"{ORIGINE}/login"})
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await c.get("/api/v1/auth/spid/mock-callback", params={"state": state, "cf": CF})
    codice = parse_qs(urlparse(r.headers["location"]).fragment)["spid_codice"][0]
    assert (await c.post("/api/v1/auth/spid/scambio", json={"codice": codice})).status_code == 200
