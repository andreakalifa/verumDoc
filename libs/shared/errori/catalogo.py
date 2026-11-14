# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Catalogo dei codici d'errore (VD-C-100): caricamento e ricerca.

La fonte di verità è ``catalogo.yaml``; i servizi leggono la sua copia Python
``_catalogo_dati.py`` (generata da ``tools/docs/genera_catalogo_errori.py``),
così a runtime non serve un parser YAML. Questo modulo non importa FastAPI.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from libs.shared.errori.schema import (
    CatalogoNonValido,
    Esito,
    Famiglia,
    VoceCatalogo,
    valida_catalogo,
)

__all__ = ["CODICI", "FAMIGLIE", "VERSIONE_CATALOGO", "CatalogoNonValido", "Esito", "Famiglia",
           "VoceCatalogo", "cerca", "codice_generico", "valida_catalogo", "voce"]


def _costruisci(dati: Mapping[str, Any]) -> tuple[
    dict[str, Famiglia], dict[str, VoceCatalogo], dict[str, str],
]:
    problemi = valida_catalogo(dati)
    if problemi:
        raise CatalogoNonValido(problemi)
    famiglie = {s: Famiglia(s, str(f["descrizione"]).strip())
                for s, f in dati["famiglie"].items()}
    codici: dict[str, VoceCatalogo] = {}
    alias: dict[str, str] = {}
    for codice, v in dati["codici"].items():
        codici[codice] = VoceCatalogo(
            codice=codice, famiglia=v["famiglia"], status=int(v["status"]),
            titolo=str(v["titolo"]).strip(), descrizione=str(v["descrizione"]).strip(),
            azione=str(v["azione"]).strip(), esito=Esito(v["esito"]),
            ritentabile=bool(v["ritentabile"]), retry_after=v.get("retry_after"),
            contesto=v.get("contesto", "http"), alias=tuple(v.get("alias") or ()),
            dismesso=bool(v.get("dismesso", False)),
            sostituito_da=tuple(v.get("sostituito_da") or ()),
        )
        for a in v.get("alias") or ():
            alias[str(a)] = codice
    return famiglie, codici, alias


def _carica() -> tuple[str, dict[str, Famiglia], dict[str, VoceCatalogo], dict[str, str]]:
    from libs.shared.errori._catalogo_dati import DATI

    return (DATI["versione"], *_costruisci(DATI))


VERSIONE_CATALOGO, _FAMIGLIE, _CODICI, _ALIAS = _carica()

#: Viste in sola lettura.
FAMIGLIE: Mapping[str, Famiglia] = MappingProxyType(_FAMIGLIE)
CODICI: Mapping[str, VoceCatalogo] = MappingProxyType(_CODICI)


def voce(codice: str) -> VoceCatalogo:
    """La voce del catalogo; KeyError esplicito se il codice non c'è."""
    try:
        return _CODICI[codice]
    except KeyError:
        raise KeyError(
            f"codice d'errore {codice!r} non presente nel catalogo "
            "(aggiungerlo a libs/shared/errori/catalogo.yaml e rigenerare)"
        ) from None


def cerca(codice: str | None) -> VoceCatalogo | None:
    """La voce per codice o per alias del vecchio formato; None se sconosciuto."""
    if not codice:
        return None
    trovata = _CODICI.get(codice)
    if trovata is None and codice in _ALIAS:
        trovata = _CODICI[_ALIAS[codice]]
    return trovata


def codice_generico(status: int) -> str:
    """Codice generico per uno stato HTTP senza codice di dominio (HTTP-xxx)."""
    candidato = f"HTTP-{status}"
    if candidato in _CODICI:
        return candidato
    return "HTTP-500" if status >= 500 else "HTTP-400"
