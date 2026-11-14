# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 f2 — lavoratore SFTP proporzionale alle cartelle pronte (REV-15) e attesa
del Retry-After con un tetto (rapporto c2, difetto 1), su PostgreSQL reale.

REV-15: prima ogni replica, a ogni giro, elencava tutti gli account e
scandiva ``in/`` e ``lavorazione/`` di ciascuno; ``gather`` su tutte le
candidate faceva aspettare il giro successivo alla cartella più lenta. Ora il
server SFTP segnala gli account con attività, il lavoratore guarda solo
quelli (in lease, una replica per account) e le riprese vengono dal database;
in esercizio una coda serve le cartelle.

c2: con QTA-002 il ``Retry-After`` arriva a mezzanotte: il lavoratore restava
fermo dentro la chiamata (fino a 3 volte) e poi consumava i tentativi.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from app.application.lavoratore import Lavoratore
from app.infrastructure import archivio, repository
from app.infrastructure.gateway import ClientGateway, ErroreGateway
from app.infrastructure.segnalazioni import Segnalatore

from tests.conftest import Account, config_lavoratore, connetti, crea_account
from tests.infra_reale import richiede_pg, sql

pytestmark = richiede_pg

PDF = b"%PDF-1.7\n" + b"contenuto " * 50


def _indice(nome: str = "a.pdf", dati: bytes = PDF) -> dict:
    return {"classe_documentale": "fattura", "documenti": [{
        "id": "1", "tipo": "PRINCIPALE", "nome_file": nome,
        "hash": {"impronta": hashlib.sha256(dati).hexdigest(), "algoritmo": "SHA-256"}}]}


def _scrivi(radice: Path, account: Account, cartella: str) -> Path:
    dest = radice / str(account.id) / "in" / cartella
    dest.mkdir(parents=True)
    (dest / "a.pdf").write_bytes(PDF)
    (dest / "indice.json").write_text(json.dumps(_indice()))
    return dest


async def _segnala(account: Account, pronta: bool = True) -> None:
    await repository.segnala_attivita({account.id: (account.tenant_id, pronta)},
                                      stabilita_secondi=0)


async def _segnalazioni(url: str) -> dict:
    righe = await sql(url, "SELECT account_id, versione, detentore, controllo_completo, "
                           "da_guardare_dal FROM public.sftp_segnalazioni")
    return {r["account_id"]: dict(r) for r in righe}


# ── REV-15 ──────────────────────────────────────────────────────────

async def test_giro_guarda_solo_gli_account_segnalati(db_url, radice, lavoratore,
                                                      monkeypatch) -> None:
    """Primo giro: controllo completo (tutti gli account, una volta). Poi solo gli
    account segnalati: con 20 account e uno solo attivo si scandisce un'area;
    senza attività nessuna. Le segnalazioni esaurite si eliminano."""
    accounts = [await crea_account(db_url) for _ in range(20)]
    letture: list[Path] = []
    originale = archivio.sottocartelle

    def conta(percorso: Path, limite: int):
        letture.append(percorso)
        return originale(percorso, limite)

    monkeypatch.setattr(archivio, "sottocartelle", conta)
    assert await lavoratore.giro() == 0
    assert len({p.parent for p in letture}) >= 20          # controllo completo
    assert await _segnalazioni(db_url) == {}               # niente da riguardare

    letture.clear()
    assert await lavoratore.giro() == 0
    assert letture == []                                   # nessuna attività: nessuna scansione

    _scrivi(radice, accounts[7], "PDV-1")
    await _segnala(accounts[7])
    letture.clear()
    assert await lavoratore.giro() == 1
    assert {p.parent.name for p in letture} == {str(accounts[7].id)}
    assert (radice / str(accounts[7].id) / "fatti" / "PDV-1").is_dir()


async def test_cartella_in_caricamento_riguardata_dopo_la_stabilita(db_url, radice,
                                                                    lavoratore) -> None:
    """Una cartella non ancora stabile: l'account resta segnalato con il prossimo
    controllo alla fine della stabilità (non a ogni giro)."""
    account = await crea_account(db_url)
    lavoratore.c = config_lavoratore(radice, stabilita_secondi=120)
    await lavoratore.giro()                                # controllo completo iniziale
    _scrivi(radice, account, "PDV-2")
    await _segnala(account)
    assert await lavoratore.giro() == 0
    seg = (await _segnalazioni(db_url))[account.id]
    assert seg["detentore"] is None
    attesa = (seg["da_guardare_dal"] - datetime.now(UTC)).total_seconds()
    assert 60 < attesa <= 121


