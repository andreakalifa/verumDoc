# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Pulizia dello staging dopo la conservazione (R03 f1, REV-08).

Servizio: gateway. Riferimenti: piano R03–R05 §2 (300 GB/giorno di ingresso) e
§4.2 (storage); REGOLE_SVILUPPO §10.3 (impronte sui byte), §12.9 (job unici),
§22.3 (rete di sicurezza); rapporto R03 i2 (ciclo di vita dei bucket).

Il versamento lascia i file del PdV nel bucket di STAGING (mai conservativo):

  - ``versamenti/{tenant}/{pdv}/NNNN-<nome>``: file caricati in streaming;
  - ``caricamenti/{tenant}/{sessione}/dati``: file delle sessioni di caricamento
    usate dal PdV (la pulizia delle sessioni NON li tocca: appartengono al PdV).

aip-builder li COPIA nello storage conservativo (WORM). Senza questo job le
copie di transito restavano per sempre (~110 TB l'anno all'obiettivo di 1M
documenti/giorno). Il job elimina le copie di un PdV quando:

  1. il PdV è nel PdA o conservato (``pdv.stato`` IN_BUILD o CONSERVATO, con un
     PdA in stato SU_STORAGE, IN_CATENA, CONSERVATO o ESIBIZIONE) da almeno
     ``STAGING_PULIZIA_ATTESA_ORE``;
  2. OGNI file di transito ha la sua copia nel PdA VERIFICATA: riga in
     ``documenti`` del PdA con la stessa impronta SHA-256 e la stessa dimensione
     (impronta calcolata da aip-builder sui byte copiati) e oggetto presente nello
     storage del PdA con la stessa dimensione (HEAD; se lo storage restituisce
     la checksum SHA-256 dell'oggetto intero, anche quella deve coincidere);
  3. la chiave è davvero di staging: bucket di staging del servizio e prefisso
     ``versamenti/{tenant}/`` o ``caricamenti/{tenant}/`` dell'ente del PdV.

Se una verifica non riesce il PdV resta com'è: nessuna eliminazione «sulla
fiducia». R03 f3 (REV-N1): il PdV si RINVIA da solo, con attesa crescente
(``STAGING_PULIZIA_RINVIO_MINUTI`` × 2^(tentativi-1), al più
``STAGING_PULIZIA_RINVIO_MAX_ORE``; colonne ``pdv.staging_verifica_*``,
migrazione 20261021_024). Dopo ``STAGING_PULIZIA_TENTATIVI_MAX`` tentativi, o
subito per un motivo permanente (nessun file di transito registrato, impronta o
dimensione assenti come nei PdV di R02, chiave fuori dallo staging), il PdV è
BLOCCATO: esce dai giri, si registra un errore nel log
(``staging_pulizia_bloccata``) e la metrica ``verumdoc_staging_pulizia_pdv_bloccati``
del /metrics del gateway lo conta. Elenco e rimessa in coda:
``python -m app.services.pulizia_staging --bloccati`` / ``--sblocca <pdv_id>…``.
Un PdV non ancora pronto (PdA assente o più recente dell'attesa) si rinvia al
momento in cui potrà esserlo, senza contare un tentativo.

I candidati si leggono in ordine di rinvio, fra quelli già scaduti, sull'indice
parziale ``idx_pdv_staging_da_verificare`` (staging_verifica_prossima, id): un
PdV non verificabile va in fondo e non impedisce di raggiungere quelli dopo di
lui, e il costo di un giro dipende solo da ``lotto × lotti`` (i PdV rinviati nel
futuro e i bloccati non si leggono), anche con milioni di righe.

Idempotente e a lotti: ``pdv.staging_eliminato_il`` marca i PdV puliti (che
escono dall'indice); eliminare un oggetto già eliminato non è un errore. Più
repliche: un solo giro alla volta (lock consultivo di transazione per lotto,
``pg_try_advisory_xact_lock``): le altre repliche saltano il lotto invece di
rifare le stesse verifiche.

Ciclo di vita del bucket (rapporto i2, ``infra/terraform/modules/s3``): lo
staging NON ha versioning (l'eliminazione libera davvero lo spazio) e la sola
regola di ciclo di vita annulla i multipart incompleti dopo 2 giorni; non c'è
scadenza degli oggetti completi, quindi questo job è l'unico che li elimina.
Se in futuro si aggiungesse una scadenza degli oggetti, dovrebbe essere più
lunga del tempo massimo fra versamento e PdA su storage (ritentativi e coda di
scarto compresi), altrimenti aip-builder non troverebbe più i file.

PdV RIFIUTATI o in ERRORE (R04, VD-C-104, decisione VD-P-36): le copie di
transito restano perché «Rielabora» (REGOLE §11.5, code di scarto VD-C-075) può
ancora servirsene, ma solo per ``STAGING_SCARTATI_GIORNI`` (predefinito 90)
dall'ingresso in ERRORE (``pdv.data_aggiornamento``). R07 (D15): è
un'impostazione della console PER ENTE (30-180 giorni; valore del conservatore
come predefinito), letta riga per riga nella stessa query; l'evento porta il
collegamento «Rielabora» della console. Lo stesso giro
(``scadenza_staging_scartati``):

  1. ``STAGING_SCARTATI_PREAVVISO_GIORNI`` (predefinito 7) prima della scadenza
     accoda nell'outbox l'evento per il cliente ``versamento.staging_scaduto``
     (payload: PdV, ente, data di scadenza) e marca ``pdv.staging_avvisato_il``
     (un solo avviso per PdV);
  2. alla scadenza, e comunque non prima di un preavviso pieno dall'avviso,
     elimina le copie di transito (solo chiavi dello staging dell'ente) e marca
     ``pdv.staging_eliminato_il``: da quel momento il PdV è «non rielaborabile»
     (le code di scarto lo dicono nel piano di rielaborazione). Una chiave fuori
     dallo staging dell'ente blocca il PdV (``staging_verifica_bloccata_il``,
     elenco ``--bloccati``) invece di eliminarla.
Letture sull'indice parziale ``idx_pdv_staging_scartati`` (migrazione 20261023_029)
a lotti, sotto lo stesso lock consultivo dei giri di pulizia.

Uso come processo: ``python -m app.services.pulizia_staging [--prova | --bloccati |
--sblocca <pdv_id>…]``; nel
gateway gira nel lavoratore delle sessioni (``STAGING_PULIZIA_ATTIVO``).
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from app.config import settings
from libs.shared.db import get_async_session
from libs.shared.db.ambito import con_ambito_conservatore
from libs.shared.outbox import accoda_evento
from libs.shared.parametri import SQL_GIORNI_STAGING, parametri_staging
from libs.shared.storage.flusso import elimina_oggetti

logger = structlog.get_logger(__name__)

#: Stati del PdV con il PdA costruito o in costruzione (aip-builder → hashchain).
STATI_PDV_CANDIDATI = ("IN_BUILD", "CONSERVATO")
#: Stati del PdA con i file già copiati e verificati nello storage conservativo.
STATI_PDA_SU_STORAGE = ("SU_STORAGE", "IN_CATENA", "CONSERVATO", "ESIBIZIONE")
#: Chiave del lock consultivo: un solo lotto alla volta fra le repliche.
LOCK_PULIZIA = "pulizia_staging"

#: Candidati di un lotto (R03 f3, REV-N1): PdV nel PdA o conservati, staging da
#: pulire, non bloccati e con il rinvio SCADUTO, in ordine di rinvio. Scansione
#: d'intervallo sull'indice parziale ``idx_pdv_staging_da_verificare`` fino ad
#: «adesso»: i PdV rinviati nel futuro non si leggono, quindi il costo di un giro
#: dipende dal lotto, non dal numero di PdV in attesa o non verificabili.
#: ``pda_il``: il PdA su storage più vecchio del PdV (None se non c'è ancora).
_CANDIDATI = text(
    "SELECT p.id, p.tenant_id, p.staging_verifica_prossima AS prossima, "
    " p.staging_verifica_tentativi AS tentativi, "
    " p.metadata_json -> 'docs_s3_keys' AS docs, "
    " (SELECT min(a.created_at) FROM public.aip a WHERE a.pdv_id = p.id "
    "   AND a.stato IN ('SU_STORAGE', 'IN_CATENA', 'CONSERVATO', 'ESIBIZIONE')) AS pda_il "
    "FROM public.pdv p "
    "WHERE p.staging_eliminato_il IS NULL AND p.staging_verifica_bloccata_il IS NULL "
    " AND p.stato IN ('IN_BUILD', 'CONSERVATO') "
    " AND p.staging_verifica_prossima <= :adesso "
    " AND (p.staging_verifica_prossima, p.id) > (:dopo_t, :dopo_id) "
    "ORDER BY p.staging_verifica_prossima, p.id LIMIT :n"
)

#: PdV non ancora pronti (PdA assente o troppo recente): nuovo rinvio, senza tentativo.
_RINVIA_SENZA_TENTATIVO = text(
    "UPDATE public.pdv p SET staging_verifica_prossima = v.prossima "
    "FROM unnest(CAST(:ids AS uuid[]), CAST(:prossime AS timestamptz[])) AS v(id, prossima) "
    "WHERE p.id = v.id AND p.staging_eliminato_il IS NULL"
)

#: Verifiche non riuscite: tentativo in più, rinvio crescente o blocco.
_RINVIA_CON_TENTATIVO = text(
    "UPDATE public.pdv p SET staging_verifica_tentativi = v.tentativi, "
    " staging_verifica_prossima = v.prossima, staging_verifica_motivo = v.motivo, "
    " staging_verifica_bloccata_il = v.bloccata "
    "FROM unnest(CAST(:ids AS uuid[]), CAST(:tentativi AS smallint[]), "
    " CAST(:prossime AS timestamptz[]), CAST(:motivi AS varchar[]), "
    " CAST(:bloccate AS timestamptz[])) AS v(id, tentativi, prossima, motivo, bloccata) "
    "WHERE p.id = v.id AND p.staging_eliminato_il IS NULL"
)

_COPIE = text(
    "SELECT a.pdv_id, a.s3_bucket, d.s3_key, d.hash_sha256, d.dimensione_bytes "
    "FROM public.aip a JOIN public.documenti d ON d.aip_id = a.id "
    "WHERE a.pdv_id = ANY(CAST(:ids AS uuid[])) "
    " AND a.stato IN ('SU_STORAGE', 'IN_CATENA', 'CONSERVATO', 'ESIBIZIONE')"
)


@dataclass(frozen=True)
class _Copia:
    bucket: str
    chiave: str
    sha256: str
    dimensione: int


@dataclass
class EsitoPulizia:
    """Esito di un giro: PdV puliti, PdV rinviati (verifica non riuscita, con il motivo),
    fra questi i bloccati (motivo permanente o tentativi esauriti), PdV non ancora
    pronti (PdA assente o troppo recente: rinviati senza tentativo), oggetti eliminati."""

    puliti: list[str] = field(default_factory=list)
    rinviati: dict[str, str] = field(default_factory=dict)
    bloccati: dict[str, str] = field(default_factory=dict)
    non_pronti: int = 0
    oggetti: list[str] = field(default_factory=list)
    occupato: bool = False
    #: R04 (VD-C-104): avvisi e scadenze dei PdV in ERRORE dello stesso giro
    scadenza: EsitoScadenza | None = None


class MotivoPermanente(str):
    """Motivo di rinvio che un nuovo tentativo non può cambiare (blocca subito)."""


def attesa_rinvio(tentativi: int) -> timedelta:
    """Attesa prima del tentativo successivo: RINVIO_MINUTI × 2^(tentativi-1),
    al più RINVIO_MAX_ORE (impostazioni)."""
    base = timedelta(minutes=settings.staging_pulizia_rinvio_minuti)
    massimo = timedelta(hours=settings.staging_pulizia_rinvio_max_ore)
    esponente = min(max(tentativi - 1, 0), 30)
    return min(base * (2 ** esponente), massimo)


def _sha256_da_checksum(valore: str | None) -> str | None:
    """Checksum S3 SHA-256 dell'oggetto intero (base64) in esadecimale; None se assente
    o composita (``…-N``, multipart: non è l'impronta del file)."""
    if not valore or "-" in valore:
        return None
    try:
        return base64.b64decode(valore).hex()
    except ValueError:
        return None


def _copia_verificata(s3: Any, copia: _Copia) -> str | None:
    """None se la copia nel PdA c'è con la dimensione (e la checksum) attese; altrimenti
    il motivo. Eseguita in un thread (chiamata bloccante allo storage)."""
    try:
        testa = s3.head_object(Bucket=copia.bucket, Key=copia.chiave, ChecksumMode="ENABLED")
    except Exception as exc:  # noqa: BLE001 — copia assente o storage giù: non si elimina
        return f"copia nel PdA non leggibile ({type(exc).__name__})"
    if int(testa.get("ContentLength", -1)) != copia.dimensione:
        return "dimensione della copia nel PdA diversa"
    calcolata = _sha256_da_checksum(testa.get("ChecksumSHA256"))
    if calcolata is not None and calcolata != copia.sha256.lower():
        return "checksum della copia nel PdA diversa"
    return None


def _voci(docs: Any) -> list[Mapping[str, Any]]:
    if isinstance(docs, str):
        docs = json.loads(docs)
    return [d for d in (docs or []) if isinstance(d, Mapping)]


def _da_eliminare(pdv_id: uuid.UUID, tenant: uuid.UUID, docs: Sequence[Mapping[str, Any]],
                  copie: Sequence[_Copia], bucket_staging: str
                  ) -> tuple[list[tuple[str, _Copia]], str | None]:
    """Coppie (chiave di staging, copia nel PdA) da verificare, o il motivo per non farlo."""
    if not docs:
        return [], MotivoPermanente("nessun file di transito registrato nel PdV")
    prefissi = (f"versamenti/{tenant}/", f"caricamenti/{tenant}/")
    coppie: list[tuple[str, _Copia]] = []
    for d in docs:
        chiave, bucket = str(d.get("s3_key") or ""), str(d.get("s3_bucket") or "")
        sha = str(d.get("hash_sha256") or "").lower()
        dim = d.get("size_bytes")
        if bucket != bucket_staging or not chiave.startswith(prefissi) \
                or ".." in chiave.split("/"):
            return [], MotivoPermanente(f"chiave {chiave!r} fuori dallo staging dell'ente")
        if not sha or not isinstance(dim, int):
            # PdV versati prima che il PdV registrasse impronta e dimensione dei file
            # di transito (R02): la copia non si può verificare, mai
            return [], MotivoPermanente(f"impronta o dimensione di {chiave!r} non registrate "
                                        "nel PdV: copia non verificabile")
        copia = next((c for c in copie if c.sha256.lower() == sha and c.dimensione == dim), None)
        if copia is None:
            return [], f"nessuna copia nel PdA per {chiave!r} (impronta e dimensione)"
        coppie.append((chiave, copia))
    return coppie, None


def _rinvio(tentativi_prima: int, motivo: str, adesso: datetime
            ) -> tuple[int, datetime, datetime | None]:
    """(tentativi, prossima verifica, bloccato il) dopo una verifica non riuscita."""
    tentativi = tentativi_prima + 1
    if isinstance(motivo, MotivoPermanente) or tentativi >= settings.staging_pulizia_tentativi_max:
        return tentativi, adesso, adesso
    return tentativi, adesso + attesa_rinvio(tentativi), None


async def _lotto(s3: Any, *, bucket_staging: str, adesso: datetime, attesa: timedelta,
                 dopo: tuple[datetime, uuid.UUID], dimensione: int, prova: bool,
                 esito: EsitoPulizia) -> tuple[datetime, uuid.UUID] | None:
    """Un lotto sotto lock consultivo. Restituisce il cursore per il lotto dopo, o None."""
    soglia = adesso - attesa
    async with get_async_session() as session:
        preso = (await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": LOCK_PULIZIA})).scalar_one()
        if not preso:
            esito.occupato = True
            return None
        righe = (await session.execute(_CANDIDATI, {
            "adesso": adesso, "dopo_t": dopo[0], "dopo_id": dopo[1], "n": dimensione,
        })).mappings().all()
        if not righe:
            return None

        # PdV non ancora pronti: si torna a guardarli quando potranno esserlo
        # (PdA troppo recente: alla fine dell'attesa; PdA assente: dopo il rinvio
        # minimo), senza contare un tentativo
        non_pronti: dict[uuid.UUID, datetime] = {}
        pronti = []
        for r in righe:
            if r["pda_il"] is None:
                non_pronti[r["id"]] = adesso + attesa_rinvio(1)
            elif r["pda_il"] > soglia:
                non_pronti[r["id"]] = r["pda_il"] + attesa
            else:
                pronti.append(r)
        esito.non_pronti += len(non_pronti)

        ids = [r["id"] for r in pronti]
        copie: dict[uuid.UUID, list[_Copia]] = {}
        if ids:
            for c in (await session.execute(_COPIE, {"ids": [str(i) for i in ids]})).mappings():
                copie.setdefault(c["pdv_id"], []).append(
                    _Copia(c["s3_bucket"], c["s3_key"], c["hash_sha256"],
                           int(c["dimensione_bytes"])))

        falliti: dict[uuid.UUID, str] = {}
        piani: dict[uuid.UUID, list[tuple[str, _Copia]]] = {}
        for r in pronti:
            coppie, motivo = _da_eliminare(r["id"], r["tenant_id"], _voci(r["docs"]),
                                           copie.get(r["id"], []), bucket_staging)
            if motivo:
                falliti[r["id"]] = motivo
            else:
                piani[r["id"]] = coppie

        limite = asyncio.Semaphore(settings.staging_pulizia_verifiche_parallele)

        async def verifica(copia: _Copia) -> str | None:
            async with limite:
                return await asyncio.to_thread(_copia_verificata, s3, copia)

        # Tutte le verifiche del lotto in parallelo (al più N alla volta)
        elenco = [(pdv_id, c) for pdv_id, coppie in piani.items() for _, c in coppie]
        motivi = await asyncio.gather(*(verifica(c) for _, c in elenco))
        for (pdv_id, _), motivo in zip(elenco, motivi, strict=True):
            if motivo and pdv_id in piani:
                falliti[pdv_id] = motivo
                del piani[pdv_id]

        # Rinvio per riga (REV-N1): ogni PdV non verificato va in fondo con la sua
        # attesa crescente, o si blocca; i PdV dopo di lui restano raggiungibili
        tentativi_prima = {r["id"]: int(r["tentativi"]) for r in pronti}
        rinvii = {pdv_id: _rinvio(tentativi_prima[pdv_id], motivo, adesso)
                  for pdv_id, motivo in falliti.items()}
        for pdv_id, motivo in falliti.items():
            esito.rinviati[str(pdv_id)] = str(motivo)
            if rinvii[pdv_id][2] is not None:
                esito.bloccati[str(pdv_id)] = str(motivo)
                logger.error("staging_pulizia_bloccata", pdv_id=str(pdv_id), motivo=str(motivo),
                             tentativi=rinvii[pdv_id][0], prova=prova)

        chiavi = sorted({k for coppie in piani.values() for k, _ in coppie})
        if chiavi and not prova:
            await elimina_oggetti(s3, bucket_staging, chiavi)
        if not prova:
            if piani:
                puliti = [str(i) for i in piani]
                await session.execute(text(
                    "UPDATE public.pdv SET staging_eliminato_il = :ora "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) AND staging_eliminato_il IS NULL"
                ), {"ora": adesso, "ids": puliti})
                await session.execute(text(
                    "UPDATE public.caricamenti SET storage_eliminato_il = :ora "
                    "WHERE pdv_id = ANY(CAST(:ids AS uuid[])) AND storage_eliminato_il IS NULL"
                ), {"ora": adesso, "ids": puliti})
            if non_pronti:
                await session.execute(_RINVIA_SENZA_TENTATIVO, {
                    "ids": [str(i) for i in non_pronti], "prossime": list(non_pronti.values())})
            if rinvii:
                await session.execute(_RINVIA_CON_TENTATIVO, {
                    "ids": [str(i) for i in rinvii],
                    "tentativi": [min(t, 32_767) for t, _, _ in rinvii.values()],
                    "prossime": [p for _, p, _ in rinvii.values()],
                    "motivi": [str(falliti[i])[:500] for i in rinvii],
                    "bloccate": [b for _, _, b in rinvii.values()],
                })
        esito.puliti.extend(str(i) for i in piani)
        esito.oggetti.extend(chiavi)
        ultima = righe[-1]
        if len(righe) < dimensione:
            return None
        return ultima["prossima"], ultima["id"]


@con_ambito_conservatore("pulizia dello staging dei PdV conservati di tutti gli enti")
async def pulisci_staging(*, s3: Any, bucket_staging: str, adesso: datetime,
                          attesa: timedelta | None = None, lotto: int | None = None,
                          lotti: int | None = None, prova: bool = False,
                          scadenza: bool = True) -> EsitoPulizia:
    """Un giro: al più ``lotti`` lotti da ``lotto`` PdV (predefiniti dalle impostazioni),
    poi (``scadenza``) avvisi e scadenza dello staging dei PdV in ERRORE (VD-C-104).

    ``prova``: verifica ed elenca senza eliminare né marcare (nemmeno i rinvii)."""
    attesa = attesa if attesa is not None else timedelta(hours=settings.staging_pulizia_attesa_ore)
    dimensione = lotto or settings.staging_pulizia_lotto
    massimo = lotti or settings.staging_pulizia_lotti_per_giro
    esito = EsitoPulizia()
    cursore: tuple[datetime, uuid.UUID] | None = (datetime.min.replace(tzinfo=adesso.tzinfo),
                                                  uuid.UUID(int=0))
    for _ in range(massimo):
        assert cursore is not None
        cursore = await _lotto(s3, bucket_staging=bucket_staging, adesso=adesso, attesa=attesa,
                               dopo=cursore, dimensione=dimensione, prova=prova, esito=esito)
        if cursore is None:
            break
    if esito.puliti or esito.rinviati or esito.non_pronti:
        logger.info("staging_pulito", pdv=len(esito.puliti), oggetti=len(esito.oggetti),
                    rinviati=len(esito.rinviati), bloccati=len(esito.bloccati),
                    non_pronti=esito.non_pronti, prova=prova)
    for pdv_id, motivo in [(k, v) for k, v in esito.rinviati.items()
                           if k not in esito.bloccati][:50]:
        logger.warning("staging_pulizia_rinviata", pdv_id=pdv_id, motivo=motivo)
    if scadenza and IMPOSTAZIONI_SCARTATI.attivo:
        esito.scadenza = await scadenza_staging_scartati(
            s3=s3, bucket_staging=bucket_staging, adesso=adesso, prova=prova)
    return esito


# ── PdV in ERRORE: avviso e scadenza dello staging (R04, VD-C-104, VD-P-36) ───

#: Evento per il cliente (topic = tipo dell'evento, come gli altri eventi R03).
TOPIC_STAGING_SCADUTO = "versamento.staging_scaduto"


class ImpostazioniStagingScartati(BaseSettings):
    """Variabili ``STAGING_SCARTATI_*``: conservazione delle copie di transito dei
    PdV rifiutati o in ERRORE (VD-P-36: 90 giorni con avviso al cliente).
    Le stesse variabili le legge dashboard-api per dire se un PdV è rielaborabile."""

    model_config = SettingsConfigDict(env_prefix="STAGING_SCARTATI_", case_sensitive=False,
                                      extra="ignore")

    attivo: bool = True
    # R07 (D15): STAGING_SCARTATI_GIORNI tolta: impostazione della console per ente
    preavviso_giorni: int = Field(7, ge=0, le=365)
    lotto: int = Field(500, ge=1, le=5_000)
    lotti_per_giro: int = Field(20, ge=1, le=1_000)


IMPOSTAZIONI_SCARTATI = ImpostazioniStagingScartati()


@dataclass
class EsitoScadenza:
    """PdV avvisati, PdV con lo staging scaduto ed eliminato, PdV bloccati (chiave
    estranea), oggetti eliminati; ``occupato`` se un'altra replica sta lavorando."""

    avvisati: list[str] = field(default_factory=list)
    scaduti: list[str] = field(default_factory=list)
    bloccati: dict[str, str] = field(default_factory=dict)
    oggetti: list[str] = field(default_factory=list)
    occupato: bool = False


def scadenza_staging(data_errore: datetime, giorni: int) -> datetime:
    """Istante in cui scadono le copie di transito di un PdV entrato in ERRORE
    (``giorni``: ``STAGING_SCARTATI_GIORNI`` dell'ente)."""
    return data_errore + timedelta(days=giorni)


def collegamento_rielabora(pdv_id: str) -> str:
    """D15: pagina della console da cui si rielabora il versamento rifiutato. R07
    (richiesta del committente): l'ente rielabora da sé dai propri versamenti
    rifiutati (prima: la coda di scarto, riservata al conservatore)."""
    return f"{settings.base_url.rstrip('/')}/versamenti?stato=ERRORE&pdv_id={pdv_id}"


_DA_AVVISARE = text(
    "SELECT p.id, p.tenant_id, p.data_aggiornamento, g.giorni FROM public.pdv p "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
    + SQL_GIORNI_STAGING +
    "WHERE p.stato = 'ERRORE' AND p.staging_eliminato_il IS NULL "
    " AND p.data_aggiornamento <= :soglia "
    " AND p.data_aggiornamento <= CAST(:adesso AS timestamptz) - make_interval(days => g.giorni) "
    "+ CAST(:preavviso AS interval) "
    " AND (p.data_aggiornamento, p.id) > (:dopo_t, :dopo_id) "
    " AND p.staging_avvisato_il IS NULL AND p.staging_verifica_bloccata_il IS NULL "
    "ORDER BY p.data_aggiornamento, p.id LIMIT :n FOR UPDATE OF p SKIP LOCKED"
)

_DA_ELIMINARE = text(
    "SELECT p.id, p.tenant_id, p.data_aggiornamento, "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
    " p.metadata_json -> 'docs_s3_keys' AS docs, g.giorni FROM public.pdv p "
    + SQL_GIORNI_STAGING +
    "WHERE p.stato = 'ERRORE' AND p.staging_eliminato_il IS NULL "
    " AND p.data_aggiornamento <= :soglia "
    " AND p.data_aggiornamento <= CAST(:adesso AS timestamptz) - make_interval(days => g.giorni) "
    " AND (p.data_aggiornamento, p.id) > (:dopo_t, :dopo_id) "
    " AND p.staging_avvisato_il <= :avvisato_entro AND p.staging_verifica_bloccata_il IS NULL "
    "ORDER BY p.data_aggiornamento, p.id LIMIT :n FOR UPDATE OF p SKIP LOCKED"
)


def _chiavi_scartato(tenant: uuid.UUID, docs: Sequence[Mapping[str, Any]],
                     bucket_staging: str) -> tuple[list[str], str | None]:
    """Chiavi di staging di un PdV in ERRORE da eliminare, o il motivo per non farlo."""
    prefissi = (f"versamenti/{tenant}/", f"caricamenti/{tenant}/")
    chiavi: list[str] = []
    for d in docs:
        chiave, bucket = str(d.get("s3_key") or ""), str(d.get("s3_bucket") or "")
        if not chiave:
            continue
        if bucket != bucket_staging or not chiave.startswith(prefissi) \
                or ".." in chiave.split("/"):
            return [], f"chiave {chiave!r} fuori dallo staging dell'ente: copia non eliminata"
        chiavi.append(chiave)
    return chiavi, None


@con_ambito_conservatore("scadenza dello staging dei PdV rifiutati di tutti gli enti")
async def scadenza_staging_scartati(*, s3: Any, bucket_staging: str, adesso: datetime,
                                    prova: bool = False,
                                    imp: ImpostazioniStagingScartati | None = None
                                    ) -> EsitoScadenza:
    """Avvisi e scadenza dello staging dei PdV in ERRORE (vedi docstring del modulo).

    Idempotente: un PdV avvisato non si riavvisa, uno eliminato esce dall'indice.
    ``prova``: elenca senza scrivere né eliminare."""
    imp = imp or IMPOSTAZIONI_SCARTATI
    esito = EsitoScadenza()
    limiti: dict[str, Any] = {**await parametri_staging(), "adesso": adesso}
    durata_minima = timedelta(days=int(limiti["minimo"]))
    preavviso = timedelta(days=imp.preavviso_giorni)
    inizio = (datetime.min.replace(tzinfo=adesso.tzinfo), uuid.UUID(int=0))

    # 1. avvisi al cliente (evento nell'outbox, stessa transazione della marca)
    dopo = inizio
    for _ in range(imp.lotti_per_giro):
        async with get_async_session() as session:
            if not await _lock(session):
                esito.occupato = True
                return esito
            righe = (await session.execute(_DA_AVVISARE, {
                **limiti, "preavviso": preavviso, "soglia": adesso - durata_minima + preavviso,
                "dopo_t": dopo[0], "dopo_id": dopo[1], "n": imp.lotto})).mappings().all()
            for r in righe:
                scade = scadenza_staging(r["data_aggiornamento"], int(r["giorni"]))
                esito.avvisati.append(str(r["id"]))
                if prova:
                    continue
                await accoda_evento(session, topic=TOPIC_STAGING_SCADUTO, chiave=str(r["id"]),
                                    tenant_id=r["tenant_id"], payload={
                                        "tipo": TOPIC_STAGING_SCADUTO, "pdv_id": str(r["id"]),
                                        "tenant_id": str(r["tenant_id"]), "stato": "ERRORE",
                                        "scade_il": scade.isoformat(),
                                        "giorni_conservazione": int(r["giorni"]),
                                        "preavviso_giorni": imp.preavviso_giorni,
                                        "avvisato_il": adesso.isoformat(),
                                        "rielabora": collegamento_rielabora(str(r["id"]))})
            if righe and not prova:
                await session.execute(text(
                    "UPDATE public.pdv SET staging_avvisato_il = :ora "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) AND staging_avvisato_il IS NULL"
                ), {"ora": adesso, "ids": [str(r["id"]) for r in righe]})
        if len(righe) < imp.lotto:
            break
        dopo = (righe[-1]["data_aggiornamento"], righe[-1]["id"])

    # 2. scadenza: eliminazione delle copie di transito, PdV non più rielaborabile
    dopo = inizio
    for _ in range(imp.lotti_per_giro):
        async with get_async_session() as session:
            if not await _lock(session):
                esito.occupato = True
                return esito
            righe = (await session.execute(_DA_ELIMINARE, {
                **limiti, "soglia": adesso - durata_minima, "avvisato_entro": adesso - preavviso,
                "dopo_t": dopo[0], "dopo_id": dopo[1], "n": imp.lotto})).mappings().all()
            scaduti: list[str] = []
            bloccati: dict[str, str] = {}
            chiavi: list[str] = []
            for r in righe:
                proprie, motivo = _chiavi_scartato(r["tenant_id"], _voci(r["docs"]),
                                                   bucket_staging)
                if motivo:
                    bloccati[str(r["id"])] = motivo
                    logger.error("staging_scadenza_bloccata", pdv_id=str(r["id"]), motivo=motivo)
                    continue
                scaduti.append(str(r["id"]))
                chiavi.extend(proprie)
            if not prova:
                if chiavi:
                    await elimina_oggetti(s3, bucket_staging, sorted(set(chiavi)))
                if scaduti:
                    await session.execute(text(
                        "UPDATE public.pdv SET staging_eliminato_il = :ora "
                        "WHERE id = ANY(CAST(:ids AS uuid[])) AND staging_eliminato_il IS NULL"
                    ), {"ora": adesso, "ids": scaduti})
                    await session.execute(text(
                        "UPDATE public.caricamenti SET storage_eliminato_il = :ora "
                        "WHERE pdv_id = ANY(CAST(:ids AS uuid[])) "
                        " AND storage_eliminato_il IS NULL"), {"ora": adesso, "ids": scaduti})
                if bloccati:
                    await session.execute(text(
                        "UPDATE public.pdv p SET staging_verifica_bloccata_il = :ora, "
                        " staging_verifica_motivo = v.motivo "
                        "FROM unnest(CAST(:ids AS uuid[]), CAST(:motivi AS varchar[])) "
                        " AS v(id, motivo) WHERE p.id = v.id"
                    ), {"ora": adesso, "ids": list(bloccati),
                        "motivi": [m[:500] for m in bloccati.values()]})
            esito.scaduti.extend(scaduti)
            esito.bloccati.update(bloccati)
            esito.oggetti.extend(sorted(set(chiavi)))
        if len(righe) < imp.lotto:
            break
        dopo = (righe[-1]["data_aggiornamento"], righe[-1]["id"])

    if esito.avvisati or esito.scaduti or esito.bloccati:
        logger.info("staging_scartati", avvisati=len(esito.avvisati),
                    scaduti=len(esito.scaduti), bloccati=len(esito.bloccati),
                    oggetti=len(esito.oggetti), prova=prova)
    return esito


async def _lock(session: Any) -> bool:
    """Stesso lock consultivo dei lotti di pulizia: un solo lavoro alla volta."""
    return bool((await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": LOCK_PULIZIA})).scalar_one())


# ── PdV bloccati: elenco, rimessa in coda, metrica (R03 f3, REV-N1) ───

@con_ambito_conservatore("pulizia dello staging: PdV bloccati (operatore del conservatore)")
async def elenca_bloccati(limite: int = 100) -> list[dict[str, Any]]:
    """PdV esclusi dalla pulizia (dal più vecchio), con motivo e tentativi."""
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT id::text AS pdv_id, tenant_id::text AS tenant_id, "
            " staging_verifica_bloccata_il AS bloccato_il, staging_verifica_tentativi AS "
            " tentativi, staging_verifica_motivo AS motivo FROM public.pdv "
            "WHERE staging_eliminato_il IS NULL AND staging_verifica_bloccata_il IS NOT NULL "
            "ORDER BY staging_verifica_bloccata_il LIMIT :n"), {"n": limite})).mappings().all()
    return [dict(r) for r in righe]


@con_ambito_conservatore("pulizia dello staging: sblocco (operatore del conservatore)")
async def sblocca(pdv_ids: Sequence[str], adesso: datetime) -> int:
    """Rimette in coda PdV bloccati (dopo aver corretto la causa): tentativi azzerati,
    verifica al prossimo giro. Restituisce quanti PdV sono stati rimessi in coda."""
    async with get_async_session() as session:
        risultato = await session.execute(text(
            "UPDATE public.pdv SET staging_verifica_bloccata_il = NULL, "
            " staging_verifica_tentativi = 0, staging_verifica_prossima = :ora "
            "WHERE id = ANY(CAST(:ids AS uuid[])) AND staging_eliminato_il IS NULL "
            " AND staging_verifica_bloccata_il IS NOT NULL"),
            {"ora": adesso, "ids": [str(uuid.UUID(i)) for i in pdv_ids]})
    n = int(risultato.rowcount or 0)
    logger.info("staging_pulizia_sbloccati", pdv=n)
    return n


@con_ambito_conservatore("pulizia dello staging: metrica dei PdV bloccati")
async def conta_bloccati(tetto: int | None = None) -> int:
    """PdV bloccati, contati sull'indice parziale e fermati a ``tetto``."""
    tetto = tetto or settings.staging_pulizia_metriche_tetto
    async with get_async_session() as session:
        return int((await session.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM public.pdv "
            "WHERE staging_eliminato_il IS NULL AND staging_verifica_bloccata_il IS NOT NULL "
            "LIMIT :n) AS b"), {"n": tetto})).scalar_one())


