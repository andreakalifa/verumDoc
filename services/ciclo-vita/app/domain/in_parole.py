# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/domain/in_parole.py — esito di una pratica in parole semplici (R09).

Riferimenti: REGOLE_SVILUPPO §25.9 (testi per le persone), §11.5 (la pagina dice
la verità sullo stato). Modulo PURO: dalla pratica e dai conteggi degli oggetti
una frase per la console, senza ricalcolare permessi né stati.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain import stati as st


def _pacchetti(n: int) -> str:
    return "1 pacchetto" if n == 1 else f"{n} pacchetti"


def descrivi(pratica: Mapping[str, Any], conteggi: Mapping[str, Any]) -> str:
    """Una o due frasi sull'esito o sul prossimo passo della pratica."""
    tipo, stato = pratica.get("tipo"), pratica.get("stato")
    dati = dict(pratica.get("dati") or {})
    lavoro = pratica.get("lavoro")
    if tipo == st.SCARTO:
        eliminati = int(conteggi.get("ELIMINATO") or 0)
        incompleti = int(conteggi.get("NON_COMPLETATO") or 0)
        if stato == st.BOZZA and dati.get("proposta_automatica"):
            return ("Proposta preparata in automatico: controllare l'elenco dei pacchetti e, se "
                    "va bene, inviarla. Nulla viene cancellato senza autorizzazione firmata, "
                    "nulla osta (per le PA) ed esecuzione del conservatore.")
        if stato == st.ESEGUITA:
            return f"Scarto eseguito: {_pacchetti(eliminati)} cancellati per intero."
        if stato == st.ESEGUITA_CON_ECCEZIONI:
            if lavoro == st.LAVORO_CHIUDI:
                return "Chiusura con eccezioni in corso: il servizio la registra nel registro."
            if lavoro == st.LAVORO_ESEGUI:
                return "Ripresa in corso: il servizio riprova i pacchetti non cancellati."
            riprese = int(dati.get("riprese_automatiche") or 0)
            return (f"Scarto concluso con eccezioni: {_pacchetti(incompleti)} non cancellati del "
                    f"tutto (di solito un blocco legale). Ripresa automatica quando il blocco "
                    f"sarà tolto (riprese automatiche già fatte: {riprese}); il conservatore "
                    "può anche riprendere ora o chiudere con eccezioni.")
        if stato == st.CHIUSA_CON_ECCEZIONI:
            return (f"Scarto chiuso con eccezioni: {_pacchetti(eliminati)} cancellati, "
                    f"{_pacchetti(incompleti)} restano conservati in parte e continuano a "
                    "essere verificati. Motivo: " + str((dati.get("chiusura") or {})
                                                        .get("motivo") or "non indicato"))
    if tipo == st.ANNULLAMENTO:
        if stato in (st.RICHIESTA, st.APPROVATA) and lavoro == st.LAVORO_ANNULLA:
            return ("Annullamento accettato: il sistema lo ricontrolla e lo applica a breve. "
                    "Fino ad allora il versamento resta valido.")
        if stato in (st.RICHIESTA, st.APPROVATA) and pratica.get("ultimo_errore"):
            return ("Annullamento fermo: il ricontrollo del sistema non è riuscito (vedi "
                    "dettaglio). Il versamento resta valido: un amministratore dell'ente o il "
                    "conservatore lo respinge, poi si può chiedere di nuovo.")
        if stato == st.RICHIESTA and (dati.get("approvazione") or {}).get("richiesta"):
            return ("Annullamento in attesa: deve approvarlo un'altra persona dell'ente. Il "
                    "versamento resta valido fino all'approvazione.")
        if stato == st.ESEGUITA:
            return "Versamento annullato (nessun file cancellato: resta per lo scarto)."
        if stato == st.RESPINTA and dati.get("respinta_dal_sistema"):
            return ("Annullamento non applicato: "
                    + str(dati["respinta_dal_sistema"].get("motivo") or "qualcosa è cambiato")
                    + ". Il versamento resta valido; si può chiedere di nuovo.")
        if stato == st.RESPINTA:
            return "Annullamento respinto: il versamento resta valido; si può chiedere di nuovo."
    if stato == st.RESPINTA:
        return "Pratica respinta: nessun effetto sui pacchetti."
    if stato in (st.ESEGUITA, st.COMPLETATA):
        return "Pratica conclusa."
    return f"Pratica nello stato {stato}."


__all__ = ["descrivi"]
