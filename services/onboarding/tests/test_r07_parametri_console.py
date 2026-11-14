# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07 — parametri in console: modifiche programmate (D28), avviso dei versamenti
senza Idempotency-Key (D17), l'ente che può solo ridurre (D14), preferenze
personali (D31, D29), natura dell'ente con motivo solo per PRIVATO (D25).

Su PostgreSQL vero con le rotte reali della configurazione e il lavoro periodico
(``app.services.programmate.giro``) chiamato con l'orologio dei test.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from app.services import programmate
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from libs.shared.parametri import svuota_cache
from libs.verumdoc_rules.impostazioni import giorno_locale
from tests.infra_reale import richiede_pg, sql
from tests.test_r05_configurazione import (  # noqa: F401 — fixture e aiuti
    _bozza,
    _cache_e_pepper,
    _chiama,
    _classe,
    _ente,
    _pdv,
    _scenario,
    _token,
    _utente,
    db_url,
)

pytestmark = richiede_pg


async def _applica(token: str, bozza: dict, valido_dal: str | None = None) -> tuple[int, dict]:
    _, piano = await _chiama("POST", f"/bozze/{bozza['bozza_id']}/anteprima", token=token)
    corpo = {"impronta_piano": piano["impronta_piano"]}
    if valido_dal:
        corpo["valido_dal"] = valido_dal
    return await _chiama("POST", f"/bozze/{bozza['bozza_id']}/applica", token=token, corpo=corpo)


async def _valore_ente(url: str, tid: str, codice: str):
    righe = await sql(url, "SELECT valore::text AS v FROM public.impostazioni_valori "
                           "WHERE livello = 'ENTE' AND tenant_id = $1 AND codice = $2",
                      uuid.UUID(tid), codice)
    return json.loads(righe[0]["v"]) if righe else None


# ── D28: modifiche programmate ──────────────────────────────────────

async def test_modifica_programmata_anteprima_il_giorno_prima_poi_applicata(db_url) -> None:
    s = await _scenario(db_url)
    adesso = datetime.now(UTC)
    tra_due = (giorno_locale(adesso) + timedelta(days=2)).isoformat()
    b = await _bozza(s.t_admin, {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                                 "modifiche": [{"codice": "STAGING_SCARTATI_GIORNI",
                                                "valore": 60}],
                                 "motivo": "Spazio dello staging ridotto dal mese prossimo"})
    stato, esito = await _applica(s.t_admin, b, tra_due)
    assert stato == 200 and esito["stato"] == "PROGRAMMATA", esito
    assert esito["valido_dal"] == tra_due
    assert await _valore_ente(db_url, s.tid, "STAGING_SCARTATI_GIORNI") is None
    _, elenco = await _chiama("GET", "/bozze", token=s.t_admin, params={"stato": "PROGRAMMATA"})
    assert [x["bozza_id"] for x in elenco["items"]] == [b["bozza_id"]]
    # passato e proposte: no
    b2 = await _bozza(s.t_admin, {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                                  "modifiche": [{"codice": "PDD_VALIDITA_ORE", "valore": 48}],
                                  "motivo": "Restituzioni con tempi più brevi"})
    ieri = (giorno_locale(adesso) - timedelta(days=1)).isoformat()
    stato, err = await _applica(s.t_admin, b2, ieri)
    assert stato == 422 and err["codice"] == "CFG-423"

    # oggi: niente; domani (il giorno prima): anteprima ricalcolata e autore avvisato
    assert (await programmate.giro(adesso)).anteprime == []
    domani = adesso + timedelta(days=1)
    giro = await programmate.giro(domani)
    assert giro.anteprime == [b["bozza_id"]] and giro.applicate == []
    _, bozza = await _chiama("GET", f"/bozze/{b['bozza_id']}", token=s.t_admin)
    assert bozza["anteprima_programmata"]["modifiche"][0]["dopo"] == 60
    assert bozza["avviso_autore_il"] is not None
    messaggi = await sql(db_url, "SELECT payload FROM public.outbox_eventi "
                                 "WHERE topic = 'notification.send'")
    assert len(messaggi) == 1
    payload = json.loads(messaggi[0]["payload"])
    assert payload["destinatario"].endswith("@x.it") and tra_due in payload["oggetto"]
    assert (await programmate.giro(domani)).anteprime == []      # una volta sola

    # alla decorrenza: applicata con lo storico e l'audit della bozza
    giro = await programmate.giro(adesso + timedelta(days=2))
    assert giro.applicate == [b["bozza_id"]]
    assert await _valore_ente(db_url, s.tid, "STAGING_SCARTATI_GIORNI") == 60
    _, bozza = await _chiama("GET", f"/bozze/{b['bozza_id']}", token=s.t_admin)
    assert bozza["stato"] == "APPLICATA" and bozza["applicata_da"] == s.admin
    storico = await sql(db_url, "SELECT bozza_id FROM public.impostazioni_storico "
                                "WHERE codice = 'STAGING_SCARTATI_GIORNI'")
    assert [str(r["bozza_id"]) for r in storico] == [b["bozza_id"]]


