# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione indipendente R09, secondo giro — correzioni W1–W3 dell'annullamento
applicato dal lavoratore (Z3). Le prove del revisore come test di regressione.

  - W1: un annullamento che il lavoratore non può applicare perché qualcosa è
    CAMBIATO (PdV preso dalla validazione, approvatore disattivato, regola resa
    più severa) si chiude RESPINTO dal sistema con avviso; uno bloccato per
    manomissione si respinge dall'ente o dal conservatore; poi una nuova richiesta
    è possibile;
  - W2: dopo l'applicazione il lavoratore ricontrolla solo l'integrità: atto,
    fatto e foglia si completano anche se l'approvatore viene disattivato;
  - W3: gli annullamenti R08 nati fra la migrazione e il Chart li recupera il
    lavoratore al primo avvio (mai quelli nati dopo).
"""
from __future__ import annotations

import json

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
RESPINGI = {"esito": "RESPINGI", "motivo": "Da respingere: il ricontrollo non è riuscito"}


async def _pratica(db_url, pid) -> dict:
    r = (await sql(db_url, "SELECT stato, lavoro, dati, ultimo_errore FROM public.pratiche "
                           "WHERE id = $1::uuid", pid))[0]
    return {"stato": r["stato"], "lavoro": r["lavoro"],
            "dati": json.loads(r["dati"]) if isinstance(r["dati"], str) else r["dati"],
            "errore": json.loads(r["ultimo_errore"]) if r["ultimo_errore"] else None}


async def _fatti(db_url, pid) -> list[str]:
    return [r["esito"] for r in await sql(
        db_url, "SELECT esito FROM public.esiti_esecuzione WHERE pratica_id = $1::uuid "
                "ORDER BY scritto_il, id", pid)]


async def _richiedi(c, headers, pdv) -> dict:
    r = await c.post(f"{URL}/pratiche/annullamento", headers=headers,
                     json={"pdv_id": str(pdv), **MOTIVO})
    assert r.status_code == 201, r.text
    return r.json()


# ── W1 ───────────────────────────────────────────────────────────────────

async def test_w1_pdv_preso_dalla_validazione_respinta_e_nuova_richiesta(db_url,
                                                                       s3_ciclo) -> None:
    """Prova del revisore: la validazione prende il PdV RICEVUTO prima del
    lavoratore. Prima: pratica bloccata per sempre, PdV non più annullabile."""
    a = await _conservato(db_url, s3_ciclo, approvazione="MAI")
    classe = await crea_classe(db_url, a["tenant"], codice="altra")
    pdv = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
    chi = intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"])
    async with client_app() as c:
        pid = (await _richiedi(c, chi, pdv))["id"]
    await sql(db_url, "UPDATE public.pdv SET stato = 'IN_VALIDAZIONE' WHERE id = $1", pdv)
    dip = dipendenze(db_url)
    await lavora(dip)
    p = await _pratica(db_url, pid)
    assert (p["stato"], p["lavoro"], p["errore"]) == ("RESPINTA", None, None)
    assert "versamento è cambiato" in p["dati"]["respinta_dal_sistema"]["motivo"]
    avvisi = [e for e in await outbox(db_url, "ciclo_vita.avviso")
              if e["sottotipo"] == "ANNULLAMENTO_RESPINTO"]
    assert len(avvisi) == 1 and avvisi[0]["pratica_id"] == pid
    assert await _fatti(db_url, pid) == []
    # il PdV prosegue; una nuova richiesta è possibile e va a buon fine
    await sql(db_url, "UPDATE public.pdv SET stato = 'CONSERVATO' WHERE id = $1", pdv)
    async with client_app() as c:
        nuova = await _richiedi(c, chi, pdv)
        dettaglio = (await c.get(f"{URL}/pratiche/{pid}", headers=chi)).json()
    assert "non applicato" in dettaglio["in_parole"]
    assert nuova["id"] != pid
    from app.application import metriche

    assert (await metriche.aggiorna())["puntuali"] == 0          # nessun allarme acceso


async def test_w1_approvata_e_approvatore_disattivato_respinta(db_url, s3_ciclo) -> None:
    a = await _conservato(db_url, s3_ciclo, approvazione="SEMPRE")
    classe = await crea_classe(db_url, a["tenant"], codice="altra")
    pdv = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
    async with client_app() as c:
        pid = (await _richiedi(c, intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                               pdv))["id"]
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["secondo"]),
                         json={"esito": "APPROVA", "motivo": "Verificato: versato per errore"})
        assert r.status_code == 200 and r.json()["stato"] == "APPROVATA", r.text
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", a["secondo"])
    await lavora(dipendenze(db_url))
    p = await _pratica(db_url, pid)
    assert (p["stato"], p["errore"]) == ("RESPINTA", None)
    assert any(d.startswith("APPROVATORE_NON_ATTIVO")
               for d in p["dati"]["respinta_dal_sistema"]["difetti"])
    assert await _stato_pdv(db_url, pdv) == "RICEVUTO"


async def test_w1_bloccata_per_manomissione_si_respinge(db_url, s3_ciclo) -> None:
    """Approvazione fabbricata (account creato dopo la richiesta): BLOCCATA con
    allarme; non si approva, la respinge l'ente (o il conservatore), poi si chiede
    di nuovo."""
    a = await _conservato(db_url, s3_ciclo)   # DOPO_CONSERVAZIONE
    chi = intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"])
    async with client_app() as c:
        pid = (await _richiedi(c, chi, a["pdv"]))["id"]
    alias = await crea_utente(db_url, a["tenant"], "ADMIN_TENANT")
    await sql(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, "
                      "a_stato, utente_id, ruolo, metodo, motivo) VALUES ($1::uuid, $2, "
                      "'RICHIESTA', 'APPROVATA', $3, 'ADMIN_TENANT', 'jwt', 'alias')",
              pid, a["tenant"], alias)
    await sql(db_url, "UPDATE public.pratiche SET stato = 'APPROVATA', lavoro = 'ANNULLA', "
                      "prossimo_tentativo = now() WHERE id = $1::uuid", pid)
    await lavora(dipendenze(db_url))
    p = await _pratica(db_url, pid)
    assert p["stato"] == "APPROVATA" and p["lavoro"] is None
    assert any(d.startswith("APPROVATORE_POSTERIORE") for d in p["errore"]["difetti"])
    from app.application import metriche

    assert (await metriche.aggiorna())["puntuali"] == 1          # allarme acceso
    async with client_app() as c:
        dettaglio = (await c.get(f"{URL}/pratiche/{pid}", headers=chi)).json()
        assert dettaglio["approvazione"]["solo_respingere"] is True
        assert dettaglio["approvazione"]["consentita"] is True
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["secondo"]),
                         json={"esito": "APPROVA", "motivo": "Approvo comunque la richiesta"})
        assert r.status_code == 409 and r.json()["codice"] == "LC-409-APPROVAZIONE"
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione",
                         headers=intestazioni(a["tenant"], "OPERATORE", a["operatore"]),
                         json=RESPINGI)
        assert r.status_code == 403
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione", headers=chi, json=RESPINGI)
        assert r.status_code == 200 and r.json()["stato"] == "RESPINTA", r.text
        assert (await metriche.aggiorna())["puntuali"] == 0
        await _richiedi(c, chi, a["pdv"])
    assert await _stato_pdv(db_url, a["pdv"]) == "CONSERVATO"


async def test_w1_conservatore_respinge_una_pratica_bloccata(db_url, s3_ciclo) -> None:
    a = await _conservato(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await _richiedi(c, intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                               a["pdv"]))["id"]
    await sql(db_url, "UPDATE public.pratiche SET ultimo_errore = CAST($2 AS jsonb) "
                      "WHERE id = $1::uuid", pid,
              json.dumps({"codice": "LC-409-AUTORIZZAZIONI", "difetti": ["CRONOLOGIA_X"]}))
    async with client_app() as c:
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione",
                         headers=intestazioni(a["tenant"], "RSC_CONSERVATORE"), json=RESPINGI)
    assert r.status_code == 200 and r.json()["stato"] == "RESPINTA", r.text


# ── W2 ───────────────────────────────────────────────────────────────────

async def test_w2_dopo_l_applicazione_si_completa_anche_con_approvatore_disattivato(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Prova del revisore: applicato, la fase REGISTRO fallisce (WORM giù) e
    intanto l'approvatore viene disattivato. Prima: BLOCCATA con il PdV
    ANNULLATO senza atto né foglia. Ora: atto, fatto e foglia completati."""
    from app.application import puntuali

    a = await _conservato(db_url, s3_ciclo)   # DOPO_CONSERVAZIONE
    async with client_app() as c:
        pid = (await _richiedi(c, intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                               a["pdv"]))["id"]
        r = await c.post(f"{URL}/pratiche/{pid}/approvazione",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["secondo"]),
                         json={"esito": "APPROVA", "motivo": "Verificato: versato per errore"})
        assert r.status_code == 200, r.text

    async def giu(*_a, **_k):
        raise RuntimeError("storage WORM non raggiungibile")

    vero = puntuali._atto_nel_worm
    monkeypatch.setattr(puntuali, "_atto_nel_worm", giu)
    await lavora(dipendenze(db_url))
    assert (await _pratica(db_url, pid))["lavoro"] == "REGISTRO"
    assert await _stato_pdv(db_url, a["pdv"]) == "ANNULLATO"
    monkeypatch.setattr(puntuali, "_atto_nel_worm", vero)
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = ANY($1)",
              [a["secondo"], a["admin"]])
    from tests.test_r09_revisione_z import _regola

    await _regola(db_url, a["tenant"], "SEMPRE")
    await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() WHERE id = $1::uuid",
              pid)
    dip = dipendenze(db_url)
    await lavora(dip)
    p = await _pratica(db_url, pid)
    assert (p["stato"], p["lavoro"], p["errore"]) == ("ESEGUITA", None, None)
    assert await _fatti(db_url, pid) == ["AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO"]
    assert ("annullamento", a["urn"], pid) in dip.registro.foglie


