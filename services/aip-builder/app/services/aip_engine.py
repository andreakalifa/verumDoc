# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Costruzione del Pacchetto di Archiviazione (PdA/AIP) da un PdV validato.

Servizio: aip-builder. Riferimenti: VD-C-008, VD-C-009, VD-C-012 (R08/R09);
REGOLE_SVILUPPO §10.3 (impronte calcolate sui byte, mai fidate), §11.2,
§12.2 (consumer idempotente, offset dopo l'effetto), §12.4 (ritentativi e DLQ).

Flusso per ogni ``pdv.validated``:
  1. transazione con lock del PdV: se il PdA esiste già (riconsegna Kafka) non
     si fa nulla; altrimenti PdV VALIDATO → IN_BUILD;
  2. ogni file si scarica dallo storage del versamento; se un file non si
     scarica il PdA NON si crea (errore transitorio → ritentativi → DLQ e PdV
     ERRORE); l'impronta si RICALCOLA sui byte con l'algoritmo dichiarato e si
     confronta con la dichiarata: se diverge il PdA non si crea (errore
     permanente di integrità); in IPdA, DB e catena vanno le impronte calcolate;
  3. IPdA SInCRO, manifest e albero del PdA (``catena_pda.json``) su storage,
     poi in UNA transazione INSERT di AIP (stato SU_STORAGE), documenti,
     metadati Allegato 5 (``metadati_allegato5``), riga della coda
     d'indicizzazione (``indicizzazione_aip``, R03 b4, VD-C-093), albero di
     livello 1 della catena (``catena_pda``: foglie e radice, contratto K1 di
     R04), riga della coda del registro (``catena_coda``) e (R13a, VD-C-068)
     riga dell'IPdA da sigillare (``firme_documenti``: bucket, chiave, VERSIONE e
     impronta dell'oggetto scritto; la sigilla la hashchain).
     NESSUN evento «conservato» parte da qui (VD-C-097): il PdA è solo sullo
     storage. Il sequenziatore della hashchain legge ``catena_coda``, aggiunge
     il PdA al registro globale e, quando un checkpoint lo copre, emette il RdV
     definitivo, porta PdA e PdV a CONSERVATO e pubblica ``aip.conserved``.
L'identificativo del PdA deriva dal PdV (uuid5); URN e istante di costruzione
si fissano al primo tentativo in ``pdv.metadata_json['costruzione_pda']``.
Così un nuovo tentativo produce GLI STESSI oggetti sulle stesse chiavi: lo
storage WORM (s3_uploader, Object Lock COMPLIANCE, nessuna sovrascrittura)
risponde «già presente» e l'oggetto si accetta solo se il contenuto è identico.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import text

from app.config import settings
from app.services.file_pda import includi_file_nel_pda
from app.services.s3_uploader import (
    OggettoGiaPresente,
    PoliticaConservazione,
    RetentionOggetto,
    calcola_retention,
    compute_aip_hash,
    oggetto_identico,
    upload_aip_to_s3,
)
from app.services.sincro_builder import (
    FORMATO_ATTUALE,
    MetadatiIPdANonValidi,
    build_sincro_xml,
    formato_della_costruzione,
    verifica_metadati_ipda,
)
from libs.catena.canonico import json_canonico
from libs.catena.foglie import AlberoPda, ComponentePda, albero_pda
from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_ente
from libs.shared.impronte import ALGORITMI_IMPRONTA, funzione_impronta  # noqa: F401
from libs.shared.kafka.connessione import parametri_connessione
from libs.shared.kafka.consumatore_affidabile import (
    ErrorePermanente,
    EsitoElaborazione,
    ciclo_consumer,
)

logger = structlog.get_logger(__name__)

_consumer: AIOKafkaConsumer | None = None
_producer: AIOKafkaProducer | None = None

# Spazio dei nomi per l'identificativo deterministico del PdA (uuid5 del PdV)
NAMESPACE_PDA = uuid.UUID("6f1c2a52-9a3e-5b7e-8d0b-2c4f6a8e0d11")

# Algoritmi d'impronta ammessi: tabella unica in libs/shared/impronte.py
# (ALGORITMI_IMPRONTA resta importabile da qui).

STATI_PDV_DA_COSTRUIRE = ("VALIDATO", "IN_BUILD")
STATI_AIP_COSTRUITO = ("SU_STORAGE", "IN_CATENA", "CONSERVATO")


def id_pda_per_pdv(pdv_id: str) -> str:
    """Identificativo stabile del PdA di un PdV (idempotenza dei ritentativi)."""
    return str(uuid.uuid5(NAMESPACE_PDA, f"pda:{pdv_id}"))


def nome_sicuro(nome: str) -> str:
    """Nome file utilizzabile in una chiave di storage (niente '/', '..', controlli)."""
    pulito = re.sub(r"[^A-Za-z0-9._-]", "_", nome).strip("._") or "file"
    return pulito[:150]


def verifica_impronta(contenuto: bytes, algoritmo: str, impronta_dichiarata: str) -> str:
    """
    Ricalcola l'impronta con l'algoritmo dichiarato e la confronta.

    Restituisce l'impronta calcolata; solleva ErrorePermanente se l'algoritmo
    non è ammesso o se l'impronta non corrisponde.
    """
    funzione = funzione_impronta(algoritmo)
    if funzione is None:
        raise ErrorePermanente(
            "AIP-INTEGRITA-001", f"Algoritmo d'impronta non ammesso: {algoritmo!r}")
    calcolata = funzione(contenuto).hexdigest()
    if calcolata.lower() != (impronta_dichiarata or "").lower():
        raise ErrorePermanente(
            "AIP-INTEGRITA-002",
            "L'impronta ricalcolata sul file non corrisponde a quella dichiarata",
            {"algoritmo": algoritmo, "dichiarata": impronta_dichiarata, "calcolata": calcolata},
        )
    return calcolata


async def init_aip_consumer() -> None:
    global _consumer, _producer
    _consumer = AIOKafkaConsumer(
        settings.kafka_topic_in,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=_deserializza,
        **parametri_connessione(),
    )
    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        acks="all",
        **parametri_connessione(),
    )
    await _consumer.start()
    await _producer.start()
    logger.info("aip_consumer_started", topic=settings.kafka_topic_in)


