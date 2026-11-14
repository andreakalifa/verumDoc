# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/lavoratore.py — esecuzione delle pratiche fuori dalla richiesta.

Riferimenti: REGOLE_SVILUPPO §12.9 (operazioni lunghe uniche: lease con
scadenza; lease scaduto = tentativo fallito), §12.4 (ritardi come lista
costante), §12.11 (effetti esterni dopo il commit, ripresa idempotente),
§15.8 (ogni esecuzione lascia una riga append-only), §22.

Una pratica con ``lavoro`` non nullo e ``prossimo_tentativo`` passato è presa
da UNA replica (``FOR UPDATE SKIP LOCKED`` + lease in tabella: compatibile con
PgBouncer in modalità transaction, nessun lock di sessione). Il passo del tipo
di pratica si ripete finché c'è lavoro, rinnovando il lease a ogni passo; un
lease non rinnovabile (preso da un'altra replica dopo la scadenza) ferma il
giro. Errori temporanei (registro, gateway, distribution, storage, database):
nuovo tentativo dopo ``LAVORATORE_RITARDI_SECONDI`` (l'ultimo ritardo si ripete:
la pratica non si perde mai e l'errore resta visibile in ``esecuzione``).
"""
from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.application import autorizzazioni, conferme, puntuali, riversamento, scarto
from app.application import lease as lease_mod
from app.application import pratiche as pr
from app.config import settings
from app.domain import stati as st
from app.infrastructure import repository as repo
from app.infrastructure.ambito import ambito_lavoratore
from app.infrastructure.distribution import ClientDistribution, ErroreDistribution, Esportatore
from app.infrastructure.gateway import ClientGateway, ErroreGateway, Versatore
from app.infrastructure.registro import ClientRegistro, ErroreRegistro, RegistroImpronte
from libs.shared.firme import ErroreVerificatoreNonDisponibile
from libs.shared.storage.worm import ErroreStorageConservativo

logger = structlog.get_logger(__name__)

#: R08: anche il verificatore delle firme non raggiungibile (si ritenta: una
#: firma non diventa valida né non valida perché un servizio è giù)
ERRORI_TEMPORANEI = (ErroreRegistro, ErroreGateway, ErroreDistribution,
                     ErroreStorageConservativo, ClientError, BotoCoreError, SQLAlchemyError,
                     OSError, TimeoutError, ErroreVerificatoreNonDisponibile)


@dataclass
class Dipendenze:
    """Sistemi esterni del lavoratore (iniettabili nei test)."""

    registro: RegistroImpronte = field(default_factory=ClientRegistro)
    versatore: Versatore = field(default_factory=ClientGateway)
    esportatore: Esportatore = field(default_factory=ClientDistribution)


def detentore_predefinito() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


_PRENDI = text(
    "UPDATE public.pratiche p SET lease_detentore = :d, lease_scade_il = :scade, "  # noqa: S608 — frammenti costanti del modulo
    "tentativi = p.tentativi + CASE WHEN p.lease_detentore IS NOT NULL THEN 1 ELSE 0 END, "
    "updated_at = :adesso "
    "WHERE p.id = (SELECT id FROM public.pratiche WHERE lavoro IS NOT NULL "
    "  AND prossimo_tentativo <= :adesso "
    "  AND (lease_scade_il IS NULL OR lease_scade_il < :adesso) "
    "  ORDER BY prossimo_tentativo, id FOR UPDATE SKIP LOCKED LIMIT 1) "
    f"RETURNING {repo.COLONNE_PRATICA}")


async def prendi_pratica(detentore: str, adesso: datetime) -> dict[str, Any] | None:
    """Prende in esclusiva (lease) la prossima pratica con lavoro dovuto. Un
    lease scaduto di un altro detentore conta come tentativo fallito."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        riga = (await session.execute(_PRENDI, {
            "d": detentore, "adesso": adesso,
            "scade": adesso + timedelta(seconds=settings.lavoratore_lease_secondi)}
        )).mappings().first()
    return dict(riga) if riga else None


