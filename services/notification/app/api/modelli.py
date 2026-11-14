# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/api/modelli.py
Modelli Pydantic delle API eventi e webhook — contratto C3 (REGOLE §17.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class Risorsa(BaseModel):
    tipo: str = Field(...,
                      description="Tipo della risorsa: versamento, caricamento, pdd")
    id: str = Field(..., description="Identificativo della risorsa (UUID)")


class Evento(BaseModel):
    id: int = Field(..., description="Identificativo dell'evento (stesso valore di "
                                     "`VerumDoc-Id-Evento` nei webhook): per scartare i duplicati")
    tipo: str = Field(..., description="Tipo stabile, es. versamento.conservato")
    creato_il: datetime
    risorsa: Risorsa
    dati: dict[str, Any] = Field(default_factory=dict,
                                 description="Dati essenziali del tipo (vedi documentazione)")
    posizione: int | None = Field(
        None, description="Posizione dell'evento nell'elenco (crescente, assegnata alla "
                          "pubblicazione): è il cursore. Solo nell'elenco, non nei webhook")


class ElencoEventi(BaseModel):
    eventi: list[Evento]
    cursore: int = Field(..., description="Posizione dell'ultimo evento restituito (o il `dopo` "
                                          "ricevuto se l'elenco è vuoto): passarlo come `dopo`")
    altri: bool = Field(..., description="True se ci sono altri eventi dopo il cursore")


class NuovaSottoscrizione(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=9, max_length=2048,
                     description="URL https del sistema che riceve gli eventi")
    tipi: list[str] = Field(..., min_length=1, max_length=50,
                            description="Tipi di evento da ricevere")
    descrizione: str | None = Field(None, max_length=200)
    attiva: StrictBool = True


class ModificaSottoscrizione(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(None, min_length=9, max_length=2048)
    tipi: list[str] | None = Field(None, min_length=1, max_length=50)
    descrizione: str | None = Field(None, max_length=200)
    attiva: StrictBool | None = None

    @model_validator(mode="after")
    def _almeno_un_campo(self) -> ModificaSottoscrizione:
        if not self.model_fields_set:
            raise ValueError("indicare almeno un campo da modificare")
        for campo in ("url", "tipi", "attiva"):
            if campo in self.model_fields_set and getattr(self, campo) is None:
                raise ValueError(f"{campo} non può essere null")
        return self


class Sottoscrizione(BaseModel):
    id: str
    url: str
    tipi: list[str]
    descrizione: str | None = None
    attiva: bool
    segreto_suffisso: str = Field(..., description="Ultimi 4 caratteri del segreto")
    creata_il: datetime
    aggiornata_il: datetime
    # R13b: rotazione del segreto con doppia firma nel periodo di transizione
    segreto_precedente_suffisso: str | None = Field(
        None, description="Ultimi 4 caratteri del segreto precedente, ancora valido fino a "
                          "transizione_fino_al (le consegne portano due firme v1)")
    transizione_fino_al: datetime | None = None
    segreto_ruotato_il: datetime | None = None


class SottoscrizioneCreata(Sottoscrizione):
    segreto: str = Field(..., description="Segreto HMAC: mostrato SOLO in questa risposta, "
                                          "conservarlo per verificare la firma")


class ElencoSottoscrizioni(BaseModel):
    sottoscrizioni: list[Sottoscrizione]


class Consegna(BaseModel):
    id: int = Field(..., description="Valore dell'intestazione VerumDoc-Id-Consegna")
    evento_id: int
    tipo_evento: str
    stato: str = Field(..., description="IN_CODA, IN_CONSEGNA, CONSEGNATA, FALLITA, ANNULLATA")
    tentativi: int
    riprove: int = Field(0, description="Ripetizioni chieste dal cliente")
    prossimo_tentativo: datetime | None = None
    ultimo_stato_http: int | None = None
    ultimo_codice: str | None = Field(
        None, description="Codice del catalogo dell'ultimo esito (WH-1xx)")
    ultimo_errore: str | None = None
    ultimo_tentativo_il: datetime | None = None
    consegnata_il: datetime | None = None
    creata_il: datetime | None = None


class ElencoConsegne(BaseModel):
    consegne: list[Consegna]
    cursore: int
    altri: bool


class ConsegnaRiaccodata(BaseModel):
    id: int
    evento_id: int
    stato: str
    tentativi: int
    riprove: int
    prossimo_tentativo: datetime | None = None


class TipoSottoscrivibile(BaseModel):
    """Voce del catalogo dei tipi di evento (R05: letta dalla console)."""

    tipo: str
    risorsa: str
    descrizione: str


class CatalogoTipi(BaseModel):
    tipi: list[TipoSottoscrivibile]


class RotazioneSegreto(BaseModel):
    """R13b: rotazione del segreto HMAC con periodo di transizione."""
    model_config = ConfigDict(extra="forbid")
    transizione_ore: int = Field(24, ge=1, le=168,
                                 description="Ore in cui il segreto precedente resta valido: "
                                             "ogni consegna porta la firma con il nuovo E con il "
                                             "precedente (due valori v1)")


class SegretoRuotato(Sottoscrizione):
    segreto: str = Field(..., description="Nuovo segreto HMAC: mostrato SOLO in questa risposta")
