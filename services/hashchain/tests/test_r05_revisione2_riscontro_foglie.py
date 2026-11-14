# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 2 di R05 — rilievo S2: l'API interna delle foglie del ciclo di vita.

Prima della correzione ``POST /api/v1/hashchain/interno/foglie`` (raggiungibile
dall'ingress) controllava solo che l'URN del PdA esistesse: con il token di
servizio di ciclo-vita (allora anche nel pod API esposto) si faceva firmare al
registro lo SCARTO di un PdA integro con una pratica inventata («PS-1»), e il
verificatore pubblico dichiarava SCARTATO un documento conservato.

Ora:
  - il percorso è ``/interno/v1/registro/foglie``, fuori da ``/api/``: nessuna
    rotta dell'ingress lo raggiunge; una richiesta con ``X-Forwarded-For``
    (arrivata dall'ALB) riceve 404; il vecchio percorso non esiste più;
  - la foglia si accoda solo se il FATTO risulta dal database (riscontro):
    scarto = pratica SCARTO dello stesso ente, esecuzione avviata, oggetto
    ELIMINATO, PdA in ELIMINAZIONE; annullamento = pratica ANNULLAMENTO ESEGUITA
    del PdV, PdV ANNULLATO; rettifica = pratica RETTIFICA IN_VERSAMENTO del PdV.
    Altrimenti 409 ``CAT-409-RISCONTRO`` e nessuna scrittura.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
import yaml
from app.auth.jwt_handler import chiavi_jwt
from app.config import settings
from app.main import app
from httpx import ASGITransport, AsyncClient

from libs.shared.security.jwt_rs256 import firma_token_servizio
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401
from tests.supporto_registro import crea_pda, fatto_ciclo_vita

pytestmark = [pytest.mark.integration, richiede_pg]

URL = "/interno/v1/registro/foglie"
VERBALE = hashlib.sha256(b"verbale").hexdigest()


def _token() -> dict[str, str]:
    tok = firma_token_servizio("ciclo-vita", chiave_privata_pem=chiavi_jwt().privata_pem,
                               emittente=settings.jwt_issuer, durata_secondi=300,
                               pubblico=settings.catena_pubblico_servizio,
                               scopes=["registro:aggiungi"])
    return {"Authorization": f"Bearer {tok}"}


async def _post(corpo: dict, *, url: str = URL, extra: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(url, json=corpo, headers={**_token(), **(extra or {})})


def _corpo(pda: str, pratica: str, tipo: str = "scarto", **altro) -> dict:
    return {"tipo": tipo, "pda": pda, "riferimento": {"pratica": pratica, **altro},
            "impronta_documento_pratica": VERBALE}


async def _nessuna_foglia(db_url: str) -> bool:  # noqa: F811
    return await sql(db_url, "SELECT 1 FROM public.catena_foglie_interne") == []


def _rifiuto(r) -> None:
    assert r.status_code == 409, r.text
    assert r.json()["codice"] == "CAT-409-RISCONTRO"


async def test_scarto_inventato_di_un_pda_conservato_rifiutato(db_url):  # noqa: F811
    """La prova del revisore rovesciata: PdA CONSERVATO, pratica «PS-1» o un id
    qualunque → 409, nessuna foglia (prima: 202 e scarto firmato)."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    _rifiuto(await _post(_corpo(pda.urn, "PS-1")))
    _rifiuto(await _post(_corpo(pda.urn, str(uuid.uuid4()))))
    assert await _nessuna_foglia(db_url)


async def test_scarto_con_il_fatto_nel_database_accettato(db_url):  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    async with fatto_ciclo_vita(db_url, pda, "scarto") as pratica:
        r = await _post(_corpo(pda.urn, pratica, aip=str(pda.aip_id)))
        assert r.status_code == 202, r.text
    righe = await sql(db_url, "SELECT tipo, pratica FROM public.catena_foglie_interne")
    assert [(x["tipo"], x["pratica"]) for x in righe] == [("SCARTO", pratica)]


@pytest.mark.parametrize("guasto", ["pda_conservato", "altro_ente", "non_avviata",
                                    "oggetto_non_eliminato", "pratica_respinta",
                                    "aip_diverso"])
async def test_scarto_senza_riscontro_completo_rifiutato(db_url, guasto):  # noqa: F811
    """Ogni condizione del riscontro dello scarto è necessaria."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    async with fatto_ciclo_vita(db_url, pda, "scarto") as pratica:
        if guasto == "pda_conservato":
            await sql(db_url, "UPDATE public.aip SET stato = 'CONSERVATO' WHERE id = $1",
                      pda.aip_id)
        elif guasto == "altro_ente":
            altro, _ = await crea_tenant_e_classe(db_url)
            await sql(db_url, "UPDATE public.pratiche SET tenant_id = $2 WHERE id = $1::uuid",
                      pratica, altro)
            await sql(db_url, "UPDATE public.pratiche_oggetti SET tenant_id = $2 "
                              "WHERE pratica_id = $1::uuid", pratica, altro)
        elif guasto == "non_avviata":
            await sql(db_url, "UPDATE public.pratiche SET dati = '{}'::jsonb "
                              "WHERE id = $1::uuid", pratica)
        elif guasto == "oggetto_non_eliminato":
            await sql(db_url, "UPDATE public.pratiche_oggetti SET esito = 'IN_CORSO' "
                              "WHERE pratica_id = $1::uuid", pratica)
        elif guasto == "pratica_respinta":
            await sql(db_url, "UPDATE public.pratiche SET stato = 'RESPINTA', lavoro = NULL "
                              "WHERE id = $1::uuid", pratica)
        extra = {"aip": str(uuid.uuid4())} if guasto == "aip_diverso" else {}
        _rifiuto(await _post(_corpo(pda.urn, pratica, **extra)))
    assert await _nessuna_foglia(db_url)


async def test_annullamento_e_rettifica_con_e_senza_riscontro(db_url):  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    # annullamento: senza PdV ANNULLATO → 409; con il fatto → 202
    async with fatto_ciclo_vita(db_url, pda, "annullamento", VERBALE) as pratica:
        await sql(db_url, "UPDATE public.pdv SET stato = 'CONSERVATO' WHERE id = $1", pda.pdv_id)
        _rifiuto(await _post(_corpo(pda.urn, pratica, tipo="annullamento")))
        await sql(db_url, "UPDATE public.pdv SET stato = 'ANNULLATO' WHERE id = $1", pda.pdv_id)
        assert (await _post(_corpo(pda.urn, pratica, tipo="annullamento"))).status_code == 202
    # rettifica di un altro PdV o già COMPLETATA → 409; in versamento → 202
    altro = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    async with fatto_ciclo_vita(db_url, altro, "rettifica", VERBALE) as pratica:
        _rifiuto(await _post(_corpo(pda.urn, pratica, tipo="rettifica",
                                    pdv_rettifica=str(altro.pdv_id))))
        assert (await _post(_corpo(altro.urn, pratica, tipo="rettifica",
                                   pdv_rettifica=str(altro.pdv_id)))).status_code == 202
    async with fatto_ciclo_vita(db_url, pda, "rettifica", VERBALE) as pratica:
        await sql(db_url, "UPDATE public.pratiche SET stato = 'COMPLETATA' WHERE id = $1::uuid",
                  pratica)
        _rifiuto(await _post(_corpo(pda.urn, pratica, tipo="rettifica",
                                    pdv_rettifica=str(pda.pdv_id))))
    righe = await sql(db_url, "SELECT tipo FROM public.catena_foglie_interne ORDER BY creata_il")
    assert [x["tipo"] for x in righe] == ["ANNULLAMENTO", "RETTIFICA"]
    voci = await sql(db_url, "SELECT dettagli FROM public.audit_global "
                             "WHERE evento = 'catena.foglia_interna_richiesta'")
    assert len(voci) == 2 and json.loads(voci[0]["dettagli"])["richiesta_da"] == "ciclo-vita"


async def test_api_interna_non_raggiungibile_dall_esterno(db_url):  # noqa: F811
    """Fuori da /api/ (vecchio percorso inesistente), 404 con X-Forwarded-For
    (richiesta arrivata dall'ALB), nessuna rotta dell'ingress la raggiunge."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    async with fatto_ciclo_vita(db_url, pda, "scarto") as pratica:
        vecchio = await _post(_corpo(pda.urn, pratica),
                              url="/api/v1/hashchain/interno/foglie")
        assert vecchio.status_code == 404
        dall_alb = await _post(_corpo(pda.urn, pratica), extra={"X-Forwarded-For": "1.2.3.4"})
        assert dall_alb.status_code == 404
    assert await _nessuna_foglia(db_url)
    from app.routers.interno import PREFISSO, router

    interne = [r.path for r in router.routes]
    assert PREFISSO == "/interno/v1/registro"
    assert interne and all(p.startswith(PREFISSO + "/") for p in interne)
    valori = yaml.safe_load((Path(__file__).resolve().parents[3] / "infra" / "helm" /
                             "values.yaml").read_text(encoding="utf-8"))
    for rotta in valori["ingress"]["routes"]:
        if rotta["service"] == "frontend":
            continue
        prefisso = rotta["path"].rstrip("/") + "/"
        assert not any(p.startswith(prefisso) for p in interne), rotta
    # nessuna operazione interna nell'OpenAPI pubblicata
    assert not any(p.startswith("/interno") for p in app.openapi()["paths"])
