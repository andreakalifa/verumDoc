# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/token_canale.py — token di servizio dei canali (contratto C2).

Riferimenti: piano R03 §3.2 (SFTP), contratto C2 dell'ondata B (agente b4);
REGOLE_SVILUPPO §19.1 (token sempre verificati), §19.3 (chiavi solo da
ambiente), §14.3 (cliente non determinabile → negato).

Un servizio di canale (oggi solo ``sftp-ingest``) chiama il gateway PER CONTO
di un ente e di un utente responsabile (l'utente scelto per l'account SFTP).
Lo fa con un token RS256 di breve durata:

    sub        "servizio:<nome>"          (es. servizio:sftp-ingest)
    servizio   "<nome>"
    tipo_token "canale"
    tenant_id  ente per conto del quale agisce
    user_id    utente responsabile (deve esistere, essere attivo e dell'ente)
    ruolo      ruolo ATTUALE dell'utente (se cambia, il token non vale più)
    canale     "SFTP"                     (il token non vale per altri canali)
    aud        "verumdoc-canale"          (≠ verumdoc-api dei token utente,
                                           ≠ verumdoc-signature del servizio di firma)

Firma: con la chiave privata del servizio di canale. Il gateway conosce le
chiavi pubbliche dei servizi ammessi (impostazione) e verifica la firma con
quella del servizio dichiarato; poi controlla sul database che l'utente sia
attivo, appartenga all'ente, abbia ancora quel ruolo e che l'ente non sia
SOSPESO/DISATTIVATO (``verifica_utente_del_canale``: a differenza della revoca
dei token utente, qui un utente ASSENTE è un rifiuto).

Durata predefinita 300 s, massima 3600 s: il servizio ne emette uno nuovo per
ogni lotto di lavoro (``firma_token_canale``), senza salvarlo.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from libs.shared.security.jwt_rs256 import (
    DETTAGLIO_TOKEN_NON_VALIDO,
    ErroreTokenNonValido,
    firma_token,
    verifica_token,
)
from libs.shared.security.revoca import STATI_ENTE_BLOCCATI, RevocaNonVerificabile

logger = structlog.get_logger(__name__)

#: Audience dei token di canale emessi da ``firma_token_canale``.
PUBBLICO_TOKEN_CANALE = "verumdoc-canale"
#: Audience accettate in verifica: solo quella dei token di canale. R03 i1:
#: sftp-ingest emette ora i token con ``firma_token_canale`` (aud
#: «verumdoc-canale»); ``verumdoc-api`` (pubblico dei token utente) non è più
#: accettata, così un token di servizio non ha mai il pubblico dei token utente.
PUBBLICI_ACCETTATI_TOKEN_CANALE: tuple[str, ...] = (PUBBLICO_TOKEN_CANALE,)
TIPO_TOKEN_CANALE = "canale"
DURATA_PREDEFINITA_TOKEN_CANALE = 300
DURATA_MASSIMA_TOKEN_CANALE = 3600
#: Canali per cui un servizio può emettere token per conto di un ente (C2).
#: Gli altri canali (REST, PORTALE, PDND) hanno credenziali proprie.
#: R05: ``CICLO_VITA`` (versamenti di rettifica e riversamento in ingresso).
CANALI_CON_TOKEN_DI_SERVIZIO: frozenset[str] = frozenset({"SFTP", "CICLO_VITA"})
#: Account del canale (R03 f1, REV-04): il token vale solo se l'utente è il
#: RESPONSABILE di un account ATTIVO del canale, del proprio ente (tabelle della
#: migrazione 20261021_016 per SFTP). Un canale senza voce qui non è ammesso.
#: R05: per ``CICLO_VITA`` l'«account» è una pratica di rettifica o di
#: riversamento in ingresso APERTA avviata dall'utente (vista della migrazione
#: 20261023_037, ``security_invoker`` dalla 20261023_040: rispetta la RLS).
TABELLE_ACCOUNT_CANALE: dict[str, str] = {
    "SFTP": "public.sftp_account",
    "CICLO_VITA": "public.pratiche_canale_versamento",
}
#: Claim obbligatori di un token di canale.
CLAIM_TOKEN_CANALE = ("servizio", "tenant_id", "user_id", "ruolo", "canale")


@dataclass(frozen=True)
class ServizioCanale:
    """Servizio di canale ammesso dal gateway, con i canali e la chiave pubblica."""

    nome: str
    canali: frozenset[str]
    chiave_pubblica_pem: str


def _uuid(valore: Any, campo: str) -> str:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        raise ValueError(f"{campo} deve essere un UUID") from None


def firma_token_canale(
    servizio: str,
    *,
    tenant_id: str,
    user_id: str,
    ruolo: str,
    canale: str,
    chiave_privata_pem: str,
    emittente: str,
    durata_secondi: int = DURATA_PREDEFINITA_TOKEN_CANALE,
    pubblico: str = PUBBLICO_TOKEN_CANALE,
    adesso: int | None = None,
) -> str:
    """Token di canale firmato con la chiave privata del servizio (ValueError
    se i dati non sono validi)."""
    nome = (servizio or "").strip()
    if not nome:
        raise ValueError("Indicare il servizio di canale")
    if canale not in CANALI_CON_TOKEN_DI_SERVIZIO:
        raise ValueError(f"Il canale {canale!r} non usa token di servizio "
                         f"(ammessi: {', '.join(sorted(CANALI_CON_TOKEN_DI_SERVIZIO))})")
    if not (ruolo or "").strip():
        raise ValueError("Indicare il ruolo attuale dell'utente responsabile")
    if durata_secondi <= 0 or durata_secondi > DURATA_MASSIMA_TOKEN_CANALE:
        raise ValueError(f"Durata del token di canale fra 1 e {DURATA_MASSIMA_TOKEN_CANALE} s")
    return firma_token(
        {
            "sub": f"servizio:{nome}", "servizio": nome, "tipo_token": TIPO_TOKEN_CANALE,
            "tenant_id": _uuid(tenant_id, "tenant_id"), "user_id": _uuid(user_id, "user_id"),
            "ruolo": ruolo.strip(), "canale": canale,
        },
        chiave_privata_pem=chiave_privata_pem, emittente=emittente, pubblico=pubblico,
        durata_secondi=durata_secondi, adesso=adesso,
    )


def _rifiuto(motivo: str) -> ErroreTokenNonValido:
    logger.info("token_canale_rifiutato", motivo=motivo)
    return ErroreTokenNonValido("AUTH-001", "Token JWT non valido", DETTAGLIO_TOKEN_NON_VALIDO,
                                motivo=motivo)


def verifica_token_canale(
    token: str,
    *,
    servizi: Sequence[ServizioCanale],
    emittente: str,
    pubblico: str | Sequence[str] = PUBBLICI_ACCETTATI_TOKEN_CANALE,
    tolleranza_secondi: int = 30,
) -> dict[str, Any]:
    """
    Verifica firma (con la chiave di un servizio ammesso), iss, aud, exp e i
    claim; il servizio dichiarato deve essere quello della chiave che ha
    verificato la firma e il canale uno di quelli ammessi per lui.
    ``ErroreTokenNonValido`` (AUTH-001, AUTH-002 se scaduto) altrimenti.
    """
    scaduto: ErroreTokenNonValido | None = None
    for servizio in servizi:
        try:
            payload = verifica_token(
                token, chiave_pubblica_pem=servizio.chiave_pubblica_pem, emittente=emittente,
                pubblico=pubblico if isinstance(pubblico, str) else list(pubblico),  # type: ignore[arg-type]
                claim_obbligatori=CLAIM_TOKEN_CANALE,
                tolleranza_secondi=tolleranza_secondi,
            )
        except ErroreTokenNonValido as exc:
            if exc.codice == "AUTH-002":
                scaduto = exc
            continue
        if (payload.get("servizio") != servizio.nome
                or payload.get("sub") != f"servizio:{servizio.nome}"):
            continue  # firmato con una chiave condivisa da più servizi: provo gli altri
        # ``tipo_token`` è facoltativo (sftp-ingest non lo emette): se c'è, deve dire «canale»
        if payload.get("tipo_token", TIPO_TOKEN_CANALE) != TIPO_TOKEN_CANALE:
            raise _rifiuto("tipo_token diverso da «canale»")
        if payload.get("canale") not in servizio.canali:
            raise _rifiuto(f"canale {payload.get('canale')!r} non ammesso per {servizio.nome}")
        try:
            _uuid(payload.get("tenant_id"), "tenant_id")
            _uuid(payload.get("user_id"), "user_id")
        except ValueError as exc:
            raise _rifiuto(str(exc)) from None
        return payload
    if scaduto is not None:
        raise scaduto
    raise _rifiuto("nessun servizio di canale ammesso ha firmato il token")


async def verifica_utente_del_canale(payload: Mapping[str, Any]) -> None:
    """
    Controllo sul database (sempre, senza cache): utente esistente, attivo,
    dell'ente del token e con lo stesso ruolo (``ruolo_utente`` se il payload è
    già stato tradotto per i casi d'uso, altrimenti ``ruolo``); ente non bloccato;
    R03 f1 (REV-04): l'utente è il RESPONSABILE di almeno un account ATTIVO del
    canale del token nel PROPRIO ente (per SFTP ``sftp_account``): un ente senza
    account del canale, o con l'account SOSPESO, non accetta token di canale.
    ``ErroreTokenNonValido`` AUTH-003 se no; ``RevocaNonVerificabile`` se il
    database non risponde o non è configurato (fail-closed).
    """
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import DBNonInizializzato, get_async_session

    tabella = TABELLE_ACCOUNT_CANALE.get(str(payload.get("canale")))
    if tabella is None:
        raise ErroreTokenNonValido(
            "AUTH-003", "Accesso revocato", "Canale senza account: token non ammesso.",
            motivo=f"canale {payload.get('canale')!r} senza tabella degli account")
    try:
        async with get_async_session() as session:
            # tabella da costante del modulo (TABELLE_ACCOUNT_CANALE), mai da input
            sql = (
                "SELECT u.attivo, u.tenant_id::text, u.ruolo::text, t.stato::text, "  # noqa: S608
                f" EXISTS (SELECT 1 FROM {tabella} a WHERE a.tenant_id = u.tenant_id "
                "  AND a.responsabile_id = u.id AND a.stato = 'ATTIVO') AS account_attivo "
                "FROM public.users u JOIN public.tenants t ON t.id = u.tenant_id "
                "WHERE u.id = CAST(:uid AS uuid)"
            )
            riga = (await session.execute(text(sql),
                                          {"uid": str(payload.get("user_id"))})).first()
    except DBNonInizializzato as exc:
        raise RevocaNonVerificabile("database non configurato") from exc
    except SQLAlchemyError as exc:
        logger.error("utente_canale_non_verificabile", errore=type(exc).__name__)
        raise RevocaNonVerificabile("controllo dell'utente del canale non riuscito") from exc

    motivo: str | None = None
    if riga is None:
        motivo = "utente inesistente"
    else:
        attivo, ente, ruolo, stato_ente, account_attivo = riga
        if not attivo:
            motivo = "utente disattivato"
        elif ente != str(payload.get("tenant_id")):
            motivo = "utente di un altro ente"
        elif ruolo != str(payload.get("ruolo_utente") or payload.get("ruolo")):
            motivo = "ruolo dell'utente cambiato"
        elif stato_ente in STATI_ENTE_BLOCCATI:
            motivo = f"ente in stato {stato_ente}"
        elif not account_attivo:
            motivo = (f"utente non responsabile di un account {payload.get('canale')} attivo "
                      "dell'ente")
    if motivo is not None:
        logger.warning("token_canale_revocato", motivo=motivo, servizio=payload.get("servizio"),
                       tenant_id=payload.get("tenant_id"), user_id=payload.get("user_id"))
        raise ErroreTokenNonValido(
            "AUTH-003", "Accesso revocato",
            "L'utente responsabile del canale non è attivo per questo ente, il suo ruolo "
            "è cambiato oppure l'account del canale non è attivo: aggiornare l'account "
            "del canale.",
            motivo=motivo,
        )


def servizi_da_configurazione(
    ammessi: Mapping[str, Iterable[str]],
    chiavi_pubbliche: Mapping[str, str],
    *,
    chiave_ripiego_pem: str | None,
) -> list[ServizioCanale]:
    """
    Servizi ammessi con la loro chiave pubblica. Un servizio senza chiave
    dedicata usa ``chiave_ripiego_pem`` se indicata (sviluppo e test: la chiave
    del gateway), altrimenti è escluso (token rifiutati) e lo si scrive nel log.
    """
    from libs.shared.security.jwt_rs256 import normalizza_pem

    servizi: list[ServizioCanale] = []
    for nome, canali in sorted(ammessi.items()):
        validi = frozenset(c for c in canali if c in CANALI_CON_TOKEN_DI_SERVIZIO)
        scartati = sorted(set(canali) - validi)
        if scartati:
            logger.warning("canali_senza_token_di_servizio", servizio=nome, canali=scartati)
        pem = normalizza_pem(chiavi_pubbliche.get(nome)) or (chiave_ripiego_pem or "")
        if not pem:
            logger.warning("servizio_canale_senza_chiave", servizio=nome,
                           nota="token di canale rifiutati finché la chiave pubblica "
                                "dedicata non è configurata")
            continue
        if validi:
            servizi.append(ServizioCanale(nome=nome, canali=validi, chiave_pubblica_pem=pem))
    return servizi


__all__ = [
    "CANALI_CON_TOKEN_DI_SERVIZIO", "CLAIM_TOKEN_CANALE", "DURATA_MASSIMA_TOKEN_CANALE",
    "DURATA_PREDEFINITA_TOKEN_CANALE", "PUBBLICI_ACCETTATI_TOKEN_CANALE",
    "PUBBLICO_TOKEN_CANALE", "TIPO_TOKEN_CANALE",
    "TABELLE_ACCOUNT_CANALE", "ServizioCanale", "firma_token_canale", "servizi_da_configurazione",
    "verifica_token_canale", "verifica_utente_del_canale",
]
