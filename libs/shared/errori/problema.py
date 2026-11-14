# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Errore verso il client nel formato application/problem+json (RFC 9457).

Un solo formato per tutti i servizi (VD-C-100)::

    {
      "type": "https://docs.verumdoc.it/errori/VRS-102",
      "title": "Impronta del file errata",
      "status": 400,
      "detail": "Atteso: ab12… | Calcolato: cd34…",
      "instance": "/api/v1/versamento/ud",
      "codice": "VRS-102",
      "esito": "NON_VALIDO",
      "ritentabile": false,
      "dettagli": {},
      "campi": [],
      "id_richiesta": "5f0c…",
      "messaggio": "Impronta del file errata"
    }

``messaggio`` duplica ``title`` per compatibilità con i client del vecchio
formato ``{codice, messaggio}``. Questo modulo non importa FastAPI.
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from pydantic import BaseModel, Field

from libs.shared.errori.catalogo import Esito, VoceCatalogo, voce

logger = structlog.get_logger(__name__)

URI_BASE = "https://docs.verumdoc.it/errori/"
MEDIA_TYPE_PROBLEMA = "application/problem+json"
INTESTAZIONE_ID_RICHIESTA = "X-Request-ID"

_FORMA_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_id_richiesta: ContextVar[str | None] = ContextVar("verumdoc_id_richiesta", default=None)


def nuovo_id_richiesta() -> str:
    return uuid.uuid4().hex


def normalizza_id_richiesta(valore: object) -> str | None:
    """L'id arrivato dal client si accetta solo se breve e senza caratteri strani
    (finisce nei log: niente a capo o testo arbitrario)."""
    if isinstance(valore, str) and _FORMA_ID.fullmatch(valore):
        return valore
    return None


def id_richiesta_corrente() -> str | None:
    """Id della richiesta in corso (impostato dal middleware dei gestori)."""
    return _id_richiesta.get()


def imposta_id_richiesta(valore: str | None) -> Any:
    """Imposta l'id corrente; restituisce il token per ripristinarlo."""
    return _id_richiesta.set(valore)


def ripristina_id_richiesta(token: Any) -> None:
    _id_richiesta.reset(token)


def uri_tipo(codice: str) -> str:
    return f"{URI_BASE}{codice}"


class ErroreApi(Exception):
    """
    Errore verso il client con codice del catalogo.

    Titolo, stato e Retry-After vengono dal catalogo e si possono
    sovrascrivere; ``dettaglio`` spiega il caso specifico; ``dettagli`` porta
    dati strutturati (es. ``{"ammessi": [...]}``); ``campi`` elenca gli errori
    per campo (validazione). Un codice assente dal catalogo solleva KeyError
    già alla costruzione.
    """

    def __init__(
        self,
        codice: str,
        dettaglio: str | None = None,
        *,
        titolo: str | None = None,
        status: int | None = None,
        dettagli: dict[str, Any] | None = None,
        campi: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        retry_after: int | None = None,
        id_richiesta: str | None = None,
    ) -> None:
        self.voce: VoceCatalogo = voce(codice)
        self.codice = codice
        self.titolo = titolo or self.voce.titolo
        self.status = int(status or self.voce.status)
        self.dettaglio = dettaglio
        self.dettagli = dict(dettagli or {})
        self.campi = list(campi or [])
        self.headers = dict(headers or {})
        self.retry_after = retry_after
        self.id_richiesta = id_richiesta
        super().__init__(f"{codice}: {self.titolo}")

    # Proprietà dal catalogo
    @property
    def esito(self) -> Esito:
        return self.voce.esito

    @property
    def ritentabile(self) -> bool:
        return self.voce.ritentabile

    def secondi_retry_after(self) -> int | None:
        return self.retry_after or self.voce.retry_after

    def intestazioni(self, id_richiesta: str | None = None) -> dict[str, str]:
        """Intestazioni della risposta: quelle dell'errore, Retry-After dal catalogo
        se non già presente, X-Request-ID."""
        h = dict(self.headers)
        presenti = {k.lower() for k in h}
        secondi = self.secondi_retry_after()
        if secondi and "retry-after" not in presenti:
            h["Retry-After"] = str(secondi)
        idr = id_richiesta or self.id_richiesta
        if idr and INTESTAZIONE_ID_RICHIESTA.lower() not in presenti:
            h[INTESTAZIONE_ID_RICHIESTA] = idr
        return h

    def corpo(self, *, instance: str | None = None,
              id_richiesta: str | None = None) -> dict[str, Any]:
        """Corpo problem+json (RFC 9457) con le estensioni VerumDoc."""
        idr = (id_richiesta or self.id_richiesta or id_richiesta_corrente()
               or nuovo_id_richiesta())
        self.id_richiesta = idr
        return corpo_problema(
            self.voce, status=self.status, titolo=self.titolo, dettaglio=self.dettaglio,
            dettagli=self.dettagli, campi=self.campi, instance=instance, id_richiesta=idr,
        )


