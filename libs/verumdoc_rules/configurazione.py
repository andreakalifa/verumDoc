# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/configurazione.py — piano di una modifica di configurazione
(bozza → anteprima → applica), permessi e impronta del piano, in forma PURA
(R05 Q1; REGOLE_SVILUPPO §8.4, §8.5, §8.13, §13, §14.1, §14.9).

Il servizio onboarding legge i valori attuali dal database, chiama
``piano_valori`` / ``piano_profilo`` e, all'applicazione, ricalcola lo stesso
piano dentro il lock: se ``impronta`` è cambiata risponde 409 (CFG-409,
«PIANO_CAMBIATO»). L'impronta copre ciò che la modifica FA (valori prima e dopo
con l'origine, elementi bloccati, avvisi, destinatari delle proposte) e NON
l'impatto sui PdV recenti, che cambia a ogni versamento: l'impatto si mostra,
non si firma.

Permessi (contratto Q1, una sola funzione: ``motivo_blocco_ambito`` e
``motivo_blocco_voce``; REGOLE §14.9):
  - SUPER_ADMIN e RSC_CONSERVATORE: livelli CONSERVATORE ed ENTE di qualunque
    ente; SUPER_ADMIN anche il livello CLASSE delle classi di BASE del servizio
    (come l'API delle classi, che riserva a lui le classi di base);
  - ADMIN_TENANT: livelli ENTE, CLASSE e ACCORDO del PROPRIO ente, mai sulle
    classi di base, solo per voci non ``locked``, non riservate al conservatore
    e con il livello nell'``ambito_ammesso`` della voce;
  - livello PDV: non si configura qui; si chiede nel versamento (campo
    ``override`` dell'indice) con il motivo;
  - una voce che nessun servizio applica (``applicata_da`` None) non si modifica
    (§9.9).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from libs.verumdoc_rules.impostazioni import (
    ASSENTE,
    CATENA,
    LIVELLI_CLIENTE,
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Impostazione,
    ImpostazioneSconosciuta,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    ValoreEffettivo,
    ValoreImpostazioneNonValido,
    VersoSicuro,
    valore_per_fotografia,
)
from libs.verumdoc_rules.profili import CampoProfilo, ProfiloMetadati, ProfiloNonValido

RUOLI_CONSERVATORE = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})
RUOLI_ENTE = frozenset({"ADMIN_TENANT"})
#: Ruoli che leggono la configurazione del proprio ente (impostazioni, regole, profili).
RUOLI_LETTURA_ENTE = frozenset({"ADMIN_TENANT", "OPERATORE", "VIEWER"})
#: Motivo minimo di una bozza, di una risposta a una proposta (REGOLE §15.7).
MOTIVO_MINIMO = 10


class Risorsa(StrEnum):
    IMPOSTAZIONI = "impostazioni"
    REGOLE = "regole"
    PROFILO = "profilo"
    CLASSE = "classe"
    ACCORDO = "accordo"
    QUOTE = "quote"


#: Livelli a cui si applica ogni risorsa.
LIVELLI_PER_RISORSA: dict[Risorsa, frozenset[Livello]] = {
    Risorsa.IMPOSTAZIONI: frozenset({Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE,
                                     Livello.ACCORDO}),
    Risorsa.REGOLE: frozenset({Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE,
                               Livello.ACCORDO}),
    Risorsa.PROFILO: frozenset({Livello.CONSERVATORE, Livello.CLASSE}),
    Risorsa.CLASSE: frozenset({Livello.CLASSE}),
    Risorsa.ACCORDO: frozenset({Livello.ACCORDO}),
    Risorsa.QUOTE: frozenset({Livello.ENTE}),
}


@dataclass(frozen=True)
class Attore:
    """Chi chiede la modifica (dal token verificato)."""

    ruolo: str
    tenant_id: str | None
    user_id: str | None = None

    @property
    def conservatore(self) -> bool:
        return self.ruolo in RUOLI_CONSERVATORE


@dataclass(frozen=True)
class Ambito:
    """Dove si applica la modifica. ``classe_di_base``: classe del servizio (senza ente)."""

    livello: Livello
    ente: str | None = None
    classe: str | None = None
    classe_di_base: bool = False
    accordo: str | None = None

    def come_dict(self) -> dict[str, Any]:
        return {"livello": self.livello.value, "ente": self.ente, "classe": self.classe,
                "accordo": self.accordo}


