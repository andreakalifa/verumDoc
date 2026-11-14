<!-- File generato da tools/docs/genera_cataloghi.py: non modificare a mano (REGOLE_SVILUPPO §28). -->

# Catalogo delle metriche e degli allarmi

113 metriche registrate nel codice, 57 allarmi in `infra/observability/regole/`; cruscotto in `infra/observability/grafana/cruscotti/`.

## Metriche

| Metrica | Tipo | Descrizione | Dove | Allarmi |
|---|---|---|---|---|
| `verumdoc_catena_allarmi_controfirma_totali` | Counter | Allarmi: checkpoint senza controfirma oltre il limite | `services/hashchain/app/registro/metriche.py` |  |
| `verumdoc_catena_allarmi_testimone_aperti` | Gauge | Allarmi del testimone aperti (rifiuti raggruppati per causa) | `services/hashchain/app/registro/metriche.py` | AllarmiTestimoneAperti |
| `verumdoc_catena_audit_orizzonte_non_verificabile` | Gauge | 1 se l | `services/hashchain/app/registro/metriche.py` | AuditOrizzonteNonVerificabile |
| `verumdoc_catena_audit_ritardo_secondi` | Gauge | Secondi dalla fine dell | `services/hashchain/app/registro/metriche.py` | AuditNonEsportato |
| `verumdoc_catena_audit_transazione_aperta_secondi` | Gauge | Età in secondi della transazione aperta che trattiene l | `services/hashchain/app/registro/metriche.py` | AuditExportTrattenuto |
| `verumdoc_catena_audit_worm_divergente` | Gauge | 1 se un segmento dell | `services/hashchain/app/registro/metriche.py` | AuditWormDivergente |
| `verumdoc_catena_checkpoint_dimensione` | Gauge | Dimensione dell | `services/hashchain/app/registro/metriche.py` | CatenaFerma |
| `verumdoc_catena_checkpoint_marca_eta_secondi` | Gauge | Secondi dall | `services/hashchain/app/registro/metriche.py` | CheckpointNonMarcato |
| `verumdoc_catena_checkpoint_rifiutati_aperti` | Gauge | Checkpoint rifiutati dal testimone e non ancora gestiti (completamento sospeso) | `services/hashchain/app/registro/metriche.py` | CheckpointRifiutatoDalTestimone |
| `verumdoc_catena_checkpoint_senza_controfirma_secondi` | Gauge | Età dell | `services/hashchain/app/registro/metriche.py` | CheckpointSenzaControfirma |
| `verumdoc_catena_dimensione` | Gauge | Foglie nel registro | `services/hashchain/app/registro/metriche.py` | CatenaFerma |
| `verumdoc_catena_errori_totali` | Counter | Errori dei passi del registro | `services/hashchain/app/registro/metriche.py` | CatenaErroriRegistro |
| `verumdoc_catena_registro_bloccato` | Gauge | Blocchi persistenti aperti del registro (indietro, divergente, WORM diverso) | `services/hashchain/app/registro/metriche.py` | RegistroBloccato |
| `verumdoc_catena_registro_soglia` | Gauge | Massimo fra ultimo checkpoint nel WORM, fine dei segmenti esportati e | `services/hashchain/app/registro/metriche.py` |  |
| `verumdoc_catena_registro_sospeso` | Gauge | 1 se il registro è sospeso (CATENA_REGISTRO_SOSPESO): niente sequenziatore, | `services/hashchain/app/registro/metriche.py` | RegistroSospeso |
| `verumdoc_catena_testimone_configurato` | Gauge | 1 se il testimone è configurato (obbligatorio in produzione) | `services/hashchain/app/registro/metriche.py` | TestimoneNonConfigurato |
| `verumdoc_catena_testimone_raggiungibile` | Gauge | 1 se l | `services/hashchain/app/registro/metriche.py` | TestimoneNonRisponde |
| `verumdoc_catena_worm_in_scadenza` | Gauge | Oggetti registro/ con il blocco WORM entro la soglia di rinnovo | `services/hashchain/app/registro/metriche.py` | RegistroWormInScadenza |
| `verumdoc_catena_worm_rinnovo_elementi_in_errore` | Gauge | Oggetti registro/ non rinnovati nell | `services/hashchain/app/registro/metriche.py` | RegistroWormRinnovoInErrore |
| `verumdoc_catena_worm_rinnovo_in_errore` | Gauge | 1 se l | `services/hashchain/app/registro/metriche.py` | RegistroWormRinnovoInErrore |
| `verumdoc_ciclo_vita_oggetti_non_determinabili` | Gauge | PdA di scarti con eccezioni chiusi con versioni rimaste non determinabili | `services/ciclo-vita/app/application/metriche.py` | ScartoNonDeterminabile |
| `verumdoc_ciclo_vita_passi_periodici_in_errore` | Gauge | Passi del lavoro periodico con l | `services/ciclo-vita/app/application/metriche.py` | PassoPeriodicoInErrore |
| `verumdoc_ciclo_vita_passo_periodico_eta_secondi` | Gauge | Secondi dall | `services/ciclo-vita/app/application/metriche.py` | PassoPeriodicoFermo |
| `verumdoc_ciclo_vita_passo_periodico_in_errore` | Gauge | 1 se l | `services/ciclo-vita/app/application/metriche.py` | PassoPeriodicoSingoloInErrore |
| `verumdoc_ciclo_vita_passo_periodico_intervallo_secondi` | Gauge | Intervallo configurato del passo periodico | `services/ciclo-vita/app/application/metriche.py` | PassoPeriodicoFermo |
| `verumdoc_ciclo_vita_pda_eliminazione_incompleta` | Gauge | PdA con cancellazione dello scarto iniziata e non completata | `services/ciclo-vita/app/application/metriche.py` | ScartoEliminazioneIncompleta |
| `verumdoc_ciclo_vita_pda_permanenti_senza_oggetti` | Gauge | PdA permanenti senza oggetti nel WORM (fuori dal rinnovo) | `services/ciclo-vita/app/application/metriche.py` | PdaPermanenteSenzaOggetti |
| `verumdoc_ciclo_vita_pratiche_lavoro_in_errore` | Gauge | Pratiche con lavoro e tentativi falliti oltre la soglia d | `services/ciclo-vita/app/application/metriche.py` | PraticaLavoroInErrore |
| `verumdoc_ciclo_vita_pratiche_sospese` | Gauge | Pratiche sospese dopo i tentativi esauriti (LC-409-TENTATIVI-ESAURITI): | `services/ciclo-vita/app/application/metriche.py` | PraticaSospesa |
| `verumdoc_ciclo_vita_puntuali_autorizzazioni_non_verificate` | Gauge | Pratiche di annullamento e rettifica bloccate dal lavoratore: catena | `services/ciclo-vita/app/application/metriche.py` | PuntualeAutorizzazioniNonVerificate |
| `verumdoc_ciclo_vita_scarti_autorizzazioni_non_verificate` | Gauge | Pratiche di scarto bloccate dal lavoratore: catena delle | `services/ciclo-vita/app/application/metriche.py` | ScartoAutorizzazioniNonVerificate |
| `verumdoc_contatori_delta_in_attesa` | Gauge | Variazioni dei contatori per ente non consolidate | `libs/shared/db/metriche.py` |  |
| `verumdoc_documentazione_da_archiviare` | Gauge | Versioni della documentazione del servizio non ancora archiviate nel WORM | `services/hashchain/app/registro/metriche.py` | DocumentazioneNonArchiviata |
| `verumdoc_documentazione_generazioni_totali` | Counter | Generazioni della documentazione del servizio per esito | `services/dashboard-api/app/routers/documentazione_servizio.py` | DocumentazioneNonGenerata |
| `verumdoc_firme_arretrato_ipda_completato` | Gauge | 1 se l | `services/hashchain/app/registro/metriche.py` |  |
| `verumdoc_firme_arretrato_ipda_inseriti` | Counter | IPdA storici aggiunti ai documenti da sigillare | `services/hashchain/app/registro/metriche.py` |  |
| `verumdoc_firme_arretrato_ipda_scartati` | Counter | PdA storici con manifest illeggibile o di impronta diversa (non accodati) | `services/hashchain/app/registro/metriche.py` | ArretratoIpdaScartati |
| `verumdoc_firme_conservazione_bloccata` | Gauge | PdA che non vanno a CONSERVATO per l | `services/hashchain/app/registro/metriche.py` | ConservazioneFermaPerFirma |
| `verumdoc_firme_documenti_da_firmare` | Gauge | Documenti del conservatore in attesa di sigillo (IPdA, RdV, manuale, piano) | `services/hashchain/app/registro/metriche.py` | DocumentiDaFirmareInAccumulo |
| `verumdoc_firme_documenti_in_errore` | Gauge | Documenti del conservatore in ERRORE dopo FIRMA_TENTATIVI_MASSIMI errori | `services/hashchain/app/registro/metriche.py` | DocumentiDaSigillareInErrore |
| `verumdoc_firme_documenti_in_scadenza` | Gauge | Documenti firmati da rimarcare (certificato, TSA o algoritmo in scadenza) | `services/hashchain/app/registro/metriche.py` | DocumentiInScadenzaNonRimarcati |
| `verumdoc_firme_documenti_non_firmati` | Gauge | Documenti del conservatore dichiarati NON_FIRMATO (senza fornitore qualificato) | `services/hashchain/app/registro/metriche.py` | DocumentiNonFirmati |
| `verumdoc_firme_documenti_origine_diversa` | Counter | Documenti non sigillati perché l | `services/hashchain/app/registro/metriche.py` | FirmaOrigineDiversa |
| `verumdoc_firme_responsabile_respinte` | Gauge | Firme personali del responsabile RESPINTE dalla riverifica prima del WORM | `services/hashchain/app/registro/metriche.py` | FirmeResponsabileRespinte |
| `verumdoc_firme_rinnovo_in_errore` | Gauge | Documenti non rimarcati nell | `services/hashchain/app/registro/metriche.py` | RimarcaturaInErrore |
| `verumdoc_gateway_disco_temporaneo_byte_budget` | Gauge | Budget di byte su /tmp della replica (DISCO_TEMPORANEO_BUDGET_MB) | `services/gateway/app/services/corpo.py` |  |
| `verumdoc_gateway_disco_temporaneo_byte_prenotati` | Gauge | Byte prenotati su /tmp dalle richieste in corso (multipart e PDND) | `services/gateway/app/services/corpo.py` |  |
| `verumdoc_gateway_multipart_in_corso` | Gauge | Versamenti multipart REST in corso nella replica | `services/gateway/app/services/corpo.py` |  |
| `verumdoc_http_durata_secondi` | Histogram | Durata delle richieste HTTP in secondi | `libs/shared/observability/metriche.py` |  |
| `verumdoc_http_richieste_in_corso` | Gauge | Richieste HTTP in corso | `libs/shared/observability/metriche.py` |  |
| `verumdoc_http_richieste_totali` | Counter | Richieste HTTP servite | `libs/shared/observability/metriche.py` |  |
| `verumdoc_outbox_arretrato` | Gauge | Eventi dell | `libs/shared/db/metriche.py` |  |
| `verumdoc_outbox_bloccati` | Gauge | Chiavi dell | `libs/shared/db/metriche.py` | OutboxEventoBloccato |
| `verumdoc_outbox_chiavi_ferme` | Gauge | Chiavi dell | `libs/shared/db/metriche.py` | OutboxChiaviFerme |
| `verumdoc_outbox_errori` | Gauge | Invii dell | `libs/shared/db/metriche.py` |  |
| `verumdoc_outbox_errori_evento` | Gauge | Invii falliti per un errore del singolo evento | `libs/shared/db/metriche.py` |  |
| `verumdoc_outbox_errori_kafka` | Gauge | Invii falliti perché Kafka non è raggiungibile | `libs/shared/db/metriche.py` |  |
| `verumdoc_outbox_eta_piu_vecchio_secondi` | Gauge | Età in secondi dell | `libs/shared/db/metriche.py` | OutboxEventiInAttesa |
| `verumdoc_outbox_eventi_bloccati` | Gauge | Eventi dell | `libs/shared/db/metriche.py` |  |
| `verumdoc_outbox_kafka_cortocircuito` | Gauge | 1 se il cortocircuito verso Kafka è aperto | `libs/shared/db/metriche.py` | OutboxKafkaNonDisponibile |
| `verumdoc_outbox_pubblicati` | Gauge | Eventi dell | `libs/shared/db/metriche.py` |  |
| `verumdoc_outbox_topic_sospensioni` | Gauge | Sospensioni di topic dell | `libs/shared/db/metriche.py` |  |
| `verumdoc_outbox_topic_sospesi` | Gauge | Topic dell | `libs/shared/db/metriche.py` | OutboxTopicSospeso |
| `verumdoc_parametri_scartati` | Gauge | Righe di parametri ignorate (livello non ammesso o valore | `libs/shared/db/metriche.py` |  |
| `verumdoc_partizioni_bloccate` | Gauge | Partizioni scadute che non si possono staccare | `libs/shared/db/metriche.py` |  |
| `verumdoc_partizioni_future` | Gauge | Periodi futuri con la partizione già pronta | `libs/shared/db/metriche.py` |  |
| `verumdoc_partizioni_staccate` | Gauge | Partizioni staccate ed eliminate da questo processo | `libs/shared/db/metriche.py` |  |
| `verumdoc_pec_canali_attivi` | Gauge | Canali PEC attivi in console | `services/notification/app/application/metriche_posta.py` |  |
| `verumdoc_pec_canali_non_utilizzabili` | Gauge | Canali PEC attivi con problemi dichiarati dal lavoratore della posta | `services/notification/app/application/metriche_posta.py` | PecCanaleNonUtilizzabile |
| `verumdoc_pec_messaggi_non_partiti_24h` | Gauge | PEC NON_PARTITO nelle ultime 24 ore | `services/notification/app/application/metriche_posta.py` |  |
| `verumdoc_pec_messaggi_senza_accettazione` | Gauge | PEC inviate da oltre un | `services/notification/app/application/metriche_posta.py` | PecSenzaRicevuta |
| `verumdoc_pec_messaggi_senza_consegna` | Gauge | PEC accettate da oltre PEC_CONSEGNA_ATTESA_ORE senza ricevuta di consegna | `services/notification/app/application/metriche_posta.py` | PecSenzaRicevuta |
| `verumdoc_pec_ricevute_non_autentiche_24h` | Gauge | Ricevute PEC non autentiche nelle ultime 24 ore | `services/notification/app/application/metriche_posta.py` | PecRicevuteNonAutentiche |
| `verumdoc_pec_stato_eta_secondi` | Gauge | Secondi dall | `services/notification/app/application/metriche_posta.py` | PecLavoratoreFermo |
| `verumdoc_posta_coda_eta_secondi` | Gauge | Età in secondi del messaggio di posta in coda più vecchio | `services/notification/app/application/metriche_posta.py` | PostaCodaFerma |
| `verumdoc_posta_messaggi_in_coda` | Gauge | Messaggi di posta in coda (al più 100.000) | `services/notification/app/application/metriche_posta.py` |  |
| `verumdoc_posta_messaggi_non_partiti_24h` | Gauge | Messaggi di posta NON_PARTITA nelle ultime 24 ore | `services/notification/app/application/metriche_posta.py` | PostaNonPartita |
| `verumdoc_programmate_avvisi_non_inviati` | Gauge | E-mail delle modifiche programmate non | `libs/shared/db/metriche.py` |  |
| `verumdoc_sftp_accessi_totali` | Counter | Tentativi di accesso SFTP per esito | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_account_esaminati_totali` | Counter | Account segnalati la cui area è stata esaminata (R03 f2: solo quelli con | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_byte_caricati_totali` | Counter | Byte inviati al gateway nelle sessioni di caricamento | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_byte_ricevuti_totali` | Counter | Byte scritti dai clienti via SFTP | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_cartelle_totali` | Counter | Cartelle lavorate per esito | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_durata_lavorazione_secondi` | Histogram | Durata della lavorazione di una cartella | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_giri_scansione_totali` | Counter | Giri di scansione del lavoratore per esito | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_lavorazioni_in_corso` | Gauge | Cartelle in lavorazione in questa replica | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_operazioni_negate_totali` | Counter | Operazioni SFTP rifiutate (permesso, quota, nome non valido) | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_sftp_sessioni_attive` | Gauge | Sessioni SFTP aperte | `services/sftp-ingest/app/metriche.py` |  |
| `verumdoc_testimone_allarmi_totali` | Counter | Incoerenze del registro rilevate | `services/testimone/app/osservabilita.py` | TestimoneAllarmi |
| `verumdoc_testimone_compromesso` | Gauge | 1 se il testimone è bloccato per un fork del registro | `services/testimone/app/osservabilita.py` | TestimoneRegistroCompromesso |
| `verumdoc_testimone_controfirme_totali` | Counter | Checkpoint controfirmati | `services/testimone/app/osservabilita.py` |  |
| `verumdoc_testimone_dimensione_controfirmata` | Gauge | Dimensione dell | `services/testimone/app/osservabilita.py` |  |
| `verumdoc_testimone_rifiuti_totali` | Counter | Richieste di controfirma rifiutate, per motivo | `services/testimone/app/osservabilita.py` |  |
| `verumdoc_testimone_ultima_controfirma_secondi` | Gauge | Istante (epoch) dell | `services/testimone/app/osservabilita.py` | TestimoneSenzaControfirmeRecenti |
| `verumdoc_validation_byte_letti_totali` | Counter | Byte letti dallo storage per ricalcolare le impronte | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_concorrenza_massima` | Gauge | Limite di elaborazioni parallele configurato | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_conferme_offset_totali` | Counter | Conferme d | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_dlq_non_registrata_totali` | Counter | Volte in cui DLQ o stato d | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_durata_elaborazione_secondi` | Histogram | Durata dell | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_elaborazioni_in_corso` | Gauge | Messaggi in elaborazione in questa replica | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_messaggi_non_confermati` | Gauge | Messaggi letti il cui offset non è ancora confermato | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_messaggi_totali` | Counter | Messaggi pdv.received chiusi per esito (elaborato, dlq) | `services/validation/app/services/metriche.py` |  |
| `verumdoc_validation_pdv_totali` | Counter | PdV verificati per esito (VALIDATO, RIFIUTATO, RIPUBBLICATO, SALTATO) | `services/validation/app/services/metriche.py` |  |
| `verumdoc_verifica_anomalie` | Counter | Oggetti in anomalia d | `services/hashchain/app/verifica_continua/metriche.py` | VerificaNuoveAnomalie |
| `verumdoc_verifica_anomalie_aperte` | Gauge | PdA con un | `services/hashchain/app/verifica_continua/metriche.py` | VerificaAnomalieAperte |
| `verumdoc_verifica_attesa_budget_secondi` | Counter | Secondi di attesa imposti dai budget di lettura | `services/hashchain/app/verifica_continua/metriche.py` |  |
| `verumdoc_verifica_byte_letti` | Counter | Byte riletti da storage | `services/hashchain/app/verifica_continua/metriche.py` |  |
| `verumdoc_verifica_errori_lettura_ripetuti` | Counter | PdA con errori di lettura oltre la soglia | `services/hashchain/app/verifica_continua/metriche.py` |  |
| `verumdoc_verifica_lease_persi` | Counter | Esiti scartati perché il lease era scaduto | `services/hashchain/app/verifica_continua/metriche.py` |  |
| `verumdoc_verifica_oggetti` | Counter | Oggetti riletti da storage per esito | `services/hashchain/app/verifica_continua/metriche.py` |  |
| `verumdoc_verifica_pda` | Counter | PdA con verifica conclusa per esito | `services/hashchain/app/verifica_continua/metriche.py` |  |
| `verumdoc_verifica_ritardo_secondi` | Gauge | Attesa in secondi del PdA più in ritardo sulla rotazione | `services/hashchain/app/verifica_continua/metriche.py` | VerificaInRitardo |
| `verumdoc_verifica_ultimo_giro_timestamp` | Gauge | Istante (epoch) dell | `services/hashchain/app/verifica_continua/metriche.py` | VerificaFerma |

## Allarmi

| Allarme | Gravità | Attesa | Sintesi | File |
|---|---|---|---|---|
| `AllarmiTestimoneAperti` | pagina | 0m | {{ $value }} allarmi del testimone aperti (rifiuti raggruppati per causa) | verumdoc-r04.yml |
| `ArretratoIpdaScartati` | pagina | 0m | IPdA storici non accodati al sigillo: manifest diverso o illeggibile | verumdoc-r14.yml |
| `AuditExportTrattenuto` | avviso | 5m | Export dell'audit trattenuto da una transazione aperta da oltre 30 minuti | verumdoc-r05.yml |
| `AuditNonEsportato` | pagina | 15m | audit_global non esportato nel WORM da oltre 2 ore | verumdoc-r05.yml |
| `AuditOrizzonteNonVerificabile` | pagina | 15m | Export dell'audit sospeso: inizio di transazioni non visibile | verumdoc-r05.yml |
| `AuditWormDivergente` | pagina | 0m | Un segmento dell'audit nel WORM differisce dalle righe del database | verumdoc-r05.yml |
| `CatenaErroriRegistro` | avviso | 0m | Errori nei passi del registro delle impronte negli ultimi 15 minuti | verumdoc-r04.yml |
| `CatenaFerma` | pagina | 10m | Il registro cresce ma nessun checkpoint nuovo da 30 minuti | verumdoc-r04.yml |
| `CheckpointNonMarcato` | avviso | 1h | Nessuna marca temporale dei checkpoint da oltre un giorno | verumdoc-r13.yml |
| `CheckpointRifiutatoDalTestimone` | pagina | 0m | Il testimone ha rifiutato un checkpoint del registro: conservazione sospesa | verumdoc-r04.yml |
| `CheckpointSenzaControfirma` | pagina | 2m | Checkpoint del registro senza controfirma del testimone da oltre 10 minuti (K3) | verumdoc-r04.yml |
| `ConservazioneFermaPerFirma` | pagina | 15m | PdA fermi: IPdA non firmato con FIRMA_SENZA_FORNITORE=BLOCCA | verumdoc-r13.yml |
| `DocumentazioneNonArchiviata` | avviso | 2h | Versioni della documentazione non archiviate nel WORM da oltre 2 ore | verumdoc-r15.yml |
| `DocumentazioneNonGenerata` | avviso | 0m | Generazione della documentazione del servizio non riuscita | verumdoc-r15.yml |
| `DocumentiDaFirmareInAccumulo` | avviso | 30m | Documenti in attesa di sigillo in accumulo | verumdoc-r13.yml |
| `DocumentiDaSigillareInErrore` | pagina | 0m | Documenti del conservatore in ERRORE (sigillo non riuscito) | verumdoc-r13.yml |
| `DocumentiInScadenzaNonRimarcati` | avviso | 3d | Documenti firmati entro il preavviso di scadenza dopo tre giorni | verumdoc-r13.yml |
| `DocumentiNonFirmati` | avviso | 1h | Documenti del conservatore dichiarati NON_FIRMATO | verumdoc-r13.yml |
| `FirmaOrigineDiversa` | pagina | 0m | Documento da sigillare con impronta diversa nello storage | verumdoc-r13.yml |
| `FirmeResponsabileRespinte` | avviso | 5m | Firma personale del responsabile respinta prima del WORM | verumdoc-r16.yml |
| `OutboxChiaviFerme` | avviso | 15m | Chiavi dell'outbox in riprova da oltre 15 minuti ({{ $labels.origine }}) | verumdoc-r04.yml |
| `OutboxEventiInAttesa` | avviso | 5m | Eventi in outbox non pubblicati da oltre 5 minuti ({{ $labels.origine }}) | verumdoc-r04.yml |
| `OutboxEventoBloccato` | pagina | 0m | Eventi dell'outbox bloccati dopo i ritentativi ({{ $labels.origine }}) | verumdoc-r04.yml |
| `OutboxKafkaNonDisponibile` | pagina | 5m | Kafka non raggiungibile dal relay dell'outbox ({{ $labels.origine }}) | verumdoc-r04.yml |
| `OutboxTopicSospeso` | pagina | 2m | Topic dell'outbox sospeso per un errore del topic o dei permessi ({{ $labels.origine }}) | verumdoc-r04.yml |
| `PassoPeriodicoFermo` | pagina | 15m | Un passo automatico del ciclo di vita non gira da oltre due intervalli | verumdoc-r12.yml |
| `PassoPeriodicoInErrore` | pagina | 30m | Un passo automatico del ciclo di vita non riesce (es. rinnovo dei blocchi WORM) | verumdoc-r09.yml |
| `PassoPeriodicoSingoloInErrore` | avviso | 30m | Il passo periodico {{ $labels.passo }} ha l'ultimo giro in errore | verumdoc-r12.yml |
| `PdaPermanenteSenzaOggetti` | pagina | 0m | PdA permanenti senza oggetti nello storage WORM | verumdoc-r09.yml |
| `PecCanaleNonUtilizzabile` | pagina | 15m | Canale PEC attivo non utilizzabile | verumdoc-r14.yml |
| `PecLavoratoreFermo` | pagina | 5m | Stato dei canali PEC non aggiornato da oltre 15 minuti | verumdoc-r14.yml |
| `PecRicevuteNonAutentiche` | pagina | 0m | Ricevute PEC non autentiche nelle ultime 24 ore | verumdoc-r14.yml |
| `PecSenzaRicevuta` | avviso | 30m | PEC senza ricevuta di accettazione o di consegna | verumdoc-r14.yml |
| `PostaCodaFerma` | pagina | 10m | Posta in coda da oltre 30 minuti | verumdoc-r14.yml |
| `PostaNonPartita` | avviso | 15m | Messaggi di posta NON_PARTITA nelle ultime 24 ore | verumdoc-r14.yml |
| `PraticaLavoroInErrore` | avviso | 30m | Pratiche del ciclo di vita con il lavoro che fallisce ripetutamente | verumdoc-r05.yml |
| `PraticaSospesa` | pagina | 0m | Pratica del ciclo di vita sospesa dopo i tentativi esauriti | verumdoc-r14.yml |
| `PuntualeAutorizzazioniNonVerificate` | pagina | 0m | Annullamento o rettifica bloccati: richiesta non verificata dal lavoratore | verumdoc-r06.yml |
| `RegistroBloccato` | pagina | 0m | Registro delle impronte bloccato: niente sequenziatore né checkpoint | verumdoc-r04.yml |
| `RegistroSospeso` | pagina | 30m | Registro delle impronte sospeso da oltre 30 minuti (CATENA_REGISTRO_SOSPESO) | verumdoc-r04.yml |
| `RegistroWormInScadenza` | avviso | 2d | Oggetti del registro con il blocco WORM entro la soglia di rinnovo | verumdoc-r12.yml |
| `RegistroWormRinnovoInErrore` | pagina | 1h | Il rinnovo del blocco WORM degli oggetti del registro non riesce | verumdoc-r12.yml |
| `RimarcaturaInErrore` | pagina | 2h | La rimarcatura dei documenti firmati non riesce | verumdoc-r13.yml |
| `ScartoAutorizzazioniNonVerificate` | pagina | 0m | Scarto bloccato: autorizzazioni non verificate dal lavoratore | verumdoc-r05.yml |
| `ScartoEliminazioneIncompleta` | pagina | 0m | PdA con cancellazione dello scarto iniziata e non completata | verumdoc-r05.yml |
| `ScartoNonDeterminabile` | avviso | 1h | Scarto con versioni rimaste non determinabili | verumdoc-r14.yml |
| `TestimoneAllarmi` | pagina | 0m | Allarmi del testimone negli ultimi 15 minuti | verumdoc-r04.yml |
| `TestimoneNonConfigurato` | pagina | 0m | Registro delle impronte senza testimone configurato | verumdoc-r04.yml |
| `TestimoneNonRisponde` | pagina | 5m | Il testimone non risponde al registro da oltre 5 minuti | verumdoc-r04.yml |
| `TestimoneRegistroCompromesso` | pagina | 0m | Il testimone ha rilevato una biforcazione del registro (fork) | verumdoc-r04.yml |
| `TestimoneSenzaControfirmeRecenti` | avviso | 5m | Nessuna controfirma del testimone da oltre 10 minuti (CATENA_CONTROFIRMA_MAX_SECONDI) | verumdoc-r04.yml |
| `VerificaAnomalieAperte` | pagina | 0m | Anomalie d'integrità aperte nella verifica continua | verumdoc-r04.yml |
| `VerificaAuditFallita` | pagina | 0m | La verifica giornaliera dell'audit nel WORM è fallita (codice d'uscita diverso da 0) | verumdoc-r06.yml |
| `VerificaAuditNonEseguita` | avviso | 30m | Nessuna verifica dell'audit riuscita da oltre 26 ore | verumdoc-r06.yml |
| `VerificaFerma` | avviso | 5m | Nessun giro della verifica continua da oltre 10 minuti | verumdoc-r04.yml |
| `VerificaInRitardo` | avviso | 1h | Verifica continua in ritardo di oltre un giorno sul ciclo (capacità insufficiente) | verumdoc-r04.yml |
| `VerificaNuoveAnomalie` | pagina | 0m | Nuove anomalie d'integrità nell'ultima ora | verumdoc-r04.yml |
