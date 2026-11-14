# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/interfaccia.py — interfaccia unica dei verificatori di firma
intercambiabili (R08, decisione D01).

Un verificatore riceve i byte di un file (e, per le firme staccate, il
contenuto originale) e restituisce un ``EsitoVerifica`` normalizzato. Realizzazioni:

  - ``VerificatoreDss`` (``dss.py``): client del servizio interno
    ``verifica-firme`` (EU DSS: Trusted List UE, OCSP/CRL, CAdES, PAdES, XAdES,
    ASiC). Predefinito in esercizio;
  - ``VerificatorePyHanko`` (``pyhanko_verificatore.py``): CAdES e PAdES con
    trust anchor configurate e revoche fornite o, se ammesso, scaricate;
  - ``VerificatoreConRipiego`` (``ripiego.py``): il primo, e se non è
    raggiungibile il secondo per i soli formati indicati (PDF);
  - ``RilevatoreFirme`` (``rilevazione.py``): solo livello RILEVATA.

``verifica_al_livello`` è il solo punto in cui il livello richiesto decide
COSA si fa: NESSUNA → nulla; RILEVATA → rilevazione; CRITTOGRAFICA e
CON_REVOCHE → verificatore configurato.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol, TypeVar

from libs.shared.firme.esito import (
    EsitoVerifica,
    FormatoFirma,
    LivelloVerifica,
    esito_oltre_limite,
)
from libs.shared.firme.rilevazione import rileva, rileva_formato_firma

T = TypeVar("T")


class ErroreVerificatoreNonDisponibile(Exception):
    """Il verificatore non ha risposto (rete, servizio giù, configurazione): è un
    errore TECNICO, non un esito. Il chiamante ritenta; non vale mai come
    «firma valida» né come «firma non valida»."""


class ErroreConnessioneVerificatore(ErroreVerificatoreNonDisponibile):
    """Revisione X7: il verificatore non è RAGGIUNGIBILE (connessione rifiutata o
    interrotta, tempo scaduto). È il solo errore per cui scatta il ripiego; le
    risposte del servizio (401/403/5xx, Trusted List non caricata) restano
    ``ErroreVerificatoreNonDisponibile``: si ritenta, senza ripiego."""


#: Revisione X8: tempo massimo di una verifica o rilevazione eseguita in un
#: thread (secondi); oltre, l'esito è «firme non verificate» (limite superato).
TEMPO_MAX_S = 120.0


async def in_thread(funzione: Callable[[], T], *,  # noqa: UP047 — TypeVar del modulo
                    tempo_max_s: float | None = None) -> T:
    """Esegue una funzione sincrona (parsing PDF/CMS) in un thread, così il loop
    asyncio del consumatore resta libero (heartbeat Kafka). ``TimeoutError``
    oltre ``tempo_max_s``: il thread non si interrompe ma il chiamante prosegue."""
    return await asyncio.wait_for(asyncio.to_thread(funzione),
                                  timeout=tempo_max_s or TEMPO_MAX_S)


class Verificatore(Protocol):
    nome: str
    formati: frozenset[FormatoFirma]

    async def verifica(self, contenuto: bytes, *, livello: LivelloVerifica,
                       nome_file: str = "", originale: bytes | None = None,
                       verificato_il: str | None = None) -> EsitoVerifica: ...


async def verifica_al_livello(verificatore: Verificatore, contenuto: bytes,
                              livello: LivelloVerifica, *, nome_file: str = "",
                              originale: bytes | None = None,
                              verificato_il: str | None = None) -> EsitoVerifica | None:
    """Esito al livello richiesto; None per NESSUNA (nessun controllo)."""
    if livello is LivelloVerifica.NESSUNA:
        return None
    if livello is LivelloVerifica.RILEVATA:
        try:
            return await in_thread(lambda: rileva(contenuto, nome_file,
                                                  verificato_il=verificato_il))
        except TimeoutError:
            return esito_oltre_limite(rileva_formato_firma(contenuto, nome_file), livello,
                                      "rilevazione", f"tempo massimo della rilevazione "
                                      f"superato ({TEMPO_MAX_S:.0f} s)", verificato_il)
    return await verificatore.verifica(contenuto, livello=livello, nome_file=nome_file,
                                       originale=originale, verificato_il=verificato_il)


__all__ = ["TEMPO_MAX_S", "ErroreConnessioneVerificatore", "ErroreVerificatoreNonDisponibile",
           "Verificatore", "in_thread", "verifica_al_livello"]
