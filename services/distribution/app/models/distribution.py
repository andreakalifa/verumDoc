# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modelli Pydantic dei pacchetti di distribuzione (PdD) e della ricerca.
Riferimento piano: F2.1.2; R03 b4 (VD-C-077, VD-C-093).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr


class TipoPdD(str, Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    COMPLETO   = "COMPLETO"    # documenti + IPdA + manifesto
    ESTRATTO   = "ESTRATTO"    # documenti richiesti + IPdA
    ESIBIZIONE = "ESIBIZIONE"  # documenti + IPdA + manifesto, per l'esibizione
    MIGRAZIONE = "MIGRAZIONE"  # ZIP con documenti, IPdA, manifesto e dati di migrazione


class ConsegnaPdD(str, Enum):  # noqa: UP042 — str() dei membri usato nei dati: StrEnum lo cambierebbe
    LINK = "LINK"   # collegamenti prefirmati ai file, impronte verificate alla richiesta
    ZIP = "ZIP"     # pacchetto unico preparato fuori dalla richiesta (evento pdd.pronto)


class RichiestaDistribuzione(BaseModel):
    aip_id: str = Field(..., description="UUID dell'AIP da distribuire")
    tipo: TipoPdD
    motivo: str = Field(..., min_length=5, max_length=500,
                        description="Motivazione della richiesta (audit)")
    # G2: il richiedente è l'utente del token verificato; il valore inviato dal
    # client è ignorato (accettato solo per compatibilità con i client esistenti)
    richiedente_id: str | None = Field(
        None, description="Ignorato: il richiedente è ricavato dal token"
    )
    # Solo per ESTRATTO: lista nomi file richiesti
    file_richiesti: list[str] | None = Field(
        None, description="Per tipo ESTRATTO: nomi dei file del pacchetto da includere",
        max_length=10_000,
    )
    # Durata dei collegamenti (massimo PDD_DURATA_LINK_MAX_SECONDI)
    url_expiry_seconds: int | None = Field(None, ge=60, le=604_800)
    consegna: ConsegnaPdD | None = Field(
        None, description="LINK o ZIP; predefinito: LINK fino a PDD_SINCRONO_MAX_MB "
                          "(ZIP sopra, e sempre ZIP per MIGRAZIONE)",
    )


class DocumentoDistribuito(BaseModel):
    nome_file: str
    tipo: str
    formato_mime: str
    # Impronta registrata alla conservazione; con consegna LINK è VERIFICATA sui
    # byte dello storage prima di consegnare il collegamento.
    hash_sha256: str | None = None
    dimensione_bytes: int | None = None
    # Mai valorizzata in risposta (chiavi interne di storage): resta per compatibilità
    s3_key: str | None = None
    signed_url: str | None = None
    impronta_verificata: bool = False


class PacchettoZip(BaseModel):
    url: str | None = None
    dimensione_bytes: int | None = None
    hash_sha256: str | None = None


class RispostaDistribuzione(BaseModel):
    pdd_id: str
    aip_id: str
    tipo: str
    stato: str = "PRONTO"
    consegna: str = "LINK"
    documenti: list[DocumentoDistribuito]
    sincro_xml_incluso: bool
    hash_pdd: str | None
    created_at: str
    url_scadenza: str | None = None
    link_stato: str | None = None
    zip: PacchettoZip | None = None
    errore: dict[str, Any] | None = None
    messaggio: str | None = None
    # File con le chiavi interne (solo per il registro del PdD, mai in risposta)
    _file_interni: list[dict[str, Any]] = PrivateAttr(default_factory=list)


class AIPSearchRequest(BaseModel):
    query: str | None = Field(None, description="Ricerca full-text", max_length=500)
    tenant_id: str | None = Field(
        None, description="Solo profili del conservatore (SUPER_ADMIN, RSC_CONSERVATORE): "
                          "ente in cui cercare; per gli altri ignorato (ente dell'identità)")
    classe_documentale: str | None = None
    anno: str | None = None
    data_da: datetime | None = None
    data_a: datetime | None = None
    skip: int = Field(0, ge=0, json_schema_extra={"deprecated": True},
                      description="Non più supportato (R03): solo 0; usare `cursore`")
    limit: int = Field(20, ge=1, le=100)
    cursore: str | None = Field(None, max_length=500,
                                description="Cursore restituito dalla pagina precedente")


class AIPSearchResult(BaseModel):
    total: int = Field(..., description="Risultati contati, al massimo RICERCA_CONTEGGIO_MASSIMO")
    total_esatto: bool = Field(True, description="false: i risultati sono più di `total`")
    items: list[dict]
    cursore: str | None = Field(None, description="Da passare per la pagina successiva")
    altri: bool = False
    campi_testo: list[str] = Field(default_factory=list,
                                   description="Campi in cui cerca il testo")
