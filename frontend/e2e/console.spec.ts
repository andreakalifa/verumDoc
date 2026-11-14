// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Flussi principali delle console R05 su backend finto:
 *  1. impostazioni → bozza → anteprima (impatto) → applica → 409 «piano cambiato» con il
 *     piano nuovo → conferma di nuovo → applicata;
 *  2. proposta di scarto: anteprima → conferma → 409 piano cambiato → conferma → pratica;
 *     poi transizione con stato atteso, e 409 «stato cambiato» gestito;
 *  3. validatore preventivo: carica un indice JSON, mostra i rilievi;
 *  4. guardie di ruolo e valori `locked` non modificabili con il motivo normativo.
 */
import { expect, test } from "@playwright/test";
import { installaFinto, ENTE } from "./finto";

test("bozza → anteprima → applica → 409 piano cambiato → applica", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione");
  await expect(page.getByRole("heading", { level: 1, name: "Impostazioni e regole" })).toBeVisible();

  // Un valore imposto dalla norma: dichiarato, con il riferimento, senza comando «Modifica»
  const rigaNorma = page.getByRole("row", { name: /Metadati minimi del documento principale/ });
  await expect(rigaNorma.getByText("Imposto dalla norma")).toBeVisible();
  await expect(rigaNorma.getByText(/Allegato 5/).first()).toBeVisible();
  await expect(rigaNorma.getByRole("button", { name: /Modifica/ })).toHaveCount(0);
  // L'origine del valore: «ereditato da: conservatore»
  await expect(page.getByRole("row", { name: /Formati \(MIME\) ammessi/ }).getByText("ereditato da: conservatore")).toBeVisible();

  // Cambia la severità di RGL-103 e salva la bozza
  await page.getByRole("button", { name: "Cambia l'effetto di RGL-103" }).click();
  await page.getByLabel("Nuovo effetto", { exact: true }).selectOption("BLOCCA");
  await page.getByRole("button", { name: "Metti nella bozza" }).click();
  await expect(page.getByRole("heading", { name: "Bozza in preparazione" })).toBeVisible();
  await expect(page.getByLabel("Livello")).toBeDisabled();
  await page.getByLabel(/^Motivo/).fill("Le fatture con metadati non concordati vanno respinte.");
  await page.getByRole("button", { name: /Salva la bozza \(1 modifica\)/ }).click();

  // Pagina della bozza: anteprima con impatto
  await expect(page).toHaveURL(/\/configurazione\/bozze\/b0220000/);
  const creata = stato.richieste.find(r => r.metodo === "POST" && r.percorso === "/v1/configurazione/bozze");
  expect(creata?.corpo).toMatchObject({ risorsa: "regole", ambito: { livello: "ENTE", ente: ENTE }, modifiche: [{ regola: "RGL-103", effetto: "BLOCCA" }] });
  await expect(page.getByText("Bozza salvata")).toBeVisible();
  await page.getByRole("button", { name: "Calcola l'anteprima" }).click();
  await expect(page.getByText(/12 versamenti/).first()).toBeVisible();
  await expect(page.getByText(/sarebbero stati rifiutati/)).toBeVisible();
  await expect(page.getByText(/Limite del campione raggiunto/)).toBeVisible();
  await expect(page.getByRole("row", { name: /RGL-103 · severità/ }).last()).toContainText("Avvisa");

  // Applica con l'impronta letta: il servizio risponde 409 con il piano nuovo
  await page.getByRole("button", { name: /Conferma e applica 1 modifica/ }).click();
  await expect(page.getByText("Il piano è cambiato")).toBeVisible();
  await expect(page.getByText(/Nulla è stato scritto/)).toBeVisible();
  await expect(page.getByRole("row", { name: /FORMATI_AMMESSI/ })).toBeVisible();
  await expect(page.getByText("Impatto calcolato sul piano precedente.")).toBeVisible();
  const prima = stato.richieste.filter(r => r.percorso.endsWith("/applica")).map(r => (r.corpo as { impronta_piano: string }).impronta_piano);
  expect(prima).toHaveLength(1);

  // Conferma del piano nuovo: applicata con l'impronta nuova
  await page.getByRole("button", { name: /Conferma e applica 2 modifiche/ }).click();
  await expect(page.getByText("Modifica applicata")).toBeVisible();
  const impronte = stato.richieste.filter(r => r.percorso.endsWith("/applica")).map(r => (r.corpo as { impronta_piano: string }).impronta_piano);
  expect(impronte).toHaveLength(2);
  expect(impronte[0]).not.toBe(impronte[1]);
});

