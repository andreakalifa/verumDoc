# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 2 di R05 — rilievi S3 e S6: scarto avviato che non riesce a
completare la cancellazione di un PdA; versioni nate dopo l'elenco.

Prima della correzione una versione diventata non cancellabile a metà PdA
(legal hold posto durante lo scarto) dava ERRORE a ogni tentativo, per sempre:
pratica ESEGUIBILE con il lavoro, PdA parzialmente distrutto in ELIMINAZIONE,
nessun verbale né tombstone, nessuna transizione ammessa (prova del revisore
``revisione2/ciclo/test_rev2_scarto_bloccato_per_sempre.py``).

Ora (PostgreSQL vero, S3 moto con Object Lock, registro finto del contratto Q3):
  - il PdA si chiude NON_COMPLETATO (causa LEGAL_HOLD) e passa a
    ELIMINAZIONE_INCOMPLETA (metrica/allarme); la pratica termina
    ESEGUITA_CON_ECCEZIONI con il verbale che elenca cosa è stato cancellato e
    cosa no, e il tombstone dei soli PdA cancellati per intero; nessuna RESPINTA;
  - quando il blocco cessa il conservatore riprende (ESEGUITA_CON_ECCEZIONI →
    ESEGUITA): si riprovano solo i PdA incompleti, verbale integrativo,
    tombstone, pratica ESEGUITA;
  - un errore dello storage che non è un blocco riconosciuto si ritenta al più
    ``SCARTO_TENTATIVI_MAX_PDA`` volte, poi l'oggetto si chiude (mai un ciclo
    infinito);
  - un blocco visto prima di cancellare qualunque versione non distrugge nulla:
    PdA di nuovo CONSERVATO, oggetto BLOCCATO;
  - S6: una versione scritta sotto il prefisso del PdA dopo l'elenco non resta
    nel WORM di un PdA dichiarato ELIMINATO.
