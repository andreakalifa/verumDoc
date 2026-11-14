# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Accesso al database della verifica continua (tabelle della migrazione 20261023_027).

Lavoratore:
  - ``semina``: nuovi PdA nella programmazione, per posizione su
    ``aip(data_versamento, id)`` (indice ``idx_aip_data_v``) con un intervallo di
    sovrapposizione riletto a ogni giro; idempotente (ON CONFLICT DO NOTHING);
  - ``riconcilia``: rete di sicurezza (REGOLE §22.3), ripassa l'intera tabella dei
    PdA per chiave a lotti, ogni ``VERIFICA_RICONCILIAZIONE_GIORNI``;
  - ``prendi_lotto``: presa in carico con ``FOR UPDATE SKIP LOCKED``; prima le
    righe marcate da una verifica su richiesta, poi la rotazione. La presa sposta
    ``prossima_verifica`` alla scadenza del lease: due repliche non leggono mai lo
    stesso PdA e una replica ferma restituisce il lavoro da sola;
  - ``registra_esito``: in UNA transazione (recinto sul ``lease_id``) registro
    delle verifiche, programmazione, copertura, evento d'allarme in outbox,
    avanzamento della richiesta;
  - ``prepara_richieste``: marcatura a lotti dei PdA di una verifica d'ente.
API: richieste, stato, registro a cursore, riepilogo.

