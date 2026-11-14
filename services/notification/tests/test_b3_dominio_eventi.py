# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Catalogo degli eventi per i clienti e proiezione dagli eventi Kafka —
contratto C3 (agente R03 b3), REGOLE §19.9 (niente dati interni o personali).
"""

from __future__ import annotations

import uuid

import pytest
from app.config import Settings
from app.domain.eventi import (
    TIPI_EVENTO,
    EventoNonValido,
    proietta,
    tipi_ammessi,
    tipi_sconosciuti,
)

T = str(uuid.uuid4())
PDV = str(uuid.uuid4())
AIP = str(uuid.uuid4())


def test_tipi_evento_catalogo_contratto_c3_completo() -> None:
    """I nomi del contratto C3 sono tutti nel catalogo, stabili e al passato
    (R04, contratto K1: in più ``catena.checkpoint``; integrazione R04:
    ``versamento.staging_scaduto`` e ``integrita.anomalia``)."""
    assert set(TIPI_EVENTO) == {
        "versamento.ricevuto", "versamento.validato", "versamento.rifiutato",
        "versamento.conservato", "caricamento.completato", "caricamento.errore",
        "pdd.pronto", "catena.checkpoint", "versamento.staging_scaduto",
        "integrita.anomalia",
        # R05 (contratto Q2): ciclo di vita
        "pratica.cambio_stato", "versamento.annullato", "versamento.rettificato",
        "scarto.eseguito", "riversamento.pronto",
        # R07 (D13, D17): avvisi automatici all'ente
        "quota.soglia_raggiunta", "idempotenza.chiave_omessa",
        # R09 (D20, D21, D23): avvisi del ciclo di vita
        "ciclo_vita.avviso",
    }
    assert tipi_ammessi() == sorted(TIPI_EVENTO)


def test_eventi_topic_predefiniti_mappano_su_tipi_noti() -> None:
    """Ogni topic configurato di default porta a un tipo del catalogo (VD-C-097: aip.conserved)."""
    mappa = Settings().eventi_topic
    assert not tipi_sconosciuti(list(mappa.values()))
    assert mappa["aip.conserved"] == "versamento.conservato"
    assert mappa["pdv.invalid"] == "versamento.rifiutato"


def test_tipi_sconosciuti_senza_ripetizioni() -> None:
    assert tipi_sconosciuti(["pdd.pronto", "x.y", "x.y", "z"]) == ["x.y", "z"]
    assert tipi_sconosciuti([]) == []


def test_proietta_versamento_ricevuto_solo_campi_ammessi() -> None:
    """L'evento pdv.received porta indice, chiavi S3 e RdV: nei dati del cliente non finiscono."""
    evento = {
        "pdv_id": PDV, "tenant_id": T, "classe_documentale": "fattura",
        "user_id": str(uuid.uuid4()),
        "canale": "REST", "num_documenti": 2, "received_at": "2026-10-21T10:00:00+00:00",
        "indice": {"chiave": {"registro": "PROT", "anno": "2026", "numero": "12"},
                   "documenti": [{"nome_file": "Mario Rossi.pdf"}]},
        "docs_s3_keys": ["versamenti/x/y"], "rdv_preliminare_hash": "ab",
        "avvisi": [{"codice": "RGL-201", "messaggio": "x"}], "allegato5": {"nome": "Mario"},
    }
    ev = proietta("versamento.ricevuto", evento)
    assert (ev.tenant_id, ev.tipo, ev.risorsa_tipo, ev.risorsa_id) == (
        T, "versamento.ricevuto", "versamento", PDV)
    assert ev.dati == {
        "pdv_id": PDV, "classe_documentale": "fattura", "canale": "REST", "num_documenti": 2,
        "chiave": {"registro": "PROT", "anno": "2026", "numero": "12"},
        "ricevuto_il": "2026-10-21T10:00:00+00:00", "avvisi": ["RGL-201"],
    }
    testo = str(ev.dati)
    for vietato in ("s3", "Mario", "user_id", "allegato5", "nome_file"):
        assert vietato not in testo


def test_proietta_versamento_rifiutato_errori_ridotti() -> None:
    risultati = [{"codice": "VLD-101", "valido": False, "msg": "formato"},
                 {"codice": "VLD-102", "valido": True, "msg": "ok"}]
    risultati += [{"codice": f"VLD-{i}", "valido": False, "msg": "m" * 900} for i in range(30)]
    ev = proietta("versamento.rifiutato", {"pdv_id": PDV, "tenant_id": T, "risultati": risultati})
    assert ev.dati["errori"][0] == {"codice": "VLD-101", "messaggio": "formato"}
    assert len(ev.dati["errori"]) == 20
    assert all(len(e["messaggio"]) <= 500 for e in ev.dati["errori"])


def test_proietta_versamento_conservato_dichiara_la_fase_vdc097() -> None:
    """VD-C-097: l'evento parte con il PdA su storage, prima del sigillo: il cliente lo legge."""
    ev = proietta("versamento.conservato", {
        "aip_id": AIP, "pdv_id": PDV, "tenant_id": T, "urn": "urn:x", "hash_sha256": "f" * 64,
        "s3_key": "aip/segreto", "s3_bucket": "b", "data_conservazione": "2026-10-21"})
    assert ev.risorsa_id == PDV and ev.dati["aip_id"] == AIP
    assert ev.dati["fase"] == "PDA_SU_STORAGE"
    assert "s3_key" not in ev.dati and "s3_bucket" not in ev.dati


