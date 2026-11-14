# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente di R05 — correzioni dello SCARTO (rilievi R1, R2, R9, R10, R11).

PostgreSQL reale (template ``VD_TEST_DATABASE_URL``), S3 moto con Object Lock,
registro delle impronte finto che rispetta il contratto Q3. Ogni test riproduce
il difetto trovato dalla revisione e fallisce senza la correzione (prove di
mutazione nel rapporto ``correzioni_1``).

  - R1: un'esecuzione AVVIATA non si respinge più (409 LC-409-IN-ESECUZIONE) e
    si conclude con verbale e tombstone per ogni PdA cancellato; prima che il
    lavoratore la prenda, l'esecuzione chiesta si può ancora respingere;
  - R2: un PdA senza oggetti nel WORM (o con oggetti dichiarati dal database ma
    assenti) è BLOCCATO, mai «eliminato» con verbale e tombstone;
  - R9: nulla osta della Soprintendenza obbligatorio per ogni ente non
    dichiarato PRIVATO, con o senza codice IPA;
  - R10: ricontrollo e passaggio a ELIMINAZIONE con il PdA bloccato e
    ``rowcount`` controllato: uno stato cambiato dopo l'elenco del WORM blocca;
  - R11: il lease si rinnova dentro il passo; perso il lease, nessun'altra
    cancellazione.
"""
from __future__ import annotations

import pytest

from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    PASSATO,
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_pda,
    crea_pdv,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
    oggetti_pda,
    versioni,
)

pytestmark = [pytest.mark.integration, richiede_pg]

from tests.firme_prova import autorizzazione  # noqa: E402 — R12a (VD-C-145)

CORPO = {"criteri": {"classi": ["contratti"], "anni": [2015]},
         "motivo": "Scarto dei contratti 2015 secondo il piano di conservazione"}


async def _tr(c, pid, chi, da, a, allegati=()):
    return await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi,
                        json={"stato_atteso": da, "nuovo_stato": a, "allegati": list(allegati),
                              "motivo": "Passaggio di stato della prova"})


async def _ente_con_pda(db_url, s3_ciclo, n: int, *, oggetti: bool = True,
                        natura: str | None = "PRIVATO"):
    tenant = await crea_ente(db_url, natura=natura)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    ente_c = await crea_ente(db_url)
    rsc = await crea_utente(db_url, ente_c, "RSC_CONSERVATORE")
    classe = await crea_classe(db_url, tenant)
    s3 = bucket_worm(s3_ciclo)
    aips = []
    for _ in range(n):
        pdv = await crea_pdv(db_url, tenant, classe, admin)
        aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
        if oggetti:
            oggetti_pda(s3, tenant, aip, fino_a=PASSATO)
        aips.append((aip, urn))
    return {"tenant": tenant, "admin": intestazioni(tenant, "ADMIN_TENANT", admin),
            "rsc": intestazioni(ente_c, "RSC_CONSERVATORE", rsc), "s3": s3, "pda": aips}


async def _fino_a_eseguibile(c, sc) -> str:
    """Proposta applicata e autorizzata (ente privato): pratica ESEGUIBILE."""
    corpo = {"ente": str(sc["tenant"]), **CORPO}
    piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                          json=corpo)).json()
    r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                     json={**corpo, "impronta_piano": piano["impronta_piano"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert (await _tr(c, pid, sc["admin"], "BOZZA", "PROPOSTA")).status_code == 200
    aut = (await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                        data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                        files={"file": ("a.p7m", autorizzazione(pid),
                                        "application/pkcs7-mime")})).json()["id"]
    assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                      [aut])).status_code == 200
    r = await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
    assert r.status_code == 200, r.text
    return pid


async def _pda_in_eliminazione_senza_tombstone(db_url) -> list:
    """PdA in ELIMINAZIONE che non hanno un oggetto ELIMINATO con foglia in una
    pratica ESEGUITA: distrutti senza traccia probatoria."""
    return await sql(db_url, """
        SELECT a.id FROM public.aip a WHERE a.stato = 'ELIMINAZIONE' AND NOT EXISTS (
          SELECT 1 FROM public.pratiche_oggetti o JOIN public.pratiche p ON p.id = o.pratica_id
          WHERE o.aip_id = a.id AND o.esito = 'ELIMINATO' AND o.foglia_id IS NOT NULL
            AND p.stato = 'ESEGUITA')""")


# ── R1 ───────────────────────────────────────────────────────────────────

async def test_scarto_avviato_non_si_respinge_e_chiude_con_verbale_e_tombstone(
        db_url, s3_ciclo, monkeypatch) -> None:
    """R1: il produttore prova a respingere mentre il lavoratore ha già cancellato
    il primo PdA: 409, la pratica arriva a ESEGUITA, verbale e una foglia
    «scarto» per ogni PdA cancellato, nessun PdA in ELIMINAZIONE senza traccia."""
    from app.application import scarto
    from app.config import settings

    sc = await _ente_con_pda(db_url, s3_ciclo, 3)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    originale = scarto.elimina_pda
    respinta: dict[str, object] = {}

    async def elimina_e_respingi(pratica, oggetto, adesso):
        esito = await originale(pratica, oggetto, adesso)
        if not respinta:
            async with client_app() as c2:
                r = await _tr(c2, pratica["id"], sc["admin"], "ESEGUIBILE", "RESPINTA")
                respinta.update(status=r.status_code, corpo=r.json())
        return esito

    monkeypatch.setattr(scarto, "elimina_pda", elimina_e_respingi)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["rsc"])).json()

    assert respinta["status"] == 409
    assert respinta["corpo"]["codice"] == "LC-409-IN-ESECUZIONE"
    assert dettaglio["stato"] == "ESEGUITA"
    assert dettaglio["conteggi"] == {"ELIMINATO": 3}
    for aip, _ in sc["pda"]:
        assert versioni(sc["s3"], f"{sc['tenant']}/{aip}/") == []
    assert any(a["tipo"] == "VERBALE_SCARTO" for a in dettaglio["allegati"])
    urn_scartati = {k[1] for k in dip.registro.foglie if k[0] == "scarto"}
    assert urn_scartati == {urn for _, urn in sc["pda"]}
    assert await _pda_in_eliminazione_senza_tombstone(db_url) == []


async def test_esecuzione_chiesta_si_respinge_prima_che_il_lavoratore_la_prenda(
        db_url, s3_ciclo) -> None:
    """R1: l'esecuzione chiesta ma non ancora AVVIATA si può respingere; il
    lavoratore poi non cancella nulla."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        r = await _tr(c, pid, sc["admin"], "ESEGUIBILE", "RESPINTA")
        assert r.status_code == 200, r.text
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["rsc"])).json()
    assert dettaglio["stato"] == "RESPINTA"
    for aip, _ in sc["pda"]:
        assert len(versioni(sc["s3"], f"{sc['tenant']}/{aip}/")) == 3
    assert dip.registro.foglie == {}


