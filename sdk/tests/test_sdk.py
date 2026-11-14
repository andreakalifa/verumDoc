# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test dell'SDK VerumDoc 3 con un server finto (httpx.MockTransport, ``server_finto``).

Coprono: credenziali, errori tipizzati dal problem+json, ritentativi solo sui
``ritentabile`` e sulle richieste ripetibili, Idempotency-Key generata e riusata,
versamento multipart e per riferimento con parti parallele e riprendibili da
file o flusso (sincrono e asincrono), controlli d'integrità, attesa dello stato
con intervalli crescenti, rapporto, eventi a cursore, webhook (gestione e firma),
ricerca, elenco versamenti, PdD. VD-C-091.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from verumdoc_sdk import (
    ChiaveApi,
    ErroreIntegrita,
    ErroreRete,
    ErroreServizio,
    ErroreTemporaneo,
    NonValido,
    PoliticaAttesa,
    PoliticaRitentativi,
    Rifiutato,
    RispostaNonValida,
    Sorgente,
    TempoScaduto,
    TokenBearer,
    VerumDoc,
    VerumDocAsincrono,
)
from verumdoc_sdk._comune import (
    Instradamento,
    chiave_derivata,
    credenziali_da_ambiente,
    retry_after,
)
from verumdoc_sdk.errori import CaricamentoNonUtilizzabile
from verumdoc_sdk.modelli import IndiceUD
from verumdoc_sdk.webhook import intestazione_firma, leggi_evento, verifica_firma

from tests.server_finto import ServerFinto, leggi_multipart, problema, versamento_in_elenco

CHIAVE = "vd_abcdef12_segretodiprova"
MINIMI = {"identificativo_documento": "DOC-1", "modalita_formazione": "a",
          "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
          "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme",
          "oggetto": "Fattura di prova"}


def indice(*nomi: str) -> dict:
    return {"classe_documentale": "fattura",
            "chiave": {"numero": "FT-1", "anno": "2026", "registro": "FATTURE"},
            "documenti": [{"id": f"d{i}", "tipo": "PRINCIPALE" if i == 0 else "ALLEGATO",
                           "nome_file": n, **({"parametri": MINIMI} if i == 0 else {})}
                          for i, n in enumerate(nomi)]}


class Dormiente:
    """Sostituisce time.sleep/asyncio.sleep e l'orologio: registra le attese."""

    def __init__(self) -> None:
        self.attese: list[float] = []
        self.tempo = 0.0

    def __call__(self, secondi: float) -> None:
        self.attese.append(secondi)
        self.tempo += secondi

    async def asincrono(self, secondi: float) -> None:
        self(secondi)

    def orologio(self) -> float:
        return self.tempo


@pytest.fixture
def server() -> ServerFinto:
    return ServerFinto(dimensione_parte=8)


@pytest.fixture
def dorme() -> Dormiente:
    return Dormiente()


@pytest.fixture
def vd(server, dorme):
    http = httpx.Client(transport=httpx.MockTransport(server), base_url="https://api.test")
    client = VerumDoc("https://api.test", credenziali=ChiaveApi(CHIAVE), http=http,
                      dormi=dorme, orologio=dorme.orologio, soglia_multipart=20,
                      concorrenza_parti=3)
    yield client
    http.close()


@pytest.fixture
async def vda(server, dorme):
    http = httpx.AsyncClient(transport=httpx.MockTransport(server.asincrono))
    client = VerumDocAsincrono("https://api.test", credenziali=ChiaveApi(CHIAVE), http=http,
                               dormi=dorme.asincrono, orologio=dorme.orologio,
                               soglia_multipart=20, concorrenza_parti=3)
    yield client
    await http.aclose()


# ── Credenziali e instradamento ─────────────────────────────────────

def test_chiave_api_completa_usa_x_api_key():
    assert ChiaveApi(CHIAVE).intestazioni() == {"X-Api-Key": CHIAVE}


def test_chiave_api_identificativo_e_segreto():
    h = ChiaveApi(identificativo="vd_abcdef12", segreto="s").intestazioni()
    assert h == {"X-Api-Key-Id": "vd_abcdef12", "X-Api-Key-Secret": "s"}


def test_chiave_api_vuota_o_segreto_senza_id_rifiutate():
    with pytest.raises(ValueError):
        ChiaveApi()
    with pytest.raises(ValueError):
        ChiaveApi(segreto="s")


def test_chiave_api_non_mostra_il_segreto():
    assert "segretodiprova" not in repr(ChiaveApi(CHIAVE))


def test_token_bearer_funzione_chiamata_a_ogni_richiesta():
    valori = iter(["t1", "t2"])
    t = TokenBearer(lambda: next(valori))
    assert t.intestazioni()["Authorization"] == "Bearer t1"
    assert t.intestazioni()["Authorization"] == "Bearer t2"


