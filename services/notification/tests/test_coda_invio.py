# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Destinatari reali, coda di uscita, ritentativi e DLQ — VD-C-024.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.services.coda import IN_CODA, INVIATA, NON_PARTITA, NuovoMessaggio
from app.services.consumer import gestisci_messaggio
from app.services.destinatari import destinatari
from app.services.elaborazione import ScartoEvento, accoda_esplicita, accoda_evento
from app.services.invio import invia_lotto
from app.services.smtp import INVIATA as ESITO_INVIATA
from app.services.smtp import TEMPORANEO, EsitoInvio

from tests.conftest import config_smtp

ADESSO = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TENANT = "00000000-0000-0000-0000-000000000001"
RITARDI = [60, 300]


def _lettore(email: str | None = "referente@cliente-prova.it", stato: str = "ATTIVO"):
    async def _leggi(tenant_id: str):
        if email is None:
            return None
        return {"email_referente": email, "pec_referente": None, "stato": stato}

    return _leggi


EVENTO_AIP = {"tenant_id": TENANT, "aip_id": "aip-1", "urn": "urn:IT:X:2026:1"}
EVENTO_PDV = {
    "tenant_id": TENANT,
    "pdv_id": "pdv-1",
    "risultati": [{"valido": False, "msg": "impronta errata"}, {"valido": True}],
}


# ── destinatari ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_destinatari_dal_referente_del_cliente():
    d = await destinatari(TENANT, "aip.conserved", lettore=_lettore())
    assert d.indirizzi == ["referente@cliente-prova.it"]


@pytest.mark.asyncio
async def test_destinatari_segnaposto_rifiutato():
    """tenant-x@verumdoc.local non è un destinatario: nessun indirizzo, motivo esplicito."""
    d = await destinatari(
        TENANT, "aip.conserved", lettore=_lettore(f"tenant-{TENANT}@verumdoc.local")
    )
    assert d.indirizzi == [] and "destinatario non configurato" in d.motivo


@pytest.mark.asyncio
async def test_destinatari_cliente_sospeso_o_inesistente():
    assert (
        await destinatari(TENANT, "aip.conserved", lettore=_lettore(stato="SOSPESO"))
    ).indirizzi == []
    d = await destinatari(TENANT, "aip.conserved", lettore=_lettore(None))
    assert d.motivo == "cliente inesistente"
    assert (await destinatari("non-uuid", "aip.conserved", lettore=_lettore())).indirizzi == []


# ── accodamento ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accoda_evento_destinatario_reale_e_deduplica(coda):
    """Un evento → un messaggio IN_CODA al referente; la riconsegna Kafka non duplica."""
    assert (
        await accoda_evento("aip.conserved", EVENTO_AIP, coda, adesso=ADESSO, lettore=_lettore())
        == 1
    )
    assert (
        await accoda_evento("aip.conserved", EVENTO_AIP, coda, adesso=ADESSO, lettore=_lettore())
        == 0
    )
    assert len(coda.righe) == 1
    r = coda.righe[0]
    assert r.stato == IN_CODA and r.destinatario == "referente@cliente-prova.it"
    assert "urn:IT:X:2026:1" in r.payload["testo"]
    assert "immutabile" not in r.payload["testo"]


@pytest.mark.asyncio
async def test_accoda_evento_senza_destinatario_non_partita(coda):
    """Senza referente valido: riga NON_PARTITA con il motivo (visibile in dashboard)."""
    await accoda_evento("pdv.invalid", EVENTO_PDV, coda, adesso=ADESSO, lettore=_lettore(""))
    assert coda.righe[0].stato == NON_PARTITA
    assert "destinatario non configurato" in coda.righe[0].errore
    assert "impronta errata" in coda.righe[0].payload["testo"]


@pytest.mark.asyncio
async def test_accoda_esplicita_pec_e_webhook_dichiarati_non_disponibili(coda):
    await accoda_esplicita(
        {"tipo": "pec", "tenant_id": TENANT, "destinatario": "x@pec.cliente.it",
         "oggetto": "o", "oggetto_id": "1"},
        coda,
        adesso=ADESSO,
        lettore=_lettore(),
    )
    await accoda_esplicita(
        {
            "tipo": "webhook",
            "tenant_id": TENANT,
            "destinatario": "https://cliente.it/h",
            "oggetto": "o",
            "oggetto_id": "2",
        },
        coda,
        adesso=ADESSO,
        lettore=_lettore(),
    )
    assert [r.stato for r in coda.righe] == [NON_PARTITA, NON_PARTITA]
    assert "PEC non disponibile" in coda.righe[0].errore


