# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Metriche Prometheus del canale SFTP (esposte su GET /metrics insieme a quelle HTTP).

Servizio: sftp-ingest. Etichette a cardinalità limitata: nessun identificativo
di ente, account o cartella nelle metriche.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class MetricheSftp:
    def __init__(self, registro: CollectorRegistry | None = None) -> None:
        self.registro = registro or CollectorRegistry()
        r = self.registro
        self.accessi = Counter("verumdoc_sftp_accessi_totali",
                               "Tentativi di accesso SFTP per esito", ["esito"], registry=r)
        self.sessioni = Gauge("verumdoc_sftp_sessioni_attive", "Sessioni SFTP aperte",
                              registry=r)
        self.byte_ricevuti = Counter("verumdoc_sftp_byte_ricevuti_totali",
                                     "Byte scritti dai clienti via SFTP", registry=r)
        self.operazioni_negate = Counter(
            "verumdoc_sftp_operazioni_negate_totali",
            "Operazioni SFTP rifiutate (permesso, quota, nome non valido)", ["motivo"],
            registry=r)
        self.cartelle = Counter("verumdoc_sftp_cartelle_totali",
                                "Cartelle lavorate per esito", ["esito"], registry=r)
        self.byte_caricati = Counter("verumdoc_sftp_byte_caricati_totali",
                                     "Byte inviati al gateway nelle sessioni di caricamento",
                                     registry=r)
        self.lavorazioni_in_corso = Gauge("verumdoc_sftp_lavorazioni_in_corso",
                                          "Cartelle in lavorazione in questa replica",
                                          registry=r)
        self.durata = Histogram("verumdoc_sftp_durata_lavorazione_secondi",
                                "Durata della lavorazione di una cartella",
                                buckets=(1, 5, 15, 60, 300, 900, 3600), registry=r)
        self.giri = Counter("verumdoc_sftp_giri_scansione_totali",
                            "Giri di scansione del lavoratore per esito", ["esito"], registry=r)
        self.account_esaminati = Counter(
            "verumdoc_sftp_account_esaminati_totali",
            "Account segnalati la cui area è stata esaminata (R03 f2: solo quelli con "
            "attività, non tutti a ogni giro)", registry=r)
