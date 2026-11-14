// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Revisione R17 della console (audit finale, backend finto):
 *  - R17-01: l'operatore (permesso «esibizione:richiedi») apre «Esibizioni e copie» e chiede
 *    un'esibizione, senza rinvio muto al quadro;
 *  - R17-02: la Consultazione vede lo storico in sola lettura, niente modulo né «Rigenera»;
 *  - R17-03: il catalogo dei formati a 390 px non collassa la seconda colonna;
 *  - R17-04: «Invia alla firma del responsabile» mette in firma la versione stampabile HTML e
 *    la pagina di firma mostra quel documento;
 *  - R17-05/06: nessuna classe predefinita, select coerente con il valore inviato, metadati
 *    obbligatori della classe nel modulo;
 *  - R17-07: il pacchetto si cerca e si sceglie, o arriva scelto da «Esibisci»;
 *  - R17-11: le rotte non accessibili spiegano, non rimandano al quadro;
 *  - R17-18: pagine centrali con dati, axe-core A/AA in chiaro e in scuro.
 * Schermate solo se R17_FOTO indica una cartella fuori dal repository.
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installaFinto, type Ruolo } from "./finto";

async function pronta(page: Page, url: string) {
  await page.goto(url);
  await page.locator("h1").first().waitFor();
  await page.evaluate(() => document.fonts.ready);
}
const foto = async (page: Page, nome: string) => {
  const cartella = process.env.R17_FOTO;
  if (cartella && !cartella.includes("/work/")) await page.screenshot({ path: `${cartella}/${nome}.png`, fullPage: true });
};

test("R17-01: l'operatore chiede un'esibizione scegliendo il pacchetto dalla ricerca", async ({ page }) => {
  const stato = await installaFinto(page, "OPERATORE");
  await pronta(page, "/overview");
  await page.getByRole("navigation", { name: "Aree del servizio" }).getByRole("link", { name: "Esibizioni" }).click();
  await expect(page).toHaveURL(/\/distribution$/);
  await expect(page.getByRole("heading", { level: 1, name: "Esibizioni e copie" })).toBeVisible();
  // R17-07: nessun UUID da incollare
  await expect(page.getByText(/36 caratteri/)).toHaveCount(0);
  await page.getByLabel(/^Pacchetto da esibire/).fill("Determina");
  await page.getByRole("button", { name: "Cerca", exact: true }).click();
  await page.getByRole("button", { name: /Scegli Determina n\. 412/ }).click();
  await expect(page.getByRole("group", { name: "Pacchetto da esibire" })).toContainText("Determina n. 412/2026");
  await page.getByLabel(/^Motivazione/).fill("Richiesta dell'ufficio legale");
  await page.getByRole("button", { name: "Prepara l'esibizione" }).click();
  await expect(page.getByText("determina_412.pdf")).toBeVisible();
  expect(stato.esibizioniRichieste?.[0]).toMatchObject({ aip_id: "a1b2c3d4-0000-4000-8000-000000000009", tipo: "ESIBIZIONE" });
});

test("R17-07: «Esibisci» dalla scheda del pacchetto porta il pacchetto già scelto", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await pronta(page, "/aip");
  await page.getByRole("button", { name: /Fattura passiva 1203\/2026/ }).click();
  const dialogo = page.getByRole("dialog");
  await expect(dialogo.getByText("Identificativo del pacchetto (PdA)")).toBeVisible();
  await dialogo.getByRole("link", { name: "Esibisci" }).click();
  await expect(page).toHaveURL(/\/distribution\?aip=a1b2c3d4-0000-4000-8000-000000000010$/);
  await expect(page.getByRole("group", { name: "Pacchetto da esibire" })).toContainText("Fattura passiva 1203/2026");
});

test("R17-21: conservazione permanente mostrata come illimitata", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await pronta(page, "/aip");
  await expect(page.getByRole("row", { name: /Determina n\. 412/ })).toContainText("Illimitata");
});

test("R17-02: la Consultazione vede lo storico in sola lettura", async ({ page }) => {
  const stato = await installaFinto(page, "VIEWER");
  await pronta(page, "/distribution");
  await expect(page.getByText("Sola lettura")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Prepara/ })).toHaveCount(0);
  await expect(page.getByLabel(/^Pacchetto da esibire/)).toHaveCount(0);
  await expect(page.getByRole("table", { name: /Richieste di esibizione/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Rigenera se scaduto" })).toHaveCount(0);
  // R17-14: il canale in parole
  await expect(page.getByText("dal portale", { exact: true })).toBeVisible();
  expect(stato.richieste.some(r => r.percorso.startsWith("/dashboard/distribution"))).toBe(false);
});

test("R17-12/11: la Consultazione non apre il versamento e non viene rimandata al quadro", async ({ page }) => {
  await installaFinto(page, "VIEWER");
  await pronta(page, "/versamento");
  await expect(page).toHaveURL(/\/versamento$/);
  await expect(page.getByRole("heading", { name: "Funzione non compresa nel tuo profilo" })).toBeVisible();
  await expect(page.getByLabel("Classe documentale")).toHaveCount(0);
  await pronta(page, "/manuale");
  await expect(page).toHaveURL(/\/manuale$/);
  await expect(page.getByRole("heading", { name: "Pagina non accessibile con il tuo profilo" })).toBeVisible();
  // nessuna voce «Versa un documento» nel menu
  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.getByRole("link", { name: "Versa un documento" })).toHaveCount(0);
});

