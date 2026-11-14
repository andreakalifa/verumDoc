# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Genera i manifest kustomize di infra/k8s da infra/helm/values*.yaml (VD-C-038).

Una sola fonte per la topologia di deploy: infra/helm/values.yaml (servizi, porte,
percorsi di salute, ConfigMap, chiavi dei Secret, flussi di rete, rotte Ingress,
lavoratori dedicati, job periodici, volumi, bilanciatori TCP). Il chart Helm la
legge direttamente; questo script produce l'equivalente kustomize:

  base/                      Deployment (servizi e lavoratori), Service, ConfigMap,
                             NetworkPolicy, HPA, CronJob, PVC, StorageClass,
                             ServiceAccount, Ingress, kustomization.yaml
  overlays/<amb>/            kustomization.yaml + patch delle differenze di
                             values.<amb>.yaml (repliche, HPA, ConfigMap, host,
                             NetworkPolicy, sorgenti dei bilanciatori TCP)
  secrets/                   modelli dei Secret con valori VUOTI (non applicati:
                             vedi secrets/README.md)

  testimone/                 R04: kustomization a sé del testimone della catena
                             (account AWS separato, values.testimone.yaml)

R04: ScaledObject KEDA al posto dell'HPA per i carichi con autoscaling.keda
(ritardo dei gruppi Kafka o coda in PostgreSQL, più la CPU), PgBouncer in
modalità transaction davanti a PostgreSQL (pgbouncer in values), Job una tantum
(migrazioni), ruoli IRSA reali nelle annotazioni dei ServiceAccount.

La vista dei carichi (servizi + lavoratori + job) è in topologia.py, condivisa
con verifica_manifest.py.

Uso:  python infra/k8s/genera_manifest.py      (rigenera; poi verifica_manifest.py)
Non modificare a mano i file generati: cambiare i values e rigenerare.
"""

from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import topologia  # noqa: E402

RADICE = Path(__file__).resolve().parents[2]
HELM = RADICE / "infra" / "helm"
K8S = RADICE / "infra" / "k8s"
NAMESPACE = "verumdoc"
AMBIENTI = ("staging", "prod")
#: Rilascio dell'account del testimone (R04, contratto K3): cartella e values
TESTIMONE = "testimone"
INTESTAZIONE = (
    # R18 (VD-P-190): intestazione SPDX come in ogni sorgente del repository
    "# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc\n"
    "# SPDX-License-Identifier: EUPL-1.2\n"
    "# FILE GENERATO da infra/k8s/genera_manifest.py a partire da infra/helm/values*.yaml.\n"
    "# Non modificare a mano: cambiare i values e rigenerare.\n"
)
#: Cartelle di base/ interamente generate (ripulite a ogni giro: un carico tolto
#: dai values non lascia file orfani).
CARTELLE_GENERATE = ("configmaps", "deployments", "services", "hpa", "network-policies",
                     "cronjobs", "volumes", "storage", "keda", "jobs", "pgbouncer")


class _SenzaAncore(yaml.SafeDumper):
    """Nessuna àncora YAML (&id001) nei file generati: ogni blocco si legge da solo."""

    def ignore_aliases(self, data):  # noqa: ANN001, ANN201 — firma di PyYAML
        return True


def _unisci(a: dict, b: dict) -> dict:
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _unisci(a[k], v)
        else:
            a[k] = copy.deepcopy(v)
    return a


def carica_values(*ambienti: str | None) -> dict:
    """values.yaml fuso con values.<amb>.yaml nell'ordine dato (come helm -f ... -f ...)."""
    valori = yaml.safe_load((HELM / "values.yaml").read_text(encoding="utf-8"))
    for ambiente in ambienti:
        if ambiente:
            extra = yaml.safe_load((HELM / f"values.{ambiente}.yaml").read_text(encoding="utf-8"))
            _unisci(valori, extra or {})
    return valori


def _namespace(v: dict) -> str:
    return str((v.get("global") or {}).get("namespace") or NAMESPACE)


def _dump(d: dict) -> str:
    return yaml.dump(d, Dumper=_SenzaAncore, sort_keys=False, allow_unicode=True, width=120)


def _scrivi(percorso: Path, documenti: list[dict] | dict, commento: str = "") -> str:
    if isinstance(documenti, dict):
        documenti = [documenti]
    corpo = "---\n".join(_dump(d) for d in documenti)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(
        INTESTAZIONE + (commento + "\n" if commento else "") + corpo, encoding="utf-8")
    return str(percorso.relative_to(K8S))


#: Namespace del rilascio in generazione (global.namespace: verumdoc o verumdoc-testimone)
_NS = {"valore": NAMESPACE}


def _meta(nome: str, app: str | None = None, componente: str | None = None) -> dict:
    m: dict = {"name": nome, "namespace": _NS["valore"]}
    if app:
        m["labels"] = {
            "app": app, "app.kubernetes.io/name": app, "app.kubernetes.io/part-of": "verumdoc"}
        if componente:
            m["labels"]["app.kubernetes.io/component"] = componente
    return m


