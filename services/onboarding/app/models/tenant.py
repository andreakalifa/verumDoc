# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modelli Pydantic per l'API onboarding.
Riferimento piano: F1.5.1
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TenantCreate(BaseModel):
    ragione_sociale: str = Field(..., min_length=2, max_length=500)
    codice_fiscale: str = Field(..., min_length=11, max_length=16)
    email_referente: str = Field(..., description="Email contatto principale")
    pec_referente: str | None = None
    codice_ipa: str | None = Field(None, description="Codice IPA per enti PA")
    natura: Literal["PA", "PRIVATO"] | None = Field(
        None, description="Natura dell'ente (revisione R05, R9): PRIVATO esenta lo scarto dal "
                          "nulla osta della Soprintendenza (D.lgs. 42/2004, art. 21); se assente "
                          "l'ente si tratta come pubblica amministrazione (il caso più prudente)")
    piano: str = Field(default="starter", pattern="^(starter|business|enterprise)$")
    motivo_natura: str | None = Field(
        None, min_length=10, max_length=1000,
        description="Revisione 2 di R05 (S5): obbligatorio se natura = PRIVATO (esenzione "
                    "dal nulla osta della Soprintendenza): finisce nella voce d'audit "
                    "«tenant.natura_dichiarata» scritta nella transazione della creazione")

    @model_validator(mode="after")
    def _privato_con_motivo(self) -> TenantCreate:
        if self.natura == "PRIVATO" and not (self.motivo_natura or "").strip():
            raise ValueError("natura PRIVATO: indicare motivo_natura (almeno 10 caratteri): "
                             "esenta lo scarto dal nulla osta della Soprintendenza")
        return self


class TenantResponse(BaseModel):
    id: str
    ragione_sociale: str
    codice_fiscale: str
    schema_name: str
    stato: str
    piano: str
    email_referente: str
    natura: str | None = None
    #: R07 (D25): codice IPA dell'ente (precompila il motivo della natura PA)
    codice_ipa: str | None = None
    storage_quota_gb: int
    storage_usato_gb: float
    data_attivazione: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, tenant) -> TenantResponse:
        return cls(
            id=str(tenant.id),
            ragione_sociale=tenant.ragione_sociale,
            codice_fiscale=tenant.codice_fiscale,
            schema_name=tenant.schema_name,
            stato=tenant.stato.value,
            piano=tenant.piano,
            email_referente=tenant.email_referente,
            natura=tenant.natura,
            codice_ipa=tenant.codice_ipa,
            storage_quota_gb=tenant.storage_quota_gb,
            storage_usato_gb=tenant.storage_usato_gb,
            data_attivazione=tenant.data_attivazione,
            created_at=tenant.created_at,
        )


class TenantNaturaChange(BaseModel):
    """Dichiarazione della natura dell'ente (revisione R05, R9)."""

    natura: Literal["PA", "PRIVATO"] = Field(
        ..., description="PA: lo scarto richiede il nulla osta della Soprintendenza archivistica "
                         "(D.lgs. 42/2004, art. 21); PRIVATO: non lo richiede")
    motivo: str | None = Field(
        None, max_length=500,
        description="Motivo della dichiarazione (REGOLE §15.7), es. atto o visura. R07 (D25): "
                    "obbligatorio (almeno 10 caratteri) solo per PRIVATO; per PA è facoltativo "
                    "e, se manca, si registra il codice IPA dell'ente quando c'è")

    @model_validator(mode="after")
    def _motivo_per_privato(self) -> TenantNaturaChange:
        testo = (self.motivo or "").strip()
        if self.natura == "PRIVATO" and len(testo) < 10:
            raise ValueError("natura PRIVATO: indicare il motivo (almeno 10 caratteri), es. "
                             "visura camerale")
        self.motivo = testo or None
        return self


class TenantStatoChange(BaseModel):
    stato: str = Field(..., description="Nuovo stato: ATTIVO, SOSPESO, DISATTIVATO")
    motivo: str | None = Field(None, max_length=500, description="Motivo del cambio stato")


class TenantList(BaseModel):
    total: int
    items: list[TenantResponse]
