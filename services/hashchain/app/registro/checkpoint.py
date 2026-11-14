# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Checkpoint del registro: emissione, sigillo qualificato, controfirma del
testimone, allarme (contratti K2 e K3 di R04).

Servizio: hashchain. Riferimenti: piano §5.2-§5.3; REGOLE_SVILUPPO §10.1,
§12.3 (outbox), §12.11 (effetti esterni dopo il commit), §20.2 (stato onesto).

Emissione (``emetti_checkpoint``), solo se il registro è cresciuto:
  1. esporta nel WORM le foglie non ancora esportate (segmenti ``.jsonl``),
     così il registro si ricostruisce dallo storage;
  2. calcola la radice dai nodi salvati (O(log n)), compone il corpo C2SP e lo
     firma con la chiave del registro (Ed25519, chiave di SERVIZIO);
  3. scrive la nota nel WORM (``registro/checkpoint/<dimensione>.txt``);
  4. in UNA transazione: riga ``catena_checkpoint`` (sigillo DA_FIRMARE,
     testimone IN_ATTESA o NON_CONFIGURATO), checkpoint precedenti ancora in
     attesa di controfirma → SUPERATO, evento ``catena.checkpoint`` in outbox.
     Il checkpoint è «pubblicato» (API, evento) solo dopo DB e WORM.

Dopo il commit, ripresi a ogni giro finché non hanno un esito definitivo:
  - ``sigilla_in_sospeso``: sigillo qualificato e marca RFC 3161 del corpo del
    checkpoint tramite il servizio signature (stesso client dei blocchi);
    senza fornitore qualificato → NON_FIRMATO con motivo (nessuna finzione);
  - ``invia_al_testimone``: protocollo K3; la controfirma verificata si
    registra e la nota controfirmata va nel WORM;
  - ``controlla_controfirme``: l'ultimo checkpoint senza controfirma oltre
    ``CATENA_CONTROFIRMA_MAX_SECONDI`` è un allarme (log + metrica);
  - ``controlla_rifiuti``: ogni checkpoint RIFIUTATO dal testimone (403, 422,
    423, controfirma non verificabile) è un allarme PERSISTENTE: resta nel DB
    finché un operatore non ne registra la gestione
    (``python -m app.registro.operatore``) e, finché resta aperto, nessun PdA
    passa a CONSERVATO (rev4 rilievo 1). Un checkpoint rifiutato non diventa
    mai SUPERATO né utilizzabile.
