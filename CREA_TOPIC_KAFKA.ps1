# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Crea i topic Kafka di VerumDoc nel container locale (Windows, sviluppo).
# La lista NON e' qui: si legge l'unica fonte infra/kafka/topics.yaml (VD-C-035),
# la stessa usata da infra/scripts/kafka_create_topics.sh.
#
# Topic gia' esistenti (R03): con MENO partizioni di quelle dichiarate si
# aumentano (Kafka non le riduce mai) e retention.ms si riallinea.
#
# Uso (dopo docker compose up):
#   powershell -ExecutionPolicy Bypass -File .\CREA_TOPIC_KAFKA.ps1
#   .\CREA_TOPIC_KAFKA.ps1 -Container vd-kafka -Replication 1

param(
    [string]$Container = "vd-kafka",
    [string]$Bootstrap = "localhost:9092",
    [int]$Replication = 1,
    [string]$TopicsFile = (Join-Path $PSScriptRoot "infra/kafka/topics.yaml")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $TopicsFile)) {
    throw "Lista topic non trovata: $TopicsFile"
}

# Lettura del formato piatto di topics.yaml (name / partitions / retention_ms)
function Get-Valore([string]$riga) {
    $v = ($riga -replace '^[^:]*:\s*', '') -replace '\s*#.*$', ''
    return $v.Trim()
}

$topics = @()
$corrente = $null
foreach ($riga in Get-Content $TopicsFile) {
    if ($riga -match '^\s*-\s*name:') {
        if ($corrente) { $topics += $corrente }
        $corrente = @{ Nome = (Get-Valore $riga); Partizioni = $null; Retention = $null }
    } elseif ($corrente -and $riga -match '^\s*partitions:') {
        $corrente.Partizioni = Get-Valore $riga
    } elseif ($corrente -and $riga -match '^\s*retention_ms:') {
        $corrente.Retention = Get-Valore $riga
    }
}
if ($corrente) { $topics += $corrente }

foreach ($t in $topics) {
    if (-not $t.Partizioni -or -not $t.Retention) {
        throw "Voce incompleta in ${TopicsFile}: $($t.Nome)"
    }
    Write-Host "Creazione topic: $($t.Nome) (partizioni $($t.Partizioni), retention.ms $($t.Retention))" -ForegroundColor Cyan
    docker exec $Container kafka-topics `
        --bootstrap-server $Bootstrap `
        --create --if-not-exists `
        --topic $t.Nome `
        --partitions $t.Partizioni `
        --replication-factor $Replication `
        --config "retention.ms=$($t.Retention)"
    if ($LASTEXITCODE -ne 0) { throw "Creazione del topic $($t.Nome) fallita (codice $LASTEXITCODE)" }

    $descr = docker exec $Container kafka-topics --bootstrap-server $Bootstrap --describe --topic $t.Nome
    if ($LASTEXITCODE -ne 0) { throw "Lettura del topic $($t.Nome) fallita (codice $LASTEXITCODE)" }
    $m = [regex]::Match(($descr -join "`n"), 'PartitionCount:\s*(\d+)')
    if (-not $m.Success) { throw "Partizioni del topic $($t.Nome) non leggibili" }
    $attuali = [int]$m.Groups[1].Value
    $dichiarate = [int]$t.Partizioni
    if ($attuali -lt $dichiarate) {
        Write-Host "    partizioni $attuali -> $dichiarate (aumento)" -ForegroundColor Yellow
        docker exec $Container kafka-topics --bootstrap-server $Bootstrap --alter --topic $t.Nome --partitions $dichiarate
        if ($LASTEXITCODE -ne 0) { throw "Aumento delle partizioni di $($t.Nome) fallito" }
    } elseif ($attuali -gt $dichiarate) {
        Write-Host "    ATTENZIONE: $($t.Nome) ha $attuali partizioni, $dichiarate dichiarate (non si riducono)" -ForegroundColor Yellow
    }
    docker exec $Container kafka-configs --bootstrap-server $Bootstrap --alter --entity-type topics `
        --entity-name $t.Nome --add-config "retention.ms=$($t.Retention)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Allineamento di retention.ms di $($t.Nome) fallito" }
}

Write-Host ""
Write-Host "$($topics.Count) topic dichiarati in $TopicsFile. Topic presenti sul broker:" -ForegroundColor Green
docker exec $Container kafka-topics --bootstrap-server $Bootstrap --list
