# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R12b (VD-C-161) — invio della posta in un lavoratore dedicato non esposto.

  - il processo senza invio (repliche API, POSTA_INVIO_ATTIVO=false) non deve ricevere
    la chiave privata della posta né le credenziali SMTP: in produzione non parte
    (``problemi_avvio``), fuori produzione lo segnala;
  - le repliche API non avviano il ciclo di invio (solo il consumer che ACCODA);
  - il lavoratore della posta (``python -m app.lavoratore_posta``) in produzione non
    parte senza chiave privata valida né con l'invio spento; avviato, gira il ciclo di
    invio e si ferma in ordine al segnale;
  - il ciclo del lavoratore è la stessa ``invia_lotto`` di prima (apertura del sigillo e
    invio: tests/test_r12_posta_sigillata.py).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from app.application.configurazione import problemi_avvio, problemi_posta
from app.config import Settings
from pydantic import SecretStr

from libs.shared.security.sigillo import genera_coppia

K1 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # 32 byte in base64 (prova)


def _s(**kw: Any) -> Settings:
    return Settings(**kw)


def test_api_senza_invio_rifiuta_i_segreti_della_posta() -> None:
    privata = genera_coppia()[0]
    for campo, valore in (("posta_chiave_privata", privata),
                          ("posta_chiavi_private_precedenti", '{"p0": "' + privata + '"}'),
                          ("smtp_password", "password-smtp-di-prova")):
        problemi = problemi_avvio(_s(env="production", webhook_chiave_cifratura=K1,
                                     posta_invio_attivo=False, **{campo: valore}))
        assert any("VD-C-161" in p and campo.upper() in p for p in problemi), (campo, problemi)
    # senza segreti della posta il processo API parte (nessuna chiave privata richiesta)
    assert problemi_avvio(_s(env="production", webhook_chiave_cifratura=K1,
                             posta_invio_attivo=False)) == []


def test_processo_con_invio_vuole_la_chiave_in_produzione() -> None:
    assert any("POSTA_CHIAVE_PRIVATA mancante" in p
               for p in problemi_posta(_s(env="production", posta_invio_attivo=True)))
    assert problemi_posta(_s(env="production", posta_invio_attivo=True,
                             posta_chiave_privata=genera_coppia()[0])) == []
    # sviluppo: un solo processo fa tutto, come prima
    assert problemi_posta(_s(env="development")) == []


def test_lavoratore_in_produzione_non_parte_senza_chiave_o_con_invio_spento(monkeypatch) -> None:
    from app import lavoratore_posta as lp

    avviati: list[str] = []

    async def _non_deve(*_a: Any, **_k: Any) -> None:
        avviati.append("db")

    monkeypatch.setattr("libs.shared.db.init_db", _non_deve)
    for impostazioni in (_s(env="production", posta_invio_attivo=True),
                         _s(env="production", posta_invio_attivo=False,
                            posta_chiave_privata=genera_coppia()[0]),
                         _s(env="production", posta_invio_attivo=True, smtp_host="smtp.example",
                            smtp_sicurezza="nessuna",
                            posta_chiave_privata=genera_coppia()[0])):
        monkeypatch.setattr(lp, "settings", impostazioni)
        with pytest.raises(SystemExit):
            asyncio.run(lp.esegui(asyncio.Event()))
    assert avviati == []  # controllo PRIMA di aprire il database


def test_lavoratore_gira_il_ciclo_e_si_ferma_in_ordine(monkeypatch) -> None:
    from app import lavoratore_posta as lp

    eventi: list[str] = []

    async def _db(*_a: Any, **_k: Any) -> None:
        eventi.append("db")

    async def _chiudi_db() -> None:
        eventi.append("db_chiuso")

    async def _producer() -> None:
        eventi.append("producer_dlq")

    async def _chiudi_producer() -> None:
        eventi.append("producer_chiuso")

    async def _ciclo(coda: Any) -> None:
        eventi.append("ciclo")
        await asyncio.sleep(3600)

    monkeypatch.setattr("libs.shared.db.init_db", _db)
    monkeypatch.setattr("libs.shared.db.close_db", _chiudi_db)
    monkeypatch.setattr("app.services.consumer.init_producer_dlq", _producer)
    monkeypatch.setattr("app.services.consumer.close_notification_consumer", _chiudi_producer)
    monkeypatch.setattr(lp, "ciclo_invio", _ciclo)
    monkeypatch.setattr(lp, "settings", _s(env="test", posta_invio_attivo=True))

    async def _prova() -> None:
        ferma = asyncio.Event()
        compito = asyncio.create_task(lp.esegui(ferma))
        await asyncio.sleep(0.05)
        ferma.set()
        await asyncio.wait_for(compito, 5)

    asyncio.run(_prova())
    assert eventi == ["db", "producer_dlq", "ciclo", "producer_chiuso", "db_chiuso"]


