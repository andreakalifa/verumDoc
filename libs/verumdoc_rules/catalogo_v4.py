# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/catalogo_v4.py — catalogo delle regole e registro delle
impostazioni del versamento, versione 4 (R11: decisione D07, VD-C-136;
REGOLE_SVILUPPO §8.4, §8.6, §8.9, §8.14).

Cosa cambia rispetto alla versione 3 (che resta per leggere le fotografie dei
PdV già versati):

  - nuova impostazione ``PROFILO_TIPOLOGIA_VERSIONE`` (solo livello CLASSE):
    0 = comportamento di prima (versione più alta valida fra quelle ad adozione
    automatica, cioè le v1 di R05 e le versioni del conservatore), N = la
    versione N del profilo di tipologia. Le versioni R11 riscontrate
    sull'Allegato 5 sono ad adozione ESPLICITA: una classe passa alla v2 con una
    bozza (anteprima dell'impatto sui PdV recenti, applicazione subito o
    programmata, R05/R07); i PdV già versati restano con la versione fotografata;
  - RGL-102 (metadati obbligatori) e RGL-106 (tipi del profilo) hanno il verso
    «più sicuro» BLOCCA > AVVISA > IGNORA (``solo_piu_severo``): l'ente e la
    classe possono solo renderle più severe del conservatore, mai spegnerle
    (con IGNORA un ente renderebbe facoltativi i campi obbligatori del profilo);
  - la fotografia porta anche ``PROFILO_TIPOLOGIA`` e
    ``PROFILO_TIPOLOGIA_VERSIONE``;
  - revisione R11 (T1, VD-P-114): ``PROFILO_TIPOLOGIA`` si sceglie solo per
    conservatore, ente e classe (mai per accordo o singolo versamento: un valore
    salvato a quei livelli si ignora) e un livello inferiore vale solo se chiede
    ALMENO gli obblighi di quello superiore (``lettura.tipologia_effettiva``); nella
    console chi non è il conservatore non toglie né abbassa il profilo.

Le funzioni delle regole sono le stesse: con un profilo R05 (senza obblighi a
condizione né strutture) il risultato non cambia (§8.14).
"""

from __future__ import annotations

from dataclasses import replace

from libs.verumdoc_rules.catalogo_v2 import PROFILO_TIPOLOGIA
from libs.verumdoc_rules.catalogo_v3 import CATALOGO_V3, IMPOSTAZIONI_V3
from libs.verumdoc_rules.impostazioni import Impostazione, Livello, TipoImpostazione
from libs.verumdoc_rules.regole import CatalogoRegole, crea_registro

VERSIONE_CATALOGO_V4 = "4"

PROFILO_TIPOLOGIA_VERSIONE = "PROFILO_TIPOLOGIA_VERSIONE"

#: Regole che con R11 si possono solo rendere più severe ai livelli del cliente.
_PIU_SEVERE = frozenset({"RGL-102", "RGL-106"})

IMPOSTAZIONE_VERSIONE = Impostazione(
    PROFILO_TIPOLOGIA_VERSIONE,
    TipoImpostazione.INTERO,
    predefinito=0,
    descrizione=(
        "Versione del profilo di tipologia (Allegato 5) applicata dalla classe; 0 = la più "
        "alta valida ad adozione automatica (comportamento precedente)"
    ),
    norm_ref="Linee guida AgID sul documento informatico (2021), Allegato 5",
    minimo=0,
    massimo=99,
    ambito_ammesso=frozenset({Livello.CLASSE}),
)

#: Livelli a cui si sceglie la tipologia (revisione T1: niente accordo né PdV).
AMBITO_TIPOLOGIA = frozenset({Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE})

IMPOSTAZIONI_V4: tuple[Impostazione, ...] = (
    *(replace(v, ambito_ammesso=AMBITO_TIPOLOGIA) if v.codice == PROFILO_TIPOLOGIA else v
      for v in IMPOSTAZIONI_V3),
    IMPOSTAZIONE_VERSIONE)

CATALOGO_V4 = CatalogoRegole(
    VERSIONE_CATALOGO_V4,
    [replace(r, solo_piu_severo=True) if r.codice in _PIU_SEVERE else r
     for r in CATALOGO_V3.regole()],
    fotografa=(*CATALOGO_V3.fotografa, PROFILO_TIPOLOGIA, PROFILO_TIPOLOGIA_VERSIONE),
    extra_fotografia=CATALOGO_V3.extra_fotografia,
)

REGISTRO_V4 = crea_registro(IMPOSTAZIONI_V4, CATALOGO_V4)

#: Catalogo e registro IN VIGORE per il versamento (da R11).
CATALOGO = CATALOGO_V4
REGISTRO = REGISTRO_V4

__all__ = [
    "AMBITO_TIPOLOGIA", "CATALOGO", "CATALOGO_V4", "IMPOSTAZIONE_VERSIONE", "IMPOSTAZIONI_V4",
    "PROFILO_TIPOLOGIA_VERSIONE", "REGISTRO", "REGISTRO_V4", "VERSIONE_CATALOGO_V4",
]