async def rinnova_lease(pratica_id: uuid.UUID, detentore: str, adesso: datetime) -> bool:
    return await lease_mod.rinnova(pratica_id, detentore, adesso)


async def _rilascia(pratica: Mapping[str, Any], detentore: str, *, adesso: datetime,
                    avvio: datetime, esito: str, errore: Mapping[str, Any] | None,
                    tentativo: int, prossimo: datetime | None,
                    sospendi: bool = False, azzera: bool = False) -> None:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        campi: dict[str, Any] = {"lease_detentore": None, "lease_scade_il": None}
        if sospendi:
            campi["lavoro"] = None  # R14 (VD-C-133): stato di errore esplicito
        if azzera and errore is None:
            campi["tentativi"] = 0  # revisione R14 (5): la presa ha fatto progressi
        if errore is not None:
            campi["ultimo_errore"] = dict(errore)
            campi["tentativi"] = tentativo + 1
            if prossimo is not None:
                campi["prossimo_tentativo"] = prossimo
        r = await session.execute(text(
            "SELECT lease_detentore, lavoro FROM public.pratiche WHERE id = :id FOR UPDATE"),
            {"id": pratica["id"]})
        attuale = r.mappings().first()
        if attuale is None or attuale["lease_detentore"] != detentore:
            return  # lease perso: l'altra replica registra il suo tentativo
        await repo.aggiorna_pratica(session, pratica["id"], adesso, **campi)
        if sospendi:
            # R17 (VD-P-164): fatto del lavoratore, la ripresa richiederà una conferma
            await conferme.registra_sospensione(session, pratica)
        elif errore is None and attuale["lavoro"] is None and pratica.get("lavoro"):
            # revisione R17: il giro è concluso; un giro nuovo vuole una conferma nuova
            await conferme.registra_fatto(session, pratica, "GIRO_CHIUSO")
        await session.execute(text(
            "INSERT INTO public.pratiche_esecuzioni (pratica_id, tenant_id, lavoro, detentore, "
            "tentativo, avviata_il, terminata_il, esito, conteggi, errore) VALUES (:p, :t, :l, "
            ":d, :n, :avvio, :fine, :esito, CAST(:c AS jsonb), CAST(:e AS jsonb))"),
            {"p": pratica["id"], "t": pratica["tenant_id"], "l": pratica["lavoro"],
             "d": detentore, "n": tentativo, "avvio": avvio, "fine": adesso, "esito": esito,
             "c": _json(await repo.conteggi_oggetti(session, pratica["id"])),
             "e": None if errore is None else _json(dict(errore))})


def _json(valore: Any) -> str:
    import json

    return json.dumps(valore, default=str)


Passo = Callable[[Mapping[str, Any], Dipendenze, datetime], Awaitable[bool]]


