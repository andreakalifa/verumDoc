# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# VerumDoc — frontend (VD-C-038)
# Stage 1: build di frontend/dist con Vite. Stage 2: nginx non privilegiato che
# serve i file statici con fallback SPA (vedi nginx.conf).
# Costruzione:  docker build -t verumdoc-frontend frontend/
FROM node:20.18-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
# Solo i sorgenti: mai node_modules dell'host (binari nativi di un'altra piattaforma)
COPY index.html tsconfig.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31-alpine AS runtime
# Configurazione completa (pid e file temporanei in /tmp: compatibile con
# readOnlyRootFilesystem in Kubernetes, con /tmp montato come emptyDir)
COPY nginx.conf /etc/nginx/nginx.conf
RUN rm -f /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
