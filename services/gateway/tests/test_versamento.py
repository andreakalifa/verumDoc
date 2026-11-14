# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test di POST /api/v1/versamento/ud e del caso d'uso unico del versamento (R03).

VD-C-015 (outbox, file solo con PdV salvato), VD-C-016 (idempotenza e
duplicati), VD-C-017 (impronte per ogni algoritmo, file non dichiarati, classe
mai creata dal versamento, eseguibili rinominati), VD-C-083 (Idempotency-Key
obbligatoria con transizione), VD-C-094 (metadati mai persi, regole della
classe), VD-C-095 (chiave UD univoca), streaming sullo staging (nessun file
intero in memoria).

I test senza database verificano i rifiuti che precedono ogni lettura e le
funzioni pure; gli altri girano su PostgreSQL migrato (VD_TEST_DATABASE_URL) e
S3 (moto).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from app.auth.dependencies import get_current_user
from app.config import settings
from app.main import app
from app.models.versamento import IndiceUD, MetadatiDocumento, ModalitaFormazione
from app.routers.versamento import intestazioni_versamento
from app.services import versamento_service as vs
from app.services.outbox import pubblica_in_sospeso
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from libs.shared.db.ambito import ambito_ente
from libs.shared.storage.flusso import ErroreStorageFlusso
from libs.verumdoc_rules import (
    REGISTRO_V1,
    AmbienteRegole,
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
)
from tests.identita_prova import dipendenza_utente
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"
UTENTE = "00000000-0000-0000-0000-000000000002"
NUOVA = object()  # segnaposto: una Idempotency-Key nuova per ogni richiesta
MINIMI = {
    "identificativo_documento": "FT-2026-001",
    "modalita_formazione": next(iter(ModalitaFormazione)).value,
    "tipologia_documentale": "fattura",
    "data_documento": "2026-03-19",
    "soggetto_produttore_id": "12345678901",
    "soggetto_produttore_nome": "Acme S.r.l.",
    "oggetto": "Fattura servizi IT marzo 2026",
}


def _indice(
    nome: str = "fattura.pdf",
    contenuto: bytes = PDF,
    *,
    algoritmo: str = "SHA-256",
    impronta: str | None = None,
    classe: str = "fattura",
    formato: str = "application/pdf",
    con_parametri: bool = True,
    numero: str = "FT-001",
    registro: str = "FATTURE",
    extra: dict | None = None,
) -> dict[str, Any]:
    funzione = vs.ALGORITMI_IMPRONTA[algoritmo]
    doc: dict[str, Any] = {
        "id": "doc-001",
        "tipo": "PRINCIPALE",
        "nome_file": nome,
        "formato": formato,
        "hash": {"impronta": impronta or funzione(contenuto).hexdigest(), "algoritmo": algoritmo},
    }
    if con_parametri:
        doc["parametri"] = {**MINIMI, **(extra or {})}
    return {
        "classe_documentale": classe,
        "chiave": {"numero": numero, "anno": "2026", "registro": registro},
        "documenti": [doc],
    }


async def _versa(
    indice: dict | str,
    files: list[tuple[str, bytes]],
    *,
    chiave: Any = NUOVA,
    token: str | None = None,
):
    intestazioni = {}
    if chiave is NUOVA:
        chiave = f"prova-{uuid.uuid4().hex}"
    if chiave:
        intestazioni["Idempotency-Key"] = chiave
    if token:
        intestazioni["Authorization"] = f"Bearer {token}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/versamento/ud",
            headers=intestazioni,
            data={"indice": indice if isinstance(indice, str) else json.dumps(indice)},
            files=[("files", (n, c, "application/octet-stream")) for n, c in files],
        )


@pytest.fixture
def utente_di(request):
    """Imposta l'utente autenticato (dipendenza sovrascritta) per il cliente indicato."""

    def _imposta(tenant: uuid.UUID | str, scopes: list[str] | None = None) -> None:
        app.dependency_overrides[get_current_user] = dipendenza_utente({
            "tenant_id": str(tenant),
            "user_id": UTENTE,
            "ruolo": "OPERATORE",
            "scopes": scopes or ["versamento:ud", "read"],
        })

    yield _imposta
    app.dependency_overrides.pop(get_current_user, None)


def _ambiente(valori: dict | None = None) -> AmbienteRegole:
    contesto = ContestoRisoluzione(adesso=datetime(2026, 10, 21, tzinfo=UTC), tenant_id="t")
    return AmbienteRegole(Risolutore(REGISTRO_V1, FornitoreDaDizionari(valori or {})), contesto)


def _caricato(nome: str, contenuto: bytes) -> vs.FileCaricato:
    return vs.FileCaricato(
        nome,
        "b",
        f"k/{nome}",
        len(contenuto),
        {a: f(contenuto).hexdigest() for a, f in vs.ALGORITMI_IMPRONTA.items()},
        contenuto[:4096],
    )


# ── Rifiuti prima di ogni lettura (senza database) ─────────────────


async def test_versamento_senza_token_ritorna_401():
    resp = await _versa("{}", [])
    assert resp.status_code == 401


async def test_versamento_scope_insufficiente_ritorna_403(utente_di):
    utente_di(uuid.uuid4(), scopes=["read"])
    resp = await _versa("{}", [])
    assert resp.status_code == 403


async def test_versamento_indice_json_invalido_ritorna_400_problem_json(utente_di):
    utente_di(uuid.uuid4())
    resp = await _versa("QUESTO NON E JSON {{{", [("fattura.pdf", PDF)])
    assert resp.status_code == 400 and resp.json()["codice"] == "VRS-001"
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_versamento_algoritmo_non_ammesso_ritorna_400(utente_di):
    utente_di(uuid.uuid4())
    indice = _indice()
    indice["documenti"][0]["hash"]["algoritmo"] = "MD5"
    resp = await _versa(indice, [("fattura.pdf", PDF)])
    assert resp.status_code == 400  # rifiutato già dallo schema dell'indice


async def test_versamento_idempotency_key_non_valida_ritorna_400(utente_di):
    utente_di(uuid.uuid4())
    resp = await _versa(_indice(), [("fattura.pdf", PDF)], chiave="a b")
    assert resp.status_code == 400 and resp.json()["codice"] == "VRS-122"


