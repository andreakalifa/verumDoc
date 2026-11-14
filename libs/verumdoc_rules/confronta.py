# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/confronta.py — confronto di due configurazioni sui PdV reali
(R05 Q1; REGOLE_SVILUPPO §8.11, §13.1, §13.4).

UNA funzione, ``confronta``, usata da:
  - l'anteprima di una bozza di configurazione (onboarding,
    ``POST /api/v1/configurazione/bozze/{id}/anteprima``): impatto sui PdV degli
    ultimi giorni;
  - ``tools/confronta_regole.py`` (sola lettura, da riga di comando).

Riapplica il motore (fasi INDICE e FILE, con le dimensioni e i formati
RILEVATI salvati sul PdV) con la configurazione «prima» e «dopo» e conta i PdV
che sarebbero rifiutati e quelli con avvisi nuovi. Lavora su un CAMPIONE
limitato e dichiarato (numero massimo e tempo massimo): con milioni di PdV per
ente il calcolo resta limitato e l'esito dice se è stato troncato.

Pacchetto puro: il tempo massimo lo controlla il chiamante con la funzione
``tempo_scaduto`` (nessun orologio qui).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from libs.verumdoc_rules.regole import (
    AmbienteRegole,
    CatalogoRegole,
    DatiVersamento,
    DocumentoDaValutare,
    EsitoRegole,
    Fase,
    Rilievo,
)


@dataclass(frozen=True)
class PdvCampione:
    """Un PdV ricostruito per il confronto (nessun contenuto, solo metadati)."""

    id: str
    tenant_id: str
    classe_id: str
    dati: DatiVersamento
    accordo: str | None = None
    ricevuto_il: str | None = None


def dati_da_pdv(indice: Mapping[str, Any] | None, documenti: Iterable[Mapping[str, Any]],
                campi_noti: frozenset[str],
                normalizza: Callable[[str], str] | None = None) -> DatiVersamento:
    """Vista del motore da ``pdv.metadata_json``: ``indice`` (normalizzato al
    versamento) e ``docs_s3_keys`` (dimensione e formato rilevato per file)."""
    indice = indice or {}
    per_nome = {str(d.get("nome_file")): d for d in documenti}
    docs = []
    for doc in indice.get("documenti") or ():
        info = per_nome.get(str(doc.get("nome_file")), {})
        parametri = doc.get("parametri")
        docs.append(DocumentoDaValutare(
            id=str(doc.get("id")), tipo=str(doc.get("tipo")), nome_file=str(doc.get("nome_file")),
            metadati=dict(parametri) if isinstance(parametri, Mapping) else None,
            dimensione_byte=int(info["size_bytes"]) if info.get("size_bytes") is not None
            else None,
            formato_rilevato=str(info["formato_rilevato"]) if info.get("formato_rilevato")
            else None,
        ))
    extra: dict[str, Any] = {"normalizza_formato": normalizza} if normalizza else {}
    return DatiVersamento(classe=str(indice.get("classe_documentale") or ""),
                          documenti=tuple(docs), campi_noti=campi_noti, **extra)


def valuta_tutto(catalogo: CatalogoRegole, dati: DatiVersamento,
                 ambiente: AmbienteRegole) -> EsitoRegole:
    """Le due fasi del versamento, nello stesso ordine del gateway."""
    return catalogo.valuta(dati, ambiente, Fase.INDICE).unisci(
        catalogo.valuta(dati, ambiente, Fase.FILE))


def _chiave(r: Rilievo) -> tuple[str, str, str | None, str | None]:
    return (r.regola, r.effetto.value, r.campo, r.documento)


@dataclass
class EsitoConfronto:
    """Differenze sul campione; ``troncato`` se il tempo massimo è finito prima."""

    analizzati: int = 0
    rifiutati_dopo: int = 0
    rifiutati_prima: int = 0
    con_nuovi_avvisi: int = 0
    #: PdV con un esito diverso (nuovi rilievi, nuovi avvisi o rifiuto tolto)
    con_differenze: int = 0
    non_valutabili: int = 0
    esempi: list[dict[str, Any]] = field(default_factory=list)
    troncato_per_tempo: bool = False

    def come_dict(self) -> dict[str, Any]:
        return {"analizzati": self.analizzati, "rifiutati_dopo": self.rifiutati_dopo,
                "rifiutati_prima": self.rifiutati_prima,
                "con_nuovi_avvisi": self.con_nuovi_avvisi,
                "con_differenze": self.con_differenze,
                "non_valutabili": self.non_valutabili, "esempi": self.esempi,
                "troncato_per_tempo": self.troncato_per_tempo}


def confronta(campione: Iterable[PdvCampione], *, catalogo: CatalogoRegole,
              ambiente_prima: Callable[[PdvCampione], AmbienteRegole],
              ambiente_dopo: Callable[[PdvCampione], AmbienteRegole],
              max_esempi: int = 10,
              tempo_scaduto: Callable[[], bool] | None = None) -> EsitoConfronto:
    """Riapplica il motore a ogni PdV con le due configurazioni.

    Un PdV la cui configurazione non si risolve (es. accordo non più valido) si
    conta in ``non_valutabili`` con un esempio: non si indovina un esito."""
    esito = EsitoConfronto()
    for pdv in campione:
        if tempo_scaduto is not None and tempo_scaduto():
            esito.troncato_per_tempo = True
            break
        esito.analizzati += 1
        try:
            prima = valuta_tutto(catalogo, pdv.dati, ambiente_prima(pdv))
            dopo = valuta_tutto(catalogo, pdv.dati, ambiente_dopo(pdv))
        except ValueError as exc:
            esito.non_valutabili += 1
            if len(esito.esempi) < max_esempi:
                esito.esempi.append({"pdv_id": pdv.id, "esito": "NON_VALUTABILE",
                                     "motivo": str(exc)[:300]})
            continue
        rifiutato_prima, rifiutato_dopo = prima.blocca, dopo.blocca
        esito.rifiutati_prima += int(rifiutato_prima)
        esito.rifiutati_dopo += int(rifiutato_dopo)
        visti = {_chiave(r) for r in prima.avvisi}
        nuovi_avvisi = [r for r in dopo.avvisi if _chiave(r) not in visti]
        if nuovi_avvisi:
            esito.con_nuovi_avvisi += 1
        bloccanti_prima = {_chiave(r) for r in prima.bloccanti}
        nuovi_bloccanti = [r for r in dopo.bloccanti if _chiave(r) not in bloccanti_prima]
        if nuovi_bloccanti or nuovi_avvisi or rifiutato_prima != rifiutato_dopo:
            esito.con_differenze += 1
        if (nuovi_bloccanti or nuovi_avvisi) and len(esito.esempi) < max_esempi:
            esito.esempi.append({
                "pdv_id": pdv.id, "ricevuto_il": pdv.ricevuto_il,
                "esito": "RIFIUTATO" if rifiutato_dopo else "AVVISI",
                "rilievi": [r.come_dict() for r in nuovi_bloccanti[:5]],
                "avvisi": [r.come_dict() for r in nuovi_avvisi[:5]],
            })
    return esito


__all__ = ["EsitoConfronto", "PdvCampione", "confronta", "dati_da_pdv", "valuta_tutto"]
