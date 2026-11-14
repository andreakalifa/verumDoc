# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Server SFTP del canale di versamento (asyncssh).

Servizio: sftp-ingest. Riferimenti: piano R03 §3.2; REGOLE §19 (sicurezza),
§14.4 (nessuna informazione su altri enti).

Sicurezza:
  - autenticazione SOLO a chiave pubblica (niente password né keyboard-interactive);
    tipi e lunghezze ammessi da impostazione, ricontrollati a ogni accesso;
    nessun indizio sull'esistenza del nome utente (sempre «chiave richiesta»);
  - accesso solo se account ATTIVO, ente non sospeso o disattivato e utente
    responsabile attivo dello stesso ente (stesse regole di revoca dei token);
  - solo il sottosistema SFTP: niente shell, exec, scp, inoltro di porte,
    agente o X11;
  - chroot nell'area dell'account (``<radice>/<account_id>``); niente
    collegamenti (symlink/hardlink); attributi (permessi, proprietario) ignorati,
    solo gli orari dei file;
  - scrittura solo in ``in/<cartella>/<file>`` e creazione solo di
    ``in/<cartella>`` (nomi validati); ``lavorazione/`` in sola lettura;
    eliminazione ammessa in ``in/``, ``out/``, ``fatti/``, ``scarti/``;
  - quota di spazio per account (impostazione), controllata a ogni scrittura.

R03 f2 (REV-15): ogni creazione, scrittura, rinomina o eliminazione in ``in/``
segnala l'account al lavoratore (``Segnalatore``, a lotti sul database): il
lavoratore guarda solo gli account con attività invece di scandire tutte le
aree a ogni giro. La rinomina in un nome di cartella definitivo è «pronta»
(da guardare subito); il resto si guarda dopo la stabilità.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import posixpath
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncssh
import structlog

from app.domain.chiavi import motivo_chiave_non_ammessa
from app.domain.protocollo import (
    FATTI,
    IN,
    OUT,
    SCARTI,
    nome_cartella_temporanea_valido,
    nome_cartella_valido,
    nome_file_valido,
)
from app.infrastructure import archivio
from app.infrastructure.repository import AccountAccesso
from app.infrastructure.segnalazioni import Segnalatore
from app.metriche import MetricheSftp
from libs.shared.security.proxy_protocol import ProxyNonValido, leggi_intestazione
from libs.shared.security.rete import ammesso

logger = structlog.get_logger(__name__)

#: Ricerca dell'account per nome utente (iniettabile nei test).
CercaAccount = Callable[[str], Awaitable[AccountAccesso | None]]
RegistraAccesso = Callable[..., Awaitable[None]]

_PERMESSI_FILE = 0o640
_PERMESSI_CARTELLA = 0o750
_ELIMINABILI = frozenset({IN, OUT, FATTI, SCARTI})
_SCRITTURA = (asyncssh.FXF_WRITE | asyncssh.FXF_CREAT | asyncssh.FXF_TRUNC
              | asyncssh.FXF_APPEND)


@dataclass
class QuotaAccount:
    """Spazio occupato per account: ricalcolato dal disco ogni ``ricalcolo_secondi``
    e aggiornato a ogni scrittura. Con più repliche del server il limite può
    essere superato al massimo di quanto scritto nell'intervallo di ricalcolo."""

    limite_byte: int
    ricalcolo_secondi: float
    _uso: dict[str, tuple[int, float]] = field(default_factory=dict)

    async def aggiorna(self, account_id: str, area: Path, adesso: float | None = None) -> int:
        ora = time.monotonic() if adesso is None else adesso
        presente = self._uso.get(account_id)
        if presente is None or ora - presente[1] >= self.ricalcolo_secondi:
            uso = await asyncio.to_thread(archivio.uso_disco, area)
            self._uso[account_id] = (uso, ora)
            return uso
        return presente[0]

    def riserva(self, account_id: str, byte: int) -> bool:
        """Aggiunge ``byte`` all'uso se restano entro il limite; False altrimenti."""
        uso, quando = self._uso.get(account_id, (0, 0.0))
        if byte > 0 and uso + byte > self.limite_byte:
            return False
        self._uso[account_id] = (max(0, uso + byte), quando)
        return True


