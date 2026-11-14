# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Cifratura a riposo dei segreti dei webhook (AES-256-GCM) e controlli d'avvio —
contratto C3 (agente R03 b3), REGOLE §19.3, §19.4.
"""

from __future__ import annotations

import base64
import json
import os

import pytest
from app.application.configurazione import problemi_avvio, stato_webhook
from app.config import Settings
from app.infrastructure.cifratura import Cifratore, ErroreCifratura, contesto_segreto

K1 = base64.b64encode(os.urandom(32)).decode()
K0 = base64.b64encode(os.urandom(32)).decode()


def test_cifra_decifra_e_nessun_chiaro_nel_salvato() -> None:
    c = Cifratore.da_impostazioni(K1, "k1")
    salvato = c.cifra("whsec_segreto", contesto_segreto("t", "s"))
    assert salvato.startswith("aesgcm$k1$") and "whsec_segreto" not in salvato
    assert c.decifra(salvato, contesto_segreto("t", "s")) == "whsec_segreto"
    assert c.cifra("whsec_segreto", "t:s") != salvato  # nonce casuale
    assert K1 not in repr(c)


def test_decifra_con_contesto_diverso_rifiutato() -> None:
    """Un segreto copiato su un'altra riga o un altro ente non si decifra (AAD)."""
    c = Cifratore.da_impostazioni(K1, "k1")
    salvato = c.cifra("whsec_x", contesto_segreto("ente-a", "s1"))
    with pytest.raises(ErroreCifratura):
        c.decifra(salvato, contesto_segreto("ente-b", "s1"))
    with pytest.raises(ErroreCifratura):
        c.decifra(salvato.replace("$k1$", "$k1$A"), contesto_segreto("ente-a", "s1"))


def test_rotazione_decifra_con_chiave_precedente_e_cifra_con_la_nuova() -> None:
    vecchio = Cifratore.da_impostazioni(K0, "k0")
    salvato = vecchio.cifra("whsec_v", "t:s")
    nuovo = Cifratore.da_impostazioni(K1, "k1", json.dumps({"k0": K0}))
    assert nuovo.decifra(salvato, "t:s") == "whsec_v"
    assert nuovo.cifra("whsec_v", "t:s").startswith("aesgcm$k1$")
    senza_precedenti = Cifratore.da_impostazioni(K1, "k1")
    with pytest.raises(ErroreCifratura, match="non configurata"):
        senza_precedenti.decifra(salvato, "t:s")


@pytest.mark.parametrize(("chiave", "precedenti"), [
    (base64.b64encode(b"corta").decode(), ""),
    ("non base64 !!", ""),
    (K1, "non json"),
    (K1, json.dumps({"k1": K0})),  # id duplicato
])
def test_chiavi_non_valide_rifiutate(chiave: str, precedenti: str) -> None:
    with pytest.raises(ErroreCifratura):
        Cifratore.da_impostazioni(chiave, "k1", precedenti)


def test_chiave_assente_nessun_cifratore() -> None:
    assert Cifratore.da_impostazioni("", "k1") is None


def _s(**kw) -> Settings:
    return Settings(**kw)


def test_problemi_avvio_produzione_senza_chiave_o_con_indirizzi_privati() -> None:
    """In produzione la chiave mancante e gli indirizzi privati fermano l'avvio (§19.4)."""
    problemi = problemi_avvio(_s(env="production", webhook_consenti_indirizzi_privati=True))
    assert any("WEBHOOK_CHIAVE_CIFRATURA" in p for p in problemi)
    assert any("INDIRIZZI_PRIVATI" in p for p in problemi)
    # R12a (VD-C-152): in produzione serve anche la chiave privata della posta
    assert [p for p in problemi_avvio(_s(env="production", webhook_chiave_cifratura=K1))
            if "POSTA_CHIAVE_PRIVATA" not in p] == []
    from libs.shared.security.sigillo import genera_coppia

    assert problemi_avvio(_s(env="production", webhook_chiave_cifratura=K1,
                             posta_chiave_privata=genera_coppia()[0])) == []


def test_problemi_avvio_sviluppo_senza_chiave_ammesso_ma_stato_onesto() -> None:
    s = _s(env="development")
    assert problemi_avvio(s) == []
    assert stato_webhook(s)["stato"] == "non configurati"
    configurato = stato_webhook(_s(env="development", webhook_chiave_cifratura=K1))
    assert configurato["stato"] == "configurati"


@pytest.mark.parametrize("kw", [
    {"webhook_chiave_cifratura": "corta"},
    {"webhook_ritardi_secondi": [60, 0]},
    {"webhook_lease_secondi": 5, "webhook_timeout_secondi": 10},
    {"eventi_limite_predefinito": 900, "eventi_limite_massimo": 500},
    {"eventi_topic": {"pdv.received": "versamento.inventato"}},
])
def test_problemi_avvio_impostazioni_non_sensate(kw) -> None:
    assert problemi_avvio(_s(env="test", **kw))
