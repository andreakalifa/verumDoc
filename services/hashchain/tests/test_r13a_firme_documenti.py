# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13a (VD-C-068, VD-C-124) — documenti del conservatore da sigillare e ancoraggio
dei checkpoint, su PostgreSQL vero e S3 (moto con Object Lock).

  - IPdA: origine riletta alla versione registrata e impronta riverificata; busta
    nel WORM (``Sigillo_*``) con la retention del PdA; riga FIRMATO con i dati
    della firma;
  - senza fornitore: NON_FIRMATO (predefinito) o DA_FIRMARE con BLOCCA, e in quel
    caso il PdA non va a CONSERVATO (transizione della migrazione 054);
  - recupero dei NON_FIRMATO quando il fornitore c'è; fencing (lease del registro);
  - rimarcatura prima delle scadenze o con algoritmo tolto, nuova busta;
  - marca dell'ultimo checkpoint, precedenti SUPERATO, marca immutabile;
  - revisione R13a (1): firma non deterministica scritta nel WORM e poi lease perso
    (o crash) prima dell'UPDATE → la nuova firma va su una chiave nuova (impronta
    della busta nella chiave) e il documento si sigilla; un errore su un documento
    non ferma gli altri né resta in testa alla coda.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.registro import archivio, firme_documenti
from app.registro.metriche import metriche
from app.services import block_sealer
from app.services.firma_blocchi import (
    STATO_DA_RITENTARE,
    STATO_FIRMATO,
    STATO_MARCATO,
    STATO_NON_FIRMATO,
    EsitoFirmaBlocco,
    EsitoMarca,
)

from libs.catena import nota
from libs.shared.db import close_db, get_async_session, init_db
from libs.shared.parametri import svuota_cache
from tests.infra_reale import (  # noqa: F401
    client_s3,
    crea_tenant_e_classe,
    db_url,
    richiede_pg,
    s3_server,
    sql,
)
from tests.supporto_registro import crea_pda

pytestmark = [pytest.mark.integration, richiede_pg]

ADESSO = datetime.now(UTC).replace(microsecond=0)
IO = "detentore-r13a"


class FirmaFinta:
    """Servizio di firma finto con l'interfaccia R13a (firma, marca, rimarca)."""

    def __init__(self, stato: str = STATO_FIRMATO, scadenza: datetime | None = None) -> None:
        self.stato = stato
        self.scadenza = scadenza or ADESSO + timedelta(days=700)
        self.firmati: list[bytes] = []
        self.rimarcati: list[bytes] = []
        self.marche: list[bytes] = []

    async def firma(self, contenuto: bytes, *, formato: str = "cades_detached",
                    livello: str | None = None) -> EsitoFirmaBlocco:
        self.firmati.append(contenuto)
        if self.stato == STATO_FIRMATO:
            return EsitoFirmaBlocco(
                STATO_FIRMATO, None, firma_b64=base64.b64encode(b"P7M" + contenuto).decode(),
                tsa_timestamp=ADESSO, dettagli={
                    "livello": "B-LT", "formato": "CAdES", "codice_fornitore": "csc-prova",
                    "fornitore_marca": "tsa-prova", "algoritmo": "sha256",
                    "certificato_sha256": "ab" * 32, "firmatario": "Sigillo di prova",
                    "certificato_scadenza": self.scadenza.isoformat(),
                    "marca_scadenza": (ADESSO + timedelta(days=900)).isoformat()})
        if self.stato == STATO_DA_RITENTARE:
            return EsitoFirmaBlocco(STATO_DA_RITENTARE, "servizio non disponibile")
        return EsitoFirmaBlocco(STATO_NON_FIRMATO, "non firmato: servizio di firma non "
                                                   "configurato")

    async def rimarca(self, busta: bytes, contenuto: bytes | None = None) -> EsitoFirmaBlocco:
        self.rimarcati.append(busta)
        return EsitoFirmaBlocco(STATO_FIRMATO, None,
                                firma_b64=base64.b64encode(busta + b"+ATS").decode(),
                                dettagli={"livello": "B-LTA", "algoritmo": "sha512",
                                          "marca_scadenza": (ADESSO + timedelta(days=3000)
                                                             ).isoformat()})

    async def marca(self, impronta: bytes) -> EsitoMarca:
        self.marche.append(impronta)
        if self.stato != STATO_FIRMATO:
            return EsitoMarca("NON_MARCATO", "non marcato: marca temporale non configurata")
        return EsitoMarca(STATO_MARCATO, None, token_b64=base64.b64encode(b"TSR" + impronta)
                          .decode(), istante=ADESSO, tsa="tsa-prova")


