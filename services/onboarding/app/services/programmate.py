# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/app/services/programmate.py — lavoro periodico della
configurazione (R07).

Riferimenti: decisioni D28 (modifiche programmate) e D17 (avviso ai clienti
senza Idempotency-Key); REGOLE_SVILUPPO §13 (plan/apply), §12.3 (outbox),
§12.7 (lock nello stesso ordine), §11.9 (scadenze controllate).

Un giro (``giro``), ogni ``CONFIGURAZIONE_PROGRAMMATE_INTERVALLO_SECONDI``:

1. ANTEPRIMA DEL GIORNO PRIMA (D28): per ogni bozza PROGRAMMATA che decorre
   entro domani e non ha ancora l'anteprima ricalcolata, piano e impatto si
   ricalcolano con lo STESSO codice dell'anteprima della console
   (``calcola_piano`` + ``_impatto``), si salvano nella bozza
   (``anteprima_programmata``, ``anteprima_il``) e l'autore riceve un'e-mail
   (``notification.send`` nell'outbox, stessa transazione; ``avviso_autore_il``).
2. APPLICAZIONE ALLA DECORRENZA (D28): ogni bozza PROGRAMMATA con
   ``valido_dal`` ≤ oggi (giorno italiano) si applica in UNA transazione con lo
   stesso ordine di lock dell'applicazione immediata (bozza → ambito) e con i
   permessi ATTUALI dell'autore (un autore disattivato o con un altro ruolo non
   applica più nulla). Piano con elementi bloccati: la bozza torna APERTA con il
   motivo in ``esito`` e l'autore è avvisato; nulla è scritto.
3. AVVISO IDEMPOTENZA (D17): una volta per settimana ISO e per ente, se negli
   ultimi 7 giorni ci sono versamenti senza Idempotency-Key e la transizione
   dell'ente non è finita, evento ``idempotenza.chiave_omessa`` (+ e-mail al
   referente, ``libs.shared.avvisi``).

Ogni passo lavora per bozza o per ente in transazioni brevi (FOR UPDATE SKIP
LOCKED, vincolo unico degli avvisi): più repliche possono girare insieme.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_conservatore
from libs.verumdoc_rules.configurazione import Attore
from libs.verumdoc_rules.impostazioni import giorno_locale

logger = structlog.get_logger(__name__)

TOPIC_MESSAGGI = "notification.send"


@dataclass
class EsitoGiro:
    anteprime: list[str] = field(default_factory=list)
    applicate: list[str] = field(default_factory=list)
    bloccate: list[str] = field(default_factory=list)
    avvisi_idempotenza: list[str] = field(default_factory=list)


async def _autore(session: Any, user_id: str | None) -> tuple[Attore, dict[str, Any] | None]:
    """L'autore con il ruolo e l'ente ATTUALI (nessun ruolo se disattivato o assente)."""
    if not user_id:
        return Attore(ruolo="", tenant_id=None, user_id=None), None
    riga = (await session.execute(text(
        "SELECT ruolo::text AS ruolo, tenant_id::text AS tenant_id, email, attivo "
        "FROM public.users WHERE id = CAST(:u AS uuid)"), {"u": user_id})).mappings().first()
    if riga is None or not riga["attivo"]:
        return Attore(ruolo="", tenant_id=None, user_id=user_id), None
    return Attore(ruolo=riga["ruolo"], tenant_id=riga["tenant_id"], user_id=user_id), dict(riga)