async def test_nuova_segnalazione_durante_l_esame_non_si_perde(db_url, radice) -> None:
    """Se il server segnala di nuovo mentre una replica guarda l'account, la
    replica non elimina la riga: l'account torna da guardare subito."""
    account = await crea_account(db_url)
    await _segnala(account)
    [seg] = await repository.prendi_segnalazioni("a", 10, 60)
    assert await repository.prendi_segnalazioni("b", 10, 60) == []   # lease: una replica
    await _segnala(account, pronta=False)                            # attività nuova
    assert await repository.rilascia_segnalazione(seg, "a", riguarda_fra_secondi=None) is False
    [di_nuovo] = await repository.prendi_segnalazioni("b", 10, 60)
    assert di_nuovo.versione == seg.versione + 1
    assert await repository.rilascia_segnalazione(di_nuovo, "b",
                                                  riguarda_fra_secondi=None) is True


async def test_account_non_operativo_resta_segnalato(db_url) -> None:
    account = await crea_account(db_url, stato="SOSPESO")
    await _segnala(account)
    assert await repository.prendi_segnalazioni("a", 10, 60) == []
    await sql(db_url, "UPDATE public.sftp_account SET stato = 'ATTIVO' WHERE id = $1",
              account.id)
    assert [s.account_id for s in await repository.prendi_segnalazioni("a", 10, 60)] == [
        account.id]


async def test_server_sftp_segnala_scritture_e_rinomina(db_url, server_sftp) -> None:
    """Il server SFTP segnala l'account: scrittura → dopo la stabilità; rinomina
    in un nome definitivo → «pronta» (da guardare subito). A lotti."""
    porta, contesto = server_sftp
    contesto.segnalatore = Segnalatore(stabilita_secondi=300)
    account = await crea_account(db_url)
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/PDV-9.tmp")
        async with sftp.open("/in/PDV-9.tmp/a.pdf", "wb") as f:
            await f.write(PDF)
        assert contesto.segnalatore.in_attesa == {account.id: (account.tenant_id, False)}
        assert await contesto.segnalatore.svuota() == 1
        seg = (await _segnalazioni(db_url))[account.id]
        assert (seg["da_guardare_dal"] - datetime.now(UTC)).total_seconds() > 200
        await sftp.rename("/in/PDV-9.tmp", "/in/PDV-9")
        assert contesto.segnalatore.in_attesa == {account.id: (account.tenant_id, True)}
        await contesto.segnalatore.svuota()
    seg2 = (await _segnalazioni(db_url))[account.id]
    assert seg2["versione"] == seg["versione"] + 1
    assert seg2["da_guardare_dal"] <= datetime.now(UTC)


async def test_segnalatore_database_giu_non_perde_segnalazioni() -> None:
    chiamate: list[dict] = []

    async def scrivi(lotto, *, stabilita_secondi: float) -> None:
        chiamate.append(dict(lotto))
        if len(chiamate) == 1:
            raise ConnectionError("database giù")

    s = Segnalatore(stabilita_secondi=30, scrivi=scrivi)
    a, t = uuid.uuid4(), uuid.uuid4()
    s.segnala(a, t, pronta=True)
    assert await s.svuota() == 0 and s.in_attesa == {a: (t, True)}
    s.segnala(a, t, pronta=False)             # «pronta» resta: si guarda subito
    assert await s.svuota() == 1 and chiamate[-1] == {a: (t, True)}


async def test_esegui_coda_una_cartella_lenta_non_ferma_le_altre(db_url, radice, lavoratore,
                                                                monkeypatch) -> None:
    """In esercizio le cartelle passano da una coda: mentre una è ferma (5 GB,
    gateway lento) le altre si lavorano, e una nuova raccolta avviene senza
    aspettare la lenta."""
    account = await crea_account(db_url)
    for nome in ("PDV-LENTA", "PDV-B", "PDV-C"):
        _scrivi(radice, account, nome)
    await _segnala(account)
    sblocca = asyncio.Event()
    fatte: list[str] = []

    async def lavora(account_id, tenant_id, cartella) -> bool:
        if cartella == "PDV-LENTA":
            await sblocca.wait()
        fatte.append(cartella)
        return True

    monkeypatch.setattr(lavoratore, "lavora", lavora)
    lavoratore.c = config_lavoratore(radice, parallelismo=2, intervallo_secondi=0.01)
    compito = asyncio.create_task(lavoratore.esegui())
    try:
        for _ in range(500):
            if {"PDV-B", "PDV-C"} <= set(fatte):
                break
            await asyncio.sleep(0.01)
        assert {"PDV-B", "PDV-C"} <= set(fatte) and "PDV-LENTA" not in fatte
        sblocca.set()
        for _ in range(500):
            if "PDV-LENTA" in fatte:
                break
            await asyncio.sleep(0.01)
        assert "PDV-LENTA" in fatte
    finally:
        compito.cancel()
        with pytest.raises(asyncio.CancelledError):
            await compito


# ── c2: Retry-After con tetto e quote ───────────────────────────────

