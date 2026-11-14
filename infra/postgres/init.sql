-- SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
-- SPDX-License-Identifier: EUPL-1.2
-- VerumDoc PostgreSQL init — eseguito una sola volta dal container postgres
-- al primo avvio (docker-entrypoint-initdb.d), solo in sviluppo.
--
-- REGOLE_SVILUPPO §16.2: qui NIENTE tabelle, tipi enum o altro DDL di schema.
-- Lo schema lo crea SOLO `alembic upgrade head` (entrypoint del gateway),
-- identico in sviluppo e produzione. Database e ruolo applicativo li crea
-- l'immagine postgres dalle variabili POSTGRES_DB/POSTGRES_USER del compose.
--
-- Estensioni: gen_random_uuid() è nel core da PostgreSQL 13, quindi non
-- servono estensioni per lo schema. pgcrypto resta per compatibilità con
-- strumenti di sviluppo che la usano; uuid-ossp è stata tolta (mai usata).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
