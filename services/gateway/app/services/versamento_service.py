# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Caso d'uso UNICO «versa un'Unità Documentaria» (PdV), per tutti i canali.

Servizio: gateway. Riferimenti: piano R03-R05 §3 e §4.2; REGOLE_SVILUPPO §7.4,
§8.3, §8.6, §8.15, §9.1, §10.3, §12.1, §12.3, §12.6, §18, §19.5; criticità
VD-C-015/016/017 (R02), VD-C-083, VD-C-094, VD-C-095 (R03).

Ogni canale (REST multipart, portale, PDND, SFTP, ciclo-vita)
traduce il proprio formato in una ``RichiestaVersamento`` e chiama ``versa``:
nessun canale ha regole proprie (§9.1). I file arrivano come ``FonteFile``:

  - ``FonteFlusso``: flusso di byte asincrono (UploadFile o altro), caricato IN
    STREAMING sullo staging calcolando durante il trasferimento impronte (tutti
    gli algoritmi ammessi), dimensione e testa (primi KB) del file;
  - ``FileGiaCaricato``: oggetto già nello staging (sessioni di caricamento) con
    impronte, dimensione e testa calcolate dal server durante il caricamento.

Ordine (controlli economici prima, poi i byte):
  1. indice (schema), input (nomi, estensioni), Idempotency-Key ben formata,
     corrispondenza fra file dichiarati e fonti, numero di file;
  2. classe documentale censita e attiva (MAI creata dal versamento);
     impostazioni e profilo di metadati dal motore delle regole;
     Idempotency-Key obbligatoria con transizione (VD-P-27);
  3. idempotenza e duplicati con una lettura: stessa chiave e stesso indice →
     stessa risposta (200); stessa chiave e indice diverso → VRS-121; stesso
     indice di un PdV attivo → VRS-120 (senza chiave: stessa risposta, perché la
     chiave derivata dal contenuto è la stessa); stessa chiave UD (registro, anno,
     numero) con contenuto diverso → VRS-124; la stessa richiesta di un PdV
     rifiutato, in errore o annullato → VRS-127, mai una risposta positiva
     (R03 f1, REV-01); ``allegati`` annidati → VRS-114 (R03 f1);
  4. estensioni dai formati della classe (registro dei formati, SEC-006);
     regole della fase INDICE (metadati) → VRS-130 con TUTTI i rilievi bloccanti;
     quota di PdV al giorno PRENOTATA atomicamente (QTA-002), rilasciata se il
     PdV non nasce (R03 f1, REV-02);
  5. streaming sullo staging (multipart a parti, nessun file intero in memoria);
     un file oltre il limite del canale si rifiuta (VRS-010) senza leggerlo tutto;
  6. controlli sui file: impronte dichiarate (VRS-107/102), formato dalla testa
     (VRS-108/109), regole della fase FILE (dimensione e formati della classe);
  7. UNA transazione: lock consultivo (cliente, impronta dell'indice),
     ricontrollo, INSERT del PdV (con fotografia delle regole e avvisi), della
     riga d'impronta (con chiave UD) e dell'evento in outbox; azioni in
     transazione del canale (es. sessione di caricamento consumata);
  8. ogni rifiuto o errore dopo il passo 5 elimina i file caricati in questa
     richiesta (compensazione); un crash fra 5 e 7 lascia oggetti senza PdV che
     ``pulizia_orfani`` rimuove. I ``FileGiaCaricato`` non si eliminano mai qui:
     appartengono alla loro sessione.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

import structlog
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import settings
from app.models.versamento import CAMPI_METADATI_NOTI, ChiaveUD, IndiceUD
from app.services.outbox import accoda_evento, pubblica_dopo_commit
from app.services.quota_versamento import prenota_quota_versamento, rilascia_quota_versamento
from app.services.regole_db import ClasseLetta, carica_ambiente, leggi_classe
from libs.shared.db import get_async_session
from libs.shared.formati.firma_binaria import compatibile, normalizza_mime, rileva_formato
from libs.shared.impronte import ALGORITMI_IMPRONTA, funzione_impronta
from libs.shared.security.input_validation import sanitize_string, validate_file_extension
from libs.shared.storage.client import client_s3
from libs.shared.storage.flusso import (
    ErroreStorageFlusso,
    EsitoCaricamento,
    FlussoOltreLimite,
    LettoreAsincrono,
    blocchi_da_lettore,
    carica_flusso,
    dimensione_oggetto,
    elimina_oggetti,
)
from libs.verumdoc_rules import (
    CATALOGO,
    AccordiAmbigui,
    AccordoNonValido,
    AmbienteRegole,
    DatiVersamento,
    DocumentoDaValutare,
    Effetto,
    EsitoRegole,
    Fase,
    OverrideNonAmmesso,
    ProfiloNonValido,
    Rilievo,
    ValoreImpostazioneNonValido,
)
from libs.verumdoc_rules.atti_sistema import e_atto_di_sistema
from libs.verumdoc_rules.catalogo_v1 import (
    FORMATI_AMMESSI,
    IDEMPOTENZA_OBBLIGATORIA,
    IDEMPOTENZA_TRANSIZIONE_FINO_AL,
    limite_byte_classe,
)
from libs.verumdoc_rules.formati import estensioni_ammesse, motivo_estensione
from libs.verumdoc_rules.impostazioni import giorno_locale
from libs.verumdoc_rules.regole import rilievo_override

logger = structlog.get_logger(__name__)

TOPIC_PDV_RICEVUTO = "pdv.received"
MB = 1024 * 1024
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:\-]{8,200}$")

#: Prefisso degli oggetti caricati in streaming dal versamento: ``versamenti/{tenant}/{pdv}/…``
PREFISSO_STAGING_VERSAMENTI = "versamenti"
#: Prefisso degli oggetti delle sessioni di caricamento: ``caricamenti/{tenant}/…``
PREFISSO_STAGING_CARICAMENTI = "caricamenti"
#: Namespace del RdV preliminare (formato VerumDoc, versione 1; NON è uno schema AgID).
NAMESPACE_RDV_PRELIMINARE = "https://schemi.verumdoc.it/rdv/preliminare/1"
#: Chiave usata per il versamento senza Idempotency-Key durante la transizione.
PREFISSO_CHIAVE_DERIVATA = "derivata:"
#: Byte di testa che un ``FileGiaCaricato`` deve portare (o tutto il file se più corto).
BYTE_TESTA_MINIMI = 4096
#: Stati di un PdV concluso con esito negativo (rifiutato dalla verifica o annullato):
#: una richiesta ripetuta che lo ritrova non riceve mai una risposta positiva (VRS-127).
STATI_PDV_NEGATIVI = frozenset({"ERRORE", "ANNULLATO"})

# Algoritmi d'impronta ammessi: tabella unica in libs/shared/impronte.py
# (stessa per validation Step 5 e aip-builder).


# ── Esiti ───────────────────────────────────────────────────────────

from libs.shared.errori import ErroreApi  # noqa: E402 — VD-C-100, solo per RifiutoVersamento


class RifiutoVersamento(ErroreApi):
    """Rifiuto con codice stabile del catalogo errori (§18.1, VD-C-100).

    È un ``ErroreApi``: sollevato fino al router, il gestore installato in
    main.py risponde in application/problem+json. ``corpo()`` resta per chi
    costruisce la risposta a mano e restituisce lo stesso problem+json.
    Il codice deve esistere in libs/shared/errori/catalogo.yaml (KeyError).
    """

    def __init__(self, status: int, codice: str, titolo: str,
                 dettaglio: str | None = None, dettagli: dict[str, Any] | None = None) -> None:
        super().__init__(codice, dettaglio, titolo=titolo, status=status, dettagli=dettagli)

    def corpo(self, *, instance: str | None = None,
              id_richiesta: str | None = None) -> dict[str, Any]:
        return super().corpo(instance=instance, id_richiesta=id_richiesta)


