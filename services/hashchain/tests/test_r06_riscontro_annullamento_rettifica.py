# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R06 — VD-C-119: il riscontro di annullamento e rettifica pretende un FATTO
scritto dal solo ruolo del lavoratore di ciclo-vita.

Prima della correzione il riscontro delle foglie «annullamento» e «rettifica»
controllava solo righe (pratica, PdV ANNULLATO, pratica IN_VERSAMENTO) che il
login dell'API di ciclo-vita (esposta all'ingress) può scrivere: un'API
compromessa poteva far firmare dal registro un annullamento o una rettifica non
veri. Ora (migrazione 20261030_045) serve anche la riga di ``esiti_esecuzione``
(``ANNULLAMENTO``/``ATTO_ARCHIVIATO`` o ``RETTIFICA``/``PDV_VERSATO``), che il
login dell'API non può scrivere, con l'impronta dell'atto uguale a quella della
foglia e, per la rettifica, il PdV di rettifica uguale a quello della pratica e
del riferimento.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import asyncpg
import pytest

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401
from tests.supporto_registro import crea_pda
from tests.test_r05_revisione2_riscontro_foglie import _corpo, _nessuna_foglia, _post, _rifiuto
from tests.test_r05_revisione3_riscontro_esiti import _come_api

pytestmark = [pytest.mark.integration, richiede_pg]

ATTO = hashlib.sha256(b"atto vero").hexdigest()
ALTRO_ATTO = hashlib.sha256(b"atto diverso").hexdigest()


async def _fatto(url: str, tipo: str, pratica: str, pda, dettagli: dict) -> None:
    """Fatto del lavoratore (qui il superutente dei test, come in R05)."""
    await sql(url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, "
                   "esito, dettagli) VALUES ($1, $2::uuid, $3, $4, $5, CAST($6 AS jsonb))",
              tipo, pratica, pda.tenant, pda.aip_id,
              "ATTO_ARCHIVIATO" if tipo == "ANNULLAMENTO" else "PDV_VERSATO",
              json.dumps(dettagli))


async def test_annullamento_fabbricato_dall_api_rifiutato(db_url):  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    pratica = str(uuid.uuid4())
    # il login dell'API scrive la pratica eseguita e il PdV ANNULLATO
    await _come_api(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, "
                            "pdv_id) VALUES ($1::uuid, $2, 'ANNULLAMENTO', 'ESEGUITA', "
                            "'annullamento fabbricato', $3)", pratica, tenant, pda.pdv_id)
    await _come_api(db_url, "UPDATE public.pdv SET stato = 'ANNULLATO' WHERE id = $1",
                    pda.pdv_id)
    _rifiuto(await _post(_corpo(pda.urn, pratica, tipo="annullamento") | {
        "impronta_documento_pratica": ATTO}))
    assert await _nessuna_foglia(db_url)
    # il fatto il login dell'API non lo può scrivere
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _come_api(db_url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, "
                                "tenant_id, aip_id, esito, dettagli) VALUES ('ANNULLAMENTO', "
                                "$1::uuid, $2, $3, 'ATTO_ARCHIVIATO', $4::jsonb)",
                        pratica, tenant, pda.aip_id, json.dumps({"atto_sha256": ATTO}))
    # fatto con un'altra impronta dell'atto: rifiutata
    await _fatto(db_url, "ANNULLAMENTO", pratica, pda, {"atto_sha256": ALTRO_ATTO})
    _rifiuto(await _post(_corpo(pda.urn, pratica, tipo="annullamento") | {
        "impronta_documento_pratica": ATTO}))
    assert await _nessuna_foglia(db_url)
    # con il fatto dell'atto giusto → accettata
    r = await _post(_corpo(pda.urn, pratica, tipo="annullamento") | {
        "impronta_documento_pratica": ALTRO_ATTO})
    assert r.status_code == 202, r.text


