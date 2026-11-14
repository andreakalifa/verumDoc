# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/scarto.py — scarto: proposta (anteprima e applica) ed esecuzione.

Riferimenti: contratto Q2 di R05; Linee guida AgID §4.11; per le PA il nulla
osta della Soprintendenza archivistica (D.lgs. 42/2004 art. 21); REGOLE
§9.3 (``pda_scartabili``: UNA query per anteprima, applica ed esecuzione),
§10.1, §11.7, §12.6-§12.9, §13.1 (anteprima senza scritture, applica con
l'impronta del piano ricalcolata nel lock, 409 LC-409-PIANO-CAMBIATO), §15.8.

ESECUZIONE (lavoratore, passo ``ESEGUI``), ripresa sicura dopo un crash:
  1. per ogni PdA SELEZIONATO (a lotti): i blocchi si ricalcolano sui dati di
     ADESSO (stessa funzione dell'anteprima); poi si leggono TUTTE le versioni
     degli oggetti del PdA nel WORM con retention e legal hold: se anche una
     sola non è scaduta o ha il legal hold il PdA resta BLOCCATO e NESSUNA
     cancellazione si tenta (mai uno scarto parziale, mai forzato). Altrimenti
     PdA → ELIMINAZIONE e oggetto IN_CORSO (commit), cancellazione di ogni
     versione, oggetto ELIMINATO;
  2. verbale di scarto (XML, con l'iter, gli allegati e l'esito per PdA) nel
     WORM, una volta (stessi dati = stessi byte: la ripresa lo riconosce);
  3. per ogni PdA ELIMINATO, foglia «scarto» nel registro delle impronte
     (contratto Q3: tombstone con URN, pratica e impronta del verbale);
  4. transizione ESEGUIBILE → ESEGUITA (ruolo SISTEMA, stessa funzione delle
     transizioni manuali) ed evento ``scarto.eseguito``.

Revisione 2 di R05 (S3, S6): le versioni si elencano con il PdA bloccato e si
rielencano dopo le cancellazioni; una cancellazione INIZIATA che non si può
completare (legal hold arrivato durante lo scarto, retention, errore dello
storage oltre ``SCARTO_TENTATIVI_MAX_PDA``) chiude l'oggetto NON_COMPLETATO e il
PdA ELIMINAZIONE_INCOMPLETA (allarme ``ScartoEliminazioneIncompleta``); la
pratica si conclude ESEGUITA_CON_ECCEZIONI con il verbale (cosa è stato
cancellato e cosa no) e i tombstone dei PdA cancellati per intero. Quando il
blocco cessa il conservatore riprende l'esecuzione (ESEGUITA_CON_ECCEZIONI →
ESEGUITA): si riprovano solo i PdA incompleti, con un verbale integrativo
(runbook ``docs/runbooks/scarto_incompleto.md``). Mai un ciclo infinito.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import tempfile
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import lease
from app.application import pratiche as pr
from app.config import settings
from app.domain import scarto as dom
from app.domain import stati as st
from app.domain.documenti import scrivi_verbale_scarto
from app.domain.permessi import Identita, motivo_blocco_avvio
from app.infrastructure import repository as repo
from app.infrastructure import storage
from app.infrastructure.registro import RegistroImpronte
from libs.shared.errori import ErroreApi
from libs.shared.security.audit import registra_audit
from libs.shared.storage.worm import aggiungi_anni

logger = structlog.get_logger(__name__)
FUSO = ZoneInfo("Europe/Rome")
LOTTO_INSERIMENTO = 1000

# UNA query per i PdA candidati (anteprima, applica, esecuzione): i blocchi si
# decidono in ``app.domain.scarto.motivo_blocco_pda`` su questi dati. Join
# PdA → PdV → classe (stessa riga per PdA); sottointerrogazioni indicizzate:
# idx_fascicoli_aip_aip, idx_pratiche_oggetti_aip, pacchetti per aip_id.
_CANDIDATI = """
SELECT a.id::text AS aip_id, a.urn, a.stato::text AS stato,
       c.conservazione_permanente AS permanente, c.anni_conservazione AS anni,
       a.scadenza_conservazione AS scadenza,
       EXISTS (SELECT 1 FROM public.fascicoli_aip fa JOIN public.fascicoli f
                 ON f.id = fa.fascicolo_id
               WHERE fa.aip_id = a.id AND f.stato <> 'CHIUSO') AS fascicolo_aperto,
       (SELECT max(f.data_chiusura) FROM public.fascicoli_aip fa JOIN public.fascicoli f
          ON f.id = fa.fascicolo_id
        WHERE fa.aip_id = a.id AND f.stato = 'CHIUSO') AS chiusura_fascicolo,
       EXISTS (SELECT 1 FROM public.pratiche_oggetti o JOIN public.pratiche p
                 ON p.id = o.pratica_id
               WHERE o.aip_id = a.id AND p.tipo = 'SCARTO'
                 AND p.stato NOT IN ('ESEGUITA', 'RESPINTA')
                 AND o.esito IN ('SELEZIONATO', 'IN_CORSO')
                 AND p.id IS DISTINCT FROM CAST(:esclusa AS uuid)) AS in_altra_pratica,
       EXISTS (SELECT 1 FROM public.pacchetti_distribuzione d
               WHERE d.aip_id = a.id AND d.stato = 'IN_PREPARAZIONE') AS esibizione
FROM public.aip a
JOIN public.pdv v ON v.id = a.pdv_id
JOIN public.classi_documentali c ON c.id = v.classe_doc_id
WHERE a.tenant_id = :ente {filtri}
ORDER BY a.id
"""


class CriteriNonValidi(ValueError):
    pass


def normalizza_criteri(criteri: Mapping[str, Any]) -> dict[str, Any]:
    """Criteri canonici (entrano nell'impronta del piano): classi ordinate,
    ``anni`` ordinati OPPURE ``scadenza_entro`` (data ISO), mai entrambi."""
    classi = sorted({str(c) for c in (criteri.get("classi") or [])})
    anni = sorted({int(a) for a in (criteri.get("anni") or [])})
    entro = criteri.get("scadenza_entro")
    if bool(anni) == bool(entro):
        raise CriteriNonValidi("indicare «anni» oppure «scadenza_entro» (uno dei due)")
    if entro is not None and not isinstance(entro, date):
        entro = date.fromisoformat(str(entro))
    return {"classi": classi, "anni": anni,
            "scadenza_entro": entro.isoformat() if entro else None}


def filtri_criteri(criteri: Mapping[str, Any], *, con_scadenza: bool = True) -> list[str]:
    """Frammenti SQL (costanti) dei criteri dello scarto su ``a`` (aip) e ``c``
    (classe). Revisione 4 di R05 (U2): senza la scadenza li usa anche la verifica
    del lavoratore (attributi che non cambiano: ente, classe, anno di versamento)."""
    filtri = []
    if criteri.get("classi"):
        filtri.append("AND c.codice = ANY(:classi)")
    if criteri.get("anni"):
        filtri.append("AND CAST(EXTRACT(YEAR FROM a.data_versamento AT TIME ZONE "
                      "'Europe/Rome') AS integer) = ANY(:anni)")
    if con_scadenza and criteri.get("scadenza_entro"):
        filtri.append("AND a.scadenza_conservazione <= :entro")
    return filtri


def _sql_candidati(criteri: Mapping[str, Any], *, solo_aip: bool = False) -> str:
    filtri = ["AND a.id = CAST(:aip AS uuid)"] if solo_aip else filtri_criteri(criteri)
    return _CANDIDATI.format(filtri=" ".join(filtri))


def _parametri(ente: uuid.UUID, criteri: Mapping[str, Any], esclusa: str | None) -> dict[str, Any]:
    parametri: dict[str, Any] = {"ente": ente, "esclusa": esclusa,
                                 "classi": criteri.get("classi") or None,
                                 "anni": criteri.get("anni") or None}
    if criteri.get("scadenza_entro"):
        giorno = date.fromisoformat(criteri["scadenza_entro"])
        # fine del giorno indicato, ora italiana
        parametri["entro"] = datetime.combine(giorno + timedelta(days=1), time(0), tzinfo=FUSO)
    return parametri


def dati_pda(riga: Mapping[str, Any]) -> dom.DatiPda:
    termine = None
    if riga["chiusura_fascicolo"] is not None and riga["anni"]:
        chiusura = datetime.combine(riga["chiusura_fascicolo"], time(0), tzinfo=FUSO)
        termine = aggiungi_anni(chiusura, int(riga["anni"]))
    return dom.DatiPda(
        aip_id=riga["aip_id"], urn=riga["urn"], stato=riga["stato"],
        permanente=bool(riga["permanente"]), scadenza=riga["scadenza"],
        fascicolo_aperto=bool(riga["fascicolo_aperto"]), termine_fascicolo=termine,
        in_altra_pratica=bool(riga["in_altra_pratica"]),
        esibizione_in_corso=bool(riga["esibizione"]))


async def _piano(session: AsyncSession, ente: uuid.UUID, criteri: Mapping[str, Any],
                 adesso: datetime) -> AsyncIterator[tuple[dom.DatiPda, str | None]]:
    risultato = await session.stream(text(_sql_candidati(criteri)),
                                     _parametri(ente, criteri, None))
    async for riga in risultato.mappings():
        pda = dati_pda(riga)
        yield pda, dom.motivo_blocco_pda(pda, adesso)


async def calcola_piano(session: AsyncSession, ente: uuid.UUID, criteri: Mapping[str, Any],
                        adesso: datetime) -> dict[str, Any]:
    """Piano dello scarto in streaming: impronta, conteggi, esempi (mai l'elenco
    intero in memoria)."""
    impronta = dom.ImprontaPiano(st.SCARTO, str(ente), criteri)
    bloccati: dict[str, int] = {}
    esempi_sel: list[dict[str, Any]] = []
    esempi_bl: list[dict[str, Any]] = []
    selezionabili = 0
    massimo = settings.scarto_max_pda_per_pratica
    async for pda, motivo in _piano(session, ente, criteri, adesso):
        if impronta.elementi >= massimo:
            raise CriteriNonValidi(f"oltre {massimo} PdA per pratica: restringere i criteri")
        esito = dom.BLOCCATO if motivo else dom.SELEZIONATO
        impronta.aggiungi(pda.aip_id, esito, motivo)
        if motivo:
            bloccati[motivo] = bloccati.get(motivo, 0) + 1
            if len(esempi_bl) < settings.anteprima_esempi:
                esempi_bl.append({"aip_id": pda.aip_id, "urn": pda.urn, "motivo": motivo,
                                  "descrizione": dom.DESCRIZIONE_MOTIVI[motivo]})
        else:
            selezionabili += 1
            if len(esempi_sel) < settings.anteprima_esempi:
                esempi_sel.append({"aip_id": pda.aip_id, "urn": pda.urn,
                                   "scadenza": pda.scadenza})
    return {"impronta_piano": impronta.hex(), "totale": impronta.elementi,
            "selezionabili": selezionabili, "bloccati": bloccati,
            "esempi": {"selezionabili": esempi_sel, "bloccati": esempi_bl,
                       "limite_esempi": settings.anteprima_esempi}}


def richiede_nulla_osta(ente: Mapping[str, Any]) -> bool:
    """Nulla osta della Soprintendenza archivistica (D.lgs. 42/2004, art. 21) per
    lo scarto: revisione R05 (R9), deciso dalla NATURA dichiarata dell'ente,
    mai dal codice IPA (facoltativo). Solo un ente dichiarato ``PRIVATO`` ne è
    esente; natura assente = pubblica amministrazione (il caso più prudente)."""
    return dom.richiede_nulla_osta(ente.get("natura"))


async def _ente_e_criteri(session: AsyncSession, identita: Identita, ente: str,
                          criteri: Mapping[str, Any]) -> tuple[uuid.UUID, dict[str, Any],
                                                               dict[str, Any]]:
    blocco = motivo_blocco_avvio(st.SCARTO, identita, ente)
    if blocco is not None:
        raise ErroreApi(*blocco)
    tenant = pr.uuid_o_404(ente, "LC-404-ENTE", "Ente non trovato")
    dati_ente = await repo.ente(session, tenant)
    if dati_ente is None:
        raise ErroreApi("LC-404-ENTE", "Ente non trovato")
    try:
        canonici = normalizza_criteri(criteri)
    except (CriteriNonValidi, ValueError, TypeError) as exc:
        raise ErroreApi("LC-422-CRITERI", str(exc)) from None
    return tenant, canonici, dati_ente


async def anteprima(identita: Identita, *, ente: str, criteri: Mapping[str, Any],
                    motivo: str) -> dict[str, Any]:
    """Anteprima della proposta di scarto: NESSUNA scrittura (transazione di
    sola lettura)."""
    from libs.shared.db import get_async_session_lettura

    adesso = pr.adesso_utc()
    try:
        async with get_async_session_lettura() as session:
            tenant, canonici, dati_ente = await _ente_e_criteri(session, identita, ente, criteri)
            piano = await calcola_piano(session, tenant, canonici, adesso)
    except CriteriNonValidi as exc:
        raise ErroreApi("LC-422-CRITERI", str(exc)) from None
    except SQLAlchemyError as exc:
        raise pr.errore_db("anteprima_scarto", exc) from None
    return {**piano, "ente": str(tenant), "criteri": canonici, "motivo": motivo,
            "richiede_nulla_osta": richiede_nulla_osta(dati_ente),
            "calcolata_il": adesso}


async def _inserisci_oggetti(session: AsyncSession, pratica_id: uuid.UUID, tenant: uuid.UUID,
                             lotto: list[tuple[dom.DatiPda, str | None]]) -> None:
    if not lotto:
        return
    await session.execute(text(
        "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, urn, esito, motivo) "
        "VALUES (:p, :t, CAST(:aip AS uuid), :urn, :esito, :motivo)"),
        [{"p": pratica_id, "t": tenant, "aip": pda.aip_id, "urn": pda.urn,
          "esito": dom.BLOCCATO if motivo else dom.SELEZIONATO, "motivo": motivo}
         for pda, motivo in lotto])


async def crea_bozza(session: AsyncSession, *, tenant: uuid.UUID, canonici: Mapping[str, Any],
                     dati_ente: Mapping[str, Any], motivo: str, creata_da: str | None,
                     creata_ruolo: str, metodo: str | None, idempotency_key: str | None,
                     adesso: datetime, impronta_attesa: str,
                     dati_extra: Mapping[str, Any] | None = None) -> uuid.UUID:
    """Pratica di scarto in BOZZA con il piano congelato (UNA funzione per la
    proposta manuale e, R09 D21, per quella automatica del lavoratore). Nel lock
    dell'ente (una proposta alla volta per ente). L'impronta ricalcolata nel lock
    deve coincidere con ``impronta_attesa`` (anteprima della persona o, per la
    proposta automatica, del lavoratore appena prima): altrimenti 409
    LC-409-PIANO-CAMBIATO. Nessun PdA scartabile: LC-422-CRITERI. Le eccezioni
    annullano tutto (transazione del chiamante)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                          {"k": f"ciclo-vita:scarto:{tenant}"})
    # Pratica e oggetti si scrivono DURANTE la lettura del piano (a lotti);
    # se l'impronta finale non coincide l'eccezione annulla tutto.
    pratica_id = await repo.inserisci_pratica(
        session, tenant_id=tenant, tipo=st.SCARTO, stato=st.BOZZA, motivo=motivo,
        dati={"criteri": dict(canonici), "piano": {"calcolato_il": adesso.isoformat()},
              **dict(dati_extra or {})},
        creata_da=creata_da, creata_ruolo=creata_ruolo, impronta_piano=impronta_attesa,
        richiede_nulla_osta=richiede_nulla_osta(dati_ente),
        idempotency_key=idempotency_key, adesso=adesso)
    lotto: list[tuple[dom.DatiPda, str | None]] = []
    impronta = dom.ImprontaPiano(st.SCARTO, str(tenant), canonici)
    conteggi: dict[str, int] = {}
    massimo = settings.scarto_max_pda_per_pratica
    async for pda, mot in _piano(session, tenant, canonici, adesso):
        if impronta.elementi >= massimo:
            raise CriteriNonValidi(f"oltre {massimo} PdA per pratica")
        esito = dom.BLOCCATO if mot else dom.SELEZIONATO
        impronta.aggiungi(pda.aip_id, esito, mot)
        conteggi[esito] = conteggi.get(esito, 0) + 1
        lotto.append((pda, mot))
        if len(lotto) >= LOTTO_INSERIMENTO:
            await _inserisci_oggetti(session, pratica_id, tenant, lotto)
            lotto = []
    await _inserisci_oggetti(session, pratica_id, tenant, lotto)
    if impronta.hex() != impronta_attesa:
        piano = await calcola_piano(session, tenant, canonici, adesso)
        raise ErroreApi("LC-409-PIANO-CAMBIATO",
                        "Il piano è cambiato dall'anteprima: rivedere e confermare",
                        dettagli={"errore": "PIANO_CAMBIATO", "piano": json.loads(
                            json.dumps(piano, default=str))})
    if conteggi.get(dom.SELEZIONATO, 0) == 0:
        raise ErroreApi("LC-422-CRITERI", "Nessun PdA scartabile con questi criteri")
    await repo.aggiorna_pratica(session, pratica_id, adesso, conteggi=conteggi)
    await repo.inserisci_stato(session, pratica_id=pratica_id, tenant_id=tenant,
                               da_stato=None, a_stato=st.BOZZA, utente_id=creata_da,
                               ruolo=creata_ruolo, metodo=metodo, motivo=motivo, allegati=(),
                               adesso=adesso)
    await pr.evento(session, pr.TOPIC_CAMBIO_STATO, str(pratica_id), {
        "pratica_id": str(pratica_id), "tipo": st.SCARTO, "da_stato": None,
        "a_stato": st.BOZZA, "versione": 1, "ente": str(tenant),
        "il": adesso.isoformat()}, tenant)
    await registra_audit(session, evento="ciclo_vita.scarto.proposta",
                         tenant_id=str(tenant), user_id=creata_da,
                         dettagli={"pratica_id": str(pratica_id),
                                   "impronta_piano": impronta.hex(),
                                   "criteri": dict(canonici), "conteggi": conteggi,
                                   "motivo": motivo, "ruolo": creata_ruolo})
    return pratica_id


async def applica(identita: Identita, *, ente: str, criteri: Mapping[str, Any], motivo: str,
                  impronta_piano: str, idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
    """Crea la pratica di scarto (BOZZA) con il piano congelato. L'impronta si
    ricalcola nel lock dell'ente (una proposta di scarto alla volta per ente):
    se è cambiata → 409 LC-409-PIANO-CAMBIATO con il piano nuovo, nessuna
    scrittura. Restituisce (pratica, nuova)."""
    from libs.shared.db import get_async_session

    adesso = pr.adesso_utc()
    try:
        async with get_async_session() as session:
            tenant, canonici, dati_ente = await _ente_e_criteri(session, identita, ente, criteri)
            if idempotency_key:
                esistente = await repo.pratica_per_idempotenza(session, tenant, st.SCARTO,
                                                               idempotency_key)
                if esistente is not None:
                    if esistente["impronta_piano"] != impronta_piano or \
                            (esistente["dati"] or {}).get("criteri") != canonici:
                        raise ErroreApi("LC-409-IDEMPOTENZA",
                                        "Stessa Idempotency-Key con una richiesta diversa")
                    return pr.rappresenta(esistente), False
            pratica_id = await crea_bozza(
                session, tenant=tenant, canonici=canonici, dati_ente=dati_ente, motivo=motivo,
                creata_da=identita.user_id, creata_ruolo=identita.ruolo,
                metodo=identita.metodo, idempotency_key=idempotency_key, adesso=adesso,
                impronta_attesa=impronta_piano)
            pratica = await repo.leggi_pratica(session, pratica_id)
            assert pratica is not None
    except CriteriNonValidi as exc:
        raise ErroreApi("LC-422-CRITERI", str(exc)) from None
    except SQLAlchemyError as exc:
        raise pr.errore_db("applica_scarto", exc) from None
    return pr.rappresenta(pratica), True


# ── Esecuzione (lavoratore) ──────────────────────────────────────────────

#: Stati del PdA di una cancellazione già iniziata da QUESTA pratica (ripresa)
STATI_IN_RIPRESA = ("ELIMINAZIONE", dom.PDA_ELIMINAZIONE_INCOMPLETA)


async def _ricontrolla(session: AsyncSession, pratica: Mapping[str, Any], aip_id: str,
                       adesso: datetime, *, in_ripresa: bool = False
                       ) -> tuple[dom.DatiPda | None, str | None]:
    """Blocchi del PdA ricalcolati sui dati di ADESSO (stessa query e stessa
    funzione dell'anteprima). Revisione 3 di R05 (T6): anche alla RIPRESA di una
    cancellazione (crash o ripresa del conservatore), ignorando solo lo stato
    ELIMINAZIONE/ELIMINAZIONE_INCOMPLETA che la pratica stessa ha dato al PdA:
    classe diventata permanente, scadenza estesa, fascicolo riaperto,
    esibizione in corso bloccano la ripresa come la prima esecuzione."""
    riga = (await session.execute(text(_sql_candidati({}, solo_aip=True)), {
        "ente": pratica["tenant_id"], "aip": aip_id, "esclusa": str(pratica["id"]),
        "classi": None, "anni": None})).mappings().first()
    if riga is None:
        return None, dom.STATO_NON_CONSERVATO
    pda = dati_pda(riga)
    if in_ripresa:
        if pda.stato not in STATI_IN_RIPRESA:
            return pda, dom.STATO_NON_CONSERVATO
        pda = dataclasses.replace(pda, stato="CONSERVATO")
    return pda, dom.motivo_blocco_pda(pda, adesso)


async def _segna(session: AsyncSession, oggetto_id: int, esito: str, motivo: str | None,
                 dettagli: Mapping[str, Any], adesso: datetime) -> None:
    await session.execute(text(
        "UPDATE public.pratiche_oggetti SET esito = :esito, motivo = :motivo, "
        "dettagli = dettagli || CAST(:d AS jsonb), aggiornato_il = :adesso, "
        "tentativi = tentativi + 1 WHERE id = :id"),
        {"id": oggetto_id, "esito": esito, "motivo": motivo, "adesso": adesso,
         "d": json.dumps(dict(dettagli), default=str)})


async def _chiavi_attese(session: AsyncSession, aip: Mapping[str, Any], aip_id: str,
                         prefisso: str) -> tuple[set[str], str | None]:
    """Chiavi del PdA che il database dichiara (manifest, IPdA, file) e motivo
    di blocco se una di esse non sta sotto il prefisso del PdA o il bucket del
    PdA non è quello conservativo configurato (revisione R05, R2)."""
    attese = {str(c) for c in (aip["s3_key"], aip["sincro_xml_key"]) if c}
    righe = (await session.execute(text(
        "SELECT s3_key FROM public.documenti WHERE aip_id = CAST(:aip AS uuid) "
        "AND s3_key IS NOT NULL"), {"aip": aip_id})).scalars().all()
    attese.update(str(c) for c in righe)
    if aip["s3_bucket"] and str(aip["s3_bucket"]) != storage.bucket_worm():
        return attese, dom.OGGETTI_MANCANTI
    if any(not c.startswith(prefisso) for c in attese):
        return attese, dom.OGGETTI_MANCANTI
    return attese, None


async def elimina_pda(pratica: Mapping[str, Any], oggetto: Mapping[str, Any],
                      adesso: datetime) -> str:
    """Un PdA della pratica: ricontrollo, retention di TUTTE le versioni, poi
    cancellazione. Restituisce l'esito scritto (ELIMINATO, BLOCCATO o
    NON_COMPLETATO).

    Revisione R05:
      - R10: ricontrollo e passaggio a ELIMINAZIONE in UNA transazione con il
        PdA bloccato (``FOR UPDATE``: distribution prende ``FOR SHARE`` sullo
        stesso PdA quando registra un PdD) e ``rowcount == 1`` obbligatorio
        prima di qualunque cancellazione;
      - R2: nessuna versione trovata, o una chiave dichiarata dal database
        assente, è un BLOCCO, mai un PdA «eliminato».
    Revisione 2 di R05:
      - S6: le versioni si elencano DOPO il lock del PdA (e si rielencano dopo
        le cancellazioni: ``_cancella_versioni``);
      - S3: una cancellazione iniziata che non si può completare (legal hold
        arrivato durante lo scarto, retention, errore persistente) chiude
        l'oggetto NON_COMPLETATO e il PdA ELIMINAZIONE_INCOMPLETA, con l'elenco
        di ciò che è stato cancellato e di ciò che resta: mai un ciclo infinito."""
    from libs.shared.db import get_async_session

    aip_id = str(oggetto["aip_id"])
    s3 = storage.client()
    bucket = storage.bucket_worm()
    prefisso = f"{pratica['tenant_id']}/{aip_id}/"
    if oggetto["esito"] == dom.SELEZIONATO:
        async with get_async_session() as session:
            aip = (await session.execute(text(
                "SELECT stato::text AS stato, s3_bucket, s3_key, sincro_xml_key "
                "FROM public.aip WHERE id = CAST(:aip AS uuid) AND tenant_id = :t FOR UPDATE"),
                {"aip": aip_id, "t": pratica["tenant_id"]})).mappings().first()
            motivo: str | None = dom.STATO_NON_CONSERVATO if aip is None else None
            if motivo is None:
                _, motivo = await _ricontrolla(session, pratica, aip_id, adesso)
            attese: set[str] = set()
            versioni: list[dom.OggettoWorm] = []
            fase = "esecuzione"
            if motivo is None:
                assert aip is not None
                # S6: elenco con il PdA BLOCCATO (nessun cambio di stato concorrente)
                versioni = await asyncio.to_thread(storage.versioni_del_pda, s3, bucket,
                                                   prefisso)
                attese, motivo = await _chiavi_attese(session, aip, aip_id, prefisso)
                # Controllo PRIMA di ogni cancellazione: nulla si tenta (contratto Q2)
                motivo = motivo or dom.motivo_blocco_oggetti(versioni, adesso, attese=attese)
                fase = "esecuzione_storage"
            if motivo is None:
                r = await session.execute(text(
                    "UPDATE public.aip SET stato = 'ELIMINAZIONE', updated_at = :adesso "
                    "WHERE id = CAST(:aip AS uuid) AND tenant_id = :t AND stato = 'CONSERVATO'"),
                    {"aip": aip_id, "t": pratica["tenant_id"], "adesso": adesso})
                if r.rowcount != 1:
                    motivo = dom.STATO_NON_CONSERVATO
            if motivo is not None:
                await _segna(session, oggetto["id"], dom.BLOCCATO, motivo,
                             {"fase": fase, "versioni": len(versioni),
                              "chiavi_attese": len(attese)}, adesso)
                return dom.BLOCCATO
            # T1: l'INVENTARIO delle versioni (chiave, versione) si registra nella
            # stessa transazione che porta il PdA in ELIMINAZIONE, cioè PRIMA della
            # prima cancellazione: ogni chiusura successiva (anche dopo un processo
            # ucciso) decide dallo storage che cosa è stato distrutto.
            await _segna(session, oggetto["id"], "IN_CORSO", None,
                         {"versioni": len(versioni), "versioni_iniziali": len(versioni),
                          "versioni_eliminate": 0, "inventario": dom.inventario(versioni)},
                         adesso)
    else:
        # Ripresa (crash o ripresa del conservatore dopo ESEGUITA_CON_ECCEZIONI).
        # T6: i blocchi lato database si ricontrollano come alla prima esecuzione,
        # con il PdA bloccato; solo senza blocchi il PdA torna in ELIMINAZIONE.
        async with get_async_session() as session:
            aip = (await session.execute(text(
                "SELECT stato::text AS stato FROM public.aip "
                "WHERE id = CAST(:aip AS uuid) AND tenant_id = :t FOR UPDATE"),
                {"aip": aip_id, "t": pratica["tenant_id"]})).mappings().first()
            motivo = dom.STATO_NON_CONSERVATO if aip is None else None
            if motivo is None:
                _, motivo = await _ricontrolla(session, pratica, aip_id, adesso,
                                               in_ripresa=True)
            if motivo is None:
                await session.execute(text(
                    "UPDATE public.aip SET stato = 'ELIMINAZIONE', updated_at = :adesso "
                    "WHERE id = CAST(:aip AS uuid) AND tenant_id = :t "
                    "AND stato = 'ELIMINAZIONE_INCOMPLETA'"),
                    {"aip": aip_id, "t": pratica["tenant_id"], "adesso": adesso})
        if motivo is not None:
            rimaste: list[dom.OggettoWorm] | None
            try:
                rimaste = await asyncio.to_thread(storage.versioni_del_pda, s3, bucket, prefisso)
            except Exception:  # noqa: BLE001 — storage non leggibile: mai «nulla distrutto»
                rimaste = None
            return await _chiudi_non_completato(pratica, oggetto, motivo, 0, rimaste, adesso)
    return await _cancella_versioni(pratica, oggetto, adesso, s3=s3, bucket=bucket,
                                    prefisso=prefisso)


#: S6: giri di elenco + cancellazione al più per PdA (versioni nate dopo l'elenco)
GIRI_ELENCO = 3
_CAMPIONE = 50


def _campione(versioni: list[dom.OggettoWorm]) -> list[str]:
    return sorted({v.chiave for v in versioni})[:_CAMPIONE]


async def _progresso(oggetto_id: int, eliminate: list[dom.OggettoWorm], adesso: datetime, *,
                     tentativo: bool = False) -> tuple[int, int]:
    """Somma le versioni cancellate in questo tentativo a quelle dei tentativi
    precedenti (``dettagli.versioni_eliminate``) e, con ``tentativo``, conta un
    tentativo fallito (``dettagli.tentativi_falliti``). Restituisce (cancellate
    in tutto, tentativi falliti)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        riga = (await session.execute(text(
            "UPDATE public.pratiche_oggetti SET dettagli = dettagli || jsonb_build_object("
            "  'chiavi_eliminate', COALESCE(dettagli -> 'chiavi_eliminate', '[]'::jsonb) "
            "    || CAST(:chiavi AS jsonb), "
            "  'versioni_eliminate', COALESCE((dettagli ->> 'versioni_eliminate')::int, 0) + :n, "
            "  'tentativi_falliti', COALESCE((dettagli ->> 'tentativi_falliti')::int, 0) + :t), "
            "tentativi = tentativi + :t, aggiornato_il = :adesso WHERE id = :id "
            "RETURNING (dettagli ->> 'versioni_eliminate')::int AS eliminate, "
            "(dettagli ->> 'tentativi_falliti')::int AS tentativi"),
            {"id": oggetto_id, "n": len(eliminate), "t": 1 if tentativo else 0,
             "chiavi": json.dumps(_campione(eliminate)), "adesso": adesso})).mappings().one()
    return int(riga["eliminate"] or 0), int(riga["tentativi"])


async def _inventario_registrato(oggetto_id: int) -> set[tuple[str, str]] | None:
    """Revisione 4 di R05 (U4): inventario attuale dell'oggetto (None per gli
    oggetti avviati prima di T1, che non ne hanno uno)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        valore = (await session.execute(text(
            "SELECT dettagli -> 'inventario' FROM public.pratiche_oggetti WHERE id = :id"),
            {"id": oggetto_id})).scalar_one()
    if isinstance(valore, str):
        valore = json.loads(valore)
    if valore is None:
        return None
    return {(str(k), str(v)) for k, v in valore}


async def _estendi_inventario(oggetto_id: int, nuove: list[dom.OggettoWorm],
                              adesso: datetime) -> None:
    """Revisione 4 di R05 (U4): versioni nate sotto il prefisso del PdA DOPO
    l'inventario si aggiungono all'inventario, con COMMIT, PRIMA di cancellarle:
    se il processo muore subito dopo la cancellazione, la chiusura le vede
    mancare dallo storage e non dichiara mai «nulla distrutto»."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.pratiche_oggetti SET dettagli = dettagli || jsonb_build_object("
            "  'inventario', (dettagli -> 'inventario') || CAST(:nuove AS jsonb), "
            "  'versioni_dopo_inventario', "
            "    COALESCE((dettagli ->> 'versioni_dopo_inventario')::int, 0) + :n), "
            "aggiornato_il = :adesso WHERE id = :id AND dettagli ? 'inventario'"),
            {"id": oggetto_id, "nuove": json.dumps(dom.inventario(nuove)), "n": len(nuove),
             "adesso": adesso})
    logger.warning("scarto_versioni_dopo_inventario", oggetto_id=oggetto_id,
                   versioni=len(nuove), campione=_campione(nuove)[:5])


