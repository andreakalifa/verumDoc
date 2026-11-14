# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Prova di carico locale e ripetibile del versamento REST (R04, VD-C-081 parte carico).

Obiettivo del piano (§2): 50 versamenti/s di picco, file medio 300 KB, presa in
carico p95 ≤ 2 s (escluso il trasferimento). Questa prova misura la presa in
carico del GATEWAY REALE su componenti reali, sulla macchina dove gira:

  - gateway: ``uvicorn app.main:app`` con più worker (processi separati);
  - PostgreSQL: un database già migrato a head (``--database-url``);
  - S3: server moto in un processo separato (``python -m moto.server``), con i
    bucket creati dal seed del gateway (PdA con Object Lock, staging, PdD);
  - Redis: ``redis-server`` reale (quote e limiti di richieste);
  - Kafka: ASSENTE. Gli eventi restano nell'outbox (relay spento): è il caso
    «Kafka giù» dell'outbox transazionale; il tentativo di pubblicazione dopo
    il commit fallisce subito e non blocca la risposta.

Generatore a ritmo costante (modello «arrivi aperti», come lo scenario k6
``constant-arrival-rate``): una richiesta ogni 1/ritmo secondi, senza aspettare
le precedenti; se il generatore resta indietro lo dichiara (``ritardo_avvio``).
Ogni richiesta è un multipart ``POST /api/v1/versamento/ud`` con un file PDF da
``--dimensione-kb`` KB, indice con chiave UD unica e ``Idempotency-Key`` unica.

Misure per livello: richieste inviate, esiti per codice HTTP, throughput dei
202, latenze (media, p50, p90, p95, p99, massimo), CPU (in % di un core) e
memoria residente di: worker del gateway, server S3, Redis, processi PostgreSQL
del database di prova, generatore; CPU totale della macchina (comprende ciò che
gira in parallelo, per distinguere i limiti della macchina da quelli del codice).

Uso tipico (da radice del repository, interprete con le dipendenze del gateway):

    python tools/carico/prova_locale.py \\
        --database-url postgresql+asyncpg://postgres@127.0.0.1:5466/r04_carico \\
        --livelli 10,25,50 --durata 120 --worker 2 --uscita /tmp/carico

Il database deve essere migrato a head (``alembic upgrade head`` dal gateway).
La prova scrive righe di PdV e outbox nel database indicato: usare un database
dedicato, mai uno condiviso. Nessun segreto viene scritto su disco: le chiavi JWT
e il pepe delle chiavi API sono generati in memoria a ogni esecuzione.

Codice d'uscita: 0 se ogni livello ha rispettato la soglia (p95 ≤ ``--soglia-p95``
e errori ≤ ``--soglia-errori``), 1 altrimenti, 2 per un errore d'avvio.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RADICE = Path(__file__).resolve().parents[2]
GATEWAY = RADICE / "services" / "gateway"
PERCORSO_VERSAMENTO = "/api/v1/versamento/ud"
PERCORSO_SALUTE = "/api/v1/health/live"
TENANT_DEMO = "00000000-0000-0000-0000-000000000001"
BUCKET_STAGING = "carico-staging"
BUCKET_AIP = "carico-aip"
BUCKET_PDD = "carico-pdd"
#: Quote alte per la sola prova: il limite di richieste e di PdV al giorno del
#: piano non deve essere ciò che si misura (si misurerebbe il 429).
QUOTE_PROVA = '{"starter": 100000000, "business": 100000000, "enterprise": 100000000}'
TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


# ── Processi ────────────────────────────────────────────────────────

def porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def attendi_porta(porta: int, timeout: float, processo: subprocess.Popen | None = None) -> None:
    fine = time.monotonic() + timeout
    while time.monotonic() < fine:
        if processo is not None and processo.poll() is not None:
            raise RuntimeError(f"processo terminato con codice {processo.returncode} prima di "
                               f"aprire la porta {porta}")
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", porta)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"porta {porta} non aperta entro {timeout:.0f} s")


