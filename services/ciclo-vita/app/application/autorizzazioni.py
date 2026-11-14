# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/autorizzazioni.py — il lavoratore verifica da sé la catena
delle autorizzazioni di uno scarto prima di cancellare (revisione 4 di R05, U2).

Perché: il pod API di ciclo-vita (esposto all'ingress) scrive ``pratiche`` e
``pratiche_oggetti`` con il proprio login. Compromesso, poteva inserire una
pratica di SCARTO già ``ESEGUIBILE`` con ``lavoro = ESEGUI`` (anche per un ente
PA, senza nulla osta) e il lavoratore legittimo la eseguiva: cancellazione vera,
esito di esecuzione autentico, tombstone firmato. Ora, prima di ogni giro di
esecuzione, il lavoratore ricostruisce e controlla tutto ciò che autorizza la
cancellazione, senza fidarsi della riga della pratica:

  1. ITER (``app.domain.autorizzazioni.difetti_iter``): cronologia completa e
     continua BOZZA → PROPOSTA → AUTORIZZATA_PRODUTTORE → (NULLA_OSTA) →
     ESEGUIBILE secondo la macchina a stati, ruoli ammessi, allegati
     obbligatori citati; nulla osta richiesto se la pratica lo richiede O se la
     natura ATTUALE dell'ente (``tenants.natura``) è pubblica amministrazione;
  2. ALLEGATI nel WORM: l'autorizzazione del produttore e il nulla osta citati
     dall'iter si rileggono dallo storage conservativo (bucket e prefisso della
     pratica, versione registrata): impronta e dimensione uguali a quelle
     registrate, retention presente; R08 (D01, D22): la firma
     dell'autorizzazione e del nulla osta si VERIFICA sui byte riletti con
     l'interfaccia dei verificatori (livello ``CICLO_VITA_FIRME_LIVELLO``
     dell'ente, letto qui e non dal campo del database): una firma non valida,
     revocata o non riconducibile a un prestatore fidato blocca; un nulla osta
     senza firma blocca se ``NULLA_OSTA_FIRMA`` è BLOCCANTE;
  3. PIANO: l'impronta ricalcolata dagli oggetti (``aip_id``, ``esito_piano``,
     ``motivo_piano``: congelati dal trigger della migrazione 044) e dai criteri
     è quella approvata (``impronta_piano``, immutabile); ogni PdA SELEZIONATO è
     dell'ente e rispetta i criteri (classe, anno di versamento).

Esito:
  - catena valida → riga ``esiti_esecuzione`` ``AUTORIZZAZIONI_VERIFICATE``
    (scrivibile SOLO dal ruolo del lavoratore, append-only) con il punto della
    cronologia verificato: finché la cronologia non cresce, i passi successivi
    dello stesso giro non ripetono la verifica (un giro avviato si conclude
    anche se nel frattempo la natura dell'ente cambia: R1);
  - catena NON valida → pratica BLOCCATA prima di qualunque cancellazione:
    ``lavoro`` NULL, ``ultimo_errore.codice = LC-409-AUTORIZZAZIONI`` con i
    difetti, voce d'audit ``ciclo_vita.scarto.autorizzazioni_non_verificate``,
    log di errore, allarme ``ScartoAutorizzazioniNonVerificate``.

Revisione R06 (V2): lo stesso schema vale per ANNULLAMENTO e RETTIFICA prima
che il lavoratore scriva il FATTO (``esiti_esecuzione``) che il riscontro della
hashchain pretende (``verifica_puntuale``/``assicura_puntuale``): richiedente
presente, attivo e dell'ente, ruolo ammesso dalla configurazione, cronologia
dalla richiesta allo stato attuale, voce d'audit della richiesta, PdV e PdA
dell'ente e della pratica, una sola pratica di annullamento per PdV. R09 (D23):
con la doppia approvazione richiesta dai parametri ATTUALI dell'ente, un'altra
persona dell'ente, attiva e con un ruolo ammesso, ha approvato (quattro occhi). Difetti:
pratica BLOCCATA (``LC-409-AUTORIZZAZIONI``), nessun atto, nessun fatto, nessuna
foglia; allarme ``PuntualeAutorizzazioniNonVerificate``.

Residuo dichiarato (``docs/architettura/sicurezza.md`` §6 e §8, aggiornato in
R08): un'API compromessa che agisce come un utente legittimo può ancora
costruire un iter completo, ma non può più FABBRICARE l'autorizzazione firmata
né il nulla osta firmato (con NULLA_OSTA_FIRMA BLOCCANTE): servono firme valide
(TOTAL_PASSED, revisione X1) di un FIRMATARIO AUTORIZZATO dal conservatore per
l'ente (codice fiscale o impronta del certificato, revisione X3, VD-P-76),
verificate dal lavoratore. Una firma qualificata personale di un altro titolare
non basta. Resta il riuso di un documento firmato autentico, di un firmatario
autorizzato, di un'altra pratica (il contenuto firmato non è legato alla
pratica) e, con NULLA_OSTA_FIRMA = AVVISO, un nulla osta non firmato o di un
firmatario non in elenco (accettato con avviso).

R12b (VD-C-160): lo stesso controllo (iter, allegati riletti dal WORM, firma
verificata, firmatario autorizzato, riferimento alla pratica nel contenuto
firmato, riuso del documento) vale per la RESTITUZIONE prima che il lavoratore
avvii l'esportazione: fatto ``RESTITUZIONE`` / ``AUTORIZZAZIONI_VERIFICATE``
(migrazione 20261106_053), pratica bloccata senza esportare in caso di difetti.
Per la restituzione il piano non ha criteri da riconfrontare: si controlla che
ogni oggetto sia un PdA dell'ente della pratica (``OGGETTI_ALTRO_ENTE``).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import firme, parametri
from app.config import settings
from app.domain import autorizzazioni as dom_aut
from app.domain import scarto as dom
from app.domain import stati as st
from app.domain.permessi import RUOLI_AVVIO
from app.infrastructure import repository as repo
from app.infrastructure import storage
from libs.shared.security.audit import registra_audit

logger = structlog.get_logger(__name__)

ESITO_VERIFICATE = "AUTORIZZAZIONI_VERIFICATE"
#: Tipi di pratica di cui il lavoratore verifica la catena prima di eseguire:
#: scarto (revisione 4 di R05) e restituzione (R12b, VD-C-160)
TIPI_VERIFICATI = frozenset({st.SCARTO, st.RESTITUZIONE, st.RIVERSAMENTO_USCITA})


