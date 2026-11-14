# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Completamento della conservazione dei PdA coperti da un checkpoint: RdV
definitivo v2 nel WORM, PdA e PdV a CONSERVATO, evento ``aip.conserved``.

Servizio: hashchain. Riferimenti: VD-C-097 (l'evento «conservato» parte solo
a PdA nel registro e coperto da checkpoint), VD-C-096 / contratto K5 (RdV
definitivo v2), contratto K1 (evento con ``pda``, ``foglia_indice``,
``checkpoint_dimensione``); REGOLE_SVILUPPO §10.1-§10.3, §11.6, §12.3.

Per ogni giro:
  0. se un checkpoint RIFIUTATO dal testimone non è ancora stato gestito da un
     operatore, il giro non completa nulla (allarme persistente, rev4 rilievo 1):
     un rifiuto è il segnale di una riscrittura o biforcazione del registro;
  1. checkpoint di riferimento (``archivio.checkpoint_utilizzabili``), sempre
     con sigillo definitivo (FIRMATO o NON_FIRMATO): per ogni PdA l'ultimo
     CONTROFIRMATO che lo copre; senza, l'ultimo NON_CONFIGURATO solo se il
     testimone non è configurato in un ambiente che lo ammette (mai in
     produzione). Un checkpoint IN_ATTESA (il testimone tace: allarme oltre
     ``CATENA_CONTROFIRMA_MAX_SECONDI``), SUPERATO o RIFIUTATO non porta MAI a
     CONSERVATO;
  2. PdA IN_CATENA (fuori dalla vecchia catena a blocchi) con foglia coperta,
     in ordine di indice;
  3. per ciascuno: RdV v2 con le prove → verifica offline del documento con la
     chiave del registro (un RdV che non si verifica NON si scrive) → WORM con la
     retention del PdA → in UNA transazione AIP e PdV a CONSERVATO, riferimento
     al RdV in ``pdv.metadata_json['rdv_definitiva']`` ed evento ``aip.conserved``
     in outbox. Un errore lascia il PdA IN_CATENA: si riprende al giro dopo.

R13a (VD-C-068): nella stessa transazione il RdV entra fra i documenti da
sigillare (``firme_documenti``); con ``FIRMA_SENZA_FORNITORE=BLOCCA`` un PdA
il cui IPdA non è ancora FIRMATO resta IN_CATENA (``firme_documenti.bloccati``).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.registro import archivio, prove
from app.registro.rdv import FORMATO, costruisci_rdv, riferimenti_rdv, verifica_rdv
from app.services.block_sealer import _carica_rdv
from libs.catena import nota
from libs.catena.foglie import iso_utc
from libs.shared.db import get_async_session
from libs.shared.outbox import accoda_evento

logger = structlog.get_logger(__name__)

TOPIC_CONSERVATO = "aip.conserved"
NOME_RDV_V2 = "RdV_Definitivo_v2.xml"


class RdvNonVerificato(RuntimeError):
    """Il RdV costruito non supera la verifica offline: non si scrive."""


@dataclass
class EsitoConservazione:
    """Riepilogo di un giro."""

    checkpoint: int | None = None
    completati: list[str] = field(default_factory=list)
    falliti: dict[str, str] = field(default_factory=dict)
    #: Motivo per cui il giro non ha completato nulla (rifiuto aperto)
    sospeso: str | None = None


def _documenti(pda: prove.PdaNelRegistro, righe_doc: Sequence[Mapping[str, Any]]) -> tuple[
        list[dict[str, Any]], dict[str, Any] | None]:
    """Documenti del RdV (con prove nel PdA) e IPdA. Le foglie dell'albero sono la
    fonte (id del documento, nome, SHA-256); lo SHA-512 viene da ``documenti``."""
    sha512 = {(r["nome_file"], r["hash_sha256"]): r["hash_sha512"] for r in righe_doc}
    if pda.albero is None:  # riversamento: nessun albero di livello 1
        return [{"identificativo": None, "nome_file": r["nome_file"],
                 "sha256": r["hash_sha256"], "sha512": r["hash_sha512"]}
                for r in righe_doc], None
    documenti: list[dict[str, Any]] = []
    ipda: dict[str, Any] | None = None
    for i, foglia in enumerate(pda.albero.foglie):
        dati = json.loads(foglia)
        prova = prove.prova_nel_pda(pda.albero, i)
        if "ipda" in dati:
            ipda = {"sha256": dati["ipda"], "prova_pda": prova, "foglia": foglia}
            continue
        documenti.append({"identificativo": dati["doc"], "nome_file": dati["file"],
                          "sha256": dati["sha256"],
                          "sha512": sha512.get((dati["file"], dati["sha256"])),
                          "prova_pda": prova, "foglia": foglia})
    return documenti, ipda


