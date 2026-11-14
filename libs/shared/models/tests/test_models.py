# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test: libs/shared/models
Riferimento piano: F0.4 — Schema Database; REGOLE_SVILUPPO §10.4, §16.

Verifiche sui metadati SQLAlchemy (senza database). Il confronto con lo
schema prodotto davvero dalle migrazioni è in
services/gateway/tests/test_schema_alembic.py (PostgreSQL reale).

Nota R03: i vecchi test controllavano i default Python su oggetti appena
istanziati (che SQLAlchemy applica solo all'INSERT) e un enum di modalità
di formazione non conforme: erano sbagliati, sostituiti.
"""
from __future__ import annotations

import enum

import pytest
from sqlalchemy import Enum as SAEnum

from libs.shared import models
from libs.shared.models import (
    METADATI_ALLEGATO5_CAMPI,
    AipStato,
    Base,
    MetadatiAllegato5,
    ModalitaFormazione,
    PdvStato,
    StorageTier,
    TenantStato,
    UserRuolo,
)

# ── Enum ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_modalita_formazione_valori_allegato5() -> None:
    """VD-C-037: ModalitaDiFormazione dell'Allegato 5 = lettere a–d (LG §2.1.1)."""
    assert [m.value for m in ModalitaFormazione] == ["a", "b", "c", "d"]


@pytest.mark.unit
def test_modalita_formazione_salvata_come_lettera() -> None:
    """Nel database va la lettera (values_callable), non il nome del membro."""
    tipo = MetadatiAllegato5.__table__.c.modalita_formazione.type
    assert isinstance(tipo, SAEnum)
    assert list(tipo.enums) == ["a", "b", "c", "d"]
    assert tipo.name == "modalita_formazione"


@pytest.mark.unit
def test_enum_di_stato() -> None:
    assert {s.value for s in TenantStato} == {"TRIAL", "ATTIVO", "SOSPESO", "DISATTIVATO"}
    assert {s.value for s in PdvStato} == {
        "RICEVUTO", "IN_VALIDAZIONE", "VALIDATO", "IN_BUILD", "CONSERVATO", "ERRORE", "ANNULLATO",
    }
    assert {s.value for s in AipStato} == {
        "IN_BUILD", "FIRMATO", "TIMESTAMPATO", "SU_STORAGE",
        "IN_CATENA", "CONSERVATO", "ESIBIZIONE", "ELIMINAZIONE", "ELIMINAZIONE_INCOMPLETA",
    }
    assert {t.value for t in StorageTier} == {"HOT", "WARM", "COLD", "ARCHIVAL"}
    # Ordine identico al tipo PostgreSQL public.user_ruolo (RSC_CONSERVATORE in coda)
    assert [r.value for r in UserRuolo] == [
        "SUPER_ADMIN", "ADMIN_TENANT", "OPERATORE", "VIEWER", "API_SYSTEM", "RSC_CONSERVATORE",
    ]


#: Nome del tipo PostgreSQL atteso per ogni enum (stesso nome usato dalle migrazioni)
_NOMI_TIPI = {
    ("tenants", "stato"): "tenant_stato",
    ("users", "ruolo"): "user_ruolo",
    ("pdv", "stato"): "pdv_stato",
    ("pdv", "tipo_flusso"): "tipo_flusso",
    ("aip", "stato"): "aip_stato",
    ("aip", "storage_tier"): "storage_tier",
    ("classi_documentali", "tipologia_xml"): "tipo_sincro_xml",
    ("metadati_allegato5", "modalita_formazione"): "modalita_formazione",
}


@pytest.mark.unit
def test_enum_nativi_con_nome_delle_migrazioni() -> None:
    """VD-C-007: prima l'ORM usava «tenantstato»/«userruolo» o VARCHAR mentre
    le migrazioni creavano «tenant_stato»/«user_ruolo»: ogni INSERT falliva."""
    trovati = {}
    for tabella in Base.metadata.sorted_tables:
        for col in tabella.columns:
            if isinstance(col.type, SAEnum):
                assert col.type.native_enum, f"{tabella.name}.{col.name} non è un enum nativo"
                assert col.type.schema == "public", f"{tabella.name}.{col.name}: schema del tipo"
                trovati[(tabella.name, col.name)] = col.type.name
    assert trovati == _NOMI_TIPI


# ── Struttura ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_tutte_le_tabelle_in_public() -> None:
    assert {t.schema for t in Base.metadata.sorted_tables} == {"public"}


_TABELLE_CONSERVATE = {"pdv", "aip", "documenti"}


@pytest.mark.unit
def test_fk_verso_dati_conservati_restrict() -> None:
    """REGOLE §10.4: nessuna cascata verso PdV, PdA, documenti."""
    for tabella in Base.metadata.sorted_tables:
        for fk in tabella.foreign_keys:
            if fk.column.table.name in _TABELLE_CONSERVATE:
                assert (fk.ondelete or "").upper() == "RESTRICT", (
                    f"{tabella.name}.{fk.parent.name} → {fk.column.table.name}: "
                    f"ondelete={fk.ondelete!r}"
                )


@pytest.mark.unit
@pytest.mark.parametrize("tabella", [
    "pdv", "aip", "fascicoli", "notifications", "users", "api_keys", "contracts",
    "rsc_nominations", "pdnd_fruitori", "versamento_impronte",
])
def test_tenant_id_obbligatorio(tabella: str) -> None:
    col = Base.metadata.tables[f"public.{tabella}"].c.tenant_id
    assert col.nullable is False
    assert any(fk.column.table.name == "tenants" for fk in col.foreign_keys)


@pytest.mark.unit
def test_default_lato_server_per_lo_stato_del_cliente() -> None:
    """Il seed falliva (NOT NULL su tenants.stato) perché il default era solo Python."""
    assert Base.metadata.tables["public.tenants"].c.stato.server_default is not None
    assert Base.metadata.tables["public.users"].c.ruolo.server_default is not None


@pytest.mark.unit
def test_classi_codice_unico_per_cliente() -> None:
    """VD-C-023: nessun UNIQUE globale su codice; unicità (tenant_id, codice)
    con NULL uguali (classi di base)."""
    tabella = Base.metadata.tables["public.classi_documentali"]
    assert not tabella.c.codice.unique
    vincoli = {c.name: c for c in tabella.constraints if c.name == "uq_classi_tenant_codice"}
    vincolo = vincoli["uq_classi_tenant_codice"]
    assert [c.name for c in vincolo.columns] == ["tenant_id", "codice"]
    assert vincolo.dialect_options["postgresql"]["nulls_not_distinct"] is True


# ── Metadati Allegato 5: conteggio veritiero ─────────────────────

@pytest.mark.unit
def test_conteggio_metadati_allegato5_dichiarato_correttamente() -> None:
    """VD-C-037: il modello dichiarava «38 campi» e ne aveva meno."""
    doc = MetadatiAllegato5.__doc__ or ""
    assert f"{len(METADATI_ALLEGATO5_CAMPI)} campi di metadato" in doc


@pytest.mark.unit
def test_models_all_exports() -> None:
    for name in models.__all__:
        assert hasattr(models, name), f"{name} in __all__ ma non definito"
    classi = {
        n for n, v in vars(models).items()
        if isinstance(v, type) and issubclass(v, Base) and v is not Base
    }
    assert classi <= set(models.__all__)
    enum_esportati = {
        n for n, v in vars(models).items()
        if isinstance(v, type) and issubclass(v, enum.Enum) and v.__module__ == models.__name__
    }
    assert enum_esportati <= set(models.__all__)