async def _chiudi_non_completato(pratica: Mapping[str, Any], oggetto: Mapping[str, Any],
                                 causa: str, eliminate_contate: int,
                                 rimaste: list[dom.OggettoWorm] | None, adesso: datetime) -> str:
    """S3: chiusura di un PdA la cui cancellazione non si può completare.

    Revisione 3 di R05 (T1): «nulla distrutto» si decide dallo STORAGE, non da
    un contatore: inventario registrato prima della prima cancellazione
    (``dettagli.inventario``) tutto ancora presente fra le versioni ``rimaste``
    e nessuna cancellazione contata → PdA di nuovo CONSERVATO, oggetto BLOCCATO
    (come il controllo preventivo). In ogni altro caso — anche inventario
    assente o storage non leggibile (``rimaste`` None) — oggetto NON_COMPLETATO
    (stato terminale fino alla ripresa) con ciò che resta e ciò che manca, PdA
    ELIMINAZIONE_INCOMPLETA (allarme), voce d'audit."""
    from libs.shared.db import get_async_session

    aip_id = str(oggetto["aip_id"])
    async with get_async_session() as session:
        attuali = (await session.execute(text(
            "SELECT dettagli FROM public.pratiche_oggetti WHERE id = :id FOR UPDATE"),
            {"id": oggetto["id"]})).scalar_one()
        if isinstance(attuali, str):
            attuali = json.loads(attuali)
        attuali = dict(attuali or {})
        inventario = attuali.get("inventario")
        contate = max(eliminate_contate, int(attuali.get("versioni_eliminate") or 0))
        distrutte = dom.versioni_distrutte(inventario, rimaste)
        lista_rimaste = list(rimaste or [])
        # Revisione 4 di R05 (U1): storage non leggibile (``rimaste`` None) → numero
        # di versioni rimaste NON determinabile (mai 0, mai un TypeError che
        # annullerebbe la chiusura lasciando l'oggetto IN_CORSO per sempre)
        n_rimaste: int | None = None if rimaste is None else len(lista_rimaste)
        dettagli: dict[str, Any] = {
            "fase": "cancellazione", "causa": causa, "versioni_rimaste": n_rimaste,
            "chiavi_rimaste": _campione(lista_rimaste), "chiusa_il": adesso.isoformat(),
            "verifica_storage": ("inventario" if distrutte is not None
                                 else "non_determinabile")}
        if rimaste is not None:
            # Revisione R09 (Z2, D26): TUTTE le versioni trattenute (chiave e
            # version id), che la verifica continua cerca una per una
            dettagli["versioni_trattenute"] = dom.inventario(
                v for v in lista_rimaste if not storage.e_marcatore(v))
        if dom.nulla_distrutto(inventario, rimaste, contate):
            await session.execute(text(
                "UPDATE public.aip SET stato = 'CONSERVATO', updated_at = :adesso "
                "WHERE id = CAST(:aip AS uuid) AND tenant_id = :t "
                "AND stato IN ('ELIMINAZIONE', 'ELIMINAZIONE_INCOMPLETA')"),
                {"aip": aip_id, "t": pratica["tenant_id"], "adesso": adesso})
            await _segna(session, oggetto["id"], dom.BLOCCATO, causa, dettagli, adesso)
            return dom.BLOCCATO
        # Versioni distrutte: le più fra quelle contate e quelle che lo storage non
        # ha più (il contatore si perde se il processo muore fra due registrazioni)
        eliminate_totali = max(contate, len(distrutte or []))
        dettagli["versioni_eliminate"] = eliminate_totali
        dettagli["chiavi_eliminate"] = sorted(
            {str(c) for c in (attuali.get("chiavi_eliminate") or [])}
            | {k for k, _ in (distrutte or [])})[:_CAMPIONE]
        await session.execute(text(
            "UPDATE public.aip SET stato = 'ELIMINAZIONE_INCOMPLETA', updated_at = :adesso "
            "WHERE id = CAST(:aip AS uuid) AND tenant_id = :t "
            "AND stato IN ('ELIMINAZIONE', 'ELIMINAZIONE_INCOMPLETA')"),
            {"aip": aip_id, "t": pratica["tenant_id"], "adesso": adesso})
        await _segna(session, oggetto["id"], dom.NON_COMPLETATO, causa, dettagli, adesso)
        await registra_audit(session, evento="ciclo_vita.scarto.pda_eliminazione_incompleta",
                             tenant_id=str(pratica["tenant_id"]), aip_id=aip_id,
                             dettagli={"pratica_id": str(pratica["id"]), "urn": oggetto["urn"],
                                       "causa": causa, "versioni_eliminate": eliminate_totali,
                                       "versioni_rimaste": n_rimaste,
                                       "verifica_storage": dettagli["verifica_storage"]})
    logger.error("scarto_pda_eliminazione_incompleta", pratica_id=str(pratica["id"]),
                 aip_id=aip_id, causa=causa, versioni_eliminate=eliminate_totali,
                 versioni_rimaste=n_rimaste, verifica_storage=dettagli["verifica_storage"])
    return dom.NON_COMPLETATO


