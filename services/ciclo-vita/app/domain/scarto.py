# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/domain/scarto.py — regole pure dello scarto (selezione, blocchi, piano).

Riferimenti: contratto Q2 di R05; Linee guida AgID §4.11 (scarto); REGOLE
§9.3 (un concetto, una query: ``pda_scartabili``), §9.7 (motivo di blocco),
§10.1 (nulla si cancella prima del termine), §11.7, §13 (impronta del piano).

Modulo PURO: niente I/O, niente orologio (``adesso`` è un argomento). Lo usano
l'anteprima, l'applicazione della proposta (nel lock, sugli stessi dati) e
l'esecuzione (controllo sugli oggetti WORM): stessa funzione, stesso esito.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Motivi di blocco (stabili: finiscono nel piano, nel verbale e nell'API)
STATO_NON_CONSERVATO = "STATO_NON_CONSERVATO"
CONSERVAZIONE_PERMANENTE = "CONSERVAZIONE_PERMANENTE"
RETENTION_NON_SCADUTA = "RETENTION_NON_SCADUTA"
FASCICOLO_APERTO = "FASCICOLO_APERTO"
TERMINE_FASCICOLO_NON_SCADUTO = "TERMINE_FASCICOLO_NON_SCADUTO"
IN_ALTRA_PRATICA = "IN_ALTRA_PRATICA"
ESIBIZIONE_IN_CORSO = "ESIBIZIONE_IN_CORSO"
LEGAL_HOLD = "LEGAL_HOLD"
RETENTION_ASSENTE = "RETENTION_ASSENTE"
#: Revisione R05 (R2): nessun oggetto del PdA nel WORM, o mancano oggetti che il
#: database dichiara: lo scarto non si attesta mai su ciò che non si è visto
OGGETTI_ASSENTI = "OGGETTI_ASSENTI"
OGGETTI_MANCANTI = "OGGETTI_MANCANTI"
#: Revisione 2 di R05 (S3): cause di un PdA la cui cancellazione, già INIZIATA,
#: non si può completare (oggetto NON_COMPLETATO, PdA ELIMINAZIONE_INCOMPLETA)
ERRORE_PERSISTENTE = "ERRORE_PERSISTENTE"
VERSIONI_RIMASTE = "VERSIONI_RIMASTE"
#: Revisione 4 di R05 (U2): oggetto da eseguire che il piano APPROVATO non
#: selezionava (``esito_piano`` diverso da SELEZIONATO): mai cancellato
FUORI_PIANO = "FUORI_PIANO"

DESCRIZIONE_MOTIVI: Mapping[str, str] = {
    STATO_NON_CONSERVATO: "Il pacchetto non è nello stato CONSERVATO",
    CONSERVAZIONE_PERMANENTE: "La classe documentale prevede la conservazione permanente",
    RETENTION_NON_SCADUTA: "Il termine di conservazione non è ancora scaduto",
    FASCICOLO_APERTO: "Il pacchetto appartiene a un fascicolo ancora aperto",
    TERMINE_FASCICOLO_NON_SCADUTO: ("Il termine di conservazione calcolato dalla chiusura del "
                                    "fascicolo non è ancora scaduto"),
    IN_ALTRA_PRATICA: "Il pacchetto è già in un'altra pratica di scarto non conclusa",
    ESIBIZIONE_IN_CORSO: "Un pacchetto di distribuzione del PdA è in preparazione",
    LEGAL_HOLD: "Blocco legale (legal hold) attivo su un oggetto del PdA nello storage",
    RETENTION_ASSENTE: ("Un oggetto del PdA nello storage conservativo non ha retention: "
                        "anomalia da verificare prima dello scarto"),
    OGGETTI_ASSENTI: ("Nessun oggetto del PdA nello storage conservativo (bucket o prefisso "
                      "diversi, oggetti altrove): anomalia da verificare, nulla si dichiara "
                      "scartato"),
    OGGETTI_MANCANTI: ("Oggetti del PdA indicati nel database (manifest, IPdA, file) assenti "
                       "dallo storage conservativo: anomalia da verificare prima dello scarto"),
    ERRORE_PERSISTENTE: ("Lo storage ha rifiutato la cancellazione di una versione più volte "
                         "(es. permessi): cancellazione del PdA interrotta, da riprendere"),
    VERSIONI_RIMASTE: ("Dopo la cancellazione restano versioni sotto il prefisso del PdA "
                       "(scritte dopo l'elenco o non cancellabili): da verificare e riprendere"),
    FUORI_PIANO: ("Il PdA non era selezionato nel piano approvato (esito del piano diverso): "
                  "mai cancellato, anomalia da verificare"),
}

