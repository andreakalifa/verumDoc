# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R16 (VD-C-158): condizioni fra campi diversi nel motore dei profili.

Solo gli obblighi dell'Allegato 5 già dichiarati «non controllati» nel riscontro
(``docs/conformita/riscontro_allegato5.md``): mittente per i documenti protocollati,
operatore quando ci sono tracciature, soggetti PAI/PAE solo in entrata o in uscita,
elemento «Annullamento» fra le tracciature di un documento annullato. Nella v3 del
documento informatico (adozione esplicita); v1 e v2 invariate (impronte).
"""
from __future__ import annotations

from typing import Any

import pytest

from libs.verumdoc_rules.profili import (
    ProfiloMetadati,
    ProfiloNonValido,
    RegolaIncrociata,
    almeno_severo,
)
from libs.verumdoc_rules.tipologie import differenze_impronte, profili_predefiniti

from .test_r11_profili import _metadati_di, _rilievi, _soggetti

V3 = {"PROFILO_TIPOLOGIA": "documento_informatico", "PROFILO_TIPOLOGIA_VERSIONE": 3}
PROTOCOLLO = "Protocollo Ordinario/Protocollo Emergenza"
TRACCIATURA = {"tipo_modifica": "Rettifica", "autore_modifica": "op1",
               "data_ora_modifica": "2026-11-21T10:00:00Z", "id_doc_versione_precedente": "D"}


def _incrociate(metadati: dict[str, Any], classe: dict[str, Any] | None = None) -> list[str]:
    descrizioni = {r.descrizione for r in profili_predefiniti("documento_informatico")[2]
                   .regole_incrociate}
    return [m for _r, _c, m in _rilievi(metadati, classe or V3)
            if any(d in m for d in descrizioni)]


def _con(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    return [*_soggetti(), *extra]


MITTENTE = {"ruolo": "Mittente", "tipo_soggetto": "PG", "denominazione": "Mittente di prova"}
OPERATORE = {"ruolo": "Operatore", "tipo_soggetto": "PF", "cognome": "Prova", "nome": "Op"}
PAI = {"ruolo": "Destinatario", "tipo_soggetto": "PAI", "codice_ipa": "ente_prova"}


def test_v3_esplicita_e_versioni_precedenti_invariate() -> None:
    v1, v2, v3 = profili_predefiniti("documento_informatico")
    assert v3.adozione == "esplicita" and len(v3.regole_incrociate) == 4
    assert not v1.regole_incrociate and not v2.regole_incrociate
    assert "regole_incrociate" not in v2.come_dict()
    assert differenze_impronte() == []
    # verso «più sicuro»: la v2 non è severa quanto la v3 (meno regole)
    assert almeno_severo(v3, v2) and not almeno_severo(v2, v3)


def test_v2_non_controlla_le_condizioni_incrociate() -> None:
    v2 = {"PROFILO_TIPOLOGIA": "documento_informatico", "PROFILO_TIPOLOGIA_VERSIONE": 2}
    protocollato = _metadati_di(tipo_registro=PROTOCOLLO, codice_registro="PG")
    assert _incrociate(protocollato, v2) == []


def test_mittente_obbligatorio_per_i_documenti_protocollati() -> None:
    senza = _metadati_di(tipo_registro=PROTOCOLLO, codice_registro="PG")
    assert any("Mittente" in m for m in _incrociate(senza))
    con = _metadati_di(tipo_registro=PROTOCOLLO, codice_registro="PG",
                       soggetti=_con(MITTENTE))
    assert _incrociate(con) == []
    # non protocollato: nessun obbligo di mittente
    assert _incrociate(_metadati_di()) == []


def test_operatore_obbligatorio_con_le_tracciature() -> None:
    senza = _metadati_di(versione_documento="2", tracciature_modifiche=[TRACCIATURA])
    assert any("Operatore" in m for m in _incrociate(senza))
    con = _metadati_di(versione_documento="2", tracciature_modifiche=[TRACCIATURA],
                       soggetti=_con(OPERATORE))
    assert _incrociate(con) == []


@pytest.mark.parametrize("flusso,ammesso", [("E", True), ("U", True), ("I", False)])
def test_pai_pae_solo_in_entrata_o_in_uscita(flusso: str, ammesso: bool) -> None:
    for tipo in ("PAI", "PAE"):
        soggetto = dict(PAI) if tipo == "PAI" else {"ruolo": "Destinatario",
                                                     "tipo_soggetto": "PAE",
                                                     "denominazione": "Ente estero"}
        esito = _incrociate(_metadati_di(tipologia_flusso=flusso, soggetti=_con(soggetto)))
        assert (esito == []) is ammesso, (tipo, flusso, esito)


def test_annullamento_tracciato() -> None:
    senza = _metadati_di(documento_annullato=True, tracciature_modifiche=[TRACCIATURA],
                         soggetti=_con(OPERATORE))
    assert any("Annullamento" in m for m in _incrociate(senza))
    con = _metadati_di(documento_annullato=True, soggetti=_con(OPERATORE),
                       tracciature_modifiche=[{**TRACCIATURA, "tipo_modifica": "Annullamento"}])
    assert _incrociate(con) == []


def test_messaggi_senza_valori() -> None:
    m = _incrociate(_metadati_di(tipo_registro=PROTOCOLLO, codice_registro="PG-SEGRETO"))
    assert m and all("PG-SEGRETO" not in x for x in m)


@pytest.mark.parametrize("regola, motivo", [
    ({"codice": "XAA", "elenco": "soggetti", "descrizione": "d"}, "richiede"),
    ({"codice": "XBB", "elenco": "soggetti", "descrizione": "d", "se": {"campo": "a", "in": ["1"]},
      "se_presente": "b", "richiede": {"campo": "ruolo", "in": ["x"]}}, "richiede"),
    ({"codice": "XCC", "elenco": "soggetti", "descrizione": "",
      "se_presente": "b", "richiede": {"campo": "ruolo", "in": ["x"]}}, "descrizione"),
    ({"codice": "minuscolo", "elenco": "s", "descrizione": "d"}, "codice"),
])
def test_forma_delle_regole(regola: dict, motivo: str) -> None:
    with pytest.raises(ProfiloNonValido, match=motivo):
        RegolaIncrociata.da_dict(regola)


def test_riferimenti_a_campi_inesistenti_rifiutati() -> None:
    v3 = profili_predefiniti("documento_informatico")[2]
    forma = v3.come_dict()
    forma["regole_incrociate"] = [{**forma["regole_incrociate"][0],
                                   "richiede": {"campo": "ruolo_inesistente", "in": ["x"]}}]
    with pytest.raises(ProfiloNonValido, match="inesistenti"):
        ProfiloMetadati.da_dict(forma)
    forma["regole_incrociate"] = [{**v3.come_dict()["regole_incrociate"][0], "elenco": "oggetto"}]
    with pytest.raises(ProfiloNonValido, match="elenco di strutture"):
        ProfiloMetadati.da_dict(forma)


def test_andata_e_ritorno_e_composizione_con_la_classe() -> None:
    from libs.verumdoc_rules.profili import CampoProfilo, componi_profili

    v3 = profili_predefiniti("documento_informatico")[2]
    assert ProfiloMetadati.da_dict(v3.come_dict()).come_dict() == v3.come_dict()
    classe = ProfiloMetadati(codice="classe", versione=1, valido_dal=v3.valido_dal, campi=(
        CampoProfilo.da_dict({"nome": "numero_pratica", "tipo": "stringa"}),))
    composto = componi_profili(v3, classe)
    assert composto is not None and composto.regole_incrociate == v3.regole_incrociate