def test_credenziali_da_ambiente(monkeypatch):
    monkeypatch.delenv("VERUMDOC_API_KEY", raising=False)
    monkeypatch.setenv("VERUMDOC_TOKEN", "jwt")
    assert isinstance(credenziali_da_ambiente(), TokenBearer)
    monkeypatch.setenv("VERUMDOC_API_KEY", CHIAVE)
    assert credenziali_da_ambiente() == ChiaveApi(CHIAVE)


def test_instradamento_prefisso_piu_lungo():
    i = Instradamento("https://api.x", {"/api/v1/eventi": "http://n:8006", "/api/v2": "http://d"})
    assert i.url("/api/v1/eventi") == "http://n:8006/api/v1/eventi"
    assert i.url("/api/v2/distribution/search") == "http://d/api/v2/distribution/search"
    assert i.url("/api/v1/versamento/ud") == "https://api.x/api/v1/versamento/ud"


def test_intestazioni_inviate(vd, server):
    vd.stato_versamento("p1")
    r = server.richieste[-1]
    assert r.headers["x-api-key"] == CHIAVE
    assert r.headers["user-agent"].startswith("verumdoc-sdk-python/3.")


# ── Errori tipizzati ────────────────────────────────────────────────

def test_errore_non_valido_con_campi(vd, server):
    server.guasti.append(lambda r: problema(422, "VAL-422", "Dati non validi", campi=[
        {"campo": "dopo", "posizione": "query", "tipo": "int_parsing", "messaggio": "intero"}]))
    with pytest.raises(NonValido) as exc:
        vd.elenca_eventi()
    e = exc.value
    assert e.codice == "VAL-422" and e.status == 422 and e.esito == "NON_VALIDO"
    assert e.campi[0].campo == "dopo" and e.id_richiesta
    assert len(server.richieste) == 1


def test_errore_rifiutato_con_dettagli(vd, server):
    server.guasti.append(lambda r: problema(409, "VRS-124", "Chiave UD già usata",
                                            esito="RIFIUTATO",
                                            dettagli={"pdv_id_esistente": "p0"}))
    with pytest.raises(Rifiutato) as exc:
        vd.versa(indice("a.pdf"), file={"a.pdf": b"%PDF-1"})
    assert exc.value.dettagli == {"pdv_id_esistente": "p0"}
    assert len(server.richieste) == 1


def test_errore_servizio_non_ritentabile_non_ritentato(vd, server):
    server.guasti.append(lambda r: problema(500, "INTERNAL-001", "Errore interno",
                                            esito="ERRORE"))
    with pytest.raises(ErroreServizio) as exc:
        vd.stato_versamento("p1")
    assert not isinstance(exc.value, ErroreTemporaneo)
    assert len(server.richieste) == 1


def test_risposta_non_problem_502_diventa_temporanea(vd, server, dorme):
    server.guasti.append(lambda r: httpx.Response(502, text="<html>Bad gateway</html>"))
    with pytest.raises(ErroreTemporaneo) as exc:
        vd.stato_versamento("p1")
    assert exc.value.codice == "HTTP-502"
    assert len(server.richieste) == 4 and len(dorme.attese) == 3


def test_risposta_non_conforme_al_modello(vd, server):
    server.guasti.append(lambda r: httpx.Response(200, json={"inatteso": True}))
    with pytest.raises(RispostaNonValida):
        vd.stato_versamento("p1")


# ── Ritentativi ─────────────────────────────────────────────────────

def test_ritentabile_ritentato_con_retry_after_e_stessa_chiave(vd, server, dorme):
    chiamate = []

    def una_volta(r):
        if r.url.path == "/api/v1/versamento/ud" and not chiamate:
            chiamate.append(r)
            return problema(503, "VRS-501", "Database non disponibile", esito="ERRORE",
                            ritentabile=True, intestazioni={"Retry-After": "7"})
        return None

    server.guasti.append(una_volta)
    esito = vd.versa(indice("a.pdf"), file={"a.pdf": b"%PDF-1"})
    versamenti = server.richieste_di("POST", "/api/v1/versamento/ud")
    assert len(versamenti) == 2
    assert versamenti[0].headers["idempotency-key"] == versamenti[1].headers["idempotency-key"]
    assert versamenti[0].headers["idempotency-key"] == esito.chiave_idempotenza
    assert dorme.attese == [7.0]
    assert esito.stato_http == 202 and not esito.ripetuto


def test_post_non_ripetibile_non_ritentato(vd, server):
    """La riprova di una consegna webhook non ha Idempotency-Key: mai ritentata."""
    server.guasti.append(lambda r: problema(503, "WH-503", "Non disponibile", esito="ERRORE",
                                            ritentabile=True))
    with pytest.raises(ErroreTemporaneo):
        vd.riprova_consegna("w1", 7)
    assert len(server.richieste) == 1


