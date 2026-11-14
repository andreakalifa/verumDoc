# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/pkcs11_fornitore.py
Firma con HSM o smart card via PKCS#11 (python-pkcs11) — R13a.

  - libreria: SOLO fra quelle ammesse al deploy (``PKCS11_LIBRERIE_AMMESSE``):
    il percorso arriva dalla console, ma caricare una libreria è eseguire codice,
    quindi l'elenco lo fissa chi fa il deploy;
  - token per etichetta, PIN dal Secret (``FIRMA_SEGRETI_JSON``), chiave privata
    per etichetta; certificato dalla configurazione (PEM pubblico) o dal token
    (oggetto certificato con la stessa etichetta);
  - firma sull'IMPRONTA: RSA PKCS#1 v1.5 con DigestInfo costruito qui
    (meccanismo ``RSA_PKCS``), ECDSA sull'impronta (meccanismo ``ECDSA``, r||s
    convertito in DER). Le operazioni PKCS#11 sono bloccanti: girano in un thread,
    una sessione per firma, sotto un lock (molti HSM non sono rientranti).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import structlog
from asn1crypto import algos, core, x509

from app.services.fornitori import ErroreFornitore, FornitoreFirma, MaterialeFirma

logger = structlog.get_logger(__name__)

_OID_DIGEST = {"sha256": "2.16.840.1.101.3.4.2.1", "sha384": "2.16.840.1.101.3.4.2.2",
               "sha512": "2.16.840.1.101.3.4.2.3"}


def digest_info(impronta: bytes, algoritmo: str) -> bytes:
    """DigestInfo DER (RFC 8017 §9.2) per RSA PKCS#1 v1.5 su impronta."""
    return algos.DigestInfo({
        "digest_algorithm": {"algorithm": algoritmo, "parameters": core.Null()},
        "digest": impronta,
    }).dump()


def ammessa(libreria: str, ammesse: str) -> bool:
    elenco = {v.strip() for v in (ammesse or "").split(",") if v.strip()}
    return bool(libreria) and libreria in elenco


class FornitoreFirmaPKCS11(FornitoreFirma):
    """Sigillo con chiave in HSM/smart card (PKCS#11)."""

    nome = "pkcs11"
    di_sviluppo = False

    def __init__(self, *, libreria: str, token_label: str, chiave_label: str, pin: str,
                 certificato_pem: str = "", librerie_ammesse: str = "", codice: str = "",
                 radici: tuple[x509.Certificate, ...] = ()) -> None:
        if not ammessa(libreria, librerie_ammesse):
            raise ErroreFornitore(
                "libreria PKCS#11 non fra quelle ammesse al deploy (PKCS11_LIBRERIE_AMMESSE)",
                {"servizio": "pkcs11", "libreria": libreria})
        if not pin:
            raise ErroreFornitore("PIN del token PKCS#11 mancante nel Secret",
                                  {"servizio": "pkcs11", "motivo": "pin_richiesto"})
        self._libreria = libreria
        self._token_label = token_label
        self._chiave_label = chiave_label
        self._pin = pin
        self._certificato_pem = certificato_pem
        self.codice = codice
        self.radici_proprie = tuple(radici)
        self._materiale: MaterialeFirma | None = None
        self._lock = threading.Lock()
        self._tipo = "rsa"
        self._bit = 0

    def __repr__(self) -> str:  # mai il PIN
        return f"FornitoreFirmaPKCS11(codice={self.codice!r}, token={self._token_label!r})"

    def _token(self) -> Any:
        try:
            import pkcs11
        except ImportError as exc:  # pragma: no cover — dipendenza dichiarata
            raise ErroreFornitore("python-pkcs11 non installato", {"servizio": "pkcs11"}) from exc
        try:
            lib = pkcs11.lib(self._libreria)
            return lib.get_token(token_label=self._token_label)
        except Exception as exc:  # noqa: BLE001 — libreria o token assenti
            raise ErroreFornitore(f"token PKCS#11 non disponibile: {type(exc).__name__}",
                                  {"servizio": "pkcs11"}) from exc

    def _materiale_sync(self) -> MaterialeFirma:
        import pkcs11
        from pkcs11 import Attribute, ObjectClass

        if self._certificato_pem:
            from libs.shared.firma_qualificata.configurazione import certificati_pem

            cert = x509.Certificate.load(certificati_pem(self._certificato_pem)[0])
        else:
            with self._lock, self._token().open(user_pin=self._pin) as sessione:
                trovati = list(sessione.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE,
                                                     Attribute.LABEL: self._chiave_label}))
                if not trovati:
                    raise ErroreFornitore("certificato non trovato nel token (etichetta "
                                          "della chiave)", {"servizio": "pkcs11"})
                cert = x509.Certificate.load(bytes(trovati[0][Attribute.VALUE]))
        with self._lock, self._token().open(user_pin=self._pin) as sessione:
            try:
                sessione.get_key(object_class=ObjectClass.PRIVATE_KEY, label=self._chiave_label)
            except (pkcs11.NoSuchKey, pkcs11.MultipleObjectsReturned) as exc:
                raise ErroreFornitore("chiave privata non trovata (o non unica) nel token",
                                      {"servizio": "pkcs11"}) from exc
        pub = cert.public_key
        self._tipo = "ec" if pub.algorithm == "ec" else "rsa"
        self._bit = pub.bit_size
        return MaterialeFirma(certificato=cert, catena=[],
                              meccanismo=algos.SignedDigestAlgorithm(
                                  {"algorithm": "sha256_ecdsa" if self._tipo == "ec"
                                   else "sha256_rsa"}))

    async def materiale(self) -> MaterialeFirma:
        if self._materiale is None:
            try:
                materiale = await asyncio.to_thread(self._materiale_sync)
            except ErroreFornitore:
                raise
            except Exception as exc:  # noqa: BLE001 — PIN errato, token bloccato, ...
                raise ErroreFornitore(f"token PKCS#11: {type(exc).__name__}",
                                      {"servizio": "pkcs11"}) from exc
            # revisione R13a (4): il token deve portare il certificato atteso
            self.identita.controlla(materiale.certificato)
            self._materiale = materiale
        return self._materiale

    def _firma_sync(self, impronta: bytes, algoritmo: str) -> bytes:
        from pkcs11 import Mechanism, ObjectClass

        with self._lock, self._token().open(user_pin=self._pin) as sessione:
            chiave = sessione.get_key(object_class=ObjectClass.PRIVATE_KEY,
                                      label=self._chiave_label)
            if self._tipo == "ec":
                grezza = bytes(chiave.sign(impronta, mechanism=Mechanism.ECDSA))
                n = (self._bit + 7) // 8
                return algos.DSASignature({"r": int.from_bytes(grezza[:n], "big"),
                                           "s": int.from_bytes(grezza[n:], "big")}).dump()
            return bytes(chiave.sign(digest_info(impronta, algoritmo),
                                     mechanism=Mechanism.RSA_PKCS))

    async def firma_impronta(self, impronta: bytes, algoritmo_digest: str) -> bytes:
        await self.materiale()
        if algoritmo_digest not in _OID_DIGEST:
            raise ErroreFornitore(f"digest non supportato: {algoritmo_digest}",
                                  {"servizio": "pkcs11"})
        try:
            return await asyncio.to_thread(self._firma_sync, impronta, algoritmo_digest)
        except ErroreFornitore:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ErroreFornitore(f"firma PKCS#11 non riuscita: {type(exc).__name__}",
                                  {"servizio": "pkcs11"}) from exc


__all__ = ["FornitoreFirmaPKCS11", "ammessa", "digest_info"]