async def test_programmata_annullata_o_con_autore_disattivato_non_si_applica(db_url) -> None:
    s = await _scenario(db_url)
    adesso = datetime.now(UTC)
    domani = (giorno_locale(adesso) + timedelta(days=1)).isoformat()
    corpo = {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
             "modifiche": [{"codice": "PDD_VALIDITA_ORE", "valore": 48}],
             "motivo": "Restituzioni con tempi più brevi"}
    annullata = await _bozza(s.t_admin, corpo)
    assert (await _applica(s.t_admin, annullata, domani))[1]["stato"] == "PROGRAMMATA"
    stato, _ = await _chiama("DELETE", f"/bozze/{annullata['bozza_id']}", token=s.t_admin)
    assert stato == 204
    altra = await _bozza(s.t_admin, corpo)
    assert (await _applica(s.t_admin, altra, domani))[1]["stato"] == "PROGRAMMATA"
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1",
              uuid.UUID(s.admin))
    giro = await programmate.giro(adesso + timedelta(days=1))
    assert giro.applicate == [] and giro.bloccate == [altra["bozza_id"]]
    assert await _valore_ente(db_url, s.tid, "PDD_VALIDITA_ORE") is None
    [riga] = await sql(db_url, "SELECT stato, esito::text AS e FROM public.configurazione_bozze "
                               "WHERE id = $1", uuid.UUID(altra["bozza_id"]))
    assert riga["stato"] == "APERTA"
    assert json.loads(riga["e"])["programmazione"]["esito"] == "BLOCCATA"
    [annullata_db] = await sql(db_url, "SELECT stato FROM public.configurazione_bozze "
                                       "WHERE id = $1", uuid.UUID(annullata["bozza_id"]))
    assert annullata_db["stato"] == "ANNULLATA"


# ── D14: l'ente può solo ridurre ────────────────────────────────────

async def test_inattivita_l_ente_puo_solo_ridurre(db_url) -> None:
    s = await _scenario(db_url)
    codice = "SESSIONE_INATTIVITA_MINUTI_OPERATORE"
    alta = await _bozza(s.t_admin, {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                                    "modifiche": [{"codice": codice, "valore": 45}],
                                    "motivo": "Sessioni più lunghe per gli operatori"})
    _, piano = await _chiama("POST", f"/bozze/{alta['bozza_id']}/anteprima", token=s.t_admin)
    assert [b["codice"] for b in piano["bloccati"]] == [codice]
    assert "solo ridurre" in piano["bloccati"][0]["motivo"]
    bassa = await _bozza(s.t_admin, {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                                     "modifiche": [{"codice": codice, "valore": 10}],
                                     "motivo": "Sessioni più brevi per gli operatori"})
    stato, esito = await _applica(s.t_admin, bassa)
    assert stato == 200 and esito["stato"] == "APPLICATA"
    _, elenco = await _chiama("GET", "/impostazioni", token=s.t_admin,
                              params={"gruppo": "sessione", "limit": 50})
    voce = next(v for v in elenco["items"] if v["codice"] == codice)
    assert voce["valore"] == 10 and voce["origine"] == "ENTE"


# ── D17: avviso ai clienti senza Idempotency-Key ────────────────────

async def _impronta(url: str, tid: str, cid: str, uid: str, chiave: str | None) -> None:
    pid = await _pdv(url, tid, cid, uid)
    await sql(url, "INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
                   "impronta_richiesta, chiave_idempotenza) VALUES ($1, $2, $3, $3, $4)",
              uuid.UUID(pid), uuid.UUID(tid), uuid.uuid4().hex + uuid.uuid4().hex, chiave)


async def test_avviso_settimanale_versamenti_senza_chiave(db_url) -> None:
    s = await _scenario(db_url)
    finita = await _scenario(db_url)
    for ente in (s, finita):
        await _impronta(db_url, ente.tid, ente.cid, ente.admin, None)
        await _impronta(db_url, ente.tid, ente.cid, ente.admin, "chiave-del-cliente-1")
    # per il secondo ente la transizione è già finita: niente avviso (riceve già 428)
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                      "motivo) VALUES ('ENTE', $1, 'IDEMPOTENZA_TRANSIZIONE_FINO_AL', "
                      "'\"2026-01-01\"', 'fine anticipata')", uuid.UUID(finita.tid))
    svuota_cache()
    adesso = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    assert await programmate.avvisa_idempotenza(adesso) == [s.tid]
    assert await programmate.avvisa_idempotenza(adesso + timedelta(hours=5)) == []
    [evento] = await sql(db_url, "SELECT payload FROM public.outbox_eventi "
                                 "WHERE topic = 'idempotenza.chiave_omessa'")
    payload = json.loads(evento["payload"])
    assert payload["tenant_id"] == s.tid and payload["versamenti"] == 1
    assert payload["fino_al"] == "2027-03-31" and payload["settimana"] == "2026-W44"