async def test_azioni_consentite_dopo_l_avvio_senza_respingere(db_url, s3_ciclo,
                                                               monkeypatch) -> None:
    """R1: dopo l'avvio l'interfaccia non offre più «respingi» (azioni_consentite
    con il motivo di blocco)."""
    from app.application import pratiche as pr

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        prima = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["admin"])).json()
        from libs.shared.db.ambito import ambito_conservatore

        with ambito_conservatore("test: avvio dell'esecuzione"):
            assert await pr.segna_avvio_esecuzione(__import__("uuid").UUID(pid),
                                                   pr.adesso_utc()) is not None
        dopo = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["admin"])).json()
    respingi = {a["nuovo_stato"]: a for a in prima["azioni_consentite"]}["RESPINTA"]
    assert respingi["consentita"] is True
    respingi = {a["nuovo_stato"]: a for a in dopo["azioni_consentite"]}["RESPINTA"]
    assert respingi["consentita"] is False and "iniziata" in respingi["motivo_blocco"]


# ── R2 ───────────────────────────────────────────────────────────────────

async def test_pda_senza_oggetti_nel_worm_bloccato_mai_eliminato(db_url, s3_ciclo) -> None:
    """R2: nessuna versione sotto <ente>/<aip>/ → BLOCCATO (OGGETTI_ASSENTI): nessun
    tombstone e il PdA resta CONSERVATO."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1, oggetti=False)
    aip, urn = sc["pda"][0]
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await lavora(dip)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["rsc"])).json()
    assert dettaglio["conteggi"] == {"BLOCCATO": 1}
    riga = (await sql(db_url, "SELECT esito, motivo FROM public.pratiche_oggetti "
                              "WHERE pratica_id = $1::uuid", pid))[0]
    assert (riga["esito"], riga["motivo"]) == ("BLOCCATO", "OGGETTI_ASSENTI")
    assert not any(k[0] == "scarto" for k in dip.registro.foglie)
    stato = await sql(db_url, "SELECT stato::text AS s FROM public.aip WHERE id = $1", aip)
    assert stato[0]["s"] == "CONSERVATO"
    assert urn


async def test_oggetto_dichiarato_dal_database_assente_dal_worm_blocca(db_url,
                                                                      s3_ciclo) -> None:
    """R2: un file del PdA registrato in ``documenti`` ma assente dal WORM
    (OGGETTI_MANCANTI): nessuna cancellazione delle altre versioni."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    aip, _ = sc["pda"][0]
    await sql(db_url, "INSERT INTO public.documenti (id, aip_id, tenant_id, nome_file, tipo, "
                      "formato_mime, dimensione_bytes, hash_sha256, s3_key, ordine) VALUES "
                      "(gen_random_uuid(), $1, $2, 'mancante.pdf', 'ALLEGATO', 'application/pdf', "
                      "1, $3, $4, 1)", aip, sc["tenant"], "ab" * 32,
              f"{sc['tenant']}/{aip}/files/0001-mancante.pdf")
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await lavora(dip)
    riga = (await sql(db_url, "SELECT esito, motivo FROM public.pratiche_oggetti "
                              "WHERE pratica_id = $1::uuid", pid))[0]
    assert (riga["esito"], riga["motivo"]) == ("BLOCCATO", "OGGETTI_MANCANTI")
    assert len(versioni(sc["s3"], f"{sc['tenant']}/{aip}/")) == 3


