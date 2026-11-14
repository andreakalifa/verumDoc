// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R08 sulle console (backend finto):
 *  - D01: «Verifica delle firme» in Configurazione (livello per livello, regola RGL-109);
 *  - D02: «Catalogo dei formati» con gli esiti per famiglia e il catalogo base (PUID, MIME);
 *  - D01: nel versamento l'esito delle firme in parole (firmatario, valida/indeterminata, motivo).
 */
import { expect, test } from "@playwright/test";
import { installaFinto } from "./finto";

test("verifica delle firme in configurazione", async ({ page }) => {
  await installaFinto(page, "SUPER_ADMIN");
  await page.goto("/configurazione/firme");
  await expect(page.getByRole("heading", { name: "Verifica delle firme" })).toBeVisible();
  await expect(page.getByText("FIRME_LIVELLO_VERIFICA", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("NULLA_OSTA_FIRMA", { exact: false }).first()).toBeVisible();
});

test("catalogo dei formati: esiti e catalogo base", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione/formati");
  await expect(page.getByRole("heading", { name: "Catalogo dei formati" })).toBeVisible();
  await expect(page.getByText("FORMATO.OFFICE_BINARIO.ESITO", { exact: false }).first()).toBeVisible();
  const base = page.getByRole("region", { name: "Catalogo base del conservatore" });
  await expect(base.getByText("PDF/A (ISO 19005)")).toBeVisible();
  await expect(base.getByText("fmt/95, fmt/477")).toBeVisible();
  await expect(base.getByText("Da convertire")).toBeVisible();
});

test("esito delle firme nel versamento, in parole", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/versamenti");
  const bottone = page.getByRole("button", { name: "Firme: 1 valida, 1 indeterminata" });
  await expect(bottone).toBeVisible();
  await bottone.click();
  await expect(page.getByText("Dirigente del settore")).toBeVisible();
  await expect(page.getByText("Funzionario istruttore: il certificato non risale a un prestatore fidato (Trusted List)")).toBeVisible();
  await expect(page.getByText("nota.xml")).toHaveCount(0);   // file non firmati non elencati
});
