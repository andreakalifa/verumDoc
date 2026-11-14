# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""VD-C-085 — rotazione delle chiavi JWT con ``kid`` e più chiavi pubbliche valide insieme.

Si eseguono firma e verifica vere (PyJWT, RSA 2048): nessun test sui sorgenti.
"""
from __future__ import annotations

import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from libs.shared.security.jwt_rs256 import (
    ErroreChiaviJwt,
    ErroreTokenNonValido,
    blocchi_pem,
    carica_chiavi_jwt,
    firma_token,
    jwks,
    kid_di,
    verifica_token,
)

ISS, AUD = "verumdoc.it", "verumdoc-api"


def _coppia() -> tuple[str, str, rsa.RSAPrivateKey]:
    privata = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = privata.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode()
    pub_pem = privata.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return pub_pem, priv_pem, privata


VECCHIA = _coppia()
NUOVA = _coppia()


def _token(priv_pem: str) -> str:
    return firma_token({"tenant_id": "t", "ruolo": "VIEWER"}, chiave_privata_pem=priv_pem,
                       emittente=ISS, pubblico=AUD, durata_secondi=60)


def _verifica(token: str, mazzo: str) -> dict:
    return verifica_token(token, chiave_pubblica_pem=mazzo, emittente=ISS, pubblico=AUD)


def test_kid_rfc7638_vettore_della_norma() -> None:
    """Oracolo esterno: esempio di RFC 7638 §3.1 (chiave RSA → impronta JWK)."""
    n_b64 = ("0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6"
             "tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v"
             "-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08"
             "qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8"
             "awapJzKnqDKgw")
    n = int.from_bytes(base64.urlsafe_b64decode(n_b64 + "=" * (-len(n_b64) % 4)), "big")
    chiave = rsa.RSAPublicNumbers(e=65537, n=n).public_key()
    assert kid_di(chiave) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


def test_firma_token_mette_il_kid_della_chiave_privata() -> None:
    intestazione = jwt.get_unverified_header(_token(NUOVA[1]))
    assert intestazione["kid"] == kid_di(NUOVA[2].public_key())
    assert intestazione["alg"] == "RS256"


def test_mazzo_due_chiavi_token_vecchi_e_nuovi_validi() -> None:
    """Durante la rotazione i token firmati con la vecchia e con la nuova sono validi."""
    mazzo = VECCHIA[0] + NUOVA[0]
    assert _verifica(_token(VECCHIA[1]), mazzo)["tenant_id"] == "t"
    assert _verifica(_token(NUOVA[1]), mazzo)["tenant_id"] == "t"


def test_token_senza_kid_emesso_prima_di_r04_verificato_col_mazzo() -> None:
    ora = int(time.time())
    vecchio = jwt.encode({"tenant_id": "t", "ruolo": "VIEWER", "iat": ora, "exp": ora + 60,
                          "iss": ISS, "aud": AUD}, VECCHIA[1], algorithm="RS256")
    assert "kid" not in jwt.get_unverified_header(vecchio)
    assert _verifica(vecchio, NUOVA[0] + VECCHIA[0])["ruolo"] == "VIEWER"


def test_chiave_tolta_dal_mazzo_token_rifiutato_auth_001() -> None:
    """Fine rotazione: tolta la vecchia, i suoi token non valgono più."""
    token = _token(VECCHIA[1])
    with pytest.raises(ErroreTokenNonValido) as exc:
        _verifica(token, NUOVA[0])
    assert exc.value.codice == "AUTH-001"
    assert "kid sconosciuto" in exc.value.motivo


def test_kid_falsificato_non_sceglie_un_altra_chiave() -> None:
    """Un token firmato con una chiave estranea ma col kid di una chiave valida: firma errata."""
    estranea = _coppia()
    ora = int(time.time())
    falso = jwt.encode({"tenant_id": "t", "ruolo": "VIEWER", "iat": ora, "exp": ora + 60,
                        "iss": ISS, "aud": AUD}, estranea[1], algorithm="RS256",
                       headers={"kid": kid_di(NUOVA[2].public_key())})
    with pytest.raises(ErroreTokenNonValido) as exc:
        _verifica(falso, VECCHIA[0] + NUOVA[0])
    assert exc.value.codice == "AUTH-001"


def test_token_scaduto_nel_mazzo_resta_auth_002() -> None:
    token = firma_token({"tenant_id": "t", "ruolo": "VIEWER"}, chiave_privata_pem=VECCHIA[1],
                        emittente=ISS, pubblico=AUD, durata_secondi=1,
                        adesso=int(time.time()) - 3600)
    with pytest.raises(ErroreTokenNonValido) as exc:
        _verifica(token, VECCHIA[0] + NUOVA[0])
    assert exc.value.codice == "AUTH-002"


def test_carica_chiavi_mazzo_con_privata_nuova() -> None:
    chiavi = carica_chiavi_jwt(env="production", servizio="prova",
                               chiave_pubblica=VECCHIA[0] + NUOVA[0],
                               chiave_privata=NUOVA[1], richiede_privata=True)
    assert chiavi.kid_firma == kid_di(NUOVA[2].public_key())
    assert set(chiavi.kid_validi) == {kid_di(VECCHIA[2].public_key()),
                                      kid_di(NUOVA[2].public_key())}
    assert len(blocchi_pem(chiavi.pubblica_pem)) == 2


def test_carica_chiavi_mazzo_con_a_capo_letterali() -> None:
    """Formato .env: tutto su una riga con «\\n» letterali."""
    una_riga = (VECCHIA[0] + NUOVA[0]).replace("\n", "\\n")
    chiavi = carica_chiavi_jwt(env="production", servizio="prova", chiave_pubblica=una_riga,
                               chiave_privata="", richiede_privata=False)
    assert len(chiavi.kid_validi) == 2


def test_carica_chiavi_privata_fuori_dal_mazzo_rifiutata() -> None:
    with pytest.raises(ErroreChiaviJwt, match="non sono una coppia"):
        carica_chiavi_jwt(env="production", servizio="prova", chiave_pubblica=VECCHIA[0],
                          chiave_privata=NUOVA[1], richiede_privata=True)


def test_carica_chiavi_mazzo_con_chiave_illeggibile_rifiutato() -> None:
    rotto = "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n"
    with pytest.raises(ErroreChiaviJwt, match="non è una chiave pubblica"):
        carica_chiavi_jwt(env="production", servizio="prova", chiave_pubblica=NUOVA[0] + rotto,
                          chiave_privata="", richiede_privata=False)


def test_jwks_elenca_le_chiavi_pubbliche_con_kid() -> None:
    insieme = jwks(VECCHIA[0] + NUOVA[0])
    kid = [k["kid"] for k in insieme["keys"]]
    assert kid == [kid_di(VECCHIA[2].public_key()), kid_di(NUOVA[2].public_key())]
    assert all(k["kty"] == "RSA" and k["alg"] == "RS256" and k["use"] == "sig" and "d" not in k
               for k in insieme["keys"])
