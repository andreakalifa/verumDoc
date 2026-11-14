# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Configurazione dei canali PEC (R13b, VD-C-078): tipi, campi, segreti legati all'host.

Riferimenti: DPR 11/02/2005 n. 68 (posta elettronica certificata), DM 2/11/2005
(regole tecniche: ricevute di accettazione, presa in carico, avvenuta consegna,
mancata consegna, firmate dal gestore); CAD art. 48; REGOLE_SVILUPPO §31 R13a
(interfaccia unica con registro dei tipi, segreti nel Secret legati agli host,
https/TLS obbligatorio in produzione, niente di finto in produzione).

Tipi (registro ``TIPI``; aggiungere un gestore = un costruttore nel servizio):
  SMTP_IMAP  casella PEC del conservatore presso un gestore accreditato: invio con
             SMTP autenticato (TLS implicito 465 o STARTTLS 587) e lettura delle
             ricevute con IMAP (TLS implicito 993). È il solo accesso comune a
             tutti i gestori (le API REST dei gestori sono proprietarie: nessun
             secondo adattatore generalizzabile, VD-P-147).
  SVILUPPO   simulatore locale (``python -m tools.simulatore_pec``), stessi
             protocolli ma senza TLS e con ricevute firmate da una CA di prova:
             MAI in produzione (console, avvio del lavoratore, costruzione).

La configurazione (tabella ``canali_pec``) non contiene MAI segreti; i segreti
stanno nel Secret ``notification-posta-secrets``, chiave ``PEC_SEGRETI_JSON``:
``{"<codice>": {"host": ["smtps.pec.gestore.it", "imaps.pec.gestore.it"],
"utente": "conservazione@pec.esempio.it", "password": "…"}}``. Come per la firma
(R13a, revisione (2)) i segreti sono LEGATI AGLI HOST della loro voce: un canale
con segreti si costruisce solo se SMTP e IMAP hanno un host elencato in ``host``,
e il client lo ricontrolla prima di autenticarsi; cambiare l'host dalla console
non porta mai fuori la password.

Ricevute AUTENTICHE: ogni ricevuta è un messaggio S/MIME firmato dal gestore
(DM 2/11/2005); si accetta solo con firma valida di un certificato che discende
da una delle radici del canale (``radici_pem``: certificati dei gestori PEC
dall'indice pubblico dei gestori, obbligatori in produzione). Vedi
``libs.shared.pec.smime`` e ``libs.shared.pec.ricevute``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from libs.shared.firma_qualificata.configurazione import (
    ConfigurazioneFornitoreNonValida,
    certificati_pem,
)
from libs.shared.firma_qualificata.configurazione import (
    leggi_segreti as _leggi_segreti,
)
from libs.shared.firma_qualificata.configurazione import (
    vincoli_deploy as _vincoli_deploy,
)

VARIABILE_SEGRETI = "PEC_SEGRETI_JSON"
TIPO_SMTP_IMAP = "SMTP_IMAP"
TIPO_SVILUPPO = "SVILUPPO"
TIPI: tuple[str, ...] = (TIPO_SMTP_IMAP, TIPO_SVILUPPO)
#: Codice del canale: minuscole, cifre e trattini (es. «pec-conservatore»)
CODICE_CANALE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
#: Segreti per tipo: (nome, obbligatorio). Mai nella configurazione.
SEGRETI_PER_TIPO: dict[str, tuple[tuple[str, bool], ...]] = {
    TIPO_SMTP_IMAP: (("password", True), ("utente", False)),
    TIPO_SVILUPPO: (("password", False), ("utente", False)),
}
SICUREZZE_SMTP = ("tls", "starttls", "nessuna")
SICUREZZE_IMAP = ("tls", "nessuna")
TIPI_RICEVUTA = ("completa", "breve", "sintetica")
_PAROLE_SEGRETE = ("secret", "segreto", "password", "pin", "otp", "chiave_privata", "private")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)*$")
_INDIRIZZO = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_CARTELLA = re.compile(r"^[A-Za-z0-9 ._/-]{1,100}$")
RADICI_MASSIME = 50
PEM_MASSIMO_BYTE = 256 * 1024


@dataclass(frozen=True)
class CanaleRegistrato:
    """Una riga attiva di ``canali_pec`` (configurazione già validata)."""

    codice: str
    tipo: str
    nome: str
    priorita: int
    configurazione: dict[str, Any] = field(default_factory=dict)
    versione: int = 1


