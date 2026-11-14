{{- /*
SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
SPDX-License-Identifier: EUPL-1.2
*/ -}}
{{/*
Helper templates — VerumDoc Helm chart (VD-C-038)
*/}}

{{/*
Label standard per tutte le risorse
*/}}
{{- define "verumdoc.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: verumdoc
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
project: verumdoc
environment: {{ .Values.global.environment }}
{{- end }}

{{/*
R04 (contratto K3): "true" se il servizio è abilitato e appartiene all'account
del rilascio (global.account; il testimone ha un account AWS separato). Stessa
regola di infra/k8s/topologia.py (servizi_attivi). Parametri: root, svc.
*/}}
{{- define "verumdoc.attivo" -}}
{{- $acc := default "principale" .root.Values.global.account -}}
{{- if and .svc.enabled (eq (toString (default "principale" .svc.account)) (toString $acc)) -}}true{{- end -}}
{{- end }}

{{/*
ServiceAccount con l'eventuale annotazione IRSA. Parametri: root, nome, irsa.
*/}}
{{- define "verumdoc.serviceaccount" -}}
{{- $root := .root -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .nome }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: {{ .nome }}
    {{- include "verumdoc.labels" $root | nindent 4 }}
  {{- if .irsa }}
  annotations:
    # IRSA: accesso AWS (S3, KMS, MSK) senza credenziali statiche
    eks.amazonaws.com/role-arn: {{ printf "arn:aws:iam::%s:role/%s-%s-%s" $root.Values.global.awsAccountId $root.Values.global.project ($root.Values.global.ambienteIam | default $root.Values.global.environment) .nome | quote }}
  {{- end }}
{{- end }}

{{/*
Pod di un carico (R03 i2): servizio, suo lavoratore (services.<s>.workers) o job
periodico (services.<s>.cronJobs). Stessa immagine, ConfigMap, Secret e
ServiceAccount del servizio; «env» del lavoratore prevale sulla ConfigMap.
Parametri (dict): root, nome (carico), servizio, svc, w (spec del lavoratore o
del job, dict vuoto per il servizio), http (bool), lavoratore (bool),
antiAffinita (bool). Stessa logica di infra/k8s/topologia.py.
*/}}
{{- define "verumdoc.pod" -}}
{{- $root := .root -}}
{{- $svc := .svc -}}
{{- $w := .w -}}
{{- $uid := $svc.runAsUser | default 1000 -}}
securityContext:
  runAsNonRoot: true
  runAsUser: {{ $uid }}
  runAsGroup: {{ $uid }}
  fsGroup: {{ $uid }}
{{- if .job }}
# Job una tantum (R04): parte prima delle altre risorse del rilascio (hook),
# quindi nessun ServiceAccount, ConfigMap o Secret del chart
serviceAccountName: default
automountServiceAccountToken: false
{{- else if $w.iam }}
# R04: lavoratore con ServiceAccount e ruolo propri (permessi minori del servizio)
serviceAccountName: {{ .nome }}
automountServiceAccountToken: true
{{- else }}
serviceAccountName: {{ .servizio }}
{{- /* revisione R06 (V3): un job può rinunciare al token del ServiceAccount */}}
automountServiceAccountToken: {{ and (ne (toString $svc.irsa) "false") (ne (toString $w.automountServiceAccountToken) "false") }}
{{- end }}
terminationGracePeriodSeconds: 60
containers:
  - name: {{ .servizio }}
    image: "{{ $root.Values.global.imageRegistry }}/{{ $svc.image }}:{{ $root.Values.global.imageTag }}"
    imagePullPolicy: {{ $root.Values.global.imagePullPolicy }}
    {{- if $w.command }}
    # Sostituisce l'ENTRYPOINT dell'immagine (es. il gateway non rifà le migrazioni)
    command: {{ toJson $w.command }}
    {{- end }}
    {{- if or .http (and (not .lavoratore) $svc.publicTcp) $w.metricsPort }}
    ports:
      {{- if .http }}
      - containerPort: {{ $svc.port }}
        name: http
        protocol: TCP
      {{- end }}
      {{- if $w.metricsPort }}
      - containerPort: {{ $w.metricsPort }}
        name: metriche
        protocol: TCP
      {{- end }}
      {{- if and (not .lavoratore) $svc.publicTcp }}
      - containerPort: {{ $svc.publicTcp.targetPort }}
        name: {{ $svc.publicTcp.name }}
        protocol: TCP
      {{- end }}
    {{- end }}
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: [ALL]
    resources:
      {{- toYaml (default $svc.resources $w.resources) | nindent 6 }}
    {{- if .http }}
    livenessProbe:
      httpGet:
        path: {{ $svc.health.live }}
        port: http
      initialDelaySeconds: 15
      periodSeconds: 30
      timeoutSeconds: 5
      failureThreshold: 3
    readinessProbe:
      httpGet:
        path: {{ $svc.health.ready }}
        port: http
      initialDelaySeconds: 5
      periodSeconds: 10
      timeoutSeconds: 3
      failureThreshold: 3
    startupProbe:
      httpGet:
        path: {{ $svc.health.live }}
        port: http
      failureThreshold: 30
      periodSeconds: 5
    {{- end }}
    {{- $segretiServizio := and $svc.secretKeys (ne (toString $w.segretiServizio) "false") }}
    {{- if and (not .job) (or $svc.config $segretiServizio (and .lavoratore $w.secretKeys)) }}
    envFrom:
      {{- if $svc.config }}
      - configMapRef:
          name: {{ .servizio }}-config
      {{- end }}
      {{- if $segretiServizio }}
      - secretRef:
          name: {{ .servizio }}-secrets
      {{- end }}
      {{- if and .lavoratore $w.secretKeys }}
      # Revisione 2 di R05 (S2): credenziali del SOLO lavoratore (il servizio
      # esposto non le riceve): Secret <servizio>-<lavoratore>-secrets
      - secretRef:
          name: {{ .nome }}-secrets
      {{- end }}
    {{- end }}
    env:
      - name: POD_NAME
        valueFrom:
          fieldRef:
            fieldPath: metadata.name
      - name: POD_NAMESPACE
        valueFrom:
          fieldRef:
            fieldPath: metadata.namespace
      {{- range $k, $v := (merge (dict) (default (dict) $w.env) (ternary (default (dict) $svc.entrypointEnv) (dict) (not .lavoratore))) }}
      - name: {{ $k }}
        value: {{ toString $v | quote }}
      {{- end }}
      {{- range $k, $r := $w.envSegreti }}
      # R04: da un Secret diverso da quello del servizio (es. URL diretto per le migrazioni)
      - name: {{ $k }}
        valueFrom:
          secretKeyRef:
            name: {{ $r.secret }}
            key: {{ $r.key }}
      {{- end }}
    volumeMounts:
      - name: tmp
        mountPath: /tmp
      {{- range $v := $svc.persistentVolumes }}
      - name: {{ $v.name }}
        mountPath: {{ $v.mountPath }}
      {{- end }}
volumes:
  - name: tmp
    emptyDir:
      sizeLimit: {{ $svc.tmpSizeLimit | default "1Gi" }}
  {{- range $v := $svc.persistentVolumes }}
  - name: {{ $v.name }}
    persistentVolumeClaim:
      claimName: {{ $.servizio }}-{{ $v.name }}
  {{- end }}
{{- if .antiAffinita }}
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: {{ .nome }}
          topologyKey: kubernetes.io/hostname
{{- end }}
{{- end }}

{{/*
Deployment di un servizio o di un suo lavoratore. Parametri come verumdoc.pod
più componente.
*/}}
{{- define "verumdoc.deployment" -}}
{{- $root := .root -}}
{{- $svc := .svc -}}
{{- $w := .w -}}
{{- $as := default $svc.autoscaling $w.autoscaling -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .nome }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: {{ .nome }}
    app.kubernetes.io/component: {{ .componente }}
    {{- include "verumdoc.labels" $root | nindent 4 }}
spec:
  {{- if not $as.enabled }}
  replicas: {{ default (default 1 $svc.replicas) $w.replicas }}
  {{- end }}
  selector:
    matchLabels:
      app: {{ .nome }}
  strategy:
    {{- if eq (toString (default $svc.strategy $w.strategy)) "Recreate" }}
    type: Recreate
    {{- else }}
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
    {{- end }}
  template:
    metadata:
      labels:
        app: {{ .nome }}
        app.kubernetes.io/component: {{ .componente }}
        {{- include "verumdoc.labels" $root | nindent 8 }}
      annotations:
        {{- if $w.metricsPort }}
        # R04: lavoratore senza HTTP con le sole metriche Prometheus su una porta propria
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ $w.metricsPort | quote }}
        prometheus.io/path: /metrics
        {{- else if and .http (ne (toString $svc.metrics) "false") }}
        # GET /metrics (libs/shared/observability/metriche.py, VD-C-033)
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ $svc.port | quote }}
        prometheus.io/path: /metrics
        {{- else }}
        prometheus.io/scrape: "false"
        {{- end }}
        {{- if $svc.config }}
        checksum/config: {{ toJson $svc.config | sha256sum }}
        {{- end }}
    spec:
      {{- include "verumdoc.pod" (dict "root" $root "nome" .nome "servizio" .servizio "svc" $svc "w" $w "http" .http "lavoratore" .lavoratore "antiAffinita" true) | nindent 6 }}
{{- end }}

