# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/lettura.py — lettura della configurazione dal database in
forma PURA: le query (testo SQL) e la conversione delle righe nel formato del
motore (R05 Q1; REGOLE_SVILUPPO §8.2, §9.1, §9.3).

Il gateway (versamento), onboarding (console, anteprima, validatore preventivo)
e ``tools/confronta_regole.py`` leggono la configurazione con LE STESSE query e
la convertono con LE STESSE funzioni: una sola definizione di «valori di un
contesto». Il pacchetto resta puro: niente SQLAlchemy qui; i servizi eseguono il
testo con i parametri indicati e passano le righe (mapping) alle funzioni.

Parametri delle query: ``:t`` ente (uuid), ``:c`` classe (uuid), ``:g`` giorno
(date, ora italiana). Le colonne jsonb si leggono come testo (``::text``) e si
decodificano qui (un valore stringa JSON non si confonde con il JSON da
decodificare).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from libs.verumdoc_rules.accordi import AccordoVersamento, seleziona_accordo
from libs.verumdoc_rules.catalogo_v1 import (
    CLASSE_DIMENSIONE_MAX_MB,
    FORMATI_AMMESSI,
    METADATI_OBBLIGATORI,
)
from libs.verumdoc_rules.catalogo_v2 import PROFILO_TIPOLOGIA
from libs.verumdoc_rules.catalogo_v4 import PROFILO_TIPOLOGIA_VERSIONE
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    giorno_locale,
)
from libs.verumdoc_rules.override import OverrideVersamento
from libs.verumdoc_rules.profili import (
    ADOZIONE_AUTOMATICA,
    CampoProfilo,
    ProfiloMetadati,
    ProfiloNonValido,
    almeno_severo,
    componi_profili,
    seleziona_profilo,
)
from libs.verumdoc_rules.regole import AmbienteRegole
from libs.verumdoc_rules.tipologie import profilo_tipologia

# ── Query (una sola definizione, §9.3) ──────────────────────────────

#: Valori dei livelli CONSERVATORE, ENTE (:t) e CLASSE (:c). Indici:
#: uq_impostazioni_valori_livello, idx_impostazioni_valori_tenant/_classe.
SQL_VALORI_LIVELLI = (
    "SELECT livello, codice, valore::text AS valore "
    "FROM public.impostazioni_valori "
    "WHERE livello = 'CONSERVATORE' "
    "   OR (livello = 'ENTE' AND tenant_id = :t) "
    "   OR (livello = 'CLASSE' AND classe_id = :c)"
)

#: Versioni del profilo della classe (:c) valide nel giorno (:g).
SQL_PROFILI_CLASSE = (
    "SELECT id::text AS id, versione, valido_dal, valido_al, campi::text AS campi "
    "FROM public.profili_metadati "
    "WHERE classe_id = :c AND valido_dal <= :g AND (valido_al IS NULL OR valido_al >= :g)"
)

#: Versioni già decorse (:g) degli accordi di versamento dell'ente (:t). Pochi
#: per ente; indice idx_accordi_versamento_ente (migrazione 20261023_036).
SQL_ACCORDI_ENTE = (
    "SELECT id::text AS id, tenant_id::text AS tenant_id, codice, versione, "
    "classe_id::text AS classe_id, valido_dal, cessato, valori::text AS valori, descrizione "
    "FROM public.accordi_versamento WHERE tenant_id = :t AND valido_dal <= :g"
)

#: Versioni dei profili di tipologia create dal conservatore (tabella piccola).
SQL_PROFILI_TIPOLOGIA = (
    "SELECT id::text AS id, codice, versione, valido_dal, valido_al, campi::text AS campi, "
    "norm_ref, adozione FROM public.profili_tipologia"
)

#: Impostazioni della classe che stanno nelle colonne di ``classi_documentali``
#: e PREVALGONO sulle righe di livello CLASSE (§8.14: lista vuota = eredita).
CODICI_IN_COLONNA_CLASSE: dict[str, str] = {
    FORMATI_AMMESSI: "formati_ammessi",
    METADATI_OBBLIGATORI: "metadati_obbligatori",
    CLASSE_DIMENSIONE_MAX_MB: "max_dimensione_mb",
}


def json_o_none(testo: str | None) -> Any:
    return None if testo is None else json.loads(testo)


