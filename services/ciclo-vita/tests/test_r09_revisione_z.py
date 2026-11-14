# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente R09 — correzioni Z1 e Z3 (le prove del revisore come test
di regressione).

  - Z1 (GRAVE): quattro occhi aggirabili con una chiave API il cui responsabile
    è un'altra persona; con la doppia approvazione l'annullamento lo chiede una
    persona, approva solo un ADMIN_TENANT con un account nato prima della
    richiesta (VD-P-90); API e lavoratore controllano lo stesso;
  - Z3 (MEDIO): l'annullamento logico lo applica SOLO il lavoratore dopo la
    riverifica; una regola resa più severa nel frattempo rimette la pratica in
    attesa della seconda persona con il PdV intatto; gli annullamenti già
    applicati prima di R09 e in coda al deploy ricevono dalla migrazione 048 il
    fatto che il lavoratore pretende.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    client_app,
    crea_classe,
    crea_pdv,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
    outbox,
)
from tests.test_r09_ciclo_vita_automatico import URL, _conservato, _stato_pdv

pytestmark = [pytest.mark.integration, richiede_pg]

MOTIVO = {"motivo": "Documento versato per errore"}


async def _regola(db_url, tenant, valore: str) -> None:
    from libs.shared.parametri import svuota_cache

    await sql(db_url, "UPDATE public.impostazioni_valori SET valore = CAST($2 AS jsonb) "
                      "WHERE tenant_id = $1 AND codice IN ('ANNULLAMENTO_DOPPIA_APPROVAZIONE', "
                      "'LIMITE.ANNULLAMENTO_DOPPIA_APPROVAZIONE')", tenant, json.dumps(valore))
    svuota_cache()


async def _pratica(db_url, pid) -> dict:
    riga = (await sql(db_url, "SELECT stato, lavoro, dati, ultimo_errore FROM public.pratiche "
                              "WHERE id = $1::uuid", pid))[0]
    return {"stato": riga["stato"], "lavoro": riga["lavoro"],
            "dati": json.loads(riga["dati"]) if isinstance(riga["dati"], str) else riga["dati"],
            "errore": json.loads(riga["ultimo_errore"]) if riga["ultimo_errore"] else None}


async def _fatti(db_url, pid) -> list[str]:
    return [r["esito"] for r in await sql(
        db_url, "SELECT esito FROM public.esiti_esecuzione WHERE pratica_id = $1::uuid "
                "ORDER BY scritto_il, id", pid)]


# ── Z1 ───────────────────────────────────────────────────────────────────

async def test_z1_chiave_api_con_doppia_approvazione_rifiutata(db_url, s3_ciclo) -> None:
    """Prova del revisore: l'ADMIN genera una chiave con responsabile l'OPERATORE
    e la usa per chiedere l'annullamento, poi lo approverebbe da sé. Con la
    doppia approvazione la richiesta con la chiave è rifiutata."""
    a = await _conservato(db_url, s3_ciclo, approvazione="SEMPRE")
    classe = await crea_classe(db_url, a["tenant"], codice="altra")
    ricevuto = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
    for chiave in (intestazioni(a["tenant"], "API_SYSTEM", a["operatore"]),):
        async with client_app() as c:
            r = await c.post(f"{URL}/pratiche/annullamento", headers=chiave,
                             json={"pdv_id": str(ricevuto), **MOTIVO})
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-QUATTRO-OCCHI", r.text
    assert await _stato_pdv(db_url, ricevuto) == "RICEVUTO"
    assert await sql(db_url, "SELECT 1 FROM public.pratiche WHERE pdv_id = $1", ricevuto) == []


async def test_z1_chiave_api_e_regola_resa_severa_prima_del_lavoratore(db_url,
                                                                       s3_ciclo) -> None:
    """Richiesta con la chiave quando la regola non chiede la seconda persona,
    poi la regola diventa SEMPRE: il lavoratore non applica (la richiesta non è
    di una persona) e la pratica non si può approvare; il PdV resta intatto."""
    a = await _conservato(db_url, s3_ciclo, approvazione="MAI")
    classe = await crea_classe(db_url, a["tenant"], codice="altra")
    ricevuto = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "API_SYSTEM", a["operatore"]),
                         json={"pdv_id": str(ricevuto), **MOTIVO})
        assert r.status_code == 201 and r.json()["stato"] == "RICHIESTA", r.text
        pid = r.json()["id"]
    await _regola(db_url, a["tenant"], "SEMPRE")
    await lavora(dipendenze(db_url))
    pratica = await _pratica(db_url, pid)
    # revisione R09 (W1): non più bloccata senza uscita ma RESPINTA dal sistema
    assert (pratica["stato"], pratica["lavoro"], pratica["errore"]) == ("RESPINTA", None, None)
    assert any(d.startswith("RICHIEDENTE_NON_PERSONA")
               for d in pratica["dati"]["respinta_dal_sistema"]["difetti"])
    assert await _stato_pdv(db_url, ricevuto) == "RICEVUTO"
    assert await _fatti(db_url, pid) == []


