# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/permessi.py — catalogo dei permessi e ruoli modello (R10, D30).

Riferimenti: decisione D30 (VD-P-38, VD-P-14), REGOLE_SVILUPPO §14.1 (un cambio
di permessi è una decisione), §14.8 (il ruolo dà il perimetro, il permesso
l'azione), §8 (regole come dati). Pacchetto PURO: nessun accesso al database.

  - ``PERMESSI``: azioni con una descrizione in parole semplici;
  - ``CONCEDIBILI_POSSIBILI``: permessi che un ruolo personalizzato può avere.
    Quali lo sono davvero per un ente lo stabilisce il conservatore con il
    parametro ``RUOLI_PERMESSI_CONCEDIBILI`` (sottoinsieme, anche per ente);
  - ``MAI_DELEGABILI``: azioni che restano SOLO ai ruoli fissi (mai in un ruolo
    personalizzato, nemmeno se il conservatore lo volesse: il formato del
    parametro e un vincolo del database li escludono): esecuzione dello scarto,
    autorizzazione dello scarto, approvazione dell'annullamento con doppia
    approvazione, gestione dei firmatari autorizzati, «chiudi con eccezioni»,
    gestione di utenti, ruoli e metodi d'accesso;
  - ``MODELLI``: i ruoli fissi come modelli (permessi di ciascuno);
  - ``RUOLI_BASE``: ruoli fissi su cui si costruisce un ruolo personalizzato
    (perimetro). Mai ADMIN_TENANT: l'amministratore non crea amministratori.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

CONSULTA = "archivio:consulta"
VERSA = "versamento:esegui"
ESIBIZIONE = "esibizione:richiedi"
RETTIFICA = "rettifica:richiedi"
ANNULLAMENTO = "annullamento:richiedi"

SCARTO_ESEGUI = "scarto:esegui"
SCARTO_AUTORIZZA = "scarto:autorizza"
ANNULLAMENTO_APPROVA = "annullamento:approva"
FIRMATARI_GESTISCI = "firmatari:gestisci"
CHIUDI_ECCEZIONI = "pratica:chiudi_eccezioni"
UTENTI_GESTISCI = "utenti:gestisci"
RUOLI_GESTISCI = "ruoli:gestisci"
ACCESSO_CONFIGURA = "accesso:configura"
#: R13a: registro dei fornitori di firma e marca qualificate (solo il conservatore)
FIRMA_FORNITORI_GESTISCI = "firma_fornitori:gestisci"
#: R13b: canali PEC del conservatore (registro, prova di invio, ricevute)
PEC_CANALI_GESTISCI = "pec_canali:gestisci"
#: Revisione R14 (4): riprendere una pratica sospesa dopo i tentativi esauriti
PRATICA_RIPRENDI = "pratica:riprendi"
#: R15 (VD-C-177): generare, scaricare e consultare la documentazione di conformità del
#: servizio (manuale, piano della sicurezza, guida, catalogo delle pagine)
DOCUMENTAZIONE_GENERA = "documentazione:genera"

#: codice → descrizione in parole semplici (console e manuale)
PERMESSI: dict[str, str] = {
    CONSULTA: "Vedere i pacchetti dell'ente, il loro stato e i rapporti di versamento",
    VERSA: "Versare pacchetti (API, SFTP, portale)",
    ESIBIZIONE: "Chiedere pacchetti di distribuzione ed esibizioni",
    RETTIFICA: "Chiedere la rettifica dei metadati di un versamento",
    ANNULLAMENTO: "Chiedere l'annullamento di un versamento",
    SCARTO_ESEGUI: "Eseguire lo scarto (solo il conservatore)",
    SCARTO_AUTORIZZA: "Autorizzare lo scarto come produttore (solo l'amministratore dell'ente)",
    ANNULLAMENTO_APPROVA: "Approvare un annullamento come seconda persona "
                          "(solo l'amministratore dell'ente)",
    FIRMATARI_GESTISCI: "Gestire i firmatari autorizzati (solo il conservatore)",
    CHIUDI_ECCEZIONI: "Chiudere uno scarto con eccezioni (solo il conservatore)",
    UTENTI_GESTISCI: "Invitare e gestire gli utenti dell'ente",
    RUOLI_GESTISCI: "Creare e assegnare ruoli personalizzati",
    ACCESSO_CONFIGURA: "Scegliere i metodi d'accesso dell'ente",
    FIRMA_FORNITORI_GESTISCI: ("Registrare e provare i fornitori di firma, sigillo e marca "
                               "temporale qualificati (solo il conservatore)"),
    PEC_CANALI_GESTISCI: ("Registrare e provare i canali PEC del conservatore e leggerne "
                          "le ricevute (solo il conservatore)"),
    PRATICA_RIPRENDI: ("Riprendere una pratica ferma dopo ripetuti errori del servizio "
                       "(solo il conservatore)"),
    DOCUMENTAZIONE_GENERA: ("Generare e scaricare il manuale di conservazione, il piano della "
                            "sicurezza e la documentazione del servizio (solo il conservatore)"),
}

MAI_DELEGABILI: frozenset[str] = frozenset({
    SCARTO_ESEGUI, SCARTO_AUTORIZZA, ANNULLAMENTO_APPROVA, FIRMATARI_GESTISCI,
    CHIUDI_ECCEZIONI, UTENTI_GESTISCI, RUOLI_GESTISCI, ACCESSO_CONFIGURA,
    FIRMA_FORNITORI_GESTISCI, PEC_CANALI_GESTISCI, PRATICA_RIPRENDI, DOCUMENTAZIONE_GENERA,
})
#: In ordine di console: tutti i permessi che NON sono mai delegabili.
CONCEDIBILI_POSSIBILI: tuple[str, ...] = tuple(p for p in PERMESSI if p not in MAI_DELEGABILI)
#: Ogni ruolo personalizzato vede l'archivio del proprio ente (perimetro del ruolo base).
SEMPRE_INCLUSI: frozenset[str] = frozenset({CONSULTA})

RUOLI_CONSERVATORE: frozenset[str] = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})
RUOLI_BASE: tuple[str, ...] = ("OPERATORE", "VIEWER")