async def _avvisa_autore(session: Any, autore: dict[str, Any] | None, *, bozza_id: str,
                         fase: str, oggetto: str, testo: str) -> bool:
    """E-mail all'autore (messaggio esplicito nell'outbox, stessa transazione).

    Revisione W4: vale per ogni autore, anche SUPER_ADMIN e RSC_CONSERVATORE (il
    loro utente ha l'ente del conservatore, ``users.tenant_id`` obbligatorio, a
    cui la notifica si intesta). Autore disattivato o senza indirizzo: errore
    nel log e metrica ``verumdoc_programmate_avvisi_non_inviati_total``."""
    from libs.shared.db.metriche import incrementa
    from libs.shared.outbox import accoda_evento

    tenant = (autore or {}).get("tenant_id")
    if autore is None or not autore.get("email") or not tenant:
        logger.error("programmata_autore_non_avvisabile", bozza_id=bozza_id, fase=fase,
                     ruolo=(autore or {}).get("ruolo"))
        incrementa("verumdoc_programmate_avvisi_non_inviati", fase=fase)
        return False
    await accoda_evento(session, topic=TOPIC_MESSAGGI, chiave=f"{bozza_id}:{fase}",
                        tenant_id=tenant, payload={
                            "evento": "configurazione.programmata", "tipo": "email",
                            "tenant_id": tenant, "destinatario": autore["email"],
                            "oggetto": oggetto, "testo": testo,
                            "oggetto_id": f"configurazione:{bozza_id}:{fase}"})
    return True


def _riassunto(piano: dict[str, Any], impatto: dict[str, Any] | None) -> str:
    righe = [f"- {m['codice']}: {json.dumps(m['prima'])} → {json.dumps(m['dopo'])}"
             for m in piano.get("modifiche", [])]
    if piano.get("bloccati"):
        righe += [f"- BLOCCATO {b['codice']}: {b['motivo']}" for b in piano["bloccati"]]
    if impatto and impatto.get("campione"):
        righe.append(f"Impatto sul campione: "
                     f"{impatto['pdv_ultimi_30_giorni_che_sarebbero_rifiutati']} versamenti "
                     f"recenti sarebbero rifiutati, "
                     f"{impatto['pdv_ultimi_30_giorni_con_nuovi_avvisi']} con nuovi avvisi.")
    return "\n".join(righe)


# ── 1. Anteprima del giorno prima ───────────────────────────────────

async def anteprime_del_giorno_prima(adesso: datetime, *, lotto: int = 50) -> list[str]:
    from app.services.configurazione import _COLONNE_BOZZA, _impatto, calcola_piano
    from libs.shared.errori import ErroreApi

    domani = giorno_locale(adesso) + timedelta(days=1)
    fatte: list[str] = []
    with ambito_conservatore("configurazione: anteprima delle modifiche programmate"):
        async with get_async_session() as session:
            ids = (await session.execute(text(
                "SELECT id::text FROM public.configurazione_bozze WHERE stato = 'PROGRAMMATA' "
                "AND valido_dal <= :d AND anteprima_il IS NULL ORDER BY valido_dal, id "
                "LIMIT :n"), {"d": domani, "n": lotto})).scalars().all()
        for bozza_id in ids:
            async with get_async_session() as session:
                riga = (await session.execute(text(
                    _COLONNE_BOZZA + "WHERE id = CAST(:b AS uuid) AND stato = 'PROGRAMMATA' "
                    "AND anteprima_il IS NULL FOR UPDATE SKIP LOCKED"),
                    {"b": bozza_id})).mappings().first()
                if riga is None:
                    continue
                attore, autore = await _autore(session, riga["programmata_da"])
                try:
                    calcolato = await calcola_piano(session, attore, riga, adesso)
                    piano = calcolato.come_dict()
                    impatto = await _impatto(session, calcolato, adesso)
                except ErroreApi as exc:  # permessi cambiati, ente o classe spariti
                    piano = {"modifiche": [], "bloccati": [
                        {"codice": "ambito", "motivo": exc.dettaglio or exc.titolo}],
                        "avvisi": [], "impronta_piano": None}
                    impatto = None
                anteprima = {**piano, "impatto": impatto, "calcolata_il": adesso.isoformat()}
                avvisato = await _avvisa_autore(
                    session, autore, bozza_id=bozza_id, fase="anteprima",
                    oggetto=f"[VerumDoc] Modifica di configurazione dal "
                            f"{riga['valido_dal'].isoformat()}: anteprima aggiornata",
                    testo=(f"La modifica programmata ({riga['risorsa']}, livello "
                           f"{riga['livello']}) si applicherà il "
                           f"{riga['valido_dal'].isoformat()}. Anteprima ricalcolata:\n"
                           + _riassunto(piano, impatto)
                           + "\nSe non va più bene, annullala dalla console prima di quella "
                             "data."))
                await session.execute(text(
                    "UPDATE public.configurazione_bozze SET anteprima_programmata = "
                    "CAST(:a AS jsonb), anteprima_il = :ora, avviso_autore_il = "
                    "CASE WHEN :avv THEN :ora ELSE avviso_autore_il END "
                    "WHERE id = CAST(:b AS uuid)"),
                    {"a": json.dumps(anteprima, default=str), "ora": adesso, "avv": avvisato,
                     "b": bozza_id})
                fatte.append(bozza_id)
    return fatte