async def test_z1_approvatore_creato_dopo_la_richiesta(db_url, s3_ciclo) -> None:
    """Variante del revisore: un account creato DOPO la richiesta non approva
    (API) e un'approvazione scritta a mano a suo nome non regge (lavoratore)."""
    a = await _conservato(db_url, s3_ciclo)   # DOPO_CONSERVAZIONE
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(a["pdv"]), **MOTIVO})
        pid = r.json()["id"]
        alias = await crea_utente(db_url, a["tenant"], "ADMIN_TENANT")
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", alias),
                         json={"esito": "APPROVA", "motivo": "approvo con l'alias"})
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-QUATTRO-OCCHI"
        assert "esisteva già" in r.text
    # API compromessa: approvazione dell'alias scritta col login dell'API
    await sql(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, "
                      "a_stato, utente_id, ruolo, metodo, motivo) VALUES ($1::uuid, $2, "
                      "'RICHIESTA', 'APPROVATA', $3, 'ADMIN_TENANT', 'jwt', 'alias')",
              pid, a["tenant"], alias)
    await sql(db_url, "UPDATE public.pratiche SET stato = 'APPROVATA', lavoro = 'ANNULLA', "
                      "prossimo_tentativo = now() WHERE id = $1::uuid", pid)
    await lavora(dipendenze(db_url))
    pratica = await _pratica(db_url, pid)
    assert any(d.startswith("APPROVATORE_POSTERIORE") for d in pratica["errore"]["difetti"])
    assert await _stato_pdv(db_url, a["pdv"]) == "CONSERVATO"
    assert await _fatti(db_url, pid) == []


def test_z1_operatore_non_approva_nemmeno_se_configurato() -> None:
    """VD-P-90: il motore non accetta OPERATORE fra i ruoli che approvano."""
    from libs.verumdoc_rules.impostazioni import ValoreImpostazioneNonValido
    from libs.verumdoc_rules.parametri_servizio import (
        ANNULLAMENTO_RUOLI_APPROVAZIONE,
        REGISTRO_SERVIZIO,
    )

    voce = REGISTRO_SERVIZIO[ANNULLAMENTO_RUOLI_APPROVAZIONE]
    assert tuple(voce.converti(["ADMIN_TENANT"])) == ("ADMIN_TENANT",)
    with pytest.raises(ValoreImpostazioneNonValido):
        voce.converti(["ADMIN_TENANT", "OPERATORE"])


# ── Z3 ───────────────────────────────────────────────────────────────────

async def test_z3_regola_resa_severa_rimette_in_attesa_con_pdv_intatto(db_url,
                                                                      s3_ciclo) -> None:
    """Prova del revisore: annullamento accettato senza seconda persona (MAI),
    poi la regola diventa SEMPRE prima che il lavoratore passi. Prima: PdV
    ANNULLATO senza atto, fatto né foglia. Ora: l'API non tocca il PdV, il
    lavoratore rimette la pratica in attesa della seconda persona."""
    a = await _conservato(db_url, s3_ciclo, approvazione="MAI")
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(a["pdv"]), **MOTIVO})
        assert r.status_code == 201 and r.json()["stato"] == "RICHIESTA", r.text
        pid = r.json()["id"]
    assert await _stato_pdv(db_url, a["pdv"]) == "CONSERVATO"     # l'API non applica
    await _regola(db_url, a["tenant"], "SEMPRE")
    dip = dipendenze(db_url)
    await lavora(dip)
    pratica = await _pratica(db_url, pid)
    assert (pratica["stato"], pratica["lavoro"], pratica["errore"]) == ("RICHIESTA", None, None)
    assert pratica["dati"]["approvazione"]["richiesta"] is True
    assert await _stato_pdv(db_url, a["pdv"]) == "CONSERVATO"
    assert await _fatti(db_url, pid) == [] and dip.registro.foglie == {}
    assert [e["sottotipo"] for e in await outbox(db_url, "ciclo_vita.avviso")] == [
        "ANNULLAMENTO_DA_APPROVARE"]
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["secondo"]),
                         json={"esito": "APPROVA", "motivo": "Verificato: versato per errore"})
        assert r.status_code == 200 and r.json()["stato"] == "APPROVATA", r.text
    await lavora(dip)
    assert await _stato_pdv(db_url, a["pdv"]) == "ANNULLATO"
    assert await _fatti(db_url, pid) == ["AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO"]
    assert ("annullamento", a["urn"], pid) in dip.registro.foglie


