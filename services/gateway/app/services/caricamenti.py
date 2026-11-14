# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Sessioni di caricamento dei file grandi (contratto C1) e versamento per riferimento.

Servizio: gateway. Riferimenti: piano R03 §3.1 e §4.2 («nessun file intero in
memoria», impronta calcolata durante il trasferimento); contratti condivisi
dell'ondata B, C1; REGOLE_SVILUPPO §8.15, §9.1, §10.3, §12.1, §12.3, §12.9,
§14.4, §18, §22.3.

Ciclo di vita di una sessione (tabella ``public.caricamenti``):

  APERTO ──completa──▶ IN_VERIFICA ──verificatore──▶ COMPLETO ──versamento──▶ (usata da un PdV)
     │                       └──────────────────────▶ ERRORE
     ├──annulla──▶ ANNULLATO          (APERTO/COMPLETO non usata, oltre ``scade_il``) ─▶ SCADUTO

  - apertura: si fissano dimensione delle parti e numero di parti, si apre un
    caricamento multipart S3 nello staging (``caricamenti/{tenant}/{id}/dati``
    del bucket ``S3_BUCKET_PDV``, mai il bucket conservativo);
  - parti: arrivano in parallelo e in qualsiasi ordine; ognuna è letta al più
    per la sua dimensione (una parte in memoria), se ne calcola lo SHA-256 e
    la si scrive come parte del multipart. La stessa parte ripetuta con lo
    stesso contenuto è idempotente; con contenuto diverso è un conflitto e lo
    storage non viene toccato (la riga della parte è «prenotata» prima di
    scrivere);
  - completamento: controlla che tutte le parti ci siano e passa a
    IN_VERIFICA; la verifica avviene FUORI dalla richiesta HTTP (lavoratore
    nel processo del gateway o processo dedicato, con lease, REGOLE §12.9):
    assemblaggio del multipart, rilettura in streaming con le impronte di
    TUTTI gli algoritmi ammessi, testa per il formato, confronto con
    l'impronta dichiarata; evento ``caricamento.completato`` o
    ``caricamento.errore`` in outbox;
  - versamento per riferimento: ``POST /api/v1/versamento/ud`` in JSON cita le
    sessioni COMPLETE; il caso d'uso unico (``versamento_service.versa``)
    riceve ``FileGiaCaricato`` e, nella transazione del PdV, la sessione è
    segnata come usata (una sola volta: UPL-008);
  - pulizia: sessioni scadute, annullate o in errore perdono i file nello
    staging (multipart annullato, oggetto eliminato). Gli oggetti usati da un
    PdV non si toccano: appartengono al PdV.

Uso come processi dedicati: ``python -m app.services.caricamenti verifica``,
``python -m app.services.caricamenti pulizia [--prova]``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import settings
from app.models.versamento import IndiceUD
from app.services.outbox import accoda_evento
from app.services.versamento_service import (
    _IDEMPOTENCY_KEY,
    MB,
    AzioneInTransazione,
    Canale,
    EsitoVersamento,
    FileGiaCaricato,
    IdentitaVersante,
    RichiestaVersamento,
    _assicura_bucket,
    _bucket_pdv,
    _s3_client,
    identita_da_utente,
    prefisso_caricamenti,
    versa,
)
from libs.shared.db import get_async_session
from libs.shared.db.ambito import con_ambito_conservatore
from libs.shared.errori import ErroreApi
from libs.shared.formati.firma_binaria import rileva_formato
from libs.shared.impronte import ALGORITMI_IMPRONTA, funzione_impronta
from libs.shared.security.input_validation import sanitize_string, validate_file_extension
from libs.shared.storage.flusso import BYTE_TESTA_PREDEFINITI, elimina_oggetti

logger = structlog.get_logger(__name__)

#: Topic degli eventi (contratto C3: il consumer di notification li proietta).
TOPIC_COMPLETATO = "caricamento.completato"
TOPIC_ERRORE = "caricamento.errore"
#: Limite di S3 sul numero di parti di un caricamento multipart.
MAX_PARTI_S3 = 10_000
#: Blocco di lettura dell'oggetto assemblato durante la verifica.
BLOCCO_VERIFICA = 1024 * 1024
#: Parti elencate al più in un errore UPL-005 (le altre sono contate).
MAX_PARTI_ELENCATE = 100

APERTO, IN_VERIFICA, COMPLETO = "APERTO", "IN_VERIFICA", "COMPLETO"
SCADUTO, ANNULLATO, ERRORE = "SCADUTO", "ANNULLATO", "ERRORE"
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_IMPRONTA_PARTE = re.compile(r"^sha256=([0-9a-fA-F]{64})$")


# ── Modelli e funzioni pure ─────────────────────────────────────────

class ImprontaDichiarata(BaseModel):
    """Impronta del file dichiarata dal client (confrontata con quella calcolata)."""

    model_config = ConfigDict(extra="forbid")
    algoritmo: str = Field(..., description="SHA-256, SHA-512 o SHA3-256 (Allegato 6 LG AgID)")
    valore: str = Field(..., min_length=64, max_length=128, description="Impronta esadecimale")


class AperturaCaricamento(BaseModel):
    """Corpo di ``POST /api/v1/caricamenti`` (contratto C1)."""

    model_config = ConfigDict(extra="forbid")
    nome_file: str = Field(..., min_length=1, max_length=255,
                           description="Nome del file (informativo; nel versamento vale l'indice)")
    dimensione: int = Field(..., ge=1, description="Dimensione esatta del file in byte")
    mime_dichiarato: str | None = Field(None, max_length=255,
                                        description="Tipo dichiarato (il formato si rileva dal "
                                                    "contenuto, REGOLE §8.15)")
    impronta: ImprontaDichiarata | None = Field(
        None, description="Impronta del file: se presente si confronta con quella calcolata")


