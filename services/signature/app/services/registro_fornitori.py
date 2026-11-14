# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/registro_fornitori.py
Registro dei fornitori di firma e marca configurati in console (R13a).

  - ``CATALOGO``: un costruttore per (ruolo, tipo) — CSC, PKCS11, SVILUPPO per la
    firma; RFC3161, SVILUPPO per la marca. Un tipo nuovo si aggiunge qui e in
    ``libs.shared.firma_qualificata`` (campi e segreti), senza toccare i chiamanti;
  - ``leggi_registro``: righe attive di ``fornitori_firma`` (login di sola
    lettura), configurazione RIVALIDATA con la stessa funzione della console;
  - ``costruisci_da_registro``: ``ServizioFirma`` con i fornitori di firma in
    ordine di priorità (ripiego) e le TSA in ripiego. Un fornitore con
    configurazione non valida, segreti mancanti o vietato (SVILUPPO in
    produzione) NON si costruisce: resta nei ``problemi`` (stato e console);
  - ``GestoreConfigurazione``: ricostruisce il servizio quando il registro
    cambia (impronta), al più ogni ``FIRMA_CONFIGURAZIONE_CACHE_SECONDI``; con
    il database non raggiungibile tiene l'ultima configurazione buona e, se non ne
    ha, risponde ``FIRMA-503-CONFIGURAZIONE`` (transitorio, mai «non configurata»);
    senza righe attive vale la configurazione d'ambiente (§8.14);
  - ``problemi_avvio``: in produzione il servizio NON parte senza DATABASE_URL o
    con un fornitore di SVILUPPO attivo nel registro (§19.4).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from asn1crypto import x509

from app.config import Settings
from app.services.csc_client import ClienteCSC, FornitoreFirmaCSC
from app.services.firma_cms import ServizioFirma
from app.services.fornitori import (
    ConfigurazioneNonDisponibile,
    ErroreFirma,
    FornitoreFirma,
    FornitoreMarca,
    IdentitaAttesa,
)
from app.services.tsa_client import ClienteTSA, FornitoreMarcaRFC3161, MarcaConRipiego
from libs.shared.firma_qualificata.configurazione import (
    ConfigurazioneFornitoreNonValida,
    FornitoreRegistrato,
    certificati_pem,
    controlla_deploy,
    impronta_configurazione,
    leggi_segreti,
    segreti_mancanti,
    valida_configurazione,
    vincoli_deploy,
)

logger = structlog.get_logger(__name__)

SQL_REGISTRO = ("SELECT codice, ruolo, tipo, nome, priorita, configurazione, versione "
                "FROM public.fornitori_firma WHERE attivo ORDER BY ruolo, priorita, codice")


def radici_da_pem(testo: str) -> tuple[x509.Certificate, ...]:
    return tuple(x509.Certificate.load(d) for d in certificati_pem(testo or ""))


@dataclass
class Contesto:
    """Dipendenze dei costruttori (impostazioni, segreti, trasporti dei test)."""

    cfg: Settings
    adesso: datetime
    segreti_json: str = ""
    trasporti: dict[str, httpx.AsyncBaseTransport] = field(default_factory=dict)
    _chiavi_sviluppo: Any = None

    def segreti(self, codice: str) -> dict[str, str]:
        return leggi_segreti(self.segreti_json, codice)

    def vincoli(self, codice: str) -> dict[str, tuple[str, ...]]:
        return vincoli_deploy(self.segreti_json, codice)

    def rete(self, f: FornitoreRegistrato) -> dict[str, Any]:
        """Revisione R13a (2), (3): host legati ai segreti, https in produzione con
        segreti, pinning del Secret che prevale su quello della console."""
        s, v = self.segreti(f.codice), self.vincoli(f.codice)
        con_segreti = any(s.values())
        return {"host_ammessi": v["host"] if con_segreti else (),
                "solo_https": con_segreti and self.cfg.in_produzione,
                "pin_spki_sha256": v["pin_spki_sha256"]
                or tuple(f.configurazione.get("pin_spki_sha256") or ())}

    def chiavi_sviluppo(self) -> Any:
        from app.services.sviluppo import carica_o_genera

        if self._chiavi_sviluppo is None:
            self._chiavi_sviluppo = carica_o_genera(self.cfg.sviluppo_dir_chiavi, self.adesso)
        return self._chiavi_sviluppo


