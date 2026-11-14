# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Topologia di deploy derivata da infra/helm/values.yaml (R03 i2).

Funzioni pure condivise da genera_manifest.py e verifica_manifest.py, così le
due letture dei values non possono divergere:

  - ``carichi(valori)``: i carichi di lavoro da distribuire. Ogni servizio
    abilitato è un carico; ogni voce abilitata di ``services.<s>.workers`` è un
    carico in più («lavoratore», Deployment ``<s>-<nome>``) con la stessa
    immagine, la stessa ConfigMap e lo stesso Secret del servizio, un comando
    proprio e variabili ``env`` che prevalgono sulla ConfigMap;
  - ``cronjob(valori)``: i job periodici (``services.<s>.cronJobs``);
  - ``errori_ordine_rotte(rotte)``: l'ALB valuta le regole nell'ordine scritto,
    quindi un prefisso più specifico deve precedere quello che lo contiene
    (instradamento «a prefisso più lungo»).

R04 (infra):
  - ``account``: un rilascio riguarda UN account (``global.account``:
    «principale» o «testimone»). Il testimone della catena (contratto K3) gira
    in un account AWS con amministratori diversi: i servizi con
    ``account: testimone`` si distribuiscono solo nel rilascio di quell'account
    (values.testimone.yaml), mai insieme al registro;
  - ``jobs(valori)``: Job una tantum (es. migrazioni prima dei servizi);
  - ``clienti_pgbouncer(valori)``: carichi che raggiungono PostgreSQL attraverso
    PgBouncer (``pgbouncer`` in ``egress.data``): la NetworkPolicy di PgBouncer
    ammette esattamente loro.
"""
from __future__ import annotations

import copy
from typing import Any

#: Dimensione predefinita di /tmp (emptyDir) se il servizio non indica tmpSizeLimit.
TMP_PREDEFINITO = "1Gi"

#: Account predefinito di un servizio e di un rilascio.
ACCOUNT_PRINCIPALE = "principale"

#: Voce di ``egress.data`` che indica PostgreSQL attraverso PgBouncer.
PGBOUNCER = "pgbouncer"

#: Campi del servizio che un lavoratore può ridefinire.
CAMPI_LAVORATORE = ("replicas", "resources", "autoscaling", "egress", "strategy")


def account(valori: dict[str, Any]) -> str:
    """Account del rilascio (``global.account``)."""
    return str((valori.get("global") or {}).get("account") or ACCOUNT_PRINCIPALE)


def servizi_attivi(valori: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Servizi abilitati che appartengono all'account del rilascio."""
    acc = account(valori)
    return {n: s for n, s in valori["services"].items()
            if s.get("enabled") and str(s.get("account") or ACCOUNT_PRINCIPALE) == acc}


