# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 3 di R05 — correzioni dello scarto e del ciclo di vita (T1, T5–T8).

PostgreSQL reale (template ``VD_TEST_DATABASE_URL``), S3 moto con Object Lock,
registro delle impronte finto (contratto Q3). Ogni test fallisce senza la sua
correzione (prove di mutazione nel rapporto ``correzioni_3``).

  - T1: dopo un'INTERRUZIONE del lavoratore (pod ucciso: nessun ``except``
    gira) che ha già cancellato una versione, un legal hold arrivato prima
    della ripresa non fa più dichiarare «nulla distrutto»: la decisione viene
    dallo storage (inventario registrato prima della prima cancellazione), non
    da un contatore in memoria → PdA ELIMINAZIONE_INCOMPLETA, oggetto
    NON_COMPLETATO, pratica ESEGUITA_CON_ECCEZIONI, allarme. Con l'interruzione
    PRIMA di ogni cancellazione lo storage conferma «nulla distrutto».
  - T5: l'esito di esecuzione (``esiti_esecuzione``) lo scrive il lavoratore
    alla chiusura dell'oggetto ELIMINATO; il login dell'API non lo può
    scrivere e il controllo d'avvio riconosce i due login.
  - T6: la ripresa ricontrolla i blocchi lato database (classe diventata a
    conservazione permanente): nessuna ulteriore cancellazione.
  - T7: nessuna transazione aperta durante le scritture nel WORM del verbale e
    dell'export dei registri.
  - T8: il riallineamento del nulla osta avviene solo dopo i controlli
    d'accesso (un utente di un altro ente non fa scrivere nulla).
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import asyncpg
import pytest

from tests.infra_reale import _asyncpg_dsn, richiede_pg, sql
from tests.supporto import (
    client_app,
    crea_ente,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
    versioni,
)
from tests.test_r05_revisione2_natura_scarto import _fino_ad_autorizzata
from tests.test_r05_revisione2_scarto_incompleto import (
    _lavora_finche,
    _legal_hold_durante_la_cancellazione,
    _oggetti,
    _stato,
    _stato_pda,
    _verbali,
)
from tests.test_r05_revisione_scarto import _ente_con_pda, _fino_a_eseguibile, _tr

pytestmark = [pytest.mark.integration, richiede_pg]


class Ucciso(BaseException):
    """SIGKILL/OOM simulato: nessun handler applicativo lo intercetta."""


async def _interrompi(monkeypatch, db_url, dip, sc, pid, *, dopo_la_cancellazione: bool) -> None:
    """Esecuzione avviata e lavoratore ucciso alla prima cancellazione (prima o
    dopo averla fatta); poi il lease del pod morto scade."""
    from app.infrastructure import storage

    originale = storage.elimina_versione
    fatto = {"n": 0}

    def muori(s3, bucket, oggetto):
        fatto["n"] += 1
        if dopo_la_cancellazione:
            originale(s3, bucket, oggetto)
        raise Ucciso("pod terminato")

    monkeypatch.setattr(storage, "elimina_versione", muori)
    with pytest.raises(Ucciso):
        await lavora(dip, giri=3)
    assert fatto["n"] == 1
    monkeypatch.setattr(storage, "elimina_versione", originale)
    await sql(db_url, "UPDATE public.pratiche SET lease_scade_il = now() - interval '1 s', "
                      "prossimo_tentativo = now() - interval '1 s' WHERE id = $1::uuid", str(pid))


# ── T1 ───────────────────────────────────────────────────────────────────

async def test_interruzione_dopo_una_cancellazione_poi_legal_hold_mai_nulla_distrutto(
        db_url, s3_ciclo, monkeypatch) -> None:
    from app.application import metriche
    from app.config import settings

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    (aip, urn), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _interrompi(monkeypatch, db_url, dip, sc, pid, dopo_la_cancellazione=True)
    dopo_crash = versioni(sc["s3"], prefisso)
    assert len(dopo_crash) == len(iniziali) - 1
    # il contatore in memoria è perso; l'inventario era già registrato
    oggetto = (await _oggetti(db_url, pid))[aip]
    dettagli = json.loads(oggetto["dettagli"])
    assert dettagli["versioni_eliminate"] == 0 and len(dettagli["inventario"]) == len(iniziali)
    # ordine dell'autorità giudiziaria su una versione rimasta, prima della ripresa
    sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip", Key=dopo_crash[0],
                                   LegalHold={"Status": "ON"})
    await _lavora_finche(db_url, dip, pid)

    stato = await _stato(db_url, pid)
    assert (stato["stato"], stato["lavoro"]) == ("ESEGUITA_CON_ECCEZIONI", None)
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("NON_COMPLETATO", "LEGAL_HOLD")
    dettagli = json.loads(oggetto["dettagli"])
    assert dettagli["versioni_eliminate"] == 1 and dettagli["verifica_storage"] == "inventario"
    assert await _stato_pda(db_url, aip) == "ELIMINAZIONE_INCOMPLETA"
    assert versioni(sc["s3"], prefisso) == dopo_crash
    assert dip.registro.foglie == {}                       # nessun tombstone
    [(_, verbale)] = await _verbali(db_url, sc["s3"], pid)
    assert "<Esito>ESEGUITO_CON_ECCEZIONI</Esito>" in verbale
    assert 'esito="NON_COMPLETATO" motivo="LEGAL_HOLD" versioniEliminate="1"' in verbale
    # allarme
    metriche.registra(__import__("prometheus_client").CollectorRegistry())
    assert (await metriche.aggiorna())["incompleti"] == 1
    assert len(await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                                 "'ciclo_vita.scarto.pda_eliminazione_incompleta'")) == 1
    # (revisione 4, U2: la riga AUTORIZZAZIONI_VERIFICATE del lavoratore non è un esito di PdA)
    assert await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione "
                             "WHERE esito = 'ELIMINATO'") == []