@pytest.mark.asyncio
async def test_accoda_esplicita_email_valida(coda):
    n = await accoda_esplicita(
        {
            "tipo": "email",
            "tenant_id": TENANT,
            "destinatario": "a@cliente.it",
            "oggetto": "Prova",
            "testo": "t",
            "oggetto_id": "x",
        },
        coda,
        adesso=ADESSO,
        lettore=_lettore(),
    )
    assert n == 1 and coda.righe[0].stato == IN_CODA


# ── cliente obbligatorio ─────────────────────────────────────────────


def _lettore_oggetto(mappa: dict[str, str]):
    async def _leggi(pdv_id: str, aip_id: str):
        return mappa.get(pdv_id) or mappa.get(aip_id)

    return _leggi


def test_nuovo_messaggio_senza_cliente_rifiutato():
    for valore in ("", None, "non-uuid"):
        with pytest.raises(ValueError, match="cliente"):
            NuovoMessaggio(tenant_id=valore, tipo="aip.conserved", oggetto_id="a",
                           destinatario="x@y.it", oggetto="o", testo="t")


@pytest.mark.asyncio
async def test_accoda_evento_cliente_risolto_dal_pdv(coda):
    """Evento senza tenant_id: il cliente si ricava dal PdV a cui si riferisce."""
    evento = {k: v for k, v in EVENTO_PDV.items() if k != "tenant_id"}
    await accoda_evento("pdv.invalid", evento, coda, adesso=ADESSO, lettore=_lettore(),
                        lettore_oggetto=_lettore_oggetto({"pdv-1": TENANT}))
    assert [r.tenant_id for r in coda.righe] == [TENANT]


@pytest.mark.asyncio
async def test_accoda_evento_cliente_non_risolvibile_scartato(coda):
    """Nessun tenant_id e oggetto sconosciuto: nessuna riga, ScartoEvento con motivo."""
    evento = {"aip_id": "aip-ignoto", "urn": "u"}
    with pytest.raises(ScartoEvento, match="cliente non risolvibile"):
        await accoda_evento("aip.conserved", evento, coda, adesso=ADESSO, lettore=_lettore(),
                            lettore_oggetto=_lettore_oggetto({}))
    assert coda.righe == []


@pytest.mark.asyncio
async def test_accoda_evento_cliente_inesistente_scartato(coda):
    """Prima diventava una riga NON_PARTITA con un cliente inesistente (FK violata)."""
    with pytest.raises(ScartoEvento, match="cliente inesistente"):
        await accoda_evento("aip.conserved", EVENTO_AIP, coda, adesso=ADESSO,
                            lettore=_lettore(None))
    assert coda.righe == []


@pytest.mark.asyncio
async def test_accoda_esplicita_senza_cliente_scartata(coda):
    with pytest.raises(ScartoEvento):
        await accoda_esplicita(
            {"tipo": "email", "destinatario": "a@cliente.it", "oggetto": "o", "testo": "t"},
            coda, adesso=ADESSO, lettore=_lettore(), lettore_oggetto=_lettore_oggetto({}),
        )
    assert coda.righe == []


@pytest.mark.asyncio
async def test_accoda_esplicita_senza_oggetto_deduplica_per_contenuto(coda):
    evento = {"tipo": "email", "tenant_id": TENANT, "destinatario": "a@cliente.it",
              "oggetto": "Avviso", "testo": "t"}
    assert await accoda_esplicita(evento, coda, adesso=ADESSO, lettore=_lettore()) == 1
    assert await accoda_esplicita(evento, coda, adesso=ADESSO, lettore=_lettore()) == 0
    assert await accoda_esplicita({**evento, "testo": "altro"}, coda, adesso=ADESSO,
                                  lettore=_lettore()) == 1
    assert coda.righe[0].payload["oggetto_id"].startswith("contenuto:")


