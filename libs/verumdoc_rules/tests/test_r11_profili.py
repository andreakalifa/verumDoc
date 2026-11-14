# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R11 (D07, VD-C-136) — profili dell'Allegato 5 riscontrati sul testo ufficiale.

Casi coperti:
  - versioni pubblicate append-only: impronte registrate, eredità a versione
    fissata (una v2 della base non cambia la v1 dei profili che la estendono);
  - transizione: una classe senza scelta resta sulla v1 (adozione automatica),
    la v2 si applica solo con ``PROFILO_TIPOLOGIA_VERSIONE`` della classe;
  - obblighi a condizione (Verifica, CodiceRegistro, Tracciature), strutture
    annidate (Soggetti con ruoli e tipo ammesso per ruolo, Assegnazione),
    intervalli, data e ora;
  - l'ente non rende facoltativo un campo obbligatorio: né con un profilo di
    classe (composizione restrittiva, piano bloccato) né spegnendo RGL-102/106;
  - validatore preventivo: l'esportazione porta il profilo strutturato e dà gli
    stessi rilievi del motore del gateway.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from libs.verumdoc_rules import CATALOGO, REGISTRO
from libs.verumdoc_rules.catalogo_v4 import PROFILO_TIPOLOGIA_VERSIONE
from libs.verumdoc_rules.configurazione import (
    MOTIVO_CAMPO_DI_TIPOLOGIA,
    MOTIVO_CAMPO_PROPRIO_FACOLTATIVO,
    esito_versione_tipologia,
    motivi_campi_classe,
)
from libs.verumdoc_rules.impostazioni import ContestoRisoluzione, Livello
from libs.verumdoc_rules.lettura import prepara_ambiente
from libs.verumdoc_rules.profili import (
    CampoProfilo,
    Condizione,
    ProfiloMetadati,
    ProfiloNonValido,
    TipoCampo,
    almeno_severo,
    componi_profili,
    componi_profili_r05,
    conflitti_composizione,
    motivo_tipo,
    restringi_campo,
)
from libs.verumdoc_rules.regole import Fase
from libs.verumdoc_rules.tipologie import (
    campi_allegato5,
    codici_tipologie,
    differenze_impronte,
    impronta_profilo,
    impronte_pubblicate,
    profili_predefiniti,
    profilo_tipologia,
    tipologie,
)
from libs.verumdoc_rules.validatore import (
    ambiente_da_esportazione,
    dati_da_indice,
    esporta_profilo,
    valida_versamento,
)

DOPO = datetime(2026, 12, 1, 10, tzinfo=UTC)
GIORNO = date(2026, 12, 1)


def _ambiente(classe: Mapping[str, Any], adesso: datetime = DOPO,
              ente: Mapping[str, Any] | None = None) -> Any:
    livelli = {Livello.CLASSE: dict(classe)}
    if ente:
        livelli[Livello.ENTE] = dict(ente)
    return prepara_ambiente(registro=REGISTRO, livelli=livelli, adesso=adesso,
                            tenant_id="t1", classe_id="c1")


def _soggetti() -> list[dict[str, Any]]:
    return [
        {"ruolo": "Soggetto Che Effettua La Registrazione", "tipo_soggetto": "PG",
         "denominazione": "Ente di prova"},
        {"ruolo": "Autore", "tipo_soggetto": "PF", "cognome": "Prova", "nome": "Autore"},
    ]


def _metadati_di(**altro: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "identificativo_documento": "DOC-1", "modalita_formazione": "c",
        "tipologia_documentale": "Relazione", "data_documento": "2026-11-20",
        "tipologia_flusso": "I", "tipo_registro": "Nessuno",
        "data_registrazione": "2026-11-20T09:30:00+01:00", "numero_registrazione": "DOC-1",
        "soggetto_produttore_id": "01234567897", "soggetto_produttore_nome": "Ente di prova",
        "soggetti": _soggetti(), "oggetto": "Relazione di prova", "riservato": False,
        "versione_documento": "1",
    }
    base.update(altro)
    return {k: v for k, v in base.items() if v is not None}


def _indice(metadati: Mapping[str, Any]) -> dict[str, Any]:
    return {"classe_documentale": "RELAZIONI", "documenti": [
        {"id": "d1", "tipo": "PRINCIPALE", "nome_file": "a.pdf", "parametri": dict(metadati)}]}