async def _cancella_versioni(pratica: Mapping[str, Any], oggetto: Mapping[str, Any],
                             adesso: datetime, *, s3: Any, bucket: str, prefisso: str) -> str:
    """Cancellazione delle versioni del PdA (oggetto IN_CORSO, PdA in ELIMINAZIONE).

    Ogni giro rielenca il prefisso (S6: una versione nata dopo l'elenco non
    sfugge) e cancella ciò che è cancellabile ADESSO. Una versione non
    cancellabile (legal hold, retention) — vista nell'elenco o scoperta da un
    rifiuto dello storage riletto subito dopo — è un blocco DEFINITIVO: la
    cancellazione del PdA si ferma (si conserva quanto più possibile) e
    l'oggetto si chiude. Un altro errore dello storage è un tentativo fallito:
    oltre ``SCARTO_TENTATIVI_MAX_PDA`` l'oggetto si chiude (ERRORE_PERSISTENTE).

    Revisione 4 di R05 (U4): ogni giro confronta le versioni elencate con
    l'inventario registrato; quelle nate dopo (scrittore concorrente sotto il
    prefisso) entrano nell'inventario con commit PRIMA di essere cancellate."""
    from libs.shared.db import get_async_session

    eliminate: list[dom.OggettoWorm] = []
    causa: str | None = None
    rimaste: list[dom.OggettoWorm] = []
    try:
        inventariate = await _inventario_registrato(oggetto["id"])
        for _ in range(GIRI_ELENCO):
            versioni = await asyncio.to_thread(storage.versioni_del_pda, s3, bucket, prefisso)
            if not versioni:
                break
            # U4: nessuna versione si cancella se non è nell'inventario registrato
            if inventariate is not None:
                nuove = [v for v in versioni if (v.chiave, v.version_id) not in inventariate]
                if nuove:
                    await _estendi_inventario(oggetto["id"], nuove, adesso)
                    inventariate.update((v.chiave, v.version_id) for v in nuove)
            # Controllo di TUTTE le versioni elencate prima di cancellare in questo
            # giro: un blocco già visibile ferma tutto (nulla in più si distrugge)
            causa = next((m for m in (dom.motivo_blocco_oggetto(v, adesso) for v in versioni)
                          if m is not None), None)
            if causa is not None:
                break
            for versione in versioni:
                motivo = dom.motivo_blocco_oggetto(versione, adesso)
                if motivo is None:
                    try:
                        await asyncio.to_thread(storage.elimina_versione, s3, bucket, versione)
                    except Exception:
                        attuale = await asyncio.to_thread(storage.stato_versione, s3, bucket,
                                                          versione)
                        if attuale is None:     # già cancellata (ripresa)
                            eliminate.append(versione)
                            continue
                        motivo = dom.motivo_blocco_oggetto(attuale, adesso)
                        if motivo is None:
                            raise               # errore dello storage: tentativo fallito
                    else:
                        eliminate.append(versione)
                        continue
                causa = motivo if motivo in dom.BLOCCHI_DEFINITIVI else dom.VERSIONI_RIMASTE
                break
            if causa is not None:
                break
        rimaste = await asyncio.to_thread(storage.versioni_del_pda, s3, bucket, prefisso)
        if rimaste and causa is None:
            causa = dom.VERSIONI_RIMASTE
    except lease.LeasePerso:
        await _progresso(oggetto["id"], eliminate, adesso)
        raise
    except Exception as exc:
        totali, tentativi = await _progresso(oggetto["id"], eliminate, adesso, tentativo=True)
        if tentativi < await tentativi_massimi(pratica["tenant_id"]):
            raise
        logger.error("scarto_pda_errore_persistente", pratica_id=str(pratica["id"]),
                     aip_id=str(oggetto["aip_id"]), tentativi=tentativi,
                     errore=type(exc).__name__)
        leggibili: list[dom.OggettoWorm] | None
        try:
            leggibili = await asyncio.to_thread(storage.versioni_del_pda, s3, bucket, prefisso)
        except Exception:  # noqa: BLE001 — storage non leggibile: si chiude comunque,
            leggibili = None  # mai come «nulla distrutto» (T1)
        return await _chiudi_non_completato(pratica, oggetto, dom.ERRORE_PERSISTENTE, totali,
                                            leggibili, adesso)
    totali, _ = await _progresso(oggetto["id"], eliminate, adesso)
    if causa is not None:
        return await _chiudi_non_completato(pratica, oggetto, causa, totali, rimaste, adesso)
    async with get_async_session() as session:
        iniziali = (await session.execute(text(
            "SELECT COALESCE(jsonb_array_length(dettagli -> 'inventario'), 0) "
            "FROM public.pratiche_oggetti WHERE id = :id"), {"id": oggetto["id"]})).scalar_one()
        # T1: tutte le versioni dell'inventario non ci sono più (rielenco vuoto)
        totali = max(totali, int(iniziali or 0))
        await _segna(session, oggetto["id"], "ELIMINATO", None,
                     {"versioni_eliminate_nel_giro": len(eliminate),
                      "versioni_eliminate": totali}, adesso)
        # T5: il FATTO dell'esecuzione, scrivibile solo dal ruolo del lavoratore
        # (verumdoc_ciclo_vita_lavoratore, migrazione 043): il registro delle
        # impronte firma il tombstone solo se lo trova (riscontro della hashchain)
        await registra_esito_esecuzione(session, pratica, oggetto, dom_esito="ELIMINATO",
                                        dettagli={"versioni": totali,
                                                  "giro": giro_esecuzione(pratica)})
        await registra_audit(session, evento="ciclo_vita.scarto.pda_eliminato",
                             tenant_id=str(pratica["tenant_id"]), aip_id=str(oggetto["aip_id"]),
                             dettagli={"pratica_id": str(pratica["id"]), "urn": oggetto["urn"],
                                       "versioni": totali})
    return "ELIMINATO"