def livelli_da_righe(righe: Iterable[Mapping[str, Any]], *,
                     conservatore_ambiente: Mapping[str, Any] | None = None,
                     colonne_classe: Mapping[str, Any] | None = None
                     ) -> dict[Livello, dict[str, Any]]:
    """Valori per livello dalle righe di ``SQL_VALORI_LIVELLI``.

    ``conservatore_ambiente``: valori del conservatore da variabili d'ambiente
    (la riga del database prevale); ``colonne_classe``: valori dalle colonne
    della classe (prevalgono sulle righe CLASSE)."""
    livelli: dict[Livello, dict[str, Any]] = {
        Livello.CONSERVATORE: dict(conservatore_ambiente or {}),
        Livello.ENTE: {},
        Livello.CLASSE: {},
    }
    for riga in righe:
        livelli[Livello(riga["livello"])][riga["codice"]] = json_o_none(riga["valore"])
    livelli[Livello.CLASSE].update(colonne_classe or {})
    return livelli


def valori_colonne_classe(formati_ammessi: Iterable[str], metadati_obbligatori: Iterable[str],
                          max_dimensione_mb: int | None) -> dict[str, Any]:
    """Livello CLASSE dalle colonne (§8.14: lista vuota = eredita dal livello superiore)."""
    valori: dict[str, Any] = {}
    formati = list(formati_ammessi)
    obbligatori = list(metadati_obbligatori)
    if formati:
        valori[FORMATI_AMMESSI] = formati
    if obbligatori:
        valori[METADATI_OBBLIGATORI] = obbligatori
    if max_dimensione_mb is not None:
        valori[CLASSE_DIMENSIONE_MAX_MB] = max_dimensione_mb
    return valori


def _campi(testo: str | None, origine: str) -> tuple[CampoProfilo, ...]:
    campi = json_o_none(testo)
    if not isinstance(campi, list):
        raise ProfiloNonValido(f"profilo {origine}: `campi` deve essere un elenco")
    return tuple(CampoProfilo.da_dict(c) for c in campi)


def profili_classe_da_righe(righe: Iterable[Mapping[str, Any]],
                            codice_classe: str) -> list[ProfiloMetadati]:
    return [ProfiloMetadati(codice=codice_classe, versione=int(r["versione"]),
                            valido_dal=r["valido_dal"], valido_al=r["valido_al"],
                            campi=_campi(r["campi"], str(r["id"])), id=str(r["id"]))
            for r in righe]


def profili_tipologia_da_righe(righe: Iterable[Mapping[str, Any]]
                               ) -> dict[str, list[ProfiloMetadati]]:
    """Versioni create dal conservatore, per codice di tipologia."""
    per_codice: dict[str, list[ProfiloMetadati]] = {}
    for r in righe:
        per_codice.setdefault(str(r["codice"]), []).append(ProfiloMetadati(
            codice=str(r["codice"]), versione=int(r["versione"]), valido_dal=r["valido_dal"],
            valido_al=r["valido_al"], campi=_campi(r["campi"], str(r["id"])), id=str(r["id"]),
            tipologia=str(r["codice"]), norm_ref=r.get("norm_ref"),
            adozione=str(r.get("adozione") or ADOZIONE_AUTOMATICA)))
    return per_codice


def accordi_da_righe(righe: Iterable[Mapping[str, Any]]) -> list[AccordoVersamento]:
    accordi = []
    for r in righe:
        valori = json_o_none(r["valori"]) or {}
        if not isinstance(valori, dict):
            raise ProfiloNonValido(f"accordo {r['id']}: `valori` deve essere un oggetto")
        accordi.append(AccordoVersamento(
            tenant_id=str(r["tenant_id"]), codice=str(r["codice"]), versione=int(r["versione"]),
            valido_dal=r["valido_dal"], classe_id=r["classe_id"], valori=valori,
            cessato=bool(r["cessato"]), descrizione=str(r["descrizione"] or ""), id=str(r["id"])))
    return accordi


_LIVELLI_TIPOLOGIA = (Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE)


