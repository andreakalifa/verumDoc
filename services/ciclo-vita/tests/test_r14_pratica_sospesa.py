# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R14 (VD-C-133) — stato di errore esplicito dopo i tentativi esauriti.

Prima di R14 un'esecuzione avviata che non riusciva a scrivere le foglie (registro
sempre giù) ritentava ogni 7200 s per sempre con il solo allarme
PraticaLavoroInErrore. Ora (PostgreSQL vero, S3 moto con Object Lock, registro finto):

  - dopo ``LAVORATORE_TENTATIVI_MASSIMI`` tentativi falliti la pratica è SOSPESA: lavoro
    tolto, ``ultimo_errore.codice = LC-409-TENTATIVI-ESAURITI`` con il lavoro sospeso e
    l'ultimo errore, esecuzione registrata ERRORE, nessun nuovo tentativo automatico;
  - la metrica ``verumdoc_ciclo_vita_pratiche_sospese`` la conta (allarme PraticaSospesa);
  - nessuna transizione manuale (l'esecuzione è iniziata: niente RESPINTA);
  - la ripresa del runbook ``pratica_sospesa.md`` (stesso SQL) la porta a termine;
  - con ``LAVORATORE_TENTATIVI_MASSIMI = 0`` (comportamento prima di R14) non si sospende;
  - anche i lease scaduti ripetutamente (processo interrotto) portano alla sospensione.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, dipendenze, lavora
from tests.test_r05_revisione_scarto import _ente_con_pda, _fino_a_eseguibile, _tr

pytestmark = [pytest.mark.integration, richiede_pg]

RADICE = Path(__file__).resolve().parents[3]
RUNBOOK = RADICE / "docs" / "runbooks" / "pratica_sospesa.md"


async def _stato(db_url, pid) -> dict:
    return dict((await sql(db_url, "SELECT stato, lavoro, tentativi, ultimo_errore "
                                   "FROM public.pratiche WHERE id = $1::uuid", str(pid)))[0])


async def _lavora_anticipando(db_url, dip, pid, giri: int) -> list[str]:
    esiti: list[str] = []
    for _ in range(giri):
        esiti += await lavora(dip, giri=1)
        await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() - "
                          "interval '1 second' WHERE id = $1::uuid", str(pid))
    return esiti


def _sql_ripresa() -> str:
    """Il comando di ripresa del runbook (blocco ```sql marcato «ripresa»)."""
    testo = RUNBOOK.read_text(encoding="utf-8")
    inizio = testo.index("-- ripresa")
    fine = testo.index("```", inizio)
    return testo[inizio:fine].replace(":'pratica'", "$1::uuid")


async def test_scarto_avviato_con_registro_giu_si_sospende_e_si_riprende(
        db_url, s3_ciclo, monkeypatch) -> None:
    import json

    from app.application import metriche
    from app.config import settings
    from app.infrastructure.registro import ErroreRegistro
    from prometheus_client import CollectorRegistry

    monkeypatch.setattr(settings, "lavoratore_tentativi_massimi", 3)
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    dip = dipendenze(db_url)
    dip.registro.errore = ErroreRegistro("registro irraggiungibile (prova)", ritentabile=True)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        esiti = await _lavora_anticipando(db_url, dip, pid, 6)
        stato = await _stato(db_url, pid)
        errore = json.loads(stato["ultimo_errore"]) if isinstance(
            stato["ultimo_errore"], str) else stato["ultimo_errore"]
        # sospesa al terzo tentativo, poi nessun altro giro
        assert esiti == ["RINVIATO", "RINVIATO", "SOSPESO"], esiti
        assert stato["lavoro"] is None and stato["stato"] == "ESEGUIBILE"
        assert errore["codice"] == "LC-409-TENTATIVI-ESAURITI"
        assert errore["lavoro_sospeso"] == "ESEGUI" and errore["tentativi"] == 3
        assert errore["ultimo"]["codice"] and "registro" in errore["messaggio"]
        esecuzioni = await sql(db_url, "SELECT esito FROM public.pratiche_esecuzioni "
                                       "WHERE pratica_id = $1::uuid ORDER BY id", str(pid))
        assert [e["esito"] for e in esecuzioni] == ["RINVIATO", "RINVIATO", "ERRORE"]
        # allarme
        metriche.registra(CollectorRegistry())
        assert (await metriche.aggiorna())["sospese"] == 1
        # nessuna transizione manuale: l'esecuzione è iniziata
        respinta = await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "RESPINTA")
        assert respinta.status_code == 409, (respinta.json(), await _stato(db_url, pid))
        assert respinta.json()["codice"] == "LC-409-IN-ESECUZIONE"
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["rsc"])).json()
        assert not any(a["consentita"] for a in dettaglio["azioni_consentite"])
        # ripresa del runbook, corretta la causa
        dip.registro.errore = None
        await sql(db_url, _sql_ripresa(), str(pid))
        await _lavora_anticipando(db_url, dip, pid, 4)
    finale = await _stato(db_url, pid)
    assert (finale["stato"], finale["lavoro"]) == ("ESEGUITA", None)
    assert (await metriche.aggiorna())["sospese"] == 0