# ── Interfaccia del caso d'uso ──────────────────────────────────────

class Canale(StrEnum):
    """Canale d'ingresso: cambia solo la traduzione del formato, mai le regole."""

    REST = "REST"
    PORTALE = "PORTALE"
    PDND = "PDND"
    SFTP = "SFTP"
    #: R05: versamenti del servizio ciclo-vita (rettifica, riversamento in ingresso)
    CICLO_VITA = "CICLO_VITA"


@dataclass(frozen=True)
class IdentitaVersante:
    """Chi versa: cliente (ente) e utente già autenticati dal canale, e il canale."""

    tenant_id: uuid.UUID
    utente_id: uuid.UUID
    canale: Canale
    #: piano dell'ente se il canale lo conosce già (letto dal database da
    #: ``get_current_user``); None = si legge dall'ente (quota PdV/giorno)
    piano: str | None = None


class FonteFile(ABC):
    """Sorgente di un file del versamento; ``nome_file`` deve coincidere con l'indice."""

    nome_file: str

    @property
    @abstractmethod
    def da_caricare(self) -> bool:
        """True se il caso d'uso deve trasferire i byte sullo staging."""


@dataclass(eq=False)
class FonteFlusso(FonteFile):
    """File che arriva come flusso di byte, caricato in streaming sullo staging.

    ``flusso``: iteratore asincrono di blocchi oppure oggetto con ``read(n)``
    asincrono (``UploadFile``). ``limite_byte``: limite del CANALE (es. 100 MB per
    il multipart REST); oltre si rifiuta con VRS-010 senza leggere il resto.
    ``dimensione_annunciata``: se il canale la conosce prima (``UploadFile.size``),
    un file oltre il limite si rifiuta senza leggerne un byte."""

    nome_file: str
    flusso: AsyncIterator[bytes] | LettoreAsincrono
    content_type: str | None = None
    dimensione_annunciata: int | None = None
    limite_byte: int | None = None

    @property
    def da_caricare(self) -> bool:
        return True

    def blocchi(self) -> AsyncIterator[bytes]:
        if hasattr(self.flusso, "read"):
            return blocchi_da_lettore(self.flusso)  # type: ignore[arg-type]
        return self.flusso  # type: ignore[return-value]


@dataclass(frozen=True, eq=False)
class FileGiaCaricato(FonteFile):
    """File già presente nello staging (sessione di caricamento, R03 ondata B).

    Campi (tutti calcolati DAL SERVER durante il caricamento, mai dichiarati dal client):
      - ``nome_file``: nome come nell'indice del versamento;
      - ``bucket``: bucket di staging (deve essere ``settings.s3_bucket_pdv``);
      - ``chiave``: chiave dell'oggetto, sotto ``caricamenti/{tenant_id}/``: un oggetto
        di un altro cliente risponde come inesistente (UPL-404, REGOLE §14.4);
      - ``dimensione``: byte dell'oggetto (si confronta con ``head_object``: UPL-422);
      - ``impronte``: esadecimali per OGNI algoritmo di ``libs.shared.impronte``
        (``SHA-256``, ``SHA-512``, ``SHA3-256``), altrimenti UPL-422;
      - ``testa``: primi ``BYTE_TESTA_MINIMI`` byte (o tutto il file se più corto),
        per il riconoscimento del formato dal contenuto;
      - ``caricamento_id``: id della sessione (va nell'evento e nel PdV);
      - ``content_type``: tipo dichiarato al caricamento (solo informativo).
    Il caso d'uso NON elimina mai questi oggetti (nemmeno sul rifiuto): ciclo di
    vita e scadenza sono della sessione. Per segnare la sessione «consumata»
    nella stessa transazione del PdV usare ``RichiestaVersamento.in_transazione``."""

    nome_file: str
    bucket: str
    chiave: str
    dimensione: int
    impronte: Mapping[str, str]
    testa: bytes
    caricamento_id: str | None = None
    content_type: str | None = None

    @property
    def da_caricare(self) -> bool:
        return False


#: Azione del canale eseguita DENTRO la transazione del PdV: ``(sessione, pdv_id)``.
AzioneInTransazione = Callable[[Any, str], Awaitable[None]]


@dataclass
class RichiestaVersamento:
    """Ingresso unico del caso d'uso (tutti i canali).

    ``idempotency_key``: chiave del client (8-200 car. ``[A-Za-z0-9._:-]``); senza,
    durante la transizione si usa una chiave derivata dal contenuto. ``adesso``:
    istante della richiesta (UTC, il tempo è un argomento: §17.6)."""

    indice: IndiceUD
    fonti: Sequence[FonteFile]
    identita: IdentitaVersante
    idempotency_key: str | None
    adesso: datetime
    in_transazione: Sequence[AzioneInTransazione] = ()
    #: chiave UD del CANALE quando l'indice non la porta completa. Vale per
    #: l'unicità fra i PdV attivi (VRS-124) come quella dell'indice. None = quella
    #: dell'indice (``normalizza_chiave_ud``).
    chiave_ud: ChiaveUDNormalizzata | None = None


@dataclass
class EsitoVersamento:
    """Esito positivo: PdV nuovo (``nuovo``) oppure stessa risposta per la stessa richiesta."""

    pdv_id: str
    stato: str
    nuovo: bool = True
    avvisi: list[dict[str, Any]] = field(default_factory=list)
    #: True se la richiesta non aveva Idempotency-Key (transizione VD-P-27)
    chiave_derivata: bool = False
    #: ultimo giorno della transizione (per l'intestazione Sunset), se chiave derivata
    transizione_fino_al: date | None = None

    @property
    def link_stato(self) -> str:
        return f"/api/v1/versamento/{self.pdv_id}"


# ── Storage ─────────────────────────────────────────────────────────

def _s3_client() -> Any:
    """Client S3 dello staging, UNO per processo e configurazione (R04: prima se ne
    creava uno a versamento, 18,8 % della CPU nella prova di carico). Chiavi vuote →
    catena di credenziali di boto3 (IRSA in esercizio, VD-C-101)."""
    return client_s3(endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
                     secret_key=settings.s3_secret_key, region=settings.s3_region)


def _bucket_pdv() -> str:
    """Bucket di staging dei versamenti (transito, MAI il bucket conservativo)."""
    return getattr(settings, "s3_bucket_pdv", "verumdoc-pdv")


#: (client, bucket) già controllati da questo processo: la HEAD fuori produzione
#: si fa una volta, non a ogni versamento (R04, 7,4 % della CPU nella prova di carico).
_BUCKET_CONTROLLATI: set[tuple[int, str]] = set()


def _assicura_bucket(s3: Any, bucket: str) -> None:
    """Fuori produzione crea il bucket di staging se manca (in produzione lo crea Terraform)."""
    if settings.env.lower() == "production":
        return
    chiave = (id(s3), bucket)
    if chiave in _BUCKET_CONTROLLATI:
        return
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001 — bucket di sviluppo assente: si crea
        logger.info("s3_bucket_pdv_assente_creazione", bucket=bucket)
        kwargs: dict[str, Any] = {"Bucket": bucket}
        regione = getattr(getattr(s3, "meta", None), "region_name", None)
        if regione and regione != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": regione}
        s3.create_bucket(**kwargs)
    _BUCKET_CONTROLLATI.add(chiave)


def chiave_staging(tenant_id: uuid.UUID, pdv_id: str, ordine: int, nome_file: str) -> str:
    """Chiave di un file caricato in streaming dal versamento."""
    nome = nome_sicuro(nome_file)
    return f"{PREFISSO_STAGING_VERSAMENTI}/{tenant_id}/{pdv_id}/{ordine:04d}-{nome}"