def motivo_blocco_lettura(attore: Attore, ente: str | None) -> str | None:
    """None se l'attore può leggere la configurazione di quell'ente (None = conservatore)."""
    if attore.conservatore:
        return None
    if attore.ruolo not in RUOLI_LETTURA_ENTE:
        return f"il ruolo {attore.ruolo} non legge la configurazione"
    if ente is not None and ente != attore.tenant_id:
        return "ente non trovato"
    return None


MOTIVO_CONSERVATORE_CLASSI = (
    "il conservatore configura i livelli CONSERVATORE ed ENTE (e, solo il SUPER_ADMIN, le "
    "classi di base); classi e accordi dell'ente li configura l'amministratore dell'ente")
#: Voci che il conservatore può impostare anche sulle classi di un ente (revisione R11,
#: T1): solo lui riporta una classe a un profilo con meno obblighi, con motivo.
VOCI_TIPOLOGIA_DEL_CONSERVATORE = frozenset({"PROFILO_TIPOLOGIA", "PROFILO_TIPOLOGIA_VERSIONE"})


def motivo_blocco_ambito(attore: Attore, risorsa: Risorsa, ambito: Ambito) -> str | None:
    """None se l'attore può modificare la risorsa a quel livello (permessi Q1)."""
    if ambito.livello is Livello.PDV:
        return ("il livello PDV non si configura: si chiede nel versamento (campo «override» "
                "dell'indice) con il motivo")
    if ambito.livello not in LIVELLI_PER_RISORSA[risorsa]:
        ammessi = ", ".join(sorted(liv.value for liv in LIVELLI_PER_RISORSA[risorsa]))
        return f"la risorsa {risorsa.value} si configura ai livelli: {ammessi}"
    if ambito.livello in (Livello.ENTE, Livello.ACCORDO) and not ambito.ente:
        return "indicare l'ente"
    if ambito.livello is Livello.ACCORDO and not ambito.accordo:
        return "indicare il codice dell'accordo di versamento"
    if ambito.livello is Livello.CLASSE and not ambito.classe:
        return "indicare la classe documentale"
    if attore.conservatore:
        if ambito.livello in (Livello.CONSERVATORE, Livello.ENTE):
            return None
        if ambito.livello is Livello.CLASSE and ambito.classe_di_base \
                and attore.ruolo == "SUPER_ADMIN":
            return None
        if ambito.livello is Livello.CLASSE and risorsa is Risorsa.IMPOSTAZIONI:
            # revisione R11 (T1, VD-P-114): solo le voci della tipologia
            # (``motivo_blocco_voce``), per l'uscita da un'adozione con motivo
            return None
        return MOTIVO_CONSERVATORE_CLASSI
    if attore.ruolo not in RUOLI_ENTE:
        return f"il ruolo {attore.ruolo} non modifica la configurazione"
    if ambito.livello is Livello.CONSERVATORE:
        return "il livello del conservatore lo modifica solo il conservatore"
    if ambito.ente != attore.tenant_id:
        return "ente non trovato"
    if ambito.livello is Livello.CLASSE and ambito.classe_di_base:
        return ("le classi di base del servizio non si modificano: creare una classe "
                "dell'ente con lo stesso codice")
    if risorsa is Risorsa.QUOTE:
        return "le quote dell'ente le stabilisce il conservatore"
    return None


def motivo_blocco_voce(attore: Attore, ambito: Ambito, voce: Impostazione) -> str | None:
    """None se la voce si può valorizzare a quel livello da quell'attore."""
    if voce.applicata_da is None:
        return "valore registrato ma non ancora applicato da alcun servizio: non modificabile"
    ammessi = voce.livelli_ammessi()
    if ambito.livello not in ammessi:
        if voce.locked:
            return "valore imposto dalla norma: si stabilisce solo al livello del conservatore"
        return ("non personalizzabile a questo livello (ammessi: "
                + ", ".join(sorted(liv.value for liv in ammessi)) + ")")
    if not attore.conservatore and (voce.locked or voce.riservata_conservatore):
        return "valore stabilito dal conservatore"
    if attore.conservatore and ambito.livello is Livello.CLASSE and not ambito.classe_di_base \
            and voce.codice not in VOCI_TIPOLOGIA_DEL_CONSERVATORE:
        return MOTIVO_CONSERVATORE_CLASSI
    return None


