# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lavoratore del canale SFTP su PostgreSQL reale con il gateway finto (contratto
C1 + versamento per riferimento): flusso completo via SFTP, idempotenza dei
reinvii, scarti con esito, ritentativi, ripresa dopo crash, lease fra repliche.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from app.application.lavoratore import Lavoratore
from app.infrastructure import archivio, repository

from tests.conftest import Account, config_lavoratore, connetti, crea_account
from tests.infra_reale import richiede_pg, sql

pytestmark = richiede_pg

PDF = b"%PDF-1.7\n" + b"contenuto di prova " * 300   # ~5,7 KB: 6 parti da 1000 byte
XML = b"<?xml version='1.0'?><allegato/>"


def _indice(file: dict[str, bytes], *, impronta_errata: str | None = None) -> dict:
    documenti = []
    for i, (nome, dati) in enumerate(file.items()):
        impronta = hashlib.sha256(dati).hexdigest()
        if nome == impronta_errata:
            impronta = "0" * 64
        documenti.append({"id": str(i + 1), "tipo": "PRINCIPALE" if i == 0 else "ALLEGATO",
                          "nome_file": nome, "hash": {"impronta": impronta,
                                                      "algoritmo": "SHA-256"}})
    return {"classe_documentale": "fattura", "documenti": documenti}


async def _deposita(radice: Path, account: Account, cartella: str, file: dict[str, bytes],
                    indice: dict | bytes | None = None) -> Path:
    """Scrive direttamente sul volume una cartella pronta in in/ e la segnala
    come farebbe il server SFTP alla rinomina finale (R03 f2, REV-15)."""
    dest = radice / str(account.id) / "in" / cartella
    dest.mkdir(parents=True)
    for nome, dati in file.items():
        (dest / nome).write_bytes(dati)
    if indice is not None:
        (dest / "indice.json").write_bytes(
            indice if isinstance(indice, bytes) else json.dumps(indice).encode())
    await repository.segnala_attivita({account.id: (account.tenant_id, True)},
                                      stabilita_secondi=0)
    return dest


def _esito(radice: Path, account: Account, cartella: str) -> dict:
    return json.loads((radice / str(account.id) / "out" / cartella / "esito.json").read_text())


async def _righe(url: str, account: Account) -> list:
    return await sql(url, "SELECT * FROM public.sftp_lavorazioni WHERE account_id = $1 "
                          "ORDER BY created_at", account.id)


async def test_flusso_completo_via_sftp_versato(db_url, server_sftp, radice, lavoratore,
                                                gateway_finto) -> None:
    """Il cliente carica in in/<id>.tmp/, rinomina, il lavoratore versa per
    riferimento e scrive out/<id>/esito.json; la cartella finisce in fatti/."""
    porta, _ = server_sftp
    account = await crea_account(db_url)
    file = {"fattura.pdf": PDF, "allegato.xml": XML}
    async with connetti(porta, account) as conn, conn.start_sftp_client() as sftp:
        await sftp.mkdir("/in/PDV-2026-001.tmp")
        for nome, dati in file.items():
            async with sftp.open(f"/in/PDV-2026-001.tmp/{nome}", "wb") as f:
                await f.write(dati)
        async with sftp.open("/in/PDV-2026-001.tmp/indice.json", "wb") as f:
            await f.write(json.dumps(_indice(file)).encode())
        await sftp.rename("/in/PDV-2026-001.tmp", "/in/PDV-2026-001")

        assert await lavoratore.giro() == 1

        async with sftp.open("/out/PDV-2026-001/esito.json", "rb") as f:
            esito = json.loads(await f.read())
        assert "PDV-2026-001" in await sftp.listdir("/fatti")
        assert await sftp.listdir("/in") in ([".", ".."], ["..", "."])

    assert esito["stato"] == "VERSATO" and esito["reinvio"] is False
    assert esito["stato_pdv"] == "RICEVUTO" and esito["avvisi"][0]["codice"] == "RGL-103"
    pdv = esito["pdv_id"]
    assert esito["link"] == {"stato": f"https://api.verumdoc.test/api/v1/versamento/{pdv}",
                             "rapporto": f"https://api.verumdoc.test/api/v1/versamento/{pdv}/rdv"}
    # versamento per riferimento: indice tale e quale, mappa nome → caricamento, chiave stabile
    (chiamata,) = gateway_finto.chiamate_versamento
    assert chiamata["indice"] == _indice(file)
    assert set(chiamata["file"]) == set(file)
    impronta_indice = hashlib.sha256(json.dumps(_indice(file)).encode()).hexdigest()
    attesa = "sftp-" + hashlib.sha256("\n".join(
        ["versamento", str(account.tenant_id), "PDV-2026-001", impronta_indice]).encode()
    ).hexdigest()
    assert chiamata["chiave"] == attesa == esito["chiave_idempotenza"]
    # file ricostruiti dal gateway identici ai byte del cliente
    for nome, cid in chiamata["file"].items():
        s = gateway_finto.sessioni[cid]
        assert b"".join(s.parti[i] for i in sorted(s.parti)) == file[nome]
    assert len(gateway_finto.sessioni[chiamata["file"]["fattura.pdf"]].parti) == 6
    # token di canale (contratto C2): utente responsabile, ente, canale SFTP
    token = gateway_finto.token_visti[0]
    assert token["tenant_id"] == str(account.tenant_id)
    assert token["user_id"] == str(account.responsabile_id)
    assert token["canale"] == "SFTP" and token["ruolo"] == "OPERATORE"
    (riga,) = await _righe(db_url, account)
    assert riga["stato"] == "VERSATA" and str(riga["pdv_id"]) == pdv
    assert riga["detentore"] is None and riga["concluso_il"] is not None


