# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Integrazione su PostgreSQL vero (migrato) e S3 (moto con Object Lock):
costruzione del PdA con impronte ricalcolate (VD-C-008), idempotenza alle
riconsegne e stato di errore esplicito (VD-C-009, VD-C-012).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from app.config import settings
from app.services import s3_uploader
from app.services.aip_engine import costruisci_pda, id_pda_per_pdv, segna_pdv_in_errore

from libs.shared.kafka.consumatore_affidabile import ErrorePermanente
from tests.infra_reale import client_s3, crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture

pytestmark = [pytest.mark.integration, richiede_pg]

ADESSO = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"


@pytest.fixture
def s3(s3_server, monkeypatch):
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    s3_uploader._client.cache_clear()
    client = client_s3(s3_server)
    client.create_bucket(Bucket="pdv-transito")
    yield client
    s3_uploader._client.cache_clear()


async def _pdv_validato(url: str, s3, *, contenuto: bytes = PDF, algoritmo: str = "SHA-512",
                        impronta: str | None = None, carica: bool = True) -> dict:
    tenant, classe = await crea_tenant_e_classe(url)
    pdv = uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                   "VALUES ($1, 'prova', 'VALIDATO', $2, $3, $4)", pdv, classe, uuid.uuid4(),
                   tenant)
    chiave = f"{tenant}/{pdv}/0000-doc.pdf"
    if carica:
        s3.put_object(Bucket="pdv-transito", Key=chiave, Body=contenuto)
    funzione = {"SHA-256": hashlib.sha256, "SHA-512": hashlib.sha512,
                "SHA3-256": hashlib.sha3_256}[algoritmo]
    return {
        "pdv_id": str(pdv), "tenant_id": str(tenant),
        "indice": {"classe_documentale": "fattura", "chiave": None, "documenti": [{
            "id": "d1", "tipo": "PRINCIPALE", "nome_file": "doc.pdf", "formato": "application/pdf",
            "hash": {
                "algoritmo": algoritmo, "impronta": impronta or funzione(contenuto).hexdigest()},
        }]},
        "docs_s3_keys": [{"doc_id": "d1", "nome_file": "doc.pdf", "s3_key": chiave,
                          "s3_bucket": "pdv-transito", "size_bytes": len(contenuto)}],
        # Classe «fattura» → IPdA DAI: Allegato 5 del principale obbligatorio (VD-C-037)
        "allegato5": {"d1": {
            "identificativo_documento": "FT-1", "modalita_formazione": "b",
            "tipologia_documentale": "fattura", "data_documento": "2026-03-19",
            "soggetto_produttore_id": "12345678901", "soggetto_produttore_nome": "Acme S.r.l.",
            "oggetto": "Fattura di prova",
        }},
    }


