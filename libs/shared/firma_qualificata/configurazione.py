# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Configurazione dei fornitori di firma e marca (R13a): tipi, campi, segreti.

Riferimenti: Cloud Signature Consortium API v2 (firma remota), OASIS PKCS#11
(HSM e smart card), RFC 3161/5816 (marca temporale), Reg. UE 910/2014 artt. 26,
36, 42; REGOLE_SVILUPPO §9.1 (una definizione), §19.4 (niente di finto in
produzione), §20.2 (stato onesto).

Ruoli e tipi:
  FIRMA  CSC (firma remota CSC v2), PKCS11 (HSM o smart card), SVILUPPO (chiave
         locale autogenerata, MAI in produzione)
  MARCA  RFC3161 (TSA su HTTP), SVILUPPO (TSA locale, MAI in produzione)

La configurazione (JSON della tabella ``fornitori_firma``) non contiene MAI
segreti: chiavi con «secret», «segreto», «password», «pin», «otp» o «chiave
privata» si rifiutano. I segreti stanno nel Secret ``signature-secrets``, chiave
``FIRMA_SEGRETI_JSON``: un oggetto JSON ``{"<codice>": {"host": ["csc.prestatore.it",
"auth.prestatore.it"], "pin_spki_sha256": [...], "client_secret": …, "pin": …}}``
(formato in ``infra/helm/values.yaml``, ``formatoSegreti``).

Revisione R13a (2), (3): i segreti sono LEGATI AGLI HOST fissati al deploy. Chi ha
il permesso della console può cambiare gli indirizzi, ma un fornitore CSC o
RFC 3161 con segreti si costruisce solo se OGNI indirizzo configurato ha un host
elencato in ``host`` della sua voce del Secret (e il client HTTP lo ricontrolla
a ogni richiesta): cambiare ``url_token`` o l'indirizzo della TSA verso un host
esterno non porta mai fuori ``client_secret`` o la password. Il pinning del
Secret (``pin_spki_sha256``), se presente, prevale su quello della console. In
produzione un fornitore con segreti usa solo indirizzi https (TSA comprese).

Revisione R13a (4): ogni fornitore di FIRMA (CSC, PKCS#11) dichiara l'identità
attesa del certificato (``certificati_attesi_sha256`` e/o
``organization_identifier``, obbligatoria in produzione), controllata quando si
legge il materiale e sulla firma prodotta; in produzione ``credential_id`` CSC è
obbligatorio.

Le librerie PKCS#11 ammesse non si scelgono dalla console: il percorso deve
essere fra quelli fissati al deploy (``PKCS11_LIBRERIE_AMMESSE`` del servizio di
firma), altrimenti chi scrive nel database potrebbe far caricare una libreria
qualunque al servizio di firma.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

RUOLI: tuple[str, ...] = ("FIRMA", "MARCA")
TIPI_PER_RUOLO: dict[str, tuple[str, ...]] = {
    "FIRMA": ("CSC", "PKCS11", "SVILUPPO"),
    "MARCA": ("RFC3161", "SVILUPPO"),
}
#: Codice del fornitore: minuscole, cifre e trattini (es. «csc-principale»)
CODICE_FORNITORE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")

#: Segreti per tipo: (nome, obbligatorio). Mai nella configurazione.
SEGRETI_PER_TIPO: dict[str, tuple[tuple[str, bool], ...]] = {
    "CSC": (("client_secret", True), ("pin", False), ("otp", False),
            ("mtls_certificato_pem", False), ("mtls_chiave_pem", False)),
    "PKCS11": (("pin", True),),
    "RFC3161": (("utente", False), ("password", False)),
    "SVILUPPO": (),
}

_PAROLE_SEGRETE = ("secret", "segreto", "password", "pin", "otp", "chiave_privata", "private")
#: chiavi della voce del Secret che NON sono segreti ma vincoli fissati al deploy
CHIAVI_DEPLOY: tuple[str, ...] = ("host", "pin_spki_sha256")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)*$")
_ORG_ID = re.compile(r"^[A-Z]{3}[A-Z]{2}[-:]?[A-Za-z0-9+./-]{1,60}$")
_OID = re.compile(r"^[0-2](\.(0|[1-9]\d{0,9})){1,30}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PEM_CERT = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+?)-----END CERTIFICATE-----")
RADICI_MASSIME = 20
PEM_MASSIMO_BYTE = 64 * 1024


