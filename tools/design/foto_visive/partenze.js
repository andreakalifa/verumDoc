// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
const { chromium } = require("playwright");
const path = require("path");
const M = require("./mocks");
const OUT = process.argv[2];
const BASE = "http://localhost:4174";
async function ctx(browser, vp, mode, ruolo = "RSC_CONSERVATORE") {
  const c = await browser.newContext({ viewport: vp, deviceScaleFactor: 1, locale: "it-IT", reducedMotion: "reduce" });
  await c.route("**/api/**", async (r) => {
    const url = r.request().url();
    if (mode === "error") return r.fulfill({ status: 500, contentType: "application/json", body: "{}" });
    let body = mode === "empty" ? M.empty(url) : mode === "worst" ? M.worst(url) : M.normal(url, r.request().method());
    if (mode === "compromessa" && url.includes("/dashboard/hashchain")) body = { integrita: { integra: false, messaggio: "Blocco 4.211: impronta divergente da quella iscritta il 03/09/2026" }, totale_blocchi: 4817 };
    if (url.includes("/v1/versamento/") && !url.endsWith("/ud")) body = mode === "rifiuto" ? { stato: "ERRORE", errori: [{ msg: "Formato non ammesso dalla classe «fattura»: image/heic" }] } : { stato: "CONSERVATO", urn: "urn:IT:c_c287:FATTURA:2026:000419", pdv_id: "x" };
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await M.instradaSessione(c, ruolo);  // sessione in cookie (R05)
  return c;
}
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const D = { width: 1440, height: 900 }, Mo = { width: 390, height: 844 };
  const shot = async (vp, route, name, mode = "normal", act) => {
    const c = await ctx(b, vp, mode); const p = await c.newPage();
    const errs = []; p.on("pageerror", e => errs.push(e.message));
    await p.goto(BASE + route, { waitUntil: "networkidle" }); await p.waitForTimeout(500);
    if (act) await act(p);
    await p.screenshot({ path: path.join(OUT, name + ".png"), fullPage: true });
    if (errs.length) console.log(name, errs);
    await c.close();
  };
  if (process.argv[3] === "focus") { await shot(D, "/overview", "focus-riga", "normal", async p => { let n = 0; for (; n < 60; n++) { await p.keyboard.press("Tab"); const t = await p.evaluate(() => document.activeElement?.textContent || ""); if (t.startsWith("Rinnovo marche")) break; } console.log("tab presses", n + 1); }); await b.close(); return; }
  await shot(D, "/overview", "desktop");
  await shot(Mo, "/overview", "mobile");
  await shot(D, "/versamento", "versamento-desktop");
  await shot(Mo, "/versamento", "versamento-mobile");
  await shot(D, "/overview", "stato-errore", "error");
  await shot(D, "/overview", "stato-compromessa", "compromessa");
  await shot(D, "/overview", "stato-vuoto", "empty");
  await shot(D, "/overview", "stato-peggiore", "worst");
  const compila = async (p) => {
    await p.setInputFiles("#vd-file", { name: "fattura_418_PA.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7 demo") });
    await p.fill("#vd-id", "FT-2026-0419"); await p.fill("#vd-tipo", "fattura elettronica"); await p.fill("#vd-data", "2026-10-19");
    await p.fill("#vd-prod-id", "00000000011"); await p.fill("#vd-prod-nome", "Comune di Borgo Immaginario");
    await p.fill("#vd-oggetto", "Fattura n. 419/PA, manutenzione verde pubblico");
    await p.click("button[type=submit]"); await p.waitForTimeout(4800);
  };
  await shot(D, "/versamento", "versamento-ok", "normal", compila);
  await shot(D, "/versamento", "versamento-rifiuto", "rifiuto", compila);
  await shot(D, "/versamento", "versamento-mancanti", "normal", async p => { await p.click("button[type=submit]"); });
  await shot(D, "/aip", "pagina-ponte-aip");
  await shot(D, "/da-realizzare/marche", "da-realizzare-marche");
  await shot(D, "/overview", "focus-riga", "normal", async p => { let n = 0; for (; n < 60; n++) { await p.keyboard.press("Tab"); const t = await p.evaluate(() => document.activeElement?.textContent || ""); if (t.startsWith("Rinnovo marche")) break; } console.log("tab presses", n + 1); });
  await b.close();
})();
