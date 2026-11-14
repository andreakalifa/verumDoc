# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R13b, revisione: trust chain OpenID Federation (rilievi 2 e 4).

Riproduzione del rilievo 2: configurazione dell'OP autofirmata dall'attaccante con lo
STESSO kid della chiave attestata dal Trust Anchor ma chiave diversa → rifiutata
(thumbprint RFC 7638, jwks del Subordinate Statement unica fonte delle chiavi).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from libs.shared.security.oidc_federazione import (
    CatenaNonValida,
    controlla_indirizzo,
    impronta_jwk,
    risolvi,
)

TA, INT, OP = "https://ta.test", "https://intermedio.test", "https://op.test"


def _jwk(k: Any, kid: str) -> dict[str, Any]:
    d = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(k.public_key()))
    d["kid"] = kid
    return d


def _firma(claims: dict[str, Any], k: Any, kid: str) -> str:
    return jwt.encode(claims, k, algorithm="ES256",
                      headers={"kid": kid, "typ": "entity-statement+jwt"})


class Federazione:
    """TA → (intermedio facoltativo) → OP, con leve per l'attaccante."""

    def __init__(self, *, intermedio: bool = False) -> None:
        self.ora = int(time.time())
        self.ta = ec.generate_private_key(ec.SECP256R1())
        self.int = ec.generate_private_key(ec.SECP256R1())
        self.op = ec.generate_private_key(ec.SECP256R1())
        self.intermedio = intermedio
        self.pagine: dict[str, str] = {}
        self.ta_jwks = {"keys": [_jwk(self.ta, "ta1")]}
        self.costruisci()

    def _base(self) -> dict[str, Any]:
        return {"iat": self.ora, "exp": self.ora + 3600}

    def _ec(self, chi: str, chiave: Any, kid: str, *, hints: list[str] | None = None,
            fetch: str | None = None, jwks: dict[str, Any] | None = None,
            op_meta: bool = False) -> str:
        meta: dict[str, Any] = {}
        if fetch:
            meta["federation_entity"] = {"federation_fetch_endpoint": fetch}
        if op_meta:
            meta["openid_provider"] = {"issuer": chi, "jwks": jwks or {"keys": []}}
        return _firma({**self._base(), "iss": chi, "sub": chi,
                       "jwks": jwks or {"keys": [_jwk(chiave, kid)]},
                       "authority_hints": hints or [], "metadata": meta}, chiave, kid)

    def costruisci(self, *, ec_op: str | None = None, ec_int: str | None = None,
                   ss_int_op: str | None = None) -> None:
        sup = INT if self.intermedio else TA
        self.pagine = {
            f"{TA}/.well-known/openid-federation": self._ec(TA, self.ta, "ta1",
                                                            fetch=f"{TA}/fetch"),
            f"{OP}/.well-known/openid-federation": ec_op or self._ec(
                OP, self.op, "k1", hints=[sup], op_meta=True,
                jwks={"keys": [_jwk(self.op, "k1")]}),
        }
        ss_op = lambda emittente, chiave, kid: _firma(  # noqa: E731
            {**self._base(), "iss": emittente, "sub": OP,
             "jwks": {"keys": [_jwk(self.op, "k1")]}}, chiave, kid)
        if self.intermedio:
            self.pagine[f"{INT}/.well-known/openid-federation"] = ec_int or self._ec(
                INT, self.int, "i1", hints=[TA], fetch=f"{INT}/fetch")
            self.pagine[f"{TA}/fetch?sub=https%3A%2F%2Fintermedio.test"] = _firma(
                {**self._base(), "iss": TA, "sub": INT,
                 "jwks": {"keys": [_jwk(self.int, "i1")]}}, self.ta, "ta1")
            self.pagine[f"{INT}/fetch?sub=https%3A%2F%2Fop.test"] = \
                ss_int_op or ss_op(INT, self.int, "i1")
        else:
            self.pagine[f"{TA}/fetch?sub=https%3A%2F%2Fop.test"] = ss_op(TA, self.ta, "ta1")

    def risolvi(self, **kw: Any) -> Any:
        async def scarica(u: str) -> str:
            return self.pagine[u]
        return asyncio.run(risolvi(OP, trust_anchor=TA, chiavi_trust_anchor=self.ta_jwks,
                                   scarica=scarica, **kw))


@pytest.mark.parametrize("intermedio", [False, True])
def test_catena_valida(intermedio: bool) -> None:
    f = Federazione(intermedio=intermedio)
    c = f.risolvi()
    assert c.percorso[-1] == TA
    assert impronta_jwk(c.jwks["keys"][0]) == impronta_jwk(_jwk(f.op, "k1"))


def test_stesso_kid_chiave_diversa_rifiutata() -> None:
    """Riproduzione del rilievo 2 (prima accettata)."""
    f = Federazione()
    att = ec.generate_private_key(ec.SECP256R1())
    falsa = f._ec(OP, att, "k1", hints=[TA], op_meta=True, jwks={"keys": [_jwk(att, "k1")]})
    f.costruisci(ec_op=falsa)
    with pytest.raises(CatenaNonValida):
        f.risolvi()


