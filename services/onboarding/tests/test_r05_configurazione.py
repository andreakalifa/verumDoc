# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 Q1 — API della configurazione (app/routers/configurazione.py) su PostgreSQL reale.

Verifica: permessi del contratto (conservatore ai livelli CONSERVATORE/ENTE,
amministratore dell'ente ai livelli ENTE/CLASSE/ACCORDO del proprio ente, 404 fra
enti, voci bloccate), bozze con versione (409 CFG-410), anteprima che NON scrive e
calcola l'impatto su un campione dichiarato, applicazione con impronta del piano
(409 CFG-409 PIANO_CAMBIATO con il piano nuovo), storico e audit, accordi
versionati e append-only, profili di tipologia versionati, proposte del
conservatore (§8.13), validatore preventivo anche con chiave API e profilo
esportato per il validatore senza rete.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from app.routers import configurazione_auth
from app.services import configurazione as servizio
from httpx import ASGITransport, AsyncClient

from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.revoca import svuota_cache_revoche
from libs.verumdoc_rules.validatore import ambiente_da_esportazione, valida_versamento
from tests import infra_reale
from tests.infra_reale import richiede_pg, sql

db_url = infra_reale.db_url

BASE = "/api/v1/configurazione"
PEPPER = b"pepper-di-prova-per-i-test-r05-regole"


@pytest.fixture(autouse=True)
def _cache_e_pepper():
    svuota_cache_revoche()
    configurazione_auth.imposta_pepper(PEPPER)
    yield
    svuota_cache_revoche()


async def _ente(url: str) -> str:
    tid = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente', $3, 'ATTIVO', 'a@b.it')",
              tid, tid.hex[:11], f"t_{tid.hex[:20]}")
    return str(tid)


async def _utente(url: str, tid: str, ruolo: str) -> str:
    uid = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
                   "VALUES ($1, $2, $3, 'Nome', 'Cognome', CAST($4 AS public.user_ruolo), true)",
              uid, uuid.UUID(tid), f"{uid.hex[:8]}@x.it", ruolo)
    return str(uid)


async def _classe(url: str, tid: str | None, codice: str = "fattura", **colonne: Any) -> str:
    cid = uuid.uuid4()
    await sql(url, "INSERT INTO public.classi_documentali (id, tenant_id, codice, nome, "
                   "formati_ammessi, max_dimensione_mb, attiva) VALUES ($1, $2, $3, $3, "
                   "CAST($4 AS jsonb), $5, true)",
              cid, uuid.UUID(tid) if tid else None, codice,
              json.dumps(colonne.get("formati", [])), colonne.get("max_mb", 100))
    return str(cid)


def _indice(classe: str = "fattura", nome: str = "doc.pdf", **extra: Any) -> dict[str, Any]:
    return {"classe_documentale": classe, "documenti": [{
        "id": "d1", "tipo": "PRINCIPALE", "nome_file": nome,
        "hash": {"impronta": "ab" * 32, "algoritmo": "SHA-256"},
        "parametri": {"identificativo_documento": "X1", "modalita_formazione": "a",
                      "tipologia_documentale": "fattura", "data_documento": "2026-10-01",
                      "soggetto_produttore_id": "12345678901",
                      "soggetto_produttore_nome": "Acme", "oggetto": "Fattura di prova",
                      **extra}}]}


async def _pdv(url: str, tid: str, cid: str, uid: str, *, formato: str = "application/pdf",
               dimensione: int = 1000, nome: str = "doc.pdf", giorni_fa: int = 1,
               **extra: Any) -> str:
    pid = uuid.uuid4()
    meta = {"indice": _indice(nome=nome, **extra),
            "docs_s3_keys": [{"nome_file": nome, "size_bytes": dimensione,
                              "formato_rilevato": formato}]}
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "metadata_json, data_ricezione) VALUES ($1, 'PdV', 'RICEVUTO', $2, $3, $4, "
                   "CAST($5 AS jsonb), $6)",
              pid, uuid.UUID(cid), uuid.UUID(uid), uuid.UUID(tid), json.dumps(meta),
              datetime.now(UTC) - timedelta(days=giorni_fa))
    return str(pid)


