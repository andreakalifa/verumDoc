// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R12c — revisione grafica della console. Prove che falliscono se torna un difetto
 * visto dal committente:
 *  - a 1280 e 1850 px le aree della testata non finiscono sotto la ricerca né sotto le
 *    azioni, e nessuna area è tagliata (tutte intere nella loro riga);
 *  - a 390 px nessuna pagina principale scorre in orizzontale; sotto 1024 px le aree
 *    stanno nel menu a scomparsa, che si apre e si chiude da tastiera;
 *  - contrasto AA (axe-core, regola color-contrast) in tema chiaro e scuro;
 *  - nel quadro del servizio nessuna riga «Esempio» senza l'indicatore di sviluppo.
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installaFinto, type Ruolo } from "./finto";

const fuoriSessione = (page: Page) =>
  page.route("**/api/v1/auth/sessione", r => r.fulfill({ status: 401, contentType: "application/json", body: "{}" }));

async function pronta(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole("heading", { level: 1 }).first().waitFor();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

/** Rettangoli di aree, ricerca, azioni e di ogni scheda; nessuna sovrapposizione e nessun taglio. */
async function testata(page: Page) {
  return page.evaluate(() => {
    const r = (sel: string) => document.querySelector(sel)?.getBoundingClientRect() ?? null;
    const ul = document.querySelector<HTMLElement>('nav[aria-label="Aree del servizio"] ul');
    const nav = ul?.getBoundingClientRect() ?? null;
    const schede = [...(ul?.querySelectorAll("a") ?? [])].map(a => ({ testo: a.textContent ?? "", r: a.getBoundingClientRect() }));
    return {
      nav, cerca: r("#vd-cerca"), azioni: r(".vd-testata-azioni"), marchio: r(".vd-testata-marchio"),
      tagliata: ul ? ul.scrollWidth > ul.clientWidth + 1 : true,
      schede: schede.map(s => ({ testo: s.testo, sinistra: s.r.left, destra: s.r.right, alto: s.r.top, basso: s.r.bottom })),
    };
  });
}
type R = { left: number; right: number; top: number; bottom: number } | null;
const sovrapposti = (a: R, b: R) => !!a && !!b && a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;

for (const ruolo of ["SUPER_ADMIN", "RSC_CONSERVATORE", "OPERATORE"] as Ruolo[]) {
  for (const larghezza of [1280, 1850]) {
    test(`testata a ${larghezza} px (${ruolo}): aree intere, mai sotto ricerca o azioni`, async ({ page }) => {
      await installaFinto(page, ruolo);
      await page.setViewportSize({ width: larghezza, height: 900 });
      await pronta(page, "/versamento");
      const t = await testata(page);
      expect(t.nav, "le aree sono visibili in testata").not.toBeNull();
      expect(t.tagliata, "nessuna area tagliata o nascosta dallo scorrimento").toBe(false);
      for (const s of t.schede) {
        const r = { left: s.sinistra, right: s.destra, top: s.alto, bottom: s.basso };
        expect(sovrapposti(r, t.cerca), `«${s.testo}» sotto la ricerca`).toBe(false);
        expect(sovrapposti(r, t.azioni), `«${s.testo}» sotto le azioni`).toBe(false);
        expect(sovrapposti(r, t.marchio), `«${s.testo}» sotto il marchio`).toBe(false);
      }
      expect(sovrapposti(t.cerca, t.azioni), "ricerca sotto le azioni").toBe(false);
    });
  }
}

const PAGINE: { url: string; ruolo: Ruolo | null }[] = [
  { url: "/login", ruolo: null },
  { url: "/overview", ruolo: "RSC_CONSERVATORE" },
  { url: "/versamento", ruolo: "SUPER_ADMIN" },
  { url: "/versamenti", ruolo: "OPERATORE" },
  { url: "/aip", ruolo: "ADMIN_TENANT" },
  { url: "/hashchain", ruolo: "RSC_CONSERVATORE" },
  { url: "/configurazione", ruolo: "RSC_CONSERVATORE" },
  { url: "/pratiche", ruolo: "RSC_CONSERVATORE" },
  { url: "/utenti", ruolo: "SUPER_ADMIN" },
  // revisione R17 (R17-03): tabellone del catalogo base impilato a 390 px
  { url: "/configurazione/formati", ruolo: "SUPER_ADMIN" },
  { url: "/distribution", ruolo: "OPERATORE" },
  { url: "/documentazione-servizio", ruolo: "RSC_CONSERVATORE" },
];

for (const p of PAGINE) {
  test(`390 px senza scorrimento orizzontale: ${p.url}`, async ({ page }) => {
    await installaFinto(page, p.ruolo ?? "OPERATORE");
    if (!p.ruolo) await fuoriSessione(page);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const tema of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await pronta(page, p.url);
      const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(sw, `scorrimento orizzontale a 390 px su ${p.url} (${tema})`).toBeLessThanOrEqual(cw);
      // Marchio e azioni della testata non si coprono
      if (p.ruolo) {
        const t = await testata(page);
        expect(sovrapposti(t.marchio, t.azioni), "marchio sotto le azioni").toBe(false);
      }
    }
  });
}

