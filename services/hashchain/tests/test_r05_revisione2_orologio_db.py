# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 2 di R05 — rilievo S7: l'istante delle voci d'audit viene dal
DATABASE, mai dall'orologio dell'applicazione.

L'export dell'audit nel WORM chiude un periodo quando nessuna transazione
iniziata prima della sua fine è ancora aperta (``vd_transazioni_aperte``): vale
solo se ``created_at`` è ``now()`` della transazione che scrive. Prima
``log_audit`` e la verifica completa scrivevano ``created_at`` con l'orologio
dell'applicazione: con uno scarto fra gli orologi maggiore del margine una riga
poteva cadere in un periodo già esportato. Qui l'orologio dell'applicazione è
falsato (1999): le righe hanno comunque l'istante del database.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]


class _OrologioSbagliato(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        return datetime(1999, 1, 1, tzinfo=tz or UTC)


async def _vicino_al_database(db_url, creato: datetime) -> None:  # noqa: F811
    adesso = (await sql(db_url, "SELECT clock_timestamp() AS t"))[0]["t"]
    assert abs(adesso - creato) < timedelta(minutes=5), (creato, adesso)


async def test_log_audit_usa_l_orologio_del_database(db_url, monkeypatch):  # noqa: F811
    from libs.shared.security import audit

    monkeypatch.setattr(audit, "datetime", _OrologioSbagliato, raising=False)
    await audit.log_audit(evento="prova.orologio_s7", dettagli={"x": 1})
    righe = await sql(db_url, "SELECT created_at FROM public.audit_global "
                              "WHERE evento = 'prova.orologio_s7'")
    assert len(righe) == 1
    await _vicino_al_database(db_url, righe[0]["created_at"])


async def test_verifica_completa_registrata_con_l_orologio_del_database(db_url,  # noqa: F811
                                                                         monkeypatch):
    from app.services import verifica_completa

    monkeypatch.setattr(verifica_completa, "datetime", _OrologioSbagliato)
    esito = await verifica_completa.esegui_verifica_completa(avviata_da=None, ruolo="prova")
    righe = await sql(db_url, "SELECT created_at, dettagli FROM public.audit_global "
                              "WHERE id = $1::uuid", esito.verifica_id)
    assert len(righe) == 1
    await _vicino_al_database(db_url, righe[0]["created_at"])
    # l'istante dell'applicazione resta nei dettagli (conclusa_il), non in created_at
    assert "1999-01-01" in righe[0]["dettagli"]


async def test_export_trattenuto_o_sospeso_ha_il_suo_segnale(monkeypatch):
    """S7: una transazione aperta che trattiene l'export, o l'orizzonte non
    verificabile, si vedono subito nelle metriche (allarmi AuditExportTrattenuto,
    AuditOrizzonteNonVerificabile), non solo dopo 2 ore di ritardo."""
    from app.registro import audit_worm
    from app.registro.metriche import metriche

    adesso = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    aperta = adesso - timedelta(hours=1)

    async def con_transazione() -> tuple[datetime | None, int]:
        return aperta, 0

    monkeypatch.setattr(audit_worm, "orizzonte_transazioni", con_transazione)
    limite, motivo = await audit_worm.limite_esportabile(adesso)
    assert limite == aperta and motivo is not None
    assert metriche().audit_transazione_aperta._value.get() == 3600.0
    assert metriche().audit_orizzonte_non_verificabile._value.get() == 0

    async def non_visibili() -> tuple[datetime | None, int]:
        return None, 2

    monkeypatch.setattr(audit_worm, "orizzonte_transazioni", non_visibili)
    limite, _ = await audit_worm.limite_esportabile(adesso)
    assert limite is None
    assert metriche().audit_orizzonte_non_verificabile._value.get() == 1

    async def libero() -> tuple[datetime | None, int]:
        return None, 0

    monkeypatch.setattr(audit_worm, "orizzonte_transazioni", libero)
    await audit_worm.limite_esportabile(adesso)
    assert metriche().audit_transazione_aperta._value.get() == 0
    assert metriche().audit_orizzonte_non_verificabile._value.get() == 0
