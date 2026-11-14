# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/services/pdd_zip.py — preparazione dei PdD ZIP fuori dalla richiesta.

Riferimenti: R03 b4 (VD-C-077); piano R03–R05 §4.2 (nessun file in memoria,
lavoro pesante fuori dalla richiesta); REGOLE_SVILUPPO §10.3 (impronte sui
byte), §12.3 (outbox), §12.4 (ritentativi con lista costante), §12.9 (lease).

Un PdD con consegna ZIP nasce IN_PREPARAZIONE (``registro_pdd``). Questo
lavoratore:
  1. prende un PdD con un LEASE (``lease_fino_al``, ``FOR UPDATE SKIP LOCKED``):
     più repliche non preparano lo stesso pacchetto; un lease scaduto (crash)
     lo rende di nuovo disponibile;
  2. scrive lo ZIP IN STREAMING sul bucket temporaneo ``S3_BUCKET_PDD`` con
     un caricamento multipart (in memoria al più una parte): ogni file si legge
     a blocchi dallo storage conservativo, la sua impronta SHA-256 si verifica
     mentre lo si scrive (IPdA: impronta letta dal manifesto verificato);
     dentro lo ZIP ``contenuto_pdd.json`` elenca file e impronte e, per una
     MIGRAZIONE, ``migrazione.json`` riporta conservatore d'origine (da
     impostazione), PdA, destinatario e motivo;
  3. esito: PRONTO (impronta e dimensione dello ZIP, scadenza
     ``PDD_VALIDITA_ORE`` dell'ente dalla console, R07 D18, al più
     ``PDD_ZIP_VALIDITA_ORE``) ed evento ``pdd.pronto`` nell'outbox nella
     stessa transazione; impronta non corrispondente → ERRORE definitivo
     (DST-502, anomalia nel log, caricamento annullato); altro errore →
     nuovo tentativo dopo ``PDD_RITARDI_SECONDI``, poi ERRORE (DST-503).
``pulisci_scaduti`` elimina gli ZIP scaduti e marca SCADUTO i PdD scaduti.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.services.distribution_engine import (
    MANIFESTO_MAX_BYTE,
    NOME_IPDA,
    NOME_MANIFESTO,
    impronta_pdd,
)
from app.services.s3_client import (
    ImprontaNonCorrispondente,
    _get_s3,
    leggi_piccolo_sync,
    verifica_impronta_sync,
)
from libs.shared.db import get_async_session
from libs.shared.db.ambito import con_ambito_conservatore
from libs.shared.errori import ErroreApi
from libs.shared.outbox import accoda_evento

logger = structlog.get_logger(__name__)

MB = 1024 * 1024


class _CaricamentoMultipart(io.RawIOBase):
    """Flusso di sola scrittura verso un caricamento multipart S3, con impronta.

    Non è «seekable»: ``zipfile`` scrive allora i descrittori dopo i dati e
    non torna mai indietro. In memoria resta al più una parte."""

    def __init__(self, client: Any, bucket: str, chiave: str, parte_byte: int) -> None:
        super().__init__()
        self._s3, self._bucket, self._chiave = client, bucket, chiave
        self._parte = parte_byte
        self._buffer = bytearray()
        self._parti: list[dict[str, Any]] = []
        self._posizione = 0
        self.impronta = hashlib.sha256()
        self._upload_id = client.create_multipart_upload(
            Bucket=bucket, Key=chiave, ContentType="application/zip")["UploadId"]

    def writable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._posizione

    def write(self, dati: Any) -> int:
        blocco = bytes(dati)
        self._buffer.extend(blocco)
        self.impronta.update(blocco)
        self._posizione += len(blocco)
        while len(self._buffer) >= self._parte:
            self._invia(bytes(self._buffer[: self._parte]))
            del self._buffer[: self._parte]
        return len(blocco)

    def _invia(self, dati: bytes) -> None:
        numero = len(self._parti) + 1
        risposta = self._s3.upload_part(Bucket=self._bucket, Key=self._chiave,
                                        UploadId=self._upload_id, PartNumber=numero, Body=dati)
        self._parti.append({"ETag": risposta["ETag"], "PartNumber": numero})

    def completa(self) -> int:
        if self._buffer or not self._parti:
            self._invia(bytes(self._buffer))
            self._buffer.clear()
        self._s3.complete_multipart_upload(
            Bucket=self._bucket, Key=self._chiave, UploadId=self._upload_id,
            MultipartUpload={"Parts": self._parti})
        return self._posizione

    def annulla(self) -> None:
        try:
            self._s3.abort_multipart_upload(Bucket=self._bucket, Key=self._chiave,
                                            UploadId=self._upload_id)
        except Exception as exc:  # noqa: BLE001 — pulizia: il multipart orfano scade da sé
            logger.warning("pdd_zip_annullamento_non_riuscito", chiave=self._chiave,
                           errore=type(exc).__name__)