async def test_reinvio_stessa_cartella_stesso_pdv_senza_nuovo_versamento(
        db_url, radice, lavoratore, gateway_finto) -> None:
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-R", file, _indice(file))
    await lavoratore.giro()
    primo = _esito(radice, account, "PDV-R")
    await _deposita(radice, account, "PDV-R", file, _indice(file))
    assert await lavoratore.giro() == 1
    secondo = _esito(radice, account, "PDV-R")
    assert secondo["pdv_id"] == primo["pdv_id"] and secondo["reinvio"] is True
    assert len(gateway_finto.chiamate_versamento) == 1
    fatti = sorted(p.name for p in (radice / str(account.id) / "fatti").iterdir())
    assert fatti[0] == "PDV-R" and fatti[1].startswith("PDV-R__")


async def test_stessa_cartella_indice_diverso_nuovo_versamento(db_url, radice, lavoratore,
                                                              gateway_finto) -> None:
    account = await crea_account(db_url)
    await _deposita(radice, account, "PDV-D", {"a.pdf": PDF}, _indice({"a.pdf": PDF}))
    await lavoratore.giro()
    altro = {"a.pdf": PDF + b"v2"}
    await _deposita(radice, account, "PDV-D", altro, _indice(altro))
    await lavoratore.giro()
    assert len(gateway_finto.chiamate_versamento) == 2
    chiavi = {c["chiave"] for c in gateway_finto.chiamate_versamento}
    assert len(chiavi) == 2


@pytest.mark.parametrize("caso,codice", [
    ("senza_indice", "SFTP-101"),
    ("indice_non_json", "SFTP-102"),
    ("indice_lista", "SFTP-102"),
    ("file_mancante", "SFTP-103"),
    ("file_in_piu", "SFTP-104"),
    ("sottocartella", "SFTP-105"),
    ("nome_con_percorso", "SFTP-105"),
])
async def test_cartella_non_valida_scartata_con_esito(db_url, radice, lavoratore, gateway_finto,
                                                      caso, codice) -> None:
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    indice: dict | bytes | None = _indice(file)
    if caso == "senza_indice":
        indice = None
    elif caso == "indice_non_json":
        indice = b"{non json"
    elif caso == "indice_lista":
        indice = b"[1, 2]"
    elif caso == "file_mancante":
        indice = _indice({"a.pdf": PDF, "b.xml": XML})
    elif caso == "file_in_piu":
        file = {"a.pdf": PDF, "extra.txt": b"x"}
    elif caso == "nome_con_percorso":
        indice = _indice(file)
        indice["documenti"][0]["nome_file"] = "../a.pdf"
    cartella = await _deposita(radice, account, "PDV-X", file, indice)
    if caso == "sottocartella":
        (cartella / "sotto").mkdir()
    # senza indice la cartella è «in caricamento»: si lavora solo quando abbandonata
    if caso == "senza_indice":
        lavoratore.c = config_lavoratore(radice, abbandono_secondi=0)
    assert await lavoratore.giro() == 1
    esito = _esito(radice, account, "PDV-X")
    assert esito["stato"] == "SCARTATO"
    assert [e["codice"] for e in esito["errori"]] == [codice]
    assert esito["errori"][0]["esito"] == "NON_VALIDO"
    assert (radice / str(account.id) / "scarti" / "PDV-X").is_dir()
    assert gateway_finto.chiamate_versamento == []
    (riga,) = await _righe(db_url, account)
    assert riga["stato"] == "SCARTATA"


