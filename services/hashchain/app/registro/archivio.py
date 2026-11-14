# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Accesso al database del registro delle impronte (tabelle della migrazione 20261023_025).

Servizio: hashchain. Riferimenti: contratto K1 di R04; REGOLE_SVILUPPO §12.9
(lease), §16.3 (SQL con parametri, mai «:p::tipo»), §23.9 (query costanti).

Ogni lettura di nodi è UNA query con l'elenco delle coordinate (``unnest``):
radice e prove costano O(log n) righe lette, mai l'albero intero.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from libs.catena.albero import Coordinata


class LeasePerso(RuntimeError):  # noqa: N818 — nome di dominio
    """Il lease del sequenziatore non è (più) di questo processo: nessuna scrittura."""


@dataclass(frozen=True)
class StatoRegistro:
    """Riga unica ``catena_stato``."""

    dimensione: int
    detentore: str | None
    lease_scade_il: datetime | None
    worm_foglie_esportate: int
    testimone_dimensione: int | None


_STATO = ("SELECT dimensione, detentore, lease_scade_il, worm_foglie_esportate, "
          "testimone_dimensione FROM public.catena_stato WHERE id = 1")
_STATO_BLOCCATO = _STATO + " FOR UPDATE"


async def leggi_stato(session: Any, *, blocca: bool = False) -> StatoRegistro:
    """Stato del registro; con ``blocca`` la riga resta bloccata fino al commit
    (serializza le scritture del registro: una sola dimensione alla volta)."""
    riga = (await session.execute(text(
        _STATO_BLOCCATO if blocca else _STATO
    ))).mappings().first()
    if riga is None:
        raise RuntimeError("catena_stato senza la riga 1: migrazione 20261023_025 non applicata")
    return StatoRegistro(int(riga["dimensione"]), riga["detentore"], riga["lease_scade_il"],
                         int(riga["worm_foglie_esportate"]),
                         None if riga["testimone_dimensione"] is None
                         else int(riga["testimone_dimensione"]))


async def stato_con_lease(session: Any, detentore: str, adesso: datetime) -> StatoRegistro:
    """Blocca la riga di stato e verifica che il lease sia di ``detentore``."""
    stato = await leggi_stato(session, blocca=True)
    if stato.detentore != detentore or stato.lease_scade_il is None \
            or stato.lease_scade_il <= adesso:
        raise LeasePerso(f"lease del registro non detenuto da {detentore}")
    return stato


async def acquisisci_lease(session: Any, detentore: str, adesso: datetime,
                           scade_il: datetime) -> bool:
    """Prende o rinnova il lease (libero, scaduto o già suo). True se ottenuto."""
    risultato = await session.execute(text(
        "UPDATE public.catena_stato SET detentore = :detentore, lease_scade_il = :scade, "
        "aggiornato_il = :adesso WHERE id = 1 AND (detentore IS NULL OR detentore = :detentore "
        "OR lease_scade_il IS NULL OR lease_scade_il <= :adesso)"
    ), {"detentore": detentore, "scade": scade_il, "adesso": adesso})
    return bool(risultato.rowcount == 1)


async def rilascia_lease(session: Any, detentore: str) -> None:
    """Rilascia il lease se è di ``detentore`` (arresto ordinato)."""
    await session.execute(text(
        "UPDATE public.catena_stato SET detentore = NULL, lease_scade_il = NULL "
        "WHERE id = 1 AND detentore = :detentore"
    ), {"detentore": detentore})


async def leggi_nodi(session: Any, coordinate: Iterable[Coordinata]) -> dict[Coordinata, bytes]:
    """Nodi alle coordinate richieste (una query)."""
    elenco = sorted(set(coordinate))
    if not elenco:
        return {}
    righe = (await session.execute(text(
        "SELECT n.livello, n.indice, n.hash FROM public.catena_nodi n "
        "JOIN unnest(CAST(:livelli AS smallint[]), CAST(:indici AS bigint[])) AS c(l, i) "
        "ON n.livello = c.l AND n.indice = c.i"
    ), {"livelli": [c[0] for c in elenco], "indici": [c[1] for c in elenco]})).all()
    return {(int(r[0]), int(r[1])): bytes(r[2]) for r in righe}


async def scrivi_nodi(session: Any, nodi: Mapping[Coordinata, bytes]) -> None:
    """INSERT in blocco dei nodi nuovi (append-only: un conflitto è un errore)."""
    if not nodi:
        return
    elenco = sorted(nodi.items())
    await session.execute(text(
        "INSERT INTO public.catena_nodi (livello, indice, hash) "
        "SELECT * FROM unnest(CAST(:livelli AS smallint[]), CAST(:indici AS bigint[]), "
        "CAST(:hash AS bytea[]))"
    ), {"livelli": [c[0] for c, _ in elenco], "indici": [c[1] for c, _ in elenco],
        "hash": [h for _, h in elenco]})


async def scrivi_foglie(session: Any, foglie: list[dict[str, Any]]) -> None:
    """INSERT in blocco delle foglie del registro (indice, aip, ente, tipo, dati, hash, istante)."""
    if not foglie:
        return
    await session.execute(text(
        "INSERT INTO public.catena_registro "
        "(indice, aip_id, tenant_id, tipo, dati, hash, conservato_il) "
        "SELECT * FROM unnest(CAST(:indici AS bigint[]), CAST(:aip AS uuid[]), "
        "CAST(:enti AS uuid[]), CAST(:tipi AS varchar[]), CAST(:dati AS text[]), "
        "CAST(:hash AS bytea[]), CAST(:istanti AS timestamptz[]))"
    ), {"indici": [f["indice"] for f in foglie], "aip": [f["aip_id"] for f in foglie],
        "enti": [f["tenant_id"] for f in foglie], "tipi": [f["tipo"] for f in foglie],
        "dati": [f["dati"] for f in foglie], "hash": [f["hash"] for f in foglie],
        "istanti": [f["conservato_il"] for f in foglie]})


