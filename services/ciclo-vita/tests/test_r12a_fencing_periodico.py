# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-151) — fencing token dei passi periodici del lavoratore di ciclo-vita.

Il lease si rinnova prima di ogni elemento (R09), ma un elemento più lungo del
lease poteva ancora scrivere dopo che un'altra replica aveva ripreso il passo.
Ora ogni presa alza la ``generazione`` e ogni scrittura dei fatti la riverifica
nella propria transazione (riga del passo FOR SHARE fino al commit):
  - lease ripreso da un'altra replica (o dalla stessa con una presa nuova) →
    ``LeasePassoPerso``, nessuna scrittura;
  - mentre una scrittura è in corso, un'altra replica che prova a prendere il
    lease aspetta il commit (nessuna sovrapposizione nella stessa transazione).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.application import periodico
from app.infrastructure.ambito import ambito_lavoratore

from libs.shared.db import get_async_session
from tests.infra_reale import richiede_pg, sql

pytestmark = [pytest.mark.integration, richiede_pg]

ADESSO = datetime.now(UTC)


async def _prendi(nome: str, chi: str) -> int:
    g = await periodico.prendi(nome, chi, datetime.now(UTC), timedelta(0))
    assert g is not None
    return g


async def test_generazione_cresce_a_ogni_presa(db_url) -> None:  # noqa: ARG001
    with ambito_lavoratore("prova R12a"):
        g1 = await _prendi("prova_fencing", "A")
        await sql(db_url, "UPDATE public.ciclo_vita_periodico SET lease_scade_il = now() - "
                          "interval '1 s', ultimo_giro = NULL WHERE nome = 'prova_fencing'")
        g2 = await _prendi("prova_fencing", "A")
    assert g2 == g1 + 1


async def test_scrittura_rifiutata_dopo_la_ripresa_del_lease(db_url) -> None:
    o = {"pratica_id": uuid.uuid4(), "aip_id": uuid.uuid4(), "tenant_id": uuid.uuid4()}
    with ambito_lavoratore("prova R12a"):
        g = await _prendi("prova_fencing2", "A")
        token = periodico._LEASE.set(("prova_fencing2", "A", g))
        try:
            # con il lease nostro: la scrittura procede (qui nessun PdA: None)
            assert await periodico._minimizza_uno(o, ADESSO) is None
            # lease scaduto e ripreso da un'altra replica (generazione nuova)
            await sql(db_url, "UPDATE public.ciclo_vita_periodico SET lease_scade_il = now() - "
                              "interval '1 s', ultimo_giro = NULL WHERE nome = 'prova_fencing2'")
            assert await periodico.prendi("prova_fencing2", "B", datetime.now(UTC),
                                          timedelta(0)) == g + 1
            with pytest.raises(periodico.LeasePassoPerso):
                await periodico._minimizza_uno(o, ADESSO)
            # anche la stessa replica con una presa nuova invalida la vecchia
            await sql(db_url, "UPDATE public.ciclo_vita_periodico SET lease_detentore = 'A' "
                              "WHERE nome = 'prova_fencing2'")
            with pytest.raises(periodico.LeasePassoPerso):
                async with get_async_session() as s:
                    await periodico.verifica_fencing(s)
            with pytest.raises(periodico.LeasePassoPerso):
                await periodico.tieni_lease()
        finally:
            periodico._LEASE.reset(token)


async def test_presa_concorrente_aspetta_il_commit_della_scrittura(db_url) -> None:  # noqa: ARG001
    with ambito_lavoratore("prova R12a"):
        g = await _prendi("prova_fencing3", "A")
        token = periodico._LEASE.set(("prova_fencing3", "A", g))
        try:
            async with get_async_session() as s:
                await periodico.verifica_fencing(s)
                # B prova a riprendere il lease mentre A scrive: aspetta il commit di A
                presa = asyncio.create_task(_presa_dopo_scadenza(db_url, "prova_fencing3"))
                await asyncio.sleep(0.5)
                assert not presa.done()
            assert await asyncio.wait_for(presa, 10) == g + 1
        finally:
            periodico._LEASE.reset(token)


async def _presa_dopo_scadenza(db_url, nome: str) -> int | None:
    await sql(db_url, "UPDATE public.ciclo_vita_periodico SET lease_scade_il = now() - "
                      "interval '1 s', ultimo_giro = NULL, lease_detentore = 'B', "
                      "generazione = generazione + 1 WHERE nome = $1", nome)
    righe = await sql(db_url, "SELECT generazione FROM public.ciclo_vita_periodico "
                              "WHERE nome = $1", nome)
    return int(righe[0]["generazione"])


async def test_vdc150_metriche_per_passo(db_url) -> None:
    """R12a (VD-C-150): età, intervallo ed errore PER PASSO (allarme PassoPeriodicoFermo)."""
    from app.application import metriche
    from prometheus_client import CollectorRegistry

    registro = CollectorRegistry()
    metriche.registra(registro)
    await sql(db_url, "INSERT INTO public.ciclo_vita_periodico (nome, ultimo_giro, ultimo_esito) "
                      "VALUES ('rinnovo_retention', now() - interval '3 days', "
                      "'{\"errore\": \"RinnovoNonPossibile\"}'), ('riprese_scarto', now(), '{}') "
                      "ON CONFLICT (nome) DO UPDATE SET ultimo_giro = EXCLUDED.ultimo_giro, "
                      "ultimo_esito = EXCLUDED.ultimo_esito")
    await metriche.aggiorna()

    def valore(nome: str, passo: str) -> float | None:
        return registro.get_sample_value(nome, {"passo": passo})

    assert valore("verumdoc_ciclo_vita_passo_periodico_in_errore", "rinnovo_retention") == 1
    assert valore("verumdoc_ciclo_vita_passo_periodico_in_errore", "riprese_scarto") == 0
    eta = valore("verumdoc_ciclo_vita_passo_periodico_eta_secondi", "rinnovo_retention") or 0
    intervallo = valore("verumdoc_ciclo_vita_passo_periodico_intervallo_secondi",
                        "rinnovo_retention") or 0
    assert eta > 2 * intervallo + 3600      # condizione di PassoPeriodicoFermo
    assert (valore("verumdoc_ciclo_vita_passo_periodico_eta_secondi", "riprese_scarto")
            or 0) < 60