class ConfigurazioneFornitoreNonValida(ValueError):
    """Configurazione non utilizzabile: ``motivi`` in parole semplici."""

    def __init__(self, motivi: list[str]) -> None:
        super().__init__("; ".join(motivi))
        self.motivi = motivi


@dataclass(frozen=True)
class FornitoreRegistrato:
    """Una riga attiva di ``fornitori_firma`` (configurazione già validata)."""

    codice: str
    ruolo: str
    tipo: str
    nome: str
    priorita: int
    configurazione: dict[str, Any] = field(default_factory=dict)
    versione: int = 1


def _testo(conf: Mapping[str, Any], chiave: str, motivi: list[str], *, obbligatorio: bool,
           massimo: int = 300, etichetta: str = "") -> str:
    valore = conf.get(chiave)
    testo = "" if valore is None else str(valore).strip()
    if obbligatorio and not testo:
        motivi.append(f"{etichetta or chiave}: obbligatorio")
    if len(testo) > massimo or any(c in testo for c in "\r\n\t"):
        motivi.append(f"{etichetta or chiave}: valore non ammesso")
    return testo


def _intero(conf: Mapping[str, Any], chiave: str, predefinito: int, minimo: int, massimo: int,
            motivi: list[str]) -> int:
    valore = conf.get(chiave, predefinito)
    if isinstance(valore, bool) or not isinstance(valore, int) or not minimo <= valore <= massimo:
        motivi.append(f"{chiave}: intero fra {minimo} e {massimo}")
        return predefinito
    return valore


def _indirizzo(conf: Mapping[str, Any], chiave: str, motivi: list[str], *, obbligatorio: bool,
               solo_https: bool, etichetta: str) -> str:
    testo = _testo(conf, chiave, motivi, obbligatorio=obbligatorio, massimo=500,
                   etichetta=etichetta).rstrip("/")
    if not testo:
        return ""
    ammessi = ("https://",) if solo_https else ("https://", "http://")
    if not testo.startswith(ammessi) or any(c.isspace() for c in testo):
        motivi.append(f"{etichetta}: serve un indirizzo {'https' if solo_https else 'http(s)'}")
    return testo


def certificati_pem(testo: str) -> list[bytes]:
    """DER dei certificati in un testo PEM (solo blocchi CERTIFICATE)."""
    import base64

    return [base64.b64decode(re.sub(rb"\s+", b"", m.group(1)))
            for m in _PEM_CERT.finditer(testo.encode("ascii", "ignore"))]


def _radici(conf: Mapping[str, Any], motivi: list[str], *, obbligatorie: bool) -> str:
    testo = str(conf.get("radici_pem") or "").strip()
    if not testo:
        if obbligatorie:
            motivi.append("radici_pem: in produzione servono i certificati radice del "
                          "prestatore (catena con cui si verifica ogni firma prodotta)")
        return ""
    if len(testo) > PEM_MASSIMO_BYTE:
        motivi.append("radici_pem: testo troppo lungo")
        return ""
    der = certificati_pem(testo)
    if not der or len(der) > RADICI_MASSIME:
        motivi.append(f"radici_pem: da 1 a {RADICI_MASSIME} certificati PEM")
        return ""
    try:
        from cryptography import x509

        for d in der:
            _ = x509.load_der_x509_certificate(d).subject
    except Exception:  # noqa: BLE001 — certificato illeggibile
        motivi.append("radici_pem: certificato non leggibile")
    return testo


def _pins(conf: Mapping[str, Any], chiave: str, motivi: list[str]) -> list[str]:
    valore = conf.get(chiave) or []
    if not isinstance(valore, list) or len(valore) > 10:
        motivi.append(f"{chiave}: elenco di al più 10 impronte SHA-256 esadecimali")
        return []
    pins = [str(v).strip().lower() for v in valore]
    if not all(_HEX64.match(p) for p in pins):
        motivi.append(f"{chiave}: ogni impronta è SHA-256 in esadecimale (64 caratteri)")
    return pins


