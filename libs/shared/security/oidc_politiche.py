# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/oidc_politiche.py — politiche dei metadati e vincoli della trust chain
OpenID Federation 1.0 (R16, VD-C-173).

Riferimento: OpenID Federation 1.0, §6.1 (Metadata Policy: operatori ``value``, ``add``,
``default``, ``one_of``, ``subset_of``, ``superset_of``, ``essential``; combinazione dei
livelli dal Trust Anchor verso il basso; ordine di applicazione; ``metadata_policy_crit``)
e §6.2 (Constraints: ``max_path_length``, ``naming_constraints``,
``allowed_entity_types``). Funzioni pure, provate in
``libs/shared/security/tests/test_r16_oidc_federazione.py``.

Scelte (dove la norma lascia margine, sempre nel verso più restrittivo):
  - un operatore sconosciuto si ignora, salvo che sia in ``metadata_policy_crit`` (allora la
    catena non vale);
  - ``subset_of`` che lascia un insieme vuoto toglie il parametro (e ``essential`` lo fa
    poi fallire);
  - i nomi nei ``naming_constraints`` seguono RFC 5280 per i nomi DNS: ``host.example``
    vale per quell'host, ``.example`` per ogni sottodominio.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

OPERATORI = ("value", "add", "default", "one_of", "subset_of", "superset_of", "essential")


class PoliticaNonValida(ValueError):
    """Politica, combinazione o vincolo che invalida la catena."""


def _elenco(v: Any, operatore: str) -> list[Any]:
    if not isinstance(v, list):
        raise PoliticaNonValida(f"{operatore}: atteso un elenco")
    return v


def _insieme(v: Any) -> list[Any]:
    """Valore di un parametro come elenco (scalare → elenco di un elemento)."""
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def _unione(a: Iterable[Any], b: Iterable[Any]) -> list[Any]:
    esito = list(a)
    for x in b:
        if x not in esito:
            esito.append(x)
    return esito


def _intersezione(a: Iterable[Any], b: Iterable[Any]) -> list[Any]:
    bb = list(b)
    return [x for x in a if x in bb]


def _sottoinsieme(a: Iterable[Any], b: Iterable[Any]) -> bool:
    bb = list(b)
    return all(x in bb for x in a)


def controlla_voce(nome: str, voce: Mapping[str, Any], critici: Iterable[str] = ()) -> dict:
    """Operatori noti di una voce, con i tipi e le combinazioni ammesse nello STESSO livello
    (§6.1.3); un operatore sconosciuto in ``critici`` invalida la catena."""
    critici = set(critici)
    noti: dict[str, Any] = {}
    for operatore, valore in voce.items():
        if operatore not in OPERATORI:
            if operatore in critici:
                raise PoliticaNonValida(f"{nome}: operatore critico non supportato {operatore}")
            continue
        if operatore in ("add", "one_of", "subset_of", "superset_of"):
            _elenco(valore, f"{nome}.{operatore}")
        if operatore == "essential" and not isinstance(valore, bool):
            raise PoliticaNonValida(f"{nome}.essential: atteso un booleano")
        noti[operatore] = valore
    _coerenza(nome, noti)
    return noti


def _coerenza(nome: str, v: Mapping[str, Any]) -> None:
    """Combinazioni ammesse fra operatori della stessa voce (§6.1.3.2)."""
    if "one_of" in v and ("add" in v or "subset_of" in v or "superset_of" in v):
        raise PoliticaNonValida(f"{nome}: one_of non si combina con add/subset_of/superset_of")
    if "value" in v:
        valore = v["value"]
        if valore is None:
            if v.get("essential") is True:
                raise PoliticaNonValida(f"{nome}: value nullo con essential")
            if "default" in v:
                raise PoliticaNonValida(f"{nome}: value nullo con default")
        else:
            if "add" in v and not _sottoinsieme(v["add"], _insieme(valore)):
                raise PoliticaNonValida(f"{nome}: add non contenuto in value")
            if "one_of" in v and valore not in v["one_of"]:
                raise PoliticaNonValida(f"{nome}: value fuori da one_of")
            if "subset_of" in v and not _sottoinsieme(_insieme(valore), v["subset_of"]):
                raise PoliticaNonValida(f"{nome}: value non contenuto in subset_of")
            if "superset_of" in v and not _sottoinsieme(v["superset_of"], _insieme(valore)):
                raise PoliticaNonValida(f"{nome}: value non contiene superset_of")
    if "add" in v and "subset_of" in v and not _sottoinsieme(v["add"], v["subset_of"]):
        raise PoliticaNonValida(f"{nome}: add non contenuto in subset_of")
    if "subset_of" in v and "superset_of" in v and \
            not _sottoinsieme(v["superset_of"], v["subset_of"]):
        raise PoliticaNonValida(f"{nome}: superset_of non contenuto in subset_of")
    if "default" in v and v["default"] is not None:
        d = v["default"]
        if "one_of" in v and d not in v["one_of"]:
            raise PoliticaNonValida(f"{nome}: default fuori da one_of")
        if "subset_of" in v and not _sottoinsieme(_insieme(d), v["subset_of"]):
            raise PoliticaNonValida(f"{nome}: default non contenuto in subset_of")
        if "superset_of" in v and not _sottoinsieme(v["superset_of"], _insieme(d)):
            raise PoliticaNonValida(f"{nome}: default non contiene superset_of")