"""
from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from tests.infra_reale import richiede_pg, sql
from tests.supporto import PASSATO, client_app, dipendenze, lavora, versioni
from tests.test_r05_revisione_scarto import _ente_con_pda, _fino_a_eseguibile, _tr

pytestmark = [pytest.mark.integration, richiede_pg]


async def _stato(db_url, pid) -> dict:
    return dict((await sql(db_url, "SELECT stato, lavoro, tentativi FROM public.pratiche "
                                   "WHERE id = $1::uuid", str(pid)))[0])


async def _oggetti(db_url, pid) -> dict:
    righe = await sql(db_url, "SELECT aip_id, esito, motivo, foglia_id, dettagli "
                              "FROM public.pratiche_oggetti WHERE pratica_id = $1::uuid", str(pid))
    return {r["aip_id"]: r for r in righe}


async def _stato_pda(db_url, aip) -> str:
    return (await sql(db_url, "SELECT stato::text AS s FROM public.aip WHERE id = $1",
                      aip))[0]["s"]


async def _verbali(db_url, s3, pid) -> list[tuple[str, str]]:
    righe = await sql(db_url, "SELECT nome_file, bucket, chiave FROM public.pratiche_allegati "
                              "WHERE pratica_id = $1::uuid AND tipo = 'VERBALE_SCARTO' "
                              "ORDER BY created_at", str(pid))
    return [(r["nome_file"], s3.get_object(Bucket=r["bucket"], Key=r["chiave"])["Body"]
             .read().decode()) for r in righe]


async def _lavora_finche(db_url, dip, pid, *, giri: int = 12) -> list[str]:
    """Giri del lavoratore anticipando il prossimo tentativo (come il tempo che passa)."""
    esiti: list[str] = []
    for _ in range(giri):
        esiti += await lavora(dip, giri=3)
        await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() - "
                          "interval '1 second' WHERE id = $1::uuid", str(pid))
        if (await _stato(db_url, pid))["lavoro"] is None:
            break
    return esiti


def _legal_hold_durante_la_cancellazione(monkeypatch, s3, chiave_hold: str) -> None:
    from app.infrastructure import storage

    originale = storage.elimina_versione
    fatto = {"n": 0}

    prefisso = chiave_hold.rsplit("/", 1)[0] + "/"

    def elimina_e_hold(client, bucket, oggetto):
        # alla prima cancellazione DI QUESTO PdA (i PdA si eseguono in ordine di id)
        if fatto["n"] == 0 and oggetto.chiave.startswith(prefisso):
            # ordine dell'autorità giudiziaria mentre lo scarto cancella il PdA
            s3.put_object_legal_hold(Bucket=bucket, Key=chiave_hold, LegalHold={"Status": "ON"})
            fatto["n"] += 1
        return originale(client, bucket, oggetto)

    monkeypatch.setattr(storage, "elimina_versione", elimina_e_hold)


async def test_legal_hold_a_meta_pda_conclude_con_eccezioni_e_si_riprende(
        db_url, s3_ciclo, monkeypatch) -> None:
    from app.application import metriche
    from app.config import settings
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 2)
    monkeypatch.setattr(settings, "lavoratore_lotto", 1)
    (aip1, urn1), (aip2, urn2) = sc["pda"]
    chiave_hold = f"{sc['tenant']}/{aip1}/manifest.json"
    originale = storage.elimina_versione
    _legal_hold_durante_la_cancellazione(monkeypatch, sc["s3"], chiave_hold)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        esiti = await _lavora_finche(db_url, dip, pid)
        dettaglio = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pid}", headers=sc["rsc"])).json()
        respinta = await _tr(c, pid, sc["rsc"], "ESEGUITA_CON_ECCEZIONI", "RESPINTA")

    # Conclusa (mai in esecuzione per sempre), senza errori ripetuti
    assert "ERRORE" not in esiti and len(esiti) <= 3, esiti
    stato = await _stato(db_url, pid)
    assert (stato["stato"], stato["lavoro"]) == ("ESEGUITA_CON_ECCEZIONI", None)
    assert dettaglio["conteggi"] == {"ELIMINATO": 1, "NON_COMPLETATO": 1}
    assert respinta.status_code == 409   # nessuna RESPINTA: si riprende, non si annulla
    oggetti = await _oggetti(db_url, pid)
    assert (oggetti[aip1]["esito"], oggetti[aip1]["motivo"]) == ("NON_COMPLETATO", "LEGAL_HOLD")
    assert oggetti[aip1]["foglia_id"] is None
    assert oggetti[aip2]["esito"] == "ELIMINATO" and oggetti[aip2]["foglia_id"]
    assert await _stato_pda(db_url, aip1) == "ELIMINAZIONE_INCOMPLETA"
    assert versioni(sc["s3"], f"{sc['tenant']}/{aip1}/") == [chiave_hold]
    assert versioni(sc["s3"], f"{sc['tenant']}/{aip2}/") == []
    # Tombstone SOLO del PdA cancellato per intero
    assert {(t, p) for t, p, _ in dip.registro.foglie} == {("scarto", urn2)}
    # Verbale: esito con eccezioni, cosa è stato cancellato e cosa resta
    [(nome, verbale)] = await _verbali(db_url, sc["s3"], pid)
    assert nome == f"verbale_scarto_{pid}.xml"
    assert "<Esito>ESEGUITO_CON_ECCEZIONI</Esito>" in verbale
    assert 'esito="NON_COMPLETATO" motivo="LEGAL_HOLD" versioniEliminate="2" ' \
           'versioniRimaste="1"' in verbale
    assert f'<Rimasta chiave="{chiave_hold}"/>' in verbale
    assert f'<Eliminata chiave="{sc["tenant"]}/{aip1}/IndiceAIP_SInCRO.xml"/>' in verbale
    # Allarme: la metrica conta il PdA incompleto
    metriche.registra(__import__("prometheus_client").CollectorRegistry())
    assert (await metriche.aggiorna())["incompleti"] == 1
    voci = await sql(db_url, "SELECT 1 FROM public.audit_global "
                             "WHERE evento = 'ciclo_vita.scarto.pda_eliminazione_incompleta'")
    assert len(voci) == 1

    # ── Il blocco cessa: il conservatore riprende i soli PdA incompleti ──
    monkeypatch.setattr(storage, "elimina_versione", originale)
    sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip", Key=chiave_hold,
                                   LegalHold={"Status": "OFF"})
    async with client_app() as c:
        produttore = await _tr(c, pid, sc["admin"], "ESEGUITA_CON_ECCEZIONI", "ESEGUITA")
        assert produttore.status_code == 403            # la ripresa è del conservatore
        ripresa = await _tr(c, pid, sc["rsc"], "ESEGUITA_CON_ECCEZIONI", "ESEGUITA")
        assert ripresa.status_code == 202, ripresa.text
        await _lavora_finche(db_url, dip, pid)
    stato = await _stato(db_url, pid)
    assert (stato["stato"], stato["lavoro"]) == ("ESEGUITA", None)
    oggetti = await _oggetti(db_url, pid)
    assert oggetti[aip1]["esito"] == "ELIMINATO" and oggetti[aip1]["foglia_id"]
    assert await _stato_pda(db_url, aip1) == "ELIMINAZIONE"
    assert versioni(sc["s3"], f"{sc['tenant']}/{aip1}/") == []
    assert {(t, p) for t, p, _ in dip.registro.foglie} == {("scarto", urn1), ("scarto", urn2)}
    verbali = await _verbali(db_url, sc["s3"], pid)
    assert [n for n, _ in verbali] == [f"verbale_scarto_{pid}.xml",
                                       f"verbale_scarto_{pid}_ripresa_2.xml"]
    assert "<Giro>2</Giro>" in verbali[1][1] and "<Esito>ESEGUITO</Esito>" in verbali[1][1]
    # il tombstone del PdA ripreso cita il verbale integrativo
    import hashlib

    sha_ripresa = hashlib.sha256(verbali[1][1].encode()).hexdigest()
    assert dip.registro.foglie[("scarto", urn1, str(pid))]["impronta"] == sha_ripresa
    assert (await metriche.aggiorna())["incompleti"] == 0


async def test_errore_persistente_dello_storage_chiude_dopo_il_limite(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Un rifiuto dello storage che non è un blocco riconosciuto (es. permesso
    IAM mancante su una chiave) si ritenta al più SCARTO_TENTATIVI_MAX_PDA volte."""
    from app.config import settings
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    monkeypatch.setattr(settings, "scarto_tentativi_max_pda", 3)
    (aip, _), = sc["pda"]
    originale = storage.elimina_versione
    chiamate = {"n": 0}

    def nega_dopo_la_prima(client, bucket, oggetto):
        chiamate["n"] += 1
        if chiamate["n"] > 1:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "negato"}},
                              "DeleteObject")
        return originale(client, bucket, oggetto)

    monkeypatch.setattr(storage, "elimina_versione", nega_dopo_la_prima)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        esiti = await _lavora_finche(db_url, dip, pid, giri=10)
    assert esiti.count("ERRORE") + esiti.count("RINVIATO") == 2, esiti   # 3° tentativo chiude
    stato = await _stato(db_url, pid)
    assert (stato["stato"], stato["lavoro"]) == ("ESEGUITA_CON_ECCEZIONI", None)
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("NON_COMPLETATO", "ERRORE_PERSISTENTE")
    assert await _stato_pda(db_url, aip) == "ELIMINAZIONE_INCOMPLETA"
    assert len(versioni(sc["s3"], f"{sc['tenant']}/{aip}/")) == 2