def _rilievi(metadati: Mapping[str, Any], classe: Mapping[str, Any] | None = None,
             ente: Mapping[str, Any] | None = None) -> list[tuple[str, str | None, str]]:
    ambiente = _ambiente(classe if classe is not None else {
        "PROFILO_TIPOLOGIA": "documento_informatico", PROFILO_TIPOLOGIA_VERSIONE: 2},
        ente=ente)
    esito = CATALOGO.valuta(dati_da_indice(_indice(metadati)), ambiente, Fase.INDICE)
    return [(r.regola, r.campo, r.messaggio) for r in esito.rilievi
            if r.regola in ("RGL-102", "RGL-106")]


# ── Versioni pubblicate: append-only ───────────────────────────────

def test_impronte_delle_versioni_pubblicate_invariate() -> None:
    assert differenze_impronte() == []
    registrate = impronte_pubblicate()
    for codice in codici_tipologie():
        assert {f"{codice}.v1", f"{codice}.v2"} <= set(registrate)


def test_una_versione_pubblicata_modificata_si_scopre() -> None:
    """Mutazione: un campo della v1 reso facoltativo cambia l'impronta registrata."""
    v1 = profili_predefiniti("documento_informatico")[0]
    campi = tuple(replace(c, obbligatorio=False) if c.nome == "riservato" else c
                  for c in v1.campi)
    assert impronta_profilo(replace(v1, campi=campi)) != \
        impronte_pubblicate()["documento_informatico.v1"]


def test_la_v1_ereditata_non_cambia_con_la_v2_della_base() -> None:
    dai_v1 = profili_predefiniti("documento_amministrativo_informatico")[0]
    # la v1 eredita dalla v1 del documento informatico: niente campi nuovi della v2
    assert "soggetti" not in dai_v1.nomi() and "tracciature_modifiche" not in dai_v1.nomi()
    assert dai_v1.campo("tipo_registro").valori_ammessi[0] == "Nessuno"
    dai_v2 = profili_predefiniti("documento_amministrativo_informatico")[1]
    assert "Nessuno" not in dai_v2.campo("tipo_registro").valori_ammessi
    assert dai_v2.campo("tracciature_modifiche") is not None  # ereditato dalla v2 del DI


def test_profili_propri_del_conservatore_dichiarati() -> None:
    for codice in ("fattura", "registro_protocollo", "contratto"):
        assert tipologie()[codice].origine == "conservatore"
        v2 = profili_predefiniti(codice)[1]
        propri = [c for c in v2.campi if c.origine == "conservatore"]
        assert propri and all(c.allegato5 is None for c in propri)
    for codice in ("documento_informatico", "documento_amministrativo_informatico"):
        v2 = profili_predefiniti(codice)[1]
        assert tipologie()[codice].origine == "allegato5"
        assert not any(c.da_riscontrare for c in v2.campi)
        assert all(c.rif for c in v2.campi if c.origine == "allegato5")
    # aggregazioni: restano da riscontrare SOLO i metadati oltre la parte letta del testo
    agg = profili_predefiniti("aggregazione_documentale")[1]
    assert {c.nome for c in agg.campi if c.da_riscontrare} == {
        "soggetti", "procedimento_amministrativo", "id_aggregazione_precedente",
        "indice_documenti", "tempo_conservazione", "note"}


def test_valori_riscontrati() -> None:
    agg = profili_predefiniti("aggregazione_documentale")
    assert agg[1].campo("tipo_aggregazione").valori_ammessi == (
        "Fascicolo", "Serie Documentale", "Serie Di Fascicoli")
    assert "Serie Fascicoli" in agg[0].campo("tipo_aggregazione").valori_ammessi  # v1 intatta
    di = profili_predefiniti("documento_informatico")[1]
    assert di.campo("riservato").obbligatorio
    assert not di.campo("firmato_digitalmente").obbligatorio
    assert di.campo("firmato_digitalmente").obbligatorio_se == (
        Condizione("modalita_formazione", ("a", "b")),)
    assert di.campo("data_registrazione").tipo is TipoCampo.DATA_ORA


# ── Transizione: adozione esplicita per classe ────────────────────

