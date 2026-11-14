# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/periodico.py — lavoro periodico del lavoratore di ciclo-vita (R09).

Riferimenti: decisioni D04 (rinnovo del blocco WORM), D06 (minimizzazione dopo
lo scarto), D20 (ripresa automatica degli scarti incompleti), D21 (proposta di
scarto automatica); REGOLE_SVILUPPO §8.1 (parametri dal motore), §10.2,
§12.9 (lease), §12.11, §15.8, §22.

Quattro passi, ognuno con un lease in ``ciclo_vita_periodico`` (una replica
per passo; un lease scaduto si riprende) e il suo intervallo (impostazioni
``PERIODICO_*``). Revisione R09 (nota sul lease): il passo rinnova il lease prima
di OGNI elemento (``tieni_lease``) e si ferma con ``LeasePassoPerso`` se un'altra
replica l'ha preso (lease scaduto durante un passo lungo): due repliche non
trattano lo stesso elemento dopo la perdita del lease; la proposta di scarto
ha in più un passo per ente con la frequenza del parametro dell'ente. Un errore
di un passo non ferma gli altri: si registra e il passo si riprova al controllo successivo.
R12a (VD-C-151): ogni presa del lease alza la ``generazione`` (fencing token) e
OGNI scrittura dei fatti la riverifica nella propria transazione
(``verifica_fencing``, riga del passo FOR SHARE fino al commit): un elemento più
lungo del lease non scrive più dopo che un'altra replica ha ripreso il passo.

1. PROPOSTE (D21): per ogni ente attivo con SCARTO_PROPOSTA_AUTOMATICA = ATTIVA,
   ogni SCARTO_PROPOSTA_FREQUENZA_GIORNI, una pratica di scarto in BOZZA con il
   piano dei PdA scaduti a oggi (classi escluse: SCARTO_PROPOSTA_CLASSI_ESCLUSE),
   creata con la STESSA funzione della proposta manuale (``scarto.crea_bozza``,
   ruolo SISTEMA senza utente) più il fatto ``PROPOSTA_GENERATA`` (solo il ruolo
   del lavoratore lo scrive: prova per la verifica della catena) e l'avviso
   all'ente. Nessuna proposta se ce n'è già una automatica in bozza o se nulla
   è scartabile. L'esecuzione resta manuale (proposta, autorizzazione firmata,
   nulla osta, esecuzione del conservatore).
2. RIPRESE (D20): uno scarto ESEGUITA_CON_ECCEZIONI senza lavoro, con meno di
   SCARTO_RIPRESE_AUTOMATICHE riprese automatiche, riprende da solo se almeno
   uno dei suoi PdA incompleti NON ha più versioni bloccate nello storage
   (legal hold tolto, retention scaduta): stessa richiesta d'esecuzione del
   conservatore (``pratiche.richiedi_esecuzione``, ruolo SISTEMA), avviso.
3. MINIMIZZAZIONE (D06): un PdA eliminato per intero (tombstone nel registro)
   da più di SCARTO_MINIMIZZAZIONE_GIORNI conserva solo l'elenco di scarto
   (identificativo, classe, date, impronta, pratica e verbale; il RdV
   definitivo per le prove): oggetto e protocollo del PdV, metadati del PdV,
   nomi dei file e metadati dell'allegato 5 si cancellano in UNA transazione
   con il fatto ``METADATI_MINIMIZZATI``; poi la foglia «minimizzazione».
   R12a (VD-C-149): nella stessa transazione si tolgono i collegamenti ai
   fascicoli e il PdA torna nella coda dell'indicizzazione (l'indice di ricerca
   si riscrive con i dati minimizzati). Gli eventi per i clienti già pubblicati e
   i dettagli dell'audit (registro di legge) restano: VD-P-118.
4. RINNOVO (D04): PdA permanenti (``anni_conservazione = 999``) e allegati,
   verbali, atti con il blocco WORM che termina prima di adesso +
   RETENTION_RINNOVO_SOGLIA_ANNI: ogni versione si estende fino ad adesso +
   RETENTION_PERMANENTE_ANNI, MAI in riduzione (``storage.estendi_retention``);
   fatto ``RINNOVATA`` / ``RINNOVATA_ALLEGATO`` e, per i PdA, la foglia
   «rinnovo_retention» nel registro.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.application import avvisi, parametri, scarto
from app.application import pratiche as pr
from app.config import settings
from app.domain import scarto as dom
from app.domain import stati as st
from app.infrastructure import repository as repo
from app.infrastructure import storage
from app.infrastructure.registro import RegistroImpronte
from libs.catena.canonico import json_canonico
from libs.shared.errori import ErroreApi
from libs.shared.security.audit import registra_audit
from libs.shared.storage.worm import aggiungi_anni
from libs.verumdoc_rules.impostazioni import giorno_locale
from libs.verumdoc_rules.parametri_servizio import (
    PROPOSTA_ATTIVA,
    RETENTION_PERMANENTE_ANNI,
    RETENTION_RINNOVO_SOGLIA_ANNI,
    SCARTO_MINIMIZZAZIONE_GIORNI,
    SCARTO_PROPOSTA_AUTOMATICA,
    SCARTO_PROPOSTA_CLASSI_ESCLUSE,
    SCARTO_PROPOSTA_FREQUENZA_GIORNI,
    SCARTO_RIPRESE_AUTOMATICHE,
)

