# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/shared/models
Riferimento piano: F0.4 — Schema Database
Aggiornato: R03 (20/10/2026) — schema unico riproducibile (VD-C-007, VD-P-00)

ARCHITETTURA (regola vincolante, REGOLE_SVILUPPO §16):
  Tutti i modelli usano schema="public" su PostgreSQL.
  L'isolamento multi-tenant avviene tramite colonna tenant_id.
  Non esiste alcuno schema per-tenant PostgreSQL (decisione VD-P-00, ADR-001 superato).
  Questi modelli sono la fonte di verità dello schema; le migrazioni Alembic
  (services/gateway/alembic/versions/) lo costruiscono da un database vuoto
  e il test tests/test_schema_alembic.py del gateway verifica che
  `alembic upgrade head` + confronto con questi modelli non dia differenze.
  Mai `Base.metadata.create_all()` a runtime.

  Tipi ENUM nativi PostgreSQL: il nome del tipo è dichiarato qui (``name=...``)
  ed è lo stesso creato dalle migrazioni; un test confronta i valori Python
  con quelli di pg_enum.

  Per aggiungere una tabella:
    1. Aggiungere il modello SQLAlchemy qui con __table_args__ = {"schema": "public"}
    2. Creare una nuova migration Alembic in services/gateway/alembic/versions/
    3. Non modificare mai il DB manualmente in produzione

  Per aggiungere una colonna a una tabella esistente:
    1. Aggiungere mapped_column() al modello
    2. Creare migration con op.add_column(..., schema="public")
    3. Eseguire alembic upgrade head (automatico all'avvio del gateway)

Modelli pubblici (schema public, accesso cross-tenant):
  Tenant, User, ApiKey, RscNomination, Contract, AuditGlobal, HashchainBlock

Modelli operativi (schema public, isolati da tenant_id):
  ClasseDocumentale, Pdv, Aip, Documento, MetadatiAllegato5,
  LifecycleEvent, Fascicolo, FascicoloAip, Notification, PdndFruitore

R05 (VD-C-080, migrazione 20261023_038): documenti, metadati_allegato5,
lifecycle_events e fascicoli_aip hanno una colonna tenant_id propria, riempita
da un trigger dal padre (AIP, documento, fascicolo); le tabelle dei dati hanno la
Row Level Security per ente (docs/architettura/sicurezza.md).
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


# ── Base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Base class per tutti i modelli VerumDoc."""
    pass


# ── ENUM TIPI ─────────────────────────────────────────────────────

class TenantStato(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    TRIAL         = "TRIAL"
    ATTIVO        = "ATTIVO"
    SOSPESO       = "SOSPESO"
    DISATTIVATO   = "DISATTIVATO"


class UserRuolo(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    # L'ordine è quello del tipo PostgreSQL public.user_ruolo
    # (RSC_CONSERVATORE aggiunto in coda dalla migrazione 007).
    SUPER_ADMIN       = "SUPER_ADMIN"
    ADMIN_TENANT      = "ADMIN_TENANT"
    OPERATORE         = "OPERATORE"
    VIEWER            = "VIEWER"
    API_SYSTEM        = "API_SYSTEM"
    RSC_CONSERVATORE  = "RSC_CONSERVATORE"  # F1-05 AGID: Responsabile Servizio Conservazione


class PdvStato(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    RICEVUTO      = "RICEVUTO"
    IN_VALIDAZIONE = "IN_VALIDAZIONE"
    VALIDATO      = "VALIDATO"
    IN_BUILD      = "IN_BUILD"
    CONSERVATO    = "CONSERVATO"
    ERRORE        = "ERRORE"
    ANNULLATO     = "ANNULLATO"


class AipStato(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    IN_BUILD      = "IN_BUILD"
    FIRMATO       = "FIRMATO"
    TIMESTAMPATO  = "TIMESTAMPATO"
    SU_STORAGE    = "SU_STORAGE"
    IN_CATENA     = "IN_CATENA"
    CONSERVATO    = "CONSERVATO"
    ESIBIZIONE    = "ESIBIZIONE"
    ELIMINAZIONE  = "ELIMINAZIONE"
    # Revisione 2 di R05 (S3, migrazione 20261023_042): cancellazione dello scarto
    # iniziata e non completata (runbook scarto_incompleto)
    ELIMINAZIONE_INCOMPLETA = "ELIMINAZIONE_INCOMPLETA"


class StorageTier(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    HOT     = "HOT"
    WARM    = "WARM"
    COLD    = "COLD"
    ARCHIVAL = "ARCHIVAL"


class ModalitaFormazione(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    """
    Metadato ModalitaDiFormazione — Allegato 5 alle Linee guida AgID sul
    documento informatico (2021), che rimanda al §2.1.1 delle Linee guida:
    il documento informatico è formato mediante una delle modalità a)–d).
    Il valore trasmesso e conservato è la lettera.

    UNICA definizione nel sistema (VD-C-037): migrazioni (tipo PostgreSQL
    public.modalita_formazione), validazione e API la importano da qui.
    Se la norma cambia: nuova voce qui + migrazione ``ALTER TYPE ... ADD VALUE``.
    """
    # a) creazione tramite l'utilizzo di strumenti software che assicurino
    #    la produzione di documenti nei formati dell'Allegato 2
    CREAZIONE_SOFTWARE = "a"
    # b) acquisizione di un documento informatico per via telematica o su
    #    supporto informatico, della copia per immagine o della copia
    #    informatica di un documento analogico
    ACQUISIZIONE = "b"
    # c) memorizzazione su supporto informatico delle informazioni risultanti
    #    da transazioni o processi informatici o dalla presentazione telematica
    #    di dati attraverso moduli o formulari
    MEMORIZZAZIONE_TRANSAZIONI = "c"
    # d) generazione o raggruppamento, anche automatico, di un insieme di dati
    #    o registrazioni provenienti da una o più banche dati, secondo una
    #    struttura logica predeterminata e memorizzata in forma statica
    GENERAZIONE_DA_BANCHE_DATI = "d"


def _valori_enum(e: type[enum.Enum]) -> list[str]:
    """Salva nel database il valore dell'enum (non il nome del membro)."""
    return [m.value for m in e]


class TipoSInCROXML(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    """Tipologia struttura SInCRO 2020 per la classe documentale."""
    DI  = "DI"   # Documento Informatico
    DAI = "DAI"  # Documento Amministrativo Informatico
    AGG = "AGG"  # Aggregazione Documentale


class TipoFlusso(str, enum.Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    ATTIVO   = "ATTIVO"
    PASSIVO  = "PASSIVO"
    INTERNO  = "INTERNO"


# ── SCHEMA PUBLIC — tabelle condivise ─────────────────────────────

class Tenant(Base):
    """
    Tenant (cliente conservatore).
    Schema: public — unico record per ogni cliente registrato.
    """
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("natura IS NULL OR natura IN ('PA', 'PRIVATO')",
                        name="ck_tenants_natura"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text(
            "gen_random_uuid()")
    )
    codice_fiscale: Mapped[str] = mapped_column(String(16), nullable=False)
    ragione_sociale: Mapped[str] = mapped_column(String(500), nullable=False)
    # Solo identificativo: nessuno schema PostgreSQL per cliente (VD-P-00)
    schema_name: Mapped[str] = mapped_column(String(63), nullable=False)
    stato: Mapped[TenantStato] = mapped_column(
        Enum(TenantStato, name="tenant_stato", schema="public"),
        nullable=False, default=TenantStato.TRIAL, server_default=text("'TRIAL'"),
    )
    piano: Mapped[str] = mapped_column(
        String(50), nullable=False, default="starter", server_default=text("'starter'"))
    storage_quota_gb: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100"))
    storage_usato_gb: Mapped[float] = mapped_column(
        nullable=False, default=0.0, server_default=text("0"))
    email_referente: Mapped[str] = mapped_column(String(255), nullable=False)
    pec_referente: Mapped[str | None] = mapped_column(String(255))
    codice_ipa: Mapped[str | None] = mapped_column(String(50))  # Solo per PA
    # Revisione R05 (R9): natura dichiarata (PA | PRIVATO). Decide il nulla osta
    # della Soprintendenza per lo scarto; NULL = trattato come PA (prudente).
    natura: Mapped[str | None] = mapped_column(String(16))
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    webhook_secret_hash: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text(
        "'{}'::jsonb"))
    # Classi documentali abilitate per il cliente (lista di id); NULL = tutte
    # quelle visibili al cliente (di base + proprie). Usata da onboarding.
    classi_abilitate: Mapped[list[str] | None] = mapped_column(JSONB)
    data_attivazione: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_scadenza: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("codice_fiscale", name="uq_tenants_cf"),
        UniqueConstraint("schema_name", name="uq_tenants_schema_name"),
        Index("idx_tenants_schema_name", "schema_name"),
        Index("idx_tenants_stato", "stato"),
        Index("idx_tenants_cf", "codice_fiscale"),
        {"schema": "public"},
    )


class User(Base):
    """
    Utente VerumDoc — appartiene a un tenant.
    Schema: public (la FK tenant_id identifica l'appartenenza).
    """
    __tablename__ = "users"
    __table_args__ = (
        Index("idx_users_tenant", "tenant_id"),
        Index("idx_users_email", "email"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        # Accesso SPID/CIE (R02, REGOLE §14.12): la stessa persona può essere
        # utente di più clienti (es. segretario comunale in convenzione), quindi
        # il codice fiscale è unico PER CLIENTE e non globale; al login con più
        # clienti la persona sceglie esplicitamente.
        UniqueConstraint("tenant_id", "codice_fiscale", name="uq_users_tenant_cf"),
        Index("idx_users_cf", "codice_fiscale"),
        CheckConstraint(
            "codice_fiscale IS NULL OR codice_fiscale ~ '^[A-Z0-9]{16}$'",
            name="ck_users_cf_formato",
        ),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    cognome: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Codice fiscale della persona (16 caratteri maiuscoli, normalizzato da
    #: libs.shared.security.codice_fiscale). Lega l'identità SPID/CIE all'utente;
    #: senza codice fiscale l'utente non può accedere con SPID/CIE.
    codice_fiscale: Mapped[str | None] = mapped_column(String(16))
    ruolo: Mapped[UserRuolo] = mapped_column(
        Enum(UserRuolo, name="user_ruolo", schema="public"),
        nullable=False, default=UserRuolo.OPERATORE, server_default=text("'OPERATORE'"),
    )
    password_hash: Mapped[str | None] = mapped_column(String(128))
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    #: R10 (D30, migrazione 20261102_049): ruolo personalizzato dell'ente (stesso
    #: ente garantito dalla chiave esterna composta ``fk_users_ruolo_personalizzato``);
    #: ``ruolo`` resta il ruolo base (perimetro). ``permessi_versione`` cresce a ogni
    #: assegnazione o modifica del ruolo: la revoca rifiuta i token emessi prima.
    ruolo_personalizzato_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    permessi_versione: Mapped[int] = mapped_column(Integer, nullable=False, default=1,
                                                   server_default=text("1"))
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")


# Aggiungi back_populates su Tenant (dopo definizione User)
Tenant.users = relationship("User", back_populates="tenant")


class ApiKey(Base):
    """
    API Key per accesso programmatico.
    Schema: public.
    """
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("idx_apikeys_tenant", "tenant_id"),
        Index("idx_apikeys_hash", "key_hash"),
        # Migrazione 20261021_018 (R03 b4): la verifica cerca per identificativo
        # pubblico a ogni richiesta con chiave API.
        Index("idx_apikeys_prefix", "key_prefix", postgresql_where=text("attiva")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(10), nullable=False)  # Es. "vd_prod_"
    permessi: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    attiva: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    scadenza: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RscNomination(Base):
    """
    Nomina del Responsabile della Conservazione di un cliente.
    Schema: public — documento legale obbligatorio (LG AgID, ruoli).
    Una sola nomina attiva per cliente (indice unico parziale); una nomina
    non si cancella: si revoca (attiva=false, data_revoca) — REGOLE §10.5.
    """
    __tablename__ = "rsc_nominations"
    __table_args__ = (
        Index("idx_rsc_tenant", "tenant_id"),
        Index(
            "uq_rsc_attiva_per_tenant", "tenant_id", unique=True,
            postgresql_where=text("attiva"),
        ),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    nome_rsc: Mapped[str] = mapped_column(String(300), nullable=False)
    codice_fiscale_rsc: Mapped[str] = mapped_column(String(16), nullable=False)
    email_rsc: Mapped[str] = mapped_column(String(255), nullable=False)
    pec_rsc: Mapped[str | None] = mapped_column(String(255))
    data_nomina: Mapped[date] = mapped_column(Date, nullable=False)
    data_scadenza: Mapped[date | None] = mapped_column(Date)
    documento_s3_key: Mapped[str | None] = mapped_column(String(500))
    attiva: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    data_revoca: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Contract(Base):
    """
    Contratto di servizio tra VerumDoc e il tenant.
    Schema: public.
    """
    __tablename__ = "contracts"
    __table_args__ = (
        Index("idx_contracts_tenant", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    numero_contratto: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    data_stipula: Mapped[date] = mapped_column(Date, nullable=False)
    data_scadenza: Mapped[date | None] = mapped_column(Date)
    piano: Mapped[str] = mapped_column(String(50), nullable=False)
    valore_annuo_eur: Mapped[float | None] = mapped_column()
    template_s3_key: Mapped[str | None] = mapped_column(String(500))
    firmato_s3_key: Mapped[str | None] = mapped_column(String(500))
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditGlobal(Base):
    """
    Log di audit globale, append-only. Schema: public — eventi di tutti i tenant.

    R04 dati (migrazione 20261023_028): tabella partizionata per MESE su
    ``created_at`` (partizioni nello schema ``partizioni``, create in anticipo da
    ``libs.shared.db.partizioni``, mai staccate); PK ``(id, created_at)``.
    Protezioni su madre e su ogni partizione: RLS con le sole politiche INSERT e
    SELECT, trigger ``vd_audit_append_only`` contro UPDATE, DELETE e TRUNCATE.
    """
    __tablename__ = "audit_global"
    __table_args__ = (
        Index("idx_audit_evento", "evento"),
        Index("idx_audit_tenant_evento", "tenant_id", "evento", text("created_at DESC")),
        # R04 servizi (migrazione 20261023_029, VD-C-102): registro a cursore
        Index("idx_audit_global_cursore", text("created_at DESC"), text("id DESC")),
        Index("idx_audit_global_tenant_cursore", "tenant_id", text("created_at DESC"),
              text("id DESC")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    aip_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    evento: Mapped[str] = mapped_column(String(100), nullable=False)
    dettagli: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text(
        "'{}'::jsonb"))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), primary_key=True
    )
    # NOTA: no updated_at — audit log è append-only per definizione


class HashchainBlock(Base):
    """
    Blocco della hashchain proprietaria.
    Schema: public — condiviso tra tutti i tenant (catena unica).
    Un blocco contiene un batch di AIP (Merkle tree).
    """
    __tablename__ = "hashchain_blocks"
    __table_args__ = (
        Index("idx_hchain_numero", "numero_blocco"),
        Index("idx_hchain_created", "created_at"),
        CheckConstraint(
            "stato_firma IN ('DA_FIRMARE', 'FIRMATO', 'NON_FIRMATO')",
            name="ck_hashchain_stato_firma",
        ),
        CheckConstraint("versione_formato IN (1, 2)", name="ck_hashchain_versione_formato"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    numero_blocco: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    hash_blocco: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    hash_blocco_precedente: Mapped[str] = mapped_column(String(128), nullable=False)
    merkle_root: Mapped[str] = mapped_column(String(128), nullable=False)
    aip_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    num_aip: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text(
        "0"))
    firma_cades: Mapped[str | None] = mapped_column(Text)
    tsa_token: Mapped[str | None] = mapped_column(Text)
    tsa_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Migrazione 20261020_011 (be-pipeline): formato del blocco e stato della
    # firma; la tabella è append-only (trigger), si aggiornano solo le
    # colonne di firma e solo da DA_FIRMARE.
    versione_formato: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    stato_firma: Mapped[str] = mapped_column(String(20), nullable=False)
    motivo_firma: Mapped[str | None] = mapped_column(Text)


# ── MODELLI OPERATIVI (schema public, isolamento via tenant_id) ──────
# Tutti i modelli sono in schema public.
# FK verso PdV, PdA, documenti: ON DELETE RESTRICT (REGOLE §10.4).

class ClasseDocumentale(Base):
    """
    Classe documentale.
    tenant_id NULL  → classe di base del servizio (la gestisce solo SUPER_ADMIN).
    tenant_id = id  → classe del cliente (la gestisce l'amministratore del cliente).
    Il codice è unico fra le classi di base e dentro ogni cliente
    (UNIQUE NULLS NOT DISTINCT, PostgreSQL >= 15); una classe del cliente
    con lo stesso codice di una classe di base la sostituisce per quel cliente.
    """
    __tablename__ = "classi_documentali"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "codice", name="uq_classi_tenant_codice",
            postgresql_nulls_not_distinct=True,
        ),
        Index("idx_classi_codice", "codice"),
        Index("idx_classi_tenant_id", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE", name="fk_classi_tenant"),
        nullable=True,
    )
    codice: Mapped[str] = mapped_column(String(50), nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    descrizione: Mapped[str | None] = mapped_column(Text)
    anni_conservazione: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default=text("10"))
    conservazione_permanente: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))
    metadati_obbligatori: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"))
    formati_ammessi: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text(
        "'[]'::jsonb"))
    max_dimensione_mb: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    tipologia_xml: Mapped[TipoSInCROXML] = mapped_column(
        Enum(TipoSInCROXML, name="tipo_sincro_xml", schema="public"),
        nullable=False, default=TipoSInCROXML.DAI, server_default=text("'DAI'"),
    )
    attiva: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )


class Pdv(Base):
    """Pacchetto di Versamento — rappresenta un documento da conservare."""
    __tablename__ = "pdv"
    __table_args__ = (
        Index("idx_pdv_tenant", "tenant_id"),
        Index("idx_pdv_stato", "stato"),
        Index("idx_pdv_classe", "classe_doc_id"),
        Index("idx_pdv_data_ric", "data_ricezione"),
        Index("idx_pdv_data_ric_desc", text("data_ricezione DESC")),
        # R03 i1 (migrazione 20261021_020): elenco dei versamenti a cursore
        Index("idx_pdv_tenant_ricezione", "tenant_id", text("data_ricezione DESC"),
              text("id DESC")),
        Index("idx_pdv_tenant_stato_ricezione", "tenant_id", "stato",
              text("data_ricezione DESC"), text("id DESC")),
        # R03 f3 (migrazione 20261021_024, REV-N1): candidati della pulizia dello
        # staging in ordine di rinvio (sostituisce idx_pdv_staging_da_pulire della 021)
        Index("idx_pdv_staging_da_verificare", "staging_verifica_prossima", "id",
              postgresql_where=text("staging_eliminato_il IS NULL AND "
                                    "staging_verifica_bloccata_il IS NULL AND "
                                    "stato IN ('IN_BUILD', 'CONSERVATO')")),
        Index("idx_pdv_staging_bloccati", "staging_verifica_bloccata_il",
              postgresql_where=text("staging_eliminato_il IS NULL AND "
                                    "staging_verifica_bloccata_il IS NOT NULL")),
        # R04 servizi (migrazione 20261023_029, VD-C-104): PdV in ERRORE con le
        # copie di transito ancora presenti, in ordine di scadenza
        Index("idx_pdv_staging_scartati", "data_aggiornamento", "id",
              postgresql_where=text("stato = 'ERRORE' AND staging_eliminato_il IS NULL")),
        # R05 Q1 (migrazione 20261023_036): campione dei PdV recenti di una classe
        # per l'anteprima delle modifiche di configurazione (limite dichiarato)
        Index("idx_pdv_classe_ricezione", "classe_doc_id", text("data_ricezione DESC"),
              text("id DESC")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    numero_protocollo: Mapped[str | None] = mapped_column(String(100))
    oggetto: Mapped[str] = mapped_column(String(500), nullable=False)
    stato: Mapped[PdvStato] = mapped_column(
        Enum(PdvStato, name="pdv_stato", schema="public"),
        nullable=False, default=PdvStato.RICEVUTO, server_default=text("'RICEVUTO'"),
    )
    classe_doc_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tipo_flusso: Mapped[TipoFlusso] = mapped_column(
        Enum(TipoFlusso, name="tipo_flusso", schema="public"),
        nullable=False, default=TipoFlusso.ATTIVO, server_default=text("'ATTIVO'"),
    )
    data_ricezione: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    data_aggiornamento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_pdv_tenant"),
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text(
        "'{}'::jsonb"))
    errori_validazione: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=text(
        "'[]'::jsonb"))
    s3_prefix: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Migrazione 20261021_014 (R03, REGOLE §8.3, §8.6): fotografia delle regole
    # applicate al versamento (versione del catalogo, profilo di metadati,
    # impostazioni effettive con origine) e avvisi non bloccanti del motore
    # delle regole, riportati nella risposta e nel rapporto di versamento.
    regole_applicate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    avvisi: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Migrazione 20261021_021 (R03 f1, REV-08): copie di transito nello staging
    # eliminate dopo la verifica della copia nel PdA (NULL = ancora presenti).
    staging_eliminato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Migrazione 20261021_024 (R03 f3, REV-N1): rinvio per riga della pulizia
    # dello staging (attesa crescente, massimo di tentativi, PdV bloccati).
    staging_verifica_prossima: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("'1970-01-01 00:00:00+00'"))
    staging_verifica_tentativi: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0"))
    staging_verifica_motivo: Mapped[str | None] = mapped_column(String(500))
    staging_verifica_bloccata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Migrazione 20261023_029 (R04, VD-C-104): avviso al cliente della scadenza delle
    # copie di transito di un PdV in ERRORE (evento versamento.staging_scaduto)
    staging_avvisato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    classe_doc: Mapped[ClasseDocumentale] = relationship(
        "ClasseDocumentale",
        foreign_keys="[Pdv.classe_doc_id]",
    )
    aip: Mapped[list[Aip]] = relationship("Aip", back_populates="pdv")


class Aip(Base):
    """Archival Information Package (PdA) — documento conservato."""
    __tablename__ = "aip"
    __table_args__ = (
        Index("idx_aip_tenant", "tenant_id"),
        Index("idx_aip_tenant_stato", "tenant_id", "stato"),
        Index("idx_aip_stato", "stato"),
        Index("idx_aip_pdv", "pdv_id"),
        Index("idx_aip_urn", "urn"),
        Index("idx_aip_data_v", "data_versamento"),
        # R03 f2 (migrazione 20261021_022): elenco e ricerca dei PdA a cursore
        Index("idx_aip_tenant_versamento", "tenant_id", text("data_versamento DESC"),
              text("id DESC")),
        Index("idx_aip_tenant_stato_versamento", "tenant_id", "stato",
              text("data_versamento DESC"), text("id DESC")),
        Index(
            "idx_aip_scadenza", "scadenza_conservazione",
            postgresql_where=text("scadenza_conservazione IS NOT NULL"),
        ),
        Index(
            "idx_aip_tier_versamento", "storage_tier", "data_versamento",
            postgresql_where=text(
                "storage_tier = ANY (ARRAY['HOT'::public.storage_tier, "
                "'WARM'::public.storage_tier, 'COLD'::public.storage_tier])"
            ),
        ),
        # R04 dati (migrazione 20261023_028): BRIN per gli intervalli di tempo (report SLA)
        Index("idx_aip_conservazione_brin", "data_conservazione", postgresql_using="brin"),
        # R08 (migrazione 20261031_047): rapporto delle firme con chiave e impronta insieme
        CheckConstraint(
            "(rapporto_firme_key IS NULL) = (rapporto_firme_sha256 IS NULL) AND "
            "(rapporto_firme_sha256 IS NULL OR rapporto_firme_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_aip_rapporto_firme"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    pdv_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pdv.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_aip_tenant"),
        nullable=False,
    )
    urn: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    stato: Mapped[AipStato] = mapped_column(
        Enum(AipStato, name="aip_stato", schema="public"),
        nullable=False, default=AipStato.IN_BUILD, server_default=text("'IN_BUILD'"),
    )
    storage_tier: Mapped[StorageTier] = mapped_column(
        Enum(StorageTier, name="storage_tier", schema="public"),
        nullable=False, default=StorageTier.HOT, server_default=text("'HOT'"),
    )
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    s3_version_id: Mapped[str | None] = mapped_column(String(200))
    dimensione_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"))
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_sha512: Mapped[str | None] = mapped_column(String(128))
    firma_cades_ref: Mapped[str | None] = mapped_column(String(500))
    tsa_token_ref: Mapped[str | None] = mapped_column(String(500))
    tsa_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hashchain_block_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.hashchain_blocks.id", ondelete="RESTRICT"),
    )
    data_versamento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    data_conservazione: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scadenza_conservazione: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anni_conservazione: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default=text("10"))
    sincro_xml_key: Mapped[str | None] = mapped_column(String(500))
    indice_xml_key: Mapped[str | None] = mapped_column(String(500))
    # R08 (D01, migrazione 20261031_047): rapporto di verifica delle firme
    # conservato nel PdA (chiave WORM e impronta, citata nel RdV)
    rapporto_firme_key: Mapped[str | None] = mapped_column(String(500))
    rapporto_firme_sha256: Mapped[str | None] = mapped_column(String(64))
    # R09 (D04, migrazione 20261101_048): termine del blocco WORM più breve fra gli
    # oggetti del PdA, scritto dal rinnovo (indice parziale sui PdA permanenti)
    retention_fino_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )

    pdv: Mapped[Pdv] = relationship("Pdv", back_populates="aip")
    documenti: Mapped[list[Documento]] = relationship("Documento", back_populates="aip")
    lifecycle_events: Mapped[list[LifecycleEvent]] = relationship(
        "LifecycleEvent", back_populates="aip"
    )


class Documento(Base):
    """File contenuto in un AIP. I metadati Allegato 5 stanno in
    MetadatiAllegato5 (relazione 1:1 tramite metadati_allegato5.documento_id)."""
    __tablename__ = "documenti"
    __table_args__ = (
        Index("idx_documenti_aip", "aip_id"),
        # R04 dati (migrazione 20261023_028): BRIN per gli intervalli di tempo
        Index("idx_documenti_creazione_brin", "created_at", postgresql_using="brin"),
        # R05 (VD-C-080, migrazione 20261023_038): ente proprio per la RLS
        Index("idx_documenti_tenant", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    aip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.aip.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: R05 (VD-C-080): ente del PdA, ricavato dal trigger ``trg_documenti_tenant``
    #: (chi scrive non lo indica; se lo indica deve coincidere).
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_documenti_tenant"),
        nullable=False,
    )
    nome_file: Mapped[str] = mapped_column(String(500), nullable=False)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    formato_mime: Mapped[str] = mapped_column(String(200), nullable=False)
    formato_pronom: Mapped[str | None] = mapped_column(String(100))
    dimensione_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_sha512: Mapped[str | None] = mapped_column(String(128))
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    ordine: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text(
        "0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    aip: Mapped[Aip] = relationship("Aip", back_populates="documenti")


class MetadatiAllegato5(Base):
    """
    Metadati del documento informatico secondo l'Allegato 5 alle Linee guida
    AgID (versione maggio 2021). Copertura PARZIALE, dichiarata (VD-C-037).

    Il modello ha 23 campi di metadato (più id, documento_id, tenant_id,
    created_at, updated_at). Non sono «38 campi»: il conteggio precedente era falso.

    Coperti (metadato Allegato 5 → colonna):
      - IdDoc.Identificativo → identificativo_documento
      - ModalitaDiFormazione → modalita_formazione (valori a/b/c/d, LG §2.1.1)
      - TipologiaDocumentale → tipologia_documentale
      - DatiDiRegistrazione (solo numero e data) → numero_registrazione,
        data_registrazione; numero_progressivo
      - Soggetti (solo produttore e destinatari, senza ruoli tipizzati) →
        soggetto_produttore_id, soggetto_produttore_nome, soggetto_destinatario,
        codice_ipa, aoo, codice_ruo
      - ChiaveDescrittiva.Oggetto → oggetto
      - Allegati (solo il numero) → numero_allegati
      - IdentificativoDelFormato (solo il formato dichiarato) → formato
      - Verifica (solo il tipo di firma dichiarato) → tipo_firma
      - VersioneDelDocumento → versione_documento
      - Classificazione → classificazione
      - Agg (identificativo dell'aggregazione) → fascicolo_id
    Campi non previsti dall'Allegato 5: data_documento, lingua,
    stato_conservazione, metadati_extra (contenitore libero).

    Mancanti (da realizzare con un profilo di metadati versionato, REGOLE §8.7):
      - IdDoc.ImprontaCrittograficaDelDocumento (impronta + algoritmo: oggi solo
        in documenti.hash_sha256) e IdDoc.Segnatura (documento amministrativo)
      - DatiDiRegistrazione.TipologiaDiFlusso e TipoRegistro
      - Soggetti con i ruoli tipizzati (autore, mittente, destinatario,
        operatore, RUP, …)
      - ChiaveDescrittiva.ParoleChiave
      - Allegati: IdDoc e descrizione di ciascun allegato
      - Riservato
      - IdentificativoDelFormato completo (formato + prodotto software)
      - Verifica completa (firma digitale, sigillo, marcatura temporale,
        conformità copie per immagine)
      - IdIdentificativoDocumentoPrimario
      - NomeDelDocumento
      - TracciatureModificheDocumento
      - TempoDiConservazione
      - Note
      - tutti i metadati delle aggregazioni documentali (fascicolo e serie)
    """
    __tablename__ = "metadati_allegato5"
    __table_args__ = (
        # R05 (VD-C-080, migrazione 20261023_038): ente proprio per la RLS
        Index("idx_metadati_allegato5_tenant", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    documento_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.documenti.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    #: R05 (VD-C-080): ente del documento, ricavato da ``trg_metadati_allegato5_tenant``.
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT",
                   name="fk_metadati_allegato5_tenant"),
        nullable=False,
    )
    identificativo_documento: Mapped[str] = mapped_column(String(255), nullable=False)
    modalita_formazione: Mapped[ModalitaFormazione] = mapped_column(
        Enum(
            ModalitaFormazione, name="modalita_formazione", schema="public",
            values_callable=_valori_enum,
        ),
        nullable=False,
    )
    tipologia_documentale: Mapped[str] = mapped_column(String(100), nullable=False)
    data_documento: Mapped[date] = mapped_column(Date, nullable=False)
    soggetto_produttore_id: Mapped[str] = mapped_column(String(255), nullable=False)
    soggetto_produttore_nome: Mapped[str] = mapped_column(String(500), nullable=False)
    soggetto_destinatario: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"))
    oggetto: Mapped[str] = mapped_column(String(1000), nullable=False)
    lingua: Mapped[str] = mapped_column(
        String(10), nullable=False, default="it", server_default=text("'it'"))
    classificazione: Mapped[str | None] = mapped_column(String(500))
    fascicolo_id: Mapped[str | None] = mapped_column(String(255))
    stato_conservazione: Mapped[str] = mapped_column(
        String(100), nullable=False, default="VERSATO", server_default=text("'VERSATO'"))
    formato: Mapped[str] = mapped_column(String(100), nullable=False)
    versione_documento: Mapped[str | None] = mapped_column(
        String(20), default="1.0", server_default=text("'1.0'"))
    numero_allegati: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tipo_firma: Mapped[str | None] = mapped_column(String(100))
    numero_registrazione: Mapped[str | None] = mapped_column(String(100))
    data_registrazione: Mapped[date | None] = mapped_column(Date)
    numero_progressivo: Mapped[str | None] = mapped_column(String(50))
    codice_ipa: Mapped[str | None] = mapped_column(String(50))
    aoo: Mapped[str | None] = mapped_column(String(100))
    codice_ruo: Mapped[str | None] = mapped_column(String(50))
    metadati_extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text(
        "'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )


# Numero di campi di metadato del modello (esclusi id, documento_id, tenant_id
# — R05, VD-C-080: colonna tecnica dell'ente — e date tecniche): tenuto qui
# perché la documentazione non dichiari un numero falso.
METADATI_ALLEGATO5_CAMPI: tuple[str, ...] = tuple(
    c.name for c in MetadatiAllegato5.__table__.columns
    if c.name not in ("id", "documento_id", "tenant_id", "created_at", "updated_at")
)


class LifecycleEvent(Base):
    """Audit trail degli eventi su un AIP."""
    __tablename__ = "lifecycle_events"
    __table_args__ = (
        Index("idx_lifecycle_aip", "aip_id"),
        # R05 (VD-C-080, migrazione 20261023_038): ente proprio per la RLS
        Index("idx_lifecycle_events_tenant", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    aip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.aip.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: R05 (VD-C-080): ente del PdA, ricavato da ``trg_lifecycle_events_tenant``.
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT",
                   name="fk_lifecycle_events_tenant"),
        nullable=False,
    )
    evento: Mapped[str] = mapped_column(String(100), nullable=False)
    da_stato: Mapped[str | None] = mapped_column(String(50))
    a_stato: Mapped[str | None] = mapped_column(String(50))
    dettagli: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text(
        "'{}'::jsonb"))
    utente_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    aip: Mapped[Aip] = relationship("Aip", back_populates="lifecycle_events")


class Fascicolo(Base):
    """
    Fascicolo informatico (aggregazione documentale) di un cliente: raggruppa
    più AIP correlati. Il codice (IdAggregazione) è unico dentro il cliente.
    Metadati Allegato 5 delle aggregazioni: coperti solo oggetto (titolo),
    DataApertura/DataChiusura, classificazione (classe_doc_id) e tipo;
    gli altri vanno in ``metadati`` finché non esiste il profilo versionato.
    """
    __tablename__ = "fascicoli"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codice", name="uq_fascicoli_tenant_codice"),
        Index("idx_fascicoli_tenant", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_fascicoli_tenant"),
        nullable=False,
    )
    codice: Mapped[str] = mapped_column(String(255), nullable=False)
    titolo: Mapped[str] = mapped_column(String(500), nullable=False)
    descrizione: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(
        String(30), nullable=False, default="GENERICO", server_default=text("'GENERICO'"))
    anno: Mapped[int | None] = mapped_column(Integer)
    responsabile_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL", name="fk_fascicoli_responsabile"),
    )
    classe_doc_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"),
    )
    data_apertura: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date())
    data_chiusura: Mapped[date | None] = mapped_column(Date)
    stato: Mapped[str] = mapped_column(
        String(50), nullable=False, default="APERTO", server_default=text("'APERTO'"))
    metadati: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text(
        "'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )


class FascicoloAip(Base):
    """Associazione M2M tra Fascicolo e AIP. Stesso cliente: verificato dall'API e,
    da R05, dal trigger ``trg_fascicoli_aip_tenant`` (ente del fascicolo = ente del PdA)."""
    __tablename__ = "fascicoli_aip"
    __table_args__ = (
        # R05 (VD-C-080, migrazione 20261023_038): ente proprio per la RLS
        Index("idx_fascicoli_aip_tenant", "tenant_id"),
        {"schema": "public"},
    )

    fascicolo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.fascicoli.id", ondelete="CASCADE"),
        primary_key=True,
    )
    aip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.aip.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    #: R05 (VD-C-080): ente del fascicolo, ricavato da ``trg_fascicoli_aip_tenant``.
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_fascicoli_aip_tenant"),
        nullable=False,
    )
    ruolo: Mapped[str] = mapped_column(
        String(50), nullable=False, default="DOCUMENTO", server_default=text("'DOCUMENTO'"))
    note: Mapped[str | None] = mapped_column(Text)
    data_inserimento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Notification(Base):
    """Storico notifiche e richieste di distribuzione (webhook, PEC, email, API)."""
    __tablename__ = "notifications"
    __table_args__ = (
        Index("idx_notifications_tenant", "tenant_id"),
        Index(
            "idx_notifications_pending", "created_at",
            postgresql_where=text("(stato)::text = 'PENDING'::text"),
        ),
        # Deduplica: la stessa notifica per lo stesso oggetto non si accoda due volte (migrazione
        # 20261020_013)
        Index(
            "uq_notifications_dedup", "tenant_id", "tipo", "destinatario",
            text("(payload_json ->> 'oggetto_id'::text)"), unique=True,
        ),
        # R04 servizi (migrazione 20261023_029, VD-C-102): elenchi a cursore
        Index("idx_notifications_tenant_cursore", "tenant_id", text("created_at DESC"),
              text("id DESC")),
        Index("idx_notifications_pdd_cursore", "tenant_id", text("created_at DESC"),
              text("id DESC"), postgresql_where=text("tipo LIKE 'PDD\\_%'")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_notifications_tenant"),
        nullable=False,
    )
    aip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT")
    )
    tipo: Mapped[str] = mapped_column(String(100), nullable=False)
    canale: Mapped[str] = mapped_column(String(50), nullable=False)
    destinatario: Mapped[str] = mapped_column(String(500), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    stato: Mapped[str] = mapped_column(
        String(50), nullable=False, default="PENDING", server_default=text("'PENDING'"))
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text(
        "0"))
    prossimo_tentativo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inviato_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    errore: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )


class PdndFruitore(Base):
    """
    Fruitore PDND registrato: associa il client_id del voucher PDND
    (Interoperabilità) al cliente VerumDoc per conto del quale versa e consulta.
    Senza riga attiva il voucher è rifiutato (fail-closed, VD-C-021).
    """
    __tablename__ = "pdnd_fruitori"
    __table_args__ = (
        Index("idx_pdnd_fruitori_tenant", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    client_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_pdnd_fruitori_tenant"),
        nullable=False,
    )
    descrizione: Mapped[str | None] = mapped_column(String(500))
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxEvento(Base):
    """
    Outbox transazionale (migrazione 20261020_011, REGOLE §12.3, VD-C-015):
    l'evento si scrive nella stessa transazione del cambio di stato e un relay
    lo pubblica su Kafka. Logica in libs/shared/outbox.py.

    R04 dati (migrazione 20261023_028, VD-C-076): partizionata per GIORNO su
    ``creato_il`` (le partizioni pubblicate oltre la retention si staccano);
    ``origine`` = servizio che ha scritto l'evento (ogni servizio ha il suo
    relay); ``gruppo`` = ``hashtext(chiave) & 63`` per l'ordine per chiave fra
    più repliche. PK ``(id, creato_il)``.
    """
    __tablename__ = "outbox_eventi"
    __table_args__ = (
        Index(
            "idx_outbox_in_sospeso", "origine", "sequenza",
            postgresql_where=text("pubblicato_il IS NULL"),
        ),
        # R04 correzioni (migrazione 20261023_032): sonda dei gruppi con eventi in attesa
        Index(
            "idx_outbox_gruppi_in_sospeso", "origine", "gruppo", "sequenza",
            postgresql_where=text("pubblicato_il IS NULL"),
        ),
        # R04 secondo giro (migrazione 20261023_033, N1): chiavi con eventi in
        # attesa dietro un topic sospeso (ordine per chiave)
        Index(
            "idx_outbox_chiavi_in_sospeso", "origine", "chiave",
            postgresql_where=text("pubblicato_il IS NULL"),
        ),
        CheckConstraint("gruppo BETWEEN 0 AND 63", name="ck_outbox_eventi_gruppo"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    # Ordine totale di pubblicazione, assegnato dal database all'INSERT (sequenza:
    # le colonne IDENTITY non sono ammesse sulle tabelle partizionate in PostgreSQL 16)
    sequenza: Mapped[int] = mapped_column(
        BigInteger, Sequence("outbox_eventi_seq", schema="public"), nullable=False,
    )
    origine: Mapped[str] = mapped_column(String(40), nullable=False)
    gruppo: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT")
    )
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    chiave: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), primary_key=True
    )
    pubblicato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text(
        "0"))
    ultimo_errore: Mapped[str | None] = mapped_column(Text)
    # R04 correzioni (migrazione 20261023_031): evento bloccato scartato da un
    # operatore dalle code di scarto (pubblicato_il = istante dello scarto)
    scartato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxChiaveFerma(Base):
    """
    Chiave dell'outbox ferma per un errore del SUO evento (R04 correzioni,
    migrazione 20261023_031, rev4 rilievo 2): i suoi eventi sono esclusi dalle
    letture normali del relay, il primo si ritenta con pausa crescente; dopo
    ``OUTBOX_MAX_TENTATIVI`` la chiave è BLOCCATA (``bloccato_il``) e si gestisce
    dalle code di scarto. ``chiave`` = chiave dell'evento, o il suo id se senza.
    """
    __tablename__ = "outbox_chiavi_ferme"
    __table_args__ = (
        Index("uq_outbox_chiavi_ferme_evento", "evento_id", unique=True),
        Index("idx_outbox_chiavi_ferme_riprova", "origine", "prossimo_tentativo_il",
              postgresql_where=text("bloccato_il IS NULL")),
        CheckConstraint("gruppo BETWEEN 0 AND 63", name="ck_outbox_chiavi_ferme_gruppo"),
        {"schema": "public"},
    )

    origine: Mapped[str] = mapped_column(String(40), primary_key=True)
    chiave: Mapped[str] = mapped_column(String(200), primary_key=True)
    gruppo: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    evento_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    evento_creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sequenza: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False)
    ultimo_errore: Mapped[str | None] = mapped_column(Text)
    prossimo_tentativo_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bloccato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fermata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class OutboxTopicSospeso(Base):
    """Topic dell'outbox SOSPESO per un errore del topic o dell'autorizzazione
    (R04 secondo giro, migrazione 20261023_033, rilievo N1): il relay non ne
    pubblica gli eventi fino a ``sospeso_fino_a`` e poi fa UN tentativo; la
    pausa raddoppia fino al massimo; i tentativi delle chiavi non si consumano.
    Riga eliminata al primo invio riuscito (topic ripristinato)."""
    __tablename__ = "outbox_topic_sospesi"
    __table_args__ = ({"schema": "public"},)

    origine: Mapped[str] = mapped_column(String(40), primary_key=True)
    topic: Mapped[str] = mapped_column(String(200), primary_key=True)
    sospeso_fino_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pausa_secondi: Mapped[float] = mapped_column(Float, nullable=False)
    sospensioni: Mapped[int] = mapped_column(Integer, nullable=False)
    ultimo_errore: Mapped[str | None] = mapped_column(Text)
    dal: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class ManutenzioneLease(Base):
    """Lease dei lavori di manutenzione del database (R04 correzioni, migrazione
    20261023_031): una replica alla volta senza lock consultivi di sessione
    (compatibile con PgBouncer in modalità transaction)."""
    __tablename__ = "manutenzione_lease"
    __table_args__ = ({"schema": "public"},)

    nome: Mapped[str] = mapped_column(String(100), primary_key=True)
    detentore: Mapped[str] = mapped_column(String(200), nullable=False)
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class VersamentoImpronta(Base):
    """
    Impronte e chiave di idempotenza di ogni PdV (migrazione 20261020_011,
    REGOLE §12.1, VD-C-016): unicità per cliente della Idempotency-Key e
    dell'impronta dell'indice fra i PdV non rifiutati (``attivo``).
    """
    __tablename__ = "versamento_impronte"
    __table_args__ = (
        Index(
            "uq_versamento_idempotenza", "tenant_id", "chiave_idempotenza", unique=True,
            postgresql_where=text("chiave_idempotenza IS NOT NULL"),
        ),
        Index(
            "uq_versamento_impronta_attiva", "tenant_id", "impronta_indice", unique=True,
            postgresql_where=text("attivo"),
        ),
        # Migrazione 20261021_014 (VD-C-095): chiave dell'unità documentaria
        # (registro, anno, numero) unica per cliente fra i PdV non rifiutati.
        # R07 (D17, migrazione 20261030_046): versamenti SENZA Idempotency-Key
        Index("idx_versamento_impronte_senza_chiave", "created_at",
              postgresql_where=text("chiave_idempotenza IS NULL")),
        Index(
            "uq_versamento_chiave_ud_attiva", "tenant_id", "chiave_ud_registro",
            "chiave_ud_anno", "chiave_ud_numero", unique=True,
            postgresql_where=text("attivo AND chiave_ud_numero IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    pdv_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.pdv.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    impronta_indice: Mapped[str] = mapped_column(String(64), nullable=False)
    impronta_richiesta: Mapped[str] = mapped_column(String(64), nullable=False)
    chiave_idempotenza: Mapped[str | None] = mapped_column(String(200))
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Chiave UD normalizzata (registro maiuscolo senza spazi ai lati, '' se assente;
    # anno e numero senza spazi ai lati). NULL se l'indice non ha anno e numero.
    chiave_ud_registro: Mapped[str | None] = mapped_column(String(100))
    chiave_ud_anno: Mapped[str | None] = mapped_column(String(10))
    chiave_ud_numero: Mapped[str | None] = mapped_column(String(100))


class ImpostazioneValore(Base):
    """
    Valore di un'impostazione del motore delle regole a un livello della catena
    (migrazione 20261021_014; REGOLE §8.1-§8.2, decisione VD-P-01).

    Livelli: CONSERVATORE (nessun cliente né classe), ENTE (solo cliente),
    CLASSE (classe documentale; per le classi di base ``tenant_id`` è NULL).
    I codici sono quelli del registro di ``libs.verumdoc_rules``; il valore è
    JSON nel tipo dell'impostazione. Per METADATI_OBBLIGATORI, FORMATI_AMMESSI
    e CLASSE_DIMENSIONE_MAX_MB al livello CLASSE valgono le colonne di
    ``classi_documentali``. Lettura: gateway ``app/services/regole_db.py``.
    Scrittura con motivo e audit: console di configurazione (R05).
    """
    __tablename__ = "impostazioni_valori"
    __table_args__ = (
        UniqueConstraint(
            "livello", "tenant_id", "classe_id", "codice", name="uq_impostazioni_valori_livello",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(livello = 'CONSERVATORE' AND tenant_id IS NULL AND classe_id IS NULL) OR "
            "(livello = 'ENTE' AND tenant_id IS NOT NULL AND classe_id IS NULL) OR "
            "(livello = 'CLASSE' AND classe_id IS NOT NULL)",
            name="ck_impostazioni_valori_livello",
        ),
        Index("idx_impostazioni_valori_tenant", "tenant_id"),
        Index("idx_impostazioni_valori_classe", "classe_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    livello: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
    )
    classe_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"),
    )
    codice: Mapped[str] = mapped_column(String(100), nullable=False)
    valore: Mapped[Any] = mapped_column(JSONB, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text)
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProfiloMetadatiClasse(Base):
    """
    Versione di un profilo di metadati di una classe documentale (migrazione
    20261021_014; REGOLE §8.4, §8.8). Una versione applicata non si modifica: se
    ne crea una nuova con ``valido_dal``. ``campi`` è l'elenco dei metadati
    ``{nome, tipo, obbligatorio, sensibile, descrizione, valori_ammessi}`` letto
    da ``libs.verumdoc_rules.profili.CampoProfilo.da_dict``.
    """
    __tablename__ = "profili_metadati"
    __table_args__ = (
        UniqueConstraint("classe_id", "versione", name="uq_profili_metadati_versione"),
        CheckConstraint("versione >= 1", name="ck_profili_metadati_versione"),
        CheckConstraint(
            "valido_al IS NULL OR valido_al >= valido_dal", name="ck_profili_metadati_validita",
        ),
        Index("idx_profili_metadati_classe", "classe_id", "valido_dal"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    classe_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"),
        nullable=False,
    )
    versione: Mapped[int] = mapped_column(Integer, nullable=False)
    valido_dal: Mapped[date] = mapped_column(Date, nullable=False)
    valido_al: Mapped[date | None] = mapped_column(Date)
    campi: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    descrizione: Mapped[str | None] = mapped_column(Text)
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── R03 b2: canale SFTP (migrazione 20261021_016) ─────────────────────

class SftpAccount(Base):
    """
    Account SFTP di un ente (servizio ``sftp-ingest``, piano R03 §3.2).

    ``nome_utente`` è il nome di accesso SSH (unico fra tutti gli enti);
    ``responsabile_id`` è l'utente dell'ente per conto del quale il canale versa
    (token di servizio del contratto C2): stesse regole del responsabile delle
    chiavi API. Gestione: onboarding ``app/routers/sftp.py``. Un account non si
    elimina: si sospende (``stato = SOSPESO``), e torna attivo con «riattiva».
    """
    __tablename__ = "sftp_account"
    __table_args__ = (
        UniqueConstraint("nome_utente", name="uq_sftp_account_nome_utente"),
        CheckConstraint("stato IN ('ATTIVO', 'SOSPESO')", name="ck_sftp_account_stato"),
        CheckConstraint("nome_utente ~ '^[a-z0-9][a-z0-9._-]{2,63}$'",
                        name="ck_sftp_account_nome_utente"),
        Index("idx_sftp_account_tenant", "tenant_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False,
    )
    nome_utente: Mapped[str] = mapped_column(String(64), nullable=False)
    descrizione: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default=text("''"))
    responsabile_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"), nullable=False,
    )
    stato: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ATTIVO", server_default=text("'ATTIVO'"))
    sospeso_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# R10 (D12, migrazione 20261102_049): elenco degli indirizzi ammessi per account
SftpAccount.ip_ammessi = mapped_column(  # type: ignore[attr-defined]
    "ip_ammessi", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
SftpAccount.__table__.append_constraint(CheckConstraint(
    "cardinality(ip_ammessi) <= 500", name="ck_sftp_account_ip_ammessi"))


class SftpChiave(Base):
    """
    Chiave pubblica SSH di un account SFTP (solo autenticazione a chiave).

    ``chiave_pubblica`` è «tipo base64» in formato OpenSSH (senza commento);
    ``impronta`` è «SHA256:<base64>» come la mostra ``ssh-keygen -l``. Una chiave
    non si elimina: si revoca (``revocata_il``); fra le chiavi attive di un
    account l'impronta è unica.
    """
    __tablename__ = "sftp_chiavi"
    __table_args__ = (
        Index("idx_sftp_chiavi_account", "account_id"),
        Index("uq_sftp_chiavi_attiva", "account_id", "impronta", unique=True,
              postgresql_where=text("revocata_il IS NULL")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(
            "public.sftp_account.id", ondelete="RESTRICT"), nullable=False,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False,
    )
    algoritmo: Mapped[str] = mapped_column(String(50), nullable=False)
    bit: Mapped[int | None] = mapped_column(Integer)
    chiave_pubblica: Mapped[str] = mapped_column(Text, nullable=False)
    impronta: Mapped[str] = mapped_column(String(100), nullable=False)
    commento: Mapped[str | None] = mapped_column(String(200))
    creata_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revocata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocata_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SftpLavorazione(Base):
    """
    Presa in carico di una cartella SFTP da parte di un lavoratore (REGOLE §12.9).

    Una riga per tentativo di versamento di ``in/<cartella>``: ``stato`` IN_CORSO
    (al massimo una per account e cartella: indice unico parziale), VERSATA o
    SCARTATA. ``lease_scade_il`` è la scadenza del lease del ``detentore``; per
    una riga IN_CORSO senza detentore è l'istante del prossimo tentativo. Un
    lease scaduto = tentativo fallito, ripreso da un'altra replica.
    ``caricamenti`` mappa ``nome_file → {id, gen}`` delle sessioni di
    caricamento (contratto C1) per riprendere dopo un crash senza ricaricare.
    ``pdv_id`` non ha FK: il canale non dipende dallo schema dei PdV (il PdV
    lo crea il gateway).
    """
    __tablename__ = "sftp_lavorazioni"
    __table_args__ = (
        CheckConstraint("stato IN ('IN_CORSO', 'VERSATA', 'SCARTATA')",
                        name="ck_sftp_lavorazioni_stato"),
        Index("uq_sftp_lavorazioni_attiva", "account_id", "cartella", unique=True,
              postgresql_where=text("stato = 'IN_CORSO'")),
        Index("idx_sftp_lavorazioni_cartella", "account_id", "cartella", "created_at"),
        Index("idx_sftp_lavorazioni_lease", "lease_scade_il",
              postgresql_where=text("stato = 'IN_CORSO'")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(
            "public.sftp_account.id", ondelete="RESTRICT"), nullable=False,
    )
    cartella: Mapped[str] = mapped_column(String(128), nullable=False)
    stato: Mapped[str] = mapped_column(
        String(20), nullable=False, default="IN_CORSO", server_default=text("'IN_CORSO'"))
    detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text(
        "0"))
    impronta_indice: Mapped[str | None] = mapped_column(String(64))
    chiave_idempotenza: Mapped[str | None] = mapped_column(String(200))
    caricamenti: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    pdv_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    esito: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ultimo_errore: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    concluso_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── R03 b3: eventi per i clienti e webhook (migrazione 20261021_017) ──


class SftpSegnalazione(Base):
    """
    Account SFTP con attività da guardare (R03 f2, REV-15): scritta dal server
    SFTP (a lotti) quando un cliente scrive o rinomina in ``in/`` e dal
    controllo completo periodico; presa in lease da una replica del lavoratore
    (``detentore``, ``lease_scade_il``), eliminata quando l'account non ha più
    cartelle da lavorare e nessuno l'ha segnalato di nuovo (``versione``).
    """
    __tablename__ = "sftp_segnalazioni"
    __table_args__ = (
        Index("idx_sftp_segnalazioni_da_guardare", "da_guardare_dal"),
        {"schema": "public"},
    )

    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.sftp_account.id", ondelete="CASCADE",
                   name="fk_sftp_segnalazioni_account"),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_sftp_segnalazioni_tenant"),
        nullable=False,
    )
    versione: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    da_guardare_dal: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    controllo_completo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))
    detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    segnalata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class EventoCliente(Base):
    """
    Evento visibile all'ente (contratto C3, VD-C-078): alimentato dal consumer
    di notification sugli eventi Kafka. ``id`` crescente = cursore di
    ``GET /api/v1/eventi``. Un evento per (ente, tipo, risorsa): l'indice
    unico rende idempotente la riconsegna Kafka. ``dati`` non porta dati
    personali né chiavi interne di storage (REGOLE §19.9).

    R04 dati (migrazione 20261023_028, VD-C-103): partizionata per MESE su
    ``creato_il``, retention ``RETENZIONE_EVENTI_CLIENTE_GIORNI`` (predefinito 90)
    con distacco delle partizioni; PK ``(id, creato_il)``. La deduplica per
    (ente, tipo, risorsa) la fa ``registra_evento`` sotto lock consultivo
    (un vincolo unico dovrebbe contenere ``creato_il``).
    """
    __tablename__ = "eventi_cliente"
    __table_args__ = (
        Index("idx_eventi_cliente_risorsa", "tenant_id", "tipo", "risorsa_id"),
        # R03 f2 (migrazione 20261021_022): cursore sulla posizione di pubblicazione
        Index("idx_eventi_cliente_posizione", "posizione",
              postgresql_where=text("posizione IS NOT NULL")),
        Index("idx_eventi_cliente_tenant_posizione", "tenant_id", "posizione",
              postgresql_where=text("posizione IS NOT NULL")),
        Index("idx_eventi_cliente_tenant_tipo_posizione", "tenant_id", "tipo", "posizione",
              postgresql_where=text("posizione IS NOT NULL")),
        Index("idx_eventi_cliente_da_pubblicare", "id",
              postgresql_where=text("posizione IS NULL")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("eventi_cliente_seq", schema="public"), primary_key=True,
        autoincrement=False)
    # R04 correzioni (migrazione 20261023_031): NULL = evento GLOBALE, letto da
    # tutti gli enti (catena.checkpoint), invece di una copia per ente
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_eventi_cliente_tenant"),
        nullable=True,
    )
    tipo: Mapped[str] = mapped_column(String(60), nullable=False)
    risorsa_tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    risorsa_id: Mapped[str] = mapped_column(String(100), nullable=False)
    dati: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text(
        "'{}'::jsonb"))
    origine: Mapped[str | None] = mapped_column(String(200))
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), primary_key=True
    )
    #: Posizione nell'elenco degli eventi, assegnata dopo il commit dal passo di
    #: pubblicazione seriale (NULL = non ancora pubblicato). È il cursore.
    posizione: Mapped[int | None] = mapped_column(BigInteger)