def _contenitore(c: dict) -> dict:
    """Contenitore di un carico (servizio, lavoratore o job)."""
    contenitore: dict = {
        "name": c["servizio"],
        "image": c["image"],
        "imagePullPolicy": "IfNotPresent",
    }
    if c["command"]:
        # Sostituisce l'ENTRYPOINT dell'immagine (es. il gateway non rifà le migrazioni)
        contenitore["command"] = list(c["command"])
    porte = []
    if c["http"]:
        porte.append({"containerPort": c["port"], "name": "http", "protocol": "TCP"})
    if c["publicTcp"]:
        porte.append({"containerPort": c["publicTcp"]["targetPort"], "name": c["publicTcp"]["name"],
                      "protocol": "TCP"})
    if c.get("metricsPort"):
        porte.append(
            {"containerPort": int(c["metricsPort"]), "name": "metriche", "protocol": "TCP"})
    if porte:
        contenitore["ports"] = porte
    contenitore["securityContext"] = {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    contenitore["resources"] = copy.deepcopy(c["resources"])
    if c["http"] and c["health"]:
        contenitore["livenessProbe"] = {
            "httpGet": {"path": c["health"]["live"], "port": "http"},
            "initialDelaySeconds": 15, "periodSeconds": 30, "timeoutSeconds": 5,
            "failureThreshold": 3,
        }
        contenitore["readinessProbe"] = {
            "httpGet": {"path": c["health"]["ready"], "port": "http"},
            "initialDelaySeconds": 5, "periodSeconds": 10, "timeoutSeconds": 3,
            "failureThreshold": 3,
        }
        contenitore["startupProbe"] = {
            "httpGet": {"path": c["health"]["live"], "port": "http"},
            "failureThreshold": 30, "periodSeconds": 5,
        }
    env_from = []
    if c["config"]:
        env_from.append({"configMapRef": {"name": f"{c['servizio']}-config"}})
    if c["secretKeys"]:
        env_from.append({"secretRef": {"name": f"{c['servizio']}-secrets"}})
    if c.get("secretKeysPropri"):
        # Revisione 2 di R05 (S2): credenziali del solo lavoratore
        env_from.append({"secretRef": {"name": f"{c['nome']}-secrets"}})
    if env_from:
        contenitore["envFrom"] = env_from
    contenitore["env"] = [
        {"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
        {"name": "POD_NAMESPACE", "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}}},
    ] + [{"name": k, "value": str(v)} for k, v in {**c["env"], **c["entrypointEnv"]}.items()] + [
        # R04: variabili da un Secret diverso da quello del servizio (es. URL diretto
        # di PostgreSQL per le migrazioni, che non passano da PgBouncer)
        {"name": k, "valueFrom": {"secretKeyRef": {"name": r["secret"], "key": r["key"]}}}
        for k, r in (c.get("envSegreti") or {}).items()]
    contenitore["volumeMounts"] = [{"name": "tmp", "mountPath": "/tmp"}] + [  # noqa: S108 — percorso nel contenitore, non nel nodo
        {"name": v["name"], "mountPath": v["mountPath"]} for v in c["persistentVolumes"]]
    return contenitore


def _pod(c: dict, *, anti_affinita: bool = True) -> dict:
    uid = int(c["runAsUser"] or 1000)
    pod: dict = {
        "securityContext": {
            "runAsNonRoot": True, "runAsUser": uid, "runAsGroup": uid, "fsGroup": uid},
        # I lavoratori usano il ServiceAccount (e quindi il ruolo IRSA) del servizio
        "serviceAccountName": c.get("serviceAccount") or c["servizio"],
        "automountServiceAccountToken": c["irsa"] is not False and c.get("automountToken", True),
        "terminationGracePeriodSeconds": 60,
        "containers": [_contenitore(c)],
        "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": c["tmpSizeLimit"]}}] + [
            {
                "name": v["name"],
                "persistentVolumeClaim": {"claimName": f"{c['servizio']}-{v['name']}"}}
            for v in c["persistentVolumes"]],
    }
    if anti_affinita:
        pod["affinity"] = {"podAntiAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": [{
            "weight": 100,
            "podAffinityTerm": {
                "labelSelector": {"matchLabels": {"app": c["nome"]}},
                "topologyKey": "kubernetes.io/hostname",
            },
        }]}}
    return pod


def _annotazioni_metriche(c: dict) -> dict:
    if c["metrics"]:
        return {"prometheus.io/scrape": "true", "prometheus.io/port": str(
            c.get("metricsPort") or c["port"]),
                "prometheus.io/path": "/metrics"}
    return {"prometheus.io/scrape": "false"}


def deployment(c: dict) -> dict:
    strategia = (
        {"type": "Recreate"}
        if c.get("strategy") == "Recreate"
        else {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    )
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(c["nome"], c["nome"], c["componente"]),
        "spec": {
            "replicas": c.get("replicas") or 1,
            "selector": {"matchLabels": {"app": c["nome"]}},
            "strategy": strategia,
            "template": {
                "metadata": {
                    "labels": {"app": c["nome"], "app.kubernetes.io/name": c["nome"],
                               "app.kubernetes.io/component": c["componente"]},
                    "annotations": _annotazioni_metriche(c),
                },
                "spec": _pod(c),
            },
        },
    }