async def test_rifiuto_del_gateway_scartato_con_problem_originale(db_url, radice, lavoratore,
                                                                  gateway_finto) -> None:
    """Indice senza documenti: lo schema lo valida il gateway (VRS-001), il canale
    riporta il problem+json del gateway nell'esito."""
    account = await crea_account(db_url)
    await _deposita(radice, account, "PDV-S", {},
                    {"classe_documentale": "fattura", "documenti": []})
    await lavoratore.giro()
    esito = _esito(radice, account, "PDV-S")
    assert esito["stato"] == "SCARTATO" and esito["errori"][0]["codice"] == "VRS-001"


async def test_impronta_dichiarata_errata_scartata_dalla_verifica(db_url, radice, lavoratore,
                                                                  gateway_finto) -> None:
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-H", file, _indice(file, impronta_errata="a.pdf"))
    await lavoratore.giro()
    esito = _esito(radice, account, "PDV-H")
    assert esito["stato"] == "SCARTATO" and esito["errori"][0]["codice"] == "VRS-102"
    assert gateway_finto.chiamate_versamento == []


async def test_errore_temporaneo_in_riprova_poi_versato(db_url, radice, client_gateway,
                                                        emettitore, metriche,
                                                        gateway_finto) -> None:
    """503 ritentabili: la chiamata si ripete (2 ritentativi), poi la cartella va
    IN_RIPROVA con esito scritto; al giro successivo (ritardo 0) è versata."""
    account = await crea_account(db_url)
    gateway_finto.guasti["versamento"] = [503, 503, 503]
    lav = Lavoratore(config_lavoratore(radice, ritardi_secondi=(0, 0)), gateway=client_gateway,
                     emettitore=emettitore, metriche=metriche, detentore="r")
    await _deposita(radice, account, "PDV-T", {"a.pdf": PDF}, _indice({"a.pdf": PDF}))
    await lav.giro()
    esito = _esito(radice, account, "PDV-T")
    assert esito["stato"] == "IN_RIPROVA" and esito["prossimo_tentativo"]
    assert esito["errori"][0]["codice"] == "HTTP-503"
    assert (radice / str(account.id) / "lavorazione" / "PDV-T").is_dir()
    # attesa = max(ritentativo, Retry-After del catalogo per HTTP-503)
    assert client_gateway.attese == [30, 30]
    (riga,) = await _righe(db_url, account)
    assert riga["stato"] == "IN_CORSO" and riga["detentore"] is None
    caricamenti = riga["caricamenti"]
    await lav.giro()
    esito = _esito(radice, account, "PDV-T")
    assert esito["stato"] == "VERSATO" and esito["tentativi"] == 2
    # i file già caricati non si ricaricano: stesse sessioni
    assert (await _righe(db_url, account))[0]["caricamenti"] == caricamenti
    assert metriche.cartelle.labels("IN_RIPROVA")._value.get() == 1


