# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lettura limitata del corpo delle richieste e decodifica base64 a blocchi.

Servizio: gateway. Riferimenti: piano R03 §4.2 (nessun file intero in memoria
oltre il necessario), incarico R03 b1 (PDND: base64 con limite da impostazione e
decodifica in streaming verso ``FonteFlusso``). R18.2: modulo spostato da
``app/compat/`` (cartella tolta con gli adattatori di compatibilità).

Quando il file viaggia in base64 DENTRO un JSON (e-service PDND):

  - ``leggi_json_con_file`` (R03 f1, REV-05) legge il corpo IN
    STREAMING: il valore del campo del file non entra mai in memoria per intero,
    né come testo base64 né decodificato. Si valida e si decodifica a blocchi
    mentre arriva e i byte decodificati vanno in un file temporaneo su disco
    (``FileDecodificato``), da cui il caso d'uso li legge a blocchi verso lo
    staging. Il resto del JSON (metadati) si tiene con un limite proprio e si
    interpreta con il modello, con il campo del file sostituito da "";
  - ``leggi_corpo_limitato`` legge un corpo piccolo per intero con un limite
    (oltre il quale ci si ferma subito).

``LimiteConcorrenza`` limita le richieste base64 contemporanee per replica
(impostazione, errore ritentabile del catalogo oltre il limite).

R03 f3 (REV-N2): il disco temporaneo della replica (``/tmp``, emptyDir con
``sizeLimit``: oltre, kubelet sfratta il pod) ha un tetto prevedibile:
``BUDGET_DISCO_TEMPORANEO`` è un budget UNICO di byte per replica condiviso dal
multipart REST (Starlette scrive su ``/tmp`` le parti oltre 1 MB) e dal
file decodificato PDND (``FileDecodificato``). Ogni richiesta prenota PRIMA di
leggere il corpo il massimo che può scrivere (Content-Length se dichiarata e
sotto il limite, altrimenti il limite del canale) e lo rilascia quando i file
temporanei sono chiusi; oltre il budget risponde subito con l'errore ritentabile
del canale. ``posto_multipart`` unisce il tetto di multipart contemporanei
(``VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX``) e la prenotazione.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import IO, Any

from fastapi import Request

from libs.shared.errori import ErroreApi

#: Caratteri base64 decodificati per blocco (multiplo di 4): ~768 KB per blocco.
BLOCCO_BASE64 = 1024 * 1024


async def leggi_corpo_limitato(request: Request, limite: int, *, codice: str) -> bytes:
    """Il corpo della richiesta, al più ``limite`` byte; oltre → ``codice`` (413)."""
    annunciata = request.headers.get("content-length")
    if annunciata and annunciata.isdigit() and int(annunciata) > limite:
        raise ErroreApi(codice, f"Il corpo supera i {limite} byte ammessi",
                        dettagli={"limite_byte": limite})
    corpo = bytearray()
    async for blocco in request.stream():
        corpo.extend(blocco)
        if len(corpo) > limite:
            raise ErroreApi(codice, f"Il corpo supera i {limite} byte ammessi",
                            dettagli={"limite_byte": limite})
    # R03 f1 (REV-05): nessuna copia ``bytes(corpo)``; i modelli Pydantic leggono
    # direttamente il bytearray
    return corpo  # type: ignore[return-value]


# ── JSON con un file base64 letto in streaming (R03 f1, REV-05) ─────

_ALFABETO_BASE64 = re.compile(rb"[A-Za-z0-9+/]*")
_STRUTTURALE = re.compile(rb'["{}\[\]:,]')
_FINE_STRINGA = re.compile(rb'["\\\\]')
_SPAZI_JSON = b" \t\r\n"


class FileDecodificato:
    """Byte decodificati del campo base64, in un file temporaneo su disco (mai in memoria).

    Si legge a blocchi con ``blocchi()``; ``chiudi()`` elimina il file (anche il
    sistema operativo lo elimina alla chiusura: ``TemporaryFile``)."""

    def __init__(self) -> None:
        self._file: IO[bytes] = tempfile.TemporaryFile(prefix="vd-b64-")
        self.dimensione = 0

    async def scrivi(self, dati: bytes) -> None:
        if dati:
            await asyncio.to_thread(self._file.write, dati)
            self.dimensione += len(dati)

    async def blocchi(self, blocco: int = BLOCCO_BASE64) -> AsyncIterator[bytes]:
        await asyncio.to_thread(self._file.seek, 0)
        while True:
            dati = await asyncio.to_thread(self._file.read, blocco)
            if not dati:
                return
            yield dati

    def chiudi(self) -> None:
        self._file.close()