async def registra_esito_esecuzione(session: AsyncSession, pratica: Mapping[str, Any],
                                    oggetto: Mapping[str, Any], *, dom_esito: str,
                                    dettagli: Mapping[str, Any]) -> None:
    """Revisione 3 di R05 (T5): esito di esecuzione in ``esiti_esecuzione``
    (append-only, INSERT concesso SOLO a ``verumdoc_ciclo_vita_lavoratore``: il
    login dell'API non lo può scrivere). Idempotente per (tipo, pratica, PdA,
    esito): una ripresa dopo un crash non lo duplica."""
    await session.execute(text(
        "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
        "dettagli) VALUES ('SCARTO', :p, :t, CAST(:aip AS uuid), :esito, CAST(:d AS jsonb)) "
        "ON CONFLICT (tipo, pratica_id, aip_id, esito) DO NOTHING"),
        {"p": pratica["id"], "t": pratica["tenant_id"], "aip": str(oggetto["aip_id"]),
         "esito": dom_esito, "d": json.dumps(dict(dettagli), default=str)})


def giro_esecuzione(pratica: Mapping[str, Any]) -> int:
    """Numero del giro di esecuzione (1; >1 per le riprese dopo
    ESEGUITA_CON_ECCEZIONI, revisione 2 di R05, S3)."""
    return int(((pratica.get("dati") or {}).get("esecuzione") or {}).get("giro") or 1)