logger = structlog.get_logger(__name__)

PASSO_PROPOSTE = "proposte_scarto"
PASSO_RIPRESE = "riprese_scarto"
PASSO_MINIMIZZAZIONE = "minimizzazione"
PASSO_RINNOVO = "rinnovo_retention"
#: Stati del PdA con oggetti nel WORM da tenere bloccati (non in scarto)
STATI_CON_OGGETTI = ("SU_STORAGE", "IN_CATENA", "CONSERVATO", "ESIBIZIONE", "FIRMATO",
                     "TIMESTAMPATO")
#: Testo che sostituisce l'oggetto del PdV minimizzato (colonna obbligatoria)
OGGETTO_MINIMIZZATO = "[minimizzato dopo lo scarto]"


def _impronta(dati: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_canonico(dict(dati))).hexdigest()


# ── Lease dei passi ──────────────────────────────────────────────────────

class LeasePassoPerso(RuntimeError):
    """Un'altra replica ha preso il passo (lease scaduto): il passo si ferma."""


class RinnovoNonPossibile(RuntimeError):
    """Revisione R09 (Z4): configurazione che non permette il rinnovo (il passo
    risulta NON fatto, si riprova e scatta l'allarme)."""


#: (nome del passo, detentore, generazione) del passo in corso in questo task.
#: R12a (VD-C-151): la generazione è il FENCING TOKEN: cresce a ogni presa del
#: lease e ogni scrittura dei fatti la riverifica nella propria transazione
_LEASE: ContextVar[tuple[str, str, int] | None] = ContextVar("ciclo_vita_periodico_lease",
                                                              default=None)


async def verifica_fencing(session: Any) -> None:
    """R12a (VD-C-151): nella transazione di una scrittura del passo, il lease è
    ancora nostro con la STESSA generazione (riga bloccata FOR SHARE fino al
    commit: un'altra replica non lo riprende a metà). Altrimenti
    ``LeasePassoPerso`` e la transazione si annulla. Fuori da ``_esegui`` (test
    di unità) non fa nulla."""
    corrente = _LEASE.get()
    if corrente is None:
        return
    nome, detentore, generazione = corrente
    ancora = (await session.execute(text(
        "SELECT 1 FROM public.ciclo_vita_periodico WHERE nome = :n AND lease_detentore = :d "
        "AND generazione = :g FOR SHARE"),
        {"n": nome[:200], "d": detentore[:200], "g": generazione})).first()
    if ancora is None:
        raise LeasePassoPerso(f"lease del passo {nome} perso (generazione {generazione})")


async def tieni_lease() -> None:
    """Rinnova il lease del passo in corso prima di un elemento; ``LeasePassoPerso``
    se non è più nostro. Fuori da ``_esegui`` (test di unità) non fa nulla."""
    corrente = _LEASE.get()
    if corrente is None:
        return
    from libs.shared.db import get_async_session

    nome, detentore, generazione = corrente
    async with get_async_session() as session:
        r = await session.execute(text(
            "UPDATE public.ciclo_vita_periodico SET lease_scade_il = GREATEST(lease_scade_il, "
            ":scade) WHERE nome = :n AND lease_detentore = :d AND generazione = :g"),
            {"n": nome[:200], "d": detentore[:200], "g": generazione,
             "scade": datetime.now(UTC) + timedelta(seconds=settings.periodico_lease_secondi)})
    if not r.rowcount:
        raise LeasePassoPerso(f"lease del passo {nome} perso")

async def prendi(nome: str, detentore: str, adesso: datetime, intervallo: timedelta
                 ) -> int | None:
    """Il passo ``nome`` è dovuto (ultimo giro più vecchio di ``intervallo``) e
    nessun'altra replica lo sta facendo: lease preso, con la NUOVA generazione
    (fencing token, VD-C-151); None altrimenti."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.ciclo_vita_periodico (nome) VALUES (:n) "
            "ON CONFLICT (nome) DO NOTHING"), {"n": nome[:200]})
        preso = (await session.execute(text(
            "UPDATE public.ciclo_vita_periodico SET lease_detentore = :d, "
            "lease_scade_il = :scade, aggiornato_il = :adesso, generazione = generazione + 1 "
            "WHERE nome = :n AND (lease_scade_il IS NULL OR lease_scade_il < :adesso) "
            "AND (ultimo_giro IS NULL OR ultimo_giro <= :dovuto) RETURNING generazione"),
            {"n": nome[:200], "d": detentore[:200], "adesso": adesso,
             "scade": adesso + timedelta(seconds=settings.periodico_lease_secondi),
             "dovuto": adesso - intervallo})).first()
    return int(preso[0]) if preso is not None else None


async def concludi(nome: str, detentore: str, adesso: datetime, esito: Mapping[str, Any], *,
                   giro_fatto: bool = True) -> None:
    """Rilascia il lease; con ``giro_fatto`` il passo non è più dovuto fino al
    prossimo intervallo (senza, si riprova al controllo successivo)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.ciclo_vita_periodico SET lease_detentore = NULL, "
            "lease_scade_il = NULL, aggiornato_il = :adesso, "
            "ultimo_esito = CAST(:e AS jsonb), "
            "ultimo_giro = CASE WHEN CAST(:fatto AS boolean) THEN :adesso ELSE ultimo_giro END "
            "WHERE nome = :n AND lease_detentore = :d"),
            {"n": nome[:200], "d": detentore[:200], "adesso": adesso, "fatto": giro_fatto,
             "e": json.dumps(dict(esito), default=str)})