def numero_parti(dimensione: int, dimensione_parte: int) -> int:
    """Parti necessarie per ``dimensione`` byte (almeno una)."""
    return max(1, -(-dimensione // dimensione_parte))


def dimensione_attesa(numero: int, dimensione: int, dimensione_parte: int) -> int:
    """Byte attesi per la parte ``numero`` (da 1): l'ultima porta il resto."""
    inizio = (numero - 1) * dimensione_parte
    return max(0, min(dimensione_parte, dimensione - inizio))


def impronta_apertura(richiesta: AperturaCaricamento, dimensione_parte: int) -> str:
    """Impronta della richiesta d'apertura: stessa Idempotency-Key + richiesta diversa = 409."""
    dati = richiesta.model_dump(mode="json")
    dati["dimensione_parte"] = dimensione_parte
    canonico = json.dumps(dati, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def controlla_apertura(richiesta: AperturaCaricamento, dimensione_parte: int) -> int:
    """Controlli puri dell'apertura; restituisce il numero di parti."""
    try:
        sanitize_string(richiesta.nome_file, max_length=255)
        validate_file_extension(richiesta.nome_file)
    except HTTPException as exc:
        dettaglio: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        raise ErroreApi(str(dettaglio.get("type", "SEC-001")),
                        str(dettaglio.get("title") or "Nome file non valido")) from None
    massimo = settings.caricamenti_max_dimensione_mb * MB
    if richiesta.dimensione > massimo:
        raise ErroreApi("UPL-001",
                        f"Dimensione massima: {settings.caricamenti_max_dimensione_mb} MB",
                        dettagli={"massimo_byte": massimo})
    if richiesta.impronta is not None:
        funzione = funzione_impronta(richiesta.impronta.algoritmo)
        if funzione is None:
            raise ErroreApi("UPL-400", f"Algoritmo d'impronta non ammesso: "
                                       f"{richiesta.impronta.algoritmo}",
                            dettagli={"ammessi": sorted(ALGORITMI_IMPRONTA)})
        lunghezza = funzione().digest_size * 2
        if len(richiesta.impronta.valore) != lunghezza or not _HEX.match(richiesta.impronta.valore):
            raise ErroreApi("UPL-400", f"Impronta {richiesta.impronta.algoritmo} non valida: "
                                       f"attesi {lunghezza} caratteri esadecimali")
    parti = numero_parti(richiesta.dimensione, dimensione_parte)
    if parti > MAX_PARTI_S3:
        raise ErroreApi("UPL-001", f"Il file richiederebbe {parti} parti (massimo {MAX_PARTI_S3})")
    return parti


def impronta_parte_dichiarata(valore: str | None) -> str | None:
    """Valore di ``X-Impronta-Parte: sha256=<hex>`` (minuscolo); UPL-400 se malformato."""
    if valore is None or not valore.strip():
        return None
    trovato = _IMPRONTA_PARTE.match(valore.strip())
    if trovato is None:
        raise ErroreApi("UPL-400", "X-Impronta-Parte deve essere nella forma sha256=<64 cifre "
                                   "esadecimali>")
    return trovato.group(1).lower()


# ── Sessione letta dal database ─────────────────────────────────────

_COLONNE = (
    "id, tenant_id, utente_id, canale, chiave_idempotenza, impronta_richiesta, nome_file, "
    "dimensione, mime_dichiarato, impronta_dichiarata, dimensione_parte, numero_parti, stato, "
    "bucket, chiave_oggetto, upload_id, impronte, testa, formato_rilevato, errore, scade_il, "
    "detentore, lease_scade_il, tentativi_verifica, pdv_id, consumato_il, completato_il, "
    "storage_eliminato_il, creato_il"
)


@dataclass
class Sessione:
    """Riga di ``public.caricamenti``."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    utente_id: uuid.UUID
    canale: str
    chiave_idempotenza: str | None
    impronta_richiesta: str
    nome_file: str
    dimensione: int
    mime_dichiarato: str | None
    impronta_dichiarata: dict[str, Any] | None
    dimensione_parte: int
    numero_parti: int
    stato: str
    bucket: str
    chiave_oggetto: str
    upload_id: str | None
    impronte: dict[str, Any] | None
    testa: bytes | None
    formato_rilevato: str | None
    errore: dict[str, Any] | None
    scade_il: datetime
    detentore: str | None
    lease_scade_il: datetime | None
    tentativi_verifica: int
    pdv_id: uuid.UUID | None
    consumato_il: datetime | None
    completato_il: datetime | None
    storage_eliminato_il: datetime | None
    creato_il: datetime
    parti_ricevute: list[int] = field(default_factory=list)

    @classmethod
    def da_riga(cls, riga: Mapping[Any, Any]) -> Sessione:
        valori = dict(riga)
        for nome in ("impronta_dichiarata", "impronte", "errore"):
            if isinstance(valori.get(nome), str):
                valori[nome] = json.loads(valori[nome])
        if valori.get("testa") is not None:
            valori["testa"] = bytes(valori["testa"])
        return cls(**{k: valori[k] for k in _COLONNE.replace(" ", "").split(",")})

    def stato_effettivo(self, adesso: datetime) -> str:
        """Lo stato con la scadenza controllata a ogni richiesta (REGOLE §11.9)."""
        if self.stato in (APERTO, COMPLETO) and self.pdv_id is None and adesso >= self.scade_il:
            return SCADUTO
        return self.stato


def _uuid_o_404(valore: str | uuid.UUID) -> uuid.UUID:
    try:
        return valore if isinstance(valore, uuid.UUID) else uuid.UUID(str(valore))
    except ValueError:
        raise ErroreApi("UPL-404", "Sessione di caricamento non trovata") from None


def _non_disponibile(exc: BaseException, evento: str, **contesto: Any) -> ErroreApi:
    logger.error(evento, errore=type(exc).__name__, messaggio=str(exc)[:300], **contesto)
    return ErroreApi("UPL-503")


async def _leggi(session: Any, tenant: uuid.UUID, caricamento_id: uuid.UUID, *,
                 per_aggiornare: bool = False) -> Sessione:
    """La sessione del cliente; UPL-404 se non esiste o è di un altro cliente (§14.4)."""
    blocco = " FOR UPDATE" if per_aggiornare else ""
    riga = (await session.execute(
        text(f"SELECT {_COLONNE} FROM public.caricamenti "  # noqa: S608 — colonne costanti
             f"WHERE id = :id AND tenant_id = :t{blocco}"),
        {"id": caricamento_id, "t": tenant},
    )).mappings().first()
    if riga is None:
        raise ErroreApi("UPL-404", "Sessione di caricamento non trovata")
    return Sessione.da_riga(riga)


async def _parti_ricevute(session: Any, caricamento_id: uuid.UUID) -> list[int]:
    righe = await session.execute(
        text("SELECT numero FROM public.caricamenti_parti "
             "WHERE caricamento_id = :id AND etag IS NOT NULL ORDER BY numero"),
        {"id": caricamento_id},
    )
    return [int(r[0]) for r in righe.fetchall()]


def _rifiuto_stato(s: Sessione, adesso: datetime, operazione: str) -> ErroreApi:
    stato = s.stato_effettivo(adesso)
    if stato in (SCADUTO, ANNULLATO):
        return ErroreApi("UPL-410", f"Sessione {stato.lower()}: {operazione} non possibile",
                         dettagli={"stato": stato})
    return ErroreApi("UPL-409", f"Stato {stato}: {operazione} non possibile",
                     dettagli={"stato": stato})


# ── Apertura ────────────────────────────────────────────────────────

async def apri(identita: IdentitaVersante, richiesta: AperturaCaricamento, *,
               idempotency_key: str | None, adesso: datetime) -> tuple[Sessione, bool]:
    """Apre una sessione (o restituisce quella della stessa Idempotency-Key).

    Restituisce ``(sessione, nuova)``. La dimensione delle parti la sceglie il
    servizio (impostazione ``caricamenti_dimensione_parte_mb``)."""
    if idempotency_key is not None and not _IDEMPOTENCY_KEY.match(idempotency_key):
        raise ErroreApi("VRS-122", "Da 8 a 200 caratteri fra lettere, cifre e . _ : -")
    parte = settings.caricamenti_dimensione_parte_mb * MB
    parti = controlla_apertura(richiesta, parte)
    impronta = impronta_apertura(richiesta, parte)
    tenant = identita.tenant_id

    if idempotency_key is not None:
        esistente = await _per_chiave(tenant, idempotency_key)
        if esistente is not None:
            return _stessa_richiesta(esistente, impronta), False

    caricamento_id = uuid.uuid4()
    bucket = _bucket_pdv()
    chiave = f"{prefisso_caricamenti(tenant)}{caricamento_id}/dati"
    s3 = _s3_client()
    try:
        await asyncio.to_thread(_assicura_bucket, s3, bucket)
        risposta = await asyncio.to_thread(
            s3.create_multipart_upload, Bucket=bucket, Key=chiave,
            ContentType="application/octet-stream")
    except Exception as exc:  # noqa: BLE001 — storage non raggiungibile: errore temporaneo
        raise _non_disponibile(exc, "caricamento_apertura_storage_fallita") from None
    upload_id = str(risposta["UploadId"])
    scade = adesso + timedelta(hours=settings.caricamenti_scadenza_ore)
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "INSERT INTO public.caricamenti (id, tenant_id, utente_id, canale, "  # noqa: S608 — colonne costanti
                " chiave_idempotenza, impronta_richiesta, nome_file, dimensione, mime_dichiarato, "
                " impronta_dichiarata, dimensione_parte, numero_parti, stato, bucket, "
                " chiave_oggetto, upload_id, scade_il, creato_il, aggiornato_il) "
                "VALUES (:id, :t, :u, :canale, :k, :ir, :nome, :dim, :mime, "
                " CAST(:impronta AS jsonb), :parte, :parti, 'APERTO', :bucket, :chiave, :upload, "
                " :scade, :adesso, :adesso) "
                f"RETURNING {_COLONNE}"  # noqa: S608 — colonne costanti
            ), {
                "id": caricamento_id, "t": tenant, "u": identita.utente_id,
                "canale": identita.canale.value, "k": idempotency_key, "ir": impronta,
                "nome": richiesta.nome_file, "dim": richiesta.dimensione,
                "mime": richiesta.mime_dichiarato,
                "impronta": json.dumps(richiesta.impronta.model_dump()) if richiesta.impronta
                else None,
                "parte": parte, "parti": parti, "bucket": bucket, "chiave": chiave,
                "upload": upload_id, "scade": scade, "adesso": adesso,
            })).mappings().one()
    except IntegrityError:
        # Corsa con un'altra apertura con la stessa chiave: vale quella registrata
        await _annulla_multipart(s3, bucket, chiave, upload_id)
        esistente = await _per_chiave(tenant, idempotency_key or "")
        if esistente is None:
            raise ErroreApi("UPL-503") from None
        return _stessa_richiesta(esistente, impronta), False
    except SQLAlchemyError as exc:
        await _annulla_multipart(s3, bucket, chiave, upload_id)
        raise _non_disponibile(exc, "caricamento_apertura_db_fallita") from None
    sessione = Sessione.da_riga(riga)
    logger.info("caricamento_aperto", caricamento_id=str(caricamento_id), tenant_id=str(tenant),
                dimensione=richiesta.dimensione, parti=parti, canale=identita.canale.value)
    return sessione, True


async def _per_chiave(tenant: uuid.UUID, chiave: str) -> Sessione | None:
    try:
        async with get_async_session() as session:
            riga = (await session.execute(
                text(f"SELECT {_COLONNE} FROM public.caricamenti "  # noqa: S608 — colonne costanti
                     "WHERE tenant_id = :t AND chiave_idempotenza = :k"),
                {"t": tenant, "k": chiave},
            )).mappings().first()
            if riga is None:
                return None
            sessione = Sessione.da_riga(riga)
            sessione.parti_ricevute = await _parti_ricevute(session, sessione.id)
            return sessione
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_lettura_db_fallita") from None


def _stessa_richiesta(esistente: Sessione, impronta: str) -> Sessione:
    if esistente.impronta_richiesta != impronta:
        raise ErroreApi("UPL-007", "Usare una nuova Idempotency-Key per un file diverso.",
                        dettagli={"caricamento_id": str(esistente.id)})
    return esistente


# ── Lettura ─────────────────────────────────────────────────────────

async def leggi(tenant: uuid.UUID, caricamento_id: str | uuid.UUID) -> Sessione:
    """Sessione del cliente con l'elenco delle parti ricevute."""
    cid = _uuid_o_404(caricamento_id)
    try:
        async with get_async_session() as session:
            sessione = await _leggi(session, tenant, cid)
            if sessione.stato == COMPLETO:
                sessione.parti_ricevute = list(range(1, sessione.numero_parti + 1))
            elif sessione.storage_eliminato_il is None:
                sessione.parti_ricevute = await _parti_ricevute(session, cid)
            return sessione
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_lettura_db_fallita") from None


# ── Parti ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParteRicevuta:
    numero: int
    dimensione: int
    ricevute: list[int]


async def leggi_parte(blocchi: AsyncIterator[bytes], attesa: int) -> bytes:
    """Legge esattamente ``attesa`` byte; oltre si ferma subito (UPL-002)."""
    dati = bytearray()
    async for blocco in blocchi:
        if not blocco:
            continue
        if len(dati) + len(blocco) > attesa:
            raise ErroreApi("UPL-002", f"La parte supera i {attesa} byte attesi",
                            dettagli={"attesi": attesa})
        dati.extend(blocco)
    if len(dati) != attesa:
        raise ErroreApi("UPL-002", f"Ricevuti {len(dati)} byte, attesi {attesa}",
                        dettagli={"attesi": attesa, "ricevuti": len(dati)})
    return bytes(dati)


async def ricevi_parte(tenant: uuid.UUID, caricamento_id: str | uuid.UUID, numero: int,
                       blocchi: AsyncIterator[bytes], *, adesso: datetime,
                       lunghezza_annunciata: int | None = None,
                       impronta_dichiarata: str | None = None) -> ParteRicevuta:
    """Riceve la parte ``numero`` (contratto C1: parallele, in qualsiasi ordine, idempotenti)."""
    cid = _uuid_o_404(caricamento_id)
    try:
        async with get_async_session() as session:
            s = await _leggi(session, tenant, cid)
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_lettura_db_fallita") from None
    if s.stato_effettivo(adesso) != APERTO:
        raise _rifiuto_stato(s, adesso, "invio di una parte")
    if not 1 <= numero <= s.numero_parti:
        raise ErroreApi("UPL-002", f"Parte {numero} fuori intervallo (1-{s.numero_parti})",
                        dettagli={"numero_parti": s.numero_parti})
    attesa = dimensione_attesa(numero, s.dimensione, s.dimensione_parte)
    if lunghezza_annunciata is not None and lunghezza_annunciata != attesa:
        raise ErroreApi("UPL-002", f"Parte {numero}: attesi {attesa} byte, annunciati "
                                   f"{lunghezza_annunciata}",
                        dettagli={"attesi": attesa, "annunciati": lunghezza_annunciata})
    dati = await leggi_parte(blocchi, attesa)
    sha = hashlib.sha256(dati).hexdigest()
    if impronta_dichiarata is not None and impronta_dichiarata != sha:
        raise ErroreApi("UPL-003", f"Parte {numero}: impronta dichiarata diversa da quella "
                                   "dei byte ricevuti")

    # Prenotazione della parte PRIMA di scrivere sullo storage: una parte diversa
    # con lo stesso numero non sovrascrive mai quella già accettata (ETag registrato).
    # R03 f1 (REV-07): una prenotazione SENZA ETag non è una parte ricevuta:
    #   - se la scrittura sullo storage fallisce, la prenotazione si elimina subito
    #     (il reinvio, anche con byte diversi, riparte da capo);
    #   - se il processo muore fra prenotazione e scrittura, la prenotazione più
    #     vecchia di ``CARICAMENTI_PRENOTAZIONE_PARTE_SECONDI`` si sostituisce;
    #   - una prenotazione recente con byte diversi è un invio concorrente in
    #     corso: 503 UPL-503 ritentabile, non il 409 di una parte già ricevuta.
    try:
        async with get_async_session() as session:
            await _prenota_parte(session, cid, tenant, numero, attesa, sha, adesso)
            gia = (await session.execute(text(
                "SELECT etag FROM public.caricamenti_parti "
                "WHERE caricamento_id = :id AND numero = :n"), {"id": cid, "n": numero},
            )).mappings().one()
            if gia["etag"] is not None:
                return ParteRicevuta(numero, attesa, await _parti_ricevute(session, cid))
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_parte_db_fallita") from None

    s3 = _s3_client()
    try:
        risposta = await asyncio.to_thread(
            s3.upload_part, Bucket=s.bucket, Key=s.chiave_oggetto, UploadId=s.upload_id,
            PartNumber=numero, Body=dati)
    except Exception as exc:  # noqa: BLE001 — storage: sessione cambiata o errore temporaneo
        # REV-07: la parte NON è stata ricevuta: via la prenotazione (solo la nostra,
        # senza ETag), così il reinvio non trova un'impronta che non esiste
        await _libera_prenotazione(cid, numero, sha)
        try:
            async with get_async_session() as session:
                attuale = await _leggi(session, tenant, cid)
        except SQLAlchemyError:
            attuale = s
        if attuale.stato_effettivo(adesso) != APERTO:
            raise _rifiuto_stato(attuale, adesso, "invio di una parte") from None
        raise _non_disponibile(exc, "caricamento_parte_storage_fallita",
                               caricamento_id=str(cid), parte=numero) from None
    try:
        async with get_async_session() as session:
            registrata = await session.execute(text(
                "UPDATE public.caricamenti_parti SET etag = :etag "
                "WHERE caricamento_id = :id AND numero = :n AND sha256 = :sha"
            ), {"etag": str(risposta["ETag"]), "id": cid, "n": numero, "sha": sha})
            ricevute = await _parti_ricevute(session, cid)
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_parte_db_fallita") from None
    if registrata.rowcount != 1:
        # La prenotazione è stata sostituita nel frattempo (invio concorrente della
        # stessa parte con byte diversi): non si dichiara ricevuta, si ritenta.
        logger.warning("caricamento_parte_prenotazione_persa", caricamento_id=str(cid),
                       parte=numero)
        raise ErroreApi("UPL-503", f"Parte {numero} inviata contemporaneamente da un'altra "
                                   "richiesta: ripetere l'invio")
    return ParteRicevuta(numero, attesa, ricevute)


async def _prenota_parte(session: Any, cid: uuid.UUID, tenant: uuid.UUID, numero: int,
                         attesa: int, sha: str, adesso: datetime) -> None:
    """Prenota la parte (riga senza ETag) o conferma la prenotazione esistente.

    UPL-004 se la parte è GIÀ RICEVUTA (ETag registrato) con byte diversi; una
    prenotazione senza ETag e più vecchia del limite si sostituisce; una recente
    con byte diversi è un invio concorrente (UPL-503 ritentabile)."""
    inserita = (await session.execute(text(
        "INSERT INTO public.caricamenti_parti (caricamento_id, numero, tenant_id, "
        " dimensione, sha256, ricevuta_il) VALUES (:id, :n, :t, :dim, :sha, :adesso) "
        "ON CONFLICT (caricamento_id, numero) DO NOTHING RETURNING numero"
    ), {"id": cid, "n": numero, "t": tenant, "dim": attesa, "sha": sha,
        "adesso": adesso})).first()
    if inserita is not None:
        return
    gia = (await session.execute(text(
        "SELECT sha256, etag, ricevuta_il FROM public.caricamenti_parti "
        "WHERE caricamento_id = :id AND numero = :n FOR UPDATE"), {"id": cid, "n": numero},
    )).mappings().one()
    if gia["sha256"] == sha:
        return
    if gia["etag"] is not None:
        raise ErroreApi("UPL-004", f"Parte {numero} già ricevuta con un contenuto diverso",
                        dettagli={"parte": numero})
    soglia = adesso - timedelta(seconds=settings.caricamenti_prenotazione_parte_secondi)
    if gia["ricevuta_il"] is not None and gia["ricevuta_il"] > soglia:
        raise ErroreApi("UPL-503", f"Parte {numero} in ricezione da un'altra richiesta con "
                                   "byte diversi: ripetere l'invio tra poco")
    await session.execute(text(
        "UPDATE public.caricamenti_parti SET sha256 = :sha, dimensione = :dim, "
        " ricevuta_il = :adesso WHERE caricamento_id = :id AND numero = :n AND etag IS NULL"
    ), {"sha": sha, "dim": attesa, "adesso": adesso, "id": cid, "n": numero})
    logger.info("caricamento_parte_prenotazione_sostituita", caricamento_id=str(cid),
                parte=numero)


async def _libera_prenotazione(cid: uuid.UUID, numero: int, sha: str) -> None:
    """Elimina la prenotazione SENZA ETag di questa richiesta (scrittura non riuscita)."""
    try:
        async with get_async_session() as session:
            await session.execute(text(
                "DELETE FROM public.caricamenti_parti WHERE caricamento_id = :id "
                "AND numero = :n AND sha256 = :sha AND etag IS NULL"
            ), {"id": cid, "n": numero, "sha": sha})
    except SQLAlchemyError as exc:
        # Resta la sostituzione dopo CARICAMENTI_PRENOTAZIONE_PARTE_SECONDI
        logger.warning("caricamento_prenotazione_non_liberata", caricamento_id=str(cid),
                       parte=numero, errore=type(exc).__name__)


# ── Completamento e annullamento ────────────────────────────────────

_SVEGLIA: asyncio.Event | None = None


def segnala_verificatore() -> None:
    """Sveglia il lavoratore di questo processo (gli altri lo trovano al giro successivo)."""
    if _SVEGLIA is not None:
        _SVEGLIA.set()


async def completa(tenant: uuid.UUID, caricamento_id: str | uuid.UUID, *,
                   adesso: datetime) -> Sessione:
    """Chiede la verifica: tutte le parti devono esserci (UPL-005). Idempotente."""
    cid = _uuid_o_404(caricamento_id)
    try:
        async with get_async_session() as session:
            s = await _leggi(session, tenant, cid, per_aggiornare=True)
            stato = s.stato_effettivo(adesso)
            if stato in (COMPLETO, IN_VERIFICA):
                s.stato = stato
                return s
            if stato != APERTO:
                raise _rifiuto_stato(s, adesso, "completamento")
            ricevute = await _parti_ricevute(session, cid)
            mancanti = sorted(set(range(1, s.numero_parti + 1)) - set(ricevute))
            if mancanti:
                raise ErroreApi(
                    "UPL-005", f"Mancano {len(mancanti)} parti su {s.numero_parti}",
                    dettagli={"parti_mancanti": mancanti[:MAX_PARTI_ELENCATE],
                              "numero_mancanti": len(mancanti)})
            await session.execute(text(
                "UPDATE public.caricamenti SET stato = 'IN_VERIFICA', detentore = NULL, "
                "lease_scade_il = NULL, aggiornato_il = :adesso WHERE id = :id"
            ), {"id": cid, "adesso": adesso})
            s.stato, s.parti_ricevute = IN_VERIFICA, ricevute
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_completa_db_fallita") from None
    segnala_verificatore()
    return s


async def annulla(tenant: uuid.UUID, caricamento_id: str | uuid.UUID, *,
                  adesso: datetime) -> None:
    """Annulla la sessione ed elimina i suoi file (idempotente); mai se usata da un PdV."""
    cid = _uuid_o_404(caricamento_id)
    try:
        async with get_async_session() as session:
            s = await _leggi(session, tenant, cid, per_aggiornare=True)
            if s.pdv_id is not None:
                raise ErroreApi("UPL-409", "La sessione è usata da un versamento: i suoi file "
                                           "appartengono al PdV", dettagli={"stato": s.stato})
            if s.stato in (ANNULLATO, SCADUTO):
                return
            await session.execute(text(
                "UPDATE public.caricamenti SET stato = 'ANNULLATO', detentore = NULL, "
                "lease_scade_il = NULL, aggiornato_il = :adesso WHERE id = :id"
            ), {"id": cid, "adesso": adesso})
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_annulla_db_fallita") from None
    logger.info("caricamento_annullato", caricamento_id=str(cid), tenant_id=str(tenant))
    # Eliminazione subito (se fallisce resta alla pulizia periodica)
    await _elimina_storage(s, adesso)


# ── Versamento per riferimento ──────────────────────────────────────

async def fonti_da_caricamenti(
    tenant: uuid.UUID, file: Mapping[str, str], *, adesso: datetime,
) -> tuple[list[FileGiaCaricato], AzioneInTransazione]:
    """Fonti del caso d'uso per ``{nome_file: caricamento_id}`` e l'azione che, nella
    transazione del PdV, segna le sessioni come usate (una volta sola: UPL-008)."""
    ids: dict[str, uuid.UUID] = {}
    for nome, valore in file.items():
        ids[nome] = _uuid_o_404(valore)
    if len(set(ids.values())) != len(ids):
        raise ErroreApi("UPL-008", "La stessa sessione è citata per più file")
    try:
        async with get_async_session() as session:
            righe = (await session.execute(
                text(f"SELECT {_COLONNE} FROM public.caricamenti "  # noqa: S608 — colonne costanti
                     "WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"),
                {"t": tenant, "ids": [str(i) for i in ids.values()]},
            )).mappings().all()
    except SQLAlchemyError as exc:
        raise _non_disponibile(exc, "caricamento_lettura_db_fallita") from None
    per_id = {r["id"]: Sessione.da_riga(r) for r in righe}
    fonti: list[FileGiaCaricato] = []
    for nome, cid in ids.items():
        s = per_id.get(cid)
        if s is None:
            raise ErroreApi("UPL-404", f"Caricamento del file {nome!r} non trovato")
        stato = s.stato_effettivo(adesso)
        if stato in (SCADUTO, ANNULLATO):
            raise ErroreApi("UPL-410", f"Caricamento del file {nome!r} {stato.lower()}",
                            dettagli={"caricamento_id": str(cid), "stato": stato})
        if stato != COMPLETO:
            raise ErroreApi("UPL-009", f"Caricamento del file {nome!r} nello stato {stato}",
                            dettagli={"caricamento_id": str(cid), "stato": stato})
        fonti.append(FileGiaCaricato(
            nome_file=nome, bucket=s.bucket, chiave=s.chiave_oggetto, dimensione=s.dimensione,
            impronte=dict(s.impronte or {}), testa=s.testa or b"", caricamento_id=str(cid),
            content_type=s.mime_dichiarato,
        ))
    elenco = [str(i) for i in ids.values()]

    async def segna_usate(sessione_db: Any, pdv_id: str) -> None:
        esito = await sessione_db.execute(text(
            "UPDATE public.caricamenti SET pdv_id = CAST(:pdv AS uuid), consumato_il = :adesso, "
            "aggiornato_il = :adesso WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[])) "
            "AND stato = 'COMPLETO' AND pdv_id IS NULL"
        ), {"pdv": pdv_id, "adesso": adesso, "t": tenant, "ids": elenco})
        if esito.rowcount != len(elenco):
            raise ErroreApi("UPL-008", "Una sessione citata è già usata da un altro versamento "
                                       "(o non è più completa)")

    return fonti, segna_usate


class VersamentoPerRiferimento(BaseModel):
    """Corpo JSON di ``POST /api/v1/versamento/ud`` per riferimento (contratto C1)."""

    model_config = ConfigDict(extra="forbid")
    indice: dict[str, Any] = Field(..., description="IndiceUD (stesso schema del multipart)")
    file: dict[str, str] = Field(..., min_length=1,
                                 description="nome_file dichiarato nell'indice → caricamento_id "
                                             "di una sessione COMPLETA")


def canale_da_utente(user: Mapping[str, Any], predefinito: Canale = Canale.REST) -> Canale:
    """Canale effettivo dell'identità sulle rotte REST delle sessioni e del
    versamento per riferimento (contratto C2): il canale del token di canale
    (es. SFTP), altrimenti ``predefinito``. Una sola regola per tutto il gateway:
    ``app.auth.canali.canale_effettivo`` (un claim ``canale`` in un JWT utente
    non cambia il canale; un token di canale non vale sulle rotte di altri canali)."""
    from app.auth.canali import canale_effettivo

    return Canale(canale_effettivo(dict(user), predefinito.value))


async def versa_per_riferimento(corpo: bytes, *, user: Mapping[str, Any],
                                idempotency_key: str | None,
                                adesso: datetime | None = None) -> EsitoVersamento:
    """Versamento JSON che cita sessioni COMPLETE: ``FileGiaCaricato`` → caso d'uso unico."""
    try:
        richiesta = VersamentoPerRiferimento.model_validate_json(corpo)
    except ValidationError as exc:
        raise ErroreApi("VAL-422", "Corpo atteso: {\"indice\": {...}, \"file\": {\"<nome>\": "
                                   "\"<caricamento_id>\"}}",
                        campi=[{"campo": ".".join(str(p) for p in e["loc"]),
                                "posizione": "body", "tipo": e["type"], "messaggio": e["msg"]}
                               for e in exc.errors()[:20]]) from None
    try:
        indice = IndiceUD.model_validate(richiesta.indice)
    except ValidationError as exc:
        raise ErroreApi("VRS-001", str(exc)[:2000]) from None
    quando = adesso or datetime.now(UTC)
    identita = identita_da_utente(user, canale_da_utente(user))
    fonti, segna_usate = await fonti_da_caricamenti(identita.tenant_id, richiesta.file,
                                                    adesso=quando)
    return await versa(RichiestaVersamento(
        indice=indice, fonti=fonti, identita=identita, idempotency_key=idempotency_key,
        adesso=quando, in_transazione=[segna_usate],
    ))


# ── Verifica fuori dalla richiesta HTTP ─────────────────────────────

def _detentore() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class _Letto:
    dimensione: int
    impronte: dict[str, str]
    testa: bytes


def _leggi_oggetto(s3: Any, bucket: str, chiave: str) -> _Letto:
    """Rilegge l'oggetto a blocchi calcolando tutte le impronte e la testa (in un thread)."""
    calcolatori = {nome: costruttore() for nome, costruttore in ALGORITMI_IMPRONTA.items()}
    testa = bytearray()
    letti = 0
    corpo = s3.get_object(Bucket=bucket, Key=chiave)["Body"]
    try:
        while True:
            blocco = corpo.read(BLOCCO_VERIFICA)
            if not blocco:
                break
            letti += len(blocco)
            for c in calcolatori.values():
                c.update(blocco)
            if len(testa) < BYTE_TESTA_PREDEFINITI:
                testa.extend(blocco[: BYTE_TESTA_PREDEFINITI - len(testa)])
    finally:
        corpo.close()
    return _Letto(letti, {n: c.hexdigest() for n, c in calcolatori.items()}, bytes(testa))


def _codice_s3(exc: BaseException) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


@con_ambito_conservatore("sessioni di caricamento: verifica delle sessioni di tutti gli enti")
async def verifica_in_sospeso(*, adesso: Callable[[], datetime] = lambda: datetime.now(UTC),
                              limite: int = 5) -> int:
    """Un giro del verificatore: prende in carico (lease) fino a ``limite`` sessioni
    IN_VERIFICA libere o con lease scaduto e le verifica. Restituisce quante."""
    detentore = _detentore()
    ora = adesso()
    scade = ora + timedelta(seconds=settings.caricamenti_lease_verifica_secondi)
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "UPDATE public.caricamenti SET detentore = :d, lease_scade_il = :scade, "  # noqa: S608 — colonne costanti
            " tentativi_verifica = tentativi_verifica + 1, aggiornato_il = :ora "
            "WHERE id IN (SELECT id FROM public.caricamenti WHERE stato = 'IN_VERIFICA' "
            " AND (lease_scade_il IS NULL OR lease_scade_il < :ora) "
            " ORDER BY lease_scade_il NULLS FIRST, id LIMIT :n FOR UPDATE SKIP LOCKED) "
            f"RETURNING {_COLONNE}"  # noqa: S608 — colonne costanti
        ), {"d": detentore, "scade": scade, "ora": ora, "n": limite})).mappings().all()
    sessioni = [Sessione.da_riga(r) for r in righe]
    for s in sessioni:
        try:
            await _verifica(s, detentore, adesso)
        except Exception as exc:  # noqa: BLE001 — una sessione non ferma le altre; lease scade
            logger.error("caricamento_verifica_errore", caricamento_id=str(s.id),
                         errore=type(exc).__name__, messaggio=str(exc)[:300])
    return len(sessioni)


