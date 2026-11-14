# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R14 (2) — IPdA ricostruito a cavallo del rilascio.

Un PdA la cui costruzione è iniziata con il codice di R13b ha l'IPdA già nel WORM con il
namespace storico; se la costruzione si interrompe e riprende con il codice di R14, un IPdA
con il namespace nuovo avrebbe byte diversi e la scrittura idempotente lo rifiuterebbe
(AIP-STORAGE-001, errore permanente). Ora il formato dell'IPdA si fissa nel record di
costruzione al primo tentativo (``formato_ipda``; assente = di prima di R14) e si ricostruisce
con quello: stessi byte, costruzione completata. (Che il formato storico dia esattamente i
byte di R13b è verificato contro il codice di R13b nel rapporto di sviluppo.)
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from app.services import s3_uploader
from app.services.aip_engine import costruisci_pda
from app.services.sincro_builder import (
    FORMATO_ATTUALE,
    FORMATO_STORICO,
    NAMESPACE_STORICI,
    SINCRO_NS,
    build_sincro_xml,
    formato_della_costruzione,
)

from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.test_integrazione_pda import ADESSO, _pdv_validato, s3  # noqa: F401 — fixture

DOCS = [{"id": "d1", "nome_file": "a.pdf", "tipo": "PRINCIPALE", "formato": "application/pdf",
         "hash_sha256": "a" * 64, "dimensione_bytes": 10}]


def _ipda(formato: str) -> str:
    return build_sincro_xml(aip_id="a", pdv_id="p", tenant_id="t", classe_documentale="c",
                            documenti=DOCS, tipo_xml="DI", formato=formato,
                            data_versamento=datetime(2026, 3, 20, tzinfo=UTC)).decode()


def test_formato_storico_e_attuale() -> None:
    storico, attuale = _ipda(FORMATO_STORICO), _ipda(FORMATO_ATTUALE)
    assert f'xmlns="{NAMESPACE_STORICI[0]}"' in storico and "schemaLocation" in storico
    assert "urn:agid:sincro:versatore" in storico and "PROVVISORIO" not in storico
    assert f'xmlns="{SINCRO_NS}"' in attuale and "schemaLocation" not in attuale
    assert formato_della_costruzione({"urn": "u"}) == FORMATO_STORICO
    assert formato_della_costruzione({"formato_ipda": FORMATO_ATTUALE}) == FORMATO_ATTUALE
    with pytest.raises(ValueError):
        formato_della_costruzione({"formato_ipda": "inventato"})


@pytest.mark.integration
@richiede_pg
async def test_costruzione_iniziata_prima_di_r14_si_completa(db_url, s3) -> None:  # noqa: F811
    import app.services.aip_engine as motore

    evento = await _pdv_validato(db_url, s3)
    originale = s3_uploader.upload_aip_to_s3

    async def guasto_dopo_l_ipda(**kw):
        if kw.get("key_suffix") == "catena_pda.json":
            raise ConnectionError("rete")
        return await originale(**kw)

    # primo tentativo «con il codice di R13b»: IPdA storico nel WORM, poi un guasto
    motore.upload_aip_to_s3 = guasto_dopo_l_ipda
    vecchio = motore.FORMATO_ATTUALE
    motore.FORMATO_ATTUALE = FORMATO_STORICO
    try:
        with pytest.raises(ConnectionError):
            await costruisci_pda(evento, adesso=ADESSO)
    finally:
        motore.upload_aip_to_s3 = originale
        motore.FORMATO_ATTUALE = vecchio
    # il record di R13b non aveva il formato
    pdv = uuid.UUID(evento["pdv_id"])
    meta = json.loads((await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1",
                                 pdv))[0]["metadata_json"])
    meta["costruzione_pda"].pop("formato_ipda")
    await sql(db_url, "UPDATE public.pdv SET metadata_json = CAST($2 AS jsonb) WHERE id = $1",
              pdv, json.dumps(meta))
    # ripresa con il codice di R14: stesso IPdA, PdA costruito
    uscita = await costruisci_pda(evento, adesso=datetime(2026, 10, 21, tzinfo=UTC))
    riga = (await sql(db_url, "SELECT stato::text AS stato, s3_bucket, sincro_xml_key "
                              "FROM public.aip WHERE id = $1", uuid.UUID(uscita["aip_id"])))[0]
    assert riga["stato"] == "SU_STORAGE"
    xml = s3.get_object(Bucket=riga["s3_bucket"], Key=riga["sincro_xml_key"])["Body"].read()
    assert NAMESPACE_STORICI[0].encode() in xml


@pytest.mark.integration
@richiede_pg
async def test_costruzione_nuova_registra_il_formato(db_url, s3) -> None:  # noqa: F811
    evento = await _pdv_validato(db_url, s3)
    await costruisci_pda(evento, adesso=ADESSO)
    meta = json.loads((await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1",
                                 uuid.UUID(evento["pdv_id"])))[0]["metadata_json"])
    assert meta["costruzione_pda"]["formato_ipda"] == FORMATO_ATTUALE
