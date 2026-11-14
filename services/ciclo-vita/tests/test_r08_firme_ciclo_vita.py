# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R08 (D01, D22; VD-C-120, VD-C-141) — firme del ciclo di vita VERIFICATE con
l'interfaccia dei verificatori (CA di prova generata a runtime, nessuna rete).

  - caricamento: un'autorizzazione firmata da una CA non attendibile, o alterata,
    si rifiuta subito (LC-422-FIRMA, motivo in parole);
  - lavoratore: riverifica sui byte riletti dal WORM; una firma accettata solo
    «da verificare» (verificatore giù al caricamento, o API compromessa) e non
    valida BLOCCA la pratica, nulla si cancella; verificatore giù nel lavoratore
    = errore temporaneo (nessun blocco, nessuna cancellazione);
  - CICLO_VITA_FIRME_LIVELLO = CON_REVOCHE per l'ente: senza stato di revoca, blocco;
  - NULLA_OSTA_FIRMA = BLOCCANTE: nulla osta non firmato rifiutato;
  - annullamento: richiesta firmata facoltativa/obbligatoria, che deve citare il
    versamento; il lavoratore la pretende e la riverifica prima del fatto.
"""
from __future__ import annotations

import base64
import json

import pytest

from libs.shared.firme import ErroreVerificatoreNonDisponibile, imposta_verificatore
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from tests.firme_prova import P7M, P7M_ESTRANEO, PKI, autorizzazione, richiesta_annullamento
from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, dipendenze, intestazioni, lavora, versioni
from tests.test_r05_revisione2_scarto_incompleto import _lavora_finche
from tests.test_r05_revisione4_scarto import _bloccata, _difetti, _eliminati
from tests.test_r05_revisione_scarto import CORPO, _ente_con_pda, _tr
from tests.test_r06_revisione_puntuali_autorizzazioni import (
    _ambiente_con_vittima,
    _dip,
    _nessun_fatto,
)

pytestmark = [pytest.mark.integration, richiede_pg]


class Giu:
    nome = "giu"
    formati = frozenset()

    async def verifica(self, *a, **k):
        raise ErroreVerificatoreNonDisponibile("verifica-firme non raggiungibile")


async def _parametro_ente(db_url, tenant, codice: str, valore: str) -> None:
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                      "motivo) VALUES ('ENTE', $1, $2, CAST($3 AS jsonb), 'prova R08')",
              tenant, codice, json.dumps(valore))


async def _fino_ad_autorizzata(c, sc, contenuto) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    """``contenuto``: byte, o funzione della pratica (R12a: documento che la cita)."""
    corpo = {"ente": str(sc["tenant"]), **CORPO}
    piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                          json=corpo)).json()
    r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                     json={**corpo, "impronta_piano": piano["impronta_piano"]})
    pid = r.json()["id"]
    assert (await _tr(c, pid, sc["admin"], "BOZZA", "PROPOSTA")).status_code == 200
    r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                     data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                     files={"file": ("a.p7m", contenuto(pid) if callable(contenuto)
                                     else contenuto, "application/pkcs7-mime")})
    return pid, r.json() | {"status": r.status_code}


async def test_autorizzazione_non_attendibile_o_alterata_rifiutata(db_url, s3_ciclo) -> None:
    i = P7M.index(b"Autorizzazione")
    alterata = P7M[:i] + b"X" + P7M[i + 1:]
    async with client_app() as c:
        for contenuto, parole in ((P7M_ESTRANEO, "Firmatario estraneo: firma indeterminata"),
                                  (alterata, "modificato dopo la firma")):
            sc = await _ente_con_pda(db_url, s3_ciclo, 1)
            _, risposta = await _fino_ad_autorizzata(c, sc, contenuto)
            assert risposta["status"] == 422 and risposta["codice"] == "LC-422-FIRMA"
            assert parole in risposta["detail"]


async def test_lavoratore_riverifica_dal_worm_e_blocca(db_url, s3_ciclo) -> None:
    """API compromessa (qui: verificatore giù al caricamento): l'autorizzazione
    di una CA estranea entra «da verificare»; il lavoratore la verifica e blocca."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    dip = dipendenze(db_url)
    async with client_app() as c:
        imposta_verificatore(Giu())  # type: ignore[arg-type]
        pid, aut = await _fino_ad_autorizzata(c, sc, P7M_ESTRANEO)
        assert aut["status"] == 201 and aut["firma"]["verificata"] is False
        imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
        assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          [aut["id"]])).status_code == 200
        assert (await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE",
                          "ESEGUIBILE")).status_code == 200
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        # verificatore giù anche nel lavoratore: errore temporaneo, nessun blocco
        imposta_verificatore(Giu())  # type: ignore[arg-type]
        try:
            await lavora(dip, giri=1)
        except ErroreVerificatoreNonDisponibile:
            pass
        assert (await _bloccata(db_url, pid))["errore"] is None or \
            (await _bloccata(db_url, pid))["errore"].get("codice") != "LC-409-AUTORIZZAZIONI"
        assert versioni(sc["s3"], prefisso) == iniziali
        imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
        riga = (await sql(db_url, "SELECT lavoro, ultimo_errore, lease_detentore, tentativi "
                                  "FROM public.pratiche WHERE id = $1::uuid", pid))[0]
        assert riga["lavoro"] == "ESEGUI" and "verifica-firme" in str(riga["ultimo_errore"])
        await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() - "
                          "interval '1 s' WHERE id = $1::uuid", pid)
        await _lavora_finche(db_url, dip, pid)
    bloccata = await _bloccata(db_url, pid)
    assert bloccata["lavoro"] is None, bloccata
    assert "ALLEGATO_FIRMA_NON_VALIDA" in _difetti(bloccata)
    assert "Firmatario estraneo: firma indeterminata" in _difetti(bloccata)
    assert versioni(sc["s3"], prefisso) == iniziali
    assert await _eliminati(db_url) == []