def cronjob(c: dict) -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": _meta(c["nome"], c["nome"], c["componente"]),
        "spec": {
            "schedule": c["schedule"],
            "timeZone": "Europe/Rome",
            "concurrencyPolicy": "Forbid",
            "startingDeadlineSeconds": 600,
            "successfulJobsHistoryLimit": 3,
            "failedJobsHistoryLimit": 3,
            "jobTemplate": {"spec": {
                "backoffLimit": int(c.get("backoffLimit", 2)),
                "activeDeadlineSeconds": int(c["activeDeadlineSeconds"]),
                "template": {
                    "metadata": {"labels": {"app": c["nome"], "app.kubernetes.io/name": c["nome"],
                                            "app.kubernetes.io/component": c["componente"]}},
                    "spec": {**_pod(c, anti_affinita=False), "restartPolicy": "Never"},
                },
            }},
        },
    }


def service(c: dict) -> dict:
    meta = _meta(c["nome"], c["nome"])
    if c["exposed"] and c["health"]:
        # Salute dei target dell'ALB sul percorso reale (non «/», che dà 404)
        meta["annotations"] = {"alb.ingress.kubernetes.io/healthcheck-path": c["health"]["ready"]}
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": meta,
        "spec": {
            "type": "ClusterIP",
            "selector": {"app": c["nome"]},
            "ports": [{"name": "http", "port": c["port"], "targetPort": "http", "protocol": "TCP"}],
        },
    }


def service_pubblico(c: dict) -> dict:
    """Service LoadBalancer TCP separato dall'ingress HTTP (es. SFTP → NLB)."""
    p = c["publicTcp"]
    meta = _meta(f"{c['nome']}-pubblico", c["nome"])
    meta["annotations"] = {k: str(v) for k, v in (p.get("annotations") or {}).items()}
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": meta,
        "spec": {
            "type": "LoadBalancer",
            "selector": {"app": c["nome"]},
            "loadBalancerSourceRanges": list(p.get("sourceRanges") or ["0.0.0.0/0"]),
            "ports": [
                {"name": p["name"], "port": p["port"], "targetPort": p["name"], "protocol": "TCP"}],
        },
    }


def configmap(nome: str, s: dict) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(f"{nome}-config", nome),
        "data": {k: str(v) for k, v in s["config"].items()},
    }


def secret_modello(nome: str, s: dict) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(f"{nome}-secrets", nome),
        "type": "Opaque",
        "stringData": dict.fromkeys(s["secretKeys"], ""),
    }


def _comportamento() -> dict:
    """Comportamento di scalatura comune a HPA e ScaledObject (KEDA crea un HPA)."""
    return {
        "scaleUp": {"stabilizationWindowSeconds": 60,
                    "policies": [{"type": "Pods", "value": 2, "periodSeconds": 60}]},
        "scaleDown": {"stabilizationWindowSeconds": 300,
                      "policies": [{"type": "Pods", "value": 1, "periodSeconds": 60}]},
    }


def scaled_object(c: dict, v: dict) -> dict:
    """ScaledObject KEDA (R04): ritardo dei gruppi Kafka o coda in PostgreSQL, più la CPU.

    KEDA crea e governa un HPA con lo stesso comportamento degli altri carichi:
    per lo stesso Deployment non si genera anche l'HPA (si contenderebbero le repliche).
    """
    a, k, glob = c["autoscaling"], c["autoscaling"]["keda"], v["keda"]
    config = topologia.config_effettiva(c)
    attivatori: list[dict] = []
    for kt in k.get("kafka") or []:
        attivatori.append({
            "type": "kafka",
            "metadata": {
                "bootstrapServers": config.get("KAFKA_BOOTSTRAP", ""),
                "consumerGroup": kt["gruppo"],
                "topic": kt["topic"],
                "lagThreshold": str(kt["lagThreshold"]),
                "offsetResetPolicy": "earliest",
                **{x: str(y) for x, y in (glob["kafka"].get("metadata") or {}).items()},
            },
            "authenticationRef": {"name": glob["kafka"]["authenticationRef"]},
        })
    for q in k.get("postgresql") or []:
        attivatori.append({
            "type": "postgresql",
            "metadata": {"query": " ".join(q["query"].split()),
                         "targetQueryValue": str(q["targetQueryValue"]),
                         "activationTargetQueryValue": "0"},
            "authenticationRef": {"name": glob["postgresql"]["authenticationRef"]},
        })
    attivatori.append({"type": "cpu", "metricType": "Utilization",
                       "metadata": {"value": str(a.get("targetCPUUtilization", 70))}})
    return {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": _meta(f"{c['nome']}-keda", c["nome"]),
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": c["nome"]},
            "minReplicaCount": a.get("minReplicas", 2),
            "maxReplicaCount": a.get("maxReplicas", 10),
            "pollingInterval": int(k.get("pollingInterval", 15)),
            "cooldownPeriod": int(k.get("cooldownPeriod", 300)),
            "advanced": {"horizontalPodAutoscalerConfig": {
                "name": f"{c['nome']}-hpa", "behavior": _comportamento()}},
            "triggers": attivatori,
        },
    }


