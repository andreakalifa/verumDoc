# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/firme.py — livello di verifica delle firme per classe e
regola RGL-109 (R08, decisione D01; REGOLE_SVILUPPO §8.1, §8.6).

Impostazione ``FIRME_LIVELLO_VERIFICA`` (scelta, verso «più sicuro» = livello
più alto):

    CON_REVOCHE   crittografica + catena fino a una trust anchor + stato di revoca noto e buono
    CRITTOGRAFICA impronta e firma valide, catena attendibile, certificato non revocato,
                  indicazione TOTAL_PASSED (revoca non pretesa)
    RILEVATA      firmatari letti dalla busta, nessuna verifica (busta illeggibile = rilievo)
    NESSUNA       nessun controllo

Livelli: CONSERVATORE (fissa il MINIMO), ENTE e CLASSE (possono solo alzare:
il motore lo fa valere con l'ordine delle scelte, la console lo blocca). Il
predefinito è RILEVATA: la verifica crittografica richiede trust anchor e
servizio configurati, quindi la sceglie il conservatore (decisione VD-P-70).

RGL-109 (fase VERIFICA, effetto predefinito BLOCCA): per ogni file FIRMATO,
l'esito normalizzato (``libs.shared.firme``) deve soddisfare il livello. Un
file non firmato non è un rilievo (la firma obbligatoria per classe non è in
questo rilascio). Il giudizio sta in ``problemi_firme``: una sola funzione per
la validazione dei PdV e per il ciclo di vita (scarto, nulla osta, annullamento).

Revisione R08 (X1): a CRITTOGRAFICA e CON_REVOCHE una firma vale solo con
indicazione TOTAL_PASSED. Una INDETERMINATE (certificato scaduto, algoritmi
deboli, PDF modificato dopo la firma, revoca non determinabile…) NON è mai
valida. Solo nella validazione dei PdV il conservatore può concedere che le
INDETERMINATE con firma integra, catena attendibile e certificato non revocato
diano un AVVISO (``FIRME_INDETERMINATE_EFFETTO`` = AVVISA, riservata al
conservatore, predefinito BLOCCA; regola RGL-113). Il ciclo di vita non ha
concessioni: ``problemi_firme`` è sempre rigorosa.

Pacchetto puro: lavora sulla forma dizionario dell'esito.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from libs.verumdoc_rules.impostazioni import Impostazione, Livello, TipoImpostazione, VersoSicuro

FIRME_LIVELLO_VERIFICA = "FIRME_LIVELLO_VERIFICA"
NESSUNA, RILEVATA, CRITTOGRAFICA, CON_REVOCHE = ("NESSUNA", "RILEVATA", "CRITTOGRAFICA",
                                                 "CON_REVOCHE")
#: Dal più sicuro al meno (ordine delle scelte = verso sicuro del motore).
LIVELLI: tuple[str, ...] = (CON_REVOCHE, CRITTOGRAFICA, RILEVATA, NESSUNA)

PAROLE_LIVELLO = {
    NESSUNA: "nessuna verifica", RILEVATA: "firma rilevata (non verificata)",
    CRITTOGRAFICA: "verifica crittografica con catena fino a un prestatore fidato",
    CON_REVOCHE: "verifica crittografica con controllo della revoca (OCSP/CRL)",
}

IMPOSTAZIONE_LIVELLO = Impostazione(
    FIRME_LIVELLO_VERIFICA, TipoImpostazione.SCELTA, predefinito=RILEVATA,
    descrizione=("Livello di verifica delle firme dei documenti versati: NESSUNA, RILEVATA, "
                 "CRITTOGRAFICA, CON_REVOCHE. Il conservatore fissa il minimo; l'ente può solo "
                 "alzarlo, anche per classe. L'effetto di un esito insufficiente è quello "
                 "della regola RGL-109"),
    norm_ref="Regolamento (UE) 910/2014 (eIDAS), art. 32; ETSI EN 319 102-1",
    scelte=LIVELLI, ambito_ammesso=frozenset({Livello.CONSERVATORE, Livello.ENTE,
                                              Livello.CLASSE}),
    applicata_da="validation (verifica delle firme)", gruppo="firme",
    verso_sicuro=VersoSicuro.ORDINE_SCELTE)


#: R08 revisione X1: effetto delle firme INDETERMINATE (integre, catena
#: attendibile, non revocate) nella validazione dei PdV. Riservata al
#: conservatore; predefinito BLOCCA (nessuna concessione).
FIRME_INDETERMINATE_EFFETTO = "FIRME_INDETERMINATE_EFFETTO"
INDETERMINATE_BLOCCA, INDETERMINATE_AVVISA = "BLOCCA", "AVVISA"

IMPOSTAZIONE_INDETERMINATE = Impostazione(
    FIRME_INDETERMINATE_EFFETTO, TipoImpostazione.SCELTA, predefinito=INDETERMINATE_BLOCCA,
    descrizione=("Firme INDETERMINATE (firma integra e catena attendibile, ma per esempio "
                 "certificato scaduto o revoca non determinabile) ai livelli CRITTOGRAFICA e "
                 "CON_REVOCHE: BLOCCA (predefinito, la firma non è valida) o AVVISA (concessione "
                 "esplicita del conservatore: il PdV prosegue con l'avviso RGL-113). Non vale "
                 "per il ciclo di vita, dove una firma indeterminata non è mai valida"),
    norm_ref="ETSI EN 319 102-1 §5.1.3",
    scelte=(INDETERMINATE_BLOCCA, INDETERMINATE_AVVISA),
    # locked: solo il conservatore; il motore ignora i livelli del cliente
    locked=True,
    applicata_da="validation (verifica delle firme)", gruppo="firme",
    verso_sicuro=VersoSicuro.ORDINE_SCELTE)


def rango(livello: str) -> int:
    """0 = NESSUNA … 3 = CON_REVOCHE."""
    return len(LIVELLI) - 1 - LIVELLI.index(livello)


def _motivi(firma: Mapping[str, Any]) -> str:
    motivi = [str(m) for m in firma.get("motivi") or () if m]
    return "; ".join(motivi) or str(firma.get("sottoindicazione") or "esito non valido")


def valuta_firme(esito: Mapping[str, Any] | None, livello: str, *,
                 indeterminate_con_avviso: bool = False) -> tuple[list[str], list[str]]:
    """(problemi bloccanti, avvisi) dell'esito rispetto al livello.

    A CRITTOGRAFICA e CON_REVOCHE vale solo TOTAL_PASSED (X1). Con
    ``indeterminate_con_avviso`` (concessione del conservatore, solo PdV) una
    INDETERMINATE con firma integra, catena attendibile e certificato non
    revocato finisce fra gli avvisi invece che fra i problemi."""
    if livello == NESSUNA or esito is None or esito.get("formato") in (None, "NESSUNO"):
        return [], []
    if esito.get("limite_superato"):
        # X5: firme non verificate per dimensione o tempo. A RILEVATA nessun
        # rifiuto (come prima di R08); da CRITTOGRAFICA un file firmato e non
        # verificato non soddisfa il livello (nessuna concessione)
        if livello == RILEVATA:
            return [], []
        return [f"firme non verificate ({esito['limite_superato']})"], []
    firme = list(esito.get("firme") or ())
    if not firme:
        return [str(esito.get("motivo") or "busta di firma senza firme leggibili")], []
    problemi: list[str] = []
    avvisi: list[str] = []
    for i, f in enumerate(firme, start=1):
        chi = f.get("firmatario") or f"firma {i}"
        if livello == RILEVATA:
            continue
        if f.get("sottoindicazione") == "SIGNED_DATA_NOT_FOUND":
            # X14: firma staccata senza il documento firmato: esito esplicito
            problemi.append(f"{chi}: {_motivi(f)}")
            continue
        if f.get("indicazione") == "TOTAL_FAILED" or not f.get("crittografia_valida"):
            problemi.append(f"{chi}: firma non valida ({_motivi(f)})")
            continue
        if f.get("revoca") == "REVOCATO":
            problemi.append(f"{chi}: certificato revocato ({_motivi(f)})")
            continue
        if not f.get("catena_attendibile"):
            problemi.append(f"{chi}: firma indeterminata ({_motivi(f)})")
            continue
        difetto = None
        if f.get("indicazione") != "TOTAL_PASSED":
            difetto = f"{chi}: firma indeterminata ({_motivi(f)})"
        elif livello == CON_REVOCHE and f.get("revoca") != "BUONO":
            difetto = f"{chi}: stato di revoca non verificato ({_motivi(f)})"
        if difetto is None:
            continue
        (avvisi if indeterminate_con_avviso else problemi).append(difetto)
    return problemi, avvisi


def problemi_firme(esito: Mapping[str, Any] | None, livello: str) -> list[str]:
    """Problemi (in parole) dell'esito rispetto al livello; [] = soddisfatto.
    Rigorosa: nessuna concessione per le INDETERMINATE (ciclo di vita)."""
    return valuta_firme(esito, livello)[0]


def esito_in_parole(esito: Mapping[str, Any] | None) -> str:
    """Riassunto per la console: «valida», «non valida», «indeterminata», «non firmato»."""
    if esito is None:
        return "non verificata"
    indicazione = esito.get("indicazione")
    return {None: "non firmato", "TOTAL_PASSED": "valida", "TOTAL_FAILED": "non valida",
            "INDETERMINATE": "indeterminata"}.get(indicazione, str(indicazione))


def riscontri_firme(documenti: Iterator[tuple[str, str, Mapping[str, Any] | None]],
                    livello: str, *, indeterminate_con_avviso: bool = False,
                    avvisi: bool = False) -> Iterator[tuple[str, str, str]]:
    """(id documento, nome file, messaggio) per ogni problema dei documenti
    (``avvisi=True``: per ogni INDETERMINATE concessa con avviso)."""
    for doc_id, nome, esito in documenti:
        problemi, concessi = valuta_firme(esito, livello,
                                          indeterminate_con_avviso=indeterminate_con_avviso)
        for problema in (concessi if avvisi else problemi):
            prefisso = ("ammessa con avviso per concessione del conservatore"
                        if avvisi else "insufficiente")
            yield doc_id, nome, (f"Firma del file {nome} {prefisso} per il livello "
                                 f"{livello} ({PAROLE_LIVELLO[livello]}): {problema}")


__all__ = [
    "CON_REVOCHE", "CRITTOGRAFICA", "FIRME_INDETERMINATE_EFFETTO", "FIRME_LIVELLO_VERIFICA",
    "IMPOSTAZIONE_INDETERMINATE", "IMPOSTAZIONE_LIVELLO", "INDETERMINATE_AVVISA",
    "INDETERMINATE_BLOCCA", "LIVELLI", "NESSUNA", "PAROLE_LIVELLO", "RILEVATA", "esito_in_parole",
    "problemi_firme", "rango", "riscontri_firme", "valuta_firme",
]
