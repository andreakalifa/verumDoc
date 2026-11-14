#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# =============================================================================
# tools/dev/genera_chiavi_dev.sh
#
# Genera in locale la coppia di chiavi RS256 per lo sviluppo e il pepper delle
# chiavi API (API_KEY_PEPPER, 32 byte casuali) e scrive infra/dev_jwt.env (letto
# da docker-compose.yml). Il file NON va mai committato: è escluso da .gitignore.
# Regole: REGOLE_SVILUPPO.md §19.3. I valori non vengono mai stampati.
#
# USO (dalla radice del monorepo):
#   bash tools/dev/genera_chiavi_dev.sh          # non sovrascrive un file esistente;
#                                                # se manca solo API_KEY_PEPPER lo aggiunge
#   bash tools/dev/genera_chiavi_dev.sh --forza  # rigenera tutto (le chiavi API
#                                                # generate prima smettono di valere)
# Richiede openssl.
# =============================================================================
set -euo pipefail
DEST="infra/dev_jwt.env"

command -v openssl >/dev/null || { echo "ERRORE: openssl non trovato." >&2; exit 2; }

# Pepper HMAC delle chiavi API (gateway e onboarding): 32 byte casuali in esadecimale.
nuovo_pepper() { openssl rand -hex 32; }

if [[ -f "$DEST" && "${1:-}" != "--forza" ]]; then
  if grep -q '^API_KEY_PEPPER=' "$DEST"; then
    echo "$DEST esiste già: lo lascio com'è (usa --forza per rigenerarlo)."
  else
    # File di una versione precedente: si aggiunge solo il pepper, le chiavi JWT restano.
    if [[ -s "$DEST" && -n "$(tail -c1 "$DEST")" ]]; then echo >> "$DEST"; fi
    echo "API_KEY_PEPPER=$(nuovo_pepper)" >> "$DEST"
    chmod 600 "$DEST"
    echo "Aggiunto API_KEY_PEPPER a $DEST (chiavi JWT invariate)."
  fi
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$TMP/priv.pem" 2>/dev/null
openssl pkey -in "$TMP/priv.pem" -pubout -out "$TMP/pub.pem" 2>/dev/null

# Le variabili d'ambiente contengono il PEM su una riga con \n letterali,
# come si aspettano le Settings dei servizi.
una_riga() { awk 'BEGIN{ORS="\\n"} {print}' "$1" | sed 's/\\n$//'; }

mkdir -p "$(dirname "$DEST")"
{
  echo "# GENERATO_LOCALMENTE da tools/dev/genera_chiavi_dev.sh — non committare"
  echo "JWT_PRIVATE_KEY=$(una_riga "$TMP/priv.pem")"
  echo "JWT_PUBLIC_KEY=$(una_riga "$TMP/pub.pem")"
  echo "API_KEY_PEPPER=$(nuovo_pepper)"
} > "$DEST"
chmod 600 "$DEST"
echo "Creato $DEST (solo per sviluppo locale, non committarlo)."