async def _verifica(s: Sessione, detentore: str, adesso: Callable[[], datetime]) -> None:
    """Assembla, rilegge, confronta; COMPLETO o ERRORE con evento in outbox."""
    log = logger.bind(caricamento_id=str(s.id), tenant_id=str(s.tenant_id))
    if s.tentativi_verifica > settings.caricamenti_verifica_tentativi_max:
        await _in_errore(s, detentore, adesso(), ErroreApi(
            "UPL-502", f"Verifica non riuscita dopo {s.tentativi_verifica - 1} tentativi"))
        return
    s3 = _s3_client()
    if s.upload_id:
        async with get_async_session() as session:
            parti = (await session.execute(text(
                "SELECT numero, etag FROM public.caricamenti_parti WHERE caricamento_id = :id "
                "AND etag IS NOT NULL ORDER BY numero"), {"id": s.id})).fetchall()
        elenco = [{"PartNumber": int(n), "ETag": str(e)} for n, e in parti]
        try:
            await asyncio.to_thread(
                s3.complete_multipart_upload, Bucket=s.bucket, Key=s.chiave_oggetto,
                UploadId=s.upload_id, MultipartUpload={"Parts": elenco})
        except Exception as exc:  # noqa: BLE001 — distinguere «già assemblato» da guasto
            if _codice_s3(exc) not in ("NoSuchUpload", "404"):
                log.warning("caricamento_assemblaggio_rinviato", errore=type(exc).__name__,
                            codice=_codice_s3(exc))
                return  # il lease scadrà e un altro giro riproverà
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.caricamenti SET upload_id = NULL WHERE id = :id "
                "AND detentore = :d"), {"id": s.id, "d": detentore})
    try:
        letto = await asyncio.to_thread(_leggi_oggetto, s3, s.bucket, s.chiave_oggetto)
    except Exception as exc:  # noqa: BLE001
        if _codice_s3(exc) in ("NoSuchKey", "404"):
            await _in_errore(s, detentore, adesso(), ErroreApi(
                "UPL-502", "Il file assemblato non è presente nello storage"))
            return
        log.warning("caricamento_rilettura_rinviata", errore=type(exc).__name__)
        return
    if letto.dimensione != s.dimensione:
        await _in_errore(s, detentore, adesso(), ErroreApi(
            "UPL-502", f"Il file assemblato ha {letto.dimensione} byte, attesi {s.dimensione}"))
        return
    if s.impronta_dichiarata:
        algoritmo = str(s.impronta_dichiarata.get("algoritmo", "")).upper()
        valore = str(s.impronta_dichiarata.get("valore", "")).lower()
        if letto.impronte.get(algoritmo) != valore:
            await _in_errore(s, detentore, adesso(), ErroreApi(
                "UPL-006", f"Impronta {algoritmo} calcolata diversa da quella dichiarata",
                dettagli={"algoritmo": algoritmo, "calcolata": letto.impronte.get(algoritmo)}))
            return
    formato = rileva_formato(letto.testa)
    ora = adesso()
    payload = {
        "tenant_id": str(s.tenant_id), "caricamento_id": str(s.id), "stato": COMPLETO,
        "dimensione": letto.dimensione, "formato_rilevato": formato.mime,
        "impronte": letto.impronte, "completato_il": ora.isoformat(),
    }
    async with get_async_session() as session:
        aggiornata = (await session.execute(text(
            "UPDATE public.caricamenti SET stato = 'COMPLETO', impronte = CAST(:imp AS jsonb), "
            " testa = :testa, formato_rilevato = :formato, completato_il = :ora, "
            " detentore = NULL, lease_scade_il = NULL, errore = NULL, aggiornato_il = :ora "
            "WHERE id = :id AND stato = 'IN_VERIFICA' AND detentore = :d RETURNING id"
        ), {"imp": json.dumps(letto.impronte), "testa": letto.testa, "formato": formato.mime,
            "ora": ora, "id": s.id, "d": detentore})).first()
        if aggiornata is None:
            log.info("caricamento_verifica_superata", nota="sessione cambiata durante la verifica")
            return
        await session.execute(text(
            "DELETE FROM public.caricamenti_parti WHERE caricamento_id = :id"), {"id": s.id})
        await accoda_evento(session, topic=TOPIC_COMPLETATO, chiave=str(s.id), payload=payload,
                            tenant_id=s.tenant_id)
    log.info("caricamento_completo", dimensione=letto.dimensione, formato=formato.mime)


