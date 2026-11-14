# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test delle sessioni di caricamento (contratto C1) e del versamento per riferimento.

R03 b1: apertura con Idempotency-Key, parti parallele e fuori ordine,
idempotenza delle parti e conflitto (409 UPL-004) senza toccare lo storage,
completamento con parti mancanti (UPL-005), verifica FUORI dalla richiesta con
impronte di tutti gli algoritmi e formato dalla testa, impronta dichiarata
errata (UPL-006), annullamento e pulizia, scadenza, isolamento fra enti (404),
versamento JSON per riferimento con sessione usata una sola volta (UPL-008,
anche in parallelo), canale dal token (contratto C2).

Le funzioni pure girano senza infrastruttura; gli altri test su PostgreSQL
migrato (VD_TEST_DATABASE_URL) e S3 moto.
"""
# ruff: noqa: F811 — le fixture importate tornano come parametri dei test
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.main import app
from app.services import caricamenti as car
from app.services.versamento_service import Canale, IdentitaVersante

from libs.shared.db.ambito import ambito_ente
from libs.shared.errori import ErroreApi
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.supporto_canali import (  # noqa: F401 — fixture
    MB,
    PDF,
    UTENTE,
    chiavi_staging,
    client_http,
    indice,
    pdf_di,
    s3,
    sha256,
    utente_di,
)

PARTE = 5 * MB


@pytest.fixture(autouse=True)
def parti_da_5_mb(monkeypatch):
    """Parti della dimensione minima S3: file di prova piccoli con più parti."""
    monkeypatch.setattr(settings, "caricamenti_dimensione_parte_mb", 5)


# ── Funzioni pure ───────────────────────────────────────────────────

def test_numero_parti_e_dimensione_attesa():
    """L'ultima parte porta il resto; un file piccolo è una parte sola."""
    assert car.numero_parti(1, PARTE) == 1
    assert car.numero_parti(PARTE, PARTE) == 1
    assert car.numero_parti(PARTE + 1, PARTE) == 2
    assert car.dimensione_attesa(1, 11 * MB, PARTE) == PARTE
    assert car.dimensione_attesa(3, 11 * MB, PARTE) == MB
    assert car.dimensione_attesa(4, 11 * MB, PARTE) == 0


def test_controlla_apertura_oltre_il_limite_upl001(monkeypatch):
    monkeypatch.setattr(settings, "caricamenti_max_dimensione_mb", 10)
    richiesta = car.AperturaCaricamento(nome_file="a.pdf", dimensione=10 * MB + 1)
    with pytest.raises(ErroreApi) as exc:
        car.controlla_apertura(richiesta, PARTE)
    assert exc.value.codice == "UPL-001" and exc.value.status == 413


@pytest.mark.parametrize("algoritmo,valore", [("MD5", "a" * 64), ("SHA-256", "a" * 65),
                                              ("SHA-256", "z" * 64), ("SHA-512", "a" * 64)])
def test_controlla_apertura_impronta_non_valida_upl400(algoritmo, valore):
    richiesta = car.AperturaCaricamento(
        nome_file="a.pdf", dimensione=10,
        impronta=car.ImprontaDichiarata(algoritmo=algoritmo, valore=valore))
    with pytest.raises(ErroreApi) as exc:
        car.controlla_apertura(richiesta, PARTE)
    assert exc.value.codice == "UPL-400"


def test_controlla_apertura_nome_con_percorso_rifiutato():
    with pytest.raises(ErroreApi) as exc:
        car.controlla_apertura(car.AperturaCaricamento(nome_file="../../etc/x.pdf",
                                                       dimensione=10), PARTE)
    assert exc.value.codice.startswith("SEC-")


@pytest.mark.parametrize("valore", ["sha256=abc", "md5=" + "a" * 64, "a" * 64])
def test_impronta_parte_dichiarata_malformata_upl400(valore):
    with pytest.raises(ErroreApi) as exc:
        car.impronta_parte_dichiarata(valore)
    assert exc.value.codice == "UPL-400"