def nome_verbale(pratica: Mapping[str, Any]) -> str:
    """Un verbale per giro: il primo con il nome di sempre, le riprese
    «…_ripresa_<n>.xml» (verbale integrativo)."""
    giro = giro_esecuzione(pratica)
    if giro == 1:
        return f"verbale_scarto_{pratica['id']}.xml"
    return f"verbale_scarto_{pratica['id']}_ripresa_{giro}.xml"


async def _verbale(pratica: Mapping[str, Any], adesso: datetime) -> dict[str, Any]:
    """Scrive (o ritrova) il verbale del giro nel WORM: XML a pezzi su file temporaneo.

    Revisione 2 di R05 (S3): per ogni PdA NON_COMPLETATO il verbale dice quante
    versioni sono state cancellate e quante restano (con un campione delle
    chiavi) e la causa; un verbale per giro di esecuzione.

    Revisione 3 di R05 (T7): tre fasi, mai una transazione aperta durante la
    scrittura nel WORM (il verbale di una pratica con 100.000 PdA può essere
    grande): lettura dei dati su file temporaneo (transazione di sola lettura,
    chiusa), composizione e caricamento nel WORM (nessuna transazione),
    registrazione dell'allegato con la pratica bloccata (transazione breve; un
    verbale dello stesso giro già registrato da un'altra replica vince)."""
    from libs.shared.db import get_async_session

    nome = nome_verbale(pratica)
    dati = dict(pratica["dati"] or {})
    eseguita_il = (dati.get("esecuzione") or {}).get("conclusa_il") or adesso.isoformat()
    with tempfile.TemporaryFile() as f, tempfile.TemporaryFile() as elementi:
        # 1. dati (transazione chiusa prima di scrivere nel WORM)
        async with get_async_session() as session:
            allegati = await repo.allegati(session, pratica["id"])
            esistente = next((a for a in allegati if a["tipo"] == st.ALLEGATO_VERBALE
                              and a["nome_file"] == nome), None)
            if esistente is not None:
                return esistente
            storia = await repo.cronologia(session, pratica["id"])
            ente = await repo.ente(session, pratica["tenant_id"]) or {}
            conteggi = await repo.conteggi_oggetti(session, pratica["id"])
            # Elementi su file temporaneo (una riga JSON per PdA): mai tutti in memoria
            righe = await session.stream(text(
                "SELECT aip_id::text AS aip_id, urn, esito, motivo, "
                "(dettagli ->> 'versioni_eliminate')::int AS versioni_eliminate, "
                "(dettagli ->> 'versioni_rimaste')::int AS versioni_rimaste, "
                "dettagli -> 'chiavi_eliminate' AS chiavi_eliminate, "
                "dettagli -> 'chiavi_rimaste' AS chiavi_rimaste, "
                "dettagli ->> 'verifica_storage' AS verifica_storage "
                "FROM public.pratiche_oggetti WHERE pratica_id = :p ORDER BY id"),
                {"p": pratica["id"]})
            async for r in righe.mappings():
                riga = dict(r)
                for campo in ("chiavi_eliminate", "chiavi_rimaste"):
                    if isinstance(riga[campo], str):
                        riga[campo] = json.loads(riga[campo])
                elementi.write(json.dumps(riga, default=str).encode() + b"\n")
        # 2. composizione e scrittura nel WORM, fuori da ogni transazione
        elementi.seek(0)

        def scrivi(pezzo: str) -> None:
            f.write(pezzo.encode("utf-8"))

        scrivi_verbale_scarto(
            scrivi, pratica={"id": str(pratica["id"]), "motivo": pratica["motivo"],
                             "criteri": dati.get("criteri"),
                             "impronta_piano": pratica["impronta_piano"],
                             "eseguita_il": eseguita_il,
                             "giro": giro_esecuzione(pratica)},
            ente={"id": str(pratica["tenant_id"]), "denominazione": ente.get("denominazione"),
                  "codice_ipa": ente.get("codice_ipa")},
            conservatore=settings.conservatore_denominazione,
            cronologia=[{**p, "created_at": p["created_at"].isoformat()} for p in storia],
            allegati=[a for a in allegati if a["tipo"] in st.ALLEGATI_CARICABILI],
            elementi=(json.loads(riga) for riga in elementi), conteggi=conteggi)
        f.seek(0)
        caricato = await pr.carica_documento(
            pratica, tipo=st.ALLEGATO_VERBALE, nome_file=nome,
            content_type="application/xml", f=f, adesso=datetime.fromisoformat(eseguita_il))
    # 3. registrazione con la pratica bloccata (transazione breve)
    async with get_async_session() as session:
        ancora = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert ancora is not None
        gia = next((a for a in await repo.allegati(session, pratica["id"])
                    if a["tipo"] == st.ALLEGATO_VERBALE and a["nome_file"] == nome), None)
        if gia is not None:
            return gia
        return await pr.registra_documento(session, ancora, caricato)


