# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/tsa_client.py
Client RFC 3161 (Time-Stamp Protocol su HTTP) — VD-C-010, REGOLE §20.1/§20.3.

Un solo client per qualunque TSA: si configura con URL, credenziali (Basic),
politica attesa e radici attendibili. Il trasporto httpx è iniettabile: i test
usano httpx.MockTransport con una TSA simulata, mai la rete.

Ogni token ricevuto viene verificato prima di essere restituito:
  - stato della risposta «granted»/«grantedWithMods»;
  - nonce uguale a quello inviato (anti-replay);
  - impronta nel TSTInfo uguale a quella richiesta;
  - politica uguale a quella configurata (se configurata);
  - firma del token crittograficamente valida e certificato con EKU
    timeStamping; catena verso le radici configurate se presenti.

R13a: istante della marca entro la tolleranza dall'orologio del servizio
(``tolleranza_secondi``), impronta SHA-256 del certificato della TSA se
configurata, ritentativi idempotenti e pinning come gli altri fornitori
(``http_sicuro``), più TSA in ripiego (``MarcaConRipiego``): una TSA non
raggiungibile O con una risposta che non supera la verifica si scarta (errore
nel log) e si passa alla successiva; nessuna risposta non verificata è mai usata.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx
import structlog
from asn1crypto import algos, cms, core, tsp, x509
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.validation.generic_cms import validate_tst_signed_data
from pyhanko_certvalidator import ValidationContext

from app.services.fornitori import (
    OID_QTST_STATEMENT,
    ErroreFornitore,
    FornitoreMarca,
)
from app.services.http_sicuro import ClienteHttpSicuro

logger = structlog.get_logger(__name__)

TIPO_RICHIESTA = "application/timestamp-query"
TIPO_RISPOSTA = "application/timestamp-reply"


def contesto_validazione(
    radici: list[x509.Certificate], momento: datetime | None = None
) -> ValidationContext:
    """Contesto di validazione senza accesso alla rete (revoca in soft-fail).

    La verifica OCSP/CRL e la Trusted List europea non sono ancora integrate:
    lo stato del servizio lo dichiara.
    """
    return ValidationContext(
        trust_roots=radici,
        allow_fetching=False,
        revocation_mode="soft-fail",
        moment=momento,
    )


async def verifica_token(
    token: cms.ContentInfo,
    impronta_attesa: bytes,
    radici: list[x509.Certificate],
) -> dict:
    """Verifica crittografica di un TimeStampToken rispetto a un'impronta.

    Restituisce un esito strutturato; non solleva per token non validi.
    """
    try:
        if token["content_type"].native != "signed_data":
            return {"valida": False, "motivo": "il token non è un SignedData"}
        sd = token["content"]
        tst_info = sd["encap_content_info"]["content"].parsed
        if not isinstance(tst_info, tsp.TSTInfo):
            return {"valida": False, "motivo": "il token non contiene un TSTInfo"}
        algoritmo = tst_info["message_imprint"]["hash_algorithm"]["algorithm"].native
        stato = await validate_tst_signed_data(
            sd,
            contesto_validazione(radici),
            lambda _alg: impronta_attesa,
        )
    except Exception as exc:  # noqa: BLE001 — qualunque errore di formato = token non valido
        logger.info("marca_non_verificabile", errore=type(exc).__name__)
        return {"valida": False, "motivo": f"token non leggibile: {type(exc).__name__}"}
    integra = bool(stato.get("intact"))
    firma_ok = bool(stato.get("valid"))
    # pyHanko: catena attendibile = percorso costruito e nessun problema di fiducia
    attendibile = (
        stato.get("validation_path") is not None and stato.get("trust_problem_indic") is None
    )
    # ETSI EN 319 422 §9.1: la TSA qualificata lo dichiara con l'estensione
    # esi4-qtstStatement-1 nel TSTInfo (dichiarazione, non verifica su Trusted List)
    dichiarata_qualificata = any(
        ext["extn_id"].dotted == OID_QTST_STATEMENT for ext in (tst_info["extensions"] or [])
    )
    cert_tsa = _certificato_tsa(token)
    return {
        "certificato_tsa_sha256": (hashlib.sha256(cert_tsa.dump()).hexdigest()
                                   if cert_tsa is not None else None),
        "certificato_tsa_scadenza": (cert_tsa["tbs_certificate"]["validity"]["not_after"]
                                     .native.isoformat() if cert_tsa is not None else None),
        "valida": integra and firma_ok,
        "integra": integra,
        "firma_valida": firma_ok,
        "attendibile": attendibile,
        "data_marca": stato.get("timestamp").isoformat() if stato.get("timestamp") else None,
        "algoritmo_impronta": algoritmo,
        "politica": tst_info["policy"].dotted,
        "numero_serie": str(tst_info["serial_number"].native),
        "tsa": (tst_info["tsa"].native if tst_info["tsa"] else None),
        "dichiarata_qualificata": dichiarata_qualificata,
        "motivo": None if integra and firma_ok else "impronta o firma del token non valide",
    }


