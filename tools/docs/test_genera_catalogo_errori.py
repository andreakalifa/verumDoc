# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Il catalogo errori genera file derivati aggiornati e copre i codici usati."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genera_catalogo_errori as g  # noqa: E402


def test_file_derivati_aggiornati():
    """Fallisce se catalogo.yaml è cambiato senza rigenerare
    (python tools/docs/genera_catalogo_errori.py)."""
    assert g.non_aggiornati() == []
    assert g.main(["--verifica"]) == 0


def test_codici_usati_nei_servizi_tutti_in_catalogo():
    assert g.codici_fuori_catalogo() == {}


def test_verifica_scopre_un_file_non_aggiornato():
    dati = g.leggi_yaml()
    cambiato = copy.deepcopy(dati)
    cambiato["codici"]["VRS-102"]["titolo"] = "Titolo cambiato"
    assert set(g.non_aggiornati(cambiato)) == {g.DATI_PY, g.DOC_MD}


def test_markdown_ha_ogni_codice_e_ogni_famiglia():
    dati = g.leggi_yaml()
    md = g.genera_markdown(dati)
    for codice in dati["codici"]:
        assert f"| `{codice}` |" in md
    for famiglia in dati["famiglie"]:
        assert f"\n## {famiglia}\n" in md
    assert "*Nessun codice ancora definito.*" in md or all(
        any(v["famiglia"] == f for v in dati["codici"].values()) for f in dati["famiglie"])


def test_catalogo_non_valido_blocca_la_generazione():
    dati = copy.deepcopy(g.leggi_yaml())
    dati["codici"]["VRS-102"]["esito"] = "FORSE"
    with pytest.raises(SystemExit, match="esito"):
        g.file_attesi(dati)


def test_ricerca_dei_codici_nel_sorgente():
    sorgente = '''
raise HTTPException(404, {"type": "ABC-404", "title": "x"})
raise HTTPException(401, {"codice": "SIG-401", "messaggio": "x"})
raise ErroreApi("NUOVO-001", "x")
raise RifiutoVersamento(400, "VRS-999", "x")
class E(Exception):
    codice = "CLS-500"
algoritmo = "SHA-256"
numero = f"FASC-{1}"
_ritorno(url, spid_errore="SPID-099")
'''
    assert g.codici_nel_sorgente(sorgente) == {
        "ABC-404", "SIG-401", "NUOVO-001", "VRS-999", "CLS-500", "SPID-099"}


# ── Codici dismessi (R03 i1) ─────────────────────────────────────────

def test_codici_dismessi_dichiarati_nel_catalogo_e_nel_markdown():
    dati = g.leggi_yaml()
    attesi = {"VRS-103": "VRS-130", "VRS-112": "VRS-130", "PDND-RIFIUTO": "VRS-130",
              "PDND-502": "VRS-500", "RATE-001": "QTA-001"}
    md = g.genera_markdown(dati)
    for codice, sostituto in attesi.items():
        voce = dati["codici"][codice]
        assert voce["dismesso"] is True and voce["titolo"].startswith("Dismesso: vedi")
        assert sostituto in voce["sostituito_da"]
        riga = next(r for r in md.splitlines() if r.startswith(f"| `{codice}` |"))
        assert "*Dismesso: non più emesso" in riga and f"`{sostituto}`" in riga


def test_codici_dismessi_non_usati_nel_codice():
    assert g.codici_dismessi_usati() == {}


def test_codice_dismesso_usato_nel_codice_scoperto(tmp_path):
    (tmp_path / "services" / "x").mkdir(parents=True)
    (tmp_path / "libs" / "shared").mkdir(parents=True)
    (tmp_path / "services" / "x" / "m.py").write_text('raise ErroreApi("RATE-001")\n')
    assert g.codici_dismessi_usati(tmp_path) == {"RATE-001": ["services/x/m.py"]}


@pytest.mark.parametrize("modifica,frammento", [
    ({"dismesso": "si"}, "`dismesso` deve essere vero o falso"),
    ({"dismesso": True}, "inizia con «Dismesso»"),
    ({"sostituito_da": ["VRS-130"]}, "solo per un codice dismesso"),
    ({"dismesso": True, "titolo": "Dismesso: vedi X", "sostituito_da": ["NON-ESISTE"]},
     "non è nel catalogo"),
    ({"dismesso": True, "titolo": "Dismesso: vedi X", "sostituito_da": ["VRS-103"]},
     "a sua volta dismesso"),
])
def test_forma_della_dismissione_validata(modifica, frammento):
    dati = copy.deepcopy(g.leggi_yaml())
    dati["codici"]["VRS-102"].update(modifica)
    with pytest.raises(SystemExit, match=frammento):
        g.file_attesi(dati)
