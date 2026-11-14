// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R13b sulla console (backend finto): «Canali PEC», rotazione del segreto dei webhook,
 * voce personale della campanella.
 *  - conservatore: registrazione di un canale senza segreti, stato «mai visto dal
 *    lavoratore», prova d'invio (indirizzo segnaposto rifiutato), messaggi con lo stato
 *    fino alla consegna e le ricevute, messaggi della casella non associati;
 *  - amministratore dell'ente: solo i messaggi (nessun registro dei canali);
 *  - webhook: «Ruota il segreto» mostra il nuovo segreto una volta e la doppia firma;
 *  - campanella: «Il tuo account» separato dagli avvisi dell'ente;
 *  - controllo visivo a 390 e 1280 px in chiaro e scuro: nessuno scorrimento orizzontale,
 *    contrasto AA (axe-core). Le schermate si salvano SOLO se R13B_FOTO indica una
 *    cartella fuori dal repository (regola R06: nessuna immagine nel repository).
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installaFinto } from "./finto";

const CANALE = { codice: "pec-conservatore", tipo: "SMTP_IMAP", nome: "Casella PEC del conservatore", attivo: true, priorita: 1,
  configurazione: { indirizzo: "conservazione@pec.verumdoc.it", smtp_host: "smtps.pec.gestore-prova.it", imap_host: "imaps.pec.gestore-prova.it" },
  versione: 2, aggiornato_il: "2026-10-23T09:00:00Z",
  stato: { segreti_presenti: { password: true, utente: true }, problemi: [], ultimo_invio_il: "2026-10-23T08:07:00Z",
    ultima_lettura_il: "2026-10-23T09:40:00Z", ultimo_errore: null, aggiornato_il: "2026-10-23T09:40:00Z" } };

async function pronta(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

test("canali PEC: registrazione senza segreti, prova, messaggi e ricevute", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await pronta(page, "/canali/pec");
  await expect(page.getByRole("heading", { name: "Canali PEC", level: 1 })).toBeVisible();
  await expect(page.getByText("Nessun canale PEC registrato")).toBeVisible();
  await page.getByRole("button", { name: "Registra un canale" }).click();
  const dialogo = page.getByRole("dialog", { name: "Nuovo canale PEC" });
  await dialogo.getByLabel("Codice").fill("pec-conservatore");
  await dialogo.getByLabel("Nome").fill("Casella PEC del conservatore");
  await dialogo.getByLabel("Casella PEC del conservatore").fill("conservazione@pec.verumdoc.it");
  await dialogo.getByLabel("Server di invio (SMTP)").fill("smtps.pec.gestore-prova.it");
  await dialogo.getByLabel("Server delle ricevute (IMAP)").fill("imaps.pec.gestore-prova.it");
  await expect(dialogo.getByText("PEC_SEGRETI_JSON", { exact: false })).toBeVisible();
  await expect(dialogo.getByLabel("Tipo").locator("option", { hasText: "Simulatore" })).toHaveCount(0);
  await dialogo.getByLabel("Attivo").check();
  await dialogo.getByRole("button", { name: "Registra il canale" }).click();
  await expect(dialogo).toHaveCount(0);
  const put = stato.richieste.find(r => r.metodo === "PUT" && r.percorso.includes("/notifiche/pec/canali/pec-conservatore"))!
    .corpo as { tipo: string; configurazione: Record<string, unknown> };
  expect(put.tipo).toBe("SMTP_IMAP");
  expect(Object.keys(put.configurazione).some(k => /secret|pin|password|otp/.test(k))).toBe(false);
  await expect(page.getByText("Mai visto dal lavoratore")).toBeVisible();

  await page.getByRole("button", { name: "Prova d'invio" }).click();
  await page.getByLabel("Indirizzo PEC di prova").fill("prova@example.org");
  await page.getByRole("button", { name: "Invia la prova" }).click();
  await expect(page.getByText("Indirizzo di prova non valido", { exact: false })).toBeVisible();
  await page.getByLabel("Indirizzo PEC di prova").fill("prova@pec.ente-prova.it");
  await page.getByRole("button", { name: "Invia la prova" }).click();
  await expect(page.getByText("Prova accodata", { exact: false })).toBeVisible();

  const messaggi = page.getByRole("table", { name: "Messaggi PEC, dal più recente" });
  await expect(messaggi.getByText("Consegnato", { exact: true })).toBeVisible();
  await expect(messaggi.getByText("consegna non confermata dopo 24 ore")).toBeVisible();
  await expect(messaggi.getByText("Mancata consegna", { exact: true })).toBeVisible();
  await messaggi.getByRole("button", { name: "Ricevute" }).first().click();
  const ricevute = page.getByRole("dialog", { name: "Messaggio PEC" });
  await expect(ricevute.getByText("Avvenuta consegna")).toBeVisible();
  await expect(ricevute.getByText("Associata").first()).toBeVisible();
  await ricevute.getByRole("button", { name: "Chiudi" }).click();
  await expect(page.getByRole("table", { name: "Messaggi della casella non associati, dal più recente" })
    .getByText("Non autentica")).toBeVisible();
});