def _certificato_tsa(token: cms.ContentInfo) -> x509.Certificate | None:
    """Certificato che ha firmato il token (sid del SignerInfo)."""
    try:
        sd = token["content"]
        sid = sd["signer_infos"][0]["sid"]
        for scelta in sd["certificates"] or []:
            cert = scelta.chosen
            if not isinstance(cert, x509.Certificate):
                continue
            if sid.name == "issuer_and_serial_number":
                if (cert.issuer == sid.chosen["issuer"]
                        and cert.serial_number == sid.chosen["serial_number"].native):
                    return cert
            elif cert.key_identifier == sid.chosen.native:
                return cert
    except (KeyError, IndexError, ValueError, AttributeError):
        return None
    return None


class ClienteTSA(TimeStamper):
    """Client RFC 3161 su HTTP basato su httpx (trasporto iniettabile)."""

    def __init__(
        self,
        url: str,
        *,
        utente: str = "",
        password: str = "",
        politica_oid: str = "",
        radici: list[x509.Certificate] | None = None,
        timeout: float = 30.0,
        trasporto: httpx.AsyncBaseTransport | None = None,
        ritentativi: int = 1,
        tolleranza_secondi: int = 300,
        certificato_sha256: str = "",
        pin_spki_sha256: tuple[str, ...] = (),
        orologio: object = None,
        host_ammessi: tuple[str, ...] = (),
        solo_https: bool = False,
    ) -> None:
        super().__init__(include_nonce=True)
        if not url:
            raise ValueError("URL della TSA obbligatorio")
        self._url = url
        self._auth = httpx.BasicAuth(utente, password) if utente else None
        self._politica = politica_oid
        self._radici = radici or []
        self._timeout = timeout
        self._trasporto = trasporto
        self._tolleranza = tolleranza_secondi
        self._certificato_sha256 = certificato_sha256.lower()
        self._orologio = orologio or (lambda: datetime.now(UTC))
        self._http = ClienteHttpSicuro("tsa", timeout=timeout, ritentativi=ritentativi,
                                       trasporto=trasporto, pin_spki_sha256=pin_spki_sha256,
                                       host_ammessi=host_ammessi, solo_https=solo_https)

    @property
    def host(self) -> str | None:
        from urllib.parse import urlparse

        return urlparse(self._url).hostname

    def __repr__(self) -> str:  # mai la password
        return f"ClienteTSA(host={self.host!r})"

    def request_cms(self, message_digest, md_algorithm):  # type: ignore[no-untyped-def]
        nonce, req = super().request_cms(message_digest, md_algorithm)
        if self._politica:
            req["req_policy"] = self._politica
        return nonce, req

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        risposta = await self._http.richiesta(
            "POST", self._url, operazione="timestamp", idempotente=True,
            content=req.dump(), auth=self._auth,
            headers={"Content-Type": TIPO_RICHIESTA, "Accept": TIPO_RISPOSTA})
        if risposta.status_code != 200:
            raise ErroreFornitore(
                f"la TSA ha risposto HTTP {risposta.status_code}",
                {"servizio": "tsa", "http": risposta.status_code},
            )
        tipo = risposta.headers.get("content-type", "").split(";")[0].strip()
        if tipo != TIPO_RISPOSTA:
            raise ErroreFornitore(
                "la TSA ha risposto con un tipo di contenuto inatteso",
                {"servizio": "tsa", "content_type": tipo[:100]},
            )
        try:
            resp = tsp.TimeStampResp.load(risposta.content)
            _ = resp["status"].native  # forza il parsing dello stato
        except Exception as exc:  # noqa: BLE001
            # asn1crypto non accetta una TimeStampResp senza token (rifiuto RFC 3161
            # §2.4.2): si legge il solo PKIStatusInfo per riportare il motivo.
            stato = _stato_senza_token(risposta.content)
            if stato is not None:
                raise ErroreFornitore(
                    "la TSA ha rifiutato la richiesta",
                    {
                        "servizio": "tsa",
                        "stato": stato.get("status"),
                        "testo": "; ".join(stato.get("status_string") or []),
                        "fail_info": stato.get("fail_info"),
                    },
                ) from exc
            raise ErroreFornitore(
                "risposta TSA non decodificabile", {"servizio": "tsa"}
            ) from exc
        return resp

    async def async_timestamp(self, message_digest, md_algorithm) -> cms.ContentInfo:  # type: ignore[no-untyped-def]
        nonce, req = self.request_cms(message_digest, md_algorithm)
        resp = await self.async_request_tsa_response(req)
        stato = resp["status"]["status"].native
        if stato not in ("granted", "granted_with_mods"):
            testo = "; ".join(resp["status"]["status_string"].native or [])
            raise ErroreFornitore(
                "la TSA ha rifiutato la richiesta",
                {
                    "servizio": "tsa",
                    "stato": stato,
                    "testo": testo,
                    "fail_info": resp["status"]["fail_info"].native,
                },
            )
        try:
            token = resp["time_stamp_token"]
            tst_info = token["content"]["encap_content_info"]["content"].parsed
            ricevuto_nonce = tst_info["nonce"].native
            imprint = tst_info["message_imprint"]
            ricevuta_impronta = imprint["hashed_message"].native
            ricevuto_alg = imprint["hash_algorithm"]["algorithm"].native
            ricevuta_politica = tst_info["policy"].dotted
            istante = tst_info["gen_time"].native
        except Exception as exc:  # noqa: BLE001 — revisione R13a (6): token malformato
            # → errore del fornitore, così il ripiego passa alla TSA successiva
            raise ErroreFornitore("token di marca non decodificabile",
                                  {"servizio": "tsa", "errore": type(exc).__name__}) from exc
        if ricevuto_nonce != nonce:
            raise ErroreFornitore(
                "nonce della marca diverso da quello inviato", {"servizio": "tsa"}
            )
        if (
            ricevuta_impronta != message_digest
            or ricevuto_alg != md_algorithm
        ):
            raise ErroreFornitore(
                "impronta della marca diversa da quella richiesta", {"servizio": "tsa"}
            )
        if self._politica and ricevuta_politica != self._politica:
            raise ErroreFornitore(
                "politica della TSA diversa da quella configurata",
                {
                    "servizio": "tsa",
                    "attesa": self._politica,
                    "ricevuta": ricevuta_politica,
                },
            )
        scarto = abs((istante - self._orologio()).total_seconds())  # type: ignore[operator]
        if scarto > self._tolleranza:
            raise ErroreFornitore(
                "istante della marca fuori dalla tolleranza rispetto all'orologio del servizio",
                {"servizio": "tsa", "scarto_secondi": int(scarto)})
        esito = await verifica_token(token, message_digest, self._radici)
        if not esito["valida"]:
            raise ErroreFornitore("firma del token di marca non valida", {"servizio": "tsa"})
        if self._radici and not esito["attendibile"]:
            raise ErroreFornitore(
                "certificato della TSA non riconducibile alle radici configurate",
                {"servizio": "tsa"},
            )
        if self._certificato_sha256 and esito.get("certificato_tsa_sha256") != \
                self._certificato_sha256:
            raise ErroreFornitore("certificato della TSA diverso da quello configurato",
                                  {"servizio": "tsa"})
        return token


