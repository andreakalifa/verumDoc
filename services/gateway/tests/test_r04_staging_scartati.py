# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 (VD-C-104, VD-P-36) — scadenza dello staging dei PdV rifiutati o in ERRORE.

PostgreSQL migrato e S3 (moto). I PdV in ERRORE si scrivono come li lascia il
versamento (``metadata_json.docs_s3_keys`` con le copie di transito nello
staging, sessione di caricamento collegata) e come li chiudono validation e
aip-builder (stato ERRORE e ``data_aggiornamento``): il test non dipende dal
percorso di versamento, provato altrove.
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.services.pulizia_staging import (
    TOPIC_STAGING_SCADUTO,
    ImpostazioniStagingScartati,
    pulisci_staging,
    scadenza_staging_scartati,
)

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.supporto_canali import PDF, chiavi_staging, s3  # noqa: F401 — fixture

pytestmark = [richiede_pg, pytest.mark.integration]

IMP = ImpostazioniStagingScartati(preavviso_giorni=7)


async def _pdv(url: str, s3, ente: tuple[uuid.UUID, uuid.UUID], *, sessione: bool = False,
               contenuto: bytes = PDF) -> str:
    """PdV con una copia di transito nello staging (versamento multipart o sessione)."""
    tenant, classe = ente
    pdv_id = uuid.uuid4()
    if sessione:
        sid = uuid.uuid4()
        chiave = f"caricamenti/{tenant}/{sid}/dati"
    else:
        chiave = f"versamenti/{tenant}/{pdv_id}/0000-fattura.pdf"
    s3.put_object(Bucket=settings.s3_bucket_pdv, Key=chiave, Body=contenuto)
    docs = [{"s3_key": chiave, "s3_bucket": settings.s3_bucket_pdv, "nome_file": "fattura.pdf",
             "hash_sha256": hashlib.sha256(contenuto).hexdigest(), "size_bytes": len(contenuto)}]
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, "
                   "tenant_id, metadata_json) VALUES ($1, 'Prova', 'RICEVUTO', $2, $3, $4, "
                   "CAST($5 AS jsonb))", pdv_id, classe, uuid.uuid4(), tenant,
              json.dumps({"docs_s3_keys": docs}))
    if sessione:
        await sql(url, "INSERT INTO public.caricamenti (id, tenant_id, utente_id, canale, "
                       "impronta_richiesta, nome_file, dimensione, dimensione_parte, "
                       "numero_parti, stato, bucket, chiave_oggetto, scade_il, pdv_id) "
                       "VALUES ($1, $2, $3, 'REST', $4, 'fattura.pdf', $5, $5, 1, 'COMPLETO', "
                       "$6, $7, now(), $8)", sid, tenant, uuid.uuid4(), "0" * 64,
                  len(contenuto), settings.s3_bucket_pdv, chiave, pdv_id)
    return str(pdv_id)


async def _in_errore(url: str, pdv_id: str, *, da_giorni: float, adesso: datetime,
                     avvisato_da_giorni: float | None = None) -> None:
    await sql(url, "UPDATE public.pdv SET stato = 'ERRORE', data_aggiornamento = $2, "
                   "staging_avvisato_il = $3 WHERE id = $1", uuid.UUID(pdv_id),
              adesso - timedelta(days=da_giorni),
              None if avvisato_da_giorni is None else adesso - timedelta(days=avvisato_da_giorni))


async def _stato(url: str, pdv_id: str):
    [riga] = await sql(url, "SELECT staging_avvisato_il, staging_eliminato_il, "
                            "staging_verifica_bloccata_il FROM public.pdv WHERE id = $1",
                       uuid.UUID(pdv_id))
    return riga


async def _avvisi(url: str) -> list[dict]:
    righe = await sql(url, "SELECT chiave, tenant_id, payload FROM public.outbox_eventi "
                           "WHERE topic = $1 ORDER BY sequenza", TOPIC_STAGING_SCADUTO)
    return [{"chiave": r["chiave"], "tenant_id": r["tenant_id"],
             "payload": json.loads(r["payload"])} for r in righe]


async def _scadenza(s3, adesso, **kw):
    return await scadenza_staging_scartati(s3=s3, bucket_staging=settings.s3_bucket_pdv,
                                           adesso=adesso, imp=IMP, **kw)


