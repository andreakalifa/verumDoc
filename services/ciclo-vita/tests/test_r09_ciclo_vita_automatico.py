# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R09 — ciclo di vita automatico (decisioni D04, D06, D20, D21, D23).

PostgreSQL vero (database migrato alla 048), S3 moto con Object Lock, registro
delle impronte finto del contratto Q3:
  - D23: annullamento dopo la conservazione con doppia approvazione (quattro
    occhi veri: la stessa persona non approva; il lavoratore riverifica anche
    una cronologia fabbricata dall'API); respinto → nuova richiesta possibile;
  - D20: «chiudi con eccezioni» solo del conservatore e con motivo, fatto e
    foglia per PdA, riverificato dal lavoratore; ripresa automatica quando il
    legal hold viene tolto;
  - D21: proposta di scarto automatica in bozza con fatto, avviso, una sola
    aperta; la creazione «SISTEMA» senza fatto non regge la verifica;
  - D06: dopo lo scarto, trascorso il periodo, restano i soli dati dell'elenco
    minimo (fatto e foglia); nulla si tocca prima o sui PdA non eliminati;
  - D04: rinnovo del blocco WORM dei PdA permanenti e degli allegati, mai in
    riduzione, con fatto e foglia.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

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
    outbox,
    versioni,
)
from tests.test_r05_revisione2_scarto_incompleto import (
    _lavora_finche,
    _legal_hold_durante_la_cancellazione,
    _oggetti,
    _stato,
    _stato_pda,
)
from tests.test_r05_revisione_scarto import _ente_con_pda, _fino_a_eseguibile, _tr

pytestmark = [pytest.mark.integration, richiede_pg]

URL = "/api/v1/ciclo-vita"
MOTIVO_CHIUSURA = "Blocco legale definitivo disposto dall'autorità giudiziaria"


async def _parametro(db_url, codice: str, valore, *, ente=None) -> None:
    livello = "ENTE" if ente else "CONSERVATORE"
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                      "valore, motivo) VALUES ($1, $2, $3, CAST($4 AS jsonb), 'prova R09')",
              livello, ente, codice, json.dumps(valore))


async def _giro_periodico(dip, *, adesso: datetime | None = None) -> dict:
    """Un giro dei passi periodici come se fossero tutti dovuti."""
    from app.application import periodico
    from app.infrastructure.ambito import ambito_lavoratore

    with ambito_lavoratore("prova R09"):
        return await periodico.giro(dip.registro, "test-periodico",
                                    adesso or datetime.now(UTC))


async def _azzera_passi(db_url) -> None:
    await sql(db_url, "UPDATE public.ciclo_vita_periodico SET ultimo_giro = NULL, "
                      "lease_scade_il = NULL, lease_detentore = NULL")


# ── D23: annullamento con doppia approvazione ────────────────────────────

async def _conservato(db_url, s3_ciclo, *, approvazione: str | None = None):
    tenant = await crea_ente(db_url, doppia_approvazione=approvazione)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    secondo = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    operatore = await crea_utente(db_url, tenant, "OPERATORE")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    bucket_worm(s3_ciclo)
    return {"tenant": tenant, "admin": admin, "secondo": secondo, "operatore": operatore,
            "pdv": pdv, "aip": aip, "urn": urn}


async def _stato_pdv(db_url, pdv) -> str:
    return (await sql(db_url, "SELECT stato::text AS s FROM public.pdv WHERE id = $1",
                      pdv))[0]["s"]