async def test_z3_pdv_cambiato_prima_del_lavoratore_blocca_senza_toccarlo(db_url,
                                                                         s3_ciclo) -> None:
    a = await _conservato(db_url, s3_ciclo, approvazione="MAI")
    classe = await crea_classe(db_url, a["tenant"], codice="altra")
    ricevuto = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(ricevuto), **MOTIVO})
        pid = r.json()["id"]
    await sql(db_url, "UPDATE public.pdv SET stato = 'IN_VALIDAZIONE' WHERE id = $1", ricevuto)
    await lavora(dipendenze(db_url))
    pratica = await _pratica(db_url, pid)
    # revisione R09 (W1): RESPINTA dal sistema, si può chiedere di nuovo
    assert pratica["stato"] == "RESPINTA" and pratica["errore"] is None
    assert any(d.startswith("PDV_CAMBIATO")
               for d in pratica["dati"]["respinta_dal_sistema"]["difetti"])
    assert await _stato_pdv(db_url, ricevuto) == "IN_VALIDAZIONE"
    assert await _fatti(db_url, pid) == []


def _migrazione_048():
    percorso = (Path(__file__).resolve().parents[2] / "gateway" / "alembic" / "versions"
                / "20261101_048_ciclo_vita_automatico.py")
    spec = importlib.util.spec_from_file_location("migrazione_048", percorso)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize("migrata", [False, True])
async def test_z3_annullamento_in_coda_al_deploy(db_url, s3_ciclo, migrata) -> None:
    """Annullamento applicato dall'API con le regole R08 (nessuna seconda persona)
    e ancora in coda (ESEGUITA, lavoro REGISTRO) al deploy di R09, che porta il
    predefinito a DOPO_CONSERVAZIONE. Con il fatto scritto dalla migrazione 048
    il lavoratore lo conclude; senza (pratica scritta a mano dopo il deploy) no."""
    a = await _conservato(db_url, s3_ciclo, approvazione="MAI")
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(a["pdv"]), **MOTIVO})
        pid = r.json()["id"]
    # come l'API R08: PdV annullato e pratica ESEGUITA nella richiesta
    await sql(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, "
                      "a_stato, ruolo, metodo, motivo) VALUES ($1::uuid, $2, 'RICHIESTA', "
                      "'ESEGUITA', 'SISTEMA', 'lavoratore', 'Annullamento logico applicato')",
              pid, a["tenant"])
    await sql(db_url, "UPDATE public.pratiche SET stato = 'ESEGUITA', lavoro = 'REGISTRO', "
                      "prossimo_tentativo = now() WHERE id = $1::uuid", pid)
    await sql(db_url, "UPDATE public.pdv SET stato = 'ANNULLATO', metadata_json = metadata_json "
                      "|| jsonb_build_object('annullamento', jsonb_build_object('pratica_id', "
                      "CAST($2 AS text))) WHERE id = $1", a["pdv"], pid)
    await sql(db_url, "DELETE FROM public.impostazioni_valori WHERE tenant_id = $1 AND codice "
                      "LIKE '%ANNULLAMENTO_DOPPIA_APPROVAZIONE'", a["tenant"])
    if migrata:
        await sql(db_url, _migrazione_048()._ANNULLAMENTI_IN_CODA)
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    dip = dipendenze(db_url)
    await lavora(dip)
    pratica = await _pratica(db_url, pid)
    if migrata:
        assert pratica["lavoro"] is None and pratica["errore"] is None
        assert await _fatti(db_url, pid) == ["AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO"]
        assert ("annullamento", a["urn"], pid) in dip.registro.foglie
    else:
        assert any(d.startswith("ANNULLAMENTO_NON_APPLICATO")
                   for d in pratica["errore"]["difetti"])
        assert dip.registro.foglie == {}


# ── Z4, Z5 e lease del lavoro periodico ─────────────────────────────────────

async def _pda_permanente(db_url, client, tenant, admin, classe, *, giorni: int | None):
    from datetime import UTC, datetime, timedelta

    from tests.supporto import crea_pda, oggetti_pda

    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=None)
    await sql(db_url, "UPDATE public.aip SET anni_conservazione = 999 WHERE id = $1", aip)
    if giorni is not None:
        fino = datetime.now(UTC) + timedelta(days=giorni)
        oggetti_pda(client, tenant, aip, fino_a=fino)
        await sql(db_url, "UPDATE public.aip SET retention_fino_a = $2 WHERE id = $1", aip, fino)
    return aip, urn


