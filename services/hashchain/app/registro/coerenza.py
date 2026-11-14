# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Coerenza del registro con ciò che è GIÀ PUBBLICATO, e blocchi persistenti
(R04 terzo giro, T4; migrazione 20261023_034).

Dopo un ripristino del database da backup il registro può essere indietro
rispetto a:
  - i segmenti di foglie già esportati nel WORM (``registro/foglie/<da>-<a>.jsonl``),
    fine dell'ultimo = ``X``;
  - l'ultimo checkpoint emesso, scritto nel WORM prima di essere pubblicato
    (``registro/checkpoint/<n>.txt``) = ``N``;
  - l'ultima dimensione controfirmata dal testimone = ``T``.
Se il sequenziatore ripartisse con ``D < max(N, X, T)`` rimetterebbe in registro la
coda in un ordine qualsiasi: le foglie già pubblicate cambierebbero posto e i
clienti riceverebbero checkpoint firmati dalla stessa chiave e inconsistenti con
quelli che hanno (biforcazione firmata). Quindi, all'avvio del ciclo e quando si
toglie ``CATENA_REGISTRO_SOSPESO``, ``verifica`` misura ``D, N, X, T``; se il
registro è indietro o diverge da quanto pubblicato si apre un BLOCCO persistente
(``catena_blocchi_registro``) e il registro non scrive finché il controllo non
passa (dopo un ripristino point-in-time più recente: runbook
``ripristino_registro_da_backup.md``).

Scelta: FERMO CON ALLARME, non ricostruzione automatica dal WORM. Ricostruire
richiede di ricreare foglie, nodi interni dell'albero e collegamento PdA↔foglia
che il database ripristinato ha perso; una ricostruzione sbagliata trasforma un
registro fermo in una biforcazione firmata, mentre un registro fermo non perde
nulla (i PdA attendono in coda). Il comando versionato di ricostruzione, con
modalità di prova e obiettivo ``max(N, X)``, è registrato per R05.

Come si trovano ``X`` e ``N`` senza elencare tutto il WORM: i segmenti hanno nomi
a 12 cifre (ordine lessicografico = numerico), si elencano solo quelli che
iniziano dalla fine dell'ultima esportazione nota al database (``E``). Ogni
emissione di checkpoint esporta PRIMA le foglie fino alla sua dimensione, quindi
ogni checkpoint ``n`` ha un segmento che finisce in ``n``: ``N`` si cerca solo fra
le fine dei segmenti oltre l'ultimo checkpoint del database (``C``), più ``E``.

Contenuti: i segmenti oltre ``E`` coperti dal database si confrontano riga per riga
con le foglie del database; il checkpoint ``N`` coperto dal database si confronta
con la radice calcolata dai nodi. Una differenza è ``REGISTRO_DIVERGENTE``.

``E`` stesso (R05, VD-C-118): il database potrebbe dichiarare esportate foglie che
nel WORM NON ci sono (database di un altro ambiente o bucket, ripristino
incoerente, esportazione registrata e mai scritta). Si verifica quindi che i
segmenti nel WORM vadano, contigui e senza sovrapposizioni, dall'ultimo
checkpoint del database prima di ``E`` (``L``: ogni checkpoint ha un segmento che
finisce nella sua dimensione) esattamente fino a ``E``, e che il loro contenuto
coincida con le foglie del database. Altrimenti ``REGISTRO_DIVERGENTE``: il
registro non scrive (un checkpoint successivo pubblicherebbe foglie che il WORM
non contiene, e la ricostruzione dal WORM non sarebbe più possibile).

