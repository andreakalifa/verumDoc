# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Sequenziatore del registro globale (livello 2): aggiunge i PdA in coda come
foglie, in ordine, con indice progressivo (contratto K1 di R04).

Servizio: hashchain. Riferimenti: REGOLE_SVILUPPO §12.8 (ordine totale: la
``sequenza`` della coda), §12.9 (lease: un solo sequenziatore attivo), §10.1
(append-only), §12.6 (controlla poi crea sotto lock).

Un lotto è UNA transazione:
  1. blocca ``catena_stato`` e verifica il lease (dimensione n letta sotto lock:
     due processi non possono mai assegnare lo stesso indice);
  2. legge in ordine di ``sequenza`` fino a ``limite`` PdA in coda, con il loro
     albero (``catena_pda``) o, per i PdA precedenti al registro a due livelli,
     il blocco d'origine;
  3. per ogni PdA costruisce la foglia (PdA → ``dati_foglia_registro``; senza
     albero → foglia di riversamento) e ne calcola l'hash;
  4. legge la frontiera dell'albero (≤ log n nodi), calcola i nodi nuovi e li
     inserisce insieme alle foglie (INSERT in blocco);
  5. aggiorna la dimensione, toglie i PdA dalla coda e porta a IN_CATENA quelli
     ancora SU_STORAGE (mai i PdA della vecchia catena, già IN_CATENA o CONSERVATO).
Un errore annulla tutto il lotto: la coda resta com'era.

R05 (contratto Q3): una riga in coda può essere anche una FOGLIA INTERNA
(``catena_foglie_interne``: scarto, annullamento, rettifica, audit) con il JSON
canonico e l'hash già fissati alla richiesta. Entra nello stesso ordine totale
(``sequenza``) e nello stesso lotto dei PdA; il sequenziatore ne scrive la foglia
(senza PdA collegato in ``catena_registro``) e l'indice nella richiesta. Una foglia
interna già nel registro (indice presente, es. dopo una ricostruzione dal WORM) si
toglie dalla coda senza riscriverla.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.registro import archivio
from libs.catena import albero
from libs.catena.foglie import dati_foglia_registro, dati_foglia_riversamento
from libs.catena.rfc6962 import hash_foglia
from libs.shared.db import get_async_session

logger = structlog.get_logger(__name__)

TIPO_PDA = "PDA"
TIPO_RIVERSAMENTO = "RIVERSAMENTO"


class FogliaInternaCorrotta(RuntimeError):
    """L'hash salvato di una foglia interna non corrisponde ai suoi dati."""


@dataclass(frozen=True)
class EsitoLotto:
    """Foglie aggiunte e dimensione del registro dopo il lotto."""

    aggiunte: int
    dimensione: int
    gia_presenti: int = 0


def foglia_per(riga: dict[str, Any], conservato_il: datetime) -> tuple[str, bytes]:
    """(tipo, dati della foglia) per un PdA in coda (funzione pura)."""
    urn, ente = str(riga["urn"]), str(riga["tenant_id"])
    if riga.get("radice"):
        return TIPO_PDA, dati_foglia_registro(urn, str(riga["radice"]), ente, conservato_il)
    blocco = None
    if riga.get("numero_blocco") is not None:
        blocco = {"numero": int(riga["numero_blocco"]), "hash": str(riga["hash_blocco"]),
                  "formato": int(riga["versione_formato"] or 1)}
    return TIPO_RIVERSAMENTO, dati_foglia_riversamento(
        urn, str(riga["hash_sha256"]), ente, conservato_il, blocco)


