# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 Q1 — motore delle regole completo: livelli ACCORDO e PDV, profili di tipologia
dell'Allegato 5 come dati versionati, catalogo completo delle impostazioni,
piano di configurazione con permessi e impronta, confronto sui PdV, campione
in blocco e validatore preventivo (anche senza rete).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest

from libs.verumdoc_rules import CATALOGO, CATALOGO_V1, CATALOGO_V2, REGISTRO, REGISTRO_V1
from libs.verumdoc_rules.accordi import (
    AccordiAmbigui,
    AccordoNonValido,
    AccordoVersamento,
    in_vigore,
    seleziona_accordo,
)
from libs.verumdoc_rules.campione import (
    SQL_CAMPIONE_CLASSE,
    SQL_CAMPIONE_ENTE,
    SQL_CAMPIONE_ENTE_CLASSE,
    SQL_CAMPIONE_TUTTI,
    Variante,
    carica_campione,
)
from libs.verumdoc_rules.catalogo import catalogo_impostazioni
from libs.verumdoc_rules.configurazione import (
    Ambito,
    Attore,
    ModificaValore,
    Risorsa,
    motivo_blocco_ambito,
    motivo_blocco_voce,
    normalizza_modifiche,
    piano_profilo,
    piano_valori,
)
from libs.verumdoc_rules.confronta import confronta
from libs.verumdoc_rules.impostazioni import (
    ASSENTE,
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
)
from libs.verumdoc_rules.lettura import prepara_ambiente
from libs.verumdoc_rules.normative import REGISTRO_NORMATIVO
from libs.verumdoc_rules.override import OverrideNonAmmesso, valida_override
from libs.verumdoc_rules.profili import (
    CampoProfilo,
    ProfiloMetadati,
    ProfiloNonValido,
    TipoCampo,
    componi_profili,
)
from libs.verumdoc_rules.regole import AmbienteRegole, Fase
from libs.verumdoc_rules.tipologie import (
    codici_tipologie,
    profili_predefiniti,
    profilo_tipologia,
    versioni_tipologia,
)
from libs.verumdoc_rules.validatore import (
    CAMPI_METADATI_INDICE,
    EsportazioneNonValida,
    ambiente_da_esportazione,
    controlla_forma,
    dati_da_indice,
    esporta_profilo,
    valida_versamento,
)

ADESSO = datetime(2026, 10, 23, 10, tzinfo=UTC)
OGGI = date(2026, 10, 23)


def _indice(**parametri: Any) -> dict[str, Any]:
    base = {"identificativo_documento": "X", "modalita_formazione": "a",
            "tipologia_documentale": "fattura", "data_documento": "2026-10-01",
            "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme",
            "oggetto": "Oggetto di prova"}
    return {"classe_documentale": "fattura", "documenti": [{
        "id": "d1", "tipo": "PRINCIPALE", "nome_file": "a.pdf",
        "hash": {"impronta": "ab" * 32, "algoritmo": "SHA-256"},
        "parametri": {**base, **parametri}}]}


def _ambiente(livelli: Mapping[Livello, Mapping[str, Any]] | None = None, **kw: Any
              ) -> AmbienteRegole:
    return prepara_ambiente(registro=REGISTRO, livelli=livelli or {}, adesso=ADESSO,
                            tenant_id="t", classe_id="c", **kw)


# ── Impostazioni: livelli e ambiti ─────────────────────────────────

def test_livello_pdv_e_alias_storico() -> None:
    assert Livello.OVERRIDE_PDV is Livello.PDV and Livello.PDV.value == "PDV"


def test_catena_mostra_ogni_livello_e_ignora_il_cliente_per_le_voci_locked() -> None:
    fornitore = FornitoreDaDizionari({Livello.ENTE: {"CLASSE_DIMENSIONE_MAX_MB": 50},
                                      Livello.CLASSE: {"METADATI_MINIMI_PRINCIPALE": ["x"]}})
    ris = Risolutore(REGISTRO, fornitore)
    ctx = ContestoRisoluzione(adesso=ADESSO)
    catena = dict(ris.catena(ctx, "CLASSE_DIMENSIONE_MAX_MB"))
    assert catena[Livello.ENTE] == 50 and catena[Livello.CLASSE] is ASSENTE
    assert catena[Livello.PREDEFINITO] == 100
    minimi = dict(ris.catena(ctx, "METADATI_MINIMI_PRINCIPALE"))
    assert minimi[Livello.CLASSE] is ASSENTE  # locked: il livello del cliente non conta