async def test_d23_doppia_approvazione_dopo_la_conservazione(db_url, s3_ciclo) -> None:
    a = await _conservato(db_url, s3_ciclo)   # predefinito: DOPO_CONSERVAZIONE
    chi = intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"])
    secondo = intestazioni(a["tenant"], "ADMIN_TENANT", a["secondo"])
    corpo_ok = {"esito": "APPROVA", "motivo": "Verificato: versato per errore"}
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento", headers=chi,
                         json={"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"})
        assert r.status_code == 201, r.text
        pratica = r.json()
        assert pratica["stato"] == "RICHIESTA" and pratica["esecuzione"]["lavoro"] is None
        assert await _stato_pdv(db_url, a["pdv"]) == "CONSERVATO"   # nulla ancora
        # una seconda richiesta: già in attesa
        r = await c.post(f"{URL}/pratiche/annullamento", headers=chi,
                         json={"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-ANNULLATO"
        dettaglio = (await c.get(f"{URL}/pratiche/{pratica['id']}", headers=chi)).json()
        assert dettaglio["approvazione"]["consentita"] is False
        assert "approvarlo" in dettaglio["in_parole"]
        # quattro occhi: chi l'ha chiesto non approva; un OPERATORE non è ammesso
        r = await c.post(f"{URL}/pratiche/{pratica['id']}/approvazione", headers=chi,
                         json=corpo_ok)
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-QUATTRO-OCCHI"
        r = await c.post(f"{URL}/pratiche/{pratica['id']}/approvazione",
                         headers=intestazioni(a["tenant"], "OPERATORE", a["operatore"]),
                         json=corpo_ok)
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-QUATTRO-OCCHI"
        dettaglio2 = (await c.get(f"{URL}/pratiche/{pratica['id']}", headers=secondo)).json()
        assert dettaglio2["approvazione"]["consentita"] is True
        r = await c.post(f"{URL}/pratiche/{pratica['id']}/approvazione", headers=secondo,
                         json=corpo_ok)
        assert r.status_code == 200, r.text
        # revisione R09 (Z3): approvata, l'annullamento lo applica il lavoratore
        assert r.json()["stato"] == "APPROVATA"
        assert r.json()["esecuzione"]["lavoro"] == "ANNULLA"
        r = await c.post(f"{URL}/pratiche/{pratica['id']}/approvazione", headers=secondo,
                         json=corpo_ok)
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-APPROVAZIONE"
    assert await _stato_pdv(db_url, a["pdv"]) == "CONSERVATO"
    dip = dipendenze(db_url)
    await lavora(dip)
    assert await _stato_pdv(db_url, a["pdv"]) == "ANNULLATO"
    assert ("annullamento", a["urn"], pratica["id"]) in dip.registro.foglie
    fatti = await sql(db_url, "SELECT esito FROM public.esiti_esecuzione WHERE pratica_id = "
                              "$1::uuid ORDER BY scritto_il, id", pratica["id"])
    assert [f["esito"] for f in fatti] == ["AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO"]
    passi = await sql(db_url, "SELECT a_stato, utente_id FROM public.pratiche_stati "
                              "WHERE pratica_id = $1::uuid ORDER BY id", pratica["id"])
    assert [(p["a_stato"], p["utente_id"]) for p in passi] == [
        ("RICHIESTA", a["admin"]), ("APPROVATA", a["secondo"]), ("ESEGUITA", None)]
    avvisi = await outbox(db_url, "ciclo_vita.avviso")
    assert [e["sottotipo"] for e in avvisi] == ["ANNULLAMENTO_DA_APPROVARE"]


async def test_d23_respinto_si_puo_chiedere_di_nuovo_e_prima_della_conservazione_subito(
        db_url, s3_ciclo) -> None:
    a = await _conservato(db_url, s3_ciclo)
    chi = intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"])
    corpo = {"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"}
    async with client_app() as c:
        prima = (await c.post(f"{URL}/pratiche/annullamento", headers=chi, json=corpo)).json()
        r = await c.post(f"{URL}/pratiche/{prima['id']}/approvazione",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["secondo"]),
                         json={"esito": "RESPINGI", "motivo": "Il documento è corretto"})
        assert r.status_code == 200 and r.json()["stato"] == "RESPINTA"
        assert await _stato_pdv(db_url, a["pdv"]) == "CONSERVATO"
        r = await c.post(f"{URL}/pratiche/annullamento", headers=chi, json=corpo)
        assert r.status_code == 201 and r.json()["id"] != prima["id"]
        # prima della conservazione (DOPO_CONSERVAZIONE): annullamento subito
        classe = await crea_classe(db_url, a["tenant"], codice="altra")
        ricevuto = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
        r = await c.post(f"{URL}/pratiche/annullamento", headers=chi,
                         json={"pdv_id": str(ricevuto), "motivo": "Versato due volte"})
        assert r.status_code == 201 and r.json()["stato"] == "RICHIESTA"
        assert r.json()["esecuzione"]["lavoro"] == "ANNULLA"
    await lavora(dipendenze(db_url))
    assert await _stato_pdv(db_url, ricevuto) == "ANNULLATO"


async def test_d23_sempre_per_ente_e_ruoli_dal_parametro(db_url, s3_ciclo) -> None:
    """L'ente rende la doppia approvazione SEMPRE (verso sicuro); il conservatore
    toglie l'annullamento dopo la conservazione per l'ente (elenco vuoto)."""
    a = await _conservato(db_url, s3_ciclo, approvazione="SEMPRE")
    await _parametro(db_url, "ANNULLAMENTO_RUOLI_DOPO_CONSERVAZIONE", [], ente=a["tenant"])
    chi = intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"])
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento", headers=chi,
                         json={"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-ANNULLAMENTO"
        classe = await crea_classe(db_url, a["tenant"], codice="altra")
        ricevuto = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
        r = await c.post(f"{URL}/pratiche/annullamento", headers=chi,
                         json={"pdv_id": str(ricevuto), "motivo": "Versato due volte"})
        assert r.status_code == 201 and r.json()["stato"] == "RICHIESTA"


@pytest.mark.parametrize("approvatore", ["stesso", "nessuno"])
async def test_d23_lavoratore_rifiuta_approvazione_fabbricata(db_url, s3_ciclo,
                                                              approvatore) -> None:
    """API compromessa: cronologia RICHIESTA → APPROVATA scritta col login dell'API
    dalla STESSA persona (o senza approvazione), PdV portato ad ANNULLATO. Il
    lavoratore riverifica i quattro occhi: nessun atto, fatto né foglia."""
    a = await _conservato(db_url, s3_ciclo)
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"})
        pid = r.json()["id"]
    if approvatore == "stesso":
        await sql(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, "
                          "a_stato, utente_id, ruolo, metodo, motivo) VALUES ($1::uuid, $2, "
                          "'RICHIESTA', 'APPROVATA', $3, 'ADMIN_TENANT', 'jwt', 'approvo io')",
                  pid, a["tenant"], a["admin"])
        da = "APPROVATA"
    else:
        da = "RICHIESTA"
    await sql(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, "
                      "a_stato, ruolo, metodo, motivo) VALUES ($1::uuid, $2, $3, 'ESEGUITA', "
                      "'SISTEMA', 'lavoratore', 'fabbricato')", pid, a["tenant"], da)
    await sql(db_url, "UPDATE public.pratiche SET stato = 'ESEGUITA', lavoro = 'REGISTRO', "
                      "prossimo_tentativo = now() WHERE id = $1::uuid", pid)
    await sql(db_url, "UPDATE public.pdv SET stato = 'ANNULLATO', metadata_json = metadata_json "
                      "|| jsonb_build_object('annullamento', jsonb_build_object('pratica_id', "
                      "CAST($2 AS text))) WHERE id = $1", a["pdv"], pid)
    dip = dipendenze(db_url)
    await lavora(dip)
    assert dip.registro.foglie == {}
    assert await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione WHERE pratica_id = $1::uuid",
                     pid) == []
    errore = json.loads((await sql(db_url, "SELECT ultimo_errore FROM public.pratiche WHERE "
                                           "id = $1::uuid", pid))[0]["ultimo_errore"])
    # revisione R09 (Z3): senza il fatto del lavoratore che l'ha applicato
    # l'annullamento «ESEGUITA» scritto dall'API non regge
    atteso = "QUATTRO_OCCHI" if approvatore == "stesso" \
        else "ANNULLAMENTO_NON_APPLICATO_DAL_LAVORATORE"
    assert any(d.startswith(atteso) for d in errore["difetti"]), errore


# ── D20: chiudi con eccezioni e ripresa automatica ────────────────────────

async def _con_eccezioni(db_url, s3_ciclo, monkeypatch, dip) -> dict:
    from app.config import settings

    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    (aip1, urn1), _ = sc["pda"]
    chiave_hold = f"{sc['tenant']}/{aip1}/manifest.json"
    _legal_hold_durante_la_cancellazione(monkeypatch, sc["s3"], chiave_hold)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA_CON_ECCEZIONI"
    return {**sc, "pid": pid, "aip1": aip1, "urn1": urn1, "chiave_hold": chiave_hold}


async def _chiudi(c, sc, chi, motivo=MOTIVO_CHIUSURA):
    return await c.post(f"{URL}/pratiche/{sc['pid']}/transizione", headers=chi,
                        json={"stato_atteso": "ESEGUITA_CON_ECCEZIONI",
                              "nuovo_stato": "CHIUSA_CON_ECCEZIONI", "motivo": motivo})


async def test_d20_chiudi_con_eccezioni_solo_conservatore_con_motivo(db_url, s3_ciclo,
                                                                    monkeypatch) -> None:
    dip = dipendenze(db_url)
    sc = await _con_eccezioni(db_url, s3_ciclo, monkeypatch, dip)
    async with client_app() as c:
        r = await _chiudi(c, sc, sc["admin"])
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-RUOLO"
        r = await _chiudi(c, sc, sc["rsc"], motivo="Motivo breve")
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-MOTIVO"
        dettaglio = (await c.get(f"{URL}/pratiche/{sc['pid']}", headers=sc["rsc"])).json()
        azione = next(x for x in dettaglio["azioni_consentite"]
                      if x["nuovo_stato"] == "CHIUSA_CON_ECCEZIONI")
        assert azione["consentita"] and azione["motivo_minimo"] == 20
        assert "chiudere con eccezioni" in dettaglio["in_parole"]
        r = await _chiudi(c, sc, sc["rsc"])
        assert r.status_code == 202 and r.json()["esecuzione"]["lavoro"] == "CHIUDI"
        await lavora(dip)
        finale = (await c.get(f"{URL}/pratiche/{sc['pid']}", headers=sc["rsc"])).json()
    assert finale["stato"] == "CHIUSA_CON_ECCEZIONI" and finale["esecuzione"]["lavoro"] is None
    assert MOTIVO_CHIUSURA in finale["in_parole"]
    assert await _stato_pda(db_url, sc["aip1"]) == "ELIMINAZIONE_INCOMPLETA"
    assert versioni(sc["s3"], f"{sc['tenant']}/{sc['aip1']}/") == [sc["chiave_hold"]]
    fatti = await sql(db_url, "SELECT aip_id, dettagli FROM public.esiti_esecuzione WHERE "
                              "pratica_id = $1::uuid AND esito = 'CHIUSO_CON_ECCEZIONI'",
                      sc["pid"])
    assert [f["aip_id"] for f in fatti] == [sc["aip1"]]
    impronta = json.loads(fatti[0]["dettagli"])["impronta"]
    foglia = dip.registro.foglie[("chiusura_eccezioni", sc["urn1"], sc["pid"])]
    assert foglia["impronta"] == impronta
    assert await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                             "'ciclo_vita.scarto.chiusa_con_eccezioni' AND dettagli ->> "
                             "'pratica_id' = $1", sc["pid"])
    # chiusa: niente più riprese (né manuali né automatiche)
    async with client_app() as c:
        r = await _tr(c, sc["pid"], sc["rsc"], "CHIUSA_CON_ECCEZIONI", "ESEGUITA")
        assert r.status_code == 409


