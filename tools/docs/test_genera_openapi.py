# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""OpenAPI unica e catalogo degli endpoint (tools/docs/genera_openapi.py).

Il primo gruppo importa le app FastAPI vere (in processi separati, una volta per
modulo) e fallisce se ``docs/catalogo/openapi.yaml`` o ``endpoint.md`` non sono
aggiornati; il secondo prova le funzioni di unione su specifiche sintetiche.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genera_openapi as g  # noqa: E402

PROBLEMA = {"$ref": "#/components/schemas/Problema"}


@pytest.fixture(scope="module")
def estratti():
    return {s: g.estrai_servizio(s) for s in g.SERVIZI}


@pytest.fixture(scope="module")
def unione(estratti):
    host, rotte = g.rotte_ingress()
    return g.unisci(estratti, host, rotte)


# ── Sulle app vere ──────────────────────────────────────────────────

def test_file_generati_aggiornati(estratti):
    """Fallisce se un router è cambiato senza rigenerare
    (python tools/docs/genera_openapi.py)."""
    assert g.non_aggiornati(g.genera(estratti)) == []


def test_specifica_valida_openapi_31(unione):
    validatore = pytest.importorskip("openapi_spec_validator")
    spec, _ = unione
    assert spec["openapi"] == "3.1.0"
    validatore.validate(copy.deepcopy(spec))


def test_ogni_errore_rimanda_allo_schema_problema(unione):
    spec, _ = unione
    for percorso, voce in spec["paths"].items():
        for metodo, op in voce.items():
            risposte = op["responses"]
            assert "4XX" in risposte and "5XX" in risposte, (metodo, percorso)
            for codice, r in risposte.items():
                if str(codice)[:1] in "45":
                    assert r.get("$ref", "").startswith("#/components/responses/") or \
                        r["content"] == {"application/problem+json": {"schema": PROBLEMA}}, \
                        (metodo, percorso, codice)
    for r in spec["components"]["responses"].values():
        assert r["content"]["application/problem+json"]["schema"] == PROBLEMA
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert set(spec["components"]["schemas"]["Problema"]["required"]) >= {
        "codice", "esito", "ritentabile", "status", "title", "type"}


def test_sicurezza_dalle_rotte(unione):
    spec, _ = unione
    versa = spec["paths"]["/api/v1/versamento/ud"]["post"]
    assert {"bearerJWT": []} in versa["security"] and {"chiaveApi": []} in versa["security"]
    assert all(p["name"].lower() not in ("authorization", "x-api-key")
               for p in versa.get("parameters", []))
    assert any(p["name"] == "Idempotency-Key" for p in versa["parameters"])
    assert spec["paths"]["/api/pdnd/v1/versamento"]["post"]["security"] == [{"voucherPDND": []}]
    assert "security" not in spec["paths"]["/api/v1/auth/spid/providers"]["get"]
    assert set(spec["components"]["securitySchemes"]) == {
        "bearerJWT", "chiaveApi", "chiaveApiId", "chiaveApiSegreto", "voucherPDND"}


def test_autenticazione_ricavata_dalle_dipendenze_reali(unione):
    """R03 f2 (REV-10): i webhook accettano solo il JWT
    dell'amministratore dell'ente (notification lo impone): l'OpenAPI e il
    catalogo lo dicono; le rotte che leggono da sé solo il Bearer non
    dichiarano la chiave API anche se il router la accetta per contare le quote."""
    spec, rapporto = unione
    for percorso, metodo in (("/api/v1/webhook", "post"), ("/api/v1/webhook", "get"),
                             ("/api/v1/webhook/{webhook_id}", "delete")):
        op = spec["paths"][percorso][metodo]
        assert op["security"] == [{"bearerJWT": []}], (metodo, percorso)
        assert op["x-verumdoc-ruoli"] == ["ADMIN_TENANT"], (metodo, percorso)
    assert {"chiaveApi": []} in spec["paths"]["/api/v1/eventi"]["get"]["security"]
    assert spec["paths"]["/api/dashboard/overview"]["get"]["security"] == [{"bearerJWT": []}]
    assert {"chiaveApi": []} in spec["paths"]["/api/dashboard/versamenti"]["get"]["security"]
    md = g.genera_markdown(spec, rapporto)
    riga = next(r for r in md.splitlines() if r.startswith("| POST | `/api/v1/webhook` |"))
    assert "| JWT (ruolo ADMIN_TENANT) |" in riga, riga


def test_webhook_per_ogni_tipo_di_evento(unione, estratti):
    spec, _ = unione
    tipi = [e["tipo"] for e in estratti["notification"]["eventi"]]
    assert sorted(spec["webhooks"]) == sorted(tipi) and "versamento.conservato" in tipi
    post = spec["webhooks"]["versamento.conservato"]["post"]
    assert {p["name"] for p in post["parameters"]} >= {"VerumDoc-Firma", "VerumDoc-Id-Consegna"}
    assert post["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Evento"}


