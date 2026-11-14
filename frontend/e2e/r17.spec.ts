// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R17 sulla console (backend finto): conferma firmata delle decisioni sensibili (VD-P-164).
 *  - richiesta di annullamento: il gateway chiede la conferma dell'identità, il dialogo la
 *    raccoglie, poi la conferma firmata va con la richiesta; l'impronta calcolata dalla console
 *    è quella dei vettori condivisi con il servizio (libs/shared/tests/vettori_conferme.json);
 *  - esecuzione di uno scarto con l'identità già confermata: nessun dialogo, conferma inviata;
 *  - dialogo chiuso senza confermare: nessuna decisione e nessun errore;
 *  - con la conferma spenta (sviluppo) nulla cambia: nessuna chiamata al gateway;
 *  - controllo visivo del dialogo a 390 e 1280 px in chiaro e scuro (R12c): testata non
 *    coperta, nessuno scorrimento orizzontale, contrasto AA (axe-core). Schermate solo se
 *    R13B_FOTO indica una cartella fuori dal repository.
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installaFinto, type Stato } from "./finto";

const PDV = "11111111-1111-4111-8111-111111111111";
const MOTIVO = "Versamento duplicato: è già presente il PdV corretto";
/** Vettore ANNULLAMENTO_RICHIEDI di libs/shared/tests/vettori_conferme.json */
const IMPRONTA_ATTESA = "23f3bbf832a416b7c384f4756282a010c639df0f0119e66e9ca67391bed9bed0";
const ESEGUIBILE = "51a7e0aa-1111-4aaa-8aaa-000000000002";

function attiva(stato: Stato, confermata: boolean) {
  stato.conferme = { attiva: true, richieste: [], decisioni: [] };
  stato.credenziali = { metodi: ["PASSWORD_TOTP"], confermata, password: true, passkey: [], codiciRimasti: 7 };
}

async function annullamento(page: Page) {
  await page.goto("/pratiche/nuova?tipo=annullamento");
  await page.getByLabel(/Identificativo del versamento/).fill(PDV.toUpperCase());
  await page.getByLabel("Motivo").fill(MOTIVO);
  await page.getByRole("button", { name: "Controlla e conferma" }).click();
  await page.getByRole("button", { name: "Conferma e crea la pratica" }).click();
}

test("annullamento: conferma dell'identità, poi la conferma firmata va con la richiesta", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  attiva(stato, false);
  await annullamento(page);
  const dialogo = page.getByRole("dialog", { name: "Conferma la tua identità" });
  await expect(dialogo).toBeVisible();
  await expect(dialogo.getByText("Per chiedere l'annullamento del versamento")).toBeVisible();
  expect(stato.conferme!.decisioni).toHaveLength(0);
  await dialogo.getByLabel("Password").fill("password sbagliata di prova");
  await dialogo.getByLabel("Codice a sei cifre dell'app").fill("123456");
  await dialogo.getByRole("button", { name: "Conferma e decidi" }).click();
  await expect(dialogo.getByText("Password, codice o passkey non corretti.")).toBeVisible();
  await expect(dialogo.getByLabel("Password")).toHaveValue("");
  await dialogo.getByLabel("Password").fill("una frase lunga di prova");
  await dialogo.getByLabel("Codice a sei cifre dell'app").fill("123456");
  await dialogo.getByRole("button", { name: "Conferma e decidi" }).click();
  await expect.poll(() => stato.conferme!.decisioni.length).toBe(1);
  await expect(dialogo).toHaveCount(0);
  const richieste = stato.conferme!.richieste;
  expect(richieste).toHaveLength(2);   // la prima rifiutata (403), la seconda firmata
  expect(richieste[1]).toEqual({ azione: "ANNULLAMENTO_RICHIEDI", pratica: null, versione: 0,
                                 impronta: IMPRONTA_ATTESA, ente: richieste[1].ente });
  const decisione = stato.conferme!.decisioni[0] as { pdv_id: string; conferma?: { payload: string; firma: string } };
  expect(decisione.pdv_id).toBe(PDV);
  expect(decisione.conferma?.firma).toBeTruthy();
  await expect(page).toHaveURL(/\/pratiche\/51a7e0aa-1111-4aaa-8aaa-0000000000d1$/);
});

