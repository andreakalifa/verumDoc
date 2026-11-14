# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 4 di R05 — rilievi U1, U2, U4 sullo SCARTO (PostgreSQL vero, S3 moto
con Object Lock, registro finto del contratto Q3).

  - U1: lo storage non leggibile alla chiusura di una cancellazione iniziata
    (ripresa con un blocco del database, ``ERRORE_PERSISTENTE``) chiude il PdA
    come distrutto in parte: oggetto NON_COMPLETATO con ``verifica_storage =
    non_determinabile``, PdA ELIMINAZIONE_INCOMPLETA, voce d'audit, allarme,
    pratica ESEGUITA_CON_ECCEZIONI. Prima: ``len(None)`` → TypeError, chiusura
    annullata, oggetto IN_CORSO e lavoratore in ciclo di errori per sempre
    (prova del revisore ``revisione4/ciclo/test_rev4_storage_illeggibile_alla_chiusura.py``);
  - U2: prima di cancellare, il lavoratore verifica da sé la catena delle
    autorizzazioni (``app.application.autorizzazioni``): una pratica fabbricata
    con il login dell'API (o a cui manca un passo, il nulla osta di un ente PA,
    un allegato nel WORM, la coerenza col piano approvato) resta bloccata con
    allarme, NULLA si cancella; il database rifiuta cronologie discontinue e
    modifiche del piano congelato (migrazione 044);
  - U4: una versione nata sotto il prefisso del PdA dopo l'inventario non si
    cancella prima di essere aggiunta all'inventario (in modo durevole): dopo un
    crash la chiusura non dichiara mai «nulla distrutto».
"""
from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, dipendenze, lavora, versioni
from tests.test_r05_revisione2_scarto_incompleto import (
    _lavora_finche,
    _oggetti,
    _stato,
    _stato_pda,
    _verbali,
)
from tests.test_r05_revisione_scarto import _ente_con_pda, _fino_a_eseguibile, _tr

pytestmark = [pytest.mark.integration, richiede_pg]


class Ucciso(BaseException):
    """SIGKILL/OOM simulato: nessun handler applicativo lo intercetta."""


async def _audit_incompleti(db_url) -> list:
    return await sql(db_url, "SELECT dettagli FROM public.audit_global WHERE evento = "
                             "'ciclo_vita.scarto.pda_eliminazione_incompleta'")


# ── U1 ───────────────────────────────────────────────────────────────────

async def test_storage_illeggibile_alla_ripresa_con_blocco_chiude_distrutto_in_parte(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Scenario del revisore: una versione cancellata, processo ucciso, la classe
    diventa permanente (blocco del DB, T6), alla ripresa l'elenco dello storage
    fallisce sempre. L'oggetto si CHIUDE (nessun ciclo di errori)."""
    from app.application import metriche
    from app.config import settings
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    originale = storage.elimina_versione

    def muori(s3, bucket, oggetto):
        originale(s3, bucket, oggetto)
        raise Ucciso("pod terminato")

    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        monkeypatch.setattr(storage, "elimina_versione", muori)
        with pytest.raises(Ucciso):
            await lavora(dip, giri=3)
    monkeypatch.setattr(storage, "elimina_versione", originale)
    assert len(versioni(sc["s3"], prefisso)) == len(iniziali) - 1
    await sql(db_url, "UPDATE public.pratiche SET lease_scade_il = now() - interval '1 s', "
                      "prossimo_tentativo = now() - interval '1 s' WHERE id = $1::uuid", str(pid))
    await sql(db_url, "UPDATE public.classi_documentali c SET conservazione_permanente = true "
                      "FROM public.pdv v JOIN public.aip a ON a.pdv_id = v.id "
                      "WHERE a.id = $1 AND c.id = v.classe_doc_id", aip)

    def elenco_rotto(*a, **kw):
        raise RuntimeError("storage non raggiungibile")

    monkeypatch.setattr(storage, "versioni_del_pda", elenco_rotto)
    esiti = await _lavora_finche(db_url, dip, pid, giri=4)

    stato = await _stato(db_url, pid)
    assert (stato["stato"], stato["lavoro"]) == ("ESEGUITA_CON_ECCEZIONI", None), esiti
    assert "ERRORE" not in esiti
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("NON_COMPLETATO", "CONSERVAZIONE_PERMANENTE")
    dettagli = json.loads(oggetto["dettagli"])
    assert dettagli["verifica_storage"] == "non_determinabile"
    assert dettagli["versioni_rimaste"] is None
    assert await _stato_pda(db_url, aip) == "ELIMINAZIONE_INCOMPLETA"
    [audit] = await _audit_incompleti(db_url)
    audit_d = json.loads(audit["dettagli"]) if isinstance(audit["dettagli"], str) \
        else audit["dettagli"]
    assert audit_d["verifica_storage"] == "non_determinabile"
    assert dip.registro.foglie == {}
    [(_, verbale)] = await _verbali(db_url, sc["s3"], pid)
    assert 'versioniRimaste="non determinabile" verificaStorage="non_determinabile"' in verbale
    metriche.registra(__import__("prometheus_client").CollectorRegistry())
    assert (await metriche.aggiorna())["incompleti"] == 1