@dataclass(frozen=True)
class ModificaValore:
    """Una modifica normalizzata: ``eredita`` = torna al valore del livello superiore."""

    codice: str
    valore: Any = None
    eredita: bool = False


def normalizza_modifiche(risorsa: Risorsa, grezze: Iterable[Mapping[str, Any]]
                         ) -> tuple[list[ModificaValore], dict[str, Any], list[dict[str, str]]]:
    """Modifiche di valore, campi propri dell'accordo (``descrizione``, ``cessato``)
    e modifiche non comprensibili (bloccate con motivo)."""
    valori: list[ModificaValore] = []
    campi: dict[str, Any] = {}
    bloccati: list[dict[str, str]] = []
    for m in grezze:
        if not isinstance(m, Mapping):
            bloccati.append({"codice": "?", "motivo": "modifica non è un oggetto"})
            continue
        if risorsa is Risorsa.ACCORDO and "campo" in m:
            if m["campo"] in ("descrizione", "cessato"):
                campi[str(m["campo"])] = m.get("valore")
            else:
                bloccati.append({"codice": str(m["campo"]),
                                 "motivo": "campo dell'accordo sconosciuto (descrizione, cessato)"})
            continue
        if risorsa is Risorsa.REGOLE or "regola" in m:
            # effetto di una regola: {regola: "RGL-103", effetto: "AVVISA"} (anche nelle
            # bozze di classe e di accordo, insieme alle impostazioni)
            regola = m.get("regola")
            if not isinstance(regola, str) or not regola:
                bloccati.append({"codice": "?", "motivo": "indicare «regola» (es. RGL-103)"})
                continue
            codice = f"REGOLA.{regola}.EFFETTO"
            valore = m.get("effetto")
        else:
            grezzo = m.get("codice")
            if not isinstance(grezzo, str) or not grezzo:
                bloccati.append({"codice": "?", "motivo": "indicare «codice»"})
                continue
            codice = grezzo
            valore = m.get("valore")
        valori.append(ModificaValore(codice, valore, bool(m.get("eredita", False))))
    return valori, campi, bloccati


@dataclass
class RigaPiano:
    codice: str
    prima: Any
    dopo: Any
    origine_prima: str
    origine_dopo: str

    def come_dict(self) -> dict[str, Any]:
        return {"codice": self.codice, "prima": self.prima, "dopo": self.dopo,
                "origine_prima": self.origine_prima, "origine_dopo": self.origine_dopo}


