# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Ciclo di fondo del registro delle impronte (sostituisce, da R04, il consumer
Kafka dei lotti di ``aip.conserved``).

Servizio: hashchain. Riferimenti: contratti K1-K3 di R04; REGOLE_SVILUPPO
§12.9 (lease), §22.2 (nessun lotto resta aperto), §22.3 (rete di sicurezza).

A ogni giro (``CATENA_SEQUENZIATORE_INTERVALLO_SECONDI``), solo se il processo
detiene il lease del registro:
  1. sequenziatore: aggiunge al registro i PdA in coda, a lotti, finché la coda
     non si svuota (con un massimo di lotti per giro);
  2. checkpoint: uno ogni ``CATENA_CHECKPOINT_SECONDI`` se il registro è cresciuto;
  3. sigillo qualificato dei checkpoint in sospeso, invio al testimone (o,
     se non c'è nulla da inviare, una lettura di prova ogni
     ``TESTIMONE_SONDA_SECONDI``: un testimone che non risponde è un allarme),
     controllo delle controfirme e dei rifiuti (allarmi);
  4. completamento dei PdA coperti (RdV v2, CONSERVATO, ``aip.conserved``),
     solo con checkpoint controfirmati (o senza testimone dove l'ambiente lo
     ammette) e mai con un rifiuto del testimone non gestito;
  5. ogni ``INTERVALLO_RIPRESA_SECONDI``: firma e RdV dei blocchi della vecchia
     catena ancora in sospeso (formato 2, fino a esaurimento);
  6. (R05, VD-C-112) ogni ``audit_secondi``: export nel WORM dei periodi chiusi di
     ``audit_global``, con la foglia ``audit`` in coda al registro
     (``app.registro.audit_worm``); un errore dell'export non ferma il registro;
  7. (R12a, VD-C-148) ogni ``rinnovo_worm_secondi``: rinnovo del blocco WORM degli
     oggetti ``registro/`` (``app.registro.rinnovo_worm``), anche con il registro
     bloccato dalla coerenza; un passo non riuscito si riprova al giro dopo;
  8. (R13a, VD-C-068) marca RFC 3161 dell'ultimo checkpoint (ancoraggio esterno),
     sigillo dei documenti del conservatore PRIMA della conservazione (IPdA, RdV,
     manuale, piano) e, ogni ``firma_rinnovo_secondi``, rimarcatura dei documenti
     in scadenza (``app.registro.firme_documenti``), con il lease del registro.
Ogni passo registra i propri errori e non ferma gli altri: un passo fallito si
riprende al giro successivo (nessun errore inghiottito: log + metrica).
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.registro import (
    archivio,
    archivio_worm,
    audit_worm,
    checkpoint,
    coerenza,
    conservazione,
    sequenziatore,
)
from app.registro.metriche import metriche
from app.registro.testimone import ClientTestimone
from app.services.firma_blocchi import ServizioFirmaBlocchi
from libs.shared.db import get_async_session

logger = structlog.get_logger(__name__)

#: Lotti del sequenziatore al massimo per giro (gli altri al giro dopo)
LOTTI_MASSIMI_PER_GIRO = 20