async def test_w2_dopo_l_applicazione_la_manomissione_resta_bloccata(db_url, s3_ciclo,
                                                                     monkeypatch) -> None:
    """Il ricontrollo d'integrità resta: una cronologia alterata dopo
    l'applicazione blocca atto e foglia."""
    from app.application import puntuali

    a = await _conservato(db_url, s3_ciclo, approvazione="MAI")
    async with client_app() as c:
        pid = (await _richiedi(c, intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                               a["pdv"]))["id"]

    async def giu(*_a, **_k):
        raise RuntimeError("storage WORM non raggiungibile")

    vero = puntuali._atto_nel_worm
    monkeypatch.setattr(puntuali, "_atto_nel_worm", giu)
    await lavora(dipendenze(db_url))
    monkeypatch.setattr(puntuali, "_atto_nel_worm", vero)
    # PdV ricondotto a un'altra pratica dopo l'applicazione
    await sql(db_url, "UPDATE public.pdv SET metadata_json = jsonb_set(metadata_json, "
                      "'{annullamento,pratica_id}', '\"altra\"') WHERE id = $1", a["pdv"])
    await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() WHERE id = $1::uuid",
              pid)
    dip = dipendenze(db_url)
    await lavora(dip)
    p = await _pratica(db_url, pid)
    assert any(d.startswith("PDV_NON_ANNULLATO_DALLA_PRATICA") for d in p["errore"]["difetti"])
    assert dip.registro.foglie == {}