def lavoratori(nome: str, s: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Lavoratori abilitati del servizio ``nome`` come (nome Deployment, spec)."""
    return [(f"{nome}-{n}", w) for n, w in (s.get("workers") or {}).items()
            if w.get("enabled", True)]


def carico(nome_servizio: str, s: dict[str, Any], nome: str | None = None,
           w: dict[str, Any] | None = None) -> dict[str, Any]:
    """Vista unica di un carico: servizio (w=None) o suo lavoratore."""
    w = w or {}
    c: dict[str, Any] = {
        "nome": nome or nome_servizio,
        "servizio": nome_servizio,
        "lavoratore": bool(w),
        "componente": (nome or nome_servizio).removeprefix(f"{nome_servizio}-") if w else "api",
        "image": s["image"],
        "port": s["port"],
        "health": s.get("health"),
        # Un lavoratore senza server HTTP (http: false) non ha probe né porta
        "http": w.get("http", True) is not False,
        "command": w.get("command"),
        "config": s.get("config") or {},
        "env": w.get("env") or {},
        # R04: variabili lette dall'entrypoint dell'immagine (non dalle Settings),
        # solo per il processo principale del servizio (i lavoratori hanno «command»)
        "entrypointEnv": {} if w else dict(s.get("entrypointEnv") or {}),
        # R04: variabili da un Secret diverso da quello del servizio (es. URL via PgBouncer)
        "envSegreti": dict(w.get("envSegreti") or {}),
        "secretKeys": s.get("secretKeys") or [],
        # Revisione 2 di R05 (S2): chiavi del Secret PROPRIO del lavoratore
        # (<servizio>-<lavoratore>-secrets), che il servizio (esposto) non riceve
        "secretKeysPropri": list(w.get("secretKeys") or []) if w else [],
        "callers": [] if w else list(s.get("callers") or []),
        "exposed": False if w else bool(s.get("exposed")),
        "publicTcp": None if w else s.get("publicTcp"),
        "persistentVolumes": s.get("persistentVolumes") or [],
        "runAsUser": s.get("runAsUser"),
        # R03 f3 (REV-N2): dimensione di /tmp (emptyDir) del servizio e dei suoi carichi
        "tmpSizeLimit": str(s.get("tmpSizeLimit") or TMP_PREDEFINITO),
        "irsa": True if w.get("iam") else s.get("irsa", True),
        # R04: un lavoratore con «iam» proprio ha ServiceAccount (e ruolo) propri,
        # con permessi minori di quelli del servizio
        "serviceAccount": (nome or nome_servizio) if w.get("iam") else nome_servizio,
        "iam": w.get("iam") if w else s.get("iam"),
        # GET /metrics (libs/shared/observability/metriche.py) su tutte le API Python
        "metrics": (s.get("metrics", True) is not False and w.get("http", True) is not False)
        or bool(w.get("metricsPort")),
        # R04: lavoratore senza HTTP che espone solo le metriche Prometheus su una porta propria
        "metricsPort": w.get("metricsPort"),
        "disattivaNelPadre": w.get("disattivaNelPadre"),
    }
    for campo in CAMPI_LAVORATORE:
        c[campo] = copy.deepcopy(w[campo] if campo in w else s.get(campo))
    c["egress"] = c["egress"] or {}
    c["autoscaling"] = c["autoscaling"] or {}
    return c


def carichi(valori: dict[str, Any]) -> list[dict[str, Any]]:
    """Servizi abilitati e i loro lavoratori, nell'ordine dei values."""
    risultato: list[dict[str, Any]] = []
    for nome, s in servizi_attivi(valori).items():
        risultato.append(carico(nome, s))
        for nome_w, w in lavoratori(nome, s):
            c = carico(nome, s, nome_w, w)
            # Revisione R12b: un lavoratore che non deve ricevere il Secret del servizio
            # (segretiServizio: false) ha solo il proprio Secret ed envSegreti (come i
            # job periodici, revisione R06 V3; il chart lo fa in verumdoc.pod)
            if w.get("segretiServizio", True) is False:
                c["secretKeys"] = []
            risultato.append(c)
    return risultato


def cronjob(valori: dict[str, Any]) -> list[dict[str, Any]]:
    """Job periodici abilitati: stessa immagine, ConfigMap e Secret del servizio."""
    risultato: list[dict[str, Any]] = []
    for nome, s in servizi_attivi(valori).items():
        for n, j in (s.get("cronJobs") or {}).items():
            if not j.get("enabled", True):
                continue
            c = carico(nome, s, f"{nome}-{n}", {**j, "http": False})
            c.update(metrics=False, metricsPort=None)
            c.update(schedule=j["schedule"], activeDeadlineSeconds=j.get(
                "activeDeadlineSeconds", 3600),
                     componente=n, backoffLimit=int(j.get("backoffLimit", 2)))
            # Revisione R06 (V3): un job che non deve ricevere il Secret del servizio
            # (segretiServizio: false) ha solo envSegreti e il proprio Secret
            if j.get("segretiServizio", True) is False:
                c["secretKeys"] = []
            if j.get("automountServiceAccountToken") is False:
                c["automountToken"] = False
            risultato.append(c)
    return risultato


def jobs(valori: dict[str, Any]) -> list[dict[str, Any]]:
    """Job una tantum (``services.<s>.jobs``), es. le migrazioni prima dei servizi.

    Come i CronJob usano immagine, ConfigMap, Secret e ServiceAccount del servizio;
    ``envSegreti`` sostituisce singole variabili con chiavi di un Secret diverso
    (es. l'URL DIRETTO di PostgreSQL per le migrazioni, che non passano da PgBouncer).
    """
    risultato: list[dict[str, Any]] = []
    for nome, s in servizi_attivi(valori).items():
        for n, j in (s.get("jobs") or {}).items():
            if not j.get("enabled", True):
                continue
            c = carico(nome, s, f"{nome}-{n}", {**j, "http": False})
            c.update(metrics=False, metricsPort=None)
            # Parte PRIMA delle altre risorse del rilascio (hook): niente ConfigMap,
            # Secret né ServiceAccount del servizio, solo le variabili dichiarate
            c.update(componente=n, activeDeadlineSeconds=j.get("activeDeadlineSeconds", 1800),
                     envSegreti=dict(j.get("envSegreti") or {}),
                     helmHook=j.get("helmHook", "pre-install,pre-upgrade"),
                     config={}, secretKeys=[], secretKeysPropri=[], irsa=False,
                     serviceAccount="default", iam=None,
                     autoscaling={}, replicas=1)
            risultato.append(c)
    return risultato


def account_di_servizio(valori: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, bool]]:
    """ServiceAccount del rilascio: (nome, blocco iam, irsa) per ogni servizio,
    per ogni lavoratore e (R09) per ogni job periodico con «iam» proprio."""
    risultato: list[tuple[str, dict[str, Any] | None, bool]] = []
    for nome, s in servizi_attivi(valori).items():
        risultato.append((nome, s.get("iam"), s.get("irsa", True) is not False))
        for nome_w, w in lavoratori(nome, s):
            if w.get("iam"):
                risultato.append((nome_w, w["iam"], True))
        # R09 (D27): un job periodico con «iam» proprio ha ServiceAccount e ruolo propri
        for n, j in (s.get("cronJobs") or {}).items():
            if j.get("enabled", True) and j.get("iam"):
                risultato.append((f"{nome}-{n}", j["iam"], True))
    return risultato