async def test_senza_limite_comportamento_precedente(db_url, s3_ciclo, monkeypatch) -> None:
    from app.config import settings
    from app.infrastructure.registro import ErroreRegistro

    monkeypatch.setattr(settings, "lavoratore_tentativi_massimi", 0)
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    dip = dipendenze(db_url)
    dip.registro.errore = ErroreRegistro("registro irraggiungibile (prova)", ritentabile=True)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        esiti = await _lavora_anticipando(db_url, dip, pid, 5)
    assert esiti == ["RINVIATO"] * 5
    assert (await _stato(db_url, pid))["lavoro"] == "ESEGUI"


async def test_lease_scaduti_ripetutamente_sospendono(db_url, s3_ciclo, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "lavoratore_tentativi_massimi", 3)
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
    # tre processi interrotti a metà passo (lease preso e scaduto): tentativi = 3
    await sql(db_url, "UPDATE public.pratiche SET tentativi = 3, lease_detentore = NULL, "
                      "lease_scade_il = NULL WHERE id = $1::uuid", str(pid))
    assert await lavora(dip, giri=2) == ["SOSPESO"]
    stato = await _stato(db_url, pid)
    assert stato["lavoro"] is None
    assert '"LC-409-TENTATIVI-ESAURITI"' in str(stato["ultimo_errore"]).replace("'", '"')


def test_tentativi_esauriti_puro() -> None:
    from app.application.lavoratore import tentativi_esauriti

    assert not tentativi_esauriti(0, 3) and not tentativi_esauriti(1, 3)
    assert tentativi_esauriti(2, 3) and tentativi_esauriti(7, 3)
    assert not tentativi_esauriti(10_000, 0)


def test_lavoro_sospeso_puro() -> None:
    from app.domain.stati import CODICE_TENTATIVI_ESAURITI, lavoro_sospeso

    errore = {"codice": CODICE_TENTATIVI_ESAURITI, "lavoro_sospeso": "ESEGUI"}
    assert lavoro_sospeso({"lavoro": None, "ultimo_errore": errore}) == "ESEGUI"
    assert lavoro_sospeso({"lavoro": "ESEGUI", "ultimo_errore": errore}) is None
    assert lavoro_sospeso({"lavoro": None, "ultimo_errore": {"codice": "LC-503"}}) is None
    assert lavoro_sospeso({"lavoro": None, "ultimo_errore": None}) is None


