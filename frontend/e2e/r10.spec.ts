// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R10 sulle console (backend finto): accesso, ruoli e notifiche.
 *  - D32: campanella «Da fare» in testata e preferenze con avviso obbligatorio;
 *  - D30: ruoli personalizzati, permessi mai delegabili spenti con il motivo;
 *  - D08: inviti dell'ente, attivazione guidata (password e codice a tempo, codici di
 *    recupero), accesso con password e codice a tempo dalla pagina d'accesso,
 *    fornitori d'identità per il conservatore (nessun segreto).
 * D12 (indirizzi SFTP) è provata sul server vero: services/sftp-ingest/tests/test_r10_proxy_allowlist.py.
 */
import { expect, test } from "@playwright/test";
import { installaFinto } from "./finto";

test("campanella: da fare per te e preferenze", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  stato.campanella = {
    da_fare: [{ id: 7, categoria: "PRATICHE", categoria_nome: "Pratiche da approvare", titolo: "Un annullamento aspetta la tua approvazione come seconda persona", collegamento: "/pratiche/51a7e0aa-1111-4aaa-8aaa-0000000000c2", quando: "2026-10-23T09:00:00Z", letto: false, da_fare: true }],
    avvisi: [{ id: 8, categoria: "QUOTE", categoria_nome: "Quote vicine al limite", titolo: "I versamenti di oggi hanno raggiunto l'80% della quota", collegamento: "/overview", quando: "2026-10-23T08:00:00Z", letto: true, da_fare: false }],
    non_letti: 1, intervallo_aggiornamento_secondi: 60,
  };
  await page.goto("/overview");
  await page.getByRole("button", { name: /Da fare\s*1\s*: 1 da leggere/ }).click();
  const pannello = page.getByRole("region", { name: "Da fare per te" });
  await expect(pannello.getByText("Un annullamento aspetta la tua approvazione")).toBeVisible();
  await pannello.getByRole("button", { name: "Segna tutto come letto" }).click();
  await expect.poll(() => stato.letti ?? 0).toBe(1);
  await pannello.getByRole("link", { name: "Scegli cosa ricevere" }).click();
  await expect(page.getByRole("heading", { name: "Cosa ricevere" })).toBeVisible();
  await expect(page.getByText("Obbligatorio per l'ente")).toBeVisible();
  await page.getByRole("row", { name: /Quote vicine al limite/ }).getByRole("checkbox").nth(1).check();
  await page.getByRole("button", { name: "Salva le preferenze" }).click();
  await expect(page.getByText("Preferenze salvate.")).toBeVisible();
});

test("ruoli personalizzati: i permessi mai delegabili sono spenti", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/utenti/ruoli");
  await expect(page.getByRole("heading", { name: "Ruoli personalizzati" })).toBeVisible();
  await expect(page.getByText("Lettore che versa")).toBeVisible();
  const nuovo = page.locator("form").filter({ hasText: "Nuovo ruolo" });
  await expect(nuovo.getByLabel(/Eseguire lo scarto/)).toBeDisabled();
  await expect(nuovo.getByText("Resta ai ruoli fissi: non si delega.").first()).toBeVisible();
  await expect(nuovo.getByLabel(/Chiedere pacchetti di distribuzione/)).toBeDisabled();
  await nuovo.getByLabel("Nome del ruolo").fill("Operatore con annullamenti");
  await nuovo.getByLabel(/Chiedere l'annullamento/).check();
  await nuovo.getByRole("button", { name: "Crea il ruolo" }).click();
  await expect.poll(() => stato.richieste.filter(r => r.metodo === "POST" && r.percorso.endsWith("/ruoli")).length).toBe(1);
  const corpo = stato.richieste.find(r => r.metodo === "POST" && r.percorso.endsWith("/ruoli"))!.corpo as { permessi: string[] };
  expect(corpo.permessi).toEqual(["annullamento:richiedi"]);
});