SELEZIONATO = "SELEZIONATO"
BLOCCATO = "BLOCCATO"
#: Revisione 2 di R05 (S3): cancellazione iniziata e NON completabile (almeno
#: una versione cancellata, almeno una rimasta): stato terminale dell'oggetto
#: finché il conservatore non riprende l'esecuzione (runbook scarto_incompleto)
NON_COMPLETATO = "NON_COMPLETATO"
#: Stato del PdA con una cancellazione iniziata e non completata
PDA_ELIMINAZIONE_INCOMPLETA = "ELIMINAZIONE_INCOMPLETA"
#: Motivi che non cambiano ritentando (S3: niente tentativi, oggetto chiuso subito)
BLOCCHI_DEFINITIVI = frozenset({LEGAL_HOLD, RETENTION_NON_SCADUTA, RETENTION_ASSENTE})


@dataclass(frozen=True)
class DatiPda:
    """Dati di un PdA candidato allo scarto (letti in un'unica query)."""

    aip_id: str
    urn: str
    stato: str
    permanente: bool
    scadenza: datetime | None
    fascicolo_aperto: bool = False
    #: fine del termine calcolato dalla chiusura del fascicolo più recente (se c'è)
    termine_fascicolo: datetime | None = None
    in_altra_pratica: bool = False
    esibizione_in_corso: bool = False


def motivo_blocco_pda(pda: DatiPda, adesso: datetime) -> str | None:
    """``None`` se il PdA è scartabile ora, altrimenti il motivo (il primo, in
    ordine fisso). Lo scarto non è mai anticipato: scadenza assente (permanente)
    o futura → bloccato."""
    if adesso.tzinfo is None:
        raise ValueError("adesso senza fuso orario")
    if pda.stato != "CONSERVATO":
        return STATO_NON_CONSERVATO
    if pda.permanente or pda.scadenza is None:
        return CONSERVAZIONE_PERMANENTE
    if pda.scadenza > adesso:
        return RETENTION_NON_SCADUTA
    if pda.fascicolo_aperto:
        return FASCICOLO_APERTO
    if pda.termine_fascicolo is not None and pda.termine_fascicolo > adesso:
        return TERMINE_FASCICOLO_NON_SCADUTO
    if pda.in_altra_pratica:
        return IN_ALTRA_PRATICA
    if pda.esibizione_in_corso:
        return ESIBIZIONE_IN_CORSO
    return None


@dataclass(frozen=True)
class OggettoWorm:
    """Una versione di un oggetto del PdA nello storage conservativo."""

    chiave: str
    version_id: str
    conserva_fino_a: datetime | None
    legal_hold: bool


def motivo_blocco_oggetto(oggetto: OggettoWorm, adesso: datetime) -> str | None:
    """Controllo PRIMA di ogni cancellazione nel WORM: la retention Object Lock
    deve essere scaduta e il legal hold spento. S3 in COMPLIANCE rifiuterebbe
    comunque, ma il servizio non lo tenta mai (contratto Q2)."""
    if adesso.tzinfo is None:
        raise ValueError("adesso senza fuso orario")
    if oggetto.legal_hold:
        return LEGAL_HOLD
    if oggetto.conserva_fino_a is None:
        return RETENTION_ASSENTE
    if oggetto.conserva_fino_a > adesso:
        return RETENTION_NON_SCADUTA
    return None


def inventario(versioni: Iterable[OggettoWorm]) -> list[list[str]]:
    """Revisione 3 di R05 (T1): inventario delle versioni del PdA (chiave e
    versione), registrato nel database PRIMA della prima cancellazione. È il
    riferimento con cui, a ogni chiusura, si decide dallo STORAGE (non da un
    contatore in memoria, perso se il processo muore) che cosa è stato distrutto."""
    return sorted([v.chiave, v.version_id] for v in versioni)


