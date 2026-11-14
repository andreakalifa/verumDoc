# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica delle versioni rimaste dei PdA in eliminazione incompleta (R09, D26).

Uno scarto che non ha potuto completare la cancellazione di un PdA (legal hold
arrivato durante lo scarto, errore persistente dello storage) lascia il PdA in
``ELIMINAZIONE_INCOMPLETA`` con alcune versioni ancora nel WORM. Prima di R09 il
PdA usciva dalla verifica continua (revisione T4: altrimenti gli oggetti
cancellati davano MANCANTE); ora si verifica ciò che RESTA.

Revisione R09 (Z2): lo scarto registra, alla chiusura dell'oggetto
NON_COMPLETATO, le versioni TRATTENUTE (``pratiche_oggetti.dettagli.
versioni_trattenute``: chiave e version id di ogni versione che lo storage ha
ancora). Allora:

  - un oggetto MANCANTE è atteso SOLO se la sua chiave è nell'inventario
    registrato prima di cancellare (``dettagli.inventario``) e NESSUNA sua
    versione è fra le trattenute: esito OK con il motivo;
  - una chiave con versioni trattenute resta obbligatoria: se manca è
    un'anomalia (MANCANTE, allarme come ogni anomalia);
  - ogni versione trattenuta si cerca nello storage per version id (una
    lettura HEAD ciascuna, nel budget di letture): una versione sparita è
    MANCANTE sull'oggetto della sua chiave (o sul PdA, se la chiave non è fra gli
    oggetti verificati);
  - un oggetto mancante fuori dall'inventario resta un'anomalia;
  - oggetti chiusi prima della correzione (senza ``versioni_trattenute``): sono
    attese mancanti solo le chiavi registrate come eliminate (``chiavi_eliminate``)
    e non fra le rimaste (``chiavi_rimaste``); nel dubbio, anomalia.

Mai «nessun falso allarme» al prezzo di un allarme vero perso.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import text

from app.verifica_continua import esiti
from app.verifica_continua.verificatore import EsitoOggetto, EsitoPda

MOTIVO = "cancellato dallo scarto (eliminazione incompleta): non più atteso"
MOTIVO_TRATTENUTA = "versione trattenuta dallo scarto non più presente nello storage"
MOTIVO_CHIAVE_TRATTENUTA = ("chiave con versioni trattenute dallo scarto (legal hold): "
                            "doveva esserci")
_ASSENTE = frozenset({"NoSuchKey", "NoSuchVersion", "404", "NotFound"})


@dataclass
class ScartoIncompleto:
    """Ciò che lo scarto ha registrato per il PdA (tutti gli oggetti NON_COMPLETATO)."""

    inventariate: set[str] = field(default_factory=set)
    #: (chiave, version id) trattenute; None = oggetti chiusi prima di Z2
    trattenute: set[tuple[str, str]] | None = None
    eliminate: set[str] = field(default_factory=set)
    rimaste: set[str] = field(default_factory=set)

    def attese_mancanti(self) -> set[str]:
        if self.trattenute is not None:
            return self.inventariate - {k for k, _ in self.trattenute}
        return (self.eliminate & self.inventariate) - self.rimaste


def _carica(dettagli: Any) -> dict[str, Any]:
    if isinstance(dettagli, str):
        dettagli = json.loads(dettagli)
    return dict(dettagli or {})


def da_dettagli(righe: list[Any]) -> ScartoIncompleto:
    s = ScartoIncompleto()
    trattenute: set[tuple[str, str]] = set()
    tutte_registrate = bool(righe)
    for riga in righe:
        d = _carica(riga)
        for voce in d.get("inventario") or []:
            if isinstance(voce, list | tuple) and voce:
                s.inventariate.add(str(voce[0]))
        if "versioni_trattenute" in d:
            for voce in d.get("versioni_trattenute") or []:
                if isinstance(voce, list | tuple) and len(voce) >= 2:
                    trattenute.add((str(voce[0]), str(voce[1])))
        else:
            tutte_registrate = False
        s.eliminate |= {str(c) for c in d.get("chiavi_eliminate") or []}
        s.rimaste |= {str(c) for c in d.get("chiavi_rimaste") or []}
    s.trattenute = trattenute if tutte_registrate else None
    return s