def _testo(conf: Mapping[str, Any], chiave: str, motivi: list[str], *, obbligatorio: bool,
           massimo: int = 300) -> str:
    valore = conf.get(chiave)
    testo = "" if valore is None else str(valore).strip()
    if obbligatorio and not testo:
        motivi.append(f"{chiave}: obbligatorio")
    if len(testo) > massimo or any(c in testo for c in "\r\n\t"):
        motivi.append(f"{chiave}: valore non ammesso")
    return testo


def _intero(conf: Mapping[str, Any], chiave: str, predefinito: int, minimo: int, massimo: int,
            motivi: list[str]) -> int:
    valore = conf.get(chiave, predefinito)
    if isinstance(valore, bool) or not isinstance(valore, int) or not minimo <= valore <= massimo:
        motivi.append(f"{chiave}: intero fra {minimo} e {massimo}")
        return predefinito
    return valore


def _host(conf: Mapping[str, Any], chiave: str, motivi: list[str]) -> str:
    host = _testo(conf, chiave, motivi, obbligatorio=True, massimo=253).lower().rstrip(".")
    if host and not _HOST.match(host):
        motivi.append(f"{chiave}: solo il nome dell'host (senza schema, porta né percorso)")
    return host


def _radici(conf: Mapping[str, Any], motivi: list[str], *, obbligatorie: bool) -> str:
    testo = str(conf.get("radici_pem") or "").strip()
    if not testo:
        if obbligatorie:
            motivi.append("radici_pem: in produzione servono i certificati dei gestori PEC "
                          "(indice pubblico dei gestori): senza, nessuna ricevuta è autentica")
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


def valida_configurazione(tipo: str, conf: Mapping[str, Any], *,
                          produzione: bool) -> dict[str, Any]:
    """Configurazione normalizzata del canale oppure
    ``ConfigurazioneFornitoreNonValida`` con i motivi. Mai segreti; in produzione
    niente SVILUPPO, solo TLS (SMTP 465/STARTTLS, IMAP 993) e radici obbligatorie."""
    motivi: list[str] = []
    if tipo not in TIPI:
        raise ConfigurazioneFornitoreNonValida([f"tipo non ammesso: {tipo} ({', '.join(TIPI)})"])
    if not isinstance(conf, Mapping):
        raise ConfigurazioneFornitoreNonValida(["la configurazione è un oggetto JSON"])
    vietate = sorted(k for k in conf if any(p in str(k).lower() for p in _PAROLE_SEGRETE))
    if vietate:
        motivi.append("i segreti non si salvano nella console (restano nel Secret del "
                      f"lavoratore della posta, {VARIABILE_SEGRETI}): " + ", ".join(vietate))
    if tipo == TIPO_SVILUPPO and produzione:
        motivi.append("il canale di SVILUPPO (simulatore locale) è vietato in produzione")
    pulita: dict[str, Any] = {
        "indirizzo": _testo(conf, "indirizzo", motivi, obbligatorio=True, massimo=320).lower(),
        "smtp_host": _host(conf, "smtp_host", motivi),
        "smtp_porta": _intero(conf, "smtp_porta", 465, 1, 65535, motivi),
        "smtp_sicurezza": str(conf.get("smtp_sicurezza") or "tls"),
        "imap_host": _host(conf, "imap_host", motivi),
        "imap_porta": _intero(conf, "imap_porta", 993, 1, 65535, motivi),
        "imap_sicurezza": str(conf.get("imap_sicurezza") or "tls"),
        "cartella": _testo(conf, "cartella", motivi, obbligatorio=False, massimo=100) or "INBOX",
        "tipo_ricevuta": str(conf.get("tipo_ricevuta") or "completa"),
        "timeout_secondi": _intero(conf, "timeout_secondi", 30, 1, 120, motivi),
        "dimensione_massima_kb": _intero(conf, "dimensione_massima_kb", 10240, 64, 51200, motivi),
        "radici_pem": _radici(conf, motivi, obbligatorie=produzione),
    }
    if conf.get("ca_pem"):
        # revisione R13b, rilievo 3: chi scrive in console non sceglie di quali CA fidarsi
        # per le connessioni che portano la password (come R13a: vincoli nel Secret)
        motivi.append(f"ca_pem: la CA dei server si mette nella voce del canale in "
                      f"{VARIABILE_SEGRETI} (Secret del lavoratore), non nella console")
    if pulita["indirizzo"] and not _INDIRIZZO.match(pulita["indirizzo"]):
        motivi.append("indirizzo: indirizzo PEC del conservatore non valido")
    if pulita["smtp_sicurezza"] not in SICUREZZE_SMTP:
        motivi.append(f"smtp_sicurezza: {', '.join(SICUREZZE_SMTP)}")
    if pulita["imap_sicurezza"] not in SICUREZZE_IMAP:
        motivi.append(f"imap_sicurezza: {', '.join(SICUREZZE_IMAP)}")
    if pulita["tipo_ricevuta"] not in TIPI_RICEVUTA:
        motivi.append(f"tipo_ricevuta: {', '.join(TIPI_RICEVUTA)}")
    if not _CARTELLA.match(pulita["cartella"]):
        motivi.append("cartella: nome della cartella IMAP non ammesso")
    if produzione and tipo == TIPO_SMTP_IMAP:
        if pulita["smtp_sicurezza"] == "nessuna" or pulita["imap_sicurezza"] == "nessuna":
            motivi.append("in produzione SMTP e IMAP solo cifrati (TLS o STARTTLS)")
        if pulita["smtp_sicurezza"] == "tls" and pulita["smtp_porta"] == 587:
            motivi.append("porta 587 con TLS implicito: usare starttls")
    if motivi:
        raise ConfigurazioneFornitoreNonValida(motivi)
    return pulita