Costi per richiesta HTTP indipendenti dalla dimensione delle tabelle: nessun
``COUNT(*)`` sul registro o sulla programmazione (copertura da
``verifiche_copertura``, anomalie aperte contate su un indice parziale con un
tetto). Semina e riconciliazione hanno un lock consultivo di transazione: una
replica alla volta, le altre saltano il giro.
"""
from __future__ import annotations

import base64
import json
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.verifica_continua import esiti
from app.verifica_continua.config import ImpostazioniVerifica
from app.verifica_continua.verificatore import (
    DocumentoDaVerificare,
    EsitoOggetto,
    EsitoPda,
    PdaDaVerificare,
)
from libs.shared.db import get_async_session
from libs.shared.outbox import accoda_evento

logger = structlog.get_logger(__name__)

#: Giorni convenzionali di ``verifiche_copertura``.
GIORNO_MAI = date(1, 1, 1)
GIORNO_VECCHI = date(1, 1, 2)
#: Stati del PdA con oggetti già sullo storage conservativo (da verificare).
STATI_VERIFICABILI = ("SU_STORAGE", "IN_CATENA", "CONSERVATO", "ESIBIZIONE",
                      "FIRMATO", "TIMESTAMPATO",
                      # R09 (D26): si verificano le versioni RIMASTE (vedi incompleti.py)
                      "ELIMINAZIONE_INCOMPLETA")
#: PdA che escono dalla rotazione: scarto in corso o eseguito (ELIMINAZIONE).
#: Revisione 3 di R05 (T4) escludeva anche l'eliminazione INCOMPLETA; R09 (D26,
#: VD-C-135): ora se ne verificano le versioni rimaste, senza falsi allarmi per
#: gli oggetti che lo scarto ha cancellato (``incompleti.adatta``).
STATI_FUORI_ROTAZIONE = ("ELIMINAZIONE",)
#: R09 (D26): PdA con le sole versioni rimaste da verificare
STATO_INCOMPLETO = "ELIMINAZIONE_INCOMPLETA"
#: Lock consultivo di semina e riconciliazione ("VDVC").
CHIAVE_LOCK_SEMINA = 0x5644_5643
#: Tetto del conteggio delle anomalie aperte in una risposta.
TETTO_ANOMALIE = 10_000

_STATI_SQL = ", ".join(f"'{s}'" for s in STATI_VERIFICABILI)


# ── utilità ──────────────────────────────────────────────────────────────────

async def _leggi_stato(session: Any, nome: str, *, blocca: bool = False) -> dict[str, Any]:
    sql = "SELECT valore FROM public.verifiche_stato WHERE nome = :n"
    if blocca:
        sql += " FOR UPDATE"
    riga = (await session.execute(text(sql), {"n": nome})).first()
    if riga is None:
        return {}
    valore = riga[0]
    return json.loads(valore) if isinstance(valore, str) else dict(valore)


async def _scrivi_stato(session: Any, nome: str, valore: dict[str, Any]) -> None:
    await session.execute(text(
        "INSERT INTO public.verifiche_stato (nome, valore, aggiornato_il) "
        "VALUES (:n, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (nome) DO UPDATE SET valore = EXCLUDED.valore, aggiornato_il = now()"),
        {"n": nome, "v": json.dumps(valore, default=str)})


async def _lock_semina(session: Any) -> bool:
    return bool((await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": CHIAVE_LOCK_SEMINA})).scalar_one())


async def _aggiungi_copertura(session: Any, per_ente: Counter[uuid.UUID], giorno: date) -> None:
    """Aggiunge PdA a un giorno di copertura (ordine per ente: lock in ordine)."""
    if not per_ente:
        return
    enti = sorted(per_ente)
    await session.execute(text(
        "INSERT INTO public.verifiche_copertura (tenant_id, giorno, oggetti) "
        "SELECT t, CAST(:g AS date), n FROM unnest(CAST(:enti AS uuid[]), "
        "CAST(:numeri AS bigint[])) AS x(t, n) "
        "ON CONFLICT (tenant_id, giorno) DO UPDATE "
        "SET oggetti = public.verifiche_copertura.oggetti + EXCLUDED.oggetti"),
        {"g": giorno, "enti": enti, "numeri": [per_ente[e] for e in enti]})


async def _togli_copertura(session: Any, tenant: uuid.UUID, giorno: date) -> None:
    """Toglie un PdA dal suo giorno (o, se il giorno è stato accorpato, dai «vecchi»)."""
    for g in (giorno, GIORNO_VECCHI):
        tolti = await session.execute(text(
            "UPDATE public.verifiche_copertura SET oggetti = oggetti - 1 "
            "WHERE tenant_id = :t AND giorno = :g AND oggetti > 0"), {"t": tenant, "g": g})
        if tolti.rowcount == 1:
            return
    logger.warning("verifica_copertura_non_allineata", tenant_id=str(tenant), giorno=str(giorno))


def _giorno_di(istante: datetime | None) -> date:
    return GIORNO_MAI if istante is None else istante.astimezone(UTC).date()


async def _inserisci_programmazione(
    session: Any, righe: Sequence[tuple[uuid.UUID, uuid.UUID, datetime]],
) -> int:
    """Nuove righe di programmazione (aip_id, tenant_id, prossima); le esistenti
    restano. I PdA nuovi entrano nella copertura come «mai verificati»."""
    if not righe:
        return 0
    inseriti = (await session.execute(text(
        "INSERT INTO public.verifiche_programmazione (aip_id, tenant_id, prossima_verifica) "
        "SELECT * FROM unnest(CAST(:ids AS uuid[]), CAST(:enti AS uuid[]), "
        "CAST(:prossime AS timestamptz[])) "
        "ON CONFLICT (aip_id) DO NOTHING RETURNING tenant_id"),
        {"ids": [r[0] for r in righe], "enti": [r[1] for r in righe],
         "prossime": [r[2] for r in righe]})).scalars().all()
    await _aggiungi_copertura(session, Counter(inseriti), GIORNO_MAI)
    return len(inseriti)


def _righe_semina(candidati: Iterable[Any], adesso: datetime,
                  cfg: ImpostazioniVerifica) -> list[tuple[uuid.UUID, uuid.UUID, datetime]]:
    finestra = timedelta(days=cfg.prima_entro_giorni)
    return [(c["id"], c["tenant_id"], esiti.prima_verifica(c["id"], adesso, finestra))
            for c in candidati if c["stato"] in STATI_VERIFICABILI]


# ── semina e riconciliazione ─────────────────────────────────────────────────

async def semina(cfg: ImpostazioniVerifica, adesso: datetime) -> int:
    """Porta nella programmazione i PdA nuovi; restituisce quanti ne ha aggiunti."""
    aggiunti = 0
    for _ in range(cfg.semina_lotti_per_giro):
        async with get_async_session() as session:
            if not await _lock_semina(session):
                return aggiunti
            stato = await _leggi_stato(session, "semina", blocca=True)
            if stato.get("data"):
                da_data = datetime.fromisoformat(stato["data"])
                da_id = uuid.UUID(stato["id"])
            else:
                da_data, da_id = datetime(1970, 1, 1, tzinfo=UTC), uuid.UUID(int=0)
            # Sovrapposizione: si rilegge l'intervallo prima della posizione
            # (PdA scritti in ritardo con una data di versamento precedente).
            scansione = stato.get("scansione")
            if scansione:  # giro precedente interrotto a metà intervallo
                da_data = datetime.fromisoformat(scansione["data"])
                da_id = uuid.UUID(scansione["id"])
            elif stato.get("data"):
                da_data = da_data - timedelta(minutes=cfg.semina_sovrapposizione_minuti)
                da_id = uuid.UUID(int=0)
            candidati = (await session.execute(text(
                "SELECT id, tenant_id, stato::text AS stato, data_versamento FROM public.aip "
                "WHERE data_versamento >= :d AND (data_versamento, id) > (:d, :i) "
                "ORDER BY data_versamento, id LIMIT :n"),
                {"d": da_data, "i": da_id, "n": cfg.semina_lotto})).mappings().all()
            aggiunti += await _inserisci_programmazione(
                session, _righe_semina(candidati, adesso, cfg))
            if candidati:
                ultimo = candidati[-1]
                posizione = {"data": ultimo["data_versamento"].isoformat(),
                             "id": str(ultimo["id"])}
                precedente = stato.get("data")
                piu_avanti = (precedente is None or ultimo["data_versamento"]
                              >= datetime.fromisoformat(precedente))
                nuovo = dict(stato)
                if piu_avanti:
                    nuovo.update(posizione)
                if len(candidati) == cfg.semina_lotto:
                    nuovo["scansione"] = posizione  # resta lavoro: si riprende da qui
                else:
                    nuovo.pop("scansione", None)
                nuovo["giro_il"] = adesso.isoformat()
                await _scrivi_stato(session, "semina", nuovo)
            else:
                nuovo = dict(stato)
                nuovo.pop("scansione", None)
                nuovo["giro_il"] = adesso.isoformat()
                await _scrivi_stato(session, "semina", nuovo)
            if len(candidati) < cfg.semina_lotto:
                break
    if aggiunti:
        logger.info("verifica_semina", aggiunti=aggiunti)
    return aggiunti


async def riconcilia(cfg: ImpostazioniVerifica, adesso: datetime) -> int:
    """Ripasso dell'intera tabella dei PdA per chiave, a lotti: aggiunge alla
    programmazione i PdA sfuggiti alla semina. Un passaggio completo ogni
    ``riconciliazione_giorni``; restituisce i PdA aggiunti in questo giro."""
    aggiunti = 0
    for _ in range(cfg.riconciliazione_lotti_per_giro):
        async with get_async_session() as session:
            if not await _lock_semina(session):
                return aggiunti
            stato = await _leggi_stato(session, "riconciliazione", blocca=True)
            cursore = stato.get("cursore")
            if cursore is None:
                conclusa = stato.get("conclusa_il")
                if conclusa and datetime.fromisoformat(conclusa) > adesso - timedelta(
                        days=cfg.riconciliazione_giorni):
                    return aggiunti
                cursore = str(uuid.UUID(int=0))
                stato["avviata_il"] = adesso.isoformat()
            candidati = (await session.execute(text(
                "SELECT a.id, a.tenant_id, a.stato::text AS stato FROM public.aip a "
                "WHERE a.id > :c AND NOT EXISTS (SELECT 1 FROM public.verifiche_programmazione p "
                "  WHERE p.aip_id = a.id) "
                "ORDER BY a.id LIMIT :n"),
                {"c": uuid.UUID(cursore), "n": cfg.semina_lotto})).mappings().all()
            # Il cursore avanza sull'intera tabella (anche sui PdA già programmati)
            ultimo = (await session.execute(text(
                "SELECT id FROM (SELECT id FROM public.aip WHERE id > :c ORDER BY id LIMIT :n) x "
                "ORDER BY id DESC LIMIT 1"),
                {"c": uuid.UUID(cursore), "n": cfg.semina_lotto})).scalar_one_or_none()
            nuovi = await _inserisci_programmazione(
                session, _righe_semina(candidati, adesso, cfg))
            aggiunti += nuovi
            if nuovi:
                logger.warning("verifica_riconciliazione_pda_mancanti", aggiunti=nuovi)
            if ultimo is None:
                stato.update({"cursore": None, "conclusa_il": adesso.isoformat()})
                await _scrivi_stato(session, "riconciliazione", stato)
                return aggiunti
            stato["cursore"] = str(ultimo)
            await _scrivi_stato(session, "riconciliazione", stato)
    return aggiunti


# ── presa in carico ──────────────────────────────────────────────────────────

_PRENDI = (
    "WITH scelti AS ("
    "  SELECT aip_id FROM public.verifiche_programmazione "
    "  WHERE {filtro} prossima_verifica <= :adesso "
    "  ORDER BY prossima_verifica LIMIT :n FOR UPDATE SKIP LOCKED) "
    "UPDATE public.verifiche_programmazione p SET lease_id = :lease, "
    "  lease_detentore = :detentore, lease_scade_il = :scade, prossima_verifica = :scade "
    "FROM scelti WHERE p.aip_id = scelti.aip_id RETURNING p.aip_id"
)


async def prendi_lotto(cfg: ImpostazioniVerifica, adesso: datetime,
                       detentore: str) -> list[PdaDaVerificare]:
    """Prende in lease fino a ``cfg.lotto`` PdA da verificare e ne legge i dati."""
    lease = uuid.uuid4()
    scade = adesso + timedelta(seconds=cfg.lease_secondi)
    parametri = {"adesso": adesso, "lease": lease, "detentore": detentore[:200], "scade": scade}
    async with get_async_session() as session:
        presi = list((await session.execute(
            text(_PRENDI.format(filtro="richiesta_id IS NOT NULL AND")),
            {**parametri, "n": cfg.lotto})).scalars().all())
        if len(presi) < cfg.lotto:
            presi += list((await session.execute(
                text(_PRENDI.format(filtro="")),
                {**parametri, "n": cfg.lotto - len(presi)})).scalars().all())
    if not presi:
        return []
    return await dati_pda(presi, lease)


async def dati_pda(aip_ids: Sequence[uuid.UUID], lease: uuid.UUID) -> list[PdaDaVerificare]:
    """Dati registrati dei PdA presi in lease (due query per lotto, non per PdA)."""
    async with get_async_session() as session:
        righe = (await session.execute(text(
            # programmazione → PdA → PdV (riferimento al RdV definitivo)
            "SELECT p.aip_id, p.tenant_id, p.lease_id, p.richiesta_id, "
            "  p.errori_lettura_consecutivi, p.da_ritentare, p.ultima_verifica, p.anomalia_dal, "
            "  a.urn, a.stato::text AS stato, a.s3_bucket, a.s3_key, a.hash_sha256, "
            "  a.dimensione_bytes, a.sincro_xml_key, a.scadenza_conservazione, "
            "  v.metadata_json -> 'rdv_definitiva' AS rdv, v.classe_doc_id "
            "FROM public.verifiche_programmazione p "
            "JOIN public.aip a ON a.id = p.aip_id "
            "LEFT JOIN public.pdv v ON v.id = a.pdv_id "
            "WHERE p.aip_id = ANY(CAST(:ids AS uuid[])) AND p.lease_id = :lease"),
            {"ids": list(aip_ids), "lease": lease})).mappings().all()
        documenti = (await session.execute(text(
            "SELECT id, aip_id, nome_file, s3_key, hash_sha256, dimensione_bytes, ordine "
            "FROM public.documenti WHERE aip_id = ANY(CAST(:ids AS uuid[])) "
            "ORDER BY aip_id, ordine, id"),
            {"ids": list(aip_ids)})).mappings().all()
    per_pda: dict[uuid.UUID, list[DocumentoDaVerificare]] = {}
    for d in documenti:
        per_pda.setdefault(d["aip_id"], []).append(DocumentoDaVerificare(
            id=d["id"], nome_file=d["nome_file"], chiave=d["s3_key"],
            impronta=d["hash_sha256"], dimensione=d["dimensione_bytes"], ordine=d["ordine"]))
    risultato = []
    for r in righe:
        rdv = r["rdv"]
        if isinstance(rdv, str):
            rdv = json.loads(rdv)
        da_ritentare = r["da_ritentare"]
        if isinstance(da_ritentare, str):
            da_ritentare = json.loads(da_ritentare)
        risultato.append(PdaDaVerificare(
            aip_id=r["aip_id"], tenant_id=r["tenant_id"], urn=r["urn"], stato=r["stato"],
            bucket=r["s3_bucket"], chiave_pda=r["s3_key"], impronta_pda=r["hash_sha256"],
            dimensione_pda=r["dimensione_bytes"] or None, chiave_ipda=r["sincro_xml_key"],
            scadenza_conservazione=r["scadenza_conservazione"],
            rdv=rdv if isinstance(rdv, dict) else None,
            documenti=per_pda.get(r["aip_id"], []), lease_id=r["lease_id"],
            richiesta_id=r["richiesta_id"],
            errori_lettura_consecutivi=r["errori_lettura_consecutivi"],
            da_ritentare=da_ritentare, ultima_verifica=r["ultima_verifica"],
            anomalia_dal=r["anomalia_dal"], classe_id=r["classe_doc_id"]))
    return risultato


async def esci_dalla_rotazione(pda: PdaDaVerificare) -> bool:
    """PdA in scarto: la riga di programmazione si elimina (dato operativo, non
    conservato) e il PdA esce dalla copertura. Il registro resta."""
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "DELETE FROM public.verifiche_programmazione WHERE aip_id = :a AND lease_id = :l "
            "RETURNING tenant_id, ultima_verifica"),
            {"a": pda.aip_id, "l": pda.lease_id})).mappings().first()
        if riga is None:
            return False
        await _togli_copertura(session, riga["tenant_id"], _giorno_di(riga["ultima_verifica"]))
    logger.info("verifica_pda_fuori_rotazione", aip_id=str(pda.aip_id), stato=pda.stato)
    return True


# ── registrazione dell'esito ─────────────────────────────────────────────────

@dataclass
class Conclusione:
    """Che cosa ha prodotto la registrazione dell'esito di un PdA."""

    registrato: bool
    #: T4: il PdA è entrato in scarto durante la verifica: nessun esito registrato
    fuori_rotazione: bool = False
    concluso: bool = False
    esito: str = esiti.OK
    anomalie: list[EsitoOggetto] = field(default_factory=list)
    errore_ripetuto: bool = False
    allarme: bool = False
    tentativo: int = 1