def _token(tid: str | None, uid: str, ruolo: str) -> str:
    return create_access_token(tenant_id=tid, user_id=uid, ruolo=ruolo)


async def _chiama(metodo: str, percorso: str, *, token: str | None = None,
                  corpo: Any = None, intestazioni: dict[str, str] | None = None,
                  params: dict[str, Any] | None = None) -> tuple[int, Any]:
    h = dict(intestazioni or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.request(metodo, BASE + percorso, json=corpo, headers=h, params=params)
    return r.status_code, (r.json() if r.content else None)


async def _conteggi(url: str) -> dict[str, Any]:
    """Stato delle tabelle che la configurazione può scrivere."""
    tabelle = ("impostazioni_valori", "impostazioni_storico", "accordi_versamento",
               "profili_tipologia", "profili_metadati", "configurazione_proposte",
               "audit_global")
    conteggi: dict[str, Any] = {}
    for t in tabelle:
        conteggi[t] = (await sql(url, f"SELECT count(*) AS n FROM public.{t}"))[0]["n"]  # noqa: S608
    conteggi["bozze"] = [dict(r) for r in await sql(
        url, "SELECT id, versione, stato, updated_at FROM public.configurazione_bozze ORDER BY id")]
    conteggi["classi"] = [dict(r) for r in await sql(
        url, "SELECT id, formati_ammessi::text, max_dimensione_mb, updated_at "
             "FROM public.classi_documentali ORDER BY id")]
    return conteggi


class Scenario:
    """Un ente con amministratore, una classe dell'ente e un conservatore."""

    def __init__(self, url: str, tid: str, admin: str, cid: str) -> None:
        self.url, self.tid, self.admin, self.cid = url, tid, admin, cid
        self.t_admin = _token(tid, admin, "ADMIN_TENANT")
        # conservatore: il token porta sempre un ente (quello del conservatore)
        self.t_super = _token(str(uuid.uuid4()), str(uuid.uuid4()), "SUPER_ADMIN")


async def _scenario(url: str) -> Scenario:
    tid = await _ente(url)
    admin = await _utente(url, tid, "ADMIN_TENANT")
    cid = await _classe(url, tid)
    return Scenario(url, tid, admin, cid)


async def _bozza(token: str, corpo: dict[str, Any]) -> dict[str, Any]:
    stato, risposta = await _chiama("POST", "/bozze", token=token, corpo=corpo)
    assert stato == 201, risposta
    return risposta


# ── Permessi ────────────────────────────────────────────────────────

@richiede_pg
async def test_permessi_del_contratto(db_url) -> None:
    s = await _scenario(db_url)
    altro = await _ente(db_url)
    viewer = await _utente(db_url, s.tid, "VIEWER")
    operatore = await _utente(db_url, s.tid, "OPERATORE")
    corpo_ente = {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                  "modifiche": [{"codice": "CLASSE_DIMENSIONE_MAX_MB", "valore": 50}],
                  "motivo": "Limite concordato con l'ente"}
    # senza token 401; VIEWER legge ma non modifica; OPERATORE non modifica
    assert (await _chiama("GET", "/impostazioni"))[0] == 401
    stato, elenco = await _chiama("GET", "/impostazioni", token=_token(s.tid, viewer, "VIEWER"))
    assert stato == 200 and elenco["contesto"]["livello"] == "ENTE"
    voce = next(v for v in elenco["items"] if v["codice"] == "CLASSE_DIMENSIONE_MAX_MB")
    assert voce["azioni_consentite"]["modifica"] is False
    stato, err = await _chiama("POST", "/bozze", token=_token(s.tid, operatore, "OPERATORE"),
                               corpo=corpo_ente)
    assert stato == 403 and err["codice"] == "CFG-403"
    # ADMIN_TENANT: livello del conservatore no (403), ente altrui 404
    stato, err = await _chiama("POST", "/bozze", token=s.t_admin,
                               corpo={**corpo_ente, "ambito": {"livello": "CONSERVATORE"}})
    assert stato == 403 and err["codice"] == "CFG-403"
    stato, err = await _chiama("POST", "/bozze", token=s.t_admin,
                               corpo={**corpo_ente, "ambito": {"livello": "ENTE",
                                                               "ente": altro}})
    assert stato == 404 and err["codice"] == "CFG-404" and altro not in json.dumps(err)
    stato, err = await _chiama("GET", "/impostazioni", token=s.t_admin, params={"ente": altro})
    assert stato == 404
    # ADMIN_TENANT sul proprio ente: sì; quote no (le stabilisce il conservatore)
    assert (await _chiama("POST", "/bozze", token=s.t_admin, corpo=corpo_ente))[0] == 201
    stato, err = await _chiama("POST", "/bozze", token=s.t_admin, corpo={
        "risorsa": "quote", "ambito": {"livello": "ENTE"},
        "modifiche": [{"codice": "QUOTA_PDV_GIORNO", "valore": 10}],
        "motivo": "Più quota per l'ente"})
    assert stato == 403, err
    # classe di base: l'ente no (403), il SUPER_ADMIN sì
    base = await _classe(db_url, None, codice="registro_base")
    corpo_base = {"risorsa": "classe", "ambito": {"livello": "CLASSE", "classe": base},
                  "modifiche": [{"codice": "CLASSE_DIMENSIONE_MAX_MB", "valore": 20}],
                  "motivo": "Limite per la classe di base"}
    assert (await _chiama("POST", "/bozze", token=s.t_admin, corpo=corpo_base))[0] == 403
    assert (await _chiama("POST", "/bozze", token=s.t_super, corpo=corpo_base))[0] == 201
    # RSC_CONSERVATORE: livelli CONSERVATORE ed ENTE di qualsiasi ente
    rsc = _token(str(uuid.uuid4()), str(uuid.uuid4()), "RSC_CONSERVATORE")
    assert (await _chiama("POST", "/bozze", token=rsc, corpo={
        **corpo_ente, "ambito": {"livello": "ENTE", "ente": altro}}))[0] == 201
    assert (await _chiama("POST", "/bozze", token=rsc, corpo=corpo_base))[0] == 403
    # livello PDV: si chiede nel versamento
    stato, err = await _chiama("POST", "/bozze", token=s.t_admin, corpo={
        **corpo_ente, "ambito": {"livello": "PDV"}})
    assert stato == 400 and "override" in err["detail"]
    # elenco bozze: l'ente vede solo le sue
    stato, elenco = await _chiama("GET", "/bozze", token=s.t_admin)
    assert stato == 200 and {b["ambito"]["ente"] for b in elenco["items"]} == {s.tid}


# ── Bozze, anteprima, applica ───────────────────────────────────────

@richiede_pg
async def test_anteprima_non_scrive_e_calcola_impatto_poi_applica(db_url) -> None:
    s = await _scenario(db_url)
    # 3 PdV recenti: due PNG (diventerebbero non conformi), un PDF; uno vecchio fuori finestra
    for formato in ("image/png", "image/png", "application/pdf"):
        await _pdv(db_url, s.tid, s.cid, s.admin, formato=formato)
    await _pdv(db_url, s.tid, s.cid, s.admin, formato="image/png", giorni_fa=90)
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
        "modifiche": [{"codice": "FORMATI_AMMESSI", "valore": ["application/pdf"]}],
        "motivo": "Solo PDF per l'ente"})
    prima = await _conteggi(db_url)
    stato, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert stato == 200, piano
    assert await _conteggi(db_url) == prima  # l'anteprima NON scrive nulla
    assert piano["bloccati"] == [] and len(piano["impronta_piano"]) == 64
    assert piano["modifiche"] == [{"codice": "FORMATI_AMMESSI", "prima": [],
                                   "dopo": ["application/pdf"], "origine_prima": "PREDEFINITO",
                                   "origine_dopo": "ENTE"}]
    impatto = piano["impatto"]
    assert impatto["pdv_ultimi_30_giorni_che_sarebbero_rifiutati"] == 2
    assert impatto["campione"]["letti"] == 3 and impatto["campione"]["finestra_giorni"] == 30
    assert impatto["campione"]["troncato_per_tempo"] is False
    assert {e["esito"] for e in impatto["esempi"]} == {"RIFIUTATO"}
    assert impatto["esempi"][0]["rilievi"][0]["regola"] == "RGL-105"

    stato, esito = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                                 corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 200, esito
    assert esito["esito"] == {"valori_scritti": 1}
    riga = (await sql(db_url, "SELECT valore::text AS v, motivo FROM public.impostazioni_valori "
                              "WHERE livello = 'ENTE' AND tenant_id = $1", uuid.UUID(s.tid)))[0]
    assert json.loads(riga["v"]) == ["application/pdf"] and riga["motivo"] == "Solo PDF per l'ente"
    storico = await sql(db_url, "SELECT valore_prima, valore_dopo::text AS d, bozza_id "
                                "FROM public.impostazioni_storico")
    assert len(storico) == 1 and storico[0]["valore_prima"] is None
    assert str(storico[0]["bozza_id"]) == b["bozza_id"]
    audit = await sql(db_url, "SELECT evento FROM public.audit_global WHERE tenant_id = $1 "
                              "ORDER BY created_at", uuid.UUID(s.tid))
    assert [a["evento"] for a in audit] == ["configurazione.bozza.creata",
                                            "configurazione.bozza.applicata"]
    stato, elenco = await _chiama("GET", "/impostazioni", token=s.t_admin)
    voce = next(v for v in elenco["items"] if v["codice"] == "FORMATI_AMMESSI")
    assert voce["origine"] == "ENTE" and voce["valore"] == ["application/pdf"]
    assert voce["ereditato"] == {"valore": [], "origine": "PREDEFINITO"}
    # una bozza applicata non si riapplica né si modifica
    stato, err = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                               corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 409 and err["codice"] == "CFG-411"