def _una_volta(server, guasto):
    """Guasto che agisce sulla prima richiesta e poi si toglie."""
    def una(r):
        server.guasti.remove(una)
        return guasto(r)
    return una


def test_richiedi_pdd_ritentato_con_la_stessa_idempotency_key(vd, server, dorme):
    """R04 (VD-C-106): richiesta di PdD con Idempotency-Key, ritentata in sicurezza."""
    server.guasti.append(_una_volta(server, lambda r: problema(
        503, "DST-503", "Non disponibile", esito="ERRORE", ritentabile=True)))
    pdd = vd.richiedi_pdd("a1", "COMPLETO", "Controllo interno")
    richieste = server.richieste_di("POST", "/api/v2/distribution/richiedi")
    assert len(richieste) == 2 and pdd.aip_id == "a1"
    chiavi = {r.headers["idempotency-key"] for r in richieste}
    assert len(chiavi) == 1 and next(iter(chiavi)).startswith("vdsdk-")
    vd.richiedi_pdd("a1", "COMPLETO", "Controllo interno", chiave_idempotenza="erp-pdd-000123")
    ultima = server.richieste_di("POST", "/api/v2/distribution/richiedi")[-1]
    assert ultima.headers["idempotency-key"] == "erp-pdd-000123"
    with pytest.raises(ValueError):
        vd.richiedi_pdd("a1", "COMPLETO", "Controllo interno", chiave_idempotenza="no")


def test_crea_webhook_ritentato_con_la_stessa_idempotency_key(vd, server, dorme):
    def lento(r):
        raise httpx.ReadTimeout("lento")

    server.guasti.append(_una_volta(server, lento))
    creata = vd.crea_webhook("https://cliente.example.org/h", ["versamento.conservato"])
    richieste = server.richieste_di("POST", "/api/v1/webhook")
    assert len(richieste) == 2 and creata.segreto
    assert richieste[0].headers["idempotency-key"] == richieste[1].headers["idempotency-key"]


def test_errore_di_rete_ritentato_solo_se_ripetibile(vd, server, dorme):
    conteggio = {"n": 0}

    def rete(r):
        conteggio["n"] += 1
        if conteggio["n"] == 1:
            raise httpx.ConnectError("rifiutata")
        return None

    server.guasti.append(rete)
    assert vd.stato_versamento("p1").pdv_id == "p1"
    assert len(dorme.attese) == 1
    server.guasti.clear()
    server.guasti.append(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("lento")))
    with pytest.raises(ErroreRete):
        vd.riprova_consegna("w1", 7)
    assert len(dorme.attese) == 1  # nessuna nuova attesa: POST non ripetibile


def test_politica_attesa_esponenziale_con_limite():
    p = PoliticaRitentativi(attesa_iniziale=1, fattore=2, attesa_massima=5, variazione=0)
    assert [p.attesa(n) for n in (1, 2, 3, 4)] == [1, 2, 4, 5]
    assert p.attesa(1, retry_after=3) == 3
    assert p.attesa(1, retry_after=60) == 5


def test_retry_after_numero_e_data():
    from datetime import UTC, datetime

    assert retry_after("30") == 30.0
    adesso = datetime(2026, 10, 21, 10, 0, 0, tzinfo=UTC)
    assert retry_after("Wed, 21 Oct 2026 10:00:20 GMT", adesso) == 20.0
    assert retry_after("non valido") is None and retry_after(None) is None


def test_chiave_derivata_stabile_e_valida():
    a = chiave_derivata("vdsdk-abc12345", "a.pdf:10")
    assert a == chiave_derivata("vdsdk-abc12345", "a.pdf:10")
    assert a != chiave_derivata("vdsdk-abc12345", "b.pdf:10")
    with pytest.raises(ValueError):
        chiave_derivata("chiave con spazi", "x")


# ── Versamento multipart ────────────────────────────────────────────

def test_versa_multipart_calcola_impronte_e_invia_indice(vd, server, tmp_path):
    f = tmp_path / "fattura.pdf"
    f.write_bytes(b"%PDF-1.7 corpo")
    esito = vd.versa(indice("fattura.pdf", "allegato.xml"),
                     file={"fattura.pdf": f, "allegato.xml": b"<x/>"})
    [r] = server.richieste_di("POST", "/api/v1/versamento/ud")
    campi, file = leggi_multipart(r)
    inviato = json.loads(campi["indice"])
    assert inviato["documenti"][0]["hash"]["impronta"] == hashlib.sha256(
        b"%PDF-1.7 corpo").hexdigest()
    assert inviato["documenti"][1]["hash"]["impronta"] == hashlib.sha256(b"<x/>").hexdigest()
    assert "lingua" not in inviato["documenti"][0]["parametri"]  # i None non si inviano
    assert file == {"fattura.pdf": b"%PDF-1.7 corpo", "allegato.xml": b"<x/>"}
    assert esito.location == f"/api/v1/versamento/{esito.pdv_id}"
    assert server.richieste_di("POST", "/api/v1/caricamenti") == []