def _manifesto_migrazione(riga: dict[str, Any], aip: dict[str, Any], voci: list[dict],
                          versione_sincro: str | None, adesso: datetime) -> dict[str, Any]:
    opzioni = riga.get("opzioni") or {}
    return {
        "tipo_operazione": "MIGRAZIONE",
        "pdd_id": str(riga["id"]),
        "conservatore_origine": {
            "denominazione": settings.conservatore_denominazione or None,
            "codice_fiscale": settings.conservatore_codice_fiscale or None,
        },
        "conservatore_destinatario": opzioni.get("conservatore_destinatario"),
        "motivo": riga.get("motivo"),
        "data_preparazione": adesso.isoformat(),
        "aip": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in aip.items()},
        "versione_formato_ipda": versione_sincro,
        "file": [{"nome": v["nome_zip"], "sha256": v["sha256"], "dimensione": v["dimensione"]}
                 for v in voci],
    }


def costruisci_zip_sync(riga: dict[str, Any], aip: dict[str, Any], *,
                        client: Any | None = None, adesso: datetime | None = None) -> dict:
    """
    Scrive lo ZIP del PdD (sincrono: gira in un thread). Restituisce chiave,
    dimensione, impronta dello ZIP e i file con impronte verificate.
    ``ImprontaNonCorrispondente`` se un file non corrisponde (ZIP annullato).
    """
    ora = adesso or datetime.now(UTC)
    s3 = client or _get_s3()
    voci = [dict(v) for v in (riga.get("documenti") or [])]
    versione_sincro: str | None = None
    manifesto = next((v for v in voci if v["nome"] == NOME_MANIFESTO), None)
    if any(v["nome"] == NOME_IPDA and not v.get("sha256") for v in voci):
        sorgente = manifesto or {"bucket": aip["s3_bucket"], "chiave": aip["s3_key"],
                                 "sha256": aip["hash_sha256"]}
        contenuto = leggi_piccolo_sync(sorgente["bucket"], sorgente["chiave"],
                                       MANIFESTO_MAX_BYTE, s3)
        calcolata = hashlib.sha256(contenuto).hexdigest()
        if calcolata != str(sorgente["sha256"]).lower():
            raise ImprontaNonCorrispondente(sorgente["chiave"], sorgente["sha256"], calcolata,
                                            None, len(contenuto))
        dati = json.loads(contenuto)
        versione_sincro = dati.get("versione_sincro")
        for v in voci:
            if v["nome"] == NOME_IPDA:
                v["sha256"] = dati.get("sincro_hash_sha256")

    chiave = f"pdd/{riga['tenant_id']}/{riga['id']}.zip"
    flusso = _CaricamentoMultipart(s3, settings.s3_bucket_pdd, chiave,
                                   settings.pdd_zip_parte_mb * MB)
    try:
        with zipfile.ZipFile(flusso, mode="w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as archivio:
            for v in voci:
                with archivio.open(v["nome_zip"], mode="w", force_zip64=True) as dentro:
                    if v.get("sha256"):
                        v["dimensione"] = verifica_impronta_sync(
                            v["bucket"], v["chiave"], v["sha256"], v.get("dimensione"),
                            client=s3, consumatore=dentro.write)
                    else:  # IPdA senza impronta nel manifesto (PdA precedenti): dichiarato
                        h = hashlib.sha256()
                        letti = 0
                        for blocco in _blocchi(s3, v["bucket"], v["chiave"]):
                            h.update(blocco)
                            letti += len(blocco)
                            dentro.write(blocco)
                        v["sha256"], v["dimensione"] = h.hexdigest(), letti
                        v["impronta_non_verificabile"] = True
            if riga["tipo"] == "MIGRAZIONE":
                archivio.writestr("migrazione.json", json.dumps(
                    _manifesto_migrazione(riga, aip, voci, versione_sincro, ora),
                    ensure_ascii=False, indent=2, default=str))
            archivio.writestr("contenuto_pdd.json", json.dumps({
                "pdd_id": str(riga["id"]), "aip_id": str(riga["aip_id"]), "tipo": riga["tipo"],
                "hash_pdd": impronta_pdd(voci),
                "file": [{"nome": v["nome_zip"], "sha256": v["sha256"],
                          "dimensione": v["dimensione"],
                          "impronta_verificata": not v.get("impronta_non_verificabile")}
                         for v in voci],
            }, ensure_ascii=False, indent=2))
        dimensione = flusso.completa()
    except BaseException:
        flusso.annulla()
        raise
    return {"chiave": chiave, "dimensione": dimensione, "sha256": flusso.impronta.hexdigest(),
            "voci": voci, "hash_pdd": impronta_pdd(voci)}


def _blocchi(s3: Any, bucket: str, chiave: str):
    from app.services.s3_client import blocchi_oggetto

    yield from blocchi_oggetto(bucket, chiave, s3)


def _problema(codice: str, dettaglio: str) -> dict[str, Any]:
    return ErroreApi(codice, dettaglio).corpo(instance=None)


async def _prendi_in_carico(adesso: datetime) -> dict[str, Any] | None:
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "UPDATE public.pacchetti_distribuzione p SET "
            " lease_fino_al = :lease, tentativi = p.tentativi + 1 "
            "WHERE p.id = (SELECT id FROM public.pacchetti_distribuzione "
            "  WHERE stato = 'IN_PREPARAZIONE' AND prossimo_tentativo <= :ora "
            "    AND (lease_fino_al IS NULL OR lease_fino_al < :ora) "
            "  ORDER BY prossimo_tentativo, id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING p.id, p.tenant_id, p.aip_id, p.tipo, p.motivo, p.opzioni, p.documenti, "
            " p.tentativi, p.durata_link_secondi"
        ), {"ora": adesso, "lease": adesso + timedelta(seconds=settings.pdd_lease_secondi)}
        )).mappings().first()
        if riga is None:
            return None
        aip = (await session.execute(text(
            "SELECT id::text AS id, urn, hash_sha256, hash_sha512, s3_bucket, s3_key, "
            "data_versamento, data_conservazione, scadenza_conservazione, anni_conservazione "
            "FROM public.aip WHERE id = :id AND tenant_id = :t"
        ), {"id": riga["aip_id"], "t": riga["tenant_id"]})).mappings().first()
    return {"riga": dict(riga), "aip": dict(aip) if aip else None}