"""
from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.registro import archivio, archivio_worm
from app.registro.metriche import metriche
from app.registro.testimone import (
    CAUSA_DIMENSIONE,
    ESITO_CONTROFIRMATO,
    ESITO_DA_RITENTARE,
    ESITO_DIMENSIONE_DIVERSA,
    ESITO_RIFIUTATO,
    ClientTestimone,
)
from app.services.firma_blocchi import (
    STATO_FIRMATO,
    ServizioFirmaBlocchi,
)
from libs.catena import albero, nota
from libs.catena.foglie import iso_utc
from libs.shared.db import get_async_session
from libs.shared.outbox import accoda_evento

logger = structlog.get_logger(__name__)

TOPIC_CHECKPOINT = "catena.checkpoint"
STATI_ATTESA_TESTIMONE = ("IN_ATTESA",)


@dataclass(frozen=True)
class Identita:
    """Chiave del registro e origine dei checkpoint."""

    firmatario: nota.Firmatario
    origine: str
    testimone_configurato: bool


async def esporta_foglie(*, detentore: str, adesso: datetime, segmento: int) -> int:
    """Esporta nel WORM le foglie [esportate, dimensione) a segmenti; restituisce
    il nuovo numero di foglie esportate."""
    async with get_async_session() as session:
        stato = await archivio.leggi_stato(session)
    esportate = stato.worm_foglie_esportate
    while esportate < stato.dimensione:
        fine = min(stato.dimensione, esportate + segmento)
        async with get_async_session() as session:
            dati = await archivio.leggi_dati_foglie(session, esportate, fine)
        contenuto = ("\n".join(dati) + "\n").encode("utf-8")
        await archivio_worm.scrivi(archivio_worm.chiave_segmento(esportate, fine), contenuto,
                                   "application/x-ndjson", adesso)
        async with get_async_session() as session:
            await archivio.stato_con_lease(session, detentore, adesso)
            await session.execute(text(
                "UPDATE public.catena_stato SET worm_foglie_esportate = :n WHERE id = 1 "
                "AND worm_foglie_esportate < :n"
            ), {"n": fine})
        esportate = fine
    return esportate


async def emetti_checkpoint(*, detentore: str, adesso: datetime, identita: Identita,
                            segmento: int) -> dict[str, Any] | None:
    """Emette un checkpoint se il registro è cresciuto dall'ultimo (vedi modulo)."""
    async with get_async_session() as session:
        stato = await archivio.leggi_stato(session)
        ultimo = await archivio.ultimo_checkpoint(session)
    n = stato.dimensione
    if n == 0 or (ultimo is not None and int(ultimo["dimensione"]) >= n):
        return None
    await esporta_foglie(detentore=detentore, adesso=adesso, segmento=segmento)

    async with get_async_session() as session:
        nodi = await archivio.leggi_nodi(session, albero.nodi_per_radice(n))
    radice = albero.radice_da_nodi(n, nodi)
    corpo = nota.formatta(nota.Checkpoint(identita.origine, n, radice))
    firmata = nota.componi(corpo, [identita.firmatario.firma(corpo)])
    bucket, versione = await archivio_worm.scrivi(
        archivio_worm.chiave_checkpoint(n), firmata.encode("utf-8"),
        "text/plain; charset=utf-8", adesso)

    stato_testimone = "IN_ATTESA" if identita.testimone_configurato else "NON_CONFIGURATO"
    async with get_async_session() as session:
        await archivio.stato_con_lease(session, detentore, adesso)
        inserito = await session.execute(text(
            "INSERT INTO public.catena_checkpoint (dimensione, radice, origine, testo, "
            "chiave_verificatore, emesso_il, worm_bucket, worm_chiave, worm_version_id, "
            "stato_sigillo, stato_testimone) VALUES (:n, :radice, :origine, :testo, :chiave, "
            ":emesso, :bucket, :worm_chiave, :versione, 'DA_FIRMARE', :testimone) "
            "ON CONFLICT (dimensione) DO NOTHING"
        ), {"n": n, "radice": radice.hex(), "origine": identita.origine, "testo": firmata,
            "chiave": identita.firmatario.verificatore.testo, "emesso": adesso,
            "bucket": bucket, "worm_chiave": archivio_worm.chiave_checkpoint(n),
            "versione": versione, "testimone": stato_testimone})
        if inserito.rowcount != 1:
            logger.warning("checkpoint_gia_emesso", dimensione=n)
            return None
        await session.execute(text(
            # Solo i checkpoint ancora IN_ATTESA: un RIFIUTATO resta tale (allarme
            # persistente, mai «superato» in silenzio)
            "UPDATE public.catena_checkpoint SET stato_testimone = 'SUPERATO' "
            "WHERE dimensione < :n AND stato_testimone = 'IN_ATTESA'"
        ), {"n": n})
        payload = {"dimensione": n, "radice": radice.hex(), "emesso_il": iso_utc(adesso),
                   "testo": firmata}
        await accoda_evento(session, topic=TOPIC_CHECKPOINT, chiave=str(n), payload=payload)
    metriche().checkpoint.set(n)
    logger.info("checkpoint_emesso", dimensione=n, radice=radice.hex()[:16],
                testimone=stato_testimone)
    return payload


