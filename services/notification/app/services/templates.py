# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Template HTML/testo per le notifiche VerumDoc.
F2.3 — Notification Service Completo
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from enum import StrEnum


class EventoNotifica(StrEnum):
    TENANT_CREATO = "tenant.creato"
    TENANT_ATTIVATO = "tenant.attivato"
    TENANT_SOSPESO = "tenant.sospeso"
    PDV_RICEVUTO = "pdv.ricevuto"
    PDV_VALIDATO = "pdv.validato"
    PDV_ERRORE = "pdv.errore"
    AIP_CONSERVATO = "aip.conserved"
    AIP_DISTRIBUITO = "aip.distribuito"
    SCADENZA_CONSERVAZIONE = "scadenza.conservazione"
    HASHCHAIN_SEALED = "hashchain.seal"


@dataclass
class NotificaContent:
    oggetto: str
    corpo_html: str
    corpo_testo: str


_CSS = (
    "<style>"
    "body{font-family:Arial,sans-serif;background:#f4f4f4;margin:0;padding:0}"
    ".container{max-width:600px;margin:20px auto;background:white;"
    "border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1)}"
    ".header{background:#1a3a5c;color:white;padding:24px;text-align:center}"
    ".header h1{margin:0;font-size:22px}"
    ".body{padding:28px;color:#333}"
    ".highlight{background:#e8f4fd;border-left:4px solid #1a3a5c;"
    "padding:12px 16px;margin:16px 0;border-radius:0 4px 4px 0}"
    ".footer{background:#f8f8f8;padding:16px;text-align:center;"
    "font-size:12px;color:#888;border-top:1px solid #eee}"
    "</style>"
)

_FOOTER = (
    '<div class="footer">'
    "Servizio di conservazione VerumDoc<br>"
    "Messaggio generato automaticamente. Non rispondere."
    "</div>"
)


def _e(valore: str) -> str:
    """Escape HTML dei valori che arrivano dagli eventi (es. messaggi di errore)."""
    return html.escape(str(valore or ""))


def _wrap(titolo: str, corpo: str) -> str:
    return (
        "<!DOCTYPE html><html><head>" + _CSS + "</head><body>"
        '<div class="container">'
        '<div class="header"><h1>VerumDoc</h1>'
        '<p style="margin:4px 0;opacity:0.8">' + titolo + "</p></div>"
        '<div class="body">' + corpo + "</div>" + _FOOTER + "</div></body></html>"
    )


