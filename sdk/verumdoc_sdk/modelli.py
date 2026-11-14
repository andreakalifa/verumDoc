# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modelli Pydantic dell'SDK: corrispondono alle risposte reali dei servizi VerumDoc
(gateway, distribution, notification, dashboard-api) alla R03.

I modelli di risposta accettano campi in più (``extra="allow"``): un campo nuovo
aggiunto dal servizio non rompe i client esistenti e resta leggibile in
``model_extra``. I modelli di richiesta (indice dell'unità documentaria)
seguono lo schema del gateway; ``hash`` di un documento è facoltativo perché
l'SDK lo calcola dai byte del file quando manca.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Risposta(BaseModel):
    """Base dei modelli di risposta: campi sconosciuti conservati in ``model_extra``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ── Errori (application/problem+json, RFC 9457) ────────────────────────

class CampoNonValido(_Risposta):
    campo: str
    posizione: str | None = None
    tipo: str
    messaggio: str


class Problema(_Risposta):
    """Errore del catalogo VerumDoc (``docs/catalogo/errori.md``)."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    codice: str
    esito: str = Field(..., description="NON_VALIDO, RIFIUTATO o ERRORE")
    ritentabile: bool = False
    dettagli: dict[str, Any] = Field(default_factory=dict)
    campi: list[CampoNonValido] = Field(default_factory=list)
    id_richiesta: str | None = None
    messaggio: str | None = None


# ── Indice dell'unità documentaria (richiesta di versamento) ─────────

class ImprontaFile(BaseModel):
    """Impronta del file dichiarata nell'indice (``hash`` del documento)."""

    impronta: str
    algoritmo: str = "SHA-256"


class MetadatiDocumento(BaseModel):
    """Metadati del documento (Allegato 5 LG AgID) più quelli propri della classe.

    I 7 minimi sono obbligatori sul documento PRINCIPALE (regola RGL-101 del
    servizio); le chiavi non previste sono ammesse e conservate.
    """

    model_config = ConfigDict(extra="allow")

    identificativo_documento: str | None = None
    modalita_formazione: str | None = Field(None, description="Lettera a/b/c/d")
    tipologia_documentale: str | None = None
    data_documento: date | None = None
    soggetto_produttore_id: str | None = None
    soggetto_produttore_nome: str | None = None
    oggetto: str | None = None
    lingua: str | None = None
    classificazione: str | None = None
    fascicolo_id: str | None = None
    numero_registrazione: str | None = None
    data_registrazione: date | None = None
    tipo_firma: str | None = None
    soggetto_destinatario: list[str] | None = None
    codice_ipa: str | None = None
    aoo: str | None = None
    versione_documento: str | None = None
    metadati_extra: dict[str, Any] | None = None


class DocumentoIndice(BaseModel):
    id: str
    tipo: str = Field("PRINCIPALE", description="PRINCIPALE, ALLEGATO, ANNESSO o ANNOTAZIONE")
    nome_file: str
    formato: str | None = None
    hash: ImprontaFile | None = Field(
        None, description="Se assente l'SDK la calcola (SHA-256) dai byte del file")
    parametri: MetadatiDocumento | None = None
    allegati: list[DocumentoIndice] | None = None


class ChiaveUD(BaseModel):
    numero: str | None = None
    anno: str | None = None
    registro: str | None = None


class IndiceUD(BaseModel):
    classe_documentale: str
    chiave: ChiaveUD | None = None
    documenti: list[DocumentoIndice] = Field(..., min_length=1)

    def documenti_tutti(self) -> list[DocumentoIndice]:
        """Documenti e allegati annidati, in ordine di visita."""
        esito: list[DocumentoIndice] = []
        coda = list(self.documenti)
        while coda:
            d = coda.pop(0)
            esito.append(d)
            coda[0:0] = list(d.allegati or [])
        return esito


# ── Versamento (gateway) ──────────────────────────────────────────────

class AvvisoVersamento(_Risposta):
    codice: str
    effetto: str = "AVVISA"
    messaggio: str
    campo: str | None = None
    documento: str | None = None


class RispostaVersamento(_Risposta):
    """Corpo di ``POST /api/v1/versamento/ud`` (202 nuovo, 200 ripetuto)."""

    pdv_id: str
    stato: str = "RICEVUTO"
    message: str = ""
    avvisi: list[AvvisoVersamento] = Field(default_factory=list)
    link_stato: str | None = None
    rdv_preliminare_disponibile: bool = True
    nota_rdv: str | None = None


class AnnullamentoVersamento(_Risposta):
    """Annullamento logico del PdV (pratica ANNULLAMENTO del ciclo di vita, 3.2)."""

    pratica_id: str
    annullato_il: str | None = None
    stato_precedente: str | None = None


class RettificaVersamento(_Risposta):
    """Catena di rettifica (3.2): ``rettifica_di`` sul PdV di rettifica (PdV
    originale e pratica), ``rettificato_da`` sull'originale (rettifiche chieste,
    dalla più recente). L'originale non cambia mai."""

    rettifica_di: dict[str, Any] | None = None
    rettificato_da: list[dict[str, Any]] = Field(default_factory=list)


class StatoVersamento(_Risposta):
    """Corpo di ``GET /api/v1/versamento/{pdv_id}``.

    ``annullamento`` e ``rettifica`` (3.2, ciclo di vita) ci sono solo quando
    il PdV è annullato o coinvolto in una rettifica; altrimenti ``None``."""

    pdv_id: str
    stato: str
    classe_documentale: str | None = None
    data_ricezione: datetime | None = None
    data_aggiornamento: datetime | None = None
    errori: list[Any] = Field(default_factory=list)
    rdv_preliminare: str | dict[str, Any] | None = None
    rdv_definitivo: str | dict[str, Any] | None = None
    aip_id: str | None = None
    urn: str | None = None
    annullamento: AnnullamentoVersamento | None = None
    rettifica: RettificaVersamento | None = None


class StatoPdV(StrEnum):
    RICEVUTO = "RICEVUTO"
    IN_VALIDAZIONE = "IN_VALIDAZIONE"
    VALIDATO = "VALIDATO"
    IN_BUILD = "IN_BUILD"
    CONSERVATO = "CONSERVATO"
    ERRORE = "ERRORE"
    ANNULLATO = "ANNULLATO"


#: Stati da cui un PdV non si muove più.
STATI_FINALI_PDV: frozenset[str] = frozenset(
    {StatoPdV.CONSERVATO, StatoPdV.ERRORE, StatoPdV.ANNULLATO})


# ── Sessioni di caricamento (gateway, contratto C1) ───────────────────

class Collegamenti(_Risposta):
    self_: str = Field(..., alias="self")
    parti: str
    completa: str


class SessioneCaricamento(_Risposta):
    """Corpo di ``POST /api/v1/caricamenti`` (201 nuova, 200 stessa Idempotency-Key)."""

    caricamento_id: str
    stato: str
    dimensione_parte: int
    numero_parti: int
    scade_il: datetime
    link: Collegamenti


class StatoCaricamento(_Risposta):
    """Corpo di ``GET /api/v1/caricamenti/{id}`` e di ``.../completa``."""

    caricamento_id: str
    stato: str = Field(..., description="APERTO, IN_VERIFICA, COMPLETO, SCADUTO, "
                                        "ANNULLATO, ERRORE")
    nome_file: str
    dimensione: int
    dimensione_parte: int
    numero_parti: int
    parti_ricevute: list[int] = Field(default_factory=list)
    scade_il: datetime
    impronte: dict[str, str] | None = None
    formato_rilevato: str | None = None
    errore: dict[str, Any] | None = None
    pdv_id: str | None = None
    link: Collegamenti


class ParteAccettata(_Risposta):
    parte: int
    dimensione: int
    ricevute: list[int]


# ── Eventi e webhook (notification, contratto C3) ─────────────────────

class Risorsa(_Risposta):
    tipo: str
    id: str


class Evento(_Risposta):
    #: Identità dell'evento (uguale a ``VerumDoc-Id-Evento`` dei webhook): per
    #: scartare i duplicati. NON è il cursore dell'elenco.
    id: int
    tipo: str
    creato_il: datetime
    risorsa: Risorsa
    dati: dict[str, Any] = Field(default_factory=dict)
    #: Posizione nell'elenco (crescente, assegnata alla pubblicazione): è il
    #: cursore da passare come ``dopo``. Solo nell'elenco, non nei webhook.
    posizione: int | None = None


class ElencoEventi(_Risposta):
    eventi: list[Evento]
    #: Posizione dell'ultimo evento della pagina (da passare come ``dopo``).
    cursore: int
    altri: bool


class Sottoscrizione(_Risposta):
    id: str
    url: str
    tipi: list[str]
    descrizione: str | None = None
    attiva: bool
    segreto_suffisso: str
    creata_il: datetime
    aggiornata_il: datetime
    # R13b: rotazione del segreto con doppia firma fino a transizione_fino_al
    segreto_precedente_suffisso: str | None = None
    transizione_fino_al: datetime | None = None
    segreto_ruotato_il: datetime | None = None


class SottoscrizioneCreata(Sottoscrizione):
    segreto: str = Field(..., description="Mostrato solo alla creazione: conservarlo")


class ElencoSottoscrizioni(_Risposta):
    sottoscrizioni: list[Sottoscrizione]


class Consegna(_Risposta):
    id: int
    evento_id: int
    tipo_evento: str
    stato: str
    tentativi: int
    riprove: int = 0
    prossimo_tentativo: datetime | None = None
    ultimo_stato_http: int | None = None
    ultimo_codice: str | None = None
    ultimo_errore: str | None = None
    ultimo_tentativo_il: datetime | None = None
    consegnata_il: datetime | None = None
    creata_il: datetime | None = None


class ElencoConsegne(_Risposta):
    consegne: list[Consegna]
    cursore: int
    altri: bool


class ConsegnaRiaccodata(_Risposta):
    id: int
    evento_id: int
    stato: str
    tentativi: int
    riprove: int
    prossimo_tentativo: datetime | None = None


# ── Distribuzione e ricerca (distribution) ────────────────────────────

class TipoPdD(StrEnum):
    COMPLETO = "COMPLETO"
    ESTRATTO = "ESTRATTO"
    ESIBIZIONE = "ESIBIZIONE"
    MIGRAZIONE = "MIGRAZIONE"


class ConsegnaPdD(StrEnum):
    LINK = "LINK"
    ZIP = "ZIP"


class DocumentoDistribuito(_Risposta):
    nome_file: str
    tipo: str
    formato_mime: str
    hash_sha256: str | None = None
    dimensione_bytes: int | None = None
    s3_key: str | None = None
    signed_url: str | None = None
    impronta_verificata: bool = False


class PacchettoZip(_Risposta):
    url: str | None = None
    dimensione_bytes: int | None = None
    hash_sha256: str | None = None


class PacchettoDistribuzione(_Risposta):
    """Corpo di ``POST /api/v2/distribution/richiedi`` e ``GET .../pdd/{id}``."""

    pdd_id: str
    aip_id: str
    tipo: str
    stato: str = Field("PRONTO", description="PRONTO, IN_PREPARAZIONE, ERRORE")
    consegna: str = "LINK"
    documenti: list[DocumentoDistribuito]
    sincro_xml_incluso: bool
    hash_pdd: str | None = None
    created_at: str
    url_scadenza: str | None = None
    link_stato: str | None = None
    zip: PacchettoZip | None = None
    errore: dict[str, Any] | None = None
    messaggio: str | None = None


class DocumentoTrovato(_Risposta):
    """Un risultato della ricerca (campi dell'indice di ricerca; stato dal database)."""

    aip_id: str
    pdv_id: str | None = None
    urn: str | None = None
    stato: str | None = None
    classe_documentale: str | None = None
    hash_sha256: str | None = None
    anno: str | None = None
    numero: str | None = None
    registro: str | None = None
    data_versamento: str | None = None
    data_conservazione: str | None = None
    scadenza_conservazione: str | None = None
    anni_conservazione: int | None = None
    dimensione_bytes: int | None = None
    oggetto: str | None = None
    identificativi_documento: list[str] = Field(default_factory=list)
    tipologie_documentali: list[str] = Field(default_factory=list)
    soggetti_produttori: str | None = None
    numeri_registrazione: list[str] = Field(default_factory=list)
    data_documento: str | None = None
    nomi_file: list[str] = Field(default_factory=list)


class RisultatoRicerca(_Risposta):
    """Corpo di ``POST /api/v2/distribution/search``."""

    #: Risultati contati, al massimo il tetto del servizio (``total_esatto`` falso
    #: se sono di più): non usarlo per calcolare le pagine, seguire il cursore.
    total: int
    total_esatto: bool = True
    items: list[DocumentoTrovato]
    cursore: str | None = None
    altri: bool = False
    #: Campi in cui il servizio cerca il testo (parole intere, non parti di parola).
    campi_testo: list[str] = Field(default_factory=list)


# ── Elenco dei versamenti (dashboard-api) ─────────────────────────────

class ClasseInElenco(_Risposta):
    codice: str | None = None
    nome: str | None = None


class MotivoRifiuto(_Risposta):
    codice: str | None = None
    messaggio: str | None = None
    passo: str | None = None
    tipo: str | None = None


class VersamentoInElenco(_Risposta):
    pdv_id: str
    stato: str
    data_ricezione: str | None = None
    data_aggiornamento: str | None = None
    oggetto: str | None = None
    numero_protocollo: str | None = None
    classe_documentale: ClasseInElenco | None = None
    aip_id: str | None = None
    motivo_rifiuto: str | None = None
    motivi_rifiuto: list[MotivoRifiuto] = Field(default_factory=list)


class ElencoVersamenti(_Risposta):
    """Corpo di ``GET /api/dashboard/versamenti`` (R03: paginazione a cursore).

    Nessun totale: ``altri`` dice se esiste una pagina successiva e ``cursore``
    (valorizzato solo se ``altri``) la apre. Il cursore è opaco: va ripassato così
    com'è, con gli stessi filtri."""

    items: list[VersamentoInElenco]
    limit: int
    cursore: str | None = None
    altri: bool = False


# ── Identità della richiesta (gateway, ``GET /api/v1/me``) ─────────────

class EnteIdentita(_Risposta):
    id: str
    ragione_sociale: str | None = None
    stato: str | None = None


class ResponsabileChiave(_Risposta):
    """Utente responsabile della chiave API (senza email)."""

    user_id: str
    nome: str | None = None
    cognome: str | None = None


class ChiaveApiIdentita(_Risposta):
    id: str
    responsabile: ResponsabileChiave | None = None


class Identita(_Risposta):
    """Corpo di ``GET /api/v1/me``: chi sta chiamando (Bearer, token di canale o
    chiave API). Un sistema versante ricava qui il proprio ``tenant_id``."""

    tenant_id: str | None = None
    user_id: str | None = None
    ruolo: str | None = None
    scopes: list[str] = Field(default_factory=list)
    piano: str | None = None
    auth_method: str = Field("jwt", description="Es. jwt, api_key, token_canale")
    canale: str | None = None
    ente: EnteIdentita | None = None
    chiave_api: ChiaveApiIdentita | None = None
