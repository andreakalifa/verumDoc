# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Codice fiscale degli utenti per l'accesso SPID/CIE; chiavi API nel formato unico

Revision ID: 20261020_012
Revises: 20261020_011
Create Date: 2026-10-20

Contenuto (agente g2-auth, VD-C-006 seguito, criticità chiavi API):
  - public.users.codice_fiscale VARCHAR(16) NULL: lega l'identità SPID/CIE
    (claim fiscal_number) all'utente. Unico PER CLIENTE (uq_users_tenant_cf),
    non globale: la stessa persona può operare per più clienti (REGOLE §14.12),
    e al login sceglie il cliente. Indice idx_users_cf per la ricerca al login;
    controllo di forma ck_users_cf_formato (16 caratteri maiuscoli: la
    validazione completa con il carattere di controllo è nel codice,
    libs.shared.security.codice_fiscale).
  - public.api_keys: le chiavi salvate prima di questa versione (impronta
    sha256 senza pepper) non hanno mai potuto autenticare (il gateway cercava
    un altro formato). Si disattivano, così l'elenco non le mostra come
    «attive»: vanno rigenerate. Nessuna riga viene cancellata.

Colonna nuova e vuota: nessun controllo di doppioni necessario (§16.5).
Nessun contenuto conservato viene toccato (§10.6).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261020_012"
down_revision = "20261020_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("codice_fiscale", sa.String(16), nullable=True),
        schema="public",
    )
    op.create_unique_constraint(
        "uq_users_tenant_cf", "users", ["tenant_id", "codice_fiscale"], schema="public"
    )
    op.create_index("idx_users_cf", "users", ["codice_fiscale"], schema="public")
    op.create_check_constraint(
        "ck_users_cf_formato",
        "users",
        "codice_fiscale IS NULL OR codice_fiscale ~ '^[A-Z0-9]{16}$'",
        schema="public",
    )
    # Chiavi API nel vecchio formato: mai utilizzabili, si disattivano.
    op.execute(
        "UPDATE public.api_keys SET attiva = false "
        "WHERE attiva AND key_hash NOT LIKE 'hmac-sha256$%'"
    )


def downgrade() -> None:
    # Le chiavi disattivate non si riattivano: nel vecchio formato non
    # autenticavano comunque, e riattivarle mostrerebbe uno stato falso.
    op.drop_constraint("ck_users_cf_formato", "users", schema="public", type_="check")
    op.drop_index("idx_users_cf", table_name="users", schema="public")
    op.drop_constraint("uq_users_tenant_cf", "users", schema="public", type_="unique")
    op.drop_column("users", "codice_fiscale", schema="public")