async def test_interruzione_prima_di_cancellare_poi_legal_hold_nulla_distrutto(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Interruzione con il PdA già in ELIMINAZIONE ma PRIMA della prima
    cancellazione: lo storage conferma che l'inventario è intatto → PdA di
    nuovo CONSERVATO, oggetto BLOCCATO, pratica ESEGUITA (nessuna eccezione)."""
    from app.config import settings

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _interrompi(monkeypatch, db_url, dip, sc, pid, dopo_la_cancellazione=False)
    assert versioni(sc["s3"], prefisso) == iniziali
    sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip", Key=iniziali[0],
                                   LegalHold={"Status": "ON"})
    await _lavora_finche(db_url, dip, pid)
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("BLOCCATO", "LEGAL_HOLD")
    assert await _stato_pda(db_url, aip) == "CONSERVATO"
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"


def test_nulla_distrutto_solo_se_lo_storage_lo_conferma() -> None:
    from app.domain import scarto as dom

    v = [dom.OggettoWorm("a/b/x", "1", None, False), dom.OggettoWorm("a/b/y", "2", None, False)]
    inventario = dom.inventario(v)
    assert dom.nulla_distrutto(inventario, v, 0)
    assert not dom.nulla_distrutto(inventario, v[:1], 0)        # una versione manca
    assert not dom.nulla_distrutto(inventario, v, 1)            # contata una cancellazione
    assert not dom.nulla_distrutto(None, v, 0)                  # inventario non registrato
    assert not dom.nulla_distrutto(inventario, None, 0)         # storage non leggibile
    assert dom.versioni_distrutte(inventario, v[1:]) == [("a/b/x", "1")]


# ── T5 ───────────────────────────────────────────────────────────────────

async def test_esito_di_esecuzione_scritto_dal_lavoratore(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"
    righe = await sql(db_url, "SELECT tipo, pratica_id::text AS p, aip_id, esito, scritto_da "
                              "FROM public.esiti_esecuzione WHERE esito = 'ELIMINATO'")
    assert [(r["tipo"], r["p"], r["aip_id"], r["esito"]) for r in righe] == [
        ("SCARTO", str(pid), aip, "ELIMINATO")]
    assert righe[0]["scritto_da"] == "vd_prova_ciclo_vita_lavoratore"


async def test_login_dell_api_e_del_lavoratore_riconosciuti_all_avvio(db_url) -> None:
    """Il controllo d'avvio (fuori da sviluppo/test blocca): l'API con il login
    del lavoratore, o il lavoratore con il login dell'API, sono rifiutati."""
    from app.main import problemi_login_db

    from libs.shared.db import close_db, init_db
    from libs.shared.db.ruolo_prova import (
        GRUPPO_LAVORATORE_CICLO_VITA,
        RUOLO_LAVORATORE_CICLO_VITA_PROVA,
        url_ruolo_servizio,
    )

    # login del lavoratore (quello della fixture)
    assert await problemi_login_db(lavoratore=True) == []
    assert "distinto da quello del lavoratore" in " ".join(
        await problemi_login_db(lavoratore=False))
    # login dell'API: solo verumdoc_applicazione
    await close_db()
    await init_db(await url_ruolo_servizio(db_url))
    try:
        assert await problemi_login_db(lavoratore=False) == []
        assert "verumdoc_ciclo_vita_lavoratore" in " ".join(
            await problemi_login_db(lavoratore=True))
    finally:
        await close_db()
        await init_db(await url_ruolo_servizio(db_url, RUOLO_LAVORATORE_CICLO_VITA_PROVA,
                                               (GRUPPO_LAVORATORE_CICLO_VITA,)))


# ── T6 ───────────────────────────────────────────────────────────────────

async def test_la_ripresa_ricontrolla_i_blocchi_del_database(db_url, s3_ciclo,
                                                            monkeypatch) -> None:
    """Scarto incompleto (legal hold a metà PdA); il blocco cessa ma la classe
    diventa a conservazione PERMANENTE: la ripresa non cancella nulla, l'oggetto
    resta NON_COMPLETATO con il motivo (prima: cancellava senza ricontrollare)."""
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    chiave_hold = f"{sc['tenant']}/{aip}/manifest.json"
    originale = storage.elimina_versione
    _legal_hold_durante_la_cancellazione(monkeypatch, sc["s3"], chiave_hold)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
        assert (await _stato(db_url, pid))["stato"] == "ESEGUITA_CON_ECCEZIONI"
        monkeypatch.setattr(storage, "elimina_versione", originale)
        sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip", Key=chiave_hold,
                                       LegalHold={"Status": "OFF"})
        # la classe documentale diventa a conservazione permanente
        await sql(db_url, "UPDATE public.classi_documentali c SET conservazione_permanente = "
                          "true FROM public.pdv v JOIN public.aip a ON a.pdv_id = v.id "
                          "WHERE a.id = $1 AND c.id = v.classe_doc_id", aip)
        ripresa = await _tr(c, pid, sc["rsc"], "ESEGUITA_CON_ECCEZIONI", "ESEGUITA")
        assert ripresa.status_code == 202, ripresa.text
        await _lavora_finche(db_url, dip, pid)
    assert versioni(sc["s3"], f"{sc['tenant']}/{aip}/") == [chiave_hold]   # nulla di più
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("NON_COMPLETATO",
                                                     "CONSERVAZIONE_PERMANENTE")
    assert await _stato_pda(db_url, aip) == "ELIMINAZIONE_INCOMPLETA"
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA_CON_ECCEZIONI"
    assert dip.registro.foglie == {}


