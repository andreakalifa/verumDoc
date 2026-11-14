# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Eventi per i clienti e consegne webhook su PostgreSQL vero — contratto C3
(agente R03 b3), REGOLE §12.2, §12.4, §12.9, §14.4, §23.3.

Copre: registrazione idempotente con accodamento delle consegne solo alle
sottoscrizioni attive dell'ente con il tipo; cliente inesistente → scarto;
cursore sulla posizione di pubblicazione, filtro per tipo, isolamento fra enti;
consumer con DLQ; lavoratore con destinatario HTTPS vero: consegna firmata,
ritentativi secondo la lista, FALLITA (coda di scarto), riprova; prelievo con
due connessioni (SKIP LOCKED), lease scaduto ripreso, lease perso non
sovrascrive; consegne in pausa con sottoscrizione disattivata.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.application.consegne import ParametriConsegna, esegui_giro
from app.application.eventi_consumer import elabora_evento_cliente
from app.config import settings
from app.domain.eventi import EventoCliente
from app.domain.webhook import verifica_firma
from app.infrastructure.cifratura import Cifratore, contesto_segreto
from app.infrastructure.consegna_http import InviatoreWebhook
from app.infrastructure.repo_eventi import (
    ClienteInesistente,
    elenca_eventi,
    pubblica_eventi,
    registra_evento,
)
from app.infrastructure.repo_webhook import (
    RepositoryWebhook,
    chiudi_tentativo,
    preleva_consegne,
)
from app.services.consumer import gestisci_messaggio

from tests.b3_supporto import avvia_destinatario
from tests.infra_reale import (
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]

NOME = "ricevitore.cliente-prova.it"
CIFRATORE = Cifratore.da_impostazioni(base64.b64encode(os.urandom(32)).decode(), "k1")
MAPPA = settings.eventi_topic


async def crea_tenant(url: str, stato: str = "ATTIVO") -> uuid.UUID:
    t = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente di prova', $3, $4, "
                   "'prova@example.org')", t, t.hex[:16], f"t_{t.hex[:20]}", stato)
    return t


async def crea_sottoscrizione(url: str, tenant: uuid.UUID, tipi: list[str], *,
                              indirizzo: str = f"https://{NOME}/eventi", attiva: bool = True,
                              segreto: str = "whsec_prova") -> str:
    id_ = str(uuid.uuid4())
    await RepositoryWebhook().crea(
        id_=id_, tenant_id=str(tenant), url=indirizzo, tipi=tipi, descrizione=None,
        segreto_cifrato=CIFRATORE.cifra(segreto, contesto_segreto(str(tenant), id_)),
        segreto_suffisso=segreto[-4:], attiva=attiva, creata_da=None, massimo=10)
    return id_


def _evento(tenant, tipo="versamento.ricevuto", risorsa=None) -> EventoCliente:
    rid = str(risorsa or uuid.uuid4())
    return EventoCliente(str(tenant), tipo, "versamento", rid, {"pdv_id": rid})


async def _consegne(url: str):
    return await sql(url, "SELECT id, sottoscrizione_id::text AS sid, evento_id, stato, tentativi, "
                          "prossimo_tentativo, ultimo_codice, ultimo_stato_http, riprove "
                          "FROM public.webhook_consegne ORDER BY id")


# ── Registrazione degli eventi ──────────────────────────────────────

