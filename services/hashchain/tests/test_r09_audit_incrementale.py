# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R09 (D27, VD-C-142) — verifica dell'audit incrementale ogni giorno e completa una
volta al mese (CronJob ``verifica-audit --automatica``, login in sola lettura).

  - incrementale: solo i segmenti dalla finestra in poi, AGGANCIATI al segmento
    precedente (impronta e fine): una catena interrotta al confine si vede;
  - completa: tutti i segmenti (una modifica vecchia la trova solo questa);
  - modo automatico: giorno del mese e finestra dai parametri del motore.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.registro import audit_worm, operatore

from tests.infra_reale import db_url, richiede_pg, s3_server, sql  # noqa: F401 — fixture
from tests.test_r05_audit_worm import (  # noqa: F401 — fixture s3
    ADESSO,
    INIZIO,
    _come_superutente,
    _popola,
    _registra,
    s3,
)

pytestmark = [pytest.mark.integration, richiede_pg]


async def test_incrementale_aggancia_il_segmento_precedente(db_url, s3, monkeypatch):  # noqa: F811
    ids = await _popola(db_url)
    await audit_worm.esporta_periodi_chiusi(adesso=ADESSO)
    await _registra()
    da = INIZIO + timedelta(hours=2)
    esito = await audit_worm.verifica(da=da, incrementale=True)
    assert esito.coerente and esito.segmenti == 1
    # una modifica in un segmento PRIMA della finestra: la trova solo la completa
    await _come_superutente(db_url, ("UPDATE public.audit_global SET evento = 'innocuo' "
                                     "WHERE id = $1", ids[1]))
    assert (await audit_worm.verifica(da=da, incrementale=True)).coerente
    completa = await audit_worm.verifica()
    assert not completa.coerente and completa.modificate == 1
    # l'àncora (segmento precedente) riletta con un contenuto diverso: catena interrotta
    originale = audit_worm._leggi

    def ancora_alterata(client, chiave):
        corpo = originale(client, chiave)
        # tutti i segmenti prima della finestra (l'àncora è l'ultimo di questi)
        return corpo if "/20261020T12" in chiave else corpo + b"\n"

    monkeypatch.setattr(audit_worm, "_leggi", ancora_alterata)
    esito = await audit_worm.verifica(da=da, incrementale=True)
    assert any("non segue il segmento precedente" in p for p in esito.problemi), esito.problemi


async def test_modo_automatico_dai_parametri(db_url, s3):  # noqa: F811
    from libs.shared.parametri import svuota_cache

    svuota_cache()
    modo, da = await operatore.modo_automatico(datetime(2026, 11, 1, 3, 41, tzinfo=UTC))
    assert (modo, da) == ("COMPLETA", None)
    adesso = datetime(2026, 11, 5, 3, 41, tzinfo=UTC)
    assert await operatore.modo_automatico(adesso) == ("INCREMENTALE", adesso - timedelta(days=2))
    for codice, valore in (("AUDIT_VERIFICA_COMPLETA_GIORNO", "5"),
                           ("AUDIT_VERIFICA_FINESTRA_GIORNI", "3")):
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, codice, valore, "
                          "motivo) VALUES ('CONSERVATORE', $1, CAST($2 AS jsonb), 'prova')",
                  codice, valore)
    svuota_cache()
    assert await operatore.modo_automatico(adesso) == ("COMPLETA", None)
    altro = adesso + timedelta(days=1)
    assert await operatore.modo_automatico(altro) == ("INCREMENTALE", altro - timedelta(days=3))
    svuota_cache()