@richiede_pg
async def test_impatto_con_campione_limitato_e_dichiarato(db_url, monkeypatch) -> None:
    s = await _scenario(db_url)
    for _ in range(5):
        await _pdv(db_url, s.tid, s.cid, s.admin, formato="image/png")
    monkeypatch.setattr(servizio.impostazioni, "configurazione_anteprima_campione_max", 2)
    b = await _bozza(s.t_admin, {
        "risorsa": "classe", "ambito": {"livello": "CLASSE", "classe": s.cid},
        "modifiche": [{"codice": "FORMATI_AMMESSI", "valore": ["application/pdf"]}],
        "motivo": "Classe solo PDF"})
    stato, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert stato == 200, piano
    campione = piano["impatto"]["campione"]
    assert campione["limite"] == 2 and campione["letti"] == 2 and campione["raggiunto_limite"]
    assert campione["metodo"] == "ultimi PdV della classe per l'ente"
    assert piano["impatto"]["pdv_ultimi_30_giorni_che_sarebbero_rifiutati"] == 2
    # la classe in colonna: l'applicazione aggiorna la colonna (letta dal gateway)
    stato, _ = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                             corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 200
    colonna = (await sql(db_url, "SELECT formati_ammessi::text AS f FROM "
                                 "public.classi_documentali WHERE id = $1", uuid.UUID(s.cid)))
    assert json.loads(colonna[0]["f"]) == ["application/pdf"]


