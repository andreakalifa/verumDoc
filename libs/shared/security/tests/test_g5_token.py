# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza (g5-sicurezza) — libreria JWT condivisa.

B2: il dettaglio per il client è generico; il motivo tecnico resta in `motivo`.
M2: token di servizio con audience dedicata, senza tenant_id/ruolo: non vale
    come token utente; durata limitata; emissione da riga di comando.
"""
from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from libs.shared.security.jwt_rs256 import (
    DETTAGLIO_TOKEN_NON_VALIDO,
    PUBBLICO_SERVIZIO_FIRMA,
    SCOPE_FIRMA_SERVIZIO,
    ErroreTokenNonValido,
    firma_token,
    firma_token_servizio,
    verifica_token,
    verifica_token_servizio,
)

PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIV_PEM = PRIV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
PUB_PEM = PRIV.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode()
ALTRA = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode()


def _utente(**kw) -> str:
    base = {"chiave_privata_pem": PRIV_PEM, "emittente": "verumdoc.it", "pubblico": "verumdoc-api",
                "durata_secondi": 60}
    base.update(kw)
    return firma_token({"tenant_id": "t", "ruolo": "VIEWER"}, **base)


@pytest.mark.parametrize("token,parola", [
    (lambda: _utente(pubblico="altro"), "audience"),
    (lambda: _utente(emittente="altro"), "issuer"),
    (lambda: _utente(chiave_privata_pem=ALTRA), "signature"),
    (lambda: firma_token({"ruolo": "VIEWER"}, chiave_privata_pem=PRIV_PEM,
                         emittente="verumdoc.it", pubblico="verumdoc-api", durata_secondi=60),
     "tenant_id"),
])
def test_dettaglio_generico_motivo_nei_log(token, parola: str) -> None:
    with pytest.raises(ErroreTokenNonValido) as exc:
        verifica_token(token(), chiave_pubblica_pem=PUB_PEM, emittente="verumdoc.it",
                       pubblico="verumdoc-api")
    assert exc.value.dettaglio == DETTAGLIO_TOKEN_NON_VALIDO
    assert parola not in exc.value.dettaglio.lower()
    assert parola in exc.value.motivo.lower()


def _servizio(**kw) -> str:
    base = {"chiave_privata_pem": PRIV_PEM, "emittente": "verumdoc.it", "durata_secondi": 60}
    base.update(kw)
    return firma_token_servizio("hashchain", **base)


def test_token_di_servizio_verificato_con_scope() -> None:
    payload = verifica_token_servizio(_servizio(), chiave_pubblica_pem=PUB_PEM,
                                      emittente="verumdoc.it")
    assert payload["aud"] == PUBBLICO_SERVIZIO_FIRMA
    assert payload["scopes"] == [SCOPE_FIRMA_SERVIZIO] and payload["servizio"] == "hashchain"
    assert "tenant_id" not in payload and "ruolo" not in payload


def test_token_di_servizio_non_vale_come_token_utente() -> None:
    with pytest.raises(ErroreTokenNonValido):
        verifica_token(_servizio(), chiave_pubblica_pem=PUB_PEM, emittente="verumdoc.it",
                       pubblico="verumdoc-api")
    # anche se firmato con l'audience utente, senza tenant_id e ruolo è rifiutato
    with pytest.raises(ErroreTokenNonValido):
        verifica_token(_servizio(pubblico="verumdoc-api"), chiave_pubblica_pem=PUB_PEM,
                       emittente="verumdoc.it", pubblico="verumdoc-api")


def test_token_utente_non_vale_come_token_di_servizio() -> None:
    with pytest.raises(ErroreTokenNonValido):
        verifica_token_servizio(_utente(), chiave_pubblica_pem=PUB_PEM, emittente="verumdoc.it")
    falso = firma_token({"servizio": "x", "scopes": [SCOPE_FIRMA_SERVIZIO]},
                        chiave_privata_pem=ALTRA, emittente="verumdoc.it",
                        pubblico=PUBBLICO_SERVIZIO_FIRMA, durata_secondi=60)
    with pytest.raises(ErroreTokenNonValido):
        verifica_token_servizio(falso, chiave_pubblica_pem=PUB_PEM, emittente="verumdoc.it")


@pytest.mark.parametrize("durata", [0, -1, 91 * 86400])
def test_durata_del_token_di_servizio_limitata(durata: int) -> None:
    with pytest.raises(ValueError):
        _servizio(durata_secondi=durata)


def test_emissione_da_riga_di_comando(monkeypatch, capsys) -> None:
    from libs.shared.security.emetti_token_servizio import main

    monkeypatch.setenv("JWT_PRIVATE_KEY", PRIV_PEM)
    monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
    assert main(["--servizio", "hashchain", "--giorni", "30"]) == 0
    token = capsys.readouterr().out.strip()
    payload = verifica_token_servizio(token, chiave_pubblica_pem=PUB_PEM, emittente="verumdoc.it")
    assert 29 * 86400 < payload["exp"] - int(time.time()) <= 30 * 86400
    assert main(["--servizio", "hashchain", "--giorni", "120"]) == 2


def test_emissione_con_scope_per_la_lettura_del_registro(monkeypatch, capsys) -> None:
    """R04: token del testimone/verificatore per GET /registro/foglie (aud
    verumdoc-registro, scope registro:leggi); senza --scope resta firma:servizio."""
    from libs.shared.security.emetti_token_servizio import main
    from libs.shared.security.jwt_rs256 import SCOPE_FIRMA_SERVIZIO

    monkeypatch.setenv("JWT_PRIVATE_KEY", PRIV_PEM)
    monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
    assert main(["--servizio", "testimone", "--pubblico", "verumdoc-registro",
                 "--scope", "registro:leggi", "--scope", "registro:altro"]) == 0
    token = capsys.readouterr().out.strip()
    payload = verifica_token_servizio(token, chiave_pubblica_pem=PUB_PEM,
                                      emittente="verumdoc.it", pubblico="verumdoc-registro")
    assert payload["scopes"] == ["registro:leggi", "registro:altro"]
    assert payload["servizio"] == "testimone"
    assert main(["--servizio", "hashchain"]) == 0
    predefinito = verifica_token_servizio(capsys.readouterr().out.strip(),
                                          chiave_pubblica_pem=PUB_PEM, emittente="verumdoc.it")
    assert predefinito["scopes"] == [SCOPE_FIRMA_SERVIZIO]
    assert main(["--servizio", "x", "--scope", "con spazio"]) == 2
    assert main(["--servizio", "sftp-ingest", "--canale", "SFTP", "--scope", "a"]) == 2
