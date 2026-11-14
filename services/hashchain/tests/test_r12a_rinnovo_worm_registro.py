# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-148) — rinnovo del blocco WORM degli oggetti ``registro/`` della
hashchain (segmenti, checkpoint, audit), su PostgreSQL vero e S3 (moto con
Object Lock).

  - ogni scrittura del registro entra nell'elenco; il censimento aggiunge gli
    oggetti scritti prima di R12a;
  - in scadenza (entro RETENTION_RINNOVO_SOGLIA_ANNI) → esteso fino ad adesso +
    RETENTION_PERMANENTE_ANNI, MAI in riduzione (elenco obsoleto: lo storage ha un
    termine più lontano e resta quello);
  - Z4: configurazione impossibile → passo non riuscito, metrica d'allarme,
    ritentato al giro dopo;
  - Z5: un oggetto che non si rinnova non ferma gli altri (metrica degli errori).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.registro import archivio_worm, rinnovo_worm
from app.registro.metriche import metriche
from app.services import block_sealer

from libs.shared.db import close_db, init_db
from libs.shared.storage import worm
from tests.infra_reale import client_s3, db_url, richiede_pg, s3_server, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

ADESSO = datetime.now(UTC).replace(microsecond=0)
#: scritti 27 anni fa con l'orizzonte di 30: blocco fra 3 anni (< soglia di 5)
VECCHIO = ADESSO - timedelta(days=365 * 27)


@pytest.fixture
def s3(s3_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", 30)
    block_sealer._client.cache_clear()
    yield client_s3(s3_server)
    block_sealer._client.cache_clear()


@pytest.fixture
async def db(db_url):  # noqa: F811
    await init_db(db_url)
    yield db_url
    await close_db()


def _termine(s3, chiave: str) -> datetime:  # type: ignore[no-untyped-def]
    return s3.head_object(Bucket=settings.s3_bucket_aip, Key=chiave)["ObjectLockRetainUntilDate"]


async def _parametro(url: str, codice: str, valore: int) -> None:
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ('CONSERVATORE', NULL, $1, CAST($2 AS jsonb), 'prova R12a')",
              codice, json.dumps(valore))