async def collega_foglie_interne(session: Any, coppie: list[tuple[Any, int]],
                                 adesso: datetime) -> None:
    """Indice nel registro delle foglie interne (scritto una sola volta: trigger)."""
    if not coppie:
        return
    risultato = await session.execute(text(
        "UPDATE public.catena_foglie_interne f SET indice = v.indice, sequenziata_il = :adesso "
        "FROM unnest(CAST(:ids AS uuid[]), CAST(:indici AS bigint[])) AS v(id, indice) "
        "WHERE f.id = v.id AND f.indice IS NULL"
    ), {"ids": [c[0] for c in coppie], "indici": [c[1] for c in coppie], "adesso": adesso})
    if risultato.rowcount != len(coppie):
        raise RuntimeError("foglie interne non collegate al registro: indice già presente")


async def leggi_dati_foglie(session: Any, da: int, a: int) -> list[str]:
    """Dati (JSON canonico) delle foglie con indice in [da, a), in ordine."""
    righe = (await session.execute(text(
        "SELECT indice, dati FROM public.catena_registro "
        "WHERE indice >= :da AND indice < :a ORDER BY indice"
    ), {"da": da, "a": a})).all()
    if len(righe) != a - da or (righe and (righe[0][0] != da or righe[-1][0] != a - 1)):
        raise RuntimeError(f"Foglie del registro mancanti nell'intervallo [{da}, {a})")
    return [str(r[1]) for r in righe]


_COLONNE_CHECKPOINT = (
    "SELECT dimensione, radice, origine, testo, chiave_verificatore, emesso_il, "
    "worm_bucket, worm_chiave, stato_sigillo, motivo_sigillo, data_marca, "
    "sigillo_worm_chiave, stato_testimone, controfirma, controfirmato_il, esito_testimone "
    "FROM public.catena_checkpoint")


async def ultimo_checkpoint(session: Any, *, dimensione: int | None = None
                            ) -> dict[str, Any] | None:
    """Ultimo checkpoint emesso (o quello a ``dimensione``), qualunque sia lo stato."""
    dove, parametri = "", {}
    if dimensione is not None:
        dove, parametri = " WHERE dimensione = :dimensione", {"dimensione": dimensione}
    riga = (await session.execute(text(
        _COLONNE_CHECKPOINT + dove + " ORDER BY dimensione DESC LIMIT 1"
    ), parametri)).mappings().first()
    return dict(riga) if riga else None


@dataclass(frozen=True)
class CheckpointUtilizzabili:
    """Checkpoint con cui si può completare la conservazione (rev4 rilievo 1).

    ``controfirmato``: l'ultimo CONTROFIRMATO dal testimone con sigillo definitivo
    (preferito per ogni PdA che copre). ``senza_testimone``: l'ultimo
    NON_CONFIGURATO con sigillo definitivo, solo se l'ambiente ammette un
    registro senza testimone (mai in produzione) e il testimone non è
    configurato ora. Un checkpoint IN_ATTESA, SUPERATO o RIFIUTATO non è mai
    utilizzabile."""

    controfirmato: dict[str, Any] | None
    senza_testimone: dict[str, Any] | None

    def per_foglia(self, indice: int) -> dict[str, Any] | None:
        """Checkpoint per la foglia ``indice``: il controfirmato se la copre."""
        for cp in (self.controfirmato, self.senza_testimone):
            if cp is not None and indice < int(cp["dimensione"]):
                return cp
        return None

    @property
    def copertura(self) -> int:
        """Foglie coperte da almeno uno dei due checkpoint."""
        return max((int(cp["dimensione"]) for cp in (self.controfirmato, self.senza_testimone)
                    if cp is not None), default=0)


async def checkpoint_utilizzabili(session: Any, *, ammetti_senza_testimone: bool
                                  ) -> CheckpointUtilizzabili:
    """Vedi ``CheckpointUtilizzabili`` (due letture dall'indice primario)."""
    async def _ultimo(stato: str) -> dict[str, Any] | None:
        riga = (await session.execute(text(
            _COLONNE_CHECKPOINT + " WHERE stato_testimone = :stato "
            "AND stato_sigillo <> 'DA_FIRMARE' ORDER BY dimensione DESC LIMIT 1"
        ), {"stato": stato})).mappings().first()
        return dict(riga) if riga else None

    controfirmato = await _ultimo("CONTROFIRMATO")
    libero = await _ultimo("NON_CONFIGURATO") if ammetti_senza_testimone else None
    return CheckpointUtilizzabili(controfirmato, libero)


async def rifiuti_aperti(session: Any) -> int:
    """Checkpoint rifiutati dal testimone e non ancora gestiti da un operatore."""
    return int((await session.execute(text(
        "SELECT count(*) FROM public.catena_checkpoint "
        "WHERE stato_testimone = 'RIFIUTATO' AND rifiuto_gestito_il IS NULL"
    ))).scalar_one())


def nota_pubblicata(checkpoint: Mapping[str, Any]) -> str:
    """Nota firmata dal registro, con la controfirma del testimone se presente."""
    from libs.catena import nota

    testo = str(checkpoint["testo"])
    if checkpoint.get("controfirma"):
        return str(nota.aggiungi_firme(testo, [str(checkpoint["controfirma"])]))
    return testo
