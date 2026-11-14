#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Crea i topic Kafka di VerumDoc leggendo l'UNICA lista: infra/kafka/topics.yaml (VD-C-035).
#
# Variabili d'ambiente:
#   KAFKA_BOOTSTRAP       bootstrap server               (predefinito: localhost:9092)
#   KAFKA_REPLICATION     fattore di replica             (predefinito: 1; in produzione 3)
#   KAFKA_CONTAINER       se valorizzato, esegue kafka-topics dentro quel container
#                         con «docker exec» (sviluppo locale: vd-kafka)
#   KAFKA_CMD             comando kafka-topics da usare  (predefinito: kafka-topics se
#                         presente, altrimenti kafka-topics.sh)
#   KAFKA_COMMAND_CONFIG  file di proprietà client (TLS/SASL/IAM, per esempio su MSK),
#                         passato come --command-config
#   KAFKA_CONFIGS_CMD     comando kafka-configs           (predefinito: ricavato da
#                         KAFKA_CMD, kafka-configs o kafka-configs.sh)
#   TOPICS_FILE           lista dei topic                (predefinito: infra/kafka/topics.yaml)
#
# Esempi:
#   make kafka-topics                                       # locale, container vd-kafka
#   KAFKA_BOOTSTRAP=b-1.xxx:9094 KAFKA_REPLICATION=3 \
#     KAFKA_COMMAND_CONFIG=/etc/kafka/client.properties bash infra/scripts/kafka_create_topics.sh
#
# Topic già esistenti (R03): se hanno MENO partizioni di quelle dichiarate si
# aumentano (--alter; Kafka non le riduce mai: con più partizioni sul broker si
# segnala e basta) e retention.ms si riallinea con kafka-configs. Aumentare le
# partizioni cambia la partizione delle chiavi già scritte: farlo con i consumer
# fermi o accettare che l'ordine per chiave non valga durante il passaggio.
#
# Esce con errore alla prima creazione fallita (nessun errore viene mascherato).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPICS_FILE="${TOPICS_FILE:-$SCRIPT_DIR/../kafka/topics.yaml}"
BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
REPLICATION="${KAFKA_REPLICATION:-1}"

if [[ ! -f "$TOPICS_FILE" ]]; then
  echo "ERRORE: lista topic non trovata: $TOPICS_FILE" >&2
  exit 1
fi

if [[ -n "${KAFKA_CONTAINER:-}" ]]; then
  KAFKA=(docker exec "$KAFKA_CONTAINER" "${KAFKA_CMD:-kafka-topics}")
else
  if [[ -z "${KAFKA_CMD:-}" ]]; then
    if command -v kafka-topics >/dev/null 2>&1; then KAFKA_CMD=kafka-topics; else KAFKA_CMD=kafka-topics.sh; fi
  fi
  KAFKA=("$KAFKA_CMD")
fi
# kafka-configs accanto a kafka-topics (stesso suffisso .sh, stesso container)
CONFIGS_CMD="${KAFKA_CONFIGS_CMD:-$(basename "${KAFKA_CMD:-kafka-topics}" | sed 's/kafka-topics/kafka-configs/')}"
if [[ -n "${KAFKA_CONTAINER:-}" ]]; then
  CONFIGS=(docker exec "$KAFKA_CONTAINER" "$CONFIGS_CMD")
else
  CONFIGS=("$CONFIGS_CMD")
fi
EXTRA=()
if [[ -n "${KAFKA_COMMAND_CONFIG:-}" ]]; then
  EXTRA=(--command-config "$KAFKA_COMMAND_CONFIG")
fi

# Estrae «nome partizioni retention» per riga dal formato piatto di topics.yaml
leggi_topic() {
  awk '
    function val(s) { sub(/^[^:]*:[ \t]*/, "", s); sub(/[ \t]*#.*$/, "", s); return s }
    /^[ \t]*-[ \t]*name:/      { if (n != "") print n, p, r; n = val($0); p = ""; r = "" ; next }
    /^[ \t]*partitions:/       { p = val($0); next }
    /^[ \t]*retention_ms:/     { r = val($0); next }
    END                        { if (n != "") print n, p, r }
  ' "$TOPICS_FILE"
}

conteggio=0
echo "Creazione topic Kafka su $BOOTSTRAP (replica: $REPLICATION) da $TOPICS_FILE"
while read -r nome partizioni retention; do
  if [[ -z "$nome" || -z "$partizioni" || -z "$retention" ]]; then
    echo "ERRORE: voce incompleta in $TOPICS_FILE: '$nome $partizioni $retention'" >&2
    exit 1
  fi
  echo "  $nome (partizioni: $partizioni, retention.ms: $retention)"
  "${KAFKA[@]}" --bootstrap-server "$BOOTSTRAP" "${EXTRA[@]}" \
    --create --if-not-exists \
    --topic "$nome" \
    --partitions "$partizioni" \
    --replication-factor "$REPLICATION" \
    --config "retention.ms=$retention"
  attuali=$("${KAFKA[@]}" --bootstrap-server "$BOOTSTRAP" "${EXTRA[@]}" --describe --topic "$nome" \
    | grep -o 'PartitionCount: *[0-9]*' | head -1 | grep -o '[0-9]*$' || true)
  if [[ -z "$attuali" ]]; then
    echo "ERRORE: impossibile leggere le partizioni di $nome" >&2
    exit 1
  elif (( attuali < partizioni )); then
    echo "    partizioni $attuali → $partizioni (aumento)"
    "${KAFKA[@]}" --bootstrap-server "$BOOTSTRAP" "${EXTRA[@]}" --alter --topic "$nome" --partitions "$partizioni"
  elif (( attuali > partizioni )); then
    echo "    ATTENZIONE: $nome ha $attuali partizioni sul broker, $partizioni dichiarate (non si riducono)"
  fi
  "${CONFIGS[@]}" --bootstrap-server "$BOOTSTRAP" "${EXTRA[@]}" --alter --entity-type topics \
    --entity-name "$nome" --add-config "retention.ms=$retention" >/dev/null
  conteggio=$((conteggio + 1))
done < <(leggi_topic)

echo "---"
echo "Topic presenti sul broker:"
"${KAFKA[@]}" --bootstrap-server "$BOOTSTRAP" "${EXTRA[@]}" --list
echo "Completato: $conteggio topic dichiarati in $TOPICS_FILE."
