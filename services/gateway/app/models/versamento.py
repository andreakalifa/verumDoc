# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modelli Pydantic per il versamento.

F1-02 AGID: ``parametri`` di DocumentoIndice è MetadatiDocumento strutturato.
R03 (VD-C-094): i metadati NON si perdono più. ``MetadatiDocumento`` accetta
le chiavi non previste (``extra="allow"``): restano nell'indice, nell'evento,
nel database e nell'IPdA; la regola RGL-103 le segnala con un avviso. La
presenza dei 7 metadati minimi dell'Allegato 5 sul documento principale la
controlla il motore delle regole (RGL-101, locked, blocca): qui i campi sono
facoltativi perché il rifiuto elenchi TUTTI i mancanti in una volta; tipi e
formati restano controllati dallo schema.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from libs.shared.impronte import PATTERN_ALGORITMI

# VD-C-037: definizione UNICA della modalità di formazione (valori a/b/c/d,
# LG AgID §2.1.1, Allegato 5). Riesportata qui per compatibilità degli import.
from libs.shared.models import ModalitaFormazione


class HashInfo(BaseModel):
    impronta: str
    algoritmo: str = Field(default="SHA-256", pattern=PATTERN_ALGORITMI)


class MetadatiDocumento(BaseModel):
    """
    Metadati del documento (Allegato 5 LG AgID) più metadati propri della classe.

    I 7 campi minimi sono obbligatori sul documento PRINCIPALE (regola RGL-101).
    Chiavi non previste: ammesse e conservate (VD-C-094), segnalate da RGL-103;
    i metadati del profilo della classe si passano qui (al primo livello) o in
    ``metadati_extra``.
    """
    model_config = ConfigDict(extra="allow")

    # ── Minimi Allegato 5 (obbligatori sul principale: regola RGL-101) ─────
    identificativo_documento: str | None = Field(
        None, description="Identificativo univoco nel sistema documentale del versante"
    )
    modalita_formazione: ModalitaFormazione | None = Field(
        None, description="Modalità di formazione, lettera a/b/c/d (LG AgID §2.1.1, Allegato 5)"
    )
    tipologia_documentale: str | None = Field(
        None, description="Tipo documento (es. fattura, contratto, delibera)"
    )
    data_documento: date | None = Field(
        None, description="Data di formazione o firma del documento"
    )
    soggetto_produttore_id: str | None = Field(
        None, description="CF o P.IVA del soggetto che ha prodotto il documento"
    )
    soggetto_produttore_nome: str | None = Field(
        None, description="Denominazione del soggetto produttore"
    )
    oggetto: str | None = Field(
        None, min_length=5, max_length=1000,
        description="Descrizione sintetica del contenuto del documento"
    )
    # ── Opzionali ────────────────────────────────────────────
    lingua: str = Field(default="it", description="Lingua del documento (ISO 639-1)")
    classificazione: str | None = Field(default=None, description="Classificazione archivistica")
    fascicolo_id: str | None = Field(default=None, description="Riferimento al fascicolo")
    numero_registrazione: str | None = Field(
        default=None, description="Numero di protocollo/registrazione")
    data_registrazione: date | None = Field(default=None, description="Data di protocollazione")
    tipo_firma: str | None = Field(
        default=None, description="Tipo firma digitale (CAdES, PAdES, XAdES)")
    soggetto_destinatario: list[str] = Field(default_factory=list, description="Lista destinatari")
    codice_ipa: str | None = Field(default=None, description="Codice IPA ente produttore")
    aoo: str | None = Field(default=None, description="Area Organizzativa Omogenea")
    versione_documento: str = Field(default="1.0", description="Versione del documento")
    metadati_extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadati aggiuntivi liberi: conservati e riportati nell'IPdA",
    )


#: Nomi dei metadati previsti dallo schema (gli altri sono «non previsti», RGL-103).
CAMPI_METADATI_NOTI: frozenset[str] = frozenset(MetadatiDocumento.model_fields)


class DocumentoIndice(BaseModel):
    id: str
    tipo: str = Field(..., pattern="^(PRINCIPALE|ALLEGATO|ANNESSO|ANNOTAZIONE)$")
    nome_file: str
    formato: str | None = None
    hash: HashInfo
    # Metadati del documento: per il PRINCIPALE i minimi dell'Allegato 5 li
    # richiede la regola RGL-101; per gli altri tipi sono facoltativi.
    parametri: MetadatiDocumento | None = None
    allegati: list[DocumentoIndice] | None = None


class ChiaveUD(BaseModel):
    numero: str | None = None
    anno: str | None = None
    registro: str | None = None


class OverrideIndice(BaseModel):
    """Personalizzazione motivata delle impostazioni per QUESTO versamento (livello
    PDV della catena, R05 Q1). Quali impostazioni si possono personalizzare e la
    lunghezza minima del motivo li decide il motore delle regole
    (``libs.verumdoc_rules.override``): altrimenti 422 RGL-111."""
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(..., max_length=2000, description="Motivo (almeno 10 caratteri)")
    valori: dict[str, Any] = Field(..., description="Codice dell'impostazione → valore")


class IndiceUD(BaseModel):
    classe_documentale: str
    chiave: ChiaveUD | None = None
    documenti: list[DocumentoIndice] = Field(..., min_length=1)
    # R05 Q1: accordo di versamento da applicare (codice); assente = quello
    # dell'ente per la classe, se c'è. Un accordo non in vigore → 422 RGL-112.
    accordo: str | None = Field(
        None, pattern=r"^[A-Za-z0-9._-]{1,50}$",
        description="Codice dell'accordo di versamento dell'ente da applicare",
    )
    override: OverrideIndice | None = Field(
        None, description="Personalizzazione motivata delle impostazioni (livello PDV)",
    )


class AvvisoVersamento(BaseModel):
    """Rilievo non bloccante: salvato sul PdV e riportato nel rapporto di versamento."""
    codice: str = Field(..., description="Codice stabile (regola RGL-nnn o VRS-nnn)")
    effetto: str = Field("AVVISA", description="Sempre AVVISA per un avviso")
    messaggio: str
    campo: str | None = Field(None, description="Metadato o proprietà interessata")
    documento: str | None = Field(None, description="Id del documento nell'indice")


class VersamentoResponse(BaseModel):
    pdv_id: str
    stato: str = "RICEVUTO"
    message: str = "Versamento ricevuto. La validazione è in corso."
    avvisi: list[AvvisoVersamento] = Field(
        default_factory=list,
        description="Avvisi del motore delle regole e del servizio (non bloccanti)",
    )
    link_stato: str | None = Field(None, description="Percorso dello stato del PdV")
    rdv_preliminare_disponibile: bool = True
    nota_rdv: str = "Scarica RdV Preliminare: GET /api/v1/versamento/{pdv_id}/rdv"


class StatoPdvResponse(BaseModel):
    pdv_id: str
    stato: str
    classe_documentale: str | None = None
    errori: list[dict] | None = None
    rdv_preliminare: dict | None = None
    rdv_definitivo: dict | None = None
    ricevuto_at: str | None = None
    completato_at: str | None = None
