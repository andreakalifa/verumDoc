# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R06 — VD-C-128: ``GET /api/v1/ciclo-vita/pratiche?in_attesa_di=me``.

Prima la pagina «In attesa di te» faceva una richiesta per stato (5 per il
conservatore, 3 per il produttore). Ora un solo elenco a cursore restituisce le
pratiche ferme in uno stato in cui decide il ruolo di chi chiede, nel suo
perimetro.
"""
from __future__ import annotations

import uuid

import pytest

from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, crea_ente, crea_utente, intestazioni

pytestmark = [pytest.mark.integration, richiede_pg]

PRATICHE = [  # (tipo, stato)
    ("SCARTO", "BOZZA"), ("SCARTO", "PROPOSTA"), ("RESTITUZIONE", "PROPOSTA"),
    ("SCARTO", "ESEGUIBILE"), ("SCARTO", "AUTORIZZATA_PRODUTTORE"), ("SCARTO", "NULLA_OSTA"),
    ("RIVERSAMENTO_USCITA", "PROPOSTA"), ("RIVERSAMENTO_INGRESSO", "PROPOSTA"),
    ("SCARTO", "ESEGUITA"), ("RIVERSAMENTO_USCITA", "BOZZA"), ("RESTITUZIONE", "BOZZA"),
]


async def _crea(url: str, ente: uuid.UUID) -> dict[str, tuple[str, str]]:
    ids = {}
    for tipo, stato in PRATICHE:
        pid = uuid.uuid4()
        await sql(url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo) VALUES "
                       "($1, $2, $3, $4, 'pratica di prova del filtro')", pid, ente, tipo, stato)
        ids[str(pid)] = (tipo, stato)
    return ids


async def _tutte(c, intest: dict, limite: int = 3) -> list[dict]:
    voci, cursore = [], None
    while True:
        q = f"/api/v1/ciclo-vita/pratiche?in_attesa_di=me&limite={limite}"
        r = await c.get(q + (f"&cursore={cursore}" if cursore else ""), headers=intest)
        assert r.status_code == 200, r.text
        voci += r.json()["items"]
        cursore = r.json()["cursore"]
        if not r.json()["altri"]:
            return voci


async def test_in_attesa_di_me_per_ruolo_e_perimetro(db_url) -> None:
    a, b = await crea_ente(db_url), await crea_ente(db_url)
    ids_a, ids_b = await _crea(db_url, a), await _crea(db_url, b)
    admin_a = await crea_utente(db_url, a, "ADMIN_TENANT")
    conservatore = await crea_utente(db_url, a, "RSC_CONSERVATORE")
    operatore = await crea_utente(db_url, a, "OPERATORE")
    async with client_app() as c:
        # produttore: proporre e autorizzare scarti e restituzioni, solo il suo ente
        voci = await _tutte(c, intestazioni(a, "ADMIN_TENANT", admin_a))
        assert sorted(ids_a[v["id"]] for v in voci) == sorted(
            [("SCARTO", "BOZZA"), ("SCARTO", "PROPOSTA"), ("RESTITUZIONE", "PROPOSTA")])
        # conservatore: tutti gli enti (filtrabile per ente), a cursore senza salti
        voci = await _tutte(c, intestazioni(a, "RSC_CONSERVATORE", conservatore), limite=2)
        attese = {("SCARTO", "ESEGUIBILE"), ("SCARTO", "AUTORIZZATA_PRODUTTORE"),
                  ("SCARTO", "NULLA_OSTA"), ("RIVERSAMENTO_USCITA", "PROPOSTA"),
                  ("RIVERSAMENTO_INGRESSO", "PROPOSTA")}
        tutte = {**ids_a, **ids_b}
        mie = [tutte[v["id"]] for v in voci if v["id"] in tutte]
        assert len(mie) == 10 and set(mie) == attese and len({v["id"] for v in voci}) == len(voci)
        r = await c.get(f"/api/v1/ciclo-vita/pratiche?in_attesa_di=me&ente={b}&limite=50",
                        headers=intestazioni(a, "RSC_CONSERVATORE", conservatore))
        assert {v["id"] for v in r.json()["items"]} <= set(ids_b) and len(r.json()["items"]) == 5
        # altri ruoli: nessuna pratica li aspetta
        r = await c.get("/api/v1/ciclo-vita/pratiche?in_attesa_di=me",
                        headers=intestazioni(a, "OPERATORE", operatore))
        assert r.status_code == 200 and r.json()["items"] == []
        # valore non ammesso
        r = await c.get("/api/v1/ciclo-vita/pratiche?in_attesa_di=altri",
                        headers=intestazioni(a, "ADMIN_TENANT", admin_a))
        assert r.status_code == 422 and r.json()["codice"] == "VAL-422"
