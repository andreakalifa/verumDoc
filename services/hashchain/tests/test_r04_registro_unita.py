# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prove senza database del registro R04: configurazione e chiavi, client del
testimone (K3), foglie del sequenziatore, RdV v2 e sua verifica offline.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from app.registro import chiavi
from app.registro.rdv import costruisci_rdv, verifica_rdv
from app.registro.sequenziatore import TIPO_PDA, TIPO_RIVERSAMENTO, foglia_per
from app.registro.testimone import ClientTestimone, corpo_richiesta
from pydantic import SecretStr

from libs.catena import nota
from libs.catena.foglie import ComponentePda, albero_pda, dati_foglia_registro
from libs.catena.rfc6962 import esadecimale, hash_foglia, prova_inclusione, radice

QUANDO = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)


def _cfg(**altro):
    base = {"env": "development", "catena_origine": "",
            "catena_chiave_privata": SecretStr(nota.genera_chiave("verumdoc-registro")[0]),
            "testimone_url": "", "testimone_verificatore": "",
            "testimone_service_token": SecretStr(""), "catena_checkpoint_secondi": 60.0,
            "catena_controfirma_max_secondi": 600.0,
            "catena_sequenziatore_intervallo_secondi": 1.0, "catena_lease_secondi": 30.0,
            "catena_sequenziatore_lotto": 1000, "catena_completamento_lotto": 200,
            "catena_worm_segmento_foglie": 10000}
    base.update(altro)
    return SimpleNamespace(**base)


# ── Configurazione (§19.4) ──────────────────────────────────────────

def test_origine_per_ambiente():
    assert chiavi.origine_registro(_cfg(env="production")) == "verumdoc.it/registro/v1"
    assert chiavi.origine_registro(_cfg(env="staging")) == "verumdoc.it/registro/v1/staging"
    assert chiavi.origine_registro(_cfg(catena_origine="x/y")) == "x/y"


def test_configurazione_valida_nessun_problema():
    assert chiavi.problemi_configurazione(_cfg()) == []


@pytest.mark.parametrize("modifica,atteso", [
    ({"catena_chiave_privata": SecretStr("")}, "CATENA_CHIAVE_PRIVATA non impostata"),
    ({"catena_chiave_privata": SecretStr("PRIVATE+KEY+altro+0+AA==")}, "non valida"),
    ({"testimone_url": "http://t"}, "senza TESTIMONE_VERIFICATORE"),
    ({"testimone_url": "http://t",
      "testimone_verificatore": nota.genera_chiave("verumdoc-testimone")[1]},
     "senza TESTIMONE_SERVICE_TOKEN"),
    ({"testimone_url": "http://t", "testimone_service_token": SecretStr("t"),
      "testimone_verificatore": nota.genera_chiave("altro-nome")[1]},
     "atteso 'verumdoc-testimone'"),
    ({"catena_checkpoint_secondi": 0}, "CATENA_CHECKPOINT_SECONDI"),
    ({"catena_sequenziatore_lotto": 0}, "CATENA_SEQUENZIATORE_LOTTO"),
])
def test_configurazione_incoerente_ferma_l_avvio(modifica, atteso):
    cfg = _cfg(**modifica)
    with pytest.raises(chiavi.ConfigurazioneCatenaNonValida, match=atteso):
        chiavi.verifica_configurazione(cfg)


def test_chiave_della_chiave_privata_mai_nei_messaggi():
    privata = nota.genera_chiave("verumdoc-testimone")[0]   # nome sbagliato
    problemi = chiavi.problemi_configurazione(_cfg(catena_chiave_privata=SecretStr(privata)))
    assert problemi and privata.split("+", 4)[-1] not in " ".join(problemi)


def test_test_senza_chiave_usa_chiave_effimera():
    cfg = _cfg(env="test", catena_chiave_privata=SecretStr(""))
    assert chiavi.problemi_configurazione(cfg) == []
    assert chiavi.firmatario_registro(cfg).nome == "verumdoc-registro"


# ── Foglie del sequenziatore ─────────────────────────────────────────

def test_foglia_per_pda_e_riversamento():
    riga = {"urn": "urn:a", "tenant_id": "ente", "radice": "ab" * 32, "hash_sha256": "cd" * 32}
    tipo, dati = foglia_per(riga, QUANDO)
    assert tipo == TIPO_PDA and dati == dati_foglia_registro("urn:a", "ab" * 32, "ente", QUANDO)
    tipo, dati = foglia_per({**riga, "radice": None, "numero_blocco": 3,
                             "hash_blocco": "ef" * 32, "versione_formato": 1}, QUANDO)
    assert tipo == TIPO_RIVERSAMENTO
    assert json.loads(dati)["blocco"] == {"numero": 3, "hash": "ef" * 32, "formato": 1}
    assert json.loads(dati)["impronta_pda"] == "cd" * 32