#: CA privata dei server SMTP/IMAP nella voce del Secret (vuoto = CA di sistema)
CHIAVE_CA = "ca_pem"


def leggi_segreti(testo_json: str, codice: str) -> dict[str, str]:
    """Segreti del canale ``codice`` da ``PEC_SEGRETI_JSON`` (vuoto = nessuno)."""
    return _leggi_segreti(testo_json, codice, variabile=VARIABILE_SEGRETI)


def vincoli_deploy(testo_json: str, codice: str) -> dict[str, tuple[str, ...]]:
    """Host a cui si possono inviare i segreti del canale (fissati al deploy)."""
    return _vincoli_deploy(testo_json, codice, variabile=VARIABILE_SEGRETI)


def indirizzi_host(conf: Mapping[str, Any]) -> list[str]:
    """Host a cui il canale si autentica (e quindi invia la password)."""
    return [str(conf.get(k) or "").lower() for k in ("smtp_host", "imap_host") if conf.get(k)]


def controlla_deploy(tipo: str, conf: Mapping[str, Any], segreti: Mapping[str, str],
                     vincoli: Mapping[str, tuple[str, ...]], *, produzione: bool) -> list[str]:
    """Segreti legati agli host del Secret (come R13a, revisione (2)) e TLS in
    produzione per chi ha segreti. Motivi (vuoto = ammesso)."""
    motivi: list[str] = []
    if produzione and tipo == TIPO_SVILUPPO:
        motivi.append("canale di SVILUPPO vietato in produzione")
    ca = str(segreti.get(CHIAVE_CA) or "")
    if ca and len(certificati_pem(ca)) < 1:
        motivi.append(f"{VARIABILE_SEGRETI}: «{CHIAVE_CA}» del canale deve contenere "
                      "certificati PEM")
    if not credenziali(segreti):
        return motivi
    ammessi = set(vincoli.get("host") or ())
    if not ammessi:
        motivi.append(f"{VARIABILE_SEGRETI}: la voce del canale deve indicare «host», gli host "
                      "a cui si possono inviare i suoi segreti (fissati al deploy)")
    for host in indirizzi_host(conf):
        if ammessi and host not in ammessi:
            motivi.append(f"{host}: host non fra quelli legati ai segreti nel Secret "
                          f"({VARIABILE_SEGRETI}, «host»)")
    if produzione and ("nessuna" in (conf.get("smtp_sicurezza"), conf.get("imap_sicurezza"))):
        motivi.append("in produzione un canale con credenziali usa solo connessioni cifrate")
    return motivi


def segreti_presenti(tipo: str, segreti: Mapping[str, str]) -> dict[str, bool]:
    return {nome: bool(segreti.get(nome)) for nome, _ in SEGRETI_PER_TIPO.get(tipo, ())}


def credenziali(segreti: Mapping[str, str]) -> bool:
    """La voce del Secret porta credenziali (la CA dei server da sola non lo è)."""
    return any(v for k, v in segreti.items() if k != CHIAVE_CA)


def segreti_mancanti(tipo: str, segreti: Mapping[str, str]) -> list[str]:
    return [n for n, obbligatorio in SEGRETI_PER_TIPO.get(tipo, ())
            if obbligatorio and not segreti.get(n)]


__all__ = [
    "CHIAVE_CA", "CODICE_CANALE", "SEGRETI_PER_TIPO", "TIPI", "TIPI_RICEVUTA",
    "TIPO_SMTP_IMAP", "TIPO_SVILUPPO", "VARIABILE_SEGRETI", "CanaleRegistrato",
    "controlla_deploy", "credenziali",
    "indirizzi_host", "leggi_segreti", "segreti_mancanti", "segreti_presenti",
    "valida_configurazione", "vincoli_deploy",
]
