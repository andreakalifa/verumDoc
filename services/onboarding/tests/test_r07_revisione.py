# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07, revisione — W2 (annullamento delle bozze del conservatore) e W4 (e-mail delle
modifiche programmate anche per gli autori del conservatore), su PostgreSQL vero
con le rotte reali e il lavoro periodico chiamato con l'orologio dei test.

W2: prima ``annulla_bozza`` controllava solo l'ente e l'ambito: l'ADMIN_TENANT
annullava una modifica PROGRAMMATA dal conservatore sul suo ente (anche su voci
riservate, es. porte dei webhook) prima della decorrenza.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from app.services import programmate

from libs.verumdoc_rules.impostazioni import giorno_locale
from tests.infra_reale import richiede_pg, sql
from tests.test_r05_configurazione import (  # noqa: F401 — fixture e aiuti
    _bozza,
    _cache_e_pepper,
    _chiama,
    _ente,
    _scenario,
    _token,
    _utente,
    db_url,
)
from tests.test_r07_parametri_console import _applica

pytestmark = richiede_pg


async def _conservatore(url: str, ruolo: str = "RSC_CONSERVATORE") -> tuple[str, str]:
    """Utente del conservatore (con l'ente del conservatore, come in produzione)."""
    ente = await _ente(url)
    uid = await _utente(url, ente, ruolo)
    return uid, _token(ente, uid, ruolo)


async def test_w2_l_ente_non_annulla_le_bozze_del_conservatore(db_url) -> None:
    s = await _scenario(db_url)
    _rsc, t_rsc = await _conservatore(db_url)
    domani = (giorno_locale(datetime.now(UTC)) + timedelta(days=1)).isoformat()
    ambito = {"livello": "ENTE", "ente": s.tid}
    porte = await _bozza(t_rsc, {"risorsa": "impostazioni", "ambito": ambito,
                                 "modifiche": [{"codice": "WEBHOOK_PORTE_AGGIUNTIVE",
                                                "valore": []}],
                                 "motivo": "Revoca della porta 8443 dal mese prossimo"})
    assert (await _applica(t_rsc, porte, domani))[1]["stato"] == "PROGRAMMATA"
    stato, err = await _chiama("DELETE", f"/bozze/{porte['bozza_id']}", token=s.t_admin)
    assert stato == 403 and err["codice"] == "CFG-403", err
    # anche una bozza APERTA del conservatore su una voce non riservata
    aperta = await _bozza(t_rsc, {"risorsa": "impostazioni", "ambito": ambito,
                                  "modifiche": [{"codice": "PDD_VALIDITA_ORE", "valore": 24}],
                                  "motivo": "Restituzioni più brevi per l'ente"})
    stato, err = await _chiama("DELETE", f"/bozze/{aperta['bozza_id']}", token=s.t_admin)
    assert stato == 403 and "conservatore" in err["detail"], err
    # la bozza di un altro amministratore dell'ente con una voce riservata: no
    collega = await _utente(db_url, s.tid, "ADMIN_TENANT")
    t_collega = _token(s.tid, collega, "ADMIN_TENANT")
    riservata = await _bozza(t_collega, {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                                         "modifiche": [{"codice": "IDEMPOTENZA_TRANSIZIONE_FINO_AL",
                                                        "valore": "2099-12-31"}],
                                         "motivo": "Richiesta di proroga della transizione"})
    stato, err = await _chiama("DELETE", f"/bozze/{riservata['bozza_id']}", token=s.t_admin)
    assert stato == 403 and err["dettagli"]["voci"] == ["IDEMPOTENZA_TRANSIZIONE_FINO_AL"], err
    # chi l'ha scritta sì; il conservatore sì
    assert (await _chiama("DELETE", f"/bozze/{riservata['bozza_id']}", token=t_collega))[0] == 204
    assert (await _chiama("DELETE", f"/bozze/{porte['bozza_id']}", token=t_rsc))[0] == 204
    [riga] = await sql(db_url, "SELECT stato FROM public.configurazione_bozze WHERE id = $1",
                       uuid.UUID(aperta["bozza_id"]))
    assert riga["stato"] == "APERTA"


