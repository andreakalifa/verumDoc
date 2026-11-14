// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
// Prova della pagina del verificatore con Playwright (Chromium), sugli stessi
// dati di prova della CLI (tools/verificatore/tests/dati_prova.py).
//
// Uso:  node tools/verificatore/web/prova_pagina.mjs <percorsi.json>
// dove <percorsi.json> è l'uscita di dati_prova.py. Codice d'uscita 0 se tutti
// i casi passano, 1 altrimenti; stampa un riepilogo JSON su stdout.
//
// Il modulo «playwright» si cerca in PLAYWRIGHT_MODULE, poi nei percorsi noti
// degli ambienti di sviluppo; i browser in PLAYWRIGHT_BROWSERS_PATH.
import { createRequire } from "node:module";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const candidati = [process.env.PLAYWRIGHT_MODULE, "playwright",
  "/opt/node-tools/node_modules/playwright", "/opt/npm-tools/node_modules/playwright"].filter(Boolean);
let chromium;
for (const c of candidati) {
  try { ({ chromium } = require(c)); break; } catch { /* prossimo */ }
}
if (!chromium) { console.error("playwright non trovato"); process.exit(2); }

const dati = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const pagina = pathToFileURL(path.join(path.dirname(fileURLToPath(import.meta.url)), "index.html")).href;
const esiti = [];
let falliti = 0;
function verifica(nome, condizione, dettaglio = "") {
  esiti.push({ nome, ok: !!condizione, dettaglio });
  if (!condizione) falliti++;
}

const browser = await chromium.launch();
async function nuovaPagina({ senzaEd25519 = false } = {}) {
  const contesto = await browser.newContext();
  const richieste = [];
  contesto.on("request", (r) => { if (!r.url().startsWith("file:")) richieste.push(r.url()); });
  await contesto.route((url) => !url.href.startsWith("file:"), (route) => route.abort());
  const p = await contesto.newPage();
  const errori = [];
  p.on("pageerror", (e) => errori.push(String(e)));
  if (senzaEd25519) {
    await p.addInitScript(() => {
      const originale = crypto.subtle.importKey.bind(crypto.subtle);
      crypto.subtle.importKey = (formato, chiave, algoritmo, ...resto) => {
        const nome = typeof algoritmo === "string" ? algoritmo : algoritmo?.name;
        if (nome === "Ed25519") return Promise.reject(new DOMException("Algoritmo non supportato", "NotSupportedError"));
        return originale(formato, chiave, algoritmo, ...resto);
      };
    });
  }
  await p.goto(pagina);
  await p.evaluate(() => window.VerumDocVerifica.pronto);
  return { p, contesto, richieste, errori };
}
async function compila(p, campi) {
  await p.fill("#chiave-registro", campi.chiaveRegistro ?? dati.chiave_registro);
  await p.fill("#chiave-testimone", campi.chiaveTestimone ?? dati.chiave_testimone);
  if (campi.origine !== undefined) await p.fill("#origine", campi.origine);
  for (const [id, file] of Object.entries(campi.file || {})) await p.setInputFiles(`#${id}`, file);
  await p.click("#verifica");
  await p.waitForSelector("#risultati:not([hidden])");
  const righe = await p.$$eval("#controlli tr", (tr) => tr.map((r) => ({
    nome: r.children[1].firstChild.textContent, esito: r.dataset.esito,
    dettaglio: r.querySelector(".vd-sotto")?.textContent })));
  const titolo = await p.textContent("#fascia-titolo");
  const classe = await p.getAttribute("#fascia", "class");
  return { righe, titolo, classe };
}
const esitoDi = (righe, nome) => righe.find((r) => r.nome.endsWith(nome))?.esito;