{{/*
NetworkPolicy di un carico: dall'ALB se esposto, dal NLB se ha publicTcp, dai
chiamanti dichiarati, da Prometheus se ha HTTP; in uscita servizi, dati e
Internet (solo IP pubblici: network.internetExcept).
Parametri: root, nome, svc, eg (egress), callers, exposed, publicTcp, http.
*/}}
{{- define "verumdoc.networkpolicy" -}}
{{- $root := .root -}}
{{- $net := $root.Values.network -}}
{{- $svc := .svc -}}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-{{ .nome }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: {{ .nome }}
    {{- include "verumdoc.labels" $root | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app: {{ .nome }}
  policyTypes: [Ingress, Egress]
  {{- if not (or .exposed .publicTcp .callers .http .metricsPort) }}
  ingress: []
  {{- else }}
  ingress:
    {{- if .exposed }}
    # Dall'ALB (subnet pubbliche, target-type ip)
    - from:
        {{- range $c := $net.albSourceCidrs }}
        - ipBlock: {cidr: {{ $c }}}
        {{- end }}
      ports:
        - {port: {{ $svc.port }}, protocol: TCP}
    {{- end }}
    {{- if .publicTcp }}
    # Dal Network Load Balancer TCP (subnet pubbliche, preserve_client_ip disattivato)
    - from:
        {{- range $c := $net.nlbSourceCidrs }}
        - ipBlock: {cidr: {{ $c }}}
        {{- end }}
      ports:
        - {port: {{ .publicTcp.targetPort }}, protocol: TCP}
    {{- end }}
    {{- range $c := .callers }}
    - from:
        - podSelector:
            matchLabels: {app: {{ $c }}}
      ports:
        - {port: {{ $svc.port }}, protocol: TCP}
    {{- end }}
    {{- if or .http .metricsPort }}
    # Prometheus dal namespace di monitoraggio
    - from:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: {{ $net.monitoringNamespace }}}
      ports:
        - {port: {{ .metricsPort | default $svc.port }}, protocol: TCP}
    {{- end }}
  {{- end }}
  {{- if not (or .eg.services .eg.data .eg.internetPorts) }}
  egress: []
  {{- else }}
  egress:
    {{- range $s := .eg.services }}
    - to:
        - podSelector:
            matchLabels: {app: {{ $s }}}
      ports:
        - {port: {{ (index $root.Values.services $s).port }}, protocol: TCP}
    {{- end }}
    {{- range $d := .eg.data }}
    {{- if eq $d "pgbouncer" }}
    # PostgreSQL attraverso PgBouncer (R04)
    - to:
        - podSelector:
            matchLabels: {app: pgbouncer}
      ports:
        - {port: {{ $root.Values.pgbouncer.port }}, protocol: TCP}
    {{- else }}
    # {{ $d }}
    - to:
        {{- range $c := $net.dataCidrs }}
        - ipBlock: {cidr: {{ $c }}}
        {{- end }}
      ports:
        {{- range $p := index $net.dataPorts $d }}
        - {port: {{ $p }}, protocol: TCP}
        {{- end }}
    {{- end }}
    {{- end }}
    {{- if .eg.internetPorts }}
    # Internet: solo IP pubblici (escluse VPC, reti private/riservate, metadati)
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              {{- range $c := $net.internetExcept }}
              - {{ $c }}
              {{- end }}
      ports:
        {{- range $p := .eg.internetPorts }}
        - {port: {{ $p }}, protocol: TCP}
        {{- end }}
    {{- end }}
  {{- end }}
{{- end }}

{{/*
HorizontalPodAutoscaler di un carico. Parametri: root, nome, as (autoscaling).
*/}}
{{- define "verumdoc.hpa" -}}
{{- $root := .root -}}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ .nome }}-hpa
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: {{ .nome }}
    {{- include "verumdoc.labels" $root | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ .nome }}
  minReplicas: {{ .as.minReplicas | default 2 }}
  maxReplicas: {{ .as.maxReplicas | default 10 }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .as.targetCPUUtilization | default 70 }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    {{- include "verumdoc.comportamento" . | nindent 4 }}
{{- end }}