def autenticazioni_keda(v: dict) -> list[dict]:
    """TriggerAuthentication di KEDA (R04): MSK con IAM (identità dell'operatore,
    ruolo keda-operatore del modulo Terraform iam_servizi) e PostgreSQL (Secret
    keda-postgres, utente di sola lettura sulle tabelle di coda)."""
    glob = v["keda"]
    return [
        {"apiVersion": "keda.sh/v1alpha1", "kind": "TriggerAuthentication",
         "metadata": _meta(glob["kafka"]["authenticationRef"]),
         "spec": {"podIdentity": {"provider": "aws", "identityOwner": "keda"}}},
        {"apiVersion": "keda.sh/v1alpha1", "kind": "TriggerAuthentication",
         "metadata": _meta(glob["postgresql"]["authenticationRef"]),
         "spec": {"secretTargetRef": [{"parameter": "connection",
                                       "name": glob["postgresql"][
                                           "secret"], "key": "connection"}]}},
    ]


def job(c: dict) -> dict:
    """Job una tantum (R04), es. migrazioni: con Helm è un hook pre-install/pre-upgrade,
    con kustomize/Argo CD un hook PreSync; terminato si elimina da sé (TTL)."""
    meta = _meta(c["nome"], c["nome"], c["componente"])
    meta["annotations"] = {
        "argocd.argoproj.io/hook": "PreSync",
        "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": meta,
        "spec": {
            "backoffLimit": 1,
            "activeDeadlineSeconds": int(c["activeDeadlineSeconds"]),
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": {"app": c["nome"], "app.kubernetes.io/name": c["nome"],
                                        "app.kubernetes.io/component": c["componente"]}},
                "spec": {**_pod(c, anti_affinita=False), "restartPolicy": "Never"},
            },
        },
    }


def pgbouncer_ini(v: dict) -> str:
    """pgbouncer.ini dai values (R04): due database logici, primario e replica."""
    pb = v["pgbouncer"]
    righe = ["; FILE GENERATO da infra/helm/values.yaml (pgbouncer): non modificare a mano.",
             "[databases]"]
    for nome, d in pb["databases"].items():
        righe.append(f"{nome} = host={d['host']} port={d.get('port', 5432)} dbname={d['dbname']}")
    righe.append("")
    righe.append("[pgbouncer]")
    righe.append("listen_addr = 0.0.0.0")
    righe.append(f"listen_port = {pb['port']}")
    for chiave, valore in pb["impostazioni"].items():
        righe.append(f"{chiave} = {valore}")
    return "\n".join(righe) + "\n"


def risorse_pgbouncer(v: dict) -> list[dict]:
    """ConfigMap, Deployment, Service, PodDisruptionBudget e ServiceAccount
    di PgBouncer (R04, piano §4.2). La NetworkPolicy è con le
    altre (politica_pgbouncer)."""
    pb = v["pgbouncer"]
    nome = "pgbouncer"
    uid = int(pb.get("runAsUser", 1000))
    montaggi = [{"name": "configurazione", "mountPath": "/etc/pgbouncer/pgbouncer.ini",
                 "subPath": "pgbouncer.ini", "readOnly": True},
                {"name": "segreti", "mountPath": "/etc/pgbouncer/segreti", "readOnly": True},
                {"name": "ca", "mountPath": "/etc/pgbouncer/ca", "readOnly": True},
                {"name": "tmp", "mountPath": "/tmp"}]  # noqa: S108 — percorso nel contenitore, non nel nodo
    contenitore = {
        "name": nome, "image": pb["image"], "imagePullPolicy": "IfNotPresent",
        "args": ["/etc/pgbouncer/pgbouncer.ini"],
        "ports": [{"containerPort": pb["port"], "name": "postgres", "protocol": "TCP"}],
        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]}},
        "resources": copy.deepcopy(pb["resources"]),
        "livenessProbe": {"tcpSocket": {"port": "postgres"}, "initialDelaySeconds": 10,
                          "periodSeconds": 20, "timeoutSeconds": 3, "failureThreshold": 3},
        "readinessProbe": {"tcpSocket": {"port": "postgres"}, "initialDelaySeconds": 3,
                           "periodSeconds": 10, "timeoutSeconds": 3, "failureThreshold": 3},
        "volumeMounts": montaggi,
    }
    pod = {
        "securityContext": {
            "runAsNonRoot": True, "runAsUser": uid, "runAsGroup": uid, "fsGroup": uid},
        "serviceAccountName": nome,
        "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 60,
        "containers": [contenitore],
        "volumes": [
            {"name": "configurazione", "configMap": {"name": "pgbouncer-config"}},
            {"name": "segreti", "secret": {"secretName": pb["secret"], "defaultMode": 0o400}},
            {"name": "ca", "configMap": {"name": pb["caConfigMap"]}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
        ],
        "affinity": {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
            "labelSelector": {"matchLabels": {"app": nome}},
            "topologyKey": "kubernetes.io/hostname"}]}},
    }
    return [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": _meta(nome, nome)},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": _meta("pgbouncer-config", nome),
         "data": {"pgbouncer.ini": pgbouncer_ini(v)}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": _meta(nome, nome, "pool"),
         "spec": {"replicas": int(pb["replicas"]), "selector": {"matchLabels": {"app": nome}},
                  "strategy": {
                      "type": "RollingUpdate",
                      "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
                  "template": {"metadata": {"labels": {"app": nome, "app.kubernetes.io/name": nome,
                                                       "app.kubernetes.io/component": "pool"},
                                            "annotations": {"prometheus.io/scrape": "false"}},
                               "spec": pod}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": _meta(nome, nome),
         "spec": {"type": "ClusterIP", "selector": {"app": nome},
                  "ports": [{"name": "postgres", "port": pb["port"], "targetPort": "postgres",
                             "protocol": "TCP"}]}},
        {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": _meta(nome, nome),
         "spec": {"maxUnavailable": 1, "selector": {"matchLabels": {"app": nome}}}},
    ]


def politica_pgbouncer(v: dict) -> dict:
    """NetworkPolicy di PgBouncer: ingresso dai soli carichi con «pgbouncer» in
    egress.data, uscita verso PostgreSQL (subnet dei dati)."""
    pb, net = v["pgbouncer"], v["network"]
    return {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": _meta("allow-pgbouncer", "pgbouncer"),
        "spec": {"podSelector": {"matchLabels": {"app": "pgbouncer"}}, "policyTypes": [
            "Ingress", "Egress"],
                 "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": c}}}
                                       for c in topologia.clienti_pgbouncer(v)],
                              "ports": [{"port": pb["port"], "protocol": "TCP"}]}],
                 "egress": [{"to": [{"ipBlock": {"cidr": x}} for x in net["dataCidrs"]],
                             "ports": [{"port": p, "protocol": "TCP"} for p in net[
                                 "dataPorts"]["postgres"]]}]},
    }


