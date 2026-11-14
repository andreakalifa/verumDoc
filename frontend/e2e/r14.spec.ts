// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R14 sulla console (backend finto): pratica SOSPESA dopo i tentativi esauriti
 * (VD-C-133, revisione R14 (4)).
 *  - la pagina non dice più «il servizio riprova da solo»: dice che la pratica è ferma;
 *  - il conservatore la riprende con il motivo (almeno 20 caratteri) e la versione letta;
 *  - l'amministratore dell'ente la vede ferma ma non ha il comando;
 *  - controllo visivo a 390 e 1280 px in chiaro e scuro: testata non coperta né tagliata,
 *    nessuno scorrimento orizzontale, contrasto AA (axe-core). Le schermate si salvano SOLO
 *    se R13B_FOTO indica una cartella fuori dal repository (nessuna immagine nel repository).
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installaFinto, R14_SOSPESA } from "./finto";

const URL = `/pratiche/${R14_SOSPESA}`;

async function pronta(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

test("pratica sospesa: il conservatore la riprende con il motivo", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await pronta(page, URL);
  await expect(page.getByRole("heading", { name: "Pratica ferma" })).toBeVisible();
  await expect(page.getByText("si è fermato: non riprova più da solo")).toBeVisible();
  await expect(page.getByText("Il servizio riprova da solo")).toHaveCount(0);
  await page.getByRole("button", { name: "Riprendi il lavoro" }).click();
  expect(stato.riprese ?? []).toEqual([]);           // motivo obbligatorio
  await page.getByLabel("Causa corretta e motivo della ripresa").fill("Registro delle impronte di nuovo raggiungibile");
  await page.getByRole("button", { name: "Riprendi il lavoro" }).click();
  await expect(page.getByRole("heading", { name: "Pratica ferma" })).toHaveCount(0);
  await expect(page.getByText("Esecuzione in corso")).toBeVisible();
  expect(stato.riprese).toEqual([{ versione: 5, motivo: "Registro delle impronte di nuovo raggiungibile" }]);
});

test("pratica sospesa: l'ente la vede ferma senza il comando", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await pronta(page, URL);
  await expect(page.getByRole("heading", { name: "Pratica ferma" })).toBeVisible();
  await expect(page.getByText("La pratica la riprende il conservatore")).toBeVisible();
  await expect(page.getByRole("button", { name: "Riprendi il lavoro" })).toHaveCount(0);
});

for (const larghezza of [390, 1280]) {
  for (const tema of ["light", "dark"] as const) {
    test(`pratica sospesa a ${larghezza} px in tema ${tema === "light" ? "chiaro" : "scuro"}: testata, niente scorrimento, contrasto AA`, async ({ page }) => {
      await installaFinto(page, "RSC_CONSERVATORE");
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await page.setViewportSize({ width: larghezza, height: larghezza < 600 ? 844 : 900 });
      await pronta(page, URL);
      await expect(page.getByRole("button", { name: "Riprendi il lavoro" })).toBeVisible();
      const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(sw, "nessuno scorrimento orizzontale").toBeLessThanOrEqual(cw + 1);
      // testata: il titolo è tutto dentro la finestra e nessun elemento lo copre
      const titolo = page.getByRole("heading", { level: 1 }).first();
      const box = (await titolo.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(larghezza + 1);
      const sopra = await titolo.evaluate(el => {
        const r = el.getBoundingClientRect();
        const x = r.left + Math.min(20, r.width / 2), y = r.top + r.height / 2;
        const top = document.elementFromPoint(x, y);
        return !!top && (el === top || el.contains(top));
      });
      expect(sopra, "titolo non coperto").toBe(true);
      const bottone = (await page.getByRole("button", { name: "Riprendi il lavoro" }).boundingBox())!;
      expect(bottone.x + bottone.width).toBeLessThanOrEqual(larghezza + 1);
      const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
      expect(r.violations.map(v => v.nodes.map(n => n.target.join(" ")).join(", "))).toEqual([]);
      const cartella = process.env.R13B_FOTO;
      if (cartella && !cartella.includes("/work/")) {
        await page.screenshot({ path: `${cartella}/r14_sospesa_${larghezza}_${tema}.png`, fullPage: true });
      }
    });
  }
}
