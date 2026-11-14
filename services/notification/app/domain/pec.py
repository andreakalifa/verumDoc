# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/domain/pec.py — regole pure del canale PEC (R13b, VD-C-078).

Modulo PURO (REGOLE §7.2): niente rete, niente orologio, niente database.

Stati di un messaggio PEC (stessi valori del vincolo ``ck_pec_messaggi_stato``):

    IN_CODA → IN_INVIO → INVIATO ──(accettazione)──→ ACCETTATO ──(consegna)──→ CONSEGNATO
        ↑         │          │                          └──(mancata consegna)──→ MANCATA_CONSEGNA
        └─────────┘          └──(non accettazione)──→ NON_ACCETTATO
     (errore temporaneo)
    IN_CODA/IN_INVIO ──(errore permanente o tentativi esauriti)──→ NON_PARTITO

Una ricevuta può arrivare prima che il lavoratore abbia scritto INVIATO (il gestore
risponde subito e un'altra replica legge la casella): le transizioni da IN_INVIO
sono ammesse e la scrittura di INVIATO avviene solo se lo stato è ancora IN_INVIO.
La consegna può arrivare prima dell'accettazione: CONSEGNATO resta, l'accettazione
registra solo l'ora. Lo stato non torna mai indietro.

Casi d'uso (parametro ``PEC_CASI_USO`` per ente): dagli eventi che la posta già
riceve (``pratica.cambio_stato`` di scarto, restituzione e riversamento in uscita;
``versamento.staging_scaduto``) il testo della comunicazione formale all'ente.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

IN_CODA = "IN_CODA"
IN_INVIO = "IN_INVIO"
INVIATO = "INVIATO"
ACCETTATO = "ACCETTATO"
CONSEGNATO = "CONSEGNATO"
NON_ACCETTATO = "NON_ACCETTATO"
MANCATA_CONSEGNA = "MANCATA_CONSEGNA"
NON_PARTITO = "NON_PARTITO"
STATI = (IN_CODA, IN_INVIO, INVIATO, ACCETTATO, CONSEGNATO, NON_ACCETTATO, MANCATA_CONSEGNA,
         NON_PARTITO)
FINALI = frozenset({CONSEGNATO, NON_ACCETTATO, MANCATA_CONSEGNA, NON_PARTITO})

CASO_PROVA = "PROVA"
CASI_USO = ("SCARTO", "RESTITUZIONE", "RIVERSAMENTO", "SCADENZE", CASO_PROVA)
#: Topic che possono generare una comunicazione PEC
TOPIC_PEC = ("pratica.cambio_stato", "versamento.staging_scaduto")

#: Passaggi di stato ammessi, ribaditi dal trigger ``pec_messaggi_protetti``
#: (migrazione 055, ``PASSAGGI_PEC``; revisione R13b, rilievo 5)
PASSAGGI: frozenset[tuple[str, str]] = frozenset({
    (IN_CODA, IN_INVIO), (IN_CODA, NON_PARTITO),
    (IN_INVIO, IN_CODA), (IN_INVIO, INVIATO), (IN_INVIO, NON_PARTITO),
    (IN_INVIO, ACCETTATO), (IN_INVIO, NON_ACCETTATO), (IN_INVIO, CONSEGNATO),
    (IN_INVIO, MANCATA_CONSEGNA),
    (INVIATO, ACCETTATO), (INVIATO, NON_ACCETTATO), (INVIATO, CONSEGNATO),
    (INVIATO, MANCATA_CONSEGNA),
    (ACCETTATO, CONSEGNATO), (ACCETTATO, MANCATA_CONSEGNA),
})

#: ricevuta → (stati da cui si applica, nuovo stato, colonna dell'ora)
_TRANSIZIONI: dict[str, tuple[frozenset[str], str, str]] = {
    "ACCETTAZIONE": (frozenset({IN_INVIO, INVIATO}), ACCETTATO, "accettato_il"),
    "NON_ACCETTAZIONE": (frozenset({IN_INVIO, INVIATO}), NON_ACCETTATO, "esito_il"),
    "AVVENUTA_CONSEGNA": (frozenset({IN_INVIO, INVIATO, ACCETTATO}), CONSEGNATO,
                          "consegnato_il"),
    "ERRORE_CONSEGNA": (frozenset({IN_INVIO, INVIATO, ACCETTATO}), MANCATA_CONSEGNA, "esito_il"),
}


@dataclass(frozen=True)
class Effetto:
    """Che cosa fa una ricevuta associata sul messaggio."""

    nuovo_stato: str | None   # None = stato invariato
    colonna_ora: str | None   # colonna dell'ora da valorizzare (se ancora vuota)


def effetto_ricevuta(stato: str, tipo: str) -> Effetto:
    """Effetto di una ricevuta AUTENTICA e ASSOCIATA sul messaggio nello ``stato``."""
    regola = _TRANSIZIONI.get(tipo)
    if regola is None:
        return Effetto(None, None)
    da, a, colonna = regola
    if stato in da:
        return Effetto(a, colonna)
    if tipo == "ACCETTAZIONE" and stato in (CONSEGNATO, MANCATA_CONSEGNA):
        # consegna arrivata prima: si registra solo l'ora dell'accettazione
        return Effetto(None, "accettato_il")
    return Effetto(None, None)


@dataclass(frozen=True)
class Comunicazione:
    caso_uso: str
    oggetto_id: str
    oggetto: str
    testo: str


_PRATICHE: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {
    "SCARTO": ("SCARTO", {
        "PROPOSTA": ("Proposta di scarto: serve l'autorizzazione dell'ente",
                     "Il conservatore ha registrato una proposta di scarto di documenti "
                     "dell'ente. Prima di qualsiasi eliminazione serve l'autorizzazione "
                     "firmata del produttore (e, per le pubbliche amministrazioni, il nulla "
                     "osta della Soprintendenza archivistica)."),
        "ESEGUITA": ("Scarto eseguito",
                     "Lo scarto autorizzato è stato eseguito. Il verbale e l'elenco dei "
                     "pacchetti eliminati sono disponibili nella console."),
    }),
    "RESTITUZIONE": ("RESTITUZIONE", {
        "PROPOSTA": ("Restituzione dei documenti: serve l'autorizzazione dell'ente",
                     "È stata proposta la restituzione di documenti conservati. Prima "
                     "dell'esportazione serve l'autorizzazione firmata del produttore."),
        "ESEGUITA": ("Restituzione eseguita",
                     "La restituzione è stata eseguita: il pacchetto di distribuzione e "
                     "l'esportazione dei registri sono disponibili nella console."),
    }),
    "RIVERSAMENTO_USCITA": ("RIVERSAMENTO", {
        "PROPOSTA": ("Riversamento verso un altro conservatore",
                     "Il conservatore ha avviato il riversamento di documenti dell'ente verso "
                     "un altro conservatore."),
        "ESEGUITA": ("Riversamento eseguito",
                     "Il riversamento verso l'altro conservatore è stato eseguito: il "
                     "manifesto è disponibile nella console."),
    }),
}

PREFISSO_OGGETTO = "[VerumDoc] "
CHIUSURA = ("\n\nRiferimento della pratica: {rif}.\nQuesta comunicazione è inviata per posta "
            "elettronica certificata dal servizio di conservazione; il dettaglio è nella "
            "console. Non rispondere a questo messaggio.")


def comunicazione(topic: str, evento: Mapping[str, Any]) -> Comunicazione | None:
    """Comunicazione formale per l'evento, o None se l'evento non ne prevede."""
    if topic == "pratica.cambio_stato":
        voce = _PRATICHE.get(str(evento.get("tipo") or ""))
        if voce is None:
            return None
        caso, stati = voce
        a_stato = str(evento.get("a_stato") or "")
        testi = stati.get(a_stato)
        pratica = str(evento.get("pratica_id") or "")
        if testi is None or not pratica:
            return None
        oggetto, testo = testi
        return Comunicazione(caso, f"{pratica}:{a_stato}", PREFISSO_OGGETTO + oggetto,
                             testo + CHIUSURA.format(rif=pratica))
    if topic == "versamento.staging_scaduto":
        rif = str(evento.get("avviso_id") or evento.get("pdv_id") or "")
        if not rif:
            return None
        return Comunicazione(
            "SCADENZE", f"staging:{rif}",
            PREFISSO_OGGETTO + "Copie di un versamento rifiutato in scadenza",
            "Le copie di transito di un versamento rifiutato stanno per essere eliminate. Se "
            "il versamento va ripetuto, rielaboralo dalla console prima della scadenza."
            + CHIUSURA.format(rif=rif))
    return None


def comunicazione_prova(destinatario: str) -> Comunicazione:
    return Comunicazione(CASO_PROVA, "", PREFISSO_OGGETTO + "Prova del canale PEC",
                         "Messaggio di prova del canale PEC del servizio di conservazione, "
                         f"inviato a {destinatario}. Non serve alcuna azione.")


__all__ = [
    "ACCETTATO", "CASI_USO", "CASO_PROVA", "CONSEGNATO", "Comunicazione", "Effetto", "FINALI",
    "INVIATO", "IN_CODA", "IN_INVIO", "MANCATA_CONSEGNA", "NON_ACCETTATO", "NON_PARTITO",
    "PASSAGGI", "STATI", "TOPIC_PEC", "comunicazione", "comunicazione_prova", "effetto_ricevuta",
]