async def test_versamento_token_senza_cliente_valido_ritorna_403(utente_di):
    utente_di("non-un-uuid")
    resp = await _versa(_indice(), [("fattura.pdf", PDF)])
    assert resp.status_code == 403 and resp.json()["codice"] == "VRS-403"


async def test_versamento_file_allegato_non_dichiarato_rifiutato_prima_del_db(utente_di):
    utente_di(uuid.uuid4())
    resp = await _versa(_indice(), [("fattura.pdf", PDF), ("extra.pdf", PDF)])
    assert resp.status_code == 400 and resp.json()["codice"] == "VRS-104"
    assert resp.json()["dettagli"]["file"] == ["extra.pdf"]


async def test_versamento_troppi_file_413(utente_di, monkeypatch):
    utente_di(uuid.uuid4())
    monkeypatch.setattr(settings, "versamento_max_file_per_pdv", 1)
    resp = await _versa(_indice(), [("fattura.pdf", PDF), ("b.pdf", PDF)])
    assert resp.status_code == 413 and resp.json()["codice"] == "VRS-113"


@pytest.mark.parametrize("algoritmo", ["SHA-256", "SHA-512", "SHA3-256"])
def test_verifica_file_impronta_falsa_ogni_algoritmo_rifiutata(algoritmo):
    lunghezza = len(vs.ALGORITMI_IMPRONTA[algoritmo](b"").hexdigest())
    indice = IndiceUD.model_validate(_indice(algoritmo=algoritmo, impronta="a" * lunghezza))
    with pytest.raises(vs.RifiutoVersamento) as err:
        vs.verifica_file(indice, {"fattura.pdf": _caricato("fattura.pdf", PDF)})
    assert err.value.codice == "VRS-102"


@pytest.mark.parametrize("algoritmo", ["SHA-256", "SHA-512", "SHA3-256"])
def test_verifica_file_impronta_giusta_ogni_algoritmo_ammessa(algoritmo):
    indice = IndiceUD.model_validate(_indice(algoritmo=algoritmo))
    [v] = vs.verifica_file(indice, {"fattura.pdf": _caricato("fattura.pdf", PDF)})
    assert v.mime_rilevato == "application/pdf" and v.sha256 == hashlib.sha256(PDF).hexdigest()


def test_verifica_file_eseguibile_rinominato_rifiutato():
    exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00"
    indice = IndiceUD.model_validate(_indice(contenuto=exe))
    with pytest.raises(vs.RifiutoVersamento) as err:
        vs.verifica_file(indice, {"fattura.pdf": _caricato("fattura.pdf", exe)})
    assert err.value.codice == "VRS-108" and err.value.status == 422


def test_verifica_file_contenuto_diverso_dal_dichiarato_rifiutato():
    testo = b"solo testo, non un PDF"
    indice = IndiceUD.model_validate(_indice(contenuto=testo))
    with pytest.raises(vs.RifiutoVersamento) as err:
        vs.verifica_file(indice, {"fattura.pdf": _caricato("fattura.pdf", testo)})
    assert err.value.codice == "VRS-109"


def test_calcola_impronta_indice_indipendente_dalla_forma_del_json():
    a = IndiceUD.model_validate_json(json.dumps(_indice()))
    b = IndiceUD.model_validate_json(json.dumps(_indice(), indent=4, sort_keys=True))
    assert vs.calcola_impronta_indice(a) == vs.calcola_impronta_indice(b)


def test_metadati_documento_conserva_le_chiavi_non_previste():
    """VD-C-094: prima `importo` spariva in silenzio (extra=ignore)."""
    m = MetadatiDocumento.model_validate({**MINIMI, "importo": "10.50", "valuta": "EUR"})
    dump = m.model_dump(mode="json")
    assert dump["importo"] == "10.50" and dump["valuta"] == "EUR"


def test_normalizza_chiave_ud():
    from app.models.versamento import ChiaveUD

    assert vs.normalizza_chiave_ud(
        ChiaveUD(numero=" 7 ", anno="2026", registro=" fatt ")
    ) == vs.ChiaveUDNormalizzata("FATT", "2026", "7")
    assert vs.normalizza_chiave_ud(ChiaveUD(numero="7", anno="2026")) == vs.ChiaveUDNormalizzata(
        "", "2026", "7"
    )
    assert vs.normalizza_chiave_ud(ChiaveUD(numero="7")) is None
    assert vs.normalizza_chiave_ud(None) is None


def test_decidi_idempotenza_con_chiave_nessun_avviso():
    idem = vs.decidi_idempotenza("chiave-0001", _ambiente(), datetime.now(UTC), uuid.uuid4(), "i")
    assert idem.chiave == "chiave-0001" and idem.derivata is None and idem.avviso is None


def test_decidi_idempotenza_senza_chiave_in_transizione_avviso_e_chiave_derivata():
    tenant = uuid.uuid4()
    adesso = datetime(2027, 3, 31, 21, 0, tzinfo=UTC)  # 23:00 del 31/03 in Italia
    idem = vs.decidi_idempotenza(None, _ambiente(), adesso, tenant, "abc")
    assert idem.chiave is None and idem.derivata == vs.chiave_derivata(tenant, "abc")
    assert idem.avviso["codice"] == "VRS-126" and idem.fino_al == date(2027, 3, 31)


def test_decidi_idempotenza_senza_chiave_dopo_la_transizione_428():
    adesso = datetime(2027, 3, 31, 22, 30, tzinfo=UTC)  # già 1° aprile in Italia
    with pytest.raises(vs.RifiutoVersamento) as err:
        vs.decidi_idempotenza(None, _ambiente(), adesso, uuid.uuid4(), "abc")
    assert err.value.codice == "VRS-125" and err.value.status == 428


def test_decidi_idempotenza_transizione_prorogata_per_ente():
    ambiente = _ambiente({Livello.ENTE: {"IDEMPOTENZA_TRANSIZIONE_FINO_AL": "2027-12-31"}})
    idem = vs.decidi_idempotenza(
        None, ambiente, datetime(2027, 6, 1, tzinfo=UTC), uuid.uuid4(), "i"
    )
    assert idem.fino_al == date(2027, 12, 31)


