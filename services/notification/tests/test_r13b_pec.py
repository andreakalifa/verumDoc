# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13b (VD-C-078) — canale PEC sul PostgreSQL vero con il simulatore SMTP+IMAP.

  - dominio: transizioni da ricevuta (anche fuori ordine) e comunicazioni per caso d'uso;
  - accodamento solo per i casi d'uso scelti dall'ente, NON_PARTITO senza PEC dell'ente;
  - giro del lavoratore: invio al gestore, lettura delle ricevute via IMAP, stato fino a
    CONSEGNATO / MANCATA_CONSEGNA / NON_ACCETTATO; ritentativi su errore temporaneo;
  - ricevute false (altro gestore, manomesse) e ricevute autentiche di altri messaggi
    registrate senza cambiare lo stato; lettura idempotente per UID;
  - segreti legati all'host: canale con host fuori dal Secret non costruito;
  - database: chi accoda non scrive stati d'invio, nessuno scrive CONSEGNATO senza la
    ricevuta associata, ricevute append-only.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.application import pec as app_pec
from app.domain import pec as dom

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tools.simulatore_pec.gestore import Pki, ricevuta
from tools.simulatore_pec.server import SimulatorePec

CASELLA = "conservazione@pec.conservatore-prova.it"
PASSWORD = "password-di-prova-r13b"
ENTE_PEC = "protocollo@pec.ente-prova.it"


# ── dominio (puro) ───────────────────────────────────────────────────

@pytest.mark.parametrize("stato, tipo, nuovo, colonna", [
    (dom.INVIATO, "ACCETTAZIONE", dom.ACCETTATO, "accettato_il"),
    (dom.IN_INVIO, "ACCETTAZIONE", dom.ACCETTATO, "accettato_il"),
    (dom.ACCETTATO, "AVVENUTA_CONSEGNA", dom.CONSEGNATO, "consegnato_il"),
    (dom.INVIATO, "AVVENUTA_CONSEGNA", dom.CONSEGNATO, "consegnato_il"),
    (dom.ACCETTATO, "ERRORE_CONSEGNA", dom.MANCATA_CONSEGNA, "esito_il"),
    (dom.INVIATO, "NON_ACCETTAZIONE", dom.NON_ACCETTATO, "esito_il"),
    (dom.CONSEGNATO, "ACCETTAZIONE", None, "accettato_il"),  # consegna arrivata prima
    (dom.CONSEGNATO, "ERRORE_CONSEGNA", None, None),          # mai indietro
    (dom.ACCETTATO, "NON_ACCETTAZIONE", None, None),
    (dom.ACCETTATO, "PREAVVISO_ERRORE_CONSEGNA", None, None),
    (dom.IN_CODA, "AVVENUTA_CONSEGNA", None, None),
])
def test_effetto_ricevuta(stato, tipo, nuovo, colonna) -> None:
    e = dom.effetto_ricevuta(stato, tipo)
    assert (e.nuovo_stato, e.colonna_ora) == (nuovo, colonna)


def test_comunicazioni_per_caso_d_uso() -> None:
    p = str(uuid.uuid4())
    c = dom.comunicazione("pratica.cambio_stato", {"tipo": "SCARTO", "a_stato": "PROPOSTA",
                                                   "pratica_id": p})
    assert c is not None and c.caso_uso == "SCARTO" and c.oggetto_id == f"{p}:PROPOSTA"
    assert p in c.testo
    assert dom.comunicazione("pratica.cambio_stato", {"tipo": "RIVERSAMENTO_USCITA",
                                                      "a_stato": "ESEGUITA",
                                                      "pratica_id": p}).caso_uso == "RIVERSAMENTO"
    assert dom.comunicazione("pratica.cambio_stato", {"tipo": "SCARTO", "a_stato": "BOZZA",
                                                      "pratica_id": p}) is None
    assert dom.comunicazione("pratica.cambio_stato", {"tipo": "RETTIFICA", "a_stato": "ESEGUITA",
                                                      "pratica_id": p}) is None
    assert dom.comunicazione("versamento.staging_scaduto",
                             {"avviso_id": "a1"}).caso_uso == "SCADENZE"


