// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R06 (VD-C-127): scadenza della sessione del portale per inattività.
 *  1. due minuti prima del limite comunicato dal gateway (R07, D14) la pagina avvisa;
 *     «Resta collegato» rinnova la sessione (GET /api/v1/auth/sessione) e chiude l'avviso;
 *  2. un 401 AUTH-004 da qualunque servizio riporta all'accesso con la spiegazione;
 *  3. gli aggiornamenti automatici senza azioni dell'utente non rinnovano la sessione.
 */
import { expect, test } from "@playwright/test";
import { installaFinto } from "./finto";

test("avviso prima della scadenza e «Resta collegato»", async ({ page }) => {
  const stato = await installaFinto(page, "SUPER_ADMIN");
  stato.inattivitaSecondi = 63;
  await page.goto("/configurazione");
  const avviso = page.getByRole("alertdialog", { name: "La sessione sta per scadere" });
  await expect(avviso).toBeVisible({ timeout: 10_000 });
  const prima = stato.richieste.filter(r => r.percorso === "/v1/auth/sessione").length;
  await avviso.getByRole("button", { name: "Resta collegato" }).click();
  await expect(avviso).toBeHidden();
  expect(stato.richieste.filter(r => r.percorso === "/v1/auth/sessione").length).toBeGreaterThan(prima);
});

/** Una richiesta delle pagine (client axios del portale, con i suoi intercettori). */
async function richiestaDelPortale(page: import("@playwright/test").Page) {
  await page.evaluate(async () => {
    const modulo = await import("/src/api/client.ts");
    await modulo.api.get("/v1/configurazione/regole").catch(() => null);
  });
}

test("sessione scaduta: ritorno all'accesso con la spiegazione", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione");
  await expect(page.getByRole("heading", { level: 1, name: "Impostazioni e regole" })).toBeVisible();
  stato.sessioneScaduta = true;
  await richiestaDelPortale(page);
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByText("Sessione scaduta per inattività")).toBeVisible();
});

test("gli aggiornamenti senza azioni dell'utente non rinnovano la sessione", async ({ page }) => {
  await page.clock.install();
  await installaFinto(page, "ADMIN_TENANT");
  const intestazioni: (string | null)[] = [];
  page.on("request", r => {
    if (r.url().includes("/api/v1/configurazione/regole")) intestazioni.push(r.headers()["x-sessione-rinnovo"] ?? null);
  });
  await page.goto("/configurazione");
  await expect(page.getByRole("heading", { level: 1, name: "Impostazioni e regole" })).toBeVisible();
  // caricamento della pagina = azione dell'utente: rinnova
  expect(intestazioni.at(-1)).toBeNull();
  // due minuti senza azioni: la richiesta automatica non rinnova
  await page.clock.fastForward("02:00");
  await richiestaDelPortale(page);
  expect(intestazioni.at(-1)).toBe("no");
  // dopo un'azione dell'utente torna a rinnovare
  await page.keyboard.press("Shift");
  await richiestaDelPortale(page);
  expect(intestazioni.at(-1)).toBeNull();
});
