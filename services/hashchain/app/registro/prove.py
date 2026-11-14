# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prove del registro a due livelli (contratto K1 di R04), dal database.

Servizio: hashchain. Formati JSON (hash in esadecimale minuscolo):
  - inclusione nel PdA: ``{"foglia_indice", "dimensione", "percorso"}``;
  - inclusione nel registro: ``{"foglia_indice", "dimensione_albero", "percorso"}``;
  - consistenza fra checkpoint: ``{"da", "a", "percorso"}``;
  - R05 (Q3): foglie del ciclo di vita del PdA coperte dallo stesso checkpoint
    ``{"tipo", "foglia_dati", "prova_registro"}`` (scarto, annullamento,
    rettifica), così il verificatore mostra lo stato del documento e non dichiara
    mai verificato un contenuto scartato o annullato. Revisione R05 (R4):
    scarto e annullamento sempre tutti; rettifiche al più
    ``CATENA_CICLO_VITA_MAX_FOGLIE`` (le più recenti) con ``ciclo_vita_totale``
    e ``ciclo_vita_completo``: un elenco incompleto non vale «conservato».
Costi: l'albero del PdA si ricalcola dalle sue foglie (una riga, poche
decine di foglie); le prove nel registro leggono O(log n) nodi in una query.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.registro import archivio
from libs.catena import albero
from libs.catena import attestazione as attestazione_lib
from libs.catena import nota as nota_lib
from libs.catena.foglie import AlberoPda, albero_pda_da_foglie
from libs.catena.rfc6962 import esadecimale, prova_inclusione


class ProvaNonDisponibile(LookupError):
    """La prova richiesta non esiste (codice del catalogo nel campo ``codice``)."""

    def __init__(self, codice: str, messaggio: str) -> None:
        super().__init__(messaggio)
        self.codice = codice
        self.messaggio = messaggio


@dataclass(frozen=True)
class PdaNelRegistro:
    """PdA con la sua foglia del registro e (se c'è) l'albero di livello 1."""

    aip_id: str
    tenant_id: str
    urn: str
    pdv_id: str
    impronta_manifest: str
    foglia_indice: int | None
    foglia_dati: str | None
    tipo: str | None
    albero: AlberoPda | None
    #: Revisione 3 di R05 (T3): stato del PdA nel database (ELIMINAZIONE_INCOMPLETA…)
    stato: str | None = None


async def pda_per_urn(session: Any, urn: str) -> PdaNelRegistro | None:
    """PdA per URN con foglia del registro e albero (None se il PdA non esiste)."""
    riga = (await session.execute(text(
        # join aip → foglia del registro e albero del PdA (entrambi facoltativi)
        "SELECT a.id::text AS aip_id, a.tenant_id::text AS tenant_id, a.urn, "
        "a.pdv_id::text AS pdv_id, a.hash_sha256, r.indice, r.dati, r.tipo, c.foglie, "
        "a.stato::text AS stato_aip "
        "FROM public.aip a LEFT JOIN public.catena_registro r ON r.aip_id = a.id "
        "LEFT JOIN public.catena_pda c ON c.aip_id = a.id WHERE a.urn = :urn"
    ), {"urn": urn})).mappings().first()
    if riga is None:
        return None
    return _pda(dict(riga))


async def pda_per_id(session: Any, aip_id: str) -> PdaNelRegistro | None:
    riga = (await session.execute(text(
        "SELECT a.id::text AS aip_id, a.tenant_id::text AS tenant_id, a.urn, "
        "a.pdv_id::text AS pdv_id, a.hash_sha256, r.indice, r.dati, r.tipo, c.foglie, "
        "a.stato::text AS stato_aip "
        "FROM public.aip a LEFT JOIN public.catena_registro r ON r.aip_id = a.id "
        "LEFT JOIN public.catena_pda c ON c.aip_id = a.id WHERE a.id = :id"
    ), {"id": uuid.UUID(aip_id)})).mappings().first()
    return _pda(dict(riga)) if riga else None


def _pda(riga: dict[str, Any]) -> PdaNelRegistro:
    foglie = riga.get("foglie")
    if isinstance(foglie, str):
        foglie = json.loads(foglie)
    return PdaNelRegistro(
        riga["aip_id"], riga["tenant_id"], riga["urn"], riga["pdv_id"], riga["hash_sha256"],
        None if riga["indice"] is None else int(riga["indice"]), riga["dati"], riga["tipo"],
        albero_pda_da_foglie(foglie) if foglie else None,
        riga.get("stato_aip"),
    )


def prova_nel_pda(albero_pda: AlberoPda, indice: int) -> dict[str, Any]:
    """Prova d'inclusione della foglia ``indice`` nell'albero del PdA."""
    foglie = albero_pda.hash_foglie()
    return {"foglia_indice": indice, "dimensione": len(foglie),
            "percorso": esadecimale(prova_inclusione(foglie, indice))}


