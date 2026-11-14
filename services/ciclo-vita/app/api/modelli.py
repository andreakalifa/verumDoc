# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/api/modelli.py — modelli Pydantic delle API del ciclo di vita (contratto Q2).

Riferimenti: REGOLE_SVILUPPO §17.5 (input e output tipizzati, descrizioni con la
regola), §9.6 (azioni consentite con il motivo di blocco).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain import stati as st

TipoPratica = Literal["RETTIFICA", "ANNULLAMENTO", "SCARTO", "RIVERSAMENTO_USCITA",
                      "RIVERSAMENTO_INGRESSO", "RESTITUZIONE"]
Motivo = Field(..., min_length=10, max_length=2000,
               description="Motivo (almeno 10 caratteri, REGOLE §15.7): entra in audit e "
                           "cronologia")
Impronta = Field(None, pattern=r"^[0-9a-f]{64}$",
                 description="Impronta del piano restituita dall'anteprima: se presente la "
                             "richiesta APPLICA (crea la pratica), altrimenti è un'anteprima")


class Criteri(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classi: list[str] = Field(default_factory=list, max_length=200,
                              description="Codici delle classi documentali (vuoto = tutte)")
    anni: list[int] = Field(default_factory=list, max_length=100,
                            description="Anni di versamento (ora italiana)")
    scadenza_entro: date | None = Field(
        None, description="PdA con termine di conservazione entro questa data (inclusa)")


class RichiestaProposta(BaseModel):
    """Proposta di scarto: ``anni`` OPPURE ``scadenza_entro``."""
    model_config = ConfigDict(extra="forbid")
    ente: str = Field(..., description="Identificativo dell'ente (UUID)")
    criteri: Criteri
    motivo: str = Motivo
    impronta_piano: str | None = Impronta


class RichiestaRiversamentoUscita(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ente: str
    criteri: Criteri = Field(default_factory=Criteri)
    conservatore_destinatario: str = Field(..., min_length=3, max_length=500)
    motivo: str = Motivo
    impronta_piano: str | None = Impronta


class RichiestaRestituzione(BaseModel):
    """Restituzione di fine contratto: TUTTI i PdA dell'ente più i registri."""
    model_config = ConfigDict(extra="forbid")
    ente: str
    conservatore_destinatario: str | None = Field(
        None, max_length=500, description="Nuovo conservatore (vuoto: restituzione all'ente)")
    motivo: str = Motivo
    impronta_piano: str | None = Impronta


class RichiestaRiversamentoIngresso(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ente: str
    caricamento_id: str = Field(..., description="Sessione di caricamento COMPLETA del gateway "
                                                 "con il PdD (ZIP) dell'altro conservatore")
    classe_documentale: str = Field(..., min_length=1, max_length=50)
    conservatore_origine: str = Field(..., min_length=2, max_length=500)
    metadati: dict[str, Any] = Field(
        default_factory=dict, description="Metadati del documento principale che integrano o "
                                          "sostituiscono quelli letti dall'indice")
    motivo: str = Motivo


class ConfermaFirmata(BaseModel):
    """R17 (VD-P-164): conferma della decisione firmata dal gateway
    (``POST /api/v1/auth/conferme-decisione``), così come restituita."""

    model_config = ConfigDict(extra="forbid")
    payload: str = Field(..., max_length=6000, description="Oggetto canonico (base64url)")
    firma: str = Field(..., max_length=200, description="Firma Ed25519 del gateway (base64url)")


_CONFERMA = Field(None, description=(
    "R17 (VD-P-164): conferma firmata dal gateway; obbligatoria per le decisioni sensibili "
    "quando la conferma delle decisioni è attiva (sempre in produzione)"))


class RichiestaRettifica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aip_id: str | None = None
    pdv_id: str | None = None
    metadati_nuovi: dict[str, Any] = Field(..., description="Metadati del documento principale "
                                                            "da rettificare (nome → valore)")
    motivo: str = Motivo
    conferma: ConfermaFirmata | None = _CONFERMA


class DocumentoFirmato(BaseModel):
    """R08 (D22): documento firmato (CAdES .p7m o PDF PAdES) che cita il versamento."""

    model_config = ConfigDict(extra="forbid")
    nome_file: str = Field(..., min_length=1, max_length=255)
    contenuto: str = Field(..., min_length=4, max_length=14_000_000,
                           description="Contenuto in base64 (al massimo 10 MB)")


class RichiestaAnnullamento(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pdv_id: str
    motivo: str = Motivo
    documento_firmato: DocumentoFirmato | None = Field(
        None, description=("Richiesta firmata che riporta l'identificativo del versamento: "
                           "facoltativa o obbligatoria secondo ANNULLAMENTO_RICHIESTA_FIRMATA"))
    conferma: ConfermaFirmata | None = _CONFERMA


class RichiestaTransizione(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stato_atteso: str = Field(..., description="Stato letto dal client (409 se è cambiato)")
    nuovo_stato: str
    allegati: list[str] = Field(default_factory=list, max_length=20,
                                description="Allegati della pratica citati dalla transizione")
    motivo: str = Motivo
    conferma: ConfermaFirmata | None = _CONFERMA


class RichiestaApprovazione(BaseModel):
    """R09 (D23): decisione della seconda persona su un annullamento in attesa."""

    model_config = ConfigDict(extra="forbid")
    esito: Literal["APPROVA", "RESPINGI"]
    motivo: str = Motivo
    conferma: ConfermaFirmata | None = _CONFERMA


class RichiestaRipresa(BaseModel):
    """Revisione R14 (4): ripresa di una pratica sospesa dopo i tentativi esauriti."""

    model_config = ConfigDict(extra="forbid")
    versione: int = Field(..., ge=1, description="Versione attesa della pratica (§12.5)")
    motivo: str = Field(..., min_length=20, max_length=2000,
                        description="Causa corretta e motivo della ripresa (resta nell'audit)")
    conferma: ConfermaFirmata | None = _CONFERMA


class StatoAutomatico(BaseModel):
    """R09: lavoro periodico del ciclo di vita e stato dei blocchi WORM."""

    model_config = ConfigDict(extra="allow")
    passi: list[dict[str, Any]]
    retention: dict[str, Any]
    in_parole: list[str]


class Esecuzione(BaseModel):
    lavoro: str | None = None
    tentativi: int | None = None
    prossimo_tentativo: datetime | None = None
    ultimo_errore: dict[str, Any] | None = None
    in_corso_fino_al: datetime | None = None
    #: R14 (VD-C-133): lavoro sospeso dopo i tentativi esauriti (None = non sospesa)
    lavoro_sospeso: str | None = None
    #: revisione R14 (4): chi legge può riprendere la pratica (conservatore, sospesa)
    ripresa_consentita: bool = False


class PraticaRiepilogo(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    tipo: TipoPratica
    stato: str
    versione: int
    ente: str
    motivo: str
    pdv_id: str | None = None
    pdv_risultato_id: str | None = None
    richiede_nulla_osta: bool
    # R13b (VD-C-162): autorizzazione del produttore richiesta (riversamento in uscita)
    richiede_autorizzazione_produttore: bool = False
    impronta_piano: str | None = None
    creata_da: str | None = None
    creata_ruolo: str | None = None
    creata_il: datetime
    aggiornata_il: datetime
    conclusa_il: datetime | None = None
    conteggi: dict[str, Any] = Field(default_factory=dict)
    esecuzione: Esecuzione


class AzioneConsentita(BaseModel):
    nuovo_stato: str
    allegati_richiesti: list[str]
    esecuzione: bool
    consentita: bool
    motivo_blocco: str | None = None
    #: R09 (D20): lunghezza minima del motivo (chiusura con eccezioni)
    motivo_minimo: int | None = None


class DettaglioPratica(PraticaRiepilogo):
    dati: dict[str, Any] = Field(default_factory=dict)
    cronologia: list[dict[str, Any]]
    autorizzazioni: list[dict[str, Any]]
    allegati: list[dict[str, Any]]
    oggetti: dict[str, Any]
    azioni_consentite: list[AzioneConsentita]
    #: R09: esito in parole semplici e, per un annullamento in attesa, chi può approvare
    in_parole: str | None = None
    approvazione: dict[str, Any] | None = None


class ElencoPratiche(BaseModel):
    items: list[PraticaRiepilogo]
    limit: int
    cursore: str | None = Field(None, description="Da passare come `cursore` per la pagina dopo")
    altri: bool


class ElencoOggetti(BaseModel):
    items: list[dict[str, Any]]
    limit: int
    cursore: str | None = None
    altri: bool


class PianoAnteprima(BaseModel):
    """Anteprima (nessuna scrittura): da confermare ripetendo la richiesta con
    ``impronta_piano``."""
    model_config = ConfigDict(extra="allow")
    impronta_piano: str
    ente: str
    totale: int
    criteri: dict[str, Any]
    calcolata_il: datetime


class AllegatoCaricato(BaseModel):
    id: str
    tipo: str
    sha256: str
    dimensione: int
    nome_file: str
    firma: dict[str, Any]


TIPI_ALLEGATO_CARICABILI = tuple(sorted(st.ALLEGATI_CARICABILI))
