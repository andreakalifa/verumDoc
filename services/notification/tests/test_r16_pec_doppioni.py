# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R16 (VD-C-171) — consegna PEC senza doppioni dopo un arresto fra l'accettazione SMTP del
gestore e la scrittura di INVIATO, con il simulatore SMTP+IMAP e PostgreSQL veri.

  - Message-ID deterministico (``<pec.<id>@<dominio della casella>>``);
  - arresto DOPO l'accettazione del gestore: al giro successivo nessun reinvio (la casella
    non è ancora stata letta), poi le ricevute portano il messaggio a CONSEGNATO: una sola
    PEC al destinatario (prima di R16: due);
  - arresto PRIMA che il messaggio arrivi al gestore: si rimanda (stesso Message-ID) solo
    dopo una lettura riuscita della casella oltre la soglia di recupero;
  - ricevuta presente con quel Message-ID ma non associabile: INVIATO riconciliato, nessun
    reinvio.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.application import pec as app_pec
from app.infrastructure.pec_canale import costruisci

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

from .test_r13b_pec import (  # noqa: F401 — fixture e helper comuni
    CASELLA,
    _accoda,
    _canale,
    _ente,
    _giro,
    _imp,
    _messaggi,
    simulatore,
)

pytestmark = richiede_pg


class Arresto(RuntimeError):
    """Il processo «muore» qui (simulato)."""


def _costruttore_con_arresto(*, dopo_smtp: bool):
    def costruttore(canale, segreti, vincoli, *, produzione):  # type: ignore[no-untyped-def]
        vero = costruisci(canale, segreti, vincoli, produzione=produzione)
        invia = vero.invia

        async def invia_e_muori(destinatario, oggetto, testo, message_id):  # type: ignore[no-untyped-def]
            if dopo_smtp:
                await invia(destinatario, oggetto, testo, message_id)
            raise Arresto("arresto fra l'accettazione SMTP e la scrittura di INVIATO")

        vero.invia = invia_e_muori  # type: ignore[method-assign]
        return vero
    return costruttore


async def _arresto(imp, adesso, *, dopo_smtp: bool) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(Arresto):
        await app_pec.giro_pec(imp, adesso=adesso,
                               costruttore=_costruttore_con_arresto(dopo_smtp=dopo_smtp))


async def test_message_id_deterministico(db_url, simulatore) -> None:  # noqa: F811
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    await _giro(_imp())
    m = (await _messaggi(db_url))[0]
    assert m["message_id"] == app_pec.message_id(str(m["id"]), CASELLA)
    assert m["message_id"] == f"<pec.{m['id']}@{CASELLA.split('@')[1]}>"
    assert app_pec.message_id("x", "senza-dominio") == "<pec.x@verumdoc.invalid>"


async def test_arresto_dopo_l_accettazione_nessun_doppione(db_url, simulatore) -> None:  # noqa: F811
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    inizio = datetime.now(UTC)
    await _arresto(_imp(), inizio, dopo_smtp=True)
    assert len(simulatore.inviati) == 1
    assert (await _messaggi(db_url))[0]["stato"] == "IN_INVIO"
    # oltre la soglia di recupero: la casella non è stata letta → nessun reinvio; la
    # lettura dello stesso giro trova accettazione e consegna
    conteggi = await _giro(_imp(), inizio + timedelta(seconds=1000))
    assert conteggi["recuperati"] == 0 and conteggi["inviati"] == 0
    assert conteggi["ricevute_associate"] == 2
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "CONSEGNATO"
    assert (await _giro(_imp(), inizio + timedelta(seconds=3000)))["inviati"] == 0
    assert len(simulatore.inviati) == 1  # una sola PEC al destinatario


async def test_arresto_prima_del_gestore_si_rimanda_dopo_una_lettura(db_url,  # noqa: F811
                                                                    simulatore) -> None:
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    inizio = datetime.now(UTC)
    await _arresto(_imp(), inizio, dopo_smtp=False)
    assert simulatore.inviati == []
    primo = (await _messaggi(db_url))[0]["message_id"]
    # primo giro dopo la soglia: nessuna lettura riuscita dopo l'arresto → resta IN_INVIO
    conteggi = await _giro(_imp(), inizio + timedelta(seconds=1000))
    assert conteggi["recuperati"] == 0 and simulatore.inviati == []
    assert (await _messaggi(db_url))[0]["stato"] == "IN_INVIO"
    # la casella è stata letta (nessuna ricevuta): al giro dopo si rimanda, stesso Message-ID
    conteggi = await _giro(_imp(), inizio + timedelta(seconds=1100))
    assert conteggi["recuperati"] == 1 and conteggi["inviati"] == 1
    assert len(simulatore.inviati) == 1
    m = (await _messaggi(db_url))[0]
    assert m["message_id"] == primo and m["stato"] == "CONSEGNATO"


async def test_ricevuta_non_associabile_con_quel_message_id_riconcilia(db_url,  # noqa: F811
                                                                      simulatore) -> None:
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    inizio = datetime.now(UTC)
    await _arresto(_imp(), inizio, dopo_smtp=False)
    m = (await _messaggi(db_url))[0]
    await sql(db_url, "INSERT INTO public.pec_ricevute (canale_codice, imap_uidvalidity, "
                      "imap_uid, esito, motivo, msgid, ricevuta_il) VALUES ('pec-prova', 1, "
                      "999999, 'NON_ASSOCIATA', 'prova R16', $1, now())", m["message_id"])
    await sql(db_url, "UPDATE public.pec_canali_stato SET ultima_lettura_il = $1",
              inizio + timedelta(seconds=950))
    conteggi = await _giro(_imp(), inizio + timedelta(seconds=1000))
    assert conteggi["riconciliati"] == 1 and conteggi["recuperati"] == 0
    assert simulatore.inviati == []
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "INVIATO" and "nessun reinvio" in m["errore"]


async def test_messaggi_di_altri_non_toccati(db_url, simulatore) -> None:  # noqa: F811
    """Un messaggio IN_INVIO recente (sotto la soglia) non si riconcilia né si rimanda."""
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t, pratica=str(uuid.uuid4()))
    inizio = datetime.now(UTC)
    await _arresto(_imp(), inizio, dopo_smtp=False)
    conteggi = await _giro(_imp(), inizio + timedelta(seconds=10))
    assert conteggi["riconciliati"] == 0 and conteggi["recuperati"] == 0
