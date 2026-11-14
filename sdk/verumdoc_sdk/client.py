# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Client sincrono dell'API VerumDoc (httpx).

Esempio::

    from verumdoc_sdk import VerumDoc, ChiaveApi

    with VerumDoc("https://api.verumdoc.it", credenziali=ChiaveApi("vd_<id>_<segreto>")) as vd:
        esito = vd.versa(indice, file={"fattura.pdf": "/dati/fattura.pdf"})
        stato = vd.attendi_versamento(esito.pdv_id)

Vedi README.md per gli esempi completi.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx

from verumdoc_sdk import _operazioni as op
from verumdoc_sdk._comune import (
    URL_PREDEFINITO,
    Credenziali,
    Instradamento,
    PoliticaAttesa,
    PoliticaRitentativi,
    Richiesta,
    chiave_derivata,
    come_indice,
    corpo_multipart,
    credenziali_da_ambiente,
    dimensione_attesa,
    documenti_senza_impronta,
    errore_da_risposta,
    imposta_impronta,
    intestazioni_base,
    leggi_modello,
    nuova_chiave_idempotenza,
    prepara_sorgenti,
)
from verumdoc_sdk._operazioni import CaricamentoCompletato, EsitoVersamento
from verumdoc_sdk.errori import (
    CaricamentoNonUtilizzabile,
    ErroreIntegrita,
    ErroreRete,
    TempoScaduto,
    errore_da_problema,
    problema_da_corpo,
)
from verumdoc_sdk.modelli import (
    STATI_FINALI_PDV,
    ConsegnaPdD,
    ConsegnaRiaccodata,
    ElencoConsegne,
    ElencoEventi,
    ElencoSottoscrizioni,
    ElencoVersamenti,
    Evento,
    Identita,
    IndiceUD,
    PacchettoDistribuzione,
    ParteAccettata,
    RispostaVersamento,
    RisultatoRicerca,
    SessioneCaricamento,
    Sottoscrizione,
    SottoscrizioneCreata,
    StatoCaricamento,
    StatoVersamento,
    TipoPdD,
    VersamentoInElenco,
)
from verumdoc_sdk.sorgenti import LettoreParti, Sorgente, come_sorgente

MB = 1024 * 1024
T = TypeVar("T")


