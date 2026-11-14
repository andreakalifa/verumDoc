# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/services/elaborazione.py
Dagli eventi di dominio ai messaggi in coda — VD-C-024.

Ogni evento produce messaggi IN_CODA per i destinatari reali, oppure una riga
NON_PARTITA con il motivo (nessun destinatario, canale non disponibile).
Nessun invio avviene qui: lo fa invio.py, con ritentativi.

Cliente obbligatorio: ogni riga di public.notifications appartiene a un cliente.
Il cliente si prende dall'evento (``tenant_id``) oppure si risolve dall'oggetto
a cui l'evento si riferisce (``pdv_id`` / ``aip_id`` → tenant_id nel DB). Se
non si risolve, o il cliente non esiste, l'evento NON si inserisce: si solleva
``ScartoEvento`` e il consumer lo manda subito sulla DLQ con il motivo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from app.services.coda import CodaUscita, NuovoMessaggio
from app.services.destinatari import Destinatari, LettoreCliente, destinatari, leggi_cliente
from app.services.smtp import indirizzo_plausibile
from app.services.templates import get_template

logger = structlog.get_logger(__name__)

TOPIC_EVENTI = ("pdv.invalid", "aip.conserved",
                # R07: avvisi automatici all'ente (D13, D17) e scadenza dello staging (D15)
                "quota.soglia_raggiunta", "idempotenza.chiave_omessa", "versamento.staging_scaduto",
                # R09: avvisi del ciclo di vita (D20, D21, D23)
                "ciclo_vita.avviso",
                # R10 (D32): allarmi d'integrità, per posta solo a chi la sceglie
                "integrita.anomalia")
CANALI_NON_DISPONIBILI = {
    "pec": "canale PEC non disponibile: ricevute di accettazione e consegna non gestite",
    "webhook": "canale webhook non disponibile come messaggio esplicito: usare le "
               "sottoscrizioni firmate di /api/v1/webhook (eventi per i clienti)",
}


class ScartoEvento(Exception):
    """Evento non notificabile per un motivo che un nuovo tentativo non cambia:
    va sulla DLQ con ``motivo`` (nessuna riga in coda)."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


#: (pdv_id, aip_id) → tenant_id dell'oggetto nel DB, o None
LettoreTenantOggetto = Callable[[str, str], Awaitable[str | None]]


async def leggi_tenant_oggetto(pdv_id: str, aip_id: str) -> str | None:
    """Cliente del PdV o del PdA a cui l'evento si riferisce (dal DB).

    R05 (VD-C-080): l'ente è proprio ciò che si cerca, quindi la lettura avviene
    nell'ambito conservatore (consumer degli eventi della pipeline)."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session
    from libs.shared.db.ambito import ambito_conservatore

    with ambito_conservatore("notifiche: ente del PdV o del PdA dell'evento", registra=False):
        return await _leggi_tenant_oggetto(get_async_session, text, pdv_id, aip_id)


async def _leggi_tenant_oggetto(get_async_session: Any, text: Any, pdv_id: str,
                                aip_id: str) -> str | None:
    async with get_async_session() as session:
        for tabella, valore in (("pdv", pdv_id), ("aip", aip_id)):
            if not tenant_o_vuoto(valore):
                continue
            riga = (await session.execute(
                text(f"SELECT tenant_id::text FROM public.{tabella} "  # noqa: S608 — costante
                     "WHERE id = CAST(:id AS uuid)"),
                {"id": str(UUID(str(valore)))},
            )).scalar_one_or_none()
            if riga:
                return str(riga)
    return None


async def risolvi_cliente(
    evento: dict[str, Any],
    *,
    lettore: LettoreCliente,
    lettore_oggetto: LettoreTenantOggetto,
) -> tuple[str, dict]:
    """(tenant_id, dati del cliente) per l'evento; ``ScartoEvento`` se impossibile."""
    tenant_id = tenant_o_vuoto(evento.get("tenant_id"))
    if not tenant_id:
        trovato = await lettore_oggetto(str(evento.get("pdv_id") or ""),
                                        str(evento.get("aip_id") or ""))
        tenant_id = tenant_o_vuoto(trovato)
    if not tenant_id:
        raise ScartoEvento(
            "cliente non risolvibile: evento senza tenant_id valido e senza pdv_id/aip_id "
            "di un oggetto esistente"
        )
    cliente = await lettore(tenant_id)
    if cliente is None:
        raise ScartoEvento(f"cliente inesistente: tenant_id={tenant_id}")
    return tenant_id, cliente


