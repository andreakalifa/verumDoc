# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/parametri_servizio.py — parametri del servizio portati in
console (R07; decisioni D13-D19 del registro, REGOLE_SVILUPPO §8.1, §8.2, §9.9).

Valori prima fissi nel codice o nelle variabili d'ambiente dei servizi, ora
impostazioni del motore: livelli conservatore → ente (dove ammesso), con bozza,
anteprima, applicazione, storico e decorrenza programmata (D28) della console.

  Gruppo «quote» (solo conservatore, D13): limiti dei piani
    QUOTA_PIANO_<PIANO>_PDV_GIORNO / _RICHIESTE_MINUTO   valori R03 come modello
  Gruppo «consumi» (D13):
    QUOTA_SUPERAMENTO            RIFIUTA | RALLENTA  ente: oltre la quota giornaliera
                                                     rifiuta (429) o rallenta
    QUOTA_RALLENTAMENTO_SECONDI  30 (1-3600)         conservatore: oltre la quota, con
                                                     RALLENTA, un PdV ogni N secondi
  Gruppo «sessione» (D14): SESSIONE_INATTIVITA_MINUTI_<RUOLO>, 15 per SUPER_ADMIN,
    RSC_CONSERVATORE e ADMIN_TENANT, 30 per gli altri; 5-120 (W5; massimo dal
    vincolo della migrazione 045); il conservatore fissa il massimo, l'ente può
    solo abbassare
  Gruppo «staging» (D15): STAGING_SCARTATI_GIORNI 90 (30-180) per ente, l'ente
    può solo ridurre
  Gruppo «webhook» (D16): WEBHOOK_PORTE_AGGIUNTIVE, porte oltre la 443 ammesse per
    l'ente; solo il conservatore, con il motivo della bozza
  Gruppo «restituzione» (D18): PDD_VALIDITA_ORE 72 (1-720)
  Gruppo «audit» (D19): AUDIT_EXPORT_PERIODO_MINUTI 15 (5-60), solo conservatore

Revisione W1 — verso «più sicuro» dichiarato per OGNI voce (``verso_sicuro``):
  MINORE: piani, inattività, staging, validità PdD, periodo dell'audit;
  MAGGIORE: rallentamento; ORDINE_SCELTE: superamento (RIFIUTA prima di
  RALLENTA); SOTTOINSIEME: porte dei webhook. Sulle voci che l'ente può
  personalizzare (inattività, staging, validità PdD, superamento) il motore lo
  fa valere: l'ente va solo nel verso sicuro rispetto al tetto, cioè al limite
  che il conservatore può fissare per quell'ente (``LIMITE.<codice>``, generato
  dal registro, riservato) o, senza limite, al valore del conservatore.