async def test_costruisci_pda_impronte_ricalcolate_e_su_storage(db_url, s3):
    """PdA creato con SHA-256/512 calcolati sui byte; oggetti WORM con retention."""
    evento = await _pdv_validato(db_url, s3)
    uscita = await costruisci_pda(evento, adesso=ADESSO)

    aip = await sql(db_url, "SELECT stato::text AS stato, urn FROM public.aip WHERE pdv_id = $1",
                    uuid.UUID(evento["pdv_id"]))
    assert aip[0]["stato"] == "SU_STORAGE"
    doc = await sql(db_url, "SELECT hash_sha256, hash_sha512, dimensione_bytes, s3_key "
                            "FROM public.documenti WHERE aip_id = $1", uuid.UUID(uscita["aip_id"]))
    assert doc[0]["hash_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert doc[0]["hash_sha512"] == hashlib.sha512(PDF).hexdigest()
    assert doc[0]["dimensione_bytes"] == len(PDF)
    testa = s3.head_object(Bucket=settings.s3_bucket_aip, Key=doc[0]["s3_key"])
    assert testa["ObjectLockMode"] == "COMPLIANCE"
    stato = await sql(db_url, "SELECT stato::text AS s FROM public.pdv WHERE id = $1",
                      uuid.UUID(evento["pdv_id"]))
    assert stato[0]["s"] == "IN_BUILD"


async def test_costruisci_pda_impronta_dichiarata_falsa_nessun_pda(db_url, s3):
    """SHA-512 dichiarato falso: errore permanente di integrità, nessun AIP creato."""
    evento = await _pdv_validato(db_url, s3, impronta="ab" * 64)
    with pytest.raises(ErrorePermanente) as err:
        await costruisci_pda(evento, adesso=ADESSO)
    assert err.value.codice == "AIP-INTEGRITA-002"
    assert await sql(db_url, "SELECT 1 FROM public.aip WHERE pdv_id = $1",
                     uuid.UUID(evento["pdv_id"])) == []


async def test_costruisci_pda_file_non_scaricabile_nessun_pda(db_url, s3):
    """VD-C-008: file assente nello storage → eccezione (ritentabile), nessun AIP SU_STORAGE."""
    evento = await _pdv_validato(db_url, s3, carica=False)
    with pytest.raises(Exception) as err:
        await costruisci_pda(evento, adesso=ADESSO)
    assert not isinstance(err.value, ErrorePermanente)
    assert await sql(db_url, "SELECT 1 FROM public.aip WHERE pdv_id = $1",
                     uuid.UUID(evento["pdv_id"])) == []


async def test_costruisci_pda_riconsegna_nessun_duplicato(db_url, s3):
    """La riconsegna di un PdA già costruito non duplica PdA, albero né coda del
    registro e restituisce la stessa radice."""
    evento = await _pdv_validato(db_url, s3)
    prima = await costruisci_pda(evento, adesso=ADESSO)
    uscita = await costruisci_pda(evento, adesso=ADESSO)
    assert uscita["aip_id"] == id_pda_per_pdv(evento["pdv_id"]) == prima["aip_id"]
    assert uscita["radice_pda"] == prima["radice_pda"]
    for tabella in ("aip", "catena_pda", "catena_coda"):
        colonna = "pdv_id" if tabella == "aip" else "aip_id"
        valore = uuid.UUID(evento["pdv_id"] if tabella == "aip" else uscita["aip_id"])
        assert len(await sql(db_url, f"SELECT 1 FROM public.{tabella} WHERE {colonna} = $1",
                             valore)) == 1, tabella


async def test_costruisci_pda_albero_k1_coda_e_nessun_evento_conservato(db_url, s3):
    """VD-C-097 e contratto K1: alla costruzione il PdA resta SU_STORAGE, nessun
    evento «conservato» parte; albero di livello 1 (foglia del file con impronta
    calcolata e metadati, foglia dell'IPdA per ultima) in DB e nel WORM, PdA in
    coda per il registro."""
    from app.services import aip_engine

    from libs.catena.foglie import albero_pda_da_foglie, impronta_metadati

    pubblicati: list = []

    async def registra(*argomenti):
        pubblicati.append(argomenti)

    originale = aip_engine._pubblica
    aip_engine._pubblica = registra
    try:
        evento = await _pdv_validato(db_url, s3)
        uscita = await costruisci_pda(evento, adesso=ADESSO)
    finally:
        aip_engine._pubblica = originale
    assert pubblicati == []
    riga = (await sql(db_url, "SELECT urn, radice, num_foglie, foglie FROM public.catena_pda "
                              "WHERE aip_id = $1", uuid.UUID(uscita["aip_id"])))[0]
    foglie = json.loads(riga["foglie"])
    assert riga["num_foglie"] == 2 and riga["radice"] == uscita["radice_pda"]
    assert albero_pda_da_foglie(foglie).radice_hex == riga["radice"]
    documento, ipda = json.loads(foglie[0]), json.loads(foglie[1])
    assert documento == {"v": 1, "pda": riga["urn"], "doc": "d1", "file": "doc.pdf",
                         "sha256": hashlib.sha256(PDF).hexdigest(),
                         "metadati": impronta_metadati(evento["allegato5"]["d1"])}
    sincro = (await sql(db_url, "SELECT s3_bucket, sincro_xml_key FROM public.aip WHERE id = $1",
                        uuid.UUID(uscita["aip_id"])))[0]
    xml = s3.get_object(Bucket=sincro["s3_bucket"], Key=sincro["sincro_xml_key"])["Body"].read()
    assert ipda == {"v": 1, "pda": riga["urn"], "ipda": hashlib.sha256(xml).hexdigest()}
    worm = s3.get_object(Bucket=settings.s3_bucket_aip,
                         Key=f"{evento['tenant_id']}/{uscita['aip_id']}/catena_pda.json")
    assert json.loads(worm["Body"].read()) == {"v": 1, "pda": riga["urn"],
                                               "radice": riga["radice"], "foglie": foglie}
    assert worm["ObjectLockMode"] == "COMPLIANCE"
    assert len(await sql(db_url, "SELECT 1 FROM public.catena_coda WHERE aip_id = $1",
                         uuid.UUID(uscita["aip_id"]))) == 1


async def test_costruisci_pda_ripreso_dopo_caricamento_parziale(db_url, s3):
    """Oggetti già caricati da un tentativo precedente: accettati perché identici."""
    evento = await _pdv_validato(db_url, s3)
    originale = s3_uploader.upload_aip_to_s3
    chiamate = {"n": 0}

    async def guasto_al_secondo(**kw):
        chiamate["n"] += 1
        if chiamate["n"] == 2:
            raise ConnectionError("rete")
        return await originale(**kw)

    import app.services.aip_engine as motore
    motore.upload_aip_to_s3 = guasto_al_secondo
    try:
        with pytest.raises(ConnectionError):
            await costruisci_pda(evento, adesso=ADESSO)
    finally:
        motore.upload_aip_to_s3 = originale
    urn_prima = json.loads((await sql(db_url, "SELECT metadata_json FROM public.pdv WHERE id = $1",
                                      uuid.UUID(evento["pdv_id"])))[0]["metadata_json"])
    uscita = await costruisci_pda(evento,
                                  adesso=datetime(2026, 10, 21, tzinfo=UTC))
    assert uscita["urn"] == urn_prima["costruzione_pda"]["urn"]


async def test_costruisci_pda_pdv_non_validato_rifiutato(db_url, s3):
    evento = await _pdv_validato(db_url, s3)
    await sql(db_url, "UPDATE public.pdv SET stato = 'RICEVUTO' WHERE id = $1",
              uuid.UUID(evento["pdv_id"]))
    with pytest.raises(ErrorePermanente) as err:
        await costruisci_pda(evento, adesso=ADESSO)
    assert err.value.codice == "AIP-PDV-002"


async def test_segna_pdv_in_errore_stato_esplicito(db_url, s3):
    evento = await _pdv_validato(db_url, s3)
    assert await segna_pdv_in_errore(evento["pdv_id"], evento["tenant_id"],
                                     {"codice": "TECNICO", "messaggio": "S3 giù"})
    riga = (await sql(db_url, "SELECT stato::text AS s, errori_validazione FROM public.pdv "
                              "WHERE id = $1", uuid.UUID(evento["pdv_id"])))[0]
    assert riga["s"] == "ERRORE"
    assert json.loads(riga["errori_validazione"])[-1]["messaggio"] == "S3 giù"


async def _impronta_attiva(url: str, evento: dict, numero: str) -> None:
    await sql(url, "INSERT INTO public.versamento_impronte (pdv_id, tenant_id, impronta_indice, "
                   "impronta_richiesta, chiave_idempotenza, chiave_ud_registro, chiave_ud_anno, "
                   "chiave_ud_numero) VALUES ($1, $2, $3, $3, $4, 'DELIBERE', '2026', $5)",
              uuid.UUID(evento["pdv_id"]), uuid.UUID(evento["tenant_id"]),
              hashlib.sha256(numero.encode()).hexdigest(), f"chiave-{numero}", numero)


async def _attivo(url: str, evento: dict) -> bool:
    return (await sql(url, "SELECT attivo FROM public.versamento_impronte WHERE pdv_id = $1",
                      uuid.UUID(evento["pdv_id"])))[0]["attivo"]


async def test_segna_pdv_in_errore_libera_la_chiave_ud(db_url, s3):
    """R03 i3 (richiesta 3 di f1): il PdV portato in ERRORE dalla costruzione del PdA
    libera chiave UD e impronta (come validation per i rifiuti), così il
    riversamento corretto della stessa UD non riceve VRS-124. Un PdV in uno stato
    non applicabile (nessuna transizione) non tocca la sua riga."""
    in_errore = await _pdv_validato(db_url, s3)
    await _impronta_attiva(db_url, in_errore, "45")
    conservato = await _pdv_validato(db_url, s3)
    await _impronta_attiva(db_url, conservato, "46")
    await sql(db_url, "UPDATE public.pdv SET stato = 'CONSERVATO' WHERE id = $1",
              uuid.UUID(conservato["pdv_id"]))

    assert await segna_pdv_in_errore(in_errore["pdv_id"], in_errore["tenant_id"],
                                     {"codice": "AIP-X", "messaggio": "permanente"})
    assert not await segna_pdv_in_errore(conservato["pdv_id"], conservato["tenant_id"],
                                         {"codice": "AIP-X", "messaggio": "permanente"})
    assert await _attivo(db_url, in_errore) is False
    assert await _attivo(db_url, conservato) is True


@pytest.mark.parametrize("allegato5", [{}, {"d1": {
    "identificativo_documento": "FT-1", "modalita_formazione": "INFORMATICA_AB_ORIGINE"}}])
async def test_costruisci_pda_metadati_mancanti_errore_prima_dello_storage(db_url, s3, allegato5):
    """VD-C-037: modalità mancante o vecchia → errore permanente PRIMA di scrivere
    oggetti WORM (nessun oggetto sotto retention senza PdA); PdV non toccato."""
    evento = {**await _pdv_validato(db_url, s3), "allegato5": allegato5}
    with pytest.raises(ErrorePermanente) as errore:
        await costruisci_pda(evento, adesso=ADESSO)
    assert errore.value.codice == "AIP-METADATI-001"
    assert await sql(db_url, "SELECT 1 FROM public.aip WHERE pdv_id = $1",
                     uuid.UUID(evento["pdv_id"])) == []
    stato = await sql(db_url, "SELECT stato::text AS s FROM public.pdv WHERE id = $1",
                      uuid.UUID(evento["pdv_id"]))
    assert stato[0]["s"] == "VALIDATO"
    try:
        oggetti = s3.list_objects_v2(Bucket=settings.s3_bucket_aip).get("Contents", [])
    except s3.exceptions.NoSuchBucket:
        oggetti = []
    assert not [o for o in oggetti if evento["tenant_id"] in o["Key"]]


# ── R03: tipologia dell'IPdA dalla classe documentale (tipologia_xml) ──

async def _imposta_tipologia(url: str, evento: dict, tipologia: str) -> None:
    await sql(url, "UPDATE public.classi_documentali c SET tipologia_xml = "
                   "CAST($2 AS public.tipo_sincro_xml) FROM public.pdv p "
                   "WHERE p.id = $1 AND c.id = p.classe_doc_id",
              uuid.UUID(evento["pdv_id"]), tipologia)


async def test_costruisci_pda_tipologia_dalla_classe_non_dal_nome(db_url, s3):
    """La classe «fattura» configurata DI produce un IPdA DI (prima: DAI dal nome)."""
    evento = await _pdv_validato(db_url, s3)
    await _imposta_tipologia(db_url, evento, "DI")
    try:
        uscita = await costruisci_pda(evento, adesso=ADESSO)
    finally:
        await _imposta_tipologia(db_url, evento, "DAI")
    riga = await sql(db_url, "SELECT s3_bucket, sincro_xml_key FROM public.aip WHERE id = $1",
                     uuid.UUID(uscita["aip_id"]))
    xml = s3.get_object(Bucket=riga[0]["s3_bucket"], Key=riga[0]["sincro_xml_key"])["Body"].read()
    import xml.etree.ElementTree as ET

    assert ET.fromstring(xml).get("tipo") == "DI"


async def test_costruisci_pda_tipologia_dai_della_classe_richiede_allegato5(db_url, s3):
    """Classe DAI senza Allegato 5 del principale → errore prima dello storage."""
    evento = {**await _pdv_validato(db_url, s3), "allegato5": {}}
    await _imposta_tipologia(db_url, evento, "DAI")
    with pytest.raises(ErrorePermanente) as errore:
        await costruisci_pda(evento, adesso=ADESSO)
    assert errore.value.codice == "AIP-METADATI-001"
    # con la classe DI lo stesso PdV senza Allegato 5 si costruisce
    await _imposta_tipologia(db_url, evento, "DI")
    try:
        uscita = await costruisci_pda(evento, adesso=ADESSO)
    finally:
        await _imposta_tipologia(db_url, evento, "DAI")
    assert uscita["aip_id"] == id_pda_per_pdv(evento["pdv_id"])


# ── R03 b4: metadati Allegato 5 e coda d'indicizzazione (VD-C-093) ────

async def test_costruisci_pda_popola_metadati_allegato5_e_coda(db_url, s3):
    """Il PdA scrive metadati_allegato5 (con extra e profilo) e la riga in coda,
    nella stessa transazione; una riconsegna non duplica."""
    evento = await _pdv_validato(db_url, s3)
    evento["allegato5"]["d1"].update({
        "numero_registrazione": "PG/2026/1", "soggetto_destinatario": ["Mario Rossi"],
        "importo": "12.50", "metadati_extra": {"cig": "ZX123"},
    })
    uscita = await costruisci_pda(evento, adesso=ADESSO)
    righe = await sql(db_url,
                      "SELECT m.identificativo_documento, m.modalita_formazione::text AS mod, "
                      "m.oggetto, m.numero_registrazione, m.formato, m.metadati_extra, "
                      "m.soggetto_destinatario, m.data_documento "
                      "FROM public.metadati_allegato5 m JOIN public.documenti d "
                      "ON d.id = m.documento_id WHERE d.aip_id = $1", uuid.UUID(uscita["aip_id"]))
    assert len(righe) == 1
    r = righe[0]
    assert r["identificativo_documento"] == "FT-1" and r["mod"] == "b"
    assert r["numero_registrazione"] == "PG/2026/1" and r["formato"] == "application/pdf"
    assert json.loads(r["metadati_extra"]) == {"cig": "ZX123", "importo": "12.50"}
    assert json.loads(r["soggetto_destinatario"]) == ["Mario Rossi"]
    assert r["data_documento"].isoformat() == "2026-03-19"
    coda = await sql(db_url, "SELECT stato, tenant_id FROM public.indicizzazione_aip "
                             "WHERE aip_id = $1", uuid.UUID(uscita["aip_id"]))
    assert coda[0]["stato"] == "IN_CODA" and str(coda[0]["tenant_id"]) == evento["tenant_id"]
    # riconsegna: nessuna riga in più
    await costruisci_pda(evento, adesso=ADESSO)
    assert len(await sql(
        db_url, "SELECT 1 FROM public.metadati_allegato5 m JOIN public.documenti d "
                                 "ON d.id = m.documento_id WHERE d.aip_id = $1",
                         uuid.UUID(uscita["aip_id"]))) == 1


async def test_costruisci_pda_ricerca_del_portale_trova_i_metadati(db_url, s3):
    """La ricerca SQL del portale (dashboard-api) interroga gli stessi campi: il
    dato ora c'è (prima la tabella era sempre vuota)."""
    evento = await _pdv_validato(db_url, s3)
    uscita = await costruisci_pda(evento, adesso=ADESSO)
    trovati = await sql(db_url,
                        "SELECT d.aip_id FROM public.documenti d JOIN public.metadati_allegato5 m "
                        "ON m.documento_id = d.id WHERE m.soggetto_produttore_nome ILIKE $1",
                        "%acme%")
    assert uuid.UUID(uscita["aip_id"]) in [t["aip_id"] for t in trovati]


async def test_r13a_ipda_accodato_per_il_sigillo_nella_transazione_del_pda(db_url, s3):  # noqa: F811
    """R13a (VD-C-068): con il PdA nasce la riga dell'IPdA da sigillare, con bucket,
    chiave, versione e impronta dell'oggetto scritto; la riconsegna non la duplica."""
    evento = await _pdv_validato(db_url, s3)
    uscita = await costruisci_pda(evento, adesso=ADESSO)
    await costruisci_pda(evento, adesso=ADESSO)
    righe = await sql(db_url, "SELECT tipo, tenant_id, nome, origine_bucket, origine_chiave, "
                              "origine_version_id, impronta_sha256, stato FROM "
                              "public.firme_documenti WHERE aip_id = $1",
                      uuid.UUID(uscita["aip_id"]))
    assert len(righe) == 1
    r = righe[0]
    assert (r["tipo"], r["stato"], r["nome"]) == ("IPDA", "DA_FIRMARE", "IndiceAIP_SInCRO.xml")
    assert str(r["tenant_id"]) == evento["tenant_id"]
    oggetto = s3.get_object(Bucket=r["origine_bucket"], Key=r["origine_chiave"],
                            **({"VersionId": r["origine_version_id"]}
                               if r["origine_version_id"] else {}))
    assert hashlib.sha256(oggetto["Body"].read()).hexdigest() == r["impronta_sha256"].strip()
