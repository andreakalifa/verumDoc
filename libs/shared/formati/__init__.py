# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/formati — riconoscimento dei formati (R08, decisione D02; VD-C-074).

Configurazione dalle variabili d'ambiente (un solo punto, ``strumenti()``):

  FORMATI_RICONOSCITORE  siegfried | firma_binaria   (predefinito siegfried con
                                                      ripiego per firma binaria)
  SIEGFRIED_BIN          sf
  SIEGFRIED_HOME         cartella del file di firme (nell'immagine: /opt/siegfried)
  SIEGFRIED_FIRME        default.sig
  VERAPDF_BIN            verapdf (assente: ripiego «dichiarazione XMP»)
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from libs.shared.formati.pdfa import (
    DichiarazionePdfA,
    EsitoPdfA,
    ValidatorePdfA,
    ValidatorePdfAConRipiego,
    ValidatoreVeraPdf,
)
from libs.shared.formati.riconoscimento import (
    FormatoIdentificato,
    Riconoscitore,
    RiconoscitoreConRipiego,
    RiconoscitoreFirmaBinaria,
    RiconoscitoreNonDisponibile,
)
from libs.shared.formati.siegfried import RiconoscitoreSiegfried

_strumenti: tuple[Riconoscitore, ValidatorePdfA] | None = None


def crea_strumenti(ambiente: Mapping[str, str] | None = None
                   ) -> tuple[Riconoscitore, ValidatorePdfA]:
    env = os.environ if ambiente is None else ambiente
    scelta = env.get("FORMATI_RICONOSCITORE", "siegfried").strip().lower()
    binaria = RiconoscitoreFirmaBinaria()
    riconoscitore: Riconoscitore
    if scelta == "firma_binaria":
        riconoscitore = binaria
    elif scelta == "siegfried":
        riconoscitore = RiconoscitoreConRipiego(RiconoscitoreSiegfried(
            env.get("SIEGFRIED_BIN", "sf"), home=env.get("SIEGFRIED_HOME") or None,
            firme=env.get("SIEGFRIED_FIRME", "default.sig")), binaria)
    else:
        raise ValueError(f"FORMATI_RICONOSCITORE non valido: {scelta!r}")
    pdfa = ValidatorePdfAConRipiego(ValidatoreVeraPdf(env.get("VERAPDF_BIN", "verapdf")),
                                    DichiarazionePdfA())
    return riconoscitore, pdfa


def strumenti(ambiente: Mapping[str, str] | None = None
              ) -> tuple[Riconoscitore, ValidatorePdfA]:
    """Strumenti del processo, creati alla prima richiesta (Settings del servizio
    o variabili d'ambiente)."""
    global _strumenti
    if _strumenti is None:
        _strumenti = crea_strumenti(ambiente)
    return _strumenti


def imposta_strumenti(nuovi: tuple[Riconoscitore, ValidatorePdfA] | None) -> None:
    global _strumenti
    _strumenti = nuovi


__all__ = ["DichiarazionePdfA", "EsitoPdfA", "FormatoIdentificato", "Riconoscitore",
           "RiconoscitoreConRipiego", "RiconoscitoreFirmaBinaria", "RiconoscitoreNonDisponibile",
           "RiconoscitoreSiegfried", "ValidatorePdfA", "ValidatoreVeraPdf", "crea_strumenti",
           "imposta_strumenti", "strumenti"]