@richiede_pg
async def test_piano_cambiato_409_con_il_piano_nuovo(db_url) -> None:
    s = await _scenario(db_url)
    modifica = {"risorsa": "regole", "ambito": {"livello": "ENTE"},
                "modifiche": [{"regola": "RGL-103", "effetto": "BLOCCA"}],
                "motivo": "Niente metadati liberi"}
    prima = await _bozza(s.t_admin, modifica)
    seconda = await _bozza(s.t_admin, {**modifica, "modifiche": [
        {"regola": "RGL-103", "effetto": "IGNORA"}], "motivo": "Ignorare i metadati liberi"})
    _, piano1 = await _chiama("POST", f"/bozze/{prima['bozza_id']}/anteprima", token=s.t_admin)
    _, piano2 = await _chiama("POST", f"/bozze/{seconda['bozza_id']}/anteprima", token=s.t_admin)
    assert (await _chiama("POST", f"/bozze/{seconda['bozza_id']}/applica", token=s.t_admin,
                          corpo={"impronta_piano": piano2["impronta_piano"]}))[0] == 200
    stato, err = await _chiama("POST", f"/bozze/{prima['bozza_id']}/applica", token=s.t_admin,
                               corpo={"impronta_piano": piano1["impronta_piano"]})
    assert stato == 409 and err["codice"] == "CFG-409"
    assert err["dettagli"]["motivo"] == "PIANO_CAMBIATO"
    nuovo = err["dettagli"]["piano"]
    assert nuovo["impronta_piano"] != piano1["impronta_piano"]
    assert nuovo["modifiche"][0]["prima"] == "IGNORA" and nuovo["modifiche"][0]["dopo"] == "BLOCCA"
    # nulla scritto dal tentativo respinto
    valore = await sql(db_url, "SELECT valore::text AS v FROM public.impostazioni_valori "
                               "WHERE codice = 'REGOLA.RGL-103.EFFETTO'")
    assert [json.loads(v["v"]) for v in valore] == ["IGNORA"]
    stato, _ = await _chiama("POST", f"/bozze/{prima['bozza_id']}/applica", token=s.t_admin,
                             corpo={"impronta_piano": nuovo["impronta_piano"]})
    assert stato == 200