def prefisso_caricamenti(tenant_id: uuid.UUID) -> str:
    """Prefisso degli oggetti delle sessioni di caricamento del cliente."""
    return f"{PREFISSO_STAGING_CARICAMENTI}/{tenant_id}/"


@dataclass
class _Caricamento:
    """Oggetti caricati da QUESTA richiesta (da eliminare su rifiuto o errore)."""

    chiavi: list[str] = field(default_factory=list)


async def _compensa(s3: Any, caricamento: _Caricamento, pdv_id: str) -> None:
    """Elimina i file caricati per un PdV non salvato (nessun orfano)."""
    if not caricamento.chiavi:
        return
    try:
        await elimina_oggetti(s3, _bucket_pdv(), list(caricamento.chiavi))
        logger.info("versamento_compensazione_file_eliminati", pdv_id=pdv_id,
                    num=len(caricamento.chiavi))
    except Exception as exc:  # noqa: BLE001 — resta il job pulizia_orfani
        logger.error("versamento_compensazione_fallita", pdv_id=pdv_id,
                     num=len(caricamento.chiavi), errore=type(exc).__name__)


# ── Funzioni pure ───────────────────────────────────────────────────

def nome_sicuro(nome: str) -> str:
    """Nome file utilizzabile in una chiave di storage."""
    return (re.sub(r"[^A-Za-z0-9._-]", "_", nome).strip("._") or "file")[:150]