async def _in_errore(s: Sessione, detentore: str, ora: datetime, errore: ErroreApi) -> None:
    problema = errore.corpo(instance=f"/api/v1/caricamenti/{s.id}")
    async with get_async_session() as session:
        aggiornata = (await session.execute(text(
            "UPDATE public.caricamenti SET stato = 'ERRORE', errore = CAST(:errore AS jsonb), "
            " detentore = NULL, lease_scade_il = NULL, aggiornato_il = :ora "
            "WHERE id = :id AND stato = 'IN_VERIFICA' AND detentore = :d RETURNING id"
        ), {"errore": json.dumps(problema, default=str), "ora": ora, "id": s.id,
            "d": detentore})).first()
        if aggiornata is None:
            return
        await accoda_evento(session, topic=TOPIC_ERRORE, chiave=str(s.id), payload={
            "tenant_id": str(s.tenant_id), "caricamento_id": str(s.id), "stato": ERRORE,
            "dimensione": s.dimensione,
            "errore": {"codice": problema.get("codice"), "title": problema.get("title")},
        }, tenant_id=s.tenant_id)
    logger.info("caricamento_in_errore", caricamento_id=str(s.id), codice=errore.codice)
    # I file di una sessione in errore non servono più: si eliminano subito
    # (se fallisce resta la pulizia periodica).
    await _elimina_storage(s, ora)


