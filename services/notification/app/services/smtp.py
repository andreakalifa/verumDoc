# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/services/smtp.py
Invio reale via SMTP — VD-C-024, REGOLE §20.1 e §21.

Un solo client SMTP (aiosmtplib). Sicurezza del canale esplicita:
  - porta 465: TLS implicito (SMTPS);
  - porta 587 (e ogni altra): STARTTLS obbligatorio — se il server non lo offre
    l'invio fallisce, nessuna degradazione in chiaro;
  - «nessuna»: solo fuori produzione (server di sviluppo locali).
Il certificato del server si verifica sempre (CA di sistema o SMTP_CA_PEM).

L'esito distingue errori temporanei (4xx, rete, timeout: si ritenta) da
permanenti (5xx, autenticazione, configurazione: non si ritenta).
"""

from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import aiosmtplib
import structlog

from app.config import Settings

logger = structlog.get_logger(__name__)

INVIATA = "inviata"
TEMPORANEO = "errore_temporaneo"
PERMANENTE = "errore_permanente"
NON_CONFIGURATA = "non_configurata"


@dataclass(frozen=True)
class ConfigSMTP:
    host: str
    porta: int
    sicurezza: str  # "starttls" | "tls" | "nessuna"
    mittente: str
    nome_mittente: str = ""
    utente: str = ""
    password: str = ""
    timeout: float = 30.0
    ca_pem: str = ""


@dataclass(frozen=True)
class EsitoInvio:
    esito: str
    messaggio: str
    codice_smtp: int | None = None
    message_id: str | None = None

    @property
    def ritentabile(self) -> bool:
        return self.esito == TEMPORANEO


def sicurezza_effettiva(porta: int, scelta: str) -> str:
    """auto → 465 = TLS implicito, altrimenti STARTTLS."""
    if scelta != "auto":
        return scelta
    return "tls" if porta == 465 else "starttls"


def problemi_configurazione(cfg: Settings) -> list[str]:
    """Controlli puri: cosa manca o è pericoloso nella configurazione della posta."""
    problemi: list[str] = []
    if not cfg.smtp_host:
        problemi.append("SMTP_HOST non impostato")
    if not cfg.smtp_mittente or not indirizzo_plausibile(cfg.smtp_mittente):
        problemi.append("SMTP_MITTENTE non impostato o non valido")
    sic = sicurezza_effettiva(cfg.smtp_port, cfg.smtp_sicurezza)
    if sic == "nessuna" and cfg.in_produzione:
        problemi.append("SMTP_SICUREZZA=nessuna vietata in produzione")
    if sic == "tls" and cfg.smtp_port == 587:
        problemi.append("porta 587 con TLS implicito: usare STARTTLS (SMTP_SICUREZZA=starttls)")
    return problemi


def config_da_settings(cfg: Settings) -> ConfigSMTP | None:
    """ConfigSMTP se la posta è configurata e sicura, altrimenti None («non configurata»)."""
    if problemi_configurazione(cfg):
        return None
    return ConfigSMTP(
        host=cfg.smtp_host,
        porta=cfg.smtp_port,
        sicurezza=sicurezza_effettiva(cfg.smtp_port, cfg.smtp_sicurezza),
        mittente=cfg.smtp_mittente,
        nome_mittente=cfg.smtp_nome_mittente,
        utente=cfg.smtp_utente,
        password=cfg.smtp_password.get_secret_value(),
        timeout=cfg.smtp_timeout_secondi,
        ca_pem=cfg.smtp_ca_pem,
    )


_DOMINI_SEGNAPOSTO = (".local", ".localhost", ".invalid", ".test", ".example", ".internal")
_DOMINI_ESEMPIO = ("example.com", "example.org", "example.net")


def indirizzo_plausibile(indirizzo: str) -> bool:
    """Indirizzo sintatticamente valido e non segnaposto (es. tenant-x@verumdoc.local)."""
    a = (indirizzo or "").strip().lower()
    if a.count("@") != 1 or " " in a or "\n" in a or "\r" in a:
        return False
    locale, dominio = a.split("@")
    if not locale or "." not in dominio or dominio.startswith(".") or dominio.endswith("."):
        return False
    return not (dominio.endswith(_DOMINI_SEGNAPOSTO) or dominio in _DOMINI_ESEMPIO)


def componi(
    cfg: ConfigSMTP, destinatario: str, oggetto: str, testo: str, html: str | None = None
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = (
        formataddr((cfg.nome_mittente, cfg.mittente)) if cfg.nome_mittente else cfg.mittente
    )
    msg["To"] = destinatario
    msg["Subject"] = oggetto
    dominio = cfg.mittente.split("@", 1)[1] if "@" in cfg.mittente else None
    msg["Message-ID"] = make_msgid(domain=dominio)
    msg.set_content(testo)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _contesto_tls(cfg: ConfigSMTP) -> ssl.SSLContext:
    """Contesto TLS verificato. ``ca_pem``: testo PEM della CA privata (R13b: la
    stessa funzione serve il canale PEC, che lo riceve dalla console) oppure, come
    prima, il percorso di un file di CA."""
    testo = (cfg.ca_pem or "").strip()
    if "-----BEGIN" in testo:
        ctx = ssl.create_default_context(cadata=testo)
    else:
        ctx = ssl.create_default_context(cafile=testo or None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


async def invia_email(cfg: ConfigSMTP, msg: EmailMessage) -> EsitoInvio:
    """Invia un messaggio e classifica l'esito. Non solleva eccezioni."""
    inizio = time.monotonic()
    kwargs: dict = {
        "hostname": cfg.host,
        "port": cfg.porta,
        "timeout": cfg.timeout,
        "username": cfg.utente or None,
        "password": cfg.password or None,
    }
    if cfg.sicurezza == "tls":
        kwargs.update(use_tls=True, start_tls=False, tls_context=_contesto_tls(cfg))
    elif cfg.sicurezza == "starttls":
        kwargs.update(use_tls=False, start_tls=True, tls_context=_contesto_tls(cfg))
    else:
        kwargs.update(use_tls=False, start_tls=False)
    try:
        await aiosmtplib.send(msg, **kwargs)
        esito = EsitoInvio(INVIATA, "accettata dal server SMTP", 250, msg["Message-ID"])
    except aiosmtplib.SMTPRecipientsRefused as exc:
        codici = [r.code for r in exc.recipients]
        temporaneo = all(400 <= c < 500 for c in codici)
        esito = EsitoInvio(
            TEMPORANEO if temporaneo else PERMANENTE,
            f"destinatario rifiutato ({', '.join(map(str, codici))})",
            codici[0] if codici else None,
        )
    except aiosmtplib.SMTPAuthenticationError as exc:
        esito = EsitoInvio(PERMANENTE, "autenticazione SMTP rifiutata", exc.code)
    except aiosmtplib.SMTPResponseException as exc:
        esito = EsitoInvio(
            TEMPORANEO if 400 <= exc.code < 500 else PERMANENTE,
            f"risposta SMTP {exc.code}",
            exc.code,
        )
    except ssl.SSLCertVerificationError:
        esito = EsitoInvio(PERMANENTE, "certificato del server SMTP non attendibile")
    except aiosmtplib.SMTPException as exc:
        # STARTTLS non offerto, connessione caduta, timeout...
        testo = str(exc).lower()
        if _da_certificato(exc):
            # R13b: aiosmtplib incapsula l'errore TLS; ritentare non lo cambia
            esito = EsitoInvio(PERMANENTE, "certificato del server SMTP non attendibile")
        elif "starttls" in testo and "not supported" in testo:
            esito = EsitoInvio(
                PERMANENTE, "il server non offre STARTTLS: invio in chiaro rifiutato"
            )
        else:
            esito = EsitoInvio(TEMPORANEO, f"errore SMTP: {type(exc).__name__}")
    except (OSError, TimeoutError) as exc:
        esito = EsitoInvio(TEMPORANEO, f"server SMTP non raggiungibile: {type(exc).__name__}")
    # REGOLE §20.4 e §17.4: una riga per chiamata, destinatario mascherato, nessun segreto
    logger.info(
        "chiamata_integrazione",
        servizio="smtp",
        operazione="send",
        esito=esito.esito,
        codice=esito.codice_smtp,
        durata_ms=int((time.monotonic() - inizio) * 1000),
        destinatario=maschera(str(msg["To"])),
    )
    return esito


def _da_certificato(exc: BaseException) -> bool:
    """True se l'errore nasce da un certificato non verificato (anche incapsulato)."""
    visti = 0
    corrente: BaseException | None = exc
    while corrente is not None and visti < 5:
        if isinstance(corrente, ssl.SSLCertVerificationError) or \
                "CERTIFICATE_VERIFY_FAILED" in str(corrente):
            return True
        corrente = corrente.__cause__ or corrente.__context__
        visti += 1
    return False


def maschera(indirizzo: str) -> str:
    """m***@dominio.it"""
    if "@" not in indirizzo:
        return "***"
    locale, dominio = indirizzo.split("@", 1)
    return f"{locale[:1]}***@{dominio}"
