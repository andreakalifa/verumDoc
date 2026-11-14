// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R13a sulla console (backend finto): «Fornitori di firma e marca».
 *  - solo il conservatore: parametri del gruppo firma_qualificata, stato con scadenza
 *    del certificato e prova con firma di prova verificata;
 *  - registrazione di un fornitore TSA senza segreti (i segreti stanno nel Secret);
 *  - un amministratore dell'ente non vede la pagina.
 */
import { expect, test } from "@playwright/test";
import { installaFinto } from "./finto";

test("stato, prova e registrazione dei fornitori di firma e marca", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await page.goto("/configurazione/firma-qualificata");
  await expect(page.getByRole("heading", { name: "Firma e marca qualificate" })).toBeVisible();
  await expect(page.getByText("FIRMA_SENZA_FORNITORE", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("45 giorni")).toBeVisible();
  await page.getByRole("button", { name: "Prova", exact: true }).click();
  await expect(page.getByText("Riuscita in 812 ms, livello B-T, verificata.")).toBeVisible();

  const nuovo = page.locator("form").filter({ hasText: "Nuovo fornitore" });
  await nuovo.getByLabel("Codice").fill("tsa-principale");
  await nuovo.getByLabel("Ruolo").selectOption("MARCA");
  await nuovo.getByLabel("Nome").fill("TSA qualificata");
  await nuovo.getByLabel("Indirizzo della TSA").fill("https://tsa.prova.example");
  await expect(nuovo.getByText("password (facoltativo)", { exact: false })).toBeVisible();
  await nuovo.getByRole("button", { name: "Registra il fornitore" }).click();
  await expect(nuovo.getByText("Salvato.", { exact: false })).toBeVisible();
  const put = stato.richieste.find(r => r.metodo === "PUT" && r.percorso.includes("/firma/fornitori/tsa-principale"))!
    .corpo as { ruolo: string; tipo: string; configurazione: Record<string, unknown> };
  expect(put.ruolo).toBe("MARCA");
  expect(put.tipo).toBe("RFC3161");
  expect(put.configurazione.url).toBe("https://tsa.prova.example");
  expect(Object.keys(put.configurazione).some(k => /secret|pin|password|otp/.test(k))).toBe(false);
  // il tipo di sviluppo non si offre dove non è ammesso
  await expect(nuovo.getByLabel("Tipo").locator("option", { hasText: "SVILUPPO" })).toHaveCount(0);
});

test("l'amministratore dell'ente non vede i fornitori di firma", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione/firma-qualificata");
  await expect(page.getByRole("heading", { name: "Firma e marca qualificate" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Fornitori di firma e marca" })).toHaveCount(0);
});