def _riga_registro(o: EsitoOggetto, pda: PdaDaVerificare, adesso: datetime, tentativo: int,
                   detentore: str) -> dict[str, Any]:
    return {
        "verificato_il": adesso, "tenant_id": pda.tenant_id, "aip_id": pda.aip_id,
        "documento_id": o.documento_id, "componente": o.componente,
        "chiave": (o.chiave or None) and o.chiave[:500], "esito": o.esito,
        "impronta_attesa": o.impronta_attesa, "impronta_trovata": o.impronta_trovata,
        "dimensione_attesa": o.dimensione_attesa, "dimensione_trovata": o.dimensione_trovata,
        "retention_modalita": o.retention_modalita, "retention_fino_a": o.retention_fino_a,
        "foglia_attesa": o.foglia_attesa, "foglia_trovata": o.foglia_trovata,
        "tentativo": min(tentativo, 32767), "motivo": o.motivo,
        "richiesta_id": pda.richiesta_id, "esecutore": detentore[:200],
    }


_INSERISCI_REGISTRO = text(
    "INSERT INTO public.verifiche_registro (verificato_il, tenant_id, aip_id, documento_id, "
    " componente, chiave, esito, impronta_attesa, impronta_trovata, dimensione_attesa, "
    " dimensione_trovata, retention_modalita, retention_fino_a, foglia_attesa, foglia_trovata, "
    " tentativo, motivo, richiesta_id, esecutore) VALUES (:verificato_il, :tenant_id, :aip_id, "
    " :documento_id, :componente, :chiave, :esito, :impronta_attesa, :impronta_trovata, "
    " :dimensione_attesa, :dimensione_trovata, :retention_modalita, :retention_fino_a, "
    " :foglia_attesa, :foglia_trovata, :tentativo, :motivo, :richiesta_id, :esecutore)"
)


