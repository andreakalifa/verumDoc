# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica di un PdA: rilettura da storage di manifest, IPdA, file e RdV e
confronto con le impronte registrate.

Fonti delle impronte attese (mai il messaggio di un client, REGOLE §10.3):
  - manifest del PdA (componente ``PDA``): ``aip.hash_sha256`` e ``aip.dimensione_bytes``;
  - IPdA (``IPDA``): la foglia K1 dell'IPdA se disponibile, altrimenti il campo
    ``sincro_hash_sha256`` del manifest appena riletto e trovato integro;
  - file (``FILE``): ``documenti.hash_sha256`` e ``documenti.dimensione_bytes``;
    in più, se il manifest è integro, l'impronta del database deve coincidere con
    quella del manifest, e con la foglia K1 se disponibile;
  - COSA rileggere (rev4 rilievo 3): con le foglie K1 disponibili la fonte di
    verità è l'insieme delle foglie, non la sola tabella ``documenti`` (che non è
    append-only). Una foglia di file senza documento nel database è un'anomalia:
    il file si cerca nel manifest integro per impronta e si rilegge contro la
    foglia (DIVERSO anche se intatto: manca la riga del database), oppure è
    MANCANTE se non si trova; un documento senza foglia è DIVERSO; un manifest
    integro con un numero di file diverso dai documenti rende DIVERSO il PdA;
  - RdV definitivo (``RDV``): ``pdv.metadata_json.rdv_definitiva.impronta_sha256``.

Ogni oggetto letto ha anche il controllo della retention (COMPLIANCE, attiva,
non più breve della scadenza di conservazione). Esiti: OK, DIVERSO, MANCANTE,
RETENTION_ASSENTE, ERRORE_LETTURA (``esiti.py``). Dopo un ERRORE_LETTURA si
rileggono solo gli oggetti non letti (``da_ritentare``).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog

from app.verifica_continua import esiti, storage
from app.verifica_continua.budget import Budget
from app.verifica_continua.config import ImpostazioniVerifica
from app.verifica_continua.foglie import FogliaAttesa, FoglieAbbinate, FogliePda, abbina

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DocumentoDaVerificare:
    id: uuid.UUID
    nome_file: str
    chiave: str
    impronta: str
    dimensione: int | None
    ordine: int


@dataclass
class PdaDaVerificare:
    """Riga di programmazione presa in lease, con i dati registrati del PdA."""

    aip_id: uuid.UUID
    tenant_id: uuid.UUID
    urn: str
    stato: str
    bucket: str
    chiave_pda: str
    impronta_pda: str
    dimensione_pda: int | None
    chiave_ipda: str | None
    scadenza_conservazione: datetime | None
    rdv: dict[str, Any] | None
    documenti: list[DocumentoDaVerificare]
    lease_id: uuid.UUID
    richiesta_id: uuid.UUID | None = None
    errori_lettura_consecutivi: int = 0
    da_ritentare: dict[str, Any] | None = None
    ultima_verifica: datetime | None = None
    anomalia_dal: datetime | None = None
    #: R09 (D05): classe documentale (ciclo per classe)
    classe_id: uuid.UUID | None = None


@dataclass
class EsitoOggetto:
    componente: str
    chiave: str | None
    esito: str
    documento_id: uuid.UUID | None = None
    impronta_attesa: str | None = None
    impronta_trovata: str | None = None
    dimensione_attesa: int | None = None
    dimensione_trovata: int | None = None
    retention_modalita: str | None = None
    retention_fino_a: datetime | None = None
    foglia_attesa: str | None = None
    foglia_trovata: str | None = None
    motivi: list[str] = field(default_factory=list)

    @property
    def chiave_ritentativo(self) -> str:
        return f"FILE:{self.documento_id}" if self.componente == "FILE" else self.componente

    @property
    def motivo(self) -> str | None:
        return "; ".join(self.motivi)[:500] if self.motivi else None


@dataclass
class EsitoPda:
    pda: PdaDaVerificare
    oggetti: list[EsitoOggetto]
    ipda_attesa: str | None = None
    byte_letti: int = 0

    @property
    def peggiore(self) -> str:
        return esiti.peggiore(o.esito for o in self.oggetti)


