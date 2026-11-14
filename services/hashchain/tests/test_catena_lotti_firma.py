# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test della vecchia catena a blocchi (formato 2, solo verifica e ripresa dei
blocchi in sospeso) e del client di firma (usato anche per il sigillo dei
checkpoint del registro R04). VD-C-019 (firma o «non firmato» esplicito,
verifica della catena).

R04: i test del ciclo a lotti del consumer Kafka (``ciclo_lotti``, ``Lotto``)
sono stati tolti insieme al consumer: la hashchain non consuma più
``aip.conserved`` (il registro legge ``catena_coda``; prove del sequenziatore
in test_r04_registro.py).
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from app.services.block_sealer import (
    calcola_hash_blocco,
    compute_block_hash,
    verifica_blocchi,
)
from app.services.firma_blocchi import (
    MOTIVO_CREDENZIALI_RIFIUTATE,
    MOTIVO_NON_QUALIFICATO,
    MOTIVO_SENZA_CREDENZIALI,
    STATO_DA_RITENTARE,
    STATO_FIRMATO,
    STATO_NON_FIRMATO,
    ClientServizioSignature,
)
from app.services.merkle import compute_merkle_root, compute_merkle_root_v1_legacy

ISTANTE = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=UTC)


def _h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


# ── Impronta del blocco ─────────────────────────────────────────────

def test_calcola_hash_blocco_deterministico():
    assert calcola_hash_blocco(1, "p", "m", ISTANTE, ["a"]) == calcola_hash_blocco(
        1, "p", "m", ISTANTE, ["a"])


def test_calcola_hash_blocco_cambia_con_istante():
    altro = ISTANTE + timedelta(microseconds=1)
    assert calcola_hash_blocco(1, "p", "m", ISTANTE, ["a"]) != calcola_hash_blocco(
        1, "p", "m", altro, ["a"])


def test_calcola_hash_blocco_cambia_con_elenco_aip():
    assert calcola_hash_blocco(1, "p", "m", ISTANTE, ["a", "b"]) != calcola_hash_blocco(
        1, "p", "m", ISTANTE, ["b", "a"])


def test_calcola_hash_blocco_istante_senza_fuso_errore():
    with pytest.raises(ValueError):
        calcola_hash_blocco(1, "p", "m", datetime(2026, 1, 1), [])


def test_calcola_hash_blocco_stesso_istante_fusi_diversi_uguale():
    roma = ISTANTE.astimezone(timezone(timedelta(hours=2)))
    assert calcola_hash_blocco(1, "p", "m", ISTANTE, []) == calcola_hash_blocco(
        1, "p", "m", roma, [])


# ── Verifica della catena (pura) ───────────────────────────────────

def _catena() -> tuple[list[dict], dict[str, str]]:
    impronte = {"aip-1": _h("1"), "aip-2": _h("2"), "aip-3": _h("3")}
    genesi = {"numero_blocco": 0, "hash_blocco": "GENESIS_" + "0" * 64,
              "hash_blocco_precedente": "0" * 64, "merkle_root": "0" * 64,
              "aip_ids": [], "versione_formato": 1, "created_at": ISTANTE,
              "stato_firma": "NON_FIRMATO"}
    m1 = compute_merkle_root_v1_legacy([impronte["aip-1"]])
    b1 = {"numero_blocco": 1, "hash_blocco_precedente": genesi["hash_blocco"], "merkle_root": m1,
          "aip_ids": ["aip-1"], "versione_formato": 1, "created_at": ISTANTE,
          "stato_firma": "NON_FIRMATO"}
    b1["hash_blocco"] = compute_block_hash(1, b1["hash_blocco_precedente"], m1)
    ids2 = ["aip-2", "aip-3"]
    m2 = compute_merkle_root([impronte[i] for i in ids2])
    b2 = {"numero_blocco": 2, "hash_blocco_precedente": b1["hash_blocco"], "merkle_root": m2,
          "aip_ids": ids2, "versione_formato": 2, "created_at": ISTANTE, "stato_firma": "FIRMATO"}
    b2["hash_blocco"] = calcola_hash_blocco(2, b1["hash_blocco"], m2, ISTANTE, ids2)
    return [genesi, b1, b2], impronte


def test_verifica_blocchi_catena_mista_integra():
    blocchi, impronte = _catena()
    integra, messaggio = verifica_blocchi(blocchi, impronte)
    assert integra, messaggio
    assert "1 blocchi senza firma qualificata" in messaggio


def test_verifica_blocchi_impronta_aip_alterata_rilevata():
    blocchi, impronte = _catena()
    impronte["aip-3"] = _h("alterato")
    integra, messaggio = verifica_blocchi(blocchi, impronte)
    assert not integra
    assert "Merkle" in messaggio


def test_verifica_blocchi_istante_alterato_rilevato():
    blocchi, impronte = _catena()
    blocchi[2]["created_at"] = ISTANTE + timedelta(days=1)
    assert verifica_blocchi(blocchi, impronte) == (False, "Hash non valido al blocco #2")


