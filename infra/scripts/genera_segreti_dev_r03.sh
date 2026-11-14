#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# =============================================================================
# infra/scripts/genera_segreti_dev_r03.sh
#
# Segreti di SVILUPPO dei canali R03 per docker-compose.yml (mai committati:
# infra/*.env è in .gitignore). I valori non vengono mai stampati.
#
#   infra/dev_sftp.env          SFTP_TOKEN_CHIAVE_PRIVATA  (sftp-ingest: firma i
#                               token di canale, contratto C2; MAI la chiave del gateway)
#   infra/dev_canali_pub.env    SFTP_TOKEN_CHIAVE_PUBBLICA (gateway: verifica quei token)
#   infra/dev_webhook.env       WEBHOOK_CHIAVE_CIFRATURA   (notification: cifra i
#                               segreti dei webhook, 32 byte in base64)
#   infra/dev_posta_priv.env    POSTA_CHIAVE_PRIVATA       (R12a, VD-C-152: notification
#                               apre all'invio il collegamento d'invito sigillato)
#   infra/dev_posta_pub.env     POSTA_CHIAVE_PUBBLICA      (onboarding: lo sigilla)
#
# Uso (dalla radice):  bash infra/scripts/genera_segreti_dev_r03.sh [--forza]
# Senza --forza un file esistente non si tocca (i token e i segreti dei webhook
# già emessi restano validi). Richiede openssl.
# =============================================================================
set -euo pipefail
command -v openssl >/dev/null || { echo "ERRORE: openssl non trovato." >&2; exit 2; }
RADICE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRIV="$RADICE/infra/dev_sftp.env"
PUB="$RADICE/infra/dev_canali_pub.env"
WH="$RADICE/infra/dev_webhook.env"
POSTA_PRIV="$RADICE/infra/dev_posta_priv.env"
POSTA_PUB="$RADICE/infra/dev_posta_pub.env"
FORZA="${1:-}"

# PEM su una riga con \n letterali, come si aspettano le Settings
una_riga() { awk 'BEGIN{ORS="\\n"} {print}' "$1" | sed 's/\\n$//'; }

if [[ "$FORZA" == "--forza" || ! -f "$PRIV" || ! -f "$PUB" ]]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$TMP/priv.pem" 2>/dev/null
  openssl pkey -in "$TMP/priv.pem" -pubout -out "$TMP/pub.pem" 2>/dev/null
  umask 077
  { echo "# GENERATO_LOCALMENTE da infra/scripts/genera_segreti_dev_r03.sh — non committare"
    echo "SFTP_TOKEN_CHIAVE_PRIVATA=$(una_riga "$TMP/priv.pem")"; } > "$PRIV"
  { echo "# GENERATO_LOCALMENTE da infra/scripts/genera_segreti_dev_r03.sh — non committare"
    echo "SFTP_TOKEN_CHIAVE_PUBBLICA=$(una_riga "$TMP/pub.pem")"; } > "$PUB"
  chmod 600 "$PRIV" "$PUB"
  echo "Creati $PRIV e $PUB (coppia del canale SFTP, solo sviluppo)."
else
  echo "$PRIV e $PUB esistono già: lasciati com'erano (--forza per rigenerarli)."
fi

if [[ "$FORZA" == "--forza" || ! -f "$WH" ]]; then
  umask 077
  { echo "# GENERATO_LOCALMENTE da infra/scripts/genera_segreti_dev_r03.sh — non committare"
    echo "WEBHOOK_CHIAVE_CIFRATURA=$(openssl rand -base64 32)"; } > "$WH"
  chmod 600 "$WH"
  echo "Creato $WH (chiave di cifratura dei segreti webhook, solo sviluppo)."
else
  echo "$WH esiste già: lasciato com'era."
fi

# R12a (VD-C-152): coppia X25519 della posta (32 byte grezzi in base64url)
if [[ "$FORZA" == "--forza" || ! -f "$POSTA_PRIV" || ! -f "$POSTA_PUB" ]]; then
  TMPP="$(mktemp -d)"
  openssl genpkey -algorithm X25519 -out "$TMPP/x.pem" 2>/dev/null
  b64url() { base64 -w0 | tr '+/' '-_' | tr -d '='; }
  umask 077
  { echo "# GENERATO_LOCALMENTE da infra/scripts/genera_segreti_dev_r03.sh — non committare"
    echo "POSTA_CHIAVE_PRIVATA=$(openssl pkey -in "$TMPP/x.pem" -outform DER 2>/dev/null | tail -c 32 | b64url)"; } > "$POSTA_PRIV"
  { echo "# GENERATO_LOCALMENTE da infra/scripts/genera_segreti_dev_r03.sh — non committare"
    echo "POSTA_CHIAVE_PUBBLICA=$(openssl pkey -in "$TMPP/x.pem" -pubout -outform DER 2>/dev/null | tail -c 32 | b64url)"; } > "$POSTA_PUB"
  rm -rf "$TMPP"
  chmod 600 "$POSTA_PRIV" "$POSTA_PUB"
  echo "Creati $POSTA_PRIV e $POSTA_PUB (coppia della posta per gli inviti, solo sviluppo)."
else
  echo "$POSTA_PRIV e $POSTA_PUB esistono già: lasciati com'erano."
fi
