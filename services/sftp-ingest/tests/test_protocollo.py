# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Regole pure del protocollo delle cartelle SFTP (app/domain/protocollo.py, chiavi.py)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.chiavi import motivo_chiave_non_ammessa
from app.domain.protocollo import (
    ErroreCartella,
    StatoCartella,
    StatoEsito,
    TipoVoce,
    VoceCartella,
    chiave_caricamento,
    chiave_idempotenza,
    controlla_contenuto,
    costruisci_esito,
    impronte_dichiarate,
    nome_cartella_temporanea_valido,
    nome_cartella_valido,
    nome_file_valido,
    nomi_file_dichiarati,
    problema_ritentabile,
    ritardo_tentativo,
    valuta_cartella,
)


def _f(nome: str, mtime: float = 0.0, tipo: TipoVoce = TipoVoce.FILE) -> VoceCartella:
    return VoceCartella(nome, tipo, 10, mtime)


@pytest.mark.parametrize("nome", ["PDV-2026-001", "a", "x.y_z-1", "A" * 128])
def test_nome_cartella_valido_ammessi_vero(nome: str) -> None:
    assert nome_cartella_valido(nome)


@pytest.mark.parametrize("nome", ["", ".nascosta", "-x", "a b", "a/b", "pdv.tmp", "PDV.TMP",
                                  "A" * 129, "è"])
def test_nome_cartella_valido_non_ammessi_falso(nome: str) -> None:
    assert not nome_cartella_valido(nome)


def test_nome_cartella_temporanea_valido_suffisso_tmp() -> None:
    assert nome_cartella_temporanea_valido("PDV-1.tmp")
    assert not nome_cartella_temporanea_valido("PDV-1")
    assert not nome_cartella_temporanea_valido("-x.tmp")


@pytest.mark.parametrize("nome,atteso", [
    ("fattura.pdf", True), ("Allegato 1 (firmato).p7m", True), (".tmp-indice.json", True),
    ("", False), (".", False), ("..", False), ("a/b.pdf", False), ("a\\b.pdf", False),
    ("a\x00b", False), ("a\nb", False), ("x" * 256, False), ("è" * 127, True),
    ("è" * 128, False), ("a‮b", False),
])
def test_nome_file_valido_casi(nome: str, atteso: bool) -> None:
    assert nome_file_valido(nome) is atteso


def test_valuta_cartella_completa_e_ferma_pronta() -> None:
    voci = [_f("indice.json", 100), _f("a.pdf", 90)]
    stato = valuta_cartella(voci, modificata_il=100, adesso=200, stabilita_secondi=30,
                            abbandono_secondi=3600)
    assert stato is StatoCartella.PRONTA


def test_valuta_cartella_modificata_da_poco_in_caricamento() -> None:
    voci = [_f("indice.json", 190), _f("a.pdf", 90)]
    assert valuta_cartella(voci, modificata_il=100, adesso=200, stabilita_secondi=30,
                           abbandono_secondi=3600) is StatoCartella.IN_CARICAMENTO


def test_valuta_cartella_file_temporaneo_in_caricamento() -> None:
    voci = [_f("indice.json"), _f(".tmp-a.pdf")]
    assert valuta_cartella(voci, modificata_il=0, adesso=200, stabilita_secondi=30,
                           abbandono_secondi=3600) is StatoCartella.IN_CARICAMENTO


def test_valuta_cartella_senza_indice_ferma_a_lungo_abbandonata() -> None:
    voci = [_f("a.pdf")]
    assert valuta_cartella(voci, modificata_il=0, adesso=200, stabilita_secondi=30,
                           abbandono_secondi=3600) is StatoCartella.IN_CARICAMENTO
    assert valuta_cartella(voci, modificata_il=0, adesso=4000, stabilita_secondi=30,
                           abbandono_secondi=3600) is StatoCartella.ABBANDONATA


def test_valuta_cartella_indice_cartella_non_conta() -> None:
    voci = [_f("indice.json", tipo=TipoVoce.CARTELLA)]
    assert valuta_cartella(voci, modificata_il=0, adesso=100, stabilita_secondi=0,
                           abbandono_secondi=3600) is StatoCartella.IN_CARICAMENTO


INDICE = {
    "classe_documentale": "fattura",
    "documenti": [
        {"id": "1", "tipo": "PRINCIPALE", "nome_file": "a.pdf",
         "hash": {"impronta": "ab" * 32, "algoritmo": "SHA-256"},
         "allegati": [{"id": "2", "tipo": "ALLEGATO", "nome_file": "b.xml",
                       "hash": {"impronta": "cd" * 32}}]},
        {"id": "3", "tipo": "ANNESSO", "nome_file": "a.pdf", "hash": {"impronta": "ab" * 32}},
    ],
}


def test_nomi_file_dichiarati_documenti_e_allegati_senza_ripetizioni() -> None:
    assert nomi_file_dichiarati(INDICE) == ["a.pdf", "b.xml"]


@pytest.mark.parametrize("indice", [{}, {"documenti": []}, {"documenti": "x"},
                                    {"documenti": [1]}, {"documenti": [{"nome_file": 3}]},
                                    {"documenti": [{"nome_file": "a", "allegati": "x"}]}])