async def prova_nel_registro(session: Any, indice: int, dimensione: int) -> dict[str, Any]:
    """Prova d'inclusione della foglia ``indice`` nel registro di ``dimensione`` foglie."""
    nodi = await archivio.leggi_nodi(session, albero.nodi_per_inclusione(indice, dimensione))
    return {"foglia_indice": indice, "dimensione_albero": dimensione,
            "percorso": esadecimale(albero.prova_inclusione_da_nodi(indice, dimensione, nodi))}


async def prova_consistenza(session: Any, da: int, a: int) -> dict[str, Any]:
    """Prova di consistenza fra il registro di ``da`` e quello di ``a`` foglie."""
    nodi = await archivio.leggi_nodi(session, albero.nodi_per_consistenza(da, a))
    return {"da": da, "a": a,
            "percorso": esadecimale(albero.prova_consistenza_da_nodi(da, a, nodi))}


#: Foglie che invalidano il contenuto: SEMPRE tutte nella prova (revisione R05, R4)
TIPI_INVALIDANTI = ("SCARTO", "ANNULLAMENTO")


async def foglie_ciclo_vita(session: Any, aip_id: str, dimensione: int,
                            limite: int = 50) -> tuple[list[dict[str, Any]], int]:
    """Foglie del ciclo di vita del PdA con indice < ``dimensione``, in ordine di
    registro, ciascuna con la sua prova d'inclusione nel registro di ``dimensione``,
    e il loro numero TOTALE.

    Revisione R05 (R4): le foglie che invalidano il contenuto (scarto,
    annullamento) ci sono SEMPRE, tutte; ``limite`` vale solo per le rettifiche
    (le più recenti). Se l'elenco non è completo il chiamante lo dichiara
    (``ciclo_vita_completo = false``) e il verificatore non conclude
    «conservato»."""
    aip = uuid.UUID(aip_id)
    invalidanti = (await session.execute(text(
        "SELECT tipo, dati, indice FROM public.catena_foglie_interne "
        "WHERE aip_id = :aip AND indice IS NOT NULL AND indice < :n "
        "AND tipo IN ('SCARTO', 'ANNULLAMENTO') ORDER BY indice"
    ), {"aip": aip, "n": dimensione})).mappings().all()
    rettifiche = (await session.execute(text(
        "SELECT tipo, dati, indice FROM public.catena_foglie_interne "
        "WHERE aip_id = :aip AND indice IS NOT NULL AND indice < :n "
        "AND tipo NOT IN ('SCARTO', 'ANNULLAMENTO') ORDER BY indice DESC LIMIT :limite"
    ), {"aip": aip, "n": dimensione, "limite": max(0, limite)})).mappings().all()
    totale = int((await session.execute(text(
        "SELECT count(*) FROM public.catena_foglie_interne "
        "WHERE aip_id = :aip AND indice IS NOT NULL AND indice < :n"
    ), {"aip": aip, "n": dimensione})).scalar_one())
    righe = sorted([*invalidanti, *rettifiche], key=lambda r: int(r["indice"]))
    voci = [{"tipo": str(r["tipo"]).lower(), "foglia_dati": r["dati"],
             "prova_registro": await prova_nel_registro(session, int(r["indice"]), dimensione)}
            for r in righe]
    return voci, totale


async def impronta_ciclo_vita(session: Any, aip_id: str, dimensione: int) -> tuple[int, str]:
    """Revisione 2 di R05 (S8): (numero, impronta dell'elenco) di TUTTE le foglie
    del ciclo di vita del PdA con indice < ``dimensione`` (indice e hash di
    foglia RFC 6962, ``libs.catena.attestazione.impronta_elenco``)."""
    righe = (await session.execute(text(
        "SELECT indice, hash FROM public.catena_foglie_interne "
        "WHERE aip_id = :aip AND indice IS NOT NULL AND indice < :n ORDER BY indice"
    ), {"aip": uuid.UUID(aip_id), "n": dimensione})).all()
    return len(righe), attestazione_lib.impronta_elenco(
        (int(r[0]), bytes(r[1])) for r in righe)


async def attestazione_ciclo_vita(session: Any, pda: PdaNelRegistro, nota_checkpoint: str,
                                  firmatario: nota_lib.Firmatario) -> str:
    """Nota firmata dal registro che impegna numero e impronta delle foglie del
    ciclo di vita del PdA fino al checkpoint della prova (S8) e — revisione 3 di
    R05 — l'istante del rilascio (orologio del database, T2) e lo stato del PdA
    in quell'istante (T3). Il chiamante la chiede SOLO per l'ultimo checkpoint."""
    cp = nota_lib.analizza(nota_checkpoint)
    foglie, elenco = await impronta_ciclo_vita(session, pda.aip_id, cp.dimensione)
    adesso = (await session.execute(text("SELECT now()"))).scalar_one()
    return attestazione_lib.firma(attestazione_lib.Attestazione(
        origine=cp.origine, dimensione=cp.dimensione, pda=pda.urn, foglie=foglie,
        elenco=elenco, rilasciata=attestazione_lib.istante(adesso),
        stato=attestazione_lib.stato_da_aip(pda.stato)), firmatario)


