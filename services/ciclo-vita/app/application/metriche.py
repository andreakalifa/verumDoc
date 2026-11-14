# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/metriche.py — metriche (e quindi allarmi) del ciclo di vita.

Revisione 2 di R05 (S3): uno scarto che non riesce a completare la
cancellazione di un PdA non resta silenzioso.

  verumdoc_ciclo_vita_pda_eliminazione_incompleta   PdA in ELIMINAZIONE_INCOMPLETA
      (cancellazione iniziata e non completata: legal hold arrivato durante lo
      scarto, retention, errore persistente). Allarme ScartoEliminazioneIncompleta
      (infra/observability/regole/verumdoc-r05.yml), runbook
      docs/runbooks/scarto_incompleto.md.
  verumdoc_ciclo_vita_pratiche_lavoro_in_errore     pratiche con lavoro del
      lavoratore e almeno LAVORATORE_SOGLIA_ALLARME_TENTATIVI tentativi falliti
      (registro, gateway o distribution che rifiutano sempre): allarme
      PraticaLavoroInErrore.
  verumdoc_ciclo_vita_scarti_autorizzazioni_non_verificate   pratiche di scarto
      BLOCCATE dal lavoratore perché la catena delle autorizzazioni non regge
      (revisione 4 di R05, U2: ``ultimo_errore.codice = LC-409-AUTORIZZAZIONI``,
      nessuna cancellazione): allarme ScartoAutorizzazioniNonVerificate (pagina).
  verumdoc_ciclo_vita_puntuali_autorizzazioni_non_verificate   pratiche di
      annullamento e rettifica BLOCCATE dal lavoratore prima del fatto (revisione
      R06, V2: stesso codice): allarme PuntualeAutorizzazioniNonVerificate (pagina,
      infra/observability/regole/verumdoc-r06.yml).

Revisione R09 (Z4, Z5):

  verumdoc_ciclo_vita_passi_periodici_in_errore     passi del lavoro periodico
      (proposte, riprese, minimizzazione, rinnovo WORM) il cui ultimo giro è
      fallito (configurazione che non permette il rinnovo, storage o registro
      giù) o ha lasciato elementi in errore: allarme PassoPeriodicoInErrore.
  verumdoc_ciclo_vita_pda_permanenti_senza_oggetti  PdA permanenti senza oggetti
      nel WORM (il rinnovo li toglie dalla coda con una data sentinella): allarme
      PdaPermanenteSenzaOggetti (infra/observability/regole/verumdoc-r09.yml).