class VerumDoc:
    """Client sincrono. Thread-safe per le chiamate singole (usa un ``httpx.Client``).

    Parametri principali:
      - ``base_url``: URL dell'API (predefinito ``https://api.verumdoc.it``, oppure
        ``VERUMDOC_URL``);
      - ``credenziali``: :class:`ChiaveApi` o :class:`TokenBearer` (predefinito da
        ``VERUMDOC_API_KEY`` / ``VERUMDOC_TOKEN``);
      - ``basi``: URL per prefisso di percorso (sviluppo senza ingress);
      - ``ritentativi`` / ``attesa``: politiche di ritentativo e di interrogazione;
      - ``concorrenza_parti``: parti inviate in parallelo nelle sessioni di caricamento
        (in memoria al più ``concorrenza_parti + 1`` parti);
      - ``soglia_multipart``: oltre questa dimensione per file ``versa`` usa le
        sessioni di caricamento e il versamento per riferimento;
      - ``http``: un ``httpx.Client`` già configurato (proxy, certificati, test).
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        credenziali: Credenziali | None = None,
        basi: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout = 60.0,
        ritentativi: PoliticaRitentativi | None = None,
        attesa: PoliticaAttesa | None = None,
        concorrenza_parti: int = 4,
        soglia_multipart: int = 32 * MB,
        http: httpx.Client | None = None,
        dormi: Callable[[float], None] = time.sleep,
        orologio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.instradamento = Instradamento(
            base_url or os.environ.get("VERUMDOC_URL") or URL_PREDEFINITO, dict(basi or {}))
        self.credenziali = credenziali if credenziali is not None else credenziali_da_ambiente()
        self.ritentativi = ritentativi or PoliticaRitentativi()
        self.attesa = attesa or PoliticaAttesa()
        if concorrenza_parti < 1:
            raise ValueError("concorrenza_parti deve essere almeno 1")
        self.concorrenza_parti = concorrenza_parti
        self.soglia_multipart = soglia_multipart
        self._proprio = http is None
        self._http = http or httpx.Client(timeout=timeout)
        self._dormi = dormi
        self._orologio = orologio

    # ── Ciclo di vita ─────────────────────────────────────────────────
    def __enter__(self) -> VerumDoc:
        return self

    def __exit__(self, *_: object) -> None:
        self.chiudi()

    def chiudi(self) -> None:
        if self._proprio:
            self._http.close()

    # ── Trasporto con ritentativi ─────────────────────────────────────
    def _costruisci(self, r: Richiesta, argomenti: dict[str, Any]) -> httpx.Request:
        intestazioni = intestazioni_base(self.credenziali)
        intestazioni.update(r.intestazioni)
        return self._http.build_request(r.metodo, self.instradamento.url(r.percorso),
                                        params=r.params, headers=intestazioni, **argomenti)

    def _invia(self, r: Richiesta, *, flusso: bool = False) -> httpx.Response:
        """Esegue la richiesta; ritenta solo se ripetibile e l'errore è ritentabile
        (o di rete). Restituisce la risposta 2xx/3xx; altrimenti solleva l'eccezione
        tipizzata. Con ``flusso`` il corpo non è letto (chiudere la risposta)."""
        politica = self.ritentativi
        for tentativo in range(1, politica.tentativi + 1):
            argomenti, chiudi = r.argomenti()
            try:
                risposta = self._http.send(self._costruisci(r, argomenti), stream=True)
            except httpx.TransportError as exc:
                chiudi()
                if not r.ripetibile or tentativo == politica.tentativi:
                    raise ErroreRete(f"{r.metodo} {r.percorso}: {type(exc).__name__}: "
                                     f"{exc}") from exc
                self._dormi(politica.attesa(tentativo))
                continue
            chiudi()
            if risposta.status_code < 400:
                if not flusso:
                    risposta.read()
                    risposta.close()
                return risposta
            risposta.read()
            risposta.close()
            errore = errore_da_risposta(risposta)
            if errore.ritentabile and r.ripetibile and tentativo < politica.tentativi:
                self._dormi(politica.attesa(tentativo, errore.retry_after))
                continue
            raise errore
        raise AssertionError("non raggiungibile")  # pragma: no cover

    def _attendi(self, leggi: Callable[[], T], finale: Callable[[T], bool],
                 politica: PoliticaAttesa | None, cosa: str) -> T:
        politica = politica or self.attesa
        limite = self._orologio() + politica.timeout
        stato = leggi()
        for intervallo in politica.intervalli():
            if finale(stato):
                return stato
            if self._orologio() + intervallo > limite:
                raise TempoScaduto(f"{cosa}: stato finale non raggiunto in "
                                   f"{politica.timeout:.0f} s", ultimo_stato=stato)
            self._dormi(intervallo)
            stato = leggi()
        raise AssertionError("non raggiungibile")  # pragma: no cover

    # ── Versamento ────────────────────────────────────────────────────
    def versa(
        self,
        indice: IndiceUD | Mapping[str, Any],
        file: Mapping[str, Any],
        *,
        chiave_idempotenza: str | None = None,
        modo: str = "auto",
    ) -> EsitoVersamento:
        """Versa un'unità documentaria. ``file`` associa a ogni ``nome_file``
        dell'indice la sua sorgente (percorso, byte, :class:`Sorgente`).

        ``modo``: ``multipart`` (una richiesta, file piccoli e rileggibili),
        ``riferimento`` (sessioni di caricamento a parti parallele e riprendibili,
        poi versamento per riferimento) o ``auto`` (riferimento se un file supera
        ``soglia_multipart`` o non è rileggibile). Le impronte mancanti nell'indice
        si calcolano sui byte. La ``Idempotency-Key`` si genera se non indicata ed è
        riusata in ogni ritentativo: per riprendere dopo un'interruzione si richiama
        ``versa`` con la stessa chiave (``EsitoVersamento.chiave_idempotenza``).
        """
        ud = come_indice(indice)
        sorgenti = prepara_sorgenti(ud, file)
        chiave = chiave_idempotenza or nuova_chiave_idempotenza()
        if modo not in ("auto", "multipart", "riferimento"):
            raise ValueError("modo: auto, multipart o riferimento")
        if modo == "auto":
            modo = "riferimento" if any(
                not s.ripetibile or s.dimensione is None or s.dimensione > self.soglia_multipart
                for s in sorgenti.values()) else "multipart"
        if modo == "multipart":
            return self._versa_multipart(ud, sorgenti, chiave)
        caricamenti: dict[str, str] = {}
        for nome, sorgente in sorgenti.items():
            fatto = self.carica(sorgente, nome_file=nome, chiave_idempotenza=chiave_derivata(
                chiave, f"{nome}:{sorgente.dimensione}"))
            imposta_impronta(ud, nome, fatto.sha256)
            caricamenti[nome] = fatto.caricamento_id
        esito = self.versa_per_riferimento(ud, caricamenti, chiave_idempotenza=chiave)
        esito.caricamenti = caricamenti
        return esito

    def _versa_multipart(self, ud: IndiceUD, sorgenti: Mapping[str, Sorgente],
                         chiave: str) -> EsitoVersamento:
        for doc in documenti_senza_impronta(ud):
            imposta_impronta(ud, doc.nome_file, sorgenti[doc.nome_file].sha256())
        ripetibile = all(s.ripetibile for s in sorgenti.values())
        risposta = self._invia(op.versamento_multipart(corpo_multipart(ud, sorgenti), chiave,
                                                       ripetibile))
        return _esito(risposta, chiave)

    def versa_per_riferimento(self, indice: IndiceUD | Mapping[str, Any],
                              caricamenti: Mapping[str, str], *,
                              chiave_idempotenza: str | None = None) -> EsitoVersamento:
        """Versamento JSON che cita sessioni di caricamento COMPLETE
        (``{nome_file: caricamento_id}``). L'indice deve avere le impronte."""
        ud = come_indice(indice)
        chiave = chiave_idempotenza or nuova_chiave_idempotenza()
        risposta = self._invia(op.versamento_per_riferimento(ud, caricamenti, chiave))
        esito = _esito(risposta, chiave)
        esito.caricamenti = dict(caricamenti)
        return esito

    def stato_versamento(self, pdv_id: str) -> StatoVersamento:
        return leggi_modello(StatoVersamento, self._invia(op.stato_versamento(pdv_id)))

    def attendi_versamento(self, pdv_id: str, *, stati: Sequence[str] | None = None,
                           politica: PoliticaAttesa | None = None) -> StatoVersamento:
        """Interroga lo stato con intervalli crescenti finché è in ``stati``
        (predefinito: CONSERVATO, ERRORE, ANNULLATO). TempoScaduto oltre il limite."""
        attesi = frozenset(stati) if stati else STATI_FINALI_PDV
        return self._attendi(lambda: self.stato_versamento(pdv_id),
                             lambda s: s.stato in attesi, politica, f"PdV {pdv_id}")

    def scarica_rapporto(self, pdv_id: str,
                         destinazione: str | os.PathLike[str] | None = None) -> bytes | Path:
        """Rapporto di versamento definitivo (XML). Con ``destinazione`` lo scrive su
        file a blocchi (prima in un file temporaneo nella stessa cartella) e restituisce
        il percorso; senza, restituisce i byte. Verifica l'impronta
        ``X-Impronta-SHA256`` inviata dal servizio (ErroreIntegrita se diversa).
        409 VRS-409 (Rifiutato) finché il PdV non è CONSERVATO."""
        risposta = self._invia(op.rapporto_versamento(pdv_id), flusso=True)
        attesa = risposta.headers.get("X-Impronta-SHA256")
        h = hashlib.sha256()
        try:
            if destinazione is None:
                parti = []
                for blocco in risposta.iter_bytes():
                    h.update(blocco)
                    parti.append(blocco)
                _controlla_impronta(attesa, h.hexdigest(), f"RdV del PdV {pdv_id}")
                return b"".join(parti)
            finale = Path(destinazione)
            fd, temporaneo = tempfile.mkstemp(dir=finale.parent, prefix=f".{finale.name}.")
            try:
                with os.fdopen(fd, "wb") as f:
                    for blocco in risposta.iter_bytes():
                        h.update(blocco)
                        f.write(blocco)
                _controlla_impronta(attesa, h.hexdigest(), f"RdV del PdV {pdv_id}")
                os.replace(temporaneo, finale)
            except BaseException:
                Path(temporaneo).unlink(missing_ok=True)
                raise
            return finale
        finally:
            risposta.close()

    def elenca_versamenti(self, tenant_id: str | None = None, *, stato: str | None = None,
                          cursore: str | None = None, limite: int = 20) -> ElencoVersamenti:
        """Una pagina dei versamenti dell'ente, dal più recente (dashboard-api; anche
        con chiave API). Senza ``tenant_id`` l'ente è quello dell'identità. Per la
        pagina successiva ripassare ``cursore`` della risposta (se ``altri``)."""
        return leggi_modello(ElencoVersamenti, self._invia(
            op.elenco_versamenti(tenant_id, stato, cursore, limite)))

    def itera_versamenti(self, tenant_id: str | None = None, *, stato: str | None = None,
                         limite: int = 100) -> Iterator[VersamentoInElenco]:
        """Tutti i versamenti dell'ente dal più recente, seguendo il cursore pagina
        per pagina (``limite`` per pagina, al più 100). Si ferma quando ``altri`` è
        falso; non legge mai tutto l'elenco in memoria."""
        cursore: str | None = None
        while True:
            pagina = self.elenca_versamenti(tenant_id, stato=stato, cursore=cursore,
                                            limite=limite)
            yield from pagina.items
            if not pagina.altri or not pagina.cursore or pagina.cursore == cursore:
                return
            cursore = pagina.cursore

    def identita(self) -> Identita:
        """Chi sta chiamando (``GET /api/v1/me``): ente, piano, metodo di
        autenticazione, chiave API e suo responsabile."""
        return leggi_modello(Identita, self._invia(op.identita()))

    # ── Sessioni di caricamento ───────────────────────────────────────
    def apri_caricamento(self, nome_file: str, dimensione: int, *, mime: str | None = None,
                         impronta_sha256: str | None = None,
                         chiave_idempotenza: str | None = None,
                         ) -> tuple[SessioneCaricamento, bool]:
        """Apre una sessione; restituisce ``(sessione, nuova)``: ``nuova`` è falso se la
        stessa ``Idempotency-Key`` aveva già aperto la sessione."""
        risposta = self._invia(op.apri_caricamento(
            nome_file, dimensione, mime, impronta_sha256,
            chiave_idempotenza or nuova_chiave_idempotenza()))
        return leggi_modello(SessioneCaricamento, risposta), risposta.status_code == 201

    def stato_caricamento(self, caricamento_id: str) -> StatoCaricamento:
        return leggi_modello(StatoCaricamento, self._invia(op.stato_caricamento(caricamento_id)))

    def invia_parte(self, caricamento_id: str, numero: int, dati: bytes) -> ParteAccettata:
        return leggi_modello(ParteAccettata,
                             self._invia(op.invia_parte(caricamento_id, numero, dati)))

    def completa_caricamento(self, caricamento_id: str) -> StatoCaricamento:
        return leggi_modello(StatoCaricamento,
                             self._invia(op.completa_caricamento(caricamento_id)))

    def annulla_caricamento(self, caricamento_id: str) -> None:
        self._invia(op.annulla_caricamento(caricamento_id))

    def attendi_caricamento(self, caricamento_id: str, *,
                            politica: PoliticaAttesa | None = None) -> StatoCaricamento:
        return self._attendi(lambda: self.stato_caricamento(caricamento_id),
                             lambda s: s.stato in op.STATI_FINALI_CARICAMENTO, politica,
                             f"Caricamento {caricamento_id}")

    def carica(self, sorgente: Any, *, nome_file: str | None = None, mime: str | None = None,
               chiave_idempotenza: str | None = None,
               caricamento_id: str | None = None) -> CaricamentoCompletato:
        """Carica un file con una sessione di caricamento: parti in parallelo
        (``concorrenza_parti``), lette in ordine dalla sorgente, ognuna con la sua
        impronta; poi completamento e attesa della verifica. Riprende una sessione
        interrotta con ``caricamento_id`` o con la stessa ``chiave_idempotenza``
        (le parti già ricevute non si reinviano). Restituisce la sessione COMPLETA e
        lo SHA-256 calcolato dall'SDK, confrontato con quello del servizio."""
        s = come_sorgente(sorgente, nome_file)
        nome = nome_file or s.nome
        if not nome:
            raise ValueError("Indicare nome_file")
        if s.asincrona:
            raise ValueError("Flusso asincrono: usare VerumDocAsincrono")
        if caricamento_id is None:
            if s.dimensione is None:
                raise ValueError("Sorgente senza dimensione: Sorgente.da_flusso(..., dimensione=)")
            sessione, nuova = self.apri_caricamento(
                nome, s.dimensione, mime=mime or s.mime,
                chiave_idempotenza=chiave_idempotenza or nuova_chiave_idempotenza())
            caricamento_id = sessione.caricamento_id
            if nuova:
                stato_iniziale, ricevute = "APERTO", set()
                dim_parte, totale = sessione.dimensione_parte, s.dimensione
            else:
                attuale = self.stato_caricamento(caricamento_id)
                stato_iniziale, ricevute = attuale.stato, set(attuale.parti_ricevute)
                dim_parte, totale = attuale.dimensione_parte, attuale.dimensione
        else:
            attuale = self.stato_caricamento(caricamento_id)
            stato_iniziale, ricevute = attuale.stato, set(attuale.parti_ricevute)
            dim_parte, totale = attuale.dimensione_parte, attuale.dimensione
        if stato_iniziale in ("SCADUTO", "ANNULLATO"):
            raise CaricamentoNonUtilizzabile(caricamento_id, stato_iniziale)
        if s.dimensione is not None and s.dimensione != totale:
            raise ValueError(f"La sessione {caricamento_id} è per {totale} byte, la sorgente "
                             f"ne ha {s.dimensione}")
        invia = stato_iniziale == "APERTO"
        sha = self._invia_parti(s, caricamento_id, dim_parte, totale,
                                ricevute if invia else None)
        stato = self.completa_caricamento(caricamento_id) if invia else \
            self.stato_caricamento(caricamento_id)
        if stato.stato not in op.STATI_FINALI_CARICAMENTO:
            stato = self.attendi_caricamento(caricamento_id)
        return _caricamento_finale(caricamento_id, sha, stato)

    def _invia_parti(self, s: Sorgente, caricamento_id: str, dim_parte: int, totale: int,
                     ricevute: set[int] | None) -> str:
        """Legge la sorgente in ordine, invia le parti mancanti in parallelo
        (``ricevute`` None: nessun invio, solo impronta) e restituisce lo SHA-256."""
        lettore = LettoreParti(dim_parte)
        in_volo: set[Future[ParteAccettata]] = set()
        numero = 0

        def spedisci(dati: bytes) -> None:
            nonlocal numero
            numero += 1
            if len(dati) != dimensione_attesa(numero, totale, dim_parte):
                raise ErroreIntegrita(f"Parte {numero}: la sorgente non ha la dimensione "
                                      f"dichiarata ({totale} byte)")
            if ricevute is None or numero in ricevute:
                return
            while len(in_volo) >= self.concorrenza_parti:
                _raccogli(in_volo, wait(in_volo, return_when=FIRST_COMPLETED).done)
            in_volo.add(esecutore.submit(self.invia_parte, caricamento_id, numero, dati))

        with ThreadPoolExecutor(max_workers=self.concorrenza_parti,
                                thread_name_prefix="verumdoc-parti") as esecutore:
            try:
                for blocco in s.blocchi():
                    for parte in lettore.aggiungi(blocco):
                        spedisci(parte)
                    if lettore.letti > totale:
                        raise ErroreIntegrita(f"La sorgente supera i {totale} byte dichiarati")
                resto = lettore.fine()
                if resto is not None:
                    spedisci(resto)
                if lettore.letti != totale:
                    raise ErroreIntegrita(f"Letti {lettore.letti} byte, dichiarati {totale}")
                _raccogli(in_volo, set(in_volo))
            except BaseException:
                for f in in_volo:
                    f.cancel()
                raise
        return lettore.sha256.hexdigest()

    # ── Eventi ────────────────────────────────────────────────────────
    def elenca_eventi(self, *, dopo: int = 0, limite: int | None = None,
                      tipi: Sequence[str] | None = None) -> ElencoEventi:
        """Eventi dell'ente dopo il cursore ``dopo`` (contratto C3)."""
        return leggi_modello(ElencoEventi, self._invia(op.elenco_eventi(dopo, limite, tipi)))

    def itera_eventi(self, *, dopo: int = 0, limite: int | None = None,
                     tipi: Sequence[str] | None = None) -> Iterator[Evento]:
        """Tutti gli eventi disponibili dopo la posizione ``dopo``, pagina per
        pagina (finché ``altri`` è vero).

        Il cursore è la POSIZIONE nell'elenco, non l'id: per riprendere salvare
        ``evento.posizione`` dell'ultimo evento elaborato (oppure ``cursore``
        della pagina) e passarlo come ``dopo``. ``evento.id`` serve solo a
        scartare i duplicati (è lo stesso dei webhook). Per gli eventi creati
        prima di R03 posizione e id coincidono."""
        while True:
            pagina = self.elenca_eventi(dopo=dopo, limite=limite, tipi=tipi)
            yield from pagina.eventi
            if not pagina.altri or pagina.cursore == dopo:
                return
            dopo = pagina.cursore

    # ── Webhook ───────────────────────────────────────────────────────
    def crea_webhook(self, url: str, tipi: Sequence[str], *, descrizione: str | None = None,
                     attiva: bool = True,
                     chiave_idempotenza: str | None = None) -> SottoscrizioneCreata:
        """Crea una sottoscrizione (solo amministratore dell'ente, token utente).
        Il ``segreto`` è nella risposta: conservarlo. R04: la richiesta porta una
        ``Idempotency-Key`` (generata se non indicata) riusata nei ritentativi
        automatici: un ritentativo restituisce la stessa sottoscrizione con lo
        stesso segreto. Ripetendo la chiamata con la stessa ``chiave_idempotenza``
        (entro 24 ore) si rilegge la sottoscrizione creata e il suo segreto."""
        return leggi_modello(SottoscrizioneCreata, self._invia(op.crea_webhook(
            url, tipi, descrizione, attiva, chiave_idempotenza or nuova_chiave_idempotenza())))

    def elenca_webhook(self) -> list[Sottoscrizione]:
        return leggi_modello(ElencoSottoscrizioni,
                             self._invia(op.elenco_webhook())).sottoscrizioni

    def leggi_webhook(self, webhook_id: str) -> Sottoscrizione:
        return leggi_modello(Sottoscrizione, self._invia(op.leggi_webhook(webhook_id)))

    def modifica_webhook(self, webhook_id: str, **campi: Any) -> Sottoscrizione:
        """Campi: ``url``, ``tipi``, ``descrizione``, ``attiva``."""
        return leggi_modello(Sottoscrizione, self._invia(op.modifica_webhook(webhook_id, campi)))

    def elimina_webhook(self, webhook_id: str) -> None:
        self._invia(op.elimina_webhook(webhook_id))

    def elenca_consegne(self, webhook_id: str, *, stato: str | None = None, dopo: int = 0,
                        limite: int | None = None) -> ElencoConsegne:
        return leggi_modello(ElencoConsegne, self._invia(
            op.elenco_consegne(webhook_id, stato, dopo, limite)))

    def riprova_consegna(self, webhook_id: str, consegna_id: int) -> ConsegnaRiaccodata:
        return leggi_modello(ConsegnaRiaccodata,
                             self._invia(op.riprova_consegna(webhook_id, consegna_id)))

    # ── Ricerca e distribuzione ───────────────────────────────────────
    def cerca(self, query: str | None = None, *, classe_documentale: str | None = None,
              anno: str | int | None = None, data_da: datetime | None = None,
              data_a: datetime | None = None, limite: int = 20,
              cursore: str | None = None) -> RisultatoRicerca:
        """Ricerca nei documenti conservati dell'ente (pagina a cursore)."""
        return leggi_modello(RisultatoRicerca, self._invia(op.ricerca(
            query, classe_documentale, anno, data_da, data_a, limite, cursore)))

    def itera_ricerca(self, query: str | None = None, **filtri: Any) -> Iterator[Any]:
        """Tutti i risultati della ricerca, seguendo il cursore."""
        cursore = filtri.pop("cursore", None)
        while True:
            pagina = self.cerca(query, cursore=cursore, **filtri)
            yield from pagina.items
            if not pagina.altri or not pagina.cursore:
                return
            cursore = pagina.cursore

    def richiedi_pdd(self, aip_id: str, tipo: TipoPdD | str, motivo: str, *,
                     file_richiesti: Sequence[str] | None = None,
                     durata_link_secondi: int | None = None,
                     consegna: ConsegnaPdD | str | None = None,
                     chiave_idempotenza: str | None = None) -> PacchettoDistribuzione:
        """Chiede un pacchetto di distribuzione. Con consegna LINK la risposta ha i
        collegamenti; con ZIP lo stato è IN_PREPARAZIONE: usare ``attendi_pdd``.
        R04: la richiesta porta una ``Idempotency-Key`` (generata se non indicata)
        ed è ritentata in sicurezza: un ritentativo restituisce lo stesso PdD."""
        return leggi_modello(PacchettoDistribuzione, self._invia(op.richiedi_pdd(
            aip_id, tipo, motivo, file_richiesti, durata_link_secondi, consegna,
            chiave_idempotenza or nuova_chiave_idempotenza())))

    def stato_pdd(self, pdd_id: str) -> PacchettoDistribuzione:
        return leggi_modello(PacchettoDistribuzione, self._invia(op.stato_pdd(pdd_id)))

    def attendi_pdd(self, pdd_id: str, *,
                    politica: PoliticaAttesa | None = None) -> PacchettoDistribuzione:
        return self._attendi(lambda: self.stato_pdd(pdd_id),
                             lambda p: p.stato in op.STATI_FINALI_PDD, politica, f"PdD {pdd_id}")


