// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // BUG-001 FIX: paths più specifici PRIMA di quelli generici.
      // Vite proxy matcha il primo prefisso — /api/v1/onboarding deve
      // precedere /api/v1 altrimenti tutte le chiamate onboarding
      // vengono inviate al gateway (porta 8000) invece che a onboarding (8007).
      "/api/v1/onboarding": { target: "http://localhost:8007", changeOrigin: true },
      // R05 (console): configurazione (onboarding), ciclo di vita, registro e verifiche
      // (hashchain), webhook (notification): prima del generico /api/v1 del gateway.
      "/api/v1/configurazione": { target: "http://localhost:8007", changeOrigin: true },
      "/api/v1/ciclo-vita":     { target: "http://localhost:8012", changeOrigin: true },
      "/api/v1/hashchain":      { target: "http://localhost:8004", changeOrigin: true },
      "/api/v1/webhook":        { target: "http://localhost:8006", changeOrigin: true },
      "/api/v1/notifiche":      { target: "http://localhost:8006", changeOrigin: true },
      "/api/dashboard": { target: "http://localhost:8009", changeOrigin: true },
      "/api/info":       { target: "http://localhost:8009", changeOrigin: true },
      "/api/health":     { target: "http://localhost:8009", changeOrigin: true },
      "/api/v1":         { target: "http://localhost:8000", changeOrigin: true },
      "/api/v2":         { target: "http://localhost:8008", changeOrigin: true },
      "/api/pdnd":       { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        // VD-C-027: i grafici (recharts e le sue dipendenze d3) in un pezzo a parte,
        // scaricato solo dalle pagine che li usano.
        manualChunks(id) {
          if (/node_modules\/(recharts|d3-|victory-vendor|react-smooth|recharts-scale|decimal\.js-light)/.test(id)) return "grafici";
          if (/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|@tanstack)\//.test(id)) return "base";
          return undefined;
        },
      },
    },
  },
});
