# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/codice_fiscale.py — codice fiscale delle persone fisiche.

Usato da:
  - gateway: associazione dell'identità SPID/CIE (claim fiscal_number) all'utente;
  - onboarding: creazione e modifica degli utenti (campo codice_fiscale).

Una sola funzione di normalizzazione e una sola di validazione (REGOLE §9.2):
il valore salvato in `public.users.codice_fiscale` e quello letto dal token
dell'aggregatore passano dalla stessa `normalizza_codice_fiscale`, quindi il
confronto è sempre fra stringhe identiche (16 caratteri maiuscoli).

Riferimento: D.M. 23/12/1976 (struttura e carattere di controllo), con le
sostituzioni per omocodia (cifre → lettere L M N P Q R S T U V). SPID e CIE
consegnano il codice fiscale con il prefisso «TINIT-» (Avviso SPID n. 6 / OIDC
federation: attributo fiscal_number), che qui si toglie.
"""
from __future__ import annotations

import re

#: Prefisso con cui SPID/CIE consegnano il codice fiscale (ETSI TS 119 412-1).
PREFISSO_SPID = "TINIT-"

_OMOCODIA = "LMNPQRSTUV"
# Lettere in 0-5, 8, 11, 15; cifre (o lettere di omocodia) in 6-7, 9-10, 12-14.
_FORMA = re.compile(
    r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$"
)
_DISPARI = {
    **dict(zip("0123456789", (1, 0, 5, 7, 9, 13, 15, 17, 19, 21), strict=True)),
    **dict(zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        (1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14,
         16, 10, 22, 25, 24, 23),
        strict=True,
    )),
}


class CodiceFiscaleNonValido(ValueError):
    """Codice fiscale di persona fisica non valido; il messaggio non contiene il valore."""


def normalizza_codice_fiscale(valore: str | None) -> str:
    """Toglie spazi e il prefisso «TINIT-», porta in maiuscolo. Non valida."""
    testo = re.sub(r"\s+", "", str(valore or "")).upper()
    if testo.startswith(PREFISSO_SPID):
        testo = testo[len(PREFISSO_SPID):]
    return testo


def carattere_di_controllo(primi_15: str) -> str:
    """Carattere di controllo dei primi 15 caratteri (D.M. 23/12/1976)."""
    somma = 0
    for posizione, carattere in enumerate(primi_15):
        if posizione % 2 == 0:  # posizioni dispari contando da 1
            somma += _DISPARI[carattere]
        else:
            somma += int(carattere) if carattere.isdigit() else ord(carattere) - ord("A")
    return chr(ord("A") + somma % 26)


def valida_codice_fiscale(valore: str | None) -> str:
    """
    Restituisce il codice fiscale normalizzato o solleva CodiceFiscaleNonValido.
    Accetta solo il codice di 16 caratteri delle persone fisiche (anche omocodico).
    """
    cf = normalizza_codice_fiscale(valore)
    if len(cf) != 16:
        raise CodiceFiscaleNonValido(
            "Il codice fiscale di una persona ha 16 caratteri"
        )
    if not _FORMA.match(cf):
        raise CodiceFiscaleNonValido("Il codice fiscale non ha la forma prevista")
    giorno = cf[9:11].translate(str.maketrans(_OMOCODIA, "0123456789"))
    if not (1 <= int(giorno) <= 31 or 41 <= int(giorno) <= 71):
        raise CodiceFiscaleNonValido("Il codice fiscale indica un giorno di nascita impossibile")
    if carattere_di_controllo(cf[:15]) != cf[15]:
        raise CodiceFiscaleNonValido("Il carattere di controllo del codice fiscale non corrisponde")
    return cf


def maschera_codice_fiscale(cf: str | None) -> str:
    """Forma da log (REGOLE §17.4): solo le ultime 4 posizioni."""
    testo = str(cf or "")
    return f"************{testo[-4:]}" if len(testo) >= 4 else "****"