async def passo(pratica: Mapping[str, Any], dip: Dipendenze, adesso: datetime) -> bool:
    """Instradamento per tipo e lavoro. ``True`` = altro lavoro subito."""
    tipo, lavoro = pratica["tipo"], pratica["lavoro"]
    if tipo == st.SCARTO and lavoro == pr.LAVORO_ESEGUI:
        return await scarto.esegui_passo(pratica, dip.registro, adesso=adesso,
                                         lotto=settings.lavoratore_lotto)
    if tipo == st.SCARTO and lavoro == st.LAVORO_CHIUDI:
        return await scarto.chiudi_passo(pratica, dip.registro, adesso=adesso,
                                         lotto=settings.lavoratore_lotto)
    if tipo == st.ANNULLAMENTO and lavoro == st.LAVORO_ANNULLA:
        return await puntuali.passo_applica_annullamento(pratica, adesso=adesso)
    if tipo == st.ANNULLAMENTO and lavoro == puntuali.LAVORO_REGISTRO:
        return await puntuali.passo_annullamento(pratica, dip.registro, adesso=adesso)
    if tipo == st.RETTIFICA and lavoro in (puntuali.LAVORO_VERSA, puntuali.LAVORO_ATTENDI):
        return await puntuali.passo_rettifica(pratica, dip.registro, dip.versatore,
                                              adesso=adesso)
    if tipo in (st.RIVERSAMENTO_USCITA, st.RESTITUZIONE) and lavoro == pr.LAVORO_ESEGUI:
        return await riversamento.passo_uscita(pratica, dip.esportatore, adesso=adesso)
    if tipo == st.RIVERSAMENTO_INGRESSO and lavoro == riversamento.LAVORO_ACQUISISCI:
        return await riversamento.passo_acquisizione(pratica, adesso=adesso)
    if tipo == st.RIVERSAMENTO_INGRESSO and lavoro == pr.LAVORO_ESEGUI:
        return await riversamento.passo_ingresso(pratica, dip.versatore, adesso=adesso)
    raise RuntimeError(f"lavoro {lavoro!r} non previsto per una pratica di tipo {tipo}")


def _codice_errore(exc: BaseException) -> str:
    codice = getattr(exc, "codice", None)
    if isinstance(codice, str) and codice:
        return codice
    problema = getattr(exc, "problema", None)
    if isinstance(problema, dict) and problema.get("codice"):
        return str(problema["codice"])
    return "LC-503-LAVORATORE"


def tentativi_esauriti(tentativo: int, massimo: int | None = None) -> bool:
    """R14 (VD-C-133): ``True`` se il tentativo fallito ``tentativo`` (contato da 0)
    è l'ultimo ammesso; ``LAVORATORE_TENTATIVI_MASSIMI`` = 0: mai (illimitati)."""
    limite = settings.lavoratore_tentativi_massimi if massimo is None else massimo
    return limite > 0 and tentativo + 1 >= limite


async def _sospendi(pratica: Mapping[str, Any], detentore: str, errore: Mapping[str, Any], *,
                    avvio: datetime, tentativo: int, adesso: datetime) -> str:
    """R14 (VD-C-133): stato di errore ESPLICITO dopo i tentativi esauriti. Il lavoro
    si toglie (nessun nuovo tentativo automatico: niente ritentativi all'infinito su
    un errore persistente) e resta scritto in ``ultimo_errore.lavoro_sospeso`` per la
    ripresa manuale (runbook ``pratica_sospesa.md``); ciò che è già stato fatto
    (cancellazioni, foglie, atti) resta registrato e la ripresa è idempotente.
    Allarme PraticaSospesa (metrica ``verumdoc_ciclo_vita_pratiche_sospese``)."""
    sospeso = {"codice": st.CODICE_TENTATIVI_ESAURITI,
               "messaggio": (f"Lavoro {pratica['lavoro']} sospeso dopo {tentativo + 1} "
                             "tentativi falliti; ultimo errore: "
                             f"{errore.get('messaggio') or errore.get('codice')}")[:500],
               "lavoro_sospeso": pratica["lavoro"], "tentativi": tentativo + 1,
               "ultimo": dict(errore), "il": adesso.isoformat()}
    logger.error("pratica_sospesa_tentativi_esauriti", pratica_id=str(pratica["id"]),
                 tipo=pratica["tipo"], lavoro=pratica["lavoro"], tentativi=tentativo + 1,
                 codice=errore.get("codice"),
                 azione="correggere la causa e riprendere (docs/runbooks/pratica_sospesa.md)")
    await _rilascia(pratica, detentore, adesso=adesso, avvio=avvio, esito="ERRORE",
                    errore=sospeso, tentativo=tentativo, prossimo=None, sospendi=True)
    return "SOSPESO"