def test_catalogo_completo_senza_doppioni_e_normative_in_sola_lettura() -> None:
    registro = catalogo_impostazioni()
    assert set(REGISTRO.codici()) <= set(registro.codici())
    for voce in REGISTRO_NORMATIVO:
        assert voce.applicata_da is None and voce.livelli_ammessi() == frozenset()
    quota = registro["QUOTA_PDV_GIORNO"]
    assert quota.riservata_conservatore and quota.livelli_ammessi() == {Livello.ENTE}
    assert REGISTRO["REGOLA.RGL-103.EFFETTO"].livelli_ammessi() >= {Livello.PDV}
    assert Livello.PDV not in REGISTRO["REGOLA.RGL-102.EFFETTO"].livelli_ammessi()
    with pytest.raises(ValueError, match="già registrata"):
        catalogo_impostazioni(list(REGISTRO_NORMATIVO)[:1])


def test_catalogo_2_stesse_regole_e_fotografia_con_accordo_e_override() -> None:
    # R08: il catalogo in vigore è il 3 (R11: il 4); il 2 resta per le fotografie già scritte
    assert [r.codice for r in CATALOGO_V2.regole()] == [r.codice for r in CATALOGO_V1.regole()]
    assert {r.effetto_predefinito for r in CATALOGO_V2.regole()} == \
        {r.effetto_predefinito for r in CATALOGO_V1.regole()}
    assert CATALOGO.versione == "4"
    foto = CATALOGO_V2.fotografia(_ambiente())
    assert foto["catalogo_regole"] == "2" and foto["accordo"] is None and foto["override"] is None
    v1 = CATALOGO_V1.fotografia(AmbienteRegole(Risolutore(REGISTRO_V1, FornitoreDaDizionari()),
                                               ContestoRisoluzione(adesso=ADESSO)))
    assert "accordo" not in v1  # le fotografie del catalogo 1 restano come erano


# ── Accordi ─────────────────────────────────────────────────────────

def _accordo(codice: str, versione: int, dal: date, classe: str | None = None,
             **kw: Any) -> AccordoVersamento:
    return AccordoVersamento("t", codice, versione, dal, classe_id=classe, **kw)


def test_versione_in_vigore_futura_e_cessata() -> None:
    v1 = _accordo("A", 1, date(2026, 1, 1))
    v2 = _accordo("A", 2, date(2027, 1, 1))
    assert in_vigore([v1, v2], OGGI) is v1
    assert in_vigore([v1, v2], date(2027, 1, 1)) is v2
    chiuso = _accordo("A", 3, date(2026, 6, 1), cessato=True)
    assert in_vigore([v1, chiuso], OGGI) is None
    assert in_vigore([v2], OGGI) is None


def test_scelta_dell_accordo_classe_poi_ente_poi_indice() -> None:
    generico = _accordo("GEN", 1, date(2026, 1, 1))
    di_classe = _accordo("CLS", 1, date(2026, 1, 1), classe="c")
    altro = _accordo("ALT", 1, date(2026, 1, 1), classe="x")
    tutti = [generico, di_classe, altro]
    assert seleziona_accordo(tutti, giorno=OGGI, classe_id="c") is di_classe
    assert seleziona_accordo(tutti, giorno=OGGI, classe_id="z") is generico
    assert seleziona_accordo(tutti, giorno=OGGI, classe_id="c", richiesto="GEN") is generico
    assert seleziona_accordo([], giorno=OGGI, classe_id="c") is None
    with pytest.raises(AccordoNonValido, match="non vale per la classe"):
        seleziona_accordo(tutti, giorno=OGGI, classe_id="c", richiesto="ALT")
    with pytest.raises(AccordoNonValido, match="non esiste"):
        seleziona_accordo(tutti, giorno=OGGI, classe_id="c", richiesto="NO")
    with pytest.raises(AccordiAmbigui):
        seleziona_accordo([di_classe, _accordo("CLS2", 1, date(2026, 2, 1), classe="c")],
                          giorno=OGGI, classe_id="c")