@dataclass
class Processi:
    """Processi avviati dalla prova, fermati in ordine inverso all'uscita."""
    elenco: list[tuple[str, subprocess.Popen]] = field(default_factory=list)

    def avvia(self, nome: str, comando: list[str], *, env: dict[str, str] | None = None,
              cwd: Path | None = None, log: Path) -> subprocess.Popen:
        f = open(log, "wb")  # noqa: SIM115 — chiuso con il processo
        p = subprocess.Popen(comando, env=env, cwd=cwd, stdout=f, stderr=subprocess.STDOUT,  # noqa: S603 — comando e argomenti costruiti dallo strumento
                             start_new_session=True)
        self.elenco.append((nome, p))
        return p

    def ferma(self, nome: str | None = None) -> None:
        for n, p in reversed(list(self.elenco)):
            if nome is not None and n != nome:
                continue
            if p.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=5)
            self.elenco.remove((n, p))


# ── Misure di CPU e memoria da /proc (nessuna dipendenza esterna) ───

def _figli(pid: int) -> list[int]:
    """pid e discendenti (lettura di /proc/*/stat)."""
    genitori: dict[int, list[int]] = {}
    for voce in os.listdir("/proc"):
        if not voce.isdigit():
            continue
        try:
            stat = Path(f"/proc/{voce}/stat").read_text()
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        genitori.setdefault(ppid, []).append(int(voce))
    risultato, coda = [], [pid]
    while coda:
        p = coda.pop()
        risultato.append(p)
        coda.extend(genitori.get(p, []))
    return risultato


def _tick_e_rss(pid: int) -> tuple[int, int]:
    try:
        campi = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        rss_kb = 0
        for riga in Path(f"/proc/{pid}/status").read_text().splitlines():
            if riga.startswith("VmRSS:"):
                rss_kb = int(riga.split()[1])
                break
        return int(campi[11]) + int(campi[12]), rss_kb
    except (OSError, IndexError, ValueError):
        return 0, 0


def _cpu_macchina() -> tuple[int, int]:
    """(tick occupati, tick totali) di tutta la macchina da /proc/stat."""
    valori = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    inattivo = valori[3] + (valori[4] if len(valori) > 4 else 0)
    return sum(valori) - inattivo, sum(valori)


class Campionatore:
    """Ogni ``intervallo`` secondi somma tick di CPU e RSS per gruppo di processi."""

    def __init__(self, gruppi: dict[str, Any], intervallo: float = 1.0) -> None:
        # gruppo → funzione che restituisce l'elenco dei pid del gruppo
        self.gruppi = gruppi
        self.intervallo = intervallo
        self.campioni: dict[str, list[tuple[float, float]]] = {g: [] for g in gruppi}
        self.macchina: list[float] = []
        self._fine = asyncio.Event()

    def _lettura(self) -> tuple[dict[str, tuple[int, int]], tuple[int, int]]:
        letture = {}
        for g, pid_di in self.gruppi.items():
            tick = rss = 0
            for pid in pid_di():
                t, r = _tick_e_rss(pid)
                tick += t
                rss += r
            letture[g] = (tick, rss)
        return letture, _cpu_macchina()

    async def esegui(self) -> None:
        prima, macchina_prima = await asyncio.to_thread(self._lettura)
        t_prima = time.monotonic()
        while not self._fine.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._fine.wait(), timeout=self.intervallo)
            dopo, macchina_dopo = await asyncio.to_thread(self._lettura)
            dt = time.monotonic() - t_prima
            for g in self.gruppi:
                cpu = max(0, dopo[g][0] - prima[g][0]) / TICK / dt * 100
                self.campioni[g].append((cpu, dopo[g][1] / 1024))
            occ = macchina_dopo[0] - macchina_prima[0]
            tot = macchina_dopo[1] - macchina_prima[1]
            self.macchina.append(100 * occ / tot if tot else 0.0)
            prima, macchina_prima, t_prima = dopo, macchina_dopo, time.monotonic()

    def ferma(self) -> None:
        self._fine.set()

    def riepilogo(self) -> dict[str, Any]:
        r: dict[str, Any] = {}
        for g, valori in self.campioni.items():
            if not valori:
                continue
            cpu = [c for c, _ in valori]
            rss = [m for _, m in valori]
            r[g] = {"cpu_media_pct": round(statistics.fmean(cpu), 1),
                    "cpu_max_pct": round(max(cpu), 1),
                    "rss_max_mb": round(max(rss), 1)}
        if self.macchina:
            r["macchina"] = {"cpu_media_pct": round(statistics.fmean(self.macchina), 1),
                             "cpu_max_pct": round(max(self.macchina), 1),
                             "core": os.cpu_count()}
        return r