async def _rinvia(pratica: Mapping[str, Any], detentore: str, exc: BaseException, *,
                 avvio: datetime, tentativo: int, orologio: Callable[[], datetime]) -> str:
    """Errore temporaneo: nuovo tentativo dopo ``LAVORATORE_RITARDI_SECONDI``; al
    tentativo ``LAVORATORE_TENTATIVI_MASSIMI`` la pratica si sospende (R14)."""
    ritardi = settings.lavoratore_ritardi_secondi or [60]
    attesa = ritardi[min(tentativo, len(ritardi) - 1)]
    adesso = orologio()
    errore = {"codice": _codice_errore(exc), "messaggio": str(exc)[:500],
              "tipo": type(exc).__name__, "il": adesso.isoformat()}
    if tentativi_esauriti(tentativo):
        return await _sospendi(pratica, detentore, errore, avvio=avvio, tentativo=tentativo,
                               adesso=adesso)
    logger.warning("pratica_passo_rinviato", pratica_id=str(pratica["id"]),
                   tipo=pratica["tipo"], codice=errore["codice"], attesa_secondi=attesa)
    await _rilascia(pratica, detentore, adesso=adesso, avvio=avvio, esito="RINVIATO",
                    errore=errore, tentativo=tentativo,
                    prossimo=adesso + timedelta(seconds=attesa))
    return "RINVIATO"


async def _bloccata(pratica: Mapping[str, Any], detentore: str,
                    exc: autorizzazioni.AutorizzazioniNonVerificate, *, avvio: datetime,
                    tentativo: int, orologio: Callable[[], datetime]) -> str:
    """Revisione 4 di R05 (U2): catena delle autorizzazioni dello scarto non
    verificata. La pratica è già senza lavoro (``autorizzazioni.blocca``): si
    registra l'esecuzione come ERRORE, senza nuovo tentativo."""
    adesso = orologio()
    await _rilascia(pratica, detentore, adesso=adesso, avvio=avvio, esito="ERRORE",
                    errore={"codice": exc.codice, "messaggio": str(exc)[:500],
                            "difetti": exc.difetti[:20], "il": adesso.isoformat()},
                    tentativo=tentativo, prossimo=None)
    return "BLOCCATO"