@pytest.mark.asyncio
async def test_gestisci_messaggio_scarto_subito_in_dlq_con_motivo():
    """ScartoEvento: nessun ritentativo, DLQ con il motivo, offset confermabile."""
    chiamate = []

    async def _elabora(topic, evento):
        chiamate.append(1)
        raise ScartoEvento("cliente non risolvibile: prova")

    async def _attendi(s):
        raise AssertionError("nessuna attesa per uno scarto")

    dlq = _DLQ()
    esito = await gestisci_messaggio("aip.conserved", 0, 5, {"aip_id": "x"}, _elabora, dlq,
                                     ritardi=[1.0, 2.0], attendi=_attendi)
    assert esito == "dlq" and len(chiamate) == 1
    assert dlq.eventi[0]["motivo"] == "cliente non risolvibile: prova"
    assert dlq.eventi[0]["retries"] == 1


# ── invio ────────────────────────────────────────────────────────────


class _DLQ:
    def __init__(self) -> None:
        self.eventi: list[dict] = []

    async def __call__(self, evento: dict) -> None:
        self.eventi.append(evento)


@pytest.mark.asyncio
async def test_invio_reale_starttls_segna_inviata(coda, smtp_starttls, certificato_tls):
    """Coda → SMTP simulato con STARTTLS: messaggio consegnato al server e stato INVIATA."""
    await accoda_evento("aip.conserved", EVENTO_AIP, coda, adesso=ADESSO, lettore=_lettore())
    dlq = _DLQ()
    esiti = await invia_lotto(
        coda,
        config_smtp(smtp_starttls.porta, "starttls", certificato_tls["cert"]),
        adesso=ADESSO,
        ritardi_secondi=RITARDI,
        lotto=10,
        recupero_dopo_secondi=600,
        pubblica_dlq=dlq,
    )
    assert esiti["inviate"] == 1
    assert coda.righe[0].stato == INVIATA and coda.righe[0].payload["message_id"]
    assert smtp_starttls.gestore.ricevuti[0].rcpt_tos == ["referente@cliente-prova.it"]
    assert dlq.eventi == []


@pytest.mark.asyncio
async def test_invio_temporaneo_ritenta_poi_dlq(coda, smtp_in_chiaro):
    """451 ripetuto: riprogrammato secondo i ritardi, poi NON_PARTITA e evento in DLQ."""
    await accoda_evento(
        "aip.conserved",
        EVENTO_AIP,
        coda,
        adesso=ADESSO,
        lettore=_lettore("occupato@cliente-prova.it"),
    )
    cfg = config_smtp(smtp_in_chiaro.porta, "nessuna")
    dlq = _DLQ()
    t = ADESSO
    stati = []
    for _ in range(3):
        await invia_lotto(
            coda,
            cfg,
            adesso=t,
            ritardi_secondi=RITARDI,
            lotto=10,
            recupero_dopo_secondi=600,
            pubblica_dlq=dlq,
        )
        r = coda.righe[0]
        stati.append((r.stato, r.tentativi))
        if r.prossimo_tentativo:
            # prima della scadenza non si ritenta
            assert (await coda.preleva(t, 10)) == []
            t = r.prossimo_tentativo
    assert stati == [(IN_CODA, 1), (IN_CODA, 2), (NON_PARTITA, 3)]
    assert coda.righe[0].prossimo_tentativo is None
    assert len(dlq.eventi) == 1
    assert dlq.eventi[0]["tentativi"] == 3 and "tentativi esauriti" in dlq.eventi[0]["errore"]
    assert "testo" not in dlq.eventi[0]


@pytest.mark.asyncio
async def test_invio_permanente_subito_dlq(coda, smtp_in_chiaro):
    await accoda_evento(
        "aip.conserved",
        EVENTO_AIP,
        coda,
        adesso=ADESSO,
        lettore=_lettore("rifiuta@cliente-prova.it"),
    )
    dlq = _DLQ()
    await invia_lotto(
        coda,
        config_smtp(smtp_in_chiaro.porta, "nessuna"),
        adesso=ADESSO,
        ritardi_secondi=RITARDI,
        lotto=10,
        recupero_dopo_secondi=600,
        pubblica_dlq=dlq,
    )
    assert coda.righe[0].stato == NON_PARTITA and coda.righe[0].tentativi == 1
    assert len(dlq.eventi) == 1


