# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f3 — correzioni dei rilievi della seconda revisione (REV-N1, REV-N2).

Con PostgreSQL migrato e S3 (moto), come le prove del revisore
(``r03/rev/test_rev2_*.py``), da cui derivano i primi test di ogni
gruppo: prima della correzione fallivano, dopo passano.

  - REV-N1: la pulizia dello staging non si blocca sui PdV non verificabili
    (rinvio per riga con attesa crescente, massimo di tentativi, PdV bloccati
    segnalati in log e metrica, costo di un giro limitato dal lotto);
  - REV-N2: multipart REST con un tetto di richieste contemporanee per
    replica e un budget unico di byte su /tmp condiviso con PDND (503
    ritentabile del catalogo, corpo non letto).
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.main import app
from app.routers import pdnd as pdnd_router
from app.services import corpo, pulizia_staging
from app.services.pulizia_staging import pulisci_staging
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from libs.shared.errori import ErroreApi
from tests.infra_reale import (  # noqa: F401
    _asyncpg_dsn,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)
from tests.supporto_canali import (  # noqa: F401 — fixture
    MB,
    PDF,
    chiavi_staging,
    client_http,
    s3,
    utente_di,
)
from tests.test_f1_correzioni_canali import _app_pdnd, _versa_pdnd
from tests.test_f1_pulizia_staging import _pda, _versa
from tests.test_pdnd import _corpo as _corpo_pdnd
from tests.test_pdnd import _registra_fruitore, chiave_pdnd  # noqa: F401

pytestmark = [richiede_pg, pytest.mark.integration]

async def _giro(s3, adesso: datetime, **kwargs):
    return await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso,
                                 **kwargs)


async def _stato_pulizia(url: str, pdv_id: str):
    [riga] = await sql(url, "SELECT staging_eliminato_il, staging_verifica_prossima, "
                            "staging_verifica_tentativi, staging_verifica_motivo, "
                            "staging_verifica_bloccata_il FROM public.pdv WHERE id = $1",
                       uuid.UUID(pdv_id))
    return riga


# ── REV-N1: pulizia dello staging senza starvation ───────────────────