def test_proietta_caricamento_e_pdd_ed_esibizione() -> None:
    """R03 i1: l'esibizione arriva come pdd.pronto con tipo ESIBIZIONE; il tipo
    «esibizione.pronta» (nessun produttore) non esiste più."""
    car = str(uuid.uuid4())
    ev = proietta("caricamento.errore", {
        "tenant_id": T, "caricamento_id": car, "stato": "ERRORE", "nome_file": "privato.pdf",
        "errore": {"codice": "UPL-422", "title": "Impronta errata", "detail": "file privato.pdf"}})
    assert ev.risorsa_tipo == "caricamento" and ev.risorsa_id == car
    assert ev.dati == {"caricamento_id": car, "stato": "ERRORE",
                       "errore": {"codice": "UPL-422", "title": "Impronta errata"}}
    pdd = str(uuid.uuid4())
    ev = proietta("pdd.pronto", {"tenant_id": T, "pdd_id": pdd, "tipo": "COMPLETO",
                                 "link": "https://s3/prefirmato?firma=segreta"})
    assert ev.dati == {"pdd_id": pdd, "tipo": "COMPLETO"}
    ev = proietta("pdd.pronto", {"evento": "pdd.pronto", "tenant_id": T, "pdd_id": pdd,
                                 "aip_id": AIP, "tipo": "ESIBIZIONE",
                                 "scade_il": "2026-10-24T10:00:00+00:00"})
    assert ev.risorsa_tipo == "pdd" and ev.risorsa_id == pdd
    assert ev.dati == {"pdd_id": pdd, "tipo": "ESIBIZIONE",
                       "scade_il": "2026-10-24T10:00:00+00:00"}
    with pytest.raises(EventoNonValido):
        proietta("esibizione.pronta", {"tenant_id": T, "esibizione_id": str(uuid.uuid4())})


@pytest.mark.parametrize("evento", [
    {"pdv_id": PDV},                                   # senza cliente
    {"pdv_id": PDV, "tenant_id": "non-uuid"},
    {"tenant_id": T},                                  # senza risorsa
    {"tenant_id": T, "pdv_id": "123"},
    "non un oggetto",
])
def test_proietta_evento_incompleto_rifiutato(evento) -> None:
    with pytest.raises(EventoNonValido):
        proietta("versamento.validato", evento)


def test_proietta_tipo_sconosciuto_rifiutato() -> None:
    with pytest.raises(EventoNonValido, match="sconosciuto"):
        proietta("versamento.inventato", {"tenant_id": T, "pdv_id": PDV})


def test_r07_avvisi_automatici_proiettati_e_notificati() -> None:
    """R07 (D13, D17, D15): avvisi automatici come eventi per il cliente con dati
    ridotti e come e-mail al referente; lo staging porta il collegamento «Rielabora»."""
    import uuid as _uuid

    from app.domain.eventi import proietta
    from app.services.destinatari import EVENTI_EMAIL_REFERENTE
    from app.services.elaborazione import TOPIC_EVENTI, contenuto_evento

    tid, aid = str(_uuid.uuid4()), str(_uuid.uuid4())
    quota = {"tipo": "quota.soglia_raggiunta", "avviso_id": aid, "tenant_id": tid,
             "chiave": "2026-10-30:PDV:80", "quota": "PDV_GIORNO", "soglia": 80,
             "limite": 1000, "utilizzo": 800, "giorno": "2026-10-30", "piano": "starter",
             "piano_suggerito": "business", "interno": {"non": "esposto"}}
    evento = proietta("quota.soglia_raggiunta", quota)
    assert evento.risorsa_id == aid and evento.tenant_id == tid
    assert evento.dati["soglia"] == 80 and "interno" not in evento.dati
    idem = proietta("idempotenza.chiave_omessa", {
        "avviso_id": aid, "tenant_id": tid, "versamenti": 12, "fino_al": "2027-03-31"})
    assert idem.dati == {"versamenti": 12, "fino_al": "2027-03-31"}
    for topic in ("quota.soglia_raggiunta", "idempotenza.chiave_omessa",
                  "versamento.staging_scaduto", "ciclo_vita.avviso"):
        assert topic in TOPIC_EVENTI and topic in EVENTI_EMAIL_REFERENTE
    # R09: avvisi del ciclo di vita in parole semplici, solo campi ammessi
    avviso = {"avviso_id": aid, "tenant_id": tid, "sottotipo": "PROPOSTA_SCARTO",
              "pratica_id": "p-1", "selezionabili": 4, "interno": "no"}
    assert proietta("ciclo_vita.avviso", avviso).dati == {
        "sottotipo": "PROPOSTA_SCARTO", "pratica_id": "p-1", "selezionabili": 4}
    _, oggetto, testo, _ = contenuto_evento("ciclo_vita.avviso", avviso)
    assert "Proposta di scarto" in oggetto and "Nulla viene cancellato" in testo
    oggetto_id, oggetto, testo, html = contenuto_evento("quota.soglia_raggiunta", quota)
    assert oggetto_id == aid and "80%" in oggetto and "business" in testo
    pdv = str(_uuid.uuid4())
    staging = {"pdv_id": pdv, "tenant_id": tid, "scade_il": "2026-11-06T10:00:00+00:00",
               "rielabora": f"https://app.esempio.it/code-scarto?pdv_id={pdv}"}
    assert proietta("versamento.staging_scaduto", staging).dati["rielabora"].endswith(pdv)
    _, _, testo, html = contenuto_evento("versamento.staging_scaduto", staging)
    assert "Rielabora" in html and staging["rielabora"] in testo