# ── 2. Applicazione alla decorrenza ─────────────────────────────────

async def applica_programmate(adesso: datetime, *, lotto: int = 50
                              ) -> tuple[list[str], list[str]]:
    from app.services.configurazione import (
        _COLONNE_BOZZA,
        _esegui_piano,
        _scrivi_audit,
        calcola_piano,
    )
    from libs.shared.errori import ErroreApi

    oggi = giorno_locale(adesso)
    applicate: list[str] = []
    bloccate: list[str] = []
    with ambito_conservatore("configurazione: applicazione delle modifiche programmate"):
        async with get_async_session() as session:
            ids = (await session.execute(text(
                "SELECT id::text FROM public.configurazione_bozze WHERE stato = 'PROGRAMMATA' "
                "AND valido_dal <= :g ORDER BY valido_dal, id LIMIT :n"),
                {"g": oggi, "n": lotto})).scalars().all()
        for bozza_id in ids:
            async with get_async_session() as session:
                riga = (await session.execute(text(
                    _COLONNE_BOZZA + "WHERE id = CAST(:b AS uuid) AND stato = 'PROGRAMMATA' "
                    "FOR UPDATE SKIP LOCKED"), {"b": bozza_id})).mappings().first()
                if riga is None:
                    continue
                # lock dell'ambito (stesso ordine dell'applicazione immediata: bozza → ambito)
                await session.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": "configurazione:" + "|".join(
                        str(riga[c] or "") for c in ("livello", "tenant_id", "classe_id",
                                                     "accordo_codice"))})
                attore, autore = await _autore(session, riga["programmata_da"])
                try:
                    calcolato = await calcola_piano(session, attore, riga, adesso)
                    bloccati = calcolato.piano.bloccati
                except ErroreApi as exc:
                    calcolato = None
                    bloccati = [{"codice": "ambito", "motivo": exc.dettaglio or exc.titolo}]
                if bloccati or calcolato is None:
                    esito = {"programmazione": {"esito": "BLOCCATA", "valido_dal":
                                                riga["valido_dal"].isoformat(),
                                                "bloccati": bloccati}}
                    await session.execute(text(
                        "UPDATE public.configurazione_bozze SET stato = 'APERTA', "
                        "esito = CAST(:e AS jsonb), versione = versione + 1, updated_at = now() "
                        "WHERE id = CAST(:b AS uuid)"),
                        {"e": json.dumps(esito, default=str), "b": bozza_id})
                    await _scrivi_audit(session, "configurazione.bozza.programmata_bloccata",
                                        attore, riga["tenant_id"],
                                        f"Modifica programmata {bozza_id} non applicata: "
                                        "elementi bloccati", bozza_id=bozza_id,
                                        bloccati=bloccati)
                    await _avvisa_autore(
                        session, autore, bozza_id=bozza_id, fase="bloccata",
                        oggetto="[VerumDoc] Modifica di configurazione programmata NON applicata",
                        testo=("La modifica programmata per il "
                               f"{riga['valido_dal'].isoformat()} non è stata applicata:\n"
                               + "\n".join(f"- {b['codice']}: {b['motivo']}" for b in bloccati)
                               + "\nLa bozza è tornata aperta nella console."))
                    bloccate.append(bozza_id)
                    continue
                esito = await _esegui_piano(session, calcolato, attore, riga, riga["valido_dal"])
                await session.execute(text(
                    "UPDATE public.configurazione_bozze SET stato = 'APPLICATA', "
                    "versione = versione + 1, applicata_il = now(), "
                    "applicata_da = programmata_da, updated_at = now(), impronta_piano = :i, "
                    "esito = CAST(:e AS jsonb) WHERE id = CAST(:b AS uuid)"),
                    {"i": calcolato.impronta, "e": json.dumps(esito, default=str),
                     "b": bozza_id})
                await _scrivi_audit(
                    session, "configurazione.bozza.applicata", attore, riga["tenant_id"],
                    f"Configurazione {riga['risorsa']} programmata applicata al livello "
                    f"{riga['livello']}: {len(calcolato.piano.modifiche)} modifiche",
                    bozza_id=bozza_id, programmata=True, valido_dal=riga["valido_dal"],
                    impronta_piano=calcolato.impronta,
                    impronta_programmazione=riga["impronta_piano"], esito=esito,
                    modifiche=[r.come_dict() for r in calcolato.piano.modifiche],
                    motivo=riga["motivo"])
                applicate.append(bozza_id)
    return applicate, bloccate