async def test_pdv_non_verificabili_non_bloccano_i_piu_recenti(db_url, s3, utente_di):
    """Prova del revisore (test_rev2_pulizia_starvation.py): lotto 2 × 1 lotto per
    giro, 2 PdV vecchi senza copia nel PdA, 1 PdV pulibile più recente. Prima: 5
    giri «puliti=[] rinviati=2», il PdV pulibile mai raggiunto."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        vecchi = [await _versa(c, f"OLD-{i}", PDF + str(i).encode()) for i in range(2)]
        buono = await _versa(c, "NEW-1", PDF + b"buono")
    for i, pdv in enumerate(vecchi):
        await _pda(db_url, s3, pdv, quando=adesso - timedelta(days=10), copia=False)
        await sql(db_url, "UPDATE public.pdv SET data_aggiornamento = $2 WHERE id = $1",
                  uuid.UUID(pdv), adesso - timedelta(days=10 - i))
    await _pda(db_url, s3, buono, quando=adesso - timedelta(days=2))
    await sql(db_url, "UPDATE public.pdv SET data_aggiornamento = $2 WHERE id = $1",
              uuid.UUID(buono), adesso - timedelta(days=2))
    # i vecchi davanti nell'ordine dei candidati (come nella prova: prima di tutto)
    for pdv in vecchi:
        await sql(db_url, "UPDATE public.pdv SET staging_verifica_prossima = $2 WHERE id = $1",
                  uuid.UUID(pdv), datetime(1960, 1, 1, tzinfo=UTC))
    puliti: list[str] = []
    for _ in range(5):
        esito = await _giro(s3, adesso, lotto=2, lotti=1)
        # costo limitato: mai più di lotto × lotti PdV letti in un giro
        assert len(esito.puliti) + len(esito.rinviati) + esito.non_pronti <= 2
        puliti += esito.puliti
    assert buono in puliti, "il PdV pulibile non è mai stato raggiunto"
    # i vecchi sono rinviati con attesa, non riletti a ogni giro
    for pdv in vecchi:
        stato = await _stato_pulizia(db_url, pdv)
        assert stato["staging_verifica_tentativi"] == 1
        assert stato["staging_verifica_prossima"] > adesso
        assert "non leggibile" in stato["staging_verifica_motivo"]
        assert stato["staging_eliminato_il"] is None


async def test_rinvio_con_attesa_crescente_poi_blocco_log_metrica_e_sblocco(
        db_url, s3, utente_di, monkeypatch):
    monkeypatch.setattr(settings, "staging_pulizia_rinvio_minuti", 10)
    monkeypatch.setattr(settings, "staging_pulizia_rinvio_max_ore", 24)
    monkeypatch.setattr(settings, "staging_pulizia_tentativi_max", 3)
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        pdv = await _versa(c, "RB-1")
    await _pda(db_url, s3, pdv, quando=adesso - timedelta(days=2), copia=False)

    primo = await _giro(s3, adesso)
    assert pdv in primo.rinviati and not primo.bloccati
    stato = await _stato_pulizia(db_url, pdv)
    assert stato["staging_verifica_tentativi"] == 1
    assert stato["staging_verifica_prossima"] == adesso + timedelta(minutes=10)

    # prima della scadenza il PdV non si rilegge (nessuna HEAD inutile)
    presto = await _giro(s3, adesso + timedelta(minutes=9))
    assert pdv not in presto.rinviati

    secondo = await _giro(s3, adesso + timedelta(minutes=10))
    assert pdv in secondo.rinviati
    stato = await _stato_pulizia(db_url, pdv)
    assert stato["staging_verifica_tentativi"] == 2
    assert stato["staging_verifica_prossima"] == adesso + timedelta(minutes=10 + 20)

    with capture_logs() as log:
        terzo = await _giro(s3, adesso + timedelta(minutes=30))
    assert terzo.bloccati == {pdv: terzo.rinviati[pdv]}
    stato = await _stato_pulizia(db_url, pdv)
    assert stato["staging_verifica_tentativi"] == 3
    assert stato["staging_verifica_bloccata_il"] == adesso + timedelta(minutes=30)
    errori = [e for e in log if e["event"] == "staging_pulizia_bloccata"]
    assert errori and errori[0]["pdv_id"] == pdv and errori[0]["log_level"] == "error"

    # bloccato: escluso dai giri anche molto dopo
    dopo = await _giro(s3, adesso + timedelta(days=30))
    assert pdv not in dopo.rinviati
    # metrica ed elenco per la console
    assert await pulizia_staging.conta_bloccati() == 1
    metrica = pulizia_staging.MetrichePuliziaStaging()
    await metrica.aggiorna()
    [famiglia] = list(metrica.collect())
    assert famiglia.name == "verumdoc_staging_pulizia_pdv_bloccati"
    assert famiglia.samples[0].value == 1.0
    [riga] = await pulizia_staging.elenca_bloccati()
    assert riga["pdv_id"] == pdv and riga["tentativi"] == 3 and "non leggibile" in riga["motivo"]

    # corretta la causa (copia presente), l'operatore lo rimette in coda
    await sql(db_url, "DELETE FROM public.documenti")
    await sql(db_url, "DELETE FROM public.aip")
    await _pda(db_url, s3, pdv, quando=adesso - timedelta(days=2))
    assert await pulizia_staging.sblocca([pdv], adesso + timedelta(days=30)) == 1
    pulito = await _giro(s3, adesso + timedelta(days=30))
    assert pulito.puliti == [pdv] and chiavi_staging(s3) == []
    assert await pulizia_staging.conta_bloccati() == 0


async def test_motivo_permanente_blocca_subito(db_url, s3, utente_di):
    """PdV come quelli di R02: nessuna impronta o dimensione dei file di transito
    nel PdV. Un nuovo tentativo non può cambiare l'esito: bloccato al primo giro,
    marcato una volta sola."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        pdv = await _versa(c, "R02-1")
    await _pda(db_url, s3, pdv, quando=adesso - timedelta(days=2))
    await sql(db_url, "UPDATE public.pdv SET metadata_json = jsonb_set(metadata_json, "
                      "'{docs_s3_keys,0}', (metadata_json #> '{docs_s3_keys,0}') "
                      "- 'hash_sha256' - 'size_bytes') WHERE id = $1", uuid.UUID(pdv))
    esito = await _giro(s3, adesso)
    assert "non registrate" in esito.bloccati[pdv]
    stato = await _stato_pulizia(db_url, pdv)
    assert stato["staging_verifica_tentativi"] == 1
    assert stato["staging_verifica_bloccata_il"] == adesso
    assert len(chiavi_staging(s3)) == 1
    assert pdv not in (await _giro(s3, adesso + timedelta(days=1))).rinviati


