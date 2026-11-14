# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/infrastructure/pec_canale.py — adattatori dei canali PEC
(R13b, VD-C-078; REGOLE §31 R13a (1): interfaccia unica con registro dei tipi).

Interfaccia ``CanalePec``:
  - ``invia(destinatario, oggetto, testo, message_id)`` → ``EsitoInvio`` (stesse
    classi d'esito della posta: inviata, errore temporaneo da ritentare, errore
    permanente);
  - ``leggi(dopo_uid, uidvalidity)`` → ``LetturaCasella``: messaggi nuovi della
    casella (UID crescenti), con i soli byte sotto il limite di dimensione.

Registro ``COSTRUTTORI`` per tipo (``SMTP_IMAP``; ``SVILUPPO`` usa lo stesso
adattatore verso il simulatore locale, rifiutato in produzione). Aggiungere un
gestore con un'interfaccia diversa = un costruttore qui, nessun chiamante cambia.

Segreti legati all'host (R13a, revisione (2)): ``costruisci`` rifiuta un canale con
segreti se SMTP o IMAP puntano a un host fuori dalla voce del Secret, e ogni
connessione lo ricontrolla prima di autenticarsi. TLS sempre verificato (CA di
sistema o ``ca_pem`` della voce del canale in ``PEC_SEGRETI_JSON``), almeno TLS 1.2;
senza cifratura solo fuori produzione (simulatore).
"""
from __future__ import annotations

import asyncio
import imaplib
import re
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any, Protocol

import structlog

from app.services.smtp import PERMANENTE, ConfigSMTP, EsitoInvio, invia_email
from libs.shared.pec.configurazione import (
    CHIAVE_CA,
    TIPO_SMTP_IMAP,
    TIPO_SVILUPPO,
    CanaleRegistrato,
    controlla_deploy,
    credenziali,
    segreti_mancanti,
)

logger = structlog.get_logger(__name__)


class CanaleNonCostruibile(Exception):
    """Il canale non si può usare: ``motivi`` in parole semplici (mai segreti)."""

    def __init__(self, motivi: list[str]) -> None:
        super().__init__("; ".join(motivi))
        self.motivi = motivi


class ErroreCasella(Exception):
    """Lettura della casella non riuscita (rete, autenticazione, protocollo)."""


@dataclass(frozen=True)
class MessaggioCasella:
    uid: int
    dimensione: int
    grezzo: bytes | None   # None = oltre il limite, non scaricato


@dataclass(frozen=True)
class LetturaCasella:
    uidvalidity: int
    messaggi: list[MessaggioCasella] = field(default_factory=list)


class CanalePec(Protocol):
    codice: str
    indirizzo: str

    async def invia(self, destinatario: str, oggetto: str, testo: str,
                    message_id: str) -> EsitoInvio: ...

    async def leggi(self, dopo_uid: int, uidvalidity: int | None, *,
                    massimo: int) -> LetturaCasella: ...


def contesto_tls(ca_pem: str) -> ssl.SSLContext:
    testo = (ca_pem or "").strip()
    ctx = ssl.create_default_context(cadata=testo) if testo else ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


_FETCH_DIMENSIONE = re.compile(rb"UID (\d+) RFC822\.SIZE (\d+)|RFC822\.SIZE (\d+) UID (\d+)")
_FETCH_UID = re.compile(rb"UID (\d+)")


class CanaleSmtpImap:
    """Casella PEC presso un gestore: SMTP autenticato e IMAP (in sola lettura)."""

    def __init__(self, canale: CanaleRegistrato, segreti: Mapping[str, str],
                 vincoli: Mapping[str, tuple[str, ...]], *, produzione: bool,
                 imap_fabbrica: Callable[..., Any] | None = None) -> None:
        conf = canale.configurazione
        motivi = controlla_deploy(canale.tipo, conf, segreti, vincoli, produzione=produzione)
        motivi += [f"segreto mancante nel Secret: {n}" for n in
                   segreti_mancanti(canale.tipo, segreti)]
        if motivi:
            raise CanaleNonCostruibile(motivi)
        self.codice = canale.codice
        self.tipo = canale.tipo
        self.indirizzo = str(conf["indirizzo"]).lower()
        self._conf = dict(conf)
        self._utente = segreti.get("utente") or self.indirizzo
        self._password = segreti.get("password") or ""
        self._host_ammessi = set(vincoli.get("host") or ()) if credenziali(segreti) else None
        # revisione R13b, rilievo 3: CA dei server SOLO dalla voce del Secret
        self._ca_pem = str(segreti.get(CHIAVE_CA) or "")
        self._imap_fabbrica = imap_fabbrica

    def _controlla_host(self, host: str) -> None:
        """Ricontrollo a ogni connessione: la password va solo agli host del Secret."""
        if self._host_ammessi is not None and host.lower() not in self._host_ammessi:
            raise CanaleNonCostruibile([f"{host}: host non legato ai segreti del canale"])

    # ── invio ────────────────────────────────────────────────────────
    def componi(self, destinatario: str, oggetto: str, testo: str,
                message_id: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.indirizzo
        msg["To"] = destinatario
        msg["Subject"] = oggetto
        msg["Date"] = format_datetime(datetime.now(UTC))
        msg["Message-ID"] = message_id
        # DM 2/11/2005: tipo di ricevuta di consegna richiesto dal mittente
        msg["X-TipoRicevuta"] = str(self._conf.get("tipo_ricevuta") or "completa")
        msg.set_content(testo)
        return msg

    async def invia(self, destinatario: str, oggetto: str, testo: str,
                    message_id: str) -> EsitoInvio:
        host = str(self._conf["smtp_host"])
        try:
            self._controlla_host(host)
        except CanaleNonCostruibile as exc:
            return EsitoInvio(PERMANENTE, str(exc))
        cfg = ConfigSMTP(host=host, porta=int(self._conf["smtp_porta"]),
                         sicurezza=str(self._conf["smtp_sicurezza"]), mittente=self.indirizzo,
                         utente=self._utente, password=self._password,
                         timeout=float(self._conf.get("timeout_secondi") or 30),
                         ca_pem=self._ca_pem)
        return await invia_email(cfg, self.componi(destinatario, oggetto, testo, message_id))

    # ── lettura delle ricevute ──────────────────────────────────────
    def _connetti(self) -> imaplib.IMAP4:
        host = str(self._conf["imap_host"])
        self._controlla_host(host)
        porta = int(self._conf["imap_porta"])
        timeout = float(self._conf.get("timeout_secondi") or 30)
        if self._imap_fabbrica is not None:
            return self._imap_fabbrica(host, porta, timeout)
        if self._conf.get("imap_sicurezza") == "tls":
            return imaplib.IMAP4_SSL(host, porta, ssl_context=contesto_tls(self._ca_pem),
                                     timeout=timeout)
        return imaplib.IMAP4(host, porta, timeout=timeout)

    def _leggi_sincrono(self, dopo_uid: int, uidvalidity: int | None,
                        massimo: int) -> LetturaCasella:
        limite = int(self._conf.get("dimensione_massima_kb") or 10240) * 1024
        try:
            conn = self._connetti()
        except CanaleNonCostruibile:
            raise
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ErroreCasella(f"connessione IMAP non riuscita: {type(exc).__name__}") from None
        try:
            try:
                conn.login(self._utente, self._password)
            except imaplib.IMAP4.error:
                raise ErroreCasella("autenticazione IMAP rifiutata") from None
            stato, _ = conn.select(_virgolette(str(self._conf.get("cartella") or "INBOX")),
                                   readonly=True)
            if stato != "OK":
                raise ErroreCasella("cartella IMAP non selezionabile")
            _, valori = conn.response("UIDVALIDITY")
            try:
                validita = int(valori[0]) if valori and valori[0] else 0
            except (TypeError, ValueError):
                validita = 0
            if not validita:
                raise ErroreCasella("UIDVALIDITY assente dalla risposta del server")
            da = dopo_uid + 1 if uidvalidity == validita else 1
            stato, dati = conn.uid("SEARCH", None, f"UID {da}:*")
            if stato != "OK":
                raise ErroreCasella("ricerca IMAP non riuscita")
            uids = sorted(int(u) for u in (dati[0] or b"").split() if int(u) >= da)[:massimo]
            if not uids:
                return LetturaCasella(validita, [])
            stato, dati = conn.uid("FETCH", ",".join(str(u) for u in uids), "(RFC822.SIZE)")
            dimensioni: dict[int, int] = {}
            for voce in dati or []:
                testo = voce if isinstance(voce, bytes) else (voce[0] if voce else b"")
                m = _FETCH_DIMENSIONE.search(testo or b"")
                if m:
                    uid = int(m.group(1) or m.group(4))
                    dimensioni[uid] = int(m.group(2) or m.group(3))
            messaggi: list[MessaggioCasella] = []
            for uid in uids:
                dimensione = dimensioni.get(uid, 0)
                if dimensione > limite:
                    messaggi.append(MessaggioCasella(uid, dimensione, None))
                    continue
                stato, dati = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                grezzo = next((v[1] for v in dati or [] if isinstance(v, tuple)
                               and _FETCH_UID.search(v[0] or b"")), None)
                if stato != "OK" or grezzo is None:
                    raise ErroreCasella(f"messaggio UID {uid} non letto")
                if len(grezzo) > limite:
                    messaggi.append(MessaggioCasella(uid, len(grezzo), None))
                    continue
                messaggi.append(MessaggioCasella(uid, len(grezzo), bytes(grezzo)))
            return LetturaCasella(validita, messaggi)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ErroreCasella(f"lettura IMAP interrotta: {type(exc).__name__}") from None
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    async def leggi(self, dopo_uid: int, uidvalidity: int | None, *,
                    massimo: int = 100) -> LetturaCasella:
        return await asyncio.to_thread(self._leggi_sincrono, dopo_uid, uidvalidity, massimo)


def _virgolette(nome: str) -> str:
    return '"' + nome.replace("\\", "\\\\").replace('"', '\\"') + '"'


#: Registro dei tipi: tipo → costruttore(canale, segreti, vincoli, produzione=…)
COSTRUTTORI: dict[str, Callable[..., CanalePec]] = {
    TIPO_SMTP_IMAP: CanaleSmtpImap,
    TIPO_SVILUPPO: CanaleSmtpImap,
}


def costruisci(canale: CanaleRegistrato, segreti: Mapping[str, str],
               vincoli: Mapping[str, tuple[str, ...]], *, produzione: bool) -> CanalePec:
    """Adattatore del canale o ``CanaleNonCostruibile``. Il canale di SVILUPPO è
    vietato in produzione anche qui (terzo livello, dopo console e avvio)."""
    if canale.tipo == TIPO_SVILUPPO and produzione:
        raise CanaleNonCostruibile(["canale di SVILUPPO vietato in produzione"])
    costruttore = COSTRUTTORI.get(canale.tipo)
    if costruttore is None:
        raise CanaleNonCostruibile([f"tipo di canale sconosciuto: {canale.tipo}"])
    return costruttore(canale, segreti, vincoli, produzione=produzione)


__all__ = ["COSTRUTTORI", "CanaleNonCostruibile", "CanalePec", "CanaleSmtpImap",
           "ErroreCasella", "LetturaCasella", "MessaggioCasella", "costruisci"]
