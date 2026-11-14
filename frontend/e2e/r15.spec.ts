// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R15 sulla console (backend finto): «Documentazione del servizio» (VD-C-177).
 *  - il conservatore vede lo stato vuoto, genera, segue la richiesta fino a COMPLETATA e
 *    trova le quattro versioni, i dati da compilare con chi li fornisce, lo storico e lo
 *    scaricamento Markdown;
 *  - l'amministratore dell'ente non ha la voce di menu né la pagina;
 *  - controllo visivo a 390 e 1280 px in chiaro e scuro: testata non coperta né tagliata,
 *    nessuno scorrimento orizzontale, contrasto AA (axe-core). Le schermate si salvano SOLO
 *    se R15_FOTO indica una cartella fuori dal repository (nessuna immagine nel repository).
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installaFinto } from "./finto";

const URL = "/documentazione-servizio";

async function pronta(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

async function generaEAttendi(page: Page) {
  // revisione R17 (R17-31): il comando è anche nello stato vuoto
  await page.getByRole("button", { name: "Genera la documentazione" }).first().click();
  await expect(page.getByText("Generazione completata")).toBeVisible({ timeout: 10_000 });
}

test("documentazione: il conservatore genera, vede i dati da compilare e scarica", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await pronta(page, URL);
  await expect(page.getByRole("heading", { level: 1, name: "Documentazione del servizio" })).toBeVisible();
  await expect(page.getByText("Nessuna versione generata")).toBeVisible();
  await generaEAttendi(page);
  expect(stato.richieste.filter(r => r.metodo === "POST" && r.percorso.endsWith("/generazioni"))).toHaveLength(1);
  await expect(page.getByText("Versioni nuove: Manuale di conservazione 1")).toBeVisible();
  const documenti = page.getByRole("table", { name: "Ultima versione di ogni documento" });
  await expect(documenti.getByRole("row")).toHaveCount(5);
  await expect(documenti.getByText("3 dati").first()).toBeVisible();
  await expect(documenti.getByText("in attesa")).toBeVisible();          // piano non ancora nel WORM
  const compilare = page.getByRole("list", { name: "A.8 Allegato 8 — Continuità operativa (RPO e RTO)" });
  await expect(compilare.getByText("RPO misurato (dati persi al più)")).toBeVisible();
  await expect(compilare.getByText("Fornisce: il responsabile della sicurezza dei sistemi", { exact: false })).toBeVisible();
  await expect(page.getByText("da verificare sul testo ufficiale", { exact: false })).toBeVisible();
  // la guida non ha dati da compilare
  await documenti.getByRole("button", { name: "Guida alle funzionalità", exact: true }).click();
  await expect(page.getByText("Nessun dato da compilare")).toBeVisible();
  await expect(page.getByRole("table", { name: "Versioni di Guida alle funzionalità" })).toBeVisible();
  const scaricamento = page.waitForEvent("download");
  await page.getByRole("button", { name: "Scarica il Markdown: Guida alle funzionalità, versione 1" }).first().click();
  expect((await scaricamento).suggestedFilename()).toBe("manuale_conservazione_v1.md");
  expect(stato.richieste.some(r => r.percorso.endsWith("/versioni/ver-r15-2/markdown"))).toBe(true);
});

test("documentazione: l'amministratore dell'ente non ha la pagina", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await pronta(page, URL);
  await expect(page.getByRole("heading", { level: 1, name: "Documentazione del servizio" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Documentazione del servizio" })).toHaveCount(0);
  expect(stato.richieste.some(r => r.percorso.startsWith("/dashboard/documentazione-servizio"))).toBe(false);
});

for (const larghezza of [390, 1280]) {
  for (const tema of ["light", "dark"] as const) {
    test(`documentazione a ${larghezza} px in tema ${tema === "light" ? "chiaro" : "scuro"}: testata, niente scorrimento, contrasto AA`, async ({ page }) => {
      await installaFinto(page, "RSC_CONSERVATORE");
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await page.setViewportSize({ width: larghezza, height: larghezza < 600 ? 844 : 900 });
      await pronta(page, URL);
      const cartella = process.env.R15_FOTO;
      const salva = async (nome: string) => {
        if (cartella && !/\/work(-r\d+)?\//.test(cartella)) {
          await page.screenshot({ path: `${cartella}/r15_${nome}_${larghezza}_${tema}.png`, fullPage: true });
        }
      };
      await expect(page.getByText("Nessuna versione generata")).toBeVisible();
      await salva("vuoto");
      await generaEAttendi(page);
      await expect(page.getByRole("heading", { name: /^Dati da compilare/ })).toBeVisible();
      const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(sw, "nessuno scorrimento orizzontale").toBeLessThanOrEqual(cw + 1);
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
      const bottone = (await page.getByRole("button", { name: "Genera la documentazione" }).boundingBox())!;
      expect(bottone.x + bottone.width).toBeLessThanOrEqual(larghezza + 1);
      const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
      expect(r.violations.map(v => v.nodes.map(n => n.target.join(" ")).join(", "))).toEqual([]);
      await salva("generata");
    });
  }
}