@richiede_pg
async def test_bozza_con_versione_e_voci_bloccate(db_url) -> None:
    s = await _scenario(db_url)
    b = await _bozza(s.t_admin, {
        "risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
        "modifiche": [{"codice": "METADATI_MINIMI_PRINCIPALE", "valore": ["oggetto"]},
                      {"codice": "CLASSE_DIMENSIONE_MAX_MB", "valore": "molti"},
                      {"codice": "ALGORITMI_IMPRONTA_AMMESSI", "valore": ["MD5"]}],
        "motivo": "Prova di voci non modificabili"})
    stato, err = await _chiama("PATCH", f"/bozze/{b['bozza_id']}", token=s.t_admin,
                               corpo={"versione": 7, "motivo": "Motivo aggiornato"})
    assert stato == 409 and err["codice"] == "CFG-410" and err["dettagli"]["versione"] == 1
    stato, agg = await _chiama("PATCH", f"/bozze/{b['bozza_id']}", token=s.t_admin,
                               corpo={"versione": 1, "motivo": "Motivo aggiornato"})
    assert stato == 200 and agg["versione"] == 2
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    motivi = {x["codice"]: x["motivo"] for x in piano["bloccati"]}
    assert "imposto dalla norma" in motivi["METADATI_MINIMI_PRINCIPALE"]
    assert "intero" in motivi["CLASSE_DIMENSIONE_MAX_MB"]
    assert "non ancora applicato" in motivi["ALGORITMI_IMPRONTA_AMMESSI"]
    stato, err = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                               corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 422 and err["codice"] == "CFG-422"
    stato, _ = await _chiama("DELETE", f"/bozze/{b['bozza_id']}", token=s.t_admin)
    assert stato == 204
    _, dettaglio = await _chiama("GET", f"/bozze/{b['bozza_id']}", token=s.t_admin)
    assert dettaglio["stato"] == "ANNULLATA"


# ── Accordi, profili, proposte ──────────────────────────────────────

