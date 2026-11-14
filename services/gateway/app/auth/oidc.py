# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/gateway/app/auth/oidc.py — elementi OpenID Connect del cliente verso
l'aggregatore SPID/CIE (R13b, VD-C-069).

Riferimenti pubblici: OpenID Connect Core 1.0 (§3.1 codice, §5.3 UserInfo: ``sub``
uguale a quello dell'id_token, §6 request object, §9 autenticazione del cliente);
RFC 7636 (PKCE, metodo S256); RFC 7523 (``private_key_jwt``); RFC 9101 (JAR); Linee
guida AgID «OpenID Connect in SPID» e regole tecniche CIE OIDC (PKCE S256,
``private_key_jwt``, request object firmato, ``acr_values`` SpidL2/SpidL3, attributi
richiesti con ``claims`` e restituiti dallo UserInfo firmato e cifrato). Quello che la
documentazione dell'aggregatore scelto prevede davvero (metodo del cliente, request
object, UserInfo) si imposta in console: i requisiti non verificabili da qui sono
annotati in ``docs/architettura/accesso_spid_cie.md``.

Segreti: ``SPID_CLIENT_SECRET`` e ``SPID_CHIAVE_PRIVATA_CLIENT`` restano nell'ambiente
del gateway e vanno SOLO agli host fissati al deploy (``SPID_HOST_AMMESSI``): chi cambia
gli indirizzi dalla console non porta fuori il segreto (stessa regola di R13a).
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

import jwt

METODI_CLIENTE = ("client_secret_post", "client_secret_basic", "private_key_jwt")
CLAIM_CODICE_FISCALE = "https://attributes.eid.gov.it/fiscal_number"
DURATA_ASSERZIONE = 300
#: Algoritmi ammessi per lo UserInfo cifrato (gestione della chiave e contenuto)
ALGORITMI_JWE = ("RSA-OAEP", "RSA-OAEP-256", "ECDH-ES", "ECDH-ES+A256KW",
                 "A128CBC-HS256", "A256CBC-HS512", "A128GCM", "A256GCM")


class ErroreOidc(ValueError):
    """Risposta o configurazione OIDC non utilizzabile (messaggio sicuro per i log)."""


def b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


def coppia_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge S256), RFC 7636 §4.1–4.2 (43–128 caratteri)."""
    verifier = secrets.token_urlsafe(64)[:96]
    return verifier, b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def host_ammesso(url: str, ammessi: Iterable[str]) -> bool:
    """True se l'host dell'indirizzo è fra quelli fissati al deploy. Nessun elenco =
    nessun vincolo (solo fuori produzione: lo controlla l'avvio)."""
    elenco = {h.strip().lower() for h in ammessi if h.strip()}
    if not elenco:
        return True
    return (urlparse(url).hostname or "").lower() in elenco


def chiave_privata(pem: str) -> Any:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        return load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError):
        raise ErroreOidc("SPID_CHIAVE_PRIVATA_CLIENT non leggibile") from None


def _alg(chiave: Any) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    if isinstance(chiave, rsa.RSAPrivateKey):
        return "RS256"
    if isinstance(chiave, ec.EllipticCurvePrivateKey):
        return "ES256"
    raise ErroreOidc("tipo di chiave del cliente non ammesso (RSA o EC P-256)")


def asserzione_cliente(*, client_id: str, audience: str, chiave: Any, kid: str,
                       adesso: int | None = None) -> str:
    """``client_assertion`` per ``private_key_jwt`` (RFC 7523 §3; OIDC Core §9):
    iss = sub = client_id, aud = endpoint del token, jti monouso, scadenza breve."""
    ora = int(adesso if adesso is not None else time.time())
    return jwt.encode({"iss": client_id, "sub": client_id, "aud": audience,
                       "jti": str(uuid.uuid4()), "iat": ora, "exp": ora + DURATA_ASSERZIONE},
                      chiave, algorithm=_alg(chiave), headers={"kid": kid})


def oggetto_richiesta(parametri: Mapping[str, Any], *, client_id: str, audience: str,
                      chiave: Any, kid: str, adesso: int | None = None) -> str:
    """Request object firmato (RFC 9101; OIDC Core §6.1) con tutti i parametri."""
    ora = int(adesso if adesso is not None else time.time())
    corpo = {**dict(parametri), "iss": client_id, "aud": audience, "iat": ora,
             "exp": ora + DURATA_ASSERZIONE, "jti": str(uuid.uuid4())}
    return jwt.encode(corpo, chiave, algorithm=_alg(chiave),
                      headers={"kid": kid, "typ": "oauth-authz-req+jwt"})


def richiesta_attributi() -> str:
    """Parametro ``claims`` (OIDC Core §5.5): codice fiscale essenziale nello UserInfo e
    nell'id_token (l'aggregatore usa quello che supporta)."""
    voce = {CLAIM_CODICE_FISCALE: {"essential": True}}
    return json.dumps({"userinfo": voce, "id_token": voce}, separators=(",", ":"))