Permessi (quarto giro, F1): il ruolo IAM della hashchain può elencare il bucket
dei PdA SOLO sotto ``registro/`` (``s3:ListBucket`` con condizione ``s3:prefix``).
Con quel permesso una GET su una chiave assente risponde comunque 403, non 404:
l'esistenza di una chiave si verifica quindi con un ELENCO del suo prefisso
esatto (``_esiste``), mai con una lettura, e ogni 403 rende il controllo «non
verificabile». Un controllo che non si può fare apre il blocco esplicito
``COERENZA_NON_VERIFICABILE``: subito per un errore di permesso o di
credenziali, dopo ``ERRORI_PRIMA_DEL_BLOCCO`` tentativi per gli altri errori.

Blocchi (al più uno aperto per causa): ``REGISTRO_INDIETRO``,
``REGISTRO_DIVERGENTE`` e ``COERENZA_NON_VERIFICABILE`` si chiudono da soli quando
il controllo passa;
``WORM_CONTENUTO_DIVERSO`` (scrittura WORM con lo stesso nome e contenuto diverso)
ferma le scritture invece di ritentare a ogni giro e si chiude con il comando
dell'operatore dopo l'indagine (``python -m app.registro.operatore blocchi``).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from app.registro import archivio, archivio_worm
from app.registro.metriche import metriche
from libs.catena import albero, nota
from libs.shared.db import get_async_session
from libs.shared.storage import worm

logger = structlog.get_logger(__name__)

CAUSA_INDIETRO = "REGISTRO_INDIETRO"
CAUSA_DIVERGENTE = "REGISTRO_DIVERGENTE"
CAUSA_WORM_DIVERSO = "WORM_CONTENUTO_DIVERSO"
#: Quarto giro (F1): il controllo non si può fare (permessi, WORM irraggiungibile)
CAUSA_NON_VERIFICABILE = "COERENZA_NON_VERIFICABILE"
#: Cause che si chiudono da sole quando il controllo di coerenza passa
CAUSE_DEL_CONTROLLO = (CAUSA_INDIETRO, CAUSA_DIVERGENTE, CAUSA_NON_VERIFICABILE)
#: Errori consecutivi (non di permesso) del controllo prima del blocco esplicito:
#: uno sbalzo di rete non blocca, un errore che persiste sì (≈ 3 minuti)
ERRORI_PRIMA_DEL_BLOCCO = 3
#: Codici S3 di permesso o credenziali: errore di CONFIGURAZIONE, blocco subito
_CODICI_PERMESSO = frozenset({"AccessDenied", "403", "Forbidden", "AllAccessDisabled",
                              "InvalidAccessKeyId", "SignatureDoesNotMatch",
                              "ExpiredToken", "InvalidToken"})


def errore_di_permesso(exc: BaseException) -> bool:
    """True se l'errore è di permessi o credenziali (es. ruolo IAM senza
    ``s3:ListBucket``): non passa da solo, il blocco si apre subito."""
    risposta = getattr(exc, "response", None)
    if isinstance(risposta, dict):
        return str(risposta.get("Error", {}).get("Code", "")) in _CODICI_PERMESSO
    return type(exc).__name__ in ("NoCredentialsError", "PartialCredentialsError")

_SEGMENTO = re.compile(r"registro/foglie/(\d{12})-(\d{12})\.jsonl$")


@dataclass
class EsitoCoerenza:
    """Misure del controllo (vedi modulo)."""

    dimensione_db: int            # D
    esportate_db: int             # E
    checkpoint_db: int            # C
    fine_segmenti_worm: int       # X
    checkpoint_worm: int          # N
    testimone: int | None         # T (None: testimone non configurato o non letto)
    divergenze: list[str] = field(default_factory=list)

    @property
    def soglia(self) -> int:
        return max(self.fine_segmenti_worm, self.checkpoint_worm, self.testimone or 0)

    @property
    def indietro(self) -> bool:
        return self.dimensione_db < self.soglia

    @property
    def coerente(self) -> bool:
        return not self.indietro and not self.divergenze

    def come_dict(self) -> dict[str, Any]:
        return {**asdict(self), "soglia": self.soglia}