def test_nomi_file_dichiarati_forma_inattesa_none(indice: dict) -> None:
    assert nomi_file_dichiarati(indice) is None


def test_impronte_dichiarate_algoritmo_predefinito() -> None:
    assert impronte_dichiarate(INDICE) == {
        "a.pdf": {"algoritmo": "SHA-256", "valore": "ab" * 32},
        "b.xml": {"algoritmo": "SHA-256", "valore": "cd" * 32},
    }


def test_controlla_contenuto_coerente_restituisce_file_in_ordine() -> None:
    voci = [_f("indice.json"), _f("b.xml"), _f("a.pdf")]
    assert controlla_contenuto(voci, ["a.pdf", "b.xml"]) == ["a.pdf", "b.xml"]


@pytest.mark.parametrize("voci,dichiarati,codice", [
    ([_f("indice.json"), _f("a.pdf")], ["a.pdf", "b.xml"], "SFTP-103"),
    ([_f("indice.json"), _f("a.pdf"), _f("c.txt")], ["a.pdf"], "SFTP-104"),
    ([_f("indice.json"), _f("sub", tipo=TipoVoce.CARTELLA)], [], "SFTP-105"),
    ([_f("indice.json"), _f("l", tipo=TipoVoce.ALTRO)], [], "SFTP-105"),
    ([_f("indice.json"), _f(".tmp-a.pdf")], [], "SFTP-105"),
    ([_f("indice.json")], ["../x"], "SFTP-105"),
    ([_f("indice.json")], ["indice.json"], "SFTP-105"),
])
def test_controlla_contenuto_errori_codice(voci, dichiarati, codice) -> None:
    with pytest.raises(ErroreCartella) as exc:
        controlla_contenuto(voci, dichiarati)
    assert exc.value.codice == codice


def test_chiave_idempotenza_stabile_e_dipende_da_ente_cartella_indice() -> None:
    k = chiave_idempotenza("t1", "PDV-1", "aa")
    assert k == chiave_idempotenza("t1", "PDV-1", "aa")
    assert k.startswith("sftp-") and 8 <= len(k) <= 200
    assert len({k, chiave_idempotenza("t2", "PDV-1", "aa"), chiave_idempotenza("t1", "PDV-2", "aa"),
                chiave_idempotenza("t1", "PDV-1", "bb")}) == 4


def test_chiave_caricamento_cambia_con_la_generazione() -> None:
    assert chiave_caricamento("l", "a.pdf", "i", 1) != chiave_caricamento("l", "a.pdf", "i", 2)
    assert chiave_caricamento("l", "a.pdf", "i", 1) == chiave_caricamento("l", "a.pdf", "i", 1)


def test_chiave_caricamento_cambia_con_il_contenuto() -> None:
    """R03 i3: un indice diverso (file corretto, impronta dichiarata diversa) dà una
    chiave diversa anche con la stessa lavorazione e la stessa generazione."""
    assert chiave_caricamento("l", "a.pdf", "i1", 1) != chiave_caricamento("l", "a.pdf", "i2", 1)


def test_ritardo_tentativo_lista_poi_esauriti() -> None:
    ritardi = [60, 300]
    assert ritardo_tentativo(1, ritardi) == 60
    assert ritardo_tentativo(2, ritardi) == 300
    assert ritardo_tentativo(3, ritardi) is None
    assert ritardo_tentativo(0, ritardi) is None


def test_problema_ritentabile_solo_se_dichiarato() -> None:
    assert problema_ritentabile({"ritentabile": True})
    assert not problema_ritentabile({"ritentabile": "true"})
    assert not problema_ritentabile({})


def test_costruisci_esito_link_assoluti_e_campi() -> None:
    adesso = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    e = costruisci_esito(cartella="PDV-1", stato=StatoEsito.VERSATO, lavorazione_id="L",
                         adesso=adesso, tentativi=1, pdv_id="P", stato_pdv="RICEVUTO",
                         link_stato="/api/v1/versamento/P", chiave="k",
                         url_pubblico_api="https://api.x/")
    assert e["versione"] == "1" and e["stato"] == "VERSATO"
    assert e["link"] == {"stato": "https://api.x/api/v1/versamento/P",
                         "rapporto": "https://api.x/api/v1/versamento/P/rdv"}
    assert e["elaborato_il"] == "2026-10-21T10:00:00+00:00"
    vuoto = costruisci_esito(cartella="X", stato=StatoEsito.SCARTATO, lavorazione_id="L",
                             adesso=adesso, tentativi=2, errori=[{"codice": "SFTP-103"}])
    assert vuoto["link"] == {"stato": None, "rapporto": None}
    assert vuoto["errori"] == [{"codice": "SFTP-103"}]


@pytest.mark.parametrize("tipo,bit,ammessa", [
    ("ssh-ed25519", None, True), ("ecdsa-sha2-nistp256", None, True),
    ("ssh-rsa", 3072, True), ("ssh-rsa", 4096, True), ("ssh-rsa", 2048, False),
    ("ssh-rsa", None, False), ("ssh-dss", 1024, False),
])
def test_motivo_chiave_non_ammessa_politica(tipo, bit, ammessa) -> None:
    motivo = motivo_chiave_non_ammessa(
        tipo, bit, tipi_ammessi={"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"},
        rsa_bit_minimi=3072)
    assert (motivo is None) is ammessa
