# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lettura della fotografia delle regole del PdV (``pdv.regole_applicate``) per i
passi della verifica che dipendono dalla classe documentale.

Servizio: validation. Riferimenti: REGOLE_SVILUPPO §8.1 (nessun valore
normativo cablato), §8.3 (il pacchetto porta le sue regole: la verifica applica
quelle fotografate al versamento, non quelle in vigore oggi); piano R03 §3.

La fotografia è scritta dal gateway (motore ``libs/verumdoc_rules``, catalogo v1):
``{"catalogo_regole", "profilo_metadati", "impostazioni": {codice: {valore, origine}}}``.
Qui si leggono solo:

- ``CLASSE_DIMENSIONE_MAX_MB`` con l'effetto ``REGOLA.RGL-104.EFFETTO``: se la
  regola BLOCCA, il limite per file è quello della classe; se AVVISA o IGNORA il
  versamento l'ha accettato e la verifica non lo rifiuta;
- ``FORMATI_AMMESSI`` con l'effetto ``REGOLA.RGL-105.EFFETTO``: elenco vuoto o
  regola non bloccante = nessuna restrizione (come al versamento);
- ``METADATI_MINIMI_PRINCIPALE`` (R03 f1): l'elenco dei metadati minimi che il
  passo 6 controlla con la STESSA funzione della regola RGL-101 del gateway
  (``libs.verumdoc_rules.catalogo_v1.metadati_minimi_mancanti``), solo se
  ``REGOLA.RGL-101.EFFETTO`` era BLOCCA al versamento.

R05 (Q1): dal catalogo 2 la fotografia porta anche ``accordo`` (accordo di
versamento applicato: id, codice, versione, decorrenza) e ``override``
(personalizzazione motivata del PdV: motivo e valori). I loro valori sono già
nelle ``impostazioni`` con l'origine ACCORDO o PDV, quindi i limiti qui sopra li
rispettano senza altro; la verifica li riporta nei dettagli dei passi (RdV) e
rifiuta come illeggibile una fotografia in cui non hanno la forma attesa.

R08 (D01, D02): dal catalogo 3 la fotografia porta il livello di verifica delle
firme (``FIRME_LIVELLO_VERIFICA``), gli esiti del catalogo dei formati
(``FORMATO.<ID>.ESITO``) e gli effetti di RGL-107/108/109: si leggono in
``contenuto`` (valori della fotografia) e i passi 10 e 11 li applicano con il
motore. Per le fotografie dei cataloghi 1 e 2 (PdV versati prima di R08) le
regole del contenuto non erano in vigore: ``contenuto`` è None e i passi
registrano solo l'identificazione (REGOLE §8.10).

PdV senza fotografia (versati prima di R03): limite ``VALIDAZIONE_MAX_FILE_MB``
(predefinito 5120 MB) e formati ``FORMATI_AMMESSI`` del servizio, come in R02.
Una fotografia presente ma illeggibile NON si sostituisce con un valore
indovinato: ``RegoleNonLeggibili`` (il consumer la registra come VLD-802).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from libs.verumdoc_rules.catalogo_v1 import (
    METADATI_MINIMI_PRINCIPALE,
    minimi_fotografati,
    minimi_predefiniti,
)

MIB = 1024 * 1024

DIMENSIONE_MAX = "CLASSE_DIMENSIONE_MAX_MB"
FORMATI = "FORMATI_AMMESSI"
EFFETTO_DIMENSIONE = "REGOLA.RGL-104.EFFETTO"
EFFETTO_FORMATI = "REGOLA.RGL-105.EFFETTO"
EFFETTO_MINIMI = "REGOLA.RGL-101.EFFETTO"
BLOCCA = "BLOCCA"
EFFETTI = ("BLOCCA", "AVVISA", "IGNORA")

ORIGINE_SERVIZIO = "servizio_validation"


class RegoleNonLeggibili(Exception):
    """La fotografia delle regole del PdV c'è ma non è interpretabile."""


