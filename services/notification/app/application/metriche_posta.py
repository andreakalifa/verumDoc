# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/metriche_posta.py — metriche (e quindi allarmi) del lavoratore della posta.

R14 (VD-C-170). Il lavoratore della posta (``notification-posta``, R12b) non ha HTTP: è
l'unico processo con le chiavi della posta e i segreti PEC e non deve esporre porte. Le
metriche si calcolano quindi dal DATABASE nelle repliche API di notification (che hanno
già ``/metrics``), come per il ciclo di vita (``ciclo-vita/app/application/metriche.py``):
ciò che il lavoratore fa o non riesce a fare resta scritto nelle tabelle che legge anche la
console.

  verumdoc_posta_messaggi_in_coda              messaggi di posta IN_CODA (al più 100.000)
  verumdoc_posta_coda_eta_secondi              età del messaggio IN_CODA più vecchio (0 se
                                               nessuno): allarme PostaCodaFerma
  verumdoc_posta_messaggi_non_partiti_24h      messaggi NON_PARTITA nelle ultime 24 ore
                                               (DLQ): allarme PostaNonPartita
  verumdoc_pec_messaggi_senza_accettazione     PEC INVIATO da oltre un'ora senza ricevuta di
                                               accettazione: allarme PecSenzaRicevuta
  verumdoc_pec_messaggi_senza_consegna         PEC ACCETTATO da oltre PEC_CONSEGNA_ATTESA_ORE
                                               senza ricevuta di consegna: PecSenzaRicevuta
  verumdoc_pec_messaggi_non_partiti_24h        PEC NON_PARTITO nelle ultime 24 ore
  verumdoc_pec_canali_attivi                   canali PEC attivi in console
  verumdoc_pec_canali_non_utilizzabili         canali attivi con problemi dichiarati dal
                                               lavoratore (segreti assenti, host non ammessi,
                                               errore di connessione): PecCanaleNonUtilizzabile
  verumdoc_pec_stato_eta_secondi               secondi dall'ultimo aggiornamento dello stato
                                               dei canali attivi scritto dal lavoratore (-1
                                               se nessun canale attivo): PecLavoratoreFermo
  verumdoc_pec_ricevute_non_autentiche_24h     ricevute NON_AUTENTICA nelle ultime 24 ore
                                               (firma non valida o fuori dalle radici):
                                               PecRicevuteNonAutentiche

Regole: ``infra/observability/regole/verumdoc-r14.yml``; runbook ``docs/integrazione/pec.md``
§5. Aggiornamento ogni ``METRICHE_POSTA_INTERVALLO_SECONDI`` nell'ambito del conservatore.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Gauge
from sqlalchemy import text

from libs.shared.db.ambito import ambito_conservatore

logger = structlog.get_logger(__name__)

MOTIVO_AMBITO = "metriche del lavoratore della posta: conteggi su tutti gli enti"
LIMITE_CONTEGGIO = 100_000

#: nome della metrica → descrizione (una sola tabella: registrazione e aggiornamento)
METRICHE: dict[str, str] = {
    "verumdoc_posta_messaggi_in_coda": "Messaggi di posta in coda (al più 100.000)",
    "verumdoc_posta_coda_eta_secondi": "Età in secondi del messaggio di posta in coda più vecchio",
    "verumdoc_posta_messaggi_non_partiti_24h": "Messaggi di posta NON_PARTITA nelle ultime 24 ore",
    "verumdoc_pec_messaggi_senza_accettazione":
        "PEC inviate da oltre un'ora senza ricevuta di accettazione",
    "verumdoc_pec_messaggi_senza_consegna":
        "PEC accettate da oltre PEC_CONSEGNA_ATTESA_ORE senza ricevuta di consegna",
    "verumdoc_pec_messaggi_non_partiti_24h": "PEC NON_PARTITO nelle ultime 24 ore",
    "verumdoc_pec_canali_attivi": "Canali PEC attivi in console",
    "verumdoc_pec_canali_non_utilizzabili":
        "Canali PEC attivi con problemi dichiarati dal lavoratore della posta",
    "verumdoc_pec_stato_eta_secondi":
        "Secondi dall'ultimo stato dei canali PEC attivi scritto dal lavoratore (-1: nessuno)",
    "verumdoc_pec_ricevute_non_autentiche_24h": "Ricevute PEC non autentiche nelle ultime 24 ore",
}


@dataclass
class MetrichePosta:
    misure: dict[str, Gauge]


_METRICHE: MetrichePosta | None = None


def registra(registro: CollectorRegistry) -> MetrichePosta:
    """Registra le metriche nel registro del /metrics del servizio."""
    global _METRICHE
    _METRICHE = MetrichePosta({nome: Gauge(nome, descr, registry=registro)
                               for nome, descr in METRICHE.items()})
    return _METRICHE


def metriche() -> MetrichePosta | None:
    return _METRICHE


async def _attesa_consegna_ore() -> int:
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import PEC_CONSEGNA_ATTESA_ORE

    return int((await parametri([PEC_CONSEGNA_ATTESA_ORE], None))[PEC_CONSEGNA_ATTESA_ORE])


