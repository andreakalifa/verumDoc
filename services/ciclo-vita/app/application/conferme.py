# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/conferme.py — conferma firmata dal gateway delle decisioni sensibili
(R17, VD-P-164; chiude il residuo di VD-C-120 e VD-C-141).

API (``registra``): una decisione sensibile porta la conferma firmata dal gateway
(``libs.shared.conferme_decisione``). Con la conferma attiva (sempre in produzione) l'API la
controlla con le chiavi pubbliche della configurazione, la registra in
``conferme_decisione`` (il database fissa istante e versione) nella STESSA transazione della
decisione e la collega alla pratica (``dati.conferme[azione]``); una decisione con una chiave
API, senza conferma o con una conferma che non corrisponde è rifiutata (LC-403-CONFERMA).

Lavoratore (``assicura``): prima di eseguire un lavoro distruttivo ricostruisce le conferme
che quel lavoro richiede e le VERIFICA con le chiavi pubbliche della propria configurazione
(mai dal database): firma, chiave non revocata, scadenza, versione, contenuto ricalcolato
dalla pratica che sta per eseguire, utente che ha deciso, ente, nonce non consumato da
un'altra conferma (registro ``conferme_lavoratore``, scritto solo dal lavoratore). Difetti →
pratica BLOCCATA come per le autorizzazioni (``LC-409-AUTORIZZAZIONI``), nessun effetto.
Le azioni già decise prima della migrazione 059 su pratiche allora aperte non si
pretendono (``conferme_transizione_r17``, scritta solo dalla migrazione).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain import stati as st
from app.domain.permessi import Identita
from libs.shared import conferme_decisione as cd
from libs.shared.errori import ErroreApi

logger = structlog.get_logger(__name__)

#: lavori dei rettifica che il lavoratore esegue (stessa conferma)
_LAVORI_RETTIFICA = frozenset({"VERSA", "ATTENDI"})
#: difetti della sola chiave (non del contenuto), ignorati per una conferma già consumata
_DIFETTI_CHIAVE = ("CONFERMA_CHIAVE_REVOCATA", "CONFERMA_CHIAVE_IGNOTA")
#: revisione R17: azioni che autorizzano UN giro di esecuzione; chiuso il giro (fatto
#: GIRO_CHIUSO del lavoratore) un giro nuovo vuole una conferma registrata dopo, salvo la
#: ripresa automatica chiesta dal lavoro periodico (fatto RIPRESA_SISTEMA)
AZIONI_GIRO = frozenset({cd.SCARTO_ESEGUI, cd.RIVERSAMENTO_USCITA_ESEGUI,
                         cd.RETTIFICA_RICHIEDI})


def attiva() -> bool:
    return settings.conferma_decisioni_attiva


@lru_cache(maxsize=4)
def _chiavi(pubbliche: str, revocati: str) -> tuple[dict[str, Any], frozenset[str]]:
    return cd.chiavi_pubbliche(pubbliche), cd.kid_revocati(revocati)


def chiavi() -> tuple[dict[str, Any], frozenset[str]]:
    """Chiavi pubbliche del gateway e kid revocati, dalla configurazione del processo."""
    return _chiavi(settings.conferme_chiavi_pubbliche, settings.conferme_kid_revocati)


# ── Azioni ───────────────────────────────────────────────────────────────

def azione_transizione(tipo: str, da: str, a: str, esecuzione: bool) -> str | None:
    """Azione sensibile di una transizione chiesta da una persona (None = non sensibile)."""
    if tipo == st.SCARTO and a == st.ESEGUIBILE:
        return cd.SCARTO_APPROVA
    if tipo == st.SCARTO and esecuzione and a == st.ESEGUITA:
        return cd.SCARTO_ESEGUI
    if tipo == st.RIVERSAMENTO_USCITA and esecuzione:
        return cd.RIVERSAMENTO_USCITA_ESEGUI
    return None


