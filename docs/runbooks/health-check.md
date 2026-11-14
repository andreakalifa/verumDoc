# Runbook: Health Check Servizi

## Verifica rapida tutti i servizi

```bash
make health
```

## Verifica singolo servizio

```bash
curl -f http://localhost:8000/api/v1/health/live   # gateway
curl -f http://localhost:8001/api/v1/health/live   # validation
curl -f http://localhost:8004/api/v1/health/live   # hashchain
curl -f http://localhost:8007/api/v1/health/live   # onboarding
```

## Se un servizio non risponde

1. Controlla i log: `docker compose logs -f <servizio>`
2. Verifica che le dipendenze siano up: `docker compose ps`
3. Restart: `docker compose restart <servizio>`