@dataclass
class _Contesto:
    client: Any
    budget_letture: Budget
    budget_byte: Budget
    cfg: ImpostazioniVerifica
    adesso: datetime
    scadenza: datetime | None
    byte_letti: int = 0

    async def leggi(self, bucket: str, chiave: str, trattieni: int = 0) -> storage.Lettura:
        lettura = await storage.leggi_oggetto(
            self.client, bucket, chiave, budget_letture=self.budget_letture,
            budget_byte=self.budget_byte, blocco=self.cfg.blocco_lettura_byte,
            trattieni_max=trattieni)
        self.byte_letti += lettura.dimensione or 0
        return lettura


def _valuta(
    ctx: _Contesto,
    oggetto: EsitoOggetto,
    lettura: storage.Lettura,
    foglia: FogliaAttesa | None = None,
) -> EsitoOggetto:
    """Esito dell'oggetto dalla lettura e dalle attese già scritte in ``oggetto``."""
    if lettura.stato == storage.NON_TROVATO:
        oggetto.esito = esiti.MANCANTE
        oggetto.motivi.append(lettura.motivo or "oggetto assente dallo storage")
        return oggetto
    if lettura.stato == storage.ERRORE:
        oggetto.esito = esiti.ERRORE_LETTURA
        oggetto.motivi.append(lettura.motivo or "errore di lettura")
        return oggetto
    oggetto.impronta_trovata = lettura.sha256
    oggetto.dimensione_trovata = lettura.dimensione
    oggetto.retention_modalita = lettura.retention_modalita
    oggetto.retention_fino_a = lettura.retention_fino_a
    risultati = [esiti.OK]
    attesa = (oggetto.impronta_attesa or "").lower()
    if lettura.sha256 != attesa:
        risultati.append(esiti.DIVERSO)
        oggetto.motivi.append("impronta SHA-256 diversa da quella registrata")
    if oggetto.dimensione_attesa is not None and lettura.dimensione != oggetto.dimensione_attesa:
        risultati.append(esiti.DIVERSO)
        oggetto.motivi.append("dimensione diversa da quella registrata")
    motivo_retention = esiti.retention_valida(
        lettura.retention_modalita, lettura.retention_fino_a, adesso=ctx.adesso,
        scadenza_conservazione=ctx.scadenza,
        tolleranza=timedelta(seconds=ctx.cfg.retention_tolleranza_secondi))
    if motivo_retention:
        risultati.append(esiti.RETENTION_ASSENTE)
        oggetto.motivi.append(motivo_retention)
    if foglia is not None and lettura.sha256:
        oggetto.foglia_attesa = foglia.hash_hex
        oggetto.foglia_trovata = foglia.ricalcola(lettura.sha256)
        if oggetto.foglia_attesa != oggetto.foglia_trovata:
            risultati.append(esiti.DIVERSO)
            oggetto.motivi.append("foglia del registro diversa da quella ricalcolata")
        if foglia.impronta_registrata.lower() != attesa:
            risultati.append(esiti.DIVERSO)
            oggetto.motivi.append("impronta del database diversa da quella della foglia")
    oggetto.esito = esiti.peggiore(risultati)
    return oggetto


def _chiave_orfana(foglia: FogliaAttesa) -> str:
    """Chiave di ritentativo di una foglia senza documento (``FILE:<doc>``)."""
    return f"FILE:{foglia.dati.get('doc')}"


