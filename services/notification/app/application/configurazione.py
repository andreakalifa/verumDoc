# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/application/configurazione.py
Impostazioni dei webhook e degli eventi tradotte per i casi d'uso — contratto C3.

- ``cifratore()``: cifratore dei segreti dalle impostazioni (una volta per
  processo); None se la chiave non è configurata (le API rispondono 503 WH-030).
- ``problemi_avvio(settings)``: controlli puri che in produzione fermano
  l'avvio (REGOLE §19.4): chiave di cifratura assente o non valida, indirizzi
  privati ammessi, ritardi o limiti non sensati; R12b (VD-C-161,
  ``problemi_posta``): segreti della posta in un processo senza invio.
"""

from __future__ import annotations

from typing import Any

from app.application.consegne import ParametriConsegna
from app.application.webhook_service import RegoleWebhook
from app.infrastructure.cifratura import Cifratore, ErroreCifratura
from libs.shared.errori import ErroreApi

_cifratore: Cifratore | None = None
_cifratore_letto = False


def _da_impostazioni(s: Any) -> Cifratore | None:
    return Cifratore.da_impostazioni(
        s.webhook_chiave_cifratura.get_secret_value(), s.webhook_chiave_cifratura_id,
        s.webhook_chiavi_cifratura_precedenti.get_secret_value())


def cifratore() -> Cifratore | None:
    global _cifratore, _cifratore_letto
    if not _cifratore_letto:
        from app.config import settings

        _cifratore = _da_impostazioni(settings)
        _cifratore_letto = True
    return _cifratore


def imposta_cifratore(valore: Cifratore | None) -> None:
    """Solo test e avvio: sostituisce il cifratore del processo."""
    global _cifratore, _cifratore_letto
    _cifratore, _cifratore_letto = valore, True


def cifratore_obbligatorio() -> Cifratore:
    """Cifratore o 503 WH-030 (webhook non disponibili in questo ambiente)."""
    try:
        c = cifratore()
    except ErroreCifratura:
        c = None
    if c is None:
        raise ErroreApi("WH-030")
    return c


def regole_webhook(s: Any) -> RegoleWebhook:
    return RegoleWebhook(lunghezza_massima_url=s.webhook_lunghezza_massima_url,
                         consenti_privati=s.webhook_consenti_indirizzi_privati,
                         massimo_per_ente=s.webhook_max_sottoscrizioni_per_ente,
                         porte_ammesse=frozenset(s.webhook_porte_ammesse))


def parametri_consegna(s: Any) -> ParametriConsegna:
    return ParametriConsegna(
        ritardi_secondi=list(s.webhook_ritardi_secondi), lotto=s.webhook_lotto,
        concorrenza=s.webhook_concorrenza, lease_secondi=s.webhook_lease_secondi,
        lunghezza_massima_url=s.webhook_lunghezza_massima_url,
        consenti_privati=s.webhook_consenti_indirizzi_privati,
        porte_ammesse=frozenset(s.webhook_porte_ammesse))


def apritore_posta(s: Any) -> Any:
    """R12a (VD-C-152): chiavi private della posta, None se non configurate."""
    from libs.shared.security.sigillo import Apritore

    return Apritore.da_impostazioni(
        s.posta_chiave_privata.get_secret_value(), s.posta_chiave_privata_id,
        s.posta_chiavi_private_precedenti.get_secret_value())


#: R12b (VD-C-161): segreti che servono SOLO all'invio della posta. Un processo
#: senza invio (repliche API esposte) non li riceve: in produzione non parte.
SEGRETI_SOLO_POSTA = ("posta_chiave_privata", "posta_chiavi_private_precedenti",
                      "smtp_password",
                      # R13b (VD-C-078): password dei canali PEC (legate agli host)
                      "pec_segreti_json")


def segreti_posta_presenti(s: Any) -> list[str]:
    """Nomi (variabili d'ambiente) dei segreti della posta configurati nel processo."""
    presenti = []
    for campo in SEGRETI_SOLO_POSTA:
        valore = getattr(s, campo, None)
        testo = valore.get_secret_value() if hasattr(valore, "get_secret_value") else valore
        if str(testo or "").strip():
            presenti.append(campo.upper())
    return presenti


def problemi_posta(s: Any) -> list[str]:
    """Controllo d'avvio coerente con il ruolo del processo (R12a VD-C-152, R12b
    VD-C-161): con l'invio attivo la chiave privata serve (in produzione) ed è
    valida; senza invio (API esposta) nessun segreto della posta deve arrivare."""
    problemi: list[str] = []
    if not getattr(s, "posta_invio_attivo", True):
        presenti = segreti_posta_presenti(s)
        if presenti:
            problemi.append(
                f"{', '.join(presenti)} nel processo senza invio della posta "
                "(POSTA_INVIO_ATTIVO=false: repliche API esposte): vanno solo al lavoratore "
                "notification-posta (VD-C-161)")
        return problemi
    problemi += problemi_pec(s)
    try:
        if apritore_posta(s) is None and s.in_produzione:
            problemi.append("POSTA_CHIAVE_PRIVATA mancante: gli inviti sigillati non si "
                            "possono inviare (VD-C-152)")
    except Exception as exc:  # noqa: BLE001 — chiave malformata: si dice quale
        problemi.append(f"chiave privata della posta non valida: {exc}")
    return problemi


def problemi_pec(s: Any) -> list[str]:
    """R13b: ``PEC_SEGRETI_JSON`` leggibile (oggetto JSON di voci per codice, senza
    mai mostrare i valori) e ritardi sensati. Ogni voce con segreti deve dichiarare
    gli host a cui legarli (controllato anche alla costruzione del canale)."""
    import json

    problemi: list[str] = []
    testo = s.pec_segreti_json.get_secret_value() if hasattr(s, "pec_segreti_json") else ""
    if str(testo or "").strip():
        try:
            voci = json.loads(testo)
        except json.JSONDecodeError:
            return ["PEC_SEGRETI_JSON: JSON non valido"]
        if not isinstance(voci, dict):
            return ["PEC_SEGRETI_JSON: serve un oggetto {codice: {host, utente, password}}"]
        for codice, voce in voci.items():
            if not isinstance(voce, dict) or not voce.get("host"):
                problemi.append(f"PEC_SEGRETI_JSON: la voce {codice} deve indicare «host»")
    if any(int(r) <= 0 for r in getattr(s, "pec_ritardi_secondi", [1])):
        problemi.append("PEC_RITARDI_SECONDI: ogni ritardo deve essere positivo")
    return problemi


def problemi_avvio(s: Any) -> list[str]:
    """Problemi di configurazione dei webhook e degli eventi. In produzione
    ognuno ferma l'avvio; fuori produzione si registrano nel log."""
    problemi: list[str] = []
    try:
        if _da_impostazioni(s) is None and s.in_produzione:
            problemi.append("WEBHOOK_CHIAVE_CIFRATURA mancante: i segreti dei webhook "
                            "non si possono cifrare (openssl rand -base64 32)")
    except ErroreCifratura as exc:
        problemi.append(f"chiave di cifratura dei webhook non valida: {exc}")
    problemi += problemi_posta(s)
    if s.in_produzione and s.webhook_consenti_indirizzi_privati:
        problemi.append("WEBHOOK_CONSENTI_INDIRIZZI_PRIVATI=true vietato in produzione (SSRF)")
    if any(int(r) <= 0 for r in s.webhook_ritardi_secondi):
        problemi.append("WEBHOOK_RITARDI_SECONDI: ogni ritardo deve essere positivo")
    if s.webhook_timeout_secondi <= 0 or s.webhook_timeout_connessione_secondi <= 0:
        problemi.append("timeout dei webhook non positivi")
    if s.webhook_lease_secondi <= s.webhook_timeout_secondi:
        problemi.append("WEBHOOK_LEASE_SECONDI deve superare WEBHOOK_TIMEOUT_SECONDI")
    if not 1 <= s.eventi_limite_predefinito <= s.eventi_limite_massimo:
        problemi.append("EVENTI_LIMITE_PREDEFINITO deve stare fra 1 ed EVENTI_LIMITE_MASSIMO")
    from app.domain.eventi import tipi_sconosciuti

    sconosciuti = tipi_sconosciuti(list(s.eventi_topic.values()))
    if sconosciuti:
        problemi.append(f"EVENTI_TOPIC usa tipi sconosciuti: {', '.join(sconosciuti)}")
    return problemi


def stato_webhook(s: Any) -> dict[str, Any]:
    """Stato onesto per /api/v1/info."""
    try:
        configurato = _da_impostazioni(s) is not None
    except ErroreCifratura:
        configurato = False
    if not configurato:
        return {"stato": "non configurati",
                "motivo": "chiave di cifratura dei segreti assente o non valida"}
    return {"stato": "configurati", "lavoratore_attivo": s.webhook_lavoratore_attivo,
            "ritardi_secondi": list(s.webhook_ritardi_secondi),
            "indirizzi_privati_ammessi": s.webhook_consenti_indirizzi_privati}