def test_impronta_parte_dichiarata_valida_minuscola():
    assert car.impronta_parte_dichiarata("sha256=" + "AB" * 32) == "ab" * 32
    assert car.impronta_parte_dichiarata(None) is None


async def _blocchi(*parti: bytes):
    for p in parti:
        yield p


async def test_leggi_parte_si_ferma_oltre_la_dimensione_attesa():
    """Oltre i byte attesi la lettura si ferma (UPL-002) senza consumare il resto."""
    letti: list[bytes] = []

    async def sorgente():
        for p in (b"a" * 6, b"b" * 6, b"c" * 6):
            letti.append(p)
            yield p

    with pytest.raises(ErroreApi) as exc:
        await car.leggi_parte(sorgente(), 10)
    assert exc.value.codice == "UPL-002" and len(letti) == 2


async def test_leggi_parte_corta_upl002():
    with pytest.raises(ErroreApi) as exc:
        await car.leggi_parte(_blocchi(b"abc"), 10)
    assert exc.value.codice == "UPL-002" and exc.value.dettagli["ricevuti"] == 3


def test_stato_effettivo_scaduto_solo_se_non_usata():
    adesso = datetime(2026, 10, 21, tzinfo=UTC)
    s = car.Sessione(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), utente_id=uuid.uuid4(), canale="REST",
        chiave_idempotenza=None, impronta_richiesta="x", nome_file="a.pdf", dimensione=1,
        mime_dichiarato=None, impronta_dichiarata=None, dimensione_parte=PARTE, numero_parti=1,
        stato="COMPLETO", bucket="b", chiave_oggetto="k", upload_id=None, impronte=None,
        testa=None, formato_rilevato=None, errore=None, scade_il=adesso - timedelta(seconds=1),
        detentore=None, lease_scade_il=None, tentativi_verifica=0, pdv_id=None,
        consumato_il=None, completato_il=None, storage_eliminato_il=None, creato_il=adesso)
    assert s.stato_effettivo(adesso) == "SCADUTO"
    s.pdv_id = uuid.uuid4()
    assert s.stato_effettivo(adesso) == "COMPLETO"


def test_canale_da_utente_solo_canali_rest():
    """R03 i1: una sola regola (``canale_effettivo``): il canale del token di
    canale vale; un claim ``canale`` in un JWT utente non cambia il canale."""
    token_sftp = {"auth_method": "token_canale", "canale": "SFTP"}
    assert car.canale_da_utente(token_sftp) is Canale.SFTP
    assert car.canale_da_utente({"auth_method": "jwt", "canale": "SFTP"}) is Canale.REST
    assert car.canale_da_utente({"canale": "PDND"}) is Canale.REST
    assert car.canale_da_utente({}) is Canale.REST
    with pytest.raises(ErroreApi) as errore:
        car.canale_da_utente(token_sftp, Canale.PDND)
    assert errore.value.codice == "AUTH-010"


async def test_caricamenti_senza_token_401():
    async with client_http() as c:
        r = await c.post("/api/v1/caricamenti", json={"nome_file": "a.pdf", "dimensione": 1})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


# ── Con PostgreSQL e S3 ─────────────────────────────────────────────

async def _apri(c, contenuto: bytes, *, chiave: str | None = None, impronta: str | None = None,
                nome: str = "grande.pdf"):
    corpo = {"nome_file": nome, "dimensione": len(contenuto), "mime_dichiarato": None,
             "impronta": {"algoritmo": "SHA-256", "valore": impronta or sha256(contenuto)}}
    h = {"Idempotency-Key": chiave} if chiave else {}
    return await c.post("/api/v1/caricamenti", json=corpo, headers=h)


