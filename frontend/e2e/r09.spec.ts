// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R09 sulle console (backend finto): ciclo di vita automatico.
 *  - Configurazione «Ciclo di vita automatico»: gruppi scarto, annullamento, blocchi
 *    WORM, verifiche, audit; stato del lavoro automatico per il conservatore;
 *  - D20: «Chiudi con eccezioni» chiede un motivo di almeno 20 caratteri;
 *  - D23: seconda approvazione dell'annullamento (chi può, approva o respinge).
 */
import { expect, test } from "@playwright/test";
import { installaFinto } from "./finto";

test("ciclo di vita automatico in configurazione", async ({ page }) => {
  await installaFinto(page, "SUPER_ADMIN");
  await page.goto("/configurazione/ciclo-vita");
  await expect(page.getByRole("heading", { name: "Ciclo di vita automatico: scarto" })).toBeVisible();
  await expect(page.getByText("SCARTO_PROPOSTA_AUTOMATICA", { exact: false }).first()).toBeVisible();
  await page.getByRole("tab", { name: "Blocchi WORM" }).click();
  await expect(page.getByText("RETENTION_PERMANENTE_ANNI", { exact: false }).first()).toBeVisible();
  const lavoro = page.getByRole("region", { name: "Lavoro automatico" });
  await expect(lavoro.getByText("si rinnovano da soli", { exact: false })).toBeVisible();
  await expect(lavoro.getByText("Rinnovo dei blocchi WORM")).toBeVisible();
});

test("chiudi con eccezioni: motivo di almeno 20 caratteri", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await page.goto("/pratiche/51a7e0aa-1111-4aaa-8aaa-0000000000c1");
  await expect(page.getByText("Scarto concluso con eccezioni", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Chiudi con eccezioni…" }).click();
  await page.getByLabel("Motivo").fill("Blocco legale");
  await page.getByRole("button", { name: "Conferma: chiudi con eccezioni" }).click();
  await expect(page.getByText("Scrivi un motivo di almeno 20 caratteri.")).toBeVisible();
  expect(stato.transizioni).toHaveLength(0);
  await page.getByLabel("Motivo").fill("Blocco legale definitivo disposto dal tribunale");
  await page.getByRole("button", { name: "Conferma: chiudi con eccezioni" }).click();
  await expect.poll(() => stato.transizioni.length).toBe(1);
  expect(stato.transizioni[0].nuovo_stato).toBe("CHIUSA_CON_ECCEZIONI");
});

test("annullamento: seconda approvazione", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/pratiche/51a7e0aa-1111-4aaa-8aaa-0000000000c2");
  await expect(page.getByText("deve approvarlo un'altra persona", { exact: false })).toBeVisible();
  const sezione = page.getByRole("region", { name: "Seconda approvazione" });
  await sezione.getByLabel("Motivo").fill("Verificato con l'ufficio protocollo");
  await sezione.getByRole("button", { name: "Approva l'annullamento" }).click();
  await expect.poll(() => stato.approvazioni.length).toBe(1);
  expect(stato.approvazioni[0].esito).toBe("APPROVA");
});