def test_verifica_blocchi_collegamento_rotto_rilevato():
    blocchi, impronte = _catena()
    blocchi[2]["hash_blocco_precedente"] = "f" * 64
    assert not verifica_blocchi(blocchi, impronte)[0]


def test_verifica_blocchi_numerazione_interrotta_rilevata():
    blocchi, impronte = _catena()
    blocchi[2]["numero_blocco"] = 3
    assert "Numerazione" in verifica_blocchi(blocchi, impronte)[1]


def test_verifica_blocchi_aip_scollegato_rilevato():
    blocchi, impronte = _catena()
    del impronte["aip-2"]
    assert not verifica_blocchi(blocchi, impronte)[0]


def test_verifica_blocchi_senza_genesi_rifiutata():
    blocchi, impronte = _catena()
    assert not verifica_blocchi(blocchi[1:], impronte)[0]


# ── Client del servizio di firma ───────────────────────────────────

def _client(gestore, token: str = "tok") -> ClientServizioSignature:  # noqa: S107 — di prova
    return ClientServizioSignature(
        "http://signature", token, transport=httpx.MockTransport(gestore))


async def test_firma_servizio_non_qualificato_non_firmato_esplicito():
    def gestore(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/signature/status"
        assert req.headers.get("authorization") == "Bearer tok"
        return httpx.Response(200, json={"firma": {"fornitore": "sviluppo", "qualificata": False}})

    esito = await _client(gestore).firma(b"\x01" * 32)
    assert esito.stato == STATO_NON_FIRMATO
    assert esito.motivo == MOTIVO_NON_QUALIFICATO \
        == "non firmato: servizio di firma non qualificato"


async def test_firma_servizio_irraggiungibile_da_ritentare():
    def gestore(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("rifiutata", request=req)

    esito = await _client(gestore).firma(b"\x01" * 32)
    assert esito.stato == STATO_DA_RITENTARE
    assert not esito.definitivo


async def test_firma_servizio_qualificato_firmato():
    ricevuto: dict = {}

    def gestore(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/status"):
            return httpx.Response(200, json={"firma": {"qualificata": True}})
        ricevuto["auth"] = req.headers.get("authorization")
        ricevuto["corpo"] = json.loads(req.content)
        return httpx.Response(200, json={"busta_b64": "QUJD", "qualificata": True,
                                         "data_marca": "2026-10-20T12:00:00+00:00"})

    esito = await _client(gestore).firma(b"\x02" * 32)
    assert esito.stato == STATO_FIRMATO
    assert esito.firma_b64 == "QUJD"
    assert esito.tsa_timestamp == datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert ricevuto["auth"] == "Bearer tok"
    assert ricevuto["corpo"]["formato"] == "cades_detached" and ricevuto[
        "corpo"]["con_marca"] is True


async def test_firma_restituita_non_qualificata_non_vale():
    """Busta prodotta dall'adattatore di sviluppo (qualificata=false): blocco NON_FIRMATO."""
    def gestore(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/status"):
            return httpx.Response(200, json={"firma": {"qualificata": True}})
        return httpx.Response(200, json={"busta_b64": "QUJD", "qualificata": False})

    esito = await _client(gestore).firma(b"\x02" * 32)
    assert (esito.stato, esito.motivo) == (STATO_NON_FIRMATO, MOTIVO_NON_QUALIFICATO)


@pytest.mark.parametrize("percorso_rifiutato", ["/status", "/firma"])
@pytest.mark.parametrize("stato_http", [401, 403])
async def test_firma_credenziali_rifiutate_da_ritentare(percorso_rifiutato, stato_http):
    """Revisione M2: il token di servizio scade e si ruota; un rifiuto lascia il
    blocco DA_FIRMARE (si firma dopo il rinnovo), non NON_FIRMATO per sempre."""
    def gestore(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith(percorso_rifiutato):
            return httpx.Response(stato_http, json={"codice": "SIG-401"})
        return httpx.Response(200, json={"firma": {"qualificata": True}})

    esito = await _client(gestore).firma(b"\x01" * 32)
    assert esito.stato == STATO_DA_RITENTARE and not esito.definitivo
    assert esito.motivo == MOTIVO_CREDENZIALI_RIFIUTATE


async def test_firma_senza_token_non_firmato():
    def gestore(req: httpx.Request) -> httpx.Response:
        raise AssertionError("nessuna chiamata senza credenziali")

    esito = await _client(gestore, token="").firma(b"\x01" * 32)
    assert (esito.stato, esito.motivo) == (STATO_NON_FIRMATO, MOTIVO_SENZA_CREDENZIALI)


async def test_firma_senza_url_non_configurato():
    esito = await ClientServizioSignature("", "tok").firma(b"\x01" * 32)
    assert esito.stato == STATO_NON_FIRMATO
    assert "non configurato" in esito.motivo
