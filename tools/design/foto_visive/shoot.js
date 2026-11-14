// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const M = require("./mocks");
const OUT = __dirname;
const BASE = "http://localhost:4173";
const ROUTES = ["overview", "versamento", "versamenti", "aip", "search", "blockchain", "hashchain", "distribution", "classi-documentali", "utenti", "tenants", "sla", "manuale", "audit", "notifiche", "fascicoli", "contratti", "nomine-rsc"];
const VIEWPORTS = { desk: { width: 1440, height: 900 }, mob: { width: 390, height: 844 } };
const log = [];

async function ctxFor(browser, vp, mode, withSession = true) {
  const ctx = await browser.newContext({ viewport: VIEWPORTS[vp], deviceScaleFactor: 1, locale: "it-IT" });
  // Google Fonts bloccato dal proxy: servo gli stessi font da @fontsource
  const FD = path.join(__dirname, "node_modules/@fontsource");
  const face = (fam, file, w, style = "normal") => `@font-face{font-family:'${fam}';font-style:${style};font-weight:${w};font-display:swap;src:url(https://fonts.gstatic.com/local/${file}) format('woff2');}`;
  let css = "";
  for (const w of [300, 400, 500, 600, 700]) css += face("DM Sans", `dm-sans/files/dm-sans-latin-${w}-normal.woff2`, w);
  for (const w of [400, 500]) css += face("DM Mono", `dm-mono/files/dm-mono-latin-${w}-normal.woff2`, w);
  css += face("Instrument Serif", "instrument-serif/files/instrument-serif-latin-400-normal.woff2", 400);
  css += face("Instrument Serif", "instrument-serif/files/instrument-serif-latin-400-italic.woff2", 400, "italic");
  await ctx.route("https://fonts.googleapis.com/**", (r) => r.fulfill({ status: 200, contentType: "text/css", body: css }));
  await ctx.route("https://fonts.gstatic.com/local/**", (r) => {
    const rel = r.request().url().split("/local/")[1];
    r.fulfill({ status: 200, contentType: "font/woff2", body: fs.readFileSync(path.join(FD, rel)) });
  });
  await ctx.route("**/api/**", async (route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (mode === "loading") { await new Promise((r) => setTimeout(r, 60000)); return route.abort().catch(() => {}); }
    if (mode === "error") return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Internal Server Error" }) });
    let body = mode === "empty" ? M.empty(url) : mode === "worst" ? M.worst(url) : M.normal(url, method);
    if (typeof mode === "function") body = mode(url, method) ?? M.normal(url, method);
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  // Sessione in cookie (R05): senza, il portale mostra la pagina d'accesso
  if (withSession) await M.instradaSessione(ctx, "SUPER_ADMIN");
  return ctx;
}

async function shot(page, name, full = true) {
  const f = path.join(OUT, name + ".png");
  await page.screenshot({ path: f, fullPage: full });
  log.push(f);
}

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const only = process.argv[2];

  if (!only || only === "main") {
    for (const vp of Object.keys(VIEWPORTS)) {
      // login senza sessione
      let ctx = await ctxFor(browser, vp, "normal", false);
      let page = await ctx.newPage();
      await page.goto(BASE + "/login", { waitUntil: "networkidle" }).catch(() => {});
      await page.waitForTimeout(700);
      await shot(page, `${vp}-login`);
      await ctx.close();

      ctx = await ctxFor(browser, vp, "normal");
      page = await ctx.newPage();
      const errs = [];
      page.on("pageerror", (e) => errs.push(e.message));
      for (const r of ROUTES) {
        await page.goto(BASE + "/" + r, { waitUntil: "networkidle" }).catch(() => {});
        await page.waitForTimeout(900);
        if (r === "search") {
          await page.fill('input[placeholder^="Cerca per URN"]', "delibera");
          await page.keyboard.press("Enter");
          await page.waitForTimeout(800);
        }
        await shot(page, `${vp}-${r}`);
        // viewport only (above the fold) for mobile
        if (vp === "mob") await shot(page, `${vp}-${r}-fold`, false);
      }
      if (errs.length) fs.writeFileSync(path.join(OUT, `${vp}-pageerrors.txt`), errs.join("\n"));
      await ctx.close();
    }
  }

  if (!only || only === "states") {
    for (const mode of ["loading", "error", "empty"]) {
      const ctx = await ctxFor(browser, "desk", mode);
      const page = await ctx.newPage();
      for (const r of ["overview", "aip", "hashchain", "blockchain", "contratti", "nomine-rsc", "fascicoli", "versamenti"]) {
        await page.goto(BASE + "/" + r, { waitUntil: "domcontentloaded" }).catch(() => {});
        await page.waitForTimeout(mode === "loading" ? 1200 : 1500);
        await shot(page, `state-${mode}-${r}`, false);
      }
      await ctx.close();
    }
    // worst case
    const ctx = await ctxFor(browser, "desk", "worst");
    const page = await ctx.newPage();
    for (const r of ["overview", "aip", "utenti"]) {
      await page.goto(BASE + "/" + r, { waitUntil: "networkidle" }).catch(() => {});
      await page.waitForTimeout(900);
      await shot(page, `worst-${r}`, false);
    }
    const ctxm = await ctxFor(browser, "mob", "worst");
    const pm = await ctxm.newPage();
    await pm.goto(BASE + "/overview", { waitUntil: "networkidle" }).catch(() => {});
    await pm.waitForTimeout(900);
    await shot(pm, `worst-mob-overview`, false);
    await ctxm.close();
    await ctx.close();
  }

  if (!only || only === "flows") {
    // AIP drawer
    let ctx = await ctxFor(browser, "desk", "normal");
    let page = await ctx.newPage();
    await page.goto(BASE + "/aip", { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.locator(".table-row").first().click();
    await page.waitForTimeout(500);
    await shot(page, "flow-aip-drawer", false);
    // focus ring: tab through
    await page.goto(BASE + "/overview", { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    for (let i = 0; i < 4; i++) await page.keyboard.press("Tab");
    await shot(page, "flow-focus-tab4", false);
    const focused = await page.evaluate(() => { const a = document.activeElement; const cs = getComputedStyle(a); return { tag: a.tagName, text: a.textContent.trim().slice(0, 40), outline: cs.outlineStyle + " " + cs.outlineWidth + " " + cs.outlineColor, boxShadow: cs.boxShadow }; });
    fs.writeFileSync(path.join(OUT, "focus.json"), JSON.stringify(focused, null, 2));
    // Distribution esibizione + submit
    await page.goto(BASE + "/distribution", { waitUntil: "networkidle" });
    await page.fill('input[placeholder="UUID dell\'AIP..."]', M.uuid("aip0"));
    await page.getByText("Esibizione", { exact: true }).first().click();
    await page.fill("textarea", "Verifica fiscale Guardia di Finanza, PV n. 112/2026");
    await page.getByRole("button", { name: /Genera PdD/ }).click();
    await page.waitForTimeout(700);
    await shot(page, "flow-distribution-esibizione", false);
    // Blockchain verify
    await page.goto(BASE + "/blockchain", { waitUntil: "networkidle" });
    await page.fill('input[placeholder^="Inserisci AIP UUID"]', M.uuid("aip0"));
    await page.keyboard.press("Enter");
    await page.waitForTimeout(700);
    await shot(page, "flow-blockchain-verify", false);
    // computed fonts check
    const fonts = await page.evaluate(() => [...document.fonts].filter(f => f.status === "loaded").map(f => f.family + " " + f.weight));
    fs.writeFileSync(path.join(OUT, "fonts.json"), JSON.stringify(fonts));
    await ctx.close();

    // Versamento happy path
    let n = 0;
    const seq = ["IN_VALIDAZIONE", "IN_BUILD", "CONSERVATO"];
    ctx = await ctxFor(browser, "desk", (url, method) => {
      const p = new URL(url).pathname;
      if (/\/api\/v1\/versamento\/[^u]/.test(p)) { const s = seq[Math.min(n++, 2)]; return { stato: s, pdv_id: M.uuid("pdv-live"), urn: s === "CONSERVATO" ? "urn:IT:c_c287:DELIBERA:2026:000425" : null }; }
      return null;
    });
    page = await ctx.newPage();
    await page.goto(BASE + "/versamento", { waitUntil: "networkidle" });
    const tmp = path.join(OUT, "Delibera_GC_112_2026.pdf");
    fs.writeFileSync(tmp, "%PDF-1.7\n" + "x".repeat(184000));
    await page.setInputFiles('input[type="file"]', tmp);
    const inputs = page.locator("input.input-dark");
    await page.fill('input[placeholder="es. FT-2026-001"]', "DEL-GC-2026-0112");
    await page.fill('input[placeholder="es. fattura elettronica"]', "Delibera di Giunta");
    await page.fill('input[type="date"]', "2026-10-14");
    await page.fill('input[placeholder="es. 12345678901"]', "00000000011");
    await page.fill('input[placeholder="es. Acme S.r.l."]', "Comune di Borgo Immaginario");
    await page.fill('input[placeholder^="es. Fattura servizi IT"]', "Variazione di bilancio 2026-2028");
    await shot(page, "flow-versamento-filled", true);
    await page.getByRole("button", { name: /Avvia conservazione/ }).click();
    await page.waitForTimeout(2600);
    await shot(page, "flow-versamento-running", false);
    await page.waitForTimeout(5000);
    await shot(page, "flow-versamento-done", false);
    await ctx.close();

    // Versamento error path (VLD-703)
    ctx = await ctxFor(browser, "desk", (url) => {
      const p = new URL(url).pathname;
      if (/\/api\/v1\/versamento\/[^u]/.test(p)) return { stato: "ERRORE", pdv_id: M.uuid("pdv-live"), errori: [{ codice: "VLD-703", msg: "VLD-703 Classe documentale 'mandato_pagamento' non abilitata per il produttore" }] };
      return null;
    });
    page = await ctx.newPage();
    await page.goto(BASE + "/versamento", { waitUntil: "networkidle" });
    await page.setInputFiles('input[type="file"]', tmp);
    await page.fill('input[placeholder="es. FT-2026-001"]', "OPI-2026-3381");
    await page.fill('input[placeholder="es. fattura elettronica"]', "Mandato di pagamento");
    await page.fill('input[type="date"]', "2026-10-14");
    await page.fill('input[placeholder="es. 12345678901"]', "00000000011");
    await page.fill('input[placeholder="es. Acme S.r.l."]', "Comune di Borgo Immaginario");
    await page.fill('input[placeholder^="es. Fattura servizi IT"]', "Mandato n. 3381 fornitore Hera S.p.A.");
    await page.getByRole("button", { name: /Avvia conservazione/ }).click();
    await page.waitForTimeout(9000);
    await shot(page, "flow-versamento-error-9s", false);
    await ctx.close();
  }

  await browser.close();
  fs.writeFileSync(path.join(OUT, "index.txt"), log.join("\n"));
  console.log("OK", log.length);
})().catch((e) => { console.error(e); process.exit(1); });
