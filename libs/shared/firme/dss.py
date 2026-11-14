# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/dss.py — client del servizio interno ``verifica-firme``
(EU DSS, Java; R08 D01).

Il servizio fa la verifica con la libreria della Commissione europea (Trusted
List UE caricata dalla LOTL e aggiornata periodicamente, OCSP/CRL, CAdES,
PAdES, XAdES, ASiC) e risponde GIÀ nella forma normalizzata
(``EsitoVerifica.come_dict``): qui si invia il file e si rilegge l'esito.

Contratto (``services/verifica-firme/README.md``):
  POST {base}/api/v1/verifica   Authorization: Bearer <token di servizio>
  {"contenuto": base64, "originale": base64|null, "nome_file": str, "livello": str}
  → 200 esito normalizzato | 400 richiesta non valida | 401/403 token

Revisione R08:
  - X7: solo gli errori di CONNESSIONE (rete, tempo scaduto) sono
    ``ErroreConnessioneVerificatore`` (il solo caso in cui scatta il ripiego);
    401/403/5xx (es. 503 «Trusted List non ancora caricata») e risposte non
    interpretabili sono ``ErroreVerificatoreNonDisponibile`` (si ritenta, senza
    ripiego); 413 e i file oltre ``dimensione_max_byte`` (allineata a
    ``VERIFICA_DIMENSIONE_MASSIMA`` del servizio) sono un esito esplicito
    «firme non verificate» (``limite_superato``), mai inviati;
  - X6: se il servizio risponde «non firmato» (o nessuna firma) ma la
    rilevazione locale riconosce una busta, l'esito è busta illeggibile
    (TOTAL_FAILED / FORMAT_FAILURE): alzare il livello non rende mai il
    controllo meno severo;
  - X1: a CRITTOGRAFICA la revoca non è richiesta (come pyHanko in
    «soft-fail»): una INDETERMINATE di EU DSS dovuta SOLO alla revoca non
    disponibile (TRY_LATER, REVOCATION_OUT_OF_BOUNDS_NO_POE) con firma integra,
    catena attendibile e certificato non revocato diventa TOTAL_PASSED con
    revoca SCONOSCIUTO e una nota. Ogni altra INDETERMINATE resta tale e la
    regola RGL-109 la rifiuta.