async def sigilla_in_sospeso(servizio: ServizioFirmaBlocchi, *, adesso: datetime,
                             limite: int = 20) -> int:
    """Sigillo qualificato e marca dei checkpoint DA_FIRMARE; restituisce quanti hanno
    ora un esito definitivo (FIRMATO o NON_FIRMATO)."""
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT dimensione, testo FROM public.catena_checkpoint "
            "WHERE stato_sigillo = 'DA_FIRMARE' ORDER BY dimensione LIMIT :limite"
        ), {"limite": limite})).mappings().all()
    definitivi = 0
    for riga in righe:
        corpo, _ = nota.separa(str(riga["testo"]))
        esito = await servizio.firma(corpo.encode("utf-8"))
        if not esito.definitivo:
            logger.warning("sigillo_checkpoint_rinviato", dimensione=riga["dimensione"],
                           motivo=esito.motivo)
            continue
        chiave_sigillo = None
        if esito.stato == STATO_FIRMATO and esito.firma_b64:
            chiave_sigillo = archivio_worm.chiave_sigillo(int(riga["dimensione"]))
            await archivio_worm.scrivi(chiave_sigillo, base64.b64decode(esito.firma_b64),
                                       "application/pkcs7-signature", adesso)
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.catena_checkpoint SET stato_sigillo = :stato, "
                "motivo_sigillo = :motivo, sigillo_cades = :busta, data_marca = :marca, "
                "sigillo_worm_chiave = :chiave WHERE dimensione = :n "
                "AND stato_sigillo = 'DA_FIRMARE'"
            ), {"stato": esito.stato, "motivo": esito.motivo, "busta": esito.firma_b64,
                "marca": esito.tsa_timestamp, "chiave": chiave_sigillo,
                "n": riga["dimensione"]})
        definitivi += 1
        logger.info("sigillo_checkpoint_registrato", dimensione=riga["dimensione"],
                    stato=esito.stato, motivo=esito.motivo)
    return definitivi


async def _registra_esito_testimone(n: int, stato: str, motivo: str) -> None:
    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.catena_checkpoint SET stato_testimone = :stato, "
            "esito_testimone = :motivo, tentativi_testimone = tentativi_testimone + 1 "
            "WHERE dimensione = :n AND stato_testimone = 'IN_ATTESA'"
        ), {"stato": stato, "motivo": motivo[:500], "n": n})


# Un ALLARME aperto per causa (indice unico parziale, migrazione 20261023_033):
# il rifiuto si aggiunge all'allarme della stessa causa, o ne apre uno nuovo.
_ALLARME_RIFIUTO = text(
    "INSERT INTO public.catena_allarmi_testimone AS a (causa, aperto_il, ultimo_il, rifiuti, "
    "  prima_dimensione, ultima_dimensione, ultimo_motivo) "
    "VALUES (:causa, :adesso, :adesso, 1, :n, :n, :motivo) "
    "ON CONFLICT (causa) WHERE gestito_il IS NULL DO UPDATE SET rifiuti = a.rifiuti + 1, "
    "  ultimo_il = EXCLUDED.ultimo_il, ultima_dimensione = EXCLUDED.ultima_dimensione, "
    "  ultimo_motivo = EXCLUDED.ultimo_motivo "
    "RETURNING id, rifiuti"
)


async def _registra_rifiuto(n: int, causa: str, motivo: str, adesso: datetime) -> None:
    """Checkpoint RIFIUTATO e rifiuto aggiunto all'allarme aperto della stessa causa
    (N3: un'ora di 403 = UN allarme con 60 rifiuti, non 60 allarmi)."""
    async with get_async_session() as session:
        allarme = (await session.execute(_ALLARME_RIFIUTO, {
            "causa": causa[:60], "adesso": adesso, "n": n, "motivo": motivo[:500]})).one()
        await session.execute(text(
            "UPDATE public.catena_checkpoint SET stato_testimone = 'RIFIUTATO', "
            "esito_testimone = :motivo, allarme_id = :allarme, "
            "tentativi_testimone = tentativi_testimone + 1 "
            "WHERE dimensione = :n AND stato_testimone = 'IN_ATTESA'"
        ), {"motivo": motivo[:500], "allarme": int(allarme.id), "n": n})
    if int(allarme.rifiuti) == 1:
        logger.error("checkpoint_rifiutato_dal_testimone", dimensione=n, causa=causa,
                     motivo=motivo[:200], allarme=int(allarme.id))
    else:
        logger.warning("rifiuto_testimone_raggruppato", dimensione=n, causa=causa,
                       allarme=int(allarme.id), rifiuti=int(allarme.rifiuti))