async def test_scadenza_staging_avviso_sette_giorni_prima_una_volta(db_url, s3):
    """A 83 giorni dall'errore: evento versamento.staging_scaduto con la data di
    scadenza, PdV marcato avvisato, file intatti; il giro dopo non riavvisa."""
    ente = await crea_tenant_e_classe(db_url)
    tenant = ente[0]
    adesso = datetime.now(UTC)
    da_avvisare = await _pdv(db_url, s3, ente)
    recente = await _pdv(db_url, s3, ente)
    await _in_errore(db_url, da_avvisare, da_giorni=83.5, adesso=adesso)
    await _in_errore(db_url, recente, da_giorni=10, adesso=adesso)

    esito = await _scadenza(s3, adesso)
    assert esito.avvisati == [da_avvisare] and esito.scaduti == []
    [avviso] = await _avvisi(db_url)
    assert avviso["chiave"] == da_avvisare and avviso["tenant_id"] == tenant
    assert avviso["payload"]["tipo"] == TOPIC_STAGING_SCADUTO
    scade = datetime.fromisoformat(avviso["payload"]["scade_il"])
    assert abs((scade - (adesso + timedelta(days=6.5))).total_seconds()) < 5
    assert avviso["payload"]["giorni_conservazione"] == 90
    # R07 (D15): collegamento «Rielabora» della console
    assert avviso["payload"]["rielabora"].endswith(
        f"/versamenti?stato=ERRORE&pdv_id={da_avvisare}")
    assert (await _stato(db_url, da_avvisare))["staging_avvisato_il"] is not None
    assert (await _stato(db_url, recente))["staging_avvisato_il"] is None
    assert len(chiavi_staging(s3)) == 2

    di_nuovo = await _scadenza(s3, adesso + timedelta(hours=1))
    assert di_nuovo.avvisati == [] and len(await _avvisi(db_url)) == 1


async def test_scadenza_staging_dopo_novanta_giorni_eliminato_e_non_rielaborabile(db_url, s3):
    """Scaduto e avvisato da più di 7 giorni: copie eliminate (anche quelle della
    sessione di caricamento), PdV marcato; un PdV non avvisato riceve prima l'avviso."""
    ente = await crea_tenant_e_classe(db_url)
    adesso = datetime.now(UTC)
    multipart = await _pdv(db_url, s3, ente)
    per_riferimento = await _pdv(db_url, s3, ente, sessione=True)
    mai_avvisato = await _pdv(db_url, s3, ente)
    await _in_errore(db_url, multipart, da_giorni=95, adesso=adesso, avvisato_da_giorni=12)
    await _in_errore(db_url, per_riferimento, da_giorni=91, adesso=adesso, avvisato_da_giorni=8)
    await _in_errore(db_url, mai_avvisato, da_giorni=120, adesso=adesso)
    assert len(chiavi_staging(s3)) == 3

    prova = await _scadenza(s3, adesso, prova=True)
    assert sorted(prova.scaduti) == sorted([multipart, per_riferimento])
    assert len(chiavi_staging(s3)) == 3 and await _avvisi(db_url) == []

    esito = await _scadenza(s3, adesso)
    assert sorted(esito.scaduti) == sorted([multipart, per_riferimento])
    assert esito.avvisati == [mai_avvisato]
    rimasti = chiavi_staging(s3)
    assert len(rimasti) == 1 and mai_avvisato in rimasti[0]
    for pdv_id in (multipart, per_riferimento):
        assert (await _stato(db_url, pdv_id))["staging_eliminato_il"] is not None
    [sessione] = await sql(db_url, "SELECT storage_eliminato_il FROM public.caricamenti "
                                   "WHERE pdv_id = $1", uuid.UUID(per_riferimento))
    assert sessione["storage_eliminato_il"] is not None
    # il PdV mai avvisato si elimina solo dopo un preavviso pieno
    assert (await _scadenza(s3, adesso + timedelta(days=6))).scaduti == []
    assert (await _scadenza(s3, adesso + timedelta(days=7, minutes=1))).scaduti == [mai_avvisato]
    assert chiavi_staging(s3) == []