async def esegui_pratica(pratica: dict[str, Any], dip: Dipendenze, detentore: str, *,
                         orologio: Callable[[], datetime] = pr.adesso_utc,
                         massimo_passi: int = 10_000) -> str:
    """Esegue i passi di una pratica presa in lease; restituisce l'esito
    registrato (COMPLETATO, PARZIALE, RINVIATO, ERRORE).

    Revisione R05: un'esecuzione (``lavoro = ESEGUI``) si marca AVVIATA con la
    pratica bloccata prima del primo passo (R1: da lì non si respinge più); il
    lease si rinnova anche dentro i passi lunghi (R11, ``app.application.lease``)."""
    avvio = orologio()
    tentativo = int(pratica["tentativi"])
    massimo = settings.lavoratore_tentativi_massimi
    if massimo > 0 and tentativo >= massimo:
        # R14 (VD-C-133): tentativi esauriti per lease scaduti (processo interrotto a
        # metà passo più volte: memoria, arresto): stessa sospensione esplicita
        adesso = orologio()
        return await _sospendi(pratica, detentore, {
            "codice": "LC-500-LAVORATORE", "tipo": "LeaseScaduto",
            "messaggio": "lease scaduto ripetutamente: il processo si è interrotto a metà passo",
            "il": adesso.isoformat()}, avvio=avvio, tentativo=tentativo - 1, adesso=adesso)
    # R17 (VD-P-164): conferme firmate dal gateway delle decisioni che hanno chiesto questo
    # lavoro, verificate con le chiavi pubbliche della configurazione PRIMA di ogni effetto
    try:
        await conferme.assicura(pratica, orologio())
    except autorizzazioni.AutorizzazioniNonVerificate as exc:
        return await _bloccata(pratica, detentore, exc, avvio=avvio, tentativo=tentativo,
                               orologio=orologio)
    except ERRORI_TEMPORANEI as exc:
        return await _rinvia(pratica, detentore, exc, avvio=avvio, tentativo=tentativo,
                             orologio=orologio)
    if autorizzazioni.richiede_verifica(pratica) and pratica["lavoro"] == pr.LAVORO_ESEGUI:
        # Revisione 4 di R05 (U2): la catena delle autorizzazioni si verifica dal
        # lavoratore PRIMA dell'avvio (e di ogni cancellazione), non dallo stato
        # che l'API ha scritto nella pratica; R12b (VD-C-160): anche prima di
        # esportare una restituzione
        try:
            await autorizzazioni.assicura(pratica, orologio())
        except autorizzazioni.AutorizzazioniNonVerificate as exc:
            return await _bloccata(pratica, detentore, exc, avvio=avvio, tentativo=tentativo,
                                   orologio=orologio)
        except ERRORI_TEMPORANEI as exc:
            return await _rinvia(pratica, detentore, exc, avvio=avvio, tentativo=tentativo,
                                 orologio=orologio)
    if pratica["lavoro"] == pr.LAVORO_ESEGUI:
        avviata = await pr.segna_avvio_esecuzione(pratica["id"], orologio())
        if avviata is None:
            # respinta prima che il lavoratore la prendesse: nulla da eseguire
            await _rilascia(pratica, detentore, adesso=orologio(), avvio=avvio,
                            esito="COMPLETATO", errore=None, tentativo=tentativo, prossimo=None)
            return "COMPLETATO"
        pratica = {**pratica, **avviata}
    # revisione R14 (5): passi riusciti in questa presa. Con almeno un passo riuscito il
    # lavoro ha fatto progressi: i tentativi falliti ripartono da zero (la sospensione
    # riguarda un lavoro che fallisce SEMPRE, non uno lungo che avanza a fatica)
    passi_riusciti = 0
    try:
        with lease_mod.lease_attivo(pratica["id"], detentore, orologio):
            for _ in range(massimo_passi):
                adesso = orologio()
                altro = await passo(pratica, dip, adesso)
                passi_riusciti += 1
                if not altro:
                    break
                if not await rinnova_lease(pratica["id"], detentore, orologio()):
                    logger.warning("pratica_lease_perso", pratica_id=str(pratica["id"]))
                    return "RINVIATO"
                from libs.shared.db import get_async_session

                async with get_async_session() as session:
                    ricaricata = await repo.leggi_pratica(session, pratica["id"])
                if ricaricata is None or ricaricata["lavoro"] is None:
                    break
                pratica = ricaricata
            else:
                await _rilascia(pratica, detentore, adesso=orologio(), avvio=avvio,
                                esito="PARZIALE", errore=None, tentativo=tentativo,
                                prossimo=None, azzera=True)
                return "PARZIALE"
    except lease_mod.LeasePerso:
        # un'altra replica ha preso la pratica: il passo si è fermato prima del
        # prossimo elemento; il tentativo lo registra chi ha il lease
        logger.warning("pratica_lease_perso_nel_passo", pratica_id=str(pratica["id"]))
        return "RINVIATO"
    except autorizzazioni.AutorizzazioniNonVerificate as exc:
        return await _bloccata(pratica, detentore, exc, avvio=avvio, tentativo=tentativo,
                               orologio=orologio)
    except ERRORI_TEMPORANEI as exc:
        return await _rinvia(pratica, detentore, exc, avvio=avvio,
                             tentativo=0 if passi_riusciti else tentativo, orologio=orologio)
    except Exception as exc:  # noqa: BLE001 — difetto: registrato, la pratica si riprova
        if passi_riusciti:
            tentativo = 0
        ritardi = settings.lavoratore_ritardi_secondi or [60]
        adesso = orologio()
        logger.exception("pratica_passo_errore", pratica_id=str(pratica["id"]),
                         tipo=pratica["tipo"])
        errore = {"codice": "LC-500-LAVORATORE", "tipo": type(exc).__name__,
                  "messaggio": str(exc)[:500], "il": adesso.isoformat()}
        if tentativi_esauriti(tentativo):
            return await _sospendi(pratica, detentore, errore, avvio=avvio,
                                   tentativo=tentativo, adesso=adesso)
        await _rilascia(pratica, detentore, adesso=adesso, avvio=avvio, esito="ERRORE",
                        errore=errore, tentativo=tentativo,
                        prossimo=adesso + timedelta(seconds=ritardi[-1]))
        return "ERRORE"
    await _rilascia(pratica, detentore, adesso=orologio(), avvio=avvio, esito="COMPLETATO",
                    errore=None, tentativo=tentativo, prossimo=None,
                    azzera=passi_riusciti > 0)
    return "COMPLETATO"


