# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firma_qualificata — registro dei fornitori di firma, sigillo e marca
temporale qualificati (R13a; VD-C-068, VD-C-124; decisioni VD-P-05, VD-P-07).

Una sola definizione (REGOLE §9.1) dei tipi di fornitore, dei campi della loro
configurazione (tabella ``fornitori_firma``, mai segreti) e dei segreti attesi
(Secret ``signature-secrets``, chiave ``FIRMA_SEGRETI_JSON``): la usano la
console (onboarding, validazione al salvataggio) e il servizio ``signature``
(costruzione degli adattatori).
"""
from libs.shared.firma_qualificata.configurazione import (
    CODICE_FORNITORE,
    RUOLI,
    SEGRETI_PER_TIPO,
    TIPI_PER_RUOLO,
    ConfigurazioneFornitoreNonValida,
    FornitoreRegistrato,
    leggi_segreti,
    segreti_presenti,
    valida_configurazione,
)

__all__ = [
    "CODICE_FORNITORE", "RUOLI", "SEGRETI_PER_TIPO", "TIPI_PER_RUOLO",
    "ConfigurazioneFornitoreNonValida", "FornitoreRegistrato", "leggi_segreti",
    "segreti_presenti", "valida_configurazione",
]