def test_intermedio_stesso_kid_chiave_diversa_rifiutato() -> None:
    """L'attaccante sostituisce l'intermedio (stesso kid) e firma con la sua chiave la
    dichiarazione sull'OP: la dichiarazione va riverificata con le chiavi attestate dal TA."""
    f = Federazione(intermedio=True)
    att = ec.generate_private_key(ec.SECP256R1())
    op_att = ec.generate_private_key(ec.SECP256R1())
    ec_int = f._ec(INT, att, "i1", hints=[TA], fetch=f"{INT}/fetch")
    ss = _firma({**f._base(), "iss": INT, "sub": OP, "jwks": {"keys": [_jwk(op_att, "k1")]}},
                att, "i1")
    ec_op = f._ec(OP, op_att, "k1", hints=[INT], op_meta=True,
                  jwks={"keys": [_jwk(op_att, "k1")]})
    f.costruisci(ec_op=ec_op, ec_int=ec_int, ss_int_op=ss)
    with pytest.raises(CatenaNonValida):
        f.risolvi()


def test_chiave_dichiarata_con_kid_attestato_ma_impronta_diversa() -> None:
    """Firma con la chiave vera, ma la configurazione dichiara per lo stesso kid un'altra
    chiave pubblica (che finirebbe fra i metadati): rifiutata per thumbprint."""
    f = Federazione()
    att = ec.generate_private_key(ec.SECP256R1())
    falsa = _firma({**f._base(), "iss": OP, "sub": OP, "jwks": {"keys": [_jwk(att, "k1")]},
                    "authority_hints": [TA],
                    "metadata": {"openid_provider": {"issuer": OP, "jwks": {"keys": []}}}},
                   f.op, "k1")
    f.costruisci(ec_op=falsa)
    with pytest.raises(CatenaNonValida):
        f.risolvi()


@pytest.mark.parametrize("url, motivo", [
    ("http://ta.test/fetch", "https"),
    ("https://utente:pw@ta.test/x", "credenziali"),
    ("https://altro.test/x", "non ammesso"),
    ("", "assente"),
])
def test_indirizzi_di_federazione(url: str, motivo: str) -> None:
    with pytest.raises(CatenaNonValida, match=motivo):
        controlla_indirizzo(url, ["ta.test", "op.test"])


def test_authority_hint_fuori_dagli_host_ammessi() -> None:
    f = Federazione()
    with pytest.raises(CatenaNonValida, match="non ammesso"):
        f.risolvi(host_ammessi=["op.test"])
    assert f.risolvi(host_ammessi=["op.test", "ta.test"]).percorso[-1] == TA


def test_impronta_rfc7638_vettore_della_norma() -> None:
    # RFC 7638 §3.1
    jwk = {"kty": "RSA", "e": "AQAB", "n": (
        "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_"
        "BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0"
        "_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWh"
        "AI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw")}
    assert impronta_jwk(jwk) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


def test_chiavi_dell_op_solo_quelle_attestate() -> None:
    """La configurazione dell'OP dichiara una chiave in più (k2) non attestata: le chiavi
    restituite sono SOLO quelle del Subordinate Statement."""
    f = Federazione()
    extra = ec.generate_private_key(ec.SECP256R1())
    con_extra = f._ec(OP, f.op, "k1", hints=[TA], op_meta=True,
                      jwks={"keys": [_jwk(f.op, "k1"), _jwk(extra, "k2")]})
    f.costruisci(ec_op=con_extra)
    c = f.risolvi()
    assert {k["kid"] for k in c.jwks["keys"]} == {"k1"}


def test_intermedio_firma_con_chiave_non_attestata_dal_trust_anchor() -> None:
    """L'intermedio dichiara per sé i1 (attestata) e i2 (NON attestata dal TA) e firma la
    dichiarazione sull'OP con i2: rifiutata (le chiavi dell'intermedio sono quelle del TA)."""
    f = Federazione(intermedio=True)
    i2 = ec.generate_private_key(ec.SECP256R1())
    ec_int = f._ec(INT, f.int, "i1", hints=[TA], fetch=f"{INT}/fetch",
                   jwks={"keys": [_jwk(f.int, "i1"), _jwk(i2, "i2")]})
    ss = _firma({**f._base(), "iss": INT, "sub": OP, "jwks": {"keys": [_jwk(f.op, "k1")]}},
                i2, "i2")
    f.costruisci(ec_int=ec_int, ss_int_op=ss)
    with pytest.raises(CatenaNonValida):
        f.risolvi()


def test_altra_chiave_dichiarata_con_kid_attestato_e_impronta_diversa() -> None:
    """Firma con k1 attestata, ma dichiara k2 con una chiave diversa da quella che il
    superiore attesta con lo stesso kid: rifiutata."""
    f = Federazione()
    k2_vera, k2_falsa = (ec.generate_private_key(ec.SECP256R1()) for _ in range(2))
    ec_op = f._ec(OP, f.op, "k1", hints=[TA], op_meta=True,
                  jwks={"keys": [_jwk(f.op, "k1"), _jwk(k2_falsa, "k2")]})
    f.costruisci(ec_op=ec_op)
    f.pagine[f"{TA}/fetch?sub=https%3A%2F%2Fop.test"] = _firma(
        {**f._base(), "iss": TA, "sub": OP,
         "jwks": {"keys": [_jwk(f.op, "k1"), _jwk(k2_vera, "k2")]}}, f.ta, "ta1")
    with pytest.raises(CatenaNonValida, match="stesso kid"):
        f.risolvi()
