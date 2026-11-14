# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Client asincrono dell'API VerumDoc (httpx.AsyncClient): stesse operazioni e
stessi nomi del client sincrono, con ``await``.

Esempio::

    async with VerumDocAsincrono(credenziali=ChiaveApi("vd_<id>_<segreto>")) as vd:
        esito = await vd.versa(indice, file={"video.mp4": Sorgente.da_percorso("video.mp4")})
        stato = await vd.attendi_versamento(esito.pdv_id)
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

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
from verumdoc_sdk.client import MB, T, _caricamento_finale, _controlla_impronta, _esito
from verumdoc_sdk.errori import (
    CaricamentoNonUtilizzabile,
    ErroreIntegrita,
    ErroreRete,
    TempoScaduto,
)
from verumdoc_sdk.modelli import (
    STATI_FINALI_PDV,
    ConsegnaPdD,
    ConsegnaRiaccodata,
    DocumentoTrovato,
    ElencoConsegne,
    ElencoEventi,
    ElencoSottoscrizioni,
    ElencoVersamenti,
    Evento,
    Identita,
    IndiceUD,
    PacchettoDistribuzione,
    ParteAccettata,
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


class VerumDocAsincrono:
    """Client asincrono; parametri come :class:`verumdoc_sdk.VerumDoc` (``http`` è un
    ``httpx.AsyncClient``, ``dormi`` una coroutine come ``asyncio.sleep``)."""

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
        http: httpx.AsyncClient | None = None,
        dormi: Callable[[float], Awaitable[None]] = asyncio.sleep,
        orologio: Callable[[], float] | None = None,
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
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._dormi = dormi
        self._orologio = orologio

    async def __aenter__(self) -> VerumDocAsincrono:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.chiudi()

    async def chiudi(self) -> None:
        if self._proprio:
            await self._http.aclose()

    def _adesso(self) -> float:
        return self._orologio() if self._orologio else asyncio.get_running_loop().time()

    # ── Trasporto con ritentativi ─────────────────────────────────────
    def _costruisci(self, r: Richiesta, argomenti: dict[str, Any]) -> httpx.Request:
        intestazioni = intestazioni_base(self.credenziali)
        intestazioni.update(r.intestazioni)
        return self._http.build_request(r.metodo, self.instradamento.url(r.percorso),
                                        params=r.params, headers=intestazioni, **argomenti)

    async def _invia(self, r: Richiesta, *, flusso: bool = False) -> httpx.Response:
        politica = self.ritentativi
        for tentativo in range(1, politica.tentativi + 1):
            argomenti, chiudi = r.argomenti()
            try:
                risposta = await self._http.send(self._costruisci(r, argomenti), stream=True)
            except httpx.TransportError as exc:
                chiudi()
                if not r.ripetibile or tentativo == politica.tentativi:
                    raise ErroreRete(f"{r.metodo} {r.percorso}: {type(exc).__name__}: "
                                     f"{exc}") from exc
                await self._dormi(politica.attesa(tentativo))
                continue
            chiudi()
            if risposta.status_code < 400:
                if not flusso:
                    await risposta.aread()
                    await risposta.aclose()
                return risposta
            await risposta.aread()
            await risposta.aclose()
            errore = errore_da_risposta(risposta)
            if errore.ritentabile and r.ripetibile and tentativo < politica.tentativi:
                await self._dormi(politica.attesa(tentativo, errore.retry_after))
                continue
            raise errore
        raise AssertionError("non raggiungibile")  # pragma: no cover

    async def _attendi(self, leggi: Callable[[], Awaitable[T]], finale: Callable[[T], bool],
                       politica: PoliticaAttesa | None, cosa: str) -> T:
        politica = politica or self.attesa
        limite = self._adesso() + politica.timeout
        stato = await leggi()
        for intervallo in politica.intervalli():
            if finale(stato):
                return stato
            if self._adesso() + intervallo > limite:
                raise TempoScaduto(f"{cosa}: stato finale non raggiunto in "
                                   f"{politica.timeout:.0f} s", ultimo_stato=stato)
            await self._dormi(intervallo)
            stato = await leggi()
        raise AssertionError("non raggiungibile")  # pragma: no cover

    # ── Versamento ────────────────────────────────────────────────────
    async def versa(self, indice: IndiceUD | Mapping[str, Any], file: Mapping[str, Any], *,
                    chiave_idempotenza: str | None = None,
                    modo: str = "auto") -> EsitoVersamento:
        """Vedi :meth:`verumdoc_sdk.VerumDoc.versa`. I flussi asincroni (iterabili
        asincroni, ``async read``) vanno sempre per sessione di caricamento."""
        ud = come_indice(indice)
        sorgenti = prepara_sorgenti(ud, file)
        chiave = chiave_idempotenza or nuova_chiave_idempotenza()
        if modo not in ("auto", "multipart", "riferimento"):
            raise ValueError("modo: auto, multipart o riferimento")
        if modo == "auto":
            modo = "riferimento" if any(
                not s.ripetibile or s.asincrona or s.dimensione is None
                or s.dimensione > self.soglia_multipart for s in sorgenti.values()
            ) else "multipart"
        if modo == "multipart":
            for doc in documenti_senza_impronta(ud):
                sha = await asyncio.to_thread(sorgenti[doc.nome_file].sha256)
                imposta_impronta(ud, doc.nome_file, sha)
            ripetibile = all(s.ripetibile for s in sorgenti.values())
            risposta = await self._invia(op.versamento_multipart(
                corpo_multipart(ud, sorgenti), chiave, ripetibile))
            return _esito(risposta, chiave)
        caricamenti: dict[str, str] = {}
        for nome, sorgente in sorgenti.items():
            fatto = await self.carica(sorgente, nome_file=nome, chiave_idempotenza=chiave_derivata(
                chiave, f"{nome}:{sorgente.dimensione}"))
            imposta_impronta(ud, nome, fatto.sha256)
            caricamenti[nome] = fatto.caricamento_id
        esito = await self.versa_per_riferimento(ud, caricamenti, chiave_idempotenza=chiave)
        return esito

    async def versa_per_riferimento(self, indice: IndiceUD | Mapping[str, Any],
                                    caricamenti: Mapping[str, str], *,
                                    chiave_idempotenza: str | None = None) -> EsitoVersamento:
        ud = come_indice(indice)
        chiave = chiave_idempotenza or nuova_chiave_idempotenza()
        risposta = await self._invia(op.versamento_per_riferimento(ud, caricamenti, chiave))
        esito = _esito(risposta, chiave)
        esito.caricamenti = dict(caricamenti)
        return esito

    async def stato_versamento(self, pdv_id: str) -> StatoVersamento:
        return leggi_modello(StatoVersamento, await self._invia(op.stato_versamento(pdv_id)))

    async def attendi_versamento(self, pdv_id: str, *, stati: Sequence[str] | None = None,
                                 politica: PoliticaAttesa | None = None) -> StatoVersamento:
        attesi = frozenset(stati) if stati else STATI_FINALI_PDV
        return await self._attendi(lambda: self.stato_versamento(pdv_id),
                                   lambda s: s.stato in attesi, politica, f"PdV {pdv_id}")

    async def scarica_rapporto(self, pdv_id: str,
                               destinazione: str | os.PathLike[str] | None = None,
                               ) -> bytes | Path:
        """Vedi :meth:`verumdoc_sdk.VerumDoc.scarica_rapporto`."""
        risposta = await self._invia(op.rapporto_versamento(pdv_id), flusso=True)
        attesa = risposta.headers.get("X-Impronta-SHA256")
        h = hashlib.sha256()
        try:
            if destinazione is None:
                parti = []
                async for blocco in risposta.aiter_bytes():
                    h.update(blocco)
                    parti.append(blocco)
                _controlla_impronta(attesa, h.hexdigest(), f"RdV del PdV {pdv_id}")
                return b"".join(parti)
            finale = Path(destinazione)
            temporaneo = finale.with_name(f".{finale.name}.parziale")
            try:
                f = await asyncio.to_thread(temporaneo.open, "wb")
                try:
                    async for blocco in risposta.aiter_bytes():
                        h.update(blocco)
                        await asyncio.to_thread(f.write, blocco)
                finally:
                    await asyncio.to_thread(f.close)
                _controlla_impronta(attesa, h.hexdigest(), f"RdV del PdV {pdv_id}")
                await asyncio.to_thread(os.replace, temporaneo, finale)
            except BaseException:
                temporaneo.unlink(missing_ok=True)
                raise
            return finale
        finally:
            await risposta.aclose()

    async def elenca_versamenti(self, tenant_id: str | None = None, *,
                                stato: str | None = None, cursore: str | None = None,
                                limite: int = 20) -> ElencoVersamenti:
        return leggi_modello(ElencoVersamenti, await self._invia(
            op.elenco_versamenti(tenant_id, stato, cursore, limite)))

    async def itera_versamenti(self, tenant_id: str | None = None, *,
                               stato: str | None = None,
                               limite: int = 100) -> AsyncIterator[VersamentoInElenco]:
        cursore: str | None = None
        while True:
            pagina = await self.elenca_versamenti(tenant_id, stato=stato, cursore=cursore,
                                                  limite=limite)
            for voce in pagina.items:
                yield voce
            if not pagina.altri or not pagina.cursore or pagina.cursore == cursore:
                return
            cursore = pagina.cursore

    async def identita(self) -> Identita:
        return leggi_modello(Identita, await self._invia(op.identita()))

    # ── Sessioni di caricamento ───────────────────────────────────────
    async def apri_caricamento(self, nome_file: str, dimensione: int, *,
                               mime: str | None = None, impronta_sha256: str | None = None,
                               chiave_idempotenza: str | None = None,
                               ) -> tuple[SessioneCaricamento, bool]:
        risposta = await self._invia(op.apri_caricamento(
            nome_file, dimensione, mime, impronta_sha256,
            chiave_idempotenza or nuova_chiave_idempotenza()))
        return leggi_modello(SessioneCaricamento, risposta), risposta.status_code == 201

    async def stato_caricamento(self, caricamento_id: str) -> StatoCaricamento:
        return leggi_modello(StatoCaricamento,
                             await self._invia(op.stato_caricamento(caricamento_id)))

    async def invia_parte(self, caricamento_id: str, numero: int, dati: bytes) -> ParteAccettata:
        return leggi_modello(ParteAccettata,
                             await self._invia(op.invia_parte(caricamento_id, numero, dati)))

    async def completa_caricamento(self, caricamento_id: str) -> StatoCaricamento:
        return leggi_modello(StatoCaricamento,
                             await self._invia(op.completa_caricamento(caricamento_id)))

    async def annulla_caricamento(self, caricamento_id: str) -> None:
        await self._invia(op.annulla_caricamento(caricamento_id))

    async def attendi_caricamento(self, caricamento_id: str, *,
                                  politica: PoliticaAttesa | None = None) -> StatoCaricamento:
        return await self._attendi(lambda: self.stato_caricamento(caricamento_id),
                                   lambda s: s.stato in op.STATI_FINALI_CARICAMENTO, politica,
                                   f"Caricamento {caricamento_id}")

    async def carica(self, sorgente: Any, *, nome_file: str | None = None,
                     mime: str | None = None, chiave_idempotenza: str | None = None,
                     caricamento_id: str | None = None) -> CaricamentoCompletato:
        """Vedi :meth:`verumdoc_sdk.VerumDoc.carica`."""
        s = come_sorgente(sorgente, nome_file)
        nome = nome_file or s.nome
        if not nome:
            raise ValueError("Indicare nome_file")
        if caricamento_id is None:
            if s.dimensione is None:
                raise ValueError("Sorgente senza dimensione: Sorgente.da_flusso(..., dimensione=)")
            sessione, nuova = await self.apri_caricamento(
                nome, s.dimensione, mime=mime or s.mime,
                chiave_idempotenza=chiave_idempotenza or nuova_chiave_idempotenza())
            caricamento_id = sessione.caricamento_id
            if nuova:
                stato_iniziale, ricevute = "APERTO", set()
                dim_parte, totale = sessione.dimensione_parte, s.dimensione
            else:
                attuale = await self.stato_caricamento(caricamento_id)
                stato_iniziale, ricevute = attuale.stato, set(attuale.parti_ricevute)
                dim_parte, totale = attuale.dimensione_parte, attuale.dimensione
        else:
            attuale = await self.stato_caricamento(caricamento_id)
            stato_iniziale, ricevute = attuale.stato, set(attuale.parti_ricevute)
            dim_parte, totale = attuale.dimensione_parte, attuale.dimensione
        if stato_iniziale in ("SCADUTO", "ANNULLATO"):
            raise CaricamentoNonUtilizzabile(caricamento_id, stato_iniziale)
        if s.dimensione is not None and s.dimensione != totale:
            raise ValueError(f"La sessione {caricamento_id} è per {totale} byte, la sorgente "
                             f"ne ha {s.dimensione}")
        invia = stato_iniziale == "APERTO"
        sha = await self._invia_parti(s, caricamento_id, dim_parte, totale,
                                      ricevute if invia else None)
        stato = (await self.completa_caricamento(caricamento_id) if invia
                 else await self.stato_caricamento(caricamento_id))
        if stato.stato not in op.STATI_FINALI_CARICAMENTO:
            stato = await self.attendi_caricamento(caricamento_id)
        return _caricamento_finale(caricamento_id, sha, stato)

    async def _invia_parti(self, s: Sorgente, caricamento_id: str, dim_parte: int, totale: int,
                           ricevute: set[int] | None) -> str:
        lettore = LettoreParti(dim_parte)
        in_volo: set[asyncio.Task[ParteAccettata]] = set()
        numero = 0

        async def raccogli(fatti: set[asyncio.Task[ParteAccettata]]) -> None:
            for t in fatti:
                in_volo.discard(t)
                t.result()

        async def spedisci(dati: bytes) -> None:
            nonlocal numero
            numero += 1
            if len(dati) != dimensione_attesa(numero, totale, dim_parte):
                raise ErroreIntegrita(f"Parte {numero}: la sorgente non ha la dimensione "
                                      f"dichiarata ({totale} byte)")
            if ricevute is None or numero in ricevute:
                return
            while len(in_volo) >= self.concorrenza_parti:
                fatti, _ = await asyncio.wait(in_volo, return_when=asyncio.FIRST_COMPLETED)
                await raccogli(fatti)
            in_volo.add(asyncio.ensure_future(self.invia_parte(caricamento_id, numero, dati)))

        try:
            async for blocco in s.blocchi_async():
                for parte in lettore.aggiungi(blocco):
                    await spedisci(parte)
                if lettore.letti > totale:
                    raise ErroreIntegrita(f"La sorgente supera i {totale} byte dichiarati")
            resto = lettore.fine()
            if resto is not None:
                await spedisci(resto)
            if lettore.letti != totale:
                raise ErroreIntegrita(f"Letti {lettore.letti} byte, dichiarati {totale}")
            if in_volo:
                await asyncio.wait(in_volo)
                await raccogli(set(in_volo))
        except BaseException:
            for t in in_volo:
                t.cancel()
            if in_volo:
                await asyncio.gather(*in_volo, return_exceptions=True)
            raise
        return lettore.sha256.hexdigest()

    # ── Eventi ────────────────────────────────────────────────────────
    async def elenca_eventi(self, *, dopo: int = 0, limite: int | None = None,
                            tipi: Sequence[str] | None = None) -> ElencoEventi:
        return leggi_modello(ElencoEventi,
                             await self._invia(op.elenco_eventi(dopo, limite, tipi)))

    async def itera_eventi(self, *, dopo: int = 0, limite: int | None = None,
                           tipi: Sequence[str] | None = None) -> AsyncIterator[Evento]:
        """Come ``VerumDoc.itera_eventi``: il cursore è ``evento.posizione`` (o il
        ``cursore`` della pagina), non ``evento.id``."""
        while True:
            pagina = await self.elenca_eventi(dopo=dopo, limite=limite, tipi=tipi)
            for evento in pagina.eventi:
                yield evento
            if not pagina.altri or pagina.cursore == dopo:
                return
            dopo = pagina.cursore

    # ── Webhook ───────────────────────────────────────────────────────
    async def crea_webhook(self, url: str, tipi: Sequence[str], *,
                           descrizione: str | None = None, attiva: bool = True,
                           chiave_idempotenza: str | None = None) -> SottoscrizioneCreata:
        """Come :meth:`VerumDoc.crea_webhook` (Idempotency-Key, ritentativi sicuri)."""
        return leggi_modello(SottoscrizioneCreata, await self._invia(op.crea_webhook(
            url, tipi, descrizione, attiva,
            chiave_idempotenza or nuova_chiave_idempotenza())))

    async def elenca_webhook(self) -> list[Sottoscrizione]:
        return leggi_modello(ElencoSottoscrizioni,
                             await self._invia(op.elenco_webhook())).sottoscrizioni

    async def leggi_webhook(self, webhook_id: str) -> Sottoscrizione:
        return leggi_modello(Sottoscrizione, await self._invia(op.leggi_webhook(webhook_id)))

    async def modifica_webhook(self, webhook_id: str, **campi: Any) -> Sottoscrizione:
        return leggi_modello(Sottoscrizione,
                             await self._invia(op.modifica_webhook(webhook_id, campi)))

    async def elimina_webhook(self, webhook_id: str) -> None:
        await self._invia(op.elimina_webhook(webhook_id))

    async def elenca_consegne(self, webhook_id: str, *, stato: str | None = None,
                              dopo: int = 0, limite: int | None = None) -> ElencoConsegne:
        return leggi_modello(ElencoConsegne, await self._invia(
            op.elenco_consegne(webhook_id, stato, dopo, limite)))

    async def riprova_consegna(self, webhook_id: str, consegna_id: int) -> ConsegnaRiaccodata:
        return leggi_modello(ConsegnaRiaccodata,
                             await self._invia(op.riprova_consegna(webhook_id, consegna_id)))

    # ── Ricerca e distribuzione ───────────────────────────────────────
    async def cerca(self, query: str | None = None, *, classe_documentale: str | None = None,
                    anno: str | int | None = None, data_da: datetime | None = None,
                    data_a: datetime | None = None, limite: int = 20,
                    cursore: str | None = None) -> RisultatoRicerca:
        return leggi_modello(RisultatoRicerca, await self._invia(op.ricerca(
            query, classe_documentale, anno, data_da, data_a, limite, cursore)))

    async def itera_ricerca(self, query: str | None = None,
                            **filtri: Any) -> AsyncIterator[DocumentoTrovato]:
        cursore = filtri.pop("cursore", None)
        while True:
            pagina = await self.cerca(query, cursore=cursore, **filtri)
            for voce in pagina.items:
                yield voce
            if not pagina.altri or not pagina.cursore:
                return
            cursore = pagina.cursore

    async def richiedi_pdd(self, aip_id: str, tipo: TipoPdD | str, motivo: str, *,
                           file_richiesti: Sequence[str] | None = None,
                           durata_link_secondi: int | None = None,
                           consegna: ConsegnaPdD | str | None = None,
                           chiave_idempotenza: str | None = None) -> PacchettoDistribuzione:
        """Come :meth:`VerumDoc.richiedi_pdd` (Idempotency-Key, ritentativi sicuri)."""
        return leggi_modello(PacchettoDistribuzione, await self._invia(op.richiedi_pdd(
            aip_id, tipo, motivo, file_richiesti, durata_link_secondi, consegna,
            chiave_idempotenza or nuova_chiave_idempotenza())))

    async def stato_pdd(self, pdd_id: str) -> PacchettoDistribuzione:
        return leggi_modello(PacchettoDistribuzione, await self._invia(op.stato_pdd(pdd_id)))

    async def attendi_pdd(self, pdd_id: str, *,
                          politica: PoliticaAttesa | None = None) -> PacchettoDistribuzione:
        return await self._attendi(lambda: self.stato_pdd(pdd_id),
                                   lambda p: p.stato in op.STATI_FINALI_PDD, politica,
                                   f"PdD {pdd_id}")
