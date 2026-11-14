# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R08 (D01, D02) — catalogo v3: livello di verifica delle firme (il conservatore
fissa il minimo, l'ente può solo alzarlo, anche per classe), RGL-109 con i tre
effetti; catalogo dei formati come impostazioni (RGL-107/108 con i tre effetti,
restrizioni dell'ente per classe, PDF/A); validatore preventivo per file."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from libs.verumdoc_rules.catalogo_formati import (
    CATALOGO_FORMATI,
    codice_esito,
    esito_formato,
)
from libs.verumdoc_rules.catalogo_v3 import CATALOGO_V3, REGISTRO_V3
from libs.verumdoc_rules.catalogo_v4 import REGISTRO_V4
from libs.verumdoc_rules.configurazione import Ambito, Attore, ModificaValore, piano_valori
from libs.verumdoc_rules.firme import FIRME_LIVELLO_VERIFICA, problemi_firme
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
    codice_limite,
)
from libs.verumdoc_rules.regole import (
    AmbienteRegole,
    DatiVersamento,
    DocumentoDaValutare,
    Fase,
)
from libs.verumdoc_rules.validatore import valida_versamento

CTX = ContestoRisoluzione(adesso=datetime(2026, 10, 31, tzinfo=UTC), tenant_id="t1",
                          classe_id="c1")
ENTE = Attore("ADMIN_TENANT", "t1", "u1")
LIV = FIRME_LIVELLO_VERIFICA


def _amb(livelli: dict[Livello, dict[str, Any]]) -> AmbienteRegole:
    # R11: registro in vigore (v4, superinsieme del v3) per il validatore preventivo
    return AmbienteRegole(Risolutore(REGISTRO_V4, FornitoreDaDizionari(livelli)), CTX)


def _firma(**kw: Any) -> dict[str, Any]:
    base = {"formato": "CAdES", "indicazione": "TOTAL_PASSED", "firmatario": "Mario",
            "crittografia_valida": True, "catena_attendibile": True, "revoca": "BUONO",
            "motivi": []}
    return {**base, **kw}


def _esito(*firme: dict[str, Any], formato: str = "CAdES") -> dict[str, Any]:
    return {"formato": formato, "firme": list(firme)}


def test_livello_firme_solo_verso_alto() -> None:
    assert REGISTRO_V3[LIV].predefinito == "RILEVATA"
    # il conservatore fissa CRITTOGRAFICA: l'ente non può scendere a RILEVATA
    livelli = {Livello.CONSERVATORE: {LIV: "CRITTOGRAFICA"}}
    piano = piano_valori(registro=REGISTRO_V3, attore=ENTE,
                         ambito=Ambito(Livello.ENTE, ente="t1"),
                         modifiche=[ModificaValore(LIV, "RILEVATA")], livelli=livelli, contesto=CTX)
    assert piano.bloccati and "più restrittivo" in piano.bloccati[0]["motivo"]
    piano = piano_valori(registro=REGISTRO_V3, attore=ENTE,
                         ambito=Ambito(Livello.CLASSE, ente="t1", classe="c1"),
                         modifiche=[ModificaValore(LIV, "CON_REVOCHE")], livelli=livelli,
                         contesto=CTX)
    assert not piano.bloccati
    # anche una riga scritta a mano più bassa non vale: risolve al minimo
    a = _amb({Livello.CONSERVATORE: {LIV: "CRITTOGRAFICA"}, Livello.CLASSE: {LIV: "NESSUNA"}})
    assert a.valore(LIV) == "CRITTOGRAFICA"
    a = _amb({Livello.CONSERVATORE: {LIV: "CRITTOGRAFICA"}, Livello.CLASSE: {LIV: "CON_REVOCHE"}})
    assert a.valore(LIV) == "CON_REVOCHE"
    assert codice_limite(LIV) in REGISTRO_V3