async def costruisci_rdv_pda(aip_id: str, cp: Mapping[str, Any], *,
                             emesso_il: datetime) -> tuple[bytes, dict[str, Any]]:
    """RdV v2 di un PdA nel registro rispetto al checkpoint ``cp`` (letture sole)."""
    n = int(cp["dimensione"])
    async with get_async_session() as session:
        pda = await prove.pda_per_id(session, aip_id)
        if pda is None or pda.foglia_indice is None or pda.foglia_indice >= n:
            raise LookupError(f"PdA {aip_id} non coperto dal checkpoint {n}")
        righe_doc = [dict(r) for r in (await session.execute(text(
            "SELECT nome_file, hash_sha256, hash_sha512 FROM public.documenti "
            "WHERE aip_id = :id ORDER BY ordine, id"
        ), {"id": uuid.UUID(aip_id)})).mappings().all()]
        extra = (await session.execute(text(
            # join foglia → pdv (avvisi) e → blocco d'origine dei PdA della vecchia catena
            "SELECT p.avvisi, r.conservato_il, b.numero_blocco, b.hash_blocco, "
            "a.rapporto_firme_key, a.rapporto_firme_sha256, "
            "b.versione_formato FROM public.catena_registro r "
            "JOIN public.aip a ON a.id = r.aip_id JOIN public.pdv p ON p.id = a.pdv_id "
            "LEFT JOIN public.hashchain_blocks b ON b.id = a.hashchain_block_id "
            "WHERE r.aip_id = :id"
        ), {"id": uuid.UUID(aip_id)})).mappings().one()
        prova_registro = await prove.prova_nel_registro(session, pda.foglia_indice, n)
    documenti, ipda = _documenti(pda, righe_doc)
    avvisi = extra["avvisi"] or []
    if isinstance(avvisi, str):
        avvisi = json.loads(avvisi)
    blocco = None
    if extra["numero_blocco"] is not None:
        blocco = {"numero": extra["numero_blocco"], "hash": extra["hash_blocco"],
                  "formato": extra["versione_formato"]}
    conservato_il = iso_utc(extra["conservato_il"])
    xml = costruisci_rdv(
        pdv_id=pda.pdv_id,
        pda={"aip_id": pda.aip_id, "urn": pda.urn, "impronta_manifest": pda.impronta_manifest,
             "radice_pda": pda.albero.radice_hex if pda.albero else None,
             "ente": pda.tenant_id},
        documenti=documenti, ipda=ipda, avvisi=avvisi,
        rapporto_firme=({"chiave": extra["rapporto_firme_key"],
                         "sha256": extra["rapporto_firme_sha256"]}
                        if extra["rapporto_firme_sha256"] else None),
        registro={"foglia_indice": pda.foglia_indice, "foglia": pda.foglia_dati,
                  "tipo": pda.tipo, "dimensione_albero": n,
                  "percorso": prova_registro["percorso"], "blocco_storico": blocco},
        checkpoint={"nota": archivio.nota_pubblicata(cp),
                    "chiave_verificatore": cp["chiave_verificatore"], "dimensione": n,
                    "radice": cp["radice"], "emesso_il": iso_utc(cp["emesso_il"]),
                    "stato_sigillo": cp["stato_sigillo"], "motivo_sigillo": cp["motivo_sigillo"],
                    "data_marca": iso_utc(cp["data_marca"]) if cp["data_marca"] else None,
                    "stato_testimone": cp["stato_testimone"],
                    "controfirmato_il": (iso_utc(cp["controfirmato_il"])
                                         if cp["controfirmato_il"] else None),
                    "esito_testimone": cp["esito_testimone"]},
        conservato_il=conservato_il, emesso_il=iso_utc(emesso_il),
        conservatore=settings.conservatore_nome,
    )
    contesto = {"pda": pda, "conservato_il": extra["conservato_il"], "checkpoint": n}
    return xml, contesto