async def test_registra_evento_idempotente_e_consegne_solo_alle_sottoscrizioni_giuste(db_url):
    t1, t2 = await crea_tenant(db_url), await crea_tenant(db_url)
    s_ok = await crea_sottoscrizione(db_url, t1, ["versamento.ricevuto", "pdd.pronto"])
    await crea_sottoscrizione(db_url, t1, ["pdd.pronto"])                       # altro tipo
    await crea_sottoscrizione(db_url, t1, ["versamento.ricevuto"], attiva=False)  # disattivata
    await crea_sottoscrizione(db_url, t2, ["versamento.ricevuto"])              # altro ente
    eliminata = await crea_sottoscrizione(db_url, t1, ["versamento.ricevuto"])
    await RepositoryWebhook().elimina(str(t1), eliminata)

    ev = _evento(t1)
    primo = await registra_evento(ev, origine="pdv.received")
    assert primo.evento_id is not None and primo.consegne == 1
    # riconsegna Kafka dello stesso evento: nessuna riga nuova
    secondo = await registra_evento(ev, origine="pdv.received")
    assert (secondo.evento_id, secondo.consegne) == (None, 0)
    righe = await _consegne(db_url)
    assert [(r["sid"], r["stato"], r["evento_id"]) for r in righe] == [
        (s_ok, "IN_CODA", primo.evento_id)]
    eventi = await sql(db_url, "SELECT tenant_id, tipo, risorsa_id, origine, dati "
                               "FROM public.eventi_cliente")
    assert len(eventi) == 1 and eventi[0]["origine"] == "pdv.received"


async def test_registra_evento_cliente_inesistente(db_url):
    with pytest.raises(ClienteInesistente):
        await registra_evento(_evento(uuid.uuid4()), origine="pdv.received")
    assert await sql(db_url, "SELECT 1 FROM public.eventi_cliente") == []


async def test_consumer_evento_valido_registrato_e_non_valido_in_dlq(db_url):
    """Stessa semantica del consumer della posta: ScartoEvento → DLQ con motivo."""
    t = await crea_tenant(db_url)
    dlq: list[dict] = []

    async def pubblica(evento: dict) -> None:
        dlq.append(evento)

    async def elabora(topic, evento):
        return await elabora_evento_cliente(topic, evento, mappa_topic=MAPPA)

    pdv = str(uuid.uuid4())
    esito = await gestisci_messaggio("pdv.validated", 0, 1, {"tenant_id": str(t), "pdv_id": pdv},
                                     elabora, pubblica, ritardi=[])
    assert esito == "ok"
    for evento in ({"pdv_id": pdv},                                       # senza cliente
                   {"tenant_id": str(uuid.uuid4()), "pdv_id": pdv}):      # cliente inesistente
        assert await gestisci_messaggio("pdv.validated", 0, 2, evento, elabora, pubblica,
                                        ritardi=[0.0, 0.0]) == "dlq"
    assert [d["original_topic"] for d in dlq] == ["pdv.validated", "pdv.validated"]
    assert "cliente inesistente" in dlq[1]["motivo"] and dlq[1]["retries"] == 1
    righe = await sql(db_url, "SELECT tipo, risorsa_id FROM public.eventi_cliente")
    assert [(r["tipo"], r["risorsa_id"]) for r in righe] == [("versamento.validato", pdv)]
    # topic non mappato: ignorato senza errori
    assert await elabora_evento_cliente("altro.topic", {}, mappa_topic=MAPPA) == 0


# ── Elenco a cursore ────────────────────────────────────────────────

async def test_elenca_eventi_cursore_tipo_e_isolamento(db_url):
    t1, t2 = await crea_tenant(db_url), await crea_tenant(db_url)
    for _ in range(5):
        await registra_evento(_evento(t1, "versamento.ricevuto"), origine="x")
        await registra_evento(_evento(t2, "versamento.ricevuto"), origine="x")
    await registra_evento(_evento(t1, "versamento.validato"), origine="x")
    assert await pubblica_eventi() == 11

    pagina1, altri = await elenca_eventi(str(t1), dopo=0, limite=4, tipi=[])
    assert len(pagina1) == 4 and altri
    pagina2, altri = await elenca_eventi(str(t1), dopo=pagina1[-1].posizione, limite=4, tipi=[])
    assert len(pagina2) == 2 and not altri
    ids = [e.id for e in pagina1 + pagina2]
    assert ids == sorted(ids) and len(set(ids)) == 6
    validati, _ = await elenca_eventi(str(t1), dopo=0, limite=10, tipi=["versamento.validato"])
    assert [e.tipo for e in validati] == ["versamento.validato"]
    # l'altro ente vede solo i suoi 5
    altri_ente, _ = await elenca_eventi(str(t2), dopo=0, limite=50, tipi=[])
    assert len(altri_ente) == 5 and not {e.id for e in altri_ente} & set(ids)