def detentore_predefinito() -> str:
    """Identità del processo per il lease (host, pid, casuale)."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:200]


@dataclass
class ConfigCiclo:
    """Parametri del ciclo (dalle impostazioni del servizio)."""

    intervallo: float
    checkpoint_secondi: float
    controfirma_max_secondi: float
    lease_secondi: float
    lotto: int
    completamento_lotto: int
    segmento_worm: int
    ripresa_secondi: float
    sonda_testimone_secondi: float = 60.0
    #: Registro SOSPESO (runbook ripristino_registro_da_backup.md): il ciclo non
    #: sequenzia, non emette checkpoint e non contatta il testimone; la API
    #: (checkpoint, prove, foglie) resta disponibile per i controlli.
    sospeso: bool = False
    #: Ogni quanto si ripete il controllo di coerenza finché non passa (T4)
    controllo_coerenza_secondi: float = 60.0
    #: R05 (VD-C-112): ogni quanto si esportano i periodi chiusi dell'audit
    audit_secondi: float = 60.0
    audit_attivo: bool = False
    #: R12a (VD-C-148): rinnovo del blocco WORM degli oggetti registro/
    rinnovo_worm_secondi: float = 86400.0
    rinnovo_worm_attivo: bool = False
    #: R13a: sigillo dei documenti e rimarcatura
    firma_documenti_attiva: bool = False
    firma_rinnovo_secondi: float = 3600.0
    firma_rinnovo_attivo: bool = False


@dataclass
class CicloCatena:
    """Stato del ciclo; ``giro`` è la singola esecuzione (provabile con un orologio finto)."""

    config: ConfigCiclo
    identita: checkpoint.Identita
    servizio_firma: ServizioFirmaBlocchi
    testimone: ClientTestimone | None
    detentore: str = field(default_factory=detentore_predefinito)
    adesso: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    orologio: Callable[[], float] = time.monotonic
    ripresa_storica: Callable[[datetime], Awaitable[Any]] | None = None
    #: Completamento con checkpoint NON_CONFIGURATO ammesso: solo senza testimone
    #: e fuori da produzione (``chiavi.testimone_facoltativo``)
    ammetti_senza_testimone: bool = False
    _prossimo_checkpoint: float = 0.0
    _prossima_ripresa: float = 0.0
    _prossima_sonda: float = 0.0
    _prossimo_audit: float = 0.0
    _prossimo_rinnovo_worm: float = 0.0
    _prossimo_rinnovo_firme: float = 0.0
    #: Controllo di coerenza con WORM e testimone (T4); None = quello reale
    controllo_coerenza: Callable[[], Awaitable[Any]] | None = None
    _coerenza_ok: bool = False
    _prossimo_controllo: float = 0.0
    _errori_coerenza: int = 0

    async def _testimone(self, ora: float) -> Any:
        """Invio K3; se non c'è nulla da inviare, una sonda ogni tanto."""
        metriche().testimone_configurato.set(1 if self.testimone is not None else 0)
        esito = await checkpoint.invia_al_testimone(
            self.testimone, detentore=self.detentore, adesso=self.adesso())
        if esito is None and self.testimone is not None and ora >= self._prossima_sonda:
            self._prossima_sonda = ora + self.config.sonda_testimone_secondi
            raggiunto = await self.testimone.sonda()
            checkpoint.registra_raggiungibilita(
                raggiunto, "" if raggiunto else "lettura di prova non riuscita")
        return esito

    async def _passo(self, nome: str, azione: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await azione()
        except archivio.LeasePerso as exc:
            logger.warning("registro_lease_perso", passo=nome, errore=str(exc)[:200])
        except archivio_worm.ContenutoWormDiverso as exc:
            # T4: stesso nome nel WORM con contenuto diverso = allarme PERSISTENTE;
            # il registro smette di scrivere invece di ritentare a ogni giro
            metriche().errori.labels(nome).inc()
            try:
                await coerenza.apri_blocco(coerenza.CAUSA_WORM_DIVERSO,
                                           {"passo": nome, **exc.dettagli}, self.adesso())
            except Exception as altro:  # noqa: BLE001 — DB irraggiungibile: riprova al giro
                logger.error("registro_blocco_non_registrato", errore=str(altro)[:200])
        except Exception as exc:  # noqa: BLE001 — registrato, ripreso al giro dopo
            metriche().errori.labels(nome).inc()
            logger.error("registro_passo_fallito", passo=nome, tipo=type(exc).__name__,
                         errore=str(exc)[:300])
        return None

    async def _verifica_coerenza(self) -> Any:
        if self.controllo_coerenza is not None:
            return await self.controllo_coerenza()
        return await coerenza.verifica(testimone=self.testimone,
                                       origine=self.identita.origine)

    async def _puo_scrivere(self, ora: float) -> bool:
        """T4: il registro scrive solo dopo un controllo di coerenza superato con
        WORM e testimone, e senza blocchi persistenti aperti."""
        if not self._coerenza_ok and ora >= self._prossimo_controllo:
            self._prossimo_controllo = ora + self.config.controllo_coerenza_secondi
            try:
                esito = await self._verifica_coerenza()
            except Exception as exc:  # noqa: BLE001 — mai un fallimento silenzioso (F1)
                await self._coerenza_non_verificabile(exc)
                return False
            self._errori_coerenza = 0
            self._coerenza_ok = await coerenza.applica_controllo(esito, self.adesso())
        async with get_async_session() as session:
            aperti = await coerenza.blocchi_aperti(session)
        return self._coerenza_ok and not aperti

    async def _coerenza_non_verificabile(self, exc: Exception) -> None:
        """Quarto giro (F1): il controllo di coerenza non si può fare. Il registro
        non scrive; un errore di permesso (es. ruolo senza ``s3:ListBucket``) apre
        SUBITO il blocco esplicito ``COERENZA_NON_VERIFICABILE`` (allarme
        ``RegistroBloccato``), un altro errore dopo ``ERRORI_PRIMA_DEL_BLOCCO``
        tentativi consecutivi. Il blocco si chiude da solo quando il controllo passa."""
        self._errori_coerenza += 1
        permesso = coerenza.errore_di_permesso(exc)
        logger.error("registro_coerenza_non_verificabile", tipo=type(exc).__name__,
                     errore=str(exc)[:300], tentativi=self._errori_coerenza,
                     errore_di_permesso=permesso,
                     azione="controllare il ruolo IAM della hashchain (s3:ListBucket sul "
                            "prefisso registro/ del bucket dei PdA) e la raggiungibilità del WORM")
        metriche().errori.labels("coerenza").inc()
        if permesso or self._errori_coerenza >= coerenza.ERRORI_PRIMA_DEL_BLOCCO:
            await coerenza.apri_blocco(coerenza.CAUSA_NON_VERIFICABILE, {
                "tipo": type(exc).__name__, "errore": str(exc)[:500],
                "errore_di_permesso": permesso, "tentativi": self._errori_coerenza},
                self.adesso())

    async def _allarmi(self) -> None:
        """Controlli degli allarmi: a ogni giro, anche con il registro sospeso o
        bloccato (T5), così le loro metriche non restano ferme."""
        await self._passo("allarme_controfirma", lambda: checkpoint.controlla_controfirme(
            adesso=self.adesso(), massimo_secondi=self.config.controfirma_max_secondi))
        await self._passo("allarme_rifiuti", checkpoint.controlla_rifiuti)
        await self._passo("allarme_blocchi", coerenza.controlla_blocchi)

    async def _lease(self, adesso: datetime) -> bool:
        async with get_async_session() as session:
            return await archivio.acquisisci_lease(
                session, self.detentore, adesso,
                adesso + timedelta(seconds=self.config.lease_secondi))

    async def giro(self) -> None:
        metriche().registro_sospeso.set(1 if self.config.sospeso else 0)
        if self.config.sospeso:
            # Si ripeterà il controllo di coerenza quando la sospensione si toglie
            self._coerenza_ok = False
            self._prossimo_controllo = 0.0
            logger.warning("registro_sospeso", motivo="CATENA_REGISTRO_SOSPESO=true: niente "
                           "sequenziatore, checkpoint né testimone; gli allarmi continuano")
            await self._allarmi()
            return
        adesso = self.adesso()
        ottenuto = await self._passo("lease", lambda: self._lease(adesso))
        if not ottenuto:
            return
        ora = self.orologio()
        if await self._passo("coerenza", lambda: self._puo_scrivere(ora)):
            await self._scritture(ora)
        await self._rinnovo_worm(ora)
        await self._allarmi()

    async def _rinnovo_worm(self, ora: float) -> None:
        """R12a (VD-C-148): rinnovo del blocco WORM degli oggetti registro/, con il
        lease del registro, anche con il registro bloccato (protegge, non scrive
        contenuti). Un passo non riuscito si riprova al giro successivo (Z4)."""
        if not self.config.rinnovo_worm_attivo or ora < self._prossimo_rinnovo_worm:
            return
        from app.registro import rinnovo_worm

        esito = await self._passo("rinnovo_worm", lambda: rinnovo_worm.passo(self.adesso()))
        if esito is not None:
            self._prossimo_rinnovo_worm = ora + self.config.rinnovo_worm_secondi

    async def _scritture(self, ora: float) -> None:
        async def sequenzia() -> int:
            totale = 0
            for _ in range(LOTTI_MASSIMI_PER_GIRO):
                esito = await sequenziatore.aggiungi_lotto(
                    detentore=self.detentore, adesso=self.adesso(), limite=self.config.lotto)
                totale += esito.aggiunte
                metriche().dimensione.set(esito.dimensione)
                if esito.aggiunte + esito.gia_presenti < self.config.lotto:
                    break
            return totale

        await self._passo("sequenziatore", sequenzia)
        if ora >= self._prossimo_checkpoint:
            # Cadenza K2: un tentativo ogni CATENA_CHECKPOINT_SECONDI (emette solo
            # se il registro è cresciuto dall'ultimo checkpoint)
            self._prossimo_checkpoint = ora + self.config.checkpoint_secondi
            await self._passo("checkpoint", lambda: checkpoint.emetti_checkpoint(
                detentore=self.detentore, adesso=self.adesso(), identita=self.identita,
                segmento=self.config.segmento_worm))
        await self._passo("sigillo", lambda: checkpoint.sigilla_in_sospeso(
            self.servizio_firma, adesso=self.adesso()))
        await self._passo("testimone", lambda: self._testimone(ora))
        if self.config.firma_documenti_attiva:
            from app.registro import firme_documenti

            await self._passo("marca_checkpoint", lambda: firme_documenti.marca_checkpoint(
                self.servizio_firma, detentore=self.detentore, adesso=self.adesso()))
            # R14 (VD-C-165): IPdA storici fra i documenti da sigillare (prima del
            # sigillo, così il giro stesso li firma)
            await self._passo("arretrato_ipda", lambda: firme_documenti.censisci_arretrato_ipda(
                self.servizio_firma, detentore=self.detentore, adesso=self.adesso()))
            await self._passo("firme_documenti", lambda: firme_documenti.sigilla(
                self.servizio_firma, detentore=self.detentore, adesso=self.adesso()))
            # R16 (VD-C-166): firme personali del responsabile riverificate e nel WORM
            from app.registro import firme_responsabile

            await self._passo("firme_responsabile", lambda: firme_responsabile.archivia(
                self.servizio_firma, detentore=self.detentore, adesso=self.adesso()))
            if self.config.firma_rinnovo_attivo and ora >= self._prossimo_rinnovo_firme:
                esito = await self._passo("rinnovo_firme", lambda: firme_documenti.rinnova(
                    self.servizio_firma, detentore=self.detentore, adesso=self.adesso()))
                if esito is not None:
                    self._prossimo_rinnovo_firme = ora + self.config.firma_rinnovo_secondi
        # R15 (VD-C-177): versioni della documentazione del servizio nel WORM (registro/)
        from app.registro import documentazione

        await self._passo("documentazione", lambda: documentazione.archivia(
            detentore=self.detentore, adesso=self.adesso()))
        await self._passo("conservazione", lambda: conservazione.completa_pda_coperti(
            adesso=self.adesso(), ammetti_senza_testimone=self.ammetti_senza_testimone,
            limite=self.config.completamento_lotto,
            verificatore=self.identita.firmatario.verificatore))
        if self.config.audit_attivo and ora >= self._prossimo_audit:
            self._prossimo_audit = ora + self.config.audit_secondi
            await self._passo("audit", lambda: audit_worm.esporta_periodi_chiusi(
                adesso=self.adesso()))
        ripresa = self.ripresa_storica
        if ripresa is not None and ora >= self._prossima_ripresa:
            self._prossima_ripresa = ora + self.config.ripresa_secondi
            await self._passo("blocchi_storici", lambda: ripresa(self.adesso()))

    async def esegui(self, fine: asyncio.Event | None = None) -> None:
        """Gira finché ``fine`` non è impostato; all'arresto rilascia il lease."""
        logger.info("registro_ciclo_avviato", detentore=self.detentore)
        try:
            while fine is None or not fine.is_set():
                await self.giro()
                if fine is None:
                    await asyncio.sleep(self.config.intervallo)
                else:
                    try:
                        await asyncio.wait_for(fine.wait(), timeout=self.config.intervallo)
                    except TimeoutError:
                        pass
        finally:
            try:
                async with get_async_session() as session:
                    await archivio.rilascia_lease(session, self.detentore)
            except Exception as exc:  # noqa: BLE001 — il lease scade comunque
                logger.warning("registro_lease_non_rilasciato", errore=str(exc)[:200])


def ciclo_predefinito() -> CicloCatena:
    """Ciclo con le dipendenze reali (impostazioni del servizio)."""
    from app.config import settings
    from app.registro.chiavi import firmatario_registro, origine_registro, testimone_facoltativo
    from app.registro.testimone import client_predefinito
    from app.services.block_sealer import riprendi_in_sospeso
    from app.services.firma_blocchi import servizio_firma_predefinito

    servizio = servizio_firma_predefinito()
    testimone = client_predefinito()

    async def ripresa(adesso: datetime) -> Any:
        return await riprendi_in_sospeso(servizio, emessa_il=adesso)

    return CicloCatena(
        config=ConfigCiclo(
            intervallo=settings.catena_sequenziatore_intervallo_secondi,
            checkpoint_secondi=settings.catena_checkpoint_secondi,
            controfirma_max_secondi=settings.catena_controfirma_max_secondi,
            lease_secondi=settings.catena_lease_secondi,
            lotto=settings.catena_sequenziatore_lotto,
            completamento_lotto=settings.catena_completamento_lotto,
            segmento_worm=settings.catena_worm_segmento_foglie,
            ripresa_secondi=settings.intervallo_ripresa_secondi,
            sospeso=settings.catena_registro_sospeso,
            sonda_testimone_secondi=settings.testimone_sonda_secondi,
            audit_attivo=settings.audit_export_attivo,
            rinnovo_worm_secondi=settings.rinnovo_worm_secondi,
            rinnovo_worm_attivo=settings.rinnovo_worm_attivo,
            firma_documenti_attiva=True,
            firma_rinnovo_secondi=settings.firma_rinnovo_secondi,
            firma_rinnovo_attivo=settings.firma_rinnovo_attivo,
        ),
        identita=checkpoint.Identita(firmatario_registro(settings), origine_registro(settings),
                                     testimone is not None),
        servizio_firma=servizio,
        testimone=testimone,
        ripresa_storica=ripresa,
        ammetti_senza_testimone=testimone is None and testimone_facoltativo(settings),
    )
