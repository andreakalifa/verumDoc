# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VerumDoc SDK Python 3 — client ufficiale delle API di conservazione VerumDoc.

Uso rapido::

    from verumdoc_sdk import VerumDoc, ChiaveApi

    with VerumDoc(credenziali=ChiaveApi("vd_<identificativo>_<segreto>")) as vd:
        esito = vd.versa(indice, file={"fattura.pdf": "/dati/fattura.pdf"})
        stato = vd.attendi_versamento(esito.pdv_id)

Vedi README.md. La versione 3 non è compatibile con la 2 (nomi, modelli ed
eccezioni allineati all'API R03).
"""
from verumdoc_sdk._comune import (
    VERSIONE,
    ChiaveApi,
    Credenziali,
    PoliticaAttesa,
    PoliticaRitentativi,
    TokenBearer,
    nuova_chiave_idempotenza,
)
from verumdoc_sdk._operazioni import CaricamentoCompletato, EsitoVersamento
from verumdoc_sdk.client import VerumDoc
from verumdoc_sdk.client_asincrono import VerumDocAsincrono
from verumdoc_sdk.errori import (
    CaricamentoNonUtilizzabile,
    ErroreApi,
    ErroreIntegrita,
    ErroreRete,
    ErroreServizio,
    ErroreTemporaneo,
    ErroreVerumDoc,
    NonValido,
    Rifiutato,
    RispostaNonValida,
    TempoScaduto,
)
from verumdoc_sdk.modelli import (
    ChiaveUD,
    ConsegnaPdD,
    DocumentoIndice,
    Evento,
    ImprontaFile,
    IndiceUD,
    MetadatiDocumento,
    Problema,
    StatoPdV,
    TipoPdD,
)
from verumdoc_sdk.sorgenti import Sorgente
from verumdoc_sdk.webhook import leggi_evento, verifica_firma

__version__ = VERSIONE

__all__ = [
    "VerumDoc", "VerumDocAsincrono", "ChiaveApi", "TokenBearer", "Credenziali",
    "PoliticaRitentativi", "PoliticaAttesa", "nuova_chiave_idempotenza",
    "EsitoVersamento", "CaricamentoCompletato", "Sorgente",
    "IndiceUD", "DocumentoIndice", "MetadatiDocumento", "ChiaveUD", "ImprontaFile",
    "Evento", "Problema", "StatoPdV", "TipoPdD", "ConsegnaPdD",
    "ErroreVerumDoc", "ErroreApi", "NonValido", "Rifiutato", "ErroreServizio",
    "ErroreTemporaneo", "ErroreRete", "RispostaNonValida", "ErroreIntegrita",
    "TempoScaduto", "CaricamentoNonUtilizzabile",
    "verifica_firma", "leggi_evento", "__version__",
]
