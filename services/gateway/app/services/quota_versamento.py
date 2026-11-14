# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Quota di PdV al giorno nel caso d'uso unico del versamento (VD-C-099, R03 i1).

Servizio: gateway. Riferimenti: piano R03–R05 §4.2; REGOLE_SVILUPPO §8.1, §19.8;
``libs.shared.security.quote`` (logica e impostazioni, agente b4).

Perché qui e non nelle rotte: ogni canale che crea un PdV (REST multipart, REST
per riferimento, PDND, SFTP che passa dal REST, ciclo-vita)
chiama ``versamento_service.versa``. Applicare la quota nel caso d'uso:
  - la rende valida per ogni canale presente e futuro, senza che una rotta
    possa dimenticarla;
  - permette di controllarla DOPO la ricerca idempotente: la stessa richiesta
    ripetuta (stessa Idempotency-Key, PdV già creato) riceve lo stesso PdV
    (200) anche a quota esaurita, invece di un 429 che un client in ritentativo
    non saprebbe come risolvere;
  - conta un PdV solo quando è davvero nuovo (``EsitoVersamento.nuovo``).

R03 f1 (REV-02): controllo e conteggio sono UNA operazione atomica su Redis
(``prenota_quota_versamento``, script Lua) fatta prima del trasferimento dei
byte; se il PdV poi non nasce (rifiuto, ripetizione trovata nella transazione,
errore) il posto si rilascia (``rilascia_quota_versamento``). Con N richieste
parallele dello stesso ente il limite non si supera mai.

Il piano si legge dall'identità se il canale lo conosce (``user["piano"]``,
già letto dal database da ``get_current_user``), altrimenti dall'ente nel
database (``limiti_ente``, stessa cache), come per PDND. R03 i3: la quota
personalizzata dell'ente (``QUOTA_PDV_GIORNO`` al livello ENTE) prevale sul piano.

R07 (D13): limiti dei piani dalla console; oltre la quota l'ente può scegliere
il rallentamento invece del rifiuto (``prenota_pdv(rallenta_secondi=…)``).
Avvisi automatici all'ente (evento ``quota.soglia_raggiunta`` + e-mail) quando il
conteggio del giorno raggiunge l'80% e il 100%: un filtro Redis (SET NX per
ente, giorno e soglia) evita di toccare il database a ogni versamento dopo la
soglia; il database (``avvisi_automatici``) garantisce l'avviso unico. Un errore
nell'avviso non ferma mai il versamento (si registra nel log).

Redis non raggiungibile: fail-open con avviso nel log, come la quota al minuto
(decisione b4, confermata per coerenza in f1: la quota protegge la capacità, non
i dati; un guasto di Redis non deve fermare i versamenti della PA). Se Redis
cade fra prenotazione e rilascio il posto resta occupato fino a mezzanotte: a
sfavore del cliente, mai oltre il limite. Il fornitore del client Redis è sostituibile
(``imposta_fornitore_redis``): i test lo usano per un Redis in memoria o per
escludere le quote (``None``).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import structlog