{{/*
Comportamento di scalatura comune a HPA e ScaledObject (KEDA crea un HPA).
*/}}
{{- define "verumdoc.comportamento" -}}
scaleUp:
  stabilizationWindowSeconds: 60
  policies:
    - type: Pods
      value: 2
      periodSeconds: 60
scaleDown:
  stabilizationWindowSeconds: 300
  policies:
    - type: Pods
      value: 1
      periodSeconds: 60
{{- end }}

{{/*
ScaledObject KEDA (R04): ritardo dei gruppi Kafka o coda in PostgreSQL, più la
CPU. Per lo stesso Deployment non c'è anche l'HPA (KEDA ne crea uno suo).
Parametri: root, nome, as (autoscaling), bootstrap (KAFKA_BOOTSTRAP effettivo).
*/}}
{{- define "verumdoc.scaledobject" -}}
{{- $root := .root -}}
{{- $k := .as.keda -}}
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: {{ .nome }}-keda
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: {{ .nome }}
    {{- include "verumdoc.labels" $root | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ .nome }}
  minReplicaCount: {{ .as.minReplicas | default 2 }}
  maxReplicaCount: {{ .as.maxReplicas | default 10 }}
  pollingInterval: {{ $k.pollingInterval | default 15 }}
  cooldownPeriod: {{ $k.cooldownPeriod | default 300 }}
  advanced:
    horizontalPodAutoscalerConfig:
      name: {{ .nome }}-hpa
      behavior:
        {{- include "verumdoc.comportamento" . | nindent 8 }}
  triggers:
    {{- range $t := $k.kafka }}
    - type: kafka
      metadata:
        bootstrapServers: {{ $.bootstrap | quote }}
        consumerGroup: {{ $t.gruppo | quote }}
        topic: {{ $t.topic | quote }}
        lagThreshold: {{ toString $t.lagThreshold | quote }}
        offsetResetPolicy: earliest
        {{- range $mk, $mv := $root.Values.keda.kafka.metadata }}
        {{ $mk }}: {{ toString $mv | quote }}
        {{- end }}
      authenticationRef:
        name: {{ $root.Values.keda.kafka.authenticationRef }}
    {{- end }}
    {{- range $q := $k.postgresql }}
    - type: postgresql
      metadata:
        query: {{ $q.query | quote }}
        targetQueryValue: {{ toString $q.targetQueryValue | quote }}
        activationTargetQueryValue: "0"
      authenticationRef:
        name: {{ $root.Values.keda.postgresql.authenticationRef }}
    {{- end }}
    - type: cpu
      metricType: Utilization
      metadata:
        value: {{ toString (.as.targetCPUUtilization | default 70) | quote }}
{{- end }}
