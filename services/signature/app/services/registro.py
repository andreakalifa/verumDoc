# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/registro.py
Selezione dei fornitori da configurazione e stato onesto del servizio — VD-C-010.

Regole (REGOLE §19.4, §20.2):
  - nessun fallback silenzioso: un fornitore non configurato resta «non
    configurato» e la firma risponde 503 con codice esplicito;
  - in ENV=production l'adattatore di sviluppo è rifiutato (avvio bloccato e,
    per difesa in profondità, firma rifiutata);
  - lo stato dice «firma qualificata: non configurata» finché il fornitore
    qualificato non è configurato e il suo certificato non dichiara QcCompliance.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog
from asn1crypto import pem, x509

from app.config import Settings
from app.services.csc_client import ClienteCSC, FornitoreFirmaCSC
from app.services.firma_cms import ServizioFirma
from app.services.fornitori import (
    ErroreFirma,
    FornitoreFirma,
    FornitoreMarca,
    FornitoreVietatoInProduzione,
)
from app.services.sviluppo import (
    FornitoreFirmaSviluppo,
    FornitoreMarcaSviluppo,
    a_asn1,
    carica_o_genera,
)
from app.services.tsa_client import ClienteTSA, FornitoreMarcaRFC3161

logger = structlog.get_logger(__name__)


def carica_radici(percorso: str) -> list[x509.Certificate]:
    """Legge uno o più certificati PEM/DER da file; percorso vuoto = nessuna radice."""
    if not percorso:
        return []
    dati = Path(percorso).read_bytes()
    if pem.detect(dati):
        return [x509.Certificate.load(der) for _, _, der in pem.unarmor(dati, multiple=True)]
    return [x509.Certificate.load(dati)]


def problemi_configurazione(cfg: Settings) -> list[str]:
    """Controlli puri sulla configurazione (testati). In produzione bloccano l'avvio."""
    problemi: list[str] = []
    if cfg.firma_fornitore == "sviluppo" and cfg.in_produzione:
        problemi.append("FIRMA_FORNITORE=sviluppo vietato in produzione")
    if cfg.marca_fornitore == "sviluppo" and cfg.in_produzione:
        problemi.append("MARCA_FORNITORE=sviluppo vietato in produzione")
    if cfg.firma_fornitore == "csc":
        mancanti = [
            n
            for n, v in (
                ("CSC_URL_BASE", cfg.csc_url_base),
                ("CSC_URL_TOKEN", cfg.csc_url_token),
                ("CSC_CLIENT_ID", cfg.csc_client_id),
                ("CSC_CREDENTIAL_ID", cfg.csc_credential_id),
            )
            if not v
        ]
        if mancanti:
            problemi.append("firma CSC: mancano " + ", ".join(mancanti))
        if cfg.in_produzione and cfg.csc_url_base and urlparse(cfg.csc_url_base).scheme != "https":
            problemi.append("firma CSC: URL non https in produzione")
    if cfg.marca_fornitore == "rfc3161":
        if not cfg.tsa_url:
            problemi.append("marca RFC 3161: manca TSA_URL")
        if cfg.in_produzione and not cfg.tsa_radici_pem:
            problemi.append("marca RFC 3161: TSA_RADICI_PEM obbligatorio in produzione")
    if cfg.in_produzione and cfg.firma_fornitore != "non_configurato" and not cfg.firma_radici_pem:
        problemi.append("FIRMA_RADICI_PEM obbligatorio in produzione per verificare le firme")
    return problemi