@dataclass(frozen=True)
class RegolePdv:
    """Parametri della verifica risolti per un PdV."""

    #: limite per file in byte; None = nessun limite (regola non bloccante)
    limite_byte_file: int | None
    #: da dove viene il limite (origine nella fotografia o servizio)
    origine_limite: str
    #: formati ammessi normalizzati; None = nessuna restrizione
    formati_ammessi: tuple[str, ...] | None
    origine_formati: str
    #: True se i valori vengono dalla fotografia del PdV
    da_fotografia: bool
    #: metadati minimi del documento principale (``METADATI_MINIMI_PRINCIPALE``):
    #: R03 f1, definizione unica nel motore delle regole (difetto 3 di c2)
    metadati_minimi: tuple[str, ...] = field(default_factory=minimi_predefiniti)
    origine_minimi: str = "predefinito_normativo"
    #: R05 Q1: accordo di versamento e personalizzazione del PdV fotografati
    accordo: dict[str, Any] | None = None
    override: dict[str, Any] | None = None
    catalogo: str | None = None
    #: R08: valori fotografati delle regole del contenuto (catalogo ≥ 3); None = non in vigore
    contenuto: dict[str, Any] | None = None

    def dettagli(self) -> dict[str, Any]:
        """Riepilogo per i dettagli dei passi (nessun dato del cliente)."""
        return {
            "limite_byte_file": self.limite_byte_file, "origine_limite": self.origine_limite,
            "formati_ammessi": (list(self.formati_ammessi)
                                if self.formati_ammessi is not None else None),
            "origine_formati": self.origine_formati, "da_fotografia": self.da_fotografia,
            "metadati_minimi": list(self.metadati_minimi), "origine_minimi": self.origine_minimi,
            "catalogo_regole": self.catalogo,
            "accordo": ({k: self.accordo.get(k) for k in ("codice", "versione", "valido_dal")}
                        if self.accordo else None),
            # del motivo e dei valori basta sapere che c'era (i valori sono nelle impostazioni)
            "override": sorted(self.override.get("valori") or {}) if self.override else None,
        }


def normalizza_mime(valore: str | None) -> str:
    """``Application/PDF; charset=x`` → ``application/pdf``."""
    return (valore or "").split(";", 1)[0].strip().lower()


def predefinite(*, limite_mb: int, formati: Sequence[str]) -> RegolePdv:
    return RegolePdv(
        limite_byte_file=int(limite_mb) * MIB, origine_limite=ORIGINE_SERVIZIO,
        formati_ammessi=tuple(sorted({normalizza_mime(f) for f in formati if normalizza_mime(f)})),
        origine_formati=ORIGINE_SERVIZIO, da_fotografia=False,
    )


def _voce(impostazioni: Mapping[str, Any], codice: str) -> tuple[Any, str] | None:
    voce = impostazioni.get(codice)
    if voce is None:
        return None
    if not isinstance(voce, Mapping) or "valore" not in voce:
        raise RegoleNonLeggibili(f"impostazione {codice} senza valore nella fotografia")
    return voce["valore"], str(voce.get("origine") or "")


def _effetto(impostazioni: Mapping[str, Any], codice: str) -> str:
    voce = _voce(impostazioni, codice)
    if voce is None:
        return BLOCCA  # effetto predefinito di RGL-101, RGL-104 e RGL-105 nel catalogo v1
    effetto = str(voce[0]).upper()
    if effetto not in EFFETTI:
        raise RegoleNonLeggibili(f"effetto {effetto!r} non valido per {codice}")
    return effetto