def test_classe_senza_scelta_resta_sulla_v1() -> None:
    assert profilo_tipologia("documento_informatico", GIORNO).versione == 1
    assert profilo_tipologia("documento_informatico", GIORNO, versione=2).versione == 2
    with pytest.raises(ProfiloNonValido, match="non esiste"):
        profilo_tipologia("documento_informatico", GIORNO, versione=7)
    with pytest.raises(ProfiloNonValido, match="non è valida"):
        profilo_tipologia("documento_informatico", date(2026, 1, 1), versione=2)
    ambiente = _ambiente({"PROFILO_TIPOLOGIA": "documento_informatico"})
    assert ambiente.profilo.riferimento()["versione"] == 1
    scelta = _ambiente({"PROFILO_TIPOLOGIA": "documento_informatico",
                        PROFILO_TIPOLOGIA_VERSIONE: 2})
    foto = CATALOGO.fotografia(scelta)
    assert foto["catalogo_regole"] == "4" and foto["profilo_metadati"]["versione"] == 2
    assert foto["impostazioni"][PROFILO_TIPOLOGIA_VERSIONE]["valore"] == 2


def test_la_versione_si_sceglie_solo_per_classe() -> None:
    voce = REGISTRO[PROFILO_TIPOLOGIA_VERSIONE]
    assert voce.livelli_ammessi() == frozenset({Livello.CONSERVATORE, Livello.CLASSE}) or \
        Livello.ENTE not in voce.livelli_ammessi()
    assert Livello.PDV not in voce.livelli_ammessi()


def test_esito_della_scelta_di_versione() -> None:
    versioni = profili_predefiniti("documento_informatico")
    assert esito_versione_tipologia("documento_informatico", 0, versioni, GIORNO) == (None, None)
    assert esito_versione_tipologia("documento_informatico", 2, versioni, GIORNO) == (None, None)
    blocco, _ = esito_versione_tipologia("documento_informatico", 5, versioni, GIORNO)
    assert blocco and "non ha la versione 5" in blocco
    blocco, _ = esito_versione_tipologia("", 2, versioni, GIORNO)
    assert blocco and "PROFILO_TIPOLOGIA" in blocco
    _, avviso = esito_versione_tipologia("documento_informatico", 2, versioni, date(2026, 10, 1))
    assert avviso and "programmare" in avviso


# ── Validazione della v2 ───────────────────────────────────────────

def test_documento_completo_senza_rilievi() -> None:
    assert _rilievi(_metadati_di()) == []


@pytest.mark.parametrize("modalita,attesi", [
    ("a", {"firmato_digitalmente", "sigillato_elettronicamente", "marcatura_temporale"}),
    ("b", {"firmato_digitalmente", "sigillato_elettronicamente", "marcatura_temporale",
           "conformita_copie_immagine"}),
    ("c", set()), ("d", set())])
def test_verifica_obbligatoria_a_condizione(modalita: str, attesi: set[str]) -> None:
    mancanti = {campo for regola, campo, _ in _rilievi(_metadati_di(modalita_formazione=modalita))
                if regola == "RGL-102"}
    assert mancanti == attesi


def test_codice_registro_per_protocollo_e_tracciature_dalla_versione_2() -> None:
    protocollo = _metadati_di(tipo_registro="Protocollo Ordinario/Protocollo Emergenza")
    assert ("RGL-102", "codice_registro") in {(r, c) for r, c, _ in _rilievi(protocollo)}
    versione2 = _metadati_di(versione_documento="2")
    assert ("RGL-102", "tracciature_modifiche") in {(r, c) for r, c, _ in _rilievi(versione2)}
    # revisione R11: anche il documento annullato (versione 1) richiede le tracciature
    annullato = _metadati_di(documento_annullato=True)
    assert ("RGL-102", "tracciature_modifiche") in {(r, c) for r, c, _ in _rilievi(annullato)}
    assert _rilievi(_metadati_di(documento_annullato=False)) == []
    completo = _metadati_di(versione_documento="2", tracciature_modifiche=[{
        "tipo_modifica": "Rettifica", "autore_modifica": "op1",
        "data_ora_modifica": "2026-11-21T10:00:00Z", "id_doc_versione_precedente": "DOC-1"}])
    assert _rilievi(completo) == []
    sbagliato = _metadati_di(versione_documento="2", tracciature_modifiche=[{
        "tipo_modifica": "Cancellazione", "autore_modifica": "op1",
        "data_ora_modifica": "21/11/2026", "id_doc_versione_precedente": "DOC-1"}])
    messaggi = " ".join(m for _, _, m in _rilievi(sbagliato))
    assert "[1].tipo_modifica: valore non fra quelli ammessi" in messaggi
    assert "[1].data_ora_modifica: attese data e ora" in messaggi
    assert "21/11/2026" not in messaggi  # mai i valori nei messaggi