# ── Lettura del WORM ─────────────────────────────────────────────────────────

def _client_e_bucket() -> tuple[Any, str]:
    from app.services.block_sealer import _client

    return _client(), worm.bucket_conservativo(settings)


def _segmenti_oltre(client: Any, bucket: str, esportate: int) -> list[tuple[int, int, str]]:
    """Segmenti che iniziano da ``esportate`` in poi: (da, a, chiave), in ordine."""
    from botocore.exceptions import ClientError

    risultato: list[tuple[int, int, str]] = []
    pagine = client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{archivio_worm.PREFISSO}/foglie/",
        StartAfter=f"{archivio_worm.PREFISSO}/foglie/{esportate:012d}")
    try:
        for pagina in pagine:
            for oggetto in pagina.get("Contents", []):
                m = _SEGMENTO.search(oggetto["Key"])
                if m and int(m.group(1)) >= esportate:
                    risultato.append((int(m.group(1)), int(m.group(2)), oggetto["Key"]))
    except ClientError as exc:
        # Bucket inesistente = nulla di pubblicato (una configurazione sbagliata
        # fallisce comunque, in modo esplicito, alla prima scrittura nel WORM)
        if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
            raise
    return sorted(risultato)


def _esiste(client: Any, bucket: str, chiave: str) -> bool:
    """Esistenza di una chiave con un ELENCO del suo prefisso esatto, mai con una
    lettura: senza ``s3:ListBucket`` incondizionato S3 risponde 403 (non 404) a
    una GET su una chiave assente, e un 403 non è un'assenza (quarto giro, F1).
    Il ruolo della hashchain può elencare solo sotto ``registro/``."""
    risposta = client.list_objects_v2(Bucket=bucket, Prefix=chiave, MaxKeys=1)
    return any(o.get("Key") == chiave for o in risposta.get("Contents", []))


@dataclass
class LetturaWorm:
    """Ciò che il controllo legge dal WORM (vedi ``_leggi_worm``)."""

    segmenti: list[tuple[int, int, bytes]]        # segmenti che finiscono oltre E
    checkpoint_worm: int                          # N
    testo_checkpoint: str | None                  # nota di N se oltre C
    esportati: list[tuple[int, int, bytes]] = field(default_factory=list)  # [L, E)


def _leggi_worm(esportate: int, checkpoint_db: int, base: int = 0) -> LetturaWorm:
    """Segmenti da ``base`` (L) in poi con il contenuto, N e il testo del
    checkpoint N se oltre C. Qualunque errore S3 (403 compreso) si propaga:
    controllo NON verificabile."""
    client, bucket = _client_e_bucket()
    tutti = [(da, a, worm.leggi_oggetto(client, bucket, chiave))
             for da, a, chiave in _segmenti_oltre(client, bucket, min(base, esportate))]
    # oltre E: anche un segmento che inizia prima di E e finisce dopo (riscritto)
    segmenti = [s for s in tutti if s[1] > esportate]
    esportati = [s for s in tutti if s[0] < esportate]
    candidati = sorted({a for _, a, _ in segmenti} | {esportate}, reverse=True)
    for n in candidati:
        if n <= checkpoint_db:
            break
        chiave = archivio_worm.chiave_checkpoint(n)
        if _esiste(client, bucket, chiave):
            return LetturaWorm(segmenti, n, worm.leggi_oggetto(client, bucket, chiave)
                               .decode("utf-8"), esportati)
    return LetturaWorm(segmenti, checkpoint_db, None, esportati)


