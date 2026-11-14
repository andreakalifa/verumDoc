# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R07 (D19) — periodo dell'export dell'audit nel WORM come impostazione del
conservatore (``AUDIT_EXPORT_PERIODO_MINUTI``: 15 minuti predefiniti, fra 5 e 60;
prima 1 ora fissa con 10 minuti di margine).
"""
from __future__ import annotations

from datetime import timedelta

from app.registro import audit_worm

from libs.shared.parametri import svuota_cache
from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.test_r05_audit_worm import ADESSO, INIZIO, _popola, s3  # noqa: F401

pytestmark = richiede_pg
ORIGINALE = audit_worm.periodo_secondi


async def test_periodo_predefinito_quindici_minuti(db_url, s3, monkeypatch):  # noqa: F811
    monkeypatch.setattr(audit_worm, "periodo_secondi", ORIGINALE)
    svuota_cache()
    await _popola(db_url)
    esito = await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    assert esito.problemi == []
    righe = await sql(db_url, "SELECT periodo_da, periodo_a, righe FROM "
                              "public.audit_esportazioni ORDER BY periodo_da")
    m = lambda minuti: INIZIO + timedelta(minutes=minuti)  # noqa: E731
    assert [(r["periodo_da"], r["periodo_a"], r["righe"]) for r in righe] == [
        (m(0), m(15), 3), (m(15), m(30), 2), (m(30), m(120), 0), (m(120), m(135), 1),
        (m(135), m(150), 0), (m(150), m(165), 1), (m(165), m(180), 1)]


async def test_periodo_dalla_console(db_url):  # noqa: F811
    svuota_cache()
    assert await ORIGINALE() == 900
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, codice, valore, motivo) "
                      "VALUES ('CONSERVATORE', 'AUDIT_EXPORT_PERIODO_MINUTI', '30', "
                      "'meno periodo scoperto')")
    svuota_cache()
    assert await ORIGINALE() == 1800
    await sql(db_url, "UPDATE public.impostazioni_valori SET valore = '3' "
                      "WHERE codice = 'AUDIT_EXPORT_PERIODO_MINUTI'")   # sotto il minimo
    svuota_cache()
    assert await ORIGINALE() == 900