async def test_d20_chiusura_fabbricata_dall_api_bloccata(db_url, s3_ciclo, monkeypatch) -> None:
    """API compromessa: lavoro CHIUDI scritto direttamente con un richiedente che non
    è del conservatore (o senza la voce d'audit): il lavoratore non scrive fatti
    né foglie e blocca la pratica."""
    dip = dipendenze(db_url)
    sc = await _con_eccezioni(db_url, s3_ciclo, monkeypatch, dip)
    admin = (await sql(db_url, "SELECT id FROM public.users WHERE tenant_id = $1 AND ruolo = "
                               "'ADMIN_TENANT'", sc["tenant"]))[0]["id"]
    chiusura = {"richiesta_da": str(admin), "ruolo": "RSC_CONSERVATORE",
                "richiesta_il": datetime.now(UTC).isoformat(), "motivo": MOTIVO_CHIUSURA}
    await sql(db_url, "UPDATE public.pratiche SET lavoro = 'CHIUDI', prossimo_tentativo = now(), "
                      "dati = dati || jsonb_build_object('chiusura', CAST($2 AS jsonb)) "
                      "WHERE id = $1::uuid", sc["pid"], json.dumps(chiusura))
    await lavora(dip)
    stato = await _stato(db_url, sc["pid"])
    assert (stato["stato"], stato["lavoro"]) == ("ESEGUITA_CON_ECCEZIONI", None)
    assert not [k for k in dip.registro.foglie if k[0] == "chiusura_eccezioni"]
    assert await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione WHERE esito = "
                             "'CHIUSO_CON_ECCEZIONI'") == []
    errore = json.loads((await sql(db_url, "SELECT ultimo_errore FROM public.pratiche WHERE "
                                           "id = $1::uuid", sc["pid"]))[0]["ultimo_errore"])
    assert any(d.startswith("RUOLO_NON_AMMESSO") for d in errore["difetti"]), errore
    assert any(d.startswith("RICHIESTA_SENZA_AUDIT") for d in errore["difetti"]), errore


