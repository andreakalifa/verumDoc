// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * R12b (VD-C-156) sulla console (backend finto): «Le mie credenziali».
 *  - password e codice a tempo: nessun comando di modifica prima della conferma dell'identità;
 *    conferma sbagliata spiegata, poi giusta; nuovi codici di recupero mostrati una volta;
 *    cambio della password con l'avviso delle altre sessioni chiuse; nuovo codice a tempo;
 *  - passkey (autenticatore del browser simulato: la crittografia vera è provata nel gateway,
 *    services/gateway/tests/test_r12b_credenziali_utente.py): conferma con la passkey,
 *    aggiunta di una seconda, revoca, l'ultima non si revoca;
 *  - ente con solo SPID/CIE: la pagina lo spiega e non offre comandi.
 */
import { expect, test, type Page } from "@playwright/test";
import { installaFinto } from "./finto";

/** WebAuthn simulato: create/get restituiscono credenziali con la forma di quelle vere. */
async function autenticatoreFinto(page: Page) {
  await page.addInitScript(() => {
    const buf = (s: string) => new TextEncoder().encode(s).buffer;
    const credenziale = (tipo: "create" | "get") => ({
      id: "Y3JlZGVuemlhbGU", rawId: buf("credenziale"), type: "public-key",
      response: tipo === "create"
        ? { clientDataJSON: buf("{}"), attestationObject: buf("attestazione") }
        : { clientDataJSON: buf("{}"), authenticatorData: buf("dati"), signature: buf("firma"), userHandle: null },
    });
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: { create: async () => credenziale("create"), get: async () => credenziale("get") },
    });
  });
}

test("password e codice a tempo: conferma, codici di recupero, password, codice a tempo", async ({ page }) => {
  const stato = await installaFinto(page, "OPERATORE");
  await page.goto("/preferenze");
  await page.getByRole("link", { name: "Le mie credenziali" }).click();
  await expect(page.getByRole("heading", { name: "Le mie credenziali" })).toBeVisible();
  await expect(page.getByText("7 di 10 ancora da usare")).toBeVisible();
  // prima della conferma nessun comando di modifica
  await expect(page.getByRole("button", { name: "Cambia la password" })).toHaveCount(0);
  await expect(page.getByText("Conferma la tua identità qui sopra per modificare.").first()).toBeVisible();
  const conferma = page.getByRole("region", { name: "Conferma la tua identità" });
  await conferma.getByLabel("Password attuale").fill("una password sbagliata");
  await conferma.getByLabel("Codice a sei cifre dell'app").fill("123456");
  await conferma.getByRole("button", { name: "Conferma con password e codice" }).click();
  await expect(conferma.getByText("Password, codice o passkey non corretti.")).toBeVisible();
  await conferma.getByLabel("Password attuale").fill("una frase lunga di prova");
  await conferma.getByRole("button", { name: "Conferma con password e codice" }).click();
  await expect(conferma.getByText("Identità confermata")).toBeVisible();
  // nuovi codici di recupero: si vedono una volta
  const codici = page.getByRole("region", { name: "Codici di recupero" });
  await codici.getByRole("button", { name: "Genera nuovi codici di recupero" }).click();
  await codici.getByRole("button", { name: "Sì, genera nuovi codici" }).click();
  await expect(codici.getByText("QWER-TYUP-ASDF-GHJK")).toBeVisible();
  await codici.getByRole("button", { name: "Li ho conservati" }).click();
  await expect(codici.getByText("QWER-TYUP-ASDF-GHJK")).toHaveCount(0);
  // cambio della password
  const pw = page.getByRole("region", { name: "Password" });
  await pw.getByLabel("Password attuale").fill("una frase lunga di prova");
  await pw.getByLabel("Nuova password", { exact: true }).fill("un'altra frase lunga di prova");
  await pw.getByLabel("Ripeti la nuova password").fill("un'altra frase diversa");
  await expect(pw.getByText("Le due password non coincidono.")).toBeVisible();
  await expect(pw.getByRole("button", { name: "Cambia la password" })).toBeDisabled();
  await pw.getByLabel("Ripeti la nuova password").fill("un'altra frase lunga di prova");
  await pw.getByRole("button", { name: "Cambia la password" }).click();
  await expect(pw.getByText("Password cambiata. Abbiamo chiuso l'altra sessione aperta.")).toBeVisible();
  const put = stato.richieste.find(r => r.metodo === "PUT" && r.percorso === "/v1/auth/credenziali/password")!.corpo as Record<string, string>;
  expect(put).toEqual({ password_attuale: "una frase lunga di prova", password_nuova: "un'altra frase lunga di prova" });
  // nuovo codice a tempo
  const totp = page.getByRole("region", { name: "Codice a tempo" });
  await totp.getByRole("button", { name: "Prepara un nuovo codice a tempo" }).click();
  await expect(totp.getByLabel("Nuova chiave del codice a tempo")).toHaveText("KRSXG5CTMVRXEZLUKN2XAZLSKNSWG4TF");
  await totp.getByLabel("Codice a sei cifre mostrato dall'app per la nuova chiave").fill("654321");
  await totp.getByRole("button", { name: "Attiva il nuovo codice a tempo" }).click();
  await expect(totp.getByText("Il codice a sei cifre non corrisponde", { exact: false })).toBeVisible();
  await totp.getByLabel("Codice a sei cifre mostrato dall'app per la nuova chiave").fill("123456");
  await totp.getByRole("button", { name: "Attiva il nuovo codice a tempo" }).click();
  await expect(totp.getByText("Nuovo codice a tempo attivo: il precedente non vale più.")).toBeVisible();
});