@dataclass
class ContestoServer:
    radice: str
    cerca_account: CercaAccount
    tipi_chiave_ammessi: tuple[str, ...]
    rsa_bit_minimi: int
    quota: QuotaAccount
    metriche: MetricheSftp
    #: Segnalazioni al lavoratore (None: nessuna, es. test del solo server).
    segnalatore: Segnalatore | None = None
    #: R10 (D12): voce d'audit di un accesso (account, ente, ip, esito, motivo);
    #: None nei test del solo protocollo
    registra_accesso: RegistraAccesso | None = None


def _parti(path: bytes) -> tuple[str, ...]:
    """Percorso virtuale (relativo alla radice del chroot) come tuple di nomi."""
    try:
        testo = path.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise asyncssh.SFTPInvalidFilename("Nome non in UTF-8") from exc
    normale = posixpath.normpath(posixpath.join("/", testo))
    return tuple(p for p in normale.split("/") if p)


def _cartella_pacchetto_valida(nome: str) -> bool:
    return nome_cartella_valido(nome) or nome_cartella_temporanea_valido(nome)


def _file_scrivibile(p: tuple[str, ...]) -> bool:
    return (len(p) == 3 and p[0] == IN and _cartella_pacchetto_valida(p[1])
            and nome_file_valido(p[2]))


def _cartella_creabile(p: tuple[str, ...]) -> bool:
    return len(p) == 2 and p[0] == IN and _cartella_pacchetto_valida(p[1])