def test_soggetti_strutturati_con_ruoli() -> None:
    def messaggi(soggetti: list[dict[str, Any]]) -> str:
        return " ".join(m for r, c, m in _rilievi(_metadati_di(soggetti=soggetti))
                        if c == "soggetti")

    persona_senza_cognome = [*_soggetti()[:1], {"ruolo": "Autore", "tipo_soggetto": "PF",
                                                "nome": "Autore"}]
    assert "[2].cognome: obbligatorio se tipo_soggetto è PF" in messaggi(persona_senza_cognome)
    produttore_persona = [*_soggetti(), {"ruolo": "Produttore", "tipo_soggetto": "PF",
                                         "cognome": "X", "nome": "Y"}]
    assert "[3].tipo_soggetto: valore non fra quelli ammessi (SW)" in \
        messaggi(produttore_persona)
    senza_autore = _soggetti()[:1]
    assert "almeno un elemento con ruolo è Autore o Mittente" in messaggi(senza_autore)
    assert "atteso un elenco di oggetti" in messaggi("Mario Rossi")  # type: ignore[arg-type]
    mancanti = {c for r, c, _ in _rilievi(_metadati_di(soggetti=None)) if r == "RGL-102"}
    assert "soggetti" in mancanti


def test_intervalli_e_numero_di_elementi() -> None:
    troppe = _metadati_di(parole_chiave=["a", "b", "c", "d", "e", "f"])
    assert any("al più 5 elementi" in m for _, c, m in _rilievi(troppe) if c == "parole_chiave")
    zero = _metadati_di(tempo_conservazione=0)
    assert any("non minore di 1" in m for _, c, m in _rilievi(zero))
    assert _rilievi(_metadati_di(tempo_conservazione=9999)) == []


def test_aggregazione_fascicolo_con_assegnazione() -> None:
    classe = {"PROFILO_TIPOLOGIA": "aggregazione_documentale", PROFILO_TIPOLOGIA_VERSIONE: 2}
    base = {"identificativo_aggregazione": "F-1", "tipo_aggregazione": "Fascicolo",
            "soggetti": [{"ruolo": "Amministrazione titolare", "tipo_soggetto": "PAI",
                          "codice_ipa": "ente_prova"}],
            "data_apertura": "2026-11-01", "classificazione": "1.2",
            "classificazione_descrizione": "Affari generali", "progressivo": 3,
            "oggetto": "Fascicolo di prova", "identificativo_documento": "x",
            "modalita_formazione": "c", "tipologia_documentale": "f",
            "data_documento": "2026-11-01", "soggetto_produttore_id": "01234567897",
            "soggetto_produttore_nome": "E"}
    mancanti = {c for r, c, _ in _rilievi(base, classe) if r == "RGL-102"}
    assert mancanti == {"tipologia_fascicolo", "assegnazione"}
    serie = {**base, "tipo_aggregazione": "Serie Documentale"}
    assert _rilievi(serie, classe) == []
    completo = {**base, "tipologia_fascicolo": "affare", "assegnazione": [
        {"tipo_assegnazione": "Per competenza", "soggetto_assegnatario": "UOR-1",
         "data_inizio": "2026-11-01T08:00:00Z"}]}
    assert _rilievi(completo, classe) == []


# ── L'ente non rende facoltativo un campo obbligatorio ─────────────