# ── integrazione ─────────────────────────────────────────────────────

async def _ente(url: str, pec: str | None = ENTE_PEC) -> uuid.UUID:
    t = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente, pec_referente) VALUES ($1, $2, 'Ente PEC', $3, "
                   "'ATTIVO', 'a@example.org', $4)", t, t.hex[:16], f"t_{t.hex[:20]}", pec)
    return t


@pytest.fixture
async def simulatore():
    pki = Pki()
    sim = SimulatorePec(pki, utenti={CASELLA: PASSWORD})
    await sim.avvia()
    yield sim
    await sim.ferma()


async def _canale(url: str, sim: SimulatorePec, *, codice: str = "pec-prova",
                  host: str = "127.0.0.1", attivo: bool = True, priorita: int = 10) -> None:
    conf = {"indirizzo": CASELLA, "smtp_host": host, "smtp_porta": sim.porta_smtp,
            "smtp_sicurezza": "nessuna", "imap_host": host, "imap_porta": sim.porta_imap,
            "imap_sicurezza": "nessuna", "radici_pem": sim.pki.radice_pem, "timeout_secondi": 5}
    await sql(url, "INSERT INTO public.canali_pec (codice, tipo, nome, attivo, priorita, "
                   "configurazione) VALUES ($1, 'SVILUPPO', 'Simulatore', $2, $3, "
                   "CAST($4 AS jsonb))", codice, attivo, priorita, json.dumps(conf))


def _imp(segreti_host: list[str] | None = None, ca_pem: str = "",
         **kw) -> app_pec.ImpostazioniPec:
    voce = {"host": segreti_host or ["127.0.0.1"], "utente": CASELLA, "password": PASSWORD}
    if ca_pem:
        voce["ca_pem"] = ca_pem
    return app_pec.ImpostazioniPec(segreti_json=json.dumps({"pec-prova": voce}),
                                   produzione=False, ritardi_secondi=[1, 1],
                                   **({"recupero_dopo_secondi": 900} | kw))


async def _accoda(url: str, tenant: uuid.UUID, a_stato: str = "PROPOSTA",
                  casi: tuple[str, ...] = ("SCARTO",), pratica: str | None = None) -> int:
    async def _casi(_t: str) -> tuple[str, ...]:
        return casi

    return await app_pec.accoda_comunicazione(
        "pratica.cambio_stato",
        {"tipo": "SCARTO", "a_stato": a_stato, "pratica_id": pratica or str(uuid.uuid4()),
         "tenant_id": str(tenant)}, casi=_casi)


async def _messaggi(url: str) -> list:
    return await sql(url, "SELECT * FROM public.pec_messaggi ORDER BY created_at")


async def _giro(imp: app_pec.ImpostazioniPec, adesso: datetime | None = None) -> dict:
    return await app_pec.giro_pec(imp, adesso=adesso or datetime.now(UTC))


@richiede_pg
async def test_accodamento_solo_per_i_casi_scelti(db_url) -> None:  # noqa: F811
    t = await _ente(db_url)
    assert await _accoda(db_url, t, casi=()) == 0
    assert await _accoda(db_url, t, casi=("RESTITUZIONE",)) == 0
    pratica = str(uuid.uuid4())
    assert await _accoda(db_url, t, pratica=pratica) == 1
    assert await _accoda(db_url, t, pratica=pratica) == 0   # deduplica (riletto da Kafka)
    righe = await _messaggi(db_url)
    assert [(r["stato"], r["destinatario"], r["caso_uso"]) for r in righe] == [
        ("IN_CODA", ENTE_PEC, "SCARTO")]
    senza = await _ente(db_url, pec=None)
    assert await _accoda(db_url, senza) == 0
    righe = await sql(db_url, "SELECT stato, errore FROM public.pec_messaggi WHERE tenant_id = $1",
                      senza)
    assert righe[0]["stato"] == "NON_PARTITO" and "PEC dell'ente" in righe[0]["errore"]