// 1. Ricevuta completa e consistenza: verificata
{
  const { p, contesto, richieste, errori } = await nuovaPagina();
  verifica("ed25519 disponibile in Chromium", await p.isHidden("#avviso-ed25519"));
  const r = await compila(p, { file: { prova: dati.prova, documento: dati.documento,
    "cp-vecchio": dati.checkpoint_vecchio, "cp-nuovo": dati.checkpoint_nuovo,
    "prova-consistenza": dati.consistenza } });
  verifica("ricevuta completa: fascia gialla «Ricevuta verificata»",
    r.titolo === "Ricevuta verificata" && r.classe.includes("vd-fascia--giallo"), JSON.stringify(r.righe));
  verifica("ricevuta completa: tutti i controlli OK", r.righe.length >= 13 && r.righe.every((x) => x.esito === "OK"),
    JSON.stringify(r.righe));
  verifica("nessuna richiesta di rete", richieste.length === 0, richieste.join(", "));
  verifica("nessun errore JavaScript", errori.length === 0, errori.join(" | "));
  // Accessibilità di base: ogni campo ha un'etichetta, la fascia è una regione viva
  const senzaEtichetta = await p.$$eval("input", (inp) => inp.filter((i) =>
    !i.labels || i.labels.length === 0).map((i) => i.id));
  verifica("ogni campo ha un'etichetta", senzaEtichetta.length === 0, senzaEtichetta.join(","));
  verifica("fascia aria-live", (await p.getAttribute("#fascia", "aria-live")) === "polite");
  await contesto.close();
}

// 2. Documento diverso: impronta fallita
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova, documento: dati.documento_altro } });
  verifica("documento diverso: non verificata", r.titolo === "Ricevuta non verificata" && r.classe.includes("vd-fascia--critico"));
  verifica("documento diverso: impronta Fallito", esitoDi(r.righe, "Impronta del file") === "Fallito");
  await contesto.close();
}

// 3. Registro riscritto: consistenza fallita, firme valide
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { "cp-vecchio": dati.checkpoint_vecchio,
    "cp-nuovo": dati.checkpoint_riscritto, "prova-consistenza": dati.consistenza_riscritta } });
  verifica("registro riscritto: consistenza Fallito", esitoDi(r.righe, "Consistenza fra i checkpoint") === "Fallito", JSON.stringify(r.righe));
  verifica("registro riscritto: firma del registro comunque valida",
    esitoDi(r.righe, "Checkpoint recente 12: firma del registro") === "OK");
  await contesto.close();
}

// 4. Checkpoint senza controfirma del testimone
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_solo_registro } });
  verifica("senza controfirma: Fallito", esitoDi(r.righe, "controfirma del testimone") === "Fallito");
  verifica("senza controfirma: non verificata", r.titolo === "Ricevuta non verificata");
  await contesto.close();
}

// 5. Browser senza Ed25519: avviso, firme «Non eseguito», mai «verificata»
{
  const { p, contesto } = await nuovaPagina({ senzaEd25519: true });
  verifica("senza Ed25519: avviso visibile", await p.isVisible("#avviso-ed25519"));
  const r = await compila(p, { file: { prova: dati.prova, documento: dati.documento } });
  verifica("senza Ed25519: firme Non eseguito", esitoDi(r.righe, "firma del registro") === "Non eseguito"
    && esitoDi(r.righe, "controfirma del testimone") === "Non eseguito", JSON.stringify(r.righe));
  verifica("senza Ed25519: inclusione comunque verificata", esitoDi(r.righe, "Inclusione nel registro") === "OK");
  verifica("senza Ed25519: non verificata", r.titolo === "Ricevuta non verificata");
  await contesto.close();
}

// 6. Ingressi mancanti: errore spiegato, nessuna eccezione
{
  const { p, contesto, errori } = await nuovaPagina();
  const r = await compila(p, { chiaveTestimone: "", file: { prova: dati.prova } });
  verifica("chiave del testimone mancante: Ingressi Fallito", esitoDi(r.righe, "Ingressi") === "Fallito");
  verifica("chiave mancante: nessun errore JS", errori.length === 0, errori.join(" | "));
  await contesto.close();
}

