# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prove di libs/catena/nota.py (contratto K2 di R04): checkpoint C2SP in nota
firmata C2SP con Ed25519.

Vettore noto: l'esempio della documentazione di ``golang.org/x/mod/sumdb/note``
(chiave «PeterNeumann», licenza BSD): stessa chiave, stessa firma, stesso key
hash. L'interoperabilità completa con il pacchetto Go è in test_oracolo_go.py.
"""
from __future__ import annotations

import base64
import hashlib

import pytest

from libs.catena import nota
from libs.catena.nota import Checkpoint, ErroreNota

CHIAVE_GO = "PRIVATE+KEY+PeterNeumann+c74f20a3+AYEKFALVFGyNhPJEMzD1QIDr+Y7hfZx09iUvxdXHKDFz"
VERIFICATORE_GO = "PeterNeumann+c74f20a3+ARpc2QcUPDhMQegwxbzhKqiBfsVkmqq/LDE4izWy10TW"
TESTO_GO = ("If you think cryptography is the answer to your problem,\n"
            "then you don't know what your problem is.\n")
FIRMA_GO = ("— PeterNeumann x08go/ZJkuBS9UG/SffcvIAQxVBtiFupLLr8pAcElZInNIuGUgYN1FFYC2pZSNX"
            "gKvqfqdngotpRZb6KE6RyyBwJnAM=")
RADICE = hashlib.sha256(b"radice").digest()


def test_vettore_go_firma_identica():
    """Stessa chiave del pacchetto Go → stessa riga di firma (Ed25519 è deterministico)."""
    firmatario = nota.analizza_firmatario(CHIAVE_GO)
    assert firmatario.nome == "PeterNeumann" and firmatario.key_hash == 0xC74F20A3
    assert firmatario.firma(TESTO_GO) == FIRMA_GO
    assert firmatario.verificatore.testo == VERIFICATORE_GO
    assert nota.chiave_firmatario("PeterNeumann", firmatario.chiave_privata) == CHIAVE_GO


def test_vettore_go_verifica():
    messaggio = TESTO_GO + "\n" + FIRMA_GO + "\n"
    assert nota.verifica(messaggio, [VERIFICATORE_GO]) == [
        nota.FirmaValida("PeterNeumann", 0xC74F20A3)]


def test_checkpoint_formatta_e_analizza():
    cp = Checkpoint("verumdoc.it/registro/v1", 12345, RADICE)
    testo = nota.formatta(cp)
    assert testo == ("verumdoc.it/registro/v1\n12345\n"
                     + base64.b64encode(RADICE).decode() + "\n")
    assert nota.analizza(testo) == cp


def test_checkpoint_firmato_verificato_e_analizzato():
    privata, pubblica = nota.genera_chiave("verumdoc-registro")
    firmatario = nota.analizza_firmatario(privata, nome_atteso="verumdoc-registro")
    corpo = nota.formatta(Checkpoint("verumdoc.it/registro/v1/sviluppo", 7, RADICE))
    firmata = nota.componi(corpo, [firmatario.firma(corpo)])
    assert nota.verifica(firmata, [pubblica])[0].nome == "verumdoc-registro"
    assert nota.analizza(firmata).dimensione == 7


def test_controfirma_del_testimone_aggiunta():
    reg_priv, reg_pub = nota.genera_chiave("verumdoc-registro")
    tes_priv, tes_pub = nota.genera_chiave("verumdoc-testimone")
    corpo = nota.formatta(Checkpoint("verumdoc.it/registro/v1", 3, RADICE))
    firmata = nota.componi(corpo, [nota.analizza_firmatario(reg_priv).firma(corpo)])
    controfirma = nota.analizza_firmatario(tes_priv).firma(corpo)
    completa = nota.aggiungi_firme(firmata, [controfirma, controfirma])
    assert completa.count("— ") == 2
    assert {f.nome for f in nota.verifica(completa, [reg_pub, tes_pub])} == \
        {"verumdoc-registro", "verumdoc-testimone"}
    # Solo i verificatori indicati contano
    assert [f.nome for f in nota.verifica(completa, [tes_pub])] == ["verumdoc-testimone"]


def test_corpo_alterato_firma_non_valida():
    priv, pub = nota.genera_chiave("verumdoc-registro")
    corpo = nota.formatta(Checkpoint("verumdoc.it/registro/v1", 3, RADICE))
    firmata = nota.componi(corpo, [nota.analizza_firmatario(priv).firma(corpo)])
    alterata = firmata.replace("\n3\n", "\n4\n", 1)
    assert nota.verifica(alterata, [pub]) == []


def test_chiave_diversa_stesso_nome_ignorata():
    priv, _ = nota.genera_chiave("verumdoc-registro")
    _, altra_pub = nota.genera_chiave("verumdoc-registro")
    corpo = nota.formatta(Checkpoint("o", 1, RADICE))
    firmata = nota.componi(corpo, [nota.analizza_firmatario(priv).firma(corpo)])
    assert nota.verifica(firmata, [altra_pub]) == []


def test_firma_falsificata_con_key_hash_giusto_rifiutata():
    priv, pub = nota.genera_chiave("verumdoc-registro")
    firmatario = nota.analizza_firmatario(priv)
    corpo = nota.formatta(Checkpoint("o", 1, RADICE))
    riga = nota.analizza_riga_firma(firmatario.firma(corpo))
    falsa = base64.b64encode(riga.key_hash.to_bytes(4, "big") + b"\x00" * 64).decode()
    firmata = nota.componi(corpo, [f"— verumdoc-registro {falsa}"])
    assert nota.verifica(firmata, [pub]) == []


@pytest.mark.parametrize("testo", [
    "o\n1\n",                                  # manca la radice
    "o\n01\n" + base64.b64encode(RADICE).decode() + "\n",   # dimensione non canonica
    "o\n-1\n" + base64.b64encode(RADICE).decode() + "\n",
    "o\n1\nnon-base64!\n",
    "o\n1\n" + base64.b64encode(b"corta").decode() + "\n",
    "o\n1\n" + base64.b64encode(RADICE).decode(),           # senza a capo finale
])
def test_checkpoint_malformato_rifiutato(testo):
    with pytest.raises(ErroreNota):
        nota.analizza(testo)


@pytest.mark.parametrize("nota_testo", [
    "corpo\n",                                     # senza firme
    "corpo\n\n— nome\n",                           # riga di firma incompleta
    "corpo\n\n- nome AAAA\n",                      # trattino sbagliato
    "corpo\n\n— nome AAAAAAAA",                    # senza a capo finale
    "corpo\x01\n\n— nome AAAAAAAA\n",              # carattere di controllo
])
def test_nota_malformata_rifiutata(nota_testo):
    with pytest.raises(ErroreNota):
        nota.verifica(nota_testo, [])


@pytest.mark.parametrize("nome", ["", "con spazio", "con+piu", "a\tb"])
def test_nome_chiave_non_valido(nome):
    with pytest.raises(ErroreNota):
        nota.genera_chiave(nome)


def test_verificatore_incoerente_rifiutato():
    _, pub = nota.genera_chiave("verumdoc-registro")
    nome, h, chiave = pub.split("+", 2)
    with pytest.raises(ErroreNota):
        nota.analizza_verificatore(f"{nome}+{int(h, 16) ^ 1:08x}+{chiave}")
    with pytest.raises(ErroreNota):
        nota.analizza_verificatore(f"altro+{h}+{chiave}")
    with pytest.raises(ErroreNota):
        nota.analizza_verificatore("solo-nome")


def test_firmatario_nome_atteso_e_messaggi_senza_chiave():
    priv, _ = nota.genera_chiave("verumdoc-testimone")
    with pytest.raises(ErroreNota) as errore:
        nota.analizza_firmatario(priv, nome_atteso="verumdoc-registro")
    assert priv.split("+", 4)[-1] not in str(errore.value)
    assert priv.split("+", 4)[-1] not in repr(nota.analizza_firmatario(priv))
    with pytest.raises(ErroreNota):
        nota.analizza_firmatario("non-una-chiave")


def test_firma_testo_senza_a_capo_rifiutata():
    priv, _ = nota.genera_chiave("k")
    with pytest.raises(ErroreNota):
        nota.analizza_firmatario(priv).firma("senza a capo")