def test_servizi_e_percorsi_dell_ingress(unione):
    spec, rapporto = unione
    servizi = {op["x-verumdoc-servizio"] for v in spec["paths"].values() for op in v.values()}
    assert servizi == set(g.SERVIZI)
    assert spec["paths"]["/api/v1/eventi"]["get"]["x-verumdoc-servizio"] == "notification"
    assert spec["paths"]["/api/v2/distribution/search"]["post"]["x-verumdoc-servizio"] == \
        "distribution"
    # /api/v1/info esiste in più servizi: resta quello raggiunto dall'ingress
    assert spec["paths"]["/api/v1/info"]["get"]["x-verumdoc-servizio"] == "gateway"
    assert {o.servizio for o in rapporto.oscurate if o.percorso == "/api/v1/info"} >= {
        "notification", "onboarding"}


def test_ogni_operazione_raggiungibile_dall_ingress(unione):
    """Ogni operazione pubblica è instradata al suo servizio (VD-C-098): una rotta
    nuova senza regola d'ingress fa fallire il test."""
    _, rapporto = unione
    assert [(o.metodo, o.percorso, o.instradato_a) for o in rapporto.non_instradate] == []


def test_yaml_rileggibile_e_uguale(unione):
    spec, _ = unione
    assert yaml.safe_load(g.genera_yaml(spec)) == spec


# ── Funzioni di unione su dati sintetici ───────────────────────────

def _rotte():
    return [g.Rotta("/api/v1", "gw"), g.Rotta("/api/v1/eventi", "notif"),
            g.Rotta("/api", "dash")]


def test_servizio_instradato_prefisso_per_segmenti():
    rotte = _rotte()
    assert g.servizio_instradato("/api/v1/eventi", rotte) == "notif"
    assert g.servizio_instradato("/api/v1/eventi/x", rotte) == "notif"
    assert g.servizio_instradato("/api/v1/eventixyz", rotte) == "gw"
    assert g.servizio_instradato("/api/v2/x", rotte) == "dash"
    assert g.servizio_instradato("/altro", rotte) is None


def test_rinomina_schemi_in_conflitto_e_riferimenti():
    a = {"Voce": {"type": "object", "properties": {"x": {"type": "string"}}},
         "Elenco": {"type": "array", "items": {"$ref": "#/components/schemas/Voce"}}}
    b = {"Voce": {"type": "object", "properties": {"y": {"type": "integer"}}},
         "Elenco": {"type": "array", "items": {"$ref": "#/components/schemas/Voce"}},
         "Uguale": {"type": "string"}}
    uniti, mappe = g.rinomina_schemi({}, {"gw": a, "notif-x": dict(b)})
    assert mappe["gw"] == {}
    assert mappe["notif-x"] == {"Voce": "NotifXVoce", "Elenco": "NotifXElenco"}
    assert uniti["NotifXElenco"]["items"]["$ref"] == "#/components/schemas/NotifXVoce"
    assert uniti["Elenco"]["items"]["$ref"] == "#/components/schemas/Voce"
    assert uniti["Uguale"] == {"type": "string"}


def _op(intestazioni=(), risposte=None, sicurezza=None):
    op = {"summary": "s", "operationId": "op",
          "parameters": [{"name": n, "in": "header", "schema": {"type": "string"}}
                         for n in intestazioni],
          "responses": risposte or {"200": {"description": "ok"},
                                    "422": {"description": "Validation Error", "content": {
                                        "application/json": {"schema": {
                                            "$ref": "#/components/schemas/HTTPValidationError"}}}}}}
    if sicurezza:
        op["security"] = sicurezza
    return op


def _estratto(percorsi, schemi=None, eventi=None):
    return {"openapi": {"paths": percorsi, "components": {"schemas": schemi or {}}},
            "problema": {"type": "object", "properties": {"codice": {"type": "string"}},
                         "required": ["codice"]},
            **({"eventi": eventi} if eventi is not None else {})}