async def test_scadenza_staging_chiave_estranea_bloccata_mai_eliminata(db_url, s3):
    ente = await crea_tenant_e_classe(db_url)
    adesso = datetime.now(UTC)
    pdv_id = await _pdv(db_url, s3, ente)
    await sql(db_url, "UPDATE public.pdv SET metadata_json = jsonb_set(metadata_json, "
                      "'{docs_s3_keys,0,s3_key}', to_jsonb('versamenti/' || gen_random_uuid() "
                      "|| '/x/0000-fattura.pdf')) WHERE id = $1", uuid.UUID(pdv_id))
    await _in_errore(db_url, pdv_id, da_giorni=100, adesso=adesso, avvisato_da_giorni=10)
    esito = await _scadenza(s3, adesso)
    assert esito.scaduti == [] and "fuori dallo staging" in esito.bloccati[pdv_id]
    riga = await _stato(db_url, pdv_id)
    assert riga["staging_eliminato_il"] is None and riga["staging_verifica_bloccata_il"]
    assert len(chiavi_staging(s3)) == 1
    # bloccato: i giri successivi non lo riprendono
    assert (await _scadenza(s3, adesso)).bloccati == {}


async def test_pulisci_staging_esegue_anche_la_scadenza(db_url, s3, monkeypatch):
    """Il giro della pulizia (lavoratore del gateway e comando) include la scadenza."""
    from app.services import pulizia_staging

    monkeypatch.setattr(pulizia_staging, "IMPOSTAZIONI_SCARTATI", IMP)
    ente = await crea_tenant_e_classe(db_url)
    adesso = datetime.now(UTC)
    pdv_id = await _pdv(db_url, s3, ente)
    await _in_errore(db_url, pdv_id, da_giorni=92, adesso=adesso, avvisato_da_giorni=9)
    esito = await pulisci_staging(s3=s3, bucket_staging=settings.s3_bucket_pdv, adesso=adesso)
    assert esito.scadenza is not None and esito.scadenza.scaduti == [pdv_id]
    assert chiavi_staging(s3) == []


async def test_scadenza_staging_configurabile(db_url, s3):
    """R07 (D15): la durata è un'impostazione della console PER ENTE (30-180 giorni):
    con 30 giorni per l'ente scade prima; un altro ente resta a 90.
    Revisione W1: l'ente può solo ridurre rispetto al tetto (limite del conservatore
    per l'ente, ``LIMITE.STAGING_SCARTATI_GIORNI``, o valore del conservatore): un
    valore dell'ente oltre il tetto (999, anche oltre l'intervallo) vale 90; con un
    limite di 120 l'ente arriva a 120; un limite di 40 vale anche senza valore."""
    ente = await crea_tenant_e_classe(db_url)
    altro = await crea_tenant_e_classe(db_url)
    terzo = await crea_tenant_e_classe(db_url)
    quarto = await crea_tenant_e_classe(db_url)
    quinto = await crea_tenant_e_classe(db_url)
    adesso = datetime.now(UTC)
    pdv = {k: await _pdv(db_url, s3, e) for k, e in (("ente", ente), ("altro", altro),
                                                       ("terzo", terzo), ("quarto", quarto),
                                                       ("quinto", quinto))}
    for k, giorni in (("ente", 31), ("altro", 31), ("terzo", 91), ("quarto", 100),
                      ("quinto", 41)):
        await _in_errore(db_url, pdv[k], da_giorni=giorni, adesso=adesso, avvisato_da_giorni=8)
    for tenant, codice, giorni in ((ente[0], "STAGING_SCARTATI_GIORNI", 30),
                                   (terzo[0], "STAGING_SCARTATI_GIORNI", 999),
                                   (quarto[0], "LIMITE.STAGING_SCARTATI_GIORNI", 120),
                                   (quarto[0], "STAGING_SCARTATI_GIORNI", 120),
                                   (quinto[0], "LIMITE.STAGING_SCARTATI_GIORNI", 40)):
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                          "valore, motivo) VALUES ('ENTE', $1, $2, "
                          "CAST($3 AS jsonb), 'prova R07')", tenant, codice, str(giorni))
    esito = await _scadenza(s3, adesso)
    assert sorted(esito.scaduti) == sorted([pdv["ente"], pdv["terzo"], pdv["quinto"]])