async def _completa(riga: Mapping[str, Any], cp: Mapping[str, Any],
                    verificatore: nota.Verificatore, *, emesso_il: datetime) -> None:
    aip_id = str(riga["id"])
    xml, contesto = await costruisci_rdv_pda(aip_id, cp, emesso_il=emesso_il)
    problemi = verifica_rdv(xml, [verificatore])
    if problemi:
        raise RdvNonVerificato("; ".join(problemi[:5]))
    pda: prove.PdaNelRegistro = contesto["pda"]
    aip_worm = {"id": aip_id, "tenant_id": pda.tenant_id,
                "data_versamento": riga["data_versamento"],
                "anni_conservazione": riga["anni_conservazione"],
                "conservazione_permanente": riga["conservazione_permanente"]}
    bucket, chiave, contenuto, versione = await _carica_rdv(aip_worm, xml, nome=NOME_RDV_V2)
    citati = {"foglia_indice": pda.foglia_indice, "checkpoint_dimensione": int(cp["dimensione"]),
              "stato_sigillo_checkpoint": cp["stato_sigillo"],
              "stato_testimone": cp["stato_testimone"], "emessa_il": iso_utc(emesso_il)}
    if contenuto != xml:
        # Riuso di un RdV già conservato da un tentativo precedente (crash dopo la
        # scrittura nel WORM, prima del commit): il riferimento e l'evento citano
        # il checkpoint DEL DOCUMENTO CONSERVATO, non quello di questo giro (rev4
        # rilievo 12). Il documento si riverifica e non si usa se il suo
        # checkpoint è stato rifiutato dal testimone.
        problemi = verifica_rdv(contenuto, [verificatore])
        if problemi:
            raise RdvNonVerificato("RdV già conservato non valido: " + "; ".join(problemi[:5]))
        citati = riferimenti_rdv(contenuto)
        async with get_async_session() as session:
            stato = (await session.execute(text(
                "SELECT stato_testimone FROM public.catena_checkpoint WHERE dimensione = :n"
            ), {"n": citati["checkpoint_dimensione"]})).scalar_one_or_none()
        if stato == "RIFIUTATO":
            raise RdvNonVerificato(f"RdV già conservato sul checkpoint "
                                   f"{citati['checkpoint_dimensione']} rifiutato dal testimone")
        logger.warning("rdv_riusato_riferimento_dal_documento", aip_id=aip_id,
                       checkpoint=citati["checkpoint_dimensione"],
                       checkpoint_del_giro=int(cp["dimensione"]))
    n = int(citati["checkpoint_dimensione"])
    riferimento = {
        "aip_id": aip_id, "s3_bucket": bucket, "s3_key": chiave, "s3_version_id": versione,
        "impronta_sha256": hashlib.sha256(contenuto).hexdigest(), "formato": FORMATO,
        **citati,
    }
    evento = {
        "aip_id": aip_id, "pdv_id": pda.pdv_id, "tenant_id": pda.tenant_id, "urn": pda.urn,
        "hash_sha256": pda.impronta_manifest, "s3_key": riga["s3_key"],
        "s3_bucket": riga["s3_bucket"], "classe_documentale": riga["classe"],
        "data_conservazione": iso_utc(contesto["conservato_il"]),
        "num_files": riga["num_files"], "fase": "CONSERVATO",
        "pda": pda.urn, "foglia_indice": citati["foglia_indice"], "checkpoint_dimensione": n,
        "radice_pda": pda.albero.radice_hex if pda.albero else None,
    }
    async with get_async_session() as session:
        aggiornati = await session.execute(text(
            "UPDATE public.aip SET stato = 'CONSERVATO', data_conservazione = :data, "
            "updated_at = now() WHERE id = :id AND stato = 'IN_CATENA'"
        ), {"id": uuid.UUID(aip_id), "data": contesto["conservato_il"]})
        if aggiornati.rowcount != 1:
            raise RuntimeError(f"PdA {aip_id} non più IN_CATENA: completamento annullato")
        pdv = await session.execute(text(
            "UPDATE public.pdv SET stato = 'CONSERVATO', data_aggiornamento = now(), "
            "metadata_json = COALESCE(metadata_json, '{}'::jsonb) "
            "  || jsonb_build_object('rdv_definitiva', CAST(:rif AS jsonb)) "
            "WHERE id = :pdv AND tenant_id = :tenant AND stato IN ('IN_BUILD', 'CONSERVATO')"
        ), {"rif": json.dumps(riferimento), "pdv": uuid.UUID(pda.pdv_id),
            "tenant": uuid.UUID(pda.tenant_id)})
        if pdv.rowcount != 1:
            raise RuntimeError(f"PdV {pda.pdv_id} non trovato o in stato inatteso: "
                               "completamento annullato")
        await accoda_evento(session, topic=TOPIC_CONSERVATO, chiave=aip_id, payload=evento,
                            tenant_id=pda.tenant_id)
        # R13a: il RdV si sigilla dopo (lavoro dei documenti del conservatore)
        from app.registro import firme_documenti

        await firme_documenti.accoda_rdv(
            session, tenant_id=pda.tenant_id, aip_id=aip_id, bucket=bucket, chiave=chiave,
            versione=versione, contenuto=contenuto, nome=NOME_RDV_V2)
    logger.info("pda_conservato", aip_id=aip_id, pdv_id=pda.pdv_id,
                foglia_indice=pda.foglia_indice, checkpoint=n, rdv_key=chiave)


