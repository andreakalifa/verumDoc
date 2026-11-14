#!/bin/sh
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# VerumDoc Testimone - Entrypoint
#
# Lo schema del database del testimone si crea SOLO con le migrazioni Alembic
# del servizio (services/testimone/alembic, schema «testimone»): il testimone
# non usa mai le migrazioni del gateway. Più repliche in parallelo sono
# serializzate da un advisory lock (alembic/env.py). Con
# VD_SALTA_MIGRAZIONI=1 le migrazioni si eseguono in un passo dedicato.
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERRORE: DATABASE_URL non impostata"
  exit 1
fi

cd /app
if [ "${VD_SALTA_MIGRAZIONI:-0}" != "1" ]; then
  echo ">>> Migrazioni del testimone: alembic upgrade head"
  python -m alembic upgrade head
fi

exec "$@"
