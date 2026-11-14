# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Motore dei pacchetti di distribuzione (PdD).
Riferimenti: F2.1.2; R03 b4 (VD-C-077, VD-C-005); REGOLE_SVILUPPO §10.3
(impronte calcolate sui byte), §19.6 (download), §14.3-§14.4 (fail-closed, 404).

Prima: COMPLETO e MIGRAZIONE restituivano chiavi S3 interne, ESTRATTO
ricostruiva chiavi dal nome del file (mai corrispondenti a quelle reali),
ESIBIZIONE collegava solo manifesto e indice, MIGRAZIONE aveva valori fissi.

Ora il contenuto di ogni PdD viene dal REGISTRO del PdA (``documenti``, scritto
da aip-builder con le chiavi reali ``{tenant}/{aip}/files/{ordine}-{nome}``):

  - COMPLETO, ESIBIZIONE: tutti i documenti + IPdA (``IndiceAIP_SInCRO.xml``)
    + manifesto del PdA;
  - ESTRATTO: i documenti richiesti per nome (DST-103 se un nome non c'è) +
    IPdA (l'indice è firmato e descrive l'intero PdA: non si «riduce»);
  - MIGRAZIONE: come COMPLETO, sempre in ZIP, con ``migrazione.json``
    (conservatore d'origine da impostazione, dati del PdA e impronte).

Consegna:
  - LINK (fino a ``PDD_SINCRONO_MAX_MB``): ogni file si rilegge dallo storage
    a blocchi e la sua impronta si VERIFICA prima di consegnare il
    collegamento prefirmato (``Content-Disposition: attachment``); un file che
    non corrisponde → 502 DST-502 e nessun collegamento;
  - ZIP (sopra soglia, MIGRAZIONE, o se richiesto): la richiesta risponde
    subito con stato IN_PREPARAZIONE; il lavoratore ``app.services.pdd_zip``
    genera lo ZIP in streaming verificando le impronte e pubblica
    ``pdd.pronto``. Lo stato e il collegamento: ``GET /api/v2/distribution/pdd/{id}``.
L'impronta del PdD (``hash_pdd``) è SHA-256 dell'elenco ordinato
«nome:impronta» dei file inclusi.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import botocore.exceptions
import structlog
from sqlalchemy import select, text

from app.config import settings
from app.models.distribution import (
    ConsegnaPdD,
    DocumentoDistribuito,
    RichiestaDistribuzione,
    RispostaDistribuzione,
    TipoPdD,
)
from app.services.s3_client import (
    ImprontaNonCorrispondente,
    compute_pdd_hash,
    generate_signed_url,
    get_aip_file,
    verifica_impronta,
)
from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.models import Aip, AipStato

logger = structlog.get_logger(__name__)

MB = 1024 * 1024
STATI_DISTRIBUIBILI = (AipStato.CONSERVATO, AipStato.IN_CATENA, AipStato.ESIBIZIONE)
NOME_IPDA = "IndiceAIP_SInCRO.xml"
NOME_MANIFESTO = "manifest.json"
#: Dimensione massima del manifesto del PdA letto per l'impronta dell'IPdA.
MANIFESTO_MAX_BYTE = 16 * MB


def _uuid_o_none(valore: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


async def _get_aip_record(aip_id: str, tenant_id: str) -> Aip:
    """
    Recupera l'AIP SOLO se appartiene al tenant del token verificato (VD-C-005).

    Un AIP di un altro cliente, un id non valido o un tenant non determinabile
    rispondono 404 come un AIP inesistente (REGOLE §14.3, §14.4).
    """
    non_trovato = ErroreApi("DST-404", f"AIP {aip_id!r} non presente nel sistema",
                            titolo="AIP non trovato")
    aip_uuid = _uuid_o_none(aip_id)
    tenant_uuid = _uuid_o_none(tenant_id)
    if tenant_uuid is None:
        logger.warning("distribuzione_tenant_non_determinabile", aip_id=aip_id)
        raise non_trovato
    if aip_uuid is None:
        raise non_trovato
    async with get_async_session() as session:
        result = await session.execute(
            select(Aip).where(Aip.id == aip_uuid, Aip.tenant_id == tenant_uuid)
        )
        aip = result.scalar_one_or_none()
    if not aip:
        raise non_trovato
    if aip.stato not in STATI_DISTRIBUIBILI:
        raise ErroreApi("DST-101", titolo=f"AIP non distribuibile — stato: {aip.stato.value}")
    return aip


def evento_distribuzione(
    aip_id: str, tenant_id: str, tipo: str,
    richiedente_id: str, pdd_id: str, motivo: str,
) -> dict:
    """Evento di ciclo di vita «aip.distribuito» (scritto nell'outbox dal registro)."""
    return {
        "evento": "aip.distribuito",
        "aip_id": aip_id,
        "pdd_id": pdd_id,
        "tenant_id": tenant_id,
        "tipo_pdd": tipo,
        "richiedente_id": richiedente_id,
        "motivo": motivo,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── Contenuto del pacchetto ─────────────────────────────────────────

async def documenti_del_pda(aip_id: uuid.UUID) -> list[dict[str, Any]]:
    """Documenti del PdA dal registro, in ordine (una query)."""
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT nome_file, tipo, formato_mime, s3_key, hash_sha256, dimensione_bytes, ordine "
            "FROM public.documenti WHERE aip_id = :id ORDER BY ordine, id"
        ), {"id": aip_id})).mappings().all()
    return [dict(r) for r in righe]


def file_del_pacchetto(aip: Any, documenti: list[dict[str, Any]], tipo: TipoPdD,
                       file_richiesti: list[str] | None) -> list[dict[str, Any]]:
    """
    File inclusi (funzione pura). Ogni voce: nome, nome_zip, tipo, formato_mime,
    bucket, chiave, sha256 (None per l'IPdA: si legge dal manifesto),
    dimensione. DST-102/DST-103 per un ESTRATTO senza file o con nomi assenti.
    """
    scelti = documenti
    if tipo == TipoPdD.ESTRATTO:
        if not file_richiesti:
            raise ErroreApi("DST-102", "file_richiesti obbligatorio per tipo ESTRATTO")
        per_nome = {d["nome_file"]: d for d in documenti}
        mancanti = [n for n in dict.fromkeys(file_richiesti) if n not in per_nome]
        if mancanti:
            raise ErroreApi("DST-103", f"{len(mancanti)} file non presenti nel pacchetto",
                            dettagli={"mancanti": mancanti[:100],
                                      "disponibili": sorted(per_nome)[:200]})
        voluti = set(file_richiesti)
        scelti = [d for d in documenti if d["nome_file"] in voluti]
    voci: list[dict[str, Any]] = [{
        "nome": d["nome_file"],
        "nome_zip": f"documenti/{int(d['ordine']):04d}-{d['nome_file']}",
        "tipo": d["tipo"], "formato_mime": d["formato_mime"],
        "bucket": aip.s3_bucket, "chiave": d["s3_key"],
        "sha256": d["hash_sha256"], "dimensione": d["dimensione_bytes"],
    } for d in scelti]
    if aip.sincro_xml_key:
        voci.append({"nome": NOME_IPDA, "nome_zip": NOME_IPDA, "tipo": "INDICE",
                     "formato_mime": "application/xml", "bucket": aip.s3_bucket,
                     "chiave": aip.sincro_xml_key, "sha256": None, "dimensione": None})
    if tipo != TipoPdD.ESTRATTO:
        voci.append({"nome": NOME_MANIFESTO, "nome_zip": NOME_MANIFESTO, "tipo": "MANIFEST",
                     "formato_mime": "application/json", "bucket": aip.s3_bucket,
                     "chiave": aip.s3_key, "sha256": aip.hash_sha256,
                     "dimensione": aip.dimensione_bytes})
    return voci


async def impronta_ipda_dal_manifesto(bucket: str, chiave: str, hash_manifesto: str) -> str | None:
    """Impronta dell'IPdA scritta nel manifesto del PdA, dopo aver verificato
    il manifesto stesso; ImprontaNonCorrispondente se il manifesto è alterato."""
    import hashlib

    contenuto = await get_aip_file(bucket, chiave, MANIFESTO_MAX_BYTE)
    calcolata = hashlib.sha256(contenuto).hexdigest()
    if calcolata != (hash_manifesto or "").lower():
        raise ImprontaNonCorrispondente(chiave, hash_manifesto, calcolata, None, len(contenuto))
    try:
        valore = json.loads(contenuto).get("sincro_hash_sha256")
    except (ValueError, AttributeError):
        return None
    return str(valore) if valore else None


def impronta_pdd(voci: list[dict[str, Any]]) -> str:
    return compute_pdd_hash([f"{v['nome']}:{v['sha256']}" for v in voci])


def durata_link(richiesta: int | None) -> int:
    return min(richiesta or settings.pdd_durata_link_secondi, settings.pdd_durata_link_max_secondi)


def scegli_consegna(tipo: TipoPdD, richiesta: ConsegnaPdD | None, totale_byte: int) -> ConsegnaPdD:
    """MIGRAZIONE e PdD sopra soglia: ZIP; altrimenti quella chiesta (predefinita LINK)."""
    if tipo == TipoPdD.MIGRAZIONE or totale_byte > settings.pdd_sincrono_max_mb * MB:
        return ConsegnaPdD.ZIP
    return richiesta or ConsegnaPdD.LINK


def errore_integrita(exc: ImprontaNonCorrispondente, aip_id: str) -> ErroreApi:
    """Anomalia d'integrità: nel log con tutti i dati, al client solo il codice."""
    logger.error("pdd_impronta_non_corrispondente", aip_id=aip_id, chiave=exc.chiave,
                 attesa=exc.attesa, calcolata=exc.calcolata,
                 dimensione_attesa=exc.dimensione_attesa, dimensione_letta=exc.dimensione_letta)
    return ErroreApi("DST-502", "Un file del pacchetto non corrisponde all'impronta registrata: "
                                "il pacchetto non è stato consegnato.")


async def _verifica_e_collega(voci: list[dict[str, Any]], durata: int, aip_id: str) -> None:
    """Consegna LINK: verifica le impronte (a blocchi) e firma i collegamenti."""
    try:
        for v in voci:
            if v["sha256"]:
                v["dimensione"] = await verifica_impronta(v["bucket"], v["chiave"], v["sha256"],
                                                          v["dimensione"])
                v["verificata"] = True
            v["url"] = await generate_signed_url(v["bucket"], v["chiave"], durata, v["nome"])
    except ImprontaNonCorrispondente as exc:
        raise errore_integrita(exc, aip_id) from None
    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError, OSError) as exc:
        raise errore_registrato("DST-503", exc, evento="pdd_storage_non_disponibile",
                                titolo="Storage del pacchetto non disponibile") from None