async def test_errore_temporaneo_tentativi_esauriti_scartato(db_url, radice, client_gateway,
                                                             emettitore, gateway_finto) -> None:
    account = await crea_account(db_url)
    gateway_finto.guasti["versamento"] = [503] * 20
    lav = Lavoratore(config_lavoratore(radice, ritardi_secondi=(0,)), gateway=client_gateway,
                     emettitore=emettitore, detentore="r")
    await _deposita(radice, account, "PDV-E", {"a.pdf": PDF}, _indice({"a.pdf": PDF}))
    await lav.giro()
    assert _esito(radice, account, "PDV-E")["stato"] == "IN_RIPROVA"
    await lav.giro()
    esito = _esito(radice, account, "PDV-E")
    assert esito["stato"] == "SCARTATO" and esito["errori"][0]["codice"] == "HTTP-503"
    assert (radice / str(account.id) / "scarti" / "PDV-E").is_dir()


async def test_ripresa_dopo_crash_parti_mancanti_soltanto(db_url, radice, lavoratore,
                                                          gateway_finto, client_gateway) -> None:
    """Crash a metà caricamento: sessione aperta con 2 parti su 6 e lease scaduto.
    Un'altra replica riprende la stessa sessione e invia solo le 4 mancanti."""
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-C", file, _indice(file))
    lav = await repository.prendi_in_carico(account.tenant_id, account.id, "PDV-C", "morta", 60)
    assert lav is not None
    area = radice / str(account.id)
    archivio.assicura_area(area)
    (area / "in" / "PDV-C").rename(area / "lavorazione" / "PDV-C")
    token = lavoratore.emettitore.token(tenant_id=account.tenant_id,
                                        user_id=account.responsabile_id, ruolo="OPERATORE")
    from app.domain.protocollo import chiave_caricamento
    s = await client_gateway.apri_caricamento(
        token, nome_file="a.pdf", dimensione=len(PDF), impronta=None,
        chiave=chiave_caricamento(
            str(lav.id), "a.pdf",
            hashlib.sha256(json.dumps(_indice(file)).encode()).hexdigest(), 1))
    for n in (1, 2):
        await client_gateway.carica_parte(token, s["caricamento_id"], n,
                                          percorso=area / "lavorazione" / "PDV-C" / "a.pdf",
                                          inizio=(n - 1) * 1000, lunghezza=1000)
    await repository.aggiorna_lavoro(
        lav.id, "morta", impronta_indice=None, chiave_idempotenza=None,
        caricamenti={"a.pdf": {"id": s["caricamento_id"], "gen": 1, "dimensione_parte": 1000,
                               "numero_parti": 6}})
    await sql(db_url, "UPDATE public.sftp_lavorazioni SET lease_scade_il = now() - "
                      "interval '1 second' WHERE id = $1", lav.id)
    gateway_finto.parti_ricevute.clear()
    assert await lavoratore.giro() == 1
    assert sorted(n for _, n in gateway_finto.parti_ricevute) == [3, 4, 5, 6]
    esito = _esito(radice, account, "PDV-C")
    assert esito["stato"] == "VERSATO"
    (riga,) = await _righe(db_url, account)
    assert riga["tentativi"] == 2 and riga["stato"] == "VERSATA"


async def test_ripresa_dopo_crash_prima_dello_spostamento_stesso_pdv(
        db_url, radice, lavoratore, gateway_finto) -> None:
    """Crash dopo il versamento ma prima di scrivere l'esito e spostare la cartella:
    al giro dopo (lease scaduto) la cartella è ripresa da lavorazione/ con la stessa
    riga, le stesse sessioni e la stessa Idempotency-Key: stesso PdV, nessun duplicato."""
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-K", file, _indice(file))
    originale = lavoratore._scrivi_e_sposta
    chiamate = {"n": 0}

    class Crash(BaseException):
        """Morte del processo: nessun gestore la intercetta."""

    def crash(*a, **k):
        chiamate["n"] += 1
        if chiamate["n"] == 1:
            raise Crash()
        return originale(*a, **k)

    lavoratore._scrivi_e_sposta = crash  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await lavoratore.giro()
    area = radice / str(account.id)
    assert (area / "lavorazione" / "PDV-K").is_dir()
    assert not (area / "out" / "PDV-K").exists()
    # il lease del processo «morto» scade
    await sql(db_url, "UPDATE public.sftp_lavorazioni SET lease_scade_il = now() WHERE "
                      "stato = 'IN_CORSO'")
    assert await lavoratore.giro() == 1
    esito = _esito(radice, account, "PDV-K")
    assert esito["stato"] == "VERSATO" and esito["reinvio"] is True
    assert len(gateway_finto.versamenti) == 1
    pdv = next(iter(gateway_finto.versamenti.values()))["pdv_id"]
    assert esito["pdv_id"] == pdv
    assert (area / "fatti" / "PDV-K").is_dir()
    (riga,) = await _righe(db_url, account)
    assert riga["stato"] == "VERSATA" and riga["tentativi"] == 2