def versioni_distrutte(inventario_iniziale: Iterable[Sequence[str]] | None,
                       rimaste: Iterable[OggettoWorm] | None) -> list[tuple[str, str]] | None:
    """Versioni dell'inventario che lo storage non ha più. ``None`` se non si può
    stabilire (inventario non registrato, storage non leggibile): il chiamante
    lo tratta come «distrutto in parte», mai come «nulla distrutto»."""
    if inventario_iniziale is None or rimaste is None:
        return None
    presenti = {(v.chiave, v.version_id) for v in rimaste}
    return sorted((str(k), str(v)) for k, v in inventario_iniziale
                  if (str(k), str(v)) not in presenti)


def nulla_distrutto(inventario_iniziale: Iterable[Sequence[str]] | None,
                    rimaste: Iterable[OggettoWorm] | None, contate: int) -> bool:
    """T1: «nulla distrutto» SOLO se lo storage lo conferma: inventario
    registrato, storage letto, ogni versione dell'inventario ancora presente e
    nessuna cancellazione contata (una versione nata dopo l'elenco e cancellata
    non è nell'inventario, ma il contatore la vede)."""
    distrutte = versioni_distrutte(inventario_iniziale, rimaste)
    return distrutte is not None and not distrutte and contate == 0


def motivo_blocco_oggetti(oggetti: list[OggettoWorm], adesso: datetime, *,
                          attese: Collection[str] = ()) -> str | None:
    """Il PdA si cancella solo se TUTTE le sue versioni sono cancellabili: mai
    uno scarto parziale. Revisione R05 (R2): nessuna versione trovata è un
    blocco (``OGGETTI_ASSENTI``), mai un PdA «eliminato» senza aver cancellato
    nulla; ogni chiave ``attese`` (dal database: manifest, IPdA, file del PdA)
    deve essere fra quelle trovate (``OGGETTI_MANCANTI``)."""
    if not oggetti:
        return OGGETTI_ASSENTI
    trovate = {o.chiave for o in oggetti}
    if any(chiave not in trovate for chiave in attese):
        return OGGETTI_MANCANTI
    for o in oggetti:
        motivo = motivo_blocco_oggetto(o, adesso)
        if motivo is not None:
            return motivo
    return None


#: Natura dell'ente (``tenants.natura``, revisione R05 R9)
NATURA_PA = "PA"
NATURA_PRIVATO = "PRIVATO"


def richiede_nulla_osta(natura: str | None) -> bool:
    """Lo scarto di un ente richiede il nulla osta della Soprintendenza salvo che
    l'ente sia DICHIARATO privato: natura assente o sconosciuta = pubblica
    amministrazione (fail-closed, REGOLE §14.3). Il codice IPA non conta."""
    return (natura or "").strip().upper() != NATURA_PRIVATO


class ImprontaPiano:
    """Impronta SHA-256 del piano di una pratica massiva (REGOLE §13.1):
    intestazione canonica (tipo, ente, criteri) e una riga per elemento, in
    ordine totale (l'ordine lo dà la query: chiave primaria per ultima).
    Incrementale: il piano di 100.000 PdA non sta mai tutto in memoria."""

    def __init__(self, tipo: str, ente: str, criteri: Mapping[str, Any]) -> None:
        self._h = hashlib.sha256()
        intestazione = json.dumps({"tipo": tipo, "ente": ente, "criteri": criteri},
                                  sort_keys=True, separators=(",", ":"), default=str)
        self._h.update(intestazione.encode("utf-8"))
        self.elementi = 0

    def aggiungi(self, ident: str, esito: str, motivo: str | None) -> None:
        self._h.update(f"\n{ident}|{esito}|{motivo or ''}".encode())
        self.elementi += 1

    def hex(self) -> str:
        return self._h.copy().hexdigest()


__all__ = [
    "BLOCCATO", "CONSERVAZIONE_PERMANENTE", "DESCRIZIONE_MOTIVI", "DatiPda",
    "ESIBIZIONE_IN_CORSO", "FASCICOLO_APERTO", "FUORI_PIANO", "IN_ALTRA_PRATICA", "ImprontaPiano",
    "LEGAL_HOLD",
    "NATURA_PA", "NATURA_PRIVATO", "OGGETTI_ASSENTI", "OGGETTI_MANCANTI", "OggettoWorm",
    "RETENTION_ASSENTE", "RETENTION_NON_SCADUTA", "SELEZIONATO", "STATO_NON_CONSERVATO",
    "TERMINE_FASCICOLO_NON_SCADUTO", "inventario", "motivo_blocco_oggetti",
    "motivo_blocco_oggetto", "motivo_blocco_pda", "nulla_distrutto", "richiede_nulla_osta",
    "versioni_distrutte",
]