class EventoClientePubblicazione(Base):
    """Contatore unico (id = 1) del passo di pubblicazione degli eventi (R03 f2,
    REV-13): la riga bloccata serializza l'assegnazione delle posizioni."""
    __tablename__ = "eventi_cliente_pubblicazione"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_eventi_cliente_pubblicazione_unica"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    ultima_posizione: Mapped[int] = mapped_column(BigInteger, nullable=False)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WebhookSottoscrizione(Base):
    """
    Sottoscrizione webhook di un ente (contratto C3): URL https, tipi di
    evento, segreto HMAC cifrato a riposo (AES-GCM, chiave d'ambiente del
    servizio notification), mai in chiaro nel database né nei log.
    Eliminazione logica (``eliminata_il``): le consegne restano come storico.
    """
    __tablename__ = "webhook_sottoscrizioni"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(tipi) = 'array'", name="ck_webhook_sottoscrizioni_tipi"),
        Index("idx_webhook_sottoscrizioni_tenant", "tenant_id",
              postgresql_where=text("eliminata_il IS NULL")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "public.tenants.id", ondelete="RESTRICT", name="fk_webhook_sottoscrizioni_tenant"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    tipi: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    descrizione: Mapped[str | None] = mapped_column(String(200))
    segreto_cifrato: Mapped[str] = mapped_column(Text, nullable=False)
    segreto_suffisso: Mapped[str] = mapped_column(String(8), nullable=False)
    attiva: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text(
        "true"))
    creata_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    creata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    aggiornata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    eliminata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # R13b (migrazione 20261108_055): rotazione del segreto con doppia firma nel
    # periodo di transizione (segreto precedente cifrato come il corrente)
    segreto_precedente_cifrato: Mapped[str | None] = mapped_column(Text)
    segreto_precedente_suffisso: Mapped[str | None] = mapped_column(String(8))
    segreto_precedente_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    segreto_ruotato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookConsegna(Base):
    """
    Consegna di un evento a una sottoscrizione (contratto C3). Stati:
    IN_CODA → IN_CONSEGNA (lease del lavoratore) → CONSEGNATA | IN_CODA (con
    prossimo tentativo) | FALLITA (ritentativi esauriti: coda di scarto
    consultabile e ripetibile, REGOLE §12.4) | ANNULLATA (webhook eliminato).

    R04 dati (migrazione 20261023_028, VD-C-103): partizionata per MESE su
    ``evento_creato_il`` (= ``creato_il`` dell'evento): stesse partizioni degli
    eventi, staccate insieme a fine retention. Nessuna chiave esterna verso
    ``eventi_cliente`` (su tabelle partizionate il distacco la verificherebbe
    sull'intera tabella sotto lock): consegna ed evento nascono nella stessa
    istruzione. PK ``(id, evento_creato_il)``.
    """
    __tablename__ = "webhook_consegne"
    __table_args__ = (
        UniqueConstraint("sottoscrizione_id", "evento_id", "evento_creato_il",
                         name="uq_webhook_consegne_evento"),
        CheckConstraint(
            "stato IN ('IN_CODA', 'IN_CONSEGNA', 'CONSEGNATA', 'FALLITA', 'ANNULLATA')",
            name="ck_webhook_consegne_stato",
        ),
        Index("idx_webhook_consegne_da_fare", "prossimo_tentativo",
              postgresql_where=text("stato IN ('IN_CODA', 'IN_CONSEGNA')")),
        Index("idx_webhook_consegne_sottoscrizione", "sottoscrizione_id", "id"),
        Index("idx_webhook_consegne_sottoscrizione_stato", "sottoscrizione_id", "stato", "id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("webhook_consegne_seq", schema="public"), primary_key=True,
        autoincrement=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_webhook_consegne_tenant"),
        nullable=False,
    )
    sottoscrizione_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.webhook_sottoscrizioni.id", ondelete="RESTRICT",
                   name="fk_webhook_consegne_sottoscrizione"),
        nullable=False,
    )
    evento_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evento_creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True)
    stato: Mapped[str] = mapped_column(String(20), nullable=False)
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text(
        "0"))
    riprove: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text(
        "0"))
    prossimo_tentativo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ultimo_stato_http: Mapped[int | None] = mapped_column(SmallInteger)
    ultimo_codice: Mapped[str | None] = mapped_column(String(20))
    ultimo_errore: Mapped[str | None] = mapped_column(Text)
    ultimo_tentativo_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consegnata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    aggiornata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── R03 b4: indicizzazione e pacchetti di distribuzione (migrazione 20261021_018) ──