async def _prepara_ripresa(pratica: Mapping[str, Any], adesso: datetime) -> dict[str, Any]:
    """S3: all'inizio di una RIPRESA (pratica ESEGUITA_CON_ECCEZIONI con
    l'esecuzione chiesta dal conservatore) gli oggetti NON_COMPLETATO tornano
    IN_CORSO con i tentativi azzerati: il lavoratore riprova la cancellazione
    dei soli PdA incompleti. Una volta per giro (``dati.esecuzione.ripresa_il``)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        dati = dict(attuale["dati"] or {})
        esecuzione = dict(dati.get("esecuzione") or {})
        if attuale["stato"] != st.ESEGUITA_CON_ECCEZIONI or esecuzione.get("ripresa_il"):
            return attuale
        r = await session.execute(text(
            "UPDATE public.pratiche_oggetti SET esito = 'IN_CORSO', tentativi = 0, "
            "dettagli = dettagli - 'tentativi_falliti', "
            "aggiornato_il = :adesso WHERE pratica_id = :p AND esito = 'NON_COMPLETATO'"),
            {"p": pratica["id"], "adesso": adesso})
        esecuzione["ripresa_il"] = adesso.isoformat()
        dati["esecuzione"] = esecuzione
        await repo.aggiorna_pratica(session, pratica["id"], adesso, dati=dati)
        await registra_audit(session, evento="ciclo_vita.scarto.ripresa",
                             tenant_id=str(pratica["tenant_id"]),
                             dettagli={"pratica_id": str(pratica["id"]),
                                       "giro": giro_esecuzione(attuale),
                                       "pda_ripresi": r.rowcount})
    return {**attuale, "dati": dati}


async def esegui_passo(pratica: Mapping[str, Any], registro: RegistroImpronte, *,
                       adesso: datetime, lotto: int) -> bool:
    """Un passo dell'esecuzione. ``True`` se resta lavoro (il lavoratore
    continua subito), ``False`` se la pratica è conclusa (ESEGUITA, o — S3 —
    ESEGUITA_CON_ECCEZIONI se restano PdA NON_COMPLETATO)."""
    from app.application import autorizzazioni
    from libs.shared.db import get_async_session

    # Revisione 4 di R05 (U2): catena delle autorizzazioni verificata dal
    # lavoratore (una volta per punto della cronologia: riga esiti_esecuzione)
    await autorizzazioni.assicura(pratica, adesso)
    if pratica["stato"] == st.ESEGUITA_CON_ECCEZIONI:
        pratica = await _prepara_ripresa(pratica, adesso)
    async with get_async_session() as session:
        pendenti = (await session.execute(text(
            "SELECT id, aip_id, urn, esito, esito_piano FROM public.pratiche_oggetti "
            "WHERE pratica_id = :p AND esito IN ('SELEZIONATO', 'IN_CORSO') "
            "ORDER BY id LIMIT :lim"),
            {"p": pratica["id"], "lim": lotto})).mappings().all()
    if pendenti:
        for oggetto in pendenti:
            # R11: lease rinnovato prima di ogni PdA (LeasePerso ferma il passo)
            await lease.rinnova_nel_passo()
            if oggetto["esito_piano"] != dom.SELEZIONATO:
                # U2: si cancella solo ciò che il piano APPROVATO selezionava
                # (esito_piano congelato dal trigger della migrazione 044)
                async with get_async_session() as session:
                    await _segna(session, oggetto["id"], dom.BLOCCATO, dom.FUORI_PIANO,
                                 {"fase": "esecuzione", "esito_piano": oggetto["esito_piano"],
                                  "esito_trovato": oggetto["esito"]}, adesso)
                logger.error("scarto_oggetto_fuori_piano", pratica_id=str(pratica["id"]),
                             aip_id=str(oggetto["aip_id"]), esito_piano=oggetto["esito_piano"])
                continue
            await elimina_pda(pratica, oggetto, adesso)
        return True
    dati = dict(pratica["dati"] or {})
    esecuzione = dict(dati.get("esecuzione") or {})
    if not esecuzione.get("conclusa_il"):
        esecuzione["conclusa_il"] = adesso.isoformat()
        dati["esecuzione"] = esecuzione
        async with get_async_session() as session:
            await repo.aggiorna_pratica(session, pratica["id"], adesso, dati=dati)
        pratica = {**pratica, "dati": dati}
    verbale = await _verbale(pratica, adesso)
    # Foglie «scarto» (tombstone) dei PdA eliminati per intero, a lotti
    async with get_async_session() as session:
        senza_foglia = (await session.execute(text(
            "SELECT id, aip_id::text AS aip_id, urn FROM public.pratiche_oggetti "
            "WHERE pratica_id = :p AND esito = 'ELIMINATO' AND foglia_id IS NULL "
            "ORDER BY id LIMIT :lim"), {"p": pratica["id"], "lim": lotto})).mappings().all()
    for o in senza_foglia:
        await lease.rinnova_nel_passo()
        foglia = await registro.aggiungi_foglia(
            tipo="scarto", pda=o["urn"],
            riferimento={"pratica": str(pratica["id"]), "aip": o["aip_id"],
                         "verbale_sha256": verbale["sha256"]},
            impronta_documento_pratica=verbale["sha256"])
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.pratiche_oggetti SET foglia_id = :f, aggiornato_il = :adesso "
                "WHERE id = :id"), {"f": foglia, "id": o["id"], "adesso": adesso})
    if senza_foglia:
        return True
    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        if attuale["stato"] not in (st.ESEGUIBILE, st.ESEGUITA_CON_ECCEZIONI):
            return False
        conteggi = await repo.conteggi_oggetti(session, pratica["id"])
        con_eccezioni = bool(conteggi.get(dom.NON_COMPLETATO))
        verso = st.ESEGUITA_CON_ECCEZIONI if con_eccezioni else st.ESEGUITA
        await pr.applica_transizione(
            session, attuale, verso, pr.SISTEMA,
            motivo=("Esecuzione dello scarto conclusa con eccezioni: PdA con cancellazione "
                    "incompleta (vedi verbale)") if con_eccezioni
            else "Esecuzione dello scarto conclusa", adesso=adesso, lavoro=None,
            conteggi=conteggi)
        await pr.evento(session, pr.TOPIC_SCARTO_ESEGUITO, str(pratica["id"]), {
            "pratica_id": str(pratica["id"]), "ente": str(pratica["tenant_id"]),
            "conteggi": conteggi, "verbale_sha256": verbale["sha256"],
            "eseguito_il": esecuzione["conclusa_il"]}, pratica["tenant_id"])
        await registra_audit(session, evento="ciclo_vita.scarto.eseguito",
                             tenant_id=str(pratica["tenant_id"]),
                             dettagli={"pratica_id": str(pratica["id"]), "conteggi": conteggi,
                                       "verbale_sha256": verbale["sha256"],
                                       "con_eccezioni": con_eccezioni,
                                       "giro": giro_esecuzione(pratica)})
    if con_eccezioni:
        logger.error("scarto_eseguito_con_eccezioni", pratica_id=str(pratica["id"]),
                     conteggi=conteggi)
    else:
        logger.info("scarto_eseguito", pratica_id=str(pratica["id"]), conteggi=conteggi)
    return False


async def tentativi_massimi(tenant_id: Any) -> int:
    """R09 (D20): tentativi per PdA dal parametro SCARTO_TENTATIVI_PDA (riga del
    conservatore nel database; senza, ``SCARTO_TENTATIVI_MAX_PDA`` dell'ambiente)."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import SCARTO_TENTATIVI_PDA

    valori = await parametri([SCARTO_TENTATIVI_PDA], tenant_id,
                             ambiente={SCARTO_TENTATIVI_PDA: settings.scarto_tentativi_max_pda})
    return int(valori[SCARTO_TENTATIVI_PDA])


# ── R09 (D20): chiusura con eccezioni (lavoratore) ───────────────────────

def impronta_chiusura(pratica: Mapping[str, Any], aip_id: str, urn: str) -> str:
    """Impronta del fatto e della foglia «chiusura_eccezioni» di un PdA: JSON
    canonico di pratica, PdA, motivo, richiedente e istante della richiesta."""
    from libs.catena.canonico import json_canonico

    chiusura = dict((pratica.get("dati") or {}).get("chiusura") or {})
    return hashlib.sha256(json_canonico({
        "tipo": "chiusura_eccezioni", "pratica": str(pratica["id"]), "aip": str(aip_id),
        "urn": urn, "motivo": chiusura.get("motivo"),
        "richiesta_da": chiusura.get("richiesta_da"),
        "richiesta_il": chiusura.get("richiesta_il")})).hexdigest()


async def chiudi_passo(pratica: Mapping[str, Any], registro: RegistroImpronte, *,
                       adesso: datetime, lotto: int) -> bool:
    """R09 (D20): il conservatore ha chiesto «chiudi con eccezioni».

    Il lavoratore riverifica la richiesta (``autorizzazioni.assicura_chiusura``:
    conservatore attivo, motivo, voce d'audit, PdA ancora incompleti), poi per
    ogni PdA NON_COMPLETATO scrive il FATTO ``CHIUSO_CON_ECCEZIONI`` (solo il suo
    ruolo può) e chiede la foglia «chiusura_eccezioni» al registro; infine
    ESEGUITA_CON_ECCEZIONI → CHIUSA_CON_ECCEZIONI (SISTEMA) con audit. Nulla si
    cancella: le versioni rimaste restano nel WORM (verificate dalla verifica
    continua, D26). Ripresa idempotente (fatto e foglia una volta per PdA)."""
    from app.application import autorizzazioni
    from libs.shared.db import get_async_session

    await autorizzazioni.assicura_chiusura(pratica, adesso)
    chiusura = dict((pratica.get("dati") or {}).get("chiusura") or {})
    async with get_async_session() as session:
        oggetti = (await session.execute(text(
            "SELECT id, aip_id::text AS aip_id, urn FROM public.pratiche_oggetti "
            "WHERE pratica_id = :p AND esito = 'NON_COMPLETATO' "
            "AND NOT (dettagli ? 'foglia_chiusura') ORDER BY id LIMIT :lim"),
            {"p": pratica["id"], "lim": lotto})).mappings().all()
    for o in oggetti:
        await lease.rinnova_nel_passo()
        impronta = impronta_chiusura(pratica, o["aip_id"], o["urn"])
        async with get_async_session() as session:
            await session.execute(text(
                "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, "
                "esito, dettagli) VALUES ('SCARTO', :p, :t, CAST(:aip AS uuid), "
                "'CHIUSO_CON_ECCEZIONI', CAST(:d AS jsonb)) "
                "ON CONFLICT (tipo, pratica_id, aip_id, esito) DO NOTHING"),
                {"p": pratica["id"], "t": pratica["tenant_id"], "aip": o["aip_id"],
                 "d": json.dumps({"impronta": impronta, "motivo": chiusura.get("motivo"),
                                  "richiesta_da": chiusura.get("richiesta_da"),
                                  "richiesta_il": chiusura.get("richiesta_il")})})
        foglia = await registro.aggiungi_foglia(
            tipo="chiusura_eccezioni", pda=o["urn"],
            riferimento={"pratica": str(pratica["id"]), "aip": o["aip_id"]},
            impronta_documento_pratica=impronta)
        async with get_async_session() as session:
            await session.execute(text(
                "UPDATE public.pratiche_oggetti SET dettagli = dettagli || jsonb_build_object("
                "'foglia_chiusura', CAST(:f AS text), 'chiusura_impronta', CAST(:i AS text)), "
                "aggiornato_il = :adesso WHERE id = :id"),
                {"f": foglia, "i": impronta, "id": o["id"], "adesso": adesso})
    if oggetti:
        return True
    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        if attuale["stato"] != st.ESEGUITA_CON_ECCEZIONI:
            return False
        conteggi = await repo.conteggi_oggetti(session, pratica["id"])
        await pr.applica_transizione(
            session, attuale, st.CHIUSA_CON_ECCEZIONI, pr.SISTEMA,
            motivo=str(chiusura.get("motivo") or "")[:1000], adesso=adesso, lavoro=None,
            conteggi=conteggi)
        await registra_audit(session, evento="ciclo_vita.scarto.chiusa_con_eccezioni",
                             tenant_id=str(pratica["tenant_id"]),
                             user_id=chiusura.get("richiesta_da"),
                             dettagli={"pratica_id": str(pratica["id"]),
                                       "motivo": chiusura.get("motivo"),
                                       "pda_incompleti": conteggi.get(dom.NON_COMPLETATO, 0)})
    logger.warning("scarto_chiuso_con_eccezioni", pratica_id=str(pratica["id"]),
                   pda_incompleti=conteggi.get(dom.NON_COMPLETATO, 0))
    return False


def impronta_testo(testo: str) -> str:
    return hashlib.sha256(testo.encode()).hexdigest()


__all__ = ["CriteriNonValidi", "anteprima", "applica", "calcola_piano", "chiudi_passo", "dati_pda",
           "elimina_pda", "esegui_passo", "giro_esecuzione", "nome_verbale",
           "normalizza_criteri"]