@dataclass
class _Base64InFlusso:
    """Validazione e decodifica a blocchi di un base64 standard che arriva a pezzi."""

    destinazione: FileDecodificato
    limite_byte: int
    codice_base64: str
    codice_limite: str
    coda: bytearray = field(default_factory=bytearray)
    caratteri: int = 0
    riempimento: int = 0

    def _non_valido(self) -> ErroreApi:
        return ErroreApi(self.codice_base64, "Base64 standard atteso: alfabeto A-Z a-z 0-9 + /, "
                                             "lunghezza multipla di 4, «=» solo in fondo, "
                                             "senza a capo")

    async def aggiungi(self, testo: bytes) -> None:
        if not testo:
            return
        self.caratteri += len(testo)
        if self.riempimento:
            # dopo il primo «=» sono ammessi solo altri «=» (al più due in tutto)
            if testo.strip(b"=") or self.riempimento + len(testo) > 2:
                raise self._non_valido()
            self.riempimento += len(testo)
            self.coda.extend(testo)
            return
        uguale = testo.find(b"=")
        corpo = testo if uguale < 0 else testo[:uguale]
        if not _ALFABETO_BASE64.fullmatch(corpo):
            raise self._non_valido()
        if uguale >= 0:
            pad = testo[uguale:]
            if pad.strip(b"=") or len(pad) > 2:
                raise self._non_valido()
            self.riempimento = len(pad)
        self.coda.extend(testo)
        if not self.riempimento and len(self.coda) >= BLOCCO_BASE64:
            utile = len(self.coda) - len(self.coda) % 4
            await self._decodifica(bytes(self.coda[:utile]))
            del self.coda[:utile]

    async def _decodifica(self, quartetti: bytes) -> None:
        dati = base64.b64decode(quartetti, validate=True)
        if self.destinazione.dimensione + len(dati) > self.limite_byte:
            raise ErroreApi(self.codice_limite,
                            f"Il file supera i {self.limite_byte} byte ammessi",
                            dettagli={"limite_byte": self.limite_byte})
        await self.destinazione.scrivi(dati)

    async def chiudi(self) -> None:
        if self.caratteri % 4:
            raise self._non_valido()
        if self.coda:
            await self._decodifica(bytes(self.coda))
            self.coda.clear()


@dataclass
class JsonConFile:
    """Corpo JSON interpretato in streaming: il testo senza il file e il file decodificato."""

    #: JSON dell'oggetto con il campo del file sostituito da "" (stringa vuota)
    json: bytes
    #: byte decodificati del campo del file (None se il campo non c'era)
    file: FileDecodificato | None

    def chiudi(self) -> None:
        if self.file is not None:
            self.file.chiudi()