test("R17-03: catalogo dei formati a 390 px, seconda colonna leggibile e nessuno scorrimento", async ({ page }) => {
  await installaFinto(page, "SUPER_ADMIN");
  await page.setViewportSize({ width: 390, height: 844 });
  for (const tema of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
    await pronta(page, "/configurazione/formati");
    const tabella = page.getByRole("table").filter({ hasText: "Riconosciuta da" });
    const cella = tabella.locator("td[data-label='Riconosciuta da']").first();
    await cella.waitFor();
    const larga = await cella.evaluate(el => el.getBoundingClientRect().width);
    expect(larga, "la colonna non è collassata").toBeGreaterThan(150);
    const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    expect(sw).toBeLessThanOrEqual(cw);
    await expect(page.getByText(/in vigore dal 31\/10\/2026/)).toBeVisible();
    await foto(page, `formati_390_${tema}`);
  }
});

test("R17-04: la versione stampabile va in firma e la pagina di firma la mostra", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await pronta(page, "/documentazione-servizio");
  await page.getByRole("button", { name: "Genera la documentazione" }).first().click();
  await page.getByRole("button", { name: /Invia alla firma del responsabile: Manuale di conservazione/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "In firma" })).toBeVisible();
  expect(stato.inviatiAllaFirma).toHaveLength(1);
  // la guida non si firma: niente comando
  await expect(page.getByRole("button", { name: /Invia alla firma del responsabile: Guida/ })).toHaveCount(0);
  await page.getByRole("link", { name: "apri Firme del responsabile" }).click();
  await expect(page.getByRole("button", { name: /Prepara il manuale/ })).toHaveCount(0);
  const riga = page.getByRole("row", { name: /versione 1/ }).filter({ hasText: ".html" });
  await riga.getByRole("button", { name: "Firma" }).click();
  const cornice = page.frameLocator("iframe[title^='Documento da firmare']");
  await expect(cornice.getByRole("heading", { name: /Manuale di conservazione/ })).toBeVisible();
  expect(await page.locator("iframe[title^='Documento da firmare']").getAttribute("sandbox")).toBe("");
});

test("R17-05/06: nessuna classe predefinita; metadati della classe nel modulo e nel versamento", async ({ page }) => {
  const stato = await installaFinto(page, "OPERATORE");
  await pronta(page, "/versamento");
  const classe = page.getByLabel("Classe documentale");
  await expect(classe).toHaveValue("");
  await expect(classe.locator("option")).toHaveText(["Scegli la classe", "Fatture passive", "Determine"]);
  await expect(page.getByLabel(/^Numero fattura/)).toHaveCount(0);
  await page.getByRole("button", { name: "Versa in conservazione" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "classe documentale" })).toBeVisible();
  await classe.selectOption({ label: "Fatture passive" });
  await expect(classe).toHaveValue("FATTURA");
  await expect(page.getByLabel(/^Numero fattura/)).toBeVisible();
  await page.locator("#vd-file").setInputFiles({ name: "fattura.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7 prova") });
  await page.getByLabel(/^Identificativo del documento/).fill("FT-2026-1203");
  await page.getByLabel(/^Tipologia documentale/).fill("Fattura elettronica");
  await page.getByLabel(/^Data del documento/).fill("2026-10-20");
  await page.getByLabel(/^Codice fiscale o partita IVA del produttore/).fill("00000000000");
  await page.getByLabel(/^Nome del produttore/).fill("Fornitore di prova");
  await page.getByLabel(/^Oggetto/).fill("Forniture di cancelleria");
  await page.getByRole("button", { name: "Versa in conservazione" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /numero fattura/i })).toBeVisible();
  await page.getByLabel(/^Numero fattura/).fill("1203");
  await page.getByLabel(/^Partita iva cedente|^Partita IVA cedente/i).fill("00000000000");
  await page.getByRole("button", { name: "Versa in conservazione" }).click();
  await expect.poll(() => stato.versamenti?.length ?? 0).toBe(1);
  const inviato = stato.versamenti![0];
  expect(inviato).toContain('"classe_documentale":"FATTURA"');
  expect(inviato).toContain('"numero_fattura":"1203"');
});

