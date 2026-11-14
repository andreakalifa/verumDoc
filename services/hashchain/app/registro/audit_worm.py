# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Export di ``audit_global`` nel WORM e verifica (R05, VD-C-112).

Servizio: hashchain. Perché: contro l'amministratore del database (superutente,
su RDS l'utente master) le difese di ``audit_global`` dentro PostgreSQL (RLS,
trigger, event trigger) non bastano: può sospenderle. Con questo export ogni
periodo chiuso dell'audit finisce in un oggetto WORM (Object Lock COMPLIANCE, non
cancellabile né modificabile, neppure dal titolare dell'account) e la sua impronta
entra nel registro delle impronte come foglia ``audit`` di formato 2, sotto
checkpoint firmati e controfirmati dal testimone. Cancellare o modificare una
riga già esportata è quindi DIMOSTRABILE: ``verifica`` (comando
``python -m app.registro.operatore verifica-audit``) confronta il database con i
segmenti e stampa ogni differenza.

Segmento ``registro/audit/<da>-<a>.jsonl`` (istanti UTC ``AAAAMMGGThhmmssffffffZ``,
ordine alfabetico = ordine temporale), JSON Lines canonico (``libs.catena.canonico``):

  riga 1 — intestazione::

      {"formato":"verumdoc-audit-segmento","v":1,"tabella":"audit_global",
       "periodo_da":<ISO>,"periodo_a":<ISO>,"righe":<n>,"radice_righe":<hex>,
       "segmento_precedente":<SHA-256 hex del segmento precedente>|null}

  righe 2…n+1 — una per riga di ``audit_global`` con ``created_at`` in
  [periodo_da, periodo_a), in ordine (created_at, id)::

      {"aip_id","creato_il","dettagli":<testo jsonb di PostgreSQL>,"evento","id",
       "ip","tenant_id","user_agent","user_id"}

  ``dettagli`` è il testo che PostgreSQL produce per il jsonb (forma
  normalizzata e deterministica): nessuna conversione di numeri decimali.