class AreaSftp(asyncssh.SFTPServer):
    """Sessione SFTP di un account, confinata nella sua area."""

    def __init__(self, chan: asyncssh.SSHServerChannel[bytes], contesto: ContestoServer,
                 account: AccountAccesso) -> None:
        self._contesto = contesto
        self._account = account
        self._area = archivio.area_account(contesto.radice, str(account.id))
        archivio.assicura_area(self._area)
        super().__init__(chan, chroot=os.fsencode(self._area))
        contesto.metriche.sessioni.inc()
        self._aperta = True

    # ── Controlli ───────────────────────────────────────────────────────
    def _nega(self, motivo: str, path: bytes) -> asyncssh.SFTPError:
        self._contesto.metriche.operazioni_negate.labels(motivo).inc()
        logger.info("sftp_operazione_negata", motivo=motivo, account_id=str(self._account.id),
                    percorso=path.decode("utf-8", "replace")[:200])
        if motivo == "quota":
            return asyncssh.SFTPQuotaExceeded("Quota di spazio dell'account superata")
        return asyncssh.SFTPPermissionDenied(
            "Operazione non ammessa: si scrive solo in in/<cartella>/<file> con nomi validi")

    def _segnala(self, *, pronta: bool = False) -> None:
        if self._contesto.segnalatore is not None:
            self._contesto.segnalatore.segnala(self._account.id, self._account.tenant_id,
                                               pronta=pronta)

    # ── File ────────────────────────────────────────────────────────────
    async def open(self, path: bytes, pflags: int, attrs: asyncssh.SFTPAttrs) -> object:
        if pflags & _SCRITTURA:
            if not _file_scrivibile(_parti(path)):
                raise self._nega("percorso", path)
            self._segnala()
            await self._contesto.quota.aggiorna(str(self._account.id), self._area)
            # permessi e proprietario del client ignorati
            return super().open(path, pflags, asyncssh.SFTPAttrs(permissions=_PERMESSI_FILE))
        return super().open(path, pflags, asyncssh.SFTPAttrs())

    def write(self, file_obj: object, offset: int, data: bytes) -> int:
        dimensione = os.fstat(file_obj.fileno()).st_size  # type: ignore[attr-defined]
        crescita = max(0, offset + len(data) - dimensione)
        if not self._contesto.quota.riserva(str(self._account.id), crescita):
            raise self._nega("quota", b"")
        scritti = super().write(file_obj, offset, data)
        self._contesto.metriche.byte_ricevuti.inc(len(data))
        return scritti  # type: ignore[return-value]

    def remove(self, path: bytes) -> None:
        p = _parti(path)
        if len(p) < 2 or p[0] not in _ELIMINABILI or (p[0] == IN and len(p) != 3):
            raise self._nega("percorso", path)
        locale = self.map_path(path)
        try:
            dimensione = os.lstat(locale).st_size
        except FileNotFoundError:
            dimensione = 0
        super().remove(path)
        self._contesto.quota.riserva(str(self._account.id), -dimensione)
        if p[0] == IN:
            self._segnala()

    # ── Cartelle ────────────────────────────────────────────────────────
    def mkdir(self, path: bytes, attrs: asyncssh.SFTPAttrs) -> None:
        if not _cartella_creabile(_parti(path)):
            raise self._nega("percorso", path)
        super().mkdir(path, asyncssh.SFTPAttrs(permissions=_PERMESSI_CARTELLA))
        self._segnala()

    def rmdir(self, path: bytes) -> None:
        p = _parti(path)
        if len(p) < 2 or p[0] not in _ELIMINABILI:
            raise self._nega("percorso", path)
        super().rmdir(path)
        if p[0] == IN:
            self._segnala()

    def _rinomina_ammessa(self, vecchio: bytes, nuovo: bytes) -> bool:
        a, b = _parti(vecchio), _parti(nuovo)
        if len(a) != len(b) or not a or a[0] != IN or b[0] != IN:
            return False
        if len(a) == 2:   # in/<c>.tmp → in/<c> (cartella pronta) o altre rinomine di cartelle
            return _cartella_pacchetto_valida(a[1]) and _cartella_pacchetto_valida(b[1])
        return _file_scrivibile(a) and _file_scrivibile(b)

    def _dopo_rinomina(self, newpath: bytes) -> None:
        nuovo = _parti(newpath)
        self._segnala(pronta=len(nuovo) == 2 and nome_cartella_valido(nuovo[1]))

    def rename(self, oldpath: bytes, newpath: bytes) -> None:
        if not self._rinomina_ammessa(oldpath, newpath):
            raise self._nega("percorso", newpath)
        super().rename(oldpath, newpath)
        self._dopo_rinomina(newpath)

    def posix_rename(self, oldpath: bytes, newpath: bytes) -> None:
        if not self._rinomina_ammessa(oldpath, newpath):
            raise self._nega("percorso", newpath)
        if len(_parti(newpath)) == 2 and os.path.exists(self.map_path(newpath)):
            # una cartella pronta non si sostituisce (l'atomicità è della singola rinomina)
            raise asyncssh.SFTPFileAlreadyExists("La cartella di destinazione esiste già")
        super().posix_rename(oldpath, newpath)
        self._dopo_rinomina(newpath)

    # ── Attributi e collegamenti ────────────────────────────────────────
    @staticmethod
    def _solo_orari(attrs: asyncssh.SFTPAttrs) -> asyncssh.SFTPAttrs:
        return asyncssh.SFTPAttrs(atime=attrs.atime, mtime=attrs.mtime)

    def setstat(self, path: bytes, attrs: asyncssh.SFTPAttrs) -> None:
        p = _parti(path)
        if not p or p[0] != IN:
            raise self._nega("percorso", path)
        super().setstat(path, self._solo_orari(attrs))

    def lsetstat(self, path: bytes, attrs: asyncssh.SFTPAttrs) -> None:
        self.setstat(path, attrs)

    def fsetstat(self, file_obj: object, attrs: asyncssh.SFTPAttrs) -> None:
        super().fsetstat(file_obj, self._solo_orari(attrs))

    def symlink(self, oldpath: bytes, newpath: bytes) -> None:
        raise self._nega("collegamento", newpath)

    def link(self, oldpath: bytes, newpath: bytes) -> None:
        raise self._nega("collegamento", newpath)

    def exit(self) -> None:
        if self._aperta:
            self._aperta = False
            self._contesto.metriche.sessioni.dec()
        super().exit()


#: Task delle voci d'audit in corso (riferimento forte fino alla fine)
_attivita_audit: set[asyncio.Future[None]] = set()