def azioni_del_lavoro(pratica: Mapping[str, Any]) -> list[str]:
    """Conferme che il lavoro in esecuzione richiede (oltre alla ripresa)."""
    tipo, lavoro, stato = pratica.get("tipo"), pratica.get("lavoro"), pratica.get("stato")
    if tipo == st.SCARTO and lavoro == st.LAVORO_ESEGUI:
        return [cd.SCARTO_APPROVA, cd.SCARTO_ESEGUI]
    if tipo == st.RIVERSAMENTO_USCITA and lavoro == st.LAVORO_ESEGUI:
        return [cd.RIVERSAMENTO_USCITA_ESEGUI]
    if tipo == st.ANNULLAMENTO and lavoro == st.LAVORO_ANNULLA:
        return [cd.ANNULLAMENTO_RICHIEDI] + (
            [cd.ANNULLAMENTO_APPROVA] if stato == st.APPROVATA else [])
    if tipo == st.RETTIFICA and lavoro in _LAVORI_RETTIFICA:
        return [cd.RETTIFICA_RICHIEDI]
    return []


# ── API: controllo e registrazione ───────────────────────────────────────

def _rifiuto(messaggio: str, difetti: Sequence[str] = ()) -> ErroreApi:
    return ErroreApi("LC-403-CONFERMA", messaggio,
                     dettagli={"difetti": list(difetti)[:10]} if difetti else None)


async def registra(session: AsyncSession, identita: Identita, *, azione: str,
                   pratica: Mapping[str, Any], conferma: Mapping[str, Any] | None,
                   campi: Mapping[str, Any], versione_attesa: int,
                   adesso: datetime) -> str | None:
    """Controlla e registra la conferma della decisione ``azione`` su ``pratica`` (per le
    azioni che creano la pratica: la riga appena inserita). None se la conferma non è
    attiva. Errori: LC-403-CONFERMA (manca, chiave API, non valida, già usata),
    LC-503-CONFERMA (chiavi non configurate)."""
    if not attiva():
        return None
    if identita.metodo != "jwt" or not identita.user_id:
        raise _rifiuto("Una decisione sensibile la conferma una persona dalla console, non "
                       "una chiave API.")
    if conferma is None:
        raise _rifiuto("Manca la conferma firmata della decisione: confermare l'identità e "
                       "ripetere.")
    try:
        pubbliche, revocati = chiavi()
    except ValueError:
        pubbliche, revocati = {}, frozenset()
    if not pubbliche:
        logger.error("conferme_chiavi_pubbliche_assenti")
        raise ErroreApi("LC-503-CONFERMA")
    try:
        crudi, firma, campi_firmati = cd.leggi(conferma)
    except cd.ConfermaNonValida as exc:
        raise _rifiuto("Conferma della decisione illeggibile.", [str(exc)]) from None
    creazione = azione in cd.AZIONI_CREAZIONE
    atteso = cd.Atteso(azione=azione, pratica=None if creazione else str(pratica["id"]),
                       impronta=cd.impronta_contenuto(azione, campi),
                       utente=identita.user_id, ente=str(pratica["tenant_id"]))
    provvisoria = cd.Registrazione(id="", payload=crudi, firma=firma, registrata_il=adesso,
                                   versione_pratica=0 if creazione else int(versione_attesa))
    difetti = cd.difetti(provvisoria, atteso, chiavi=pubbliche, revocati=revocati)
    if difetti:
        logger.warning("conferma_decisione_rifiutata", azione=azione,
                       pratica_id=str(pratica["id"]), difetti=difetti[:5])
        raise _rifiuto("La conferma non corrisponde a questa decisione: ripetere la "
                       "decisione dalla pagina aggiornata.", difetti)
    try:
        async with session.begin_nested():
            conferma_id = (await session.execute(text(
                "INSERT INTO public.conferme_decisione (pratica_id, tenant_id, azione, kid, "
                "nonce, utente_id, payload, firma) VALUES (:p, :t, :a, :k, :n, "
                "CAST(:u AS uuid), :payload, :firma) RETURNING id"),
                {"p": pratica["id"], "t": pratica["tenant_id"], "a": azione,
                 "k": campi_firmati["kid"], "n": campi_firmati["nonce"], "u": identita.user_id,
                 "payload": crudi, "firma": firma})).scalar_one()
    except IntegrityError:
        raise _rifiuto("Questa conferma è già stata usata: confermare di nuovo la "
                       "decisione.") from None
    await session.execute(text(
        "UPDATE public.pratiche SET dati = COALESCE(dati, '{}'::jsonb) || jsonb_build_object("
        "'conferme', COALESCE(dati -> 'conferme', '{}'::jsonb) || jsonb_build_object("
        "CAST(:a AS text), CAST(:c AS text))) WHERE id = :p"),
        {"a": azione, "c": str(conferma_id), "p": pratica["id"]})
    logger.info("conferma_decisione_registrata", azione=azione, pratica_id=str(pratica["id"]),
                conferma_id=str(conferma_id))
    return str(conferma_id)


