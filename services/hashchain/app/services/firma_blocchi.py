# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Firma e marca dei blocchi della catena tramite il servizio ``signature``.

Servizio: hashchain. Riferimento: VD-C-019 (R15), REGOLE_SVILUPPO §20.2, §20.3, §12.11.

Il blocco si sigilla prima (commit), poi si firma (effetto esterno dopo il
commit, ripresa idempotente): la colonna ``stato_firma`` parte da
``DA_FIRMARE`` e passa a uno di questi stati definitivi:
  - ``FIRMATO``      firma/marca restituite da un servizio qualificato;
  - ``NON_FIRMATO``  il servizio dichiara di non essere qualificato, oppure
                     non è configurato: il motivo si registra in chiaro
                     («non firmato: servizio di firma non qualificato»).
Un errore temporaneo (servizio irraggiungibile, risposta inattesa) lascia il
blocco ``DA_FIRMARE``: la ripresa periodica ritenta.

Contratto usato del servizio signature (R03, VD-C-010), sempre con
Authorization: Bearer <token di servizio>. Revisione di sicurezza M2: il token
è un TOKEN DI SERVIZIO (audience «verumdoc-signature», scope «firma:servizio»,
nessun tenant né ruolo), emesso con
``python -m libs.shared.security.emetti_token_servizio --servizio hashchain``
e salvato nel Secret ``SIGNATURE_SERVICE_TOKEN``; scade (massimo 90 giorni).
Credenziali rifiutate (401/403, es. token scaduto o non ancora ruotato) sono un
esito TRANSITORIO: il blocco resta DA_FIRMARE e si firma appena il token è
rinnovato (prima diventava NON_FIRMATO per sempre).
  GET  /api/v1/signature/status -> {"firma": {"qualificata": bool, ...}, "marca": {...}}
  POST /api/v1/signature/firma  {"content_b64", "formato": "cades_detached", "con_marca": true}
       -> {"busta_b64", "qualificata", "data_marca", "fornitore_firma", ...}
Una firma restituita con ``qualificata = false`` NON vale come firma del
blocco: il blocco resta NON_FIRMATO con il motivo esplicito.

R13a: la stessa interfaccia firma i documenti del conservatore (IPdA, RdV,
manuale, piano: ``formato`` «cades», livello dai parametri del servizio di
firma) e restituisce i dati per la rimarcatura (certificato, scadenze,
algoritmo); ``marca`` chiede la sola marca RFC 3161 (ancoraggio dei
checkpoint), ``rimarca`` una nuova marca d'archivio. Una risposta
``FIRMA-503-NON-CONFIGURATA`` è un esito (non firmato), ogni altro errore del
servizio (``FIRMA-503-CONFIGURAZIONE``, 5xx, rete) è transitorio.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
import structlog

logger = structlog.get_logger(__name__)

STATO_DA_FIRMARE = "DA_FIRMARE"
STATO_FIRMATO = "FIRMATO"
STATO_NON_FIRMATO = "NON_FIRMATO"
STATO_DA_RITENTARE = "DA_RITENTARE"  # esito transitorio, non si scrive sul blocco

MOTIVO_NON_QUALIFICATO = "non firmato: servizio di firma non qualificato"
MOTIVO_NON_CONFIGURATO = "non firmato: servizio di firma non configurato"
MOTIVO_SENZA_CREDENZIALI = (
    "non firmato: credenziali per il servizio di firma non configurate"
)
MOTIVO_CREDENZIALI_RIFIUTATE = (
    "non firmato: credenziali rifiutate dal servizio di firma"
)


@dataclass(frozen=True)
class EsitoFirmaBlocco:
    """Esito della richiesta di firma dell'impronta di un blocco."""
    stato: str
    motivo: str | None = None
    firma_b64: str | None = None
    tsa_token: str | None = None
    tsa_timestamp: datetime | None = None
    #: R13a: dati della firma (rimarcatura e registro dei documenti firmati)
    dettagli: dict | None = None

    @property
    def definitivo(self) -> bool:
        return self.stato in (STATO_FIRMATO, STATO_NON_FIRMATO)


class ServizioFirmaBlocchi(Protocol):
    """Interfaccia: firma l'impronta di un blocco (32 byte SHA-256)."""

    async def firma(self, contenuto: bytes) -> EsitoFirmaBlocco: ...


STATO_MARCATO = "MARCATO"
STATO_NON_MARCATO = "NON_MARCATO"
MOTIVO_MARCA_NON_CONFIGURATA = "non marcato: marca temporale non configurata"
MOTIVO_MARCA_SVILUPPO = "non marcato: marca di sviluppo, non qualificata"


@dataclass(frozen=True)
class EsitoMarca:
    """R13a: esito della marca RFC 3161 di un'impronta."""
    stato: str
    motivo: str | None = None
    token_b64: str | None = None
    istante: datetime | None = None
    tsa: str | None = None

    @property
    def definitivo(self) -> bool:
        return self.stato in (STATO_MARCATO, STATO_NON_MARCATO)


