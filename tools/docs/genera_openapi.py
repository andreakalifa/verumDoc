#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Genera l'OpenAPI 3.1 UNICA dei servizi pubblici VerumDoc (VD-C-100, piano R03)
e il catalogo degli endpoint:

- ``docs/catalogo/openapi.yaml``: unione delle OpenAPI di gateway, distribution,
  notification, onboarding, dashboard-api, hashchain e ciclo-vita (R05), con
  - i percorsi come li vede l'ingress (``infra/helm/values.yaml``,
    ``ingress.routes``: le app non riscrivono i percorsi); ogni operazione dice a
    quale servizio la manda oggi l'ingress (``x-verumdoc-ingress``);
  - gli schemi di sicurezza (``bearerJWT``, ``chiaveApi``, ``chiaveApiId`` +
    ``chiaveApiSegreto``, ``voucherPDND``) RICAVATI DALLE DIPENDENZE REALI di
    ogni rotta (R03 f2, REV-10): le dipendenze d'autenticazione note dicono
    quali credenziali accettano e quali ruoli chiedono (attributo
    ``__verumdoc_autenticazione__`` della dipendenza, oppure
    ``AUTENTICAZIONE_DIPENDENZE``); una rotta che legge da sé solo
    ``Authorization`` accetta solo il JWT; il risultato è l'intersezione di
    tutte le dipendenze che decidono. Le rotte che inoltrano la richiesta a un
    altro servizio con le credenziali del chiamante (``INOLTRI``, o
    ``x-verumdoc-inoltra`` nella rotta) prendono anche le regole di quel
    servizio. Solo se nessuna dipendenza nota decide si deduce dalle
    intestazioni dichiarate (comportamento precedente);
  - tutte le risposte d'errore in ``application/problem+json`` che rimandano allo
    schema comune ``Problema`` (``libs/shared/errori``);
  - i webhook (contratto C3) nella sezione ``webhooks``;
- ``docs/catalogo/endpoint.md``: tabella per servizio, instradamenti da
  correggere, operazioni oscurate, operazioni senza autenticazione dichiarata.

Le app FastAPI si importano davvero (ENV=test), ognuna in un processo separato
con ambiente minimo e cartella di lavoro vuota: il risultato non dipende dalle
variabili o dal ``.env`` di chi lancia lo strumento.

Uso (dalla radice del repository)::

    python tools/docs/genera_openapi.py             # rigenera
    python tools/docs/genera_openapi.py --verifica  # 1 se i file non sono aggiornati
    python tools/docs/genera_openapi.py --ingress   # 1 se un'operazione non è instradata

Richiede PyYAML e le dipendenze dei servizi (lo stesso ambiente dei test).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RADICE = Path(__file__).resolve().parents[2]
VALUES_HELM = RADICE / "infra/helm/values.yaml"
OUT_YAML = RADICE / "docs/catalogo/openapi.yaml"
OUT_MD = RADICE / "docs/catalogo/endpoint.md"

#: Servizi pubblici, nell'ordine di priorità per gli schemi omonimi.
SERVIZI: tuple[str, ...] = (
    "gateway", "distribution", "notification", "onboarding", "dashboard-api", "hashchain",
    "ciclo-vita")
#: Versione del contratto pubblico (allineata alla major dell'SDK Python).
VERSIONE_API = "3.0.0"
SCHEMA_PROBLEMA = "Problema"
MEDIA_PROBLEMA = "application/problem+json"
METODI = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

#: Credenziali accettate e ruoli delle dipendenze d'autenticazione note che non
#: possono dichiararlo da sé (librerie e servizi fuori dal perimetro), per
#: ``<servizio>:<modulo>.<qualname>``. «metodi»: «jwt», «chiave_api».
AUTENTICAZIONE_DIPENDENZE: dict[str, dict[str, Any]] = {
    # libs/shared/security/autenticazione_servizi.crea_dipendenza_utente
    # (distribution, dashboard-api): Bearer JWT o chiave API
    "*:libs.shared.security.autenticazione_servizi.crea_dipendenza_utente.<locals>."
    "utente_corrente": {"metodi": ["jwt", "chiave_api"]},
    # gateway: JWT utente, chiave API (e token di canale, interno: non pubblicato)
    "gateway:app.auth.dependencies.get_current_user": {"metodi": ["jwt", "chiave_api"]},
}
#: Operazioni che inoltrano la richiesta, con le credenziali del chiamante, a
#: un'operazione di un altro servizio: valgono anche le regole di quella.
#: R18.2: vuoto (l'unico inoltro era di un adattatore di compatibilità tolto); il
#: meccanismo resta per le rotte future e ha i suoi test.
INOLTRI: dict[tuple[str, str], tuple[str, str, str]] = {}

