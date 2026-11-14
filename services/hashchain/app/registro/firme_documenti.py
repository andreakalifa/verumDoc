# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Sigillo qualificato dei documenti del conservatore e ancoraggio dei checkpoint (R13a).

Servizio: hashchain (ha il token del servizio di firma, scrive nel WORM sotto
``registro/`` e ``<ente>/<pda>/Sigillo_*``). Riferimenti: VD-C-068, VD-C-124,
decisioni VD-P-05/07; REGOLE_SVILUPPO §10.2, §12.9, §12.11, §20.2, §31 R12a (3), (5).

Documenti (tabella ``firme_documenti``, scritta da aip-builder per l'IPdA nella
transazione del PdA, dalla hashchain per il RdV nella transazione di
conservazione, da dashboard-api per manuale e piano):

  ``sigilla``  a ogni giro, i documenti ``DA_FIRMARE`` (e i ``NON_FIRMATO`` a
               ogni ``RIPROVA_NON_FIRMATI``, così si firmano appena un fornitore
               qualificato è attivo): l'origine si rilegge dallo storage alla
               VERSIONE registrata e se l'impronta non coincide NON si firma
               (allarme); la busta CAdES (.p7m con il contenuto, livello dai
               parametri) va nel WORM con la retention del PdA (IPdA, RdV) o
               permanente (manuale, piano) e la riga diventa FIRMATO. La chiave
               della busta contiene l'impronta della busta stessa
               (``Sigillo_<nome>.<sha256[:24]>.p7m``): una firma CAdES non è
               deterministica (signing-time, marca), quindi dopo un crash o un
               lease perso fra la scrittura nel WORM e l'UPDATE la nuova firma
               va su una chiave nuova e non collide con la busta orfana (che
               resta nel WORM, mai registrata e quindi mai valida per il
               registro). Un errore su un documento non ferma il lotto: il
               documento riceve un nuovo tentativo con attesa crescente e, dopo
               ``FIRMA_TENTATIVI_MASSIMI`` errori, va in ERRORE (allarme, nessun
               nuovo tentativo: un errore persistente dopo la firma non fa
               pagare una firma ogni ora all'infinito). La retention del PdA si
               legge PRIMA di chiedere la firma. Senza
               fornitore qualificato: ``FIRMA_SENZA_FORNITORE`` = NON_FIRMATO →
               stato NON_FIRMATO con il motivo e allarme; = BLOCCA → resta
               DA_FIRMARE (fail-closed) e il PdA non va a CONSERVATO (``bloccati``);
               errori transitori → nuovo tentativo con attesa crescente.
  ``rinnova``  ogni ``FIRMA_RINNOVO_SECONDI``: i documenti FIRMATO il cui
               certificato o la cui ultima marca scadono entro
               ``FIRMA_RINNOVO_PREAVVISO_GIORNI`` (dopo una marca d'archivio conta
               solo la marca) o firmati con un algoritmo tolto da
               ``FIRMA_ALGORITMI_AMMESSI`` ricevono una nuova marca d'archivio
               (B-LTA): nuova busta nel WORM (``….r<n>.<sha>.p7m``, mai sovrascritta);
               chiave, versione e impronta della busta precedente si aggiungono a
               ``storico_buste`` (il trigger della migrazione 054 lo esige).
Ogni scrittura nel database avviene nella stessa transazione della verifica del
lease del registro (riga di ``catena_stato`` bloccata fino al commit: fencing,
come R12a); un processo che ha perso il lease non scrive.

Checkpoint: ``marca_checkpoint`` marca con RFC 3161 l'ULTIMO checkpoint (gli
altri in attesa diventano SUPERATO: il successivo li impegna) se l'ultima marca
è più vecchia di ``CATENA_MARCA_INTERVALLO_MINUTI``: ancoraggio esterno della
catena. Il token va nel WORM (``registro/marche/<dimensione>.<sha>.tsr``: chiave
con l'impronta del token, per lo stesso motivo delle buste).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.registro import archivio, archivio_worm
from app.registro.metriche import metriche
from app.services.firma_blocchi import (
    STATO_DA_RITENTARE,
    STATO_FIRMATO,
    STATO_MARCATO,
    STATO_NON_FIRMATO,
)
from libs.shared.db import get_async_session
from libs.shared.storage import worm

logger = structlog.get_logger(__name__)

PREFISSO_SIGILLO = "Sigillo_"
DIMENSIONE_MASSIMA = 50 * 1024 * 1024
RIPROVA_NON_FIRMATI = timedelta(hours=1)
ATTESA_MASSIMA = timedelta(hours=1)
TIPI_PACCHETTO = ("IPDA", "RDV")


class OrigineDiversa(RuntimeError):
    """L'oggetto nello storage non ha l'impronta registrata: non si firma."""


# ── parametri ───────────────────────────────────────────────────────────

async def parametri_firma() -> dict[str, Any]:
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import (
        CATENA_MARCA_INTERVALLO_MINUTI,
        FIRMA_ALGORITMI_AMMESSI,
        FIRMA_RINNOVO_PREAVVISO_GIORNI,
        FIRMA_SENZA_FORNITORE,
        FIRMA_TENTATIVI_MASSIMI,
    )

    v = await parametri([FIRMA_SENZA_FORNITORE, FIRMA_RINNOVO_PREAVVISO_GIORNI,
                         FIRMA_ALGORITMI_AMMESSI, CATENA_MARCA_INTERVALLO_MINUTI,
                         FIRMA_TENTATIVI_MASSIMI])
    return {"blocca": v[FIRMA_SENZA_FORNITORE] == "BLOCCA",
            "preavviso_giorni": int(v[FIRMA_RINNOVO_PREAVVISO_GIORNI]),
            "algoritmi": [str(a) for a in v[FIRMA_ALGORITMI_AMMESSI]],
            "marca_minuti": int(v[CATENA_MARCA_INTERVALLO_MINUTI]),
            "tentativi_massimi": int(v[FIRMA_TENTATIVI_MASSIMI])}


# ── storage ─────────────────────────────────────────────────────────────

def _leggi_sync(bucket: str, chiave: str, version_id: str | None) -> bytes:
    from app.services.block_sealer import _client

    argomenti: dict[str, Any] = {"Bucket": bucket, "Key": chiave}
    if version_id:
        argomenti["VersionId"] = version_id
    risposta = _client().get_object(**argomenti)
    if int(risposta.get("ContentLength") or 0) > DIMENSIONE_MASSIMA:
        raise OrigineDiversa(f"oggetto oltre {DIMENSIONE_MASSIMA} byte")
    return bytes(risposta["Body"].read(DIMENSIONE_MASSIMA + 1))


async def leggi_origine(riga: Mapping[str, Any]) -> bytes:
    """Contenuto da firmare: dal database (manuale, piano) o dallo storage alla
    VERSIONE registrata, con l'impronta riverificata (§31 R12a (5))."""
    if riga["contenuto"] is not None:
        dati = bytes(riga["contenuto"])
    else:
        dati = await asyncio.to_thread(_leggi_sync, str(riga["origine_bucket"]),
                                       str(riga["origine_chiave"]),
                                       riga["origine_version_id"] or None)
    if hashlib.sha256(dati).hexdigest() != str(riga["impronta_sha256"]).strip():
        raise OrigineDiversa("impronta dell'origine diversa da quella registrata")
    return dati


def impronta_breve(dati: bytes) -> str:
    """Impronta nella chiave WORM (96 bit bastano: la scrittura confronta comunque
    il contenuto se la chiave esiste già)."""
    return hashlib.sha256(dati).hexdigest()[:24]


def chiave_busta(riga: Mapping[str, Any], busta: bytes, rinnovo: int = 0) -> str:
    """Chiave della busta, con l'impronta della busta: una nuova firma dello
    stesso documento (dopo un crash) non collide mai con una busta orfana."""
    suffisso = (f".r{rinnovo}" if rinnovo else "") + f".{impronta_breve(busta)}.p7m"
    if riga["tipo"] in TIPI_PACCHETTO:
        return f"{riga['tenant_id']}/{riga['aip_id']}/{PREFISSO_SIGILLO}{riga['nome']}{suffisso}"
    return f"registro/documenti/{str(riga['tipo']).lower()}/{riga['impronta_sha256']}{suffisso}"


def _scrivi_pacchetto_sync(chiave: str, busta: bytes, retention: worm.RetentionOggetto
                           ) -> tuple[str, str | None]:
    from app.services.block_sealer import _client

    client, bucket = _client(), worm.bucket_conservativo(settings)
    try:
        r = worm.scrivi_oggetto_conservato(
            client, bucket, chiave, busta, "application/pkcs7-mime", retention,
            adesso=datetime.now(UTC), produzione=settings.env.lower() == "production",
            regione=settings.s3_region)
        return bucket, (str(r["VersionId"]) if r.get("VersionId") else None)
    except worm.OggettoGiaPresente:
        # ripresa dopo un errore successivo alla scrittura: vale solo se identica
        if worm.leggi_oggetto(client, bucket, chiave) != busta:
            raise archivio_worm.ContenutoWormDiverso(
                "busta già presente con contenuto diverso", bucket=bucket,
                chiave=chiave) from None
        return bucket, None


async def retention_pacchetto(riga: Mapping[str, Any]) -> worm.RetentionOggetto | None:
    """Retention del PdA per le buste di IPdA e RdV (None per manuale e piano, che
    vanno nel registro). Si legge PRIMA della firma: un errore qui non costa una firma."""
    if riga["tipo"] not in TIPI_PACCHETTO:
        return None
    from app.services.block_sealer import retention_rdv
    from libs.shared.parametri import orizzonte_permanente_anni

    async with get_async_session() as session:
        aip = (await session.execute(text(
            "SELECT a.id, a.data_versamento, c.anni_conservazione, c.conservazione_permanente "
            "FROM public.aip a JOIN public.pdv p ON p.id = a.pdv_id "
            "LEFT JOIN public.classi_documentali c ON c.id = p.classe_doc_id WHERE a.id = :id"
        ), {"id": riga["aip_id"]})).mappings().one()
    return retention_rdv(dict(aip), await orizzonte_permanente_anni(
        settings.retention_permanente_orizzonte_anni))


async def scrivi_busta(riga: Mapping[str, Any], busta: bytes, *, adesso: datetime,
                       rinnovo: int = 0, retention: worm.RetentionOggetto | None = None
                       ) -> tuple[str, str | None]:
    """Busta nel WORM: con la retention del PdA (IPdA, RdV) o del registro."""
    chiave = chiave_busta(riga, busta, rinnovo)
    if riga["tipo"] not in TIPI_PACCHETTO:
        _, versione = await archivio_worm.scrivi(chiave, busta, "application/pkcs7-mime",
                                                 adesso)
        return chiave, versione
    if retention is None:
        retention = await retention_pacchetto(riga)
    assert retention is not None
    _, versione = await asyncio.to_thread(_scrivi_pacchetto_sync, chiave, busta, retention)
    return chiave, versione


# ── sigillo ─────────────────────────────────────────────────────────────

_SELEZIONE = text(
    "SELECT id, tipo, tenant_id, aip_id, nome, origine_bucket, origine_chiave, "
    "origine_version_id, contenuto, impronta_sha256, stato, tentativi, errori "
    "FROM public.firme_documenti WHERE stato IN ('DA_FIRMARE', 'NON_FIRMATO') "
    "AND (prossimo_tentativo_il IS NULL OR prossimo_tentativo_il <= :adesso) "
    "ORDER BY prossimo_tentativo_il NULLS FIRST, created_at LIMIT :limite")


def _data(valore: Any) -> datetime | None:
    if not valore:
        return None
    try:
        return datetime.fromisoformat(str(valore))
    except ValueError:
        return None


async def _aggiorna(detentore: str, adesso: datetime, sql: str, valori: dict[str, Any]) -> int:
    """UPDATE nella stessa transazione della verifica del lease (fencing)."""
    async with get_async_session() as session:
        await archivio.stato_con_lease(session, detentore, adesso)
        r = await session.execute(text(sql), valori)
        return int(r.rowcount or 0)


async def _rinvia(riga: Mapping[str, Any], motivo: str, *, detentore: str,
                 adesso: datetime) -> None:
    """Nuovo tentativo con attesa crescente (il documento esce dalla testa della coda)."""
    attesa = min(timedelta(minutes=2 ** min(int(riga["tentativi"]), 6)), ATTESA_MASSIMA)
    await _aggiorna(detentore, adesso,
                    "UPDATE public.firme_documenti SET motivo = :m, "
                    "tentativi = tentativi + 1, prossimo_tentativo_il = :p, "
                    "updated_at = :a WHERE id = :id AND stato <> 'FIRMATO'",
                    {"m": motivo[:500], "p": adesso + attesa, "a": adesso, "id": riga["id"]})


async def _errore(riga: Mapping[str, Any], motivo: str, *, massimo: int, detentore: str,
                  adesso: datetime) -> None:
    """Errore non previsto su un documento: nuovo tentativo con attesa crescente
    oppure, al ``massimo``-esimo errore, stato ERRORE con allarme (nessun nuovo
    tentativo, quindi nessuna firma pagata a vuoto)."""
    if int(riga["errori"]) + 1 < massimo:
        await _aggiorna(detentore, adesso,
                        "UPDATE public.firme_documenti SET errori = errori + 1 "
                        "WHERE id = :id AND stato <> 'FIRMATO'", {"id": riga["id"]})
        await _rinvia(riga, motivo, detentore=detentore, adesso=adesso)
        return
    await _aggiorna(detentore, adesso,
                    "UPDATE public.firme_documenti SET stato = 'ERRORE', errori = errori + 1, "
                    "tentativi = tentativi + 1, motivo = :m, prossimo_tentativo_il = NULL, "
                    "updated_at = :a WHERE id = :id AND stato <> 'FIRMATO'",
                    {"m": f"{motivo} (dopo {massimo} errori: ERRORE)"[:500], "a": adesso,
                     "id": riga["id"]})
    logger.error("firma_documento_in_errore", id=str(riga["id"]), tipo=riga["tipo"],
                 errori=int(riga["errori"]) + 1,
                 azione="correggere la causa e rimettere il documento DA_FIRMARE")


async def _sigilla_uno(servizio: Any, riga: Mapping[str, Any], param: Mapping[str, Any],
                       conteggi: dict[str, int], *, detentore: str, adesso: datetime) -> None:
    """Un documento: origine riverificata, firma, busta nel WORM, riga FIRMATO."""
    try:
        contenuto = await leggi_origine(riga)
    except OrigineDiversa as exc:
        metriche().firme_origine_diversa.inc()
        logger.error("firma_documento_origine_diversa", id=str(riga["id"]),
                     tipo=riga["tipo"], motivo=str(exc)[:200])
        await _aggiorna(detentore, adesso,
                        "UPDATE public.firme_documenti SET motivo = :m, "
                        "prossimo_tentativo_il = :p, tentativi = tentativi + 1, "
                        "updated_at = :a WHERE id = :id AND stato <> 'FIRMATO'",
                        {"m": f"origine non verificata: {exc}"[:500], "id": riga["id"],
                         "p": adesso + ATTESA_MASSIMA, "a": adesso})
        conteggi["origine_diversa"] += 1
        return
    retention = await retention_pacchetto(riga)  # prima della firma (seconda revisione)
    esito = await servizio.firma(contenuto, formato="cades", livello=None)
    if esito.stato == STATO_FIRMATO and esito.firma_b64:
        busta = base64.b64decode(esito.firma_b64)
        chiave, versione = await scrivi_busta(riga, busta, adesso=adesso, retention=retention)
        d = esito.dettagli or {}
        await _aggiorna(detentore, adesso, (
            "UPDATE public.firme_documenti SET stato = 'FIRMATO', motivo = NULL, "
            "busta_chiave = :chiave, busta_version_id = :versione, busta_sha256 = :sha, "
            "busta = :busta, formato = :formato, livello = :livello, "
            "fornitore_firma = :ff, fornitore_marca = :fm, qualificata = true, "
            "certificato_sha256 = :cs, certificato_soggetto = :sog, "
            "certificato_scadenza = :cscad, marca_scadenza = :mscad, algoritmo = :alg, "
            "data_marca = :dm, firmato_il = :adesso, prossimo_tentativo_il = NULL, "
            "updated_at = :adesso WHERE id = :id AND stato <> 'FIRMATO'"), {
            "chiave": chiave, "versione": versione,
            "sha": hashlib.sha256(busta).hexdigest(),
            "busta": busta if riga["tipo"] not in TIPI_PACCHETTO else None,
            "formato": str(d.get("formato") or "CAdES")[:10],
            "livello": str(d.get("livello") or "")[:10] or None,
            "ff": str(d.get("codice_fornitore") or d.get("fornitore_firma") or "")[:40],
            "fm": str(d.get("fornitore_marca") or "")[:40] or None,
            "cs": d.get("certificato_sha256") or None,
            "sog": str(d.get("firmatario") or "")[:500] or None,
            "cscad": _data(d.get("certificato_scadenza")),
            "mscad": _data(d.get("marca_scadenza")),
            "alg": str(d.get("algoritmo") or "sha256")[:10],
            "dm": esito.tsa_timestamp, "adesso": adesso, "id": riga["id"]})
        conteggi["firmati"] += 1
        logger.info("documento_sigillato", id=str(riga["id"]), tipo=riga["tipo"],
                    livello=d.get("livello"))
    elif esito.stato == STATO_NON_FIRMATO:
        if param["blocca"]:
            # fail-closed: resta DA_FIRMARE (il PdA non va a CONSERVATO)
            await _aggiorna(detentore, adesso,
                            "UPDATE public.firme_documenti SET stato = 'DA_FIRMARE', "
                            "motivo = :m, prossimo_tentativo_il = :p, updated_at = :a "
                            "WHERE id = :id AND stato <> 'FIRMATO'",
                            {"m": f"{esito.motivo} (FIRMA_SENZA_FORNITORE=BLOCCA)"[:500],
                             "p": adesso + timedelta(minutes=5), "a": adesso,
                             "id": riga["id"]})
            conteggi["rinviati"] += 1
        else:
            await _aggiorna(detentore, adesso,
                            "UPDATE public.firme_documenti SET stato = 'NON_FIRMATO', "
                            "motivo = :m, prossimo_tentativo_il = :p, updated_at = :a "
                            "WHERE id = :id AND stato <> 'FIRMATO'",
                            {"m": (esito.motivo or "non firmato")[:500],
                             "p": adesso + RIPROVA_NON_FIRMATI, "a": adesso,
                             "id": riga["id"]})
            conteggi["non_firmati"] += 1
    else:  # DA_RITENTARE
        await _rinvia(riga, esito.motivo or "da ritentare", detentore=detentore, adesso=adesso)
        conteggi["rinviati"] += 1


async def sigilla(servizio: Any, *, detentore: str, adesso: datetime,
                  limite: int | None = None) -> dict[str, int]:
    """Un giro di sigillo dei documenti (vedi modulo)."""
    param = await parametri_firma()
    async with get_async_session() as session:
        righe = (await session.execute(_SELEZIONE, {
            "adesso": adesso, "limite": limite or settings.firma_documenti_lotto})
        ).mappings().all()
    conteggi = {"firmati": 0, "non_firmati": 0, "rinviati": 0, "origine_diversa": 0,
                "errori": 0}
    for riga in righe:
        try:
            await _sigilla_uno(servizio, riga, param, conteggi, detentore=detentore,
                               adesso=adesso)
        except archivio.LeasePerso:
            raise
        except Exception as exc:  # noqa: BLE001 — un documento non ferma il lotto
            conteggi["errori"] += 1
            logger.error("firma_documento_errore", id=str(riga["id"]), tipo=riga["tipo"],
                         errore=type(exc).__name__, dettaglio=str(exc)[:200])
            await _errore(riga, f"errore: {type(exc).__name__}: {exc}",
                          massimo=param["tentativi_massimi"], detentore=detentore,
                          adesso=adesso)
    await aggiorna_metriche()
    if conteggi["non_firmati"] or conteggi["rinviati"] or conteggi["errori"]:
        logger.warning("documenti_non_sigillati", **conteggi)
    return conteggi


async def aggiorna_metriche() -> None:
    async with get_async_session() as session:
        riga = (await session.execute(text(
            "SELECT count(*) FILTER (WHERE stato = 'DA_FIRMARE') AS da, "
            "count(*) FILTER (WHERE stato = 'NON_FIRMATO') AS non_firmati, "
            "count(*) FILTER (WHERE stato = 'ERRORE') AS in_errore "
            "FROM (SELECT stato FROM public.firme_documenti WHERE stato <> 'FIRMATO' "
            "LIMIT 100000) x"))).mappings().one()
    metriche().firme_da_firmare.set(int(riga["da"]))
    metriche().firme_non_firmati.set(int(riga["non_firmati"]))
    metriche().firme_in_errore.set(int(riga["in_errore"]))
    from app.registro.firme_responsabile import conta_respinte

    metriche().firme_responsabile_respinte.set(await conta_respinte())


# ── arretrato: IPdA dei PdA conservati prima di R13a (VD-C-165) ─────────

NOME_IPDA = "IndiceAIP_SInCRO.xml"

_CANDIDATI_ARRETRATO = text(
    "SELECT a.id, a.tenant_id, a.s3_bucket, a.s3_key, a.s3_version_id, a.hash_sha256 "
    "FROM public.aip a WHERE a.stato = 'CONSERVATO' "
    "AND (CAST(:dopo AS uuid) IS NULL OR a.id > CAST(:dopo AS uuid)) "
    "AND NOT EXISTS (SELECT 1 FROM public.firme_documenti f "
    "                WHERE f.tipo = 'IPDA' AND f.aip_id = a.id) "
    "ORDER BY a.id LIMIT :limite")


#: versioni al più esaminate per trovare quella dell'IPdA storico
VERSIONI_MASSIME = 20


def _versione_con_impronta_sync(bucket: str, chiave: str, impronta: str) -> str | None:
    """Revisione R14 (6): la VERSIONE dell'oggetto i cui byte hanno ``impronta`` (non la
    versione corrente, che dopo una sovrascrittura non sarebbe quella impegnata nel
    manifest). ``None`` per un bucket senza versioni con l'oggetto corrente giusto;
    ``OrigineDiversa`` se nessuna versione ha l'impronta."""
    from app.services.block_sealer import _client

    client = _client()
    versioni = [v for v in client.list_object_versions(
        Bucket=bucket, Prefix=chiave, MaxKeys=VERSIONI_MASSIME).get("Versions", [])
        if v.get("Key") == chiave]
    if not versioni:
        corpo = client.get_object(Bucket=bucket, Key=chiave)["Body"].read(DIMENSIONE_MASSIMA + 1)
        if hashlib.sha256(corpo).hexdigest() == impronta:
            return None
        raise OrigineDiversa("IPdA storico con impronta diversa da quella del manifest")
    for v in sorted(versioni, key=lambda x: x.get("LastModified") or 0, reverse=True):
        vid = v.get("VersionId")
        argomenti: dict[str, Any] = {"Bucket": bucket, "Key": chiave}
        if vid and vid != "null":
            argomenti["VersionId"] = vid
        corpo = client.get_object(**argomenti)["Body"].read(DIMENSIONE_MASSIMA + 1)
        if hashlib.sha256(corpo).hexdigest() == impronta:
            return str(vid) if vid and vid != "null" else None
    raise OrigineDiversa("nessuna versione dell'IPdA storico ha l'impronta del manifest")


async def origine_ipda_storica(pda: Mapping[str, Any]) -> dict[str, Any]:
    """Origine dell'IPdA di un PdA storico, presa dal SUO manifest: il manifest si legge
    alla versione registrata e deve avere l'impronta registrata nel PdA (``hash_sha256``,
    impegnata nel registro); l'impronta dell'IPdA è quella scritta nel manifest
    (``sincro_hash_sha256``), mai calcolata sull'oggetto di oggi, e la versione registrata è
    quella i cui byte hanno quell'impronta (revisione R14 (6)). Il sigillo riverifica i byte
    dell'IPdA contro quell'impronta prima di firmare (``leggi_origine``)."""
    import json

    manifest = await asyncio.to_thread(_leggi_sync, str(pda["s3_bucket"]), str(pda["s3_key"]),
                                       pda["s3_version_id"] or None)
    if hashlib.sha256(manifest).hexdigest() != str(pda["hash_sha256"]).strip():
        raise OrigineDiversa("manifest del PdA con impronta diversa da quella registrata")
    dati = json.loads(manifest)
    chiave, impronta = dati.get("sincro_xml_key"), str(dati.get("sincro_hash_sha256") or "")
    if not chiave or len(impronta) != 64:
        raise OrigineDiversa("manifest senza chiave o impronta dell'IPdA")
    versione = await asyncio.to_thread(_versione_con_impronta_sync, str(pda["s3_bucket"]),
                                       str(chiave), impronta)
    return {"bucket": str(pda["s3_bucket"]), "chiave": str(chiave), "versione": versione,
            "impronta": impronta}


async def censisci_arretrato_ipda(servizio: Any, *, detentore: str, adesso: datetime,
                                  limite: int | None = None) -> dict[str, Any]:
    """R14 (VD-C-165): un giro del censimento degli IPdA dei PdA CONSERVATI senza riga in
    ``firme_documenti`` (prima della migrazione 054). Solo con un fornitore qualificato
    attivo (si attiva col prestatore: senza, nessuna riga che diventerebbe NON_FIRMATO).
    Cursore in ``firme_arretrato_ipda``; al più ``limite`` PdA per giro; inserimento e
    cursore nella STESSA transazione della verifica del lease del registro (fencing).
    Idempotente: ``ON CONFLICT DO NOTHING`` sulla riga dell'IPdA e, per la busta, chiave
    WORM con l'impronta (``chiave_busta``). Un PdA con manifest illeggibile o di impronta
    diversa NON si accoda (allarme) e il cursore lo supera; il passaggio successivo (dopo
    ``FIRMA_ARRETRATO_IPDA_RIPASSO_SECONDI``) lo ritenta."""
    if not settings.firma_arretrato_ipda_attivo:
        return {"disattivo": True}
    limite = limite or settings.firma_arretrato_ipda_lotto
    async with get_async_session() as session:
        cursore = (await session.execute(text(
            "SELECT ultimo_aip, completato_il FROM public.firme_arretrato_ipda WHERE id = 1")
        )).mappings().first()
    if cursore is not None and cursore["completato_il"] is not None and \
            cursore["completato_il"] > adesso - timedelta(
                seconds=settings.firma_arretrato_ipda_ripasso_secondi):
        metriche().firme_arretrato_completato.set(1)
        return {"completato": True}
    qualificato = getattr(servizio, "qualificato", None)
    if qualificato is None or not await qualificato():
        return {"in_attesa": "nessun fornitore qualificato attivo"}
    dopo = None
    if cursore is not None and cursore["completato_il"] is None:
        dopo = cursore["ultimo_aip"]
    async with get_async_session() as session:
        candidati = (await session.execute(_CANDIDATI_ARRETRATO, {
            "dopo": str(dopo) if dopo else None, "limite": limite})).mappings().all()
    righe: list[dict[str, Any]] = []
    scartati = 0
    for pda in candidati:
        try:
            origine = await origine_ipda_storica(pda)
        except Exception as exc:  # noqa: BLE001 — un PdA non ferma il censimento
            scartati += 1
            metriche().firme_arretrato_scartati.inc()
            logger.error("firma_arretrato_ipda_scartato", aip_id=str(pda["id"]),
                         errore=type(exc).__name__, dettaglio=str(exc)[:200])
            continue
        righe.append({"t": pda["tenant_id"], "a": pda["id"], "n": NOME_IPDA,
                      "b": origine["bucket"], "c": origine["chiave"], "v": origine["versione"],
                      "h": origine["impronta"]})
    finito = len(candidati) < limite
    inseriti = 0
    async with get_async_session() as session:
        await archivio.stato_con_lease(session, detentore, adesso)
        for riga in righe:
            r = await session.execute(text(
                "INSERT INTO public.firme_documenti (tipo, tenant_id, aip_id, nome, "
                "origine_bucket, origine_chiave, origine_version_id, impronta_sha256) VALUES "
                "('IPDA', :t, :a, :n, :b, :c, :v, :h) "
                "ON CONFLICT (tipo, aip_id) WHERE aip_id IS NOT NULL DO NOTHING"), riga)
            inseriti += int(r.rowcount or 0)
        await session.execute(text(
            "INSERT INTO public.firme_arretrato_ipda (id, ultimo_aip, inseriti, completato_il, "
            "aggiornato_il) VALUES (1, :u, :i, :c, :a) ON CONFLICT (id) DO UPDATE SET "
            "ultimo_aip = EXCLUDED.ultimo_aip, inseriti = firme_arretrato_ipda.inseriti + "
            ":i, completato_il = EXCLUDED.completato_il, aggiornato_il = EXCLUDED.aggiornato_il"),
            {"u": None if finito else candidati[-1]["id"], "i": inseriti,
             "c": adesso if finito else None, "a": adesso})
    metriche().firme_arretrato_inseriti.inc(inseriti)
    metriche().firme_arretrato_completato.set(1 if finito else 0)
    if inseriti or scartati:
        logger.info("firma_arretrato_ipda", inseriti=inseriti, scartati=scartati,
                    completato=finito)
    return {"inseriti": inseriti, "scartati": scartati, "completato": finito}


# ── cancello della conservazione ────────────────────────────────────────

async def bloccati(session: Any, aip_ids: Iterable[str]) -> set[str]:
    """PdA che NON vanno a CONSERVATO con ``FIRMA_SENZA_FORNITORE=BLOCCA``: IPdA non
    ancora FIRMATO, o riga dell'IPdA assente per un PdA nato dopo la migrazione 054
    (fuori dall'elenco di transizione). Con NON_FIRMATO: nessuno."""
    elenco = [uuid.UUID(str(a)) for a in aip_ids]
    if not elenco or not (await parametri_firma())["blocca"]:
        return set()
    righe = (await session.execute(text(
        "SELECT a.id::text FROM unnest(CAST(:ids AS uuid[])) AS a(id) "
        "LEFT JOIN public.firme_documenti f ON f.tipo = 'IPDA' AND f.aip_id = a.id "
        "LEFT JOIN public.firma_transizione_r13 t ON t.aip_id = a.id "
        "WHERE (f.id IS NOT NULL AND f.stato <> 'FIRMATO') OR (f.id IS NULL AND t.aip_id IS NULL)"
    ), {"ids": elenco})).scalars().all()
    trovati = {str(r) for r in righe}
    metriche().firme_bloccate_conservazione.set(len(trovati))
    if trovati:
        logger.error("conservazione_bloccata_ipda_non_firmato", pda=len(trovati),
                     azione="attivare un fornitore qualificato (console: Fornitori di firma "
                            "e marca) o FIRMA_SENZA_FORNITORE=NON_FIRMATO")
    return trovati


async def accoda_rdv(session: Any, *, tenant_id: str, aip_id: str, bucket: str, chiave: str,
                     versione: str | None, contenuto: bytes, nome: str) -> None:
    """RdV da sigillare, nella STESSA transazione della conservazione."""
    await session.execute(text(
        "INSERT INTO public.firme_documenti (tipo, tenant_id, aip_id, nome, origine_bucket, "
        "origine_chiave, origine_version_id, impronta_sha256) VALUES ('RDV', :t, :a, :n, "
        ":b, :c, :v, :h) ON CONFLICT (tipo, aip_id) WHERE aip_id IS NOT NULL DO NOTHING"),
        {"t": uuid.UUID(tenant_id), "a": uuid.UUID(aip_id), "n": nome[:200], "b": bucket,
         "c": chiave, "v": versione, "h": hashlib.sha256(contenuto).hexdigest()})


# ── rimarcatura ─────────────────────────────────────────────────────────

_IN_SCADENZA = text(
    "SELECT id, tipo, tenant_id, aip_id, nome, impronta_sha256, busta_chiave, "
    "busta_version_id, busta_sha256, rinnovi, livello, algoritmo, certificato_scadenza, "
    "marca_scadenza FROM public.firme_documenti WHERE stato = 'FIRMATO' AND ("
    "  (CASE WHEN livello = 'B-LTA' THEN marca_scadenza "
    "        ELSE LEAST(certificato_scadenza, marca_scadenza) END) < :soglia "
    "  OR NOT (algoritmo = ANY(CAST(:ammessi AS text[])))) "
    "ORDER BY LEAST(certificato_scadenza, marca_scadenza) NULLS FIRST LIMIT :limite")


async def rinnova(servizio: Any, *, detentore: str, adesso: datetime,
                  limite: int | None = None) -> dict[str, Any]:
    """Rimarcatura dei documenti in scadenza (vedi modulo): nuova busta, mai
    sovrascritta; ogni aggiornamento con il lease verificato (fencing)."""
    param = await parametri_firma()
    soglia = adesso + timedelta(days=param["preavviso_giorni"])
    async with get_async_session() as session:
        righe = (await session.execute(_IN_SCADENZA, {
            "soglia": soglia, "ammessi": param["algoritmi"],
            "limite": limite or settings.firma_rinnovo_lotto})).mappings().all()
    rinnovati, errori = 0, []
    bucket = worm.bucket_conservativo(settings)
    for riga in righe:
        try:
            busta = await asyncio.to_thread(_leggi_sync, bucket, str(riga["busta_chiave"]),
                                            riga["busta_version_id"] or None)
            if hashlib.sha256(busta).hexdigest() != str(riga["busta_sha256"]).strip():
                raise OrigineDiversa("busta nello storage diversa da quella registrata")
            retention = await retention_pacchetto(riga)  # prima della marca (si paga)
            esito = await servizio.rimarca(busta)
            if esito.stato != STATO_FIRMATO or not esito.firma_b64:
                raise RuntimeError(esito.motivo or "rimarcatura non riuscita")
            nuova = base64.b64decode(esito.firma_b64)
            n = int(riga["rinnovi"]) + 1
            chiave, versione = await scrivi_busta(riga, nuova, adesso=adesso, rinnovo=n,
                                                  retention=retention)
            d = esito.dettagli or {}
            motivo = ("algoritmo non più ammesso" if riga["algoritmo"] not in param["algoritmi"]
                      else "scadenza del certificato o della marca entro il preavviso")
            fatti = await _aggiorna(detentore, adesso, (
                "UPDATE public.firme_documenti SET busta_chiave = :c, busta_version_id = :v, "
                "busta_sha256 = :s, "
                # busta precedente nello storico (seconda revisione (3)): valori OLD
                "storico_buste = storico_buste || jsonb_build_array(jsonb_build_object("
                "'busta_chiave', busta_chiave, 'busta_version_id', busta_version_id, "
                "'busta_sha256', busta_sha256, 'livello', livello, 'rinnovo', rinnovi, "
                "'sostituita_il', CAST(:a AS timestamptz))), "
                "busta = CASE WHEN busta IS NULL THEN NULL ELSE CAST(:b AS bytea) END, "
                "livello = 'B-LTA', algoritmo = :alg, marca_scadenza = :ms, rinnovi = :n, "
                "rinnovato_il = :a, rinnovo_motivo = :m, updated_at = :a "
                "WHERE id = :id AND rinnovi = :prima AND busta_sha256 = :vecchia"), {
                "c": chiave, "v": versione, "s": hashlib.sha256(nuova).hexdigest(), "b": nuova,
                "alg": str(d.get("algoritmo") or "sha256")[:10],
                "ms": _data(d.get("marca_scadenza")), "n": n, "a": adesso, "m": motivo,
                "id": riga["id"], "prima": riga["rinnovi"], "vecchia": riga["busta_sha256"]})
            if fatti == 1:
                rinnovati += 1
        except Exception as exc:  # noqa: BLE001 — un documento non ferma il lotto
            if isinstance(exc, archivio.LeasePerso):
                raise
            errori.append(f"{riga['id']}: {type(exc).__name__}")
            logger.error("rimarcatura_documento_fallita", id=str(riga["id"]),
                         errore=type(exc).__name__, dettaglio=str(exc)[:200])
    metriche().firme_rinnovo_in_errore.set(len(errori))
    async with get_async_session() as session:
        restanti = int((await session.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM public.firme_documenti WHERE stato = 'FIRMATO' "
            "AND (CASE WHEN livello = 'B-LTA' THEN marca_scadenza "
            "ELSE LEAST(certificato_scadenza, marca_scadenza) END) < :soglia LIMIT 100000) x"),
            {"soglia": soglia})).scalar_one())
    metriche().firme_in_scadenza.set(restanti)
    return {"controllati": len(righe), "rinnovati": rinnovati, "errori": errori[:20],
            "in_scadenza_dopo": restanti}


