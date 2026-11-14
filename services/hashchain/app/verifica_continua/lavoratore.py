# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lavoratore della verifica continua: ciclo di presa in carico, verifica,
registrazione e manutenzione. Più repliche lavorano insieme senza doppie letture
(lease per riga con ``FOR UPDATE SKIP LOCKED``, recinto sul ``lease_id``).

Un giro (``giro``): prende un lotto (prima le verifiche su richiesta), legge le
foglie K1 dalla fonte configurata, verifica i PdA in parallelo
(``VERIFICA_CONCORRENZA``) con i budget di lettura condivisi della replica e
registra ogni esito nella sua transazione. Allarme immediato su ogni anomalia:
log ``integrita_anomalia`` (errore), metriche, evento ``integrita.anomalia``
in outbox (scritto nella transazione dell'esito).

Manutenzione (``manutenzione``), a intervalli: semina e riconciliazione della
programmazione, preparazione delle richieste d'ente, partizioni future del
registro (``libs/shared/db/partizioni.py``), accorpamento della copertura,
riepilogo di tutti gli enti, metriche di ritardo e anomalie aperte, battito.
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from app.verifica_continua import cicli as cicli_mod
from app.verifica_continua import esiti, incompleti, repository
from app.verifica_continua.budget import Budget
from app.verifica_continua.config import ImpostazioniVerifica
from app.verifica_continua.foglie import FonteFoglie, FonteFoglieAssente
from app.verifica_continua.metriche import MetricheVerifica
from app.verifica_continua.verificatore import EsitoPda, PdaDaVerificare, verifica_pda

logger = structlog.get_logger(__name__)


def detentore_predefinito() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def politica_partizioni(cfg: ImpostazioniVerifica) -> Any:
    """Politica del registro per ``libs.shared.db.partizioni`` (mese, nessun distacco:
    il registro è evidenza dei controlli eseguiti)."""
    from libs.shared.db.partizioni import Politica

    return Politica("verifiche_registro", "mese", anticipo=cfg.partizioni_mesi_avanti)


class Lavoratore:
    def __init__(
        self,
        cfg: ImpostazioniVerifica,
        *,
        client: Any,
        detentore: str | None = None,
        fonte_foglie: FonteFoglie | None = None,
        metriche: MetricheVerifica | None = None,
        orologio: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.detentore = (detentore or cfg.detentore or detentore_predefinito())[:200]
        self.fonte_foglie: FonteFoglie = fonte_foglie or FonteFoglieAssente()
        self.metriche = metriche or MetricheVerifica()
        self.orologio = orologio or (lambda: datetime.now(UTC))
        self.budget_letture = Budget(cfg.letture_al_secondo)
        self.budget_byte = Budget(cfg.byte_al_secondo)
        self._ultima: dict[str, float] = {}
        self.cicli = cicli_mod.Cicli(predefinito=cfg.ciclo_giorni)

    # ── giro di verifica ──────────────────────────────────────────────────
    async def giro(self) -> int:
        """Un lotto: restituisce i PdA presi in carico (0 = nulla da fare)."""
        lotto = await repository.prendi_lotto(self.cfg, self.orologio(), self.detentore)
        if not lotto:
            return 0
        # R09 (D05): ciclo per ente e classe dal motore (una query per lotto)
        try:
            self.cicli = await cicli_mod.carica(self.cfg.ciclo_giorni)
        except Exception as exc:  # noqa: BLE001 — si usa l'ultimo valore letto
            logger.error("verifica_cicli_non_letti", errore=type(exc).__name__)
        try:
            foglie = await self.fonte_foglie.foglie([p.aip_id for p in lotto])
        except Exception as exc:  # noqa: BLE001 — senza foglie si verifica comunque
            logger.error("verifica_foglie_non_lette", fonte=self.fonte_foglie.nome,
                         errore=type(exc).__name__)
            foglie = {}
        semaforo = asyncio.Semaphore(self.cfg.concorrenza)

        async def uno(pda: PdaDaVerificare) -> None:
            async with semaforo:
                try:
                    await self._verifica_e_registra(pda, foglie.get(pda.aip_id))
                except Exception as exc:  # noqa: BLE001 — il lease scade e un'altra
                    # replica (o questa) riprende il PdA: nessun esito inventato
                    logger.error("verifica_pda_non_conclusa", aip_id=str(pda.aip_id),
                                 errore=type(exc).__name__, dettaglio=str(exc)[:300])

        attese_prima = (self.budget_letture.atteso_secondi, self.budget_byte.atteso_secondi)
        await asyncio.gather(*(uno(p) for p in lotto))
        self.metriche.attesa_budget.labels("letture").inc(
            self.budget_letture.atteso_secondi - attese_prima[0])
        self.metriche.attesa_budget.labels("byte").inc(
            self.budget_byte.atteso_secondi - attese_prima[1])
        self.metriche.ultimo_giro.set(time.time())
        return len(lotto)

    async def _verifica_e_registra(self, pda: PdaDaVerificare, foglie: Any) -> None:
        if pda.stato in repository.STATI_FUORI_ROTAZIONE:
            await repository.esci_dalla_rotazione(pda)
            return
        esito = await verifica_pda(
            pda, client=self.client, budget_letture=self.budget_letture,
            budget_byte=self.budget_byte, cfg=self.cfg, adesso=self.orologio(), foglie=foglie)
        if pda.stato == repository.STATO_INCOMPLETO:
            # R09 (D26, Z2): le versioni trattenute devono esserci; mancano solo
            # le chiavi cancellate per intero dallo scarto
            esito = await incompleti.adatta_esito(esito, client=self.client,
                                                  budget_letture=self.budget_letture)
        conclusione = await repository.registra_esito(
            esito, self.orologio(), self.cfg, self.detentore,
            ciclo_giorni=self.cicli.giorni(pda.tenant_id, pda.classe_id))
        self._registra_metriche(esito, conclusione)
        if self.cfg.incidente_campione and conclusione.registrato and (
                conclusione.anomalie or conclusione.errore_ripetuto):
            await self._incidente(pda, conclusione)

    async def _incidente(self, pda: PdaDaVerificare, conclusione: Any) -> None:
        """R09 (D05): un incidente di storage avvia una verifica a campione (al più
        una ogni ``VERIFICA_INCIDENTE_PAUSA_ORE``), mai bloccante per il giro."""
        from datetime import timedelta

        from libs.shared.parametri import parametri
        from libs.verumdoc_rules.parametri_servizio import VERIFICA_CAMPIONE_INCIDENTE_PDA

        try:
            n = int((await parametri([VERIFICA_CAMPIONE_INCIDENTE_PDA]))[
                VERIFICA_CAMPIONE_INCIDENTE_PDA])
            await repository.campione_dopo_incidente(
                self.orologio(), n=min(n, self.cfg.richiesta_max_pda),
                pausa=timedelta(hours=self.cfg.incidente_pausa_ore),
                motivo=f"{conclusione.esito} sul PdA {pda.aip_id}")
        except Exception as exc:  # noqa: BLE001 — l'allarme principale è già partito
            logger.error("verifica_campione_non_avviato", errore=type(exc).__name__)

    def _registra_metriche(self, esito: EsitoPda, conclusione: repository.Conclusione) -> None:
        m = self.metriche
        if conclusione.fuori_rotazione:
            return
        if not conclusione.registrato:
            m.lease_persi.inc()
            return
        m.byte_letti.inc(esito.byte_letti)
        for o in esito.oggetti:
            m.oggetti.labels(o.componente, o.esito).inc()
        for o in conclusione.anomalie:
            m.anomalie.labels(o.esito).inc()
        if conclusione.concluso:
            m.pda.labels(conclusione.esito).inc()
        if conclusione.errore_ripetuto:
            m.errori_ripetuti.inc()
        pda = esito.pda
        if conclusione.anomalie or conclusione.errore_ripetuto:
            logger.error(
                "integrita_anomalia", aip_id=str(pda.aip_id), pda=pda.urn,
                tenant_id=str(pda.tenant_id), esito=conclusione.esito,
                errore_lettura_ripetuto=conclusione.errore_ripetuto,
                tentativo=conclusione.tentativo,
                oggetti=[{"componente": o.componente, "esito": o.esito,
                          "documento_id": str(o.documento_id) if o.documento_id else None}
                         for o in esito.oggetti if o.esito != esiti.OK])
        elif any(o.esito == esiti.ERRORE_LETTURA for o in esito.oggetti):
            logger.warning("verifica_errore_lettura", aip_id=str(pda.aip_id),
                           tentativo=conclusione.tentativo)

    # ── manutenzione ──────────────────────────────────────────────────────
    def _scaduto(self, nome: str, intervallo: float, forza: bool) -> bool:
        adesso = time.monotonic()
        if forza or adesso - self._ultima.get(nome, -1e18) >= intervallo:
            self._ultima[nome] = adesso
            return True
        return False

    async def manutenzione(self, *, forza: bool = False) -> None:
        """Lavori periodici; ognuno isolato (un guasto non ferma gli altri)."""
        cfg = self.cfg
        lavori: list[tuple[str, float, Callable[[], Any]]] = [
            ("partizioni", cfg.manutenzione_intervallo_secondi, self._partizioni),
            ("semina", cfg.semina_intervallo_secondi,
             lambda: repository.semina(cfg, self.orologio())),
            ("riconciliazione", cfg.semina_intervallo_secondi,
             lambda: repository.riconcilia(cfg, self.orologio())),
            ("richieste", 0, lambda: repository.prepara_richieste(cfg, self.orologio())),
            ("copertura", cfg.manutenzione_intervallo_secondi,
             lambda: repository.compatta_copertura(cfg, self.orologio())),
            ("riepilogo", cfg.riepilogo_intervallo_secondi, self._riepilogo),
        ]
        for nome, intervallo, lavoro in lavori:
            if not self._scaduto(nome, intervallo, forza):
                continue
            try:
                await lavoro()
            except Exception as exc:  # noqa: BLE001 — si riprova al giro successivo
                logger.error("verifica_manutenzione_fallita", lavoro=nome,
                             errore=type(exc).__name__, dettaglio=str(exc)[:300])

    async def _partizioni(self) -> None:
        # R04 correzioni (rev4 rilievi 5-6): con il ruolo proprietario
        # (DATABASE_URL_MANUTENZIONE) o in sviluppo; altrimenti le crea il CronJob
        # della manutenzione con lo stesso anticipo predefinito.
        from libs.shared.db import engine_manutenzione
        from libs.shared.db.manutenzione import partizioni_nel_processo
        from libs.shared.db.partizioni import esegui_manutenzione_partizioni

        if not partizioni_nel_processo():
            return
        await esegui_manutenzione_partizioni(engine_manutenzione(), self.orologio(),
                                             politiche=(politica_partizioni(self.cfg),))

    async def _riepilogo(self) -> None:
        adesso = self.orologio()
        await repository.aggiorna_riepilogo_globale(adesso)
        self.metriche.ritardo.set(await repository.ritardo_rotazione(adesso))
        self.metriche.anomalie_aperte.set((await repository.anomalie_aperte(None, elenco=0))
                                          ["numero"])
        await repository.segnala_lavoratore(self.detentore, self.fonte_foglie.nome, adesso)

    # ── ciclo ─────────────────────────────────────────────────────────────
    async def esegui(self, fine: asyncio.Event) -> None:
        """Ciclo fino all'arresto: manutenzione quando scade, poi lotti finché ce n'è."""
        logger.info("verifica_continua_avviata", detentore=self.detentore,
                    fonte_foglie=self.fonte_foglie.nome, ciclo_giorni=self.cfg.ciclo_giorni)
        while not fine.is_set():
            await self.manutenzione()
            try:
                presi = await self.giro()
            except Exception as exc:  # noqa: BLE001 — database momentaneamente giù
                logger.error("verifica_giro_fallito", errore=type(exc).__name__,
                             dettaglio=str(exc)[:300])
                presi = 0
            if presi == 0:
                try:
                    await asyncio.wait_for(fine.wait(), timeout=self.cfg.attesa_inattivo_secondi)
                except TimeoutError:
                    pass
        logger.info("verifica_continua_fermata", detentore=self.detentore)