const CENTRALI: { url: string; ruolo: Ruolo; titolo: RegExp }[] = [
  { url: "/aip", ruolo: "ADMIN_TENANT", titolo: /Fattura passiva 1203/ },
  { url: "/search?q=Determina", ruolo: "OPERATORE", titolo: /Determina n\. 412/ },
  { url: "/notifiche", ruolo: "ADMIN_TENANT", titolo: /Pacchetto conservato/ },
  { url: "/sla", ruolo: "RSC_CONSERVATORE", titolo: /Disponibilità del servizio/ },
  { url: "/audit", ruolo: "RSC_CONSERVATORE", titolo: /10\.0\.0\.12|distribuzione/i },
  { url: "/manuale", ruolo: "RSC_CONSERVATORE", titolo: /Soggetti e ruoli/ },
  { url: "/contratti", ruolo: "SUPER_ADMIN", titolo: /CONS-2024-017/ },
  { url: "/nomine-rsc", ruolo: "SUPER_ADMIN", titolo: /RSPDPR80A01H501X|Di Prova/ },
  { url: "/code-scarto", ruolo: "SUPER_ADMIN", titolo: /pdv\.received\.dlq/ },
  { url: "/distribution", ruolo: "OPERATORE", titolo: /Restituzione all'ente/ },
  { url: "/documentazione-servizio", ruolo: "RSC_CONSERVATORE", titolo: /Nessuna versione generata|Manuale di conservazione/ },
  { url: "/firme-responsabile", ruolo: "RSC_CONSERVATORE", titolo: /Da firmare/ },
];

for (const p of CENTRALI) {
  test(`R17-18: ${p.url} con dati, axe A/AA in chiaro e in scuro`, async ({ page }) => {
    await installaFinto(page, p.ruolo);
    await page.setViewportSize({ width: 1280, height: 900 });
    for (const tema of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await pronta(page, p.url);
      await expect(page.getByText(p.titolo).first()).toBeVisible();
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const difetti = r.violations.flatMap(v => v.nodes.map(n => `${v.id} ${n.target.join(" ")}`));
      expect(difetti, `axe su ${p.url} (${tema})`).toEqual([]);
    }
  });
}

test("R17-09/10: sotto-navigazione su più righe sopra i 1024 px, fuoco interno visibile", async ({ page }) => {
  await installaFinto(page, "RSC_CONSERVATORE");
  await page.setViewportSize({ width: 1280, height: 900 });
  await pronta(page, "/configurazione/quote");
  const sotto = page.getByRole("navigation", { name: "Pagine di Configurazione" });
  for (const nome of ["Impostazioni e regole", "Validatore", "Quote degli enti"]) {
    await expect(sotto.getByRole("link", { name: nome })).toBeInViewport();
  }
  const link = sotto.getByRole("link", { name: "Validatore" });
  await link.focus();
  const stile = await link.evaluate(el => { const s = getComputedStyle(el); return { offset: s.outlineOffset, ombra: s.boxShadow }; });
  expect(stile.offset).toBe("-3px");
  expect(stile.ombra).toContain("inset");
});

test("R17-23: «Pratiche» resta attiva sul dettaglio, «Nuova pratica» solo sulla sua pagina", async ({ page }) => {
  await installaFinto(page, "RSC_CONSERVATORE");
  await page.setViewportSize({ width: 1280, height: 900 });
  await pronta(page, "/pratiche/51a7e0aa-1111-4aaa-8aaa-000000000002");
  const sotto = page.getByRole("navigation", { name: "Pagine di Pratiche" });
  await expect(sotto.getByRole("link", { name: "Pratiche", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(sotto.getByRole("link", { name: "Nuova pratica" })).not.toHaveAttribute("aria-current", "page");
});

for (const larghezza of [390, 1280]) {
  for (const tema of ["light", "dark"] as const) {
    test(`controllo visivo delle pagine toccate a ${larghezza} px (${tema})`, async ({ page }) => {
      await installaFinto(page, "OPERATORE");
      await page.setViewportSize({ width: larghezza, height: larghezza < 600 ? 844 : 900 });
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      for (const [url, nome] of [["/distribution", "esibizioni"], ["/versamento", "versamento"], ["/aip", "archivio"]] as const) {
        await pronta(page, url);
        const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
        expect(sw, `scorrimento orizzontale su ${url}`).toBeLessThanOrEqual(cw);
        const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
        expect(r.violations.map(v => v.nodes.map(n => n.target.join(" ")).join(", "))).toEqual([]);
        await foto(page, `${nome}_${larghezza}_${tema}`);
      }
    });
  }
}