def test_versa_stessa_chiave_ripetuto(vd, server):
    primo = vd.versa(indice("a.pdf"), file={"a.pdf": b"%PDF"}, chiave_idempotenza="mia-chiave-1")
    secondo = vd.versa(indice("a.pdf"), file={"a.pdf": b"%PDF"}, chiave_idempotenza="mia-chiave-1")
    assert secondo.ripetuto and secondo.stato_http == 200
    assert secondo.pdv_id == primo.pdv_id


def test_versa_file_e_indice_non_corrispondenti(vd):
    with pytest.raises(ValueError, match="senza file"):
        vd.versa(indice("a.pdf", "b.pdf"), file={"a.pdf": b"x"})


def test_versa_impronta_dichiarata_diversa(vd, server):
    """In multipart la confronta il servizio (VRS-102); per riferimento l'SDK prima di
    versare (i byte sono già stati letti per le parti)."""
    ud = indice("a.pdf")
    ud["documenti"][0]["hash"] = {"impronta": "0" * 64, "algoritmo": "SHA-256"}
    with pytest.raises(NonValido) as exc:
        vd.versa(ud, file={"a.pdf": b"%PDF"})
    assert exc.value.codice == "VRS-102"
    with pytest.raises(ErroreIntegrita):
        vd.versa(ud, file={"a.pdf": b"%PDF"}, modo="riferimento")
    assert len(server.richieste_di("POST", "/api/v1/versamento/ud")) == 1


def test_versa_indice_modello_non_modificato(vd):
    ud = IndiceUD.model_validate(indice("a.pdf"))
    vd.versa(ud, file={"a.pdf": b"%PDF"})
    assert ud.documenti[0].hash is None


# ── Versamento per riferimento con sessioni di caricamento ──────────

def test_versa_file_grande_per_riferimento_parti_parallele(vd, server, tmp_path):
    contenuto = bytes(range(256)) * 2 + b"fine"  # 516 byte, parti da 8: 65 parti
    f = tmp_path / "grande.pdf"
    f.write_bytes(contenuto)
    server.ritardo_parti = 0.002
    esito = vd.versa(indice("grande.pdf"), file={"grande.pdf": f})
    parti = server.richieste_di("PUT", "/api/v1/caricamenti/")
    assert len(parti) == 65
    assert 1 < server.massimo_in_corso <= 3
    [cid] = esito.caricamenti.values()
    assert b"".join(server.caricamenti[cid]["parti"][n] for n in range(1, 66)) == contenuto
    [r] = server.richieste_di("POST", "/api/v1/versamento/ud")
    corpo = json.loads(r.content)
    assert corpo["file"] == {"grande.pdf": cid}
    assert corpo["indice"]["documenti"][0]["hash"]["impronta"] == hashlib.sha256(
        contenuto).hexdigest()
    [apertura] = [r for r in server.richieste if r.url.path == "/api/v1/caricamenti"]
    assert apertura.headers["idempotency-key"].startswith(esito.chiave_idempotenza[:20])


def test_versa_da_flusso_non_riavvolgibile(vd, server):
    contenuto = b"%PDF" + b"x" * 40

    def generatore():
        for i in range(0, len(contenuto), 5):
            yield contenuto[i:i + 5]

    s = Sorgente.da_flusso(generatore(), dimensione=len(contenuto), nome="f.pdf")
    esito = vd.versa(indice("f.pdf"), file={"f.pdf": s})
    assert esito.caricamenti and len(server.richieste_di("PUT", "/api/v1/caricamenti/")) == 6


def test_flusso_senza_dimensione_rifiutato(vd):
    s = Sorgente.da_flusso(iter([b"abc"]))
    with pytest.raises(ValueError, match="dimensione"):
        vd.versa(indice("f.pdf"), file={"f.pdf": s})


def test_flusso_piu_corto_del_dichiarato_errore_integrita(vd):
    s = Sorgente.da_flusso(iter([b"abc"]), dimensione=20, nome="f.pdf")
    with pytest.raises(ErroreIntegrita):
        vd.carica(s)


def test_file_riavvolgibile_dimensione_dedotta():
    f = io.BytesIO(b"0123456789")
    f.seek(2)
    s = Sorgente.da_flusso(f, nome="x")
    assert s.dimensione == 8 and s.ripetibile
    assert b"".join(s.blocchi()) == b"23456789" and b"".join(s.blocchi()) == b"23456789"


