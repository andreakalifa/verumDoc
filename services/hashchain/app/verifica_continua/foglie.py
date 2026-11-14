# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Punto d'estensione: confronto con le foglie del registro a due livelli (contratto K1).

Quando la catena (r04-catena) salva i dati delle foglie di livello 1 di ogni PdA
(JSON canonico, ``libs.catena.foglie``) la verifica continua confronta anche la
foglia: sostituisce nell'oggetto della foglia l'impronta TROVATA sullo storage
(campo ``sha256`` per un file, ``ipda`` per l'IPdA), ricalcola
``SHA-256(0x00 || JSON canonico)`` e lo confronta con l'hash della foglia salvata.
Un file alterato e un'impronta del database riscritta insieme al file non
passano: la foglia è ancorata alla radice del PdA, la radice al registro, il
registro ai checkpoint firmati e controfirmati.

Inoltre: l'impronta attesa dell'IPdA si prende dalla sua foglia (invece che dal
manifest) e, se la fonte dà anche la radice registrata del PdA, si controlla che
le foglie salvate la ricompongano.

La fonte dei dati è iniettabile (``FonteFoglie``):
  - ``FonteFoglieCatena`` (usata dal processo ``python -m app.verifica_continua``):
    legge ``catena_pda`` (foglie e radice del PdA scritte dall'aip-builder,
    migrazione 20261023_025) e, se il PdA è già nel registro globale, la
    ``radice_pda`` della sua foglia in ``catena_registro`` (la radice ancorata ai
    checkpoint). PdA conservati prima del registro a due livelli (foglia di
    «riversamento», nessun ``catena_pda``): nessun confronto di foglia, restano
    il database e il manifest;
  - ``FonteFoglieAssente``: nessun confronto (colonne ``foglia_*`` vuote, stato
    dichiarato nel riepilogo: ``confronto_foglia = "NON_DISPONIBILE"``).
L'integrità della foglia del registro rispetto ai checkpoint firmati la verifica
la catena stessa (prove d'inclusione e di consistenza), non questo modulo.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

from libs.catena.canonico import json_canonico
from libs.catena.foglie import albero_pda_da_foglie
from libs.catena.rfc6962 import hash_foglia


@dataclass(frozen=True)
class FogliePda:
    """Dati delle foglie di livello 1 salvati per un PdA (testi JSON canonici, in
    ordine K1: componenti per ordine nell'IPdA, poi la foglia dell'IPdA) e, se
    nota, la radice registrata del PdA (esadecimale)."""

    foglie: tuple[str, ...]
    radice_pda: str | None = None


class FonteFoglie(Protocol):
    """Chi sa dove la catena salva le foglie dei PdA."""

    nome: str

    async def foglie(self, aip_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, FogliePda]:
        """Foglie salvate per i PdA richiesti (assenti dalla mappa = non disponibili)."""
        ...


class FonteFoglieAssente:
    """Nessuna fonte configurata: il confronto con la foglia non si esegue."""

    nome = "NON_DISPONIBILE"

    async def foglie(self, aip_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, FogliePda]:
        return {}


class FonteFoglieCatena:
    """Foglie dalla catena: ``catena_pda`` (livello 1) e ``catena_registro`` (livello 2)."""

    nome = "CATENA"

    async def foglie(self, aip_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, FogliePda]:
        from libs.shared.db import get_async_session

        if not aip_ids:
            return {}
        async with get_async_session() as session:
            righe = (await session.execute(text(
                # albero del PdA → sua foglia nel registro globale (se già sequenziato)
                "SELECT c.aip_id, c.foglie, c.radice, r.dati AS registro "
                "FROM public.catena_pda c LEFT JOIN public.catena_registro r "
                "  ON r.aip_id = c.aip_id AND r.tipo = 'PDA' "
                "WHERE c.aip_id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": list(aip_ids)})).mappings().all()
        risultato: dict[uuid.UUID, FogliePda] = {}
        for r in righe:
            foglie = r["foglie"]
            if isinstance(foglie, str):
                foglie = json.loads(foglie)
            radice = str(r["radice"])
            if r["registro"]:
                radice = str(json.loads(r["registro"]).get("radice_pda") or radice)
            risultato[r["aip_id"]] = FogliePda(tuple(str(f) for f in foglie), radice)
        return risultato


@dataclass(frozen=True)
class FogliaAttesa:
    """Foglia salvata di un componente: testo canonico e campo dell'impronta."""

    testo: str
    campo: str  # "sha256" (file) oppure "ipda"

    @property
    def dati(self) -> dict[str, Any]:
        return dict(json.loads(self.testo))

    @property
    def impronta_registrata(self) -> str:
        return str(self.dati[self.campo])

    @property
    def hash_hex(self) -> str:
        return hash_foglia(self.testo.encode("utf-8")).hex()

    def ricalcola(self, impronta_trovata: str) -> str:
        """Hash della foglia con l'impronta trovata sullo storage al posto di quella salvata."""
        dati = self.dati
        dati[self.campo] = impronta_trovata.lower()
        return hash_foglia(json_canonico(dati)).hex()


@dataclass(frozen=True)
class FoglieAbbinate:
    """Foglie di un PdA abbinate ai suoi oggetti.

    ``non_abbinati``: documenti del database senza foglia; ``orfane``: foglie di
    file senza documento nel database (riga di ``documenti`` cancellata o
    alterata). Entrambi sono anomalie (rev4 rilievo 3): le foglie della catena,
    ancorate ai checkpoint firmati, sono la fonte di verità di COSA rileggere."""

    per_documento: dict[uuid.UUID, FogliaAttesa]
    ipda: FogliaAttesa | None
    radice_coerente: bool | None  # None = radice registrata non fornita
    non_abbinati: tuple[uuid.UUID, ...]
    orfane: tuple[FogliaAttesa, ...] = ()


def _uuid(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


def abbina(foglie: FogliePda, documenti: Sequence[Mapping[str, Any]]) -> FoglieAbbinate:
    """Abbina le foglie dei file ai documenti del PdA.

    Ogni foglia porta l'id del documento (``doc``, contratto K1): l'abbinamento è
    per id, con il nome del file come controllo. Per le foglie con un ``doc``
    che non è un id di documento (PdA di prova o storici) si ripiega
    sull'ordine dei documenti nell'IPdA (``ordine``, poi id), sempre con il
    nome come controllo. Una foglia senza documento è ``orfana``, un documento
    senza foglia è ``non_abbinato``: il confronto non si inventa."""
    documenti_ordinati = sorted(documenti, key=lambda d: (int(d["ordine"]), str(d["id"])))
    per_id = {uuid.UUID(str(d["id"])): d for d in documenti_ordinati}
    foglie_file: list[FogliaAttesa] = []
    foglia_ipda: FogliaAttesa | None = None
    for testo in foglie.foglie:
        dati = json.loads(testo)
        if "ipda" in dati:
            foglia_ipda = FogliaAttesa(testo, "ipda")
        elif "sha256" in dati:
            foglie_file.append(FogliaAttesa(testo, "sha256"))
    per_documento: dict[uuid.UUID, FogliaAttesa] = {}
    senza_id: list[FogliaAttesa] = []
    orfane: list[FogliaAttesa] = []
    for foglia in foglie_file:
        dati = foglia.dati
        doc_id = _uuid(dati.get("doc"))
        if doc_id is None:
            senza_id.append(foglia)
            continue
        documento = per_id.get(doc_id)
        if documento is not None and doc_id not in per_documento \
                and dati.get("file") == documento.get("nome_file"):
            per_documento[doc_id] = foglia
        else:
            orfane.append(foglia)
    if senza_id:
        liberi = [d for d in documenti_ordinati if uuid.UUID(str(d["id"])) not in per_documento]
        for posizione, foglia in enumerate(senza_id):
            if posizione < len(liberi) and foglia.dati.get("file") == liberi[posizione].get(
                    "nome_file"):
                per_documento[uuid.UUID(str(liberi[posizione]["id"]))] = foglia
            else:
                orfane.append(foglia)
    non_abbinati = [uuid.UUID(str(d["id"])) for d in documenti_ordinati
                    if uuid.UUID(str(d["id"])) not in per_documento]
    coerente: bool | None = None
    if foglie.radice_pda is not None and foglie.foglie:
        try:
            coerente = albero_pda_da_foglie(foglie.foglie).radice_hex == foglie.radice_pda.lower()
        except ValueError:  # foglia salvata non canonica: non ricompone la radice
            coerente = False
    return FoglieAbbinate(per_documento, foglia_ipda, coerente, tuple(non_abbinati),
                          tuple(orfane))
