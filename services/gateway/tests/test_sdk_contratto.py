# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Contratto SDK ↔ gateway reale (VD-C-091): l'SDK Python 3 (``sdk/verumdoc_sdk``)
chiama l'app FastAPI VERA del gateway in-process (ASGITransport), su PostgreSQL
migrato e S3 moto, e i suoi modelli leggono le risposte vere SENZA campi
sconosciuti (``model_extra`` vuoto): se il gateway cambia una risposta, questo
test lo scopre.

Coperti: versamento multipart (202, ripetizione 200 con la stessa
Idempotency-Key), sessioni di caricamento a parti con verifica fuori richiesta
e versamento per riferimento, stato del PdV, errori problem+json tipizzati
(NonValido, Rifiutato, ErroreTemporaneo) con il modello ``Problema``.
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from app.config import settings
from app.services import caricamenti as car

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server  # noqa: F401
from tests.supporto_canali import (  # noqa: F401 — fixture
    MB,
    MINIMI,
    PDF,
    client_http,
    pdf_di,
    s3,
    senza_lavoratore,
    utente_di,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "sdk"))
from verumdoc_sdk import (  # noqa: E402
    ChiaveApi,
    ErroreTemporaneo,
    NonValido,
    PoliticaAttesa,
    PoliticaRitentativi,
    Rifiutato,
    VerumDocAsincrono,
)
from verumdoc_sdk.modelli import (  # noqa: E402
    ParteAccettata,
    SessioneCaricamento,
    StatoCaricamento,
)

pytestmark = richiede_pg


def _indice(nome: str, *, numero: str = "FT-SDK-1", classe: str = "fattura") -> dict:
    """Indice SENZA impronta: la calcola l'SDK dai byte."""
    return {"classe_documentale": classe,
            "chiave": {"numero": numero, "anno": "2026", "registro": "FATTURE"},
            "documenti": [{"id": "doc-1", "tipo": "PRINCIPALE", "nome_file": nome,
                           "formato": "application/pdf", "parametri": dict(MINIMI)}]}


def _sdk(http) -> VerumDocAsincrono:
    async def senza_attesa(_: float) -> None:
        await asyncio.sleep(0)

    return VerumDocAsincrono(
        "http://test", credenziali=ChiaveApi("vd_abcdef12_segreto"), http=http,
        ritentativi=PoliticaRitentativi(tentativi=2), dormi=senza_attesa,
        attesa=PoliticaAttesa(intervallo_iniziale=0.05, fattore=1, timeout=30))


def _nessun_campo_sconosciuto(modello) -> None:
    """Il modello dell'SDK descrive TUTTI i campi della risposta vera."""
    assert not modello.model_extra, f"{type(modello).__name__}: campi non previsti " \
                                    f"{sorted(modello.model_extra)}"
    for valore in modello.__dict__.values():
        voci = valore if isinstance(valore, list) else [valore]
        for v in voci:
            if hasattr(v, "model_extra") and v.model_extra is not None:
                _nessun_campo_sconosciuto(v)


@pytest.fixture(autouse=True)
def parti_da_5_mb(monkeypatch):
    monkeypatch.setattr(settings, "caricamenti_dimensione_parte_mb", 5)


async def _verificatore(fine: asyncio.Event) -> None:
    """Il lavoratore di verifica dei caricamenti, come nel processo del gateway."""
    while not fine.is_set():
        await car.verifica_in_sospeso()
        await asyncio.sleep(0.02)


async def test_sdk_versamento_multipart_e_stato_sul_gateway_reale(db_url, s3, utente_di):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    async with client_http() as http:
        vd = _sdk(http)
        esito = await vd.versa(_indice("fattura.pdf"), file={"fattura.pdf": PDF})
        ripetuto = await vd.versa(_indice("fattura.pdf"), file={"fattura.pdf": PDF},
                                  chiave_idempotenza=esito.chiave_idempotenza)
        stato = await vd.stato_versamento(esito.pdv_id)
    assert esito.stato_http == 202 and not esito.ripetuto
    assert esito.location == f"/api/v1/versamento/{esito.pdv_id}"
    _nessun_campo_sconosciuto(esito.risposta)
    assert ripetuto.ripetuto and ripetuto.stato_http == 200
    assert ripetuto.pdv_id == esito.pdv_id
    assert stato.pdv_id == esito.pdv_id and stato.stato == "RICEVUTO"
    assert stato.classe_documentale == "fattura"
    _nessun_campo_sconosciuto(stato)


