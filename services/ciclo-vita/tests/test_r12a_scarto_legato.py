# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-145) — autorizzazione allo scarto e nulla osta LEGATI alla pratica.

  - il documento deve riportare l'identificativo della pratica nel contenuto
    firmato da firmatari autorizzati (stesse regole X12/Y2 della richiesta di
    annullamento: PAdES con l'ultima firma che copre tutto il file e tutte le
    firme ammesse, CAdES con il contenuto incapsulato);
  - caricamento: un documento autentico di un'altra pratica si rifiuta (422);
  - lavoratore (controllo di sicurezza, API compromessa): riverifica sui byte del
    WORM e BLOCCA; rifiuta anche un documento con la stessa impronta già allegato a
    un'altra pratica dell'ente;
  - transizione: gli allegati elencati dalla migrazione 20261104_051 (pratiche
    aperte prima di R12a) passano con un avviso, ma mai se riusati.
"""
from __future__ import annotations

import pytest
from app.application import firme
from app.domain import stati as st

from libs.shared.firme import imposta_verificatore
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko
from tests.firme_prova import CF_PROVA, P7M, PKI, autorizzazione, autorizzazione_pdf
from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, dipendenze, versioni
from tests.test_r05_revisione2_scarto_incompleto import _lavora_finche
from tests.test_r05_revisione4_scarto import _bloccata, _difetti, _eliminati
from tests.test_r05_revisione_scarto import _ente_con_pda, _tr
from tests.test_r08_firme_ciclo_vita import Giu, _fino_ad_autorizzata
from tests.test_r08_revisione_firme import _aggiornamento_incrementale

PRATICA = "6f1c2a8e-0000-4000-8000-0000000000a1"
ALTRA = "6f1c2a8e-0000-4000-8000-0000000000a2"
AMMESSI = frozenset({CF_PROVA})
AVVISO = firme.ParametriFirme(firme.LivelloVerifica.CRITTOGRAFICA, False, False,
                              AMMESSI, AMMESSI)
BLOCCANTE = firme.ParametriFirme(firme.LivelloVerifica.CRITTOGRAFICA, True, False,
                                 AMMESSI, AMMESSI)


async def _esito(contenuto: bytes, nome: str = "a.p7m") -> dict:
    return await firme.verifica_documento(contenuto, nome, firme.LivelloVerifica.CRITTOGRAFICA)


# ── unità ────────────────────────────────────────────────────────────

async def test_riferimento_alla_pratica_nel_contenuto_firmato() -> None:
    giusta, altra = autorizzazione(PRATICA), autorizzazione(ALTRA)
    for tipo in (st.ALLEGATO_AUTORIZZAZIONE, st.ALLEGATO_NULLA_OSTA):
        assert firme.problemi_riferimento_pratica(
            tipo, giusta, await _esito(giusta), PRATICA, BLOCCANTE) == ([], None)
        problemi, _ = firme.problemi_riferimento_pratica(
            tipo, altra, await _esito(altra), PRATICA, BLOCCANTE)
        assert problemi and PRATICA in problemi[0]
    # documento generico (senza alcuna pratica): rifiutato
    problemi, _ = firme.problemi_riferimento_pratica(
        st.ALLEGATO_AUTORIZZAZIONE, P7M, await _esito(P7M), PRATICA, AVVISO)
    assert problemi
    # transizione: avviso, non rifiuto
    problemi, avviso = firme.problemi_riferimento_pratica(
        st.ALLEGATO_AUTORIZZAZIONE, altra, await _esito(altra), PRATICA, AVVISO,
        transizione=True)
    assert problemi == [] and "prima dell'obbligo" in (avviso or "")
    # altri tipi: nessun controllo
    assert firme.problemi_riferimento_pratica(
        st.ALLEGATO_RICHIESTA_ANNULLAMENTO, altra, None, PRATICA, BLOCCANTE) == ([], None)


async def test_nulla_osta_secondo_nulla_osta_firma() -> None:
    """AVVISO: un nulla osta senza firma (o di un firmatario non in elenco) è già
    accettato con avviso e non ha contenuto firmato da controllare; firmato da un
    firmatario autorizzato deve citare la pratica anche con AVVISO."""
    chiaro = b"%PDF-1.7 nulla osta " + PRATICA.encode()
    assert firme.problemi_riferimento_pratica(
        st.ALLEGATO_NULLA_OSTA, chiaro, await _esito(chiaro, "n.pdf"), PRATICA, AVVISO) == \
        ([], None)
    altra = autorizzazione(ALTRA)
    assert firme.problemi_riferimento_pratica(
        st.ALLEGATO_NULLA_OSTA, altra, await _esito(altra), PRATICA, AVVISO)[0]


async def test_pades_riferimento_aggiunto_dopo_la_firma_non_vale() -> None:
    """Y2/X12: la pratica aggiunta con un aggiornamento incrementale dopo la firma (o
    accodata in chiaro) non è nel contenuto firmato."""
    pdf = autorizzazione_pdf(ALTRA)
    for alterato in (_aggiornamento_incrementale(pdf, PRATICA),
                     pdf + f"\n% {PRATICA}\n".encode()):
        assert firme.problemi_riferimento_pratica(
            st.ALLEGATO_AUTORIZZAZIONE, alterato, {"formato": "PAdES", "firme": [{}]},
            PRATICA, BLOCCANTE)[0]
    giusto = autorizzazione_pdf(PRATICA)
    assert firme.problemi_riferimento_pratica(
        st.ALLEGATO_AUTORIZZAZIONE, giusto, await _esito(giusto, "a.pdf"), PRATICA,
        BLOCCANTE) == ([], None)


# ── PostgreSQL e storage veri ────────────────────────────────────────

@pytest.mark.integration
@richiede_pg
async def test_caricamento_documento_di_un_altra_pratica_rifiutato(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    async with client_app() as c:
        _, risposta = await _fino_ad_autorizzata(c, sc, autorizzazione(ALTRA))
    assert risposta["status"] == 422 and risposta["codice"] == "LC-422-FIRMA"
    assert "identificativo della pratica" in risposta["detail"]


async def _fino_a_eseguita(c, sc, contenuto, *, verificatore_giu: bool = False):  # type: ignore[no-untyped-def]
    if verificatore_giu:  # API compromessa: firma «da verificare» al caricamento
        imposta_verificatore(Giu())  # type: ignore[arg-type]
    pid, aut = await _fino_ad_autorizzata(c, sc, contenuto)
    imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
    assert aut["status"] == 201, aut
    assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                      [aut["id"]])).status_code == 200
    assert (await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE",
                      "ESEGUIBILE")).status_code == 200
    assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
    return pid, aut


@pytest.mark.integration
@richiede_pg
async def test_lavoratore_blocca_documento_di_un_altra_pratica(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    (aip, _), = sc["pda"]
    prefisso = f"{sc['tenant']}/{aip}/"
    iniziali = versioni(sc["s3"], prefisso)
    async with client_app() as c:
        pid, _ = await _fino_a_eseguita(c, sc, autorizzazione(ALTRA), verificatore_giu=True)
        await _lavora_finche(db_url, dipendenze(db_url), pid)
    bloccata = await _bloccata(db_url, pid)
    assert bloccata["lavoro"] is None, bloccata
    assert "identificativo della pratica" in _difetti(bloccata)
    assert versioni(sc["s3"], prefisso) == iniziali
    assert await _eliminati(db_url) == []


async def _in_transizione(db_url, aut, pid) -> None:  # type: ignore[no-untyped-def]
    """Come la migrazione 20261104_051 (superutente: nessun ruolo dei servizi la scrive)."""
    await sql(db_url, "INSERT INTO public.ciclo_vita_transizione_r12 (allegato_id, "
                      "pratica_id, tipo) VALUES ($1::uuid, $2::uuid, $3)",
              aut["id"], pid, "AUTORIZZAZIONE_PRODUTTORE")


VECCHIO = autorizzazione("documento generico senza pratica")


@pytest.mark.integration
@richiede_pg
async def test_transizione_senza_riferimento_si_esegue_con_avviso(db_url, s3_ciclo) -> None:
    """Pratica aperta prima di R12a (allegato nell'elenco della migrazione): senza il
    riferimento alla pratica lo scarto si esegue, con l'avviso nella verifica."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    async with client_app() as c:
        imposta_verificatore(Giu())  # type: ignore[arg-type]
        pid, aut = await _fino_ad_autorizzata(c, sc, VECCHIO)
        imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
        await _in_transizione(db_url, aut, pid)
        assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          [aut["id"]])).status_code == 200
        await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")
        await _lavora_finche(db_url, dipendenze(db_url), pid)
    stato = (await sql(db_url, "SELECT stato FROM public.pratiche WHERE id = $1::uuid",
                       pid))[0]["stato"]
    assert stato == "ESEGUITA", await _bloccata(db_url, pid)
    verifica = await sql(db_url, "SELECT dettagli::text AS d FROM public.esiti_esecuzione "
                                 "WHERE pratica_id = $1::uuid AND esito = "
                                 "'AUTORIZZAZIONI_VERIFICATE'", pid)
    assert verifica and "prima dell'obbligo" in verifica[0]["d"]