R08 (D22, D01) — gruppo «firme» del ciclo di vita, ORDINE_SCELTE (la prima
scelta è la più sicura; l'ente può solo andare verso quella):
  NULLA_OSTA_FIRMA               BLOCCANTE | AVVISO (predefinito, decisione D22: firma
                                 richiesta, con avviso se manca; il conservatore può
                                 renderla bloccante per tutti o per un ente)
  CICLO_VITA_FIRME_LIVELLO       CON_REVOCHE | CRITTOGRAFICA (predefinito): livello con
                                 cui il lavoratore verifica autorizzazione allo scarto,
                                 nulla osta e richiesta di annullamento firmata
  ANNULLAMENTO_RICHIESTA_FIRMATA OBBLIGATORIA | FACOLTATIVA (predefinito, comportamento
                                 di prima): richiesta di annullamento con documento firmato

Revisione R08 (X3, decisione VD-P-76) — firmatari autorizzati, CODICI riservati
al conservatore (CONSERVATORE per tutti, ENTE per un ente; codice fiscale di 16
caratteri o impronta SHA-256 del certificato, 64 caratteri esadecimali):
  CICLO_VITA_FIRMATARI_AUTORIZZATI  chi può firmare l'autorizzazione allo scarto e la
                                    richiesta di annullamento dell'ente
  CICLO_VITA_FIRMATARI_NULLA_OSTA   chi può firmare il nulla osta (Soprintendenza)
Elenco vuoto = nessun firmatario ammesso: un'autorizzazione o una richiesta
firmata si rifiuta finché il conservatore non configura l'elenco. Per il nulla
osta il firmatario non autorizzato segue ``NULLA_OSTA_FIRMA`` (BLOCCANTE:
rifiuto; AVVISO: accettato con avviso, come il nulla osta non firmato).

R09 (ciclo di vita automatico; decisioni D04, D05, D06, D20, D21, D23):
  Gruppo «scarto» (D21, D20, D06):
    SCARTO_PROPOSTA_AUTOMATICA      ATTIVA | DISATTIVA (predefinito, §8.14): proposta di
                                    scarto periodica in bozza per l'ente; ORDINE_SCELTE
                                    (ATTIVA è il verso sicuro: l'ente la può attivare)
    SCARTO_PROPOSTA_FREQUENZA_GIORNI 30 (7-365), MINORE: ogni quanti giorni
    SCARTO_PROPOSTA_CLASSI_ESCLUSE  codici di classe esclusi dalla proposta; riservata
                                    al conservatore (SOTTOINSIEME), anche per ente
    SCARTO_TENTATIVI_PDA            5 (1-100): tentativi per PdA con errori dello storage
    SCARTO_RIPRESE_AUTOMATICHE      3 (0-20): riprese automatiche al più per pratica
                                    quando il blocco (legal hold) cessa
    SCARTO_MINIMIZZAZIONE_GIORNI    30 (1-365): dopo lo scarto restano i soli dati
                                    dell'elenco minimo; il resto si cancella dopo N giorni
  Gruppo «annullamento» (D23), CODICI riservati al conservatore (anche per ente):
    ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE  ruoli ammessi prima della conservazione
    ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE   ruoli ammessi dopo (vuoto = non ammesso)
    ANNULLAMENTO_RUOLI_APPROVAZIONE         ruoli che approvano (seconda persona): solo
                                            ADMIN_TENANT (revisione R09, Z1, VD-P-90)
    ANNULLAMENTO_DOPPIA_APPROVAZIONE        SEMPRE | DOPO_CONSERVAZIONE (predefinito) |
                                            MAI; ORDINE_SCELTE: l'ente può solo inasprire
  Gruppo «retention» (D04), solo conservatore, MAGGIORE:
    RETENTION_PERMANENTE_ANNI       30 (5-100): orizzonte Object Lock delle classi
                                    permanenti e di allegati, verbali e atti
    RETENTION_RINNOVO_SOGLIA_ANNI   5 (1-30): rinnovo quando mancano meno di N anni
  Gruppo «verifiche» (D05):
    VERIFICA_CICLO_GIORNI           365 (7-365), MINORE: conservatore, ente e CLASSE;
                                    l'ente può solo ridurlo (verifica più frequente),
                                    il conservatore fissa il tetto (anche LIMITE per ente)
    VERIFICA_CAMPIONE_INCIDENTE_PDA 200 (10-1000), MAGGIORE, solo conservatore: PdA
                                    della verifica a campione dopo un incidente di storage
  Gruppo «audit» (D27), solo conservatore:
    AUDIT_VERIFICA_FINESTRA_GIORNI  2 (1-31): finestra della verifica giornaliera incrementale
    AUDIT_VERIFICA_COMPLETA_GIORNO  1 (1-28): giorno del mese della verifica completa

R10 (accesso, ruoli e notifiche; decisioni D08, D12, D30, D32):
  Gruppo «accesso» (D08):
    ACCESSO_METODI_AMMESSI          SPID_CIE | PASSKEY | PASSWORD_TOTP; predefinito SPID_CIE
                                    (comportamento di prima, §8.14). SOTTOINSIEME: il
                                    conservatore abilita i metodi (anche LIMITE per ente),
                                    l'ente sceglie fra quelli (solo meno metodi)
    INVITO_VALIDITA_ORE             72 (1-336), MINORE: validità del collegamento d'invito
    ACCESSO_TENTATIVI_MASSIMI       5 (3-20), MINORE, conservatore: tentativi falliti prima
                                    del blocco temporaneo dell'account locale
    ACCESSO_BLOCCO_MINUTI           15 (1-1440), MAGGIORE, conservatore: durata del blocco
    PASSWORD_LUNGHEZZA_MINIMA       12 (12-64), MAGGIORE: l'ente può solo alzarla
  Gruppo «ruoli» (D30):
    RUOLI_PERMESSI_CONCEDIBILI      permessi che l'ente può mettere nei ruoli personalizzati;
                                    riservata al conservatore (anche per ente), SOTTOINSIEME;
                                    i permessi mai delegabili non sono nemmeno ammessi dal
                                    formato (``libs.verumdoc_rules.permessi``)
  Gruppo «notifiche» (D32):
    NOTIFICHE_OBBLIGATORIE          avvisi che l'utente non può spegnere, «CATEGORIA.CANALE»
                                    (CONSOLE o EMAIL); SOVRAINSIEME: l'ente può solo aggiungerne
  Gruppo «sftp» (D12):
    SFTP_ALLOWLIST_VOCI_MASSIME     20 (1-500), conservatore: indirizzi o reti per account
    SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4 / _IPV6  16 / 48, conservatore: reti non più ampie
    SFTP_ALLOWLIST_OBBLIGATORIA     OBBLIGATORIA | FACOLTATIVA (predefinito): senza elenco
                                    l'account non si collega; l'ente può solo renderla
                                    obbligatoria

La fine della transizione dell'idempotenza (D17) resta nel catalogo del
versamento (``IDEMPOTENZA_TRANSIZIONE_FINO_AL``), riservata al conservatore.

R13a (firma e marca qualificate predisposte; VD-C-068, VD-C-124), gruppo
«firma_qualificata», solo conservatore e riservate. I FORNITORI (CSC v2, PKCS#11,
TSA RFC 3161, sviluppo) si registrano nella tabella ``fornitori_firma`` dalla
console (pagina «Fornitori di firma e marca»), con priorità e ripiego; qui le
scelte di servizio:
    FIRMA_SENZA_FORNITORE           BLOCCA | NON_FIRMATO (predefinito, comportamento di
                                    prima, VD-P-26): senza fornitore qualificato i
                                    documenti del conservatore restano DA_FIRMARE e il
                                    PdA non va a CONSERVATO (BLOCCA, fail-closed) oppure
                                    si dichiarano NON_FIRMATO con allarme
    FIRMA_LIVELLO_DOCUMENTI         B-LTA | B-LT (predefinito) | B-T: livello ETSI delle
                                    firme di IPdA, RdV, manuale e piano
    FIRMA_ALGORITMO_IMPRONTA        sha512 | sha384 | sha256 (predefinito)
    FIRMA_ALGORITMI_AMMESSI         algoritmi d'impronta ancora sicuri; una firma o una
                                    marca con un algoritmo tolto si rimarca (SOTTOINSIEME)
    FIRMA_RINNOVO_PREAVVISO_GIORNI  90 (30-730), MAGGIORE: rimarcatura prima della
                                    scadenza dei certificati di firma o della TSA
    CATENA_MARCA_INTERVALLO_MINUTI  60 (1-1440), MINORE: marca dell'ultimo checkpoint
                                    (ancoraggio esterno) almeno ogni N minuti
    ATTESTAZIONE_SIGILLO            SEMPRE | SU_RICHIESTA | DISATTIVO (predefinito):
                                    sigillo qualificato sull'attestazione del ciclo di vita

R13b (canali esterni predisposti; VD-C-078, VD-C-162):
  Gruppo «pec»:
    PEC_CASI_USO                    SCARTO | RESTITUZIONE | RIVERSAMENTO | SCADENZE: casi
                                    d'uso inviati ANCHE per PEC all'ente; predefinito
                                    nessuno (§8.14); SOVRAINSIEME: l'ente può solo
                                    aggiungerne
    PEC_CONSEGNA_ATTESA_ORE         24 (1-72), MINORE, conservatore: oltre, un messaggio
                                    accettato senza consegna è «consegna non confermata»
  Gruppo «riversamento»:
    RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE  RICHIESTA (predefinito da R17, VD-P-155)
                                    | NON_RICHIESTA; riservata al conservatore anche al
                                    livello ENTE: la deroga per un singolo ente la concede
                                    SOLO il conservatore (l'ente non la toglie a sé);
                                    fotografata alla creazione della pratica

Pacchetto puro: chi legge i valori dal database è ``libs.shared.parametri``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from libs.verumdoc_rules.impostazioni import (
    Impostazione,
    Livello,
    RegistroImpostazioni,
    TipoImpostazione,
    VersoSicuro,
)
from libs.verumdoc_rules.permessi import CONCEDIBILI_POSSIBILI, FORMATO_CONCEDIBILE

_SOLO_CONSERVATORE = frozenset({Livello.CONSERVATORE})
_CONSERVATORE_ENTE = frozenset({Livello.CONSERVATORE, Livello.ENTE})

#: Piani e valori iniziali (R03 i3, decisione D13: modello iniziale modificabile).
PIANI: tuple[str, ...] = ("starter", "business", "enterprise")
PIANI_INIZIALI: dict[str, tuple[int, int]] = {   # piano → (PdV al giorno, richieste al minuto)
    "starter": (1_000, 120),
    "business": (20_000, 600),
    "enterprise": (200_000, 6_000),
}


def codice_piano_pdv(piano: str) -> str:
    return f"QUOTA_PIANO_{piano.upper()}_PDV_GIORNO"


def codice_piano_richieste(piano: str) -> str:
    return f"QUOTA_PIANO_{piano.upper()}_RICHIESTE_MINUTO"


QUOTA_SUPERAMENTO = "QUOTA_SUPERAMENTO"
QUOTA_RALLENTAMENTO_SECONDI = "QUOTA_RALLENTAMENTO_SECONDI"
SUPERAMENTO_RIFIUTA = "RIFIUTA"
SUPERAMENTO_RALLENTA = "RALLENTA"
#: Soglie degli avvisi automatici sulla quota giornaliera (D13), in percentuale.
SOGLIE_AVVISO_QUOTA: tuple[int, ...] = (80, 100)

#: Ruoli del portale e inattività predefinita in minuti (D14).
INATTIVITA_PREDEFINITA: dict[str, int] = {
    "SUPER_ADMIN": 15,
    "RSC_CONSERVATORE": 15,
    "ADMIN_TENANT": 15,
    "OPERATORE": 30,
    "VIEWER": 30,
}
#: Ruolo non elencato (es. un ruolo nuovo): il limite più breve, a favore della sicurezza.
INATTIVITA_RUOLO_SCONOSCIUTO = "SUPER_ADMIN"
#: Vincolo ``ck_sessioni_attive_inattivita`` della migrazione 045 (≤ 2 ore).
INATTIVITA_MASSIMA_MINUTI = 120
#: Revisione W5: almeno 5 minuti, così l'avviso (2 minuti prima) non resta sempre aperto.
INATTIVITA_MINIMA_MINUTI = 5


def codice_inattivita(ruolo: str) -> str:
    return f"SESSIONE_INATTIVITA_MINUTI_{ruolo}"


STAGING_SCARTATI_GIORNI = "STAGING_SCARTATI_GIORNI"
WEBHOOK_PORTE_AGGIUNTIVE = "WEBHOOK_PORTE_AGGIUNTIVE"
#: Porta sempre ammessa per i webhook (D16).
PORTA_WEBHOOK_SEMPRE = 443
PDD_VALIDITA_ORE = "PDD_VALIDITA_ORE"
AUDIT_EXPORT_PERIODO_MINUTI = "AUDIT_EXPORT_PERIODO_MINUTI"
NULLA_OSTA_FIRMA = "NULLA_OSTA_FIRMA"
CICLO_VITA_FIRME_LIVELLO = "CICLO_VITA_FIRME_LIVELLO"
ANNULLAMENTO_RICHIESTA_FIRMATA = "ANNULLAMENTO_RICHIESTA_FIRMATA"
CICLO_VITA_FIRMATARI_AUTORIZZATI = "CICLO_VITA_FIRMATARI_AUTORIZZATI"
CICLO_VITA_FIRMATARI_NULLA_OSTA = "CICLO_VITA_FIRMATARI_NULLA_OSTA"
#: Codice fiscale (16 caratteri maiuscoli) o impronta SHA-256 del certificato.
_FIRMATARIO = r"(?:[A-Z0-9]{16}|[0-9a-f]{64})"
FIRMA_BLOCCANTE, FIRMA_AVVISO = "BLOCCANTE", "AVVISO"
RICHIESTA_OBBLIGATORIA, RICHIESTA_FACOLTATIVA = "OBBLIGATORIA", "FACOLTATIVA"
_CICLO_VITA_FIRME = "ciclo-vita (lavoratore: autorizzazioni, nulla osta, annullamento)"

# ── R09 (ciclo di vita automatico) ─────────────────────────────────────
SCARTO_PROPOSTA_AUTOMATICA = "SCARTO_PROPOSTA_AUTOMATICA"
SCARTO_PROPOSTA_FREQUENZA_GIORNI = "SCARTO_PROPOSTA_FREQUENZA_GIORNI"
SCARTO_PROPOSTA_CLASSI_ESCLUSE = "SCARTO_PROPOSTA_CLASSI_ESCLUSE"
SCARTO_TENTATIVI_PDA = "SCARTO_TENTATIVI_PDA"
SCARTO_RIPRESE_AUTOMATICHE = "SCARTO_RIPRESE_AUTOMATICHE"
SCARTO_MINIMIZZAZIONE_GIORNI = "SCARTO_MINIMIZZAZIONE_GIORNI"
PROPOSTA_ATTIVA, PROPOSTA_DISATTIVA = "ATTIVA", "DISATTIVA"
ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE = "ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE"
ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE = "ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE"
ANNULLAMENTO_RUOLI_APPROVAZIONE = "ANNULLAMENTO_RUOLI_APPROVAZIONE"
ANNULLAMENTO_DOPPIA_APPROVAZIONE = "ANNULLAMENTO_DOPPIA_APPROVAZIONE"
APPROVAZIONE_SEMPRE = "SEMPRE"
APPROVAZIONE_DOPO_CONSERVAZIONE = "DOPO_CONSERVAZIONE"
APPROVAZIONE_MAI = "MAI"
RETENTION_PERMANENTE_ANNI = "RETENTION_PERMANENTE_ANNI"
RETENTION_RINNOVO_SOGLIA_ANNI = "RETENTION_RINNOVO_SOGLIA_ANNI"
VERIFICA_CICLO_GIORNI = "VERIFICA_CICLO_GIORNI"
VERIFICA_CAMPIONE_INCIDENTE_PDA = "VERIFICA_CAMPIONE_INCIDENTE_PDA"
AUDIT_VERIFICA_FINESTRA_GIORNI = "AUDIT_VERIFICA_FINESTRA_GIORNI"
AUDIT_VERIFICA_COMPLETA_GIORNO = "AUDIT_VERIFICA_COMPLETA_GIORNO"
#: Ruoli dell'ente che possono annullare o approvare (mai quelli del conservatore)
_RUOLO_ENTE = r"(?:ADMIN_TENANT|OPERATORE|API_SYSTEM)"
#: Codice di una classe documentale (come in console: lettere, cifre, «_», «-», «.»)
_CODICE_CLASSE = r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,99}"
_CICLO_VITA_LAVORATORE = "ciclo-vita (lavoro periodico del lavoratore)"
_CICLO_VITA_ANNULLAMENTO = "ciclo-vita (annullamento: API e lavoratore)"
_RETENTION = "aip-builder, hashchain, ciclo-vita (lavoratore: rinnovo)"
_SCARTO = "scarto"

#: Porta TCP 1-65535 scritta in decimale senza zeri iniziali.
_PORTA = (r"(?:[1-9]\d{0,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])")

_QUOTE_CONSERVATORE = "libs/shared/security/quote (tutti i servizi)"

# ── R10 (accesso, ruoli e notifiche) ───────────────────────────────────
ACCESSO_METODI_AMMESSI = "ACCESSO_METODI_AMMESSI"
INVITO_VALIDITA_ORE = "INVITO_VALIDITA_ORE"
ACCESSO_TENTATIVI_MASSIMI = "ACCESSO_TENTATIVI_MASSIMI"
ACCESSO_BLOCCO_MINUTI = "ACCESSO_BLOCCO_MINUTI"
PASSWORD_LUNGHEZZA_MINIMA = "PASSWORD_LUNGHEZZA_MINIMA"
#: R12b (VD-C-156): finestra della riautenticazione recente per le modifiche delle
#: proprie credenziali (minuti)
ACCESSO_RIAUTENTICAZIONE_MINUTI = "ACCESSO_RIAUTENTICAZIONE_MINUTI"
RUOLI_PERMESSI_CONCEDIBILI = "RUOLI_PERMESSI_CONCEDIBILI"
NOTIFICHE_OBBLIGATORIE = "NOTIFICHE_OBBLIGATORIE"
SFTP_ALLOWLIST_VOCI_MASSIME = "SFTP_ALLOWLIST_VOCI_MASSIME"
SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4 = "SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4"
SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6 = "SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6"
SFTP_ALLOWLIST_OBBLIGATORIA = "SFTP_ALLOWLIST_OBBLIGATORIA"
METODO_SPID_CIE, METODO_PASSKEY, METODO_PASSWORD_TOTP = "SPID_CIE", "PASSKEY", "PASSWORD_TOTP"
METODI_ACCESSO: tuple[str, ...] = (METODO_SPID_CIE, METODO_PASSKEY, METODO_PASSWORD_TOTP)
#: Categorie della campanella (D32), nell'ordine della console
CATEGORIE_NOTIFICHE: tuple[str, ...] = ("PRATICHE", "PROPOSTE_SCARTO", "ANNULLAMENTI_RESPINTI",
                                        "QUOTE", "SCADENZE_STAGING", "ALLARMI")
CANALI_NOTIFICHE: tuple[str, ...] = ("CONSOLE", "EMAIL")
ALLOWLIST_OBBLIGATORIA, ALLOWLIST_FACOLTATIVA = "OBBLIGATORIA", "FACOLTATIVA"
_ACCESSO = "gateway (accesso), onboarding (inviti)"
_SFTP = "sftp-ingest (accesso), onboarding (elenco degli indirizzi)"

# ── R13a (firma e marca qualificate) ───────────────────────────────────
FIRMA_SENZA_FORNITORE = "FIRMA_SENZA_FORNITORE"
FIRMA_LIVELLO_DOCUMENTI = "FIRMA_LIVELLO_DOCUMENTI"
FIRMA_ALGORITMO_IMPRONTA = "FIRMA_ALGORITMO_IMPRONTA"
FIRMA_ALGORITMI_AMMESSI = "FIRMA_ALGORITMI_AMMESSI"
FIRMA_RINNOVO_PREAVVISO_GIORNI = "FIRMA_RINNOVO_PREAVVISO_GIORNI"
FIRMA_TENTATIVI_MASSIMI = "FIRMA_TENTATIVI_MASSIMI"
CATENA_MARCA_INTERVALLO_MINUTI = "CATENA_MARCA_INTERVALLO_MINUTI"
ATTESTAZIONE_SIGILLO = "ATTESTAZIONE_SIGILLO"
SENZA_FORNITORE_BLOCCA, SENZA_FORNITORE_NON_FIRMATO = "BLOCCA", "NON_FIRMATO"
LIVELLI_FIRMA: tuple[str, ...] = ("B-LTA", "B-LT", "B-T")
ALGORITMI_FIRMA: tuple[str, ...] = ("sha512", "sha384", "sha256")
ATTESTAZIONE_SEMPRE, ATTESTAZIONE_SU_RICHIESTA, ATTESTAZIONE_DISATTIVO = (
    "SEMPRE", "SU_RICHIESTA", "DISATTIVO")
_FIRMA_QUALIFICATA = "firma_qualificata"

# ── R13b (canali esterni predisposti) ──────────────────────────────────
PEC_CASI_USO = "PEC_CASI_USO"
PEC_CONSEGNA_ATTESA_ORE = "PEC_CONSEGNA_ATTESA_ORE"
RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE = "RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE"
#: Casi d'uso della PEC (comunicazioni formali all'ente), nell'ordine della console
CASI_USO_PEC: tuple[str, ...] = ("SCARTO", "RESTITUZIONE", "RIVERSAMENTO", "SCADENZE")
AUTORIZZAZIONE_RICHIESTA, AUTORIZZAZIONE_NON_RICHIESTA = "RICHIESTA", "NON_RICHIESTA"

# ── R14 (debito interno) ───────────────────────────────────────────────
NOTIFICHE_PERSONALI_GIORNI = "NOTIFICHE_PERSONALI_GIORNI"


def _voci_r13a() -> list[Impostazione]:
    return [
        Impostazione(
            FIRMA_SENZA_FORNITORE, TipoImpostazione.SCELTA,
            predefinito=SENZA_FORNITORE_NON_FIRMATO,
            descrizione=("Senza un fornitore qualificato di firma: BLOCCA (i documenti del "
                         "conservatore restano da firmare, il PdA non va a CONSERVATO e scatta "
                         "l'allarme) o NON_FIRMATO (si dichiarano non firmati, con allarme, e "
                         "si firmano appena il fornitore è attivo)"),
            norm_ref="Linee guida AgID 2021, §4.7 e Allegato 4 (indice e rapporto firmati)",
            scelte=(SENZA_FORNITORE_BLOCCA, SENZA_FORNITORE_NON_FIRMATO),
            ambito_ammesso=_SOLO_CONSERVATORE, riservata_conservatore=True,
            applicata_da="hashchain (documenti del conservatore, conservazione)",
            gruppo=_FIRMA_QUALIFICATA, verso_sicuro=VersoSicuro.ORDINE_SCELTE),
        Impostazione(
            FIRMA_LIVELLO_DOCUMENTI, TipoImpostazione.SCELTA, predefinito="B-LT",
            descrizione=("Livello ETSI delle firme del conservatore (IPdA, RdV, manuale, piano): "
                         "B-LTA (con marca d'archivio), B-LT (con certificati e revoche), "
                         "B-T (solo marca)"),
            norm_ref="ETSI EN 319 122-1 (CAdES), EN 319 142-1 (PAdES)",
            scelte=LIVELLI_FIRMA, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="signature (firma dei documenti)",
            gruppo=_FIRMA_QUALIFICATA, verso_sicuro=VersoSicuro.ORDINE_SCELTE),
        Impostazione(
            FIRMA_ALGORITMO_IMPRONTA, TipoImpostazione.SCELTA, predefinito="sha256",
            descrizione="Algoritmo d'impronta delle firme e delle marche del conservatore",
            norm_ref="ETSI TS 119 312 (algoritmi crittografici)",
            scelte=ALGORITMI_FIRMA, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="signature (firma e marca)",
            gruppo=_FIRMA_QUALIFICATA, verso_sicuro=VersoSicuro.ORDINE_SCELTE),
        Impostazione(
            FIRMA_ALGORITMI_AMMESSI, TipoImpostazione.CODICI,
            predefinito=("sha256", "sha384", "sha512"),
            descrizione=("Algoritmi d'impronta ancora considerati sicuri: le firme e le marche "
                         "fatte con un algoritmo tolto da qui si rimarcano"),
            norm_ref="ETSI TS 119 312 (algoritmi crittografici)",
            ambito_ammesso=_SOLO_CONSERVATORE, riservata_conservatore=True,
            applicata_da="hashchain (rimarcatura), signature", gruppo=_FIRMA_QUALIFICATA,
            formato_codice="(?:sha256|sha384|sha512)", verso_sicuro=VersoSicuro.SOTTOINSIEME),
        Impostazione(
            FIRMA_RINNOVO_PREAVVISO_GIORNI, TipoImpostazione.INTERO, predefinito=90,
            descrizione=("Giorni prima della scadenza del certificato di firma o della TSA in "
                         "cui i documenti firmati si rimarcano"),
            minimo=30, massimo=730, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="hashchain (rimarcatura)",
            gruppo=_FIRMA_QUALIFICATA, verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            FIRMA_TENTATIVI_MASSIMI, TipoImpostazione.INTERO, predefinito=10,
            descrizione=("Errori dopo i quali un documento da sigillare va in ERRORE (allarme, "
                         "nessun nuovo tentativo né firma pagata finché un operatore non lo "
                         "rimette DA_FIRMARE)"),
            minimo=1, massimo=100, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="hashchain (sigillo dei documenti)",
            gruppo=_FIRMA_QUALIFICATA, verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            CATENA_MARCA_INTERVALLO_MINUTI, TipoImpostazione.INTERO, predefinito=60,
            descrizione=("Minuti al più fra due marche temporali dell'ultimo checkpoint del "
                         "registro (ancoraggio esterno della catena)"),
            minimo=1, massimo=1440, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="hashchain (checkpoint)",
            gruppo=_FIRMA_QUALIFICATA, verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            ATTESTAZIONE_SIGILLO, TipoImpostazione.SCELTA, predefinito=ATTESTAZIONE_DISATTIVO,
            descrizione=("Sigillo qualificato sull'attestazione del ciclo di vita nelle prove "
                         "d'inclusione: SEMPRE, SU_RICHIESTA (parametro «sigillo») o DISATTIVO"),
            scelte=(ATTESTAZIONE_SEMPRE, ATTESTAZIONE_SU_RICHIESTA, ATTESTAZIONE_DISATTIVO),
            ambito_ammesso=_SOLO_CONSERVATORE, riservata_conservatore=True,
            applicata_da="hashchain (prove d'inclusione)", gruppo=_FIRMA_QUALIFICATA,
            verso_sicuro=VersoSicuro.ORDINE_SCELTE),
    ]


def _voci_r10() -> list[Impostazione]:
    return [
        Impostazione(
            ACCESSO_METODI_AMMESSI, TipoImpostazione.CODICI, predefinito=(METODO_SPID_CIE,),
            descrizione=("Metodi con cui le persone accedono: SPID_CIE (tramite aggregatore), "
                         "PASSKEY, PASSWORD_TOTP (password più codice a tempo). Il conservatore "
                         "li abilita, l'ente sceglie fra quelli abilitati"),
            norm_ref="CAD, art. 64 (SPID e CIE per l'accesso ai servizi in rete)",
            ambito_ammesso=_CONSERVATORE_ENTE, applicata_da=_ACCESSO, gruppo="accesso",
            formato_codice="(?:" + "|".join(METODI_ACCESSO) + ")",
            verso_sicuro=VersoSicuro.SOTTOINSIEME),
        Impostazione(
            INVITO_VALIDITA_ORE, TipoImpostazione.INTERO, predefinito=72,
            descrizione="Ore di validità del collegamento d'invito inviato per e-mail",
            minimo=1, massimo=336, ambito_ammesso=_CONSERVATORE_ENTE, applicata_da=_ACCESSO,
            gruppo="accesso", verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            ACCESSO_TENTATIVI_MASSIMI, TipoImpostazione.INTERO, predefinito=5,
            descrizione=("Tentativi sbagliati (password, codice a tempo o d'invito) dopo cui "
                         "l'accesso si blocca per un po'"),
            minimo=3, massimo=20, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_ACCESSO, gruppo="accesso",
            verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            ACCESSO_BLOCCO_MINUTI, TipoImpostazione.INTERO, predefinito=15,
            descrizione="Minuti di blocco dopo troppi tentativi sbagliati",
            minimo=1, massimo=1440, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_ACCESSO, gruppo="accesso",
            verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            PASSWORD_LUNGHEZZA_MINIMA, TipoImpostazione.INTERO, predefinito=12,
            descrizione="Lunghezza minima della password (accesso con password e codice a tempo)",
            minimo=12, massimo=64, ambito_ammesso=_CONSERVATORE_ENTE, applicata_da=_ACCESSO,
            gruppo="accesso", verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            ACCESSO_RIAUTENTICAZIONE_MINUTI, TipoImpostazione.INTERO, predefinito=5,
            descrizione=("Minuti di validità della conferma dell'identità (password e codice "
                         "a tempo, o passkey) richiesta per modificare le proprie credenziali "
                         "nella console"),
            minimo=1, massimo=15, ambito_ammesso=_CONSERVATORE_ENTE,
            applicata_da="gateway (gestione delle proprie credenziali)", gruppo="accesso",
            verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            RUOLI_PERMESSI_CONCEDIBILI, TipoImpostazione.CODICI,
            predefinito=CONCEDIBILI_POSSIBILI,
            descrizione=("Permessi che l'ente può mettere nei ruoli personalizzati. Esecuzione "
                         "e autorizzazione dello scarto, approvazione degli annullamenti, "
                         "firmatari, «chiudi con eccezioni» e gestione di utenti e accessi "
                         "non sono mai delegabili"),
            ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
            applicata_da="onboarding (ruoli personalizzati), gateway (accesso)", gruppo="ruoli",
            formato_codice=FORMATO_CONCEDIBILE, verso_sicuro=VersoSicuro.SOTTOINSIEME),
        Impostazione(
            NOTIFICHE_OBBLIGATORIE, TipoImpostazione.CODICI, predefinito=(),
            descrizione=("Avvisi che le persone non possono spegnere, nella forma "
                         "CATEGORIA.CANALE (es. ALLARMI.EMAIL); l'ente può solo aggiungerne"),
            ambito_ammesso=_CONSERVATORE_ENTE,
            applicata_da="notification (campanella e posta)", gruppo="notifiche",
            formato_codice="(?:" + "|".join(CATEGORIE_NOTIFICHE) + r")\.(?:CONSOLE|EMAIL)",
            verso_sicuro=VersoSicuro.SOVRAINSIEME),
        Impostazione(
            SFTP_ALLOWLIST_VOCI_MASSIME, TipoImpostazione.INTERO, predefinito=20,
            descrizione="Indirizzi o reti al massimo nell'elenco di un account SFTP",
            minimo=1, massimo=500, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_SFTP, gruppo="sftp",
            verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4, TipoImpostazione.INTERO, predefinito=16,
            descrizione="Rete IPv4 più ampia ammessa nell'elenco (prefisso minimo, es. 16 = /16)",
            minimo=0, massimo=32, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_SFTP, gruppo="sftp",
            verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6, TipoImpostazione.INTERO, predefinito=48,
            descrizione="Rete IPv6 più ampia ammessa nell'elenco (prefisso minimo)",
            minimo=0, massimo=128, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_SFTP, gruppo="sftp",
            verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            SFTP_ALLOWLIST_OBBLIGATORIA, TipoImpostazione.SCELTA,
            predefinito=ALLOWLIST_FACOLTATIVA,
            descrizione=("OBBLIGATORIA: un account SFTP senza elenco di indirizzi non si collega; "
                         "FACOLTATIVA: senza elenco si collega da ogni indirizzo"),
            scelte=(ALLOWLIST_OBBLIGATORIA, ALLOWLIST_FACOLTATIVA),
            ambito_ammesso=_CONSERVATORE_ENTE, applicata_da=_SFTP, gruppo="sftp",
            verso_sicuro=VersoSicuro.ORDINE_SCELTE),
    ]


def _voci_piani() -> list[Impostazione]:
    voci: list[Impostazione] = []
    for piano in PIANI:
        pdv, richieste = PIANI_INIZIALI[piano]
        voci += [
            Impostazione(
                codice_piano_pdv(piano), TipoImpostazione.INTERO, predefinito=pdv,
                descrizione=f"Pacchetti di versamento al giorno del piano «{piano}»",
                minimo=1, massimo=10_000_000, ambito_ammesso=_SOLO_CONSERVATORE,
                riservata_conservatore=True, applicata_da=_QUOTE_CONSERVATORE, gruppo="quote",
                verso_sicuro=VersoSicuro.MINORE),
            Impostazione(
                codice_piano_richieste(piano), TipoImpostazione.INTERO, predefinito=richieste,
                descrizione=f"Richieste al minuto su tutte le API del piano «{piano}»",
                minimo=1, massimo=1_000_000, ambito_ammesso=_SOLO_CONSERVATORE,
                riservata_conservatore=True, applicata_da=_QUOTE_CONSERVATORE, gruppo="quote",
                verso_sicuro=VersoSicuro.MINORE),
        ]
    return voci


def _voci_sessione() -> list[Impostazione]:
    return [
        Impostazione(
            codice_inattivita(ruolo), TipoImpostazione.INTERO, predefinito=minuti,
            descrizione=(f"Minuti di inattività dopo cui la sessione del portale del ruolo "
                         f"{ruolo} si chiude; l'ente può solo ridurre il valore del conservatore"),
            minimo=INATTIVITA_MINIMA_MINUTI, massimo=INATTIVITA_MASSIMA_MINUTI,
            ambito_ammesso=_CONSERVATORE_ENTE,
            applicata_da="gateway (accesso al portale)", gruppo="sessione",
            verso_sicuro=VersoSicuro.MINORE)
        for ruolo, minuti in INATTIVITA_PREDEFINITA.items()
    ]


def _voci_r09() -> list[Impostazione]:
    """R09: parametri del ciclo di vita automatico (D04, D05, D06, D20, D21, D23)."""
    return [
        Impostazione(
            SCARTO_PROPOSTA_AUTOMATICA, TipoImpostazione.SCELTA, predefinito=PROPOSTA_DISATTIVA,
            descrizione=("Proposta di scarto automatica: il sistema prepara periodicamente una "
                         "pratica in bozza con l'anteprima dei pacchetti scartabili e avvisa "
                         "l'ente; l'esecuzione resta manuale (autorizzazioni e nulla osta)"),
            norm_ref="Linee guida AgID 2021, §4.11; D.Lgs. 42/2004, art. 21",
            scelte=(PROPOSTA_ATTIVA, PROPOSTA_DISATTIVA), ambito_ammesso=_CONSERVATORE_ENTE,
            applicata_da=_CICLO_VITA_LAVORATORE, gruppo=_SCARTO,
            verso_sicuro=VersoSicuro.ORDINE_SCELTE),
        Impostazione(
            SCARTO_PROPOSTA_FREQUENZA_GIORNI, TipoImpostazione.INTERO, predefinito=30,
            descrizione="Ogni quanti giorni si prepara la proposta di scarto automatica",
            minimo=7, massimo=365, ambito_ammesso=_CONSERVATORE_ENTE,
            applicata_da=_CICLO_VITA_LAVORATORE, gruppo=_SCARTO,
            verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            SCARTO_PROPOSTA_CLASSI_ESCLUSE, TipoImpostazione.CODICI, predefinito=(),
            descrizione=("Codici delle classi documentali escluse dalla proposta di scarto "
                         "automatica (restano scartabili con una proposta manuale)"),
            ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
            applicata_da=_CICLO_VITA_LAVORATORE, gruppo=_SCARTO,
            formato_codice=_CODICE_CLASSE, verso_sicuro=VersoSicuro.SOTTOINSIEME),
        Impostazione(
            SCARTO_TENTATIVI_PDA, TipoImpostazione.INTERO, predefinito=5,
            descrizione=("Tentativi al più per la cancellazione di un pacchetto con errori "
                         "dello storage; oltre, eliminazione incompleta e allarme"),
            minimo=1, massimo=100, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="ciclo-vita (lavoratore: scarto)",
            gruppo=_SCARTO, verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            SCARTO_RIPRESE_AUTOMATICHE, TipoImpostazione.INTERO, predefinito=3,
            descrizione=("Riprese automatiche al più di uno scarto concluso con eccezioni, "
                         "quando il blocco legale cessa (oltre: ripresa manuale o «chiudi con "
                         "eccezioni» del conservatore)"),
            minimo=0, massimo=20, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_CICLO_VITA_LAVORATORE, gruppo=_SCARTO,
            verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            SCARTO_MINIMIZZAZIONE_GIORNI, TipoImpostazione.INTERO, predefinito=30,
            descrizione=("Giorni dopo lo scarto oltre i quali dei pacchetti scartati restano "
                         "solo i dati dell'elenco di scarto (identificativo, classe, date, "
                         "impronta, pratica, verbale): gli altri metadati si cancellano"),
            norm_ref="Regolamento (UE) 2016/679, art. 5, par. 1, lett. c) ed e)",
            minimo=1, massimo=365, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_CICLO_VITA_LAVORATORE, gruppo=_SCARTO,
            verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE, TipoImpostazione.CODICI,
            predefinito=("ADMIN_TENANT", "OPERATORE", "API_SYSTEM"),
            descrizione=("Ruoli dell'ente che possono annullare un versamento non ancora "
                         "conservato (vuoto = annullamento non ammesso in quello stato)"),
            ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
            applicata_da=_CICLO_VITA_ANNULLAMENTO, gruppo="annullamento",
            formato_codice=_RUOLO_ENTE, verso_sicuro=VersoSicuro.SOTTOINSIEME),
        Impostazione(
            ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE, TipoImpostazione.CODICI,
            predefinito=("ADMIN_TENANT",),
            descrizione=("Ruoli dell'ente che possono annullare un versamento già conservato "
                         "(vuoto = annullamento dopo la conservazione non ammesso)"),
            ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
            applicata_da=_CICLO_VITA_ANNULLAMENTO, gruppo="annullamento",
            formato_codice=_RUOLO_ENTE, verso_sicuro=VersoSicuro.SOTTOINSIEME),
        Impostazione(
            ANNULLAMENTO_RUOLI_APPROVAZIONE, TipoImpostazione.CODICI,
            predefinito=("ADMIN_TENANT",),
            descrizione=("Ruoli dell'ente che approvano un annullamento con doppia "
                         "approvazione: sempre una persona diversa da chi l'ha chiesto, con "
                         "un account nato prima della richiesta; solo ADMIN_TENANT (gli "
                         "OPERATORE li crea l'amministratore dell'ente)"),
            ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
            applicata_da=_CICLO_VITA_ANNULLAMENTO, gruppo="annullamento",
            formato_codice=r"(?:ADMIN_TENANT)", verso_sicuro=VersoSicuro.SOTTOINSIEME),
        Impostazione(
            ANNULLAMENTO_DOPPIA_APPROVAZIONE, TipoImpostazione.SCELTA,
            predefinito=APPROVAZIONE_DOPO_CONSERVAZIONE,
            descrizione=("Doppia approvazione dell'annullamento (quattro occhi): SEMPRE, solo "
                         "DOPO_CONSERVAZIONE o MAI; l'ente può solo renderla più severa"),
            scelte=(APPROVAZIONE_SEMPRE, APPROVAZIONE_DOPO_CONSERVAZIONE, APPROVAZIONE_MAI),
            ambito_ammesso=_CONSERVATORE_ENTE, applicata_da=_CICLO_VITA_ANNULLAMENTO,
            gruppo="annullamento", verso_sicuro=VersoSicuro.ORDINE_SCELTE),
        Impostazione(
            RETENTION_PERMANENTE_ANNI, TipoImpostazione.INTERO, predefinito=30,
            descrizione=("Anni di blocco WORM (Object Lock) per le classi a conservazione "
                         "permanente e per allegati, verbali e atti; si rinnova da solo"),
            norm_ref="Linee guida AgID 2021, §4.6 e allegato 4",
            minimo=5, massimo=100, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_RETENTION, gruppo="retention",
            verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            RETENTION_RINNOVO_SOGLIA_ANNI, TipoImpostazione.INTERO, predefinito=5,
            descrizione=("Il blocco WORM si rinnova di RETENTION_PERMANENTE_ANNI quando ne "
                         "mancano meno di questi anni (mai ridotto)"),
            minimo=1, massimo=30, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da=_RETENTION, gruppo="retention",
            verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            VERIFICA_CICLO_GIORNI, TipoImpostazione.INTERO, predefinito=365,
            descrizione=("Ogni pacchetto si rilegge e si verifica almeno una volta in questi "
                         "giorni; per enti o classi più critici il valore si può solo ridurre "
                         "(verifica più frequente)"),
            norm_ref="Linee guida AgID 2021, §4.7 (verifica periodica dell'integrità)",
            minimo=7, massimo=365,
            ambito_ammesso=frozenset({Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE}),
            applicata_da="hashchain (verifica continua)",
            gruppo="verifiche", verso_sicuro=VersoSicuro.MINORE),
        Impostazione(
            VERIFICA_CAMPIONE_INCIDENTE_PDA, TipoImpostazione.INTERO, predefinito=200,
            descrizione=("Pacchetti riletti subito, a campione, dopo un incidente dello "
                         "storage (anomalia d'integrità o errori di lettura ripetuti)"),
            minimo=10, massimo=1000, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="hashchain (verifica continua)",
            gruppo="verifiche", verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            AUDIT_VERIFICA_FINESTRA_GIORNI, TipoImpostazione.INTERO, predefinito=2,
            descrizione=("Verifica giornaliera INCREMENTALE dell'audit: segmenti nel WORM degli "
                         "ultimi giorni (agganciati al segmento precedente)"),
            minimo=1, massimo=31, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="hashchain (CronJob verifica-audit)",
            gruppo="audit", verso_sicuro=VersoSicuro.MAGGIORE),
        Impostazione(
            AUDIT_VERIFICA_COMPLETA_GIORNO, TipoImpostazione.INTERO, predefinito=1,
            descrizione=("Giorno del mese della verifica COMPLETA dell'audit (tutti i segmenti "
                         "nel WORM); negli altri giorni la verifica è incrementale"),
            minimo=1, massimo=28, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True, applicata_da="hashchain (CronJob verifica-audit)",
            gruppo="audit", verso_sicuro=VersoSicuro.MINORE),
    ]


def _voci_r13b() -> list[Impostazione]:
    return [
        Impostazione(
            PEC_CASI_USO, TipoImpostazione.CODICI, predefinito=(),
            descrizione=("Comunicazioni formali all'ente inviate ANCHE per PEC (all'indirizzo "
                         "PEC del referente), con ricevute di accettazione e consegna: SCARTO "
                         "(proposta ed esecuzione), RESTITUZIONE, RIVERSAMENTO (in uscita), "
                         "SCADENZE (copie di un versamento rifiutato in scadenza). Vuoto = "
                         "nessuna (comportamento di prima); l'ente può solo aggiungerne"),
            norm_ref="DPR 68/2005; CAD art. 48 (trasmissione con PEC)",
            ambito_ammesso=_CONSERVATORE_ENTE, applicata_da="notification (canale PEC)",
            gruppo="pec", formato_codice="(?:" + "|".join(CASI_USO_PEC) + ")",
            verso_sicuro=VersoSicuro.SOVRAINSIEME),
        Impostazione(
            PEC_CONSEGNA_ATTESA_ORE, TipoImpostazione.INTERO, predefinito=24,
            descrizione=("Ore dopo l'accettazione oltre le quali un messaggio PEC senza "
                         "ricevuta di consegna si segnala in console come «consegna non "
                         "confermata» (il gestore manda l'avviso di mancata consegna entro 24 "
                         "ore)"),
            norm_ref="DM 2/11/2005, art. 12 (avviso di mancata consegna per superamento dei "
                     "tempi massimi)",
            minimo=1, massimo=72, ambito_ammesso=_SOLO_CONSERVATORE, riservata_conservatore=True,
            applicata_da="notification (console PEC)", gruppo="pec",
            verso_sicuro=VersoSicuro.MINORE),
        # R17 (VD-P-155, decisione del committente del 01/11/2026): RICHIESTA come valore
        # generale; la deroga NON_RICHIESTA per un singolo ente (contratto che regola già
        # il trasferimento) la scrive SOLO il conservatore al livello ENTE (voce
        # riservata: l'amministratore dell'ente non scrive né annulla il valore, quindi
        # non può togliersi l'autorizzazione; il verso dichiarato resta ORDINE_SCELTE)
        Impostazione(
            RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE, TipoImpostazione.SCELTA,
            predefinito=AUTORIZZAZIONE_RICHIESTA,
            descrizione=("Riversamento in uscita verso un altro conservatore: RICHIESTA "
                         "(predefinito: prima dell'esecuzione serve l'autorizzazione firmata "
                         "del produttore che cita la pratica, riverificata dal lavoratore come "
                         "per la restituzione) o NON_RICHIESTA (deroga per un singolo ente, "
                         "concessa solo dal conservatore quando il contratto regola già il "
                         "trasferimento). Si fissa alla creazione della pratica (VD-P-155)"),
            scelte=(AUTORIZZAZIONE_RICHIESTA, AUTORIZZAZIONE_NON_RICHIESTA),
            ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
            applicata_da="ciclo-vita (riversamento in uscita)",
            gruppo="riversamento", verso_sicuro=VersoSicuro.ORDINE_SCELTE),
    ]


def _voci_r14() -> list[Impostazione]:
    return [
        Impostazione(
            NOTIFICHE_PERSONALI_GIORNI, TipoImpostazione.INTERO, predefinito=365,
            descrizione=("Giorni dopo i quali le voci personali della campanella (modifiche "
                         "delle proprie credenziali) si cancellano dal lavoratore della posta; "
                         "il database non scende mai sotto 30 giorni"),
            minimo=30, massimo=3650, ambito_ammesso=_SOLO_CONSERVATORE,
            riservata_conservatore=True,
            applicata_da="notification (lavoratore della posta, pulizia periodica)",
            gruppo="notifiche", verso_sicuro=VersoSicuro.MAGGIORE),
    ]


IMPOSTAZIONI_SERVIZIO: tuple[Impostazione, ...] = (
    *_voci_piani(),
    Impostazione(
        QUOTA_SUPERAMENTO, TipoImpostazione.SCELTA, predefinito=SUPERAMENTO_RIFIUTA,
        descrizione=("Oltre la quota giornaliera dei versamenti: RIFIUTA (429 fino a mezzanotte) "
                     "o RALLENTA (un versamento ogni QUOTA_RALLENTAMENTO_SECONDI)"),
        scelte=(SUPERAMENTO_RIFIUTA, SUPERAMENTO_RALLENTA), ambito_ammesso=_CONSERVATORE_ENTE,
        applicata_da="gateway (quota giornaliera)", gruppo="consumi",
        # W1: RIFIUTA è il più sicuro; RALLENTA l'ente lo sceglie solo se il
        # conservatore lo consente (valore generale o LIMITE.QUOTA_SUPERAMENTO)
        verso_sicuro=VersoSicuro.ORDINE_SCELTE),
    Impostazione(
        QUOTA_RALLENTAMENTO_SECONDI, TipoImpostazione.INTERO, predefinito=30,
        descrizione="Con RALLENTA, oltre la quota: secondi fra un versamento e il successivo",
        minimo=1, massimo=3600, ambito_ammesso=_SOLO_CONSERVATORE, riservata_conservatore=True,
        applicata_da="gateway (quota giornaliera)", gruppo="consumi",
        verso_sicuro=VersoSicuro.MAGGIORE),
    *_voci_sessione(),
    Impostazione(
        STAGING_SCARTATI_GIORNI, TipoImpostazione.INTERO, predefinito=90,
        descrizione=("Giorni per cui restano le copie di transito di un versamento rifiutato "
                     "(«Rielabora»); avviso al cliente 7 giorni prima"),
        minimo=30, massimo=180, ambito_ammesso=_CONSERVATORE_ENTE,
        applicata_da="gateway (pulizia dello staging), dashboard-api", gruppo="staging",
        verso_sicuro=VersoSicuro.MINORE),
    Impostazione(
        WEBHOOK_PORTE_AGGIUNTIVE, TipoImpostazione.CODICI, predefinito=(),
        descrizione=("Porte di destinazione dei webhook ammesse oltre alla 443 (sempre "
                     "ammessa); da allineare con la NetworkPolicy di uscita di notification"),
        ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
        applicata_da="notification (webhook)", gruppo="webhook", formato_codice=_PORTA,
        verso_sicuro=VersoSicuro.SOTTOINSIEME),
    Impostazione(
        PDD_VALIDITA_ORE, TipoImpostazione.INTERO, predefinito=72,
        descrizione=("Ore di validità di un pacchetto di distribuzione ZIP (anche di "
                     "restituzione); scaduto, si rigenera con un clic"),
        minimo=1, massimo=720, ambito_ammesso=_CONSERVATORE_ENTE,
        applicata_da="distribution (PdD ZIP)", gruppo="restituzione",
        verso_sicuro=VersoSicuro.MINORE),
    Impostazione(
        AUDIT_EXPORT_PERIODO_MINUTI, TipoImpostazione.INTERO, predefinito=15,
        descrizione="Durata dei periodi dell'audit esportati nel WORM (minuti)",
        minimo=5, massimo=60, ambito_ammesso=_SOLO_CONSERVATORE, riservata_conservatore=True,
        applicata_da="hashchain (export dell'audit)", gruppo="audit",
        verso_sicuro=VersoSicuro.MINORE),
    Impostazione(
        NULLA_OSTA_FIRMA, TipoImpostazione.SCELTA, predefinito=FIRMA_AVVISO,
        descrizione=("Nulla osta allo scarto senza firma: AVVISO (accettato con avviso nella "
                     "pratica) o BLOCCANTE (rifiutato); se firmato, la firma si verifica sempre"),
        norm_ref="D.Lgs. 42/2004, art. 21; DPR 445/2000, art. 69",
        scelte=(FIRMA_BLOCCANTE, FIRMA_AVVISO), ambito_ammesso=_CONSERVATORE_ENTE,
        applicata_da=_CICLO_VITA_FIRME, gruppo="firme", verso_sicuro=VersoSicuro.ORDINE_SCELTE),
    Impostazione(
        CICLO_VITA_FIRME_LIVELLO, TipoImpostazione.SCELTA, predefinito="CRITTOGRAFICA",
        descrizione=("Livello di verifica delle firme di autorizzazione allo scarto, nulla osta e "
                     "richiesta di annullamento: CRITTOGRAFICA o CON_REVOCHE (OCSP/CRL)"),
        norm_ref="Regolamento (UE) 910/2014 (eIDAS), art. 32",
        scelte=("CON_REVOCHE", "CRITTOGRAFICA"), ambito_ammesso=_CONSERVATORE_ENTE,
        applicata_da=_CICLO_VITA_FIRME, gruppo="firme", verso_sicuro=VersoSicuro.ORDINE_SCELTE),
    Impostazione(
        ANNULLAMENTO_RICHIESTA_FIRMATA, TipoImpostazione.SCELTA,
        predefinito=RICHIESTA_FACOLTATIVA,
        descrizione=("Richiesta di annullamento con documento firmato che cita il versamento: "
                     "FACOLTATIVA (se presente si verifica) o OBBLIGATORIA"),
        scelte=(RICHIESTA_OBBLIGATORIA, RICHIESTA_FACOLTATIVA),
        ambito_ammesso=_CONSERVATORE_ENTE, applicata_da=_CICLO_VITA_FIRME, gruppo="firme",
        verso_sicuro=VersoSicuro.ORDINE_SCELTE),
    Impostazione(
        CICLO_VITA_FIRMATARI_AUTORIZZATI, TipoImpostazione.CODICI, predefinito=(),
        descrizione=("Firmatari ammessi per l'autorizzazione allo scarto e la richiesta di "
                     "annullamento: codice fiscale del titolare del certificato o impronta "
                     "SHA-256 del certificato. Vuoto = nessuno (documento rifiutato)"),
        norm_ref="DPCM 3/12/2013 conservazione, art. 7; Linee guida AgID 2021, §4.11",
        ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
        applicata_da=_CICLO_VITA_FIRME, gruppo="firme", formato_codice=_FIRMATARIO,
        verso_sicuro=VersoSicuro.SOTTOINSIEME),
    Impostazione(
        CICLO_VITA_FIRMATARI_NULLA_OSTA, TipoImpostazione.CODICI, predefinito=(),
        descrizione=("Firmatari ammessi per il nulla osta allo scarto (Soprintendenza "
                     "competente): codice fiscale o impronta SHA-256 del certificato. Un "
                     "firmatario non ammesso segue NULLA_OSTA_FIRMA (BLOCCANTE o AVVISO)"),
        norm_ref="D.Lgs. 42/2004, art. 21",
        ambito_ammesso=_CONSERVATORE_ENTE, riservata_conservatore=True,
        applicata_da=_CICLO_VITA_FIRME, gruppo="firme", formato_codice=_FIRMATARIO,
        verso_sicuro=VersoSicuro.SOTTOINSIEME),
    *_voci_r09(),
    *_voci_r10(),
    *_voci_r13a(),
    *_voci_r13b(),
    *_voci_r14(),
)

REGISTRO_SERVIZIO = RegistroImpostazioni(IMPOSTAZIONI_SERVIZIO)


@dataclass(frozen=True)
class VincoloCongiunto:
    """Revisione R09 (Z4): condizione su PIÙ voci che vale sui valori effettivi
    dopo una modifica (i valori singoli ammessi non bastano). La bozza che la
    viola è bloccata con il motivo (anteprima e applicazione)."""

    codici: tuple[str, ...]
    motivo: str
    ammesso: Callable[[Mapping[str, Any]], bool]


#: Revisione R09 (Z4): con anni ≤ soglia il rinnovo del blocco WORM non porta mai
#: oltre la soglia (allegati, verbali e atti, senza legal hold, scadrebbero)
VINCOLI_CONGIUNTI: tuple[VincoloCongiunto, ...] = (
    VincoloCongiunto(
        (RETENTION_PERMANENTE_ANNI, RETENTION_RINNOVO_SOGLIA_ANNI),
        "RETENTION_PERMANENTE_ANNI deve superare RETENTION_RINNOVO_SOGLIA_ANNI: altrimenti "
        "il rinnovo del blocco WORM non porta mai oltre la soglia",
        lambda v: int(v[RETENTION_PERMANENTE_ANNI]) > int(v[RETENTION_RINNOVO_SOGLIA_ANNI])),
    # R13a: l'algoritmo con cui si firma deve essere fra quelli ancora ammessi
    VincoloCongiunto(
        (FIRMA_ALGORITMO_IMPRONTA, FIRMA_ALGORITMI_AMMESSI),
        "FIRMA_ALGORITMO_IMPRONTA deve essere fra FIRMA_ALGORITMI_AMMESSI: altrimenti ogni "
        "firma nuova andrebbe subito rimarcata",
        lambda v: str(v[FIRMA_ALGORITMO_IMPRONTA]) in tuple(v[FIRMA_ALGORITMI_AMMESSI])),
)

__all__ = [
    "AUTORIZZAZIONE_NON_RICHIESTA",
    "AUTORIZZAZIONE_RICHIESTA",
    "CASI_USO_PEC",
    "PEC_CASI_USO",
    "PEC_CONSEGNA_ATTESA_ORE",
    "RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE",
    "ALGORITMI_FIRMA",
    "ATTESTAZIONE_DISATTIVO",
    "ATTESTAZIONE_SEMPRE",
    "ATTESTAZIONE_SIGILLO",
    "ATTESTAZIONE_SU_RICHIESTA",
    "CATENA_MARCA_INTERVALLO_MINUTI",
    "FIRMA_ALGORITMI_AMMESSI",
    "FIRMA_ALGORITMO_IMPRONTA",
    "FIRMA_LIVELLO_DOCUMENTI",
    "FIRMA_RINNOVO_PREAVVISO_GIORNI",
    "FIRMA_SENZA_FORNITORE",
    "FIRMA_TENTATIVI_MASSIMI",
    "LIVELLI_FIRMA",
    "SENZA_FORNITORE_BLOCCA",
    "SENZA_FORNITORE_NON_FIRMATO",
    "ACCESSO_BLOCCO_MINUTI",
    "ACCESSO_METODI_AMMESSI",
    "ACCESSO_RIAUTENTICAZIONE_MINUTI",
    "ACCESSO_TENTATIVI_MASSIMI",
    "ALLOWLIST_FACOLTATIVA",
    "ALLOWLIST_OBBLIGATORIA",
    "CANALI_NOTIFICHE",
    "CATEGORIE_NOTIFICHE",
    "INVITO_VALIDITA_ORE",
    "METODI_ACCESSO",
    "METODO_PASSKEY",
    "METODO_PASSWORD_TOTP",
    "METODO_SPID_CIE",
    "NOTIFICHE_OBBLIGATORIE",
    "PASSWORD_LUNGHEZZA_MINIMA",
    "RUOLI_PERMESSI_CONCEDIBILI",
    "SFTP_ALLOWLIST_OBBLIGATORIA",
    "SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4",
    "SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6",
    "SFTP_ALLOWLIST_VOCI_MASSIME",
    "VINCOLI_CONGIUNTI",
    "VincoloCongiunto",
    "SCARTO_PROPOSTA_AUTOMATICA",
    "SCARTO_PROPOSTA_FREQUENZA_GIORNI",
    "SCARTO_PROPOSTA_CLASSI_ESCLUSE",
    "SCARTO_TENTATIVI_PDA",
    "SCARTO_RIPRESE_AUTOMATICHE",
    "SCARTO_MINIMIZZAZIONE_GIORNI",
    "PROPOSTA_ATTIVA",
    "PROPOSTA_DISATTIVA",
    "ANNULLAMENTO_RUOLI_PRIMA_CONSERVAZIONE",
    "ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE",
    "ANNULLAMENTO_RUOLI_APPROVAZIONE",
    "ANNULLAMENTO_DOPPIA_APPROVAZIONE",
    "APPROVAZIONE_SEMPRE",
    "APPROVAZIONE_DOPO_CONSERVAZIONE",
    "APPROVAZIONE_MAI",
    "RETENTION_PERMANENTE_ANNI",
    "RETENTION_RINNOVO_SOGLIA_ANNI",
    "VERIFICA_CICLO_GIORNI",
    "VERIFICA_CAMPIONE_INCIDENTE_PDA",
    "AUDIT_VERIFICA_COMPLETA_GIORNO",
    "AUDIT_VERIFICA_FINESTRA_GIORNI",
    "ANNULLAMENTO_RICHIESTA_FIRMATA",
    "AUDIT_EXPORT_PERIODO_MINUTI",
    "CICLO_VITA_FIRMATARI_AUTORIZZATI",
    "CICLO_VITA_FIRMATARI_NULLA_OSTA",
    "CICLO_VITA_FIRME_LIVELLO",
    "FIRMA_AVVISO",
    "FIRMA_BLOCCANTE",
    "NULLA_OSTA_FIRMA",
    "RICHIESTA_FACOLTATIVA",
    "RICHIESTA_OBBLIGATORIA",
    "IMPOSTAZIONI_SERVIZIO",
    "INATTIVITA_MASSIMA_MINUTI",
    "INATTIVITA_MINIMA_MINUTI",
    "INATTIVITA_PREDEFINITA",
    "INATTIVITA_RUOLO_SCONOSCIUTO",
    "PDD_VALIDITA_ORE",
    "PIANI",
    "PIANI_INIZIALI",
    "PORTA_WEBHOOK_SEMPRE",
    "QUOTA_RALLENTAMENTO_SECONDI",
    "QUOTA_SUPERAMENTO",
    "REGISTRO_SERVIZIO",
    "SOGLIE_AVVISO_QUOTA",
    "STAGING_SCARTATI_GIORNI",
    "SUPERAMENTO_RALLENTA",
    "SUPERAMENTO_RIFIUTA",
    "WEBHOOK_PORTE_AGGIUNTIVE",
    "codice_inattivita",
    "codice_piano_pdv",
    "codice_piano_richieste",
]