_INTESTAZIONI_BEARER = {"authorization"}
_INTESTAZIONI_CHIAVE = {"x-api-key", "x-api-key-id", "x-api-key-secret"}
_SCHEMI_VALIDAZIONE_FASTAPI = {"HTTPValidationError", "ValidationError"}

SECURITY_SCHEMES: dict[str, Any] = {
    "bearerJWT": {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": "Token RS256 dell'utente (accesso SPID/CIE o di sviluppo): "
                       "`Authorization: Bearer <token>`.",
    },
    "chiaveApi": {
        "type": "apiKey", "in": "header", "name": "X-Api-Key",
        "description": "Chiave API completa del sistema versante (`vd_<id>_<segreto>`).",
    },
    "chiaveApiId": {
        "type": "apiKey", "in": "header", "name": "X-Api-Key-Id",
        "description": "Identificativo della chiave API (`vd_<id>`), insieme a "
                       "`X-Api-Key-Secret`.",
    },
    "chiaveApiSegreto": {
        "type": "apiKey", "in": "header", "name": "X-Api-Key-Secret",
        "description": "Segreto della chiave API, insieme a `X-Api-Key-Id`.",
    },
    "voucherPDND": {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": "Voucher emesso dalla PDND per il fruitore dell'e-service "
                       "(solo `/api/pdnd/**`).",
    },
}

_REF_PROBLEMA = f"#/components/schemas/{SCHEMA_PROBLEMA}"

RISPOSTE_COMUNI: dict[str, Any] = {
    "ErroreRichiesta": {
        "description": "Errore della richiesta: problem+json del catalogo errori "
                       "(`esito` NON_VALIDO o RIFIUTATO, oppure ERRORE ritentabile come "
                       "429 con `Retry-After`).",
        "headers": {
            "X-Request-ID": {"$ref": "#/components/headers/IdRichiesta"},
            "Retry-After": {"$ref": "#/components/headers/RetryAfter"},
        },
        "content": {MEDIA_PROBLEMA: {"schema": {"$ref": _REF_PROBLEMA}}},
    },
    "ErroreServizio": {
        "description": "Errore del servizio: problem+json del catalogo errori (`esito` "
                       "ERRORE; ripetere solo se `ritentabile`).",
        "headers": {
            "X-Request-ID": {"$ref": "#/components/headers/IdRichiesta"},
            "Retry-After": {"$ref": "#/components/headers/RetryAfter"},
        },
        "content": {MEDIA_PROBLEMA: {"schema": {"$ref": _REF_PROBLEMA}}},
    },
}

INTESTAZIONI_COMUNI: dict[str, Any] = {
    "IdRichiesta": {"description": "Identificativo della richiesta (anche in `id_richiesta`)",
                    "schema": {"type": "string"}},
    "RetryAfter": {"description": "Secondi dopo cui ripetere (codici ritentabili)",
                   "schema": {"type": "string"}},
}

DESCRIZIONE = """\
API pubbliche di VerumDoc, servizio di conservazione digitale (Linee guida AgID).
Documento GENERATO da `tools/docs/genera_openapi.py` importando le app FastAPI dei
servizi: non modificarlo a mano.

- **Percorsi**: quelli esposti dall'ingress (`api.verumdoc.it`). `x-verumdoc-servizio`
  indica il servizio che realizza l'operazione, `x-verumdoc-ingress` quello a cui
  l'ingress oggi la instrada (vedi `docs/catalogo/endpoint.md`).
- **Errori**: sempre `application/problem+json` (RFC 9457) con `codice`, `esito`
  (NON_VALIDO, RIFIUTATO, ERRORE) e `ritentabile`; elenco in `docs/catalogo/errori.md`.
- **Idempotenza**: le richieste che creano un pacchetto o una sessione accettano
  `Idempotency-Key` (8-200 caratteri `[A-Za-z0-9._:-]`): stessa chiave, stessa risposta.
- **Webhook**: sezione `webhooks`; firma `VerumDoc-Firma` verificabile come in
  `docs/integrazione/webhook.md`.
"""


# ── Estrazione dalle app (processo figlio) ──────────────────────────

def _estrai_nel_processo(servizio: str, uscita: Path) -> None:
    """Eseguito nel processo figlio: importa l'app del servizio e scrive la sua OpenAPI."""
    os.environ["ENV"] = "test"
    os.environ.pop("VERUMDOC_DEV_AUTH", None)
    sys.path[:0] = [str(RADICE / "services" / servizio), str(RADICE)]
    import importlib

    principale = importlib.import_module("app.main")
    dati: dict[str, Any] = {"openapi": principale.app.openapi(),
                            "autenticazione": autenticazione_rotte(principale.app, servizio)}
    if servizio == "notification":
        eventi = importlib.import_module("app.domain.eventi")
        dati["eventi"] = [{"tipo": t.tipo, "risorsa": t.risorsa_tipo,
                           "descrizione": t.descrizione}
                          for t in sorted(eventi.TIPI_EVENTO.values(), key=lambda t: t.tipo)]
    from libs.shared.errori import Problema

    dati["problema"] = Problema.model_json_schema(ref_template="#/components/schemas/{model}")
    uscita.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")


