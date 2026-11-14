# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Testimone su PostgreSQL reale: protocollo K3, fork, registro malevolo,
concorrenza, append-only, interventi dell'operatore.

Il database è creato vuoto e migrato dal servizio stesso (nessuno schema del
gateway): prova che il testimone non dipende dal database del registro.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

from libs.catena import nota as nota_lib
from tests.conftest import ORIGINE, intestazioni, richiede_pg, sql
from tests.supporto import RegistroDiProva

pytestmark = [pytest.mark.integration, richiede_pg]

URL = "/api/v1/testimone/checkpoint"


async def _invia(client, corpo: bytes, **kw):  # type: ignore[no-untyped-def]
    return await client.post(URL, content=corpo, headers=kw.pop("headers", intestazioni()), **kw)


async def _avvia(client, registro: RegistroDiProva, dimensione: int = 8):  # type: ignore[no-untyped-def]
    registro.aggiungi(dimensione)
    r = await _invia(client, registro.richiesta(0))
    assert r.status_code == 200, r.text
    return r


# ── Caso felice ────────────────────────────────────────────────────────


async def test_post_checkpoint_primo_e_crescita_controfirma(
    client, registro, chiavi, db_url
) -> None:
    """Primo checkpoint (fiducia al primo uso) e crescita con prova valida:
    la riga restituita è una firma valida del testimone sul corpo del checkpoint."""
    r = await _avvia(client, registro, 5)
    assert r.headers["content-type"].startswith("text/plain")
    riga = r.text
    assert riga.startswith("— verumdoc-testimone ")
    nota = nota_lib.aggiungi_firme(registro.checkpoint(5), [riga])
    assert len(nota_lib.verifica(nota, [chiavi["testimone"]])) == 1

    registro.aggiungi(7)
    r2 = await _invia(client, registro.richiesta(5))
    assert r2.status_code == 200, r2.text

    letta = await client.get(URL)
    assert letta.status_code == 200
    firme = nota_lib.verifica(letta.text, [chiavi["registro"], chiavi["testimone"]])
    assert {f.nome for f in firme} == {"verumdoc-registro", "verumdoc-testimone"}
    assert nota_lib.analizza(letta.text).dimensione == 12

    vecchio = await client.get(f"{URL}/5")
    assert vecchio.status_code == 200 and nota_lib.analizza(vecchio.text).dimensione == 5
    assert (await client.get(f"{URL}/6")).json()["codice"] == "TST-404"

    stato = (await client.get("/api/v1/testimone/stato")).json()
    assert stato["stato"] == "ATTIVO" and stato["dimensione"] == 12
    assert stato["chiave_testimone"] == chiavi["testimone"]


async def test_post_checkpoint_ripetuto_idempotente(client, registro) -> None:
    r1 = await _avvia(client, registro, 4)
    r2 = await _invia(client, registro.richiesta(0, 4))
    assert r2.status_code == 200 and r2.text == r1.text


async def test_catena_propria_del_testimone_collegata(client, registro, db_url) -> None:
    """Ogni riga controfirmata è legata alla precedente: SHA-256(precedente || nota)."""
    await _avvia(client, registro, 2)
    for n in (3, 4, 9):
        vecchia = len(registro.foglie)
        registro.aggiungi(n - vecchia)
        assert (await _invia(client, registro.richiesta(vecchia))).status_code == 200
    righe = await sql(
        db_url,
        "SELECT impronta, impronta_precedente, nota_controfirmata "
        "FROM testimone.checkpoint_controfirmati ORDER BY id",
    )
    precedente = bytes(32)
    for riga in righe:
        assert bytes(riga["impronta_precedente"]) == precedente
        attesa = hashlib.sha256(precedente + riga["nota_controfirmata"].encode()).digest()
        assert bytes(riga["impronta"]) == attesa
        precedente = attesa


# ── Rifiuti ────────────────────────────────────────────────────────────


async def test_post_checkpoint_old_superato_409_testo(client, registro) -> None:
    await _avvia(client, registro, 6)
    registro.aggiungi(2)
    r = await _invia(client, registro.richiesta(0))
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("text/plain") and r.text == "6\n"


async def test_post_checkpoint_senza_token_401(client, registro) -> None:
    registro.aggiungi(1)
    r = await client.post(URL, content=registro.richiesta(0))
    assert r.status_code == 401 and r.json()["codice"] == "TST-401"
    r = await _invia(client, registro.richiesta(0), headers=intestazioni("altro-token"))
    assert r.status_code == 401


async def test_post_checkpoint_firma_di_altra_chiave_403(client) -> None:
    priv, _ = nota_lib.genera_chiave("verumdoc-registro")
    falso = RegistroDiProva(ORIGINE, priv)
    falso.aggiungi(3)
    r = await _invia(client, falso.richiesta(0))
    assert r.status_code == 403 and r.json()["codice"] == "TST-403"