test("l'amministratore dell'ente vede solo i messaggi PEC del proprio ente", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await pronta(page, "/canali/pec");
  await expect(page.getByRole("heading", { name: "Canali PEC", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Canali", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Ricevute non associate" })).toHaveCount(0);
  await expect(page.getByRole("table", { name: "Messaggi PEC, dal più recente" })).toBeVisible();
});

test("webhook: rotazione del segreto con doppia firma e ritiro del precedente", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await pronta(page, "/canali/webhook");
  await page.getByRole("button", { name: "Ruota il segreto" }).click();
  await page.getByLabel("Il precedente vale ancora per").selectOption("72");
  await page.getByRole("button", { name: "Ruota il segreto" }).click();
  await expect(page.getByText("Segreto ruotato: copia ora quello nuovo")).toBeVisible();
  await expect(page.getByText("whsec_nuovo-segreto-di-prova-0123456789-N3wZ")).toBeVisible();
  const corpo = stato.richieste.find(r => r.percorso.endsWith("/segreto/ruota"))!.corpo as { transizione_ore: number };
  expect(corpo.transizione_ore).toBe(72);
  await expect(page.getByText("doppia firma: il precedente …x9Qa", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Ritira il precedente" }).click();
  await expect(page.getByText("doppia firma", { exact: false })).toHaveCount(0);
});

test("campanella: «Il tuo account» separato dagli avvisi dell'ente", async ({ page }) => {
  const stato = await installaFinto(page, "OPERATORE");
  stato.campanella = { da_fare: [], avvisi: [], non_letti: 1, intervallo_aggiornamento_secondi: 60,
    personali: [{ id: 5, categoria: "PERSONALI", categoria_nome: "Il tuo account", titolo: "Le tue credenziali: è stata cambiata la password",
      collegamento: "/preferenze/credenziali", quando: "2026-10-23T09:30:00Z", letto: false, da_fare: false }] };
  await pronta(page, "/versamento");
  await page.getByRole("button", { name: /Da fare/ }).click();
  await expect(page.getByRole("heading", { name: "Il tuo account" })).toBeVisible();
  await page.getByRole("link", { name: /cambiata la password/ }).click();
  const letti = stato.richieste.find(r => r.percorso.endsWith("/campanella/letti"))!.corpo as { personali: number[] };
  expect(letti.personali).toEqual([5]);
});

for (const larghezza of [390, 1280]) {
  for (const tema of ["light", "dark"] as const) {
    test(`canali PEC a ${larghezza} px in tema ${tema === "light" ? "chiaro" : "scuro"}: niente scorrimento, contrasto AA`, async ({ page }) => {
      const stato = await installaFinto(page, "RSC_CONSERVATORE");
      stato.canaliPec = [CANALE];
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await page.setViewportSize({ width: larghezza, height: larghezza < 600 ? 844 : 900 });
      await pronta(page, "/canali/pec");
      await expect(page.getByText("Casella PEC del conservatore").first()).toBeVisible();
      const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(sw, "nessuno scorrimento orizzontale").toBeLessThanOrEqual(cw + 1);
      const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
      expect(r.violations.map(v => v.nodes.map(n => n.target.join(" ")).join(", "))).toEqual([]);
      const cartella = process.env.R13B_FOTO;
      if (cartella && !cartella.includes("/work/")) {
        await page.screenshot({ path: `${cartella}/pec_${larghezza}_${tema}.png`, fullPage: true });
        await page.getByRole("button", { name: "Ricevute" }).first().click();
        await page.getByRole("dialog", { name: "Messaggio PEC" }).getByText("Avvenuta consegna").waitFor();
        await page.screenshot({ path: `${cartella}/pec_${larghezza}_${tema}_ricevute.png` });
      }
    });
  }
}

test("webhook a 390 px: la riga con la rotazione non scorre in orizzontale", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  stato.webhookRuotato = "2026-10-26T09:41:00Z";
  await page.setViewportSize({ width: 390, height: 844 });
  await pronta(page, "/canali/webhook");
  await expect(page.getByText("doppia firma", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Ruota il segreto" }).click();
  const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  expect(sw).toBeLessThanOrEqual(cw + 1);
  const cartella = process.env.R13B_FOTO;
  if (cartella && !cartella.includes("/work/")) await page.screenshot({ path: `${cartella}/webhook_390.png`, fullPage: true });
});