class _Lettore:
    """Automa sul JSON in arrivo: copia tutto tranne il valore del campo del file."""

    def __init__(self, campo: str, flusso: _Base64InFlusso, limite_altri: int,
                 codice_corpo: str) -> None:
        self.campo = campo
        self.flusso = flusso
        self.limite_altri = limite_altri
        self.codice_corpo = codice_corpo
        self.altri = bytearray()
        self.profondita = 0
        self.in_stringa = False
        self.escape = False
        self.inizio_stringa = 0
        self.prima_della_stringa = b""   # ultimo carattere significativo prima della stringa
        self.ultimo = b""                # ultimo carattere significativo (fuori stringa)
        self.chiave: str | None = None   # chiave di primo livello appena letta
        self.attesa_valore_file = False  # dopo «"campo":» al primo livello
        self.nel_file = False            # dentro il valore base64 del campo
        self.escape_file = False
        self.trovato = False
        self._prima = b""                # ultimo carattere strutturale prima di una stringa

    def _copia(self, dati: bytes | bytearray) -> None:
        self.altri.extend(dati)
        if len(self.altri) > self.limite_altri:
            raise ErroreApi(self.codice_corpo,
                            f"I campi diversi dal file superano i {self.limite_altri} byte ammessi",
                            dettagli={"limite_byte": self.limite_altri})

    async def _nel_file(self, dati: bytes, i: int) -> int:
        """Consuma caratteri del valore base64 da ``i``; restituisce la posizione raggiunta."""
        n = len(dati)
        while i < n:
            if self.escape_file:
                # JSON ammette «\/» per «/» (alcuni codificatori lo producono); altro no
                if dati[i:i + 1] != b"/":
                    raise self.flusso._non_valido()
                await self.flusso.aggiungi(b"/")
                self.escape_file = False
                i += 1
                continue
            fine = dati.find(b'"', i)
            barra = dati.find(b"\\", i)
            stop = min(x for x in (fine, barra, n) if x >= 0)
            await self.flusso.aggiungi(dati[i:stop])
            if stop == n:
                return n
            if stop == barra:
                self.escape_file = True
                i = stop + 1
                continue
            # virgolette di chiusura
            await self.flusso.chiudi()
            self.nel_file = False
            self._copia(b'""')
            self.ultimo = b'"'
            return stop + 1
        return i

    def _non_stringa(self) -> ErroreApi:
        return ErroreApi("VAL-422", f"Il campo {self.campo} deve essere una stringa base64",
                         campi=[{"campo": self.campo, "posizione": "body",
                                 "tipo": "string_type", "messaggio": "Stringa base64 attesa"}])

    async def aggiungi(self, dati: bytes) -> None:
        """Consuma un blocco. Le corse fra caratteri strutturali si copiano con
        ricerche in C (``find``/regex), non carattere per carattere."""
        i, n = 0, len(dati)
        while i < n:
            if self.nel_file:
                i = await self._nel_file(dati, i)
                continue
            if self.in_stringa:
                if self.escape:
                    self._copia(dati[i:i + 1])
                    self.escape = False
                    i += 1
                    continue
                trovato = _FINE_STRINGA.search(dati, i)
                if trovato is None:
                    self._copia(dati[i:])
                    return
                j = trovato.start()
                self._copia(dati[i:j + 1])
                if dati[j:j + 1] == b"\\":
                    self.escape = True
                else:
                    self.in_stringa = False
                    self._fine_stringa()
                i = j + 1
                continue
            trovato = _STRUTTURALE.search(dati, i)
            j = n if trovato is None else trovato.start()
            if j > i:
                corsa = dati[i:j]
                significativa = corsa.strip(_SPAZI_JSON)
                if significativa:
                    if self.attesa_valore_file:
                        raise self._non_stringa()
                    self.ultimo = significativa[-1:]
                self._copia(corsa)
            if trovato is None:
                return
            c = dati[j:j + 1]
            i = j + 1
            if self.attesa_valore_file:
                self.attesa_valore_file = False
                if c != b'"':
                    raise self._non_stringa()
                if self.trovato:
                    raise ErroreApi("VAL-422", f"Campo {self.campo} ripetuto")
                self.trovato = True
                self.nel_file = True
                continue
            self._copia(c)
            self.ultimo = c
            if c == b'"':
                self.in_stringa = True
                self.inizio_stringa = len(self.altri) - 1
                self.prima_della_stringa = self._prima
            elif c in (b"{", b"["):
                self.profondita += 1
            elif c in (b"}", b"]"):
                self.profondita -= 1
            elif c == b":":
                # la chiave vale solo per i due punti che la seguono subito
                self.attesa_valore_file = self.profondita == 1 and self.chiave == self.campo
                self.chiave = None
            self._prima = c

    def _fine_stringa(self) -> None:
        self.ultimo = b'"'
        if self.profondita == 1 and self.prima_della_stringa in (b"{", b","):
            try:
                self.chiave = json.loads(bytes(self.altri[self.inizio_stringa:]))
            except ValueError:
                self.chiave = None
        else:
            self.chiave = None


