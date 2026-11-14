# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/application/webhook_service.py
Gestione delle sottoscrizioni webhook — contratto C3.

Regole (in un solo punto, usate dall'API e dagli adattatori che la chiamano):
  - URL validato con ``valida_url`` (https, niente credenziali, SSRF);
  - tipi solo dal catalogo degli eventi, senza ripetizioni, in ordine stabile;
  - segreto generato qui, cifrato a riposo legato a ente e sottoscrizione,
    restituito al chiamante UNA volta (mai salvato in chiaro, mai nei log);
  - limite di sottoscrizioni per ente da impostazione;
  - ogni scrittura in audit (REGOLE §15.1) senza segreto e senza URL completo
    (la query può contenere dati del cliente): solo l'host.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import structlog

from app.domain.eventi import tipi_ammessi, tipi_sconosciuti
from app.domain.identita import Identita
from app.domain.webhook import UrlNonValido, genera_segreto, suffisso_segreto, valida_url
from app.infrastructure.cifratura import Cifratore, contesto_segreto
from app.infrastructure.repo_webhook import (
    LimiteSottoscrizioni,
    NellaTransazione,
    RepositoryWebhook,
    Sottoscrizione,
)
from libs.shared.errori import ErroreApi

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RegoleWebhook:
    lunghezza_massima_url: int
    consenti_privati: bool
    massimo_per_ente: int
    porte_ammesse: frozenset[int] | None = frozenset()


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def normalizza_tipi(tipi: list[str]) -> list[str]:
    sconosciuti = tipi_sconosciuti(tipi)
    if sconosciuti:
        raise ErroreApi("WH-003", f"Tipi sconosciuti: {', '.join(sconosciuti)}",
                        dettagli={"sconosciuti": sconosciuti, "ammessi": tipi_ammessi()})
    return sorted(set(tipi))


async def porte_ente(tenant_id: Any, globali: frozenset[int]) -> frozenset[int] | None:
    """R07 (D16): porte ammesse per i webhook dell'ente.

    ``globali`` (``WEBHOOK_PORTE_AMMESSE``) vuoto = nessun limite di porta (solo
    sviluppo, come prima); altrimenti la 443 sempre, le porte globali e
    ``WEBHOOK_PORTE_AGGIUNTIVE`` (impostazione della console: valore dell'ente o,
    senza, del conservatore; la cambia solo il conservatore, con il motivo della
    bozza). La NetworkPolicy di uscita di notification deve ammettere le stesse
    porte (docs/integrazione/webhook.md)."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import (
        PORTA_WEBHOOK_SEMPRE,
        WEBHOOK_PORTE_AGGIUNTIVE,
    )

    if not globali:
        return None
    aggiuntive = (await parametri([WEBHOOK_PORTE_AGGIUNTIVE], tenant_id))[
        WEBHOOK_PORTE_AGGIUNTIVE]
    return frozenset({PORTA_WEBHOOK_SEMPRE, *globali, *(int(p) for p in aggiuntive)})


async def regole_per_ente(regole: RegoleWebhook, tenant_id: Any) -> RegoleWebhook:
    """Regole con le porte dell'ente (D16)."""
    return replace(regole, porte_ammesse=await porte_ente(tenant_id, regole.porte_ammesse))


def controlla_url(url: str, regole: RegoleWebhook) -> str:
    try:
        return valida_url(url, lunghezza_massima=regole.lunghezza_massima_url,
                          consenti_privati=regole.consenti_privati,
                          porte_ammesse=regole.porte_ammesse).url
    except UrlNonValido as exc:
        raise ErroreApi(exc.codice, exc.messaggio) from None


async def _audit(evento: str, identita: Identita, dettagli: dict[str, Any]) -> None:
    from libs.shared.security.audit import log_audit  # registra da sé i propri errori

    await log_audit(evento=evento, tenant_id=identita.tenant_id, user_id=identita.user_id,
                    dettagli=dettagli)


async def crea(identita: Identita, *, url: str, tipi: list[str], descrizione: str | None,
               attiva: bool, repo: RepositoryWebhook, cifratore: Cifratore,
               regole: RegoleWebhook,
               nella_transazione: NellaTransazione | None = None) -> tuple[Sottoscrizione, str]:
    """(sottoscrizione, segreto in chiaro da mostrare una volta).

    ``nella_transazione(sessione, id)``: lavoro del chiamante nella STESSA
    transazione dell'INSERT (R04: completamento della Idempotency-Key, così non
    esiste mai un webhook creato con la chiave ancora IN_CORSO)."""
    tipi_ok = normalizza_tipi(tipi)
    url_ok = controlla_url(url, await regole_per_ente(regole, identita.tenant_id))
    id_ = str(uuid.uuid4())
    segreto = genera_segreto()
    try:
        creata = await repo.crea(
            id_=id_, tenant_id=identita.tenant_id, url=url_ok, tipi=tipi_ok,
            descrizione=descrizione, attiva=attiva,
            segreto_cifrato=cifratore.cifra(segreto, contesto_segreto(identita.tenant_id, id_)),
            segreto_suffisso=suffisso_segreto(segreto), creata_da=identita.user_id,
            massimo=regole.massimo_per_ente, nella_transazione=nella_transazione)
    except LimiteSottoscrizioni:
        raise ErroreApi("WH-004", f"Massimo {regole.massimo_per_ente} webhook per ente",
                        dettagli={"massimo": regole.massimo_per_ente}) from None
    await _audit("webhook.creato", identita, {
        "sottoscrizione_id": id_, "host": _host(url_ok), "tipi": tipi_ok, "attiva": attiva})
    logger.info("webhook_creato", tenant_id=identita.tenant_id, sottoscrizione_id=id_,
                host=_host(url_ok))
    return creata, segreto


async def modifica(identita: Identita, id_: str, campi: dict[str, Any], *,
                   repo: RepositoryWebhook, regole: RegoleWebhook) -> Sottoscrizione:
    modifiche = dict(campi)
    if "tipi" in modifiche:
        modifiche["tipi"] = normalizza_tipi(modifiche["tipi"])
    if "url" in modifiche:
        modifiche["url"] = controlla_url(modifiche["url"],
                                         await regole_per_ente(regole, identita.tenant_id))
    esito = await repo.aggiorna(identita.tenant_id, id_, modifiche)
    if esito is None:
        raise ErroreApi("WH-404")
    prima, dopo = esito
    await _audit("webhook.modificato", identita, {
        "sottoscrizione_id": id_, "campi": sorted(modifiche),
        "prima": {"host": _host(prima.url), "tipi": prima.tipi, "attiva": prima.attiva},
        "dopo": {"host": _host(dopo.url), "tipi": dopo.tipi, "attiva": dopo.attiva},
    })
    return dopo


async def elimina(identita: Identita, id_: str, *, repo: RepositoryWebhook) -> None:
    annullate = await repo.elimina(identita.tenant_id, id_)
    if annullate is None:
        raise ErroreApi("WH-404")
    await _audit("webhook.eliminato", identita, {
        "sottoscrizione_id": id_, "consegne_annullate": annullate})


async def ruota_segreto(identita: Identita, id_: str, *, transizione_ore: int,
                        repo: RepositoryWebhook, cifratore: Cifratore,
                        adesso: datetime | None = None) -> tuple[Sottoscrizione, str]:
    """R13b: nuovo segreto (mostrato una volta) e il precedente valido ancora per
    ``transizione_ore``: nel frattempo ogni consegna porta due firme ``v1``. Una
    seconda rotazione durante la transizione rende «precedente» il segreto appena
    dato (il più vecchio smette di valere)."""
    segreto = genera_segreto()
    scade = (adesso or datetime.now(UTC)) + timedelta(hours=transizione_ore)
    ruotata = await repo.ruota_segreto(
        identita.tenant_id, id_, cifrato=cifratore.cifra(segreto, contesto_segreto(
            identita.tenant_id, id_)), suffisso=suffisso_segreto(segreto), scade_il=scade)
    if ruotata is None:
        raise ErroreApi("WH-404")
    await _audit("webhook.segreto_ruotato", identita, {
        "sottoscrizione_id": id_, "suffisso_nuovo": ruotata.segreto_suffisso,
        "suffisso_precedente": ruotata.segreto_precedente_suffisso,
        "transizione_fino_al": scade.isoformat()})
    logger.info("webhook_segreto_ruotato", tenant_id=identita.tenant_id, sottoscrizione_id=id_,
                transizione_ore=transizione_ore)
    return ruotata, segreto


async def chiudi_transizione(identita: Identita, id_: str, *,
                             repo: RepositoryWebhook) -> Sottoscrizione:
    """R13b: ritira subito il segreto precedente (il cliente ha già aggiornato)."""
    esito, s = await repo.chiudi_transizione(identita.tenant_id, id_)
    if esito == "non_trovata" or s is None:
        raise ErroreApi("WH-404")
    if esito == "nessuna":
        raise ErroreApi("WH-013")
    await _audit("webhook.transizione_chiusa", identita, {"sottoscrizione_id": id_})
    return s


async def riprova(identita: Identita, id_: str, consegna_id: int, *,
                  repo: RepositoryWebhook) -> dict[str, Any]:
    esito, dati = await repo.riprova(identita.tenant_id, id_, consegna_id)
    if esito == "non_trovata":
        raise ErroreApi("WH-010")
    if esito == "stato":
        raise ErroreApi("WH-011", f"La consegna è {dati['stato'] if dati else '?'}",
                        dettagli=dati or {})
    if esito == "oltre_retention":
        raise ErroreApi("WH-012", dettagli=dati or {})
    await _audit("webhook.consegna_riprovata", identita, {
        "sottoscrizione_id": id_, "consegna_id": consegna_id})
    return dati or {}
