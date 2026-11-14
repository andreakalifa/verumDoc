# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/csc_client.py
Client Cloud Signature Consortium API v2 (firma remota) — VD-C-010, R13a, REGOLE §20.1.

Flusso (CSC API v2.0):
  1. token OAuth2 ``client_credentials`` sull'endpoint configurato (prima di
     spedire il segreto, con il pinning attivo, connessione di prova);
  2. ``credentials/list`` (se l'identificativo della credenziale non è
     configurato e ce n'è una sola) e ``credentials/info`` → certificato, catena,
     algoritmi, ``authMode``, ``SCAL``, presenza di PIN e OTP;
  3. ``credentials/authorize`` → SAD:
       - sigillo automatico (firma massiva senza interazione): PIN e OTP
         «statico» dal Secret se la credenziale li chiede; con SCAL 1 e
         ``firme_per_autorizzazione`` > 1 un SAD vale per N firme fino alla sua
         scadenza (``expiresIn``), senza ripetere l'autorizzazione;
       - con SCAL 2 il SAD è legato alle impronte: una autorizzazione per firma;
       - firma di una PERSONA con OTP (responsabile della conservazione):
         ``invia_otp`` (``credentials/sendOTP``) e ``firma_impronta_con_otp``.
         Una credenziale che chiede un OTP «online» non è usabile per il sigillo
         automatico (errore esplicito);
  4. ``signatures/signHash`` → valore di firma (ECDSA r||s convertito in DER).

La busta CAdES/PAdES resta locale (firma_cms.py): al fornitore viaggia solo
l'impronta degli attributi firmati. Il trasporto httpx è iniettabile; i test
usano il simulatore ``tools/simulatore_firma`` (ASGI) o httpx.MockTransport.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from asn1crypto import algos, x509

from app.services.fornitori import ErroreFornitore, FornitoreFirma, MaterialeFirma
from app.services.http_sicuro import ClienteHttpSicuro

logger = structlog.get_logger(__name__)

OID_HASH = {
    "sha256": "2.16.840.1.101.3.4.2.1",
    "sha384": "2.16.840.1.101.3.4.2.2",
    "sha512": "2.16.840.1.101.3.4.2.3",
}
OID_RSA = "1.2.840.113549.1.1.1"
OID_EC = "1.2.840.10045.2.1"
# Algoritmi di firma CSC (signAlgo) e meccanismo CMS corrispondente
ALGO_FIRMA = {
    ("rsa", "sha256"): ("1.2.840.113549.1.1.11", "sha256_rsa"),
    ("rsa", "sha384"): ("1.2.840.113549.1.1.12", "sha384_rsa"),
    ("rsa", "sha512"): ("1.2.840.113549.1.1.13", "sha512_rsa"),
    ("ec", "sha256"): ("1.2.840.10045.4.3.2", "sha256_ecdsa"),
    ("ec", "sha384"): ("1.2.840.10045.4.3.3", "sha384_ecdsa"),
    ("ec", "sha512"): ("1.2.840.10045.4.3.4", "sha512_ecdsa"),
}


def _b64(dati: bytes) -> str:
    return base64.b64encode(dati).decode("ascii")


def _ecdsa_der(firma: bytes, bit_chiave: int) -> bytes:
    """Alcuni RSSP restituiscono ECDSA come r||s: CMS vuole la SEQUENCE DER."""
    n = (bit_chiave + 7) // 8
    try:
        _ = algos.DSASignature.load(firma).native  # già DER
        return firma
    except Exception:  # noqa: BLE001 — non DER: si prova r||s
        logger.debug("firma_ecdsa_non_der", lunghezza=len(firma))
    if len(firma) != 2 * n:
        raise ErroreFornitore(
            "firma ECDSA del fornitore in formato non riconosciuto", {"servizio": "csc"}
        )
    r = int.from_bytes(firma[:n], "big")
    s = int.from_bytes(firma[n:], "big")
    return algos.DSASignature({"r": r, "s": s}).dump()


class ClienteCSC:
    """Client HTTP per un RSSP conforme a CSC API v2."""

    def __init__(
        self,
        url_base: str,
        url_token: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 30.0,
        ritentativi: int = 2,
        trasporto: httpx.AsyncBaseTransport | None = None,
        mtls_certificato_pem: str = "",
        mtls_chiave_pem: str = "",
        pin_spki_sha256: tuple[str, ...] = (),
        host_ammessi: tuple[str, ...] = (),
        solo_https: bool = False,
    ) -> None:
        if not url_base or not url_token or not client_id:
            raise ValueError("url_base, url_token e client_id sono obbligatori")
        self._base = url_base.rstrip("/")
        self._url_token = url_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = ClienteHttpSicuro(
            "csc", timeout=timeout, ritentativi=ritentativi, trasporto=trasporto,
            mtls_certificato_pem=mtls_certificato_pem, mtls_chiave_pem=mtls_chiave_pem,
            pin_spki_sha256=pin_spki_sha256, host_ammessi=host_ammessi, solo_https=solo_https)
        self._token: str | None = None
        self._scadenza_token = 0.0

    def __repr__(self) -> str:  # mai il segreto
        return f"ClienteCSC(base={self._base!r}, client_id={self._client_id!r})"

    @staticmethod
    def _corpo(risposta: httpx.Response, operazione: str) -> dict:
        try:
            corpo = risposta.json()
        except ValueError:
            corpo = {}
        if not isinstance(corpo, dict):
            corpo = {}
        if risposta.status_code != 200:
            # CSC v2: {"error": "...", "error_description": "..."}
            raise ErroreFornitore(
                "il fornitore di firma ha rifiutato la richiesta",
                {"servizio": "csc", "operazione": operazione, "http": risposta.status_code,
                 "errore": str(corpo.get("error") or "")[:100],
                 "descrizione": str(corpo.get("error_description") or "")[:300]},
            )
        return corpo

    async def _intestazioni(self) -> dict[str, str]:
        if self._token is None or time.monotonic() >= self._scadenza_token:
            await self._http.verifica_pin_preliminare(self._url_token)
            risposta = await self._http.richiesta(
                "POST", self._url_token, operazione="oauth2/token", idempotente=True,
                data={"grant_type": "client_credentials", "client_id": self._client_id,
                      "client_secret": self._client_secret})
            corpo = self._corpo(risposta, "oauth2/token")
            token = corpo.get("access_token")
            if not token:
                raise ErroreFornitore("token OAuth2 assente nella risposta", {"servizio": "csc"})
            self._token = str(token)
            try:
                durata = int(corpo.get("expires_in", 300))
            except (TypeError, ValueError):
                durata = 300
            self._scadenza_token = time.monotonic() + max(durata - 30, 1)
        return {"Authorization": f"Bearer {self._token}"}

    async def chiama(self, metodo: str, corpo: dict, *, idempotente: bool = True) -> dict:
        """Chiamata a un metodo CSC (es. «credentials/info») con token di servizio.
        Un 401 (token scaduto prima del previsto) rinnova il token una volta."""
        for tentativo in range(2):
            risposta = await self._http.richiesta(
                "POST", f"{self._base}/{metodo}", operazione=metodo, idempotente=idempotente,
                json=corpo, headers=await self._intestazioni())
            if risposta.status_code == 401 and tentativo == 0:
                self._token = None
                continue
            return self._corpo(risposta, metodo)
        raise AssertionError("non raggiungibile")  # pragma: no cover


@dataclass
class _Sad:
    valore: str
    scade: float
    restanti: int


class FornitoreFirmaCSC(FornitoreFirma):
    """Firma remota tramite CSC API v2 (sigillo/firma del conservatore in HSM)."""

    nome = "csc"
    di_sviluppo = False

    def __init__(
        self,
        cliente: ClienteCSC,
        credential_id: str = "",
        *,
        pin: str = "",
        otp: str = "",
        sign_algo_oid: str = "",
        modo_autorizzazione: str = "auto",
        firme_per_autorizzazione: int = 1,
        codice: str = "",
        radici: tuple[x509.Certificate, ...] = (),
    ) -> None:
        self._cliente = cliente
        self._credential_id = credential_id
        self._pin = pin
        self._otp = otp
        self._sign_algo_oid = sign_algo_oid
        self._modo = modo_autorizzazione
        self._per_autorizzazione = max(1, int(firme_per_autorizzazione))
        self.codice = codice
        self.radici_proprie = tuple(radici)
        self._materiale: MaterialeFirma | None = None
        self._auth_mode = "explicit"
        self._scal = "2"
        self._otp_presenza = "none"
        self._otp_tipo = "offline"
        self._pin_presenza = "none"
        self._tipo_chiave = "rsa"
        self._bit_chiave = 0
        self._sad: _Sad | None = None
        self.info: dict[str, Any] = {}

    def __repr__(self) -> str:  # mai PIN né OTP
        return f"FornitoreFirmaCSC(codice={self.codice!r}, credenziale={self._credential_id!r})"

    async def _credenziale(self) -> str:
        if self._credential_id:
            return self._credential_id
        elenco = await self._cliente.chiama("credentials/list", {"maxResults": 10})
        ids = [str(i) for i in (elenco.get("credentialIDs") or [])]
        if len(ids) != 1:
            raise ErroreFornitore(
                "il fornitore elenca " + ("nessuna credenziale" if not ids else
                                          f"{len(ids)} credenziali") +
                ": indicare credential_id nella configurazione", {"servizio": "csc"})
        self._credential_id = ids[0]
        return self._credential_id

    async def materiale(self) -> MaterialeFirma:
        if self._materiale is not None:
            return self._materiale
        credenziale = await self._credenziale()
        info = await self._cliente.chiama(
            "credentials/info",
            {"credentialID": credenziale, "certificates": "chain", "certInfo": True,
             "authInfo": True},
        )
        try:
            certificati = [
                x509.Certificate.load(base64.b64decode(c)) for c in info["cert"]["certificates"]
            ]
            stato_chiave = info.get("key", {}).get("status", "enabled")
            stato_cert = info.get("cert", {}).get("status", "valid")
        except (KeyError, TypeError, ValueError) as exc:
            raise ErroreFornitore(
                "credentials/info senza certificati leggibili", {"servizio": "csc"}
            ) from exc
        if not certificati:
            raise ErroreFornitore("credentials/info senza certificati", {"servizio": "csc"})
        if stato_chiave != "enabled" or stato_cert != "valid":
            raise ErroreFornitore(
                "credenziale di firma non utilizzabile",
                {"servizio": "csc", "chiave": stato_chiave, "certificato": stato_cert},
            )
        self._auth_mode = str(info.get("authMode", "explicit"))
        self._scal = str(info.get("SCAL", "2"))
        otp = info.get("OTP") if isinstance(info.get("OTP"), dict) else {}
        pin = info.get("PIN") if isinstance(info.get("PIN"), dict) else {}
        self._otp_presenza = str(otp.get("presence", "none"))
        self._otp_tipo = str(otp.get("type", "offline"))
        self._pin_presenza = str(pin.get("presence", "none"))
        modo = self._auth_mode if self._modo == "auto" else self._modo
        if modo == "oauth2code":
            raise ErroreFornitore(
                "la credenziale richiede l'autorizzazione interattiva (oauth2code): non è "
                "usabile per il sigillo automatico", {"servizio": "csc"})
        self.info = {"authMode": self._auth_mode, "SCAL": self._scal,
                     "PIN": self._pin_presenza, "OTP": self._otp_presenza,
                     "OTP_tipo": self._otp_tipo, "credenziale": credenziale}
        # revisione R13a (4): la credenziale deve portare il certificato atteso
        self.identita.controlla(certificati[0])
        pub = certificati[0].public_key
        self._tipo_chiave = "ec" if pub.algorithm == "ec" else "rsa"
        self._bit_chiave = pub.bit_size
        _, meccanismo = ALGO_FIRMA[(self._tipo_chiave, "sha256")]
        self._materiale = MaterialeFirma(
            certificato=certificati[0],
            catena=certificati[1:],
            meccanismo=algos.SignedDigestAlgorithm({"algorithm": meccanismo}),
        )
        return self._materiale

    def meccanismo(self, algoritmo_digest: str) -> algos.SignedDigestAlgorithm:
        return algos.SignedDigestAlgorithm(
            {"algorithm": ALGO_FIRMA[(self._tipo_chiave, algoritmo_digest)][1]})

    def _automatico_possibile(self) -> None:
        """Il sigillo automatico non può chiedere un OTP online a una persona."""
        if self._otp_presenza in ("true", "required") and not self._otp:
            raise ErroreFornitore(
                "la credenziale chiede un OTP " + ("online" if self._otp_tipo == "online" else "")
                + " e nel Secret non c'è un OTP per il sigillo automatico",
                {"servizio": "csc", "motivo": "otp_richiesto"})
        if self._pin_presenza in ("true", "required") and not self._pin:
            raise ErroreFornitore("la credenziale chiede il PIN e nel Secret non c'è",
                                  {"servizio": "csc", "motivo": "pin_richiesto"})

    async def _autorizza(self, impronte: list[bytes], oid_hash: str, *, pin: str, otp: str,
                         num: int) -> _Sad:
        richiesta: dict[str, Any] = {"credentialID": await self._credenziale(),
                                     "numSignatures": num, "hashAlgorithmOID": oid_hash}
        if impronte:
            richiesta["hashes"] = [_b64(i) for i in impronte]
        if pin:
            richiesta["PIN"] = pin
        if otp:
            richiesta["OTP"] = otp
        # con un OTP (si consuma) l'autorizzazione non si ritenta a metà
        risposta = await self._cliente.chiama("credentials/authorize", richiesta,
                                              idempotente=not otp)
        sad = risposta.get("SAD")
        if not sad:
            raise ErroreFornitore("SAD assente nella risposta di autorizzazione",
                                  {"servizio": "csc"})
        try:
            durata = int(risposta.get("expiresIn", 3600))
        except (TypeError, ValueError):
            durata = 3600
        return _Sad(str(sad), time.monotonic() + max(durata - 5, 1), num)

    async def _sign_hash(self, sad: str, impronte: list[bytes], algoritmo_digest: str
                         ) -> list[bytes]:
        oid_hash = OID_HASH[algoritmo_digest]
        sign_algo = self._sign_algo_oid or ALGO_FIRMA[(self._tipo_chiave, algoritmo_digest)][0]
        firmato = await self._cliente.chiama(
            "signatures/signHash",
            {"credentialID": await self._credenziale(), "SAD": sad,
             "hashes": [_b64(i) for i in impronte], "hashAlgorithmOID": oid_hash,
             "signAlgo": sign_algo},
        )
        try:
            valori = [base64.b64decode(v) for v in firmato["signatures"]]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ErroreFornitore("risposta signHash senza firma", {"servizio": "csc"}) from exc
        if len(valori) != len(impronte):
            raise ErroreFornitore("signHash: numero di firme diverso dalle impronte",
                                  {"servizio": "csc"})
        if self._tipo_chiave == "ec":
            valori = [_ecdsa_der(v, self._bit_chiave) for v in valori]
        return valori

    async def firma_impronte(self, impronte: list[bytes], algoritmo_digest: str) -> list[bytes]:
        """Firma massiva senza interazione (sigillo automatico)."""
        await self.materiale()
        if algoritmo_digest not in OID_HASH:
            raise ErroreFornitore(f"digest non supportato: {algoritmo_digest}",
                                  {"servizio": "csc"})
        self._automatico_possibile()
        oid_hash = OID_HASH[algoritmo_digest]
        modo = self._auth_mode if self._modo == "auto" else self._modo
        riuso = self._scal == "1" and self._per_autorizzazione > 1 and not self._otp
        if riuso:
            sad = self._sad
            if sad is None or sad.scade <= time.monotonic() or sad.restanti < len(impronte):
                sad = await self._autorizza(
                    [], oid_hash, pin=self._pin if modo == "explicit" else "", otp="",
                    num=max(self._per_autorizzazione, len(impronte)))
            sad.restanti -= len(impronte)
            self._sad = sad
            return await self._sign_hash(sad.valore, impronte, algoritmo_digest)
        sad = await self._autorizza(
            impronte, oid_hash, pin=self._pin if modo == "explicit" else "",
            otp=self._otp if modo == "explicit" else "", num=len(impronte))
        return await self._sign_hash(sad.valore, impronte, algoritmo_digest)

    async def firma_impronta(self, impronta: bytes, algoritmo_digest: str) -> bytes:
        return (await self.firma_impronte([impronta], algoritmo_digest))[0]

    # ── firma di una persona con OTP (responsabile della conservazione) ──

    async def invia_otp(self) -> None:
        """Chiede al prestatore l'invio dell'OTP al titolare (credentials/sendOTP)."""
        await self._cliente.chiama("credentials/sendOTP",
                                   {"credentialID": await self._credenziale()},
                                   idempotente=False)

    async def firma_impronta_con_otp(self, impronta: bytes, algoritmo_digest: str, *,
                                     otp: str, pin: str = "") -> bytes:
        """Firma interattiva: OTP (e PIN) forniti dalla persona per QUESTA firma."""
        await self.materiale()
        if not otp:
            raise ErroreFornitore("OTP mancante", {"servizio": "csc", "motivo": "otp_richiesto"})
        sad = await self._autorizza([impronta], OID_HASH[algoritmo_digest], pin=pin, otp=otp,
                                    num=1)
        return (await self._sign_hash(sad.valore, [impronta], algoritmo_digest))[0]