async def test_errore_persistente_con_storage_illeggibile_chiude_distrutto_in_parte(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Ramo ERRORE_PERSISTENTE: una versione cancellata, poi la cancellazione e
    l'elenco falliscono sempre; dopo ``SCARTO_TENTATIVI_MAX_PDA`` l'oggetto si
    chiude NON_COMPLETATO anche se l'ultimo elenco non riesce."""
    from app.config import settings
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    monkeypatch.setattr(settings, "scarto_tentativi_max_pda", 2)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    elenco_vero, elimina_vera = storage.versioni_del_pda, storage.elimina_versione
    chiamate = {"elenco": 0, "elimina": 0}

    def elenco(*a, **kw):
        chiamate["elenco"] += 1
        if chiamate["elenco"] > 2:      # elenco iniziale e primo giro: poi lo storage cade
            raise RuntimeError("storage non raggiungibile")
        return elenco_vero(*a, **kw)

    def elimina(s3, bucket, oggetto):
        chiamate["elimina"] += 1
        if chiamate["elimina"] > 1:
            raise RuntimeError("storage non raggiungibile")
        elimina_vera(s3, bucket, oggetto)

    monkeypatch.setattr(storage, "versioni_del_pda", elenco)
    monkeypatch.setattr(storage, "elimina_versione", elimina)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid, giri=6)

    stato = await _stato(db_url, pid)
    assert (stato["stato"], stato["lavoro"]) == ("ESEGUITA_CON_ECCEZIONI", None)
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("NON_COMPLETATO", "ERRORE_PERSISTENTE")
    dettagli = json.loads(oggetto["dettagli"])
    assert dettagli["verifica_storage"] == "non_determinabile"
    assert dettagli["versioni_rimaste"] is None and dettagli["versioni_eliminate"] == 1
    assert await _stato_pda(db_url, aip) == "ELIMINAZIONE_INCOMPLETA"
    assert len(await _audit_incompleti(db_url)) == 1
    assert len(versioni(sc["s3"], prefisso)) == len(iniziali) - 1
    assert dip.registro.foglie == {}


# ── U4 ───────────────────────────────────────────────────────────────────