def _payload_allarme(pda: PdaDaVerificare, adesso: datetime, oggetti: list[EsitoOggetto],
                     esito: str, tentativo: int, ripetuto: bool) -> dict[str, Any]:
    """Evento ``integrita.anomalia``: identificativi, esiti e impronte; nessun nome
    di file né chiave di storage (possono contenere dati personali, REGOLE §19.9)."""
    return {
        "tipo": esiti.TOPIC_ANOMALIA, "versione": 1,
        "aip_id": str(pda.aip_id), "pda": pda.urn, "tenant_id": str(pda.tenant_id),
        "verificato_il": adesso.isoformat(), "esito": esito, "tentativo": tentativo,
        "errore_lettura_ripetuto": ripetuto,
        "oggetti": [{
            "componente": o.componente,
            "documento_id": str(o.documento_id) if o.documento_id else None,
            "esito": o.esito, "impronta_attesa": o.impronta_attesa,
            "impronta_trovata": o.impronta_trovata, "motivo": o.motivo,
        } for o in oggetti],
    }


async def registra_esito(esito_pda: EsitoPda, adesso: datetime, cfg: ImpostazioniVerifica,
                         detentore: str, *, ciclo_giorni: int | None = None) -> Conclusione:
    """Registra in UNA transazione l'esito di un PdA (vedi docstring del modulo).

    Recinto: se il lease non è più di questo lotto (scaduto e ripreso da
    un'altra replica) non si scrive nulla: la verifica la registra chi ha il lease.

    R09: ``ciclo_giorni`` del PdA (D05: per ente o classe, ``cicli.Cicli``);
    un PdA entrato in eliminazione incompleta DURANTE la lettura (D26) non
    registra nulla e torna subito in coda (gli oggetti cancellati nel frattempo
    non diventano anomalie)."""
    ciclo = int(ciclo_giorni or cfg.ciclo_giorni)
    pda = esito_pda.pda
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "SELECT richiesta_id, errori_lettura_consecutivi, ultima_verifica, anomalia_dal "
            "FROM public.verifiche_programmazione WHERE aip_id = :a AND lease_id = :l FOR UPDATE"),
            {"a": pda.aip_id, "l": pda.lease_id})).mappings().first()
        if riga is None:
            logger.warning("verifica_lease_perso", aip_id=str(pda.aip_id), detentore=detentore)
            return Conclusione(registrato=False)
        # Revisione 3 di R05 (T4): stato del PdA riletto ADESSO, con la riga di
        # programmazione bloccata: uno scarto iniziato DURANTE la lettura (oggetti
        # cancellati nel frattempo) non diventa un'anomalia d'integrità
        attuale = (await session.execute(text(
            "SELECT stato::text FROM public.aip WHERE id = :a"),
            {"a": pda.aip_id})).scalar_one_or_none()
        if attuale in STATI_FUORI_ROTAZIONE:
            uscita = (await session.execute(text(
                "DELETE FROM public.verifiche_programmazione WHERE aip_id = :a "
                "RETURNING tenant_id, ultima_verifica"), {"a": pda.aip_id})).mappings().first()
            if uscita is not None:
                await _togli_copertura(session, uscita["tenant_id"],
                                       _giorno_di(uscita["ultima_verifica"]))
            logger.info("verifica_pda_fuori_rotazione", aip_id=str(pda.aip_id), stato=attuale,
                        durante_la_verifica=True)
            return Conclusione(registrato=False, fuori_rotazione=True)
        if attuale == STATO_INCOMPLETO and pda.stato != STATO_INCOMPLETO:
            await session.execute(text(
                "UPDATE public.verifiche_programmazione SET prossima_verifica = :adesso, "
                "lease_id = NULL, lease_detentore = NULL, lease_scade_il = NULL "
                "WHERE aip_id = :a"), {"a": pda.aip_id, "adesso": adesso})
            logger.info("verifica_pda_diventato_incompleto", aip_id=str(pda.aip_id))
            return Conclusione(registrato=False, fuori_rotazione=True)
        tentativo = int(riga["errori_lettura_consecutivi"]) + 1
        oggetti = esito_pda.oggetti
        if oggetti:
            await session.execute(_INSERISCI_REGISTRO, [
                _riga_registro(o, pda, adesso, tentativo, detentore) for o in oggetti])
        errori = [o for o in oggetti if o.esito == esiti.ERRORE_LETTURA]
        anomalie = [o for o in oggetti if o.esito in esiti.ANOMALIE]
        precedente = (pda.da_ritentare or {}).get("peggiore") or esiti.OK
        letti = esiti.peggiore([o.esito for o in oggetti if o.esito != esiti.ERRORE_LETTURA]
                               + [precedente])
        marcata_ora = riga["richiesta_id"]
        stessa_richiesta = marcata_ora is not None and marcata_ora == pda.richiesta_id
        conclusione = Conclusione(registrato=True, anomalie=anomalie, tentativo=tentativo)
        if errori:
            ripetuto = tentativo >= cfg.soglia_errore_lettura
            conclusione.errore_ripetuto = ripetuto
            conclusione.esito = esiti.peggiore([letti, esiti.ERRORE_LETTURA]) if ripetuto else letti
            anomalia_dal = riga["anomalia_dal"]
            if (anomalie or ripetuto) and anomalia_dal is None:
                anomalia_dal = adesso
            conta = ripetuto and stessa_richiesta
            await session.execute(text(
                "UPDATE public.verifiche_programmazione SET errori_lettura_consecutivi = :n, "
                " prossima_verifica = :prossima, da_ritentare = CAST(:da AS jsonb), "
                " anomalia_dal = :anomalia, "
                " ultimo_esito = CASE WHEN CAST(:ripetuto AS boolean) THEN CAST(:esito AS varchar) "
                "   ELSE ultimo_esito END, "
                " richiesta_id = CASE WHEN CAST(:conta AS boolean) THEN NULL "
                "   ELSE richiesta_id END, "
                " lease_id = NULL, lease_detentore = NULL, lease_scade_il = NULL "
                "WHERE aip_id = :a"),
                {"n": min(tentativo, 32767),
                 "prossima": adesso + esiti.ritardo_errore(
                     tentativo, cfg.ritardi_errore_lettura_secondi),
                 "da": json.dumps({"componenti": sorted({o.chiave_ritentativo for o in errori}),
                                   "peggiore": letti, "ipda_attesa": esito_pda.ipda_attesa}),
                 "anomalia": anomalia_dal, "ripetuto": ripetuto, "esito": conclusione.esito,
                 "conta": conta, "a": pda.aip_id})
            conclusione.allarme = bool(anomalie) or tentativo == cfg.soglia_errore_lettura
            oggetti_allarme = anomalie + (errori if ripetuto else [])
        else:
            conclusione.concluso = True
            conclusione.esito = letti
            # Marcata da una richiesta arrivata durante il lease: resta marcata e
            # torna subito in coda (la lettura potrebbe precedere la richiesta).
            ri_marcata = marcata_ora is not None and not stessa_richiesta
            conta = stessa_richiesta
            await session.execute(text(
                "UPDATE public.verifiche_programmazione SET ultima_verifica = :adesso, "
                " ultimo_esito = CAST(:esito AS varchar), prossima_verifica = :prossima, "
                " errori_lettura_consecutivi = 0, da_ritentare = NULL, "
                " anomalia_dal = CASE WHEN CAST(:esito AS varchar) = 'OK' THEN NULL "
                "   ELSE COALESCE(anomalia_dal, CAST(:adesso AS timestamptz)) END, "
                " verifiche_eseguite = verifiche_eseguite + 1, "
                " richiesta_id = CASE WHEN CAST(:conta AS boolean) THEN NULL "
                "   ELSE richiesta_id END, "
                " lease_id = NULL, lease_detentore = NULL, lease_scade_il = NULL "
                "WHERE aip_id = :a"),
                {"adesso": adesso, "esito": letti, "conta": conta, "a": pda.aip_id,
                 "prossima": adesso if ri_marcata else adesso + timedelta(days=ciclo)})
            vecchio, nuovo = _giorno_di(riga["ultima_verifica"]), _giorno_di(adesso)
            if vecchio != nuovo:
                await _togli_copertura(session, pda.tenant_id, vecchio)
                await _aggiungi_copertura(session, Counter({pda.tenant_id: 1}), nuovo)
            conclusione.allarme = bool(anomalie)
            oggetti_allarme = anomalie
        if conclusione.allarme:
            await accoda_evento(
                session, topic=esiti.TOPIC_ANOMALIA, chiave=str(pda.aip_id),
                payload=_payload_allarme(pda, adesso, oggetti_allarme, conclusione.esito,
                                         tentativo, conclusione.errore_ripetuto),
                tenant_id=pda.tenant_id)
        if conta and pda.richiesta_id is not None:
            # Ultima istruzione: il lock sulla riga della richiesta dura il meno possibile
            await session.execute(text(
                "UPDATE public.verifiche_richieste SET "
                " oggetti_verificati = oggetti_verificati + 1, anomalie = anomalie + :anomalia, "
                " stato = CASE WHEN preparazione_conclusa "
                "   AND oggetti_verificati + 1 >= oggetti_totali THEN 'COMPLETATA' ELSE stato END, "
                " conclusa_il = CASE WHEN preparazione_conclusa "
                "   AND oggetti_verificati + 1 >= oggetti_totali THEN :adesso ELSE conclusa_il END "
                "WHERE id = :r"),
                {"anomalia": 1 if conclusione.esito != esiti.OK else 0, "adesso": adesso,
                 "r": pda.richiesta_id})
    return conclusione


