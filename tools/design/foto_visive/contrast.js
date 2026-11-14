// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
// Rapporti di contrasto WCAG 2.x sui colori reali di index.css / inline
const hex = (h) => { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
const blend = (fg, a, bg) => fg.map((c, i) => Math.round(c * a + bg[i] * (1 - a)));
const lum = (rgb) => { const [r, g, b] = rgb.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x); return (l1 + 0.05) / (l2 + 0.05); };
const C = { base: hex("#0a0e1a"), card: hex("#111827"), hover: hex("#1a2236"), border: hex("#1e2d45"), gold: hex("#c9a84c"), goldL: hex("#e2c97e"), t1: hex("#f0f4ff"), t2: hex("#8b9cc4"), t3: hex("#4a5a7a"), accent: hex("#4f8ef7"), green: hex("#22c55e"), g4: hex("#4ade80"), red: hex("#ef4444"), r4: hex("#f87171"), amber: hex("#f59e0b"), a4: hex("#fbbf24"), b4: hex("#60a5fa"), p4: hex("#a78bfa"), s4: hex("#94a3b8") };
const goldDimOnCard = blend(C.gold, 0.12, C.card);
const rows = [
  ["text-3 #4a5a7a su card #111827 (label, sottotitoli, date, placeholder)", C.t3, C.card, "11-12px"],
  ["text-3 #4a5a7a su base #0a0e1a (sottotitoli pagina, placeholder input)", C.t3, C.base, "12-13px"],
  ["text-3 su bg-hover #1a2236 (riga in hover)", C.t3, C.hover, "10-11px"],
  ["text-2 #8b9cc4 su card", C.t2, C.card, "11-13px"],
  ["text-2 su base", C.t2, C.base, ""],
  ["text-1 #f0f4ff su card", C.t1, C.card, ""],
  ["gold #c9a84c su gold-dim (voce nav attiva)", C.gold, goldDimOnCard, "12.5px"],
  ["#0a0e1a su gold (btn-gold)", C.base, C.gold, "14px 600"],
  ["#0a0e1a su gold a opacity .5 (btn disabilitato su base)", C.base, blend(C.gold, 0.5, C.base), ""],
  ["accent #4f8ef7 su card (URN, AIP ID)", C.accent, C.card, "10-11px mono"],
  ["#4ade80 su verde 12% (tag-green) su card", C.g4, blend(C.green, 0.12, C.card), "11px"],
  ["#f87171 su rosso 12% (tag-red)", C.r4, blend(C.red, 0.12, C.card), "11px"],
  ["#fbbf24 su ambra 12% (tag-amber)", C.a4, blend(C.amber, 0.12, C.card), "11px"],
  ["#60a5fa su blu 12% (tag-blue)", C.b4, blend(hex("#3b82f6"), 0.12, C.card), ""],
  ["#a78bfa su viola 12% (tag-purple)", C.p4, blend(hex("#8b5cf6"), 0.12, C.card), ""],
  ["tag-gold MANCANTE: testo ereditato su card (login: colore bottone default)", hex("#000000"), C.card, "9px"],
  ["tick grafici #4a5a7a su card", C.t3, C.card, "9-10px"],
  ["bordo input #1e2d45 su base (WCAG 1.4.11 ≥3:1)", C.border, C.base, "non-text"],
  ["bordo input focus gold-dim su base", blend(C.gold, 0.12, C.base), C.base, "non-text"],
  ["outline focus default rgb(16,16,16) su card", [16, 16, 16], C.card, "non-text"],
  ["pulse 'Sistema operativo' text-3 10px su card", C.t3, C.card, "10px"],
];
console.log("| Coppia | Rapporto | Dimensione | AA testo normale (4.5) | AA testo grande/UI (3) |");
console.log("|---|---|---|---|---|");
for (const [n, f, b, s] of rows) { const r = ratio(f, b); console.log(`| ${n} | ${r.toFixed(2)}:1 | ${s} | ${r >= 4.5 ? "OK" : "FAIL"} | ${r >= 3 ? "OK" : "FAIL"} |`); }
