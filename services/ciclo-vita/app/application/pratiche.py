# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/pratiche.py — casi d'uso comuni delle pratiche: lettura,
elenco, allegati, transizioni (manuali e automatiche), documenti nel WORM.

Riferimenti: contratto Q2 di R05; REGOLE_SVILUPPO §11.1 (un solo punto di
transizione: ``applica_transizione``, usato anche dal lavoratore), §12.3
(evento nella stessa transazione: outbox), §12.5 (stato atteso → 409),
§14 (perimetri), §15.1-§15.3 (audit nella transazione), §18 (errori dal
catalogo), §19.5 (file caricati: dimensione, impronta, mai il nome come chiave).
"""
from __future__ import annotations

import asyncio
import dataclasses
import re
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO, Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain import stati as st
from app.domain.in_parole import descrivi
from app.domain.permessi import Identita, ente_del_perimetro, puo_leggere
from app.infrastructure import repository as repo
from app.infrastructure import storage
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.outbox import accoda_evento
from libs.shared.security.audit import registra_audit
from libs.shared.storage.worm import aggiungi_anni

logger = structlog.get_logger(__name__)

# Topic degli eventi per i clienti (nome = tipo dell'evento, outbox → notification)
TOPIC_CAMBIO_STATO = "pratica.cambio_stato"
TOPIC_ANNULLATO = "versamento.annullato"
TOPIC_RETTIFICATO = "versamento.rettificato"
TOPIC_SCARTO_ESEGUITO = "scarto.eseguito"
TOPIC_RIVERSAMENTO_PRONTO = "riversamento.pronto"
TOPIC_CICLO_VITA = (TOPIC_CAMBIO_STATO, TOPIC_ANNULLATO, TOPIC_RETTIFICATO,
                    TOPIC_SCARTO_ESEGUITO, TOPIC_RIVERSAMENTO_PRONTO)

#: Stati in cui una transizione equivale a un'autorizzazione (mostrate a parte)
STATI_AUTORIZZAZIONE = (st.AUTORIZZATA_PRODUTTORE, st.NULLA_OSTA, st.ESEGUIBILE)
_NOME_SICURO = re.compile(r"[^A-Za-z0-9._-]+")


def adesso_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Attore:
    """Chi applica una transizione: una persona/sistema versante o il lavoratore."""

    utente_id: str | None
    ruolo: str
    metodo: str | None

    @classmethod
    def da_identita(cls, identita: Identita) -> Attore:
        return cls(identita.user_id, identita.ruolo, identita.metodo)


SISTEMA = Attore(None, st.SISTEMA, "lavoratore")


def errore_db(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("LC-503-DB", exc, evento=evento)


def non_trovata() -> ErroreApi:
    return ErroreApi("LC-404", "Pratica non trovata")


def uuid_o_404(valore: str, codice: str = "LC-404", messaggio: str = "Pratica non trovata"
               ) -> uuid.UUID:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        raise ErroreApi(codice, messaggio) from None


async def evento(session: AsyncSession, topic: str, chiave: str, payload: Mapping[str, Any],
                 tenant_id: uuid.UUID | str) -> None:
    """Evento per i clienti in outbox. ``tenant_id`` anche nel payload, come per
    tutti gli altri produttori: notification ricava il cliente solo da lì
    (integrazione R05)."""
    await accoda_evento(session, topic=topic, chiave=chiave,
                        payload={**payload, "tenant_id": str(tenant_id)},
                        tenant_id=str(tenant_id))


async def applica_transizione(session: AsyncSession, pratica: Mapping[str, Any], nuovo_stato: str,
                              attore: Attore, *, motivo: str | None, allegati: Sequence[str] = (),
                              adesso: datetime, lavoro: str | None | object = ...,
                              dati: Mapping[str, Any] | None = None,
                              conteggi: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """UNICO punto che cambia lo stato di una pratica (REGOLE §11.1): cronologia
    append-only, versione + 1, evento ``pratica.cambio_stato`` e voce d'audit
    nella stessa transazione. ``lavoro``: passo del lavoratore da impostare
    (``None`` = nessuno; omesso = invariato)."""
    m = st.macchina(pratica["tipo"])
    if m.cerca(pratica["stato"], nuovo_stato) is None:
        raise RuntimeError(f"transizione {pratica['stato']} → {nuovo_stato} non prevista")
    finale = nuovo_stato in m.finali or nuovo_stato in m.concluse_con_eccezioni
    campi: dict[str, Any] = {"stato": nuovo_stato, "versione": int(pratica["versione"]) + 1}
    if finale:
        campi["conclusa_il"] = adesso
        if lavoro is ...:
            lavoro = None  # una pratica conclusa non ha più lavoro (§11.5)
    if lavoro is not ...:
        campi["lavoro"] = lavoro
        campi["prossimo_tentativo"] = adesso
        campi["tentativi"] = 0
        campi["ultimo_errore"] = None
    if dati is not None:
        campi["dati"] = dict(dati)
    if conteggi is not None:
        campi["conteggi"] = dict(conteggi)
    await repo.inserisci_stato(session, pratica_id=pratica["id"], tenant_id=pratica["tenant_id"],
                               da_stato=pratica["stato"], a_stato=nuovo_stato,
                               utente_id=attore.utente_id, ruolo=attore.ruolo,
                               metodo=attore.metodo, motivo=motivo, allegati=allegati,
                               adesso=adesso)
    await repo.aggiorna_pratica(session, pratica["id"], adesso, **campi)
    payload = {"pratica_id": str(pratica["id"]), "tipo": pratica["tipo"],
               "da_stato": pratica["stato"], "a_stato": nuovo_stato,
               "versione": campi["versione"], "ente": str(pratica["tenant_id"]),
               "il": adesso.isoformat()}
    await evento(session, TOPIC_CAMBIO_STATO, str(pratica["id"]), payload, pratica["tenant_id"])
    await registra_audit(session, evento="ciclo_vita.transizione",
                         tenant_id=str(pratica["tenant_id"]), user_id=attore.utente_id,
                         dettagli={**payload, "ruolo": attore.ruolo, "motivo": motivo,
                                   "allegati": list(allegati)})
    logger.info("pratica_transizione", pratica_id=str(pratica["id"]), tipo=pratica["tipo"],
                da=pratica["stato"], a=nuovo_stato, ruolo=attore.ruolo)
    return {**pratica, **campi}


# ── Lettura ──────────────────────────────────────────────────────────────

def _allegati_usati(storia: Sequence[Mapping[str, Any]]) -> set[str]:
    usati: set[str] = set()
    for passo in storia:
        usati.update(str(a) for a in (passo.get("allegati") or []))
    return usati


def contesto_transizione(identita: Identita, pratica: Mapping[str, Any],
                         allegati_pratica: Sequence[Mapping[str, Any]],
                         storia: Sequence[Mapping[str, Any]],
                         citati: Sequence[str] = ()) -> st.ContestoTransizione:
    usati = _allegati_usati(storia)
    disponibili = frozenset(str(a["tipo"]) for a in allegati_pratica if str(a["id"]) not in usati)
    tipo_di = {str(a["id"]): str(a["tipo"]) for a in allegati_pratica if str(a["id"]) not in usati}
    return st.ContestoTransizione(
        ruolo=identita.ruolo, stesso_ente=identita.tenant_id == str(pratica["tenant_id"]),
        richiede_nulla_osta=bool(pratica["richiede_nulla_osta"]),
        allegati_disponibili=disponibili,
        allegati_citati=frozenset(tipo_di[c] for c in citati if c in tipo_di),
        # R14 (VD-C-133): una pratica SOSPESA (tentativi esauriti) resta «con lavoro»:
        # nessuna transizione manuale, solo RESPINTA se l'esecuzione non è iniziata
        lavoro_in_corso=(pratica.get("lavoro") is not None
                         or st.lavoro_sospeso(pratica) is not None),
        esecuzione_avviata=esecuzione_avviata(pratica),
        richiede_autorizzazione=bool(pratica.get("richiede_autorizzazione_produttore")))


def esecuzione_avviata(pratica: Mapping[str, Any]) -> bool:
    """True se il lavoratore ha iniziato l'esecuzione (``dati.esecuzione.avviata_il``,
    scritto da ``segna_avvio_esecuzione`` con la pratica bloccata). R14 (VD-C-133): vale
    anche per l'esecuzione SOSPESA dopo i tentativi esauriti (lavoro tolto ma non finito:
    una pratica sospesa a metà scarto non si respinge)."""
    if (pratica.get("lavoro") or st.lavoro_sospeso(pratica)) != LAVORO_ESEGUI:
        return False
    esecuzione = (pratica.get("dati") or {}).get("esecuzione") or {}
    return bool(esecuzione.get("avviata_il"))


def _allegato_pubblico(a: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": str(a["id"]), "tipo": a["tipo"], "nome_file": a["nome_file"],
            "content_type": a["content_type"], "dimensione": a["dimensione"],
            "sha256": a["sha256"], "conserva_fino_a": a["conserva_fino_a"],
            "firma": a.get("firma") or {}, "caricato_da": a.get("caricato_da"),
            "caricato_il": a["created_at"]}


def _testo(valore: Any) -> str | None:
    return None if valore is None else str(valore)


def rappresenta(pratica: Mapping[str, Any]) -> dict[str, Any]:
    """Riepilogo della pratica (elenco)."""
    return {
        "id": str(pratica["id"]), "tipo": pratica["tipo"], "stato": pratica["stato"],
        "versione": pratica["versione"], "ente": str(pratica["tenant_id"]),
        "motivo": pratica["motivo"], "pdv_id": _testo(pratica.get("pdv_id")),
        "pdv_risultato_id": _testo(pratica.get("pdv_risultato_id")),
        "richiede_nulla_osta": pratica["richiede_nulla_osta"],
        "richiede_autorizzazione_produttore": bool(
            pratica.get("richiede_autorizzazione_produttore")),
        "impronta_piano": pratica.get("impronta_piano"),
        "creata_da": _testo(pratica.get("creata_da")), "creata_ruolo": pratica.get("creata_ruolo"),
        "creata_il": pratica["created_at"], "aggiornata_il": pratica["updated_at"],
        "conclusa_il": pratica.get("conclusa_il"), "conteggi": pratica.get("conteggi") or {},
        "esecuzione": {
            "lavoro": pratica.get("lavoro"), "tentativi": pratica.get("tentativi"),
            "prossimo_tentativo": pratica.get("prossimo_tentativo") if pratica.get("lavoro")
            else None,
            "ultimo_errore": pratica.get("ultimo_errore"),
            "in_corso_fino_al": pratica.get("lease_scade_il") if pratica.get("lavoro") else None,
            "lavoro_sospeso": st.lavoro_sospeso(pratica),
        },
    }


async def leggi(identita: Identita, pratica_id: str) -> dict[str, Any]:
    pid = uuid_o_404(pratica_id)
    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            pratica = await repo.leggi_pratica(session, pid)
            if pratica is None or not puo_leggere(identita, str(pratica["tenant_id"])):
                raise non_trovata()
            storia = await repo.cronologia(session, pid)
            allegati = await repo.allegati(session, pid)
            conteggi = await repo.conteggi_oggetti(session, pid)
            approvazione = await _approvazione(session, identita, pratica)
    except SQLAlchemyError as exc:
        raise errore_db("lettura_pratica", exc) from None
    ctx = contesto_transizione(identita, pratica, allegati, storia)
    risposta = rappresenta(pratica)
    risposta.update({
        "dati": pratica.get("dati") or {},
        "cronologia": [{"da_stato": p["da_stato"], "a_stato": p["a_stato"],
                        "utente_id": p["utente_id"], "ruolo": p["ruolo"], "motivo": p["motivo"],
                        "allegati": p["allegati"], "il": p["created_at"]} for p in storia],
        "autorizzazioni": [{"stato": p["a_stato"], "utente_id": p["utente_id"],
                            "ruolo": p["ruolo"], "allegati": p["allegati"], "il": p["created_at"]}
                           for p in storia if p["a_stato"] in STATI_AUTORIZZAZIONE],
        "allegati": [_allegato_pubblico(a) for a in allegati],
        "oggetti": {"conteggi": conteggi,
                    "elenco": f"/api/v1/ciclo-vita/pratiche/{pid}/oggetti"},
        "azioni_consentite": st.azioni_consentite(pratica["tipo"], pratica["stato"], ctx),
        "in_parole": descrivi(pratica, conteggi),
        "approvazione": approvazione,
    })
    from app.application.ripresa import motivo_blocco_ripresa

    risposta["esecuzione"]["ripresa_consentita"] = (
        motivo_blocco_ripresa(identita, pratica) is None)
    return risposta


async def utente_creato_il(session: AsyncSession, identita: Identita,
                           tenant_id: Any) -> datetime | None:
    """Revisione R09 (Z1): quando è nato l'account (attivo, dell'ente) di chi agisce."""
    if not identita.user_id:
        return None
    try:
        uid = uuid.UUID(str(identita.user_id))
    except ValueError:
        return None
    return (await session.execute(text(
        "SELECT created_at FROM public.users WHERE id = :u AND tenant_id = :t AND attivo"),
        {"u": uid, "t": tenant_id})).scalar()


async def _approvazione(session: AsyncSession, identita: Identita,
                        pratica: Mapping[str, Any]) -> dict[str, Any] | None:
    """R09 (D23): per un annullamento in attesa, se chi guarda può approvarlo e,
    se no, perché (REGOLE §9.6: l'interfaccia non ricalcola i permessi)."""
    dati = dict(pratica.get("dati") or {})
    from app.application.puntuali import annullamento_bloccato
    from app.domain.permessi import persona

    if annullamento_bloccato(pratica):
        # revisione R09 (W1): fermo dopo il ricontrollo del sistema → solo «Respingi»
        puo = identita.conservatore or (persona(identita) and identita.ruolo == st.ADMIN_TENANT
                                        and str(identita.tenant_id) == str(pratica["tenant_id"]))
        return {"in_attesa": True, "solo_respingere": True, "ruoli_ammessi": [st.ADMIN_TENANT],
                "consentita": bool(puo), "motivo_blocco": None if puo else (
                    "Un annullamento fermo lo respinge un amministratore dell'ente o il "
                    "conservatore.")}
    if pratica["tipo"] != st.ANNULLAMENTO or pratica["stato"] != st.RICHIESTA \
            or not (dati.get("approvazione") or {}).get("richiesta"):
        return None
    from app.application.parametri import regola_annullamento
    from app.domain.permessi import motivo_blocco_approvazione

    regola = await regola_annullamento(pratica["tenant_id"], session=session)
    blocco = motivo_blocco_approvazione(
        identita, tenant_pratica=str(pratica["tenant_id"]),
        richiedente=pratica.get("creata_da") and str(pratica["creata_da"]),
        ruoli_approvazione=regola.ruoli_approvazione,
        richiesta_ruolo=pratica.get("creata_ruolo"),
        approvatore_creato_il=await utente_creato_il(session, identita, pratica["tenant_id"]),
        pratica_creata_il=pratica["created_at"])
    return {"in_attesa": True, "solo_respingere": False,
            "ruoli_ammessi": sorted(regola.ruoli_approvazione),
            "consentita": blocco is None, "motivo_blocco": None if blocco is None else blocco[1]}


async def elenca(identita: Identita, *, tipo: str | None, stato: str | None, ente: str | None,
                 cursore: str | None, limite: int, in_attesa: bool = False) -> dict[str, Any]:
    coppie = st.in_attesa_di(identita.ruolo) if in_attesa else None
    if coppie is not None and not coppie:
        # R06 (VD-C-128): nessuna pratica aspetta questo ruolo
        return {"items": [], "limit": limite, "cursore": None, "altri": False}
    perimetro = ente_del_perimetro(identita, ente)
    tenant = None
    if perimetro is not None:
        try:
            tenant = uuid.UUID(perimetro)
        except ValueError:
            return {"items": [], "limit": limite, "cursore": None, "altri": False}
        if not identita.conservatore and ente and ente != identita.tenant_id:
            # perimetro dell'ente: un altro ente dà un elenco vuoto (§14.4)
            return {"items": [], "limit": limite, "cursore": None, "altri": False}
    dopo = None
    if cursore:
        try:
            quando, ultimo = repo.decodifica_cursore("pratiche", cursore)
            dopo = (quando, uuid.UUID(ultimo)) if quando else None
        except (repo.CursoreNonValido, ValueError):
            raise ErroreApi("LC-400-CURSORE", "Cursore non valido per l'elenco delle pratiche"
                            ) from None
    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            righe = await repo.elenca(session, tenant_id=tenant, tipo=tipo, stato=stato,
                                      dopo=dopo, limite=limite + 1, coppie=coppie)
    except SQLAlchemyError as exc:
        raise errore_db("elenco_pratiche", exc) from None
    altri = len(righe) > limite
    righe = righe[:limite]
    prossimo = repo.codifica_cursore("pratiche", righe[-1]["created_at"], righe[-1]["id"]) \
        if altri and righe else None
    return {"items": [rappresenta(r) for r in righe], "limit": limite, "cursore": prossimo,
            "altri": altri}


async def elenca_oggetti(identita: Identita, pratica_id: str, *, esito: str | None,
                         cursore: str | None, limite: int) -> dict[str, Any]:
    pid = uuid_o_404(pratica_id)
    dopo = 0
    if cursore:
        try:
            _, ultimo = repo.decodifica_cursore(f"oggetti:{pid}", cursore)
            dopo = int(ultimo)
        except (repo.CursoreNonValido, ValueError):
            raise ErroreApi("LC-400-CURSORE", "Cursore non valido per gli oggetti della pratica"
                            ) from None
    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            pratica = await repo.leggi_pratica(session, pid)
            if pratica is None or not puo_leggere(identita, str(pratica["tenant_id"])):
                raise non_trovata()
            righe = await repo.oggetti(session, pid, esito=esito, dopo_id=dopo,
                                       limite=limite + 1)
    except SQLAlchemyError as exc:
        raise errore_db("oggetti_pratica", exc) from None
    altri = len(righe) > limite
    righe = righe[:limite]
    prossimo = repo.codifica_cursore(f"oggetti:{pid}", None, righe[-1]["id"]) \
        if altri and righe else None
    return {"items": [{k: v for k, v in r.items() if k != "id"} for r in righe],
            "limit": limite, "cursore": prossimo, "altri": altri}


# ── Transizioni manuali ──────────────────────────────────────────────────

LAVORO_ESEGUI = st.LAVORO_ESEGUI


async def riallinea_nulla_osta(pratica_id: uuid.UUID, adesso: datetime,
                               identita: Identita) -> bool:
    """Revisione 2 di R05 (S5): la natura dell'ente si RIVALUTA quando uno
    scarto chiede di diventare ESEGUIBILE (``richiede_nulla_osta`` era fissato
    alla creazione). Solo verso il caso più prudente: un ente che ORA è (o si
    tratta come) pubblica amministrazione richiede il nulla osta della
    Soprintendenza anche se alla creazione era dichiarato PRIVATO; mai il
    contrario (una pratica che lo richiedeva continua a richiederlo). In una
    transazione propria, con voce d'audit: resta anche se la transizione
    chiesta viene poi rifiutata. True se il valore è cambiato.

    Revisione 3 di R05 (T8): solo DOPO i controlli d'accesso, con il PdA
    bloccato: perimetro di lettura (un utente di un altro ente non fa scrivere
    nulla su una pratica che non vede: la transizione risponderà 404) e ruolo
    ammesso per la transizione verso ESEGUIBILE dallo stato attuale; la voce
    d'audit porta l'utente che l'ha causata."""
    from app.domain import scarto as dom_scarto
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        pratica = await repo.leggi_pratica(session, pratica_id, per_aggiornare=True)
        if pratica is None or pratica["tipo"] != st.SCARTO or pratica["richiede_nulla_osta"]:
            return False
        if not puo_leggere(identita, str(pratica["tenant_id"])):
            return False
        verso = st.macchina(pratica["tipo"]).cerca(pratica["stato"], st.ESEGUIBILE)
        if verso is None or verso.automatica or identita.ruolo not in verso.ruoli:
            return False
        ente = await repo.ente(session, pratica["tenant_id"]) or {}
        if not dom_scarto.richiede_nulla_osta(ente.get("natura")):
            return False
        await repo.aggiorna_pratica(session, pratica_id, adesso, richiede_nulla_osta=True)
        await registra_audit(session, evento="ciclo_vita.scarto.nulla_osta_richiesto",
                             tenant_id=str(pratica["tenant_id"]), user_id=identita.user_id,
                             dettagli={"pratica_id": str(pratica_id), "ruolo": identita.ruolo,
                                       "natura_attuale": ente.get("natura"),
                                       "stato": pratica["stato"],
                                       "motivo": "natura dell'ente rivalutata prima di "
                                                 "ESEGUIBILE: pubblica amministrazione"})
    logger.warning("scarto_nulla_osta_ora_richiesto", pratica_id=str(pratica_id))
    return True


async def transizione(identita: Identita, pratica_id: str, *, stato_atteso: str,
                      nuovo_stato: str, allegati: Sequence[str], motivo: str,
                      conferma: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``POST /pratiche/{id}/transizione``. Con lo stato atteso (409
    LC-409-STATO-CAMBIATO se è cambiato), ruolo, allegati obbligatori. Una
    transizione «di esecuzione» non cambia subito lo stato: chiede al
    lavoratore di eseguire la pratica (risposta con ``esecuzione_richiesta``)."""
    pid = uuid_o_404(pratica_id)
    citati = [str(uuid_o_404(a, "LC-404-ALLEGATO", "Allegato non trovato")) for a in allegati]
    adesso = adesso_utc()
    from libs.shared.db import get_async_session

    try:
        if nuovo_stato == st.ESEGUIBILE:
            # S5: natura dell'ente rivalutata al passaggio verso ESEGUIBILE
            await riallinea_nulla_osta(pid, adesso, identita)
        async with get_async_session() as session:
            pratica = await repo.leggi_pratica(session, pid, per_aggiornare=True)
            if pratica is None or not puo_leggere(identita, str(pratica["tenant_id"])):
                raise non_trovata()
            if pratica["stato"] != stato_atteso:
                raise ErroreApi("LC-409-STATO-CAMBIATO",
                                f"Lo stato della pratica è {pratica['stato']}, non {stato_atteso}",
                                dettagli={"errore": "STATO_CAMBIATO",
                                          "stato_attuale": pratica["stato"],
                                          "versione": pratica["versione"]})
            allegati_pratica = await repo.allegati(session, pid)
            storia = await repo.cronologia(session, pid)
            noti = {str(a["id"]) for a in allegati_pratica}
            estranei = [c for c in citati if c not in noti]
            if estranei:
                raise ErroreApi("LC-404-ALLEGATO", "Allegato non trovato in questa pratica",
                                dettagli={"allegati": estranei})
            ctx = dataclasses.replace(
                contesto_transizione(identita, pratica, allegati_pratica, storia, citati),
                motivo=motivo)
            # Lavoro in corso (verifica, esecuzione): si attende che finisca;
            # respingere è possibile solo finché il lavoratore non ha AVVIATO
            # l'esecuzione (revisione R05, R1: lock della pratica qui e in
            # ``segna_avvio_esecuzione``, quindi le due cose non si incrociano).
            blocco = st.motivo_blocco_transizione(pratica["tipo"], pratica["stato"], nuovo_stato,
                                                  ctx)
            if blocco is not None:
                dettagli = None
                if blocco[0] == "LC-409-IN-ESECUZIONE":
                    dettagli = {"lavoro": pratica["lavoro"],
                                "esecuzione_avviata": ctx.esecuzione_avviata,
                                "ultimo_errore": pratica["ultimo_errore"]}
                raise ErroreApi(blocco[0], blocco[1], dettagli=dettagli)
            t = st.macchina(pratica["tipo"]).cerca(pratica["stato"], nuovo_stato)
            assert t is not None
            attore = Attore.da_identita(identita)
            # R17 (VD-P-164): decisione sensibile → conferma firmata dal gateway registrata
            # nella stessa transazione (il lavoratore la riverifica prima di eseguire)
            from app.application import conferme

            azione = conferme.azione_transizione(pratica["tipo"], pratica["stato"], nuovo_stato,
                                                 t.esecuzione)
            if azione is not None:
                conferma_id = await conferme.registra(
                    session, identita, azione=azione, pratica=pratica, conferma=conferma,
                    campi=conferme.cd.campi_impronta(azione, pratica),
                    versione_attesa=int(pratica["versione"]), adesso=adesso)
                pratica = con_conferma(pratica, azione, conferma_id)
            if t.esecuzione:
                aggiornata = await richiedi_esecuzione(
                    session, pratica, t, utente_id=identita.user_id, ruolo=identita.ruolo,
                    motivo=motivo, adesso=adesso)
                esito = {"esecuzione_richiesta": True}
            else:
                aggiornata = await applica_transizione(
                    session, pratica, nuovo_stato, attore, motivo=motivo, allegati=citati,
                    adesso=adesso)
                esito = {"esecuzione_richiesta": False}
    except SQLAlchemyError as exc:
        raise errore_db("transizione_pratica", exc) from None
    return {**rappresenta(aggiornata), **esito}


def con_conferma(pratica: Mapping[str, Any], azione: str,
                 conferma_id: str | None) -> dict[str, Any]:
    """R17: la pratica letta con la conferma appena collegata in ``dati.conferme`` (chi
    riscrive ``dati`` nella stessa transazione non la perde)."""
    if conferma_id is None:
        return dict(pratica)
    dati = dict(pratica.get("dati") or {})
    dati["conferme"] = {**dict(dati.get("conferme") or {}), azione: conferma_id}
    return {**pratica, "dati": dati}


async def richiedi_esecuzione(session: AsyncSession, pratica: Mapping[str, Any],
                              t: st.Transizione, *, utente_id: str | None, ruolo: str,
                              motivo: str, adesso: datetime) -> dict[str, Any]:
    """Una transizione «di esecuzione» chiesta (dal ruolo ammesso o, R09, dal
    SISTEMA per la ripresa automatica): lo stato resta, il lavoratore riceve il
    lavoro ``t.lavoro``. Pratica già bloccata dal chiamante.

    ESEGUI: storia delle esecuzioni (S3: una ripresa dopo ESEGUITA_CON_ECCEZIONI
    conserva le precedenti; ogni giro ha il suo verbale). CHIUDI (R09, D20):
    richiesta di chiusura con eccezioni in ``dati.chiusura``."""
    dati = dict(pratica["dati"] or {})
    richiesta = {"richiesta_da": utente_id, "ruolo": ruolo,
                 "richiesta_il": adesso.isoformat(), "motivo": motivo}
    if t.lavoro == st.LAVORO_CHIUDI:
        dati["chiusura"] = richiesta
    else:
        precedente = dict(dati.get("esecuzione") or {})
        giro = 1
        if precedente.get("conclusa_il"):
            dati["esecuzioni_precedenti"] = [
                *(dati.get("esecuzioni_precedenti") or []), precedente]
            giro = int(precedente.get("giro") or 1) + 1
        dati["esecuzione"] = {**richiesta, "giro": giro}
    await repo.aggiorna_pratica(session, pratica["id"], adesso, lavoro=t.lavoro,
                                prossimo_tentativo=adesso, tentativi=0,
                                ultimo_errore=None, dati=dati)
    await registra_audit(session, evento="ciclo_vita.esecuzione_richiesta",
                         tenant_id=str(pratica["tenant_id"]), user_id=utente_id,
                         dettagli={"pratica_id": str(pratica["id"]), "tipo": pratica["tipo"],
                                   "motivo": motivo, "ruolo": ruolo, "lavoro": t.lavoro,
                                   "verso": t.a})
    return {**pratica, "lavoro": t.lavoro, "dati": dati}


async def segna_avvio_esecuzione(pratica_id: uuid.UUID, adesso: datetime
                                 ) -> dict[str, Any] | None:
    """Il lavoratore AVVIA l'esecuzione (revisione R05, R1): con la pratica
    bloccata (``FOR UPDATE``, lo stesso lock di ``transizione``) controlla che
    l'esecuzione sia ancora chiesta e la marca avviata (``dati.esecuzione.avviata_il``).
    Da qui nessuna transizione manuale è ammessa (``motivo_blocco_transizione``).
    Restituisce la pratica aggiornata, o ``None`` se nel frattempo è stata
    respinta (il lavoratore non fa nulla)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        pratica = await repo.leggi_pratica(session, pratica_id, per_aggiornare=True)
        if pratica is None or pratica["lavoro"] != LAVORO_ESEGUI \
                or pratica["stato"] in st.macchina(pratica["tipo"]).finali:
            return None
        if esecuzione_avviata(pratica):
            return pratica
        dati = dict(pratica["dati"] or {})
        esecuzione = dict(dati.get("esecuzione") or {})
        esecuzione["avviata_il"] = adesso.isoformat()
        dati["esecuzione"] = esecuzione
        await repo.aggiorna_pratica(session, pratica_id, adesso, dati=dati)
        await registra_audit(session, evento="ciclo_vita.esecuzione_avviata",
                             tenant_id=str(pratica["tenant_id"]),
                             dettagli={"pratica_id": str(pratica_id), "tipo": pratica["tipo"],
                                       "stato": pratica["stato"]})
        return {**pratica, "dati": dati}


# ── Allegati caricati dagli utenti ───────────────────────────────────────

def ruoli_caricamento(tipo_pratica: str, stato: str, tipo_allegato: str) -> frozenset[str]:
    """Chi carica un allegato: i ruoli delle transizioni dallo stato attuale che
    lo richiedono; ALTRO: chi può muovere la pratica dallo stato attuale."""
    ruoli: set[str] = set()
    for t in st.macchina(tipo_pratica).uscite(stato):
        if t.automatica:
            continue
        if tipo_allegato == st.ALLEGATO_ALTRO or tipo_allegato in t.allegati:
            ruoli.update(t.ruoli)
    return frozenset(ruoli)


async def firma_del_documento(f: IO[bytes], nome_file: str, tipo: str, tenant_id: Any, *,
                              pratica: Mapping[str, Any] | None = None,
                              ) -> tuple[dict[str, Any], list[str], str | None]:
    """R08 (D01, D22): esito VERIFICATO della firma di un allegato al caricamento
    (livello ``CICLO_VITA_FIRME_LIVELLO`` dell'ente), problemi bloccanti e avviso.
    Verificatore non raggiungibile: firma solo rilevata, la verifica la fa il
    lavoratore prima dell'esecuzione (che resta il controllo di sicurezza)."""
    from app.application import firme
    from libs.shared.firme import ErroreVerificatoreNonDisponibile

    f.seek(0)
    contenuto = await asyncio.to_thread(f.read)
    f.seek(0)
    parametri = await firme.parametri_firme(tenant_id)
    try:
        esito = await firme.verifica_documento(contenuto, nome_file, parametri.livello)
    except ErroreVerificatoreNonDisponibile as exc:
        esito = firme.solo_rilevata(contenuto, nome_file, str(exc))
    problemi, avviso = firme.problemi_documento(tipo, esito, parametri)
    if not problemi and pratica is not None and pratica["tipo"] in firme.TIPI_PRATICA_LEGATI:
        # R12a (VD-C-145), R12b (VD-C-160: restituzione): l'identificativo della
        # pratica nel contenuto firmato (al caricamento sempre richiesto, anche per
        # le pratiche in transizione)
        problemi, avviso_rif = firme.problemi_riferimento_pratica(
            tipo, contenuto, esito, pratica["id"], parametri)
        avviso = "; ".join(a for a in (avviso, avviso_rif) if a) or None
    esito["presente"] = firme.firmato(esito)
    if avviso:
        esito["avviso"] = avviso
    return esito, problemi, avviso


def chiave_documento(tenant_id: Any, pratica_id: Any, tipo: str, nome: str, sha256: str) -> str:
    sicuro = _NOME_SICURO.sub("_", nome)[:120].strip("._") or "documento"
    return f"pratiche/{tenant_id}/{pratica_id}/{tipo.lower()}/{sha256[:16]}-{sicuro}"


@dataclass(frozen=True)
class DocumentoCaricato:
    """Documento della pratica già scritto nel WORM e non ancora registrato
    (revisione 3 di R05, T7: ``carica_documento`` fuori da ogni transazione,
    poi ``registra_documento`` in una transazione breve)."""

    tipo: str
    nome_file: str
    content_type: str
    bucket: str
    chiave: str
    scritto: Any
    conserva_fino_a: datetime


async def carica_documento(pratica: Mapping[str, Any], *, tipo: str, nome_file: str,
                           content_type: str, f: IO[bytes],
                           adesso: datetime) -> DocumentoCaricato:
    """Scrive un documento della pratica nel WORM (retention
    ``RETENTION_PERMANENTE_ANNI``, R09) SENZA sessione del database: un upload grande
    (export dei registri di un ente, verbale con migliaia di PdA) non tiene
    aperta una transazione «idle» oltre il timeout imposto ai login dei servizi
    (S7). La chiave dipende dall'impronta: ripetibile."""
    s3 = storage.client()
    bucket = storage.bucket_worm()
    sha, _ = await asyncio.to_thread(storage.sha256_file, f)
    chiave = chiave_documento(pratica["tenant_id"], pratica["id"], tipo, nome_file, sha)
    from app.application.parametri import anni_retention

    fino = aggiungi_anni(adesso, await anni_retention(pratica["tenant_id"]))
    scritto = await asyncio.to_thread(storage.scrivi_worm_da_file, s3, bucket, chiave, f,
                                      content_type=content_type, conserva_fino_a=fino,
                                      adesso=adesso)
    return DocumentoCaricato(tipo=tipo, nome_file=nome_file, content_type=content_type,
                             bucket=bucket, chiave=chiave, scritto=scritto,
                             conserva_fino_a=fino)


async def registra_documento(session: AsyncSession, pratica: Mapping[str, Any],
                             doc: DocumentoCaricato, *, caricato_da: str | None = None,
                             firma: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Registra come allegato un documento già scritto da ``carica_documento``."""
    scritto = doc.scritto
    allegato_id = await repo.inserisci_allegato(
        session, pratica_id=pratica["id"], tenant_id=pratica["tenant_id"], tipo=doc.tipo,
        nome_file=doc.nome_file[:255], content_type=doc.content_type,
        dimensione=scritto.dimensione, sha256=scritto.sha256, bucket=doc.bucket,
        chiave=doc.chiave, version_id=scritto.version_id, conserva_fino_a=doc.conserva_fino_a,
        firma=dict(firma or {}), caricato_da=caricato_da)
    return {"id": str(allegato_id), "tipo": doc.tipo, "sha256": scritto.sha256,
            "dimensione": scritto.dimensione, "nome_file": doc.nome_file, "chiave": doc.chiave}


async def archivia_documento(session: AsyncSession, pratica: Mapping[str, Any], *, tipo: str,
                             nome_file: str, content_type: str, f: IO[bytes],
                             adesso: datetime, caricato_da: str | None = None,
                             firma: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Scrive un documento PICCOLO della pratica nel WORM e lo registra nella
    transazione del chiamante (allegati degli utenti, entro ``ALLEGATI_MAX_MB``;
    manifesto). Per documenti di dimensione non limitata: ``carica_documento``
    fuori dalla transazione, poi ``registra_documento`` (T7)."""
    doc = await carica_documento(pratica, tipo=tipo, nome_file=nome_file,
                                 content_type=content_type, f=f, adesso=adesso)
    return await registra_documento(session, pratica, doc, caricato_da=caricato_da, firma=firma)


async def _altro_con_impronta_autorizzativa(f: IO[bytes], pratica: Mapping[str, Any]
                                            ) -> list[str]:
    """Revisione R12a (VD-C-145): un allegato ALTRO con l'impronta di un documento
    autorizzativo (autorizzazione, nulla osta) già presente nell'ente si rifiuta."""
    from app.application import firme
    from libs.shared.db import get_async_session

    f.seek(0)
    sha, _ = await asyncio.to_thread(storage.sha256_file, f)
    f.seek(0)
    async with get_async_session() as session:
        trovato = (await session.execute(text(
            "SELECT 1 FROM public.pratiche_allegati WHERE tenant_id = :t AND sha256 = :h "
            "AND tipo = ANY(:tipi) LIMIT 1"),
            {"t": pratica["tenant_id"], "h": sha,
             "tipi": sorted(firme.ALLEGATI_LEGATI_ALLA_PRATICA)})).first()
    return (["documento autorizzativo già presente in una pratica: non si allega come ALTRO"]
            if trovato is not None else [])


async def carica_allegato(identita: Identita, pratica_id: str, *, tipo: str, nome_file: str,
                          content_type: str, lettore: Any) -> dict[str, Any]:
    """``POST /pratiche/{id}/allegati``: lettura a blocchi su file temporaneo
    (limite ``ALLEGATI_MAX_MB``), impronta, scrittura WORM, riga immutabile."""
    pid = uuid_o_404(pratica_id)
    if tipo not in st.ALLEGATI_CARICABILI:
        raise ErroreApi("LC-422-ALLEGATO-TIPO",
                        f"Tipo di allegato non caricabile: {tipo} (ammessi: "
                        f"{', '.join(sorted(st.ALLEGATI_CARICABILI))})")
    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            pratica = await repo.leggi_pratica(session, pid)
    except SQLAlchemyError as exc:
        raise errore_db("allegato_pratica", exc) from None
    if pratica is None or not puo_leggere(identita, str(pratica["tenant_id"])):
        raise non_trovata()
    if pratica["stato"] in st.macchina(pratica["tipo"]).finali:
        raise ErroreApi("LC-409-TRANSIZIONE", "La pratica è conclusa: non accetta allegati")
    ammessi = ruoli_caricamento(pratica["tipo"], pratica["stato"], tipo)
    if identita.ruolo not in ammessi or (not identita.conservatore
                                         and identita.tenant_id != str(pratica["tenant_id"])):
        raise ErroreApi("LC-403-RUOLO", f"Il ruolo {identita.ruolo} non può caricare un "
                                        f"allegato {tipo} in questo stato")
    limite = settings.allegati_max_mb * 1024 * 1024
    adesso = adesso_utc()
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as f:
        letti = 0
        while blocco := await lettore.read(storage.BLOCCO):
            letti += len(blocco)
            if letti > limite:
                raise ErroreApi("LC-413-ALLEGATO",
                                f"Allegato oltre {settings.allegati_max_mb} MB")
            f.write(blocco)
        if letti == 0:
            raise ErroreApi("LC-422-ALLEGATO-TIPO", "Allegato vuoto")
        f.seek(0)
        firma, problemi, _ = await firma_del_documento(f, nome_file, tipo, pratica["tenant_id"],
                                                       pratica=pratica)
        if not problemi and tipo == st.ALLEGATO_ALTRO:
            problemi = await _altro_con_impronta_autorizzativa(f, pratica)
        if problemi:
            raise ErroreApi("LC-422-FIRMA", f"Firma dell'allegato {tipo} non accettabile: "
                            + "; ".join(problemi)[:500],
                            dettagli={"firma": {k: firma.get(k) for k in (
                                "formato", "indicazione", "in_parole", "firme")}})
        try:
            async with get_async_session() as session:
                pratica = await repo.leggi_pratica(session, pid, per_aggiornare=True)
                assert pratica is not None
                allegato = await archivia_documento(
                    session, pratica, tipo=tipo, nome_file=nome_file, content_type=content_type,
                    f=f, adesso=adesso, caricato_da=identita.user_id, firma=firma)
                await registra_audit(session, evento="ciclo_vita.allegato",
                                     tenant_id=str(pratica["tenant_id"]),
                                     user_id=identita.user_id,
                                     dettagli={"pratica_id": str(pid), "tipo": tipo,
                                               "sha256": allegato["sha256"],
                                               "dimensione": allegato["dimensione"]})
        except SQLAlchemyError as exc:
            raise errore_db("allegato_pratica", exc) from None
        except storage.ErroreStorageConservativo as exc:
            raise errore_registrato("LC-503-STORAGE", exc, evento="allegato_worm") from None
    allegato.pop("chiave", None)
    return {**allegato, "firma": firma}


__all__ = [
    "Attore", "LAVORO_ESEGUI", "SISTEMA", "TOPIC_ANNULLATO", "TOPIC_CAMBIO_STATO",
    "TOPIC_CICLO_VITA", "TOPIC_RETTIFICATO", "TOPIC_RIVERSAMENTO_PRONTO",
    "TOPIC_SCARTO_ESEGUITO", "DocumentoCaricato", "adesso_utc", "applica_transizione",
    "archivia_documento", "carica_documento", "registra_documento",
    "carica_allegato", "elenca", "elenca_oggetti", "esecuzione_avviata", "evento", "leggi",
    "rappresenta", "richiedi_esecuzione", "segna_avvio_esecuzione", "transizione",
]
