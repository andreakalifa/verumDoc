// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R06 sulle console (backend finto):
 *  - VD-C-128: «In attesa di te» con UNA sola richiesta (`in_attesa_di=me`);
 *  - VD-C-129: allarmi del registro e del testimone dall'API, anche quelli gestiti;
 *  - VD-C-139: «Classi e accordi» elenca gli accordi (GET /api/v1/configurazione/accordi);
 *  - VD-C-138: natura dell'ente (PA/PRIVATO con motivo) nella console degli enti.
 */
import { expect, test } from "@playwright/test";
import { installaFinto } from "./finto";

test("«In attesa di te» fa una sola richiesta", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await page.goto("/pratiche");
  await expect(page.getByRole("heading", { name: "In attesa di te" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Pratiche in attesa di una tua decisione" })
    .or(page.getByText("Nessuna pratica aspetta una tua decisione"))).toBeVisible();
  const attesa = stato.richieste.filter(r => r.percorso.startsWith("/v1/ciclo-vita/pratiche?") && r.percorso.includes("in_attesa_di=me"));
  expect(attesa).toHaveLength(1);
  // nessuna richiesta per singolo stato oltre all'elenco completo
  const perStato = stato.richieste.filter(r => r.percorso.startsWith("/v1/ciclo-vita/pratiche?") && r.percorso.includes("stato="));
  expect(perStato).toHaveLength(0);
});

test("allarmi del registro e del testimone", async ({ page }) => {
  await installaFinto(page, "SUPER_ADMIN");
  await page.goto("/registro");
  const sezione = page.getByRole("region", { name: "Allarmi del registro e del testimone" });
  await expect(sezione.getByText("FIRMA_NON_VALIDA")).toBeVisible();
  await expect(sezione.getByText("COERENZA_NON_VERIFICABILE")).toHaveCount(0);
  await sezione.getByLabel("anche quelli già gestiti").check();
  await expect(sezione.getByText("COERENZA_NON_VERIFICABILE")).toBeVisible();
  await expect(sezione.getByText(/gestito da operatore-turno/)).toBeVisible();
  await expect(page.getByText("Che cosa manca")).toHaveCount(0);
});

test("«Classi e accordi» elenca gli accordi di versamento", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione/classi-accordi");
  const tabella = page.getByRole("table", { name: "Accordi di versamento dell'ente, ultima versione" });
  await expect(tabella.getByText("PROTOCOLLO-WS")).toBeVisible();
  await expect(tabella.getByRole("row", { name: /FATTURE-SDI/ }).getByText("Fatture passive")).toBeVisible();
  await expect(tabella.getByRole("link", { name: "Regole dell'accordo" }).first())
    .toHaveAttribute("href", /livello=ACCORDO&accordo=PROTOCOLLO-WS/);
  await expect(page.getByText("non ancora offerto dal servizio")).toHaveCount(0);
});

test("natura dell'ente: PRIVATO con il motivo", async ({ page }) => {
  const stato = await installaFinto(page, "SUPER_ADMIN");
  await page.goto("/tenants");
  await page.getByRole("button", { name: /Utenti e classi di Comune di Borgo Immaginario/ }).click();
  const natura = page.getByRole("region", { name: "Natura dell'ente" });
  await expect(natura.getByText(/Non dichiarata/)).toBeVisible();
  await natura.getByLabel("Soggetto privato").check();
  await natura.getByRole("button", { name: "Dichiara la natura" }).click();
  // senza motivo non parte
  expect(stato.richieste.filter(r => r.metodo === "PUT")).toHaveLength(0);
  await natura.getByLabel(/^Motivo/).fill("Visura camerale del 2026: società di diritto privato");
  await natura.getByRole("button", { name: "Dichiara la natura" }).click();
  await expect(natura.getByText("Natura dichiarata.")).toBeVisible();
  const put = stato.richieste.find(r => r.metodo === "PUT" && r.percorso.endsWith("/natura"));
  expect(put?.corpo).toEqual({ natura: "PRIVATO", motivo: "Visura camerale del 2026: società di diritto privato" });
  await expect(natura.getByText(/Soggetto privato: lo scarto non richiede il nulla osta/)).toBeVisible();
});