def _identita(conf: Mapping[str, Any], motivi: list[str], *, obbligatoria: bool
              ) -> dict[str, Any]:
    """Identità attesa del certificato di firma (revisione R13a (4))."""
    valore = conf.get("certificati_attesi_sha256") or []
    if isinstance(valore, str):
        valore = [valore]
    impronte: list[str] = []
    if not isinstance(valore, list) or len(valore) > 5:
        motivi.append("certificati_attesi_sha256: elenco di al più 5 impronte SHA-256")
    else:
        impronte = [str(v).strip().lower() for v in valore]
        if not all(_HEX64.match(i) for i in impronte):
            motivi.append("certificati_attesi_sha256: ogni impronta è SHA-256 in esadecimale "
                          "(64 caratteri) del certificato DER")
    org = str(conf.get("organization_identifier") or "").strip()
    if org and not _ORG_ID.match(org):
        motivi.append("organization_identifier: formato ETSI EN 319 412-1 (es. VATIT-12345678901)")
    if obbligatoria and not impronte and not org:
        motivi.append("identità attesa del certificato: in produzione indicare "
                      "certificati_attesi_sha256 o organization_identifier del conservatore")
    return {"certificati_attesi_sha256": impronte, "organization_identifier": org}


def valida_configurazione(ruolo: str, tipo: str, conf: Mapping[str, Any], *,
                          produzione: bool) -> dict[str, Any]:
    """Configurazione normalizzata del fornitore oppure
    ``ConfigurazioneFornitoreNonValida`` con i motivi. Mai segreti; in produzione
    niente fornitore di SVILUPPO, indirizzi CSC solo https e radici obbligatorie."""
    motivi: list[str] = []
    if ruolo not in RUOLI:
        raise ConfigurazioneFornitoreNonValida([f"ruolo non ammesso: {ruolo}"])
    if tipo not in TIPI_PER_RUOLO[ruolo]:
        raise ConfigurazioneFornitoreNonValida(
            [f"tipo {tipo} non ammesso per il ruolo {ruolo} "
             f"({', '.join(TIPI_PER_RUOLO[ruolo])})"])
    if not isinstance(conf, Mapping):
        raise ConfigurazioneFornitoreNonValida(["la configurazione è un oggetto JSON"])
    vietate = sorted(k for k in conf if any(p in str(k).lower() for p in _PAROLE_SEGRETE))
    if vietate:
        motivi.append("i segreti non si salvano nella console (restano nel Secret del servizio "
                      "di firma, FIRMA_SEGRETI_JSON): " + ", ".join(vietate))
    pulita: dict[str, Any]
    if tipo == "SVILUPPO":
        if produzione:
            motivi.append("il fornitore di SVILUPPO (chiave locale non qualificata) è vietato "
                          "in produzione")
        pulita = {}
    elif tipo == "CSC":
        pulita = {
            "url_base": _indirizzo(conf, "url_base", motivi, obbligatorio=True,
                                   solo_https=produzione, etichetta="Indirizzo CSC (url_base)"),
            "url_token": _indirizzo(conf, "url_token", motivi, obbligatorio=True,
                                    solo_https=produzione,
                                    etichetta="Indirizzo del token OAuth2 (url_token)"),
            "client_id": _testo(conf, "client_id", motivi, obbligatorio=True, massimo=200),
            # in produzione obbligatorio: mai «la prima credenziale» elencata
            "credential_id": _testo(conf, "credential_id", motivi, obbligatorio=produzione,
                                    massimo=200),
            "modo_autorizzazione": str(conf.get("modo_autorizzazione") or "auto"),
            "sign_algo_oid": _testo(conf, "sign_algo_oid", motivi, obbligatorio=False,
                                    massimo=100),
            "timeout_secondi": _intero(conf, "timeout_secondi", 30, 1, 120, motivi),
            "ritentativi": _intero(conf, "ritentativi", 2, 0, 5, motivi),
            "firme_per_autorizzazione": _intero(conf, "firme_per_autorizzazione", 1, 1, 100,
                                                motivi),
            "mtls": bool(conf.get("mtls", False)),
            "pin_spki_sha256": _pins(conf, "pin_spki_sha256", motivi),
            "radici_pem": _radici(conf, motivi, obbligatorie=produzione),
            **_identita(conf, motivi, obbligatoria=produzione),
        }
        if pulita["modo_autorizzazione"] not in ("auto", "implicit", "explicit"):
            motivi.append("modo_autorizzazione: auto, implicit o explicit (la firma automatica "
                          "del sigillo non ammette oauth2code)")
        if pulita["sign_algo_oid"] and not _OID.match(pulita["sign_algo_oid"]):
            motivi.append("sign_algo_oid: OID non valido")
    elif tipo == "PKCS11":
        pulita = {
            "libreria": _testo(conf, "libreria", motivi, obbligatorio=True, massimo=300),
            "token_label": _testo(conf, "token_label", motivi, obbligatorio=True, massimo=32),
            "chiave_label": _testo(conf, "chiave_label", motivi, obbligatorio=True, massimo=100),
            "certificato_pem": str(conf.get("certificato_pem") or "").strip(),
            "radici_pem": _radici(conf, motivi, obbligatorie=produzione),
            **_identita(conf, motivi, obbligatoria=produzione),
        }
        if pulita["libreria"] and not pulita["libreria"].startswith("/"):
            motivi.append("libreria: percorso assoluto della libreria PKCS#11")
        if pulita["certificato_pem"] and len(certificati_pem(pulita["certificato_pem"])) != 1:
            motivi.append("certificato_pem: un solo certificato PEM (o vuoto: si legge dal token)")
    else:  # RFC3161
        pulita = {
            "url": _indirizzo(conf, "url", motivi, obbligatorio=True, solo_https=False,
                              etichetta="Indirizzo della TSA (url)"),
            "politica_oid": _testo(conf, "politica_oid", motivi, obbligatorio=False, massimo=100),
            "timeout_secondi": _intero(conf, "timeout_secondi", 30, 1, 120, motivi),
            "ritentativi": _intero(conf, "ritentativi", 1, 0, 5, motivi),
            "tolleranza_secondi": _intero(conf, "tolleranza_secondi", 300, 30, 3600, motivi),
            "certificato_sha256": str(conf.get("certificato_sha256") or "").strip().lower(),
            "radici_pem": _radici(conf, motivi, obbligatorie=produzione),
        }
        if pulita["politica_oid"] and not _OID.match(pulita["politica_oid"]):
            motivi.append("politica_oid: OID non valido")
        if pulita["certificato_sha256"] and not _HEX64.match(pulita["certificato_sha256"]):
            motivi.append("certificato_sha256: impronta SHA-256 esadecimale (64 caratteri)")
    if motivi:
        raise ConfigurazioneFornitoreNonValida(motivi)
    return pulita