async def _ente_permanente(db_url, s3_ciclo):
    from tests.supporto import bucket_worm, crea_ente

    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant, codice="delibere", permanente=True)
    return tenant, admin, classe, bucket_worm(s3_ciclo)


async def test_z4_rinnovo_impossibile_non_risulta_fatto_e_da_allarme(db_url, s3_ciclo) -> None:
    """Prova del revisore: anni = soglia (valori ammessi uno per uno). Prima il
    passo risultava FATTO in silenzio; ora non è fatto, si riprova, metrica."""
    from app.application import metriche

    from tests.test_r09_ciclo_vita_automatico import _azzera_passi, _giro_periodico, _parametro

    await _parametro(db_url, "RETENTION_PERMANENTE_ANNI", 10)
    await _parametro(db_url, "RETENTION_RINNOVO_SOGLIA_ANNI", 10)
    await _azzera_passi(db_url)
    esito = await _giro_periodico(dipendenze(db_url))
    assert esito["rinnovo_retention"]["errore"] == "RinnovoNonPossibile"
    riga = (await sql(db_url, "SELECT ultimo_giro, ultimo_esito FROM public.ciclo_vita_periodico "
                              "WHERE nome = 'rinnovo_retention'"))[0]
    assert riga["ultimo_giro"] is None                          # non fatto: si riprova
    assert "RinnovoNonPossibile" in riga["ultimo_esito"]
    assert (await metriche.aggiorna())["periodici"] >= 1


async def test_z5_pda_senza_oggetti_non_affama_quelli_in_scadenza(db_url, s3_ciclo,
                                                                   monkeypatch) -> None:
    """Prova del revisore: lotto 1, un PdA permanente vuoto (NULL) e uno vero a
    400 giorni dalla scadenza. Prima: il vero mai rinnovato. Ora: code distinte,
    il vuoto esce dalla coda con la sentinella e l'allarme."""
    from app.application import metriche
    from app.application.periodico import SENZA_OGGETTI
    from app.config import settings

    from tests.test_r09_ciclo_vita_automatico import _azzera_passi, _giro_periodico

    monkeypatch.setattr(settings, "periodico_lotto", 1)
    tenant, admin, classe, client = await _ente_permanente(db_url, s3_ciclo)
    vuoto, _ = await _pda_permanente(db_url, client, tenant, admin, classe, giorni=None)
    vero, _ = await _pda_permanente(db_url, client, tenant, admin, classe, giorni=400)
    dip = dipendenze(db_url)
    esito = (await _giro_periodico(dip))["rinnovo_retention"]
    assert (esito["pda_rinnovati"], esito["pda_senza_oggetti"]) == (1, 1), esito
    righe = {r["id"]: r["retention_fino_a"] for r in await sql(
        db_url, "SELECT id, retention_fino_a FROM public.aip WHERE id = ANY($1)", [vuoto, vero])}
    assert righe[vuoto] == SENZA_OGGETTI and righe[vero].year >= 2050
    assert (await metriche.aggiorna())["senza_oggetti"] == 1
    await _azzera_passi(db_url)
    assert (await _giro_periodico(dip))["rinnovo_retention"]["pda_controllati"] == 0


async def test_z5_errore_su_un_elemento_non_ferma_il_lotto(db_url, s3_ciclo,
                                                          monkeypatch) -> None:
    from app.application import periodico

    from tests.test_r09_ciclo_vita_automatico import _giro_periodico

    tenant, admin, classe, client = await _ente_permanente(db_url, s3_ciclo)
    primo, _ = await _pda_permanente(db_url, client, tenant, admin, classe, giorni=100)
    secondo, _ = await _pda_permanente(db_url, client, tenant, admin, classe, giorni=200)
    originale = periodico._rinnova_pda

    async def guasto(r, *a, **k):
        if r["id"] == primo:
            raise OSError("storage rifiuta put_object_retention")
        return await originale(r, *a, **k)

    monkeypatch.setattr(periodico, "_rinnova_pda", guasto)
    esito = (await _giro_periodico(dipendenze(db_url)))["rinnovo_retention"]
    assert esito["pda_rinnovati"] == 1 and esito["elementi_in_errore"] == 1, esito
    riga = (await sql(db_url, "SELECT ultimo_esito FROM public.ciclo_vita_periodico "
                              "WHERE nome = 'rinnovo_retention'"))[0]
    assert json.loads(riga["ultimo_esito"])["elementi_in_errore"] == 1