async def invia_al_testimone(client: ClientTestimone | None, *, detentore: str,
                             adesso: datetime) -> str | None:
    """Invia l'ultimo checkpoint in attesa al testimone; restituisce l'esito (o None
    se non c'è nulla da inviare o il testimone non è configurato)."""
    if client is None:
        return None
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "SELECT dimensione, testo FROM public.catena_checkpoint "
            "WHERE stato_testimone = 'IN_ATTESA' ORDER BY dimensione DESC LIMIT 1"
        ))).mappings().first()
        stato = await archivio.leggi_stato(session)
    if riga is None:
        return None
    n, firmata = int(riga["dimensione"]), str(riga["testo"])
    vecchia = stato.testimone_dimensione or 0
    for _ in range(2):  # un secondo tentativo se il testimone dichiara un'altra dimensione
        if vecchia > n:
            motivo = f"il testimone conosce la dimensione {vecchia}, maggiore di {n}"
            logger.error("testimone_dimensione_maggiore_del_registro", registro=n,
                         testimone=vecchia)
            await _registra_rifiuto(n, CAUSA_DIMENSIONE, motivo, adesso)
            metriche().errori.labels("testimone").inc()
            return ESITO_RIFIUTATO
        async with get_async_session() as session:
            nodi = await archivio.leggi_nodi(session, albero.nodi_per_consistenza(vecchia, n))
        prova = albero.prova_consistenza_da_nodi(vecchia, n, nodi)
        esito = await client.invia(vecchia, prova, firmata)
        _registra_contatto(esito)
        if esito.esito != ESITO_DIMENSIONE_DIVERSA or esito.dimensione_testimone == vecchia:
            break
        vecchia = int(esito.dimensione_testimone or 0)
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.catena_stato SET testimone_dimensione = :v WHERE id = 1"
            ), {"v": vecchia})

    if esito.esito == ESITO_CONTROFIRMATO and esito.controfirma:
        completa = nota.aggiungi_firme(firmata, [esito.controfirma])
        await archivio_worm.scrivi(archivio_worm.chiave_controfirmato(n),
                                   completa.encode("utf-8"), "text/plain; charset=utf-8", adesso)
        async with get_async_session() as session:
            await archivio.stato_con_lease(session, detentore, adesso)
            await session.execute(text(
                "UPDATE public.catena_checkpoint SET stato_testimone = 'CONTROFIRMATO', "
                "controfirma = :firma, controfirmato_il = :adesso, esito_testimone = NULL, "
                "tentativi_testimone = tentativi_testimone + 1 "
                "WHERE dimensione = :n AND stato_testimone = 'IN_ATTESA'"
            ), {"firma": esito.controfirma, "adesso": adesso, "n": n})
            await session.execute(text(
                "UPDATE public.catena_stato SET testimone_dimensione = :n WHERE id = 1"
            ), {"n": n})
        logger.info("checkpoint_controfirmato", dimensione=n)
        return ESITO_CONTROFIRMATO
    if esito.esito == ESITO_RIFIUTATO:
        metriche().errori.labels("testimone").inc()
        await _registra_rifiuto(n, esito.causa or "ALTRO", esito.motivo, adesso)
        return ESITO_RIFIUTATO
    logger.warning("testimone_da_ritentare", dimensione=n, motivo=esito.motivo)
    await _registra_esito_testimone(n, "IN_ATTESA", esito.motivo)
    return esito.esito


async def controlla_controfirme(*, adesso: datetime, massimo_secondi: float) -> int | None:
    """Allarme se l'ultimo checkpoint in attesa di controfirma è più vecchio del
    limite: log ``checkpoint_senza_controfirma`` + metrica. Restituisce la
    dimensione in allarme (o None)."""
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "SELECT dimensione, emesso_il, stato_testimone FROM public.catena_checkpoint "
            "WHERE stato_testimone = 'IN_ATTESA' "
            "ORDER BY dimensione DESC LIMIT 1"
        ))).mappings().first()
    if riga is None:
        metriche().senza_controfirma.set(0)
        return None
    eta = (adesso - riga["emesso_il"]).total_seconds()
    metriche().senza_controfirma.set(max(eta, 0.0))
    if eta <= massimo_secondi:
        return None
    metriche().allarmi_controfirma.inc()
    logger.error("checkpoint_senza_controfirma", dimensione=riga["dimensione"],
                 secondi=int(eta), stato=riga["stato_testimone"])
    return int(riga["dimensione"])