class IndicizzazioneAip(Base):
    """
    Coda transazionale dell'indicizzazione di ricerca (VD-C-093): una riga per
    PdA, scritta da aip-builder nella STESSA transazione dell'INSERT del PdA (è
    l'«evento d'indicizzazione»: nessun evento perso fra commit e pubblicazione).
    Il lavoratore di distribution (``app/services/indicizzazione.py``) prende le
    righe IN_CODA a lotti con ``FOR UPDATE SKIP LOCKED``, le indicizza su
    OpenSearch con la bulk API (id documento = id del PdA: idempotente) e le
    marca INDICIZZATO; un errore riprogramma con i ritardi costanti
    (REGOLE §12.4) e dopo l'ultimo la riga resta in ERRORE (consultabile).
    """
    __tablename__ = "indicizzazione_aip"
    __table_args__ = (
        CheckConstraint("stato IN ('IN_CODA', 'INDICIZZATO', 'ERRORE')",
                        name="ck_indicizzazione_aip_stato"),
        Index("idx_indicizzazione_aip_coda", "prossimo_tentativo",
              postgresql_where=text("stato = 'IN_CODA'")),
        # R04 servizi (migrazione 20261023_029, VD-C-075): righe in ERRORE per le
        # code di scarto consultabili
        Index("idx_indicizzazione_aip_errore", "prossimo_tentativo", "aip_id",
              postgresql_where=text("stato = 'ERRORE'")),
        Index("idx_indicizzazione_aip_tenant", "tenant_id"),
        {"schema": "public"},
    )

    aip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT"), primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stato: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'IN_CODA'"))
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    prossimo_tentativo: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ultimo_errore: Mapped[str | None] = mapped_column(Text)
    richiesta_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    indicizzato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PacchettoDistribuzione(Base):
    """
    Pacchetto di distribuzione (PdD) richiesto a distribution (VD-C-077).

    ``id`` coincide con la riga del registro delle distribuzioni
    (``notifications`` di tipo PDD_*). ``consegna``: LINK (collegamenti
    prefirmati ai file reali, impronte verificate alla richiesta) o ZIP
    (pacchetto generato in streaming da un lavoratore con lease, fuori dalla
    richiesta HTTP; evento ``pdd.pronto`` all'esito). ``documenti`` elenca i
    file inclusi con le impronte registrate alla conservazione.
    """
    __tablename__ = "pacchetti_distribuzione"
    __table_args__ = (
        CheckConstraint("tipo IN ('COMPLETO', 'ESTRATTO', 'ESIBIZIONE', 'MIGRAZIONE')",
                        name="ck_pacchetti_distribuzione_tipo"),
        CheckConstraint("consegna IN ('LINK', 'ZIP')", name="ck_pacchetti_distribuzione_consegna"),
        CheckConstraint("stato IN ('IN_PREPARAZIONE', 'PRONTO', 'ERRORE', 'SCADUTO')",
                        name="ck_pacchetti_distribuzione_stato"),
        Index("idx_pacchetti_distribuzione_coda", "prossimo_tentativo",
              postgresql_where=text("stato = 'IN_PREPARAZIONE'")),
        Index("idx_pacchetti_distribuzione_scadenza", "scade_il",
              postgresql_where=text("stato = 'PRONTO'")),
        Index("idx_pacchetti_distribuzione_tenant", "tenant_id", "creato_il"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    aip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT"), nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    consegna: Mapped[str] = mapped_column(String(10), nullable=False)
    stato: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'IN_PREPARAZIONE'")
    )
    richiedente_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    canale: Mapped[str | None] = mapped_column(String(20))
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    file_richiesti: Mapped[list[Any] | None] = mapped_column(JSONB)
    opzioni: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    documenti: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    hash_pdd: Mapped[str | None] = mapped_column(String(64))
    zip_bucket: Mapped[str | None] = mapped_column(String(100))
    zip_key: Mapped[str | None] = mapped_column(String(500))
    zip_dimensione: Mapped[int | None] = mapped_column(BigInteger)
    zip_sha256: Mapped[str | None] = mapped_column(String(64))
    durata_link_secondi: Mapped[int] = mapped_column(Integer, nullable=False)
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    prossimo_tentativo: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_fino_al: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_errore: Mapped[str | None] = mapped_column(Text)
    errore: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    pronto_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))