def _identita(conf: dict[str, Any]) -> IdentitaAttesa:
    return IdentitaAttesa(frozenset(conf.get("certificati_attesi_sha256") or ()),
                          str(conf.get("organization_identifier") or ""))


def _csc(f: FornitoreRegistrato, c: Contesto) -> FornitoreFirma:
    conf, s = f.configurazione, c.segreti(f.codice)
    cliente = ClienteCSC(
        conf["url_base"], conf["url_token"], conf["client_id"], s.get("client_secret", ""),
        timeout=float(conf.get("timeout_secondi", 30)), ritentativi=int(conf.get("ritentativi", 2)),
        trasporto=c.trasporti.get(f.codice),
        mtls_certificato_pem=s.get("mtls_certificato_pem", "") if conf.get("mtls") else "",
        mtls_chiave_pem=s.get("mtls_chiave_pem", "") if conf.get("mtls") else "",
        **c.rete(f))
    fornitore = FornitoreFirmaCSC(
        cliente, conf.get("credential_id", ""), pin=s.get("pin", ""), otp=s.get("otp", ""),
        sign_algo_oid=conf.get("sign_algo_oid", ""),
        modo_autorizzazione=conf.get("modo_autorizzazione", "auto"),
        firme_per_autorizzazione=int(conf.get("firme_per_autorizzazione", 1)),
        codice=f.codice, radici=radici_da_pem(conf.get("radici_pem", "")))
    fornitore.identita = _identita(conf)
    return fornitore


def _pkcs11(f: FornitoreRegistrato, c: Contesto) -> FornitoreFirma:
    from app.services.pkcs11_fornitore import FornitoreFirmaPKCS11

    conf = f.configurazione
    fornitore = FornitoreFirmaPKCS11(
        libreria=conf["libreria"], token_label=conf["token_label"],
        chiave_label=conf["chiave_label"], pin=c.segreti(f.codice).get("pin", ""),
        certificato_pem=conf.get("certificato_pem", ""),
        librerie_ammesse=c.cfg.pkcs11_librerie_ammesse, codice=f.codice,
        radici=radici_da_pem(conf.get("radici_pem", "")))
    fornitore.identita = _identita(conf)
    return fornitore


def _firma_sviluppo(f: FornitoreRegistrato, c: Contesto) -> FornitoreFirma:
    from app.services.sviluppo import FornitoreFirmaSviluppo

    return FornitoreFirmaSviluppo(c.chiavi_sviluppo(), codice=f.codice)


def _tsa(f: FornitoreRegistrato, c: Contesto) -> FornitoreMarca:
    conf, s = f.configurazione, c.segreti(f.codice)
    radici = list(radici_da_pem(conf.get("radici_pem", "")))
    cliente = ClienteTSA(
        conf["url"], utente=s.get("utente", ""), password=s.get("password", ""),
        politica_oid=conf.get("politica_oid", ""), radici=radici,
        timeout=float(conf.get("timeout_secondi", 30)),
        ritentativi=int(conf.get("ritentativi", 1)),
        tolleranza_secondi=int(conf.get("tolleranza_secondi", 300)),
        certificato_sha256=conf.get("certificato_sha256", ""),
        trasporto=c.trasporti.get(f.codice), **c.rete(f))
    return FornitoreMarcaRFC3161(cliente, radici, codice=f.codice)


def _marca_sviluppo(f: FornitoreRegistrato, c: Contesto) -> FornitoreMarca:
    from app.services.sviluppo import FornitoreMarcaSviluppo

    return FornitoreMarcaSviluppo(c.chiavi_sviluppo(), codice=f.codice)