def test_valori_dell_accordo_al_livello_accordo() -> None:
    accordo = _accordo("A", 1, date(2026, 1, 1), valori={"CLASSE_DIMENSIONE_MAX_MB": 3})
    ambiente = _ambiente({Livello.CLASSE: {"CLASSE_DIMENSIONE_MAX_MB": 50}}, accordi=[accordo])
    effettivo = ambiente.effettiva("CLASSE_DIMENSIONE_MAX_MB")
    assert (effettivo.valore, effettivo.origine) == (3, Livello.ACCORDO)
    assert CATALOGO.fotografia(ambiente)["accordo"]["codice"] == "A"


# ── Override del PdV ────────────────────────────────────────────────

def test_override_ammesso_prevale_e_finisce_nella_fotografia() -> None:
    pdv = valida_override({"motivo": "Concordato col responsabile",
                           "valori": {"REGOLA.RGL-103.EFFETTO": "IGNORA"}}, REGISTRO)
    ambiente = _ambiente({Livello.ENTE: {"REGOLA.RGL-103.EFFETTO": "BLOCCA"}}, override=pdv)
    assert ambiente.effettiva("REGOLA.RGL-103.EFFETTO").origine is Livello.PDV
    assert CATALOGO.fotografia(ambiente)["override"]["valori"] == {
        "REGOLA.RGL-103.EFFETTO": "IGNORA"}
    assert valida_override(None, REGISTRO) is None


def test_override_non_ammesso_elenca_tutti_i_problemi() -> None:
    with pytest.raises(OverrideNonAmmesso) as exc:
        valida_override({"motivo": "breve", "valori": {
            "REGOLA.RGL-101.EFFETTO": "IGNORA", "SCONOSCIUTA": 1, "FORMATI_AMMESSI": [],
            "REGOLA.RGL-103.EFFETTO": "FORSE"}}, REGISTRO)
    codici = {p["codice"] for p in exc.value.problemi}
    assert codici == {"motivo", "REGOLA.RGL-101.EFFETTO", "SCONOSCIUTA", "FORMATI_AMMESSI",
                      "REGOLA.RGL-103.EFFETTO"}


# ── Profili di tipologia (Allegato 5) ───────────────────────────────

def test_sei_tipologie_come_dati_con_ereditarieta() -> None:
    assert set(codici_tipologie()) == {
        "documento_informatico", "documento_amministrativo_informatico",
        "aggregazione_documentale", "fattura", "registro_protocollo", "contratto"}
    fattura = profili_predefiniti("fattura")[0]
    nomi = fattura.nomi()
    assert {"identificativo_documento", "riservato", "numero_fattura"} <= nomi  # base + propri
    dai = profili_predefiniti("documento_amministrativo_informatico")[0]
    assert dai.campo("classificazione").obbligatorio  # sostituisce quello del DI
    registro = profili_predefiniti("registro_protocollo")[0]
    assert {"codice_ipa", "segnatura", "numero_iniziale"} <= registro.nomi()  # DI → DAI → reg.
    for codice in codici_tipologie():
        profilo = profili_predefiniti(codice)[0]
        assert profilo.valido_dal == date(2022, 1, 1) and profilo.norm_ref
        assert all(c.allegato5 or c.descrizione for c in profilo.campi)
    assert any(c.da_riscontrare for c in fattura.campi)


def test_versioni_di_tipologia_dal_database_e_selezione() -> None:
    v1 = profili_predefiniti("contratto")[0]
    # R11: la v2 predefinita esiste (adozione esplicita); il conservatore crea la v3
    v3 = ProfiloMetadati("contratto", 3, date(2027, 1, 1), v1.campi, tipologia="contratto")
    assert [p.versione for p in versioni_tipologia("contratto", [v3])] == [1, 2, 3]
    assert profilo_tipologia("contratto", OGGI, [v3]).versione == 1
    assert profilo_tipologia("contratto", date(2027, 1, 2), [v3]).versione == 3
    assert profilo_tipologia("", OGGI) is None
    with pytest.raises(ProfiloNonValido):
        profilo_tipologia("inesistente", OGGI)
    with pytest.raises(ProfiloNonValido, match="esiste già"):
        versioni_tipologia("contratto", [ProfiloMetadati("contratto", 1, OGGI, v1.campi)])


