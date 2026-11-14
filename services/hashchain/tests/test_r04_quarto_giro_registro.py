# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Quarto giro delle correzioni R04 (verifica finale, F1): il controllo di coerenza
del registro con il WORM su S3 VERO, cioè con i permessi del ruolo IAM.

  - prova del revisore (prova_coerenza_senza_listbucket.py): S3 senza
    ``s3:ListBucket`` risponde AccessDenied all'elenco e 403 (non 404) a una GET su
    una chiave assente. Prima: il controllo sollevava un'eccezione, il registro
    sano non scriveva MAI e nessun blocco si apriva (nessun allarme). Ora:
    blocco esplicito ``COERENZA_NON_VERIFICABILE`` al primo giro (allarme
    ``RegistroBloccato``), che si chiude da solo quando il permesso c'è;
  - un errore non di permesso (rete) apre il blocco solo se persiste;
  - con i permessi di produzione (elenco SOLO sotto ``registro/``, 403 sulle
    chiavi assenti) il controllo funziona: mai una GET su una chiave assente.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.config import settings
from app.registro import coerenza
from app.registro.ciclo import CicloCatena, ConfigCiclo
from app.registro.metriche import metriche
from app.services import block_sealer
from botocore.exceptions import ClientError
from structlog.testing import capture_logs

from tests.infra_reale import (  # noqa: F401 — fixture
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)
from tests.supporto_registro import FirmaFinta, crea_pda, identita

ORA = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
CONFIG = {"intervallo": 1, "checkpoint_secondi": 60, "controfirma_max_secondi": 600,
          "lease_secondi": 30, "lotto": 10, "completamento_lotto": 10, "segmento_worm": 10,
          "ripresa_secondi": 60, "sonda_testimone_secondi": 60}

pytestmark = [pytest.mark.integration, richiede_pg]


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


def _negato(operazione: str) -> ClientError:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "s3:ListBucket"}},
                       operazione)


class ClientSenzaListBucket:
    """Come S3 per un ruolo senza s3:ListBucket (la prova del revisore)."""

    def get_paginator(self, nome):
        class Paginatore:
            def paginate(self, **_):
                raise _negato("ListObjectsV2")
        return Paginatore()

    def list_objects_v2(self, **_):
        raise _negato("ListObjectsV2")

    def get_object(self, **_):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")


class ClientDiProduzione:
    """Client vero (moto) con i permessi del ruolo della hashchain: elenco solo
    sotto registro/ (condizione s3:prefix) e 403 su una GET di chiave assente."""

    def __init__(self, vero) -> None:
        self.vero = vero
        self.elenchi: list[str] = []

    def _controlla(self, argomenti: dict) -> None:
        prefisso = argomenti.get("Prefix", "")
        self.elenchi.append(prefisso)
        if not prefisso.startswith("registro/"):
            raise _negato("ListObjectsV2")

    def get_paginator(self, nome):
        vero, controlla = self.vero.get_paginator(nome), self._controlla

        class Paginatore:
            def paginate(self, **argomenti):
                controlla(argomenti)
                return vero.paginate(**argomenti)
        return Paginatore()

    def list_objects_v2(self, **argomenti):
        self._controlla(argomenti)
        return self.vero.list_objects_v2(**argomenti)

    def get_object(self, **argomenti):
        try:
            return self.vero.get_object(**argomenti)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject") from None
            raise


def _ciclo(ident) -> CicloCatena:
    tempo = {"t": 0.0}

    def orologio() -> float:
        tempo["t"] += 100.0      # oltre i 60 s: il controllo si ripete a ogni giro
        return tempo["t"]

    return CicloCatena(config=ConfigCiclo(**CONFIG), identita=ident, servizio_firma=FirmaFinta(),
                       testimone=None, detentore="quarto-giro", adesso=lambda: ORA,
                       orologio=orologio)


async def _dimensione(url: str) -> int:
    return int((await sql(url, "SELECT dimensione FROM public.catena_stato"))[0]["dimensione"])


async def _blocchi(url: str) -> list[dict]:
    return [dict(r) for r in await sql(
        url, "SELECT causa, occorrenze, dettagli::text AS dettagli, "
             "chiuso_il IS NOT NULL AS chiuso FROM public.catena_blocchi_registro ORDER BY id")]