def _regole_dipendenza(funzione: Any, servizio: str) -> dict[str, Any] | None:
    """Regole d'autenticazione dichiarate da una dipendenza, o note, o None."""
    dichiarate = getattr(funzione, "__verumdoc_autenticazione__", None)
    if isinstance(dichiarate, dict):
        return dichiarate
    nome = f"{getattr(funzione, '__module__', '')}.{getattr(funzione, '__qualname__', '')}"
    return (AUTENTICAZIONE_DIPENDENZE.get(f"{servizio}:{nome}")
            or AUTENTICAZIONE_DIPENDENZE.get(f"*:{nome}"))


def _decisori(dipendente: Any, servizio: str, *, radice: bool) -> list[dict[str, Any]]:
    """Dipendenze che decidono l'autenticazione di una rotta (albero completo)."""
    trovati: list[dict[str, Any]] = []
    for figlio in dipendente.dependencies:
        regole = _regole_dipendenza(figlio.call, servizio)
        if regole is not None:
            trovati.append({**regole, "da": getattr(figlio.call, "__qualname__", "?")})
        trovati += _decisori(figlio, servizio, radice=False)
    if radice:
        proprie = {str(getattr(p, "alias", "") or p.name).lower()
                   for p in dipendente.header_params}
        if proprie & _INTESTAZIONI_BEARER and not proprie & _INTESTAZIONI_CHIAVE:
            # la rotta verifica da sé il Bearer (es. _require_role(authorization)):
            # una chiave API, accettata da un'altra dipendenza, qui non basta
            trovati.append({"metodi": ["jwt"], "da": "rotta"})
    return trovati


def _rotte_effettive(app: Any) -> list[tuple[str, set[str], Any]]:
    """(percorso, metodi, dipendente) di ogni rotta API, anche dei router inclusi
    (FastAPI ≥ 0.150 li tiene come ``_IncludedRouter`` con il contesto effettivo:
    prefisso e dipendenze del router)."""
    from fastapi.routing import APIRoute

    try:
        from fastapi.routing import _iter_routes_with_context
    except ImportError:  # FastAPI precedente: rotte già appiattite
        return [(r.path, set(r.methods or ()), r.dependant) for r in app.routes
                if isinstance(r, APIRoute)]
    risultato = []
    for originale, contesto in _iter_routes_with_context(app.routes):
        oggetto = contesto if contesto is not None else originale
        dipendente = getattr(oggetto, "dependant", None)
        metodi = getattr(oggetto, "methods", None)
        if dipendente is None or not metodi or not isinstance(originale, APIRoute):
            continue
        risultato.append((str(getattr(oggetto, "path", "")), set(metodi), dipendente))
    return risultato


def autenticazione_rotte(app: Any, servizio: str) -> dict[str, dict[str, Any]]:
    """Per ogni «metodo percorso» dell'app: credenziali accettate e ruoli, come
    intersezione delle dipendenze che decidono (vuoto se nessuna è nota)."""
    risultato: dict[str, dict[str, Any]] = {}
    for percorso, metodi_http, dipendente in _rotte_effettive(app):
        decisori = _decisori(dipendente, servizio, radice=True)
        if not decisori:
            continue
        metodi: set[str] = set(decisori[0]["metodi"])
        ruoli: set[str] | None = None
        for d in decisori:
            metodi &= set(d["metodi"])
            if d.get("ruoli"):
                ruoli = set(d["ruoli"]) if ruoli is None else ruoli & set(d["ruoli"])
        for metodo in metodi_http:
            risultato[f"{metodo.lower()} {percorso}"] = {
                "metodi": sorted(metodi), "ruoli": sorted(ruoli) if ruoli is not None else None}
    return risultato


def estrai_servizio(servizio: str) -> dict[str, Any]:
    """OpenAPI dell'app ``servizio`` (processo separato, ambiente minimo)."""
    with tempfile.TemporaryDirectory(prefix="vd-openapi-") as cartella:
        uscita = Path(cartella) / "openapi.json"
        ambiente = {"PATH": os.environ.get("PATH", ""), "ENV": "test", "HOME": cartella,
                    "LANG": "C.UTF-8"}
        # Interprete e script di questo repository, argomenti costanti: nessun input esterno
        esito = subprocess.run(  # noqa: S603
            [sys.executable, str(Path(__file__).resolve()), "--estrai", servizio, str(uscita)],
            cwd=cartella, env=ambiente, capture_output=True, text=True, timeout=300, check=False)
        if esito.returncode != 0 or not uscita.exists():
            raise SystemExit(f"Importazione dell'app {servizio} fallita:\n"
                             f"{esito.stderr[-3000:]}")
        return json.loads(uscita.read_text(encoding="utf-8"))