def get_template(evento: str, ctx: dict) -> NotificaContent:
    """Genera il contenuto della notifica in base all'evento."""

    ragione = ctx.get("ragione_sociale", "Cliente")
    piano = ctx.get("piano", "starter").upper()
    tenant_id = ctx.get("tenant_id", "")
    schema_name = ctx.get("schema_name", "")
    quota = ctx.get("storage_quota_gb", 0)
    urn = ctx.get("urn", "")
    aip_id = ctx.get("aip_id", "")
    pdv_id = ctx.get("pdv_id", "")
    codice_errore = ctx.get("codice_errore", "")
    step = ctx.get("step", "")
    messaggio = ctx.get("messaggio", "")
    scadenza = ctx.get("scadenza_conservazione", "")
    giorni = ctx.get("giorni_rimanenti", 0)

    templates = {
        "tenant.creato": NotificaContent(
            oggetto="[VerumDoc] Benvenuto - Account creato",
            corpo_html=_wrap(
                "Account creato",
                "<p>Gentile " + ragione + ",</p>"
                "<p>Il vostro account VerumDoc e stato creato con successo.</p>"
                '<div class="highlight">'
                "<strong>Piano:</strong> " + piano + "<br>"
                "<strong>Stato:</strong> TRIAL<br>"
                "<strong>ID Tenant:</strong> " + tenant_id + "</div>"
                "<p>Per attivare il vostro account contattate il supporto VerumDoc.</p>",
            ),
            corpo_testo="Account VerumDoc creato per " + ragione + ". Piano: " + piano,
        ),
        "tenant.attivato": NotificaContent(
            oggetto="[VerumDoc] Account attivato - Conservazione operativa",
            corpo_html=_wrap(
                "Account attivato",
                "<p>Gentile " + ragione + ",</p>"
                "<p>Il vostro account VerumDoc e <strong>attivo</strong>.</p>"
                '<div class="highlight">'
                "<strong>Piano:</strong> " + piano + "<br>"
                "<strong>Schema DB:</strong> " + schema_name + "<br>"
                "<strong>Quota storage:</strong> " + str(quota) + " GB</div>"
                "<p>Potete ora inviare documenti tramite API o dashboard.</p>",
            ),
            corpo_testo="Account attivato per " + ragione,
        ),
        "aip.conserved": NotificaContent(
            oggetto="[VerumDoc] Pacchetto di archiviazione creato - " + (urn or aip_id),
            corpo_html=_wrap(
                "Pacchetto di archiviazione creato",
                "<p>Il pacchetto di archiviazione è stato creato dal sistema.</p>"
                '<div class="highlight">'
                "<strong>URN:</strong> " + _e(urn) + "<br>"
                "<strong>ID pacchetto:</strong> " + _e(aip_id) + "</div>"
                "<p>Lo stato di conservazione e il rapporto di versamento sono "
                "consultabili nella dashboard.</p>",
            ),
            corpo_testo=(
                "Pacchetto di archiviazione creato. URN: "
                + (urn or "-")
                + ". ID: "
                + aip_id
                + ". Stato e rapporto di versamento in dashboard."
            ),
        ),
        "pdv.errore": NotificaContent(
            oggetto="[VerumDoc] ERRORE validazione - PdV " + pdv_id,
            corpo_html=_wrap(
                "Errore di validazione",
                '<p style="color:#c0392b">Errore durante la validazione.</p>'
                '<div class="highlight">'
                "<strong>PdV ID:</strong> " + _e(pdv_id) + "<br>"
                "<strong>Step:</strong> " + _e(str(step)) + "<br>"
                "<strong>Codice:</strong> " + _e(codice_errore) + "<br>"
                "<strong>Messaggio:</strong> " + _e(messaggio) + "</div>"
                "<p>Correggere il documento e ripresentare il versamento.</p>",
            ),
            corpo_testo="Errore validazione PdV "
            + pdv_id
            + ". Codice: "
            + codice_errore
            + ". "
            + messaggio,
        ),
        "scadenza.conservazione": NotificaContent(
            oggetto="[VerumDoc] AVVISO - Scadenza conservazione imminente",
            corpo_html=_wrap(
                "Scadenza conservazione",
                '<p style="color:#e67e22"><strong>ATTENZIONE:</strong> scadenza imminente.</p>'
                '<div class="highlight">'
                "<strong>AIP ID:</strong> " + aip_id + "<br>"
                "<strong>URN:</strong> " + urn + "<br>"
                "<strong>Scadenza:</strong> " + scadenza + "<br>"
                "<strong>Giorni rimanenti:</strong> " + str(giorni) + "</div>"
                "<p>Rinnovare la conservazione o avviare la procedura di scarto.</p>",
            ),
            corpo_testo="Scadenza conservazione imminente per AIP "
            + aip_id
            + ". Scadenza: "
            + scadenza,
        ),
    }

    # R07: avvisi automatici all'ente (D13, D17) e scadenza dello staging (D15)
    if evento == "quota.soglia_raggiunta":
        soglia = _e(str(ctx.get("soglia", "")))
        testo = (f"I versamenti di oggi ({_e(str(ctx.get('utilizzo', '')))}) hanno raggiunto il "
                 f"{soglia}% della quota giornaliera ({_e(str(ctx.get('limite', '')))}) del piano "
                 f"{_e(str(ctx.get('piano', '')))}.")
        if str(ctx.get("effetto") or ""):
            testo += (" I versamenti successivi sono rallentati." if ctx.get("effetto")
                      == "rallentamento" else " I versamenti successivi sono rifiutati fino "
                      "a mezzanotte.")
        if ctx.get("piano_suggerito"):
            testo += f" Valutate il passaggio al piano {_e(str(ctx['piano_suggerito']))}."
        return NotificaContent(oggetto=f"[VerumDoc] Quota giornaliera al {soglia}%",
                               corpo_html=_wrap("Quota giornaliera", f"<p>{testo}</p>"),
                               corpo_testo=testo)
    if evento == "idempotenza.chiave_omessa":
        testo = (f"Nell'ultima settimana {_e(str(ctx.get('versamenti', '')))} versamenti sono "
                 "arrivati senza l'intestazione Idempotency-Key. Dal giorno successivo al "
                 f"{_e(str(ctx.get('fino_al', '')))} queste richieste saranno rifiutate "
                 "(428 VRS-125): aggiornate il sistema versante.")
        return NotificaContent(oggetto="[VerumDoc] Idempotency-Key mancante nei versamenti",
                               corpo_html=_wrap("Idempotency-Key", f"<p>{testo}</p>"),
                               corpo_testo=testo)
    if evento == "ciclo_vita.avviso":
        # R09 (D20, D21, D23): un'e-mail in parole semplici per sottotipo
        sottotipo = str(ctx.get("sottotipo") or "")
        pratica = _e(str(ctx.get("pratica_id", "")))
        testi = {
            "PROPOSTA_SCARTO": (
                "Proposta di scarto pronta da controllare",
                f"È pronta in bozza la proposta di scarto {pratica} con "
                f"{_e(str(ctx.get('selezionabili', '')))} pacchetti scaduti. Nulla viene "
                "cancellato senza l'autorizzazione firmata dell'ente, il nulla osta (per le PA) "
                "e l'esecuzione del conservatore."),
            "RIPRESA_SCARTO": (
                "Scarto ripreso automaticamente",
                f"Lo scarto {pratica} riprende da solo: "
                f"{_e(str(ctx.get('pda_sbloccati', '')))} pacchetti non hanno più blocchi "
                "(blocco legale tolto). Il verbale integrativo sarà nella pratica."),
            "ANNULLAMENTO_DA_APPROVARE": (
                "Annullamento da approvare",
                f"Un annullamento ({pratica}) attende l'approvazione di una seconda persona "
                "dell'ente. Finché non è approvato il versamento resta valido."),
            # revisione R14 (4): pratica sospesa ripresa dal conservatore
            "PRATICA_RIPRESA": (
                "Pratica ripresa",
                f"La pratica {pratica}, ferma dopo ripetuti errori del servizio, è stata "
                "ripresa dal conservatore: il lavoro continua da dove si era fermato."),
            "ANNULLAMENTO_RESPINTO": (
                "Annullamento non applicato",
                f"L'annullamento {pratica} non è stato applicato: "
                f"{_e(str(ctx.get('motivo', '')))}. Il versamento resta valido; se serve, "
                "chiedere di nuovo l'annullamento."),
        }
        oggetto, testo = testi.get(sottotipo, ("Avviso del ciclo di vita",
                                               f"Avviso sulla pratica {pratica}."))
        return NotificaContent(oggetto=f"[VerumDoc] {oggetto}",
                               corpo_html=_wrap(oggetto, f"<p>{testo}</p>"),
                               corpo_testo=testo)
    if evento == "versamento.staging_scaduto":
        link = str(ctx.get("rielabora") or "")
        testo = (f"Le copie di transito del versamento rifiutato {_e(str(ctx.get('pdv_id', '')))} "
                 f"saranno eliminate il {_e(str(ctx.get('scade_il', ''))[:10])}: dopo non "
                 "si potrà più rielaborare.")
        html = f"<p>{testo}</p>" + (f'<p><a href="{_e(link)}">Rielabora</a></p>' if link else "")
        return NotificaContent(oggetto="[VerumDoc] Versamento rifiutato in scadenza",
                               corpo_html=_wrap("Versamento rifiutato", html),
                               corpo_testo=testo + (f" Rielabora: {link}" if link else ""))

    if evento in templates:
        return templates[evento]

    # Default fallback
    return NotificaContent(
        oggetto="[VerumDoc] Evento: " + evento,
        corpo_html=_wrap(
            "Notifica di sistema",
            "<p>Si e verificato l'evento <strong>" + evento + "</strong>.</p>",
        ),
        corpo_testo="VerumDoc: evento " + evento,
    )