def richiede_verifica(pratica: Mapping[str, Any]) -> bool:
    """Il lavoratore verifica la catena prima di eseguire: scarto e restituzione
    sempre; R13b (VD-C-162) il riversamento in uscita SOLO se la pratica richiede
    l'autorizzazione del produttore (fotografia alla creazione)."""
    tipo = pratica.get("tipo")
    if tipo == st.RIVERSAMENTO_USCITA:
        return bool(pratica.get("richiede_autorizzazione_produttore"))
    return tipo in TIPI_VERIFICATI


@dataclass
class Verifica:
    """Esito della verifica completa."""

    difetti: list[str] = field(default_factory=list)
    cronologia_fino_a: int | None = None
    richiede_nulla_osta: bool = True
    natura: str | None = None
    allegati: list[dict[str, Any]] = field(default_factory=list)
    parametri_firme: dict[str, Any] | None = None

    @property
    def valida(self) -> bool:
        return not self.difetti


class AutorizzazioniNonVerificate(Exception):
    """La catena delle autorizzazioni dello scarto non regge: nulla si cancella."""

    codice = dom_aut.CODICE_BLOCCO

    def __init__(self, difetti: list[str]) -> None:
        super().__init__("; ".join(difetti[:5]))
        self.difetti = difetti


async def _ultimo_passo(session: AsyncSession, pratica_id: Any) -> int | None:
    return (await session.execute(text(
        "SELECT max(id) FROM public.pratiche_stati WHERE pratica_id = :p"),
        {"p": pratica_id})).scalar()


async def _gia_verificata(session: AsyncSession, pratica_id: Any, fino_a: int | None,
                          tipo: str = st.SCARTO) -> bool:
    """Una verifica del LAVORATORE (riga che l'API non può scrivere) sullo
    stesso punto della cronologia."""
    if fino_a is None:
        return False
    return bool((await session.execute(text(
        "SELECT EXISTS (SELECT 1 FROM public.esiti_esecuzione WHERE tipo = :tipo "
        "AND pratica_id = :p AND esito = :e AND aip_id IS NULL "
        "AND (dettagli ->> 'cronologia_fino_a')::bigint = :n)"),
        {"p": pratica_id, "e": ESITO_VERIFICATE, "n": fino_a, "tipo": tipo})).scalar_one())


_PIANO = text(
    "SELECT aip_id::text AS aip_id, tenant_id, esito_piano, motivo_piano "
    "FROM public.pratiche_oggetti WHERE pratica_id = :p ORDER BY aip_id")


async def _difetti_piano(session: AsyncSession, pratica: Mapping[str, Any]) -> list[str]:
    """Il piano eseguibile è quello approvato: impronta ricalcolata da oggetti e
    criteri, PdA SELEZIONATI dell'ente e nei criteri (attributi immutabili)."""
    from app.application import scarto as sc

    difetti: list[str] = []
    impronta_approvata = pratica.get("impronta_piano")
    criteri = (pratica.get("dati") or {}).get("criteri")
    if not impronta_approvata or not isinstance(criteri, Mapping):
        return ["PIANO_ASSENTE: impronta del piano o criteri non registrati"]
    try:
        canonici = sc.normalizza_criteri(criteri)
    except (ValueError, TypeError) as exc:
        return [f"CRITERI_NON_VALIDI: {exc}"]
    if canonici != dict(criteri):
        difetti.append("CRITERI_NON_CANONICI: i criteri registrati non sono quelli approvati")
    impronta = dom.ImprontaPiano(st.SCARTO, str(pratica["tenant_id"]), canonici)
    senza_piano = selezionati = altro_ente = 0
    risultato = await session.stream(_PIANO, {"p": pratica["id"]})
    async for riga in risultato.mappings():
        if riga["aip_id"] is None or riga["esito_piano"] not in (dom.SELEZIONATO, dom.BLOCCATO):
            senza_piano += 1
            continue
        if str(riga["tenant_id"]) != str(pratica["tenant_id"]):
            altro_ente += 1
        if riga["esito_piano"] == dom.SELEZIONATO:
            selezionati += 1
        impronta.aggiungi(riga["aip_id"], riga["esito_piano"], riga["motivo_piano"])
    if senza_piano:
        difetti.append(f"OGGETTI_FUORI_PIANO: {senza_piano} oggetti senza esito del piano "
                       "approvato")
    if altro_ente:
        difetti.append(f"OGGETTI_ALTRO_ENTE: {altro_ente} oggetti di un altro ente")
    if impronta.elementi > settings.scarto_max_pda_per_pratica:
        difetti.append(f"PIANO_TROPPO_GRANDE: {impronta.elementi} PdA")
    if selezionati == 0:
        difetti.append("PIANO_VUOTO: nessun PdA selezionato")
    if impronta.hex() != impronta_approvata:
        difetti.append("IMPRONTA_PIANO_DIVERSA: oggetti o criteri non sono quelli del piano "
                       "approvato")
    condizione = " ".join(["a.tenant_id = :ente", *sc.filtri_criteri(canonici,
                                                                     con_scadenza=False)])
    fuori = (await session.execute(text(
        "SELECT count(*) FROM public.pratiche_oggetti o "  # noqa: S608 — frammenti costanti
        "LEFT JOIN public.aip a ON a.id = o.aip_id "
        "LEFT JOIN public.pdv v ON v.id = a.pdv_id "
        "LEFT JOIN public.classi_documentali c ON c.id = v.classe_doc_id "
        "WHERE o.pratica_id = :p AND o.esito_piano = 'SELEZIONATO' "
        f"AND NOT COALESCE(({condizione}), false)"),
        {"p": pratica["id"], "ente": pratica["tenant_id"],
         "classi": canonici.get("classi") or None, "anni": canonici.get("anni") or None})
    ).scalar_one()
    if fuori:
        difetti.append(f"OGGETTI_FUORI_CRITERI: {fuori} PdA selezionati non rispettano i "
                       "criteri approvati (ente, classe, anno di versamento)")
    return difetti