def tipologia_effettiva(*, registro: RegistroImpostazioni,
                        livelli: Mapping[Livello, Mapping[str, Any]], giorno: date,
                        profili_tipologia_db: Mapping[str, Iterable[ProfiloMetadati]] | None,
                        versione: int = 0) -> ProfiloMetadati | None:
    """Profilo di tipologia applicato (revisione R11, T1, VD-P-114): si scorrono i
    livelli dal conservatore alla classe e il valore di un livello inferiore vale
    SOLO se il suo profilo chiede almeno gli obblighi di quello del livello sopra
    (``almeno_severo``); altrimenti resta quello superiore. Accordo e PdV non
    scelgono la tipologia. ``versione`` (della classe) si applica alla tipologia
    risultante. Funzione unica per gateway, anteprima, validatore e console."""
    db = profili_tipologia_db or {}
    voce = registro[PROFILO_TIPOLOGIA]
    codice = str(voce.predefinito or "")
    attuale = profilo_tipologia(codice, giorno, db.get(codice, ()))
    for livello in _LIVELLI_TIPOLOGIA:
        valori = livelli.get(livello) or {}
        if PROFILO_TIPOLOGIA not in valori or livello not in voce.livelli_ammessi():
            continue
        candidato_codice = str(voce.converti(valori[PROFILO_TIPOLOGIA], livello) or "")
        candidato = profilo_tipologia(candidato_codice, giorno, db.get(candidato_codice, ()))
        if almeno_severo(candidato, attuale):
            codice, attuale = candidato_codice, candidato
    if versione and codice:
        return profilo_tipologia(codice, giorno, db.get(codice, ()), versione=versione)
    return attuale


def prepara_ambiente(*, registro: RegistroImpostazioni,
                     livelli: Mapping[Livello, Mapping[str, Any]],
                     adesso: datetime, tenant_id: str | None, classe_id: str | None,
                     profili_classe: Iterable[ProfiloMetadati] = (),
                     profili_tipologia_db: Mapping[str, Iterable[ProfiloMetadati]] | None = None,
                     accordi: Iterable[AccordoVersamento] = (),
                     accordo_richiesto: str | None = None,
                     override: OverrideVersamento | None = None) -> AmbienteRegole:
    """Ambiente del motore per un contesto: sceglie l'accordo (``seleziona_accordo``),
    aggiunge i livelli ACCORDO e PDV, risolve ``PROFILO_TIPOLOGIA`` e compone il
    profilo di tipologia con quello della classe valido nel giorno.

    Solleva ``AccordoNonValido`` (accordo dell'indice), ``AccordiAmbigui``,
    ``ValoreImpostazioneNonValido`` o ``ProfiloNonValido`` (configurazione)."""
    giorno = giorno_locale(adesso)
    accordo = seleziona_accordo(accordi, giorno=giorno, classe_id=classe_id,
                                richiesto=accordo_richiesto)
    tutti: dict[Livello, Mapping[str, Any]] = dict(livelli)
    tutti[Livello.ACCORDO] = dict(accordo.valori) if accordo else {}
    tutti[Livello.PDV] = dict(override.valori) if override else {}
    contesto = ContestoRisoluzione(adesso=adesso, tenant_id=tenant_id, classe_id=classe_id,
                                   accordo_id=accordo.id if accordo else None)
    risolutore = Risolutore(registro, FornitoreDaDizionari(tutti))
    tipologia = None
    if PROFILO_TIPOLOGIA in registro:
        # R11 (D07): versione scelta dalla classe (0 = adozione automatica, come prima);
        # revisione T1: un livello inferiore non toglie né abbassa la tipologia
        versione = (int(risolutore.effettiva(contesto, PROFILO_TIPOLOGIA_VERSIONE).valore)
                    if PROFILO_TIPOLOGIA_VERSIONE in registro else 0)
        tipologia = tipologia_effettiva(registro=registro, livelli=tutti, giorno=giorno,
                                        profili_tipologia_db=profili_tipologia_db,
                                        versione=versione)
    profilo = componi_profili(tipologia, seleziona_profilo(profili_classe, giorno))
    return AmbienteRegole(risolutore, contesto, profilo, accordo=accordo, override=override)


__all__ = [
    "CODICI_IN_COLONNA_CLASSE",
    "SQL_ACCORDI_ENTE",
    "SQL_PROFILI_CLASSE",
    "SQL_PROFILI_TIPOLOGIA",
    "SQL_VALORI_LIVELLI",
    "accordi_da_righe",
    "json_o_none",
    "livelli_da_righe",
    "prepara_ambiente",
    "profili_classe_da_righe",
    "profili_tipologia_da_righe",
    "tipologia_effettiva",
    "valori_colonne_classe",
]