test("esecuzione dello scarto con l'identità già confermata: nessun dialogo", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  attiva(stato, true);
  await page.goto(`/pratiche/${ESEGUIBILE}`);
  await page.getByRole("button", { name: /Esegui/ }).first().click();
  await page.getByLabel("Motivo").fill("Nulla osta ricevuto, scarto da eseguire oggi");
  await page.getByRole("button", { name: /^Conferma:/ }).click();
  await expect.poll(() => stato.transizioni.length).toBe(1);
  const t = stato.transizioni[0] as { nuovo_stato: string; conferma?: unknown };
  expect(t.nuovo_stato).toBe("ESEGUITA");
  expect(t.conferma).toBeTruthy();
  expect(stato.conferme!.richieste).toHaveLength(1);
  expect(stato.conferme!.richieste[0]).toMatchObject({ azione: "SCARTO_ESEGUI", pratica: ESEGUIBILE, versione: 3 });
  await expect(page.getByRole("dialog", { name: "Conferma la tua identità" })).toHaveCount(0);
});

test("dialogo chiuso senza confermare: nessuna decisione e nessun errore", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  attiva(stato, false);
  await annullamento(page);
  const dialogo = page.getByRole("dialog", { name: "Conferma la tua identità" });
  await dialogo.getByRole("button", { name: "Chiudi" }).click();
  await expect(dialogo).toHaveCount(0);
  expect(stato.conferme!.decisioni).toHaveLength(0);
  await expect(page.getByText("Pratica non creata")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Conferma e crea la pratica" })).toBeEnabled();
});

test("con la conferma spenta nulla cambia", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await annullamento(page);
  await expect.poll(() => stato.conferme?.decisioni.length ?? 0).toBe(1);
  expect(stato.richieste.some(r => r.percorso.startsWith("/v1/auth/conferme-decisione"))).toBe(false);
  expect((stato.conferme!.decisioni[0] as { conferma?: unknown }).conferma).toBeUndefined();
});

for (const larghezza of [390, 1280]) {
  for (const tema of ["light", "dark"] as const) {
    test(`dialogo della conferma a ${larghezza} px in tema ${tema === "light" ? "chiaro" : "scuro"}: niente scorrimento, contrasto AA`, async ({ page }) => {
      const stato = await installaFinto(page, "ADMIN_TENANT");
      attiva(stato, false);
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await page.setViewportSize({ width: larghezza, height: larghezza < 600 ? 844 : 900 });
      await annullamento(page);
      const dialogo = page.getByRole("dialog", { name: "Conferma la tua identità" });
      await expect(dialogo.getByRole("button", { name: "Conferma e decidi" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(sw, "nessuno scorrimento orizzontale").toBeLessThanOrEqual(cw + 1);
      const titolo = dialogo.getByRole("heading", { name: "Conferma la tua identità" });
      const box = (await titolo.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(larghezza + 1);
      const sopra = await titolo.evaluate(el => {
        const r = el.getBoundingClientRect();
        const top = document.elementFromPoint(r.left + Math.min(20, r.width / 2), r.top + r.height / 2);
        return !!top && (el === top || el.contains(top));
      });
      expect(sopra, "titolo del dialogo non coperto").toBe(true);
      for (const campo of ["Password", "Codice a sei cifre dell'app"]) {
        const b = (await dialogo.getByLabel(campo).boundingBox())!;
        expect(b.x + b.width).toBeLessThanOrEqual(larghezza + 1);
      }
      const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
      expect(r.violations.map(v => v.nodes.map(n => n.target.join(" ")).join(", "))).toEqual([]);
      const cartella = process.env.R13B_FOTO;
      if (cartella && !cartella.includes("/work/")) {
        await page.screenshot({ path: `${cartella}/r17_conferma_${larghezza}_${tema}.png`, fullPage: true });
      }
    });
  }
}