@pytest.mark.asyncio
async def test_invio_posta_non_configurata_non_finge(coda):
    """Senza SMTP nessun «inviato» finto: NON_PARTITA con motivo e DLQ."""
    await accoda_evento("aip.conserved", EVENTO_AIP, coda, adesso=ADESSO, lettore=_lettore())
    dlq = _DLQ()
    await invia_lotto(
        coda,
        None,
        adesso=ADESSO,
        ritardi_secondi=RITARDI,
        lotto=10,
        recupero_dopo_secondi=600,
        pubblica_dlq=dlq,
    )
    assert coda.righe[0].stato == NON_PARTITA
    assert "posta non configurata" in coda.righe[0].errore


@pytest.mark.asyncio
async def test_messaggio_bloccato_in_invio_torna_in_coda(coda):
    """Crash a metà invio: dopo la soglia il messaggio torna IN_CODA e viene inviato."""
    await accoda_evento("aip.conserved", EVENTO_AIP, coda, adesso=ADESSO, lettore=_lettore())
    await coda.preleva(ADESSO, 10)  # prelevato e mai concluso
    inviati = []

    async def _invia(cfg, msg):
        inviati.append(msg["To"])
        return EsitoInvio(ESITO_INVIATA, "ok", 250, "<id@x>")

    dopo = ADESSO + timedelta(minutes=20)
    esiti = await invia_lotto(
        coda,
        config_smtp(1, "nessuna"),
        adesso=dopo,
        ritardi_secondi=RITARDI,
        lotto=10,
        recupero_dopo_secondi=600,
        pubblica_dlq=_DLQ(),
        invia=_invia,
    )
    assert esiti["recuperate"] == 1 and inviati == ["referente@cliente-prova.it"]


# ── consumer: ritentativi e DLQ dell'elaborazione ───────────────────


@pytest.mark.asyncio
async def test_gestisci_messaggio_ritenta_e_poi_ok():
    chiamate = []
    attese = []

    async def _elabora(topic, evento):
        chiamate.append(1)
        if len(chiamate) < 3:
            raise ConnectionError("DB giù")
        return 1

    async def _attendi(s):
        attese.append(s)

    dlq = _DLQ()
    esito = await gestisci_messaggio(
        "aip.conserved", 0, 7, EVENTO_AIP, _elabora, dlq, ritardi=[1.0, 5.0, 30.0], attendi=_attendi
    )
    assert esito == "ok" and len(chiamate) == 3 and attese == [1.0, 5.0]
    assert dlq.eventi == []


@pytest.mark.asyncio
async def test_gestisci_messaggio_esaurito_va_in_dlq_con_contesto():
    """Dopo i ritentativi l'evento originale finisce nella DLQ (prima andava perso)."""

    async def _elabora(topic, evento):
        raise RuntimeError("errore persistente")

    async def _attendi(s):
        return None

    dlq = _DLQ()
    esito = await gestisci_messaggio(
        "pdv.invalid", 2, 99, EVENTO_PDV, _elabora, dlq, ritardi=[1.0, 2.0], attendi=_attendi
    )
    assert esito == "dlq"
    e = dlq.eventi[0]
    assert e["original_topic"] == "pdv.invalid" and e["offset"] == 99 and e["retries"] == 3
    assert e["event"] == EVENTO_PDV and "errore persistente" in e["error"]


@pytest.mark.asyncio
async def test_gestisci_messaggio_dlq_irraggiungibile_solleva():
    """Se anche la DLQ fallisce si solleva: il chiamante non conferma l'offset."""

    async def _elabora(topic, evento):
        raise RuntimeError("x")

    async def _dlq(evento):
        raise ConnectionError("kafka giù")

    async def _attendi(s):
        return None

    with pytest.raises(ConnectionError):
        await gestisci_messaggio(
            "aip.conserved", 0, 1, EVENTO_AIP, _elabora, _dlq, ritardi=[], attendi=_attendi
        )


@pytest.mark.asyncio
async def test_gestisci_messaggio_non_json_in_dlq():
    dlq = _DLQ()

    async def _elabora(topic, evento):
        return 0

    assert (
        await gestisci_messaggio("aip.conserved", 0, 1, "testo", _elabora, dlq, ritardi=[1.0])
        == "dlq"
    )
    assert dlq.eventi[0]["retries"] == 0


def test_esito_temporaneo_ritentabile():
    assert EsitoInvio(TEMPORANEO, "x").ritentabile is True
