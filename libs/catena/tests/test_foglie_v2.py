# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Foglie di formato 2 (R05, contratto Q3): ciclo di vita, audit e interpretazione."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from libs.catena.canonico import json_canonico
from libs.catena.foglie import (
    TIPO_ANNULLAMENTO,
    TIPO_AUDIT,
    TIPO_PDA,
    TIPO_RETTIFICA,
    TIPO_RIVERSAMENTO,
    TIPO_SCARTO,
    FogliaNonValida,
    analizza_foglia_registro,
    controlla_riferimento,
    dati_foglia_audit,
    dati_foglia_ciclo_vita,
    dati_foglia_registro,
    dati_foglia_riversamento,
    stato_documento,
)

ORA = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
ENTE = "00000000-0000-0000-0000-000000000001"
ID = "11111111-2222-3333-4444-555555555555"
PDA = "urn:verumdoc:pda:x"
VERBALE = hashlib.sha256(b"verbale").hexdigest()


def _scarto(**altro) -> bytes:
    argomenti = {"foglia_id": ID, "pda": PDA, "ente": ENTE,
                 "riferimento": {"pratica": "P-1", "verbale": "registro/verbali/1.pdf"},
                 "impronta_documento_pratica": VERBALE, "registrato_il": ORA, **altro}
    return dati_foglia_ciclo_vita(TIPO_SCARTO, **argomenti)


def test_foglia_scarto_canonica_con_v2_e_tipo():
    dati = _scarto()
    oggetto = json.loads(dati)
    assert oggetto["v"] == 2 and oggetto["tipo"] == "scarto"
    assert json_canonico(oggetto) == dati
    assert oggetto["registrato_il"] == "2026-10-23T10:00:00.000000Z"
    # tombstone: nessun dato del contenuto, solo pratica e impronta del verbale
    assert set(oggetto) == {"v", "tipo", "id", "pda", "ente", "riferimento",
                            "impronta_documento_pratica", "registrato_il"}


@pytest.mark.parametrize("tipo", [TIPO_SCARTO, TIPO_ANNULLAMENTO, TIPO_RETTIFICA])
def test_analizza_tipi_ciclo_vita(tipo):
    dati = dati_foglia_ciclo_vita(tipo, foglia_id=ID, pda=PDA, ente=ENTE,
                                  riferimento={"pratica": "P-9"},
                                  impronta_documento_pratica=VERBALE, registrato_il=ORA)
    foglia = analizza_foglia_registro(dati)
    assert (foglia.versione, foglia.tipo, foglia.pda) == (2, tipo, PDA)
    assert foglia.ciclo_vita and not foglia.prova_conservazione


def test_analizza_formato_1_pda_e_riversamento():
    pda = analizza_foglia_registro(dati_foglia_registro(PDA, "ab" * 32, ENTE, ORA))
    assert (pda.versione, pda.tipo) == (1, TIPO_PDA) and pda.prova_conservazione
    riv = analizza_foglia_registro(dati_foglia_riversamento(PDA, "cd" * 32, ENTE, ORA))
    assert riv.tipo == TIPO_RIVERSAMENTO and riv.prova_conservazione


def test_analizza_audit_non_riferito_a_un_pda():
    dati = dati_foglia_audit(foglia_id=ID, segmento="registro/audit/a.jsonl",
                             impronta_segmento="ee" * 32, impronta_precedente=None,
                             radice_righe="ff" * 32, righe=3, periodo_da=ORA,
                             periodo_a=ORA, registrato_il=ORA)
    foglia = analizza_foglia_registro(dati)
    assert foglia.tipo == TIPO_AUDIT and foglia.pda is None
    assert not foglia.ciclo_vita and not foglia.prova_conservazione


@pytest.mark.parametrize("dati", [
    {"v": 3, "pda": PDA, "radice_pda": "ab" * 32},
    {"v": 2, "tipo": "trasferimento", "pda": PDA},
    {"v": 1, "tipo": "scarto", "pda": PDA},
    {"v": 1, "pda": PDA},  # formato 1 senza radice
])
def test_formato_o_tipo_sconosciuto_rifiutato(dati):
    with pytest.raises(FogliaNonValida):
        analizza_foglia_registro(json_canonico(dati))


def test_foglia_v2_con_campi_in_piu_o_in_meno_rifiutata():
    oggetto = json.loads(_scarto())
    oggetto["radice_pda"] = "ab" * 32  # una foglia di scarto non può sembrare un PdA
    with pytest.raises(FogliaNonValida, match="campi"):
        analizza_foglia_registro(json_canonico(oggetto))
    del oggetto["radice_pda"], oggetto["riferimento"]
    with pytest.raises(FogliaNonValida):
        analizza_foglia_registro(json_canonico(oggetto))


def test_foglia_non_canonica_rifiutata():
    testo = _scarto().decode().replace('"v":2', '"v": 2')
    with pytest.raises(FogliaNonValida, match="canonica"):
        analizza_foglia_registro(testo)


@pytest.mark.parametrize("riferimento", [
    {}, {"pratica": ""}, {"pratica": 1}, {"pratica": "P", "Maiuscola": 1},
    {"pratica": "P", "x": 1.5}, {"pratica": "P", "x": {"annidato": 1}},
    {"pratica": "P", **{f"k{i}": i for i in range(16)}},
    {"pratica": "P", "x": "a" * 501},
])
def test_riferimento_non_valido(riferimento):
    with pytest.raises(FogliaNonValida):
        controlla_riferimento(riferimento)


def test_stato_documento_scarto_invalida_e_rettifica_no():
    rettifica = analizza_foglia_registro(dati_foglia_ciclo_vita(
        TIPO_RETTIFICA, foglia_id=ID, pda=PDA, ente=ENTE, riferimento={"pratica": "R-1"},
        impronta_documento_pratica=VERBALE, registrato_il=ORA))
    solo_rettifica = stato_documento(PDA, [(rettifica, 7)])
    assert solo_rettifica.codice == "RETTIFICATO" and not solo_rettifica.invalidato
    scarto = analizza_foglia_registro(_scarto())
    stato = stato_documento(PDA, [(scarto, 9), (rettifica, 7)])
    assert stato.invalidato and stato.codice == "SCARTATO"
    assert [e.indice for e in stato.eventi] == [7, 9]
    assert stato.invalidante.descrizione() == \
        "scartato il 2026-10-23T10:00:00.000000Z con pratica P-1"
    assert stato_documento(PDA, []).codice == "CONSERVATO"


def test_stato_documento_rifiuta_foglie_di_altri_pda():
    with pytest.raises(FogliaNonValida):
        stato_documento("urn:altro", [(analizza_foglia_registro(_scarto()), 1)])