def test_unisci_sicurezza_errori_oscurate_e_non_instradate():
    estratti = {
        "gw": _estratto({
            "/api/v1/info": {"get": _op()},
            "/api/v1/x": {"post": _op(["authorization", "X-Api-Key", "Idempotency-Key"])},
        }),
        "notif": _estratto({
            "/api/v1/info": {"get": _op()},
            "/api/v1/webhook": {"get": _op(["authorization"])},
        }, schemi={"Evento": {"type": "object"}},
            eventi=[{"tipo": "a.fatto", "risorsa": "a", "descrizione": "Fatto."}]),
    }
    spec, rapporto = g.unisci(estratti, "api.prova", _rotte())
    x = spec["paths"]["/api/v1/x"]["post"]
    assert x["security"] == [{"bearerJWT": []}, {"chiaveApi": []},
                             {"chiaveApiId": [], "chiaveApiSegreto": []}]
    assert [p["name"] for p in x["parameters"]] == ["Idempotency-Key"]
    assert x["responses"]["422"]["content"] == {"application/problem+json": {"schema": PROBLEMA}}
    assert x["responses"]["4XX"] == {"$ref": "#/components/responses/ErroreRichiesta"}
    assert "security" not in spec["paths"]["/api/v1/info"]["get"]
    assert spec["paths"]["/api/v1/info"]["get"]["x-verumdoc-servizio"] == "gw"
    assert [(o.servizio, o.oscurata_da) for o in rapporto.oscurate] == [("notif", "gw")]
    [non] = rapporto.non_instradate
    assert (non.percorso, non.servizio, non.instradato_a) == ("/api/v1/webhook", "notif", "gw")
    assert spec["paths"]["/api/v1/webhook"]["get"]["x-verumdoc-ingress"] == {
        "servizio": "gw", "raggiungibile": False}
    assert operazione_ids(spec) == sorted(["gw__op", "gw__op", "notif__op"])
    assert list(spec["webhooks"]) == ["a.fatto"]
    assert set(spec["components"]["schemas"]) == {"Problema", "Evento"}
    md = g.genera_markdown(spec, rapporto)
    assert "- `/api/v1/webhook` → notif" in md
    assert "| GET | `/api/v1/info` | notif | gw |" in md


def operazione_ids(spec):
    return sorted(op["operationId"] for v in spec["paths"].values() for op in v.values())


def test_voucher_pdnd_sui_percorsi_pdnd():
    estratti = {"gw": _estratto({"/api/pdnd/v1/y": {"get": _op(["authorization"])}})}
    spec, _ = g.unisci(estratti, "h", [g.Rotta("/api/pdnd", "gw")])
    assert spec["paths"]["/api/pdnd/v1/y"]["get"]["security"] == [{"voucherPDND": []}]


def test_schema_citato_ma_assente_ferma_la_generazione():
    op = _op(risposte={"200": {"description": "ok", "content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/Mancante"}}}}})
    with pytest.raises(SystemExit, match="Mancante"):
        g.unisci({"gw": _estratto({"/api/v1/z": {"get": op}})}, "h", [g.Rotta("/api", "gw")])


def test_unisci_regole_dalle_dipendenze_e_inoltri(monkeypatch):
    """Le regole ricavate dalle dipendenze prevalgono sulle intestazioni
    dichiarate; un inoltro prende anche le regole dell'operazione di destinazione."""
    monkeypatch.setattr(g, "INOLTRI", {("put", "/api/v1/cb"): ("notif", "post", "/api/v1/wh")})
    gw = _estratto({"/api/v1/cb": {"put": _op(["authorization", "X-Api-Key"])},
                    "/api/v1/solo": {"get": _op(["authorization", "X-Api-Key"])}})
    gw["autenticazione"] = {"put /api/v1/cb": {"metodi": ["chiave_api", "jwt"], "ruoli": None},
                            "get /api/v1/solo": {"metodi": ["jwt"], "ruoli": None}}
    notif = _estratto({"/api/v1/wh": {"post": _op(["authorization", "X-Api-Key"])}})
    notif["autenticazione"] = {"post /api/v1/wh": {"metodi": ["jwt"], "ruoli": ["ADMIN"]}}
    spec, rapporto = g.unisci({"gw": gw, "notif": notif}, "h",
                              [g.Rotta("/api/v1/wh", "notif"), g.Rotta("/api", "gw")])
    cb = spec["paths"]["/api/v1/cb"]["put"]
    assert cb["security"] == [{"bearerJWT": []}] and cb["x-verumdoc-ruoli"] == ["ADMIN"]
    assert spec["paths"]["/api/v1/solo"]["get"]["security"] == [{"bearerJWT": []}]
    assert "x-verumdoc-ruoli" not in spec["paths"]["/api/v1/solo"]["get"]
    assert [o.ruoli for o in rapporto.operazioni if o.percorso == "/api/v1/cb"] == [["ADMIN"]]
    monkeypatch.setattr(g, "INOLTRI", {("put", "/api/v1/cb"): ("notif", "post", "/api/v1/no")})
    with pytest.raises(SystemExit, match="regole d'autenticazione non trovate"):
        g.unisci({"gw": gw, "notif": notif}, "h", [g.Rotta("/api", "gw")])


def test_combina_regole_intersezione():
    assert g.combina_regole(None, None) is None
    assert g.combina_regole({"metodi": ["jwt", "chiave_api"], "ruoli": None},
                            {"metodi": ["jwt"], "ruoli": ["A", "B"]},
                            {"metodi": ["jwt"], "ruoli": ["B"]}) == {
        "metodi": ["jwt"], "ruoli": ["B"]}
