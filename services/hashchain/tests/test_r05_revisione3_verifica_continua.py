# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 3 di R05 — rilievo T4, superato da R09 (decisione D26, VD-C-135).

T4 escludeva dalla verifica continua i PdA con scarto INCOMPLETO
(``ELIMINAZIONE_INCOMPLETA``) per evitare falsi ``MANCANTE``. R09: se ne
verificano le versioni RIMASTE (ad esempio quelle sotto legal hold); un oggetto
mancante è atteso solo se lo scarto l'aveva inventariato prima di cancellarlo
(``pratiche_oggetti.dettagli.inventario``), altrimenti resta un'anomalia.

Resta da T4: uno scarto che INIZIA mentre il PdA è in verifica non produce
anomalie (lo stato si rilegge nella transazione che registra l'esito); il PdA
torna subito in coda e si verifica come incompleto.
"""
from __future__ import annotations

from typing import Any

import pytest
from app.verifica_continua import lavoratore as modulo_lavoratore
from app.verifica_continua.lavoratore import Lavoratore
from botocore.exceptions import ClientError

from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.test_r04_verifica_continua import (  # noqa: F401 — fixture s3 e api
    ClientS3,
    Orologio,
    _cfg,
    _eventi,
    _prepara,
    _registro,
    _tutto_in_scadenza,
    api,
    s3,
)

pytestmark = [pytest.mark.integration, richiede_pg]


class ClientS3Scartato(ClientS3):
    """Le chiavi in ``cancellate`` non esistono più (cancellate dallo scarto)."""

    def __init__(self, reale: Any) -> None:
        super().__init__(reale)
        self.cancellate: set[str] = set()

    def get_object(self, **kw: Any) -> Any:
        if kw["Key"] in self.cancellate:
            self.letture.append(kw["Key"])
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "cancellata"}},
                              "GetObject")
        return super().get_object(**kw)

    def head_object(self, **kw: Any) -> Any:
        if kw["Key"] in self.cancellate:
            raise ClientError({"Error": {"Code": "404", "Message": "cancellata"}}, "HeadObject")
        return self.reale.head_object(**kw)

    def __getattr__(self, nome: str) -> Any:
        return getattr(self.reale, nome)


async def _programmato(url: str, aip_id) -> bool:
    return bool(await sql(url, "SELECT 1 FROM public.verifiche_programmazione "
                               "WHERE aip_id = $1", aip_id))


def _trattenute(coppia_s3: Any, pda: dict, cancellate: set[str]) -> list[list[str]]:
    """Versioni rimaste nello storage (come le registra ciclo-vita, Z2)."""
    reale, bucket = coppia_s3
    chiavi = {pda["chiave_ipda"], *(d["s3_key"] for d in pda["documenti"])} - cancellate
    out: list[list[str]] = []
    for chiave in sorted(chiavi):
        for v in reale.list_object_versions(Bucket=bucket, Prefix=chiave).get("Versions", []):
            if v["Key"] == chiave:
                out.append([chiave, v["VersionId"]])
    return out


async def _scarto_incompleto(url: str, pda: dict, inventariate: list[str],
                             trattenute: list[list[str]] | None = None) -> None:
    """Pratica di scarto con l'oggetto NON_COMPLETATO e l'inventario registrato
    prima della cancellazione (come ciclo-vita); con ``trattenute`` anche le
    versioni rimaste (revisione R09, Z2)."""
    import json
    import uuid

    tenant = (await sql(url, "SELECT tenant_id FROM public.aip WHERE id = $1",
                        pda["aip_id"]))[0]["tenant_id"]
    pratica = uuid.uuid4()
    await sql(url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo) VALUES "
                   "($1, $2, 'SCARTO', 'ESEGUITA_CON_ECCEZIONI', 'Scarto di prova R09')",
              pratica, tenant)
    await sql(url, "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, esito, "
                   "dettagli) VALUES ($1, $2, $3, 'NON_COMPLETATO', CAST($4 AS jsonb))",
              pratica, tenant, pda["aip_id"],
              json.dumps({"inventario": [[k, "v1"] for k in inventariate]
                          + [list(t) for t in trattenute or []],
                          **({"versioni_trattenute": trattenute}
                             if trattenute is not None else {})}))
    await sql(url, "UPDATE public.aip SET stato = 'ELIMINAZIONE_INCOMPLETA' WHERE id = $1",
              pda["aip_id"])


async def test_r09_scarto_incompleto_si_verificano_le_versioni_rimaste(db_url, s3):  # noqa: F811
    orologio = Orologio()
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    client = ClientS3Scartato(s3[0])
    lav = Lavoratore(_cfg(), client=client, detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    # Lo scarto cancella IPdA e un file, poi si ferma (legal hold sul resto)
    cancellate = {pda["chiave_ipda"], pda["documenti"][0]["s3_key"]}
    client.cancellate |= cancellate
    await _scarto_incompleto(db_url, pda, sorted(cancellate),
                             _trattenute(s3, pda, cancellate))
    assert await lav.giro() == 1
    righe = await _registro(db_url, pda["aip_id"])
    assert righe and {r["esito"] for r in righe} == {"OK"}           # nessun falso MANCANTE
    attese = [r for r in righe if r["chiave"] in cancellate]
    assert len(attese) == 2 and all("cancellato dallo scarto" in r["motivo"] for r in attese)
    rimaste = [r for r in righe if r["chiave"] not in cancellate]
    assert rimaste and all(r["impronta_attesa"] == r["impronta_trovata"] for r in rimaste)
    assert await _eventi(db_url) == []
    assert await _programmato(db_url, pda["aip_id"])                   # resta in rotazione
    # la riconciliazione lo tiene (stato verificabile)
    from app.verifica_continua import repository

    await repository.riconcilia(lav.cfg, orologio())
    assert await _programmato(db_url, pda["aip_id"])


async def test_r09_oggetto_mancante_fuori_inventario_resta_anomalia(db_url, s3):  # noqa: F811
    orologio = Orologio()
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    client = ClientS3Scartato(s3[0])
    lav = Lavoratore(_cfg(), client=client, detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    client.cancellate |= {pda["chiave_ipda"], pda["documenti"][0]["s3_key"]}
    await _scarto_incompleto(db_url, pda, [pda["chiave_ipda"]],   # il file NON inventariato
                             _trattenute(s3, pda, client.cancellate))
    assert await lav.giro() == 1
    mancanti = [r for r in await _registro(db_url, pda["aip_id"]) if r["esito"] == "MANCANTE"]
    assert [r["chiave"] for r in mancanti] == [pda["documenti"][0]["s3_key"]]
    assert len(await _eventi(db_url)) == 1


async def test_z2_chiave_trattenuta_sparita_e_anomalia(db_url, s3):  # noqa: F811
    """Revisione R09 (Z2): l'inventario contiene ANCHE le versioni che lo scarto
    non ha potuto cancellare (legal hold). Se una chiave trattenuta sparisce è
    MANCANTE (allarme), non «cancellata dallo scarto»."""
    orologio = Orologio()
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    client = ClientS3Scartato(s3[0])
    lav = Lavoratore(_cfg(), client=client, detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    cancellato = pda["documenti"][0]["s3_key"]
    trattenute = _trattenute(s3, pda, {cancellato})
    await _scarto_incompleto(db_url, pda, [cancellato], trattenute)
    # poi sparisce l'IPdA trattenuto (incidente, cancellazione con bypass)
    client.cancellate |= {cancellato, pda["chiave_ipda"]}
    assert await lav.giro() == 1
    righe = {r["chiave"]: r for r in await _registro(db_url, pda["aip_id"])}
    assert righe[cancellato]["esito"] == "OK"
    assert righe[pda["chiave_ipda"]]["esito"] == "MANCANTE"
    assert "trattenute" in righe[pda["chiave_ipda"]]["motivo"]
    assert len(await _eventi(db_url)) == 1


async def test_z2_versione_trattenuta_sparita_e_anomalia(db_url, s3):  # noqa: F811
    """Z2: la chiave c'è ancora ma la VERSIONE trattenuta (version id registrato
    dallo scarto) non più: MANCANTE con il motivo, allarme."""
    orologio = Orologio()
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    client = ClientS3Scartato(s3[0])
    lav = Lavoratore(_cfg(), client=client, detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    cancellato = pda["documenti"][0]["s3_key"]
    trattenute = _trattenute(s3, pda, {cancellato})
    trattenute = [[k, "versione-sparita" if k == pda["chiave_ipda"] else v]
                  for k, v in trattenute]
    await _scarto_incompleto(db_url, pda, [cancellato], trattenute)
    client.cancellate.add(cancellato)
    assert await lav.giro() == 1
    righe = {r["chiave"]: r for r in await _registro(db_url, pda["aip_id"])}
    assert righe[pda["chiave_ipda"]]["esito"] == "MANCANTE"
    assert "versione-sparita" in righe[pda["chiave_ipda"]]["motivo"]
    assert len(await _eventi(db_url)) == 1


def test_z2_prova_della_revisione_inventario_con_versione_trattenuta() -> None:
    """La prova della revisione (prova_d26.py) come test di regressione."""
    import uuid

    from app.verifica_continua import esiti, incompleti
    from app.verifica_continua.verificatore import EsitoOggetto, EsitoPda

    t, a = uuid.uuid4(), uuid.uuid4()
    doc, man = f"{t}/{a}/documento.pdf", f"{t}/{a}/manifest.json"
    scarto = incompleti.da_dettagli([{"inventario": [[doc, "1"], [man, "2"]],
                                      "versioni_trattenute": [[man, "2"]]}])
    e = EsitoPda(pda=None, oggetti=[EsitoOggetto("FILE", doc, esiti.MANCANTE),  # type: ignore[arg-type]
                                    EsitoOggetto("PDA", man, esiti.MANCANTE)])
    incompleti.adatta(e, scarto)
    assert [o.esito for o in e.oggetti] == [esiti.OK, esiti.MANCANTE]
    # oggetti chiusi prima della correzione: solo le chiavi registrate come eliminate
    vecchio = incompleti.da_dettagli([{"inventario": [[doc, "1"], [man, "2"]],
                                       "chiavi_eliminate": [doc], "chiavi_rimaste": [man]}])
    assert vecchio.trattenute is None and vecchio.attese_mancanti() == {doc}


async def test_scarto_iniziato_durante_la_verifica_non_e_un_anomalia(db_url, s3, monkeypatch):  # noqa: F811
    """Il PdA è CONSERVATO quando il lavoratore lo prende; lo scarto lo porta a
    ELIMINAZIONE_INCOMPLETA e cancella oggetti mentre la verifica legge: l'esito
    (MANCANTE) non si registra; il PdA torna in coda (R09) e si verificherà come
    incompleto."""
    orologio = Orologio()
    _, _, (pda,) = await _prepara(db_url, s3, orologio)
    client = ClientS3Scartato(s3[0])
    lav = Lavoratore(_cfg(), client=client, detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    originale = modulo_lavoratore.verifica_pda

    async def scarto_a_meta(p, **kw):
        client.cancellate.add(pda["chiave_manifest"])
        await sql(db_url, "UPDATE public.aip SET stato = 'ELIMINAZIONE_INCOMPLETA' "
                          "WHERE id = $1", pda["aip_id"])
        return await originale(p, **kw)

    monkeypatch.setattr(modulo_lavoratore, "verifica_pda", scarto_a_meta)
    assert await lav.giro() == 1
    assert await _registro(db_url, pda["aip_id"]) == []
    assert await _eventi(db_url) == []
    riga = (await sql(db_url, "SELECT prossima_verifica, lease_id FROM "
                              "public.verifiche_programmazione WHERE aip_id = $1",
                      pda["aip_id"]))[0]
    assert riga["lease_id"] is None and riga["prossima_verifica"] <= orologio()


async def test_r09_ciclo_per_ente_e_classe(db_url, s3):  # noqa: F811
    """D05: VERIFICA_CICLO_GIORNI del motore (conservatore, ente, classe): vince il
    livello più specifico; senza righe vale il valore del processo."""
    from datetime import timedelta

    orologio = Orologio()
    tenant, classe, (pda,) = await _prepara(db_url, s3, orologio)
    for livello, ente, cl, valore in (("CONSERVATORE", None, None, 200), ("ENTE", tenant, None, 90),
                                      ("CLASSE", tenant, classe, 30)):
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, classe_id, "
                          "codice, valore, motivo) VALUES ($1, $2, $3, 'VERIFICA_CICLO_GIORNI', "
                          "CAST($4 AS jsonb), 'prova D05')", livello, ente, cl, str(valore))
    lav = Lavoratore(_cfg(), client=ClientS3(s3[0]), detentore="r1", orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    assert await lav.giro() == 1
    prossima = (await sql(db_url, "SELECT prossima_verifica FROM public.verifiche_programmazione "
                                  "WHERE aip_id = $1", pda["aip_id"]))[0]["prossima_verifica"]
    assert prossima == orologio() + timedelta(days=30)
    from app.verifica_continua import cicli

    c = cicli.da_righe([], 365)
    assert c.giorni(tenant, classe) == 365
    c = cicli.da_righe([{"livello": "ENTE", "tenant_id": str(tenant), "classe_id": None,
                         "valore": "90"}, {"livello": "CONSERVATORE", "tenant_id": None,
                                           "classe_id": None, "valore": "400"}], 365)
    assert c.giorni(tenant, classe) == 90 and c.giorni("altro", None) == 365  # 400: scartato


async def test_r09_campione_dopo_incidente(db_url, s3):  # noqa: F811
    """D05: un'anomalia d'integrità avvia una verifica a campione (una per pausa)."""
    orologio = Orologio()
    _, _, pdas = await _prepara(db_url, s3, orologio, n=3)
    client = ClientS3Scartato(s3[0])
    lav = Lavoratore(_cfg(incidente_campione=True), client=client, detentore="r1",
                     orologio=orologio)
    await _tutto_in_scadenza(lav, orologio)
    client.cancellate.add(pdas[0]["chiave_manifest"])   # incidente: oggetto sparito
    assert await lav.giro() == 3
    richieste = await sql(db_url, "SELECT ruolo, oggetti_totali, gia_in_verifica FROM "
                                  "public.verifiche_richieste WHERE ruolo = 'SISTEMA:INCIDENTE'")
    assert len(richieste) == 1
    assert richieste[0]["oggetti_totali"] + richieste[0]["gia_in_verifica"] == 3
    from app.verifica_continua import repository

    assert await repository.campione_dopo_incidente(
        orologio(), n=10, pausa=__import__("datetime").timedelta(hours=6), motivo="altro") is None


async def test_r09_campione_a_richiesta_solo_conservatore(db_url, s3, api):  # noqa: F811
    from tests.test_r04_verifica_continua import intestazioni

    orologio = Orologio()
    tenant, _, _ = await _prepara(db_url, s3, orologio, n=3)
    from app.verifica_continua import repository

    await repository.semina(_cfg(), orologio())
    async with api as c:
        corpo = {"motivo": "Guasto di un disco del cluster di storage", "pda": 2}
        r = await c.post("/api/v1/hashchain/verifiche/campione", json=corpo,
                         headers=intestazioni("ADMIN_TENANT", tenant_id=str(tenant)))
        assert r.status_code == 403
        r = await c.post("/api/v1/hashchain/verifiche/campione", json=corpo,
                         headers=intestazioni("RSC_CONSERVATORE"))
        assert r.status_code == 202, r.text
        assert r.json()["oggetti_totali"] + r.json()["gia_in_verifica"] == 2