async def test_pda_recente_rinviato_alla_fine_dell_attesa_senza_tentativo(db_url, s3,
                                                                           utente_di):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        pdv = await _versa(c, "REC-1")
    pda_il = adesso - timedelta(hours=1)
    await _pda(db_url, s3, pdv, quando=pda_il)
    esito = await _giro(s3, adesso, attesa=timedelta(hours=24))
    assert esito.non_pronti == 1 and not esito.rinviati and not esito.puliti
    stato = await _stato_pulizia(db_url, pdv)
    assert stato["staging_verifica_tentativi"] == 0
    assert stato["staging_verifica_prossima"] == pda_il + timedelta(hours=24)
    fine = await _giro(s3, pda_il + timedelta(hours=24), attesa=timedelta(hours=24))
    assert fine.puliti == [pdv]


async def test_in_prova_nessun_rinvio_registrato(db_url, s3, utente_di):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    adesso = datetime.now(UTC)
    async with client_http() as c:
        pdv = await _versa(c, "PRV-1")
    await _pda(db_url, s3, pdv, quando=adesso - timedelta(days=2), copia=False)
    prova = await _giro(s3, adesso, prova=True)
    assert pdv in prova.rinviati
    stato = await _stato_pulizia(db_url, pdv)
    assert stato["staging_verifica_tentativi"] == 0


async def test_lettura_dei_candidati_sull_indice_fino_ad_adesso(db_url):
    """Costo di un giro con milioni di righe: la lettura di un lotto è una scansione
    d'intervallo sull'indice parziale fino ad «adesso» (le righe rinviate nel
    futuro e le bloccate non si leggono)."""
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_dsn(db_url))
    try:
        await conn.execute("SET enable_seqscan = off")
        sql_lotto = str(pulizia_staging._CANDIDATI).replace(":adesso", "$1") \
            .replace(":dopo_t", "$2").replace(":dopo_id", "$3").replace(":n", "$4")
        piano = "\n".join(r[0] for r in await conn.fetch(
            "EXPLAIN " + sql_lotto, datetime.now(UTC), datetime(1900, 1, 1, tzinfo=UTC),
            uuid.UUID(int=0), 500))
    finally:
        await conn.close()
    assert "idx_pdv_staging_da_verificare" in piano, piano
    assert "Index Cond" in piano and "staging_verifica_prossima <=" in piano, piano
    assert "Limit" in piano


def test_attesa_crescente_con_tetto(monkeypatch):
    monkeypatch.setattr(settings, "staging_pulizia_rinvio_minuti", 10)
    monkeypatch.setattr(settings, "staging_pulizia_rinvio_max_ore", 2)
    attese = [pulizia_staging.attesa_rinvio(t) for t in range(1, 7)]
    assert attese[:4] == [timedelta(minutes=m) for m in (10, 20, 40, 80)]
    assert attese[4:] == [timedelta(hours=2)] * 2
    assert pulizia_staging.attesa_rinvio(10_000) == timedelta(hours=2)


# ── REV-N2: disco temporaneo limitato e prevedibile ───────────────────

def _corpo_multipart(n_mb: int, letti: list[int]):
    async def gen():
        yield (b"--x\r\nContent-Disposition: form-data; name=\"indice\"\r\n\r\n{}\r\n"
               b"--x\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.pdf\"\r\n"
               b"Content-Type: application/pdf\r\n\r\n")
        for _ in range(n_mb):
            letti[0] += 1
            yield b"A" * MB
        yield b"\r\n--x--\r\n"
    return gen()