def _rileggi(allegato: Mapping[str, Any], adesso: datetime
            ) -> tuple[list[str], dict[str, Any], bytes | None]:
    """Rilettura sincrona (thread) di un allegato autorizzativo dal WORM; per i
    documenti con firma restituisce anche i byte riletti (verifica in R08)."""
    difetti: list[str] = []
    nome = f"allegato {allegato['tipo']} {allegato['id']}"
    prefisso = f"pratiche/{allegato['tenant_id']}/{allegato['pratica_id']}/"
    if str(allegato["bucket"]) != storage.bucket_worm():
        return [f"ALLEGATO_FUORI_WORM: {nome} non è nello storage conservativo"], {}, None
    if not str(allegato["chiave"]).startswith(prefisso):
        return [f"ALLEGATO_FUORI_PRATICA: {nome} non è sotto il prefisso della pratica"], {}, None
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as f:
        letta = storage.leggi_versione(storage.client(), str(allegato["bucket"]),
                                       str(allegato["chiave"]), allegato.get("version_id"), f)
        if letta is None:
            return [f"ALLEGATO_ASSENTE_DAL_WORM: {nome} non si trova nello storage"], {}, None
        if letta.sha256 != str(allegato["sha256"]) or letta.dimensione != int(
                allegato["dimensione"]):
            difetti.append(f"ALLEGATO_IMPRONTA_DIVERSA: {nome} nel WORM non ha l'impronta "
                           "registrata")
        if letta.conserva_fino_a is None or letta.conserva_fino_a <= adesso:
            difetti.append(f"ALLEGATO_SENZA_RETENTION: {nome} non è protetto dalla retention")
        contenuto = None
        if str(allegato["tipo"]) in firme.ALLEGATI_CON_FIRMA:
            f.seek(0)
            contenuto = f.read()
    return difetti, {"id": str(allegato["id"]), "tipo": allegato["tipo"],
                     "sha256": letta.sha256}, contenuto


async def difetti_firma(allegato: Mapping[str, Any], contenuto: bytes,
                        parametri: firme.ParametriFirme, *, pratica_id: Any = None,
                        transizione: bool = False) -> tuple[list[str], dict[str, Any]]:
    """R08: firma VERIFICATA dei byte riletti dal WORM (l'esito salvato dall'API
    non conta). ``ErroreVerificatoreNonDisponibile`` si propaga: il lavoratore
    ritenta, nulla si cancella né si scrive. R12a (VD-C-145): con ``pratica_id``
    anche il riferimento alla pratica nel contenuto firmato (``transizione``:
    allegato caricato prima dell'obbligo in una pratica allora aperta → avviso)."""
    nome = f"allegato {allegato['tipo']} {allegato['id']}"
    esito = await firme.verifica_documento(contenuto, str(allegato["nome_file"]),
                                           parametri.livello)
    problemi, avviso = firme.problemi_documento(str(allegato["tipo"]), esito, parametri)
    if not problemi and pratica_id is not None:
        problemi, avviso_rif = firme.problemi_riferimento_pratica(
            str(allegato["tipo"]), contenuto, esito, pratica_id, parametri,
            transizione=transizione)
        avviso = "; ".join(a for a in (avviso, avviso_rif) if a) or None
    riassunto = {"firma": esito.get("in_parole"), "verificatore": esito.get("verificatore"),
                 "firmatari": [f.get("firmatario") for f in esito.get("firme") or ()]}
    if avviso:
        riassunto["avviso"] = avviso
    return [f"ALLEGATO_FIRMA_NON_VALIDA: {nome}: {p}" for p in problemi], riassunto


async def _difetti_riuso(session: Any, pratica: Mapping[str, Any],
                         allegati: list[Mapping[str, Any]]) -> list[str]:
    """R12a (VD-C-145): un'autorizzazione o un nulla osta con la STESSA impronta di un
    documento autorizzativo (stessi tipi) di un'altra pratica DELLO STESSO TIPO
    dell'ente (scarto; R12b: restituzione), caricato PRIMA, è un riuso (anche per le
    pratiche in transizione). Revisione R12a: solo i tipi autorizzativi e solo il
    documento più vecchio conta, così nessuno blocca la pratica originale
    ricaricando il suo documento altrove (come ALTRO o in una pratica successiva)."""
    legati = [a for a in allegati if a["tipo"] in firme.ALLEGATI_LEGATI_ALLA_PRATICA]
    if not legati:
        return []
    difetti: list[str] = []
    for a in legati:
        prima = (await session.execute(text(
            "SELECT 1 FROM public.pratiche_allegati o JOIN public.pratiche p "
            "ON p.id = o.pratica_id WHERE o.tenant_id = :t AND o.sha256 = :h "
            "AND o.pratica_id <> :p AND p.tipo = :tipo AND o.tipo = ANY(:tipi) "
            "AND o.created_at < :quando LIMIT 1"),
            {"t": pratica["tenant_id"], "h": str(a["sha256"]), "p": pratica["id"],
             "tipo": pratica["tipo"], "tipi": sorted(firme.ALLEGATI_LEGATI_ALLA_PRATICA),
             "quando": a["created_at"]})).first()
        if prima is not None:
            difetti.append(f"ALLEGATO_GIA_USATO: il documento autorizzativo con impronta "
                           f"{str(a['sha256'])[:16]}… è già allegato a un'altra pratica di "
                           f"{str(pratica['tipo']).lower()} dell'ente")
    return difetti


async def _difetti_oggetti_restituzione(session: Any, pratica: Mapping[str, Any]) -> list[str]:
    """R12b (VD-C-160): ogni oggetto di una restituzione è un PdA dell'ente della
    pratica (una riga scritta dall'API non esporta pacchetti di un altro ente)."""
    altro = (await session.execute(text(
        "SELECT count(*) FROM public.pratiche_oggetti o "
        "LEFT JOIN public.aip a ON a.id = o.aip_id "
        "WHERE o.pratica_id = :p AND (o.tenant_id <> :t OR a.id IS NULL "
        "OR a.tenant_id <> :t)"), {"p": pratica["id"], "t": pratica["tenant_id"]})
    ).scalar_one()
    return [f"OGGETTI_ALTRO_ENTE: {altro} oggetti non sono PdA dell'ente della pratica"] \
        if altro else []


async def _allegati_in_transizione(session: Any, pratica_id: uuid.UUID) -> set[str]:
    """R12a (VD-C-145): allegati autorizzativi già caricati, prima della migrazione
    20261104_051, in pratiche di scarto allora aperte (R12b, VD-C-160: e prima della
    20261106_053 in restituzioni allora aperte); elenco scritto SOLO dalle
    migrazioni, nessun ruolo dei servizi può modificarlo: per loro l'assenza del
    riferimento alla pratica è un avviso."""
    return {str(r) for r in (await session.execute(text(
        "SELECT allegato_id FROM public.ciclo_vita_transizione_r12 WHERE pratica_id = :p"),
        {"p": pratica_id})).scalars().all()}


