<!-- File generato da tools/docs/genera_cataloghi.py: non modificare a mano (REGOLE_SVILUPPO §28). -->

# Catalogo dei topic Kafka

24 topic da `infra/kafka/topics.yaml` (unica fonte, anche dei permessi IAM/ACL per servizio).

| Topic | Partizioni | Retention | Produttori | Consumatori | Chiave |
|---|---|---|---|---|---|
| `aip.conserved` | 48 | 7 giorni | hashchain | notification | aip_id |
| `aip.error` | 6 | 7 giorni | aip-builder, dashboard-api |  |  |
| `caricamento.completato` | 12 | 3 giorni | gateway, dashboard-api | notification | caricamento_id |
| `caricamento.errore` | 6 | 3 giorni | gateway, dashboard-api | notification | caricamento_id |
| `catena.checkpoint` | 3 | 30 giorni | hashchain | notification | dimensione |
| `ciclo_vita.avviso` | 3 | 7 giorni | ciclo-vita | notification | ente_e_chiave |
| `idempotenza.chiave_omessa` | 3 | 7 giorni | onboarding | notification | ente_e_chiave |
| `integrita.anomalia` | 3 | 90 giorni | hashchain | notification | aip_id |
| `lifecycle.event` | 6 | 30 giorni | onboarding, distribution, aip-builder |  |  |
| `notification.dlq` | 3 | 30 giorni | notification | dashboard-api |  |
| `notification.send` | 6 | 3 giorni | dashboard-api, onboarding, gateway | notification |  |
| `pdd.pronto` | 6 | 7 giorni | distribution, dashboard-api | notification | pdd_id |
| `pdv.invalid` | 12 | 7 giorni | validation, dashboard-api | notification | pdv_id |
| `pdv.received` | 48 | 3 giorni | gateway, dashboard-api | validation, notification | pdv_id |
| `pdv.received.dlq` | 3 | 30 giorni | validation | dashboard-api |  |
| `pdv.validated` | 48 | 3 giorni | validation, dashboard-api | aip-builder, notification | pdv_id |
| `pdv.validated.dlq` | 3 | 30 giorni | aip-builder | dashboard-api |  |
| `pratica.cambio_stato` | 6 | 3 giorni | ciclo-vita | notification | pratica_id |
| `quota.soglia_raggiunta` | 3 | 7 giorni | gateway | notification | ente_e_chiave |
| `riversamento.pronto` | 3 | 3 giorni | ciclo-vita | notification | pratica_id |
| `scarto.eseguito` | 3 | 3 giorni | ciclo-vita | notification | pratica_id |
| `versamento.annullato` | 6 | 3 giorni | ciclo-vita | notification | pdv_id |
| `versamento.rettificato` | 6 | 3 giorni | ciclo-vita | notification | pdv_id |
| `versamento.staging_scaduto` | 6 | 7 giorni | gateway, dashboard-api | notification | pdv_id |
