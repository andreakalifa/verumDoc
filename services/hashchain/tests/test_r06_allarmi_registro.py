# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R06 — VD-C-129: ``GET /api/v1/hashchain/registro/allarmi``.

Prima la console mostrava solo l'età dell'attesa della controfirma: nessuna API
degli allarmi del registro e del testimone, ``CATENA_CONTROFIRMA_MAX_SECONDI``
non esposta. Ora l'elenco a cursore (solo conservatore) riunisce gli allarmi
del testimone (rifiuti per causa) e i blocchi del registro, aperti o anche
gestiti, e dichiara le condizioni del momento con le soglie.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.token_di_prova import intestazioni

pytestmark = [pytest.mark.integration, richiede_pg]
ORA = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


async def _get(percorso: str, intest: dict):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(percorso, headers=intest)


async def test_allarmi_a_cursore_e_condizioni(db_url):  # noqa: F811
    for i in range(3):
        await sql(db_url, "INSERT INTO public.catena_allarmi_testimone (causa, aperto_il, "
                          "ultimo_il, rifiuti, prima_dimensione, ultima_dimensione, "
                          "ultimo_motivo, gestito_il, gestito_da) "
                          "VALUES ($1, $2, $2, 2, 5, 7, 'firma non valida', "
                          "$3, $4)", f"CAUSA_{i}", ORA - timedelta(hours=i),
                  ORA if i == 2 else None, "operatore" if i == 2 else None)
    await sql(db_url, "INSERT INTO public.catena_blocchi_registro (causa, aperto_il, ultimo_il, "
                      "occorrenze, dettagli) VALUES ('REGISTRO_DIVERGENTE', $1, $1, 1, "
                      "'{\"indice\": 4}'::jsonb)", ORA - timedelta(minutes=30))
    await sql(db_url, "INSERT INTO public.catena_checkpoint (dimensione, radice, origine, testo, "
                      "chiave_verificatore, emesso_il, worm_bucket, worm_chiave, stato_sigillo, "
                      "stato_testimone) VALUES (9, repeat('a', 64), 'o', 't', 'k', "
                      "now() - interval '2 hours', 'b', 'c', 'FIRMATO', 'IN_ATTESA')")
    conservatore = intestazioni("RSC_CONSERVATORE")
    # solo gli aperti, dal più recente, a cursore (pagine da 2)
    p1 = await _get("/api/v1/hashchain/registro/allarmi?limite=2", conservatore)
    assert p1.status_code == 200, p1.text
    corpo = p1.json()
    assert [a["id"].split("-")[0] for a in corpo["items"]] == ["testimone", "blocco"]
    assert corpo["altri"] is True and corpo["items"][0]["causa"] == "CAUSA_0"
    assert corpo["items"][1]["dettagli"] == {"indice": 4}
    p2 = (await _get(f"/api/v1/hashchain/registro/allarmi?limite=2&cursore={corpo['cursore']}",
                     conservatore)).json()
    assert [a["causa"] for a in p2["items"]] == ["CAUSA_1"] and p2["altri"] is False
    # anche i gestiti
    tutti = (await _get("/api/v1/hashchain/registro/allarmi?aperti=false",
                        conservatore)).json()["items"]
    gestito = [a for a in tutti if a["causa"] == "CAUSA_2"]
    assert gestito and gestito[0]["aperto"] is False and gestito[0]["gestito_da"] == "operatore"
    # condizioni del momento con la soglia esposta
    condizioni = {c["codice"]: c for c in corpo["condizioni"]}
    attesa = condizioni["CHECKPOINT_SENZA_CONTROFIRMA"]
    assert attesa["attiva"] is True and attesa["dettagli"]["dimensione"] == 9
    assert attesa["dettagli"]["soglia_secondi"] == corpo["soglie"]["controfirma_max_secondi"]
    # cursore non valido, ruoli non del conservatore
    r = await _get("/api/v1/hashchain/registro/allarmi?cursore=zzz", conservatore)
    assert r.status_code == 400 and r.json()["codice"] == "CAT-400-CURSORE"
    for ruolo in ("OPERATORE", "ADMIN_TENANT"):
        r = await _get("/api/v1/hashchain/registro/allarmi", intestazioni(ruolo))
        assert r.status_code == 403 and r.json()["codice"] == "CAT-403"