def divergenze_esportazione(base: int, esportate: int,
                            esportati: list[tuple[int, int, bytes]]) -> list[str]:
    """VD-C-118: i segmenti nel WORM devono coprire [L, E) senza buchi (funzione
    pura). Un segmento riscritto dopo un errore dell'aggiornamento di E riparte
    dallo stesso punto e si sovrappone al precedente: ammesso (il contenuto delle
    righe in comune lo confronta ``verifica``)."""
    if esportate <= 0:
        return []
    posizione = base
    for da, a, _ in sorted(esportati):
        if posizione >= esportate:
            break
        if da > posizione:
            return [f"esportazione: il database dichiara {esportate} foglie esportate, ma nel "
                    f"WORM dopo la foglia {posizione} il segmento successivo inizia da {da}"]
        posizione = max(posizione, a)
    if posizione < esportate:
        return [f"esportazione: il database dichiara {esportate} foglie esportate, ma i "
                f"segmenti nel WORM da {base} finiscono in {posizione}"]
    return []


# ── Controllo ───────────────────────────────────────────────────────────────

async def verifica(*, testimone: Any = None, origine: str = "") -> EsitoCoerenza:
    """Misura D, E, C, X, N, T e confronta i contenuti già pubblicati (vedi modulo)."""
    async with get_async_session() as session:
        stato = await archivio.leggi_stato(session)
        ultimo = await archivio.ultimo_checkpoint(session)
        d, e = stato.dimensione, stato.worm_foglie_esportate
        # L: ultimo checkpoint del database PRIMA di E (un segmento finisce lì)
        base = (await session.execute(text(
            "SELECT dimensione FROM public.catena_checkpoint WHERE dimensione < :e "
            "ORDER BY dimensione DESC LIMIT 1"), {"e": e})).scalar_one_or_none() or 0
    c = int(ultimo["dimensione"]) if ultimo is not None else 0
    letto = await asyncio.to_thread(_leggi_worm, e, c, int(base))
    segmenti, n, testo_n = letto.segmenti, letto.checkpoint_worm, letto.testo_checkpoint
    x = max([e] + [a for _, a, _ in segmenti])
    t = None
    if testimone is not None:
        t = await testimone.dimensione_controfirmata(origine)
        if t is None:
            logger.warning("coerenza_testimone_non_letto",
                           azione="controllo fatto con WORM e database soltanto")
    esito = EsitoCoerenza(d, e, c, x, n, t)
    # VD-C-118: E dichiarato dal database = segmenti davvero presenti nel WORM
    esito.divergenze.extend(divergenze_esportazione(int(base), e, letto.esportati))
    # Contenuti già pubblicati coperti dal database: devono coincidere
    for da, a, contenuto in sorted({(x[0], x[1]): x for x in
                                    [*letto.esportati, *segmenti]}.values()):
        fine = min(a, d)
        if fine <= da:
            continue
        async with get_async_session() as session:
            nostre = await archivio.leggi_dati_foglie(session, da, fine)
        loro = contenuto.decode("utf-8").splitlines()[: fine - da]
        if loro != nostre:
            esito.divergenze.append(f"segmento {da}-{a}: foglie diverse da quelle del database")
    if testo_n is not None and n <= d:
        async with get_async_session() as session:
            nodi = await archivio.leggi_nodi(session, albero.nodi_per_radice(n))
        corpo, _ = nota.separa(testo_n)
        if nota.analizza(corpo).radice != albero.radice_da_nodi(n, nodi):
            esito.divergenze.append(f"checkpoint {n}: radice diversa da quella del database")
    return esito


# ── Blocchi persistenti ─────────────────────────────────────────────────────

_APRI = text(
    "INSERT INTO public.catena_blocchi_registro AS b (causa, aperto_il, ultimo_il, "
    "  occorrenze, dettagli) VALUES (:causa, :adesso, :adesso, 1, CAST(:dettagli AS jsonb)) "
    "ON CONFLICT (causa) WHERE chiuso_il IS NULL DO UPDATE SET "
    "  occorrenze = b.occorrenze + 1, ultimo_il = EXCLUDED.ultimo_il, "
    "  dettagli = EXCLUDED.dettagli RETURNING id, occorrenze"
)


