// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R16 sulla console (backend finto): «Firme del responsabile» (VD-C-166).
 *  - il responsabile legge il documento, chiede l'OTP e firma; un OTP rifiutato non firma
 *    e il campo si svuota; il documento passa fra i firmati con la busta scaricabile;
 *  - «Prepara il manuale da firmare» accoda il manuale; la richiesta si annulla con il motivo;
 *  - l'amministratore dell'ente non vede la pagina;
 *  - controllo visivo a 390 e 1280 px in chiaro e scuro (R12c): testata non coperta né
 *    tagliata, nessuno scorrimento orizzontale, contrasto AA (axe-core), anche con il dialogo
 *    di firma aperto. Le schermate si salvano SOLO se R13B_FOTO indica una cartella fuori dal
 *    repository (nessuna immagine nel repository).
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installaFinto } from "./finto";

const URL = "/firme-responsabile";

async function pronta(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

test("firma personale con OTP: lettura, OTP rifiutato, firma, busta", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await pronta(page, URL);
  await expect(page.getByRole("heading", { name: "Firme del responsabile", level: 1 })).toBeVisible();
  const daFirmare = page.getByRole("table", { name: "Documenti da firmare, dal più recente" });
  await expect(daFirmare.getByText("Nomina del vicario del responsabile del servizio di conservazione")).toBeVisible();
  await daFirmare.getByRole("button", { name: "Firma" }).click();
  const dialogo = page.getByRole("dialog", { name: "Firma personale" });
  const scarico = page.waitForEvent("download");
  await dialogo.getByRole("button", { name: "Leggi il documento" }).click();
  expect((await scarico).suggestedFilename()).toBe("nomina_vicario_2026.pdf");
  await expect(dialogo.getByRole("button", { name: "Firma il documento" })).toBeDisabled();
  await dialogo.getByRole("button", { name: "Chiedi l'OTP" }).click();
  await expect(dialogo.getByText("OTP richiesto al prestatore", { exact: false })).toBeVisible();
  expect(stato.otpChiesti).toBe(1);
  await dialogo.getByLabel("Codice OTP").fill("111111");
  await dialogo.getByRole("button", { name: "Firma il documento" }).click();
  await expect(dialogo.getByText("Documento non firmato")).toBeVisible();
  await expect(dialogo.getByLabel("Codice OTP")).toHaveValue("");   // l'OTP non resta nel modulo
  await dialogo.getByLabel("Codice OTP").fill("246810");
  await dialogo.getByLabel("PIN").fill("1234");
  await dialogo.getByRole("button", { name: "Firma il documento" }).click();
  await expect(dialogo.getByText("Documento firmato", { exact: true })).toBeVisible();
  const richiesta = stato.richieste.filter(r => r.percorso.endsWith("/fr-nomina/firma")).at(-1)!;
  expect(richiesta.corpo).toEqual({ otp: "246810", pin: "1234" });
  await dialogo.getByRole("button", { name: "Torna all'elenco" }).click();
  await expect(page.getByText("Nessun documento da firmare")).toBeVisible();
  const firmati = page.getByRole("table", { name: "Documenti firmati, annullati o respinti, dal più recente" });
  await expect(firmati.getByText("Firmato, da archiviare")).toBeVisible();
  await expect(firmati.getByText("Firmato e archiviato")).toBeVisible();
  const busta = page.waitForEvent("download");
  await firmati.getByRole("button", { name: "Scarica la busta (.p7m)" }).first().click();
  expect((await busta).suggestedFilename()).toMatch(/\.p7m$/);
});

test("manuale in firma dalla versione stampabile e richiesta annullata con il motivo", async ({ page }) => {
  // revisione R17 (R17-04): niente più «Prepara il manuale» sui dati JSON
  const stato = await installaFinto(page, "SUPER_ADMIN");
  await pronta(page, URL);
  await expect(page.getByRole("button", { name: "Prepara il manuale da firmare" })).toHaveCount(0);
  await page.getByRole("link", { name: "Metti in firma il manuale o il piano" }).click();
  await page.getByRole("button", { name: "Genera la documentazione" }).first().click();
  await expect(page.getByText("Generazione completata")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /Invia alla firma del responsabile: Manuale di conservazione/ }).click();
  await page.getByRole("link", { name: "apri Firme del responsabile" }).click();
  const daFirmare = page.getByRole("table", { name: "Documenti da firmare, dal più recente" });
  await expect(daFirmare.getByText("manuale_conservazione_v1.html")).toBeVisible();
  await daFirmare.getByRole("row", { name: /Manuale di conservazione — versione 1/ }).getByRole("button", { name: "Firma" }).click();
  const dialogo = page.getByRole("dialog", { name: "Firma personale" });
  await dialogo.getByRole("button", { name: "Annulla la richiesta di firma" }).click();
  await dialogo.getByRole("button", { name: "Annulla la richiesta", exact: true }).click();
  await expect(dialogo.getByText("Scrivi un motivo di almeno", { exact: false })).toBeVisible();
  await dialogo.getByLabel("Perché la richiesta si annulla").fill("Versione superata dalla revisione di novembre");
  await dialogo.getByRole("button", { name: "Annulla la richiesta", exact: true }).click();
  await expect(dialogo).toHaveCount(0);
  expect(stato.richieste.some(r => r.percorso.endsWith("/annulla"))).toBe(true);
  await expect(page.getByRole("table", { name: "Documenti firmati, annullati o respinti, dal più recente" })
    .getByText("Annullato", { exact: true })).toBeVisible();
});

test("l'amministratore dell'ente non vede le firme del responsabile", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto(URL);
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Firme del responsabile", level: 1 })).toHaveCount(0);
});

for (const larghezza of [390, 1280]) {
  for (const tema of ["light", "dark"] as const) {
    test(`firme del responsabile a ${larghezza} px in tema ${tema === "light" ? "chiaro" : "scuro"}: testata, niente scorrimento, contrasto AA`, async ({ page }) => {
      await installaFinto(page, "RSC_CONSERVATORE");
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await page.setViewportSize({ width: larghezza, height: larghezza < 600 ? 844 : 900 });
      await pronta(page, URL);
      await expect(page.getByRole("button", { name: "Firma", exact: true })).toBeVisible();
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
      const contrasto = async () => {
        const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
        return r.violations.map(v => v.nodes.map(n => n.target.join(" ")).join(", "));
      };
      expect(await contrasto()).toEqual([]);
      const cartella = process.env.R13B_FOTO;
      const foto = cartella && !cartella.includes("/work/");
      if (foto) await page.screenshot({ path: `${cartella}/r16_firme_${larghezza}_${tema}.png`, fullPage: true });
      await page.getByRole("button", { name: "Firma", exact: true }).click();
      const dialogo = page.getByRole("dialog", { name: "Firma personale" });
      await expect(dialogo.getByRole("button", { name: "Firma il documento" })).toBeVisible();
      const campo = (await dialogo.getByLabel("Codice OTP").boundingBox())!;
      expect(campo.x + campo.width).toBeLessThanOrEqual(larghezza + 1);
      expect(await contrasto()).toEqual([]);
      if (foto) await page.screenshot({ path: `${cartella}/r16_firme_${larghezza}_${tema}_dialogo.png`, fullPage: true });
    });
  }
}