# ── Ingress ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rotta:
    prefisso: str
    servizio: str


def rotte_ingress(percorso: Path = VALUES_HELM) -> tuple[str, list[Rotta]]:
    """Host dell'API e rotte dell'ingress valide per quell'host (``ingress.routes``)."""
    dati = yaml.safe_load(percorso.read_text(encoding="utf-8"))
    ingress = dati["ingress"]
    host = ingress["hosts"]["api"]
    rotte = [Rotta(r["path"], r["service"]) for r in ingress["routes"]
             if r.get("onlyHost") in (None, "api")]
    return host, rotte


def _segmenti(percorso: str) -> list[str]:
    return [s for s in percorso.split("/") if s]


def servizio_instradato(percorso: str, rotte: Iterable[Rotta]) -> str | None:
    """Servizio che riceve ``percorso`` (pathType Prefix: per segmenti, vince il più lungo)."""
    segmenti = _segmenti(percorso)
    migliore: tuple[int, str] | None = None
    for r in rotte:
        prefisso = _segmenti(r.prefisso)
        if segmenti[: len(prefisso)] == prefisso and (
                migliore is None or len(prefisso) > migliore[0]):
            migliore = (len(prefisso), r.servizio)
    return migliore[1] if migliore else None


# ── Unione ──────────────────────────────────────────────────────────

def _canonico(valore: Any) -> str:
    return json.dumps(valore, sort_keys=True, ensure_ascii=False)


def _prefisso_servizio(servizio: str) -> str:
    return "".join(p.capitalize() for p in re.split(r"[-_]", servizio))


def _riscrivi_ref(nodo: Any, mappa: Mapping[str, str]) -> Any:
    """Copia di ``nodo`` con i ``$ref`` agli schemi rinominati."""
    if isinstance(nodo, dict):
        nuovo = {}
        for k, v in nodo.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/components/schemas/"):
                nome = v.rsplit("/", 1)[1]
                nuovo[k] = f"#/components/schemas/{mappa.get(nome, nome)}"
            else:
                nuovo[k] = _riscrivi_ref(v, mappa)
        return nuovo
    if isinstance(nodo, list):
        return [_riscrivi_ref(v, mappa) for v in nodo]
    return nodo


def _ref_usati(nodo: Any, trovati: set[str]) -> None:
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/components/schemas/"):
                trovati.add(v.rsplit("/", 1)[1])
            else:
                _ref_usati(v, trovati)
    elif isinstance(nodo, list):
        for v in nodo:
            _ref_usati(v, trovati)


def rinomina_schemi(schemi_comuni: Mapping[str, Any], per_servizio: Mapping[str, Mapping[str, Any]],
                    ) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Schemi uniti e, per servizio, la mappa nome → nome finale.

    Gli schemi comuni (``Problema`` e dipendenti) vengono prima. Uno schema con lo
    stesso nome e la stessa definizione in più servizi resta uno; con definizioni
    diverse, quello del servizio che arriva dopo diventa ``<Servizio><Nome>``. I
    riferimenti interni si riscrivono con la mappa del servizio: se uno schema
    citato è rinominato, anche chi lo cita può diventare diverso e si rinomina a
    sua volta (si ripete fino a un punto fisso)."""
    mappe: dict[str, dict[str, str]] = {s: {} for s in per_servizio}
    while True:
        uniti: dict[str, Any] = {k: copy.deepcopy(v) for k, v in schemi_comuni.items()}
        cambiato = False
        for servizio, schemi in per_servizio.items():
            mappa = mappe[servizio]
            for nome in sorted(schemi):
                if nome in _SCHEMI_VALIDAZIONE_FASTAPI:
                    continue
                definizione = _riscrivi_ref(schemi[nome], mappa)
                finale = mappa.get(nome, nome)
                if finale in uniti and _canonico(uniti[finale]) != _canonico(definizione):
                    if nome in mappa:
                        raise SystemExit(f"Schema {finale} definito due volte in modo diverso")
                    mappa[nome] = f"{_prefisso_servizio(servizio)}{nome}"
                    cambiato = True
                    break
                uniti.setdefault(finale, definizione)
            if cambiato:
                break
        if not cambiato:
            return uniti, mappe


def combina_regole(*regole: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Intersezione di più regole d'autenticazione (None = nessuna informazione)."""
    note = [r for r in regole if r is not None]
    if not note:
        return None
    metodi = set(note[0]["metodi"])
    ruoli: set[str] | None = None
    for r in note:
        metodi &= set(r["metodi"])
        if r.get("ruoli"):
            ruoli = set(r["ruoli"]) if ruoli is None else ruoli & set(r["ruoli"])
    return {"metodi": sorted(metodi), "ruoli": sorted(ruoli) if ruoli is not None else None}