// 6b. Rapporto di versamento definitivo v2 (generato dalla hashchain) con il file allegato
{
  const { p, contesto, errori } = await nuovaPagina();
  const r = await compila(p, { file: { rdv: dati.rdv, documento: dati.documento_allegato } });
  verifica("RdV v2: ricevuta verificata", r.titolo === "Ricevuta verificata", JSON.stringify(r.righe));
  verifica("RdV v2: impronta del file allegato OK", esitoDi(r.righe, "allegato.txt: Impronta del file") === "OK");
  verifica("RdV v2: firme mostrate una volta", r.righe.filter((x) => x.nome.endsWith("controfirma del testimone")).length === 1);
  verifica("RdV v2: nessun errore JS", errori.length === 0, errori.join(" | "));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { rdv: dati.rdv, documento: dati.checkpoint_vecchio } });
  verifica("RdV v2 con un file estraneo: impronta Fallito", esitoDi(r.righe, "Impronta del file") === "Fallito", JSON.stringify(r.righe));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { rdv: dati.consistenza } });
  verifica("file non RdV caricato come RdV: Ingressi Fallito", esitoDi(r.righe, "Ingressi") === "Fallito", JSON.stringify(r.righe));
  await contesto.close();
}

// 6c. R04 correzioni (rev4 rilievo 9): origine attesa e chiavi distinte
{
  const { p, contesto, errori } = await nuovaPagina();
  verifica("origine attesa predefinita = produzione",
    (await p.inputValue("#origine")) === "verumdoc.it/registro/v1");
  const r = await compila(p, { file: { prova: dati.prova_sviluppo } });
  verifica("checkpoint di sviluppo: origine Fallito", esitoDi(r.righe, "origine") === "Fallito", JSON.stringify(r.righe));
  verifica("checkpoint di sviluppo: non verificata", r.titolo === "Ricevuta non verificata");
  verifica("checkpoint di sviluppo: nessun errore JS", errori.length === 0, errori.join(" | "));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { origine: "verumdoc.it/registro/v1/development",
    file: { prova: dati.prova_sviluppo } });
  verifica("origine di sviluppo dichiarata: verificata", r.titolo === "Ricevuta verificata", JSON.stringify(r.righe));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { chiaveTestimone: dati.chiave_registro, file: { prova: dati.prova_solo_registro } });
  verifica("stessa chiave come testimone: Ingressi Fallito", esitoDi(r.righe, "Ingressi") === "Fallito"
    && r.righe.some((x) => (x.dettaglio || "").includes("coincide con quella del registro")), JSON.stringify(r.righe));
  verifica("stessa chiave come testimone: non verificata", r.titolo === "Ricevuta non verificata");
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { chiaveRegistro: dati.chiave_testimone, chiaveTestimone: dati.chiave_registro,
    file: { prova: dati.prova } });
  verifica("chiavi scambiate: Ingressi Fallito", esitoDi(r.righe, "Ingressi") === "Fallito", JSON.stringify(r.righe));
  await contesto.close();
}