def test_profilo_di_classe_restringe_soltanto() -> None:
    """Mutazione: se la composizione lasciasse prevalere la classe, ``riservato``
    diventerebbe facoltativo e il versamento senza ``riservato`` passerebbe."""
    tipologia = profilo_tipologia("documento_informatico", GIORNO, versione=2)
    classe = ProfiloMetadati("RELAZIONI", 1, date(2026, 1, 1), (
        CampoProfilo("riservato", TipoCampo.STRINGA, obbligatorio=False),
        CampoProfilo("tipo_registro", TipoCampo.ENUM, valori_ammessi=("Nessuno", "Altro")),
        CampoProfilo("commessa", TipoCampo.STRINGA)))
    composto = componi_profili(tipologia, classe)
    assert composto.campo("riservato").obbligatorio is True
    assert composto.campo("riservato").tipo is TipoCampo.BOOLEANO
    assert composto.campo("tipo_registro").valori_ammessi == ("Nessuno",)
    assert composto.campo("commessa") is not None
    # la classe può rendere obbligatorio un campo facoltativo (più severo)
    note = CampoProfilo("note", TipoCampo.STRINGA, obbligatorio=True)
    assert restringi_campo(tipologia.campo("note"), note).obbligatorio is True


def test_ente_non_spegne_le_regole_dei_metadati() -> None:
    """RGL-102/106 «solo più severe»: IGNORA dell'ente non toglie l'obbligo."""
    ente = {"REGOLA.RGL-102.EFFETTO": "IGNORA", "REGOLA.RGL-106.EFFETTO": "IGNORA"}
    rilievi = _rilievi(_metadati_di(riservato=None, tempo_conservazione=0), ente=ente)
    assert ("RGL-102", "riservato") in {(r, c) for r, c, _ in rilievi}
    assert any(r == "RGL-106" for r, _, _ in rilievi)
    assert all(REGISTRO[f"REGOLA.{c}.EFFETTO"].solo_riduzione for c in ("RGL-102", "RGL-106"))


def test_piano_della_classe_solo_campi_facoltativi_propri() -> None:
    riservati = profilo_tipologia("documento_informatico", GIORNO, versione=2).nomi()
    campi = [CampoProfilo("riservato", TipoCampo.BOOLEANO),
             CampoProfilo("commessa", TipoCampo.STRINGA, obbligatorio=True),
             CampoProfilo("centro_costo", TipoCampo.STRINGA,
                          obbligatorio_se=(Condizione("commessa", ("X",)),)),
             CampoProfilo("ufficio", TipoCampo.STRINGA)]
    bloccati = {b["codice"]: b["motivo"] for b in motivi_campi_classe(campi, riservati, "classe:R")}
    assert bloccati == {"classe:R.riservato": MOTIVO_CAMPO_DI_TIPOLOGIA,
                        "classe:R.commessa": MOTIVO_CAMPO_PROPRIO_FACOLTATIVO,
                        "classe:R.centro_costo": MOTIVO_CAMPO_PROPRIO_FACOLTATIVO}


# ── Forma dei dati e validatore preventivo ─────────────────────────

def test_forma_dei_campi_controllata() -> None:
    with pytest.raises(ProfiloNonValido, match="sottocampi"):
        CampoProfilo.da_dict({"nome": "s", "tipo": "struttura"})
    with pytest.raises(ProfiloNonValido, match="inesistenti"):
        CampoProfilo.da_dict({"nome": "s", "tipo": "struttura", "sottocampi": [
            {"nome": "a", "tipo": "stringa", "obbligatorio_se": [{"campo": "b", "in": ["x"]}]}]})
    with pytest.raises(ProfiloNonValido, match="in.*non_in"):
        Condizione.da_dict({"campo": "a", "in": ["x"], "non_in": ["y"]})
    with pytest.raises(ProfiloNonValido, match="fuori dai valori"):
        CampoProfilo.da_dict({"nome": "e", "tipo": "enum", "valori_ammessi": ["a"],
                              "valori_ammessi_se": [{"campo": "x", "in": ["1"], "valori": ["b"]}]})
    with pytest.raises(ProfiloNonValido, match="condizioni su campi inesistenti"):
        ProfiloMetadati("p", 1, GIORNO, (CampoProfilo(
            "a", TipoCampo.STRINGA, obbligatorio_se=(Condizione("zz", ("1",)),)),))


def test_forma_r05_invariata_e_andata_e_ritorno() -> None:
    v1 = profili_predefiniti("documento_informatico")[0]
    assert set(v1.campi[0].come_dict()) == {
        "nome", "tipo", "obbligatorio", "sensibile", "descrizione", "valori_ammessi",
        "allegato5", "calcolato", "da_riscontrare"}
    v2 = profilo_tipologia("documento_informatico", GIORNO, versione=2)
    copia = ProfiloMetadati.da_dict(json.loads(json.dumps(v2.come_dict())))
    assert copia == replace(v2, composto_da=())