def leggi_segreti(testo_json: str, codice: str, *,
                  variabile: str = "FIRMA_SEGRETI_JSON") -> dict[str, str]:
    """Segreti del fornitore ``codice`` dal JSON del Secret (vuoto = nessuno).
    JSON non valido: ``ValueError`` senza il contenuto (mai nei log). R13b: la
    stessa funzione legge ``PEC_SEGRETI_JSON`` (``variabile`` nei messaggi)."""
    if not (testo_json or "").strip():
        return {}
    try:
        tutti = json.loads(testo_json)
    except json.JSONDecodeError:
        raise ValueError(f"{variabile}: JSON non valido") from None
    if not isinstance(tutti, dict):
        raise ValueError(f"{variabile}: serve un oggetto JSON {{codice: {{nome: valore}}}}")
    propri = tutti.get(codice) or {}
    if not isinstance(propri, dict):
        raise ValueError(f"{variabile}: la voce di {codice} non è un oggetto")
    return {str(k): str(v) for k, v in propri.items()
            if v not in (None, "") and str(k) not in CHIAVI_DEPLOY
            and isinstance(v, (str, int, float))}


def vincoli_deploy(testo_json: str, codice: str, *,
                   variabile: str = "FIRMA_SEGRETI_JSON") -> dict[str, tuple[str, ...]]:
    """Vincoli fissati al deploy nella voce del Secret: ``host`` (a cui si possono
    inviare i segreti) e ``pin_spki_sha256``. Mai i segreti. R13b: la stessa
    funzione per ``PEC_SEGRETI_JSON``."""
    if not (testo_json or "").strip():
        return {"host": (), "pin_spki_sha256": ()}
    try:
        propri = (json.loads(testo_json) or {}).get(codice) or {}
    except (json.JSONDecodeError, AttributeError):
        raise ValueError(f"{variabile}: JSON non valido") from None
    if not isinstance(propri, dict):
        raise ValueError(f"{variabile}: la voce di {codice} non è un oggetto")

    def elenco(nome: str) -> tuple[str, ...]:
        v = propri.get(nome) or []
        v = [v] if isinstance(v, str) else v
        if not isinstance(v, list):
            raise ValueError(f"{variabile}: «{nome}» di {codice} è un elenco")
        return tuple(str(x).strip().lower() for x in v if str(x).strip())

    host, pin = elenco("host"), elenco("pin_spki_sha256")
    if not all(_HOST.match(h) for h in host):
        raise ValueError(f"{variabile}: «host» di {codice}: solo nomi di host, "
                         "senza schema, porta né percorso")
    if not all(_HEX64.match(p) for p in pin):
        raise ValueError(f"{variabile}: «pin_spki_sha256» di {codice}: impronte "
                         "SHA-256 esadecimali")
    return {"host": host, "pin_spki_sha256": pin}