#: (ruolo, tipo) → costruttore dell'adattatore
CATALOGO: dict[tuple[str, str], Callable[[FornitoreRegistrato, Contesto], Any]] = {
    ("FIRMA", "CSC"): _csc,
    ("FIRMA", "PKCS11"): _pkcs11,
    ("FIRMA", "SVILUPPO"): _firma_sviluppo,
    ("MARCA", "RFC3161"): _tsa,
    ("MARCA", "SVILUPPO"): _marca_sviluppo,
}


async def leggi_registro() -> list[FornitoreRegistrato]:
    """Righe attive del registro (una query). Database non inizializzato: nessuna."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session
    from libs.shared.security.ambito_dati import ambito_conservatore

    with ambito_conservatore("registro dei fornitori di firma", registra=False):
        async with get_async_session() as s:
            righe = (await s.execute(text(SQL_REGISTRO))).mappings().all()
    return [FornitoreRegistrato(
        codice=str(r["codice"]), ruolo=str(r["ruolo"]), tipo=str(r["tipo"]),
        nome=str(r["nome"]), priorita=int(r["priorita"]),
        configurazione=dict(r["configurazione"] or {}), versione=int(r["versione"]))
        for r in righe]


@dataclass
class EsitoCostruzione:
    servizio: ServizioFirma
    problemi: dict[str, list[str]] = field(default_factory=dict)  # codice → motivi
    registrati: list[FornitoreRegistrato] = field(default_factory=list)


def costruisci_da_registro(fornitori: list[FornitoreRegistrato], cfg: Settings, *,
                           adesso: datetime,
                           trasporti: dict[str, httpx.AsyncBaseTransport] | None = None,
                           trasporto_revoca: httpx.AsyncBaseTransport | None = None
                           ) -> EsitoCostruzione:
    """ServizioFirma dai fornitori registrati (vedi modulo)."""
    from app.services.registro import carica_radici

    contesto = Contesto(cfg, adesso, cfg.firma_segreti_json.get_secret_value(),
                        dict(trasporti or {}))
    problemi: dict[str, list[str]] = {}
    firme: list[FornitoreFirma] = []
    marche: list[FornitoreMarca] = []
    for f in sorted(fornitori, key=lambda x: (x.ruolo, x.priorita, x.codice)):
        motivi: list[str] = []
        try:
            f = FornitoreRegistrato(f.codice, f.ruolo, f.tipo, f.nome, f.priorita,
                                    valida_configurazione(f.ruolo, f.tipo, f.configurazione,
                                                          produzione=cfg.in_produzione),
                                    f.versione)
        except ConfigurazioneFornitoreNonValida as exc:
            motivi += exc.motivi
        try:
            mancanti = segreti_mancanti(f.tipo, contesto.segreti(f.codice))
            # revisione R13a (2), (3): segreti legati agli host del Secret, https
            motivi += controlla_deploy(f.tipo, f.configurazione, contesto.segreti(f.codice),
                                       contesto.vincoli(f.codice),
                                       produzione=cfg.in_produzione)
        except ValueError as exc:
            mancanti = []
            motivi.append(str(exc))
        if mancanti:
            motivi.append("segreti mancanti in FIRMA_SEGRETI_JSON: " + ", ".join(mancanti))
        costruttore = CATALOGO.get((f.ruolo, f.tipo))
        if costruttore is None:
            motivi.append(f"tipo {f.tipo} non gestito per il ruolo {f.ruolo}")
        if motivi:
            problemi[f.codice] = motivi
            logger.error("fornitore_firma_non_costruito", codice=f.codice, motivi=motivi[:5])
            continue
        try:
            adattatore = costruttore(f, contesto)  # type: ignore[misc]
        except (ErroreFirma, ValueError, KeyError) as exc:
            problemi[f.codice] = [str(getattr(exc, "messaggio", exc))[:300]]
            logger.error("fornitore_firma_non_costruito", codice=f.codice,
                         errore=type(exc).__name__)
            continue
        (firme if f.ruolo == "FIRMA" else marche).append(adattatore)
    radici_sviluppo: list[x509.Certificate] = []
    if contesto._chiavi_sviluppo is not None:
        from app.services.sviluppo import a_asn1

        radici_sviluppo = [a_asn1(contesto._chiavi_sviluppo.ca_cert)]
    marca: FornitoreMarca | None = MarcaConRipiego(marche) if marche else None
    servizio = ServizioFirma(
        firme, marca, produzione=cfg.in_produzione,
        radici_firma=carica_radici(cfg.firma_radici_pem),
        radici_marca=carica_radici(cfg.tsa_radici_pem),
        radici_sviluppo=radici_sviluppo, trasporto_revoca=trasporto_revoca,
        configurazione={"origine": "registro", "impronta": impronta_configurazione(fornitori)})
    return EsitoCostruzione(servizio, problemi, list(fornitori))


def problemi_avvio(cfg: Settings, fornitori: list[FornitoreRegistrato] | None) -> list[str]:
    """In produzione: il servizio non parte senza DATABASE_URL o con un fornitore
    di SVILUPPO attivo nel registro (controllo d'avvio, §19.4)."""
    problemi: list[str] = []
    if not cfg.in_produzione:
        return problemi
    if not cfg.database_url:
        problemi.append("DATABASE_URL obbligatorio in produzione: il registro dei fornitori "
                        "di firma si legge dal database")
    if not cfg.redis_url:
        # R16 (VD-C-168): senza Redis il limite delle prove e delle firme personali
        # varrebbe per replica (N repliche = N volte il limite)
        problemi.append("REDIS_URL obbligatorio in produzione: limiti condivisi delle prove "
                        "di connessione e delle firme personali")
    for f in fornitori or []:
        if f.tipo == "SVILUPPO":
            problemi.append(f"fornitore {f.codice} di SVILUPPO attivo nel registro: vietato in "
                            "produzione")
    return problemi


class GestoreConfigurazione:
    """Servizio di firma costruito dal registro, ricostruito quando cambia."""

    def __init__(self, cfg: Settings, *, adesso: Callable[[], datetime] | None = None,
                 trasporti: dict[str, httpx.AsyncBaseTransport] | None = None,
                 trasporto_revoca: httpx.AsyncBaseTransport | None = None,
                 lettore: Callable[[], Any] | None = None) -> None:
        self.cfg = cfg
        self._adesso = adesso or (lambda: datetime.now(UTC))
        self._trasporti = trasporti
        self._trasporto_revoca = trasporto_revoca
        self._lettore = lettore or leggi_registro
        self._esito: EsitoCostruzione | None = None
        self._impronta: str | None = None
        self._scade = 0.0
        self._lock = asyncio.Lock()
        self.ultimo_errore: str | None = None
        self.ultima_lettura: datetime | None = None

    async def esito(self) -> EsitoCostruzione:
        async with self._lock:
            if self._esito is not None and time.monotonic() < self._scade:
                return self._esito
            try:
                fornitori = await self._lettore()
            except Exception as exc:  # noqa: BLE001 — DB non raggiungibile o non inizializzato
                from libs.shared.db import DBNonInizializzato

                if isinstance(exc, DBNonInizializzato) and not self.cfg.database_url:
                    fornitori = []
                else:
                    self.ultimo_errore = type(exc).__name__
                    logger.error("registro_fornitori_non_leggibile", errore=type(exc).__name__)
                    if self._esito is not None:
                        self._scade = time.monotonic() + min(
                            5.0, self.cfg.firma_configurazione_cache_secondi)
                        return self._esito
                    raise ConfigurazioneNonDisponibile(
                        "registro dei fornitori di firma non leggibile") from exc
            self.ultimo_errore = None
            self.ultima_lettura = self._adesso()
            impronta = impronta_configurazione(fornitori)
            if self._esito is None or impronta != self._impronta:
                if fornitori:
                    nuovo = costruisci_da_registro(
                        fornitori, self.cfg, adesso=self._adesso(), trasporti=self._trasporti,
                        trasporto_revoca=self._trasporto_revoca)
                else:
                    from app.services.registro import costruisci_servizio

                    nuovo = EsitoCostruzione(costruisci_servizio(self.cfg, adesso=self._adesso()))
                    nuovo.servizio.configurazione = {"origine": "ambiente"}
                if self._esito is not None:
                    for f in self._esito.servizio.fornitori_firma:
                        await f.chiudi()
                self._esito, self._impronta = nuovo, impronta
                logger.info("registro_fornitori_caricato", fornitori=len(fornitori),
                            problemi=len(nuovo.problemi))
            self._scade = time.monotonic() + self.cfg.firma_configurazione_cache_secondi
            return self._esito

    async def servizio(self) -> ServizioFirma:
        return (await self.esito()).servizio

    async def personale(self, codice: str, credential_id: str, certificato_sha256: str, *,
                        otp: str = "", pin: str = "") -> tuple[ServizioFirma, Any]:
        """R16 (VD-C-166): servizio di UNA firma personale con la credenziale della persona
        presso il fornitore CSC ``codice`` (attivo e costruito senza problemi dal registro:
        stessi controlli di configurazione, segreti e host del sigillo) e la marca del
        servizio. ``FornitoreNonRegistrato`` se il fornitore non è utilizzabile."""
        from app.services.fornitori import FornitoreNonRegistrato
        from app.services.personale import FornitoreFirmaPersonale

        esito = await self.esito()
        registrato = next((r for r in esito.registrati if r.codice == codice
                           and r.ruolo == "FIRMA" and r.tipo == "CSC"), None)
        costruito = any(f.codice == codice for f in esito.servizio.fornitori_firma)
        if registrato is None or codice in esito.problemi or not costruito:
            raise FornitoreNonRegistrato(f"fornitore CSC {codice} non attivo o non costruito")
        conf = valida_configurazione("FIRMA", "CSC", {**registrato.configurazione,
                                                      "credential_id": credential_id},
                                     produzione=self.cfg.in_produzione)
        contesto = Contesto(self.cfg, self._adesso(),
                            self.cfg.firma_segreti_json.get_secret_value(),
                            dict(self._trasporti or {}))
        base = _csc(FornitoreRegistrato(registrato.codice, "FIRMA", "CSC", registrato.nome,
                                        registrato.priorita, conf, registrato.versione),
                    contesto)
        assert isinstance(base, FornitoreFirmaCSC)
        personale = FornitoreFirmaPersonale(base, otp=otp, pin=pin,
                                            certificato_sha256=certificato_sha256)
        principale = esito.servizio
        servizio = ServizioFirma(
            [personale], principale.fornitore_marca, produzione=principale.produzione,
            radici_firma=list(principale._radici_firma),
            radici_marca=list(principale._radici_marca),
            trasporto_revoca=self._trasporto_revoca or principale._trasporto_revoca,
            configurazione={"origine": "personale", "fornitore": codice})
        return servizio, personale


async def parametri_firma() -> dict[str, Any]:
    """Livello e algoritmo dal motore (gruppo «firma_qualificata»)."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import (
        FIRMA_ALGORITMI_AMMESSI,
        FIRMA_ALGORITMO_IMPRONTA,
        FIRMA_LIVELLO_DOCUMENTI,
    )

    v = await parametri([FIRMA_LIVELLO_DOCUMENTI, FIRMA_ALGORITMO_IMPRONTA,
                         FIRMA_ALGORITMI_AMMESSI])
    return {"livello": str(v[FIRMA_LIVELLO_DOCUMENTI]),
            "algoritmo": str(v[FIRMA_ALGORITMO_IMPRONTA]),
            "algoritmi_ammessi": list(v[FIRMA_ALGORITMI_AMMESSI])}


__all__ = ["CATALOGO", "Contesto", "EsitoCostruzione", "GestoreConfigurazione",
           "costruisci_da_registro", "leggi_registro", "parametri_firma", "problemi_avvio",
           "radici_da_pem"]
