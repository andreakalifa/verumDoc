# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/domain/permessi.py — chi può avviare e vedere le pratiche (REGOLE §14).

Riferimenti: contratto Q2 di R05 (permessi), REGOLE_SVILUPPO §14.1 (un cambio
di permessi è una decisione), §14.2 (perimetro vuoto = nessun dato), §14.3
(ente non determinabile = negato), §14.4 (404 fra enti), §14.9 (helper unici).

Modulo PURO. Matrice (contratto Q2):
  - scarto e restituzione: avviano RSC_CONSERVATORE o ADMIN_TENANT; autorizza
    il produttore (ADMIN_TENANT); esegue il conservatore (RSC/SUPER_ADMIN)
    — le transizioni sono in ``app.domain.stati``;
  - rettifica e annullamento: ADMIN_TENANT, OPERATORE e chiave API (API_SYSTEM)
    del proprio ente, con regola configurabile (R09, D23: parametri dell'ente,
    con doppia approvazione di una seconda persona);
  - riversamento in uscita (non fissato dal contratto, scelta da confermare):
    avviano RSC_CONSERVATORE o ADMIN_TENANT, rende eseguibile ed esegue il
    conservatore;
  - riversamento in INGRESSO: lo avvia solo ADMIN_TENANT dell'ente (revisione
    R05, R8: i documenti si versano con il token di canale A NOME di chi
    avvia, e un utente del conservatore non può versare per un ente; scelta da
    confermare, REGOLE §14.1); rende eseguibile ed esegue il conservatore.
Lettura: un ruolo dell'ente vede solo le pratiche del proprio ente; i ruoli
del conservatore vedono le pratiche (metadati, mai contenuti) di tutti gli enti.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from app.domain import stati as st

#: R10 (D30): permessi del catalogo (``libs.verumdoc_rules.permessi``) controllati qui
PERMESSO_ANNULLAMENTO = "annullamento:richiedi"
PERMESSO_RETTIFICA = "rettifica:richiedi"

#: Ruoli dell'ente che leggono le pratiche del proprio ente (VIEWER compreso).
RUOLI_LETTURA_ENTE = frozenset({st.ADMIN_TENANT, st.OPERATORE, "VIEWER", st.API_SYSTEM})

#: Avvio delle pratiche massive (rettifica e annullamento: impostazioni).
RUOLI_AVVIO: dict[str, frozenset[str]] = {
    st.SCARTO: frozenset({st.RSC, st.ADMIN_TENANT}),
    st.RESTITUZIONE: frozenset({st.RSC, st.ADMIN_TENANT}),
    st.RIVERSAMENTO_USCITA: frozenset({st.RSC, st.ADMIN_TENANT}),
    st.RIVERSAMENTO_INGRESSO: frozenset({st.ADMIN_TENANT}),
}


@dataclass(frozen=True)
class Identita:
    """Identità verificata del chiamante (JWT utente o chiave API)."""

    tenant_id: str
    user_id: str | None
    ruolo: str
    metodo: str  # jwt | api_key
    api_key_id: str | None = None
    #: R10 (D30): ruolo personalizzato del token (``rp``) e i suoi permessi; None =
    #: ruolo fisso (valgono i permessi del modello)
    ruolo_personalizzato: str | None = None
    permessi: frozenset[str] | None = None

    @property
    def conservatore(self) -> bool:
        # un ruolo personalizzato non è mai del conservatore (base OPERATORE o VIEWER)
        return self.ruolo in st.RUOLI_CONSERVATORE and not self.ruolo_personalizzato

    def concesso(self, permesso: str) -> bool:
        """R10 (D30): con un ruolo personalizzato serve il permesso; con un ruolo
        fisso decidono le regole per ruolo (parametri dell'ente) come prima."""
        if not self.ruolo_personalizzato:
            return True
        return permesso in (self.permessi or frozenset())


def puo_leggere(identita: Identita, tenant_pratica: str) -> bool:
    """Perimetro di lettura: stesso ente, oppure ruolo del conservatore."""
    if identita.conservatore:
        return True
    return identita.ruolo in RUOLI_LETTURA_ENTE and identita.tenant_id == tenant_pratica