def test_carica_ripresa_dopo_interruzione_reinvia_solo_le_mancanti(vd, server, tmp_path):
    contenuto = b"A" * 8 + b"B" * 8 + b"C" * 8 + b"D" * 3
    f = tmp_path / "x.pdf"
    f.write_bytes(contenuto)
    vd.concorrenza_parti = 1

    def guasto_parte_3(r):
        if r.method == "PUT" and r.url.path.endswith("/parti/3"):
            return problema(400, "UPL-002", "Parte rifiutata")
        return None

    server.guasti.append(guasto_parte_3)
    with pytest.raises(NonValido):
        vd.carica(f, chiave_idempotenza="carico-x-0001")
    inviate_prima = len(server.richieste_di("PUT", "/api/v1/caricamenti/"))
    server.guasti.clear()
    fatto = vd.carica(f, chiave_idempotenza="carico-x-0001")
    nuove = server.richieste_di("PUT", "/api/v1/caricamenti/")[inviate_prima:]
    assert sorted(r.url.path.rsplit("/", 1)[1] for r in nuove) == ["3", "4"]
    assert fatto.sha256 == hashlib.sha256(contenuto).hexdigest()
    assert fatto.stato.stato == "COMPLETO"


def test_carica_riprende_con_id_sessione(vd, server):
    sessione, nuova = vd.apri_caricamento("y.pdf", 10)
    assert nuova
    vd.invia_parte(sessione.caricamento_id, 1, b"0123456789"[:8])
    fatto = vd.carica(b"0123456789", nome_file="y.pdf", caricamento_id=sessione.caricamento_id)
    assert len(server.richieste_di("PUT", "/api/v1/caricamenti/")) == 2
    assert fatto.caricamento_id == sessione.caricamento_id


def test_carica_sessione_annullata(vd):
    sessione, _ = vd.apri_caricamento("y.pdf", 10)
    vd.annulla_caricamento(sessione.caricamento_id)
    with pytest.raises(CaricamentoNonUtilizzabile):
        vd.carica(b"0123456789", nome_file="y.pdf", caricamento_id=sessione.caricamento_id)


def test_carica_impronta_del_servizio_diversa(vd, server):
    server.sha_servizio_alterato = True
    with pytest.raises(ErroreIntegrita):
        vd.carica(b"0123456789", nome_file="y.pdf")


def test_carica_verifica_asincrona_con_attesa(vd, server, dorme):
    server.verifica_immediata = False
    letture = {"n": 0}

    def verifica_dopo_due(r):
        if r.method == "GET" and r.url.path.startswith("/api/v1/caricamenti/"):
            letture["n"] += 1
            if letture["n"] == 2:
                server.verifica_in_sospeso()
        return None

    server.guasti.append(verifica_dopo_due)
    fatto = vd.carica(b"0123456789", nome_file="y.pdf")
    assert fatto.stato.stato == "COMPLETO" and dorme.attese == [1.0]


def test_carica_errore_di_verifica_diventa_eccezione(vd, server):
    s = Sorgente.da_bytes(b"0123456789", nome="y.pdf")
    sessione, _ = vd.apri_caricamento("y.pdf", 10, impronta_sha256="1" * 64,
                                      chiave_idempotenza="chiave-imp-01")
    with pytest.raises(NonValido) as exc:
        vd.carica(s, caricamento_id=sessione.caricamento_id)
    assert exc.value.codice == "UPL-006"


# ── Stato, attesa, rapporto, elenco ────────────────────────────────

def test_attendi_versamento_intervalli_crescenti(vd, server, dorme):
    server.stati_pdv["p9"] = ["RICEVUTO", "IN_VALIDAZIONE", "VALIDATO", "CONSERVATO"]
    vd.attesa = PoliticaAttesa(intervallo_iniziale=1, fattore=2, intervallo_massimo=3)
    stato = vd.attendi_versamento("p9")
    assert stato.stato == "CONSERVATO"
    assert dorme.attese == [1, 2, 3]


def test_attendi_versamento_tempo_scaduto(vd, server):
    server.stati_pdv["p8"] = ["RICEVUTO"]
    with pytest.raises(TempoScaduto) as exc:
        vd.attendi_versamento("p8", politica=PoliticaAttesa(timeout=10, intervallo_iniziale=4,
                                                             fattore=1))
    assert exc.value.ultimo_stato.stato == "RICEVUTO"


def test_scarica_rapporto_byte_e_file(vd, server, tmp_path):
    assert vd.scarica_rapporto("p1") == server.rdv
    destinazione = tmp_path / "rdv.xml"
    assert vd.scarica_rapporto("p1", destinazione) == destinazione
    assert destinazione.read_bytes() == server.rdv