async def checkpoint_di_riferimento(session: Any, dimensione: int | None) -> dict[str, Any]:
    """Checkpoint richiesto (o l'ultimo); CAT-404 se non esiste."""
    cp = await archivio.ultimo_checkpoint(session, dimensione=dimensione)
    if cp is None:
        raise ProvaNonDisponibile("CAT-404", "Nessun checkpoint emesso" if dimensione is None
                                  else f"Nessun checkpoint alla dimensione {dimensione}")
    return cp


async def prova_inclusione_documento(session: Any, pda: PdaNelRegistro, documento: str,
                                     dimensione: int | None,
                                     limite_ciclo_vita: int = 50,
                                     firmatario: nota_lib.Firmatario | None = None
                                     ) -> dict[str, Any]:
    """Risposta di ``GET /prova/inclusione`` (contratto K1). Con ``firmatario``
    (la chiave del registro) anche ``attestazione_ciclo_vita`` (revisione 2 di
    R05, S8): senza, il verificatore non può dimostrare l'assenza di scarti.
    Revisione 3 di R05: attestazione solo per l'ultimo checkpoint (T2), stato
    del PdA dichiarato (T3, ``stato_pda``)."""
    cp = await checkpoint_di_riferimento(session, dimensione)
    n = int(cp["dimensione"])
    if pda.foglia_indice is None or pda.foglia_indice >= n:
        raise ProvaNonDisponibile(
            "CAT-409", "PdA non ancora coperto dal checkpoint richiesto "
                       "(non ancora nel registro o aggiunto dopo)")
    foglia_dati, prova_pda, radice_pda = None, None, None
    if pda.albero is not None:
        i = pda.albero.indice_documento(documento)
        if i is None:
            raise ProvaNonDisponibile("CAT-404", "Documento non presente nel PdA")
        foglia_dati, prova_pda = pda.albero.foglie[i], prova_nel_pda(pda.albero, i)
        radice_pda = pda.albero.radice_hex
    else:
        # PdA in riversamento (precedente al registro a due livelli): nessun albero
        # di livello 1; il documento deve comunque appartenere al PdA.
        trovato = (await session.execute(text(
            "SELECT 1 FROM public.documenti WHERE aip_id = :aip AND nome_file = :doc LIMIT 1"
        ), {"aip": uuid.UUID(pda.aip_id), "doc": documento})).first()
        if trovato is None:
            raise ProvaNonDisponibile("CAT-404", "Documento non presente nel PdA")
    ciclo_vita, totale = await foglie_ciclo_vita(session, pda.aip_id, n, limite_ciclo_vita)
    nota_cp = archivio.nota_pubblicata(cp)
    # Revisione 3 di R05 (T2): attestazione SOLO per l'ultimo checkpoint. Una
    # prova a un checkpoint vecchio (chiesto dopo uno scarto) non porta
    # l'attestazione: il verificatore non dichiara «conservato».
    ultimo = cp if dimensione is None else await archivio.ultimo_checkpoint(session)
    ultima_dimensione = int(ultimo["dimensione"]) if ultimo else n
    attestazione, motivo = None, None
    if firmatario is None:
        motivo = "chiave del registro non configurata"
    elif ultima_dimensione != n:
        motivo = (f"rilasciata solo per l'ultimo checkpoint ({ultima_dimensione}): chiedere la "
                  "prova senza «dimensione» per lo stato attuale")
    else:
        attestazione = await attestazione_ciclo_vita(session, pda, nota_cp, firmatario)
    return {
        "pda": pda.urn, "documento": documento, "foglia_dati": foglia_dati,
        "prova_pda": prova_pda, "radice_pda": radice_pda,
        "foglia_registro_dati": pda.foglia_dati,
        "prova_registro": await prova_nel_registro(session, pda.foglia_indice, n),
        "checkpoint": nota_cp,
        "ciclo_vita": ciclo_vita,
        "ciclo_vita_totale": totale,
        "ciclo_vita_completo": len(ciclo_vita) == totale,
        "attestazione_ciclo_vita": attestazione,
        "attestazione_assente": motivo,
        "ultimo_checkpoint": ultima_dimensione,
        # T3: stato del PdA (lo stesso valore firmato nell'attestazione)
        "stato_pda": attestazione_lib.stato_da_aip(pda.stato),
    }