def _sicurezza(operazione: dict[str, Any], percorso: str,
               regole: Mapping[str, Any] | None = None) -> list[dict[str, list[str]]]:
    """Requisiti di sicurezza: dalle regole ricavate dalle dipendenze, se ci sono;
    altrimenti dalle intestazioni e dagli schemi dichiarati dalla rotta. Toglie
    dai parametri le intestazioni di autenticazione."""
    nomi = {p["name"].lower() for p in operazione.get("parameters", []) if p.get("in") == "header"}
    if regole is not None:
        bearer = "jwt" in regole["metodi"]
        chiave = "chiave_api" in regole["metodi"]
    else:
        bearer = bool(nomi & _INTESTAZIONI_BEARER) or any(
            "HTTPBearer" in req for req in operazione.get("security", []) or [])
        chiave = bool(nomi & _INTESTAZIONI_CHIAVE)
    if "parameters" in operazione:
        operazione["parameters"] = [
            p for p in operazione["parameters"]
            if not (p.get("in") == "header"
                    and p["name"].lower() in _INTESTAZIONI_BEARER | _INTESTAZIONI_CHIAVE)]
        if not operazione["parameters"]:
            del operazione["parameters"]
    requisiti: list[dict[str, list[str]]] = []
    if bearer:
        requisiti.append({"voucherPDND": []} if percorso.startswith("/api/pdnd/")
                         else {"bearerJWT": []})
    if chiave:
        requisiti += [{"chiaveApi": []}, {"chiaveApiId": [], "chiaveApiSegreto": []}]
    return requisiti


def _errori(operazione: dict[str, Any]) -> None:
    """Ogni risposta 4xx/5xx in problem+json con lo schema comune; 4XX e 5XX sempre."""
    risposte = operazione.setdefault("responses", {})
    for codice in list(risposte):
        testo = str(codice).upper()
        if testo == "4XX":
            risposte[codice] = {"$ref": "#/components/responses/ErroreRichiesta"}
        elif testo == "5XX" or testo == "DEFAULT":
            risposte[codice] = {"$ref": "#/components/responses/ErroreServizio"}
        elif testo[:1] in ("4", "5"):
            descrizione = risposte[codice].get("description") or "Errore"
            if descrizione == "Validation Error":
                descrizione = "Dati della richiesta non validi (VAL-422, `campi`)"
            risposte[codice] = {
                "description": descrizione,
                "content": {MEDIA_PROBLEMA: {
                    "schema": {"$ref": f"#/components/schemas/{SCHEMA_PROBLEMA}"}}},
            }
    risposte.setdefault("4XX", {"$ref": "#/components/responses/ErroreRichiesta"})
    risposte.setdefault("5XX", {"$ref": "#/components/responses/ErroreServizio"})
    operazione["responses"] = dict(sorted(risposte.items(), key=lambda kv: str(kv[0])))


@dataclass
class Operazione:
    servizio: str
    metodo: str
    percorso: str
    sintesi: str
    instradato_a: str | None
    sicurezza: list[dict[str, list[str]]]
    oscurata_da: str | None = None
    ruoli: list[str] | None = None


@dataclass
class Rapporto:
    operazioni: list[Operazione] = field(default_factory=list)
    eventi: list[dict[str, str]] = field(default_factory=list)
    host: str = ""

    @property
    def esposte(self) -> list[Operazione]:
        return [o for o in self.operazioni if o.oscurata_da is None]

    @property
    def non_instradate(self) -> list[Operazione]:
        return [o for o in self.esposte if o.instradato_a != o.servizio]

    @property
    def oscurate(self) -> list[Operazione]:
        return [o for o in self.operazioni if o.oscurata_da is not None]


def _webhooks(eventi: list[dict[str, str]]) -> dict[str, Any]:
    intestazioni = [
        ("VerumDoc-Firma", True, "`t=<unix>,v1=<hex HMAC-SHA256(segreto, t + \".\" + corpo)>`"),
        ("VerumDoc-Evento", True, "Tipo dell'evento"),
        ("VerumDoc-Id-Evento", True, "`id` dell'evento (come nell'elenco eventi)"),
        ("VerumDoc-Id-Consegna", True, "Id della consegna, stabile fra i tentativi: "
                                       "per scartare i duplicati"),
    ]
    risultato: dict[str, Any] = {}
    for e in eventi:
        risultato[e["tipo"]] = {"post": {
            "summary": f"Evento {e['tipo']}",
            "description": f"{e['descrizione']} Risorsa: `{e['risorsa']}`. Il corpo è l'evento "
                           "in JSON compatto con chiavi ordinate (UTF-8): verificare la firma "
                           "sui byte ricevuti e rispondere 2xx entro 10 secondi.",
            "operationId": "webhook_" + re.sub(r"\W", "_", e["tipo"]),
            "tags": ["Webhook"],
            "x-verumdoc-servizio": "notification",
            "parameters": [{"name": n, "in": "header", "required": obbligatoria,
                            "description": d, "schema": {"type": "string"}}
                           for n, obbligatoria, d in intestazioni],
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Evento"}}}},
            "responses": {
                "2XX": {"description": "Consegna confermata"},
                "default": {"description": "Consegna fallita: ritentata dopo 60 s, 5 min, "
                                           "25 min e 2 h, poi in coda di scarto"},
            },
        }}
    return risultato