async def _esegui(nome: str, detentore: str, adesso: datetime, intervallo: timedelta,
                  passo: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any] | None:
    generazione = await prendi(nome, detentore, adesso, intervallo)
    if generazione is None:
        return None
    token = _LEASE.set((nome, detentore, generazione))
    try:
        esito = await passo()
    except LeasePassoPerso:
        logger.warning("ciclo_vita_periodico_lease_perso", passo=nome)
        return {"lease_perso": True}
    except Exception as exc:  # noqa: BLE001 — registrato; il passo si riprova
        logger.exception("ciclo_vita_periodico_errore", passo=nome, errore=type(exc).__name__)
        await concludi(nome, detentore, adesso, {"errore": type(exc).__name__,
                                                 "messaggio": str(exc)[:300]}, giro_fatto=False)
        return {"errore": type(exc).__name__, "messaggio": str(exc)[:300]}
    finally:
        _LEASE.reset(token)
    await concludi(nome, detentore, adesso, esito)
    return esito


# ── 1. Proposte di scarto automatiche (D21) ──────────────────────────────

async def proponi(ente: uuid.UUID, classi_escluse: tuple[str, ...], adesso: datetime
                  ) -> dict[str, Any]:
    """Una proposta di scarto in BOZZA per l'ente (vedi modulo). Esito in parole."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        aperta = (await session.execute(text(
            "SELECT id::text FROM public.pratiche WHERE tenant_id = :t AND tipo = 'SCARTO' "
            "AND stato = 'BOZZA' AND creata_ruolo = :s LIMIT 1"),
            {"t": ente, "s": st.SISTEMA})).scalar()
        if aperta:
            return {"esito": "PROPOSTA_GIA_APERTA", "pratica": aperta}
        classi: list[str] = []
        if classi_escluse:
            tutte = (await session.execute(text(
                "SELECT DISTINCT codice FROM public.classi_documentali "
                "WHERE (tenant_id = :t OR tenant_id IS NULL) "
                "AND NOT conservazione_permanente ORDER BY codice"),
                {"t": ente})).scalars().all()
            classi = sorted(set(tutte) - set(classi_escluse))
            if not classi:
                return {"esito": "TUTTE_LE_CLASSI_ESCLUSE"}
        criteri = scarto.normalizza_criteri(
            {"classi": classi, "scadenza_entro": giorno_locale(adesso)})
        piano = await scarto.calcola_piano(session, ente, criteri, adesso)
    if not piano["selezionabili"]:
        return {"esito": "NULLA_DA_SCARTARE", "bloccati": piano["bloccati"]}
    giorno = giorno_locale(adesso).isoformat()
    async with get_async_session() as session:
        await verifica_fencing(session)
        dati_ente = await repo.ente(session, ente) or {}
        pratica_id = await scarto.crea_bozza(
            session, tenant=ente, canonici=criteri, dati_ente=dati_ente,
            motivo=f"Proposta di scarto automatica del {giorno}: pacchetti con il termine di "
                   "conservazione scaduto (da rivedere, autorizzare ed eseguire)",
            creata_da=None, creata_ruolo=st.SISTEMA, metodo="lavoratore",
            idempotency_key=f"proposta-automatica-{giorno}", adesso=adesso,
            impronta_attesa=piano["impronta_piano"],
            dati_extra={"proposta_automatica": {
                "generata_il": adesso.isoformat(), "classi_escluse": list(classi_escluse),
                "selezionabili": piano["selezionabili"], "bloccati": piano["bloccati"]}})
        # Prova della creazione da parte del lavoratore (solo il suo ruolo la scrive)
        await session.execute(text(
            "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
            "dettagli) VALUES ('SCARTO', :p, :t, NULL, 'PROPOSTA_GENERATA', CAST(:d AS jsonb))"),
            {"p": pratica_id, "t": ente, "d": json.dumps({
                "impronta_piano": piano["impronta_piano"], "criteri": criteri,
                "generata_il": adesso.isoformat()})})
        await avvisi.avvisa(session, ente, avvisi.PROPOSTA_SCARTO, str(pratica_id), {
            "pratica_id": str(pratica_id), "selezionabili": piano["selezionabili"],
            "bloccati": sum(piano["bloccati"].values())})
    logger.info("scarto_proposta_automatica", ente=str(ente), pratica_id=str(pratica_id),
                selezionabili=piano["selezionabili"])
    return {"esito": "PROPOSTA_CREATA", "pratica": str(pratica_id),
            "selezionabili": piano["selezionabili"]}


async def proposte(detentore: str, adesso: datetime) -> dict[str, Any]:
    """Passo 1: un sotto-passo per ente con la frequenza dell'ente."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        enti = (await session.execute(text(
            "SELECT id FROM public.tenants WHERE stato::text IN ('ATTIVO', 'TRIAL') "
            "ORDER BY id"))).scalars().all()
    esiti: dict[str, Any] = {}
    for ente in enti:
        await tieni_lease()
        v = await parametri.valori([SCARTO_PROPOSTA_AUTOMATICA, SCARTO_PROPOSTA_FREQUENZA_GIORNI,
                                    SCARTO_PROPOSTA_CLASSI_ESCLUSE], ente, rigoroso=True)
        if v[SCARTO_PROPOSTA_AUTOMATICA] != PROPOSTA_ATTIVA:
            continue
        classi_escluse = tuple(v[SCARTO_PROPOSTA_CLASSI_ESCLUSE])

        async def una(e: uuid.UUID = ente, escluse: tuple[str, ...] = classi_escluse
                      ) -> dict[str, Any]:
            try:
                return await proponi(e, escluse, adesso)
            except ErroreApi as exc:   # piano cambiato o nulla di scartabile nel lock
                raise RuntimeError(f"{exc.codice}: {exc.dettaglio or exc.titolo}") from None

        esito = await _esegui(f"{PASSO_PROPOSTE}:{ente}", detentore, adesso,
                              timedelta(days=int(v[SCARTO_PROPOSTA_FREQUENZA_GIORNI])), una)
        if esito is not None:
            esiti[str(ente)] = esito
    return {"enti": esiti}