async def _parte(c, cid: str, n: int, dati: bytes, **intestazioni):
    return await c.put(f"/api/v1/caricamenti/{cid}/parti/{n}", content=dati,
                       headers={"Content-Type": "application/octet-stream", **intestazioni})


def _fette(contenuto: bytes, parte: int) -> list[bytes]:
    return [contenuto[i:i + parte] for i in range(0, len(contenuto), parte)]


async def _carica_completo(c, contenuto: bytes, **kwargs) -> str:
    """Apre, invia tutte le parti, completa e verifica (un giro del verificatore)."""
    r = await _apri(c, contenuto, **kwargs)
    assert r.status_code == 201, r.text
    cid = r.json()["caricamento_id"]
    for n, dati in enumerate(_fette(contenuto, r.json()["dimensione_parte"]), start=1):
        assert (await _parte(c, cid, n, dati)).status_code == 200
    assert (await c.post(f"/api/v1/caricamenti/{cid}/completa")).status_code == 202
    assert await car.verifica_in_sospeso() >= 1
    return cid


async def _outbox(url: str, topic: str) -> list[dict]:
    righe = await sql(url, "SELECT payload FROM public.outbox_eventi WHERE topic = $1 "
                           "ORDER BY sequenza", topic)
    return [json.loads(r["payload"]) for r in righe]