async def test_livello_con_revoche_per_ente_blocca_senza_stato_di_revoca(db_url,
                                                                         s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid, aut = await _fino_ad_autorizzata(c, sc, autorizzazione)
        assert aut["status"] == 201 and aut["firma"]["in_parole"] == "valida"
        assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          [aut["id"]])).status_code == 200
        assert (await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE",
                          "ESEGUIBILE")).status_code == 200
        # l'ente alza il livello DOPO il caricamento: vale quello attuale (lavoratore)
        await _parametro_ente(db_url, sc["tenant"], "CICLO_VITA_FIRME_LIVELLO", "CON_REVOCHE")
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    difetti = _difetti(await _bloccata(db_url, pid))
    assert "ALLEGATO_FIRMA_NON_VALIDA" in difetti and "revoca" in difetti
    assert await _eliminati(db_url) == []


async def test_nulla_osta_non_firmato_bloccante_per_ente(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    await sql(db_url, "UPDATE public.tenants SET natura = 'PA' WHERE id = $1", sc["tenant"])
    await _parametro_ente(db_url, sc["tenant"], "NULLA_OSTA_FIRMA", "BLOCCANTE")
    async with client_app() as c:
        pid, aut = await _fino_ad_autorizzata(c, sc, autorizzazione)
        assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          [aut["id"]])).status_code == 200
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                         data={"tipo": "NULLA_OSTA"},
                         files={"file": ("n.pdf", b"%PDF-1.7 nulla osta", "application/pdf")})
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-FIRMA"
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                         data={"tipo": "NULLA_OSTA"},
                         files={"file": ("n.pdf", PKI.pades("Nulla osta"), "application/pdf")})
        # R12a (VD-C-145): firmato ma senza l'identificativo della pratica → rifiuto
        assert r.status_code == 422 and "identificativo della pratica" in r.json()["detail"]
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                         data={"tipo": "NULLA_OSTA"},
                         files={"file": ("n.pdf", PKI.pades(f"Nulla osta, pratica {pid}"),
                                         "application/pdf")})
        assert r.status_code == 201 and r.json()["firma"]["in_parole"] == "valida"


async def test_annullamento_con_richiesta_firmata(db_url, s3_ciclo) -> None:
    a = await _ambiente_con_vittima(db_url, s3_ciclo)
    chi = intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"])
    await _parametro_ente(db_url, a["tenant"], "ANNULLAMENTO_RICHIESTA_FIRMATA", "OBBLIGATORIA")
    corpo = {"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"}

    def documento(contenuto: bytes) -> dict:
        return {"nome_file": "richiesta.p7m",
                "contenuto": base64.b64encode(contenuto).decode()}

    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi, json=corpo)
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-RICHIESTA-FIRMATA"
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi, json={
            **corpo, "documento_firmato": documento(richiesta_annullamento(str(a["pdv_v"])))})
        assert r.status_code == 422 and "identificativo del versamento" in r.json()["detail"]
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi, json={
            **corpo, "documento_firmato": documento(P7M_ESTRANEO)})
        assert r.status_code == 422 and r.json()["codice"] == "LC-422-FIRMA"
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi, json={
            **corpo, "documento_firmato": documento(richiesta_annullamento(str(a["pdv"])))})
        assert r.status_code == 201, r.text
        pratica = r.json()["id"]
    allegati = await sql(db_url, "SELECT tipo FROM public.pratiche_allegati WHERE pratica_id = "
                                 "$1::uuid", pratica)
    assert [x["tipo"] for x in allegati] == ["RICHIESTA_ANNULLAMENTO"]
    dip = _dip(db_url)
    await lavora(dip)
    assert ("annullamento", a["urn"], pratica) in dip.registro.foglie


async def test_annullamento_senza_richiesta_firmata_obbligatoria_bloccato(db_url,
                                                                         s3_ciclo) -> None:
    """Richiesta fatta quando la firma era facoltativa (o da un'API compromessa che
    salta il controllo): il lavoratore applica l'obbligo ATTUALE e blocca."""
    a = await _ambiente_con_vittima(db_url, s3_ciclo)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"})
        assert r.status_code == 201
        pratica = r.json()["id"]
    await _parametro_ente(db_url, a["tenant"], "ANNULLAMENTO_RICHIESTA_FIRMATA", "OBBLIGATORIA")
    await lavora(_dip(db_url))
    from tests.test_r06_revisione_puntuali_autorizzazioni import _bloccata as bloccata_p
    errore = await bloccata_p(db_url, pratica)
    assert any(d.startswith("RICHIESTA_NON_FIRMATA") for d in errore["difetti"])
    await _nessun_fatto(db_url, pratica)
