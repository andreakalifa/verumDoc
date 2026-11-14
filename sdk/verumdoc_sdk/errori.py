# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Eccezioni dell'SDK VerumDoc.

Ogni errore dell'API arriva come ``application/problem+json`` (RFC 9457) con le
estensioni del catalogo errori VerumDoc (``codice``, ``esito``, ``ritentabile``,
``dettagli``, ``campi``, ``id_richiesta``). L'SDK lo trasforma in un'eccezione
tipizzata secondo l'``esito``:

- :class:`NonValido` — la richiesta o il pacchetto sono da correggere;
- :class:`Rifiutato` — una regola o una policy lo impedisce: ripetere non serve;
- :class:`ErroreServizio` — problema del servizio; :class:`ErroreTemporaneo`
  (sottoclasse) quando ``ritentabile`` è vero: l'SDK l'ha già ritentato da solo
  quanto consentito dalla politica di ritentativo.

Tutte derivano da :class:`ErroreApi`, che a sua volta deriva da
:class:`ErroreVerumDoc` (base di ogni eccezione dell'SDK).

Esempio::

    try:
        client.versa(indice, file={"fattura.pdf": "fattura.pdf"})
    except NonValido as e:
        for campo in e.campi:
            print(campo.campo, campo.messaggio)
    except Rifiutato as e:
        print(e.codice, e.titolo, e.dettagli)
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from verumdoc_sdk.modelli import CampoNonValido, Problema

ESITO_NON_VALIDO = "NON_VALIDO"
ESITO_RIFIUTATO = "RIFIUTATO"
ESITO_ERRORE = "ERRORE"


class ErroreVerumDoc(Exception):
    """Base di tutte le eccezioni dell'SDK."""


class ErroreApi(ErroreVerumDoc):
    """Risposta d'errore dell'API (problem+json del catalogo VerumDoc)."""

    def __init__(self, problema: Problema, *, metodo: str = "", url: str = "",
                 retry_after: float | None = None) -> None:
        self.problema = problema
        self.metodo = metodo
        self.url = url
        self.retry_after = retry_after
        testo = f"{problema.codice} ({problema.status}) {problema.title}"
        if problema.detail:
            testo += f": {problema.detail}"
        super().__init__(testo)

    # Accesso diretto ai campi più usati
    @property
    def codice(self) -> str:
        return self.problema.codice

    @property
    def status(self) -> int:
        return self.problema.status

    @property
    def titolo(self) -> str:
        return self.problema.title

    @property
    def dettaglio(self) -> str | None:
        return self.problema.detail

    @property
    def esito(self) -> str:
        return self.problema.esito

    @property
    def ritentabile(self) -> bool:
        return self.problema.ritentabile

    @property
    def dettagli(self) -> dict[str, Any]:
        return self.problema.dettagli

    @property
    def campi(self) -> list[CampoNonValido]:
        return self.problema.campi

    @property
    def id_richiesta(self) -> str | None:
        return self.problema.id_richiesta


class NonValido(ErroreApi):
    """Esito NON_VALIDO: correggere la richiesta o il pacchetto e reinviare."""


class Rifiutato(ErroreApi):
    """Esito RIFIUTATO: regola o policy; ripetere la stessa richiesta non serve."""


class ErroreServizio(ErroreApi):
    """Esito ERRORE: problema del servizio."""


class ErroreTemporaneo(ErroreServizio):
    """Esito ERRORE con ``ritentabile`` vero: la stessa richiesta può riuscire più tardi
    (l'SDK la ritenta da solo; questa eccezione arriva quando i tentativi sono finiti)."""


class ErroreRete(ErroreVerumDoc):
    """Il servizio non ha risposto (connessione, DNS, TLS, tempo scaduto)."""


class RispostaNonValida(ErroreVerumDoc):
    """La risposta del servizio non ha la forma attesa dall'SDK."""


class ErroreIntegrita(ErroreVerumDoc):
    """Le impronte calcolate dall'SDK e quelle del servizio non coincidono."""


class CaricamentoNonUtilizzabile(ErroreVerumDoc):
    """La sessione di caricamento è SCADUTA o ANNULLATA: aprirne una nuova."""

    def __init__(self, caricamento_id: str, stato: str) -> None:
        super().__init__(f"Caricamento {caricamento_id} nello stato {stato}: aprire una "
                         "nuova sessione (con un'altra Idempotency-Key)")
        self.caricamento_id = caricamento_id
        self.stato = stato


class TempoScaduto(ErroreVerumDoc):
    """L'attesa di uno stato finale ha superato il tempo massimo."""

    def __init__(self, messaggio: str, ultimo_stato: Any = None) -> None:
        super().__init__(messaggio)
        self.ultimo_stato = ultimo_stato


def classe_per_esito(esito: str, ritentabile: bool) -> type[ErroreApi]:
    """Classe dell'eccezione per ``esito`` e ``ritentabile`` del problema."""
    if esito == ESITO_NON_VALIDO:
        return NonValido
    if esito == ESITO_RIFIUTATO:
        return Rifiutato
    return ErroreTemporaneo if ritentabile else ErroreServizio


#: Stati HTTP senza corpo problem+json (proxy, ingress) che si possono ritentare.
_STATI_TEMPORANEI = frozenset({429, 502, 503, 504})


def problema_generico(status: int, testo: str, istanza: str | None = None) -> Problema:
    """Problema per una risposta d'errore che non è problem+json (es. pagina HTML di
    un proxy): codice ``HTTP-<status>``, esito dedotto dallo stato."""
    temporaneo = status in _STATI_TEMPORANEI
    esito = ESITO_ERRORE if status >= 500 or temporaneo else ESITO_NON_VALIDO
    return Problema(
        type=f"https://docs.verumdoc.it/errori/HTTP-{status}",
        title=testo[:200] or f"Errore HTTP {status}",
        status=status, detail=None, instance=istanza, codice=f"HTTP-{status}",
        esito=esito, ritentabile=temporaneo, dettagli={}, campi=[], id_richiesta=None,
        messaggio=testo[:200] or None,
    )


def errore_da_problema(problema: Problema, *, metodo: str = "", url: str = "",
                       retry_after: float | None = None) -> ErroreApi:
    """Eccezione tipizzata per un problema già letto."""
    classe = classe_per_esito(problema.esito, problema.ritentabile)
    return classe(problema, metodo=metodo, url=url, retry_after=retry_after)


def problema_da_corpo(status: int, corpo: Any, testo: str = "",
                      istanza: str | None = None) -> Problema:
    """Legge un corpo d'errore: problem+json VerumDoc se ha ``codice`` ed ``esito``,
    altrimenti un problema generico ``HTTP-<status>``."""
    if isinstance(corpo, Mapping) and corpo.get("codice") and corpo.get("esito"):
        try:
            dati = dict(corpo)
            dati.setdefault("status", status)
            dati.setdefault("title", str(corpo.get("messaggio") or corpo.get("codice")))
            dati.setdefault("type", f"https://docs.verumdoc.it/errori/{corpo['codice']}")
            dati.setdefault("ritentabile", False)
            return Problema.model_validate(dati)
        except ValueError:
            pass
    if isinstance(corpo, Mapping):
        titolo = corpo.get("title") or corpo.get("messaggio") or corpo.get("detail")
        if isinstance(titolo, str):
            testo = titolo
    return problema_generico(status, testo, istanza)
