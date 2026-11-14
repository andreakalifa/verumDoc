# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-155) — il lavoratore riverifica il permesso del RUOLO PERSONALIZZATO
del richiedente di un annullamento (e di una rettifica), non solo il ruolo base.

  - permesso ancora concesso: l'annullamento si applica come prima;
  - permesso tolto dal ruolo dopo la richiesta, o non più concedibile per l'ente:
    la pratica si chiude RESPINTA dal SISTEMA (stato cambiato, PdV intatto), con
    il difetto ``PERMESSO_ATTUALE_NON_AMMESSO``.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, lavora
from tests.test_r06_revisione_puntuali_autorizzazioni import _ambiente_con_vittima, _dip

pytestmark = [pytest.mark.integration, richiede_pg]


def _token_rp(tenant, utente, rp: str, permessi: list[str], pv: int) -> dict[str, str]:  # type: ignore[no-untyped-def]
    from app.api.autenticazione import chiavi_jwt
    from app.config import settings

    from libs.shared.security.jwt_rs256 import firma_token

    tok = firma_token(
        {"sub": str(utente), "tenant_id": str(tenant), "user_id": str(utente),
         "ruolo": "OPERATORE", "scopes": ["read"], "rp": rp, "permessi": permessi, "pv": pv},
        chiave_privata_pem=chiavi_jwt().privata_pem, emittente=settings.jwt_issuer,
        pubblico=settings.jwt_audience, durata_secondi=600)
    return {"Authorization": f"Bearer {tok}"}


async def _operatore_con_ruolo(db_url, tenant) -> tuple[uuid.UUID, str, int]:  # type: ignore[no-untyped-def]
    from tests.supporto import crea_utente

    utente = await crea_utente(db_url, tenant, "OPERATORE")
    rp = (await sql(db_url, "INSERT INTO public.ruoli_personalizzati (tenant_id, nome, "
                            "descrizione, ruolo_base, permessi) VALUES ($1, 'Annullatori', "
                            "'prova R12a', 'OPERATORE', ARRAY['consulta', "
                            "'annullamento:richiedi']) RETURNING id::text AS id", tenant))[0]["id"]
    pv = (await sql(db_url, "UPDATE public.users SET ruolo_personalizzato_id = $1::uuid "
                            "WHERE id = $2 RETURNING permessi_versione AS pv", rp, utente))[0]["pv"]
    return utente, rp, int(pv)


async def _richiedi(db_url, s3_ciclo) -> tuple[dict, str, str]:  # type: ignore[no-untyped-def]
    a = await _ambiente_con_vittima(db_url, s3_ciclo)
    # il PdV di prova è conservato: anche l'operatore può annullarlo (conservatore)
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                      "valore, motivo) VALUES ('CONSERVATORE', NULL, "
                      "'ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE', CAST($1 AS jsonb), 'prova R12a')",
              json.dumps(["ADMIN_TENANT", "OPERATORE"]))
    utente, rp, pv = await _operatore_con_ruolo(db_url, a["tenant"])
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=_token_rp(a["tenant"], utente, rp,
                                           ["consulta", "annullamento:richiedi"], pv),
                         json={"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"})
    assert r.status_code == 201, r.text
    return a, rp, r.json()["id"]


async def _esito(db_url, pratica: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    riga = (await sql(db_url, "SELECT p.stato, (SELECT string_agg(s.motivo, ' | ') FROM "
                              "public.pratiche_stati s WHERE s.pratica_id = p.id) || ' | ' || "
                              "COALESCE(p.dati ->> 'respinta_dal_sistema', '') AS motivi "
                              "FROM public.pratiche p WHERE p.id = $1::uuid", pratica))[0]
    return riga["stato"], riga["motivi"] or ""


async def test_permesso_ancora_concesso_si_applica(db_url, s3_ciclo) -> None:
    a, _, pratica = await _richiedi(db_url, s3_ciclo)
    await lavora(_dip(db_url))
    fatti = await sql(db_url, "SELECT esito FROM public.esiti_esecuzione WHERE pratica_id = "
                              "$1::uuid", pratica)
    assert "AUTORIZZAZIONI_VERIFICATE" in {f["esito"] for f in fatti}
    pdv = await sql(db_url, "SELECT stato::text AS s FROM public.pdv WHERE id = $1", a["pdv"])
    assert pdv[0]["s"] == "ANNULLATO"


@pytest.mark.parametrize("come", ["tolto_dal_ruolo", "non_piu_concedibile"])
async def test_permesso_tolto_dopo_la_richiesta_respinge(db_url, s3_ciclo, come) -> None:
    a, rp, pratica = await _richiedi(db_url, s3_ciclo)
    if come == "tolto_dal_ruolo":
        await sql(db_url, "UPDATE public.ruoli_personalizzati SET permessi = ARRAY['consulta'] "
                          "WHERE id = $1::uuid", rp)
    else:
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                          "valore, motivo) VALUES ('CONSERVATORE', NULL, "
                          "'RUOLI_PERMESSI_CONCEDIBILI', CAST($1 AS jsonb), 'prova R12a')",
                  json.dumps(["rettifica:richiedi"]))
    await lavora(_dip(db_url))
    stato, motivi = await _esito(db_url, pratica)
    assert stato == "RESPINTA", motivi
    assert "PERMESSO_ATTUALE_NON_AMMESSO" in motivi
    pdv = await sql(db_url, "SELECT stato::text AS s FROM public.pdv WHERE id = $1", a["pdv"])
    assert pdv[0]["s"] != "ANNULLATO"