async def aggiungi_lotto(*, detentore: str, adesso: datetime, limite: int) -> EsitoLotto:
    """Aggiunge al registro fino a ``limite`` PdA in coda (vedi docstring del modulo)."""
    async with get_async_session() as session:
        stato = await archivio.stato_con_lease(session, detentore, adesso)
        righe = [dict(r) for r in (await session.execute(text(
            # join coda → PdA (urn, ente, stato), albero del PdA e blocco d'origine
            # (PdA della vecchia catena): tutto ciò che serve alla foglia
            "SELECT q.sequenza, q.aip_id, q.foglia_interna_id, a.urn, a.tenant_id, "
            "a.stato::text AS stato, a.hash_sha256, c.radice, b.numero_blocco, b.hash_blocco, "
            "b.versione_formato, (r.indice IS NOT NULL) AS gia_nel_registro, "
            "f.tipo AS tipo_interno, f.dati AS dati_interni, f.hash AS hash_interno, "
            "f.tenant_id AS ente_interno, (f.indice IS NOT NULL) AS interna_gia_nel_registro "
            "FROM public.catena_coda q LEFT JOIN public.aip a ON a.id = q.aip_id "
            "LEFT JOIN public.catena_pda c ON c.aip_id = q.aip_id "
            "LEFT JOIN public.hashchain_blocks b ON b.id = a.hashchain_block_id "
            "LEFT JOIN public.catena_registro r ON r.aip_id = q.aip_id "
            "LEFT JOIN public.catena_foglie_interne f ON f.id = q.foglia_interna_id "
            "ORDER BY q.sequenza LIMIT :limite FOR UPDATE OF q"
        ), {"limite": limite})).mappings().all()]
        if not righe:
            return EsitoLotto(0, stato.dimensione)

        nuove = [r for r in righe if not r["gia_nel_registro"]
                 and not r["interna_gia_nel_registro"]]
        n = stato.dimensione
        foglie: list[dict[str, Any]] = []
        for posizione, riga in enumerate(nuove):
            if riga["foglia_interna_id"] is not None:
                dati = str(riga["dati_interni"]).encode("utf-8")
                if hash_foglia(dati) != bytes(riga["hash_interno"]):
                    raise FogliaInternaCorrotta(f"foglia interna {riga['foglia_interna_id']}")
                foglie.append({"indice": n + posizione, "aip_id": None,
                               "tenant_id": riga["ente_interno"], "tipo": riga["tipo_interno"],
                               "dati": dati.decode("utf-8"), "hash": bytes(riga["hash_interno"]),
                               "conservato_il": adesso,
                               "interna": riga["foglia_interna_id"]})
                continue
            tipo, dati = foglia_per(riga, adesso)
            foglie.append({"indice": n + posizione, "aip_id": riga["aip_id"],
                           "tenant_id": riga["tenant_id"], "tipo": tipo,
                           "dati": dati.decode("utf-8"), "hash": hash_foglia(dati),
                           "conservato_il": adesso})
        if foglie:
            frontiera = await archivio.leggi_nodi(session, albero.coordinate_frontiera(n))
            nodi = albero.aggiungi_foglie(n, frontiera, [f["hash"] for f in foglie])
            await archivio.scrivi_nodi(session, nodi)
            await archivio.scrivi_foglie(session, foglie)
            await session.execute(text(
                "UPDATE public.catena_stato SET dimensione = :n, aggiornato_il = :adesso "
                "WHERE id = 1"
            ), {"n": n + len(foglie), "adesso": adesso})
            await session.execute(text(
                "UPDATE public.aip SET stato = 'IN_CATENA', updated_at = now() "
                "WHERE id = ANY(CAST(:ids AS uuid[])) AND stato = 'SU_STORAGE' "
                "AND hashchain_block_id IS NULL"
            ), {"ids": [uuid.UUID(str(f["aip_id"])) for f in foglie if f["aip_id"] is not None]})
            interne = [f for f in foglie if f.get("interna") is not None]
            if interne:
                await archivio.collega_foglie_interne(
                    session, [(f["interna"], f["indice"]) for f in interne], adesso)
        await session.execute(text(
            "DELETE FROM public.catena_coda WHERE sequenza = ANY(CAST(:seq AS bigint[]))"
        ), {"seq": [int(r["sequenza"]) for r in righe]})

    gia = len(righe) - len(nuove)
    if gia:
        logger.warning("registro_pda_gia_presenti_tolti_dalla_coda", quanti=gia)
    if foglie:
        logger.info("registro_foglie_aggiunte", da=n, a=n + len(foglie),
                    riversamenti=sum(1 for f in foglie if f["tipo"] == TIPO_RIVERSAMENTO),
                    interne=sum(1 for f in foglie if f.get("interna") is not None))
    return EsitoLotto(len(foglie), n + len(foglie), gia)