def test_decidi_idempotenza_obbligo_spento_dal_conservatore_nessun_avviso():
    ambiente = _ambiente({Livello.CONSERVATORE: {"IDEMPOTENZA_OBBLIGATORIA": False}})
    idem = vs.decidi_idempotenza(
        None, ambiente, datetime(2030, 1, 1, tzinfo=UTC), uuid.uuid4(), "i"
    )
    assert idem.avviso is None and idem.derivata is not None


def test_intestazioni_versamento_deprecation_e_sunset():
    esito = vs.EsitoVersamento(
        "p1", "RICEVUTO", nuovo=False, chiave_derivata=True, transizione_fino_al=date(2027, 3, 31)
    )
    h = intestazioni_versamento(esito)
    assert h["Location"] == "/api/v1/versamento/p1" and h["Idempotent-Replayed"] == "true"
    assert h["Deprecation"].startswith("@")
    assert h["Sunset"] == "Wed, 31 Mar 2027 22:00:00 GMT"  # mezzanotte italiana del 1° aprile
    assert "Deprecation" not in intestazioni_versamento(vs.EsitoVersamento("p1", "RICEVUTO"))


def test_rdv_preliminare_namespace_verumdoc_documenti_e_avvisi():
    import xml.etree.ElementTree as ET

    xml = vs.costruisci_rdv_preliminare(
        "p1",
        "t1",
        "abc",
        datetime(2026, 10, 21, tzinfo=UTC),
        [{"doc_id": "d1", "nome_file": "a.pdf", "size_bytes": 3, "hash_sha256": "ff"}],
        [{"codice": "RGL-103", "campo": "importo", "documento": "d1", "messaggio": "m"}],
    )
    ns = {"r": vs.NAMESPACE_RDV_PRELIMINARE}
    radice = ET.fromstring(xml)
    assert "agid.gov.it" not in xml and radice.get("versione") == "1"
    assert radice.find("r:Documenti/r:Documento/r:ImprontaSHA256", ns).text == "ff"
    assert radice.find("r:Avvisi/r:Avviso/r:Codice", ns).text == "RGL-103"


# ── File già caricati (sessioni di caricamento) ────────────────────


@pytest.fixture
def s3(s3_server, monkeypatch):
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_pdv", f"pdv-{uuid.uuid4().hex[:8]}")
    client = client_s3(s3_server)
    client.create_bucket(Bucket=settings.s3_bucket_pdv)
    return client


def _gia_caricato(s3, tenant: uuid.UUID, contenuto: bytes = PDF, **modifiche) -> vs.FileGiaCaricato:
    chiave = f"{vs.prefisso_caricamenti(tenant)}car-1/fattura.pdf"
    s3.put_object(Bucket=settings.s3_bucket_pdv, Key=chiave, Body=contenuto)
    campi: dict[str, Any] = {
        "nome_file": "fattura.pdf",
        "bucket": settings.s3_bucket_pdv,
        "chiave": chiave,
        "dimensione": len(contenuto),
        "impronte": {a: f(contenuto).hexdigest() for a, f in vs.ALGORITMI_IMPRONTA.items()},
        "testa": contenuto[:4096],
        "caricamento_id": "car-1",
    }
    campi.update(modifiche)
    return vs.FileGiaCaricato(**campi)


async def test_file_gia_caricato_valido(s3):
    tenant = uuid.uuid4()
    f = await vs._verifica_gia_caricato(s3, _gia_caricato(s3, tenant), tenant)
    assert f.caricamento_id == "car-1" and f.dimensione == len(PDF)


async def test_file_gia_caricato_di_altro_cliente_404(s3):
    proprietario = uuid.uuid4()
    with pytest.raises(vs.RifiutoVersamento) as err:
        await vs._verifica_gia_caricato(s3, _gia_caricato(s3, proprietario), uuid.uuid4())
    assert err.value.codice == "UPL-404"


async def test_file_gia_caricato_oggetto_assente_404(s3):
    tenant = uuid.uuid4()
    fonte = _gia_caricato(s3, tenant, chiave=f"{vs.prefisso_caricamenti(tenant)}manca/x.pdf")
    with pytest.raises(vs.RifiutoVersamento) as err:
        await vs._verifica_gia_caricato(s3, fonte, tenant)
    assert err.value.codice == "UPL-404"


async def test_file_gia_caricato_dimensione_diversa_422(s3):
    tenant = uuid.uuid4()
    with pytest.raises(vs.RifiutoVersamento) as err:
        await vs._verifica_gia_caricato(
            s3, _gia_caricato(s3, tenant, dimensione=len(PDF) + 1), tenant
        )
    assert err.value.codice == "UPL-422"


async def test_file_gia_caricato_impronte_incomplete_422(s3):
    tenant = uuid.uuid4()
    fonte = _gia_caricato(s3, tenant, impronte={"SHA-256": hashlib.sha256(PDF).hexdigest()})
    with pytest.raises(vs.RifiutoVersamento) as err:
        await vs._verifica_gia_caricato(s3, fonte, tenant)
    assert err.value.codice == "UPL-422"


# ── Con PostgreSQL e S3 ────────────────────────────────────────────


def _chiavi(s3) -> list[str]:
    try:
        return [
            o["Key"] for o in s3.list_objects_v2(Bucket=settings.s3_bucket_pdv).get("Contents", [])
        ]
    except s3.exceptions.NoSuchBucket:
        return []


class _ProducerFinto:
    def __init__(self):
        self.inviati: list[tuple[str, str, dict]] = []

    async def send_and_wait(self, topic, value=None, key=None):
        self.inviati.append((topic, key, value))


async def _payload(db_url: str, pdv_id: str) -> dict:
    riga = await sql(db_url, "SELECT payload FROM public.outbox_eventi WHERE chiave = $1", pdv_id)
    return json.loads(riga[0]["payload"])


@pytest.fixture
def transizione_aperta(monkeypatch):
    """Transizione VD-P-27 in corso indipendentemente dalla data dei test."""
    monkeypatch.setattr(settings, "idempotenza_transizione_fino_al", date(2999, 12, 31))