@dataclass
class Piano:
    """Ciò che l'applicazione farà. ``livelli_dopo``: valori dei livelli con la
    modifica (per l'impatto). ``scritture``: cosa scrivere al livello dell'ambito
    (codice → valore convertito per JSON, o ASSENTE per «eredita»)."""

    modifiche: list[RigaPiano] = field(default_factory=list)
    bloccati: list[dict[str, str]] = field(default_factory=list)
    avvisi: list[str] = field(default_factory=list)
    livelli_dopo: dict[Livello, dict[str, Any]] = field(default_factory=dict)
    scritture: dict[str, Any] = field(default_factory=dict)
    dettagli: dict[str, Any] = field(default_factory=dict)

    def impronta(self, base: Mapping[str, Any]) -> str:
        """Impronta SHA-256 del piano (JSON canonico) con la ``base`` (bozza,
        versione, risorsa, ambito): cambia se cambia qualcosa che l'applicazione fa."""
        contenuto = {
            "base": dict(base),
            "modifiche": [r.come_dict() for r in self.modifiche],
            "bloccati": self.bloccati, "avvisi": self.avvisi, "dettagli": self.dettagli,
        }
        testo = json.dumps(contenuto, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
        return hashlib.sha256(testo.encode("utf-8")).hexdigest()


def _foto(valore: Any) -> Any:
    return None if valore is ASSENTE else valore_per_fotografia(valore)


def _tetto_superiore(registro: RegistroImpostazioni, livelli: Mapping[Livello, Mapping[str, Any]],
                     livello: Livello, contesto: ContestoRisoluzione, codice: str
                     ) -> ValoreEffettivo:
    """Valore meno sicuro che quel livello può dare a una voce con verso sicuro
    (R07, D14 e revisione W1). Al livello ENTE è il tetto del motore (limite del
    conservatore per l'ente, o valore del conservatore, o predefinito); ai
    livelli sotto l'ente è il valore effettivo SENZA quel livello e i più
    specifici."""
    if livello is Livello.ENTE:
        return Risolutore(registro, FornitoreDaDizionari(livelli)).tetto(contesto, codice)
    ordine = list(CATENA)
    sopra = {liv: v for liv, v in livelli.items() if ordine.index(liv) > ordine.index(livello)}
    return Risolutore(registro, FornitoreDaDizionari(sopra)).effettiva(contesto, codice)


def _motivo_oltre_tetto(voce: Impostazione, tetto: ValoreEffettivo, livello: Livello) -> str:
    da = ("limite del conservatore per l'ente" if livello is Livello.ENTE
          and tetto.origine is Livello.ENTE else f"livello {tetto.origine.value}")
    valore = valore_per_fotografia(tetto.valore)
    if voce.verso_sicuro is VersoSicuro.ORDINE_SCELTE:
        ammesse = voce.scelte[:voce.scelte.index(tetto.valore) + 1]
        return (f"a questo livello si può scegliere solo un valore più restrittivo: "
                f"ammessi {', '.join(ammesse)} ({da})")
    if voce.verso_sicuro is VersoSicuro.MAGGIORE:
        return f"a questo livello il valore si può solo aumentare: minimo {valore} ({da})"
    if voce.verso_sicuro is VersoSicuro.SOTTOINSIEME:
        return (f"a questo livello si possono scegliere solo codici fra quelli ammessi: "
                f"{', '.join(valore) or 'nessuno'} ({da})")
    if voce.verso_sicuro is VersoSicuro.SOVRAINSIEME:
        return (f"a questo livello si possono solo aggiungere codici: restano obbligatori "
                f"{', '.join(valore) or 'nessuno'} ({da})")
    return f"a questo livello il valore si può solo ridurre: massimo {valore} ({da})"


def piano_valori(*, registro: RegistroImpostazioni, attore: Attore, ambito: Ambito,
                 modifiche: Iterable[ModificaValore],
                 livelli: Mapping[Livello, Mapping[str, Any]], contesto: ContestoRisoluzione,
                 codici_senza_eredita: frozenset[str] = frozenset()) -> Piano:
    """Piano di modifiche di valori a un livello: prima/dopo con l'origine, bloccati
    con il motivo (permessi, voce, tipo del valore), avvisi.

    ``codici_senza_eredita``: codici che a quel livello hanno sempre un valore
    (colonne della classe) e non si possono riportare a «eredita»."""
    piano = Piano(livelli_dopo={liv: dict(v) for liv, v in livelli.items()})
    piano.livelli_dopo.setdefault(ambito.livello, {})
    prima_ris = Risolutore(registro, FornitoreDaDizionari(livelli))
    visti: set[str] = set()
    for m in modifiche:
        if m.codice in visti:
            piano.bloccati.append({"codice": m.codice, "motivo": "codice ripetuto nella bozza"})
            continue
        visti.add(m.codice)
        try:
            voce = registro[m.codice]
        except ImpostazioneSconosciuta:
            piano.bloccati.append({"codice": m.codice, "motivo": "impostazione sconosciuta"})
            continue
        motivo = motivo_blocco_voce(attore, ambito, voce)
        if motivo:
            piano.bloccati.append({"codice": m.codice, "motivo": motivo})
            continue
        attuale = livelli.get(ambito.livello, {}).get(m.codice, ASSENTE)
        if m.eredita:
            if m.codice in codici_senza_eredita:
                piano.bloccati.append({"codice": m.codice, "motivo": (
                    "a questo livello il valore sta nella classe documentale: indicare un "
                    "valore")})
                continue
            if attuale is ASSENTE:
                piano.avvisi.append(f"{m.codice}: già ereditato dal livello superiore")
            piano.livelli_dopo[ambito.livello].pop(m.codice, None)
            piano.scritture[m.codice] = ASSENTE
        else:
            try:
                convertito = voce.converti(m.valore, ambito.livello)
            except ValoreImpostazioneNonValido as exc:
                piano.bloccati.append({"codice": m.codice, "motivo": exc.motivo})
                continue
            if voce.solo_riduzione and ambito.livello in LIVELLI_CLIENTE:
                tetto = _tetto_superiore(registro, livelli, ambito.livello, contesto, m.codice)
                if not voce.entro_tetto(convertito, tetto.valore):
                    piano.bloccati.append({"codice": m.codice, "motivo": _motivo_oltre_tetto(
                        voce, tetto, ambito.livello)})
                    continue
            piano.livelli_dopo[ambito.livello][m.codice] = convertito
            piano.scritture[m.codice] = valore_per_fotografia(convertito)
            if voce.locked:
                piano.avvisi.append(f"{m.codice}: valore imposto dalla norma, vale per tutti "
                                    "gli enti e non è personalizzabile")
        prima = prima_ris.effettiva(contesto, m.codice)
        dopo = Risolutore(registro, FornitoreDaDizionari(piano.livelli_dopo)).effettiva(
            contesto, m.codice)
        if _foto(prima.valore) == _foto(dopo.valore) and prima.origine == dopo.origine:
            piano.avvisi.append(f"{m.codice}: nessun cambiamento")
        piano.modifiche.append(RigaPiano(m.codice, _foto(prima.valore), _foto(dopo.valore),
                                         prima.origine.value, dopo.origine.value))
    _controlla_vincoli(registro, piano, contesto)
    return piano


def _controlla_vincoli(registro: RegistroImpostazioni, piano: Piano,
                       contesto: ContestoRisoluzione) -> None:
    """Revisione R09 (Z4): vincoli su più voci (``VINCOLI_CONGIUNTI``) sui valori
    effettivi DOPO la modifica; una violazione blocca la bozza."""
    from libs.verumdoc_rules.parametri_servizio import VINCOLI_CONGIUNTI

    dopo = Risolutore(registro, FornitoreDaDizionari(piano.livelli_dopo))
    for vincolo in VINCOLI_CONGIUNTI:
        toccati = [c for c in vincolo.codici if c in piano.scritture]
        if not toccati or any(c not in registro for c in vincolo.codici):
            continue
        valori = {c: dopo.effettiva(contesto, c).valore for c in vincolo.codici}
        if not vincolo.ammesso(valori):
            piano.bloccati.append({"codice": toccati[0], "motivo": vincolo.motivo})


def piano_profilo(*, attuale: ProfiloMetadati | None, campi_nuovi: Any,
                  codice: str) -> Piano:
    """Piano di una nuova versione di un profilo (tipologia o classe): differenze
    per campo; i campi non validi sono bloccati con il motivo."""
    piano = Piano()
    if not isinstance(campi_nuovi, list) or not campi_nuovi:
        piano.bloccati.append({"codice": codice, "motivo": "indicare l'elenco dei campi"})
        return piano
    nuovi: dict[str, CampoProfilo] = {}
    for grezzo in campi_nuovi:
        try:
            campo = CampoProfilo.da_dict(grezzo if isinstance(grezzo, Mapping) else {})
        except ProfiloNonValido as exc:
            piano.bloccati.append({"codice": codice, "motivo": str(exc)})
            continue
        if campo.nome in nuovi:
            piano.bloccati.append({"codice": f"{codice}.{campo.nome}", "motivo": "campo ripetuto"})
            continue
        nuovi[campo.nome] = campo
    vecchi = {c.nome: c for c in attuale.campi} if attuale else {}
    for nome in sorted(set(vecchi) | set(nuovi)):
        prima = vecchi[nome].come_dict() if nome in vecchi else None
        dopo = nuovi[nome].come_dict() if nome in nuovi else None
        if prima == dopo:
            continue
        origine = f"versione {attuale.versione}" if attuale else "nessuna"
        piano.modifiche.append(RigaPiano(f"{codice}.{nome}", prima, dopo, origine,
                                         "nuova versione"))
        if dopo and dopo["obbligatorio"] and not dopo["calcolato"] \
                and not (prima and prima["obbligatorio"]):
            piano.avvisi.append(f"{nome}: diventa obbligatorio per i nuovi versamenti")
    if not piano.modifiche and not piano.bloccati:
        piano.avvisi.append(f"{codice}: nessun cambiamento rispetto alla versione in vigore")
    piano.scritture["campi"] = [c.come_dict() for c in nuovi.values()]
    return piano


MOTIVO_CAMPO_DI_TIPOLOGIA = (
    "campo dei profili di tipologia (Allegato 5): una classe non lo ridefinisce; per "
    "renderlo obbligatorio si usa METADATI_OBBLIGATORI")
MOTIVO_CAMPO_PROPRIO_FACOLTATIVO = (
    "un campo proprio della classe è facoltativo e non calcolato; per renderlo obbligatorio "
    "si usa METADATI_OBBLIGATORI")


def motivi_campi_classe(campi: Iterable[CampoProfilo], riservati: Iterable[str],
                        codice: str) -> list[dict[str, str]]:
    """R11 (D07): un profilo di CLASSE aggiunge solo campi FACOLTATIVI PROPRI.

    Bloccati: un campo con il nome di un campo dei profili di tipologia
    (``riservati``: tutte le versioni, così un cambio di tipologia non lo fa
    riemergere) e un campo obbligatorio, obbligatorio a condizione o calcolato.
    L'obbligo in più si dà con ``METADATI_OBBLIGATORI`` (verso «più severo»): una
    classe non rende mai facoltativo un campo obbligatorio della norma."""
    nomi_riservati = frozenset(riservati)
    bloccati = []
    for campo in campi:
        if campo.nome in nomi_riservati:
            bloccati.append({"codice": f"{codice}.{campo.nome}",
                             "motivo": MOTIVO_CAMPO_DI_TIPOLOGIA})
        elif campo.obbligatorio or campo.obbligatorio_se or campo.calcolato:
            bloccati.append({"codice": f"{codice}.{campo.nome}",
                             "motivo": MOTIVO_CAMPO_PROPRIO_FACOLTATIVO})
    return bloccati


def esito_versione_tipologia(codice: str, versione: int, versioni: Iterable[ProfiloMetadati],
                             giorno: date) -> tuple[str | None, str | None]:
    """R11: (motivo di blocco, avviso) per la scelta ``PROFILO_TIPOLOGIA_VERSIONE``
    della classe. 0 = adozione automatica (nessun controllo); N deve esistere per la
    tipologia scelta e non essere scaduta; se vale da una data futura è un avviso
    (la modifica va programmata da quella data)."""
    if not versione:
        return None, None
    if not codice:
        return "una versione del profilo si sceglie insieme a una tipologia (PROFILO_TIPOLOGIA)", \
            None
    scelta = next((p for p in versioni if p.versione == versione), None)
    if scelta is None:
        return f"la tipologia {codice} non ha la versione {versione}", None
    if scelta.valido_al is not None and scelta.valido_al < giorno:
        return f"la versione {versione} di {codice} è scaduta il {scelta.valido_al}", None
    if scelta.valido_dal > giorno:
        return None, (f"la versione {versione} di {codice} vale dal {scelta.valido_dal}: "
                      "programmare la modifica da quella data")
    return None, None


__all__ = [
    "MOTIVO_CONSERVATORE_CLASSI",
    "VOCI_TIPOLOGIA_DEL_CONSERVATORE",
    "MOTIVO_CAMPO_DI_TIPOLOGIA",
    "MOTIVO_CAMPO_PROPRIO_FACOLTATIVO",
    "esito_versione_tipologia",
    "motivi_campi_classe",
    "Ambito",
    "Attore",
    "LIVELLI_PER_RISORSA",
    "MOTIVO_MINIMO",
    "ModificaValore",
    "Piano",
    "RUOLI_CONSERVATORE",
    "RUOLI_ENTE",
    "RUOLI_LETTURA_ENTE",
    "Risorsa",
    "RigaPiano",
    "motivo_blocco_ambito",
    "motivo_blocco_lettura",
    "motivo_blocco_voce",
    "normalizza_modifiche",
    "piano_profilo",
    "piano_valori",
]