# ── richieste ────────────────────────────────────────────────────────────────

#: R09 (D05): stato della verifica a campione dopo gli incidenti di storage
STATO_INCIDENTE = "incidente_campione"


async def campione(n: int, *, richiesta_da: str | None, ruolo: str, adesso: datetime
                   ) -> dict[str, Any] | None:
    """R09 (D05): richiesta di verifica su ``n`` PdA presi a caso (punto di
    partenza casuale sulla chiave della programmazione: una lettura d'indice,
    mai un ordinamento casuale della tabella). None se non c'è nulla."""
    partenza = uuid.uuid4()
    async with get_async_session() as session:
        ids = [str(i) for i in (await session.execute(text(
            "(SELECT aip_id FROM public.verifiche_programmazione WHERE aip_id >= :p "
            " ORDER BY aip_id LIMIT :n) UNION ALL "
            "(SELECT aip_id FROM public.verifiche_programmazione WHERE aip_id < :p "
            " ORDER BY aip_id LIMIT :n)"), {"p": partenza, "n": n})).scalars().all()][:n]
    if not ids:
        return None
    try:
        return await crea_richiesta_pda(ids, tenant=None, richiesta_da=richiesta_da,
                                        ruolo=ruolo, adesso=adesso)
    except NonTrovati as exc:   # PdA usciti dalla rotazione nel frattempo: si tolgono
        restanti = [i for i in ids if i not in set(exc.identificativi)]
        if not restanti:
            return None
        return await crea_richiesta_pda(restanti, tenant=None, richiesta_da=richiesta_da,
                                        ruolo=ruolo, adesso=adesso)


async def campione_dopo_incidente(adesso: datetime, *, n: int, pausa: timedelta,
                                  motivo: str) -> dict[str, Any] | None:
    """R09 (D05): verifica a campione avviata in automatico da un incidente di
    storage (anomalia d'integrità o errori di lettura ripetuti): al più una ogni
    ``pausa`` (stato con lock: più repliche, un solo campione)."""
    async with get_async_session() as session:
        stato = await _leggi_stato(session, STATO_INCIDENTE, blocca=True)
        ultimo = stato.get("ultimo")
        if ultimo and datetime.fromisoformat(ultimo) > adesso - pausa:
            return None
        await _scrivi_stato(session, STATO_INCIDENTE, {"ultimo": adesso.isoformat(),
                                                       "motivo": motivo[:300]})
    richiesta = await campione(n, richiesta_da=None, ruolo="SISTEMA:INCIDENTE", adesso=adesso)
    logger.warning("verifica_campione_dopo_incidente", motivo=motivo[:300],
                   richiesta_id=(richiesta or {}).get("richiesta_id"), pda=n)
    return richiesta


_MARCA = (
    "UPDATE public.verifiche_programmazione p SET richiesta_id = :r, "
    # Mai anticipare una riga in lease: tornerebbe prendibile da un'altra replica
    " prossima_verifica = CASE WHEN p.lease_scade_il IS NOT NULL AND p.lease_scade_il > :adesso "
    "   THEN p.prossima_verifica ELSE LEAST(p.prossima_verifica, :adesso) END "
)