# ── Lavoratore: verifica prima di eseguire ───────────────────────────────

def _dati(pratica: Mapping[str, Any]) -> dict[str, Any]:
    dati = pratica.get("dati") or {}
    return json.loads(dati) if isinstance(dati, str) else dict(dati)


def _testo(valore: Any) -> str | None:
    return None if valore is None else str(valore)


async def _ultimo_utente(session: AsyncSession, pratica_id: Any, a_stato: str) -> str | None:
    return _testo((await session.execute(text(
        "SELECT utente_id FROM public.pratiche_stati WHERE pratica_id = :p AND a_stato = :a "
        "ORDER BY id DESC LIMIT 1"), {"p": pratica_id, "a": a_stato})).scalar())


async def _utente_atteso(session: AsyncSession, azione: str, pratica: Mapping[str, Any],
                         utente_firmato: str | None, *, ripresa_sistema: bool = False
                         ) -> str | None:
    """Chi ha preso la decisione, secondo la pratica e la sua cronologia."""
    dati = _dati(pratica)
    if azione == cd.SCARTO_APPROVA:
        return await _ultimo_utente(session, pratica["id"], st.ESEGUIBILE)
    if azione == cd.ANNULLAMENTO_APPROVA:
        return await _ultimo_utente(session, pratica["id"], st.APPROVATA)
    if azione in (cd.SCARTO_ESEGUI, cd.RIVERSAMENTO_USCITA_ESEGUI):
        richiesta = _testo((dati.get("esecuzione") or {}).get("richiesta_da"))
        if richiesta is not None:
            return richiesta
        # ripresa automatica del SISTEMA (R09): riconosciuta SOLO dal fatto RIPRESA_SISTEMA
        # scritto dal lavoro periodico (mai da ``dati``, scrivibile dall'API); vale la
        # conferma del giro precedente già consumata per questa pratica (controllo a parte)
        return utente_firmato if ripresa_sistema else None
    if azione in cd.AZIONI_CREAZIONE:
        return _testo(pratica.get("creata_da"))
    if azione == cd.PRATICA_RIPRENDI:
        return _testo((dati.get("ripresa") or {}).get("da"))
    return None


async def _sospensione_aperta(session: AsyncSession, pratica_id: Any
                              ) -> Mapping[str, Any] | None:
    """Ultima sospensione registrata dal lavoratore non seguita da una ripresa verificata."""
    riga = (await session.execute(text(
        "SELECT s.id, s.created_at FROM public.conferme_lavoratore s "
        "WHERE s.pratica_id = :p AND s.evento = 'SOSPESA' AND NOT EXISTS ("
        "  SELECT 1 FROM public.conferme_lavoratore v WHERE v.pratica_id = s.pratica_id "
        "  AND v.evento = 'VERIFICATA' AND v.azione = 'PRATICA_RIPRENDI' AND v.id > s.id) "
        "ORDER BY s.id DESC LIMIT 1"), {"p": pratica_id})).mappings().first()
    return dict(riga) if riga else None