def costruisci_servizio(
    cfg: Settings,
    *,
    adesso: datetime,
    trasporto_csc: httpx.AsyncBaseTransport | None = None,
    trasporto_tsa: httpx.AsyncBaseTransport | None = None,
) -> ServizioFirma:
    """Crea il ServizioFirma dai fornitori configurati.

    Solleva FornitoreVietatoInProduzione se si chiede l'adattatore di sviluppo
    in produzione: nessun fallback.
    """
    if cfg.in_produzione and "sviluppo" in (cfg.firma_fornitore, cfg.marca_fornitore):
        raise FornitoreVietatoInProduzione(
            "adattatore di sviluppo configurato in produzione",
            {"firma": cfg.firma_fornitore, "marca": cfg.marca_fornitore},
        )
    firma: FornitoreFirma | None = None
    marca: FornitoreMarca | None = None
    radici_sviluppo: list[x509.Certificate] = []
    if "sviluppo" in (cfg.firma_fornitore, cfg.marca_fornitore):
        chiavi = carica_o_genera(cfg.sviluppo_dir_chiavi, adesso)
        radici_sviluppo = [a_asn1(chiavi.ca_cert)]
        if cfg.firma_fornitore == "sviluppo":
            firma = FornitoreFirmaSviluppo(chiavi)
        if cfg.marca_fornitore == "sviluppo":
            marca = FornitoreMarcaSviluppo(chiavi)
    if cfg.firma_fornitore == "csc":
        cliente = ClienteCSC(
            cfg.csc_url_base,
            cfg.csc_url_token,
            cfg.csc_client_id,
            cfg.csc_client_secret.get_secret_value(),
            timeout=cfg.csc_timeout_secondi,
            trasporto=trasporto_csc,
        )
        firma = FornitoreFirmaCSC(
            cliente,
            cfg.csc_credential_id,
            pin=cfg.csc_pin.get_secret_value(),
            sign_algo_oid=cfg.csc_sign_algo_oid,
        )
    radici_tsa = carica_radici(cfg.tsa_radici_pem)
    if cfg.marca_fornitore == "rfc3161":
        cliente_tsa = ClienteTSA(
            cfg.tsa_url,
            utente=cfg.tsa_utente,
            password=cfg.tsa_password.get_secret_value(),
            politica_oid=cfg.tsa_policy_oid,
            radici=radici_tsa,
            timeout=cfg.tsa_timeout_secondi,
            trasporto=trasporto_tsa,
        )
        marca = FornitoreMarcaRFC3161(cliente_tsa, radici_tsa)
    return ServizioFirma(
        firma,
        marca,
        produzione=cfg.in_produzione,
        radici_firma=carica_radici(cfg.firma_radici_pem),
        radici_marca=radici_tsa,
        radici_sviluppo=radici_sviluppo,
    )


async def stato_servizio(servizio: ServizioFirma, cfg: Settings) -> dict:
    """Stato onesto di firma e marca, senza segreti."""
    f = servizio.fornitore_firma
    m = servizio.fornitore_marca
    firma_qualificata = "non configurata"
    firma: dict = {"fornitore": f.nome if f else "non_configurato", "qualificata": False}
    if f is None:
        firma["stato"] = (
            "firma qualificata: non configurata (fornitore da scegliere, decisione VD-P-05)"
        )
    elif f.di_sviluppo:
        firma["stato"] = (
            "firma di SVILUPPO: chiave autogenerata, NON qualificata, nessun valore legale"
        )
        firma_qualificata = "non configurata (attivo solo l'adattatore di sviluppo)"
        if servizio.produzione:
            firma["stato"] += " — VIETATA in produzione"
    else:
        try:
            mat = await f.materiale()
            firma["certificato"] = mat.certificato.subject.human_friendly
            firma["qualificata"] = mat.qualificato
            if mat.qualificato:
                firma_qualificata = (
                    "configurata (QcCompliance dichiarata dal certificato; "
                    "Trusted List non verificata)"
                )
                firma["stato"] = "firma remota CSC attiva con certificato che dichiara QcCompliance"
            else:
                firma_qualificata = (
                    "non configurata (il certificato del fornitore non è qualificato)"
                )
                firma["stato"] = "firma remota CSC attiva ma il certificato NON è qualificato"
        except ErroreFirma as exc:
            firma_qualificata = "non disponibile (fornitore non raggiungibile)"
            firma["stato"] = f"fornitore di firma non disponibile: {exc.messaggio}"
            firma["errore"] = exc.come_dict()
    marca: dict = {
        "fornitore": m.nome if m else "non_configurato",
        "di_sviluppo": bool(m and m.di_sviluppo),
    }
    if m is None:
        marca["stato"] = (
            "marca temporale qualificata: non configurata (TSA da scegliere, decisione VD-P-05)"
        )
    elif m.di_sviluppo:
        marca["stato"] = "marca di SVILUPPO: TSA locale NON qualificata, nessun valore legale"
    else:
        marca["stato"] = (
            "TSA RFC 3161 configurata; la qualificazione dipende dal servizio "
            "scelto (verifica sulla Trusted List non integrata)"
        )
        marca["radici_configurate"] = bool(cfg.tsa_radici_pem)
        marca["host"] = urlparse(cfg.tsa_url).hostname
    problemi = problemi_configurazione(cfg)
    production_ready = (
        f is not None
        and not f.di_sviluppo
        and bool(firma.get("qualificata"))
        and m is not None
        and not m.di_sviluppo
        and not problemi
    )
    return {
        "ambiente": cfg.env,
        "firma_qualificata": firma_qualificata,
        "firma": firma,
        "marca": marca,
        "production_ready": production_ready,
        "problemi_configurazione": problemi,
        "limiti": [
            "Trusted List europea non integrata: la catena si verifica sulle radici "
            "configurate per il fornitore (EU DSS in verifica-firme, VD-C-143)",
            "revoche (OCSP/CRL) incluse e verificate solo per i livelli B-LT e B-LTA",
        ],
    }