async def prepara_richieste(cfg: ImpostazioniVerifica, adesso: datetime,
                            lotti: int = 20) -> int:
    """Marca a lotti i PdA delle richieste d'ente in preparazione; restituisce i marcati."""
    marcati_totali = 0
    for _ in range(lotti):
        async with get_async_session() as session:
            r = (await session.execute(text(
                "SELECT id, tenant_id, cursore_preparazione, oggetti_totali, oggetti_verificati "
                "FROM public.verifiche_richieste WHERE stato = 'IN_PREPARAZIONE' "
                "ORDER BY creata_il LIMIT 1 FOR UPDATE SKIP LOCKED"))).mappings().first()
            if r is None:
                break
            cursore = r["cursore_preparazione"] or uuid.UUID(int=0)
            esito = (await session.execute(text(
                "WITH scelti AS (SELECT aip_id, richiesta_id FROM public.verifiche_programmazione "  # noqa: S608 — frammenti SQL costanti, valori come parametri
                "  WHERE tenant_id = :t AND aip_id > :c ORDER BY aip_id LIMIT :n FOR UPDATE), "
                "marcati AS (" + _MARCA + " FROM scelti s WHERE p.aip_id = s.aip_id "
                "  AND s.richiesta_id IS NULL RETURNING p.aip_id) "
                "SELECT (SELECT count(*) FROM scelti) AS letti, "
                "  (SELECT count(*) FROM marcati) AS marcati, "
                "  (SELECT aip_id FROM scelti ORDER BY aip_id DESC LIMIT 1) AS ultimo"),
                {"t": r["tenant_id"], "c": cursore, "n": cfg.preparazione_lotto,
                 "r": r["id"], "adesso": adesso})).mappings().one()
            letti, marcati = int(esito["letti"]), int(esito["marcati"])
            totali = int(r["oggetti_totali"]) + marcati
            conclusa = letti < cfg.preparazione_lotto
            stato = "IN_PREPARAZIONE"
            if conclusa:
                stato = "COMPLETATA" if int(r["oggetti_verificati"]) >= totali else "IN_CORSO"
            await session.execute(text(
                "UPDATE public.verifiche_richieste SET oggetti_totali = :tot, "
                " gia_in_verifica = gia_in_verifica + :gia, cursore_preparazione = :cur, "
                " preparazione_conclusa = :conclusa, stato = CAST(:stato AS varchar), "
                " conclusa_il = CASE WHEN CAST(:stato AS varchar) = 'COMPLETATA' "
                "   THEN CAST(:adesso AS timestamptz) ELSE NULL END "
                "WHERE id = :r"),
                {"tot": totali, "gia": letti - marcati, "cur": esito["ultimo"] or cursore,
                 "conclusa": conclusa, "stato": stato, "adesso": adesso, "r": r["id"]})
            marcati_totali += marcati
    return marcati_totali


class NonTrovati(LookupError):
    """Identificativi di PdA non trovati (o non visibili) per chi chiede."""

    def __init__(self, identificativi: list[str]) -> None:
        super().__init__(f"{len(identificativi)} PdA non trovati")
        self.identificativi = identificativi


class RichiestaEnteAttiva(Exception):
    def __init__(self, richiesta_id: uuid.UUID) -> None:
        super().__init__(str(richiesta_id))
        self.richiesta_id = richiesta_id


def _dividi_identificativi(valori: Sequence[str]) -> tuple[list[uuid.UUID], list[str]]:
    ids: list[uuid.UUID] = []
    urn: list[str] = []
    for v in valori:
        try:
            ids.append(uuid.UUID(v))
        except ValueError:
            urn.append(v)
    return ids, urn


async def crea_richiesta_pda(
    identificativi: Sequence[str], *, tenant: uuid.UUID | None, richiesta_da: str | None,
    ruolo: str, adesso: datetime,
) -> dict[str, Any]:
    """Richiesta su un elenco di PdA (id o URN). ``tenant`` limita la ricerca ai
    PdA dell'ente di chi chiede (None = personale del conservatore)."""
    unici = list(dict.fromkeys(identificativi))
    ids, urn = _dividi_identificativi(unici)
    async with get_async_session() as session:
        trovati = (await session.execute(text(
            "SELECT id, tenant_id, urn FROM public.aip "  # noqa: S608 — frammenti SQL costanti, valori come parametri
            "WHERE (id = ANY(CAST(:ids AS uuid[])) OR urn = ANY(CAST(:urn AS text[]))) "
            "AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid)) "
            f"AND stato::text IN ({_STATI_SQL})"),
            {"ids": ids, "urn": urn, "t": tenant})).mappings().all()
        visti = {str(r["id"]) for r in trovati} | {r["urn"] for r in trovati}
        mancanti = [v for v in unici if v not in visti
                    and not (_uuid_o_none(v) and str(_uuid_o_none(v)) in visti)]
        if mancanti:
            raise NonTrovati(mancanti)
        enti = {r["tenant_id"] for r in trovati}
        aip_ids = sorted({r["id"] for r in trovati})
        richiesta_id = uuid.uuid4()
        await session.execute(text(
            "INSERT INTO public.verifiche_richieste (id, tenant_id, ambito, stato, pda_richiesti, "
            " richiesta_da, ruolo, preparazione_conclusa, creata_il) "
            "VALUES (:id, :t, 'PDA', 'IN_CORSO', CAST(:pda AS jsonb), :da, :ruolo, true, :adesso)"),
            {"id": richiesta_id, "t": next(iter(enti)) if len(enti) == 1 else None,
             "pda": json.dumps([str(a) for a in aip_ids]), "da": richiesta_da,
             "ruolo": ruolo[:50], "adesso": adesso})
        await _inserisci_programmazione(
            session, [(r["id"], r["tenant_id"], adesso) for r in trovati])
        marcati = (await session.execute(text(
            _MARCA + "WHERE p.aip_id = ANY(CAST(:ids AS uuid[])) AND p.richiesta_id IS NULL "
            "RETURNING p.aip_id"),
            {"r": richiesta_id, "adesso": adesso, "ids": aip_ids})).scalars().all()
        totali = len(marcati)
        await session.execute(text(
            "UPDATE public.verifiche_richieste SET oggetti_totali = :tot, gia_in_verifica = :gia, "
            " stato = CASE WHEN :tot = 0 THEN 'COMPLETATA' ELSE stato END, "
            " conclusa_il = CASE WHEN :tot = 0 THEN CAST(:adesso AS timestamptz) ELSE NULL END "
            "WHERE id = :id"),
            {"tot": totali, "gia": len(aip_ids) - totali, "adesso": adesso, "id": richiesta_id})
    return await leggi_richiesta(richiesta_id)  # type: ignore[return-value]