@richiede_pg
@pytest.mark.integration
class TestVersamentoSuDatabase:
    async def test_versamento_valido_202_pdv_outbox_file_e_fotografia(
        self, db_url, s3, utente_di, monkeypatch
    ):
        """PdV, riga d'impronta ed evento in outbox nella stessa transazione; evento pubblicato."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        producer = _ProducerFinto()
        from libs.shared import kafka as kafka_condiviso

        monkeypatch.setattr(kafka_condiviso, "_producer", producer)

        resp = await _versa(_indice(algoritmo="SHA3-256"), [("fattura.pdf", PDF)])
        assert resp.status_code == 202, resp.text
        corpo = resp.json()
        pdv_id = corpo["pdv_id"]
        assert corpo["avvisi"] == [] and corpo["link_stato"] == f"/api/v1/versamento/{pdv_id}"
        assert resp.headers["Location"] == corpo["link_stato"] and "Deprecation" not in resp.headers
        riga = (
            await sql(
                db_url,
                "SELECT stato::text AS s, regole_applicate, avvisi FROM public.pdv WHERE id = $1",
                uuid.UUID(pdv_id),
            )
        )[0]
        assert riga["s"] == "RICEVUTO" and json.loads(riga["avvisi"]) == []
        regole = json.loads(riga["regole_applicate"])
        # R05 Q1: catalogo 2, con accordo e override (nessuno) nella fotografia (R11: catalogo 4)
        assert regole["catalogo_regole"] == "4" and regole["profilo_metadati"] is None
        assert regole["accordo"] is None and regole["override"] is None
        assert regole["impostazioni"]["PROFILO_TIPOLOGIA"] == {"valore": "",
                                                               "origine": "PREDEFINITO"}
        assert regole["impostazioni"]["REGOLA.RGL-101.EFFETTO"] == {
            "valore": "BLOCCA",
            "origine": "PREDEFINITO",
        }
        assert regole["impostazioni"]["CLASSE_DIMENSIONE_MAX_MB"]["origine"] == "CLASSE"
        outbox = await sql(db_url, "SELECT topic, pubblicato_il, payload FROM public.outbox_eventi")
        assert len(outbox) == 1 and outbox[0]["pubblicato_il"] is not None
        payload = json.loads(outbox[0]["payload"])
        doc = payload["docs_s3_keys"][0]
        assert doc["hash_sha256"] == hashlib.sha256(PDF).hexdigest()
        assert doc["impronte"]["SHA3-256"] == hashlib.sha3_256(PDF).hexdigest()
        assert doc["formato_rilevato"] == "application/pdf" and doc["size_bytes"] == len(PDF)
        assert doc["s3_key"].startswith(f"versamenti/{tenant}/{pdv_id}/")
        assert payload["canale"] == "REST" and payload["tipologia_xml"] == "DAI"
        assert [t for t, _, _ in producer.inviati] == ["pdv.received"]
        assert _chiavi(s3) == [doc["s3_key"]]
        assert s3.get_object(Bucket=settings.s3_bucket_pdv, Key=doc["s3_key"])["Body"].read() == PDF

    async def test_versamento_file_grande_multipart_in_streaming(
        self, db_url, s3, utente_di, monkeypatch
    ):
        """Un file oltre una parte va sullo staging a parti; impronte calcolate in transito."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        monkeypatch.setattr(settings, "staging_parte_mb", 5)
        grande = PDF + b"0" * (6 * 1024 * 1024)
        resp = await _versa(
            _indice(contenuto=grande, algoritmo="SHA-512"), [("fattura.pdf", grande)]
        )
        assert resp.status_code == 202, resp.text
        doc = (await _payload(db_url, resp.json()["pdv_id"]))["docs_s3_keys"][0]
        letto = s3.get_object(Bucket=settings.s3_bucket_pdv, Key=doc["s3_key"])["Body"].read()
        assert hashlib.sha512(letto).hexdigest() == doc["impronte"]["SHA-512"]
        assert s3.list_multipart_uploads(Bucket=settings.s3_bucket_pdv).get("Uploads", []) == []

    async def test_versamento_kafka_giu_202_evento_in_outbox(self, db_url, s3, utente_di):
        """VD-C-015: Kafka non disponibile → 202 e l'evento resta in outbox (non perso).

        R04 integrazione: con il producer non attivo il cortocircuito dell'outbox
        esce senza toccare le righe (nessun tentativo registrato, un solo avviso
        nel log e la metrica ``verumdoc_outbox_kafka_cortocircuito``): prima ogni
        versamento segnava un tentativo fallito per evento."""
        import libs.shared.outbox as outbox_condiviso

        outbox_condiviso._cortocircuiti.clear()
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 202
        outbox = await sql(
            db_url, "SELECT pubblicato_il, tentativi, ultimo_errore FROM public.outbox_eventi"
        )
        assert outbox[0]["pubblicato_il"] is None
        assert outbox[0]["tentativi"] == 0 and outbox[0]["ultimo_errore"] is None
        outbox_condiviso._cortocircuiti.clear()

        inviati: list = []

        async def invia(topic, chiave, payload):
            inviati.append((topic, chiave))

        assert await pubblica_in_sospeso(invia) == 1
        assert inviati == [("pdv.received", resp.json()["pdv_id"])]

    # ── Idempotenza e duplicati ──────────────────────────────────

    async def test_versamento_duplicato_altra_chiave_409_rimanda_al_pdv_esistente(
        self, db_url, s3, utente_di
    ):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        primo = await _versa(_indice(), [("fattura.pdf", PDF)])
        secondo = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert secondo.status_code == 409
        corpo = secondo.json()
        assert corpo["codice"] == "VRS-120"
        assert corpo["dettagli"]["pdv_id_esistente"] == primo.json()["pdv_id"]
        assert corpo["dettagli"]["link"].endswith(primo.json()["pdv_id"])
        assert len(_chiavi(s3)) == 1  # nessun file caricato per il duplicato

    async def test_versamento_duplicato_dopo_rifiuto_ammesso(self, db_url, s3, utente_di):
        """Un PdV rifiutato (impronta disattivata dalla verifica) non blocca il nuovo versamento."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        primo = await _versa(_indice(), [("fattura.pdf", PDF)])
        await sql(
            db_url,
            "UPDATE public.versamento_impronte SET attivo = false WHERE pdv_id = $1",
            uuid.UUID(primo.json()["pdv_id"]),
        )
        secondo = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert secondo.status_code == 202
        assert secondo.json()["pdv_id"] != primo.json()["pdv_id"]

    async def test_versamento_stesso_indice_altro_cliente_ammesso(self, db_url, s3, utente_di):
        tenant_a, _ = await crea_tenant_e_classe(db_url)
        tenant_b, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant_a)
        assert (await _versa(_indice(), [("fattura.pdf", PDF)])).status_code == 202
        utente_di(tenant_b)
        assert (await _versa(_indice(), [("fattura.pdf", PDF)])).status_code == 202

    async def test_idempotency_key_ripetuta_stesso_pdv_senza_ricaricare(
        self, db_url, s3, utente_di
    ):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        primo = await _versa(_indice(), [("fattura.pdf", PDF)], chiave="ordine-000123")
        secondo = await _versa(_indice(), [("fattura.pdf", PDF)], chiave="ordine-000123")
        assert primo.status_code == 202 and secondo.status_code == 200
        assert secondo.headers["Idempotent-Replayed"] == "true"
        assert secondo.json()["pdv_id"] == primo.json()["pdv_id"]
        assert len(await sql(db_url, "SELECT 1 FROM public.pdv")) == 1
        assert len(_chiavi(s3)) == 1

    async def test_idempotency_key_contenuto_diverso_409(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        await _versa(_indice(), [("fattura.pdf", PDF)], chiave="ordine-000124")
        altro = await _versa(
            _indice(numero="FT-999"), [("fattura.pdf", PDF)], chiave="ordine-000124"
        )
        assert altro.status_code == 409 and altro.json()["codice"] == "VRS-121"

    async def test_senza_chiave_in_transizione_202_con_avviso_e_deprecation(
        self, db_url, s3, utente_di, transizione_aperta
    ):
        """VD-C-083: senza Idempotency-Key durante la transizione: accettato, avviso VRS-126,
        intestazioni Deprecation e Sunset; la stessa richiesta ripetuta → stesso PdV (200)."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        primo = await _versa(_indice(), [("fattura.pdf", PDF)], chiave=None)
        assert primo.status_code == 202, primo.text
        assert [a["codice"] for a in primo.json()["avvisi"]] == ["VRS-126"]
        assert primo.headers["Deprecation"].startswith("@") and "Sunset" in primo.headers
        secondo = await _versa(_indice(), [("fattura.pdf", PDF)], chiave=None)
        assert secondo.status_code == 200 and secondo.json()["pdv_id"] == primo.json()["pdv_id"]
        assert secondo.headers["Idempotent-Replayed"] == "true"
        riga = (await sql(db_url, "SELECT chiave_idempotenza FROM public.versamento_impronte"))[0]
        assert riga["chiave_idempotenza"] is None

    async def test_senza_chiave_dopo_la_transizione_428(self, db_url, s3, utente_di, monkeypatch):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        monkeypatch.setattr(settings, "idempotenza_transizione_fino_al", date(2020, 1, 1))
        resp = await _versa(_indice(), [("fattura.pdf", PDF)], chiave=None)
        assert resp.status_code == 428 and resp.json()["codice"] == "VRS-125"
        assert _chiavi(s3) == [] and await sql(db_url, "SELECT 1 FROM public.pdv") == []

    async def test_transizione_prorogata_per_ente_dal_database(
        self, db_url, s3, utente_di, monkeypatch
    ):
        """Livello ENTE in impostazioni_valori: la proroga vale solo per quel cliente."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        monkeypatch.setattr(settings, "idempotenza_transizione_fino_al", date(2020, 1, 1))
        await sql(
            db_url,
            "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
            "valore, motivo) VALUES ('ENTE', $1, 'IDEMPOTENZA_TRANSIZIONE_FINO_AL', "
            "'\"2999-01-01\"'::jsonb, 'proroga concordata')",
            tenant,
        )
        resp = await _versa(_indice(), [("fattura.pdf", PDF)], chiave=None)
        assert resp.status_code == 202, resp.text
        regole = json.loads(
            (await sql(db_url, "SELECT regole_applicate FROM public.pdv"))[0]["regole_applicate"]
        )
        assert regole["impostazioni"]["IDEMPOTENZA_TRANSIZIONE_FINO_AL"] == {
            "valore": "2999-01-01",
            "origine": "ENTE",
        }

    async def test_riga_del_conservatore_prevale_sulla_variabile(
        self, db_url, s3, utente_di, monkeypatch
    ):
        """Livello CONSERVATORE: la riga di impostazioni_valori prevale sulla variabile
        d'ambiente; obbligo spento → accettato senza avviso anche dopo la transizione."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        monkeypatch.setattr(settings, "idempotenza_obbligatoria", True)
        monkeypatch.setattr(settings, "idempotenza_transizione_fino_al", date(2020, 1, 1))
        await sql(
            db_url,
            "INSERT INTO public.impostazioni_valori (livello, codice, valore) "
            "VALUES ('CONSERVATORE', 'IDEMPOTENZA_OBBLIGATORIA', 'false')",
        )
        resp = await _versa(_indice(), [("fattura.pdf", PDF)], chiave=None)
        assert resp.status_code == 202 and resp.json()["avvisi"] == []
        assert "Deprecation" in resp.headers  # resta deprecato: manca comunque la chiave

    # ── Chiave UD univoca (VD-C-095) ────────────────────────────

    async def test_chiave_ud_gia_usata_con_contenuto_diverso_409(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        primo = await _versa(_indice(), [("fattura.pdf", PDF)])
        altro_pdf = PDF + b"% versione 2\n"
        secondo = await _versa(
            _indice(contenuto=altro_pdf, registro=" fatture "), [("fattura.pdf", altro_pdf)]
        )
        assert secondo.status_code == 409 and secondo.json()["codice"] == "VRS-124"
        assert secondo.json()["dettagli"]["pdv_id_esistente"] == primo.json()["pdv_id"]
        assert len(_chiavi(s3)) == 1

    async def test_chiave_ud_liberata_dal_rifiuto(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        primo = await _versa(_indice(), [("fattura.pdf", PDF)])
        await sql(
            db_url,
            "UPDATE public.versamento_impronte SET attivo = false WHERE pdv_id = $1",
            uuid.UUID(primo.json()["pdv_id"]),
        )
        altro_pdf = PDF + b"% corretto\n"
        resp = await _versa(_indice(contenuto=altro_pdf), [("fattura.pdf", altro_pdf)])
        assert resp.status_code == 202, resp.text

    async def test_chiave_ud_altro_registro_ammessa(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        assert (await _versa(_indice(), [("fattura.pdf", PDF)])).status_code == 202
        altro_pdf = PDF + b"% x\n"
        resp = await _versa(
            _indice(contenuto=altro_pdf, registro="PROTOCOLLO"), [("fattura.pdf", altro_pdf)]
        )
        assert resp.status_code == 202

    async def test_chiave_ud_in_parallelo_una_sola_accettata(self, db_url, s3, utente_di):
        """Due UD diverse con la stessa chiave in parallelo: l'indice unico decide (VRS-124)."""
        import asyncio

        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        b = PDF + b"% b\n"
        risposte = await asyncio.gather(
            _versa(_indice(), [("fattura.pdf", PDF)]),
            _versa(_indice(contenuto=b), [("fattura.pdf", b)]),
        )
        assert sorted(r.status_code for r in risposte) == [202, 409]
        assert {r.json()["codice"] for r in risposte if r.status_code == 409} == {"VRS-124"}
        assert len(_chiavi(s3)) == 1

    # ── Classe documentale ────────────────────────────────────────

    async def test_versamento_classe_sconosciuta_422_e_classe_non_creata(
        self, db_url, s3, utente_di
    ):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        resp = await _versa(_indice(classe="classe_inventata_xyz"), [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-105"
        assert (
            await sql(
                db_url,
                "SELECT 1 FROM public.classi_documentali WHERE codice = $1",
                "classe_inventata_xyz",
            )
            == []
        )

    async def test_versamento_classe_di_altro_cliente_invisibile(self, db_url, s3, utente_di):
        await crea_tenant_e_classe(db_url, codice_classe="riservata", classe_del_tenant=True)
        altro, _ = await crea_tenant_e_classe(db_url)
        utente_di(altro)
        resp = await _versa(_indice(classe="riservata"), [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-105"

    async def test_versamento_classe_non_attiva_422(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url, attiva=False)
        utente_di(tenant)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-106"

    # ── Contenuto dei file ───────────────────────────────────────

    async def test_versamento_hash_sha512_falso_400_file_compensato(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        resp = await _versa(
            _indice(algoritmo="SHA-512", impronta="ab" * 64), [("fattura.pdf", PDF)]
        )
        assert resp.status_code == 400 and resp.json()["codice"] == "VRS-102"
        assert _chiavi(s3) == []

    async def test_versamento_file_mancante_400(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        resp = await _versa(_indice(), [])
        assert resp.status_code == 400 and resp.json()["codice"] == "VRS-101"

    async def test_versamento_eseguibile_rinominato_422(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"
        resp = await _versa(_indice(contenuto=exe), [("fattura.pdf", exe)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-108"
        assert _chiavi(s3) == []

    async def test_versamento_oltre_limite_del_canale_413_senza_staging(
        self, db_url, s3, utente_di, monkeypatch
    ):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        monkeypatch.setattr(settings, "versamento_rest_max_file_mb", 1)
        grande = PDF + b"0" * (2 * 1024 * 1024)
        resp = await _versa(_indice(contenuto=grande), [("fattura.pdf", grande)])
        assert resp.status_code == 413 and resp.json()["codice"] == "VRS-010"
        assert _chiavi(s3) == []

    # ── Motore delle regole (VD-C-094) ───────────────────────────

    async def test_metadati_minimi_mancanti_422_con_tutti_i_rilievi(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        resp = await _versa(_indice(con_parametri=False), [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-130"
        rilievi = resp.json()["dettagli"]["rilievi"]
        assert {r["codice"] for r in rilievi} == {"RGL-101"} and len(rilievi) == 7
        assert {r["campo"] for r in rilievi} == set(MINIMI)
        assert _chiavi(s3) == []  # rifiutato prima di trasferire i file

    async def test_metadato_non_previsto_avviso_e_conservato(self, db_url, s3, utente_di):
        """VD-C-094: `importo` non va perso: è nell'evento, nel DB e nel RdV; avviso RGL-103."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        resp = await _versa(_indice(extra={"importo": "1250.00"}), [("fattura.pdf", PDF)])
        assert resp.status_code == 202, resp.text
        assert [(a["codice"], a["campo"]) for a in resp.json()["avvisi"]] == [
            ("RGL-103", "importo")
        ]
        pdv_id = resp.json()["pdv_id"]
        payload = await _payload(db_url, pdv_id)
        assert payload["allegato5"]["doc-001"]["importo"] == "1250.00"
        assert payload["avvisi"][0]["codice"] == "RGL-103"
        riga = (await sql(db_url, "SELECT avvisi, metadata_json FROM public.pdv"))[0]
        assert json.loads(riga["avvisi"])[0]["campo"] == "importo"
        meta = json.loads(riga["metadata_json"])
        assert meta["indice"]["documenti"][0]["parametri"]["importo"] == "1250.00"
        assert "<Codice>RGL-103</Codice>" in meta["rdv_preliminare"]

    async def test_metadato_obbligatorio_della_classe_mancante_422(self, db_url, s3, utente_di):
        tenant, classe = await crea_tenant_e_classe(db_url)
        await sql(
            db_url,
            "UPDATE public.classi_documentali SET metadati_obbligatori = "
            "'[\"numero_fattura\"]'::jsonb WHERE id = $1",
            classe,
        )
        utente_di(tenant)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 422
        assert [(r["codice"], r["campo"]) for r in resp.json()["dettagli"]["rilievi"]] == [
            ("RGL-102", "numero_fattura")
        ]
        ok = await _versa(
            _indice(extra={"metadati_extra": {"numero_fattura": "12"}}), [("fattura.pdf", PDF)]
        )
        assert ok.status_code == 202 and ok.json()["avvisi"] == []

    async def test_formato_fuori_classe_422_rgl105_file_compensato(self, db_url, s3, utente_di):
        # R03 f1: l'estensione .png è ammessa dalla classe (formati PNG), il contenuto
        # è un PDF: il formato dal contenuto lo rifiuta RGL-105 dopo il trasferimento
        tenant, _ = await crea_tenant_e_classe(db_url, formati='["image/png"]')
        utente_di(tenant)
        resp = await _versa(_indice("fattura.png"), [("fattura.png", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-130"
        assert resp.json()["dettagli"]["rilievi"][0]["codice"] == "RGL-105"
        assert _chiavi(s3) == []

    async def test_estensione_fuori_dai_formati_della_classe_sec006_prima_dei_byte(
        self, db_url, s3, utente_di
    ):
        """R03 f1 (difetto 4 di c2): le estensioni derivano dai formati della classe."""
        tenant, _ = await crea_tenant_e_classe(db_url, formati='["image/png"]')
        utente_di(tenant)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 400, resp.text
        corpo = resp.json()
        assert corpo["codice"] == "SEC-006"
        assert corpo["dettagli"]["ammesse"] == [".png"]
        assert corpo["dettagli"]["origine"] == "classe"
        assert _chiavi(s3) == []

    async def test_estensioni_della_classe_tiff_e_registro_senza_restrizione(
        self, db_url, s3, utente_di
    ):
        """Classe TIFF: .tif ammessa (registro: image/tiff → .tif .tiff), .pdf no; classe
        senza formati: tutte le estensioni del registro; RGL-105 in avviso: registro."""
        tenant, classe = await crea_tenant_e_classe(db_url, formati='["image/tiff"]')
        utente_di(tenant)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.json()["dettagli"]["ammesse"] == [".tif", ".tiff"]
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, classe_id, "
                          "codice, valore, motivo) VALUES ('ENTE', $1, NULL, "
                          "'REGOLA.RGL-105.EFFETTO', '\"AVVISA\"'::jsonb, 'prova')", tenant)
        resp = await _versa(_indice(numero="FT-002"), [("fattura.pdf", PDF)])
        assert resp.status_code == 202, resp.text
        await sql(db_url, "UPDATE public.classi_documentali SET formati_ammessi = '[]'::jsonb "
                          "WHERE id = $1", classe)
        resp = await _versa(_indice("x.jpeg", numero="FT-003"), [("x.jpeg", PDF)])
        # estensione ammessa dal registro; il formato vero viene dal contenuto (PDF,
        # come dichiarato nell'indice): l'estensione è solo un primo filtro
        assert resp.status_code == 202, resp.text

    async def test_dimensione_massima_della_classe_422_rgl104_streaming_interrotto(
        self, db_url, s3, utente_di
    ):
        tenant, classe = await crea_tenant_e_classe(db_url)
        await sql(
            db_url,
            "UPDATE public.classi_documentali SET max_dimensione_mb = 1 WHERE id = $1",
            classe,
        )
        utente_di(tenant)
        grande = PDF + b"0" * (1024 * 1024)
        resp = await _versa(_indice(contenuto=grande), [("fattura.pdf", grande)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-130"
        assert resp.json()["dettagli"]["rilievi"][0]["codice"] == "RGL-104"
        assert _chiavi(s3) == []

    async def test_dimensione_classe_in_avviso_per_ente(self, db_url, s3, utente_di):
        """Effetto di RGL-104 portato ad AVVISA per il cliente: accettato con avviso."""
        tenant, classe = await crea_tenant_e_classe(db_url)
        await sql(
            db_url,
            "UPDATE public.classi_documentali SET max_dimensione_mb = 1 WHERE id = $1",
            classe,
        )
        await sql(
            db_url,
            "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
            "valore) VALUES ('ENTE', $1, 'REGOLA.RGL-104.EFFETTO', '\"AVVISA\"')",
            tenant,
        )
        utente_di(tenant)
        grande = PDF + b"0" * (1024 * 1024)
        resp = await _versa(_indice(contenuto=grande), [("fattura.pdf", grande)])
        assert resp.status_code == 202, resp.text
        assert [a["codice"] for a in resp.json()["avvisi"]] == ["RGL-104"]

    async def test_regola_non_previsti_bloccante_per_classe(self, db_url, s3, utente_di):
        tenant, classe = await crea_tenant_e_classe(db_url)
        await sql(
            db_url,
            "INSERT INTO public.impostazioni_valori (livello, classe_id, codice, "
            "valore) VALUES ('CLASSE', $1, 'REGOLA.RGL-103.EFFETTO', '\"BLOCCA\"')",
            classe,
        )
        utente_di(tenant)
        resp = await _versa(_indice(extra={"importo": "1"}), [("fattura.pdf", PDF)])
        assert resp.status_code == 422
        assert resp.json()["dettagli"]["rilievi"][0]["codice"] == "RGL-103"

    async def test_regola_locked_non_disattivabile_dall_ente(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        await sql(
            db_url,
            "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
            "valore) VALUES ('ENTE', $1, 'REGOLA.RGL-101.EFFETTO', '\"IGNORA\"')",
            tenant,
        )
        utente_di(tenant)
        resp = await _versa(_indice(con_parametri=False), [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-130"

    async def test_configurazione_regole_non_valida_503(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        await sql(
            db_url,
            "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
            "valore) VALUES ('ENTE', $1, 'REGOLA.RGL-103.EFFETTO', '\"FORSE\"')",
            tenant,
        )
        utente_di(tenant)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 503 and resp.json()["codice"] == "RGL-503"
        assert "FORSE" not in resp.text

    async def test_profilo_di_metadati_tipi_obbligatori_e_fotografia(self, db_url, s3, utente_di):
        tenant, classe = await crea_tenant_e_classe(db_url)
        campi = [
            {"nome": "importo", "tipo": "decimale", "obbligatorio": True},
            {"nome": "cf_cliente", "tipo": "codice_fiscale", "sensibile": True},
        ]
        await sql(
            db_url,
            "INSERT INTO public.profili_metadati (classe_id, versione, valido_dal, "
            "campi) VALUES ($1, 2, '2020-01-01', CAST($2 AS jsonb))",
            classe,
            json.dumps(campi),
        )
        utente_di(tenant)
        mancante = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert [r["codice"] for r in mancante.json()["dettagli"]["rilievi"]] == ["RGL-102"]
        errato = await _versa(
            _indice(extra={"importo": "dieci", "cf_cliente": "RSSMRA85T10A562X"}),
            [("fattura.pdf", PDF)],
        )
        assert errato.status_code == 422
        assert {r["campo"] for r in errato.json()["dettagli"]["rilievi"]} == {
            "importo",
            "cf_cliente",
        }
        assert "RSSMRA85T10A562X" not in errato.text  # valore sensibile mai nella risposta
        ok = await _versa(
            _indice(extra={"importo": "10.50", "cf_cliente": "RSSMRA85T10A562S"}),
            [("fattura.pdf", PDF)],
        )
        assert ok.status_code == 202 and ok.json()["avvisi"] == []  # campi del profilo: previsti
        regole = json.loads(
            (await sql(db_url, "SELECT regole_applicate FROM public.pdv"))[0]["regole_applicate"]
        )
        assert regole["profilo_metadati"]["versione"] == 2

    # ── Guasti e concorrenza ─────────────────────────────────────

    async def test_versamento_s3_giu_503_niente_pdv(self, db_url, s3, utente_di, monkeypatch):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)

        async def s3_giu(*_a, **_k):
            raise ErroreStorageFlusso("S3 down")

        monkeypatch.setattr(vs, "carica_flusso", s3_giu)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 503 and resp.json()["codice"] == "VRS-500"
        assert "S3 down" not in resp.text  # nessun dettaglio interno al client
        assert await sql(db_url, "SELECT 1 FROM public.pdv") == []

    async def test_versamento_db_fallisce_dopo_upload_file_eliminati(
        self, db_url, s3, utente_di, monkeypatch
    ):
        """VD-C-015: transazione fallita → nessun PdV, nessun evento, file compensati."""
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)

        async def accoda_guasta(*_a, **_k):
            raise OperationalError("INSERT", {}, Exception("connessione persa"))

        monkeypatch.setattr(vs, "accoda_evento", accoda_guasta)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 503 and resp.json()["codice"] == "VRS-501"
        assert await sql(db_url, "SELECT 1 FROM public.pdv") == []
        assert await sql(db_url, "SELECT 1 FROM public.outbox_eventi") == []
        assert _chiavi(s3) == []

    async def test_versamenti_uguali_in_parallelo_uno_solo_accettato(self, db_url, s3, utente_di):
        """§12.6: due richieste con lo stesso indice e chiavi diverse (due connessioni) → un PdV
        e un 409; i file del perdente sono compensati."""
        import asyncio

        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        risposte = await asyncio.gather(
            _versa(_indice(), [("fattura.pdf", PDF)]),
            _versa(_indice(), [("fattura.pdf", PDF)]),
        )
        assert sorted(r.status_code for r in risposte) == [202, 409]
        assert len(await sql(db_url, "SELECT 1 FROM public.pdv")) == 1
        assert len(_chiavi(s3)) == 1

    async def test_senza_chiave_in_parallelo_stesso_pdv(
        self, db_url, s3, utente_di, transizione_aperta
    ):
        """Senza chiave la chiave derivata è la stessa: due richieste uguali → un PdV, 202 e 200."""
        import asyncio

        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        risposte = await asyncio.gather(
            _versa(_indice(), [("fattura.pdf", PDF)], chiave=None),
            _versa(_indice(), [("fattura.pdf", PDF)], chiave=None),
        )
        assert sorted(r.status_code for r in risposte) == [200, 202]
        assert len({r.json()["pdv_id"] for r in risposte}) == 1
        assert len(_chiavi(s3)) == 1

    # ── Caso d'uso con FileGiaCaricato (interfaccia per le sessioni) ──

    async def test_versa_file_gia_caricato_e_azione_in_transazione(self, db_url, s3):
        tenant, _ = await crea_tenant_e_classe(db_url)
        fonte = _gia_caricato(s3, tenant)
        chiamate: list[str] = []

        async def segna_consumato(session, pdv_id: str) -> None:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
            chiamate.append(pdv_id)

        # R05 (VD-C-080): caso d'uso chiamato direttamente con l'ambito dell'ente,
        # come farebbe il canale autenticato
        with ambito_ente(tenant):
            esito = await vs.versa(
                vs.RichiestaVersamento(
                    indice=IndiceUD.model_validate(_indice()),
                    fonti=[fonte],
                    identita=vs.IdentitaVersante(tenant, uuid.UUID(UTENTE), vs.Canale.SFTP),
                    idempotency_key="sessione-0001",
                    adesso=datetime.now(UTC),
                    in_transazione=[segna_consumato],
                )
            )
        assert esito.nuovo and chiamate == [esito.pdv_id]
        payload = await _payload(db_url, esito.pdv_id)
        doc = payload["docs_s3_keys"][0]
        assert doc["s3_key"] == fonte.chiave and doc["caricamento_id"] == "car-1"
        assert payload["canale"] == "SFTP"

    async def test_versa_file_gia_caricato_rifiutato_non_elimina_l_oggetto(self, db_url, s3):
        tenant, _ = await crea_tenant_e_classe(db_url)
        fonte = _gia_caricato(s3, tenant)
        with pytest.raises(vs.RifiutoVersamento) as err:
            await vs.versa(
                vs.RichiestaVersamento(
                    indice=IndiceUD.model_validate(_indice(impronta="0" * 64)),
                    fonti=[fonte],
                    identita=vs.IdentitaVersante(tenant, uuid.UUID(UTENTE), vs.Canale.REST),
                    idempotency_key="sessione-0002",
                    adesso=datetime.now(UTC),
                )
            )
        assert err.value.codice == "VRS-102"
        assert _chiavi(s3) == [fonte.chiave]  # l'oggetto resta alla sua sessione