def ente_del_perimetro(identita: Identita, ente_richiesto: str | None) -> str | None:
    """Ente su cui filtrare un elenco. Ruoli dell'ente: sempre il proprio (un
    ``ente`` diverso dà un elenco vuoto, non un errore: §14.4). Conservatore:
    l'ente indicato o tutti (``None``)."""
    if identita.conservatore:
        return ente_richiesto
    return identita.tenant_id


def motivo_blocco_avvio(tipo: str, identita: Identita, ente: str) -> tuple[str, str] | None:
    """``None`` se l'identità può avviare una pratica massiva di ``tipo`` per ``ente``."""
    ammessi = RUOLI_AVVIO.get(tipo, frozenset())
    if identita.ruolo_personalizzato:
        # R10 (D30): le pratiche massive restano ai ruoli fissi (base mai ammessa)
        return ("LC-403-RUOLO", "Le pratiche di scarto, riversamento e restituzione le avvia "
                                "l'amministratore dell'ente o il conservatore")
    if identita.ruolo not in ammessi:
        return ("LC-403-RUOLO", f"Il ruolo {identita.ruolo or 'sconosciuto'} non può avviare "
                                f"una pratica di {tipo.lower()} "
                                f"(ammessi: {', '.join(sorted(ammessi))})")
    if identita.metodo != "jwt":
        return ("LC-403-RUOLO", "Le pratiche di scarto, riversamento e restituzione si "
                                "avviano solo con l'accesso di una persona (non con chiave API)")
    if not identita.conservatore and identita.tenant_id != ente:
        return ("LC-404-ENTE", "Ente non trovato")
    return None


def motivo_blocco_annullamento(identita: Identita, *, conservato: bool,
                               ruoli_prima: Collection[str],
                               ruoli_dopo: Collection[str]) -> tuple[str, str] | None:
    """Regola configurabile dell'annullamento (contratto Q2; R09, D23: parametri
    ANNULLAMENTO_RUOLI_* dell'ente): ruoli ammessi prima e dopo la conservazione;
    un elenco vuoto vuol dire che in quello stato l'annullamento non è ammesso."""
    ruoli = ruoli_dopo if conservato else ruoli_prima
    if not identita.concesso(PERMESSO_ANNULLAMENTO):
        return ("LC-403-RUOLO", "Il tuo ruolo non comprende la richiesta di annullamento")
    if not ruoli:
        quando = "dopo la conservazione" if conservato else "prima della conservazione"
        return ("LC-409-ANNULLAMENTO",
                f"L'annullamento {quando} non è ammesso dalla configurazione dell'ente")
    if identita.ruolo not in ruoli:
        return ("LC-403-RUOLO", ("Dopo la conservazione possono annullare: " if conservato
                                 else "Possono annullare un versamento: ")
                + ", ".join(sorted(ruoli)))
    return None


#: R09 (D23): scelte del parametro ANNULLAMENTO_DOPPIA_APPROVAZIONE
DOPPIA_SEMPRE, DOPPIA_DOPO_CONSERVAZIONE, DOPPIA_MAI = "SEMPRE", "DOPO_CONSERVAZIONE", "MAI"


def doppia_approvazione_richiesta(scelta: str, *, conservato: bool) -> bool:
    """R09 (D23): l'annullamento chiede una seconda persona? Una scelta non nota
    vale come SEMPRE (fail-closed)."""
    if scelta == DOPPIA_MAI:
        return False
    if scelta == DOPPIA_DOPO_CONSERVAZIONE:
        return conservato
    return True


def persona(identita: Identita) -> bool:
    """Accesso personale (JWT con utente), non una chiave API."""
    return identita.metodo == "jwt" and bool(identita.user_id) \
        and identita.ruolo != "API_SYSTEM"