// 6d. R05 (contratto Q3): foglie di formato 2 e stato del documento
{
  const { p, contesto, errori } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_scartata, documento: dati.documento } });
  verifica("scartato: fascia «Documento scartato», mai gialla",
    r.titolo === "Documento scartato" && r.classe.includes("vd-fascia--rischio") && !r.classe.includes("giallo"), JSON.stringify(r));
  verifica("scartato: impronta del file comunque OK", esitoDi(r.righe, "Impronta del file") === "OK");
  verifica("scartato: stato del documento «Scartato» con data e pratica",
    esitoDi(r.righe, "Stato del documento") === "Scartato"
    && r.righe.some((x) => (x.dettaglio || "").includes("scartato il 2026-11-02T09:30:00.000000Z con pratica PS-2026-0042")), JSON.stringify(r.righe));
  verifica("scartato: foglia di scarto inclusa nel checkpoint", esitoDi(r.righe, "Ciclo di vita: scarto (foglia 14)") === "OK");
  verifica("scartato: nessun errore JS", errori.length === 0, errori.join(" | "));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_rettificata, documento: dati.documento } });
  verifica("rettificato: ricevuta verificata con la rettifica indicata", r.titolo === "Ricevuta verificata"
    && r.righe.some((x) => (x.dettaglio || "").includes("metadati rettificati il 2026-11-02T09:30:00.000000Z con pratica PR-2026-0007")), JSON.stringify(r.righe));
  await contesto.close();
}
// Revisione R05 (R4): elenco del ciclo di vita troncato dal server
{
  const { p, contesto, errori } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_troncata, documento: dati.documento } });
  verifica("troncato: mai «Ricevuta verificata», stato del documento Non eseguito",
    r.titolo === "Ricevuta non verificata" && esitoDi(r.righe, "Stato del documento") === "Non eseguito"
    && r.righe.some((x) => (x.dettaglio || "").includes("incompleto (1 di 51 foglie)")), JSON.stringify(r));
  verifica("troncato: nessun errore JS", errori.length === 0, errori.join(" | "));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_scartata_troncata, documento: dati.documento } });
  verifica("troncato con scarto: lo scarto prevale", r.titolo === "Documento scartato", JSON.stringify(r));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_totale_maggiore, documento: dati.documento } });
  verifica("totale maggiore delle foglie: non verificata", r.titolo === "Ricevuta non verificata", JSON.stringify(r));
  await contesto.close();
}
// Revisione 2 di R05 (S8): attestazione del ciclo di vita
{
  const { p, contesto, errori } = await nuovaPagina();
  let r = await compila(p, { file: { prova: dati.prova, documento: dati.documento } });
  verifica("attestazione valida: OK, ricevuta verificata",
    esitoDi(r.righe, "Attestazione del ciclo di vita") === "OK" && r.titolo === "Ricevuta verificata", JSON.stringify(r.righe));
  await contesto.close();
  const n2 = await nuovaPagina();
  r = await compila(n2.p, { file: { prova: dati.prova_scarto_tolto, documento: dati.documento } });
  verifica("scarto tolto dalla prova: mai verificata, elenco incompleto (1 di 2)",
    r.titolo === "Ricevuta non verificata" && esitoDi(r.righe, "Stato del documento") === "Non eseguito"
    && r.righe.some((x) => (x.dettaglio || "").includes("incompleto (1 di 2 foglie)")), JSON.stringify(r));
  await n2.contesto.close();
  const n3 = await nuovaPagina();
  r = await compila(n3.p, { file: { prova: dati.prova_scarto_tolto_senza_attestazione } });
  verifica("senza attestazione: assenza di scarto non dimostrabile, non verificata",
    r.titolo === "Ricevuta non verificata" && esitoDi(r.righe, "Attestazione del ciclo di vita") === "Non eseguito"
    && r.righe.some((x) => (x.dettaglio || "").includes("assenza di scarto non dimostrabile")), JSON.stringify(r));
  await n3.contesto.close();
  for (const caso of ["prova_attestazione_altrui", "prova_attestazione_falsa"]) {
    const n = await nuovaPagina();
    r = await compila(n.p, { file: { prova: dati[caso] } });
    verifica(`${caso}: attestazione Fallito`, esitoDi(r.righe, "Attestazione del ciclo di vita") === "Fallito"
      && r.titolo === "Ricevuta non verificata", JSON.stringify(r.righe));
    await n.contesto.close();
  }
  verifica("attestazione: nessun errore JS", errori.length === 0, errori.join(" | "));
}
// Revisione 3 di R05 (T2, T3): stato con checkpoint e data, checkpoint corrente, stato del PdA
{
  const { p, contesto, errori } = await nuovaPagina();
  let r = await compila(p, { file: { prova: dati.prova, documento: dati.documento } });
  const det = (righe) => (righe.find((x) => x.nome.endsWith("Stato del documento")) || {}).dettaglio || "";
  verifica("T2: «conservato» con checkpoint e data dello stato",
    r.titolo === "Ricevuta verificata" && det(r.righe).includes("stato al checkpoint 12 del 2026-10-23T08:16:30Z"), JSON.stringify(r.righe));
  await contesto.close();
  const n2 = await nuovaPagina();
  r = await compila(n2.p, { file: { prova: dati.prova, documento: dati.documento, "cp-corrente": dati.checkpoint_dopo_scarto } });
  verifica("T2: checkpoint corrente più recente → stato attuale non dimostrato, non verificata",
    r.titolo === "Ricevuta non verificata" && esitoDi(r.righe, "Stato del documento") === "Non eseguito"
    && det(r.righe).includes("il registro ha ora 15 foglie")
    && esitoDi(r.righe, "Checkpoint corrente 15: firma del registro") === "OK", JSON.stringify(r));
  await n2.contesto.close();
  const n3 = await nuovaPagina();
  r = await compila(n3.p, { file: { prova: dati.prova, "cp-corrente": dati.checkpoint_nuovo } });
  verifica("T2: checkpoint corrente uguale → verificata", r.titolo === "Ricevuta verificata"
    && det(r.righe).includes("è il checkpoint corrente indicato"), JSON.stringify(r));
  await n3.contesto.close();
  const n4 = await nuovaPagina();
  r = await compila(n4.p, { file: { prova: dati.prova_scarto_parziale, documento: dati.documento } });
  verifica("T3: PdA scartato in parte → «Documento scartato in parte», mai verificata",
    r.titolo === "Documento scartato in parte" && esitoDi(r.righe, "Stato del documento") === "Scartato in parte", JSON.stringify(r));
  await n4.contesto.close();
  const n5 = await nuovaPagina();
  r = await compila(n5.p, { file: { prova: dati.prova_in_scarto } });
  verifica("T3: PdA in scarto → «Documento scartato»",
    r.titolo === "Documento scartato" && esitoDi(r.righe, "Stato del documento") === "Scartato", JSON.stringify(r));
  await n5.contesto.close();
  verifica("rev. 3: nessun errore JS", errori.length === 0, errori.join(" | "));
}
// Revisione 4 di R05 (U3): checkpoint corrente confrontato con quello della prova
{
  const det = (righe) => (righe.find((x) => x.nome.endsWith("Stato del documento")) || {}).dettaglio || "";
  const errori = [];
  const prova = async (file) => {
    const n = await nuovaPagina();
    const r = await compila(n.p, { file });
    errori.push(...n.errori);
    await n.contesto.close();
    return r;
  };
  let r = await prova({ prova: dati.prova, "cp-corrente": dati.checkpoint_vecchio });
  verifica("U3: corrente più vecchio della prova → Fallito, mai verificata",
    r.titolo === "Ricevuta non verificata" && esitoDi(r.righe, "Checkpoint corrente rispetto alla prova") === "Fallito"
    && r.righe.some((x) => (x.dettaglio || "").includes("checkpoint fornito più vecchio della prova"))
    && esitoDi(r.righe, "Stato del documento") === "Non eseguito" && !det(r.righe).includes("è il checkpoint corrente indicato"), JSON.stringify(r));
  r = await prova({ prova: dati.prova, "cp-corrente": dati.checkpoint_riscritto });
  verifica("U3: stessa dimensione, radice diversa → REGISTRO BIFORCATO, mai verificata",
    r.titolo === "Ricevuta non verificata" && esitoDi(r.righe, "Checkpoint corrente rispetto alla prova") === "Fallito"
    && r.righe.some((x) => (x.dettaglio || "").includes("REGISTRO BIFORCATO")), JSON.stringify(r));
  r = await prova({ prova: dati.prova, "cp-corrente": dati.checkpoint_dopo_scarto, "consistenza-corrente": dati.consistenza_corrente });
  verifica("U3: corrente più recente con consistenza → nessuna riscrittura, stato non dimostrato",
    r.titolo === "Ricevuta non verificata" && esitoDi(r.righe, "Consistenza con il checkpoint corrente") === "OK"
    && esitoDi(r.righe, "Stato del documento") === "Non eseguito", JSON.stringify(r));
  r = await prova({ prova: dati.prova, "cp-corrente": dati.checkpoint_dopo_scarto, "consistenza-corrente": dati.consistenza_corrente_falsa });
  verifica("U3: consistenza falsa → Fallito (biforcato o riscritto)",
    esitoDi(r.righe, "Consistenza con il checkpoint corrente") === "Fallito"
    && r.righe.some((x) => (x.dettaglio || "").includes("REGISTRO BIFORCATO o riscritto")), JSON.stringify(r));
  verifica("rev. 4: nessun errore JS", errori.length === 0, errori.join(" | "));
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_scarto_alterato } });
  verifica("scarto alterato: inclusione Fallito, non verificata",
    esitoDi(r.righe, "Ciclo di vita: scarto (foglia 14)") === "Fallito" && r.titolo === "Ricevuta non verificata", JSON.stringify(r.righe));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_scarto_come_documento, documento: dati.documento } });
  verifica("tombstone come foglia del documento: Formato Fallito, non verificata",
    esitoDi(r.righe, "Formato della foglia del registro") === "Fallito" && r.titolo === "Ricevuta non verificata", JSON.stringify(r.righe));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { prova: dati.prova_formato_sconosciuto } });
  verifica("formato sconosciuto: non verificata", r.titolo === "Ricevuta non verificata"
    && r.righe.some((x) => (x.dettaglio || "").includes("formato di foglia sconosciuto")), JSON.stringify(r.righe));
  await contesto.close();
}
{
  const { p, contesto } = await nuovaPagina();
  const r = await compila(p, { file: { rdv: dati.rdv } });
  verifica("RdV: stato del documento «Non applicabile»", esitoDi(r.righe, "Stato del documento") === "Non applicabile", JSON.stringify(r.righe));
  await contesto.close();
}

