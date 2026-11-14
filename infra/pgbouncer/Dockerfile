# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# PgBouncer di VerumDoc (R04, piano §4.2): pool in modalità transaction davanti
# a PostgreSQL. Immagine costruita dalla CI dal pacchetto della distribuzione
# (nessuna immagine di terzi non verificata); configurazione e segreti montati
# dal chart (ConfigMap pgbouncer-config, Secret pgbouncer-secrets, ConfigMap
# pgbouncer-ca): l'immagine non contiene credenziali.
#
# Versione minima 1.21: prima PgBouncer non gestisce in modalità transaction i
# prepared statement di protocollo che asyncpg usa (max_prepared_statements).
# La build fallisce se il pacchetto è più vecchio.
FROM alpine:3.21

RUN apk add --no-cache pgbouncer ca-certificates \
 && v="$(pgbouncer --version | head -1 | sed -E 's/^[^0-9]*([0-9]+)\.([0-9]+).*/\1 \2/')" \
 && set -- $v \
 && if [ "$1" -lt 1 ] || { [ "$1" -eq 1 ] && [ "$2" -lt 21 ]; }; then \
      echo "PgBouncer $1.$2 < 1.21: prepared statement di protocollo non supportati" >&2; exit 1; fi \
 && addgroup -S -g 1000 verumdoc \
 && adduser -S -D -H -u 1000 -G verumdoc verumdoc

USER 1000:1000
EXPOSE 6432
ENTRYPOINT ["pgbouncer"]
CMD ["/etc/pgbouncer/pgbouncer.ini"]
