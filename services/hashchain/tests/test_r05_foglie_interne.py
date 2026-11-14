# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05, contratto Q3: API interna del registro per il ciclo di vita (scarto,
annullamento, rettifica) su PostgreSQL vero e S3 (moto con Object Lock).

  - ``POST /interno/v1/registro/foglie`` (revisione 2 di R05, S2: fuori da
    ``/api/``; le richieste dei test passano da ``fatto_ciclo_vita``, che scrive
    nel database il fatto che giustifica la foglia): solo token di servizio con scope
    ``registro:aggiungi`` (403 senza, 401 con token utente), 202 ``{foglia_id}``,
    idempotente per (tipo, pda, pratica), 409 con dati diversi, 404 PdA ignoto,
    422 riferimento non valido;
  - la foglia entra nello stesso ordine totale dei PdA (coda), riceve indice,
    nodi, checkpoint e controfirma del testimone come le foglie dei PdA;
  - ``GET …/interno/foglie/{id}`` riporta indice e checkpoint quando ci sono;
  - la prova d'inclusione del documento riporta le foglie del ciclo di vita e il
    verificatore pubblico dice «DOCUMENTO SCARTATO», mai «VERIFICATA».
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.auth.jwt_handler import chiavi_jwt
from app.config import settings
from app.main import app
from app.registro import archivio, checkpoint, sequenziatore
from app.services import block_sealer
from httpx import ASGITransport, AsyncClient

from libs.catena import albero
from libs.catena.foglie import analizza_foglia_registro
from libs.catena.rfc6962 import hash_foglia
from libs.shared.db import get_async_session
from libs.shared.security.jwt_rs256 import firma_token_servizio
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.supporto_registro import (
    FintoTestimone,
    PdaDiProva,
    crea_pda,
    fatto_ciclo_vita,
    identita,
)
from tests.token_di_prova import intestazioni

pytestmark = [pytest.mark.integration, richiede_pg]

ORA = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
IO = "detentore-q3"
VERBALE = hashlib.sha256(b"verbale di scarto").hexdigest()
URL = "/interno/v1/registro/foglie"
#: Revisione 2 di R05 (S2): database del test e pratiche create per etichetta
#: ((urn, tipo, etichetta) → id della pratica con il fatto nel database)
_DB: dict[str, str] = {}
PRATICHE: dict[tuple[str, str, str], tuple[str, dict]] = {}


@pytest.fixture(autouse=True)
def _ricorda_db(request):
    PRATICHE.clear()
    if "db_url" in request.fixturenames:
        _DB["url"] = request.getfixturevalue("db_url")
    yield
    _DB.clear()