# ── Richieste ───────────────────────────────────────────────────────

def pdf_sintetico(dimensione: int, seme: int) -> bytes:
    """PDF sintetico (firma «%PDF-1.7», corpo pseudo-casuale ripetibile)."""
    testa = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    coda = b"\n%%EOF\n"
    blocco = hashlib.sha512(f"carico-{seme}".encode()).digest() * 128
    corpo = (blocco * (dimensione // len(blocco) + 1))[
        : max(0, dimensione - len(testa) - len(coda))]
    return testa + corpo + coda


@dataclass(frozen=True)
class FilePronto:
    dati: bytes
    sha256: str


def indice_versamento(numero: str, file: FilePronto, nome: str) -> dict[str, Any]:
    """Indice minimo valido per la classe di base «fattura» (metadati minimi)."""
    return {
        "classe_documentale": "fattura",
        "chiave": {"numero": numero, "anno": "2026", "registro": "CARICO"},
        "documenti": [{
            "id": "doc-1", "tipo": "PRINCIPALE", "nome_file": nome,
            "formato": "application/pdf",
            "hash": {"impronta": file.sha256, "algoritmo": "SHA-256"},
            "parametri": {
                "identificativo_documento": numero,
                "modalita_formazione": "a",
                "tipologia_documentale": "fattura",
                "data_documento": "2026-10-23",
                "soggetto_produttore_id": "12345678901",
                "soggetto_produttore_nome": "Ente di prova del carico",
                "oggetto": f"Documento di prova di carico {numero}",
            },
        }],
    }


def corpo_multipart(indice: dict[str, Any], file: FilePronto, nome: str) -> tuple[bytes, str]:
    """Corpo multipart già serializzato: il generatore copia byte, non li ricalcola."""
    confine = uuid.uuid4().hex
    testa = (
        f"--{confine}\r\nContent-Disposition: form-data; name=\"indice\"\r\n"
        "Content-Type: application/json\r\n\r\n"
    ).encode() + json.dumps(indice).encode() + (
        f"\r\n--{confine}\r\nContent-Disposition: form-data; name=\"files\"; "
        f"filename=\"{nome}\"\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    tipo = f"multipart/form-data; boundary={confine}"
    return testa + file.dati + f"\r\n--{confine}--\r\n".encode(), tipo


@dataclass
class Esito:
    inviata: float
    durata: float
    stato: int
    codice: str = ""


def percentile(valori: list[float], p: float) -> float:
    if not valori:
        return float("nan")
    ordinati = sorted(valori)
    k = (len(ordinati) - 1) * p / 100
    basso, alto = math.floor(k), math.ceil(k)
    return ordinati[basso] + (ordinati[alto] - ordinati[basso]) * (k - basso)


async def livello(sessione: Any, url: str, token: str, ritmo: float, durata: float,
                  file: list[FilePronto], etichetta: str) -> tuple[list[Esito], float]:
    """Invia ``ritmo`` richieste al secondo per ``durata`` secondi (arrivi aperti)."""
    import aiohttp

    totale = int(ritmo * durata)
    esiti: list[Esito] = []
    ritardo_max = 0.0
    compiti: set[asyncio.Task] = set()

    async def una(i: int) -> None:
        f = file[i % len(file)]
        numero = f"{etichetta}-{i:07d}"
        nome = f"documento-{i % len(file)}.pdf"
        corpo, tipo = corpo_multipart(indice_versamento(numero, f, nome), f, nome)
        intestazioni = {"Authorization": f"Bearer {token}", "Content-Type": tipo,
                        "Idempotency-Key": f"carico-{etichetta}-{i}-{uuid.uuid4().hex[:8]}"}
        t0 = time.monotonic()
        try:
            async with sessione.post(url + PERCORSO_VERSAMENTO, data=corpo,
                                     headers=intestazioni) as r:
                testo = await r.read()
                codice = ""
                if r.status >= 400:
                    with contextlib.suppress(ValueError):
                        codice = str(json.loads(testo).get("codice") or "")
                esiti.append(Esito(t0, time.monotonic() - t0, r.status, codice))
        except (TimeoutError, aiohttp.ClientError) as exc:
            esiti.append(Esito(t0, time.monotonic() - t0, 0, type(exc).__name__))

    inizio = time.monotonic()
    for i in range(totale):
        previsto = inizio + i / ritmo
        attesa = previsto - time.monotonic()
        if attesa > 0:
            await asyncio.sleep(attesa)
        else:
            ritardo_max = max(ritardo_max, -attesa)
        t = asyncio.create_task(una(i))
        compiti.add(t)
        t.add_done_callback(compiti.discard)
    if compiti:
        await asyncio.wait(compiti, timeout=120)
    return esiti, ritardo_max


def statistiche(esiti: list[Esito], ritmo: float, durata: float, ritardo: float) -> dict[str, Any]:
    ok = [e for e in esiti if e.stato == 202]
    lat = [e.durata for e in ok]
    stati: dict[str, int] = {}
    for e in esiti:
        k = str(e.stato) + (f" {e.codice}" if e.codice else "")
        stati[k] = stati.get(k, 0) + 1
    if ok:
        finestra = max(e.inviata + e.durata for e in ok) - min(e.inviata for e in ok)
    else:
        finestra = durata
    return {
        "ritmo_richiesto": ritmo,
        "durata_s": durata,
        "inviate": len(esiti),
        "esiti": dict(sorted(stati.items())),
        "accettate_202": len(ok),
        "errori_pct": round(100 * (len(esiti) - len(ok)) / len(esiti), 2) if esiti else 100.0,
        "throughput_202_s": round(len(ok) / finestra, 2) if finestra > 0 else 0.0,
        "latenza_s": {
            "media": round(statistics.fmean(lat), 3) if lat else None,
            "p50": round(percentile(lat, 50), 3) if lat else None,
            "p90": round(percentile(lat, 90), 3) if lat else None,
            "p95": round(percentile(lat, 95), 3) if lat else None,
            "p99": round(percentile(lat, 99), 3) if lat else None,
            "max": round(max(lat), 3) if lat else None,
        },
        "ritardo_avvio_max_s": round(ritardo, 3),
    }


# ── Orchestrazione ─────────────────────────────────────────────────

def chiavi_jwt() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    privata = chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()).decode()
    pubblica = chiave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return privata, pubblica


def ambiente_gateway(
    a: argparse.Namespace, porte: dict[str, int], jwt: tuple[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VD_", "S3_", "DATABASE_"))}
    env.update({
        "PYTHONPATH": os.pathsep.join([str(RADICE), str(GATEWAY)]),
        "ENV": "development",
        "VERUMDOC_DEV_AUTH": "1",
        "LOG_LEVEL": "WARNING",
        "LOG_JSON": "true",
        "DATABASE_URL": a.database_url,
        "REDIS_URL": a.redis_url or f"redis://127.0.0.1:{porte['redis']}/0",
        # Kafka assente: porta chiusa (gli eventi restano nell'outbox)
        "KAFKA_BOOTSTRAP": f"127.0.0.1:{porte['kafka']}",
        "OUTBOX_RELAY_ATTIVO": "false",
        "S3_ENDPOINT_URL": f"http://127.0.0.1:{porte['s3']}",
        "S3_ACCESS_KEY": "carico", "S3_SECRET_KEY": "carico", "S3_REGION": "us-east-1",
        "S3_BUCKET_PDV": BUCKET_STAGING, "S3_BUCKET_AIP": BUCKET_AIP, "S3_BUCKET_PDD": BUCKET_PDD,
        "JWT_PRIVATE_KEY": jwt[0], "JWT_PUBLIC_KEY": jwt[1],
        "API_KEY_PEPPER": secrets.token_urlsafe(32),
        "QUOTE_RICHIESTE_MINUTO": QUOTE_PROVA, "QUOTE_PDV_GIORNO": QUOTE_PROVA,
        "CARICAMENTI_LAVORATORE_ATTIVO": "false",
        "STAGING_PULIZIA_ATTIVO": "false",
        "SEED_ATTESA_STORAGE_SECONDI": "30",
    })
    if a.multipart_max:
        env["VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX"] = str(a.multipart_max)
    return env


def pid_postgres(database_url: str) -> list[int]:
    """Pid dei processi PostgreSQL collegati al database della prova."""
    from urllib.parse import urlparse

    u = urlparse(database_url.replace("+asyncpg", ""))
    dsn = ["psql", "-h", u.hostname or "127.0.0.1", "-p", str(u.port or 5432),
           "-U", u.username or "postgres", "-d", u.path.lstrip("/"), "-tAc",
           "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
           "AND pid <> pg_backend_pid()"]
    try:
        out = subprocess.run(dsn, capture_output=True, text=True, timeout=5,  # noqa: S603 — comando e argomenti costruiti dallo strumento
                             env={**os.environ, "PGCONNECT_TIMEOUT": "3"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def conta_db(database_url: str) -> dict[str, int]:
    from urllib.parse import urlparse

    u = urlparse(database_url.replace("+asyncpg", ""))
    sql = ("SELECT (SELECT count(*) FROM public.pdv), "
           "(SELECT count(*) FROM public.outbox_eventi WHERE pubblicato_il IS NULL)")
    out = subprocess.run(["psql", "-h", u.hostname or "127.0.0.1", "-p", str(u.port or 5432),  # noqa: S603, S607 — comando costruito dallo strumento, eseguibile nel PATH
                          "-U", u.username or "postgres", "-d", u.path.lstrip("/"), "-tAc", sql],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    try:
        pdv, outbox = (int(x) for x in out.split("|"))
    except ValueError:
        return {}
    return {"pdv": pdv, "outbox_in_sospeso": outbox}


async def esegui(a: argparse.Namespace) -> int:
    import aiohttp

    lavoro = Path(a.lavoro or tempfile.mkdtemp(prefix="vd-carico-"))
    lavoro.mkdir(parents=True, exist_ok=True)
    uscita = Path(a.uscita)
    uscita.mkdir(parents=True, exist_ok=True)
    porte = {"redis": porta_libera(), "s3": porta_libera(), "kafka": porta_libera(),
             "gateway": a.porta or porta_libera()}
    proc = Processi()
    risultati: dict[str, Any] = {"parametri": {k: v for k, v in vars(a).items()
                                               if k not in ("database_url",)},
                                 "livelli": []}
    try:
        print(f"[carico] cartella di lavoro {lavoro}")
        redis: subprocess.Popen | None = None
        if not a.redis_url:
            redis = proc.avvia("redis", [a.redis_server, "--port", str(porte["redis"]),
                                         "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
                               log=lavoro / "redis.log")
            attendi_porta(porte["redis"], 15, redis)

        def avvia_s3() -> subprocess.Popen:
            p = proc.avvia("s3", [a.python, "-m", "moto.server", "-H", "127.0.0.1",
                                  "-p", str(porte["s3"])], log=lavoro / "s3.log")
            attendi_porta(porte["s3"], 60, p)
            return p

        s3 = avvia_s3()
        jwt = chiavi_jwt()
        env = ambiente_gateway(a, porte, jwt)
        print("[carico] seed del gateway (bucket e dati dimostrativi)")
        seed = subprocess.run([a.python, "-m", "app.seed"], cwd=GATEWAY, env=env,  # noqa: S603 — comando e argomenti costruiti dallo strumento
                              capture_output=True, text=True, timeout=300)
        (lavoro / "seed.log").write_text(seed.stdout + seed.stderr)
        if seed.returncode != 0:
            print(f"[carico] seed fallito (vedi {lavoro / 'seed.log'})", file=sys.stderr)
            return 2
        gateway = proc.avvia("gateway", [a.python, "-m", "uvicorn", "app.main:app",
                                         "--host", "127.0.0.1", "--port", str(porte["gateway"]),
                                         "--workers", str(a.worker), "--no-access-log",
                                         "--log-level", "warning"],
                             env=env, cwd=GATEWAY, log=lavoro / "gateway.log")
        attendi_porta(porte["gateway"], 120, gateway)
        url = f"http://127.0.0.1:{porte['gateway']}"
        timeout = aiohttp.ClientTimeout(total=a.timeout)
        connettore = aiohttp.TCPConnector(limit=a.connessioni, force_close=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connettore) as sessione:
            for _ in range(240):
                with contextlib.suppress(aiohttp.ClientError):
                    async with sessione.get(url + PERCORSO_SALUTE) as r:
                        if r.status == 200:
                            break
                await asyncio.sleep(0.5)
            async with sessione.post(url + "/api/v1/auth/dev-token",
                                     json={"ruolo": "OPERATORE", "tenant_id": TENANT_DEMO}) as r:
                if r.status != 200:
                    print(f"[carico] dev-token: HTTP {r.status}", file=sys.stderr)
                    return 2
                token = (await r.json())["access_token"]
            dimensione = a.dimensione_kb * 1024
            file = [FilePronto(d, hashlib.sha256(d).hexdigest())
                    for d in (pdf_sintetico(dimensione, s) for s in range(a.file_distinti))]
            # Riscaldamento: import pigri, pool di connessioni, client S3
            await livello(sessione, url, token, ritmo=5, durata=4, file=file,
                          etichetta=f"risc-{uuid.uuid4().hex[:6]}")
            soglia_ok = True
            for ritmo in a.livelli:
                if a.riavvia_s3 and risultati["livelli"]:
                    proc.ferma("s3")
                    s3 = avvia_s3()
                    ricrea = subprocess.run([a.python, "-m", "app.seed"], cwd=GATEWAY, env=env,  # noqa: S603 — comando e argomenti costruiti dallo strumento
                                            capture_output=True, text=True, timeout=300)
                    if ricrea.returncode != 0:
                        print("[carico] seed dopo il riavvio di S3 fallito", file=sys.stderr)
                        return 2
                gruppi = {
                    "gateway": lambda: _figli(gateway.pid),
                    "s3": lambda: _figli(s3.pid),  # noqa: B023 — letta nello stesso giro
                    "redis": lambda: [redis.pid] if redis else [],
                    "postgres": lambda: pid_postgres(a.database_url),
                    "generatore": lambda: [os.getpid()],
                }
                camp = Campionatore(gruppi)
                compito = asyncio.create_task(camp.esegui())
                etichetta = f"r{int(ritmo)}-{uuid.uuid4().hex[:6]}"
                print(f"[carico] livello {ritmo}/s per {a.durata:.0f} s")
                esiti, ritardo = await livello(
                    sessione, url, token, ritmo, a.durata, file, etichetta)
                camp.ferma()
                await compito
                st = statistiche(esiti, ritmo, a.durata, ritardo)
                st["risorse"] = camp.riepilogo()
                st["database_dopo"] = conta_db(a.database_url)
                risultati["livelli"].append(st)
                p95 = st["latenza_s"]["p95"]
                rispettata = (p95 is not None and p95 <= a.soglia_p95
                              and st["errori_pct"] <= a.soglia_errori)
                st["soglia_rispettata"] = rispettata
                soglia_ok = soglia_ok and rispettata
                print(f"[carico]   202/s={st['throughput_202_s']} p95={p95} s "
                      f"errori={st['errori_pct']}% esiti={st['esiti']}")
                await asyncio.sleep(a.pausa)
        risultati["esito"] = "SOGLIE RISPETTATE" if soglia_ok else "SOGLIE NON RISPETTATE"
        (uscita / "risultati.json").write_text(json.dumps(risultati, indent=2, ensure_ascii=False))
        (uscita / "risultati.md").write_text(tabella_md(risultati), encoding="utf-8")
        print(f"[carico] risultati in {uscita}")
        return 0 if soglia_ok else 1
    finally:
        proc.ferma()
        if not a.conserva_lavoro and not a.lavoro:
            shutil.rmtree(lavoro, ignore_errors=True)


def tabella_md(r: dict[str, Any]) -> str:
    righe = ["| Ritmo richiesto | Inviate | 202 | 202/s | Errori % | p50 s | p95 s | p99 s | max s "
             "| CPU gateway % | CPU S3 % | CPU PG % | CPU gen. % | CPU macchina % | RSS gateway "
             "MB |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for liv in r["livelli"]:
        lat, ris = liv["latenza_s"], liv.get("risorse", {})

        def c(g: str, k: str = "cpu_media_pct", ris: dict = ris) -> str:
            return str(ris.get(g, {}).get(k, "—"))
        righe.append(
            f"| {liv['ritmo_richiesto']}/s | {liv['inviate']} | {liv['accettate_202']} | "
            f"{liv['throughput_202_s']} | {liv['errori_pct']} | {lat['p50']} | {lat['p95']} | "
            f"{lat['p99']} | {lat['max']} | {c('gateway')} | {c('s3')} | {c('postgres')} | "
            f"{c('generatore')} | {c('macchina')} | {c('gateway', 'rss_max_mb')} |")
    esiti = "\n".join(f"- {liv['ritmo_richiesto']}/s: {liv['esiti']}" for liv in r["livelli"])
    return "\n".join(righe) + "\n\nEsiti per codice:\n" + esiti + f"\n\nEsito: {r.get('esito')}\n"


def argomenti(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database-url", required=True,
                   help="database PostgreSQL DEDICATO già migrato a head (postgresql+asyncpg://...)")
    p.add_argument("--livelli", default="10,25,50",
                   type=lambda s: [float(x) for x in s.split(",") if x.strip()],
                   help="ritmi in versamenti al secondo, separati da virgole")
    p.add_argument("--durata", type=float, default=120.0, help="secondi per livello")
    p.add_argument("--pausa", type=float, default=10.0, help="secondi fra un livello e l'altro")
    p.add_argument("--worker", type=int, default=2, help="worker uvicorn del gateway")
    p.add_argument(
        "--dimensione-kb", type=int, default=300, help="dimensione del file per versamento")
    p.add_argument("--file-distinti", type=int, default=32, help="contenuti diversi in rotazione")
    p.add_argument(
        "--connessioni", type=int, default=400, help="connessioni HTTP massime del generatore")
    p.add_argument("--timeout", type=float, default=60.0, help="timeout di una richiesta (s)")
    p.add_argument(
        "--soglia-p95", type=float, default=2.0, help="p95 massimo della presa in carico (s)")
    p.add_argument("--soglia-errori", type=float, default=1.0, help="errori massimi (%%)")
    p.add_argument("--multipart-max", type=int, default=0,
                   help="VERSAMENTO_MULTIPART_RICHIESTE_CONTEMPORANEE_MAX "
                        "(0 = predefinito del servizio)")
    p.add_argument("--riavvia-s3", action="store_true",
                   help="riavvia il server S3 fra i livelli (moto tiene gli oggetti in memoria)")
    p.add_argument("--porta", type=int, default=0, help="porta del gateway (0 = libera)")
    p.add_argument(
        "--python", default=sys.executable, help="interprete con le dipendenze del gateway")
    p.add_argument(
        "--redis-server", default=shutil.which("redis-server") or "/usr/bin/redis-server")
    p.add_argument("--redis-url", default="",
                   help="Redis già in esecuzione (es. servizio della CI); "
                        "vuoto = avvia redis-server")
    p.add_argument("--lavoro", default="", help="cartella dei log (predefinito: temporanea)")
    p.add_argument(
        "--conserva-lavoro", action="store_true", help="non eliminare la cartella dei log")
    p.add_argument("--uscita", required=True, help="cartella dei risultati (JSON e Markdown)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = argomenti(argv)
    try:
        return asyncio.run(esegui(a))
    except RuntimeError as exc:
        print(f"[carico] errore d'avvio: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