test("proposta di scarto con anteprima e bloccati → pratica → transizione e stato cambiato", async ({ page }) => {
  const stato = await installaFinto(page, "RSC_CONSERVATORE");
  await page.goto("/pratiche/nuova");
  await page.getByRole("radio", { name: /^Scarto/ }).check();
  await page.getByLabel("Ente su cui lavori").selectOption(ENTE);
  await page.getByRole("checkbox", { name: /Fatture passive/ }).check();
  await page.getByLabel("Termine scaduto entro il").fill("2025-12-31");
  await page.getByLabel(/^Motivo/).fill("Fatture passive a fine termine decennale");
  await page.getByRole("button", { name: "Calcola l'anteprima" }).click();

  // Anteprima: inclusi, bloccati per motivo, nulla osta
  await expect(page.getByText(/1\.204 pacchetti/).first()).toBeVisible();
  await expect(page.getByRole("row", { name: /Blocco legale attivo/ })).toContainText("4");
  await expect(page.getByText(/Serve il nulla osta della Soprintendenza/)).toBeVisible();
  const anteprima = stato.richieste.find(r => r.percorso === "/v1/ciclo-vita/pratiche/scarto/proposta");
  expect(anteprima?.corpo).toMatchObject({ ente: ENTE, criteri: { classi: ["FATTURA"], anni: [], scadenza_entro: "2025-12-31" } });
  expect((anteprima?.corpo as Record<string, unknown>).impronta_piano).toBeUndefined();

  // Conferma: il piano è cambiato (409), si mostra quello nuovo
  await page.getByRole("button", { name: /Crea la pratica: 1\.204 pacchetti/ }).click();
  await expect(page.getByText("Il piano è cambiato")).toBeVisible();
  await expect(page.getByText(/1\.203 pacchetti/).first()).toBeVisible();
  await page.getByRole("button", { name: /Crea la pratica: 1\.203 pacchetti/ }).click();

  // Pratica creata in BOZZA: la corsa mostra la tappa attuale
  await expect(page).toHaveURL(/\/pratiche\/51a7e0aa-1111-4aaa-8aaa-0000000000aa/);
  await expect(page.getByRole("heading", { level: 1, name: "Scarto" })).toBeVisible();
  await expect(page.locator("tr.vd-riga-in-corso")).toContainText("Bozza");
  await expect(page.getByRole("row", { name: /Nulla osta ricevuto/ })).toBeVisible();

  // Transizione BOZZA → PROPOSTA con lo stato atteso
  await page.getByRole("button", { name: "Proponi…" }).click();
  await page.getByLabel(/^Motivo/).fill("Proposta inviata al Comune per l'autorizzazione");
  await page.getByRole("button", { name: "Conferma: proponi" }).click();
  await expect(page.getByText("Fatto: proposta")).toBeVisible();
  expect(stato.transizioni[0]).toMatchObject({ stato_atteso: "BOZZA", nuovo_stato: "PROPOSTA", allegati: [] });
  await expect(page.locator("tr.vd-riga-in-corso")).toContainText("Proposta");

  // Un altro ha agito nel frattempo: 409 LC-409-STATO-CAMBIATO
  stato.transizioneInConflitto = true;
  await page.getByRole("button", { name: "Respingi…" }).click();
  await page.getByLabel(/^Motivo/).fill("Il Comune ha ritirato l'elenco");
  await page.getByRole("button", { name: "Conferma: respingi" }).click();
  await expect(page.getByText("Lo stato della pratica è cambiato")).toBeVisible();
  await expect(page.getByText(/Adesso è «Respinta»/)).toBeVisible();
  await page.getByRole("button", { name: "Rileggi la pratica" }).click();
  await expect(page.getByText("Respinta").first()).toBeVisible();
});

