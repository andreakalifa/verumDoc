# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 servizi — code di scarto consultabili e «Rielabora» (VD-C-075), su PostgreSQL
reale migrato. Il consumer si prova con messaggi nella forma esatta scritta da
validation, aip-builder e notification (``registra_fallimento``,
``gestisci_messaggio``, ``_evento_dlq``), passando per ``elabora_lotto``.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

ADESSO = datetime.now(UTC)


def _token(ruolo: str = "SUPER_ADMIN", tenant: str | None = None,
           user_id: str | None = None) -> dict[str, str]:
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id=tenant or "00000000-0000-0000-0000-0000000000c0",
        user_id=user_id or str(uuid.uuid4()), ruolo=ruolo)}


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@dataclass
class Messaggio:
    topic: str
    partition: int
    offset: int
    value: object
    timestamp: int = int(ADESSO.timestamp() * 1000)


class Conferma:
    def __init__(self) -> None:
        self.volte = 0

    async def __call__(self) -> None:
        self.volte += 1


def _dlq_pipeline(topic_origine: str, pdv_id, tenant, *, codice="TECNICO",
                  tipo="tecnico") -> dict:
    """Come ``registra_fallimento`` di validation / aip-builder."""
    return {"topic_origine": topic_origine, "partizione": 7, "offset": 99,
            "evento": {"pdv_id": str(pdv_id), "tenant_id": str(tenant),
                       "indice": {"documenti": [{"nome_file": "a.pdf"}]}},
            "motivo": {"fase": "verifica", "codice": codice, "messaggio": "S3 non risponde",
                       "dettagli": {}, "tipo": tipo, "tentativi": 5},
            "registrato_il": (ADESSO - timedelta(hours=1)).isoformat()}


async def _pdv_in_errore(url, tenant, classe, *, giorni: float = 1, eliminato: bool = False,
                         numero: str | None = None) -> uuid.UUID:
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                   "metadata_json, data_aggiornamento, staging_eliminato_il) VALUES ($1, 'P', "
                   "'ERRORE', $2, $3, $4, '{}'::jsonb, $5, $6)", pdv, classe, uuid.uuid4(), tenant,
              ADESSO - timedelta(days=giorni), ADESSO if eliminato else None)
    await sql(url, "INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
                   "impronta_richiesta, chiave_idempotenza, attivo, chiave_ud_registro, "
                   "chiave_ud_anno, chiave_ud_numero) VALUES ($1, $2, $3, $3, $4, false, '', "
                   "'2026', $5)", pdv, tenant, uuid.uuid4().hex + uuid.uuid4().hex,
              f"k-{uuid.uuid4().hex}", numero or uuid.uuid4().hex[:10])
    return pdv


async def _registra(messaggi: list[Messaggio]) -> int:
    from app.services.consumer_scarti import elabora_lotto

    conferma = Conferma()
    nuovi = await elabora_lotto({"tp": messaggi}, conferma=conferma)
    assert conferma.volte == 1
    return nuovi


async def _id_scarto(url, chiave: str) -> int:
    [riga] = await sql(url, "SELECT id FROM public.scarti WHERE chiave_origine = $1", chiave)
    return int(riga["id"])


# ── Consumer: materializzazione idempotente ──────────────────────────

