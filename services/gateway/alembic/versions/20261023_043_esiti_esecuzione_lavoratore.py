# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Esiti di esecuzione scritti solo dal lavoratore di ciclo-vita (revisione 3 di R05, T5)

Revision ID: 20261023_043
Revises: 20261023_042
Create Date: 2026-10-23

Revisione 3 di R05 (T5). Il riscontro della hashchain (revisione 2, S2) prima
di firmare un tombstone di scarto controllava righe che il pod API di
ciclo-vita, esposto all'ingress, può scrivere con il suo login (pratica
ESEGUIBILE con il lavoro, oggetto ELIMINATO, PdA in ELIMINAZIONE: tutti DML di
``verumdoc_applicazione``). Un'API compromessa poteva quindi far firmare dal
registro uno scarto mai avvenuto. Ora il fatto che il riscontro pretende sta in
una tabella che SOLO il lavoratore scrive:

1. ruolo di gruppo ``verumdoc_ciclo_vita_lavoratore`` (NOLOGIN): ne è membro
   SOLO il login del Deployment ``ciclo-vita-lavoratore`` (Secret proprio
   ``ciclo-vita-lavoratore-secrets``, chiave ``DATABASE_URL``); il login
   dell'API resta membro del solo ``verumdoc_applicazione``;
2. tabella ``esiti_esecuzione`` (append-only): esito di esecuzione per
   (tipo di pratica, pratica, PdA), oggi lo scarto (``ELIMINATO``: tutte le
   versioni del PdA cancellate), scritto nella transazione che chiude l'oggetto;
3. privilegi: ``verumdoc_applicazione`` solo SELECT (la legge la hashchain);
   ``verumdoc_ciclo_vita_lavoratore`` SELECT e INSERT; UPDATE, DELETE e
   TRUNCATE vietati a tutti anche da un trigger (il proprietario compreso);
4. Row Level Security per ente (politica ``vd_ente_isolamento``, come la 038).

Riprendibile (S1): un solo segmento transazionale, saltato se già confermato
(``to_regclass``); ruolo e privilegi idempotenti. Senza CREATEROLE il ruolo non
si crea (avviso): crearlo come amministratore e rieseguire i GRANT
(``docs/architettura/sicurezza.md``).

Downgrade: tabella, trigger e funzione tolti; il ruolo resta (oggetto del
cluster, può avere membri).
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261023_043"
down_revision = "20261023_042"
branch_labels = None
depends_on = None

RUOLO_APPLICAZIONE = "verumdoc_applicazione"
RUOLO_LAVORATORE = "verumdoc_ciclo_vita_lavoratore"
POLITICA = "vd_ente_isolamento"
CONDIZIONE = ("(tenant_id = NULLIF(current_setting('app.ente', true), '')::uuid "
              "OR current_setting('app.ambito', true) = 'conservatore')")
TIPI = ("SCARTO",)
ESITI = ("ELIMINATO",)

_RUOLO = """
DO $ruolo$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@LAVORATORE@') THEN
        IF (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            CREATE ROLE @LAVORATORE@ NOLOGIN;
        ELSE
            RAISE WARNING 'ruolo @LAVORATORE@ non creato: serve CREATEROLE (crearlo come '
                'amministratore e rieseguire i GRANT di questa migrazione)';
        END IF;
    END IF;
END
$ruolo$;
""".replace("@LAVORATORE@", RUOLO_LAVORATORE)

_PRIVILEGI = """
DO $p$
BEGIN
    REVOKE ALL ON public.esiti_esecuzione FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@APPLICAZIONE@') THEN
        REVOKE ALL ON public.esiti_esecuzione FROM @APPLICAZIONE@;
        GRANT SELECT ON public.esiti_esecuzione TO @APPLICAZIONE@;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '@LAVORATORE@') THEN
        GRANT USAGE ON SCHEMA public TO @LAVORATORE@;
        GRANT SELECT, INSERT ON public.esiti_esecuzione TO @LAVORATORE@;
        GRANT USAGE ON SEQUENCE public.esiti_esecuzione_id_seq TO @LAVORATORE@;
    END IF;
END
$p$;
""".replace("@APPLICAZIONE@", RUOLO_APPLICAZIONE).replace("@LAVORATORE@", RUOLO_LAVORATORE)

_PROTEZIONE = """
CREATE OR REPLACE FUNCTION public.esiti_esecuzione_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'esiti_esecuzione è append-only: % vietata', TG_OP;
END;
$$;
"""


def _limita_attesa_lock() -> None:
    valore = os.environ.get("VD_MIGRAZIONE_LOCK_TIMEOUT", "5s").strip()
    if not valore.replace("ms", "").replace("s", "").isdigit():
        valore = "5s"
    op.execute(f"SET LOCAL lock_timeout = '{valore}'")


def _gia_applicato(oggetto: str) -> bool:
    if context.is_offline_mode():
        return False
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:o) IS NOT NULL"), {"o": oggetto}).scalar_one())


def _elenco(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


def upgrade() -> None:
    _limita_attesa_lock()
    op.execute(_RUOLO)
    if not _gia_applicato("public.esiti_esecuzione"):
        _catalogo()
    op.execute(_PRIVILEGI)


def _catalogo() -> None:
    op.create_table(
        "esiti_esecuzione",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("pratica_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                name="fk_esiti_esecuzione_pratica"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aip_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("public.aip.id", ondelete="RESTRICT",
                                name="fk_esiti_esecuzione_aip"), nullable=False),
        sa.Column("esito", sa.String(30), nullable=False),
        sa.Column("dettagli", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("scritto_da", sa.String(200), nullable=False,
                  server_default=sa.text("current_user")),
        sa.Column("scritto_il", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(f"tipo IN ({_elenco(TIPI)})", name="ck_esiti_esecuzione_tipo"),
        sa.CheckConstraint(f"esito IN ({_elenco(ESITI)})", name="ck_esiti_esecuzione_esito"),
        sa.UniqueConstraint("tipo", "pratica_id", "aip_id", "esito",
                            name="uq_esiti_esecuzione_fatto"),
        schema="public",
    )
    op.create_index("idx_esiti_esecuzione_aip", "esiti_esecuzione", ["aip_id"],
                    schema="public")
    op.execute(_PROTEZIONE)
    op.execute("CREATE TRIGGER trg_esiti_esecuzione_append_only BEFORE UPDATE OR DELETE "
               "ON public.esiti_esecuzione FOR EACH ROW "
               "EXECUTE FUNCTION public.esiti_esecuzione_append_only()")
    op.execute("CREATE TRIGGER trg_esiti_esecuzione_no_truncate BEFORE TRUNCATE "
               "ON public.esiti_esecuzione FOR EACH STATEMENT "
               "EXECUTE FUNCTION public.esiti_esecuzione_append_only()")
    op.execute("ALTER TABLE public.esiti_esecuzione ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {POLITICA} ON public.esiti_esecuzione AS PERMISSIVE FOR ALL "
               f"TO PUBLIC USING {CONDIZIONE} WITH CHECK {CONDIZIONE}")


def downgrade() -> None:
    _limita_attesa_lock()
    op.execute("DROP TABLE IF EXISTS public.esiti_esecuzione")
    op.execute("DROP FUNCTION IF EXISTS public.esiti_esecuzione_append_only()")