# ── Pulizia ─────────────────────────────────────────────────────────

async def _annulla_multipart(s3: Any, bucket: str, chiave: str, upload_id: str) -> None:
    try:
        await asyncio.to_thread(s3.abort_multipart_upload, Bucket=bucket, Key=chiave,
                                UploadId=upload_id)
    except Exception as exc:  # noqa: BLE001 — già annullato o storage giù: resta la pulizia
        if _codice_s3(exc) not in ("NoSuchUpload", "404"):
            raise


async def _elimina_storage(s: Sessione, ora: datetime) -> bool:
    """Annulla il multipart ed elimina l'oggetto; segna ``storage_eliminato_il``."""
    s3 = _s3_client()
    try:
        if s.upload_id:
            await _annulla_multipart(s3, s.bucket, s.chiave_oggetto, s.upload_id)
        await elimina_oggetti(s3, s.bucket, [s.chiave_oggetto])
    except Exception as exc:  # noqa: BLE001 — si riprova alla pulizia successiva
        logger.warning("caricamento_pulizia_rinviata", caricamento_id=str(s.id),
                       errore=type(exc).__name__)
        return False
    try:
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.caricamenti SET storage_eliminato_il = :ora, upload_id = NULL "
                "WHERE id = :id AND pdv_id IS NULL"), {"ora": ora, "id": s.id})
            await session.execute(text(
                "DELETE FROM public.caricamenti_parti WHERE caricamento_id = :id"), {"id": s.id})
    except SQLAlchemyError as exc:
        logger.warning("caricamento_pulizia_db_rinviata", caricamento_id=str(s.id),
                       errore=type(exc).__name__)
        return False
    return True