def _lifespan_con_finti(monkeypatch, invio_attivo: bool) -> list[str]:
    import app.main as m

    avviati: list[str] = []

    async def _nulla(*_a: Any, **_k: Any) -> Any:
        return None

    async def _ciclo(_coda: Any) -> None:
        avviati.append("invio")
        await asyncio.sleep(3600)

    async def _consumo(_coda: Any) -> None:
        avviati.append("accodamento")
        await asyncio.sleep(3600)

    monkeypatch.setattr("libs.shared.db.init_db", _nulla)
    monkeypatch.setattr("libs.shared.db.close_db", _nulla)
    monkeypatch.setattr(m, "avvia_manutenzione_db", _nulla)
    monkeypatch.setattr(m, "ferma_manutenzione_db", _nulla)
    monkeypatch.setattr(m, "init_notification_consumer", _nulla)
    monkeypatch.setattr(m, "close_notification_consumer", _nulla)
    monkeypatch.setattr(m, "ferma_consumer_eventi", _nulla)
    monkeypatch.setattr(m, "consume_loop", _consumo)
    monkeypatch.setattr(m, "ciclo_invio", _ciclo)
    impostazioni = SimpleNamespace(**{
        **m.settings.__dict__, "posta_invio_attivo": invio_attivo,
        "eventi_consumer_attivo": False, "eventi_pubblicazione_attiva": False,
        "webhook_lavoratore_attivo": False, "in_produzione": False})
    monkeypatch.setattr(m, "settings", impostazioni)

    async def _prova() -> None:
        async with m.lifespan(m.app):
            await asyncio.sleep(0.05)

    asyncio.run(_prova())
    return avviati


def test_le_repliche_api_non_inviano(monkeypatch) -> None:
    assert _lifespan_con_finti(monkeypatch, invio_attivo=False) == ["accodamento"]


def test_un_solo_processo_in_sviluppo_invia_come_prima(monkeypatch) -> None:
    assert sorted(_lifespan_con_finti(monkeypatch, invio_attivo=True)) == \
        ["accodamento", "invio"]


def test_info_dell_api_rimanda_al_lavoratore(monkeypatch) -> None:
    import app.main as m

    monkeypatch.setattr(m, "settings", SimpleNamespace(**{**m.settings.__dict__,
                                                          "posta_invio_attivo": False}))
    assert m.stato_posta()["stato"] == "inviata dal lavoratore dedicato"


def test_segreti_in_secretstr_non_vuoti_contano() -> None:
    from app.application.configurazione import segreti_posta_presenti

    s = SimpleNamespace(posta_chiave_privata=SecretStr(" "),
                        posta_chiavi_private_precedenti=SecretStr(""),
                        smtp_password=SecretStr("x"))
    assert segreti_posta_presenti(s) == ["SMTP_PASSWORD"]



def test_revisione_il_lavoratore_non_carica_le_api() -> None:
    """Il lavoratore della posta NON riceve notification-secrets (revisione R12b): non
    deve importare le API (che all'import pretendono JWT_PUBLIC_KEY e API_KEY_PEPPER)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    codice = ("import sys, app.lavoratore_posta, app.application.configurazione, "
              "app.services.coda, app.services.consumer, app.services.invio, "
              "app.services.smtp, libs.shared.db; "
              "print(sorted(m for m in sys.modules if m.startswith(('app.api', 'app.main'))))")
    radice = Path(__file__).resolve().parents[3]
    esito = subprocess.run(
        [sys.executable, "-c", codice], capture_output=True, text=True, timeout=60,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ENV": "production", "JWT_PUBLIC_KEY": "", "API_KEY_PEPPER": "",
             "PYTHONPATH": f"{radice}{os.pathsep}."})
    assert esito.returncode == 0, esito.stderr[-500:]
    assert esito.stdout.strip() == "[]"
