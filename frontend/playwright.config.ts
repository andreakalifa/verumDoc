// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Prove end-to-end delle console R05 (Playwright, Chromium di /opt/pw-browsers).
 * Il backend è finto (e2e/finto.ts, route mocking): nessun servizio da avviare.
 * `npm run e2e` avvia il server di sviluppo di Vite su una porta dedicata.
 * `FOTO=1 npm run e2e -- foto` rigenera le foto in docs/design/foto/.
 */
import { defineConfig, devices } from "@playwright/test";

const PORTA = 5179;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORTA}`,
    locale: "it-IT",
    timezoneId: "Europe/Rome",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npx vite --port ${PORTA} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORTA}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