@con_ambito_conservatore(
    "sessioni di caricamento: pulizia delle sessioni scadute di tutti gli enti")
async def pulisci(*, adesso: datetime, limite: int = 200, prova: bool = False) -> list[str]:
    """Marca SCADUTE le sessioni non usate oltre ``scade_il`` ed elimina dallo staging
    i file delle sessioni scadute, annullate o in errore. Restituisce gli id trattati.

    Più repliche in parallelo non si intralciano (``FOR UPDATE SKIP LOCKED``)."""
    async with get_async_session() as session:
        if not prova:
            await session.execute(text(
                "UPDATE public.caricamenti SET stato = 'SCADUTO', aggiornato_il = :ora "
                "WHERE id IN (SELECT id FROM public.caricamenti WHERE pdv_id IS NULL "
                " AND storage_eliminato_il IS NULL AND stato IN ('APERTO', 'COMPLETO') "
                " AND scade_il < :ora ORDER BY scade_il LIMIT :n FOR UPDATE SKIP LOCKED)"
            ), {"ora": adesso, "n": limite})
        righe = (await session.execute(text(
            f"SELECT {_COLONNE} FROM public.caricamenti WHERE pdv_id IS NULL "  # noqa: S608 — colonne costanti
            "AND storage_eliminato_il IS NULL AND (stato IN ('SCADUTO', 'ANNULLATO', 'ERRORE') "
            " OR (stato IN ('APERTO', 'COMPLETO') AND scade_il < :ora)) "
            "ORDER BY scade_il LIMIT :n FOR UPDATE SKIP LOCKED"
        ), {"ora": adesso, "n": limite})).mappings().all()
        sessioni = [Sessione.da_riga(r) for r in righe]
    trattate: list[str] = []
    for s in sessioni:
        if prova or await _elimina_storage(s, adesso):
            trattate.append(str(s.id))
    if trattate:
        logger.info("caricamenti_puliti", num=len(trattate), prova=prova)
    return trattate


