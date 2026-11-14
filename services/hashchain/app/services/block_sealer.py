# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Sigillo dei blocchi della catena delle impronte, firma del blocco, RdV definitiva.

Servizio: hashchain.
Riferimenti: VD-C-012, VD-C-018, VD-C-019, VD-C-020; REGOLE_SVILUPPO §10.1,
§10.3, §11.2, §12.2, §12.8, §12.11, §15.4.

Flusso (ogni passo è ripetibile senza effetti doppi):
  1. ``sigilla_blocco``: in UNA transazione blocca l'ultimo blocco, rilegge
     dal DB gli AIP del lotto ancora fuori catena (stato SU_STORAGE, nessun
     blocco), calcola Merkle (RFC 6962) e impronta del blocco, inserisce il
     blocco con ``stato_firma = DA_FIRMARE`` e porta gli AIP a IN_CATENA.
     Le impronte vengono dal DB (calcolate dall'aip-builder sui byte), mai
     dal messaggio Kafka. Un AIP già in catena viene ignorato (idempotenza).
  2. ``firma_blocco``: dopo il commit chiede firma/marca al servizio signature;
     esito definitivo FIRMATO o NON_FIRMATO con motivo esplicito.
  3. ``completa_conservazione``: per ogni AIP IN_CATENA il cui blocco non è più
     DA_FIRMARE genera il RdV definitivo, lo scrive in WORM (Object Lock
     COMPLIANCE, retention del PdA: libs/shared/storage/worm.py) e in UNA transazione
     porta AIP e PdV a CONSERVATO e scrive il riferimento al RdV in
     ``pdv.metadata_json['rdv_definitiva']`` (colonna esistente letta dal
     gateway e dalla dashboard). Gli errori si registrano come errori e il
     passo si riprende al giro successivo: nessun errore inghiottito.

Formati del blocco (colonna ``versione_formato``):
  1 = storico: hash = SHA-256("numero:precedente:merkle"), Merkle con
      duplicazione dell'ultima foglia. Si verifica soltanto, non si produce.
  2 = attuale: hash = SHA-256("VERUMDOC-BLOCCO-v2\\0" || JSON canonico con
      numero, precedente, merkle_root, creato_il, aip_ids); Merkle RFC 6962.

La tabella ``hashchain_blocks`` è append-only a livello di database (trigger
della migrazione 20261020_011): UPDATE consentito solo sulle colonne di firma e
solo da DA_FIRMARE; DELETE vietata.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import boto3
import botocore.config
import structlog
from sqlalchemy import text

from app.config import settings
from app.services.firma_blocchi import (
    STATO_DA_FIRMARE,
    ServizioFirmaBlocchi,
)
from app.services.merkle import (
    compute_merkle_root,
    compute_merkle_root_v1_legacy,
    genera_prova_inclusione,
)
from app.services.rdv_definitiva import costruisci_rdv_definitiva
from libs.shared.db import get_async_session
from libs.shared.storage import worm

logger = structlog.get_logger(__name__)

VERSIONE_FORMATO_BLOCCO = 2
_DOMINIO_BLOCCO_V2 = b"VERUMDOC-BLOCCO-v2\x00"


# ── Impronta del blocco ─────────────────────────────────────────────

def compute_block_hash(
    numero: int, hash_precedente: str, merkle_root: str,
) -> str:
    """Formato 1 (storico): SHA-256("numero:precedente:merkle"). Solo verifica."""
    data = f"{numero}:{hash_precedente}:{merkle_root}"
    return hashlib.sha256(data.encode()).hexdigest()


def _iso_utc(istante: datetime) -> str:
    if istante.tzinfo is None:
        raise ValueError("L'istante del blocco deve avere il fuso orario")
    return istante.astimezone(UTC).isoformat(timespec="microseconds")


def calcola_hash_blocco(
    numero: int,
    hash_precedente: str,
    merkle_root: str,
    creato_il: datetime,
    aip_ids: list[str],
) -> str:
    """
    Formato 2: impronta del blocco su JSON canonico con separazione di dominio.
    Include l'istante del sigillo e l'elenco ordinato degli AIP, così né la
    data né la composizione del lotto si possono alterare senza cambiare l'hash.
    """
    canonico = json.dumps(
        {
            "versione": VERSIONE_FORMATO_BLOCCO,
            "numero": numero,
            "precedente": hash_precedente,
            "merkle_root": merkle_root,
            "creato_il": _iso_utc(creato_il),
            "aip_ids": list(aip_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(_DOMINIO_BLOCCO_V2 + canonico.encode("utf-8")).hexdigest()


def hash_atteso_blocco(blocco: dict[str, Any]) -> str:
    """Ricalcola l'impronta di un blocco letto dal DB secondo il suo formato."""
    versione = blocco.get("versione_formato") or 1
    if versione == 1:
        return compute_block_hash(
            blocco["numero_blocco"], blocco["hash_blocco_precedente"], blocco["merkle_root"],
        )
    if versione == 2:
        return calcola_hash_blocco(
            blocco["numero_blocco"],
            blocco["hash_blocco_precedente"],
            blocco["merkle_root"],
            blocco["created_at"],
            list(blocco["aip_ids"] or []),
        )
    raise ValueError(f"Formato blocco sconosciuto: {versione}")


def merkle_atteso_blocco(versione: int, impronte: list[str]) -> str:
    """Radice di Merkle secondo il formato del blocco."""
    if versione == 1:
        return compute_merkle_root_v1_legacy(impronte)
    return compute_merkle_root(impronte)


def _build_rdv_definitiva(**kwargs: Any) -> str:
    """Compatibilità: delega al costruttore unico del RdV definitivo."""
    kwargs.setdefault("conservatore", settings.conservatore_nome)
    return costruisci_rdv_definitiva(**kwargs)


# ── S3 (scrittura WORM condivisa, VD-C-011 / REGOLE §10.2) ─────────

NOME_RDV = "RdV_Definitiva.xml"


@lru_cache(maxsize=1)
def _client() -> Any:
    """Client boto3 unico per processo."""
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        config=botocore.config.Config(signature_version="s3v4", retries={"max_attempts": 3}),
        region_name=settings.s3_region,
    )


def retention_rdv(aip: dict[str, Any], orizzonte: int | None = None) -> worm.RetentionOggetto:
    """Retention del RdV = retention del PdA: stessa classe documentale, stessa
    data di riferimento (``aip.data_versamento``, fissata dall'aip-builder al
    primo tentativo e usata per gli oggetti del PdA). Nessun valore inventato."""
    return worm.calcola_retention(
        worm.PoliticaConservazione(
            anni_conservazione=aip.get("anni_conservazione"),
            permanente=bool(aip.get("conservazione_permanente")),
        ),
        aip["data_versamento"],
        orizzonte_permanente_anni=orizzonte,
    )


def _scrivi_rdv_sync(
    bucket: str, chiave: str, rdv_xml: bytes, retention: worm.RetentionOggetto, aip_id: str,
) -> tuple[bytes, str | None]:
    """Scrive il RdV sul bucket conservativo (Object Lock COMPLIANCE, nessuna
    sovrascrittura). Se esiste già (ripresa dopo un errore successivo alla
    scrittura) si riusa quello conservato: è il RdV emesso, immutabile.
    Restituisce (contenuto conservato, version_id)."""
    client = _client()
    try:
        resp = worm.scrivi_oggetto_conservato(
            client, bucket, chiave, rdv_xml, "application/xml", retention,
            adesso=datetime.now(UTC),
            produzione=settings.env.lower() == "production",
            regione=settings.s3_region,
        )
        return rdv_xml, resp.get("VersionId")
    except worm.OggettoGiaPresente:
        esistente = worm.leggi_oggetto(client, bucket, chiave)
        if aip_id.encode() not in esistente:
            raise worm.ErroreStorageConservativo(
                "oggetto già presente sulla chiave del RdV ma non riferito a questo AIP",
                bucket=bucket, chiave=chiave,
            ) from None
        logger.info("rdv_gia_conservato_riuso", aip_id=aip_id, chiave=chiave)
        return esistente, None


async def _carica_rdv(aip: dict[str, Any], rdv_xml: bytes,
                     nome: str = NOME_RDV) -> tuple[str, str, bytes, str | None]:
    """Carica il RdV definitivo in WORM; restituisce (bucket, chiave, contenuto, version_id).
    ``nome``: file del RdV nel PdA (formato 1 «RdV_Definitiva.xml», formato 2
    «RdV_Definitivo_v2.xml», app/registro/conservazione.py)."""
    bucket = worm.bucket_conservativo(settings)
    chiave = f"{aip['tenant_id']}/{aip['id']}/{nome}"
    from libs.shared.parametri import orizzonte_permanente_anni

    # R09 (D04): orizzonte dal parametro RETENTION_PERMANENTE_ANNI (rinnovo automatico)
    retention = retention_rdv(aip, await orizzonte_permanente_anni(
        settings.retention_permanente_orizzonte_anni))
    contenuto, versione = await asyncio.to_thread(
        _scrivi_rdv_sync, bucket, chiave, rdv_xml, retention, aip["id"],
    )
    return bucket, chiave, contenuto, versione


# ── 1. Sigillo ──────────────────────────────────────────────────────

async def sigilla_blocco(aip_ids: list[str], *, ora: datetime) -> dict | None:
    """
    Sigilla in una transazione gli AIP del lotto ancora fuori catena.

    Restituisce i dati del blocco oppure None se nessun AIP del lotto è da
    sigillare (già in catena o inesistente: riconsegna Kafka).
    """
    richiesti = sorted({str(uuid.UUID(a)) for a in aip_ids})
    if not richiesti:
        return None

    async with get_async_session() as session:
        ultimo = (await session.execute(text(
            "SELECT numero_blocco, hash_blocco FROM public.hashchain_blocks "
            "ORDER BY numero_blocco DESC LIMIT 1 FOR UPDATE"
        ))).mappings().first()
        if ultimo is None:
            raise RuntimeError("Blocco genesi non trovato: catena non inizializzata")

        righe = (await session.execute(
            text(
                "SELECT id::text AS id, hash_sha256 FROM public.aip "
                "WHERE id = ANY(CAST(:ids AS uuid[])) "
                "AND hashchain_block_id IS NULL AND stato = 'SU_STORAGE' "
                "ORDER BY id FOR UPDATE"
            ),
            {"ids": [uuid.UUID(a) for a in richiesti]},
        )).mappings().all()

        ignorati = sorted(set(richiesti) - {r["id"] for r in righe})
        if ignorati:
            logger.warning("sigillo_aip_ignorati", aip_ids=ignorati[:20],
                           motivo="già in catena, non su storage o inesistenti")
        if not righe:
            return None

        ids_blocco = [r["id"] for r in righe]          # ordine totale: id crescente
        impronte = [r["hash_sha256"] for r in righe]
        merkle_root = compute_merkle_root(impronte)
        numero = int(ultimo["numero_blocco"]) + 1
        precedente = str(ultimo["hash_blocco"])
        hash_blocco = calcola_hash_blocco(numero, precedente, merkle_root, ora, ids_blocco)
        blocco_id = str(uuid.uuid4())

        await session.execute(
            text(
                "INSERT INTO public.hashchain_blocks "
                "(id, numero_blocco, hash_blocco, hash_blocco_precedente, merkle_root, "
                " aip_ids, num_aip, created_at, versione_formato, stato_firma) "
                "VALUES (:id, :numero, :hash, :prec, :merkle, CAST(:aip_ids AS jsonb), "
                " :num, :creato, :versione, :stato_firma)"
            ),
            {
                "id": uuid.UUID(blocco_id), "numero": numero, "hash": hash_blocco,
                "prec": precedente, "merkle": merkle_root,
                "aip_ids": json.dumps(ids_blocco), "num": len(ids_blocco),
                "creato": ora, "versione": VERSIONE_FORMATO_BLOCCO,
                "stato_firma": STATO_DA_FIRMARE,
            },
        )
        aggiornati = await session.execute(
            text(
                "UPDATE public.aip SET stato = 'IN_CATENA', hashchain_block_id = :blocco, "
                "updated_at = now() WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"blocco": uuid.UUID(blocco_id), "ids": [uuid.UUID(a) for a in ids_blocco]},
        )
        if aggiornati.rowcount != len(ids_blocco):
            raise RuntimeError(
                f"AIP aggiornati {aggiornati.rowcount} su {len(ids_blocco)}: sigillo annullato"
            )

    logger.info("blocco_sigillato", numero=numero, num_aip=len(ids_blocco),
                hash=hash_blocco[:16], merkle_root=merkle_root[:16])
    return {
        "id": blocco_id,
        "numero_blocco": numero,
        "hash_blocco": hash_blocco,
        "hash_blocco_precedente": precedente,
        "merkle_root": merkle_root,
        "aip_ids": ids_blocco,
        "num_aip": len(ids_blocco),
        "created_at": _iso_utc(ora),
        "versione_formato": VERSIONE_FORMATO_BLOCCO,
    }


# ── 2. Firma ────────────────────────────────────────────────────────

async def firma_blocco(blocco_id: str, servizio: ServizioFirmaBlocchi) -> str:
    """Firma un blocco DA_FIRMARE; restituisce lo stato di firma risultante."""
    async with get_async_session() as session:
        riga = (await session.execute(
            text("SELECT hash_blocco, stato_firma FROM public.hashchain_blocks WHERE id = :id"),
            {"id": uuid.UUID(blocco_id)},
        )).mappings().first()
    if riga is None:
        raise LookupError(f"Blocco {blocco_id} inesistente")
    if riga["stato_firma"] != STATO_DA_FIRMARE:
        return str(riga["stato_firma"])

    # Si firma l'impronta binaria del blocco (32 byte, formato 2)
    esito = await servizio.firma(bytes.fromhex(riga["hash_blocco"]))
    if not esito.definitivo:
        logger.warning("firma_blocco_rinviata", blocco_id=blocco_id, motivo=esito.motivo)
        return STATO_DA_FIRMARE

    async with get_async_session() as session:
        await session.execute(
            text(
                "UPDATE public.hashchain_blocks SET stato_firma = :stato, motivo_firma = :motivo, "
                "firma_cades = :firma, tsa_token = :tsa, tsa_timestamp = :tsa_ts "
                "WHERE id = :id AND stato_firma = 'DA_FIRMARE'"
            ),
            {
                "stato": esito.stato, "motivo": esito.motivo, "firma": esito.firma_b64,
                "tsa": esito.tsa_token, "tsa_ts": esito.tsa_timestamp,
                "id": uuid.UUID(blocco_id),
            },
        )
    logger.info("firma_blocco_registrata", blocco_id=blocco_id, stato=esito.stato,
                motivo=esito.motivo)
    return esito.stato


# ── 3. RdV definitiva e CONSERVATO ──────────────────────────────────

@dataclass
class EsitoCompletamento:
    """Riepilogo di un giro di completamento della conservazione."""
    completati: list[str] = field(default_factory=list)
    falliti: dict[str, str] = field(default_factory=dict)


async def _completa_aip(aip: dict[str, Any], *, emessa_il: datetime) -> None:
    """Emette il RdV di un AIP in catena e porta AIP e PdV a CONSERVATO."""
    aip_id = aip["id"]
    async with get_async_session() as session:
        documenti = (await session.execute(
            text(
                "SELECT nome_file, hash_sha256, hash_sha512 FROM public.documenti "
                "WHERE aip_id = :id ORDER BY ordine, id"
            ),
            {"id": uuid.UUID(aip_id)},
        )).mappings().all()
        ids_blocco = [str(x) for x in (aip["aip_ids"] or [])]
        impronte_blocco = {
            r["id"]: r["hash_sha256"]
            for r in (await session.execute(
                text(
                    "SELECT id::text AS id, hash_sha256 FROM public.aip "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [uuid.UUID(x) for x in ids_blocco]},
            )).mappings().all()
        }

    prova = None
    if (aip["versione_formato"] or 1) >= 2 and aip_id in ids_blocco:
        prova = genera_prova_inclusione(
            [impronte_blocco[x] for x in ids_blocco], ids_blocco.index(aip_id),
        )
    data_conservazione = _iso_utc(aip["blocco_creato_il"])
    rdv_xml = costruisci_rdv_definitiva(
        aip_id=aip_id,
        pdv_id=aip["pdv_id"],
        urn=aip["urn"] or "",
        hash_sha256=aip["hash_sha256"],
        documenti=[dict(d) for d in documenti],
        numero_blocco=int(aip["numero_blocco"]),
        merkle_root=aip["merkle_root"],
        hash_blocco=aip["hash_blocco"],
        hash_blocco_precedente=aip["hash_blocco_precedente"],
        prova_inclusione=prova,
        stato_firma_blocco=aip["stato_firma"],
        motivo_firma_blocco=aip["motivo_firma"],
        data_conservazione=data_conservazione,
        emessa_il=_iso_utc(emessa_il),
        conservatore=settings.conservatore_nome,
    ).encode("utf-8")
    bucket, chiave, rdv_conservato, versione = await _carica_rdv(aip, rdv_xml)

    riferimento = {
        "aip_id": aip_id,
        "s3_bucket": bucket,
        "s3_key": chiave,
        "s3_version_id": versione,
        "impronta_sha256": hashlib.sha256(rdv_conservato).hexdigest(),
        "numero_blocco": int(aip["numero_blocco"]),
        "stato_firma_blocco": aip["stato_firma"],
        "emessa_il": _iso_utc(emessa_il),
        "formato": "verumdoc-rdv-1",
    }
    async with get_async_session() as session:
        aip_agg = await session.execute(
            text(
                "UPDATE public.aip SET stato = 'CONSERVATO', data_conservazione = :data, "
                "updated_at = now() WHERE id = :id AND stato = 'IN_CATENA'"
            ),
            {"id": uuid.UUID(aip_id), "data": aip["blocco_creato_il"]},
        )
        if aip_agg.rowcount != 1:
            raise RuntimeError(f"AIP {aip_id} non più IN_CATENA: completamento annullato")
        pdv_agg = await session.execute(
            text(
                "UPDATE public.pdv SET stato = 'CONSERVATO', data_aggiornamento = now(), "
                "metadata_json = COALESCE(metadata_json, '{}'::jsonb) "
                "  || jsonb_build_object('rdv_definitiva', CAST(:rif AS jsonb)) "
                "WHERE id = :pdv AND tenant_id = :tenant AND stato IN ('IN_BUILD', 'CONSERVATO')"
            ),
            {"rif": json.dumps(riferimento), "pdv": uuid.UUID(aip["pdv_id"]),
             "tenant": uuid.UUID(aip["tenant_id"])},
        )
        if pdv_agg.rowcount != 1:
            raise RuntimeError(
                f"PdV {aip['pdv_id']} non trovato o in stato inatteso: "
                "riferimento al RdV non scritto, completamento annullato"
            )
    logger.info("pda_conservato", aip_id=aip_id, pdv_id=aip["pdv_id"],
                numero_blocco=int(aip["numero_blocco"]), rdv_key=chiave)


async def completa_conservazione(
    *, emessa_il: datetime, blocco_id: str | None = None, limite: int = 200,
) -> EsitoCompletamento:
    """
    Completa gli AIP IN_CATENA il cui blocco ha una firma definitiva
    (FIRMATO o NON_FIRMATO). Gli errori per AIP si registrano e restano da
    riprendere: l'AIP resta IN_CATENA, nulla viene segnato come fatto.
    """
    filtro = "AND b.id = :blocco " if blocco_id else ""
    parametri: dict[str, Any] = {"limite": limite}
    if blocco_id:
        parametri["blocco"] = uuid.UUID(blocco_id)
    async with get_async_session() as session:
        candidati = (await session.execute(
            text(
                # join aip → blocco: servono i dati del blocco per il RdV
                "SELECT a.id::text AS id, a.pdv_id::text AS pdv_id, a.tenant_id::text AS "
                "tenant_id, "
                "a.urn, a.hash_sha256, b.numero_blocco, b.hash_blocco, "
                "b.hash_blocco_precedente, b.merkle_root, b.aip_ids, b.created_at AS "
                "blocco_creato_il, "
                "b.versione_formato, b.stato_firma, b.motivo_firma, "
                # politica di conservazione per la retention del RdV (= quella del PdA)
                "a.data_versamento, c.anni_conservazione, c.conservazione_permanente "
                "FROM public.aip a JOIN public.hashchain_blocks b ON b.id = a.hashchain_block_id "
                "JOIN public.pdv p ON p.id = a.pdv_id "
                "LEFT JOIN public.classi_documentali c ON c.id = p.classe_doc_id "
                "WHERE a.stato = 'IN_CATENA' AND b.stato_firma IS NOT NULL "
                "AND b.stato_firma <> 'DA_FIRMARE' " + filtro +
                "ORDER BY b.numero_blocco, a.id LIMIT :limite"
            ),
            parametri,
        )).mappings().all()

    esito = EsitoCompletamento()
    for riga in candidati:
        try:
            await _completa_aip(dict(riga), emessa_il=emessa_il)
            esito.completati.append(riga["id"])
        except Exception as exc:  # noqa: BLE001 — errore registrato e ripreso al giro dopo
            esito.falliti[riga["id"]] = f"{type(exc).__name__}: {exc}"[:500]
            logger.error("rdv_definitiva_errore", aip_id=riga["id"], pdv_id=riga["pdv_id"],
                         errore=str(exc)[:300])
    return esito


async def riprendi_in_sospeso(
    servizio: ServizioFirmaBlocchi, *, emessa_il: datetime,
) -> EsitoCompletamento:
    """Rete di sicurezza (§22.3): firma i blocchi DA_FIRMARE e completa gli AIP pendenti."""
    async with get_async_session() as session:
        da_firmare = (await session.execute(text(
            "SELECT id::text AS id FROM public.hashchain_blocks "
            "WHERE stato_firma = 'DA_FIRMARE' ORDER BY numero_blocco LIMIT 50"
        ))).scalars().all()
    for blocco_id in da_firmare:
        await firma_blocco(blocco_id, servizio)
    return await completa_conservazione(emessa_il=emessa_il)


# ── Verifica della catena ───────────────────────────────────────────

async def verify_chain_integrity() -> tuple[bool, str]:
    """
    Verifica la catena: numerazione continua, collegamento al precedente,
    impronta di ogni blocco ricalcolata secondo il suo formato e radice di
    Merkle ricalcolata dalle impronte degli AIP registrate nel DB.

    Limite dichiarato: senza firma qualificata o marca esterna sui blocchi,
    chi può scrivere sul DB e disattivare il trigger append-only può
    ricostruire una catena coerente; la verifica lo rileva solo per i blocchi
    FIRMATO (firma esterna) — vedi ``stato_firma``.
    """
    async with get_async_session() as session:
        blocchi = [dict(r) for r in (await session.execute(text(
            "SELECT id::text AS id, numero_blocco, hash_blocco, hash_blocco_precedente, "
            "merkle_root, aip_ids, created_at, versione_formato, stato_firma "
            "FROM public.hashchain_blocks ORDER BY numero_blocco ASC"
        ))).mappings().all()]
        impronte = {
            r["id"]: r["hash_sha256"]
            for r in (await session.execute(text(
                "SELECT id::text AS id, hash_sha256 FROM public.aip "
                "WHERE hashchain_block_id IS NOT NULL"
            ))).mappings().all()
        }
    return verifica_blocchi(blocchi, impronte)


def verifica_blocchi(
    blocchi: list[dict[str, Any]], impronte_aip: dict[str, str],
) -> tuple[bool, str]:
    """Verifica pura di una sequenza di blocchi (ordinata per numero)."""
    if not blocchi:
        return False, "Nessun blocco trovato"
    if blocchi[0]["numero_blocco"] != 0:
        return False, f"Blocco genesi mancante — trovato #{blocchi[0]['numero_blocco']}"

    non_firmati = 0
    for i, blocco in enumerate(blocchi[1:], 1):
        precedente = blocchi[i - 1]
        numero = blocco["numero_blocco"]
        if numero != precedente["numero_blocco"] + 1:
            return False, f"Numerazione interrotta: #{precedente['numero_blocco']} → #{numero}"
        if blocco["hash_blocco_precedente"] != precedente["hash_blocco"]:
            return False, f"Catena interrotta al blocco #{numero}"
        try:
            atteso = hash_atteso_blocco(blocco)
        except ValueError as exc:
            return False, f"Blocco #{numero}: {exc}"
        if atteso != blocco["hash_blocco"]:
            return False, f"Hash non valido al blocco #{numero}"
        ids = [str(x) for x in (blocco["aip_ids"] or [])]
        mancanti = [x for x in ids if x not in impronte_aip]
        if mancanti:
            return False, f"Blocco #{numero}: AIP non trovati o scollegati {mancanti[:3]}"
        merkle = merkle_atteso_blocco(blocco.get("versione_formato") or 1,
                                      [impronte_aip[x] for x in ids])
        if merkle != blocco["merkle_root"]:
            return False, f"Radice di Merkle non corrispondente al blocco #{numero}"
        if blocco.get("stato_firma") != "FIRMATO":
            non_firmati += 1

    messaggio = f"Catena integra — {len(blocchi)} blocchi verificati"
    if non_firmati:
        messaggio += f"; {non_firmati} blocchi senza firma qualificata"
    return True, messaggio
