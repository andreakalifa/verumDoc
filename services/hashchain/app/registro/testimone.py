# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Client del testimone indipendente (contratto K3 di R04, ispirato a C2SP tlog-witness).

Servizio: hashchain. Riferimenti: piano §5.3; REGOLE_SVILUPPO §20.1 (un sistema
esterno, un solo client), §20.4 (nessun segreto nei log), §20.5.

Richiesta: ``POST {TESTIMONE_URL}/api/v1/testimone/checkpoint`` con token di
servizio, corpo ``text/plain``::

    old <dimensione già controfirmata dal testimone>
    <prova di consistenza in base64, una riga per hash>

    <checkpoint firmato dal registro (nota K2)>

Risposte: 200 → righe di firma del testimone; 409 → il testimone conosce
un'altra dimensione (nel corpo); 422 → prova non valida; 403 → firma del
registro non valida; 423 → testimone BLOCCATO (ha visto due checkpoint con la
stessa dimensione e radici diverse: biforcazione del registro). 403, 422 e 423
sono rifiuti NON ritentabili e un allarme (rev4 rilievo 1). Qualsiasi altra
risposta o errore di rete = «da ritentare».
La controfirma restituita si verifica con la verifier key del testimone prima
di registrarla: una controfirma non verificabile non vale.
"""
from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
import structlog

from libs.catena import nota

logger = structlog.get_logger(__name__)

PERCORSO = "/api/v1/testimone/checkpoint"

ESITO_CONTROFIRMATO = "CONTROFIRMATO"
ESITO_DIMENSIONE_DIVERSA = "DIMENSIONE_DIVERSA"
ESITO_RIFIUTATO = "RIFIUTATO"
ESITO_DA_RITENTARE = "DA_RITENTARE"

#: Cause dei rifiuti senza codice HTTP (N3: rifiuti con la stessa causa = un allarme)
CAUSA_CONTROFIRMA = "CONTROFIRMA_NON_VERIFICABILE"
CAUSA_DIMENSIONE = "DIMENSIONE_TESTIMONE_MAGGIORE"

#: Risposte del testimone che sono un RIFIUTO definitivo (mai ritentate da sole).
MOTIVI_RIFIUTO = {
    403: "firma del registro non accettata",
    422: "prova di consistenza non accettata",
    423: "testimone bloccato: biforcazione del registro rilevata",
}


@dataclass(frozen=True)
class EsitoTestimone:
    """Esito di un invio al testimone."""

    esito: str
    controfirma: str | None = None
    dimensione_testimone: int | None = None
    motivo: str = ""
    #: Causa normalizzata di un rifiuto (raggruppa i rifiuti in un allarme, N3)
    causa: str = ""


def corpo_richiesta(vecchia: int, prova: Sequence[bytes], nota_firmata: str) -> str:
    """Corpo K3: ``old N``, prova in base64 una per riga, riga vuota, nota."""
    righe = [f"old {vecchia}"] + [base64.b64encode(h).decode("ascii") for h in prova]
    return "\n".join(righe) + "\n\n" + nota_firmata


def _controfirma_valida(nota_firmata: str, risposta: str,
                        verificatore: nota.Verificatore) -> str | None:
    """Prima riga di firma della risposta che verifica con la chiave del testimone."""
    for riga in risposta.splitlines():
        if not riga.startswith(nota.TRATTINO):
            continue
        try:
            completa = nota.aggiungi_firme(nota_firmata, [riga])
        except nota.ErroreNota:
            continue
        if nota.verifica(completa, [verificatore]):
            return str(nota.analizza_riga_firma(riga).riga)
    return None


class ClientTestimone:
    """Client HTTP del testimone (uno per processo)."""

    def __init__(self, base_url: str, token: str, verificatore: nota.Verificatore,
                 timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._verificatore = verificatore
        self._timeout = timeout
        self._transport = transport

    async def invia(self, vecchia: int, prova: Sequence[bytes],
                    nota_firmata: str) -> EsitoTestimone:
        corpo = corpo_richiesta(vecchia, prova, nota_firmata)
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout,
                                         transport=self._transport) as client:
                risposta = await client.post(
                    PERCORSO, content=corpo.encode("utf-8"),
                    headers={"Authorization": f"Bearer {self._token}",
                             "Content-Type": "text/plain; charset=utf-8"},
                )
        except httpx.HTTPError as exc:
            return EsitoTestimone(ESITO_DA_RITENTARE,
                                  motivo=f"testimone non raggiungibile: {type(exc).__name__}")
        testo = risposta.text
        if risposta.status_code == 200:
            controfirma = _controfirma_valida(nota_firmata, testo, self._verificatore)
            if controfirma is None:
                logger.error("testimone_controfirma_non_verificabile")
                return EsitoTestimone(ESITO_RIFIUTATO,
                                      motivo="controfirma assente o non verificabile",
                                      causa=CAUSA_CONTROFIRMA)
            return EsitoTestimone(ESITO_CONTROFIRMATO, controfirma=controfirma)
        if risposta.status_code == 409:
            try:
                dimensione = int(testo.strip().split()[0])
            except (ValueError, IndexError):
                return EsitoTestimone(ESITO_DA_RITENTARE,
                                      motivo="409 senza dimensione leggibile")
            return EsitoTestimone(ESITO_DIMENSIONE_DIVERSA, dimensione_testimone=dimensione,
                                  motivo=f"il testimone conosce la dimensione {dimensione}")
        if risposta.status_code in MOTIVI_RIFIUTO:
            return EsitoTestimone(
                ESITO_RIFIUTATO,
                motivo=f"{risposta.status_code}: {MOTIVI_RIFIUTO[risposta.status_code]}",
                causa=f"HTTP_{risposta.status_code}")
        return EsitoTestimone(ESITO_DA_RITENTARE,
                              motivo=f"risposta inattesa del testimone: {risposta.status_code}")

    async def dimensione_controfirmata(self, origine: str) -> int | None:
        """Ultima dimensione che il testimone ha controfirmato per ``origine``
        (``GET`` dell'ultimo checkpoint controfirmato): 0 se non ne ha (404), None
        se non risponde o la risposta non è leggibile (R04 terzo giro, T4)."""
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout,
                                         transport=self._transport) as client:
                risposta = await client.get(PERCORSO, params={"origine": origine})
        except httpx.HTTPError:
            return None
        if risposta.status_code == 404:
            return 0
        if risposta.status_code != 200:
            return None
        try:
            corpo, _ = nota.separa(risposta.text)
            return int(nota.analizza(corpo).dimensione)
        except (nota.ErroreNota, ValueError):
            return None

    async def sonda(self) -> bool:
        """Lettura di prova (``GET`` dell'ultimo checkpoint controfirmato): True se il
        testimone risponde (200, o 404 se non ha ancora controfirmato nulla)."""
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout,
                                         transport=self._transport) as client:
                risposta = await client.get(PERCORSO)
        except httpx.HTTPError:
            return False
        return risposta.status_code in (200, 404)


def client_predefinito() -> ClientTestimone | None:
    """Client dalle impostazioni; None se il testimone non è configurato."""
    from app.config import settings
    from app.registro.chiavi import verificatore_testimone

    verificatore = verificatore_testimone(settings)
    if not settings.testimone_url or verificatore is None:
        return None
    return ClientTestimone(settings.testimone_url,
                           settings.testimone_service_token.get_secret_value(),
                           verificatore, settings.testimone_timeout_seconds)