def motivo_blocco_richiesta_doppia(identita: Identita) -> tuple[str, str] | None:
    """Revisione R09 (Z1, VD-P-90): con la doppia approvazione l'annullamento lo
    chiede una PERSONA. Con una chiave API il «richiedente» registrato è il
    responsabile della chiave, non chi la usa: chi ha generato la chiave (o il
    responsabile) potrebbe poi approvare da sé la propria richiesta."""
    if not persona(identita):
        return ("LC-403-QUATTRO-OCCHI",
                "Con la doppia approvazione l'annullamento si chiede con l'accesso di una "
                "persona, non con una chiave API")
    return None


def motivo_blocco_approvazione(identita: Identita, *, tenant_pratica: str,
                               richiedente: str | None,
                               ruoli_approvazione: Collection[str],
                               richiesta_ruolo: str | None = None,
                               approva: bool = True,
                               approvatore_creato_il: datetime | None = None,
                               pratica_creata_il: datetime | None = None
                               ) -> tuple[str, str] | None:
    """R09 (D23): quattro occhi VERI. Approva (o respinge) solo una persona
    dell'ente della pratica (accesso personale, mai una chiave API), con un ruolo
    ammesso dall'ente, DIVERSA da chi ha chiesto l'annullamento.

    Revisione R09 (Z1, VD-P-90): una richiesta arrivata con una chiave API non
    si approva (si può solo respingere): la persona dietro la chiave non è nota.
    Chi approva deve avere un account nato PRIMA della richiesta (nessun utente
    creato apposta dopo l'avvio della pratica)."""
    if identita.conservatore:
        return ("LC-403-QUATTRO-OCCHI", "L'annullamento lo approva l'ente produttore")
    if identita.ruolo_personalizzato:
        # R10 (D30): l'approvazione con doppia approvazione non si delega mai
        return ("LC-403-QUATTRO-OCCHI", "L'approvazione di un annullamento resta "
                                        "all'amministratore dell'ente: non si delega")
    if identita.tenant_id != tenant_pratica:
        return ("LC-404", "Pratica non trovata")
    if not persona(identita):
        return ("LC-403-QUATTRO-OCCHI",
                "L'approvazione richiede l'accesso di una persona (non una chiave API)")
    if identita.ruolo not in ruoli_approvazione:
        return ("LC-403-QUATTRO-OCCHI",
                "Possono approvare un annullamento: " + (", ".join(sorted(ruoli_approvazione))
                                                        or "nessun ruolo (configurazione)"))
    if richiedente is None or str(identita.user_id) == str(richiedente):
        return ("LC-403-QUATTRO-OCCHI",
                "Chi ha chiesto l'annullamento non può approvarlo: serve una seconda persona")
    if not approva:
        return None
    if richiesta_ruolo == "API_SYSTEM":
        return ("LC-403-QUATTRO-OCCHI",
                "La richiesta è arrivata con una chiave API: va respinta e chiesta di nuovo "
                "da una persona")
    if approvatore_creato_il is None or pratica_creata_il is None \
            or approvatore_creato_il >= pratica_creata_il:
        return ("LC-403-QUATTRO-OCCHI",
                "Approva solo un utente che esisteva già quando l'annullamento è stato chiesto")
    return None


def motivo_blocco_rettifica(identita: Identita, ruoli: Collection[str]) -> tuple[str, str] | None:
    if not identita.concesso(PERMESSO_RETTIFICA):
        return ("LC-403-RUOLO", "Il tuo ruolo non comprende la richiesta di rettifica")
    if identita.ruolo not in ruoli:
        return ("LC-403-RUOLO", f"Possono chiedere una rettifica: {', '.join(sorted(ruoli))}")
    return None


__all__ = ["DOPPIA_DOPO_CONSERVAZIONE", "DOPPIA_MAI", "DOPPIA_SEMPRE", "Identita", "RUOLI_AVVIO",
           "RUOLI_LETTURA_ENTE", "doppia_approvazione_richiesta", "ente_del_perimetro",
           "motivo_blocco_annullamento", "motivo_blocco_approvazione", "motivo_blocco_avvio",
           "motivo_blocco_richiesta_doppia", "persona",
           "motivo_blocco_rettifica", "puo_leggere"]
