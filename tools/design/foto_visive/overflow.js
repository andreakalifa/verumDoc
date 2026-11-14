// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
const { chromium } = require("playwright");
const M = require("./mocks");
(async () => {
  const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 } });
  await ctx.route("**/api/**", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(M.normal(r.request().url())) }));
  await M.instradaSessione(ctx, "SUPER_ADMIN");  // sessione in cookie (R05)
  await ctx.route("https://fonts.googleapis.com/**", (r) => r.fulfill({ status: 200, contentType: "text/css", body: "" }));
  const p = await ctx.newPage();
  for (const r of ["overview", "versamento", "aip", "audit"]) {
    await p.goto("http://localhost:4173/" + r, { waitUntil: "networkidle" });
    const m = await p.evaluate(() => ({ scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth, mainW: document.querySelector("main")?.getBoundingClientRect().width, aside: document.querySelector("aside")?.getBoundingClientRect().width }));
    console.log(r, JSON.stringify(m));
  }
  // count inline styles & interactive divs
  const info = await p.evaluate(() => ({ landmarks: [...document.querySelectorAll("main,nav,aside,header")].map(e => e.tagName), h1: document.querySelectorAll("h1").length, skip: !!document.querySelector('a[href="#main"],a[href="#content"]'), ariaCurrent: !!document.querySelector('[aria-current]') }));
  console.log(JSON.stringify(info));
  await b.close();
})();