async def _stato_giri(session: AsyncSession, pratica_id: Any
                      ) -> tuple[Mapping[str, Any] | None, bool]:
    """Ultimo giro chiuso dal lavoratore e se dopo di esso il lavoro periodico ha chiesto
    una ripresa automatica (fatti del solo lavoratore)."""
    chiuso = (await session.execute(text(
        "SELECT id, created_at FROM public.conferme_lavoratore WHERE pratica_id = :p "
        "AND evento = 'GIRO_CHIUSO' ORDER BY id DESC LIMIT 1"), {"p": pratica_id})
    ).mappings().first()
    if chiuso is None:
        return None, False
    ripresa = bool((await session.execute(text(
        "SELECT EXISTS (SELECT 1 FROM public.conferme_lavoratore WHERE pratica_id = :p "
        "AND evento = 'RIPRESA_SISTEMA' AND id > :c)"), {"p": pratica_id, "c": chiuso["id"]})
    ).scalar())
    return dict(chiuso), ripresa


async def difetti_conferme(session: AsyncSession, pratica: Mapping[str, Any]
                           ) -> tuple[list[str], list[dict[str, Any]]]:
    """Difetti delle conferme richieste dal lavoro e conferme verificate da consumare."""
    pubbliche, revocati = chiavi()
    richieste = azioni_del_lavoro(pratica)
    sospensione = await _sospensione_aperta(session, pratica["id"])
    if sospensione is not None:
        richieste.append(cd.PRATICA_RIPRENDI)
    if not richieste:
        return [], []
    esenti = set((await session.execute(text(
        "SELECT azioni FROM public.conferme_transizione_r17 WHERE pratica_id = :p"),
        {"p": pratica["id"]})).scalar() or ())
    collegate = _dati(pratica).get("conferme") or {}
    chiuso, ripresa_sistema = await _stato_giri(session, pratica["id"])
    difetti: list[str] = []
    verificate: list[dict[str, Any]] = []
    for azione in richieste:
        # l'esenzione della transizione vale per il giro in corso alla migrazione: chiuso
        # quel giro, un giro nuovo di esecuzione vuole la sua conferma
        if azione in esenti and not (azione in AZIONI_GIRO and chiuso is not None):
            continue
        conferma_id = collegate.get(azione) if isinstance(collegate, dict) else None
        try:
            ident = uuid.UUID(str(conferma_id))
        except (TypeError, ValueError):
            difetti.append(f"CONFERMA_ASSENTE: nessuna conferma firmata per {azione}")
            continue
        riga = (await session.execute(text(
            "SELECT id, pratica_id, tenant_id, payload, firma, registrata_il, versione_pratica "
            "FROM public.conferme_decisione WHERE id = :c"), {"c": ident})).mappings().first()
        if riga is None or riga["pratica_id"] != pratica["id"]:
            difetti.append(f"CONFERMA_ASSENTE: conferma di {azione} non registrata per questa "
                           "pratica")
            continue
        payload, firma = bytes(riga["payload"]), bytes(riga["firma"])
        try:
            utente_firmato = _testo(json.loads(payload.decode("utf-8")).get("utente"))
            nonce = cd.nonce_di(payload)
        except (ValueError, KeyError, UnicodeDecodeError):
            difetti.append(f"CONFERMA_ILLEGGIBILE: conferma di {azione}")
            continue
        campi = cd.campi_impronta(azione, pratica)
        if azione == cd.RETTIFICA_RICHIEDI and not cd.riferimento_coerente(
                campi.get("riferimento"), pratica):
            difetti.append("CONFERMA_RIFERIMENTO: il riferimento confermato non è il "
                           "versamento della pratica")
        atteso = cd.Atteso(
            azione=azione,
            pratica=None if azione in cd.AZIONI_CREAZIONE else str(pratica["id"]),
            impronta=cd.impronta_contenuto(azione, campi),
            utente=await _utente_atteso(session, azione, pratica, utente_firmato,
                                        ripresa_sistema=ripresa_sistema),
            ente=str(pratica["tenant_id"]))
        usata_da = _testo((await session.execute(text(
            "SELECT conferma_id FROM public.conferme_lavoratore WHERE evento = 'VERIFICATA' "
            "AND nonce = :n"), {"n": nonce})).scalar())
        reg = cd.Registrazione(id=str(riga["id"]), payload=payload, firma=firma,
                               registrata_il=riga["registrata_il"],
                               versione_pratica=int(riga["versione_pratica"]))
        trovati = cd.difetti(reg, atteso, chiavi=pubbliche, revocati=revocati,
                             usata_da=usata_da)
        if usata_da == str(riga["id"]):
            # già verificata e consumata da questo lavoratore (stessa conferma, giro
            # successivo dello stesso lavoro): una chiave ruotata o revocata DOPO non ferma
            # un'esecuzione già autorizzata; il contenuto si ricontrolla comunque
            trovati = [d for d in trovati if not d.startswith(_DIFETTI_CHIAVE)]
        if azione in AZIONI_GIRO and chiuso is not None \
                and riga["registrata_il"] <= chiuso["created_at"] \
                and not (ripresa_sistema and usata_da == str(riga["id"])):
            # conferma di un giro già chiuso: vale solo per la ripresa automatica del
            # SISTEMA e solo se era stata consumata per questa pratica
            trovati.append("CONFERMA_GIRO_PRECEDENTE: la conferma è di un giro di "
                           "esecuzione già concluso")
        if azione == cd.PRATICA_RIPRENDI and sospensione is not None \
                and riga["registrata_il"] <= sospensione["created_at"]:
            trovati.append("CONFERMA_RIPRESA_PRECEDENTE: la conferma della ripresa è "
                           "anteriore alla sospensione")
        difetti += [f"{azione}: {d}" for d in trovati]
        if not trovati:
            verificate.append({"conferma_id": str(riga["id"]), "nonce": nonce,
                               "azione": azione})
    return difetti, verificate