def test_composizione_tipologia_e_classe_e_campi_calcolati() -> None:
    classe = ProfiloMetadati("fattura", 3, date(2026, 1, 1), (
        CampoProfilo("numero_fattura", TipoCampo.STRINGA, obbligatorio=False),
        CampoProfilo("commessa", TipoCampo.STRINGA, obbligatorio=True)), id="p3")
    tipologia = profilo_tipologia("fattura", OGGI)
    composto = componi_profili(tipologia, classe)
    # R11 (D07): la classe restringe soltanto, non rende facoltativo un campo obbligatorio
    assert composto.campo("numero_fattura").obbligatorio is True
    assert composto.campo("commessa") and composto.campo("riservato")
    assert [r["versione"] for r in composto.riferimento()["composto_da"]] == [1, 3]
    assert componi_profili(None, classe) is classe and componi_profili(None, None) is None
    # RGL-102 non chiede i campi calcolati dal servizio (impronta, formato, n. allegati)
    ambiente = _ambiente({Livello.CLASSE: {"PROFILO_TIPOLOGIA": "fattura"}})
    from libs.verumdoc_rules.validatore import dati_da_indice as dati
    mancanti = {r.campo for r in CATALOGO.valuta(dati(_indice()), ambiente, Fase.INDICE).rilievi
                if r.regola == "RGL-102"}
    assert "numero_fattura" in mancanti and "impronta_documento" not in mancanti
    assert "formato" not in mancanti and "numero_allegati" not in mancanti
    assert CATALOGO.fotografia(ambiente)["profilo_metadati"]["tipologia"] == "fattura"


# ── Piano di configurazione e permessi ─────────────────────────────

SUPER = Attore("SUPER_ADMIN", "x")
RSC = Attore("RSC_CONSERVATORE", "x")
ADMIN = Attore("ADMIN_TENANT", "t")


@pytest.mark.parametrize("attore,risorsa,ambito,bloccato", [
    (SUPER, Risorsa.IMPOSTAZIONI, Ambito(Livello.CONSERVATORE), False),
    (RSC, Risorsa.IMPOSTAZIONI, Ambito(Livello.ENTE, ente="altro"), False),
    (RSC, Risorsa.CLASSE, Ambito(Livello.CLASSE, classe="c", classe_di_base=True), True),
    (SUPER, Risorsa.CLASSE, Ambito(Livello.CLASSE, classe="c", classe_di_base=True), False),
    (SUPER, Risorsa.ACCORDO, Ambito(Livello.ACCORDO, ente="t", accordo="A"), True),
    (ADMIN, Risorsa.IMPOSTAZIONI, Ambito(Livello.CONSERVATORE), True),
    (ADMIN, Risorsa.IMPOSTAZIONI, Ambito(Livello.ENTE, ente="t"), False),
    (ADMIN, Risorsa.IMPOSTAZIONI, Ambito(Livello.ENTE, ente="u"), True),
    (ADMIN, Risorsa.CLASSE, Ambito(Livello.CLASSE, ente="t", classe="c"), False),
    (ADMIN, Risorsa.CLASSE, Ambito(Livello.CLASSE, classe="c", classe_di_base=True), True),
    (ADMIN, Risorsa.ACCORDO, Ambito(Livello.ACCORDO, ente="t", accordo="A"), False),
    (ADMIN, Risorsa.QUOTE, Ambito(Livello.ENTE, ente="t"), True),
    (ADMIN, Risorsa.IMPOSTAZIONI, Ambito(Livello.PDV, ente="t"), True),
    (Attore("OPERATORE", "t"), Risorsa.IMPOSTAZIONI, Ambito(Livello.ENTE, ente="t"), True),
    (ADMIN, Risorsa.PROFILO, Ambito(Livello.ENTE, ente="t"), True),
])
def test_permessi_del_contratto_q1(attore, risorsa, ambito, bloccato) -> None:
    assert (motivo_blocco_ambito(attore, risorsa, ambito) is not None) is bloccato