@richiede_pg
async def test_accordo_versionato_append_only_e_non_ambiguo(db_url) -> None:
    s = await _scenario(db_url)
    corpo = {"risorsa": "accordo",
             "ambito": {"livello": "ACCORDO", "accordo": "AV-FATT", "classe": s.cid},
             "modifiche": [{"codice": "CLASSE_DIMENSIONE_MAX_MB", "valore": 10},
                           {"regola": "RGL-103", "effetto": "IGNORA"},
                           {"campo": "descrizione", "valore": "Accordo fatture"}],
             "motivo": "Accordo di versamento firmato"}
    b = await _bozza(s.t_admin, corpo)
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_admin)
    assert piano["bloccati"] == [] and piano["dettagli"]["accordo"]["versione_nuova"] == 1
    assert {m["origine_dopo"] for m in piano["modifiche"]} == {"ACCORDO"}
    ieri = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    stato, err = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                               corpo={"impronta_piano": piano["impronta_piano"],
                                      "valido_dal": ieri})
    assert stato == 422 and err["codice"] == "CFG-423"
    stato, esito = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_admin,
                                 corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 200 and esito["esito"]["accordo"]["versione"] == 1
    # seconda versione: parte dalla prima e cambia un solo valore
    b2 = await _bozza(s.t_admin, {**corpo, "modifiche": [
        {"codice": "CLASSE_DIMENSIONE_MAX_MB", "valore": 20}], "motivo": "Nuovo limite"})
    _, piano2 = await _chiama("POST", f"/bozze/{b2['bozza_id']}/anteprima", token=s.t_admin)
    assert piano2["modifiche"][0]["prima"] == 10 and piano2["modifiche"][0]["dopo"] == 20
    assert (await _chiama("POST", f"/bozze/{b2['bozza_id']}/applica", token=s.t_admin,
                          corpo={"impronta_piano": piano2["impronta_piano"]}))[0] == 200
    versioni = await sql(db_url, "SELECT versione, valori::text AS v FROM "
                                 "public.accordi_versamento ORDER BY versione")
    assert [(v["versione"], json.loads(v["v"])["CLASSE_DIMENSIONE_MAX_MB"]) for v in versioni] \
        == [(1, 10), (2, 20)]
    assert json.loads(versioni[1]["v"])["REGOLA.RGL-103.EFFETTO"] == "IGNORA"
    # append-only: una versione in uso non si modifica
    with pytest.raises(Exception, match="sola aggiunta"):
        await sql(db_url, "UPDATE public.accordi_versamento SET valori = '{}'::jsonb")
    # un secondo accordo per la stessa classe renderebbe ambigua la scelta
    b3 = await _bozza(s.t_admin, {**corpo, "ambito": {"livello": "ACCORDO", "accordo": "AV-2",
                                                      "classe": s.cid}})
    _, piano3 = await _chiama("POST", f"/bozze/{b3['bozza_id']}/anteprima", token=s.t_admin)
    assert any("ambigua" in x["motivo"] for x in piano3["bloccati"])
    # lettura delle impostazioni al livello dell'accordo
    _, elenco = await _chiama("GET", "/impostazioni", token=s.t_admin,
                              params={"accordo": "AV-FATT"})
    voce = next(v for v in elenco["items"] if v["codice"] == "CLASSE_DIMENSIONE_MAX_MB")
    assert (voce["valore"], voce["origine"]) == (20, "ACCORDO")
    assert elenco["contesto"]["accordo_versione"] == 2


@richiede_pg
async def test_profilo_di_tipologia_versionato(db_url) -> None:
    s = await _scenario(db_url)
    stato, profili = await _chiama("GET", "/profili", token=s.t_admin)
    assert stato == 200
    codici = {p["codice"] for p in profili["items"] if p["tipo"] == "tipologia"}
    assert codici == {"documento_informatico", "documento_amministrativo_informatico",
                      "aggregazione_documentale", "fattura", "registro_protocollo", "contratto"}
    _, versioni = await _chiama("GET", "/profili/tipologia:fattura/versioni", token=s.t_admin)
    # R11: v1 (R05) e v2 (profilo proprio del conservatore, adozione esplicita) predefinite
    assert [(v["versione"], v["origine"]) for v in versioni["items"]] == [
        (1, "predefinito"), (2, "predefinito")]
    ultima = versioni["items"][-1]
    campi = ultima["campi"] + [{"nome": "codice_destinatario_sdi", "tipo": "stringa",
                            "obbligatorio": True, "descrizione": "Codice destinatario SdI"}]
    corpo = {"risorsa": "profilo", "ambito": {"livello": "CONSERVATORE"},
             "modifiche": [{"tipologia": "fattura", "campi": campi}],
             "motivo": "Nuovo metadato obbligatorio per le fatture"}
    assert (await _chiama("POST", "/bozze", token=s.t_admin, corpo=corpo))[0] == 403
    b = await _bozza(s.t_super, corpo)
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_super)
    assert piano["bloccati"] == []
    assert [m["codice"] for m in piano["modifiche"]] == [
        "tipologia:fattura.codice_destinatario_sdi"]
    assert any("diventa obbligatorio" in a for a in piano["avvisi"])
    stato, esito = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_super,
                                 corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 200 and esito["esito"]["profilo"]["versione"] == 3
    _, versioni = await _chiama("GET", "/profili/tipologia:fattura/versioni", token=s.t_admin)
    assert [(v["versione"], v["origine"]) for v in versioni["items"]] == [
        (1, "predefinito"), (2, "predefinito"), (3, "conservatore")]
    # R11: la versione del conservatore eredita l'adozione esplicita della v2
    assert versioni["items"][-1]["adozione"] == "esplicita"
    assert piano["dettagli"]["adozione"] == "esplicita"