@pytest.fixture
def s3(s3_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", 30)
    block_sealer._client.cache_clear()
    yield client_s3(s3_server)
    block_sealer._client.cache_clear()


def servizio(*scopes: str, pubblico: str | None = None) -> dict[str, str]:
    tok = firma_token_servizio("ciclo-vita", chiave_privata_pem=chiavi_jwt().privata_pem,
                               emittente=settings.jwt_issuer, durata_secondi=300,
                               pubblico=pubblico or settings.catena_pubblico_servizio,
                               scopes=list(scopes))
    return {"Authorization": f"Bearer {tok}"}


def aggiungi() -> dict[str, str]:
    """Token di ciclo-vita emesso al momento della richiesta: dura 5 minuti e la
    suite intera ne impiega di più (un token creato all'importazione scadrebbe)."""
    return servizio("registro:aggiungi")


async def _invia(corpo: dict, intest: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(URL, json=corpo,
                            headers=intest if intest is not None else aggiungi())


async def _post(corpo: dict, intest: dict | None = None):
    """POST con il FATTO nel database (S2): ``riferimento.pratica`` è un'etichetta
    del test, sostituita dall'id di una pratica che giustifica la foglia (creata
    alla prima richiesta con quell'etichetta, con gli stati di PdA e PdV del
    momento della richiesta). Una richiesta ripetuta riusa la stessa pratica."""
    rif = corpo.get("riferimento")
    tipo = corpo.get("tipo")
    if not _DB or not isinstance(rif, dict) or "pratica" not in rif \
            or tipo not in ("scarto", "annullamento", "rettifica"):
        return await _invia(corpo, intest)
    chiave = (str(corpo.get("pda")), str(tipo), str(rif["pratica"]))
    if chiave in PRATICHE:
        pratica, extra = PRATICHE[chiave]
        return await _invia({**corpo, "riferimento": {**rif, "pratica": pratica, **extra}},
                            intest)
    righe = await sql(_DB["url"], "SELECT id, pdv_id, tenant_id FROM public.aip WHERE urn = $1",
                      corpo.get("pda"))
    if not righe:
        return await _invia(corpo, intest)
    pda = PdaDiProva(righe[0]["pdv_id"], righe[0]["id"], chiave[0], righe[0]["tenant_id"],
                     [], None)
    # R06 (VD-C-119): il PdV di rettifica del fatto è il PdV del PdA
    extra = {"pdv_rettifica": str(pda.pdv_id)} if tipo == "rettifica" else {}
    async with fatto_ciclo_vita(_DB["url"], pda, str(tipo),
                                str(corpo.get("impronta_documento_pratica") or "")) as pratica:
        PRATICHE[chiave] = (pratica, extra)
        return await _invia({**corpo, "riferimento": {**rif, "pratica": pratica, **extra}},
                            intest)


async def _get(percorso: str, intest: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(percorso, headers=intest if intest is not None else aggiungi())


async def _lease(adesso: datetime = ORA) -> None:
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, adesso, adesso + timedelta(hours=1))


async def _sequenzia(adesso: datetime = ORA) -> sequenziatore.EsitoLotto:
    await _lease(adesso)
    return await sequenziatore.aggiungi_lotto(detentore=IO, adesso=adesso, limite=1000)


def _corpo(pda: str, tipo: str = "scarto", pratica: str = "PS-1", **altro) -> dict:
    return {"tipo": tipo, "pda": pda, "riferimento": {"pratica": pratica, **altro},
            "impronta_documento_pratica": VERBALE}


async def test_q3_autenticazione_e_validazione(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe)
    # nessun token, token utente (anche del conservatore), scope sbagliato, altro pubblico
    assert (await _post(_corpo(pda.urn), {})).status_code == 401
    utente = await _post(_corpo(pda.urn), intestazioni("SUPER_ADMIN"))
    assert utente.status_code == 401
    senza_scope = await _post(_corpo(pda.urn), servizio("registro:leggi"))
    assert senza_scope.status_code == 403 and senza_scope.json()["codice"] == "CAT-403"
    altro = await _post(_corpo(pda.urn), servizio("registro:aggiungi",
                                                  pubblico="verumdoc-signature"))
    assert altro.status_code == 401
    # validazione: tipo, riferimento senza pratica, impronta non esadecimale, campi in più
    for corpo in (_corpo(pda.urn, tipo="trasferimento"),
                  {**_corpo(pda.urn), "riferimento": {"verbale": "x"}},
                  {**_corpo(pda.urn), "impronta_documento_pratica": "zz"},
                  {**_corpo(pda.urn), "riferimento": {"pratica": "P", "x": 1.5}},
                  {**_corpo(pda.urn), "extra": 1}):
        r = await _post(corpo)
        assert r.status_code == 422 and r.json()["codice"] == "VAL-422", corpo
    ignoto = await _post(_corpo("urn:nessuno"))
    assert ignoto.status_code == 404 and ignoto.json()["codice"] == "CAT-404"
    assert await sql(db_url, "SELECT 1 FROM public.catena_foglie_interne") == []


async def test_q3_idempotenza_e_conflitto(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe)
    prima = await _post(_corpo(pda.urn, verbale="registro/verbali/1.pdf"))
    assert prima.status_code == 202
    foglia_id = prima.json()["foglia_id"]
    ripetuta = await _post(_corpo(pda.urn, verbale="registro/verbali/1.pdf"))
    assert ripetuta.status_code == 202 and ripetuta.json() == {"foglia_id": foglia_id}
    diversa = await _post(_corpo(pda.urn, verbale="registro/verbali/2.pdf"))
    assert diversa.status_code == 409 and diversa.json()["codice"] == "IDEM-409"
    assert diversa.json()["dettagli"]["foglia_id"] == foglia_id
    # stessa pratica ma tipo diverso, o altra pratica: foglie diverse
    annullamento = await _post(_corpo(pda.urn, tipo="annullamento"))
    altra = await _post(_corpo(pda.urn, pratica="PS-2"))
    assert len({foglia_id, annullamento.json()["foglia_id"], altra.json()["foglia_id"]}) == 3
    righe = await sql(db_url, "SELECT tipo, dati, hash FROM public.catena_foglie_interne "
                              "ORDER BY creata_il")
    assert [r["tipo"] for r in righe] == ["SCARTO", "ANNULLAMENTO", "SCARTO"]
    for r in righe:
        assert bytes(r["hash"]) == hash_foglia(r["dati"].encode())
        assert analizza_foglia_registro(r["dati"]).ciclo_vita
    assert len(await sql(db_url, "SELECT 1 FROM public.catena_coda "
                                 "WHERE foglia_interna_id IS NOT NULL")) == 3
    voci = await sql(db_url, "SELECT dettagli FROM public.audit_global "
                             "WHERE evento = 'catena.foglia_interna_richiesta'")
    assert len(voci) == 3 and json.loads(voci[0]["dettagli"])["richiesta_da"] == "ciclo-vita"


async def test_q3_sequenziamento_checkpoint_e_controfirma(db_url, s3):
    """Ordine totale con i PdA, foglia con PdA NULL nel registro, nodi, stato via
    GET fino alla controfirma del testimone: lo stesso trattamento delle foglie dei PdA."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda1 = await crea_pda(db_url, tenant, classe)
    foglia_id = (await _post(_corpo(pda1.urn))).json()["foglia_id"]
    pda2 = await crea_pda(db_url, tenant, classe)
    stato = (await _get(f"{URL}/{foglia_id}")).json()
    assert stato["stato"] == "IN_CODA" and stato["indice"] is None

    esito = await _sequenzia()
    assert (esito.aggiunte, esito.dimensione) == (3, 3)
    righe = await sql(db_url, "SELECT indice, aip_id, tenant_id, tipo, dati, hash "
                              "FROM public.catena_registro ORDER BY indice")
    assert [r["tipo"] for r in righe] == ["PDA", "SCARTO", "PDA"]
    assert [r["aip_id"] for r in righe] == [pda1.aip_id, None, pda2.aip_id]
    assert righe[1]["tenant_id"] == tenant
    assert json.loads(righe[1]["dati"])["id"] == foglia_id
    nodi = {(r["livello"], r["indice"]): bytes(r["hash"]) for r in
            await sql(db_url, "SELECT livello, indice, hash FROM public.catena_nodi")}
    assert nodi == albero.tutti_i_nodi([bytes(r["hash"]) for r in righe])
    assert await sql(db_url, "SELECT 1 FROM public.catena_coda") == []
    stato = (await _get(f"{URL}/{foglia_id}")).json()
    assert (stato["stato"], stato["indice"], stato["checkpoint"]) == ("NEL_REGISTRO", 1, None)

    testimone = FintoTestimone(identita().firmatario.verificatore)
    ident = identita(testimone=True)
    testimone.verificatore_registro = ident.firmatario.verificatore
    await _lease()
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=ORA, identita=ident, segmento=10)
    stato = (await _get(f"{URL}/{foglia_id}")).json()
    assert stato["stato"] == "IN_CHECKPOINT" and stato["checkpoint"]["dimensione"] == 3
    assert stato["checkpoint"]["stato_testimone"] == "IN_ATTESA"
    assert await checkpoint.invia_al_testimone(testimone.client(), detentore=IO, adesso=ORA) \
        == "CONTROFIRMATO"
    stato = (await _get(f"{URL}/{foglia_id}")).json()
    assert stato["stato"] == "CONTROFIRMATA"
    assert stato["checkpoint_controfirmato"]["dimensione"] == 3
    # Il segmento esportato nel WORM contiene la foglia di scarto come le altre
    segmento = s3.get_object(Bucket=settings.s3_bucket_aip,
                             Key="registro/foglie/000000000000-000000000003.jsonl")
    assert segmento["Body"].read().decode().splitlines()[1] == righe[1]["dati"]
    # Il PdA scartato NON è stato toccato dal sequenziatore della foglia di scarto
    assert (await sql(db_url, "SELECT stato::text AS s FROM public.aip WHERE id = $1",
                      pda1.aip_id))[0]["s"] == "IN_CATENA"


async def test_q3_foglia_gia_nel_registro_tolta_dalla_coda_senza_riscriverla(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe)
    foglia_id = uuid.UUID((await _post(_corpo(pda.urn))).json()["foglia_id"])
    await _sequenzia()
    # una riga in coda rimasta (es. dopo una ricostruzione dal WORM) non duplica la foglia
    await sql(db_url, "INSERT INTO public.catena_coda (foglia_interna_id) VALUES ($1)",
              foglia_id)
    esito = await _sequenzia()
    assert (esito.aggiunte, esito.gia_presenti, esito.dimensione) == (0, 1, 2)
    assert await sql(db_url, "SELECT 1 FROM public.catena_coda") == []


async def test_q3_foglia_interna_immutabile(db_url, s3):
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe)
    foglia_id = uuid.UUID((await _post(_corpo(pda.urn))).json()["foglia_id"])
    import asyncpg

    with pytest.raises(asyncpg.RaiseError, match="non modificabile"):
        await sql(db_url, "UPDATE public.catena_foglie_interne SET dati = 'x' WHERE id = $1",
                  foglia_id)
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await sql(db_url, "DELETE FROM public.catena_foglie_interne WHERE id = $1", foglia_id)
    await _sequenzia()
    with pytest.raises(asyncpg.RaiseError, match="già nel registro"):
        await sql(db_url, "UPDATE public.catena_foglie_interne SET indice = 99 WHERE id = $1",
                  foglia_id)


async def test_q3_hash_corrotto_ferma_il_lotto(db_url, s3):
    """Una foglia interna con hash diverso dai dati non entra mai nel registro."""
    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe, in_coda=False)
    foglia = uuid.uuid4()
    await sql(db_url, "INSERT INTO public.catena_foglie_interne (id, tipo, chiave, aip_id, "
                      "tenant_id, pda, dati, hash, richiesta_da) VALUES ($1, 'SCARTO', 'k', $2, "
                      "$3, $4, '{}', $5, 'prova')", foglia, pda.aip_id, tenant, pda.urn,
              b"\x00" * 32)
    await sql(db_url, "INSERT INTO public.catena_coda (foglia_interna_id) VALUES ($1)", foglia)
    with pytest.raises(sequenziatore.FogliaInternaCorrotta):
        await _sequenzia()
    assert await sql(db_url, "SELECT 1 FROM public.catena_registro") == []


def _verificatore():
    radice = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(radice / "tools" / "verificatore"))
    import verifica_ricevuta

    return verifica_ricevuta


async def test_q3_prova_e_verificatore_documento_scartato(db_url, s3, monkeypatch):
    """End-to-end: PdA conservato, poi scarto via API; la prova d'inclusione
    riporta la foglia di scarto e il verificatore pubblico NON la dichiara verificata.
    Revisione 2 di R05 (S8): la prova porta l'attestazione firmata dal registro
    (stessa chiave dei checkpoint) e una prova da cui si toglie la foglia di
    scarto non risulta mai «conservata»."""
    from app.registro.checkpoint import Identita
    from pydantic import SecretStr

    from libs.catena import attestazione as attestazione_lib
    from libs.catena import nota
    from tests.supporto_registro import ORIGINE

    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    privata, _ = nota.genera_chiave("verumdoc-registro")
    monkeypatch.setattr(settings, "catena_chiave_privata", SecretStr(privata))
    ident = Identita(nota.analizza_firmatario(privata), ORIGINE, False)
    await _lease()
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=ORA, identita=ident, segmento=10)
    utente = intestazioni("OPERATORE", tenant_id=str(tenant))
    percorso = f"/api/v1/hashchain/prova/inclusione?pda={pda.urn}&documento=doc-0"
    prima = (await _get(percorso, utente)).json()
    assert prima["ciclo_vita"] == []

    risposta = await _post(_corpo(pda.urn, pratica="PS-2026-0042"))
    adesso = ORA + timedelta(minutes=5)
    await _sequenzia(adesso)
    await _lease(adesso)
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=adesso, identita=ident, segmento=10)
    dopo = (await _get(percorso, utente)).json()
    assert [v["tipo"] for v in dopo["ciclo_vita"]] == ["scarto"]
    assert json.loads(dopo["ciclo_vita"][0]["foglia_dati"])["id"] == risposta.json()["foglia_id"]
    # Con un checkpoint precedente allo scarto la prova non riporta lo scarto
    vecchia = (await _get(percorso + "&dimensione=1", utente)).json()
    assert vecchia["ciclo_vita"] == []

    v = _verificatore()
    chiave = ident.firmatario.verificatore.testo
    rapporto = v.Rapporto()
    v.verifica_prova(rapporto, dopo, chiave_registro=chiave, chiave_testimone=None,
                     impronta_documento=hashlib.sha256(pda.documenti[0][2]).hexdigest(),
                     origine="verumdoc.it/registro/v1/test")
    assert not rapporto.riuscito and rapporto.codice_uscita == 2, rapporto.testo()
    assert "ESITO: DOCUMENTO SCARTATO" in rapporto.testo()
    assert f"con pratica {PRATICHE[(pda.urn, 'scarto', 'PS-2026-0042')][0]}" in rapporto.testo()
    rapporto = v.Rapporto()
    v.verifica_prova(rapporto, vecchia, chiave_registro=chiave, chiave_testimone=None,
                     origine="verumdoc.it/registro/v1/test")
    # Revisione 3 di R05 (T2): la prova chiesta DOPO lo scarto a un checkpoint
    # precedente non ha l'attestazione (rilasciata solo per l'ultimo): mai
    # «conservato» (prima: codice 0, «conservato fino al checkpoint 1»)
    assert dopo["attestazione_ciclo_vita"] and vecchia["attestazione_ciclo_vita"] is None
    assert "solo per l'ultimo checkpoint" in vecchia["attestazione_assente"]
    assert vecchia["ultimo_checkpoint"] == dopo["prova_registro"]["dimensione_albero"]
    assert rapporto.codice_uscita == 1, rapporto.testo()
    assert "assenza di scarto non dimostrabile" in rapporto.testo()
    assert "conservato:" not in rapporto.testo()
    # T2: la prova scaricata PRIMA dello scarto (attestata allora, all'ultimo
    # checkpoint di quel momento) dice sempre a quale stato si riferisce e, con il
    # checkpoint corrente, non vale come stato attuale
    att_prima = attestazione_lib.analizza(prima["attestazione_ciclo_vita"])
    assert att_prima.stato == "CONSERVATO" and att_prima.dimensione == 1
    rapporto = v.Rapporto()
    v.verifica_prova(rapporto, prima, chiave_registro=chiave, chiave_testimone=None,
                     origine="verumdoc.it/registro/v1/test")
    assert rapporto.codice_uscita == 0, rapporto.testo()
    assert f"stato al checkpoint 1 del {att_prima.rilasciata}" in rapporto.testo()
    corrente = nota.analizza(dopo["checkpoint"])
    rapporto = v.Rapporto()
    v.verifica_prova(rapporto, prima, chiave_registro=chiave, chiave_testimone=None,
                     origine="verumdoc.it/registro/v1/test", checkpoint_corrente=corrente)
    assert rapporto.codice_uscita == 1, rapporto.testo()
    assert f"il registro ha ora {corrente.dimensione} foglie" in rapporto.testo()
    # S8: chi presenta la prova toglie la foglia di scarto e i campi del conteggio
    tolta = {k: x for k, x in dopo.items() if k not in ("ciclo_vita_totale",
                                                         "ciclo_vita_completo")}
    tolta["ciclo_vita"] = []
    rapporto = v.Rapporto()
    v.verifica_prova(rapporto, tolta, chiave_registro=chiave, chiave_testimone=None,
                     origine="verumdoc.it/registro/v1/test")
    assert rapporto.codice_uscita == 1, rapporto.testo()
    assert "incompleto (0 di 1 foglie)" in rapporto.testo()
    del tolta["attestazione_ciclo_vita"]
    rapporto = v.Rapporto()
    v.verifica_prova(rapporto, tolta, chiave_registro=chiave, chiave_testimone=None,
                     origine="verumdoc.it/registro/v1/test")
    assert rapporto.codice_uscita == 1 and "assenza di scarto non dimostrabile" in \
        rapporto.testo()


async def test_q3_token_emesso_dalla_riga_di_comando(db_url, s3, monkeypatch, capsys):
    """Il token di ciclo-vita emesso con ``emetti_token_servizio --scope
    registro:aggiungi`` aggiunge foglie; quello di sola lettura no (e viceversa)."""
    from libs.shared.security.emetti_token_servizio import main

    monkeypatch.setenv("JWT_PRIVATE_KEY", chiavi_jwt().privata_pem)
    monkeypatch.setenv("JWT_PUBLIC_KEY", chiavi_jwt().pubblica_pem)
    monkeypatch.setenv("ENV", "test")

    def emetti(scope: str) -> dict[str, str]:
        capsys.readouterr()  # i log delle richieste precedenti vanno anch'essi su stdout
        assert main(["--servizio", "ciclo-vita", "--pubblico", "verumdoc-registro",
                     "--scope", scope, "--giorni", "1"]) == 0
        return {"Authorization": f"Bearer {capsys.readouterr().out.strip().splitlines()[-1]}"}

    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe)
    aggiunta = emetti("registro:aggiungi")
    assert (await _post(_corpo(pda.urn), aggiunta)).status_code == 202
    lettura = emetti("registro:leggi")
    assert (await _post(_corpo(pda.urn, pratica="X"), lettura)).status_code == 403
    assert (await _get("/api/v1/hashchain/registro/foglie", aggiunta)).status_code == 403


async def test_r05_revisione3_pda_scartato_in_parte_mai_conservato(db_url, s3, monkeypatch):  # noqa: F811
    """Revisione 3 di R05 (T3): PdA ELIMINAZIONE_INCOMPLETA (cancellazione
    iniziata, nessun tombstone): la prova dichiara ``stato_pda`` SCARTO_PARZIALE,
    l'attestazione firmata lo impegna e il verificatore dà «DOCUMENTO SCARTATO IN
    PARTE» (prima: «conservato … nessuno scarto», codice 0)."""
    from app.registro.checkpoint import Identita
    from pydantic import SecretStr

    from libs.catena import attestazione as attestazione_lib
    from libs.catena import nota
    from tests.supporto_registro import ORIGINE

    tenant, classe = await crea_tenant_e_classe(db_url)
    pda = await crea_pda(db_url, tenant, classe)
    await _sequenzia()
    privata, _ = nota.genera_chiave("verumdoc-registro")
    monkeypatch.setattr(settings, "catena_chiave_privata", SecretStr(privata))
    ident = Identita(nota.analizza_firmatario(privata), ORIGINE, False)
    await _lease()
    await checkpoint.emetti_checkpoint(detentore=IO, adesso=ORA, identita=ident, segmento=10)
    await sql(db_url, "UPDATE public.aip SET stato = 'ELIMINAZIONE_INCOMPLETA' WHERE id = $1",
              pda.aip_id)
    utente = intestazioni("OPERATORE", tenant_id=str(tenant))
    prova = (await _get(f"/api/v1/hashchain/prova/inclusione?pda={pda.urn}&documento=doc-0",
                        utente)).json()
    assert prova["stato_pda"] == "SCARTO_PARZIALE" and prova["ciclo_vita"] == []
    att = attestazione_lib.analizza(prova["attestazione_ciclo_vita"])
    assert att.stato == "SCARTO_PARZIALE"
    v = _verificatore()
    rapporto = v.Rapporto()
    v.verifica_prova(rapporto, prova, chiave_registro=ident.firmatario.verificatore.testo,
                     chiave_testimone=None,
                     impronta_documento=hashlib.sha256(pda.documenti[0][2]).hexdigest(),
                     origine="verumdoc.it/registro/v1/test")
    assert rapporto.codice_uscita == 2, rapporto.testo()
    assert "ESITO: DOCUMENTO SCARTATO IN PARTE" in rapporto.testo()
    assert "conservato:" not in rapporto.testo()
