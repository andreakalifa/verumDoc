# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — rilievo R5: la ricostruzione del registro dal
WORM accetta SOLO foglie coperte da un checkpoint firmato dal registro e
controfirmato dal testimone.

Prima della correzione le foglie oltre l'ultimo checkpoint davano un semplice
avviso: un segmento ``registro/foglie/<n>-<n+1>.jsonl`` depositato da chiunque
scriva nel bucket (con una foglia «scarto» falsa) entrava nel registro, la sua
richiesta interna veniva ricreata e il checkpoint successivo l'avrebbe firmata.
Ora la ricostruzione si ferma senza scrivere; con l'opzione esplicita
``scarta_coda_non_coperta`` ricostruisce fino all'ultimo checkpoint coperto e
scarta la coda senza scriverla.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest
from app.registro import archivio_worm, operatore, ricostruzione

from libs.catena.foglie import dati_foglia_ciclo_vita
from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.test_r05_ricostruzione_worm import (  # noqa: F401 — fixture
    ORA,
    _registro,
    _scenario,
    _usa,
    copie,
    s3,
    testimone,
)

pytestmark = [pytest.mark.integration, richiede_pg]


async def _segmento_falso(db_url, pda, n: int) -> None:  # noqa: F811
    """Segmento che nessun checkpoint copre, con una foglia «scarto» falsa."""
    vittima = pda[5]
    ente = (await sql(db_url, "SELECT tenant_id FROM public.aip WHERE urn = $1",
                      vittima.urn))[0]["tenant_id"]
    falsa = dati_foglia_ciclo_vita(
        "scarto", foglia_id=str(uuid.uuid4()), pda=vittima.urn, ente=str(ente),
        riferimento={"pratica": "PRATICA-MAI-ESISTITA"},
        impronta_documento_pratica=hashlib.sha256(b"verbale falso").hexdigest(),
        registrato_il=ORA)
    await archivio_worm.scrivi(archivio_worm.chiave_segmento(n, n + 1), falsa + b"\n",
                               "application/x-ndjson", ORA)


async def _falsa_nel_registro(url: str) -> list:
    return await sql(url, "SELECT 1 FROM public.catena_foglie_interne "
                          "WHERE pratica = 'PRATICA-MAI-ESISTITA'")


async def test_segmento_non_firmato_ferma_la_ricostruzione(db_url, copie, s3,  # noqa: F811
                                                           testimone) -> None:
    """R5: la coda non firmata è un problema: nulla si scrive (prima: avviso e
    foglia falsa nel registro)."""
    backup, pda = await _scenario(db_url, copie, testimone)
    n = (await _registro(db_url))["stato"][0]
    await _segmento_falso(db_url, pda, n)
    await _usa(backup)
    prima = await _registro(backup)
    r = await ricostruzione.ricostruisci(fino_a=n + 1, prova=False, operatore="Op",
                                         motivo="ripristino dopo il ritorno al backup")
    assert not r.riuscito
    assert any("non coperte da un checkpoint firmato" in p for p in r.problemi)
    assert r.foglie_scritte == 0
    assert await _registro(backup) == prima
    assert await _falsa_nel_registro(backup) == []


async def test_coda_non_firmata_scartata_con_l_opzione_esplicita(db_url, copie, s3,  # noqa: F811
                                                                testimone) -> None:
    """R5: con ``scarta_coda_non_coperta`` si ricostruisce fino all'ultimo
    checkpoint firmato e controfirmato; la foglia falsa NON entra."""
    backup, pda = await _scenario(db_url, copie, testimone)
    originale = await _registro(db_url)
    n = originale["stato"][0]
    await _segmento_falso(db_url, pda, n)
    await _usa(backup)
    r = await ricostruzione.ricostruisci(fino_a=n + 1, prova=False, operatore="Op",
                                         motivo="ripristino dopo il ritorno al backup",
                                         scarta_coda_non_coperta=True)
    assert r.riuscito, r.problemi
    assert (r.foglie_scritte, r.coda_scartata, r.fino_a) == (n, 1, n)
    assert any("scartate senza scriverle" in a for a in r.avvisi)
    assert (await _registro(backup))["foglie"] == originale["foglie"]
    assert await _falsa_nel_registro(backup) == []


async def test_checkpoint_firmati_ma_non_controfirmati_non_coprono(db_url, copie, s3,  # noqa: F811
                                                                   testimone) -> None:
    """R5: il testimone tace dopo il secondo giro: le foglie oltre il checkpoint
    10 (firmate dal solo registro) non si ricostruiscono; fino a 10 sì, e dalla
    riga di comando con --scarta-coda-non-coperta si arriva a 10."""
    backup, _ = await _scenario(db_url, copie, testimone, controfirmati=frozenset({0, 1}))
    await _usa(backup)
    r = await ricostruzione.ricostruisci(fino_a=24, prova=True)
    assert not r.riuscito and r.ultimo_checkpoint_controfirmato == 10
    assert any("foglie 10-24" in p for p in r.problemi)
    assert (await ricostruzione.ricostruisci(fino_a=10, prova=True)).riuscito
    codice = await operatore.esegui(operatore.analizza([
        "ricostruisci-da-worm", "--fino-a", "24", "--scarta-coda-non-coperta",
        "--operatore", "Op", "--motivo", "ripristino con testimone in ritardo"]))
    assert codice == 0
    assert (await _registro(backup))["stato"][0] == 10