async def verifica(pratica_id: uuid.UUID, adesso: datetime) -> Verifica:
    """Verifica COMPLETA (database in una transazione di sola lettura, poi WORM
    fuori transazione)."""
    from libs.shared.db import get_async_session

    esito = Verifica()
    async with get_async_session() as session:
        pratica = await repo.leggi_pratica(session, pratica_id)
        if pratica is None or not richiede_verifica(pratica):
            esito.difetti.append("PRATICA_NON_VERIFICABILE: né scarto, né restituzione, né "
                                 "riversamento con l'autorizzazione del produttore")
            return esito
        tipo = str(pratica["tipo"])
        cronologia = await repo.cronologia(session, pratica_id)
        allegati = {str(a["id"]): {**a, "tenant_id": pratica["tenant_id"],
                                   "pratica_id": pratica_id}
                    for a in await repo.allegati(session, pratica_id)}
        ente = await repo.ente(session, pratica["tenant_id"]) or {}
        esito.natura = ente.get("natura")
        # il nulla osta della Soprintendenza riguarda solo lo scarto
        esito.richiede_nulla_osta = tipo == st.SCARTO and (
            bool(pratica["richiede_nulla_osta"]) or dom.richiede_nulla_osta(ente.get("natura")))
        esito.cronologia_fino_a = cronologia[-1]["id"] if cronologia else None
        # R09 (D21): proposta automatica → il fatto del lavoratore che l'ha creata
        del_sistema = tipo == st.SCARTO and bool((await session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM public.esiti_esecuzione WHERE tipo = 'SCARTO' "
            "AND pratica_id = :p AND esito = 'PROPOSTA_GENERATA' AND tenant_id = :t)"),
            {"p": pratica_id, "t": pratica["tenant_id"]})).scalar_one())
        esito.difetti += dom_aut.difetti_iter(
            tipo, cronologia, allegati, richiede_nulla_osta=esito.richiede_nulla_osta,
            stato_attuale=pratica["stato"], ruoli_avvio=RUOLI_AVVIO[tipo],
            creazione_del_sistema=del_sistema,
            richiede_autorizzazione=tipo != st.RIVERSAMENTO_USCITA or bool(
                pratica["richiede_autorizzazione_produttore"]))
        if tipo == st.SCARTO:
            esito.difetti += await _difetti_piano(session, pratica)
        else:
            esito.difetti += await _difetti_oggetti_restituzione(session, pratica)
        da_rileggere = [allegati[i] for i in
                        dom_aut.allegati_autorizzativi(tipo, cronologia, allegati)]
        esito.difetti += await _difetti_riuso(session, pratica, da_rileggere)
        in_transizione = await _allegati_in_transizione(session, pratica_id)
        # parametri ATTUALI dell'ente, letti dal lavoratore (errore → si ritenta)
        parametri = await firme.parametri_firme(pratica["tenant_id"], session=session,
                                                rigoroso=True)
    esito.parametri_firme = parametri.come_dict()
    for allegato in da_rileggere:
        difetti, riassunto, contenuto = await asyncio.to_thread(_rileggi, allegato, adesso)
        esito.difetti += difetti
        if contenuto is not None and not difetti:
            difetti_f, firma = await difetti_firma(
                allegato, contenuto, parametri, pratica_id=pratica_id,
                transizione=str(allegato["id"]) in in_transizione)
            esito.difetti += difetti_f
            riassunto.update(firma)
        if riassunto:
            esito.allegati.append(riassunto)
    return esito


async def assicura(pratica: Mapping[str, Any], adesso: datetime) -> None:
    """Prima di un passo di esecuzione dello scarto: verifica (o ritrova la
    verifica del lavoratore sullo stesso punto della cronologia). Solleva
    ``AutorizzazioniNonVerificate`` dopo aver BLOCCATO la pratica; gli errori
    temporanei (database, storage) si propagano (il lavoratore ritenta)."""
    from libs.shared.db import get_async_session

    tipo = str(pratica.get("tipo") or st.SCARTO)
    async with get_async_session() as session:
        fino_a = await _ultimo_passo(session, pratica["id"])
        if await _gia_verificata(session, pratica["id"], fino_a, tipo):
            return
    esito = await verifica(pratica["id"], adesso)
    if not esito.valida:
        await blocca(pratica, esito.difetti, adesso)
        raise AutorizzazioniNonVerificate(esito.difetti)
    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
            "dettagli) VALUES (:tipo, :p, :t, NULL, :e, CAST(:d AS jsonb))"),
            {"tipo": tipo, "p": pratica["id"], "t": pratica["tenant_id"], "e": ESITO_VERIFICATE,
             "d": json.dumps({"cronologia_fino_a": esito.cronologia_fino_a,
                              "richiede_nulla_osta": esito.richiede_nulla_osta,
                              "natura_ente": esito.natura, "allegati": esito.allegati,
                              "parametri_firme": esito.parametri_firme,
                              "impronta_piano": pratica.get("impronta_piano"),
                              "verificata_il": adesso.isoformat()}, default=str)})
    logger.info(f"{tipo.lower()}_autorizzazioni_verificate", pratica_id=str(pratica["id"]),
                cronologia_fino_a=esito.cronologia_fino_a)


async def blocca(pratica: Mapping[str, Any], difetti: list[str], adesso: datetime) -> None:
    """Pratica bloccata: nessun lavoro (nessuna cancellazione), errore con i
    difetti, voce d'audit nella stessa transazione, log di errore."""
    from libs.shared.db import get_async_session

    tipo = str(pratica.get("tipo") or st.SCARTO)
    puntuale = tipo in (st.ANNULLAMENTO, st.RETTIFICA)
    errore = {"codice": dom_aut.CODICE_BLOCCO,
              "messaggio": (f"Catena delle autorizzazioni della pratica di {tipo.lower()} non "
                            "verificata dal lavoratore: nessun atto, fatto né foglia (vedi "
                            "difetti)") if puntuale else
                           ("Catena delle autorizzazioni della restituzione non verificata "
                            "dal lavoratore: nessuna esportazione (vedi difetti)")
                           if tipo == st.RESTITUZIONE else
                           ("Catena delle autorizzazioni del riversamento non verificata dal "
                            "lavoratore: nessuna esportazione (vedi difetti)")
                           if tipo == st.RIVERSAMENTO_USCITA else
                           ("Catena delle autorizzazioni dello scarto non verificata dal "
                            "lavoratore: nessuna cancellazione (vedi difetti)"),
              "difetti": difetti[:20], "il": adesso.isoformat()}
    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        if attuale is None:
            return
        await repo.aggiorna_pratica(session, pratica["id"], adesso, lavoro=None,
                                    ultimo_errore=errore)
        await registra_audit(session,
                             evento=f"ciclo_vita.{tipo.lower()}.autorizzazioni_non_verificate",
                             tenant_id=str(attuale["tenant_id"]),
                             dettagli={"pratica_id": str(pratica["id"]),
                                       "stato": attuale["stato"], "difetti": difetti[:50]})
    logger.error(f"{tipo.lower()}_autorizzazioni_non_verificate", pratica_id=str(pratica["id"]),
                 difetti=difetti[:10])