# ── R03 b1: sessioni di caricamento (migrazione 20261021_015) ──

from sqlalchemy import LargeBinary as _LargeBinary  # noqa: E402 — solo per Caricamento.testa

#: Stati di una sessione di caricamento (contratto C1).
STATI_CARICAMENTO = ("APERTO", "IN_VERIFICA", "COMPLETO", "SCADUTO", "ANNULLATO", "ERRORE")


class Caricamento(Base):
    """
    Sessione di caricamento di un file (contratto C1, piano R03 §3.1).

    Il file arriva a parti (S3 multipart nello staging, ``chiave_oggetto`` sotto
    ``caricamenti/{tenant_id}/``); al completamento un verificatore fuori dalla
    richiesta HTTP assembla l'oggetto e calcola in streaming ``impronte`` (tutti
    gli algoritmi ammessi), ``testa`` (primi byte, per il formato) e
    ``formato_rilevato``. Il versamento per riferimento usa la sessione una
    volta sola (``pdv_id``, nella transazione del PdV). Stati: APERTO →
    IN_VERIFICA → COMPLETO | ERRORE; APERTO/COMPLETO non usata → SCADUTO;
    ANNULLATO su richiesta. Gestione: gateway ``app/services/caricamenti.py``.
    """
    __tablename__ = "caricamenti"
    __table_args__ = (
        CheckConstraint(
            "stato IN ('APERTO', 'IN_VERIFICA', 'COMPLETO', 'SCADUTO', 'ANNULLATO', 'ERRORE')",
            name="ck_caricamenti_stato",
        ),
        CheckConstraint("dimensione > 0", name="ck_caricamenti_dimensione"),
        CheckConstraint("dimensione_parte > 0 AND numero_parti >= 1", name="ck_caricamenti_parti"),
        Index("idx_caricamenti_tenant", "tenant_id", "creato_il"),
        Index("uq_caricamenti_idempotenza", "tenant_id", "chiave_idempotenza", unique=True,
              postgresql_where=text("chiave_idempotenza IS NOT NULL")),
        Index("idx_caricamenti_da_verificare", "lease_scade_il",
              postgresql_where=text("stato = 'IN_VERIFICA'")),
        Index("idx_caricamenti_da_pulire", "scade_il",
              postgresql_where=text("pdv_id IS NULL AND storage_eliminato_il IS NULL")),
        Index("idx_caricamenti_pdv", "pdv_id", postgresql_where=text("pdv_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False,
    )
    utente_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    canale: Mapped[str] = mapped_column(String(20), nullable=False)
    chiave_idempotenza: Mapped[str | None] = mapped_column(String(200))
    impronta_richiesta: Mapped[str] = mapped_column(String(64), nullable=False)
    nome_file: Mapped[str] = mapped_column(String(255), nullable=False)
    dimensione: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_dichiarato: Mapped[str | None] = mapped_column(String(255))
    impronta_dichiarata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dimensione_parte: Mapped[int] = mapped_column(BigInteger, nullable=False)
    numero_parti: Mapped[int] = mapped_column(Integer, nullable=False)
    stato: Mapped[str] = mapped_column(
        String(20), nullable=False, default="APERTO", server_default=text("'APERTO'"))
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    chiave_oggetto: Mapped[str] = mapped_column(String(1024), nullable=False)
    upload_id: Mapped[str | None] = mapped_column(String(1024))
    impronte: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    testa: Mapped[bytes | None] = mapped_column(_LargeBinary)
    formato_rilevato: Mapped[str | None] = mapped_column(String(255))
    errore: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tentativi_verifica: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    pdv_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.pdv.id", ondelete="RESTRICT"),
    )
    consumato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_eliminato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CaricamentoParte(Base):
    """
    Parte ricevuta di una sessione di caricamento (contratto C1): numero (da 1),
    dimensione, SHA-256 calcolato sui byte ricevuti ed ETag dello storage
    (``NULL`` finché la parte non è scritta). Stessa parte con lo stesso
    contenuto: idempotente; con contenuto diverso: conflitto (409).
    """
    __tablename__ = "caricamenti_parti"
    __table_args__ = (
        CheckConstraint("numero >= 1", name="ck_caricamenti_parti_numero"),
        {"schema": "public"},
    )

    caricamento_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.caricamenti.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    numero: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"), nullable=False,
    )
    dimensione: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(200))
    ricevuta_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── R04 servizi (migrazione 20261023_029) ────────────────────────────

class Scarto(Base):
    """
    Messaggio finito in una coda di scarto (VD-C-075, REGOLE §12.4): DLQ Kafka
    (``pdv.received.dlq``, ``pdv.validated.dlq``, ``notification.dlq``) o riga
    in errore definitivo di una coda in tabella (``indicizzazione_aip``).
    Scritta in modo idempotente dal consumer delle code di scarto di
    dashboard-api (chiave unica ``chiave_origine``); gestita dal conservatore
    con «Rielabora» o «Archivia» (chi, quando, motivo). Identificativi senza
    chiave esterna: un messaggio scartato può citarne di inesistenti.
    """
    __tablename__ = "scarti"
    __table_args__ = (
        UniqueConstraint("chiave_origine", name="uq_scarti_chiave_origine"),
        CheckConstraint("fonte IN ('KAFKA', 'TABELLA')", name="ck_scarti_fonte"),
        CheckConstraint("stato IN ('DA_GESTIRE', 'RIELABORATO', 'ARCHIVIATO')",
                        name="ck_scarti_stato"),
        Index("idx_scarti_stato_cursore", "stato", text("id DESC")),
        Index("idx_scarti_coda_stato_cursore", "coda", "stato", text("id DESC")),
        Index("idx_scarti_tenant_stato_cursore", "tenant_id", "stato", text("id DESC")),
        Index("idx_scarti_pdv", "pdv_id", postgresql_where=text("pdv_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    chiave_origine: Mapped[str] = mapped_column(String(300), nullable=False)
    fonte: Mapped[str] = mapped_column(String(20), nullable=False)
    coda: Mapped[str] = mapped_column(String(100), nullable=False)
    topic_origine: Mapped[str | None] = mapped_column(String(100))
    partizione: Mapped[int | None] = mapped_column(Integer)
    posizione: Mapped[int | None] = mapped_column(BigInteger)
    servizio: Mapped[str] = mapped_column(String(50), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    pdv_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    aip_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    codice: Mapped[str | None] = mapped_column(String(60))
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    scartato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    messaggio: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False,
                                       server_default=text("'DA_GESTIRE'"))
    gestito_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gestito_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    motivo_gestione: Mapped[str | None] = mapped_column(Text)
    rielaborazioni: Mapped[int] = mapped_column(Integer, nullable=False,
                                                server_default=text("0"))
    registrato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )



class ScartoRipartenza(Base):
    """
    Una ripartenza («Rielabora») per PdV e per episodio d'errore (R04 correzioni,
    migrazione 20261023_031, rev4 rilievo 4): ``errore_il`` = istante in cui il
    PdV è entrato in ERRORE (``pdv.data_aggiornamento``). Il vincolo di chiave
    impedisce due eventi di ripartenza per lo stesso episodio anche con più
    scarti dello stesso PdV o richieste concorrenti.
    """
    __tablename__ = "scarti_ripartenze"
    __table_args__ = ({"schema": "public"},)

    pdv_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.pdv.id", ondelete="RESTRICT"), primary_key=True)
    errore_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    scarto_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("public.scarti.id", ondelete="RESTRICT"), nullable=False)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

class RichiestaIdempotente(Base):
    """
    Idempotency-Key delle richieste che creano una risorsa fuori dal versamento
    (VD-C-106): ``POST /api/v2/distribution/richiedi``, ``POST /api/v1/webhook``.
    (ente, ambito, chiave) → impronta della richiesta e risorsa creata: stessa
    chiave e stessa richiesta = stessa risorsa (200), richiesta diversa = 409.
    Gestita da ``libs.shared.security.idempotenza``; le righe scadute si
    riusano o si eliminano a lotti.
    """
    __tablename__ = "richieste_idempotenti"
    __table_args__ = (
        CheckConstraint("stato IN ('IN_CORSO', 'COMPLETATA')",
                        name="ck_richieste_idempotenti_stato"),
        Index("idx_richieste_idempotenti_scadenza", "scade_il"),
        {"schema": "public"},
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT",
                   name="fk_richieste_idempotenti_tenant"),
        primary_key=True,
    )
    ambito: Mapped[str] = mapped_column(String(60), primary_key=True)
    chiave: Mapped[str] = mapped_column(String(200), primary_key=True)
    impronta: Mapped[str] = mapped_column(String(64), nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False)
    risorsa_id: Mapped[str | None] = mapped_column(String(100))
    creata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    aggiornata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

# ── R04 verifiche: verifica continua dell'integrità (migrazione 20261023_027) ──
# Piano R03–R05 §5 punto 4; REGOLE §12.9 (lease), §15.8 (esecuzioni come
# evidenza), §22.1 (verifica periodica). Lavoratore:
# services/hashchain/app/verifica_continua/.

#: Esiti del controllo di un oggetto dello storage conservativo.
ESITI_VERIFICA: tuple[str, ...] = (
    "OK", "DIVERSO", "MANCANTE", "RETENTION_ASSENTE", "ERRORE_LETTURA",
)
#: Componenti di un PdA rilette da storage: manifest del PdA, IPdA, file, RdV.
COMPONENTI_VERIFICA: tuple[str, ...] = ("PDA", "IPDA", "FILE", "RDV")


def _elenco_sql_verifiche(valori: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valori)


class VerificaRichiesta(Base):
    """
    Verifica su richiesta (``POST /api/v1/hashchain/verifiche``) di un insieme
    di PdA o di tutti i PdA di un ente. I PdA coinvolti si marcano in
    ``verifiche_programmazione.richiesta_id`` e passano davanti alla rotazione.
    ``tenant_id`` NULL solo per un elenco di PdA di enti diversi (personale del
    conservatore). Al più una richiesta d'ente attiva per ente (indice unico).
    """
    __tablename__ = "verifiche_richieste"
    __table_args__ = (
        CheckConstraint("ambito IN ('PDA', 'ENTE')", name="ck_verifiche_richieste_ambito"),
        CheckConstraint(
            "stato IN ('IN_PREPARAZIONE', 'IN_CORSO', 'COMPLETATA')",
            name="ck_verifiche_richieste_stato",
        ),
        CheckConstraint(
            "ambito = 'PDA' OR tenant_id IS NOT NULL", name="ck_verifiche_richieste_ente",
        ),
        Index("idx_verifiche_richieste_tenant", "tenant_id", text("creata_il DESC")),
        Index(
            "idx_verifiche_richieste_da_preparare", "creata_il",
            postgresql_where=text("stato = 'IN_PREPARAZIONE'"),
        ),
        Index(
            "uq_verifiche_richieste_ente_attiva", "tenant_id", unique=True,
            postgresql_where=text("ambito = 'ENTE' AND stato <> 'COMPLETATA'"),
        ),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
    )
    ambito: Mapped[str] = mapped_column(String(10), nullable=False)
    stato: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'IN_PREPARAZIONE'"),
    )
    # Id dei PdA richiesti (ambito PDA); NULL per l'ambito ENTE
    pda_richiesti: Mapped[list[Any] | None] = mapped_column(JSONB)
    richiesta_da: Mapped[str | None] = mapped_column(String(100))
    ruolo: Mapped[str] = mapped_column(String(50), nullable=False)
    oggetti_totali: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"))
    oggetti_verificati: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"))
    # PdA già marcati da un'altra richiesta attiva: li verifica quella
    gia_in_verifica: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"))
    anomalie: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    preparazione_conclusa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))
    # Ultimo PdA marcato (ambito ENTE, marcatura a lotti per chiave)
    cursore_preparazione: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    creata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    conclusa_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificaProgrammazione(Base):
    """
    Programmazione della verifica continua: UNA riga per PdA (manifest, IPdA,
    file e RdV si rileggono insieme). ``prossima_verifica`` ordina la rotazione;
    la presa in carico la sposta avanti della durata del lease, così una
    replica ferma restituisce il lavoro alla scadenza senza altri controlli.
    ``lease_id`` fa da recinto: solo chi detiene il lease registra l'esito.
    """
    __tablename__ = "verifiche_programmazione"
    __table_args__ = (
        CheckConstraint(
            "ultimo_esito IS NULL OR ultimo_esito IN "
            f"({_elenco_sql_verifiche(ESITI_VERIFICA)})",
            name="ck_verifiche_programmazione_esito",
        ),
        Index("idx_verifiche_programmazione_prossima", "prossima_verifica"),
        Index(
            "idx_verifiche_programmazione_richieste", "prossima_verifica",
            postgresql_where=text("richiesta_id IS NOT NULL"),
        ),
        Index("idx_verifiche_programmazione_tenant", "tenant_id", "aip_id"),
        Index(
            "idx_verifiche_programmazione_anomalie", "tenant_id", "anomalia_dal",
            postgresql_where=text("anomalia_dal IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    aip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    prossima_verifica: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ultima_verifica: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_esito: Mapped[str | None] = mapped_column(String(20))
    # Anomalia aperta dal: NULL quando l'ultima verifica conclusa è OK
    anomalia_dal: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    errori_lettura_consecutivi: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"))
    # Componenti da rileggere al prossimo tentativo dopo ERRORE_LETTURA
    da_ritentare: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    richiesta_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.verifiche_richieste.id", ondelete="RESTRICT"),
    )
    lease_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verifiche_eseguite: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"))
    creata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class VerificaRegistro(Base):
    """
    Registro delle verifiche, append-only (trigger): una riga per oggetto
    riletto, con esito, impronta attesa e trovata, retention letta dallo
    storage. Partizionato per mese su ``verificato_il`` (milioni di righe
    all'anno); le partizioni le crea la migrazione e poi il lavoratore, con
    una partizione predefinita che non fa mai fallire una scrittura.
    ``aip_id`` e ``documento_id`` senza chiave esterna: l'evidenza della
    verifica resta anche dopo uno scarto autorizzato del PdA.
    """
    __tablename__ = "verifiche_registro"
    __table_args__ = (
        CheckConstraint(
            f"esito IN ({_elenco_sql_verifiche(ESITI_VERIFICA)})",
            name="ck_verifiche_registro_esito",
        ),
        CheckConstraint(
            f"componente IN ({_elenco_sql_verifiche(COMPONENTI_VERIFICA)})",
            name="ck_verifiche_registro_componente",
        ),
        Index("idx_verifiche_registro_tenant", "tenant_id", text("verificato_il DESC"),
              text("id DESC")),
        Index("idx_verifiche_registro_aip", "aip_id", text("verificato_il DESC")),
        Index(
            "idx_verifiche_registro_anomalie", text("verificato_il DESC"),
            postgresql_where=text("esito <> 'OK'"),
        ),
        {"schema": "public", "postgresql_partition_by": "RANGE (verificato_il)"},
    )

    verificato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    aip_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    documento_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    componente: Mapped[str] = mapped_column(String(10), nullable=False)
    chiave: Mapped[str | None] = mapped_column(String(500))
    esito: Mapped[str] = mapped_column(String(20), nullable=False)
    algoritmo: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'SHA-256'"))
    impronta_attesa: Mapped[str | None] = mapped_column(String(128))
    impronta_trovata: Mapped[str | None] = mapped_column(String(128))
    dimensione_attesa: Mapped[int | None] = mapped_column(BigInteger)
    dimensione_trovata: Mapped[int | None] = mapped_column(BigInteger)
    retention_modalita: Mapped[str | None] = mapped_column(String(20))
    retention_fino_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Foglia del registro a due livelli (contratto K1), quando disponibile
    foglia_attesa: Mapped[str | None] = mapped_column(String(64))
    foglia_trovata: Mapped[str | None] = mapped_column(String(64))
    tentativo: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    motivo: Mapped[str | None] = mapped_column(String(500))
    richiesta_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    esecutore: Mapped[str] = mapped_column(String(200), nullable=False)


class VerificaCopertura(Base):
    """
    Copertura del ciclo senza ``COUNT(*)`` sul registro: per ente, quanti PdA
    hanno l'ultima verifica conclusa in quel giorno. Giorni convenzionali:
    0001-01-01 = mai verificati; 0001-01-02 = verificati prima della finestra
    conservata (righe accorpate dal lavoratore). Aggiornata nella stessa
    transazione della verifica.
    """
    __tablename__ = "verifiche_copertura"
    __table_args__ = (
        CheckConstraint("oggetti >= 0", name="ck_verifiche_copertura_oggetti"),
        {"schema": "public"},
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    giorno: Mapped[date] = mapped_column(Date, primary_key=True)
    oggetti: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class VerificaStato(Base):
    """Stato del lavoratore di verifica (posizioni di semina e riconciliazione)."""
    __tablename__ = "verifiche_stato"
    __table_args__ = ({"schema": "public"},)

    nome: Mapped[str] = mapped_column(String(50), primary_key=True)
    valore: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


# ── R04 catena: registro delle impronte a due livelli (migrazione 20261023_025) ──
# Contratti K1/K2 di R04; docs/architettura/catena.md. Tabelle append-only per
# trigger (catena_pda, catena_registro, catena_nodi); in catena_checkpoint il
# contenuto è immutabile e sigillo/controfirma si registrano una volta.

STATI_SIGILLO_CHECKPOINT = ("DA_FIRMARE", "FIRMATO", "NON_FIRMATO")
STATI_TESTIMONE_CHECKPOINT = (
    "NON_CONFIGURATO", "IN_ATTESA", "CONTROFIRMATO", "RIFIUTATO", "SUPERATO",
)


class CatenaPda(Base):
    """Albero di livello 1 di un PdA: dati delle foglie (JSON canonico, in ordine)
    e radice. Scritto dall'aip-builder nella transazione del PdA; immutabile."""
    __tablename__ = "catena_pda"
    __table_args__ = (
        CheckConstraint("num_foglie >= 1", name="ck_catena_pda_foglie"),
        CheckConstraint("versione_formato IN (1)", name="ck_catena_pda_versione"),
        {"schema": "public"},
    )

    aip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT"),
        primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False)
    urn: Mapped[str] = mapped_column(String(500), nullable=False)
    radice: Mapped[str] = mapped_column(String(64), nullable=False)
    num_foglie: Mapped[int] = mapped_column(Integer, nullable=False)
    foglie: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    versione_formato: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1"))
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class CatenaCoda(Base):
    """PdA da aggiungere al registro globale, in ordine di ``sequenza``."""
    __tablename__ = "catena_coda"
    __table_args__ = (
        UniqueConstraint("aip_id", name="uq_catena_coda_aip"),
        UniqueConstraint("foglia_interna_id", name="uq_catena_coda_foglia_interna"),
        CheckConstraint("(aip_id IS NULL) <> (foglia_interna_id IS NULL)",
                        name="ck_catena_coda_voce"),
        {"schema": "public"},
    )

    sequenza: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # R05 (migrazione 20261023_039, Q3): un PdA OPPURE una foglia interna
    aip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT"))
    inserita_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    foglia_interna_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.catena_foglie_interne.id",
                                          ondelete="RESTRICT"))


