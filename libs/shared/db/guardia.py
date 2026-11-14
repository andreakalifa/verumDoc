# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/shared/db/guardia.py — guardia all'avvio sul ruolo del database

Riferimenti: VD-C-113 (R05, agente r05-sicurezza); REGOLE_SVILUPPO §19.4
(impostazioni pericolose fermano l'avvio); VD-C-080 (la Row Level Security non
vale per il superutente né per il proprietario delle tabelle).

Fuori da ``ENV=development|test`` un servizio NON parte se ``DATABASE_URL``
appartiene a:
  - un superutente, un ruolo con ``BYPASSRLS`` o un membro di un ruolo che li ha
    (su RDS: ``rds_superuser``);
  - il proprietario (anche tramite appartenenza a un ruolo) di una tabella degli
    schemi ``public`` o ``partizioni``: il proprietario non è soggetto alla RLS e
    può modificare lo schema; i servizi entrano con un login membro di
    ``verumdoc_applicazione`` (R04, VD-C-080).
Il processo di manutenzione delle partizioni (``uso="manutenzione"``) è
proprietario delle tabelle partizionate operative per costruzione: per lui vale
solo il primo controllo.

Revisione 2 di R05 (S7): il login di un servizio deve avere
``idle_in_transaction_session_timeout`` (impostato sul ruolo: ``ALTER ROLE
<login> SET idle_in_transaction_session_timeout = '15min'``, vale anche
attraverso PgBouncer) maggiore di zero e al più
``VD_DB_IDLE_IN_TRANSACTION_MASSIMO_SECONDI`` (predefinito 1800): una
transazione dimenticata «idle in transaction» si chiude da sola invece di
trattenere per sempre l'export dell'audit nel WORM (docs/architettura/sicurezza.md §7).

La guardia non ha interruttori: nei test d'integrazione i servizi entrano con
un login NON superutente membro di ``verumdoc_applicazione`` (vedi
``tests/infra_reale.py`` dei servizi), come in esercizio.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)

#: Ambienti in cui la guardia non si applica (sviluppo locale e test).
AMBIENTI_SENZA_GUARDIA: frozenset[str] = frozenset({"development", "test"})
#: Schemi delle tabelle di VerumDoc di cui il servizio non deve essere proprietario.
SCHEMI_PROTETTI: tuple[str, ...] = ("public", "partizioni")

Uso = Literal["servizio", "manutenzione"]

_INTERROGAZIONE = text("""
SELECT current_user AS ruolo,
       EXISTS (SELECT 1 FROM pg_roles r
               WHERE (r.rolsuper OR r.rolbypassrls)
                 AND pg_has_role(current_user, r.oid, 'MEMBER')) AS privilegiato,
       EXISTS (SELECT 1 FROM pg_roles r
               WHERE r.rolname = 'rds_superuser'
                 AND pg_has_role(current_user, r.oid, 'MEMBER')) AS rds_superuser,
       (SELECT n.nspname || '.' || c.relname
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY(:schemi) AND c.relkind IN ('r', 'p')
           AND pg_has_role(current_user, c.relowner, 'USAGE')
         ORDER BY 1 LIMIT 1) AS tabella_posseduta,
       (SELECT s.setting::bigint FROM pg_settings s
         WHERE s.name = 'idle_in_transaction_session_timeout') AS idle_ms
""")

#: Revisione 2 di R05 (S7): massimo ammesso per idle_in_transaction_session_timeout
IDLE_MASSIMO_PREDEFINITO_SECONDI = 1800


def idle_massimo_ms() -> int:
    valore = (os.environ.get("VD_DB_IDLE_IN_TRANSACTION_MASSIMO_SECONDI") or "").strip()
    secondi = int(valore) if valore.isdigit() and int(valore) > 0 \
        else IDLE_MASSIMO_PREDEFINITO_SECONDI
    return secondi * 1000