async def leggi(aip_id: uuid.UUID) -> ScartoIncompleto:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT o.dettagli FROM public.pratiche_oggetti o "
            "JOIN public.pratiche p ON p.id = o.pratica_id WHERE o.aip_id = :a "
            "AND p.tipo = 'SCARTO' AND o.esito = 'NON_COMPLETATO'"),
            {"a": aip_id})).scalars().all()
    return da_dettagli(list(righe))


def adatta(esito: EsitoPda, scarto: ScartoIncompleto) -> EsitoPda:
    """MANCANTE → OK (con il motivo) per le sole chiavi cancellate per intero
    dallo scarto; una chiave con versioni trattenute resta obbligatoria."""
    attese = scarto.attese_mancanti()
    trattenute = {k for k, _ in scarto.trattenute or ()}
    for o in esito.oggetti:
        if o.esito != esiti.MANCANTE or not o.chiave:
            continue
        if o.chiave in attese:
            o.esito = esiti.OK
            o.motivi.append(MOTIVO)
        elif o.chiave in trattenute:
            o.motivi.append(MOTIVO_CHIAVE_TRATTENUTA)
    return esito


def _cerca_versione(client: Any, bucket: str, chiave: str, versione: str) -> str | None:
    """None = presente; altrimenti l'esito (MANCANTE o ERRORE_LETTURA) con il codice."""
    try:
        client.head_object(Bucket=bucket, Key=chiave, VersionId=versione)
    except ClientError as exc:
        codice = str(exc.response.get("Error", {}).get("Code", ""))
        return esiti.MANCANTE if codice in _ASSENTE else f"{esiti.ERRORE_LETTURA}:{codice}"
    except BotoCoreError as exc:
        return f"{esiti.ERRORE_LETTURA}:{type(exc).__name__}"
    return None


def _oggetto_per(esito: EsitoPda, chiave: str) -> EsitoOggetto | None:
    for o in esito.oggetti:
        if o.chiave == chiave:
            return o
    for o in esito.oggetti:
        if o.componente == "PDA":
            return o
    return esito.oggetti[0] if esito.oggetti else None


async def controlla_trattenute(esito: EsitoPda, scarto: ScartoIncompleto, *, client: Any,
                               budget_letture: Any = None) -> EsitoPda:
    """Ogni versione trattenuta deve esistere ancora (HEAD con il version id)."""
    if not scarto.trattenute:
        return esito
    for chiave, versione in sorted(scarto.trattenute):
        if budget_letture is not None:
            await budget_letture.consuma(1)
        trovato = await asyncio.to_thread(_cerca_versione, client, esito.pda.bucket, chiave,
                                          versione)
        if trovato is None:
            continue
        o = _oggetto_per(esito, chiave)
        if o is None:
            continue
        if trovato == esiti.MANCANTE:
            o.esito = esiti.MANCANTE
            o.motivi.append(f"{MOTIVO_TRATTENUTA}: {chiave} versione {versione}")
        elif o.esito == esiti.OK:
            o.esito = esiti.ERRORE_LETTURA
            o.motivi.append(f"versione trattenuta {chiave} non leggibile ({trovato})")
    return esito


async def adatta_esito(esito: EsitoPda, *, client: Any = None,
                       budget_letture: Any = None) -> EsitoPda:
    scarto = await leggi(esito.pda.aip_id)
    adatta(esito, scarto)
    if client is not None:
        await controlla_trattenute(esito, scarto, client=client, budget_letture=budget_letture)
    return esito


def riassunto(esito: EsitoPda) -> dict[str, Any]:
    return {"rimaste": sum(1 for o in esito.oggetti if MOTIVO not in o.motivi),
            "cancellate": sum(1 for o in esito.oggetti if MOTIVO in o.motivi)}


__all__ = ["MOTIVO", "MOTIVO_CHIAVE_TRATTENUTA", "MOTIVO_TRATTENUTA", "ScartoIncompleto",
           "adatta", "adatta_esito", "controlla_trattenute", "da_dettagli", "leggi",
           "riassunto"]