async def test_sdk_sessioni_di_caricamento_e_versamento_per_riferimento(
        db_url, s3, utente_di, senza_lavoratore):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    contenuto = pdf_di(11 * MB, seme=7)  # 3 parti da 5 MB
    fine = asyncio.Event()
    verificatore = asyncio.create_task(_verificatore(fine))
    try:
        async with client_http() as http:
            vd = _sdk(http)
            vd.soglia_multipart = 1 * MB
            # Modelli delle singole chiamate C1
            sessione, nuova = await vd.apri_caricamento("prova.pdf", 6 * MB,
                                                        chiave_idempotenza="sdk-contratto-01")
            parte = await vd.invia_parte(sessione.caricamento_id, 2, b"x" * MB)
            letto = await vd.stato_caricamento(sessione.caricamento_id)
            stessa, nuova_bis = await vd.apri_caricamento("prova.pdf", 6 * MB,
                                                          chiave_idempotenza="sdk-contratto-01")
            # Versamento completo per riferimento (parti parallele, verifica, JSON)
            esito = await vd.versa(_indice("grande.pdf", numero="FT-SDK-2"),
                                   file={"grande.pdf": contenuto})
    finally:
        fine.set()
        await verificatore
    for modello, tipo in ((sessione, SessioneCaricamento), (parte, ParteAccettata),
                          (letto, StatoCaricamento)):
        assert isinstance(modello, tipo)
        _nessun_campo_sconosciuto(modello)
    assert nuova and not nuova_bis and stessa.caricamento_id == sessione.caricamento_id
    assert letto.parti_ricevute == [2] and letto.stato == "APERTO"
    assert esito.stato_http == 202
    [cid] = esito.caricamenti.values()
    async with client_http() as http:
        finale = await _sdk(http).stato_caricamento(cid)
    _nessun_campo_sconosciuto(finale)
    assert finale.stato == "COMPLETO" and finale.pdv_id == esito.pdv_id
    assert finale.numero_parti == 3


async def test_sdk_errori_tipizzati_dal_gateway_reale(db_url, s3, utente_di):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    sbagliato = _indice("fattura.pdf", numero="FT-SDK-3")
    sbagliato["documenti"][0]["hash"] = {"impronta": "0" * 64, "algoritmo": "SHA-256"}
    async with client_http() as http:
        vd = _sdk(http)
        with pytest.raises(NonValido) as impronta:
            await vd.versa(sbagliato, file={"fattura.pdf": PDF})
        with pytest.raises(Rifiutato) as classe:
            await vd.versa(_indice("fattura.pdf", classe="inesistente"),
                           file={"fattura.pdf": PDF})
        with pytest.raises(NonValido) as non_trovato:
            await vd.stato_versamento(str(uuid.uuid4()))
        with pytest.raises(NonValido) as validazione:
            await vd.elenca_eventi()  # rotta di notification: sul gateway non esiste
        esito = await vd.versa(_indice("fattura.pdf", numero="FT-SDK-4"),
                               file={"fattura.pdf": PDF})
        with pytest.raises(ErroreTemporaneo) as non_emesso:
            await vd.scarica_rapporto(esito.pdv_id)
    assert impronta.value.codice == "VRS-102" and impronta.value.status == 400
    assert classe.value.codice == "VRS-105"
    assert non_trovato.value.codice == "VRS-404" and non_trovato.value.status == 404
    assert validazione.value.status == 404
    assert non_emesso.value.codice == "VRS-409" and non_emesso.value.ritentabile
    for e in (impronta, classe, non_trovato, non_emesso):
        assert e.value.id_richiesta
        _nessun_campo_sconosciuto(e.value.problema)


async def test_sdk_stato_annullato_e_rettificato(db_url, s3, utente_di):
    """R05 (ciclo di vita): ``annullamento`` e ``rettifica`` compaiono solo se
    valorizzati; l'SDK 3.2 li descrive tutti (nessun campo sconosciuto)."""
    import json

    from tests.infra_reale import sql

    tenant, classe = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    originale, rettifica, annullato = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pratica_ann, pratica_ret = uuid.uuid4(), uuid.uuid4()
    for pdv, stato, meta in (
            (originale, "CONSERVATO", {}), (rettifica, "RICEVUTO", {}),
            (annullato, "ANNULLATO", {"annullamento": {
                "pratica_id": str(pratica_ann), "il": "2026-10-23T10:00:00+00:00",
                "stato_precedente": "CONSERVATO"}})):
        await sql(db_url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, "
                          "tenant_id, metadata_json) VALUES ($1, 'prova', $2, $3, $4, $5, "
                          "CAST($6 AS jsonb))", pdv, stato, classe, uuid.uuid4(), tenant,
                  json.dumps(meta))
    await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, pdv_id, "
                      "pdv_risultato_id) VALUES ($1, $2, 'RETTIFICA', 'IN_VERSAMENTO', "
                      "'rettifica di prova', $3, $4)", pratica_ret, tenant, originale, rettifica)
    async with client_http() as http:
        vd = _sdk(http)
        stato_ann = await vd.stato_versamento(str(annullato))
        stato_orig = await vd.stato_versamento(str(originale))
        stato_ret = await vd.stato_versamento(str(rettifica))
    assert stato_ann.annullamento is not None
    assert stato_ann.annullamento.pratica_id == str(pratica_ann)
    assert stato_orig.rettifica.rettificato_da[0]["pdv_rettifica_id"] == str(rettifica)
    assert stato_ret.rettifica.rettifica_di["pdv_id"] == str(originale)
    for stato in (stato_ann, stato_orig, stato_ret):
        _nessun_campo_sconosciuto(stato)
