# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
I modelli di risposta dell'SDK corrispondono agli schemi dell'OpenAPI unica
(``docs/catalogo/openapi.yaml``, generata dalle app FastAPI vere): stessi campi
e nessun campo obbligatorio per l'SDK che il servizio possa omettere.

Copre i servizi che il test in-process del gateway non tocca (notification,
distribution). Se l'OpenAPI non c'è (SDK fuori dal repository) il test si salta.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from verumdoc_sdk import modelli as m

yaml = pytest.importorskip("yaml")
OPENAPI = Path(__file__).resolve().parents[2] / "docs/catalogo/openapi.yaml"

#: Modello dell'SDK → schema dell'OpenAPI unica.
CORRISPONDENZE = {
    m.Problema: "Problema",
    m.CampoNonValido: "CampoNonValido",
    m.AvvisoVersamento: "AvvisoVersamento",
    m.RispostaVersamento: "VersamentoResponse",
    m.StatoVersamento: "StatoPdvCicloVita",   # R05: + annullamento, rettifica
    m.AnnullamentoVersamento: "AnnullamentoPdv",
    m.RettificaVersamento: "RettificaPdv",
    m.Collegamenti: "Collegamenti",
    m.SessioneCaricamento: "SessioneCreata",
    m.StatoCaricamento: "StatoCaricamento",
    m.ParteAccettata: "ParteAccettata",
    m.Risorsa: "Risorsa",
    m.Evento: "Evento",
    m.ElencoEventi: "ElencoEventi",
    m.Sottoscrizione: "Sottoscrizione",
    m.SottoscrizioneCreata: "SottoscrizioneCreata",
    m.ElencoSottoscrizioni: "ElencoSottoscrizioni",
    m.Consegna: "Consegna",
    m.ElencoConsegne: "ElencoConsegne",
    m.ConsegnaRiaccodata: "ConsegnaRiaccodata",
    m.DocumentoDistribuito: "DocumentoDistribuito",
    m.PacchettoZip: "PacchettoZip",
    m.PacchettoDistribuzione: "RispostaDistribuzione",
    m.RisultatoRicerca: "AIPSearchResult",
}


@pytest.fixture(scope="module")
def schemi():
    if not OPENAPI.exists():
        pytest.skip("docs/catalogo/openapi.yaml assente (SDK fuori dal repository)")
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]


def _campi(modello) -> tuple[set[str], set[str]]:
    nomi, obbligatori = set(), set()
    for nome, info in modello.model_fields.items():
        esterno = info.alias or nome
        nomi.add(esterno)
        if info.is_required():
            obbligatori.add(esterno)
    return nomi, obbligatori


@pytest.mark.parametrize("modello,schema", sorted(CORRISPONDENZE.items(),
                                                  key=lambda kv: kv[1]),
                         ids=lambda v: v if isinstance(v, str) else v.__name__)
def test_modello_sdk_uguale_allo_schema(modello, schema, schemi):
    assert schema in schemi, f"Schema {schema} assente dall'OpenAPI unica"
    proprieta = set(schemi[schema].get("properties", {}))
    richiesti = set(schemi[schema].get("required", []))
    nomi, obbligatori = _campi(modello)
    assert nomi == proprieta, f"{modello.__name__}: solo SDK {sorted(nomi - proprieta)}, " \
                              f"solo servizio {sorted(proprieta - nomi)}"
    assert obbligatori <= richiesti, f"{modello.__name__}: obbligatori per l'SDK ma " \
                                     f"facoltativi nel servizio {sorted(obbligatori - richiesti)}"


def test_enumerazioni_pdd(schemi):
    assert set(schemi["TipoPdD"]["enum"]) == {t.value for t in m.TipoPdD}
    assert set(schemi["ConsegnaPdD"]["enum"]) == {c.value for c in m.ConsegnaPdD}