# ── T7 ───────────────────────────────────────────────────────────────────

def _sorveglia_transazioni(monkeypatch, db_url) -> list[int]:
    """Durante ogni scrittura nel WORM conta le sessioni del login dei servizi
    con una transazione aperta sul database del test."""
    from app.infrastructure import storage

    database = urlsplit(db_url).path.lstrip("/")
    dsn = _asyncpg_dsn(db_url)
    originale = storage.scrivi_worm_da_file
    aperte: list[int] = []

    async def conta() -> int:
        conn = await asyncpg.connect(dsn)
        try:
            return int(await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = $1 "
                "AND usename LIKE 'vd_prova%' AND xact_start IS NOT NULL", database))
        finally:
            await conn.close()

    def scrivi(*a, **kw):
        aperte.append(asyncio.run(conta()))
        return originale(*a, **kw)

    monkeypatch.setattr(storage, "scrivi_worm_da_file", scrivi)
    return aperte


async def test_verbale_scritto_nel_worm_fuori_dalle_transazioni(db_url, s3_ciclo,
                                                                monkeypatch) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        aperte = _sorveglia_transazioni(monkeypatch, db_url)
        await _lavora_finche(db_url, dip, pid)
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"
    assert aperte == [0], aperte         # una scrittura (il verbale), nessuna transazione


async def test_export_registri_e_manifesto_fuori_dalle_transazioni(db_url, s3_ciclo,
                                                                   monkeypatch) -> None:
    """Restituzione: export dei registri dell'ente (grande per un ente grande) e
    manifesto scritti nel WORM senza transazioni aperte (prima: dentro la
    transazione con la pratica bloccata)."""
    from tests.test_riversamento import test_restituzione_con_export_registri

    aperte = _sorveglia_transazioni(monkeypatch, db_url)
    await test_restituzione_con_export_registri(db_url, s3_ciclo)
    # allegato dell'utente (piccolo, nella transazione: ammesso), export, manifesto
    assert aperte[-2:] == [0, 0], aperte


# ── T8 ───────────────────────────────────────────────────────────────────

async def test_riallineamento_del_nulla_osta_dopo_i_controlli_d_accesso(db_url,
                                                                        s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1, natura="PRIVATO")
    estraneo_ente = await crea_ente(db_url)
    estraneo = await crea_utente(db_url, estraneo_ente, "ADMIN_TENANT")
    chi_estraneo = intestazioni(estraneo_ente, "ADMIN_TENANT", estraneo)
    async with client_app() as c:
        pid = await _fino_ad_autorizzata(c, sc)
        await sql(db_url, "UPDATE public.tenants SET natura = 'PA' WHERE id = $1", sc["tenant"])
        # utente di un altro ente: 404 e NESSUNA scrittura
        r = await _tr(c, pid, chi_estraneo, "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 404, r.text
        # utente dell'ente senza il ruolo per → ESEGUIBILE: 403 e nessuna scrittura
        r = await _tr(c, pid, sc["admin"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 403, r.text
        riga = (await sql(db_url, "SELECT richiede_nulla_osta FROM public.pratiche "
                                  "WHERE id = $1::uuid", pid))[0]
        assert riga["richiede_nulla_osta"] is False
        assert await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                                 "'ciclo_vita.scarto.nulla_osta_richiesto'") == []
        # il conservatore: riallineato (409 verso il nulla osta), audit con l'utente
        r = await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-TRANSIZIONE", r.text
    voci = await sql(db_url, "SELECT user_id FROM public.audit_global WHERE evento = "
                             "'ciclo_vita.scarto.nulla_osta_richiesto'")
    assert len(voci) == 1 and voci[0]["user_id"] is not None