def test_validatore_preventivo_coerente_con_il_motore() -> None:
    ambiente = _ambiente({"PROFILO_TIPOLOGIA": "documento_informatico",
                          PROFILO_TIPOLOGIA_VERSIONE: 2})
    esportato = json.loads(json.dumps(esporta_profilo(
        ambiente, classe="RELAZIONI", ente="t1", adesso=DOPO), default=str))
    offline, avvisi = ambiente_da_esportazione(esportato, adesso=DOPO)
    assert avvisi == []
    metadati = _metadati_di(modalita_formazione="a", soggetti=_soggetti()[:1])
    indice = _indice(metadati)
    attesi = {(r["codice"], r["campo"]) for r in valida_versamento(indice, ambiente)
              .come_dict()["rilievi"]}
    trovati = {(r["codice"], r["campo"]) for r in valida_versamento(indice, offline)
               .come_dict()["rilievi"]}
    assert trovati == attesi and ("RGL-102", "firmato_digitalmente") in trovati
    assert ("RGL-106", "soggetti") in trovati


def test_motivo_tipo_senza_lettore_resta_quello_di_r05() -> None:
    campo = CampoProfilo("e", TipoCampo.ENUM, valori_ammessi=("a", "b"))
    assert motivo_tipo(campo, "a") is None and motivo_tipo(campo, "c")


def test_mappa_allegato5_per_l_ipda() -> None:
    mappa = campi_allegato5()
    assert mappa["riservato"].allegato5 == "Riservato"
    assert mappa["soggetti"].sottocampi and mappa["tracciature_modifiche"].sottocampi
    assert "data_documento" not in mappa and "lingua" not in mappa  # metadati del servizio
    assert ContestoRisoluzione  # import usato dai test di forma


# ── Revisione T2: la composizione non allenta le restrizioni della classe ────

def test_elenco_chiuso_della_classe_su_campo_libero_mantenuto() -> None:
    """Prova della revisione: ``tipologia_documentale`` ENUM della classe sopra la
    STRINGA della tipologia: «Qualsiasi» resta rifiutato (come in R05)."""
    tipologia = profilo_tipologia("documento_informatico", GIORNO)
    classe = ProfiloMetadati("DELIBERE", 1, date(2026, 1, 1), (CampoProfilo(
        "tipologia_documentale", TipoCampo.ENUM, obbligatorio=True,
        valori_ammessi=("Delibera", "Determina")),))
    composto = componi_profili(tipologia, classe)
    campo = composto.campo("tipologia_documentale")
    assert campo.tipo is TipoCampo.ENUM and campo.obbligatorio
    assert motivo_tipo(campo, "Qualsiasi") and motivo_tipo(campo, "Delibera") is None
    assert conflitti_composizione(tipologia, classe) == [{
        "campo": "tipologia_documentale", "tipo_tipologia": "stringa", "tipo_classe": "enum",
        "motivi": [], "cambia_esito": False}]


def test_tipo_piu_largo_della_classe_cede_alla_tipologia_e_si_segnala() -> None:
    tipologia = profilo_tipologia("documento_informatico", GIORNO)
    classe = ProfiloMetadati("X", 1, date(2026, 1, 1), (
        CampoProfilo("data_documento", TipoCampo.STRINGA, obbligatorio=True),
        CampoProfilo("riservato", TipoCampo.BOOLEANO),
        CampoProfilo("modalita_formazione", TipoCampo.ENUM, valori_ammessi=("z",))))
    composto = componi_profili(tipologia, classe)
    assert composto.campo("data_documento").tipo is TipoCampo.DATA
    assert composto.campo("modalita_formazione").valori_ammessi == ("a", "b", "c", "d")
    conflitti = {c["campo"]: c for c in conflitti_composizione(tipologia, classe)}
    assert "incompatibile" in conflitti["data_documento"]["motivi"][0]
    assert conflitti["riservato"]["cambia_esito"] and conflitti["modalita_formazione"][
        "cambia_esito"]
    # per il confronto del rapporto: la composizione di R05 lascia vincere la classe
    r05 = componi_profili_r05(tipologia, classe)
    assert r05.campo("data_documento").tipo is TipoCampo.STRINGA
    assert r05.campo("riservato").obbligatorio is False