def combina_voce(nome: str, sopra: Mapping[str, Any], sotto: Mapping[str, Any]) -> dict:
    """Combinazione della voce del livello superiore con quella del livello inferiore
    (§6.1.3.1): il risultato non è mai meno restrittivo del superiore."""
    esito = dict(sopra)
    for operatore, valore in sotto.items():
        if operatore not in esito:
            esito[operatore] = copy.deepcopy(valore)
            continue
        prima = esito[operatore]
        if operatore in ("value", "default"):
            if prima != valore:
                raise PoliticaNonValida(f"{nome}.{operatore}: valori diversi fra i livelli")
        elif operatore in ("add", "superset_of"):
            esito[operatore] = _unione(prima, valore)
        elif operatore == "one_of":
            comune = _intersezione(prima, valore)
            if not comune:
                raise PoliticaNonValida(f"{nome}.one_of: nessun valore comune fra i livelli")
            esito[operatore] = comune
        elif operatore == "subset_of":
            esito[operatore] = _intersezione(prima, valore)
        elif operatore == "essential":
            esito[operatore] = bool(prima) or bool(valore)
    _coerenza(nome, esito)
    return esito


def combina(politiche: Sequence[Mapping[str, Any]], critici: Iterable[str] = ()) -> dict:
    """Politiche di UN tipo d'entità, dalla più alta (Trust Anchor) alla più bassa."""
    critici = list(critici)
    esito: dict[str, dict[str, Any]] = {}
    for politica in politiche:
        if not isinstance(politica, Mapping):
            raise PoliticaNonValida("metadata_policy non è un oggetto")
        for nome, voce in politica.items():
            if not isinstance(voce, Mapping):
                raise PoliticaNonValida(f"{nome}: voce della politica non è un oggetto")
            noti = controlla_voce(nome, voce, critici)
            esito[nome] = combina_voce(nome, esito[nome], noti) if nome in esito else noti
    return esito


def applica(metadati: Mapping[str, Any], politica: Mapping[str, Mapping[str, Any]]) -> dict:
    """Applicazione nell'ordine della norma (§6.1.4.2): value, add, default, one_of,
    subset_of, superset_of, essential."""
    esito = copy.deepcopy(dict(metadati))
    for nome, v in politica.items():
        presente = nome in esito
        if "value" in v:
            if v["value"] is None:
                esito.pop(nome, None)
                presente = False
            else:
                esito[nome] = copy.deepcopy(v["value"])
                presente = True
        if "add" in v:
            esito[nome] = _unione(_insieme(esito.get(nome)), v["add"])
            presente = True
        if "default" in v and not presente and v["default"] is not None:
            esito[nome] = copy.deepcopy(v["default"])
            presente = True
        if "one_of" in v and presente and esito[nome] not in v["one_of"]:
            raise PoliticaNonValida(f"{nome}: valore {esito[nome]!r} fuori da one_of")
        if "subset_of" in v and presente:
            ristretto = _intersezione(_insieme(esito[nome]), v["subset_of"])
            if ristretto:
                esito[nome] = ristretto
            else:
                esito.pop(nome)
                presente = False
        if "superset_of" in v and presente and \
                not _sottoinsieme(v["superset_of"], _insieme(esito[nome])):
            raise PoliticaNonValida(f"{nome}: non contiene i valori richiesti da superset_of")
        if v.get("essential") is True and not presente:
            raise PoliticaNonValida(f"{nome}: parametro essenziale assente")
    return esito


# ── vincoli (§6.2) ──────────────────────────────────────────────────────

def _host(entita: str) -> str:
    return (urlsplit(entita).hostname or "").lower()


def _nome_coincide(host: str, vincolo: str) -> bool:
    vincolo = vincolo.strip().lower()
    if not vincolo:
        return False
    if vincolo.startswith("."):
        return host.endswith(vincolo) and len(host) > len(vincolo)
    return host == vincolo


def controlla_vincoli(vincoli: Any, *, livello: int, sotto: Sequence[str],
                      tipi_foglia: Iterable[str]) -> set[str]:
    """Vincoli di un Subordinate Statement emesso ``livello`` gradini sopra la foglia
    (0 = superiore diretto). ``sotto``: identificativi delle entità sotto l'emittente
    (foglia compresa). Restituisce i tipi d'entità AMMESSI per la foglia (``None`` nei
    vincoli = tutti)."""
    tipi = set(tipi_foglia)
    if vincoli is None:
        return tipi
    if not isinstance(vincoli, Mapping):
        raise PoliticaNonValida("constraints non è un oggetto")
    massimo = vincoli.get("max_path_length")
    if massimo is not None:
        if not isinstance(massimo, int) or isinstance(massimo, bool) or massimo < 0:
            raise PoliticaNonValida("max_path_length non è un intero non negativo")
        # intermedi fra l'emittente e la foglia = livello
        if livello > massimo:
            raise PoliticaNonValida(f"catena più lunga di max_path_length ({massimo})")
    nomi = vincoli.get("naming_constraints")
    if nomi is not None:
        if not isinstance(nomi, Mapping):
            raise PoliticaNonValida("naming_constraints non è un oggetto")
        ammessi = nomi.get("permitted")
        esclusi = nomi.get("excluded") or []
        for entita in sotto:
            host = _host(entita)
            if ammessi is not None and not any(_nome_coincide(host, n) for n in ammessi):
                raise PoliticaNonValida(f"{entita}: fuori dai nomi ammessi (naming_constraints)")
            if any(_nome_coincide(host, n) for n in esclusi):
                raise PoliticaNonValida(f"{entita}: fra i nomi esclusi (naming_constraints)")
    ammessi_tipi = vincoli.get("allowed_entity_types")
    if ammessi_tipi is not None:
        if not isinstance(ammessi_tipi, list):
            raise PoliticaNonValida("allowed_entity_types non è un elenco")
        tipi &= set(ammessi_tipi) | {"federation_entity"}
    return tipi


__all__ = ["OPERATORI", "PoliticaNonValida", "applica", "combina", "combina_voce",
           "controlla_vincoli", "controlla_voce"]
