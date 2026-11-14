# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme_responsabile.py — punto d'aggancio GENERICO «documento da firmare dal
responsabile» (R16, VD-C-166, VD-P-140).

Qualunque servizio che produce un documento che il responsabile del servizio di
conservazione deve firmare PERSONALMENTE (manuale, piano di cessazione, nomine, o un tipo
nuovo: R15 produrrà manuale e piano) lo accoda con ``accoda_documento`` nella propria
transazione; la firma la appone la persona dalla console (pagina «Firme del
responsabile») con l'OTP del prestatore, e la hashchain archivia la busta nel WORM.

Stati (tabella ``firme_responsabile``, migrazione 20261110_057, trigger che li ribadisce):

  DA_FIRMARE ──firma con OTP (dashboard-api)──► FIRMATO ──hashchain──► ARCHIVIATO
      │                                            └──riverifica fallita──► RESPINTO
      └──annulla──► ANNULLATO

Il ``tipo`` è un codice in maiuscolo (``^[A-Z][A-Z0-9_]{1,29}$``); quelli noti hanno una
descrizione per la console, gli altri si mostrano con il titolo del documento.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import text

DA_FIRMARE = "DA_FIRMARE"
FIRMATO = "FIRMATO"
ARCHIVIATO = "ARCHIVIATO"
RESPINTO = "RESPINTO"
ANNULLATO = "ANNULLATO"
STATI = (DA_FIRMARE, FIRMATO, ARCHIVIATO, RESPINTO, ANNULLATO)
#: passaggi ammessi, uguali a quelli del trigger della migrazione 057 (test di coerenza)
PASSAGGI: frozenset[tuple[str, str]] = frozenset({
    (DA_FIRMARE, FIRMATO), (DA_FIRMARE, ANNULLATO), (FIRMATO, ARCHIVIATO), (FIRMATO, RESPINTO),
})
#: tipi con una descrizione per la console (gli altri sono ammessi comunque)
TIPI_NOTI: dict[str, str] = {
    "MANUALE": "Manuale di conservazione",
    "PIANO": "Piano di cessazione",
    "PIANO_SICUREZZA": "Piano della sicurezza",
    "NOMINA": "Nomina o delega",
    "DOCUMENTO": "Documento del conservatore",
}
FORMATO_TIPO = re.compile(r"^[A-Z][A-Z0-9_]{1,29}$")
FORMATO_NOME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
CONTENUTO_MASSIMO = 5 * 1024 * 1024
#: revisione R17: documenti che firma SOLO il responsabile del servizio di conservazione
#: (ruolo fisso RSC_CONSERVATORE), qualunque sia il firmatario atteso
TIPI_DEL_RESPONSABILE = frozenset({"MANUALE", "PIANO", "PIANO_SICUREZZA"})
PREFISSO_WORM = "registro/documenti/responsabile"


class DocumentoNonValido(ValueError):
    """Tipo, titolo, nome o contenuto non ammessi."""


def controlla(tipo: str, titolo: str, nome: str, contenuto: bytes) -> None:
    if not FORMATO_TIPO.match(tipo or ""):
        raise DocumentoNonValido("tipo: codice in maiuscolo (lettere, cifre, _), 2–30 caratteri")
    if not (titolo or "").strip() or len(titolo) > 300:
        raise DocumentoNonValido("titolo obbligatorio, al più 300 caratteri")
    if not FORMATO_NOME.match(nome or "") or ".." in nome:
        raise DocumentoNonValido("nome del file: lettere, cifre, punto, trattino; niente '..'")
    if not contenuto or len(contenuto) > CONTENUTO_MASSIMO:
        raise DocumentoNonValido(f"contenuto non vuoto, al più {CONTENUTO_MASSIMO} byte")


async def accoda_documento(session: Any, *, tipo: str, titolo: str, nome: str,
                           contenuto: bytes, richiesto_da: str | None,
                           firmatario_atteso: str | None = None) -> tuple[str, bool]:
    """Mette il documento fra quelli da firmare dal responsabile (nella transazione di
    ``session``). Idempotente per (tipo, impronta) finché il documento non è annullato o
    respinto: restituisce (id, nuovo)."""
    controlla(tipo, titolo, nome, contenuto)
    impronta = hashlib.sha256(contenuto).hexdigest()
    nuovo = (await session.execute(text(
        "INSERT INTO public.firme_responsabile (tipo, titolo, nome, contenuto, impronta_sha256, "
        " richiesto_da, firmatario_atteso) VALUES (:t, :ti, :n, :c, :h, CAST(:r AS uuid), "
        " CAST(:f AS uuid)) ON CONFLICT (tipo, impronta_sha256) "
        "WHERE stato NOT IN ('ANNULLATO', 'RESPINTO') DO NOTHING RETURNING id::text"),
        {"t": tipo, "ti": titolo.strip(), "n": nome, "c": contenuto, "h": impronta,
         "r": _uuid(richiesto_da), "f": _uuid(firmatario_atteso)})).scalar_one_or_none()
    if nuovo is not None:
        return str(nuovo), True
    esistente = (await session.execute(text(
        "SELECT id::text FROM public.firme_responsabile WHERE tipo = :t AND impronta_sha256 = :h "
        "AND stato NOT IN ('ANNULLATO', 'RESPINTO')"), {"t": tipo, "h": impronta})).scalar_one()
    return str(esistente), False


def chiave_worm(tipo: str, impronta_contenuto: str, busta: bytes) -> str:
    """Chiave della busta nel WORM: impronta del documento e prima parte dell'impronta della
    busta (una firma nuova dopo un crash non collide con una busta orfana, §31 R13a)."""
    return (f"{PREFISSO_WORM}/{tipo.lower()}/{impronta_contenuto}."
            f"{hashlib.sha256(busta).hexdigest()[:24]}.p7m")


def _uuid(valore: Any) -> str | None:
    if valore in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(valore)))
    except ValueError:
        return None


__all__ = ["ANNULLATO", "ARCHIVIATO", "CONTENUTO_MASSIMO", "DA_FIRMARE", "FIRMATO", "PASSAGGI",
           "RESPINTO", "STATI", "TIPI_DEL_RESPONSABILE", "TIPI_NOTI", "DocumentoNonValido",
           "accoda_documento",
           "chiave_worm", "controlla"]