# ── 2. Riprese automatiche degli scarti incompleti (D20) ──────────────────

def _sbloccato(versioni: list[dom.OggettoWorm], adesso: datetime) -> bool:
    """Nessuna versione del PdA è più bloccata (legal hold, retention)."""
    return all(dom.motivo_blocco_oggetto(v, adesso) is None for v in versioni)


async def riprese(adesso: datetime) -> dict[str, Any]:
    from libs.shared.db import get_async_session

    massimo = int((await parametri.valori([SCARTO_RIPRESE_AUTOMATICHE], rigoroso=True))[
        SCARTO_RIPRESE_AUTOMATICHE])
    if massimo <= 0:
        return {"riprese": [], "nota": "riprese automatiche disattivate"}
    async with get_async_session() as session:
        candidate = (await session.execute(text(
            "SELECT id, tenant_id, dati FROM public.pratiche WHERE tipo = 'SCARTO' "
            "AND stato = 'ESEGUITA_CON_ECCEZIONI' AND lavoro IS NULL "
            "AND COALESCE((dati ->> 'riprese_automatiche')::int, 0) < :max "
            "ORDER BY updated_at, id LIMIT :lim"),
            {"max": massimo, "lim": settings.periodico_lotto})).mappings().all()
    s3, bucket = storage.client(), storage.bucket_worm()
    riprese_fatte: list[str] = []
    for p in candidate:
        await tieni_lease()
        async with get_async_session() as session:
            incompleti = (await session.execute(text(
                "SELECT aip_id::text FROM public.pratiche_oggetti WHERE pratica_id = :p "
                "AND esito = 'NON_COMPLETATO' ORDER BY id"), {"p": p["id"]})).scalars().all()
        sbloccati = 0
        for aip_id in incompleti:
            versioni = await asyncio.to_thread(storage.versioni_del_pda, s3, bucket,
                                               f"{p['tenant_id']}/{aip_id}/")
            if _sbloccato(versioni, adesso):
                sbloccati += 1
        if not sbloccati:
            continue
        async with get_async_session() as session:
            await verifica_fencing(session)
            pratica = await repo.leggi_pratica(session, p["id"], per_aggiornare=True)
            if pratica is None or pratica["stato"] != st.ESEGUITA_CON_ECCEZIONI \
                    or pratica["lavoro"] is not None:
                continue
            dati = dict(pratica["dati"] or {})
            n = int(dati.get("riprese_automatiche") or 0) + 1
            if n > massimo:
                continue
            dati["riprese_automatiche"] = n
            t = st.macchina(st.SCARTO).cerca(st.ESEGUITA_CON_ECCEZIONI, st.ESEGUITA)
            assert t is not None
            await pr.richiedi_esecuzione(
                session, {**pratica, "dati": dati}, t, utente_id=None, ruolo=st.SISTEMA,
                motivo=(f"Ripresa automatica {n} di {massimo}: {sbloccati} PdA senza più "
                        "blocchi nello storage (legal hold tolto o retention scaduta)"),
                adesso=adesso)
            # revisione R17 (VD-P-164): la ripresa automatica riusa la conferma del giro
            # precedente; la riconosce questo fatto del solo lavoratore
            from app.application import conferme

            await conferme.registra_fatto(session, pratica, "RIPRESA_SISTEMA")
            await avvisi.avvisa(session, pratica["tenant_id"], avvisi.RIPRESA_SCARTO,
                                f"{pratica['id']}:{n}", {
                                    "pratica_id": str(pratica["id"]), "ripresa": n,
                                    "pda_sbloccati": sbloccati,
                                    "pda_incompleti": len(incompleti)})
        riprese_fatte.append(str(p["id"]))
        logger.warning("scarto_ripresa_automatica", pratica_id=str(p["id"]), ripresa=n,
                       pda_sbloccati=sbloccati)
    return {"riprese": riprese_fatte}


# ── 3. Minimizzazione dopo lo scarto (D06) ────────────────────────────────