async def _verifica_orfana(ctx: _Contesto, pda: PdaDaVerificare, foglia: FogliaAttesa,
                           impronte_manifest: dict[str, str], usate: set[str],
                           manifest_integro: bool) -> EsitoOggetto:
    """Una foglia di file senza documento nel database: il file si cerca nel
    manifest integro per impronta (fra le chiavi non già di un documento) e si
    rilegge contro la foglia. Sempre un'anomalia: DIVERSO se il file c'è,
    MANCANTE se non si trova."""
    dati = foglia.dati
    attesa = str(dati.get("sha256") or "").lower()
    try:
        documento_id: uuid.UUID | None = uuid.UUID(str(dati.get("doc")))
    except ValueError:
        documento_id = None
    motivo = (f"foglia del registro del file {dati.get('file')!r} senza documento nel "
              "database (riga di «documenti» assente o alterata)")
    chiave = next((k for k, h in sorted(impronte_manifest.items())
                   if h == attesa and k not in usate), None)
    oggetto = EsitoOggetto("FILE", chiave, esiti.OK, documento_id=documento_id,
                           impronta_attesa=attesa)
    if chiave is None:
        oggetto.esito = esiti.MANCANTE
        oggetto.motivi.append(motivo)
        oggetto.motivi.append("file non trovato nel manifest" if manifest_integro
                              else "manifest non integro: file non localizzabile")
        oggetto.foglia_attesa = foglia.hash_hex
        return oggetto
    lettura = await ctx.leggi(pda.bucket, chiave)
    _valuta(ctx, oggetto, lettura, foglia)
    oggetto.esito = esiti.peggiore([oggetto.esito, esiti.DIVERSO])
    oggetto.motivi.insert(0, motivo)
    return oggetto


def _impronte_manifest(contenuto: bytes | None) -> tuple[str | None, dict[str, str]]:
    """(impronta dell'IPdA, impronte dei file per chiave) dal manifest integro."""
    if not contenuto:
        return None, {}
    try:
        manifest = json.loads(contenuto)
    except ValueError:
        return None, {}
    if not isinstance(manifest, dict):
        return None, {}
    ipda = manifest.get("sincro_hash_sha256")
    file: dict[str, str] = {}
    for doc in manifest.get("documenti") or []:
        if isinstance(doc, dict) and doc.get("s3_key") and doc.get("hash_sha256"):
            file[str(doc["s3_key"])] = str(doc["hash_sha256"]).lower()
    return (str(ipda).lower() if ipda else None), file