# ── Lavoratore nel processo del gateway ─────────────────────────────

async def lavoratore(fine: asyncio.Event) -> None:
    """Verifica, pulizia delle sessioni e pulizia dello staging dei PdV conservati
    (R03 f1, REV-08) periodiche finché ``fine`` non è impostato (come il relay)."""
    global _SVEGLIA
    _SVEGLIA = asyncio.Event()
    ultima_pulizia: datetime | None = None
    ultima_pulizia_staging: datetime | None = None
    while not fine.is_set():
        try:
            while await verifica_in_sospeso() > 0 and not fine.is_set():
                pass
            ora = datetime.now(UTC)
            if ultima_pulizia is None or (ora - ultima_pulizia).total_seconds() \
                    >= settings.caricamenti_pulizia_intervallo_secondi:
                await pulisci(adesso=ora)
                ultima_pulizia = ora
            # R03 f1 (REV-08): copie di transito dei PdV già nel PdA
            if settings.staging_pulizia_attivo and (
                    ultima_pulizia_staging is None
                    or (ora - ultima_pulizia_staging).total_seconds()
                    >= settings.staging_pulizia_intervallo_secondi):
                from app.services.pulizia_staging import pulisci_staging

                ultima_pulizia_staging = ora
                await pulisci_staging(s3=_s3_client(), bucket_staging=_bucket_pdv(), adesso=ora)
        except Exception as exc:  # noqa: BLE001 — DB o storage giù: si riprova al giro dopo
            logger.warning("caricamenti_lavoratore_giro_fallito", errore=type(exc).__name__,
                           messaggio=str(exc)[:200])
        _SVEGLIA.clear()
        attese = [asyncio.create_task(fine.wait()), asyncio.create_task(_SVEGLIA.wait())]
        await asyncio.wait(attese, timeout=settings.caricamenti_intervallo_secondi,
                           return_when=asyncio.FIRST_COMPLETED)
        for t in attese:
            t.cancel()
    _SVEGLIA = None


async def _main(argomenti: Sequence[str]) -> None:  # pragma: no cover — comando operativo
    from libs.shared.db import init_db

    await init_db(settings.database_url)
    if "pulizia" in argomenti:
        trattate = await pulisci(adesso=datetime.now(UTC), prova="--prova" in argomenti)
        import sys

        sys.stdout.write("".join(f"{t}\n" for t in trattate))
        return
    fine = asyncio.Event()
    await lavoratore(fine)


if __name__ == "__main__":  # pragma: no cover
    import sys

    asyncio.run(_main(sys.argv[1:]))