async def test_z5_foglia_del_rinnovo_ripresa_se_il_registro_non_risponde(db_url,
                                                                          s3_ciclo) -> None:
    from tests.test_r09_ciclo_vita_automatico import _azzera_passi, _giro_periodico

    tenant, admin, classe, client = await _ente_permanente(db_url, s3_ciclo)
    aip, urn = await _pda_permanente(db_url, client, tenant, admin, classe, giorni=100)
    dip = dipendenze(db_url)
    dip.registro.errore = OSError("registro giù")
    esito = (await _giro_periodico(dip))["rinnovo_retention"]
    assert esito["elementi_in_errore"] == 1 and dip.registro.foglie == {}
    fatti = await sql(db_url, "SELECT dettagli FROM public.esiti_esecuzione WHERE aip_id = $1 "
                              "AND esito = 'RINNOVATA'", aip)
    assert len(fatti) == 1
    # il termine non avanza finché la foglia non c'è: il PdA torna in coda
    riga = (await sql(db_url, "SELECT retention_fino_a FROM public.aip WHERE id = $1", aip))[0]
    assert riga["retention_fino_a"].year < 2050
    dip.registro.errore = None
    await _azzera_passi(db_url)
    await _giro_periodico(dip)
    dettagli = json.loads(fatti[0]["dettagli"])
    assert ("rinnovo_retention", urn, dettagli["rinnovo"]) in dip.registro.foglie
    assert len(await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione WHERE aip_id = $1 "
                                 "AND esito = 'RINNOVATA'", aip)) == 1   # nessun fatto nuovo
    riga = (await sql(db_url, "SELECT retention_fino_a FROM public.aip WHERE id = $1", aip))[0]
    assert riga["retention_fino_a"].year >= 2050


async def test_lease_del_passo_perso_ferma_il_passo(db_url) -> None:
    """Nota della revisione: un passo più lungo del lease non continua su due
    repliche; il rinnovo proroga il lease prima di ogni elemento."""
    from datetime import UTC, datetime, timedelta

    from app.application import periodico
    from app.infrastructure.ambito import ambito_lavoratore

    adesso = datetime.now(UTC)
    trattati: list[int] = []

    async def passo_lungo() -> dict:
        for i in range(3):
            await periodico.tieni_lease()
            trattati.append(i)
            if i == 0:
                # proroga: il lease avanza
                riga = (await sql(db_url, "SELECT lease_scade_il FROM "
                                          "public.ciclo_vita_periodico WHERE nome = 'prova'"))[0]
                assert riga["lease_scade_il"] > adesso
                # un'altra replica prende il passo (lease scaduto)
                await sql(db_url, "UPDATE public.ciclo_vita_periodico SET "
                                  "lease_detentore = 'replica-2' WHERE nome = 'prova'")
        return {"fatto": True}

    with ambito_lavoratore("prova lease"):
        esito = await periodico._esegui("prova", "replica-1", adesso, timedelta(seconds=1),
                                        passo_lungo)
    assert esito == {"lease_perso": True} and trattati == [0]
    riga = (await sql(db_url, "SELECT lease_detentore, ultimo_giro FROM "
                              "public.ciclo_vita_periodico WHERE nome = 'prova'"))[0]
    assert (riga["lease_detentore"], riga["ultimo_giro"]) == ("replica-2", None)


async def test_z2_lo_scarto_registra_le_versioni_trattenute(db_url, s3_ciclo,
                                                             monkeypatch) -> None:
    """Z2 (D26): alla chiusura NON_COMPLETATO lo scarto registra chiave e version
    id di OGNI versione rimasta (quelle che la verifica continua pretende)."""
    from tests.test_r09_ciclo_vita_automatico import _con_eccezioni

    dip = dipendenze(db_url)
    sc = await _con_eccezioni(db_url, s3_ciclo, monkeypatch, dip)
    dettagli = (await sql(db_url, "SELECT dettagli FROM public.pratiche_oggetti WHERE "
                                  "pratica_id = $1::uuid AND esito = 'NON_COMPLETATO'",
                          sc["pid"]))[0]["dettagli"]
    dettagli = json.loads(dettagli) if isinstance(dettagli, str) else dettagli
    trattenute = dettagli["versioni_trattenute"]
    assert trattenute and {k for k, _ in trattenute} == {sc["chiave_hold"]}
    reali = sc["s3"].list_object_versions(Bucket="verumdoc-aip", Prefix=sc["chiave_hold"])
    assert {(v["Key"], v["VersionId"]) for v in reali.get("Versions", [])} == {
        tuple(t) for t in trattenute}