async def test_d20_ripresa_automatica_quando_il_legal_hold_cessa(db_url, s3_ciclo,
                                                                 monkeypatch) -> None:
    dip = dipendenze(db_url)
    sc = await _con_eccezioni(db_url, s3_ciclo, monkeypatch, dip)
    # ancora sotto legal hold: nessuna ripresa
    esito = await _giro_periodico(dip)
    assert esito["riprese_scarto"]["riprese"] == []
    assert (await _stato(db_url, sc["pid"]))["lavoro"] is None
    sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip", Key=sc["chiave_hold"],
                                   LegalHold={"Status": "OFF"})
    await _azzera_passi(db_url)
    esito = await _giro_periodico(dip)
    assert esito["riprese_scarto"]["riprese"] == [sc["pid"]]
    pratica = await sql(db_url, "SELECT lavoro, dati FROM public.pratiche WHERE id = $1::uuid",
                        sc["pid"])
    dati = json.loads(pratica[0]["dati"])
    assert pratica[0]["lavoro"] == "ESEGUI" and dati["riprese_automatiche"] == 1
    assert dati["esecuzione"]["ruolo"] == "SISTEMA" and dati["esecuzione"]["giro"] == 2
    assert [e["sottotipo"] for e in await outbox(db_url, "ciclo_vita.avviso")] == [
        "RIPRESA_SCARTO"]
    await _lavora_finche(db_url, dip, sc["pid"])
    assert (await _stato(db_url, sc["pid"]))["stato"] == "ESEGUITA"
    assert (await _oggetti(db_url, sc["pid"]))[sc["aip1"]]["esito"] == "ELIMINATO"
    assert versioni(sc["s3"], f"{sc['tenant']}/{sc['aip1']}/") == []


