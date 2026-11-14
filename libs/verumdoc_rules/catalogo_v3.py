# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/catalogo_v3.py — catalogo delle regole e registro delle
impostazioni del versamento, versione 3 (R08: decisioni D01 e D02;
REGOLE_SVILUPPO §8.4, §8.6, §8.9, §8.14).

Cosa cambia rispetto alla versione 2 (che resta per leggere le fotografie dei
PdV già versati):

  - catalogo dei formati ammessi (``catalogo_formati.py``): una voce
    ``FORMATO.<ID>.ESITO`` per famiglia dell'Allegato 2 e due regole:
      RGL-107 formato RIFIUTATO dal catalogo               FILE     BLOCCA
      RGL-108 formato AMMESSO CON AVVISO o DA CONVERTIRE   FILE     AVVISA
    Al versamento la famiglia si riconosce dal MIME (firma binaria); la verifica
    riapplica le due regole con il PUID (Siegfried) e la conformità PDF/A;
  - livello di verifica delle firme ``FIRME_LIVELLO_VERIFICA`` (``firme.py``) e
      RGL-109 firme insufficienti per il livello richiesto  VERIFICA BLOCCA
    valutata solo dalla verifica asincrona (validation), con l'esito
    normalizzato dei verificatori intercambiabili;
  - la fotografia del PdV porta anche il livello delle firme, gli esiti del
    catalogo dei formati e la versione del catalogo (``catalogo_formati``).