"""
from __future__ import annotations

import base64
from dataclasses import replace
from typing import Any

import httpx

from libs.shared.firme.esito import (
    EsitoVerifica,
    FirmaVerificata,
    FormatoFirma,
    Indicazione,
    LivelloVerifica,
    StatoRevoca,
    esito_illeggibile,
    esito_oltre_limite,
)
from libs.shared.firme.interfaccia import (
    ErroreConnessioneVerificatore,
    ErroreVerificatoreNonDisponibile,
)
from libs.shared.firme.rilevazione import rileva_formato_firma

PERCORSO = "/api/v1/verifica"
#: Predefinito allineato a VERIFICA_DIMENSIONE_MASSIMA del servizio (60 MiB).
DIMENSIONE_MAX_BYTE = 60 * 1024 * 1024
#: Sotto-indicazioni EU DSS dovute alla sola revoca non disponibile.
SOLO_REVOCA = frozenset({"TRY_LATER", "REVOCATION_OUT_OF_BOUNDS_NO_POE"})


def adatta_al_livello(esito: EsitoVerifica) -> EsitoVerifica:
    """X1: a CRITTOGRAFICA la sola revoca non disponibile non rende la firma
    indeterminata (il livello non la richiede); ogni altra INDETERMINATE resta."""
    if esito.livello is not LivelloVerifica.CRITTOGRAFICA:
        return esito
    firme: list[FirmaVerificata] = []
    adattate = 0
    for f in esito.firme:
        if (f.indicazione is Indicazione.INDETERMINATE and f.sottoindicazione in SOLO_REVOCA
                and f.crittografia_valida is True and f.catena_attendibile is True
                and f.revoca is not StatoRevoca.REVOCATO):
            f = replace(f, indicazione=Indicazione.TOTAL_PASSED, revoca=StatoRevoca.SCONOSCIUTO,
                        motivi=(*f.motivi, "stato di revoca non richiesto al livello "
                                           "CRITTOGRAFICA"))
            adattate += 1
        firme.append(f)
    if not adattate:
        return esito
    return replace(esito, firme=tuple(firme), note=(
        *esito.note, f"{adattate} firma/e con la sola revoca non disponibile: valide al "
                     "livello CRITTOGRAFICA (revoca SCONOSCIUTO)"))


class VerificatoreDss:
    nome = "eu-dss (servizio verifica-firme)"
    formati = frozenset({FormatoFirma.CADES, FormatoFirma.PADES, FormatoFirma.XADES,
                         FormatoFirma.ASIC})

    def __init__(self, url_base: str, token: str, *, timeout_s: float = 60.0,
                 dimensione_max_byte: int = DIMENSIONE_MAX_BYTE,
                 trasporto: httpx.AsyncBaseTransport | None = None) -> None:
        if not url_base:
            raise ValueError("FIRME_DSS_URL non configurato")
        self.url = url_base.rstrip("/") + PERCORSO
        self.token = token
        self.timeout_s = timeout_s
        self.dimensione_max_byte = dimensione_max_byte
        self._trasporto = trasporto

    def _oltre_limite(self, contenuto: bytes, nome_file: str, livello: LivelloVerifica,
                      verificato_il: str | None) -> EsitoVerifica:
        mb = self.dimensione_max_byte // (1024 * 1024)
        return esito_oltre_limite(rileva_formato_firma(contenuto, nome_file), livello, self.nome,
                                  f"file oltre {mb} MB, limite del servizio di verifica",
                                  verificato_il)

    async def verifica(self, contenuto: bytes, *, livello: LivelloVerifica,
                       nome_file: str = "", originale: bytes | None = None,
                       verificato_il: str | None = None) -> EsitoVerifica:
        if len(contenuto) + len(originale or b"") > self.dimensione_max_byte:
            return self._oltre_limite(contenuto, nome_file, livello, verificato_il)
        corpo: dict[str, Any] = {
            "contenuto": base64.b64encode(contenuto).decode("ascii"),
            "originale": base64.b64encode(originale).decode("ascii") if originale else None,
            "nome_file": nome_file, "livello": livello.value,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s,
                                         transport=self._trasporto) as client:
                risposta = await client.post(self.url, json=corpo, headers={
                    "Authorization": f"Bearer {self.token}"})
        except httpx.TransportError as exc:
            # rete, connessione, tempo scaduto: il solo caso del ripiego (X7)
            raise ErroreConnessioneVerificatore(
                f"verifica-firme non raggiungibile: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise ErroreVerificatoreNonDisponibile(
                f"verifica-firme: errore del client {type(exc).__name__}") from exc
        if risposta.status_code == 413:
            return self._oltre_limite(contenuto, nome_file, livello, verificato_il)
        if risposta.status_code != 200:
            raise ErroreVerificatoreNonDisponibile(
                f"verifica-firme ha risposto {risposta.status_code}")
        try:
            esito = EsitoVerifica.da_dict(risposta.json())
        except (ValueError, KeyError, TypeError) as exc:
            raise ErroreVerificatoreNonDisponibile(
                "risposta di verifica-firme non interpretabile") from exc
        if verificato_il and not esito.verificato_il:
            esito = replace(esito, verificato_il=verificato_il)
        if not esito.firme:
            # X6: «non firmato» o busta vuota per DSS, ma busta riconosciuta qui
            locale = rileva_formato_firma(contenuto, nome_file)
            if locale is not FormatoFirma.NESSUNO:
                return replace(esito_illeggibile(
                    locale, livello, self.nome, "busta riconosciuta ma non interpretata dal "
                    "servizio di verifica", esito.verificato_il),
                    rapporto_grezzo=esito.rapporto_grezzo)
        return adatta_al_livello(esito)


__all__ = ["DIMENSIONE_MAX_BYTE", "PERCORSO", "SOLO_REVOCA", "VerificatoreDss", "adatta_al_livello"]