@richiede_pg
async def test_invio_e_ricevute_fino_alla_consegna(db_url, simulatore) -> None:  # noqa: F811
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    conteggi = await _giro(_imp())
    assert conteggi["inviati"] == 1
    # il gestore ha preso in carico il messaggio dal mittente autenticato
    assert len(simulatore.inviati) == 1 and simulatore.inviati[0][0] == CASELLA
    m = (await _messaggi(db_url))[0]
    assert m["message_id"] and m["canale_codice"] == "pec-prova"
    assert b"X-TipoRicevuta: completa" in simulatore.inviati[0][2]
    # stesso giro: la lettura ha già trovato accettazione e consegna
    assert conteggi["ricevute_associate"] == 2
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "CONSEGNATO" and m["accettato_il"] and m["consegnato_il"]
    ricevute = await sql(db_url, "SELECT esito, tipo, eml, certificato_sha256 FROM "
                                 "public.pec_ricevute ORDER BY id")
    assert [(r["esito"], r["tipo"]) for r in ricevute] == [
        ("ASSOCIATA", "ACCETTAZIONE"), ("ASSOCIATA", "AVVENUTA_CONSEGNA")]
    assert all(r["eml"] and r["certificato_sha256"] for r in ricevute)
    # nuovo giro: nessun doppione (UID già letti)
    assert (await _giro(_imp()))["ricevute_associate"] == 0
    stato = await sql(db_url, "SELECT * FROM public.pec_canali_stato")
    assert stato[0]["ultimo_invio_il"] and stato[0]["ultima_lettura_il"]
    assert json.loads(stato[0]["segreti_presenti"]) == {"password": True, "utente": True}


@richiede_pg
@pytest.mark.parametrize("locale, stato, errore", [
    ("errore.utente", "MANCATA_CONSEGNA", "no-dest"),
    ("nonaccetta", "NON_ACCETTATO", "altro"),
    ("silenzio", "ACCETTATO", None),
])
async def test_esiti_negativi_e_consegna_non_confermata(db_url, simulatore, locale, stato,  # noqa: F811
                                                       errore) -> None:
    t = await _ente(db_url, pec=f"{locale}@pec.ente-prova.it")
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    await _giro(_imp())
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == stato
    if errore:
        assert errore in m["errore"]


@richiede_pg
async def test_errore_temporaneo_si_ritenta_poi_parte(db_url, simulatore) -> None:  # noqa: F811
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    simulatore.rifiuta_temporaneo = 1
    adesso = datetime.now(UTC)
    assert (await _giro(_imp(), adesso))["riprogrammati"] == 1
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "IN_CODA" and m["tentativi"] == 1 and "451" in m["errore"]
    primo_id = m["message_id"]
    assert (await _giro(_imp(), adesso + timedelta(seconds=5)))["inviati"] == 1
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "CONSEGNATO" and m["message_id"] == primo_id  # stesso Message-ID