# ── Revisione R06 (V2): annullamento e rettifica ─────────────────────────

#: Voce d'audit che l'API scrive con la richiesta (stessa transazione)
EVENTI_RICHIESTA = {st.ANNULLAMENTO: "ciclo_vita.annullamento",
                    st.RETTIFICA: "ciclo_vita.rettifica.richiesta"}


def _uuid(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


async def ruoli_ammessi_puntuale(session: AsyncSession, pratica: Mapping[str, Any]
                                 ) -> tuple[frozenset[str], list[str], Any]:
    """Ruoli che la configurazione ATTUALE dell'ente ammette per la pratica (mai
    del conservatore), i difetti di configurazione (annullamento non ammesso in
    quello stato) e, per l'annullamento, la regola (R09, D23: parametri del
    motore letti dal lavoratore; un errore del database si propaga e si ritenta)."""
    from app.application import parametri

    difetti: list[str] = []
    regola = None
    if pratica["tipo"] == st.ANNULLAMENTO:
        regola = await parametri.regola_annullamento(pratica["tenant_id"], session=session,
                                                     rigoroso=True)
        conservato = (pratica.get("dati") or {}).get("stato_precedente") == "CONSERVATO"
        ruoli = regola.ruoli_dopo if conservato else regola.ruoli_prima
        if not ruoli:
            difetti.append("ANNULLAMENTO_DOPO_CONSERVAZIONE_NON_AMMESSO" if conservato
                           else "ANNULLAMENTO_PRIMA_DELLA_CONSERVAZIONE_NON_AMMESSO")
    else:
        ruoli = frozenset(settings.rettifica_ruoli)
    return frozenset(ruoli) - frozenset(st.RUOLI_CONSERVATORE), difetti, regola


async def _difetti_approvatori(session: AsyncSession, pratica: Mapping[str, Any],
                               cronologia: list[dict[str, Any]],
                               ruoli: frozenset[str]) -> list[str]:
    """R09 (D23): chi ha approvato esiste, è attivo, è dell'ente e ha ADESSO un
    ruolo ammesso ad approvare (la cronologia dice solo il ruolo di allora)."""
    difetti: list[str] = []
    for passo in cronologia:
        if passo.get("a_stato") != st.APPROVATA or passo.get("utente_id") is None:
            continue
        utente = (await session.execute(text(
            "SELECT tenant_id, ruolo::text AS ruolo, attivo, created_at FROM public.users "
            "WHERE id = :u"), {"u": passo["utente_id"]})).mappings().first()
        if utente is None:
            difetti.append("APPROVATORE_SCONOSCIUTO: l'utente che ha approvato non esiste")
            continue
        # Revisione R09 (Z1, VD-P-90): nessun utente creato dopo la richiesta
        if utente["created_at"] is None or utente["created_at"] >= _istante(
                pratica["created_at"]):
            difetti.append("APPROVATORE_POSTERIORE: l'utente che ha approvato è stato creato "
                           "dopo la richiesta di annullamento")
        if not utente["attivo"]:
            difetti.append("APPROVATORE_NON_ATTIVO: l'utente che ha approvato è disattivato")
        if str(utente["tenant_id"]) != str(pratica["tenant_id"]):
            difetti.append("APPROVATORE_ALTRO_ENTE: l'utente che ha approvato è di un altro ente")
        if utente["ruolo"] not in ruoli:
            difetti.append(f"APPROVATORE_RUOLO_NON_AMMESSO: chi ha approvato ora è "
                           f"{utente['ruolo']}")
    return difetti


async def _difetti_richiedente(session: AsyncSession, pratica: Mapping[str, Any],
                               ruoli: frozenset[str]) -> list[str]:
    utente = (await session.execute(text(
        "SELECT u.tenant_id, u.ruolo::text AS ruolo, u.attivo, "
        "u.ruolo_personalizzato_id AS rp, r.permessi, r.ruolo_base, r.attivo AS rp_attivo "
        "FROM public.users u LEFT JOIN public.ruoli_personalizzati r "
        "ON r.id = u.ruolo_personalizzato_id AND r.tenant_id = u.tenant_id WHERE u.id = :u"),
        {"u": pratica.get("creata_da")})).mappings().first() if pratica.get("creata_da") \
        else None
    if utente is None:
        return ["RICHIEDENTE_SCONOSCIUTO: l'utente della richiesta non esiste"]
    difetti: list[str] = []
    if not utente["attivo"]:
        difetti.append("RICHIEDENTE_NON_ATTIVO: l'utente della richiesta è disattivato")
    if str(utente["tenant_id"]) != str(pratica["tenant_id"]):
        difetti.append("RICHIEDENTE_ALTRO_ENTE: l'utente della richiesta è di un altro ente")
    # con la chiave API (API_SYSTEM) l'utente è il titolare della chiave: il suo
    # ruolo non è API_SYSTEM; per gli accessi di persona il ruolo ATTUALE deve
    # essere ancora ammesso
    if pratica.get("creata_ruolo") != st.API_SYSTEM and utente["ruolo"] not in ruoli:
        difetti.append(f"RUOLO_ATTUALE_NON_AMMESSO: il richiedente ora è {utente['ruolo']}")
    if pratica.get("creata_ruolo") != st.API_SYSTEM and utente["rp"] is not None:
        difetti += await _difetti_permesso_attuale(session, pratica, utente)
    return difetti


async def _difetti_permesso_attuale(session: AsyncSession, pratica: Mapping[str, Any],
                                    utente: Mapping[str, Any]) -> list[str]:
    """R12a (VD-C-155): richiedente con un RUOLO PERSONALIZZATO: il lavoratore
    riverifica il permesso dell'azione con i permessi ATTUALI del ruolo,
    intersecati con i concedibili ATTUALI dell'ente (stessa regola dell'emissione
    del token nel gateway), fail-closed: un permesso tolto dopo la richiesta la
    respinge (stato cambiato, come il ruolo base)."""
    from app.domain.permessi import PERMESSO_ANNULLAMENTO, PERMESSO_RETTIFICA
    from libs.verumdoc_rules.parametri_servizio import RUOLI_PERMESSI_CONCEDIBILI
    from libs.verumdoc_rules.permessi import MAI_DELEGABILI, SEMPRE_INCLUSI

    permesso = PERMESSO_ANNULLAMENTO if pratica["tipo"] == st.ANNULLAMENTO else \
        PERMESSO_RETTIFICA
    concedibili = set((await parametri.valori(
        [RUOLI_PERMESSI_CONCEDIBILI], pratica["tenant_id"], session=session, rigoroso=True)
    ).get(RUOLI_PERMESSI_CONCEDIBILI) or ())
    effettivi: set[str] = set()
    if utente["rp_attivo"] and utente["ruolo_base"] == utente["ruolo"]:
        effettivi = ((set(utente["permessi"] or ()) & concedibili) | SEMPRE_INCLUSI) \
            - MAI_DELEGABILI
    if permesso in effettivi:
        return []
    return [f"PERMESSO_ATTUALE_NON_AMMESSO: il ruolo personalizzato del richiedente non "
            f"comprende più «{permesso}»"]


async def _difetti_oggetti(session: AsyncSession, pratica: Mapping[str, Any]) -> list[str]:
    """PdV e PdA della pratica: dell'ente, coerenti fra loro e con la pratica."""
    difetti: list[str] = []
    dati = pratica.get("dati") or {}
    pdv = (await session.execute(text(
        "SELECT tenant_id, stato::text AS stato, "
        "metadata_json -> 'annullamento' ->> 'pratica_id' AS annullato_da "
        "FROM public.pdv WHERE id = :p"), {"p": pratica.get("pdv_id")})).mappings().first() \
        if pratica.get("pdv_id") else None
    if pdv is None:
        return ["PDV_ASSENTE: la pratica non indica un versamento esistente"]
    if str(pdv["tenant_id"]) != str(pratica["tenant_id"]):
        difetti.append("PDV_ALTRO_ENTE: il versamento è di un altro ente")
    if pratica["tipo"] == st.ANNULLAMENTO and \
            pratica["stato"] in dom_aut.STATI_APPLICA_ANNULLAMENTO:
        # Revisione R09 (Z3): non ancora applicato; lo stato del PdV lo
        # ricontrolla il lavoratore nella transazione che lo annulla
        pass
    elif pratica["tipo"] == st.ANNULLAMENTO:
        if pdv["stato"] != "ANNULLATO" or pdv["annullato_da"] != str(pratica["id"]):
            difetti.append("PDV_NON_ANNULLATO_DALLA_PRATICA: il versamento non risulta "
                           "annullato da questa pratica")
        conservato = dati.get("stato_precedente") == "CONSERVATO"
        if conservato != bool(dati.get("aip_id")):
            difetti.append("STATO_PRECEDENTE_INCOERENTE: PdA indicato solo per un versamento "
                           "CONSERVATO")
        altra = (await session.execute(text(
            "SELECT count(*) FROM public.pratiche WHERE tipo = 'ANNULLAMENTO' "
            "AND pdv_id = :p AND id <> :id AND stato <> 'RESPINTA'"),
            {"p": pratica["pdv_id"], "id": pratica["id"]})).scalar_one()
        if altra:
            difetti.append("PRATICA_DUPLICATA: un'altra pratica di annullamento per lo stesso "
                           "versamento")
    elif pdv["stato"] != "CONSERVATO":
        difetti.append(f"PDV_NON_CONSERVATO: si rettifica solo un versamento CONSERVATO "
                       f"({pdv['stato']})")
    if dati.get("aip_id"):
        aip_id = _uuid(dati["aip_id"])
        aip = (await session.execute(text(
            "SELECT tenant_id, pdv_id, urn FROM public.aip WHERE id = :a"), {"a": aip_id})
        ).mappings().first() if aip_id else None
        if aip is None:
            difetti.append("PDA_ASSENTE: il PdA indicato non esiste")
        else:
            if str(aip["tenant_id"]) != str(pratica["tenant_id"]):
                difetti.append("PDA_ALTRO_ENTE: il PdA è di un altro ente")
            if str(aip["pdv_id"]) != str(pratica["pdv_id"]):
                difetti.append("PDA_DI_ALTRO_VERSAMENTO: il PdA non è del versamento della "
                               "pratica")
            if aip["urn"] != dati.get("urn"):
                difetti.append("PDA_URN_DIVERSO: l'URN registrato non è quello del PdA")
    elif dati.get("urn"):
        difetti.append("PDA_INCOERENTE: URN senza PdA")
    return difetti


async def _audit_della_richiesta(session: AsyncSession, pratica: Mapping[str, Any]) -> bool:
    """La voce d'audit che l'API scrive nella transazione della richiesta (stesso
    ente, utente e pratica, nell'intorno della creazione: partizioni potate)."""
    return bool((await session.execute(text(
        "SELECT EXISTS (SELECT 1 FROM public.audit_global WHERE evento = :e "
        "AND tenant_id = :t AND user_id IS NOT DISTINCT FROM :u "
        "AND dettagli ->> 'pratica_id' = :p "
        "AND created_at BETWEEN CAST(:c AS timestamptz) - interval '1 hour' "
        "AND CAST(:c AS timestamptz) + interval '1 hour')"),
        {"e": EVENTI_RICHIESTA[pratica["tipo"]], "t": pratica["tenant_id"],
         "u": pratica.get("creata_da"), "p": str(pratica["id"]),
         "c": pratica["created_at"]})).scalar_one())


#: Revisione R09 (Z3): fatto del lavoratore che ha verificato e APPLICATO un
#: annullamento (prima dell'atto); la migrazione 048 lo scrive per gli
#: annullamenti già applicati dall'API prima di R09 e ancora in coda
ESITO_ANNULLAMENTO_APPLICATO = "AUTORIZZAZIONI_VERIFICATE"


async def annullamento_applicato(session: AsyncSession, pratica_id: Any) -> bool:
    return bool((await session.execute(text(
        "SELECT EXISTS (SELECT 1 FROM public.esiti_esecuzione WHERE tipo = 'ANNULLAMENTO' "
        "AND esito = :e AND pratica_id = :p)"),
        {"e": ESITO_ANNULLAMENTO_APPLICATO, "p": pratica_id})).scalar_one())


#: Revisione R09 (W1): difetti di uno stato CAMBIATO fra la richiesta e il
#: lavoratore (persone, ruoli, parametri, PdV): prima dell'applicazione il PdV è
#: intatto e la pratica si chiude RESPINTA dal SISTEMA (si può chiedere di nuovo).
#: Ogni altro difetto (catena, audit, approvazione fabbricata o posteriore, richiesta
#: firmata mancante o non valida: l'API la controlla alla richiesta) è da
#: manomissione: pratica BLOCCATA con allarme, che l'ente o il conservatore respinge.
DIFETTI_STATO_CAMBIATO = (
    "PDV_CAMBIATO", "RICHIEDENTE_NON_ATTIVO", "RUOLO_ATTUALE_NON_AMMESSO",
    "PERMESSO_ATTUALE_NON_AMMESSO",
    "RICHIEDENTE_NON_PERSONA", "APPROVATORE_NON_ATTIVO", "APPROVATORE_RUOLO_NON_AMMESSO",
    "ANNULLAMENTO_DOPO_CONSERVAZIONE_NON_AMMESSO",
    "ANNULLAMENTO_PRIMA_DELLA_CONSERVAZIONE_NON_AMMESSO", "RUOLO_NON_AMMESSO: chiesta dal",
    "RUOLO_APPROVAZIONE_NON_AMMESSO")


async def verifica_puntuale(pratica_id: uuid.UUID) -> list[str]:
    """Difetti della catena di una pratica di annullamento o rettifica (lista
    vuota = il lavoratore può scrivere il fatto). Transazione di sola lettura.

    Revisione R09 (W2): un annullamento GIÀ applicato dal lavoratore (fatto
    ``AUTORIZZAZIONI_VERIFICATE``) si ricontrolla solo nell'integrità — catena
    della cronologia, quattro occhi nella cronologia, audit della richiesta, PdV
    annullato da questa pratica, fatto del lavoratore — e non nello stato ATTUALE
    di persone, ruoli e parametri, già verificati all'applicazione: un
    approvatore disattivato dopo non lascia il PdV annullato senza atto e foglia."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        pratica = await repo.leggi_pratica(session, pratica_id)
        if pratica is None or pratica["tipo"] not in (st.ANNULLAMENTO, st.RETTIFICA):
            return ["PRATICA_NON_PUNTUALE"]
        cronologia = await repo.cronologia(session, pratica_id)
        applica = (pratica["tipo"] == st.ANNULLAMENTO
                   and pratica["stato"] in dom_aut.STATI_APPLICA_ANNULLAMENTO)
        if pratica["tipo"] == st.ANNULLAMENTO and not applica:
            difetti: list[str] = []
            if not await annullamento_applicato(session, pratica_id):
                difetti.append("ANNULLAMENTO_NON_APPLICATO_DAL_LAVORATORE: nessun fatto del "
                               "lavoratore che abbia verificato e applicato l'annullamento")
            ruoli_storici = frozenset(
                str(p.get("ruolo")) for p in cronologia
                if p.get("da_stato") == st.RICHIESTA and p.get("a_stato") == st.APPROVATA)
            difetti += dom_aut.difetti_iter_puntuale(
                pratica["tipo"], cronologia, stato_attuale=pratica["stato"],
                creata_da=pratica.get("creata_da"), creata_ruolo=pratica.get("creata_ruolo"),
                ruoli_ammessi={str(pratica.get("creata_ruolo"))},
                ruoli_approvazione=ruoli_storici)
            difetti += await _difetti_oggetti(session, pratica)
            if not await _audit_della_richiesta(session, pratica):
                difetti.append("RICHIESTA_SENZA_AUDIT: nessuna voce d'audit della richiesta")
            return difetti
        ruoli, difetti, regola = await ruoli_ammessi_puntuale(session, pratica)
        approvazione = {"approvazione_richiesta": False, "ruoli_approvazione": frozenset()}
        if regola is not None:
            conservato = (pratica.get("dati") or {}).get("stato_precedente") == "CONSERVATO"
            # Revisione R09 (Z3): la regola ATTUALE decide PRIMA di applicare
            approvazione = {"approvazione_richiesta": regola.doppia_richiesta(
                conservato=conservato), "ruoli_approvazione": regola.ruoli_approvazione}
            difetti += await _difetti_approvatori(session, pratica, cronologia,
                                                  regola.ruoli_approvazione)
        difetti += dom_aut.difetti_iter_puntuale(
            pratica["tipo"], cronologia, stato_attuale=pratica["stato"],
            creata_da=pratica.get("creata_da"), creata_ruolo=pratica.get("creata_ruolo"),
            ruoli_ammessi=ruoli, applica=applica, **approvazione)
        difetti += await _difetti_richiedente(session, pratica, ruoli)
        difetti += await _difetti_oggetti(session, pratica)
        if not await _audit_della_richiesta(session, pratica):
            difetti.append("RICHIESTA_SENZA_AUDIT: nessuna voce d'audit della richiesta")
        richieste: list[dict[str, Any]] = []
        parametri = None
        if pratica["tipo"] == st.ANNULLAMENTO:
            parametri = await firme.parametri_firme(pratica["tenant_id"], session=session,
                                                    rigoroso=True)
            richieste = [{**a, "tenant_id": pratica["tenant_id"], "pratica_id": pratica_id}
                         for a in await repo.allegati(session, pratica_id)
                         if a["tipo"] == st.ALLEGATO_RICHIESTA_ANNULLAMENTO]
    if parametri is not None:
        difetti += await _difetti_richiesta_firmata(pratica, richieste, parametri)
    return difetti


async def _difetti_richiesta_firmata(pratica: Mapping[str, Any],
                                     richieste: list[dict[str, Any]],
                                     parametri: firme.ParametriFirme) -> list[str]:
    """R08 (D22, VD-C-141): la richiesta di annullamento firmata, se c'è o se
    l'ente la rende obbligatoria, si riverifica sui byte del WORM: firma valida
    al livello dell'ente e identificativo del versamento nel contenuto firmato."""
    if not richieste:
        return (["RICHIESTA_NON_FIRMATA: l'ente richiede la richiesta di annullamento firmata "
                 "(ANNULLAMENTO_RICHIESTA_FIRMATA = OBBLIGATORIA)"]
                if parametri.annullamento_obbligatoria else [])
    difetti: list[str] = []
    adesso = datetime.now(UTC)
    for richiesta in richieste:
        letti, _, contenuto = await asyncio.to_thread(_rileggi, richiesta, adesso)
        difetti += letti
        if contenuto is None or letti:
            continue
        difetti += (await difetti_firma(richiesta, contenuto, parametri))[0]
        if not firme.contiene_riferimento(contenuto, str(pratica["pdv_id"]),
                                          ammessi=parametri.firmatari_autorizzati):
            difetti.append("RICHIESTA_FIRMATA_ALTRO_VERSAMENTO: il documento firmato non cita "
                           "il versamento della pratica")
    return difetti


#: Difetti che dipendono solo dallo stato ATTUALE del richiedente (disattivato o
#: ruolo cambiato dopo una richiesta valida): la rettifica si respinge come prima
#: (nessun versamento per suo conto), senza allarme di sicurezza
DIFETTI_DEL_RICHIEDENTE = ("RICHIEDENTE_NON_ATTIVO", "RUOLO_ATTUALE_NON_AMMESSO",
                           "PERMESSO_ATTUALE_NON_AMMESSO")  # R12a (VD-C-155)
RESPINGI = "RESPINGI"


async def assicura_puntuale(pratica: Mapping[str, Any], adesso: datetime) -> str | None:
    """Prima dell'atto e del fatto di un annullamento o di una rettifica: verifica
    la catena; se non regge BLOCCA la pratica e solleva
    ``AutorizzazioniNonVerificate`` (nessun atto, fatto, versamento né foglia).
    ``RESPINGI``: rettifica valida il cui richiedente non è più attivo o
    autorizzato (il chiamante la conclude RESPINTA)."""
    difetti = await verifica_puntuale(pratica["id"])
    if difetti and pratica["tipo"] == st.RETTIFICA and all(
            d.startswith(DIFETTI_DEL_RICHIEDENTE) for d in difetti):
        return RESPINGI
    if difetti:
        await blocca(pratica, difetti, adesso)
        raise AutorizzazioniNonVerificate(difetti)
    logger.info("puntuale_autorizzazioni_verificate", pratica_id=str(pratica["id"]),
                tipo=pratica["tipo"])
    return None


# ── R09 (D20): chiusura con eccezioni ─────────────────────────────────────

async def verifica_chiusura(pratica_id: uuid.UUID) -> list[str]:
    """Difetti di una richiesta di «chiudi con eccezioni» (vuota = il lavoratore
    può scrivere fatti e foglie): pratica di scarto ESEGUITA_CON_ECCEZIONI con il
    lavoro CHIUDI, richiedente del conservatore ADESSO attivo, motivo sufficiente,
    voce d'audit della richiesta (stesso utente e pratica), PdA ancora
    NON_COMPLETATO. L'API esposta non può fabbricare la chiusura da sola."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        pratica = await repo.leggi_pratica(session, pratica_id)
        if pratica is None or pratica["tipo"] != st.SCARTO:
            return ["PRATICA_NON_DI_SCARTO"]
        difetti: list[str] = []
        if pratica["stato"] != st.ESEGUITA_CON_ECCEZIONI or pratica["lavoro"] != st.LAVORO_CHIUDI:
            difetti.append(f"STATO_NON_CHIUDIBILE: la pratica è {pratica['stato']} con lavoro "
                           f"{pratica['lavoro']}")
        chiusura = dict((pratica.get("dati") or {}).get("chiusura") or {})
        if len(str(chiusura.get("motivo") or "").strip()) < st.MOTIVO_MINIMO:
            difetti.append("MOTIVO_INSUFFICIENTE: la chiusura con eccezioni chiede un motivo")
        utente_id = _uuid(chiusura.get("richiesta_da"))
        utente = (await session.execute(text(
            "SELECT ruolo::text AS ruolo, attivo FROM public.users WHERE id = :u"),
            {"u": utente_id})).mappings().first() if utente_id else None
        if utente is None:
            difetti.append("RICHIEDENTE_SCONOSCIUTO: la chiusura non indica un utente esistente")
        else:
            if not utente["attivo"]:
                difetti.append("RICHIEDENTE_NON_ATTIVO: chi ha chiesto la chiusura è disattivato")
            if utente["ruolo"] not in st.RUOLI_CONSERVATORE:
                difetti.append(f"RUOLO_NON_AMMESSO: la chiusura è del conservatore, chi l'ha "
                               f"chiesta ora è {utente['ruolo']}")
        audit = bool((await session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM public.audit_global "
            "WHERE evento = 'ciclo_vita.esecuzione_richiesta' "
            "AND tenant_id = :t AND user_id IS NOT DISTINCT FROM :u "
            "AND dettagli ->> 'pratica_id' = :p AND dettagli ->> 'lavoro' = :l "
            "AND created_at BETWEEN CAST(:c AS timestamptz) - interval '1 hour' "
            "AND CAST(:c AS timestamptz) + interval '1 hour')"),
            {"t": pratica["tenant_id"], "u": utente_id, "p": str(pratica_id),
             "l": st.LAVORO_CHIUDI,
             "c": _istante(chiusura.get("richiesta_il"))})).scalar_one())
        if not audit:
            difetti.append("RICHIESTA_SENZA_AUDIT: nessuna voce d'audit della richiesta di "
                           "chiusura")
        incompleti = int((await session.execute(text(
            "SELECT count(*) FROM public.pratiche_oggetti WHERE pratica_id = :p "
            "AND esito = 'NON_COMPLETATO'"), {"p": pratica_id})).scalar_one())
        if not incompleti:
            difetti.append("NESSUN_PDA_INCOMPLETO: nessun PdA da chiudere con eccezioni")
    return difetti


def _istante(valore: Any) -> datetime:
    try:
        istante = datetime.fromisoformat(str(valore))
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=UTC)
    return istante if istante.tzinfo else istante.replace(tzinfo=UTC)


async def assicura_chiusura(pratica: Mapping[str, Any], adesso: datetime) -> None:
    """Prima di fatti e foglie della chiusura: difetti → pratica BLOCCATA e
    ``AutorizzazioniNonVerificate`` (nessun fatto, nessuna foglia)."""
    difetti = await verifica_chiusura(pratica["id"])
    if difetti:
        await blocca(pratica, difetti, adesso)
        raise AutorizzazioniNonVerificate(difetti)


__all__ = ["AutorizzazioniNonVerificate", "DIFETTI_STATO_CAMBIATO", "ESITO_VERIFICATE",
           "EVENTI_RICHIESTA", "RESPINGI",
           "Verifica",
           "assicura", "assicura_chiusura", "assicura_puntuale", "blocca",
           "ruoli_ammessi_puntuale", "verifica", "verifica_chiusura",
           "verifica_puntuale"]