def _jwk_set(jwks: Mapping[str, Any]) -> jwt.PyJWKSet:
    try:
        return jwt.PyJWKSet.from_dict(dict(jwks))
    except jwt.PyJWTError:
        raise ErroreOidc("JWKS dell'OP non leggibile") from None


def decodifica_userinfo(risposta: bytes, tipo_contenuto: str, *, jwks: Mapping[str, Any],
                        issuer: str, client_id: str, sub_id_token: str,
                        chiave_decifratura: Any | None) -> dict[str, Any]:
    """Claim dello UserInfo. JSON semplice, JWS firmato dall'OP o JWE (cifrato per il
    cliente) che contiene il JWS. ``sub`` deve essere quello dell'id_token (OIDC Core
    §5.3.2); per il JWS anche ``iss`` e ``aud``."""
    testo = risposta.decode("utf-8", "replace").strip()
    if "json" in tipo_contenuto and not testo.startswith("ey"):
        try:
            claims = json.loads(testo)
        except json.JSONDecodeError:
            raise ErroreOidc("UserInfo JSON non valido") from None
        if not isinstance(claims, dict):
            raise ErroreOidc("UserInfo non è un oggetto")
    else:
        parti = testo.split(".")
        if len(parti) == 5:
            if chiave_decifratura is None:
                raise ErroreOidc("UserInfo cifrato ma SPID_CHIAVE_PRIVATA_CLIENT assente")
            from cryptography.hazmat.primitives import serialization
            from joserfc import jwe
            from joserfc.jwk import ECKey, RSAKey

            try:
                pem = chiave_decifratura.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption())
                chiave = (RSAKey if _alg(chiave_decifratura) == "RS256" else ECKey
                          ).import_key(pem)
                testo = jwe.decrypt_compact(testo, chiave, algorithms=list(ALGORITMI_JWE)
                                            ).plaintext.decode("utf-8").strip()
            except Exception:  # noqa: BLE001 — qualunque errore di decifratura
                raise ErroreOidc("UserInfo cifrato non decifrabile") from None
            parti = testo.split(".")
        if len(parti) != 3:
            raise ErroreOidc("UserInfo non è un JWT")
        try:
            testa = jwt.get_unverified_header(testo)
        except jwt.PyJWTError:
            raise ErroreOidc("UserInfo JWT malformato") from None
        if testa.get("alg") not in ("RS256", "PS256", "ES256"):
            raise ErroreOidc("algoritmo dello UserInfo non ammesso")
        candidati = [k for k in _jwk_set(jwks).keys
                     if k.public_key_use in (None, "sig") and k.key_id == testa.get("kid")]
        if len(candidati) != 1:
            raise ErroreOidc("chiave della firma dello UserInfo non nel JWKS")
        try:
            claims = jwt.decode(testo, candidati[0].key, algorithms=[testa["alg"]],
                                audience=client_id, issuer=issuer, leeway=30,
                                options={"require": ["iss", "aud", "sub"]})
        except jwt.PyJWTError as exc:
            raise ErroreOidc(f"UserInfo non valido: {type(exc).__name__}") from None
    if str(claims.get("sub") or "") != sub_id_token or not sub_id_token:
        raise ErroreOidc("sub dello UserInfo diverso da quello dell'id_token")
    return claims


def problemi_avvio(s: Any) -> list[str]:
    """Controlli d'avvio del gateway (in produzione fermano l'avvio): con un segreto del
    cliente OIDC servono gli host a cui legarlo; la chiave privata deve essere leggibile."""
    problemi: list[str] = []
    segreto = s.spid_client_secret.get_secret_value().strip()
    chiave = s.spid_chiave_privata_client.get_secret_value().strip()
    if (segreto or chiave) and not [h for h in s.spid_host_ammessi if h.strip()]:
        problemi.append("SPID_HOST_AMMESSI vuoto: il segreto del cliente OIDC andrebbe a "
                        "qualunque indirizzo scritto in console (legarlo agli host "
                        "dell'aggregatore)")
    if chiave:
        try:
            _alg(chiave_privata(chiave))
        except ErroreOidc as exc:
            problemi.append(str(exc))
        if not s.spid_chiave_client_kid.strip():
            problemi.append("SPID_CHIAVE_CLIENT_KID obbligatorio con SPID_CHIAVE_PRIVATA_CLIENT")
    return problemi


__all__ = [
    "problemi_avvio", "CLAIM_CODICE_FISCALE", "ErroreOidc", "METODI_CLIENTE", "asserzione_cliente",
           "chiave_privata", "coppia_pkce", "decodifica_userinfo", "host_ammesso",
           "oggetto_richiesta", "richiesta_attributi"]