// 7. Funzioni: SHA-256 a blocchi = WebCrypto; vettori RFC 6962 noti
{
  const { p, contesto } = await nuovaPagina();
  const sha = await p.evaluate(async () => {
    const V = window.VerumDocVerifica;
    const esiti = [];
    for (const n of [0, 1, 55, 56, 63, 64, 65, 1000, 5 * 1024 * 1024 + 3]) {
      const dati = new Uint8Array(n);
      for (let i = 0; i < n; i++) dati[i] = (i * 31 + 7) & 0xff;
      const atteso = V.hex(new Uint8Array(await crypto.subtle.digest("SHA-256", dati)));
      const s = new V.Sha256Flusso();
      s.aggiorna(dati.subarray(0, Math.floor(n / 3))); s.aggiorna(dati.subarray(Math.floor(n / 3)));
      V.config.limiteDigestIntero = 0;
      const daFile = await V.impronteFile(new Blob([dati]));
      esiti.push(V.hex(s.fine()) === atteso && daFile === atteso);
    }
    return esiti;
  });
  verifica("SHA-256 a blocchi coincide con WebCrypto (0…5 MiB)", sha.every(Boolean), JSON.stringify(sha));
  const rfc = await p.evaluate(async () => {
    const V = window.VerumDocVerifica;
    const h = V.daHex;
    const r6 = h("76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef");
    const r8 = h("5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328");
    const c68 = ["0ebc5d3437fbe2db158b9f126a1d118e308181031d0a949f8dededebc558ef6a",
      "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
      "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7"].map(h);
    const p58 = ["bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
      "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
      "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7"].map(h);
    const foglia5 = new Uint8Array(await crypto.subtle.digest("SHA-256",
      new Uint8Array([0, 0x40, 0x41, 0x42, 0x43])));
    return [await V.verificaConsistenza(6n, 8n, r6, r8, c68),
      !(await V.verificaConsistenza(6n, 8n, r6, r8, [c68[1], c68[0], c68[2]])),
      await V.verificaInclusione(foglia5, 5n, 8n, p58, r8),
      !(await V.verificaInclusione(foglia5, 4n, 8n, p58, r8)),
      V.canonico({ b: 1, a: ["x", null, true], "€": "à" }) === '{"a":["x",null,true],"b":1,"€":"à"}'];
  });
  verifica("vettori RFC 6962 (consistenza 6→8, inclusione 5 in 8) e JSON canonico", rfc.every(Boolean), JSON.stringify(rfc));
  await contesto.close();
}

// 8. Telefono (360px): nessuno scorrimento orizzontale della pagina
{
  const contesto = await browser.newContext({ viewport: { width: 360, height: 780 } });
  const p = await contesto.newPage();
  await p.goto(pagina);
  const larghezza = await p.evaluate(() => document.documentElement.scrollWidth);
  verifica("telefono 360px: nessuno scorrimento orizzontale", larghezza <= 360, String(larghezza));
  await contesto.close();
}

await browser.close();
// Su una pipe process.stdout è asincrono: process.exit() subito dopo console.log
// troncherebbe il rapporto oltre 64 KiB. Si esce solo a scrittura conclusa.
process.stdout.write(JSON.stringify({ casi: esiti.length, falliti, esiti }, null, 2) + "\n",
  () => process.exit(falliti ? 1 : 0));