def usa_pgbouncer(v: dict) -> bool:
    return bool((v.get("pgbouncer") or {}).get("enabled")) and \
        topologia.account(v) == topologia.ACCOUNT_PRINCIPALE


def hpa(c: dict) -> dict:
    a = c["autoscaling"]
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": _meta(f"{c['nome']}-hpa", c["nome"]),
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": c["nome"]},
            "minReplicas": a.get("minReplicas", 2),
            "maxReplicas": a.get("maxReplicas", 10),
            "metrics": [
                {"type": "Resource", "resource": {"name": "cpu", "target": {
                    "type": "Utilization", "averageUtilization": a.get(
                        "targetCPUUtilization", 70)}}},
                {"type": "Resource", "resource": {"name": "memory", "target": {
                    "type": "Utilization", "averageUtilization": 80}}},
            ],
            "behavior": _comportamento(),
        },
    }


def pvc(nome: str, v: dict) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _meta(f"{nome}-{v['name']}", nome),
        "spec": {
            "accessModes": list(v["accessModes"]),
            "storageClassName": v["storageClassName"],
            "resources": {"requests": {"storage": v["size"]}},
        },
    }


def storage_class(nome: str, sc: dict) -> dict:
    return {
        "apiVersion": "storage.k8s.io/v1",
        "kind": "StorageClass",
        "metadata": {"name": nome, "labels": {"app.kubernetes.io/part-of": "verumdoc"}},
        "provisioner": sc["provisioner"],
        "reclaimPolicy": sc.get("reclaimPolicy", "Retain"),
        "volumeBindingMode": sc.get("volumeBindingMode", "Immediate"),
        "parameters": {k: str(v) for k, v in (sc.get("parameters") or {}).items()},
    }


def arn_ruolo(v: dict, nome: str) -> str:
    """ARN del ruolo IRSA creato da Terraform (modulo iam_servizi): stesso nome
    <progetto>-<ambienteIam>-<servizio>; l'account resta un segnaposto."""
    g = v["global"]
    return (f"arn:aws:iam::{g['awsAccountId']}:role/"
            f"{g['project']}-{g.get('ambienteIam') or g['environment']}-{nome}")


def service_account(nome: str, irsa: bool, v: dict) -> dict:
    sa: dict = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": _meta(nome, nome)}
    if irsa:
        # IRSA (R04, VD-C-101): ruolo reale del modulo Terraform iam_servizi
        sa["metadata"]["annotations"] = {"eks.amazonaws.com/role-arn": arn_ruolo(v, nome)}
    return sa