async def test_post_checkpoint_origine_non_ammessa_404(client, chiavi) -> None:
    altro = RegistroDiProva("verumdoc.it/registro/v1/altro", chiavi["registro_privata"])
    altro.aggiungi(2)
    r = await _invia(client, altro.richiesta(0))
    assert r.status_code == 404 and r.json()["codice"] == "TST-405"


async def test_post_checkpoint_corpo_malformato_400_e_troppo_grande_413(client, registro) -> None:
    r = await _invia(client, b"old x\n\nnon una nota\n")
    assert r.status_code == 400 and r.json()["codice"] == "TST-400"
    r = await _invia(client, b"old 0\n" + b"A" * 70000)
    assert r.status_code == 413 and r.json()["codice"] == "TST-413"


async def test_post_checkpoint_prova_non_valida_422_allarme_non_bloccante(
    client, registro, db_url
) -> None:
    await _avvia(client, registro, 6)
    registro.aggiungi(2)
    prova = registro.prova(6)
    prova[0] = bytes(32)
    r = await _invia(client, registro.corpo(6, prova, registro.checkpoint()))
    assert r.status_code == 422 and r.json()["codice"] == "TST-422"
    allarmi = await sql(db_url, "SELECT tipo, blocca FROM testimone.allarmi")
    assert [(a["tipo"], a["blocca"]) for a in allarmi] == [("PROVA_NON_VALIDA", False)]
    # Non bloccante: la richiesta corretta passa
    assert (await _invia(client, registro.richiesta(6))).status_code == 200


# ── Registro malevolo e fork ───────────────────────────────────────────


async def test_registro_malevolo_riscrive_la_storia_rifiutato_e_bloccato(
    client, registro, db_url
) -> None:
    """Il registro riscrive la foglia 3 dopo che il checkpoint 8 è stato
    controfirmato. (1) Il nuovo checkpoint 12 non si lega all'8 controfirmato:
    422 e nessuna controfirma. (2) Il registro ripresenta la dimensione 8 con la
    radice riscritta: due note firmate con la stessa dimensione e radici
    diverse = fork → 423, stato COMPROMESSO persistente, poi nessuna controfirma."""
    await _avvia(client, registro, 8)
    onesto = registro.checkpoint(8)
    registro.riscrivi(3, b"documento sostituito")
    registro.aggiungi(4)

    r = await _invia(client, registro.richiesta(8))
    assert r.status_code == 422

    r = await _invia(client, registro.corpo(8, [], registro.checkpoint(8)))
    assert r.status_code == 423 and r.json()["codice"] == "TST-423"
    assert r.json()["dettagli"]["stato"] == "COMPROMESSO"

    stato = await sql(
        db_url, "SELECT stato, compromesso_dal, dimensione FROM testimone.stato_registro"
    )
    assert stato[0]["stato"] == "COMPROMESSO" and stato[0]["compromesso_dal"] is not None
    assert stato[0]["dimensione"] == 8
    tipi = [a["tipo"] for a in await sql(db_url, "SELECT tipo FROM testimone.allarmi ORDER BY id")]
    assert tipi == ["PROVA_NON_VALIDA", "FORK"]

    # Anche una richiesta formalmente corretta è rifiutata finché nessuno interviene
    onesto_corpo = RegistroDiProva.corpo(0, [], onesto)
    assert (await _invia(client, onesto_corpo)).status_code == 423
    # Le letture pubbliche restano disponibili e mostrano l'ultimo controfirmato
    letta = await client.get(URL)
    assert letta.status_code == 200 and nota_lib.analizza(letta.text).dimensione == 8
    assert (await client.get("/api/v1/testimone/stato")).json()["stato"] == "COMPROMESSO"
    metriche = (await client.get("/metrics")).text
    assert f'verumdoc_testimone_compromesso{{origine="{ORIGINE}"}} 1.0' in metriche
    assert "verumdoc_testimone_allarmi_totali" in metriche


async def test_fork_due_rami_in_parallelo_uno_solo_controfirmato(
    client, registro, chiavi, db_url
) -> None:
    """Due richieste concorrenti (due connessioni reali) con la stessa
    dimensione e due rami diversi, entrambi consistenti con l'8 controfirmato:
    una sola controfirma, l'altra rileva il fork."""
    await _avvia(client, registro, 8)
    ramo_a = RegistroDiProva(ORIGINE, chiavi["registro_privata"])
    ramo_b = RegistroDiProva(ORIGINE, chiavi["registro_privata"])
    ramo_a.foglie = list(registro.foglie)
    ramo_b.foglie = list(registro.foglie)
    ramo_a.aggiungi(2, seme="a")
    ramo_b.aggiungi(2, seme="b")
    risposte = await asyncio.gather(
        _invia(client, ramo_a.richiesta(8)), _invia(client, ramo_b.richiesta(8))
    )
    assert sorted(r.status_code for r in risposte) == [200, 423]
    righe = await sql(
        db_url, "SELECT dimensione FROM testimone.checkpoint_controfirmati WHERE dimensione = 10"
    )
    assert len(righe) == 1
    assert (await sql(db_url, "SELECT stato FROM testimone.stato_registro"))[0][
        "stato"
    ] == "COMPROMESSO"