def calcola_impronta_indice(indice: IndiceUD) -> str:
    """Impronta SHA-256 dell'indice normalizzato (JSON canonico) — chiave dei duplicati.

    L'indice comprende le impronte dichiarate dei file: due indici uguali
    dichiarano gli stessi byte (le impronte si verificano sul contenuto)."""
    # R05 Q1: ``accordo`` e ``override`` assenti NON entrano nell'impronta, così
    # l'impronta di un indice di prima di R05 non cambia (idempotenza e duplicati)
    assenti = {c for c in ("accordo", "override") if getattr(indice, c, None) is None}
    canonico = json.dumps(indice.model_dump(mode="json", exclude=assenti), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def calcola_impronta_richiesta(impronta_indice: str, verificati: Sequence[_FileVerificato]) -> str:
    """Impronta dell'intera richiesta (indice + contenuto dei file), conservata sul PdV."""
    parti = [impronta_indice] + sorted(f"{f.nome_file}\x00{f.sha256}" for f in verificati)
    return hashlib.sha256("\x01".join(parti).encode("utf-8")).hexdigest()


def chiave_derivata(tenant_id: uuid.UUID, impronta_indice: str) -> str:
    """Chiave d'idempotenza derivata dal contenuto (ente + impronta dell'indice)."""
    impronta = hashlib.sha256(f"{tenant_id}:{impronta_indice}".encode()).hexdigest()
    return f"{PREFISSO_CHIAVE_DERIVATA}{impronta}"


@dataclass(frozen=True)
class ChiaveUDNormalizzata:
    registro: str
    anno: str
    numero: str


def normalizza_chiave_ud(chiave: ChiaveUD | None) -> ChiaveUDNormalizzata | None:
    """Chiave UD per l'unicità (VD-C-095): c'è se anno e numero non sono vuoti.

    Registro senza spazi ai lati e in maiuscolo ('' se assente); anno e numero
    senza spazi ai lati (il numero conserva maiuscole e minuscole)."""
    if chiave is None:
        return None
    anno = (chiave.anno or "").strip()
    numero = (chiave.numero or "").strip()
    if not anno or not numero:
        return None
    return ChiaveUDNormalizzata((chiave.registro or "").strip().upper(), anno, numero)


def _da_http(exc: HTTPException) -> RifiutoVersamento:
    dettaglio = exc.detail if isinstance(exc.detail, dict) else {"title": str(exc.detail)}
    return RifiutoVersamento(exc.status_code, str(dettaglio.get("type", "VRS-110")),
                             str(dettaglio.get("title", "Input non valido")),
                             dettaglio.get("detail"))


def leggi_indice(indice_json: str | bytes) -> IndiceUD:
    """Indice dal JSON del canale; VRS-001 se non è valido."""
    try:
        return IndiceUD.model_validate_json(indice_json)
    except ValidationError as exc:
        raise RifiutoVersamento(400, "VRS-001", "JSON IndiceUD non valido",
                                str(exc)[:2000]) from exc


def identita_da_utente(user: Mapping[str, Any], canale: Canale) -> IdentitaVersante:
    """Cliente, utente e canale EFFETTIVO dall'identità autenticata (§14.3, contratto C2).

    ``canale`` è il canale della rotta; passa da ``app.auth.canali.canale_effettivo``:
    con un token di canale (es. SFTP) su una rotta REST il PdV è del canale del
    token, su una rotta di un altro canale (PDND, portale) → 403
    AUTH-010. Così nessuna rotta registra un canale diverso da quello reale.
    Se cliente o utente non si ricavano, il versamento è negato (VRS-403)."""
    from app.auth.canali import canale_effettivo

    effettivo = Canale(canale_effettivo(dict(user), canale.value))
    piano = user.get("piano")
    try:
        return IdentitaVersante(uuid.UUID(str(user.get("tenant_id"))),
                                uuid.UUID(str(user.get("user_id"))), effettivo,
                                str(piano) if piano else None)
    except ValueError as exc:
        raise RifiutoVersamento(403, "VRS-403", "Cliente o utente non identificabili dal token") \
            from exc


def controlla_indice_e_fonti(indice: IndiceUD, fonti: Sequence[FonteFile],
                             idempotency_key: str | None) -> dict[str, FonteFile]:
    """Controlli puri che precedono ogni lettura: restituisce le fonti per nome."""
    if idempotency_key is not None and not _IDEMPOTENCY_KEY.match(idempotency_key):
        raise RifiutoVersamento(400, "VRS-122", "Idempotency-Key non valida",
                                "Da 8 a 200 caratteri fra lettere, cifre e . _ : -")
    annidati = sorted(d.id for d in indice.documenti if d.allegati)
    if annidati:
        # R03 f1 (difetto 2 del rapporto c2): prima accettati e ignorati in silenzio
        # (§18.3). Il modello è piatto: allegati e annessi sono voci di
        # ``documenti`` con il loro ``tipo``.
        raise RifiutoVersamento(
            400, "VRS-114", "Documenti annidati in «allegati» non ammessi",
            "Elencare allegati e annessi in «documenti» con tipo ALLEGATO, ANNESSO o "
            "ANNOTAZIONE.", {"documenti": annidati})
    try:
        sanitize_string(indice.classe_documentale, max_length=50)
        for doc in indice.documenti:
            sanitize_string(doc.nome_file, max_length=255)
            # Primo filtro senza database: estensioni del REGISTRO dei formati (non
            # più un elenco fisso). Quelle della classe si controllano dopo aver
            # letto le regole (``controlla_estensioni``).
            validate_file_extension(doc.nome_file)
            sanitize_string(doc.id, max_length=100)
    except HTTPException as exc:
        raise _da_http(exc) from exc
    if len(fonti) > settings.versamento_max_file_per_pdv:
        raise RifiutoVersamento(
            413, "VRS-113",
            f"Troppi file nel versamento (max {settings.versamento_max_file_per_pdv})")
    per_nome: dict[str, FonteFile] = {}
    for fonte in fonti:
        if fonte.nome_file in per_nome:
            raise RifiutoVersamento(400, "VRS-111", f"File {fonte.nome_file!r} allegato più volte")
        per_nome[fonte.nome_file] = fonte
    dichiarati = [d.nome_file for d in indice.documenti]
    doppi = sorted({n for n in dichiarati if dichiarati.count(n) > 1})
    if doppi:
        raise RifiutoVersamento(400, "VRS-111", "Nome file dichiarato più volte nell'indice",
                                dettagli={"file": doppi})
    for nome in dichiarati:
        if nome not in per_nome:
            raise RifiutoVersamento(400, "VRS-101", f"File {nome!r} dichiarato ma non allegato")
    non_dichiarati = sorted(set(per_nome) - set(dichiarati))
    if non_dichiarati:
        raise RifiutoVersamento(
            400, "VRS-104", "File allegati non dichiarati nell'indice",
            "Ogni file allegato deve comparire nell'indice del versamento.",
            {"file": non_dichiarati},
        )
    for doc in indice.documenti:
        if funzione_impronta(doc.hash.algoritmo) is None:  # schema già restrittivo: difesa
            raise RifiutoVersamento(400, "VRS-107",
                                    f"Algoritmo d'impronta non ammesso: {doc.hash.algoritmo}",
                                    dettagli={"ammessi": sorted(ALGORITMI_IMPRONTA)})
    return per_nome


def controlla_estensioni(indice: IndiceUD, ambiente: AmbienteRegole,
                         canale: str | None = None) -> None:
    """Estensioni dei nomi file ammesse dai formati della classe (R03 f1, difetto 4 c2).

    Derivano da ``FORMATI_AMMESSI`` della classe quando RGL-105 blocca, altrimenti
    da tutto il registro dei formati (``libs.verumdoc_rules.formati``): mai da un
    elenco fisso. È un primo filtro economico prima del trasferimento; il formato
    vero si riconosce dal contenuto (VRS-108/109, RGL-105). SEC-006 se no."""
    blocca = Effetto(ambiente.valore("REGOLA.RGL-105.EFFETTO")) is Effetto.BLOCCA
    ammesse = estensioni_ammesse(ambiente.valore(FORMATI_AMMESSI), restrizione_attiva=blocca,
                                 normalizza=_normalizza_formato)
    rifiutati = [(d.nome_file, m) for d in indice.documenti
                 if not _atto_di_sistema(d, canale)   # R09 (D24): atto XML di rettifica
                 and (m := motivo_estensione(d.nome_file, ammesse)) is not None]
    if rifiutati:
        raise RifiutoVersamento(
            400, "SEC-006", "Estensione del file non ammessa", rifiutati[0][1],
            {"file": [n for n, _ in rifiutati], "ammesse": sorted(ammesse.estensioni),
             "origine": ammesse.origine, "registro_formati": ammesse.registro})


@dataclass(frozen=True)
class FileCaricato:
    """Un file nello staging con ciò che si è calcolato sui suoi byte."""

    nome_file: str
    bucket: str
    chiave: str
    dimensione: int
    impronte: Mapping[str, str]
    testa: bytes
    caricamento_id: str | None = None


@dataclass
class _FileVerificato:
    doc_id: str
    tipo: str
    nome_file: str
    caricato: FileCaricato
    mime_dichiarato: str | None
    mime_rilevato: str
    algoritmo: str
    impronta_dichiarata: str

    @property
    def sha256(self) -> str:
        return self.caricato.impronte["SHA-256"]


def verifica_file(indice: IndiceUD, caricati: Mapping[str, FileCaricato]) -> list[_FileVerificato]:
    """Controlli puri sui file caricati (VD-C-017): impronta dichiarata = calcolata,
    formato riconosciuto dalla testa, niente eseguibili, MIME coerente."""
    verificati: list[_FileVerificato] = []
    for doc in indice.documenti:
        f = caricati[doc.nome_file]
        algoritmo = doc.hash.algoritmo.upper()
        calcolata = f.impronte.get(algoritmo)
        if calcolata is None:
            raise RifiutoVersamento(400, "VRS-107",
                                    f"Algoritmo d'impronta non ammesso: {algoritmo}",
                                    dettagli={"ammessi": sorted(ALGORITMI_IMPRONTA)})
        if calcolata.lower() != doc.hash.impronta.lower():
            raise RifiutoVersamento(
                400, "VRS-102", f"Hash {algoritmo} errato per {doc.nome_file!r}",
                f"Atteso: {doc.hash.impronta} | Calcolato: {calcolata}",
            )
        formato = rileva_formato(f.testa)
        if formato.eseguibile:
            raise RifiutoVersamento(
                422, "VRS-108", f"Il file {doc.nome_file!r} è un {formato.eseguibile}",
                "Gli eseguibili non sono ammessi in conservazione, qualunque sia il nome.",
            )
        if formato.mime is None:
            raise RifiutoVersamento(
                422, "VRS-109",
                f"Formato del file {doc.nome_file!r} non riconosciuto dal contenuto",
            )
        if not compatibile(doc.formato, formato.mime):
            raise RifiutoVersamento(
                422, "VRS-109",
                f"Il contenuto di {doc.nome_file!r} non corrisponde al formato dichiarato",
                dettagli={"dichiarato": doc.formato, "rilevato": formato.mime},
            )
        verificati.append(_FileVerificato(
            doc_id=doc.id, tipo=doc.tipo, nome_file=doc.nome_file, caricato=f,
            mime_dichiarato=doc.formato, mime_rilevato=formato.mime,
            algoritmo=algoritmo, impronta_dichiarata=doc.hash.impronta,
        ))
    return verificati


def _normalizza_formato(mime: str) -> str:
    return normalizza_mime(mime) or mime


def _atto_di_sistema(doc: Any, canale: str | None) -> bool:
    """R09 (D24): atto XML di rettifica versato da ciclo-vita (canale CICLO_VITA)."""
    return e_atto_di_sistema(
        canale, tipo=doc.tipo, formato=doc.formato,
        parametri=doc.parametri.model_dump(mode="json") if doc.parametri else None)


def dati_per_regole(indice: IndiceUD,
                    verificati: Sequence[_FileVerificato] | None = None, *,
                    canale: str | None = None) -> DatiVersamento:
    """Vista pura del versamento per il motore delle regole (metadati come versati)."""
    per_nome = {v.nome_file: v for v in verificati or ()}
    documenti = []
    for doc in indice.documenti:
        v = per_nome.get(doc.nome_file)
        documenti.append(DocumentoDaValutare(
            id=doc.id, tipo=doc.tipo, nome_file=doc.nome_file,
            metadati=doc.parametri.model_dump(mode="json") if doc.parametri else None,
            dimensione_byte=v.caricato.dimensione if v else None,
            formato_rilevato=v.mime_rilevato if v else None,
            atto_di_sistema=_atto_di_sistema(doc, canale),
        ))
    return DatiVersamento(classe=indice.classe_documentale, documenti=tuple(documenti),
                          campi_noti=CAMPI_METADATI_NOTI, normalizza_formato=_normalizza_formato)


def avviso(rilievo: Rilievo) -> dict[str, Any]:
    """Rilievo nella forma della risposta, del PdV e del RdV (``codice`` = regola)."""
    return {"codice": rilievo.regola, "effetto": rilievo.effetto.value, "campo": rilievo.campo,
            "documento": rilievo.documento, "messaggio": rilievo.messaggio}


def rifiuto_regole(esito: EsitoRegole) -> RifiutoVersamento:
    """VRS-130 con TUTTI i rilievi bloccanti (e gli avvisi) nei dettagli."""
    bloccanti = [avviso(r) for r in esito.bloccanti]
    return RifiutoVersamento(
        422, "VRS-130", "Versamento non conforme alle regole di conservazione",
        f"{len(bloccanti)} rilievi bloccanti: "
        + "; ".join(r["messaggio"] for r in bloccanti[:5]),
        {"rilievi": bloccanti, "avvisi": [avviso(r) for r in esito.avvisi]},
    )


def costruisci_rdv_preliminare(
    pdv_id: str, tenant_id: str, indice_hash: str, now: datetime,
    documenti: Sequence[Mapping[str, Any]] = (), avvisi: Sequence[Mapping[str, Any]] = (),
) -> str:
    """
    Rapporto di versamento PRELIMINARE (XML) emesso all'accettazione del PdV.

    Formato VerumDoc versionato (``NAMESPACE_RDV_PRELIMINARE``, versione 1): NON
    dichiara alcuno schema AgID (VD-C-096). Elenca i file con le impronte
    calcolate sui byte e gli avvisi del motore delle regole. Non è firmato: il
    formato definitivo e la firma del rapporto sono previsti in R04.
    """
    from xml.dom.minidom import parseString
    from xml.etree.ElementTree import Element, SubElement, tostring

    root = Element("RicevutaVersamento")
    root.set("xmlns", NAMESPACE_RDV_PRELIMINARE)
    root.set("versione", "1")
    root.set("tipo", "PRELIMINARE")
    SubElement(root, "IdentificativoPdV").text = pdv_id
    SubElement(root, "TenantId").text = tenant_id
    SubElement(root, "DataRicezione").text = now.isoformat()
    SubElement(root, "HashIndiceUD").text = indice_hash
    SubElement(root, "AlgoritmoHash").text = "SHA-256"
    SubElement(root, "EsitoRicezione").text = "ACCETTATO"
    elenco = SubElement(root, "Documenti")
    for d in documenti:
        el = SubElement(elenco, "Documento")
        SubElement(el, "IdDocumento").text = str(d.get("doc_id", ""))
        SubElement(el, "NomeFile").text = str(d.get("nome_file", ""))
        SubElement(el, "DimensioneBytes").text = str(d.get("size_bytes", ""))
        SubElement(el, "ImprontaSHA256").text = str(d.get("hash_sha256", ""))
    lista = SubElement(root, "Avvisi")
    for a in avvisi:
        el = SubElement(lista, "Avviso")
        SubElement(el, "Codice").text = str(a.get("codice", ""))
        if a.get("documento"):
            SubElement(el, "Documento").text = str(a["documento"])
        if a.get("campo"):
            SubElement(el, "Campo").text = str(a["campo"])
        SubElement(el, "Messaggio").text = str(a.get("messaggio", ""))
    SubElement(root, "ConservatoreId").text = "VerumDoc S.r.l."
    SubElement(root, "NotaConservatore").text = (
        "Ricevuta preliminare non firmata: versamento ricevuto e in attesa di verifica. "
        "Il rapporto di versamento definitivo sarà emesso dopo l'inserimento nella catena "
        "delle impronte."
    )
    return parseString(tostring(root, encoding="unicode")).toprettyxml(indent="  ")  # noqa: S318 — XML prodotto dal servizio stesso, non dall'esterno


# ── Letture DB ──────────────────────────────────────────────────────

async def _leggi_classe(session: Any, codice: str, tenant: uuid.UUID) -> ClasseLetta:
    classe = await leggi_classe(session, codice, tenant)
    if classe is None:
        raise RifiutoVersamento(
            422, "VRS-105", f"Classe documentale {codice!r} non censita",
            "Le classi documentali si configurano dal pannello Classi Documentali: "
            "il versamento non le crea.",
        )
    if not classe.attiva:
        raise RifiutoVersamento(422, "VRS-106", f"Classe documentale {codice!r} non attiva")
    return classe


@dataclass
class _Esistente:
    pdv_id: str
    stato: str
    impronta_indice: str
    avvisi: list[dict[str, Any]]
    motivo: str  # "chiave" | "indice" | "chiave_ud"
    #: False se la verifica l'ha rifiutato (chiave UD e indice liberati)
    attivo: bool = True

    @property
    def negativo(self) -> bool:
        """PdV concluso con esito negativo: rifiutato, in errore o annullato."""
        return not self.attivo or self.stato in STATI_PDV_NEGATIVI


_SELECT_ESISTENTE = (
    "SELECT v.pdv_id::text AS pdv_id, p.stato::text AS stato, v.impronta_indice, "
    "p.avvisi::text AS avvisi, v.attivo "
    "FROM public.versamento_impronte v JOIN public.pdv p ON p.id = v.pdv_id "
)


def _esistente(riga: Any, motivo: str) -> _Esistente:
    avvisi = json.loads(riga["avvisi"]) if riga["avvisi"] else []
    return _Esistente(riga["pdv_id"], riga["stato"], riga["impronta_indice"], list(avvisi or []),
                      motivo, bool(riga["attivo"]))


async def _cerca_esistente(session: Any, tenant: uuid.UUID, chiave: str | None,
                           impronta_indice: str,
                           chiave_ud: ChiaveUDNormalizzata | None) -> _Esistente | None:
    """Un PdV che decide la risposta: per Idempotency-Key, per indice o per chiave UD."""
    if chiave:
        riga = (await session.execute(
            text(_SELECT_ESISTENTE + "WHERE v.tenant_id = :t AND v.chiave_idempotenza = :k"),
            {"t": tenant, "k": chiave},
        )).mappings().first()
        if riga:
            return _esistente(riga, "chiave")
    riga = (await session.execute(
        text(_SELECT_ESISTENTE + "WHERE v.tenant_id = :t AND v.impronta_indice = :i AND v.attivo"),
        {"t": tenant, "i": impronta_indice},
    )).mappings().first()
    if riga:
        return _esistente(riga, "indice")
    if chiave_ud is not None:
        riga = (await session.execute(
            text(_SELECT_ESISTENTE + "WHERE v.tenant_id = :t AND v.attivo "
                 "AND v.chiave_ud_registro = :r AND v.chiave_ud_anno = :a "
                 "AND v.chiave_ud_numero = :n"),
            {"t": tenant, "r": chiave_ud.registro, "a": chiave_ud.anno, "n": chiave_ud.numero},
        )).mappings().first()
        if riga:
            return _esistente(riga, "chiave_ud")
    return None


@dataclass(frozen=True)
class _Idempotenza:
    chiave: str | None          # chiave del client (salvata con vincolo unico)
    derivata: str | None        # chiave derivata (transizione): non salvata nel vincolo
    fino_al: date | None
    avviso: dict[str, Any] | None


def _decidi_su_esistente(esistente: _Esistente, impronta_indice: str,
                         idem: _Idempotenza) -> EsitoVersamento:
    """Stessa richiesta → stessa risposta; altrimenti il conflitto con il suo codice.

    R03 f1 (REV-01): la «stessa risposta» per un PdV concluso con esito negativo
    (rifiutato, in errore, annullato) NON è positiva su nessun canale: 409 VRS-127
    con il PdV e il suo stato. Per riversare dopo una correzione si cambia il
    contenuto (la chiave UD è libera) e, se la si manda, la Idempotency-Key."""
    link = f"/api/v1/versamento/{esistente.pdv_id}"

    def ripetuta() -> EsitoVersamento:
        if esistente.negativo:
            raise RifiutoVersamento(
                409, "VRS-127", "Versamento già concluso con esito negativo",
                f"La stessa richiesta corrisponde al PdV {esistente.pdv_id} nello stato "
                f"{esistente.stato}{'' if esistente.attivo else ' (rifiutato)'}: non è "
                f"conservato. Vedi GET {link}",
                {"pdv_id_esistente": esistente.pdv_id, "stato_esistente": esistente.stato,
                 "link": link},
            )
        avvisi = list(esistente.avvisi)
        if idem.avviso:
            avvisi.append(idem.avviso)
        return EsitoVersamento(esistente.pdv_id, esistente.stato, nuovo=False, avvisi=avvisi,
                               chiave_derivata=idem.derivata is not None,
                               transizione_fino_al=idem.fino_al)

    if esistente.motivo == "chiave":
        if esistente.impronta_indice == impronta_indice:
            return ripetuta()
        raise RifiutoVersamento(
            409, "VRS-121", "Idempotency-Key già usata con un contenuto diverso",
            "Usare una nuova chiave per un versamento diverso.",
            {"pdv_id_esistente": esistente.pdv_id},
        )
    if esistente.motivo == "indice":
        if idem.derivata is not None:  # stessa chiave derivata = stessa richiesta
            return ripetuta()
        raise RifiutoVersamento(
            409, "VRS-120", "Versamento duplicato: lo stesso indice è già stato versato",
            f"Vedi GET {link}",
            {"pdv_id_esistente": esistente.pdv_id, "stato_esistente": esistente.stato,
             "link": link},
        )
    raise RifiutoVersamento(
        409, "VRS-124", "Chiave dell'unità documentaria già usata",
        "Registro, anno e numero identificano già un altro versamento attivo con contenuto "
        f"diverso. Vedi GET {link}",
        {"pdv_id_esistente": esistente.pdv_id, "stato_esistente": esistente.stato, "link": link},
    )


def decidi_idempotenza(chiave: str | None, ambiente: AmbienteRegole, adesso: datetime,
                       tenant: uuid.UUID, impronta_indice: str) -> _Idempotenza:
    """Idempotency-Key obbligatoria con transizione (VD-C-083, VD-P-27).

    Con chiave: si usa quella. Senza chiave e obbligo attivo: fino al giorno
    ``IDEMPOTENZA_TRANSIZIONE_FINO_AL`` (incluso, ora italiana) si accetta con
    una chiave derivata dal contenuto e un avviso VRS-126; dopo, VRS-125 (428)."""
    if chiave:
        return _Idempotenza(chiave, None, None, None)
    if not ambiente.valore(IDEMPOTENZA_OBBLIGATORIA):
        return _Idempotenza(None, chiave_derivata(tenant, impronta_indice), None, None)
    fino_al: date = ambiente.valore(IDEMPOTENZA_TRANSIZIONE_FINO_AL)
    if giorno_locale(adesso) > fino_al:
        raise RifiutoVersamento(
            428, "VRS-125", "Idempotency-Key obbligatoria",
            "Inviare l'intestazione Idempotency-Key (un identificativo per ogni versamento, "
            "riusato sui ritentativi dello stesso versamento).",
        )
    return _Idempotenza(None, chiave_derivata(tenant, impronta_indice), fino_al, {
        "codice": "VRS-126", "effetto": Effetto.AVVISA.value, "campo": "Idempotency-Key",
        "documento": None,
        "messaggio": ("Versamento senza Idempotency-Key: accettato con una chiave derivata dal "
                      f"contenuto. Dal {fino_al.isoformat()} in poi la richiesta sarà rifiutata "
                      "(428 VRS-125)."),
    })


# ── Caricamento ─────────────────────────────────────────────────────

async def _carica_flusso(s3: Any, fonte: FonteFlusso, *, chiave: str, limite_classe: int | None,
                         indice: IndiceUD, ambiente: AmbienteRegole) -> FileCaricato:
    limiti = [x for x in (fonte.limite_byte, limite_classe) if x is not None]
    limite = min(limiti) if limiti else None

    def oltre(letti: int) -> RifiutoVersamento:
        if fonte.limite_byte is not None and letti > fonte.limite_byte:
            return RifiutoVersamento(
                413, "VRS-010",
                f"File {fonte.nome_file!r} oltre il limite del canale "
                f"({fonte.limite_byte // MB} MB)",
                "Per i file più grandi usare le sessioni di caricamento.",
            )
        # Limite della classe (RGL-104 che blocca): rilievo prodotto dal motore
        doc = next(d for d in indice.documenti if d.nome_file == fonte.nome_file)
        dati = DatiVersamento(indice.classe_documentale, (DocumentoDaValutare(
            doc.id, doc.tipo, doc.nome_file, None, letti, None),), CAMPI_METADATI_NOTI)
        esito = EsitoRegole([r for r in CATALOGO.valuta(dati, ambiente, Fase.FILE).rilievi
                             if r.regola == "RGL-104"])
        return rifiuto_regole(esito)

    if fonte.dimensione_annunciata is not None and limite is not None \
            and fonte.dimensione_annunciata > limite:
        raise oltre(fonte.dimensione_annunciata)
    try:
        esito: EsitoCaricamento = await carica_flusso(
            fonte.blocchi(), s3=s3, bucket=_bucket_pdv(), chiave=chiave,
            content_type="application/octet-stream",
            dimensione_parte=settings.staging_parte_mb * MB, limite_byte=limite,
        )
    except FlussoOltreLimite as exc:
        raise oltre(exc.letti) from exc
    except ErroreStorageFlusso as exc:
        logger.error("staging_caricamento_fallito", chiave=chiave, errore=str(exc)[:200])
        raise RifiutoVersamento(503, "VRS-500", "Storage temporaneamente non disponibile",
                                "Riprovare tra qualche minuto.") from exc
    return FileCaricato(fonte.nome_file, esito.bucket, esito.chiave, esito.dimensione,
                        dict(esito.impronte), esito.testa)


async def _verifica_gia_caricato(s3: Any, fonte: FileGiaCaricato,
                                 tenant: uuid.UUID) -> FileCaricato:
    """Un file di una sessione di caricamento: dello stesso cliente, presente, coerente."""
    if fonte.bucket != _bucket_pdv() or not fonte.chiave.startswith(prefisso_caricamenti(tenant)) \
            or ".." in fonte.chiave.split("/"):
        raise RifiutoVersamento(404, "UPL-404", f"File caricato {fonte.nome_file!r} non trovato")
    if set(fonte.impronte) != set(ALGORITMI_IMPRONTA) \
            or len(fonte.testa) < min(fonte.dimensione, BYTE_TESTA_MINIMI):
        raise RifiutoVersamento(422, "UPL-422",
                                f"File caricato {fonte.nome_file!r} incompleto",
                                "Impronte o testa del file mancanti: caricamento non concluso.")
    try:
        dimensione = await dimensione_oggetto(s3, fonte.bucket, fonte.chiave)
    except Exception as exc:  # noqa: BLE001 — storage non raggiungibile: errore temporaneo
        logger.error("staging_lettura_fallita", chiave=fonte.chiave, errore=type(exc).__name__)
        raise RifiutoVersamento(503, "VRS-500", "Storage temporaneamente non disponibile",
                                "Riprovare tra qualche minuto.") from exc
    if dimensione is None:
        raise RifiutoVersamento(404, "UPL-404", f"File caricato {fonte.nome_file!r} non trovato")
    if dimensione != fonte.dimensione:
        raise RifiutoVersamento(422, "UPL-422", f"File caricato {fonte.nome_file!r} incoerente",
                                "La dimensione registrata non corrisponde all'oggetto caricato.")
    return FileCaricato(fonte.nome_file, fonte.bucket, fonte.chiave, fonte.dimensione,
                        {k: v.lower() for k, v in fonte.impronte.items()}, fonte.testa,
                        fonte.caricamento_id)


# ── Caso d'uso ──────────────────────────────────────────────────────

async def versa(richiesta: RichiestaVersamento) -> EsitoVersamento:
    """Versa un PdV; solleva ``RifiutoVersamento`` per ogni rifiuto (codici del catalogo)."""
    indice = richiesta.indice
    identita = richiesta.identita
    tenant = identita.tenant_id
    per_nome = controlla_indice_e_fonti(indice, richiesta.fonti, richiesta.idempotency_key)
    now = richiesta.adesso
    impronta_indice = calcola_impronta_indice(indice)
    chiave_ud = richiesta.chiave_ud or normalizza_chiave_ud(indice.chiave)

    # 1. Letture preliminari (senza lock): classe, regole, idempotenza, duplicati
    try:
        async with get_async_session() as session:
            classe = await _leggi_classe(session, indice.classe_documentale, tenant)
            # R05 Q1: accordo di versamento e personalizzazione motivata dall'indice
            ambiente = await carica_ambiente(
                session, tenant_id=tenant, classe=classe, adesso=now, accordo=indice.accordo,
                override=indice.override.model_dump(mode="json") if indice.override else None)
            idem = decidi_idempotenza(richiesta.idempotency_key, ambiente, now, tenant,
                                      impronta_indice)
            esistente = await _cerca_esistente(session, tenant, idem.chiave, impronta_indice,
                                               chiave_ud)
    except SQLAlchemyError as exc:
        logger.error("versamento_db_non_disponibile", errore=str(exc)[:200])
        raise RifiutoVersamento(503, "VRS-501", "Database temporaneamente non disponibile") from exc
    except OverrideNonAmmesso as exc:
        raise RifiutoVersamento(422, "RGL-111", "Personalizzazione del versamento non ammessa",
                                str(exc)[:500], {"problemi": exc.problemi}) from exc
    except AccordoNonValido as exc:
        raise RifiutoVersamento(422, "RGL-112", "Accordo di versamento non valido",
                                str(exc), {"accordo": exc.codice, "motivo": exc.motivo}) from exc
    except (ValoreImpostazioneNonValido, ProfiloNonValido, AccordiAmbigui) as exc:
        logger.error("versamento_configurazione_regole_non_valida", tenant_id=str(tenant),
                     classe=indice.classe_documentale, errore=str(exc)[:300])
        raise RifiutoVersamento(503, "RGL-503", "Configurazione delle regole non valida",
                                "Il versamento per questa classe è sospeso finché il "
                                "conservatore non corregge la configurazione.") from exc
    if esistente:
        return _decidi_su_esistente(esistente, impronta_indice, idem)

    # 1-bis. Estensioni ammesse dai formati della classe (registro dei formati)
    controlla_estensioni(indice, ambiente, identita.canale.value)

    # 2. Regole sui metadati (prima di trasferire un solo byte e di prendere quota)
    esito_regole = CATALOGO.valuta(dati_per_regole(indice, canale=identita.canale.value),
                                   ambiente, Fase.INDICE)
    if esito_regole.blocca:
        raise rifiuto_regole(esito_regole)

    # 2-bis. Quota di PdV al giorno del piano (VD-C-099), per OGNI canale: dopo la
    # ricerca idempotente (una richiesta ripetuta riceve lo stesso PdV anche a
    # quota esaurita) e prima di trasferire un byte. 429 QTA-002 con Retry-After.
    # R03 f1 (REV-02): prenotazione ATOMICA (controllo e incremento in uno script
    # Redis), rilasciata se il PdV nuovo non nasce.
    prenotazione = await prenota_quota_versamento(identita, now)
    creato: list[str] = []  # id del PdV nuovo dopo il commit: la prenotazione è il conteggio
    try:
        esito = await _trasferisci_e_registra(richiesta, per_nome, classe, ambiente, idem,
                                              impronta_indice, chiave_ud, esito_regole, creato)
    finally:
        if not creato:
            await asyncio.shield(rilascia_quota_versamento(prenotazione))
    return esito


async def _trasferisci_e_registra(
    richiesta: RichiestaVersamento, per_nome: Mapping[str, FonteFile], classe: ClasseLetta,
    ambiente: AmbienteRegole, idem: _Idempotenza, impronta_indice: str,
    chiave_ud: ChiaveUDNormalizzata | None, esito_regole: EsitoRegole, creato: list[str],
) -> EsitoVersamento:
    """Passi 3-8 del caso d'uso: staging, controlli sui file, transazione, effetti.

    ``creato`` riceve l'id del PdV appena il PdV NUOVO è confermato nel database."""
    indice = richiesta.indice
    identita = richiesta.identita
    tenant, utente = identita.tenant_id, identita.utente_id

    # 3. Trasferimento sullo staging, controlli sui file, transazione
    pdv_id = str(uuid.uuid4())
    s3 = _s3_client()
    caricamento = _Caricamento()
    try:
        if any(f.da_caricare for f in richiesta.fonti):
            await asyncio.to_thread(_assicura_bucket, s3, _bucket_pdv())
        rgl104_blocca = Effetto(ambiente.valore("REGOLA.RGL-104.EFFETTO")) is Effetto.BLOCCA
        limite_classe = limite_byte_classe(ambiente) if rgl104_blocca else None
        caricati: dict[str, FileCaricato] = {}
        for ordine, doc in enumerate(indice.documenti):
            fonte = per_nome[doc.nome_file]
            if isinstance(fonte, FonteFlusso):
                chiave = chiave_staging(tenant, pdv_id, ordine, doc.nome_file)
                caricati[doc.nome_file] = await _carica_flusso(
                    s3, fonte, chiave=chiave, limite_classe=limite_classe, indice=indice,
                    ambiente=ambiente)
                caricamento.chiavi.append(chiave)
            elif isinstance(fonte, FileGiaCaricato):
                caricati[doc.nome_file] = await _verifica_gia_caricato(s3, fonte, tenant)
            else:  # pragma: no cover — FonteFile è chiusa su queste due realizzazioni
                raise TypeError(f"fonte non gestita: {type(fonte).__name__}")

        verificati = verifica_file(indice, caricati)
        esito_file = CATALOGO.valuta(
            dati_per_regole(indice, verificati, canale=identita.canale.value), ambiente,
            Fase.FILE)
        if esito_file.blocca:
            raise rifiuto_regole(esito_file.unisci(esito_regole))
        esito = await _registra(richiesta, classe, ambiente, idem, pdv_id, impronta_indice,
                                chiave_ud, verificati, esito_regole.unisci(esito_file))
    except BaseException:
        await _compensa(s3, caricamento, pdv_id)
        raise
    if not esito.nuovo:
        await _compensa(s3, caricamento, pdv_id)
        return esito
    creato.append(pdv_id)

    logger.info("pdv_received", pdv_id=pdv_id, tenant_id=str(tenant), canale=identita.canale.value,
                classe=indice.classe_documentale, num_files=len(verificati),
                avvisi=[a["codice"] for a in esito.avvisi])
    from libs.shared.security.audit import log_audit  # registra da sé i propri errori
    await log_audit(
        evento="pdv.versamento_ricevuto", tenant_id=str(tenant), user_id=str(utente),
        dettagli={"pdv_id": pdv_id, "classe": indice.classe_documentale,
                  "num_documenti": len(indice.documenti), "canale": identita.canale.value},
    )
    # Best effort e mai un'eccezione; con Kafka giù il cortocircuito dell'outbox
    # esce subito senza log (R04 int): l'evento resta in outbox per il relay.
    await pubblica_dopo_commit(limite=20, pdv_id=pdv_id)
    return esito


async def _registra(richiesta: RichiestaVersamento, classe: ClasseLetta, ambiente: AmbienteRegole,
                    idem: _Idempotenza, pdv_id: str, impronta_indice: str,
                    chiave_ud: ChiaveUDNormalizzata | None, verificati: list[_FileVerificato],
                    esito_regole: EsitoRegole) -> EsitoVersamento:
    """Transazione del PdV (§12.3, §12.6). Restituisce l'esito, nuovo o ripetuto."""
    indice = richiesta.indice
    tenant, utente = richiesta.identita.tenant_id, richiesta.identita.utente_id
    canale = richiesta.identita.canale.value
    now = richiesta.adesso
    bucket_docs: list[dict[str, Any]] = []
    for f in verificati:
        c = f.caricato
        bucket_docs.append({
            "doc_id": f.doc_id, "nome_file": f.nome_file, "s3_key": c.chiave, "s3_bucket": c.bucket,
            "size_bytes": c.dimensione,
            "mime": normalizza_mime(f.mime_dichiarato) or f.mime_rilevato,
            "formato_rilevato": f.mime_rilevato, "hash_sha256": c.impronte["SHA-256"],
            "impronte": dict(c.impronte),
            "hash_dichiarato": {"algoritmo": f.algoritmo, "impronta": f.impronta_dichiarata},
            "caricamento_id": c.caricamento_id,
        })
    avvisi = [avviso(r) for r in esito_regole.avvisi]
    personalizzazione = rilievo_override(ambiente)  # R05 Q1: RGL-110 nel RdV
    if personalizzazione is not None:
        avvisi.append(avviso(personalizzazione))
    if idem.avviso:
        avvisi.append(idem.avviso)
    regole_applicate = CATALOGO.fotografia(ambiente)
    impronta_richiesta = calcola_impronta_richiesta(impronta_indice, verificati)
    indice_normalizzato = indice.model_dump(mode="json")
    allegato5 = {d.id: d.parametri.model_dump(mode="json") if d.parametri else None
                 for d in indice.documenti}
    principale = next((d for d in indice.documenti if d.tipo == "PRINCIPALE"), indice.documenti[0])
    oggetto = f"{indice.classe_documentale} — {principale.nome_file}"[:500]
    rdv_xml = costruisci_rdv_preliminare(pdv_id, str(tenant), impronta_indice, now,
                                         bucket_docs, avvisi)
    idempotenza = {"chiave": idem.chiave, "derivata": idem.derivata is not None}
    evento = {
        "pdv_id": pdv_id, "tenant_id": str(tenant),
        "classe_documentale": indice.classe_documentale, "user_id": str(utente),
        "canale": canale, "tipologia_xml": classe.tipologia_xml,
        "indice_hash": impronta_indice, "num_documenti": len(indice.documenti),
        "received_at": now.isoformat(), "indice": indice_normalizzato,
        "docs_s3_keys": bucket_docs,
        "rdv_preliminare_hash": hashlib.sha256(rdv_xml.encode()).hexdigest(),
        "allegato5": allegato5, "avvisi": avvisi, "regole_applicate": regole_applicate,
    }
    meta = {
        "indice_hash": impronta_indice, "classe_documentale": indice.classe_documentale,
        "tipologia_xml": classe.tipologia_xml, "canale": canale,
        "chiave": indice.chiave.model_dump(mode="json") if indice.chiave else None,
        "num_documenti": len(indice.documenti), "indice": indice_normalizzato,
        "docs_s3_keys": bucket_docs, "rdv_preliminare": rdv_xml, "allegato5": allegato5,
        "idempotency_key": idem.chiave, "idempotenza": idempotenza,
    }

    async def cerca() -> _Esistente | None:
        async with get_async_session() as s:
            return await _cerca_esistente(s, tenant, idem.chiave, impronta_indice, chiave_ud)

    try:
        async with get_async_session() as session:
            # «Controlla poi crea» sotto lock (§12.6): due versamenti uguali in parallelo
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"versamento:{tenant}:{impronta_indice}"},
            )
            esistente = await _cerca_esistente(session, tenant, idem.chiave, impronta_indice,
                                               chiave_ud)
            if esistente is None:
                await session.execute(text(
                    "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, tipo_flusso, "
                    " user_id, tenant_id, metadata_json, errori_validazione, s3_prefix, "
                    " regole_applicate, avvisi) "
                    "VALUES (:id, :oggetto, 'RICEVUTO', :classe, 'ATTIVO', :utente, :tenant, "
                    " CAST(:meta AS jsonb), '[]'::jsonb, :prefisso, CAST(:regole AS jsonb), "
                    " CAST(:avvisi AS jsonb))"
                ), {
                    "id": uuid.UUID(pdv_id), "oggetto": oggetto, "classe": classe.id,
                    "utente": utente, "tenant": tenant,
                    "prefisso": f"{PREFISSO_STAGING_VERSAMENTI}/{tenant}/{pdv_id}",
                    "meta": json.dumps(meta, default=str),
                    "regole": json.dumps(regole_applicate, default=str),
                    "avvisi": json.dumps(avvisi, default=str),
                })
                await session.execute(text(
                    "INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
                    " impronta_richiesta, chiave_idempotenza, chiave_ud_registro, chiave_ud_anno, "
                    " chiave_ud_numero) VALUES (:pdv, :tenant, :ii, :ir, :k, :r, :a, :n)"
                ), {"pdv": uuid.UUID(pdv_id), "tenant": tenant, "ii": impronta_indice,
                    "ir": impronta_richiesta, "k": idem.chiave,
                    "r": chiave_ud.registro if chiave_ud else None,
                    "a": chiave_ud.anno if chiave_ud else None,
                    "n": chiave_ud.numero if chiave_ud else None})
                await accoda_evento(session, topic=TOPIC_PDV_RICEVUTO, chiave=pdv_id,
                                    payload=evento, tenant_id=tenant)
                for azione in richiesta.in_transazione:
                    await azione(session, pdv_id)
    except IntegrityError as exc:
        # Corsa con un'altra richiesta (stessa chiave, stesso indice o stessa chiave UD)
        esistente = await cerca()
        if esistente:
            return _decidi_su_esistente(esistente, impronta_indice, idem)
        logger.error("versamento_vincolo_violato", pdv_id=pdv_id, errore=str(exc)[:300])
        raise RifiutoVersamento(409, "VRS-123", "Versamento in conflitto con un altro") from exc
    except SQLAlchemyError as exc:
        logger.error("versamento_db_errore", pdv_id=pdv_id, errore=str(exc)[:300])
        raise RifiutoVersamento(503, "VRS-501", "Database temporaneamente non disponibile") from exc
    if esistente is not None:
        return _decidi_su_esistente(esistente, impronta_indice, idem)
    return EsitoVersamento(pdv_id, "RICEVUTO", nuovo=True, avvisi=avvisi,
                           chiave_derivata=idem.derivata is not None,
                           transizione_fino_al=idem.fino_al)


# ── Adattatore del canale REST multipart ────────────────────────────

async def versa_unita_documentaria(
    *,
    indice_json: str,
    files: Sequence[Any],
    user: Mapping[str, Any],
    idempotency_key: str | None = None,
    adesso: datetime | None = None,
    canale: Canale = Canale.REST,
) -> EsitoVersamento:
    """Canale multipart (REST e portale): indice JSON + ``UploadFile`` → ``versa``.

    Ogni ``UploadFile`` diventa una ``FonteFlusso`` con il limite per file del
    canale (``VERSAMENTO_REST_MAX_FILE_MB``) e la dimensione annunciata."""
    indice = leggi_indice(indice_json)
    identita = identita_da_utente(user, canale)
    limite = settings.versamento_rest_max_file_mb * MB
    fonti = [
        FonteFlusso(nome_file=f.filename or "", flusso=f, content_type=f.content_type,
                    dimensione_annunciata=getattr(f, "size", None), limite_byte=limite)
        for f in files
    ]
    return await versa(RichiestaVersamento(
        indice=indice, fonti=fonti, identita=identita, idempotency_key=idempotency_key,
        adesso=adesso or datetime.now(UTC),
    ))