@richiede_pg
async def test_ricevute_false_o_di_altri_messaggi_non_cambiano_lo_stato(db_url,  # noqa: F811
                                                                       simulatore) -> None:
    t = await _ente(db_url, pec="silenzio@pec.ente-prova.it")
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    await _giro(_imp())
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "ACCETTATO"
    argomenti = {"msgid": m["message_id"], "mittente": CASELLA,
                 "destinatari": [m["destinatario"]], "oggetto": "x",
                 "consegna": m["destinatario"]}
    # 1. consegna firmata da un gestore estraneo (radice diversa)
    simulatore.inietta(CASELLA, ricevuta(Pki(dominio="pec.estraneo.test"),
                                         "AVVENUTA_CONSEGNA", **argomenti))
    # 2. consegna del gestore vero ma manomessa
    simulatore.inietta(CASELLA, ricevuta(simulatore.pki, "AVVENUTA_CONSEGNA", manomessa=True,
                                         **argomenti))
    # 3. consegna autentica ma per un altro destinatario
    simulatore.inietta(CASELLA, ricevuta(simulatore.pki, "AVVENUTA_CONSEGNA",
                                         **(argomenti | {"consegna": "altro@pec.ente.it"})))
    # 4. consegna autentica di un messaggio mai inviato
    simulatore.inietta(CASELLA, ricevuta(simulatore.pki, "AVVENUTA_CONSEGNA",
                                         **(argomenti | {"msgid": "<mai@pec.x.it>"})))
    # 5. posta ordinaria non firmata
    simulatore.inietta(CASELLA, b"From: a@b.it\r\nSubject: ciao\r\n\r\ntesto")
    conteggi = await _giro(_imp())
    assert conteggi["non_autentiche"] == 3 and conteggi["altri_letti"] == 2
    assert conteggi["ricevute_associate"] == 0
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "ACCETTATO" and m["consegnato_il"] is None
    esiti = [(r["esito"], r["messaggio_id"]) for r in await sql(
        db_url, "SELECT esito, messaggio_id FROM public.pec_ricevute WHERE id > 1 ORDER BY id")]
    assert [e for e, _ in esiti] == ["NON_AUTENTICA", "NON_AUTENTICA", "NON_ASSOCIATA",
                                     "NON_ASSOCIATA", "NON_AUTENTICA"]
    assert all(mid is None for _, mid in esiti)
    # la consegna vera arriva dopo: si associa
    simulatore.inietta(CASELLA, ricevuta(simulatore.pki, "AVVENUTA_CONSEGNA", **argomenti))
    await _giro(_imp())
    assert (await _messaggi(db_url))[0]["stato"] == "CONSEGNATO"


@richiede_pg
async def test_segreti_legati_all_host_canale_non_costruito(db_url, simulatore) -> None:  # noqa: F811
    t = await _ente(db_url)
    await _canale(db_url, simulatore)
    await _accoda(db_url, t)
    conteggi = await _giro(_imp(segreti_host=["smtps.pec.gestore.it"]))
    assert conteggi["inviati"] == 0 and simulatore.inviati == []
    stato = await sql(db_url, "SELECT problemi FROM public.pec_canali_stato")
    assert any("127.0.0.1" in p for p in json.loads(stato[0]["problemi"]))
    m = (await _messaggi(db_url))[0]
    assert m["stato"] == "IN_CODA" and "nessun canale" in m["errore"]


@richiede_pg
async def test_prova_su_canale_scelto_e_sviluppo_vietato_in_produzione(db_url,  # noqa: F811
                                                                      simulatore) -> None:
    await _canale(db_url, simulatore)
    await sql(db_url, "INSERT INTO public.pec_messaggi (caso_uso, oggetto_id, destinatario, "
                      "oggetto, testo, canale_codice) VALUES ('PROVA', 'prova:1', "
                      "'prova@pec.esempio-ente.it', 'Prova', 'testo', 'pec-prova')")
    conteggi = await _giro(app_pec.ImpostazioniPec(
        segreti_json=_imp().segreti_json, produzione=True, ritardi_secondi=[1]))
    assert conteggi["inviati"] == 0
    stato = await sql(db_url, "SELECT problemi FROM public.pec_canali_stato")
    assert any("SVILUPPO" in p for p in json.loads(stato[0]["problemi"]))
    # fuori produzione il canale c'è: la prova parte dal canale scelto (dopo l'attesa)
    assert (await _giro(_imp(), datetime.now(UTC) + timedelta(seconds=5)))["inviati"] == 1
    assert (await _messaggi(db_url))[0]["canale_codice"] == "pec-prova"


