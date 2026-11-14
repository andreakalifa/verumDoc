# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Metriche Prometheus del registro delle impronte (GET /metrics della hashchain).

  verumdoc_catena_dimensione                          foglie nel registro
  verumdoc_catena_checkpoint_dimensione               dimensione dell'ultimo checkpoint
  verumdoc_catena_checkpoint_senza_controfirma_secondi età dell'ultimo checkpoint in
                                                       attesa di controfirma (0 = nessuno)
  verumdoc_catena_allarmi_controfirma_totali          allarmi «checkpoint senza controfirma»
  verumdoc_catena_errori_totali{passo}                errori dei passi del ciclo
  verumdoc_catena_checkpoint_rifiutati_aperti         checkpoint RIFIUTATI dal testimone e
                                                       non ancora gestiti da un operatore
                                                       (> 0 = allarme; completamento sospeso)
  verumdoc_catena_allarmi_testimone_aperti            allarmi aperti (rifiuti raggruppati per causa)
  verumdoc_catena_testimone_configurato               1 se TESTIMONE_URL è impostato
  verumdoc_catena_testimone_raggiungibile             1 se l'ultimo contatto col testimone
                                                       è riuscito, 0 se non risponde
  verumdoc_catena_registro_sospeso                    1 se CATENA_REGISTRO_SOSPESO (T5)
  verumdoc_catena_registro_bloccato                   blocchi persistenti aperti (T4): il
                                                       registro non scrive
  verumdoc_catena_registro_soglia                     max(N, X, T) dell'ultimo controllo di
                                                       coerenza con il WORM e il testimone
  verumdoc_catena_audit_ritardo_secondi               R05 (VD-C-112): secondi fra adesso e la
                                                       fine dell'ultimo periodo di audit_global
                                                       esportato nel WORM
  verumdoc_catena_audit_worm_divergente               1 se l'export dell'audit ha trovato nel
                                                       WORM un segmento con contenuto diverso
                                                       (righe cambiate dopo l'esportazione)
  verumdoc_catena_audit_transazione_aperta_secondi    revisione 2 di R05 (S7): età della
                                                       transazione aperta più vecchia che
                                                       trattiene l'export (0 = nessuna)
  verumdoc_catena_audit_orizzonte_non_verificabile    1 se l'export è sospeso perché non si
                                                       vede l'inizio di alcune transazioni
  verumdoc_catena_worm_rinnovo_in_errore              R12a (VD-C-148): 1 se l'ultimo passo di
                                                       rinnovo del blocco WORM degli oggetti
                                                       registro/ non è riuscito (configurazione
                                                       o errore del passo)
  verumdoc_catena_worm_rinnovo_elementi_in_errore     oggetti registro/ non rinnovati
                                                       nell'ultimo passo (errori isolati, Z5)
  verumdoc_catena_worm_in_scadenza                    oggetti registro/ con blocco WORM entro
                                                       la soglia di rinnovo dopo l'ultimo passo
  verumdoc_documentazione_da_archiviare               R15 (VD-C-177): versioni della
                                                       documentazione del servizio non ancora
                                                       archiviate nel WORM
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge


class MetricheCatena:
    """Metriche del registro su un registro Prometheus (quello del servizio)."""

    def __init__(self, registro: CollectorRegistry | None = None) -> None:
        r = registro or CollectorRegistry()
        self.dimensione = Gauge("verumdoc_catena_dimensione", "Foglie nel registro",
                                registry=r)
        self.checkpoint = Gauge("verumdoc_catena_checkpoint_dimensione",
                                "Dimensione dell'ultimo checkpoint", registry=r)
        self.senza_controfirma = Gauge(
            "verumdoc_catena_checkpoint_senza_controfirma_secondi",
            "Età dell'ultimo checkpoint senza controfirma del testimone (0 = nessuno)",
            registry=r)
        self.allarmi_controfirma = Counter(
            "verumdoc_catena_allarmi_controfirma_totali",
            "Allarmi: checkpoint senza controfirma oltre il limite", registry=r)
        self.rifiutati_aperti = Gauge(
            "verumdoc_catena_checkpoint_rifiutati_aperti",
            "Checkpoint rifiutati dal testimone e non ancora gestiti (completamento sospeso)",
            registry=r)
        self.allarmi_testimone = Gauge(
            "verumdoc_catena_allarmi_testimone_aperti",
            "Allarmi del testimone aperti (rifiuti raggruppati per causa)", registry=r)
        self.testimone_configurato = Gauge(
            "verumdoc_catena_testimone_configurato",
            "1 se il testimone è configurato (obbligatorio in produzione)", registry=r)
        self.testimone_raggiungibile = Gauge(
            "verumdoc_catena_testimone_raggiungibile",
            "1 se l'ultimo contatto con il testimone è riuscito, 0 se non risponde",
            registry=r)
        self.registro_sospeso = Gauge(
            "verumdoc_catena_registro_sospeso",
            "1 se il registro è sospeso (CATENA_REGISTRO_SOSPESO): niente sequenziatore, "
            "checkpoint né testimone", registry=r)
        self.registro_bloccato = Gauge(
            "verumdoc_catena_registro_bloccato",
            "Blocchi persistenti aperti del registro (indietro, divergente, WORM diverso)",
            registry=r)
        self.registro_soglia = Gauge(
            "verumdoc_catena_registro_soglia",
            "Massimo fra ultimo checkpoint nel WORM, fine dei segmenti esportati e "
            "dimensione controfirmata (ultimo controllo di coerenza)", registry=r)
        self.errori = Counter("verumdoc_catena_errori_totali", "Errori dei passi del registro",
                              ["passo"], registry=r)
        self.audit_ritardo = Gauge(
            "verumdoc_catena_audit_ritardo_secondi",
            "Secondi dalla fine dell'ultimo periodo di audit_global esportato nel WORM",
            registry=r)
        self.audit_divergente = Gauge(
            "verumdoc_catena_audit_worm_divergente",
            "1 se un segmento dell'audit nel WORM differisce dalle righe del database",
            registry=r)
        # Revisione 2 di R05 (S7): l'export sospeso da una transazione aperta (o da
        # un orizzonte non verificabile) ha il suo segnale, prima del ritardo
        self.audit_transazione_aperta = Gauge(
            "verumdoc_catena_audit_transazione_aperta_secondi",
            "Età in secondi della transazione aperta che trattiene l'export dell'audit "
            "(0 = nessuna)", registry=r)
        self.audit_orizzonte_non_verificabile = Gauge(
            "verumdoc_catena_audit_orizzonte_non_verificabile",
            "1 se l'export dell'audit è sospeso: inizio di transazioni non visibile",
            registry=r)
        # R12a (VD-C-148): rinnovo del blocco WORM degli oggetti registro/
        self.worm_rinnovo_in_errore = Gauge(
            "verumdoc_catena_worm_rinnovo_in_errore",
            "1 se l'ultimo passo di rinnovo del blocco WORM del registro non è riuscito",
            registry=r)
        self.worm_rinnovo_elementi_in_errore = Gauge(
            "verumdoc_catena_worm_rinnovo_elementi_in_errore",
            "Oggetti registro/ non rinnovati nell'ultimo passo (errori isolati)", registry=r)
        self.worm_in_scadenza = Gauge(
            "verumdoc_catena_worm_in_scadenza",
            "Oggetti registro/ con il blocco WORM entro la soglia di rinnovo", registry=r)
        # R13a (VD-C-068): documenti del conservatore e ancoraggio dei checkpoint
        self.firme_da_firmare = Gauge(
            "verumdoc_firme_documenti_da_firmare",
            "Documenti del conservatore in attesa di sigillo (IPdA, RdV, manuale, piano)",
            registry=r)
        # R16 (VD-C-166): firme personali del responsabile respinte dalla riverifica
        self.firme_responsabile_respinte = Gauge(
            "verumdoc_firme_responsabile_respinte",
            "Firme personali del responsabile RESPINTE dalla riverifica prima del WORM",
            registry=r)
        self.firme_non_firmati = Gauge(
            "verumdoc_firme_documenti_non_firmati",
            "Documenti del conservatore dichiarati NON_FIRMATO (senza fornitore qualificato)",
            registry=r)
        self.firme_origine_diversa = Counter(
            "verumdoc_firme_documenti_origine_diversa",
            "Documenti non sigillati perché l'oggetto nello storage non ha l'impronta attesa",
            registry=r)
        self.firme_bloccate_conservazione = Gauge(
            "verumdoc_firme_conservazione_bloccata",
            "PdA che non vanno a CONSERVATO per l'IPdA non firmato (FIRMA_SENZA_FORNITORE=BLOCCA)",
            registry=r)
        self.firme_in_scadenza = Gauge(
            "verumdoc_firme_documenti_in_scadenza",
            "Documenti firmati da rimarcare (certificato, TSA o algoritmo in scadenza)",
            registry=r)
        self.firme_in_errore = Gauge(
            "verumdoc_firme_documenti_in_errore",
            "Documenti del conservatore in ERRORE dopo FIRMA_TENTATIVI_MASSIMI errori",
            registry=r)
        self.firme_rinnovo_in_errore = Gauge(
            "verumdoc_firme_rinnovo_in_errore",
            "Documenti non rimarcati nell'ultimo passo di rimarcatura", registry=r)
        # R14 (VD-C-165): arretrato dei sigilli degli IPdA dei PdA conservati prima di R13a
        self.firme_arretrato_completato = Gauge(
            "verumdoc_firme_arretrato_ipda_completato",
            "1 se l'ultimo passaggio del censimento degli IPdA storici è terminato",
            registry=r)
        self.firme_arretrato_inseriti = Counter(
            "verumdoc_firme_arretrato_ipda_inseriti",
            "IPdA storici aggiunti ai documenti da sigillare", registry=r)
        self.firme_arretrato_scartati = Counter(
            "verumdoc_firme_arretrato_ipda_scartati",
            "PdA storici con manifest illeggibile o di impronta diversa (non accodati)",
            registry=r)
        self.checkpoint_marca_eta = Gauge(
            "verumdoc_catena_checkpoint_marca_eta_secondi",
            "Secondi dall'ultima marca temporale di un checkpoint (-1 = mai marcato)",
            registry=r)
        # R15 (VD-C-177): versioni della documentazione del servizio non ancora nel WORM
        self.documentazione_da_archiviare = Gauge(
            "verumdoc_documentazione_da_archiviare",
            "Versioni della documentazione del servizio non ancora archiviate nel WORM",
            registry=r)


_metriche: MetricheCatena | None = None


def installa(registro: CollectorRegistry | None) -> MetricheCatena:
    """Registra le metriche sul registro del servizio (una volta per processo)."""
    global _metriche
    if _metriche is None:
        _metriche = MetricheCatena(registro)
    return _metriche


def metriche() -> MetricheCatena:
    """Metriche correnti (su un registro privato se il servizio non le ha installate)."""
    return installa(None)