async def verifica_pda(
    pda: PdaDaVerificare,
    *,
    client: Any,
    budget_letture: Budget,
    budget_byte: Budget,
    cfg: ImpostazioniVerifica,
    adesso: datetime,
    foglie: FogliePda | None = None,
) -> EsitoPda:
    """Rilegge gli oggetti del PdA (tutti, o solo quelli da ritentare)."""
    ctx = _Contesto(client, budget_letture, budget_byte, cfg, adesso, pda.scadenza_conservazione)
    selezione: set[str] | None = None
    ipda_attesa: str | None = None
    if pda.da_ritentare:
        selezione = set(pda.da_ritentare.get("componenti") or [])
        ipda_attesa = pda.da_ritentare.get("ipda_attesa")

    def scelto(chiave: str) -> bool:
        return selezione is None or chiave in selezione

    documenti = [{"id": d.id, "ordine": d.ordine, "nome_file": d.nome_file} for d in pda.documenti]
    abbinate: FoglieAbbinate | None = abbina(foglie, documenti) if foglie else None
    if abbinate is not None and abbinate.ipda is not None:
        ipda_attesa = abbinate.ipda.impronta_registrata.lower()
    orfane = [f for f in (abbinate.orfane if abbinate is not None else ())
              if scelto(_chiave_orfana(f))]
    oggetti: list[EsitoOggetto] = []
    impronte_file_manifest: dict[str, str] = {}
    manifest_integro = False

    # ── manifest del PdA ──────────────────────────────────────────────────
    serve_manifest = scelto("PDA") or bool(orfane) or (
        scelto("IPDA") and ipda_attesa is None and pda.chiave_ipda)
    oggetto_pda: EsitoOggetto | None = None
    if serve_manifest:
        oggetto_pda = EsitoOggetto("PDA", pda.chiave_pda, esiti.OK,
                                   impronta_attesa=pda.impronta_pda,
                                   dimensione_attesa=pda.dimensione_pda)
        lettura = await ctx.leggi(pda.bucket, pda.chiave_pda, trattieni=cfg.manifest_max_byte)
        _valuta(ctx, oggetto_pda, lettura)
        if abbinate is not None and abbinate.radice_coerente is False:
            oggetto_pda.esito = esiti.peggiore([oggetto_pda.esito, esiti.DIVERSO])
            oggetto_pda.motivi.append(
                "foglie salvate non coerenti con la radice registrata del PdA")
        if lettura.stato == storage.LETTO and lettura.sha256 == pda.impronta_pda.lower():
            ipda_manifest, impronte_file_manifest = _impronte_manifest(lettura.contenuto)
            manifest_integro = True
            if ipda_attesa is None:
                ipda_attesa = ipda_manifest
            if selezione is None and impronte_file_manifest \
                    and len(impronte_file_manifest) != len(pda.documenti):
                oggetto_pda.esito = esiti.peggiore([oggetto_pda.esito, esiti.DIVERSO])
                oggetto_pda.motivi.append(
                    f"il manifest elenca {len(impronte_file_manifest)} file, il database "
                    f"{len(pda.documenti)} documenti")
        if scelto("PDA"):
            oggetti.append(oggetto_pda)

    # ── IPdA ──────────────────────────────────────────────────────────────
    if pda.chiave_ipda and scelto("IPDA"):
        if ipda_attesa is None:
            nota = "IPdA non verificata: impronta attesa non disponibile (manifest non integro)"
            if oggetto_pda is not None and scelto("PDA"):
                oggetto_pda.motivi.append(nota)
            logger.warning("verifica_ipda_senza_attesa", aip_id=str(pda.aip_id))
        else:
            oggetto = EsitoOggetto("IPDA", pda.chiave_ipda, esiti.OK, impronta_attesa=ipda_attesa)
            lettura = await ctx.leggi(pda.bucket, pda.chiave_ipda)
            oggetti.append(_valuta(ctx, oggetto, lettura,
                                   abbinate.ipda if abbinate is not None else None))

    # ── file ──────────────────────────────────────────────────────────────
    for doc in sorted(pda.documenti, key=lambda d: (d.ordine, str(d.id))):
        if not scelto(f"FILE:{doc.id}"):
            continue
        oggetto = EsitoOggetto("FILE", doc.chiave, esiti.OK, documento_id=doc.id,
                               impronta_attesa=doc.impronta, dimensione_attesa=doc.dimensione)
        lettura = await ctx.leggi(pda.bucket, doc.chiave)
        foglia = abbinate.per_documento.get(doc.id) if abbinate is not None else None
        _valuta(ctx, oggetto, lettura, foglia)
        da_manifest = impronte_file_manifest.get(doc.chiave)
        if da_manifest is not None and da_manifest != doc.impronta.lower():
            oggetto.esito = esiti.peggiore([oggetto.esito, esiti.DIVERSO])
            oggetto.motivi.append("impronta del database diversa da quella del manifest")
        if abbinate is not None and foglia is None:
            oggetto.esito = esiti.peggiore([oggetto.esito, esiti.DIVERSO])
            oggetto.motivi.append("documento del database senza foglia corrispondente nel "
                                  "registro (id o nome del file diversi)")
        oggetti.append(oggetto)

    # ── foglie senza documento nel database (rev4 rilievo 3) ────────────
    usate = {d.chiave for d in pda.documenti}
    for foglia in orfane:
        oggetti.append(await _verifica_orfana(ctx, pda, foglia, impronte_file_manifest,
                                              usate, manifest_integro))

    # ── RdV definitivo ────────────────────────────────────────────────────
    if scelto("RDV"):
        rdv = pda.rdv or {}
        if rdv.get("s3_key") and rdv.get("impronta_sha256"):
            oggetto = EsitoOggetto("RDV", str(rdv["s3_key"]), esiti.OK,
                                   impronta_attesa=str(rdv["impronta_sha256"]))
            lettura = await ctx.leggi(str(rdv.get("s3_bucket") or pda.bucket), str(rdv["s3_key"]))
            oggetti.append(_valuta(ctx, oggetto, lettura))
        elif pda.stato == "CONSERVATO":
            oggetti.append(EsitoOggetto(
                "RDV", None, esiti.MANCANTE,
                motivi=["PdA conservato senza riferimento al RdV definitivo"]))

    return EsitoPda(pda, oggetti, ipda_attesa=ipda_attesa, byte_letti=ctx.byte_letti)