# ── W3 ───────────────────────────────────────────────────────────────────

async def _come_r08(db_url, a, pdv) -> str:
    """Annullamento applicato come faceva l'API R08 (ESEGUITA nella richiesta)."""
    async with client_app() as c:
        pid = (await _richiedi(c, intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                               pdv))["id"]
    await sql(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, "
                      "a_stato, ruolo, metodo, motivo) VALUES ($1::uuid, $2, 'RICHIESTA', "
                      "'ESEGUITA', 'SISTEMA', 'lavoratore', 'Annullamento logico applicato')",
              pid, a["tenant"])
    await sql(db_url, "UPDATE public.pratiche SET stato = 'ESEGUITA', lavoro = 'REGISTRO', "
                      "prossimo_tentativo = now() WHERE id = $1::uuid", pid)
    await sql(db_url, "UPDATE public.pdv SET stato = 'ANNULLATO', metadata_json = metadata_json "
                      "|| jsonb_build_object('annullamento', jsonb_build_object('pratica_id', "
                      "CAST($2 AS text))) WHERE id = $1", pdv, pid)
    return pid


async def test_w3_annullamenti_r08_fra_migrazione_e_chart_recuperati_al_primo_avvio(
        db_url, s3_ciclo) -> None:
    from app.application.lavoratore import recupero_r08

    a = await _conservato(db_url, s3_ciclo, approvazione="MAI")
    classe = await crea_classe(db_url, a["tenant"], codice="altra")
    secondo_pdv = await crea_pdv(db_url, a["tenant"], classe, a["admin"], stato="RICEVUTO")
    prima = await _come_r08(db_url, a, a["pdv"])       # API R08 dopo la migrazione
    await sql(db_url, "DELETE FROM public.impostazioni_valori WHERE tenant_id = $1 AND codice "
                      "LIKE '%ANNULLAMENTO_DOPPIA_APPROVAZIONE'", a["tenant"])
    assert await recupero_r08() == 1                   # primo avvio del lavoratore R09
    assert await recupero_r08() == 0                   # idempotente
    dopo = await _come_r08(db_url, a, secondo_pdv)     # nato DOPO il primo avvio
    assert await recupero_r08() == 0
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    dip = dipendenze(db_url)
    await lavora(dip)
    assert (await _pratica(db_url, prima))["lavoro"] is None
    assert await _fatti(db_url, prima) == ["AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO"]
    bloccata = await _pratica(db_url, dopo)
    assert any(d.startswith("ANNULLAMENTO_NON_APPLICATO") for d in bloccata["errore"]["difetti"])
    assert await _fatti(db_url, dopo) == []


async def test_w3_solo_il_lavoratore_scrive_il_confine(db_url) -> None:
    """Il confine del recupero sta in ``ciclo_vita_periodico``, che il login
    dell'API (solo ``verumdoc_applicazione``) non può scrivere (migrazione 048)."""
    privilegi = await sql(db_url, "SELECT has_table_privilege('verumdoc_applicazione', "
                                  "'public.ciclo_vita_periodico', 'INSERT') AS ins, "
                                  "has_table_privilege('verumdoc_applicazione', "
                                  "'public.ciclo_vita_periodico', 'UPDATE') AS upd, "
                                  "has_table_privilege('verumdoc_ciclo_vita_lavoratore', "
                                  "'public.ciclo_vita_periodico', 'UPDATE') AS lav")
    assert dict(privilegi[0]) == {"ins": False, "upd": False, "lav": True}
