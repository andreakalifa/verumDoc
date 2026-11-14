# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Simulatore di OpenID Provider SPID/CIE (aggregatore) per collaudo ed e2e (R13b, VD-C-069).
SOLO sviluppo e test: chiavi generate a runtime, nessuna identità reale.

Implementa quanto il cliente del gateway usa:
  - ``/.well-known/openid-configuration``, ``/jwks``;
  - ``/authorize``: approva subito con il codice fiscale e il livello configurati;
    PKCE S256 obbligatorio (``pkce_obbligatorio``), request object firmato verificato
    con le chiavi del cliente se presente (o obbligatorio con ``request_object``);
  - ``/token``: codice monouso, redirect_uri e code_verifier controllati; autenticazione
    del cliente ``client_secret_post``, ``client_secret_basic`` o ``private_key_jwt``
    (asserzione verificata: firma, iss = sub = client_id, aud = endpoint, scadenza, jti
    monouso); id_token RS256 con ``acr`` SpidL2/L3, ``nonce`` e, senza UserInfo, il
    codice fiscale;
  - ``/userinfo``: JWT firmato dall'OP (``iss``, ``aud``, ``sub``, codice fiscale),
    cifrato per il cliente (JWE RSA-OAEP-256 / A256GCM) con ``cifra_userinfo``;
  - federazione OpenID: Entity Configuration dell'OP e del Trust Anchor (``/ta``) e
    ``/ta/fetch`` con il Subordinate Statement dell'OP.
Leve per le prove negative: ``sub_userinfo`` diverso, ``acr`` assente, firma con una
chiave estranea, Trust Anchor che non attesta l'OP.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

ASSERZIONE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def jwk_pubblica(chiave: Any, kid: str, uso: str = "sig") -> dict[str, Any]:
    from cryptography.hazmat.primitives.asymmetric import ec

    if isinstance(chiave, ec.EllipticCurvePrivateKey):
        jwk: dict[str, Any] = jwt.algorithms.ECAlgorithm.to_jwk(chiave.public_key(),
                                                                as_dict=True)
        jwk["alg"] = "ES256" if uso == "sig" else "ECDH-ES"
    else:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(chiave.public_key(), as_dict=True)
        jwk["alg"] = "RS256" if uso == "sig" else "RSA-OAEP-256"
    jwk.update({"kid": kid, "use": uso})
    return jwk


def _b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


@dataclass
class SimulatoreOidc:
    url: str = "https://aggregatore.simulato.test"
    client_id: str = "verumdoc-prova"
    client_secret: str = ""
    #: JWKS PUBBLICO del cliente (private_key_jwt, request object, cifratura UserInfo)
    client_jwks: dict[str, Any] = field(default_factory=dict)
    codice_fiscale: str = "TINIT-FTZPRV00A01Z999V"
    livello: int = 2
    pkce_obbligatorio: bool = True
    request_object: bool = False
    cifra_userinfo: bool = False
    acr_assente: bool = False
    sub_userinfo: str | None = None
    ta_attesta_op: bool = True
    #: Entity Configuration del Trust Anchor autofirmata con questa chiave (non fissata)
    chiave_ec_ta_falsa: Any | None = None
    #: codice fiscale anche nell'id_token (False: solo dallo UserInfo)
    cf_nell_id_token: bool = True
    chiave: Any = field(default_factory=lambda: rsa.generate_private_key(65537, 2048))
    chiave_ta: Any = field(default_factory=lambda: rsa.generate_private_key(65537, 2048))
    chiave_firma_falsa: Any | None = None
    codici: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_accesso: dict[str, str] = field(default_factory=dict)
    jti_visti: set[str] = field(default_factory=set)
    richieste: list[dict[str, Any]] = field(default_factory=list)

    @property
    def issuer(self) -> str:
        return self.url

    @property
    def trust_anchor(self) -> str:
        return f"{self.url}/ta"

    @property
    def jwks(self) -> dict[str, Any]:
        return {"keys": [jwk_pubblica(self.chiave, "op-1")]}

    @property
    def jwks_ta(self) -> dict[str, Any]:
        return {"keys": [jwk_pubblica(self.chiave_ta, "ta-1")]}

    def configurazione_console(self) -> dict[str, Any]:
        return {"aggregatore_url": self.url, "issuer": self.issuer,
                "jwks_url": f"{self.url}/jwks", "client_id": self.client_id,
                "livello_minimo": 2}

    def _firma(self, claims: dict[str, Any], *, typ: str | None = None, kid: str = "op-1",
               chiave: Any | None = None) -> str:
        intestazioni = {"kid": kid, **({"typ": typ} if typ else {})}
        return jwt.encode(claims, chiave or self.chiave_firma_falsa or self.chiave,
                          algorithm="RS256", headers=intestazioni)

    def _chiave_cliente(self, kid: str | None) -> Any:
        try:
            insieme = jwt.PyJWKSet.from_dict(self.client_jwks)
        except jwt.PyJWTError:
            return None
        trovate = [k for k in insieme.keys if k.key_id == kid and k.public_key_use in
                   (None, "sig")]
        return trovate[0].key if len(trovate) == 1 else None

    def _verifica_cliente(self, token: str, *, aud: str, typ: str | None = None) -> dict[str, Any]:
        testa = jwt.get_unverified_header(token)
        if typ is not None and testa.get("typ") != typ:
            raise ValueError("typ inatteso")
        chiave = self._chiave_cliente(testa.get("kid"))
        if chiave is None:
            raise ValueError("chiave del cliente sconosciuta")
        return jwt.decode(token, chiave, algorithms=[testa.get("alg", "RS256")], audience=aud,
                          options={"require": ["iss", "aud", "exp", "iat"]})