#: Ruoli fissi come modelli. I ruoli del conservatore hanno ogni permesso.
MODELLI: dict[str, frozenset[str]] = {
    "SUPER_ADMIN": frozenset(PERMESSI),
    "RSC_CONSERVATORE": frozenset(PERMESSI),
    "ADMIN_TENANT": frozenset({CONSULTA, VERSA, ESIBIZIONE, RETTIFICA, ANNULLAMENTO,
                               SCARTO_AUTORIZZA, ANNULLAMENTO_APPROVA,
                               UTENTI_GESTISCI, RUOLI_GESTISCI, ACCESSO_CONFIGURA}),
    "OPERATORE": frozenset({CONSULTA, VERSA, ESIBIZIONE, RETTIFICA, ANNULLAMENTO}),
    "VIEWER": frozenset({CONSULTA}),
    "API_SYSTEM": frozenset({CONSULTA, VERSA}),
}

#: Scope dei token (gateway, servizi) dati da ciascun permesso concedibile.
SCOPE_DEL_PERMESSO: dict[str, tuple[str, ...]] = {
    CONSULTA: ("read",),
    VERSA: ("versamento:ud", "versamento:doc"),
    ESIBIZIONE: ("distribution",),
    RETTIFICA: ("lifecycle",),
    ANNULLAMENTO: ("lifecycle",),
}

#: Espressione regolare dei codici ammessi nel parametro dei concedibili.
FORMATO_CONCEDIBILE = "(?:" + "|".join(re.escape(c) for c in CONCEDIBILI_POSSIBILI) + ")"


def permessi_del_modello(ruolo: str | None) -> frozenset[str]:
    """Permessi del ruolo fisso; ruolo sconosciuto: nessuno (§14.2)."""
    return MODELLI.get(str(ruolo or ""), frozenset())


def scope_dei_permessi(permessi: Iterable[str]) -> list[str]:
    """Scope dei token per un insieme di permessi (ordine stabile, senza ripetizioni)."""
    scope: list[str] = []
    for p in sorted(set(permessi)):
        for s in SCOPE_DEL_PERMESSO.get(p, ()):
            if s not in scope:
                scope.append(s)
    return scope


def motivo_composizione_non_ammessa(*, ruolo_chiamante: str, permessi_chiamante: Iterable[str],
                                    ruolo_base: str, richiesti: Iterable[str],
                                    concedibili: Iterable[str]) -> str | None:
    """``None`` se chi compone (o assegna) il ruolo può farlo; altrimenti il motivo.

    Nessuna escalation: ogni permesso deve essere concedibile per l'ente E già
    posseduto da chi compone; mai un permesso non delegabile; base solo
    OPERATORE o VIEWER (l'amministratore non crea amministratori)."""
    richiesti = set(richiesti)
    if ruolo_base not in RUOLI_BASE:
        return f"Un ruolo personalizzato si costruisce su {' o '.join(RUOLI_BASE)}"
    ignoti = sorted(richiesti - set(PERMESSI))
    if ignoti:
        return "Permessi sconosciuti: " + ", ".join(ignoti)
    vietati = sorted(richiesti & MAI_DELEGABILI)
    if vietati:
        return "Questi permessi restano solo ai ruoli fissi: " + ", ".join(vietati)
    non_concedibili = sorted(richiesti - set(concedibili) - SEMPRE_INCLUSI)
    if non_concedibili:
        return "Il conservatore non consente di concedere: " + ", ".join(non_concedibili)
    if ruolo_chiamante not in RUOLI_CONSERVATORE:
        mancanti = sorted(richiesti - set(permessi_chiamante))
        if mancanti:
            return "Non puoi concedere permessi che non hai: " + ", ".join(mancanti)
    return None


__all__ = [
    "ANNULLAMENTO", "ANNULLAMENTO_APPROVA", "CHIUDI_ECCEZIONI", "CONCEDIBILI_POSSIBILI",
    "CONSULTA", "DOCUMENTAZIONE_GENERA", "ESIBIZIONE", "FIRMATARI_GESTISCI",
    "FIRMA_FORNITORI_GESTISCI",
    "PEC_CANALI_GESTISCI", "PRATICA_RIPRENDI",
    "FORMATO_CONCEDIBILE", "MAI_DELEGABILI",
    "MODELLI", "PERMESSI", "RETTIFICA", "RUOLI_BASE", "RUOLI_CONSERVATORE",
    "SCARTO_AUTORIZZA", "SCARTO_ESEGUI", "SEMPRE_INCLUSI", "VERSA",
    "motivo_composizione_non_ammessa", "permessi_del_modello", "scope_dei_permessi",
]