# ── 3. Avviso ai clienti senza Idempotency-Key (D17) ────────────────

def settimana_iso(giorno: date) -> str:
    anno, settimana, _ = giorno.isocalendar()
    return f"{anno}-W{settimana:02d}"


async def avvisa_idempotenza(adesso: datetime) -> list[str]:
    from app.services.configurazione import valori_conservatore_da_ambiente
    from libs.shared.avvisi import TIPO_IDEMPOTENZA_MANCANTE, registra_avviso
    from libs.shared.parametri import leggi_livelli
    from libs.verumdoc_rules.catalogo_v4 import REGISTRO
    from libs.verumdoc_rules.impostazioni import (
        ContestoRisoluzione,
        FornitoreDaDizionari,
        Livello,
        Risolutore,
    )

    codici = ["IDEMPOTENZA_OBBLIGATORIA", "IDEMPOTENZA_TRANSIZIONE_FINO_AL"]
    oggi = giorno_locale(adesso)
    settimana = settimana_iso(oggi)
    dal = adesso - timedelta(days=7)
    avvisati: list[str] = []
    with ambito_conservatore("configurazione: avviso dei versamenti senza Idempotency-Key"):
        async with get_async_session() as session:
            righe = (await session.execute(text(
                "SELECT tenant_id::text AS tenant_id, count(*) AS n, min(created_at) AS dal, "
                "max(created_at) AS al FROM public.versamento_impronte "
                "WHERE chiave_idempotenza IS NULL AND created_at >= :dal GROUP BY tenant_id "
                "ORDER BY tenant_id"), {"dal": dal})).mappings().all()
        for r in righe:
            async with get_async_session() as session:
                livelli = await leggi_livelli(codici, r["tenant_id"], session=session)
                conservatore = {**valori_conservatore_da_ambiente(),
                                **livelli.get(Livello.CONSERVATORE, {})}
                risolutore = Risolutore(REGISTRO, FornitoreDaDizionari({
                    Livello.CONSERVATORE: conservatore, Livello.ENTE: livelli[Livello.ENTE]}))
                ctx = ContestoRisoluzione(adesso=adesso, tenant_id=r["tenant_id"])
                if not risolutore.effettiva(ctx, "IDEMPOTENZA_OBBLIGATORIA").valore:
                    continue
                fino_al = risolutore.effettiva(ctx, "IDEMPOTENZA_TRANSIZIONE_FINO_AL").valore
                if oggi > fino_al:
                    continue  # transizione finita: le richieste sono già rifiutate (428)
                if await registra_avviso(session, tipo=TIPO_IDEMPOTENZA_MANCANTE,
                                         tenant_id=r["tenant_id"], chiave=settimana, dati={
                                             "versamenti": int(r["n"]),
                                             "dal": r["dal"].isoformat(),
                                             "al": r["al"].isoformat(),
                                             "fino_al": fino_al.isoformat(),
                                             "settimana": settimana}):
                    avvisati.append(r["tenant_id"])
    return avvisati