async def test_elenca_eventi_solo_pubblicati(db_url):
    """R03 f2 (REV-13): un evento registrato compare solo dopo la pubblicazione."""
    t = await crea_tenant(db_url)
    await registra_evento(_evento(t), origine="x")
    assert (await elenca_eventi(str(t), dopo=0, limite=10, tipi=[]))[0] == []
    assert await pubblica_eventi() == 1
    [evento], _ = await elenca_eventi(str(t), dopo=0, limite=10, tipi=[])
    assert evento.posizione >= 1


async def test_elenca_eventi_query_costanti_con_2_e_50_righe(db_url):
    """REGOLE §23.9: una sola query per pagina, indipendente dal numero di righe."""
    from sqlalchemy import event

    from libs.shared.db import get_engine

    conteggi = []
    for n in (2, 50):
        t = await crea_tenant(db_url)
        for _ in range(n):
            await registra_evento(_evento(t), origine="x")
        await pubblica_eventi()
        istruzioni: list[str] = []

        def conta(conn, cursor, statement, *a, _dove=istruzioni):  # noqa: ANN001
            # R05 (VD-C-080): l'ambito della transazione (SET LOCAL) non è una query
            if "set_config('app.ente'" not in statement:
                _dove.append(statement)

        motore = get_engine().sync_engine
        event.listen(motore, "before_cursor_execute", conta)
        try:
            await elenca_eventi(str(t), dopo=0, limite=500, tipi=[])
        finally:
            event.remove(motore, "before_cursor_execute", conta)
        conteggi.append(len(istruzioni))
    assert conteggi[0] == conteggi[1] == 1


# ── Lavoratore delle consegne ───────────────────────────────────────

class Orologio:
    def __init__(self) -> None:
        self.adesso = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.adesso


def _parametri(**kw) -> ParametriConsegna:
    base = {"ritardi_secondi": [60, 300], "lotto": 50, "concorrenza": 5, "lease_secondi": 120,
                "lunghezza_massima_url": 2048, "consenti_privati": True}
    base.update(kw)
    return ParametriConsegna(**base)


@pytest.fixture
def destinatario(tmp_path):
    d = avvia_destinatario(tmp_path, [NOME])
    yield d
    d.ferma()


def _inviatore(dest) -> InviatoreWebhook:
    async def risolvi(host: str, porta: int) -> list[str]:
        return ["127.0.0.1"]
    return InviatoreWebhook(timeout_connessione=2, timeout=3, consenti_privati=True,
                            ca_pem=dest.pem, risolutore=risolvi)


async def test_lavoratore_consegna_firmata_e_intestazioni(db_url, destinatario):
    t = await crea_tenant(db_url)
    sid = await crea_sottoscrizione(db_url, t, ["versamento.ricevuto"],
                                    indirizzo=f"https://{NOME}:{destinatario.porta}/hook",
                                    segreto="whsec_del_cliente")
    reg = await registra_evento(_evento(t), origine="pdv.received")
    orologio = Orologio()
    inviatore = _inviatore(destinatario)
    try:
        conteggi = await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE,
                                     parametri=_parametri(), orologio=orologio)
    finally:
        await inviatore.chiudi()
    assert conteggi == {"CONSEGNATA": 1}
    r = destinatario.ricevute[0]
    corpo = json.loads(r.corpo)
    assert corpo["id"] == reg.evento_id and corpo["tipo"] == "versamento.ricevuto"
    assert set(corpo) == {"id", "tipo", "creato_il", "risorsa", "dati"}
    riga = (await _consegne(db_url))[0]
    assert r.intestazioni["VerumDoc-Id-Consegna"] == str(riga["id"])
    assert r.intestazioni["VerumDoc-Id-Evento"] == str(reg.evento_id)
    assert r.intestazioni["VerumDoc-Evento"] == "versamento.ricevuto"
    assert verifica_firma("whsec_del_cliente", r.intestazioni["VerumDoc-Firma"], r.corpo,
                          adesso=int(orologio.adesso.timestamp()))
    assert (riga["stato"], riga["tentativi"], riga["sid"]) == ("CONSEGNATA", 1, sid)