``radice_righe`` = radice RFC 6962 degli hash di foglia delle righe (prova
d'inclusione di una singola riga possibile); impronta del segmento = SHA-256 dei
byte dell'oggetto; la catena dei segmenti lega ognuno al precedente.

Periodi: ``AUDIT_EXPORT_PERIODO_MINUTI`` (R07, D19: impostazione del
conservatore in console, 15 minuti, fra 5 e 60; prima 1 ora fissa) allineati al
tempo UTC. Cambiare il periodo è sicuro: ogni segmento parte dalla fine del
precedente e finisce al primo confine della griglia nuova. Un
periodo si esporta solo dopo ``AUDIT_EXPORT_MARGINE_SECONDI`` (600) dalla sua
fine E (revisione R05, R7) solo quando nessuna transazione iniziata prima della
sua fine è ancora aperta. ``created_at`` vale ``now()``, cioè l'istante
d'inizio della transazione che scrive la riga (``pg_stat_activity.xact_start``):
una transazione aperta da prima della fine del periodo potrebbe ancora
aggiungervi righe, che dopo l'export non finirebbero in nessun segmento. Il
riferimento è quindi l'ORIZZONTE delle transazioni aperte
(``public.vd_transazioni_aperte()``, migrazione 041): limite =
min(adesso − margine, inizio della transazione aperta più vecchia). Il margine
da solo non bastava (nessun timeout limita la durata di una transazione: scarto
di 100.000 PdA, ricostruzione). Se la funzione non vede l'inizio di tutte le
transazioni (proprietario senza ``pg_read_all_stats``) l'export si ferma
(fail-closed) e il ritardo fa scattare l'allarme ``AuditNonEsportato``.
Periodi vuoti consecutivi si uniscono in un segmento; un periodo con più di
``AUDIT_EXPORT_RIGHE_MASSIME`` righe si divide a metà (memoria limitata).

Errori: un segmento già nel WORM con lo stesso nome e contenuto diverso (righe
cambiate o aggiunte dopo l'esportazione) è un ALLARME dell'audit (log
``audit_worm_divergente``, metrica ``verumdoc_catena_audit_worm_divergente``),
NON un blocco del registro: l'export dell'audit si ferma, la conservazione no.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.registro import archivio_worm
from app.registro.interne import accoda_foglia, chiave_audit
from app.registro.metriche import metriche
from libs.catena.canonico import json_canonico
from libs.catena.foglie import dati_foglia_audit, iso_utc
from libs.catena.rfc6962 import hash_foglia, radice
from libs.shared.db import get_async_session
from libs.shared.storage import worm

logger = structlog.get_logger(__name__)

PREFISSO_AUDIT = f"{archivio_worm.PREFISSO}/audit"
FORMATO = "verumdoc-audit-segmento"
_FORMATO_CHIAVE = "%Y%m%dT%H%M%S%fZ"
PAGINA = 5000
EPOCA = datetime(1970, 1, 1, tzinfo=UTC)


def chiave_segmento(da: datetime, a: datetime) -> str:
    return (f"{PREFISSO_AUDIT}/{da.astimezone(UTC).strftime(_FORMATO_CHIAVE)}-"
            f"{a.astimezone(UTC).strftime(_FORMATO_CHIAVE)}.jsonl")


def riga_canonica(r: Any) -> bytes:
    """Riga di ``audit_global`` in JSON canonico (vedi modulo)."""
    def testo(v: Any) -> str | None:
        return None if v is None else str(v)

    return json_canonico({
        "aip_id": testo(r["aip_id"]), "creato_il": iso_utc(r["created_at"]),
        "dettagli": str(r["dettagli"]), "evento": str(r["evento"]), "id": str(r["id"]),
        "ip": testo(r["ip"]), "tenant_id": testo(r["tenant_id"]),
        "user_agent": testo(r["user_agent"]), "user_id": testo(r["user_id"]),
    })


def intestazione(da: datetime, a: datetime, righe: int, radice_righe: str,
                 precedente: str | None) -> bytes:
    return json_canonico({
        "formato": FORMATO, "v": 1, "tabella": "audit_global", "periodo_da": iso_utc(da),
        "periodo_a": iso_utc(a), "righe": righe, "radice_righe": radice_righe,
        "segmento_precedente": precedente})


def componi_segmento(da: datetime, a: datetime, righe: list[bytes],
                     precedente: str | None) -> tuple[bytes, str, str]:
    """(byte del segmento, radice delle righe, impronta del segmento)."""
    radice_righe = radice([hash_foglia(r) for r in righe]).hex()
    corpo = b"\n".join([intestazione(da, a, len(righe), radice_righe, precedente), *righe]) + b"\n"
    return corpo, radice_righe, hashlib.sha256(corpo).hexdigest()


_RIGHE = text(
    "SELECT id, tenant_id, user_id, aip_id, evento, dettagli::text AS dettagli, "
    "ip_address::text AS ip, user_agent, created_at FROM public.audit_global "
    "WHERE created_at >= :da AND created_at < :a "
    "AND (created_at, id) > (CAST(:dopo_t AS timestamptz), CAST(:dopo_id AS uuid)) "
    "ORDER BY created_at, id LIMIT :pagina")


async def righe_periodo(da: datetime, a: datetime) -> list[bytes]:
    """Righe del periodo in ordine (created_at, id), a pagine (cursore)."""
    risultato: list[bytes] = []
    dopo_t, dopo_id = da - timedelta(microseconds=1), uuid.UUID(int=0)
    while True:
        async with get_async_session() as session:
            pagina = (await session.execute(_RIGHE, {
                "da": da, "a": a, "dopo_t": dopo_t, "dopo_id": dopo_id,
                "pagina": PAGINA})).mappings().all()
        risultato.extend(riga_canonica(r) for r in pagina)
        if len(pagina) < PAGINA:
            return risultato
        dopo_t, dopo_id = pagina[-1]["created_at"], pagina[-1]["id"]


def _griglia_su(t: datetime, periodo: int) -> datetime:
    """Primo confine di periodo (allineato al tempo UTC) strettamente dopo ``t``."""
    secondi = (t - EPOCA).total_seconds()
    return EPOCA + timedelta(seconds=(int(secondi // periodo) + 1) * periodo)


def _griglia_giu(t: datetime, periodo: int) -> datetime:
    secondi = (t - EPOCA).total_seconds()
    return EPOCA + timedelta(seconds=int(secondi // periodo) * periodo)


@dataclass
class EsitoExport:
    esportati: int = 0
    problemi: list[str] = field(default_factory=list)
    #: R7: perché l'export si è fermato prima del limite del margine (transazione
    #: aperta da prima della fine del periodo, orizzonte non verificabile)
    attesa: str | None = None


async def orizzonte_transazioni() -> tuple[datetime | None, int]:
    """(inizio della transazione aperta più vecchia, transazioni di cui non si
    vede l'inizio), esclusa la sessione che chiede (revisione R05, R7)."""
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "SELECT inizio_piu_vecchio, non_visibili FROM public.vd_transazioni_aperte()"
        ))).one()
    return riga[0], int(riga[1] or 0)


async def limite_esportabile(adesso: datetime) -> tuple[datetime | None, str | None]:
    """Fine massima di un periodo esportabile ADESSO, o None (con il motivo) se
    l'orizzonte delle transazioni non si può verificare."""
    limite = adesso - timedelta(seconds=settings.audit_export_margine_secondi)
    inizio, non_visibili = await orizzonte_transazioni()
    m = metriche()
    # Revisione 2 di R05 (S7): segnali propri della sospensione (allarmi
    # AuditExportTrattenuto e AuditOrizzonteNonVerificabile); il limite alla
    # durata di una transazione dimenticata è idle_in_transaction_session_timeout
    # dei login dei servizi (guardia all'avvio, docs/architettura/sicurezza.md §7)
    m.audit_orizzonte_non_verificabile.set(1 if non_visibili else 0)
    trattiene = inizio is not None and inizio < limite
    m.audit_transazione_aperta.set(
        max((adesso - inizio).total_seconds(), 0.0) if trattiene and inizio else 0.0)
    if non_visibili:
        return None, (f"{non_visibili} transazioni di cui non si vede l'inizio "
                      "(pg_stat_activity): il proprietario di vd_transazioni_aperte deve "
                      "essere membro di pg_read_all_stats; export sospeso")
    if trattiene and inizio is not None:
        return inizio, (f"transazione aperta dal {inizio.isoformat()}: i periodi successivi "
                        "si esportano quando termina")
    return limite, None


async def periodo_secondi() -> int:
    """R07 (D19): durata dei periodi esportati (``AUDIT_EXPORT_PERIODO_MINUTI``)."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import AUDIT_EXPORT_PERIODO_MINUTI

    minuti = (await parametri([AUDIT_EXPORT_PERIODO_MINUTI]))[AUDIT_EXPORT_PERIODO_MINUTI]
    return int(minuti) * 60


async def _ultima() -> dict[str, Any] | None:
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "SELECT periodo_da, periodo_a, impronta_segmento FROM public.audit_esportazioni "
            "ORDER BY periodo_da DESC LIMIT 1"))).mappings().first()
    return dict(riga) if riga else None


async def _inizio(ultima: dict[str, Any] | None) -> datetime | None:
    if ultima is not None:
        return ultima["periodo_a"]
    if settings.audit_export_inizio.strip():
        return datetime.fromisoformat(settings.audit_export_inizio.strip().replace("Z", "+00:00"))
    async with get_async_session() as session:
        prima = (await session.execute(text(
            "SELECT created_at FROM public.audit_global ORDER BY created_at LIMIT 1"
        ))).scalar_one_or_none()
    return None if prima is None else _griglia_giu(prima, await periodo_secondi())


async def _conta(da: datetime, a: datetime) -> int:
    async with get_async_session() as session:
        return int((await session.execute(text(
            "SELECT count(*) FROM public.audit_global WHERE created_at >= :da AND created_at < :a"
        ), {"da": da, "a": a})).scalar_one())


async def _prossima_riga(dopo: datetime) -> datetime | None:
    async with get_async_session() as session:
        return (await session.execute(text(
            "SELECT created_at FROM public.audit_global WHERE created_at >= :t "
            "ORDER BY created_at LIMIT 1"), {"t": dopo})).scalar_one_or_none()


async def prossimo_periodo(da: datetime, limite: datetime) -> tuple[datetime, int] | None:
    """Fine del prossimo segmento da ``da`` (e righe), o None se non è ancora chiuso.
    Vuoto → fino al periodo della riga successiva (o al limite); troppo pieno → metà."""
    periodo = await periodo_secondi()
    a = _griglia_su(da, periodo)
    if a > limite:
        return None
    n = await _conta(da, a)
    if n == 0:
        prossima = await _prossima_riga(a)
        fine = _griglia_giu(limite, periodo) if prossima is None else \
            min(_griglia_giu(prossima, periodo), _griglia_giu(limite, periodo))
        return max(a, fine), 0
    while n > settings.audit_export_righe_massime and (a - da) > timedelta(milliseconds=2):
        a = da + (a - da) / 2  # risoluzione del microsecondo
        n = await _conta(da, a)
    return a, n


async def esporta_periodo(da: datetime, a: datetime, precedente: str | None,
                          adesso: datetime) -> str:
    """Scrive il segmento [da, a) nel WORM e, in UNA transazione, la foglia
    ``audit`` in coda al registro e la riga in ``audit_esportazioni``. Un nuovo
    tentativo dopo un errore ritrova l'oggetto WORM identico (contenuto
    deterministico). Restituisce l'impronta del segmento."""
    righe = await righe_periodo(da, a)
    corpo, radice_righe, impronta = componi_segmento(da, a, righe, precedente)
    chiave = chiave_segmento(da, a)
    bucket, _ = await archivio_worm.scrivi(chiave, corpo, "application/x-ndjson", adesso)
    foglia_id = uuid.uuid4()
    dati = dati_foglia_audit(
        foglia_id=str(foglia_id), segmento=chiave, impronta_segmento=impronta,
        impronta_precedente=precedente, radice_righe=radice_righe, righe=len(righe),
        periodo_da=da, periodo_a=a, registrato_il=adesso)
    async with get_async_session() as session:
        nuova = await accoda_foglia(
            session, foglia_id=foglia_id, tipo_db="AUDIT", chiave=chiave_audit(chiave),
            dati=dati, aip_id=None, tenant_id=None, pda=None, pratica=None, riferimento={},
            impronta=None, richiesta_da="hashchain:audit", adesso=adesso)
        if not nuova:
            raise RuntimeError(f"foglia dell'audit per {chiave} già richiesta")
        await session.execute(text(
            "INSERT INTO public.audit_esportazioni (periodo_da, periodo_a, righe, radice_righe, "
            "impronta_segmento, impronta_precedente, worm_bucket, worm_chiave, foglia_id, "
            "esportato_il) VALUES (:da, :a, :n, :radice, :impronta, :precedente, :bucket, "
            ":chiave, :foglia, :adesso)"
        ), {"da": da, "a": a, "n": len(righe), "radice": radice_righe, "impronta": impronta,
            "precedente": precedente, "bucket": bucket, "chiave": chiave, "foglia": foglia_id,
            "adesso": adesso})
    logger.info("audit_segmento_esportato", segmento=chiave, righe=len(righe),
                impronta=impronta[:16])
    return impronta


async def esporta_periodi_chiusi(*, adesso: datetime, limite_periodi: int | None = None
                                 ) -> EsitoExport:
    """Esporta i periodi chiusi (fine + margine ≤ adesso), al più
    ``AUDIT_EXPORT_PERIODI_PER_GIRO``. Un segmento WORM divergente è un allarme
    dell'audit, non del registro."""
    esito = EsitoExport()
    if not settings.audit_export_attivo:
        return esito
    limite, esito.attesa = await limite_esportabile(adesso)
    if esito.attesa is not None:
        logger.warning("audit_export_in_attesa", motivo=esito.attesa)
    ultima = await _ultima()
    da = await _inizio(ultima)
    precedente = ultima["impronta_segmento"] if ultima else None
    if da is None:
        return esito
    if limite is None:
        metriche().audit_ritardo.set(max((adesso - da).total_seconds(), 0.0))
        return esito
    for _ in range(limite_periodi or settings.audit_export_periodi_per_giro):
        prossimo = await prossimo_periodo(da, limite)
        if prossimo is None:
            break
        a, _n = prossimo
        try:
            precedente = await esporta_periodo(da, a, precedente, adesso)
        except archivio_worm.ContenutoWormDiverso:
            metriche().audit_divergente.set(1)
            messaggio = (f"segmento {chiave_segmento(da, a)} già nel WORM con contenuto diverso: "
                         "righe di audit_global cambiate o aggiunte dopo l'esportazione")
            logger.error("audit_worm_divergente", segmento=chiave_segmento(da, a),
                         azione="python -m app.registro.operatore verifica-audit")
            esito.problemi.append(messaggio)
            break
        esito.esportati += 1
        da = a
    metriche().audit_ritardo.set(max((adesso - da).total_seconds(), 0.0))
    if not esito.problemi:
        metriche().audit_divergente.set(0)
    return esito


class AdozioneNonAmmessa(ValueError):
    """Il segmento indicato non è il successivo della catena o non è conforme."""


async def adotta_segmento(chiave: str, *, operatore: str, motivo: str,
                          adesso: datetime) -> dict[str, Any]:
    """Registra come esportato un segmento GIÀ nel WORM che l'export non riesce a
    riscrivere (segmento divergente: righe del periodo cambiate o perse dopo
    l'esportazione). Il segmento non si tocca: si controlla che sia il successivo
    della catena (periodo e impronta precedente) e conforme (intestazione, numero
    di righe, radice), poi foglia ``audit`` in coda, riga in ``audit_esportazioni``
    e voce ``catena.audit_segmento_adottato`` in ``audit_global``, in UNA
    transazione. Le differenze con il database restano visibili a ``verifica``."""
    from app.services.block_sealer import _client
    from libs.shared.security.audit import registra_audit

    ultima = await _ultima()
    inizio = await _inizio(ultima)
    precedente = ultima["impronta_segmento"] if ultima else None
    if not chiave.startswith(f"{PREFISSO_AUDIT}/"):
        raise AdozioneNonAmmessa(f"il segmento deve stare sotto {PREFISSO_AUDIT}/")
    try:
        corpo = await asyncio.to_thread(_leggi, _client(), chiave)
    except worm.ErroreStorageConservativo as exc:
        raise AdozioneNonAmmessa(f"segmento non leggibile nel WORM: {exc}") from None
    righe = corpo.split(b"\n")
    if righe and righe[-1] == b"":
        righe.pop()
    try:
        testa = json.loads(righe[0])
        da = datetime.fromisoformat(testa["periodo_da"].replace("Z", "+00:00"))
        a = datetime.fromisoformat(testa["periodo_a"].replace("Z", "+00:00"))
    except (IndexError, KeyError, ValueError) as exc:
        raise AdozioneNonAmmessa(f"intestazione non valida: {exc}") from None
    if chiave != chiave_segmento(da, a) or testa.get("formato") != FORMATO:
        raise AdozioneNonAmmessa("segmento non conforme")
    if inizio is None or da != inizio or testa.get("segmento_precedente") != precedente:
        raise AdozioneNonAmmessa(f"non è il segmento successivo della catena (atteso l'inizio "
                                 f"{inizio}, impronta precedente {precedente})")
    radice_righe = radice([hash_foglia(r) for r in righe[1:]]).hex()
    if testa.get("righe") != len(righe) - 1 or testa.get("radice_righe") != radice_righe:
        raise AdozioneNonAmmessa("righe o radice diverse dall'intestazione")
    impronta = hashlib.sha256(corpo).hexdigest()
    foglia_id = uuid.uuid4()
    dati = dati_foglia_audit(
        foglia_id=str(foglia_id), segmento=chiave, impronta_segmento=impronta,
        impronta_precedente=precedente, radice_righe=radice_righe, righe=len(righe) - 1,
        periodo_da=da, periodo_a=a, registrato_il=adesso)
    async with get_async_session() as session:
        esistente = (await session.execute(text(
            "SELECT id, dati FROM public.catena_foglie_interne WHERE chiave = :chiave"),
            {"chiave": chiave_audit(chiave)})).mappings().first()
        if esistente is not None:
            # La foglia del segmento c'è già (si è persa solo l'esportazione): si riusa
            # se riguarda proprio questo contenuto
            if json.loads(esistente["dati"]).get("impronta_segmento") != impronta:
                raise AdozioneNonAmmessa("una foglia del registro per questo segmento ha "
                                         "un'impronta diversa")
            foglia_id = esistente["id"]
        elif not await accoda_foglia(
                session, foglia_id=foglia_id, tipo_db="AUDIT", chiave=chiave_audit(chiave),
                dati=dati, aip_id=None, tenant_id=None, pda=None, pratica=None, riferimento={},
                impronta=None, richiesta_da="hashchain:audit-adottato", adesso=adesso):
            raise AdozioneNonAmmessa("foglia del segmento richiesta in concorrenza")
        await session.execute(text(
            "INSERT INTO public.audit_esportazioni (periodo_da, periodo_a, righe, radice_righe, "
            "impronta_segmento, impronta_precedente, worm_bucket, worm_chiave, foglia_id, "
            "esportato_il) VALUES (:da, :a, :n, :radice, :impronta, :precedente, :bucket, "
            ":chiave, :foglia, :adesso)"
        ), {"da": da, "a": a, "n": len(righe) - 1, "radice": radice_righe, "impronta": impronta,
            "precedente": precedente, "bucket": worm.bucket_conservativo(settings),
            "chiave": chiave, "foglia": foglia_id, "adesso": adesso})
        dettagli = {"segmento": chiave, "impronta": impronta, "righe": len(righe) - 1,
                    "operatore": operatore[:200], "motivo": motivo[:2000]}
        await registra_audit(session, evento="catena.audit_segmento_adottato", dettagli=dettagli)
    metriche().audit_divergente.set(0)
    logger.warning("audit_segmento_adottato", **dettagli)
    return dettagli


# ── Verifica: database contro WORM ──────────────────────────────────────────

@dataclass
class EsitoVerifica:
    segmenti: int = 0
    righe_worm: int = 0
    righe_db: int = 0
    mancanti_nel_db: int = 0        # nel WORM, non più nel database (cancellate)
    modificate: int = 0             # stesso id, contenuto diverso
    aggiunte_nel_db: int = 0        # nel database ma non nel segmento del periodo
    non_esportate: int = 0          # dopo l'ultimo segmento (informativo)
    differenze: list[str] = field(default_factory=list)
    problemi: list[str] = field(default_factory=list)  # segmenti, catena, registro

    @property
    def coerente(self) -> bool:
        return not self.differenze and not self.problemi

    def righe(self) -> list[str]:
        campi = ("segmenti", "righe_worm", "righe_db", "mancanti_nel_db", "modificate",
                 "aggiunte_nel_db", "non_esportate")
        out = [f"{c}\t{getattr(self, c)}" for c in campi]
        out += [f"PROBLEMA\t{p}" for p in self.problemi[:100]]
        out += [f"DIFFERENZA\t{d}" for d in self.differenze[:200]]
        if len(self.differenze) > 200:
            out.append(f"DIFFERENZA\t… altre {len(self.differenze) - 200}")
        return out


def _elenca(da: datetime | None) -> list[tuple[str, Any]]:
    from app.services.block_sealer import _client

    client, bucket = _client(), worm.bucket_conservativo(settings)
    parametri: dict[str, Any] = {"Bucket": bucket, "Prefix": f"{PREFISSO_AUDIT}/"}
    if da is not None:
        parametri["StartAfter"] = f"{PREFISSO_AUDIT}/{da.astimezone(UTC).strftime(_FORMATO_CHIAVE)}"
    chiavi = [o["Key"] for pagina in client.get_paginator("list_objects_v2").paginate(**parametri)
              for o in pagina.get("Contents", []) if o["Key"].endswith(".jsonl")]
    return [(k, client) for k in sorted(chiavi)]


def _leggi(client: Any, chiave: str) -> bytes:
    return worm.leggi_oggetto(client, worm.bucket_conservativo(settings), chiave)


async def _foglia_nel_registro(chiave: str) -> dict[str, Any] | None:
    """Foglia ``audit`` del segmento nel registro (via la richiesta interna) e la
    dimensione dell'ultimo checkpoint."""
    async with get_async_session() as session:
        riga = (await session.execute(text(
            # richiesta interna → foglia del registro allo stesso indice
            "SELECT f.indice, r.dati AS registro, "
            "(SELECT max(dimensione) FROM public.catena_checkpoint) AS checkpoint "
            "FROM public.catena_foglie_interne f "
            "LEFT JOIN public.catena_registro r ON r.indice = f.indice "
            "WHERE f.chiave = :chiave"), {"chiave": chiave_audit(chiave)})).mappings().first()
    return dict(riga) if riga else None


def _confronta(esito: EsitoVerifica, chiave: str, worm_righe: list[bytes],
               db_righe: list[bytes]) -> None:
    """Confronto per id: cancellate, modificate, aggiunte."""
    def per_id(righe: list[bytes]) -> dict[str, bytes]:
        return {json.loads(r)["id"]: r for r in righe}

    nel_worm, nel_db = per_id(worm_righe), per_id(db_righe)
    for id_, riga in nel_worm.items():
        if id_ not in nel_db:
            esito.mancanti_nel_db += 1
            esito.differenze.append(f"{chiave}: riga {id_} cancellata dal database "
                                    f"({json.loads(riga)['evento']})")
        elif nel_db[id_] != riga:
            esito.modificate += 1
            esito.differenze.append(f"{chiave}: riga {id_} modificata nel database")
    for id_ in nel_db.keys() - nel_worm.keys():
        esito.aggiunte_nel_db += 1
        esito.differenze.append(f"{chiave}: riga {id_} presente nel database ma non nel "
                                "segmento (aggiunta dopo l'esportazione)")


#: R09 (D27): margine prima dell'inizio della verifica incrementale in cui cercare
#: il segmento precedente (l'«àncora» della catena dei segmenti)
#: (senza àncora entro il margine la catena al confine la controlla la verifica completa)
MARGINE_ANCORA = timedelta(days=7)


async def verifica(*, da: datetime | None = None, a: datetime | None = None,
                   incrementale: bool = False) -> EsitoVerifica:
    """Confronta ``audit_global`` con i segmenti nel WORM (vedi modulo): catena dei
    segmenti, radice delle righe, foglia nel registro coperta da un checkpoint,
    righe cancellate, modificate o aggiunte.

    R09 (D27): ``incrementale`` (con ``da``) verifica solo i segmenti che iniziano
    da ``da`` in poi ma li AGGANCIA al segmento precedente (letto entro
    ``MARGINE_ANCORA`` prima, solo per la sua impronta e la sua fine): un buco o
    una catena interrotta al confine si vedono anche nella verifica giornaliera."""
    esito = EsitoVerifica()
    elenco = await asyncio.to_thread(_elenca, (da - MARGINE_ANCORA) if (incrementale and da)
                                     else da)
    precedente_impronta: str | None = None
    precedente_fine: datetime | None = None
    for chiave, client in elenco:
        corpo = await asyncio.to_thread(_leggi, client, chiave)
        righe = corpo.split(b"\n")
        if righe and righe[-1] == b"":
            righe.pop()
        try:
            testa = json.loads(righe[0])
            periodo_da = datetime.fromisoformat(testa["periodo_da"].replace("Z", "+00:00"))
            periodo_a = datetime.fromisoformat(testa["periodo_a"].replace("Z", "+00:00"))
        except (IndexError, KeyError, ValueError) as exc:
            esito.problemi.append(f"{chiave}: intestazione non valida ({exc})")
            continue
        if incrementale and da is not None and periodo_da < da:
            # àncora: solo impronta e fine (la sua verifica è del giro precedente)
            precedente_impronta = hashlib.sha256(corpo).hexdigest()
            precedente_fine = periodo_a
            continue
        if a is not None and periodo_da >= a:
            break
        esito.segmenti += 1
        corpo_righe = righe[1:]
        esito.righe_worm += len(corpo_righe)
        if testa.get("formato") != FORMATO or json_canonico(testa) != righe[0] \
                or chiave != chiave_segmento(periodo_da, periodo_a):
            esito.problemi.append(f"{chiave}: intestazione non conforme")
        if testa.get("righe") != len(corpo_righe) or testa.get("radice_righe") != radice(
                [hash_foglia(r) for r in corpo_righe]).hex():
            esito.problemi.append(f"{chiave}: righe o radice delle righe diverse "
                                  "dall'intestazione")
        if (precedente_fine is None and da is None and testa.get("segmento_precedente")) or (
                precedente_fine is not None and (
                    periodo_da != precedente_fine
                    or testa.get("segmento_precedente") != precedente_impronta)):
            esito.problemi.append(f"{chiave}: non segue il segmento precedente (buco o catena "
                                  "delle impronte interrotta)")
        impronta = hashlib.sha256(corpo).hexdigest()
        foglia = await _foglia_nel_registro(chiave)
        if foglia is None or foglia["indice"] is None or foglia["registro"] is None:
            esito.problemi.append(f"{chiave}: impronta non ancora (o non più) nel registro "
                                  "delle impronte")
        else:
            dati = json.loads(foglia["registro"])
            if dati.get("impronta_segmento") != impronta or dati.get("segmento") != chiave:
                esito.problemi.append(f"{chiave}: foglia del registro con un'impronta diversa")
            elif foglia["checkpoint"] is None or int(foglia["indice"]) >= int(foglia["checkpoint"]):
                esito.problemi.append(f"{chiave}: foglia nel registro non ancora coperta da un "
                                      "checkpoint")
        db_righe = await righe_periodo(periodo_da, periodo_a)
        esito.righe_db += len(db_righe)
        _confronta(esito, chiave, corpo_righe, db_righe)
        precedente_impronta, precedente_fine = impronta, periodo_a
    if precedente_fine is not None:
        async with get_async_session() as session:
            esito.non_esportate = int((await session.execute(text(
                "SELECT count(*) FROM public.audit_global WHERE created_at >= :t"),
                {"t": precedente_fine})).scalar_one())
    return esito