async def _post_chunked(percorso: str, n_mb: int):
    letti = [0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(percorso, content=_corpo_multipart(n_mb, letti),
                         headers={"Content-Type": "multipart/form-data; boundary=x",
                                  "Idempotency-Key": "prova-disco-0001"})
    return r, letti[0]


async def test_multipart_oltre_il_tetto_di_concorrenza_503_senza_leggere(
        db_url, s3, utente_di, monkeypatch):
    """Prima: nessun tetto, un cliente poteva riempire /tmp con multipart da 110 MB
    contemporanei. Ora oltre il tetto 503 UPL-503-OCCUPATO, ritentabile, prima di
    leggere il corpo."""
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    monkeypatch.setattr(settings, "versamento_multipart_richieste_contemporanee_max", 1)
    corpo.LIMITE_MULTIPART.in_corso += 1    # un multipart già in corso sulla replica
    try:
        rest, letti_rest = await _post_chunked("/api/v1/versamento/ud", 20)
    finally:
        corpo.LIMITE_MULTIPART.in_corso -= 1
    assert rest.status_code == 503, rest.text
    assert rest.json()["codice"] == "UPL-503-OCCUPATO" and rest.json()["ritentabile"] is True
    assert int(rest.headers["Retry-After"]) >= 1
    assert letti_rest < 2
    assert corpo.LIMITE_MULTIPART.in_corso == 0 and corpo.BUDGET_DISCO_TEMPORANEO.impegnati == 0


async def test_budget_del_disco_comune_a_multipart_e_pdnd(db_url, s3, utente_di, chiave_pdnd,
                                                          monkeypatch):
    tenant, _ = await crea_tenant_e_classe(db_url)
    utente_di(tenant)
    await _registra_fruitore(db_url, "client-disco", tenant)
    monkeypatch.setattr(settings, "disco_temporaneo_budget_mb", 4)
    # chunked senza Content-Length: si prenota il limite del corpo (110 MB > 4 MB)
    chunked, letti = await _post_chunked("/api/v1/versamento/ud", 20)
    assert chunked.status_code == 503 and chunked.json()["codice"] == "UPL-503-OCCUPATO"
    assert chunked.json()["dettagli"]["budget_byte"] == 4 * MB and letti < 2

    # il budget è condiviso: occupato da un multipart in corso, PDND aspetta (503)
    occupato = corpo.BUDGET_DISCO_TEMPORANEO.prenota(4 * MB, "UPL-503-OCCUPATO")
    try:
        async with _app_pdnd() as c:
            pdnd = await _versa_pdnd(c, "client-disco", _corpo_pdnd(), "pdnd-disco-0001")
        async with client_http() as c:
            rest = await c.post("/api/v1/versamento/ud",
                                data={"indice": json.dumps({})},
                                files=[("files", ("a.pdf", PDF, "application/pdf"))],
                                headers={"Idempotency-Key": "disco-rest-0001"})
    finally:
        occupato.rilascia()
    assert pdnd.status_code == 503 and pdnd.json()["codice"] == "PDND-503-OCCUPATO"
    assert rest.status_code == 503 and rest.json()["codice"] == "UPL-503-OCCUPATO"

    # liberato il budget, le stesse richieste passano e lo restituiscono
    async with _app_pdnd() as c:
        pdnd = await _versa_pdnd(c, "client-disco", _corpo_pdnd(), "pdnd-disco-0001")
    async with client_http() as c:
        await _versa(c, "DISCO-1")          # 202 (controllato in _versa)
    assert pdnd.status_code == 202, pdnd.text
    assert corpo.BUDGET_DISCO_TEMPORANEO.impegnati == 0 and corpo.LIMITE_MULTIPART.in_corso == 0
    assert pdnd_router.LIMITE_VERSAMENTI_PDND.in_corso == 0


def test_budget_prenota_e_rilascia_una_volta_sola():
    budget = corpo.BudgetDisco(lambda: 10)
    a = budget.prenota(6, "UPL-503-OCCUPATO")
    with pytest.raises(ErroreApi) as e:
        budget.prenota(5, "PDND-503-OCCUPATO")
    assert e.value.codice == "PDND-503-OCCUPATO" and e.value.status == 503
    with budget.prenota(4, "UPL-503-OCCUPATO"):
        assert budget.impegnati == 10
    a.rilascia()
    a.rilascia()
    assert budget.impegnati == 0


async def test_metriche_esposte_dal_gateway(db_url, utente_di):
    """/metrics del gateway: PdV bloccati dalla pulizia (REV-N1) e disco temporaneo (REV-N2)."""
    import app.main as principale

    async with client_http() as c:
        prima = (await c.get("/metrics")).text
        if principale.metriche_pulizia._in_corso is not None:
            await principale.metriche_pulizia._in_corso   # lettura avviata dalla raccolta
        dopo = (await c.get("/metrics")).text
    from prometheus_client.parser import text_string_to_metric_families

    valori = {s.name: s.value for f in text_string_to_metric_families(dopo) for s in f.samples}
    assert "verumdoc_staging_pulizia_pdv_bloccati" in prima
    assert valori["verumdoc_staging_pulizia_pdv_bloccati"] == 0
    assert valori["verumdoc_gateway_disco_temporaneo_byte_budget"] == \
        settings.disco_temporaneo_budget_mb * MB
    assert valori["verumdoc_gateway_disco_temporaneo_byte_prenotati"] == 0
    assert valori["verumdoc_gateway_multipart_in_corso"] == 0
