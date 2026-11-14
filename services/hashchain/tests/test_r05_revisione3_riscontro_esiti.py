# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 3 di R05 — rilievo T5: il riscontro dello scarto pretende un FATTO che
solo il lavoratore di ciclo-vita può scrivere.

Prima della correzione il riscontro (revisione 2, S2) controllava righe che il
login dell'API di ciclo-vita (esposta all'ingress, stesso login del lavoratore,
membro di ``verumdoc_applicazione``) può scrivere: pratica SCARTO ESEGUIBILE con
il lavoro e l'esecuzione avviata, oggetto ELIMINATO, PdA in ELIMINAZIONE. Un pod
API compromesso le scriveva per un PdA integro, il lavoratore legittimo chiedeva
il tombstone e il registro firmava uno scarto mai avvenuto.

Ora il riscontro richiede anche la riga di ``esiti_esecuzione`` (migrazione
20261023_043), che ``verumdoc_applicazione`` può solo leggere: la scrive solo
``verumdoc_ciclo_vita_lavoratore``, nella transazione che chiude l'oggetto
ELIMINATO.
"""
from __future__ import annotations

import asyncpg
import pytest

from libs.shared.db.ruolo_prova import RUOLO_SERVIZIO_PROVA, url_ruolo_servizio
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401
from tests.supporto_registro import crea_pda
from tests.test_r05_revisione2_riscontro_foglie import _corpo, _nessuna_foglia, _post, _rifiuto

pytestmark = [pytest.mark.integration, richiede_pg]


async def _come_api(url: str, istruzione: str, *argomenti) -> None:
    """Istruzione con il login dei servizi (membro del solo verumdoc_applicazione,
    come l'API di ciclo-vita in esercizio), nell'ambito del conservatore."""
    dsn = (await url_ruolo_servizio(url)).replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.ambito', 'conservatore', true)")
            await conn.execute(istruzione, *argomenti)
    finally:
        await conn.close()


async def test_fatti_scritti_dal_login_dell_api_non_bastano(db_url):  # noqa: F811
    """Lo scenario del revisore: il login dell'API scrive pratica, oggetto
    ELIMINATO e PdA in ELIMINAZIONE per un PdA integro → 409, nessuna foglia;
    l'esito di esecuzione lo stesso login NON lo può scrivere."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    pratica = "6f1c1a52-6a1d-4c39-9d1e-3f7a7f1c0001"
    await _come_api(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, "
                            "dati, lavoro) VALUES ($1::uuid, $2, 'SCARTO', 'ESEGUIBILE', "
                            "'pratica fabbricata', '{\"esecuzione\": {\"avviata_il\": "
                            "\"2026-10-23T08:00:00+00:00\"}}'::jsonb, 'ESEGUI')",
                    pratica, tenant)
    await _come_api(db_url, "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, "
                            "urn, esito) VALUES ($1::uuid, $2, $3, $4, 'ELIMINATO')",
                    pratica, tenant, pda.aip_id, pda.urn)
    await _come_api(db_url, "UPDATE public.aip SET stato = 'ELIMINAZIONE' WHERE id = $1",
                    pda.aip_id)
    _rifiuto(await _post(_corpo(pda.urn, pratica, aip=str(pda.aip_id))))
    assert await _nessuna_foglia(db_url)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _come_api(db_url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, "
                                "tenant_id, aip_id, esito) VALUES ('SCARTO', $1::uuid, $2, $3, "
                                "'ELIMINATO')", pratica, tenant, pda.aip_id)
    assert await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione") == []
    # con il fatto scritto dal lavoratore (qui il superutente dei test) → accettata
    await sql(db_url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, "
                      "esito) VALUES ('SCARTO', $1::uuid, $2, $3, 'ELIMINATO')",
              pratica, tenant, pda.aip_id)
    r = await _post(_corpo(pda.urn, pratica, aip=str(pda.aip_id)))
    assert r.status_code == 202, r.text


async def test_esiti_append_only_e_privilegi(db_url):  # noqa: F811
    """``verumdoc_applicazione`` solo SELECT; nessuno (nemmeno il proprietario)
    modifica o cancella un esito."""
    righe = await sql(db_url, """
        SELECT r.rolname, has_table_privilege(r.rolname, 'public.esiti_esecuzione', p) AS ok, p
        FROM pg_roles r, unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p
        WHERE r.rolname IN ('verumdoc_applicazione', 'verumdoc_ciclo_vita_lavoratore', $1)""",
                      RUOLO_SERVIZIO_PROVA)
    diritti = {(x["rolname"], x["p"]) for x in righe if x["ok"]}
    assert diritti == {
        ("verumdoc_applicazione", "SELECT"), (RUOLO_SERVIZIO_PROVA, "SELECT"),
        ("verumdoc_ciclo_vita_lavoratore", "SELECT"),
        ("verumdoc_ciclo_vita_lavoratore", "INSERT")}
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    pratica = "6f1c1a52-6a1d-4c39-9d1e-3f7a7f1c0002"
    await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo) "
                      "VALUES ($1::uuid, $2, 'SCARTO', 'ESEGUIBILE', "
                      "'pratica di prova degli esiti')", pratica, tenant)
    await sql(db_url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, "
                      "esito) VALUES ('SCARTO', $1::uuid, $2, $3, 'ELIMINATO')",
              pratica, tenant, pda.aip_id)
    for istruzione in ("UPDATE public.esiti_esecuzione SET esito = 'ELIMINATO'",
                       "DELETE FROM public.esiti_esecuzione",
                       "TRUNCATE public.esiti_esecuzione"):
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await sql(db_url, istruzione)