@richiede_pg
@pytest.mark.integration
class TestSessioniDiCaricamento:
    async def test_flusso_completo_parti_parallele_fuori_ordine(self, db_url, s3, utente_di):
        """Tre parti in parallelo e in ordine inverso, una ripetuta; verifica fuori
        richiesta: impronte di tutti gli algoritmi, formato, evento in outbox."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        contenuto = pdf_di(11 * MB)
        async with client_http() as c:
            r = await _apri(c, contenuto)
            assert r.status_code == 201, r.text
            corpo = r.json()
            assert corpo["stato"] == "APERTO" and corpo["numero_parti"] == 3
            assert corpo["dimensione_parte"] == settings.caricamenti_dimensione_parte_mb * MB
            assert r.headers["Location"] == corpo["link"]["self"]
            cid, parte = corpo["caricamento_id"], corpo["dimensione_parte"]
            fette = _fette(contenuto, parte)
            risposte = await asyncio.gather(*(
                _parte(c, cid, n, fette[n - 1],
                       **{"X-Impronta-Parte": f"sha256={sha256(fette[n - 1])}"})
                for n in (3, 1, 2)))
            assert [x.status_code for x in risposte] == [200, 200, 200], [x.text for x in risposte]
            ripetuta = await _parte(c, cid, 2, fette[1])
            assert ripetuta.status_code == 200 and ripetuta.json()["ricevute"] == [1, 2, 3]
            stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
            assert stato["stato"] == "APERTO" and stato["parti_ricevute"] == [1, 2, 3]

            r = await c.post(f"/api/v1/caricamenti/{cid}/completa")
            assert r.status_code == 202 and r.json()["stato"] == "IN_VERIFICA"
            assert (await c.get(f"/api/v1/caricamenti/{cid}")).json()["stato"] == "IN_VERIFICA"
            assert await car.verifica_in_sospeso() == 1
            stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
            r2 = await c.post(f"/api/v1/caricamenti/{cid}/completa")
        assert stato["stato"] == "COMPLETO", stato
        assert stato["impronte"] == {
            "SHA-256": hashlib.sha256(contenuto).hexdigest(),
            "SHA-512": hashlib.sha512(contenuto).hexdigest(),
            "SHA3-256": hashlib.sha3_256(contenuto).hexdigest(),
        }
        assert stato["formato_rilevato"] == "application/pdf" and stato["errore"] is None
        assert stato["parti_ricevute"] == [1, 2, 3]
        assert r2.status_code == 200 and r2.json()["stato"] == "COMPLETO"   # idempotente
        chiave = f"caricamenti/{tenant}/{cid}/dati"
        assert chiavi_staging(s3) == [chiave]
        assert s3.get_object(Bucket=settings.s3_bucket_pdv, Key=chiave)["Body"].read() == contenuto
        assert await sql(db_url, "SELECT 1 FROM public.caricamenti_parti") == []
        [evento] = await _outbox(db_url, "caricamento.completato")
        assert evento["caricamento_id"] == cid and evento["tenant_id"] == str(tenant)
        assert evento["impronte"]["SHA-256"] == sha256(contenuto)
        assert "nome_file" not in evento   # nessun dato del documento negli eventi

    async def test_parte_diversa_409_e_storage_non_sovrascritto(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        contenuto = pdf_di(6 * MB, seme=1)
        async with client_http() as c:
            cid = (await _apri(c, contenuto)).json()["caricamento_id"]
            prima, seconda = _fette(contenuto, PARTE)
            assert (await _parte(c, cid, 1, prima)).status_code == 200
            diversa = b"X" * len(prima)
            r = await _parte(c, cid, 1, diversa)
            assert r.status_code == 409 and r.json()["codice"] == "UPL-004"
            assert (await _parte(c, cid, 2, seconda)).status_code == 200
            assert (await c.post(f"/api/v1/caricamenti/{cid}/completa")).status_code == 202
            await car.verifica_in_sospeso()
            stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
        assert stato["stato"] == "COMPLETO"   # assemblato con la parte 1 originale
        assert stato["impronte"]["SHA-256"] == sha256(contenuto)

    async def test_parte_non_valida_impronta_numero_dimensione(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            cid = (await _apri(c, PDF)).json()["caricamento_id"]
            r = await _parte(c, cid, 1, PDF, **{"X-Impronta-Parte": f"sha256={'0' * 64}"})
            assert r.status_code == 422 and r.json()["codice"] == "UPL-003"
            r = await _parte(c, cid, 2, PDF)
            assert r.status_code == 422 and r.json()["codice"] == "UPL-002"
            r = await _parte(c, cid, 1, PDF + b"in piu")
            assert r.status_code == 422 and r.json()["codice"] == "UPL-002"
            r = await _parte(c, cid, 0, PDF)
            assert r.status_code == 422 and r.json()["codice"] == "UPL-002"
            stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
        assert stato["parti_ricevute"] == []

    async def test_completa_con_parti_mancanti_409(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        contenuto = pdf_di(11 * MB, seme=2)
        async with client_http() as c:
            cid = (await _apri(c, contenuto)).json()["caricamento_id"]
            assert (await _parte(c, cid, 2, _fette(contenuto, PARTE)[1])).status_code == 200
            r = await c.post(f"/api/v1/caricamenti/{cid}/completa")
        assert r.status_code == 409 and r.json()["codice"] == "UPL-005"
        assert r.json()["dettagli"] == {"parti_mancanti": [1, 3], "numero_mancanti": 2}

    async def test_idempotency_key_stessa_sessione_o_conflitto(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            prima = await _apri(c, PDF, chiave="sessione-prova-01")
            seconda = await _apri(c, PDF, chiave="sessione-prova-01")
            diversa = await _apri(c, PDF + b"x", chiave="sessione-prova-01")
        assert prima.status_code == 201 and seconda.status_code == 200
        assert seconda.headers["Idempotent-Replayed"] == "true"
        assert seconda.json()["caricamento_id"] == prima.json()["caricamento_id"]
        assert diversa.status_code == 409 and diversa.json()["codice"] == "UPL-007"
        assert len(await sql(db_url, "SELECT 1 FROM public.caricamenti")) == 1

    async def test_sessione_di_altro_ente_404(self, db_url, s3, utente_di):
        proprietario, _ = await crea_tenant_e_classe(db_url)
        estraneo, _ = await crea_tenant_e_classe(db_url)
        utente_di(proprietario)
        async with client_http() as c:
            cid = (await _apri(c, PDF)).json()["caricamento_id"]
            utente_di(estraneo)
            risposte = [
                await c.get(f"/api/v1/caricamenti/{cid}"),
                await _parte(c, cid, 1, PDF),
                await c.post(f"/api/v1/caricamenti/{cid}/completa"),
                await c.delete(f"/api/v1/caricamenti/{cid}"),
                await c.get("/api/v1/caricamenti/non-un-uuid"),
            ]
        assert [r.status_code for r in risposte] == [404] * 5
        assert {r.json()["codice"] for r in risposte} == {"UPL-404"}

    async def test_impronta_dichiarata_errata_errore_upl006_ed_evento(self, db_url, s3,
                                                                      utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            r = await _apri(c, PDF, impronta="0" * 64)
            cid = r.json()["caricamento_id"]
            assert (await _parte(c, cid, 1, PDF)).status_code == 200
            assert (await c.post(f"/api/v1/caricamenti/{cid}/completa")).status_code == 202
            assert await car.verifica_in_sospeso() == 1
            stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
        assert stato["stato"] == "ERRORE"
        assert stato["errore"]["codice"] == "UPL-006" and stato["impronte"] is None
        [evento] = await _outbox(db_url, "caricamento.errore")
        assert evento["errore"]["codice"] == "UPL-006"
        assert chiavi_staging(s3) == []   # i file di una sessione in errore si eliminano

    async def test_annulla_elimina_file_e_parti(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        contenuto = pdf_di(6 * MB, seme=3)
        async with client_http() as c:
            cid = (await _apri(c, contenuto)).json()["caricamento_id"]
            assert (await _parte(c, cid, 1, contenuto[:PARTE])).status_code == 200
            assert (await c.delete(f"/api/v1/caricamenti/{cid}")).status_code == 204
            assert (await c.delete(f"/api/v1/caricamenti/{cid}")).status_code == 204
            r = await _parte(c, cid, 2, contenuto[PARTE:])
            stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
        assert r.status_code == 410 and r.json()["codice"] == "UPL-410"
        assert stato["stato"] == "ANNULLATO" and stato["parti_ricevute"] == []
        assert s3.list_multipart_uploads(Bucket=settings.s3_bucket_pdv).get("Uploads", []) == []
        riga = (await sql(db_url, "SELECT storage_eliminato_il FROM public.caricamenti"))[0]
        assert riga["storage_eliminato_il"] is not None

    async def test_scadenza_controllata_a_ogni_richiesta_e_pulizia(self, db_url, s3, utente_di):
        """Una sessione oltre ``scade_il`` è SCADUTA anche prima del job; la pulizia
        la marca e annulla il multipart. Le sessioni valide non si toccano."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        identita = IdentitaVersante(tenant, uuid.UUID(UTENTE), Canale.REST)
        ieri = datetime.now(UTC) - timedelta(hours=settings.caricamenti_scadenza_ore + 1)
        # R05 (VD-C-080): chiamata diretta del caso d'uso con l'ambito dell'ente,
        # come farebbe la richiesta autenticata
        with ambito_ente(tenant):
            vecchia, _ = await car.apri(identita, car.AperturaCaricamento(
                nome_file="a.pdf", dimensione=len(PDF)), idempotency_key=None, adesso=ieri)
        async with client_http() as c:
            valida = (await _apri(c, PDF)).json()["caricamento_id"]
            stato = (await c.get(f"/api/v1/caricamenti/{vecchia.id}")).json()
            r = await _parte(c, str(vecchia.id), 1, PDF)
        assert stato["stato"] == "SCADUTO" and r.status_code == 410
        assert await car.pulisci(adesso=datetime.now(UTC), prova=True) == [str(vecchia.id)]
        assert await car.pulisci(adesso=datetime.now(UTC)) == [str(vecchia.id)]
        righe = {str(r["id"]): r for r in await sql(
            db_url, "SELECT id, stato, storage_eliminato_il FROM public.caricamenti")}
        assert righe[str(vecchia.id)]["stato"] == "SCADUTO"
        assert righe[str(vecchia.id)]["storage_eliminato_il"] is not None
        assert righe[valida]["stato"] == "APERTO" and righe[valida]["storage_eliminato_il"] is None
        aperti = s3.list_multipart_uploads(Bucket=settings.s3_bucket_pdv).get("Uploads", [])
        assert [u["Key"] for u in aperti] == [f"caricamenti/{tenant}/{valida}/dati"]
        assert await car.pulisci(adesso=datetime.now(UTC)) == []   # idempotente

    async def test_verifica_tentativi_esauriti_errore_upl502(self, db_url, s3, utente_di,
                                                             monkeypatch):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            cid = (await _apri(c, PDF)).json()["caricamento_id"]
            await _parte(c, cid, 1, PDF)
            await c.post(f"/api/v1/caricamenti/{cid}/completa")
        await sql(db_url, "UPDATE public.caricamenti SET tentativi_verifica = $1",
                  settings.caricamenti_verifica_tentativi_max)
        assert await car.verifica_in_sospeso() == 1
        riga = (await sql(db_url, "SELECT stato, errore FROM public.caricamenti"))[0]
        assert riga["stato"] == "ERRORE" and json.loads(riga["errore"])["codice"] == "UPL-502"

    async def test_verifica_ripresa_dopo_assemblaggio_gia_fatto(self, db_url, s3, utente_di):
        """Crash fra l'assemblaggio S3 e il database: il giro successivo trova il
        multipart già chiuso (NoSuchUpload) e prosegue con la rilettura."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            cid = (await _apri(c, PDF)).json()["caricamento_id"]
            await _parte(c, cid, 1, PDF)
            await c.post(f"/api/v1/caricamenti/{cid}/completa")
        riga = (await sql(db_url, "SELECT upload_id, chiave_oggetto FROM public.caricamenti"))[0]
        [parte] = await sql(db_url, "SELECT etag FROM public.caricamenti_parti")
        s3.complete_multipart_upload(Bucket=settings.s3_bucket_pdv, Key=riga["chiave_oggetto"],
                                     UploadId=riga["upload_id"], MultipartUpload={
                                         "Parts": [{"PartNumber": 1, "ETag": parte["etag"]}]})
        assert await car.verifica_in_sospeso() == 1
        stato = (await sql(db_url, "SELECT stato, upload_id FROM public.caricamenti"))[0]
        assert stato["stato"] == "COMPLETO" and stato["upload_id"] is None

    async def test_lease_impedisce_doppia_presa_in_carico(self, db_url, s3, utente_di):
        """Un lease valido esclude un secondo verificatore (più repliche insieme)."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            cid = (await _apri(c, PDF)).json()["caricamento_id"]
            await _parte(c, cid, 1, PDF)
            await c.post(f"/api/v1/caricamenti/{cid}/completa")
        futuro = datetime.now(UTC) + timedelta(minutes=10)
        await sql(db_url, "UPDATE public.caricamenti SET detentore = 'altro', "
                          "lease_scade_il = $1", futuro)
        assert await car.verifica_in_sospeso() == 0
        assert await car.verifica_in_sospeso(adesso=lambda: futuro + timedelta(seconds=1)) == 1

    async def test_lavoratore_verifica_e_si_ferma(self, db_url, s3, utente_di, monkeypatch):
        monkeypatch.setattr(settings, "caricamenti_intervallo_secondi", 0.05)
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        fine = asyncio.Event()
        task = asyncio.create_task(car.lavoratore(fine))
        try:
            async with client_http() as c:
                cid = (await _apri(c, PDF)).json()["caricamento_id"]
                await _parte(c, cid, 1, PDF)
                await c.post(f"/api/v1/caricamenti/{cid}/completa")
                for _ in range(100):
                    stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()["stato"]
                    if stato == "COMPLETO":
                        break
                    await asyncio.sleep(0.05)
        finally:
            fine.set()
            await asyncio.wait_for(task, timeout=5)
        assert stato == "COMPLETO"