from libs.shared.security.quote import (
    LimitiEnte,
    PrenotazionePdv,
    QuotaSuperata,
    limiti_ente,
    prenota_pdv,
    registra_pdv_versato,
    rilascia_pdv,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.services.versamento_service import IdentitaVersante

logger = structlog.get_logger(__name__)


def _redis_del_gateway() -> Any:
    from app.auth.dependencies import get_redis

    return get_redis()


_fornitore_redis: Callable[[], Any] = _redis_del_gateway


def imposta_fornitore_redis(fornitore: Callable[[], Any]) -> Callable[[], Any]:
    """Sostituisce il fornitore del client Redis; restituisce il precedente."""
    global _fornitore_redis
    precedente = _fornitore_redis
    _fornitore_redis = fornitore
    return precedente


def _impostazioni() -> Any:
    from app.auth.rate_limiter import IMPOSTAZIONI_QUOTE

    return IMPOSTAZIONI_QUOTE


async def _limiti(identita: IdentitaVersante) -> LimitiEnte:
    """Piano e quota personalizzata dell'ente (stessa lettura e cache di
    ``get_current_user``). Il piano dell'identità, se c'è, è quello già letto."""
    limiti = await limiti_ente(identita.tenant_id, _impostazioni())
    if identita.piano and identita.piano != limiti.piano:
        # piano diverso da quello letto: i limiti del piano dalla console non valgono
        return replace(limiti, piano=identita.piano, richieste_minuto_piano=None,
                       pdv_giorno_piano=None)
    return limiti


async def prenota_quota_versamento(identita: IdentitaVersante,
                                   adesso: datetime) -> PrenotazionePdv | None:
    """Prende un posto della quota di PdV del giorno (atomico, R03 f1 REV-02).

    429 QTA-002 (``ErroreApi`` con Retry-After fino a mezzanotte) se l'ente ha già
    versato o sta versando i PdV del suo piano. Restituisce la prenotazione da
    rilasciare con ``rilascia_quota_versamento`` se il PdV poi non nasce; None
    senza Redis (fail-open con avviso nel log)."""
    redis = _fornitore_redis()
    if redis is None:
        return None
    imp = _impostazioni()
    limiti = await _limiti(identita)
    limite = limiti.pdv_giorno(imp)
    try:
        prenotazione = await prenota_pdv(
            redis, str(identita.tenant_id), limite=limite, piano=limiti.piano,
            fuso=imp.fuso_giornata, adesso=adesso, rallenta_secondi=limiti.rallenta_secondi)
    except QuotaSuperata as exc:
        await avvisa_soglie(redis, identita, limiti, limite, limite, adesso)
        raise exc.errore_api() from None
    if prenotazione is not None:
        await avvisa_soglie(redis, identita, limiti, prenotazione.totale, limite, adesso)
    return prenotazione


def soglie_raggiunte(totale: int, limite: int) -> list[int]:
    """Soglie d'avviso (percentuali) raggiunte con ``totale`` PdV su ``limite``."""
    from libs.verumdoc_rules.parametri_servizio import SOGLIE_AVVISO_QUOTA

    if limite <= 0:
        return []
    return [s for s in SOGLIE_AVVISO_QUOTA if totale >= math.ceil(limite * s / 100)]


def piano_suggerito(piano: str, totale: int, limiti_piani: dict[str, int]) -> str | None:
    """Il primo piano (in ordine) con un limite giornaliero sopra il consumo."""
    from libs.verumdoc_rules.parametri_servizio import PIANI

    for p in PIANI:
        if p != piano and limiti_piani.get(p, 0) > totale and \
                PIANI.index(p) > (PIANI.index(piano) if piano in PIANI else -1):
            return p
    return None


async def avvisa_soglie(redis: Any, identita: IdentitaVersante, limiti: LimitiEnte,
                        totale: int, limite: int, adesso: datetime) -> None:
    """D13: avviso all'ente al primo versamento che raggiunge l'80% e il 100%."""
    from libs.shared.security.quote import ambiente_piani

    imp = _impostazioni()
    giorno = adesso.astimezone(ZoneInfo(imp.fuso_giornata)).date().isoformat()
    tid = str(identita.tenant_id)
    for soglia in soglie_raggiunte(totale, limite):
        filtro = f"qta:avviso:{tid}:{giorno}:{soglia}"
        try:
            if not await redis.set(filtro, "1", nx=True, ex=2 * 86400):
                continue
        except Exception as exc:  # noqa: BLE001 — l'avviso non ferma il versamento
            logger.warning(
                "quota_avviso_non_verificabile", tenant_id=tid, errore=type(exc).__name__)
            return
        piani = {p.split("_")[2].lower(): v for p, v in ambiente_piani(imp).items()
                 if p.endswith("_PDV_GIORNO")}
        if limiti.pdv_giorno_piano is not None:
            piani[limiti.piano] = limiti.pdv_giorno_piano
        dati = {"quota": "PDV_GIORNO", "soglia": soglia, "limite": limite, "utilizzo": totale,
                "giorno": giorno, "piano": limiti.piano,
                "piano_suggerito": piano_suggerito(limiti.piano, totale, piani),
                "effetto": ("rallentamento" if limiti.rallenta_secondi else "rifiuto")
                if soglia >= 100 else None}
        try:
            await _registra_avviso_quota(tid, f"{giorno}:PDV:{soglia}", dati)
        except Exception as exc:  # noqa: BLE001
            logger.error("quota_avviso_non_registrato", tenant_id=tid, soglia=soglia,
                         errore=type(exc).__name__, messaggio=str(exc)[:200])
            try:
                await redis.delete(filtro)  # si riprova al prossimo versamento
            except Exception as exc:  # noqa: BLE001
                logger.warning("quota_filtro_non_rimosso", errore=type(exc).__name__)


async def _registra_avviso_quota(tid: str, chiave: str, dati: dict[str, Any]) -> None:
    from libs.shared.avvisi import TIPO_QUOTA_SOGLIA, registra_avviso
    from libs.shared.db import get_async_session
    from libs.shared.security.ambito_dati import ambito_ente

    with ambito_ente(tid):
        async with get_async_session() as session:
            await registra_avviso(session, tipo=TIPO_QUOTA_SOGLIA, tenant_id=tid, chiave=chiave,
                                  dati=dati)


async def rilascia_quota_versamento(prenotazione: PrenotazionePdv | None) -> None:
    """Restituisce il posto di un versamento che non ha creato un PdV nuovo."""
    if prenotazione is None:
        return
    await rilascia_pdv(_fornitore_redis(), prenotazione)


async def registra_pdv_versamento(identita: IdentitaVersante, adesso: datetime) -> int | None:
    """Conta un PdV nuovo SENZA prenotazione (compatibilità: il caso d'uso usa la
    prenotazione atomica). None senza Redis."""
    redis = _fornitore_redis()
    if redis is None:
        return None
    return await registra_pdv_versato(redis, str(identita.tenant_id),
                                      fuso=_impostazioni().fuso_giornata, adesso=adesso)


__all__ = [
    "avvisa_soglie", "imposta_fornitore_redis", "piano_suggerito", "soglie_raggiunte",
    "prenota_quota_versamento", "registra_pdv_versamento",
           "rilascia_quota_versamento"]
