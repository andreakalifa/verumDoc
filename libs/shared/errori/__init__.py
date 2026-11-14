# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Catalogo errori e formato unico problem+json (RFC 9457) di VerumDoc (VD-C-100).

Uso nei servizi::

    from libs.shared.errori import ErroreApi, installa_gestori_errori

    installa_gestori_errori(app)                       # in main.py
    raise ErroreApi("VRS-404", f"PdV {pdv_id} non trovato")

Vedi README.md nella stessa cartella.
"""
from libs.shared.errori.catalogo import (
    CODICI,
    FAMIGLIE,
    VERSIONE_CATALOGO,
    CatalogoNonValido,
    Esito,
    Famiglia,
    VoceCatalogo,
    cerca,
    codice_generico,
    valida_catalogo,
    voce,
)
from libs.shared.errori.gestori import (
    MiddlewareIdRichiesta,
    RispostaProblema,
    campi_da_validazione,
    errore_da_http,
    id_richiesta_di,
    installa_gestori_errori,
    risposta_problema,
)
from libs.shared.errori.problema import (
    INTESTAZIONE_ID_RICHIESTA,
    MEDIA_TYPE_PROBLEMA,
    URI_BASE,
    CampoNonValido,
    ErroreApi,
    Problema,
    corpo_problema,
    errore_registrato,
    id_richiesta_corrente,
    nuovo_id_richiesta,
    uri_tipo,
)

#: Risposte OpenAPI riusabili: ``responses=RISPOSTE_ERRORE`` su un router.
RISPOSTE_ERRORE: dict[int | str, dict] = {
    "4XX": {"model": Problema, "description": "Errore della richiesta (catalogo errori)"},
    "5XX": {"model": Problema, "description": "Errore del servizio (catalogo errori)"},
}

__all__ = [
    "CODICI", "FAMIGLIE", "VERSIONE_CATALOGO", "CatalogoNonValido", "Esito", "Famiglia",
    "VoceCatalogo", "cerca", "codice_generico", "valida_catalogo", "voce",
    "MiddlewareIdRichiesta", "RispostaProblema", "campi_da_validazione", "errore_da_http",
    "id_richiesta_di", "installa_gestori_errori", "risposta_problema",
    "INTESTAZIONE_ID_RICHIESTA", "MEDIA_TYPE_PROBLEMA", "URI_BASE", "CampoNonValido",
    "ErroreApi", "Problema", "corpo_problema", "errore_registrato", "id_richiesta_corrente",
    "nuovo_id_richiesta", "uri_tipo", "RISPOSTE_ERRORE",
]