class RuoloDatabaseNonAmmesso(RuntimeError):
    """Il ruolo di ``DATABASE_URL`` non è ammesso per un servizio in esercizio."""


@dataclass(frozen=True)
class EsitoRuolo:
    """Caratteristiche del ruolo connesso, lette dal database."""

    ruolo: str
    privilegiato: bool
    rds_superuser: bool
    tabella_posseduta: str | None
    #: S7: idle_in_transaction_session_timeout della sessione in ms (None = non letto)
    idle_in_transaction_ms: int | None = None


def guardia_attiva(env: str | None = None) -> bool:
    """True fuori da development/test (predefinito dei servizi: production)."""
    valore = (env if env is not None else os.environ.get("ENV", "production")).strip().lower()
    return valore not in AMBIENTI_SENZA_GUARDIA


def problemi_ruolo(esito: EsitoRuolo, uso: Uso) -> list[str]:
    """Controllo puro: elenco dei motivi per cui il ruolo non è ammesso."""
    problemi: list[str] = []
    if esito.privilegiato:
        problemi.append(f"il ruolo {esito.ruolo} è superutente o ha BYPASSRLS (anche per "
                        "appartenenza a un ruolo)")
    if esito.rds_superuser:
        problemi.append(f"il ruolo {esito.ruolo} è membro di rds_superuser")
    if uso == "servizio" and esito.tabella_posseduta:
        problemi.append(f"il ruolo {esito.ruolo} è proprietario di {esito.tabella_posseduta} "
                        "(usare un login membro di verumdoc_applicazione)")
    idle = esito.idle_in_transaction_ms
    if uso == "servizio" and (idle is None or idle <= 0 or idle > idle_massimo_ms()):
        problemi.append(
            f"il ruolo {esito.ruolo} ha idle_in_transaction_session_timeout "
            f"{'non letto' if idle is None else str(idle) + ' ms'}: serve un valore fra 1 ms e "
            f"{idle_massimo_ms() // 1000} s (ALTER ROLE {esito.ruolo} SET "
            "idle_in_transaction_session_timeout = '15min'), altrimenti una transazione "
            "dimenticata trattiene l'export dell'audit senza limite")
    return problemi


async def leggi_ruolo(engine: AsyncEngine) -> EsitoRuolo:
    """Legge dal database le caratteristiche del ruolo di ``engine``."""
    async with engine.connect() as conn:
        riga = (await conn.execute(_INTERROGAZIONE, {"schemi": list(SCHEMI_PROTETTI)})).one()
        await conn.rollback()
    return EsitoRuolo(ruolo=str(riga.ruolo), privilegiato=bool(riga.privilegiato),
                      rds_superuser=bool(riga.rds_superuser),
                      tabella_posseduta=riga.tabella_posseduta,
                      idle_in_transaction_ms=None if riga.idle_ms is None else int(riga.idle_ms))


async def verifica_ruolo_database(engine: AsyncEngine, *, uso: Uso = "servizio",
                                  env: str | None = None) -> None:
    """Solleva ``RuoloDatabaseNonAmmesso`` se il ruolo non è ammesso (§19.4)."""
    if not guardia_attiva(env):
        return
    problemi = problemi_ruolo(await leggi_ruolo(engine), uso)
    if problemi:
        logger.error("ruolo_database_non_ammesso", uso=uso, problemi=problemi)
        raise RuoloDatabaseNonAmmesso(
            "Avvio rifiutato (VD-C-113): " + "; ".join(problemi)
            + ". In esercizio DATABASE_URL deve usare un login membro di "
              "verumdoc_applicazione, non superutente né proprietario delle tabelle.")


__all__ = [
    "AMBIENTI_SENZA_GUARDIA", "IDLE_MASSIMO_PREDEFINITO_SECONDI", "SCHEMI_PROTETTI",
    "EsitoRuolo", "RuoloDatabaseNonAmmesso", "guardia_attiva", "idle_massimo_ms",
    "leggi_ruolo", "problemi_ruolo", "verifica_ruolo_database",
]
