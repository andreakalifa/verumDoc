# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/catalogo_v2.py — catalogo delle regole e registro delle
impostazioni del versamento, versione 2 (R05 Q1; REGOLE_SVILUPPO §8.1-§8.6,
§8.8, §8.14).

Cosa cambia rispetto alla versione 1 (che resta per leggere le fotografie dei
PdV già versati, §8.9):

  - stesse sei regole RGL-101…106 con le stesse funzioni e gli stessi effetti
    predefiniti (nessun cambio di comportamento per chi c'è già, §8.14);
  - ogni voce dichiara dove si personalizza (``ambito_ammesso``): l'effetto di
    RGL-103 (metadati non previsti) si può scegliere anche sul singolo PdV;
    la fine della transizione dell'idempotenza solo per conservatore ed ente;
  - nuova impostazione ``PROFILO_TIPOLOGIA``: la tipologia dell'Allegato 5 il
    cui profilo di metadati si compone con quello della classe (predefinito
    vuoto = nessuna tipologia, come prima);
  - la fotografia del PdV porta anche ``accordo`` e ``override`` (livelli
    ACCORDO e PDV della catena).

Il gateway versa con questo catalogo; la verifica asincrona legge la
fotografia (qualunque versione).
"""

from __future__ import annotations

from dataclasses import replace

from libs.verumdoc_rules.catalogo_v1 import (
    CATALOGO_V1,
    CLASSE_DIMENSIONE_MAX_MB,
    FORMATI_AMMESSI,
    IDEMPOTENZA_OBBLIGATORIA,
    IDEMPOTENZA_TRANSIZIONE_FINO_AL,
    IMPOSTAZIONI_V1,
    METADATI_MINIMI_PRINCIPALE,
    METADATI_OBBLIGATORI,
)
from libs.verumdoc_rules.impostazioni import (
    AMBITO_PREDEFINITO,
    Impostazione,
    Livello,
    TipoImpostazione,
    VersoSicuro,
)
from libs.verumdoc_rules.regole import CatalogoRegole, crea_registro
from libs.verumdoc_rules.tipologie import codici_tipologie

VERSIONE_CATALOGO_V2 = "2"

PROFILO_TIPOLOGIA = "PROFILO_TIPOLOGIA"

_ALLEGATO5 = "Linee guida AgID sul documento informatico (2021), Allegato 5"

_AMBITI: dict[str, frozenset[Livello] | None] = {
    METADATI_MINIMI_PRINCIPALE: None,            # locked: solo conservatore
    METADATI_OBBLIGATORI: AMBITO_PREDEFINITO,
    FORMATI_AMMESSI: AMBITO_PREDEFINITO,
    CLASSE_DIMENSIONE_MAX_MB: AMBITO_PREDEFINITO,
    IDEMPOTENZA_OBBLIGATORIA: None,              # locked
    IDEMPOTENZA_TRANSIZIONE_FINO_AL: frozenset({Livello.CONSERVATORE, Livello.ENTE}),
}

IMPOSTAZIONI_V2: tuple[Impostazione, ...] = (
    # R07 (D17): la fine della transizione per ente la proroga solo il conservatore
    *(replace(voce, ambito_ammesso=_AMBITI[voce.codice],
              riservata_conservatore=voce.codice == IDEMPOTENZA_TRANSIZIONE_FINO_AL,
              # W1: una fine della transizione più vicina è più sicura (dichiarativo)
              verso_sicuro=(VersoSicuro.MINORE if voce.codice == IDEMPOTENZA_TRANSIZIONE_FINO_AL
                            else voce.verso_sicuro))
      for voce in IMPOSTAZIONI_V1),
    Impostazione(
        PROFILO_TIPOLOGIA,
        TipoImpostazione.SCELTA,
        predefinito="",
        descrizione=(
            "Tipologia dell'Allegato 5 il cui profilo di metadati si applica insieme a "
            "quello della classe; vuoto = nessuna tipologia"
        ),
        norm_ref=_ALLEGATO5,
        scelte=("", *codici_tipologie()),
    ),
)

#: Effetti personalizzabili anche sul singolo versamento (livello PDV).
_EFFETTI_ANCHE_PDV = frozenset({"RGL-103"})

CATALOGO_V2 = CatalogoRegole(
    VERSIONE_CATALOGO_V2,
    [
        replace(regola, ambito_ammesso=(AMBITO_PREDEFINITO | {Livello.PDV}
                                        if regola.codice in _EFFETTI_ANCHE_PDV else None))
        for regola in CATALOGO_V1.regole()
    ],
)

REGISTRO_V2 = crea_registro(IMPOSTAZIONI_V2, CATALOGO_V2)

#: Catalogo e registro in vigore fino a R07 (da R08: ``catalogo_v3``). I nomi
#: restano per compatibilità del codice che li importava da qui.
CATALOGO = CATALOGO_V2
REGISTRO = REGISTRO_V2

__all__ = [
    "CATALOGO",
    "CATALOGO_V2",
    "IMPOSTAZIONI_V2",
    "PROFILO_TIPOLOGIA",
    "REGISTRO",
    "REGISTRO_V2",
    "VERSIONE_CATALOGO_V2",
]