async def test_metrica_scarto_non_determinabile(db_url) -> None:
    """VD-C-122: PdA chiusi con versioni rimaste non determinabili hanno una metrica
    (allarme ScartoNonDeterminabile): il controllo manuale non resta silenzioso."""
    import uuid

    from app.application import metriche
    from prometheus_client import CollectorRegistry

    from tests.supporto import crea_ente

    metriche.registra(CollectorRegistry())
    assert (await metriche.aggiorna())["non_determinabili"] == 0
    tenant = await crea_ente(db_url)
    pid = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo) VALUES "
                      "($1, $2, 'SCARTO', 'ESEGUITA_CON_ECCEZIONI', 'scarto della prova R14')",
              pid, tenant)
    for verifica in ("non_determinabile", "inventario"):
        await sql(db_url, "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, urn, "
                          "esito, dettagli) VALUES ($1, $2, $3::text, 'NON_COMPLETATO', "
                          "jsonb_build_object('verifica_storage', $3::text))",
                  pid, tenant, verifica)
    assert (await metriche.aggiorna())["non_determinabili"] == 1


async def test_sospesa_prima_dell_avvio_resta_con_lavoro(db_url, s3_ciclo) -> None:
    """Una pratica sospesa PRIMA dell'avvio dell'esecuzione non si fa ripartire con una
    nuova transizione di esecuzione (resta «con lavoro»: ripresa solo dal runbook); la
    si può solo respingere."""
    import json

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        await sql(db_url, "UPDATE public.pratiche SET lavoro = NULL, ultimo_errore = "
                          "CAST($2 AS jsonb) WHERE id = $1::uuid", str(pid),
                  json.dumps({"codice": "LC-409-TENTATIVI-ESAURITI", "lavoro_sospeso": "ESEGUI",
                              "messaggio": "prova"}))
        di_nuovo = await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")
        assert di_nuovo.status_code == 409, di_nuovo.text
        assert di_nuovo.json()["codice"] == "LC-409-IN-ESECUZIONE"
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "RESPINTA")).status_code == 200


async def test_progressi_nella_presa_azzerano_i_tentativi(db_url, s3_ciclo, monkeypatch) -> None:
    """Revisione R14 (5): una presa che completa almeno un passo ha fatto progressi: un
    errore successivo conta come primo tentativo (nessuna sospensione di un lavoro lungo
    che avanza), e una presa che finisce azzera i tentativi."""
    from app.config import settings
    from app.infrastructure.registro import ErroreRegistro

    monkeypatch.setattr(settings, "lavoratore_tentativi_massimi", 3)
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    dip = dipendenze(db_url)
    dip.registro.errore = ErroreRegistro("registro irraggiungibile (prova)", ritentabile=True)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
    # già due tentativi falliti prima: la presa cancella il PdA (passo riuscito) e poi
    # fallisce sulle foglie → primo tentativo, non sospensione
    await sql(db_url, "UPDATE public.pratiche SET tentativi = 2 WHERE id = $1::uuid", str(pid))
    assert await lavora(dip, giri=1) == ["RINVIATO"]
    stato = await _stato(db_url, pid)
    assert stato["lavoro"] == "ESEGUI" and stato["tentativi"] == 1
    # corretta la causa, la presa finisce: tentativi azzerati
    dip.registro.errore = None
    await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now(), tentativi = 2 "
                      "WHERE id = $1::uuid", str(pid))
    assert await lavora(dip, giri=2) == ["COMPLETATO"]
    finale = await _stato(db_url, pid)
    assert (finale["stato"], finale["lavoro"], finale["tentativi"]) == ("ESEGUITA", None, 0)