def test_voce_locked_riservata_o_non_applicata() -> None:
    registro = catalogo_impostazioni()
    ente = Ambito(Livello.ENTE, ente="t")
    assert motivo_blocco_voce(ADMIN, ente, registro["METADATI_MINIMI_PRINCIPALE"])
    assert motivo_blocco_voce(SUPER, Ambito(Livello.CONSERVATORE),
                              registro["METADATI_MINIMI_PRINCIPALE"]) is None
    assert motivo_blocco_voce(ADMIN, ente, registro["QUOTA_PDV_GIORNO"])
    assert motivo_blocco_voce(SUPER, ente, registro["QUOTA_PDV_GIORNO"]) is None
    assert "non ancora applicato" in motivo_blocco_voce(SUPER, Ambito(Livello.CONSERVATORE),
                                                        registro["OGGETTO_LUNGHEZZA_MINIMA"])
    # R07 (D17): la fine della transizione per ente la proroga solo il conservatore
    assert motivo_blocco_voce(ADMIN, ente, registro["IDEMPOTENZA_TRANSIZIONE_FINO_AL"]) == \
        "valore stabilito dal conservatore"
    assert motivo_blocco_voce(SUPER, ente, registro["IDEMPOTENZA_TRANSIZIONE_FINO_AL"]) is None
    assert motivo_blocco_voce(ADMIN, Ambito(Livello.CLASSE, ente="t", classe="c"),
                              registro["IDEMPOTENZA_TRANSIZIONE_FINO_AL"])


def test_normalizza_modifiche_regole_campi_accordo_e_scarti() -> None:
    valori, campi, bloccati = normalizza_modifiche(Risorsa.ACCORDO, [
        {"codice": "FORMATI_AMMESSI", "valore": ["application/pdf"]},
        {"regola": "RGL-103", "effetto": "IGNORA"},
        {"codice": "CLASSE_DIMENSIONE_MAX_MB", "eredita": True},
        {"campo": "descrizione", "valore": "x"}, {"campo": "colore"}, {"valore": 1}])
    assert [v.codice for v in valori] == ["FORMATI_AMMESSI", "REGOLA.RGL-103.EFFETTO",
                                          "CLASSE_DIMENSIONE_MAX_MB"]
    assert valori[2].eredita and campi == {"descrizione": "x"} and len(bloccati) == 2


def test_piano_valori_prima_dopo_origine_e_impronta() -> None:
    registro = catalogo_impostazioni()
    livelli = {Livello.CONSERVATORE: {}, Livello.ENTE: {"CLASSE_DIMENSIONE_MAX_MB": 50}}
    ctx = ContestoRisoluzione(adesso=ADESSO, tenant_id="t")
    ambito = Ambito(Livello.ENTE, ente="t")
    piano = piano_valori(registro=registro, attore=ADMIN, ambito=ambito, modifiche=[
        ModificaValore("CLASSE_DIMENSIONE_MAX_MB", eredita=True),
        ModificaValore("FORMATI_AMMESSI", ["application/pdf"]),
        ModificaValore("FORMATI_AMMESSI", ["image/png"]),
        ModificaValore("METADATI_MINIMI_PRINCIPALE", ["x"])], livelli=livelli, contesto=ctx)
    righe = {r.codice: r for r in piano.modifiche}
    assert (righe["CLASSE_DIMENSIONE_MAX_MB"].prima, righe["CLASSE_DIMENSIONE_MAX_MB"].dopo) == \
        (50, 100)
    assert righe["CLASSE_DIMENSIONE_MAX_MB"].origine_dopo == "PREDEFINITO"
    assert righe["FORMATI_AMMESSI"].dopo == ["application/pdf"]
    assert {b["codice"] for b in piano.bloccati} == {"FORMATI_AMMESSI",
                                                     "METADATI_MINIMI_PRINCIPALE"}
    assert piano.scritture == {"CLASSE_DIMENSIONE_MAX_MB": ASSENTE,
                               "FORMATI_AMMESSI": ["application/pdf"]}
    assert livelli[Livello.ENTE] == {"CLASSE_DIMENSIONE_MAX_MB": 50}  # ingresso non toccato
    base = {"bozza": "b", "versione": 1}
    stesso = piano_valori(registro=registro, attore=ADMIN, ambito=ambito, modifiche=[
        ModificaValore("CLASSE_DIMENSIONE_MAX_MB", eredita=True),
        ModificaValore("FORMATI_AMMESSI", ["application/pdf"]),
        ModificaValore("FORMATI_AMMESSI", ["image/png"]),
        ModificaValore("METADATI_MINIMI_PRINCIPALE", ["x"])], livelli=livelli, contesto=ctx)
    assert piano.impronta(base) == stesso.impronta(base)
    cambiato = piano_valori(registro=registro, attore=ADMIN, ambito=ambito, modifiche=[
        ModificaValore("CLASSE_DIMENSIONE_MAX_MB", eredita=True)],
        livelli={Livello.ENTE: {"CLASSE_DIMENSIONE_MAX_MB": 60}}, contesto=ctx)
    assert cambiato.impronta(base) != piano.impronta(base)