def clienti_pgbouncer(valori: dict[str, Any]) -> list[str]:
    """Carichi (servizi, lavoratori, job periodici e una tantum) che usano PgBouncer."""
    return [c["nome"] for c in carichi(valori) + cronjob(valori) + jobs(valori)
            if PGBOUNCER in (c["egress"].get("data") or [])]


def config_effettiva(c: dict[str, Any]) -> dict[str, str]:
    """Variabili viste dal processo: ConfigMap del servizio + env del carico."""
    return {
        **{k: str(v) for k, v in c["config"].items()}, **{k: str(v) for k, v in c["env"].items()}}


def _contiene(generale: str, specifico: str) -> bool:
    """True se il prefisso ``generale`` contiene ``specifico`` (per segmenti)."""
    if generale == specifico:
        return False
    return generale == "/" or specifico.startswith(generale.rstrip("/") + "/")


def errori_ordine_rotte(rotte: list[dict[str, Any]]) -> list[str]:
    """Rotte che una precedente più generale renderebbe irraggiungibili."""
    errori: list[str] = []
    for i, prima in enumerate(rotte):
        for dopo in rotte[i + 1:]:
            stessi_host = (not prima.get("onlyHost") or not dopo.get("onlyHost")
                           or prima.get("onlyHost") == dopo.get("onlyHost"))
            if not stessi_host:
                continue
            if prima["path"] == dopo["path"]:
                errori.append(f"ingress: rotta {dopo['path']} duplicata")
            elif _contiene(prima["path"], dopo["path"]):
                errori.append(f"ingress: {dopo['path']} ({dopo['service']}) dopo {prima['path']} "
                              f"({prima['service']}): l'ALB non la raggiungerebbe mai, "
                              "spostarla prima (prefisso più lungo per primo)")
    return errori


def limite_corpo_rotta(rotte: list[dict[str, Any]], percorso: str) -> int | None:
    """maxBodyMB effettivo per ``percorso``: rotta più specifica che lo contiene e
    ne dichiara uno (il WAF applica tutti i limiti dei prefissi che combaciano,
    quindi vale il più piccolo: verifica_manifest impone che un prefisso più
    specifico non abbia un limite più alto di quello che lo contiene)."""
    candidati = [r for r in rotte if r.get("maxBodyMB") is not None
                 and (r["path"] == percorso or _contiene(r["path"], percorso))]
    if not candidati:
        return None
    return min(int(r["maxBodyMB"]) for r in candidati)


def errori_limiti_annidati(rotte: list[dict[str, Any]]) -> list[str]:
    """Un prefisso più specifico con limite maggiore di uno che lo contiene
    verrebbe comunque bloccato dal limite più piccolo (regole WAF in OR)."""
    errori: list[str] = []
    con_limite = [r for r in rotte if r.get("maxBodyMB") is not None]
    for g in con_limite:
        for s in con_limite:
            if _contiene(g["path"], s["path"]) and int(s["maxBodyMB"]) > int(g["maxBodyMB"]):
                errori.append(
                    f"ingress: maxBodyMB di {s['path']} ({s['maxBodyMB']}) maggiore di quello "
                              f"di {g['path']} ({g['maxBodyMB']}) che lo contiene: il WAF "
                              f"bloccherebbe "
                              "al limite più piccolo")
    return errori