test("inviti dell'ente e metodi d'accesso", async ({ page }) => {
  const stato = await installaFinto(page, "ADMIN_TENANT");
  await page.goto("/utenti/inviti");
  await expect(page.getByRole("heading", { level: 1, name: "Inviti e metodi d'accesso" })).toBeVisible();
  await expect(page.getByText("Ammessi per l'ente")).toBeVisible();
  await expect(page.getByText("nuova.persona@comune-immaginario.it")).toBeVisible();
  await page.getByLabel(/Indirizzo e-mail/).fill("altra.persona@comune-immaginario.it");
  await page.getByLabel(/^Nome/).fill("Altra");
  await page.getByLabel(/^Cognome/).fill("Persona");
  await page.getByLabel("Ruolo").selectOption({ label: "Lettore che versa" });
  await page.getByRole("button", { name: "Invia l'invito" }).click();
  await expect(page.getByText("Invito inviato: la persona riceverà un'e-mail.")).toBeVisible();
  const corpo = stato.richieste.find(r => r.metodo === "POST" && r.percorso.endsWith("/inviti"))!.corpo as Record<string, unknown>;
  expect(corpo.ruolo).toBe("VIEWER");
  expect(corpo.ruolo_personalizzato_id).toBe("r0000000-0000-4000-8000-000000000001");
});

test("attivazione guidata: password, codice a tempo e codici di recupero", async ({ page }) => {
  await installaFinto(page, "OPERATORE");
  await page.goto("/attiva#invito=0123456789abcdef0123456789abcdef.segreto-di-prova-abcdefghijklmnopqrstu");
  await expect(page).toHaveURL(/\/attiva$/);  // il codice esce subito dall'indirizzo
  await page.getByLabel("Indirizzo e-mail a cui hai ricevuto l'invito").fill("sbagliata@comune-immaginario.it");
  await page.getByRole("button", { name: "Continua" }).click();
  await expect(page.getByText("l'indirizzo e-mail non corrisponde", { exact: false })).toBeVisible();
  await page.getByLabel("Indirizzo e-mail a cui hai ricevuto l'invito").fill("nuova.persona@comune-immaginario.it");
  await page.getByRole("button", { name: "Continua" }).click();
  await page.getByLabel("Scegli una password").fill("una frase lunga di prova");
  await page.getByRole("button", { name: "Prepara il codice a tempo" }).click();
  await expect(page.getByLabel("Chiave del codice a tempo")).toBeVisible();
  await page.getByLabel("Codice a sei cifre mostrato dall'app").fill("123456");
  await page.getByRole("button", { name: "Attiva l'accesso" }).click();
  await expect(page.getByRole("heading", { name: "Conserva i codici di recupero" })).toBeVisible();
  await expect(page.getByText("ABCD-EFGH-JKLM-NPQR")).toBeVisible();
});

test("accesso con password e codice a tempo", async ({ page }) => {
  const stato = await installaFinto(page, "OPERATORE");
  stato.metodiAccesso = ["SPID_CIE", "PASSWORD_TOTP"];
  await page.route("**/api/v1/auth/sessione", r => r.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await page.goto("/login");
  const sezione = page.getByRole("region", { name: "Accesso senza SPID" });
  await sezione.getByLabel("Indirizzo e-mail").fill("persona@studio-prova.it");
  await sezione.getByLabel("Password").fill("sbagliata ma lunga");
  await sezione.getByRole("button", { name: "Continua" }).click();
  await expect(sezione.getByText("Indirizzo e-mail, password o codice non corretti", { exact: false })).toBeVisible();
  await sezione.getByLabel("Password").fill("una frase lunga di prova");
  await sezione.getByRole("button", { name: "Continua" }).click();
  await sezione.getByLabel("Codice a sei cifre dell'app di autenticazione").fill("123456");
  await sezione.getByRole("button", { name: "Entra" }).click();
  await expect(page).not.toHaveURL(/\/login/);
});

test("fornitori d'identità del conservatore senza segreti", async ({ page }) => {
  const stato = await installaFinto(page, "SUPER_ADMIN");
  await page.goto("/configurazione/accesso");
  await expect(page.getByRole("heading", { name: "Accesso e notifiche: metodi d'accesso" })).toBeVisible();
  await expect(page.getByText("ACCESSO_METODI_AMMESSI", { exact: false }).first()).toBeVisible();
  const spid = page.locator("form").filter({ hasText: "SPID e CIE (aggregatore)" });
  await expect(spid.getByText("Il segreto del cliente non si scrive qui", { exact: false })).toBeVisible();
  await spid.getByLabel("Indirizzo dell'aggregatore").fill("https://aggregatore.prova.example");
  await spid.getByRole("button", { name: /Salva SPID e CIE/ }).click();
  await expect(spid.getByText("Salvato.")).toBeVisible();
  const put = stato.richieste.find(r => r.metodo === "PUT" && r.percorso.includes("/fornitori/SPID_CIE"))!.corpo as { configurazione: Record<string, unknown> };
  expect(Object.keys(put.configurazione).some(k => k.includes("secret"))).toBe(false);
});