def test_scarica_rapporto_impronta_errata_nessun_file(vd, server, tmp_path):
    server.impronta_rdv = "f" * 64
    destinazione = tmp_path / "rdv.xml"
    with pytest.raises(ErroreIntegrita):
        vd.scarica_rapporto("p1", destinazione)
    assert list(tmp_path.iterdir()) == []


def test_elenca_versamenti(vd, server):
    elenco = vd.elenca_versamenti("t1", stato="ERRORE", limite=5)
    assert elenco.items[0].motivi_rifiuto[0].codice == "VLD-401"
    assert elenco.limit == 5 and elenco.altri is False and elenco.cursore is None
    assert not elenco.model_extra  # forma R03: niente total/skip
    parametri = server.richieste[-1].url.params
    assert parametri["tenant_id"] == "t1" and parametri["stato"] == "ERRORE"
    assert "skip" not in parametri and "cursore" not in parametri


def test_elenca_versamenti_senza_tenant_usa_l_ente_dell_identita(vd, server):
    elenco = vd.elenca_versamenti()
    assert [v.pdv_id for v in elenco.items] == ["p1"]
    assert "tenant_id" not in server.richieste[-1].url.params


def test_elenca_versamenti_pagina_successiva_con_cursore(vd, server):
    server.elenco_versamenti = [versamento_in_elenco(f"p{i}") for i in range(5)]
    prima = vd.elenca_versamenti(limite=2)
    assert [v.pdv_id for v in prima.items] == ["p0", "p1"]
    assert prima.altri and prima.cursore
    seconda = vd.elenca_versamenti(cursore=prima.cursore, limite=2)
    assert [v.pdv_id for v in seconda.items] == ["p2", "p3"]
    assert server.richieste[-1].url.params["cursore"] == prima.cursore


def test_itera_versamenti_segue_il_cursore_fino_alla_fine(vd, server):
    server.elenco_versamenti = [versamento_in_elenco(f"p{i}", "ERRORE" if i % 2 else
                                                     "CONSERVATO") for i in range(7)]
    assert [v.pdv_id for v in vd.itera_versamenti(limite=3)] == [f"p{i}" for i in range(7)]
    pagine = server.richieste_di("GET", "/api/dashboard/versamenti")
    assert len(pagine) == 3
    assert "cursore" not in pagine[0].url.params
    assert [r.url.params["cursore"] for r in pagine[1:]] == ["cur-3", "cur-6"]
    # Con un filtro il filtro resta lo stesso su ogni pagina
    errori = [v.pdv_id for v in vd.itera_versamenti(stato="ERRORE", limite=1)]
    assert errori == ["p1", "p3", "p5"]


def test_itera_versamenti_elenco_vuoto(vd, server):
    server.elenco_versamenti = []
    assert list(vd.itera_versamenti()) == []
    assert len(server.richieste_di("GET", "/api/dashboard/versamenti")) == 1


def test_elenca_versamenti_cursore_non_valido_e_tipizzato(vd, server):
    with pytest.raises(NonValido) as errore:
        vd.elenca_versamenti(cursore="alterato")
    assert errore.value.codice == "DSH-104" and errore.value.status == 422


def test_identita_dalla_chiave_api(vd, server):
    io_ = vd.identita()
    assert io_.tenant_id == "t1" and io_.auth_method == "api_key"
    assert io_.ente.ragione_sociale == "Comune di Prova"
    assert io_.chiave_api.responsabile.cognome == "Rossi"
    assert not io_.model_extra and not io_.chiave_api.responsabile.model_extra
    assert server.richieste[-1].url.path == "/api/v1/me"


# ── Eventi e webhook ────────────────────────────────────────────────

def _eventi(n: int) -> list[dict]:
    return [{"id": i, "tipo": "versamento.ricevuto", "creato_il": "2026-10-21T10:00:00Z",
             "risorsa": {"tipo": "versamento", "id": f"p{i}"}, "dati": {"pdv_id": f"p{i}"}}
            for i in range(1, n + 1)]


def test_itera_eventi_cursore_e_la_posizione_non_l_id(vd, server):
    """R03: l'id è l'identità dell'evento, la posizione è il cursore. Un evento con
    id basso pubblicato tardi (posizione alta) non si perde."""
    eventi = _eventi(4)
    for evento, posizione in zip(eventi, (10, 11, 13, 12), strict=True):
        evento["posizione"] = posizione
    server.eventi = sorted(eventi, key=lambda e: e["posizione"])
    letti = list(vd.itera_eventi(dopo=10))
    assert [(e.id, e.posizione) for e in letti] == [(2, 11), (4, 12), (3, 13)]
    dopo = [r.url.params["dopo"] for r in server.richieste_di("GET", "/api/v1/eventi")]
    assert dopo == ["10", "12"]