def _regole_operazione(estratti: Mapping[str, Mapping[str, Any]], servizio: str, metodo: str,
                       percorso: str, op: Mapping[str, Any]) -> dict[str, Any] | None:
    """Regole d'autenticazione dell'operazione ricavate dalle dipendenze, combinate
    con quelle dell'operazione a cui inoltra (``x-verumdoc-inoltra`` o ``INOLTRI``)."""
    proprie = (estratti[servizio].get("autenticazione") or {}).get(f"{metodo} {percorso}")
    destinazione = op.get("x-verumdoc-inoltra")
    if isinstance(destinazione, dict):
        bersaglio: tuple[str, str, str] | None = (
            str(destinazione.get("servizio")), str(destinazione.get("metodo", "")).lower(),
            str(destinazione.get("percorso")))
    else:
        bersaglio = INOLTRI.get((metodo, percorso))
    if bersaglio is None or bersaglio[0] not in estratti:
        return combina_regole(proprie)
    s, m, p = bersaglio
    altrui = (estratti[s].get("autenticazione") or {}).get(f"{m} {p}")
    if altrui is None:
        raise SystemExit(f"Inoltro di {metodo.upper()} {percorso} verso {s} {m.upper()} {p}: "
                         "regole d'autenticazione non trovate")
    return combina_regole(proprie, altrui)


def unisci(estratti: Mapping[str, Mapping[str, Any]], host: str,
           rotte: list[Rotta]) -> tuple[dict[str, Any], Rapporto]:
    """OpenAPI unica e rapporto sull'instradamento."""
    rapporto = Rapporto(host=host)
    problema = next(iter(estratti.values()))["problema"]
    comuni = dict(problema.get("$defs", {}))
    comuni[SCHEMA_PROBLEMA] = {k: v for k, v in problema.items() if k != "$defs"}
    per_servizio = {s: e["openapi"].get("components", {}).get("schemas", {})
                    for s, e in estratti.items()}
    schemi, mappe = rinomina_schemi(comuni, per_servizio)

    # Operazioni candidate per (metodo, percorso)
    candidate: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for servizio, e in estratti.items():
        for percorso, voce in e["openapi"].get("paths", {}).items():
            for metodo in METODI:
                if metodo in voce:
                    op = _riscrivi_ref(copy.deepcopy(voce[metodo]), mappe[servizio])
                    candidate.setdefault((metodo, percorso), []).append((servizio, op))

    percorsi: dict[str, dict[str, Any]] = {}
    id_operazioni: dict[str, int] = {}
    for (metodo, percorso), servizi in sorted(candidate.items(),
                                             key=lambda kv: (kv[0][1], METODI.index(kv[0][0]))):
        instradato = servizio_instradato(percorso, rotte)
        indice_scelto = next((i for i, s in enumerate(servizi) if s[0] == instradato), 0)
        scelto = servizi[indice_scelto]
        for i, (servizio, op) in enumerate(servizi):
            if i != indice_scelto:
                rapporto.operazioni.append(Operazione(
                    servizio, metodo.upper(), percorso, op.get("summary", ""), instradato,
                    [], oscurata_da=scelto[0]))
        servizio, op = scelto
        regole = _regole_operazione(estratti, servizio, metodo, percorso, op)
        sicurezza = _sicurezza(op, percorso, regole)
        if sicurezza:
            op["security"] = sicurezza
        else:
            op.pop("security", None)
        ruoli = regole.get("ruoli") if regole else None
        if ruoli:
            op["x-verumdoc-ruoli"] = list(ruoli)
        _errori(op)
        op["x-verumdoc-servizio"] = servizio
        op["x-verumdoc-ingress"] = {"servizio": instradato, "raggiungibile": instradato == servizio}
        id_op = op.get("operationId", "")
        id_operazioni[id_op] = id_operazioni.get(id_op, 0) + 1
        percorsi.setdefault(percorso, {})[metodo] = op
        rapporto.operazioni.append(Operazione(servizio, metodo.upper(), percorso,
                                              op.get("summary", ""), instradato, sicurezza,
                                              ruoli=list(ruoli) if ruoli else None))
    # operationId univoci: in caso di omonimia si antepone il servizio
    for voce in percorsi.values():
        for op in voce.values():
            if id_operazioni.get(op.get("operationId", ""), 0) > 1:
                op["operationId"] = f"{op['x-verumdoc-servizio']}__{op['operationId']}"

    eventi = [e for estratto in estratti.values() for e in estratto.get("eventi", [])]
    rapporto.eventi = eventi
    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "VerumDoc API", "version": VERSIONE_API, "description": DESCRIZIONE,
                 "license": {"name": "Proprietaria"}},
        "servers": [{"url": f"https://{host}", "description": "Ingress pubblico"}],
        "tags": [],
        "paths": percorsi,
        "webhooks": _webhooks(eventi),
        "components": {
            "schemas": {},
            "responses": RISPOSTE_COMUNI,
            "headers": INTESTAZIONI_COMUNI,
            "securitySchemes": SECURITY_SCHEMES,
        },
    }
    # Solo gli schemi raggiungibili (chiusura sui riferimenti)
    usati: set[str] = set()
    _ref_usati({"p": percorsi, "w": spec["webhooks"], "r": RISPOSTE_COMUNI}, usati)
    da_visitare = list(usati)
    while da_visitare:
        nome = da_visitare.pop()
        interni: set[str] = set()
        _ref_usati(schemi.get(nome, {}), interni)
        for n in interni - usati:
            usati.add(n)
            da_visitare.append(n)
    mancanti = sorted(n for n in usati if n not in schemi)
    if mancanti:
        raise SystemExit(f"Schemi citati ma non definiti: {mancanti}")
    spec["components"]["schemas"] = {n: schemi[n] for n in sorted(usati)}
    nomi_tag = sorted({t for v in percorsi.values() for op in v.values()
                       for t in op.get("tags", [])} | {"Webhook"})
    spec["tags"] = [{"name": t} for t in nomi_tag]
    return spec, rapporto