def tenant_o_vuoto(valore: Any) -> str:
    """UUID del cliente normalizzato, o stringa vuota se assente/non valido."""
    try:
        return str(UUID(str(valore)))
    except (ValueError, TypeError):
        return ""


def contenuto_evento(topic: str, evento: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """(oggetto_id, oggetto, testo, html) per l'evento, o None se non notificabile."""
    if topic == "pdv.invalid":
        pdv_id = str(evento.get("pdv_id") or "")
        errori = [
            r.get("msg")
            for r in evento.get("risultati") or []
            if not r.get("valido") and r.get("msg")
        ]
        t = get_template(
            "pdv.errore",
            {
                "pdv_id": pdv_id,
                "messaggio": "; ".join(errori[:3]) or "vedi dettaglio in dashboard",
            },
        )
        return pdv_id, t.oggetto, t.corpo_testo, t.corpo_html
    if topic == "aip.conserved":
        aip_id = str(evento.get("aip_id") or "")
        t = get_template("aip.conserved", {"urn": evento.get("urn") or "", "aip_id": aip_id})
        return aip_id, t.oggetto, t.corpo_testo, t.corpo_html
    if topic in ("quota.soglia_raggiunta", "idempotenza.chiave_omessa",
                 "versamento.staging_scaduto", "ciclo_vita.avviso"):
        oggetto_id = str(evento.get("avviso_id") or evento.get("pdv_id") or "")
        t = get_template(topic, {k: "" if v is None else str(v) for k, v in evento.items()
                                 if isinstance(v, (str, int, float)) or v is None})
        return oggetto_id, t.oggetto, t.corpo_testo, t.corpo_html
    if topic == "integrita.anomalia":
        aip_id = str(evento.get("aip_id") or "")
        oggetto = "[VerumDoc] Anomalia d'integrità su un pacchetto"
        testo = ("La verifica periodica dell'integrità ha trovato un'anomalia sul pacchetto di "
                 f"archiviazione {aip_id}. Il conservatore è già stato avvisato e gestisce il "
                 "ripristino; il dettaglio è nella console.")
        return aip_id, oggetto, testo, None
    return None


async def accoda_evento(
    topic: str,
    evento: dict[str, Any],
    coda: CodaUscita,
    *,
    adesso: datetime,
    lettore: LettoreCliente = leggi_cliente,
    lettore_oggetto: LettoreTenantOggetto = leggi_tenant_oggetto,
    destinatari_per_utente: Any = None,
) -> int:
    """Accoda le notifiche di un evento di pipeline. Restituisce i messaggi accodati."""
    if destinatari_per_utente is None:
        destinatari_per_utente = _destinatari_utenti
    contenuto = contenuto_evento(topic, evento)
    if contenuto is None:
        logger.info("evento_senza_notifica", topic=topic)
        return 0
    oggetto_id, oggetto, testo, html = contenuto
    if not oggetto_id:
        raise ScartoEvento(f"evento {topic} senza identificativo dell'oggetto (pdv_id/aip_id)")
    tenant_id, cliente = await risolvi_cliente(
        evento, lettore=lettore, lettore_oggetto=lettore_oggetto,
    )

    async def _cliente_letto(_tid: str) -> dict | None:
        return cliente

    dest: Destinatari = await destinatari(tenant_id, topic, lettore=_cliente_letto)
    # R10 (D32): le persone che hanno scelto l'e-mail per la categoria (o obbligata
    # dall'ente), oltre al referente; per integrita.anomalia solo loro
    persone = await destinatari_per_utente(tenant_id, topic, evento)
    if persone:
        dest = Destinatari(indirizzi=list(dict.fromkeys([*dest.indirizzi, *persone])))
    if not dest.indirizzi:
        await coda.registra_non_partita(
            NuovoMessaggio(
                tenant_id=tenant_id,
                tipo=topic,
                oggetto_id=oggetto_id,
                destinatario="",
                oggetto=oggetto,
                testo=testo,
                html=html,
            ),
            dest.motivo or "nessun destinatario",
            adesso,
        )
        logger.info("notifica_non_partita", topic=topic, tenant_id=tenant_id, motivo=dest.motivo)
        return 0
    accodati = 0
    for indirizzo in dest.indirizzi:
        nuovo = await coda.accoda(
            NuovoMessaggio(
                tenant_id=tenant_id,
                tipo=topic,
                oggetto_id=oggetto_id,
                destinatario=indirizzo,
                oggetto=oggetto,
                testo=testo,
                html=html,
            ),
            adesso,
        )
        accodati += int(nuovo)
    logger.info(
        "notifica_accodata",
        topic=topic,
        tenant_id=tenant_id,
        accodati=accodati,
        duplicati=len(dest.indirizzi) - accodati,
    )
    return accodati


async def _destinatari_utenti(tenant_id: str, topic: str, evento: dict[str, Any]) -> list[str]:
    """Persone con l'e-mail scelta. Senza database nel processo (test unitari):
    nessuna; un errore del database si propaga (il consumer ritenta il messaggio,
    come per la lettura del cliente)."""
    from app.services.destinatari import destinatari_utenti
    from libs.shared.db import DBNonInizializzato

    try:
        return await destinatari_utenti(tenant_id, topic, evento)
    except DBNonInizializzato:
        return []


def oggetto_messaggio_esplicito(evento: dict[str, Any]) -> str:
    """Identità del messaggio esplicito per la deduplica: l'oggetto dichiarato
    (oggetto_id, aip_id, pdv_id) o, se manca, l'impronta del contenuto (lo
    stesso messaggio riletto da Kafka non si duplica)."""
    dichiarato = evento.get("oggetto_id") or evento.get("aip_id") or evento.get("pdv_id")
    if dichiarato:
        return str(dichiarato)
    parti = [str(evento.get(k) or "")
             for k in ("evento", "tipo", "destinatario", "oggetto", "testo")]
    return "contenuto:" + hashlib.sha256("\x1f".join(parti).encode("utf-8")).hexdigest()


def _segreti(evento: dict[str, Any]) -> dict[str, str] | None:
    """R12a (VD-C-152): segreti del messaggio, SOLO sigillati (nome → sigillo).
    None se il messaggio non ne porta; {} se qualcuno non è un sigillo valido
    (il messaggio si scarta: un segreto in chiaro non entra nella coda)."""
    from libs.shared.security.sigillo import e_sigillo

    segreti = evento.get("segreti")
    if segreti is None:
        return None
    if not isinstance(segreti, dict) or not segreti or not all(
            isinstance(k, str) and k.isidentifier() and e_sigillo(v)
            for k, v in segreti.items()):
        return {}
    return {str(k): str(v) for k, v in segreti.items()}


async def accoda_esplicita(
    evento: dict[str, Any],
    coda: CodaUscita,
    *,
    adesso: datetime,
    lettore: LettoreCliente = leggi_cliente,
    lettore_oggetto: LettoreTenantOggetto = leggi_tenant_oggetto,
) -> int:
    """Topic notification.send: messaggio esplicito (tipo, destinatario, oggetto, testo)."""
    canale = str(evento.get("tipo") or "email")
    canale = "email" if canale in ("email", "email_smtp") else canale
    tenant_id, _cliente = await risolvi_cliente(
        evento, lettore=lettore, lettore_oggetto=lettore_oggetto,
    )
    m = NuovoMessaggio(
        tenant_id=tenant_id,
        tipo=str(evento.get("evento") or "notification.send"),
        oggetto_id=oggetto_messaggio_esplicito(evento),
        destinatario=str(evento.get("destinatario") or ""),
        oggetto=str(evento.get("oggetto") or ""),
        testo=str(evento.get("testo") or ""),
        html=evento.get("html"),
        canale=canale,
        segreti=_segreti(evento),
    )
    if m.segreti is not None and not m.segreti:
        # VD-C-152: un segreto non sigillato non entra nella coda (resta solo il motivo)
        await coda.registra_non_partita(
            NuovoMessaggio(**{**m.__dict__, "segreti": None, "testo": ""}),
            "segreti non sigillati: messaggio scartato (VD-C-152)", adesso)
        return 0
    if canale in CANALI_NON_DISPONIBILI:
        await coda.registra_non_partita(m, CANALI_NON_DISPONIBILI[canale], adesso)
        return 0
    if canale != "email" or not indirizzo_plausibile(m.destinatario) or not m.oggetto:
        await coda.registra_non_partita(
            m, "messaggio non valido: canale, destinatario o oggetto", adesso
        )
        return 0
    return int(await coda.accoda(m, adesso))