def limite_controfirma(adesso: datetime, massimo_secondi: float) -> datetime:
    """Istante prima del quale un checkpoint senza controfirma è in allarme."""
    return adesso - timedelta(seconds=massimo_secondi)


def _registra_contatto(esito: Any) -> None:
    """Metrica «testimone raggiungibile»: ogni risposta HTTP del testimone (anche
    un rifiuto) è un contatto riuscito; rete o 5xx no (log una volta per caduta)."""
    giu = esito.esito == ESITO_DA_RITENTARE
    registra_raggiungibilita(not giu, esito.motivo if giu else "")


_raggiungibile: bool | None = None


def registra_raggiungibilita(raggiunto: bool, motivo: str = "") -> None:
    """Aggiorna la metrica; il passaggio a «non risponde» è un log d'errore
    (``testimone_non_risponde``), il ritorno un log informativo."""
    global _raggiungibile
    metriche().testimone_raggiungibile.set(1 if raggiunto else 0)
    if not raggiunto and _raggiungibile is not False:
        logger.error("testimone_non_risponde", motivo=motivo[:200])
    elif raggiunto and _raggiungibile is False:
        logger.info("testimone_di_nuovo_raggiungibile")
    _raggiungibile = raggiunto


async def allarmi_aperti(session: Any) -> list[dict[str, Any]]:
    """Allarmi del testimone aperti (uno per causa), con il numero di rifiuti."""
    righe = (await session.execute(text(
        "SELECT id, causa, aperto_il, ultimo_il, rifiuti, prima_dimensione, ultima_dimensione, "
        "ultimo_motivo FROM public.catena_allarmi_testimone WHERE gestito_il IS NULL ORDER BY id"
    ))).mappings().all()
    return [dict(r) for r in righe]


async def controlla_rifiuti() -> list[int]:
    """Allarme PERSISTENTE dei rifiuti del testimone non ancora gestiti: metriche
    ``verumdoc_catena_checkpoint_rifiutati_aperti`` (checkpoint) e
    ``verumdoc_catena_allarmi_testimone_aperti`` (allarmi, uno per causa), log
    ``checkpoint_rifiutato_aperto`` a ogni giro con gli allarmi e i loro
    contatori. Restituisce le dimensioni dei checkpoint rifiutati aperti."""
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT dimensione FROM public.catena_checkpoint "
            "WHERE stato_testimone = 'RIFIUTATO' AND rifiuto_gestito_il IS NULL "
            "ORDER BY dimensione"
        ))).all()
        allarmi = await allarmi_aperti(session)
    metriche().rifiutati_aperti.set(len(righe))
    metriche().allarmi_testimone.set(len(allarmi))
    if allarmi:
        logger.error("checkpoint_rifiutato_aperto",
                     allarmi=[{"id": a["id"], "causa": a["causa"], "rifiuti": a["rifiuti"],
                               "dimensioni": [a["prima_dimensione"], a["ultima_dimensione"]]}
                              for a in allarmi][:10],
                     azione="completamento sospeso: python -m app.registro.operatore "
                            "gestisci-rifiuti")
    return [int(r[0]) for r in righe]


#: Cause che indicano una BIFORCAZIONE o un registro indietro (terzo giro, T3): non
#: si gestiscono mai in blocco, solo una per una con ``--allarme ID``.
CAUSE_GRAVI = ("HTTP_423", CAUSA_DIMENSIONE)


class GestioneNonAmmessa(ValueError):
    """La gestione chiesta chiuderebbe allarmi senza la conferma esplicita richiesta."""


