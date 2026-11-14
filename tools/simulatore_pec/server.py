# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Simulatore PEC locale: SMTP (invio) + IMAP (lettura delle ricevute) con ricevute
finte firmate dal gestore di prova (R13b, VD-C-078). SOLO collaudo, test ed e2e.

Comportamento del «gestore» per destinatario (parte locale dell'indirizzo):
  - contiene ``nonaccetta``  → avviso di NON ACCETTAZIONE (nessuna consegna);
  - contiene ``errore``      → ACCETTAZIONE e avviso di MANCATA CONSEGNA;
  - contiene ``silenzio``    → solo ACCETTAZIONE (consegna mai confermata);
  - altrimenti               → ACCETTAZIONE e AVVENUTA CONSEGNA.
Il mittente deve essere la casella autenticata (come presso i gestori veri); le
ricevute entrano nella casella del mittente, letta via IMAP. ``inietta`` mette
nella casella messaggi arbitrari (ricevute false o estranee per le prove).

IMAP: sottoinsieme di IMAP4rev1 sufficiente al lavoratore (CAPABILITY, LOGIN,
SELECT/EXAMINE, UID SEARCH, UID FETCH di RFC822.SIZE e BODY.PEEK[], NOOP, LOGOUT);
TLS implicito se si passa un contesto SSL.
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import re
import ssl
from dataclasses import dataclass, field
from typing import Any

from aiosmtpd.smtp import SMTP, AuthResult, LoginPassword

from tools.simulatore_pec.gestore import Pki, ricevuta


@dataclass
class Casella:
    uidvalidity: int = 1
    messaggi: list[tuple[int, bytes]] = field(default_factory=list)
    prossimo: int = 1

    def aggiungi(self, grezzo: bytes) -> int:
        uid = self.prossimo
        self.messaggi.append((uid, grezzo))
        self.prossimo += 1
        return uid


class _Gestore:
    def __init__(self, sim: SimulatorePec) -> None:
        self.sim = sim

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:  # noqa: N802
        if not getattr(session, "authenticated", False):
            return "530 5.7.0 Authentication required"
        utente = self.sim.utente_di.get(id(session))
        mittente = (envelope.mail_from or "").lower()
        if utente is None or mittente != utente:
            return "553 5.7.1 Il mittente deve essere la casella autenticata"
        if self.sim.rifiuta_temporaneo > 0:
            self.sim.rifiuta_temporaneo -= 1
            return "451 4.3.0 Gestore temporaneamente non disponibile"
        self.sim.ricevi(mittente, [r.lower() for r in envelope.rcpt_tos],
                        bytes(envelope.original_content or envelope.content))
        return "250 2.0.0 Ok: messaggio preso in carico"


class SimulatorePec:
    def __init__(self, pki: Pki | None = None, *, utenti: dict[str, str] | None = None) -> None:
        self.pki = pki or Pki()
        self.utenti = {k.lower(): v for k, v in (utenti or {}).items()}
        self.caselle: dict[str, Casella] = {}
        self.inviati: list[tuple[str, list[str], bytes]] = []
        self.utente_di: dict[int, str] = {}
        self.rifiuta_temporaneo = 0
        self._server: list[asyncio.base_events.Server] = []
        self.porta_smtp = 0
        self.porta_imap = 0

    def casella(self, indirizzo: str) -> Casella:
        return self.caselle.setdefault(indirizzo.lower(), Casella())

    def inietta(self, indirizzo: str, grezzo: bytes) -> int:
        return self.casella(indirizzo).aggiungi(grezzo)

    def ricevi(self, mittente: str, destinatari: list[str], grezzo: bytes) -> None:
        self.inviati.append((mittente, destinatari, grezzo))
        msg = email.message_from_bytes(grezzo, policy=email.policy.compat32)
        msgid = str(msg.get("Message-ID") or "").strip()
        oggetto = str(msg.get("Subject") or "")
        casella = self.casella(mittente)
        nonaccetta = [d for d in destinatari if "nonaccetta" in d.split("@")[0]]
        if nonaccetta:
            casella.aggiungi(ricevuta(self.pki, "NON_ACCETTAZIONE", msgid=msgid,
                                      mittente=mittente, destinatari=destinatari,
                                      oggetto=oggetto, errore="altro",
                                      errore_esteso="Destinatario non ammesso dal gestore"))
            return
        casella.aggiungi(ricevuta(self.pki, "ACCETTAZIONE", msgid=msgid, mittente=mittente,
                                  destinatari=destinatari, oggetto=oggetto))
        for d in destinatari:
            locale = d.split("@")[0]
            if "silenzio" in locale:
                continue
            if "errore" in locale:
                casella.aggiungi(ricevuta(self.pki, "ERRORE_CONSEGNA", msgid=msgid,
                                          mittente=mittente, destinatari=destinatari,
                                          oggetto=oggetto, consegna=d, errore="no-dest",
                                          errore_esteso="5.1.1 - Utente non esistente"))
            else:
                casella.aggiungi(ricevuta(self.pki, "AVVENUTA_CONSEGNA", msgid=msgid,
                                          mittente=mittente, destinatari=destinatari,
                                          oggetto=oggetto, consegna=d))

    # ── SMTP ──────────────────────────────────────────────────────────
    def _autentica(self, server: Any, session: Any, envelope: Any, mechanism: str,
                   auth_data: Any) -> AuthResult:
        if not isinstance(auth_data, LoginPassword):
            return AuthResult(success=False, handled=False)
        utente = auth_data.login.decode("utf-8", "replace").lower()
        if self.utenti.get(utente) != auth_data.password.decode("utf-8", "replace"):
            return AuthResult(success=False, handled=False)
        self.utente_di[id(session)] = utente
        return AuthResult(success=True)

    async def avvia(self, host: str = "127.0.0.1", *, porta_smtp: int = 0, porta_imap: int = 0,
                    ssl_server: ssl.SSLContext | None = None) -> None:
        import warnings

        # simulatore: AUTH anche in chiaro (in produzione il canale è solo cifrato)
        warnings.filterwarnings("ignore", message="Requiring AUTH while not requiring TLS")
        ciclo = asyncio.get_running_loop()
        gestore = _Gestore(self)

        def protocollo_smtp() -> SMTP:
            return SMTP(gestore, hostname="smtp.simulatore-pec.test", authenticator=self._autentica,
                        auth_required=True, auth_require_tls=False)

        smtp = await ciclo.create_server(protocollo_smtp, host, porta_smtp, ssl=ssl_server)
        imap = await asyncio.start_server(self._sessione_imap, host, porta_imap, ssl=ssl_server)
        self._server = [smtp, imap]
        self.porta_smtp = smtp.sockets[0].getsockname()[1]
        self.porta_imap = imap.sockets[0].getsockname()[1]

    async def ferma(self) -> None:
        for s in self._server:
            s.close()
            await s.wait_closed()

    # ── IMAP ──────────────────────────────────────────────────────────
    async def _sessione_imap(self, lettore: asyncio.StreamReader,
                             scrittore: asyncio.StreamWriter) -> None:
        def scrivi(riga: str | bytes) -> None:
            scrittore.write(riga if isinstance(riga, bytes) else riga.encode("utf-8"))

        scrivi("* OK [CAPABILITY IMAP4rev1] Simulatore PEC pronto\r\n")
        utente: str | None = None
        selezionata: Casella | None = None
        try:
            while True:
                linea = await lettore.readline()
                if not linea:
                    break
                parti = linea.decode("utf-8", "replace").strip().split(" ", 2)
                if len(parti) < 2:
                    continue
                tag, comando = parti[0], parti[1].upper()
                argomenti = parti[2] if len(parti) > 2 else ""
                if comando == "CAPABILITY":
                    scrivi("* CAPABILITY IMAP4rev1\r\n" + f"{tag} OK CAPABILITY completato\r\n")
                elif comando == "LOGIN":
                    m = re.match(r'"?([^" ]+)"?\s+"?(.*?)"?$', argomenti)
                    if m and self.utenti.get(m.group(1).lower()) == m.group(2):
                        utente = m.group(1).lower()
                        scrivi(f"{tag} OK LOGIN completato\r\n")
                    else:
                        scrivi(f"{tag} NO [AUTHENTICATIONFAILED] credenziali non valide\r\n")
                elif comando in ("SELECT", "EXAMINE"):
                    if utente is None:
                        scrivi(f"{tag} NO non autenticato\r\n")
                        continue
                    selezionata = self.casella(utente)
                    scrivi(f"* {len(selezionata.messaggi)} EXISTS\r\n* 0 RECENT\r\n"
                           f"* OK [UIDVALIDITY {selezionata.uidvalidity}] UID validi\r\n"
                           f"* OK [UIDNEXT {selezionata.prossimo}] prossimo\r\n"
                           f"{tag} OK [READ-ONLY] {comando} completato\r\n")
                elif comando == "UID" and selezionata is not None:
                    self._uid(tag, argomenti, selezionata, scrivi)
                elif comando == "NOOP":
                    scrivi(f"{tag} OK NOOP\r\n")
                elif comando == "LOGOUT":
                    scrivi("* BYE arrivederci\r\n" + f"{tag} OK LOGOUT completato\r\n")
                    await scrittore.drain()
                    break
                else:
                    scrivi(f"{tag} BAD comando non supportato\r\n")
                await scrittore.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            scrittore.close()

    @staticmethod
    def _uid(tag: str, argomenti: str, casella: Casella, scrivi: Any) -> None:
        sotto, _, resto = argomenti.partition(" ")
        sotto = sotto.upper()
        if sotto == "SEARCH":
            m = re.search(r"UID (\d+):\*", resto, re.IGNORECASE)
            dal = int(m.group(1)) if m else 1
            uids = [u for u, _ in casella.messaggi if u >= dal]
            if not uids and casella.messaggi:
                # come i server veri, «n:*» comprende sempre l'ultimo messaggio
                uids = [casella.messaggi[-1][0]]
            scrivi("* SEARCH" + "".join(f" {u}" for u in uids) + "\r\n" +
                   f"{tag} OK SEARCH completato\r\n")
        elif sotto == "FETCH":
            insieme, _, cosa = resto.partition(" ")
            richiesti = {int(x) for x in insieme.split(",") if x.isdigit()}
            for posizione, (uid, grezzo) in enumerate(casella.messaggi, start=1):
                if uid not in richiesti:
                    continue
                if "RFC822.SIZE" in cosa.upper():
                    scrivi(f"* {posizione} FETCH (UID {uid} RFC822.SIZE {len(grezzo)})\r\n")
                else:
                    scrivi(f"* {posizione} FETCH (UID {uid} BODY[] {{{len(grezzo)}}}\r\n".encode()
                           + grezzo + b")\r\n")
            scrivi(f"{tag} OK FETCH completato\r\n")
        else:
            scrivi(f"{tag} BAD UID {sotto} non supportato\r\n")


__all__ = ["Casella", "SimulatorePec"]