class ServerSsh(asyncssh.SSHServer):
    """Una connessione SSH: autenticazione a chiave sull'account dell'ente."""

    def __init__(self, contesto: ContestoServer, ip_reale: str | None = None) -> None:
        self._contesto = contesto
        self.account: AccountAccesso | None = None
        self._autenticato = False
        #: R10 (D12): IP del cliente (dall'intestazione PROXY di un bilanciatore
        #: fidato, altrimenti quello del socket)
        self.ip: str | None = ip_reale
        self._negato_per_ip = False

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self._peer = conn.get_extra_info("peername")
        if self.ip is None and self._peer:
            self.ip = str(self._peer[0])

    def _registra(self, esito: str, motivo: str | None = None) -> None:
        """Voce d'audit dell'accesso con l'IP reale (in un task: i callback di
        asyncssh non sono asincroni). Un errore si registra nel log."""
        registra = self._contesto.registra_accesso
        account = self.account
        if registra is None or account is None:
            return

        async def _scrivi() -> None:
            try:
                await registra(account.id, account.tenant_id, ip=self.ip, esito=esito,
                               motivo=motivo)
            except Exception as exc:  # noqa: BLE001 — il registro non ferma l'accesso
                logger.error("sftp_audit_accesso_errore", errore=type(exc).__name__)

        _attivita_audit.add(t := asyncio.ensure_future(_scrivi()))
        t.add_done_callback(_attivita_audit.discard)

    def ip_ammesso(self) -> bool:
        """R10 (D12): l'elenco dell'account (se c'è, o se l'ente lo rende obbligatorio)."""
        account = self.account
        if account is None:
            return False
        if not account.ip_ammessi:
            return not account.elenco_obbligatorio
        return ammesso(self.ip, account.ip_ammessi)

    def connection_lost(self, exc: Exception | None) -> None:
        if not self._autenticato:
            self._contesto.metriche.accessi.labels("negato").inc()

    async def begin_auth(self, username: str) -> bool:
        try:
            self.account = await self._contesto.cerca_account(username)
        except Exception as exc:  # noqa: BLE001 — DB non disponibile: accesso negato, registrato
            logger.error("sftp_ricerca_account_errore", errore=type(exc).__name__)
            self.account = None
        # Sempre «autenticazione richiesta»: il nome utente non si rivela.
        return True

    def password_auth_supported(self) -> bool:
        return False

    def kbdint_auth_supported(self) -> bool:
        return False

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        account = self.account
        if account is None or account.nome_utente != username:
            return False
        if not self.ip_ammesso():
            # stessa risposta di una chiave sbagliata: il motivo resta nel log e in audit
            if not self._negato_per_ip:
                self._negato_per_ip = True
                logger.warning("sftp_accesso_negato_ip", account_id=str(account.id), ip=self.ip)
                self._registra("negato", "indirizzo non ammesso")
            return False
        impronta = key.get_fingerprint("sha256")
        registrata = account.chiavi.get(impronta)
        if registrata is None:
            return False
        # confronto della chiave intera, non solo dell'impronta
        if registrata.split()[:2] != key.export_public_key("openssh").decode().split()[:2]:
            return False
        motivo = motivo_chiave_non_ammessa(
            key.get_algorithm(), _bit_rsa(key),
            tipi_ammessi=self._contesto.tipi_chiave_ammessi,
            rsa_bit_minimi=self._contesto.rsa_bit_minimi)
        if motivo is not None:
            logger.warning("sftp_chiave_non_ammessa", account_id=str(account.id), motivo=motivo)
            return False
        return True

    def auth_completed(self) -> None:
        self._autenticato = True
        self._contesto.metriche.accessi.labels("ok").inc()
        if self.account is not None:
            logger.info("sftp_accesso", account_id=str(self.account.id),
                        tenant_id=str(self.account.tenant_id), ip=self.ip)
            self._registra("riuscito")


def _bit_rsa(key: asyncssh.SSHKey) -> int | None:
    pyca = getattr(key, "pyca_key", None)
    return getattr(pyca, "key_size", None)


def _fabbrica_sftp(
    contesto: ContestoServer,
) -> Callable[[asyncssh.SSHServerChannel[bytes]], AreaSftp]:
    def crea(chan: asyncssh.SSHServerChannel[bytes]) -> AreaSftp:
        proprietario = chan.get_connection().get_owner()
        account = getattr(proprietario, "account", None)
        if account is None:  # non accade: la sessione esiste solo dopo l'autenticazione
            raise asyncssh.DisconnectError(asyncssh.DISC_NO_MORE_AUTH_METHODS_AVAILABLE,
                                           "Accesso non autorizzato")
        return AreaSftp(chan, contesto, account)
    return crea


def _opzioni_ssh(contesto: ContestoServer, chiave_host: asyncssh.SSHKey,
                 algoritmi_firma: list[str], login_timeout: int) -> dict[str, Any]:
    return {"server_host_keys": [chiave_host], "sftp_factory": _fabbrica_sftp(contesto),
            "sftp_version": 3, "allow_scp": False, "agent_forwarding": False,
            "x11_forwarding": False, "signature_algs": algoritmi_firma,
            "login_timeout": login_timeout, "server_version": "VerumDoc-SFTP"}


