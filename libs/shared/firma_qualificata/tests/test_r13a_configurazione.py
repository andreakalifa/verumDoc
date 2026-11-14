# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R13a — configurazione dei fornitori di firma e marca (libs/shared/firma_qualificata),
parametri del gruppo «firma_qualificata» e attributi CAdES ETSI per asn1crypto."""
from __future__ import annotations

import pytest

from libs.shared.firma_qualificata.configurazione import (
    SEGRETI_PER_TIPO,
    ConfigurazioneFornitoreNonValida,
    FornitoreRegistrato,
    impronta_configurazione,
    leggi_segreti,
    segreti_mancanti,
    segreti_presenti,
    valida_configurazione,
)

CSC = {"url_base": "https://rssp.esempio.it/csc/v2/", "url_token": "https://rssp.esempio.it/t",
       "client_id": "vd"}


def test_csc_normalizzato_con_predefiniti() -> None:
    conf = valida_configurazione("FIRMA", "CSC", CSC, produzione=False)
    assert conf["url_base"] == "https://rssp.esempio.it/csc/v2"
    assert conf["timeout_secondi"] == 30 and conf["ritentativi"] == 2
    assert conf["modo_autorizzazione"] == "auto" and conf["pin_spki_sha256"] == []


@pytest.mark.parametrize("modifica, motivo", [
    ({"client_id": ""}, "client_id"),
    ({"modo_autorizzazione": "oauth2code"}, "oauth2code"),
    ({"pin_spki_sha256": ["non-esadecimale"]}, "SHA-256"),
    ({"timeout_secondi": 0}, "timeout_secondi"),
    ({"sign_algo_oid": "1.2.x"}, "OID"),
    ({"radici_pem": "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"}, "radici"),
    ({"otp_statico": "1"}, "segreti"),
    ({"pin": "1"}, "segreti"),
])
def test_csc_rifiuti(modifica, motivo) -> None:
    with pytest.raises(ConfigurazioneFornitoreNonValida, match=motivo):
        valida_configurazione("FIRMA", "CSC", {**CSC, **modifica}, produzione=False)


def test_tipi_e_ruoli() -> None:
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="non ammesso"):
        valida_configurazione("MARCA", "CSC", CSC, produzione=False)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="ruolo"):
        valida_configurazione("ALTRO", "CSC", CSC, produzione=False)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="assoluto"):
        valida_configurazione("FIRMA", "PKCS11", {"libreria": "lib.so", "token_label": "t",
                                                  "chiave_label": "k"}, produzione=False)
    tsa = valida_configurazione("MARCA", "RFC3161", {"url": "http://tsa.esempio.it"},
                                produzione=False)
    assert tsa["tolleranza_secondi"] == 300  # http ammesso per la TSA (token firmato)


def test_segreti() -> None:
    testo = '{"csc-a": {"client_secret": "s", "pin": ""}, "altro": {"pin": "1"}}'
    segreti = leggi_segreti(testo, "csc-a")
    assert segreti == {"client_secret": "s"}
    assert segreti_presenti("CSC", segreti)["pin"] is False
    assert segreti_mancanti("CSC", {}) == ["client_secret"]
    assert segreti_mancanti("PKCS11", {}) == ["pin"]
    assert leggi_segreti("", "x") == {}
    with pytest.raises(ValueError) as errore:
        leggi_segreti('{"rotto": ', "x")
    assert "rotto" not in str(errore.value)
    assert all(not obbl for _, obbl in SEGRETI_PER_TIPO["RFC3161"])


def test_impronta_cambia_con_la_configurazione() -> None:
    a = FornitoreRegistrato("a", "FIRMA", "SVILUPPO", "a", 1, {})
    b = FornitoreRegistrato("a", "FIRMA", "SVILUPPO", "a", 2, {})
    assert impronta_configurazione([a]) != impronta_configurazione([b])
    assert impronta_configurazione([a, b]) == impronta_configurazione([b, a])


def test_parametri_firma_qualificata_e_vincolo_dell_algoritmo() -> None:
    from libs.verumdoc_rules.impostazioni import Livello
    from libs.verumdoc_rules.parametri_servizio import (
        IMPOSTAZIONI_SERVIZIO,
        VINCOLI_CONGIUNTI,
    )

    voci = {v.codice: v for v in IMPOSTAZIONI_SERVIZIO if v.gruppo == "firma_qualificata"}
    assert set(voci) == {"FIRMA_SENZA_FORNITORE", "FIRMA_LIVELLO_DOCUMENTI",
                         "FIRMA_ALGORITMO_IMPRONTA", "FIRMA_ALGORITMI_AMMESSI",
                         "FIRMA_RINNOVO_PREAVVISO_GIORNI", "CATENA_MARCA_INTERVALLO_MINUTI",
                         "ATTESTAZIONE_SIGILLO", "FIRMA_TENTATIVI_MASSIMI"}
    for v in voci.values():
        assert v.riservata_conservatore and v.livelli_ammessi() == {Livello.CONSERVATORE}
        assert v.verso_sicuro is not None
    # comportamento di prima come predefinito (VD-P-26): non firmato, nessun blocco
    assert voci["FIRMA_SENZA_FORNITORE"].predefinito == "NON_FIRMATO"
    assert voci["FIRMA_SENZA_FORNITORE"].scelte[0] == "BLOCCA"  # verso sicuro
    vincolo = next(v for v in VINCOLI_CONGIUNTI if "FIRMA_ALGORITMO_IMPRONTA" in v.codici)
    assert vincolo.ammesso({"FIRMA_ALGORITMO_IMPRONTA": "sha256",
                            "FIRMA_ALGORITMI_AMMESSI": ("sha256",)})
    assert not vincolo.ammesso({"FIRMA_ALGORITMO_IMPRONTA": "sha256",
                                "FIRMA_ALGORITMI_AMMESSI": ("sha512",)})


def test_attributi_etsi_leggibili() -> None:
    from asn1crypto import cms

    from libs.shared.firme.asn1_etsi import indice_v3, leggi_indice_v3

    for algoritmo in ("sha256", "sha512"):
        indice = indice_v3(algoritmo, [b"\x01" * 32], [], [b"\x02" * 32, b"\x03" * 32])
        assert leggi_indice_v3(type(indice).load(indice.dump())) == (
            algoritmo, [b"\x01" * 32], [], [b"\x02" * 32, b"\x03" * 32])
    attr = cms.CMSAttribute({"type": "0.4.0.19122.1.5",
                             "values": [indice_v3("sha256", [], [], [])]})
    assert cms.CMSAttribute.load(attr.dump()).native["type"] == "ats_hash_index_v3"


def test_permesso_mai_delegabile() -> None:
    from libs.shared.security.permessi import ha_permesso
    from libs.verumdoc_rules.permessi import (
        CONCEDIBILI_POSSIBILI,
        FIRMA_FORNITORI_GESTISCI,
        MAI_DELEGABILI,
    )

    assert FIRMA_FORNITORI_GESTISCI in MAI_DELEGABILI
    assert FIRMA_FORNITORI_GESTISCI not in CONCEDIBILI_POSSIBILI
    assert ha_permesso({"ruolo": "RSC_CONSERVATORE"}, FIRMA_FORNITORI_GESTISCI)
    assert not ha_permesso({"ruolo": "ADMIN_TENANT"}, FIRMA_FORNITORI_GESTISCI)
    assert not ha_permesso({"ruolo": "OPERATORE", "rp": "r1",
                            "permessi": [FIRMA_FORNITORI_GESTISCI]}, FIRMA_FORNITORI_GESTISCI)
