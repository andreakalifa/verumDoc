# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/override.py — personalizzazione motivata sul singolo
versamento, livello PDV della catena (R05 Q1; REGOLE_SVILUPPO §8.2, §8.5, §15.7).

Il produttore può chiedere, per UN versamento, un valore diverso di alcune
impostazioni (es. l'effetto della regola RGL-103 sui metadati non previsti)
indicando nell'indice:

    "override": {"motivo": "<almeno 10 caratteri>", "valori": {"<codice>": <valore>}}

Regole (un solo punto, ``valida_override``):
  - motivo obbligatorio (almeno ``MOTIVO_MINIMO`` caratteri, §15.7);
  - solo impostazioni del registro che ammettono il livello PDV
    (``Impostazione.livelli_ammessi``): mai una voce ``locked`` o riservata al
    conservatore;
  - ogni valore rispetta il tipo della voce.
Ogni errore si raccoglie (il versante li vede tutti insieme, RGL-111).
L'override applicato entra nella fotografia del PdV con il motivo ed esce come
avviso RGL-110 nel rapporto di versamento.

Pacchetto puro.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from libs.verumdoc_rules.impostazioni import (
    ImpostazioneSconosciuta,
    Livello,
    RegistroImpostazioni,
    ValoreImpostazioneNonValido,
    valore_per_fotografia,
)

#: Lunghezza minima del motivo di una forzatura (REGOLE §15.7: scelta di servizio).
MOTIVO_MINIMO = 10


class OverrideNonAmmesso(ValueError):
    """La personalizzazione chiesta non è ammessa: ``problemi`` li elenca tutti."""

    def __init__(self, problemi: list[dict[str, str]]) -> None:
        super().__init__("; ".join(f"{p['codice']}: {p['motivo']}" for p in problemi))
        self.problemi = problemi


@dataclass(frozen=True)
class OverrideVersamento:
    """Valori del livello PDV, già convertiti nel tipo della voce, con il motivo."""

    motivo: str
    valori: Mapping[str, Any] = field(default_factory=dict)

    def riferimento(self) -> dict[str, Any]:
        """Forma fotografata nel PdV."""
        return {"motivo": self.motivo,
                "valori": {k: valore_per_fotografia(v) for k, v in sorted(self.valori.items())}}


def valida_override(grezzo: Mapping[str, Any] | None,
                    registro: RegistroImpostazioni) -> OverrideVersamento | None:
    """Override ammesso e convertito, None se assente; ``OverrideNonAmmesso`` altrimenti."""
    if grezzo is None:
        return None
    problemi: list[dict[str, str]] = []
    motivo = str(grezzo.get("motivo") or "").strip()
    if len(motivo) < MOTIVO_MINIMO:
        problemi.append({"codice": "motivo",
                         "motivo": f"motivo obbligatorio di almeno {MOTIVO_MINIMO} caratteri"})
    valori = grezzo.get("valori")
    if not isinstance(valori, Mapping) or not valori:
        problemi.append({"codice": "valori", "motivo": "indicare almeno un'impostazione"})
        valori = {}
    convertiti: dict[str, Any] = {}
    for codice, valore in sorted(valori.items()):
        try:
            voce = registro[str(codice)]
        except ImpostazioneSconosciuta:
            problemi.append({"codice": str(codice), "motivo": "impostazione sconosciuta"})
            continue
        if Livello.PDV not in voce.livelli_ammessi() or voce.riservata_conservatore:
            problemi.append({"codice": voce.codice,
                             "motivo": "non personalizzabile sul singolo versamento"})
            continue
        try:
            convertiti[voce.codice] = voce.converti(valore, Livello.PDV)
        except ValoreImpostazioneNonValido as exc:
            problemi.append({"codice": voce.codice, "motivo": exc.motivo})
    if problemi:
        raise OverrideNonAmmesso(problemi)
    return OverrideVersamento(motivo=motivo, valori=convertiti)


__all__ = ["MOTIVO_MINIMO", "OverrideNonAmmesso", "OverrideVersamento", "valida_override"]
