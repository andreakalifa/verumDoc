# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/catalogo.py — catalogo COMPLETO delle impostazioni mostrato
dalla console (R05 Q1; REGOLE_SVILUPPO §8.1, §9.9).

Unisce in un solo registro, senza duplicati di codice:
  - le impostazioni e gli effetti delle regole del versamento (``catalogo_v4``:
    da R08 anche il livello delle firme e il catalogo dei formati, da R11 la
    versione del profilo di tipologia della classe);
  - le quote per ente (``quote``);
  - i parametri del servizio portati in console in R07 (``parametri_servizio``);
  - i valori normativi ancora nel codice dei servizi (``normative``: sola lettura);
  - i registri forniti dal chiamante (es. la retention dei registri di
    ``libs.shared.db.partizioni``, che questo pacchetto puro non importa).
"""

from __future__ import annotations

from collections.abc import Iterable

from libs.verumdoc_rules.catalogo_v4 import REGISTRO_V4
from libs.verumdoc_rules.impostazioni import Impostazione, RegistroImpostazioni
from libs.verumdoc_rules.normative import REGISTRO_NORMATIVO
from libs.verumdoc_rules.parametri_servizio import REGISTRO_SERVIZIO
from libs.verumdoc_rules.quote import REGISTRO_QUOTE


def catalogo_impostazioni(extra: Iterable[Impostazione] = ()) -> RegistroImpostazioni:
    """Registro unico per la console; un codice ripetuto è un errore (ValueError)."""
    return RegistroImpostazioni([*REGISTRO_V4, *REGISTRO_QUOTE, *REGISTRO_SERVIZIO,
                                 *REGISTRO_NORMATIVO, *extra])


__all__ = ["catalogo_impostazioni"]