async def test_versione_nata_dopo_l_inventario_cancellata_poi_crash_mai_nulla_distrutto(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Una versione scritta sotto il prefisso DOPO l'inventario (fra l'avvio
    della cancellazione e il primo elenco del giro), cancellata, poi il processo
    muore prima di contare; alla ripresa un legal hold blocca. Prima: inventario
    intatto e contatore a 0 → «nulla distrutto», PdA CONSERVATO con una versione
    in meno. Ora la versione entra nell'inventario prima di essere cancellata:
    NON_COMPLETATO, PdA ELIMINAZIONE_INCOMPLETA."""
    from app.config import settings
    from app.infrastructure import storage

    from tests.supporto import PASSATO

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    nuova = f"{prefisso}AAAA-scritta-dopo-l-inventario.xml"
    elenco_vero, elimina_vera = storage.versioni_del_pda, storage.elimina_versione
    chiamate = {"elenco": 0}

    def elenco(s3, bucket, pref):
        chiamate["elenco"] += 1
        if chiamate["elenco"] == 2:     # dopo l'inventario, prima del primo giro
            sc["s3"].put_object(Bucket="verumdoc-aip", Key=nuova, Body=b"<tardiva/>",
                                ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=PASSATO)
        return elenco_vero(s3, bucket, pref)

    def muori_dopo_la_nuova(s3, bucket, oggetto):
        elimina_vera(s3, bucket, oggetto)
        if oggetto.chiave == nuova:
            raise Ucciso("pod terminato")

    monkeypatch.setattr(storage, "versioni_del_pda", elenco)
    monkeypatch.setattr(storage, "elimina_versione", muori_dopo_la_nuova)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        with pytest.raises(Ucciso):
            await lavora(dip, giri=3)
    monkeypatch.setattr(storage, "versioni_del_pda", elenco_vero)
    monkeypatch.setattr(storage, "elimina_versione", elimina_vera)
    rimaste = versioni(sc["s3"], prefisso)
    assert nuova not in rimaste and len(rimaste) == 3
    dettagli = json.loads((await _oggetti(db_url, pid))[aip]["dettagli"])
    assert dettagli["versioni_eliminate"] == 0                  # contatore perso
    assert any(k == nuova for k, _ in dettagli["inventario"])   # ma inventariata prima
    assert dettagli["versioni_dopo_inventario"] == 1
    await sql(db_url, "UPDATE public.pratiche SET lease_scade_il = now() - interval '1 s', "
                      "prossimo_tentativo = now() - interval '1 s' WHERE id = $1::uuid", str(pid))
    sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip", Key=rimaste[0],
                                   LegalHold={"Status": "ON"})
    await _lavora_finche(db_url, dip, pid)

    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("NON_COMPLETATO", "LEGAL_HOLD")
    dettagli = json.loads(oggetto["dettagli"])
    assert dettagli["versioni_eliminate"] == 1 and dettagli["verifica_storage"] == "inventario"
    assert await _stato_pda(db_url, aip) == "ELIMINAZIONE_INCOMPLETA"
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA_CON_ECCEZIONI"


# ── U2 ───────────────────────────────────────────────────────────────────

async def _come_api(url: str, istruzione: str, *argomenti) -> None:
    """SQL con il login dell'API (membro del solo verumdoc_applicazione), come un
    pod API compromesso."""
    from libs.shared.db.ruolo_prova import url_ruolo_servizio

    dsn = (await url_ruolo_servizio(url)).replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.ambito', 'conservatore', true)")
            await conn.execute(istruzione, *argomenti)
    finally:
        await conn.close()


async def _bloccata(db_url, pid) -> dict:
    riga = (await sql(db_url, "SELECT stato, lavoro, ultimo_errore FROM public.pratiche "
                              "WHERE id = $1::uuid", str(pid)))[0]
    errore = riga["ultimo_errore"]
    return {"stato": riga["stato"], "lavoro": riga["lavoro"],
            "errore": json.loads(errore) if isinstance(errore, str) else errore}


async def _eliminati(db_url) -> list:
    return await sql(db_url, "SELECT aip_id FROM public.esiti_esecuzione "
                             "WHERE esito = 'ELIMINATO'")


def _difetti(bloccata: dict) -> str:
    assert bloccata["errore"] is not None, f"pratica NON bloccata: {bloccata}"
    return " | ".join(bloccata["errore"]["difetti"])


async def test_pratica_fabbricata_dal_login_api_non_cancella_nulla(db_url, s3_ciclo) -> None:
    """Scenario del revisore (U2): il login dell'API inserisce una pratica di
    SCARTO già ESEGUIBILE con il lavoro, per un ente PA, senza iter né allegati,
    e un oggetto SELEZIONATO. Prima: il lavoratore cancellava il PdA, scriveva
    l'esito e chiedeva il tombstone. Ora: pratica bloccata, nulla cancellato."""
    from app.application import metriche

    sc = await _ente_con_pda(db_url, s3_ciclo, 1, natura="PA")
    (aip, urn), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    pid = str(uuid.uuid4())
    await _come_api(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, "
                            "richiede_nulla_osta, lavoro, prossimo_tentativo) VALUES ($1::uuid, "
                            "$2, 'SCARTO', 'ESEGUIBILE', 'pratica fabbricata dal pod API', true, "
                            "'ESEGUI', now() - interval '1 s')", pid, sc["tenant"])
    await _come_api(db_url, "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, "
                            "urn, esito) VALUES ($1::uuid, $2, $3, $4, 'SELEZIONATO')",
                    pid, sc["tenant"], aip, urn)
    dip = dipendenze(db_url)
    esiti = await _lavora_finche(db_url, dip, pid)

    assert esiti[0] == "BLOCCATO"
    assert versioni(sc["s3"], prefisso) == iniziali             # nulla cancellato
    assert await _stato_pda(db_url, aip) == "CONSERVATO"
    bloccata = await _bloccata(db_url, pid)
    assert (bloccata["stato"], bloccata["lavoro"]) == ("ESEGUIBILE", None)
    assert bloccata["errore"]["codice"] == "LC-409-AUTORIZZAZIONI"
    assert "CRONOLOGIA_ASSENTE" in _difetti(bloccata)
    assert "PIANO_ASSENTE" in _difetti(bloccata)
    assert await _eliminati(db_url) == []
    assert dip.registro.foglie == {}
    assert (await _oggetti(db_url, pid))[aip]["esito"] == "SELEZIONATO"
    assert len(await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                                 "'ciclo_vita.scarto.autorizzazioni_non_verificate'")) == 1
    assert await sql(db_url, "SELECT 1 FROM public.pratiche_esecuzioni WHERE esito = "
                             "'ERRORE' AND pratica_id = $1::uuid", pid)
    metriche.registra(__import__("prometheus_client").CollectorRegistry())
    assert (await metriche.aggiorna())["autorizzazioni"] == 1