async def test_w4_avviso_del_giorno_prima_anche_al_conservatore(db_url) -> None:
    """Gli utenti del conservatore hanno l'ente del conservatore (``users.tenant_id``
    obbligatorio): l'e-mail parte e si intesta a quell'ente. Un autore che non si
    può avvisare (token senza utente nel database) è segnalato da log e metrica."""
    from libs.shared.db import metriche

    metriche.azzera()
    rsc, t_rsc = await _conservatore(db_url)
    adesso = datetime.now(UTC)
    tra_due = (giorno_locale(adesso) + timedelta(days=2)).isoformat()
    b = await _bozza(t_rsc, {"risorsa": "impostazioni", "ambito": {"livello": "CONSERVATORE"},
                             "modifiche": [{"codice": "AUDIT_EXPORT_PERIODO_MINUTI",
                                            "valore": 10}],
                             "motivo": "Periodi dell'audit più brevi"})
    assert (await _applica(t_rsc, b, tra_due))[1]["stato"] == "PROGRAMMATA"
    giro = await programmate.giro(adesso + timedelta(days=1))
    assert b["bozza_id"] in giro.anteprime
    righe = await sql(db_url, "SELECT tenant_id, payload FROM public.outbox_eventi "
                              "WHERE topic = 'notification.send' AND chiave = $1",
                      f"{b['bozza_id']}:anteprima")
    assert len(righe) == 1
    [utente] = await sql(db_url, "SELECT email, tenant_id FROM public.users WHERE id = $1",
                         uuid.UUID(rsc))
    payload = json.loads(righe[0]["payload"])
    assert payload["destinatario"] == utente["email"]
    assert righe[0]["tenant_id"] == utente["tenant_id"]
    # autore senza utente (nessun indirizzo): nessuna e-mail, ma segnalato
    t_ignoto = _token(str(uuid.uuid4()), str(uuid.uuid4()), "SUPER_ADMIN")
    ignota = await _bozza(t_ignoto, {"risorsa": "impostazioni",
                                     "ambito": {"livello": "CONSERVATORE"},
                                     "modifiche": [{"codice": "AUDIT_EXPORT_PERIODO_MINUTI",
                                                    "valore": 20}],
                                     "motivo": "Periodi dell'audit di venti minuti"})
    assert (await _applica(t_ignoto, ignota, tra_due))[1]["stato"] == "PROGRAMMATA"
    await programmate.giro(adesso + timedelta(days=1))
    assert metriche.valore("verumdoc_programmate_avvisi_non_inviati", fase="anteprima") == 1


async def test_programmata_al_piu_entro_un_anno(db_url) -> None:
    s = await _scenario(db_url)
    b = await _bozza(s.t_admin, {"risorsa": "impostazioni", "ambito": {"livello": "ENTE"},
                                 "modifiche": [{"codice": "PDD_VALIDITA_ORE", "valore": 24}],
                                 "motivo": "Restituzioni più brevi fra molto tempo"})
    lontano = (giorno_locale(datetime.now(UTC)) + timedelta(days=400)).isoformat()
    stato, err = await _applica(s.t_admin, b, lontano)
    assert stato == 422 and err["codice"] == "CFG-423", err


async def test_w6_pagina_iniziale_solo_percorsi_interni(db_url) -> None:
    from tests.test_r07_parametri_console import _pref

    tid = await _ente(db_url)
    t = _token(tid, await _utente(db_url, tid, "OPERATORE"), "OPERATORE")
    for errata in ("//localhost", "/versamenti//x", "https://x.it", "\\\\x", "/a\\b",
                   "versamenti", "/x:y"):
        stato, _ = await _pref("PUT", t, {"versione": 0,
                                          "preferenze": {"pagina_iniziale": errata}})
        assert stato == 422, errata
    stato, r = await _pref("PUT", t, {"versione": 0,
                                      "preferenze": {"pagina_iniziale": "/conf/impostazioni"}})
    assert stato == 200, r