async def giro(dip: Dipendenze, detentore: str, *,
               orologio: Callable[[], datetime] = pr.adesso_utc) -> str | None:
    """Prende ed esegue al più UNA pratica; ``None`` se non c'è lavoro."""
    with ambito_lavoratore("lavoratore ciclo-vita: pratiche di tutti gli enti"):
        pratica = await prendi_pratica(detentore, orologio())
        if pratica is None:
            return None
        return await esegui_pratica(pratica, dip, detentore, orologio=orologio)


async def giro_periodico(dip: Dipendenze, detentore: str, *,
                         orologio: Callable[[], datetime] = pr.adesso_utc) -> dict[str, Any]:
    """R09: passi periodici dovuti (proposte, riprese, minimizzazione, rinnovo)."""
    from app.application import periodico

    with ambito_lavoratore("lavoratore ciclo-vita: lavoro periodico di tutti gli enti"):
        return await periodico.giro(dip.registro, detentore, orologio())


async def recupero_r08() -> int:
    """Revisione R09 (W3): recupero idempotente all'avvio (vedi
    ``puntuali.recupera_annullamenti_r08``)."""
    with ambito_lavoratore("lavoratore ciclo-vita: annullamenti R08 in coda all'avvio"):
        return await puntuali.recupera_annullamenti_r08()


async def ciclo(fine: asyncio.Event, dip: Dipendenze | None = None) -> None:
    import time

    dip = dip or Dipendenze()
    detentore = detentore_predefinito()
    logger.info("ciclo_vita_lavoratore_avviato", detentore=detentore)
    prossimo_periodico = 0.0
    recupero_fatto = False
    while not fine.is_set():
        if not recupero_fatto:
            # revisione R09 (W3): annullamenti R08 in coda al cambio di versione
            try:
                await recupero_r08()
                recupero_fatto = True
            except Exception as exc:  # noqa: BLE001 — database giù: si riprova
                logger.error("ciclo_vita_recupero_r08_errore", errore=type(exc).__name__)
        if settings.periodico_attivo and time.monotonic() >= prossimo_periodico:
            prossimo_periodico = time.monotonic() + settings.periodico_controllo_secondi
            try:
                await giro_periodico(dip, detentore)
            except Exception as exc:  # noqa: BLE001 — database giù: si riprova al prossimo
                logger.error("ciclo_vita_periodico_errore", errore=type(exc).__name__)
        try:
            esito = await giro(dip, detentore)
        except Exception as exc:  # noqa: BLE001 — database momentaneamente giù: si riprova
            logger.error("ciclo_vita_lavoratore_errore", errore=type(exc).__name__)
            esito = None
        if esito is None:
            try:
                await asyncio.wait_for(fine.wait(), timeout=settings.lavoratore_intervallo_secondi)
            except TimeoutError:
                pass


__all__ = ["Dipendenze", "ciclo", "detentore_predefinito", "esegui_pratica", "giro",
           "giro_periodico", "passo", "recupero_r08",
           "prendi_pratica", "rinnova_lease"]