def corpo_problema(
    v: VoceCatalogo,
    *,
    status: int | None = None,
    titolo: str | None = None,
    dettaglio: str | None = None,
    dettagli: dict[str, Any] | None = None,
    campi: list[dict[str, Any]] | None = None,
    instance: str | None = None,
    id_richiesta: str | None = None,
) -> dict[str, Any]:
    titolo = titolo or v.titolo
    corpo: dict[str, Any] = {
        "type": uri_tipo(v.codice),
        "title": titolo,
        "status": int(status or v.status),
    }
    if dettaglio:
        corpo["detail"] = str(dettaglio)
    if instance:
        corpo["instance"] = instance
    corpo.update({
        "codice": v.codice,
        "esito": v.esito.value,
        "ritentabile": v.ritentabile,
        "dettagli": dict(dettagli or {}),
        "campi": list(campi or []),
        "id_richiesta": id_richiesta or id_richiesta_corrente() or nuovo_id_richiesta(),
        "messaggio": titolo,
    })
    return corpo


def errore_registrato(
    codice: str,
    exc: BaseException | None = None,
    *,
    dettaglio: str | None = None,
    titolo: str | None = None,
    status: int | None = None,
    evento: str = "errore_servizio",
    **contesto: Any,
) -> ErroreApi:
    """
    ErroreApi per un guasto interno (database, storage, servizio a valle):
    l'eccezione va SOLO nel log, con l'id della richiesta; il client riceve
    codice, titolo e lo stesso id per la segnalazione (REGOLE §18).
    """
    idr = id_richiesta_corrente() or nuovo_id_richiesta()
    errore = ErroreApi(codice, dettaglio, titolo=titolo, status=status, id_richiesta=idr)
    logger.error(evento, codice=codice, status=errore.status, id_richiesta=idr,
                 errore=repr(exc)[:500] if exc is not None else None, **contesto)
    return errore


# ── Modello per la documentazione OpenAPI ────────────────────────────

class CampoNonValido(BaseModel):
    campo: str = Field(..., description="Percorso del campo, es. `indice.documenti.0.nome_file`")
    posizione: str | None = Field(None, description="body, query, path, header, cookie")
    tipo: str = Field(..., description="Tipo stabile dell'errore di validazione, es. `missing`")
    messaggio: str = Field(..., description="Messaggio in italiano")


class Problema(BaseModel):
    """Errore nel formato application/problem+json (RFC 9457) con estensioni VerumDoc."""

    type: str = Field(..., description="URI della pagina del codice nel catalogo errori")
    title: str = Field(..., description="Titolo breve")
    status: int = Field(..., description="Stato HTTP")
    detail: str | None = Field(None, description="Spiegazione del caso specifico")
    instance: str | None = Field(None, description="Percorso della richiesta")
    codice: str = Field(..., description="Codice stabile del catalogo, es. VRS-102")
    esito: Esito = Field(..., description="NON_VALIDO, RIFIUTATO o ERRORE")
    ritentabile: bool = Field(..., description="Se la stessa richiesta, ripetuta, può riuscire")
    dettagli: dict[str, Any] = Field(default_factory=dict, description="Dati strutturati")
    campi: list[CampoNonValido] = Field(default_factory=list, description="Errori per campo")
    id_richiesta: str = Field(..., description="Identificativo da citare nella segnalazione")
    messaggio: str = Field(..., description="Uguale a `title` (compatibilità)")