async def _minimizza_uno(o: Mapping[str, Any], adesso: datetime) -> str | None:
    """In UNA transazione: dati personali tolti e fatto METADATI_MINIMIZZATI.
    Restituisce l'impronta del fatto (anche se già scritto), None se il PdA non
    è (più) un PdA eliminato per intero."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await verifica_fencing(session)
        gia = (await session.execute(text(
            "SELECT dettagli ->> 'impronta' FROM public.esiti_esecuzione "
            "WHERE tipo = 'SCARTO' AND pratica_id = :p AND aip_id = :a "
            "AND esito = 'METADATI_MINIMIZZATI'"),
            {"p": o["pratica_id"], "a": o["aip_id"]})).scalar()
        if gia:
            return str(gia)
        aip = (await session.execute(text(
            "SELECT a.stato::text AS stato, a.pdv_id, a.urn, a.hash_sha256, a.data_versamento, "
            "a.data_conservazione, v.classe_doc_id FROM public.aip a "
            "JOIN public.pdv v ON v.id = a.pdv_id WHERE a.id = :a AND a.tenant_id = :t "
            "FOR UPDATE OF a, v"), {"a": o["aip_id"], "t": o["tenant_id"]})).mappings().first()
        if aip is None or aip["stato"] != "ELIMINAZIONE":
            return None
        elenco = {"urn": aip["urn"], "impronta_pda": aip["hash_sha256"],
                  "classe_id": str(aip["classe_doc_id"]),
                  "data_versamento": aip["data_versamento"],
                  "data_conservazione": aip["data_conservazione"],
                  "pratica": str(o["pratica_id"]), "minimizzato_il": adesso}
        impronta = _impronta({"tipo": "minimizzazione", "aip": str(o["aip_id"]),
                              **json.loads(json.dumps(elenco, default=str))})
        await session.execute(text(
            "UPDATE public.pdv SET oggetto = :ogg, numero_protocollo = NULL, "
            "errori_validazione = '[]'::jsonb, avvisi = '[]'::jsonb, regole_applicate = NULL, "
            "metadata_json = jsonb_build_object('minimizzazione', CAST(:m AS jsonb)) "
            "|| jsonb_strip_nulls(jsonb_build_object('rdv_definitiva', "
            "   metadata_json -> 'rdv_definitiva')), data_aggiornamento = :adesso "
            "WHERE id = :pdv"),
            {"ogg": OGGETTO_MINIMIZZATO, "pdv": aip["pdv_id"], "adesso": adesso,
             "m": json.dumps({"il": adesso.isoformat(), "pratica": str(o["pratica_id"]),
                              "impronta": impronta})})
        allegato5 = (await session.execute(text(
            "DELETE FROM public.metadati_allegato5 WHERE documento_id IN "
            "(SELECT id FROM public.documenti WHERE aip_id = :a)"), {"a": o["aip_id"]})).rowcount
        documenti = (await session.execute(text(
            "UPDATE public.documenti SET nome_file = 'documento-' || ordine::text "
            "WHERE aip_id = :a"), {"a": o["aip_id"]})).rowcount
        # R12a (VD-C-149): collegamenti ai fascicoli del PdA scartato tolti e PdA
        # rimesso nella coda dell'indicizzazione: il lavoratore di distribution
        # rilegge i dati GIÀ minimizzati e sovrascrive il documento dell'indice
        # (stesso id). Stessa transazione del fatto: niente minimizzato fuori indice.
        fascicoli = (await session.execute(text(
            "DELETE FROM public.fascicoli_aip WHERE aip_id = :a"), {"a": o["aip_id"]})).rowcount
        await session.execute(text(
            "INSERT INTO public.indicizzazione_aip (aip_id, tenant_id, stato, tentativi, "
            "prossimo_tentativo, richiesta_il) VALUES (:a, :t, 'IN_CODA', 0, :adesso, :adesso) "
            "ON CONFLICT (aip_id) DO UPDATE SET stato = 'IN_CODA', tentativi = 0, "
            "prossimo_tentativo = EXCLUDED.prossimo_tentativo, ultimo_errore = NULL, "
            "richiesta_il = EXCLUDED.richiesta_il"),
            {"a": o["aip_id"], "t": o["tenant_id"], "adesso": adesso})
        await session.execute(text(
            "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
            "dettagli) VALUES ('SCARTO', :p, :t, :a, 'METADATI_MINIMIZZATI', CAST(:d AS jsonb))"),
            {"p": o["pratica_id"], "t": o["tenant_id"], "a": o["aip_id"],
             "d": json.dumps({"impronta": impronta, "elenco_minimo": elenco,
                              "documenti": documenti, "metadati_allegato5": allegato5,
                              "fascicoli_scollegati": fascicoli,
                              "indice_ricerca": "rimesso in coda"}, default=str)})
        await registra_audit(session, evento="ciclo_vita.scarto.metadati_minimizzati",
                             tenant_id=str(o["tenant_id"]), aip_id=str(o["aip_id"]),
                             dettagli={"pratica_id": str(o["pratica_id"]), "urn": aip["urn"],
                                       "documenti": documenti,
                                       "metadati_allegato5": allegato5})
    return impronta


async def minimizza(adesso: datetime, registro: RegistroImpronte) -> dict[str, Any]:
    from libs.shared.db import get_async_session

    giorni = int((await parametri.valori([SCARTO_MINIMIZZAZIONE_GIORNI], rigoroso=True))[
        SCARTO_MINIMIZZAZIONE_GIORNI])
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT o.id, o.pratica_id, o.tenant_id, o.aip_id, o.urn "
            "FROM public.pratiche p JOIN public.pratiche_oggetti o ON o.pratica_id = p.id "
            "WHERE p.tipo = 'SCARTO' AND p.stato IN ('ESEGUITA', 'ESEGUITA_CON_ECCEZIONI', "
            "'CHIUSA_CON_ECCEZIONI') AND p.conclusa_il <= :limite "
            "AND o.esito = 'ELIMINATO' AND o.foglia_id IS NOT NULL "
            "AND NOT (o.dettagli ? 'foglia_minimizzazione') ORDER BY o.id LIMIT :lim"),
            {"limite": adesso - timedelta(days=giorni),
             "lim": settings.periodico_lotto})).mappings().all()
    fatti = 0
    for o in righe:
        await tieni_lease()
        impronta = await _minimizza_uno(o, adesso)
        if impronta is None:
            continue
        foglia = await registro.aggiungi_foglia(
            tipo="minimizzazione", pda=o["urn"],
            riferimento={"pratica": str(o["pratica_id"]), "aip": str(o["aip_id"])},
            impronta_documento_pratica=impronta)
        async with get_async_session() as session:
            await verifica_fencing(session)
            await session.execute(text(
                "UPDATE public.pratiche_oggetti SET dettagli = dettagli || jsonb_build_object("
                "'foglia_minimizzazione', CAST(:f AS text)), aggiornato_il = :adesso "
                "WHERE id = :id"), {"f": foglia, "id": o["id"], "adesso": adesso})
        fatti += 1
    return {"minimizzati": fatti, "giorni": giorni}


# ── 4. Rinnovo del blocco WORM (D04) ──────────────────────────────────────

def _estendi_tutte(s3: Any, bucket: str, versioni: list[dom.OggettoWorm],
                   nuovo: datetime, soglia: datetime | None = None
                   ) -> tuple[int, datetime | None]:
    """Estende fino a ``nuovo`` (mai in riduzione) le versioni che terminano prima
    della ``soglia`` (predefinita ``nuovo``) e restituisce (versioni estese,
    termine più vicino dopo il rinnovo). Revisione R09 (Z5): una versione già
    oltre la soglia non si «rinnova» di pochi secondi a ogni giro."""
    limite = soglia or nuovo
    estese = 0
    termini: list[datetime] = []
    for v in versioni:
        if storage.e_marcatore(v):
            continue
        attuale = v.conserva_fino_a
        if attuale is None or attuale < limite:
            dopo = storage.estendi_retention(s3, bucket, v.chiave, v.version_id, nuovo)
            estese += 1
            attuale = dopo
        if attuale is not None:
            termini.append(attuale)
    return estese, (min(termini) if termini else None)


#: Revisione R09 (Z5): termine sentinella dei PdA permanenti senza oggetti nel
#: WORM (non si selezionano più; contati dalla metrica e dallo stato automatico)
SENZA_OGGETTI = datetime(9999, 12, 31, tzinfo=UTC)


async def _foglia_rinnovo(registro: RegistroImpronte, r: Mapping[str, Any],
                          fatto: Mapping[str, Any]) -> None:
    """Foglia «rinnovo_retention» del fatto (idempotente per pda e rinnovo)."""
    await registro.aggiungi_foglia(
        tipo="rinnovo_retention", pda=r["urn"],
        riferimento={"pratica": str(fatto["rinnovo"]), "aip": str(r["id"])},
        impronta_documento_pratica=str(fatto["impronta"]))


async def _ultimo_rinnovo(r: Mapping[str, Any]) -> dict[str, Any] | None:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        dettagli = (await session.execute(text(
            "SELECT dettagli FROM public.esiti_esecuzione WHERE tipo = 'RETENTION' "
            "AND esito = 'RINNOVATA' AND aip_id = :a ORDER BY scritto_il DESC, id DESC LIMIT 1"),
            {"a": r["id"]})).scalar()
    if isinstance(dettagli, str):
        dettagli = json.loads(dettagli)
    return dict(dettagli) if dettagli else None


async def _rinnova_pda(r: Mapping[str, Any], adesso: datetime, nuovo: datetime,
                       registro: RegistroImpronte, soglia: datetime | None = None) -> str:
    """Rinnovo di un PdA. Ordine (revisione R09, Z5): versioni estese → fatto
    RINNOVATA → foglia → ``retention_fino_a``. Se la foglia non si scrive il
    termine non avanza: al giro dopo il PdA torna e la foglia del fatto già
    scritto si richiede (idempotente), senza un nuovo fatto. Esito: RINNOVATO,
    INVARIATO o SENZA_OGGETTI."""
    from libs.shared.db import get_async_session

    s3, bucket = storage.client(), storage.bucket_worm()
    versioni = await asyncio.to_thread(storage.versioni_del_pda, s3, bucket,
                                       f"{r['tenant_id']}/{r['id']}/")
    reali = [v for v in versioni if not storage.e_marcatore(v)]
    if not reali:
        logger.error("retention_rinnovo_pda_senza_oggetti", aip_id=str(r["id"]))
        async with get_async_session() as session:
            await verifica_fencing(session)
            await session.execute(text(
                "UPDATE public.aip SET retention_fino_a = :fino WHERE id = :a"),
                {"fino": SENZA_OGGETTI, "a": r["id"]})
        return "SENZA_OGGETTI"
    precedente = min((v.conserva_fino_a for v in reali if v.conserva_fino_a), default=None)
    estese, dopo = await asyncio.to_thread(_estendi_tutte, s3, bucket, reali, nuovo, soglia)
    if estese:
        rinnovo = str(uuid.uuid4())
        fatto = {"rinnovo": rinnovo, "impronta": _impronta({
            "tipo": "rinnovo_retention", "rinnovo": rinnovo, "aip": str(r["id"]),
            "urn": r["urn"], "precedente": str(precedente), "nuova": str(dopo),
            "versioni_estese": estese}), "precedente": precedente, "nuova": dopo,
            "versioni_estese": estese, "versioni": len(reali)}
        async with get_async_session() as session:
            await verifica_fencing(session)
            await session.execute(text(
                "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
                "dettagli) VALUES ('RETENTION', NULL, :t, :a, 'RINNOVATA', CAST(:d AS jsonb))"),
                {"t": r["tenant_id"], "a": r["id"], "d": json.dumps(fatto, default=str)})
            await registra_audit(session, evento="ciclo_vita.retention.rinnovata",
                                 tenant_id=str(r["tenant_id"]), aip_id=str(r["id"]),
                                 dettagli={"urn": r["urn"], "precedente": str(precedente),
                                           "nuova": str(dopo), "versioni_estese": estese})
        await _foglia_rinnovo(registro, r, fatto)
    else:
        # nulla da estendere: se l'ultimo fatto è rimasto senza foglia (registro
        # giù al giro prima) la si richiede ora; idempotente se c'è già
        ultimo = await _ultimo_rinnovo(r)
        if ultimo is not None and ultimo.get("rinnovo") and ultimo.get("impronta"):
            await _foglia_rinnovo(registro, r, ultimo)
    async with get_async_session() as session:
        await verifica_fencing(session)
        await session.execute(text(
            "UPDATE public.aip SET retention_fino_a = :fino WHERE id = :a"),
            {"fino": dopo, "a": r["id"]})
    return "RINNOVATO" if estese else "INVARIATO"


async def _rinnova_allegato(a: Mapping[str, Any], s3: Any, nuovo: datetime) -> None:
    from libs.shared.db import get_async_session

    dopo = await asyncio.to_thread(storage.estendi_retention, s3, a["bucket"], a["chiave"],
                                   a["version_id"], nuovo)
    impronta = _impronta({"tipo": "rinnovo_retention", "allegato": str(a["id"]),
                          "precedente": str(a["fino"]), "nuova": str(dopo)})
    async with get_async_session() as session:
        await verifica_fencing(session)
        await session.execute(text(
            "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
            "dettagli) VALUES ('RETENTION', :p, :t, NULL, 'RINNOVATA_ALLEGATO', "
            "CAST(:d AS jsonb))"),
            {"p": a["pratica_id"], "t": a["tenant_id"], "d": json.dumps({
                "allegato": str(a["id"]), "impronta": impronta, "precedente": a["fino"],
                "nuova": dopo}, default=str)})
        await registra_audit(session, evento="ciclo_vita.retention.allegato_rinnovato",
                             tenant_id=str(a["tenant_id"]),
                             dettagli={"pratica_id": str(a["pratica_id"]),
                                       "allegato_id": str(a["id"]),
                                       "precedente": str(a["fino"]), "nuova": str(dopo)})


async def _rinnova_allegati(adesso: datetime, soglia: datetime, nuovo: datetime
                            ) -> tuple[int, list[str]]:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT * FROM (SELECT a.id, a.pratica_id, a.tenant_id, a.bucket, a.chiave, "
            "a.version_id, GREATEST(a.conserva_fino_a, (SELECT max(CAST(e.dettagli ->> "
            "'nuova' AS timestamptz)) FROM public.esiti_esecuzione e WHERE e.tipo = 'RETENTION' "
            "AND e.esito = 'RINNOVATA_ALLEGATO' AND e.pratica_id = a.pratica_id "
            "AND e.dettagli ->> 'allegato' = a.id::text)) AS fino "
            "FROM public.pratiche_allegati a) x WHERE x.fino < :soglia ORDER BY x.fino, x.id "
            "LIMIT :lim"), {"soglia": soglia, "lim": settings.periodico_lotto})).mappings().all()
    s3 = storage.client()
    rinnovati, errori = 0, []
    for a in righe:
        await tieni_lease()
        try:
            await _rinnova_allegato(a, s3, nuovo)
        except LeasePassoPerso:
            raise
        except Exception as exc:  # noqa: BLE001 — Z5: un oggetto non ferma il lotto
            logger.error("retention_rinnovo_allegato_fallito", allegato_id=str(a["id"]),
                         errore=type(exc).__name__, dettaglio=str(exc)[:300])
            errori.append(f"allegato {a['id']}: {type(exc).__name__}")
            continue
        rinnovati += 1
    return rinnovati, errori


async def controlla_configurazione(anni: int, soglia_anni: int) -> None:
    """Revisione R09 (Z4): un rinnovo deve portare oltre la soglia (anni > soglia),
    altrimenti si rinnoverebbe ogni giorno o mai: il passo NON risulta fatto."""
    if int(anni) <= int(soglia_anni):
        logger.error("retention_rinnovo_configurazione_non_valida", anni=anni,
                     soglia=soglia_anni)
        raise RinnovoNonPossibile(
            f"RETENTION_PERMANENTE_ANNI ({anni}) deve superare RETENTION_RINNOVO_SOGLIA_ANNI "
            f"({soglia_anni}): nessun rinnovo possibile")


async def rinnova(adesso: datetime, registro: RegistroImpronte) -> dict[str, Any]:
    """Passo 4 (vedi modulo). Revisione R09 (Z5): due code distinte, ognuna col
    suo lotto: i PdA da censire (``retention_fino_a`` NULL, PdA precedenti alla
    correzione dell'aip-builder) e quelli in scadenza, in ordine di scadenza (i
    NULL non affamano più i PdA vicini alla scadenza); un PdA senza oggetti esce
    dalla coda (data sentinella e allarme); un errore su un elemento si registra
    e il lotto prosegue (``elementi_in_errore``: allarme)."""
    from libs.shared.db import get_async_session

    v = await parametri.valori([RETENTION_PERMANENTE_ANNI, RETENTION_RINNOVO_SOGLIA_ANNI],
                               rigoroso=True)
    await controlla_configurazione(int(v[RETENTION_PERMANENTE_ANNI]),
                                   int(v[RETENTION_RINNOVO_SOGLIA_ANNI]))
    soglia = aggiungi_anni(adesso, int(v[RETENTION_RINNOVO_SOGLIA_ANNI]))
    nuovo = aggiungi_anni(adesso, int(v[RETENTION_PERMANENTE_ANNI]))
    lotto = settings.periodico_lotto
    async with get_async_session() as session:
        in_scadenza = (await session.execute(text(
            "SELECT id, tenant_id, urn FROM public.aip WHERE anni_conservazione = 999 "
            "AND stato::text = ANY(:stati) AND retention_fino_a < :soglia "
            "ORDER BY retention_fino_a, id LIMIT :lim"),
            {"stati": list(STATI_CON_OGGETTI), "soglia": soglia, "lim": lotto})
        ).mappings().all()
        da_censire = (await session.execute(text(
            "SELECT id, tenant_id, urn FROM public.aip WHERE anni_conservazione = 999 "
            "AND stato::text = ANY(:stati) AND retention_fino_a IS NULL "
            "ORDER BY id LIMIT :lim"),
            {"stati": list(STATI_CON_OGGETTI), "lim": lotto})).mappings().all()
    conteggi = {"RINNOVATO": 0, "INVARIATO": 0, "SENZA_OGGETTI": 0}
    errori: list[str] = []
    for r in [*in_scadenza, *da_censire]:
        await tieni_lease()
        try:
            conteggi[await _rinnova_pda(r, adesso, nuovo, registro, soglia)] += 1
        except LeasePassoPerso:
            raise
        except Exception as exc:  # noqa: BLE001 — Z5: un PdA non ferma il lotto
            logger.error("retention_rinnovo_pda_fallito", aip_id=str(r["id"]),
                         errore=type(exc).__name__, dettaglio=str(exc)[:300])
            errori.append(f"PdA {r['urn']}: {type(exc).__name__}")
    allegati, errori_allegati = await _rinnova_allegati(adesso, soglia, nuovo)
    errori += errori_allegati
    if errori:
        logger.error("retention_rinnovo_elementi_in_errore", elementi=len(errori),
                     primi=errori[:5])
    return {"pda_controllati": len(in_scadenza) + len(da_censire),
            "pda_in_scadenza": len(in_scadenza), "pda_censiti": len(da_censire),
            "pda_rinnovati": conteggi["RINNOVATO"], "pda_senza_oggetti": conteggi["SENZA_OGGETTI"],
            "allegati_rinnovati": allegati, "elementi_in_errore": len(errori),
            "errori": errori[:20], "fino_a": nuovo.isoformat()}


# ── Giro ─────────────────────────────────────────────────────────────────

async def giro(registro: RegistroImpronte, detentore: str, adesso: datetime) -> dict[str, Any]:
    """Un controllo dei passi dovuti (ognuno con il suo lease)."""
    esiti: dict[str, Any] = {}
    secondi = timedelta(seconds=1)
    passi: list[tuple[str, int, Callable[[], Awaitable[dict[str, Any]]]]] = [
        (PASSO_PROPOSTE, settings.periodico_proposte_secondi,
         lambda: proposte(detentore, adesso)),
        (PASSO_RIPRESE, settings.periodico_riprese_secondi, lambda: riprese(adesso)),
        (PASSO_MINIMIZZAZIONE, settings.periodico_minimizzazione_secondi,
         lambda: minimizza(adesso, registro)),
        (PASSO_RINNOVO, settings.periodico_rinnovo_secondi, lambda: rinnova(adesso, registro)),
    ]
    for nome, intervallo, passo in passi:
        esito = await _esegui(nome, detentore, adesso, intervallo * secondi, passo)
        if esito is not None:
            esiti[nome] = esito
    if esiti:
        logger.info("ciclo_vita_periodico_giro", **{k: json.dumps(v, default=str)[:300]
                                                    for k, v in esiti.items()})
    return esiti


__all__ = ["LeasePassoPerso", "OGGETTO_MINIMIZZATO", "PASSO_MINIMIZZAZIONE", "PASSO_PROPOSTE",
           "PASSO_RINNOVO", "PASSO_RIPRESE", "RinnovoNonPossibile", "SENZA_OGGETTI", "concludi",
           "controlla_configurazione", "giro", "minimizza", "prendi", "proponi", "proposte",
           "riprese", "rinnova", "tieni_lease", "verifica_fencing"]