async def test_s3_senza_listbucket_blocco_esplicito_poi_ripartenza(db_url, s3, monkeypatch):
    tenant, classe = await crea_tenant_e_classe(db_url)
    await crea_pda(db_url, tenant, classe, n_doc=1)
    originale = coerenza._client_e_bucket
    monkeypatch.setattr(coerenza, "_client_e_bucket",
                        lambda: (ClientSenzaListBucket(), settings.s3_bucket_aip))
    ciclo = _ciclo(identita(testimone=False))
    with capture_logs() as log:
        await ciclo.giro()
    assert await _dimensione(db_url) == 0                     # registro fermo...
    blocchi = await _blocchi(db_url)                          # ...ma NON in silenzio
    assert [(b["causa"], b["chiuso"]) for b in blocchi] == [("COERENZA_NON_VERIFICABILE", False)]
    assert "AccessDenied" in blocchi[0]["dettagli"]
    assert '"errore_di_permesso": true' in blocchi[0]["dettagli"]
    eventi = [r["event"] for r in log]
    assert "registro_coerenza_non_verificabile" in eventi and "registro_bloccato" in eventi
    assert metriche().registro_bloccato._value.get() == 1      # allarme RegistroBloccato

    # Permesso concesso (ruolo corretto): al controllo successivo il blocco si chiude
    monkeypatch.setattr(coerenza, "_client_e_bucket",
                        lambda: (ClientDiProduzione(originale()[0]), originale()[1]))
    with capture_logs() as log:
        await ciclo.giro()
    assert "registro_coerente" in [r["event"] for r in log]
    assert [(b["causa"], b["chiuso"]) for b in await _blocchi(db_url)] == [
        ("COERENZA_NON_VERIFICABILE", True)]
    assert await _dimensione(db_url) == 1
    assert metriche().registro_bloccato._value.get() == 0
    chiusura = await sql(db_url, "SELECT 1 FROM public.audit_global "
                                 "WHERE evento = 'catena.blocco_registro_chiuso'")
    assert len(chiusura) == 1


async def test_errore_non_di_permesso_blocca_solo_se_persiste(db_url, s3, monkeypatch):
    class Irraggiungibile:
        def get_paginator(self, nome):
            class Paginatore:
                def paginate(self, **_):
                    raise ConnectionError("WORM irraggiungibile")
            return Paginatore()

    monkeypatch.setattr(coerenza, "_client_e_bucket",
                        lambda: (Irraggiungibile(), settings.s3_bucket_aip))
    ciclo = _ciclo(identita(testimone=False))
    for atteso in ([], [], [("COERENZA_NON_VERIFICABILE", False)]):
        await ciclo.giro()
        assert [(b["causa"], b["chiuso"]) for b in await _blocchi(db_url)] == atteso
    assert '"tentativi": 3' in (await _blocchi(db_url))[0]["dettagli"]


async def test_controllo_con_i_permessi_di_produzione(db_url, s3, monkeypatch):
    """Elenco solo sotto registro/ e 403 sulle chiavi assenti: il controllo
    funziona su un registro sano con checkpoint e segmenti nel WORM."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    originale = coerenza._client_e_bucket
    produzione = {}

    def client():
        vero, bucket = originale()
        produzione["c"] = ClientDiProduzione(vero)
        return produzione["c"], bucket

    monkeypatch.setattr(coerenza, "_client_e_bucket", client)
    ciclo = _ciclo(identita(testimone=False))
    for _ in range(3):
        await crea_pda(db_url, tenant, classe, n_doc=1)
        await ciclo.giro()                       # sequenzia, emette ed esporta
    assert await _dimensione(db_url) == 3
    # Riavvio (nuovo processo): il controllo legge segmenti e checkpoint del WORM
    with capture_logs() as log:
        nuovo = _ciclo(identita(testimone=False))
        await crea_pda(db_url, tenant, classe, n_doc=1)
        await nuovo.giro()
    assert "registro_coerente" in [r["event"] for r in log]
    assert await _blocchi(db_url) == []
    assert await _dimensione(db_url) == 4
    assert produzione["c"].elenchi and all(p.startswith("registro/")
                                           for p in produzione["c"].elenchi)


async def test_chiave_assente_con_i_permessi_di_produzione_non_e_un_errore(db_url, s3,
                                                                          monkeypatch):
    """Un segmento esportato oltre il database (X = 4 > D = 3) e nessun checkpoint 4:
    l'esistenza di «registro/checkpoint/4.txt» si verifica con un elenco del suo
    prefisso, MAI con una GET (che senza ListBucket incondizionato darebbe 403).
    Esito corretto: REGISTRO_INDIETRO, non «non verificabile»."""
    from app.registro import archivio_worm

    tenant, classe = await crea_tenant_e_classe(db_url)
    originale = coerenza._client_e_bucket
    produzione = {}

    def client():
        vero, bucket = originale()
        produzione["c"] = ClientDiProduzione(vero)
        return produzione["c"], bucket

    monkeypatch.setattr(coerenza, "_client_e_bucket", client)
    ciclo = _ciclo(identita(testimone=False))
    for _ in range(3):
        await crea_pda(db_url, tenant, classe, n_doc=1)
        await ciclo.giro()
    await archivio_worm.scrivi(archivio_worm.chiave_segmento(3, 4), b'{"foglia":"altrove"}\n',
                               "application/x-ndjson", ORA)
    esito = await coerenza.verifica()
    assert (esito.dimensione_db, esito.fine_segmenti_worm, esito.checkpoint_worm) == (3, 4, 3)
    assert esito.indietro and not esito.divergenze
    assert "registro/checkpoint/4.txt" in produzione["c"].elenchi