def crea_app(sim: SimulatoreOidc) -> FastAPI:
    app = FastAPI(title="Simulatore OIDC SPID/CIE (solo collaudo)")

    @app.get("/.well-known/openid-configuration")
    async def configurazione() -> dict[str, Any]:
        return {"issuer": sim.issuer, "authorization_endpoint": f"{sim.url}/authorize",
                "token_endpoint": f"{sim.url}/token", "userinfo_endpoint": f"{sim.url}/userinfo",
                "jwks_uri": f"{sim.url}/jwks", "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["private_key_jwt",
                                                          "client_secret_post",
                                                          "client_secret_basic"],
                "acr_values_supported": ["https://www.spid.gov.it/SpidL2",
                                         "https://www.spid.gov.it/SpidL3"],
                "request_parameter_supported": True}

    @app.get("/jwks")
    async def jwks() -> dict[str, Any]:
        return sim.jwks

    @app.get("/authorize")
    async def authorize(request: Request) -> Response:
        p = dict(request.query_params)
        sim.richieste.append({"authorize": p})
        if "request" in p:
            try:
                p = {**p, **sim._verifica_cliente(p["request"], aud=sim.issuer,
                                                  typ="oauth-authz-req+jwt")}
            except (ValueError, jwt.PyJWTError):
                return PlainTextResponse("request object non valido", 400)
        elif sim.request_object:
            return PlainTextResponse("request object obbligatorio", 400)
        if p.get("client_id") != sim.client_id or p.get("response_type") != "code":
            return PlainTextResponse("client_id o response_type non validi", 400)
        if sim.pkce_obbligatorio and (p.get("code_challenge_method") != "S256"
                                      or not p.get("code_challenge")):
            return PlainTextResponse("PKCE S256 obbligatorio", 400)
        codice = secrets.token_urlsafe(24)
        sim.codici[codice] = {"nonce": p.get("nonce"), "challenge": p.get("code_challenge"),
                              "redirect_uri": p.get("redirect_uri"), "acr": p.get("acr_values")}
        destinazione = p["redirect_uri"] + "?" + urllib.parse.urlencode(
            {"code": codice, "state": p.get("state", "")})
        return RedirectResponse(destinazione, status_code=302)

    def _autentica(dati: dict[str, str], autorizzazione: str | None) -> bool:
        if dati.get("client_assertion_type") == ASSERZIONE:
            try:
                claims = sim._verifica_cliente(dati.get("client_assertion", ""),
                                               aud=f"{sim.url}/token")
            except (ValueError, jwt.PyJWTError):
                return False
            if claims.get("iss") != sim.client_id or claims.get("sub") != sim.client_id:
                return False
            jti = str(claims.get("jti") or "")
            if not jti or jti in sim.jti_visti:
                return False
            sim.jti_visti.add(jti)
            return True
        if autorizzazione and autorizzazione.lower().startswith("basic "):
            utente, _, segreto = base64.b64decode(autorizzazione[6:]).decode().partition(":")
            return bool(sim.client_secret) and urllib.parse.unquote(utente) == sim.client_id \
                and urllib.parse.unquote(segreto) == sim.client_secret
        return bool(sim.client_secret) and dati.get("client_secret") == sim.client_secret

    @app.post("/token")
    async def token(request: Request, authorization: str | None = Header(None)) -> Response:
        dati = {k: str(v) for k, v in (await request.form()).items()}
        sim.richieste.append({"token": {k: v for k, v in dati.items()
                                        if k not in ("client_secret", "client_assertion")}})
        if not _autentica(dati, authorization):
            return JSONResponse({"error": "invalid_client"}, 401)
        voce = sim.codici.pop(dati.get("code", ""), None)
        if voce is None or dati.get("redirect_uri") != voce["redirect_uri"]:
            return JSONResponse({"error": "invalid_grant"}, 400)
        if voce["challenge"]:
            atteso = _b64url(hashlib.sha256(dati.get("code_verifier", "").encode()).digest())
            if atteso != voce["challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, 400)
        ora = int(time.time())
        claims: dict[str, Any] = {"iss": sim.issuer, "aud": sim.client_id, "sub": "persona-sim-1",
                                  "iat": ora, "exp": ora + 300, "nonce": voce["nonce"]}
        if not sim.acr_assente:
            claims["acr"] = f"https://www.spid.gov.it/SpidL{sim.livello}"
        if sim.cf_nell_id_token:
            claims["https://attributes.eid.gov.it/fiscal_number"] = sim.codice_fiscale
        accesso = secrets.token_urlsafe(24)
        sim.token_accesso[accesso] = "persona-sim-1"
        return JSONResponse({"id_token": sim._firma(claims), "access_token": accesso,
                             "token_type": "Bearer", "expires_in": 300})

    @app.get("/userinfo")
    async def userinfo(authorization: str | None = Header(None)) -> Response:
        accesso = (authorization or "").removeprefix("Bearer ").strip()
        sub = sim.token_accesso.get(accesso)
        if sub is None:
            return JSONResponse({"error": "invalid_token"}, 401)
        ora = int(time.time())
        firmato = sim._firma({"iss": sim.issuer, "aud": sim.client_id,
                              "sub": sim.sub_userinfo or sub, "iat": ora, "exp": ora + 300,
                              "https://attributes.eid.gov.it/fiscal_number":
                                  sim.codice_fiscale})
        if not sim.cifra_userinfo:
            return Response(firmato, media_type="application/jwt")
        from joserfc import jwe
        from joserfc.jwk import RSAKey

        chiave = next(k for k in sim.client_jwks["keys"] if k.get("use") == "enc")
        cifrato = jwe.encrypt_compact({"alg": "RSA-OAEP-256", "enc": "A256GCM",
                                       "kid": chiave["kid"], "cty": "JWT"},
                                      firmato.encode(), RSAKey.import_key(
                                          {k: chiave[k] for k in ("kty", "n", "e")}),
                                      algorithms=["RSA-OAEP-256", "A256GCM"])
        return Response(cifrato, media_type="application/jwt")

    # ── federazione OpenID ─────────────────────────────────────────────
    def _dichiarazione(iss: str, sub: str, jwks: dict[str, Any], chiave: Any, kid: str,
                       **altro: Any) -> str:
        ora = int(time.time())
        return sim._firma({"iss": iss, "sub": sub, "iat": ora, "exp": ora + 3600,
                           "jwks": jwks, **altro}, typ="entity-statement+jwt", kid=kid,
                          chiave=chiave)

    @app.get("/.well-known/openid-federation")
    async def ec_op() -> Response:
        return Response(_dichiarazione(
            sim.issuer, sim.issuer, sim.jwks, sim.chiave, "op-1",
            authority_hints=[sim.trust_anchor],
            metadata={"openid_provider": {"issuer": sim.issuer, "jwks": sim.jwks,
                                          "authorization_endpoint": f"{sim.url}/authorize",
                                          "token_endpoint": f"{sim.url}/token"}}),
            media_type="application/entity-statement+jwt")

    @app.get("/ta/.well-known/openid-federation")
    async def ec_ta() -> Response:
        falsa = sim.chiave_ec_ta_falsa
        return Response(_dichiarazione(
            sim.trust_anchor, sim.trust_anchor,
            sim.jwks_ta if falsa is None else {"keys": [jwk_pubblica(falsa, "ta-1")]},
            sim.chiave_ta if falsa is None else falsa, "ta-1",
            metadata={"federation_entity": {
                "federation_fetch_endpoint": f"{sim.trust_anchor}/fetch"}}),
            media_type="application/entity-statement+jwt")

    @app.get("/ta/fetch")
    async def fetch(sub: str) -> Response:
        if sub != sim.issuer:
            return PlainTextResponse("entità sconosciuta", 404)
        attestate = sim.jwks if sim.ta_attesta_op else {
            "keys": [jwk_pubblica(rsa.generate_private_key(65537, 2048), "op-1")]}
        if not sim.ta_attesta_op:
            attestate["keys"][0]["kid"] = "altra"
        return Response(_dichiarazione(sim.trust_anchor, sub, attestate, sim.chiave_ta,
                                       "ta-1"), media_type="application/entity-statement+jwt")

    return app


__all__ = ["SimulatoreOidc", "crea_app", "jwk_pubblica"]