def _uuid_o_none(valore: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(valore)
    except ValueError:
        return None


async def ente_esiste(tenant: uuid.UUID) -> bool:
    async with get_async_session() as session:
        return (await session.execute(text("SELECT 1 FROM public.tenants WHERE id = :t"),
                                      {"t": tenant})).first() is not None


async def crea_richiesta_ente(tenant: uuid.UUID, *, richiesta_da: str | None, ruolo: str,
                              adesso: datetime) -> dict[str, Any]:
    """Richiesta su tutti i PdA dell'ente; la marcatura la fa il lavoratore a lotti.
    ``RichiestaEnteAttiva`` se ce n'è già una non completata (indice unico)."""
    async with get_async_session() as session:
        nuova = (await session.execute(text(
            "INSERT INTO public.verifiche_richieste (tenant_id, ambito, stato, richiesta_da, "
            " ruolo, creata_il) VALUES (:t, 'ENTE', 'IN_PREPARAZIONE', :da, :ruolo, :adesso) "
            "ON CONFLICT (tenant_id) WHERE ambito = 'ENTE' AND stato <> 'COMPLETATA' "
            "DO NOTHING RETURNING id"),
            {"t": tenant, "da": richiesta_da, "ruolo": ruolo[:50], "adesso": adesso})
        ).scalar_one_or_none()
        if nuova is None:
            attiva = (await session.execute(text(
                "SELECT id FROM public.verifiche_richieste WHERE tenant_id = :t "
                "AND ambito = 'ENTE' AND stato <> 'COMPLETATA'"), {"t": tenant})).scalar_one()
            raise RichiestaEnteAttiva(attiva)
    return await leggi_richiesta(nuova)  # type: ignore[return-value]


def _iso(valore: datetime | None) -> str | None:
    return valore.isoformat() if valore else None


async def leggi_richiesta(richiesta_id: uuid.UUID) -> dict[str, Any] | None:
    async with get_async_session() as session:
        r = (await session.execute(text(
            "SELECT id, tenant_id, ambito, stato, pda_richiesti, richiesta_da, ruolo, "
            " oggetti_totali, oggetti_verificati, gia_in_verifica, anomalie, "
            " preparazione_conclusa, creata_il, conclusa_il "
            "FROM public.verifiche_richieste WHERE id = :id"),
            {"id": richiesta_id})).mappings().first()
    if r is None:
        return None
    pda = r["pda_richiesti"]
    if isinstance(pda, str):
        pda = json.loads(pda)
    return {
        "richiesta_id": str(r["id"]), "ente": str(r["tenant_id"]) if r["tenant_id"] else None,
        "ambito": r["ambito"], "stato": r["stato"],
        "pda": list(pda) if pda is not None else None,
        "oggetti_totali": r["oggetti_totali"], "oggetti_verificati": r["oggetti_verificati"],
        "gia_in_verifica": r["gia_in_verifica"], "anomalie": r["anomalie"],
        "preparazione_conclusa": r["preparazione_conclusa"],
        "richiesta_da": r["richiesta_da"], "ruolo": r["ruolo"],
        "creata_il": _iso(r["creata_il"]), "conclusa_il": _iso(r["conclusa_il"]),
    }


# ── registro a cursore ───────────────────────────────────────────────────────

class CursoreNonValido(ValueError):
    pass


def codifica_cursore(verificato_il: datetime, riga_id: int) -> str:
    grezzo = f"{verificato_il.astimezone(UTC).isoformat()}|{riga_id}".encode()
    return base64.urlsafe_b64encode(grezzo).decode().rstrip("=")


def decodifica_cursore(cursore: str) -> tuple[datetime, int]:
    try:
        grezzo = base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)).decode()
        istante, riga_id = grezzo.split("|", 1)
        valore = datetime.fromisoformat(istante)
        if valore.tzinfo is None:
            raise ValueError("istante senza fuso orario")
        return valore, int(riga_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CursoreNonValido(str(exc)) from None


async def pda_di_ente(identificativo: str, tenant: uuid.UUID | None) -> uuid.UUID | None:
    """Id del PdA (per id o URN) visibile nel perimetro (None = non trovato)."""
    aip_id = _uuid_o_none(identificativo)
    async with get_async_session() as session:
        return (await session.execute(text(
            "SELECT id FROM public.aip WHERE "  # noqa: S608 — frammenti SQL costanti, valori come parametri
            + ("id = :v" if aip_id else "urn = :v")
            + " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"),
            {"v": aip_id or identificativo, "t": tenant})).scalar_one_or_none()


async def elenco_registro(
    *, tenant: uuid.UUID | None, aip_id: uuid.UUID | None, esito: str | None,
    cursore: str | None, limite: int,
) -> dict[str, Any]:
    """Voci del registro dalla più recente, a cursore ``(verificato_il, id)``.
    Una query per la pagina e una per gli URN dei PdA della pagina."""
    condizioni = ["TRUE"]
    parametri: dict[str, Any] = {"n": limite + 1}
    if tenant is not None:
        condizioni.append("r.tenant_id = :t")
        parametri["t"] = tenant
    if aip_id is not None:
        condizioni.append("r.aip_id = :a")
        parametri["a"] = aip_id
    if esito == "ANOMALIE":
        condizioni.append("r.esito <> 'OK'")
    elif esito is not None:
        condizioni.append("r.esito = :e")
        parametri["e"] = esito
    if cursore:
        istante, riga_id = decodifica_cursore(cursore)
        condizioni.append("(r.verificato_il, r.id) < (:cv, :ci)")
        parametri.update({"cv": istante, "ci": riga_id})
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT r.id, r.verificato_il, r.tenant_id, r.aip_id, r.documento_id, r.componente, "
            " r.esito, r.algoritmo, r.impronta_attesa, r.impronta_trovata, r.dimensione_attesa, "
            " r.dimensione_trovata, r.retention_modalita, r.retention_fino_a, r.foglia_attesa, "
            " r.foglia_trovata, r.tentativo, r.motivo, r.richiesta_id "
            "FROM public.verifiche_registro r WHERE " + " AND ".join(condizioni)
            + " ORDER BY r.verificato_il DESC, r.id DESC LIMIT :n"), parametri)).mappings().all()
        pagina = righe[:limite]
        urn: dict[uuid.UUID, str] = {}
        if pagina:
            urn = {r[0]: r[1] for r in (await session.execute(text(
                "SELECT id, urn FROM public.aip WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": list({r["aip_id"] for r in pagina})})).all()}
    voci = [{
        "verificato_il": _iso(r["verificato_il"]), "ente": str(r["tenant_id"]),
        "pda_id": str(r["aip_id"]), "pda": urn.get(r["aip_id"]),
        "documento_id": str(r["documento_id"]) if r["documento_id"] else None,
        "componente": r["componente"], "esito": r["esito"], "algoritmo": r["algoritmo"],
        "impronta_attesa": r["impronta_attesa"], "impronta_trovata": r["impronta_trovata"],
        "dimensione_attesa": r["dimensione_attesa"], "dimensione_trovata": r["dimensione_trovata"],
        "retention_modalita": r["retention_modalita"],
        "retention_fino_a": _iso(r["retention_fino_a"]),
        "foglia_attesa": r["foglia_attesa"], "foglia_trovata": r["foglia_trovata"],
        "tentativo": r["tentativo"], "motivo": r["motivo"],
        "richiesta_id": str(r["richiesta_id"]) if r["richiesta_id"] else None,
    } for r in pagina]
    successivo = None
    if len(righe) > limite and pagina:
        successivo = codifica_cursore(pagina[-1]["verificato_il"], pagina[-1]["id"])
    return {"voci": voci, "cursore_successivo": successivo}