@richiede_pg
async def test_proposta_del_conservatore_accettata_dall_ente(db_url) -> None:
    s = await _scenario(db_url)
    altro = await _ente(db_url)
    admin_altro = _token(altro, await _utente(db_url, altro, "ADMIN_TENANT"), "ADMIN_TENANT")
    b = await _bozza(s.t_super, {
        "risorsa": "regole", "ambito": {"livello": "CONSERVATORE"},
        "modifiche": [{"regola": "RGL-103", "effetto": "IGNORA"}],
        "motivo": "Proposta: ignorare i metadati liberi", "proposta": {"enti": [s.tid]}})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_super)
    assert piano["dettagli"]["proposta"] == {"enti_destinatari": 1}
    stato, esito = await _chiama("POST", f"/bozze/{b['bozza_id']}/applica", token=s.t_super,
                                 corpo={"impronta_piano": piano["impronta_piano"]})
    assert stato == 200 and esito["esito"] == {"proposte": 1}
    # il valore del conservatore NON cambia
    assert await sql(db_url, "SELECT 1 FROM public.impostazioni_valori") == []
    _, elenco = await _chiama("GET", "/proposte", token=s.t_admin)
    proposta = elenco["items"][0]
    assert proposta["stato"] == "PROPOSTA" and proposta["azioni_consentite"]["accetta"]
    assert (await _chiama("GET", "/proposte", token=admin_altro))[1]["items"] == []
    stato, _ = await _chiama("POST", f"/proposte/{proposta['proposta_id']}/accetta",
                             token=admin_altro, corpo={"motivo": "Accettata da un altro ente"})
    assert stato == 404
    stato, _ = await _chiama("POST", f"/proposte/{proposta['proposta_id']}/accetta",
                             token=s.t_super, corpo={"motivo": "Il conservatore non decide"})
    assert stato == 403
    stato, ris = await _chiama("POST", f"/proposte/{proposta['proposta_id']}/accetta",
                               token=s.t_admin, corpo={"motivo": "Va bene per il nostro ente"})
    assert stato == 200 and ris["stato"] == "ACCETTATA"
    valore = await sql(db_url, "SELECT livello, valore::text AS v FROM public.impostazioni_valori")
    assert [(v["livello"], json.loads(v["v"])) for v in valore] == [("ENTE", "IGNORA")]
    stato, err = await _chiama("POST", f"/proposte/{proposta['proposta_id']}/rifiuta",
                               token=s.t_admin, corpo={"motivo": "Ci ho ripensato ora"})
    assert stato == 409 and err["codice"] == "CFG-412"
    storico = await sql(db_url, "SELECT proposta_id FROM public.impostazioni_storico")
    assert str(storico[0]["proposta_id"]) == proposta["proposta_id"]


@richiede_pg
async def test_proposta_di_valore_imposto_dalla_norma_bloccata(db_url) -> None:
    s = await _scenario(db_url)
    b = await _bozza(s.t_super, {
        "risorsa": "impostazioni", "ambito": {"livello": "CONSERVATORE"},
        "modifiche": [{"codice": "IDEMPOTENZA_OBBLIGATORIA", "valore": False}],
        "motivo": "Prova di proposta non ammessa", "proposta": {}})
    _, piano = await _chiama("POST", f"/bozze/{b['bozza_id']}/anteprima", token=s.t_super)
    assert any("si applica, non si propone" in x["motivo"] for x in piano["bloccati"])


# ── Validatore preventivo ───────────────────────────────────────────

async def _chiave_api(url: str, tid: str, uid: str) -> str:
    chiave = genera_chiave_api(PEPPER)
    await sql(url, "INSERT INTO public.api_keys (tenant_id, user_id, nome, key_hash, key_prefix, "
                   "permessi) VALUES ($1, $2, 'erp', $3, $4, "
                   "'[\"versamento:ud\", \"read\"]'::jsonb)",
              uuid.UUID(tid), uuid.UUID(uid), chiave.impronta, chiave.identificativo)
    return chiave.chiave


