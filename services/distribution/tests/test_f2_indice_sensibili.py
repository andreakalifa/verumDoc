# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 (REV-11) — nessun metadato marcato «sensibile» dal profilo entra in
chiaro nell'indice di ricerca: nemmeno i campi base (oggetto, nomi dei file,
produttore), le liste dell'Allegato 5 né le chiavi annidate dei metadati
liberi (``dati_specifici``). Prima l'esclusione valeva solo per alcuni campi
del testo completo e per le chiavi di primo livello di ``metadati_extra``.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

from app.services.search_index import NOMI_CAMPO, documento_ricerca

from tests import infra_reale, pda_reale
from tests.infra_reale import richiede_pg, sql
from tests.pda_reale import crea_pda

# fixture condivise
db_url = infra_reale.db_url
s3_server = infra_reale.s3_server
s3 = pda_reale.s3

SEGRETI = {
    "oggetto": "Ricovero di Mario Rossi",
    "produttore": "Studio medico Bianchi",
    "file": "referto_rossi_mario.pdf",
    "identificativo": "CART-ROSSI-1",
    "registrazione": "PG/RISERVATO/9",
    "annidato": "FTZPRV00A01Z999V",
}


def _aip() -> dict:
    return {"aip_id": str(uuid.uuid4()), "pdv_id": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()), "urn": "urn:x", "stato": "CONSERVATO",
            "classe_documentale": "cartella_clinica", "oggetto": SEGRETI["oggetto"],
            "nomi_file": [SEGRETI["file"]],
            "chiave": {"numero": "9", "anno": "2026", "registro": "RISERVATO"}}


def _metadati() -> list[dict]:
    return [{"identificativo_documento": SEGRETI["identificativo"],
             "tipologia_documentale": "referto", "oggetto": SEGRETI["oggetto"],
             "soggetto_produttore_nome": SEGRETI["produttore"],
             "numero_registrazione": SEGRETI["registrazione"],
             "data_documento": date(2026, 3, 1),
             "metadati_extra": {"reparto": "Cardiologia",
                                "dati_specifici": {"codice_fiscale": SEGRETI["annidato"],
                                                   "letto": "12"}}}]


def test_documento_ricerca_esclude_campi_base_sensibili() -> None:
    sensibili = {"oggetto", "soggetto_produttore_nome", "nome_file", "identificativo_documento",
                 "numero_registrazione", "codice_fiscale", "registro"}
    doc = documento_ricerca(_aip(), _metadati(), sensibili=sensibili)
    testo = json.dumps(doc, ensure_ascii=False, default=str)
    for valore in SEGRETI.values():
        assert valore not in testo, valore
    for campo in ("oggetto", "soggetti_produttori", "nomi_file", "identificativi_documento",
                  "numeri_registrazione", "registro"):
        assert campo not in doc, campo
    # ciò che non è sensibile resta cercabile, anche annidato
    assert "Cardiologia" in doc["testo_completo"] and '"letto": "12"' in doc["testo_completo"]
    assert doc["tipologie_documentali"] == ["referto"] and doc["numero"] == "9"


def test_documento_ricerca_senza_sensibili_invariato() -> None:
    doc = documento_ricerca(_aip(), _metadati())
    assert doc["oggetto"] == SEGRETI["oggetto"] and doc["nomi_file"] == [SEGRETI["file"]]
    assert SEGRETI["annidato"] in doc["testo_completo"]
    assert doc["identificativi_documento"] == [SEGRETI["identificativo"]]


def test_nomi_campo_coprono_ogni_campo_testuale_del_documento() -> None:
    """Ogni campo dell'indice con dati del cliente ha i suoi nomi di metadato."""
    doc = documento_ricerca(_aip(), _metadati())
    di_sistema = {"aip_id", "pdv_id", "tenant_id", "urn", "stato", "classe_documentale",
                  "hash_sha256", "data_versamento", "testo_completo", "anni_conservazione",
                  "dimensione_bytes", "data_conservazione", "scadenza_conservazione"}
    assert set(doc) - di_sistema <= set(NOMI_CAMPO)


@richiede_pg
async def test_indicizzazione_profilo_con_oggetto_sensibile(db_url, s3, monkeypatch) -> None:
    """Dal database all'indice: profilo che marca sensibili oggetto e nome del
    file → nessuno dei due nel documento inviato a OpenSearch."""
    from app.config import settings
    from app.services.indicizzazione import elabora_lotto

    from tests.opensearch_finto import OpenSearchFinto

    finto = OpenSearchFinto().avvia()
    monkeypatch.setattr(settings, "opensearch_url", finto.url)
    try:
        classe = (await sql(db_url, "SELECT id FROM public.classi_documentali WHERE "
                                    "codice = 'fattura' AND tenant_id IS NULL"))[0]["id"]
        profilo = uuid.uuid4()
        await sql(db_url, "INSERT INTO public.profili_metadati (id, classe_id, versione, "
                          "valido_dal, campi) VALUES ($1, $2, 1, DATE '2026-01-01', "
                          "CAST($3 AS jsonb))", profilo, classe, json.dumps([
                              {"nome": "oggetto", "tipo": "stringa", "sensibile": True},
                              {"nome": "nome_file", "tipo": "stringa", "sensibile": True}]))
        pda = await crea_pda(db_url, s3, oggetto=SEGRETI["oggetto"],
                             documenti=[(SEGRETI["file"], b"%PDF-1.7 x\n%%EOF\n")],
                             regole_applicate={"profilo_metadati": {"id": str(profilo)}})
        adesso = datetime.now(UTC)
        await sql(db_url, "INSERT INTO public.indicizzazione_aip (aip_id, tenant_id, "
                          "prossimo_tentativo) VALUES ($1, $2, $3)",
                  pda["aip"], pda["tenant"], adesso - timedelta(seconds=1))
        await elabora_lotto(adesso=adesso)
        inviato = json.dumps(finto.documenti[str(pda["aip"])], ensure_ascii=False)
    finally:
        finto.ferma()
    assert SEGRETI["oggetto"] not in inviato and SEGRETI["file"] not in inviato
    assert "Comune di Prova" in inviato