# ── Revisione T1: l'ente non toglie né abbassa la tipologia ────────────────

def _con_accordo(classe: Mapping[str, Any], accordo: Mapping[str, Any]) -> Any:
    from libs.verumdoc_rules.accordi import AccordoVersamento

    acc = AccordoVersamento(id="a1", codice="ACC", tenant_id="t1", classe_id="c1", versione=1,
                            valido_dal=date(2026, 1, 1), valori=dict(accordo))
    return prepara_ambiente(registro=REGISTRO, livelli={Livello.CLASSE: dict(classe)},
                            adesso=DOPO, tenant_id="t1", classe_id="c1", accordi=[acc],
                            accordo_richiesto="ACC").profilo


DAI_V2 = {"PROFILO_TIPOLOGIA": "documento_amministrativo_informatico",
          PROFILO_TIPOLOGIA_VERSIONE: 2}


@pytest.mark.parametrize("accordo", [{"PROFILO_TIPOLOGIA": ""},
                                     {"PROFILO_TIPOLOGIA": "documento_informatico"}])
def test_accordo_non_toglie_ne_abbassa_la_tipologia(accordo: dict[str, Any]) -> None:
    """Prova della revisione (t_accordo.py): l'accordo non sceglie la tipologia."""
    profilo = _con_accordo(DAI_V2, accordo)
    assert (profilo.codice, profilo.versione) == ("documento_amministrativo_informatico", 2)
    assert Livello.ACCORDO not in REGISTRO["PROFILO_TIPOLOGIA"].livelli_ammessi()
    assert Livello.PDV not in REGISTRO["PROFILO_TIPOLOGIA"].livelli_ammessi()


@pytest.mark.parametrize("classe,attesa", [
    ("", "documento_amministrativo_informatico"),                    # togliere: no
    ("documento_informatico", "documento_amministrativo_informatico"),  # abbassare: no
    ("registro_protocollo", "registro_protocollo"),                  # rafforzare: sì
])
def test_la_classe_non_scende_sotto_la_tipologia_dell_ente(classe: str, attesa: str) -> None:
    ambiente = _ambiente({"PROFILO_TIPOLOGIA": classe},
                         ente={"PROFILO_TIPOLOGIA": "documento_amministrativo_informatico"})
    assert ambiente.profilo.codice == attesa


def test_verso_degli_obblighi() -> None:
    v1, v2 = profili_predefiniti("documento_informatico")[:2]  # R16: c'è anche la v3
    assert almeno_severo(v2, v1) and not almeno_severo(v1, v2)
    assert almeno_severo(v1, None) and not almeno_severo(None, v1)
    dai_v2 = profili_predefiniti("documento_amministrativo_informatico")[1]
    assert almeno_severo(dai_v2, v2) and not almeno_severo(v2, dai_v2)


def test_uscita_del_conservatore_solo_per_la_tipologia() -> None:
    """Revisione T1: il conservatore riporta una classe dell'ente a un profilo con meno
    obblighi (con motivo); ogni altra voce della classe resta all'ente."""
    from libs.verumdoc_rules.configurazione import (
        MOTIVO_CONSERVATORE_CLASSI,
        Ambito,
        Attore,
        Risorsa,
        motivo_blocco_ambito,
        motivo_blocco_voce,
    )

    rsc = Attore("RSC_CONSERVATORE", "x")
    classe = Ambito(Livello.CLASSE, ente="t1", classe="c1")
    assert motivo_blocco_ambito(rsc, Risorsa.IMPOSTAZIONI, classe) is None
    assert motivo_blocco_ambito(rsc, Risorsa.CLASSE, classe) == MOTIVO_CONSERVATORE_CLASSI
    assert motivo_blocco_voce(rsc, classe, REGISTRO[PROFILO_TIPOLOGIA_VERSIONE]) is None
    assert motivo_blocco_voce(rsc, classe, REGISTRO["PROFILO_TIPOLOGIA"]) is None
    assert motivo_blocco_voce(rsc, classe, REGISTRO["CLASSE_DIMENSIONE_MAX_MB"]) == \
        MOTIVO_CONSERVATORE_CLASSI