def _politica(v: dict, c: dict, porte_servizi: dict[str, int]) -> dict:
    net = v["network"]
    porta = [{"port": c["port"], "protocol": "TCP"}]
    ingresso: list[dict] = []
    if c["exposed"]:
        ingresso.append({"from": [{"ipBlock": {"cidr": x}} for x in net["albSourceCidrs"]],
                         "ports": copy.deepcopy(porta)})
    if c["publicTcp"]:
        ingresso.append({"from": [{"ipBlock": {"cidr": x}} for x in net["nlbSourceCidrs"]],
                         "ports": [{"port": c["publicTcp"]["targetPort"], "protocol": "TCP"}]})
    for chiamante in c["callers"]:
        ingresso.append({"from": [{"podSelector": {"matchLabels": {"app": chiamante}}}],
                         "ports": copy.deepcopy(porta)})
    if c["http"] or c.get("metricsPort"):
        ingresso.append({
            "from": [{"namespaceSelector": {"matchLabels": {
                "kubernetes.io/metadata.name": net["monitoringNamespace"]}}}],
            "ports": ([{"port": int(c["metricsPort"]), "protocol": "TCP"}] if c.get("metricsPort")
                      else copy.deepcopy(porta)),
        })
    uscita: list[dict] = []
    eg = c["egress"]
    for dest in eg.get("services", []):
        uscita.append({"to": [{"podSelector": {"matchLabels": {"app": dest}}}],
                       "ports": [{"port": porte_servizi[dest], "protocol": "TCP"}]})
    for dato in eg.get("data", []):
        if dato == topologia.PGBOUNCER:
            # PostgreSQL attraverso PgBouncer (R04): verso i pod di PgBouncer, non verso RDS
            uscita.append({"to": [{"podSelector": {"matchLabels": {"app": "pgbouncer"}}}],
                           "ports": [{"port": v["pgbouncer"]["port"], "protocol": "TCP"}]})
            continue
        uscita.append({"to": [{"ipBlock": {"cidr": x}} for x in net["dataCidrs"]],
                       "ports": [{"port": p, "protocol": "TCP"} for p in net["dataPorts"][dato]]})
    if eg.get("internetPorts"):
        # Solo IP pubblici: escluse VPC, reti private/riservate e metadati (internetExcept)
        uscita.append({"to": [
            {"ipBlock": {"cidr": "0.0.0.0/0", "except": list(net["internetExcept"])}}],
                       "ports": [{"port": p, "protocol": "TCP"} for p in eg["internetPorts"]]})
    return {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": _meta(f"allow-{c['nome']}", c["nome"]),
        "spec": {"podSelector": {"matchLabels": {"app": c["nome"]}}, "policyTypes": [
            "Ingress", "Egress"],
                 "ingress": ingresso, "egress": uscita},
    }