async def test_consumer_scarti_idempotente_tre_forme_e_illeggibile(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv_in_errore(db_url, tenant, classe)
    messaggi = [
        Messaggio("pdv.received.dlq", 0, 10, _dlq_pipeline("pdv.received", pdv, tenant)),
        Messaggio("notification.dlq", 1, 3, {
            "original_topic": "pdv.invalid", "partition": 2, "offset": 8,
            "event": {"pdv_id": str(pdv), "tenant_id": str(tenant)},
            "error": "ScartoEvento: cliente non risolvibile", "motivo": "cliente non risolvibile",
            "retries": 1, "failed_at": ADESSO.isoformat()}),
        Messaggio("notification.dlq", 1, 4, {
            "notifica_id": str(uuid.uuid4()), "tenant_id": str(tenant), "tipo": "pdv.invalid",
            "canale": "email", "oggetto_id": None, "tentativi": 4, "errore": "550",
            "fallita_il": ADESSO.isoformat()}),
        Messaggio("pdv.validated.dlq", 2, 1, b"\xff\xfe non json"),
    ]
    assert await _registra(messaggi) == 4
    # stesso lotto riletto (crash prima della conferma): nessun doppione
    assert await _registra(messaggi) == 0
    righe = await sql(db_url, "SELECT coda, servizio, pdv_id, tenant_id, codice, tentativi, "
                              "motivo, topic_origine FROM public.scarti ORDER BY id")
    assert [r["coda"] for r in righe] == ["pdv.received.dlq", "notification.dlq",
                                          "notification.dlq", "pdv.validated.dlq"]
    assert righe[0]["servizio"] == "validation" and righe[0]["pdv_id"] == pdv
    assert righe[0]["tenant_id"] == tenant and righe[0]["tentativi"] == 5
    assert righe[0]["motivo"] == "S3 non risponde" and righe[0]["topic_origine"] == "pdv.received"
    assert righe[1]["codice"] == "SCARTO_EVENTO" and righe[1]["topic_origine"] == "pdv.invalid"
    assert righe[2]["codice"] == "NON_PARTITA"
    assert righe[3]["codice"] == "FORMATO" and righe[3]["servizio"] == "aip-builder"


async def test_consumer_scarti_scrittura_fallita_offset_non_confermati(db_url,
                                                                      monkeypatch) -> None:
    from app.services import consumer_scarti, scarti

    async def guasto(session, righe):
        raise RuntimeError("database giù")

    monkeypatch.setattr(scarti, "registra", guasto)
    conferma = Conferma()
    with pytest.raises(RuntimeError):
        await consumer_scarti.elabora_lotto(
            {"tp": [Messaggio("notification.dlq", 0, 1, {"x": 1})]}, conferma=conferma)
    assert conferma.volte == 0


async def test_rilevamento_indicizzazione_in_errore(db_url) -> None:
    from app.services.consumer_scarti import rileva_tabelle

    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    await sql(db_url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, "
                      "tenant_id) VALUES ($1, 'P', 'CONSERVATO', $2, $3, $4)",
              pdv, classe, uuid.uuid4(), tenant)
    await sql(
        db_url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket,"
                      " hash_sha256) VALUES ($1, $2, $3, $4, 'CONSERVATO', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:x:{aip}", "a" * 64)
    await sql(db_url, "INSERT INTO public.indicizzazione_aip (aip_id, tenant_id, stato, tentativi,"
                      " ultimo_errore) VALUES ($1, $2, 'ERRORE', 4, 'OpenSearch 503')", aip, tenant)
    assert await rileva_tabelle() == 1
    assert await rileva_tabelle() == 0           # idempotente
    [riga] = await sql(db_url, "SELECT id, coda, servizio, motivo FROM public.scarti")
    assert (riga["coda"], riga["servizio"]) == ("indicizzazione_aip", "distribution")
    async with _client() as c:
        piano = await c.post("/api/dashboard/scarti/rielabora/anteprima",
                             json={"ids": [riga["id"]]}, headers=_token())
        assert piano.json()["rielaborabili"] == 1, piano.text
        r = await c.post("/api/dashboard/scarti/rielabora/applica", headers=_token(), json={
            "ids": [riga["id"]], "impronta_piano": piano.json()["impronta_piano"],
            "motivo": "OpenSearch di nuovo disponibile"})
    assert r.status_code == 200, r.text
    [ind] = await sql(db_url, "SELECT stato, tentativi FROM public.indicizzazione_aip")
    assert (ind["stato"], ind["tentativi"]) == ("IN_CODA", 0)


# ── API: permessi, elenco, dettaglio ─────────────────────────────────

@pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "OPERATORE", "VIEWER"])
async def test_scarti_solo_conservatore(db_url, ruolo) -> None:
    async with _client() as c:
        assert (await c.get("/api/dashboard/scarti")).status_code == 401
        r = await c.get("/api/dashboard/scarti", headers=_token(ruolo, str(uuid.uuid4())))
        assert r.status_code == 403 and r.json()["codice"] == "AUTH-010"
        r = await c.post("/api/dashboard/scarti/rielabora/anteprima", json={"ids": [1]},
                         headers=_token(ruolo, str(uuid.uuid4())))
        assert r.status_code == 403


