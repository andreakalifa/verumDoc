# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (integrazione, richiesta della console) — ``GET /api/v1/configurazione/accordi``:
elenco a cursore degli accordi di versamento (ultima versione di ogni codice)
con filtri ``ente`` (solo conservatore), ``classe`` e ``stato``.

Permessi come le bozze: conservatore (tutti gli enti) e ADMIN_TENANT (il suo
ente); gli altri ruoli 403 CFG-403. Con la RLS (migrazione 20261023_040) un ente
non vede gli accordi degli altri anche se il filtro applicativo mancasse.
"""
from __future__ import annotations

import uuid
from datetime import date

from tests.infra_reale import richiede_pg, sql
from tests.test_r05_configurazione import (  # noqa: F401 — fixture e supporto
    _cache_e_pepper,
    _chiama,
    _classe,
    _ente,
    _token,
    _utente,
    db_url,
)

pytestmark = richiede_pg


async def _accordo(url: str, tid: str, codice: str, versione: int, *, classe: str | None = None,
                   cessato: bool = False, descrizione: str | None = None) -> None:
    await sql(url, "INSERT INTO public.accordi_versamento (tenant_id, codice, versione, "
                   "classe_id, descrizione, valido_dal, cessato, motivo) VALUES ($1, $2, $3, $4, "
                   "$5, $6, $7, 'motivo di prova')",
              uuid.UUID(tid), codice, versione, uuid.UUID(classe) if classe else None,
              descrizione, date(2026, 1, versione), cessato)


async def test_elenco_accordi_ultima_versione_filtri_e_cursore(db_url) -> None:  # noqa: F811
    a, b = await _ente(db_url), await _ente(db_url)
    classe = await _classe(db_url, a, "contratti")
    await _accordo(db_url, a, "AV-1", 1, classe=classe)
    await _accordo(db_url, a, "AV-1", 2, classe=classe, descrizione="seconda versione")
    await _accordo(db_url, a, "AV-2", 1)
    await _accordo(db_url, a, "AV-3", 1)
    await _accordo(db_url, a, "AV-3", 2, cessato=True)
    await _accordo(db_url, b, "AV-B", 1)
    admin_a = _token(a, await _utente(db_url, a, "ADMIN_TENANT"), "ADMIN_TENANT")

    stato, elenco = await _chiama("GET", "/accordi", token=admin_a)
    assert stato == 200, elenco
    per_codice = {x["codice"]: x for x in elenco["items"]}
    assert set(per_codice) == {"AV-1", "AV-2", "AV-3"}     # mai gli accordi di b
    assert per_codice["AV-1"]["versione"] == 2
    assert per_codice["AV-1"]["descrizione"] == "seconda versione"
    assert per_codice["AV-1"]["classe"] == classe and per_codice["AV-1"]["ente"] == a
    assert per_codice["AV-3"]["cessato"] is True
    assert set(elenco) == {"items", "limit", "cursore", "altri"}

    _, attivi = await _chiama("GET", "/accordi", token=admin_a, params={"stato": "ATTIVO"})
    assert {x["codice"] for x in attivi["items"]} == {"AV-1", "AV-2"}
    _, cessati = await _chiama("GET", "/accordi", token=admin_a, params={"stato": "CESSATO"})
    assert [x["codice"] for x in cessati["items"]] == ["AV-3"]
    _, della_classe = await _chiama("GET", "/accordi", token=admin_a, params={"classe": classe})
    assert [x["codice"] for x in della_classe["items"]] == ["AV-1"]

    # cursore: due pagine da 2, nessun accordo ripetuto o perso
    _, p1 = await _chiama("GET", "/accordi", token=admin_a, params={"limit": 2})
    assert p1["altri"] is True and p1["cursore"]
    _, p2 = await _chiama("GET", "/accordi", token=admin_a,
                          params={"limit": 2, "cursore": p1["cursore"]})
    assert p2["altri"] is False
    assert sorted(x["codice"] for x in p1["items"] + p2["items"]) == ["AV-1", "AV-2", "AV-3"]
    stato, err = await _chiama("GET", "/accordi", token=admin_a, params={"cursore": "rotto"})
    assert stato == 400 and err["codice"] == "CFG-400"

    # il conservatore vede tutti gli enti, o uno solo col filtro
    rsc = _token(str(uuid.uuid4()), str(uuid.uuid4()), "RSC_CONSERVATORE")
    _, tutti = await _chiama("GET", "/accordi", token=rsc)
    assert {"AV-1", "AV-B"} <= {x["codice"] for x in tutti["items"]}
    _, solo_b = await _chiama("GET", "/accordi", token=rsc, params={"ente": b})
    assert [x["codice"] for x in solo_b["items"]] == ["AV-B"]
    # un ente che chiede un altro ente non lo vede
    _, altro = await _chiama("GET", "/accordi", token=admin_a, params={"ente": b})
    assert {x["codice"] for x in altro["items"]} == {"AV-1", "AV-2", "AV-3"}


async def test_elenco_accordi_permessi(db_url) -> None:  # noqa: F811
    a = await _ente(db_url)
    for ruolo in ("OPERATORE", "VIEWER"):
        stato, err = await _chiama("GET", "/accordi",
                                   token=_token(a, await _utente(db_url, a, ruolo), ruolo))
        assert stato == 403 and err["codice"] == "CFG-403", ruolo
    stato, _ = await _chiama("GET", "/accordi")
    assert stato == 401