class MetrichePuliziaStaging:
    """Collector Prometheus del /metrics del gateway: PdV bloccati dalla pulizia.

    La pulizia gira in un CronJob (processo breve, non raccolto da Prometheus):
    la metrica la espongono le repliche API leggendo il database al più ogni
    ``STAGING_PULIZIA_METRICHE_SECONDI``. La raccolta è sincrona: restituisce
    l'ultimo valore letto e, se è vecchio, avvia la lettura in sottofondo
    (-1 finché non c'è una prima lettura riuscita)."""

    def __init__(self) -> None:
        self.bloccati = -1
        self.letto_il = 0.0
        self._in_corso: asyncio.Task[None] | None = None

    async def aggiorna(self) -> None:
        try:
            self.bloccati = await conta_bloccati()
        except Exception as exc:  # noqa: BLE001 — metrica: il valore precedente resta
            logger.warning("staging_pulizia_metrica_non_letta", errore=type(exc).__name__)
        finally:
            self.letto_il = time.monotonic()

    def _forse_aggiorna(self) -> None:
        if time.monotonic() - self.letto_il < settings.staging_pulizia_metriche_secondi \
                and self.letto_il:
            return
        if self._in_corso is not None and not self._in_corso.done():
            return
        try:
            self._in_corso = asyncio.get_running_loop().create_task(self.aggiorna())
        except RuntimeError:  # raccolta fuori dal ciclo asyncio: solo il valore precedente
            return

    def collect(self) -> Iterator[Any]:
        from prometheus_client.core import GaugeMetricFamily

        self._forse_aggiorna()
        g = GaugeMetricFamily("verumdoc_staging_pulizia_pdv_bloccati",
                              "PdV con le copie di transito escluse dalla pulizia dello staging "
                              "(verifica non riuscita: motivo permanente o tentativi esauriti)")
        g.add_metric([], float(self.bloccati))
        yield g


