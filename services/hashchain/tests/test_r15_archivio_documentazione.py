# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R15 (VD-C-177) — archivio WORM delle versioni della documentazione del servizio, su
PostgreSQL vero e S3 (moto con Object Lock).

  - la versione va nel WORM con l'impronta nella chiave (Markdown e HTML) e retention del
    registro; la riga registra chiavi e versioni una sola volta;
  - idempotente: dopo un crash fra la scrittura e l'aggiornamento della riga la seconda
    scrittura ritrova l'oggetto identico; un giro successivo non riscrive nulla;
  - un oggetto DIVERSO con la stessa chiave non si registra (blocco del registro);
  - impronta diversa dal contenuto: non si archivia;
  - fencing: con il lease perso nessun aggiornamento.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest
from app.config import settings
from app.registro import archivio, archivio_worm, documentazione
from app.registro.metriche import metriche

from libs.shared.db import get_async_session
from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.test_r13a_firme_documenti import ADESSO, IO, db, s3  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


async def _versione(url: str, testo: str = "# Manuale\n") -> dict:  # type: ignore[type-arg]
    gen = (await sql(url, "INSERT INTO public.documentazione_generazioni (scade_il) VALUES "
                          "(now() + interval '5 minutes') RETURNING id"))[0]["id"]
    md, html = testo.encode(), f"<p>{testo}</p>".encode()
    riga = {"id": uuid.uuid4(), "tipo": "MANUALE_CONSERVAZIONE",
            "impronta_sha256": hashlib.sha256(md).hexdigest(),
            "impronta_html": hashlib.sha256(html).hexdigest(), "markdown": md, "html": html}
    await sql(url, "INSERT INTO public.documentazione_versioni (id, tipo, versione, "
                   "generazione_id, titolo, nome_file, markdown, html, impronta_contenuto, "
                   "impronta_sha256, impronta_html, sezioni, differenze, fonti, da_compilare, "
                   "da_verificare, generata_il) VALUES ($1, $2, 1, $3, 'Manuale', "
                   "'manuale_conservazione', $4, $5, $6, $7, $8, '[]', '{}', '{}', '[]', '[]', "
                   "now())", riga["id"], riga["tipo"], gen, md, html, "c" * 64,
              riga["impronta_sha256"], riga["impronta_html"])
    return riga


async def _archivio(url: str) -> dict:  # type: ignore[type-arg]
    return dict((await sql(url, "SELECT worm_chiave_md, worm_chiave_html, worm_versione_md, "
                                "archiviata_il FROM public.documentazione_versioni"))[0])


async def test_versione_nel_worm_con_l_impronta_nella_chiave(db, s3) -> None:  # noqa: F811
    v = await _versione(db)
    esito = await documentazione.archivia(detentore=IO, adesso=ADESSO)
    assert esito == {"archiviate": 1, "errori": 0}
    a = await _archivio(db)
    chiave_md, chiave_html = documentazione.chiavi(v)
    assert a["worm_chiave_md"] == chiave_md == \
        f"registro/documentazione/manuale_conservazione/{v['impronta_sha256']}.md"
    assert a["worm_chiave_html"] == chiave_html and a["archiviata_il"] is not None
    oggetto = s3.get_object(Bucket=settings.s3_bucket_aip, Key=chiave_md)
    assert oggetto["Body"].read() == v["markdown"]
    assert oggetto["ObjectLockMode"] == "COMPLIANCE"
    assert metriche().documentazione_da_archiviare._value.get() == 0  # noqa: SLF001
    # nessun nuovo giro di scrittura
    assert await documentazione.archivia(detentore=IO, adesso=ADESSO) == \
        {"archiviate": 0, "errori": 0}


async def test_ripresa_dopo_crash_idempotente(db, s3) -> None:  # noqa: F811
    v = await _versione(db)
    chiave_md, chiave_html = documentazione.chiavi(v)
    # crash dopo la scrittura nel WORM e prima dell'aggiornamento della riga
    await archivio_worm.scrivi(chiave_md, v["markdown"], "text/markdown; charset=utf-8", ADESSO)
    esito = await documentazione.archivia(detentore=IO, adesso=ADESSO)
    assert esito == {"archiviate": 1, "errori": 0}
    assert (await _archivio(db))["worm_chiave_md"] == chiave_md


async def test_oggetto_diverso_con_la_stessa_chiave_non_si_registra(db, s3) -> None:  # noqa: F811
    v = await _versione(db)
    chiave_md, _ = documentazione.chiavi(v)
    await archivio_worm.scrivi(chiave_md, b"contenuto diverso", "text/markdown", ADESSO)
    with pytest.raises(archivio_worm.ContenutoWormDiverso):
        await documentazione.archivia(detentore=IO, adesso=ADESSO)
    assert (await _archivio(db))["archiviata_il"] is None


def test_impronta_diversa_non_si_archivia() -> None:
    riga = {"id": "x", "markdown": b"# A", "html": b"<p>A</p>",
            "impronta_sha256": hashlib.sha256(b"# B").hexdigest(),
            "impronta_html": hashlib.sha256(b"<p>A</p>").hexdigest()}
    with pytest.raises(documentazione.ImprontaDiversa):
        documentazione._controlla(riga)  # noqa: SLF001
    riga["impronta_sha256"] = hashlib.sha256(b"# A").hexdigest()
    assert documentazione._controlla(riga) == (b"# A", b"<p>A</p>")  # noqa: SLF001


async def test_lease_perso_nessun_aggiornamento(db, s3) -> None:  # noqa: F811
    await _versione(db)
    async with get_async_session() as session:
        await archivio.rilascia_lease(session, IO)
        assert await archivio.acquisisci_lease(session, "altro", ADESSO,
                                               ADESSO + timedelta(hours=1))
    with pytest.raises(archivio.LeasePerso):
        await documentazione.archivia(detentore=IO, adesso=ADESSO)
    assert (await _archivio(db))["archiviata_il"] is None


async def test_riga_con_impronta_alterata_non_si_archivia(db, s3) -> None:  # noqa: F811
    """Anche se una riga fosse alterata aggirando il trigger (superutente), la hashchain
    ricalcola le impronte e non la archivia."""
    v = await _versione(db)
    await sql(db, "ALTER TABLE public.documentazione_versioni DISABLE TRIGGER "
                  "documentazione_versioni_protette")
    await sql(db, "UPDATE public.documentazione_versioni SET markdown = 'alterato'")
    await sql(db, "ALTER TABLE public.documentazione_versioni ENABLE TRIGGER "
                  "documentazione_versioni_protette")
    esito = await documentazione.archivia(detentore=IO, adesso=ADESSO)
    assert esito == {"archiviate": 0, "errori": 1}
    assert (await _archivio(db))["archiviata_il"] is None
    chiave_md, _ = documentazione.chiavi(v)
    assert "Contents" not in s3.list_objects_v2(Bucket=settings.s3_bucket_aip, Prefix=chiave_md)
