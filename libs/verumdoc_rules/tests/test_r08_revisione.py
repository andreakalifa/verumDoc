# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Revisione R08 — X1 (INDETERMINATE mai valida a CRITTOGRAFICA/CON_REVOCHE,
concessione AVVISA solo del conservatore e solo per i PdV) e X2 (effetti di
RGL-107/108/109/113 con verso «più sicuro»: l'ente può solo renderli più severi)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from libs.verumdoc_rules.catalogo_v3 import CATALOGO_V3, REGISTRO_V3
from libs.verumdoc_rules.configurazione import Ambito, Attore, ModificaValore, piano_valori
from libs.verumdoc_rules.firme import (
    FIRME_INDETERMINATE_EFFETTO,
    FIRME_LIVELLO_VERIFICA,
    problemi_firme,
    valuta_firme,
)
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
    codice_limite,
)
from libs.verumdoc_rules.regole import AmbienteRegole, DatiVersamento, DocumentoDaValutare, Fase

CTX = ContestoRisoluzione(adesso=datetime(2026, 10, 31, tzinfo=UTC), tenant_id="t1",
                          classe_id="c1")
ENTE = Attore("ADMIN_TENANT", "t1", "u1")
LIV = FIRME_LIVELLO_VERIFICA


def _amb(livelli: dict[Livello, dict[str, Any]]) -> AmbienteRegole:
    return AmbienteRegole(Risolutore(REGISTRO_V3, FornitoreDaDizionari(livelli)), CTX)


def _esito(**kw: Any) -> dict[str, Any]:
    firma = {"formato": "PAdES", "indicazione": "TOTAL_PASSED", "firmatario": "Mario",
             "crittografia_valida": True, "catena_attendibile": True, "revoca": "BUONO",
             "motivi": [], **kw}
    return {"formato": "PAdES", "firme": [firma]}


SCADUTO = _esito(indicazione="INDETERMINATE", sottoindicazione="OUT_OF_BOUNDS_NO_POE",
                 revoca="NON_CONTROLLATO")
SHA1 = _esito(indicazione="INDETERMINATE", sottoindicazione="CRYPTO_CONSTRAINTS_FAILURE_NO_POE")
DOCMDP = _esito(indicazione="INDETERMINATE", sottoindicazione=None, revoca="NON_CONTROLLATO",
                motivi=["il PDF contiene modifiche non ammesse dopo la firma"])
REVOCATA = _esito(indicazione="INDETERMINATE", revoca="REVOCATO")


@pytest.mark.parametrize("livello", ["CRITTOGRAFICA", "CON_REVOCHE"])
@pytest.mark.parametrize("esito", [SCADUTO, SHA1, DOCMDP], ids=["scaduto", "sha1", "docmdp"])
def test_x1_indeterminate_mai_valida(esito: dict[str, Any], livello: str) -> None:
    problemi = problemi_firme(esito, livello)
    assert problemi and "indeterminata" in problemi[0]
    # la concessione sposta solo fra gli avvisi, la funzione rigorosa resta rigorosa
    bloccanti, avvisi = valuta_firme(esito, livello, indeterminate_con_avviso=True)
    assert bloccanti == [] and avvisi
    assert problemi_firme(_esito(), livello) == []


def test_x1_revocata_e_non_valida_mai_concesse() -> None:
    non_valida = _esito(indicazione="TOTAL_FAILED", crittografia_valida=False)
    catena = _esito(indicazione="INDETERMINATE", catena_attendibile=False)
    for esito in (REVOCATA, non_valida, catena):
        bloccanti, avvisi = valuta_firme(esito, "CRITTOGRAFICA", indeterminate_con_avviso=True)
        assert bloccanti and not avvisi


def test_x1_rilevata_invariata() -> None:
    assert problemi_firme(SCADUTO, "RILEVATA") == []


