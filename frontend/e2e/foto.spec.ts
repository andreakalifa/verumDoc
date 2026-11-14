// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Ogni pagina delle console R05 a 1440 e 390 px, in tema chiaro e scuro (tema automatico
 * dal sistema operativo, `prefers-color-scheme`), su backend finto.
 * - Sempre: nessuno scorrimento orizzontale della PAGINA a 390 px (i tabelloni stretti
 *   scorrono nel proprio contenitore, mai la pagina) e nessun errore in console.
 * - Con FOTO=1: salva le foto in docs/design/foto/<pagina>-<larghezza>-<tema>.png.
 */
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { installaFinto, type Ruolo } from "./finto";

const CARTELLA = path.resolve(__dirname, "../../docs/design/foto");
const FOTO = !!process.env.FOTO;

interface Vista { nome: string; ruolo: Ruolo; url: string; prepara?: (p: Page) => Promise<void> }

const VISTE: Vista[] = [
  { nome: "conservatore-impostazioni", ruolo: "RSC_CONSERVATORE", url: "/configurazione" },
  { nome: "conservatore-quote", ruolo: "RSC_CONSERVATORE", url: "/configurazione/quote" },
  { nome: "conservatore-bozze", ruolo: "RSC_CONSERVATORE", url: "/configurazione/bozze" },
  {
    nome: "bozza-anteprima", ruolo: "ADMIN_TENANT", url: "/configurazione/bozze/b0220000-1111-4222-8333-444455556666",
    prepara: async p => { await p.getByRole("button", { name: "Calcola l'anteprima" }).click(); await p.getByText(/sarebbero stati rifiutati/).waitFor(); },
  },
  {
    nome: "bozza-piano-cambiato", ruolo: "ADMIN_TENANT", url: "/configurazione/bozze/b0220000-1111-4222-8333-444455556666",
    prepara: async p => {
      await p.getByRole("button", { name: "Calcola l'anteprima" }).click();
      await p.getByRole("button", { name: /Conferma e applica/ }).click();
      await p.getByText("Il piano è cambiato").waitFor();
    },
  },
  { nome: "conservatore-registro", ruolo: "RSC_CONSERVATORE", url: "/registro" },
  { nome: "conservatore-pratiche", ruolo: "RSC_CONSERVATORE", url: "/pratiche" },
  { nome: "pratica-eseguibile", ruolo: "RSC_CONSERVATORE", url: "/pratiche/51a7e0aa-1111-4aaa-8aaa-000000000002" },
  {
    nome: "pratica-autorizza", ruolo: "ADMIN_TENANT", url: "/pratiche/51a7e0aa-1111-4aaa-8aaa-000000000001",
    prepara: async p => { await p.getByRole("button", { name: "Autorizza come produttore…" }).click(); },
  },
  {
    nome: "nuovo-scarto-anteprima", ruolo: "RSC_CONSERVATORE", url: "/pratiche/nuova?tipo=scarto",
    prepara: async p => {
      await p.getByLabel("Ente su cui lavori").selectOption({ index: 1 });
      await p.getByRole("checkbox", { name: /Fatture passive/ }).check();
      await p.getByLabel("Termine scaduto entro il").fill("2025-12-31");
      await p.getByLabel(/^Motivo/).fill("Fatture passive a fine termine decennale");
      await p.getByRole("button", { name: "Calcola l'anteprima" }).click();
      await p.getByText(/Perché alcuni pacchetti restano fuori/).waitFor();
    },
  },
  { nome: "cliente-impostazioni", ruolo: "ADMIN_TENANT", url: "/configurazione" },
  {
    nome: "cliente-modifica-in-riga", ruolo: "ADMIN_TENANT", url: "/configurazione",
    prepara: async p => {
      await p.getByRole("button", { name: "Cambia l'effetto di RGL-103" }).click();
      await p.getByLabel("Nuovo effetto", { exact: true }).selectOption("BLOCCA");
      await p.getByRole("button", { name: "Metti nella bozza" }).click();
    },
  },
  { nome: "cliente-profili", ruolo: "ADMIN_TENANT", url: "/configurazione/profili" },
  { nome: "cliente-classi-accordi", ruolo: "ADMIN_TENANT", url: "/configurazione/classi-accordi" },
  { nome: "cliente-proposte", ruolo: "ADMIN_TENANT", url: "/configurazione/proposte" },
  {
    nome: "cliente-validatore", ruolo: "OPERATORE", url: "/configurazione/validatore",
    prepara: async p => {
      await p.setInputFiles("#vd-val-file", { name: "indice-fattura.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ classe_documentale: "FATTURA", metadati: { codice_cig: "Z1234" } })) });
      await p.getByRole("button", { name: "Controlla l'indice" }).click();
      await p.getByRole("heading", { name: /Indice non conforme/ }).waitFor();
    },
  },
  { nome: "cliente-sftp", ruolo: "ADMIN_TENANT", url: "/canali/sftp", prepara: async p => { await p.getByRole("button", { name: "Chiavi e collegamento" }).first().click(); } },
  { nome: "cliente-webhook", ruolo: "ADMIN_TENANT", url: "/canali/webhook" },
  { nome: "cliente-fascicoli", ruolo: "ADMIN_TENANT", url: "/fascicoli" },
  { nome: "cliente-pratiche", ruolo: "ADMIN_TENANT", url: "/pratiche" },
];

for (const v of VISTE) {
  test(`foto: ${v.nome}`, async ({ page }) => {
    const errori: string[] = [];
    page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errori.push(m.text()); });
    page.on("pageerror", e => errori.push(String(e)));
    await installaFinto(page, v.ruolo);
    for (const tema of ["chiaro", "scuro"] as const) {
      await page.emulateMedia({ colorScheme: tema === "scuro" ? "dark" : "light", reducedMotion: "reduce" });
      for (const larghezza of [1440, 390]) {
        await page.setViewportSize({ width: larghezza, height: larghezza === 390 ? 844 : 900 });
        await page.goto(v.url);
        await page.getByRole("heading", { level: 1 }).first().waitFor();
        await page.waitForLoadState("networkidle");
        if (v.prepara) await v.prepara(page);
        await page.evaluate(() => document.fonts.ready);
        if (larghezza === 390) {
          const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
          expect(sw, `scorrimento orizzontale a 390 px su ${v.url} (${tema})`).toBeLessThanOrEqual(cw);
          // Nessuna cella schiacciata (testo che va a capo lettera per lettera)
          const strette = await page.evaluate(() => [...document.querySelectorAll(".vd-tabellone td")]
            .filter(td => (td.textContent ?? "").trim().length > 3 && td.getBoundingClientRect().width > 0 && td.getBoundingClientRect().width < 56)
            .map(td => (td.textContent ?? "").trim().slice(0, 30)));
          expect(strette, `celle troppo strette a 390 px su ${v.url}`).toEqual([]);
        }
        if (FOTO) await page.screenshot({ path: path.join(CARTELLA, `${v.nome}-${larghezza}-${tema}.png`), fullPage: true });
      }
    }
    expect(errori, "errori nella console del browser").toEqual([]);
  });
}