@richiede_pg
async def test_database_protegge_stati_e_ricevute(db_url) -> None:  # noqa: F811
    import asyncpg

    from libs.shared.db.ruolo_prova import assicura_ruolo_servizio, url_con_utente

    t = await _ente(db_url)
    # login di chi ACCODA soltanto (repliche API): niente stati d'invio
    await assicura_ruolo_servizio(db_url, "vd_prova_pec_accoda", ("verumdoc_pec_accoda",))
    conn = await asyncpg.connect(url_con_utente(db_url, "vd_prova_pec_accoda").replace(
        "postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute("SELECT set_config('app.ambito', 'conservatore', false)")
        with pytest.raises(asyncpg.exceptions.RaiseError, match="IN_CODA"):
            await conn.execute(
                "INSERT INTO public.pec_messaggi (tenant_id, caso_uso, oggetto_id, destinatario, "
                "oggetto, testo, stato) VALUES ($1, 'SCARTO', 'x', 'a@pec.b.it', 'o', 't', "
                "'INVIATO')", t)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO public.pec_messaggi (tenant_id, caso_uso, oggetto_id, destinatario, "
                "oggetto, testo, message_id) VALUES ($1, 'SCARTO', 'x', 'a@pec.b.it', 'o', 't', "
                "'<m@x>')", t)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("UPDATE public.pec_messaggi SET stato = 'CONSEGNATO'")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("SELECT 1 FROM public.canali_pec")
    finally:
        await conn.close()
    # anche il lavoratore: CONSEGNATO solo con la ricevuta associata
    riga = await sql(db_url, "INSERT INTO public.pec_messaggi (tenant_id, caso_uso, oggetto_id, "
                             "destinatario, oggetto, testo, stato, message_id, canale_codice) "
                             "VALUES ($1, 'SCARTO', 'y', 'a@pec.b.it', 'o', 't', 'INVIATO', "
                             "'<m1@x>', 'c') RETURNING id", t)
    mid = riga[0]["id"]
    with pytest.raises(asyncpg.exceptions.RaiseError, match="ricevuta"):
        await sql(db_url, "UPDATE public.pec_messaggi SET stato = 'CONSEGNATO' WHERE id = $1", mid)
    with pytest.raises(asyncpg.exceptions.RaiseError, match="Message-ID"):
        await sql(db_url, "UPDATE public.pec_messaggi SET message_id = '<m2@x>' WHERE id = $1",
                  mid)
    await sql(db_url, "INSERT INTO public.pec_ricevute (canale_codice, imap_uidvalidity, imap_uid, "
                      "esito, messaggio_id, tenant_id, tipo) VALUES ('c', 1, 1, 'ASSOCIATA', $1, "
                      "$2, 'AVVENUTA_CONSEGNA')", mid, t)
    await sql(db_url, "UPDATE public.pec_messaggi SET stato = 'CONSEGNATO' WHERE id = $1", mid)
    with pytest.raises(asyncpg.exceptions.RaiseError, match="esito"):
        await sql(db_url, "UPDATE public.pec_messaggi SET stato = 'MANCATA_CONSEGNA' "
                          "WHERE id = $1", mid)
    for istruzione in ("UPDATE public.pec_ricevute SET esito = 'NON_ASSOCIATA'",
                       "DELETE FROM public.pec_ricevute", "TRUNCATE public.pec_ricevute CASCADE",
                       "DELETE FROM public.pec_messaggi"):
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await sql(db_url, istruzione)


@richiede_pg
async def test_canale_cifrato_smtps_e_imaps_con_ca_del_canale(db_url, certificato_tls) -> None:  # noqa: F811
    """Percorso di produzione: TLS implicito su SMTP e IMAP, certificato verificato con
    la CA della voce del canale nel Secret (``ca_pem``, revisione rilievo 3), mai senza
    verifica; la console non può indicarla."""
    import ssl
    from pathlib import Path

    contesto = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    contesto.load_cert_chain(certificato_tls["cert"], certificato_tls["key"])
    sim = SimulatorePec(Pki(), utenti={CASELLA: PASSWORD})
    await sim.avvia(ssl_server=contesto)
    try:
        t = await _ente(db_url)
        conf = {"indirizzo": CASELLA, "smtp_host": "localhost", "smtp_porta": sim.porta_smtp,
                "smtp_sicurezza": "tls", "imap_host": "localhost", "imap_porta": sim.porta_imap,
                "imap_sicurezza": "tls", "radici_pem": sim.pki.radice_pem, "timeout_secondi": 5}
        ca = Path(certificato_tls["cert"]).read_text()
        await sql(db_url, "INSERT INTO public.canali_pec (codice, tipo, nome, attivo, "
                          "configurazione) VALUES ('pec-prova', 'SMTP_IMAP', 'Gestore', true, "
                          "CAST($1 AS jsonb))", json.dumps(conf))
        await _accoda(db_url, t)
        conteggi = await _giro(_imp(segreti_host=["localhost"], ca_pem=ca))
        assert conteggi["inviati"] == 1 and conteggi["ricevute_associate"] == 2
        assert (await _messaggi(db_url))[0]["stato"] == "CONSEGNATO"
        # senza la CA nel Secret il certificato non è attendibile: nulla parte
        await _accoda(db_url, t, a_stato="ESEGUITA")
        conteggi = await _giro(_imp(segreti_host=["localhost"]))
        assert conteggi["inviati"] == 0 and conteggi["non_partiti"] == 1
        righe = await sql(db_url, "SELECT errore FROM public.pec_messaggi WHERE stato = "
                                  "'NON_PARTITO'")
        assert "certificato" in righe[0]["errore"]
    finally:
        await sim.ferma()


def test_passaggi_del_dominio_uguali_a_quelli_del_trigger() -> None:
    """Revisione R13b, rilievo 5: una sola tabella dei passaggi (dominio = migrazione 055)."""
    import importlib.util
    from pathlib import Path

    from app.domain import pec as dom
    file = next((Path(__file__).resolve().parents[2] / "gateway" / "alembic" / "versions")
                .glob("20261108_055_*.py"))
    spec = importlib.util.spec_from_file_location("migrazione_055", file)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert set(modulo.PASSAGGI_PEC) == set(dom.PASSAGGI)
    # ogni effetto di ricevuta del dominio è un passaggio ammesso
    for tipo in ("ACCETTAZIONE", "NON_ACCETTAZIONE", "AVVENUTA_CONSEGNA", "ERRORE_CONSEGNA"):
        for stato in dom.STATI:
            nuovo = dom.effetto_ricevuta(stato, tipo).nuovo_stato
            if nuovo is not None:
                assert (stato, nuovo) in dom.PASSAGGI, (stato, tipo)


@richiede_pg
@pytest.mark.parametrize("da, a", [
    ("INVIATO", "IN_CODA"), ("INVIATO", "IN_INVIO"), ("ACCETTATO", "INVIATO"),
    ("IN_CODA", "INVIATO"), ("ACCETTATO", "IN_CODA"),
])
async def test_trigger_rifiuta_i_passaggi_all_indietro(db_url, da, a) -> None:  # noqa: F811
    import asyncpg
    t = await _ente(db_url)
    riga = await sql(db_url, "INSERT INTO public.pec_messaggi (tenant_id, caso_uso, oggetto_id, "
                             "destinatario, oggetto, testo, stato, message_id, canale_codice) "
                             "VALUES ($1, 'SCARTO', $2, 'a@pec.b.invalid', 'o', 't', $3, $4, 'c') "
                             "RETURNING id", t, str(uuid.uuid4()), da, f"<{uuid.uuid4()}@x>")
    with pytest.raises(asyncpg.exceptions.RaiseError, match="non ammesso"):
        await sql(db_url, "UPDATE public.pec_messaggi SET stato = $2 WHERE id = $1",
                  riga[0]["id"], a)