def test_x1_concessione_riservata_al_conservatore_predefinito_blocca() -> None:
    voce = REGISTRO_V3[FIRME_INDETERMINATE_EFFETTO]
    assert voce.predefinito == "BLOCCA"
    assert voce.livelli_ammessi() == frozenset({Livello.CONSERVATORE})
    piano = piano_valori(registro=REGISTRO_V3, attore=ENTE, ambito=Ambito(Livello.ENTE, ente="t1"),
                         modifiche=[ModificaValore(FIRME_INDETERMINATE_EFFETTO, "AVVISA")],
                         livelli={}, contesto=CTX)
    assert piano.bloccati
    # un valore scritto a mano al livello ENTE non vale
    a = _amb({Livello.ENTE: {FIRME_INDETERMINATE_EFFETTO: "AVVISA"}})
    assert a.valore(FIRME_INDETERMINATE_EFFETTO) == "BLOCCA"


def _dati() -> DatiVersamento:
    return DatiVersamento("c", (DocumentoDaValutare("d1", "PRINCIPALE", "a.pdf", firme=SCADUTO),
                                DocumentoDaValutare("d2", "ALLEGATO", "b.pdf",
                                                    firme=_esito())))


def test_x1_rgl_109_blocca_e_rgl_113_avvisa_con_concessione() -> None:
    a = _amb({Livello.CONSERVATORE: {LIV: "CRITTOGRAFICA"}})
    esito = CATALOGO_V3.valuta(_dati(), a, Fase.VERIFICA)
    assert [(r.regola, r.documento, r.effetto.value) for r in esito.rilievi] == [
        ("RGL-109", "d1", "BLOCCA")]
    a = _amb({Livello.CONSERVATORE: {LIV: "CRITTOGRAFICA", FIRME_INDETERMINATE_EFFETTO: "AVVISA"}})
    esito = CATALOGO_V3.valuta(_dati(), a, Fase.VERIFICA)
    assert [(r.regola, r.documento, r.effetto.value) for r in esito.rilievi] == [
        ("RGL-113", "d1", "AVVISA")]
    assert not esito.blocca


@pytest.mark.parametrize("regola", ["RGL-107", "RGL-108", "RGL-109", "RGL-113"])
def test_x2_ente_non_puo_rendere_meno_severo(regola: str) -> None:
    codice = f"REGOLA.{regola}.EFFETTO"
    voce = REGISTRO_V3[codice]
    assert voce.solo_riduzione and voce.scelte == ("BLOCCA", "AVVISA", "IGNORA")
    assert codice_limite(codice) in REGISTRO_V3
    # il conservatore a BLOCCA: ente e classe a IGNORA/AVVISA non valgono
    for livello in (Livello.ENTE, Livello.CLASSE):
        for valore in ("IGNORA", "AVVISA"):
            a = _amb({Livello.CONSERVATORE: {codice: "BLOCCA", LIV: "CON_REVOCHE"},
                      livello: {codice: valore}})
            effettivo = a.effettiva(codice)
            assert (effettivo.valore, effettivo.origine) == ("BLOCCA", Livello.CONSERVATORE)
    piano = piano_valori(registro=REGISTRO_V3, attore=ENTE,
                         ambito=Ambito(Livello.ENTE, ente="t1"),
                         modifiche=[ModificaValore(codice, "IGNORA")],
                         livelli={Livello.CONSERVATORE: {codice: "BLOCCA"}}, contesto=CTX)
    assert piano.bloccati
    # più severo sì
    a = _amb({Livello.CONSERVATORE: {codice: "AVVISA"}, Livello.CLASSE: {codice: "BLOCCA"}})
    assert a.valore(codice) == "BLOCCA"


def test_x2_rgl_109_ignora_dell_ente_non_disattiva_le_firme() -> None:
    """Scenario della revisione: conservatore CON_REVOCHE, ente RGL-109 = IGNORA."""
    a = _amb({Livello.CONSERVATORE: {LIV: "CON_REVOCHE"},
              Livello.ENTE: {"REGOLA.RGL-109.EFFETTO": "IGNORA",
                             "REGOLA.RGL-107.EFFETTO": "IGNORA"}})
    esito = CATALOGO_V3.valuta(_dati(), a, Fase.VERIFICA)
    assert esito.blocca and esito.rilievi[0].regola == "RGL-109"
    assert a.valore("REGOLA.RGL-107.EFFETTO") == "BLOCCA"