@richiede_pg
@pytest.mark.integration
class TestVersamentoPerRiferimento:
    async def _versa(self, c, indice_ud: dict, file: dict, chiave: str | None = None):
        h = {"Idempotency-Key": chiave or f"rif-{uuid.uuid4().hex}"}
        return await c.post("/api/v1/versamento/ud", json={"indice": indice_ud, "file": file},
                            headers=h)

    async def test_versamento_per_riferimento_202_sessione_usata_una_volta(
            self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        contenuto = pdf_di(6 * MB, seme=4)
        async with client_http() as c:
            cid = await _carica_completo(c, contenuto)
            ud = indice("fattura.pdf", contenuto)
            r = await self._versa(c, ud, {"fattura.pdf": cid}, chiave="rif-prova-0001")
            ripetuta = await self._versa(c, ud, {"fattura.pdf": cid}, chiave="rif-prova-0001")
            altro = await self._versa(c, indice("fattura.pdf", contenuto, numero="FT-002"),
                                      {"fattura.pdf": cid})
            annulla = await c.delete(f"/api/v1/caricamenti/{cid}")
            stato = (await c.get(f"/api/v1/caricamenti/{cid}")).json()
        assert r.status_code == 202, r.text
        pdv_id = r.json()["pdv_id"]
        assert ripetuta.status_code == 200 and ripetuta.json()["pdv_id"] == pdv_id
        assert altro.status_code == 409 and altro.json()["codice"] == "UPL-008"
        assert annulla.status_code == 409 and stato["pdv_id"] == pdv_id
        [evento] = await _outbox(db_url, "pdv.received")
        doc = evento["docs_s3_keys"][0]
        assert doc["caricamento_id"] == cid and doc["s3_key"] == f"caricamenti/{tenant}/{cid}/dati"
        assert doc["impronte"]["SHA-512"] == hashlib.sha512(contenuto).hexdigest()
        assert evento["canale"] == "REST"
        assert len(await sql(db_url, "SELECT 1 FROM public.pdv")) == 1
        # la pulizia non tocca mai un oggetto usato da un PdV
        assert await car.pulisci(adesso=datetime.now(UTC) + timedelta(days=60)) == []
        assert chiavi_staging(s3) == [f"caricamenti/{tenant}/{cid}/dati"]

    async def test_stessa_sessione_in_parallelo_un_solo_versamento(self, db_url, s3, utente_di):
        """Due versamenti diversi che citano la stessa sessione insieme: uno solo vince."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            cid = await _carica_completo(c, PDF)
            risposte = await asyncio.gather(*(
                self._versa(c, indice("fattura.pdf", PDF, numero=f"FT-10{i}"),
                            {"fattura.pdf": cid}) for i in range(2)))
        assert sorted(r.status_code for r in risposte) == [202, 409], [r.text for r in risposte]
        [rifiuto] = [r for r in risposte if r.status_code == 409]
        assert rifiuto.json()["codice"] == "UPL-008"
        assert len(await sql(db_url, "SELECT 1 FROM public.pdv")) == 1

    async def test_sessione_non_completa_inesistente_o_altrui(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        altro, _ = await crea_tenant_e_classe(db_url)
        utente_di(altro)
        async with client_http() as c:
            altrui = await _carica_completo(c, PDF)
            utente_di(tenant)
            aperta = (await _apri(c, PDF)).json()["caricamento_id"]
            ud = indice("fattura.pdf", PDF)
            r_aperta = await self._versa(c, ud, {"fattura.pdf": aperta})
            r_ignota = await self._versa(c, ud, {"fattura.pdf": str(uuid.uuid4())})
            r_altrui = await self._versa(c, ud, {"fattura.pdf": altrui})
            r_non_uuid = await self._versa(c, ud, {"fattura.pdf": "abc"})
        assert r_aperta.status_code == 409 and r_aperta.json()["codice"] == "UPL-009"
        assert r_aperta.json()["dettagli"]["stato"] == "APERTO"
        assert "Retry-After" in r_aperta.headers
        assert [r.status_code for r in (r_ignota, r_altrui, r_non_uuid)] == [404, 404, 404]
        assert await sql(db_url, "SELECT 1 FROM public.pdv") == []

    async def test_file_dichiarato_e_riferimenti_non_coerenti(self, db_url, s3, utente_di):
        """Le regole sono quelle del caso d'uso: file non dichiarato (VRS-104) o
        impronta dell'indice diversa da quella calcolata (VRS-102)."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            cid = await _carica_completo(c, PDF)
            r_nome = await self._versa(c, indice("fattura.pdf", PDF), {"altro.pdf": cid})
            r_hash = await self._versa(c, indice("fattura.pdf", PDF + b"diverso"),
                                       {"fattura.pdf": cid})
        assert r_nome.status_code == 400 and r_nome.json()["codice"] in ("VRS-101", "VRS-104")
        assert r_hash.status_code == 400 and r_hash.json()["codice"] == "VRS-102"
        stato = (await sql(db_url, "SELECT pdv_id FROM public.caricamenti"))[0]
        assert stato["pdv_id"] is None   # un rifiuto non consuma la sessione

    async def test_corpo_json_non_valido(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            r_forma = await c.post("/api/v1/versamento/ud", json={"indice": {}},
                                   headers={"Idempotency-Key": "rif-forma-0001"})
            r_indice = await c.post("/api/v1/versamento/ud",
                                    json={"indice": {"documenti": []}, "file": {"a.pdf": "x"}},
                                    headers={"Idempotency-Key": "rif-forma-0002"})
            r_multipart = await c.post("/api/v1/versamento/ud", data={"altro": "x"},
                                       files=[("files", ("a.pdf", PDF, "application/pdf"))])
        assert r_forma.status_code == 422 and r_forma.json()["codice"] == "VAL-422"
        assert r_indice.status_code == 400 and r_indice.json()["codice"] == "VRS-001"
        # multipart senza indice: stessa risposta di prima della variante JSON
        assert r_multipart.status_code == 422 and r_multipart.json()["codice"] == "VAL-422"

    async def test_corpo_json_oltre_limite_413(self, db_url, s3, utente_di, monkeypatch):
        monkeypatch.setattr(settings, "versamento_json_max_mb", 1)
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        async with client_http() as c:
            r = await c.post("/api/v1/versamento/ud",
                             content=b'{"indice": "' + b"x" * (MB + 10) + b'"}',
                             headers={"Content-Type": "application/json",
                                      "Idempotency-Key": "rif-grande-0001"})
        assert r.status_code == 413 and r.json()["codice"] == "UPL-413"

    async def test_canale_sftp_dal_token_di_servizio(self, db_url, s3, utente_di):
        """Contratto C2: il canale dell'identità arriva al caso d'uso (evento e sessione)."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant, canale="SFTP", auth_method="token_canale")
        async with client_http() as c:
            cid = await _carica_completo(c, PDF)
            r = await self._versa(c, indice("fattura.pdf", PDF), {"fattura.pdf": cid})
        assert r.status_code == 202, r.text
        [evento] = await _outbox(db_url, "pdv.received")
        assert evento["canale"] == "SFTP"
        assert (await sql(db_url, "SELECT canale FROM public.caricamenti"))[0]["canale"] == "SFTP"


def test_openapi_versamento_multipart_e_json():
    operazione = app.openapi()["paths"]["/api/v1/versamento/ud"]["post"]
    assert set(operazione["requestBody"]["content"]) == {"multipart/form-data",
                                                          "application/json"}