@pytest.mark.integration
@richiede_pg
async def test_riuso_dello_stesso_documento_bloccato_anche_in_transizione(db_url,
                                                                          s3_ciclo) -> None:
    """Lo stesso documento (stessa impronta) allegato a una pratica respinta e poi a
    una nuova pratica dell'ente: il lavoratore blocca (ALLEGATO_GIA_USATO)."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    async with client_app() as c:
        imposta_verificatore(Giu())  # type: ignore[arg-type]
        pid1, aut1 = await _fino_ad_autorizzata(c, sc, VECCHIO)
        imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
        assert aut1["status"] == 201
        assert (await _tr(c, pid1, sc["admin"], "PROPOSTA", "RESPINTA")).status_code == 200
        imposta_verificatore(Giu())  # type: ignore[arg-type]
        pid2, aut2 = await _fino_ad_autorizzata(c, sc, VECCHIO)
        imposta_verificatore(VerificatorePyHanko([PKI.ca_asn1]))
        await _in_transizione(db_url, aut2, pid2)
        assert (await _tr(c, pid2, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          [aut2["id"]])).status_code == 200
        await _tr(c, pid2, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        await _tr(c, pid2, sc["rsc"], "ESEGUIBILE", "ESEGUITA")
        await _lavora_finche(db_url, dipendenze(db_url), pid2)
    bloccata = await _bloccata(db_url, pid2)
    assert bloccata["lavoro"] is None and "ALLEGATO_GIA_USATO" in _difetti(bloccata), bloccata
    assert await _eliminati(db_url) == []


@pytest.mark.integration
@richiede_pg
async def test_elenco_di_transizione_non_scrivibile_dai_servizi(db_url) -> None:
    """Il confine della transizione lo scrive SOLO la migrazione: il lavoratore legge,
    l'API non vede nulla."""
    righe = await sql(db_url, """
        SELECT r.rolname, has_table_privilege(r.rolname, 'public.ciclo_vita_transizione_r12',
                                              'SELECT') AS legge,
               has_table_privilege(r.rolname, 'public.ciclo_vita_transizione_r12',
                                   'INSERT,UPDATE,DELETE') AS scrive
        FROM pg_roles r WHERE r.rolname IN ('verumdoc_ciclo_vita_lavoratore',
                                            'verumdoc_applicazione')""")
    per_ruolo = {r["rolname"]: (r["legge"], r["scrive"]) for r in righe}
    assert per_ruolo["verumdoc_ciclo_vita_lavoratore"] == (True, False)
    assert per_ruolo["verumdoc_applicazione"] == (False, False)