# ── Uscite ──────────────────────────────────────────────────────────

_INTESTAZIONE_YAML = (
    "# FILE GENERATO da tools/docs/genera_openapi.py importando le app FastAPI dei\n"
    "# servizi pubblici: non modificarlo a mano. Rigenerare con\n"
    "#   python tools/docs/genera_openapi.py\n"
)


def genera_yaml(spec: Mapping[str, Any]) -> str:
    corpo = yaml.safe_dump(dict(spec), sort_keys=False, allow_unicode=True, width=100,
                           default_flow_style=False)
    return _INTESTAZIONE_YAML + corpo


def _descrivi_sicurezza(requisiti: list[dict[str, list[str]]],
                        ruoli: list[str] | None = None) -> str:
    if not requisiti:
        return "nessuna"
    parti = []
    for r in requisiti:
        if "bearerJWT" in r:
            parti.append("JWT")
        elif "voucherPDND" in r:
            parti.append("voucher PDND")
        elif "chiaveApi" in r:
            parti.append("chiave API")
    testo = ", ".join(dict.fromkeys(parti))
    return f"{testo} (ruolo {', '.join(ruoli)})" if ruoli else testo


def _cella(testo: str) -> str:
    return (testo or "").replace("|", "\\|").replace("\n", " ")


def proposte_ingress(rapporto: Rapporto) -> list[tuple[str, str]]:
    """Prefissi da instradare (primi tre segmenti) per le operazioni non raggiungibili."""
    proposte: dict[str, str] = {}
    for o in rapporto.non_instradate:
        prefisso = "/" + "/".join(_segmenti(o.percorso)[:3])
        proposte.setdefault(prefisso, o.servizio)
    return sorted(proposte.items())