async def test_crash_fra_spostamento_e_database_reinvio_stesso_pdv(
        db_url, radice, lavoratore, gateway_finto) -> None:
    """Crash dopo lo spostamento in fatti/ ma prima del database: il cliente ha
    l'esito; se reinvia la stessa cartella ottiene lo stesso PdV."""
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-M", file, _indice(file))
    originale = repository.concludi

    class Crash(BaseException):
        pass

    async def crash(*a, **k):
        raise Crash()

    repository.concludi = crash  # type: ignore[assignment]
    try:
        with pytest.raises(Crash):
            await lavoratore.giro()
    finally:
        repository.concludi = originale  # type: ignore[assignment]
    primo = _esito(radice, account, "PDV-M")
    assert primo["stato"] == "VERSATO"
    await sql(db_url, "UPDATE public.sftp_lavorazioni SET lease_scade_il = now() WHERE "
                      "stato = 'IN_CORSO'")
    assert await lavoratore.giro() == 0          # nulla da riprendere: cartella in fatti/
    await _deposita(radice, account, "PDV-M", file, _indice(file))
    assert await lavoratore.giro() == 1
    secondo = _esito(radice, account, "PDV-M")
    assert secondo["pdv_id"] == primo["pdv_id"] and secondo["reinvio"] is True
    assert len(gateway_finto.versamenti) == 1


async def test_due_repliche_una_sola_presa_in_carico(db_url, radice, client_gateway, emettitore,
                                                     gateway_finto) -> None:
    account = await crea_account(db_url)
    for i in range(6):
        file = {"a.pdf": PDF + str(i).encode()}
        await _deposita(radice, account, f"PDV-{i}", file, _indice(file))
    a = Lavoratore(config_lavoratore(radice), gateway=client_gateway, emettitore=emettitore,
                   detentore="replica-a")
    b = Lavoratore(config_lavoratore(radice), gateway=client_gateway, emettitore=emettitore,
                   detentore="replica-b")
    presi = await asyncio.gather(a.giro(), b.giro())
    assert sum(presi) == 6
    assert len(gateway_finto.chiamate_versamento) == 6
    righe = await _righe(db_url, account)
    assert len(righe) == 6 and {r["stato"] for r in righe} == {"VERSATA"}


async def test_cartella_in_caricamento_ignorata(db_url, radice, lavoratore) -> None:
    account = await crea_account(db_url)
    await _deposita(radice, account, "PDV-W.tmp", {"a.pdf": PDF}, _indice({"a.pdf": PDF}))
    cart = await _deposita(radice, account, "PDV-Z", {".tmp-a.pdf": PDF}, _indice({"a.pdf": PDF}))
    assert await lavoratore.giro() == 0
    lento = Lavoratore(config_lavoratore(radice, stabilita_secondi=3600),
                       gateway=lavoratore.gateway, emettitore=lavoratore.emettitore)
    (cart / ".tmp-a.pdf").rename(cart / "a.pdf")
    await repository.segnala_attivita({account.id: (account.tenant_id, False)},
                                      stabilita_secondi=0)
    assert await lento.giro() == 0          # appena cambiata: non ancora stabile
    # R03 f2: «lento» la riguarda fra un'ora (la sua stabilità); in esercizio le
    # repliche hanno la stessa configurazione: qui una nuova segnalazione
    await repository.segnala_attivita({account.id: (account.tenant_id, False)},
                                      stabilita_secondi=0)
    assert await lavoratore.giro() == 1     # stabilità 0: pronta


async def test_account_sospeso_cartelle_non_lavorate(db_url, radice, lavoratore) -> None:
    account = await crea_account(db_url, stato="SOSPESO")
    await _deposita(radice, account, "PDV-1", {"a.pdf": PDF}, _indice({"a.pdf": PDF}))
    assert await lavoratore.giro() == 0