async def leggi() -> dict[str, float]:
    """I valori di tutte le metriche, dal database (funzione unica: test e ciclo)."""
    from libs.shared.db import get_async_session_lettura

    attesa = await _attesa_consegna_ore()
    with ambito_conservatore(MOTIVO_AMBITO, registra=False):
        async with get_async_session_lettura() as session:
            posta = (await session.execute(text(
                "SELECT (SELECT count(*) FROM (SELECT 1 FROM public.notifications "
                "        WHERE stato = 'IN_CODA' LIMIT :lim) x) AS in_coda, "
                " COALESCE((SELECT EXTRACT(EPOCH FROM now() - min(created_at)) "
                "  FROM public.notifications WHERE stato = 'IN_CODA'), 0) AS eta, "
                " (SELECT count(*) FROM (SELECT 1 FROM public.notifications "
                "        WHERE stato = 'NON_PARTITA' AND updated_at > now() - interval '1 day' "
                "        LIMIT :lim) x) AS non_partiti"), {"lim": LIMITE_CONTEGGIO})
            ).mappings().one()
            pec = (await session.execute(text(
                "SELECT (SELECT count(*) FROM (SELECT 1 FROM public.pec_messaggi WHERE stato = "
                "  'INVIATO' AND inviato_il < now() - interval '1 hour' LIMIT :lim) x) AS sa, "
                " (SELECT count(*) FROM (SELECT 1 FROM public.pec_messaggi WHERE stato = "
                "  'ACCETTATO' AND accettato_il < now() - make_interval(hours => :ore) "
                "  LIMIT :lim) x) AS sc, "
                " (SELECT count(*) FROM (SELECT 1 FROM public.pec_messaggi WHERE stato = "
                "  'NON_PARTITO' AND updated_at > now() - interval '1 day' LIMIT :lim) x) AS np, "
                " (SELECT count(*) FROM public.canali_pec WHERE attivo) AS attivi, "
                " (SELECT count(*) FROM public.canali_pec c JOIN public.pec_canali_stato s "
                "   ON s.canale_codice = c.codice WHERE c.attivo "
                "   AND (jsonb_array_length(s.problemi) > 0 OR s.ultimo_errore IS NOT NULL)) "
                "   AS non_utilizzabili, "
                " (SELECT EXTRACT(EPOCH FROM now() - min(COALESCE(s.aggiornato_il, c.updated_at)))"
                "   FROM public.canali_pec c LEFT JOIN public.pec_canali_stato s "
                "   ON s.canale_codice = c.codice WHERE c.attivo) AS eta_stato, "
                " (SELECT count(*) FROM (SELECT 1 FROM public.pec_ricevute WHERE esito = "
                "  'NON_AUTENTICA' AND ricevuta_il > now() - interval '1 day' LIMIT :lim) x) "
                "  AS non_autentiche"), {"lim": LIMITE_CONTEGGIO, "ore": attesa})
            ).mappings().one()
    eta_stato = pec["eta_stato"]
    return {
        "verumdoc_posta_messaggi_in_coda": float(posta["in_coda"]),
        "verumdoc_posta_coda_eta_secondi": float(posta["eta"] or 0),
        "verumdoc_posta_messaggi_non_partiti_24h": float(posta["non_partiti"]),
        "verumdoc_pec_messaggi_senza_accettazione": float(pec["sa"]),
        "verumdoc_pec_messaggi_senza_consegna": float(pec["sc"]),
        "verumdoc_pec_messaggi_non_partiti_24h": float(pec["np"]),
        "verumdoc_pec_canali_attivi": float(pec["attivi"]),
        "verumdoc_pec_canali_non_utilizzabili": float(pec["non_utilizzabili"]),
        "verumdoc_pec_stato_eta_secondi": -1.0 if eta_stato is None else float(eta_stato),
        "verumdoc_pec_ricevute_non_autentiche_24h": float(pec["non_autentiche"]),
    }


async def aggiorna() -> dict[str, float]:
    valori = await leggi()
    m = metriche()
    if m is not None:
        for nome, valore in valori.items():
            m.misure[nome].set(valore)
    if valori["verumdoc_pec_canali_non_utilizzabili"]:
        logger.error("pec_canali_non_utilizzabili",
                     canali=int(valori["verumdoc_pec_canali_non_utilizzabili"]))
    if valori["verumdoc_pec_ricevute_non_autentiche_24h"]:
        logger.error("pec_ricevute_non_autentiche",
                     ricevute=int(valori["verumdoc_pec_ricevute_non_autentiche_24h"]))
    return valori


async def ciclo(fine: asyncio.Event, intervallo: float) -> None:
    """Aggiornamento periodico (repliche API di notification)."""
    while not fine.is_set():
        try:
            await aggiorna()
        except Exception as exc:  # noqa: BLE001 — database momentaneamente giù: si riprova
            logger.warning("metriche_posta_non_aggiornate", errore=type(exc).__name__)
        try:
            await asyncio.wait_for(fine.wait(), timeout=intervallo)
        except TimeoutError:
            pass


__all__: list[Any] = ["METRICHE", "MetrichePosta", "aggiorna", "ciclo", "leggi", "metriche",
                      "registra"]