def _deserializza(valore: bytes) -> Any:
    try:
        return json.loads(valore.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


async def close_aip_consumer() -> None:
    global _consumer, _producer
    for c in (_consumer, _producer):
        if c:
            try:
                await c.stop()
            except Exception as exc:  # noqa: BLE001 — chiusura: si registra e si prosegue
                logger.warning("aip_consumer_stop_errore", errore=str(exc)[:200])
    _consumer = _producer = None


async def _pubblica(topic: str, chiave: str, valore: dict) -> None:
    if _producer is None:
        raise RuntimeError("Producer Kafka non inizializzato")
    from libs.shared.observability.tracciamento import argomenti_kafka

    await _producer.send_and_wait(topic, key=chiave, value=valore, **argomenti_kafka())


async def gestisci_messaggio(msg: Any) -> None:
    """Gestore del ciclo affidabile: un messaggio ``pdv.validated``."""
    evento = msg.value
    if not isinstance(evento, dict):
        raise ErrorePermanente("AIP-EVENTO-001", "Messaggio non JSON o non oggetto")
    await costruisci_pda(evento)


async def registra_fallimento(msg: Any, esito: EsitoElaborazione) -> None:
    """
    Esito negativo definitivo: DLQ con motivo strutturato, PdV in ERRORE con il
    motivo in ``errori_validazione``, evento ``aip.error`` per le notifiche.
    Solleva se non riesce a registrare: l'offset allora NON si conferma.
    """
    evento = msg.value if isinstance(msg.value, dict) else {}
    pdv_id = str(evento.get("pdv_id", ""))
    tenant_id = str(evento.get("tenant_id", ""))
    motivo = {"fase": "costruzione_pda", **esito.motivo()}
    await _pubblica(f"{settings.kafka_topic_in}{settings.suffisso_dlq}", pdv_id or "senza-chiave", {
        "topic_origine": msg.topic, "partizione": msg.partition, "offset": msg.offset,
        "evento": evento, "motivo": motivo,
        "registrato_il": datetime.now(UTC).isoformat(),
    })
    await segna_pdv_in_errore(pdv_id, tenant_id, motivo)
    await _pubblica(settings.kafka_topic_error, pdv_id or "senza-chiave", {
        "pdv_id": pdv_id, "tenant_id": tenant_id, "fase": "aip_build",
        "errore": motivo["messaggio"], "codice": motivo["codice"],
    })


async def segna_pdv_in_errore(pdv_id: str, tenant_id: str, motivo: dict) -> bool:
    """PdV (VALIDATO o IN_BUILD) → ERRORE con il motivo. False se non applicabile.

    R03 i3 (richiesta 3 di f1): nella STESSA transazione la chiave UD e
    l'impronta del versamento si liberano (``versamento_impronte.attivo =
    false``), come fa validation per i PdV rifiutati: un errore permanente di
    costruzione non blocca il riversamento corretto della stessa UD (VRS-124)."""
    try:
        pdv_uuid, tenant_uuid = uuid.UUID(pdv_id), uuid.UUID(tenant_id)
    except ValueError:
        logger.error("pdv_errore_id_non_validi", pdv_id=pdv_id, tenant_id=tenant_id)
        return False
    with ambito_ente(tenant_uuid):  # R05 (VD-C-080): ente dell'evento
        return await _segna_pdv_in_errore(pdv_uuid, tenant_uuid, motivo)


async def _segna_pdv_in_errore(pdv_uuid: uuid.UUID, tenant_uuid: uuid.UUID,
                               motivo: dict) -> bool:
    async with get_async_session() as session:
        risultato = await session.execute(
            text(
                "UPDATE public.pdv SET stato = 'ERRORE', data_aggiornamento = now(), "
                "errori_validazione = COALESCE(errori_validazione, '[]'::jsonb) "
                "  || jsonb_build_array(CAST(:motivo AS jsonb)) "
                "WHERE id = :id AND tenant_id = :tenant AND stato IN ('VALIDATO', 'IN_BUILD')"
            ),
            {"motivo": json.dumps(motivo, default=str), "id": pdv_uuid, "tenant": tenant_uuid},
        )
        if risultato.rowcount == 1:
            await session.execute(
                text("UPDATE public.versamento_impronte SET attivo = false WHERE pdv_id = :id"),
                {"id": pdv_uuid},
            )
    return risultato.rowcount == 1


async def consume_loop() -> None:
    if not _consumer or not _producer:
        raise RuntimeError("AIP consumer non inizializzato")
    await ciclo_consumer(
        _consumer,
        gestore=gestisci_messaggio,
        su_fallimento=registra_fallimento,
        ritardi=list(settings.ritardi_ritentativi_secondi),
    )


async def _generate_urn_agid(
    session, tenant_id: str, classe: str, now: datetime
) -> str:
    """
    Genera l'URN del PdA: urn:IT:{CF_CONSERVATORE}:{Tipologia}:{Anno}:{Progressivo}
    Esempio: urn:IT:12345678901:FATTURA:2026:0000001234

    Usa una sequence PostgreSQL per garantire l'univocità del progressivo
    (buchi ammessi: un tentativo fallito consuma un numero, §12.10).
    """
    result = await session.execute(text("SELECT nextval('public.urn_progressivo')"))
    progressivo = result.scalar_one()
    cf_conservatore = getattr(settings, "conservatore_cf", "00000000000")
    tipologia = classe.upper().replace(" ", "_")[:20]
    return f"urn:IT:{cf_conservatore}:{tipologia}:{now.year}:{progressivo:010d}"


async def _carica_idempotente(
    *, aip_id: str, tenant_id: str, contenuto: bytes, suffisso: str, content_type: str,
    retention: RetentionOggetto,
) -> tuple[str, str, str | None]:
    """
    Scrittura WORM ripetibile: se l'oggetto esiste già (tentativo precedente)
    lo si accetta solo se il contenuto è identico, altrimenti errore permanente.
    """
    try:
        return await upload_aip_to_s3(
            aip_id=aip_id, tenant_id=tenant_id, content=contenuto,
            key_suffix=suffisso, content_type=content_type, retention=retention,
        )
    except OggettoGiaPresente as exc:
        bucket, chiave = exc.dettagli.get("bucket"), exc.dettagli.get("chiave")
        if not await oggetto_identico(str(bucket), str(chiave),
                                      hashlib.sha256(contenuto).hexdigest()):
            raise ErrorePermanente(
                "AIP-STORAGE-001",
                "Oggetto già presente nello storage conservativo con contenuto diverso",
                {"chiave": chiave},
            ) from exc
        logger.info("oggetto_pda_gia_presente_identico", chiave=chiave)
        return str(chiave), str(bucket), None


async def _pda_esistente(session, aip_id: str) -> dict | None:
    riga = (await session.execute(
        text(
            # aip → albero del PdA (assente per i PdA costruiti prima di R04)
            "SELECT a.id::text AS id, a.stato::text AS stato, a.urn, a.hash_sha256, "
            "a.s3_key, a.s3_bucket, c.radice AS radice_pda "
            "FROM public.aip a LEFT JOIN public.catena_pda c ON c.aip_id = a.id "
            "WHERE a.id = :id"
        ),
        {"id": uuid.UUID(aip_id)},
    )).mappings().first()
    return dict(riga) if riga else None


def _esito_costruzione(aip: dict, pdv_id: str, tenant_id: str, classe: str,
                       num_files: int, radice_pda: str | None) -> dict:
    """Esito restituito da ``costruisci_pda`` (non è un evento: VD-C-097)."""
    return {
        "aip_id": aip["id"], "pdv_id": pdv_id, "tenant_id": tenant_id,
        "urn": aip["urn"], "hash_sha256": aip["hash_sha256"],
        "s3_key": aip["s3_key"], "s3_bucket": aip["s3_bucket"],
        "classe_documentale": classe, "num_files": num_files,
        "stato": aip.get("stato", "SU_STORAGE"), "radice_pda": radice_pda,
    }


# ── Rapporto di verifica delle firme (R08, D01) ─────────────────────────

NOME_RAPPORTO_FIRME = "rapporto_verifica_firme.json"


def rapporto_verifica_firme(verifica: Any) -> dict | None:
    """Rapporto del passo 11 della verifica (``verifica_firme``) salvato sul PdV;
    None se il livello era NESSUNA o il PdV è stato verificato prima di R08."""
    if isinstance(verifica, str):
        verifica = json.loads(verifica)
    if not isinstance(verifica, dict):
        return None
    for passo in verifica.get("passi") or ():
        if isinstance(passo, dict) and passo.get("nome") == "verifica_firme":
            rapporto = (passo.get("dettagli") or {}).get("rapporto")
            return rapporto if isinstance(rapporto, dict) else None
    return None


# ── Albero di livello 1 della catena (contratto K1 di R04) ───────────────

def albero_del_pda(urn: str, documenti: list[dict], allegato5_map: dict,
                   ipda_sha256: str) -> AlberoPda:
    """Albero del PdA: una foglia per file (ordine nell'IPdA, poi id) con
    l'impronta SHA-256 CALCOLATA sui byte e l'impronta dei metadati del
    documento (quelli usati per l'IPdA), più la foglia dell'IPdA. Unica
    implementazione in ``libs.catena.foglie`` (REGOLE §9.4)."""
    return albero_pda(urn, [
        ComponentePda(doc=str(d["id"]), file=str(d["nome_file"]), sha256=d["hash_sha256"],
                      metadati=allegato5_map.get(d["id"]), ordine=int(d["ordine"]))
        for d in documenti
    ], ipda_sha256)


def contenuto_albero_worm(urn: str, albero: AlberoPda) -> bytes:
    """``catena_pda.json`` nel WORM (deterministico): il registro si ricostruisce
    dallo storage anche senza il database (piano §5)."""
    return json_canonico({"v": 1, "pda": urn, "radice": albero.radice_hex,
                          "foglie": list(albero.foglie)})


async def _scrivi_albero_e_coda(session: Any, *, aip_id: str, tenant_id: str, urn: str,
                                albero: AlberoPda) -> None:
    """INSERT di ``catena_pda`` e ``catena_coda`` nella transazione del PdA."""
    await session.execute(text(
        "INSERT INTO public.catena_pda (aip_id, tenant_id, urn, radice, num_foglie, foglie) "
        "VALUES (:aip, :tenant, :urn, :radice, :num, CAST(:foglie AS jsonb))"
    ), {"aip": uuid.UUID(aip_id), "tenant": uuid.UUID(tenant_id), "urn": urn,
        "radice": albero.radice_hex, "num": len(albero.foglie),
        "foglie": json.dumps(list(albero.foglie), ensure_ascii=False)})
    await session.execute(text(
        "INSERT INTO public.catena_coda (aip_id) VALUES (:aip) ON CONFLICT (aip_id) DO NOTHING"
    ), {"aip": uuid.UUID(aip_id)})


# ── Metadati Allegato 5 per la ricerca (R03 b4, VD-C-093) ──────────────
#
# ``metadati_allegato5`` è la tabella interrogata dalla ricerca del portale e
# dall'indicizzazione: prima nessuno la scriveva. Si popola qui, nella STESSA
# transazione dell'INSERT di PdA e documenti, dai metadati versati (indice →
# evento ``allegato5``). Una riga per documento che ha i metadati minimi
# dell'Allegato 5 (sul principale li impone la regola RGL-101); un allegato
# senza metadati propri non ha riga (nessun valore inventato o ereditato).
# I metadati senza colonna propria (profilo della classe, chiavi non previste,
# ``metadati_extra``) vanno in ``metadati_extra``. È una tabella d'indice: la
# fonte conservata resta l'IPdA; un testo oltre la lunghezza della colonna si
# tronca qui (e lo si scrive nel log), mai nell'IPdA.

#: Metadati minimi senza i quali la riga non si scrive (colonne NOT NULL).
CAMPI_MINIMI_ALLEGATO5 = (
    "identificativo_documento", "modalita_formazione", "tipologia_documentale",
    "data_documento", "soggetto_produttore_id", "soggetto_produttore_nome", "oggetto",
)
#: Colonna testuale → lunghezza massima (modello MetadatiAllegato5).
_LUNGHEZZE_ALLEGATO5 = {
    "identificativo_documento": 255, "tipologia_documentale": 100,
    "soggetto_produttore_id": 255, "soggetto_produttore_nome": 500, "oggetto": 1000,
    "lingua": 10, "classificazione": 500, "fascicolo_id": 255, "formato": 100,
    "versione_documento": 20, "tipo_firma": 100, "numero_registrazione": 100,
    "numero_progressivo": 50, "codice_ipa": 50, "aoo": 100, "codice_ruo": 50,
}
_COLONNE_ALLEGATO5 = frozenset({*_LUNGHEZZE_ALLEGATO5, "modalita_formazione", "data_documento",
                                "data_registrazione", "soggetto_destinatario",
                                "numero_allegati", "metadati_extra"})
_MODALITA_AMMESSE = frozenset({"a", "b", "c", "d"})


def _data_iso(valore: Any) -> str | None:
    if valore in (None, ""):
        return None
    try:
        return date.fromisoformat(str(valore)[:10]).isoformat()
    except ValueError:
        return None


def riga_metadati_allegato5(documento: dict, parametri: dict | None,
                            numero_allegati: int) -> dict | None:
    """
    Riga di ``metadati_allegato5`` per un documento del PdA, o None se mancano
    i metadati minimi (o non sono interpretabili). Funzione pura.
    """
    if not isinstance(parametri, dict):
        return None
    if any(parametri.get(c) in (None, "") for c in CAMPI_MINIMI_ALLEGATO5):
        return None
    modalita = str(parametri.get("modalita_formazione")).strip().lower()
    data_documento = _data_iso(parametri.get("data_documento"))
    if modalita not in _MODALITA_AMMESSE or data_documento is None:
        return None
    riga: dict[str, Any] = {
        "modalita_formazione": modalita, "data_documento": data_documento,
        "data_registrazione": _data_iso(parametri.get("data_registrazione")),
        "lingua": parametri.get("lingua") or "it",
        "versione_documento": parametri.get("versione_documento") or "1.0",
        "formato": documento.get("formato") or "application/octet-stream",
        "numero_allegati": int(numero_allegati),
    }
    for colonna in _LUNGHEZZE_ALLEGATO5:
        if colonna in riga or colonna not in parametri:
            continue
        riga[colonna] = parametri.get(colonna)
    for colonna, massimo in _LUNGHEZZE_ALLEGATO5.items():
        valore = riga.get(colonna)
        if valore is None:
            continue
        testo = str(valore)
        if len(testo) > massimo:
            logger.warning("metadato_troncato_nell_indice", colonna=colonna,
                           lunghezza=len(testo), massimo=massimo)
            testo = testo[:massimo]
        riga[colonna] = testo
    destinatari = parametri.get("soggetto_destinatario") or []
    riga["soggetto_destinatario"] = [str(d) for d in destinatari] \
        if isinstance(destinatari, list) else [str(destinatari)]
    extra = dict(parametri.get("metadati_extra") or {}) \
        if isinstance(parametri.get("metadati_extra"), dict) else {}
    for nome, valore in parametri.items():
        if nome not in _COLONNE_ALLEGATO5 and nome not in CAMPI_MINIMI_ALLEGATO5 \
                and valore is not None:
            extra.setdefault(nome, valore)
    riga["metadati_extra"] = extra
    return riga


async def _scrivi_metadati_e_coda_indicizzazione(
    session: Any, *, aip_id: str, tenant_id: str, documenti: list[dict],
    id_righe: dict[str, uuid.UUID], allegato5_map: dict, docs_indice: list[dict],
) -> int:
    """INSERT di ``metadati_allegato5`` e della riga della coda d'indicizzazione
    (``indicizzazione_aip``), nella transazione del PdA. Restituisce le righe
    di metadati scritte."""
    allegati_del_principale = sum(1 for d in documenti if d.get("tipo") != "PRINCIPALE")
    allegati_per_doc = {str(d.get("id")): len(d.get("allegati") or []) for d in docs_indice}
    scritte = 0
    for d in documenti:
        numero = (allegati_del_principale if d.get("tipo") == "PRINCIPALE"
                  else allegati_per_doc.get(str(d["id"]), 0))
        riga = riga_metadati_allegato5(d, allegato5_map.get(d["id"]), numero)
        if riga is None:
            logger.info("documento_senza_metadati_allegato5", aip_id=aip_id, doc=d["id"])
            continue
        await session.execute(text(
            "INSERT INTO public.metadati_allegato5 "
            "(documento_id, identificativo_documento, modalita_formazione, "
            " tipologia_documentale, data_documento, soggetto_produttore_id, "
            " soggetto_produttore_nome, soggetto_destinatario, oggetto, lingua, "
            " classificazione, fascicolo_id, formato, versione_documento, numero_allegati, "
            " tipo_firma, numero_registrazione, data_registrazione, numero_progressivo, "
            " codice_ipa, aoo, codice_ruo, metadati_extra) "
            "VALUES (:documento_id, :identificativo_documento, "
            " CAST(:modalita_formazione AS public.modalita_formazione), "
            " :tipologia_documentale, :data_documento, :soggetto_produttore_id, "
            " :soggetto_produttore_nome, CAST(:soggetto_destinatario AS jsonb), :oggetto, "
            " :lingua, :classificazione, :fascicolo_id, :formato, :versione_documento, "
            " :numero_allegati, :tipo_firma, :numero_registrazione, "
            " :data_registrazione, :numero_progressivo, :codice_ipa, :aoo, "
            " :codice_ruo, CAST(:metadati_extra AS jsonb))"
        ), {
            **dict.fromkeys(("classificazione", "fascicolo_id", "tipo_firma",
                             "numero_registrazione", "numero_progressivo", "codice_ipa",
                             "aoo", "codice_ruo")),
            **riga,
            "documento_id": id_righe[d["id"]],
            "data_documento": date.fromisoformat(riga["data_documento"]),
            "data_registrazione": (date.fromisoformat(riga["data_registrazione"])
                                   if riga["data_registrazione"] else None),
            "soggetto_destinatario": json.dumps(riga["soggetto_destinatario"]),
            "metadati_extra": json.dumps(riga["metadati_extra"], default=str),
        })
        scritte += 1
    # «Evento d'indicizzazione»: riga in coda nella stessa transazione del PdA
    # (consumata a lotti da distribution, app/services/indicizzazione.py).
    await session.execute(text(
        "INSERT INTO public.indicizzazione_aip (aip_id, tenant_id) VALUES (:aip, :tenant) "
        "ON CONFLICT (aip_id) DO NOTHING"
    ), {"aip": uuid.UUID(aip_id), "tenant": uuid.UUID(tenant_id)})
    return scritte


NOME_IPDA = "IndiceAIP_SInCRO.xml"


async def accoda_firma_ipda(session: Any, *, aip_id: str, tenant_id: str, bucket: str,
                            chiave: str, versione: str | None, impronta_sha256: str) -> None:
    """R13a (VD-C-068): IPdA da sigillare con il sigillo qualificato del conservatore,
    nella STESSA transazione del PdA (un PdA senza la sua riga è un difetto: con
    FIRMA_SENZA_FORNITORE=BLOCCA non va a CONSERVATO). Idempotente."""
    await session.execute(text(
        "INSERT INTO public.firme_documenti (tipo, tenant_id, aip_id, nome, origine_bucket, "
        "origine_chiave, origine_version_id, impronta_sha256) VALUES ('IPDA', :t, :a, :n, :b, "
        ":c, :v, :h) ON CONFLICT (tipo, aip_id) WHERE aip_id IS NOT NULL DO NOTHING"),
        {"t": uuid.UUID(tenant_id), "a": uuid.UUID(aip_id), "n": NOME_IPDA, "b": bucket,
         "c": chiave, "v": versione, "h": impronta_sha256})


async def costruisci_pda(evento: dict, *, adesso: datetime | None = None) -> dict:
    """
    Costruisce il PdA di un PdV validato. Idempotente rispetto alle riconsegne.
    Restituisce l'esito (id, URN, impronta del manifest, radice del PdA); non
    pubblica eventi: il PdA entra nella coda del registro (VD-C-097).

    R05 (VD-C-080): tutte le transazioni nell'ambito dell'ente dell'evento (RLS).
    """
    try:
        ente = uuid.UUID(str(evento.get("tenant_id")))
    except ValueError as exc:
        raise ErrorePermanente("AIP-EVENTO-002", "pdv_id o tenant_id non validi") from exc
    with ambito_ente(ente):
        return await _costruisci_pda(evento, adesso=adesso)


async def _costruisci_pda(evento: dict, *, adesso: datetime | None) -> dict:
    istante_tentativo = adesso or datetime.now(UTC)
    try:
        pdv_id = str(uuid.UUID(str(evento.get("pdv_id"))))
        tenant_id = str(uuid.UUID(str(evento.get("tenant_id"))))
    except ValueError as exc:
        raise ErrorePermanente("AIP-EVENTO-002", "pdv_id o tenant_id non validi") from exc

    indice = evento.get("indice") or {}
    classe = indice.get("classe_documentale") or evento.get("classe_documentale") or ""
    chiave = indice.get("chiave")
    docs_raw: list[dict] = list(indice.get("documenti") or [])
    if not docs_raw:
        raise ErrorePermanente("AIP-EVENTO-003", "Indice senza documenti")
    id_principale_raw = next((d.get("id") for d in docs_raw if d.get("tipo") == "PRINCIPALE"),
                             docs_raw[0].get("id"))
    aip_id = id_pda_per_pdv(pdv_id)

    # 1. Lock del PdV, controllo idempotenza, VALIDATO → IN_BUILD
    async with get_async_session() as session:
        pdv = (await session.execute(
            text(
                "SELECT p.stato, c.anni_conservazione, c.conservazione_permanente, "
                "c.tipologia_xml::text AS tipologia_xml, "
                "p.metadata_json -> 'costruzione_pda' AS costruzione, "
                "p.metadata_json -> 'verifica' AS verifica "
                "FROM public.pdv p JOIN public.classi_documentali c ON c.id = p.classe_doc_id "
                "WHERE p.id = :id AND p.tenant_id = :tenant FOR UPDATE OF p"
            ),
            {"id": uuid.UUID(pdv_id), "tenant": uuid.UUID(tenant_id)},
        )).mappings().first()
        if pdv is None:
            raise ErrorePermanente("AIP-PDV-001", "PdV inesistente per il cliente indicato")
        esistente = await _pda_esistente(session, aip_id)
        if esistente and esistente["stato"] in STATI_AIP_COSTRUITO:
            # Riconsegna: PdA, albero e coda del registro sono nati insieme
            # (una transazione): non c'è nulla da rifare né da pubblicare.
            logger.info("pda_gia_costruito_riconsegna_ignorata", aip_id=aip_id, pdv_id=pdv_id)
            return _esito_costruzione(esistente, pdv_id, tenant_id, classe, len(docs_raw),
                                      esistente.get("radice_pda"))
        if pdv["stato"] not in STATI_PDV_DA_COSTRUIRE:
            raise ErrorePermanente(
                "AIP-PDV-002", f"PdV in stato {pdv['stato']}: costruzione del PdA non ammessa",
            )
        # Metadati dell'IPdA controllati, con la tipologia della CLASSE
        # (``classi_documentali.tipologia_xml``), PRIMA di scrivere byte sullo
        # storage WORM: un errore dopo lascerebbe oggetti sotto retention senza PdA.
        tipo_xml = pdv["tipologia_xml"]
        try:
            verifica_metadati_ipda(
                classe, (evento.get("allegato5") or {}).get(id_principale_raw), tipo_xml,
            )
        except MetadatiIPdANonValidi as exc:
            raise ErrorePermanente("AIP-METADATI-001", str(exc)) from exc
        costruzione = pdv["costruzione"]
        if isinstance(costruzione, str):
            costruzione = json.loads(costruzione)
        if not costruzione:
            # Primo tentativo: URN e istante fissati una volta (§12.10) e riusati
            costruzione = {
                "urn": await _generate_urn_agid(session, tenant_id, classe, istante_tentativo),
                "avviata_il": istante_tentativo.isoformat(),
                "aip_id": aip_id,
                # revisione R14 (2): formato dell'IPdA fissato al primo tentativo
                "formato_ipda": FORMATO_ATTUALE,
            }
        await session.execute(
            text("UPDATE public.pdv SET stato = 'IN_BUILD', data_aggiornamento = now(), "
                 "metadata_json = COALESCE(metadata_json, '{}'::jsonb) "
                 "  || jsonb_build_object('costruzione_pda', CAST(:costruzione AS jsonb)) "
                 "WHERE id = :id"),
            {"id": uuid.UUID(pdv_id), "costruzione": json.dumps(costruzione)},
        )
        permanente = bool(pdv["conservazione_permanente"])
        anni_classe = pdv["anni_conservazione"]
        rapporto_firme = rapporto_verifica_firme(pdv["verifica"])

    now = datetime.fromisoformat(costruzione["avviata_il"])
    urn = str(costruzione["urn"])
    # revisione R14 (2): una costruzione iniziata prima di R14 (senza formato) ricostruisce
    # l'IPdA con il formato di allora: stessi byte di quello già nel WORM
    formato_ipda = formato_della_costruzione(costruzione)
    # Retention WORM dalla classe documentale (s3_uploader, VD-C-011): nessun
    # valore inventato; per la conservazione permanente l'orizzonte è il parametro
    # RETENTION_PERMANENTE_ANNI del motore (R09, D04: 30 anni con rinnovo automatico)
    from libs.shared.parametri import orizzonte_permanente_anni

    retention = calcola_retention(
        PoliticaConservazione(anni_conservazione=anni_classe, permanente=permanente),
        now,
        orizzonte_permanente_anni=(await orizzonte_permanente_anni(
            settings.retention_permanente_orizzonte_anni)) if permanente else None,
    )
    anni_conservazione = 999 if permanente else int(anni_classe)

    # 2. Impronte ricalcolate leggendo i file a blocchi, confronto con le
    # dichiarate, copia server-side nello storage del PdA (R03 b5: nessun file
    # intero in memoria; app/services/file_pda.py). Errori di lettura o di
    # storage: transitori (ritentativi, poi DLQ); integrità: permanenti.
    s3_map = {d.get("doc_id"): d for d in (evento.get("docs_s3_keys") or [])}
    documenti: list[dict] = []
    for ordine, doc in enumerate(docs_raw):
        doc_id = str(doc.get("id", ""))
        nome = str(doc.get("nome_file", ""))
        documenti.append(await includi_file_nel_pda(
            aip_id=aip_id, tenant_id=tenant_id, ordine=ordine, doc=doc,
            info=s3_map.get(doc_id) or {},
            suffisso=f"files/{ordine:04d}-{nome_sicuro(nome)}", retention=retention,
        ))

    allegato5_map: dict = evento.get("allegato5") or {}
    id_principale = next((d["id"] for d in documenti if d["tipo"] == "PRINCIPALE"),
                         documenti[0]["id"])
    try:
        sincro_xml = build_sincro_xml(
            aip_id=aip_id, pdv_id=pdv_id, tenant_id=tenant_id, classe_documentale=classe,
            documenti=documenti, chiave=chiave, data_versamento=now,
            allegato5=allegato5_map.get(id_principale), tipo_xml=tipo_xml,
            formato=formato_ipda,
        )
    except MetadatiIPdANonValidi as exc:
        raise ErrorePermanente("AIP-METADATI-001", str(exc)) from exc
    sincro_sha256, _ = compute_aip_hash(sincro_xml)
    sincro_key, sincro_bucket, sincro_vid = await _carica_idempotente(
        aip_id=aip_id, tenant_id=tenant_id, contenuto=sincro_xml,
        suffisso="IndiceAIP_SInCRO.xml", content_type="application/xml", retention=retention,
    )
    scadenza = None if permanente else retention.conserva_fino_a
    # R08 (D01): rapporto di verifica delle firme conservato nel PdA (WORM, stessa
    # retention); la sua impronta entra nel manifest e il RdV lo cita
    firme_info: dict[str, str] | None = None
    if rapporto_firme is not None:
        contenuto_rapporto = json_canonico(rapporto_firme)
        chiave_rapporto, _, _ = await _carica_idempotente(
            aip_id=aip_id, tenant_id=tenant_id, contenuto=contenuto_rapporto,
            suffisso=NOME_RAPPORTO_FIRME, content_type="application/json", retention=retention,
        )
        firme_info = {"chiave": chiave_rapporto,
                      "sha256": hashlib.sha256(contenuto_rapporto).hexdigest(),
                      "livello": str(rapporto_firme.get("livello") or "")}
    # Albero di livello 1 (K1): impronte calcolate, metadati dell'IPdA, IPdA per ultima
    albero = albero_del_pda(urn, documenti, allegato5_map, sincro_sha256)
    await _carica_idempotente(
        aip_id=aip_id, tenant_id=tenant_id, contenuto=contenuto_albero_worm(urn, albero),
        suffisso="catena_pda.json", content_type="application/json", retention=retention,
    )

    # 3. Manifest (deterministico), INSERT AIP + documenti in una transazione
    async with get_async_session() as session:
        manifest = {
            "aip_id": aip_id, "pdv_id": pdv_id, "tenant_id": tenant_id, "urn": urn,
            "classe_documentale": classe, "chiave": chiave,
            "sincro_xml_key": sincro_key, "sincro_hash_sha256": sincro_sha256,
            "documenti": documenti, "data_creazione": now.isoformat(),
            "versione_sincro": "2020", "radice_pda": albero.radice_hex,
        }
        if firme_info is not None:
            manifest["rapporto_verifica_firme"] = firme_info
        manifest_bytes = json.dumps(manifest, default=str, indent=2).encode()
        manifest_sha256, manifest_sha512 = compute_aip_hash(manifest_bytes)
        manifest_key, bucket, manifest_vid = await _carica_idempotente(
            aip_id=aip_id, tenant_id=tenant_id, contenuto=manifest_bytes,
            suffisso="manifest.json", content_type="application/json", retention=retention,
        )
        await session.execute(text(
            "INSERT INTO public.aip "
            "(id, pdv_id, tenant_id, urn, stato, storage_tier, "
            " s3_key, s3_bucket, s3_version_id, sincro_xml_key, "
            " dimensione_bytes, hash_sha256, hash_sha512, "
            " data_versamento, data_conservazione, scadenza_conservazione, anni_conservazione, "
            " rapporto_firme_key, rapporto_firme_sha256, retention_fino_a) "
            "VALUES "
            "(:id, :pdv_id, :tenant_id, :urn, 'SU_STORAGE', 'HOT', "
            " :s3_key, :s3_bucket, :s3_vid, :sincro_key, "
            " :dim, :sha256, :sha512, :data_v, NULL, :scadenza, :anni, :firme_key, :firme_sha, "
            " :retention_fino_a)"
        ), {
            "id": uuid.UUID(aip_id), "pdv_id": uuid.UUID(pdv_id),
            "tenant_id": uuid.UUID(tenant_id), "urn": urn,
            "s3_key": manifest_key, "s3_bucket": bucket, "s3_vid": manifest_vid,
            "sincro_key": sincro_key, "dim": len(manifest_bytes),
            "sha256": manifest_sha256, "sha512": manifest_sha512,
            "data_v": now, "scadenza": scadenza, "anni": anni_conservazione,
            "firme_key": firme_info["chiave"] if firme_info else None,
            "firme_sha": firme_info["sha256"] if firme_info else None,
            # revisione R09 (Z5): termine del blocco WORM dal primo giorno (il
            # rinnovo dei permanenti parte da qui, non da un NULL da censire)
            "retention_fino_a": retention.conserva_fino_a,
        })
        id_righe: dict[str, uuid.UUID] = {}
        for d in documenti:
            id_righe[d["id"]] = uuid.uuid4()
            await session.execute(text(
                "INSERT INTO public.documenti "
                "(id, aip_id, nome_file, tipo, formato_mime, dimensione_bytes, "
                " hash_sha256, hash_sha512, s3_key, ordine) "
                "VALUES (:id, :aip_id, :nome, :tipo, :fmt, :size, :sha256, :sha512, :s3_key, "
                ":ordine)"
            ), {
                "id": id_righe[d["id"]], "aip_id": uuid.UUID(aip_id), "nome": d["nome_file"],
                "tipo": d["tipo"], "fmt": d["formato"], "size": d["dimensione_bytes"],
                "sha256": d["hash_sha256"], "sha512": d["hash_sha512"],
                "s3_key": d["s3_key"], "ordine": d["ordine"],
            })
        await _scrivi_metadati_e_coda_indicizzazione(
            session, aip_id=aip_id, tenant_id=tenant_id, documenti=documenti,
            id_righe=id_righe, allegato5_map=allegato5_map, docs_indice=docs_raw,
        )
        await _scrivi_albero_e_coda(session, aip_id=aip_id, tenant_id=tenant_id, urn=urn,
                                    albero=albero)
        await accoda_firma_ipda(session, aip_id=aip_id, tenant_id=tenant_id,
                                bucket=sincro_bucket, chiave=sincro_key, versione=sincro_vid,
                                impronta_sha256=sincro_sha256)

    logger.info("pda_su_storage_in_coda_registro", aip_id=aip_id, pdv_id=pdv_id,
                num_files=len(documenti), radice_pda=albero.radice_hex[:16])
    return _esito_costruzione(
        {"id": aip_id, "urn": urn, "hash_sha256": manifest_sha256,
         "s3_key": manifest_key, "s3_bucket": bucket, "stato": "SU_STORAGE"},
        pdv_id, tenant_id, classe, len(documenti), albero.radice_hex,
    )