async def test_elenco_scarti_a_cursore_e_filtri(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    messaggi = [Messaggio("pdv.received.dlq", 0, i, _dlq_pipeline(
        "pdv.received", uuid.uuid4(), tenant)) for i in range(5)]
    messaggi.append(Messaggio("notification.dlq", 0, 1, {"notifica_id": str(uuid.uuid4())}))
    await _registra(messaggi)
    async with _client() as c:
        h = _token("RSC_CONSERVATORE")
        p1 = (await c.get("/api/dashboard/scarti?limit=4", headers=h)).json()
        assert len(p1["items"]) == 4 and p1["altri"] and "total" not in p1
        p2 = (await c.get(f"/api/dashboard/scarti?limit=4&cursore={p1['cursore']}",
                          headers=h)).json()
        assert len(p2["items"]) == 2 and not p2["altri"]
        ids = [i["id"] for i in p1["items"] + p2["items"]]
        assert ids == sorted(ids, reverse=True) and len(set(ids)) == 6
        assert p1["items"][0]["azioni_consentite"] == ["rielabora", "archivia"]
        solo = (await c.get("/api/dashboard/scarti?coda=notification.dlq", headers=h)).json()
        assert [i["coda"] for i in solo["items"]] == ["notification.dlq"]
        per_ente = (await c.get(f"/api/dashboard/scarti?tenant_id={tenant}", headers=h)).json()
        assert len(per_ente["items"]) == 5 and per_ente["items"][0]["ente"] == "Cliente di prova"
        assert (await c.get("/api/dashboard/scarti?cursore=xx", headers=h)).json()["codice"] \
            == "DSH-104"
        assert (await c.get("/api/dashboard/scarti?tenant_id=abc", headers=h)).status_code == 422
        code = (await c.get("/api/dashboard/scarti/code", headers=h)).json()["code"]
        assert {q["coda"]: q["da_gestire"] for q in code}["pdv.received.dlq"] == 5


async def test_dettaglio_senza_contenuti_e_404(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv_in_errore(db_url, tenant, classe)
    await _registra([Messaggio("pdv.received.dlq", 0, 1,
                               _dlq_pipeline("pdv.received", pdv, tenant))])
    sid = await _id_scarto(db_url, "kafka:pdv.received.dlq:0:1")
    async with _client() as c:
        r = await c.get(f"/api/dashboard/scarti/{sid}", headers=_token())
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["valutazione"]["rielaborabile"] is True
        assert "indice" in corpo["messaggio"]["campi_evento"]
        assert "a.pdf" not in r.text            # nessun contenuto (REGOLE §19.9)
        assert (await c.get("/api/dashboard/scarti/999999", headers=_token())).json()["codice"] \
            == "DSH-404-SCARTO"


# ── «Rielabora»: anteprima e applicazione (REGOLE §13) ───────────────

async def test_rielabora_pdv_anteprima_non_scrive_applica_riparte_dallo_staging(db_url) -> None:
    """Il PdV torna RICEVUTO, l'impronta torna attiva, l'evento originale (con le chiavi
    di staging) va nell'outbox verso pdv.received; scarto RIELABORATO; audit."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv_in_errore(db_url, tenant, classe, giorni=10)
    messaggio = _dlq_pipeline("pdv.received", pdv, tenant)
    messaggio["evento"]["docs_s3_keys"] = [{"s3_key": f"versamenti/{tenant}/{pdv}/0000-a.pdf"}]
    await _registra([Messaggio("pdv.received.dlq", 0, 1, messaggio)])
    sid = await _id_scarto(db_url, "kafka:pdv.received.dlq:0:1")
    utente = str(uuid.uuid4())
    async with _client() as c:
        anteprima = await c.post("/api/dashboard/scarti/rielabora/anteprima",
                                 json={"ids": [sid]}, headers=_token())
        assert anteprima.status_code == 200, anteprima.text
        piano = anteprima.json()
        assert piano["rielaborabili"] == 1 and piano["bloccati"] == 0
        assert "RICEVUTO" in piano["elementi"][0]["azione"]
        # l'anteprima non scrive
        assert (await sql(db_url, "SELECT stato FROM public.pdv WHERE id = $1", pdv))[0][
            "stato"] == "ERRORE"
        assert await sql(db_url, "SELECT 1 FROM public.outbox_eventi") == []
        # motivo obbligatorio (≥ 10 caratteri)
        corto = await c.post("/api/dashboard/scarti/rielabora/applica", headers=_token(), json={
            "ids": [sid], "impronta_piano": piano["impronta_piano"], "motivo": "breve"})
        assert corto.status_code == 422
        r = await c.post("/api/dashboard/scarti/rielabora/applica",
                         headers=_token(user_id=utente), json={
                             "ids": [sid], "impronta_piano": piano["impronta_piano"],
                             "motivo": "S3 di nuovo raggiungibile dopo il guasto"})
    assert r.status_code == 200, r.text
    assert r.json()["rielaborati"] == 1
    [p] = await sql(db_url, "SELECT stato FROM public.pdv WHERE id = $1", pdv)
    assert p["stato"] == "RICEVUTO"
    [imp] = await sql(db_url, "SELECT attivo FROM public.versamento_impronte WHERE pdv_id = $1",
                      pdv)
    assert imp["attivo"] is True
    [ev] = await sql(db_url, "SELECT topic, chiave, tenant_id, payload FROM public.outbox_eventi")
    assert (ev["topic"], ev["chiave"], ev["tenant_id"]) == ("pdv.received", str(pdv), tenant)
    assert json.loads(ev["payload"]) == messaggio["evento"]
    [s] = await sql(db_url, "SELECT stato, gestito_da, motivo_gestione, rielaborazioni "
                            "FROM public.scarti WHERE id = $1", sid)
    assert (s["stato"], str(s["gestito_da"]), s["rielaborazioni"]) == ("RIELABORATO", utente, 1)
    [a] = await sql(db_url, "SELECT evento, dettagli FROM public.audit_global "
                            "WHERE evento = 'scarti.rielaborati'")
    dettagli = json.loads(a["dettagli"])
    assert dettagli["rielaborati"] == 1 and dettagli["scarti"] == [sid]
    assert dettagli["motivo"].startswith("S3 di nuovo")


async def test_rielabora_pdv_validated_riparte_da_validato(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv = await _pdv_in_errore(db_url, tenant, classe)
    await _registra([Messaggio("pdv.validated.dlq", 0, 1,
                               _dlq_pipeline("pdv.validated", pdv, tenant, codice="AIP-PDV-002",
                                             tipo="permanente"))])
    sid = await _id_scarto(db_url, "kafka:pdv.validated.dlq:0:1")
    async with _client() as c:
        piano = (await c.post("/api/dashboard/scarti/rielabora/anteprima", json={"ids": [sid]},
                              headers=_token())).json()
        assert any("permanente" in a for a in piano["elementi"][0]["avvisi"])
        r = await c.post("/api/dashboard/scarti/rielabora/applica", headers=_token(), json={
            "ids": [sid], "impronta_piano": piano["impronta_piano"],
            "motivo": "Corretta la configurazione del bucket"})
    assert r.status_code == 200, r.text
    assert (await sql(db_url, "SELECT stato FROM public.pdv WHERE id = $1", pdv))[0]["stato"] \
        == "VALIDATO"
    [ev] = await sql(db_url, "SELECT topic FROM public.outbox_eventi")
    assert ev["topic"] == "pdv.validated"


async def test_rielabora_bloccati_staging_scaduto_eliminato_doppione_stato(db_url) -> None:
    """Il piano dice perché: staging eliminato, staging scaduto (90 giorni, VD-P-36),
    stessa UD già versata di nuovo, PdV non più in ERRORE, formato ignoto."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    eliminato = await _pdv_in_errore(db_url, tenant, classe, eliminato=True)
    scaduto = await _pdv_in_errore(db_url, tenant, classe, giorni=91)
    doppione = await _pdv_in_errore(db_url, tenant, classe, numero="UD-42")
    altro = await _pdv_in_errore(db_url, tenant, classe, numero="UD-42")
    await sql(db_url, "UPDATE public.versamento_impronte SET attivo = true WHERE pdv_id = $1",
              altro)
    await sql(db_url, "UPDATE public.pdv SET stato = 'CONSERVATO' WHERE id = $1", altro)
    conservato = altro
    messaggi = [Messaggio("pdv.received.dlq", 0, i, _dlq_pipeline("pdv.received", p, tenant))
                for i, p in enumerate((eliminato, scaduto, doppione, conservato))]
    messaggi.append(Messaggio("pdv.received.dlq", 0, 9, {"strano": True}))
    await _registra(messaggi)
    async with _client() as c:
        r = await c.post("/api/dashboard/scarti/rielabora/anteprima",
                         json={"filtri": {"coda": "pdv.received.dlq"}}, headers=_token())
    piano = r.json()
    assert piano["rielaborabili"] == 0 and piano["bloccati"] == 5
    motivi = {e["pdv_id"]: e["motivo_blocco"] for e in piano["elementi"]}
    assert "eliminate" in motivi[str(eliminato)]
    assert "scadute" in motivi[str(scaduto)] and "90 giorni" in motivi[str(scaduto)]
    assert "doppione" in motivi[str(doppione)]
    assert "CONSERVATO" in motivi[str(conservato)]
    assert any("non riconosciuto" in (e["motivo_blocco"] or "") for e in piano["elementi"])
    async with _client() as c:
        r = await c.post("/api/dashboard/scarti/rielabora/applica", headers=_token(), json={
            "filtri": {"coda": "pdv.received.dlq"}, "impronta_piano": piano["impronta_piano"],
            "motivo": "prova di rielaborazione in blocco"})
    assert r.status_code == 422 and r.json()["codice"] == "DSH-422-PIANO"


async def test_rielabora_piano_cambiato_409_niente_applicato(db_url) -> None:
    tenant, classe = await crea_tenant_e_classe(db_url)
    pdv1 = await _pdv_in_errore(db_url, tenant, classe)
    await _registra([Messaggio("pdv.received.dlq", 0, 1,
                               _dlq_pipeline("pdv.received", pdv1, tenant))])
    async with _client() as c:
        piano = (await c.post("/api/dashboard/scarti/rielabora/anteprima",
                              json={"filtri": {"coda": "pdv.received.dlq"}},
                              headers=_token())).json()
        # nel frattempo arriva un altro scarto: il piano ricalcolato è diverso
        pdv2 = await _pdv_in_errore(db_url, tenant, classe)
        await _registra([Messaggio("pdv.received.dlq", 0, 2,
                                   _dlq_pipeline("pdv.received", pdv2, tenant))])
        r = await c.post("/api/dashboard/scarti/rielabora/applica", headers=_token(), json={
            "filtri": {"coda": "pdv.received.dlq"}, "impronta_piano": piano["impronta_piano"],
            "motivo": "rielaborazione dopo il guasto"})
    assert r.status_code == 409 and r.json()["codice"] == "DSH-409-PIANO"
    assert r.json()["dettagli"]["piano"]["rielaborabili"] == 2
    assert await sql(db_url, "SELECT 1 FROM public.outbox_eventi") == []
    assert {x["stato"] for x in await sql(db_url, "SELECT stato FROM public.pdv")} == {"ERRORE"}


async def test_rielabora_notifica_non_partita_e_evento_del_consumer(db_url) -> None:
    tenant, _ = await crea_tenant_e_classe(db_url)
    nid = uuid.uuid4()
    await sql(
        db_url, "INSERT INTO public.notifications (id, tenant_id, tipo, canale, destinatario, "
                      "stato, tentativi, errore) VALUES ($1, $2, 'pdv.invalid', 'email', "
                      "'a@example.org', 'NON_PARTITA', 4, '550')", nid, tenant)
    await _registra([
        Messaggio("notification.dlq", 0, 1, {"notifica_id": str(nid), "tenant_id": str(tenant),
                                             "tentativi": 4, "errore": "550"}),
        Messaggio("notification.dlq", 0, 2, {
            "original_topic": "pdv.invalid", "partition": 0, "offset": 1,
            "event": {"pdv_id": "p-1", "tenant_id": str(tenant)}, "error": "Timeout",
            "motivo": "ritentativi esauriti", "retries": 5}),
        Messaggio("notification.dlq", 0, 3, {
            "original_topic": "argomento.sconosciuto", "event": {}, "error": "x"}),
    ])
    async with _client() as c:
        piano = (await c.post("/api/dashboard/scarti/rielabora/anteprima",
                              json={"filtri": {"coda": "notification.dlq"}},
                              headers=_token("RSC_CONSERVATORE"))).json()
        assert (piano["rielaborabili"], piano["bloccati"]) == (2, 1)
        r = await c.post("/api/dashboard/scarti/rielabora/applica",
                         headers=_token("RSC_CONSERVATORE"), json={
                             "filtri": {"coda": "notification.dlq"},
                             "impronta_piano": piano["impronta_piano"],
                             "motivo": "server di posta tornato raggiungibile"})
    assert r.json()["rielaborati"] == 2, r.text
    [n] = await sql(db_url, "SELECT stato, tentativi FROM public.notifications WHERE id = $1", nid)
    assert (n["stato"], n["tentativi"]) == ("IN_CODA", 0)
    [ev] = await sql(db_url, "SELECT topic, payload FROM public.outbox_eventi")
    assert ev["topic"] == "pdv.invalid" and json.loads(ev["payload"])["pdv_id"] == "p-1"


async def test_archivia_con_motivo_stato_atteso_e_audit(db_url) -> None:
    await _registra([Messaggio("notification.dlq", 0, 1, {"x": 1})])
    sid = await _id_scarto(db_url, "kafka:notification.dlq:0:1")
    async with _client() as c:
        h = _token("RSC_CONSERVATORE")
        assert (await c.post(f"/api/dashboard/scarti/{sid}/archivia", json={"motivo": "no"},
                             headers=h)).status_code == 422
        r = await c.post(f"/api/dashboard/scarti/{sid}/archivia",
                         json={"motivo": "messaggio di prova senza effetti"}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["stato"] == "ARCHIVIATO" and r.json()["azioni_consentite"] == []
        di_nuovo = await c.post(f"/api/dashboard/scarti/{sid}/archivia",
                                json={"motivo": "messaggio di prova senza effetti"}, headers=h)
        assert di_nuovo.status_code == 409 and di_nuovo.json()["codice"] == "DSH-409-SCARTO"
        assert (await c.post("/api/dashboard/scarti/424242/archivia",
                             json={"motivo": "messaggio inesistente"}, headers=h)
                ).json()["codice"] == "DSH-404-SCARTO"
    [a] = await sql(db_url, "SELECT dettagli FROM public.audit_global "
                            "WHERE evento = 'scarti.archiviato'")
    assert json.loads(a["dettagli"])["scarto_id"] == sid
