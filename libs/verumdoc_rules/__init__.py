# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules — motore delle regole di conservazione (decisione VD-P-01,
REGOLE_SVILUPPO §7.3 e §8).

Pacchetto PURO (solo libreria standard): nessun database, nessun orologio,
nessuna rete. Lo usano il gateway (versamento), e lo useranno il validatore
preventivo e la CLI dei produttori, così che la stessa regola dia lo stesso
esito ovunque (§9.2).

Moduli:
  - ``impostazioni``: registro delle impostazioni e catena di risoluzione unica
    ``Risolutore.effettiva(contesto, codice) -> (valore, origine)``;
  - ``profili``: profili di metadati per classe, versionati con validità;
  - ``regole``: catalogo delle regole con effetto BLOCCA/AVVISA/IGNORA;
  - ``catalogo_v1``: impostazioni e regole del versamento, versione 1 (fotografie R03-R04);
  - ``catalogo_v2``: versione 2 IN VIGORE (R05: ambiti, profilo di tipologia,
    accordo e override nella fotografia);
  - ``tipologie``: profili Allegato 5 per tipologia (dati in ``dati/profili``);
  - ``accordi``, ``override``: livelli ACCORDO e PDV della catena;
  - ``normative``, ``catalogo``: valori normativi ancora nel codice e catalogo
    completo per la console;
  - ``lettura``: query e conversione delle righe del database (puro);
  - ``configurazione``: piano di bozze, permessi e impronta del piano;
  - ``confronta``: confronto di due configurazioni sui PdV reali (anteprima e
    ``tools/confronta_regole.py``);
  - ``validatore``: validatore preventivo (API e CLI, anche senza rete).
"""

from __future__ import annotations

from libs.verumdoc_rules.accordi import AccordiAmbigui, AccordoNonValido, AccordoVersamento
from libs.verumdoc_rules.catalogo_v1 import CATALOGO_V1, REGISTRO_V1, VERSIONE_CATALOGO
from libs.verumdoc_rules.catalogo_v2 import CATALOGO_V2, REGISTRO_V2
from libs.verumdoc_rules.catalogo_v3 import CATALOGO_V3, REGISTRO_V3
from libs.verumdoc_rules.catalogo_v4 import CATALOGO, CATALOGO_V4, REGISTRO, REGISTRO_V4
from libs.verumdoc_rules.impostazioni import (
    ASSENTE,
    ContestoRisoluzione,
    FornitoreDaDizionari,
    FornitoreValori,
    Impostazione,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    TipoImpostazione,
    ValoreEffettivo,
    ValoreImpostazioneNonValido,
    effettiva,
)
from libs.verumdoc_rules.override import OverrideNonAmmesso, OverrideVersamento, valida_override
from libs.verumdoc_rules.profili import (
    CampoProfilo,
    ProfiloMetadati,
    ProfiloNonValido,
    TipoCampo,
    seleziona_profilo,
)
from libs.verumdoc_rules.regole import (
    AmbienteRegole,
    CatalogoRegole,
    DatiVersamento,
    DocumentoDaValutare,
    Effetto,
    EsitoRegole,
    Fase,
    Regola,
    Rilievo,
    Riscontro,
)

__all__ = [
    "ASSENTE",
    "AccordiAmbigui",
    "AccordoNonValido",
    "AccordoVersamento",
    "CATALOGO",
    "CATALOGO_V2",
    "CATALOGO_V3",
    "CATALOGO_V4",
    "OverrideNonAmmesso",
    "OverrideVersamento",
    "REGISTRO",
    "REGISTRO_V2",
    "REGISTRO_V3",
    "REGISTRO_V4",
    "valida_override",
    "AmbienteRegole",
    "CATALOGO_V1",
    "CampoProfilo",
    "CatalogoRegole",
    "ContestoRisoluzione",
    "DatiVersamento",
    "DocumentoDaValutare",
    "Effetto",
    "EsitoRegole",
    "Fase",
    "FornitoreDaDizionari",
    "FornitoreValori",
    "Impostazione",
    "Livello",
    "ProfiloMetadati",
    "ProfiloNonValido",
    "REGISTRO_V1",
    "RegistroImpostazioni",
    "Regola",
    "Rilievo",
    "Riscontro",
    "Risolutore",
    "TipoCampo",
    "TipoImpostazione",
    "VERSIONE_CATALOGO",
    "ValoreEffettivo",
    "ValoreImpostazioneNonValido",
    "effettiva",
    "seleziona_profilo",
]