async def test_ente_diventato_pa_senza_nulla_osta_non_si_esegue(db_url, s3_ciclo) -> None:
    """Iter completo per un ente PRIVATO (AUTORIZZATA_PRODUTTORE → ESEGUIBILE),
    poi l'ente è dichiarato pubblica amministrazione: senza il passo NULLA_OSTA
    il lavoratore non cancella (natura ATTUALE, tenants.natura)."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        await sql(db_url, "UPDATE public.tenants SET natura = 'PA' WHERE id = $1", sc["tenant"])
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    bloccata = await _bloccata(db_url, pid)
    assert (bloccata["stato"], bloccata["lavoro"]) == ("ESEGUIBILE", None)
    assert "NULLA_OSTA_SALTATO" in _difetti(bloccata)
    assert "NULLA_OSTA_ASSENTE" in _difetti(bloccata)
    assert versioni(sc["s3"], prefisso) == iniziali
    assert await _eliminati(db_url) == []


async def test_allegato_diverso_nel_worm_non_si_esegue(db_url, s3_ciclo, monkeypatch) -> None:
    """L'autorizzazione del produttore riletta dal WORM non ha l'impronta
    registrata (o non c'è): pratica bloccata, nulla cancellato."""
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    vera = storage.leggi_versione

    def contenuto_diverso(s3, bucket, chiave, version_id, f):
        letta = vera(s3, bucket, chiave, version_id, f)
        f.seek(0)
        f.truncate()
        f.write(b"documento sostituito")
        f.seek(0)
        return letta and storage.VersioneLetta("0" * 64, letta.dimensione,
                                               letta.conserva_fino_a, letta.legal_hold)

    monkeypatch.setattr(storage, "leggi_versione", contenuto_diverso)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    bloccata = await _bloccata(db_url, pid)
    assert bloccata["lavoro"] is None
    assert "ALLEGATO_IMPRONTA_DIVERSA" in _difetti(bloccata)
    assert versioni(sc["s3"], prefisso) == iniziali

    monkeypatch.setattr(storage, "leggi_versione", lambda *a, **kw: None)
    await sql(db_url, "UPDATE public.pratiche SET lavoro = 'ESEGUI', ultimo_errore = NULL, "
                      "prossimo_tentativo = now() - interval '1 s' WHERE id = $1::uuid", str(pid))
    await _lavora_finche(db_url, dip, pid)
    assert "ALLEGATO_ASSENTE_DAL_WORM" in _difetti(await _bloccata(db_url, pid))
    assert versioni(sc["s3"], prefisso) == iniziali


async def test_piano_cambiato_dopo_l_approvazione_non_si_esegue(db_url, s3_ciclo) -> None:
    """Dopo l'approvazione il login dell'API aggiunge al piano un PdA (anche di
    un altro ente) come SELEZIONATO: l'impronta ricalcolata non è quella
    approvata (immutabile) → bloccata, nessuna cancellazione."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    altro = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip_altro, urn_altro), = altro["pda"]
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        await _come_api(db_url, "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, "
                                "aip_id, urn, esito) VALUES ($1::uuid, $2, $3, $4, "
                                "'SELEZIONATO')", pid, sc["tenant"], aip_altro, urn_altro)
        # l'impronta approvata non si riscrive (migrazione 044)
        with pytest.raises(asyncpg.PostgresError, match="impronta del piano"):
            await _come_api(db_url, "UPDATE public.pratiche SET impronta_piano = $2 "
                                    "WHERE id = $1::uuid", pid, "0" * 64)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    bloccata = await _bloccata(db_url, pid)
    assert bloccata["lavoro"] is None
    assert "IMPRONTA_PIANO_DIVERSA" in _difetti(bloccata)
    assert "OGGETTI_ALTRO_ENTE" not in _difetti(bloccata)      # tenant della riga = ente
    assert "OGGETTI_FUORI_CRITERI" in _difetti(bloccata)        # il PdA è di un altro ente
    for s in (sc, altro):
        (a, _), = s["pda"]
        assert versioni(s["s3"], f"{s['tenant']}/{a}/")
    assert await _eliminati(db_url) == []


async def test_oggetto_bloccato_nel_piano_riportato_selezionato_non_si_cancella(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Dopo la verifica (riga AUTORIZZAZIONI_VERIFICATE), il login dell'API
    riporta a SELEZIONATO un PdA che il piano approvato bloccava: il lavoratore
    lo riconosce dall'esito del PIANO (congelato) e non lo cancella."""
    from app.application import autorizzazioni
    from app.config import settings

    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    (aip_sel, _), (aip_bl, _) = sorted(sc["pda"])
    # il secondo PdA è bloccato nel piano (scadenza futura), poi scade davvero
    await sql(db_url, "UPDATE public.aip SET scadenza_conservazione = now() + interval '1 day' "
                      "WHERE id = $1", aip_bl)
    vera = autorizzazioni.assicura
    fatto = {"n": 0}

    async def poi_ribalta(pratica, adesso):
        await vera(pratica, adesso)
        if not fatto["n"]:
            fatto["n"] = 1
            await _come_api(db_url, "UPDATE public.pratiche_oggetti SET esito = 'SELEZIONATO', "
                                    "motivo = NULL WHERE aip_id = $1", aip_bl)
            await sql(db_url, "UPDATE public.aip SET scadenza_conservazione = now() - "
                              "interval '1 day' WHERE id = $1", aip_bl)
            # il piano congelato non si riscrive
            with pytest.raises(asyncpg.PostgresError, match="piano congelato"):
                await _come_api(db_url, "UPDATE public.pratiche_oggetti SET esito_piano = "
                                        "'SELEZIONATO' WHERE aip_id = $1", aip_bl)

    monkeypatch.setattr(autorizzazioni, "assicura", poi_ribalta)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        oggetti = await _oggetti(db_url, pid)
        assert oggetti[aip_bl]["esito"] == "BLOCCATO"
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    oggetti = await _oggetti(db_url, pid)
    assert (oggetti[aip_bl]["esito"], oggetti[aip_bl]["motivo"]) == ("BLOCCATO", "FUORI_PIANO")
    assert versioni(sc["s3"], f"{sc['tenant']}/{aip_bl}/")       # mai cancellato
    assert oggetti[aip_sel]["esito"] == "ELIMINATO"
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"
    verificate = await sql(db_url, "SELECT scritto_da FROM public.esiti_esecuzione WHERE "
                                   "esito = 'AUTORIZZAZIONI_VERIFICATE'")
    assert [r["scritto_da"] for r in verificate] == ["vd_prova_ciclo_vita_lavoratore"]


async def test_cronologia_e_verifiche_protette_nel_database(db_url, s3_ciclo) -> None:
    """Migrazione 044: il login dell'API non salta passi della cronologia, non
    toglie il nulla osta richiesto, non scrive la verifica del lavoratore."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1, natura="PA")
    async with client_app() as c:
        corpo = {"ente": str(sc["tenant"]), "criteri": {"classi": ["contratti"], "anni": [2015]},
                 "motivo": "Scarto dei contratti 2015 secondo il piano di conservazione"}
        piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                              json=corpo)).json()
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
        pid = r.json()["id"]
    with pytest.raises(asyncpg.PostgresError, match="cronologia discontinua"):
        await _come_api(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, "
                                "da_stato, a_stato, ruolo) VALUES ($1::uuid, $2, "
                                "'NULLA_OSTA', 'ESEGUIBILE', 'RSC_CONSERVATORE')",
                        pid, sc["tenant"])
    with pytest.raises(asyncpg.PostgresError, match="non sono della pratica"):
        await _come_api(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, "
                                "da_stato, a_stato, ruolo, allegati) VALUES ($1::uuid, $2, "
                                "'BOZZA', 'PROPOSTA', 'ADMIN_TENANT', $3::jsonb)",
                        pid, sc["tenant"], json.dumps([str(uuid.uuid4())]))
    with pytest.raises(asyncpg.PostgresError, match="nulla osta richiesto"):
        await _come_api(db_url, "UPDATE public.pratiche SET richiede_nulla_osta = false "
                                "WHERE id = $1::uuid", pid)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _come_api(db_url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, "
                                "tenant_id, esito, dettagli) VALUES ('SCARTO', $1::uuid, $2, "
                                "'AUTORIZZAZIONI_VERIFICATE', '{\"cronologia_fino_a\": 1}')",
                        pid, sc["tenant"])