def _codice_errore(risposta: httpx.Response) -> str:
    try:
        corpo = risposta.json()
    except ValueError:
        return ""
    return str(corpo.get("codice") or "") if isinstance(corpo, dict) else ""


def _qualificato(stato: dict) -> bool:
    firma = stato.get("firma")
    if isinstance(firma, dict):
        return firma.get("qualificata") is True
    return stato.get("qualificata") is True


def _data(valore: object) -> datetime | None:
    if not isinstance(valore, str) or not valore:
        return None
    try:
        return datetime.fromisoformat(valore)
    except ValueError:
        return None


def _credenziali_rifiutate(stato_http: int) -> EsitoFirmaBlocco:
    """Token di servizio rifiutato: errore da segnalare, ma si ritenta."""
    logger.error("firma_blocco_credenziali_rifiutate", stato_http=stato_http,
                 azione="rinnovare SIGNATURE_SERVICE_TOKEN (emetti_token_servizio)")
    return EsitoFirmaBlocco(STATO_DA_RITENTARE, MOTIVO_CREDENZIALI_RIFIUTATE)


class ClientServizioSignature:
    """Client HTTP verso il servizio ``signature``."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport

    def _senza_configurazione(self) -> EsitoFirmaBlocco | None:
        if not self._base_url:
            return EsitoFirmaBlocco(STATO_NON_FIRMATO, MOTIVO_NON_CONFIGURATO)
        if not self._token:
            return EsitoFirmaBlocco(STATO_NON_FIRMATO, MOTIVO_SENZA_CREDENZIALI)
        return None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout,
                                 transport=self._transport,
                                 headers={"Authorization": f"Bearer {self._token}"})

    async def qualificato(self) -> bool:
        """R14 (VD-C-165): ``True`` solo se il servizio di firma dichiara un fornitore
        qualificato attivo; qualunque errore vale ``False`` (nessun lavoro avviato)."""
        if self._senza_configurazione() is not None:
            return False
        try:
            async with self._client() as client:
                risposta = await client.get("/api/v1/signature/status")
                if risposta.status_code != 200:
                    return False
                return _qualificato(risposta.json())
        except (httpx.HTTPError, ValueError):
            return False

    async def firma(self, contenuto: bytes, *, formato: str = "cades_detached",
                    livello: str | None = None) -> EsitoFirmaBlocco:
        """Firma qualificata di ``contenuto``. Blocchi e checkpoint: CAdES staccata con
        marca (B-T); documenti del conservatore: ``formato="cades"`` e livello dai
        parametri del servizio di firma (``livello=None``)."""
        assente = self._senza_configurazione()
        if assente is not None:
            return assente
        corpo_richiesta: dict = {"content_b64": base64.b64encode(contenuto).decode(),
                                 "formato": formato, "con_marca": True}
        if livello is None and formato == "cades_detached":
            livello = "B-T"  # blocchi e checkpoint: come prima di R13a
        if livello is not None:
            corpo_richiesta["livello"] = livello
        try:
            async with self._client() as client:
                risposta = await client.get("/api/v1/signature/status")
                if risposta.status_code in (401, 403):
                    return _credenziali_rifiutate(risposta.status_code)
                risposta.raise_for_status()
                if not _qualificato(risposta.json()):
                    return EsitoFirmaBlocco(STATO_NON_FIRMATO, MOTIVO_NON_QUALIFICATO)
                risposta = await client.post("/api/v1/signature/firma", json=corpo_richiesta)
                if risposta.status_code in (401, 403):
                    return _credenziali_rifiutate(risposta.status_code)
                if risposta.status_code == 503 and \
                        _codice_errore(risposta) == "FIRMA-503-NON-CONFIGURATA":
                    return EsitoFirmaBlocco(STATO_NON_FIRMATO, MOTIVO_NON_CONFIGURATO)
                risposta.raise_for_status()
                corpo = risposta.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("firma_blocco_servizio_non_disponibile", errore=str(exc)[:200])
            return EsitoFirmaBlocco(
                STATO_DA_RITENTARE, f"servizio di firma non disponibile: {type(exc).__name__}"
            )
        busta = corpo.get("busta_b64")
        if not busta:
            return EsitoFirmaBlocco(
                STATO_DA_RITENTARE, "risposta del servizio di firma senza busta")
        if corpo.get("qualificata") is not True:
            # Firma prodotta ma non qualificata (es. adattatore di sviluppo): non vale
            return EsitoFirmaBlocco(STATO_NON_FIRMATO, MOTIVO_NON_QUALIFICATO)
        return EsitoFirmaBlocco(
            STATO_FIRMATO,
            None,
            firma_b64=busta,
            tsa_token=None,  # la marca RFC 3161 è dentro la busta CAdES (livello B-T)
            tsa_timestamp=_data(corpo.get("data_marca")),
            dettagli={k: corpo.get(k) for k in (
                "livello", "formato", "fornitore_firma", "fornitore_marca", "codice_fornitore",
                "algoritmo", "certificato_sha256", "certificato_scadenza", "marca_scadenza",
                "firmatario", "verificata")},
        )

    async def verifica(self, busta: bytes, contenuto: bytes | None = None) -> dict:
        """R16 (VD-C-166): verifica crittografica di una busta (radici del prestatore).
        Solleva ``httpx.HTTPError`` se il servizio non risponde (chi chiama ritenta)."""
        if self._senza_configurazione() is not None:
            raise httpx.HTTPError("servizio di firma non configurato")
        corpo: dict = {"busta_b64": base64.b64encode(busta).decode()}
        if contenuto is not None:
            corpo["contenuto_b64"] = base64.b64encode(contenuto).decode()
        async with self._client() as client:
            risposta = await client.post("/api/v1/signature/verifica", json=corpo)
            risposta.raise_for_status()
            esito = risposta.json()
        if not isinstance(esito, dict):
            raise httpx.HTTPError("risposta di verifica non valida")
        return esito

    async def marca(self, impronta: bytes) -> EsitoMarca:
        """R13a: marca RFC 3161 qualificata di un'impronta SHA-256 (ancoraggio)."""
        if not self._base_url or not self._token:
            return EsitoMarca(STATO_NON_MARCATO, MOTIVO_MARCA_NON_CONFIGURATA)
        try:
            async with self._client() as client:
                risposta = await client.post("/api/v1/signature/marca",
                                             json={"impronta_sha256_hex": impronta.hex()})
                if risposta.status_code in (401, 403):
                    _credenziali_rifiutate(risposta.status_code)
                    return EsitoMarca(STATO_DA_RITENTARE, MOTIVO_CREDENZIALI_RIFIUTATE)
                if risposta.status_code == 503 and \
                        _codice_errore(risposta) == "FIRMA-503-NON-CONFIGURATA":
                    return EsitoMarca(STATO_NON_MARCATO, MOTIVO_MARCA_NON_CONFIGURATA)
                risposta.raise_for_status()
                corpo = risposta.json()
        except (httpx.HTTPError, ValueError) as exc:
            return EsitoMarca(STATO_DA_RITENTARE,
                              f"servizio di firma non disponibile: {type(exc).__name__}")
        verifica = corpo.get("verifica") or {}
        if not corpo.get("token_b64") or verifica.get("valida") is not True:
            return EsitoMarca(STATO_DA_RITENTARE, "marca assente o non valida nella risposta")
        if verifica.get("di_sviluppo"):
            return EsitoMarca(STATO_NON_MARCATO, MOTIVO_MARCA_SVILUPPO)
        return EsitoMarca(STATO_MARCATO, None, token_b64=str(corpo["token_b64"]),
                          istante=_data(verifica.get("data_marca")),
                          tsa=str(corpo.get("fornitore_marca") or "")[:200] or None)

    async def rimarca(self, busta: bytes, contenuto: bytes | None = None) -> EsitoFirmaBlocco:
        """R13a: nuova marca d'archivio (B-LTA) su una busta firmata."""
        assente = self._senza_configurazione()
        if assente is not None:
            return EsitoFirmaBlocco(STATO_DA_RITENTARE, assente.motivo)
        corpo_richiesta = {"busta_b64": base64.b64encode(busta).decode()}
        if contenuto is not None:
            corpo_richiesta["contenuto_b64"] = base64.b64encode(contenuto).decode()
        try:
            async with self._client() as client:
                risposta = await client.post("/api/v1/signature/rimarca", json=corpo_richiesta)
                risposta.raise_for_status()
                corpo = risposta.json()
        except (httpx.HTTPError, ValueError) as exc:
            return EsitoFirmaBlocco(STATO_DA_RITENTARE,
                                    f"rimarcatura non riuscita: {type(exc).__name__}")
        if not corpo.get("busta_b64") or corpo.get("verificata") is not True:
            return EsitoFirmaBlocco(STATO_DA_RITENTARE, "busta rimarcata assente o non verificata")
        return EsitoFirmaBlocco(STATO_FIRMATO, None, firma_b64=str(corpo["busta_b64"]),
                                tsa_timestamp=_data(corpo.get("data_marca")),
                                dettagli={k: corpo.get(k) for k in (
                                    "livello", "algoritmo", "marca_scadenza",
                                    "certificato_scadenza", "fornitore_marca")})


def servizio_firma_predefinito() -> ServizioFirmaBlocchi:
    """Client configurato dalle impostazioni del servizio."""
    from app.config import settings

    return ClientServizioSignature(
        settings.signature_url,
        settings.signature_service_token.get_secret_value(),
        settings.signature_timeout_seconds,
    )