async def leggi_json_con_file(request: Request, *, campo: str, limite_corpo: int,
                              limite_altri: int, limite_file: int, codice_corpo: str,
                              codice_file: str, codice_base64: str) -> JsonConFile:
    """Legge un oggetto JSON con un file base64 nel campo di primo livello ``campo``.

    In memoria restano al più un blocco del flusso, ~1 MB di base64 in attesa
    di decodifica e i campi diversi dal file (``limite_altri``). Errori:
    ``codice_corpo`` (413) se il corpo o gli altri campi superano i limiti,
    ``codice_file`` (413) se il file decodificato supera ``limite_file`` (ci si
    ferma subito), ``codice_base64`` se il base64 non è standard. Il chiamante
    chiude il risultato (``chiudi``) dopo averne consumato il file."""
    annunciata = request.headers.get("content-length")
    if annunciata and annunciata.isdigit() and int(annunciata) > limite_corpo:
        raise ErroreApi(codice_corpo, f"Il corpo supera i {limite_corpo} byte ammessi",
                        dettagli={"limite_byte": limite_corpo})
    file = FileDecodificato()
    flusso = _Base64InFlusso(file, limite_file, codice_base64, codice_file)
    lettore = _Lettore(campo, flusso, limite_altri, codice_corpo)
    letti = 0
    try:
        async for blocco in request.stream():
            letti += len(blocco)
            if letti > limite_corpo:
                raise ErroreApi(codice_corpo, f"Il corpo supera i {limite_corpo} byte ammessi",
                                dettagli={"limite_byte": limite_corpo})
            await lettore.aggiungi(blocco)
        if lettore.nel_file or lettore.attesa_valore_file:
            raise ErroreApi("VAL-422", "JSON incompleto: corpo terminato dentro il campo "
                                       f"{campo}")
    except BaseException:
        file.chiudi()
        raise
    if not lettore.trovato:
        file.chiudi()
        return JsonConFile(bytes(lettore.altri), None)
    return JsonConFile(bytes(lettore.altri), file)


# ── Limite di richieste contemporanee per replica (R03 f1, REV-05) ──

class LimiteConcorrenza:
    """Al più ``massimo()`` richieste contemporanee in questo processo (replica).

    Oltre il limite si risponde SUBITO con l'errore ritentabile ``codice`` (503
    con Retry-After): la richiesta non aspetta in coda tenendo la connessione
    e la memoria. In asyncio il controllo e l'incremento non hanno ``await``
    in mezzo, quindi sono atomici nel processo."""

    def __init__(self, massimo: Any, codice: str) -> None:
        self._massimo = massimo  # callable: letto a ogni richiesta (impostazione)
        self.codice = codice
        self.in_corso = 0

    @contextmanager
    def posto(self) -> Iterator[None]:
        limite = int(self._massimo())
        if self.in_corso >= limite:
            raise ErroreApi(self.codice, f"Troppe richieste contemporanee su questa replica "
                                         f"(al più {limite}): riprovare tra poco",
                            dettagli={"limite": limite})
        self.in_corso += 1
        try:
            yield
        finally:
            self.in_corso -= 1


# ── Budget del disco temporaneo per replica (R03 f3, REV-N2) ─────────

class BudgetDisco:
    """Byte prenotabili su ``/tmp`` da tutte le richieste di questo processo (replica).

    Come ``LimiteConcorrenza``: controllo e prenotazione senza ``await`` in
    mezzo (atomici in asyncio), nessuna coda: oltre il budget l'errore
    ritentabile ``codice`` (503 con Retry-After) arriva prima di leggere il corpo."""

    def __init__(self, massimo_byte: Any) -> None:
        self._massimo = massimo_byte  # callable: letto a ogni richiesta (impostazione)
        self.impegnati = 0

    @property
    def massimo(self) -> int:
        return int(self._massimo())

    def prenota(self, byte: int, codice: str) -> Prenotazione:
        massimo = self.massimo
        byte = max(int(byte), 0)
        if self.impegnati + byte > massimo:
            raise ErroreApi(codice, "Spazio temporaneo della replica esaurito da altre richieste "
                                    "in corso: riprovare tra poco",
                            dettagli={"budget_byte": massimo, "richiesti_byte": byte})
        self.impegnati += byte
        return Prenotazione(self, byte)


class Prenotazione:
    """Byte prenotati nel budget; ``rilascia`` (o ``close``, o l'uscita dal blocco
    ``with``) li restituisce una volta sola."""

    def __init__(self, budget: BudgetDisco, byte: int,
                 limite: LimiteConcorrenza | None = None) -> None:
        self._budget = budget
        self.byte = byte
        self._limite = limite
        self._attiva = True

    def rilascia(self) -> None:
        if self._attiva:
            self._attiva = False
            self._budget.impegnati -= self.byte
            if self._limite is not None:
                self._limite.in_corso -= 1

    async def close(self) -> None:  # come un modulo multipart: chiuso nel finally
        self.rilascia()

    def __enter__(self) -> Prenotazione:
        return self

    def __exit__(self, *_: object) -> None:
        self.rilascia()


MB = 1024 * 1024

#: Budget unico di /tmp della replica: multipart REST e PDND.
BUDGET_DISCO_TEMPORANEO = BudgetDisco(
    lambda: _impostazioni().disco_temporaneo_budget_mb * MB)