async def test_scrittura_censimento_e_rinnovo_mai_in_riduzione(db, s3) -> None:
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    segmento = archivio_worm.chiave_segmento(0, 4)
    checkpoint = archivio_worm.chiave_checkpoint(4)
    await archivio_worm.scrivi(segmento, b'{"foglia":1}\n', "application/x-ndjson", VECCHIO)
    await archivio_worm.scrivi(checkpoint, b"nota firmata\n", "text/plain", VECCHIO)
    righe = {r["chiave"]: r for r in await sql(
        db, "SELECT chiave, conserva_fino_a FROM public.registro_worm_oggetti")}
    assert set(righe) == {segmento, checkpoint}
    assert righe[segmento]["conserva_fino_a"] == _termine(s3, segmento)
    # oggetto dell'audit scritto «prima di R12a» (senza elenco): lo trova il censimento
    audit = "registro/audit/2000-01-01.jsonl"
    worm.scrivi_oggetto_conservato(
        s3, settings.s3_bucket_aip, audit, b"riga\n", "application/x-ndjson",
        worm.RetentionOggetto(VECCHIO + timedelta(days=365 * 30), legal_hold=True),
        adesso=ADESSO, produzione=False, regione="us-east-1")
    # elenco OBSOLETO per il checkpoint: lo storage è già più lontano (40 anni)
    lontano = ADESSO + timedelta(days=365 * 40)
    s3.put_object_retention(Bucket=settings.s3_bucket_aip, Key=checkpoint,
                            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": lontano})
    esito = await rinnovo_worm.passo(ADESSO)
    assert esito["censimento"]["censimento"] == "completato"
    assert esito["censimento"]["aggiunti"] == 1
    atteso = worm.aggiungi_anni(ADESSO, 30)
    assert _termine(s3, segmento) == atteso
    assert _termine(s3, audit) == atteso
    assert _termine(s3, checkpoint) == lontano            # mai in riduzione (storage)
    righe = {r["chiave"]: r["conserva_fino_a"] for r in await sql(
        db, "SELECT chiave, conserva_fino_a FROM public.registro_worm_oggetti")}
    assert righe == {segmento: atteso, audit: atteso, checkpoint: lontano}
    assert esito["elementi_in_errore"] == 0 and esito["in_scadenza_dopo"] == 0
    assert metriche().worm_rinnovo_in_errore._value.get() == 0
    # secondo passo: nulla da rinnovare, il censimento non è dovuto
    esito = await rinnovo_worm.passo(ADESSO)
    assert esito["rinnovati"] == 0 and esito["censimento"] == {"censimento": "non dovuto"}


async def test_prima_guardia_filtro_della_selezione(db, s3) -> None:
    """Un termine registrato oltre la soglia non si tocca anche se lo storage dice
    meno (nessun «rinnovo» di oggetti non in scadenza per l'elenco)."""
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    chiave = archivio_worm.chiave_checkpoint(9)
    await archivio_worm.scrivi(chiave, b"x\n", "text/plain", VECCHIO)
    await sql(db, "UPDATE public.registro_worm_oggetti SET conserva_fino_a = $1",
              ADESSO + timedelta(days=365 * 20))
    prima = _termine(s3, chiave)
    await sql(db, "UPDATE public.registro_worm_censimento SET completato_il = $1", ADESSO)
    await sql(db, "INSERT INTO public.registro_worm_censimento (id, completato_il) "
                  "VALUES (1, $1) ON CONFLICT (id) DO NOTHING", ADESSO)
    esito = await rinnovo_worm.passo(ADESSO)
    assert esito["controllati"] == 0 and _termine(s3, chiave) == prima


async def test_z4_configurazione_impossibile_passo_non_riuscito(db, s3) -> None:
    from app.registro.ciclo import CicloCatena, ConfigCiclo

    from libs.shared.parametri import svuota_cache

    await _parametro(db, "RETENTION_PERMANENTE_ANNI", 10)
    await _parametro(db, "RETENTION_RINNOVO_SOGLIA_ANNI", 10)
    svuota_cache()
    with pytest.raises(rinnovo_worm.RinnovoNonPossibile):
        await rinnovo_worm.passo(ADESSO)
    assert metriche().worm_rinnovo_in_errore._value.get() == 1
    # nel ciclo: il passo fallito NON sposta la prossima esecuzione (si riprova)
    ciclo = CicloCatena(config=ConfigCiclo(
        intervallo=1, checkpoint_secondi=1, controfirma_max_secondi=1, lease_secondi=10,
        lotto=1, completamento_lotto=1, segmento_worm=1, ripresa_secondi=1,
        rinnovo_worm_secondi=86400, rinnovo_worm_attivo=True),
        identita=None, servizio_firma=None, testimone=None)  # type: ignore[arg-type]
    await ciclo._rinnovo_worm(100.0)
    assert ciclo._prossimo_rinnovo_worm == 0.0
    svuota_cache()


async def test_z5_un_oggetto_in_errore_non_ferma_gli_altri(db, s3) -> None:
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    buono = archivio_worm.chiave_segmento(4, 8)
    await archivio_worm.scrivi(buono, b"y\n", "application/x-ndjson", VECCHIO)
    # voce dell'elenco senza oggetto nello storage (errore isolato), prima in ordine
    await sql(db, "INSERT INTO public.registro_worm_oggetti (chiave, version_id, "
                  "conserva_fino_a) VALUES ('registro/foglie/000000000000-000000000001.jsonl', "
                  "'', $1)", VECCHIO)
    await sql(db, "INSERT INTO public.registro_worm_censimento (id, completato_il) "
                  "VALUES (1, $1) ON CONFLICT (id) DO NOTHING", ADESSO)
    esito = await rinnovo_worm.passo(ADESSO)
    assert esito["elementi_in_errore"] == 1 and esito["rinnovati"] == 1
    assert _termine(s3, buono) == worm.aggiungi_anni(ADESSO, 30)
    assert metriche().worm_rinnovo_elementi_in_errore._value.get() == 1
    assert esito["in_scadenza_dopo"] == 1


async def test_revisione_diritti_solo_al_gruppo_del_registro(db_url) -> None:  # noqa: F811
    """Revisione R12a: l'elenco degli oggetti del registro e il cursore del censimento
    non sono del gruppo dei servizi: solo ``verumdoc_registro_worm`` (login hashchain)."""
    righe = await sql(db_url, """
        SELECT r.rolname, t.tabella,
               has_table_privilege(r.rolname, 'public.' || t.tabella, 'SELECT') AS legge,
               has_table_privilege(r.rolname, 'public.' || t.tabella, 'INSERT,UPDATE') AS scrive,
               has_table_privilege(r.rolname, 'public.' || t.tabella, 'DELETE') AS cancella
        FROM pg_roles r CROSS JOIN (VALUES ('registro_worm_oggetti'),
                                           ('registro_worm_censimento')) AS t(tabella)
        WHERE r.rolname IN ('verumdoc_applicazione', 'verumdoc_registro_worm')""")
    per = {(r["rolname"], r["tabella"]): (r["legge"], r["scrive"], r["cancella"]) for r in righe}
    for tabella in ("registro_worm_oggetti", "registro_worm_censimento"):
        assert per[("verumdoc_applicazione", tabella)] == (False, False, False)
        assert per[("verumdoc_registro_worm", tabella)] == (True, True, False)