async def test_rettifica_senza_pdv_versato_dal_lavoratore_rifiutata(db_url):  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    rettifica = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    pratica = str(uuid.uuid4())
    await _come_api(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, "
                            "pdv_id, pdv_risultato_id) VALUES ($1::uuid, $2, 'RETTIFICA', "
                            "'IN_VERSAMENTO', 'rettifica fabbricata', $3, $4)",
                    pratica, tenant, pda.pdv_id, rettifica.pdv_id)
    corpo = _corpo(pda.urn, pratica, tipo="rettifica", pdv_rettifica=str(rettifica.pdv_id)) | {
        "impronta_documento_pratica": ATTO}
    _rifiuto(await _post(corpo))
    # fatto con un PdV di rettifica diverso da quello della pratica: rifiutata
    await _fatto(db_url, "RETTIFICA", pratica, pda,
                 {"atto_sha256": ATTO, "pdv_rettifica": str(pda.pdv_id)})
    _rifiuto(await _post(corpo))
    assert await _nessuna_foglia(db_url)
    # fatto e riferimento d'accordo, ma la pratica indica un altro PdV di rettifica
    # (riga riscritta dal login dell'API dopo il versamento): rifiutata
    terzo = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    terza = str(uuid.uuid4())
    await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, pdv_id, "
                      "pdv_risultato_id) VALUES ($1::uuid, $2, 'RETTIFICA', 'IN_VERSAMENTO', "
                      "'rettifica di prova', $3, $3)", terza, tenant, terzo.pdv_id)
    await _fatto(db_url, "RETTIFICA", terza, terzo,
                 {"atto_sha256": ATTO, "pdv_rettifica": str(pda.pdv_id)})
    _rifiuto(await _post(_corpo(terzo.urn, terza, tipo="rettifica",
                                pdv_rettifica=str(pda.pdv_id))
                         | {"impronta_documento_pratica": ATTO}))
    assert await _nessuna_foglia(db_url)
    # fatto giusto (PdV e atto) → accettata
    altra = str(uuid.uuid4())
    await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, pdv_id, "
                      "pdv_risultato_id) VALUES ($1::uuid, $2, 'RETTIFICA', 'IN_VERSAMENTO', "
                      "'rettifica di prova', $3, $4)",
              altra, tenant, rettifica.pdv_id, rettifica.pdv_id)
    await _fatto(db_url, "RETTIFICA", altra, rettifica,
                 {"atto_sha256": ATTO, "pdv_rettifica": str(rettifica.pdv_id)})
    ok = _corpo(rettifica.urn, altra, tipo="rettifica", pdv_rettifica=str(rettifica.pdv_id)) | {
        "impronta_documento_pratica": ATTO}
    r = await _post(ok)
    assert r.status_code == 202, r.text


async def test_vincoli_dei_fatti_nuovi(db_url):  # noqa: F811
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, stato="CONSERVATO")
    pratica = str(uuid.uuid4())
    await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, pdv_id) "
                      "VALUES ($1::uuid, $2, 'ANNULLAMENTO', 'ESEGUITA', "
                      "'annullamento di prova', $3)",
              pratica, tenant, pda.pdv_id)
    for tipo, esito, dettagli in (
            ("ANNULLAMENTO", "PDV_VERSATO", {"atto_sha256": ATTO, "pdv_rettifica": "x"}),
            ("ANNULLAMENTO", "ATTO_ARCHIVIATO", {}),
            ("RETTIFICA", "PDV_VERSATO", {"atto_sha256": ATTO}),
            ("SCARTO", "ATTO_ARCHIVIATO", {"atto_sha256": ATTO})):
        with pytest.raises(asyncpg.CheckViolationError):
            await sql(db_url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, "
                              "aip_id, esito, dettagli) VALUES ($1, $2::uuid, $3, $4, $5, "
                              "CAST($6 AS jsonb))", tipo, pratica, tenant, pda.aip_id, esito,
                      json.dumps(dettagli))