# ── Funzioni di supporto (condivise con il client asincrono) ─────────

def _raccogli(in_volo: set[Future[ParteAccettata]], fatti: set[Future[ParteAccettata]]) -> None:
    for f in fatti:
        in_volo.discard(f)
        f.result()  # rilancia l'errore della parte


def _esito(risposta: httpx.Response, chiave: str) -> EsitoVersamento:
    return EsitoVersamento(
        risposta=leggi_modello(RispostaVersamento, risposta),
        chiave_idempotenza=chiave,
        ripetuto=risposta.headers.get("Idempotent-Replayed", "").lower() == "true",
        stato_http=risposta.status_code,
        location=risposta.headers.get("Location"),
        deprecazione=risposta.headers.get("Deprecation"),
        sunset=risposta.headers.get("Sunset"),
    )


def _controlla_impronta(attesa: str | None, calcolata: str, cosa: str) -> None:
    if attesa and attesa.lower() != calcolata:
        raise ErroreIntegrita(f"{cosa}: impronta ricevuta diversa da X-Impronta-SHA256")


def _caricamento_finale(caricamento_id: str, sha: str,
                        stato: StatoCaricamento) -> CaricamentoCompletato:
    if stato.stato == "ERRORE":
        problema = problema_da_corpo(422, stato.errore or {}, testo="Verifica del file fallita")
        raise errore_da_problema(problema, metodo="GET",
                                 url=f"{op.P_CARICAMENTI}/{caricamento_id}")
    if stato.stato != "COMPLETO":
        raise CaricamentoNonUtilizzabile(caricamento_id, stato.stato)
    servizio = (stato.impronte or {}).get("SHA-256")
    if servizio is None or servizio.lower() != sha:
        raise ErroreIntegrita(f"Caricamento {caricamento_id}: SHA-256 del servizio "
                              f"{servizio} diverso da quello dei byte inviati {sha}")
    return CaricamentoCompletato(caricamento_id=caricamento_id, sha256=sha, stato=stato)
