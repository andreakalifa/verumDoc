# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test: libs/shared/db e libs/shared/models
Servizio: gateway
Riferimento piano: F0.4 — Schema Database

Test unitari isolati — nessuna connessione DB richiesta.
"""
from __future__ import annotations

import re

import pytest

# ══════════════════════════════════════════════════════════════════
# VALIDAZIONE schema_name
# ══════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_validate_schema_name_valid_cases() -> None:
    """Schema name validi devono passare la regex di sicurezza."""
    valid_names = [
        "t_acme_corp", "t_comune_roma", "t_pa01",
        "t_test123", "t_azienda_spa_001",
    ]
    pattern = re.compile(r"^t_[a-z][a-z0-9_]{1,60}$")
    for name in valid_names:
        assert pattern.match(name), f"'{name}' dovrebbe essere valido"


@pytest.mark.unit
def test_validate_schema_name_invalid_cases() -> None:
    """Schema name non validi devono fallire — previene SQL injection."""
    invalid_names = [
        "acme_corp", "t_", "T_acme", "t_Acme",
        "t_acme corp", "t_acme; DROP TABLE",
        "t_acme--comment", "", "t_" + "a" * 62,
    ]
    pattern = re.compile(r"^t_[a-z][a-z0-9_]{1,60}$")
    for name in invalid_names:
        assert not pattern.match(name), f"'{name}' dovrebbe essere NON valido"


# ══════════════════════════════════════════════════════════════════
# MODELLI SCHEMA PUBLIC
# ══════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_tenant_model_has_required_columns() -> None:
    """Tenant deve avere le colonne core del business."""
    from libs.shared.models import Tenant
    columns = {c.key for c in Tenant.__table__.columns}
    required = {
        "id", "codice_fiscale", "ragione_sociale", "schema_name",
        "stato", "piano", "storage_quota_gb", "created_at", "email_referente",
    }
    missing = required - columns
    assert not missing, f"Colonne mancanti in Tenant: {missing}"


@pytest.mark.unit
def test_tenant_schema_name_is_unique() -> None:
    """
    schema_name deve essere univoco — campo identificativo del tenant.
    Nota v4.2: il campo è mantenuto per backward compat e identificazione,
    ma non viene più usato per creare schemi PostgreSQL separati.
    L'isolamento avviene tramite tenant_id su pdv e aip.
    """
    from libs.shared.models import Tenant
    # R03: vincolo con nome (uguale a quello della migrazione 001)
    unici = {
        tuple(c.name for c in v.columns)
        for v in Tenant.__table__.constraints if v.__class__.__name__ == "UniqueConstraint"
    }
    assert ("schema_name",) in unici


@pytest.mark.unit
def test_tenant_codice_fiscale_is_unique() -> None:
    """codice_fiscale deve essere univoco — identifica il soggetto giuridico."""
    from libs.shared.models import Tenant
    # R03: vincolo con nome (uguale a quello della migrazione 001)
    unici = {
        tuple(c.name for c in v.columns)
        for v in Tenant.__table__.constraints if v.__class__.__name__ == "UniqueConstraint"
    }
    assert ("codice_fiscale",) in unici


@pytest.mark.unit
def test_user_model_has_unique_constraint_on_tenant_email() -> None:
    """User deve avere unique constraint su (tenant_id, email)."""
    from libs.shared.models import User
    constraint_names = {c.name for c in User.__table__.constraints}
    assert "uq_users_tenant_email" in constraint_names


@pytest.mark.unit
def test_hashchain_block_has_unique_numero_blocco() -> None:
    """numero_blocco deve essere univoco — garantisce ordine sequenziale della catena."""
    from libs.shared.models import HashchainBlock
    col = HashchainBlock.__table__.c["numero_blocco"]
    assert col.unique is True


@pytest.mark.unit
def test_hashchain_block_has_crypto_fields() -> None:
    """HashchainBlock deve avere tutti i campi crittografici della catena."""
    from libs.shared.models import HashchainBlock
    cols = {c.key for c in HashchainBlock.__table__.columns}
    required = {
        "hash_blocco", "hash_blocco_precedente", "merkle_root",
        "firma_cades", "tsa_token", "tsa_timestamp",
    }
    missing = required - cols
    assert not missing, f"Campi crittografici mancanti in HashchainBlock: {missing}"


# ══════════════════════════════════════════════════════════════════
# MODELLI SCHEMA PER-TENANT
# ══════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_all_per_tenant_models_importable() -> None:
    """Tutti i modelli per-tenant devono essere importabili."""
    from libs.shared.models import (
        Aip,
        ClasseDocumentale,
        Documento,
        Fascicolo,
        FascicoloAip,
        LifecycleEvent,
        MetadatiAllegato5,
        Notification,
        Pdv,
    )
    expected = {
        "pdv", "aip", "documenti", "metadati_allegato5",
        "lifecycle_events", "fascicoli", "fascicoli_aip",
        "notifications", "classi_documentali",
    }
    actual = {
        Pdv.__tablename__, Aip.__tablename__, Documento.__tablename__,
        MetadatiAllegato5.__tablename__, LifecycleEvent.__tablename__,
        Fascicolo.__tablename__, FascicoloAip.__tablename__,
        Notification.__tablename__, ClasseDocumentale.__tablename__,
    }
    assert actual == expected


@pytest.mark.unit
def test_aip_model_has_crypto_proof_fields() -> None:
    """AIP deve avere i campi per le prove crittografiche di conservazione."""
    from libs.shared.models import Aip
    cols = {c.key for c in Aip.__table__.columns}
    required = {
        "hash_sha256", "firma_cades_ref", "tsa_token_ref",
        "tsa_timestamp", "hashchain_block_id", "s3_key", "s3_bucket",
    }
    missing = required - cols
    assert not missing, f"Campi crittografici mancanti in Aip: {missing}"


@pytest.mark.unit
def test_metadati_allegato5_has_core_agid_fields() -> None:
    """MetadatiAllegato5 deve avere i campi core dell'Allegato 5 AGID 2022."""
    from libs.shared.models import MetadatiAllegato5
    cols = {c.key for c in MetadatiAllegato5.__table__.columns}
    required = {
        "identificativo_documento", "modalita_formazione",
        "tipologia_documentale", "data_documento",
        "soggetto_produttore_id", "soggetto_produttore_nome",
        "oggetto", "formato", "stato_conservazione",
    }
    missing = required - cols
    assert not missing, f"Campi Allegato 5 mancanti: {missing}"


