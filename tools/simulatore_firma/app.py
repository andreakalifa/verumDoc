# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Simulatore locale di un prestatore di firma remota (Cloud Signature Consortium API
v2) e di una TSA RFC 3161 — R13a, SOLO collaudo e test (mai in esercizio).

Rotte (prefisso CSC ``/csc/v2``):
  POST /oauth2/token                      client_credentials (client_id/segreto)
  POST /csc/v2/info                       descrizione del servizio
  POST /csc/v2/credentials/list           elenco delle credenziali
  POST /csc/v2/credentials/info           certificato, catena, authMode, SCAL, PIN, OTP
  POST /csc/v2/credentials/authorize      SAD (PIN, OTP; con SCAL 2 legato alle impronte)
  POST /csc/v2/credentials/sendOTP        «invia» l'OTP (fisso per il sigillo; per le
                                          credenziali personali di R16 un OTP nuovo e
                                          monouso, leggibile dai test in ``otp_inviati``)
  POST /csc/v2/signatures/signHash        firma delle impronte con il SAD
  POST /tsa                               marca RFC 3161 (application/timestamp-query)
  GET  /crl/ca.crl                        CRL della CA del simulatore
  POST /ocsp                              risposta OCSP firmata dalla CA

Guasti iniettabili per le prove di ripiego e ritentativi (``Simulatore.guasti``):
``{"signHash": [503, 503]}`` fa rispondere 503 alle prossime due chiamate,
``{"tsa": ["lenta"]}`` simula la lentezza, ``{"tsa": ["impronta_errata"]}`` una
marca su un'impronta diversa (la verifica del cliente deve scartarla).
"""
from __future__ import annotations

import asyncio
import base64
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from asn1crypto import tsp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from fastapi import FastAPI, Form, Header, Request, Response
from fastapi.responses import JSONResponse

from tools.simulatore_firma.pki import PKISimulatore, genera

_HASH = {"2.16.840.1.101.3.4.2.1": hashes.SHA256, "2.16.840.1.101.3.4.2.2": hashes.SHA384,
         "2.16.840.1.101.3.4.2.3": hashes.SHA512}
_SIGN_ALGO = {"1.2.840.113549.1.1.11", "1.2.840.113549.1.1.12", "1.2.840.113549.1.1.13",
              "1.2.840.10045.4.3.2", "1.2.840.10045.4.3.3", "1.2.840.10045.4.3.4"}


@dataclass
class CredenzialePersonale:
    """R16 (VD-C-166): credenziale di firma PERSONALE (OTP online monouso, PIN facoltativo)."""

    chiave: Any
    certificato: Any
    pin: str = ""
    otp_corrente: str | None = None


@dataclass
class Simulatore:
    """Stato del simulatore (configurazione, PKI, SAD emessi, guasti)."""

    url_pubblico: str = "http://simulatore.locale"
    client_id: str = "vd-prova"
    client_secret: str = "segreto-di-prova"
    credenziale: str = "sigillo-prova-1"
    credenziali_extra: list[str] = field(default_factory=list)
    pin: str = "1234"
    otp: str = "123456"
    scal: str = "2"
    auth_mode: str = "explicit"
    otp_presenza: str = "none"
    otp_tipo: str = "offline"
    qualificato: bool = True
    chiave: str = "rsa"
    politica_tsa: str = "1.3.6.1.4.1.99999.1.1"
    pki: PKISimulatore | None = None
    token: dict[str, float] = field(default_factory=dict)
    sad: dict[str, dict[str, Any]] = field(default_factory=dict)
    guasti: dict[str, list[Any]] = field(default_factory=dict)
    chiamate: list[str] = field(default_factory=list)
    #: R16: credenziali personali (credentialID → credenziale) e OTP «inviati»
    personali: dict[str, CredenzialePersonale] = field(default_factory=dict)
    otp_inviati: dict[str, list[str]] = field(default_factory=dict)
    #: collaudo a mano (``--personale``): l'OTP «inviato» si stampa nel terminale del simulatore
    stampa_otp: bool = False

    def aggiungi_personale(self, credenziale: str, cn: str, *, pin: str = "") -> str:
        """Crea una credenziale personale; restituisce l'impronta SHA-256 del certificato."""
        import hashlib

        from cryptography.hazmat.primitives import serialization

        assert self.pki is not None
        chiave, cert = self.pki.emetti_personale(cn)
        self.personali[credenziale] = CredenzialePersonale(chiave, cert, pin)
        return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()

    def __post_init__(self) -> None:
        if self.pki is None:
            self.pki = genera(self.url_pubblico, qualificato=self.qualificato,
                              chiave=self.chiave)

    def guasto(self, operazione: str) -> Any:
        coda = self.guasti.get(operazione) or []
        return coda.pop(0) if coda else None