def regole_del_pdv(
    regole_applicate: Any, *, limite_mb_predefinito: int, formati_predefiniti: Sequence[str],
) -> RegolePdv:
    """Parametri della verifica dalla fotografia; predefiniti del servizio se assente."""
    base = predefinite(limite_mb=limite_mb_predefinito, formati=formati_predefiniti)
    if regole_applicate is None or regole_applicate == {}:
        return base
    if not isinstance(regole_applicate, Mapping):
        raise RegoleNonLeggibili("fotografia delle regole non è un oggetto")
    impostazioni = regole_applicate.get("impostazioni")
    if not isinstance(impostazioni, Mapping):
        raise RegoleNonLeggibili("fotografia delle regole senza impostazioni")

    # Dimensione massima per file (RGL-104)
    limite: int | None
    if _effetto(impostazioni, EFFETTO_DIMENSIONE) != BLOCCA:
        limite, origine_limite = None, f"{EFFETTO_DIMENSIONE}=non bloccante"
    else:
        voce = _voce(impostazioni, DIMENSIONE_MAX)
        if voce is None:
            limite, origine_limite = base.limite_byte_file, ORIGINE_SERVIZIO
        else:
            valore, origine = voce
            if isinstance(valore, bool) or not isinstance(valore, int) or valore < 1:
                raise RegoleNonLeggibili(f"{DIMENSIONE_MAX} non è un intero positivo")
            limite, origine_limite = valore * MIB, origine or "fotografia"

    # Formati ammessi (RGL-105)
    formati: tuple[str, ...] | None
    if _effetto(impostazioni, EFFETTO_FORMATI) != BLOCCA:
        formati, origine_formati = None, f"{EFFETTO_FORMATI}=non bloccante"
    else:
        voce = _voce(impostazioni, FORMATI)
        if voce is None:
            formati, origine_formati = None, "fotografia senza FORMATI_AMMESSI"
        else:
            valore, origine = voce
            if not isinstance(valore, list | tuple) or not all(isinstance(v, str) for v in valore):
                raise RegoleNonLeggibili(f"{FORMATI} non è un elenco di formati")
            normalizzati = tuple(sorted({normalizza_mime(v) for v in valore if normalizza_mime(v)}))
            formati = normalizzati or None  # vuoto = nessuna restrizione (catalogo v1)
            origine_formati = origine or "fotografia"

    # Metadati minimi (RGL-101): la stessa definizione del gateway, con l'elenco
    # fotografato al versamento; un elenco presente ma illeggibile non si indovina
    if _effetto(impostazioni, EFFETTO_MINIMI) != BLOCCA:
        # RGL-101 in avviso o ignorata al versamento: il gateway ha accettato il PdV
        # senza quei metadati, la verifica non lo rifiuta per lo stesso motivo
        minimi, origine_minimi = (), f"{EFFETTO_MINIMI}=non bloccante"
    elif impostazioni.get(METADATI_MINIMI_PRINCIPALE) is None:
        minimi, origine_minimi = minimi_predefiniti(), "predefinito_normativo"
    else:
        letti = minimi_fotografati(regole_applicate)
        if letti is None:
            raise RegoleNonLeggibili(f"{METADATI_MINIMI_PRINCIPALE} non è un elenco di nomi")
        minimi = letti
        origine_minimi = str((_voce(impostazioni, METADATI_MINIMI_PRINCIPALE) or ("", ""))[1]
                             or "fotografia")

    # Accordo e override (R05 Q1): assenti nelle fotografie del catalogo 1
    accordo = regole_applicate.get("accordo")
    if accordo is not None and (not isinstance(accordo, Mapping) or "codice" not in accordo):
        raise RegoleNonLeggibili("accordo della fotografia senza codice")
    override = regole_applicate.get("override")
    if override is not None and (not isinstance(override, Mapping)
                                 or not isinstance(override.get("valori"), Mapping)
                                 or not str(override.get("motivo") or "").strip()):
        raise RegoleNonLeggibili("override della fotografia senza motivo o valori")

    return RegolePdv(
        contenuto=_contenuto(impostazioni, regole_applicate.get("catalogo_regole")),
        limite_byte_file=limite, origine_limite=origine_limite,
        formati_ammessi=formati, origine_formati=origine_formati, da_fotografia=True,
        metadati_minimi=minimi, origine_minimi=origine_minimi,
        accordo=dict(accordo) if accordo else None,
        override=dict(override) if override else None,
        catalogo=str(regole_applicate.get("catalogo_regole") or "") or None,
    )


def _contenuto(impostazioni: Mapping[str, Any], catalogo: Any) -> dict[str, Any] | None:
    """Valori fotografati delle regole del contenuto (R08) per il motore; i codici
    fuori dal registro in vigore si ignorano, i valori non validi rendono la
    fotografia illeggibile (nessun valore indovinato)."""
    from libs.verumdoc_rules.catalogo_v3 import REGISTRO_V3
    from libs.verumdoc_rules.impostazioni import ValoreImpostazioneNonValido

    try:
        versione = int(str(catalogo or "0"))
    except ValueError as exc:
        raise RegoleNonLeggibili(f"versione del catalogo non valida: {catalogo!r}") from exc
    if versione < 3:
        return None
    valori: dict[str, Any] = {}
    for codice in impostazioni:
        if not (codice.startswith(("FORMATO.", "FIRME_")) or codice in (
                "REGOLA.RGL-107.EFFETTO", "REGOLA.RGL-108.EFFETTO", "REGOLA.RGL-109.EFFETTO",
                "REGOLA.RGL-113.EFFETTO")):
            continue
        if codice not in REGISTRO_V3:
            continue
        valore = _voce(impostazioni, codice)
        assert valore is not None
        try:
            REGISTRO_V3[codice].converti(valore[0])
        except ValoreImpostazioneNonValido as exc:
            raise RegoleNonLeggibili(str(exc)) from exc
        valori[codice] = valore[0]
    return valori
