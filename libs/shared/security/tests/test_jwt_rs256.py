# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test di libs/shared/security/jwt_rs256.py (VD-C-001, VD-C-004).

Ogni test genera le proprie chiavi in memoria: nessun file, nessuna rete.
"""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from libs.shared.security.jwt_rs256 import (
    ErroreChiaviJwt,
    ErroreTokenNonValido,
    carica_chiavi_jwt,
    firma_token,
    normalizza_pem,
    verifica_token,
)

ISS = "verumdoc.it"
AUD = "verumdoc-api"


def _coppia(bits: int = 2048) -> tuple[str, str]:
    k = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    priv = k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = k.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return pub, priv


@pytest.fixture(scope="module")
def coppia() -> tuple[str, str]:
    return _coppia()


@pytest.fixture(scope="module")
def altra_coppia() -> tuple[str, str]:
    return _coppia()


def _token(priv: str, **extra: object) -> str:
    claims = {"tenant_id": "t-a", "ruolo": "ADMIN_TENANT", "user_id": "u-1"}
    claims.update(extra)
    return firma_token(
        claims, chiave_privata_pem=priv, emittente=ISS, pubblico=AUD, durata_secondi=300
    )


class TestCaricaChiavi:
    @pytest.mark.parametrize("env", ["production", "staging", "development", "prod", ""])
    def test_senza_chiavi_fuori_test_rifiuta_di_partire(self, env: str) -> None:
        with pytest.raises(ErroreChiaviJwt, match="JWT_PUBLIC_KEY mancante"):
            carica_chiavi_jwt(
                env=env, servizio="x", chiave_pubblica="", chiave_privata="",
                richiede_privata=False,
            )

    def test_firmatario_senza_privata_rifiuta_di_partire(self, coppia: tuple[str, str]) -> None:
        pub, _ = coppia
        with pytest.raises(ErroreChiaviJwt, match="JWT_PRIVATE_KEY mancante"):
            carica_chiavi_jwt(
                env="staging", servizio="gateway", chiave_pubblica=pub,
                chiave_privata="", richiede_privata=True,
            )

    def test_firmatario_senza_privata_anche_in_test_con_pubblica(
        self, coppia: tuple[str, str]
    ) -> None:
        pub, _ = coppia
        with pytest.raises(ErroreChiaviJwt):
            carica_chiavi_jwt(
                env="test", servizio="gateway", chiave_pubblica=pub,
                chiave_privata=None, richiede_privata=True,
            )

    def test_in_test_senza_chiavi_coppia_effimera_utilizzabile(self) -> None:
        ch = carica_chiavi_jwt(
            env="TEST", servizio="x", chiave_pubblica="", chiave_privata="",
            richiede_privata=True,
        )
        assert ch.effimere is True
        assert ch.privata_pem
        tok = _token(ch.privata_pem)
        assert verifica_token(
            tok, chiave_pubblica_pem=ch.pubblica_pem, emittente=ISS, pubblico=AUD
        )["tenant_id"] == "t-a"

    def test_pem_con_a_capo_letterali(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        ch = carica_chiavi_jwt(
            env="production", servizio="x",
            chiave_pubblica=pub.replace("\n", "\\n"),
            chiave_privata=priv.replace("\n", "\\n"),
            richiede_privata=True,
        )
        assert ch.effimere is False
        assert ch.pubblica_pem == normalizza_pem(pub)

    def test_pubblica_ricavata_dalla_privata(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        ch = carica_chiavi_jwt(
            env="production", servizio="x", chiave_pubblica="", chiave_privata=priv,
            richiede_privata=True,
        )
        assert ch.pubblica_pem.strip() == pub.strip()

    def test_coppia_non_corrispondente(
        self, coppia: tuple[str, str], altra_coppia: tuple[str, str]
    ) -> None:
        with pytest.raises(ErroreChiaviJwt, match="non sono una coppia"):
            carica_chiavi_jwt(
                env="production", servizio="x", chiave_pubblica=altra_coppia[0],
                chiave_privata=coppia[1], richiede_privata=True,
            )

    def test_pubblica_illeggibile(self) -> None:
        with pytest.raises(ErroreChiaviJwt, match="non è una chiave pubblica"):
            carica_chiavi_jwt(
                env="production", servizio="x", chiave_pubblica="non-un-pem",
                chiave_privata="", richiede_privata=False,
            )

    def test_chiave_corta_rifiutata(self) -> None:
        pub, _ = _coppia(1024)
        with pytest.raises(ErroreChiaviJwt, match="troppo corta"):
            carica_chiavi_jwt(
                env="production", servizio="x", chiave_pubblica=pub,
                chiave_privata="", richiede_privata=False,
            )

    def test_repr_non_espone_la_privata(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        ch = carica_chiavi_jwt(
            env="production", servizio="x", chiave_pubblica=pub, chiave_privata=priv,
            richiede_privata=True,
        )
        assert "PRIVATE" not in repr(ch)


class TestVerificaToken:
    def test_token_valido(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        p = verifica_token(_token(priv), chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)
        assert p["ruolo"] == "ADMIN_TENANT"
        assert p["iss"] == ISS and p["aud"] == AUD and p["jti"]

    def test_firma_di_altra_chiave(
        self, coppia: tuple[str, str], altra_coppia: tuple[str, str]
    ) -> None:
        with pytest.raises(ErroreTokenNonValido) as e:
            verifica_token(
                _token(altra_coppia[1]), chiave_pubblica_pem=coppia[0],
                emittente=ISS, pubblico=AUD,
            )
        assert e.value.codice == "AUTH-001"

    def test_token_non_firmato_alg_none(self, coppia: tuple[str, str]) -> None:
        falso = jwt.encode(
            {"tenant_id": "t", "ruolo": "SUPER_ADMIN", "iss": ISS, "aud": AUD,
             "iat": int(time.time()), "exp": int(time.time()) + 60},
            key=None, algorithm="none",
        )
        with pytest.raises(ErroreTokenNonValido):
            verifica_token(falso, chiave_pubblica_pem=coppia[0], emittente=ISS, pubblico=AUD)

    def test_token_hs256_con_chiave_pubblica_come_segreto(
        self, coppia: tuple[str, str]
    ) -> None:
        """Attacco di confusione d'algoritmo: rifiutato perché si accetta solo RS256."""
        import base64
        import hashlib
        import hmac
        import json

        def b64(d: bytes) -> str:
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        p = b64(json.dumps({"tenant_id": "t", "ruolo": "SUPER_ADMIN", "iss": ISS,
                            "aud": AUD, "iat": int(time.time()),
                            "exp": int(time.time()) + 60}).encode())
        firma = hmac.new(coppia[0].encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        with pytest.raises(ErroreTokenNonValido):
            verifica_token(f"{h}.{p}.{b64(firma)}", chiave_pubblica_pem=coppia[0],
                           emittente=ISS, pubblico=AUD)

    def test_scaduto(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        tok = firma_token(
            {"tenant_id": "t", "ruolo": "VIEWER"}, chiave_privata_pem=priv, emittente=ISS,
            pubblico=AUD, durata_secondi=60, adesso=int(time.time()) - 3600,
        )
        with pytest.raises(ErroreTokenNonValido) as e:
            verifica_token(tok, chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)
        assert e.value.codice == "AUTH-002"

    def test_emittente_errato(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        tok = firma_token({"tenant_id": "t", "ruolo": "VIEWER"}, chiave_privata_pem=priv,
                          emittente="altro", pubblico=AUD, durata_secondi=60)
        with pytest.raises(ErroreTokenNonValido):
            verifica_token(tok, chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)

    def test_pubblico_errato(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        tok = firma_token({"tenant_id": "t", "ruolo": "VIEWER"}, chiave_privata_pem=priv,
                          emittente=ISS, pubblico="altro-servizio", durata_secondi=60)
        with pytest.raises(ErroreTokenNonValido):
            verifica_token(tok, chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)

    def test_senza_aud_rifiutato(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        ora = int(time.time())
        tok = jwt.encode({"tenant_id": "t", "ruolo": "VIEWER", "iss": ISS, "iat": ora,
                          "exp": ora + 60}, priv, algorithm="RS256")
        with pytest.raises(ErroreTokenNonValido):
            verifica_token(tok, chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)

    def test_senza_exp_rifiutato(self, coppia: tuple[str, str]) -> None:
        pub, priv = coppia
        tok = jwt.encode({"tenant_id": "t", "ruolo": "VIEWER", "iss": ISS, "aud": AUD,
                          "iat": int(time.time())}, priv, algorithm="RS256")
        with pytest.raises(ErroreTokenNonValido):
            verifica_token(tok, chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)

    @pytest.mark.parametrize("mancante", ["tenant_id", "ruolo"])
    def test_claim_applicativo_mancante_o_vuoto(
        self, coppia: tuple[str, str], mancante: str
    ) -> None:
        pub, priv = coppia
        claims = {"tenant_id": "t", "ruolo": "VIEWER"}
        claims[mancante] = ""
        tok = firma_token(claims, chiave_privata_pem=priv, emittente=ISS, pubblico=AUD,
                          durata_secondi=60)
        with pytest.raises(ErroreTokenNonValido, match="AUTH-001"):
            verifica_token(tok, chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)

    def test_token_manomesso(self, coppia: tuple[str, str]) -> None:
        """Payload riscritto (es. ruolo SUPER_ADMIN) con la firma originale: rifiutato."""
        import base64
        import json

        pub, priv = coppia
        h, p, s = _token(priv).split(".")
        dati = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        dati["ruolo"] = "SUPER_ADMIN"
        p2 = base64.urlsafe_b64encode(json.dumps(dati).encode()).rstrip(b"=").decode()
        with pytest.raises(ErroreTokenNonValido):
            verifica_token(f"{h}.{p2}.{s}", chiave_pubblica_pem=pub, emittente=ISS, pubblico=AUD)
