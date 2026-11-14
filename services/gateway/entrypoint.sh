#!/bin/sh
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# VerumDoc Gateway - Entrypoint
#
# Schema del database: SOLO con le migrazioni Alembic (REGOLE_SVILUPPO §16.2).
# Niente Base.metadata.create_all(), niente DDL a mano: in sviluppo e in
# produzione lo schema è quello prodotto da `alembic upgrade head`.
# Qualsiasi errore (migrazione o seed) ferma l'avvio: meglio un container che
# non parte di un servizio su uno schema sbagliato.
set -eu

echo "=== VerumDoc Gateway startup ==="

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERRORE: DATABASE_URL non impostata"
  exit 1
fi

echo ">>> Attesa PostgreSQL..."
RETRIES=30
until python -c "
import asyncpg, asyncio, os
async def check():
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn=url)
    await conn.close()
asyncio.run(check())
" 2>/dev/null; do
  RETRIES=$((RETRIES - 1))
  if [ $RETRIES -eq 0 ]; then echo "ERRORE: PostgreSQL non raggiungibile"; exit 1; fi
  echo "  Attendo 2s... ($RETRIES rimasti)"
  sleep 2
done
echo ">>> PostgreSQL OK"

cd /app

echo ">>> Migrazioni: alembic upgrade head"
# Ruoli separati (R04 secondo giro): le migrazioni usano DATABASE_URL_MIGRAZIONI
# (amministratore) se impostata, altrimenti DATABASE_URL (alembic/env.py); il
# servizio e il seed restano sul ruolo dei servizi (DATABASE_URL).
# Con più repliche in parallelo Alembic serializza tramite la tabella
# alembic_version; in produzione eseguire le migrazioni in un passo dedicato
# prima dei servizi (REGOLE §27) e avviare con VD_SALTA_MIGRAZIONI=1.
if [ "${VD_SALTA_MIGRAZIONI:-0}" = "1" ]; then
  echo "    saltate (VD_SALTA_MIGRAZIONI=1): lo schema deve essere già a head"
else
  alembic upgrade head
fi
echo ">>> Schema DB a head"

echo ">>> Seed (classi di base; dati dimostrativi solo con ENV development/test)"
python -m app.seed
echo ">>> Seed OK"

echo ">>> Avvio uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