def genera_markdown(spec: Mapping[str, Any], rapporto: Rapporto) -> str:
    righe = [
        "# Catalogo degli endpoint pubblici",
        "",
        "<!-- File GENERATO da tools/docs/genera_openapi.py importando le app FastAPI:",
        "     non modificarlo a mano. -->",
        "",
        f"OpenAPI unica: [`openapi.yaml`](openapi.yaml) (OpenAPI {spec['openapi']}, "
        f"versione {spec['info']['version']}). Host: `{rapporto.host}`. "
        "Errori: [`errori.md`](errori.md).",
        "",
    ]
    esposte = rapporto.esposte
    servizi = [s for s in SERVIZI if any(o.servizio == s for o in esposte)]
    righe += ["| Servizio | Operazioni | Instradate dall'ingress |", "|---|---:|---:|"]
    for s in servizi:
        tutte = [o for o in esposte if o.servizio == s]
        ok = sum(1 for o in tutte if o.instradato_a == s)
        righe.append(f"| {s} | {len(tutte)} | {ok} |")
    righe += [f"| **Totale** | **{len(esposte)}** | "
              f"**{sum(1 for o in esposte if o.instradato_a == o.servizio)}** |", "",
              f"Webhook documentati: {len(rapporto.eventi)}.", ""]
    for s in servizi:
        righe += [f"## {s}", "", "| Metodo | Percorso | Sintesi | Autenticazione | Ingress |",
                  "|---|---|---|---|---|"]
        for o in sorted((o for o in esposte if o.servizio == s),
                        key=lambda o: (o.percorso, METODI.index(o.metodo.lower()))):
            ingresso = "sì" if o.instradato_a == s else f"**no** (→ {o.instradato_a or 'nessuno'})"
            righe.append(f"| {o.metodo} | `{o.percorso}` | {_cella(o.sintesi)} | "
                         f"{_descrivi_sicurezza(o.sicurezza, o.ruoli)} | {ingresso} |")
        righe.append("")
    righe += ["## Webhook", "", "| Evento | Risorsa | Quando |", "|---|---|---|"]
    righe += [f"| `{e['tipo']}` | {e['risorsa']} | {_cella(e['descrizione'])} |"
              for e in rapporto.eventi]
    righe += ["", "## Instradamento da correggere", ""]
    if rapporto.non_instradate:
        righe += ["Operazioni che l'ingress (`infra/helm/values.yaml`, `ingress.routes`) oggi "
                  "manda a un altro servizio o a nessuno: non sono raggiungibili dall'esterno.",
                  "", "| Metodo | Percorso | Servizio | Oggi va a |", "|---|---|---|---|"]
        righe += [f"| {o.metodo} | `{o.percorso}` | {o.servizio} | {o.instradato_a or 'nessuno'} |"
                  for o in rapporto.non_instradate]
        righe += ["", "Prefissi da aggiungere alle rotte dell'ingress:", ""]
        righe += [f"- `{p}` → {s}" for p, s in proposte_ingress(rapporto)]
    else:
        righe.append("Nessuna: ogni operazione è instradata al suo servizio.")
    righe += ["", "## Operazioni oscurate", ""]
    if rapporto.oscurate:
        righe += ["Stesso metodo e percorso in più servizi: l'ingress ne raggiunge uno solo; le "
                  "altre restano interne.", "",
                  "| Metodo | Percorso | Servizio | Raggiunto invece |",
                  "|---|---|---|---|"]
        righe += [f"| {o.metodo} | `{o.percorso}` | {o.servizio} | {o.oscurata_da} |"
                  for o in sorted(rapporto.oscurate, key=lambda o: (o.percorso, o.servizio))]
    else:
        righe.append("Nessuna.")
    senza = [o for o in esposte if not o.sicurezza]
    righe += ["", "## Senza autenticazione dichiarata", "",
              "Operazioni la cui rotta non dichiara intestazioni o schemi di autenticazione "
              "(pubbliche per scelta: salute, informazioni, accesso SPID).", ""]
    righe += [f"- {o.metodo} `{o.percorso}` ({o.servizio})" for o in senza] or ["Nessuna."]
    return "\n".join(righe) + "\n"


def genera(estratti: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[str, str]:
    """Testi di ``openapi.yaml`` e ``endpoint.md``."""
    if estratti is None:
        estratti = {s: estrai_servizio(s) for s in SERVIZI}
    host, rotte = rotte_ingress()
    spec, rapporto = unisci(estratti, host, rotte)
    return genera_yaml(spec), genera_markdown(spec, rapporto)


def non_aggiornati(testi: tuple[str, str] | None = None) -> list[Path]:
    testo_yaml, testo_md = testi or genera()
    diversi = []
    for percorso, atteso in ((OUT_YAML, testo_yaml), (OUT_MD, testo_md)):
        if not percorso.exists() or percorso.read_text(encoding="utf-8") != atteso:
            diversi.append(percorso)
    return diversi


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--verifica", action="store_true",
                    help="non scrive: esce con 1 se openapi.yaml o endpoint.md non sono aggiornati")
    ap.add_argument("--ingress", action="store_true",
                    help="esce con 1 se un'operazione non è instradata al suo servizio")
    ap.add_argument("--estrai", nargs=2, metavar=("SERVIZIO", "USCITA"), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.estrai:
        _estrai_nel_processo(a.estrai[0], Path(a.estrai[1]))
        return 0
    testi = genera()
    if a.ingress:
        host, rotte = rotte_ingress()
        _, rapporto = unisci({s: estrai_servizio(s) for s in SERVIZI}, host, rotte)
        for o in rapporto.non_instradate:
            sys.stdout.write(f"{o.metodo} {o.percorso}: servizio {o.servizio}, "
                             f"ingress → {o.instradato_a}\n")
        return 1 if rapporto.non_instradate else 0
    if a.verifica:
        diversi = non_aggiornati(testi)
        for p in diversi:
            sys.stdout.write(f"Non aggiornato: {p.relative_to(RADICE)} "
                             "(python tools/docs/genera_openapi.py)\n")
        return 1 if diversi else 0
    OUT_YAML.parent.mkdir(parents=True, exist_ok=True)
    OUT_YAML.write_text(testi[0], encoding="utf-8")
    OUT_MD.write_text(testi[1], encoding="utf-8")
    sys.stdout.write(f"Scritti {OUT_YAML.relative_to(RADICE)} e {OUT_MD.relative_to(RADICE)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