async def test_blocco_prima_di_cancellare_non_distrugge_nulla(db_url, s3_ciclo,
                                                              monkeypatch) -> None:
    """Il legal hold arriva fra l'elenco con il PdA bloccato e la prima
    cancellazione: nessuna versione si cancella, il PdA torna CONSERVATO e
    l'oggetto è BLOCCATO (nessun PdA incompleto)."""
    from app.application import scarto

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    originale = scarto._cancella_versioni

    async def hold_poi_cancella(pratica, oggetto, adesso, **kw):
        sc["s3"].put_object_legal_hold(Bucket="verumdoc-aip",
                                       Key=f"{sc['tenant']}/{aip}/manifest.json",
                                       LegalHold={"Status": "ON"})
        return await originale(pratica, oggetto, adesso, **kw)

    monkeypatch.setattr(scarto, "_cancella_versioni", hold_poi_cancella)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert (oggetto["esito"], oggetto["motivo"]) == ("BLOCCATO", "LEGAL_HOLD")
    assert await _stato_pda(db_url, aip) == "CONSERVATO"
    assert len(versioni(sc["s3"], f"{sc['tenant']}/{aip}/")) == 3
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"
    assert dip.registro.foglie == {}


async def test_versione_scritta_dopo_l_elenco_non_resta_nel_pda_eliminato(
        db_url, s3_ciclo, monkeypatch) -> None:
    """S6: un oggetto scritto sotto il prefisso del PdA dopo l'elenco (es. un
    ritentativo tardivo dell'aip-builder) non resta nel WORM mentre il PdA
    risulta ELIMINATO con il suo tombstone: si rielenca dopo le cancellazioni."""
    from app.infrastructure import storage

    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    originale = storage.elimina_versione
    tardiva = f"{sc['tenant']}/{aip}/RdV_Definitivo_v2.xml"
    scritta = {"fatto": False}

    def cancella_e_scrivi(client, bucket, oggetto):
        if not scritta["fatto"]:
            scritta["fatto"] = True
            sc["s3"].put_object(Bucket=bucket, Key=tardiva, Body=b"<rdv/>",
                                ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=PASSATO)
        return originale(client, bucket, oggetto)

    monkeypatch.setattr(storage, "elimina_versione", cancella_e_scrivi)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid = await _fino_a_eseguibile(c, sc)
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    assert scritta["fatto"]
    assert versioni(sc["s3"], f"{sc['tenant']}/{aip}/") == []
    oggetto = (await _oggetti(db_url, pid))[aip]
    assert oggetto["esito"] == "ELIMINATO"
    assert (await _stato(db_url, pid))["stato"] == "ESEGUITA"
