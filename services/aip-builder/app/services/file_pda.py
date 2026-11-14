# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Inclusione di un file del PdV nel PdA SENZA tenerlo in memoria (R03, b5).

Servizio: aip-builder. Riferimenti: piano R03 §4.2 («nessun file in memoria»),
REGOLE_SVILUPPO §10.2 (retention su ogni scrittura WORM), §10.3 (impronte
ricalcolate sui byte letti dallo storage), §12.2 (ritentativi idempotenti).

Sostituisce, per ogni documento, il vecchio «scarica tutto → ricalcola →
ricarica»:
  1. le impronte (algoritmo dichiarato, SHA-256, SHA-512) e la dimensione si
     calcolano LEGGENDO A BLOCCHI il file nello staging;
  2. l'impronta dichiarata e la dimensione versata si confrontano
     (AIP-INTEGRITA-001/002/003, errori permanenti);
  3. il file si COPIA lato storage nel bucket conservativo (CopyObject o
     UploadPartCopy), vincolato all'ETag letto, con retention COMPLIANCE, e la
     copia si verifica (AIP-STORAGE-002 se diversa);
  4. ritentativo dopo un crash: l'oggetto già presente si accetta solo se ha la
     stessa impronta SHA-256, letta a blocchi (AIP-STORAGE-001 altrimenti).
Gli errori dello storage non classificati si propagano: il ciclo affidabile
ritenta e poi manda in DLQ.

Restituisce la voce ``documenti[]`` nello stesso formato che ``aip_engine``
usa per IPdA, manifest e tabella ``documenti``.
"""
from __future__ import annotations

from typing import Any

import structlog

from app.services.s3_uploader import (
    CopiaNonCorrispondente,
    ImpronteOggetto,
    OggettoGiaPresente,
    RetentionOggetto,
    copia_in_worm,
    impronte_oggetto,
    oggetto_identico,
)
from libs.shared.impronte import funzione_impronta
from libs.shared.kafka.consumatore_affidabile import ErrorePermanente

logger = structlog.get_logger(__name__)

#: Impronte sempre calcolate per IPdA, manifest e tabella documenti
ALGORITMI_PDA = ("SHA-256", "SHA-512")


def controlla_integrita(doc: dict, info: dict, letto: ImpronteOggetto, algoritmo: str) -> None:
    """Impronta dichiarata e dimensione versata contro quanto letto dallo storage."""
    nome = str(doc.get("nome_file", ""))
    dichiarata = str((doc.get("hash") or {}).get("impronta", ""))
    calcolata = letto.impronte[algoritmo]
    if calcolata.lower() != dichiarata.lower():
        raise ErrorePermanente(
            "AIP-INTEGRITA-002",
            "L'impronta ricalcolata sul file non corrisponde a quella dichiarata",
            {"algoritmo": algoritmo, "dichiarata": dichiarata, "calcolata": calcolata},
        )
    dimensione_attesa = info.get("size_bytes")
    if dimensione_attesa is not None and int(dimensione_attesa) != letto.dimensione:
        raise ErrorePermanente(
            "AIP-INTEGRITA-003", f"Dimensione del file {nome!r} diversa da quella versata",
            {"attesa": dimensione_attesa, "letta": letto.dimensione},
        )


async def includi_file_nel_pda(
    *,
    aip_id: str,
    tenant_id: str,
    ordine: int,
    doc: dict,
    info: dict,
    suffisso: str,
    retention: RetentionOggetto,
    client: Any | None = None,
) -> dict:
    """Verifica e copia nello storage WORM un file del PdV; vedi la docstring del modulo.

    ``doc`` è il documento dell'indice, ``info`` il suo elemento di ``docs_s3_keys``,
    ``suffisso`` la parte finale della chiave nel PdA (``files/0000-nome``)."""
    doc_id = str(doc.get("id", ""))
    nome = str(doc.get("nome_file", ""))
    if not info or not info.get("s3_key") or not info.get("s3_bucket"):
        raise ErrorePermanente("AIP-FILE-001", f"File del documento {doc_id!r} non referenziato")
    algoritmo = str((doc.get("hash") or {}).get("algoritmo", "")).strip().upper()
    if funzione_impronta(algoritmo) is None:
        raise ErrorePermanente("AIP-INTEGRITA-001",
                               f"Algoritmo d'impronta non ammesso: {algoritmo!r}")

    # 1-2. Impronte in streaming e confronto (errore di lettura: transitorio, si propaga)
    letto = await impronte_oggetto(
        info["s3_bucket"], info["s3_key"], sorted({algoritmo, *ALGORITMI_PDA}), client=client,
    )
    controlla_integrita(doc, info, letto, algoritmo)

    # 3-4. Copia server-side nel bucket conservativo, idempotente
    formato = doc.get("formato") or info.get("mime") or "application/octet-stream"
    try:
        chiave_doc, _, _ = await copia_in_worm(
            aip_id=aip_id, tenant_id=tenant_id, bucket_origine=info["s3_bucket"],
            chiave_origine=info["s3_key"], key_suffix=suffisso, content_type=formato,
            retention=retention, origine=letto, client=client,
        )
    except OggettoGiaPresente as exc:
        bucket, chiave = str(exc.dettagli.get("bucket")), str(exc.dettagli.get("chiave"))
        if not await oggetto_identico(bucket, chiave, letto.impronte["SHA-256"], client=client):
            raise ErrorePermanente(
                "AIP-STORAGE-001",
                "Oggetto già presente nello storage conservativo con contenuto diverso",
                {"chiave": chiave},
            ) from exc
        logger.info("oggetto_pda_gia_presente_identico", chiave=chiave)
        chiave_doc = chiave
    except CopiaNonCorrispondente as exc:
        raise ErrorePermanente(
            "AIP-STORAGE-002",
            "La copia nello storage conservativo non corrisponde al file versato",
            {"chiave": exc.dettagli.get("chiave")},
        ) from exc

    logger.info("file_incluso_nel_pda", aip_id=aip_id, ordine=ordine, size=letto.dimensione)
    return {
        "id": doc_id, "nome_file": nome, "tipo": doc.get("tipo", "PRINCIPALE"),
        "formato": formato, "hash_sha256": letto.impronte["SHA-256"],
        "hash_sha512": letto.impronte["SHA-512"], "dimensione_bytes": letto.dimensione,
        "s3_key": chiave_doc, "ordine": ordine,
    }