# ── D31 / D29: preferenze personali ────────────────────────────────

async def _pref(metodo: str, token: str, corpo: dict | None = None) -> tuple[int, dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.request(metodo, "/api/v1/onboarding/preferenze", json=corpo,
                            headers={"Authorization": f"Bearer {token}"})
    return r.status_code, r.json()


async def test_preferenze_personali_con_versione(db_url) -> None:
    tid = await _ente(db_url)
    uid = await _utente(db_url, tid, "OPERATORE")
    t = _token(tid, uid, "OPERATORE")
    assert await _pref("GET", t) == (200, {"preferenze": {}, "versione": 0})
    pref = {"tema": "scuro", "densita": "compatta", "pagina_iniziale": "/versamenti",
            "colonne": {"versamenti": ["stato", "ricevuto_il"]}}
    stato, r = await _pref("PUT", t, {"versione": 0, "preferenze": pref})
    assert stato == 200 and r["versione"] == 1
    assert await _pref("GET", t) == (200, {"preferenze": pref, "versione": 1})
    stato, r = await _pref("PUT", t, {"versione": 0, "preferenze": {"tema": "chiaro"}})
    assert stato == 409 and r["codice"] == "ONB-412" and r["dettagli"]["versione"] == 1
    stato, r = await _pref("PUT", t, {"versione": 1, "preferenze": {"colore": "rosso"}})
    assert stato == 422
    # l'ente in testata è dei soli ruoli globali (D29)
    stato, r = await _pref("PUT", t, {"versione": 1, "preferenze": {"ente": "tutti"}})
    assert stato == 422 and r["codice"] == "ONB-422"
    # un altro utente non vede le preferenze del primo
    altro = await _utente(db_url, tid, "OPERATORE")
    assert (await _pref("GET", _token(tid, altro, "OPERATORE")))[1]["versione"] == 0
    sa = await _utente(db_url, tid, "SUPER_ADMIN")
    t_sa = _token(tid, sa, "SUPER_ADMIN")
    stato, r = await _pref("PUT", t_sa, {"versione": 0, "preferenze": {"ente": "tutti"}})
    assert stato == 200 and r["preferenze"] == {"ente": "tutti"}


# ── D25: natura dell'ente ──────────────────────────────────────────

def test_natura_motivo_obbligatorio_solo_per_privato() -> None:
    from app.models.tenant import TenantNaturaChange

    assert TenantNaturaChange(natura="PA").motivo is None
    assert TenantNaturaChange(natura="PA", motivo="  ").motivo is None
    with pytest.raises(ValidationError, match="PRIVATO"):
        TenantNaturaChange(natura="PRIVATO")
    with pytest.raises(ValidationError, match="PRIVATO"):
        TenantNaturaChange(natura="PRIVATO", motivo="corto")


async def test_natura_pa_precompilata_dal_codice_ipa(db_url) -> None:
    tid = await _ente(db_url)
    await sql(db_url, "UPDATE public.tenants SET codice_ipa = 'c_x999' WHERE id = $1",
              uuid.UUID(tid))
    sa = await _utente(db_url, tid, "SUPER_ADMIN")
    token = create_access_token(tenant_id=tid, user_id=sa, ruolo="SUPER_ADMIN")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put(f"/api/v1/onboarding/tenant/{tid}/natura", json={"natura": "PA"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert r.json()["natura"] == "PA" and r.json()["codice_ipa"] == "c_x999"
        r = await c.put(f"/api/v1/onboarding/tenant/{tid}/natura", json={"natura": "PRIVATO"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 422
    [riga] = await sql(db_url, "SELECT dettagli FROM public.audit_global "
                               "WHERE evento = 'tenant.natura_dichiarata' AND tenant_id = $1",
                       uuid.UUID(tid))
    dettagli = json.loads(riga["dettagli"])
    # W8: il codice è solo dichiarato (nessuna verifica sull'indice IPA)
    assert dettagli["origine_motivo"] == "dichiarato_ipa"
    assert dettagli["motivo"] == "Codice IPA dichiarato c_x999 (non verificato sull'indice IPA)"