R12a (VD-C-150): metriche PER PASSO (passi di base, senza i sotto-passi per ente):

  verumdoc_ciclo_vita_passo_periodico_in_errore{passo}       1 se l'ultimo giro del
      passo è fallito o ha lasciato elementi in errore;
  verumdoc_ciclo_vita_passo_periodico_eta_secondi{passo}     secondi dall'ultimo giro
      riuscito (dall'inserimento della riga se mai riuscito);
  verumdoc_ciclo_vita_passo_periodico_intervallo_secondi{passo}  intervallo configurato:
      allarme PassoPeriodicoFermo se l'età supera due intervalli più un'ora (lavoratore
      fermo, lease bloccato, passo che fallisce sempre); verumdoc-r12.yml.

R14 (VD-C-133):

  verumdoc_ciclo_vita_pratiche_sospese   pratiche SOSPESE dopo i tentativi esauriti
      (``LC-409-TENTATIVI-ESAURITI``, lavoro tolto, nessun nuovo tentativo): allarme
      PraticaSospesa (verumdoc-r14.yml, runbook docs/runbooks/pratica_sospesa.md).
  verumdoc_ciclo_vita_oggetti_non_determinabili   PdA di scarti ESEGUITA_CON_ECCEZIONI
      chiusi NON_COMPLETATO con ``verifica_storage = non_determinabile`` (VD-C-122: storage
      illeggibile alla chiusura o oggetto senza inventario): serve il controllo manuale
      del runbook scarto_incompleto prima di riprendere o chiudere; allarme
      ScartoNonDeterminabile.

Il processo API (sempre attivo, ha /metrics) aggiorna i valori ogni
``METRICHE_INTERVALLO_SECONDI`` con due conteggi indicizzati (``idx_aip_stato``,
``idx_pratiche_lavoro``), nell'ambito del conservatore.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Gauge
from sqlalchemy import text

from app.application.periodico import SENZA_OGGETTI
from app.config import settings
from app.domain.autorizzazioni import CODICE_BLOCCO
from app.domain.stati import CODICE_TENTATIVI_ESAURITI
from libs.shared.db.ambito import ambito_conservatore

logger = structlog.get_logger(__name__)

MOTIVO_AMBITO = "metriche del ciclo di vita: conteggi su tutti gli enti"


@dataclass
class MetricheCicloVita:
    incompleti: Gauge
    in_errore: Gauge
    autorizzazioni: Gauge
    puntuali: Gauge
    periodici: Gauge | None = None
    senza_oggetti: Gauge | None = None
    passo_in_errore: Gauge | None = None
    passo_eta: Gauge | None = None
    passo_intervallo: Gauge | None = None
    sospese: Gauge | None = None
    non_determinabili: Gauge | None = None


_METRICHE: MetricheCicloVita | None = None


def registra(registro: CollectorRegistry) -> MetricheCicloVita:
    """Registra le metriche nel registro del /metrics del servizio."""
    global _METRICHE
    _METRICHE = MetricheCicloVita(
        incompleti=Gauge("verumdoc_ciclo_vita_pda_eliminazione_incompleta",
                         "PdA con cancellazione dello scarto iniziata e non completata "
                         "(ELIMINAZIONE_INCOMPLETA)", registry=registro),
        in_errore=Gauge("verumdoc_ciclo_vita_pratiche_lavoro_in_errore",
                        "Pratiche con lavoro e tentativi falliti oltre la soglia d'allarme",
                        registry=registro),
        autorizzazioni=Gauge("verumdoc_ciclo_vita_scarti_autorizzazioni_non_verificate",
                             "Pratiche di scarto bloccate dal lavoratore: catena delle "
                             "autorizzazioni non verificata (nessuna cancellazione)",
                             registry=registro),
        puntuali=Gauge("verumdoc_ciclo_vita_puntuali_autorizzazioni_non_verificate",
                       "Pratiche di annullamento e rettifica bloccate dal lavoratore: catena "
                       "della richiesta non verificata (nessun fatto né foglia)",
                       registry=registro),
        periodici=Gauge("verumdoc_ciclo_vita_passi_periodici_in_errore",
                        "Passi del lavoro periodico con l'ultimo giro fallito o con elementi "
                        "in errore (rinnovo WORM, minimizzazione, proposte, riprese)",
                        registry=registro),
        senza_oggetti=Gauge("verumdoc_ciclo_vita_pda_permanenti_senza_oggetti",
                            "PdA permanenti senza oggetti nel WORM (fuori dal rinnovo)",
                            registry=registro),
        passo_in_errore=Gauge("verumdoc_ciclo_vita_passo_periodico_in_errore",
                              "1 se l'ultimo giro del passo periodico è fallito o ha lasciato "
                              "elementi in errore", ["passo"], registry=registro),
        passo_eta=Gauge("verumdoc_ciclo_vita_passo_periodico_eta_secondi",
                        "Secondi dall'ultimo giro riuscito del passo periodico", ["passo"],
                        registry=registro),
        passo_intervallo=Gauge("verumdoc_ciclo_vita_passo_periodico_intervallo_secondi",
                               "Intervallo configurato del passo periodico", ["passo"],
                               registry=registro),
        sospese=Gauge("verumdoc_ciclo_vita_pratiche_sospese",
                      "Pratiche sospese dopo i tentativi esauriti (LC-409-TENTATIVI-ESAURITI): "
                      "nessun nuovo tentativo, ripresa manuale", registry=registro),
        non_determinabili=Gauge(
            "verumdoc_ciclo_vita_oggetti_non_determinabili",
            "PdA di scarti con eccezioni chiusi con versioni rimaste non determinabili "
            "(controllo manuale prima della ripresa)", registry=registro),
    )
    return _METRICHE


def metriche() -> MetricheCicloVita | None:
    return _METRICHE


def intervalli_passi() -> dict[str, int]:
    """R12a (VD-C-150): passi di base del lavoro periodico e il loro intervallo."""
    from app.application import periodico

    return {periodico.PASSO_PROPOSTE: int(settings.periodico_proposte_secondi),
            periodico.PASSO_RIPRESE: int(settings.periodico_riprese_secondi),
            periodico.PASSO_MINIMIZZAZIONE: int(settings.periodico_minimizzazione_secondi),
            periodico.PASSO_RINNOVO: int(settings.periodico_rinnovo_secondi)}


async def aggiorna() -> dict[str, int]:
    """Legge i conteggi e aggiorna le metriche; restituisce i valori."""
    from libs.shared.db import get_async_session_lettura

    with ambito_conservatore(MOTIVO_AMBITO, registra=False):
        async with get_async_session_lettura() as session:
            incompleti = int((await session.execute(text(
                "SELECT count(*) FROM public.aip WHERE stato = 'ELIMINAZIONE_INCOMPLETA'"
            ))).scalar_one())
            in_errore = int((await session.execute(text(
                "SELECT count(*) FROM public.pratiche WHERE lavoro IS NOT NULL "
                "AND tentativi >= :soglia"),
                {"soglia": settings.lavoratore_soglia_allarme_tentativi})).scalar_one())
            autorizzazioni = int((await session.execute(text(
                "SELECT count(*) FROM public.pratiche WHERE tipo = 'SCARTO' "
                "AND lavoro IS NULL AND stato IN ('ESEGUIBILE', 'ESEGUITA_CON_ECCEZIONI') "
                "AND ultimo_errore ->> 'codice' = :codice"),
                {"codice": CODICE_BLOCCO})).scalar_one())
            puntuali = int((await session.execute(text(
                "SELECT count(*) FROM public.pratiche WHERE tipo IN ('ANNULLAMENTO', "
                "'RETTIFICA') AND lavoro IS NULL AND ultimo_errore ->> 'codice' = :codice"),
                {"codice": CODICE_BLOCCO})).scalar_one())
            periodici = int((await session.execute(text(
                "SELECT count(*) FROM public.ciclo_vita_periodico WHERE ultimo_esito ? 'errore' "
                "OR COALESCE((ultimo_esito ->> 'elementi_in_errore')::int, 0) > 0"
            ))).scalar_one())
            senza_oggetti = int((await session.execute(text(
                "SELECT count(*) FROM public.aip WHERE anni_conservazione = 999 "
                "AND retention_fino_a = :s"), {"s": SENZA_OGGETTI})).scalar_one())
            sospese = int((await session.execute(text(
                "SELECT count(*) FROM public.pratiche WHERE lavoro IS NULL "
                "AND ultimo_errore ->> 'codice' = :codice"),
                {"codice": CODICE_TENTATIVI_ESAURITI})).scalar_one())
            non_determinabili = int((await session.execute(text(
                "SELECT count(*) FROM (SELECT 1 FROM public.pratiche p "
                "JOIN public.pratiche_oggetti o ON o.pratica_id = p.id "
                "AND o.esito = 'NON_COMPLETATO' WHERE p.tipo = 'SCARTO' "
                "AND p.stato = 'ESEGUITA_CON_ECCEZIONI' "
                "AND o.dettagli ->> 'verifica_storage' = 'non_determinabile' LIMIT 100000) x"
            ))).scalar_one())
            passi = (await session.execute(text(
                "SELECT nome, (ultimo_esito ? 'errore' OR COALESCE((ultimo_esito ->> "
                "'elementi_in_errore')::int, 0) > 0) AS in_errore, "
                "EXTRACT(EPOCH FROM (now() - COALESCE(ultimo_giro, aggiornato_il))) AS eta "
                "FROM public.ciclo_vita_periodico WHERE nome = ANY(:n)"),
                {"n": list(intervalli_passi())})).mappings().all()
    m = metriche()
    if m is not None:
        m.incompleti.set(incompleti)
        m.in_errore.set(in_errore)
        m.autorizzazioni.set(autorizzazioni)
        m.puntuali.set(puntuali)
        if m.periodici is not None:
            m.periodici.set(periodici)
        if m.senza_oggetti is not None:
            m.senza_oggetti.set(senza_oggetti)
        if m.sospese is not None:
            m.sospese.set(sospese)
        if m.non_determinabili is not None:
            m.non_determinabili.set(non_determinabili)
        if m.passo_in_errore is not None and m.passo_eta is not None \
                and m.passo_intervallo is not None:
            for nome, intervallo in intervalli_passi().items():
                m.passo_intervallo.labels(nome).set(intervallo)
            for r in passi:
                m.passo_in_errore.labels(r["nome"]).set(1 if r["in_errore"] else 0)
                m.passo_eta.labels(r["nome"]).set(float(r["eta"] or 0))
    if sospese:
        logger.error("ciclo_vita_pratiche_sospese", pratiche=sospese)
    if periodici:
        logger.error("ciclo_vita_passi_periodici_in_errore", passi=periodici)
    if senza_oggetti:
        logger.error("ciclo_vita_pda_permanenti_senza_oggetti", pda=senza_oggetti)
    if incompleti:
        logger.error("scarto_pda_eliminazione_incompleta_presenti", pda=incompleti)
    if autorizzazioni:
        logger.error("scarto_autorizzazioni_non_verificate_presenti", pratiche=autorizzazioni)
    if puntuali:
        logger.error("puntuali_autorizzazioni_non_verificate_presenti", pratiche=puntuali)
    return {"incompleti": incompleti, "in_errore": in_errore, "autorizzazioni": autorizzazioni,
            "puntuali": puntuali, "periodici": periodici, "senza_oggetti": senza_oggetti,
            "sospese": sospese, "non_determinabili": non_determinabili}


async def ciclo(fine: asyncio.Event) -> None:
    """Aggiornamento periodico (processo API)."""
    while not fine.is_set():
        try:
            await aggiorna()
        except Exception as exc:  # noqa: BLE001 — database momentaneamente giù: si riprova
            logger.warning("metriche_ciclo_vita_non_aggiornate", errore=type(exc).__name__)
        try:
            await asyncio.wait_for(fine.wait(), timeout=settings.metriche_intervallo_secondi)
        except TimeoutError:
            pass


__all__: list[Any] = ["MetricheCicloVita", "aggiorna", "ciclo", "intervalli_passi", "metriche",
                      "registra"]