async def test_d20_riprese_automatiche_limitate_dal_parametro(db_url, s3_ciclo,
                                                              monkeypatch) -> None:
    dip = dipendenze(db_url)
    sc = await _con_eccezioni(db_url, s3_ciclo, monkeypatch, dip)
    await _parametro(db_url, "SCARTO_RIPRESE_AUTOMATICHE", 1)
    await sql(db_url, "UPDATE public.pratiche SET dati = dati || '{\"riprese_automatiche\": 1}'"
                      "::jsonb WHERE id = $1::uuid", sc["pid"])
    sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip", Key=sc["chiave_hold"],
                                   LegalHold={"Status": "OFF"})
    esito = await _giro_periodico(dip)
    assert esito["riprese_scarto"]["riprese"] == []


# ── D21: proposta di scarto automatica ────────────────────────────────────

async def test_d21_proposta_automatica_in_bozza(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    altro = await crea_ente(db_url)       # disattivata (predefinito): nulla
    await _parametro(db_url, "SCARTO_PROPOSTA_AUTOMATICA", "ATTIVA", ente=sc["tenant"])
    dip = dipendenze(db_url)
    esito = await _giro_periodico(dip)
    enti = esito["proposte_scarto"]["enti"]
    assert list(enti) == [str(sc["tenant"])] and str(altro) not in enti
    creata = enti[str(sc["tenant"])]
    assert creata["esito"] == "PROPOSTA_CREATA" and creata["selezionabili"] == 2
    pid = creata["pratica"]
    riga = (await sql(db_url, "SELECT stato, creata_ruolo, creata_da, impronta_piano "
                              "FROM public.pratiche WHERE id = $1::uuid", pid))[0]
    assert (riga["stato"], riga["creata_ruolo"], riga["creata_da"]) == ("BOZZA", "SISTEMA", None)
    fatti = await sql(db_url, "SELECT esito FROM public.esiti_esecuzione WHERE pratica_id = "
                              "$1::uuid", pid)
    assert [f["esito"] for f in fatti] == ["PROPOSTA_GENERATA"]
    assert [e["sottotipo"] for e in await outbox(db_url, "ciclo_vita.avviso")] == [
        "PROPOSTA_SCARTO"]
    # subito dopo: il passo non è dovuto; reso dovuto, una sola proposta aperta
    assert "proposte_scarto" not in await _giro_periodico(dip)
    await _azzera_passi(db_url)
    esito = await _giro_periodico(dip)
    assert esito["proposte_scarto"]["enti"][str(sc["tenant"])]["esito"] == "PROPOSTA_GIA_APERTA"
    async with client_app() as c:
        dettaglio = (await c.get(f"{URL}/pratiche/{pid}", headers=sc["admin"])).json()
        assert "preparata in automatico" in dettaglio["in_parole"]
        # l'ente la porta avanti come una proposta manuale
        assert (await _tr(c, pid, sc["admin"], "BOZZA", "PROPOSTA")).status_code == 200
    from app.application import autorizzazioni
    from app.infrastructure.ambito import ambito_lavoratore

    with ambito_lavoratore("prova"):
        verifica = await autorizzazioni.verifica(uuid.UUID(pid), datetime.now(UTC))
    assert not [d for d in verifica.difetti if d.startswith(("CREAZIONE", "CRONOLOGIA_INIZIO"))]
    # senza il fatto del lavoratore la creazione «SISTEMA» non regge
    await sql(db_url, "DO $x$ BEGIN SET LOCAL session_replication_role = replica; "
                      f"DELETE FROM public.esiti_esecuzione WHERE pratica_id = '{pid}'; END $x$")
    with ambito_lavoratore("prova"):
        verifica = await autorizzazioni.verifica(uuid.UUID(pid), datetime.now(UTC))
    assert any(d.startswith("CREAZIONE_SISTEMA_NON_PROVATA") for d in verifica.difetti)


async def test_d21_classi_escluse(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    await _parametro(db_url, "SCARTO_PROPOSTA_AUTOMATICA", "ATTIVA", ente=sc["tenant"])
    await _parametro(db_url, "SCARTO_PROPOSTA_CLASSI_ESCLUSE", ["contratti"], ente=sc["tenant"])
    esito = await _giro_periodico(dipendenze(db_url))
    assert esito["proposte_scarto"]["enti"][str(sc["tenant"])]["esito"] in (
        "TUTTE_LE_CLASSI_ESCLUSE", "NULLA_DA_SCARTARE")


# ── D06: minimizzazione ───────────────────────────────────────────────────

async def _scarto_eseguito(db_url, s3_ciclo, dip) -> dict:
    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
    await lavora(dip)
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"
    return {**sc, "pid": pid}


async def test_d06_minimizzazione_dopo_il_periodo(db_url, s3_ciclo) -> None:
    dip = dipendenze(db_url)
    sc = await _scarto_eseguito(db_url, s3_ciclo, dip)
    (aip1, urn1), (aip2, _) = sc["pda"]
    # un PdA conservato (non scartato) dello stesso ente: mai toccato
    classe = (await sql(db_url, "SELECT id FROM public.classi_documentali WHERE tenant_id = $1",
                        sc["tenant"]))[0]["id"]
    utente = (await sql(db_url, "SELECT id FROM public.users WHERE tenant_id = $1",
                        sc["tenant"]))[0]["id"]
    pdv_vivo = await crea_pdv(db_url, sc["tenant"], classe, utente)
    await crea_pda(db_url, sc["tenant"], pdv_vivo, scadenza=PASSATO)
    # R12a (VD-C-149): il PdA scartato è collegato a un fascicolo
    fascicolo = (await sql(db_url, "INSERT INTO public.fascicoli (tenant_id, codice, titolo) "
                                   "VALUES ($1, 'F-2015-1', 'Fascicolo di prova') "
                                   "RETURNING id", sc["tenant"]))[0]["id"]
    await sql(db_url, "INSERT INTO public.fascicoli_aip (fascicolo_id, aip_id) VALUES ($1, $2)",
              fascicolo, aip1)
    # entro il periodo (30 giorni): nulla
    esito = await _giro_periodico(dip)
    assert esito["minimizzazione"]["minimizzati"] == 0
    await sql(db_url, "UPDATE public.pratiche SET conclusa_il = now() - interval '31 days' "
                      "WHERE id = $1::uuid", sc["pid"])
    await _azzera_passi(db_url)
    esito = await _giro_periodico(dip)
    assert esito["minimizzazione"]["minimizzati"] == 2
    righe = await sql(db_url, "SELECT v.oggetto, v.metadata_json, a.urn, a.hash_sha256 FROM "
                              "public.aip a JOIN public.pdv v ON v.id = a.pdv_id WHERE a.id = $1",
                      aip1)
    meta = json.loads(righe[0]["metadata_json"])
    assert righe[0]["oggetto"].startswith("[minimizzato")
    assert "indice" not in meta and meta["minimizzazione"]["pratica"] == sc["pid"]
    assert (righe[0]["urn"], righe[0]["hash_sha256"]) == (urn1, "cd" * 32)  # elenco minimo
    vivo = await sql(db_url, "SELECT oggetto, metadata_json FROM public.pdv WHERE id = $1",
                     pdv_vivo)
    assert vivo[0]["oggetto"] == "PdV di prova" and "indice" in json.loads(
        vivo[0]["metadata_json"])
    fatto = await sql(db_url, "SELECT dettagli FROM public.esiti_esecuzione WHERE aip_id = $1 "
                              "AND esito = 'METADATI_MINIMIZZATI'", aip1)
    impronta = json.loads(fatto[0]["dettagli"])["impronta"]
    assert dip.registro.foglie[("minimizzazione", urn1, sc["pid"])]["impronta"] == impronta
    # R12a (VD-C-149): PdA rimesso nella coda dell'indicizzazione, fascicoli scollegati
    coda = await sql(db_url, "SELECT stato FROM public.indicizzazione_aip WHERE aip_id = $1",
                     aip1)
    assert [r["stato"] for r in coda] == ["IN_CODA"]
    assert await sql(db_url, "SELECT 1 FROM public.fascicoli_aip WHERE aip_id = $1", aip1) == []
    assert json.loads(fatto[0]["dettagli"])["indice_ricerca"] == "rimesso in coda"
    # idempotente: un altro giro non rifà nulla
    await _azzera_passi(db_url)
    assert (await _giro_periodico(dip))["minimizzazione"]["minimizzati"] == 0


async def test_d06_non_tocca_pda_non_eliminati(db_url, s3_ciclo, monkeypatch) -> None:
    dip = dipendenze(db_url)
    sc = await _con_eccezioni(db_url, s3_ciclo, monkeypatch, dip)
    await sql(db_url, "UPDATE public.pratiche SET conclusa_il = now() - interval '400 days' "
                      "WHERE id = $1::uuid", sc["pid"])
    await _giro_periodico(dip)
    meta = json.loads((await sql(db_url, "SELECT v.metadata_json FROM public.aip a JOIN "
                                         "public.pdv v ON v.id = a.pdv_id WHERE a.id = $1",
                                 sc["aip1"]))[0]["metadata_json"])
    assert "indice" in meta    # PdA incompleto: dati intatti
    minimizzati = await sql(db_url, "SELECT aip_id FROM public.esiti_esecuzione WHERE esito = "
                                    "'METADATI_MINIMIZZATI'")
    assert [m["aip_id"] for m in minimizzati] == [sc["pda"][1][0]]


# ── D04: rinnovo del blocco WORM ──────────────────────────────────────────

async def test_d04_rinnovo_pda_permanente_mai_in_riduzione(db_url, s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant, codice="delibere", permanente=True)
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=None)
    await sql(db_url, "UPDATE public.aip SET anni_conservazione = 999 WHERE id = $1", aip)
    client = bucket_worm(s3_ciclo)
    adesso = datetime.now(UTC)
    vicino = adesso + timedelta(days=365 * 2)        # meno di 5 anni: da rinnovare
    lontano = adesso + timedelta(days=365 * 40)      # oltre i 30 anni: mai ridotto
    chiavi = oggetti_pda(client, tenant, aip, fino_a=vicino)
    client.put_object_retention(Bucket="verumdoc-aip", Key=chiavi[2],
                                Retention={"Mode": "COMPLIANCE", "RetainUntilDate": lontano})
    dip = dipendenze(db_url)
    esito = await _giro_periodico(dip)
    assert esito["rinnovo_retention"]["pda_rinnovati"] == 1
    termini = {k: client.head_object(Bucket="verumdoc-aip", Key=k)["ObjectLockRetainUntilDate"]
               for k in chiavi}
    trent_anni = adesso.replace(year=adesso.year + 30)
    assert all(abs((termini[k] - trent_anni).total_seconds()) < 86400 for k in chiavi[:2])
    assert abs((termini[chiavi[2]] - lontano).total_seconds()) < 2   # non ridotto
    riga = await sql(db_url, "SELECT retention_fino_a FROM public.aip WHERE id = $1", aip)
    assert abs((riga[0]["retention_fino_a"] - trent_anni).total_seconds()) < 86400
    fatto = await sql(db_url, "SELECT pratica_id, dettagli FROM public.esiti_esecuzione WHERE "
                              "aip_id = $1 AND esito = 'RINNOVATA'", aip)
    assert fatto[0]["pratica_id"] is None
    dettagli = json.loads(fatto[0]["dettagli"])
    assert dettagli["versioni_estese"] == 2
    foglia = dip.registro.foglie[("rinnovo_retention", urn, dettagli["rinnovo"])]
    assert foglia["impronta"] == dettagli["impronta"]
    # rinnovato: al giro dopo non è più da rinnovare
    await _azzera_passi(db_url)
    assert (await _giro_periodico(dip))["rinnovo_retention"]["pda_controllati"] == 0


async def test_d04_rinnovo_allegati_e_retention_nuovi_allegati(db_url, s3_ciclo) -> None:
    """Gli allegati nascono con RETENTION_PERMANENTE_ANNI (prima 100 anni fissi) e
    si rinnovano quando ne mancano meno di RETENTION_RINNOVO_SOGLIA_ANNI."""
    await _parametro(db_url, "RETENTION_PERMANENTE_ANNI", 10)
    dip = dipendenze(db_url)
    sc = await _scarto_eseguito(db_url, s3_ciclo, dip)
    allegati = await sql(db_url, "SELECT id, bucket, chiave, conserva_fino_a FROM "
                                 "public.pratiche_allegati WHERE pratica_id = $1::uuid",
                         sc["pid"])
    adesso = datetime.now(UTC)
    assert allegati and all(a["conserva_fino_a"].year == adesso.year + 10 for a in allegati)
    # soglia 15 anni (> 10 rimasti): tutti da rinnovare, fino a 40 anni
    await sql(db_url, "UPDATE public.impostazioni_valori SET valore = '40'::jsonb WHERE codice = "
                      "'RETENTION_PERMANENTE_ANNI'")
    await _parametro(db_url, "RETENTION_RINNOVO_SOGLIA_ANNI", 15)
    esito = await _giro_periodico(dip)
    assert esito["rinnovo_retention"]["allegati_rinnovati"] == len(allegati)
    for a in allegati:
        testa = sc["s3"].head_object(Bucket=a["bucket"], Key=a["chiave"])
        assert testa["ObjectLockRetainUntilDate"].year == adesso.year + 40
    fatti = await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione WHERE esito = "
                              "'RINNOVATA_ALLEGATO' AND pratica_id = $1::uuid", sc["pid"])
    assert len(fatti) == len(allegati)
    await _azzera_passi(db_url)
    assert (await _giro_periodico(dip))["rinnovo_retention"]["allegati_rinnovati"] == 0


