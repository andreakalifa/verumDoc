# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/inattivita.py — scadenza della sessione del portale per inattività

Riferimenti: VD-C-127 (R06); REGOLE_SVILUPPO §19.7; migrazione
``20261030_045`` (tabella ``sessioni_attive``); documento:
docs/architettura/sicurezza.md.

Una sessione del portale (cookie ``vd_sessione``, o lo stesso token presentato
come Bearer: claim ``ses = "portale"``) vale finché:
  - il token non è scaduto (``exp``) e non è stato revocato (R05, R12);
  - non è rimasta INATTIVA oltre il suo limite. R07 (D14): limite per ruolo,
    impostazione della console ``SESSIONE_INATTIVITA_MINUTI_<RUOLO>`` (15 minuti
    per SUPER_ADMIN, RSC_CONSERVATORE e ADMIN_TENANT, 30 per gli altri; il
    conservatore fissa il massimo, l'ente può solo abbassarlo), letta dal gateway
    all'accesso con ``libs.shared.parametri.limite_inattivita_ruolo``.

Il limite si fissa all'accesso (``registra_sessione``, solo il gateway) nella
riga di ``sessioni_attive``: gli altri servizi non hanno configurazione propria
e applicano tutti la stessa regola. Ogni richiesta della sessione, in qualunque
servizio, esegue UN solo ``UPDATE`` (rinnovo a scorrimento) con la condizione di
validità: nessuna cache, quindi scadenza e uscita valgono subito in tutti i
processi (prima la revoca negli altri processi arrivava entro 30 s).
Le richieste con ``X-Sessione-Rinnovo: no`` (aggiornamenti automatici della
pagina senza azioni dell'utente) controllano senza rinnovare.

Revisione R06 (V4): il middleware chiama questo controllo solo DOPO aver
verificato firma e scadenza del token (``jwt_rs256.verifica_firma_e_scadenza``):
un token falso non costa scritture; il rinnovo scrive al più una volta ogni
``RINNOVO_MINIMO_SECONDI`` per sessione. Privilegi (migrazione 045): SELECT,
INSERT, DELETE e UPDATE della sola ``ultima_attivita`` al gruppo
``verumdoc_sessioni_portale`` (i login dei servizi del portale, non il server
SFTP); un trigger impone ``ultima_attivita = now()`` (nessun rinnovo nel futuro).

Database non inizializzato nel processo (test unitari): il controllo non si
applica, come per la revoca; errore del database: ``RevocaNonVerificabile``
(503, fail-closed).
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Claim dei token del portale (scambio SPID/CIE e token di sviluppo)
CLAIM_SESSIONE = "ses"
SESSIONE_PORTALE = "portale"
INTESTAZIONE_RINNOVO = "x-sessione-rinnovo"


#: Esiti del controllo
ATTIVA = "attiva"
SCADUTA = "scaduta"
NON_APPLICABILE = "non_applicabile"


def payload_non_verificato(token: str) -> dict[str, Any]:
    """Claim del JWT SENZA verificarne la firma: servono solo a decidere se il
    token è di una sessione del portale. La verifica vera la fa la dipendenza
    di autenticazione del servizio; chi togliesse il claim invaliderebbe la
    firma. Token malformato: ``{}``."""
    try:
        parte = token.split(".")[1]
        dati = json.loads(base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4)))
    except (IndexError, ValueError, UnicodeDecodeError):
        return {}
    return dati if isinstance(dati, dict) else {}


def e_sessione_portale(token: str) -> bool:
    return payload_non_verificato(token).get(CLAIM_SESSIONE) == SESSIONE_PORTALE


