# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-163) — canale personale della campanella: le voci di
``notifiche_personali`` le vede e le segna come lette SOLO la persona a cui sono
scritte (non un collega dello stesso ente), separate dagli eventi dell'ente; il
database permette di cambiare solo ``letto_il``, una volta.
"""
from __future__ import annotations

import asyncpg
import pytest

from libs.shared.parametri import svuota_cache
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_b3_api import client, crea_ente, h  # noqa: F401

pytestmark = richiede_pg


async def _voce(url: str, ente: dict, ruolo: str, titolo: str) -> int:
    riga = await sql(url, "INSERT INTO public.notifiche_personali (user_id, tenant_id, tipo, "
                          "titolo) VALUES ($1, $2, 'credenziali.password_cambiata', $3) "
                          "RETURNING id", ente[ruolo], ente["tenant"], titolo)
    return int(riga[0]["id"])


async def test_voci_personali_solo_della_persona(client, db_url) -> None:  # noqa: F811
    svuota_cache()
    ente = await crea_ente(db_url)
    mia = await _voce(db_url, ente, "ADMIN_TENANT", "Le tue credenziali: password cambiata")
    sua = await _voce(db_url, ente, "OPERATORE", "Le tue credenziali: passkey revocata")
    corpo = (await client.get("/api/v1/notifiche/campanella", headers=h(ente))).json()
    assert [(v["id"], v["categoria"], v["letto"]) for v in corpo["personali"]] == [
        (mia, "PERSONALI", False)]
    assert corpo["non_letti"] == 1 and corpo["da_fare"] == [] and corpo["avvisi"] == []
    # l'id di un collega non si segna (si aggiornano solo le proprie righe)
    r = await client.post("/api/v1/notifiche/campanella/letti", headers=h(ente),
                          json={"personali": [sua]})
    assert r.status_code == 200 and r.json()["letti"] == 0
    r = await client.post("/api/v1/notifiche/campanella/letti", headers=h(ente),
                          json={"personali": [mia]})
    assert r.json()["letti"] == 1
    corpo = (await client.get("/api/v1/notifiche/campanella", headers=h(ente))).json()
    assert corpo["personali"][0]["letto"] and corpo["non_letti"] == 0
    op = (await client.get("/api/v1/notifiche/campanella", headers=h(ente, "OPERATORE"))).json()
    assert [v["id"] for v in op["personali"]] == [sua]
    assert (await client.post("/api/v1/notifiche/campanella/letti", headers=h(ente, "OPERATORE"),
                              json={"tutti": True})).json()["letti"] == 1
    righe = await sql(db_url, "SELECT id, letto_il FROM public.notifiche_personali ORDER BY id")
    assert all(r["letto_il"] is not None for r in righe)
    # il database: cambia solo letto_il, una volta
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await sql(db_url, "UPDATE public.notifiche_personali SET titolo = 'x' WHERE id = $1", mia)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await sql(db_url, "UPDATE public.notifiche_personali SET letto_il = now() WHERE id = $1",
                  mia)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await sql(db_url, "DELETE FROM public.notifiche_personali")