async def completa_pda_coperti(*, adesso: datetime, emesso_il: datetime | None = None,
                               ammetti_senza_testimone: bool, limite: int,
                               verificatore: nota.Verificatore) -> EsitoConservazione:
    """Un giro di completamento (vedi docstring del modulo); ``adesso`` è l'istante
    di emissione del RdV se ``emesso_il`` non è indicato (orologio del ciclo)."""
    esito = EsitoConservazione()
    async with get_async_session() as session:
        aperti = await archivio.rifiuti_aperti(session)
        if aperti:
            esito.sospeso = (f"{aperti} checkpoint rifiutati dal testimone non ancora "
                             "gestiti: completamento sospeso")
            logger.error("conservazione_sospesa_rifiuto_testimone", rifiuti_aperti=aperti)
            return esito
        utilizzabili = await archivio.checkpoint_utilizzabili(
            session, ammetti_senza_testimone=ammetti_senza_testimone)
        if utilizzabili.copertura == 0:
            return esito
        esito.checkpoint = utilizzabili.copertura
        candidati = [dict(r) for r in (await session.execute(text(
            # join aip → foglia (indice coperto), → pdv/classe (retention del RdV,
            # dati dell'evento), conteggio dei file del PdA
            "SELECT a.id::text AS id, r.indice AS foglia_indice, a.data_versamento, "
            "a.s3_key, a.s3_bucket, "
            "c.anni_conservazione, c.conservazione_permanente, c.codice AS classe, "
            "(SELECT count(*) FROM public.documenti d WHERE d.aip_id = a.id) AS num_files "
            "FROM public.aip a JOIN public.catena_registro r ON r.aip_id = a.id "
            "JOIN public.pdv p ON p.id = a.pdv_id "
            "LEFT JOIN public.classi_documentali c ON c.id = p.classe_doc_id "
            "WHERE a.stato = 'IN_CATENA' AND a.hashchain_block_id IS NULL "
            "AND r.indice < :n ORDER BY r.indice LIMIT :limite"
        ), {"n": esito.checkpoint, "limite": limite})).mappings().all()]
    if candidati:
        # R13a: con FIRMA_SENZA_FORNITORE=BLOCCA il PdA aspetta l'IPdA firmato
        from app.registro import firme_documenti

        async with get_async_session() as session:
            fermi = await firme_documenti.bloccati(session, [r["id"] for r in candidati])
        candidati = [r for r in candidati if r["id"] not in fermi]
    for riga in candidati:
        cp = utilizzabili.per_foglia(int(riga["foglia_indice"]))
        if cp is None:  # pragma: no cover — la query seleziona solo foglie coperte
            continue
        try:
            await _completa(riga, cp, verificatore, emesso_il=emesso_il or adesso)
            esito.completati.append(riga["id"])
        except Exception as exc:  # noqa: BLE001 — registrato, ripreso al giro dopo
            esito.falliti[riga["id"]] = f"{type(exc).__name__}: {exc}"[:500]
            logger.error("conservazione_pda_rinviata", aip_id=riga["id"],
                         errore=str(exc)[:300])
    return esito