def test_itera_eventi_segue_il_cursore(vd, server):
    server.eventi = _eventi(5)
    ids = [e.id for e in vd.itera_eventi(dopo=1, tipi=["versamento.ricevuto"])]
    assert ids == [2, 3, 4, 5]
    dopo = [r.url.params["dopo"] for r in server.richieste_di("GET", "/api/v1/eventi")]
    assert dopo == ["1", "3"]  # la seconda pagina ha altri=false
    assert server.richieste[-1].url.params.get_list("tipo") == ["versamento.ricevuto"]


def test_gestione_webhook(vd, server):
    creata = vd.crea_webhook("https://cliente.example.org/eventi", ["versamento.conservato"],
                             descrizione="ERP")
    assert creata.segreto.startswith("whsec_") and creata.descrizione == "ERP"
    assert [w.id for w in vd.elenca_webhook()] == ["w1"]
    assert vd.leggi_webhook("w1").attiva
    assert vd.modifica_webhook("w1", attiva=False).attiva is False
    with pytest.raises(ValueError):
        vd.modifica_webhook("w1", segreto="x")
    assert vd.elenca_consegne("w1", stato="FALLITA").consegne[0].ultimo_codice == "WH-103"
    assert vd.riprova_consegna("w1", 3).stato == "IN_CODA"
    vd.elimina_webhook("w1")
    assert server.richieste[-1].method == "DELETE"


CORPO_ESEMPIO = (b'{"creato_il":"2026-10-21T10:00:00+00:00","dati":{"pdv_id":'
                 b'"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},"id":42,"risorsa":{"id":'
                 b'"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90","tipo":"versamento"},'
                 b'"tipo":"versamento.ricevuto"}')
FIRMA_ESEMPIO = "t=1792576800,v1=362a779b910d922709231ed641f88061f03db35fa8a4a1bc939f4afe65b228ad"
SEGRETO_ESEMPIO = "whsec_esempio-di-segreto-non-usare"


def test_verifica_firma_esempio_documentato():
    """L'esempio di docs/integrazione/webhook.md §5.3 è valido."""
    assert verifica_firma(SEGRETO_ESEMPIO, FIRMA_ESEMPIO, CORPO_ESEMPIO, adesso=1792576800)
    assert leggi_evento(CORPO_ESEMPIO).risorsa.id == "3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"


def test_verifica_firma_corpo_alterato_o_segreto_errato():
    alterato = CORPO_ESEMPIO.replace(b"42", b"43")
    assert not verifica_firma(SEGRETO_ESEMPIO, FIRMA_ESEMPIO, alterato, adesso=1792576800)
    assert not verifica_firma("whsec_altro", FIRMA_ESEMPIO, CORPO_ESEMPIO, adesso=1792576800)


def test_verifica_firma_fuori_tolleranza_anti_replay():
    assert not verifica_firma(SEGRETO_ESEMPIO, FIRMA_ESEMPIO, CORPO_ESEMPIO,
                              adesso=1792576800 + 301)
    assert verifica_firma(SEGRETO_ESEMPIO, FIRMA_ESEMPIO, CORPO_ESEMPIO,
                          adesso=1792576800 + 300)


@pytest.mark.parametrize("intestazione", ["", "v1=abc", "t=x,v1=abc", "t=1792576800", None])
def test_verifica_firma_intestazione_malformata(intestazione):
    assert not verifica_firma(SEGRETO_ESEMPIO, intestazione, CORPO_ESEMPIO, adesso=1792576800)


def test_verifica_firma_piu_valori_v1_rotazione():
    nuova = intestazione_firma("whsec_nuovo", 100, b"{}")
    doppia = nuova + ",v1=" + intestazione_firma("whsec_vecchio", 100, b"{}").split("v1=")[1]
    assert verifica_firma("whsec_vecchio", doppia, b"{}", adesso=100)
    assert verifica_firma("whsec_nuovo", doppia, b"{}", adesso=100)


# ── Ricerca e distribuzione ─────────────────────────────────────────

def test_itera_ricerca_segue_il_cursore(vd, server):
    risultati = list(vd.itera_ricerca("fattura", classe_documentale="fattura", anno=2026))
    assert [r.aip_id for r in risultati] == ["a1", "a2", "a3"]
    pagina = vd.cerca("fattura")
    assert pagina.total_esatto is True and pagina.campi_testo == ["oggetto", "produttore",
                                                                  "nomi_file"]
    corpi = [json.loads(r.content) for r in server.richieste_di("POST", "/api/v2/distribution")]
    assert corpi[0] == {"limit": 20, "query": "fattura", "classe_documentale": "fattura",
                        "anno": "2026"}
    assert corpi[1]["cursore"] == "c2"