# ── riepilogo, manutenzione, metriche ────────────────────────────────────────

async def copertura_per_giorno(tenant: uuid.UUID | None,
                               adesso: datetime) -> tuple[dict[date, int], str | None]:
    """Copertura per giorno e istante del calcolo: per un ente dalla tabella (righe ≤
    giorni conservati, calcolo al momento), per tutti gli enti dalla copia
    calcolata dal lavoratore (``riepilogo_globale``, ogni VERIFICA_RIEPILOGO_INTERVALLO_SECONDI)."""
    async with get_async_session() as session:
        if tenant is not None:
            righe = (await session.execute(text(
                "SELECT giorno, oggetti FROM public.verifiche_copertura WHERE tenant_id = :t"),
                {"t": tenant})).all()
            return {g: int(n) for g, n in righe}, adesso.isoformat()
        stato = await _leggi_stato(session, "riepilogo_globale")
    giorni = {date.fromisoformat(g): int(n) for g, n in (stato.get("giorni") or {}).items()}
    return giorni, stato.get("calcolato_il")


async def aggiorna_riepilogo_globale(adesso: datetime) -> None:
    """Somma per giorno della copertura di tutti gli enti (fuori dalle richieste HTTP)."""
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT giorno, sum(oggetti) FROM public.verifiche_copertura GROUP BY giorno"))).all()
        await _scrivi_stato(session, "riepilogo_globale", {
            "giorni": {g.isoformat(): int(n) for g, n in righe},
            "calcolato_il": adesso.isoformat()})


async def compatta_copertura(cfg: ImpostazioniVerifica, adesso: datetime) -> None:
    """Accorpa i giorni più vecchi della finestra conservata nel giorno «vecchi»."""
    soglia = (adesso - timedelta(days=cfg.copertura_conserva_giorni)).date()
    async with get_async_session() as session:
        if not await _lock_semina(session):
            return
        await session.execute(text(
            "WITH vecchie AS (DELETE FROM public.verifiche_copertura "
            "  WHERE giorno > :vecchi AND giorno < :soglia RETURNING tenant_id, oggetti) "
            "INSERT INTO public.verifiche_copertura (tenant_id, giorno, oggetti) "
            "SELECT tenant_id, :vecchi, sum(oggetti) FROM vecchie GROUP BY tenant_id "
            "ON CONFLICT (tenant_id, giorno) DO UPDATE "
            "SET oggetti = public.verifiche_copertura.oggetti + EXCLUDED.oggetti"),
            {"vecchi": GIORNO_VECCHI, "soglia": soglia})


async def anomalie_aperte(tenant: uuid.UUID | None, *, elenco: int = 20) -> dict[str, Any]:
    """Anomalie aperte (conteggio con tetto, sull'indice parziale) e le più recenti."""
    filtro = "AND p.tenant_id = :t" if tenant is not None else ""
    async with get_async_session() as session:
        numero = (await session.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM public.verifiche_programmazione p "  # noqa: S608 — frammenti SQL costanti, valori come parametri
            f"WHERE p.anomalia_dal IS NOT NULL {filtro} LIMIT :tetto) x"),
            {"t": tenant, "tetto": TETTO_ANOMALIE + 1})).scalar_one()
        righe = (await session.execute(text(
            "SELECT p.aip_id, p.tenant_id, p.ultimo_esito, p.anomalia_dal, "  # noqa: S608 — frammenti SQL costanti, valori come parametri
            " p.errori_lettura_consecutivi, a.urn FROM public.verifiche_programmazione p "
            "JOIN public.aip a ON a.id = p.aip_id "
            f"WHERE p.anomalia_dal IS NOT NULL {filtro} "
            "ORDER BY p.anomalia_dal DESC LIMIT :n"), {"t": tenant, "n": elenco})).mappings().all()
    return {
        "numero": min(int(numero), TETTO_ANOMALIE), "oltre_tetto": int(numero) > TETTO_ANOMALIE,
        "elenco": [{
            "pda_id": str(r["aip_id"]), "pda": r["urn"], "ente": str(r["tenant_id"]),
            "esito": r["ultimo_esito"] or (esiti.ERRORE_LETTURA
                                          if r["errori_lettura_consecutivi"] else None),
            "aperta_dal": _iso(r["anomalia_dal"]),
        } for r in righe],
    }


async def ritardo_rotazione(adesso: datetime) -> float:
    """Secondi di ritardo del PdA in attesa da più tempo (0 = in pari)."""
    async with get_async_session() as session:
        minimo = (await session.execute(text(
            "SELECT min(prossima_verifica) FROM public.verifiche_programmazione"))).scalar_one()
    if minimo is None:
        return 0.0
    return max(0.0, float((adesso - minimo).total_seconds()))


async def ultima_verifica_registrata(tenant: uuid.UUID | None) -> datetime | None:
    filtro = "WHERE tenant_id = :t" if tenant is not None else ""
    async with get_async_session() as session:
        return (await session.execute(text(
            f"SELECT verificato_il FROM public.verifiche_registro {filtro} "  # noqa: S608 — frammenti SQL costanti, valori come parametri
            "ORDER BY verificato_il DESC LIMIT 1"), {"t": tenant})).scalar_one_or_none()


async def stato_lavoratore() -> dict[str, Any]:
    async with get_async_session() as session:
        return await _leggi_stato(session, "lavoratore")


async def segnala_lavoratore(detentore: str, fonte_foglie: str, adesso: datetime) -> None:
    """Battito del lavoratore (letto dal riepilogo: lavoratore attivo e fonte delle foglie)."""
    async with get_async_session() as session:
        await _scrivi_stato(session, "lavoratore", {
            "detentore": detentore, "fonte_foglie": fonte_foglie, "giro_il": adesso.isoformat()})
