# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/infrastructure/modelli.py — tabelle del testimone.

Riferimenti: contratto K3 di R04, PIANO_R03_R05 §5.3, REGOLE_SVILUPPO §16.4
(modelli) e §15.4 (registro a prova di manomissione).

Queste tabelle NON stanno in ``libs/shared/models``: appartengono al database
proprio del testimone (schema ``testimone``), che deve poter vivere in un altro
account senza alcun legame con il database del registro. Lo schema si crea
SOLO con le migrazioni Alembic del servizio (``services/testimone/alembic``).

Append-only: ``checkpoint_controfirmati``, ``allarmi`` e
``interventi_operatore`` rifiutano UPDATE, DELETE e TRUNCATE con un trigger
(migrazione 001). ``stato_registro`` è l'unica tabella che cambia: una riga
per origine, usata come lock (SELECT … FOR UPDATE) e come puntatore all'ultimo
checkpoint controfirmato; ogni suo cambio di stato ha la riga che lo motiva in
``allarmi`` o in ``interventi_operatore``.

Epoche: un intervento «nuova base» (registro ricostruito, decisione
dell'operatore) apre un'epoca nuova. Il controllo di fork (stessa dimensione,
radice diversa) vale dentro l'epoca corrente; la catena delle impronte del
testimone (``impronta_precedente``) attraversa invece tutte le epoche.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "testimone"

#: Stati della riga ``stato_registro``.
STATO_ATTIVO = "ATTIVO"
STATO_COMPROMESSO = "COMPROMESSO"

#: Tipi di allarme.
ALLARME_FORK = "FORK"
ALLARME_PROVA_NON_VALIDA = "PROVA_NON_VALIDA"

#: Azioni dell'operatore.
AZIONE_SBLOCCO = "SBLOCCO"
AZIONE_NUOVA_BASE = "NUOVA_BASE"

#: Impronta precedente del primo checkpoint di un'origine (32 byte a zero).
IMPRONTA_INIZIALE = bytes(32)


class Base(DeclarativeBase):
    """Base dichiarativa del SOLO testimone (metadati separati dal gateway)."""


class StatoRegistro(Base):
    """Stato del testimone per un'origine: lock, ultimo controfirmato, blocco."""

    __tablename__ = "stato_registro"
    __table_args__ = (
        CheckConstraint(
            f"stato IN ('{STATO_ATTIVO}', '{STATO_COMPROMESSO}')", name="ck_stato_registro_stato"
        ),
        CheckConstraint(
            f"stato = '{STATO_ATTIVO}' OR compromesso_dal IS NOT NULL",
            name="ck_stato_registro_compromesso_dal",
        ),
        CheckConstraint("(dimensione IS NULL) = (radice IS NULL)", name="ck_stato_registro_testa"),
        CheckConstraint("radice IS NULL OR length(radice) = 32", name="ck_stato_registro_radice"),
        {"schema": SCHEMA},
    )

    origine: Mapped[str] = mapped_column(Text, primary_key=True)
    epoca: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    dimensione: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    radice: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Impronta dell'ultima riga di checkpoint_controfirmati (catena propria).
    impronta_ultima: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    stato: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{STATO_ATTIVO}'")
    )
    compromesso_dal: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CheckpointControfirmato(Base):
    """Checkpoint del registro controfirmato dal testimone (append-only)."""

    __tablename__ = "checkpoint_controfirmati"
    __table_args__ = (
        UniqueConstraint(
            "origine", "epoca", "dimensione", name="uq_checkpoint_origine_epoca_dimensione"
        ),
        UniqueConstraint("origine", "impronta_precedente", name="uq_checkpoint_catena"),
        CheckConstraint("dimensione >= 0", name="ck_checkpoint_dimensione"),
        CheckConstraint("length(radice) = 32", name="ck_checkpoint_radice"),
        CheckConstraint(
            "length(impronta) = 32 AND length(impronta_precedente) = 32",
            name="ck_checkpoint_impronte",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    origine: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.stato_registro.origine", ondelete="RESTRICT"), nullable=False
    )
    epoca: Mapped[int] = mapped_column(Integer, nullable=False)
    dimensione: Mapped[int] = mapped_column(BigInteger, nullable=False)
    radice: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Nota ricevuta dal registro, così com'è (corpo + firme del registro).
    nota_registro: Mapped[str] = mapped_column(Text, nullable=False)
    # Riga di firma del testimone («— verumdoc-testimone <base64>»).
    firma_testimone: Mapped[str] = mapped_column(Text, nullable=False)
    # Nota pubblicata: corpo + firme del registro + firma del testimone.
    nota_controfirmata: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256(impronta_precedente || nota_controfirmata UTF-8): catena propria.
    impronta: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    impronta_precedente: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    controfirmato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Allarme(Base):
    """Incoerenza osservata nel registro (append-only)."""

    __tablename__ = "allarmi"
    __table_args__ = (
        CheckConstraint(
            f"tipo IN ('{ALLARME_FORK}', '{ALLARME_PROVA_NON_VALIDA}')", name="ck_allarmi_tipo"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    origine: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.stato_registro.origine", ondelete="RESTRICT"), nullable=False
    )
    epoca: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    # True se l'allarme ha portato il testimone in stato COMPROMESSO.
    blocca: Mapped[bool] = mapped_column(Boolean, nullable=False)
    dimensione_presentata: Mapped[int] = mapped_column(BigInteger, nullable=False)
    radice_presentata: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dimensione_conosciuta: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    radice_conosciuta: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    old: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Prove dell'incoerenza: la nota presentata (firmata dal registro) e la
    # prova ricevuta (righe base64), conservate così come sono arrivate.
    nota_presentata: Mapped[str] = mapped_column(Text, nullable=False)
    prova: Mapped[str] = mapped_column(Text, nullable=False)
    rilevato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InterventoOperatore(Base):
    """Intervento manuale di un operatore del testimone (append-only)."""

    __tablename__ = "interventi_operatore"
    __table_args__ = (
        CheckConstraint(
            f"azione IN ('{AZIONE_SBLOCCO}', '{AZIONE_NUOVA_BASE}')", name="ck_interventi_azione"
        ),
        CheckConstraint("length(btrim(operatore)) > 0", name="ck_interventi_operatore"),
        CheckConstraint("length(btrim(motivo)) >= 10", name="ck_interventi_motivo"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    origine: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.stato_registro.origine", ondelete="RESTRICT"), nullable=False
    )
    azione: Mapped[str] = mapped_column(Text, nullable=False)
    operatore: Mapped[str] = mapped_column(Text, nullable=False)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    epoca_precedente: Mapped[int] = mapped_column(Integer, nullable=False)
    epoca_nuova: Mapped[int] = mapped_column(Integer, nullable=False)
    stato_precedente: Mapped[str] = mapped_column(Text, nullable=False)
    # Solo per NUOVA_BASE: il checkpoint controfirmato come base dell'epoca.
    checkpoint_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA}.checkpoint_controfirmati.id", ondelete="RESTRICT"),
        nullable=True,
    )
    eseguito_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "ALLARME_FORK",
    "ALLARME_PROVA_NON_VALIDA",
    "AZIONE_NUOVA_BASE",
    "AZIONE_SBLOCCO",
    "Allarme",
    "Base",
    "CheckpointControfirmato",
    "IMPRONTA_INIZIALE",
    "InterventoOperatore",
    "SCHEMA",
    "STATO_ATTIVO",
    "STATO_COMPROMESSO",
    "StatoRegistro",
]