async def apri_blocco(causa: str, dettagli: dict[str, Any], adesso: datetime) -> int:
    async with get_async_session() as session:
        riga = (await session.execute(_APRI, {
            "causa": causa, "adesso": adesso,
            "dettagli": json.dumps(dettagli, default=str)})).one()
    if int(riga.occorrenze) == 1:
        logger.error("registro_bloccato", causa=causa, blocco=int(riga.id),
                     dettagli=dettagli, azione="docs/runbooks/ripristino_registro_da_backup.md")
    return int(riga.id)


async def blocchi_aperti(session: Any) -> list[dict[str, Any]]:
    righe = (await session.execute(text(
        "SELECT id, causa, aperto_il, ultimo_il, occorrenze, dettagli "
        "FROM public.catena_blocchi_registro WHERE chiuso_il IS NULL ORDER BY id"
    ))).mappings().all()
    return [dict(r) for r in righe]


async def chiudi_blocco(session: Any, *, cause: tuple[str, ...] | None = None,
                        blocco: int | None = None, operatore: str, motivo: str,
                        adesso: datetime) -> list[dict[str, Any]]:
    """Chiude blocchi aperti (per causa o per id) con una voce in ``audit_global``."""
    from libs.shared.security.audit import registra_audit

    chiusi = (await session.execute(text(
        "UPDATE public.catena_blocchi_registro SET chiuso_il = :adesso, chiuso_da = :operatore, "
        "nota = :motivo WHERE chiuso_il IS NULL "
        "AND (CAST(:cause AS varchar[]) IS NULL OR causa = ANY(CAST(:cause AS varchar[]))) "
        "AND (CAST(:blocco AS bigint) IS NULL OR id = CAST(:blocco AS bigint)) "
        "RETURNING id, causa, occorrenze"
    ), {"adesso": adesso, "operatore": operatore[:200], "motivo": motivo,
        "cause": list(cause) if cause is not None else None, "blocco": blocco})).mappings().all()
    if chiusi:
        await registra_audit(session, evento="catena.blocco_registro_chiuso", dettagli={
            "blocchi": [dict(c) for c in chiusi], "operatore": operatore[:200],
            "motivo": motivo[:2000]})
    return [dict(c) for c in chiusi]


async def controlla_blocchi() -> list[dict[str, Any]]:
    """Allarme persistente: metrica ``verumdoc_catena_registro_bloccato`` e log
    ``registro_bloccato_aperto`` a OGNI giro finché un blocco è aperto."""
    async with get_async_session() as session:
        aperti = await blocchi_aperti(session)
    metriche().registro_bloccato.set(len(aperti))
    if aperti:
        logger.error("registro_bloccato_aperto",
                     blocchi=[{"id": b["id"], "causa": b["causa"],
                               "occorrenze": b["occorrenze"]} for b in aperti],
                     azione="il registro non scrive: runbook ripristino_registro_da_backup.md")
    return aperti


async def applica_controllo(esito: EsitoCoerenza, adesso: datetime) -> bool:
    """Apre o chiude i blocchi del controllo; True se il registro può scrivere."""
    if esito.indietro:
        await apri_blocco(CAUSA_INDIETRO, esito.come_dict(), adesso)
    if esito.divergenze:
        await apri_blocco(CAUSA_DIVERGENTE, esito.come_dict(), adesso)
    metriche().registro_soglia.set(esito.soglia)
    if not esito.coerente:
        return False
    async with get_async_session() as session:
        chiusi = await chiudi_blocco(session, cause=CAUSE_DEL_CONTROLLO,
                                     operatore="registro (controllo di coerenza)",
                                     motivo="controllo di coerenza superato: "
                                            + json.dumps(esito.come_dict()), adesso=adesso)
    if chiusi:
        logger.warning("registro_sbloccato", blocchi=chiusi, misure=esito.come_dict())
    logger.info("registro_coerente", **esito.come_dict())
    return True