# ── Client del testimone (K3) ────────────────────────────────────────

def _nota() -> tuple[str, str]:
    priv, _ = nota.genera_chiave("verumdoc-registro")
    corpo = nota.formatta(nota.Checkpoint("o", 3, hashlib.sha256(b"r").digest()))
    return corpo, nota.componi(corpo, [nota.analizza_firmatario(priv).firma(corpo)])


def _client(gestore, verificatore) -> ClientTestimone:
    return ClientTestimone("http://t", "tok", verificatore, transport=httpx.MockTransport(gestore))


def test_corpo_richiesta_formato_k3():
    _, firmata = _nota()
    corpo = corpo_richiesta(2, [b"\x01" * 32, b"\x02" * 32], firmata)
    righe = corpo.split("\n")
    assert righe[0] == "old 2" and len(righe[1]) == 44 and righe[3] == ""
    assert corpo.endswith(firmata)


async def test_testimone_200_controfirma_verificata():
    corpo, firmata = _nota()
    tes_priv, _ = nota.genera_chiave("verumdoc-testimone")
    tes = nota.analizza_firmatario(tes_priv)
    ricevute = []

    def gestore(r: httpx.Request) -> httpx.Response:
        ricevute.append(r)
        return httpx.Response(200, text=tes.firma(corpo) + "\n")

    esito = await _client(gestore, tes.verificatore).invia(0, [], firmata)
    assert esito.esito == "CONTROFIRMATO" and esito.controfirma.startswith("— verumdoc-testimone ")
    assert ricevute[0].headers["authorization"] == "Bearer tok"
    assert ricevute[0].headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("stato,testo,esito,dimensione", [
    (409, "5\n", "DIMENSIONE_DIVERSA", 5),
    (409, "boh", "DA_RITENTARE", None),
    (403, "firma", "RIFIUTATO", None),
    (422, "prova", "RIFIUTATO", None),
    (500, "giù", "DA_RITENTARE", None),
    (200, "nessuna firma\n", "RIFIUTATO", None),
])
async def test_testimone_risposte(stato, testo, esito, dimensione):
    _, firmata = _nota()
    privata = nota.genera_chiave("verumdoc-testimone")[0]
    verificatore = nota.analizza_firmatario(privata).verificatore
    r = await _client(lambda _r: httpx.Response(stato, text=testo), verificatore).invia(
        0, [], firmata)
    assert (r.esito, r.dimensione_testimone) == (esito, dimensione)


async def test_testimone_irraggiungibile_da_ritentare():
    def gestore(_r):
        raise httpx.ConnectError("rifiutata")

    _, firmata = _nota()
    v = nota.analizza_firmatario(nota.genera_chiave("verumdoc-testimone")[0]).verificatore
    assert (await _client(gestore, v).invia(0, [], firmata)).esito == "DA_RITENTARE"


# ── RdV v2 ──────────────────────────────────────────────────────────