async def test_stato_automatico_solo_conservatore(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    await _giro_periodico(dipendenze(db_url))
    async with client_app() as c:
        r = await c.get(f"{URL}/stato-automatico", headers=sc["admin"])
        assert r.status_code == 403
        r = await c.get(f"{URL}/stato-automatico", headers=sc["rsc"])
        assert r.status_code == 200, r.text
        corpo = r.json()
    assert {p["nome"] for p in corpo["passi"]} >= {"proposte_scarto", "riprese_scarto",
                                                     "minimizzazione", "rinnovo_retention"}
    assert corpo["retention"]["anni"] == 30 and "si rinnovano da soli" in corpo["in_parole"][0]


def test_d04_estendi_retention_non_riduce_mai(s3_ciclo) -> None:
    """La guardia dello storage da sola (anche se il chiamante sbagliasse): una
    data precedente alla retention attuale non si scrive."""
    from app.infrastructure import storage

    client = bucket_worm(s3_ciclo)
    adesso = datetime.now(UTC)
    lontano, vicino = adesso + timedelta(days=3650), adesso + timedelta(days=365)
    chiave = f"prova-r09/{uuid.uuid4()}/manifest.json"
    client.put_object(Bucket="verumdoc-aip", Key=chiave, Body=b"{}",
                      ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=lontano)
    dopo = storage.estendi_retention(client, "verumdoc-aip", chiave, None, vicino)
    letta = client.head_object(Bucket="verumdoc-aip", Key=chiave)["ObjectLockRetainUntilDate"]
    assert abs((letta - lontano).total_seconds()) < 2 and dopo == letta
    piu_lontano = adesso + timedelta(days=7300)
    storage.estendi_retention(client, "verumdoc-aip", chiave, None, piu_lontano)
    letta = client.head_object(Bucket="verumdoc-aip", Key=chiave)["ObjectLockRetainUntilDate"]
    assert abs((letta - piu_lontano).total_seconds()) < 2