@con_ambito_conservatore("PdD ZIP: prossimo pacchetto in coda di qualsiasi ente")
async def elabora_un_pdd(*, adesso: datetime | None = None) -> str | None:
    """Prepara un PdD ZIP in attesa. Restituisce l'esito (PRONTO, ERRORE,
    RIPROVA) o None se non c'era nulla da fare."""
    ora = adesso or datetime.now(UTC)
    preso = await _prendi_in_carico(ora)
    if preso is None:
        return None
    riga, aip = preso["riga"], preso["aip"]
    pdd_id = riga["id"]
    if aip is None:  # PdA non più leggibile per l'ente: errore definitivo
        await _chiudi(pdd_id, "ERRORE", errore=_problema("DST-404", "Pacchetto non trovato."))
        return "ERRORE"
    try:
        risultato = await asyncio.to_thread(costruisci_zip_sync, riga, aip, adesso=ora)
    except ImprontaNonCorrispondente as exc:
        logger.error("pdd_impronta_non_corrispondente", pdd_id=str(pdd_id), chiave=exc.chiave,
                     attesa=exc.attesa, calcolata=exc.calcolata)
        await _chiudi(pdd_id, "ERRORE", errore=_problema(
            "DST-502", "Un file del pacchetto non corrisponde all'impronta registrata."))
        return "ERRORE"
    except Exception as exc:  # noqa: BLE001 — errore tecnico: si ritenta con la lista costante
        ritardi = list(settings.pdd_ritardi_secondi)
        tentativi = int(riga["tentativi"])
        logger.warning("pdd_zip_non_riuscito", pdd_id=str(pdd_id), tentativo=tentativi,
                       errore=type(exc).__name__, messaggio=str(exc)[:300])
        if tentativi > len(ritardi):
            await _chiudi(pdd_id, "ERRORE", errore=_problema(
                "DST-503", "Preparazione del pacchetto non riuscita dopo più tentativi."),
                ultimo_errore=f"{type(exc).__name__}: {exc}"[:500])
            return "ERRORE"
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.pacchetti_distribuzione SET lease_fino_al = NULL, "
                "prossimo_tentativo = :p, ultimo_errore = :e WHERE id = :id"
            ), {"p": ora + timedelta(seconds=ritardi[tentativi - 1]),
                "e": f"{type(exc).__name__}: {exc}"[:500], "id": pdd_id})
        return "RIPROVA"

    scade = ora + timedelta(hours=await validita_ore(riga["tenant_id"]))
    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.pacchetti_distribuzione SET stato = 'PRONTO', lease_fino_al = NULL, "
            "zip_bucket = :b, zip_key = :k, zip_dimensione = :d, zip_sha256 = :h, "
            "hash_pdd = :hp, documenti = CAST(:doc AS jsonb), pronto_il = :ora, scade_il = :s, "
            "ultimo_errore = NULL WHERE id = :id AND stato = 'IN_PREPARAZIONE'"
        ), {"b": settings.s3_bucket_pdd, "k": risultato["chiave"], "d": risultato["dimensione"],
            "h": risultato["sha256"], "hp": risultato["hash_pdd"],
            "doc": json.dumps(risultato["voci"], default=str), "ora": ora, "s": scade,
            "id": pdd_id})
        await accoda_evento(session, topic=settings.kafka_topic_pdd_pronto, chiave=str(pdd_id),
                            tenant_id=str(riga["tenant_id"]), payload={
                                "evento": "pdd.pronto", "pdd_id": str(pdd_id),
                                "tenant_id": str(riga["tenant_id"]),
                                "aip_id": str(riga["aip_id"]), "tipo": riga["tipo"],
                                "scade_il": scade.isoformat(),
                            })
    logger.info("pdd_zip_pronto", pdd_id=str(pdd_id), byte=risultato["dimensione"])
    return "PRONTO"