@richiede_pg
async def test_validatore_con_chiave_api_non_scrive(db_url) -> None:
    s = await _scenario(db_url)
    chiave = await _chiave_api(db_url, s.tid, s.admin)
    await sql(db_url, "UPDATE public.classi_documentali SET formati_ammessi = "
                      "'[\"application/pdf\"]'::jsonb, max_dimensione_mb = 1 WHERE id = $1",
              uuid.UUID(s.cid))
    prima = await _conteggi(db_url)
    indice = _indice()
    indice["documenti"][0]["parametri"].pop("oggetto")
    stato, esito = await _chiama("POST", "/valida", intestazioni={"X-Api-Key": chiave},
                                 corpo={"indice": indice})
    assert stato == 200, esito
    assert esito["esito"] == "NON_CONFORME"
    assert [(r["codice"], r["campo"]) for r in esito["rilievi"]] == [("RGL-101", "oggetto")]
    assert esito["regole_applicate"]["catalogo_regole"] == "4"  # R08, R11
    # con le informazioni dei file: dimensione oltre il limite della classe (1 MB)
    stato, esito = await _chiama("POST", "/valida", intestazioni={"X-Api-Key": chiave}, corpo={
        "indice": _indice(), "file_impronte": {"doc.pdf": {
            "impronta": "ab" * 32, "dimensione": 2 * 1024 * 1024, "formato": "application/pdf"}}})
    assert [r["codice"] for r in esito["rilievi"]] == ["RGL-104"]
    # forma: estensione e classe sconosciuta
    stato, esito = await _chiama("POST", "/valida", intestazioni={"X-Api-Key": chiave},
                                 corpo={"indice": _indice(nome="doc.png")})
    assert [r["codice"] for r in esito["rilievi"]] == ["SEC-006"]
    stato, esito = await _chiama("POST", "/valida", intestazioni={"X-Api-Key": chiave},
                                 corpo={"indice": _indice(classe="inesistente")})
    assert [r["codice"] for r in esito["rilievi"]] == ["VRS-105"]
    stato, esito = await _chiama("POST", "/valida", intestazioni={"X-Api-Key": chiave},
                                 corpo={"indice": _indice()})
    assert esito["esito"] == "CONFORME" and esito["rilievi"] == []
    assert await _conteggi(db_url) == prima
    # chiave sbagliata 401; la chiave non apre la console
    assert (await _chiama("POST", "/valida", intestazioni={"X-Api-Key": chiave[:-2] + "xx"},
                          corpo={"indice": _indice()}))[0] == 401
    assert (await _chiama("GET", "/impostazioni", intestazioni={"X-Api-Key": chiave}))[0] == 401


@richiede_pg
async def test_profilo_esportato_da_lo_stesso_esito_senza_rete(db_url) -> None:
    s = await _scenario(db_url)
    await sql(db_url, "UPDATE public.classi_documentali SET formati_ammessi = "
                      "'[\"application/pdf\"]'::jsonb WHERE id = $1", uuid.UUID(s.cid))
    stato, esportato = await _chiama("GET", "/profilo-validatore", token=s.t_admin,
                                     params={"classe": "fattura"})
    assert stato == 200 and esportato["formato"] == "verumdoc-profilo-validatore-1"
    assert esportato["impostazioni"]["FORMATI_AMMESSI"] == {"valore": ["application/pdf"],
                                                            "origine": "CLASSE"}
    for indice in (_indice(), _indice(nome="x.png"), _indice(extra_non_previsto="1")):
        _, online = await _chiama("POST", "/valida", token=s.t_admin, corpo={"indice": indice})
        ambiente, avvisi = ambiente_da_esportazione(esportato, adesso=datetime.now(UTC))
        offline = valida_versamento(indice, ambiente).come_dict()
        assert avvisi == []
        assert (offline["esito"], offline["rilievi"], offline["avvisi"]) == \
            (online["esito"], online["rilievi"], online["avvisi"])