async def avvia_server(contesto: ContestoServer, *, host: str, porta: int,
                       chiave_host: asyncssh.SSHKey, algoritmi_firma: list[str],
                       login_timeout: int, proxy_fidate: list[str] | None = None,
                       proxy_attesa: float = 5.0) -> Any:
    """Avvia il server SFTP; restituisce l'acceptor (``close()`` e ``wait_closed()``).

    R10 (D12): con ``proxy_fidate`` (reti dei bilanciatori) le connessioni da quelle
    reti devono portare l'intestazione PROXY (v1 o v2) con l'IP del cliente; le
    altre sono dirette. Senza, il server di prima (asyncssh.create_server)."""
    opzioni = _opzioni_ssh(contesto, chiave_host, algoritmi_firma, login_timeout)
    if not proxy_fidate:
        return await asyncssh.create_server(lambda: ServerSsh(contesto), host, porta,
                                            **opzioni)
    ascolto = AscoltoProxy(contesto, opzioni, fidate=list(proxy_fidate), attesa=proxy_attesa)
    await ascolto.avvia(host, porta)
    return ascolto


class AscoltoProxy:
    """Accetta le connessioni, legge l'intestazione PROXY SOLO dalle sorgenti fidate,
    poi passa il socket ad asyncssh (``run_server``) con l'IP reale del cliente."""

    def __init__(self, contesto: ContestoServer, opzioni: dict[str, Any], *,
                 fidate: list[str], attesa: float) -> None:
        self._contesto = contesto
        self._opzioni = opzioni
        self._fidate = fidate
        self._attesa = attesa
        self._sock: socket.socket | None = None
        self._ciclo: asyncio.Task[None] | None = None
        self._connessioni: set[asyncio.Task[None]] = set()

    async def avvia(self, host: str, porta: int) -> None:
        famiglia = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(famiglia, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, porta))
        sock.listen(128)
        sock.setblocking(False)
        self._sock = sock
        self._ciclo = asyncio.create_task(self._accetta())

    def porta(self) -> int:
        assert self._sock is not None
        return int(self._sock.getsockname()[1])

    async def _accetta(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._sock is not None
        while True:
            conn, indirizzo_peer = await loop.sock_accept(self._sock)
            conn.setblocking(False)
            t = asyncio.create_task(self._gestisci(conn, str(indirizzo_peer[0])))
            self._connessioni.add(t)
            t.add_done_callback(self._connessioni.discard)

    async def _gestisci(self, conn: socket.socket, ip_peer: str) -> None:
        ip = ip_peer
        if ammesso(ip_peer, self._fidate):
            try:
                intestazione = await asyncio.wait_for(leggi_intestazione(conn), self._attesa)
            except (TimeoutError, ProxyNonValido, OSError) as exc:
                logger.warning("sftp_proxy_intestazione_rifiutata", sorgente=ip_peer,
                               motivo=str(exc) or type(exc).__name__)
                self._contesto.metriche.accessi.labels("negato").inc()
                conn.close()
                return
            if intestazione.sorgente is None:
                conn.close()  # controllo di salute del bilanciatore: nessuna sessione SSH
                return
            ip = intestazione.sorgente
        try:
            await asyncssh.run_server(conn, server_factory=lambda: ServerSsh(self._contesto, ip),
                                      **self._opzioni)
        except (asyncssh.Error, OSError, TimeoutError) as exc:
            logger.info("sftp_connessione_chiusa", ip=ip, motivo=type(exc).__name__)

    def close(self) -> None:
        if self._ciclo is not None:
            self._ciclo.cancel()
        if self._sock is not None:
            self._sock.close()

    async def wait_closed(self) -> None:
        if self._ciclo is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._ciclo


def carica_chiave_host(*, valore: str, ambiente: str, file_sviluppo: str) -> asyncssh.SSHKey:
    """Chiave host: dal segreto; in development da un file locale (generato al
    primo avvio, ignorato da git); in test in memoria; altrove avvio bloccato."""
    if valore.strip():
        return asyncssh.import_private_key(valore.replace("\\n", "\n"))
    if ambiente == "test":
        return asyncssh.generate_private_key("ssh-ed25519")
    if ambiente == "development":
        percorso = Path(file_sviluppo)
        if percorso.exists():
            return asyncssh.read_private_key(str(percorso))
        chiave = asyncssh.generate_private_key("ssh-ed25519")
        chiave.write_private_key(str(percorso))
        os.chmod(percorso, 0o600)
        logger.warning("sftp_chiave_host_sviluppo_generata", file=str(percorso))
        return chiave
    raise RuntimeError("SFTP_CHIAVE_HOST mancante: il server SFTP non parte (REGOLE §19.4)")