async def test_ripresa_dalla_console_solo_del_conservatore(db_url, s3_ciclo, monkeypatch) -> None:
    """Revisione R14 (4): ``POST /pratiche/{id}/riprendi`` — solo il conservatore (ruolo
    fisso, token utente), versione attesa, solo una pratica sospesa; audit e avviso
    all'ente; poi il lavoratore porta a termine la pratica."""
    from app.config import settings
    from app.infrastructure.registro import ErroreRegistro

    from tests.supporto import outbox

    monkeypatch.setattr(settings, "lavoratore_tentativi_massimi", 2)
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    dip = dipendenze(db_url)
    dip.registro.errore = ErroreRegistro("registro irraggiungibile (prova)", ritentabile=True)
    motivo = {"motivo": "Registro delle impronte di nuovo raggiungibile dopo l'intervento"}
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        assert (await _lavora_anticipando(db_url, dip, pid, 3))[-1] == "SOSPESO"
        url = f"/api/v1/ciclo-vita/pratiche/{pid}"
        dettaglio = (await c.get(url, headers=sc["rsc"])).json()
        assert dettaglio["esecuzione"]["lavoro_sospeso"] == "ESEGUI"
        assert dettaglio["esecuzione"]["ripresa_consentita"] is True
        assert (await c.get(url, headers=sc["admin"])).json()["esecuzione"][
            "ripresa_consentita"] is False
        versione = dettaglio["versione"]
        # l'ente non riprende (nemmeno vede la pratica come ripresa possibile)
        r = await c.post(f"{url}/riprendi", headers=sc["admin"],
                         json={"versione": versione, **motivo})
        assert r.status_code in (403, 404), r.text
        # motivo troppo corto, versione vecchia
        r = await c.post(f"{url}/riprendi", headers=sc["rsc"],
                         json={"versione": versione, "motivo": "breve"})
        assert r.status_code == 422
        r = await c.post(f"{url}/riprendi", headers=sc["rsc"],
                         json={"versione": versione - 1, **motivo})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-STATO-CAMBIATO"
        assert (await _stato(db_url, pid))["lavoro"] is None
        # ripresa
        dip.registro.errore = None
        r = await c.post(f"{url}/riprendi", headers=sc["rsc"],
                         json={"versione": versione, **motivo})
        assert r.status_code == 200, r.text
        assert r.json()["versione"] == versione + 1
        stato = await _stato(db_url, pid)
        assert (stato["lavoro"], stato["tentativi"], stato["ultimo_errore"]) == ("ESEGUI", 0,
                                                                                 None)
        # una seconda ripresa non ha nulla da riprendere
        r = await c.post(f"{url}/riprendi", headers=sc["rsc"],
                         json={"versione": versione + 1, **motivo})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-NON-SOSPESA"
        await _lavora_anticipando(db_url, dip, pid, 3)
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"
    voci = await sql(db_url, "SELECT dettagli FROM public.audit_global WHERE evento = "
                             "'ciclo_vita.pratica_ripresa'")
    assert len(voci) == 1 and "raggiungibile" in str(voci[0]["dettagli"])
    avvisi = [a for a in await outbox(db_url, "ciclo_vita.avviso")
              if a.get("sottotipo") == "PRATICA_RIPRESA"]
    assert len(avvisi) == 1 and avvisi[0]["pratica_id"] == str(pid)


def test_ripresa_mai_con_chiave_api_ne_ruolo_personalizzato() -> None:
    """Revisione R14 (4): la ripresa è del conservatore con il proprio accesso: una chiave
    API o un ruolo personalizzato (anche con il permesso nel token) non la ottengono."""
    from app.application.ripresa import motivo_blocco_ripresa
    from app.domain.permessi import Identita
    from app.domain.stati import CODICE_TENTATIVI_ESAURITI

    sospesa = {"lavoro": None, "ultimo_errore": {"codice": CODICE_TENTATIVI_ESAURITI,
                                                 "lavoro_sospeso": "ESEGUI"}}
    rsc = Identita(tenant_id="t", user_id="u", ruolo="RSC_CONSERVATORE", metodo="jwt")
    assert motivo_blocco_ripresa(rsc, sospesa) is None
    chiave = Identita(tenant_id="t", user_id=None, ruolo="RSC_CONSERVATORE", metodo="api_key")
    assert motivo_blocco_ripresa(chiave, sospesa)[0] == "LC-403-RUOLO"
    personalizzato = Identita(tenant_id="t", user_id="u", ruolo="OPERATORE", metodo="jwt",
                              ruolo_personalizzato="r1", permessi=frozenset({"pratica:riprendi"}))
    assert motivo_blocco_ripresa(personalizzato, sospesa)[0] == "LC-403-RUOLO"
    assert motivo_blocco_ripresa(rsc, {"lavoro": "ESEGUI", "ultimo_errore": None})[0] == \
        "LC-409-NON-SOSPESA"
