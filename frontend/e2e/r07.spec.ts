// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R07 sulle console (backend finto):
 *  - D29: ente in testata per i ruoli globali, con «Tutti gli enti», ricordato
 *    nelle preferenze personali (PUT /api/v1/onboarding/preferenze);
 *  - D31: preferenze salvate sul server: densità, pagina iniziale, colonne visibili;
 *  - D28: una bozza di impostazioni con decorrenza futura diventa «programmata»;
 *  - D25: natura PA senza motivo (si registra il codice IPA);
 *  - D18: «Rigenera se scaduto» nelle richieste di esibizione e copia;
 *  - richiesta del committente: «Rielabora» di un versamento rifiutato dall'ente.
 */
import { expect, test } from "@playwright/test";
import { ENTE, installaFinto } from "./finto";

const putPreferenze = (stato: Awaited<ReturnType<typeof installaFinto>>) =>
  stato.richieste.filter(r => r.metodo === "PUT" && r.percorso === "/v1/onboarding/preferenze");

test("ente in testata con «Tutti gli enti», ricordato sul server", async ({ page }) => {
  const stato = await installaFinto(page, "SUPER_ADMIN");
  stato.preferenze = { ente: "tutti" };
  stato.versionePreferenze = 3;
  await page.goto("/versamenti");
  const scelta = page.getByLabel("Ente su cui lavori");
  await expect(scelta).toHaveValue("tutti");
  await expect(page.getByText("Scegli un ente per vedere i suoi versamenti.")).toBeVisible();
  await scelta.selectOption(ENTE);
  await expect.poll(() => putPreferenze(stato).length).toBe(1);
  expect(putPreferenze(stato)[0].corpo).toEqual({ versione: 3, preferenze: { ente: ENTE } });
  // revisione R17 (R17-08): un solo selettore; la pagina segue la testata
  await expect(page.getByText("Scegli un ente per vedere i suoi versamenti.")).toBeHidden();
});

test("i ruoli dell'ente non scelgono l'ente in testata", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/overview");
  await page.getByRole("button", { name: /menu della persona/ }).click();
  await expect(page.getByRole("link", { name: "Preferenze" })).toBeVisible();
  await expect(page.getByLabel("Ente su cui lavori")).toHaveCount(0);
});

test("preferenze personali: densità, pagina iniziale e colonne", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  stato.preferenze = { pagina_iniziale: "/configurazione" };
  stato.versionePreferenze = 1;
  await page.goto("/");
  await expect(page).toHaveURL(/\/configurazione$/);   // pagina iniziale dal server
  // Il menu si chiude a ogni cambio di pagina: si riapre se la pagina iniziale arriva dopo il clic
  await expect(async () => {
    const menu = page.getByRole("button", { name: /menu della persona/ });
    if (await menu.getAttribute("aria-expanded") !== "true") await menu.click();
    await page.getByRole("link", { name: "Preferenze" }).click({ timeout: 1_000 });
    await expect(page).toHaveURL(/\/preferenze$/, { timeout: 1_000 });
  }).toPass();
  await page.getByLabel("Compatta").check();
  await expect(page.locator("html")).toHaveAttribute("data-densita", "compatta");
  await page.getByLabel("Pagina iniziale").selectOption("/versamenti");
  await expect.poll(() => stato.versionePreferenze).toBe(3);
  expect(stato.preferenze).toEqual({ pagina_iniziale: "/versamenti", densita: "compatta" });
  // colonne visibili della tabella degli arrivi, salvate con le altre preferenze
  await page.goto("/versamenti");
  await page.getByRole("checkbox", { name: "Esito" }).uncheck();
  await expect(page.getByRole("columnheader", { name: "Esito" })).toHaveCount(0);
  await expect.poll(() => (stato.preferenze.colonne as Record<string, string[]> | undefined)?.versamenti)
    .toEqual(["ricevuto", "pacchetto"]);
});

test("bozza di impostazioni con decorrenza futura: programmata", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione/bozze/b0220000-1111-4222-8333-444455556666");
  await page.getByRole("button", { name: "Calcola l'anteprima" }).click();
  await page.getByLabel("Valida dal (facoltativo)").fill("2026-12-01");
  await expect(page.getByText(/il giorno prima l'anteprima si ricalcola/)).toBeVisible();
  await page.getByRole("button", { name: /Conferma e programma 1 modifica/ }).click();   // 409: piano nuovo
  await page.getByRole("button", { name: /Conferma e programma 2 modifiche/ }).click();
  await expect(page.getByText("Si applicherà il 01/12/2026. Fino ad allora nulla cambia e la puoi annullare.")).toBeVisible();
  const applica = stato.richieste.filter(r => r.percorso.endsWith("/applica")).at(-1);
  expect(applica?.corpo).toMatchObject({ valido_dal: "2026-12-01" });
  await expect(page.getByRole("button", { name: "Annulla la modifica programmata" })).toBeVisible();
});

test("natura PA senza motivo: si registra il codice IPA", async ({ page }) => {
  const stato = await installaFinto(page, "SUPER_ADMIN");
  await page.goto("/tenants");
  await page.getByRole("button", { name: /Utenti e classi di Comune di Borgo Immaginario/ }).click();
  const natura = page.getByRole("region", { name: "Natura dell'ente" });
  await natura.getByLabel("Pubblica amministrazione").check();
  await expect(natura.getByText(/Codice IPA dichiarato c_b999/)).toBeVisible();
  await natura.getByRole("button", { name: "Dichiara la natura" }).click();
  await expect(natura.getByText("Natura dichiarata.")).toBeVisible();
  const put = stato.richieste.find(r => r.metodo === "PUT" && r.percorso.endsWith("/natura"));
  expect(put?.corpo).toEqual({ natura: "PA" });
});

test("«Rigenera se scaduto» nelle richieste di esibizione e copia", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/distribution");
  await page.getByRole("button", { name: "Rigenera se scaduto" }).click();
  await expect(page.getByText("Rigenerazione avviata: sarà pronto a breve.")).toBeVisible();
  expect(stato.richieste.some(r => r.metodo === "POST" && r.percorso.endsWith("/rigenera"))).toBe(true);
});

test("«Rielabora» di un proprio versamento rifiutato, dall'ente", async ({ page }) => {
  const stato = await installaFinto(page, "OPERATORE");
  stato.versamentoRifiutato = true;
  await page.goto("/versamenti?stato=ERRORE");
  await page.getByRole("button", { name: "Rielabora" }).click();
  const conferma = page.getByRole("button", { name: "Conferma la rielaborazione" });
  await expect(conferma).toBeDisabled();
  await page.getByLabel(/Motivo della rielaborazione/).fill("Servizio di nuovo disponibile");
  await conferma.click();
  await expect(page.getByText("Versamento di nuovo in lavorazione.")).toBeVisible();
  const applica = stato.richieste.find(r => r.percorso.endsWith("/rielabora/applica"));
  expect(applica?.corpo).toEqual({ impronta_piano: "a".repeat(64), motivo: "Servizio di nuovo disponibile" });
});
