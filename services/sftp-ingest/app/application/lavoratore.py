# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lavoratore del canale SFTP: dalle cartelle pronte al versamento per riferimento.

Servizio: sftp-ingest. Riferimenti: piano R03 §3.2; contratti C1 (sessioni di
caricamento) e C2 (token di servizio); REGOLE §9.1 (un solo caso d'uso: il
canale traduce soltanto), §12.1 (idempotenza), §12.4 (ritardi e scarto),
§12.9 (lease), §12.11 (effetti esterni ripresi con gli id salvati).

Per ogni cartella pronta di ``in/`` (o rimasta in ``lavorazione/`` dopo un crash):
  1. lease sul database (una sola replica per cartella);
  2. ``in/<c>`` → ``lavorazione/<c>`` (rinomina atomica: il cliente non la tocca più);
  3. indice.json letto (limite di dimensione), impronta, ``Idempotency-Key`` =
     impronta di (ente, cartella, impronta dell'indice);
  4. stessa cartella con lo stesso indice già VERSATA → stesso esito, nessun
     nuovo versamento (reinvio o ripresa dopo un crash);
  5. controlli del canale (file dichiarati presenti, nessun file in più);
  6. per ogni file una sessione di caricamento (parti in parallelo, in streaming
     dal disco, ripresa delle parti mancanti), completamento e attesa della verifica;
  7. versamento per riferimento con la chiave del punto 3;
  8. esito salvato sul database, ``out/<c>/esito.json`` scritto, cartella
     spostata in ``fatti/`` o ``scarti/``.
Errori temporanei: esito IN_RIPROVA e nuovo tentativo dopo il ritardo previsto;
esauriti i tentativi, scarto con l'ultimo errore.

R03 f2 — scala (REV-15) e quote (rapporto c2, difetto 1):
  - le cartelle da guardare NON si cercano più scandendo tutte le aree a ogni
    giro su ogni replica: si guardano solo gli account SEGNALATI (dal server
    SFTP quando il cliente scrive o rinomina, ``sftp_segnalazioni``), presi in
    lease da una replica sola; le lavorazioni da riprendere (errori temporanei,
    lease scaduti) vengono dal database. Un controllo completo di sicurezza
    (``controllo_completo_secondi``, anche all'avvio) segnala tutti gli account
    una volta, distribuiti fra le repliche dallo stesso lease. Il costo di un
    giro è proporzionale agli account con attività e alle cartelle pronte;
  - in esercizio (``esegui``) le cartelle passano da una CODA a
    ``parallelismo`` lavoratori: una cartella da 5 GB non ferma il giro né le
    altre;
  - un ``Retry-After`` oltre il tetto del client (es. QTA-002 fino a
    mezzanotte) non si aspetta dentro la chiamata: la cartella si rilascia
    (IN_RIPROVA) con il prossimo tentativo non prima del ``Retry-After``; gli
    errori di quota (QTA-*) NON consumano i tentativi, fino a
    ``quota_attesa_massima_secondi`` dalla prima presa in carico.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from app.domain.protocollo import (
    FATTI,
    IN,
    LAVORAZIONE,
    NOME_INDICE,
    SCARTI,
    STATI_PDV_NEGATIVI,
    ErroreCartella,
    StatoCartella,
    StatoEsito,
    chiave_caricamento,
    chiave_idempotenza,
    controlla_contenuto,
    costruisci_esito,
    impronte_dichiarate,
    nome_cartella_valido,
    nomi_file_dichiarati,
    problema_di_quota,
    prossima_valutazione,
    ritardo_tentativo,
    valuta_cartella,
)
from app.infrastructure import archivio, repository
from app.infrastructure.gateway import ClientGateway, ErroreGateway
from app.infrastructure.repository import SCARTATA, VERSATA, Lavorazione, Segnalazione
from app.infrastructure.token_canale import EmettitoreToken
from app.metriche import MetricheSftp
from libs.shared.errori import ErroreApi

logger = structlog.get_logger(__name__)

_STATI_FINALI_NEGATIVI = frozenset({"SCADUTO", "ANNULLATO"})


@dataclass(frozen=True)
class ConfigLavoratore:
    radice: str
    intervallo_secondi: float
    parallelismo: int
    parti_parallele: int
    lease_secondi: int
    stabilita_secondi: float
    abbandono_secondi: float
    ritardi_secondi: Sequence[int]
    attesa_verifica_secondi: float
    indice_max_byte: int
    cartelle_per_giro: int
    conserva_fatti_giorni: int
    conserva_scarti_giorni: int
    conserva_esiti_giorni: int
    url_pubblico_api: str = ""
    #: Ogni quanto (e all'avvio) tutti gli account si guardano una volta: rete di
    #: sicurezza per segnalazioni perse (processo del server morto) e pulizia.
    controllo_completo_secondi: float = 3600.0
    #: Account segnalati presi in lease per giro da una replica.
    account_per_giro: int = 100
    #: Attesa massima di un pacchetto fermato dalla quota del piano (QTA-*),
    #: dalla prima presa in carico; oltre, scarto con il codice della quota.
    quota_attesa_massima_secondi: float = 72 * 3600.0

    @classmethod
    def da_impostazioni(cls, s: Any) -> ConfigLavoratore:
        return cls(
            radice=s.sftp_radice, intervallo_secondi=s.lavoratore_intervallo_secondi,
            parallelismo=s.lavoratore_parallelismo, parti_parallele=s.lavoratore_parti_parallele,
            lease_secondi=s.lavoratore_lease_secondi,
            stabilita_secondi=s.lavoratore_stabilita_secondi,
            abbandono_secondi=s.lavoratore_abbandono_ore * 3600,
            ritardi_secondi=tuple(s.lavoratore_ritardi_secondi),
            attesa_verifica_secondi=s.lavoratore_attesa_verifica_secondi,
            indice_max_byte=s.lavoratore_indice_max_byte,
            cartelle_per_giro=s.lavoratore_cartelle_per_giro,
            conserva_fatti_giorni=s.lavoratore_conserva_fatti_giorni,
            conserva_scarti_giorni=s.lavoratore_conserva_scarti_giorni,
            conserva_esiti_giorni=s.lavoratore_conserva_esiti_giorni,
            url_pubblico_api=s.url_pubblico_api,
            controllo_completo_secondi=s.lavoratore_controllo_completo_secondi,
            account_per_giro=s.lavoratore_account_per_giro,
            quota_attesa_massima_secondi=s.lavoratore_quota_attesa_massima_ore * 3600.0,
        )


class LeasePerso(Exception):  # noqa: N818 — nome di dominio
    """Un'altra replica ha preso la cartella: questa lavorazione si ferma."""


class VerificaNonConclusa(Exception):  # noqa: N818 — nome di dominio
    """La verifica di un caricamento non è finita entro il tempo previsto."""


@dataclass(frozen=True)
class _Candidata:
    account_id: uuid.UUID
    tenant_id: uuid.UUID
    cartella: str


def _adesso_utc() -> datetime:
    return datetime.now(UTC)


class Lavoratore:
    def __init__(self, config: ConfigLavoratore, *, gateway: ClientGateway,
                 emettitore: EmettitoreToken, metriche: MetricheSftp | None = None,
                 detentore: str | None = None,
                 orologio: Callable[[], float] = time.time,
                 adesso: Callable[[], datetime] = _adesso_utc) -> None:
        self.c = config
        self.gateway = gateway
        self.emettitore = emettitore
        self.metriche = metriche or MetricheSftp()
        self.detentore = detentore or f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._orologio = orologio
        self._adesso = adesso
        self._ultimo_controllo: float | None = None

    # ── Ciclo ───────────────────────────────────────────────────────────
    async def esegui(self) -> None:
        """Ciclo infinito (lifespan): raccolta delle cartelle in una coda servita da
        ``parallelismo`` lavoratori. Si raccoglie solo quando la coda ha posto,
        quindi le cartelle non restano prenotate da una replica occupata. Un
        errore non ferma il ciclo."""
        coda: asyncio.Queue[_Candidata] = asyncio.Queue()
        in_coda: set[tuple[uuid.UUID, str]] = set()

        async def servi() -> None:
            while True:
                c = await coda.get()
                try:
                    await self.lavora(c.account_id, c.tenant_id, c.cartella)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — la cartella si riprende dal lease
                    logger.error("sftp_lavorazione_non_conclusa", errore=type(exc).__name__,
                                 messaggio=str(exc)[:300])
                finally:
                    in_coda.discard((c.account_id, c.cartella))
                    coda.task_done()

        servitori = [asyncio.create_task(servi()) for _ in range(max(1, self.c.parallelismo))]
        try:
            while True:
                try:
                    posto = 2 * self.c.parallelismo - coda.qsize()
                    if posto > 0:
                        for c in await self.raccogli(posto):
                            chiave = (c.account_id, c.cartella)
                            if chiave not in in_coda:
                                in_coda.add(chiave)
                                coda.put_nowait(c)
                    self.metriche.giri.labels("ok").inc()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — il ciclo continua, errore registrato
                    self.metriche.giri.labels("errore").inc()
                    logger.error("sftp_giro_errore", errore=type(exc).__name__,
                                 messaggio=str(exc)[:300])
                await asyncio.sleep(self.c.intervallo_secondi)
        finally:
            for t in servitori:
                t.cancel()
            for t in servitori:
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    async def giro(self) -> int:
        """Un giro completo (test e uso puntuale): raccoglie e lavora tutte le
        cartelle trovate; restituisce quante ne ha prese."""
        candidate = await self.raccogli(self.c.account_per_giro)
        if not candidate:
            return 0
        semaforo = asyncio.Semaphore(self.c.parallelismo)

        async def una(c: _Candidata) -> bool:
            async with semaforo:
                return await self.lavora(c.account_id, c.tenant_id, c.cartella)

        esiti = await asyncio.gather(*(una(c) for c in candidate))
        return sum(1 for e in esiti if e)

    async def raccogli(self, limite: int) -> list[_Candidata]:
        """Cartelle da tentare, senza scandire tutte le aree:
          1. lavorazioni IN_CORSO da riprendere (dal database);
          2. se è ora, richiesta di controllo completo di tutti gli account;
          3. account segnalati presi in lease: si guarda la loro ``in/`` (e
             ``lavorazione/`` e la pulizia se è un controllo completo)."""
        visti: set[tuple[uuid.UUID, str]] = set()
        risultato: list[_Candidata] = []

        def aggiungi(c: _Candidata) -> None:
            if (c.account_id, c.cartella) not in visti:
                visti.add((c.account_id, c.cartella))
                risultato.append(c)

        for account_id, tenant_id, cartella in await repository.lavorazioni_da_riprendere(
                max(1, limite)):
            aggiungi(_Candidata(account_id, tenant_id, cartella))
        adesso = self._orologio()
        if (self._ultimo_controllo is None
                or adesso - self._ultimo_controllo >= self.c.controllo_completo_secondi):
            self._ultimo_controllo = adesso
            segnalati = await repository.richiedi_controllo_completo()
            logger.info("sftp_controllo_completo_richiesto", account=segnalati)
        segnalazioni = await repository.prendi_segnalazioni(
            self.detentore, max(1, min(limite, self.c.account_per_giro)), self.c.lease_secondi)
        for seg in segnalazioni:
            trovate, attesa = await asyncio.to_thread(self._esamina_account, seg)
            for c in trovate:
                aggiungi(c)
            # Cartelle consegnate al lavoro: si ricontrolla dopo un lease (se una
            # replica muore prima di prenderle, l'account torna da guardare).
            if trovate:
                attesa = min(attesa, float(self.c.lease_secondi)) if attesa is not None \
                    else float(self.c.lease_secondi)
            await repository.rilascia_segnalazione(seg, self.detentore,
                                                   riguarda_fra_secondi=attesa)
        self.metriche.account_esaminati.inc(len(segnalazioni))
        return risultato

    def _esamina_account(self, seg: Segnalazione) -> tuple[list[_Candidata], float | None]:
        """(cartelle da tentare, secondi dopo cui riguardare l'account o None) per
        un account segnalato (thread). Solo ``in/``; con il controllo completo
        anche ``lavorazione/`` e la pulizia di fatti/, scarti/, out/."""
        area = archivio.area_account(self.c.radice, str(seg.account_id))
        archivio.assicura_area(area)
        visti: set[str] = set()
        risultato: list[_Candidata] = []
        if seg.controllo_completo:
            for nome, _ in archivio.sottocartelle(area / LAVORAZIONE, self.c.cartelle_per_giro):
                visti.add(nome)
                risultato.append(_Candidata(seg.account_id, seg.tenant_id, nome))
            self._pulizia_area(area)
        adesso = self._orologio()
        attesa: float | None = None
        presenti = archivio.sottocartelle(area / IN, self.c.cartelle_per_giro + 1)
        if len(presenti) > self.c.cartelle_per_giro:
            attesa = 1.0  # più cartelle del limite per giro: si torna subito
        for nome, mtime in presenti[: self.c.cartelle_per_giro]:
            if nome in visti or not nome_cartella_valido(nome):
                continue  # «.tmp» del cliente: la rinomina finale segnala di nuovo
            try:
                voci = archivio.voci(area / IN / nome)
                stato = valuta_cartella(
                    voci, modificata_il=mtime, adesso=adesso,
                    stabilita_secondi=self.c.stabilita_secondi,
                    abbandono_secondi=self.c.abbandono_secondi)
            except FileNotFoundError:
                continue
            if stato is StatoCartella.IN_CARICAMENTO:
                fra = prossima_valutazione(
                    voci, modificata_il=mtime, adesso=adesso,
                    stabilita_secondi=self.c.stabilita_secondi,
                    abbandono_secondi=self.c.abbandono_secondi)
                attesa = fra if attesa is None else min(attesa, fra)
            else:
                risultato.append(_Candidata(seg.account_id, seg.tenant_id, nome))
        return risultato, attesa

    def _pulizia_area(self, area: Path) -> None:
        adesso = self._orologio()
        for sotto, giorni in ((FATTI, self.c.conserva_fatti_giorni),
                              (SCARTI, self.c.conserva_scarti_giorni),
                              ("out", self.c.conserva_esiti_giorni)):
            archivio.pulisci(area / sotto, adesso - giorni * 86400)

    # ── Una cartella ───────────────────────────────────────────────────
    async def lavora(self, account_id: uuid.UUID, tenant_id: uuid.UUID, cartella: str) -> bool:
        """Lavora una cartella se ottiene il lease; ``False`` se era di un'altra replica."""
        area = archivio.area_account(self.c.radice, str(account_id))
        if not await asyncio.to_thread(_presente, area, cartella):
            return False  # già lavorata da un'altra replica dopo la scansione
        lav = await repository.prendi_in_carico(tenant_id, account_id, cartella,
                                                self.detentore, self.c.lease_secondi)
        if lav is None:
            return False
        inizio = self._orologio()
        self.metriche.lavorazioni_in_corso.inc()
        log = logger.bind(lavorazione_id=str(lav.id), tenant_id=str(tenant_id),
                          account_id=str(account_id), cartella=cartella)
        try:
            if not await self._con_lease(lav, self._lavora(lav, log)):
                return False
        except LeasePerso:
            log.warning("sftp_lease_perso")
        except asyncio.CancelledError:
            log.warning("sftp_lavorazione_interrotta")
            raise
        finally:
            self.metriche.lavorazioni_in_corso.dec()
            self.metriche.durata.observe(self._orologio() - inizio)
        return True

    async def _con_lease(self, lav: Lavorazione, lavoro: Coroutine[Any, Any, bool]) -> bool:
        """Esegue ``lavoro`` rinnovando il lease a un terzo della durata; se il
        rinnovo fallisce la lavorazione si interrompe (LeasePerso)."""
        compito = asyncio.ensure_future(lavoro)
        intervallo = max(1.0, self.c.lease_secondi / 3)
        try:
            while True:
                fatto, _ = await asyncio.wait({compito}, timeout=intervallo)
                if fatto:
                    return compito.result()
                if not await repository.rinnova_lease(lav.id, self.detentore,
                                                      self.c.lease_secondi):
                    compito.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await compito
                    raise LeasePerso()
        finally:
            if not compito.done():
                compito.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await compito

    async def _lavora(self, lav: Lavorazione, log: Any) -> bool:
        """``False`` se la cartella non c'era più (nessun lavoro fatto)."""
        area = archivio.area_account(self.c.radice, str(lav.account_id))
        in_lavorazione = area / LAVORAZIONE / lav.cartella
        try:
            presente = await asyncio.to_thread(self._porta_in_lavorazione, area, lav.cartella)
            if not presente:
                # Lavorata da un'altra replica dopo la scansione, o eliminata dal
                # cliente prima della presa in carico: la presa si annulla.
                await repository.annulla_presa(lav.id, self.detentore)
                log.info("sftp_cartella_non_presente")
                return False
            await self._versa_cartella(lav, area, in_lavorazione, log)
        except ErroreCartella as exc:
            problema = ErroreApi(exc.codice, exc.dettaglio, dettagli=exc.dettagli).corpo()
            await self._scarta(lav, area, [problema], log)
        except ErroreGateway as exc:
            if exc.ritentabile:
                await self._riprova_o_scarta(lav, area, exc.problema, log,
                                             retry_after=exc.retry_after)
            else:
                await self._scarta(lav, area, [exc.problema], log)
        except VerificaNonConclusa as exc:
            problema = ErroreApi("SFTP-202", str(exc)).corpo()
            await self._riprova_o_scarta(lav, area, problema, log)
        except (LeasePerso, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 — errore inatteso: si riprova, poi scarto
            log.error("sftp_lavorazione_errore", errore=type(exc).__name__,
                      messaggio=str(exc)[:300])
            problema = ErroreApi("SFTP-500", "Errore interno durante la lavorazione",
                                 dettagli={"lavorazione_id": str(lav.id)}).corpo()
            await self._riprova_o_scarta(lav, area, problema, log)
        return True

    @staticmethod
    def _porta_in_lavorazione(area: Path, cartella: str) -> bool:
        """``in/<c>`` → ``lavorazione/<c>``; se ``lavorazione/<c>`` esiste già (ripresa)
        si lavora quella. ``False`` se la cartella non c'è più."""
        destinazione = area / LAVORAZIONE / cartella
        if destinazione.is_dir():
            return True
        try:
            archivio.sposta(area / IN / cartella, destinazione)
        except FileNotFoundError:
            return False
        return True

    async def _leggi_indice(self, cartella: Path) -> tuple[dict[str, Any], str]:
        percorso = cartella / NOME_INDICE
        try:
            dati = await asyncio.to_thread(archivio.leggi_limitato, percorso,
                                           self.c.indice_max_byte)
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise ErroreCartella("SFTP-101", "indice.json assente nella cartella") from exc
        if dati is None:
            raise ErroreCartella("SFTP-102", "indice.json supera la dimensione massima",
                                 {"massimo_byte": self.c.indice_max_byte})
        impronta = hashlib.sha256(dati).hexdigest()
        try:
            indice = json.loads(dati.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ErroreCartella("SFTP-102", "indice.json non è JSON valido in UTF-8") from exc
        if not isinstance(indice, dict):
            raise ErroreCartella("SFTP-102", "indice.json deve essere un oggetto JSON")
        return indice, impronta

    async def _versa_cartella(self, lav: Lavorazione, area: Path, cartella: Path,
                              log: Any) -> None:
        indice, impronta = await self._leggi_indice(cartella)
        chiave = chiave_idempotenza(str(lav.tenant_id), lav.cartella, impronta)
        if lav.impronta_indice not in (None, impronta):
            # stessa cartella ma indice diverso dal tentativo precedente: si riparte
            lav.caricamenti = {}
            await self._salva(lav, impronta, chiave, azzera=True)
        lav.impronta_indice, lav.chiave_idempotenza = impronta, chiave

        lavoro = await repository.account_per_lavoro(lav.account_id)
        if lavoro is None:  # account eliminato a mano: non dovrebbe accadere (RESTRICT)
            raise ErroreCartella("SFTP-105", "Account SFTP non più presente")
        token = self.emettitore.token(tenant_id=lavoro.tenant_id,
                                      user_id=lavoro.responsabile_id, ruolo=lavoro.ruolo)

        precedente = await repository.esito_versato(lav.account_id, lav.cartella, impronta)
        if precedente is not None:
            # R03 i3 (richiesta 1 di f1): l'esito locale si riusa solo dopo aver
            # chiesto al gateway lo stato ATTUALE del PdV. Un PdV poi rifiutato o
            # annullato non è «VERSATA»: si risponde come il gateway (409 VRS-127).
            stato_attuale = await self._stato_pdv_precedente(token, precedente)
            if stato_attuale is not None and stato_attuale in STATI_PDV_NEGATIVI:
                pdv_id = str(precedente.get("pdv_id"))
                link = f"/api/v1/versamento/{pdv_id}"
                raise ErroreCartella("VRS-127", (
                    f"La cartella è già stata versata come PdV {pdv_id}, ora nello stato "
                    f"{stato_attuale}: non è conservato. Per versare di nuovo si corregge "
                    "il contenuto (indice o file)."),
                    {"pdv_id_esistente": pdv_id, "stato_esistente": stato_attuale,
                     "link": link})
            if stato_attuale is not None:
                esito = {**precedente, "stato_pdv": stato_attuale, "reinvio": True,
                         "lavorazione_id": str(lav.id), "tentativi": lav.tentativi,
                         "elaborato_il": self._adesso().isoformat()}
                await self._concludi(lav, area, VERSATA, precedente.get("pdv_id"), esito,
                                     FATTI, "REINVIO", log)
                return
            # PdV non più trovato dal gateway: si versa di nuovo (il gateway decide)

        dichiarati = nomi_file_dichiarati(indice)
        voci = await asyncio.to_thread(archivio.voci, cartella)
        da_caricare = controlla_contenuto(voci, dichiarati) if dichiarati is not None else []
        await self._salva(lav, impronta, chiave)

        impronte = impronte_dichiarate(indice)
        mappa: dict[str, str] = {}
        for nome in da_caricare:
            mappa[nome] = await self._carica_file(lav, token, cartella / nome, nome,
                                                  impronte.get(nome), impronta, chiave)
        status, corpo = await self.gateway.versa(token, indice=indice, file=mappa, chiave=chiave)
        esito = costruisci_esito(
            cartella=lav.cartella, stato=StatoEsito.VERSATO, lavorazione_id=str(lav.id),
            adesso=self._adesso(), tentativi=lav.tentativi, pdv_id=corpo.get("pdv_id"),
            stato_pdv=corpo.get("stato"), avvisi=corpo.get("avvisi") or [],
            link_stato=corpo.get("link_stato"), chiave=chiave, reinvio=status == 200,
            url_pubblico_api=self.c.url_pubblico_api)
        await self._concludi(lav, area, VERSATA, corpo.get("pdv_id"), esito, FATTI,
                             "REINVIO" if status == 200 else "VERSATO", log)

    async def _stato_pdv_precedente(self, token: str,
                                    precedente: dict[str, Any]) -> str | None:
        """Stato attuale del PdV di un esito VERSATA precedente; ``None`` se il PdV
        non c'è più (404) o l'esito non ha un pdv_id. Gli altri errori del gateway
        si propagano (ritentabili: nuovo tentativo della cartella)."""
        pdv_id = precedente.get("pdv_id")
        if not pdv_id:
            return None
        try:
            corpo = await self.gateway.stato_pdv(token, str(pdv_id))
        except ErroreGateway as exc:
            if exc.status == 404:
                return None
            raise
        stato = corpo.get("stato")
        return str(stato) if stato else None

    async def _salva(self, lav: Lavorazione, impronta: str, chiave: str,
                     azzera: bool = False) -> None:
        if not await repository.aggiorna_lavoro(
                lav.id, self.detentore, impronta_indice=impronta, chiave_idempotenza=chiave,
                caricamenti=lav.caricamenti, azzera_tentativi=azzera):
            raise LeasePerso()
        if azzera:
            lav.tentativi = 1

    # ── Sessioni di caricamento (contratto C1) ─────────────────────────
    async def _carica_file(self, lav: Lavorazione, token: str, percorso: Path, nome: str,
                           impronta: dict[str, str] | None, impronta_indice: str,
                           chiave: str) -> str:
        """Carica un file e attende la verifica; restituisce il caricamento_id COMPLETO."""
        dimensione = (await asyncio.to_thread(os.stat, percorso)).st_size
        info = lav.caricamenti.get(nome) or {}
        generazione = int(info.get("gen", 0))
        stato: dict[str, Any] | None = None
        if info.get("id"):
            try:
                stato = await self.gateway.stato_caricamento(token, info["id"])
            except ErroreGateway as exc:
                if exc.status != 404:
                    raise
                stato = None
            if stato is not None and stato.get("stato") in _STATI_FINALI_NEGATIVI:
                stato = None
            if stato is not None and stato.get("stato") == "ERRORE":
                problema = stato.get("errore") or {}
                if problema.get("ritentabile") is not True:
                    raise ErroreGateway(problema or ErroreApi(
                        "SFTP-201", "Caricamento in errore senza motivo").corpo(), status=None)
                stato = None
        if stato is None:
            generazione += 1
            stato = await self.gateway.apri_caricamento(
                token, nome_file=nome, dimensione=dimensione, impronta=impronta,
                chiave=chiave_caricamento(str(lav.id), nome, impronta_indice, generazione))
            # dimensione e numero delle parti li dà solo l'apertura: si salvano per la ripresa
            lav.caricamenti[nome] = {
                "id": str(stato["caricamento_id"]), "gen": generazione,
                "dimensione_parte": stato.get("dimensione_parte"),
                "numero_parti": stato.get("numero_parti"),
            }
            await self._salva(lav, impronta_indice, chiave)
            if stato.get("stato") == "APERTO":
                # la risposta di apertura non elenca le parti già ricevute (ripresa)
                stato = {**stato, **await self.gateway.stato_caricamento(
                    token, str(stato["caricamento_id"]))}
        caricamento_id = str(stato.get("caricamento_id") or lav.caricamenti[nome]["id"])
        if stato.get("stato") == "APERTO":
            await self._invia_parti(token, caricamento_id, percorso, dimensione, stato,
                                    lav.caricamenti[nome])
            stato = await self.gateway.completa(token, caricamento_id)
        if stato.get("stato") != "COMPLETO":
            stato = await self._attendi_verifica(token, caricamento_id)
        return caricamento_id

    async def _invia_parti(self, token: str, caricamento_id: str, percorso: Path,
                           dimensione: int, stato: dict[str, Any], info: dict[str, Any]) -> None:
        dimensione_parte = int(stato.get("dimensione_parte") or info.get("dimensione_parte") or 0)
        numero_parti = int(stato.get("numero_parti") or info.get("numero_parti") or 0)
        if dimensione_parte <= 0 and dimensione > 0:
            raise ErroreGateway(ErroreApi("SFTP-201", "Sessione di caricamento senza "
                                          "dimensione delle parti").corpo(), status=None)
        ricevute = {int(n) for n in stato.get("parti_ricevute") or []}
        mancanti = [n for n in range(1, numero_parti + 1) if n not in ricevute]
        semaforo = asyncio.Semaphore(self.c.parti_parallele)

        async def parte(n: int) -> None:
            inizio = (n - 1) * dimensione_parte
            lunghezza = max(0, min(dimensione_parte, dimensione - inizio))
            async with semaforo:
                await self.gateway.carica_parte(token, caricamento_id, n, percorso=percorso,
                                                inizio=inizio, lunghezza=lunghezza)
            self.metriche.byte_caricati.inc(lunghezza)

        await asyncio.gather(*(parte(n) for n in mancanti))

    async def _attendi_verifica(self, token: str, caricamento_id: str) -> dict[str, Any]:
        scadenza = self._orologio() + self.c.attesa_verifica_secondi
        attesa = 0.2
        while True:
            stato = await self.gateway.stato_caricamento(token, caricamento_id)
            valore = stato.get("stato")
            if valore == "COMPLETO":
                return stato
            if valore == "ERRORE":
                problema = stato.get("errore") or ErroreApi(
                    "SFTP-201", "Caricamento in errore senza motivo").corpo()
                raise ErroreGateway(problema, status=None)
            if valore in _STATI_FINALI_NEGATIVI:
                raise ErroreGateway(ErroreApi(
                    "SFTP-202", f"Sessione di caricamento {valore}").corpo(), status=None)
            if self._orologio() >= scadenza:
                raise VerificaNonConclusa(
                    f"Verifica del caricamento {caricamento_id} non conclusa in "
                    f"{int(self.c.attesa_verifica_secondi)} s")
            await asyncio.sleep(attesa)
            attesa = min(attesa * 2, 5.0)

    # ── Conclusione ────────────────────────────────────────────────────
    async def _concludi(self, lav: Lavorazione, area: Path, stato_db: str, pdv_id: Any,
                        esito: dict[str, Any], verso: str, etichetta: str, log: Any) -> None:
        """esito.json e spostamento SOTTO lease, poi esito sul database.

        Finché la riga è IN_CORSO nessun'altra replica può prendere la cartella,
        quindi lo spostamento non è mai conteso. Un crash fra spostamento e
        database lascia la riga IN_CORSO con lease scaduto e il cliente con
        l'esito corretto; un reinvio della cartella riusa la stessa riga e la
        stessa ``Idempotency-Key`` (il gateway risponde con lo stesso PdV)."""
        if not await repository.rinnova_lease(lav.id, self.detentore, self.c.lease_secondi):
            raise LeasePerso()
        await asyncio.to_thread(self._scrivi_e_sposta, area, lav.cartella, esito, verso)
        if not await repository.concludi(lav.id, self.detentore, stato_db,
                                         str(pdv_id) if pdv_id else None, esito):
            log.warning("sftp_conclusione_senza_lease", esito=etichetta)
        self.metriche.cartelle.labels(etichetta).inc()
        log.info("sftp_cartella_conclusa", esito=etichetta, pdv_id=pdv_id)

    def _scrivi_e_sposta(self, area: Path, cartella: str, esito: dict[str, Any],
                         verso: str) -> None:
        archivio.scrivi_esito(area, cartella, esito)
        sorgente = area / LAVORAZIONE / cartella
        if sorgente.is_dir():
            archivio.sposta(sorgente, archivio.destinazione_libera(area / verso, cartella,
                                                                   self._adesso()))

    async def _scarta(self, lav: Lavorazione, area: Path, errori: list[dict[str, Any]],
                      log: Any) -> None:
        esito = costruisci_esito(
            cartella=lav.cartella, stato=StatoEsito.SCARTATO, lavorazione_id=str(lav.id),
            adesso=self._adesso(), tentativi=lav.tentativi, errori=errori,
            chiave=lav.chiave_idempotenza, url_pubblico_api=self.c.url_pubblico_api)
        await self._concludi(lav, area, SCARTATA, None, esito, SCARTI, "SCARTATO", log)

    async def _riprova_o_scarta(self, lav: Lavorazione, area: Path, problema: dict[str, Any],
                                log: Any, *, retry_after: float | None = None) -> None:
        """Errore temporaneo: nuovo tentativo dopo il ritardo previsto (e non prima
        del ``Retry-After`` del gateway), oppure scarto a tentativi esauriti.
        Una quota del piano esaurita (QTA-*) non consuma tentativi finché non
        supera ``quota_attesa_massima_secondi`` dalla prima presa in carico."""
        adesso = self._adesso()
        quota = problema_di_quota(problema)
        if quota:
            inizio = lav.creata_il or adesso
            atteso = (adesso - inizio).total_seconds() + float(retry_after or 0)
            ritardo = None if atteso > self.c.quota_attesa_massima_secondi \
                else max(1, int(retry_after or self.c.ritardi_secondi[0]))
        else:
            ritardo = ritardo_tentativo(lav.tentativi, self.c.ritardi_secondi)
            if ritardo is not None and retry_after:
                ritardo = max(ritardo, int(retry_after + 0.999))
        if ritardo is None:
            await self._scarta(lav, area, [problema], log)
            return
        esito = costruisci_esito(
            cartella=lav.cartella, stato=StatoEsito.IN_RIPROVA, lavorazione_id=str(lav.id),
            adesso=adesso, tentativi=lav.tentativi, errori=[problema],
            chiave=lav.chiave_idempotenza,
            prossimo_tentativo=adesso + timedelta(seconds=ritardo),
            url_pubblico_api=self.c.url_pubblico_api)
        if not await repository.rimanda(lav.id, self.detentore, ritardo, problema,
                                        consuma_tentativo=not quota):
            raise LeasePerso()
        await asyncio.to_thread(archivio.scrivi_esito, area, lav.cartella, esito)
        self.metriche.cartelle.labels("IN_RIPROVA").inc()
        log.info("sftp_cartella_in_riprova", ritardo_secondi=ritardo,
                 codice=problema.get("codice"), tentativi=lav.tentativi)


def _presente(area: Path, cartella: str) -> bool:
    return (area / IN / cartella).is_dir() or (area / LAVORAZIONE / cartella).is_dir()