async def _chiudi(pdd_id: uuid.UUID, stato: str, *, errore: dict[str, Any],
                  ultimo_errore: str | None = None) -> None:
    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.pacchetti_distribuzione SET stato = :s, lease_fino_al = NULL, "
            "errore = CAST(:e AS jsonb), ultimo_errore = COALESCE(:u, ultimo_errore) "
            "WHERE id = :id"
        ), {"s": stato, "e": json.dumps(errore), "u": ultimo_errore, "id": pdd_id})


async def validita_ore(tenant_id: Any) -> int:
    """R07 (D18): ore di validità del PdD ZIP dell'ente (``PDD_VALIDITA_ORE`` della
    console, 72 h, fino a 30 giorni), mai oltre il tetto tecnico del bucket
    (``PDD_ZIP_VALIDITA_ORE``)."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import PDD_VALIDITA_ORE

    ore = int((await parametri([PDD_VALIDITA_ORE], tenant_id))[PDD_VALIDITA_ORE])
    return max(1, min(ore, settings.pdd_zip_validita_ore))


@con_ambito_conservatore("PdD ZIP: pulizia dei pacchetti scaduti di tutti gli enti")
async def pulisci_scaduti(*, adesso: datetime | None = None, limite: int = 100) -> int:
    """PdD PRONTO scaduti → SCADUTO, con eliminazione dello ZIP temporaneo."""
    ora = adesso or datetime.now(UTC)
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT id, zip_bucket, zip_key FROM public.pacchetti_distribuzione "
            "WHERE stato = 'PRONTO' AND scade_il <= :ora ORDER BY scade_il LIMIT :n "
            "FOR UPDATE SKIP LOCKED"
        ), {"ora": ora, "n": limite})).all()
        for pid, bucket, chiave in righe:
            if chiave:
                await asyncio.to_thread(_get_s3().delete_object, Bucket=bucket, Key=chiave)
            await session.execute(text(
                "UPDATE public.pacchetti_distribuzione SET stato = 'SCADUTO' WHERE id = :id"
            ), {"id": pid})
    return len(righe)


async def ciclo_pdd(fine: asyncio.Event, intervallo: float = 5.0) -> None:
    """Ciclo continuo: prepara i PdD in attesa e pulisce gli scaduti."""
    while not fine.is_set():
        lavorato = False
        try:
            lavorato = await elabora_un_pdd() is not None
            await pulisci_scaduti()
        except Exception as exc:  # noqa: BLE001 — il ciclo non muore
            logger.error("pdd_ciclo_errore", errore=type(exc).__name__, messaggio=str(exc)[:300])
        if not lavorato:
            try:
                await asyncio.wait_for(fine.wait(), timeout=intervallo)
            except TimeoutError:
                pass
