# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R04 dati — parti pure della gestione delle partizioni, delle metriche dei
lavori sul database e dell'origine dell'outbox (senza database).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from prometheus_client import CollectorRegistry, generate_latest

import libs.shared.outbox as outbox
from libs.shared.db import metriche
from libs.shared.db.partizioni import (
    REGISTRO_RETENZIONE,
    RETENZIONE_EVENTI_CLIENTE_GIORNI,
    inizio_da_nome,
    inizio_periodo,
    nome_partizione,
    periodi_da_creare,
    periodo_successivo,
    scadute,
)


def test_inizio_periodo_in_utc_anche_da_altro_fuso():
    """Limiti in UTC: le 00:30 del 1/11 a Roma sono ancora il 31/10 in UTC."""
    roma = timezone(timedelta(hours=1))
    istante = datetime(2026, 11, 1, 0, 30, tzinfo=roma)
    assert inizio_periodo("giorno", istante) == datetime(2026, 10, 31, tzinfo=UTC)
    assert inizio_periodo("mese", istante) == datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        inizio_periodo("giorno", datetime(2026, 1, 1))


def test_periodo_successivo_fine_anno_e_nomi():
    dic = datetime(2026, 12, 1, tzinfo=UTC)
    assert periodo_successivo("mese", dic) == datetime(2027, 1, 1, tzinfo=UTC)
    assert periodo_successivo("giorno", datetime(2026, 12, 31, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC)
    assert nome_partizione("audit_global", "mese", dic) == "audit_global_p202612"
    assert nome_partizione("outbox_eventi", "giorno", dic) == "outbox_eventi_p20261201"
    assert inizio_da_nome("outbox_eventi_p20261201") == dic
    assert inizio_da_nome("audit_global_p202612") == dic
    assert inizio_da_nome("audit_global_r03") is None


def test_periodi_da_creare_corrente_piu_anticipo():
    adesso = datetime(2026, 10, 23, 15, tzinfo=UTC)
    assert periodi_da_creare("mese", adesso, 3) == [
        datetime(2026, m, 1, tzinfo=UTC) for m in (10, 11, 12)] + [datetime(2027, 1, 1, tzinfo=UTC)]
    assert len(periodi_da_creare("giorno", adesso, 7)) == 8


def test_scadute_solo_periodi_interamente_oltre_la_retention():
    """Mese di settembre con retention 30 giorni: si stacca solo quando il
    1/10 è più vecchio di 30 giorni (retention effettiva fra N e N+31)."""
    settembre = datetime(2026, 9, 1, tzinfo=UTC)
    ottobre = datetime(2026, 10, 1, tzinfo=UTC)
    assert scadute("mese", [settembre, ottobre], datetime(2026, 10, 30, tzinfo=UTC), 30) == []
    assert scadute("mese", [settembre, ottobre], datetime(2026, 10, 31, tzinfo=UTC), 30) == [
        settembre]


def test_registro_retenzione_limiti():
    voce = REGISTRO_RETENZIONE[RETENZIONE_EVENTI_CLIENTE_GIORNI]
    assert voce.predefinito == 90 and voce.converti(120) == 120
    with pytest.raises(ValueError):
        voce.converti(3)


def test_collettore_espone_gauge_e_contatori():
    metriche.azzera()
    registro = CollectorRegistry()
    metriche.registra_metriche_db(registro)
    metriche.registra_metriche_db(registro)          # idempotente: nessun doppione
    metriche.imposta("verumdoc_outbox_arretrato", 5, origine="gateway")
    metriche.incrementa("verumdoc_outbox_pubblicati", 3, origine="gateway")
    metriche.incrementa("verumdoc_outbox_pubblicati", 2, origine="gateway")
    testo = generate_latest(registro).decode()
    assert 'verumdoc_outbox_arretrato{origine="gateway"} 5.0' in testo
    assert 'verumdoc_outbox_pubblicati_total{origine="gateway"} 5.0' in testo
    assert metriche.valore("verumdoc_outbox_arretrato", origine="gateway") == 5


def test_registro_da_app_trova_quello_di_installa_metriche():
    from fastapi import FastAPI

    from libs.shared.observability.metriche import installa_metriche

    app = FastAPI()
    assert metriche.registro_da_app(app) is None
    m = installa_metriche(app, "prova")
    assert metriche.registro_da_app(app) is m.registro


def test_origine_outbox_senza_configurazione_rifiuta(monkeypatch):
    """Senza origine un evento finirebbe senza relay: si rifiuta (fuori dai servizi)."""
    import sys

    monkeypatch.setattr(outbox, "_origine", None)
    monkeypatch.delenv("OUTBOX_ORIGINE", raising=False)
    monkeypatch.setitem(sys.modules, "app.config", None)   # nessun servizio importabile
    with pytest.raises(outbox.OrigineNonConfigurata):
        outbox.origine_corrente()
    monkeypatch.setenv("OUTBOX_ORIGINE", "strumento")
    assert outbox.origine_corrente() == "strumento"
    outbox.configura_origine("hashchain")
    assert outbox.origine_corrente() == "hashchain"
    with pytest.raises(ValueError):
        outbox.configura_origine("")
    monkeypatch.setattr(outbox, "_origine", None)


def test_chiave_lock_stabile_e_diversa_per_origine():
    assert outbox._chiave_lock("gateway") == outbox._chiave_lock("gateway")
    assert outbox._chiave_lock("gateway") != outbox._chiave_lock("onboarding")
    assert -(2**31) <= outbox._chiave_lock("gateway") < 2**31


def test_manutenzione_in_processo_solo_con_il_ruolo_dedicato(monkeypatch):
    """Secondo giro (rilievo 5): con i ruoli separati anche in sviluppo, un
    servizio mantiene le partizioni solo se ha ``DATABASE_URL_MANUTENZIONE``
    (o nella suite di prova, ruolo unico); mai con il ruolo dei servizi."""
    from libs.shared.db.manutenzione import ConfigManutenzione, partizioni_nel_processo

    monkeypatch.delenv("DATABASE_URL_MANUTENZIONE", raising=False)
    cfg = ConfigManutenzione(partizioni="auto")
    for ambiente, atteso in (("development", False), ("production", False),
                             ("staging", False), ("local", False), ("test", True)):
        monkeypatch.setenv("ENV", ambiente)
        assert partizioni_nel_processo(cfg) is atteso, ambiente
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL_MANUTENZIONE", "postgresql+asyncpg://vd_manutenzione@db/x")
    assert partizioni_nel_processo(cfg) is True
    assert partizioni_nel_processo(ConfigManutenzione(partizioni="false")) is False