def indirizzi(tipo: str, conf: Mapping[str, Any]) -> list[str]:
    """Indirizzi a cui il fornitore invia richieste (e quindi segreti)."""
    chiavi = {"CSC": ("url_base", "url_token"), "RFC3161": ("url",)}.get(tipo, ())
    return [str(conf.get(k) or "") for k in chiavi if conf.get(k)]


def controlla_deploy(tipo: str, conf: Mapping[str, Any], segreti: Mapping[str, str],
                     vincoli: Mapping[str, tuple[str, ...]], *, produzione: bool) -> list[str]:
    """Revisione R13a (2), (3): segreti legati agli host del Secret; in produzione
    https obbligatorio per chi ha segreti. Motivi (vuoto = ammesso)."""
    from urllib.parse import urlparse

    if tipo not in ("CSC", "RFC3161") or not any(segreti.values()):
        return []
    motivi: list[str] = []
    ammessi = set(vincoli.get("host") or ())
    if not ammessi:
        motivi.append("FIRMA_SEGRETI_JSON: la voce del fornitore deve indicare «host», gli "
                      "host a cui si possono inviare i suoi segreti (fissati al deploy)")
    for url in indirizzi(tipo, conf):
        parti = urlparse(url)
        if ammessi and (parti.hostname or "").lower() not in ammessi:
            motivi.append(f"{url}: host non fra quelli legati ai segreti nel Secret "
                          "(FIRMA_SEGRETI_JSON, «host»)")
        if produzione and parti.scheme != "https":
            motivi.append(f"{url}: in produzione un fornitore con credenziali usa solo https")
    return motivi


def segreti_presenti(tipo: str, segreti: Mapping[str, str]) -> dict[str, bool]:
    """Per la console: quali segreti attesi ci sono (mai i valori)."""
    return {nome: bool(segreti.get(nome)) for nome, _ in SEGRETI_PER_TIPO.get(tipo, ())}


def segreti_mancanti(tipo: str, segreti: Mapping[str, str]) -> list[str]:
    return [n for n, obbligatorio in SEGRETI_PER_TIPO.get(tipo, ())
            if obbligatorio and not segreti.get(n)]


def impronta_configurazione(fornitori: Iterable[FornitoreRegistrato]) -> str:
    """Impronta stabile dell'insieme dei fornitori (ricostruzione quando cambia)."""
    dati = [(f.codice, f.ruolo, f.tipo, f.priorita, f.versione,
             json.dumps(f.configurazione, sort_keys=True)) for f in fornitori]
    return hashlib.sha256(json.dumps(sorted(dati)).encode()).hexdigest()


__all__ = [
    "CHIAVI_DEPLOY", "CODICE_FORNITORE", "ConfigurazioneFornitoreNonValida",
    "FornitoreRegistrato", "RUOLI", "SEGRETI_PER_TIPO", "TIPI_PER_RUOLO", "certificati_pem",
    "controlla_deploy", "impronta_configurazione", "indirizzi", "leggi_segreti",
    "segreti_mancanti", "segreti_presenti", "valida_configurazione", "vincoli_deploy",
]