test("validatore preventivo: indice JSON → rilievi", async ({ page }) => {
  await installaFinto(page, "OPERATORE");
  await page.goto("/configurazione/validatore");
  const indice = { classe_documentale: "FATTURA", metadati: { identificativo_documento: "FT-2026-001", codice_cig: "Z1234" } };
  await page.setInputFiles("#vd-val-file", { name: "indice.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(indice)) });
  await expect(page.getByText("Letto: pronto da controllare")).toBeVisible();
  await page.getByRole("button", { name: "Controlla l'indice" }).click();
  await expect(page.getByRole("heading", { name: /Indice non conforme/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /numero_fattura/ })).toContainText("Blocca");
  await expect(page.getByRole("row", { name: /codice_cig/ })).toContainText("Avvisa");
  await expect(page.getByText("Che cosa controlla solo il versamento vero")).toBeVisible();

  // Un file che non è JSON: errore locale, nessuna chiamata
  await page.setInputFiles("#vd-val-file", { name: "rotto.json", mimeType: "application/json", buffer: Buffer.from("{non json") });
  await expect(page.getByText(/non è un JSON leggibile/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Controlla l'indice" })).toBeDisabled();
});

test("guardie di ruolo: il cliente non vede le quote né il registro", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione/quote");
  await expect(page.getByText("Pagina non accessibile con il tuo profilo")).toBeVisible();
  await page.goto("/registro");
  await expect(page.getByText("Pagina non accessibile con il tuo profilo")).toBeVisible();
  await page.goto("/configurazione/proposte");
  await expect(page.getByRole("row", { name: /RGL-103/ }).getByRole("button", { name: "Accetta" })).toBeVisible();
});

test("tastiera: la bozza si apre solo con Tab e Invio", async ({ page }) => {
  await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/configurazione/bozze");
  await expect(page.getByRole("heading", { level: 1, name: "Bozze" })).toBeVisible();
  const link = page.getByRole("link", { name: "Severità delle regole" });
  for (let i = 0; i < 40 && !(await link.evaluate(el => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(link).toBeFocused();
  // Il focus si vede: contorno d'inchiostro più anello giallo (alone del sistema)
  const alone = await link.evaluate(el => getComputedStyle(el).boxShadow);
  expect(alone).toContain("255, 194, 14");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/configurazione\/bozze\/b0220000/);
});

test("autorizzazione del produttore: allegato firmato caricato in multipart e citato nella transizione", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/pratiche/51a7e0aa-1111-4aaa-8aaa-000000000001");
  await page.getByRole("button", { name: "Autorizza come produttore…" }).click();
  // Senza allegato la conferma non parte
  await page.getByLabel(/^Motivo/).fill("Autorizzazione firmata dal dirigente");
  await page.getByRole("button", { name: /Conferma: autorizza/ }).click();
  await expect(page.getByText("Scegli o carica il documento.")).toBeVisible();
  expect(stato.transizioni).toHaveLength(0);
  await page.setInputFiles("#vd-file-AUTORIZZAZIONE_PRODUTTORE", { name: "autorizzazione.pdf.p7m", mimeType: "application/pkcs7-mime", buffer: Buffer.from("finto") });
  await expect.poll(() => stato.tipoContenutoAllegato).toContain("multipart/form-data");
  await page.getByRole("button", { name: /Conferma: autorizza/ }).click();
  await expect.poll(() => stato.transizioni.length).toBe(1);
  expect(stato.transizioni[0]).toMatchObject({ stato_atteso: "PROPOSTA", nuovo_stato: "AUTORIZZATA_PRODUTTORE", allegati: ["al9"] });
});

test("webhook: i tipi di evento arrivano dal servizio, compresi quelli del ciclo di vita", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/canali/webhook");
  await expect(page.getByRole("heading", { level: 1, name: "Webhook" })).toBeVisible();
  const eventi = page.getByRole("group", { name: /Eventi da ricevere/ });
  // un tipo nuovo (R05) che la vecchia lista copiata non conosceva
  await eventi.getByRole("checkbox", { name: /versamento\.annullato/ }).check();
  await expect(eventi.getByRole("checkbox", { name: /scarto\.eseguito/ })).toBeVisible();
  expect(stato.richieste.some(r => r.metodo === "GET" && r.percorso === "/v1/webhook/tipi")).toBe(true);
  await page.getByLabel(/^Indirizzo https/).fill("https://gestionale.ente-prova.it/eventi");
  await page.getByRole("button", { name: "Crea il webhook" }).click();
  await expect.poll(() => stato.richieste.find(r => r.metodo === "POST" && r.percorso === "/v1/webhook")?.corpo)
    .toMatchObject({ tipi: ["versamento.annullato"] });
});