test("passkey: conferma, seconda passkey, revoca e mai l'ultima", async ({ page }) => {
  await autenticatoreFinto(page);
  const stato = await installaFinto(page, "OPERATORE");
  stato.credenziali = { metodi: ["PASSKEY"], confermata: false, password: false, codiciRimasti: 0,
    passkey: [{ id: "pk000000-0000-4000-8000-000000000001", nome: "Portatile dell'ufficio", creata_il: "2026-09-01T08:00:00Z", ultimo_uso: "2026-10-23T08:00:00Z" }] };
  await page.goto("/preferenze/credenziali");
  await expect(page.getByRole("region", { name: "Password" })).toHaveCount(0);   // non ammessa dall'ente
  const passkey = page.getByRole("region", { name: "Passkey" });
  await expect(passkey.getByText("Portatile dell'ufficio")).toBeVisible();
  await expect(passkey.getByRole("button", { name: "Revoca" })).toBeDisabled();
  await page.getByRole("button", { name: "Conferma con una passkey" }).click();
  await expect(page.getByText("Identità confermata")).toBeVisible();
  await expect(passkey.getByText("È l'unica credenziale con cui accedi", { exact: false })).toBeVisible();
  await passkey.getByLabel("Nome della nuova passkey").fill("Telefono personale");
  await passkey.getByRole("button", { name: "Aggiungi una passkey" }).click();
  await expect(passkey.getByText("Passkey «Telefono personale» aggiunta.")).toBeVisible();
  await expect(passkey.getByRole("row", { name: /Telefono personale/ })).toBeVisible();
  // rinomina
  await passkey.getByRole("row", { name: /Telefono personale/ }).getByRole("button", { name: "Rinomina" }).click();
  await passkey.getByLabel("Nuovo nome della passkey").fill("Telefono di riserva");
  await passkey.getByRole("button", { name: "Salva il nome" }).click();
  await expect(passkey.getByRole("row", { name: /Telefono di riserva/ })).toBeVisible();
  // revoca della prima: conferma esplicita
  const prima = passkey.getByRole("row", { name: /Portatile dell'ufficio/ });
  await prima.getByRole("button", { name: "Revoca" }).click();
  await prima.getByRole("button", { name: "Sì, revoca «Portatile dell'ufficio»" }).click();
  await expect(passkey.getByText("Passkey revocata. Abbiamo chiuso l'altra sessione aperta.")).toBeVisible();
  await expect(passkey.getByRole("row", { name: /Portatile dell'ufficio/ })).toHaveCount(0);
  // l'ultima rimasta: comando spento con il motivo
  await expect(passkey.getByRole("row", { name: /Telefono di riserva/ }).getByRole("button", { name: "Revoca" })).toBeDisabled();
  const aggiunte = stato.richieste.filter(r => r.metodo === "POST" && r.percorso === "/v1/auth/credenziali/passkey");
  expect((aggiunte[0].corpo as { nome: string }).nome).toBe("Telefono personale");
});

test("ente con solo SPID o CIE: nessun comando", async ({ page }) => {
  const stato = await installaFinto(page, "VIEWER");
  stato.credenziali = { metodi: ["SPID_CIE"], confermata: false, password: false, passkey: [], codiciRimasti: 0 };
  await page.goto("/preferenze/credenziali");
  await expect(page.getByText("L'ente ammette solo SPID o CIE", { exact: false })).toBeVisible();
  await expect(page.getByRole("button")).not.toContainText(["Conferma con password e codice"]);
  await expect(page.getByRole("region", { name: "Conferma la tua identità" })).toHaveCount(0);
});