async def test_lavoratore_ritentativi_poi_fallita_e_riprova(db_url, destinatario):
    """Ritardi dalla lista costante, poi FALLITA (coda di scarto); la riprova la rimette in coda."""
    t = await crea_tenant(db_url)
    sid = await crea_sottoscrizione(db_url, t, ["versamento.ricevuto"],
                                    indirizzo=f"https://{NOME}:{destinatario.porta}/hook")
    await registra_evento(_evento(t), origine="x")
    destinatario.stato = 503
    orologio = Orologio()
    inviatore = _inviatore(destinatario)
    parametri = _parametri(ritardi_secondi=[60, 300])
    try:
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE, parametri=parametri,
                                 orologio=orologio) == {"IN_CODA": 1}
        riga = (await _consegne(db_url))[0]
        assert riga["prossimo_tentativo"] == orologio.adesso + timedelta(seconds=60)
        assert (riga["ultimo_codice"], riga["ultimo_stato_http"]) == ("WH-103", 503)
        # prima della scadenza nessun nuovo tentativo
        orologio.adesso += timedelta(seconds=59)
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE, parametri=parametri,
                                 orologio=orologio) == {}
        orologio.adesso += timedelta(seconds=1)
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE, parametri=parametri,
                                 orologio=orologio) == {"IN_CODA": 1}
        assert (await _consegne(db_url))[0]["prossimo_tentativo"] == (
            orologio.adesso + timedelta(seconds=300))
        orologio.adesso += timedelta(seconds=300)
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE, parametri=parametri,
                                 orologio=orologio) == {"FALLITA": 1}
        assert len(destinatario.ricevute) == 3
        fallita = (await _consegne(db_url))[0]
        assert (fallita["stato"], fallita["tentativi"], fallita["prossimo_tentativo"]) == (
            "FALLITA", 3, None)

        repo = RepositoryWebhook()
        elenco, _ = await repo.elenca_consegne(str(t), sid, dopo=0, limite=10, stato="FALLITA")
        assert [c["id"] for c in elenco] == [fallita["id"]]
        # riprova solo da FALLITA; da un altro ente è «non trovata»
        assert (await repo.riprova(str(uuid.uuid4()), sid, fallita["id"]))[0] == "non_trovata"
        esito, dati = await repo.riprova(str(t), sid, fallita["id"])
        assert esito == "ok" and dati["stato"] == "IN_CODA" and dati["riprove"] == 1
        assert (await repo.riprova(str(t), sid, fallita["id"])) == ("stato", {"stato": "IN_CODA"})

        destinatario.stato = 200
        orologio.adesso = datetime.now(UTC) + timedelta(seconds=1)
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE, parametri=parametri,
                                 orologio=orologio) == {"CONSEGNATA": 1}
    finally:
        await inviatore.chiudi()