def registra_metriche(registro: Any) -> MetrichePuliziaStaging:
    """Registra il collector dei PdV bloccati nel registro delle metriche del servizio."""
    collector = MetrichePuliziaStaging()
    registro.register(collector)
    return collector


async def _main(argomenti: list[str]) -> None:  # pragma: no cover — comando operativo
    import sys
    from datetime import UTC

    from app.services.versamento_service import _bucket_pdv, _s3_client
    from libs.shared.db import init_db

    await init_db(settings.database_url)
    if "--bloccati" in argomenti:
        for riga in await elenca_bloccati():
            sys.stdout.write(json.dumps(riga, default=str, ensure_ascii=False) + "\n")
        return
    if "--sblocca" in argomenti:
        ids = argomenti[argomenti.index("--sblocca") + 1:]
        sys.stdout.write(f"{await sblocca(ids, datetime.now(UTC))}\n")
        return
    esito = await pulisci_staging(s3=_s3_client(), bucket_staging=_bucket_pdv(),
                                  adesso=datetime.now(UTC), prova="--prova" in argomenti)
    sys.stdout.write("".join(f"{k}\n" for k in esito.oggetti))


if __name__ == "__main__":  # pragma: no cover
    import sys

    asyncio.run(_main(sys.argv[1:]))