async def esegui_distribuzione(
    req: RichiestaDistribuzione, tenant_id: str,
) -> RispostaDistribuzione:
    """
    Prepara il PdD richiesto. ``tenant_id`` è quello del token verificato:
    l'AIP si cerca solo dentro quel tenant (VD-C-005). Il registro (notifiche,
    ``pacchetti_distribuzione``, outbox) lo scrive il chiamante con
    ``app.services.registro_pdd``.
    """
    aip = await _get_aip_record(req.aip_id, tenant_id)
    documenti = await documenti_del_pda(aip.id)
    voci = file_del_pacchetto(aip, documenti, req.tipo, req.file_richiesti)
    totale = sum(int(v["dimensione"] or 0) for v in voci)
    consegna = scegli_consegna(req.tipo, req.consegna, totale)
    durata = durata_link(req.url_expiry_seconds)
    pdd_id = str(uuid.uuid4())
    adesso = datetime.now(UTC)

    if consegna == ConsegnaPdD.LINK:
        if aip.sincro_xml_key:
            try:
                ipda = next(v for v in voci if v["nome"] == NOME_IPDA)
                ipda["sha256"] = await impronta_ipda_dal_manifesto(
                    aip.s3_bucket, aip.s3_key, aip.hash_sha256)
            except ImprontaNonCorrispondente as exc:
                raise errore_integrita(exc, str(aip.id)) from None
            except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError,
                    OSError, ValueError) as exc:
                raise errore_registrato("DST-503", exc, evento="pdd_storage_non_disponibile",
                                        titolo="Storage del pacchetto non disponibile") from None
        await _verifica_e_collega(voci, durata, str(aip.id))
        scadenza = adesso + timedelta(seconds=durata)
        risposta = RispostaDistribuzione(
            pdd_id=pdd_id, aip_id=str(aip.id), tipo=req.tipo.value, stato="PRONTO",
            consegna=consegna.value,
            documenti=[DocumentoDistribuito(
                nome_file=v["nome"], tipo=v["tipo"], formato_mime=v["formato_mime"],
                hash_sha256=v["sha256"], dimensione_bytes=v["dimensione"],
                signed_url=v["url"], impronta_verificata=bool(v.get("verificata")),
            ) for v in voci],
            sincro_xml_incluso=bool(aip.sincro_xml_key), hash_pdd=impronta_pdd(voci),
            created_at=adesso.isoformat(), url_scadenza=scadenza.isoformat(),
            link_stato=f"/api/v2/distribution/pdd/{pdd_id}",
            messaggio=f"Collegamenti validi {durata} s; impronte verificate sui file.",
        )
    else:
        risposta = RispostaDistribuzione(
            pdd_id=pdd_id, aip_id=str(aip.id), tipo=req.tipo.value, stato="IN_PREPARAZIONE",
            consegna=consegna.value,
            documenti=[DocumentoDistribuito(
                nome_file=v["nome"], tipo=v["tipo"], formato_mime=v["formato_mime"],
                hash_sha256=v["sha256"], dimensione_bytes=v["dimensione"],
            ) for v in voci],
            sincro_xml_incluso=bool(aip.sincro_xml_key), hash_pdd=None,
            created_at=adesso.isoformat(), link_stato=f"/api/v2/distribution/pdd/{pdd_id}",
            messaggio="Pacchetto ZIP in preparazione: seguire link_stato o l'evento pdd.pronto.",
        )
    risposta._file_interni = [
        {k: v.get(k) for k in ("nome", "nome_zip", "tipo", "formato_mime", "bucket", "chiave",
                               "sha256", "dimensione")} for v in voci]
    logger.info("pdd_preparato", pdd_id=pdd_id, aip_id=str(aip.id), tipo=req.tipo.value,
                consegna=consegna.value, file=len(voci), byte=totale)
    return risposta


async def collegamenti_aggiornati(file_interni: list[dict[str, Any]], scade_il: datetime,
                                  durata: int, adesso: datetime) -> tuple[list[dict], str]:
    """Nuovi collegamenti per un PdD LINK ancora valido (GET dello stato)."""
    rimanente = max(1, min(durata, int((scade_il - adesso).total_seconds())))
    documenti = []
    for v in file_interni:
        documenti.append({
            "nome_file": v["nome"], "tipo": v["tipo"], "formato_mime": v["formato_mime"],
            "hash_sha256": v.get("sha256"), "dimensione_bytes": v.get("dimensione"),
            "signed_url": await generate_signed_url(v["bucket"], v["chiave"], rimanente, v["nome"]),
            "impronta_verificata": bool(v.get("sha256")),
        })
    return documenti, (adesso + timedelta(seconds=rimanente)).isoformat()


__all__ = [
    "NOME_IPDA", "NOME_MANIFESTO", "STATI_DISTRIBUIBILI", "collegamenti_aggiornati",
    "documenti_del_pda", "durata_link", "errore_integrita", "esegui_distribuzione",
    "evento_distribuzione", "file_del_pacchetto", "impronta_ipda_dal_manifesto",
    "impronta_pdd", "scegli_consegna",
]

