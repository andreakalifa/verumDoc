# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Scritture del registro sullo storage conservativo (WORM): checkpoint, controfirme,
sigilli e foglie esportate. Il registro si ricostruisce dal WORM (piano §5).

Servizio: hashchain. Riferimenti: contratto K2 di R04 (``registro/checkpoint/
<dimensione>.txt``); REGOLE_SVILUPPO §10.1, §10.2 (retention su ogni scrittura).

Retention: gli oggetti del registro coprono PdA di ogni classe, anche
permanenti, quindi si scrivono come conservazione PERMANENTE (legal hold +
retention fino a ``RETENTION_PERMANENTE_ORIZZONTE_ANNI``, la stessa impostazione
di aip-builder). Senza quell'impostazione la scrittura fallisce con errore
esplicito e il checkpoint NON si pubblica.

Ogni contenuto è deterministico: un nuovo tentativo dopo un errore trova
l'oggetto già presente e lo accetta solo se identico (mai sovrascritto).

R12a (VD-C-148): ogni oggetto scritto entra nell'elenco del rinnovo del blocco
WORM (``app.registro.rinnovo_worm``).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from app.config import settings
from libs.shared.storage import worm

logger = structlog.get_logger(__name__)

PREFISSO = "registro"


def chiave_checkpoint(dimensione: int) -> str:
    return f"{PREFISSO}/checkpoint/{dimensione}.txt"


def chiave_controfirmato(dimensione: int) -> str:
    return f"{PREFISSO}/checkpoint/{dimensione}.controfirmato.txt"


def chiave_sigillo(dimensione: int) -> str:
    return f"{PREFISSO}/checkpoint/{dimensione}.p7m"


def chiave_segmento(da: int, a: int) -> str:
    """Segmento di foglie [da, a): una riga di JSON canonico per foglia, in ordine."""
    return f"{PREFISSO}/foglie/{da:012d}-{a:012d}.jsonl"


class ContenutoWormDiverso(worm.ErroreStorageConservativo):  # type: ignore[misc]  # base non tipizzata
    """Oggetto del registro già presente con contenuto diverso: allarme di integrità."""

    codice = "CAT-WORM-DIVERSO"


def _scrivi_sync(chiave: str, contenuto: bytes, tipo: str, adesso: datetime,
                 orizzonte: int) -> tuple[str | None, datetime | None]:
    from app.services.block_sealer import _client

    client = _client()
    bucket = worm.bucket_conservativo(settings)
    retention = worm.calcola_retention(
        worm.PoliticaConservazione(anni_conservazione=None, permanente=True), adesso,
        orizzonte_permanente_anni=orizzonte,
    )
    try:
        risposta = worm.scrivi_oggetto_conservato(
            client, bucket, chiave, contenuto, tipo, retention, adesso=datetime.now(UTC),
            produzione=settings.env.lower() == "production", regione=settings.s3_region,
        )
        versione = risposta.get("VersionId")
        return (str(versione) if versione else None), retention.conserva_fino_a
    except worm.OggettoGiaPresente:
        esistente = worm.leggi_oggetto(client, bucket, chiave)
        if esistente != contenuto:
            logger.error("registro_worm_contenuto_diverso", chiave=chiave)
            raise ContenutoWormDiverso(
                "oggetto del registro già presente con contenuto diverso",
                bucket=bucket, chiave=chiave,
            ) from None
        logger.info("registro_worm_gia_presente_identico", chiave=chiave)
        return None, None


async def scrivi(chiave: str, contenuto: bytes, tipo: str,
                 adesso: datetime) -> tuple[str, str | None]:
    """Scrive (o ritrova identico) un oggetto del registro; (bucket, version_id)."""
    from libs.shared.parametri import orizzonte_permanente_anni

    orizzonte = await orizzonte_permanente_anni(settings.retention_permanente_orizzonte_anni)
    versione, fino_a = await asyncio.to_thread(_scrivi_sync, chiave, contenuto, tipo, adesso,
                                               orizzonte)
    # R12a (VD-C-148): l'oggetto entra nell'elenco del rinnovo del blocco WORM
    # (ritrovato identico: termine ignoto, lo legge il rinnovo). Un errore qui non
    # annulla la scrittura: il censimento lo recupera.
    try:
        from app.registro import rinnovo_worm

        await rinnovo_worm.registra(chiave, versione, fino_a)
    except Exception as exc:  # noqa: BLE001 — recuperato dal censimento
        logger.warning("registro_worm_elenco_non_aggiornato", chiave=chiave,
                       errore=type(exc).__name__)
    return worm.bucket_conservativo(settings), versione