async def test_stesso_checkpoint_in_parallelo_una_sola_riga(client, registro, db_url) -> None:
    await _avvia(client, registro, 3)
    registro.aggiungi(5)
    corpo = registro.richiesta(3)
    risposte = await asyncio.gather(*(_invia(client, corpo) for _ in range(4)))
    assert [r.status_code for r in risposte] == [200] * 4
    assert len({r.text for r in risposte}) == 1
    assert (
        len(
            await sql(
                db_url, "SELECT 1 FROM testimone.checkpoint_controfirmati WHERE dimensione = 8"
            )
        )
        == 1
    )


# ── Append-only ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "istruzione",
    [
        "UPDATE testimone.checkpoint_controfirmati SET dimensione = 99",
        "DELETE FROM testimone.checkpoint_controfirmati",
        "TRUNCATE testimone.checkpoint_controfirmati CASCADE",
        "TRUNCATE testimone.allarmi",
        "DELETE FROM testimone.stato_registro",
    ],
)
async def test_tabelle_di_prova_append_only(client, registro, db_url, istruzione: str) -> None:
    import asyncpg

    await _avvia(client, registro, 2)
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError, match="append-only"):
        await sql(db_url, istruzione)


# ── Interventi dell'operatore ──────────────────────────────────────────


async def _forza_fork(client, registro: RegistroDiProva) -> str:  # type: ignore[no-untyped-def]
    await _avvia(client, registro, 8)
    onesto = registro.checkpoint(8)
    falso = registro.checkpoint(8, radice=bytes(range(32)))
    assert (await _invia(client, RegistroDiProva.corpo(8, [], falso))).status_code == 423
    return onesto


async def test_operatore_sblocca_e_il_registro_riprende_dalla_testa(
    client, registro, db_url
) -> None:
    from app.application.stato import InterventoNonAmmesso, sblocca

    from libs.shared.db import get_async_session

    await _forza_fork(client, registro)
    async with get_async_session() as s:
        with pytest.raises(InterventoNonAmmesso):
            await sblocca(s, ORIGINE, operatore="Operatore", motivo="corto")
    async with get_async_session() as s:
        esito = await sblocca(
            s,
            ORIGINE,
            operatore="Operatore del testimone",
            motivo="Indagine conclusa: il ramo controfirmato è quello integro",
        )
    assert esito.azione == "SBLOCCO" and esito.dimensione == 8
    registro.aggiungi(3)
    assert (await _invia(client, registro.richiesta(8))).status_code == 200
    interventi = await sql(
        db_url, "SELECT azione, stato_precedente FROM testimone.interventi_operatore"
    )
    assert [(i["azione"], i["stato_precedente"]) for i in interventi] == [
        ("SBLOCCO", "COMPROMESSO")
    ]


async def test_operatore_nuova_base_apre_epoca(client, registro, configurazione, db_url) -> None:
    from app.application.stato import nuova_base
    from app.avvio import carica_contesto
    from app.osservabilita import MetricheTestimone

    from libs.shared.db import get_async_session

    await _forza_fork(client, registro)
    ctx = carica_contesto(configurazione, MetricheTestimone())
    ricostruito = RegistroDiProva(ORIGINE, _privata(registro))
    ricostruito.foglie = list(registro.foglie)
    ricostruito.riscrivi(0, b"registro ricostruito dal WORM")
    cp = ctx.verifica_registro(ricostruito.checkpoint(8))
    async with get_async_session() as s:
        esito = await nuova_base(
            s,
            cp,
            chiave=ctx.chiave,
            operatore="Operatore del testimone",
            motivo="Registro ricostruito dal WORM dopo l'incidente",
        )
    assert esito.epoca == 2
    stato = (await client.get("/api/v1/testimone/stato")).json()
    assert stato["stato"] == "ATTIVO" and stato["epoca"] == 2
    assert stato["radice"] == base64.b64encode(ricostruito.radice(8)).decode()
    ricostruito.aggiungi(2)
    assert (await _invia(client, ricostruito.richiesta(8))).status_code == 200
    # Le controfirme dell'epoca 1 restano come prova
    epoche = await sql(
        db_url, "SELECT epoca, dimensione FROM testimone.checkpoint_controfirmati ORDER BY id"
    )
    assert [(e["epoca"], e["dimensione"]) for e in epoche] == [(1, 8), (2, 8), (2, 10)]


def _privata(registro: RegistroDiProva) -> str:
    return nota_lib.chiave_firmatario(registro.firmatario.nome, registro.firmatario.chiave_privata)