@pytest.mark.integration
@richiede_pg
async def test_revisione_copia_successiva_non_blocca_l_originale(db_url, s3_ciclo) -> None:
    """Revisione R12a: chi ricarica l'autorizzazione di X in un'altra pratica (DOPO) non
    blocca X; come ALTRO la copia è rifiutata al caricamento."""
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    async with client_app() as c:
        documento: dict[str, bytes] = {}

        def una_volta(p: str) -> bytes:   # la firma ECDSA cambia a ogni chiamata
            documento["byte"] = autorizzazione(p)
            return documento["byte"]

        pid, aut = await _fino_ad_autorizzata(c, sc, una_volta)
        assert aut["status"] == 201
        # come ALTRO nella stessa pratica (o in un'altra): rifiutato
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati", headers=sc["admin"],
                         data={"tipo": "ALTRO"},
                         files={"file": ("copia.p7m", documento["byte"],
                                         "application/pkcs7-mime")})
        assert r.status_code == 422 and "autorizzativo" in r.json()["detail"]
        # copia caricata DOPO in un'altra pratica di scarto dell'ente (API compromessa)
        altra = (await sql(db_url, "INSERT INTO public.pratiche (tenant_id, tipo, stato, motivo) "
                                   "VALUES ($1, 'SCARTO', 'BOZZA', 'Pratica dell''attaccante') "
                                   "RETURNING id", sc["tenant"]))[0]["id"]
        await sql(db_url, "INSERT INTO public.pratiche_allegati (id, pratica_id, tenant_id, tipo, "
                          "nome_file, content_type, dimensione, sha256, bucket, chiave, "
                          "version_id, conserva_fino_a, firma, caricato_da, created_at) SELECT "
                          "gen_random_uuid(), $1, tenant_id, tipo, nome_file, content_type, "
                          "dimensione, sha256, bucket, chiave || '-copia', version_id, "
                          "conserva_fino_a, firma, caricato_da, now() + interval '1 minute' "
                          "FROM public.pratiche_allegati WHERE id = $2::uuid", altra, aut["id"])
        # e una copia PRECEDENTE ma come ALTRO (non autorizzativa): non conta come riuso
        await sql(db_url, "INSERT INTO public.pratiche_allegati (id, pratica_id, tenant_id, tipo, "
                          "nome_file, content_type, dimensione, sha256, bucket, chiave, "
                          "version_id, conserva_fino_a, firma, caricato_da, created_at) SELECT "
                          "gen_random_uuid(), $1, tenant_id, 'ALTRO', nome_file, content_type, "
                          "dimensione, sha256, bucket, chiave || '-altro', version_id, "
                          "conserva_fino_a, firma, caricato_da, created_at - interval '1 hour' "
                          "FROM public.pratiche_allegati WHERE id = $2::uuid", altra, aut["id"])
        assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          [aut["id"]])).status_code == 200
        await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")
        await _lavora_finche(db_url, dipendenze(db_url), pid)
    stato = (await sql(db_url, "SELECT stato FROM public.pratiche WHERE id = $1::uuid",
                       pid))[0]["stato"]
    assert stato == "ESEGUITA", await _bloccata(db_url, pid)