async def test_client_retry_after_oltre_il_tetto_non_si_aspetta() -> None:
    chiamate: list[httpx.Request] = []

    def gestore(req: httpx.Request) -> httpx.Response:
        chiamate.append(req)
        return httpx.Response(429, headers={"Retry-After": "36000"},
                              json={"codice": "QTA-002", "ritentabile": True, "title": "Q"})

    attese: list[float] = []

    async def attendi(s: float) -> None:
        attese.append(s)

    client = ClientGateway("http://gw", timeout_secondi=5, ritentativi_secondi=[1, 2],
                           transport=httpx.MockTransport(gestore), attendi=attendi,
                           retry_after_max_secondi=60)
    with pytest.raises(ErroreGateway) as exc:
        await client.versa("tok", indice={}, file={}, chiave="k" * 10)
    await client.chiudi()
    assert len(chiamate) == 1 and attese == []            # nessuna attesa dentro la chiamata
    assert exc.value.retry_after == 36000 and exc.value.problema["codice"] == "QTA-002"


async def _lavoratore_quota(radice, gateway_finto, emettitore, metriche, **variazioni):
    attese: list[float] = []

    async def attendi(s: float) -> None:
        attese.append(s)

    from tests.gateway_finto import crea_gateway

    client = ClientGateway("http://gateway", timeout_secondi=10, ritentativi_secondi=[1, 2],
                           transport=httpx.ASGITransport(app=crea_gateway(gateway_finto)),
                           attendi=attendi, retry_after_max_secondi=60)
    lav = Lavoratore(config_lavoratore(radice, **variazioni), gateway=client,
                     emettitore=emettitore, metriche=metriche, detentore="q")
    return lav, client, attese


async def test_quota_giornaliera_rilascia_la_cartella_senza_consumare_tentativi(
        db_url, radice, gateway_finto, emettitore, metriche) -> None:
    """QTA-002 (Retry-After 3600 dal catalogo, oltre il tetto di 60 s): nessuna
    attesa dentro la chiamata; cartella IN_RIPROVA con il prossimo tentativo non
    prima del Retry-After; il tentativo non conta (ritardi da un solo tentativo:
    senza questa regola sarebbe già scartata)."""
    account = await crea_account(db_url)
    gateway_finto.guasti["versamento"] = ["QTA-002", "QTA-002"]
    lav, client, attese = await _lavoratore_quota(radice, gateway_finto, emettitore, metriche,
                                                  ritardi_secondi=(0,))
    try:
        _scrivi(radice, account, "PDV-Q")
        await _segnala(account)
        assert await lav.giro() == 1
        esito = json.loads((radice / str(account.id) / "out" / "PDV-Q" / "esito.json")
                           .read_text())
        assert esito["stato"] == "IN_RIPROVA" and esito["errori"][0]["codice"] == "QTA-002"
        prossimo = datetime.fromisoformat(esito["prossimo_tentativo"])
        assert prossimo - datetime.now(UTC) > timedelta(seconds=3500)
        assert attese == []
        [riga] = await sql(db_url, "SELECT * FROM public.sftp_lavorazioni WHERE account_id = $1",
                           account.id)
        assert riga["detentore"] is None and riga["tentativi"] == 0
        assert riga["lease_scade_il"] - datetime.now(UTC) > timedelta(seconds=3500)
        # arriva la mezzanotte: nuovo tentativo, che non è il «secondo» per i ritardi
        await sql(db_url, "UPDATE public.sftp_lavorazioni SET lease_scade_il = now()")
        assert await lav.giro() == 1
        esito = json.loads((radice / str(account.id) / "out" / "PDV-Q" / "esito.json")
                           .read_text())
        assert esito["stato"] == "IN_RIPROVA" and esito["tentativi"] == 1
        await sql(db_url, "UPDATE public.sftp_lavorazioni SET lease_scade_il = now()")
        assert await lav.giro() == 1                     # quota tornata: versato
        esito = json.loads((radice / str(account.id) / "out" / "PDV-Q" / "esito.json")
                           .read_text())
        assert esito["stato"] == "VERSATO"
    finally:
        await client.chiudi()


async def test_quota_oltre_l_attesa_massima_scartata(db_url, radice, gateway_finto,
                                                    emettitore, metriche) -> None:
    account = await crea_account(db_url)
    gateway_finto.guasti["versamento"] = ["QTA-002"]
    lav, client, _ = await _lavoratore_quota(radice, gateway_finto, emettitore, metriche,
                                             quota_attesa_massima_secondi=60.0)
    try:
        _scrivi(radice, account, "PDV-Z")
        await _segnala(account)
        assert await lav.giro() == 1
        esito = json.loads((radice / str(account.id) / "out" / "PDV-Z" / "esito.json")
                           .read_text())
        assert esito["stato"] == "SCARTATO" and esito["errori"][0]["codice"] == "QTA-002"
    finally:
        await client.chiudi()