def test_richiedi_e_attendi_pdd(vd, server, dorme):
    pdd = vd.richiedi_pdd("a1", "COMPLETO", "Richiesta di audit", consegna="ZIP")
    assert pdd.stato == "IN_PREPARAZIONE"
    pronto = vd.attendi_pdd(pdd.pdd_id)
    assert pronto.stato == "PRONTO" and pronto.zip.url
    corpo = json.loads(server.richieste_di("POST", "/api/v2/distribution/richiedi")[0].content)
    assert corpo == {"aip_id": "a1", "tipo": "COMPLETO", "motivo": "Richiesta di audit",
                     "consegna": "ZIP"}


def test_tipo_pdd_non_valido(vd):
    with pytest.raises(ValueError):
        vd.richiedi_pdd("a1", "TUTTO", "motivo lungo")


# ── Client asincrono ────────────────────────────────────────────────

async def test_asincrono_versa_flusso_asincrono_parti_parallele(vda, server):
    contenuto = b"%PDF" + bytes(range(200))

    async def flusso():
        for i in range(0, len(contenuto), 7):
            await asyncio.sleep(0)
            yield contenuto[i:i + 7]

    server.ritardo_parti = 0.005
    s = Sorgente.da_flusso(flusso(), dimensione=len(contenuto), nome="f.pdf")
    esito = await vda.versa(indice("f.pdf"), file={"f.pdf": s})
    [cid] = esito.caricamenti.values()
    assert len(server.caricamenti[cid]["parti"]) == 26
    assert 1 < server.massimo_in_corso <= 3
    dati = b"".join(server.caricamenti[cid]["parti"][n] for n in range(1, 27))
    assert dati == contenuto


async def test_asincrono_multipart_da_file(vda, server, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-abc")
    esito = await vda.versa(indice("a.pdf"), file={"a.pdf": Path(f)})
    [r] = server.richieste_di("POST", "/api/v1/versamento/ud")
    assert leggi_multipart(r)[1] == {"a.pdf": b"%PDF-abc"}
    assert esito.stato_http == 202


async def test_asincrono_ritentativo_e_errori(vda, server, dorme):
    volte = {"n": 0}

    def una_volta(r):
        volte["n"] += 1
        if volte["n"] == 1:
            return problema(429, "QTA-001", "Troppe richieste", esito="ERRORE",
                            ritentabile=True, intestazioni={"Retry-After": "2"})
        return None

    server.guasti.append(una_volta)
    server.eventi = _eventi(3)
    eventi = [e.id async for e in vda.itera_eventi()]
    assert eventi == [1, 2, 3] and dorme.attese == [2.0]
    server.guasti.append(lambda r: problema(404, "WH-404", "Webhook non trovato"))
    with pytest.raises(NonValido):
        await vda.leggi_webhook("w9")


async def test_asincrono_rapporto_attesa_ricerca_pdd(vda, server, tmp_path):
    server.stati_pdv["p7"] = ["RICEVUTO", "CONSERVATO"]
    assert (await vda.attendi_versamento("p7")).stato == "CONSERVATO"
    destinazione = tmp_path / "r.xml"
    await vda.scarica_rapporto("p7", destinazione)
    assert destinazione.read_bytes() == server.rdv
    assert [r.aip_id async for r in vda.itera_ricerca()] == ["a1", "a2", "a3"]
    pdd = await vda.attendi_pdd((await vda.richiedi_pdd("a1", "COMPLETO", "motivo x")).pdd_id)
    assert pdd.stato == "PRONTO"
    assert [v.pdv_id for v in (await vda.elenca_versamenti("t1")).items] == ["p1"]
    server.elenco_versamenti = [versamento_in_elenco(f"p{i}") for i in range(5)]
    assert [v.pdv_id async for v in vda.itera_versamenti(limite=2)] == [
        f"p{i}" for i in range(5)]
    assert (await vda.identita()).ente.id == "t1"


async def test_asincrono_carica_ripresa_con_chiave(vda, server):
    vda.concorrenza_parti = 1
    contenuto = b"Z" * 20

    def guasto(r):
        if r.method == "PUT" and r.url.path.endswith("/parti/2"):
            return problema(400, "UPL-002", "Parte rifiutata")
        return None

    server.guasti.append(guasto)
    with pytest.raises(NonValido):
        await vda.carica(contenuto, nome_file="z.pdf", chiave_idempotenza="ripresa-asinc-1")
    server.guasti.clear()
    fatto = await vda.carica(contenuto, nome_file="z.pdf", chiave_idempotenza="ripresa-asinc-1")
    assert fatto.sha256 == hashlib.sha256(contenuto).hexdigest()
    numeri = [r.url.path.rsplit("/", 1)[1] for r in server.richieste_di("PUT", "/api/v1/car")]
    assert numeri == ["1", "2", "2", "3"]