#: Multipart REST contemporanei della replica (oltre: 503 UPL-503-OCCUPATO).
LIMITE_MULTIPART = LimiteConcorrenza(
    lambda: _impostazioni().versamento_multipart_richieste_contemporanee_max,
    "UPL-503-OCCUPATO")


def _impostazioni() -> Any:
    from app.config import settings

    return settings


def registra_metriche_disco(registro: Any) -> None:
    """Metriche del budget del disco temporaneo nel /metrics del servizio."""
    from prometheus_client import Gauge

    Gauge("verumdoc_gateway_disco_temporaneo_byte_prenotati",
          "Byte prenotati su /tmp dalle richieste in corso (multipart e PDND)",
          registry=registro).set_function(lambda: BUDGET_DISCO_TEMPORANEO.impegnati)
    Gauge("verumdoc_gateway_disco_temporaneo_byte_budget",
          "Budget di byte su /tmp della replica (DISCO_TEMPORANEO_BUDGET_MB)",
          registry=registro).set_function(lambda: BUDGET_DISCO_TEMPORANEO.massimo)
    Gauge("verumdoc_gateway_multipart_in_corso",
          "Versamenti multipart REST in corso nella replica",
          registry=registro).set_function(lambda: LIMITE_MULTIPART.in_corso)


def byte_da_prenotare(request: Request, limite: int) -> int:
    """Il massimo che il corpo può occupare: Content-Length se dichiarata (e non oltre
    il limite, che scatta comunque), altrimenti il limite (chunked)."""
    annunciata = request.headers.get("content-length")
    if annunciata and annunciata.isdigit():
        return min(int(annunciata), limite)
    return limite


def posto_multipart(request: Request, *, limite: int) -> Prenotazione:
    """Posto per un multipart: tetto di concorrenza e prenotazione nel budget del disco.

    Da rilasciare (``with``, ``rilascia`` o ``close``) DOPO aver chiuso il modulo,
    cioè quando Starlette ha eliminato i file temporanei."""
    codice = LIMITE_MULTIPART.codice
    massimo = int(LIMITE_MULTIPART._massimo())
    if LIMITE_MULTIPART.in_corso >= massimo:
        raise ErroreApi(codice, f"Troppi versamenti multipart contemporanei su questa replica "
                                f"(al più {massimo}): riprovare tra poco",
                        dettagli={"limite": massimo})
    prenotazione = BUDGET_DISCO_TEMPORANEO.prenota(byte_da_prenotare(request, limite), codice)
    LIMITE_MULTIPART.in_corso += 1
    prenotazione._limite = LIMITE_MULTIPART
    return prenotazione


# ── Modulo multipart letto DOPO l'autenticazione (R03 f1, REV-09) ────

async def leggi_modulo_limitato(request: Request, *, limite: int, codice: str,
                                max_file: int, max_campi: int = 100) -> Any:
    """Il modulo multipart della richiesta, letto con un limite sull'intero corpo.

    Si chiama DENTRO la funzione della rotta, cioè dopo che FastAPI ha risolto le
    dipendenze d'autenticazione: una rotta con parametri ``Form``/``File``
    farebbe leggere a FastAPI tutto il corpo (file su disco temporaneo) PRIMA
    delle dipendenze (FastAPI 0.155, ``routing.get_request_handler``). Il limite
    vale anche senza Content-Length (chunked): si conta ciò che arriva e ci si
    ferma con ``codice`` (413) appena lo si supera. Chi chiama chiude il modulo
    (``await modulo.close()``)."""
    from starlette.requests import Request as RichiestaStarlette

    annunciata = request.headers.get("content-length")
    if annunciata and annunciata.isdigit() and int(annunciata) > limite:
        raise ErroreApi(codice, f"Il corpo supera i {limite} byte ammessi",
                        dettagli={"limite_byte": limite})
    ricevi_originale = request.receive
    letti = 0

    async def ricevi() -> Any:
        nonlocal letti
        messaggio = await ricevi_originale()
        if messaggio.get("type") == "http.request":
            letti += len(messaggio.get("body", b""))
            if letti > limite:
                raise ErroreApi(codice, f"Il corpo supera i {limite} byte ammessi",
                                dettagli={"limite_byte": limite})
        return messaggio

    limitata = RichiestaStarlette(request.scope, ricevi)
    return await limitata.form(max_files=max_file, max_fields=max_campi)
