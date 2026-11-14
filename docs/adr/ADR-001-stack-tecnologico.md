# ADR-001 — Stack Tecnologico

**Data**: 2026-03
**Stato**: **Superato in parte** (20/10/2026) — database e code di lavoro sostituiti dalla decisione **VD-P-00** (registro `docs/registro/decisioni.md`, 20/03/2026; REGOLE_SVILUPPO §16.1). Annotazione della criticità VD-C-039, rilascio R03.

> Il testo originale è conservato più sotto per la storia della decisione.
> Dove contrasta con questa nota vale la nota.

## Cosa è cambiato

| Voce dell'ADR originale | Stato | Decisione in vigore |
|---|---|---|
| Database PostgreSQL «schema-per-tenant (isolamento dati senza row-level security)» | **Superata** da VD-P-00 | Un solo schema PostgreSQL `public` per tutte le tabelle; ogni dato di un cliente porta `tenant_id` e ogni query filtra per cliente. Nessuno schema per cliente: `tenants.schema_name` resta solo come identificativo. Lo schema si crea **solo** con le migrazioni Alembic in `services/gateway/alembic/` (mai `create_all()` a runtime, mai DDL in `infra/postgres/init.sql`). Row Level Security come difesa in profondità: proposta, da decidere (REGOLE §14.3). |
| Task queue «Celery + Kafka» | **Superata**: Celery non è usato | Eventi su Kafka con outbox transazionale (`public.outbox_eventi`, REGOLE §12.3) e consumer idempotenti; job periodici secondo REGOLE §22. Nessun worker Celery: la dipendenza va tolta dai servizi che ancora la dichiarano (VD-C-031). |
| Backend Python 3.12 + FastAPI | In vigore | — |
| Storage S3 WORM Object Lock Compliance | In vigore come requisito | Non ancora realizzato nel codice (VD-C-011). |
| Search OpenSearch 2.x | In vigore | — |
| Infra Kubernetes + Terraform AWS eu-south-1 | In vigore come obiettivo | Terraform e manifest da correggere (VD-C-038). |

## Testo originale (2026-03, Accettato)

### Decisione

- Backend: Python 3.12 + FastAPI (async nativo, OpenAPI auto-generata, Pydantic v2)
- Task queue: Celery + Kafka (event-driven, scala orizzontalmente) — *superato, vedi sopra*
- Database: PostgreSQL Aurora schema-per-tenant (isolamento dati senza row-level security) — *superato da VD-P-00, vedi sopra*
- Storage: AWS S3 WORM Object Lock Compliance (requisito normativo AGID)
- Search: OpenSearch 2.x
- Infra: Kubernetes + Terraform AWS eu-south-1 (provider qualificato ACN PA)

### Alternativa scartata

Django: sincrono di default, ORM async incompleto, porta overhead non necessario
(template engine, admin, form framework) per un servizio API puro.
