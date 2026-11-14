# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme — verifica delle firme con verificatori intercambiabili
(R08, decisione D01; VD-C-073, VD-C-120, VD-C-141).

Vedi ``interfaccia.py`` per il contratto e ``esito.py`` per la forma
normalizzata dell'esito.
"""
from libs.shared.firme.configurazione import (
    crea_verificatore,
    imposta_verificatore,
    verificatore,
)
from libs.shared.firme.esito import (
    Certificato,
    EsitoVerifica,
    FirmaVerificata,
    FormatoFirma,
    Indicazione,
    LivelloVerifica,
    StatoRevoca,
    in_parole,
)
from libs.shared.firme.interfaccia import (
    ErroreVerificatoreNonDisponibile,
    Verificatore,
    verifica_al_livello,
)
from libs.shared.firme.rilevazione import RilevatoreFirme, rileva, rileva_formato_firma

__all__ = [
    "Certificato", "ErroreVerificatoreNonDisponibile", "EsitoVerifica", "FirmaVerificata",
    "FormatoFirma", "Indicazione", "LivelloVerifica", "RilevatoreFirme", "StatoRevoca",
    "Verificatore", "crea_verificatore", "imposta_verificatore", "in_parole", "rileva",
    "rileva_formato_firma", "verifica_al_livello", "verificatore",
]