def _errore(stato: int, codice: str, descrizione: str) -> JSONResponse:
    return JSONResponse({"error": codice, "error_description": descrizione}, status_code=stato)


def crea_app(sim: Simulatore | None = None) -> FastAPI:
    sim = sim or Simulatore()
    app = FastAPI(title="Simulatore CSC v2 e TSA (solo collaudo)", docs_url=None, redoc_url=None)
    app.state.simulatore = sim
    pki = sim.pki
    assert pki is not None

    def autorizzato(authorization: str | None) -> bool:
        if not authorization or not authorization.startswith("Bearer "):
            return False
        scade = sim.token.get(authorization[7:])
        return scade is not None and scade > time.monotonic()

    @app.post("/oauth2/token")
    async def token(grant_type: str = Form(...), client_id: str = Form(""),
                    client_secret: str = Form("")) -> JSONResponse:
        sim.chiamate.append("oauth2/token")
        if grant_type != "client_credentials":
            return _errore(400, "unsupported_grant_type", "solo client_credentials")
        if client_id != sim.client_id or not secrets.compare_digest(client_secret,
                                                                     sim.client_secret):
            return _errore(401, "invalid_client", "credenziali del client errate")
        valore = secrets.token_urlsafe(24)
        sim.token[valore] = time.monotonic() + 3600
        return JSONResponse({"access_token": valore, "token_type": "Bearer",
                             "expires_in": 3600})

    async def corpo_csc(request: Request, operazione: str,
                        authorization: str | None) -> dict | JSONResponse:
        sim.chiamate.append(operazione)
        guasto = sim.guasto(operazione)
        if isinstance(guasto, int):
            return _errore(guasto, "temporarily_unavailable", "guasto simulato")
        if not autorizzato(authorization):
            return _errore(401, "invalid_token", "token mancante o scaduto")
        try:
            dati = await request.json()
        except ValueError:
            return _errore(400, "invalid_request", "JSON non valido")
        return dati if isinstance(dati, dict) else _errore(400, "invalid_request", "oggetto")

    @app.post("/csc/v2/info")
    async def info() -> dict:
        return {"specs": "2.0.0.2", "name": "Simulatore VerumDoc", "region": "IT",
                "lang": "it-IT", "authType": ["oauth2client"],
                "methods": ["credentials/list", "credentials/info", "credentials/authorize",
                            "credentials/sendOTP", "signatures/signHash"]}

    @app.post("/csc/v2/credentials/list")
    async def elenco(request: Request, authorization: str | None = Header(None)) -> Any:
        dati = await corpo_csc(request, "credentials/list", authorization)
        if isinstance(dati, JSONResponse):
            return dati
        return {"credentialIDs": [sim.credenziale, *sim.credenziali_extra, *sim.personali]}

    @app.post("/csc/v2/credentials/info")
    async def info_credenziale(request: Request,
                               authorization: str | None = Header(None)) -> Any:
        dati = await corpo_csc(request, "credentials/info", authorization)
        if isinstance(dati, JSONResponse):
            return dati
        personale = sim.personali.get(str(dati.get("credentialID")))
        if personale is not None:
            return {
                "key": {"status": "enabled", "algo": ["1.2.840.113549.1.1.1"], "len": 2048},
                "cert": {"status": "valid", "certificates": [
                    base64.b64encode(pki.der(personale.certificato)).decode(),
                    base64.b64encode(pki.der(pki.ca)).decode()]},
                "authMode": "explicit", "SCAL": "2", "multisign": 1,
                "PIN": {"presence": "true" if personale.pin else "false", "format": "N"},
                "OTP": {"presence": "true", "type": "online"},
            }
        if dati.get("credentialID") != sim.credenziale:
            return _errore(400, "invalid_request", "credenziale sconosciuta")
        catena = [pki.der(pki.firma), pki.der(pki.ca)]
        return {
            "key": {"status": "enabled", "algo": ["1.2.840.10045.2.1" if sim.chiave == "ec"
                                                  else "1.2.840.113549.1.1.1"],
                    "len": 256 if sim.chiave == "ec" else 2048},
            "cert": {"status": "valid",
                     "certificates": [base64.b64encode(c).decode() for c in catena]},
            "authMode": sim.auth_mode, "SCAL": sim.scal, "multisign": 100,
            "PIN": {"presence": "true" if sim.pin else "false", "format": "N"},
            "OTP": {"presence": sim.otp_presenza, "type": sim.otp_tipo},
        }

    @app.post("/csc/v2/credentials/sendOTP")
    async def invia_otp(request: Request, authorization: str | None = Header(None)) -> Any:
        dati = await corpo_csc(request, "credentials/sendOTP", authorization)
        if isinstance(dati, JSONResponse):
            return dati
        personale = sim.personali.get(str(dati.get("credentialID")))
        if personale is not None:
            personale.otp_corrente = f"{secrets.randbelow(10**6):06d}"
            sim.otp_inviati.setdefault(str(dati["credentialID"]), []).append(
                personale.otp_corrente)
            if sim.stampa_otp:
                print(f"OTP per {dati['credentialID']}: {personale.otp_corrente}",  # noqa: T201
                      flush=True)
        return Response(status_code=200)

    @app.post("/csc/v2/credentials/authorize")
    async def autorizza(request: Request, authorization: str | None = Header(None)) -> Any:
        dati = await corpo_csc(request, "credentials/authorize", authorization)
        if isinstance(dati, JSONResponse):
            return dati
        personale = sim.personali.get(str(dati.get("credentialID")))
        if personale is not None:
            # OTP online monouso: vale una volta sola, poi serve un nuovo sendOTP
            atteso, personale.otp_corrente = personale.otp_corrente, None
            if personale.pin and dati.get("PIN") != personale.pin:
                return _errore(400, "invalid_pin", "PIN errato")
            if not atteso or not secrets.compare_digest(str(dati.get("OTP") or ""), atteso):
                return _errore(400, "invalid_otp", "OTP errato, già usato o mancante")
            impronte = list(dati.get("hashes") or [])
            if len(impronte) != 1 or int(dati.get("numSignatures") or 1) != 1:
                return _errore(400, "invalid_request", "firma personale: una impronta")
            valore = secrets.token_urlsafe(16)
            sim.sad[valore] = {"impronte": set(impronte), "restanti": 1,
                               "scade": time.monotonic() + 300,
                               "credenziale": str(dati["credentialID"])}
            return {"SAD": valore, "expiresIn": 300}
        if dati.get("credentialID") != sim.credenziale:
            return _errore(400, "invalid_request", "credenziale sconosciuta")
        if sim.auth_mode == "explicit" and sim.pin and dati.get("PIN") != sim.pin:
            return _errore(400, "invalid_pin", "PIN errato")
        if sim.otp_presenza in ("true", "required") and dati.get("OTP") != sim.otp:
            return _errore(400, "invalid_otp", "OTP errato o mancante")
        impronte = list(dati.get("hashes") or [])
        if sim.scal == "2" and not impronte:
            return _errore(400, "invalid_request", "SCAL 2: impronte obbligatorie")
        num = int(dati.get("numSignatures") or 1)
        if impronte and len(impronte) != num:
            return _errore(400, "invalid_request", "numSignatures diverso dalle impronte")
        valore = secrets.token_urlsafe(16)
        sim.sad[valore] = {"impronte": set(impronte), "restanti": num,
                           "scade": time.monotonic() + 300}
        return {"SAD": valore, "expiresIn": 300}

    @app.post("/csc/v2/signatures/signHash")
    async def firma(request: Request, authorization: str | None = Header(None)) -> Any:
        dati = await corpo_csc(request, "signatures/signHash", authorization)
        if isinstance(dati, JSONResponse):
            return dati
        sad = sim.sad.get(str(dati.get("SAD")))
        impronte = list(dati.get("hashes") or [])
        if sad is None or sad["scade"] < time.monotonic() or sad["restanti"] < len(impronte):
            return _errore(400, "invalid_request", "SAD non valido, scaduto o esaurito")
        if sad["impronte"] and not set(impronte) <= sad["impronte"]:
            return _errore(400, "invalid_request", "impronte non autorizzate dal SAD")
        algoritmo = _HASH.get(str(dati.get("hashAlgorithmOID")))
        if algoritmo is None or str(dati.get("signAlgo")) not in _SIGN_ALGO:
            return _errore(400, "invalid_request", "algoritmo non supportato")
        if sad.get("credenziale", sim.credenziale) != str(dati.get("credentialID")):
            return _errore(400, "invalid_request", "SAD di un'altra credenziale")
        sad["restanti"] -= len(impronte)
        valori = []
        chiave_firma = (sim.personali[sad["credenziale"]].chiave if sad.get("credenziale")
                        else pki.firma_chiave)
        for h in impronte:
            grezza = base64.b64decode(h)
            if isinstance(chiave_firma, ec.EllipticCurvePrivateKey):
                der = chiave_firma.sign(grezza, ec.ECDSA(utils.Prehashed(algoritmo())))
                r, s = utils.decode_dss_signature(der)
                valori.append(r.to_bytes(32, "big") + s.to_bytes(32, "big"))  # r||s
            else:
                assert isinstance(chiave_firma, rsa.RSAPrivateKey)
                valori.append(chiave_firma.sign(grezza, padding.PKCS1v15(),
                                                    utils.Prehashed(algoritmo())))
        return {"signatures": [base64.b64encode(v).decode() for v in valori]}

    @app.post("/tsa")
    async def tsa(request: Request) -> Response:
        sim.chiamate.append("tsa")
        guasto = sim.guasto("tsa")
        if isinstance(guasto, int):
            return Response(status_code=guasto)
        if guasto == "lenta":
            await asyncio.sleep(5)
        if request.headers.get("content-type") != "application/timestamp-query":
            return Response(status_code=415)
        from asn1crypto import x509 as ax
        from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
        from pyhanko_certvalidator.registry import SimpleCertificateStore

        cert, chiave = pki.tsa_asn1()
        motore = DummyTimeStamper(
            tsa_cert=cert, tsa_key=chiave,
            certs_to_embed=SimpleCertificateStore.from_certs([ax.Certificate.load(
                pki.der(pki.ca))]))
        richiesta = tsp.TimeStampReq.load(await request.body())
        if guasto == "impronta_errata":
            imprint = richiesta["message_imprint"]
            richiesta = tsp.TimeStampReq({
                "version": 1, "cert_req": True, "nonce": richiesta["nonce"],
                "message_imprint": {"hash_algorithm": imprint["hash_algorithm"],
                                    "hashed_message": b"\x00" * len(
                                        imprint["hashed_message"].native)}})
        risposta = motore.request_tsa_response(richiesta)
        return Response(risposta.dump(), media_type="application/timestamp-reply")

    @app.get("/crl/ca.crl")
    async def crl() -> Response:
        sim.chiamate.append("crl")
        guasto = sim.guasto("crl")
        if isinstance(guasto, int):
            return Response(status_code=guasto)
        return Response(pki.crl(), media_type="application/pkix-crl")

    @app.post("/ocsp")
    async def risposta_ocsp(request: Request) -> Response:
        sim.chiamate.append("ocsp")
        guasto = sim.guasto("ocsp")
        if isinstance(guasto, int):
            return Response(status_code=guasto)
        return Response(pki.risposta_ocsp(await request.body()),
                        media_type="application/ocsp-response")

    return app