async def registra_sessione(token: str, payload: dict[str, Any], *, limite_secondi: int) -> None:
    """Gateway, all'accesso: riga della sessione con il suo limite d'inattività."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session
    from libs.shared.security.revoca import _uuid_testo, impronta_token

    scade = datetime.fromtimestamp(int(payload["exp"]), UTC)
    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.sessioni_attive (impronta, user_id, tenant_id, ruolo, "
            "inattivita_secondi, scade_il) VALUES (:i, CAST(:u AS uuid), CAST(:t AS uuid), :r, "
            ":s, :e) ON CONFLICT (impronta) DO NOTHING"),
            {"i": impronta_token(token), "u": _uuid_testo(payload.get("user_id")),
             "t": _uuid_testo(payload.get("tenant_id")), "r": str(payload.get("ruolo") or "")[:40],
             "s": int(limite_secondi), "e": scade})
        # pulizia delle sessioni scadute (la tabella resta piccola)
        await session.execute(text(
            "DELETE FROM public.sessioni_attive WHERE scade_il < now() - interval '1 day'"))


#: R06 (revisione V4): al più un rinnovo ogni RINNOVO_MINIMO_SECONDI per sessione
#: (meno scritture, WAL e tuple morte); la validità si controlla sempre.
RINNOVO_MINIMO_SECONDI = 30
_RINNOVA = (
    "WITH s AS (SELECT impronta, ultima_attivita < now() - make_interval(secs => :minimo) "
    "AS da_rinnovare FROM public.sessioni_attive "
    "WHERE impronta = :i AND scade_il > now() "
    "AND ultima_attivita > now() - make_interval(secs => inattivita_secondi)), "
    "u AS (UPDATE public.sessioni_attive t SET ultima_attivita = now() FROM s "
    "WHERE t.impronta = s.impronta AND s.da_rinnovare RETURNING 1) "
    "SELECT 1 FROM s")
_CONTROLLA = (
    "SELECT 1 FROM public.sessioni_attive "
    "WHERE impronta = :i AND scade_il > now() "
    "AND ultima_attivita > now() - make_interval(secs => inattivita_secondi)")


async def controlla_sessione(token: str, *, rinnova: bool = True) -> str:
    """``ATTIVA`` (e rinnovata se ``rinnova``), ``SCADUTA`` (inattiva oltre il
    limite, chiusa con «Esci» o mai registrata) o ``NON_APPLICABILE`` (nessun
    database nel processo). Errore del database: ``RevocaNonVerificabile``."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import DBNonInizializzato, get_async_session
    from libs.shared.security.revoca import RevocaNonVerificabile, impronta_token

    try:
        async with get_async_session() as session:
            parametri: dict[str, Any] = {"i": impronta_token(token)}
            if rinnova:
                parametri["minimo"] = RINNOVO_MINIMO_SECONDI
            trovata = (await session.execute(text(_RINNOVA if rinnova else _CONTROLLA),
                                             parametri)).first()
    except DBNonInizializzato:
        return NON_APPLICABILE
    except SQLAlchemyError as exc:
        logger.error("sessione_inattivita_non_verificabile", errore=type(exc).__name__)
        raise RevocaNonVerificabile("controllo della sessione non riuscito") from exc
    return ATTIVA if trovata is not None else SCADUTA


async def chiudi_sessione_attiva(token: str) -> None:
    """Uscita: la sessione non vale più in nessun servizio, subito."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session
    from libs.shared.security.revoca import impronta_token

    async with get_async_session() as session:
        await session.execute(text("DELETE FROM public.sessioni_attive WHERE impronta = :i"),
                              {"i": impronta_token(token)})


async def chiudi_sessioni_utente(session: Any, user_id: str) -> None:
    """Utente disattivato o ruolo cambiato: tutte le sue sessioni del portale
    chiuse nella transazione del chiamante (effetto immediato ovunque)."""
    from sqlalchemy import text

    await session.execute(text(
        "DELETE FROM public.sessioni_attive WHERE user_id = CAST(:u AS uuid)"), {"u": user_id})


__all__ = [
    "ATTIVA", "CLAIM_SESSIONE", "INTESTAZIONE_RINNOVO", "NON_APPLICABILE",
    "RINNOVO_MINIMO_SECONDI", "SCADUTA",
    "SESSIONE_PORTALE", "chiudi_sessione_attiva", "chiudi_sessioni_utente",
    "controlla_sessione", "e_sessione_portale",
    "payload_non_verificato", "registra_sessione",
]