@pytest.fixture
def s3(s3_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_aip", f"aip-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(settings, "retention_permanente_orizzonte_anni", 30)
    block_sealer._client.cache_clear()
    client = client_s3(s3_server)
    client.create_bucket(Bucket=settings.s3_bucket_aip, ObjectLockEnabledForBucket=True)
    yield client
    block_sealer._client.cache_clear()


@pytest.fixture
async def db(db_url):  # noqa: F811
    await init_db(db_url)
    svuota_cache()
    async with get_async_session() as session:
        assert await archivio.acquisisci_lease(session, IO, ADESSO, ADESSO + timedelta(hours=1))
    yield db_url
    svuota_cache()
    await close_db()


async def _parametro(url: str, codice: str, valore) -> None:  # type: ignore[no-untyped-def]
    await sql(url, "DELETE FROM public.impostazioni_valori WHERE livello = 'CONSERVATORE' "
                   "AND tenant_id IS NULL AND classe_id IS NULL AND codice = $1", codice)
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ('CONSERVATORE', NULL, $1, CAST($2 AS jsonb), 'prova R13a')",
              codice, json.dumps(valore))
    svuota_cache()


async def _ipda(db: str, s3, *, altera: bool = False):  # type: ignore[no-untyped-def]
    tenant, classe = await crea_tenant_e_classe(db)
    pda = await crea_pda(db, tenant, classe)
    contenuto = f"<IndiceAIP>{pda.aip_id}</IndiceAIP>".encode()
    chiave = f"{tenant}/{pda.aip_id}/IndiceAIP_SInCRO.xml"
    risposta = s3.put_object(Bucket=settings.s3_bucket_aip, Key=chiave, Body=contenuto)
    impronta = hashlib.sha256(contenuto + (b"x" if altera else b"")).hexdigest()
    await sql(db, "INSERT INTO public.firme_documenti (tipo, tenant_id, aip_id, nome, "
                  "origine_bucket, origine_chiave, origine_version_id, impronta_sha256) VALUES "
                  "('IPDA', $1, $2, 'IndiceAIP_SInCRO.xml', $3, $4, $5, $6)",
              tenant, pda.aip_id, settings.s3_bucket_aip, chiave, risposta.get("VersionId"),
              impronta)
    return pda, contenuto


