# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Autenticatore WebAuthn di prova (R10, D08): passkey P-256 generata a runtime
(REGOLE §31, R08: chiavi di prova solo generate), risposte di registrazione
(attestazione «none») e di accesso firmate come un autenticatore vero, per
provare il gateway senza browser né dispositivo.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import struct
from dataclasses import dataclass, field
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


def da_b64url(testo: str) -> bytes:
    return base64.urlsafe_b64decode(testo + "=" * (-len(testo) % 4))


@dataclass
class Autenticatore:
    rp_id: str
    origine: str
    chiave: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    credential_id: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    contatore: int = 0

    def _cose(self) -> bytes:
        numeri = self.chiave.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numeri.x.to_bytes(32, "big"),
                            -3: numeri.y.to_bytes(32, "big")})

    def _client_data(self, tipo: str, sfida: str, origine: str | None = None) -> bytes:
        return json.dumps({"type": tipo, "challenge": sfida,
                           "origin": origine or self.origine, "crossOrigin": False}).encode()

    def registra(self, opzioni: dict[str, Any], origine: str | None = None) -> dict[str, Any]:
        rp = hashlib.sha256(opzioni["rp"]["id"].encode()).digest()
        dati_auth = (rp + bytes([0x45]) + struct.pack(">I", self.contatore) + bytes(16)
                     + struct.pack(">H", len(self.credential_id)) + self.credential_id
                     + self._cose())
        attestazione = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": dati_auth})
        return {"id": b64url(self.credential_id), "rawId": b64url(self.credential_id),
                "type": "public-key",
                "response": {"clientDataJSON": b64url(self._client_data(
                    "webauthn.create", opzioni["challenge"], origine)),
                    "attestationObject": b64url(attestazione)}}

    def firma(self, opzioni: dict[str, Any], *, contatore: int | None = None,
              origine: str | None = None) -> dict[str, Any]:
        self.contatore = self.contatore + 1 if contatore is None else contatore
        rp = hashlib.sha256((opzioni.get("rpId") or self.rp_id).encode()).digest()
        dati_auth = rp + bytes([0x05]) + struct.pack(">I", self.contatore)
        client = self._client_data("webauthn.get", opzioni["challenge"], origine)
        firma = self.chiave.sign(dati_auth + hashlib.sha256(client).digest(),
                                 ec.ECDSA(hashes.SHA256()))
        return {"id": b64url(self.credential_id), "rawId": b64url(self.credential_id),
                "type": "public-key",
                "response": {"clientDataJSON": b64url(client),
                             "authenticatorData": b64url(dati_auth),
                             "signature": b64url(firma), "userHandle": None}}
