# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R10 (lato distribution).

Lo scarto (ciclo-vita) blocca il PdA ``FOR UPDATE``, lo ricontrolla e lo passa a
ELIMINAZIONE prima di cancellarne le versioni. distribution, quando registra un
PdD, blocca lo stesso PdA ``FOR SHARE`` e ne ricontrolla lo stato nella stessa
transazione: un PdD non si registra mai su un PdA già in ELIMINAZIONE (prima il
controllo dello stato era in una transazione precedente, senza lock).
"""
from __future__ import annotations

import uuid

import pytest

from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_ente
from libs.shared.errori import ErroreApi
from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.pda_reale import crea_pda, s3  # noqa: F401

pytestmark = richiede_pg


async def test_pdd_non_registrato_su_pda_in_eliminazione(db_url, s3) -> None:  # noqa: F811
    from app.models.distribution import RichiestaDistribuzione, RispostaDistribuzione
    from app.services.registro_pdd import registra_pdd

    pda = await crea_pda(db_url, s3)
    # lo scarto ha già bloccato e portato il PdA in ELIMINAZIONE
    await sql(db_url, "UPDATE public.aip SET stato = 'ELIMINAZIONE' WHERE id = $1", pda["aip"])
    req = RichiestaDistribuzione(aip_id=str(pda["aip"]), tipo="COMPLETO",
                                 motivo="verifica interna")
    risposta = RispostaDistribuzione(pdd_id=str(uuid.uuid4()), aip_id=str(pda["aip"]),
                                     tipo="COMPLETO", documenti=[], sincro_xml_incluso=False,
                                     hash_pdd=None, created_at="2026-10-23T00:00:00+00:00")
    with ambito_ente(pda["tenant"]), pytest.raises(ErroreApi) as errore:
        async with get_async_session() as session:
            await registra_pdd(session, risultato=risposta, req=req,
                               tenant_id=str(pda["tenant"]), richiedente=str(uuid.uuid4()),
                               canale="API")
    assert errore.value.codice == "DST-101"
    assert await sql(db_url, "SELECT 1 FROM public.pacchetti_distribuzione "
                             "WHERE aip_id = $1", pda["aip"]) == []