# ── ancoraggio dei checkpoint ───────────────────────────────────────────

async def marca_checkpoint(servizio: Any, *, detentore: str, adesso: datetime) -> str | None:
    """Marca RFC 3161 dell'ultimo checkpoint se l'ultima marca è più vecchia
    dell'intervallo; restituisce lo stato registrato (o None se non dovuta)."""
    from libs.catena import nota

    intervallo = timedelta(minutes=(await parametri_firma())["marca_minuti"])
    async with get_async_session() as session:
        ultima = (await session.execute(text(
            "SELECT max(marcato_il) FROM public.catena_checkpoint WHERE stato_marca = 'MARCATO'"
        ))).scalar_one()
        candidato = (await session.execute(text(
            "SELECT dimensione, testo FROM public.catena_checkpoint "
            "WHERE stato_marca = 'DA_MARCARE' ORDER BY dimensione DESC LIMIT 1"
        ))).mappings().first()
    metriche().checkpoint_marca_eta.set(
        (adesso - ultima).total_seconds() if ultima is not None else -1)
    if candidato is None or (ultima is not None and adesso - ultima < intervallo):
        return None
    n = int(candidato["dimensione"])
    corpo, _ = nota.separa(str(candidato["testo"]))
    esito = await servizio.marca(hashlib.sha256(corpo.encode("utf-8")).digest())
    if not esito.definitivo:
        logger.warning("marca_checkpoint_rinviata", dimensione=n, motivo=esito.motivo)
        return None
    chiave = None
    if esito.stato == STATO_MARCATO and esito.token_b64:
        token = base64.b64decode(esito.token_b64)
        # chiave con l'impronta del token: dopo un crash/lease perso la nuova marca
        # (diversa) non collide con il token orfano
        chiave = f"registro/marche/{n}.{impronta_breve(token)}.tsr"
        await archivio_worm.scrivi(chiave, token, "application/timestamp-reply", adesso)
    async with get_async_session() as session:
        await archivio.stato_con_lease(session, detentore, adesso)
        await session.execute(text(
            "UPDATE public.catena_checkpoint SET stato_marca = :s, motivo_marca = :m, "
            "marca_token = :t, marca_worm_chiave = :c, marcato_il = :i, marca_tsa = :tsa "
            "WHERE dimensione = :n AND stato_marca = 'DA_MARCARE'"),
            {"s": esito.stato, "m": esito.motivo, "t": esito.token_b64, "c": chiave,
             "i": esito.istante or (adesso if esito.stato == STATO_MARCATO else None),
             "tsa": esito.tsa, "n": n})
        # i precedenti in attesa sono impegnati dal checkpoint più recente (coerenza
        # RFC 6962): non si marcano più, comunque sia andata la marca di questo
        await session.execute(text(
            "UPDATE public.catena_checkpoint SET stato_marca = 'SUPERATO', "
            "motivo_marca = :m WHERE dimensione < :n AND stato_marca = 'DA_MARCARE'"),
            {"m": f"impegnato dal checkpoint {n}", "n": n})
    if esito.stato == STATO_MARCATO:
        metriche().checkpoint_marca_eta.set(0)
    logger.info("checkpoint_marcato" if esito.stato == STATO_MARCATO
                else "checkpoint_non_marcato", dimensione=n, motivo=esito.motivo)
    return esito.stato