@pytest.mark.unit
def test_pdv_stato_enum_has_all_states() -> None:
    """PdvStato deve coprire tutti gli stati del ciclo di vita del versamento."""
    from libs.shared.models import PdvStato
    expected = {
        "RICEVUTO", "IN_VALIDAZIONE", "VALIDATO",
        "IN_BUILD", "CONSERVATO", "ERRORE", "ANNULLATO",
    }
    assert {s.value for s in PdvStato} == expected


@pytest.mark.unit
def test_aip_stato_enum_covers_full_lifecycle() -> None:
    """AipStato deve coprire tutti gli stati del ciclo di vita dell'AIP."""
    from libs.shared.models import AipStato
    expected = {
        "IN_BUILD", "FIRMATO", "TIMESTAMPATO", "SU_STORAGE",
        "IN_CATENA", "CONSERVATO", "ESIBIZIONE", "ELIMINAZIONE", "ELIMINAZIONE_INCOMPLETA",
    }
    assert {s.value for s in AipStato} == expected


@pytest.mark.unit
def test_storage_tier_enum_has_four_tiers() -> None:
    """StorageTier deve avere tutti e 4 i tier."""
    from libs.shared.models import StorageTier
    assert {t.value for t in StorageTier} == {"HOT", "WARM", "COLD", "ARCHIVAL"}


@pytest.mark.unit
def test_tenant_stato_enum_values() -> None:
    """TenantStato deve coprire i 4 stati del ciclo di vita del tenant."""
    from libs.shared.models import TenantStato
    assert {s.value for s in TenantStato} == {"TRIAL", "ATTIVO", "SOSPESO", "DISATTIVATO"}


# ══════════════════════════════════════════════════════════════════
# INIT DB
# ══════════════════════════════════════════════════════════════════

@pytest.mark.unit
async def test_get_session_raises_if_not_initialized() -> None:
    """get_async_session deve sollevare RuntimeError se init_db() non è stato chiamato."""
    import libs.shared.db as db_module

    original_engine  = db_module._engine
    original_factory = db_module._session_factory
    db_module._engine          = None
    db_module._session_factory = None

    try:
        with pytest.raises(RuntimeError, match="non inizializzato"):
            async with db_module.get_async_session():
                pass
    finally:
        db_module._engine          = original_engine
        db_module._session_factory = original_factory


@pytest.mark.unit
def test_db_settings_has_default_url() -> None:
    """DatabaseSettings deve avere un URL di default per sviluppo locale."""
    from libs.shared.db.config import DatabaseSettings
    settings = DatabaseSettings()
    assert "postgresql" in settings.database_url


@pytest.mark.unit
def test_db_settings_pool_size_reasonable() -> None:
    """Pool size deve essere tra 1 e 100."""
    from libs.shared.db.config import DatabaseSettings
    settings = DatabaseSettings()
    assert 1 <= settings.db_pool_size <= 100
    assert 0 <= settings.db_max_overflow <= 100
