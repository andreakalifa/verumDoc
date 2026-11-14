# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12a (VD-C-152) — collegamento d'invito sigillato per la posta.

  - il messaggio di ``notification.send`` porta il collegamento SIGILLATO con la
    chiave pubblica della posta: nella coda (``public.notifications``) non c'è
    mai in chiaro; si apre solo all'invio, in memoria, e il testo inviato lo
    contiene;
  - senza chiave privata il messaggio non parte (NON_PARTITA, mai in chiaro);
  - il sigillo è legato a ente, oggetto e destinatario: una riga a cui si cambia
    il destinatario non si apre e non parte;
  - un «segreto» non sigillato non entra nella coda;
  - dopo l'invio la riga non conserva sigilli né (per gli inviti accodati in
    chiaro prima di R12a, che partono come prima) il collegamento.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from email import message_from_bytes

import pytest
from app.services.coda import INVIATA, NON_PARTITA, CodaUscitaSQL
from app.services.elaborazione import accoda_esplicita
from app.services.invio import invia_lotto

from libs.shared.security.sigillo import (
    SEGNAPOSTO,
    Apritore,
    contesto_posta,
    genera_coppia,
    sigilla,
)
from tests.conftest import config_smtp
from tests.infra_reale import (
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

ADESSO = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PRIVATA, PUBBLICA = genera_coppia()
APRITORE = Apritore.da_impostazioni(PRIVATA)
COLLEGAMENTO = "https://console.prova.example/attiva#invito=abc.SEGRETO-DI-PROVA"


def _evento(tenant: str, dest: str = "persona@cliente-prova.it", *, sigillato: bool = True,
            invito: str | None = None) -> dict:
    oggetto_id = f"invito:{invito or uuid.uuid4()}"
    evento = {"evento": "accesso.invito", "tipo": "email", "tenant_id": tenant,
              "oggetto_id": oggetto_id, "destinatario": dest, "oggetto": "Invito a VerumDoc"}
    if sigillato:
        evento["testo"] = f"Apri questo collegamento:\n{SEGNAPOSTO % 'collegamento'}\nGrazie."
        evento["segreti"] = {"collegamento": sigilla(
            COLLEGAMENTO, PUBBLICA, contesto_posta(tenant, oggetto_id, dest))}
    else:
        evento["testo"] = f"Apri questo collegamento:\n{COLLEGAMENTO}\nGrazie."
    return evento


async def _lettore(_tid: str) -> dict:
    return {"email_referente": None, "pec_referente": None, "stato": "ATTIVO"}


async def _nessun_oggetto(*_a: str) -> None:
    return None


class _DLQ:
    def __init__(self) -> None:
        self.eventi: list[dict] = []

    async def __call__(self, evento: dict) -> None:
        self.eventi.append(evento)


async def _invia(coda, smtp, apritore=APRITORE):  # type: ignore[no-untyped-def]
    dlq = _DLQ()
    esiti = await invia_lotto(coda, config_smtp(smtp.porta, "nessuna"), adesso=ADESSO,
                              ritardi_secondi=[60], lotto=10, recupero_dopo_secondi=600,
                              pubblica_dlq=dlq, apritore=apritore)
    return esiti, dlq


def _testo_ricevuto(smtp) -> str:  # type: ignore[no-untyped-def]
    msg = message_from_bytes(smtp.gestore.ricevuti[-1].content)
    parte = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    return parte.get_payload(decode=True).decode()


# ── coda in memoria (fixture «coda») ─────────────────────────────────

async def test_sigillato_aperto_solo_all_invio(coda, smtp_in_chiaro) -> None:
    tenant = str(uuid.uuid4())
    assert await accoda_esplicita(_evento(tenant), coda, adesso=ADESSO, lettore=_lettore,
                                  lettore_oggetto=_nessun_oggetto) == 1
    riga = coda.righe[0]
    assert "SEGRETO-DI-PROVA" not in json.dumps(riga.payload)
    assert riga.payload["segreti"]["collegamento"].startswith("vdsig1$")
    esiti, dlq = await _invia(coda, smtp_in_chiaro)
    assert esiti["inviate"] == 1 and dlq.eventi == []
    assert COLLEGAMENTO in _testo_ricevuto(smtp_in_chiaro)
    assert riga.stato == INVIATA and "segreti" not in riga.payload


async def test_senza_chiave_privata_non_parte(coda, smtp_in_chiaro) -> None:
    await accoda_esplicita(_evento(str(uuid.uuid4())), coda, adesso=ADESSO, lettore=_lettore,
                           lettore_oggetto=_nessun_oggetto)
    esiti, dlq = await _invia(coda, smtp_in_chiaro, apritore=None)
    assert esiti["non_partite"] == 1 and smtp_in_chiaro.gestore.ricevuti == []
    assert coda.righe[0].stato == NON_PARTITA
    assert "POSTA_CHIAVE_PRIVATA" in coda.righe[0].errore
    assert "testo" not in dlq.eventi[0] and "vdsig1$" not in json.dumps(dlq.eventi[0])


async def test_destinatario_cambiato_nella_coda_non_parte(coda, smtp_in_chiaro) -> None:
    """Chi scrive nella coda e cambia il destinatario non riceve il collegamento."""
    await accoda_esplicita(_evento(str(uuid.uuid4())), coda, adesso=ADESSO, lettore=_lettore,
                           lettore_oggetto=_nessun_oggetto)
    coda.righe[0].destinatario = "attaccante@cliente-prova.it"
    esiti, _ = await _invia(coda, smtp_in_chiaro)
    assert esiti["non_partite"] == 1 and smtp_in_chiaro.gestore.ricevuti == []
    assert "non apribile" in coda.righe[0].errore


async def test_segreto_non_sigillato_non_entra_nella_coda(coda) -> None:
    evento = _evento(str(uuid.uuid4()))
    evento["segreti"] = {"collegamento": COLLEGAMENTO}
    assert await accoda_esplicita(evento, coda, adesso=ADESSO, lettore=_lettore,
                                  lettore_oggetto=_nessun_oggetto) == 0
    riga = coda.righe[0]
    assert riga.stato == NON_PARTITA and "SEGRETO" not in json.dumps(riga.payload)


async def test_invito_in_chiaro_precedente_parte_come_prima(coda, smtp_in_chiaro) -> None:
    """Nessuna regressione: un invito accodato in chiaro prima di R12a parte."""
    await accoda_esplicita(_evento(str(uuid.uuid4()), sigillato=False), coda, adesso=ADESSO,
                           lettore=_lettore, lettore_oggetto=_nessun_oggetto)
    esiti, _ = await _invia(coda, smtp_in_chiaro)
    assert esiti["inviate"] == 1 and COLLEGAMENTO in _testo_ricevuto(smtp_in_chiaro)


def test_avvio_in_produzione_senza_chiave_privata() -> None:
    from types import SimpleNamespace

    from app.application.configurazione import problemi_avvio
    from app.config import settings

    for chiave, atteso in (("", "POSTA_CHIAVE_PRIVATA mancante"),
                           ("non valida!", "chiave privata della posta non valida")):
        s = SimpleNamespace(**{**settings.__dict__, "env": "production",
                               "posta_chiave_privata": type(settings.webhook_chiave_cifratura)(
                                   chiave),
                               "in_produzione": True})
        assert any(atteso in p for p in problemi_avvio(s))


# ── PostgreSQL vero: minimizzazione dopo l'invio ─────────────────────

@pytest.mark.integration
@richiede_pg
async def test_sql_dopo_l_invio_nessun_segreto(db_url, smtp_in_chiaro) -> None:  # noqa: F811
    tenant, _ = await crea_tenant_e_classe(db_url)
    coda = CodaUscitaSQL()
    for sigillato in (True, False):
        await accoda_esplicita(_evento(str(tenant), sigillato=sigillato), coda, adesso=ADESSO,
                               lettore=_lettore, lettore_oggetto=_nessun_oggetto)
    righe = await sql(db_url, "SELECT payload_json::text AS p FROM public.notifications")
    assert sum("SEGRETO-DI-PROVA" in r["p"] for r in righe) == 1  # solo l'invito precedente
    esiti, _ = await _invia(coda, smtp_in_chiaro)
    assert esiti["inviate"] == 2
    righe = await sql(db_url, "SELECT stato, payload_json::text AS p FROM public.notifications")
    assert {r["stato"] for r in righe} == {INVIATA}
    for r in righe:
        assert "SEGRETO-DI-PROVA" not in r["p"] and "vdsig1$" not in r["p"]
        assert "rimosso dopo l'invio" in json.loads(r["p"])["testo"] or \
            SEGNAPOSTO % "collegamento" in json.loads(r["p"])["testo"]