def configurazione_console(sim: Simulatore) -> dict[str, Any]:
    """Valori da inserire in console per collaudare con il simulatore."""
    assert sim.pki is not None
    base = sim.url_pubblico.rstrip("/")
    import hashlib
    from urllib.parse import urlparse

    from cryptography.hazmat.primitives import serialization

    impronta = hashlib.sha256(sim.pki.firma.public_bytes(serialization.Encoding.DER)).hexdigest()
    return {
        "firma": {"codice": "simulatore-csc", "ruolo": "FIRMA", "tipo": "CSC",
                  "configurazione": {"url_base": f"{base}/csc/v2",
                                     "url_token": f"{base}/oauth2/token",
                                     "client_id": sim.client_id,
                                     "credential_id": sim.credenziale,
                                     "radici_pem": sim.pki.ca_pem,
                                     # identità attesa (revisione R13a (4))
                                     "certificati_attesi_sha256": [impronta]}},
        "marca": {"codice": "simulatore-tsa", "ruolo": "MARCA", "tipo": "RFC3161",
                  "configurazione": {"url": f"{base}/tsa", "radici_pem": sim.pki.ca_pem}},
        # segreti legati all'host (revisione R13a (2)): fissato al deploy nel Secret
        "segreti": {"simulatore-csc": {"host": [urlparse(base).hostname],
                                       "client_secret": sim.client_secret, "pin": sim.pin}},
    }


def app_da_ambiente() -> FastAPI:
    """Applicazione per ``uvicorn`` (variabili ``SIM_*``)."""
    return crea_app(Simulatore(
        url_pubblico=os.environ.get("SIM_URL_PUBBLICO", "http://127.0.0.1:8099"),
        scal=os.environ.get("SIM_SCAL", "2"),
        qualificato=os.environ.get("SIM_QUALIFICATO", "true").lower() == "true",
        chiave=os.environ.get("SIM_CHIAVE", "rsa")))


__all__ = ["Simulatore", "app_da_ambiente", "configurazione_console", "crea_app"]