def test_piano_senza_eredita_per_le_colonne_della_classe() -> None:
    piano = piano_valori(registro=REGISTRO, attore=ADMIN,
                         ambito=Ambito(Livello.CLASSE, ente="t", classe="c"),
                         modifiche=[ModificaValore("CLASSE_DIMENSIONE_MAX_MB", eredita=True)],
                         livelli={Livello.CLASSE: {"CLASSE_DIMENSIONE_MAX_MB": 100}},
                         contesto=ContestoRisoluzione(adesso=ADESSO),
                         codici_senza_eredita=frozenset({"CLASSE_DIMENSIONE_MAX_MB"}))
    assert piano.bloccati and not piano.scritture


def test_piano_profilo_differenze_e_campi_non_validi() -> None:
    attuale = ProfiloMetadati("x", 1, OGGI, (CampoProfilo("a", TipoCampo.STRINGA),))
    piano = piano_profilo(attuale=attuale, codice="classe:x", campi_nuovi=[
        {"nome": "a", "tipo": "stringa"}, {"nome": "b", "tipo": "data", "obbligatorio": True}])
    assert [m.codice for m in piano.modifiche] == ["classe:x.b"]
    assert any("diventa obbligatorio" in a for a in piano.avvisi)
    errato = piano_profilo(attuale=attuale, codice="classe:x", campi_nuovi=[
        {"nome": "1x", "tipo": "stringa"}, {"nome": "e", "tipo": "enum"}])
    assert len(errato.bloccati) == 2
    assert piano_profilo(attuale=None, codice="c", campi_nuovi=[]).bloccati


# ── Confronto e campione ────────────────────────────────────────────

class _Esecutore:
    """Esecutore finto: registra le query e restituisce righe preparate."""

    def __init__(self, righe: dict[str, list[dict[str, Any]]]) -> None:
        self.righe, self.chiamate = righe, []

    async def __call__(self, sql: str, parametri: Mapping[str, Any]) -> list[dict[str, Any]]:
        self.chiamate.append((sql, dict(parametri)))
        for chiave, righe in self.righe.items():
            if chiave in sql:
                return righe
        return []


def _riga_pdv(pid: str, tenant: str, formato: str, **indice: Any) -> dict[str, Any]:
    import json
    return {"id": pid, "tenant_id": tenant, "classe_id": "c", "data_ricezione": ADESSO,
            "indice": json.dumps({**_indice(), **indice}),
            "documenti": json.dumps([{"nome_file": "a.pdf", "size_bytes": 10,
                                      "formato_rilevato": formato}])}


@pytest.mark.parametrize("ente,classe,sql", [
    (None, None, SQL_CAMPIONE_TUTTI), ("t", None, SQL_CAMPIONE_ENTE),
    (None, "c", SQL_CAMPIONE_CLASSE), ("t", "c", SQL_CAMPIONE_ENTE_CLASSE)])
async def test_campione_sceglie_la_query_indicizzata(ente, classe, sql) -> None:
    esegui = _Esecutore({})
    campione = await carica_campione(esegui, registro=REGISTRO, adesso=ADESSO, giorni=30,
                                     limite=7, ente=ente, classe=classe,
                                     campi_noti=CAMPI_METADATI_INDICE)
    assert esegui.chiamate[0][0] == sql and esegui.chiamate[0][1]["n"] == 7
    assert len(esegui.chiamate) == 6  # campione + 5 letture in blocco, mai una per PdV
    assert campione.dichiarazione()["finestra_giorni"] == 30