def test_problemi_firme_per_livello() -> None:
    valida = _esito(_firma())
    senza_revoca = _esito(_firma(indicazione="INDETERMINATE", revoca="SCONOSCIUTO",
                                 sottoindicazione="TRY_LATER"))
    non_valida = _esito(_firma(indicazione="TOTAL_FAILED", crittografia_valida=False,
                               motivi=["il documento è stato modificato dopo la firma"]))
    revocata = _esito(_firma(indicazione="INDETERMINATE", revoca="REVOCATO"))
    catena = _esito(_firma(indicazione="INDETERMINATE", catena_attendibile=False))
    illeggibile = {"formato": "CAdES", "firme": [], "motivo": "busta non leggibile"}
    non_firmato = {"formato": "NESSUNO", "firme": []}
    attesi = {  # livello → esiti che danno almeno un problema
        "NESSUNA": set(),
        "RILEVATA": {"illeggibile"},
        # revisione X1: INDETERMINATE non è mai valida a CRITTOGRAFICA (il client
        # DSS porta a TOTAL_PASSED la sola revoca non richiesta a questo livello)
        "CRITTOGRAFICA": {"illeggibile", "non_valida", "revocata", "catena", "senza_revoca"},
        "CON_REVOCHE": {"illeggibile", "non_valida", "revocata", "catena", "senza_revoca"},
    }
    casi = {"valida": valida, "senza_revoca": senza_revoca, "non_valida": non_valida,
            "revocata": revocata, "catena": catena, "illeggibile": illeggibile,
            "non_firmato": non_firmato}
    for livello, con_problemi in attesi.items():
        trovati = {n for n, e in casi.items() if problemi_firme(e, livello)}
        assert trovati == con_problemi, livello
    assert "modificato dopo la firma" in problemi_firme(non_valida, "CRITTOGRAFICA")[0]


@pytest.mark.parametrize("effetto", ["BLOCCA", "AVVISA", "IGNORA"])
def test_rgl_109_tre_effetti(effetto: str) -> None:
    a = _amb({Livello.CONSERVATORE: {LIV: "CRITTOGRAFICA", "REGOLA.RGL-109.EFFETTO": effetto}})
    dati = DatiVersamento("c", (
        DocumentoDaValutare("d1", "PRINCIPALE", "a.p7m", firme=_esito(_firma(
            indicazione="TOTAL_FAILED", crittografia_valida=False))),
        DocumentoDaValutare("d2", "ALLEGATO", "b.pdf", firme=_esito(_firma())),
        DocumentoDaValutare("d3", "ALLEGATO", "c.xml", firme={"formato": "NESSUNO"}),
    ))
    esito = CATALOGO_V3.valuta(dati, a, Fase.VERIFICA)
    if effetto == "IGNORA":
        assert esito.rilievi == []
    else:
        assert [r.documento for r in esito.rilievi] == ["d1"]
        assert esito.rilievi[0].effetto.value == effetto
        assert esito.blocca is (effetto == "BLOCCA")
    # la fase VERIFICA non entra nel versamento (gateway): solo la validazione
    assert not CATALOGO_V3.valuta(dati, a, Fase.FILE).rilievi or effetto


def test_catalogo_formati_dati_e_pdfa() -> None:
    v = {c: REGISTRO_V3[c].predefinito for c in REGISTRO_V3.codici() if c.startswith("FORMATO.")}
    assert v[codice_esito("PDF")] == "AMMESSO" and v[codice_esito("NON_IDENTIFICATO")] == \
        "RIFIUTATO" and v[codice_esito("OFFICE_BINARIO")] == "DA_CONVERTIRE"
    # gli esiti del registro di R03 restano ammessi (nessun cambio per chi c'è già)
    for mime in ("application/pdf", "application/xml", "application/pkcs7-mime",
                 "image/tiff", "image/jpeg", "image/png"):
        assert esito_formato(puid=None, mime=mime, pdfa_conforme=None,
                             valore=v.__getitem__).esito == "AMMESSO", mime
    restr = {**v, codice_esito("PDF"): "RIFIUTATO"}
    # senza PUID (gateway) un PDF vale la famiglia meno restrittiva (PDF/A)
    assert esito_formato(puid=None, mime="application/pdf", pdfa_conforme=None,
                         valore=restr.__getitem__).esito == "AMMESSO"
    # con il PUID decide la famiglia: PDF 1.7 rifiutato, PDF/A-2b ammesso
    assert esito_formato(puid="fmt/276", mime="application/pdf", pdfa_conforme=None,
                         valore=restr.__getitem__).esito == "RIFIUTATO"
    # revisione X11: PDF/A dal PUID ma non validato (veraPDF assente): AVVISO, mai AMMESSO
    assert esito_formato(puid="fmt/477", mime="application/pdf", pdfa_conforme=None,
                         valore=restr.__getitem__).esito == "AVVISO"
    assert esito_formato(puid="fmt/477", mime="application/pdf", pdfa_conforme=True,
                         valore=restr.__getitem__).esito == "AMMESSO"
    # PDF/A NON conforme per veraPDF: vale come PDF
    e = esito_formato(puid="fmt/477", mime="application/pdf", pdfa_conforme=False,
                      valore=restr.__getitem__)
    assert e.esito == "RIFIUTATO" and "non conforme" in e.motivo
    ignoto = esito_formato(puid=None, mime="application/octet-stream", pdfa_conforme=None,
                           valore=v.__getitem__)
    assert ignoto.voce.id == "NON_IDENTIFICATO" and ignoto.esito == "RIFIUTATO"
    assert len(CATALOGO_FORMATI.voci) >= 12