# ── sigillo dell'attestazione del ciclo di vita (VD-C-124) ───────────────

async def sigillo_attestazione(attestazione: str | None, *, richiesto: bool,
                               servizio: Any) -> dict[str, Any]:
    """Sigillo qualificato (CAdES staccata B-T) sul testo dell'attestazione,
    secondo ``ATTESTAZIONE_SIGILLO``. Mai un errore della prova: lo stato lo dice."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import (
        ATTESTAZIONE_DISATTIVO,
        ATTESTAZIONE_SIGILLO,
        ATTESTAZIONE_SU_RICHIESTA,
    )

    modo = str((await parametri([ATTESTAZIONE_SIGILLO]))[ATTESTAZIONE_SIGILLO])
    vuoto = {"attestazione_sigillo": None, "attestazione_sigillo_motivo": None}
    if modo == ATTESTAZIONE_DISATTIVO:
        return {**vuoto, "attestazione_sigillo_stato": "DISATTIVO"}
    if attestazione is None or (modo == ATTESTAZIONE_SU_RICHIESTA and not richiesto):
        return {**vuoto, "attestazione_sigillo_stato": "NON_RICHIESTO"}
    esito = await servizio.firma(attestazione.encode("utf-8"), formato="cades_detached",
                                 livello="B-T")
    if esito.stato == STATO_FIRMATO and esito.firma_b64:
        return {"attestazione_sigillo": esito.firma_b64, "attestazione_sigillo_stato": "FIRMATO",
                "attestazione_sigillo_motivo": None}
    return {"attestazione_sigillo": None,
            "attestazione_sigillo_stato": "NON_FIRMATO" if esito.stato == STATO_NON_FIRMATO
            else "NON_DISPONIBILE", "attestazione_sigillo_motivo": esito.motivo}


__all__ = ["OrigineDiversa", "accoda_rdv", "bloccati", "chiave_busta", "impronta_breve",
           "leggi_origine", "marca_checkpoint", "parametri_firma", "rinnova", "sigilla",
           "sigillo_attestazione", "STATO_DA_RITENTARE"]