def network_policies(v: dict) -> list[dict]:
    porte = {n: s["port"] for n, s in topologia.servizi_attivi(v).items()}
    docs: list[dict] = [
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": _meta("default-deny-all"),
         "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": _meta("allow-dns-egress"),
         "spec": {"podSelector": {}, "policyTypes": ["Egress"], "egress": [{"ports": [
             {"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]}]}},
    ]
    for c in topologia.carichi(v) + topologia.cronjob(v) + topologia.jobs(v):
        docs.append(_politica(v, c, porte))
    if usa_pgbouncer(v):
        docs.append(politica_pgbouncer(v))
    return docs


def ingress(v: dict) -> dict:
    ing = v["ingress"]
    regole = []
    for chiave_host, host in sorted(ing["hosts"].items()):
        if not host:   # host tolto da un values di account (es. testimone)
            continue
        percorsi = []
        for r in ing["routes"]:
            if r.get("onlyHost") and r["onlyHost"] != chiave_host:
                continue
            percorsi.append({
                "path": r["path"], "pathType": "Prefix",
                "backend": {"service": {"name": r["service"],
                                        "port": {"number": v["services"][r["service"]]["port"]}}},
            })
        if percorsi:
            regole.append({"host": host, "http": {"paths": percorsi}})
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            **_meta("verumdoc"), "annotations": {k: str(x) for k, x in ing["annotations"].items()}},
        "spec": {"ingressClassName": ing["className"], "rules": regole},
    }


def genera_base(v: dict, base: Path | None = None) -> list[str]:
    """Manifest di un rilascio: ``base/`` (account principale) o ``testimone/``."""
    base = base or K8S / "base"
    _NS["valore"] = _namespace(v)
    for cartella in CARTELLE_GENERATE:
        shutil.rmtree(base / cartella, ignore_errors=True)
    servizi = topologia.servizi_attivi(v)

    def agg(percorso: Path, doc: dict | list[dict], commento: str = "") -> None:
        risorse.append(
            str(Path(_scrivi(percorso, doc, commento)).relative_to(base.relative_to(K8S))))

    # base/namespace.yaml (quota del namespace) è scritto a mano; quello del
    # testimone si genera dai values dell'account
    risorse: list[str] = [] if base.name == TESTIMONE else ["namespace.yaml"]
    if base.name == TESTIMONE:
        agg(base / "namespace.yaml", namespace_testimone(v),
            "# Account del testimone (R04, K3): namespace, quota e limiti propri.")
    agg(base / "service-accounts.yaml",
        [service_account(n, irsa, v) for n, _, irsa in topologia.account_di_servizio(v)])
    for nome, sc in (v.get("storageClasses") or {}).items():
        if any(vol.get("storageClassName") == nome for s in servizi.values()
               for vol in s.get("persistentVolumes") or []):
            agg(base / "storage" / f"{nome}-storageclass.yaml", storage_class(nome, sc),
                "# Risorsa di cluster: il file system EFS viene da Terraform (efs_file_system_id).")
    for doc in network_policies(v):
        agg(base / "network-policies" / f"{doc['metadata']['name']}.yaml", doc)
    for nome, s in servizi.items():
        if s.get("config"):
            agg(base / "configmaps" / f"{nome}-config.yaml", configmap(nome, s))
        for vol in s.get("persistentVolumes") or []:
            agg(base / "volumes" / f"{nome}-{vol['name']}-pvc.yaml", pvc(nome, vol))
        if s.get("secretKeys"):
            # R04: ``formatoSegreti`` (facoltativo) documenta nel modello il formato
            # atteso di una chiave (es. chiavi signed note del registro e del
            # testimone, NON PEM): chi riempie il Secret lo legge accanto al campo.
            formati = "".join(f"\n# {k}: {nota}"
                              for k, nota in (s.get("formatoSegreti") or {}).items())
            _scrivi(K8S / "secrets" / f"{nome}-secrets.template.yaml", secret_modello(nome, s),
                    "# Modello: chiavi lette dal servizio, valori VUOTI. Non si applica così:\n"
                    "# riempire fuori dal repository e cifrare (vedi secrets/README.md)."
                    + formati)
    for c in topologia.carichi(v) + topologia.cronjob(v):
        if c.get("secretKeysPropri"):
            # Revisione 2 di R05 (S2): Secret proprio del lavoratore
            _scrivi(K8S / "secrets" / f"{c['nome']}-secrets.template.yaml",
                    secret_modello(c["nome"], {"secretKeys": c["secretKeysPropri"]}),
                    "# Modello: chiavi lette SOLO dal lavoratore (il servizio esposto non le\n"
                    "# riceve), valori VUOTI. Riempire fuori dal repository e cifrare\n"
                    "# (vedi secrets/README.md).")
    usa_keda = False
    for c in topologia.carichi(v):
        agg(base / "deployments" / f"{c['nome']}-deployment.yaml", deployment(c))
        if not c["lavoratore"]:
            agg(base / "services" / f"{c['nome']}-service.yaml", service(c))
            if c["publicTcp"]:
                agg(base / "services" / f"{c['nome']}-pubblico-service.yaml", service_pubblico(c))
        if c["autoscaling"].get("enabled") and c["autoscaling"].get("keda"):
            usa_keda = True
            agg(base / "keda" / f"{c['nome']}-scaledobject.yaml", scaled_object(c, v))
        elif c["autoscaling"].get("enabled"):
            agg(base / "hpa" / f"{c['nome']}-hpa.yaml", hpa(c))
    if usa_keda:
        agg(base / "keda" / "trigger-authentication.yaml", autenticazioni_keda(v),
            "# KEDA ≥ keda.versioneMinima (values.yaml): CRD keda.sh/v1alpha1 installate nel "
            "cluster.")
    for c in topologia.cronjob(v):
        agg(base / "cronjobs" / f"{c['nome']}-cronjob.yaml", cronjob(c))
    for c in topologia.jobs(v):
        agg(base / "jobs" / f"{c['nome']}-job.yaml", job(c),
            "# Job una tantum PRIMA dei servizi (Argo CD: hook PreSync; a mano: kubectl delete "
            "job\n"
            "# e poi apply, perché un Job terminato non si aggiorna).")
    if usa_pgbouncer(v):
        agg(base / "pgbouncer" / "pgbouncer.yaml", risorse_pgbouncer(v),
            "# PgBouncer in modalità transaction davanti a PostgreSQL (R04, piano §4.2).")
    agg(base / "ingress.yaml", ingress(v))
    kust = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": _namespace(v),
        "resources": risorse,
        "labels": [{"pairs": {"project": "verumdoc", "app.kubernetes.io/managed-by": "kustomize"}}],
    }
    if base.name == TESTIMONE:
        registro = v["global"]["imageRegistry"]
        immagini = list(dict.fromkeys(s["image"] for s in servizi.values()))
        kust["labels"].append({"pairs": {"environment": v["global"]["environment"]}})
        kust["images"] = [{
            "name": i, "newName": f"{registro}/{i}", "newTag": v["global"]["imageTag"]}
                          for i in immagini]
    _scrivi(base / "kustomization.yaml", kust,
            "# I Secret <servizio>-secrets NON sono qui: vanno creati prima del deploy\n"
            "# (secrets/README.md), altrimenti i pod restano in CreateContainerConfigError.")
    return risorse


def namespace_testimone(v: dict) -> list[dict]:
    """Namespace, quota e limiti del rilascio del testimone (account separato)."""
    q = v["quotaNamespace"]
    ns = _namespace(v)
    return [
        {"apiVersion": "v1", "kind": "Namespace",
         "metadata": {
             "name": ns,
             "labels": {"project": "verumdoc", "environment": v["global"]["environment"]}}},
        {"apiVersion": "v1", "kind": "ResourceQuota", "metadata": {
            "name": f"{ns}-quota", "namespace": ns},
         "spec": {"hard": {k: str(x) for k, x in q.items()}}},
        {"apiVersion": "v1", "kind": "LimitRange", "metadata": {
            "name": f"{ns}-limiti", "namespace": ns},
         "spec": {"limits": [{"type": "Container",
                              "default": {"cpu": "500m", "memory": "512Mi"},
                              "defaultRequest": {"cpu": "100m", "memory": "128Mi"},
                              "max": {"cpu": "2", "memory": "4Gi"},
                              "min": {"cpu": "50m", "memory": "64Mi"}}]}},
    ]


def _patch(kind: str, nome: str, ops: list[dict]) -> dict:
    return {"target": {"kind": kind, "name": nome}, "patch": yaml.safe_dump(ops)}


def genera_overlay(ambiente: str, base_v: dict) -> None:
    v = carica_values(ambiente)
    _NS["valore"] = _namespace(base_v)
    cartella = K8S / "overlays" / ambiente
    patch: list[dict] = []
    carichi_base = {c["nome"]: c for c in topologia.carichi(base_v)}
    for c in topologia.carichi(v):
        b = carichi_base[c["nome"]]
        if c.get("replicas") != b.get("replicas"):
            patch.append(_patch("Deployment", c["nome"],
                                [{
                                    "op": "replace", "path": "/spec/replicas",
                                    "value": c["replicas"]}]))
        a, ab = c["autoscaling"], b["autoscaling"]
        if a.get("enabled") and (a.get("minReplicas"), a.get("maxReplicas")) != (
                ab.get("minReplicas"), ab.get("maxReplicas")):
            if a.get("keda"):
                patch.append(_patch("ScaledObject", f"{c['nome']}-keda", [
                    {"op": "replace", "path": "/spec/minReplicaCount", "value": a["minReplicas"]},
                    {"op": "replace", "path": "/spec/maxReplicaCount", "value": a["maxReplicas"]}]))
            else:
                patch.append(_patch("HorizontalPodAutoscaler", f"{c['nome']}-hpa", [
                    {"op": "replace", "path": "/spec/minReplicas", "value": a["minReplicas"]},
                    {"op": "replace", "path": "/spec/maxReplicas", "value": a["maxReplicas"]}]))
        if c["publicTcp"] and c["publicTcp"].get("sourceRanges") != b[
            "publicTcp"].get("sourceRanges"):
            patch.append(_patch("Service", f"{c['nome']}-pubblico", [
                {"op": "replace", "path": "/spec/loadBalancerSourceRanges",
                 "value": list(c["publicTcp"]["sourceRanges"])}]))
    servizi = topologia.servizi_attivi(v)
    for nome, _, irsa in topologia.account_di_servizio(v):
        if irsa and arn_ruolo(v, nome) != arn_ruolo(base_v, nome):
            # Ruolo IRSA dell'ambiente (verumdoc-<ambienteIam>-<servizio>, modulo iam_servizi)
            patch.append(_patch("ServiceAccount", nome, [
                {"op": "replace", "path": "/metadata/annotations/eks.amazonaws.com~1role-arn",
                 "value": arn_ruolo(v, nome)}]))
    for nome, s in servizi.items():
        b = base_v["services"][nome]
        diff = {k: str(val) for k, val in (s.get("config") or {}).items()
                if str((b.get("config") or {}).get(k)) != str(val)}
        if diff:
            patch.append(_patch("ConfigMap", f"{nome}-config",
                                [{"op": "replace", "path": f"/data/{k}", "value": val}
                                 for k, val in sorted(diff.items())]))
    # NetworkPolicy: con reti diverse (CIDR della VPC dell'ambiente) si sostituisce la spec
    politiche_base = {d["metadata"]["name"]: d for d in network_policies(base_v)}
    for d in network_policies(v):
        if d["spec"] != politiche_base[d["metadata"]["name"]]["spec"]:
            patch.append(_patch("NetworkPolicy", d["metadata"]["name"],
                                [{"op": "replace", "path": "/spec", "value": d["spec"]}]))
    pb, pbb = v.get("pgbouncer") or {}, base_v.get("pgbouncer") or {}
    if pb.get("enabled") and pb.get("replicas") != pbb.get("replicas"):
        patch.append(_patch("Deployment", "pgbouncer",
                            [{
                                "op": "replace", "path": "/spec/replicas",
                                "value": int(pb["replicas"])}]))
    if pb.get("enabled") and pgbouncer_ini(v) != pgbouncer_ini(base_v):
        patch.append(_patch("ConfigMap", "pgbouncer-config",
                            [{
                                "op": "replace", "path": "/data/pgbouncer.ini",
                                "value": pgbouncer_ini(v)}]))
    if v["ingress"]["hosts"] != base_v["ingress"]["hosts"]:
        ops = [{"op": "replace", "path": f"/spec/rules/{i}/host", "value": h}
               for i, (_, h) in enumerate(sorted(v["ingress"]["hosts"].items()))]
        patch.append(_patch("Ingress", "verumdoc", ops))
    registro = v["global"]["imageRegistry"]
    immagini = list(dict.fromkeys(s["image"] for s in servizi.values()))
    if pb.get("enabled"):
        immagini.append(pb["image"])
    kust = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": _namespace(v),
        "resources": ["../../base"],
        "labels": [{"pairs": {"environment": v["global"]["environment"]}}],
        "images": [{"name": i, "newName": f"{registro}/{i}", "newTag": v["global"]["imageTag"]}
                   for i in immagini],
    }
    if patch:
        kust["patches"] = patch
    _scrivi(cartella / "kustomization.yaml", kust,
            f"# Overlay {ambiente}: differenze di infra/helm/values.{ambiente}.yaml rispetto a "
            f"values.yaml.\n"
            "# La pipeline sostituisce i segnaposto DA_IMPOSTARE/DA_TERRAFORM (kustomize edit set "
            "image ...).")


def main() -> int:
    base_v = carica_values()
    for vecchio in (K8S / "secrets").glob("*-secrets.template.yaml"):
        vecchio.unlink()
    genera_base(base_v)
    for amb in AMBIENTI:
        genera_overlay(amb, base_v)
    # R04 (K3): rilascio a sé dell'account del testimone (produzione)
    genera_base(carica_values(TESTIMONE), K8S / TESTIMONE)
    print("Manifest kustomize rigenerati in", K8S)
    return 0


if __name__ == "__main__":
    sys.exit(main())