async def test_lease_perso_interrompe_senza_scrivere(db_url, radice, client_gateway, emettitore,
                                                     gateway_finto) -> None:
    """Se il lease passa a un'altra replica, la lavorazione si ferma senza
    concludere né spostare la cartella."""
    account = await crea_account(db_url)
    await _deposita(radice, account, "PDV-L", {"a.pdf": PDF}, _indice({"a.pdf": PDF}))
    lav = Lavoratore(config_lavoratore(radice), gateway=client_gateway, emettitore=emettitore,
                     detentore="a")
    originale = repository.aggiorna_lavoro

    async def rubato(lid, detentore, **k):
        await sql(db_url, "UPDATE public.sftp_lavorazioni SET detentore = 'b' WHERE id = $1", lid)
        return await originale(lid, detentore, **k)

    repository.aggiorna_lavoro = rubato  # type: ignore[assignment]
    try:
        assert await lav.giro() == 1
    finally:
        repository.aggiorna_lavoro = originale  # type: ignore[assignment]
    assert gateway_finto.chiamate_versamento == []
    assert (radice / str(account.id) / "lavorazione" / "PDV-L").is_dir()
    assert not (radice / str(account.id) / "out" / "PDV-L").exists()
    (riga,) = await _righe(db_url, account)
    assert riga["stato"] == "IN_CORSO" and riga["detentore"] == "b"


async def test_pulizia_elimina_fatti_vecchi(db_url, radice, lavoratore) -> None:
    account = await crea_account(db_url)
    vecchio = radice / str(account.id) / "fatti" / "VECCHIO"
    vecchio.mkdir(parents=True)
    import os
    os.utime(vecchio, (0, 0))
    recente = radice / str(account.id) / "fatti" / "RECENTE"
    recente.mkdir()
    await lavoratore.giro()
    assert not vecchio.exists() and recente.exists()


async def test_identificativi_lavorazione_unici_per_cartella_attiva(db_url) -> None:
    """Vincolo «una attiva» (indice unico parziale) anche fuori dal codice."""
    account = await crea_account(db_url)
    l1 = await repository.prendi_in_carico(account.tenant_id, account.id, "X", "a", 60)
    l2 = await repository.prendi_in_carico(account.tenant_id, account.id, "X", "b", 60)
    assert l1 is not None and l2 is None
    with pytest.raises(Exception, match="uq_sftp_lavorazioni_attiva"):
        await sql(db_url, "INSERT INTO public.sftp_lavorazioni (tenant_id, account_id, cartella, "
                          "stato, lease_scade_il) VALUES ($1, $2, 'X', 'IN_CORSO', now())",
                  account.tenant_id, account.id)
    assert isinstance(l1.id, uuid.UUID)


async def test_responsabile_disattivato_cartelle_in_attesa(db_url, radice, lavoratore,
                                                           gateway_finto) -> None:
    """Come per l'accesso: senza responsabile attivo il canale non versa (il token
    sarebbe rifiutato) e la cartella resta in in/ senza esito, non scartata."""
    account = await crea_account(db_url)
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1",
              account.responsabile_id)
    await _deposita(radice, account, "PDV-1", {"a.pdf": PDF}, _indice({"a.pdf": PDF}))
    assert await lavoratore.giro() == 0
    assert (radice / str(account.id) / "in" / "PDV-1").is_dir()
    assert gateway_finto.chiamate_versamento == []


async def test_cartella_abbandonata_con_file_temporanei_scartata(db_url, radice,
                                                                 lavoratore) -> None:
    account = await crea_account(db_url)
    await _deposita(radice, account, "PDV-AB", {".tmp-a.pdf": PDF}, _indice({"a.pdf": PDF}))
    lavoratore.c = config_lavoratore(radice, abbandono_secondi=0, stabilita_secondi=3600)
    assert await lavoratore.giro() == 1
    esito = _esito(radice, account, "PDV-AB")
    assert esito["stato"] == "SCARTATO" and esito["errori"][0]["codice"] == "SFTP-105"