# ── Giro e lavoratore ───────────────────────────────────────────────

async def giro(adesso: datetime) -> EsitoGiro:
    esito = EsitoGiro()
    esito.anteprime = await anteprime_del_giorno_prima(adesso)
    esito.applicate, esito.bloccate = await applica_programmate(adesso)
    esito.avvisi_idempotenza = await avvisa_idempotenza(adesso)
    if esito.anteprime or esito.applicate or esito.bloccate or esito.avvisi_idempotenza:
        logger.info("configurazione_giro", anteprime=len(esito.anteprime),
                    applicate=len(esito.applicate), bloccate=len(esito.bloccate),
                    avvisi_idempotenza=len(esito.avvisi_idempotenza))
    return esito


#: R12a (VD-C-152, revisione): collegamenti d'invito rimasti in chiaro dopo il deploy
#: (eventi scritti dai pod con il codice precedente, notifiche inviate da pod vecchi):
#: tolti dagli eventi GIÀ pubblicati e dalle notifiche GIÀ inviate a ogni giro (gli
#: eventi non ancora pubblicati partono come prima: nessuna regressione).
_REGEX_INVITO = "#invito=[A-Za-z0-9._~-]+"
_SEGNAPOSTO = "#invito=[rimosso dopo l''invio]"
MINIMIZZA_INVITI = (
    f"UPDATE public.outbox_eventi SET payload = jsonb_set(payload, '{{testo}}', "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
    f"to_jsonb(regexp_replace(payload->>'testo', '{_REGEX_INVITO}', '{_SEGNAPOSTO}', 'g'))) "
    "WHERE topic = 'notification.send' AND pubblicato_il IS NOT NULL "
    "AND payload->>'evento' = 'accesso.invito' AND payload->>'testo' ~ '#invito=[A-Za-z0-9]'",
    f"UPDATE public.notifications SET payload_json = jsonb_set(payload_json, '{{testo}}', "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
    f"to_jsonb(regexp_replace(payload_json->>'testo', '{_REGEX_INVITO}', '{_SEGNAPOSTO}', "
    "'g'))) WHERE tipo = 'accesso.invito' AND stato = 'INVIATA' "
    "AND payload_json->>'testo' ~ '#invito=[A-Za-z0-9]'",
)


async def minimizza_inviti_in_chiaro() -> int:
    """Righe ripulite (vedi ``MINIMIZZA_INVITI``); idempotente."""
    from libs.shared.db import get_async_session
    from libs.shared.db.ambito import ambito_conservatore

    totale = 0
    with ambito_conservatore("minimizzazione dei collegamenti d'invito inviati", registra=False):
        async with get_async_session() as session:
            for istruzione in MINIMIZZA_INVITI:
                totale += int((await session.execute(text(istruzione))).rowcount or 0)
    if totale:
        logger.info("inviti_in_chiaro_minimizzati", righe=totale)
    return totale


async def lavoratore(fine: asyncio.Event, intervallo: float) -> None:
    """Ciclo del lavoro periodico (non muore per un errore: lo registra e riprova)."""
    from datetime import UTC

    while not fine.is_set():
        try:
            await giro(datetime.now(UTC))
            await minimizza_inviti_in_chiaro()
        except (SQLAlchemyError, OSError) as exc:
            logger.error("configurazione_giro_errore", errore=type(exc).__name__,
                         messaggio=str(exc)[:300])
        except Exception as exc:  # noqa: BLE001
            logger.exception("configurazione_giro_errore_inatteso", errore=type(exc).__name__)
        try:
            await asyncio.wait_for(fine.wait(), timeout=intervallo)
        except TimeoutError:
            pass


__all__ = ["EsitoGiro", "anteprime_del_giorno_prima", "applica_programmate",
           "avvisa_idempotenza", "giro", "lavoratore", "minimizza_inviti_in_chiaro",
           "settimana_iso"]