class CatenaRegistro(Base):
    """Foglia del registro globale (indice = posizione); immutabile."""
    __tablename__ = "catena_registro"
    __table_args__ = (
        UniqueConstraint("aip_id", name="uq_catena_registro_aip"),
        CheckConstraint("indice >= 0", name="ck_catena_registro_indice"),
        CheckConstraint("tipo IN ('PDA', 'RIVERSAMENTO', 'SCARTO', 'ANNULLAMENTO', "
                        "'RETTIFICA', 'AUDIT', 'CHIUSURA_ECCEZIONI', 'MINIMIZZAZIONE', "
                        "'RINNOVO_RETENTION')", name="ck_catena_registro_tipo"),
        # R05 (migrazione 20261023_039): il PdA solo per le foglie dei PdA
        CheckConstraint("((tipo IN ('PDA', 'RIVERSAMENTO')) = (aip_id IS NOT NULL)) "
                        "AND (tipo = 'AUDIT' OR tenant_id IS NOT NULL)",
                        name="ck_catena_registro_pda"),
        CheckConstraint("octet_length(hash) = 32", name="ck_catena_registro_hash"),
        {"schema": "public"},
    )

    indice: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    aip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT"))
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"))
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    dati: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)
    conservato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CatenaNodo(Base):
    """Radice di un sottoalbero perfetto del registro (livello 0 = hash di foglia)."""
    __tablename__ = "catena_nodi"
    __table_args__ = (
        CheckConstraint("livello >= 0 AND livello < 64 AND indice >= 0",
                        name="ck_catena_nodi_coordinate"),
        CheckConstraint("octet_length(hash) = 32", name="ck_catena_nodi_hash"),
        {"schema": "public"},
    )

    livello: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    indice: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    hash: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)


class CatenaCheckpoint(Base):
    """Checkpoint firmato del registro (nota C2SP), con sigillo e controfirma."""
    __tablename__ = "catena_checkpoint"
    __table_args__ = (
        CheckConstraint("dimensione >= 1", name="ck_catena_checkpoint_dimensione"),
        CheckConstraint("stato_sigillo IN ('DA_FIRMARE', 'FIRMATO', 'NON_FIRMATO')",
                        name="ck_catena_checkpoint_sigillo"),
        CheckConstraint(
            "stato_testimone IN ('NON_CONFIGURATO', 'IN_ATTESA', 'CONTROFIRMATO', "
            "'RIFIUTATO', 'SUPERATO')", name="ck_catena_checkpoint_testimone"),
        Index("idx_catena_checkpoint_testimone", "stato_testimone", "dimensione",
              postgresql_where=text("stato_testimone IN ('IN_ATTESA', 'RIFIUTATO')")),
        Index("idx_catena_checkpoint_sigillo", "dimensione",
              postgresql_where=text("stato_sigillo = 'DA_FIRMARE'")),
        # R13a (migrazione 20261107_054): marca dell'ultimo checkpoint (ancoraggio)
        CheckConstraint("stato_marca IN ('DA_MARCARE', 'MARCATO', 'NON_MARCATO', 'SUPERATO')",
                        name="ck_catena_checkpoint_stato_marca"),
        {"schema": "public"},
    )

    dimensione: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    radice: Mapped[str] = mapped_column(String(64), nullable=False)
    origine: Mapped[str] = mapped_column(String(200), nullable=False)
    testo: Mapped[str] = mapped_column(Text, nullable=False)
    chiave_verificatore: Mapped[str] = mapped_column(String(300), nullable=False)
    emesso_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    worm_bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    worm_chiave: Mapped[str] = mapped_column(String(500), nullable=False)
    worm_version_id: Mapped[str | None] = mapped_column(String(200))
    stato_sigillo: Mapped[str] = mapped_column(String(20), nullable=False)
    motivo_sigillo: Mapped[str | None] = mapped_column(Text)
    sigillo_cades: Mapped[str | None] = mapped_column(Text)
    data_marca: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sigillo_worm_chiave: Mapped[str | None] = mapped_column(String(500))
    stato_testimone: Mapped[str] = mapped_column(String(20), nullable=False)
    controfirma: Mapped[str | None] = mapped_column(Text)
    controfirmato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    esito_testimone: Mapped[str | None] = mapped_column(Text)
    tentativi_testimone: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    # R04 correzioni (migrazione 20261023_031, rev4 rilievo 1): un rifiuto del
    # testimone è un allarme persistente finché un operatore non lo gestisce
    rifiuto_gestito_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rifiuto_gestito_da: Mapped[str | None] = mapped_column(String(200))
    rifiuto_nota: Mapped[str | None] = mapped_column(Text)
    # R04 secondo giro (migrazione 20261023_033, N3): allarme che raggruppa il rifiuto
    allarme_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("public.catena_allarmi_testimone.id", ondelete="RESTRICT"))
    # R13a (migrazione 20261107_054, VD-C-068): marca temporale RFC 3161 del corpo del
    # checkpoint (ancoraggio esterno); immutabile dopo l'esito (funzione di protezione)
    stato_marca: Mapped[str] = mapped_column(String(15), nullable=False,
                                             server_default=text("'DA_MARCARE'"))
    motivo_marca: Mapped[str | None] = mapped_column(String(500))
    marca_token: Mapped[str | None] = mapped_column(Text)
    marca_worm_chiave: Mapped[str | None] = mapped_column(String(500))
    marcato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marca_tsa: Mapped[str | None] = mapped_column(String(200))


