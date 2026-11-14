# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07 (richiesta del committente) — «Rielabora» dei versamenti rifiutati eseguita
direttamente dall'ente: ADMIN_TENANT e OPERATORE del proprio ente (filtro
sull'ente e Row Level Security), solo i messaggi della verifica e della
costruzione del PdA; il conservatore la mantiene. PostgreSQL reale migrato.
"""
from __future__ import annotations

import json
import uuid

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401
from tests.test_r04_scarti import (
    ADESSO,
    Messaggio,
    _client,
    _dlq_pipeline,
    _pdv_in_errore,
    _registra,
    _token,
)

pytestmark = richiede_pg
MOTIVO = "Servizio di nuovo disponibile dopo il guasto"


async def _scartato(url, tenant, classe, offset: int):
    pdv = await _pdv_in_errore(url, tenant, classe, giorni=2)
    await _registra([Messaggio("pdv.received.dlq", 0, offset,
                               _dlq_pipeline("pdv.received", pdv, tenant))])
    return pdv


def _percorso(pdv, azione: str) -> str:
    return f"/api/dashboard/versamenti/{pdv}/rielabora/{azione}"


async def test_l_ente_rielabora_i_propri_versamenti_rifiutati(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _scartato(db_url, tenant, classe, 101)
    altro_pdv = await _scartato(db_url, tenant, classe, 102)
    # un messaggio di notification sullo stesso PdV resta al conservatore
    await _registra([Messaggio("notification.dlq", 1, 103, {
        "original_topic": "pdv.invalid", "partition": 2, "offset": 8,
        "event": {"pdv_id": str(pdv), "tenant_id": str(tenant)}, "error": "x",
        "motivo": "x", "retries": 1, "failed_at": ADESSO.isoformat()})])
    admin = str(uuid.uuid4())
    async with _client() as c:
        r = await c.post(_percorso(pdv, "anteprima"),
                         headers=_token("ADMIN_TENANT", str(tenant), admin))
        assert r.status_code == 200, r.text
        piano = r.json()
        assert [e["forma"] for e in piano["elementi"]] == ["pipeline"]
        assert piano["rielaborabili"] == 1
        r = await c.post(_percorso(pdv, "applica"),
                         headers=_token("ADMIN_TENANT", str(tenant), admin),
                         json={"impronta_piano": piano["impronta_piano"], "motivo": MOTIVO})
        assert r.status_code == 200, r.text
        assert r.json()["rielaborati"] == 1
        # anche l'OPERATORE dell'ente
        op = _token("OPERATORE", str(tenant))
        piano = (await c.post(_percorso(altro_pdv, "anteprima"), headers=op)).json()
        r = await c.post(_percorso(altro_pdv, "applica"), headers=op,
                         json={"impronta_piano": piano["impronta_piano"], "motivo": MOTIVO})
        assert r.status_code == 200, r.text
    stati = await sql(db_url, "SELECT id, stato FROM public.pdv WHERE id = ANY($1)",
                      [pdv, altro_pdv])
    assert {r["stato"] for r in stati} == {"RICEVUTO"}
    [a] = await sql(db_url, "SELECT tenant_id, user_id, dettagli FROM public.audit_global "
                            "WHERE evento = 'scarti.rielaborati' AND user_id = $1",
                    uuid.UUID(admin))
    assert a["tenant_id"] == tenant and json.loads(a["dettagli"])["ruolo"] == "ADMIN_TENANT"
    [n] = await sql(db_url, "SELECT stato FROM public.scarti WHERE coda = 'notification.dlq' "
                            "AND pdv_id = $1", pdv)
    assert n["stato"] == "DA_GESTIRE"


async def test_isolamento_fra_enti_e_ruoli(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    estraneo, _ = await crea_tenant_e_classe(db_url)
    pdv = await _scartato(db_url, tenant, classe, 201)
    async with _client() as c:
        # un altro ente: piano vuoto (non dice se il PdV esiste), applica rifiutata
        h = _token("ADMIN_TENANT", str(estraneo))
        r = await c.post(_percorso(pdv, "anteprima"), headers=h)
        assert r.status_code == 200 and r.json()["elementi"] == []
        assert any("versare di nuovo" in a for a in r.json()["avvisi"])
        r = await c.post(_percorso(pdv, "applica"), headers=h, json={
            "impronta_piano": r.json()["impronta_piano"], "motivo": MOTIVO})
        assert r.status_code == 422 and r.json()["codice"] == "DSH-422-PIANO", r.text
        # VIEWER no; identificativo non valido 422
        r = await c.post(_percorso(pdv, "anteprima"), headers=_token("VIEWER", str(tenant)))
        assert r.status_code == 403 and r.json()["codice"] == "AUTH-010"
        r = await c.post(_percorso("non-uuid", "anteprima"),
                         headers=_token("ADMIN_TENANT", str(tenant)))
        assert r.status_code == 422
        # il conservatore la mantiene, per qualunque ente
        r = await c.post(_percorso(pdv, "anteprima"), headers=_token("RSC_CONSERVATORE"))
        assert r.status_code == 200 and r.json()["rielaborabili"] == 1
    [p] = await sql(db_url, "SELECT stato FROM public.pdv WHERE id = $1", pdv)
    assert p["stato"] == "ERRORE"
