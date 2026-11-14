// SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
// SPDX-License-Identifier: EUPL-1.2
/**
 * Fornitori di firma, sigillo e marca temporale (R13a): registro in onboarding
 * (/api/v1/onboarding/firma/fornitori), stato e prova inoltrati al servizio di firma.
 * I segreti non passano mai di qui: restano nel Secret del servizio di firma.
 */
import { api } from "../api/client";

export interface FornitoreFirma {
  codice: string; ruolo: "FIRMA" | "MARCA"; tipo: string; nome: string; attivo: boolean;
  priorita: number; configurazione: Record<string, unknown>; versione: number; aggiornato_il: string;
}
export interface Registro {
  items: FornitoreFirma[]; ruoli: Record<string, string[]>;
  segreti: Record<string, { nome: string; obbligatorio: boolean }[]>; sviluppo_ammesso: boolean;
}
export interface Certificato {
  raggiungibile: boolean; errore?: string; soggetto?: string; emittente?: string; valido_al?: string;
  giorni_alla_scadenza?: number; dichiara_qualificato?: boolean;
}
export interface StatoFornitore {
  codice: string; ruolo: string; tipo: string; nome: string; priorita: number; costruito: boolean;
  problemi: string[]; segreti_presenti: Record<string, boolean>; vietato_in_produzione: boolean;
  certificato?: Certificato; tsa?: { host: string | null };
}
export interface StatoFirma {
  origine: string; ultima_lettura: string | null; errore_lettura: string | null;
  fornitori: StatoFornitore[]; production_ready: boolean;
  parametri: { livello: string; algoritmo: string; algoritmi_ammessi: string[] };
}
export interface EsitoProva {
  codice: string; esito: string; durata_ms: number; ruolo: string; verificata: boolean;
  qualificata?: boolean; livello?: string; data_marca?: string | null; firmatario?: string;
  certificato_scadenza?: string | null; avvisi?: string[];
}

const BASE = "/v1/onboarding/firma/fornitori";
export const leggiRegistro = () => api.get(BASE).then(r => r.data as Registro);
export const leggiStatoFirma = () => api.get(`${BASE}/stato`).then(r => r.data as StatoFirma);
export const salvaFornitoreFirma = (codice: string, corpo: Omit<FornitoreFirma, "codice" | "aggiornato_il">) =>
  api.put(`${BASE}/${codice}`, corpo).then(r => r.data as FornitoreFirma);
export const provaFornitore = (codice: string) =>
  api.post(`${BASE}/${codice}/prova`).then(r => r.data as EsitoProva);

/** Campi della configurazione per tipo (libs/shared/firma_qualificata, stessi nomi). */
export const CAMPI_FIRMA: Record<string, { chiave: string; etichetta: string; tipo?: "testo" | "numero" | "lista" | "pem" | "si_no" | "scelta"; scelte?: string[]; aiuto?: string }[]> = {
  CSC: [
    { chiave: "url_base", etichetta: "Indirizzo CSC v2 (con /csc/v2)" },
    { chiave: "url_token", etichetta: "Indirizzo del token OAuth2" },
    { chiave: "client_id", etichetta: "Identificativo del cliente (client_id)", aiuto: "Il segreto, il PIN e l'eventuale OTP del sigillo non si scrivono qui: vanno nel Secret del servizio di firma." },
    { chiave: "credential_id", etichetta: "Credenziale (credentialID)", aiuto: "Obbligatoria in produzione. Vuoto (solo fuori produzione): si usa l'unica credenziale elencata dal prestatore." },
    { chiave: "modo_autorizzazione", etichetta: "Autorizzazione", tipo: "scelta", scelte: ["auto", "explicit", "implicit"] },
    { chiave: "firme_per_autorizzazione", etichetta: "Firme per autorizzazione (SCAL 1)", tipo: "numero" },
    { chiave: "timeout_secondi", etichetta: "Tempo massimo (secondi)", tipo: "numero" },
    { chiave: "ritentativi", etichetta: "Ritentativi", tipo: "numero" },
    { chiave: "sign_algo_oid", etichetta: "OID dell'algoritmo di firma", aiuto: "Vuoto: dedotto dalla chiave." },
    { chiave: "mtls", etichetta: "Certificato del client (mTLS)", tipo: "si_no" },
    { chiave: "pin_spki_sha256", etichetta: "Impronte attese della chiave del server (pinning)", tipo: "lista", aiuto: "SHA-256 esadecimale, una per riga; vuoto = nessun pinning. Se il Secret del servizio di firma ne fissa, valgono quelle. Gli host a cui si inviano i segreti sono fissati nel Secret: un indirizzo su un altro host rende il fornitore non utilizzabile." },
    { chiave: "radici_pem", etichetta: "Certificati radice del prestatore (PEM)", tipo: "pem", aiuto: "Obbligatori in produzione: ogni firma prodotta deve risalire a queste radici." },
    { chiave: "certificati_attesi_sha256", etichetta: "Impronte attese del certificato di firma", tipo: "lista", aiuto: "SHA-256 esadecimale del certificato DER, una per riga. In produzione serve questa o l'organizationIdentifier: una firma con un altro certificato si rifiuta." },
    { chiave: "organization_identifier", etichetta: "organizationIdentifier atteso", aiuto: "Es. VATIT-12345678901 (ETSI EN 319 412-1)." },
  ],
  PKCS11: [
    { chiave: "libreria", etichetta: "Libreria PKCS#11", aiuto: "Solo fra quelle ammesse al deploy (PKCS11_LIBRERIE_AMMESSE)." },
    { chiave: "token_label", etichetta: "Etichetta del token" },
    { chiave: "chiave_label", etichetta: "Etichetta della chiave" },
    { chiave: "certificato_pem", etichetta: "Certificato (PEM)", tipo: "pem", aiuto: "Vuoto: si legge dal token." },
    { chiave: "radici_pem", etichetta: "Certificati radice (PEM)", tipo: "pem" },
    { chiave: "certificati_attesi_sha256", etichetta: "Impronte attese del certificato di firma", tipo: "lista", aiuto: "SHA-256 esadecimale del certificato DER, una per riga. In produzione serve questa o l'organizationIdentifier: una firma con un altro certificato si rifiuta." },
    { chiave: "organization_identifier", etichetta: "organizationIdentifier atteso", aiuto: "Es. VATIT-12345678901 (ETSI EN 319 412-1)." },
  ],
  RFC3161: [
    { chiave: "url", etichetta: "Indirizzo della TSA" },
    { chiave: "politica_oid", etichetta: "Politica attesa (OID)" },
    { chiave: "tolleranza_secondi", etichetta: "Tolleranza sull'orario (secondi)", tipo: "numero" },
    { chiave: "timeout_secondi", etichetta: "Tempo massimo (secondi)", tipo: "numero" },
    { chiave: "ritentativi", etichetta: "Ritentativi", tipo: "numero" },
    { chiave: "certificato_sha256", etichetta: "Impronta attesa del certificato della TSA" },
    { chiave: "radici_pem", etichetta: "Certificati radice della TSA (PEM)", tipo: "pem" },
  ],
  SVILUPPO: [],
};