async def assicura(pratica: Mapping[str, Any], adesso: datetime) -> None:
    """Prima di eseguire: conferme verificate (o pratica BLOCCATA e
    ``AutorizzazioniNonVerificate``); gli errori temporanei si propagano."""
    from app.application import autorizzazioni
    from libs.shared.db import get_async_session

    if not attiva():
        return
    async with get_async_session() as session:
        difetti, verificate = await difetti_conferme(session, pratica)
        if not difetti:
            for v in verificate:
                await session.execute(text(
                    "INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento, "
                    "conferma_id, nonce, azione) VALUES (:p, :t, 'VERIFICATA', "
                    "CAST(:c AS uuid), :n, :a) "
                    "ON CONFLICT (nonce) WHERE evento = 'VERIFICATA' DO NOTHING"),
                    {"p": pratica["id"], "t": pratica["tenant_id"], "c": v["conferma_id"],
                     "n": v["nonce"], "a": v["azione"]})
    if difetti:
        await autorizzazioni.blocca(pratica, difetti, adesso)
        logger.error("conferme_decisione_non_verificate", pratica_id=str(pratica["id"]),
                     difetti=difetti[:10])
        raise autorizzazioni.AutorizzazioniNonVerificate(difetti)


async def registra_fatto(session: AsyncSession, pratica: Mapping[str, Any], evento: str) -> None:
    """Fatto del lavoratore (GIRO_CHIUSO, RIPRESA_SISTEMA, SOSPESA)."""
    await session.execute(text(
        "INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento) "
        "VALUES (:p, :t, :e)"), {"p": pratica["id"], "t": pratica["tenant_id"], "e": evento})


async def registra_sospensione(session: AsyncSession, pratica: Mapping[str, Any]) -> None:
    """Fatto del lavoratore: pratica sospesa (la ripresa richiederà una conferma)."""
    await session.execute(text(
        "INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento) "
        "VALUES (:p, :t, 'SOSPESA')"), {"p": pratica["id"], "t": pratica["tenant_id"]})


__all__ = ["assicura", "attiva", "azione_transizione", "azioni_del_lavoro", "chiavi",
           "difetti_conferme", "registra", "registra_fatto", "registra_sospensione"]