async def test_lavoratore_segreto_non_decifrabile_fallita_subito(db_url, destinatario):
    t = await crea_tenant(db_url)
    await crea_sottoscrizione(db_url, t, ["versamento.ricevuto"],
                              indirizzo=f"https://{NOME}:{destinatario.porta}/hook")
    await registra_evento(_evento(t), origine="x")
    altro = Cifratore.da_impostazioni(base64.b64encode(os.urandom(32)).decode(), "k1")
    inviatore = _inviatore(destinatario)
    try:
        assert await esegui_giro(inviatore=inviatore, cifratore=altro, parametri=_parametri(),
                                 orologio=Orologio()) == {"FALLITA": 1}
    finally:
        await inviatore.chiudi()
    assert (await _consegne(db_url))[0]["ultimo_codice"] == "WH-106"
    assert destinatario.ricevute == []


async def test_lavoratore_sottoscrizione_disattivata_mette_in_pausa(db_url, destinatario):
    t = await crea_tenant(db_url)
    sid = await crea_sottoscrizione(db_url, t, ["versamento.ricevuto"],
                                    indirizzo=f"https://{NOME}:{destinatario.porta}/hook")
    await registra_evento(_evento(t), origine="x")
    repo = RepositoryWebhook()
    await repo.aggiorna(str(t), sid, {"attiva": False})
    inviatore = _inviatore(destinatario)
    try:
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE,
                                 parametri=_parametri(), orologio=Orologio()) == {}
        await repo.aggiorna(str(t), sid, {"attiva": True})
        assert await esegui_giro(inviatore=inviatore, cifratore=CIFRATORE,
                                 parametri=_parametri(), orologio=Orologio()) == {"CONSEGNATA": 1}
    finally:
        await inviatore.chiudi()


async def test_eliminazione_annulla_le_consegne_in_sospeso(db_url):
    t = await crea_tenant(db_url)
    sid = await crea_sottoscrizione(db_url, t, ["versamento.ricevuto"])
    await registra_evento(_evento(t), origine="x")
    assert await RepositoryWebhook().elimina(str(t), sid) == 1
    assert [r["stato"] for r in await _consegne(db_url)] == ["ANNULLATA"]
    assert await RepositoryWebhook().elimina(str(t), sid) is None


async def test_preleva_due_connessioni_insiemi_disgiunti_e_lease(db_url):
    """SKIP LOCKED: due lavoratori insieme non prendono la stessa consegna; il lease
    scaduto si riprende; un lavoratore che ha perso il lease non sovrascrive."""
    t = await crea_tenant(db_url)
    await crea_sottoscrizione(db_url, t, ["versamento.ricevuto"])
    for _ in range(10):
        await registra_evento(_evento(t), origine="x")
    adesso = datetime.now(UTC) + timedelta(seconds=1)
    a, b = await asyncio.gather(
        preleva_consegne(adesso=adesso, lotto=6, lease_secondi=120),
        preleva_consegne(adesso=adesso, lotto=6, lease_secondi=120))
    ids_a, ids_b = {c.id for c in a}, {c.id for c in b}
    assert not ids_a & ids_b and len(ids_a | ids_b) == 10
    # prima della scadenza del lease nessuno le riprende
    assert await preleva_consegne(adesso=adesso + timedelta(seconds=60), lotto=50,
                                  lease_secondi=120) == []
    # lease scaduto (crash): riprese da un altro lavoratore
    riprese = await preleva_consegne(adesso=adesso + timedelta(seconds=121), lotto=50,
                                     lease_secondi=120)
    assert {c.id for c in riprese} == ids_a | ids_b
    # il primo lavoratore torna in ritardo: il suo esito non si registra
    vecchia = next(iter(a))
    assert not await chiudi_tentativo(vecchia, stato="CONSEGNATA", tentativi=1,
                                      prossimo_tentativo=None, stato_http=200, codice=None,
                                      errore=None, adesso=adesso)
    nuova = next(c for c in riprese if c.id == vecchia.id)
    assert await chiudi_tentativo(nuova, stato="CONSEGNATA", tentativi=1,
                                  prossimo_tentativo=None, stato_http=200, codice=None,
                                  errore=None, adesso=adesso)