class FornitoreMarcaRFC3161(FornitoreMarca):
    """Marca da una TSA RFC 3161 remota (qualificata se lo è il servizio scelto)."""

    nome = "rfc3161"
    di_sviluppo = False

    def __init__(self, cliente: ClienteTSA, radici: list[x509.Certificate],
                 codice: str = "") -> None:
        self._cliente = cliente
        self._radici = radici
        self.codice = codice

    @property
    def cliente(self) -> ClienteTSA:
        return self._cliente

    async def marca(self, impronta: bytes, algoritmo: str) -> cms.ContentInfo:
        return await self._cliente.async_timestamp(impronta, algoritmo)

    def radici_attendibili(self) -> list[x509.Certificate]:
        return list(self._radici)


class MarcaConRipiego(FornitoreMarca):
    """R13a: più TSA in ordine di priorità. Si passa alla successiva se una non è
    raggiungibile o se la sua risposta non supera la verifica (mai usata)."""

    nome = "ripiego"

    def __init__(self, fornitori: list[FornitoreMarca]) -> None:
        if not fornitori:
            raise ValueError("almeno una TSA")
        self._fornitori = list(fornitori)
        self.di_sviluppo = any(f.di_sviluppo for f in fornitori)
        self.ultimo: FornitoreMarca | None = None

    @property
    def fornitori(self) -> list[FornitoreMarca]:
        return list(self._fornitori)

    @property
    def codice(self) -> str:  # type: ignore[override]
        return (self.ultimo or self._fornitori[0]).codice

    async def marca(self, impronta: bytes, algoritmo: str) -> cms.ContentInfo:
        errori: list[str] = []
        for f in self._fornitori:
            try:
                token = await f.marca(impronta, algoritmo)
            except ErroreFornitore as exc:
                logger.error("marca_tsa_scartata", tsa=f.codice or f.nome,
                             motivo=exc.messaggio[:200])
                errori.append(f"{f.codice or f.nome}: {exc.messaggio}")
                continue
            self.ultimo = f
            return token
        raise ErroreFornitore("nessuna TSA ha rilasciato una marca valida",
                              {"servizio": "tsa", "errori": errori[:5]})

    def radici_attendibili(self) -> list[x509.Certificate]:
        radici: list[x509.Certificate] = []
        for f in self._fornitori:
            radici += f.radici_attendibili()
        return radici

    async def revoca_locale(self, adesso: object) -> tuple[list[bytes], list[bytes]]:
        crl: list[bytes] = []
        ocsp: list[bytes] = []
        for f in self._fornitori:
            c, o = await f.revoca_locale(adesso)
            crl += c
            ocsp += o
        return crl, ocsp


def _stato_senza_token(der: bytes) -> dict | None:
    """Estrae il PKIStatusInfo da una risposta di rifiuto priva di token."""
    try:
        seq = core.Sequence.load(der)
        stato = tsp.PKIStatusInfo.load(seq[0].dump())
        valore = stato.native
        return valore if isinstance(valore, dict) else None
    except Exception:  # noqa: BLE001 — non è nemmeno un PKIStatusInfo
        return None


def oid_digest(nome: str) -> str:
    """OID dell'algoritmo di digest a partire dal nome asn1crypto."""
    return algos.DigestAlgorithmId(nome).dotted