async def test_confronto_con_variante_solo_nel_suo_ambito_e_tempo_massimo() -> None:
    esegui = _Esecutore({"FROM public.pdv": [
        _riga_pdv("p1", "t", "image/png"), _riga_pdv("p2", "u", "image/png"),
        _riga_pdv("p3", "t", "application/pdf"),
        _riga_pdv("p4", "t", "application/pdf", accordo="NON-ESISTE")]})
    campione = await carica_campione(esegui, registro=REGISTRO, adesso=ADESSO, giorni=30,
                                     limite=100, campi_noti=CAMPI_METADATI_INDICE)
    variante = Variante(livello=Livello.ENTE, ente="t",
                        valori={"FORMATI_AMMESSI": ["application/pdf"]})
    esito = confronta(campione.pdv, catalogo=CATALOGO,
                      ambiente_prima=lambda p: campione.contesti.ambiente(p),
                      ambiente_dopo=lambda p: campione.contesti.ambiente(p, variante))
    assert (esito.analizzati, esito.rifiutati_dopo, esito.rifiutati_prima) == (4, 1, 0)
    assert esito.non_valutabili == 1  # p4: accordo non più valido, non si indovina
    assert {e["pdv_id"] for e in esito.esempi} == {"p1", "p4"}  # p2 è di un altro ente
    chiamate = iter([False, False, True])
    troncato = confronta(campione.pdv, catalogo=CATALOGO,
                         ambiente_prima=lambda p: campione.contesti.ambiente(p),
                         ambiente_dopo=lambda p: campione.contesti.ambiente(p, variante),
                         tempo_scaduto=lambda: next(chiamate))
    assert troncato.troncato_per_tempo and troncato.analizzati == 2


# ── Validatore preventivo ───────────────────────────────────────────

def test_forma_dell_indice() -> None:
    intestazione, rilievi = controlla_forma(_indice())
    assert rilievi == [] and intestazione.classe == "fattura"
    assert controlla_forma([])[0] is None
    assert controlla_forma({"classe_documentale": "x", "documenti": []})[0] is None
    indice = _indice(data_documento="2026-02-30", soggetto_destinatario="uno")
    _, rilievi = controlla_forma(indice)
    assert {r["campo"] for r in rilievi} == {"data_documento", "soggetto_destinatario"}


def test_validatore_regole_estensioni_e_file() -> None:
    ambiente = _ambiente({Livello.CLASSE: {"FORMATI_AMMESSI": ["application/pdf"],
                                           "CLASSE_DIMENSIONE_MAX_MB": 1}})
    esito = valida_versamento(_indice(non_previsto="x"), ambiente)
    assert esito.conforme and [a["codice"] for a in esito.avvisi] == ["RGL-103"]
    png = _indice()
    png["documenti"][0]["nome_file"] = "a.png"
    assert [r["codice"] for r in valida_versamento(png, ambiente).rilievi] == ["SEC-006"]
    file_info = {"a.pdf": {"impronta": "cd" * 32, "dimensione": 2 * 1024 * 1024,
                           "formato": "image/png"}, "extra.pdf": {}}
    codici = [r["codice"] for r in valida_versamento(_indice(), ambiente,
                                                     file_info=file_info).rilievi]
    assert codici == ["VRS-102", "VRS-104", "RGL-104", "RGL-105"]
    assert dati_da_indice(_indice(), file_info).documenti[0].dimensione_byte == 2 * 1024 * 1024


def test_profilo_esportato_ricostruisce_lo_stesso_ambiente() -> None:
    accordo = _accordo("A", 2, date(2026, 1, 1), valori={"METADATI_OBBLIGATORI": ["cig"]})
    ambiente = _ambiente({Livello.CLASSE: {"FORMATI_AMMESSI": ["application/pdf"]},
                          Livello.ENTE: {"REGOLA.RGL-103.EFFETTO": "BLOCCA"}}, accordi=[accordo])
    esportato = esporta_profilo(ambiente, classe="fattura", ente="t", adesso=ADESSO)
    ricostruito, avvisi = ambiente_da_esportazione(esportato, adesso=ADESSO)
    assert avvisi == []
    for indice in (_indice(), _indice(cig="1"), _indice(cig="1", extra="x")):
        assert valida_versamento(indice, ricostruito).come_dict()["rilievi"] == \
            valida_versamento(indice, ambiente).come_dict()["rilievi"]
    with pytest.raises(EsportazioneNonValida):
        ambiente_da_esportazione({**esportato, "formato": "altro"}, adesso=ADESSO)
    vecchio, avvisi = ambiente_da_esportazione({**esportato, "catalogo_regole": "1"},
                                               adesso=ADESSO)
    assert [a["codice"] for a in avvisi] == ["RGL-503"]
    del vecchio