async def _riga(db: str, aip_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return (await sql(db, "SELECT * FROM public.firme_documenti WHERE aip_id = $1",
                      aip_id))[0]


async def test_ipda_sigillato_busta_nel_worm_e_dati_della_firma(db, s3) -> None:
    pda, contenuto = await _ipda(db, s3)
    servizio = FirmaFinta()
    esito = await firme_documenti.sigilla(servizio, detentore=IO, adesso=ADESSO)
    assert esito["firmati"] == 1 and servizio.firmati == [contenuto]
    r = await _riga(db, pda.aip_id)
    assert r["stato"] == "FIRMATO" and r["livello"] == "B-LT" and r["qualificata"]
    sha = hashlib.sha256(b"P7M" + contenuto).hexdigest()
    assert r["busta_chiave"].endswith(
        f"{pda.aip_id}/Sigillo_IndiceAIP_SInCRO.xml.{sha[:24]}.p7m")
    oggetto = s3.get_object(Bucket=settings.s3_bucket_aip, Key=r["busta_chiave"])
    assert oggetto["Body"].read() == b"P7M" + contenuto
    assert oggetto["ObjectLockMode"] == "COMPLIANCE"
    assert r["fornitore_firma"] == "csc-prova" and r["certificato_scadenza"] is not None


async def test_origine_diversa_non_si_firma(db, s3) -> None:
    pda, _ = await _ipda(db, s3, altera=True)
    servizio = FirmaFinta()
    prima = metriche().firme_origine_diversa._value.get()
    esito = await firme_documenti.sigilla(servizio, detentore=IO, adesso=ADESSO)
    assert esito["origine_diversa"] == 1 and servizio.firmati == []
    assert metriche().firme_origine_diversa._value.get() == prima + 1
    r = await _riga(db, pda.aip_id)
    assert r["stato"] == "DA_FIRMARE" and "origine non verificata" in r["motivo"]


async def test_senza_fornitore_non_firmato_poi_recuperato(db, s3) -> None:
    pda, _ = await _ipda(db, s3)
    await firme_documenti.sigilla(FirmaFinta(STATO_NON_FIRMATO), detentore=IO, adesso=ADESSO)
    r = await _riga(db, pda.aip_id)
    assert r["stato"] == "NON_FIRMATO" and "non configurato" in r["motivo"]
    async with get_async_session() as session:  # predefinito: nessun blocco
        assert await firme_documenti.bloccati(session, [str(pda.aip_id)]) == set()
    # un'ora dopo il fornitore c'è: si firma (recupero)
    dopo = ADESSO + timedelta(hours=2)
    async with get_async_session() as session:
        await archivio.acquisisci_lease(session, IO, dopo, dopo + timedelta(hours=1))
    await firme_documenti.sigilla(FirmaFinta(), detentore=IO, adesso=dopo)
    assert (await _riga(db, pda.aip_id))["stato"] == "FIRMATO"


async def test_blocca_fail_closed_e_cancello_della_conservazione(db, s3) -> None:
    await _parametro(db, "FIRMA_SENZA_FORNITORE", "BLOCCA")
    pda, _ = await _ipda(db, s3)
    await firme_documenti.sigilla(FirmaFinta(STATO_NON_FIRMATO), detentore=IO, adesso=ADESSO)
    r = await _riga(db, pda.aip_id)
    assert r["stato"] == "DA_FIRMARE" and "BLOCCA" in r["motivo"]
    tenant, classe = await crea_tenant_e_classe(db, codice_classe="altra")
    senza_riga = await crea_pda(db, tenant, classe)
    in_transizione = await crea_pda(db, tenant, classe)
    await sql(db, "INSERT INTO public.firma_transizione_r13 (aip_id) VALUES ($1)",
              in_transizione.aip_id)
    async with get_async_session() as session:
        fermi = await firme_documenti.bloccati(session, [str(pda.aip_id),
                                                         str(senza_riga.aip_id),
                                                         str(in_transizione.aip_id)])
    assert fermi == {str(pda.aip_id), str(senza_riga.aip_id)}


async def test_senza_lease_nessuna_scrittura(db, s3) -> None:
    """Fencing: un processo che ha perso il lease del registro non scrive."""
    pda, _ = await _ipda(db, s3)
    with pytest.raises(archivio.LeasePerso):
        await firme_documenti.sigilla(FirmaFinta(), detentore="altro-processo", adesso=ADESSO)
    assert (await _riga(db, pda.aip_id))["stato"] == "DA_FIRMARE"


async def test_rimarcatura_prima_della_scadenza_e_per_algoritmo(db, s3) -> None:
    pda, _ = await _ipda(db, s3)
    await firme_documenti.sigilla(FirmaFinta(scadenza=ADESSO + timedelta(days=20)),
                                  detentore=IO, adesso=ADESSO)
    servizio = FirmaFinta()
    esito = await firme_documenti.rinnova(servizio, detentore=IO, adesso=ADESSO)
    assert esito["rinnovati"] == 1 and len(servizio.rimarcati) == 1
    r = await _riga(db, pda.aip_id)
    assert r["rinnovi"] == 1 and r["livello"] == "B-LTA" and r["algoritmo"] == "sha512"
    assert ".r1." in r["busta_chiave"] and r["busta_chiave"].endswith(".p7m")
    # dopo B-LTA conta solo la marca (lontana): nulla da rimarcare
    assert (await firme_documenti.rinnova(FirmaFinta(), detentore=IO, adesso=ADESSO)
            )["rinnovati"] == 0
    # sha512 tolto dagli algoritmi ammessi: si rimarca di nuovo
    await _parametro(db, "FIRMA_ALGORITMI_AMMESSI", ["sha256", "sha384"])
    await _parametro(db, "FIRMA_ALGORITMO_IMPRONTA", "sha256")
    assert (await firme_documenti.rinnova(FirmaFinta(), detentore=IO, adesso=ADESSO)
            )["rinnovati"] == 1
    assert (await _riga(db, pda.aip_id))["rinnovo_motivo"] == "algoritmo non più ammesso"


async def test_rimarcatura_busta_alterata_nello_storage_non_procede(db, s3) -> None:
    pda, _ = await _ipda(db, s3)
    await firme_documenti.sigilla(FirmaFinta(scadenza=ADESSO + timedelta(days=20)),
                                  detentore=IO, adesso=ADESSO)
    # manomissione da superutente (il trigger della migrazione 054 la vieterebbe)
    await sql(db, "ALTER TABLE public.firme_documenti DISABLE TRIGGER "
                  "trg_firme_documenti_protette")
    try:
        await sql(db, "UPDATE public.firme_documenti SET busta_sha256 = $1 WHERE aip_id = $2",
                  "00" * 32, pda.aip_id)
    finally:
        await sql(db, "ALTER TABLE public.firme_documenti ENABLE TRIGGER "
                      "trg_firme_documenti_protette")
    servizio = FirmaFinta()
    esito = await firme_documenti.rinnova(servizio, detentore=IO, adesso=ADESSO)
    assert esito["rinnovati"] == 0 and esito["errori"] and servizio.rimarcati == []


async def _checkpoint(db: str, n: int) -> None:
    privata, _ = nota.genera_chiave("verumdoc-registro")
    firmatario = nota.analizza_firmatario(privata)
    corpo = nota.formatta(nota.Checkpoint("verumdoc.it/registro/v1/test", n, bytes([n]) * 32))
    testo = nota.componi(corpo, [firmatario.firma(corpo)])
    await sql(db, "INSERT INTO public.catena_checkpoint (dimensione, radice, origine, testo, "
                  "chiave_verificatore, emesso_il, worm_bucket, worm_chiave, stato_sigillo, "
                  "stato_testimone) VALUES ($1, $2, 'verumdoc.it/registro/v1/test', $3, $4, "
                  "now(), 'b', 'k', 'NON_FIRMATO', 'NON_CONFIGURATO')",
              n, (bytes([n]) * 32).hex(), testo, firmatario.verificatore.testo)


async def test_marca_dell_ultimo_checkpoint_e_precedenti_superati(db, s3) -> None:
    for n in (1, 2, 3):
        await _checkpoint(db, n)
    servizio = FirmaFinta()
    assert await firme_documenti.marca_checkpoint(servizio, detentore=IO,
                                                  adesso=ADESSO) == STATO_MARCATO
    righe = {r["dimensione"]: r for r in await sql(
        db, "SELECT dimensione, stato_marca, marca_worm_chiave FROM public.catena_checkpoint")}
    assert righe[3]["stato_marca"] == "MARCATO"
    assert {righe[1]["stato_marca"], righe[2]["stato_marca"]} == {"SUPERATO"}
    assert s3.get_object(Bucket=settings.s3_bucket_aip,
                         Key=righe[3]["marca_worm_chiave"])["Body"].read().startswith(b"TSR")
    # entro l'intervallo nessuna nuova marca
    await _checkpoint(db, 4)
    assert await firme_documenti.marca_checkpoint(servizio, detentore=IO,
                                                  adesso=ADESSO + timedelta(minutes=5)) is None
    # la marca registrata non si modifica (funzione di protezione, migrazione 054)
    import asyncpg

    with pytest.raises(asyncpg.exceptions.RaiseError, match="marca del checkpoint"):
        await sql(db, "UPDATE public.catena_checkpoint SET marca_token = 'x' "
                      "WHERE dimensione = 3")


async def test_rdv_accodato(db, s3) -> None:
    tenant, classe = await crea_tenant_e_classe(db)
    pda = await crea_pda(db, tenant, classe)
    async with get_async_session() as session:
        for _ in range(2):  # idempotente
            await firme_documenti.accoda_rdv(
                session, tenant_id=str(tenant), aip_id=str(pda.aip_id), bucket="b",
                chiave="k/RdV_Definitivo_v2.xml", versione="v1", contenuto=b"<RdV/>",
                nome="RdV_Definitivo_v2.xml")
    righe = await sql(db, "SELECT tipo, impronta_sha256 FROM public.firme_documenti "
                          "WHERE aip_id = $1", pda.aip_id)
    assert [(r["tipo"], r["impronta_sha256"]) for r in righe] == [
        ("RDV", hashlib.sha256(b"<RdV/>").hexdigest())]


async def test_sigillo_attestazione_secondo_il_parametro(db) -> None:
    """VD-C-124: sigillo qualificato sull'attestazione solo se il parametro lo vuole."""
    servizio = FirmaFinta()
    esito = await firme_documenti.sigillo_attestazione("att", richiesto=True, servizio=servizio)
    assert esito["attestazione_sigillo_stato"] == "DISATTIVO" and servizio.firmati == []
    await _parametro(db, "ATTESTAZIONE_SIGILLO", "SU_RICHIESTA")
    assert (await firme_documenti.sigillo_attestazione("att", richiesto=False, servizio=servizio)
            )["attestazione_sigillo_stato"] == "NON_RICHIESTO"
    esito = await firme_documenti.sigillo_attestazione("att", richiesto=True, servizio=servizio)
    assert esito["attestazione_sigillo_stato"] == "FIRMATO" and servizio.firmati == [b"att"]
    await _parametro(db, "ATTESTAZIONE_SIGILLO", "SEMPRE")
    esito = await firme_documenti.sigillo_attestazione(
        "att", richiesto=False, servizio=FirmaFinta(STATO_NON_FIRMATO))
    assert esito["attestazione_sigillo_stato"] == "NON_FIRMATO"
    assert esito["attestazione_sigillo"] is None


class FirmaNonDeterministica(FirmaFinta):
    """Come una vera CAdES: signing-time e marca diversi a ogni firma."""

    async def firma(self, contenuto: bytes, *, formato: str = "cades_detached",
                    livello: str | None = None) -> EsitoFirmaBlocco:
        e = await super().firma(contenuto, formato=formato, livello=livello)
        return EsitoFirmaBlocco(e.stato, e.motivo, firma_b64=base64.b64encode(
            b"P7M" + os.urandom(8) + contenuto).decode(), tsa_timestamp=e.tsa_timestamp,
            dettagli=e.dettagli)

    async def marca(self, impronta: bytes) -> EsitoMarca:
        e = await super().marca(impronta)
        return EsitoMarca(e.stato, e.motivo, token_b64=base64.b64encode(
            b"TSR" + os.urandom(8) + impronta).decode(), istante=e.istante, tsa=e.tsa)


async def test_nessuno_stallo_dopo_scrittura_worm_senza_update(db, s3) -> None:
    """Revisione R13a (1), GRAVE: busta scritta nel WORM, poi lease perso prima
    dell'UPDATE; il detentore legittimo deve sigillare al giro successivo."""
    pda, contenuto = await _ipda(db, s3)
    with pytest.raises(archivio.LeasePerso):
        await firme_documenti.sigilla(FirmaNonDeterministica(), detentore="altro-processo",
                                      adesso=ADESSO)
    orfane = s3.list_objects_v2(Bucket=settings.s3_bucket_aip,
                                Prefix=f"{pda.tenant}/{pda.aip_id}/Sigillo_")
    assert orfane.get("KeyCount") == 1  # la busta orfana resta nel WORM
    esito = await firme_documenti.sigilla(FirmaNonDeterministica(), detentore=IO,
                                          adesso=ADESSO)
    assert esito["firmati"] == 1 and esito["errori"] == 0
    r = await _riga(db, pda.aip_id)
    assert r["stato"] == "FIRMATO"
    busta = s3.get_object(Bucket=settings.s3_bucket_aip, Key=r["busta_chiave"])["Body"].read()
    assert hashlib.sha256(busta).hexdigest() == r["busta_sha256"].strip()
    assert busta.endswith(contenuto)


async def test_errore_su_un_documento_non_ferma_il_lotto(db, s3, monkeypatch) -> None:
    """Revisione R13a (1): un'eccezione non prevista (es. ContenutoWormDiverso)
    rinvia QUEL documento con attesa crescente e il lotto prosegue."""
    primo, _ = await _ipda(db, s3)
    secondo, _ = await _ipda(db, s3)
    originale = firme_documenti.scrivi_busta

    async def scrivi(riga, busta, **kw):  # type: ignore[no-untyped-def]
        if riga["aip_id"] == primo.aip_id:
            from app.registro.archivio_worm import ContenutoWormDiverso
            raise ContenutoWormDiverso("busta diversa", bucket="b", chiave="k")
        return await originale(riga, busta, **kw)

    monkeypatch.setattr(firme_documenti, "scrivi_busta", scrivi)
    esito = await firme_documenti.sigilla(FirmaFinta(), detentore=IO, adesso=ADESSO)
    assert esito["errori"] == 1 and esito["firmati"] == 1
    assert (await _riga(db, secondo.aip_id))["stato"] == "FIRMATO"
    r = await _riga(db, primo.aip_id)
    assert r["stato"] == "DA_FIRMARE" and r["tentativi"] == 1
    assert r["prossimo_tentativo_il"] > ADESSO and "ContenutoWormDiverso" in r["motivo"]


async def test_marca_checkpoint_dopo_lease_perso_non_collide(db, s3) -> None:
    """Revisione R13a (1): token scritto nel WORM e lease perso prima dell'UPDATE;
    la nuova marca (diversa) va su una chiave nuova."""
    await _checkpoint(db, 1)
    with pytest.raises(archivio.LeasePerso):
        await firme_documenti.marca_checkpoint(FirmaNonDeterministica(),
                                               detentore="altro-processo", adesso=ADESSO)
    assert await firme_documenti.marca_checkpoint(FirmaNonDeterministica(), detentore=IO,
                                                  adesso=ADESSO) == STATO_MARCATO


async def test_ripresa_di_sigillo_e_rimarcatura_interrotti(db, s3) -> None:
    """Seconda revisione: sigillo e rimarcatura interrotti da un lease perso dopo la
    scrittura nel WORM riprendono al giro successivo (firme non deterministiche)."""

    class Scadente(FirmaNonDeterministica):
        def __init__(self) -> None:
            super().__init__(scadenza=ADESSO + timedelta(days=10))

        async def rimarca(self, busta: bytes, contenuto: bytes | None = None
                          ) -> EsitoFirmaBlocco:
            e = await super().rimarca(busta, contenuto)
            return EsitoFirmaBlocco(e.stato, None, firma_b64=base64.b64encode(
                busta + os.urandom(4)).decode(), dettagli=e.dettagli)

    pda, _ = await _ipda(db, s3)
    with pytest.raises(archivio.LeasePerso):
        await firme_documenti.sigilla(Scadente(), detentore="altro", adesso=ADESSO)
    assert (await firme_documenti.sigilla(Scadente(), detentore=IO, adesso=ADESSO)
            )["firmati"] == 1
    assert (await _riga(db, pda.aip_id))["stato"] == "FIRMATO"
    with pytest.raises(archivio.LeasePerso):
        await firme_documenti.rinnova(Scadente(), detentore="altro", adesso=ADESSO)
    assert (await firme_documenti.rinnova(Scadente(), detentore=IO, adesso=ADESSO)
            )["rinnovati"] == 1
    assert (await _riga(db, pda.aip_id))["rinnovi"] == 1


async def test_errore_persistente_va_in_errore_senza_firme_infinite(db, s3, monkeypatch) -> None:
    """Seconda revisione (2): un errore che si ripete DOPO la firma porta il documento
    in ERRORE dopo FIRMA_TENTATIVI_MASSIMI errori (niente firma pagata ogni ora)."""
    await _parametro(db, "FIRMA_TENTATIVI_MASSIMI", 2)
    pda, _ = await _ipda(db, s3)

    async def scrivi(riga, busta, **kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("storage giù")

    monkeypatch.setattr(firme_documenti, "scrivi_busta", scrivi)
    servizio = FirmaFinta()
    adesso = ADESSO
    for _ in range(4):
        async with get_async_session() as session:
            await archivio.acquisisci_lease(session, IO, adesso, adesso + timedelta(hours=1))
        await firme_documenti.sigilla(servizio, detentore=IO, adesso=adesso)
        adesso += timedelta(hours=2)
    assert len(servizio.firmati) == 2  # poi più nessuna firma
    r = await _riga(db, pda.aip_id)
    assert r["stato"] == "ERRORE" and r["errori"] == 2 and "ERRORE" in r["motivo"]
    assert metriche().firme_in_errore._value.get() >= 1
    async with get_async_session() as session:  # con BLOCCA il PdA resta fermo
        await _parametro(db, "FIRMA_SENZA_FORNITORE", "BLOCCA")
        assert await firme_documenti.bloccati(session, [str(pda.aip_id)]) == {str(pda.aip_id)}


async def test_retention_letta_prima_della_firma(db, s3, monkeypatch) -> None:
    """Seconda revisione (2): se la retention del PdA non si legge, non si firma."""
    pda, _ = await _ipda(db, s3)

    async def rotta(riga):  # type: ignore[no-untyped-def]
        raise RuntimeError("classe documentale illeggibile")

    monkeypatch.setattr(firme_documenti, "retention_pacchetto", rotta)
    servizio = FirmaFinta()
    esito = await firme_documenti.sigilla(servizio, detentore=IO, adesso=ADESSO)
    assert esito["errori"] == 1 and servizio.firmati == []


async def test_rimarcatura_registra_la_busta_precedente(db, s3) -> None:
    """Seconda revisione (3): ogni rimarcatura aggiunge chiave, versione e impronta
    della busta precedente a storico_buste."""
    pda, _ = await _ipda(db, s3)
    await firme_documenti.sigilla(FirmaFinta(scadenza=ADESSO + timedelta(days=20)),
                                  detentore=IO, adesso=ADESSO)
    prima = await _riga(db, pda.aip_id)
    await firme_documenti.rinnova(FirmaFinta(), detentore=IO, adesso=ADESSO)
    dopo = await _riga(db, pda.aip_id)
    storico = json.loads(dopo["storico_buste"]) if isinstance(dopo["storico_buste"], str) \
        else dopo["storico_buste"]
    assert len(storico) == 1
    assert storico[0]["busta_chiave"] == prima["busta_chiave"]
    assert storico[0]["busta_sha256"].strip() == prima["busta_sha256"].strip()
    assert storico[0]["rinnovo"] == 0 and dopo["busta_chiave"] != prima["busta_chiave"]