async def registra_gestione_allarmi(session: Any, *, operatore: str, motivo: str,
                                    adesso: datetime, allarme: int | None = None,
                                    cause: Sequence[str] | None = None
                                    ) -> dict[str, Any] | None:
    """Un operatore registra in UNA operazione la gestione di un incidente: un
    allarme (``allarme``, di qualunque causa) oppure, in blocco, gli allarmi
    aperti delle ``cause`` CONFERMATE una per una (T3: mai «tutto quello che c'è»).
    Le cause gravi (``CAUSE_GRAVI``: biforcazione, testimone più avanti) non si
    gestiscono in blocco. I checkpoint restano RIFIUTATI e inutilizzabili per
    sempre. Una sola voce in ``audit_global``, con gli allarmi rimasti aperti.
    None se non c'è nulla da gestire."""
    from libs.shared.security.audit import registra_audit

    if allarme is None:
        confermate = sorted({c.strip() for c in (cause or []) if c.strip()})
        if not confermate:
            raise GestioneNonAmmessa("gestione in blocco: confermare ogni causa")
        gravi = [c for c in confermate if c in CAUSE_GRAVI]
        if gravi:
            raise GestioneNonAmmessa(
                f"le cause {', '.join(gravi)} (biforcazione o testimone più avanti) si "
                "gestiscono solo una per una con --allarme ID")
        chiusi = (await session.execute(text(
            "UPDATE public.catena_allarmi_testimone SET gestito_il = :adesso, "
            "gestito_da = :operatore, nota = :motivo WHERE gestito_il IS NULL "
            "AND causa = ANY(CAST(:cause AS varchar[])) "
            "RETURNING id, causa, rifiuti, prima_dimensione, ultima_dimensione"
        ), {"adesso": adesso, "operatore": operatore[:200], "motivo": motivo,
            "cause": confermate})).mappings().all()
    else:
        chiusi = (await session.execute(text(
            "UPDATE public.catena_allarmi_testimone SET gestito_il = :adesso, "
            "gestito_da = :operatore, nota = :motivo WHERE gestito_il IS NULL AND id = :allarme "
            "RETURNING id, causa, rifiuti, prima_dimensione, ultima_dimensione"
        ), {"adesso": adesso, "operatore": operatore[:200], "motivo": motivo,
            "allarme": allarme})).mappings().all()
    if not chiusi:
        return None
    ids = [int(c["id"]) for c in chiusi]
    rifiuti = (await session.execute(text(
        "UPDATE public.catena_checkpoint SET rifiuto_gestito_il = :adesso, "
        "rifiuto_gestito_da = :operatore, rifiuto_nota = :motivo "
        "WHERE stato_testimone = 'RIFIUTATO' AND rifiuto_gestito_il IS NULL "
        "AND allarme_id = ANY(CAST(:ids AS bigint[])) RETURNING dimensione"
    ), {"adesso": adesso, "operatore": operatore[:200], "motivo": motivo,
        "ids": ids})).all()
    dimensioni = sorted(int(r[0]) for r in rifiuti)
    dettagli = {"allarmi": [{"id": int(c["id"]), "causa": c["causa"], "rifiuti": c["rifiuti"]}
                            for c in chiusi],
                "checkpoint": len(dimensioni),
                "dimensioni": [dimensioni[0], dimensioni[-1]] if dimensioni else [],
                "rimasti_aperti": [{"id": a["id"], "causa": a["causa"]}
                                   for a in await allarmi_aperti(session)],
                "operatore": operatore[:200], "motivo": motivo[:2000]}
    await registra_audit(session, evento="catena.rifiuti_testimone_gestiti", dettagli=dettagli)
    logger.warning("rifiuti_testimone_gestiti", allarmi=ids, checkpoint=len(dimensioni),
                   operatore=operatore[:200])
    return dettagli


async def registra_gestione_rifiuto(session: Any, *, dimensione: int, operatore: str,
                                    motivo: str, adesso: datetime) -> bool:
    """Gestione a partire da un checkpoint: si chiude l'INTERO allarme (incidente)
    a cui appartiene il rifiuto del checkpoint ``dimensione``. False se il
    checkpoint non è un rifiuto aperto."""
    riga = (await session.execute(text(
        "SELECT c.allarme_id, a.causa FROM public.catena_checkpoint c "
        "JOIN public.catena_allarmi_testimone a ON a.id = c.allarme_id "
        "WHERE c.dimensione = :n AND c.stato_testimone = 'RIFIUTATO' "
        "AND c.rifiuto_gestito_il IS NULL"
    ), {"n": dimensione})).first()
    if riga is None:
        return False
    allarme = riga.allarme_id
    if riga.causa in CAUSE_GRAVI:
        raise GestioneNonAmmessa(f"allarme {allarme} di causa {riga.causa}: si gestisce solo "
                                 "con --allarme ID")
    return await registra_gestione_allarmi(session, operatore=operatore, motivo=motivo,
                                           adesso=adesso, allarme=int(allarme)) is not None