class CatenaAllarmeTestimone(Base):
    """Allarme del testimone (R04 secondo giro, migrazione 20261023_033, N3): i
    rifiuti consecutivi con la stessa ``causa`` si raggruppano in UN allarme
    aperto con un contatore; l'operatore lo gestisce in blocco (una voce di
    audit per incidente). Al più un allarme aperto per causa."""
    __tablename__ = "catena_allarmi_testimone"
    __table_args__ = (
        Index("uq_catena_allarmi_testimone_aperto", "causa", unique=True,
              postgresql_where=text("gestito_il IS NULL")),
        CheckConstraint("rifiuti >= 1", name="ck_catena_allarmi_testimone_rifiuti"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    causa: Mapped[str] = mapped_column(String(60), nullable=False)
    aperto_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ultimo_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rifiuti: Mapped[int] = mapped_column(Integer, nullable=False)
    prima_dimensione: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ultima_dimensione: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ultimo_motivo: Mapped[str | None] = mapped_column(Text)
    gestito_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gestito_da: Mapped[str | None] = mapped_column(String(200))
    nota: Mapped[str | None] = mapped_column(Text)


class CatenaBloccoRegistro(Base):
    """Blocco PERSISTENTE del registro delle impronte (R04 terzo giro, migrazione
    20261023_034, T4): finché è aperto il registro non sequenzia, non emette
    checkpoint, non scrive nel WORM e non contatta il testimone. Al più un blocco
    aperto per causa (``REGISTRO_INDIETRO``, ``REGISTRO_DIVERGENTE``,
    ``WORM_CONTENUTO_DIVERSO``, ``COERENZA_NON_VERIFICABILE``)."""
    __tablename__ = "catena_blocchi_registro"
    __table_args__ = (
        Index("uq_catena_blocchi_registro_aperto", "causa", unique=True,
              postgresql_where=text("chiuso_il IS NULL")),
        CheckConstraint("occorrenze >= 1", name="ck_catena_blocchi_registro_occorrenze"),
        # quarto giro (migrazione 20261023_035, F1): controllo non verificabile
        CheckConstraint(
            "causa IN ('REGISTRO_INDIETRO', 'REGISTRO_DIVERGENTE', 'WORM_CONTENUTO_DIVERSO', "
            "'COERENZA_NON_VERIFICABILE')",
            name="ck_catena_blocchi_registro_causa"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    causa: Mapped[str] = mapped_column(String(40), nullable=False)
    aperto_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ultimo_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occorrenze: Mapped[int] = mapped_column(Integer, nullable=False)
    dettagli: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    chiuso_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chiuso_da: Mapped[str | None] = mapped_column(String(200))
    nota: Mapped[str | None] = mapped_column(Text)


class CatenaStato(Base):
    """Riga unica (id = 1): dimensione del registro, lease del sequenziatore,
    foglie esportate nel WORM, ultima dimensione controfirmata dal testimone."""
    __tablename__ = "catena_stato"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_catena_stato_unica"),
        CheckConstraint("dimensione >= 0 AND worm_foglie_esportate <= dimensione",
                        name="ck_catena_stato_dimensioni"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    dimensione: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"))
    detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worm_foglie_esportate: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"))
    testimone_dimensione: Mapped[int | None] = mapped_column(BigInteger)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = [
    "Base",
    "TenantStato", "UserRuolo", "PdvStato", "AipStato",
    "StorageTier", "ModalitaFormazione", "TipoFlusso", "TipoSInCROXML",
    "METADATI_ALLEGATO5_CAMPI",
    # Tabelle di controllo
    "Tenant", "User", "ApiKey", "RscNomination", "Contract",
    "AuditGlobal", "HashchainBlock",
    # Tabelle operative (isolamento per tenant_id)
    "ClasseDocumentale", "Pdv", "Aip", "Documento", "MetadatiAllegato5",
    "LifecycleEvent", "Fascicolo", "FascicoloAip", "Notification",
    "PdndFruitore", "OutboxEvento", "VersamentoImpronta",
    # R03 a2: motore delle regole (migrazione 20261021_014)
    "ImpostazioneValore", "ProfiloMetadatiClasse",
    # R03 b2: canale SFTP (migrazione 20261021_016)
    "SftpAccount", "SftpChiave", "SftpLavorazione",
    # R03 b3: eventi per i clienti e webhook (migrazione 20261021_017)
    "EventoCliente", "EventoClientePubblicazione", "WebhookSottoscrizione", "WebhookConsegna",
    "SftpSegnalazione",
    # R03 b4: indicizzazione e pacchetti di distribuzione (migrazione 20261021_018)
    "IndicizzazioneAip", "PacchettoDistribuzione",
    # R03 b1: sessioni di caricamento (migrazione 20261021_015)
    "STATI_CARICAMENTO", "Caricamento", "CaricamentoParte",
    # R04 servizi: code di scarto e idempotenza generica (migrazione 20261023_029)
    "Scarto", "RichiestaIdempotente",
    # R04 verifiche: verifica continua dell'integrità (migrazione 20261023_027)
    "ESITI_VERIFICA", "COMPONENTI_VERIFICA", "VerificaRichiesta", "VerificaProgrammazione",
    "VerificaRegistro", "VerificaCopertura", "VerificaStato",
    # R04 catena: registro delle impronte a due livelli (migrazione 20261023_025)
    "STATI_SIGILLO_CHECKPOINT", "STATI_TESTIMONE_CHECKPOINT", "CatenaPda", "CatenaCoda",
    "CatenaRegistro", "CatenaNodo", "CatenaCheckpoint", "CatenaStato",
    # R04 secondo giro (migrazione 20261023_033)
    "OutboxTopicSospeso", "CatenaAllarmeTestimone",
    # R04 terzo giro (migrazione 20261023_034)
    "CatenaBloccoRegistro",
    # R04 dati: contatori per ente (migrazione 20261023_028; classi in fondo al modulo)
    "ContatoreEnte", "ContatoreEnteDelta",
    # R04 correzioni (migrazione 20261023_031)
    "OutboxChiaveFerma", "ScartoRipartenza", "ManutenzioneLease",
]


# ── R04 dati: contatori per ente (migrazione 20261023_028) ──────────
# Piano R03–R05 §4.2 («contatori per ente, mai COUNT(*) su tabelle grandi»).
# Scritti dai trigger su pdv, aip e documenti; letti e consolidati da
# libs/shared/db/contatori.py.

class ContatoreEnte(Base):
    """Valore consolidato di una metrica per ente e giorno UTC (vedi
    ``libs.shared.db.contatori`` per l'elenco delle metriche). Nessuna chiave
    esterna: tabella di riepilogo, scritta solo dal consolidamento."""
    __tablename__ = "contatori_ente"
    __table_args__ = (
        Index("idx_contatori_ente_metrica_giorno", "metrica", "giorno"),
        {"schema": "public"},
    )

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    metrica: Mapped[str] = mapped_column(String(80), primary_key=True)
    giorno: Mapped[date] = mapped_column(Date, primary_key=True)
    valore: Mapped[int] = mapped_column(BigInteger, nullable=False)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContatoreEnteDelta(Base):
    """Variazione di un contatore scritta da un trigger nella transazione della
    modifica (solo INSERT: nessuna riga contesa); consolidata a lotti in
    ``contatori_ente``."""
    __tablename__ = "contatori_ente_delta"
    __table_args__ = (
        Index("idx_contatori_ente_delta_tenant", "tenant_id", "metrica"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    metrica: Mapped[str] = mapped_column(String(80), nullable=False)
    giorno: Mapped[date] = mapped_column(Date, nullable=False)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ── R05 ciclo di vita: pratiche (migrazione 20261023_037, agente r05-ciclo-vita) ──
# Contratto Q2, VD-C-072, VD-P-06. Scritte SOLO dal servizio ``ciclo-vita``.

#: Tipi di pratica del ciclo di vita.
TIPI_PRATICA = ("RETTIFICA", "ANNULLAMENTO", "SCARTO", "RIVERSAMENTO_USCITA",
                "RIVERSAMENTO_INGRESSO", "RESTITUZIONE")
#: Tipi di allegato di una pratica (archiviati nel WORM con impronta).
TIPI_ALLEGATO_PRATICA = ("AUTORIZZAZIONE_PRODUTTORE", "NULLA_OSTA", "VERBALE_SCARTO", "ATTO",
                         "MANIFESTO", "PDD_RICEVUTO", "EXPORT_REGISTRI", "ALTRO",
                         # R08 (D22): richiesta di annullamento firmata
                         "RICHIESTA_ANNULLAMENTO")
#: Esiti di un oggetto (PdA o PdV) coinvolto in una pratica.
ESITI_OGGETTO_PRATICA = ("SELEZIONATO", "BLOCCATO", "IN_CORSO", "ELIMINATO", "ESPORTATO",
                         "VERSATO", "ERRORE", "NON_COMPLETATO")


class Pratica(Base):
    """Procedimento del ciclo di vita (rettifica, annullamento, scarto, riversamento,
    restituzione) con macchina a stati, versione per i comandi con lo stato atteso
    (REGOLE §12.5), impronta del piano (§13) e lavoro fuori richiesta con lease (§12.9)."""
    __tablename__ = "pratiche"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('RETTIFICA', 'ANNULLAMENTO', 'SCARTO', 'RIVERSAMENTO_USCITA', "
            "'RIVERSAMENTO_INGRESSO', 'RESTITUZIONE')", name="ck_pratiche_tipo"),
        CheckConstraint("char_length(motivo) >= 10", name="ck_pratiche_motivo"),
        CheckConstraint("versione >= 1", name="ck_pratiche_versione"),
        Index("idx_pratiche_tenant_cursore", "tenant_id", text("created_at DESC"),
              text("id DESC")),
        Index("idx_pratiche_cursore", text("created_at DESC"), text("id DESC")),
        Index("idx_pratiche_lavoro", "prossimo_tentativo", "id",
              postgresql_where=text("lavoro IS NOT NULL")),
        Index("uq_pratiche_idempotenza", "tenant_id", "tipo", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
        Index("uq_pratiche_rettifica_aperta", "pdv_id", unique=True,
              postgresql_where=text("tipo = 'RETTIFICA' AND "
                                    "stato NOT IN ('COMPLETATA', 'RESPINTA')")),
        # R09 (D23, migrazione 20261101_048): un annullamento RESPINTO non blocca
        # una nuova richiesta
        Index("uq_pratiche_annullamento_attivo", "pdv_id", unique=True,
              postgresql_where=text("tipo = 'ANNULLAMENTO' AND stato <> 'RESPINTA'")),
        Index("idx_pratiche_pdv", "pdv_id", postgresql_where=text("pdv_id IS NOT NULL")),
        Index("idx_pratiche_pdv_risultato", "pdv_risultato_id",
              postgresql_where=text("pdv_risultato_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_pratiche_tenant"),
        nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    stato: Mapped[str] = mapped_column(String(40), nullable=False)
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    dati: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                 server_default=text("'{}'::jsonb"))
    impronta_piano: Mapped[str | None] = mapped_column(String(64))
    richiede_nulla_osta: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                      server_default=text("false"))
    # R13b (migrazione 20261108_055, VD-C-162): fotografia alla creazione del parametro
    # RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE (immutabile, trigger della 044)
    richiede_autorizzazione_produttore: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))
    pdv_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pdv.id", ondelete="RESTRICT", name="fk_pratiche_pdv"))
    pdv_risultato_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pdv.id", ondelete="RESTRICT", name="fk_pratiche_pdv_risultato"))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    creata_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    creata_ruolo: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    conclusa_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lavoro: Mapped[str | None] = mapped_column(String(30))
    prossimo_tentativo: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                         server_default=func.now())
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lease_detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_errore: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    conteggi: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                     server_default=text("'{}'::jsonb"))


class PraticaStato(Base):
    """Cronologia append-only delle transizioni di una pratica (registro delle
    autorizzazioni: chi, ruolo, motivo, allegati citati)."""
    __tablename__ = "pratiche_stati"
    __table_args__ = (
        Index("idx_pratiche_stati_pratica", "pratica_id", "id"),
        # R14 (VD-C-121, migrazione 20261109_056): una sola riga per base
        Index("uq_pratiche_stati_precedente", "pratica_id", "precedente_id", unique=True,
              postgresql_where=text("precedente_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    pratica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pratiche.id", ondelete="RESTRICT", name="fk_pratiche_stati_pratica"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    da_stato: Mapped[str | None] = mapped_column(String(40))
    a_stato: Mapped[str] = mapped_column(String(40), nullable=False)
    utente_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ruolo: Mapped[str | None] = mapped_column(String(30))
    metodo: Mapped[str | None] = mapped_column(String(20))
    motivo: Mapped[str | None] = mapped_column(Text)
    allegati: Mapped[list[Any]] = mapped_column(JSONB, nullable=False,
                                                server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    # R14 (VD-C-121): id della riga precedente della pratica (0 per la prima), scritto dal
    # trigger di continuità; nullo per le righe precedenti alla migrazione 056
    precedente_id: Mapped[int | None] = mapped_column(BigInteger)


class PraticaAllegato(Base):
    """Allegato di una pratica archiviato nel WORM (Object Lock COMPLIANCE) con
    impronta SHA-256; riga immutabile."""
    __tablename__ = "pratiche_allegati"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('AUTORIZZAZIONE_PRODUTTORE', 'NULLA_OSTA', 'VERBALE_SCARTO', 'ATTO', "
            "'MANIFESTO', 'PDD_RICEVUTO', 'EXPORT_REGISTRI', 'ALTRO')",
            name="ck_pratiche_allegati_tipo"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_pratiche_allegati_sha256"),
        UniqueConstraint("pratica_id", "tipo", "sha256", name="uq_pratiche_allegati_impronta"),
        UniqueConstraint("bucket", "chiave", name="uq_pratiche_allegati_chiave"),
        Index("idx_pratiche_allegati_pratica", "pratica_id", "created_at"),
        # R12a (VD-C-145, migrazione 20261104_051): riuso di un documento autorizzativo
        Index("idx_pratiche_allegati_impronta", "tenant_id", "sha256"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    pratica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                   name="fk_pratiche_allegati_pratica"), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    nome_file: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    dimensione: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    chiave: Mapped[str] = mapped_column(String(500), nullable=False)
    version_id: Mapped[str | None] = mapped_column(String(200))
    conserva_fino_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    firma: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                  server_default=text("'{}'::jsonb"))
    caricato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class PraticaOggetto(Base):
    """PdA o PdV coinvolto in una pratica, con esito per elemento e foglia del
    registro delle impronte (contratto Q3)."""
    __tablename__ = "pratiche_oggetti"
    __table_args__ = (
        CheckConstraint(
            "esito IN ('SELEZIONATO', 'BLOCCATO', 'IN_CORSO', 'ELIMINATO', 'ESPORTATO', "
            "'VERSATO', 'ERRORE', 'NON_COMPLETATO')", name="ck_pratiche_oggetti_esito"),
        CheckConstraint("aip_id IS NOT NULL OR pdv_id IS NOT NULL OR urn IS NOT NULL",
                        name="ck_pratiche_oggetti_riferimento"),
        Index("idx_pratiche_oggetti_pratica", "pratica_id", "esito", "id"),
        Index("uq_pratiche_oggetti_aip", "pratica_id", "aip_id", unique=True,
              postgresql_where=text("aip_id IS NOT NULL")),
        Index("idx_pratiche_oggetti_aip", "aip_id", postgresql_where=text("aip_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    pratica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pratiche.id", ondelete="RESTRICT", name="fk_pratiche_oggetti_pratica"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.aip.id", ondelete="RESTRICT", name="fk_pratiche_oggetti_aip"))
    pdv_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pdv.id", ondelete="RESTRICT", name="fk_pratiche_oggetti_pdv"))
    urn: Mapped[str | None] = mapped_column(String(500))
    esito: Mapped[str] = mapped_column(String(20), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(60))
    #: Revisione 4 di R05 (U2, migrazione 20261024_044): esito e motivo del PIANO
    #: approvato, copiati dal trigger ``trg_pratiche_oggetti_piano`` all'INSERT e
    #: poi immutabili (il lavoratore cancella solo ``esito_piano = SELEZIONATO``)
    esito_piano: Mapped[str | None] = mapped_column(String(20))
    motivo_piano: Mapped[str | None] = mapped_column(String(60))
    dettagli: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                     server_default=text("'{}'::jsonb"))
    foglia_id: Mapped[str | None] = mapped_column(String(100))
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


class PraticaEsecuzione(Base):
    """Esecuzione del lavoratore su una pratica (append-only, REGOLE §15.8)."""
    __tablename__ = "pratiche_esecuzioni"
    __table_args__ = (
        CheckConstraint("esito IN ('COMPLETATO', 'PARZIALE', 'ERRORE', 'RINVIATO')",
                        name="ck_pratiche_esecuzioni_esito"),
        Index("idx_pratiche_esecuzioni_pratica", "pratica_id", "id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    pratica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                   name="fk_pratiche_esecuzioni_pratica"), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    lavoro: Mapped[str] = mapped_column(String(30), nullable=False)
    detentore: Mapped[str] = mapped_column(String(200), nullable=False)
    tentativo: Mapped[int] = mapped_column(Integer, nullable=False)
    avviata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    esito: Mapped[str] = mapped_column(String(20), nullable=False)
    conteggi: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                     server_default=text("'{}'::jsonb"))
    errore: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class EsitoEsecuzione(Base):
    """Revisione 3 di R05 (T5, migrazione 20261023_043): FATTO di esecuzione di
    una pratica su un PdA (oggi lo scarto: ``ELIMINATO``), append-only. INSERT
    concesso SOLO al ruolo ``verumdoc_ciclo_vita_lavoratore`` (il login del
    lavoratore, mai quello dell'API esposta); ``verumdoc_applicazione`` solo
    SELECT. Il riscontro della hashchain lo pretende prima di firmare il
    tombstone.

    Revisione 4 di R05 (U2, migrazione 20261024_044): esito
    ``AUTORIZZAZIONI_VERIFICATE`` senza PdA: il lavoratore ha verificato la
    catena delle autorizzazioni della pratica prima di un giro di esecuzione.

    R06 (VD-C-119, migrazione 20261030_045): fatti ``ANNULLAMENTO`` /
    ``ATTO_ARCHIVIATO`` e ``RETTIFICA`` / ``PDV_VERSATO`` (con ``atto_sha256``
    e ``pdv_rettifica`` nei dettagli), pretesi dal riscontro della hashchain.

    R12b (VD-C-160, migrazione 20261106_053): ``RESTITUZIONE`` /
    ``AUTORIZZAZIONI_VERIFICATE``: il lavoratore ha riverificato dal WORM
    l'autorizzazione del produttore legata alla pratica prima di esportare."""
    __tablename__ = "esiti_esecuzione"
    __table_args__ = (
        CheckConstraint("tipo IN ('SCARTO', 'ANNULLAMENTO', 'RETTIFICA', 'RETENTION', "
                        "'RESTITUZIONE')", name="ck_esiti_esecuzione_tipo"),
        CheckConstraint("esito IN ('ELIMINATO', 'AUTORIZZAZIONI_VERIFICATE', 'ATTO_ARCHIVIATO', "
                        "'PDV_VERSATO', 'CHIUSO_CON_ECCEZIONI', 'METADATI_MINIMIZZATI', "
                        "'PROPOSTA_GENERATA', 'RINNOVATA', 'RINNOVATA_ALLEGATO')",
                        name="ck_esiti_esecuzione_esito"),
        CheckConstraint("esito NOT IN ('ELIMINATO', 'ATTO_ARCHIVIATO', 'PDV_VERSATO', "
                        "'CHIUSO_CON_ECCEZIONI', 'METADATI_MINIMIZZATI', 'RINNOVATA') "
                        "OR aip_id IS NOT NULL", name="ck_esiti_esecuzione_aip"),
        CheckConstraint("(tipo = 'SCARTO' AND esito IN ('ELIMINATO', 'AUTORIZZAZIONI_VERIFICATE', "
                        "'CHIUSO_CON_ECCEZIONI', 'METADATI_MINIMIZZATI', 'PROPOSTA_GENERATA')) "
                        "OR (tipo = 'ANNULLAMENTO' AND esito IN ('ATTO_ARCHIVIATO', "
                        "'AUTORIZZAZIONI_VERIFICATE')) "
                        "OR (tipo = 'RETTIFICA' AND esito = 'PDV_VERSATO') "
                        "OR (tipo = 'RETENTION' AND esito IN ('RINNOVATA', 'RINNOVATA_ALLEGATO')) "
                        "OR (tipo = 'RESTITUZIONE' AND esito = 'AUTORIZZAZIONI_VERIFICATE')",
                        name="ck_esiti_esecuzione_coppia"),
        CheckConstraint("esito NOT IN ('ATTO_ARCHIVIATO', 'PDV_VERSATO') "
                        "OR (COALESCE((dettagli ->> 'atto_sha256') ~ '^[0-9a-f]{64}$', false) "
                        "AND (esito <> 'PDV_VERSATO' OR (dettagli ->> 'pdv_rettifica') IS NOT "
                        "NULL))",
                        name="ck_esiti_esecuzione_dettagli"),
        # R09 (migrazione 20261101_048): pratica facoltativa solo per il rinnovo di
        # un PdA; impronta obbligatoria per i fatti nuovi
        CheckConstraint("pratica_id IS NOT NULL OR (tipo = 'RETENTION' AND esito = 'RINNOVATA')",
                        name="ck_esiti_esecuzione_pratica"),
        CheckConstraint("esito NOT IN ('CHIUSO_CON_ECCEZIONI', 'METADATI_MINIMIZZATI', "
                        "'RINNOVATA', 'RINNOVATA_ALLEGATO') "
                        "OR COALESCE((dettagli ->> 'impronta') ~ '^[0-9a-f]{64}$', false)",
                        name="ck_esiti_esecuzione_impronta"),
        UniqueConstraint("tipo", "pratica_id", "aip_id", "esito",
                         name="uq_esiti_esecuzione_fatto"),
        Index("idx_esiti_esecuzione_aip", "aip_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    pratica_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                   name="fk_esiti_esecuzione_pratica"))
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.aip.id", ondelete="RESTRICT", name="fk_esiti_esecuzione_aip"))
    esito: Mapped[str] = mapped_column(String(30), nullable=False)
    dettagli: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                     server_default=text("'{}'::jsonb"))
    scritto_da: Mapped[str] = mapped_column(String(200), nullable=False,
                                            server_default=text("current_user"))
    scritto_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


# Indici della migrazione 20261023_037 su tabelle esistenti (dichiarati qui per
# non toccare le classi: aggiunte soltanto).
Index("idx_aip_tenant_scadenza", Aip.tenant_id, Aip.scadenza_conservazione, Aip.id,
      postgresql_where=text("scadenza_conservazione IS NOT NULL"))
Index("idx_fascicoli_aip_aip", FascicoloAip.aip_id)
Index("idx_fascicoli_tenant_cursore", Fascicolo.tenant_id, Fascicolo.created_at.desc(),
      Fascicolo.id.desc())

__all__ += [  # noqa: PLE0605 — aggiunte R05 ciclo di vita
    "TIPI_PRATICA", "TIPI_ALLEGATO_PRATICA", "ESITI_OGGETTO_PRATICA",
    "Pratica", "PraticaStato", "PraticaAllegato", "PraticaOggetto", "PraticaEsecuzione",
    "EsitoEsecuzione",
]


# ── R05 catena: foglie interne ed export dell'audit (migrazione 20261023_039) ──
# Contratto Q3 (foglie di formato 2 del ciclo di vita) e VD-C-112 (audit nel WORM);
# docs/architettura/catena.md §4 e §11.

TIPI_FOGLIA_INTERNA = ("SCARTO", "ANNULLAMENTO", "RETTIFICA", "AUDIT",
                       # R09 (migrazione 20261101_048): fatti che non invalidano il PdA
                       "CHIUSURA_ECCEZIONI", "MINIMIZZAZIONE", "RINNOVO_RETENTION")


class CatenaFogliaInterna(Base):
    """Richiesta di una foglia di formato 2 (ciclo di vita o audit): dati e hash
    fissati alla richiesta, ``indice`` scritto una volta dal sequenziatore."""
    __tablename__ = "catena_foglie_interne"
    __table_args__ = (
        UniqueConstraint("chiave", name="uq_catena_foglie_interne_chiave"),
        UniqueConstraint("indice", name="uq_catena_foglie_interne_indice"),
        CheckConstraint("tipo IN ('SCARTO', 'ANNULLAMENTO', 'RETTIFICA', 'AUDIT')",
                        name="ck_catena_foglie_interne_tipo"),
        CheckConstraint("(tipo = 'AUDIT') = (aip_id IS NULL)",
                        name="ck_catena_foglie_interne_pda"),
        CheckConstraint("octet_length(hash) = 32", name="ck_catena_foglie_interne_hash"),
        CheckConstraint("(indice IS NULL) = (sequenziata_il IS NULL)",
                        name="ck_catena_foglie_interne_sequenza"),
        Index("idx_catena_foglie_interne_aip", "aip_id", "indice",
              postgresql_where=text("aip_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    chiave: Mapped[str] = mapped_column(String(800), nullable=False)
    aip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.aip.id", ondelete="RESTRICT"))
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"))
    pda: Mapped[str | None] = mapped_column(String(500))
    pratica: Mapped[str | None] = mapped_column(String(500))
    riferimento: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    impronta: Mapped[str | None] = mapped_column(String(64))
    dati: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)
    richiesta_da: Mapped[str] = mapped_column(String(200), nullable=False)
    creata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    indice: Mapped[int | None] = mapped_column(BigInteger)
    sequenziata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEsportazione(Base):
    """Segmento dell'audit esportato nel WORM (append-only, VD-C-112)."""
    __tablename__ = "audit_esportazioni"
    __table_args__ = (
        UniqueConstraint("periodo_a", name="uq_audit_esportazioni_periodo_a"),
        UniqueConstraint("impronta_segmento", name="uq_audit_esportazioni_impronta"),
        UniqueConstraint("worm_chiave", name="uq_audit_esportazioni_chiave"),
        CheckConstraint("periodo_a > periodo_da", name="ck_audit_esportazioni_periodo"),
        CheckConstraint("righe >= 0", name="ck_audit_esportazioni_righe"),
        {"schema": "public"},
    )

    periodo_da: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    periodo_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    righe: Mapped[int] = mapped_column(BigInteger, nullable=False)
    radice_righe: Mapped[str] = mapped_column(String(64), nullable=False)
    impronta_segmento: Mapped[str] = mapped_column(String(64), nullable=False)
    impronta_precedente: Mapped[str | None] = mapped_column(String(64))
    worm_bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    worm_chiave: Mapped[str] = mapped_column(String(500), nullable=False)
    foglia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.catena_foglie_interne.id",
                                          ondelete="RESTRICT"), nullable=False)
    esportato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ += [  # noqa: PLE0605 — aggiunte R05 catena
    "TIPI_FOGLIA_INTERNA", "CatenaFogliaInterna", "AuditEsportazione",
]


# ── R05 Q1: configurazione con bozze, accordi, profili di tipologia ────
# (migrazione 20261023_036, agente r05-regole; REGOLE §8.4, §8.13, §13)

class ConfigurazioneBozza(Base):
    """
    Bozza versionata di una modifica di configurazione (REGOLE §13.3): niente si
    scrive fuori dalla bozza fino all'applicazione, ogni salvataggio porta la
    versione letta (409 se cambiata). ``tenant_id`` NULL solo per il livello
    CONSERVATORE. Stati: APERTA → APPLICATA | ANNULLATA; R07 (D28, migrazione
    20261030_046): APERTA → PROGRAMMATA (decorrenza futura ``valido_dal``) →
    APPLICATA dal lavoro periodico, oppure ANNULLATA.
    """
    __tablename__ = "configurazione_bozze"
    __table_args__ = (
        CheckConstraint("risorsa IN ('impostazioni', 'regole', 'profilo', 'classe', "
                        "'accordo', 'quote')", name="ck_configurazione_bozze_risorsa"),
        CheckConstraint("livello IN ('CONSERVATORE', 'ENTE', 'CLASSE', 'ACCORDO', 'PDV')",
                        name="ck_configurazione_bozze_livello"),
        CheckConstraint("stato IN ('APERTA', 'PROGRAMMATA', 'APPLICATA', 'ANNULLATA')",
                        name="ck_configurazione_bozze_stato"),
        CheckConstraint("stato <> 'PROGRAMMATA' OR valido_dal IS NOT NULL",
                        name="ck_configurazione_bozze_programmata"),
        CheckConstraint("versione >= 1", name="ck_configurazione_bozze_versione"),
        Index("idx_configurazione_bozze_ente", "tenant_id", text("created_at DESC"),
              text("id DESC")),
        Index("idx_configurazione_bozze_stato", "stato", text("created_at DESC"),
              text("id DESC")),
        Index("idx_configurazione_bozze_programmate", "valido_dal", "id",
              postgresql_where=text("stato = 'PROGRAMMATA'")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    risorsa: Mapped[str] = mapped_column(String(20), nullable=False)
    livello: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"))
    classe_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"))
    accordo_codice: Mapped[str | None] = mapped_column(String(50))
    modifiche: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    proposta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    versione: Mapped[int] = mapped_column(Integer, nullable=False, default=1,
                                          server_default=text("1"))
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="APERTA",
                                       server_default=text("'APERTA'"))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    aggiornato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    applicata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applicata_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    impronta_piano: Mapped[str | None] = mapped_column(String(64))
    esito: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # R07 (D28): decorrenza futura, anteprima del giorno prima, avviso all'autore
    valido_dal: Mapped[date | None] = mapped_column(Date)
    programmata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    programmata_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    anteprima_programmata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    anteprima_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    avviso_autore_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccordoVersamentoVersione(Base):
    """
    Versione di un accordo di versamento (livello ACCORDO della catena,
    ``libs.verumdoc_rules.accordi``). APPEND-ONLY (trigger della migrazione 036):
    una versione non si modifica mai (REGOLE §8.4); vale dal ``valido_dal``
    (giorno italiano) fino alla decorrenza della successiva; ``cessato`` chiude
    l'accordo. ``classe_id`` NULL = tutte le classi dell'ente.
    """
    __tablename__ = "accordi_versamento"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codice", "versione",
                         name="uq_accordi_versamento_versione"),
        CheckConstraint("versione >= 1", name="ck_accordi_versamento_versione"),
        CheckConstraint("codice ~ '^[A-Za-z0-9._-]{1,50}$'", name="ck_accordi_versamento_codice"),
        Index("idx_accordi_versamento_ente", "tenant_id", "valido_dal"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False)
    codice: Mapped[str] = mapped_column(String(50), nullable=False)
    versione: Mapped[int] = mapped_column(Integer, nullable=False)
    classe_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"))
    descrizione: Mapped[str | None] = mapped_column(Text)
    valido_dal: Mapped[date] = mapped_column(Date, nullable=False)
    cessato: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                          server_default=text("false"))
    valori: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                   server_default=text("'{}'::jsonb"))
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    bozza_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.configurazione_bozze.id", ondelete="RESTRICT"))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class ProfiloTipologia(Base):
    """
    Versione di un profilo di metadati di TIPOLOGIA dell'Allegato 5 creata dal
    conservatore (le versioni predefinite stanno in
    ``libs/verumdoc_rules/dati/profili``). APPEND-ONLY (trigger della 036).
    """
    __tablename__ = "profili_tipologia"
    __table_args__ = (
        UniqueConstraint("codice", "versione", name="uq_profili_tipologia_versione"),
        CheckConstraint("versione >= 1", name="ck_profili_tipologia_versione"),
        CheckConstraint("valido_al IS NULL OR valido_al >= valido_dal",
                        name="ck_profili_tipologia_validita"),
        # R11 (D07, migrazione 20261103_050)
        CheckConstraint("adozione IN ('automatica', 'esplicita')",
                        name="ck_profili_tipologia_adozione"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    codice: Mapped[str] = mapped_column(String(50), nullable=False)
    versione: Mapped[int] = mapped_column(Integer, nullable=False)
    valido_dal: Mapped[date] = mapped_column(Date, nullable=False)
    valido_al: Mapped[date | None] = mapped_column(Date)
    campi: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    descrizione: Mapped[str | None] = mapped_column(Text)
    norm_ref: Mapped[str | None] = mapped_column(Text)
    #: R11: «automatica» (vale la più alta valida) o «esplicita» (scelta dalla classe)
    adozione: Mapped[str] = mapped_column(String(16), nullable=False, default="automatica",
                                          server_default=text("'automatica'"))
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    bozza_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.configurazione_bozze.id", ondelete="RESTRICT"))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class ConfigurazioneProposta(Base):
    """
    Proposta del conservatore a un ente (REGOLE §8.13): un valore del livello
    del conservatore, non imposto dalla norma, arriva all'ente da accettare
    (diventa il valore del livello ENTE) o rifiutare con motivo.
    """
    __tablename__ = "configurazione_proposte"
    __table_args__ = (
        UniqueConstraint("bozza_id", "tenant_id", "codice", name="uq_configurazione_proposte"),
        CheckConstraint("stato IN ('PROPOSTA', 'ACCETTATA', 'RIFIUTATA')",
                        name="ck_configurazione_proposte_stato"),
        Index("idx_configurazione_proposte_ente", "tenant_id", "stato",
              text("created_at DESC"), text("id DESC")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    bozza_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.configurazione_bozze.id", ondelete="RESTRICT"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False)
    codice: Mapped[str] = mapped_column(String(100), nullable=False)
    valore: Mapped[Any] = mapped_column(JSONB, nullable=False)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="PROPOSTA",
                                       server_default=text("'PROPOSTA'"))
    motivo_risposta: Mapped[str | None] = mapped_column(Text)
    risposto_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    risposto_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class ImpostazioneStorico(Base):
    """
    Storia APPEND-ONLY dei valori di ``impostazioni_valori`` (trigger della 036):
    ogni applicazione di una bozza o accettazione di una proposta aggiunge una
    riga con valore prima e dopo (NULL = ereditato), motivo e autore. La riga
    corrente resta in ``impostazioni_valori`` (letta da gateway, quote e
    manutenzione delle partizioni); la storia è la prova delle versioni (§8.4).
    """
    __tablename__ = "impostazioni_storico"
    __table_args__ = (
        Index("idx_impostazioni_storico_ente", "tenant_id", text("created_at DESC")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    livello: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"))
    classe_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.classi_documentali.id", ondelete="RESTRICT"))
    codice: Mapped[str] = mapped_column(String(100), nullable=False)
    valore_prima: Mapped[Any | None] = mapped_column(JSONB)
    valore_dopo: Mapped[Any | None] = mapped_column(JSONB)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    bozza_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.configurazione_bozze.id", ondelete="RESTRICT"))
    proposta_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.configurazione_proposte.id",
                                          ondelete="RESTRICT"))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ += [  # noqa: PLE0605 — aggiunte R05 regole (Q1)
    "AccordoVersamentoVersione", "ConfigurazioneBozza", "ConfigurazioneProposta",
    "ImpostazioneStorico", "ProfiloTipologia",
]


class SessioneRevocata(Base):
    """Sessione del portale chiusa con «Esci» (revisione R05, R12): impronta
    SHA-256 del token e ``jti`` rifiutati fino alla scadenza del token da tutti
    i servizi (``libs.shared.security.revoca``, ``MiddlewareSessioneCookie``).
    Tabella di autenticazione: fuori dalla Row Level Security."""
    __tablename__ = "sessioni_revocate"
    __table_args__ = (
        CheckConstraint("impronta ~ '^[0-9a-f]{64}$'", name="ck_sessioni_revocate_impronta"),
        Index("idx_sessioni_revocate_jti", "jti", postgresql_where=text("jti IS NOT NULL")),
        Index("idx_sessioni_revocate_scadenza", "scade_il"),
        {"schema": "public"},
    )

    impronta: Mapped[str] = mapped_column(String(64), primary_key=True)
    jti: Mapped[str | None] = mapped_column(String(200))
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revocata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ += ["SessioneRevocata"]  # noqa: PLE0605 — revisione R05 (R12)


class SessioneAttiva(Base):
    """R06 (VD-C-127, migrazione 20261030_045): sessione del portale con il
    limite d'inattività fissato all'accesso e l'ultima attività (rinnovo a
    scorrimento, ``libs.shared.security.inattivita``). Tabella di
    autenticazione: fuori dalla Row Level Security."""
    __tablename__ = "sessioni_attive"
    __table_args__ = (
        CheckConstraint("impronta ~ '^[0-9a-f]{64}$'", name="ck_sessioni_attive_impronta"),
        CheckConstraint("inattivita_secondi > 0", name="ck_sessioni_attive_inattivita"),
        Index("idx_sessioni_attive_utente", "user_id",
              postgresql_where=text("user_id IS NOT NULL")),
        Index("idx_sessioni_attive_scadenza", "scade_il"),
        {"schema": "public"},
    )

    impronta: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ruolo: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("''"))
    inattivita_secondi: Mapped[int] = mapped_column(Integer, nullable=False)
    ultima_attivita: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    creata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ += ["SessioneAttiva"]  # noqa: PLE0605 — R06 (VD-C-127)


class PreferenzeUtente(Base):
    """R07 (D31, D29; migrazione 20261030_046): preferenze personali salvate sul
    server (tema, densità, pagina iniziale, colonne, ente scelto in testata).
    Una riga per utente; ``versione`` per il salvataggio con la versione letta.
    Senza ``tenant_id``: si legge e si scrive solo per l'utente del token."""
    __tablename__ = "preferenze_utente"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(preferenze) = 'object' AND "
                        "octet_length(preferenze::text) <= 16384",
                        name="ck_preferenze_utente_oggetto"),
        CheckConstraint("versione >= 1", name="ck_preferenze_utente_versione"),
        {"schema": "public"},
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="CASCADE"),
        primary_key=True)
    preferenze: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class AvvisoAutomatico(Base):
    """R07 (D13, D17; migrazione 20261030_046): avviso automatico all'ente,
    registrato una volta per (tipo, ente, chiave) nella stessa transazione
    dell'evento nell'outbox (quota all'80/100% del giorno, versamenti senza
    Idempotency-Key nella settimana). Row Level Security per ente."""
    __tablename__ = "avvisi_automatici"
    __table_args__ = (
        UniqueConstraint("tipo", "tenant_id", "chiave", name="uq_avvisi_automatici"),
        Index("idx_avvisi_automatici_ente", "tenant_id", text("created_at DESC")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tipo: Mapped[str] = mapped_column(String(60), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False)
    chiave: Mapped[str] = mapped_column(String(100), nullable=False)
    dati: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ += ["AvvisoAutomatico", "PreferenzeUtente"]  # noqa: PLE0605 — R07


# ── R09 ciclo di vita automatico (migrazione 20261101_048) ───────────────────

class CicloVitaPeriodico(Base):
    """Passo del lavoro periodico del lavoratore di ciclo-vita (D04, D06, D20, D21):
    lease (una replica per passo) e ultimo giro con l'esito. Tabella operativa
    senza ente: fuori dalla Row Level Security come ``verifiche_stato``."""
    __tablename__ = "ciclo_vita_periodico"
    __table_args__ = ({"schema": "public"},)

    nome: Mapped[str] = mapped_column(String(200), primary_key=True)
    ultimo_giro: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_esito: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                         server_default=text("'{}'::jsonb"))
    lease_detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())
    # R12a (VD-C-151, migrazione 20261105_052): fencing token del lease
    generazione: Mapped[int] = mapped_column(BigInteger, nullable=False,
                                             server_default=text("0"))


Index("idx_aip_retention_permanente", Aip.retention_fino_a.nulls_first(), Aip.id,
      postgresql_where=text("anni_conservazione = 999"))

__all__ += ["CicloVitaPeriodico"]  # noqa: PLE0605 — aggiunte R09


# ── R10 accesso, ruoli e notifiche (migrazione 20261102_049) ─────────────────

#: Permessi mai delegabili (``libs.verumdoc_rules.permessi.MAI_DELEGABILI``): il
#: vincolo del database li esclude da ogni ruolo personalizzato.
_MAI_DELEGABILI_SQL = ("ARRAY['scarto:esegui', 'scarto:autorizza', 'annullamento:approva', "
                       "'firmatari:gestisci', 'pratica:chiudi_eccezioni', 'utenti:gestisci', "
                       "'ruoli:gestisci', 'accesso:configura', "
                       "'firma_fornitori:gestisci', 'pec_canali:gestisci', "
                       "'pratica:riprendi', 'documentazione:genera']::text[]")


class FornitoreIdentita(Base):
    """R10 (D08): fornitore d'identità registrato dal conservatore (uno per tipo):
    SPID_CIE (aggregatore OIDC), PASSKEY (WebAuthn), PASSWORD_TOTP. In
    ``configurazione`` mai segreti (il segreto del cliente OIDC resta nella
    variabile d'ambiente del gateway). Configurazione del servizio senza ente:
    fuori dalla Row Level Security (motivo in docs/architettura/sicurezza.md)."""
    __tablename__ = "fornitori_identita"
    __table_args__ = (
        UniqueConstraint("tipo", name="uq_fornitori_identita_tipo"),
        CheckConstraint("tipo IN ('SPID_CIE', 'PASSKEY', 'PASSWORD_TOTP')",
                        name="ck_fornitori_identita_tipo"),
        CheckConstraint("jsonb_typeof(configurazione) = 'object'",
                        name="ck_fornitori_identita_configurazione"),
        CheckConstraint("versione >= 1", name="ck_fornitori_identita_versione"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    configurazione: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    aggiornato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class RuoloPersonalizzato(Base):
    """R10 (D30): ruolo dell'ente composto da permessi concedibili su un ruolo base
    (OPERATORE o VIEWER). Mai cancellato (si disattiva); ``versione`` per il
    salvataggio con la versione letta. Row Level Security per ente."""
    __tablename__ = "ruoli_personalizzati"
    __table_args__ = (
        UniqueConstraint("tenant_id", "nome", name="uq_ruoli_personalizzati_nome"),
        UniqueConstraint("tenant_id", "id", name="uq_ruoli_personalizzati_tenant_id"),
        CheckConstraint("ruolo_base IN ('OPERATORE', 'VIEWER')",
                        name="ck_ruoli_personalizzati_base"),
        CheckConstraint(f"NOT (permessi && {_MAI_DELEGABILI_SQL})",
                        name="ck_ruoli_personalizzati_non_delegabili"),
        CheckConstraint("versione >= 1", name="ck_ruoli_personalizzati_versione"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    descrizione: Mapped[str] = mapped_column(String(500), nullable=False,
                                             server_default=text("''"))
    ruolo_base: Mapped[str] = mapped_column(String(20), nullable=False)
    permessi: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False,
                                                server_default=text("'{}'::text[]"))
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


User.__table__.append_constraint(ForeignKeyConstraint(
    ["tenant_id", "ruolo_personalizzato_id"],
    ["public.ruoli_personalizzati.tenant_id", "public.ruoli_personalizzati.id"],
    name="fk_users_ruolo_personalizzato", ondelete="RESTRICT"))
Index("idx_users_ruolo_personalizzato", User.ruolo_personalizzato_id,
      postgresql_where=text("ruolo_personalizzato_id IS NOT NULL"))


class CredenzialePassword(Base):
    """R10 (D08): password (bcrypt) di un utente con accesso PASSWORD_TOTP, con i
    tentativi falliti e il blocco temporaneo. Tabella di autenticazione: fuori
    dalla RLS (si legge prima che l'ente sia noto), diritti SOLO al gruppo
    ``verumdoc_credenziali_accesso`` (login del gateway)."""
    __tablename__ = "credenziali_password"
    __table_args__ = (
        CheckConstraint("tentativi_falliti >= 0", name="ck_credenziali_password_tentativi"),
        {"schema": "public"},
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"),
        primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    impronta: Mapped[str] = mapped_column(String(100), nullable=False)
    tentativi_falliti: Mapped[int] = mapped_column(Integer, nullable=False,
                                                   server_default=text("0"))
    bloccato_fino_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    impostata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                   server_default=func.now())


class CredenzialeTotp(Base):
    """R10 (D08): segreto TOTP (RFC 6238) CIFRATO con AES-256-GCM (chiave del
    gateway, dato associato ``totp:<user_id>``) e ultimo passo usato (un codice
    non vale due volte). Stessi diritti di ``credenziali_password``."""
    __tablename__ = "credenziali_totp"
    __table_args__ = ({"schema": "public"},)

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"),
        primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    segreto_cifrato: Mapped[str] = mapped_column(Text, nullable=False)
    ultimo_passo: Mapped[int] = mapped_column(BigInteger, nullable=False,
                                              server_default=text("0"))
    confermato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


class CodiceRecupero(Base):
    """R10 (D08): codici di recupero monouso (impronta SHA-256, mai in chiaro)."""
    __tablename__ = "codici_recupero"
    __table_args__ = (
        UniqueConstraint("user_id", "impronta", name="uq_codici_recupero"),
        CheckConstraint("impronta ~ '^[0-9a-f]{64}$'", name="ck_codici_recupero_impronta"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    impronta: Mapped[str] = mapped_column(String(64), nullable=False)
    usato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class CredenzialePasskey(Base):
    """R10 (D08): passkey WebAuthn (chiave pubblica COSE, contatore delle firme)."""
    __tablename__ = "credenziali_passkey"
    __table_args__ = (
        UniqueConstraint("credential_id", name="uq_credenziali_passkey_credential"),
        Index("idx_credenziali_passkey_utente", "user_id"),
        CheckConstraint("contatore >= 0", name="ck_credenziali_passkey_contatore"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    credential_id: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    chiave_pubblica: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    contatore: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    nome: Mapped[str] = mapped_column(String(100), nullable=False,
                                      server_default=text("'Passkey'"))
    creata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                server_default=func.now())
    ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invito(Base):
    """R10 (D08): invito per e-mail con attivazione guidata. Del collegamento resta
    solo l'impronta SHA-256 (monouso, scadenza, legato all'e-mail). Un solo invito
    IN_ATTESA per utente. Row Level Security per ente (il collegamento porta l'ente)."""
    __tablename__ = "inviti"
    __table_args__ = (
        UniqueConstraint("impronta", name="uq_inviti_impronta"),
        CheckConstraint("stato IN ('IN_ATTESA', 'USATO', 'REVOCATO')", name="ck_inviti_stato"),
        CheckConstraint("impronta ~ '^[0-9a-f]{64}$'", name="ck_inviti_impronta"),
        CheckConstraint("tentativi_errati >= 0", name="ck_inviti_tentativi"),
        Index("uq_inviti_in_attesa", "user_id", unique=True,
              postgresql_where=text("stato = 'IN_ATTESA'")),
        Index("idx_inviti_ente", "tenant_id", text("created_at DESC")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT"),
        nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"),
        nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    impronta: Mapped[str] = mapped_column(String(64), nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False,
                                       server_default=text("'IN_ATTESA'"))
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metodo_attivato: Mapped[str | None] = mapped_column(String(30))
    tentativi_errati: Mapped[int] = mapped_column(Integer, nullable=False,
                                                  server_default=text("0"))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class NotifichePreferenze(Base):
    """R10 (D32): cosa riceve un utente e come (console, e-mail) per categoria.
    Row Level Security per ente; ``versione`` per il salvataggio con la versione letta."""
    __tablename__ = "notifiche_preferenze"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(scelte) = 'object' AND octet_length(scelte::text) <= 4096",
                        name="ck_notifiche_preferenze_scelte"),
        CheckConstraint("versione >= 1", name="ck_notifiche_preferenze_versione"),
        {"schema": "public"},
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"),
        primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scelte: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                   server_default=text("'{}'::jsonb"))
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class NotificaLettura(Base):
    """R10 (D32): avviso della campanella letto (o «fatto») da un utente. La
    campanella è una lettura di ``eventi_cliente``: qui solo lo stato per persona.
    Row Level Security per ente."""
    __tablename__ = "notifiche_letture"
    __table_args__ = (
        Index("idx_notifiche_letture_ente", "tenant_id", "letta_il"),
        {"schema": "public"},
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="RESTRICT"),
        primary_key=True)
    evento_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    letta_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                               server_default=func.now())


__all__ += ["CodiceRecupero", "CredenzialePasskey", "CredenzialePassword",  # noqa: PLE0605
            "CredenzialeTotp", "FornitoreIdentita", "Invito", "NotificaLettura",
            "NotifichePreferenze", "RuoloPersonalizzato"]


# ── R12a criticità aperte (migrazioni 20261104_051 e seguenti) ───────────────

class CicloVitaTransizioneR12(Base):
    """VD-C-145: allegati autorizzativi caricati prima di R12a in pratiche di scarto
    allora aperte (scritta SOLO dalla migrazione 20261104_051; il lavoratore la
    legge): per loro l'assenza del riferimento alla pratica è un avviso. Senza
    ente: fuori dalla Row Level Security come ``ciclo_vita_periodico``."""
    __tablename__ = "ciclo_vita_transizione_r12"
    __table_args__ = ({"schema": "public"},)

    allegato_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    pratica_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    registrato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


__all__ += ["CicloVitaTransizioneR12"]  # noqa: PLE0605 — aggiunte R12a


class RegistroWormOggetto(Base):
    """VD-C-148 (migrazione 20261105_052): oggetto ``registro/`` della hashchain con il
    termine del blocco WORM, per il rinnovo mai in riduzione. Senza ente."""
    __tablename__ = "registro_worm_oggetti"
    __table_args__ = (
        CheckConstraint("chiave LIKE 'registro/%'", name="ck_registro_worm_oggetti_prefisso"),
        Index("idx_registro_worm_oggetti_termine", text("conserva_fino_a NULLS FIRST"),
              "chiave"),
        {"schema": "public"},
    )

    chiave: Mapped[str] = mapped_column(String(500), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(200), primary_key=True,
                                            server_default=text("''"))
    conserva_fino_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rinnovato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


class RegistroWormCensimento(Base):
    """VD-C-148: cursore del censimento degli oggetti ``registro/`` (una riga)."""
    __tablename__ = "registro_worm_censimento"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_registro_worm_censimento_unica"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    ultima_chiave: Mapped[str | None] = mapped_column(String(500))
    completato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


__all__ += ["RegistroWormCensimento", "RegistroWormOggetto"]  # noqa: PLE0605 — R12a


# ── R13a firma e marca qualificate (migrazione 20261107_054) ─────────────────

from sqlalchemy import CHAR as _CHAR  # noqa: E402 — impronte esadecimali a lunghezza fissa


class FornitoreFirma(Base):
    """R13a: fornitore di firma/sigillo (CSC, PKCS11, SVILUPPO) o di marca (RFC3161,
    SVILUPPO) registrato dal conservatore in console, con priorità (ripiego). In
    ``configurazione`` mai segreti (``libs.shared.firma_qualificata``): stanno nel
    Secret del servizio di firma. Nessun DELETE (si disattiva). Senza ente: fuori
    dalla RLS (motivo in docs/architettura/sicurezza.md)."""
    __tablename__ = "fornitori_firma"
    __table_args__ = (
        UniqueConstraint("codice", name="uq_fornitori_firma_codice"),
        CheckConstraint("codice ~ '^[a-z][a-z0-9-]{1,39}$'", name="ck_fornitori_firma_codice"),
        CheckConstraint("(ruolo = 'FIRMA' AND tipo IN ('CSC', 'PKCS11', 'SVILUPPO')) OR "
                        "(ruolo = 'MARCA' AND tipo IN ('RFC3161', 'SVILUPPO'))",
                        name="ck_fornitori_firma_tipo"),
        CheckConstraint("priorita BETWEEN 1 AND 100", name="ck_fornitori_firma_priorita"),
        CheckConstraint("jsonb_typeof(configurazione) = 'object'",
                        name="ck_fornitori_firma_configurazione"),
        CheckConstraint("versione >= 1", name="ck_fornitori_firma_versione"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    codice: Mapped[str] = mapped_column(String(40), nullable=False)
    ruolo: Mapped[str] = mapped_column(String(10), nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    priorita: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))
    configurazione: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    aggiornato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class FirmaDocumento(Base):
    """R13a: documento del conservatore da sigillare (IPdA, RdV, manuale, piano):
    DA_FIRMARE → FIRMATO | NON_FIRMATO, busta nel WORM e dati per la rimarcatura.
    Origine nello storage (IPdA, RdV, con versione e impronta) o nel contenuto
    (manuale e piano). RLS per ente; nessun DELETE."""
    __tablename__ = "firme_documenti"
    __table_args__ = (
        CheckConstraint("tipo IN ('IPDA', 'RDV', 'MANUALE', 'PIANO')",
                        name="ck_firme_documenti_tipo"),
        CheckConstraint("stato IN ('DA_FIRMARE', 'FIRMATO', 'NON_FIRMATO', 'ERRORE')",
                        name="ck_firme_documenti_stato"),
        CheckConstraint("jsonb_typeof(storico_buste) = 'array'",
                        name="ck_firme_documenti_storico"),
        CheckConstraint("(tipo IN ('IPDA', 'RDV')) = (tenant_id IS NOT NULL AND "
                        "aip_id IS NOT NULL)", name="ck_firme_documenti_pacchetto"),
        CheckConstraint("(contenuto IS NULL) = (origine_chiave IS NOT NULL)",
                        name="ck_firme_documenti_origine"),
        CheckConstraint("contenuto IS NULL OR octet_length(contenuto) <= 5242880",
                        name="ck_firme_documenti_contenuto"),
        CheckConstraint("stato <> 'FIRMATO' OR (busta_sha256 IS NOT NULL AND "
                        "firmato_il IS NOT NULL)", name="ck_firme_documenti_firmato"),
        Index("uq_firme_documenti_pacchetto", "tipo", "aip_id", unique=True,
              postgresql_where=text("aip_id IS NOT NULL")),
        Index("uq_firme_documenti_conservatore", "tipo", "impronta_sha256", unique=True,
              postgresql_where=text("aip_id IS NULL")),
        Index("idx_firme_documenti_da_firmare", text("prossimo_tentativo_il NULLS FIRST"),
              "created_at", postgresql_where=text("stato <> 'FIRMATO'")),
        Index("idx_firme_documenti_scadenza",
              text("LEAST(certificato_scadenza, marca_scadenza)"),
              postgresql_where=text("stato = 'FIRMATO'")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    aip_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    origine_bucket: Mapped[str | None] = mapped_column(String(200))
    origine_chiave: Mapped[str | None] = mapped_column(String(1024))
    origine_version_id: Mapped[str | None] = mapped_column(String(1024))
    contenuto: Mapped[bytes | None] = mapped_column(_LargeBinary)
    impronta_sha256: Mapped[str] = mapped_column(_CHAR(64), nullable=False)
    stato: Mapped[str] = mapped_column(String(15), nullable=False,
                                       server_default=text("'DA_FIRMARE'"))
    motivo: Mapped[str | None] = mapped_column(String(500))
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    prossimo_tentativo_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    formato: Mapped[str | None] = mapped_column(String(10))
    livello: Mapped[str | None] = mapped_column(String(10))
    busta_chiave: Mapped[str | None] = mapped_column(String(1024))
    busta_version_id: Mapped[str | None] = mapped_column(String(1024))
    busta_sha256: Mapped[str | None] = mapped_column(_CHAR(64))
    busta: Mapped[bytes | None] = mapped_column(_LargeBinary)
    fornitore_firma: Mapped[str | None] = mapped_column(String(40))
    fornitore_marca: Mapped[str | None] = mapped_column(String(40))
    qualificata: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                              server_default=text("false"))
    certificato_sha256: Mapped[str | None] = mapped_column(_CHAR(64))
    certificato_soggetto: Mapped[str | None] = mapped_column(String(500))
    certificato_scadenza: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marca_scadenza: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    algoritmo: Mapped[str | None] = mapped_column(String(10))
    data_marca: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    firmato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rinnovi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rinnovato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rinnovo_motivo: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    # seconda revisione R13a: errori dopo i quali il documento va in ERRORE; buste
    # precedenti (chiave, version_id, impronta) a ogni rimarcatura
    errori: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    storico_buste: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                server_default=text("'[]'::jsonb"))


class FirmaTransizioneR13(Base):
    """R13a: PdA in lavorazione alla migrazione 054 (scritto SOLO dalla migrazione):
    senza riga dell'IPdA non si bloccano con FIRMA_SENZA_FORNITORE=BLOCCA."""
    __tablename__ = "firma_transizione_r13"
    __table_args__ = ({"schema": "public"},)

    aip_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    registrato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


__all__ += ["FirmaDocumento", "FirmaTransizioneR13", "FornitoreFirma"]  # noqa: PLE0605 — R13a


# ── R13b canali esterni predisposti (migrazione 20261108_055) ───────────────

class CanalePec(Base):
    """R13b (VD-C-078): canale PEC del conservatore (SMTP/IMAP di un gestore, simulatore
    di SVILUPPO) registrato in console. In ``configurazione`` mai segreti
    (``libs.shared.pec``): stanno nel Secret del lavoratore della posta, legati agli
    host. Nessun DELETE (si disattiva). Senza ente: fuori dalla RLS."""
    __tablename__ = "canali_pec"
    __table_args__ = (
        UniqueConstraint("codice", name="uq_canali_pec_codice"),
        CheckConstraint("codice ~ '^[a-z][a-z0-9-]{1,39}$'", name="ck_canali_pec_codice"),
        CheckConstraint("tipo IN ('SMTP_IMAP', 'SVILUPPO')", name="ck_canali_pec_tipo"),
        CheckConstraint("priorita BETWEEN 1 AND 100", name="ck_canali_pec_priorita"),
        CheckConstraint("jsonb_typeof(configurazione) = 'object'",
                        name="ck_canali_pec_configurazione"),
        CheckConstraint("versione >= 1", name="ck_canali_pec_versione"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    codice: Mapped[str] = mapped_column(String(40), nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    priorita: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))
    configurazione: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    aggiornato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class PecCanaleStato(Base):
    """R13b: stato onesto di un canale PEC scritto dal lavoratore della posta (segreti
    presenti, problemi, ultimo invio e lettura; mai i segreti)."""
    __tablename__ = "pec_canali_stato"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(problemi) = 'array'",
                        name="ck_pec_canali_stato_problemi"),
        {"schema": "public"},
    )

    canale_codice: Mapped[str] = mapped_column(String(40), primary_key=True)
    segreti_presenti: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    problemi: Mapped[list] = mapped_column(JSONB, nullable=False,
                                           server_default=text("'[]'::jsonb"))
    ultimo_invio_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_lettura_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_errore: Mapped[str | None] = mapped_column(String(500))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


class PecMessaggio(Base):
    """R13b (VD-C-078): messaggio PEC per caso d'uso con lo stato fino alla ricevuta di
    consegna. RLS per ente (la PROVA del conservatore non ha ente); nessun DELETE;
    origine immutabile e stati da ricevuta solo con una ricevuta associata (trigger)."""
    __tablename__ = "pec_messaggi"
    __table_args__ = (
        CheckConstraint("stato IN ('IN_CODA', 'IN_INVIO', 'INVIATO', 'ACCETTATO', "
                        "'CONSEGNATO', 'NON_ACCETTATO', 'MANCATA_CONSEGNA', 'NON_PARTITO')",
                        name="ck_pec_messaggi_stato"),
        CheckConstraint("caso_uso IN ('SCARTO', 'RESTITUZIONE', 'RIVERSAMENTO', 'SCADENZE', "
                        "'PROVA')", name="ck_pec_messaggi_caso"),
        CheckConstraint("caso_uso = 'PROVA' OR tenant_id IS NOT NULL",
                        name="ck_pec_messaggi_ente"),
        CheckConstraint("octet_length(testo) <= 65536", name="ck_pec_messaggi_testo"),
        Index("uq_pec_messaggi_dedup", "tenant_id", "caso_uso", "destinatario", "oggetto_id",
              unique=True),
        Index("uq_pec_messaggi_message_id", "message_id", unique=True,
              postgresql_where=text("message_id IS NOT NULL")),
        Index("idx_pec_messaggi_da_inviare", text("prossimo_tentativo NULLS FIRST"),
              "created_at", postgresql_where=text("stato IN ('IN_CODA', 'IN_INVIO')")),
        Index("idx_pec_messaggi_cursore", text("created_at DESC"), text("id DESC")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT", name="fk_pec_messaggi_tenant"))
    caso_uso: Mapped[str] = mapped_column(String(20), nullable=False)
    oggetto_id: Mapped[str] = mapped_column(String(200), nullable=False)
    destinatario: Mapped[str] = mapped_column(String(320), nullable=False)
    oggetto: Mapped[str] = mapped_column(String(300), nullable=False)
    testo: Mapped[str] = mapped_column(Text, nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False,
                                       server_default=text("'IN_CODA'"))
    canale_codice: Mapped[str | None] = mapped_column(String(40))
    message_id: Mapped[str | None] = mapped_column(String(500))
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    prossimo_tentativo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    errore: Mapped[str | None] = mapped_column(String(500))
    inviato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accettato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consegnato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    esito_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class PecRicevuta(Base):
    """R13b (VD-C-078): messaggio letto dalla casella PEC (UID IMAP, idempotente) con
    l'esito della verifica e i dati FIRMATI della ricevuta; messaggio grezzo come
    evidenza. Append-only (trigger); RLS per ente."""
    __tablename__ = "pec_ricevute"
    __table_args__ = (
        UniqueConstraint("canale_codice", "imap_uidvalidity", "imap_uid",
                         name="uq_pec_ricevute_imap"),
        CheckConstraint("esito IN ('ASSOCIATA', 'NON_AUTENTICA', 'NON_ASSOCIATA', "
                        "'OLTRE_LIMITE')", name="ck_pec_ricevute_esito"),
        CheckConstraint("(esito = 'ASSOCIATA') = (messaggio_id IS NOT NULL)",
                        name="ck_pec_ricevute_associata"),
        CheckConstraint("eml IS NULL OR octet_length(eml) <= 52428800",
                        name="ck_pec_ricevute_eml"),
        Index("idx_pec_ricevute_messaggio", "messaggio_id",
              postgresql_where=text("messaggio_id IS NOT NULL")),
        Index("idx_pec_ricevute_non_associate", text("id DESC"),
              postgresql_where=text("esito <> 'ASSOCIATA'")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    canale_codice: Mapped[str] = mapped_column(String(40), nullable=False)
    imap_uidvalidity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    imap_uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    esito: Mapped[str] = mapped_column(String(20), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(500))
    messaggio_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.pec_messaggi.id", ondelete="RESTRICT",
                   name="fk_pec_ricevute_messaggio"))
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    tipo: Mapped[str | None] = mapped_column(String(30))
    msgid: Mapped[str | None] = mapped_column(String(500))
    identificativo: Mapped[str | None] = mapped_column(String(300))
    gestore: Mapped[str | None] = mapped_column(String(300))
    consegna: Mapped[str | None] = mapped_column(String(320))
    errore: Mapped[str | None] = mapped_column(String(40))
    errore_esteso: Mapped[str | None] = mapped_column(String(500))
    data_ricevuta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificato_sha256: Mapped[str | None] = mapped_column(_CHAR(64))
    certificato_soggetto: Mapped[str | None] = mapped_column(String(500))
    impronta_sha256: Mapped[str | None] = mapped_column(_CHAR(64))
    dimensione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    eml: Mapped[bytes | None] = mapped_column(_LargeBinary)
    ricevuta_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())


class NotificaPersonale(Base):
    """R13b (VD-C-163): voce personale della campanella (es. modifica delle proprie
    credenziali), separata dagli eventi dell'ente e dai webhook. INSERT dal gateway,
    lettura e «letto» da notification; cambia solo ``letto_il`` (trigger). RLS."""
    __tablename__ = "notifiche_personali"
    __table_args__ = (
        CheckConstraint("collegamento LIKE '/%'", name="ck_notifiche_personali_collegamento"),
        Index("idx_notifiche_personali_utente", "user_id", text("creato_il DESC")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="RESTRICT", name="fk_notifiche_personali_utente"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="RESTRICT",
                   name="fk_notifiche_personali_tenant"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(60), nullable=False)
    titolo: Mapped[str] = mapped_column(String(300), nullable=False)
    collegamento: Mapped[str] = mapped_column(String(300), nullable=False,
                                              server_default=text("'/preferenze/credenziali'"))
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                server_default=func.now())
    letto_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ += ["CanalePec", "NotificaPersonale", "PecCanaleStato", "PecMessaggio",  # noqa: PLE0605
            "PecRicevuta"]  # R13b


# ── R14 debito interno (migrazione 20261109_056) ─────────────────────────────

class LavoroPeriodico(Base):
    """R14 (VD-C-172): lease con fencing token (``generazione``) dei lavori periodici dei
    servizi senza una tabella propria (pulizia delle notifiche personali del lavoratore
    della posta), come ``ciclo_vita_periodico``. Senza ente: fuori dalla RLS."""
    __tablename__ = "lavori_periodici"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(ultimo_esito) = 'object'",
                        name="ck_lavori_periodici_esito"),
        {"schema": "public"},
    )

    nome: Mapped[str] = mapped_column(String(200), primary_key=True)
    lease_detentore: Mapped[str | None] = mapped_column(String(200))
    lease_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generazione: Mapped[int] = mapped_column(BigInteger, nullable=False,
                                             server_default=text("0"))
    ultimo_giro: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_esito: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                         server_default=text("'{}'::jsonb"))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


class FirmeArretratoIpda(Base):
    """R14 (VD-C-165): cursore (una riga) del censimento degli IPdA dei PdA conservati
    prima della migrazione 054, scritto dalla hashchain con il lease del registro."""
    __tablename__ = "firme_arretrato_ipda"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_firme_arretrato_ipda_una_riga"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"),
                                    autoincrement=False)
    ultimo_aip: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    inseriti: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    completato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


__all__ += ["FirmeArretratoIpda", "LavoroPeriodico"]  # noqa: PLE0605 — R14


# ── R16 residui interni (migrazione 20261110_057) ────────────────────────────

class CredenzialeFirmaPersonale(Base):
    """R16 (VD-C-166, VD-P-140): credenziale di firma PERSONALE (``credentialID`` CSC
    presso un fornitore del registro e impronta ATTESA del certificato) di una persona del
    conservatore. Nessun segreto (PIN e OTP li dà la persona a ogni firma). Una sola
    attiva per persona; nessun DELETE (si disattiva). Senza ente: fuori dalla RLS."""
    __tablename__ = "credenziali_firma_personali"
    __table_args__ = (
        CheckConstraint("certificato_sha256 ~ '^[0-9a-f]{64}$'",
                        name="ck_credenziali_firma_personali_cert"),
        CheckConstraint("length(btrim(credential_id)) > 0",
                        name="ck_credenziali_firma_personali_cred"),
        CheckConstraint("versione >= 1", name="ck_credenziali_firma_personali_versione"),
        Index("uq_credenziali_firma_personali_attiva", "user_id", unique=True,
              postgresql_where=text("attiva")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                          ForeignKey("public.users.id"), nullable=False)
    fornitore_codice: Mapped[str] = mapped_column(String(40), nullable=False)
    credential_id: Mapped[str] = mapped_column(String(200), nullable=False)
    certificato_sha256: Mapped[str] = mapped_column(_CHAR(64), nullable=False)
    descrizione: Mapped[str | None] = mapped_column(String(200))
    attiva: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    versione: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    aggiornato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class FirmaResponsabile(Base):
    """R16 (VD-C-166): documento da firmare dal responsabile con la firma PERSONALE e
    l'OTP (manuale, piano, nomine o altro ``tipo``): DA_FIRMARE → FIRMATO → ARCHIVIATO |
    RESPINTO; DA_FIRMARE → ANNULLATO. Trigger ``firme_responsabile_protette`` su INSERT,
    UPDATE, DELETE e TRUNCATE; diritti per colonna. Senza ente: fuori dalla RLS."""
    __tablename__ = "firme_responsabile"
    __table_args__ = (
        CheckConstraint("tipo ~ '^[A-Z][A-Z0-9_]{1,29}$'", name="ck_firme_responsabile_tipo"),
        CheckConstraint("stato IN ('DA_FIRMARE', 'FIRMATO', 'ARCHIVIATO', 'RESPINTO', "
                        "'ANNULLATO')", name="ck_firme_responsabile_stato"),
        CheckConstraint("octet_length(contenuto) BETWEEN 1 AND 5242880",
                        name="ck_firme_responsabile_contenuto"),
        CheckConstraint("impronta_sha256 ~ '^[0-9a-f]{64}$'",
                        name="ck_firme_responsabile_impronta"),
        CheckConstraint("stato NOT IN ('FIRMATO', 'ARCHIVIATO') OR (busta IS NOT NULL AND "
                        "busta_sha256 IS NOT NULL AND firmato_da IS NOT NULL AND firmato_il IS "
                        "NOT NULL AND credenziale_id IS NOT NULL AND certificato_sha256 IS NOT "
                        "NULL)", name="ck_firme_responsabile_firmato"),
        CheckConstraint("stato <> 'ARCHIVIATO' OR (busta_chiave IS NOT NULL AND archiviato_il "
                        "IS NOT NULL)", name="ck_firme_responsabile_archiviato"),
        Index("uq_firme_responsabile_documento", "tipo", "impronta_sha256", unique=True,
              postgresql_where=text("stato NOT IN ('ANNULLATO', 'RESPINTO')")),
        Index("idx_firme_responsabile_da_archiviare", text("prossimo_tentativo_il NULLS FIRST"),
              "created_at", postgresql_where=text("stato = 'FIRMATO'")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    titolo: Mapped[str] = mapped_column(String(300), nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    contenuto: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)
    impronta_sha256: Mapped[str] = mapped_column(_CHAR(64), nullable=False)
    stato: Mapped[str] = mapped_column(String(12), nullable=False,
                                       server_default=text("'DA_FIRMARE'"))
    richiesto_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    firmatario_atteso: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    firmato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    firmato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credenziale_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.credenziali_firma_personali.id"))
    busta: Mapped[bytes | None] = mapped_column(_LargeBinary)
    busta_sha256: Mapped[str | None] = mapped_column(_CHAR(64))
    certificato_sha256: Mapped[str | None] = mapped_column(_CHAR(64))
    certificato_soggetto: Mapped[str | None] = mapped_column(String(500))
    livello: Mapped[str | None] = mapped_column(String(10))
    busta_chiave: Mapped[str | None] = mapped_column(String(1024))
    busta_version_id: Mapped[str | None] = mapped_column(String(1024))
    archiviato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    annullato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    motivo: Mapped[str | None] = mapped_column(String(500))
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    prossimo_tentativo_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class VersioneAccesso(Base):
    """R16 (VD-C-153): contatori per ambito (``*`` o ente) e tipo (METODI, CONCEDIBILI)
    alzati dal trigger di ``impostazioni_valori``; la revoca li confronta con il token."""
    __tablename__ = "versioni_accesso"
    __table_args__ = (
        CheckConstraint("tipo IN ('METODI', 'CONCEDIBILI')", name="ck_versioni_accesso_tipo"),
        {"schema": "public"},
    )

    ambito: Mapped[str] = mapped_column(String(40), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(12), primary_key=True)
    versione: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    aggiornato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


__all__ += ["CredenzialeFirmaPersonale", "FirmaResponsabile",  # noqa: PLE0605 — R16
            "VersioneAccesso"]


# ── R15 documentazione di conformità (migrazione 20261111_058) ───────────────

class DocumentazioneGenerazione(Base):
    """R15 (VD-C-177): richiesta di generazione della documentazione del servizio:
    IN_CORSO → COMPLETATA | ERRORE, una sola IN_CORSO alla volta. Senza ente: fuori dalla
    RLS. Trigger ``documentazione_generazioni_protette`` (un solo passaggio, nessuna DELETE)."""
    __tablename__ = "documentazione_generazioni"
    __table_args__ = (
        CheckConstraint("stato IN ('IN_CORSO', 'COMPLETATA', 'ERRORE')",
                        name="ck_documentazione_generazioni_stato"),
        CheckConstraint("(stato = 'IN_CORSO') = (conclusa_il IS NULL)",
                        name="ck_documentazione_generazioni_conclusa"),
        CheckConstraint("jsonb_typeof(esito) = 'object'",
                        name="ck_documentazione_generazioni_esito"),
        Index("uq_documentazione_generazioni_in_corso", "stato", unique=True,
              postgresql_where=text("stato = 'IN_CORSO'")),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    stato: Mapped[str] = mapped_column(String(12), nullable=False,
                                       server_default=text("'IN_CORSO'"))
    richiesta_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    richiesta_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                   server_default=func.now())
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    conclusa_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    esito: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False,
                                                  server_default=text("'{}'::jsonb"))
    errore: Mapped[str | None] = mapped_column(String(500))


class DocumentazioneVersione(Base):
    """R15 (VD-C-177): versione IMMUTABILE di un documento generato (manuale, piano della
    sicurezza, guida, catalogo delle route): Markdown e HTML, impronte riverificate dal
    database, sezioni, differenze, fonti. Trigger ``documentazione_versioni_nuove`` e
    ``documentazione_versioni_protette``; archivio WORM registrato una volta dalla hashchain."""
    __tablename__ = "documentazione_versioni"
    __table_args__ = (
        UniqueConstraint("tipo", "versione", name="uq_documentazione_versioni_tipo_versione"),
        CheckConstraint("tipo IN ('MANUALE_CONSERVAZIONE', 'PIANO_SICUREZZA', "
                        "'GUIDA_FUNZIONALITA', 'CATALOGO_ROUTE')",
                        name="ck_documentazione_versioni_tipo"),
        CheckConstraint("versione >= 1", name="ck_documentazione_versioni_versione"),
        CheckConstraint("octet_length(markdown) <= 5242880",
                        name="ck_documentazione_versioni_markdown"),
        CheckConstraint("octet_length(html) <= 8388608", name="ck_documentazione_versioni_html"),
        CheckConstraint("jsonb_typeof(sezioni) = 'array' AND jsonb_typeof(differenze) = "
                        "'object' AND jsonb_typeof(fonti) = 'object' AND "
                        "jsonb_typeof(da_compilare) = 'array' AND "
                        "jsonb_typeof(da_verificare) = 'array'",
                        name="ck_documentazione_versioni_json"),
        CheckConstraint("(archiviata_il IS NULL) = (worm_chiave_md IS NULL) AND "
                        "(archiviata_il IS NULL) = (worm_chiave_html IS NULL)",
                        name="ck_documentazione_versioni_archivio"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    versione: Mapped[int] = mapped_column(Integer, nullable=False)
    precedente_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.documentazione_versioni.id"))
    generazione_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.documentazione_generazioni.id"),
        nullable=False)
    titolo: Mapped[str] = mapped_column(String(200), nullable=False)
    nome_file: Mapped[str] = mapped_column(String(100), nullable=False)
    markdown: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)
    html: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)
    impronta_contenuto: Mapped[str] = mapped_column(_CHAR(64), nullable=False)
    impronta_sha256: Mapped[str] = mapped_column(_CHAR(64), nullable=False)
    impronta_html: Mapped[str] = mapped_column(_CHAR(64), nullable=False)
    sezioni: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    differenze: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fonti: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    da_compilare: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    da_verificare: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    generata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    creato_da: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    worm_chiave_md: Mapped[str | None] = mapped_column(String(1024))
    worm_versione_md: Mapped[str | None] = mapped_column(String(1024))
    worm_chiave_html: Mapped[str | None] = mapped_column(String(1024))
    worm_versione_html: Mapped[str | None] = mapped_column(String(1024))
    archiviata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ += ["DocumentazioneGenerazione", "DocumentazioneVersione"]  # noqa: PLE0605 — R15


# ── R17 collaudo finale (migrazione 20261112_059) ────────────────────────────

class ConfermaDecisione(Base):
    """R17 (VD-P-164): conferma di una decisione sensibile FIRMATA DAL GATEWAY
    (``libs.shared.conferme_decisione``), registrata dall'API di ciclo-vita con la decisione.
    Il trigger ``conferme_decisione_nuove`` fissa ``registrata_il`` e ``versione_pratica`` e
    pretende colonne uguali al payload firmato; append-only; nonce unico. RLS per ente."""
    __tablename__ = "conferme_decisione"
    __table_args__ = (
        CheckConstraint("azione IN ('SCARTO_APPROVA', 'SCARTO_ESEGUI', "
                        "'RIVERSAMENTO_USCITA_ESEGUI', 'ANNULLAMENTO_RICHIEDI', "
                        "'ANNULLAMENTO_APPROVA', 'RETTIFICA_RICHIEDI', 'PRATICA_RIPRENDI')",
                        name="ck_conferme_decisione_azione"),
        CheckConstraint("nonce ~ '^[0-9a-f]{32}$'", name="ck_conferme_decisione_nonce"),
        CheckConstraint("octet_length(firma) = 64", name="ck_conferme_decisione_firma"),
        CheckConstraint("octet_length(payload) BETWEEN 2 AND 4096",
                        name="ck_conferme_decisione_payload"),
        UniqueConstraint("nonce", name="uq_conferme_decisione_nonce"),
        Index("idx_conferme_decisione_pratica", "pratica_id"),
        {"schema": "public"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    pratica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                          name="fk_conferme_decisione_pratica"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.tenants.id", ondelete="RESTRICT",
                                          name="fk_conferme_decisione_ente"),
        nullable=False)
    azione: Mapped[str] = mapped_column(String(40), nullable=False)
    kid: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(_CHAR(32), nullable=False)
    utente_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    payload: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)
    firma: Mapped[bytes] = mapped_column(_LargeBinary, nullable=False)
    versione_pratica: Mapped[int] = mapped_column(Integer, nullable=False,
                                                  server_default=text("0"))
    registrata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


class ConfermaLavoratore(Base):
    """R17 (VD-P-164): fatti del SOLO lavoratore sulle conferme (append-only): VERIFICATA
    (nonce consumato, unico), SOSPESA (la ripresa richiede una conferma successiva),
    GIRO_CHIUSO (un nuovo giro di esecuzione richiede una conferma successiva) e
    RIPRESA_SISTEMA (ripresa automatica del lavoro periodico)."""
    __tablename__ = "conferme_lavoratore"
    __table_args__ = (
        CheckConstraint("evento IN ('VERIFICATA', 'SOSPESA', 'GIRO_CHIUSO', 'RIPRESA_SISTEMA')",
                        name="ck_conferme_lavoratore_evento"),
        CheckConstraint("(evento = 'VERIFICATA') = (conferma_id IS NOT NULL AND nonce IS NOT "
                        "NULL AND azione IS NOT NULL)", name="ck_conferme_lavoratore_campi"),
        Index("uq_conferme_lavoratore_nonce", "nonce", unique=True,
              postgresql_where=text("evento = 'VERIFICATA'")),
        Index("idx_conferme_lavoratore_pratica", "pratica_id", "id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    pratica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                          name="fk_conferme_lavoratore_pratica"),
        nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    evento: Mapped[str] = mapped_column(String(20), nullable=False)
    conferma_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.conferme_decisione.id", ondelete="RESTRICT",
                                          name="fk_conferme_lavoratore_conferma"))
    nonce: Mapped[str | None] = mapped_column(_CHAR(32))
    azione: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class ConfermaTransizioneR17(Base):
    """R17: pratiche aperte alla migrazione 059 con le azioni già decise prima (scritta solo
    dalla migrazione): per quelle azioni il lavoratore non pretende la conferma."""
    __tablename__ = "conferme_transizione_r17"
    __table_args__ = ({"schema": "public"},)

    pratica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("public.pratiche.id", ondelete="RESTRICT",
                                          name="fk_conferme_transizione_r17_pratica"),
        primary_key=True)
    azioni: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    registrata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    server_default=func.now())


__all__ += ["ConfermaDecisione", "ConfermaLavoratore",  # noqa: PLE0605 — R17
            "ConfermaTransizioneR17"]