# ── R9 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("natura", "pa", "atteso"), [
    (None, False, True),        # natura non dichiarata, senza codice IPA: PA
    ("PA", False, True),        # PA senza codice IPA
    ("PRIVATO", True, False),   # privato con un codice IPA: esente
])
async def test_nulla_osta_dalla_natura_dell_ente_non_dal_codice_ipa(db_url, s3_ciclo, natura,
                                                                   pa, atteso) -> None:
    """R9: il nulla osta si ricava dalla natura dichiarata (assente = PA), mai dal
    codice IPA facoltativo; per una PA AUTORIZZATA_PRODUTTORE → ESEGUIBILE è
    rifiutata (serve NULLA_OSTA)."""
    tenant = await crea_ente(db_url, pa=pa, natura=natura)
    admin_id = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    ente_c = await crea_ente(db_url)
    rsc = intestazioni(ente_c, "RSC_CONSERVATORE", await crea_utente(db_url, ente_c,
                                                                     "RSC_CONSERVATORE"))
    admin = intestazioni(tenant, "ADMIN_TENANT", admin_id)
    classe = await crea_classe(db_url, tenant)
    s3 = bucket_worm(s3_ciclo)
    pdv = await crea_pdv(db_url, tenant, classe, admin_id)
    aip, _ = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    oggetti_pda(s3, tenant, aip, fino_a=PASSATO)
    corpo = {"ente": str(tenant), **CORPO}
    async with client_app() as c:
        piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=admin,
                              json=corpo)).json()
        assert piano["richiede_nulla_osta"] is atteso
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=admin,
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
        pid = r.json()["id"]
        assert r.json()["richiede_nulla_osta"] is atteso
        await _tr(c, pid, admin, "BOZZA", "PROPOSTA")
        aut = (await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=admin,
                            data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                            files={"file": ("a.p7m", autorizzazione(pid),
                                            "application/pkcs7-mime")})).json()["id"]
        await _tr(c, pid, admin, "PROPOSTA", "AUTORIZZATA_PRODUTTORE", [aut])
        r = await _tr(c, pid, rsc, "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
    assert r.status_code == (409 if atteso else 200), r.text


# ── R10 ──────────────────────────────────────────────────────────────────

async def test_stato_del_pda_cambiato_dopo_l_elenco_del_worm_blocca(db_url, s3_ciclo,
                                                                   monkeypatch) -> None:
    """R10 + revisione 2 (S6): le versioni si elencano con il PdA BLOCCATO.
    Un'esibizione che parte mentre si elenca (UPDATE del PdA) aspetta il lock
    (qui: lock_timeout); un'esibizione partita PRIMA del lock la vede il
    ricontrollo e NESSUNA versione si cancella."""
    import asyncpg
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    (aip, _), (aip2, _) = sc["pda"]
    originale = storage.versioni_del_pda
    tentativi: list[str] = []

    def elenca_e_cambia(client, bucket, prefisso):
        risultato = originale(client, bucket, prefisso)
        if str(aip) in prefisso and not tentativi:
            import asyncio

            async def esibizione() -> None:
                conn = await asyncpg.connect(db_url.replace("postgresql+asyncpg://",
                                                            "postgresql://", 1))
                try:
                    await conn.execute("SET lock_timeout = '300ms'")
                    await conn.execute("UPDATE public.aip SET stato = 'ESIBIZIONE' "
                                       "WHERE id = $1", aip)
                    tentativi.append("aggiornato")
                except asyncpg.exceptions.LockNotAvailableError:
                    tentativi.append("in_attesa_del_lock")
                finally:
                    await conn.close()

            asyncio.run(esibizione())
        return risultato

    monkeypatch.setattr(storage, "versioni_del_pda", elenca_e_cambia)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        # esibizione del secondo PdA partita prima che il lavoratore lo blocchi
        await sql(db_url, "UPDATE public.aip SET stato = 'ESIBIZIONE' WHERE id = $1", aip2)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await lavora(dip)
    assert tentativi == ["in_attesa_del_lock"]
    righe = {r["aip_id"]: (r["esito"], r["motivo"]) for r in await sql(
        db_url, "SELECT aip_id, esito, motivo FROM public.pratiche_oggetti "
                "WHERE pratica_id = $1::uuid", pid)}
    assert righe[aip][0] == "ELIMINATO"
    assert righe[aip2] == ("BLOCCATO", "STATO_NON_CONSERVATO")
    assert len(versioni(sc["s3"], f"{sc['tenant']}/{aip2}/")) == 3


async def test_update_del_pda_senza_righe_non_cancella(db_url, s3_ciclo, monkeypatch) -> None:
    """R10: se il passaggio a ELIMINAZIONE non aggiorna esattamente una riga
    (rowcount), il PdA è BLOCCATO e nessuna versione si cancella."""
    from app.application import scarto

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    aip, _ = sc["pda"][0]

    async def ricontrollo_cieco(session, pratica, aip_id, adesso):
        return None, None  # come se il ricontrollo non vedesse il cambio di stato

    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        await sql(db_url, "UPDATE public.aip SET stato = 'ESIBIZIONE' WHERE id = $1", aip)
        monkeypatch.setattr(scarto, "_ricontrolla", ricontrollo_cieco)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await lavora(dipendenze(db_url))
    riga = (await sql(db_url, "SELECT esito, motivo FROM public.pratiche_oggetti "
                              "WHERE pratica_id = $1::uuid", pid))[0]
    assert (riga["esito"], riga["motivo"]) == ("BLOCCATO", "STATO_NON_CONSERVATO")
    assert len(versioni(sc["s3"], f"{sc['tenant']}/{aip}/")) == 3


# ── R11 ──────────────────────────────────────────────────────────────────

async def test_lease_perso_dentro_il_passo_ferma_le_cancellazioni(db_url, s3_ciclo,
                                                                 monkeypatch) -> None:
    """R11: un'altra replica prende la pratica durante il passo (lease scaduto):
    il passo si ferma prima del PdA successivo, nessuna cancellazione doppia."""
    from app.application import scarto
    from app.application.lavoratore import giro
    from app.config import settings

    sc = await _ente_con_pda(db_url, s3_ciclo, 3)
    monkeypatch.setattr(settings, "lavoratore_lotto", 3)
    originale = scarto.elimina_pda
    chiamate: list[str] = []

    async def elimina_e_perdi_il_lease(pratica, oggetto, adesso):
        esito = await originale(pratica, oggetto, adesso)
        chiamate.append(esito)
        if len(chiamate) == 1:
            await sql(db_url, "UPDATE public.pratiche SET lease_detentore = 'altra-replica' "
                              "WHERE id = $1::uuid", str(pratica["id"]))
        return esito

    monkeypatch.setattr(scarto, "elimina_pda", elimina_e_perdi_il_lease)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        esito = await giro(dipendenze(db_url), "test-lavoratore")
    assert esito == "RINVIATO"
    assert chiamate == ["ELIMINATO"]
    conteggi = await sql(db_url, "SELECT esito, count(*) AS n FROM public.pratiche_oggetti "
                                 "WHERE pratica_id = $1::uuid GROUP BY esito", pid)
    assert {r["esito"]: r["n"] for r in conteggi} == {"ELIMINATO": 1, "SELEZIONATO": 2}