@pytest.mark.parametrize("effetto", ["BLOCCA", "AVVISA", "IGNORA"])
def test_rgl_107_108_tre_effetti_e_restrizione_per_classe(effetto: str) -> None:
    pdf, doc = codice_esito("PDF"), codice_esito("OFFICE_BINARIO")
    livelli = {Livello.CONSERVATORE: {"REGOLA.RGL-107.EFFETTO": effetto,
                                      "REGOLA.RGL-108.EFFETTO": effetto},
               Livello.CLASSE: {pdf: "RIFIUTATO"}}
    dati = DatiVersamento("c", (
        DocumentoDaValutare("d1", "PRINCIPALE", "a.pdf", puid="fmt/276",
                            formato_rilevato="application/pdf"),
        DocumentoDaValutare("d2", "ALLEGATO", "b.doc", puid="fmt/40",
                            formato_rilevato="application/msword"),
        DocumentoDaValutare("d3", "ALLEGATO", "c.png", formato_rilevato="image/png"),
    ))
    esito = CATALOGO_V3.valuta_regole(["RGL-107", "RGL-108"], dati, _amb(livelli))
    if effetto == "IGNORA":
        assert esito.rilievi == []
        return
    per_regola = {r.regola: (r.documento, r.effetto.value) for r in esito.rilievi}
    assert per_regola == {"RGL-107": ("d1", effetto), "RGL-108": ("d2", effetto)}
    # l'ente non può riammettere per la classe ciò che il conservatore rifiuta
    piano = piano_valori(registro=REGISTRO_V3, attore=ENTE,
                         ambito=Ambito(Livello.CLASSE, ente="t1", classe="c1"),
                         modifiche=[ModificaValore(doc, "AMMESSO")],
                         livelli={Livello.CONSERVATORE: {doc: "RIFIUTATO"}}, contesto=CTX)
    assert piano.bloccati


def test_validatore_preventivo_mostra_i_file_rifiutati() -> None:
    indice = {"classe_documentale": "c", "documenti": [
        {"id": "d1", "tipo": "PRINCIPALE", "nome_file": "a.pdf", "formato": "application/pdf",
         "hash": {"algoritmo": "SHA-256", "impronta": "0" * 64}},
        {"id": "d2", "tipo": "ALLEGATO", "nome_file": "b.tif", "formato": "image/tiff",
         "hash": {"algoritmo": "SHA-256", "impronta": "1" * 64}}]}
    file_info = {"a.pdf": {"impronta": "0" * 64, "formato": "application/pdf"},
                 "b.tif": {"impronta": "1" * 64, "formato": "image/tiff"}}
    a = _amb({Livello.CLASSE: {codice_esito("TIFF"): "RIFIUTATO", LIV: "CRITTOGRAFICA"},
              Livello.CONSERVATORE: {"REGOLA.RGL-101.EFFETTO": "IGNORA"}})
    esito = valida_versamento(indice, a, file_info=file_info).come_dict()
    assert esito["file_rifiutati"] == ["b.tif"]
    righe = {f["nome_file"]: f for f in esito["file"]}
    assert righe["b.tif"]["catalogo"]["esito"] == "RIFIUTATO"
    assert righe["a.pdf"]["rifiutato"] is False
    assert esito["firme"]["livello"] == "CRITTOGRAFICA"
    foto = esito["regole_applicate"]
    assert foto["catalogo_regole"] == "4" and foto["catalogo_formati"]["versione"] == 1
    assert foto["impostazioni"][LIV]["valore"] == "CRITTOGRAFICA"
    assert codice_esito("PDFA") in foto["impostazioni"]