def _rdv(riversamento: bool = False, *, controfirma: bool = False, rapporto_firme=None):
    priv, pub = nota.genera_chiave("verumdoc-registro")
    firmatario = nota.analizza_firmatario(priv)
    urn = "urn:x:1"
    sha = hashlib.sha256(b"file").hexdigest()
    alb = albero_pda(urn, [ComponentePda("d1", "f.pdf", sha, {"a": 1}, 0)],
                     hashlib.sha256(b"ipda").hexdigest())
    if riversamento:
        foglia = json.dumps({"v": 1, "tipo": "riversamento", "pda": urn, "impronta_pda": "ab" * 32,
                             "ente": "e", "conservato_il": "2026-10-23T08:00:00.000000Z"},
                            sort_keys=True, separators=(",", ":"))
    else:
        foglia = dati_foglia_registro(urn, alb.radice_hex, "e", QUANDO).decode()
    foglie = [hash_foglia(b"altra"), hash_foglia(foglia.encode()), hash_foglia(b"terza")]
    corpo = nota.formatta(nota.Checkpoint("o", 3, radice(foglie)))
    firmata = nota.componi(corpo, [firmatario.firma(corpo)])
    testimone = None
    if controfirma:
        testimone = nota.analizza_firmatario(nota.genera_chiave("verumdoc-testimone")[0])
        firmata = nota.aggiungi_firme(firmata, [testimone.firma(corpo)])
    hf = alb.hash_foglie()
    xml = costruisci_rdv(
        pdv_id="pdv-1",
        pda={"aip_id": "aip-1", "urn": urn, "impronta_manifest": "ab" * 32,
             "radice_pda": None if riversamento else alb.radice_hex, "ente": "e"},
        documenti=[{"identificativo": "d1", "nome_file": "f.pdf", "sha256": sha, "sha512": "x",
                    "prova_pda": None if riversamento else {
                        "foglia_indice": 0, "dimensione": 2,
                        "percorso": esadecimale(prova_inclusione(hf, 0))},
                    "foglia": None if riversamento else alb.foglie[0]}],
        ipda=None if riversamento else {
            "sha256": json.loads(alb.foglie[1])["ipda"], "foglia": alb.foglie[1],
            "prova_pda": {"foglia_indice": 1, "dimensione": 2,
                          "percorso": esadecimale(prova_inclusione(hf, 1))}},
        avvisi=[{"codice": "RGL-1", "effetto": "AVVISA", "messaggio": "attenzione"}],
        registro={"foglia_indice": 1, "foglia": foglia, "tipo": "PDA", "dimensione_albero": 3,
                  "percorso": esadecimale(prova_inclusione(foglie, 1)), "blocco_storico": None},
        checkpoint={"nota": firmata, "chiave_verificatore": pub, "dimensione": 3,
                    "radice": radice(foglie).hex(), "emesso_il": "2026-10-23T08:00:00.000000Z",
                    "stato_sigillo": "NON_FIRMATO", "motivo_sigillo": "non firmato",
                    "data_marca": None,
                    "stato_testimone": "CONTROFIRMATO" if controfirma else "NON_CONFIGURATO",
                    "controfirmato_il": None, "esito_testimone": None},
        conservato_il="2026-10-23T08:00:00.000000Z", emesso_il="2026-10-23T08:01:00.000000Z",
        conservatore="VerumDoc", rapporto_firme=rapporto_firme)
    return xml, pub, testimone


def test_rdv_cita_il_rapporto_di_verifica_delle_firme():
    """R08 (D01): il RdV cita il rapporto delle firme conservato nel PdA (nome e
    impronta); la verifica offline del RdV resta valida."""
    impronta = hashlib.sha256(b"rapporto").hexdigest()
    xml, pub, _ = _rdv(rapporto_firme={"chiave": "aip/e/aip-1/rapporto_verifica_firme.json",
                                       "sha256": impronta})
    assert verifica_rdv(xml, [pub]) == []
    assert b"VerificaFirme" in xml
    assert impronta.encode() in xml and b"rapporto_verifica_firme.json" in xml
    senza, _, _ = _rdv()
    assert b"VerificaFirme" not in senza


def test_rdv_valido_e_deterministico():
    xml, pub, _ = _rdv()
    assert verifica_rdv(xml, [pub]) == []
    assert xml.startswith(b"<?xml") and b"https://schemi.verumdoc.it/rdv/definitivo/2" in xml
    assert b"agid" not in xml.lower()


def test_rdv_riversamento_valido_senza_albero():
    xml, pub, _ = _rdv(riversamento=True)
    assert verifica_rdv(xml, [pub]) == []
    assert b'riversamento="true"' in xml and b"ProvaPdA" not in xml


def test_rdv_controfirma_verificata_se_richiesto():
    xml, pub, testimone = _rdv(controfirma=True)
    assert verifica_rdv(xml, [pub], [testimone.verificatore]) == []
    altro = nota.analizza_firmatario(nota.genera_chiave("verumdoc-testimone")[0]).verificatore
    assert verifica_rdv(xml, [pub], [altro]) == [
        "Checkpoint dichiarato controfirmato senza controfirma valida"]


@pytest.mark.parametrize("vecchio,nuovo", [
    ('dimensione="3" radice=', 'dimensione="4" radice='),    # attributi diversi dalla nota
    ('<Esito>CONSERVATO</Esito>', '<Esito>CONSERVATO</Esito>'),  # nessuna modifica (controllo)
])
def test_rdv_attributi_checkpoint(vecchio, nuovo):
    xml, pub, _ = _rdv()
    alterato = xml.decode().replace(vecchio, nuovo, 1).encode()
    problemi = verifica_rdv(alterato, [pub])
    assert bool(problemi) == (vecchio != nuovo)


def test_rdv_non_xml_o_altra_versione():
    assert verifica_rdv(b"<non", []) != []
    assert verifica_rdv(b"<RicevutaVersamento/>", []) == ["Non è un RdV definitivo versione 2"]