test("sotto 1024 px le aree sono nel menu a scomparsa, navigabile da tastiera", async ({ page }) => {
  await installaFinto(page, "RSC_CONSERVATORE");
  await page.setViewportSize({ width: 768, height: 1024 });
  await pronta(page, "/overview");
  await expect(page.getByRole("navigation", { name: "Aree del servizio" })).toBeHidden();
  const menu = page.getByRole("button", { name: /menu della persona e delle aree/ });
  await menu.focus();
  await page.keyboard.press("Enter");
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  const aree = page.getByRole("navigation", { name: "Menu delle aree" });
  await expect(aree.getByRole("link", { name: "Catena delle impronte" })).toBeVisible();
  await page.keyboard.press("Tab");
  const dentro = await page.evaluate(() => !!document.activeElement?.closest("#vd-menu-pannello"));
  expect(dentro, "il Tab dal bottone entra nel menu").toBe(true);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(menu).toBeFocused();
  // Un clic su una voce porta alla pagina e chiude il menu
  await menu.click();
  await aree.getByRole("link", { name: "Pacchetti in arrivo" }).click();
  await expect(page).toHaveURL(/\/versamenti$/);
  await expect(page.locator("#vd-menu-pannello")).toHaveCount(0);
});

test("il fuoco è visibile sulle schede della testata e nel menu", async ({ page }) => {
  await installaFinto(page, "RSC_CONSERVATORE");
  await page.setViewportSize({ width: 1440, height: 900 });
  await pronta(page, "/overview");
  const scheda = page.getByRole("navigation", { name: "Aree del servizio" }).getByRole("link").first();
  await scheda.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  const stile = await scheda.evaluate(el => getComputedStyle(el).outlineStyle);
  expect(stile).not.toBe("none");
});

for (const tema of ["light", "dark"] as const) {
  for (const p of PAGINE.filter(x => ["/login", "/overview", "/versamento", "/versamenti", "/configurazione"].includes(x.url))) {
    test(`contrasto AA (${tema}): ${p.url}`, async ({ page }) => {
      await installaFinto(page, p.ruolo ?? "OPERATORE");
      if (!p.ruolo) await fuoriSessione(page);
      await page.emulateMedia({ colorScheme: tema, reducedMotion: "reduce" });
      await page.setViewportSize({ width: 1280, height: 900 });
      await pronta(page, p.url);
      const r = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
      const difetti = r.violations.flatMap(v => v.nodes.map(n => `${n.target.join(" ")}: ${n.failureSummary?.split("\n").slice(-1)[0] ?? ""}`));
      expect(difetti, "testi sotto il contrasto AA").toEqual([]);
    });
  }
}

test("quadro del servizio: niente righe «Esempio» fra i dati reali", async ({ page }) => {
  await installaFinto(page, "RSC_CONSERVATORE");
  await pronta(page, "/overview");
  const partenze = page.getByRole("region", { name: "Partenze" }).or(page.locator("section[aria-labelledby=vd-partenze]"));
  await expect(partenze.getByText(/Esempio/)).toHaveCount(0);
  // Le Partenze vengono dai dati del servizio: stato della catena e lavoro automatico
  await expect(partenze.getByRole("link", { name: /catena delle impronte/ })).toBeVisible();
  await expect(partenze.getByRole("link", { name: "Rinnovo dei blocchi WORM" })).toBeVisible();
  // Con l'indicatore esplicito di sviluppo le righe dimostrative si dichiarano
  await pronta(page, "/overview?dimostrativi=1");
  await expect(page.getByRole("note")).toContainText("Dati dimostrativi (solo sviluppo)");
  await expect(partenze.getByText(/^Esempio: /).first()).toBeVisible();
});

test("motivi ferroviari: corsa a fermate, binario sotto la testa, passaggio a livello", async ({ page }) => {
  await installaFinto(page, "OPERATORE");
  await pronta(page, "/versamento");
  const linea = page.getByRole("list", { name: "Fermate del pacchetto" });
  await expect(linea.locator(".vd-fermata")).toHaveCount(5);
  await expect(linea.locator(".vd-fermata--attesa")).toHaveCount(5);
  await expect(page.getByText("--:--")).toHaveCount(0);
  // Il binario chiude la testa di pagina (pseudo-elemento alto 12px), non un filetto
  const binario = await page.locator(".vd-testa-pagina").evaluate(el => getComputedStyle(el, "::after").height);
  expect(binario).toBe("12px");
  // Quadro: fascia «mai verificata» con il passaggio a livello; WORM senza date a decenni
  await installaFinto(page, "RSC_CONSERVATORE");
  await pronta(page, "/overview");
  const strisce = await page.locator(".vd-banda").evaluate(el => getComputedStyle(el, "::before").backgroundImage);
  expect(strisce).toContain("repeating-linear-gradient");
  await expect(page.getByText(/Nessun rinnovo necessario entro 90 giorni/)).toBeVisible();
});