Revisione R08:
  - X2: gli effetti di RGL-107/108/109/113 hanno il verso «più sicuro»
    BLOCCA > AVVISA > IGNORA (``Regola.solo_piu_severo``): l'ente e la classe
    possono solo renderli più severi del conservatore (o del suo LIMITE per
    l'ente), mai disattivarli;
  - X1: ``FIRME_INDETERMINATE_EFFETTO`` (riservata al conservatore, predefinito
    BLOCCA) e
      RGL-113 firme INDETERMINATE ammesse con avviso       VERIFICA AVVISA
    che segnala le INDETERMINATE concesse dal conservatore (con BLOCCA le
    INDETERMINATE sono rilievi di RGL-109).

Predefiniti = comportamento precedente (§8.14): i formati già accettati sono
AMMESSO; il livello delle firme è RILEVATA (nessun rifiuto di firme valide o
no, solo le buste illeggibili).
"""

from __future__ import annotations

from collections.abc import Iterator

from libs.verumdoc_rules.catalogo_formati import (
    AVVISO,
    CATALOGO_FORMATI,
    DA_CONVERTIRE,
    RIFIUTATO,
    codici_formati,
    esito_formato,
    impostazioni_formati,
)
from libs.verumdoc_rules.catalogo_v2 import CATALOGO_V2, IMPOSTAZIONI_V2
from libs.verumdoc_rules.firme import (
    FIRME_INDETERMINATE_EFFETTO,
    FIRME_LIVELLO_VERIFICA,
    IMPOSTAZIONE_INDETERMINATE,
    IMPOSTAZIONE_LIVELLO,
    INDETERMINATE_AVVISA,
    riscontri_firme,
)
from libs.verumdoc_rules.regole import (
    AmbienteRegole,
    CatalogoRegole,
    DatiVersamento,
    DocumentoDaValutare,
    Effetto,
    Fase,
    Regola,
    Riscontro,
    crea_registro,
)

VERSIONE_CATALOGO_V3 = "3"
_ALLEGATO2 = CATALOGO_FORMATI.norm_ref


def _da_valutare(doc: DocumentoDaValutare) -> bool:
    # R09 (D24): l'atto di sistema (rettifica XML) è sempre ammesso
    return (doc.formato_rilevato is not None or doc.puid is not None) \
        and not doc.atto_di_sistema


def _esiti(dati: DatiVersamento, ambiente: AmbienteRegole, cercati: frozenset[str]
           ) -> Iterator[Riscontro]:
    for doc in dati.documenti:
        if not _da_valutare(doc):
            continue
        esito = esito_formato(puid=doc.puid, mime=doc.formato_rilevato,
                              pdfa_conforme=doc.pdfa_conforme, valore=ambiente.valore)
        if esito.esito in cercati:
            yield Riscontro(f"File {doc.nome_file}: {esito.motivo} "
                            f"(catalogo dei formati, {esito.voce.codice})",
                            campo="formato", documento=doc.id)


def _formato_rifiutato(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    return _esiti(dati, ambiente, frozenset({RIFIUTATO}))


def _formato_con_avviso(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    return _esiti(dati, ambiente, frozenset({AVVISO, DA_CONVERTIRE}))


def _firme_avvisi(ambiente: AmbienteRegole, dati: DatiVersamento,
                  avvisi: bool) -> Iterator[Riscontro]:
    livello = str(ambiente.valore(FIRME_LIVELLO_VERIFICA))
    concesse = ambiente.valore(FIRME_INDETERMINATE_EFFETTO) == INDETERMINATE_AVVISA
    for doc_id, _, messaggio in riscontri_firme(
            ((d.id, d.nome_file, d.firme) for d in dati.documenti), livello,
            indeterminate_con_avviso=concesse, avvisi=avvisi):
        yield Riscontro(messaggio, campo="firma", documento=doc_id)


def _firme(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    return _firme_avvisi(ambiente, dati, avvisi=False)


def _firme_indeterminate(dati: DatiVersamento, ambiente: AmbienteRegole
                         ) -> Iterator[Riscontro]:
    return _firme_avvisi(ambiente, dati, avvisi=True)


REGOLE_R08 = (
    Regola("RGL-107", "Formato rifiutato dal catalogo dei formati", Fase.FILE,
           Effetto.BLOCCA, _formato_rifiutato, norm_ref=_ALLEGATO2, solo_piu_severo=True),
    Regola("RGL-108", "Formato ammesso con avviso o da convertire", Fase.FILE,
           Effetto.AVVISA, _formato_con_avviso, norm_ref=_ALLEGATO2, solo_piu_severo=True),
    Regola("RGL-109", "Firme insufficienti per il livello di verifica richiesto",
           Fase.VERIFICA, Effetto.BLOCCA, _firme, norm_ref=IMPOSTAZIONE_LIVELLO.norm_ref,
           solo_piu_severo=True),
    Regola("RGL-113", "Firme indeterminate ammesse con avviso dal conservatore",
           Fase.VERIFICA, Effetto.AVVISA, _firme_indeterminate,
           norm_ref=IMPOSTAZIONE_INDETERMINATE.norm_ref, solo_piu_severo=True),
)

#: Regole che la verifica asincrona (validation) riapplica sul contenuto intero.
REGOLE_CONTENUTO = ("RGL-107", "RGL-108", "RGL-109", "RGL-113")

IMPOSTAZIONI_V3 = (*IMPOSTAZIONI_V2, IMPOSTAZIONE_LIVELLO, IMPOSTAZIONE_INDETERMINATE,
                   *impostazioni_formati())

CATALOGO_V3 = CatalogoRegole(
    VERSIONE_CATALOGO_V3, [*CATALOGO_V2.regole(), *REGOLE_R08],
    fotografa=(FIRME_LIVELLO_VERIFICA, FIRME_INDETERMINATE_EFFETTO, *codici_formati()),
    extra_fotografia={"catalogo_formati": CATALOGO_FORMATI.riferimento()},
)

REGISTRO_V3 = crea_registro(IMPOSTAZIONI_V3, CATALOGO_V3)

#: Catalogo e registro in vigore da R08 a R10 (da R11: ``catalogo_v4``).
CATALOGO = CATALOGO_V3
REGISTRO = REGISTRO_V3

__all__ = [
    "CATALOGO", "CATALOGO_V3", "IMPOSTAZIONI_V3", "REGISTRO", "REGISTRO_V3",
    "REGOLE_CONTENUTO", "VERSIONE_CATALOGO_V3",
]
